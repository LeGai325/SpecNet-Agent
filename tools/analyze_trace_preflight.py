#!/usr/bin/env python3
"""Summarize paired trace-workload smoke or Pilot outputs without pandas."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
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
            "p99_latency": percentile(latencies, 0.99),
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


def summarize_run(name: str, root: Path, policy_prefix: str) -> dict[str, Any]:
    workflow_rows = read_csv(root / "workflow_results.csv")
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

    unique = list(unique_workloads.values())
    expected_eval_split = all(row["source_split"] == "test" for row in unique)
    return {
        "name": name,
        "root": str(root),
        "workload_profile": unique[0]["workload_profile"],
        "unique_evaluation_workflows": len(unique),
        "record_sources": ratio_counts([row["record_source"] for row in unique]),
        "workload_modes": ratio_counts([row["workload_source"] for row in unique]),
        "templates": ratio_counts([row["template"] for row in unique]),
        "mapping_versions": ratio_counts(
            [row["mapping_version"] for row in unique]
        ),
        "evaluation_split_is_test_only": expected_eval_split,
        "policies": {
            policy: {
                **summarize_policy_rows(rows),
                "training_state_coverage": summarize_training_states(
                    model_payload["policies"][policy]["model"]
                ),
            }
            for policy, rows in sorted(policy_rows.items())
        },
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
