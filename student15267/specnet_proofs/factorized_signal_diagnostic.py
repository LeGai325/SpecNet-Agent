#!/usr/bin/env python3
"""Fresh-seed global trade-off audit for the frozen factorized controller."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .factorized_signal_study import FactorizedSignalRule
    from .pressure_definition_study import PressureSimulator
    from .three_signal_confirmation_study import balanced_evaluation_matrix
except ImportError:  # pragma: no cover
    import proof_harness as h
    from factorized_signal_study import FactorizedSignalRule
    from pressure_definition_study import PressureSimulator
    from three_signal_confirmation_study import balanced_evaluation_matrix


PROTOCOL_VERSION = "2026-07-30.factorized-global-diagnostic-v1"
TEST_SEED_BASE = 2_120_000


def run_policy(
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Dict[str, float]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = h.scaled_workload(
        workload_seed,
        load,
        duration,
        max_workflows,
        deadline_scale,
        optional_scale,
    )
    simulator = PressureSimulator(
        specs,
        policy,
        load,
        workload_seed,
        duration,
        max_time,
        capacity_scale=capacity_scale,
        pressure_definition="active_speculative_backlog",
    )
    summary = simulator.run()
    metrics = h.state_metrics(summary["workflow_records"])
    background_ratios = [
        workflow.background_bytes_served
        / max(1.0, sum(workflow.spec.background_sizes))
        for workflow in simulator.completed_workflows
    ]
    return {
        "p99_latency": metrics["p99_latency"],
        "deadline_miss_ratio": metrics["deadline_miss_ratio"],
        "waste": metrics["waste"],
        "quality": metrics["quality"],
        "normalized_latency": metrics["normalized_latency"],
        "background_service_ratio": statistics.mean(background_ratios),
        "link_utilization": float(summary["link_utilization"]),
    }


def summary_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output = []
    metrics = (
        "p99_latency",
        "deadline_miss_ratio",
        "waste",
        "quality",
        "normalized_latency",
        "background_service_ratio",
        "link_utilization",
    )
    for policy in sorted({str(row["policy"]) for row in rows}):
        selected = [row for row in rows if row["policy"] == policy]
        output.append(
            {
                "policy": policy,
                "cells": len(selected),
                **{
                    f"mean_{metric}": statistics.mean(
                        float(row[metric]) for row in selected
                    )
                    for metric in metrics
                },
                "quality_feasible_fraction": statistics.mean(
                    float(row["quality"]) >= 0.95 for row in selected
                ),
                "background_floor_fraction": statistics.mean(
                    float(row["background_service_ratio"]) >= 0.20
                    for row in selected
                ),
            }
        )
    return output


def comparison_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    indexed = {
        (str(row["policy"]), int(row["run"]), int(row["scenario"])): row
        for row in rows
    }
    metrics = (
        "p99_latency",
        "deadline_miss_ratio",
        "waste",
        "quality",
        "normalized_latency",
        "background_service_ratio",
    )
    policies = sorted({str(row["policy"]) for row in rows if row["policy"] != "factorized_full"})
    output = []
    for policy_index, policy in enumerate(policies):
        keys = sorted(
            (run, scenario)
            for name, run, scenario in indexed
            if name == policy and ("factorized_full", run, scenario) in indexed
        )
        for metric_index, metric in enumerate(metrics):
            values = [
                (
                    scenario,
                    float(indexed[(policy, run, scenario)][metric])
                    - float(indexed[("factorized_full", run, scenario)][metric]),
                )
                for run, scenario in keys
            ]
            low, high = h.stratified_bootstrap_ci(
                values,
                seed=222_000 + policy_index * 100 + metric_index,
            )
            output.append(
                {
                    "comparison": f"{policy}_minus_factorized_full",
                    "policy": policy,
                    "metric": metric,
                    "paired_units": len(values),
                    "scenario_strata": len({scenario for scenario, _ in values}),
                    "mean_delta": h.stratified_mean(values),
                    "ci95_low": low,
                    "ci95_high": high,
                }
            )
    return output


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    summaries: Sequence[Mapping[str, object]],
    comparisons: Sequence[Mapping[str, object]],
) -> None:
    lines = [
        "# 因子化三信号全局代价审计",
        "",
        "该审计不再选择参数，只用第四组全新 seeds 检查冻结 controller 的总体 latency、miss、waste、quality、background 和 utilization。它是机制证明后的风险检查，不是新的调参集。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 场景：{manifest['scenarios']} × runs：{manifest['runs']}",
        f"- Seed：`{manifest['test_seed_rule']}`",
        f"- 冻结参数：`{manifest['selected_params']}`",
        "",
        "## 总体结果",
        "",
        "| Policy | p99 | Miss | Waste | Quality | Norm latency | Background | Utilization | Q-feasible | BG-floor |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['policy']} | {float(row['mean_p99_latency']):.3f} | "
            f"{float(row['mean_deadline_miss_ratio']):.4f} | {float(row['mean_waste']):.3f} | "
            f"{float(row['mean_quality']):.4f} | {float(row['mean_normalized_latency']):.4f} | "
            f"{float(row['mean_background_service_ratio']):.4f} | {float(row['mean_link_utilization']):.4f} | "
            f"{float(row['quality_feasible_fraction']):.3f} | {float(row['background_floor_fraction']):.3f} |"
        )
    lines += [
        "",
        "## 配对差异",
        "",
        "正值表示对照高于 factorized full；对 latency/miss/waste 是 factorized 更好，对 quality/background 是 factorized 更差。",
        "",
    ]
    for policy in sorted({str(row["policy"]) for row in comparisons}):
        lines.append(f"### {policy} minus factorized full")
        lines.append("")
        for row in comparisons:
            if row["policy"] != policy:
                continue
            lines.append(
                f"- {row['metric']}: {float(row['mean_delta']):+.4f}, "
                f"95% CI [{float(row['ci95_low']):+.4f}, {float(row['ci95_high']):+.4f}]"
            )
        lines.append("")
    factorized = next(row for row in summaries if row["policy"] == "factorized_full")
    lines += [
        "## 审计结论",
        "",
        f"- factorized full 平均质量为 {float(factorized['mean_quality']):.4f}，质量可行单元比例 {float(factorized['quality_feasible_fraction']):.3f}。",
        f"- 平均 background service 为 {float(factorized['mean_background_service_ratio']):.4f}，达到 0.20 floor 的单元比例 {float(factorized['background_floor_fraction']):.3f}。",
        "- Broad 三参数证明是否成立与部署是否安全是两个问题；background/miss 任一失败都必须保留。",
    ]
    (out / "FACTORIZED_GLOBAL_DIAGNOSTIC.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-candidate", required=True)
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frozen = json.loads(Path(args.frozen_candidate).read_text(encoding="utf-8"))
    params = {
        key: float(frozen["selected_params"][key])
        for key in (
            "congestion_critical_boost",
            "congestion_optional_scale",
            "slack_critical_boost",
        )
    }
    out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / "factorized_global_diagnostic_20260730"
    out.mkdir(parents=True, exist_ok=True)
    matrix = balanced_evaluation_matrix(h.scenarios("full"), 27, seed=22267)
    runs = 3
    policy_factories = {
        "factorized_full": lambda: FactorizedSignalRule(params, "full"),
        "no_congestion": lambda: FactorizedSignalRule(params, "no_congestion"),
        "no_slack": lambda: FactorizedSignalRule(params, "no_slack"),
        "no_pressure": lambda: FactorizedSignalRule(params, "no_pressure"),
        "fixed_full": lambda: h.FixedActionPolicy("full"),
        "fixed_recovery": lambda: h.FixedActionPolicy("recovery"),
    }
    rows: List[Dict[str, object]] = []
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            workload_seed = TEST_SEED_BASE + run * 10_000 + scenario_index
            for policy_name, factory in policy_factories.items():
                rows.append(
                    {
                        "policy": policy_name,
                        "run": run,
                        "scenario": scenario_index,
                        "seed": workload_seed,
                        "load": scenario[0],
                        "deadline_scale": scenario[1],
                        "optional_scale": scenario[2],
                        "capacity_scale": scenario[3],
                        **run_policy(
                            factory(),
                            scenario,
                            workload_seed,
                            1800,
                            90,
                            6000,
                        ),
                    }
                )
        print(f"[diagnostic] run {run + 1}/{runs}", flush=True)
    summaries = summary_rows(rows)
    comparisons = comparison_rows(rows)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "harness_sha256": h.sha256(Path(__file__).resolve()),
        "selected_params": params,
        "scenarios": len(matrix),
        "runs": runs,
        "evaluation_matrix": matrix,
        "test_seed_rule": f"{TEST_SEED_BASE} + run*10000 + scenario_index",
    }
    h.write_csv(out / "diagnostic_cells.csv", rows)
    h.write_csv(out / "diagnostic_summary.csv", summaries)
    h.write_csv(out / "diagnostic_comparisons.csv", comparisons)
    h.write_json(out / "run_manifest.json", manifest)
    write_report(out, manifest, summaries, comparisons)
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
