"""Workflow-hint schema, lifecycle tracking, and DAG validation.

The collector is intentionally policy-neutral.  It records compact runtime
metadata for later criticality scoring, but never changes workflow execution or
network scheduling decisions.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


SCHEMA_VERSION = "1.1"
SUPPORTED_SCHEMA_VERSIONS = {"1.0", SCHEMA_VERSION}

DEPENDENCY_KINDS = {
    "hard_dependency",
    "optional_evidence",
    "control_trigger",
}

STEP_EVENTS = {
    "created",
    "ready",
    "started",
    "completed",
    "failed",
    "retried",
    "cancelled",
    "selected",
}

EVENT_REASONS = {
    "execution_failed",
    "judge_pruned",
    "policy_cancelled",
    "retry_requested",
    "workflow_completed",
    "workflow_timeout",
}
REASONED_STEP_EVENTS = {"failed", "retried", "cancelled"}

TERMINAL_STEP_STATES = {"completed", "failed", "cancelled"}
WORKFLOW_FINAL_STATES = {"completed", "timed_out", "failed", "cancelled"}
REQUEST_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]*$")
STEP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class WorkflowHintError(ValueError):
    """Raised when a hint or lifecycle transition violates the contract."""


def _finite_number(value: object, label: str, *, minimum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowHintError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise WorkflowHintError(f"{label} must be a finite number")
    if minimum is not None and result < minimum:
        raise WorkflowHintError(f"{label} must be at least {minimum}")
    return result


def _stable_identifier(value: object, label: str) -> str:
    text = str(value)
    if not text or not STEP_ID_PATTERN.fullmatch(text):
        raise WorkflowHintError(f"invalid {label}: {value!r}")
    return text


@dataclass
class WorkflowHintRecord:
    workflow_id: str
    deadline_hint: float
    source: str
    clock_domain: str
    status: str = "active"
    steps: Dict[str, "StepHintRecord"] = field(default_factory=dict)


@dataclass
class StepHintRecord:
    workflow_id: str
    step_id: str
    parents: Tuple[str, ...]
    dependency_kinds: Dict[str, str]
    request_type: str
    deadline_hint: float
    size_hint: float
    size_unit: str
    speculation_level: float
    source: str
    attempt_id: int = 0
    state: str = "created"
    selected: bool = False
    last_timestamp: float = 0.0


@dataclass(frozen=True)
class WorkflowHintEvent:
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

    def to_dict(self) -> Dict[str, object]:
        """Return the public, content-free JSON schema for one hint event."""

        return {
            "schema_version": SCHEMA_VERSION,
            "sequence": self.sequence,
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "parents": list(self.parents),
            "dependency_kinds": dict(self.dependency_kinds),
            "request_type": self.request_type,
            "deadline_hint": self.deadline_hint,
            "size_hint": self.size_hint,
            "size_unit": self.size_unit,
            "speculation_level": self.speculation_level,
            "event": self.event,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "source": self.source,
        }


class WorkflowHintCollector:
    """Collect and validate workflow hints without changing runtime behavior."""

    def __init__(self, *, allow_forward_references: bool = False) -> None:
        self.allow_forward_references = allow_forward_references
        self._workflows: Dict[str, WorkflowHintRecord] = {}
        self._events: List[WorkflowHintEvent] = []

    @property
    def workflows(self) -> Mapping[str, WorkflowHintRecord]:
        return self._workflows

    @property
    def events(self) -> Tuple[WorkflowHintEvent, ...]:
        return tuple(self._events)

    def event_dicts(self) -> List[Dict[str, object]]:
        return [event.to_dict() for event in self._events]

    def register_workflow(
        self,
        workflow_id: object,
        *,
        deadline_hint: float,
        timestamp: float,
        source: str,
        clock_domain: str = "simulator_step",
    ) -> str:
        workflow_key = _stable_identifier(workflow_id, "workflow_id")
        deadline = _finite_number(deadline_hint, "deadline_hint", minimum=0.0)
        event_time = _finite_number(timestamp, "timestamp", minimum=0.0)
        source_name = self._validate_token(source, "source")
        clock_name = self._validate_token(clock_domain, "clock_domain")
        if workflow_key in self._workflows:
            raise WorkflowHintError(f"workflow already registered: {workflow_key}")
        if deadline < event_time:
            raise WorkflowHintError("deadline_hint cannot precede workflow registration")
        self._workflows[workflow_key] = WorkflowHintRecord(
            workflow_id=workflow_key,
            deadline_hint=deadline,
            source=source_name,
            clock_domain=clock_name,
        )
        return workflow_key

    def create_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        parents: Iterable[object],
        dependency_kinds: Optional[Mapping[object, str]] = None,
        request_type: str,
        size_hint: float,
        size_unit: str,
        speculation_level: float,
        timestamp: float,
        source: Optional[str] = None,
    ) -> StepHintRecord:
        workflow = self._workflow(workflow_id)
        if workflow.status != "active":
            raise WorkflowHintError(f"workflow is already finalized: {workflow.workflow_id}")
        step_key = _stable_identifier(step_id, "step_id")
        if step_key in workflow.steps:
            raise WorkflowHintError(
                f"step already registered: {workflow.workflow_id}/{step_key}"
            )

        parent_keys = tuple(_stable_identifier(parent, "parent step_id") for parent in parents)
        if len(set(parent_keys)) != len(parent_keys):
            raise WorkflowHintError(f"duplicate parent for step: {step_key}")
        if step_key in parent_keys:
            raise WorkflowHintError(f"step cannot depend on itself: {step_key}")

        raw_kinds = dependency_kinds or {}
        normalized_kinds = {
            _stable_identifier(parent, "dependency parent"): kind
            for parent, kind in raw_kinds.items()
        }
        if not raw_kinds:
            normalized_kinds = {parent: "hard_dependency" for parent in parent_keys}
        if set(normalized_kinds) != set(parent_keys):
            raise WorkflowHintError("dependency_kinds keys must exactly match parents")
        invalid_kinds = set(normalized_kinds.values()) - DEPENDENCY_KINDS
        if invalid_kinds:
            raise WorkflowHintError(f"invalid dependency kinds: {sorted(invalid_kinds)}")

        if not self.allow_forward_references:
            missing = [parent for parent in parent_keys if parent not in workflow.steps]
            if missing:
                raise WorkflowHintError(f"missing parent steps: {missing}")

        request_name = self._validate_token(request_type, "request_type")
        size = _finite_number(size_hint, "size_hint", minimum=0.0)
        unit = self._validate_token(size_unit, "size_unit")
        speculation = _finite_number(
            speculation_level,
            "speculation_level",
            minimum=0.0,
        )
        if speculation > 1.0:
            raise WorkflowHintError("speculation_level must not exceed 1.0")
        event_time = _finite_number(timestamp, "timestamp", minimum=0.0)
        source_name = self._validate_token(source or workflow.source, "source")

        record = StepHintRecord(
            workflow_id=workflow.workflow_id,
            step_id=step_key,
            parents=parent_keys,
            dependency_kinds=normalized_kinds,
            request_type=request_name,
            deadline_hint=workflow.deadline_hint,
            size_hint=size,
            size_unit=unit,
            speculation_level=speculation,
            source=source_name,
            last_timestamp=event_time,
        )
        workflow.steps[step_key] = record
        try:
            self._validate_acyclic(workflow)
        except Exception:
            del workflow.steps[step_key]
            raise
        self._emit(record, "created", event_time)
        return record

    def mark_ready(self, workflow_id: object, step_id: object, *, timestamp: float) -> None:
        workflow, step = self._step(workflow_id, step_id)
        self._require_state(step, {"created"}, "ready")
        incomplete_hard_parents = [
            parent
            for parent, kind in step.dependency_kinds.items()
            if kind == "hard_dependency"
            and (
                parent not in workflow.steps
                or workflow.steps[parent].state != "completed"
            )
        ]
        if incomplete_hard_parents:
            raise WorkflowHintError(
                f"hard dependencies are not completed for {step.step_id}: "
                f"{incomplete_hard_parents}"
            )
        self._transition(step, "ready", "ready", timestamp)

    def start_step(self, workflow_id: object, step_id: object, *, timestamp: float) -> None:
        _, step = self._step(workflow_id, step_id)
        self._require_state(step, {"ready"}, "start")
        self._transition(step, "running", "started", timestamp)

    def complete_step(self, workflow_id: object, step_id: object, *, timestamp: float) -> None:
        _, step = self._step(workflow_id, step_id)
        self._require_state(step, {"running"}, "complete")
        self._transition(step, "completed", "completed", timestamp)

    def fail_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
        reason: str = "execution_failed",
    ) -> None:
        _, step = self._step(workflow_id, step_id)
        self._require_state(step, {"running"}, "fail")
        self._transition(step, "failed", "failed", timestamp, reason=reason)

    def retry_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
        reason: str = "retry_requested",
    ) -> int:
        _, step = self._step(workflow_id, step_id)
        self._require_state(step, {"failed"}, "retry")
        event_time = self._validate_step_timestamp(step, timestamp)
        reason_name = self._validate_event_reason("retried", reason)
        step.attempt_id += 1
        step.state = "created"
        step.selected = False
        step.last_timestamp = event_time
        self._emit(step, "retried", event_time, reason=reason_name)
        return step.attempt_id

    def cancel_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
        reason: str = "policy_cancelled",
    ) -> None:
        _, step = self._step(workflow_id, step_id)
        self._require_state(step, {"created", "ready", "running"}, "cancel")
        self._transition(step, "cancelled", "cancelled", timestamp, reason=reason)

    def mark_selected(self, workflow_id: object, step_id: object, *, timestamp: float) -> None:
        _, step = self._step(workflow_id, step_id)
        self._require_state(step, {"completed"}, "select")
        if step.selected:
            raise WorkflowHintError(f"step already selected: {step.step_id}")
        event_time = self._validate_step_timestamp(step, timestamp)
        step.selected = True
        step.last_timestamp = event_time
        self._emit(step, "selected", event_time)

    def finalize_workflow(
        self,
        workflow_id: object,
        *,
        timestamp: float,
        status: str = "completed",
    ) -> None:
        workflow = self._workflow(workflow_id)
        if workflow.status != "active":
            raise WorkflowHintError(f"workflow is already finalized: {workflow.workflow_id}")
        if status not in WORKFLOW_FINAL_STATES:
            raise WorkflowHintError(f"invalid workflow final state: {status}")
        _finite_number(timestamp, "timestamp", minimum=0.0)
        self.validate_workflow(workflow.workflow_id, require_terminal=True)
        workflow.status = status

    def validate_workflow(
        self,
        workflow_id: object,
        *,
        require_terminal: bool = False,
    ) -> None:
        workflow = self._workflow(workflow_id)
        missing = sorted(
            {
                parent
                for step in workflow.steps.values()
                for parent in step.parents
                if parent not in workflow.steps
            }
        )
        if missing:
            raise WorkflowHintError(f"missing parent steps: {missing}")
        self._validate_acyclic(workflow)
        if require_terminal:
            active = sorted(
                step.step_id
                for step in workflow.steps.values()
                if step.state not in TERMINAL_STEP_STATES
            )
            if active:
                raise WorkflowHintError(f"workflow has non-terminal steps: {active}")

    def validate_all(self, *, require_terminal: bool = False) -> None:
        for workflow_id in self._workflows:
            self.validate_workflow(workflow_id, require_terminal=require_terminal)

    def step(self, workflow_id: object, step_id: object) -> StepHintRecord:
        return self._step(workflow_id, step_id)[1]

    def summary(self) -> Dict[str, object]:
        steps = [step for workflow in self._workflows.values() for step in workflow.steps.values()]
        dependency_counts: Counter[str] = Counter()
        for step in steps:
            dependency_counts.update(step.dependency_kinds.values())
        speculation_counts = Counter(
            "required"
            if step.speculation_level == 0.0
            else "fully_speculative"
            if step.speculation_level == 1.0
            else "partially_speculative"
            for step in steps
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "workflows_registered": len(self._workflows),
            "workflows_finalized": sum(
                workflow.status != "active" for workflow in self._workflows.values()
            ),
            "steps_recorded": len(steps),
            "events_recorded": len(self._events),
            "events_by_type": dict(sorted(Counter(event.event for event in self._events).items())),
            "event_reasons": dict(
                sorted(Counter(event.reason for event in self._events if event.reason).items())
            ),
            "request_types": dict(sorted(Counter(step.request_type for step in steps).items())),
            "dependency_kinds": dict(sorted(dependency_counts.items())),
            "speculation_levels": dict(sorted(speculation_counts.items())),
            "selected_steps": sum(step.selected for step in steps),
            "workflow_statuses": dict(
                sorted(Counter(workflow.status for workflow in self._workflows.values()).items())
            ),
            "sources": dict(sorted(Counter(step.source for step in steps).items())),
            "validation_errors": 0,
        }

    def _workflow(self, workflow_id: object) -> WorkflowHintRecord:
        workflow_key = _stable_identifier(workflow_id, "workflow_id")
        try:
            return self._workflows[workflow_key]
        except KeyError as exc:
            raise WorkflowHintError(f"workflow is not registered: {workflow_key}") from exc

    def _step(
        self,
        workflow_id: object,
        step_id: object,
    ) -> Tuple[WorkflowHintRecord, StepHintRecord]:
        workflow = self._workflow(workflow_id)
        step_key = _stable_identifier(step_id, "step_id")
        try:
            return workflow, workflow.steps[step_key]
        except KeyError as exc:
            raise WorkflowHintError(
                f"step is not registered: {workflow.workflow_id}/{step_key}"
            ) from exc

    @staticmethod
    def _validate_token(value: object, label: str) -> str:
        text = str(value)
        if not REQUEST_TYPE_PATTERN.fullmatch(text):
            raise WorkflowHintError(f"invalid {label}: {value!r}")
        return text

    @staticmethod
    def _require_state(step: StepHintRecord, allowed: set[str], action: str) -> None:
        if step.state not in allowed:
            raise WorkflowHintError(
                f"cannot {action} step {step.step_id} from state {step.state}"
            )

    def _transition(
        self,
        step: StepHintRecord,
        state: str,
        event: str,
        timestamp: float,
        *,
        reason: str = "",
    ) -> None:
        event_time = self._validate_step_timestamp(step, timestamp)
        reason_name = self._validate_event_reason(event, reason)
        step.state = state
        step.last_timestamp = event_time
        self._emit(step, event, event_time, reason=reason_name)

    @staticmethod
    def _validate_step_timestamp(step: StepHintRecord, timestamp: float) -> float:
        event_time = _finite_number(timestamp, "timestamp", minimum=0.0)
        if event_time < step.last_timestamp:
            raise WorkflowHintError(
                f"timestamp moved backwards for {step.step_id}: "
                f"{event_time} < {step.last_timestamp}"
            )
        return event_time

    @staticmethod
    def _validate_event_reason(event: str, reason: object) -> str:
        reason_name = str(reason)
        if reason_name and reason_name not in EVENT_REASONS:
            raise WorkflowHintError(f"invalid event reason: {reason!r}")
        if reason_name and event not in REASONED_STEP_EVENTS:
            raise WorkflowHintError(f"event {event} does not accept a reason")
        return reason_name

    def _emit(
        self,
        step: StepHintRecord,
        event: str,
        timestamp: float,
        *,
        reason: str = "",
    ) -> None:
        if event not in STEP_EVENTS:
            raise WorkflowHintError(f"invalid step event: {event}")
        reason_name = self._validate_event_reason(event, reason)
        self._events.append(
            WorkflowHintEvent(
                sequence=len(self._events),
                workflow_id=step.workflow_id,
                step_id=step.step_id,
                attempt_id=step.attempt_id,
                parents=step.parents,
                dependency_kinds=tuple(sorted(step.dependency_kinds.items())),
                request_type=step.request_type,
                deadline_hint=step.deadline_hint,
                size_hint=step.size_hint,
                size_unit=step.size_unit,
                speculation_level=step.speculation_level,
                event=event,
                reason=reason_name,
                timestamp=timestamp,
                source=step.source,
            )
        )

    @staticmethod
    def _validate_acyclic(workflow: WorkflowHintRecord) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise WorkflowHintError(f"workflow DAG contains a cycle at step: {step_id}")
            if step_id in visited or step_id not in workflow.steps:
                return
            visiting.add(step_id)
            for parent in workflow.steps[step_id].parents:
                visit(parent)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in workflow.steps:
            visit(step_id)
