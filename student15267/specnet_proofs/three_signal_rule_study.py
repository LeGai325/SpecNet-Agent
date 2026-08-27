#!/usr/bin/env python3
"""Select and independently confirm a monotone three-signal risk rule."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .pressure_definition_study import PressureSimulator
    from .three_signal_confirmation_study import (
        METRICS,
        PRIMARY_SPECS,
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
        paired_slice_units,
        run_once,
    )
except ImportError:  # pragma: no cover - direct execution from this directory
    import proof_harness as h
    from pressure_definition_study import PressureSimulator
    from three_signal_confirmation_study import (
        METRICS,
        PRIMARY_SPECS,
        QUALITY_FLOOR,
        balanced_evaluation_matrix,
        paired_slice_units,
        run_once,
    )


PROTOCOL_VERSION = "2026-07-30.monotone-three-signal-rule-v2.2"
PRESSURE_DEFINITION = "active_speculative_backlog"
RULE_ACTIONS = ("full", "recovery")
NEUTRAL_LEVEL = 0.5
SELECTION_SEED_BASE = 1_610_000
SMOKE_TEST_SEED_BASE = 1_630_000
CONFIRM_TEST_SEED_BASE = 1_710_000
REPLICATION_TEST_SEED_BASE = 1_810_000
CONDITIONAL_TEST_SEED_BASE = 1_910_000


class MonotoneRiskRule(h.up.CriticalPathOnlyPolicy):
    """Two-action rule whose risk is monotone in all three signals."""

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

    def risk_components(self, sim, workflow) -> Tuple[float, float, float]:
        congestion = {"low": 0.0, "medium": 0.5, "high": 1.0}[
            sim.congestion_level()
        ]
        slack = {"loose": 0.0, "normal": 0.5, "tight": 1.0}[
            sim.workflow_slack_bucket(workflow)
        ]
        pressure = {"low_spec": 0.0, "mid_spec": 0.5, "high_spec": 1.0}[
            sim.pressure_bucket(workflow)
        ]
        if self.ablation == "no_congestion":
            congestion = NEUTRAL_LEVEL
        elif self.ablation == "no_slack":
            slack = NEUTRAL_LEVEL
        elif self.ablation == "no_pressure":
            pressure = NEUTRAL_LEVEL
        return congestion, slack, pressure

    def decide_action(self, sim, workflow) -> str:
        congestion, slack, pressure = self.risk_components(sim, workflow)
        risk = (
            self.params["wc"] * congestion
            + self.params["ws"] * slack
            + self.params["wp"] * pressure
        )
        action = "recovery" if risk >= self.params["threshold"] else "full"
        self.action_counter[action] += 1
        workflow.decision_state = (
            sim.congestion_level(),
            sim.workflow_slack_bucket(workflow),
            sim.pressure_bucket(workflow),
        )
        return action


def candidate_rules() -> List[Dict[str, float]]:
    profiles = (
        (1.0, 1.0, 1.0),
        (1.2, 1.0, 1.0),
        (1.0, 1.2, 1.0),
        (1.0, 1.0, 1.2),
        (1.1, 1.1, 1.3),
    )
    fractions = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
    output = []
    for wc, ws, wp in profiles:
        maximum = wc + ws + wp
        for fraction in fractions:
            output.append(
                {
                    "wc": wc,
                    "ws": ws,
                    "wp": wp,
                    "threshold": maximum * fraction,
                }
            )
    return output


def pivotal_state_audit(params: Mapping[str, float]) -> Dict[str, int]:
    """Count states where one high signal matters without both peers high."""
    weights = (params["wc"], params["ws"], params["wp"])
    output: Dict[str, int] = {}
    names = ("congestion", "slack", "pressure")
    for signal_index, name in enumerate(names):
        pivotal = 0
        nonjoint = 0
        for peers in itertools.product((0.0, 0.5, 1.0), repeat=2):
            full_state = list(peers)
            full_state.insert(signal_index, 1.0)
            ablated_state = list(full_state)
            ablated_state[signal_index] = NEUTRAL_LEVEL
            full_risk = sum(weight * value for weight, value in zip(weights, full_state))
            ablated_risk = sum(weight * value for weight, value in zip(weights, ablated_state))
            changed = (
                full_risk >= params["threshold"]
                and ablated_risk < params["threshold"]
            )
            pivotal += int(changed)
            nonjoint += int(changed and peers != (1.0, 1.0))
        output[f"{name}_pivotal_states"] = pivotal
        output[f"{name}_nonjoint_pivotal_states"] = nonjoint
    return output


def disjoint_balanced_matrices(
    matrix: Sequence[Tuple[str, float, float, float]],
    count: int,
) -> Tuple[List[Tuple[str, float, float, float]], List[Tuple[str, float, float, float]]]:
    selection = balanced_evaluation_matrix(matrix, count, seed=16267)
    remaining = [scenario for scenario in matrix if scenario not in set(selection)]
    evaluation = balanced_evaluation_matrix(remaining, count, seed=16268)
    return selection, evaluation


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
                policy_name: MonotoneRiskRule(params, policy_name)
                for policy_name in ("full", "no_congestion", "no_slack", "no_pressure")
            }
            summaries = {
                policy_name: run_once(
                    policy,
                    scenario,
                    workload_seed,
                    duration,
                    max_workflows,
                    max_time,
                )
                for policy_name, policy in policies.items()
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
            for action, count_value in summaries["full"]["action_counts"].items():
                actions[action] += int(count_value)
    return units, nonjoint_units, actions


def paired_nonjoint_units(
    summaries: Mapping[str, Mapping[str, object]],
    eval_run: int,
    scenario_index: int,
    workload_seed: int,
) -> List[Dict[str, object]]:
    """Exclude cells where both peer signals are simultaneously maximal."""
    exclusions = {
        "H1-C": lambda row: row["slack_bucket"] == "tight" and row["spec_pressure_bucket"] == "high_spec",
        "H1-S": lambda row: row["congestion_bucket"] == "high" and row["spec_pressure_bucket"] == "high_spec",
        "H1-P-backlog": lambda row: row["congestion_bucket"] == "high" and row["slack_bucket"] == "tight",
    }
    full_records = list(summaries["full"]["workflow_records"])
    indexed = {
        policy: {int(row["workflow_id"]): row for row in summary["workflow_records"]}
        for policy, summary in summaries.items()
    }
    output = []
    for hypothesis, (ablation, field, target, primary_metric) in PRIMARY_SPECS.items():
        full_slice = [
            row for row in full_records
            if row[field] == target and not exclusions[hypothesis](row)
        ]
        ablation_slice = [
            indexed[ablation].get(int(row["workflow_id"])) for row in full_slice
        ]
        ablation_slice = [row for row in ablation_slice if row is not None]
        if not full_slice or len(full_slice) != len(ablation_slice):
            continue
        full_metrics = h.state_metrics(full_slice)
        ablation_metrics = h.state_metrics(ablation_slice)
        row: Dict[str, object] = {
            "hypothesis": hypothesis,
            "ablation": ablation,
            "slice": f"nonjoint_full_reference:{field}={target}",
            "replicate": 0,
            "eval_run": eval_run,
            "scenario": scenario_index,
            "cell": eval_run * 10_000 + scenario_index,
            "seed": workload_seed,
            "load": summaries["full"]["load"],
            "deadline_scale": summaries["full"]["deadline_scale"],
            "optional_scale": summaries["full"]["optional_scale"],
            "capacity_scale": summaries["full"]["capacity_scale"],
            "paired_workflows": len(full_slice),
            "primary_metric": primary_metric,
            "full_quality": full_metrics["quality"],
            "ablation_quality": ablation_metrics["quality"],
            "both_quality_feasible": int(
                full_metrics["quality"] >= QUALITY_FLOOR
                and ablation_metrics["quality"] >= QUALITY_FLOOR
            ),
        }
        for metric in METRICS:
            row[f"full_{metric}"] = full_metrics[metric]
            row[f"ablation_{metric}"] = ablation_metrics[metric]
            row[f"delta_{metric}"] = ablation_metrics[metric] - full_metrics[metric]
        output.append(row)
    return output


def _primary_means(units: Sequence[Mapping[str, object]]) -> Dict[str, float]:
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
        means = _primary_means(units)
        nonjoint_means = _primary_means(nonjoint_units)
        selected_by_hypothesis = {
            hypothesis: [row for row in units if row["hypothesis"] == hypothesis]
            for hypothesis in PRIMARY_SPECS
        }
        scales = {}
        for hypothesis, (_, _, _, metric) in PRIMARY_SPECS.items():
            selected = selected_by_hypothesis[hypothesis]
            scales[hypothesis] = max(
                1e-9,
                statistics.mean(abs(float(row[f"full_{metric}"])) for row in selected),
            ) if selected else math.nan
        normalized = {
            hypothesis: means[hypothesis] / scales[hypothesis]
            for hypothesis in PRIMARY_SPECS
        }
        nonjoint_selected = {
            hypothesis: [row for row in nonjoint_units if row["hypothesis"] == hypothesis]
            for hypothesis in PRIMARY_SPECS
        }
        nonjoint_normalized = {}
        for hypothesis, (_, _, _, metric) in PRIMARY_SPECS.items():
            selected = nonjoint_selected[hypothesis]
            scale = max(
                1e-9,
                statistics.mean(abs(float(row[f"full_{metric}"])) for row in selected),
            ) if selected else math.nan
            nonjoint_normalized[hypothesis] = nonjoint_means[hypothesis] / scale
        quality_fraction = statistics.mean(
            int(row["both_quality_feasible"]) for row in units
        ) if units else 0.0
        adaptive = counts["full"] > 0 and counts["recovery"] > 0
        pivotal = pivotal_state_audit(params)
        output.append(
            {
                "candidate_id": candidate_id,
                **params,
                "full_actions": counts["full"],
                "recovery_actions": counts["recovery"],
                "adaptive": int(adaptive),
                "quality_feasible_fraction": quality_fraction,
                "positive_primary_count": sum(value > 0 for value in means.values()),
                "positive_nonjoint_primary_count": sum(
                    value > 0 for value in nonjoint_means.values()
                ),
                "minimum_normalized_effect": min(normalized.values()),
                "mean_normalized_effect": statistics.mean(normalized.values()),
                "minimum_nonjoint_normalized_effect": min(nonjoint_normalized.values()),
                **pivotal,
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
        and int(row["positive_nonjoint_primary_count"]) == len(PRIMARY_SPECS)
        and min(
            int(row[f"{name}_nonjoint_pivotal_states"])
            for name in ("congestion", "slack", "pressure")
        ) >= 1
    ]
    if not feasible:
        raise ValueError("candidate search produced no adaptive quality-feasible rule")
    return max(
        feasible,
        key=lambda row: (
            int(row["positive_primary_count"]),
            int(row["positive_nonjoint_primary_count"]),
            float(row["minimum_nonjoint_normalized_effect"]),
            float(row["minimum_normalized_effect"]),
            float(row["mean_normalized_effect"]),
            -int(row["candidate_id"]),
        ),
    )


def analysis_rows(units: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output = []
    primary_p: Dict[str, float] = {}
    for hypothesis, (_, _, _, primary_metric) in PRIMARY_SPECS.items():
        selected = [row for row in units if row["hypothesis"] == hypothesis]
        for metric_index, metric in enumerate(METRICS):
            values = [
                (int(row["scenario"]), float(row[f"delta_{metric}"]))
                for row in selected
            ]
            low, high = h.stratified_bootstrap_ci(
                values,
                seed=172_000 + 100 * list(PRIMARY_SPECS).index(hypothesis) + metric_index,
            ) if values else (math.nan, math.nan)
            is_primary = metric == primary_metric
            p_value = (
                h.stratified_randomization_p(values, seed=173_000 + len(output))
                if is_primary and values
                else ""
            )
            if is_primary:
                primary_p[hypothesis] = float(p_value)
            output.append(
                {
                    "hypothesis": hypothesis,
                    "metric": metric,
                    "primary_metric": int(is_primary),
                    "paired_units": len(selected),
                    "scenario_strata": len({int(row["scenario"]) for row in selected}),
                    "paired_workflows": sum(int(row["paired_workflows"]) for row in selected),
                    "mean_delta_ablation_minus_full": h.stratified_mean(values) if values else math.nan,
                    "ci95_low": low,
                    "ci95_high": high,
                    "randomization_p": p_value,
                    "holm_adjusted_p": "",
                    "mean_full_quality": statistics.mean(float(row["full_quality"]) for row in selected) if selected else math.nan,
                    "mean_ablation_quality": statistics.mean(float(row["ablation_quality"]) for row in selected) if selected else math.nan,
                    "quality_feasible_fraction": statistics.mean(int(row["both_quality_feasible"]) for row in selected) if selected else math.nan,
                }
            )
    adjusted = h.holm_adjust(primary_p)
    for row in output:
        if int(row["primary_metric"]):
            row["holm_adjusted_p"] = adjusted[str(row["hypothesis"])]
    return output


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
        nonjoint_row = next(
            item for item in nonjoint_analysis
            if item["hypothesis"] == hypothesis and int(item["primary_metric"])
        )
        direction = (
            float(row["mean_delta_ablation_minus_full"]) > 0
            and float(row["ci95_low"]) > 0
            and float(row["holm_adjusted_p"]) < 0.05
        )
        quality = (
            float(row["mean_full_quality"]) >= QUALITY_FLOOR
            and float(row["mean_ablation_quality"]) >= QUALITY_FLOOR
            and float(row["quality_feasible_fraction"]) >= 0.95
        )
        nonjoint_direction = (
            float(nonjoint_row["mean_delta_ablation_minus_full"]) > 0
            and float(nonjoint_row["ci95_low"]) > 0
            and float(nonjoint_row["holm_adjusted_p"]) < 0.05
        )
        nonjoint_quality = (
            float(nonjoint_row["mean_full_quality"]) >= QUALITY_FLOOR
            and float(nonjoint_row["mean_ablation_quality"]) >= QUALITY_FLOOR
            and float(nonjoint_row["quality_feasible_fraction"]) >= 0.95
        )
        coverage = int(row["scenario_strata"]) >= (6 if mode == "smoke" else 18)
        nonjoint_coverage = int(nonjoint_row["scenario_strata"]) >= (4 if mode == "smoke" else 12)
        conditional_mode = mode == "conditional"
        supported = (
            nonjoint_direction and nonjoint_quality and nonjoint_coverage
            if conditional_mode
            else direction and quality and coverage and nonjoint_direction and nonjoint_quality and nonjoint_coverage
        )
        output.append(
            {
                "claim": hypothesis,
                "status": "supported_in_identifiable_context" if supported and conditional_mode else "supported" if supported else "not_supported",
                "decision_estimand": "nonjoint_high_signal_slice" if conditional_mode else "broad_and_nonjoint_slices",
                "direction_pass": int(direction),
                "quality_gate_pass": int(quality),
                "coverage_gate_pass": int(coverage),
                "nonjoint_direction_pass": int(nonjoint_direction),
                "nonjoint_quality_gate_pass": int(nonjoint_quality),
                "nonjoint_coverage_gate_pass": int(nonjoint_coverage),
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
    primary = {
        str(row["hypothesis"]): row for row in analysis if int(row["primary_metric"])
    }
    nonjoint_primary = {
        str(row["hypothesis"]): row
        for row in nonjoint_analysis
        if int(row["primary_metric"])
    }
    conditional_mode = manifest["mode"] == "conditional"
    decision_primary = nonjoint_primary if conditional_mode else primary
    verdict = {str(row["claim"]): row for row in verdicts}
    lines = [
        "# 单调三参数规则独立实验",
        "",
        "该实验是学习型 bandit 结果不稳定后的独立、可审计替代路线。规则只使用 full/recovery，因此每个动作的静态质量下界至少为 0.98。候选仅在开发集选择；确认集参数和 workload seeds 不参与选择。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 模式：`{manifest['mode']}`",
        f"- 证据阶段：`{manifest['evidence_phase']}`",
        f"- pressure：`{PRESSURE_DEFINITION}`，阈值 `0.15/0.35`",
        f"- 冻结规则：`{manifest['selected_params']}`",
        f"- 无信号替代值：`{NEUTRAL_LEVEL}`（middle bucket）",
        f"- 评估：{manifest['evaluation_scenarios']} 场景 × {manifest['evaluation_runs']} runs；seed 规则 `{manifest['test_seed_rule']}`",
        "",
        "## 主结果",
        "",
        "正值表示移除相应信号后变差。自动判定同时要求：delta>0、95% CI 下界>0、Holm-adjusted p<0.05、两侧质量均达到0.95、质量可行单元比例≥0.95。conditional 模式预先冻结非联合最高切片为主估计量；broad slack 负结果保留为诊断。",
        "",
        "| 假设 | 指标 | delta | 95% CI | Holm p | full Q | ablation Q | 可行比例 | 判定 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for hypothesis in PRIMARY_SPECS:
        row = decision_primary[hypothesis]
        lines.append(
            f"| {hypothesis} | {row['metric']} | {float(row['mean_delta_ablation_minus_full']):.4f} | "
            f"[{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] | "
            f"{float(row['holm_adjusted_p']):.4g} | {float(row['mean_full_quality']):.4f} | "
            f"{float(row['mean_ablation_quality']):.4f} | {float(row['quality_feasible_fraction']):.3f} | "
            f"{verdict[hypothesis]['status']} |"
        )
    diagnostic_primary = primary if conditional_mode else nonjoint_primary
    lines += [
        "",
        "### Broad 切片诊断" if conditional_mode else "### 非联合最高切片",
        "",
        "conditional 模式下本表保留 broad 结果但不作为新条件假设的判定门。" if conditional_mode else "为避免三项结果完全由三信号同时最高的 workflow 驱动，下面排除另外两项同时为最高的记录；该表也必须通过方向、CI、Holm、质量与覆盖门。",
        "",
        "| 假设 | 指标 | delta | 95% CI | Holm p | 场景层 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for hypothesis in PRIMARY_SPECS:
        row = diagnostic_primary[hypothesis]
        lines.append(
            f"| {hypothesis} | {row['metric']} | {float(row['mean_delta_ablation_minus_full']):.4f} | "
            f"[{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] | "
            f"{float(row['holm_adjusted_p']):.4g} | {row['scenario_strata']} |"
        )
    lines += [
        "",
        "## 创新点",
        "",
        "1. 使用容量归一化 active speculative backlog 代替原始 speculative ratio，直接观测绝对在途浪费风险。",
        "2. 将三信号压缩为非负加权的单调风险分数，并增加非联合 pivotal-state 与实证切片门，避免把三重交互误写成三个独立主效应。",
        "3. 只使用 full/recovery 两个质量下界≥0.98的动作，从机制上排除低质量换取指标改善。",
        "4. 候选搜索与确认 holdout 严格分离，并对三个主假设执行 Holm 多重比较校正。",
        "5. full-reference 切片匹配相同 workflow ID，所有负结果和质量权衡均保留。",
        "",
        "## 解释边界与展望",
        "",
        "- 该结论证明冻结规则在当前模拟器中利用了三项信号，不等于证明学习型 Q 表跨 seed 稳定；后者的开发实验仍为负。replication 模式是首次 confirm 后增加的高功效复核，必须与首次结果并列报告。",
        "- 原始 ratio H1-P 仍不支持；H1-P-backlog 是修改后的假设，不能事后改写旧预注册结论。broad H1-S 同样仍不支持；conditional 模式只确认新定义的可辨识条件效应。",
        "- 下一步应在真实 trace、容量估计误差和观测延迟下复核，并用新的外部数据冻结 neutral bucket 和 backlog 阈值。",
        "- 可进一步比较连续风险分数、校准概率模型与当前离散规则，但任何新选择都需要新的独立 holdout。",
        "",
        "详见 `confirmation_units.csv`、`nonjoint_confirmation_units.csv`、两份 analysis CSV 与 `claim_verdicts.csv`。",
    ]
    (out / "THREE_SIGNAL_RULE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "confirm", "replication", "conditional"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--frozen-candidate", default=None)
    parser.add_argument("--selection-runs", type=int, default=1)
    parser.add_argument("--evaluation-runs", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "smoke":
        duration, max_workflows, max_time = 700, 28, 2600
        matrix = h.scenarios("smoke")
        selection_matrix, evaluation_matrix = disjoint_balanced_matrices(matrix, 12)
        evaluation_runs = args.evaluation_runs or 3
        out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / "three_signal_rule_smoke_20260730"
        candidates = candidate_rules()
        all_units = []
        all_nonjoint_units = []
        all_counts = []
        for candidate_id, params in enumerate(candidates):
            print(f"[select] candidate {candidate_id + 1}/{len(candidates)}", flush=True)
            units, nonjoint_units, counts = evaluate_rule(
                params,
                selection_matrix,
                args.selection_runs,
                SELECTION_SEED_BASE,
                duration,
                max_workflows,
                max_time,
            )
            all_units.append(units)
            all_nonjoint_units.append(nonjoint_units)
            all_counts.append(counts)
        search_rows = candidate_score_rows(
            candidates,
            all_units,
            all_nonjoint_units,
            all_counts,
        )
        selected = dict(select_candidate(search_rows))
        selected_params = {
            key: float(selected[key]) for key in ("wc", "ws", "wp", "threshold")
        }
        test_seed_base = SMOKE_TEST_SEED_BASE
    else:
        if not args.frozen_candidate:
            raise ValueError("confirmation modes require --frozen-candidate from smoke selection")
        duration, max_workflows, max_time = 1800, 90, 6000
        matrix = h.scenarios("full")
        selection_matrix = []
        if args.mode == "confirm":
            evaluation_matrix = balanced_evaluation_matrix(matrix, 27, seed=17267)
            evaluation_runs = args.evaluation_runs or 3
            test_seed_base = CONFIRM_TEST_SEED_BASE
            default_directory = "three_signal_rule_confirm_20260730"
        elif args.mode == "replication":
            evaluation_matrix = list(matrix)
            evaluation_runs = args.evaluation_runs or 5
            test_seed_base = REPLICATION_TEST_SEED_BASE
            default_directory = "three_signal_rule_replication_20260730"
        else:
            evaluation_matrix = list(matrix)
            evaluation_runs = args.evaluation_runs or 5
            test_seed_base = CONDITIONAL_TEST_SEED_BASE
            default_directory = "three_signal_rule_conditional_20260730"
        out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / default_directory
        selected = json.loads(Path(args.frozen_candidate).read_text(encoding="utf-8"))
        selected_params = {
            key: float(selected["selected_params"][key])
            for key in ("wc", "ws", "wp", "threshold")
        }
        search_rows = []

    out.mkdir(parents=True, exist_ok=True)
    frozen = {
        "protocol_version": PROTOCOL_VERSION,
        "source_mode": args.mode,
        "selected_params": selected_params,
        "selection_seed_base": SELECTION_SEED_BASE if args.mode == "smoke" else selected.get("selection_seed_base"),
        "selection_matrix": selection_matrix if args.mode == "smoke" else selected.get("selection_matrix"),
        "selection_record": selected,
    }
    h.write_json(out / "selected_candidate.json", frozen)
    if search_rows:
        h.write_csv(out / "candidate_search.csv", search_rows)

    print("[test] frozen candidate on independent holdout", flush=True)
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
        "evidence_phase": (
            "development_holdout"
            if args.mode == "smoke"
            else "frozen_initial_confirmation"
            if args.mode == "confirm"
            else "post_confirmation_high_power_replication"
            if args.mode == "replication"
            else "independent_confirmation_of_posthoc_conditional_estimand"
        ),
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "rule_harness_sha256": h.sha256(Path(__file__).resolve()),
        "pressure_definition": PRESSURE_DEFINITION,
        "pressure_thresholds": [0.15, 0.35],
        "quality_floor": QUALITY_FLOOR,
        "rule_actions": RULE_ACTIONS,
        "action_quality_floors": {
            action: h.up.ACTION_CONFIG[action]["quality_floor"] for action in RULE_ACTIONS
        },
        "neutral_level": NEUTRAL_LEVEL,
        "selected_params": selected_params,
        "pivotal_state_audit": pivotal_state_audit(selected_params),
        "selection_scenarios": len(selection_matrix),
        "selection_runs": args.selection_runs if args.mode == "smoke" else 0,
        "evaluation_matrix": evaluation_matrix,
        "evaluation_scenarios": len(evaluation_matrix),
        "evaluation_runs": evaluation_runs,
        "test_seed_rule": f"{test_seed_base} + eval_run*10000 + scenario_index",
        "full_action_counts": dict(actions),
        "supported_claims": [row["claim"] for row in verdicts if str(row["status"]).startswith("supported")],
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
