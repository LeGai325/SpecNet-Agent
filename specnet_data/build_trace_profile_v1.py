#!/usr/bin/env python3
"""Build a compact, text-free TraceLab + BurstGPT profile for simulator use."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


PROFILE_ID = "trace_driven_v1"
PROFILE_SCHEMA_VERSION = 1
SPLITS = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")
SPLIT_RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_score(text: str) -> int:
    return int(hashlib.sha256(f"{PROFILE_ID}:{text}".encode("utf-8")).hexdigest(), 16)


def stable_id(text: str) -> str:
    return hashlib.sha256(f"{PROFILE_ID}:{text}".encode("utf-8")).hexdigest()[:16]


def assign_split(key: str) -> str:
    fraction = stable_score(key) / float(2**256)
    if fraction < SPLIT_RATIOS["train"]:
        return "train"
    if fraction < SPLIT_RATIOS["train"] + SPLIT_RATIOS["validation"]:
        return "validation"
    return "test"


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
        "mean": sum(values) / len(values) if values else None,
    }


class BottomK:
    """Keep records with the lowest deterministic hash scores."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.heap: list[tuple[int, int, Any]] = []
        self.counter = 0

    def add(self, score: int, value: Any) -> None:
        item = (-score, self.counter, value)
        self.counter += 1
        if len(self.heap) < self.limit:
            heapq.heappush(self.heap, item)
        elif score < -self.heap[0][0]:
            heapq.heapreplace(self.heap, item)

    def values(self) -> list[Any]:
        return [item[2] for item in sorted(self.heap, key=lambda item: -item[0])]


class Reservoir:
    def __init__(self, limit: int, seed: int) -> None:
        self.limit = limit
        self.rng = random.Random(seed)
        self.values: list[Any] = []
        self.seen = 0

    def add(self, value: Any) -> None:
        self.seen += 1
        if len(self.values) < self.limit:
            self.values.append(value)
            return
        index = self.rng.randrange(self.seen)
        if index < self.limit:
            self.values[index] = value


def parse_iso_ms(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000.0
    except ValueError:
        return None


def trace_round_duration_ms(row: dict[str, Any]) -> float:
    timestamps = [
        timestamp
        for event in row.get("timing_events", [])
        if isinstance(event, dict)
        for timestamp in [parse_iso_ms(event.get("timestamp"))]
        if timestamp is not None
    ]
    if len(timestamps) >= 2:
        return max(1.0, max(timestamps) - min(timestamps))
    tool_latency = sum(
        float(tool.get("tool_wall_latency_ms") or 0.0)
        for tool in row.get("tools", [])
        if isinstance(tool, dict)
    )
    return max(1.0, tool_latency)


def map_tool_service(tool_name: Any) -> str:
    name = str(tool_name or "").lower()
    if any(token in name for token in ("read", "grep", "glob", "search", "fetch", "list_mcp")):
        return "retrieval"
    if any(token in name for token in ("write", "edit", "patch", "notebook")):
        return "storage"
    if any(token in name for token in ("agent", "spawn", "resume_agent")):
        return "llm"
    return "tool"


def numeric(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_tracelab_profile(
    path: Path,
    sample_caps: dict[str, int],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    samplers = {split: BottomK(sample_caps[split]) for split in SPLITS}
    providers: Counter[str] = Counter()
    split_rows: Counter[str] = Counter()
    split_sessions: dict[str, set[str]] = {split: set() for split in SPLITS}
    rows = 0
    invalid_rows = 0
    tools = 0
    duration_sample = Reservoir(100000, 1001)
    output_token_sample = Reservoir(100000, 1002)
    tool_latency_sample = Reservoir(100000, 1003)

    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                invalid_rows += 1
                continue
            if not isinstance(row, dict):
                invalid_rows += 1
                continue
            rows += 1
            session_id = str(row.get("session_id") or "missing-session")
            round_id = str(row.get("round_id") or f"row-{rows}")
            source_key = f"{session_id}:{round_id}:{rows}"
            split = assign_split(f"tracelab-session:{session_id}")
            split_rows[split] += 1
            split_sessions[split].add(session_id)
            provider = str(row.get("provider") or "unknown")
            providers[provider] += 1
            duration_ms = trace_round_duration_ms(row)
            duration_sample.add(duration_ms)
            output_tokens = numeric(row.get("output_tokens"))
            output_token_sample.add(output_tokens)

            mapped_tools: list[dict[str, Any]] = []
            for tool in row.get("tools", []):
                if not isinstance(tool, dict):
                    continue
                tools += 1
                latency_ms = numeric(
                    tool.get("tool_wall_latency_ms"),
                    numeric(tool.get("tool_internal_latency_ms"), 196.0),
                )
                latency_ms = max(0.0, latency_ms)
                tool_latency_sample.add(latency_ms)
                if len(mapped_tools) < 7:
                    mapped_tools.append(
                        {
                            "service_type": map_tool_service(tool.get("tool_name")),
                            "latency_ms": round(latency_ms, 3),
                            "is_error": bool(tool.get("is_error")),
                        }
                    )

            record = {
                "sample_id": f"tl-{stable_id(source_key)}",
                "provider": provider,
                "current_input_chars": int(numeric(row.get("current_input_chars"))),
                "input_tokens_total": int(numeric(row.get("input_tokens_total"))),
                "output_tokens": int(output_tokens),
                "reasoning_output_tokens": int(numeric(row.get("reasoning_output_tokens"))),
                "prefix_tokens": int(numeric(row.get("prefix_tokens"))),
                "newly_append_tokens": int(numeric(row.get("newly_append_tokens"))),
                "round_duration_ms": round(duration_ms, 3),
                "tools": mapped_tools,
            }
            samplers[split].add(stable_score(f"tracelab-round:{source_key}"), record)

    records = {split: samplers[split].values() for split in SPLITS}
    stats = {
        "rows": rows,
        "invalid_rows": invalid_rows,
        "tool_records": tools,
        "providers": dict(providers),
        "split_rows": dict(split_rows),
        "split_sessions": {split: len(split_sessions[split]) for split in SPLITS},
        "sampled_records": {split: len(records[split]) for split in SPLITS},
        "round_duration_ms_sample": distribution(duration_sample.values),
        "output_tokens_sample": distribution(output_token_sample.values),
        "tool_latency_ms_sample": distribution(tool_latency_sample.values),
    }
    return records, stats


def burst_load_bucket(count: int, low_threshold: float, high_threshold: float) -> str:
    if count <= low_threshold:
        return "light"
    if count <= high_threshold:
        return "medium"
    return "heavy"


def iter_burst_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def burst_window_key(timestamp: float, window_seconds: int) -> tuple[int, int]:
    day_id = int(timestamp // 86400)
    second_of_day = timestamp - day_id * 86400
    slot = int(second_of_day // window_seconds)
    return day_id, slot


def build_burst_profile(
    path: Path,
    window_seconds: int,
    windows_per_split_load: int,
    max_events_per_window: int,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    window_counts: Counter[tuple[int, int]] = Counter()
    models: Counter[str] = Counter()
    log_types: Counter[str] = Counter()
    rows = 0
    invalid_rows = 0
    token_mismatches = 0
    negative_timestamp_steps = 0
    zero_response_rows = 0
    conversation_rows = 0
    previous_timestamp: float | None = None
    min_timestamp: float | None = None
    max_timestamp: float | None = None
    metric_sample = Reservoir(100000, 2001)

    for row in iter_burst_rows(path):
        rows += 1
        try:
            timestamp = float(row["Timestamp"])
            elapsed = float(row["Elapsed time"])
            request_tokens = float(row["Request tokens"])
            response_tokens = float(row["Response tokens"])
            total_tokens = float(row["Total tokens"])
        except (KeyError, TypeError, ValueError):
            invalid_rows += 1
            continue
        if previous_timestamp is not None and timestamp < previous_timestamp:
            negative_timestamp_steps += 1
        previous_timestamp = timestamp
        min_timestamp = timestamp if min_timestamp is None else min(min_timestamp, timestamp)
        max_timestamp = timestamp if max_timestamp is None else max(max_timestamp, timestamp)
        window_counts[burst_window_key(timestamp, window_seconds)] += 1
        models[str(row.get("Model") or "unknown")] += 1
        log_type = str(row.get("Log Type") or "unknown")
        log_types[log_type] += 1
        if log_type == "Conversation log":
            conversation_rows += 1
        if response_tokens == 0.0:
            zero_response_rows += 1
        if request_tokens + response_tokens != total_tokens:
            token_mismatches += 1
        metric_sample.add((elapsed, request_tokens, response_tokens, total_tokens))

    count_values = [float(value) for value in window_counts.values()]
    low_threshold = float(percentile(count_values, 1.0 / 3.0) or 0.0)
    high_threshold = float(percentile(count_values, 2.0 / 3.0) or low_threshold)
    window_samplers = {
        (split, load): BottomK(windows_per_split_load)
        for split in SPLITS
        for load in LOADS
    }
    for window_key, count in window_counts.items():
        day_id, slot = window_key
        split = assign_split(f"burst-day:{day_id}")
        load = burst_load_bucket(count, low_threshold, high_threshold)
        window_samplers[(split, load)].add(
            stable_score(f"burst-window:{day_id}:{slot}"),
            window_key,
        )

    selected = {
        window_key
        for sampler in window_samplers.values()
        for window_key in sampler.values()
    }
    if any(not window_samplers[(split, load)].heap for split in SPLITS for load in LOADS):
        raise ValueError("BurstGPT window split produced an empty split/load bucket")

    collected: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row in iter_burst_rows(path):
        try:
            timestamp = float(row["Timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        window_key = burst_window_key(timestamp, window_seconds)
        if window_key in selected:
            day_id, slot = window_key
            window_start = day_id * 86400 + slot * window_seconds
            collected[window_key].append(timestamp - window_start)

    arrival_windows: dict[str, dict[str, list[dict[str, Any]]]] = {
        split: {load: [] for load in LOADS} for split in SPLITS
    }
    for split in SPLITS:
        for load in LOADS:
            for window_key in window_samplers[(split, load)].values():
                day_id, slot = window_key
                offsets = collected[window_key]
                source_count = len(offsets)
                if len(offsets) > max_events_per_window:
                    width = len(offsets) - max_events_per_window + 1
                    start = stable_score(f"burst-slice:{day_id}:{slot}") % width
                    offsets = offsets[start : start + max_events_per_window]
                arrival_windows[split][load].append(
                    {
                        "window_id": f"bg-{stable_id(f'{day_id}:{slot}')}",
                        "source_count": source_count,
                        "arrival_offsets": [round(value, 3) for value in offsets],
                    }
                )

    elapsed_values = [value[0] for value in metric_sample.values]
    request_values = [value[1] for value in metric_sample.values]
    response_values = [value[2] for value in metric_sample.values]
    total_values = [value[3] for value in metric_sample.values]
    stats = {
        "rows": rows,
        "invalid_rows": invalid_rows,
        "models": dict(models),
        "log_types": dict(log_types),
        "conversation_rows": conversation_rows,
        "zero_response_rows": zero_response_rows,
        "token_mismatches": token_mismatches,
        "negative_timestamp_steps": negative_timestamp_steps,
        "timestamp_min": min_timestamp,
        "timestamp_max": max_timestamp,
        "window_seconds": window_seconds,
        "nonempty_windows": len(window_counts),
        "window_request_count": distribution(count_values),
        "load_thresholds": {
            "light_at_or_below": low_threshold,
            "medium_at_or_below": high_threshold,
        },
        "selected_windows": {
            split: {
                load: len(arrival_windows[split][load]) for load in LOADS
            }
            for split in SPLITS
        },
        "elapsed_seconds_sample": distribution(elapsed_values),
        "request_tokens_sample": distribution(request_values),
        "response_tokens_sample": distribution(response_values),
        "total_tokens_sample": distribution(total_values),
    }
    return arrival_windows, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracelab", type=Path, required=True)
    parser.add_argument("--burstgpt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-train-cap", type=int, default=20000)
    parser.add_argument("--trace-validation-cap", type=int, default=5000)
    parser.add_argument("--trace-test-cap", type=int, default=5000)
    parser.add_argument("--window-seconds", type=int, default=2600)
    parser.add_argument("--windows-per-split-load", type=int, default=48)
    parser.add_argument("--max-events-per-window", type=int, default=512)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for path in (args.tracelab, args.burstgpt):
        if not path.is_file():
            raise SystemExit(f"missing input: {path}")
    sample_caps = {
        "train": args.trace_train_cap,
        "validation": args.trace_validation_cap,
        "test": args.trace_test_cap,
    }
    if any(value <= 0 for value in sample_caps.values()):
        raise SystemExit("trace sample caps must be positive")

    workflow_records, tracelab_stats = build_tracelab_profile(args.tracelab, sample_caps)
    arrival_windows, burst_stats = build_burst_profile(
        args.burstgpt,
        args.window_seconds,
        args.windows_per_split_load,
        args.max_events_per_window,
    )
    duration_anchor = float(
        tracelab_stats["round_duration_ms_sample"].get("p50") or 30000.0
    )
    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "sources": {
            "tracelab": {
                "release": "v0.0.1",
                "filename": args.tracelab.name,
                "sha256": sha256(args.tracelab),
            },
            "burstgpt": {
                "release": "v2.0",
                "filename": args.burstgpt.name,
                "sha256": sha256(args.burstgpt),
            },
        },
        "split_policy": {
            "ratios": SPLIT_RATIOS,
            "tracelab_unit": "session_id",
            "burstgpt_unit": "day",
            "identifiers_in_profile": "salted_hash_only",
        },
        "phase_mix": {
            "train": {"trace": 0.60, "augmented": 0.25, "stress": 0.15},
            "validation": {"trace": 0.70, "stress": 0.30},
            "test": {"trace": 1.00},
        },
        "mapping": {
            "simulator_template": "coding",
            "round_duration_anchor_ms": duration_anchor,
            "branch_count": 7,
            "required_branch_count": 3,
            "deadline_source": "simulated_from_trace_complexity_not_observed_slo",
            "network_source": "simulator",
        },
        "workflow_records": workflow_records,
        "arrival_windows": arrival_windows,
        "stats": {
            "tracelab": tracelab_stats,
            "burstgpt": burst_stats,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
                "workflow_records": {
                    split: len(workflow_records[split]) for split in SPLITS
                },
                "arrival_windows": burst_stats["selected_windows"],
                "tracelab_rows": tracelab_stats["rows"],
                "burstgpt_rows": burst_stats["rows"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
