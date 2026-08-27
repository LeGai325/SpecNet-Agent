#!/usr/bin/env python3
"""Select a bounded deficit-aware background reservation on fresh seeds."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .factorized_background_study import (
        BACKGROUND_FLOOR,
        MISS_REGRESSION_LIMIT,
        P99_REGRESSION_LIMIT,
        candidate_row,
        evaluate_candidate,
        load_base_params,
        mean_metrics,
        run_policy,
    )
    from .factorized_signal_study import FactorizedSignalRule
    from .three_signal_confirmation_study import QUALITY_FLOOR, balanced_evaluation_matrix
except ImportError:  # pragma: no cover
    import proof_harness as h
    from factorized_background_study import (
        BACKGROUND_FLOOR,
        MISS_REGRESSION_LIMIT,
        P99_REGRESSION_LIMIT,
        candidate_row,
        evaluate_candidate,
        load_base_params,
        mean_metrics,
        run_policy,
    )
    from factorized_signal_study import FactorizedSignalRule
    from three_signal_confirmation_study import QUALITY_FLOOR, balanced_evaluation_matrix


PROTOCOL_VERSION = "2026-07-30.factorized-background-deficit-v1"
VALIDATION_SEED_BASE = 2_230_000
REFINEMENT_SEED_BASE = 2_240_000
DEFICIT_CANDIDATES = tuple(
    {"background_target_ratio": target, "background_weight_boost": boost}
    for target in (0.25, 0.30, 0.40)
    for boost in (3.0, 6.0, 12.0)
)
REFINEMENT_CANDIDATES = tuple(
    {"background_target_ratio": target, "background_weight_boost": boost}
    for target in (0.25, 0.30, 0.40)
    for boost in (2.0, 2.25, 2.50, 2.75)
)


def global_cells(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
) -> List[Dict[str, object]]:
    cells = []
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = seed_base + run * 10_000 + scenario_index
            _, metrics = run_policy(
                FactorizedSignalRule(params, "full"),
                scenario,
                seed,
                1800,
                90,
                6000,
            )
            cells.append(
                {
                    "run": run,
                    "scenario": scenario_index,
                    "seed": seed,
                    **metrics,
                }
            )
        print(
            f"[global] target={params.get('background_target_ratio', 0):.2f} "
            f"boost={params['background_weight_boost']:.3g} run {run + 1}/{runs}",
            flush=True,
        )
    return cells


def global_candidate_row(
    candidate_id: int,
    params: Mapping[str, float],
    cells: Sequence[Mapping[str, object]],
    baseline: Mapping[str, float],
) -> Dict[str, object]:
    metrics = mean_metrics(cells)
    gates = {
        "background_gate_pass": metrics["background_service_ratio"] >= BACKGROUND_FLOOR,
        "quality_gate_pass": metrics["quality"] >= QUALITY_FLOOR,
        "p99_gate_pass": metrics["p99_latency"] <= P99_REGRESSION_LIMIT * baseline["p99_latency"],
        "miss_gate_pass": metrics["deadline_miss_ratio"] <= baseline["deadline_miss_ratio"] + MISS_REGRESSION_LIMIT,
    }
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
        **{key: int(value) for key, value in gates.items()},
        "all_global_gates_pass": int(all(gates.values())),
    }


def select_deficit_candidate(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    feasible = [row for row in rows if int(row["all_validation_gates_pass"])]
    if not feasible:
        raise ValueError("no deficit-aware candidate passed every validation gate")
    return min(
        feasible,
        key=lambda row: (
            float(row["background_target_ratio"]),
            float(row["background_weight_boost"]),
            float(row["mean_p99_latency"]),
            int(row["candidate_id"]),
        ),
    )


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    global_rows: Sequence[Mapping[str, object]],
    signal_rows: Sequence[Mapping[str, object]],
) -> None:
    lines = [
        "# Deficit-aware Background 预留优化",
        "",
        "常数 boost 在 background 达到 0.20 前已经突破 p99/miss 门。本轮只在每条 background 流尚未获得预留份额时加速；达到按原始大小计算的 target 后立即恢复 0.5 基础权重。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- Seed：`{manifest['seed_rule']}`",
        f"- 场景：{manifest['scenarios']} × runs：{manifest['runs']}",
        f"- 选择状态：`{manifest['selection_status']}`",
        "",
        "## 全局硬门预筛",
        "",
        "| Target | Boost | p99× | Miss Δ | Background | BG-floor cells | Global gates |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in global_rows:
        lines.append(
            f"| {float(row['background_target_ratio']):.2f} | {float(row['background_weight_boost']):.3g} | "
            f"{float(row['p99_ratio_vs_unboosted']):.4f} | {float(row['miss_delta_vs_unboosted']):+.5f} | "
            f"{float(row['mean_background_service_ratio']):.4f} | {float(row['background_floor_fraction']):.3f} | "
            f"{int(row['all_global_gates_pass'])} |"
        )
    lines += ["", "## 三信号复核", ""]
    if not signal_rows:
        lines.append("无候选通过全局硬门，因此未消耗额外 ablation 预算。")
    else:
        lines += [
            "| Target | Boost | C | S | P | Nonjoint C/S/P | 全门 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in signal_rows:
            nonjoint_count = sum(
                float(row[f"{name}_nonjoint_delta"]) > 0
                for name in ("H1-C", "H1-S", "H1-P-backlog")
            )
            lines.append(
                f"| {float(row['background_target_ratio']):.2f} | {float(row['background_weight_boost']):.3g} | "
                f"{float(row['H1-C_delta']):+.3f} | {float(row['H1-S_delta']):+.5f} | "
                f"{float(row['H1-P-backlog_delta']):+.3f} | {nonjoint_count}/3 | "
                f"{int(row['all_validation_gates_pass'])} |"
            )
    lines += [
        "",
        "## 结论边界",
        "",
        "- validation 只用于冻结候选；若选中，必须使用 2220000 系列全新 seeds 独立确认。",
        "- 平均 background 与逐单元 floor fraction 同时保留；前者达标不能包装成所有场景公平。",
        "- 若仍无可行候选，下一突破点应修改 eligible-window/token 语义，而不是继续无限增大权重。",
    ]
    (out / "FACTORIZED_BACKGROUND_DEFICIT_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-factorized-candidate", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--grid", choices=("coarse", "refined"), default="coarse")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    refined = args.grid == "refined"
    seed_base = REFINEMENT_SEED_BASE if refined else VALIDATION_SEED_BASE
    candidates = REFINEMENT_CANDIDATES if refined else DEFICIT_CANDIDATES
    matrix = balanced_evaluation_matrix(
        h.scenarios("full"), 27, seed=26267 if refined else 25267
    )
    base_params = load_base_params(Path(args.frozen_factorized_candidate))
    baseline_params = {**base_params, "background_weight_boost": 1.0}
    baseline_cells = global_cells(baseline_params, matrix, args.runs, seed_base)
    baseline = mean_metrics(baseline_cells)
    candidate_params = [{**base_params, **candidate} for candidate in candidates]
    evaluated = []
    global_rows = []
    for candidate_id, params in enumerate(candidate_params):
        cells = global_cells(params, matrix, args.runs, seed_base)
        evaluated.append((params, cells))
        global_rows.append(global_candidate_row(candidate_id, params, cells, baseline))
    h.write_csv(out / "global_candidate_search.csv", global_rows)
    global_feasible_ids = {
        int(row["candidate_id"])
        for row in global_rows
        if int(row["all_global_gates_pass"])
    }
    signal_rows: List[Dict[str, object]] = []
    for candidate_id in sorted(global_feasible_ids):
        params = candidate_params[candidate_id]
        units, nonjoint_units, cells = evaluate_candidate(
            params,
            matrix,
            args.runs,
            seed_base,
            1800,
            90,
            6000,
        )
        signal_rows.append(
            candidate_row(
                candidate_id,
                params,
                units,
                nonjoint_units,
                cells,
                baseline,
            )
        )
    h.write_csv(out / "signal_candidate_search.csv", signal_rows)
    try:
        selected = dict(select_deficit_candidate(signal_rows))
    except ValueError:
        selected = None
    selected_params = (
        {
            key: float(selected[key])
            for key in (
                "congestion_critical_boost",
                "congestion_optional_scale",
                "slack_critical_boost",
                "background_weight_boost",
                "background_target_ratio",
            )
        }
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
        "seed_rule": f"{seed_base} + run*10000 + scenario_index",
        "scenarios": len(matrix),
        "runs": args.runs,
        "selection_status": "selected" if selected is not None else "no_feasible_candidate",
        "selected_params": selected_params,
        "candidate_grid_name": args.grid,
        "candidate_grid": candidates,
        "global_feasible_candidates": sorted(global_feasible_ids),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "script_sha256": h.sha256(Path(__file__).resolve()),
    }
    h.write_json(out / "run_manifest.json", manifest)
    write_report(out, manifest, global_rows, signal_rows)
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
