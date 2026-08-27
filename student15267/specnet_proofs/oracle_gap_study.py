#!/usr/bin/env python3
"""Measure the per-workflow counterfactual gap to the best action.

The oracle here is deliberately local: other workflows keep the frozen
controller, while one target workflow is replayed with each possible action.
This makes the gap interpretable without claiming a globally optimal policy.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
except ImportError:  # pragma: no cover
    import proof_harness as h


class CounterfactualPolicy(h.up.CriticalPathOnlyPolicy):
    """Use a frozen base policy except for one target workflow."""

    def __init__(self, base_policy, target_workflow_id: int, target_action: str, seed: int = 0):
        super().__init__(seed=seed)
        self.base_policy = base_policy
        self.target_workflow_id = target_workflow_id
        self.target_action = target_action
        self.name = f"counterfactual_{target_workflow_id}_{target_action}"

    def reset_for_run(self) -> None:
        super().reset_for_run()
        self.base_policy.reset_for_run()

    def flow_weight(self, flow, sim):
        return self.base_policy.flow_weight(flow, sim)

    def decide_action(self, sim, workflow):
        if workflow.spec.workflow_id == self.target_workflow_id:
            self.action_counter[self.target_action] += 1
            workflow.decision_state = sim.observable_state(workflow)
            return self.target_action
        return self.base_policy.decide_action(sim, workflow)


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
        workload_seed, load, duration, max_workflows, deadline_scale, optional_scale
    )
    simulator = h.ProofSimulator(
        specs,
        policy,
        load,
        workload_seed,
        duration,
        max_time,
        capacity_scale=capacity_scale,
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


def target_counterfactuals(
    base_policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> List[Dict[str, object]]:
    baseline = run_once(base_policy, scenario, workload_seed, duration, max_workflows, max_time)
    baseline_records = {
        int(row["workflow_id"]): row for row in baseline["workflow_records"]
    }
    output = []
    for workflow_id, baseline_record in sorted(baseline_records.items()):
        action_rewards: Dict[str, float] = {}
        action_metrics: Dict[str, Mapping[str, object]] = {}
        for action in h.up.ACTIONS:
            counterfactual_policy = CounterfactualPolicy(
                base_policy, workflow_id, action, seed=workload_seed
            )
            counterfactual = run_once(
                counterfactual_policy,
                scenario,
                workload_seed,
                duration,
                max_workflows,
                max_time,
            )
            record = next(
                row
                for row in counterfactual["workflow_records"]
                if int(row["workflow_id"]) == workflow_id
            )
            action_rewards[action] = float(record["reward"])
            action_metrics[action] = record
        oracle_action = max(
            h.up.ACTIONS,
            key=lambda action: (action_rewards[action], -h.up.ACTIONS.index(action)),
        )
        output.append(
            {
                "workflow_id": workflow_id,
                "baseline_action": baseline_record["action"],
                "baseline_reward": float(baseline_record["reward"]),
                "oracle_action": oracle_action,
                "oracle_reward": action_rewards[oracle_action],
                "oracle_gap": action_rewards[oracle_action] - float(baseline_record["reward"]),
                "baseline_quality": float(baseline_record["quality"]),
                "oracle_quality": float(action_metrics[oracle_action]["quality"]),
                "baseline_latency": float(baseline_record["latency"]),
                "oracle_latency": float(action_metrics[oracle_action]["latency"]),
                "baseline_waste": float(baseline_record["wasted_speculative_bytes"]),
                "oracle_waste": float(action_metrics[oracle_action]["wasted_speculative_bytes"]),
                **{f"reward_{action}": action_rewards[action] for action in h.up.ACTIONS},
                "load": baseline["load"],
                "deadline_scale": baseline["deadline_scale"],
                "optional_scale": baseline["optional_scale"],
                "capacity_scale": baseline["capacity_scale"],
                "workload_seed": workload_seed,
            }
        )
    return output


def summary_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, int], List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["load"]), int(row["scenario"]))].append(row)
    output = []
    for (load, scenario), items in sorted(grouped.items()):
        gaps = [float(row["oracle_gap"]) for row in items]
        output.append(
            {
                "load": load,
                "scenario": scenario,
                "workflows": len(items),
                "mean_oracle_gap": statistics.mean(gaps),
                "median_oracle_gap": statistics.median(gaps),
                "positive_gap_fraction": statistics.mean(gap > 1e-9 for gap in gaps),
                "mean_baseline_reward": statistics.mean(float(row["baseline_reward"]) for row in items),
                "mean_oracle_reward": statistics.mean(float(row["oracle_reward"]) for row in items),
                "mean_baseline_quality": statistics.mean(float(row["baseline_quality"]) for row in items),
                "mean_oracle_quality": statistics.mean(float(row["oracle_quality"]) for row in items),
                "mean_baseline_latency": statistics.mean(float(row["baseline_latency"]) for row in items),
                "mean_oracle_latency": statistics.mean(float(row["oracle_latency"]) for row in items),
                "mean_baseline_waste": statistics.mean(float(row["baseline_waste"]) for row in items),
                "mean_oracle_waste": statistics.mean(float(row["oracle_waste"]) for row in items),
            }
        )
    return output


def overall_summary(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    if not rows:
        return []
    gaps = [float(row["oracle_gap"]) for row in rows]
    action_counts = Counter(str(row["oracle_action"]) for row in rows)
    baseline_counts = Counter(str(row["baseline_action"]) for row in rows)
    return [
        {
            "workflows": len(rows),
            "mean_oracle_gap": statistics.mean(gaps),
            "median_oracle_gap": statistics.median(gaps),
            "p90_oracle_gap": h.up.percentile(gaps, 0.90),
            "positive_gap_fraction": statistics.mean(gap > 1e-9 for gap in gaps),
            "oracle_action_counts": dict(action_counts),
            "baseline_action_counts": dict(baseline_counts),
            "mean_baseline_quality": statistics.mean(float(row["baseline_quality"]) for row in rows),
            "mean_oracle_quality": statistics.mean(float(row["oracle_quality"]) for row in rows),
            "mean_baseline_latency": statistics.mean(float(row["baseline_latency"]) for row in rows),
            "mean_oracle_latency": statistics.mean(float(row["oracle_latency"]) for row in rows),
            "mean_baseline_waste": statistics.mean(float(row["baseline_waste"]) for row in rows),
            "mean_oracle_waste": statistics.mean(float(row["oracle_waste"]) for row in rows),
        }
    ]


def write_report(out: Path, manifest: Mapping[str, object], overall: Mapping[str, object]) -> None:
    lines = [
        "# Per-workflow oracle gap study",
        "",
        "这里的 oracle 不是一个可以在线部署的神谕，而是固定其他 workflow 的动作，只把目标 workflow 依次改成五个动作，选出其 reward 最高者。它回答的是：当前状态和训练后的 bandit，距离这个 workload 上可见的最好动作还有多大差距。",
        "",
        f"- 模式：{manifest['mode']}",
        f"- 场景数：{manifest['eval_scenarios']}",
        f"- 每个场景预算：duration={manifest['duration']}，max_workflows={manifest['max_workflows']}，max_time={manifest['max_time']}",
        f"- 试验动作：{', '.join(h.up.ACTIONS)}",
        "",
        "## 总体结果",
        "",
        f"共分析 {int(overall['workflows'])} 个 workflow；平均 oracle gap={float(overall['mean_oracle_gap']):.4f}，中位数={float(overall['median_oracle_gap']):.4f}，90 分位={float(overall['p90_oracle_gap']):.4f}，有正 gap 的比例={float(overall['positive_gap_fraction']):.3f}。",
        f"baseline 平均 quality/latency/waste={float(overall['mean_baseline_quality']):.3f}/{float(overall['mean_baseline_latency']):.2f}/{float(overall['mean_baseline_waste']):.2f}；oracle 对应为 {float(overall['mean_oracle_quality']):.3f}/{float(overall['mean_oracle_latency']):.2f}/{float(overall['mean_oracle_waste']):.2f}。",
        "",
        "## 解释边界",
        "",
        "gap 大说明状态表示、Q 表训练或 reward 仍有改进空间，不等于 oracle 能在真实系统中提前知道未来。每个 counterfactual 会重新 replay 同一 workload；它也没有证明全局最优，更没有替代真实语义质量评估。",
    ]
    (out / "ORACLE_GAP_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--eval-runs", type=int, default=None)
    parser.add_argument("--eval-scenarios", type=int, default=None)
    parser.add_argument("--max-workflows", type=int, default=None)
    parser.add_argument("--duration", type=int, default=None)
    parser.add_argument("--max-time", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "smoke":
        duration, max_workflows, max_time = 500, 12, 1800
        eval_runs, eval_scenarios, episodes = 1, 4, 24
    else:
        duration, max_workflows, max_time = 1200, 24, 4000
        eval_runs, eval_scenarios, episodes = 2, 6, 48
    if args.eval_runs is not None:
        eval_runs = args.eval_runs
    if args.eval_scenarios is not None:
        eval_scenarios = args.eval_scenarios
    if args.max_workflows is not None:
        max_workflows = args.max_workflows
    if args.duration is not None:
        duration = args.duration
    if args.max_time is not None:
        max_time = args.max_time
    out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / f"oracle_gap_{args.mode}_20260723"
    out.mkdir(parents=True, exist_ok=True)
    matrix = h.scenarios(args.mode)
    eval_matrix = matrix[:: max(1, len(matrix) // max(1, eval_scenarios))][:eval_scenarios]
    manifest = {
        "study_version": "2026-07-23.oracle-gap.v1",
        "mode": args.mode,
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "eval_runs": eval_runs,
        "eval_scenarios": len(eval_matrix),
        "duration": duration,
        "max_workflows": max_workflows,
        "max_time": max_time,
        "episodes": episodes,
        "actions": list(h.up.ACTIONS),
        "oracle_scope": "one target workflow counterfactual; other workflows use frozen base policy",
    }
    h.write_json(out / "run_manifest.json", manifest)
    rows: List[Dict[str, object]] = []
    for run in range(eval_runs):
        policy = h.train_bandit(
            h.AuditedBandit,
            7 + run * 101,
            episodes,
            duration,
            max_workflows,
            max_time,
            matrix,
        )
        for scenario_index, scenario in enumerate(eval_matrix):
            workload_seed = 930000 + run * 1000 + scenario_index
            for row in target_counterfactuals(
                policy, scenario, workload_seed, duration, max_workflows, max_time
            ):
                row["run"] = run
                row["scenario"] = scenario_index
                rows.append(row)
        print(f"[oracle] run={run} completed", flush=True)
    h.write_csv(out / "oracle_workflow_rows.csv", rows)
    h.write_csv(out / "oracle_scenario_summary.csv", summary_rows(rows))
    overall = overall_summary(rows)[0] if rows else {}
    h.write_json(out / "oracle_overall.json", overall)
    write_report(out, manifest, overall)
    print(f"[done] results written to {out}", flush=True)


if __name__ == "__main__":
    main()
