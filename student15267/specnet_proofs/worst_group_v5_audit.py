#!/usr/bin/env python3
"""Audit V5 candidates against V4 using marginal-group and worst-cell gates.

This script is intentionally post-processing only: it reads a validation V5
screen directory and never reads a test split.  A candidate must satisfy its
quality, tail, deadline, bytes, and utilization gates in every reported group,
not merely after averaging the complete validation matrix.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Tuple


METRICS = ("p99_latency", "deadline_miss_ratio", "total_served_bytes", "link_utilization")


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.fmean(values) if values else float("nan")


def group_keys(scenario: Tuple[str, float, float, float]) -> Dict[str, str]:
    load, deadline, optional, capacity = scenario
    return {
        "load": f"load={load}",
        "deadline": f"deadline={deadline:g}",
        "optional": f"optional={optional:g}",
        "capacity": f"capacity={capacity:g}",
        "worst_cell": f"cell={load}|d={deadline:g}|o={optional:g}|c={capacity:g}",
    }


def passes(row: Mapping[str, float]) -> bool:
    return (
        row["min_quality_target_ratio"] >= 1.0
        and row["delta_p99_latency_vs_v4"] <= 1e-9
        and row["delta_deadline_miss_ratio_vs_v4"] <= 0.02 + 1e-9
        and row["delta_total_served_bytes_vs_v4"] <= 1e-9
        and row["delta_link_utilization_vs_v4"] <= 1e-9
    )


def audit(screen_dir: Path) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    manifest = json.loads((screen_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("split") != "validation":
        raise ValueError("worst-group selection may only read a validation screen")
    scenarios = {index: tuple(item) for index, item in enumerate(manifest["evaluation_matrix"])}
    baseline = {
        (int(row["evaluation_run"]), int(row["scenario"])): row
        for row in read_csv(screen_dir / "v4_reference_cells.csv")
    }
    candidates = read_csv(screen_dir / "v5_candidate_cells.csv")
    grouped: Dict[str, Dict[Tuple[str, str], List[Tuple[Mapping[str, str], Mapping[str, str]]]]] = defaultdict(lambda: defaultdict(list))
    for candidate in candidates:
        key = (int(candidate["evaluation_run"]), int(candidate["scenario"]))
        reference = baseline.get(key)
        if reference is None:
            raise ValueError(f"missing V4 reference for {key}")
        for group_type, label in group_keys(scenarios[key[1]]).items():
            grouped[candidate["candidate"]][(group_type, label)].append((candidate, reference))

    units: List[Dict[str, object]] = []
    summary: List[Dict[str, object]] = []
    for candidate, candidate_groups in grouped.items():
        candidate_units: List[Dict[str, object]] = []
        for (group_type, label), pairs in candidate_groups.items():
            row: Dict[str, object] = {
                "candidate": candidate,
                "group_type": group_type,
                "group": label,
                "cells": len(pairs),
                "min_quality_target_ratio": min(float(cell["quality_target_met_ratio"]) for cell, _ in pairs),
            }
            for metric in METRICS:
                delta = mean(float(cell[metric]) - float(reference[metric]) for cell, reference in pairs)
                row[f"delta_{metric}_vs_v4"] = delta
            row["gate_pass"] = int(passes(row))
            candidate_units.append(row)
            units.append(row)
        failures = [row for row in candidate_units if not int(row["gate_pass"])]
        worst_p99 = max(float(row["delta_p99_latency_vs_v4"]) for row in candidate_units)
        worst_bytes = max(float(row["delta_total_served_bytes_vs_v4"]) for row in candidate_units)
        worst_util = max(float(row["delta_link_utilization_vs_v4"]) for row in candidate_units)
        worst_miss = max(float(row["delta_deadline_miss_ratio_vs_v4"]) for row in candidate_units)
        summary.append(
            {
                "candidate": candidate,
                "groups": len(candidate_units),
                "failed_groups": len(failures),
                "max_delta_p99_latency_vs_v4": worst_p99,
                "max_delta_deadline_miss_ratio_vs_v4": worst_miss,
                "max_delta_total_served_bytes_vs_v4": worst_bytes,
                "max_delta_link_utilization_vs_v4": worst_util,
                "selection_status": "worst_group_feasible" if not failures else "rejected_worst_group",
            }
        )
    summary.sort(key=lambda row: (row["selection_status"] != "worst_group_feasible", row["max_delta_p99_latency_vs_v4"], row["max_delta_total_served_bytes_vs_v4"]))
    return units, summary


def write_csv(path: Path, rows: List[Mapping[str, object]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    units, summary = audit(args.screen_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "worst_group_units.csv", units)
    write_csv(args.output_dir / "worst_group_summary.csv", summary)
    feasible = [row for row in summary if row["selection_status"] == "worst_group_feasible"]
    lines = [
        "# V5 最差组约束审计", "",
        "本分析只读取 validation screen。候选必须通过每个 marginal group（load/deadline/optional/capacity）和每个单独 stress cell 的质量、p99、miss、served-bytes、utilization 门。", "",
        "| Candidate | Failed groups | Max Δp99 | Max Δmiss | Max Δbytes | Max Δutil | Verdict |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary:
        lines.append(
            f"| {row['candidate']} | {row['failed_groups']} | {float(row['max_delta_p99_latency_vs_v4']):+.3f} | "
            f"{float(row['max_delta_deadline_miss_ratio_vs_v4']):+.5f} | {float(row['max_delta_total_served_bytes_vs_v4']):+.2f} | "
            f"{float(row['max_delta_link_utilization_vs_v4']):+.6f} | {row['selection_status']} |"
        )
    lines += ["", f"可行候选数：{len(feasible)}/{len(summary)}。"]
    (args.output_dir / "WORST_GROUP_V5_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[done] worst-group audit written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
