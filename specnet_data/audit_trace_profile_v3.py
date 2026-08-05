#!/usr/bin/env python3
"""Run a deterministic profile and runtime preflight for the V3 candidate."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from specnet_data.trace_driven_v3 import (  # noqa: E402
    EXPECTED_MODE_MIX,
    EXPECTED_TRACE_SOURCE_MIX,
    generate_trace_workload,
    load_profile,
    sample_trace_records,
)
from specnet_data.trace_driven_v2 import _weighted_counts  # noqa: E402


PHASES = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")


def distribution(values: list[float]) -> dict[str, float | int | None]:
    finite = sorted(value for value in values if math.isfinite(value))

    def quantile(fraction: float) -> float | None:
        if not finite:
            return None
        return finite[round(fraction * (len(finite) - 1))]

    return {
        "count": len(finite),
        "p50": quantile(0.50),
        "p95": quantile(0.95),
        "p99": quantile(0.99),
        "max": max(finite, default=None),
    }


def summarize_sample(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts = Counter(str(row["record_source"]) for row in rows)
    swe = [row for row in rows if row["record_source"] == "swe_chat"]
    return {
        "records": len(rows),
        "source_counts": dict(sorted(source_counts.items())),
        "source_ratios": {
            source: source_counts[source] / len(rows) if rows else 0.0
            for source in EXPECTED_TRACE_SOURCE_MIX
        },
        "swe_chat_agents": dict(
            Counter(str(row["agent"]) for row in swe).most_common()
        ),
        "swe_chat_input_tokens": distribution(
            [float(row["input_tokens"]) for row in swe]
        ),
        "swe_chat_output_tokens": distribution(
            [float(row["output_tokens"]) for row in swe]
        ),
        "swe_chat_tool_calls": distribution(
            [float(row["tool_call_count"]) for row in swe]
        ),
        "swe_chat_timestamp_coverage": distribution(
            [float(row["timestamp_coverage"]) for row in swe]
        ),
        "swe_chat_records_with_usable_timing": sum(
            int(row.get("usable_timing_tool_calls") or 0) > 0 for row in swe
        ),
    }


def summarize_runtime(rows: list[dict[str, Any]], duration: int) -> dict[str, Any]:
    return {
        "workflows": len(rows),
        "sources": dict(Counter(row["record_source"] for row in rows)),
        "modes": dict(Counter(row["workload_source"] for row in rows)),
        "templates": dict(Counter(row["template"] for row in rows)),
        "mapping_versions": sorted({row["mapping_version"] for row in rows}),
        "arrival_sorted": [row["arrival_time"] for row in rows]
        == sorted(row["arrival_time"] for row in rows),
        "arrivals_in_bounds": all(
            0 < int(row["arrival_time"]) < duration for row in rows
        ),
        "required_branches": dict(
            Counter(
                sum(bool(branch["required"]) for branch in row["branches"])
                for row in rows
            )
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=10000)
    parser.add_argument("--runtime-count", type=int, default=40)
    parser.add_argument("--duration", type=int, default=960)
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = load_profile(str(args.profile.resolve()))
    samples = {}
    runtime = {}
    for phase_index, phase in enumerate(PHASES):
        sampled = sample_trace_records(
            args.profile,
            phase,
            args.sample_size,
            args.seed + phase_index,
        )
        samples[phase] = summarize_sample(sampled)
        runtime[phase] = {}
        for load_index, load in enumerate(LOADS):
            rows = generate_trace_workload(
                profile_path=args.profile,
                seed=args.seed + phase_index * 10 + load_index,
                load=load,
                duration=args.duration,
                max_workflows=args.runtime_count,
                target_count=args.runtime_count,
                phase=phase,
            )
            runtime[phase][load] = summarize_runtime(rows, args.duration)

    expected_counts = _weighted_counts(
        args.sample_size, EXPECTED_TRACE_SOURCE_MIX
    )
    checks = {
        "frozen_source_mix_observed": all(
            result["source_counts"] == expected_counts
            for result in samples.values()
        ),
        "all_sources_present_in_every_phase": all(
            set(result["source_counts"]) == set(EXPECTED_TRACE_SOURCE_MIX)
            for result in samples.values()
        ),
        "runtime_mapping_version_valid": all(
            result["mapping_versions"] == ["fixed_template_v3_candidate_a"]
            for phase in runtime.values()
            for result in phase.values()
        ),
        "runtime_arrivals_valid": all(
            result["arrival_sorted"] and result["arrivals_in_bounds"]
            for phase in runtime.values()
            for result in phase.values()
        ),
        "runtime_required_branch_count_is_three": all(
            set(result["required_branches"]) == {3}
            for phase in runtime.values()
            for result in phase.values()
        ),
        "mode_contract": EXPECTED_MODE_MIX,
        "arrival_source": profile["mapping_contract"]["arrival_source"],
        "tau3_in_training_profile": False,
        "candidate_only": True,
    }
    report = {
        "schema_version": 1,
        "generated_at": "2026-08-01",
        "profile_id": profile["profile_id"],
        "sample_size_per_phase": args.sample_size,
        "runtime_count_per_phase_load": args.runtime_count,
        "samples": samples,
        "runtime": runtime,
        "checks": checks,
        "not_run_by_design": {
            "controller_training": "run only after profile preflight passes",
            "formal_evaluation": "requires paired V2 vs V3 experiment plan",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    required_checks = (
        "frozen_source_mix_observed",
        "all_sources_present_in_every_phase",
        "runtime_mapping_version_valid",
        "runtime_arrivals_valid",
        "runtime_required_branch_count_is_three",
    )
    if not all(checks[key] is True for key in required_checks):
        raise RuntimeError("V3 candidate preflight checks failed")
    print(json.dumps(report["checks"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
