"""Backward-compatible replay and diagnostics for workflow-hint events."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from workflow_hints import (
        DEPENDENCY_KINDS,
        EVENT_REASONS,
        REASONED_STEP_EVENTS,
        STEP_EVENTS,
        SUPPORTED_SCHEMA_VERSIONS,
    )
except ImportError:  # pragma: no cover - package-style imports
    from .workflow_hints import (
        DEPENDENCY_KINDS,
        EVENT_REASONS,
        REASONED_STEP_EVENTS,
        STEP_EVENTS,
        SUPPORTED_SCHEMA_VERSIONS,
    )


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]*$")
ACTIVE_STATES = {"created", "ready", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled"}
EVENT_REQUIRED_FIELDS = {
    "schema_version",
    "sequence",
    "workflow_id",
    "step_id",
    "attempt_id",
    "parents",
    "dependency_kinds",
    "request_type",
    "deadline_hint",
    "size_hint",
    "size_unit",
    "speculation_level",
    "event",
    "timestamp",
    "source",
}
FORBIDDEN_CONTENT_FIELDS = {
    "content",
    "payload",
    "prompt",
    "response_text",
    "tool_args",
}


@dataclass(frozen=True)
class ReplayDiagnostic:
    code: str
    message: str
    sequence: Optional[int] = None
    step_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "sequence": self.sequence,
            "step_id": self.step_id,
        }


class WorkflowHintReplayError(ValueError):
    """Raised with a structured diagnostic when replay cannot continue."""

    def __init__(self, diagnostic: ReplayDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class NormalizedWorkflowHintEvent:
    schema_version: str
    sequence: int
    workflow_id: str
    step_id: str
    attempt_id: int
    parents: Tuple[str, ...]
    dependency_kinds: Tuple[Tuple[str, str], ...]
    request_type: str
    deadline_hint: float
    size_hint: float
    size_unit: str
    speculation_level: float
    event: str
    reason: str
    timestamp: float
    source: str

    def structural_tuple(self) -> Tuple[object, ...]:
        return (
            self.workflow_id,
            self.step_id,
            self.parents,
            self.dependency_kinds,
            self.request_type,
            self.deadline_hint,
            self.size_hint,
            self.size_unit,
            self.speculation_level,
            self.source,
        )


@dataclass
class ReplayedStep:
    event: NormalizedWorkflowHintEvent
    state: str = "created"
    attempt_id: int = 0
    selected: bool = False
    failure_count: int = 0
    last_reason: str = ""
    last_sequence: int = 0
    last_timestamp: float = 0.0

    def to_dict(self, children: Sequence[str]) -> Dict[str, object]:
        return {
            "parents": list(self.event.parents),
            "children": list(children),
            "dependency_kinds": dict(self.event.dependency_kinds),
            "request_type": self.event.request_type,
            "deadline_hint": self.event.deadline_hint,
            "size_hint": self.event.size_hint,
            "size_unit": self.event.size_unit,
            "speculation_level": self.event.speculation_level,
            "source": self.event.source,
            "state": self.state,
            "attempt_id": self.attempt_id,
            "selected": self.selected,
            "failure_count": self.failure_count,
            "last_reason": self.last_reason,
            "last_sequence": self.last_sequence,
            "last_timestamp": self.last_timestamp,
        }


@dataclass(frozen=True)
class WorkflowReplaySnapshot:
    workflow_id: str
    last_sequence: int
    last_timestamp: float
    events_replayed: int
    schema_versions: Tuple[str, ...]
    steps: Dict[str, Dict[str, object]]
    active_steps: Tuple[str, ...]
    ready_steps: Tuple[str, ...]
    running_steps: Tuple[str, ...]
    terminal_steps: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "workflow_id": self.workflow_id,
            "last_sequence": self.last_sequence,
            "last_timestamp": self.last_timestamp,
            "events_replayed": self.events_replayed,
            "schema_versions": list(self.schema_versions),
            "active_steps": list(self.active_steps),
            "ready_steps": list(self.ready_steps),
            "running_steps": list(self.running_steps),
            "terminal_steps": list(self.terminal_steps),
            "steps": self.steps,
        }


@dataclass(frozen=True)
class ReplayAudit:
    snapshot: Optional[WorkflowReplaySnapshot]
    diagnostics: Tuple[ReplayDiagnostic, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.diagnostics and self.snapshot is not None


def _diagnostic(
    code: str,
    message: str,
    *,
    sequence: Optional[int] = None,
    step_id: str = "",
) -> WorkflowHintReplayError:
    return WorkflowHintReplayError(
        ReplayDiagnostic(
            code=code,
            message=message,
            sequence=sequence,
            step_id=step_id,
        )
    )


def _identifier(value: object, label: str, sequence: Optional[int]) -> str:
    text = str(value)
    if not text or not IDENTIFIER_PATTERN.fullmatch(text):
        raise _diagnostic(
            "invalid_identifier",
            f"invalid {label}: {value!r}",
            sequence=sequence,
        )
    return text


def _token(value: object, label: str, sequence: Optional[int]) -> str:
    text = str(value)
    if not TOKEN_PATTERN.fullmatch(text):
        raise _diagnostic(
            "invalid_token",
            f"invalid {label}: {value!r}",
            sequence=sequence,
        )
    return text


def _integer(value: object, label: str, sequence: Optional[int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _diagnostic(
            "invalid_integer",
            f"{label} must be a non-negative integer",
            sequence=sequence,
        )
    return value


def _number(value: object, label: str, sequence: Optional[int]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _diagnostic(
            "invalid_number",
            f"{label} must be a finite non-negative number",
            sequence=sequence,
        )
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise _diagnostic(
            "invalid_number",
            f"{label} must be a finite non-negative number",
            sequence=sequence,
        )
    return result


def normalize_workflow_hint_event(
    raw_event: Mapping[str, object],
) -> NormalizedWorkflowHintEvent:
    """Normalize a v1.0 or v1.1 event into the v1.1 replay contract."""

    if not isinstance(raw_event, Mapping):
        raise _diagnostic("invalid_event", "workflow hint event must be a mapping")
    forbidden = sorted(FORBIDDEN_CONTENT_FIELDS & set(raw_event))
    if forbidden:
        raise _diagnostic(
            "forbidden_content_fields",
            f"workflow hint event contains forbidden content fields: {forbidden}",
        )
    missing = sorted(EVENT_REQUIRED_FIELDS - set(raw_event))
    if missing:
        raise _diagnostic("missing_fields", f"workflow hint event is missing fields: {missing}")
    raw_sequence = raw_event["sequence"]
    sequence = _integer(raw_sequence, "sequence", None)
    schema_version = str(raw_event["schema_version"])
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise _diagnostic(
            "unsupported_schema",
            f"unsupported workflow hint schema: {schema_version}",
            sequence=sequence,
        )
    if schema_version == "1.1" and "reason" not in raw_event:
        raise _diagnostic(
            "missing_fields",
            "workflow hint v1.1 event is missing field: reason",
            sequence=sequence,
        )

    workflow_id = _identifier(raw_event["workflow_id"], "workflow_id", sequence)
    step_id = _identifier(raw_event["step_id"], "step_id", sequence)
    attempt_id = _integer(raw_event["attempt_id"], "attempt_id", sequence)
    raw_parents = raw_event["parents"]
    if not isinstance(raw_parents, (list, tuple)):
        raise _diagnostic(
            "invalid_parents",
            "parents must be a list or tuple",
            sequence=sequence,
            step_id=step_id,
        )
    parents = tuple(_identifier(parent, "parent step_id", sequence) for parent in raw_parents)
    if len(parents) != len(set(parents)):
        raise _diagnostic(
            "duplicate_parent",
            f"duplicate parent for step: {step_id}",
            sequence=sequence,
            step_id=step_id,
        )

    raw_kinds = raw_event["dependency_kinds"]
    if not isinstance(raw_kinds, Mapping):
        raise _diagnostic(
            "invalid_dependency_kinds",
            "dependency_kinds must be a mapping",
            sequence=sequence,
            step_id=step_id,
        )
    dependency_kinds = {
        _identifier(parent, "dependency parent", sequence): str(kind)
        for parent, kind in raw_kinds.items()
    }
    if set(dependency_kinds) != set(parents):
        raise _diagnostic(
            "dependency_parent_mismatch",
            "dependency_kinds keys must exactly match parents",
            sequence=sequence,
            step_id=step_id,
        )
    invalid_kinds = set(dependency_kinds.values()) - DEPENDENCY_KINDS
    if invalid_kinds:
        raise _diagnostic(
            "invalid_dependency_kind",
            f"invalid dependency kinds: {sorted(invalid_kinds)}",
            sequence=sequence,
            step_id=step_id,
        )

    event = str(raw_event["event"])
    if event not in STEP_EVENTS:
        raise _diagnostic(
            "invalid_event_type",
            f"invalid step event: {event}",
            sequence=sequence,
            step_id=step_id,
        )
    reason = str(raw_event.get("reason", ""))
    if reason and reason not in EVENT_REASONS:
        raise _diagnostic(
            "invalid_event_reason",
            f"invalid event reason: {reason!r}",
            sequence=sequence,
            step_id=step_id,
        )
    if reason and event not in REASONED_STEP_EVENTS:
        raise _diagnostic(
            "unexpected_event_reason",
            f"event {event} does not accept a reason",
            sequence=sequence,
            step_id=step_id,
        )
    if schema_version == "1.1" and event in REASONED_STEP_EVENTS and not reason:
        raise _diagnostic(
            "missing_event_reason",
            f"v1.1 {event} event must include a reason",
            sequence=sequence,
            step_id=step_id,
        )

    speculation_level = _number(
        raw_event["speculation_level"],
        "speculation_level",
        sequence,
    )
    if speculation_level > 1.0:
        raise _diagnostic(
            "invalid_speculation_level",
            "speculation_level must not exceed 1.0",
            sequence=sequence,
            step_id=step_id,
        )
    return NormalizedWorkflowHintEvent(
        schema_version=schema_version,
        sequence=sequence,
        workflow_id=workflow_id,
        step_id=step_id,
        attempt_id=attempt_id,
        parents=parents,
        dependency_kinds=tuple(sorted(dependency_kinds.items())),
        request_type=_token(raw_event["request_type"], "request_type", sequence),
        deadline_hint=_number(raw_event["deadline_hint"], "deadline_hint", sequence),
        size_hint=_number(raw_event["size_hint"], "size_hint", sequence),
        size_unit=_token(raw_event["size_unit"], "size_unit", sequence),
        speculation_level=speculation_level,
        event=event,
        reason=reason,
        timestamp=_number(raw_event["timestamp"], "timestamp", sequence),
        source=_token(raw_event["source"], "source", sequence),
    )


def replay_workflow_hint_events(
    raw_events: Iterable[Mapping[str, object]],
    *,
    workflow_id: Optional[object] = None,
    upto_sequence: Optional[int] = None,
    upto_timestamp: Optional[float] = None,
) -> WorkflowReplaySnapshot:
    """Replay one workflow's v1.0/v1.1 event stream into an active DAG snapshot."""

    normalized = [normalize_workflow_hint_event(event) for event in raw_events]
    if workflow_id is not None:
        workflow_key = _identifier(workflow_id, "workflow_id", None)
        normalized = [event for event in normalized if event.workflow_id == workflow_key]
    workflow_ids = sorted({event.workflow_id for event in normalized})
    if not workflow_ids:
        raise _diagnostic("empty_event_stream", "no workflow hint events to replay")
    if len(workflow_ids) != 1:
        raise _diagnostic(
            "multiple_workflows",
            f"replay requires one workflow, found: {workflow_ids}",
        )
    if upto_sequence is not None:
        upto_sequence = _integer(upto_sequence, "upto_sequence", None)
        normalized = [event for event in normalized if event.sequence <= upto_sequence]
    if upto_timestamp is not None:
        timestamp_limit = _number(upto_timestamp, "upto_timestamp", None)
        normalized = [event for event in normalized if event.timestamp <= timestamp_limit]
    if not normalized:
        raise _diagnostic("empty_event_stream", "no events remain within replay boundary")

    normalized.sort(key=lambda event: event.sequence)
    seen_sequences: set[int] = set()
    steps: Dict[str, ReplayedStep] = {}
    last_global_timestamp = 0.0
    for event in normalized:
        if event.sequence in seen_sequences:
            raise _diagnostic(
                "duplicate_sequence",
                f"duplicate event sequence: {event.sequence}",
                sequence=event.sequence,
                step_id=event.step_id,
            )
        seen_sequences.add(event.sequence)
        if event.timestamp < last_global_timestamp:
            raise _diagnostic(
                "timestamp_regression",
                f"event timestamp moved backwards: {event.timestamp} < {last_global_timestamp}",
                sequence=event.sequence,
                step_id=event.step_id,
            )
        last_global_timestamp = event.timestamp

        if event.event == "created":
            if event.step_id in steps:
                raise _diagnostic(
                    "duplicate_step",
                    f"step already created: {event.step_id}",
                    sequence=event.sequence,
                    step_id=event.step_id,
                )
            if event.attempt_id != 0:
                raise _diagnostic(
                    "invalid_initial_attempt",
                    f"created step must start at attempt 0: {event.step_id}",
                    sequence=event.sequence,
                    step_id=event.step_id,
                )
            steps[event.step_id] = ReplayedStep(
                event=event,
                attempt_id=event.attempt_id,
                last_sequence=event.sequence,
                last_timestamp=event.timestamp,
            )
            continue

        if event.step_id not in steps:
            raise _diagnostic(
                "event_before_create",
                f"event precedes step creation: {event.step_id}",
                sequence=event.sequence,
                step_id=event.step_id,
            )
        step = steps[event.step_id]
        if event.structural_tuple() != step.event.structural_tuple():
            raise _diagnostic(
                "structural_drift",
                f"step structural fields changed during replay: {event.step_id}",
                sequence=event.sequence,
                step_id=event.step_id,
            )
        if event.timestamp < step.last_timestamp:
            raise _diagnostic(
                "step_timestamp_regression",
                f"timestamp moved backwards for step: {event.step_id}",
                sequence=event.sequence,
                step_id=event.step_id,
            )

        if event.event == "retried":
            if step.state != "failed":
                raise _diagnostic(
                    "illegal_transition",
                    f"cannot retry step {event.step_id} from state {step.state}",
                    sequence=event.sequence,
                    step_id=event.step_id,
                )
            if event.attempt_id != step.attempt_id + 1:
                raise _diagnostic(
                    "attempt_mismatch",
                    f"retry attempt must increment by one for step: {event.step_id}",
                    sequence=event.sequence,
                    step_id=event.step_id,
                )
            step.state = "created"
            step.attempt_id = event.attempt_id
            step.selected = False
        else:
            if event.attempt_id != step.attempt_id:
                raise _diagnostic(
                    "attempt_mismatch",
                    f"event attempt does not match active attempt for step: {event.step_id}",
                    sequence=event.sequence,
                    step_id=event.step_id,
                )
            _apply_transition(step, event, steps)

        step.last_sequence = event.sequence
        step.last_timestamp = event.timestamp
        if event.reason:
            step.last_reason = event.reason

    missing_parents = sorted(
        {
            parent
            for step in steps.values()
            for parent in step.event.parents
            if parent not in steps
        }
    )
    if missing_parents:
        raise _diagnostic(
            "dangling_dependency",
            f"workflow has missing parent steps: {missing_parents}",
        )
    _validate_acyclic(steps)

    children: Dict[str, List[str]] = defaultdict(list)
    for step_id, step in steps.items():
        for parent in step.event.parents:
            children[parent].append(step_id)
    step_dicts = {
        step_id: step.to_dict(sorted(children.get(step_id, [])))
        for step_id, step in sorted(steps.items())
    }
    return WorkflowReplaySnapshot(
        workflow_id=workflow_ids[0],
        last_sequence=normalized[-1].sequence,
        last_timestamp=max(event.timestamp for event in normalized),
        events_replayed=len(normalized),
        schema_versions=tuple(sorted({event.schema_version for event in normalized})),
        steps=step_dicts,
        active_steps=tuple(sorted(step_id for step_id, step in steps.items() if step.state in ACTIVE_STATES)),
        ready_steps=tuple(sorted(step_id for step_id, step in steps.items() if step.state == "ready")),
        running_steps=tuple(sorted(step_id for step_id, step in steps.items() if step.state == "running")),
        terminal_steps=tuple(sorted(step_id for step_id, step in steps.items() if step.state in TERMINAL_STATES)),
    )


def _apply_transition(
    step: ReplayedStep,
    event: NormalizedWorkflowHintEvent,
    steps: Mapping[str, ReplayedStep],
) -> None:
    transitions = {
        "ready": ({"created"}, "ready"),
        "started": ({"ready"}, "running"),
        "completed": ({"running"}, "completed"),
        "failed": ({"running"}, "failed"),
        "cancelled": (ACTIVE_STATES, "cancelled"),
    }
    if event.event == "selected":
        if step.state != "completed" or step.selected:
            raise _diagnostic(
                "illegal_transition",
                f"cannot select step {event.step_id} from state {step.state}",
                sequence=event.sequence,
                step_id=event.step_id,
            )
        step.selected = True
        return
    try:
        allowed, target = transitions[event.event]
    except KeyError as exc:
        raise _diagnostic(
            "invalid_event_type",
            f"unsupported replay event: {event.event}",
            sequence=event.sequence,
            step_id=event.step_id,
        ) from exc
    if step.state not in allowed:
        raise _diagnostic(
            "illegal_transition",
            f"cannot apply {event.event} to step {event.step_id} from state {step.state}",
            sequence=event.sequence,
            step_id=event.step_id,
        )
    if event.event == "ready":
        incomplete = [
            parent
            for parent, kind in event.dependency_kinds
            if kind == "hard_dependency"
            and (parent not in steps or steps[parent].state != "completed")
        ]
        if incomplete:
            raise _diagnostic(
                "hard_dependency_not_completed",
                f"hard dependencies are not completed for {event.step_id}: {incomplete}",
                sequence=event.sequence,
                step_id=event.step_id,
            )
    step.state = target
    if event.event == "failed":
        step.failure_count += 1


def _validate_acyclic(steps: Mapping[str, ReplayedStep]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise _diagnostic(
                "dag_cycle",
                f"workflow DAG contains a cycle at step: {step_id}",
                step_id=step_id,
            )
        if step_id in visited:
            return
        visiting.add(step_id)
        for parent in steps[step_id].event.parents:
            visit(parent)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in steps:
        visit(step_id)


def audit_workflow_hint_events(
    raw_events: Iterable[Mapping[str, object]],
    *,
    workflow_id: Optional[object] = None,
) -> ReplayAudit:
    try:
        return ReplayAudit(
            snapshot=replay_workflow_hint_events(raw_events, workflow_id=workflow_id)
        )
    except WorkflowHintReplayError as exc:
        return ReplayAudit(snapshot=None, diagnostics=(exc.diagnostic,))


def compare_replay_to_engine_snapshot(
    replay: WorkflowReplaySnapshot,
    engine_snapshot: Mapping[str, object],
) -> Tuple[ReplayDiagnostic, ...]:
    """Compare fields observable in hint events with a Dynamic DAG snapshot."""

    diagnostics: List[ReplayDiagnostic] = []
    raw_engine_steps = engine_snapshot.get("steps", {})
    if not isinstance(raw_engine_steps, Mapping):
        return (
            ReplayDiagnostic(
                code="invalid_engine_snapshot",
                message="engine snapshot steps must be a mapping",
            ),
        )
    if set(replay.steps) != set(raw_engine_steps):
        diagnostics.append(
            ReplayDiagnostic(
                code="step_set_mismatch",
                message=(
                    f"replay steps {sorted(replay.steps)} != "
                    f"engine steps {sorted(raw_engine_steps)}"
                ),
            )
        )
        return tuple(diagnostics)

    common_fields = (
        "parents",
        "dependency_kinds",
        "request_type",
        "size_hint",
        "size_unit",
        "speculation_level",
        "state",
        "attempt_id",
        "failure_count",
    )
    for step_id, replay_step in replay.steps.items():
        engine_step = raw_engine_steps[step_id]
        for field_name in common_fields:
            if replay_step[field_name] != engine_step[field_name]:
                diagnostics.append(
                    ReplayDiagnostic(
                        code="snapshot_field_mismatch",
                        message=(
                            f"{step_id}.{field_name}: replay={replay_step[field_name]!r}, "
                            f"engine={engine_step[field_name]!r}"
                        ),
                        step_id=step_id,
                    )
                )
        if replay_step["selected"] != engine_step["selected_by_judge"]:
            diagnostics.append(
                ReplayDiagnostic(
                    code="snapshot_field_mismatch",
                    message=(
                        f"{step_id}.selected: replay={replay_step['selected']!r}, "
                        f"engine={engine_step['selected_by_judge']!r}"
                    ),
                    step_id=step_id,
                )
            )
        cancel_reason = engine_step.get("cancel_reason")
        if replay_step["state"] == "cancelled" and replay_step["last_reason"]:
            if replay_step["last_reason"] != cancel_reason:
                diagnostics.append(
                    ReplayDiagnostic(
                        code="snapshot_field_mismatch",
                        message=(
                            f"{step_id}.cancel_reason: "
                            f"replay={replay_step['last_reason']!r}, engine={cancel_reason!r}"
                        ),
                        step_id=step_id,
                    )
                )
    return tuple(diagnostics)


def audit_preflight_files(events_path: Path, snapshots_path: Path) -> Dict[str, object]:
    raw_lines = events_path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in raw_lines if line.strip()]
    snapshot_rows = json.loads(snapshots_path.read_text(encoding="utf-8"))
    event_groups: Dict[Tuple[str, str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("fixture", "")),
            str(row.get("capacity_label", "")),
            str(row["workflow_id"]),
        )
        event_groups[key].append(row)
    snapshots = {
        (
            str(row.get("fixture", "")),
            str(row.get("capacity_label", "")),
            str(row["snapshot"]["workflow_id"]),
        ): row["snapshot"]
        for row in snapshot_rows
    }

    diagnostics: List[Dict[str, object]] = []
    valid_groups = 0
    snapshot_mismatches = 0
    for key, group_events in sorted(event_groups.items()):
        audit = audit_workflow_hint_events(group_events, workflow_id=key[2])
        if not audit.valid:
            diagnostics.extend(
                {"group": list(key), **item.to_dict()} for item in audit.diagnostics
            )
            continue
        valid_groups += 1
        if key in snapshots:
            mismatches = compare_replay_to_engine_snapshot(audit.snapshot, snapshots[key])
            snapshot_mismatches += len(mismatches)
            diagnostics.extend(
                {"group": list(key), **item.to_dict()} for item in mismatches
            )
        else:
            diagnostics.append(
                {
                    "group": list(key),
                    **ReplayDiagnostic(
                        code="missing_engine_snapshot",
                        message=f"missing engine snapshot for group: {key}",
                    ).to_dict(),
                }
            )

    reasoned_rows = [row for row in rows if row.get("event") in REASONED_STEP_EVENTS]
    missing_reasons = sum(not row.get("reason") for row in reasoned_rows)
    return {
        "events_path": str(events_path),
        "snapshots_path": str(snapshots_path),
        "event_bytes": events_path.stat().st_size,
        "events": len(rows),
        "average_bytes_per_event": (
            events_path.stat().st_size / len(rows) if rows else 0.0
        ),
        "groups": len(event_groups),
        "valid_replay_groups": valid_groups,
        "snapshot_mismatches": snapshot_mismatches,
        "schema_versions": dict(sorted(Counter(str(row.get("schema_version")) for row in rows).items())),
        "events_by_type": dict(sorted(Counter(str(row.get("event")) for row in rows).items())),
        "reasoned_events": len(reasoned_rows),
        "reasoned_events_missing_reason": missing_reasons,
        "diagnostics": diagnostics,
        "unobservable_engine_fields": [
            "flow_id",
            "retry_limit",
            "workflow_final_status",
            "finalize_graph_version_increment",
        ],
        "field_decisions": {
            "reason": "required_in_v1_1",
            "graph_version": "derive_active_graph_from_sequence",
            "attempt_size": "defer_until_attempt_sizes_can_change",
            "workflow_final_event": "defer_use_summary_status",
            "periodic_snapshot": "debug_only_not_default_output",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = audit_preflight_files(args.events, args.snapshots)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
