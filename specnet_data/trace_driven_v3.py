"""Validation and fixed-template runtime mapping for the V3 candidate profile."""

from __future__ import annotations

import json
import math
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from specnet_data import trace_driven_v2 as v2


PROFILE_ID = "trace_driven_v3_candidate"
PROFILE_SCHEMA_VERSION = 3
SPLITS = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")
PHASE_TO_SPLIT = {split: split for split in SPLITS}
EXPECTED_TRACE_SOURCE_MIX = {
    "tracelab": 0.375,
    "swe_chat": 0.375,
    "ragpulse": 0.25,
}
EXPECTED_MODE_MIX = v2.EXPECTED_MODE_MIX
SWE_BRANCH_LIMIT = 7


def default_profile_path() -> Path:
    data_root = os.environ.get("SPECNET_DATA_ROOT")
    if not data_root:
        raise ValueError(
            "trace_driven_v3_candidate requires a profile path or SPECNET_DATA_ROOT"
        )
    return Path(data_root) / "processed" / PROFILE_ID / "profile.json"


def resolve_profile_path(path: str | os.PathLike[str] | None) -> Path:
    return (
        Path(path).expanduser().resolve()
        if path
        else default_profile_path().resolve()
    )


def _validate_profile(profile: dict[str, Any], path: Path) -> None:
    if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"unsupported V3 candidate profile schema in {path}")
    if profile.get("profile_id") != PROFILE_ID:
        raise ValueError(f"unexpected V3 candidate profile id in {path}")
    contract = profile.get("training_contract") or {}
    if contract.get("trace_source_mix") != EXPECTED_TRACE_SOURCE_MIX:
        raise ValueError("V3 candidate source mix differs from frozen weights")
    if contract.get("overall_mode_mix") != EXPECTED_MODE_MIX:
        raise ValueError("V3 candidate mode mix differs from V2 control")
    if contract.get("frozen_before_controller_metrics") is not True:
        raise ValueError("V3 candidate source weights are not marked frozen")
    if contract.get("candidate_only_not_final_profile") is not True:
        raise ValueError("V3 profile is not marked candidate-only")

    sources = profile.get("source_records")
    if not isinstance(sources, dict) or set(sources) != set(EXPECTED_TRACE_SOURCE_MIX):
        raise ValueError(f"unexpected V3 candidate training sources in {path}")
    for source, split_records in sources.items():
        if not isinstance(split_records, dict):
            raise ValueError(f"invalid {source} record container")
        sample_splits: dict[str, str] = {}
        component_splits: dict[str, str] = {}
        for split in SPLITS:
            records = split_records.get(split)
            if not isinstance(records, list) or not records:
                raise ValueError(f"V3 candidate profile has no {source}/{split} records")
            for record in records:
                sample_id = str(record.get("sample_id"))
                if sample_id in sample_splits:
                    if sample_splits[sample_id] != split:
                        raise ValueError(f"{source} sample split leakage detected")
                    raise ValueError(f"duplicate {source} sample ID detected")
                sample_splits[sample_id] = split
                if source == "swe_chat":
                    component_id = str(record.get("split_component_id"))
                    prior_split = component_splits.setdefault(component_id, split)
                    if prior_split != split:
                        raise ValueError(
                            "SWE-chat repo-user component split leakage detected"
                        )

    arrival_windows = profile.get("arrival_windows")
    if not isinstance(arrival_windows, dict):
        raise ValueError(f"missing V3 candidate arrival windows in {path}")
    for split in SPLITS:
        for load in LOADS:
            windows = (arrival_windows.get(split) or {}).get(load)
            if not isinstance(windows, list) or not windows:
                raise ValueError(f"missing V3 candidate {split}/{load} arrival windows")
    tau3 = ((profile.get("external_benchmarks") or {}).get("tau3_bench") or {})
    if tau3.get("included_in_training_profile") is not False:
        raise ValueError("tau3-bench must remain outside the V3 training profile")
    mapping = profile.get("mapping_contract") or {}
    swe_mapping = mapping.get("swe_chat") or {}
    if swe_mapping.get("fixed_template") != "coding":
        raise ValueError("SWE-chat V3 candidate must use the declared coding template")


@lru_cache(maxsize=8)
def load_profile(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        raise FileNotFoundError(f"V3 candidate profile not found: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError(f"V3 candidate profile must be a JSON object: {path}")
    _validate_profile(profile, path)
    return profile


def sample_trace_records(
    profile_path: str | os.PathLike[str],
    phase: str,
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    if phase not in PHASE_TO_SPLIT:
        raise ValueError(f"unknown V3 candidate profile phase: {phase}")
    if count < 0:
        raise ValueError("sample count must be non-negative")
    profile = load_profile(str(Path(profile_path).resolve()))
    split = PHASE_TO_SPLIT[phase]
    counts = v2._weighted_counts(count, EXPECTED_TRACE_SOURCE_MIX)
    rng = random.Random(seed)
    sampled: list[dict[str, Any]] = []
    for source in EXPECTED_TRACE_SOURCE_MIX:
        records = profile["source_records"][source][split]
        chosen = (
            rng.sample(records, counts[source])
            if counts[source] <= len(records)
            else [rng.choice(records) for _ in range(counts[source])]
        )
        for record in chosen:
            row = dict(record)
            row["record_source"] = source
            row["source_split"] = split
            sampled.append(row)
    rng.shuffle(sampled)
    return sampled


def _sample_assignments(
    profile: dict[str, Any],
    phase: str,
    count: int,
    rng: random.Random,
) -> list[tuple[str, str, dict[str, Any]]]:
    split = PHASE_TO_SPLIT[phase]
    source_counts = v2._weighted_counts(count, EXPECTED_TRACE_SOURCE_MIX)
    mode_counts = v2._weighted_counts(count, EXPECTED_MODE_MIX[phase])
    raw_cells = {
        (mode, source): (
            mode_counts[mode] * source_counts[source] / count if count else 0.0
        )
        for mode in mode_counts
        for source in EXPECTED_TRACE_SOURCE_MIX
    }
    cells = {cell: math.floor(value) for cell, value in raw_cells.items()}
    row_deficit = {
        mode: mode_counts[mode]
        - sum(cells[(mode, source)] for source in EXPECTED_TRACE_SOURCE_MIX)
        for mode in mode_counts
    }
    column_deficit = {
        source: source_counts[source]
        - sum(cells[(mode, source)] for mode in mode_counts)
        for source in EXPECTED_TRACE_SOURCE_MIX
    }
    while sum(row_deficit.values()):
        candidates = [
            (mode, source)
            for mode in mode_counts
            for source in EXPECTED_TRACE_SOURCE_MIX
            if row_deficit[mode] > 0
            and column_deficit[source] > 0
        ]
        if not candidates:
            raise AssertionError("V3 candidate source/mode allocation became infeasible")
        mode, source = max(
            candidates,
            key=lambda cell: (
                raw_cells[cell]
                - math.floor(raw_cells[cell])
                - (cells[cell] - math.floor(raw_cells[cell])),
                mode_counts[cell[0]],
                source_counts[cell[1]],
                cell,
            ),
        )
        cells[(mode, source)] += 1
        row_deficit[mode] -= 1
        column_deficit[source] -= 1

    sampled_by_source: dict[str, list[dict[str, Any]]] = {}
    for source in EXPECTED_TRACE_SOURCE_MIX:
        records = profile["source_records"][source][split]
        sampled_by_source[source] = (
            rng.sample(records, source_counts[source])
            if source_counts[source] <= len(records)
            else [rng.choice(records) for _ in range(source_counts[source])]
        )
    source_offsets = {source: 0 for source in EXPECTED_TRACE_SOURCE_MIX}
    assignments: list[tuple[str, str, dict[str, Any]]] = []
    for mode in mode_counts:
        for source in EXPECTED_TRACE_SOURCE_MIX:
            start = source_offsets[source]
            end = start + cells[(mode, source)]
            assignments.extend(
                (mode, source, record)
                for record in sampled_by_source[source][start:end]
            )
            source_offsets[source] = end
    if len(assignments) != count:
        raise AssertionError("V3 candidate assignment allocation changed the count")
    rng.shuffle(assignments)
    return assignments


def _service_slots(service_counts: dict[str, Any]) -> list[str]:
    valid = {
        service: max(0.0, float(service_counts.get(service) or 0.0))
        for service in v2.SERVICE_BASE_SIZE
    }
    total = sum(valid.values())
    if total <= 0:
        return []
    weights = {service: count / total for service, count in valid.items()}
    counts = v2._weighted_counts(SWE_BRANCH_LIMIT, weights)
    return [
        service for service in v2.SERVICE_BASE_SIZE for _ in range(counts[service])
    ]


def _swe_chat_base_record(
    rng: random.Random, record: dict[str, Any]
) -> dict[str, Any]:
    input_tokens = float(record.get("input_tokens") or 0.0)
    output_tokens = float(record.get("output_tokens") or 0.0)
    tool_count = float(record.get("tool_call_count") or 0.0)
    turn_count = float(record.get("turn_count") or 0.0)
    service_counts = record.get("tool_service_counts") or {}
    latency_by_service = record.get("tool_latency_ms_by_service") or {}
    services = _service_slots(service_counts)
    while len(services) < SWE_BRANCH_LIMIT:
        services.append(rng.choice(v2.FILL_SERVICE_TYPES))

    branches = []
    for service_type in services[:SWE_BRANCH_LIMIT]:
        latency_ms = float(latency_by_service.get(service_type) or 196.0)
        size = v2._scaled_size(
            latency_ms + 1.0,
            196.0,
            v2.SERVICE_BASE_SIZE[service_type],
            0.22,
        )
        branches.append(
            {
                "service_type": service_type,
                "size": size * rng.uniform(0.94, 1.06),
            }
        )

    tool_scale = v2._clamp((tool_count + 1.0) / 30.0, 0.30, 5.0)
    turn_scale = v2._clamp((turn_count + 1.0) / 12.0, 0.35, 4.0)
    output_scale = v2._clamp((output_tokens + 1.0) / 6810.0, 0.20, 5.0)
    background_base = v2._scaled_size(
        input_tokens + 1.0, 82.0, 78.0, 0.10
    )
    work_scale = (tool_scale + turn_scale + output_scale) / 3.0
    return {
        "template": "coding",
        "planner_size": v2._scaled_size(input_tokens + 1.0, 82.0, 6.0, 0.12),
        "branches": branches,
        "llm_size": v2._scaled_size(output_tokens + 1.0, 6810.0, 46.0, 0.20),
        "judge_size": v2._scaled_size(
            output_tokens * 0.10 + 1.0, 681.0, 14.0, 0.16
        ),
        "background_sizes": [
            max(2.0, background_base * rng.uniform(0.85, 1.15))
            for _ in range(3)
        ],
        "deadline_base": 300.0 * v2._clamp(work_scale**0.10, 0.82, 1.28),
    }


def _map_swe_record(
    rng: random.Random,
    record: dict[str, Any],
    mode: str,
    split: str,
    arrival_time: int,
    workflow_id: int,
) -> dict[str, Any]:
    mapped = _swe_chat_base_record(rng, record)
    size_multiplier = 1.0
    deadline_multiplier = 1.0
    if mode == "augmented":
        size_multiplier = rng.uniform(0.82, 1.28)
        deadline_multiplier = rng.uniform(0.88, 1.15)
    elif mode == "stress":
        size_multiplier = rng.uniform(1.30, 1.75)
        deadline_multiplier = rng.uniform(0.55, 0.72)
    elif mode != "trace":
        raise ValueError(f"unsupported V3 candidate workload mode: {mode}")

    branches = [
        {
            "service_type": str(branch["service_type"]),
            "size": max(2.0, float(branch["size"]) * size_multiplier),
            "required": index < 3,
        }
        for index, branch in enumerate(mapped["branches"])
    ]
    return {
        "workflow_id": workflow_id,
        "arrival_time": arrival_time,
        "template": "coding",
        "deadline": (
            float(mapped["deadline_base"])
            * deadline_multiplier
            * rng.uniform(0.94, 1.06)
        ),
        "planner_size": float(mapped["planner_size"]) * size_multiplier,
        "branches": branches,
        "llm_size": float(mapped["llm_size"]) * size_multiplier,
        "judge_size": float(mapped["judge_size"]) * size_multiplier,
        "background_sizes": [
            max(2.0, float(value) * size_multiplier)
            for value in mapped["background_sizes"]
        ],
        "workload_profile": PROFILE_ID,
        "workload_source": mode,
        "record_source": "swe_chat",
        "source_split": split,
        "source_record_id": str(record["sample_id"]),
        "mapping_version": "fixed_template_v3_candidate_a",
    }


def _map_record(
    rng: random.Random,
    record: dict[str, Any],
    record_source: str,
    mode: str,
    split: str,
    arrival_time: int,
    workflow_id: int,
    duration_anchor_ms: float,
) -> dict[str, Any]:
    if record_source == "swe_chat":
        return _map_swe_record(
            rng, record, mode, split, arrival_time, workflow_id
        )
    if record_source not in {"tracelab", "ragpulse"}:
        raise ValueError(f"unsupported V3 candidate record source: {record_source}")
    mapped = v2._map_record(
        rng=rng,
        record=record,
        record_source=record_source,
        mode=mode,
        split=split,
        arrival_time=arrival_time,
        workflow_id=workflow_id,
        duration_anchor_ms=duration_anchor_ms,
    )
    mapped["workload_profile"] = PROFILE_ID
    mapped["mapping_version"] = "fixed_template_v3_candidate_a"
    return mapped


def generate_trace_workload(
    profile_path: str | os.PathLike[str] | None,
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
    target_count: int,
    phase: str,
) -> list[dict[str, Any]]:
    if phase not in PHASE_TO_SPLIT:
        raise ValueError(f"unknown V3 candidate workload phase: {phase}")
    if load not in LOADS:
        raise ValueError(f"unknown V3 candidate workload load: {load}")
    path = resolve_profile_path(profile_path)
    profile = load_profile(str(path))
    split = PHASE_TO_SPLIT[phase]
    rng = random.Random(seed)
    window = rng.choice(profile["arrival_windows"][split][load])
    count = min(max_workflows, max(1, target_count))
    arrivals = v2._select_arrivals(
        rng,
        [float(value) for value in window["arrival_offsets"]],
        count,
        duration,
    )
    assignments = _sample_assignments(profile, phase, len(arrivals), rng)
    tracelab_mapping = profile["mapping_contract"].get("tracelab") or {}
    duration_anchor_ms = float(
        tracelab_mapping.get("round_duration_anchor_ms", 30000.0)
    )
    return [
        _map_record(
            rng=rng,
            record=record,
            record_source=record_source,
            mode=mode,
            split=split,
            arrival_time=arrival_time,
            workflow_id=workflow_id,
            duration_anchor_ms=duration_anchor_ms,
        )
        for workflow_id, (arrival_time, (mode, record_source, record)) in enumerate(
            zip(arrivals, assignments)
        )
    ]
