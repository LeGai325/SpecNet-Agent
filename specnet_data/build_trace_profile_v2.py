#!/usr/bin/env python3
"""Build the compact, training-side TraceLab + RAGPulse V2 profile."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PROFILE_ID = "trace_driven_v2"
PROFILE_SCHEMA_VERSION = 2
SPLITS = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")
TRACE_SOURCE_MIX = {"tracelab": 0.75, "ragpulse": 0.25}
OVERALL_MODE_MIX = {
    "train": {"trace": 0.60, "augmented": 0.25, "stress": 0.15},
    "validation": {"trace": 0.70, "stress": 0.30},
    "test": {"trace": 1.00},
}
RAG_REQUIRED_FIELDS = (
    "source_dataset",
    "source_version",
    "source_record_id",
    "session_id",
    "workflow_id",
    "source_window_id",
    "split",
    "arrival_time_ms",
    "input_tokens",
    "output_tokens",
    "retrieval_document_count",
    "history_component_count",
    "web_search_component_count",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSONL row at {path}:{line_number}")
            yield row


def validate_v1_profile(profile: dict[str, Any], path: Path) -> None:
    if profile.get("schema_version") != 1:
        raise ValueError(f"unexpected V1 schema in {path}")
    if profile.get("profile_id") != "trace_driven_v1":
        raise ValueError(f"unexpected V1 profile id in {path}")
    records = profile.get("workflow_records")
    windows = profile.get("arrival_windows")
    if not isinstance(records, dict) or not isinstance(windows, dict):
        raise ValueError(f"incomplete V1 profile in {path}")
    seen_ids: set[str] = set()
    for split in SPLITS:
        split_records = records.get(split)
        if not isinstance(split_records, list) or not split_records:
            raise ValueError(f"V1 profile has no {split} workflow records")
        current_ids = {str(row.get("sample_id")) for row in split_records}
        if len(current_ids) != len(split_records):
            raise ValueError(f"duplicate V1 sample IDs in {split}")
        if seen_ids.intersection(current_ids):
            raise ValueError("V1 workflow split leakage detected")
        seen_ids.update(current_ids)
        split_windows = windows.get(split)
        if not isinstance(split_windows, dict):
            raise ValueError(f"V1 profile has no {split} arrival windows")
        for load in LOADS:
            if not isinstance(split_windows.get(load), list) or not split_windows[load]:
                raise ValueError(f"V1 profile has no {split}/{load} windows")


def compact_ragpulse_record(record: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in RAG_REQUIRED_FIELDS if record.get(field) is None]
    if missing:
        raise ValueError(f"RAGPulse processed record missing fields: {missing}")
    if record["source_dataset"] != "ragpulse":
        raise ValueError("non-RAGPulse record in RAGPulse processed input")
    split = str(record["split"])
    if split not in SPLITS:
        raise ValueError(f"unexpected RAGPulse split: {split}")
    return {
        "sample_id": str(record["source_record_id"]),
        "session_id": str(record["session_id"]),
        "source_window_id": str(record["source_window_id"]),
        "source_arrival_time_ms": float(record["arrival_time_ms"]),
        "template_hint": "rag_request",
        "input_tokens": int(record["input_tokens"]),
        "output_tokens": int(record["output_tokens"]),
        "retrieval_document_count": int(record["retrieval_document_count"]),
        "history_component_count": int(record["history_component_count"]),
        "web_search_component_count": int(
            record["web_search_component_count"]
        ),
    }


def load_ragpulse_records(path: Path) -> dict[str, list[dict[str, Any]]]:
    records = {split: [] for split in SPLITS}
    session_splits: dict[str, str] = {}
    sample_ids: set[str] = set()
    for raw_record in read_jsonl(path):
        record = compact_ragpulse_record(raw_record)
        split = str(raw_record["split"])
        sample_id = str(record["sample_id"])
        session_id = str(record["session_id"])
        if sample_id in sample_ids:
            raise ValueError(f"duplicate RAGPulse sample ID: {sample_id}")
        sample_ids.add(sample_id)
        prior_split = session_splits.setdefault(session_id, split)
        if prior_split != split:
            raise ValueError("RAGPulse session split leakage detected")
        records[split].append(record)
    for split in SPLITS:
        if not records[split]:
            raise ValueError(f"RAGPulse processed input has no {split} records")
    return records


def source_stats(
    tracelab: dict[str, list[dict[str, Any]]],
    ragpulse: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "records_by_source_and_split": {
            "tracelab": {
                split: len(tracelab[split]) for split in SPLITS
            },
            "ragpulse": {
                split: len(ragpulse[split]) for split in SPLITS
            },
        },
        "ragpulse_sessions_by_split": {
            split: len({row["session_id"] for row in ragpulse[split]})
            for split in SPLITS
        },
        "ragpulse_windows": dict(
            sorted(
                Counter(
                    row["source_window_id"]
                    for split in SPLITS
                    for row in ragpulse[split]
                ).items()
            )
        ),
    }


def build_profile(v1_profile_path: Path, ragpulse_path: Path) -> dict[str, Any]:
    v1_profile = json.loads(v1_profile_path.read_text(encoding="utf-8"))
    if not isinstance(v1_profile, dict):
        raise ValueError("V1 profile must be a JSON object")
    validate_v1_profile(v1_profile, v1_profile_path)
    ragpulse_records = load_ragpulse_records(ragpulse_path)
    tracelab_records = v1_profile["workflow_records"]

    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "sources": {
            "tracelab": v1_profile["sources"]["tracelab"],
            "burstgpt": v1_profile["sources"]["burstgpt"],
            "ragpulse": {
                "revision": "3672232d45d749fdcf45dbc38cc77e5264af4a32",
                "processed_filename": ragpulse_path.name,
                "processed_sha256": sha256(ragpulse_path),
                "role": "limited_rag_request_composition",
            },
        },
        "split_policy": {
            "tracelab_unit": "session_id_inherited_from_v1",
            "burstgpt_unit": "natural_day_inherited_from_v1",
            "ragpulse_unit": "complete_session_id",
            "identifiers_in_profile": "salted_hash_only",
            "ragpulse_temporal_arrival_use": "disabled",
            "ragpulse_temporal_reason": (
                "only_two_ambiguous_windows_cannot_form_three_independent_splits"
            ),
        },
        "training_contract": {
            "overall_mode_mix": OVERALL_MODE_MIX,
            "trace_source_mix": TRACE_SOURCE_MIX,
            "effective_train_share": {
                "tracelab_trace": 0.45,
                "ragpulse_trace": 0.15,
                "empirical_neighborhood_augmentation": 0.25,
                "targeted_stress": 0.15,
            },
            "frozen_before_controller_metrics": True,
            "raw_record_counts_do_not_define_source_weight": True,
        },
        "source_records": {
            "tracelab": tracelab_records,
            "ragpulse": ragpulse_records,
        },
        "arrival_windows": v1_profile["arrival_windows"],
        "mapping_contract": {
            "tracelab": v1_profile["mapping"],
            "ragpulse": {
                "allowed_fields": [
                    "input_tokens",
                    "output_tokens",
                    "retrieval_document_count",
                    "history_component_count",
                    "web_search_component_count",
                    "session_grouping",
                ],
                "missing_and_not_inferred": [
                    "agent_step_sequence",
                    "tool_or_component_duration",
                    "dynamic_dag_parent_edges",
                    "task_outcome_or_quality",
                    "deadline_or_slo",
                    "network_telemetry",
                ],
                "simulator_mapping_status": "deferred_until_stage4_repo_check",
            },
            "arrival_source": "burstgpt_v2_natural_day_split",
            "deadline_source": "not_present_in_stage3_profile",
            "network_source": "not_present_in_stage3_profile",
        },
        "external_benchmarks": {
            "tau3_bench": {
                "included_in_training_profile": False,
                "role": "heldout_external_evaluation_after_runner_integration",
            }
        },
        "stats": source_stats(tracelab_records, ragpulse_records),
        "provenance": {
            "v1_profile_path": str(v1_profile_path),
            "v1_profile_sha256": sha256(v1_profile_path),
            "ragpulse_processed_path": str(ragpulse_path),
            "ragpulse_processed_sha256": sha256(ragpulse_path),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-profile", type=Path, required=True)
    parser.add_argument("--ragpulse-records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = build_profile(args.v1_profile, args.ragpulse_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
                "sha256": sha256(args.output),
                "records": profile["stats"]["records_by_source_and_split"],
                "trace_source_mix": TRACE_SOURCE_MIX,
                "tau3_in_training_profile": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
