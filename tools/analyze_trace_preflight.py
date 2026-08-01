#!/usr/bin/env python3
"""Summarize paired trace-workload smoke or Pilot outputs without pandas."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


LOADS = ("light", "medium", "heavy")
SLACK_BUCKETS = ("loose", "normal", "tight")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def ratio_counts(values: list[str]) -> dict[str, dict[str, float | int]]:
    counts = Counter(values)
    total = len(values)
    return {
        key: {
            "count": count,
            "ratio": count / total if total else 0.0,
        }
        for key, count in sorted(counts.items())
    }


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def numeric_distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": statistics.mean(values) if values else None,
        "sample_std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def summarize_policy_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_load: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_load[row["load"]].append(row)

    loads = {}
    for load in LOADS:
        items = by_load.get(load, [])
        buckets = {}
        for bucket in SLACK_BUCKETS:
            bucket_rows = [
                row for row in items if row["decision_slack_bucket"] == bucket
            ]
            buckets[bucket] = {
                "count": len(bucket_rows),
                "ratio": len(bucket_rows) / len(items) if items else 0.0,
                "deadline_miss_ratio": (
                    sum(int(row["deadline_miss"]) for row in bucket_rows)
                    / len(bucket_rows)
                    if bucket_rows
                    else None
                ),
            }
        observed_risk = [
            buckets[bucket]["deadline_miss_ratio"]
            for bucket in SLACK_BUCKETS
        ]
        risk_order_holds = (
            all(value is not None for value in observed_risk)
            and observed_risk[0] <= observed_risk[1] <= observed_risk[2]
        )
        latencies = [float(row["latency"]) for row in items]
        loads[load] = {
            "workflows": len(items),
            "pooled_workflow_p99_latency": percentile(latencies, 0.99),
            "deadline_miss_ratio": (
                sum(int(row["deadline_miss"]) for row in items) / len(items)
                if items
                else None
            ),
            "avg_quality": (
                sum(float(row["quality"]) for row in items) / len(items)
                if items
                else None
            ),
            "action_mix": ratio_counts([row["action"] for row in items]),
            "slack_buckets": buckets,
            "slack_risk_order_holds": risk_order_holds,
        }
    return {"loads": loads}


def summarize_reported_metrics(
    rows: list[dict[str, str]],
) -> dict[str, dict[str, float]]:
    """Read the experiment runner's aggregate metrics.

    In particular, ``p99_latency`` is the mean of the per-run P99 values.  It
    intentionally differs from the P99 obtained after pooling all workflow
    samples, which remains available in the raw-workflow summary.
    """
    by_load = {row["load"]: row for row in rows}
    metrics = (
        "mean_latency",
        "p95_latency",
        "p99_latency",
        "deadline_miss_ratio",
        "wasted_speculative_bytes_per_workflow",
        "avg_quality",
    )
    return {
        load: {metric: float(by_load[load][metric]) for metric in metrics}
        for load in LOADS
    }


def summarize_training_states(model: dict[str, Any]) -> dict[str, Any]:
    state_features = list(model.get("state_features") or [])
    counts = model.get("counts") or {}
    if "slack" not in state_features or not isinstance(counts, dict):
        return {"available": False, "reason": "slack_not_in_controller_state"}
    slack_index = state_features.index("slack")
    bucket_visits: Counter[str] = Counter()
    for state_text, action_counts in counts.items():
        state = ast.literal_eval(state_text)
        if not isinstance(state, tuple) or slack_index >= len(state):
            raise ValueError(f"invalid serialized controller state: {state_text}")
        visits = sum(int(value) for value in action_counts.values())
        bucket_visits[str(state[slack_index])] += visits
    total = sum(bucket_visits.values())
    return {
        "available": True,
        "total_state_action_visits": total,
        "slack_bucket_visits": {
            bucket: {
                "count": bucket_visits[bucket],
                "ratio": bucket_visits[bucket] / total if total else 0.0,
            }
            for bucket in SLACK_BUCKETS
        },
        "all_slack_buckets_visited": all(
            bucket_visits[bucket] > 0 for bucket in SLACK_BUCKETS
        ),
    }


def summarize_source_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["load"], row["record_source"])].append(row)
    result = {}
    for (load, source), items in sorted(grouped.items()):
        latencies = [float(row["latency"]) for row in items]
        result[f"{load}/{source}"] = {
            "workflows": len(items),
            "pooled_workflow_p99_latency": percentile(latencies, 0.99),
            "deadline_miss_ratio": (
                sum(int(row["deadline_miss"]) for row in items) / len(items)
            ),
            "avg_quality": (
                sum(float(row["quality"]) for row in items) / len(items)
            ),
        }
    return result


def aggregate_policy_summaries(
    policies: dict[str, dict[str, Any]],
    learned_rows: list[dict[str, str]],
) -> dict[str, Any]:
    load_metrics = {}
    for load in LOADS:
        load_metrics[load] = {
            metric: numeric_distribution(
                [
                    float(policy["reported_metrics"][load][metric])
                    for policy in policies.values()
                ]
            )
            for metric in ("p99_latency", "deadline_miss_ratio", "avg_quality")
        }

    combined_slack = {}
    for load in LOADS:
        load_rows = [row for row in learned_rows if row["load"] == load]
        buckets = {}
        for bucket in SLACK_BUCKETS:
            bucket_rows = [
                row
                for row in load_rows
                if row["decision_slack_bucket"] == bucket
            ]
            buckets[bucket] = {
                "count": len(bucket_rows),
                "ratio": len(bucket_rows) / len(load_rows) if load_rows else 0.0,
                "deadline_miss_ratio": (
                    sum(int(row["deadline_miss"]) for row in bucket_rows)
                    / len(bucket_rows)
                    if bucket_rows
                    else None
                ),
            }
        risks = [buckets[bucket]["deadline_miss_ratio"] for bucket in SLACK_BUCKETS]
        combined_slack[load] = {
            "buckets": buckets,
            "risk_order_holds": (
                all(value is not None for value in risks)
                and risks[0] <= risks[1] <= risks[2]
            ),
        }

    training_coverages = [
        policy["training_state_coverage"] for policy in policies.values()
    ]
    action_monopolies = []
    for policy_name, policy in policies.items():
        for load in LOADS:
            action_mix = policy["loads"][load]["action_mix"]
            max_ratio = max(
                (float(item["ratio"]) for item in action_mix.values()),
                default=0.0,
            )
            if max_ratio >= 0.95:
                action_monopolies.append(
                    {"policy": policy_name, "load": load, "ratio": max_ratio}
                )

    pressure_gradient_holds = all(
        all(
            float(policy["reported_metrics"][left][metric])
            <= float(policy["reported_metrics"][right][metric])
            for left, right in zip(LOADS, LOADS[1:])
            for metric in ("p99_latency", "deadline_miss_ratio")
        )
        for policy in policies.values()
    )
    return {
        "load_metrics_across_training_seeds": load_metrics,
        "combined_slack_risk": combined_slack,
        "source_metrics": summarize_source_metrics(learned_rows),
        "checks": {
            "all_training_seeds_visit_all_slack_buckets": all(
                bool(item.get("all_slack_buckets_visited"))
                for item in training_coverages
            ),
            "all_training_seeds_have_at_least_5pct_tight": all(
                float(item["slack_bucket_visits"]["tight"]["ratio"]) >= 0.05
                for item in training_coverages
            ),
            "heavy_slack_risk_order_holds_for_every_seed": all(
                bool(policy["loads"]["heavy"]["slack_risk_order_holds"])
                for policy in policies.values()
            ),
            "medium_combined_slack_risk_order_holds": bool(
                combined_slack["medium"]["risk_order_holds"]
            ),
            "pressure_gradient_holds_for_every_seed": pressure_gradient_holds,
            "no_95pct_action_monopoly": not action_monopolies,
        },
        "action_monopolies": action_monopolies,
    }


def summarize_run(name: str, root: Path, policy_prefix: str) -> dict[str, Any]:
    workflow_rows = read_csv(root / "workflow_results.csv")
    aggregate_rows = read_csv(root / "summary_aggregate.csv")
    model_payload = json.loads(
        (root / "specnet_agent_model.json").read_text(encoding="utf-8")
    )
    learned_rows = [
        row for row in workflow_rows if row["policy"].startswith(policy_prefix)
    ]
    if not learned_rows:
        raise ValueError(f"{name}: no policy matching {policy_prefix}")

    unique_workloads: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in learned_rows:
        key = (row["load"], row["run"], row["seed"], row["workflow_id"])
        unique_workloads.setdefault(key, row)

    policy_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in learned_rows:
        policy_rows[row["policy"]].append(row)
    reported_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in aggregate_rows:
        if row["policy"].startswith(policy_prefix):
            reported_rows[row["policy"]].append(row)

    unique = list(unique_workloads.values())
    expected_eval_split = all(row["source_split"] == "test" for row in unique)
    policies = {}
    for policy, rows in sorted(policy_rows.items()):
        model = model_payload["policies"][policy]["model"]
        policies[policy] = {
            **summarize_policy_rows(rows),
            "reported_metrics": summarize_reported_metrics(
                reported_rows[policy]
            ),
            "training_state_coverage": summarize_training_states(model),
            "selected_checkpoint_episode": model.get(
                "selected_checkpoint_episode"
            ),
            "selected_checkpoint_constraint_feasible": (
                model.get("training_info", {}).get(
                    "selected_checkpoint_constraint_feasible"
                )
            ),
        }

    record_sources = ratio_counts([row["record_source"] for row in unique])
    source_mix_check = True
    if "ragpulse" in record_sources:
        source_mix_check = (
            abs(float(record_sources["ragpulse"]["ratio"]) - 0.25) <= 0.02
        )
    aggregate = aggregate_policy_summaries(policies, learned_rows)
    aggregate["checks"].update(
        {
            "evaluation_split_is_test_only": expected_eval_split,
            "record_source_mix_within_2pct": source_mix_check,
        }
    )

    return {
        "name": name,
        "root": str(root),
        "workload_profile": unique[0]["workload_profile"],
        "unique_evaluation_workflows": len(unique),
        "record_sources": record_sources,
        "workload_modes": ratio_counts([row["workload_source"] for row in unique]),
        "templates": ratio_counts([row["template"] for row in unique]),
        "mapping_versions": ratio_counts(
            [row["mapping_version"] for row in unique]
        ),
        "evaluation_split_is_test_only": expected_eval_split,
        "policies": policies,
        "aggregate": aggregate,
    }


def parse_input(text: str) -> tuple[str, Path]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("input must use NAME=OUTPUT_DIR")
    name, path = text.split("=", 1)
    if not name or not path:
        raise argparse.ArgumentTypeError("input must use NAME=OUTPUT_DIR")
    return name, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", type=parse_input, required=True)
    parser.add_argument("--policy-prefix", default="specnet_agent")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = {
        "schema_version": 1,
        "inputs": [
            summarize_run(name, root, args.policy_prefix)
            for name, root in args.input
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
