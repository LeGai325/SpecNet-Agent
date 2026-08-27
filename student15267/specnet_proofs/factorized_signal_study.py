#!/usr/bin/env python3
"""Factorized three-signal mechanism with independent control paths."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .three_signal_confirmation_study import PRIMARY_SPECS, QUALITY_FLOOR, run_once
    from .three_signal_rule_study import (
        analysis_rows,
        disjoint_balanced_matrices,
        paired_nonjoint_units,
    )
    from .three_signal_confirmation_study import paired_slice_units
except ImportError:  # pragma: no cover - direct execution from this directory
    import proof_harness as h
    from three_signal_confirmation_study import PRIMARY_SPECS, QUALITY_FLOOR, run_once
    from three_signal_rule_study import (
        analysis_rows,
        disjoint_balanced_matrices,
        paired_nonjoint_units,
    )
    from three_signal_confirmation_study import paired_slice_units


PROTOCOL_VERSION = "2026-07-30.factorized-three-signal-v1"
SELECTION_SEED_BASE = 2_010_000
SMOKE_TEST_SEED_BASE = 2_020_000
CONFIRM_TEST_SEED_BASE = 2_110_000
NEUTRAL_PRESSURE = "mid_spec"


class FactorizedSignalRule(h.up.CriticalPathOnlyPolicy):
    """Map each signal to a distinct, auditable network-control mechanism."""

    name = "full"

    def __init__(
        self,
        params: Mapping[str, float],
        ablation: str = "full",
        seed: int = 0,
    ) -> None:
        super().__init__(seed)
        if ablation not in ("full", "no_congestion", "no_slack", "no_pressure"):
            raise ValueError(f"unknown ablation: {ablation}")
        self.params = dict(params)
        self.ablation = ablation
        self.name = ablation

    def decide_action(self, sim, workflow) -> str:
        pressure = (
            NEUTRAL_PRESSURE
            if self.ablation == "no_pressure"
            else sim.pressure_bucket(workflow)
        )
        action = "recovery" if pressure == "high_spec" else "full"
        self.action_counter[action] += 1
        workflow.decision_state = (
            sim.congestion_level(),
            sim.workflow_slack_bucket(workflow),
            sim.pressure_bucket(workflow),
        )
        return action

    def flow_weight(self, flow, sim) -> float:
        weight = super().flow_weight(flow, sim)
        critical = flow.role in ("critical_control", "critical_bulk") or flow.required
        optional = flow.speculative or flow.background

        if self.ablation != "no_congestion" and sim.congestion_level() == "high":
            if critical:
                weight *= self.params["congestion_critical_boost"]
            elif optional:
                weight *= self.params["congestion_optional_scale"]

        if self.ablation != "no_slack" and critical:
            owner = sim.workflows.get(flow.workflow_id)
            state = getattr(owner, "observable_state", None) if owner is not None else None
            if state is not None and state[1] == "tight":
                weight *= self.params["slack_critical_boost"]
        if flow.background:
            background_boost = self.params.get("background_weight_boost", 1.0)
            target_ratio = self.params.get("background_target_ratio")
            if target_ratio is not None:
                owner = sim.workflows.get(flow.workflow_id)
                action = getattr(owner, "action", "full") if owner is not None else "full"
                spawn_scale = h.up.ACTION_CONFIG[action]["background_scale"]
                original_size = flow.size / max(float(spawn_scale), 1e-9)
                if flow.served >= float(target_ratio) * original_size:
                    background_boost = 1.0
            weight *= background_boost
        return weight


def candidate_rules() -> List[Dict[str, float]]:
    output = []
    for congestion_boost in (1.25, 1.50, 2.00):
        for optional_scale in (0.50, 0.75):
            for slack_boost in (1.25, 1.50, 2.00):
                output.append(
                    {
                        "congestion_critical_boost": congestion_boost,
                        "congestion_optional_scale": optional_scale,
                        "slack_critical_boost": slack_boost,
                    }
                )
    return output


def evaluate_rule(
    params: Mapping[str, float],
    matrix: Sequence[Tuple[str, float, float, float]],
    eval_runs: int,
    seed_base: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Counter[str]]:
    units: List[Dict[str, object]] = []
    nonjoint_units: List[Dict[str, object]] = []
    actions: Counter[str] = Counter()
    for eval_run in range(eval_runs):
        for scenario_index, scenario in enumerate(matrix):
            workload_seed = seed_base + eval_run * 10_000 + scenario_index
            policies = {
                name: FactorizedSignalRule(params, name)
                for name in ("full", "no_congestion", "no_slack", "no_pressure")
            }
            summaries = {
                name: run_once(
                    policy,
                    scenario,
                    workload_seed,
                    duration,
                    max_workflows,
                    max_time,
                )
                for name, policy in policies.items()
            }
            units.extend(
                paired_slice_units(
                    summaries,
                    replicate=0,
                    eval_run=eval_run,
                    scenario_index=scenario_index,
                    workload_seed=workload_seed,
                )
            )
            nonjoint_units.extend(
                paired_nonjoint_units(
                    summaries,
                    eval_run,
                    scenario_index,
                    workload_seed,
                )
            )
            for action, count in summaries["full"]["action_counts"].items():
                actions[action] += int(count)
    return units, nonjoint_units, actions


def primary_means(units: Sequence[Mapping[str, object]]) -> Dict[str, float]:
    output = {}
    for hypothesis, (_, _, _, metric) in PRIMARY_SPECS.items():
        selected = [row for row in units if row["hypothesis"] == hypothesis]
        values = [(int(row["scenario"]), float(row[f"delta_{metric}"])) for row in selected]
        output[hypothesis] = h.stratified_mean(values) if values else math.nan
    return output


def candidate_score_rows(
    candidates: Sequence[Mapping[str, float]],
    all_units: Sequence[Sequence[Mapping[str, object]]],
    all_nonjoint_units: Sequence[Sequence[Mapping[str, object]]],
    action_counts: Sequence[Counter[str]],
) -> List[Dict[str, object]]:
    output = []
    for candidate_id, (params, units, nonjoint_units, counts) in enumerate(
        zip(candidates, all_units, all_nonjoint_units, action_counts)
    ):
        means = primary_means(units)
        nonjoint_means = primary_means(nonjoint_units)
        normalized = {}
        nonjoint_normalized = {}
        for hypothesis, (_, _, _, metric) in PRIMARY_SPECS.items():
            selected = [row for row in units if row["hypothesis"] == hypothesis]
            selected_nonjoint = [
                row for row in nonjoint_units if row["hypothesis"] == hypothesis
            ]
            scale = max(
                1e-9,
                statistics.mean(abs(float(row[f"full_{metric}"])) for row in selected),
            )
            nonjoint_scale = max(
                1e-9,
                statistics.mean(
                    abs(float(row[f"full_{metric}"])) for row in selected_nonjoint
                ),
            )
            normalized[hypothesis] = means[hypothesis] / scale
            nonjoint_normalized[hypothesis] = (
                nonjoint_means[hypothesis] / nonjoint_scale
            )
        quality_fraction = statistics.mean(
            int(row["both_quality_feasible"]) for row in units
        )
        output.append(
            {
                "candidate_id": candidate_id,
                **params,
                "full_actions": counts["full"],
                "recovery_actions": counts["recovery"],
                "adaptive": int(counts["full"] > 0 and counts["recovery"] > 0),
                "quality_feasible_fraction": quality_fraction,
                "positive_primary_count": sum(value > 0 for value in means.values()),
                "positive_nonjoint_primary_count": sum(
                    value > 0 for value in nonjoint_means.values()
                ),
                "minimum_normalized_effect": min(normalized.values()),
                "minimum_nonjoint_normalized_effect": min(nonjoint_normalized.values()),
                "mean_normalized_effect": statistics.mean(normalized.values()),
                **{
                    f"{hypothesis}_delta": means[hypothesis]
                    for hypothesis in PRIMARY_SPECS
                },
                **{
                    f"{hypothesis}_nonjoint_delta": nonjoint_means[hypothesis]
                    for hypothesis in PRIMARY_SPECS
                },
            }
        )
    return output


def select_candidate(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    feasible = [
        row
        for row in rows
        if int(row["adaptive"])
        and float(row["quality_feasible_fraction"]) >= 0.95
        and int(row["positive_primary_count"]) == len(PRIMARY_SPECS)
        and int(row["positive_nonjoint_primary_count"]) == len(PRIMARY_SPECS)
    ]
    if not feasible:
        raise ValueError("no factorized candidate has three positive broad and nonjoint effects")
    return max(
        feasible,
        key=lambda row: (
            float(row["minimum_normalized_effect"]),
            float(row["minimum_nonjoint_normalized_effect"]),
            float(row["mean_normalized_effect"]),
            -int(row["candidate_id"]),
        ),
    )


def verdict_rows(
    analysis: Sequence[Mapping[str, object]],
    nonjoint_analysis: Sequence[Mapping[str, object]],
    mode: str,
) -> List[Dict[str, object]]:
    output = []
    for hypothesis in PRIMARY_SPECS:
        row = next(
            item for item in analysis
            if item["hypothesis"] == hypothesis and int(item["primary_metric"])
        )
        nonjoint = next(
            item for item in nonjoint_analysis
            if item["hypothesis"] == hypothesis and int(item["primary_metric"])
        )
        broad_pass = (
            float(row["mean_delta_ablation_minus_full"]) > 0
            and float(row["ci95_low"]) > 0
            and float(row["holm_adjusted_p"]) < 0.05
        )
        nonjoint_pass = (
            float(nonjoint["mean_delta_ablation_minus_full"]) > 0
            and float(nonjoint["ci95_low"]) > 0
            and float(nonjoint["holm_adjusted_p"]) < 0.05
        )
        quality_pass = (
            float(row["mean_full_quality"]) >= QUALITY_FLOOR
            and float(row["mean_ablation_quality"]) >= QUALITY_FLOOR
            and float(row["quality_feasible_fraction"]) >= 0.95
            and float(nonjoint["quality_feasible_fraction"]) >= 0.95
        )
        coverage_pass = (
            int(row["scenario_strata"]) >= (6 if mode == "smoke" else 18)
            and int(nonjoint["scenario_strata"]) >= (4 if mode == "smoke" else 12)
        )
        output.append(
            {
                "claim": hypothesis,
                "status": "supported" if broad_pass and nonjoint_pass and quality_pass and coverage_pass else "not_supported",
                "broad_direction_pass": int(broad_pass),
                "nonjoint_direction_pass": int(nonjoint_pass),
                "quality_gate_pass": int(quality_pass),
                "coverage_gate_pass": int(coverage_pass),
            }
        )
    return output


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    analysis: Sequence[Mapping[str, object]],
    nonjoint_analysis: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
) -> None:
    broad = {
        str(row["hypothesis"]): row for row in analysis if int(row["primary_metric"])
    }
    nonjoint = {
        str(row["hypothesis"]): row
        for row in nonjoint_analysis
        if int(row["primary_metric"])
    }
    verdict = {str(row["claim"]): str(row["status"]) for row in verdicts}
    lines = [
        "# 因子化三信号机制实验",
        "",
        "三项信号使用不同作用路径：pressure 控制 full/recovery admission；congestion 控制全局关键/可选流权重；slack 控制当前 tight workflow 的关键流权重。该结构避免共享风险阈值把三项效应混成同一交互。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 模式：`{manifest['mode']}`",
        f"- 冻结参数：`{manifest['selected_params']}`",
        f"- 评估：{manifest['evaluation_scenarios']} 场景 × {manifest['evaluation_runs']} runs",
        f"- Seed：`{manifest['test_seed_rule']}`",
        "- Admission 动作：full/recovery，静态质量下界≥0.98。",
        "",
        "## Broad 主结果",
        "",
        "| 假设 | 主指标 delta | 95% CI | Holm p | Full Q | Ablation Q | Nonjoint delta | 判定 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for hypothesis in PRIMARY_SPECS:
        row = broad[hypothesis]
        secondary = nonjoint[hypothesis]
        lines.append(
            f"| {hypothesis} | {float(row['mean_delta_ablation_minus_full']):.4f} | "
            f"[{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] | "
            f"{float(row['holm_adjusted_p']):.4g} | {float(row['mean_full_quality']):.4f} | "
            f"{float(row['mean_ablation_quality']):.4f} | "
            f"{float(secondary['mean_delta_ablation_minus_full']):.4f} | {verdict[hypothesis]} |"
        )
    lines += [
        "",
        "## 创新点",
        "",
        "1. 三信号分别控制 source admission、global congestion scheduling 和 per-workflow deadline scheduling，降低状态交互混杂。",
        "2. admission 只用 full/recovery，质量下界≥0.98；调度权重不改变分支保留数量。",
        "3. 候选只搜索有物理含义的 boost/scale，选择前要求 broad 与 nonjoint 三项方向全部为正。",
        "4. full-reference workflow 配对、场景分层 bootstrap、随机化检验和 Holm correction 与前序协议一致。",
        "",
        "## 边界与展望",
        "",
        "- 这是机制证明，不是部署通过；仍需检查 background service、公平性和全局 tail regression。",
        "- 调度权重参数来自有限开发集，confirm 结果不能用于再次调 boost；任何新参数需要新 seeds。",
        "- 下一步应加入容量观测噪声和 slack 估计误差，检验三条作用路径是否仍稳定。",
        "",
        "详见 `candidate_search.csv`、`confirmation_analysis.csv`、`nonjoint_confirmation_analysis.csv` 与 `claim_verdicts.csv`。",
    ]
    (out / "FACTORIZED_SIGNAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "confirm"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--frozen-candidate", default=None)
    parser.add_argument("--evaluation-runs", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "smoke":
        duration, max_workflows, max_time = 700, 28, 2600
        selection_matrix, evaluation_matrix = disjoint_balanced_matrices(
            h.scenarios("smoke"), 12
        )
        evaluation_runs = args.evaluation_runs or 3
        test_seed_base = SMOKE_TEST_SEED_BASE
        out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / "factorized_signal_smoke_20260730"
        candidates = candidate_rules()
        all_units = []
        all_nonjoint_units = []
        all_counts = []
        for candidate_id, params in enumerate(candidates):
            print(f"[select] candidate {candidate_id + 1}/{len(candidates)}", flush=True)
            units, nonjoint_units, counts = evaluate_rule(
                params,
                selection_matrix,
                1,
                SELECTION_SEED_BASE,
                duration,
                max_workflows,
                max_time,
            )
            all_units.append(units)
            all_nonjoint_units.append(nonjoint_units)
            all_counts.append(counts)
        search_rows = candidate_score_rows(
            candidates, all_units, all_nonjoint_units, all_counts
        )
        selected = dict(select_candidate(search_rows))
        selected_params = {
            key: float(selected[key])
            for key in (
                "congestion_critical_boost",
                "congestion_optional_scale",
                "slack_critical_boost",
            )
        }
    else:
        if not args.frozen_candidate:
            raise ValueError("confirm mode requires --frozen-candidate")
        duration, max_workflows, max_time = 1800, 90, 6000
        selection_matrix = []
        evaluation_matrix = list(h.scenarios("full"))
        evaluation_runs = args.evaluation_runs or 5
        test_seed_base = CONFIRM_TEST_SEED_BASE
        out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / "factorized_signal_confirm_20260730"
        selected = json.loads(Path(args.frozen_candidate).read_text(encoding="utf-8"))
        selected_params = {
            key: float(selected["selected_params"][key])
            for key in (
                "congestion_critical_boost",
                "congestion_optional_scale",
                "slack_critical_boost",
            )
        }
        search_rows = []

    out.mkdir(parents=True, exist_ok=True)
    frozen = {
        "protocol_version": PROTOCOL_VERSION,
        "selected_params": selected_params,
        "selection_seed_base": SELECTION_SEED_BASE if args.mode == "smoke" else selected.get("selection_seed_base"),
        "selection_matrix": selection_matrix if args.mode == "smoke" else selected.get("selection_matrix"),
        "selection_record": selected,
    }
    h.write_json(out / "selected_candidate.json", frozen)
    if search_rows:
        h.write_csv(out / "candidate_search.csv", search_rows)

    print("[test] frozen factorized candidate", flush=True)
    units, nonjoint_units, actions = evaluate_rule(
        selected_params,
        evaluation_matrix,
        evaluation_runs,
        test_seed_base,
        duration,
        max_workflows,
        max_time,
    )
    analysis = analysis_rows(units)
    nonjoint_analysis = analysis_rows(nonjoint_units)
    verdicts = verdict_rows(analysis, nonjoint_analysis, args.mode)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "harness_sha256": h.sha256(Path(__file__).resolve()),
        "selected_params": selected_params,
        "selection_matrix": selection_matrix,
        "selection_seed_base": SELECTION_SEED_BASE if args.mode == "smoke" else None,
        "evaluation_matrix": evaluation_matrix,
        "evaluation_scenarios": len(evaluation_matrix),
        "evaluation_runs": evaluation_runs,
        "test_seed_rule": f"{test_seed_base} + eval_run*10000 + scenario_index",
        "full_action_counts": dict(actions),
        "supported_claims": [row["claim"] for row in verdicts if row["status"] == "supported"],
    }
    h.write_csv(out / "confirmation_units.csv", units)
    h.write_csv(out / "nonjoint_confirmation_units.csv", nonjoint_units)
    h.write_csv(out / "confirmation_analysis.csv", analysis)
    h.write_csv(out / "nonjoint_confirmation_analysis.csv", nonjoint_analysis)
    h.write_csv(out / "claim_verdicts.csv", verdicts)
    h.write_json(out / "run_manifest.json", manifest)
    write_report(out, manifest, analysis, nonjoint_analysis, verdicts)
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
