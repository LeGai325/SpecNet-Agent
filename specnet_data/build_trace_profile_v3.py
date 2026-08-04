#!/usr/bin/env python3
"""Build the isolated TraceLab + SWE-chat + RAGPulse V3 candidate profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from specnet_data.trace_driven_v2 import load_profile as load_v2_profile


PROFILE_ID = "trace_driven_v3_candidate"
PROFILE_SCHEMA_VERSION = 3
SPLITS = ("train", "validation", "test")
TRACE_SOURCE_MIX = {
    "tracelab": 0.375,
    "swe_chat": 0.375,
    "ragpulse": 0.25,
}
EXPECTED_MODE_MIX = {
    "train": {"trace": 0.60, "augmented": 0.25, "stress": 0.15},
    "validation": {"trace": 0.70, "stress": 0.30},
    "test": {"trace": 1.00},
}
SWE_REQUIRED_FIELDS = (
    "source_dataset",
    "source_revision",
    "sample_id",
    "split",
    "split_component_id",
    "split_unit",
    "template_hint",
    "agent",
    "input_tokens",
    "output_tokens",
    "tool_call_count",
    "tool_service_counts",
    "tool_latency_ms_by_service",
    "timestamp_coverage",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSONL row at {path}:{line_number}")
            yield row


def load_swe_records(path: Path) -> dict[str, list[dict[str, Any]]]:
    records = {split: [] for split in SPLITS}
    sample_splits: dict[str, str] = {}
    component_splits: dict[str, str] = {}
    revision: str | None = None
    forbidden_raw_fields = {"session_id", "repo_id", "user_id", "content"}

    for row in read_jsonl(path):
        missing = [field for field in SWE_REQUIRED_FIELDS if row.get(field) is None]
        if missing:
            raise ValueError(f"SWE-chat processed record missing fields: {missing}")
        if row["source_dataset"] != "swe_chat":
            raise ValueError("non-SWE-chat row in SWE-chat processed input")
        present_forbidden = forbidden_raw_fields.intersection(row)
        if present_forbidden:
            raise ValueError(
                f"SWE-chat processed record exposes raw fields: {present_forbidden}"
            )
        split = str(row["split"])
        if split not in SPLITS:
            raise ValueError(f"unexpected SWE-chat split: {split}")
        sample_id = str(row["sample_id"])
        if sample_id in sample_splits:
            raise ValueError(f"duplicate SWE-chat sample ID: {sample_id}")
        sample_splits[sample_id] = split
        component_id = str(row["split_component_id"])
        prior_split = component_splits.setdefault(component_id, split)
        if prior_split != split:
            raise ValueError("SWE-chat repo-user component split leakage detected")
        current_revision = str(row["source_revision"])
        if revision is None:
            revision = current_revision
        elif revision != current_revision:
            raise ValueError("SWE-chat processed input mixes source revisions")
        boundaries = row.get("mapping_boundaries") or {}
        if boundaries.get("session_duration_used_as_service_time") is not False:
            raise ValueError("SWE-chat session duration must not become service time")
        if boundaries.get("session_success_ground_truth") is not False:
            raise ValueError("SWE-chat session_success must remain auxiliary")
        records[split].append(row)

    for split in SPLITS:
        if not records[split]:
            raise ValueError(f"SWE-chat processed input has no {split} records")
        records[split].sort(key=lambda row: str(row["sample_id"]))
    return records


def source_stats(
    source_records: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    swe_records = source_records["swe_chat"]
    return {
        "records_by_source_and_split": {
            source: {
                split: len(source_records[source][split]) for split in SPLITS
            }
            for source in TRACE_SOURCE_MIX
        },
        "swe_chat_agents": dict(
            Counter(
                str(row["agent"])
                for split in SPLITS
                for row in swe_records[split]
            ).most_common()
        ),
        "swe_chat_timing_usable_records": {
            split: sum(
                int(row.get("usable_timing_tool_calls") or 0) > 0
                for row in swe_records[split]
            )
            for split in SPLITS
        },
    }


def build_profile(v2_profile_path: Path, swe_records_path: Path) -> dict[str, Any]:
    v2_profile = load_v2_profile(str(v2_profile_path.resolve()))
    swe_records = load_swe_records(swe_records_path)
    source_revision = str(swe_records["train"][0]["source_revision"])
    source_records = {
        "tracelab": v2_profile["source_records"]["tracelab"],
        "swe_chat": swe_records,
        "ragpulse": v2_profile["source_records"]["ragpulse"],
    }
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "sources": {
            **v2_profile["sources"],
            "swe_chat": {
                "dataset_id": "SALT-NLP/SWE-chat",
                "revision": source_revision,
                "processed_filename": swe_records_path.name,
                "processed_sha256": sha256_file(swe_records_path),
                "role": "real_agent_workflow_tool_token_and_cleaned_timing",
            },
        },
        "split_policy": {
            **v2_profile["split_policy"],
            "swe_chat_unit": "content_hash_dedup_then_repo_user_component",
            "swe_chat_identifiers_in_profile": "salted_hash_only",
            "swe_chat_session_duration_use": "disabled",
            "swe_chat_session_success_use": "auxiliary_only",
            "cross_source_repo_overlap_check": "not_available",
        },
        "training_contract": {
            "overall_mode_mix": EXPECTED_MODE_MIX,
            "trace_source_mix": TRACE_SOURCE_MIX,
            "scenario_mix_invariant": {
                "coding": 0.75,
                "rag_qa": 0.25,
            },
            "coding_source_replacement": (
                "V2 tracelab share split equally between tracelab and swe_chat"
            ),
            "effective_train_share": {
                "tracelab_trace": 0.225,
                "swe_chat_trace": 0.225,
                "ragpulse_trace": 0.15,
                "empirical_neighborhood_augmentation": 0.25,
                "targeted_stress": 0.15,
            },
            "frozen_before_controller_metrics": True,
            "candidate_only_not_final_profile": True,
            "raw_record_counts_do_not_define_source_weight": True,
        },
        "source_records": source_records,
        "arrival_windows": v2_profile["arrival_windows"],
        "mapping_contract": {
            "tracelab": v2_profile["mapping_contract"]["tracelab"],
            "ragpulse": v2_profile["mapping_contract"]["ragpulse"],
            "swe_chat": {
                "allowed_fields": [
                    "agent",
                    "input_tokens",
                    "output_tokens",
                    "turn_count",
                    "user_prompt_count",
                    "assistant_response_count",
                    "tool_service_counts",
                    "cleaned_paired_tool_latency",
                    "timestamp_coverage",
                ],
                "timing_policy": (
                    "paired_nonnegative_intervals_at_or_below_idle_gap_threshold"
                ),
                "missing_timing_fallback": (
                    "simulator_service_anchor_not_cross_agent_imputation"
                ),
                "missing_and_not_inferred": [
                    "semantic_dynamic_dag_parent_edges",
                    "ground_truth_task_quality",
                    "deadline_or_slo",
                    "network_telemetry",
                    "queue_occupancy",
                    "controller_action_counterfactual",
                ],
                "fixed_template": "coding",
            },
            "arrival_source": "burstgpt_v2_natural_day_split",
            "deadline_source": "simulator_candidate_mapping_not_swe_chat",
            "network_source": "simulator",
        },
        "external_benchmarks": v2_profile["external_benchmarks"],
        "stats": source_stats(source_records),
        "provenance": {
            "v2_profile_path": str(v2_profile_path),
            "v2_profile_sha256": sha256_file(v2_profile_path),
            "swe_chat_processed_path": str(swe_records_path),
            "swe_chat_processed_sha256": sha256_file(swe_records_path),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-profile", type=Path, required=True)
    parser.add_argument("--swe-chat-records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = build_profile(args.v2_profile, args.swe_chat_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
                "sha256": sha256_file(args.output),
                "records": profile["stats"]["records_by_source_and_split"],
                "trace_source_mix": TRACE_SOURCE_MIX,
                "candidate_only": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
