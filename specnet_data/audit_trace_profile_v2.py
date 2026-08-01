#!/usr/bin/env python3
"""Run a no-simulator, no-training coverage smoke for the V2 profile."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SOURCE_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent
if str(SOURCE_SNAPSHOT_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_SNAPSHOT_DIR))

from specnet_data.ragpulse_v2 import distribution  # noqa: E402
from specnet_data.trace_driven_v2 import (  # noqa: E402
    load_profile,
    sample_trace_records,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260801)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def summarize_sample(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sources = Counter(str(row["record_source"]) for row in rows)
    ragpulse = [row for row in rows if row["record_source"] == "ragpulse"]
    return {
        "records": len(rows),
        "source_counts": dict(sorted(sources.items())),
        "source_ratios": {
            source: count / len(rows) if rows else 0.0
            for source, count in sorted(sources.items())
        },
        "ragpulse_input_tokens": distribution(
            [float(row["input_tokens"]) for row in ragpulse]
        ),
        "ragpulse_output_tokens": distribution(
            [float(row["output_tokens"]) for row in ragpulse]
        ),
        "ragpulse_retrieval_documents": distribution(
            [float(row["retrieval_document_count"]) for row in ragpulse]
        ),
    }


def main() -> None:
    args = parse_args()
    profile = load_profile(str(args.profile.resolve()))
    phase_results = {}
    for index, phase in enumerate(("train", "validation", "test")):
        rows = sample_trace_records(
            args.profile,
            phase,
            args.sample_size,
            args.seed + index,
        )
        phase_results[phase] = summarize_sample(rows)
    report = {
        "schema_version": 1,
        "generated_at": "2026-08-01",
        "profile_id": profile["profile_id"],
        "sample_size_per_phase": args.sample_size,
        "phases": phase_results,
        "checks": {
            "preregistered_source_mix_observed": all(
                result["source_counts"]
                == {
                    "ragpulse": round(args.sample_size * 0.25),
                    "tracelab": args.sample_size
                    - round(args.sample_size * 0.25),
                }
                for result in phase_results.values()
            ),
            "both_sources_present_in_every_phase": all(
                set(result["source_counts"]) == {"tracelab", "ragpulse"}
                for result in phase_results.values()
            ),
            "tau3_in_training_profile": False,
            "ragpulse_temporal_arrival_enabled": False,
        },
        "not_run_by_design": {
            "simulator_workflow_generation": "deferred_until_stage4",
            "slack_bucket_coverage": "requires_simulated_deadline_and_queue",
            "controller_training": "stage3_is_profile_only",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
