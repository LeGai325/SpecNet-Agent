#!/usr/bin/env python3
"""Compare alternative definitions of the speculative-pressure signal.

This study is intentionally separate from proof_harness.py's preregistered
H1-P result. It keeps the original definition as a baseline, trains an
independent full and no-pressure controller for every candidate definition,
and evaluates waste only after applying a common quality floor.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
except ImportError:  # pragma: no cover - useful when run from this directory
    import proof_harness as h


PRESSURE_DEFINITIONS = (
    "original_ratio",
    "active_speculative_backlog",
    "workflow_optional_ratio",
    "cancelable_queue_length",
    "speculative_age",
    "expected_waste_risk",
)

PRESSURE_LABELS = {
    "original_ratio": "原始全局 speculative 比例",
    "active_speculative_backlog": "绝对 speculative backlog",
    "workflow_optional_ratio": "当前 workflow 可选字节比例",
    "cancelable_queue_length": "可取消分支数量",
    "speculative_age": "speculative 流等待年龄",
    "expected_waste_risk": "预计浪费风险",
}


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def numeric_bucket(definition: str, value: float) -> str:
    """Map a frozen feature value to the common three-state vocabulary."""
    if definition == "cancelable_queue_length":
        if value < 1.0:
            return "low_spec"
        if value < 4.0:
            return "mid_spec"
        return "high_spec"
    if definition == "speculative_age":
        if value < 3.0:
            return "low_spec"
        if value < 9.0:
            return "mid_spec"
        return "high_spec"
    if value < 0.15:
        return "low_spec"
    if value < 0.35:
        return "mid_spec"
    return "high_spec"


class PressureSimulator(h.ProofSimulator):
    """ProofSimulator with a replaceable, action-independent pressure feature."""

    def __init__(self, *args, pressure_definition: str, **kwargs) -> None:
        if pressure_definition not in PRESSURE_DEFINITIONS:
            raise ValueError(f"unknown pressure definition: {pressure_definition}")
        self.pressure_definition = pressure_definition
        super().__init__(*args, **kwargs)

    def active_speculative_flows(self):
        return [flow for flow in self.active_flows() if flow.speculative]

    def pressure_value(self, workflow) -> float:
        active = self.active_flows()
        speculative = self.active_speculative_flows()
        spec_bytes = sum(flow.remaining for flow in speculative)
        total_bytes = sum(flow.remaining for flow in active)
        definition = self.pressure_definition

        if definition == "original_ratio":
            return spec_bytes / total_bytes if total_bytes else 0.0
        if definition == "active_speculative_backlog":
            return spec_bytes / max(1.0, self.capacity * 12.0)
        if definition == "workflow_optional_ratio":
            branch_bytes = sum(branch.size for branch in workflow.spec.branches)
            optional_bytes = sum(branch.size for branch in workflow.spec.branches if not branch.required)
            return optional_bytes / max(1.0, branch_bytes)
        if definition == "cancelable_queue_length":
            return float(len(speculative))
        if definition == "speculative_age":
            return float(max((self.time - flow.created_at for flow in speculative), default=0))
        if definition == "expected_waste_risk":
            # A transparent proxy: bytes are riskier when their owner is already
            # beyond its estimated critical-time budget or the flow is old.
            risk_weighted_bytes = 0.0
            for flow in speculative:
                owner = self.workflows.get(flow.workflow_id)
                urgency = 0.0
                if owner is not None:
                    estimate = self.estimated_remaining_critical_time(owner)
                    budget = owner.deadline_time - self.time
                    urgency = clamp01((estimate - budget) / max(estimate, 1e-9))
                age = clamp01((self.time - flow.created_at) / 12.0)
                risk_weighted_bytes += flow.remaining * (0.65 * urgency + 0.35 * age)
            return risk_weighted_bytes / max(1.0, self.capacity * 12.0)
        raise AssertionError(definition)

    def pressure_bucket(self, workflow) -> str:
        return numeric_bucket(self.pressure_definition, self.pressure_value(workflow))

    def observable_state(self, workflow) -> Tuple[str, str, str]:
        value = self.pressure_value(workflow)
        workflow.pressure_value = value
        return (
            self.congestion_level(),
            self.workflow_slack_bucket(workflow),
            numeric_bucket(self.pressure_definition, value),
        )

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        by_id = {workflow.spec.workflow_id: workflow for workflow in self.completed_workflows}
        for record in result["workflow_records"]:
            workflow = by_id[record["workflow_id"]]
            record["pressure_value"] = getattr(workflow, "pressure_value", 0.0)
            record["pressure_definition"] = self.pressure_definition
        return result


def make_bandit_class(definition: str, disable_pressure: bool):
    policy_name = f"{definition}_{'no_pressure' if disable_pressure else 'full'}"

    class PressureBandit(h.AuditedBandit):
        name = policy_name

        def state_key(self, sim, workflow):
            pressure = "all_spec" if disable_pressure else sim.pressure_bucket(workflow)
            return (sim.congestion_level(), sim.workflow_slack_bucket(workflow), pressure)

    PressureBandit.__name__ = "PressureBandit_" + policy_name
    return PressureBandit


def train_policy(
    policy_class,
    definition: str,
    seed: int,
    episodes: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    matrix: Sequence[Tuple[str, float, float, float]],
):
    policy = policy_class(seed=seed, train=True, epsilon=0.18, learning_rate=0.25)
    for episode in range(episodes):
        load, deadline_scale, optional_scale, capacity_scale = matrix[episode % len(matrix)]
        workload_seed = seed + 10000 + episode
        specs = h.scaled_workload(
            workload_seed, load, duration, max_workflows, deadline_scale, optional_scale
        )
        sim = PressureSimulator(
            specs,
            policy,
            load,
            workload_seed,
            duration,
            max_time,
            capacity_scale=capacity_scale,
            pressure_definition=definition,
        )
        sim.run()
    policy.set_evaluation_mode()
    return policy


def run_once(
    policy,
    definition: str,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Dict[str, object]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = h.scaled_workload(
        workload_seed, load, duration, max_workflows, deadline_scale, optional_scale
    )
    sim = PressureSimulator(
        specs,
        policy,
        load,
        workload_seed,
        duration,
        max_time,
        capacity_scale=capacity_scale,
        pressure_definition=definition,
    )
    summary = sim.run()
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
        }
    )
    return summary


def record_rows(summary: Mapping[str, object], policy_name: str, definition: str, train_seed: int, run: int, scenario: int) -> List[Dict[str, object]]:
    rows = []
    for record in summary["workflow_records"]:
        rows.append(
            {
                **record,
                "policy": policy_name,
                "definition": definition,
                "train_seed": train_seed,
                "run": run,
                "scenario": scenario,
                "load": summary["load"],
                "deadline_scale": summary["deadline_scale"],
                "optional_scale": summary["optional_scale"],
                "capacity_scale": summary["capacity_scale"],
            }
        )
    return rows


def metrics(records: Sequence[Mapping[str, object]]) -> Dict[str, float]:
    return h.state_metrics(records)


def paired_rows(
    rows: Sequence[Mapping[str, object]],
    definition: str,
    quality_floor: float,
) -> List[Dict[str, object]]:
    indexed = {
        (str(row["policy"]), int(row["train_seed"]), int(row["run"]), int(row["scenario"]), int(row["workflow_id"])): row
        for row in rows
        if row["definition"] == definition
    }
    units: List[Dict[str, object]] = []
    groups = sorted(
        {
            (int(row["train_seed"]), int(row["run"]), int(row["scenario"]))
            for row in rows
            if row["definition"] == definition and row["policy"] == f"{definition}_full"
        }
    )
    for train_seed, run, scenario in groups:
        full_rows = [
            row
            for key, row in indexed.items()
            if key[:4] == (f"{definition}_full", train_seed, run, scenario)
            and row["spec_pressure_bucket"] == "high_spec"
        ]
        no_rows = [
            indexed.get((f"{definition}_no_pressure", train_seed, run, scenario, int(row["workflow_id"])))
            for row in full_rows
        ]
        no_rows = [row for row in no_rows if row is not None]
        if not full_rows or len(no_rows) != len(full_rows):
            continue
        full_metrics = metrics(full_rows)
        no_metrics = metrics(no_rows)
        units.append(
            {
                "definition": definition,
                "train_seed": train_seed,
                "run": run,
                "scenario": scenario,
                "load": full_rows[0]["load"],
                "paired_workflows": len(full_rows),
                "full_quality": full_metrics["quality"],
                "no_pressure_quality": no_metrics["quality"],
                "quality_floor": quality_floor,
                "both_quality_feasible": int(
                    full_metrics["quality"] >= quality_floor and no_metrics["quality"] >= quality_floor
                ),
                "full_waste": full_metrics["waste"],
                "no_pressure_waste": no_metrics["waste"],
                "no_pressure_minus_full_waste": no_metrics["waste"] - full_metrics["waste"],
                "no_pressure_minus_full_p99": no_metrics["p99_latency"] - full_metrics["p99_latency"],
                "no_pressure_minus_full_normalized_latency": no_metrics["normalized_latency"] - full_metrics["normalized_latency"],
                "no_pressure_minus_full_quality": no_metrics["quality"] - full_metrics["quality"],
            }
        )
    return units


def summary_rows(units: Sequence[Mapping[str, object]], definitions: Iterable[str], quality_floor: float) -> List[Dict[str, object]]:
    output = []
    for definition in definitions:
        selected = [row for row in units if row["definition"] == definition]
        raw = [(int(row["scenario"]), float(row["no_pressure_minus_full_waste"])) for row in selected]
        feasible = [row for row in selected if int(row["both_quality_feasible"])]
        constrained = [
            (int(row["scenario"]), float(row["no_pressure_minus_full_waste"]))
            for row in feasible
        ]
        raw_low, raw_high = h.stratified_bootstrap_ci(raw, seed=81000 + list(definitions).index(definition)) if raw else (math.nan, math.nan)
        con_low, con_high = h.stratified_bootstrap_ci(constrained, seed=82000 + list(definitions).index(definition)) if constrained else (math.nan, math.nan)
        output.append(
            {
                "definition": definition,
                "label": PRESSURE_LABELS[definition],
                "paired_units": len(selected),
                "quality_floor": quality_floor,
                "quality_feasible_pair_fraction": statistics.mean(int(row["both_quality_feasible"]) for row in selected) if selected else math.nan,
                "raw_waste_delta_no_pressure_minus_full": h.stratified_mean(raw) if raw else math.nan,
                "raw_waste_ci95_low": raw_low,
                "raw_waste_ci95_high": raw_high,
                "raw_waste_p": h.stratified_randomization_p(raw, seed=83000 + list(definitions).index(definition)) if raw else math.nan,
                "quality_constrained_units": len(feasible),
                "quality_constrained_waste_delta": h.stratified_mean(constrained) if constrained else math.nan,
                "quality_constrained_ci95_low": con_low,
                "quality_constrained_ci95_high": con_high,
                "quality_constrained_p": h.stratified_randomization_p(constrained, seed=84000 + list(definitions).index(definition)) if constrained else math.nan,
                "mean_p99_delta": h.stratified_mean([(int(row["scenario"]), float(row["no_pressure_minus_full_p99"])) for row in selected]) if selected else math.nan,
                "mean_normalized_latency_delta": h.stratified_mean([(int(row["scenario"]), float(row["no_pressure_minus_full_normalized_latency"])) for row in selected]) if selected else math.nan,
                "mean_quality_delta": h.stratified_mean([(int(row["scenario"]), float(row["no_pressure_minus_full_quality"])) for row in selected]) if selected else math.nan,
            }
        )
    return output


def write_report(out: Path, summaries: Sequence[Mapping[str, object]], manifest: Mapping[str, object]) -> None:
    lines = [
        "# Spec pressure definition study",
        "",
        "本实验是独立的候选定义筛选，不覆盖 proof_full_v2 的预注册 H1-P 结果。每个定义分别训练 full 与 no-pressure bandit；主比较只在两者平均质量都达到 quality floor 时解释 waste。筛选结果仍需在冻结定义后用正式 full 预算复核。",
        "",
        f"- 模式：{manifest['mode']}",
        f"- 质量门槛：{manifest['quality_floor']}",
        f"- 训练 seed：{manifest['train_seeds']}",
        f"- 评估 runs：{manifest['eval_runs']}",
        "",
        "## 结果",
        "",
        "正的 waste delta 表示：去掉 pressure 后 waste 增加，方向上支持该 pressure 有助于控制浪费。raw 结果不考虑质量；quality-constrained 结果只保留 full 与 no-pressure 都达到质量门槛的配对单元。",
        "",
        "| 定义 | 配对单元 | 质量可行比例 | raw waste delta | 质量约束 waste delta | p99 delta | quality delta |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['label']} | {row['paired_units']} | {float(row['quality_feasible_pair_fraction']):.3f} | "
            f"{float(row['raw_waste_delta_no_pressure_minus_full']):.3f} "
            f"[{float(row['raw_waste_ci95_low']):.3f}, {float(row['raw_waste_ci95_high']):.3f}] | "
            f"{float(row['quality_constrained_waste_delta']):.3f} "
            f"[{float(row['quality_constrained_ci95_low']):.3f}, {float(row['quality_constrained_ci95_high']):.3f}] | "
            f"{float(row['mean_p99_delta']):.3f} | {float(row['mean_quality_delta']):.4f} |"
        )
    lines += [
        "",
        "## 解释边界",
        "",
        "- 只有在质量约束下 waste delta 为正，且区间不跨 0，才可说该定义为 H1-P 提供较强支持。",
        "- raw waste 下降可能只是 no-pressure 策略少生成了 speculative work，不能单独当作效率提升。",
        "- 该实验比较的是模拟器和当前 reward 下的状态定义，不证明真实网络中的语义质量或 universal superiority。",
        "- expected_waste_risk 是透明的启发式代理，不能当作真实未来浪费概率。",
        "",
        "详细配对结果见 `pressure_definition_pairs.csv`，原始状态覆盖见 `pressure_bucket_coverage.csv`。",
    ]
    (out / "PRESSURE_DEFINITION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--quality-floor", type=float, default=0.95)
    parser.add_argument("--train-seeds", type=int, default=None)
    parser.add_argument("--eval-runs", type=int, default=None)
    parser.add_argument("--train-episodes", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "smoke":
        duration, max_workflows, max_time = 700, 28, 2600
        train_episodes, eval_runs, default_train_seeds = 36, 2, 2
    else:
        duration, max_workflows, max_time = 1800, 90, 6000
        train_episodes, eval_runs, default_train_seeds = 72, 3, 2
    if args.eval_runs is not None:
        eval_runs = args.eval_runs
    if args.train_episodes is not None:
        train_episodes = args.train_episodes
    train_seed_count = args.train_seeds or default_train_seeds
    out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / f"pressure_definition_{args.mode}_20260722"
    out.mkdir(parents=True, exist_ok=True)
    matrix = h.scenarios(args.mode)
    eval_matrix = matrix if args.mode == "smoke" else matrix[::3]
    manifest = {
        "study_version": "2026-07-22.pressure-definitions.v1",
        "mode": args.mode,
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "definitions": list(PRESSURE_DEFINITIONS),
        "quality_floor": args.quality_floor,
        "train_seeds": list(range(7, 7 + train_seed_count)),
        "train_episodes": train_episodes,
        "eval_runs": eval_runs,
        "eval_scenarios": len(eval_matrix),
        "duration": duration,
        "max_workflows": max_workflows,
        "max_time": max_time,
        "guard": "disabled_for_all_policies",
    }
    h.write_json(out / "run_manifest.json", manifest)

    all_rows: List[Dict[str, object]] = []
    all_units: List[Dict[str, object]] = []
    for definition in PRESSURE_DEFINITIONS:
        print(f"[pressure] {definition}: training", flush=True)
        full_class = make_bandit_class(definition, False)
        no_class = make_bandit_class(definition, True)
        for train_seed in manifest["train_seeds"]:
            full = train_policy(full_class, definition, train_seed, train_episodes, duration, max_workflows, max_time, matrix)
            no_pressure = train_policy(no_class, definition, train_seed, train_episodes, duration, max_workflows, max_time, matrix)
            for run in range(eval_runs):
                for scenario_index, scenario in enumerate(eval_matrix):
                    workload_seed = 900000 + int(train_seed) * 10000 + run * 1000 + scenario_index
                    full_summary = run_once(full, definition, scenario, workload_seed, duration, max_workflows, max_time)
                    no_summary = run_once(no_pressure, definition, scenario, workload_seed, duration, max_workflows, max_time)
                    all_rows.extend(record_rows(full_summary, f"{definition}_full", definition, train_seed, run, scenario_index))
                    all_rows.extend(record_rows(no_summary, f"{definition}_no_pressure", definition, train_seed, run, scenario_index))
        units = paired_rows(all_rows, definition, args.quality_floor)
        all_units.extend(units)

    h.write_csv(out / "pressure_definition_pairs.csv", all_units)
    h.write_csv(out / "pressure_definition_summary.csv", summary_rows(all_units, PRESSURE_DEFINITIONS, args.quality_floor))
    coverage = []
    for definition in PRESSURE_DEFINITIONS:
        selected = [row for row in all_rows if row["definition"] == definition]
        for policy in (f"{definition}_full", f"{definition}_no_pressure"):
            subset = [row for row in selected if row["policy"] == policy]
            for bucket in ("low_spec", "mid_spec", "high_spec"):
                values = [row for row in subset if row["spec_pressure_bucket"] == bucket]
                raw_values = [float(row["pressure_value"]) for row in values]
                coverage.append(
                    {
                        "definition": definition,
                        "policy": policy,
                        "bucket": bucket,
                        "records": len(values),
                        "mean_pressure_value": statistics.mean(raw_values) if raw_values else math.nan,
                        "min_pressure_value": min(raw_values) if raw_values else math.nan,
                        "max_pressure_value": max(raw_values) if raw_values else math.nan,
                    }
                )
    h.write_csv(out / "pressure_bucket_coverage.csv", coverage)
    h.write_csv(out / "pressure_definition_workflow_audit.csv", all_rows)
    write_report(out, summary_rows(all_units, PRESSURE_DEFINITIONS, args.quality_floor), manifest)
    print(f"[done] results written to {out}", flush=True)


if __name__ == "__main__":
    main()
