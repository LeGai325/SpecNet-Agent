"""Runtime adapter for the compact TraceLab + BurstGPT workload profile."""

from __future__ import annotations

import json
import math
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any


PROFILE_ID = "trace_driven_v1"
PROFILE_SCHEMA_VERSION = 1
PHASE_TO_SPLIT = {
    "train": "train",
    "validation": "validation",
    "test": "test",
}
PHASE_MIX = {
    "train": (("trace", 0.60), ("augmented", 0.25), ("stress", 0.15)),
    "validation": (("trace", 0.70), ("stress", 0.30)),
    "test": (("trace", 1.00),),
}
SERVICE_BASE_SIZE = {
    "retrieval": 28.0,
    "tool": 42.0,
    "storage": 64.0,
    "llm": 46.0,
}
FILL_SERVICE_TYPES = ("retrieval", "tool", "storage", "llm")


def default_profile_path() -> Path:
    data_root = os.environ.get("SPECNET_DATA_ROOT")
    if not data_root:
        raise ValueError(
            "trace_driven_v1 requires --trace-profile-path or SPECNET_DATA_ROOT"
        )
    return Path(data_root) / "processed" / PROFILE_ID / "profile.json"


def resolve_profile_path(path: str | os.PathLike[str] | None) -> Path:
    return Path(path).expanduser().resolve() if path else default_profile_path().resolve()


def _validate_profile(profile: dict[str, Any], path: Path) -> None:
    if profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"unsupported trace profile schema in {path}")
    if profile.get("profile_id") != PROFILE_ID:
        raise ValueError(f"unexpected trace profile id in {path}")
    workflow_records = profile.get("workflow_records")
    arrival_windows = profile.get("arrival_windows")
    if not isinstance(workflow_records, dict) or not isinstance(arrival_windows, dict):
        raise ValueError(f"incomplete trace profile in {path}")
    sample_ids: set[str] = set()
    for split in PHASE_TO_SPLIT.values():
        records = workflow_records.get(split)
        if not isinstance(records, list) or not records:
            raise ValueError(f"trace profile has no {split} workflow records")
        current_ids = {str(record.get("sample_id")) for record in records}
        if len(current_ids) != len(records):
            raise ValueError(f"duplicate sample ids in {split} workflow records")
        if sample_ids.intersection(current_ids):
            raise ValueError("trace workflow split leakage detected")
        sample_ids.update(current_ids)
        split_windows = arrival_windows.get(split)
        if not isinstance(split_windows, dict):
            raise ValueError(f"trace profile has no {split} arrival windows")
        for load in ("light", "medium", "heavy"):
            windows = split_windows.get(load)
            if not isinstance(windows, list) or not windows:
                raise ValueError(f"trace profile has no {split}/{load} arrival windows")


@lru_cache(maxsize=8)
def load_profile(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    if not path.is_file():
        raise FileNotFoundError(f"trace profile not found: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError(f"trace profile must be a JSON object: {path}")
    _validate_profile(profile, path)
    return profile


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _scaled_size(value: float, anchor: float, base: float, exponent: float) -> float:
    ratio = max(1e-6, value) / max(1e-6, anchor)
    return _clamp(base * ratio**exponent, max(2.0, base * 0.20), base * 4.0)


def _choose_mode(rng: random.Random, phase: str) -> str:
    draw = rng.random()
    cumulative = 0.0
    for mode, weight in PHASE_MIX[phase]:
        cumulative += weight
        if draw <= cumulative:
            return mode
    return PHASE_MIX[phase][-1][0]


def _select_arrivals(
    rng: random.Random,
    offsets: list[float],
    target_count: int,
    duration: int,
    fill_to_target: bool = True,
) -> list[int]:
    count = max(0, target_count)
    if count == 0:
        return []
    if not offsets:
        raise ValueError("trace arrival window has no offsets")

    ordered = sorted(float(value) for value in offsets)
    if not fill_to_target:
        count = min(count, len(ordered))
    if len(ordered) > count:
        start = rng.randrange(0, len(ordered) - count + 1)
        selected = ordered[start : start + count]
    elif len(ordered) < count and len(ordered) > 1:
        # A low-density real window can contain fewer arrivals than the
        # simulator's target count. Repeat its ordered empirical gap pattern
        # before time-normalization so that load levels stay comparable while
        # preserving simultaneous arrivals and local burst structure.
        gaps = [max(0.0, right - left) for left, right in zip(ordered, ordered[1:])]
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
        return [max(1, min(duration - 1, int(round((index + 1) * step)))) for index in range(count)]
    scale = duration * 0.92 / span
    arrivals = [max(1, min(duration - 1, int(round(value * scale)) + 1)) for value in relative]
    return sorted(arrivals)


def _map_record(
    rng: random.Random,
    record: dict[str, Any],
    arrival_time: int,
    workflow_id: int,
    split: str,
    mode: str,
    duration_anchor_ms: float,
) -> dict[str, Any]:
    current_input_chars = float(record.get("current_input_chars") or 0.0)
    output_tokens = float(record.get("output_tokens") or 0.0)
    reasoning_tokens = float(record.get("reasoning_output_tokens") or 0.0)
    prefix_tokens = float(record.get("prefix_tokens") or 0.0)
    source_duration_ms = float(record.get("round_duration_ms") or duration_anchor_ms)

    planner_size = _scaled_size(current_input_chars + 1.0, 4000.0, 6.0, 0.18)
    llm_size = _scaled_size(output_tokens + 1.0, 214.0, 46.0, 0.28)
    judge_signal = reasoning_tokens if reasoning_tokens > 0.0 else output_tokens * 0.10
    judge_size = _scaled_size(judge_signal + 1.0, 18.0, 14.0, 0.20)

    branches: list[dict[str, Any]] = []
    raw_tools = record.get("tools") if isinstance(record.get("tools"), list) else []
    for tool in raw_tools[:7]:
        if not isinstance(tool, dict):
            continue
        service_type = str(tool.get("service_type") or "tool")
        if service_type not in SERVICE_BASE_SIZE:
            service_type = "tool"
        latency_ms = float(tool.get("latency_ms") or 196.0)
        size = _scaled_size(latency_ms + 1.0, 196.0, SERVICE_BASE_SIZE[service_type], 0.22)
        if bool(tool.get("is_error")):
            size *= 1.12
        branches.append({"service_type": service_type, "size": size})

    complexity_scale = _clamp((output_tokens + 32.0) / 246.0, 0.45, 3.0)
    while len(branches) < 7:
        service_type = rng.choice(FILL_SERVICE_TYPES)
        base = SERVICE_BASE_SIZE[service_type]
        size = base * complexity_scale**0.16 * rng.uniform(0.75, 1.25)
        branches.append({"service_type": service_type, "size": size})
    branches = branches[:7]

    size_multiplier = 1.0
    deadline_multiplier = 1.0
    if mode == "augmented":
        size_multiplier = rng.uniform(0.82, 1.28)
        deadline_multiplier = rng.uniform(0.88, 1.15)
    elif mode == "stress":
        size_multiplier = rng.uniform(1.30, 1.75)
        deadline_multiplier = rng.uniform(0.55, 0.72)

    planner_size *= size_multiplier
    llm_size *= size_multiplier
    judge_size *= size_multiplier
    for branch in branches:
        branch["size"] = max(2.0, float(branch["size"]) * size_multiplier)

    background_base = _scaled_size(prefix_tokens + 1.0, 118656.0, 78.0, 0.12)
    background_sizes = [
        max(2.0, background_base * size_multiplier * rng.uniform(0.85, 1.15))
        for _ in range(3)
    ]
    duration_scale = _clamp(
        (max(1.0, source_duration_ms) / max(1.0, duration_anchor_ms)) ** 0.18,
        0.72,
        1.55,
    )
    deadline = 300.0 * duration_scale * deadline_multiplier * rng.uniform(0.94, 1.06)

    return {
        "workflow_id": workflow_id,
        "arrival_time": arrival_time,
        "template": "coding",
        "deadline": deadline,
        "planner_size": planner_size,
        "branches": [
            {
                "service_type": branch["service_type"],
                "size": branch["size"],
                "required": index < 3,
            }
            for index, branch in enumerate(branches)
        ],
        "llm_size": llm_size,
        "judge_size": judge_size,
        "background_sizes": background_sizes,
        "workload_source": mode,
        "source_split": split,
        "source_record_id": str(record["sample_id"]),
    }


def generate_trace_workload(
    profile_path: str | os.PathLike[str] | None,
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
    target_count: int,
    phase: str,
    fill_to_target: bool = False,
) -> list[dict[str, Any]]:
    if phase not in PHASE_TO_SPLIT:
        raise ValueError(f"unknown trace workload phase: {phase}")
    if load not in {"light", "medium", "heavy"}:
        raise ValueError(f"unknown trace workload load: {load}")
    path = resolve_profile_path(profile_path)
    profile = load_profile(str(path))
    split = PHASE_TO_SPLIT[phase]
    records = profile["workflow_records"][split]
    windows = profile["arrival_windows"][split][load]
    rng = random.Random(seed)
    window = rng.choice(windows)
    offsets = [float(value) for value in window["arrival_offsets"]]
    arrivals = _select_arrivals(
        rng,
        offsets,
        min(max_workflows, max(1, target_count)),
        duration,
        fill_to_target=fill_to_target,
    )
    duration_anchor_ms = float(
        profile.get("mapping", {}).get("round_duration_anchor_ms", 30000.0)
    )
    return [
        _map_record(
            rng=rng,
            record=rng.choice(records),
            arrival_time=arrival_time,
            workflow_id=workflow_id,
            split=split,
            mode=_choose_mode(rng, phase),
            duration_anchor_ms=duration_anchor_ms,
        )
        for workflow_id, arrival_time in enumerate(arrivals)
    ]
