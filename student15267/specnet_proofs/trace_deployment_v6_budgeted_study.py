#!/usr/bin/env python3
"""Validation-only resource-budgeted successor to the V5 staged rule.

V5 applies a fixed terminal 96x multiplier once required work is sufficiently
drained.  V6 keeps V5's exact minimum-quality optional admission and explicit
completion barrier, but scales the terminal multiplier by observable optional
demand over the remaining completion horizon.  It therefore cannot create a
large completion reservation when the selected optional work is already small.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Tuple

try:
    from . import trace_deployment_v4_study as v4
    from . import trace_deployment_v5_resource_study as v5
except ImportError:  # pragma: no cover - direct script invocation
    import trace_deployment_v4_study as v4
    import trace_deployment_v5_resource_study as v5


PROTOCOL_VERSION = "2026-08-11.v6-budgeted-staged-validation"
BASE_BOOST = 1.0
TRIGGER = 0.75
TERMINAL_CAP = 96.0
RESERVE_MARGIN = v5.V4_RESERVE_MARGIN


class BudgetedStagedMinimumQualityRule(v5.StagedMinimumQualityRule):
    """Use an observable demand-based terminal priority rather than fixed 96x."""

    name = "v6_budgeted_staged"

    def __init__(self, seed: int, terminal_floor: float) -> None:
        if not BASE_BOOST <= terminal_floor <= TERMINAL_CAP:
            raise ValueError("terminal floor must lie between base boost and cap")
        super().__init__(seed, BASE_BOOST, TRIGGER, TERMINAL_CAP)
        self.terminal_floor = float(terminal_floor)
        self.name = f"v6_budgeted_b1_t0.75_f{terminal_floor:g}_cap96_m0.35"

    def terminal_multiplier(self, owner, sim) -> float:
        """Bound optional priority by the observable remaining byte demand."""
        own_optional = sum(
            candidate.remaining
            for candidate in sim.active_flows()
            if candidate.workflow_id == owner.spec.workflow_id
            and candidate.speculative
            and not candidate.background
        )
        horizon = sim.optional_completion_horizon(owner)
        demand = own_optional / max(sim.capacity * horizon, 1e-9)
        urgency = v4.clamp01(
            (demand - RESERVE_MARGIN) / max(1e-9, 1.0 - RESERVE_MARGIN)
        )
        return self.terminal_floor + (self.terminal_boost - self.terminal_floor) * urgency

    def flow_weight(self, flow, sim) -> float:
        weight = super(v4.DeadlineReservedMinimumQualityRule, self).flow_weight(flow, sim)
        if not flow.speculative or flow.background:
            return weight
        owner = sim.workflows.get(flow.workflow_id)
        if owner is None:
            return weight
        required_total = sum(float(branch.size) for branch in owner.spec.branches if branch.required)
        required_remaining = sum(candidate.remaining for candidate in sim.active_flows() if candidate.workflow_id == flow.workflow_id and candidate.required)
        progress = 1.0 - required_remaining / max(required_total, 1e-9)
        terminal = owner.stage in {"llm", "judge"} or progress >= self.required_progress_trigger
        multiplier = self.terminal_multiplier(owner, sim) if terminal else self.base_optional_boost
        state = getattr(owner, "observable_state", None)
        if state is not None and state[1] == "tight":
            multiplier *= self.tight_optional_compensation
        return weight * multiplier


class DebtGuardedBudgetedRule(BudgetedStagedMinimumQualityRule):
    """V7: fall back to V5's rapid drain when observed resource pressure is high."""

    def __init__(self, seed: int, terminal_floor: float, resource_pressure_cap: float) -> None:
        if resource_pressure_cap < 0.0:
            raise ValueError("resource pressure cap must be non-negative")
        super().__init__(seed, terminal_floor)
        self.resource_pressure_cap = float(resource_pressure_cap)
        self.name = f"v7_debt_guarded_f{terminal_floor:g}_r{resource_pressure_cap:g}"

    def terminal_multiplier(self, owner, sim) -> float:
        # This ratio is available before choosing the flow's next service
        # weight; it does not inspect future arrivals or completion outcomes.
        pressure = sim.remaining_active_bytes() / max(sim.capacity * 12.0, 1e-9)
        if pressure >= self.resource_pressure_cap:
            return self.terminal_boost
        return super().terminal_multiplier(owner, sim)


def write_csv(path: Path, rows: List[Mapping[str, object]]) -> None:
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def mean_rows(rows: Iterable[Mapping[str, object]]) -> Dict[str, float]:
    return v5.mean_rows(rows)


def gate(row: Mapping[str, float], reference: Mapping[str, float]) -> bool:
    return (
        row["avg_quality"] >= v4.TRACE_QUALITY_TARGET
        and row["quality_target_met_ratio"] >= 0.95
        and row["min_cell_quality_target_ratio"] >= 1.0
        and row["template_quality_target_min"] >= 1.0
        and row["p99_latency"] <= reference["p99_latency"] + 1e-9
        and row["deadline_miss_ratio"] <= reference["deadline_miss_ratio"] + 0.02 + 1e-9
        and row["total_served_bytes"] <= reference["total_served_bytes"] + 1e-9
        and row["link_utilization"] <= reference["link_utilization"] + 1e-9
        and row.get("worst_cell_gate_pass", 1.0) >= 1.0
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=v4.PROFILE_NAMES, required=True)
    parser.add_argument("--data-root", type=Path, default=v4.DEFAULT_DATA_ROOT)
    parser.add_argument("--scenario-count", type=int, default=9)
    parser.add_argument("--evaluation-runs", type=int, default=1)
    parser.add_argument("--seed-base", type=int, default=2_660_000)
    parser.add_argument("--terminal-floor", type=float, default=1.0)
    parser.add_argument("--resource-pressure-cap", type=float, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    trace_profile = v4.profile_path(args.profile, args.data_root)
    matrix = v4.balanced_evaluation_matrix(v4.h.scenarios("smoke"), args.scenario_count, seed=266_081)
    candidate_key = "v7_debt_guarded" if args.resource_pressure_cap is not None else "v6_budgeted"
    variants: Dict[str, List[Dict[str, object]]] = {"v3_static_100x": [], "v4_minq_96": [], "v5_staged": [], candidate_key: []}
    print(f"[v6 validation] {args.profile}: {len(matrix)} scenarios x {args.evaluation_runs} runs", flush=True)
    for evaluation_run in range(args.evaluation_runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = args.seed_base + evaluation_run * 10_000 + scenario_index
            policies = {
                "v3_static_100x": v4.TraceQualitySafeFactorizedRule(v4.FROZEN_PARAMS, "full", seed, optional_completion_boost=v4.V3_OPTIONAL_COMPLETION_BOOST, tight_optional_completion_boost=v4.V3_TIGHT_OPTIONAL_COMPLETION_BOOST),
                "v4_minq_96": v4.DeadlineReservedMinimumQualityRule(seed, v5.V4_BASE_BOOST, v5.V4_URGENCY_GAIN, reserve_margin=v5.V4_RESERVE_MARGIN),
                "v5_staged": v5.StagedMinimumQualityRule(seed, BASE_BOOST, TRIGGER),
                candidate_key: (
                    DebtGuardedBudgetedRule(seed, args.terminal_floor, args.resource_pressure_cap)
                    if args.resource_pressure_cap is not None
                    else BudgetedStagedMinimumQualityRule(seed, args.terminal_floor)
                ),
            }
            names = {"v3_static_100x": "v3_static_100x", "v4_minq_96": "v4_minq_96", "v5_staged": "v5_staged_b1_t0.75_z96", candidate_key: candidate_key}
            for label, policy in policies.items():
                summary = v5.run_once(names[label], policy, scenario, seed, args.profile, trace_profile, "validation")
                variants[label].append({"evaluation_run": evaluation_run, "scenario": scenario_index, **v5.metric_row(summary)})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    aggregates = {label: mean_rows(rows) for label, rows in variants.items()}
    pairwise_rows: List[Dict[str, object]] = []
    for candidate, reference_cell in zip(variants[candidate_key], variants["v4_minq_96"]):
        row: Dict[str, object] = {
            "evaluation_run": candidate["evaluation_run"],
            "scenario": candidate["scenario"],
            "quality_target_ratio": candidate["quality_target_met_ratio"],
        }
        for metric in ("p99_latency", "deadline_miss_ratio", "total_served_bytes", "link_utilization"):
            row[f"delta_{metric}_vs_v4"] = float(candidate[metric]) - float(reference_cell[metric])
        row["cell_gate_pass"] = int(
            float(row["quality_target_ratio"]) >= 1.0
            and float(row["delta_p99_latency_vs_v4"]) <= 1e-9
            and float(row["delta_deadline_miss_ratio_vs_v4"]) <= 0.02 + 1e-9
            and float(row["delta_total_served_bytes_vs_v4"]) <= 1e-9
            and float(row["delta_link_utilization_vs_v4"]) <= 1e-9
        )
        pairwise_rows.append(row)
    by_scenario: Dict[int, List[Dict[str, object]]] = defaultdict(list)
    for row in pairwise_rows:
        by_scenario[int(row["scenario"])].append(row)
    scenario_pairwise_rows: List[Dict[str, object]] = []
    for scenario, rows in sorted(by_scenario.items()):
        row: Dict[str, object] = {
            "scenario": scenario,
            "paired_runs": len(rows),
            "min_quality_target_ratio": min(float(item["quality_target_ratio"]) for item in rows),
        }
        for metric in ("p99_latency", "deadline_miss_ratio", "total_served_bytes", "link_utilization"):
            row[f"delta_{metric}_vs_v4"] = statistics.fmean(
                float(item[f"delta_{metric}_vs_v4"]) for item in rows
            )
        row["scenario_gate_pass"] = int(
            float(row["min_quality_target_ratio"]) >= 1.0
            and float(row["delta_p99_latency_vs_v4"]) <= 1e-9
            and float(row["delta_deadline_miss_ratio_vs_v4"]) <= 0.02 + 1e-9
            and float(row["delta_total_served_bytes_vs_v4"]) <= 1e-9
            and float(row["delta_link_utilization_vs_v4"]) <= 1e-9
        )
        scenario_pairwise_rows.append(row)
    summary_rows: List[Dict[str, object]] = []
    reference = aggregates["v4_minq_96"]
    for label, rows in variants.items():
        aggregate = dict(aggregates[label])
        aggregate["variant"] = label
        aggregate["min_cell_quality_target_ratio"] = min(float(row["quality_target_met_ratio"]) for row in rows)
        aggregate["template_quality_target_min"] = min(float(row["template_quality_target_min"]) for row in rows)
        if label == candidate_key:
            aggregate["worst_cell_gate_pass"] = int(all(int(row["scenario_gate_pass"]) for row in scenario_pairwise_rows))
        for metric in ("p99_latency", "deadline_miss_ratio", "total_served_bytes", "link_utilization", "path_queue_pressure_p99"):
            aggregate[f"delta_{metric}_vs_v4"] = aggregate[metric] - reference[metric]
        aggregate["candidate_gate_pass_vs_v4"] = int(gate(aggregate, reference)) if label == candidate_key else ""
        summary_rows.append(aggregate)
        write_csv(args.output_dir / f"{label}_cells.csv", rows)
    write_csv(args.output_dir / "summary.csv", summary_rows)
    write_csv(args.output_dir / f"{candidate_key}_vs_v4_pairwise_cells.csv", pairwise_rows)
    write_csv(args.output_dir / f"{candidate_key}_vs_v4_pairwise_scenarios.csv", scenario_pairwise_rows)
    report = [
        "# Budgeted-Staged Validation", "",
        "The candidate uses the same minimum-quality admission and completion barrier as V5. V6 turns fixed terminal 96x priority into an observable demand-based multiplier; V7 additionally falls back to V5 at a preconfigured resource-pressure cap. No test split was read.", "",
        "| Variant | Quality | p99 | Bytes | Utilization | Δp99 vs V4 | Δbytes vs V4 | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary_rows:
        report.append(
            f"| {row['variant']} | {float(row['avg_quality']):.4f} | {float(row['p99_latency']):.3f} | {float(row['total_served_bytes']):.2f} | "
            f"{float(row['link_utilization']):.6f} | {float(row['delta_p99_latency_vs_v4']):+.3f} | {float(row['delta_total_served_bytes_vs_v4']):+.2f} | {row['v6_gate_pass_vs_v4'] or '-'} |"
        )
    (args.output_dir / "BUDGETED_STAGED_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"[done] budgeted-staged validation written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
