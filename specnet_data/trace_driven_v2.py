"""Validation and profile-only sampling for trace-driven workload V2."""

from __future__ import annotations

import json
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


def default_profile_path() -> Path:
    data_root = os.environ.get("SPECNET_DATA_ROOT")
    if not data_root:
        raise ValueError(
            "trace_driven_v2 requires a profile path or SPECNET_DATA_ROOT"
        )
    return Path(data_root) / "processed" / PROFILE_ID / "profile.json"


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
    ragpulse_count = round(count * EXPECTED_TRACE_SOURCE_MIX["ragpulse"])
    counts = {
        "ragpulse": ragpulse_count,
        "tracelab": count - ragpulse_count,
    }
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
