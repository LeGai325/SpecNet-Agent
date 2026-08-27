#!/usr/bin/env python3
"""Separate source admission control from queue scheduling.

The original simulator lets an action change both speculative fanout and
background traffic, while the queue policy controls bandwidth allocation.
This experiment fixes one dimension at a time and reports the resulting
factorial comparison.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from . import proof_harness as h
except ImportError:  # pragma: no cover
    import proof_harness as h


SOURCE_ACTIONS = ("full", "moderate", "conservative", "critical_only", "recovery")
QUEUE_POLICIES = {
    "fifo": h.up.FIFOPolicy,
    "static_priority": h.up.StaticPriorityPolicy,
    "critical_path": h.up.CriticalPathOnlyPolicy,
}


class IsolationSimulator(h.ProofSimulator):
    """Add source-volume accounting to the read-only simulator."""

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        by_id = {workflow.spec.workflow_id: workflow for workflow in self.completed_workflows}
        generated_speculative = []
        generated_background = []
        for record in result["workflow_records"]:
            workflow = by_id[record["workflow_id"]]
            speculative_bytes = sum(
                self.flows[flow_id].size for flow_id in workflow.speculative_branch_flows
            )
            background_bytes = sum(self.flows[flow_id].size for flow_id in workflow.background_flows)
            record["generated_speculative_bytes"] = speculative_bytes
            record["generated_background_bytes"] = background_bytes
            generated_speculative.append(speculative_bytes)
            generated_background.append(background_bytes)
        result["generated_speculative_bytes_per_workflow"] = statistics.mean(generated_speculative) if generated_speculative else 0.0
        result["generated_background_bytes_per_workflow"] = statistics.mean(generated_background) if generated_background else 0.0
        return result


def make_fixed_source_policy(source_action: str, queue_name: str):
    if source_action not in SOURCE_ACTIONS:
        raise ValueError(f"unknown source action: {source_action}")
    queue_class = QUEUE_POLICIES[queue_name]

    class FixedSourcePolicy(queue_class):
        name = f"fixed_{source_action}_{queue_name}"

        def decide_action(self, sim, workflow):
            self.action_counter[source_action] += 1
            workflow.decision_state = sim.observable_state(workflow)
            return source_action

    FixedSourcePolicy.__name__ = f"FixedSource_{source_action}_{queue_name}"
    return FixedSourcePolicy


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
    simulator = IsolationSimulator(
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


def record_rows(
    summary: Mapping[str, object],
    policy_name: str,
    run: int,
    scenario: int,
    train_seed: int,
) -> List[Dict[str, object]]:
    rows = []
    for record in summary["workflow_records"]:
        rows.append(
            {
                **record,
                "policy": policy_name,
                "run": run,
                "scenario": scenario,
                "train_seed": train_seed,
                "load": summary["load"],
                "deadline_scale": summary["deadline_scale"],
                "optional_scale": summary["optional_scale"],
                "capacity_scale": summary["capacity_scale"],
            }
        )
    return rows


def aggregate_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, int, int], List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["policy"]), int(row["run"]), int(row["scenario"]))].append(row)
    output = []
    for (policy, run, scenario), items in sorted(grouped.items()):
        metrics = h.state_metrics(items)
        output.append(
            {
                "policy": policy,
                "run": run,
                "scenario": scenario,
                "load": items[0]["load"],
                "deadline_scale": items[0]["deadline_scale"],
                "optional_scale": items[0]["optional_scale"],
                "capacity_scale": items[0]["capacity_scale"],
                "completed": metrics["n"],
                "p99_latency": metrics["p99_latency"],
                "deadline_miss_ratio": metrics["deadline_miss_ratio"],
                "waste": metrics["waste"],
                "quality": metrics["quality"],
                "normalized_latency": metrics["normalized_latency"],
                "generated_speculative_bytes": statistics.mean(float(row["generated_speculative_bytes"]) for row in items),
                "generated_background_bytes": statistics.mean(float(row["generated_background_bytes"]) for row in items),
                "background_bytes_served": statistics.mean(float(row["background_bytes_served"]) for row in items),
            }
        )
    return output


def stratified_policy_summary(aggregate: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    fields = (
        "p99_latency",
        "deadline_miss_ratio",
        "waste",
        "quality",
        "normalized_latency",
        "generated_speculative_bytes",
        "generated_background_bytes",
    )
    policies = sorted({str(row["policy"]) for row in aggregate})
    output = []
    for policy in policies:
        selected = [row for row in aggregate if row["policy"] == policy]
        result = {
            "policy": policy,
            "units": len(selected),
            "scenario_strata": len({int(row["scenario"]) for row in selected}),
            "load_strata": len({str(row["load"]) for row in selected}),
        }
        for field in fields:
            result[f"mean_{field}"] = statistics.mean(float(row[field]) for row in selected)
        result["quality_feasible_fraction"] = statistics.mean(float(row["quality"]) >= 0.95 for row in selected)
        output.append(result)
    return output


def paired_deltas(aggregate: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    indexed = {
        (str(row["policy"]), int(row["run"]), int(row["scenario"])): row for row in aggregate
    }
    keys = sorted({(int(row["run"]), int(row["scenario"])) for row in aggregate})
    output = []
    for run, scenario in keys:
        critical = indexed.get(("fixed_full_critical_path", run, scenario))
        if critical is None:
            continue
        for source_action in SOURCE_ACTIONS:
            source = indexed.get((f"fixed_{source_action}_critical_path", run, scenario))
            if source is None:
                continue
            output.append(
                {
                    "comparison": f"source_{source_action}_minus_full",
                    "run": run,
                    "scenario": scenario,
                    "load": source["load"],
                    "source_action": source_action,
                    "queue_policy": "critical_path",
                    "delta_p99_latency": float(source["p99_latency"]) - float(critical["p99_latency"]),
                    "delta_deadline_miss_ratio": float(source["deadline_miss_ratio"]) - float(critical["deadline_miss_ratio"]),
                    "delta_waste": float(source["waste"]) - float(critical["waste"]),
                    "delta_quality": float(source["quality"]) - float(critical["quality"]),
                    "delta_generated_speculative_bytes": float(source["generated_speculative_bytes"]) - float(critical["generated_speculative_bytes"]),
                    "delta_generated_background_bytes": float(source["generated_background_bytes"]) - float(critical["generated_background_bytes"]),
                }
            )
        for source_action in SOURCE_ACTIONS:
            baseline = indexed.get((f"fixed_{source_action}_critical_path", run, scenario))
            if baseline is None:
                continue
            for queue_name in QUEUE_POLICIES:
                candidate = indexed.get((f"fixed_{source_action}_{queue_name}", run, scenario))
                if candidate is None:
                    continue
                output.append(
                    {
                        "comparison": f"queue_{queue_name}_minus_critical_path",
                        "run": run,
                        "scenario": scenario,
                        "load": candidate["load"],
                        "source_action": source_action,
                        "queue_policy": queue_name,
                        "delta_p99_latency": float(candidate["p99_latency"]) - float(baseline["p99_latency"]),
                        "delta_deadline_miss_ratio": float(candidate["deadline_miss_ratio"]) - float(baseline["deadline_miss_ratio"]),
                        "delta_waste": float(candidate["waste"]) - float(baseline["waste"]),
                        "delta_quality": float(candidate["quality"]) - float(baseline["quality"]),
                        "delta_generated_speculative_bytes": float(candidate["generated_speculative_bytes"]) - float(baseline["generated_speculative_bytes"]),
                        "delta_generated_background_bytes": float(candidate["generated_background_bytes"]) - float(baseline["generated_background_bytes"]),
                    }
                )
    return output


def write_report(
    out: Path,
    manifest: Mapping[str, object],
    aggregate: Sequence[Mapping[str, object]],
    deltas: Sequence[Mapping[str, object]],
) -> None:
    critical = [row for row in aggregate if row["policy"] == "fixed_full_critical_path"]
    source_deltas = [
        row
        for row in deltas
        if str(row["comparison"]).startswith("source_") and row["source_action"] != "full"
    ]
    queue_deltas = [
        row
        for row in deltas
        if row["source_action"] == "full" and row["queue_policy"] != "critical_path"
    ]

    def mean_delta(items: Sequence[Mapping[str, object]], field: str) -> float:
        return statistics.mean(float(row[field]) for row in items) if items else math.nan

    lines = [
        "# Source-control isolation study",
        "",
        "本实验把两个可能混在一起的机制拆开：源端 admission 决定生成多少 speculative/background 流，队列 scheduler 决定已有流如何分配带宽。固定同一个工作负载并做交叉组合后，才能判断收益来自少生成流量，还是来自优先服务关键流。",
        "",
        f"- 模式：{manifest['mode']}",
        f"- 评估 runs：{manifest['eval_runs']}",
        f"- 评估场景：{manifest['eval_scenarios']}",
        f"- 每个场景预算：duration={manifest['duration']}，max_workflows={manifest['max_workflows']}，max_time={manifest['max_time']}",
        f"- 固定源端动作：{', '.join(SOURCE_ACTIONS)}",
        f"- 队列策略：{', '.join(QUEUE_POLICIES)}",
        "",
        "## 如何读结果",
        "",
        "`generated_*` 是源端实际生成的流量；`waste` 是完成关键路径后已发送但被取消的 speculative 字节。源端动作之间主要看这两列，队列策略之间主要看 p99 和 deadline miss。",
        "",
        "## 本次结果",
        "",
        f"full 源端 + critical_path 队列基线：p99={mean_delta(critical, 'p99_latency'):.2f}，deadline miss={mean_delta(critical, 'deadline_miss_ratio'):.3f}，waste={mean_delta(critical, 'waste'):.2f}，quality={mean_delta(critical, 'quality'):.3f}。",
        "",
        "在同一 critical_path 队列下，改变源端动作的平均增量（相对 full）：",
        "",
        "| 源端动作 | p99 增量 | waste 增量 | quality 增量 | speculative 生成量增量 |",
        "|---|---:|---:|---:|---:|",
    ]
    for source_action in ("moderate", "conservative", "critical_only", "recovery"):
        selected = [row for row in source_deltas if row["source_action"] == source_action]
        lines.append(
            f"| {source_action} | {mean_delta(selected, 'delta_p99_latency'):.2f} | "
            f"{mean_delta(selected, 'delta_waste'):.2f} | {mean_delta(selected, 'delta_quality'):.3f} | "
            f"{mean_delta(selected, 'delta_generated_speculative_bytes'):.2f} |"
        )
    lines += [
        "",
        "在 full 源端下，改变队列策略的平均增量（相对 critical_path）：",
        "",
        "| 队列策略 | p99 增量 | deadline miss 增量 | waste 增量 |",
        "|---|---:|---:|---:|",
    ]
    for queue_name in ("fifo", "static_priority"):
        selected = [row for row in queue_deltas if row["queue_policy"] == queue_name]
        lines.append(
            f"| {queue_name} | {mean_delta(selected, 'delta_p99_latency'):.2f} | "
            f"{mean_delta(selected, 'delta_deadline_miss_ratio'):.3f} | {mean_delta(selected, 'delta_waste'):.2f} |"
        )
    lines += [
        "",
        "## 重要边界",
        "",
        "质量仍是模拟器中的分支数量代理，不是真实答案正确率；本实验也不等价于真实网络部署。只有在同一源端动作下比较队列策略，或在同一队列策略下比较源端动作，才可解释对应机制的增量。",
        "",
        f"共得到 {len(aggregate)} 个 run-scenario-policy 聚合单元；full+critical_path 基线包含 {len(critical)} 个单元。",
    ]
    (out / "SOURCE_CONTROL_ISOLATION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--eval-runs", type=int, default=None)
    parser.add_argument("--train-seeds", type=int, default=None)
    parser.add_argument("--eval-scenarios", type=int, default=None)
    parser.add_argument("--duration", type=int, default=None)
    parser.add_argument("--max-workflows", type=int, default=None)
    parser.add_argument("--max-time", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "smoke":
        duration, max_workflows, max_time = 700, 28, 2600
        eval_runs, train_seed_count = 2, 1
    else:
        duration, max_workflows, max_time = 1800, 90, 6000
        eval_runs, train_seed_count = 3, 2
    if args.eval_runs is not None:
        eval_runs = args.eval_runs
    if args.train_seeds is not None:
        train_seed_count = args.train_seeds
    if args.duration is not None:
        duration = args.duration
    if args.max_workflows is not None:
        max_workflows = args.max_workflows
    if args.max_time is not None:
        max_time = args.max_time
    out = Path(args.output_dir) if args.output_dir else h.ROOT / "results" / f"source_control_isolation_{args.mode}_20260723"
    out.mkdir(parents=True, exist_ok=True)
    matrix = h.scenarios(args.mode)
    eval_matrix = matrix if args.mode == "smoke" else matrix[::3]
    if args.eval_scenarios is not None:
        eval_matrix = eval_matrix[: args.eval_scenarios]
    manifest = {
        "study_version": "2026-07-23.source-control-isolation.v1",
        "mode": args.mode,
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "source_actions": list(SOURCE_ACTIONS),
        "queue_policies": list(QUEUE_POLICIES),
        "eval_runs": eval_runs,
        "eval_scenarios": len(eval_matrix),
        "duration": duration,
        "max_workflows": max_workflows,
        "max_time": max_time,
        "train_seeds": list(range(7, 7 + train_seed_count)),
    }
    h.write_json(out / "run_manifest.json", manifest)

    all_rows: List[Dict[str, object]] = []
    for run in range(eval_runs):
        for scenario_index, scenario in enumerate(eval_matrix):
            workload_seed = 910000 + run * 1000 + scenario_index
            for source_action in SOURCE_ACTIONS:
                for queue_name in QUEUE_POLICIES:
                    policy_class = make_fixed_source_policy(source_action, queue_name)
                    policy = policy_class(seed=workload_seed)
                    summary = run_once(policy, scenario, workload_seed, duration, max_workflows, max_time)
                    all_rows.extend(record_rows(summary, policy.name, run, scenario_index, -1))
    adaptive_rows: List[Dict[str, object]] = []
    adaptive_class = h.AuditedBandit
    for train_seed in manifest["train_seeds"]:
        policy = h.train_bandit(adaptive_class, int(train_seed), 36 if args.mode == "smoke" else 72, duration, max_workflows, max_time, matrix)
        for run in range(eval_runs):
            for scenario_index, scenario in enumerate(eval_matrix):
                workload_seed = 910000 + run * 1000 + scenario_index
                summary = run_once(policy, scenario, workload_seed, duration, max_workflows, max_time)
                adaptive_rows.extend(record_rows(summary, "specnet_adaptive", run, scenario_index, int(train_seed)))
    all_rows.extend(adaptive_rows)
    aggregate = aggregate_rows(all_rows)
    h.write_csv(out / "source_control_workflow_audit.csv", all_rows)
    h.write_csv(out / "source_control_aggregate.csv", aggregate)
    h.write_csv(out / "source_control_policy_summary.csv", stratified_policy_summary(aggregate))
    h.write_csv(out / "source_control_paired_deltas.csv", paired_deltas(aggregate))
    write_report(out, manifest, aggregate, paired_deltas(aggregate))
    print(f"[done] results written to {out}", flush=True)


if __name__ == "__main__":
    main()
