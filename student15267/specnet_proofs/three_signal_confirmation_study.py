#!/usr/bin/env python3
"""Independent confirmation study for congestion, slack, and pressure.

The original H1-P result remains unchanged.  This protocol freezes the
candidate selected by the earlier pressure-definition audit, replaces the
aliased ``matrix[::3]`` evaluation subset with balanced scenarios, and uses a
quality-safe action family so lower waste cannot be purchased with low
semantic quality.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
    from .pressure_definition_study import PressureSimulator
except ImportError:  # pragma: no cover - direct execution from this directory
    import proof_harness as h
    from pressure_definition_study import PressureSimulator


PROTOCOL_VERSION = "2026-07-30.three-signal-confirmation-v1.1"
PRESSURE_DEFINITION = "active_speculative_backlog"
QUALITY_FLOOR = 0.95
SAFE_ACTIONS = tuple(
    action
    for action in h.up.ACTIONS
    if float(h.up.ACTION_CONFIG[action]["quality_floor"]) >= QUALITY_FLOOR
)
ACTION_PROFILES = {
    "strict_quality": SAFE_ACTIONS,
    "bounded_quality": ("full", "moderate", "recovery"),
    "unrestricted": tuple(h.up.ACTIONS),
}
PRIMARY_SPECS = {
    "H1-C": ("no_congestion", "congestion_bucket", "high", "p99_latency"),
    "H1-S": ("no_slack", "slack_bucket", "tight", "normalized_latency"),
    "H1-P-backlog": ("no_pressure", "spec_pressure_bucket", "high_spec", "waste"),
}
METRICS = (
    "p99_latency",
    "deadline_miss_ratio",
    "waste",
    "quality",
    "normalized_latency",
)
MODE_DEFAULTS = {
    "smoke": {
        "duration": 700,
        "max_workflows": 28,
        "max_time": 2600,
        "train_episodes": 72,
        "train_seeds": (3101, 3102, 3103),
        "eval_runs": 2,
        "eval_scenarios": 12,
        "eval_seed_base": 1_310_000,
    },
    "confirm": {
        "duration": 1800,
        "max_workflows": 90,
        "max_time": 6000,
        "train_episodes": 162,
        "train_seeds": (4101, 4102, 4103, 4104, 4105),
        "eval_runs": 3,
        "eval_scenarios": 27,
        "eval_seed_base": 1_410_000,
    },
}


class QualitySafeBandit(h.AuditedBandit):
    """Bandit restricted by the simulator's static action-quality contract."""

    name = "full"
    allowed_actions = SAFE_ACTIONS

    def decide_action(self, sim, workflow) -> str:
        state = self.state_key(sim, workflow)
        if self.train and self.rng.random() < self.epsilon:
            action = self.rng.choice(self.allowed_actions)
        else:
            values = self.q_values[state]
            action = max(
                self.allowed_actions,
                key=lambda candidate: (
                    values[candidate],
                    -self.allowed_actions.index(candidate),
                ),
            )
        self.action_counter[action] += 1
        workflow.decision_state = state
        return action


def make_policy_class(ablation: str):
    if ablation not in ("full", "no_congestion", "no_slack", "no_pressure"):
        raise ValueError(f"unknown ablation: {ablation}")

    class ThreeSignalBandit(QualitySafeBandit):
        name = ablation

        def state_key(self, sim, workflow):
            congestion = (
                "all_congestion" if ablation == "no_congestion" else sim.congestion_level()
            )
            slack = (
                "all_slack" if ablation == "no_slack" else sim.workflow_slack_bucket(workflow)
            )
            pressure = (
                "all_spec" if ablation == "no_pressure" else sim.pressure_bucket(workflow)
            )
            return (congestion, slack, pressure)

    ThreeSignalBandit.__name__ = f"ThreeSignalBandit_{ablation}"
    return ThreeSignalBandit


POLICY_CLASSES = {
    name: make_policy_class(name)
    for name in ("full", "no_congestion", "no_slack", "no_pressure")
}


def train_policy(
    policy_name: str,
    train_seed: int,
    episodes: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    matrix: Sequence[Tuple[str, float, float, float]],
    allowed_actions: Sequence[str] = SAFE_ACTIONS,
    training_schedule: str = "constant",
):
    policy = POLICY_CLASSES[policy_name](
        seed=train_seed,
        train=True,
        epsilon=0.18,
        learning_rate=0.25,
    )
    policy.allowed_actions = tuple(allowed_actions)
    for episode in range(episodes):
        if training_schedule == "annealed":
            progress = episode / max(1, episodes - 1)
            policy.epsilon = 0.18 + progress * (0.03 - 0.18)
            policy.learning_rate = 0.25 + progress * (0.05 - 0.25)
        elif training_schedule != "constant":
            raise ValueError(f"unknown training schedule: {training_schedule}")
        scenario = matrix[episode % len(matrix)]
        load, deadline_scale, optional_scale, capacity_scale = scenario
        workload_seed = train_seed + 10_000 + episode
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
            pressure_definition=PRESSURE_DEFINITION,
        )
        simulator.run()
    policy.set_evaluation_mode()
    return policy


def run_once(
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Dict[str, object]:
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
        pressure_definition=PRESSURE_DEFINITION,
    )
    summary = simulator.run()
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
        }
    )
    return summary


def _factor_levels(matrix: Sequence[Tuple[str, float, float, float]]) -> List[List[object]]:
    return [list(dict.fromkeys(row[index] for row in matrix)) for index in range(4)]


def _greedy_balanced_subset(
    matrix: Sequence[Tuple[str, float, float, float]],
    count: int,
    seed: int,
) -> List[Tuple[str, float, float, float]]:
    pool = list(matrix)
    levels = _factor_levels(pool)
    rng = random.Random(seed)
    tie_break = {row: rng.random() for row in pool}
    selected: List[Tuple[str, float, float, float]] = []

    def imbalance(candidate: Tuple[str, float, float, float]) -> Tuple[float, float]:
        trial = selected + [candidate]
        size = len(trial)
        score = 0.0
        for index, values in enumerate(levels):
            target = size / len(values)
            counts = Counter(row[index] for row in trial)
            score += sum((counts[value] - target) ** 2 for value in values)
        for left, right in itertools.combinations(range(4), 2):
            target = size / (len(levels[left]) * len(levels[right]))
            counts = Counter((row[left], row[right]) for row in trial)
            score += 0.2 * sum(
                (counts[(a, b)] - target) ** 2
                for a in levels[left]
                for b in levels[right]
            )
        return (round(score, 12), tie_break[candidate])

    while len(selected) < count:
        candidate = min(pool, key=imbalance)
        selected.append(candidate)
        pool.remove(candidate)
    return selected


def balanced_evaluation_matrix(
    matrix: Sequence[Tuple[str, float, float, float]],
    count: int,
    seed: int = 15270,
) -> List[Tuple[str, float, float, float]]:
    """Return a factor-balanced holdout without the old capacity alias."""
    matrix = list(matrix)
    levels = _factor_levels(matrix)
    complete_three_level = (
        len(matrix) == 81
        and [len(values) for values in levels] == [3, 3, 3, 3]
        and set(matrix) == set(itertools.product(*levels))
    )
    if not complete_three_level or count % 9:
        return _greedy_balanced_subset(matrix, count, seed)

    batches: List[List[Tuple[str, float, float, float]]] = []
    for optional_shift, capacity_shift in itertools.product(range(3), repeat=2):
        batch = []
        for load_index, deadline_index in itertools.product(range(3), repeat=2):
            optional_index = (load_index + deadline_index + optional_shift) % 3
            capacity_index = (load_index + 2 * deadline_index + capacity_shift) % 3
            batch.append(
                (
                    levels[0][load_index],
                    levels[1][deadline_index],
                    levels[2][optional_index],
                    levels[3][capacity_index],
                )
            )
        batches.append(batch)
    random.Random(seed).shuffle(batches)
    return list(itertools.chain.from_iterable(batches[: count // 9]))


def save_policy_checkpoint(path: Path, policy, metadata: Mapping[str, object]) -> None:
    states = []
    for state, values in policy.q_values.items():
        states.append(
            {
                "state": list(state),
                "q_values": dict(values),
                "counts": dict(policy.counts.get(state, Counter())),
            }
        )
    h.write_json(path, {"metadata": dict(metadata), "states": states})


def load_policy_checkpoint(
    path: Path,
    policy_name: str,
    train_seed: int,
    protocol_fingerprint: str,
    allowed_actions: Sequence[str] = SAFE_ACTIONS,
):
    payload = json.loads(path.read_text(encoding="utf-8"))
    metadata = payload["metadata"]
    expected = {
        "policy": policy_name,
        "train_seed": train_seed,
        "protocol_fingerprint": protocol_fingerprint,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f"checkpoint mismatch for {key}: {metadata.get(key)!r} != {value!r}")
    policy = POLICY_CLASSES[policy_name](seed=train_seed, train=False, epsilon=0.0)
    policy.allowed_actions = tuple(allowed_actions)
    for row in payload["states"]:
        state = tuple(row["state"])
        policy.q_values[state].update(
            {action: float(value) for action, value in row["q_values"].items()}
        )
        policy.counts[state].update(
            {action: int(value) for action, value in row["counts"].items()}
        )
    policy.set_evaluation_mode()
    return policy


def protocol_fingerprint(protocol: Mapping[str, object]) -> str:
    encoded = json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def paired_slice_units(
    summaries: Mapping[str, Mapping[str, object]],
    replicate: int,
    eval_run: int,
    scenario_index: int,
    workload_seed: int,
) -> List[Dict[str, object]]:
    full_records = list(summaries["full"]["workflow_records"])
    indexed = {
        policy: {int(row["workflow_id"]): row for row in summary["workflow_records"]}
        for policy, summary in summaries.items()
    }
    output: List[Dict[str, object]] = []
    for hypothesis, (ablation, field, target, primary_metric) in PRIMARY_SPECS.items():
        selected_full = [row for row in full_records if row[field] == target]
        selected_ablation = [
            indexed[ablation].get(int(row["workflow_id"])) for row in selected_full
        ]
        selected_ablation = [row for row in selected_ablation if row is not None]
        if not selected_full or len(selected_full) != len(selected_ablation):
            continue
        full_metrics = h.state_metrics(selected_full)
        ablation_metrics = h.state_metrics(selected_ablation)
        common = {
            "hypothesis": hypothesis,
            "ablation": ablation,
            "slice": f"full_reference:{field}={target}",
            "replicate": replicate,
            "eval_run": eval_run,
            "scenario": scenario_index,
            "cell": eval_run * 10_000 + scenario_index,
            "seed": workload_seed,
            "load": summaries["full"]["load"],
            "deadline_scale": summaries["full"]["deadline_scale"],
            "optional_scale": summaries["full"]["optional_scale"],
            "capacity_scale": summaries["full"]["capacity_scale"],
            "paired_workflows": len(selected_full),
            "primary_metric": primary_metric,
            "full_quality": full_metrics["quality"],
            "ablation_quality": ablation_metrics["quality"],
            "both_quality_feasible": int(
                full_metrics["quality"] >= QUALITY_FLOOR
                and ablation_metrics["quality"] >= QUALITY_FLOOR
            ),
        }
        for metric in METRICS:
            common[f"full_{metric}"] = full_metrics[metric]
            common[f"ablation_{metric}"] = ablation_metrics[metric]
            common[f"delta_{metric}"] = ablation_metrics[metric] - full_metrics[metric]
        output.append(common)
    return output


def cluster_bootstrap_ci(
    values: Sequence[Tuple[int, int, float]],
    seed: int,
    draws: int = 4000,
) -> Tuple[float, float]:
    """Resample complete training-policy replicates across fixed test cells."""
    if not values:
        return (math.nan, math.nan)
    by_replicate: Dict[int, Dict[int, float]] = defaultdict(dict)
    for replicate, cell, value in values:
        by_replicate[replicate][cell] = value
    replicates = sorted(by_replicate)
    cells = sorted({cell for _, cell, _ in values})
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        sampled = [rng.choice(replicates) for _ in replicates]
        cell_means = []
        for cell in cells:
            present = [by_replicate[replicate][cell] for replicate in sampled if cell in by_replicate[replicate]]
            if present:
                cell_means.append(statistics.mean(present))
        estimates.append(statistics.mean(cell_means))
    return (h.up.percentile(estimates, 0.025), h.up.percentile(estimates, 0.975))


def cluster_sign_randomization_p(values: Sequence[Tuple[int, int, float]]) -> float:
    """Two-sided exact sign test over independent training replicates."""
    by_replicate: Dict[int, List[float]] = defaultdict(list)
    for replicate, _, value in values:
        by_replicate[replicate].append(value)
    cluster_means = [statistics.mean(by_replicate[key]) for key in sorted(by_replicate)]
    if not cluster_means:
        return math.nan
    observed = abs(statistics.mean(cluster_means))
    extreme = 0
    total = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(cluster_means)):
        estimate = abs(statistics.mean(sign * value for sign, value in zip(signs, cluster_means)))
        extreme += estimate >= observed - 1e-15
        total += 1
    return extreme / total


def analysis_rows(units: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output = []
    for hypothesis, (_, _, _, primary_metric) in PRIMARY_SPECS.items():
        selected = [row for row in units if row["hypothesis"] == hypothesis]
        for metric_index, metric in enumerate(METRICS):
            clustered = [
                (int(row["replicate"]), int(row["cell"]), float(row[f"delta_{metric}"]))
                for row in selected
            ]
            low, high = cluster_bootstrap_ci(
                clustered,
                seed=161_000 + 100 * list(PRIMARY_SPECS).index(hypothesis) + metric_index,
            )
            cell_values = [(int(row["cell"]), float(row[f"delta_{metric}"])) for row in selected]
            is_primary = metric == primary_metric
            output.append(
                {
                    "hypothesis": hypothesis,
                    "ablation": PRIMARY_SPECS[hypothesis][0],
                    "slice": selected[0]["slice"] if selected else "",
                    "metric": metric,
                    "primary_metric": int(is_primary),
                    "paired_units": len(selected),
                    "training_replicates": len({int(row["replicate"]) for row in selected}),
                    "test_cells": len({int(row["cell"]) for row in selected}),
                    "paired_workflows": sum(int(row["paired_workflows"]) for row in selected),
                    "mean_delta_ablation_minus_full": h.stratified_mean(cell_values) if cell_values else math.nan,
                    "ci95_low": low,
                    "ci95_high": high,
                    "cluster_sign_p": cluster_sign_randomization_p(clustered) if is_primary else "",
                    "quality_floor": QUALITY_FLOOR,
                    "mean_full_quality": statistics.mean(float(row["full_quality"]) for row in selected) if selected else math.nan,
                    "mean_ablation_quality": statistics.mean(float(row["ablation_quality"]) for row in selected) if selected else math.nan,
                    "quality_feasible_fraction": statistics.mean(int(row["both_quality_feasible"]) for row in selected) if selected else math.nan,
                }
            )
    return output


def verdict_rows(analysis: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output = []
    for hypothesis in PRIMARY_SPECS:
        row = next(
            item
            for item in analysis
            if item["hypothesis"] == hypothesis and int(item["primary_metric"])
        )
        quality_pass = (
            float(row["mean_full_quality"]) >= QUALITY_FLOOR
            and float(row["mean_ablation_quality"]) >= QUALITY_FLOOR
            and float(row["quality_feasible_fraction"]) >= 0.95
        )
        coverage_pass = int(row["training_replicates"]) >= 3 and int(row["test_cells"]) >= 9
        direction_pass = (
            float(row["mean_delta_ablation_minus_full"]) > 0.0
            and float(row["ci95_low"]) > 0.0
        )
        output.append(
            {
                "claim": hypothesis,
                "status": "supported" if quality_pass and coverage_pass and direction_pass else "not_supported",
                "primary_metric": row["metric"],
                "direction_pass": int(direction_pass),
                "quality_gate_pass": int(quality_pass),
                "coverage_gate_pass": int(coverage_pass),
                "decision_rule": "positive ablation-minus-full mean and cluster-bootstrap CI lower>0; both sides quality>=0.95; feasible fraction>=0.95",
            }
        )
    return output


def policy_summary_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    output = []
    for policy in POLICY_CLASSES:
        selected = [row for row in rows if row["policy"] == policy]
        output.append(
            {
                "policy": policy,
                "evaluation_cells": len(selected),
                "mean_p99_latency": statistics.mean(float(row["p99_latency"]) for row in selected),
                "mean_deadline_miss_ratio": statistics.mean(float(row["deadline_miss_ratio"]) for row in selected),
                "mean_waste": statistics.mean(float(row["waste"]) for row in selected),
                "mean_quality": statistics.mean(float(row["quality"]) for row in selected),
                "mean_normalized_latency": statistics.mean(float(row["normalized_latency"]) for row in selected),
            }
        )
    return output


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    analysis: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
) -> None:
    primary = {
        str(row["hypothesis"]): row for row in analysis if int(row["primary_metric"])
    }
    verdict = {str(row["claim"]): row for row in verdicts}
    lines = [
        "# 三参数独立确认实验报告",
        "",
        "本实验确认修改后的三信号模型，不改写旧预注册结果：原始 ratio pressure 的 H1-P 仍为不支持；这里单独检验先前候选审计冻结的 active speculative backlog。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- 模式：`{manifest['mode']}`",
        f"- 压力定义：`{manifest['pressure_definition']}`，冻结阈值 `0.15 / 0.35`",
        f"- 控制器 profile：`{manifest['controller_profile']}`；训练日程：`{manifest['training_schedule']}`；质量门槛：`{manifest['quality_floor']}`；允许动作：`{manifest['allowed_actions']}`",
        f"- 允许动作的静态质量下界：`{manifest['allowed_action_quality_floors']}`",
        f"- 训练 seeds：`{manifest['train_seeds']}`；评估 seed 规则：`{manifest['eval_seed_rule']}`",
        f"- 场景：{manifest['eval_scenarios']} 个平衡场景 × {manifest['eval_runs']} 个独立 workload runs",
        "- 统计单位：完整训练 replicate cluster；同一策略的多个场景不当作独立训练样本。",
        "",
        "## 主结果",
        "",
        "正值表示移除对应参数后变差。判定要求主指标方向为正、cluster-bootstrap 95% CI 下界大于 0，并同时通过质量和覆盖门。",
        "",
        "| 假设 | 主指标 | delta | 95% CI | full quality | ablation quality | 质量可行比例 | 判定 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for hypothesis in PRIMARY_SPECS:
        row = primary[hypothesis]
        lines.append(
            f"| {hypothesis} | {row['metric']} | {float(row['mean_delta_ablation_minus_full']):.4f} | "
            f"[{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] | "
            f"{float(row['mean_full_quality']):.4f} | {float(row['mean_ablation_quality']):.4f} | "
            f"{float(row['quality_feasible_fraction']):.3f} | {verdict[hypothesis]['status']} |"
        )
    lines += [
        "",
        "## 创新点",
        "",
        "1. 将 spec pressure 从全局占比改为容量归一化的 active speculative backlog，使信号直接表示可争用、可浪费的绝对在途负载。",
        "2. 将动作集及其静态质量下界写入冻结清单，并对每个配对切片执行独立的 0.95 经验质量门，避免把低质量运行混入质量约束结论。",
        "3. 用平衡正交场景替代旧 `matrix[::3]` 容量混杂子集，并在 full-reference 状态切片上匹配相同 workflow ID。",
        "4. 以独立训练 replicate 为 cluster bootstrap 单位，避免把同一张 Q 表下的多个场景伪装成独立样本。",
        "5. 提供协议指纹与逐策略 checkpoint，训练中断后可恢复且不会混用不同代码或参数生成的策略。",
        "",
        "## 解释边界",
        "",
        "- 旧 H1-P（original ratio）结论不变；只有 H1-P-backlog 可依据本目录结果单独判定。",
        "- Smoke 只验证机制、覆盖和方向，不形成论文级确认；confirm 模式才是冻结协议的独立复核。",
        "- 本实验仍是 trace-driven simulator 证据，不能外推为真实网络、任意 reward 或任意 workload 上的普适结论。",
        "- exact cluster sign p 受训练 replicate 数限制，仅作诊断；正式判定使用预先冻结的 cluster-bootstrap CI 和质量门。",
        "",
        "## 展望",
        "",
        "- 在不查看 confirm test 的前提下，将相同定义迁移到真实 trace 或 packet-level 仿真，检验 backlog 阈值的外部有效性。",
        "- 把固定 `0.15/0.35` 阈值替换为仅用训练集估计的容量分位数，再用新的独立 confirmation split 复核校准稳定性。",
        "- 增加 7–10 个训练 replicate，以支持更有分辨率的 cluster randomization inference 和跨 seed 稳定性分析。",
        "- 对负载漂移、容量估计误差和 pressure 观测延迟做敏感性分析，报告结论失效边界。",
        "",
        "逐单元结果见 `confirmation_units.csv`，完整指标见 `confirmation_analysis.csv`，自动判定见 `claim_verdicts.csv`。",
    ]
    (out / "THREE_SIGNAL_CONFIRMATION_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "confirm"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--controller-profile",
        choices=tuple(ACTION_PROFILES),
        default="strict_quality",
    )
    parser.add_argument(
        "--training-schedule",
        choices=("constant", "annealed"),
        default="constant",
    )
    parser.add_argument("--train-episodes", type=int, default=None)
    parser.add_argument("--train-replicates", type=int, default=None)
    parser.add_argument("--eval-runs", type=int, default=None)
    parser.add_argument("--eval-scenarios", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    defaults = dict(MODE_DEFAULTS[args.mode])
    if args.train_episodes is not None:
        defaults["train_episodes"] = args.train_episodes
    if args.train_replicates is not None:
        defaults["train_seeds"] = tuple(defaults["train_seeds"][: args.train_replicates])
    if args.eval_runs is not None:
        defaults["eval_runs"] = args.eval_runs
    if args.eval_scenarios is not None:
        defaults["eval_scenarios"] = args.eval_scenarios
    if not defaults["train_seeds"]:
        raise ValueError("at least one training replicate is required")
    allowed_actions = ACTION_PROFILES[args.controller_profile]

    out = (
        Path(args.output_dir)
        if args.output_dir
        else h.ROOT / "results" / f"three_signal_{args.mode}_v1_20260730"
    )
    out.mkdir(parents=True, exist_ok=True)
    matrix = h.scenarios("smoke" if args.mode == "smoke" else "full")
    evaluation_matrix = balanced_evaluation_matrix(
        matrix,
        int(defaults["eval_scenarios"]),
    )
    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "pressure_definition": PRESSURE_DEFINITION,
        "pressure_thresholds": [0.15, 0.35],
        "quality_floor": QUALITY_FLOOR,
        "controller_profile": args.controller_profile,
        "training_schedule": args.training_schedule,
        "allowed_actions": list(allowed_actions),
        "allowed_action_quality_floors": {
            action: h.up.ACTION_CONFIG[action]["quality_floor"]
            for action in allowed_actions
        },
        "duration": defaults["duration"],
        "max_workflows": defaults["max_workflows"],
        "max_time": defaults["max_time"],
        "train_episodes": defaults["train_episodes"],
        "train_seeds": list(defaults["train_seeds"]),
        "training_matrix": matrix,
        "eval_runs": defaults["eval_runs"],
        "eval_scenarios": len(evaluation_matrix),
        "evaluation_matrix": evaluation_matrix,
        "eval_seed_base": defaults["eval_seed_base"],
        "eval_seed_rule": f"{defaults['eval_seed_base']} + eval_run*10000 + scenario_index",
        "policies": list(POLICY_CLASSES),
        "primary_specs": PRIMARY_SPECS,
        "bootstrap_unit": "training_replicate_cluster",
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "harness_sha256": h.sha256(Path(__file__).resolve()),
    }
    fingerprint = protocol_fingerprint(protocol)
    protocol["protocol_fingerprint"] = fingerprint
    manifest_path = out / "run_manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("protocol_fingerprint") != fingerprint:
            raise ValueError("output directory contains a different frozen protocol")
    h.write_json(manifest_path, protocol)

    checkpoint_dir = out / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    trained: Dict[Tuple[int, str], object] = {}
    for replicate, train_seed in enumerate(defaults["train_seeds"]):
        for policy_name in POLICY_CLASSES:
            checkpoint = checkpoint_dir / f"{policy_name}_{train_seed}.json"
            if checkpoint.exists():
                print(f"[checkpoint] loading {policy_name} seed={train_seed}", flush=True)
                policy = load_policy_checkpoint(
                    checkpoint,
                    policy_name,
                    int(train_seed),
                    fingerprint,
                    allowed_actions,
                )
            else:
                print(f"[train] {policy_name} seed={train_seed}", flush=True)
                policy = train_policy(
                    policy_name,
                    int(train_seed),
                    int(defaults["train_episodes"]),
                    int(defaults["duration"]),
                    int(defaults["max_workflows"]),
                    int(defaults["max_time"]),
                    matrix,
                    allowed_actions,
                    args.training_schedule,
                )
                save_policy_checkpoint(
                    checkpoint,
                    policy,
                    {
                        "policy": policy_name,
                        "train_seed": int(train_seed),
                        "replicate": replicate,
                        "protocol_fingerprint": fingerprint,
                    },
                )
            trained[(replicate, policy_name)] = policy

    units: List[Dict[str, object]] = []
    policy_rows: List[Dict[str, object]] = []
    action_rows: List[Dict[str, object]] = []
    coverage: Counter[Tuple[str, str, str]] = Counter()
    for replicate, train_seed in enumerate(defaults["train_seeds"]):
        for eval_run in range(int(defaults["eval_runs"])):
            for scenario_index, scenario in enumerate(evaluation_matrix):
                workload_seed = (
                    int(defaults["eval_seed_base"])
                    + eval_run * 10_000
                    + scenario_index
                )
                summaries = {
                    policy_name: run_once(
                        trained[(replicate, policy_name)],
                        scenario,
                        workload_seed,
                        int(defaults["duration"]),
                        int(defaults["max_workflows"]),
                        int(defaults["max_time"]),
                    )
                    for policy_name in POLICY_CLASSES
                }
                units.extend(
                    paired_slice_units(
                        summaries,
                        replicate,
                        eval_run,
                        scenario_index,
                        workload_seed,
                    )
                )
                for policy_name, summary in summaries.items():
                    metrics = h.state_metrics(summary["workflow_records"])
                    policy_rows.append(
                        {
                            "policy": policy_name,
                            "replicate": replicate,
                            "train_seed": train_seed,
                            "eval_run": eval_run,
                            "scenario": scenario_index,
                            "seed": workload_seed,
                            "load": scenario[0],
                            "deadline_scale": scenario[1],
                            "optional_scale": scenario[2],
                            "capacity_scale": scenario[3],
                            **metrics,
                        }
                    )
                    for action, count in summary["action_counts"].items():
                        action_rows.append(
                            {
                                "policy": policy_name,
                                "replicate": replicate,
                                "eval_run": eval_run,
                                "scenario": scenario_index,
                                "action": action,
                                "count": count,
                            }
                        )
                for row in summaries["full"]["workflow_records"]:
                    coverage[
                        (
                            str(row["congestion_bucket"]),
                            str(row["slack_bucket"]),
                            str(row["spec_pressure_bucket"]),
                        )
                    ] += 1
        print(f"[evaluation] replicate {replicate + 1}/{len(defaults['train_seeds'])}", flush=True)

    analysis = analysis_rows(units)
    verdicts = verdict_rows(analysis)
    h.write_csv(out / "confirmation_units.csv", units)
    h.write_csv(out / "confirmation_analysis.csv", analysis)
    h.write_csv(out / "claim_verdicts.csv", verdicts)
    h.write_csv(out / "policy_cells.csv", policy_rows)
    h.write_csv(out / "policy_summary.csv", policy_summary_rows(policy_rows))
    h.write_csv(out / "action_counts.csv", action_rows)
    h.write_csv(
        out / "state_coverage.csv",
        [
            {
                "congestion_bucket": state[0],
                "slack_bucket": state[1],
                "spec_pressure_bucket": state[2],
                "visit_count": count,
            }
            for state, count in sorted(coverage.items())
        ],
    )
    protocol["completed"] = True
    protocol["supported_claims"] = [
        row["claim"] for row in verdicts if row["status"] == "supported"
    ]
    protocol["upstream_sha256_after"] = h.sha256(h.UPSTREAM_PATH)
    protocol["upstream_unchanged"] = protocol["upstream_sha256_after"] == protocol["upstream_sha256"]
    h.write_json(manifest_path, protocol)
    write_report(out, protocol, analysis, verdicts)
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
