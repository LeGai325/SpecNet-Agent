#!/usr/bin/env python3
"""Validation-only deployment study for minimum-quality speculative admission.

This is deliberately separate from the V3 three-signal confirmation.  It does
not alter the frozen V3 code, parameters, or test conclusion.  Instead, it
tests whether the expensive static 100x optional completion guard can be
replaced by (1) a minimum-utility optional set and (2) an observable,
deadline-aware completion reservation.
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
# proof_harness loads its upstream at import time.  Match the V3 trace study so
# the trace-aware workload generator is selected before that import happens.
os.environ.setdefault("SPECNET_UPSTREAM", str(TRACE_UPSTREAM))
os.environ.setdefault("SPECNET_DATA_ROOT", str(DEFAULT_DATA_ROOT))

try:
    from . import proof_harness as h
    from .factorized_signal_study import FactorizedSignalRule
    from .pressure_definition_study import PressureSimulator
    from .trace_factorized_signal_study import (
        FROZEN_PARAMS,
        PROFILE_NAMES,
        TRACE_QUALITY_HARD_FLOOR,
        TRACE_QUALITY_TARGET,
        TraceQualitySafeFactorizedRule,
        profile_path,
        scaled_trace_workload,
    )
    from .three_signal_rule_study import balanced_evaluation_matrix
except ImportError:  # pragma: no cover - direct execution from this directory
    import proof_harness as h
    from factorized_signal_study import FactorizedSignalRule
    from pressure_definition_study import PressureSimulator
    from trace_factorized_signal_study import (
        FROZEN_PARAMS,
        PROFILE_NAMES,
        TRACE_QUALITY_HARD_FLOOR,
        TRACE_QUALITY_TARGET,
        TraceQualitySafeFactorizedRule,
        profile_path,
        scaled_trace_workload,
    )
    from three_signal_rule_study import balanced_evaluation_matrix


PROTOCOL_VERSION = "2026-08-06.trace-deployment-v4-minimum-quality"
PRESSURE_DEFINITION = "active_speculative_backlog"
VALIDATION_SEED_BASE = 2_460_000
CONFIRMATION_SEED_BASE = 2_470_000
SCREEN_SCENARIO_COUNT = 9
SCREEN_DURATION = 700
SCREEN_MAX_WORKFLOWS = 28
SCREEN_MAX_TIME = 2600
V3_OPTIONAL_COMPLETION_BOOST = 100.0
V3_TIGHT_OPTIONAL_COMPLETION_BOOST = 1.5


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def minimum_quality_optional_subset(
    optional_branches: Sequence[object],
    template: str,
    quality_target: float,
    upstream: object | None = None,
) -> List[object]:
    """Choose the minimum-byte subset whose *predicted* utility meets target.

    At most ``JUDGE_RETAIN_LIMIT`` optional results can affect quality.  The
    small branch count makes exact enumeration preferable to a density-greedy
    rule: it gives a transparent minimum-work admission decision.
    """
    source_upstream = upstream or h.up
    optional = list(optional_branches)
    retain_limit = source_upstream.JUDGE_RETAIN_LIMIT.get(template, len(optional))
    potential = sum(
        sorted(
            (float(branch.expected_utility) for branch in optional), reverse=True
        )[:retain_limit]
    )
    if potential <= 1e-12:
        return []
    required_fraction = clamp01(
        (quality_target - source_upstream.BASE_REQUIRED_QUALITY)
        / max(1e-12, 1.0 - source_upstream.BASE_REQUIRED_QUALITY)
    )
    required_utility = required_fraction * potential
    candidates: List[Tuple[float, int, Tuple[int, ...], Tuple[object, ...]]] = []
    for count in range(0, min(retain_limit, len(optional)) + 1):
        for subset in itertools.combinations(optional, count):
            utility = sum(
                sorted(
                    (float(branch.expected_utility) for branch in subset), reverse=True
                )[:retain_limit]
            )
            if utility + 1e-12 < required_utility:
                continue
            candidates.append(
                (
                    sum(float(branch.size) for branch in subset),
                    count,
                    tuple(sorted(int(branch.branch_index) for branch in subset)),
                    subset,
                )
            )
    if not candidates:
        # The static action guard would also regard this workflow as infeasible.
        # Keep all optional branches so the realized shortfall remains visible.
        return optional
    return list(min(candidates, key=lambda item: item[:3])[3])


class MinimumQualityPressureSimulator(PressureSimulator):
    """Pressure simulator with exact minimum-quality optional admission."""

    def __init__(self, *args, optional_quality_target: float, **kwargs) -> None:
        self.optional_quality_target = float(optional_quality_target)
        super().__init__(*args, **kwargs)

    def branches_for_action(self, workflow, action: str) -> List[object]:
        # The V4 action still controls background/source behavior.  Optional
        # branch admission is an independent quality constraint, rather than a
        # side effect of the fanout action.
        required = [branch for branch in workflow.spec.branches if branch.required]
        optional = [branch for branch in workflow.spec.branches if not branch.required]
        selected = minimum_quality_optional_subset(
            optional,
            workflow.spec.template,
            self.optional_quality_target,
        )
        return required + selected

    def optional_completion_horizon(self, workflow) -> float:
        """Observable conservative estimate of time until judge can consume optional work."""
        active = self.active_flows()
        own_required = sum(
            flow.remaining
            for flow in active
            if flow.workflow_id == workflow.spec.workflow_id and flow.required
        )
        foreign_required = sum(
            flow.remaining
            for flow in active
            if flow.workflow_id != workflow.spec.workflow_id and flow.required
        )
        active_work = sum(flow.remaining for flow in active)
        # Optional flows must finish before the current workflow's critical
        # branch barrier.  Cross-workflow critical work and total queue work
        # account for shared-capacity contention without future information.
        branch_barrier = (
            own_required + 0.35 * foreign_required + 0.15 * active_work
        ) / max(self.capacity, 1e-9)
        deadline_budget = max(1.0, workflow.deadline_time - self.time)
        return max(1.0, min(deadline_budget, branch_barrier))

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        by_id = {
            workflow.spec.workflow_id: workflow for workflow in self.completed_workflows
        }
        for record in result["workflow_records"]:
            workflow = by_id[record["workflow_id"]]
            optional_flows = [
                self.flows[flow_id] for flow_id in workflow.speculative_branch_flows
            ]
            record.update(
                {
                    "admitted_optional_count": len(optional_flows),
                    "admitted_optional_bytes": sum(flow.size for flow in optional_flows),
                    "optional_quality_target": self.optional_quality_target,
                }
            )
        return result


class DeadlineReservedMinimumQualityRule(FactorizedSignalRule):
    """Factorized scheduler with bounded optional reservation, not 100x priority."""

    name = "v4_minimum_quality"

    def __init__(
        self,
        seed: int,
        base_optional_boost: float,
        urgency_gain: float,
        reserve_margin: float = 0.35,
        tight_optional_compensation: float = 1.5,
    ) -> None:
        if base_optional_boost < 1.0:
            raise ValueError("base optional boost must be at least 1")
        if urgency_gain < 0.0:
            raise ValueError("urgency gain cannot be negative")
        super().__init__(FROZEN_PARAMS, "full", seed)
        self.base_optional_boost = float(base_optional_boost)
        self.urgency_gain = float(urgency_gain)
        self.reserve_margin = float(reserve_margin)
        self.tight_optional_compensation = float(tight_optional_compensation)
        self.name = (
            f"v4_minq_b{base_optional_boost:g}_u{urgency_gain:g}_"
            f"m{reserve_margin:g}"
        )

    def flow_weight(self, flow, sim) -> float:
        weight = super().flow_weight(flow, sim)
        if not flow.speculative or flow.background:
            return weight
        owner = sim.workflows.get(flow.workflow_id)
        if owner is None:
            return weight

        own_optional = sum(
            candidate.remaining
            for candidate in sim.active_flows()
            if candidate.workflow_id == flow.workflow_id
            and candidate.speculative
            and not candidate.background
        )
        horizon = sim.optional_completion_horizon(owner)
        required_share = own_optional / max(sim.capacity * horizon, 1e-9)
        urgency = clamp01(
            (required_share - self.reserve_margin)
            / max(1e-9, 1.0 - self.reserve_margin)
        )
        completion_multiplier = self.base_optional_boost * (
            1.0 + self.urgency_gain * urgency
        )
        state = getattr(owner, "observable_state", None)
        if state is not None and state[1] == "tight":
            completion_multiplier *= self.tight_optional_compensation
        return weight * completion_multiplier


def run_once(
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    profile: str,
    trace_profile: Path,
    split: str,
    use_minimum_admission: bool,
) -> Dict[str, object]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = scaled_trace_workload(
        workload_seed,
        load,
        duration,
        max_workflows,
        deadline_scale,
        optional_scale,
        profile,
        trace_profile,
        split,
    )
    sim_class = MinimumQualityPressureSimulator if use_minimum_admission else PressureSimulator
    sim_kwargs = {
        "capacity_scale": capacity_scale,
        "pressure_definition": PRESSURE_DEFINITION,
        "quality_target": TRACE_QUALITY_TARGET,
        "quality_hard_floor": TRACE_QUALITY_HARD_FLOOR,
        "safety_guard": True,
    }
    if use_minimum_admission:
        sim_kwargs["optional_quality_target"] = TRACE_QUALITY_TARGET
    simulator = sim_class(
        specs,
        policy,
        load,
        workload_seed,
        duration,
        max_time,
        **sim_kwargs,
    )
    summary = simulator.run()
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
            "total_served_bytes": simulator.total_served,
            "total_capacity_bytes": simulator.total_capacity,
        }
    )
    return summary


def metric_row(summary: Mapping[str, object]) -> Dict[str, float]:
    records = list(summary["workflow_records"])
    return {
        "avg_quality": float(summary["avg_quality"]),
        "quality_target_met_ratio": float(summary["quality_target_met_ratio"]),
        "p99_latency": float(summary["p99_latency"]),
        "p95_latency": float(summary["p95_latency"]),
        "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
        "link_utilization": float(summary["link_utilization"]),
        "total_served_bytes": float(summary["total_served_bytes"]),
        "total_capacity_bytes": float(summary["total_capacity_bytes"]),
        "waste_per_workflow": float(summary["wasted_speculative_bytes_per_workflow"]),
        "background_per_workflow": float(summary["background_bytes_served_per_workflow"]),
        "admitted_optional_count_per_workflow": (
            statistics.mean(float(row.get("admitted_optional_count", 0.0)) for row in records)
            if records
            else 0.0
        ),
        "admitted_optional_bytes_per_workflow": (
            statistics.mean(float(row.get("admitted_optional_bytes", 0.0)) for row in records)
            if records
            else 0.0
        ),
    }


def mean_rows(rows: Iterable[Mapping[str, object]]) -> Dict[str, float]:
    source = list(rows)
    keys = [
        key
        for key in source[0]
        if key not in {"evaluation_run", "scenario", "candidate", "policy"}
    ]
    return {
        key: statistics.mean(float(row[key]) for row in source)
        for key in keys
        if all(isinstance(row[key], (int, float)) for row in source)
    }


def write_report(
    output_dir: Path,
    baseline: Mapping[str, float],
    candidates: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> None:
    stage = str(manifest["stage"])
    split = str(manifest["split"])
    is_screen = stage == "screen"
    lines = [
        "# Trace V4 可部署性候选筛选" if is_screen else "# Trace V4 冻结候选确认",
        "",
        (
            "性质：只使用 V3 profile 的 `validation` split 做候选筛选，不改变既有 V3 `test` 三项消融结论。"
            if is_screen
            else f"性质：对已冻结 V4 候选在 `{split}` split 做确认；不在本轮重新选择参数，也不改变既有 V3 三项消融结论。"
        ),
        "",
        "## 机制",
        "",
        "V4 不再对每一条已启动 optional flow 施加静态 `100x` 权重。它先枚举所有不超过 judge retain limit 的 optional 子集，选取预测质量达到 `0.95` 的最小字节集合；再依据当前可观察到的分支 barrier、活动队列和 deadline budget，对该集合施加有上界的完成期预留。",
        "",
        "## 同 Split V3 基线",
        "",
        "| 平均质量 | 质量达标率 | p99 | miss ratio | 链路利用率 | served bytes | speculative waste/workflow |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {baseline['avg_quality']:.6f} | {baseline['quality_target_met_ratio']:.6f} | "
            f"{baseline['p99_latency']:.3f} | {baseline['deadline_miss_ratio']:.6f} | "
            f"{baseline['link_utilization']:.6f} | {baseline['total_served_bytes']:.2f} | "
            f"{baseline['waste_per_workflow']:.3f} |"
        ),
        "",
        "基线是旧的质量安全 V3：`optional=100x, tight=1.5x`。",
        "",
        "## 候选结果",
        "",
        "| 候选 | Quality | 平均达标率 | 最差 cell 达标率 | p99 | served bytes delta | utilization delta | waste delta | 选择状态 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in candidates:
        lines.append(
            f"| {row['candidate']} | {float(row['avg_quality']):.6f} | "
            f"{float(row['quality_target_met_ratio']):.6f} | "
            f"{float(row['min_cell_quality_target_ratio']):.6f} | "
            f"{float(row['p99_latency']):.3f} | "
            f"{float(row['delta_total_served_bytes']):+.2f} | "
            f"{float(row['delta_link_utilization']):+.6f} | "
            f"{float(row['delta_waste_per_workflow']):+.3f} | {row['selection_status']} |"
        )
    selected = [row for row in candidates if row["selection_status"] == "selected"]
    passed_confirmation = [
        row for row in candidates if row["selection_status"] == "passed_confirmation_gates"
    ]
    lines += [
        "",
        "## 选择规则与边界",
        "",
        (
            "候选必须同时满足：平均 quality >= `0.95`、平均 workflow quality 达标率 >= `0.95`、"
            f"每个 cell 的达标率 >= `{float(manifest['minimum_cell_target_ratio']):.2f}`、"
            "p99 不高于 V3 基线、deadline miss 不比基线高 `0.02` 以上、served bytes 和 link utilization 均严格低于 V3 基线。"
            "筛选阶段在可行候选中以最小 served bytes 选择。"
        ),
        "",
    ]
    if stage == "screen":
        lines.append(
            (
                f"本轮选中：`{selected[0]['candidate']}`。该结果仍只是 validation 候选，"
                "必须在冻结后用独立 seeds 的 validation 确认和隔离 `test` split 复现，并补充 p95/p99 path utilization、background floor 和 per-template 公平性门。"
                if selected
                else "本轮没有候选通过全部门槛。这是有效的负结果：V4 不得被写成部署改进，下一步应检查分支完成窗口估计和资源预算是否过紧。"
            )
        )
    else:
        lines.append(
            (
                "冻结候选通过全部确认门；这不是新的参数选择。"
                if passed_confirmation
                else "冻结候选未通过确认门；不得进入或保留为部署候选。"
            )
        )
    lines += [
        "",
        "## 可复查性",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- Profile/Split：`{manifest['profile']}` / `{manifest['split']}`",
        f"- Profile SHA-256：`{manifest['profile_sha256']}`",
        f"- 上游 SHA-256：`{manifest['upstream_sha256']}`",
        f"- 阶段：`{stage}`；{manifest['scenario_count']} 个均衡 smoke 场景 × {manifest['evaluation_runs']} runs。",
        f"- Seed：`{manifest['seed_rule']}`。",
        "- 本实验不重写 V3 的 action-quality guard；V4 的 admission 改动仅存在于本文件的派生 simulator。",
    ]
    (output_dir / "TRACE_DEPLOYMENT_V4_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_float_list(value: str) -> List[float]:
    values = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one number")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILE_NAMES, default="trace_driven_v3_candidate")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--split", choices=("validation", "test"), default="validation")
    parser.add_argument("--stage", choices=("screen", "confirmation"), default="screen")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--base-boosts", type=parse_float_list, default=[8.0, 16.0, 32.0, 64.0])
    parser.add_argument("--urgency-gains", type=parse_float_list, default=[0.0, 1.5])
    parser.add_argument("--reserve-margins", type=parse_float_list, default=[0.35])
    parser.add_argument("--scenario-count", type=int, default=SCREEN_SCENARIO_COUNT)
    parser.add_argument("--evaluation-runs", type=int, default=1)
    parser.add_argument("--seed-base", type=int, default=None)
    parser.add_argument(
        "--minimum-cell-target-ratio",
        type=float,
        default=1.0,
        help="Hard lower bound on every scenario/run workflow quality target ratio.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.scenario_count < 1:
        raise ValueError("scenario count must be positive")
    if args.evaluation_runs < 1:
        raise ValueError("evaluation runs must be positive")
    if not 0.0 <= args.minimum_cell_target_ratio <= 1.0:
        raise ValueError("minimum cell target ratio must be in [0, 1]")
    if any(not 0.0 <= margin < 1.0 for margin in args.reserve_margins):
        raise ValueError("reserve margins must be in [0, 1)")
    if args.stage == "screen" and args.split != "validation":
        raise ValueError("candidate screening is restricted to the validation split")
    if args.stage == "confirmation" and (
        len(args.base_boosts) != 1
        or len(args.urgency_gains) != 1
        or len(args.reserve_margins) != 1
    ):
        raise ValueError(
            "confirmation requires exactly one frozen base boost, urgency gain, and margin"
        )
    trace_profile = profile_path(args.profile, args.data_root)
    all_scenarios = h.scenarios("smoke")
    matrix = balanced_evaluation_matrix(all_scenarios, args.scenario_count, seed=246_081)
    seed_base = args.seed_base
    if seed_base is None:
        seed_base = VALIDATION_SEED_BASE if args.stage == "screen" else CONFIRMATION_SEED_BASE
    output_dir = args.output_dir or (
        ROOT / "results" / "trace_deployment_v4_validation_screen_20260806"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline_cells: List[Dict[str, object]] = []
    candidate_cells: Dict[str, List[Dict[str, object]]] = {}
    candidate_parameters: Dict[str, Dict[str, float]] = {}
    candidates = [
        (base_boost, urgency_gain, reserve_margin)
        for base_boost, urgency_gain, reserve_margin in itertools.product(
            args.base_boosts,
            args.urgency_gains,
            args.reserve_margins,
        )
    ]
    print(
        f"[{args.split}/{args.stage}] V4: {len(candidates)} candidates x "
        f"{len(matrix)} scenarios x {args.evaluation_runs} runs",
        flush=True,
    )
    for evaluation_run in range(args.evaluation_runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = seed_base + evaluation_run * 10_000 + scenario_index
            baseline = run_once(
                TraceQualitySafeFactorizedRule(
                    FROZEN_PARAMS,
                    "full",
                    seed,
                    optional_completion_boost=V3_OPTIONAL_COMPLETION_BOOST,
                    tight_optional_completion_boost=V3_TIGHT_OPTIONAL_COMPLETION_BOOST,
                ),
                scenario,
                seed,
                SCREEN_DURATION,
                SCREEN_MAX_WORKFLOWS,
                SCREEN_MAX_TIME,
                args.profile,
                trace_profile,
                args.split,
                False,
            )
            baseline_cells.append(
                {
                    "evaluation_run": evaluation_run,
                    "scenario": scenario_index,
                    "policy": "v3_static_100x",
                    **metric_row(baseline),
                }
            )
            for base_boost, urgency_gain, reserve_margin in candidates:
                policy = DeadlineReservedMinimumQualityRule(
                    seed,
                    base_boost,
                    urgency_gain,
                    reserve_margin=reserve_margin,
                )
                candidate_parameters[policy.name] = {
                    "base_optional_boost": float(base_boost),
                    "urgency_gain": float(urgency_gain),
                    "reserve_margin": policy.reserve_margin,
                }
                summary = run_once(
                    policy,
                    scenario,
                    seed,
                    SCREEN_DURATION,
                    SCREEN_MAX_WORKFLOWS,
                    SCREEN_MAX_TIME,
                    args.profile,
                    trace_profile,
                    args.split,
                    True,
                )
                candidate_cells.setdefault(policy.name, []).append(
                    {
                        "evaluation_run": evaluation_run,
                        "scenario": scenario_index,
                        "candidate": policy.name,
                        **metric_row(summary),
                    }
                )

    baseline_metrics = mean_rows(baseline_cells)
    candidate_rows: List[Dict[str, object]] = []
    feasible: List[Dict[str, object]] = []
    for candidate, rows in candidate_cells.items():
        aggregate = mean_rows(rows)
        aggregate["min_cell_quality_target_ratio"] = min(
            float(cell["quality_target_met_ratio"]) for cell in rows
        )
        row: Dict[str, object] = {
            "candidate": candidate,
            **candidate_parameters[candidate],
            **aggregate,
        }
        for metric in (
            "total_served_bytes",
            "link_utilization",
            "waste_per_workflow",
            "p99_latency",
            "deadline_miss_ratio",
        ):
            row[f"delta_{metric}"] = aggregate[metric] - baseline_metrics[metric]
        is_feasible = (
            aggregate["avg_quality"] >= TRACE_QUALITY_TARGET
            and aggregate["quality_target_met_ratio"] >= 0.95
            and aggregate["min_cell_quality_target_ratio"]
            >= args.minimum_cell_target_ratio
            and aggregate["p99_latency"] <= baseline_metrics["p99_latency"]
            and aggregate["deadline_miss_ratio"] <= baseline_metrics["deadline_miss_ratio"] + 0.02
            and aggregate["total_served_bytes"] < baseline_metrics["total_served_bytes"]
            and aggregate["link_utilization"] < baseline_metrics["link_utilization"]
        )
        if args.stage == "screen":
            row["selection_status"] = "feasible" if is_feasible else "rejected"
            if is_feasible:
                feasible.append(row)
        else:
            row["selection_status"] = (
                "passed_confirmation_gates" if is_feasible else "failed_confirmation_gates"
            )
        candidate_rows.append(row)
    if args.stage == "screen" and feasible:
        # Once every selected optional flow completes, larger boosts can produce
        # numerically identical traffic.  Prefer the smaller intervention over a
        # floating-point-sized resource difference.
        selected = min(
            feasible,
            key=lambda row: (
                round(float(row["total_served_bytes"]), 6),
                float(row["base_optional_boost"]),
                float(row["urgency_gain"]),
            ),
        )
        selected["selection_status"] = "selected"
    candidate_rows.sort(key=lambda row: (row["selection_status"] != "selected", float(row["total_served_bytes"])))

    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "profile": args.profile,
        "split": args.split,
        "stage": args.stage,
        "profile_sha256": h.sha256(trace_profile),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "harness_sha256": h.sha256(Path(__file__).resolve()),
        "scenario_count": len(matrix),
        "evaluation_runs": args.evaluation_runs,
        "evaluation_matrix": matrix,
        "seed_rule": f"{seed_base} + evaluation_run*10000 + scenario_index",
        "baseline": {
            "optional_completion_boost": V3_OPTIONAL_COMPLETION_BOOST,
            "tight_optional_completion_boost": V3_TIGHT_OPTIONAL_COMPLETION_BOOST,
        },
        "candidate_grid": {
            "base_boosts": args.base_boosts,
            "urgency_gains": args.urgency_gains,
            "reserve_margins": args.reserve_margins,
            "tight_optional_compensation": 1.5,
            "quality_target": TRACE_QUALITY_TARGET,
        },
        "selection": "quality+tail+miss+bytes+utilization validation gates",
        "minimum_cell_target_ratio": args.minimum_cell_target_ratio,
    }
    h.write_csv(output_dir / "v3_baseline_cells.csv", baseline_cells)
    h.write_csv(output_dir / "v4_candidate_cells.csv", [row for rows in candidate_cells.values() for row in rows])
    h.write_csv(output_dir / "v4_candidate_summary.csv", candidate_rows)
    h.write_json(output_dir / "run_manifest.json", manifest)
    write_report(output_dir, baseline_metrics, candidate_rows, manifest)
    print(f"[done] V4 {args.split}/{args.stage} results written to {output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
