"""Stable experiment aggregation and serialization contracts."""
from __future__ import annotations

import csv
import json
import os
import statistics
from collections import defaultdict
from typing import Dict, List, Tuple


def aggregate_summaries(summaries: List[Dict[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str], List[Dict[str, object]]] = defaultdict(list)
    for summary in summaries:
        groups[(str(summary["load"]), str(summary["policy"]))].append(summary)

    rows: List[Dict[str, object]] = []
    for (load, policy), items in sorted(groups.items()):
        row = {
            "load": load,
            "policy": policy,
            "controller_variant": items[0].get("controller_variant", ""),
            "state_features": items[0].get("state_features", ""),
            "quality_weight": items[0].get("quality_weight", ""),
            "slack_queue_basis": items[0].get("slack_queue_basis", ""),
            "slack_queue_weight": items[0].get("slack_queue_weight", ""),
            "train_seed": items[0].get("train_seed", ""),
            "eval_seed": items[0].get("eval_seed", ""),
            "runs": len(items),
            "completed": sum(int(item["completed"]) for item in items),
        }
        metric_names = [
            "mean_latency",
            "p95_latency",
            "p99_latency",
            "deadline_miss_ratio",
            "wasted_speculative_bytes_per_workflow",
            "background_bytes_served_per_workflow",
            "avg_quality",
            "link_utilization",
            "avg_queue_pressure",
        ]
        for metric in metric_names:
            row[metric] = statistics.mean(float(item[metric]) for item in items)
        rows.append(row)
    return rows


def write_csv(path: str, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
