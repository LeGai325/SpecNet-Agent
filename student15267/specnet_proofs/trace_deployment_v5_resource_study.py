#!/usr/bin/env python3
"""Resource-consistent V5 deployment refinement.

V4 established that exact minimum-quality admission removes most speculative
waste.  This follow-up keeps that admission rule but fixes the inherited
capacity-scale mismatch in the isolated simulator and tests a staged optional
completion priority.  Selected optional branches are allowed to share service
early, receive a terminal boost as the workflow's required work drains, and
must finish before judge launch.  The V3/V4 artifacts remain untouched.
"""

from __future__ import annotations

import argparse
import itertools
import os
import statistics
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parent
TRACE_UPSTREAM = (
    ROOT
    / "trace_upstream_snapshot"
    / "specnet_agent_experiments"
    / "specnet_agent_experiment.py"
)
DEFAULT_DATA_ROOT = ROOT.parent / "external_agent_data"
os.environ.setdefault("SPECNET_UPSTREAM", str(TRACE_UPSTREAM))
os.environ.setdefault("SPECNET_DATA_ROOT", str(DEFAULT_DATA_ROOT))

try:
    from . import trace_deployment_v4_study as v4
except ImportError:  # pragma: no cover - direct execution from this directory
    import trace_deployment_v4_study as v4


PROTOCOL_VERSION = "2026-08-06.trace-deployment-v5-resource-consistent"
V4_BASE_BOOST = 96.0
V4_URGENCY_GAIN = 0.0
V4_RESERVE_MARGIN = 0.35
V5_TERMINAL_BOOST = 96.0
V5_TIGHT_COMPENSATION = 1.5
V5_SEED_BASE = 2_480_000
SCREEN_DURATION = v4.SCREEN_DURATION
SCREEN_MAX_WORKFLOWS = v4.SCREEN_MAX_WORKFLOWS
SCREEN_MAX_TIME = v4.SCREEN_MAX_TIME


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    low = int(rank)
    high = min(len(ordered) - 1, low + 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


class CapacityConsistentMixin:
    """Apply capacity_scale to the capacity actually used by service pools."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        scale = float(getattr(self, "capacity_scale", 1.0))
        self.path_capacities = {
            path_id: float(capacity) * scale
            for path_id, capacity in self.path_capacities.items()
        }
        for attr in (
            "path_total_capacity",
            "path_total_served",
            "path_queue_pressure_samples",
            "path_total_base_served",
            "path_total_lent_served",
            "path_total_borrowed_received",
            "path_total_unused_after_lending",
            "path_total_home_flow_served",
        ):
            if attr == "path_queue_pressure_samples":
                setattr(self, attr, {path_id: [] for path_id in self.path_capacities})
            else:
                setattr(self, attr, {path_id: 0.0 for path_id in self.path_capacities})
        self.epoch_utilization_samples: List[float] = []

    def serve_active_flows(self) -> None:
        before = float(self.total_served)
        super().serve_active_flows()
        epoch_capacity = sum(self.path_capacities.values())
        self.epoch_utilization_samples.append(
            (float(self.total_served) - before) / max(1e-9, epoch_capacity)
        )

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        result.update(
            {
                "resource_model": "capacity_consistent_single_bottleneck",
                **self.resource_metrics(),
            }
        )
        return result

    def resource_metrics(self) -> Dict[str, float]:
        samples = self.epoch_utilization_samples
        queue_samples = [
            sample
            for path_samples in self.path_queue_pressure_samples.values()
            for sample in path_samples
        ]
        return {
            "epoch_utilization_mean": statistics.mean(samples) if samples else 0.0,
            "epoch_utilization_p95": percentile(samples, 0.95),
            "epoch_utilization_p99": percentile(samples, 0.99),
            "epoch_utilization_max": max(samples) if samples else 0.0,
            "path_queue_pressure_p95": percentile(queue_samples, 0.95),
            "path_queue_pressure_p99": percentile(queue_samples, 0.99),
        }


class CapacityConsistentPressureSimulator(CapacityConsistentMixin, v4.PressureSimulator):
    """Capacity-corrected simulator for the V3 static comparator."""

    pass


class CapacityConsistentMinimumQualitySimulator(
    CapacityConsistentMixin, v4.MinimumQualityPressureSimulator
):
    """Minimum-quality admission with optional completion barrier."""

    def __init__(self, *args, completion_barrier: bool = True, **kwargs) -> None:
        self.completion_barrier = bool(completion_barrier)
        super().__init__(*args, **kwargs)

    def progress_workflows(self) -> None:
        for workflow in self.workflows.values():
            if workflow.complete_time is not None:
                continue
            if workflow.stage == "planner" and self.completed(workflow.planner_flow):
                self.spawn_branches(workflow)
            elif workflow.stage == "branches" and self.all_completed(
                workflow.required_branch_flows
            ):
                workflow.stage = "llm"
                workflow.llm_flow = self.new_flow(
                    workflow,
                    "llm",
                    workflow.spec.llm_size,
                    role="critical_bulk",
                    stage="llm",
                    required=True,
                )
            elif workflow.stage == "llm" and self.completed(workflow.llm_flow):
                optional_done = self.all_completed(workflow.speculative_branch_flows)
                if self.completion_barrier and not optional_done:
                    workflow.optional_barrier_wait_epochs = int(
                        getattr(workflow, "optional_barrier_wait_epochs", 0)
                    ) + 1
                    continue
                workflow.stage = "judge"
                workflow.judge_flow = self.new_flow(
                    workflow,
                    "judge",
                    workflow.spec.judge_size,
                    role="critical_control",
                    stage="judge",
                    required=True,
                )
            elif workflow.stage == "judge" and self.completed(workflow.judge_flow):
                self.finish_workflow(workflow)

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        by_id = {
            workflow.spec.workflow_id: workflow for workflow in self.completed_workflows
        }
        waits = []
        for record in result["workflow_records"]:
            workflow = by_id[record["workflow_id"]]
            wait = float(getattr(workflow, "optional_barrier_wait_epochs", 0))
            record["optional_barrier_wait_epochs"] = wait
            waits.append(wait)
        result.update(
            {
                "optional_barrier_wait_per_workflow": (
                    statistics.mean(waits) if waits else 0.0
                ),
                "optional_barrier_wait_workflow_ratio": (
                    sum(wait > 0.0 for wait in waits) / max(1, len(waits))
                ),
                "resource_model": "capacity_consistent_single_bottleneck",
                **self.resource_metrics(),
            }
        )
        return result


class StagedMinimumQualityRule(v4.DeadlineReservedMinimumQualityRule):
    """Use a low early boost and a fixed terminal boost near judge readiness."""

    name = "v5_staged_minimum_quality"

    def __init__(
        self,
        seed: int,
        base_optional_boost: float,
        required_progress_trigger: float,
        terminal_boost: float = V5_TERMINAL_BOOST,
    ) -> None:
        if not 0.0 <= required_progress_trigger <= 1.0:
            raise ValueError("required progress trigger must be in [0, 1]")
        if terminal_boost < 1.0:
            raise ValueError("terminal boost must be at least 1")
        super().__init__(
            seed,
            base_optional_boost,
            urgency_gain=0.0,
            reserve_margin=V4_RESERVE_MARGIN,
            tight_optional_compensation=V5_TIGHT_COMPENSATION,
        )
        self.required_progress_trigger = float(required_progress_trigger)
        self.terminal_boost = float(terminal_boost)
        self.name = (
            f"v5_staged_b{base_optional_boost:g}_"
            f"t{required_progress_trigger:g}_z{terminal_boost:g}"
        )

    def flow_weight(self, flow, sim) -> float:
        weight = super(v4.DeadlineReservedMinimumQualityRule, self).flow_weight(
            flow, sim
        )
        if not flow.speculative or flow.background:
            return weight
        owner = sim.workflows.get(flow.workflow_id)
        if owner is None:
            return weight
        required_total = sum(
            float(branch.size) for branch in owner.spec.branches if branch.required
        )
        required_remaining = sum(
            candidate.remaining
            for candidate in sim.active_flows()
            if candidate.workflow_id == flow.workflow_id and candidate.required
        )
        progress = 1.0 - required_remaining / max(required_total, 1e-9)
        terminal = owner.stage in {"llm", "judge"} or (
            progress >= self.required_progress_trigger
        )
        multiplier = self.terminal_boost if terminal else self.base_optional_boost
        state = getattr(owner, "observable_state", None)
        if state is not None and state[1] == "tight":
            multiplier *= self.tight_optional_compensation
        return weight * multiplier


def run_once(
    variant: str,
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    profile: str,
    trace_profile: Path,
    split: str,
) -> Dict[str, object]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = v4.scaled_trace_workload(
        workload_seed,
        load,
        SCREEN_DURATION,
        SCREEN_MAX_WORKFLOWS,
        deadline_scale,
        optional_scale,
        profile,
        trace_profile,
        split,
    )
    sim_kwargs = {
        "capacity_scale": capacity_scale,
        "pressure_definition": v4.PRESSURE_DEFINITION,
        "quality_target": v4.TRACE_QUALITY_TARGET,
        "quality_hard_floor": v4.TRACE_QUALITY_HARD_FLOOR,
        "safety_guard": True,
    }
    if variant == "v3_static_100x":
        simulator = CapacityConsistentPressureSimulator(specs, policy, load, workload_seed,
            SCREEN_DURATION, SCREEN_MAX_TIME, **sim_kwargs)
    else:
        simulator = CapacityConsistentMinimumQualitySimulator(
            specs,
            policy,
            load,
            workload_seed,
            SCREEN_DURATION,
            SCREEN_MAX_TIME,
            optional_quality_target=v4.TRACE_QUALITY_TARGET,
            # V6 retains the same explicit optional-completion semantics while
            # changing only the priority rule.  Keep this check capability-
            # based so comparisons cannot accidentally omit the quality gate.
            completion_barrier=variant.startswith(("v5_", "v6_", "v7_")),
            **sim_kwargs,
        )
    summary = simulator.run()
    summary.update(
        {
            "variant": variant,
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
            "workload_profile": profile,
            "total_served_bytes": simulator.total_served,
            "total_capacity_bytes": simulator.total_capacity,
        }
    )
    return summary


def metric_row(summary: Mapping[str, object]) -> Dict[str, float]:
    records = list(summary["workflow_records"])
    return {
        **v4.metric_row(summary),
        "epoch_utilization_mean": float(summary.get("epoch_utilization_mean", 0.0)),
        "epoch_utilization_p95": float(summary.get("epoch_utilization_p95", 0.0)),
        "epoch_utilization_p99": float(summary.get("epoch_utilization_p99", 0.0)),
        "epoch_utilization_max": float(summary.get("epoch_utilization_max", 0.0)),
        "path_queue_pressure_p95": float(summary.get("path_queue_pressure_p95", 0.0)),
        "path_queue_pressure_p99": float(summary.get("path_queue_pressure_p99", 0.0)),
        "optional_barrier_wait_per_workflow": float(
            summary.get("optional_barrier_wait_per_workflow", 0.0)
        ),
        "optional_barrier_wait_workflow_ratio": float(
            summary.get("optional_barrier_wait_workflow_ratio", 0.0)
        ),
        "template_quality_target_min": min(
            (
                sum(float(row["quality_target_met"]) for row in records if row["template"] == template)
                / max(1, sum(row["template"] == template for row in records))
                for template in {row["template"] for row in records}
            ),
            default=1.0,
        ),
    }


def mean_rows(rows: Iterable[Mapping[str, object]]) -> Dict[str, float]:
    source = list(rows)
    numeric_keys = [
        key for key in source[0]
        if key not in {"evaluation_run", "scenario", "candidate", "variant"}
        and all(isinstance(row[key], (int, float)) for row in source)
    ]
    return {key: statistics.mean(float(row[key]) for row in source) for key in numeric_keys}


def write_report(
    output_dir: Path,
    baseline: Mapping[str, float],
    v4_reference: Mapping[str, float],
    candidates: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> None:
    stage = str(manifest["stage"])
    lines = [
        "# Trace V5 资源一致性与阶段校准实验",
        "",
        "本报告是独立于 V3/V4 历史产物的新评估。它修正了容量缩放只作用于状态估计、未作用于单链路服务池的问题；历史结论不被覆盖。",
        "",
        "## 改动",
        "",
        "1. `capacity_scale` 同时缩放实际 `path_capacities` 和状态容量。",
        "2. 保留 V4 的精确最小质量集合准入。",
        "3. optional 分支早期使用较低基础权重，在 required 工作进度达到阈值或进入 LLM 阶段后升至终端权重；judge 只有在选中 optional 全部完成后才启动。",
        "4. 记录每 epoch 服务利用率的均值、p95、p99 和最大值，并检查模板级质量门。",
        "",
        "## 容量模型审计",
        "",
        "旧实现的 `ProofSimulator` 先由父类创建 `path_capacities={shared:16}`，随后只更新 `self.capacity`。因此 0.72 与 1.25 场景的实际共享链路都仍为 16；本轮 V5 将其改为 11.52 与 20.0。这个问题使旧结果中的容量压力变化只能作为状态估计证据，不能作为真实资源缩放证据。",
        "",
        "## 参考结果（同一修正模型）",
        "",
        "| 方案 | quality | 达标率 | p99 | miss | served bytes | 利用率 | epoch util p99 | queue pressure p99 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| V3 static 100x | {baseline['avg_quality']:.6f} | {baseline['quality_target_met_ratio']:.6f} | {baseline['p99_latency']:.3f} | {baseline['deadline_miss_ratio']:.6f} | {baseline['total_served_bytes']:.2f} | {baseline['link_utilization']:.6f} | {baseline['epoch_utilization_p99']:.6f} | {baseline['path_queue_pressure_p99']:.3f} |",
        f"| V4 minQ 96x | {v4_reference['avg_quality']:.6f} | {v4_reference['quality_target_met_ratio']:.6f} | {v4_reference['p99_latency']:.3f} | {v4_reference['deadline_miss_ratio']:.6f} | {v4_reference['total_served_bytes']:.2f} | {v4_reference['link_utilization']:.6f} | {v4_reference['epoch_utilization_p99']:.6f} | {v4_reference['path_queue_pressure_p99']:.3f} |",
        "",
        "## V5 候选",
        "",
        "| 候选 | quality | 最差 cell 达标率 | 最差模板达标率 | p99 | served bytes delta vs V4 | queue p99 delta vs V4 | barrier wait/workflow | 状态 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in candidates:
        lines.append(
            f"| {row['candidate']} | {float(row['avg_quality']):.6f} | {float(row['min_cell_quality_target_ratio']):.6f} | {float(row['template_quality_target_min']):.6f} | {float(row['p99_latency']):.3f} | {float(row['delta_total_served_bytes_vs_v4']):+.2f} | {float(row['delta_path_queue_pressure_p99_vs_v4']):+.3f} | {float(row['optional_barrier_wait_per_workflow']):.2f} | {row['selection_status']} |"
        )
    selected = [row for row in candidates if row["selection_status"] == "selected"]
    confirmed = [
        row for row in candidates if row["selection_status"] == "passed_confirmation_gates"
    ]
    lines += [
        "",
        "## 结论",
        "",
        (
            f"当前通过筛选的候选是 `{selected[0]['candidate']}`。它在容量一致模型下保持逐 cell 和逐模板 quality 门，并相对 V4 参考降低尾延迟/资源指标。"
            if stage == "screen" and selected
            else (
                f"冻结候选 `{confirmed[0]['candidate']}` 通过独立 confirmation 门；该轮没有重新选择参数。"
                if stage == "confirmation" and confirmed
                else "没有 V5 候选同时通过全部严格门。该负结果说明阶段校准和 judge 屏障在当前资源模型下尚不能替代 V4-minQ-96；应保留 V4 作为候选并把容量修正后的失败作为后续工作。"
            )
        ),
        "",
        "## 证据边界与下一步",
        "",
        (
            "- 这是 validation screen，不是部署确认；如有候选通过，必须使用新 V3 holdout 和冻结参数复现。"
            if stage == "screen"
            else f"- 这是独立 `{manifest['split']}` confirmation；仍需要未被 V4/V5 探索访问过的 V3 holdout，才能形成无污染部署确认。"
        ),
        "- retained-branch quality 仍是模拟代理，不是端到端任务正确率。",
        "- 下一轮应把修正容量模型同步到独立 V3 holdout，并报告 path utilization 分位数、background floor、per-tenant 公平性以及真实 NIC/GPU telemetry。",
        f"- 协议：`{manifest['protocol_version']}`；profile/split：`{manifest['profile']}`/`{manifest['split']}`；seed：`{manifest['seed_rule']}`。",
    ]
    (output_dir / "TRACE_DEPLOYMENT_V5_RESOURCE_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_float_list(value: str) -> List[float]:
    values = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one number")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=v4.PROFILE_NAMES, default="trace_driven_v3_candidate")
    parser.add_argument("--data-root", type=Path, default=v4.DEFAULT_DATA_ROOT)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--stage", choices=("screen", "confirmation"), default="screen")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--base-boosts", type=parse_float_list, default=[1.0, 4.0, 8.0, 16.0, 32.0])
    parser.add_argument("--progress-triggers", type=parse_float_list, default=[0.0, 0.25, 0.5, 0.75])
    parser.add_argument("--scenario-count", type=int, default=9)
    parser.add_argument("--evaluation-runs", type=int, default=1)
    parser.add_argument("--seed-base", type=int, default=V5_SEED_BASE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.stage == "screen" and args.split != "validation":
        raise ValueError("V5 candidate screening is restricted to validation")
    if args.stage == "confirmation" and (
        len(args.base_boosts) != 1 or len(args.progress_triggers) != 1
    ):
        raise ValueError("confirmation requires exactly one frozen base boost and trigger")
    trace_profile = v4.profile_path(args.profile, args.data_root)
    matrix = v4.balanced_evaluation_matrix(
        v4.h.scenarios("smoke"), args.scenario_count, seed=246_081
    )
    output_dir = args.output_dir or ROOT / "results" / (
        "trace_deployment_v5_resource_screen_20260806"
        if args.stage == "screen"
        else "trace_deployment_v5_resource_confirmation_20260806"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        (base_boost, trigger)
        for base_boost, trigger in itertools.product(args.base_boosts, args.progress_triggers)
    ]
    baseline_cells: List[Dict[str, object]] = []
    v4_cells: List[Dict[str, object]] = []
    candidate_cells: Dict[str, List[Dict[str, object]]] = {}
    params: Dict[str, Dict[str, float]] = {}
    print(
        f"[capacity-consistent/{args.split}] {len(candidates)} V5 candidates x {len(matrix)} scenarios x {args.evaluation_runs} runs",
        flush=True,
    )
    for evaluation_run in range(args.evaluation_runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = args.seed_base + evaluation_run * 10_000 + scenario_index
            baseline_policy = v4.TraceQualitySafeFactorizedRule(
                v4.FROZEN_PARAMS,
                "full",
                seed,
                optional_completion_boost=v4.V3_OPTIONAL_COMPLETION_BOOST,
                tight_optional_completion_boost=v4.V3_TIGHT_OPTIONAL_COMPLETION_BOOST,
            )
            baseline = run_once("v3_static_100x", baseline_policy, scenario, seed, args.profile, trace_profile, args.split)
            baseline_cells.append({"evaluation_run": evaluation_run, "scenario": scenario_index, "variant": "v3_static_100x", **metric_row(baseline)})
            v4_policy = v4.DeadlineReservedMinimumQualityRule(
                seed, V4_BASE_BOOST, V4_URGENCY_GAIN, reserve_margin=V4_RESERVE_MARGIN
            )
            v4_summary = run_once("v4_minq_96", v4_policy, scenario, seed, args.profile, trace_profile, args.split)
            v4_cells.append({"evaluation_run": evaluation_run, "scenario": scenario_index, "variant": "v4_minq_96", **metric_row(v4_summary)})
            for base_boost, trigger in candidates:
                policy = StagedMinimumQualityRule(seed, base_boost, trigger)
                params[policy.name] = {"base_optional_boost": base_boost, "required_progress_trigger": trigger, "terminal_boost": V5_TERMINAL_BOOST}
                summary = run_once(policy.name, policy, scenario, seed, args.profile, trace_profile, args.split)
                candidate_cells.setdefault(policy.name, []).append({"evaluation_run": evaluation_run, "scenario": scenario_index, "candidate": policy.name, **metric_row(summary)})

    baseline_metrics = mean_rows(baseline_cells)
    v4_metrics = mean_rows(v4_cells)
    candidate_rows: List[Dict[str, object]] = []
    for candidate, rows in candidate_cells.items():
        aggregate = mean_rows(rows)
        aggregate["min_cell_quality_target_ratio"] = min(float(row["quality_target_met_ratio"]) for row in rows)
        aggregate["max_cell_epoch_utilization_p99"] = max(float(row["epoch_utilization_p99"]) for row in rows)
        row: Dict[str, object] = {"candidate": candidate, **params[candidate], **aggregate}
        for metric in ("total_served_bytes", "link_utilization", "waste_per_workflow", "p99_latency", "deadline_miss_ratio", "path_queue_pressure_p99"):
            row[f"delta_{metric}_vs_v3"] = aggregate[metric] - baseline_metrics[metric]
            row[f"delta_{metric}_vs_v4"] = aggregate[metric] - v4_metrics[metric]
        row["selection_status"] = "rejected"
        if (
            aggregate["avg_quality"] >= v4.TRACE_QUALITY_TARGET
            and aggregate["quality_target_met_ratio"] >= 0.95
            and aggregate["min_cell_quality_target_ratio"] >= 1.0
            and aggregate["template_quality_target_min"] >= 1.0
            and aggregate["p99_latency"] <= v4_metrics["p99_latency"]
            and aggregate["deadline_miss_ratio"] <= v4_metrics["deadline_miss_ratio"] + 0.02
            and aggregate["total_served_bytes"] <= v4_metrics["total_served_bytes"]
            and aggregate["link_utilization"] <= v4_metrics["link_utilization"]
        ):
            row["selection_status"] = (
                "passed_confirmation_gates" if args.stage == "confirmation" else "feasible"
            )
        elif args.stage == "confirmation":
            row["selection_status"] = "failed_confirmation_gates"
        candidate_rows.append(row)
    feasible = [row for row in candidate_rows if row["selection_status"] == "feasible"]
    if args.stage == "screen" and feasible:
        selected = min(feasible, key=lambda row: (float(row["total_served_bytes"]), float(row["p99_latency"]), float(row["base_optional_boost"])))
        selected["selection_status"] = "selected"
    candidate_rows.sort(key=lambda row: (row["selection_status"] not in {"selected", "feasible"}, float(row["total_served_bytes"])))
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "profile": args.profile,
        "split": args.split,
        "stage": args.stage,
        "profile_sha256": v4.h.sha256(trace_profile),
        "upstream_sha256": v4.h.sha256(v4.h.UPSTREAM_PATH),
        "harness_sha256": v4.h.sha256(Path(__file__).resolve()),
        "resource_model": "capacity_consistent_single_bottleneck",
        "scenario_count": len(matrix),
        "evaluation_runs": args.evaluation_runs,
        "evaluation_matrix": matrix,
        "seed_rule": f"{args.seed_base} + evaluation_run*10000 + scenario_index",
        "v4_reference": {"base_optional_boost": V4_BASE_BOOST, "urgency_gain": V4_URGENCY_GAIN, "reserve_margin": V4_RESERVE_MARGIN},
        "v5_terminal_boost": V5_TERMINAL_BOOST,
        "candidate_grid": {"base_boosts": args.base_boosts, "progress_triggers": args.progress_triggers},
        "selection": "strict quality/tail/miss/served/utilization gates vs corrected V4 reference",
    }
    v4.h.write_csv(output_dir / "v3_baseline_cells.csv", baseline_cells)
    v4.h.write_csv(output_dir / "v4_reference_cells.csv", v4_cells)
    v4.h.write_csv(output_dir / "v5_candidate_cells.csv", [row for rows in candidate_cells.values() for row in rows])
    v4.h.write_csv(output_dir / "v5_candidate_summary.csv", candidate_rows)
    v4.h.write_json(output_dir / "run_manifest.json", manifest)
    write_report(output_dir, baseline_metrics, v4_metrics, candidate_rows, manifest)
    print(f"[done] V5 results written to {output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
