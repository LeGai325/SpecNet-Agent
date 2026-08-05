"""Runtime execution model for dynamically evolving workflow DAGs.

The engine owns dependency resolution and step lifecycle state.  It is kept
independent from the network simulator: a caller turns ready steps into flows,
binds those flows with :meth:`start_step`, and reports flow completion or
failure back to the engine.  An optional WorkflowHintCollector observes the
same transitions without participating in any decision.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from workflow_hints import DEPENDENCY_KINDS, WorkflowHintCollector
except ImportError:  # pragma: no cover - package-style imports
    from .workflow_hints import DEPENDENCY_KINDS, WorkflowHintCollector


ACTIVE_STEP_STATES = {"created", "ready", "running"}
TERMINAL_STEP_STATES = {"completed", "failed", "cancelled"}
WORKFLOW_FINAL_STATES = {"completed", "timed_out", "failed", "cancelled"}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]*$")


class DynamicDAGError(ValueError):
    """Raised when a graph operation violates the runtime contract."""


def _identifier(value: object, label: str) -> str:
    text = str(value)
    if not text or not IDENTIFIER_PATTERN.fullmatch(text):
        raise DynamicDAGError(f"invalid {label}: {value!r}")
    return text


def _token(value: object, label: str) -> str:
    text = str(value)
    if not TOKEN_PATTERN.fullmatch(text):
        raise DynamicDAGError(f"invalid {label}: {value!r}")
    return text


def _finite_number(value: object, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DynamicDAGError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise DynamicDAGError(f"{label} must be a finite number >= {minimum}")
    return result


@dataclass(frozen=True)
class StepSpec:
    """Immutable description of one logical workflow step."""

    step_id: str
    parents: Tuple[str, ...] = ()
    dependency_kinds: Mapping[str, str] = field(default_factory=dict)
    request_type: str = "tool"
    size_hint: float = 0.0
    size_unit: str = "normalized_work"
    speculation_level: float = 0.0
    retry_limit: int = 0
    source: str = "dynamic_dag_engine"


@dataclass
class StepRuntime:
    """Mutable lifecycle state for a StepSpec."""

    spec: StepSpec
    state: str = "created"
    attempt_id: int = 0
    flow_id: Optional[str] = None
    created_at: float = 0.0
    ready_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    failure_count: int = 0
    selected_by_judge: bool = False
    cancel_reason: Optional[str] = None

    @property
    def step_id(self) -> str:
        return self.spec.step_id


@dataclass(frozen=True)
class FlowBinding:
    """One immutable mapping from a network flow to a step attempt."""

    flow_id: str
    workflow_id: str
    step_id: str
    attempt_id: int


@dataclass(frozen=True)
class FlowCancellation:
    """Request returned when a running step needs its flow cancelled."""

    flow_id: str
    workflow_id: str
    step_id: str
    attempt_id: int
    reason: str


@dataclass
class WorkflowGraph:
    """Runtime graph and deterministic ready queue for one workflow."""

    workflow_id: str
    deadline_hint: float
    source: str
    clock_domain: str
    registered_at: float
    steps: Dict[str, StepRuntime] = field(default_factory=dict)
    children: Dict[str, Set[str]] = field(default_factory=dict)
    ready_queue: List[str] = field(default_factory=list)
    status: str = "active"
    graph_version: int = 0
    last_timestamp: float = 0.0


class DynamicDAGEngine:
    """Execute dynamic workflow graphs while remaining simulator-neutral."""

    def __init__(self, *, collector: Optional[WorkflowHintCollector] = None) -> None:
        self.collector = collector
        self._workflows: Dict[str, WorkflowGraph] = {}
        self._flow_bindings: Dict[str, FlowBinding] = {}

    @property
    def workflows(self) -> Mapping[str, WorkflowGraph]:
        return self._workflows

    @property
    def flow_bindings(self) -> Mapping[str, FlowBinding]:
        return self._flow_bindings

    def flow_binding(self, flow_id: object) -> FlowBinding:
        """Return the immutable step-attempt binding for an external flow."""

        return self._binding(flow_id)

    def register_workflow(
        self,
        workflow_id: object,
        *,
        deadline_hint: float,
        timestamp: float,
        source: str = "dynamic_dag_engine",
        clock_domain: str = "simulator_step",
    ) -> WorkflowGraph:
        workflow_key = _identifier(workflow_id, "workflow_id")
        deadline = _finite_number(deadline_hint, "deadline_hint")
        event_time = _finite_number(timestamp, "timestamp")
        source_name = _token(source, "source")
        clock_name = _token(clock_domain, "clock_domain")
        if workflow_key in self._workflows:
            raise DynamicDAGError(f"workflow already registered: {workflow_key}")
        if deadline < event_time:
            raise DynamicDAGError("deadline_hint cannot precede workflow registration")

        if self.collector is not None:
            self.collector.register_workflow(
                workflow_key,
                deadline_hint=deadline,
                timestamp=event_time,
                source=source_name,
                clock_domain=clock_name,
            )
        graph = WorkflowGraph(
            workflow_id=workflow_key,
            deadline_hint=deadline,
            source=source_name,
            clock_domain=clock_name,
            registered_at=event_time,
            last_timestamp=event_time,
        )
        self._workflows[workflow_key] = graph
        return graph

    def add_step(
        self,
        workflow_id: object,
        spec: StepSpec,
        *,
        timestamp: float,
    ) -> StepRuntime:
        graph = self._active_workflow(workflow_id)
        event_time = self._timestamp(graph, timestamp)
        normalized = self._normalize_spec(spec)
        if normalized.step_id in graph.steps:
            raise DynamicDAGError(
                f"step already registered: {graph.workflow_id}/{normalized.step_id}"
            )
        missing = [parent for parent in normalized.parents if parent not in graph.steps]
        if missing:
            raise DynamicDAGError(f"missing parent steps: {missing}")

        if self.collector is not None:
            self.collector.create_step(
                graph.workflow_id,
                normalized.step_id,
                parents=normalized.parents,
                dependency_kinds=normalized.dependency_kinds,
                request_type=normalized.request_type,
                size_hint=normalized.size_hint,
                size_unit=normalized.size_unit,
                speculation_level=normalized.speculation_level,
                timestamp=event_time,
                source=normalized.source,
            )

        runtime = StepRuntime(spec=normalized, created_at=event_time)
        graph.steps[normalized.step_id] = runtime
        graph.children.setdefault(normalized.step_id, set())
        for parent in normalized.parents:
            graph.children.setdefault(parent, set()).add(normalized.step_id)
        self._mutated(graph, event_time)
        if self._hard_dependencies_completed(graph, runtime):
            self._mark_ready(graph, runtime, event_time)
        self.validate_workflow(graph.workflow_id)
        return runtime

    def ready_steps(self, workflow_id: object) -> Tuple[StepRuntime, ...]:
        graph = self._workflow(workflow_id)
        return tuple(
            graph.steps[step_id]
            for step_id in graph.ready_queue
            if graph.steps[step_id].state == "ready"
        )

    def start_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        flow_id: object,
        timestamp: float,
    ) -> FlowBinding:
        graph, step = self._step(workflow_id, step_id)
        self._require_active(graph)
        self._require_state(step, {"ready"}, "start")
        event_time = self._timestamp(graph, timestamp)
        flow_key = _identifier(flow_id, "flow_id")
        if flow_key in self._flow_bindings:
            raise DynamicDAGError(f"flow already bound: {flow_key}")

        if self.collector is not None:
            self.collector.start_step(graph.workflow_id, step.step_id, timestamp=event_time)
        step.state = "running"
        step.flow_id = flow_key
        step.started_at = event_time
        graph.ready_queue.remove(step.step_id)
        binding = FlowBinding(
            flow_id=flow_key,
            workflow_id=graph.workflow_id,
            step_id=step.step_id,
            attempt_id=step.attempt_id,
        )
        self._flow_bindings[flow_key] = binding
        self._mutated(graph, event_time)
        return binding

    def complete_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
        flow_id: Optional[object] = None,
    ) -> Tuple[str, ...]:
        graph, step = self._step(workflow_id, step_id)
        self._require_active(graph)
        self._require_state(step, {"running"}, "complete")
        self._verify_flow(step, flow_id)
        event_time = self._timestamp(graph, timestamp)

        if self.collector is not None:
            self.collector.complete_step(graph.workflow_id, step.step_id, timestamp=event_time)
        step.state = "completed"
        step.completed_at = event_time
        self._mutated(graph, event_time)
        return self._unlock_children(graph, step.step_id, event_time)

    def complete_flow(self, flow_id: object, *, timestamp: float) -> Tuple[str, ...]:
        binding = self._binding(flow_id)
        return self.complete_step(
            binding.workflow_id,
            binding.step_id,
            timestamp=timestamp,
            flow_id=binding.flow_id,
        )

    def fail_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
        flow_id: Optional[object] = None,
    ) -> None:
        graph, step = self._step(workflow_id, step_id)
        self._require_active(graph)
        self._require_state(step, {"running"}, "fail")
        self._verify_flow(step, flow_id)
        event_time = self._timestamp(graph, timestamp)

        if self.collector is not None:
            self.collector.fail_step(graph.workflow_id, step.step_id, timestamp=event_time)
        step.state = "failed"
        step.completed_at = event_time
        step.failure_count += 1
        self._mutated(graph, event_time)

    def fail_flow(self, flow_id: object, *, timestamp: float) -> None:
        binding = self._binding(flow_id)
        self.fail_step(
            binding.workflow_id,
            binding.step_id,
            timestamp=timestamp,
            flow_id=binding.flow_id,
        )

    def retry_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
    ) -> StepRuntime:
        graph, step = self._step(workflow_id, step_id)
        self._require_active(graph)
        self._require_state(step, {"failed"}, "retry")
        if step.attempt_id >= step.spec.retry_limit:
            raise DynamicDAGError(
                f"retry limit exhausted for {graph.workflow_id}/{step.step_id}"
            )
        event_time = self._timestamp(graph, timestamp)

        if self.collector is not None:
            self.collector.retry_step(graph.workflow_id, step.step_id, timestamp=event_time)
        step.attempt_id += 1
        step.state = "created"
        step.flow_id = None
        step.ready_at = None
        step.started_at = None
        step.completed_at = None
        step.selected_by_judge = False
        step.cancel_reason = None
        self._mutated(graph, event_time)
        if self._hard_dependencies_completed(graph, step):
            self._mark_ready(graph, step, event_time)
        return step

    def cancel_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
        reason: str = "cancelled",
    ) -> Optional[FlowCancellation]:
        graph, step = self._step(workflow_id, step_id)
        self._require_active(graph)
        self._require_state(step, ACTIVE_STEP_STATES, "cancel")
        event_time = self._timestamp(graph, timestamp)
        reason_name = _token(reason, "cancel_reason")
        previous_state = step.state

        if self.collector is not None:
            self.collector.cancel_step(graph.workflow_id, step.step_id, timestamp=event_time)
        step.state = "cancelled"
        step.completed_at = event_time
        step.cancel_reason = reason_name
        if step.step_id in graph.ready_queue:
            graph.ready_queue.remove(step.step_id)
        self._mutated(graph, event_time)
        if previous_state == "running" and step.flow_id is not None:
            return FlowCancellation(
                flow_id=step.flow_id,
                workflow_id=graph.workflow_id,
                step_id=step.step_id,
                attempt_id=step.attempt_id,
                reason=reason_name,
            )
        return None

    def cancel_flow(
        self,
        flow_id: object,
        *,
        timestamp: float,
        reason: str = "cancelled",
    ) -> Optional[FlowCancellation]:
        binding = self._binding(flow_id)
        return self.cancel_step(
            binding.workflow_id,
            binding.step_id,
            timestamp=timestamp,
            reason=reason,
        )

    def select_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
    ) -> None:
        graph, step = self._step(workflow_id, step_id)
        self._require_active(graph)
        self._require_state(step, {"completed"}, "select")
        if step.selected_by_judge:
            raise DynamicDAGError(f"step already selected: {step.step_id}")
        event_time = self._timestamp(graph, timestamp)

        if self.collector is not None:
            self.collector.mark_selected(graph.workflow_id, step.step_id, timestamp=event_time)
        step.selected_by_judge = True
        self._mutated(graph, event_time)

    def prune_subgraph(
        self,
        workflow_id: object,
        root_step_ids: Sequence[object] | object,
        *,
        timestamp: float,
        reason: str = "judge_pruned",
    ) -> Tuple[FlowCancellation, ...]:
        """Cancel optional roots and their exclusive optional descendants.

        A descendant joins the prune set only when all of its parents are
        already in that set.  If an active step outside the set has a hard
        dependency on a pruned step, pruning is rejected rather than silently
        leaving the workflow deadlocked.
        """

        graph = self._active_workflow(workflow_id)
        event_time = self._timestamp(graph, timestamp)
        if isinstance(root_step_ids, (str, int)):
            raw_roots: Iterable[object] = (root_step_ids,)
        else:
            raw_roots = root_step_ids
        roots = tuple(_identifier(root, "root step_id") for root in raw_roots)
        if not roots:
            raise DynamicDAGError("at least one prune root is required")
        missing = [root for root in roots if root not in graph.steps]
        if missing:
            raise DynamicDAGError(f"step is not registered: {missing}")

        candidates = set(roots)
        changed = True
        while changed:
            changed = False
            for parent in tuple(candidates):
                for child_id in graph.children.get(parent, set()):
                    child = graph.steps[child_id]
                    if child_id not in candidates and set(child.spec.parents) <= candidates:
                        candidates.add(child_id)
                        changed = True

        active_candidates = {
            step_id for step_id in candidates if graph.steps[step_id].state in ACTIVE_STEP_STATES
        }
        required = sorted(
            step_id
            for step_id in active_candidates
            if graph.steps[step_id].spec.speculation_level <= 0.0
        )
        if required:
            raise DynamicDAGError(f"cannot prune required steps: {required}")

        blocked = []
        for step_id, step in graph.steps.items():
            if step_id in candidates or step.state not in ACTIVE_STEP_STATES:
                continue
            hard_parents = {
                parent
                for parent, kind in step.spec.dependency_kinds.items()
                if kind == "hard_dependency"
            }
            if hard_parents & active_candidates:
                blocked.append(step_id)
        if blocked:
            raise DynamicDAGError(
                f"cannot prune steps required by active hard-dependency children: {sorted(blocked)}"
            )

        cancellations: List[FlowCancellation] = []
        for step_id in sorted(active_candidates, reverse=True):
            cancellation = self.cancel_step(
                graph.workflow_id,
                step_id,
                timestamp=event_time,
                reason=reason,
            )
            if cancellation is not None:
                cancellations.append(cancellation)
        return tuple(cancellations)

    def snapshot(self, workflow_id: object) -> Dict[str, object]:
        graph = self._workflow(workflow_id)
        return {
            "workflow_id": graph.workflow_id,
            "status": graph.status,
            "deadline_hint": graph.deadline_hint,
            "graph_version": graph.graph_version,
            "ready_steps": [
                step_id
                for step_id in graph.ready_queue
                if graph.steps[step_id].state == "ready"
            ],
            "steps": {
                step_id: {
                    "parents": list(step.spec.parents),
                    "dependency_kinds": dict(step.spec.dependency_kinds),
                    "request_type": step.spec.request_type,
                    "size_hint": step.spec.size_hint,
                    "size_unit": step.spec.size_unit,
                    "speculation_level": step.spec.speculation_level,
                    "retry_limit": step.spec.retry_limit,
                    "state": step.state,
                    "attempt_id": step.attempt_id,
                    "flow_id": step.flow_id,
                    "failure_count": step.failure_count,
                    "selected_by_judge": step.selected_by_judge,
                    "cancel_reason": step.cancel_reason,
                }
                for step_id, step in sorted(graph.steps.items())
            },
        }

    def finalize_workflow(
        self,
        workflow_id: object,
        *,
        timestamp: float,
        status: str = "completed",
    ) -> None:
        graph = self._active_workflow(workflow_id)
        if status not in WORKFLOW_FINAL_STATES:
            raise DynamicDAGError(f"invalid workflow final state: {status}")
        event_time = self._timestamp(graph, timestamp)
        self.validate_workflow(graph.workflow_id, require_terminal=True)
        if self.collector is not None:
            self.collector.finalize_workflow(
                graph.workflow_id,
                timestamp=event_time,
                status=status,
            )
        graph.status = status
        self._mutated(graph, event_time)

    def validate_workflow(
        self,
        workflow_id: object,
        *,
        require_terminal: bool = False,
    ) -> None:
        graph = self._workflow(workflow_id)
        for step_id, step in graph.steps.items():
            if step_id in step.spec.parents:
                raise DynamicDAGError(f"step cannot depend on itself: {step_id}")
            missing = [parent for parent in step.spec.parents if parent not in graph.steps]
            if missing:
                raise DynamicDAGError(f"missing parent steps: {missing}")
        self._validate_acyclic(graph)
        queue_steps = [
            step_id for step_id in graph.ready_queue if graph.steps[step_id].state == "ready"
        ]
        expected_ready = [
            step_id for step_id, step in graph.steps.items() if step.state == "ready"
        ]
        if len(queue_steps) != len(set(queue_steps)) or set(queue_steps) != set(expected_ready):
            raise DynamicDAGError("ready queue is inconsistent with step states")
        if require_terminal:
            active = sorted(
                step_id
                for step_id, step in graph.steps.items()
                if step.state not in TERMINAL_STEP_STATES
            )
            if active:
                raise DynamicDAGError(f"workflow has non-terminal steps: {active}")

    def _unlock_children(
        self,
        graph: WorkflowGraph,
        parent_id: str,
        timestamp: float,
    ) -> Tuple[str, ...]:
        unlocked: List[str] = []
        for child_id in sorted(graph.children.get(parent_id, set())):
            child = graph.steps[child_id]
            if child.state == "created" and self._hard_dependencies_completed(graph, child):
                self._mark_ready(graph, child, timestamp)
                unlocked.append(child_id)
        return tuple(unlocked)

    def _mark_ready(
        self,
        graph: WorkflowGraph,
        step: StepRuntime,
        timestamp: float,
    ) -> None:
        if self.collector is not None:
            self.collector.mark_ready(graph.workflow_id, step.step_id, timestamp=timestamp)
        step.state = "ready"
        step.ready_at = timestamp
        if step.step_id not in graph.ready_queue:
            graph.ready_queue.append(step.step_id)
        self._mutated(graph, timestamp)

    @staticmethod
    def _hard_dependencies_completed(graph: WorkflowGraph, step: StepRuntime) -> bool:
        return all(
            graph.steps[parent].state == "completed"
            for parent, kind in step.spec.dependency_kinds.items()
            if kind == "hard_dependency"
        )

    @staticmethod
    def _normalize_spec(spec: StepSpec) -> StepSpec:
        if not isinstance(spec, StepSpec):
            raise DynamicDAGError("spec must be a StepSpec")
        step_id = _identifier(spec.step_id, "step_id")
        parents = tuple(_identifier(parent, "parent step_id") for parent in spec.parents)
        if len(parents) != len(set(parents)):
            raise DynamicDAGError(f"duplicate parent for step: {step_id}")
        if step_id in parents:
            raise DynamicDAGError(f"step cannot depend on itself: {step_id}")
        raw_kinds = dict(spec.dependency_kinds)
        if not raw_kinds:
            raw_kinds = {parent: "hard_dependency" for parent in parents}
        normalized_kinds = {
            _identifier(parent, "dependency parent"): str(kind)
            for parent, kind in raw_kinds.items()
        }
        if set(normalized_kinds) != set(parents):
            raise DynamicDAGError("dependency_kinds keys must exactly match parents")
        invalid = set(normalized_kinds.values()) - DEPENDENCY_KINDS
        if invalid:
            raise DynamicDAGError(f"invalid dependency kinds: {sorted(invalid)}")
        request_type = _token(spec.request_type, "request_type")
        size_hint = _finite_number(spec.size_hint, "size_hint")
        size_unit = _token(spec.size_unit, "size_unit")
        speculation_level = _finite_number(spec.speculation_level, "speculation_level")
        if speculation_level > 1.0:
            raise DynamicDAGError("speculation_level must not exceed 1.0")
        if isinstance(spec.retry_limit, bool) or not isinstance(spec.retry_limit, int):
            raise DynamicDAGError("retry_limit must be a non-negative integer")
        if spec.retry_limit < 0:
            raise DynamicDAGError("retry_limit must be a non-negative integer")
        source = _token(spec.source, "source")
        return StepSpec(
            step_id=step_id,
            parents=parents,
            dependency_kinds=normalized_kinds,
            request_type=request_type,
            size_hint=size_hint,
            size_unit=size_unit,
            speculation_level=speculation_level,
            retry_limit=spec.retry_limit,
            source=source,
        )

    def _workflow(self, workflow_id: object) -> WorkflowGraph:
        workflow_key = _identifier(workflow_id, "workflow_id")
        try:
            return self._workflows[workflow_key]
        except KeyError as exc:
            raise DynamicDAGError(f"workflow is not registered: {workflow_key}") from exc

    def _active_workflow(self, workflow_id: object) -> WorkflowGraph:
        graph = self._workflow(workflow_id)
        self._require_active(graph)
        return graph

    @staticmethod
    def _require_active(graph: WorkflowGraph) -> None:
        if graph.status != "active":
            raise DynamicDAGError(f"workflow is already finalized: {graph.workflow_id}")

    def _step(
        self,
        workflow_id: object,
        step_id: object,
    ) -> Tuple[WorkflowGraph, StepRuntime]:
        graph = self._workflow(workflow_id)
        step_key = _identifier(step_id, "step_id")
        try:
            return graph, graph.steps[step_key]
        except KeyError as exc:
            raise DynamicDAGError(
                f"step is not registered: {graph.workflow_id}/{step_key}"
            ) from exc

    def _binding(self, flow_id: object) -> FlowBinding:
        flow_key = _identifier(flow_id, "flow_id")
        try:
            return self._flow_bindings[flow_key]
        except KeyError as exc:
            raise DynamicDAGError(f"flow is not bound: {flow_key}") from exc

    @staticmethod
    def _require_state(step: StepRuntime, allowed: Set[str], action: str) -> None:
        if step.state not in allowed:
            raise DynamicDAGError(
                f"cannot {action} step {step.step_id} from state {step.state}"
            )

    @staticmethod
    def _verify_flow(step: StepRuntime, flow_id: Optional[object]) -> None:
        if flow_id is None:
            return
        flow_key = _identifier(flow_id, "flow_id")
        if step.flow_id != flow_key:
            raise DynamicDAGError(
                f"flow {flow_key} is not bound to step {step.step_id} attempt {step.attempt_id}"
            )

    @staticmethod
    def _timestamp(graph: WorkflowGraph, timestamp: float) -> float:
        event_time = _finite_number(timestamp, "timestamp")
        if event_time < graph.last_timestamp:
            raise DynamicDAGError(
                f"timestamp moved backwards for workflow {graph.workflow_id}: "
                f"{event_time} < {graph.last_timestamp}"
            )
        return event_time

    @staticmethod
    def _mutated(graph: WorkflowGraph, timestamp: float) -> None:
        graph.graph_version += 1
        graph.last_timestamp = timestamp

    @staticmethod
    def _validate_acyclic(graph: WorkflowGraph) -> None:
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise DynamicDAGError(f"workflow DAG contains a cycle at step: {step_id}")
            if step_id in visited:
                return
            visiting.add(step_id)
            for parent in graph.steps[step_id].spec.parents:
                visit(parent)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph.steps:
            visit(step_id)


class DynamicDAGFlowBridge:
    """Connect ready DAG steps to an external flow implementation.

    ``create_flow`` receives the workflow ID and ready StepRuntime and returns
    the external flow ID. ``cancel_flow`` is called for running flows pruned by
    the DAG.  The bridge deliberately does not know about Simulator, Flow, or
    scheduling policy classes, which keeps the runtime reusable and avoids a
    circular import with the experiment script.
    """

    def __init__(
        self,
        engine: DynamicDAGEngine,
        *,
        create_flow: Callable[[str, StepRuntime], object],
        cancel_flow: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.engine = engine
        self.create_flow = create_flow
        self.cancel_flow = cancel_flow

    def dispatch_ready(
        self,
        workflow_id: object,
        *,
        timestamp: float,
    ) -> Tuple[FlowBinding, ...]:
        """Create and bind one external flow for every currently ready step."""

        bindings: List[FlowBinding] = []
        # Snapshot the queue because start_step mutates it.
        for step in tuple(self.engine.ready_steps(workflow_id)):
            external_flow_id = self.create_flow(str(workflow_id), step)
            bindings.append(
                self.engine.start_step(
                    workflow_id,
                    step.step_id,
                    flow_id=external_flow_id,
                    timestamp=timestamp,
                )
            )
        return tuple(bindings)

    def on_flow_completed(
        self,
        flow_id: object,
        *,
        timestamp: float,
        dispatch_unlocked: bool = True,
    ) -> Tuple[FlowBinding, ...]:
        """Reflect flow completion and optionally launch newly unlocked steps."""

        binding = self.engine.flow_binding(flow_id)
        self.engine.complete_flow(flow_id, timestamp=timestamp)
        if not dispatch_unlocked:
            return ()
        return self.dispatch_ready(binding.workflow_id, timestamp=timestamp)

    def on_flow_failed(
        self,
        flow_id: object,
        *,
        timestamp: float,
    ) -> None:
        self.engine.fail_flow(flow_id, timestamp=timestamp)

    def retry_step(
        self,
        workflow_id: object,
        step_id: object,
        *,
        timestamp: float,
    ) -> Tuple[FlowBinding, ...]:
        self.engine.retry_step(workflow_id, step_id, timestamp=timestamp)
        return self.dispatch_ready(workflow_id, timestamp=timestamp)

    def prune_subgraph(
        self,
        workflow_id: object,
        root_step_ids: Sequence[object] | object,
        *,
        timestamp: float,
        reason: str = "judge_pruned",
    ) -> Tuple[FlowCancellation, ...]:
        cancellations = self.engine.prune_subgraph(
            workflow_id,
            root_step_ids,
            timestamp=timestamp,
            reason=reason,
        )
        if self.cancel_flow is not None:
            for cancellation in cancellations:
                self.cancel_flow(cancellation.flow_id, cancellation.reason)
        return cancellations
