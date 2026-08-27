#!/usr/bin/env python3
"""Fresh-ledger background-constrained optimization for the factorized rule."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .factorized_signal_study import (
        FactorizedSignalRule,
        primary_means,
        verdict_rows,
    )
    from .pressure_definition_study import PressureSimulator
    from .three_signal_confirmation_study import (
        PRIMARY_SPECS,
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
        paired_slice_units,
    )
    from .three_signal_rule_study import analysis_rows, paired_nonjoint_units
except ImportError:  # pragma: no cover
    import proof_harness as h
    from factorized_signal_study import FactorizedSignalRule, primary_means, verdict_rows
    from pressure_definition_study import PressureSimulator
    from three_signal_confirmation_study import (
        PRIMARY_SPECS,
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
        paired_slice_units,
    )
    from three_signal_rule_study import analysis_rows, paired_nonjoint_units


PROTOCOL_VERSION = "2026-07-30.factorized-background-hard-constraint-v1"
VALIDATION_SEED_BASE = 2_210_000
CONFIRMATION_SEED_BASE = 2_220_000
BACKGROUND_FLOOR = 0.20
P99_REGRESSION_LIMIT = 1.10
MISS_REGRESSION_LIMIT = 0.005
BACKGROUND_BOOST_CANDIDATES = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
PARAMETER_KEYS = (
    "congestion_critical_boost",
    "congestion_optional_scale",
    "slack_critical_boost",
    "background_weight_boost",
)
OPTIONAL_PARAMETER_KEYS = ("background_target_ratio",)


def load_base_params(path: Path) -> Dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: float(payload["selected_params"][key])
        for key in PARAMETER_KEYS[:-1]
    }


def run_policy(
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Tuple[Dict[str, object], Dict[str, float]]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = h.scaled_workload(
        workload_seed,
        load,
        duration,
        max_workflows,
        deadline_scale,
        optional_scale,
    )
    background_totals = {
        spec.workflow_id: max(1.0, sum(spec.background_sizes)) for spec in specs
    }
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
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
        }
    )
    records = list(summary["workflow_records"])
    metrics = h.state_metrics(records)
    ratios = [
        float(row["background_bytes_served"])
        / background_totals[int(row["workflow_id"])]
        for row in records
    ]
    global_metrics = {
        **metrics,
        "background_service_ratio": statistics.mean(ratios),
        "link_utilization": float(summary["link_utilization"]),
    }
    return summary, global_metrics


def evaluate_candidate(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], List[Dict[str, object]]]:
    units: List[Dict[str, object]] = []
    nonjoint_units: List[Dict[str, object]] = []
    global_cells: List[Dict[str, object]] = []
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            workload_seed = seed_base + run * 10_000 + scenario_index
            summaries: Dict[str, Dict[str, object]] = {}
            for name in ("full", "no_congestion", "no_slack", "no_pressure"):
                summary, metrics = run_policy(
                    FactorizedSignalRule(params, name),
                    scenario,
                    workload_seed,
                    duration,
                    max_workflows,
                    max_time,
                )
                summaries[name] = summary
                if name == "full":
                    global_cells.append(
                        {
                            "run": run,
                            "scenario": scenario_index,
                            "seed": workload_seed,
                            "load": scenario[0],
                            "deadline_scale": scenario[1],
                            "optional_scale": scenario[2],
                            "capacity_scale": scenario[3],
                            **metrics,
                        }
                    )
            units.extend(
                paired_slice_units(
                    summaries,
                    replicate=0,
                    eval_run=run,
                    scenario_index=scenario_index,
                    workload_seed=workload_seed,
                )
            )
            nonjoint_units.extend(
                paired_nonjoint_units(
                    summaries,
                    run,
                    scenario_index,
                    workload_seed,
                )
            )
        print(f"[evaluate] boost={params['background_weight_boost']:.3g} run {run + 1}/{runs}", flush=True)
    return units, nonjoint_units, global_cells


def mean_metrics(cells: Sequence[Mapping[str, object]]) -> Dict[str, float]:
    metrics = (
        "p99_latency",
        "deadline_miss_ratio",
        "waste",
        "quality",
        "normalized_latency",
        "background_service_ratio",
        "link_utilization",
    )
    return {
        metric: statistics.mean(float(row[metric]) for row in cells)
        for metric in metrics
    }


def candidate_row(
    candidate_id: int,
    params: Mapping[str, float],
    units: Sequence[Mapping[str, object]],
    nonjoint_units: Sequence[Mapping[str, object]],
    cells: Sequence[Mapping[str, object]],
    baseline: Mapping[str, float],
) -> Dict[str, object]:
    broad = primary_means(units)
    nonjoint = primary_means(nonjoint_units)
    metrics = mean_metrics(cells)
    background_pass = metrics["background_service_ratio"] >= BACKGROUND_FLOOR
    quality_pass = metrics["quality"] >= QUALITY_FLOOR
    p99_pass = metrics["p99_latency"] <= P99_REGRESSION_LIMIT * baseline["p99_latency"]
    miss_pass = metrics["deadline_miss_ratio"] <= baseline["deadline_miss_ratio"] + MISS_REGRESSION_LIMIT
    broad_pass = all(broad[hypothesis] > 0 for hypothesis in PRIMARY_SPECS)
    nonjoint_pass = all(nonjoint[hypothesis] > 0 for hypothesis in PRIMARY_SPECS)
    return {
        "candidate_id": candidate_id,
        **params,
        **{f"mean_{key}": value for key, value in metrics.items()},
        "background_floor_fraction": statistics.mean(
            float(row["background_service_ratio"]) >= BACKGROUND_FLOOR
            for row in cells
        ),
        "p99_ratio_vs_unboosted": metrics["p99_latency"] / baseline["p99_latency"],
        "miss_delta_vs_unboosted": metrics["deadline_miss_ratio"] - baseline["deadline_miss_ratio"],
        **{f"{hypothesis}_delta": broad[hypothesis] for hypothesis in PRIMARY_SPECS},
        **{
            f"{hypothesis}_nonjoint_delta": nonjoint[hypothesis]
            for hypothesis in PRIMARY_SPECS
        },
        "background_gate_pass": int(background_pass),
        "quality_gate_pass": int(quality_pass),
        "p99_gate_pass": int(p99_pass),
        "miss_gate_pass": int(miss_pass),
        "broad_direction_gate_pass": int(broad_pass),
        "nonjoint_direction_gate_pass": int(nonjoint_pass),
        "all_validation_gates_pass": int(
            background_pass
            and quality_pass
            and p99_pass
            and miss_pass
            and broad_pass
            and nonjoint_pass
        ),
    }


def select_background_candidate(
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    feasible = [row for row in rows if int(row["all_validation_gates_pass"])]
    if not feasible:
        raise ValueError("no background boost passed every frozen validation gate")
    return min(
        feasible,
        key=lambda row: (
            float(row["background_weight_boost"]),
            float(row["mean_p99_latency"]),
            int(row["candidate_id"]),
        ),
    )


def paired_reference_cells(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> List[Dict[str, object]]:
    baseline_params = dict(params)
    baseline_params["background_weight_boost"] = 1.0
    cells = []
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            workload_seed = seed_base + run * 10_000 + scenario_index
            _, metrics = run_policy(
                FactorizedSignalRule(baseline_params, "full"),
                scenario,
                workload_seed,
                duration,
                max_workflows,
                max_time,
            )
            cells.append(
                {
                    "run": run,
                    "scenario": scenario_index,
                    "seed": workload_seed,
                    **metrics,
                }
            )
        print(f"[reference] run {run + 1}/{runs}", flush=True)
    return cells


def confirmation_gate_row(
    cells: Sequence[Mapping[str, object]],
    reference_cells: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    metrics = mean_metrics(cells)
    reference = mean_metrics(reference_cells)
    signals_pass = all(row["status"] == "supported" for row in verdicts)
    gates = {
        "background_gate_pass": metrics["background_service_ratio"] >= BACKGROUND_FLOOR,
        "quality_gate_pass": metrics["quality"] >= QUALITY_FLOOR,
        "p99_gate_pass": metrics["p99_latency"] <= P99_REGRESSION_LIMIT * reference["p99_latency"],
        "miss_gate_pass": metrics["deadline_miss_ratio"] <= reference["deadline_miss_ratio"] + MISS_REGRESSION_LIMIT,
        "three_signal_gate_pass": signals_pass,
    }
    return {
        **{f"mean_{key}": value for key, value in metrics.items()},
        **{f"reference_{key}": value for key, value in reference.items()},
        "background_floor_fraction": statistics.mean(
            float(row["background_service_ratio"]) >= BACKGROUND_FLOOR
            for row in cells
        ),
        "p99_ratio_vs_unboosted": metrics["p99_latency"] / reference["p99_latency"],
        "miss_delta_vs_unboosted": metrics["deadline_miss_ratio"] - reference["deadline_miss_ratio"],
        **{key: int(value) for key, value in gates.items()},
        "all_confirmation_gates_pass": int(all(gates.values())),
    }


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    search_rows: Sequence[Mapping[str, object]],
    confirmation: Mapping[str, object] | None = None,
    analysis: Sequence[Mapping[str, object]] = (),
    verdicts: Sequence[Mapping[str, object]] = (),
) -> None:
    lines = [
        "# 因子化控制器 Background 硬约束优化",
        "",
        "本实验保持 pressure→admission、congestion→global scheduling、slack→deadline scheduling 三条路径不变，只对所有 full/ablation 一致增加 background 权重。0.20 floor、0.95 quality、p99 +10% 和 miss +0.005 门槛均不因结果降低。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 模式：`{manifest['mode']}`",
        f"- Seed：`{manifest['seed_rule']}`",
        f"- 场景：{manifest['scenarios']} × runs：{manifest['runs']}",
        "",
    ]
    if search_rows:
        lines += [
            "## Validation 候选",
            "",
            "| Boost | p99 | Miss | Quality | Background | BG-floor cells | Broad +/+ /+ | Nonjoint +/+ /+ | 全门通过 |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in search_rows:
            broad_count = sum(float(row[f"{name}_delta"]) > 0 for name in PRIMARY_SPECS)
            nonjoint_count = sum(float(row[f"{name}_nonjoint_delta"]) > 0 for name in PRIMARY_SPECS)
            lines.append(
                f"| {float(row['background_weight_boost']):.3g} | {float(row['mean_p99_latency']):.3f} | "
                f"{float(row['mean_deadline_miss_ratio']):.4f} | {float(row['mean_quality']):.4f} | "
                f"{float(row['mean_background_service_ratio']):.4f} | {float(row['background_floor_fraction']):.3f} | "
                f"{broad_count}/3 | {nonjoint_count}/3 | {int(row['all_validation_gates_pass'])} |"
            )
        lines += [
            "",
            f"冻结选择：`{manifest.get('selected_params')}`。选择规则是先通过全部硬门，再取最小 background boost，避免用 test 结果继续调权重。",
            "",
        ]
    if confirmation is not None:
        lines += [
            "## 独立确认",
            "",
            f"- Background mean：{float(confirmation['mean_background_service_ratio']):.4f}；达到 0.20 的单元比例：{float(confirmation['background_floor_fraction']):.3f}。",
            f"- Quality：{float(confirmation['mean_quality']):.4f}。",
            f"- p99 相对未加权版本：{float(confirmation['p99_ratio_vs_unboosted']):.4f}×；miss delta：{float(confirmation['miss_delta_vs_unboosted']):+.5f}。",
            f"- 全部确认门：{'通过' if int(confirmation['all_confirmation_gates_pass']) else '未通过'}。",
            "",
            "| 假设 | Broad delta | 95% CI | Holm p | 判定 |",
            "|---|---:|---:|---:|---|",
        ]
        verdict_by_claim = {str(row["claim"]): str(row["status"]) for row in verdicts}
        for hypothesis in PRIMARY_SPECS:
            row = next(
                item
                for item in analysis
                if item["hypothesis"] == hypothesis and int(item["primary_metric"])
            )
            lines.append(
                f"| {hypothesis} | {float(row['mean_delta_ablation_minus_full']):+.5f} | "
                f"[{float(row['ci95_low']):+.5f}, {float(row['ci95_high']):+.5f}] | "
                f"{float(row['holm_adjusted_p']):.4g} | {verdict_by_claim[hypothesis]} |"
            )
        lines.append("")
    lines += [
        "## 解释边界",
        "",
        "- 若 validation 或 confirmation 任一硬门失败，结论保持不可部署，不降低 floor。",
        "- 平均 background 达标不等于逐场景公平；BG-floor cells 仍需单独报告。",
        "- simulator 在主流程完成时取消 background，因此本实验优化的是有限 eligible window 内的服务，不是长期带宽保证。",
    ]
    (out / "FACTORIZED_BACKGROUND_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("select", "confirm"), required=True)
    parser.add_argument("--frozen-factorized-candidate")
    parser.add_argument("--frozen-background-candidate")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--runs", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    duration, max_workflows, max_time = 1800, 90, 6000
    if args.mode == "select":
        if not args.frozen_factorized_candidate:
            raise ValueError("select mode requires --frozen-factorized-candidate")
        runs = args.runs or 1
        seed_base = VALIDATION_SEED_BASE
        matrix = balanced_evaluation_matrix(h.scenarios("full"), 27, seed=23267)
        base_params = load_base_params(Path(args.frozen_factorized_candidate))
        evaluations = []
        for boost in BACKGROUND_BOOST_CANDIDATES:
            params = {**base_params, "background_weight_boost": boost}
            print(f"[select] background boost {boost:.3g}", flush=True)
            evaluations.append(
                (params, *evaluate_candidate(
                    params,
                    matrix,
                    runs,
                    seed_base,
                    duration,
                    max_workflows,
                    max_time,
                ))
            )
        baseline = mean_metrics(evaluations[0][3])
        search_rows = [
            candidate_row(index, params, units, nonjoint, cells, baseline)
            for index, (params, units, nonjoint, cells) in enumerate(evaluations)
        ]
        h.write_csv(out / "validation_candidate_search.csv", search_rows)
        try:
            selected = dict(select_background_candidate(search_rows))
        except ValueError:
            selected = None
        selected_params = (
            {key: float(selected[key]) for key in PARAMETER_KEYS}
            if selected is not None
            else None
        )
        if selected is not None:
            h.write_json(
                out / "selected_candidate.json",
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "selected_params": selected_params,
                    "selection_record": selected,
                    "selection_seed_rule": f"{seed_base} + run*10000 + scenario_index",
                    "selection_matrix": matrix,
                },
            )
        manifest = {
            "protocol_version": PROTOCOL_VERSION,
            "mode": args.mode,
            "seed_rule": f"{seed_base} + run*10000 + scenario_index",
            "scenarios": len(matrix),
            "runs": runs,
            "selected_params": selected_params,
            "selection_status": "selected" if selected is not None else "no_feasible_candidate",
            "candidate_boosts": BACKGROUND_BOOST_CANDIDATES,
            "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
            "script_sha256": h.sha256(Path(__file__).resolve()),
        }
        h.write_json(out / "run_manifest.json", manifest)
        write_report(out, manifest, search_rows)
        if selected is None:
            print("[result] no candidate passed every frozen validation gate", flush=True)
    else:
        if not args.frozen_background_candidate:
            raise ValueError("confirm mode requires --frozen-background-candidate")
        runs = args.runs or 3
        seed_base = CONFIRMATION_SEED_BASE
        matrix = balanced_evaluation_matrix(h.scenarios("full"), 27, seed=24267)
        frozen = json.loads(
            Path(args.frozen_background_candidate).read_text(encoding="utf-8")
        )
        params = {
            key: float(frozen["selected_params"][key])
            for key in PARAMETER_KEYS + OPTIONAL_PARAMETER_KEYS
            if key in frozen["selected_params"]
        }
        units, nonjoint_units, cells = evaluate_candidate(
            params,
            matrix,
            runs,
            seed_base,
            duration,
            max_workflows,
            max_time,
        )
        reference_cells = paired_reference_cells(
            params,
            matrix,
            runs,
            seed_base,
            duration,
            max_workflows,
            max_time,
        )
        analysis = analysis_rows(units)
        nonjoint_analysis = analysis_rows(nonjoint_units)
        verdicts = verdict_rows(analysis, nonjoint_analysis, "confirm")
        confirmation = confirmation_gate_row(cells, reference_cells, verdicts)
        manifest = {
            "protocol_version": PROTOCOL_VERSION,
            "mode": args.mode,
            "seed_rule": f"{seed_base} + run*10000 + scenario_index",
            "scenarios": len(matrix),
            "runs": runs,
            "selected_params": params,
            "evaluation_matrix": matrix,
            "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
            "script_sha256": h.sha256(Path(__file__).resolve()),
        }
        h.write_csv(out / "confirmation_units.csv", units)
        h.write_csv(out / "nonjoint_confirmation_units.csv", nonjoint_units)
        h.write_csv(out / "confirmation_analysis.csv", analysis)
        h.write_csv(out / "nonjoint_confirmation_analysis.csv", nonjoint_analysis)
        h.write_csv(out / "claim_verdicts.csv", verdicts)
        h.write_csv(out / "global_cells.csv", cells)
        h.write_csv(out / "unboosted_reference_cells.csv", reference_cells)
        h.write_csv(out / "confirmation_gates.csv", [confirmation])
        h.write_json(out / "run_manifest.json", manifest)
        write_report(
            out,
            manifest,
            (),
            confirmation=confirmation,
            analysis=analysis,
            verdicts=verdicts,
        )
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
