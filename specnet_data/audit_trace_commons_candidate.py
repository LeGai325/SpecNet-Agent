#!/usr/bin/env python3
"""Audit a Trace Commons Parquet snapshot without exporting raw trace text."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PRIVACY_PATTERNS = {
    "email_like": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "home_path_like": re.compile(r"(?:/Users/|/home/)[^/\s]+"),
    "private_key_marker": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    "github_token_like": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "api_key_like": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate-only preflight audit for Trace Commons Parquet data."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    return parser.parse_args()


def parse_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from iter_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_strings(nested)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round(fraction * (len(ordered) - 1))
    return ordered[index]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - environment-dependent message
        raise SystemExit("pyarrow is required to read the candidate Parquet file") from exc

    rows = parquet.read_table(args.input).to_pylist()
    harnesses: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    message_roles: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    privacy_signals: Counter[str] = Counter()

    trace_event_count = 0
    timestamped_event_count = 0
    malformed_timestamps = 0
    uuid_event_count = 0
    parent_link_count = 0
    internal_parent_link_count = 0
    declared_tool_calls = 0
    observed_tool_uses = 0
    observed_tool_results = 0
    paired_tool_calls = 0
    nonnegative_tool_durations = 0
    tool_durations_ms: list[float] = []
    session_durations_s: list[float] = []

    for row in rows:
        harnesses[row.get("harness") or "<missing>"] += 1
        declared_tool_calls += row.get("num_tool_calls") or 0
        events = [parse_json(event) for event in (row.get("trace") or [])]
        messages = [parse_json(message) for message in (row.get("messages") or [])]
        tools = [parse_json(tool) for tool in (row.get("tools") or [])]
        metadata = parse_json(row.get("metadata")) if row.get("metadata") else {}

        for message in messages:
            if isinstance(message, dict):
                message_roles[message.get("role") or "<missing>"] += 1

        ids = {
            event.get("uuid")
            for event in events
            if isinstance(event, dict) and event.get("uuid")
        }
        uuid_event_count += len(ids)
        tool_starts: dict[str, datetime] = {}
        tool_ends: dict[str, datetime] = {}
        timestamps: list[datetime] = []

        for event in events:
            if not isinstance(event, dict):
                continue
            trace_event_count += 1
            event_types[event.get("type") or "<missing>"] += 1
            parent = event.get("parentUuid")
            if parent:
                parent_link_count += 1
                internal_parent_link_count += int(parent in ids)

            raw_timestamp = event.get("timestamp")
            timestamp = parse_timestamp(raw_timestamp)
            if raw_timestamp:
                timestamped_event_count += 1
                if timestamp is None:
                    malformed_timestamps += 1
                else:
                    timestamps.append(timestamp)

            message = event.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list) or timestamp is None:
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("id"):
                    observed_tool_uses += 1
                    tool_starts[block["id"]] = timestamp
                    tool_names[block.get("name") or "<missing>"] += 1
                elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                    observed_tool_results += 1
                    tool_ends[block["tool_use_id"]] = timestamp

        for tool_id, start in tool_starts.items():
            end = tool_ends.get(tool_id)
            if end is None:
                continue
            paired_tool_calls += 1
            duration_ms = (end - start).total_seconds() * 1000.0
            nonnegative_tool_durations += int(duration_ms >= 0)
            if duration_ms >= 0:
                tool_durations_ms.append(duration_ms)

        if len(timestamps) >= 2:
            session_durations_s.append((max(timestamps) - min(timestamps)).total_seconds())

        # Count only the presence of suspicious patterns; never export matching text.
        scan_values = [events, messages, tools, metadata, row.get("prompt") or ""]
        for text in iter_strings(scan_values):
            for name, pattern in PRIVACY_PATTERNS.items():
                privacy_signals[name] += len(pattern.findall(text))

    report = {
        "schema_version": 1,
        "dataset_id": "trace_commons_agent_traces",
        "revision": args.revision,
        "input": {
            "filename": args.input.name,
            "bytes": args.input.stat().st_size,
            "sha256": sha256(args.input),
        },
        "rows": len(rows),
        "harnesses": dict(sorted(harnesses.items())),
        "messages": {
            "role_counts": dict(sorted(message_roles.items())),
        },
        "events": {
            "count": trace_event_count,
            "type_counts": dict(sorted(event_types.items())),
            "timestamped": timestamped_event_count,
            "timestamp_coverage": (
                timestamped_event_count / trace_event_count if trace_event_count else 0.0
            ),
            "malformed_timestamps": malformed_timestamps,
            "uuid_events": uuid_event_count,
            "parent_links": parent_link_count,
            "internal_parent_links": internal_parent_link_count,
            "internal_parent_link_coverage": (
                internal_parent_link_count / parent_link_count if parent_link_count else 0.0
            ),
        },
        "tools": {
            "declared_calls": declared_tool_calls,
            "observed_uses": observed_tool_uses,
            "observed_results": observed_tool_results,
            "paired_calls": paired_tool_calls,
            "pair_coverage": paired_tool_calls / observed_tool_uses if observed_tool_uses else 0.0,
            "nonnegative_duration_pairs": nonnegative_tool_durations,
            "duration_ms": {
                "p50": percentile(tool_durations_ms, 0.50),
                "p95": percentile(tool_durations_ms, 0.95),
                "p99": percentile(tool_durations_ms, 0.99),
                "max": max(tool_durations_ms, default=None),
            },
            "top_names": dict(tool_names.most_common(20)),
        },
        "session_duration_s": {
            "available": len(session_durations_s),
            "p50": percentile(session_durations_s, 0.50),
            "p95": percentile(session_durations_s, 0.95),
            "max": max(session_durations_s, default=None),
        },
        "privacy_signal_counts": dict(sorted(privacy_signals.items())),
        "privacy_note": (
            "Pattern counts are conservative indicators, not confirmed leaks; raw data remains quarantined."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
