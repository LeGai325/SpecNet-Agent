#!/usr/bin/env python3
"""Evaluate an idle-only post-completion eligible window for background work."""

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
        load_base_params,
        mean_metrics,
        run_policy as run_original_policy,
    )
    from .factorized_signal_study import FactorizedSignalRule, verdict_rows
    from .pressure_definition_study import PressureSimulator
    from .three_signal_confirmation_study import (
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
        paired_slice_units,
    )
    from .three_signal_rule_study import analysis_rows, paired_nonjoint_units
except ImportError:  # pragma: no cover
    import proof_harness as h
    from factorized_background_study import (
        BACKGROUND_FLOOR,
        MISS_REGRESSION_LIMIT,
        P99_REGRESSION_LIMIT,
        load_base_params,
        mean_metrics,
        run_policy as run_original_policy,
    )
    from factorized_signal_study import FactorizedSignalRule, verdict_rows
    from pressure_definition_study import PressureSimulator
    from three_signal_confirmation_study import (
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
        paired_slice_units,
    )
    from three_signal_rule_study import analysis_rows, paired_nonjoint_units


PROTOCOL_VERSION = "2026-08-02.factorized-background-eligible-window-v3"
VALIDATION_SEED_BASE = 2_290_000
CONFIRMATION_SEED_BASE = 2_300_000
TARGET_RATIO = 0.20
PARITY_TOLERANCE = 1e-9
BACKGROUND_FLOOR_TOLERANCE = 1e-9


def meets_background_floor(ratio: float) -> bool:
    return float(ratio) >= BACKGROUND_FLOOR - BACKGROUND_FLOOR_TOLERANCE


class IdleEligibleFactorizedRule(FactorizedSignalRule):
    """Preserve live-workflow scheduling; defer only post-completion debt."""

    def flow_weight(self, flow, sim) -> float:
        if flow.background:
            owner = sim.workflows.get(flow.workflow_id)
            if owner is not None and owner.complete_time is None:
                return super().flow_weight(flow, sim)
            if any(not item.background for item in sim.active_flows()):
                return 0.0
            return 0.5
        return super().flow_weight(flow, sim)


class EligibleWindowPressureSimulator(PressureSimulator):
    """Keep unfinished background eligible until its frozen target is served."""

    def remaining_active_bytes(self) -> float:
        return sum(
            flow.remaining
            for flow in self.active_flows()
            if not flow.background
            or self.workflows[flow.workflow_id].complete_time is None
        )

    def finish_workflow(self, workflow) -> None:
        super().finish_workflow(workflow)
        for flow_id in workflow.background_flows:
            flow = self.flows[flow_id]
            if flow.completed_at is None and flow.served < self.background_target(flow):
                # Preserve the original live-workflow path, then expose only
                # the exact post-completion debt instead of a full-size flow.
                debt = max(0.0, self.background_target(flow) - flow.served)
                flow.remaining = min(flow.remaining, debt)
                flow.cancelled = False

    def background_target(self, flow) -> float:
        owner = self.workflows.get(flow.workflow_id)
        action = getattr(owner, "action", "full") if owner is not None else "full"
        spawn_scale = h.up.ACTION_CONFIG[action]["background_scale"]
        original_size = flow.size / max(float(spawn_scale), 1e-9)
        return TARGET_RATIO * original_size

    def deferred_target_reached(self, flow) -> bool:
        owner = self.workflows.get(flow.workflow_id)
        return (
            owner is not None
            and owner.complete_time is not None
            and flow.served >= self.background_target(flow)
        )

    def serve_active_flows(self) -> None:
        super().serve_active_flows()
        for workflow in self.workflows.values():
            workflow.background_bytes_served = sum(
                self.flows[flow_id].served for flow_id in workflow.background_flows
            )
            for flow_id in workflow.background_flows:
                flow = self.flows[flow_id]
                if flow.completed_at is None and self.deferred_target_reached(flow):
                    flow.cancelled = True


def foreground_parity_metrics(
    eligible_summary: Mapping[str, object],
    reference_summary: Mapping[str, object],
) -> Dict[str, float]:
    """Check that deferred background never changes the foreground trajectory."""
    eligible = {
        int(row["workflow_id"]): row
        for row in eligible_summary["workflow_records"]
    }
    reference = {
        int(row["workflow_id"]): row
        for row in reference_summary["workflow_records"]
    }
    common = sorted(set(eligible) & set(reference))
    action_mismatches = sum(
        str(eligible[key]["action"]) != str(reference[key]["action"])
        for key in common
    )
    state_mismatches = sum(
        str(eligible[key].get("decision_state"))
        != str(reference[key].get("decision_state"))
        for key in common
    )
    latency_deltas = [
        abs(float(eligible[key]["latency"]) - float(reference[key]["latency"]))
        for key in common
    ]
    waste_deltas = [
        abs(
            float(eligible[key]["wasted_speculative_bytes"])
            - float(reference[key]["wasted_speculative_bytes"])
        )
        for key in common
    ]
    id_sets_match = set(eligible) == set(reference)
    max_latency_delta = max(latency_deltas, default=0.0)
    max_waste_delta = max(waste_deltas, default=0.0)
    parity_pass = (
        id_sets_match
        and action_mismatches == 0
        and state_mismatches == 0
        and max_latency_delta <= PARITY_TOLERANCE
        and max_waste_delta <= PARITY_TOLERANCE
    )
    return {
        "foreground_parity_workflows": float(len(common)),
        "foreground_action_mismatches": float(action_mismatches),
        "foreground_state_mismatches": float(state_mismatches),
        "foreground_max_abs_latency_delta": max_latency_delta,
        "foreground_max_abs_waste_delta": max_waste_delta,
        "foreground_parity_pass": float(parity_pass),
    }


def run_eligible_policy(
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
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
    simulator = EligibleWindowPressureSimulator(
        specs,
        policy,
        load,
        workload_seed,
        1800,
        6000,
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
    ratio_by_workflow = {
        int(row["workflow_id"]): ratio for row, ratio in zip(records, ratios)
    }
    normally_completed = [
        workflow_id
        for workflow_id, workflow in simulator.workflows.items()
        if workflow.stage == "done"
    ]
    last_completion = max(float(row["arrival_time"]) + float(row["latency"]) for row in records)
    global_metrics = {
        **metrics,
        "background_service_ratio": statistics.mean(ratios),
        "link_utilization": float(summary["link_utilization"]),
        "background_floor_fraction_workflows": statistics.mean(
            meets_background_floor(ratio) for ratio in ratios
        ),
        "background_floor_fraction_completed_workflows": statistics.mean(
            meets_background_floor(ratio_by_workflow[workflow_id])
            for workflow_id in normally_completed
        ) if normally_completed else 1.0,
        "normal_completion_fraction": len(normally_completed) / max(1, len(records)),
        "simulation_end_time": float(simulator.time),
        "post_foreground_drain_time": max(0.0, float(simulator.time) - last_completion),
    }
    return summary, global_metrics


def evaluate(
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
    units: List[Dict[str, object]] = []
    nonjoint_units: List[Dict[str, object]] = []
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
            if checkpoint is not None and checkpoint.is_file():
                payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                if (
                    payload.get("protocol_version") != PROTOCOL_VERSION
                    or int(payload.get("seed", -1)) != seed
                    or list(payload.get("scenario", [])) != list(scenario)
                ):
                    raise ValueError(f"checkpoint protocol mismatch: {checkpoint}")
                units.extend(payload["units"])
                nonjoint_units.extend(payload["nonjoint_units"])
                cells.append(payload["cell"])
                references.append(payload["reference"])
                print(
                    f"[resume] run={run} scenario={scenario_index}", flush=True
                )
                continue
            summaries: Dict[str, Dict[str, object]] = {}
            for name in ("full", "no_congestion", "no_slack", "no_pressure"):
                summary, metrics = run_eligible_policy(
                    IdleEligibleFactorizedRule(params, name), scenario, seed
                )
                summaries[name] = summary
                if name == "full":
                    cells.append(
                        {
                            "run": run,
                            "scenario": scenario_index,
                            "seed": seed,
                            "load": scenario[0],
                            "deadline_scale": scenario[1],
                            "optional_scale": scenario[2],
                            "capacity_scale": scenario[3],
                            **metrics,
                        }
                    )
            scenario_units = paired_slice_units(
                summaries,
                replicate=0,
                eval_run=run,
                scenario_index=scenario_index,
                workload_seed=seed,
            )
            scenario_nonjoint_units = paired_nonjoint_units(
                summaries, run, scenario_index, seed
            )
            units.extend(scenario_units)
            nonjoint_units.extend(scenario_nonjoint_units)
            reference_summary, reference_metrics = run_original_policy(
                FactorizedSignalRule(params, "full"),
                scenario,
                seed,
                1800,
                90,
                6000,
            )
            cells[-1].update(
                foreground_parity_metrics(summaries["full"], reference_summary)
            )
            reference = {
                "run": run,
                "scenario": scenario_index,
                "seed": seed,
                **reference_metrics,
            }
            references.append(reference)
            if checkpoint is not None:
                h.write_json(
                    checkpoint,
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "seed": seed,
                        "scenario": scenario,
                        "units": scenario_units,
                        "nonjoint_units": scenario_nonjoint_units,
                        "cell": cells[-1],
                        "reference": reference,
                    },
                )
        print(f"[eligible-window] run {run + 1}/{runs}", flush=True)
    return units, nonjoint_units, cells, references


def gate_row(
    cells: Sequence[Mapping[str, object]],
    references: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    metrics = mean_metrics(cells)
    reference = mean_metrics(references)
    gates = {
        "background_gate_pass": metrics["background_service_ratio"] >= BACKGROUND_FLOOR,
        "quality_gate_pass": metrics["quality"] >= QUALITY_FLOOR,
        "p99_gate_pass": metrics["p99_latency"] <= P99_REGRESSION_LIMIT * reference["p99_latency"],
        "miss_gate_pass": metrics["deadline_miss_ratio"] <= reference["deadline_miss_ratio"] + MISS_REGRESSION_LIMIT,
        "three_signal_gate_pass": all(row["status"] == "supported" for row in verdicts),
        "foreground_parity_gate_pass": all(
            int(float(row["foreground_parity_pass"])) for row in cells
        ),
    }
    return {
        **{f"mean_{key}": value for key, value in metrics.items()},
        **{f"reference_{key}": value for key, value in reference.items()},
        "background_floor_fraction_cells": statistics.mean(
            meets_background_floor(float(row["background_service_ratio"]))
            for row in cells
        ),
        "background_floor_fraction_workflows": statistics.mean(
            float(row["background_floor_fraction_workflows"]) for row in cells
        ),
        "background_floor_fraction_completed_workflows": statistics.mean(
            float(row["background_floor_fraction_completed_workflows"])
            for row in cells
        ),
        "normal_completion_fraction": statistics.mean(
            float(row["normal_completion_fraction"]) for row in cells
        ),
        "mean_post_foreground_drain_time": statistics.mean(
            float(row["post_foreground_drain_time"]) for row in cells
        ),
        "foreground_parity_fraction_cells": statistics.mean(
            float(row["foreground_parity_pass"]) for row in cells
        ),
        "foreground_action_mismatches": sum(
            float(row["foreground_action_mismatches"]) for row in cells
        ),
        "foreground_state_mismatches": sum(
            float(row["foreground_state_mismatches"]) for row in cells
        ),
        "foreground_max_abs_latency_delta": max(
            float(row["foreground_max_abs_latency_delta"]) for row in cells
        ),
        "foreground_max_abs_waste_delta": max(
            float(row["foreground_max_abs_waste_delta"]) for row in cells
        ),
        "p99_ratio_vs_original": metrics["p99_latency"] / reference["p99_latency"],
        "miss_delta_vs_original": metrics["deadline_miss_ratio"] - reference["deadline_miss_ratio"],
        **{key: int(value) for key, value in gates.items()},
        "all_gates_pass": int(all(gates.values())),
    }


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    gates: Mapping[str, object],
    analysis: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
) -> None:
    verdict = {str(row["claim"]): str(row["status"]) for row in verdicts}
    lines = [
        "# Background Eligible-window 语义扩展实验",
        "",
        "该实验在工作流完成前完全保留原 background 调度。只有完成时仍未达到原始 background 大小 20% 的精确欠额继续保持 eligible，并且只在系统没有任何 foreground flow 时服务；post-completion deferred background 不进入 congestion/slack backlog。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 模式：`{manifest['mode']}`",
        f"- Seed：`{manifest['seed_rule']}`",
        f"- 场景：{manifest['scenarios']} × runs：{manifest['runs']}",
        "",
        "## 全局硬门",
        "",
        f"- Mean background：{float(gates['mean_background_service_ratio']):.4f}；cell floor fraction：{float(gates['background_floor_fraction_cells']):.3f}；all-workflow floor fraction：{float(gates['background_floor_fraction_workflows']):.3f}；normally-completed workflow floor fraction：{float(gates['background_floor_fraction_completed_workflows']):.3f}；normal completion fraction：{float(gates['normal_completion_fraction']):.3f}。",
        f"- p99 ratio vs 原语义：{float(gates['p99_ratio_vs_original']):.4f}×；miss delta：{float(gates['miss_delta_vs_original']):+.5f}。",
        f"- Quality：{float(gates['mean_quality']):.4f}；平均 post-foreground drain：{float(gates['mean_post_foreground_drain_time']):.2f} epochs。",
        f"- Foreground parity：cell fraction={float(gates['foreground_parity_fraction_cells']):.3f}；action mismatches={int(float(gates['foreground_action_mismatches']))}；state mismatches={int(float(gates['foreground_state_mismatches']))}；max latency delta={float(gates['foreground_max_abs_latency_delta']):.3g}；max waste delta={float(gates['foreground_max_abs_waste_delta']):.3g}。",
        f"- 全部门：{'通过' if int(gates['all_gates_pass']) else '未通过'}。",
        "",
        "## 三信号 broad 复核",
        "",
        "| 假设 | Delta | 95% CI | Holm p | 判定 |",
        "|---|---:|---:|---:|---|",
    ]
    for hypothesis in ("H1-C", "H1-S", "H1-P-backlog"):
        row = next(
            item
            for item in analysis
            if item["hypothesis"] == hypothesis and int(item["primary_metric"])
        )
        lines.append(
            f"| {hypothesis} | {float(row['mean_delta_ablation_minus_full']):+.5f} | "
            f"[{float(row['ci95_low']):+.5f}, {float(row['ci95_high']):+.5f}] | "
            f"{float(row['holm_adjusted_p']):.4g} | {verdict[hypothesis]} |"
        )
    lines += [
        "",
        "## 创新与边界",
        "",
        "- 创新点是把完成前调度与完成后精确欠额分开：前者保持原因果机制，后者使用 deadline-preserving idle-capacity reservation。",
        "- v3 将逐 workflow action/state/latency/speculative-waste parity 加入硬门，防止 background 扩展暗中改变前台轨迹。",
        "- 20% 的硬门定义为场景均值；workflow floor 使用 1e-9 浮点容差。它仍必须与正常完成工作流的 floor 分开解释，不能把均值通过写成逐 workflow 保证。",
        "- 这是新 simulator 语义，不可覆盖旧语义下两轮无可行权重的负结果；两套结果必须并列报告。",
        "- post-foreground drain 是新增代价；真实系统还需验证 background 任务是否允许跨主请求生命周期继续执行。",
        "- 若确认通过，可称“在可延迟 background 的扩展模型中满足硬门”，不能称原 simulator 已可部署。",
    ]
    (out / "FACTORIZED_BACKGROUND_ELIGIBLE_WINDOW_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("validate", "confirm"), required=True)
    parser.add_argument("--frozen-factorized-candidate", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--runs", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    confirm = args.mode == "confirm"
    runs = args.runs or (3 if confirm else 1)
    seed_base = CONFIRMATION_SEED_BASE if confirm else VALIDATION_SEED_BASE
    matrix = balanced_evaluation_matrix(
        h.scenarios("full"), 45 if confirm else 27, seed=28267 if confirm else 27267
    )
    params = load_base_params(Path(args.frozen_factorized_candidate))
    units, nonjoint_units, cells, references = evaluate(
        params, matrix, runs, seed_base, out / "checkpoints"
    )
    analysis = analysis_rows(units)
    nonjoint_analysis = analysis_rows(nonjoint_units)
    verdicts = verdict_rows(analysis, nonjoint_analysis, "confirm")
    gates = gate_row(cells, references, verdicts)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "seed_rule": f"{seed_base} + run*10000 + scenario_index",
        "scenarios": len(matrix),
        "runs": runs,
        "target_ratio": TARGET_RATIO,
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
    h.write_csv(out / "eligible_window_cells.csv", cells)
    h.write_csv(out / "original_semantics_reference_cells.csv", references)
    h.write_csv(out / "deployment_gates.csv", [gates])
    h.write_json(out / "run_manifest.json", manifest)
    write_report(out, manifest, gates, analysis, verdicts)
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
