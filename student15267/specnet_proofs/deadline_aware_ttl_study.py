#!/usr/bin/env python3
"""Test a deadline-aware idle scheduler for finite deferred-background TTLs."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .eligible_window_deployment_stress_study import (
        PARITY_TOLERANCE,
        UTILIZATION_DELTA_LIMIT,
        QuiescentTTLEligibleWindowPressureSimulator,
    )
    from .factorized_background_eligible_window_study import (
        BACKGROUND_FLOOR_TOLERANCE,
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
    from eligible_window_deployment_stress_study import (
        PARITY_TOLERANCE,
        UTILIZATION_DELTA_LIMIT,
        QuiescentTTLEligibleWindowPressureSimulator,
    )
    from factorized_background_eligible_window_study import (
        BACKGROUND_FLOOR_TOLERANCE,
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


PROTOCOL_VERSION = "2026-08-05.deadline-aware-ttl-v1"
VALIDATION_SEED_BASE = 2_410_000
CONFIRMATION_SEED_BASE = 2_420_000
TTL_CANDIDATES = (1536, 1664, 1792, 1920, 2048)
IDLE_NON_URGENT_WEIGHT = 1e-12


class EarliestExpiryIdleEligibleRule(IdleEligibleFactorizedRule):
    """Serve the earliest finite deferred expiry first during foreground idle.

    Live workflows retain the frozen factorized control path.  A completed
    owner's background remains invisible while any foreground flow exists;
    only the ordering inside a globally idle deferred window changes.
    """

    def flow_weight(self, flow, sim) -> float:
        if not flow.background:
            return super().flow_weight(flow, sim)
        owner = sim.workflows.get(flow.workflow_id)
        if owner is None or owner.complete_time is None:
            return super().flow_weight(flow, sim)
        if any(not item.background for item in sim.active_flows()):
            return 0.0
        ttl_epochs = getattr(sim, "deferred_ttl_epochs", None)
        if ttl_epochs is None:
            return 0.5
        eligible_deadlines = []
        for item in sim.active_flows():
            if not item.background:
                continue
            item_owner = sim.workflows.get(item.workflow_id)
            if item_owner is None or item_owner.complete_time is None:
                continue
            if sim.deferred_target_reached(item):
                continue
            eligible_deadlines.append(float(item_owner.complete_time) + ttl_epochs)
        if not eligible_deadlines:
            return 0.0
        deadline = float(owner.complete_time) + ttl_epochs
        earliest = min(eligible_deadlines)
        return 1.0 if deadline <= earliest + BACKGROUND_FLOOR_TOLERANCE else IDLE_NON_URGENT_WEIGHT


def percentile_or_nan(values: Sequence[float], probability: float) -> float:
    return h.up.percentile(list(values), probability) if values else math.nan


def run_deadline_aware_policy(
    params: Mapping[str, float],
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    ttl_epochs: int,
) -> Tuple[Dict[str, object], Dict[str, float], List[Dict[str, object]]]:
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
        EarliestExpiryIdleEligibleRule(params),
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
    records = {
        int(record["workflow_id"]): record for record in summary["workflow_records"]
    }
    ratios: List[float] = []
    expiry: List[float] = []
    target_lags: List[float] = []
    workflow_rows: List[Dict[str, object]] = []
    for workflow_id, workflow in sorted(simulator.workflows.items()):
        record = records.get(workflow_id)
        if record is None:
            continue
        background_total = background_totals[workflow_id]
        ratio = float(record["background_bytes_served"]) / background_total
        ratios.append(ratio)
        complete_time = getattr(workflow, "complete_time", None)
        target_time = getattr(workflow, "deferred_target_reached_time", None)
        target_lag = (
            max(0.0, float(target_time) - float(complete_time))
            if complete_time is not None and target_time is not None
            else math.nan
        )
        if not math.isnan(target_lag):
            target_lags.append(target_lag)
        expired = float(getattr(workflow, "deferred_ttl_expired", False))
        expiry.append(expired)
        at_completion = float(getattr(workflow, "background_at_completion", 0.0))
        debt_ratio = max(0.0, TARGET_RATIO - at_completion / background_total)
        workflow_rows.append(
            {
                "workflow_id": workflow_id,
                "background_ratio": ratio,
                "floor_pass": int(meets_background_floor(ratio)),
                "background_ratio_at_completion": at_completion / background_total,
                "post_completion_debt_ratio": debt_ratio,
                "deferred_required": int(debt_ratio > BACKGROUND_FLOOR_TOLERANCE),
                "target_lag_epochs": target_lag,
                "deferred_ttl_expired": int(expired),
            }
        )
    metrics = {
        **h.state_metrics(list(records.values())),
        "background_service_ratio": statistics.mean(ratios),
        "background_floor_fraction_workflows": statistics.mean(
            meets_background_floor(ratio) for ratio in ratios
        ),
        "deferred_expiry_fraction": statistics.mean(expiry),
        "p50_target_lag_epochs": percentile_or_nan(target_lags, 0.50),
        "p95_target_lag_epochs": percentile_or_nan(target_lags, 0.95),
        "p99_target_lag_epochs": percentile_or_nan(target_lags, 0.99),
        "max_target_lag_epochs": max(target_lags, default=math.nan),
        "link_utilization": float(summary["link_utilization"]),
        "post_foreground_drain_time": max(
            0.0,
            float(simulator.time)
            - max(
                float(record["arrival_time"]) + float(record["latency"])
                for record in records.values()
            ),
        ),
    }
    return summary, metrics, workflow_rows


def evaluate(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
    ttl_values: Sequence[int],
    checkpoint_dir: Path | None = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    cells: List[Dict[str, object]] = []
    workflows: List[Dict[str, object]] = []
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
                    FactorizedSignalRule(params, "full"), scenario, seed, 1800, 90, 6000
                )
                reference = dict(reference_metrics)
                saved_cells: List[Dict[str, object]] = []
                saved_workflows: List[Dict[str, object]] = []
            else:
                reference_summary, _ = run_original_policy(
                    FactorizedSignalRule(params, "full"), scenario, seed, 1800, 90, 6000
                )
                reference = dict(payload["reference"])
                saved_cells = [dict(row) for row in payload["cells"]]
                saved_workflows = [dict(row) for row in payload["workflows"]]
            completed = {int(row["ttl_epochs"]) for row in saved_cells}
            for ttl_epochs in ttl_values:
                if ttl_epochs in completed:
                    continue
                summary, metrics, rows = run_deadline_aware_policy(
                    params, scenario, seed, ttl_epochs
                )
                cell = {
                    "ttl_epochs": ttl_epochs,
                    "run": run,
                    "scenario": scenario_index,
                    "seed": seed,
                    "load": scenario[0],
                    "deadline_scale": scenario[1],
                    "optional_scale": scenario[2],
                    "capacity_scale": scenario[3],
                    **metrics,
                    **foreground_parity_metrics(summary, reference_summary),
                    "reference_link_utilization": float(reference["link_utilization"]),
                }
                saved_cells.append(cell)
                saved_workflows.extend(
                    {
                        "ttl_epochs": ttl_epochs,
                        "run": run,
                        "scenario": scenario_index,
                        "seed": seed,
                        "load": scenario[0],
                        "deadline_scale": scenario[1],
                        "optional_scale": scenario[2],
                        "capacity_scale": scenario[3],
                        **row,
                    }
                    for row in rows
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
                            "workflows": saved_workflows,
                        },
                    )
            cells.extend(saved_cells)
            workflows.extend(saved_workflows)
        print(f"[deadline-aware] run {run + 1}/{runs}", flush=True)
    return cells, workflows


def summary_rows(cells: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[int, List[Mapping[str, object]]] = {}
    for cell in cells:
        groups.setdefault(int(cell["ttl_epochs"]), []).append(cell)
    rows = []
    for ttl_epochs, items in sorted(groups.items()):
        metrics = mean_metrics(items)
        utilization_delta = statistics.mean(
            float(row["link_utilization"]) - float(row["reference_link_utilization"])
            for row in items
        )
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
            "expiry_gate_pass": all(
                float(row["deferred_expiry_fraction"]) == 0.0 for row in items
            ),
            "quality_gate_pass": metrics["quality"] >= QUALITY_FLOOR,
            "foreground_parity_gate_pass": all(
                int(float(row["foreground_parity_pass"])) for row in items
            ),
            "utilization_budget_pass": utilization_delta <= UTILIZATION_DELTA_LIMIT,
        }
        rows.append(
            {
                "ttl_epochs": ttl_epochs,
                "cells": len(items),
                "mean_background_service_ratio": metrics["background_service_ratio"],
                "background_floor_fraction_cells": statistics.mean(
                    meets_background_floor(float(row["background_service_ratio"]))
                    for row in items
                ),
                "background_floor_fraction_workflows": statistics.mean(
                    float(row["background_floor_fraction_workflows"]) for row in items
                ),
                "mean_deferred_expiry_fraction": statistics.mean(
                    float(row["deferred_expiry_fraction"]) for row in items
                ),
                "mean_quality": metrics["quality"],
                "mean_p99_latency": metrics["p99_latency"],
                "mean_deadline_miss_ratio": metrics["deadline_miss_ratio"],
                "mean_link_utilization": metrics["link_utilization"],
                "utilization_delta_vs_original": utilization_delta,
                "mean_p99_target_lag_epochs": statistics.mean(
                    float(row["p99_target_lag_epochs"])
                    for row in items
                    if not math.isnan(float(row["p99_target_lag_epochs"]))
                ),
                "max_target_lag_epochs": max(
                    float(row["max_target_lag_epochs"])
                    for row in items
                    if not math.isnan(float(row["max_target_lag_epochs"]))
                ),
                "mean_post_foreground_drain_time": statistics.mean(
                    float(row["post_foreground_drain_time"]) for row in items
                ),
                "foreground_parity_fraction_cells": statistics.mean(
                    float(row["foreground_parity_pass"]) for row in items
                ),
                **{key: int(value) for key, value in gates.items()},
                "all_deployment_gates_pass": int(all(gates.values())),
            }
        )
    return rows


def select_smallest_feasible(rows: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    feasible = [row for row in rows if int(row["all_deployment_gates_pass"])]
    if not feasible:
        raise ValueError("no deadline-aware TTL candidate passed all frozen gates")
    return min(feasible, key=lambda row: int(row["ttl_epochs"]))


def write_report(
    output_dir: Path,
    manifest: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    selected: Mapping[str, object] | None,
) -> None:
    lines = [
        "# Deadline-aware 有限 TTL 实验",
        "",
        "该实验只改变全局 foreground idle epoch 内已完成 workflow 的 deferred debt 顺序：从等权分享改为 earliest-expiry-first。所有 live-workflow 路径、三项参数、foreground busy-period 可见性和 20% floor 均保持冻结。",
        "",
        f"- 协议：`{manifest['protocol_version']}`；模式：`{manifest['mode']}`。",
        f"- Seed：`{manifest['seed_rule']}`；场景：`{manifest['scenarios']}` x runs：`{manifest['runs']}`。",
        f"- TTL 候选：`{manifest['ttl_values']}`。",
        "",
        "## 设计依据",
        "",
        "- Liu 与 Layland, *Scheduling Algorithms for Multiprogramming in a Hard-Real-Time Environment* (JACM 1973, DOI `10.1145/321738.321743`) 说明在其单机、可抢占实时模型下 EDF 具有可行性最优性。这里借用最早 expiry 优先的排序思想，但没有把该定理外推为本 simulator 的正式证明。",
        "- Dean 与 Barroso, *The Tail at Scale* (CACM 2013, DOI `10.1145/2408776.2408794`) 启发逐 workflow tail/floor 门；因此任何均值 improvement 都不能覆盖一个过期 workflow。",
        "",
        "## 扫描结果",
        "",
        "| TTL | Background | Cell floor | Workflow floor | Expiry | p99 horizon | Delta utilization | Parity | Gates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {int(row['ttl_epochs'])} | {float(row['mean_background_service_ratio']):.4f} | "
            f"{float(row['background_floor_fraction_cells']):.3f} | "
            f"{float(row['background_floor_fraction_workflows']):.3f} | "
            f"{float(row['mean_deferred_expiry_fraction']):.4f} | "
            f"{float(row['mean_p99_target_lag_epochs']):.2f} | "
            f"{float(row['utilization_delta_vs_original']):+.5f} | "
            f"{float(row['foreground_parity_fraction_cells']):.3f} | "
            f"{int(row['all_deployment_gates_pass'])} |"
        )
    lines += ["", "## 选择与边界", ""]
    if selected is None:
        lines.append("没有候选通过全部冻结门；该机制保留为负结果，不能替代已确认 TTL=2048 边界。")
    else:
        lines += [
            f"validation 选择最小可行 TTL：`{int(selected['ttl_epochs'])}` epochs。只有独立 confirmation 同时通过逐 cell、逐 workflow、expiry、前台 parity、质量和资源门后才能引用。",
            f"前台 parity 使用 `{PARITY_TOLERANCE:.0e}` 容差；link-utilization 增量预算为 `{UTILIZATION_DELTA_LIMIT:.2f}`。它不是能耗测量，也没有真实任务价值或 tenant 公平标签。",
        ]
    (output_dir / "DEADLINE_AWARE_TTL_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirm"), required=True)
    parser.add_argument("--frozen-factorized-candidate", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--frozen-selection-file")
    parser.add_argument("--runs", type=int, default=None)
    parser.add_argument("--scenarios", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    confirmation = args.mode == "confirm"
    if confirmation and not args.frozen_selection_file:
        raise ValueError("confirmation requires --frozen-selection-file from validation")
    runs = args.runs or (3 if confirmation else 1)
    scenario_count = args.scenarios or (27 if confirmation else 27)
    seed_base = CONFIRMATION_SEED_BASE if confirmation else VALIDATION_SEED_BASE
    matrix = balanced_evaluation_matrix(
        h.scenarios("full"),
        scenario_count,
        seed=31269 if confirmation else 31268,
    )
    params = load_base_params(Path(args.frozen_factorized_candidate))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if confirmation:
        selection = json.loads(
            Path(args.frozen_selection_file).read_text(encoding="utf-8")
        )
        ttl_values = (int(selection["selected_ttl_epochs"]),)
    else:
        ttl_values = TTL_CANDIDATES
    cells, workflows = evaluate(
        params,
        matrix,
        runs,
        seed_base,
        ttl_values,
        output_dir / "checkpoints",
    )
    rows = summary_rows(cells)
    if confirmation:
        selected = rows[0]
    else:
        try:
            selected = select_smallest_feasible(rows)
        except ValueError:
            selected = None
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "policy": "earliest_expiry_idle_deferred",
        "seed_rule": f"{seed_base} + run*10000 + scenario_index",
        "scenarios": len(matrix),
        "runs": runs,
        "ttl_values": list(ttl_values),
        "evaluation_matrix": matrix,
        "selected_params": params,
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "script_sha256": h.sha256(Path(__file__).resolve()),
    }
    h.write_csv(output_dir / "deadline_aware_ttl_cells.csv", cells)
    h.write_csv(output_dir / "deadline_aware_ttl_workflows.csv", workflows)
    h.write_csv(output_dir / "deadline_aware_ttl_summary.csv", rows)
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
