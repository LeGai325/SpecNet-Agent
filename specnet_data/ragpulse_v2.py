"""Training-side adapter for the numeric RAGPulse request trace."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


SOURCE_ID = "ragpulse"
SOURCE_VERSION = "3672232d"
SPLITS = ("train", "validation", "test")
SPLIT_RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}
REQUIRED_FIELDS = (
    "timestamp",
    "input_length",
    "output_length",
    "hash_ids",
    "session_id",
)
COMPONENT_FIELDS = (
    "sys_prompt",
    "passages_ids",
    "history",
    "web_search",
    "user_input",
)


def _stable_hex(namespace: str, value: str, length: int = 16) -> str:
    payload = f"ragpulse-v2:{namespace}:{value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def assign_split(session_id: str) -> str:
    """Assign an entire RAG session to one deterministic data split."""
    score = int(_stable_hex("split", session_id, 64), 16) / float(2**256)
    if score < SPLIT_RATIOS["train"]:
        return "train"
    if score < SPLIT_RATIOS["train"] + SPLIT_RATIOS["validation"]:
        return "validation"
    return "test"


def _numeric(value: Any, field: str, line_number: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"RAGPulse line {line_number} has invalid numeric field {field}"
        ) from exc


def _read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"RAGPulse line {line_number} is not valid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"RAGPulse line {line_number} is not an object")
            yield line_number, row


def adapt_ragpulse_records(root: Path) -> list[dict[str, Any]]:
    """Map the full trace to text-free, request-level unified records."""
    trace_path = root / "data" / "0_trace.jsonl"
    if not trace_path.is_file():
        raise FileNotFoundError(f"missing RAGPulse trace: {trace_path}")

    records: list[dict[str, Any]] = []
    logical_window = 0
    previous_timestamp: float | None = None
    for line_number, row in _read_jsonl(trace_path):
        missing = [field for field in REQUIRED_FIELDS if row.get(field) is None]
        if missing:
            raise ValueError(
                f"RAGPulse line {line_number} is missing fields: {missing}"
            )
        timestamp = _numeric(row["timestamp"], "timestamp", line_number)
        if previous_timestamp is not None and timestamp < previous_timestamp:
            logical_window += 1
        previous_timestamp = timestamp

        hash_ids = row["hash_ids"]
        if not isinstance(hash_ids, dict):
            raise ValueError(
                f"RAGPulse line {line_number} has non-object hash_ids"
            )
        component_counts: dict[str, int] = {}
        for field in COMPONENT_FIELDS:
            values = hash_ids.get(field)
            if not isinstance(values, list):
                raise ValueError(
                    f"RAGPulse line {line_number} has invalid hash_ids.{field}"
                )
            component_counts[field] = len(values)

        raw_session_id = str(row["session_id"])
        session_id = f"rag-session-{_stable_hex('session', raw_session_id)}"
        source_record_id = f"rag-request-{_stable_hex('record', str(line_number))}"
        records.append(
            {
                "source_dataset": SOURCE_ID,
                "source_version": SOURCE_VERSION,
                "source_record_id": source_record_id,
                "session_id": session_id,
                "workflow_id": source_record_id,
                "source_window_id": f"rag-window-{logical_window}",
                "source_split_unit": "session_id",
                "split": assign_split(raw_session_id),
                "arrival_time_ms": timestamp * 1000.0,
                "input_tokens": int(
                    _numeric(row["input_length"], "input_length", line_number)
                ),
                "output_tokens": int(
                    _numeric(row["output_length"], "output_length", line_number)
                ),
                "retrieval_document_count": component_counts["passages_ids"],
                "history_component_count": component_counts["history"],
                "web_search_component_count": component_counts["web_search"],
                "system_prompt_component_count": component_counts["sys_prompt"],
                "user_input_component_count": component_counts["user_input"],
                "field_provenance": {
                    "session_id": "mapped",
                    "workflow_id": "mapped",
                    "arrival_time_ms": "mapped",
                    "input_tokens": "real",
                    "output_tokens": "real",
                    "retrieval_document_count": "mapped",
                    "history_component_count": "mapped",
                    "web_search_component_count": "mapped",
                    "duration_ms": "missing",
                    "outcome_score": "missing",
                    "deadline_or_slo": "missing",
                    "network_telemetry": "missing",
                },
            }
        )
    if not records:
        raise ValueError("RAGPulse trace has no records")
    return records


def percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    weight = position - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


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


def summarize_ragpulse(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize coverage, split isolation, and project-fit boundaries."""
    split_sessions: dict[str, set[str]] = {split: set() for split in SPLITS}
    split_rows = Counter(str(record["split"]) for record in records)
    window_rows = Counter(str(record["source_window_id"]) for record in records)
    for record in records:
        split_sessions[str(record["split"])].add(str(record["session_id"]))

    overlaps: dict[str, int] = {}
    for left_index, left in enumerate(SPLITS):
        for right in SPLITS[left_index + 1 :]:
            overlaps[f"{left}_{right}"] = len(
                split_sessions[left].intersection(split_sessions[right])
            )

    interarrivals: list[float] = []
    for window in sorted(window_rows):
        arrivals = [
            float(record["arrival_time_ms"])
            for record in records
            if record["source_window_id"] == window
        ]
        interarrivals.extend(
            right - left for left, right in zip(arrivals, arrivals[1:])
        )

    return {
        "source": SOURCE_ID,
        "version": SOURCE_VERSION,
        "records": len(records),
        "sessions": len({str(record["session_id"]) for record in records}),
        "logical_windows": dict(sorted(window_rows.items())),
        "split_records": {split: split_rows[split] for split in SPLITS},
        "split_sessions": {
            split: len(split_sessions[split]) for split in SPLITS
        },
        "split_session_overlap": overlaps,
        "input_tokens": distribution(
            [float(record["input_tokens"]) for record in records]
        ),
        "output_tokens": distribution(
            [float(record["output_tokens"]) for record in records]
        ),
        "retrieval_document_count": distribution(
            [float(record["retrieval_document_count"]) for record in records]
        ),
        "history_component_count": distribution(
            [float(record["history_component_count"]) for record in records]
        ),
        "web_search_request_ratio": sum(
            int(record["web_search_component_count"]) > 0 for record in records
        )
        / len(records),
        "interarrival_ms_within_logical_window": distribution(interarrivals),
        "fit_decision": "retain_as_limited_rag_request_and_load_supplement",
        "fit_strength": "medium",
        "allowed_uses": [
            "rag_request_token_distribution",
            "retrieval_component_count",
            "session_grouping",
            "rag_arrival_and_burst_with_window_split",
        ],
        "forbidden_claims_or_uses": [
            "complete_agent_workflow_trace",
            "tool_or_step_runtime_calibration",
            "dynamic_dag_parent_inference",
            "task_outcome_or_quality_calibration",
            "real_deadline_or_network_telemetry",
        ],
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False))
            handle.write("\n")
