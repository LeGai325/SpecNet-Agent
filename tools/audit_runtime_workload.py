#!/usr/bin/env python3
"""Audit mapped workflow work/deadline distributions before controller training."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from specnet_agent_experiments import specnet_agent_experiment as experiment


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


def distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
        "mean": sum(values) / len(values) if values else None,
    }


def work_fields(spec: experiment.WorkflowSpec) -> dict[str, float]:
    required = sum(branch.size for branch in spec.branches if branch.required)
    optional = sum(branch.size for branch in spec.branches if not branch.required)
    background = sum(spec.background_sizes)
    return {
        "required_branch_work": required,
        "optional_branch_work": optional,
        "background_work": background,
        "critical_declared_work": (
            spec.planner_size + required + spec.llm_size + spec.judge_size
        ),
        "total_declared_work": (
            spec.planner_size
            + required
            + optional
            + spec.llm_size
            + spec.judge_size
            + background
        ),
        "deadline": spec.deadline,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload-profile", required=True)
    parser.add_argument("--trace-profile-path", required=True)
    parser.add_argument("--seeds", default="11,23,37")
    parser.add_argument("--duration", type=int, default=2600)
    parser.add_argument("--max-workflows", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    rows: list[dict[str, Any]] = []
    for phase in ("train", "validation", "test"):
        for load in experiment.LOAD_CONFIG:
            for seed in seeds:
                specs = experiment.generate_workload(
                    seed,
                    load,
                    args.duration,
                    args.max_workflows,
                    workload_profile=args.workload_profile,
                    phase=phase,
                    trace_profile_path=args.trace_profile_path,
                )
                for spec in specs:
                    rows.append(
                        {
                            "phase": phase,
                            "load": load,
                            "seed": seed,
                            "record_source": spec.record_source,
                            "workload_source": spec.workload_source,
                            "template": spec.template,
                            **work_fields(spec),
                        }
                    )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["phase"], row["load"], row["record_source"])].append(row)
    metrics = {}
    for (phase, load, source), items in sorted(grouped.items()):
        metrics[f"{phase}/{load}/{source}"] = {
            field: distribution([float(row[field]) for row in items])
            for field in (
                "required_branch_work",
                "optional_branch_work",
                "background_work",
                "critical_declared_work",
                "total_declared_work",
                "deadline",
            )
        }

    report = {
        "schema_version": 1,
        "workload_profile": args.workload_profile,
        "trace_profile_path": str(Path(args.trace_profile_path).resolve()),
        "seeds": seeds,
        "duration": args.duration,
        "max_workflows": args.max_workflows,
        "records": len(rows),
        "record_sources": dict(Counter(row["record_source"] for row in rows)),
        "workload_modes": dict(Counter(row["workload_source"] for row in rows)),
        "templates": dict(Counter(row["template"] for row in rows)),
        "metrics": metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
