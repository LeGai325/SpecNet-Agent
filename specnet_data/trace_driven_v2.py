"""Validation and profile-only sampling for trace-driven workload V2."""

from __future__ import annotations

import json
import math
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any


PROFILE_ID = "trace_driven_v2"
PROFILE_SCHEMA_VERSION = 2
SPLITS = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")
PHASE_TO_SPLIT = {split: split for split in SPLITS}
EXPECTED_TRACE_SOURCE_MIX = {"tracelab": 0.75, "ragpulse": 0.25}
EXPECTED_MODE_MIX = {
    "train": {"trace": 0.60, "augmented": 0.25, "stress": 0.15},
    "validation": {"trace": 0.70, "stress": 0.30},
    "test": {"trace": 1.00},
}
SERVICE_BASE_SIZE = {
    "retrieval": 28.0,
    "tool": 42.0,
    "storage": 64.0,
    "llm": 46.0,
}
FILL_SERVICE_TYPES = tuple(SERVICE_BASE_SIZE)
RAG_BRANCH_TYPES = (
    "retrieval",
    "retrieval",
    "retrieval",
    "retrieval",
    "llm",
    "retrieval",
    "tool",
    "retrieval",
)


def default_profile_path() -> Path:
    data_root = os.environ.get("SPECNET_DATA_ROOT")
    if not data_root:
        raise ValueError(
            "trace_driven_v2 requires a profile path or SPECNET_DATA_ROOT"
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
        raise ValueError(f"unsupported V2 profile schema in {path}")
    if profile.get("profile_id") != PROFILE_ID:
        raise ValueError(f"unexpected V2 profile id in {path}")

    contract = profile.get("training_contract")
    if not isinstance(contract, dict):
        raise ValueError(f"missing V2 training contract in {path}")
    if contract.get("trace_source_mix") != EXPECTED_TRACE_SOURCE_MIX:
        raise ValueError("V2 trace source mix differs from preregistered weights")
    if contract.get("overall_mode_mix") != EXPECTED_MODE_MIX:
        raise ValueError("V2 mode mix differs from preregistered weights")
    if contract.get("frozen_before_controller_metrics") is not True:
        raise ValueError("V2 source weights are not marked frozen")
    split_policy = profile.get("split_policy") or {}
    if split_policy.get("ragpulse_temporal_arrival_use") != "disabled":
        raise ValueError("RAGPulse temporal arrival must remain disabled in stage3")

    sources = profile.get("source_records")
    if not isinstance(sources, dict) or set(sources) != set(EXPECTED_TRACE_SOURCE_MIX):
        raise ValueError(f"unexpected V2 training sources in {path}")
    external = profile.get("external_benchmarks") or {}
    tau3 = external.get("tau3_bench") or {}
    if tau3.get("included_in_training_profile") is not False:
        raise ValueError("tau3-bench must not enter the V2 training profile")

    for source, split_records in sources.items():
        if not isinstance(split_records, dict):
            raise ValueError(f"invalid {source} record container")
        sample_splits: dict[str, str] = {}
        session_splits: dict[str, str] = {}
        for split in SPLITS:
            records = split_records.get(split)
            if not isinstance(records, list) or not records:
                raise ValueError(f"V2 profile has no {source}/{split} records")
            for record in records:
                sample_id = str(record.get("sample_id"))
                if sample_id in sample_splits:
                    if sample_splits[sample_id] != split:
                        raise ValueError(
                            f"{source} sample split leakage detected"
                        )
                    raise ValueError(f"duplicate {source} sample ID detected")
                sample_splits[sample_id] = split
                if source == "ragpulse":
                    session_id = str(record.get("session_id"))
                    prior_session_split = session_splits.setdefault(
                        session_id, split
                    )
                    if prior_session_split != split:
                        raise ValueError("RAGPulse session split leakage detected")

    arrival_windows = profile.get("arrival_windows")
    if not isinstance(arrival_windows, dict):
        raise ValueError(f"missing V2 arrival windows in {path}")
    for split in SPLITS:
        split_windows = arrival_windows.get(split)
        if not isinstance(split_windows, dict):
            raise ValueError(f"missing V2 {split} arrival windows")
        for load in LOADS:
            windows = split_windows.get(load)
            if not isinstance(windows, list) or not windows:
                raise ValueError(f"missing V2 {split}/{load} arrival windows")


@lru_cache(maxsize=8)
def load_profile(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        raise FileNotFoundError(f"V2 profile not found: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError(f"V2 profile must be a JSON object: {path}")
    _validate_profile(profile, path)
    return profile


def sample_trace_records(
    profile_path: str | os.PathLike[str],
    phase: str,
    count: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Sample profile records only; this does not create simulator workflows."""
    if phase not in PHASE_TO_SPLIT:
        raise ValueError(f"unknown V2 profile phase: {phase}")
    if count < 0:
        raise ValueError("sample count must be non-negative")
    profile = load_profile(str(Path(profile_path).resolve()))
    split = PHASE_TO_SPLIT[phase]
    counts = _weighted_counts(count, EXPECTED_TRACE_SOURCE_MIX)
    rng = random.Random(seed)
    sampled: list[dict[str, Any]] = []
    for source in ("tracelab", "ragpulse"):
        records = profile["source_records"][source][split]
        for _ in range(counts[source]):
            row = dict(rng.choice(records))
            row["record_source"] = source
            row["source_split"] = split
            sampled.append(row)
    rng.shuffle(sampled)
    return sampled


def _weighted_counts(count: int, weights: dict[str, float]) -> dict[str, int]:
    """Allocate an exact integer total with deterministic largest remainders."""
    raw = {key: count * weight for key, weight in weights.items()}
    counts = {key: math.floor(value) for key, value in raw.items()}
    remainder = count - sum(counts.values())
    ranked = sorted(
        weights,
        key=lambda key: (raw[key] - counts[key], weights[key], key),
        reverse=True,
    )
    for key in ranked[:remainder]:
        counts[key] += 1
    return counts


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _scaled_size(
    value: float,
    anchor: float,
    base: float,
    exponent: float,
) -> float:
    ratio = max(1e-6, value) / max(1e-6, anchor)
    return _clamp(base * ratio**exponent, max(2.0, base * 0.20), base * 4.0)


def _select_arrivals(
    rng: random.Random,
    offsets: list[float],
    target_count: int,
    duration: int,
) -> list[int]:
    """Repeat empirical gaps when needed, then normalize to simulator time."""
    count = max(0, target_count)
    if count == 0:
        return []
    if not offsets:
        raise ValueError("V2 arrival window has no offsets")

    ordered = sorted(float(value) for value in offsets)
    if len(ordered) > count:
        start = rng.randrange(0, len(ordered) - count + 1)
        selected = ordered[start : start + count]
    elif len(ordered) < count and len(ordered) > 1:
        gaps = [
            max(0.0, right - left)
            for left, right in zip(ordered, ordered[1:])
        ]
        selected = [ordered[0]]
        gap_start = rng.randrange(len(gaps))
        for index in range(count - 1):
            selected.append(selected[-1] + gaps[(gap_start + index) % len(gaps)])
    else:
        selected = ordered

    origin = float(selected[0])
    relative = [max(0.0, float(value) - origin) for value in selected]
    span = relative[-1] if relative else 0.0
    if span <= 0.0:
        step = max(1.0, duration * 0.92 / max(1, count))
        return [
            max(1, min(duration - 1, int(round((index + 1) * step))))
            for index in range(count)
        ]
    scale = duration * 0.92 / span
    arrivals = [
        max(1, min(duration - 1, int(round(value * scale)) + 1))
        for value in relative
    ]
    return sorted(arrivals)


def _sample_assignments(
    profile: dict[str, Any],
    phase: str,
    count: int,
    rng: random.Random,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Create exact mode/source counts and sample records without replacement."""
    split = PHASE_TO_SPLIT[phase]
    mode_counts = _weighted_counts(count, EXPECTED_MODE_MIX[phase])
    source_counts = _weighted_counts(count, EXPECTED_TRACE_SOURCE_MIX)
    rag_target = source_counts["ragpulse"]

    rag_raw = {
        mode: mode_count * EXPECTED_TRACE_SOURCE_MIX["ragpulse"]
        for mode, mode_count in mode_counts.items()
    }
    rag_by_mode = {mode: math.floor(value) for mode, value in rag_raw.items()}
    rag_remainder = rag_target - sum(rag_by_mode.values())
    ranked_modes = sorted(
        mode_counts,
        key=lambda mode: (
            rag_raw[mode] - rag_by_mode[mode],
            mode_counts[mode],
            mode,
        ),
        reverse=True,
    )
    for mode in ranked_modes[:rag_remainder]:
        rag_by_mode[mode] += 1

    sampled_by_source: dict[str, list[dict[str, Any]]] = {}
    for source, source_count in source_counts.items():
        records = profile["source_records"][source][split]
        if source_count <= len(records):
            sampled_by_source[source] = rng.sample(records, source_count)
        else:
            sampled_by_source[source] = [
                rng.choice(records) for _ in range(source_count)
            ]

    source_offsets = {source: 0 for source in sampled_by_source}
    assignments: list[tuple[str, str, dict[str, Any]]] = []
    for mode, mode_count in mode_counts.items():
        counts = {
            "ragpulse": rag_by_mode[mode],
            "tracelab": mode_count - rag_by_mode[mode],
        }
        for source in ("tracelab", "ragpulse"):
            start = source_offsets[source]
            end = start + counts[source]
            assignments.extend(
                (mode, source, record)
                for record in sampled_by_source[source][start:end]
            )
            source_offsets[source] = end
    if len(assignments) != count:
        raise AssertionError("V2 assignment allocation changed the workload count")
    rng.shuffle(assignments)
    return assignments


def _trace_base_record(
    rng: random.Random,
    record: dict[str, Any],
    duration_anchor_ms: float,
) -> dict[str, Any]:
    current_input_chars = float(record.get("current_input_chars") or 0.0)
    output_tokens = float(record.get("output_tokens") or 0.0)
    reasoning_tokens = float(record.get("reasoning_output_tokens") or 0.0)
    prefix_tokens = float(record.get("prefix_tokens") or 0.0)
    source_duration_ms = float(
        record.get("round_duration_ms") or duration_anchor_ms
    )

    branches: list[dict[str, Any]] = []
    raw_tools = record.get("tools") if isinstance(record.get("tools"), list) else []
    for tool in raw_tools[:7]:
        if not isinstance(tool, dict):
            continue
        service_type = str(tool.get("service_type") or "tool")
        if service_type not in SERVICE_BASE_SIZE:
            service_type = "tool"
        latency_ms = float(tool.get("latency_ms") or 196.0)
        size = _scaled_size(
            latency_ms + 1.0,
            196.0,
            SERVICE_BASE_SIZE[service_type],
            0.22,
        )
        if bool(tool.get("is_error")):
            size *= 1.12
        branches.append({"service_type": service_type, "size": size})

    complexity_scale = _clamp((output_tokens + 32.0) / 246.0, 0.45, 3.0)
    while len(branches) < 7:
        service_type = rng.choice(FILL_SERVICE_TYPES)
        size = (
            SERVICE_BASE_SIZE[service_type]
            * complexity_scale**0.16
            * rng.uniform(0.75, 1.25)
        )
        branches.append({"service_type": service_type, "size": size})

    judge_signal = reasoning_tokens if reasoning_tokens > 0.0 else output_tokens * 0.10
    duration_scale = _clamp(
        (max(1.0, source_duration_ms) / max(1.0, duration_anchor_ms)) ** 0.18,
        0.72,
        1.55,
    )
    background_base = _scaled_size(prefix_tokens + 1.0, 118656.0, 78.0, 0.12)
    return {
        "template": "coding",
        "planner_size": _scaled_size(
            current_input_chars + 1.0, 4000.0, 6.0, 0.18
        ),
        "branches": branches[:7],
        "llm_size": _scaled_size(output_tokens + 1.0, 214.0, 46.0, 0.28),
        "judge_size": _scaled_size(judge_signal + 1.0, 18.0, 14.0, 0.20),
        "background_sizes": [
            max(2.0, background_base * rng.uniform(0.85, 1.15))
            for _ in range(3)
        ],
        "deadline_base": 300.0 * duration_scale,
    }


def _rag_base_record(
    rng: random.Random,
    record: dict[str, Any],
) -> dict[str, Any]:
    input_tokens = float(record.get("input_tokens") or 0.0)
    output_tokens = float(record.get("output_tokens") or 0.0)
    retrieval_documents = float(record.get("retrieval_document_count") or 0.0)
    history_components = float(record.get("history_component_count") or 0.0)
    web_search_components = float(record.get("web_search_component_count") or 0.0)

    input_scale = _clamp((input_tokens + 1.0) / 3140.5, 0.30, 3.0)
    document_scale = _clamp((retrieval_documents + 1.0) / 7.0, 0.25, 1.50)
    output_scale = _clamp((output_tokens + 1.0) / 235.0, 0.20, 4.0)
    branches = []
    for service_type in RAG_BRANCH_TYPES:
        if service_type == "retrieval":
            size = 28.0 * input_scale**0.12 * document_scale**0.18
        elif service_type == "llm":
            size = 46.0 * output_scale**0.28
        else:
            size = 42.0 * input_scale**0.08 * (1.0 + 0.10 * web_search_components)
        branches.append(
            {
                "service_type": service_type,
                "size": max(2.0, size * rng.uniform(0.88, 1.12)),
            }
        )

    context_signal = input_tokens + 384.0 * history_components
    background_base = _scaled_size(context_signal + 1.0, 3524.0, 78.0, 0.12)
    work_signal = (input_scale + document_scale + output_scale) / 3.0
    return {
        "template": "rag_qa",
        "planner_size": _scaled_size(input_tokens + 1.0, 3140.5, 6.0, 0.18),
        "branches": branches,
        "llm_size": _scaled_size(output_tokens + 1.0, 235.0, 46.0, 0.28),
        "judge_size": _scaled_size(
            output_tokens * 0.10 + 1.0, 24.5, 14.0, 0.20
        ),
        "background_sizes": [
            max(2.0, background_base * rng.uniform(0.85, 1.15))
            for _ in range(2)
        ],
        "deadline_base": 230.0 * _clamp(work_signal**0.12, 0.82, 1.25),
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
    if record_source == "tracelab":
        mapped = _trace_base_record(rng, record, duration_anchor_ms)
    elif record_source == "ragpulse":
        mapped = _rag_base_record(rng, record)
    else:
        raise ValueError(f"unsupported V2 record source: {record_source}")

    size_multiplier = 1.0
    deadline_multiplier = 1.0
    if mode == "augmented":
        size_multiplier = rng.uniform(0.82, 1.28)
        deadline_multiplier = rng.uniform(0.88, 1.15)
    elif mode == "stress":
        size_multiplier = rng.uniform(1.30, 1.75)
        deadline_multiplier = rng.uniform(0.55, 0.72)
    elif mode != "trace":
        raise ValueError(f"unsupported V2 workload mode: {mode}")

    branches = []
    required_count = 3
    for index, branch in enumerate(mapped["branches"]):
        branches.append(
            {
                "service_type": str(branch["service_type"]),
                "size": max(2.0, float(branch["size"]) * size_multiplier),
                "required": index < required_count,
            }
        )
    return {
        "workflow_id": workflow_id,
        "arrival_time": arrival_time,
        "template": str(mapped["template"]),
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
        "record_source": record_source,
        "source_split": split,
        "source_record_id": str(record["sample_id"]),
        "mapping_version": "fixed_template_v2_a",
    }


def generate_trace_workload(
    profile_path: str | os.PathLike[str] | None,
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
    target_count: int,
    phase: str,
) -> list[dict[str, Any]]:
    """Map the frozen V2 profile to the simulator's fixed workflow templates."""
    if phase not in PHASE_TO_SPLIT:
        raise ValueError(f"unknown V2 workload phase: {phase}")
    if load not in LOADS:
        raise ValueError(f"unknown V2 workload load: {load}")
    path = resolve_profile_path(profile_path)
    profile = load_profile(str(path))
    split = PHASE_TO_SPLIT[phase]
    rng = random.Random(seed)
    window = rng.choice(profile["arrival_windows"][split][load])
    count = min(max_workflows, max(1, target_count))
    arrivals = _select_arrivals(
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
