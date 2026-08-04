#!/usr/bin/env python3
"""Compare V2 and V3 candidate workloads with paired deterministic seeds."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from specnet_agent_experiments.specnet_agent_experiment import (  # noqa: E402
    WorkflowSpec,
    generate_workload,
)


PHASES = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")
PROFILE_NAMES = ("v2", "v3_candidate")
PROFILE_IDS = {
    "v2": "trace_driven_v2",
    "v3_candidate": "trace_driven_v3_candidate",
}


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))

    def percentile(fraction: float) -> float | None:
        if not finite:
            return None
        if len(finite) == 1:
            return finite[0]
        rank = fraction * (len(finite) - 1)
        lower = math.floor(rank)
        upper = math.ceil(rank)
        weight = rank - lower
        return finite[lower] * (1.0 - weight) + finite[upper] * weight

    return {
        "count": len(finite),
        "min": min(finite, default=None),
        "mean": statistics.fmean(finite) if finite else None,
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(finite, default=None),
        "sample_std": statistics.stdev(finite) if len(finite) > 1 else 0.0,
    }


def counter_summary(counter: Counter[str]) -> dict[str, dict[str, float | int]]:
    total = sum(counter.values())
    return {
        key: {
            "count": count,
            "ratio": count / total if total else 0.0,
        }
        for key, count in sorted(counter.items())
    }


def workflow_values(spec: WorkflowSpec) -> dict[str, float]:
    required_work = sum(branch.size for branch in spec.branches if branch.required)
    optional_work = sum(branch.size for branch in spec.branches if not branch.required)
    background_work = sum(spec.background_sizes)
    total_work = (
        spec.planner_size
        + required_work
        + optional_work
        + spec.llm_size
        + spec.judge_size
        + background_work
    )
    return {
        "deadline": spec.deadline,
        "planner_work": spec.planner_size,
        "required_branch_work": required_work,
        "optional_branch_work": optional_work,
        "llm_work": spec.llm_size,
        "judge_work": spec.judge_size,
        "background_work": background_work,
        "total_declared_work": total_work,
        "branch_count": float(len(spec.branches)),
        "required_branch_count": float(
            sum(1 for branch in spec.branches if branch.required)
        ),
        "optional_branch_count": float(
            sum(1 for branch in spec.branches if not branch.required)
        ),
    }


def summarize_specs(specs: list[WorkflowSpec]) -> dict[str, Any]:
    metrics: dict[str, list[float]] = defaultdict(list)
    sources: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    templates: Counter[str] = Counter()
    services: Counter[str] = Counter()
    mapping_versions: Counter[str] = Counter()
    arrivals: list[int] = []
    for spec in specs:
        for key, value in workflow_values(spec).items():
            metrics[key].append(value)
        sources[spec.record_source] += 1
        modes[spec.workload_source] += 1
        templates[spec.template] += 1
        mapping_versions[spec.mapping_version] += 1
        arrivals.append(spec.arrival_time)
        services.update(branch.service_type for branch in spec.branches)
    arrival_gaps = [
        float(right - left)
        for left, right in zip(sorted(arrivals), sorted(arrivals)[1:])
    ]
    return {
        "workflows": len(specs),
        "sources": counter_summary(sources),
        "modes": counter_summary(modes),
        "templates": counter_summary(templates),
        "branch_service_types": counter_summary(services),
        "mapping_versions": counter_summary(mapping_versions),
        "metrics": {
            **{key: distribution(values) for key, values in sorted(metrics.items())},
            "arrival_gap": distribution(arrival_gaps),
        },
    }


def relative_change(v2: float | None, v3: float | None) -> float | None:
    if v2 is None or v3 is None or v2 == 0.0:
        return None
    return (v3 - v2) / v2


def parse_seed_range(text: str) -> list[int]:
    if ":" in text:
        start_text, count_text = text.split(":", 1)
        start = int(start_text)
        count = int(count_text)
        if count <= 0:
            raise argparse.ArgumentTypeError("seed count must be positive")
        return list(range(start, start + count))
    seeds = [int(item.strip()) for item in text.split(",") if item.strip()]
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-profile", type=Path, required=True)
    parser.add_argument("--v3-profile", type=Path, required=True)
    parser.add_argument(
        "--seeds",
        default="20260801:30",
        help="Comma-separated seeds or START:COUNT (default: 20260801:30).",
    )
    parser.add_argument("--duration", type=int, default=960)
    parser.add_argument("--max-workflows", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = parse_seed_range(args.seeds)
    profile_paths = {
        "v2": str(args.v2_profile.expanduser().resolve()),
        "v3_candidate": str(args.v3_profile.expanduser().resolve()),
    }
    aggregate_specs: dict[str, dict[str, dict[str, list[WorkflowSpec]]]] = {
        name: {
            phase: {load: [] for load in LOADS}
            for phase in PHASES
        }
        for name in PROFILE_NAMES
    }
    paired_arrival_checks: list[dict[str, Any]] = []
    for phase_index, phase in enumerate(PHASES):
        for load_index, load in enumerate(LOADS):
            for base_seed in seeds:
                workload_seed = base_seed + phase_index * 1000 + load_index * 100
                paired: dict[str, list[WorkflowSpec]] = {}
                for name in PROFILE_NAMES:
                    specs = generate_workload(
                        workload_seed,
                        load,
                        args.duration,
                        args.max_workflows,
                        workload_profile=PROFILE_IDS[name],
                        phase=phase,
                        trace_profile_path=profile_paths[name],
                    )
                    aggregate_specs[name][phase][load].extend(specs)
                    paired[name] = specs
                v2_arrivals = [spec.arrival_time for spec in paired["v2"]]
                v3_arrivals = [
                    spec.arrival_time for spec in paired["v3_candidate"]
                ]
                paired_arrival_checks.append(
                    {
                        "phase": phase,
                        "load": load,
                        "seed": workload_seed,
                        "counts_equal": len(v2_arrivals) == len(v3_arrivals),
                        "arrivals_equal": v2_arrivals == v3_arrivals,
                    }
                )

    summaries = {
        name: {
            phase: {
                load: summarize_specs(aggregate_specs[name][phase][load])
                for load in LOADS
            }
            for phase in PHASES
        }
        for name in PROFILE_NAMES
    }
    mean_deltas = {}
    for phase in PHASES:
        mean_deltas[phase] = {}
        for load in LOADS:
            v2_metrics = summaries["v2"][phase][load]["metrics"]
            v3_metrics = summaries["v3_candidate"][phase][load]["metrics"]
            mean_deltas[phase][load] = {
                key: {
                    "v2_mean": v2_metrics[key]["mean"],
                    "v3_mean": v3_metrics[key]["mean"],
                    "relative_change": relative_change(
                        v2_metrics[key]["mean"], v3_metrics[key]["mean"]
                    ),
                }
                for key in v2_metrics
                if key in v3_metrics
            }

    checks = {
        "all_paired_workflow_counts_equal": all(
            row["counts_equal"] for row in paired_arrival_checks
        ),
        "all_paired_arrivals_equal": all(
            row["arrivals_equal"] for row in paired_arrival_checks
        ),
        "v2_required_branch_count_is_three": all(
            summaries["v2"][phase][load]["metrics"]
            ["required_branch_count"]["min"]
            == summaries["v2"][phase][load]["metrics"]
            ["required_branch_count"]["max"]
            == 3.0
            for phase in PHASES
            for load in LOADS
        ),
        "v3_required_branch_count_is_three": all(
            summaries["v3_candidate"][phase][load]["metrics"]
            ["required_branch_count"]["min"]
            == summaries["v3_candidate"][phase][load]["metrics"]
            ["required_branch_count"]["max"]
            == 3.0
            for phase in PHASES
            for load in LOADS
        ),
        "test_mode_is_trace_only": all(
            set(summaries[name]["test"][load]["modes"]) == {"trace"}
            for name in PROFILE_NAMES
            for load in LOADS
        ),
    }
    report = {
        "schema_version": 1,
        "generated_at": "2026-08-01",
        "purpose": "paired V2 vs V3 candidate workload preflight",
        "parameters": {
            "seeds": seeds,
            "duration": args.duration,
            "max_workflows": args.max_workflows,
            "phases": PHASES,
            "loads": LOADS,
            "v2_profile": profile_paths["v2"],
            "v3_profile": profile_paths["v3_candidate"],
        },
        "checks": checks,
        "mean_deltas_v3_vs_v2": mean_deltas,
        "profiles": summaries,
        "paired_arrival_failures": [
            row
            for row in paired_arrival_checks
            if not row["counts_equal"] or not row["arrivals_equal"]
        ],
        "interpretation_boundary": (
            "Identical arrivals isolate the record-to-fixed-template mapping change; "
            "this report does not evaluate Controller quality."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not all(checks.values()):
        raise RuntimeError("paired workload preflight checks failed")
    print(json.dumps(checks, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
