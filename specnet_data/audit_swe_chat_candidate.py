#!/usr/bin/env python3
"""Run an aggregate-only 100-session preflight for gated SWE-chat data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DATASET_ID = "SALT-NLP/SWE-chat"
SAMPLE_SEED = "20260801"

GENERAL_QUOTAS = {
    "Claude Code": 48,
    "OpenCode": 10,
    "Codex": 8,
    "Gemini CLI": 5,
    "unknown": 3,
    "Cursor": 3,
    "Agent": 2,
    "other": 1,
}

TAIL_FIELDS = (
    "duration_seconds",
    "tool_call_count",
    "input_tokens",
    "output_tokens",
)

PRIVACY_PATTERNS = {
    "email_like": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "home_path_like": re.compile(r"(?:/Users/|/home/)[^/\s]+"),
    "private_key_marker": re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    "github_token_like": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "api_key_like": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate-only SWE-chat preflight; raw conversation text is never exported."
    )
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--conversations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection-output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    return parser.parse_args()


def stable_key(value: str) -> str:
    return hashlib.sha256(f"{SAMPLE_SEED}:{value}".encode()).hexdigest()


def iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from iter_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_strings(nested)


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(fraction * (len(ordered) - 1))]


def numeric_summary(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = []
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        value = float(value)
        if math.isfinite(value):
            values.append(value)
    return {
        "available": len(values),
        "zero": sum(value == 0 for value in values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values, default=None),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deduplicate_by_content_hash(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["content_hash"]].append(row)
    kept = [min(group, key=lambda row: stable_key(row["session_id"])) for group in groups.values()]
    return kept, {
        "duplicate_hash_groups": sum(len(group) > 1 for group in groups.values()),
        "rows_in_duplicate_hash_groups": sum(
            len(group) for group in groups.values() if len(group) > 1
        ),
        "rows_removed_for_sampling": len(rows) - len(kept),
    }


def select_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated, _ = deduplicate_by_content_hash(rows)
    known_agents = set(GENERAL_QUOTAS) - {"other"}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in deduplicated:
        agent = row.get("agent") or "unknown"
        bucket = agent if agent in known_agents else "other"
        buckets[bucket].append(row)

    selected: dict[str, dict[str, Any]] = {}
    for bucket, quota in GENERAL_QUOTAS.items():
        candidates = sorted(buckets[bucket], key=lambda row: stable_key(row["session_id"]))
        if len(candidates) < quota:
            raise RuntimeError(f"not enough rows for {bucket}: {len(candidates)} < {quota}")
        for row in candidates[:quota]:
            selected[row["session_id"]] = {"row": row, "stratum": f"general:{bucket}"}

    for field in TAIL_FIELDS:
        added = 0
        candidates = sorted(
            deduplicated,
            key=lambda row: (
                float(row.get(field) or 0),
                stable_key(row["session_id"]),
            ),
            reverse=True,
        )
        for row in candidates:
            if row["session_id"] in selected:
                continue
            selected[row["session_id"]] = {"row": row, "stratum": f"tail:{field}"}
            added += 1
            if added == 5:
                break

    if len(selected) != 100:
        raise RuntimeError(f"expected 100 unique sessions, selected {len(selected)}")
    return [selected[key] for key in sorted(selected, key=stable_key)]


def component_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for row in rows:
        repo = f"repo:{row['repo_id']}"
        find(repo)
        if row.get("user_id"):
            union(repo, f"user:{row['user_id']}")

    session_counts: Counter[str] = Counter()
    repo_counts: dict[str, set[str]] = defaultdict(set)
    user_counts: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        component = find(f"repo:{row['repo_id']}")
        session_counts[component] += 1
        repo_counts[component].add(row["repo_id"])
        if row.get("user_id"):
            user_counts[component].add(row["user_id"])
    return {
        "repo_user_components": len(session_counts),
        "largest_component_sessions": max(session_counts.values()),
        "largest_component_repositories": max(map(len, repo_counts.values())),
        "largest_component_users": max(map(len, user_counts.values())),
    }


def empty_aggregate() -> dict[str, Any]:
    return {
        "sessions": 0,
        "conversation_rows": 0,
        "timestamped_rows": 0,
        "malformed_timestamps": 0,
        "timestamp_regressions": 0,
        "sessions_with_timestamp_regression": 0,
        "truncated_rows": 0,
        "tool_uses": 0,
        "tool_results": 0,
        "paired_tool_calls": 0,
        "nonnegative_tool_pairs": 0,
        "duplicate_tool_use_ids": 0,
        "assistant_responses": 0,
        "assistant_input_tokens_available": 0,
        "assistant_output_tokens_available": 0,
        "tool_durations_ms": [],
        "turn_types": Counter(),
        "roles": Counter(),
        "tool_names": Counter(),
        "categories": Counter(),
        "bash_categories": Counter(),
        "queue_subtypes": Counter(),
        "prompt_intents": Counter(),
        "prompt_pushback": Counter(),
        "models": Counter(),
        "languages": Counter(),
        "privacy_signals": Counter(),
    }


def merge_aggregate(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "sessions",
        "conversation_rows",
        "timestamped_rows",
        "malformed_timestamps",
        "timestamp_regressions",
        "sessions_with_timestamp_regression",
        "truncated_rows",
        "tool_uses",
        "tool_results",
        "paired_tool_calls",
        "nonnegative_tool_pairs",
        "duplicate_tool_use_ids",
        "assistant_responses",
        "assistant_input_tokens_available",
        "assistant_output_tokens_available",
    ):
        target[key] += source[key]
    target["tool_durations_ms"].extend(source["tool_durations_ms"])
    for key in (
        "turn_types",
        "roles",
        "tool_names",
        "categories",
        "bash_categories",
        "queue_subtypes",
        "prompt_intents",
        "prompt_pushback",
        "models",
        "languages",
        "privacy_signals",
    ):
        target[key].update(source[key])


def scan_privacy(row: dict[str, Any], counts: Counter[str]) -> None:
    fields = (
        row.get("content"),
        row.get("command"),
        row.get("file_path"),
        row.get("pattern"),
        row.get("tool_input_json"),
    )
    for text in iter_strings(fields):
        for name, pattern in PRIVACY_PATTERNS.items():
            counts[name] += len(pattern.findall(text))


def empty_session_state() -> dict[str, Any]:
    aggregate = empty_aggregate()
    aggregate["sessions"] = 1
    return {
        "aggregate": aggregate,
        "tool_starts": {},
        "tool_ends": {},
        "turn_timestamps": [],
    }


def process_conversation_row(state: dict[str, Any], row: dict[str, Any]) -> None:
    aggregate = state["aggregate"]
    aggregate["conversation_rows"] += 1
    turn_type = row.get("turn_type") or "<missing>"
    role = row.get("role") or "<missing>"
    aggregate["turn_types"][turn_type] += 1
    aggregate["roles"][role] += 1
    for key, counter_name in (
        ("category", "categories"),
        ("bash_category", "bash_categories"),
        ("queue_op_subtype", "queue_subtypes"),
        ("prompt_intent", "prompt_intents"),
        ("prompt_pushback", "prompt_pushback"),
        ("model", "models"),
        ("language", "languages"),
    ):
        if row.get(key):
            aggregate[counter_name][str(row[key])] += 1

    timestamp = parse_timestamp(row.get("timestamp"))
    if row.get("timestamp"):
        aggregate["timestamped_rows"] += 1
        if timestamp is None:
            aggregate["malformed_timestamps"] += 1
        elif row.get("turn_number") is not None:
            state["turn_timestamps"].append((int(row["turn_number"]), timestamp))

    if turn_type == "assistant_response":
        aggregate["assistant_responses"] += 1
        aggregate["assistant_input_tokens_available"] += int(
            row.get("input_tokens") is not None
        )
        aggregate["assistant_output_tokens_available"] += int(
            row.get("output_tokens") is not None
        )
    elif turn_type == "tool_use":
        aggregate["tool_uses"] += 1
        tool_id = row.get("tool_call_id")
        if tool_id and timestamp:
            aggregate["duplicate_tool_use_ids"] += int(
                tool_id in state["tool_starts"]
            )
            state["tool_starts"].setdefault(tool_id, timestamp)
        aggregate["tool_names"][row.get("tool_name") or "<missing>"] += 1
    elif turn_type == "tool_result":
        aggregate["tool_results"] += 1
        tool_id = row.get("tool_call_id")
        if tool_id and timestamp:
            state["tool_ends"].setdefault(tool_id, timestamp)
    scan_privacy(row, aggregate["privacy_signals"])


def finalize_session_state(state: dict[str, Any]) -> dict[str, Any]:
    aggregate = state["aggregate"]
    ordered = sorted(state["turn_timestamps"])
    regressions = sum(right[1] < left[1] for left, right in zip(ordered, ordered[1:]))
    aggregate["timestamp_regressions"] = regressions
    aggregate["sessions_with_timestamp_regression"] = int(regressions > 0)
    for tool_id, start in state["tool_starts"].items():
        end = state["tool_ends"].get(tool_id)
        if end is None:
            continue
        aggregate["paired_tool_calls"] += 1
        duration_ms = (end - start).total_seconds() * 1000.0
        if duration_ms >= 0:
            aggregate["nonnegative_tool_pairs"] += 1
            aggregate["tool_durations_ms"].append(duration_ms)
    return aggregate


def audit_local_conversations(
    path: Path, selected: list[dict[str, Any]]
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    int,
]:
    import pyarrow.dataset as dataset
    import pyarrow.parquet as parquet

    selected_by_id = {item["row"]["session_id"]: item for item in selected}
    states = {session_id: empty_session_state() for session_id in selected_by_id}
    source = dataset.dataset(path, format="parquet")
    scanner = source.scanner(
        filter=dataset.field("session_id").isin(list(selected_by_id)),
        batch_size=4096,
        use_threads=True,
    )
    filtered_rows = 0
    for batch_number, batch in enumerate(scanner.to_batches(), start=1):
        for row in batch.to_pylist():
            process_conversation_row(states[row["session_id"]], row)
            filtered_rows += 1
        if batch_number % 25 == 0:
            print(
                f"local scan progress: batches={batch_number}, selected_rows={filtered_rows}",
                flush=True,
            )

    missing = [
        stable_key(session_id)
        for session_id, state in states.items()
        if state["aggregate"]["conversation_rows"] == 0
    ]
    if missing:
        raise RuntimeError(f"selected sessions missing from conversations parquet: {missing}")

    overall = empty_aggregate()
    by_group = {"general": empty_aggregate(), "tail": empty_aggregate()}
    by_agent = {
        agent: empty_aggregate()
        for agent in sorted(
            {item["row"].get("agent") or "unknown" for item in selected}
        )
    }
    for session_id, state in states.items():
        aggregate = finalize_session_state(state)
        merge_aggregate(overall, aggregate)
        group = selected_by_id[session_id]["stratum"].split(":", 1)[0]
        merge_aggregate(by_group[group], aggregate)
        agent = selected_by_id[session_id]["row"].get("agent") or "unknown"
        merge_aggregate(by_agent[agent], aggregate)
    return overall, by_group, by_agent, parquet.ParquetFile(path).metadata.num_rows


def conversation_session_inventory(path: Path) -> tuple[set[str], int]:
    import pyarrow.parquet as parquet

    source = parquet.ParquetFile(path)
    session_ids: set[str] = set()
    for batch in source.iter_batches(columns=["session_id"], batch_size=65536):
        session_ids.update(value for value in batch.column(0).to_pylist() if value)
    return session_ids, source.metadata.num_rows


def serialize_aggregate(aggregate: dict[str, Any]) -> dict[str, Any]:
    durations = aggregate["tool_durations_ms"]
    return {
        "sessions": aggregate["sessions"],
        "conversation_rows": aggregate["conversation_rows"],
        "timestamped_rows": aggregate["timestamped_rows"],
        "timestamp_coverage": (
            aggregate["timestamped_rows"] / aggregate["conversation_rows"]
            if aggregate["conversation_rows"]
            else 0.0
        ),
        "malformed_timestamps": aggregate["malformed_timestamps"],
        "timestamp_regressions": aggregate["timestamp_regressions"],
        "sessions_with_timestamp_regression": aggregate[
            "sessions_with_timestamp_regression"
        ],
        "truncated_rows": aggregate["truncated_rows"],
        "tool_uses": aggregate["tool_uses"],
        "tool_results": aggregate["tool_results"],
        "paired_tool_calls": aggregate["paired_tool_calls"],
        "tool_pair_coverage": (
            aggregate["paired_tool_calls"] / aggregate["tool_uses"]
            if aggregate["tool_uses"]
            else 0.0
        ),
        "nonnegative_tool_pairs": aggregate["nonnegative_tool_pairs"],
        "duplicate_tool_use_ids": aggregate["duplicate_tool_use_ids"],
        "tool_wall_interval_ms": {
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
            "p99": percentile(durations, 0.99),
            "max": max(durations, default=None),
        },
        "assistant_responses": aggregate["assistant_responses"],
        "assistant_input_token_coverage": (
            aggregate["assistant_input_tokens_available"]
            / aggregate["assistant_responses"]
            if aggregate["assistant_responses"]
            else 0.0
        ),
        "assistant_output_token_coverage": (
            aggregate["assistant_output_tokens_available"]
            / aggregate["assistant_responses"]
            if aggregate["assistant_responses"]
            else 0.0
        ),
        "turn_types": dict(aggregate["turn_types"].most_common()),
        "roles": dict(aggregate["roles"].most_common()),
        "top_tool_names": dict(aggregate["tool_names"].most_common(30)),
        "categories": dict(aggregate["categories"].most_common()),
        "bash_categories": dict(aggregate["bash_categories"].most_common()),
        "queue_subtypes": dict(aggregate["queue_subtypes"].most_common()),
        "prompt_intents": dict(aggregate["prompt_intents"].most_common()),
        "prompt_pushback": dict(aggregate["prompt_pushback"].most_common()),
        "top_models": dict(aggregate["models"].most_common(20)),
        "languages": dict(aggregate["languages"].most_common()),
        "privacy_signal_counts": dict(aggregate["privacy_signals"].most_common()),
    }


def main() -> None:
    args = parse_args()
    import pyarrow.parquet as parquet

    table = parquet.read_table(args.sessions)
    rows = table.to_pylist()
    conversation_session_ids, full_conversation_rows = conversation_session_inventory(
        args.conversations
    )
    eligible_rows = [
        row for row in rows if row["session_id"] in conversation_session_ids
    ]
    selected = select_sessions(eligible_rows)
    _, dedupe_stats = deduplicate_by_content_hash(rows)

    selection_document = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "revision": args.revision,
        "seed": SAMPLE_SEED,
        "selection_role": "schema_and_tail_preflight_only",
        "sessions": [
            {
                "session_id": item["row"]["session_id"],
                "stratum": item["stratum"],
            }
            for item in selected
        ],
    }
    args.selection_output.parent.mkdir(parents=True, exist_ok=True)
    args.selection_output.write_text(
        json.dumps(selection_document, indent=2, sort_keys=True) + "\n"
    )

    (
        overall,
        by_group,
        by_agent,
        audited_conversation_rows,
    ) = audit_local_conversations(args.conversations, selected)
    if audited_conversation_rows != full_conversation_rows:
        raise RuntimeError(
            "conversation row inventory changed during audit: "
            f"{full_conversation_rows} != {audited_conversation_rows}"
        )

    sample_rows = [item["row"] for item in selected]
    success_values = []
    for row in rows:
        try:
            success_values.append(float(row["session_success"]))
        except (TypeError, ValueError):
            pass

    report = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "revision": args.revision,
        "sessions_file": {
            "filename": args.sessions.name,
            "bytes": args.sessions.stat().st_size,
            "sha256": sha256_file(args.sessions),
        },
        "conversations_file": {
            "filename": args.conversations.name,
            "bytes": args.conversations.stat().st_size,
            "sha256": sha256_file(args.conversations),
            "rows": full_conversation_rows,
            "access_mode": "local_fixed_revision_parquet",
        },
        "full_sessions_metadata": {
            "rows": len(rows),
            "repositories": len({row["repo_id"] for row in rows}),
            "users_non_null": len(
                {row["user_id"] for row in rows if row.get("user_id")}
            ),
            "user_id_coverage": sum(bool(row.get("user_id")) for row in rows) / len(rows),
            "agents": dict(Counter(row["agent"] for row in rows).most_common()),
            "created_at_min": str(min(row["created_at"] for row in rows if row["created_at"])),
            "created_at_max": str(max(row["created_at"] for row in rows if row["created_at"])),
            "duplicate_session_ids": len(rows)
            - len({row["session_id"] for row in rows}),
            "content_hash_deduplication": dedupe_stats,
            "structured_conversation_coverage": {
                "sessions_with_rows": len(eligible_rows),
                "sessions_without_rows": len(rows) - len(eligible_rows),
                "coverage": len(eligible_rows) / len(rows),
                "missing_by_agent": dict(
                    Counter(
                        row.get("agent") or "unknown"
                        for row in rows
                        if row["session_id"] not in conversation_session_ids
                    ).most_common()
                ),
            },
            "repo_user_split_graph": component_stats(rows),
            "duration_seconds": numeric_summary(rows, "duration_seconds"),
            "tool_call_count": numeric_summary(rows, "tool_call_count"),
            "input_tokens": numeric_summary(rows, "input_tokens"),
            "output_tokens": numeric_summary(rows, "output_tokens"),
            "api_call_count": numeric_summary(rows, "api_call_count"),
            "turn_count": numeric_summary(rows, "turn_count"),
            "session_success": {
                "annotation_type": "llm_annotated_auxiliary_only",
                "available": len(success_values),
                "p50": percentile(success_values, 0.50),
                "p95": percentile(success_values, 0.95),
                "min": min(success_values, default=None),
                "max": max(success_values, default=None),
            },
        },
        "sample_design": {
            "sessions": len(selected),
            "seed": SAMPLE_SEED,
            "role": "schema_and_tail_preflight_only_not_distribution_fitting",
            "general_quotas": GENERAL_QUOTAS,
            "tail_fields": list(TAIL_FIELDS),
            "strata": dict(Counter(item["stratum"] for item in selected).most_common()),
            "sample_agents": dict(
                Counter(item["row"]["agent"] for item in selected).most_common()
            ),
            "sample_duration_seconds": numeric_summary(sample_rows, "duration_seconds"),
            "sample_tool_call_count": numeric_summary(sample_rows, "tool_call_count"),
        },
        "conversation_preflight": serialize_aggregate(overall),
        "conversation_preflight_by_group": {
            group: serialize_aggregate(aggregate) for group, aggregate in by_group.items()
        },
        "conversation_preflight_by_agent": {
            agent: serialize_aggregate(aggregate) for agent, aggregate in by_agent.items()
        },
        "privacy_note": (
            "Counts are conservative pattern matches, not confirmed leaks; no raw conversation "
            "text is written by this audit."
        ),
        "provenance_boundaries": {
            "real": [
                "session_and_turn_sequence",
                "tool_use_and_result_records",
                "timestamps_when_present",
                "token_usage_when_present",
                "git_attribution_metadata",
            ],
            "auxiliary_only": ["llm_annotated_session_success"],
            "missing_or_still_simulated": [
                "network_telemetry",
                "deadline_or_slo",
                "queue_occupancy",
                "counterfactual_controller_action_result",
                "semantic_parallel_dag_without_explicit_parent_evidence",
            ],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
