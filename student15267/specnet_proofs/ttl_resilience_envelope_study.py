#!/usr/bin/env python3
"""Derive and verify a finite-TTL resilience envelope for deferred work."""

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


PROTOCOL_VERSION = "2026-08-05.ttl-resilience-envelope-v1"
VALIDATION_SEED_BASE = 2_390_000
CONFIRMATION_SEED_BASE = 2_400_000
# The grid refines the historical 1024 -> 2048 gap without treating a
# simulator epoch as a production-time recommendation.
TTL_GRID = (1024, 1152, 1280, 1408, 1536, 1664, 1792, 1920, 2048)
HORIZON_TOLERANCE = 1e-9


def ttl_label(ttl_epochs: int | None) -> str:
    return "unbounded" if ttl_epochs is None else f"ttl_{ttl_epochs}"


def percentile_or_nan(values: Sequence[float], probability: float) -> float:
    return h.up.percentile(list(values), probability) if values else math.nan


def first_grid_ttl_at_least(
    required_ttl_epochs: float,
    ttl_grid: Sequence[int] = TTL_GRID,
) -> int:
    """Return the smallest predeclared TTL no shorter than the observed bound."""
    for candidate in ttl_grid:
        if candidate + HORIZON_TOLERANCE >= required_ttl_epochs:
            return candidate
    raise ValueError(
        f"observed required TTL {required_ttl_epochs:.6g} exceeds fixed grid maximum"
    )


def run_quiescent_policy(
    params: Mapping[str, float],
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    ttl_epochs: int | None,
) -> Tuple[Dict[str, object], Dict[str, float], List[Dict[str, object]]]:
    """Run one policy and expose per-workflow post-completion horizons."""
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

    records = {
        int(record["workflow_id"]): record for record in summary["workflow_records"]
    }
    workflow_rows: List[Dict[str, object]] = []
    target_lags: List[float] = []
    terminal_lags: List[float] = []
    ratios: List[float] = []
    expiry: List[float] = []
    debt_ratios: List[float] = []
    for workflow_id, workflow in sorted(simulator.workflows.items()):
        record = records.get(workflow_id)
        if record is None:
            continue
        background_total = background_totals[workflow_id]
        ratio = float(record["background_bytes_served"]) / background_total
        ratios.append(ratio)
        complete_time = getattr(workflow, "complete_time", None)
        target_time = getattr(workflow, "deferred_target_reached_time", None)
        terminal_time = getattr(workflow, "deferred_terminal_time", None)
        target_lag = (
            max(0.0, float(target_time) - float(complete_time))
            if complete_time is not None and target_time is not None
            else math.nan
        )
        terminal_lag = (
            max(0.0, float(terminal_time) - float(complete_time))
            if complete_time is not None and terminal_time is not None
            else math.nan
        )
        if not math.isnan(target_lag):
            target_lags.append(target_lag)
        if not math.isnan(terminal_lag):
            terminal_lags.append(terminal_lag)
        at_completion = float(getattr(workflow, "background_at_completion", 0.0))
        debt_ratio = max(0.0, TARGET_RATIO - at_completion / background_total)
        debt_ratios.append(debt_ratio)
        expired = float(getattr(workflow, "deferred_ttl_expired", False))
        expiry.append(expired)
        workflow_rows.append(
            {
                "workflow_id": workflow_id,
                "background_ratio": ratio,
                "strict_floor_pass": int(meets_background_floor(ratio)),
                "background_ratio_at_completion": at_completion / background_total,
                "post_completion_debt_ratio": debt_ratio,
                "deferred_required": int(debt_ratio > BACKGROUND_FLOOR_TOLERANCE),
                "target_lag_epochs": target_lag,
                "terminal_lag_epochs": terminal_lag,
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
        "mean_post_completion_debt_ratio": statistics.mean(debt_ratios),
        "deferred_workflow_fraction": statistics.mean(
            debt > BACKGROUND_FLOOR_TOLERANCE for debt in debt_ratios
        ),
        "mean_target_lag_epochs": statistics.mean(target_lags)
        if target_lags
        else math.nan,
        "p50_target_lag_epochs": percentile_or_nan(target_lags, 0.50),
        "p95_target_lag_epochs": percentile_or_nan(target_lags, 0.95),
        "p99_target_lag_epochs": percentile_or_nan(target_lags, 0.99),
        "max_target_lag_epochs": max(target_lags, default=math.nan),
        "max_terminal_lag_epochs": max(terminal_lags, default=math.nan),
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


def horizon_rows(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
    checkpoint_dir: Path | None = None,
) -> Tuple[
    List[Dict[str, object]],
    List[Dict[str, object]],
    List[Dict[str, object]],
    List[Dict[str, object]],
]:
    """Measure unbounded horizons and preserve a paired original reference."""
    cells: List[Dict[str, object]] = []
    workflows: List[Dict[str, object]] = []
    summaries: List[Dict[str, object]] = []
    references: List[Dict[str, object]] = []
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = seed_base + run * 10_000 + scenario_index
            checkpoint = (
                checkpoint_dir / f"horizon_run_{run:02d}_scenario_{scenario_index:03d}.json"
                if checkpoint_dir is not None
                else None
            )
            payload = (
                json.loads(checkpoint.read_text(encoding="utf-8"))
                if checkpoint is not None and checkpoint.is_file()
                else None
            )
            if payload is not None:
                if (
                    payload.get("protocol_version") != PROTOCOL_VERSION
                    or int(payload.get("seed", -1)) != seed
                    or list(payload.get("scenario", [])) != list(scenario)
                ):
                    raise ValueError(f"checkpoint protocol mismatch: {checkpoint}")
                cells.append(dict(payload["cell"]))
                workflows.extend(dict(row) for row in payload["workflows"])
                summaries.append(dict(payload["summary"]))
                references.append(dict(payload["reference"]))
                continue
            summary, metrics, rows = run_quiescent_policy(params, scenario, seed, None)
            reference_summary, reference_metrics = run_original_policy(
                FactorizedSignalRule(params, "full"), scenario, seed, 1800, 90, 6000
            )
            cell = {
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
            annotated_workflows = [
                {
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
            ]
            reference = {
                "run": run,
                "scenario": scenario_index,
                "seed": seed,
                **reference_metrics,
            }
            cells.append(cell)
            workflows.extend(annotated_workflows)
            summaries.append(summary)
            references.append(reference)
            if checkpoint is not None:
                h.write_json(
                    checkpoint,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "seed": seed,
                        "scenario": scenario,
                        "cell": cell,
                        "workflows": annotated_workflows,
                        "summary": summary,
                        "reference": reference,
                    },
                )
        print(f"[horizon] run {run + 1}/{runs}", flush=True)
    return cells, workflows, summaries, references


def select_ttl_from_horizons(
    workflow_rows: Sequence[Mapping[str, object]],
    ttl_grid: Sequence[int] = TTL_GRID,
) -> Dict[str, object]:
    lags = [
        float(row["target_lag_epochs"])
        for row in workflow_rows
        if not math.isnan(float(row["target_lag_epochs"]))
    ]
    if not lags:
        raise ValueError("no completed workflow exposed a finite target horizon")
    required = max(lags)
    selected = first_grid_ttl_at_least(required, ttl_grid)
    return {
        "required_ttl_epochs": required,
        "required_ttl_p50_epochs": percentile_or_nan(lags, 0.50),
        "required_ttl_p95_epochs": percentile_or_nan(lags, 0.95),
        "required_ttl_p99_epochs": percentile_or_nan(lags, 0.99),
        "selected_ttl_epochs": selected,
        "selection_headroom_epochs": selected - required,
        "workflow_horizons": len(lags),
    }


def finite_rows(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    runs: int,
    seed_base: int,
    ttl_epochs: int,
    checkpoint_dir: Path | None = None,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Re-run the selected finite TTL against the original policy."""
    cells: List[Dict[str, object]] = []
    workflows: List[Dict[str, object]] = []
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
    for run in range(runs):
        for scenario_index, scenario in enumerate(matrix):
            seed = seed_base + run * 10_000 + scenario_index
            checkpoint = (
                checkpoint_dir / f"finite_run_{run:02d}_scenario_{scenario_index:03d}.json"
                if checkpoint_dir is not None
                else None
            )
            payload = (
                json.loads(checkpoint.read_text(encoding="utf-8"))
                if checkpoint is not None and checkpoint.is_file()
                else None
            )
            if payload is not None:
                if (
                    payload.get("protocol_version") != PROTOCOL_VERSION
                    or int(payload.get("seed", -1)) != seed
                    or list(payload.get("scenario", [])) != list(scenario)
                    or int(payload.get("ttl_epochs", -1)) != ttl_epochs
                ):
                    raise ValueError(f"checkpoint protocol mismatch: {checkpoint}")
                cells.append(dict(payload["cell"]))
                workflows.extend(dict(row) for row in payload["workflows"])
                continue
            summary, metrics, rows = run_quiescent_policy(
                params, scenario, seed, ttl_epochs
            )
            reference_summary, reference_metrics = run_original_policy(
                FactorizedSignalRule(params, "full"), scenario, seed, 1800, 90, 6000
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
                "reference_link_utilization": float(reference_metrics["link_utilization"]),
            }
            annotated_workflows = [
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
            ]
            cells.append(cell)
            workflows.extend(annotated_workflows)
            if checkpoint is not None:
                h.write_json(
                    checkpoint,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "ttl_epochs": ttl_epochs,
                        "seed": seed,
                        "scenario": scenario,
                        "cell": cell,
                        "workflows": annotated_workflows,
                    },
                )
        print(f"[finite] run {run + 1}/{runs}", flush=True)
    return cells, workflows


def deployment_gate_row(cells: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    metrics = mean_metrics(cells)
    utilization_delta = statistics.mean(
        float(row["link_utilization"]) - float(row["reference_link_utilization"])
        for row in cells
    )
    gates = {
        "background_gate_pass": metrics["background_service_ratio"] >= BACKGROUND_FLOOR,
        "background_cell_floor_gate_pass": all(
            meets_background_floor(float(row["background_service_ratio"])) for row in cells
        ),
        "background_workflow_floor_gate_pass": all(
            float(row["background_floor_fraction_workflows"])
            >= 1.0 - BACKGROUND_FLOOR_TOLERANCE
            for row in cells
        ),
        "expiry_gate_pass": all(
            float(row["deferred_expiry_fraction"]) == 0.0 for row in cells
        ),
        "quality_gate_pass": metrics["quality"] >= QUALITY_FLOOR,
        "foreground_parity_gate_pass": all(
            int(float(row["foreground_parity_pass"])) for row in cells
        ),
        "utilization_budget_pass": utilization_delta <= UTILIZATION_DELTA_LIMIT,
    }
    return {
        "cells": len(cells),
        "mean_background_service_ratio": metrics["background_service_ratio"],
        "background_floor_fraction_cells": statistics.mean(
            meets_background_floor(float(row["background_service_ratio"])) for row in cells
        ),
        "background_floor_fraction_workflows": statistics.mean(
            float(row["background_floor_fraction_workflows"]) for row in cells
        ),
        "mean_deferred_expiry_fraction": statistics.mean(
            float(row["deferred_expiry_fraction"]) for row in cells
        ),
        "mean_quality": metrics["quality"],
        "mean_p99_latency": metrics["p99_latency"],
        "mean_deadline_miss_ratio": metrics["deadline_miss_ratio"],
        "mean_link_utilization": metrics["link_utilization"],
        "utilization_delta_vs_original": utilization_delta,
        "mean_post_foreground_drain_time": statistics.mean(
            float(row["post_foreground_drain_time"]) for row in cells
        ),
        "mean_p99_target_lag_epochs": statistics.mean(
            float(row["p99_target_lag_epochs"])
            for row in cells
            if not math.isnan(float(row["p99_target_lag_epochs"]))
        ),
        "max_target_lag_epochs": max(
            float(row["max_target_lag_epochs"])
            for row in cells
            if not math.isnan(float(row["max_target_lag_epochs"]))
        ),
        "foreground_parity_fraction_cells": statistics.mean(
            float(row["foreground_parity_pass"]) for row in cells
        ),
        **{key: int(value) for key, value in gates.items()},
        "all_deployment_gates_pass": int(all(gates.values())),
    }


def write_report(
    output_dir: Path,
    manifest: Mapping[str, object],
    horizon_selection: Mapping[str, object] | None,
    gates: Mapping[str, object],
) -> None:
    lines = [
        "# TTL 韧性包络实验",
        "",
        "本实验把有限 TTL 选择从稀疏网格试错升级为两层证据：(1) 在 quiescent、无界 deferred 参考轨迹中，记录每个 workflow 达到 20% floor 所需的 post-completion horizon；(2) 用选定的有限 TTL 从头回放并对原语义做逐 workflow foreground parity。horizon 只用于预注册网格选择，不能单独替代有限 TTL 实验。",
        "",
        f"- 协议：`{manifest['protocol_version']}`；模式：`{manifest['mode']}`。",
        f"- Seed：`{manifest['seed_rule']}`；场景：`{manifest['scenarios']}` x runs：`{manifest['runs']}`。",
        f"- 预先冻结的 TTL 网格：`{manifest['ttl_grid']}`。",
        "",
        "## 文献启发与指标",
        "",
        "- Dean 与 Barroso, *The Tail at Scale* (CACM 2013, DOI `10.1145/2408776.2408794`) 提醒均值会掩盖尾部风险，因此这里报告 workflow horizon 的 P50/P95/P99/最大值和逐 workflow floor。",
        "- Wilson et al., *D3: Deadline-Driven Delivery in Datacenters* (SIGCOMM 2011, DOI `10.1145/2018436.2018482`) 启发将有限时间预算作为显式输入；这里的 TTL 只约束完成后 debt，不影响 foreground deadline 调度。",
        "- Barroso 与 Holzle, *The Case for Energy-Proportional Computing* (Computer 2007, DOI `10.1109/MC.2007.443`) 启发资源成本账本；本实验仅报告 link-utilization 增量，不能解释为实测能耗。",
        "",
    ]
    if horizon_selection is not None:
        lines += [
            "## Horizon 选择",
            "",
            "| 指标 | 值 |",
            "|---|---:|",
            f"| Workflow horizons | {int(horizon_selection['workflow_horizons'])} |",
            f"| Required TTL P50 | {float(horizon_selection['required_ttl_p50_epochs']):.2f} epochs |",
            f"| Required TTL P95 | {float(horizon_selection['required_ttl_p95_epochs']):.2f} epochs |",
            f"| Required TTL P99 | {float(horizon_selection['required_ttl_p99_epochs']):.2f} epochs |",
            f"| Observed maximum | {float(horizon_selection['required_ttl_epochs']):.2f} epochs |",
            f"| Grid-selected TTL | {int(horizon_selection['selected_ttl_epochs'])} epochs |",
            f"| Selection headroom | {float(horizon_selection['selection_headroom_epochs']):.2f} epochs |",
            "",
            "选择规则为：取不小于 validation 观察最大 horizon 的最小预注册网格点。它是在新 seed validation 上作出的设计选择；不能把 validation 最大值当作未来 workload 的理论上界。",
            "",
        ]
    lines += [
        "## 有限 TTL 回放门",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| Cells | {int(gates['cells'])} |",
        f"| Mean background service | {float(gates['mean_background_service_ratio']):.6f} |",
        f"| Cell / workflow floor | {float(gates['background_floor_fraction_cells']):.3f} / {float(gates['background_floor_fraction_workflows']):.3f} |",
        f"| Deferred expiry | {float(gates['mean_deferred_expiry_fraction']):.6f} |",
        f"| Mean quality | {float(gates['mean_quality']):.6f} |",
        f"| Foreground parity cells | {float(gates['foreground_parity_fraction_cells']):.3f} |",
        f"| Delta utilization | {float(gates['utilization_delta_vs_original']):+.6f} |",
        f"| Max observed finite horizon | {float(gates['max_target_lag_epochs']):.2f} epochs |",
        f"| All frozen gates | {int(gates['all_deployment_gates_pass'])} |",
        "",
        "## 解释边界",
        "",
        f"- floor 采用 `{BACKGROUND_FLOOR:.2f}`，数值容差 `{BACKGROUND_FLOOR_TOLERANCE:.0e}`；foreground parity 容差 `{PARITY_TOLERANCE:.0e}`。",
        f"- link-utilization 增量预算为 `{UTILIZATION_DELTA_LIMIT:.2f}`；这是本协议工程约束，而不是通用能耗阈值。",
        "- 该包络没有真实任务价值、到期收益、tenant、能耗计或非平稳线上 trace；因此其输出是 simulator 内的机制边界，而非产品 TTL 建议。",
    ]
    (output_dir / "TTL_RESILIENCE_ENVELOPE_REPORT.md").write_text(
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
    scenario_count = args.scenarios or (27 if confirmation else 9)
    seed_base = CONFIRMATION_SEED_BASE if confirmation else VALIDATION_SEED_BASE
    matrix = balanced_evaluation_matrix(
        h.scenarios("full"),
        scenario_count,
        seed=30269 if confirmation else 30268,
    )
    params = load_base_params(Path(args.frozen_factorized_candidate))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if confirmation:
        selection = json.loads(
            Path(args.frozen_selection_file).read_text(encoding="utf-8")
        )
        ttl_epochs = int(selection["selected_ttl_epochs"])
        horizon_selection = None
    else:
        horizon_cells, horizon_workflows, _, _ = horizon_rows(
            params,
            matrix,
            runs,
            seed_base,
            output_dir / "checkpoints",
        )
        horizon_selection = select_ttl_from_horizons(horizon_workflows)
        ttl_epochs = int(horizon_selection["selected_ttl_epochs"])
        h.write_csv(output_dir / "unbounded_horizon_cells.csv", horizon_cells)
        h.write_csv(output_dir / "unbounded_horizon_workflows.csv", horizon_workflows)
        h.write_json(output_dir / "ttl_selection.json", horizon_selection)

    cells, workflows = finite_rows(
        params,
        matrix,
        runs,
        seed_base,
        ttl_epochs,
        output_dir / "checkpoints",
    )
    gates = deployment_gate_row(cells)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "seed_rule": f"{seed_base} + run*10000 + scenario_index",
        "scenarios": len(matrix),
        "runs": runs,
        "ttl_grid": list(TTL_GRID),
        "selected_ttl_epochs": ttl_epochs,
        "evaluation_matrix": matrix,
        "selected_params": params,
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "script_sha256": h.sha256(Path(__file__).resolve()),
    }
    h.write_csv(output_dir / "finite_ttl_cells.csv", cells)
    h.write_csv(output_dir / "finite_ttl_workflows.csv", workflows)
    h.write_csv(output_dir / "deployment_gates.csv", [gates])
    h.write_json(output_dir / "run_manifest.json", manifest)
    if confirmation:
        h.write_json(
            output_dir / "ttl_selection.json",
            {
                "source_selection_file": str(
                    Path(args.frozen_selection_file).resolve()
                ),
                "selected_ttl_epochs": ttl_epochs,
            },
        )
    write_report(output_dir, manifest, horizon_selection, gates)
    print(f"[done] results written to {output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
