#!/usr/bin/env python3
"""Build privacy-preserving SWE-chat workflow records for the V3 candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


DATASET_ID = "SALT-NLP/SWE-chat"
SPLIT_SEED = "specnet-swe-chat-v3-20260801"
SPLITS = ("train", "validation", "test")
SPLIT_RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}
DEFAULT_IDLE_GAP_THRESHOLD_MS = 300_000.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(f"{SPLIT_SEED}:{value}".encode()).hexdigest()


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
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
    index = round(fraction * (len(ordered) - 1))
    return ordered[index]


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def map_tool_service(tool_name: Any, category: Any = None) -> str:
    """Map a published tool label to the simulator's four service classes."""
    label = f"{tool_name or ''} {category or ''}".lower()
    if any(
        token in label
        for token in (
            "read",
            "grep",
            "glob",
            "search",
            "fetch",
            "browser",
            "retriev",
            "find",
        )
    ):
        return "retrieval"
    if any(
        token in label
        for token in ("write", "edit", "patch", "replace", "storage", "file ops")
    ):
        return "storage"
    if any(
        token in label
        for token in ("agent", "task", "model", "llm", "orchestration")
    ):
        return "llm"
    return "tool"


def deduplicate_session_rows(
    rows: list[dict[str, Any]], eligible_session_ids: set[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["session_id"] in eligible_session_ids:
            groups[str(row["content_hash"])].append(row)
    kept = [
        min(group, key=lambda row: stable_hash(str(row["session_id"])))
        for group in groups.values()
    ]
    kept.sort(key=lambda row: stable_hash(str(row["session_id"])))
    return kept, {
        "eligible_metadata_rows": sum(len(group) for group in groups.values()),
        "deduplicated_session_rows": len(kept),
        "duplicate_hash_groups": sum(len(group) > 1 for group in groups.values()),
        "rows_removed_by_content_hash": sum(len(group) - 1 for group in groups.values()),
    }


def component_groups(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        if parent[node] != node:
            parent[node] = find(parent[node])
        return parent[node]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            if left_root > right_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root

    for row in rows:
        session_id = str(row["session_id"])
        repo_id = str(row.get("repo_id") or f"missing-repo:{session_id}")
        repo_node = f"repo:{repo_id}"
        find(repo_node)
        if row.get("user_id"):
            union(repo_node, f"user:{row['user_id']}")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        session_id = str(row["session_id"])
        repo_id = str(row.get("repo_id") or f"missing-repo:{session_id}")
        grouped[find(f"repo:{repo_id}")].append(row)
    return list(grouped.values())


def assign_component_splits(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """Assign whole components while limiting holdout concentration and agent skew."""
    groups = component_groups(rows)
    groups.sort(
        key=lambda group: (
            -len(group),
            stable_hash(
                "component-members:"
                + ",".join(sorted(str(row["session_id"]) for row in group))
            ),
        )
    )
    targets = {split: len(rows) * SPLIT_RATIOS[split] for split in SPLITS}
    assigned = {split: 0 for split in SPLITS}
    agents = sorted({str(row.get("agent") or "unknown") for row in rows})
    agent_totals = Counter(str(row.get("agent") or "unknown") for row in rows)
    agent_targets = {
        split: {
            agent: agent_totals[agent] * SPLIT_RATIOS[split] for agent in agents
        }
        for split in SPLITS
    }
    assigned_agents = {split: Counter() for split in SPLITS}
    session_splits: dict[str, str] = {}
    component_hashes: dict[str, str] = {}
    components_by_split = Counter()
    forced_train_components = 0
    holdout_component_cap = max(
        1.0,
        min(targets["validation"], targets["test"]) * 0.25,
    )

    for group in groups:
        group_agents = Counter(str(row.get("agent") or "unknown") for row in group)
        if len(group) > holdout_component_cap:
            split = "train"
            forced_train_components += 1
        else:
            candidate_costs = {}
            for candidate in SPLITS:
                total_cost = 0.0
                agent_cost = 0.0
                for split_name in SPLITS:
                    next_total = assigned[split_name] + (
                        len(group) if split_name == candidate else 0
                    )
                    total_cost += (
                        (next_total - targets[split_name])
                        / max(1.0, targets[split_name])
                    ) ** 2
                    for agent in agents:
                        next_agent = assigned_agents[split_name][agent] + (
                            group_agents[agent] if split_name == candidate else 0
                        )
                        agent_cost += (
                            (next_agent - agent_targets[split_name][agent])
                            / max(1.0, agent_targets[split_name][agent])
                        ) ** 2
                candidate_costs[candidate] = total_cost + agent_cost / len(agents)
            split = min(
                SPLITS,
                key=lambda candidate: (candidate_costs[candidate], SPLITS.index(candidate)),
            )
        member_ids = sorted(str(row["session_id"]) for row in group)
        component_hash = stable_hash("component:" + ",".join(member_ids))[:24]
        for session_id in member_ids:
            session_splits[session_id] = split
            component_hashes[session_id] = component_hash
        assigned[split] += len(group)
        assigned_agents[split].update(group_agents)
        components_by_split[split] += 1

    if set(session_splits) != {str(row["session_id"]) for row in rows}:
        raise AssertionError("repo-user component split lost sessions")
    if any(assigned[split] == 0 for split in SPLITS):
        raise ValueError("repo-user component split produced an empty split")
    return session_splits, component_hashes, {
        "policy": "content_hash_dedup_then_repo_user_connected_components",
        "seed": SPLIT_SEED,
        "target_ratios": SPLIT_RATIOS,
        "sessions_by_split": dict(assigned),
        "components_by_split": {
            split: components_by_split[split] for split in SPLITS
        },
        "components_total": len(groups),
        "holdout_component_cap_sessions": holdout_component_cap,
        "large_components_forced_to_train": forced_train_components,
        "agents_by_split": {
            split: dict(assigned_agents[split].most_common()) for split in SPLITS
        },
        "largest_component_sessions_by_split": {
            split: max(
                (
                    len(group)
                    for group in groups
                    if session_splits[str(group[0]["session_id"])] == split
                ),
                default=0,
            )
            for split in SPLITS
        },
    }


def conversation_session_inventory(path: Path) -> tuple[set[str], int]:
    import pyarrow.parquet as parquet

    source = parquet.ParquetFile(path)
    session_ids: set[str] = set()
    for batch in source.iter_batches(columns=["session_id"], batch_size=65536):
        session_ids.update(value for value in batch.column(0).to_pylist() if value)
    return session_ids, source.metadata.num_rows


def empty_conversation_state() -> dict[str, Any]:
    return {
        "rows": 0,
        "timestamped_rows": 0,
        "malformed_timestamps": 0,
        "timestamp_regressions": 0,
        "last_turn_number": None,
        "last_turn_timestamp": None,
        "user_prompts": 0,
        "assistant_responses": 0,
        "tool_service_counts": Counter(),
        "tool_starts": {},
        "tool_ends": {},
        "duplicate_tool_use_ids": 0,
    }


def process_conversation_row(state: dict[str, Any], row: dict[str, Any]) -> None:
    state["rows"] += 1
    timestamp = parse_timestamp(row.get("timestamp"))
    if row.get("timestamp"):
        state["timestamped_rows"] += 1
        if timestamp is None:
            state["malformed_timestamps"] += 1

    turn_number = row.get("turn_number")
    if timestamp is not None and turn_number is not None:
        turn_number = int(turn_number)
        previous_number = state["last_turn_number"]
        previous_timestamp = state["last_turn_timestamp"]
        if (
            previous_number is not None
            and turn_number > previous_number
            and previous_timestamp is not None
            and timestamp < previous_timestamp
        ):
            state["timestamp_regressions"] += 1
        if previous_number is None or turn_number >= previous_number:
            state["last_turn_number"] = turn_number
            state["last_turn_timestamp"] = timestamp

    turn_type = row.get("turn_type")
    if turn_type == "user_prompt":
        state["user_prompts"] += 1
    elif turn_type == "assistant_response":
        state["assistant_responses"] += 1
    elif turn_type == "tool_use":
        service_type = map_tool_service(row.get("tool_name"), row.get("category"))
        state["tool_service_counts"][service_type] += 1
        tool_id = row.get("tool_call_id")
        if tool_id and timestamp is not None:
            state["duplicate_tool_use_ids"] += int(tool_id in state["tool_starts"])
            state["tool_starts"].setdefault(tool_id, (timestamp, service_type))
    elif turn_type == "tool_result":
        tool_id = row.get("tool_call_id")
        if tool_id and timestamp is not None:
            state["tool_ends"].setdefault(tool_id, timestamp)


def finalize_record(
    session_row: dict[str, Any],
    state: dict[str, Any],
    split: str,
    component_hash: str,
    revision: str,
    idle_gap_threshold_ms: float,
) -> dict[str, Any]:
    durations: list[float] = []
    durations_by_service: dict[str, list[float]] = defaultdict(list)
    paired = 0
    negative = 0
    idle_gap_excluded = 0
    for tool_id, (start, service_type) in state["tool_starts"].items():
        end = state["tool_ends"].get(tool_id)
        if end is None:
            continue
        paired += 1
        duration_ms = (end - start).total_seconds() * 1000.0
        if duration_ms < 0:
            negative += 1
        elif duration_ms > idle_gap_threshold_ms:
            idle_gap_excluded += 1
        else:
            durations.append(duration_ms)
            durations_by_service[service_type].append(duration_ms)

    source_session_id = str(session_row["session_id"])
    sample_id = f"swe-{stable_hash('session:' + source_session_id)[:24]}"
    timestamp_coverage = (
        state["timestamped_rows"] / state["rows"] if state["rows"] else 0.0
    )
    tool_uses = sum(state["tool_service_counts"].values())
    success = finite_number(session_row.get("session_success"))
    duration_seconds = finite_number(session_row.get("duration_seconds"))
    return {
        "source_dataset": "swe_chat",
        "source_revision": revision,
        "sample_id": sample_id,
        "split": split,
        "split_component_id": component_hash,
        "split_unit": "repo_user_connected_component",
        "template_hint": "coding",
        "agent": str(session_row.get("agent") or "unknown"),
        "input_tokens": int(finite_number(session_row.get("input_tokens")) or 0),
        "output_tokens": int(finite_number(session_row.get("output_tokens")) or 0),
        "turn_count": int(finite_number(session_row.get("turn_count")) or 0),
        "user_prompt_count": state["user_prompts"],
        "assistant_response_count": state["assistant_responses"],
        "tool_call_count": tool_uses,
        "tool_service_counts": dict(sorted(state["tool_service_counts"].items())),
        "tool_latency_ms_by_service": {
            service_type: percentile(values, 0.50)
            for service_type, values in sorted(durations_by_service.items())
        },
        "paired_tool_calls": paired,
        "usable_timing_tool_calls": len(durations),
        "tool_timing_coverage": len(durations) / tool_uses if tool_uses else 0.0,
        "cleaned_tool_duration_ms_p50": percentile(durations, 0.50),
        "cleaned_tool_duration_ms_p95": percentile(durations, 0.95),
        "idle_gap_excluded_tool_calls": idle_gap_excluded,
        "negative_tool_intervals": negative,
        "duplicate_tool_use_ids": state["duplicate_tool_use_ids"],
        "timestamp_coverage": timestamp_coverage,
        "timestamp_regressions": state["timestamp_regressions"],
        "session_duration_seconds_metadata": duration_seconds,
        "session_success_auxiliary": success,
        "mapping_boundaries": {
            "session_duration_used_as_service_time": False,
            "session_success_ground_truth": False,
            "network_deadline_queue_present": False,
        },
    }


def build_records(
    sessions_path: Path,
    conversations_path: Path,
    revision: str,
    idle_gap_threshold_ms: float = DEFAULT_IDLE_GAP_THRESHOLD_MS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import pyarrow.dataset as dataset
    import pyarrow.parquet as parquet

    if idle_gap_threshold_ms <= 0:
        raise ValueError("idle gap threshold must be positive")
    session_rows = parquet.read_table(sessions_path).to_pylist()
    conversation_ids, conversation_rows = conversation_session_inventory(
        conversations_path
    )
    kept_rows, dedupe_stats = deduplicate_session_rows(
        session_rows, conversation_ids
    )
    session_splits, component_hashes, split_stats = assign_component_splits(kept_rows)
    kept_by_id = {str(row["session_id"]): row for row in kept_rows}
    states = {session_id: empty_conversation_state() for session_id in kept_by_id}

    source = dataset.dataset(conversations_path, format="parquet")
    scanner = source.scanner(
        columns=[
            "session_id",
            "turn_number",
            "turn_type",
            "timestamp",
            "tool_name",
            "tool_call_id",
            "category",
        ],
        filter=dataset.field("session_id").isin(list(kept_by_id)),
        batch_size=65536,
        use_threads=True,
    )
    processed_rows = 0
    for batch_number, batch in enumerate(scanner.to_batches(), start=1):
        for row in batch.to_pylist():
            process_conversation_row(states[row["session_id"]], row)
            processed_rows += 1
        if batch_number % 10 == 0:
            print(
                f"SWE-chat V3 scan: batches={batch_number}, rows={processed_rows}",
                flush=True,
            )

    missing = [
        stable_hash(session_id)
        for session_id, state in states.items()
        if state["rows"] == 0
    ]
    if missing:
        raise RuntimeError(f"selected sessions missing conversation rows: {missing[:5]}")

    records = [
        finalize_record(
            session_row=kept_by_id[session_id],
            state=states[session_id],
            split=session_splits[session_id],
            component_hash=component_hashes[session_id],
            revision=revision,
            idle_gap_threshold_ms=idle_gap_threshold_ms,
        )
        for session_id in kept_by_id
    ]
    records.sort(key=lambda record: record["sample_id"])
    by_split = Counter(record["split"] for record in records)
    by_agent = Counter(record["agent"] for record in records)
    timing_usable = sum(record["usable_timing_tool_calls"] for record in records)
    tool_calls = sum(record["tool_call_count"] for record in records)
    report = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "revision": revision,
        "input_files": {
            "sessions": {
                "bytes": sessions_path.stat().st_size,
                "sha256": sha256_file(sessions_path),
            },
            "conversations": {
                "bytes": conversations_path.stat().st_size,
                "sha256": sha256_file(conversations_path),
                "rows": conversation_rows,
            },
        },
        "metadata_rows": len(session_rows),
        "sessions_without_structured_conversations": len(session_rows)
        - dedupe_stats["eligible_metadata_rows"],
        "deduplication": dedupe_stats,
        "split": split_stats,
        "records": len(records),
        "records_by_split": {split: by_split[split] for split in SPLITS},
        "records_by_agent": dict(by_agent.most_common()),
        "processed_conversation_rows": processed_rows,
        "time_cleaning": {
            "idle_gap_threshold_ms": idle_gap_threshold_ms,
            "usable_tool_intervals": timing_usable,
            "tool_uses": tool_calls,
            "usable_interval_coverage": timing_usable / tool_calls if tool_calls else 0.0,
            "idle_gap_excluded": sum(
                record["idle_gap_excluded_tool_calls"] for record in records
            ),
            "negative_intervals": sum(
                record["negative_tool_intervals"] for record in records
            ),
            "sessions_with_timestamp_regression": sum(
                record["timestamp_regressions"] > 0 for record in records
            ),
        },
        "privacy": {
            "raw_session_repo_user_ids_exported": False,
            "conversation_text_exported": False,
            "tool_arguments_or_paths_exported": False,
        },
    }
    return records, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--conversations", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--idle-gap-threshold-ms",
        type=float,
        default=DEFAULT_IDLE_GAP_THRESHOLD_MS,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records, report = build_records(
        sessions_path=args.sessions,
        conversations_path=args.conversations,
        revision=args.revision,
        idle_gap_threshold_ms=args.idle_gap_threshold_ms,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as destination:
        for record in records:
            destination.write(
                json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
            )
    report["output"] = {
        "filename": args.output.name,
        "bytes": args.output.stat().st_size,
        "sha256": sha256_file(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
