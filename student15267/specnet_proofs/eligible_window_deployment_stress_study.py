#!/usr/bin/env python3
"""Stress-test the eligible-window mechanism under finite background TTLs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .factorized_background_eligible_window_study import (
        BACKGROUND_FLOOR_TOLERANCE,
        EligibleWindowPressureSimulator,
        IdleEligibleFactorizedRule,
        TARGET_RATIO,
        foreground_parity_metrics,
        meets_background_floor,
        run_original_policy,
    )
    from .factorized_background_study import (
        BACKGROUND_FLOOR,
        load_base_params,
        mean_metrics,
    )
    from .factorized_signal_study import FactorizedSignalRule
    from .three_signal_confirmation_study import (
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
    )
except ImportError:  # pragma: no cover
    import proof_harness as h
    from factorized_background_eligible_window_study import (
        BACKGROUND_FLOOR_TOLERANCE,
        EligibleWindowPressureSimulator,
        IdleEligibleFactorizedRule,
        TARGET_RATIO,
        foreground_parity_metrics,
        meets_background_floor,
        run_original_policy,
    )
    from factorized_background_study import (
        BACKGROUND_FLOOR,
        load_base_params,
        mean_metrics,
    )
    from factorized_signal_study import FactorizedSignalRule
    from three_signal_confirmation_study import (
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
    )


PROTOCOL_VERSION = "2026-08-05.eligible-window-deployment-stress-v5"
VALIDATION_SEED_BASE = 2_370_000
CONFIRMATION_SEED_BASE = 2_380_000
TTL_CANDIDATES = (0, 64, 256, 512, 1024, 2048)
UTILIZATION_DELTA_LIMIT = 0.08
PARITY_TOLERANCE = 1e-9


def ttl_label(ttl_epochs: int | None) -> str:
    return "unbounded" if ttl_epochs is None else f"ttl_{ttl_epochs}"


def ttl_has_expired(
    completion_time: float,
    current_time: float,
    ttl_epochs: int | None,
) -> bool:
    return ttl_epochs is not None and current_time > completion_time + ttl_epochs


class TTLEligibleWindowPressureSimulator(EligibleWindowPressureSimulator):
    """Eligible-window simulator with an explicit post-completion TTL."""

    def __init__(self, *args, deferred_ttl_epochs: int | None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if deferred_ttl_epochs is not None and deferred_ttl_epochs < 0:
            raise ValueError("deferred TTL must be non-negative or unbounded")
        self.deferred_ttl_epochs = deferred_ttl_epochs

    def workflow_background_ratio(self, workflow) -> float:
        total = max(1.0, sum(workflow.spec.background_sizes))
        served = sum(self.flows[flow_id].served for flow_id in workflow.background_flows)
        return served / total

    def expire_deferred_background(self, workflow) -> None:
        if workflow.complete_time is None:
            return
        if getattr(workflow, "deferred_ttl_expired", False):
            return
        if not ttl_has_expired(
            float(workflow.complete_time), float(self.time), self.deferred_ttl_epochs
        ):
            return
        if meets_background_floor(self.workflow_background_ratio(workflow)):
            return
        for flow_id in workflow.background_flows:
            flow = self.flows[flow_id]
            if flow.completed_at is None:
                flow.cancelled = True
        workflow.deferred_ttl_expired = True
        workflow.deferred_terminal_time = self.time

    def finish_workflow(self, workflow) -> None:
        super().finish_workflow(workflow)
        workflow.background_at_completion = workflow.background_bytes_served
        workflow.deferred_ttl_epochs = self.deferred_ttl_epochs
        workflow.deferred_ttl_expired = False
        workflow.deferred_target_reached_time = None
        workflow.deferred_terminal_time = None
        if meets_background_floor(self.workflow_background_ratio(workflow)):
            workflow.deferred_target_reached_time = workflow.complete_time
            workflow.deferred_terminal_time = workflow.complete_time
        elif self.deferred_ttl_epochs == 0:
            for flow_id in workflow.background_flows:
                flow = self.flows[flow_id]
                if flow.completed_at is None:
                    flow.cancelled = True
            workflow.deferred_ttl_expired = True
            workflow.deferred_terminal_time = workflow.complete_time

    def serve_active_flows(self) -> None:
        for workflow in self.workflows.values():
            self.expire_deferred_background(workflow)
        super().serve_active_flows()
        for workflow in self.workflows.values():
            if workflow.complete_time is None:
                continue
            if (
                getattr(workflow, "deferred_target_reached_time", None) is None
                and meets_background_floor(self.workflow_background_ratio(workflow))
            ):
                workflow.deferred_target_reached_time = self.time + 1
                workflow.deferred_terminal_time = self.time + 1


class QuiescentTTLEligibleWindowPressureSimulator(
    TTLEligibleWindowPressureSimulator
):
    """Materialize completed-owner background only during global foreground idle."""

    def suspend_deferred_background(self) -> None:
        for workflow in self.workflows.values():
            if workflow.complete_time is None:
                continue
            for flow_id in workflow.background_flows:
                flow = self.flows[flow_id]
                if flow.completed_at is None and not self.deferred_target_reached(flow):
                    flow.cancelled = True

    def materialize_deferred_background(self) -> None:
        if any(not flow.background for flow in self.active_flows()):
            return
        for workflow in self.workflows.values():
            if workflow.complete_time is None:
                continue
            if getattr(workflow, "deferred_ttl_expired", False):
                continue
            for flow_id in workflow.background_flows:
                flow = self.flows[flow_id]
                if flow.completed_at is None and not self.deferred_target_reached(flow):
                    debt = max(0.0, self.background_target(flow) - flow.served)
                    if debt > BACKGROUND_FLOOR_TOLERANCE:
                        flow.remaining = min(flow.remaining, debt)
                        flow.cancelled = False

    def has_pending_deferred_background(self) -> bool:
        for workflow in self.workflows.values():
            if workflow.complete_time is None:
                continue
            if getattr(workflow, "deferred_ttl_expired", False):
                continue
            for flow_id in workflow.background_flows:
                flow = self.flows[flow_id]
                if flow.completed_at is None and not self.deferred_target_reached(flow):
                    return True
        return False

    def progress_workflows(self) -> None:
        self.suspend_deferred_background()
        super().progress_workflows()

    def serve_active_flows(self) -> None:
        self.materialize_deferred_background()
        super().serve_active_flows()

    def run(self) -> Dict[str, object]:
        self.policy.reset_for_run()
        for self.time in range(self.max_time):
            self.spawn_arrivals()
            self.progress_workflows()
            self.serve_active_flows()
            self.progress_workflows()
            all_arrived = all(
                workflow.stage != "not_arrived" for workflow in self.workflows.values()
            )
            no_active = not self.active_flows() and not self.has_pending_deferred_background()
            all_done_or_arrived = all(
                workflow.complete_time is not None or workflow.stage != "not_arrived"
                for workflow in self.workflows.values()
            )
            if all_arrived and no_active and all_done_or_arrived:
                break
        for workflow in self.workflows.values():
            if workflow.complete_time is None and workflow.stage != "not_arrived":
                workflow.complete_time = self.max_time
                self.completed_workflows.append(workflow)
        return self.summary()


def run_ttl_policy(
    params: Mapping[str, float],
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    ttl_epochs: int | None,
) -> Tuple[Dict[str, object], Dict[str, float]]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = h.scaled_workload(
        workload_seed,
        load,
        1800,
        90,
        deadline_scale,
        optional_scale,
    )
    background_totals = {
        spec.workflow_id: max(1.0, sum(spec.background_sizes)) for spec in specs
    }
    simulator = QuiescentTTLEligibleWindowPressureSimulator(
        specs,
        IdleEligibleFactorizedRule(params),
        load,
        workload_seed,
        1800,
        6000,
        capacity_scale=capacity_scale,
        pressure_definition="active_speculative_backlog",
        deferred_ttl_epochs=ttl_epochs,
    )
    summary = simulator.run()
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
        }
    )
    records = list(summary["workflow_records"])
    ratios = []
    target_lags = []
    terminal_lags = []
    expired = []
    for record in records:
        workflow_id = int(record["workflow_id"])
        workflow = simulator.workflows[workflow_id]
        ratio = float(record["background_bytes_served"]) / background_totals[workflow_id]
        ratios.append(ratio)
        if workflow.stage != "done":
            continue
        complete_time = float(workflow.complete_time)
        reached_time = getattr(workflow, "deferred_target_reached_time", None)
        terminal_time = getattr(workflow, "deferred_terminal_time", None)
        if reached_time is not None:
            target_lags.append(max(0.0, float(reached_time) - complete_time))
        if terminal_time is not None:
            terminal_lags.append(max(0.0, float(terminal_time) - complete_time))
        elif ttl_epochs is None:
            terminal_lags.append(max(0.0, float(simulator.time) - complete_time))
        expired.append(float(getattr(workflow, "deferred_ttl_expired", False)))
    normal_completion_fraction = statistics.mean(
        workflow.stage == "done" for workflow in simulator.workflows.values()
    )
    metrics = {
        **h.state_metrics(records),
        "background_service_ratio": statistics.mean(ratios),
        "background_floor_fraction_workflows": statistics.mean(
            meets_background_floor(ratio) for ratio in ratios
        ),
        "background_shortfall_ratio": statistics.mean(
            max(0.0, TARGET_RATIO - ratio) for ratio in ratios
        ),
        "deferred_expiry_fraction": statistics.mean(expired),
        "mean_target_lag_epochs": statistics.mean(target_lags) if target_lags else math.nan,
        "p95_target_lag_epochs": h.up.percentile(target_lags, 0.95)
        if target_lags
        else math.nan,
        "p95_terminal_lag_epochs": h.up.percentile(terminal_lags, 0.95)
        if terminal_lags
        else math.nan,
        "normal_completion_fraction": normal_completion_fraction,
        "link_utilization": float(summary["link_utilization"]),
        "post_foreground_drain_time": max(
            0.0,
            float(simulator.time)
            - max(float(record["arrival_time"]) + float(record["latency"]) for record in records),
        ),
    }
    return summary, metrics


def evaluate(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
    ttl_values: Sequence[int | None],
    checkpoint_dir: Path | None = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    cells: List[Dict[str, object]] = []
    references: List[Dict[str, object]] = []
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = seed_base + run * 10_000 + scenario_index
            checkpoint = (
                checkpoint_dir / f"run_{run:02d}_scenario_{scenario_index:03d}.json"
                if checkpoint_dir is not None
                else None
            )
            payload = (
                json.loads(checkpoint.read_text(encoding="utf-8"))
                if checkpoint is not None and checkpoint.is_file()
                else None
            )
            if payload is not None and (
                payload.get("protocol_version") != PROTOCOL_VERSION
                or int(payload.get("seed", -1)) != seed
                or list(payload.get("scenario", [])) != list(scenario)
            ):
                raise ValueError(f"checkpoint protocol mismatch: {checkpoint}")
            if payload is None:
                reference_summary, reference_metrics = run_original_policy(
                    FactorizedSignalRule(params, "full"),
                    scenario,
                    seed,
                    1800,
                    90,
                    6000,
                )
                reference = {
                    "run": run,
                    "scenario": scenario_index,
                    "seed": seed,
                    **reference_metrics,
                }
                saved_cells: List[Dict[str, object]] = []
            else:
                reference = dict(payload["reference"])
                saved_cells = [dict(row) for row in payload["cells"]]
                reference_summary = None
            references.append(reference)
            completed_labels = {str(row["ttl_label"]) for row in saved_cells}
            for ttl_epochs in ttl_values:
                if ttl_label(ttl_epochs) in completed_labels:
                    continue
                if reference_summary is None:
                    reference_summary, _ = run_original_policy(
                        FactorizedSignalRule(params, "full"),
                        scenario,
                        seed,
                        1800,
                        90,
                        6000,
                    )
                summary, metrics = run_ttl_policy(params, scenario, seed, ttl_epochs)
                saved_cells.append(
                    {
                        "ttl_epochs": "unbounded" if ttl_epochs is None else ttl_epochs,
                        "ttl_label": ttl_label(ttl_epochs),
                        "run": run,
                        "scenario": scenario_index,
                        "seed": seed,
                        "load": scenario[0],
                        "deadline_scale": scenario[1],
                        "optional_scale": scenario[2],
                        "capacity_scale": scenario[3],
                        **metrics,
                        **foreground_parity_metrics(summary, reference_summary),
                    }
                )
                if checkpoint is not None:
                    h.write_json(
                        checkpoint,
                        {
                            "protocol_version": PROTOCOL_VERSION,
                            "seed": seed,
                            "scenario": scenario,
                            "reference": reference,
                            "cells": saved_cells,
                        },
                    )
            cells.extend(saved_cells)
        print(f"[ttl-stress] run {run + 1}/{runs}", flush=True)
    return cells, references


def summary_rows(
    cells: Sequence[Mapping[str, object]],
    references: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    reference_by_key = {
        (int(row["run"]), int(row["scenario"])): row for row in references
    }
    grouped: Dict[str, List[Mapping[str, object]]] = {}
    for cell in cells:
        grouped.setdefault(str(cell["ttl_label"]), []).append(cell)
    rows: List[Dict[str, object]] = []
    for label, items in grouped.items():
        metrics = mean_metrics(items)
        reference_items = [
            reference_by_key[(int(row["run"]), int(row["scenario"]))]
            for row in items
        ]
        reference_metrics = mean_metrics(reference_items)
        finite_ttl = None if label == "unbounded" else int(items[0]["ttl_epochs"])
        utilization_delta = metrics["link_utilization"] - reference_metrics["link_utilization"]
        gates = {
            "background_gate_pass": metrics["background_service_ratio"] >= BACKGROUND_FLOOR,
            "background_cell_floor_gate_pass": all(
                meets_background_floor(float(row["background_service_ratio"]))
                for row in items
            ),
            "background_workflow_floor_gate_pass": all(
                float(row["background_floor_fraction_workflows"])
                >= 1.0 - BACKGROUND_FLOOR_TOLERANCE
                for row in items
            ),
            "quality_gate_pass": metrics["quality"] >= QUALITY_FLOOR,
            "foreground_parity_gate_pass": all(
                int(float(row["foreground_parity_pass"])) for row in items
            ),
            "utilization_budget_pass": utilization_delta <= UTILIZATION_DELTA_LIMIT,
        }
        rows.append(
            {
                "ttl_label": label,
                "ttl_epochs": "unbounded" if finite_ttl is None else finite_ttl,
                "finite_ttl": int(finite_ttl is not None),
                "cells": len(items),
                "mean_background_service_ratio": metrics["background_service_ratio"],
                "background_floor_fraction_cells": statistics.mean(
                    meets_background_floor(float(row["background_service_ratio"]))
                    for row in items
                ),
                "mean_background_floor_fraction_workflows": statistics.mean(
                    float(row["background_floor_fraction_workflows"]) for row in items
                ),
                "mean_background_shortfall_ratio": statistics.mean(
                    float(row["background_shortfall_ratio"]) for row in items
                ),
                "mean_deferred_expiry_fraction": statistics.mean(
                    float(row["deferred_expiry_fraction"]) for row in items
                ),
                "mean_p95_target_lag_epochs": statistics.mean(
                    float(row["p95_target_lag_epochs"])
                    for row in items
                    if not math.isnan(float(row["p95_target_lag_epochs"]))
                ),
                "mean_p95_terminal_lag_epochs": statistics.mean(
                    float(row["p95_terminal_lag_epochs"])
                    for row in items
                    if not math.isnan(float(row["p95_terminal_lag_epochs"]))
                ),
                "mean_post_foreground_drain_time": statistics.mean(
                    float(row["post_foreground_drain_time"]) for row in items
                ),
                "mean_quality": metrics["quality"],
                "mean_p99_latency": metrics["p99_latency"],
                "mean_deadline_miss_ratio": metrics["deadline_miss_ratio"],
                "mean_link_utilization": metrics["link_utilization"],
                "utilization_delta_vs_original": utilization_delta,
                "foreground_parity_fraction_cells": statistics.mean(
                    float(row["foreground_parity_pass"]) for row in items
                ),
                "foreground_action_mismatches": sum(
                    float(row["foreground_action_mismatches"]) for row in items
                ),
                "foreground_state_mismatches": sum(
                    float(row["foreground_state_mismatches"]) for row in items
                ),
                "foreground_max_abs_latency_delta": max(
                    float(row["foreground_max_abs_latency_delta"]) for row in items
                ),
                "foreground_max_abs_waste_delta": max(
                    float(row["foreground_max_abs_waste_delta"]) for row in items
                ),
                **{key: int(value) for key, value in gates.items()},
                "all_deployment_gates_pass": int(all(gates.values())),
            }
        )
    return sorted(
        rows,
        key=lambda row: (not bool(row["finite_ttl"]), float("inf") if row["ttl_epochs"] == "unbounded" else int(row["ttl_epochs"])),
    )


def select_smallest_feasible_ttl(rows: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    feasible = [
        row
        for row in rows
        if int(row["finite_ttl"]) and int(row["all_deployment_gates_pass"])
    ]
    if not feasible:
        raise ValueError("no finite TTL passed every frozen deployment gate")
    return min(feasible, key=lambda row: int(row["ttl_epochs"]))


def write_report(
    output_dir: Path,
    manifest: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    selected: Mapping[str, object] | None,
) -> None:
    lines = [
        "# Eligible-window 有限 TTL 部署压力实验",
        "",
        "本实验冻结 v3 的三项控制参数，并把 deferred background 从整个 foreground busy period 的 active set 移除；它只在全局没有 foreground flow 的 quiescent epoch 物化。实验只把 workflow 完成后的 background 业务有效期设为外部环境变量，回答‘多长 TTL 才足以保住 20% service，同时不改变任何 foreground workflow’，而不是在确认集上继续调权重。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 模式：`{manifest['mode']}`",
        f"- Seed：`{manifest['seed_rule']}`",
        f"- 场景：{manifest['scenarios']} × runs：{manifest['runs']}`",
        f"- TTL 候选：`{manifest['ttl_values']}`；utilization 增量上限：`{UTILIZATION_DELTA_LIMIT:.2f}`。",
        "",
        "## 文献启发与测量边界",
        "",
        "- S1 Dean & Barroso, *The Tail at Scale* (CACM 2013, DOI `10.1145/2408776.2408794`) 将尾延迟作为系统级风险；因此本协议保留逐 workflow 前台 parity，而不是只比较平均延迟。",
        "- S2 Barroso & Hölzle, *The Case for Energy-Proportional Computing* (Computer 2007, DOI `10.1109/MC.2007.443`) 说明资源工作需要显式成本核算；因此记录 link-utilization 增量，但它只是能耗代理，不能写成实测能耗。",
        "- TTL、per-workflow floor、过期 shortfall 和 drain 共同刻画 background 的业务有效性；当前 simulator 没有真实任务价值、能耗计和 tenant 标识，不能把这些代理升级为生产 SLO。",
        "",
        "```mermaid",
        "flowchart LR",
        "  S1[尾延迟风险] --> P[逐 workflow parity]",
        "  S2[资源成本] --> U[utilization 增量预算]",
        "  T[业务 TTL] --> F[20% floor 与 expiry shortfall]",
        "  P --> G[有限 TTL 部署门]",
        "  U --> G",
        "  F --> G",
        "```",
        "",
        "## TTL 扫描结果",
        "",
        "| TTL | Background | Cell floor | Workflow floor | Expiry | p95 terminal lag (normal) | Drain | Δutilization | Parity | Gates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['ttl_label']} | {float(row['mean_background_service_ratio']):.4f} | "
            f"{float(row['background_floor_fraction_cells']):.3f} | "
            f"{float(row['mean_background_floor_fraction_workflows']):.3f} | "
            f"{float(row['mean_deferred_expiry_fraction']):.3f} | "
            f"{float(row['mean_p95_terminal_lag_epochs']):.2f} | "
            f"{float(row['mean_post_foreground_drain_time']):.2f} | "
            f"{float(row['utilization_delta_vs_original']):+.5f} | "
            f"{float(row['foreground_parity_fraction_cells']):.3f} | "
            f"{int(row['all_deployment_gates_pass'])} |"
        )
    lines += ["", "## 冻结选择与解释", ""]
    if selected is None:
        lines.append("本轮未选择有限 TTL；没有候选同时通过所有冻结门，必须保留为负结果。")
    else:
        lines += [
            f"验证集选择最小可行 TTL：`{selected['ttl_epochs']}` epochs。选择规则只比较有限 TTL 中全部硬门通过的候选，并取最小值；unbounded 行仅作上界参照。",
            "该 TTL 必须在独立 seed confirmation 上复核后才能作为当前 simulator 内的部署边界；它不证明真实 background 工作具有同样长的业务价值。",
        ]
    lines += [
        "",
        "## 冻结门",
        "",
        f"- mean background ≥ `{BACKGROUND_FLOOR:.2f}`，且每个 cell 在 `{BACKGROUND_FLOOR_TOLERANCE:.0e}` 容差下达到 floor；",
        "- 每个 cell 的每个 workflow 均须在相同容差下达到 floor；单元平均通过不足以替代逐 workflow 公平；",
        f"- quality ≥ `{QUALITY_FLOOR:.2f}`，每个 cell 的 action/state/latency/waste parity 必须精确通过（容差 `{PARITY_TOLERANCE:.0e}`）；",
        f"- 相对原语义的 mean link-utilization 增量 ≤ `{UTILIZATION_DELTA_LIMIT:.2f}`；该预算为协议内工程约束，并非文献给出的通用阈值。",
    ]
    (output_dir / "ELIGIBLE_WINDOW_TTL_STRESS_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirm"), required=True)
    parser.add_argument("--frozen-factorized-candidate", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--frozen-ttl-file")
    parser.add_argument("--runs", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    confirmation = args.mode == "confirm"
    if confirmation and not args.frozen_ttl_file:
        raise ValueError("confirmation requires --frozen-ttl-file from validation")
    if confirmation:
        selection_payload = json.loads(
            Path(args.frozen_ttl_file).read_text(encoding="utf-8")
        )
        if selection_payload.get("selected_ttl_epochs") is None:
            raise ValueError("validation did not identify a finite TTL for confirmation")
        selected_ttl = int(selection_payload["selected_ttl_epochs"])
        ttl_values: Tuple[int | None, ...] = (selected_ttl, None)
    else:
        ttl_values = (*TTL_CANDIDATES, None)
    runs = args.runs or (3 if confirmation else 1)
    seed_base = CONFIRMATION_SEED_BASE if confirmation else VALIDATION_SEED_BASE
    matrix = balanced_evaluation_matrix(
        h.scenarios("full"),
        27 if confirmation else 9,
        seed=29267 if confirmation else 28268,
    )
    params = load_base_params(Path(args.frozen_factorized_candidate))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cells, references = evaluate(
        params,
        matrix,
        runs,
        seed_base,
        ttl_values,
        output_dir / "checkpoints",
    )
    rows = summary_rows(cells, references)
    if confirmation:
        selected = next(row for row in rows if int(row["finite_ttl"]))
    else:
        try:
            selected = select_smallest_feasible_ttl(rows)
        except ValueError:
            selected = None
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "seed_rule": f"{seed_base} + run*10000 + scenario_index",
        "scenarios": len(matrix),
        "runs": runs,
        "ttl_values": ["unbounded" if value is None else value for value in ttl_values],
        "selected_params": params,
        "evaluation_matrix": matrix,
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "script_sha256": h.sha256(Path(__file__).resolve()),
    }
    h.write_csv(output_dir / "ttl_stress_cells.csv", cells)
    h.write_csv(output_dir / "original_semantics_reference_cells.csv", references)
    h.write_csv(output_dir / "ttl_stress_summary.csv", rows)
    h.write_json(output_dir / "run_manifest.json", manifest)
    h.write_json(
        output_dir / "selected_ttl.json",
        {
            "protocol_version": PROTOCOL_VERSION,
            "selected_ttl_epochs": int(selected["ttl_epochs"])
            if selected is not None
            else None,
            "selection_mode": args.mode,
        },
    )
    write_report(output_dir, manifest, rows, selected)
    print(f"[done] results written to {output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
