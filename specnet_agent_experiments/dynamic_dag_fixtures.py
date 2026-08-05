"""Deterministic dynamic-DAG fixtures for preflight and regression tests.

These fixtures exercise runtime graph semantics; they are not presented as
trace-driven Agent workloads or semantic Planner/Judge implementations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

try:
    from dynamic_dag import DynamicDAGEngine, FlowCancellation, StepSpec
    from workflow_hints import WorkflowHintCollector
except ImportError:  # pragma: no cover - package-style imports
    from .dynamic_dag import DynamicDAGEngine, FlowCancellation, StepSpec
    from .workflow_hints import WorkflowHintCollector


@dataclass(frozen=True)
class FixtureResult:
    name: str
    snapshot: Dict[str, object]
    collector_summary: Dict[str, object]
    events: Tuple[Dict[str, object], ...]
    flow_cancellations: Tuple[FlowCancellation, ...] = ()

    def summary_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "workflow_status": self.snapshot["status"],
            "graph_version": self.snapshot["graph_version"],
            "step_count": len(self.snapshot["steps"]),
            "event_count": len(self.events),
            "cancelled_flows": len(self.flow_cancellations),
            "validation_errors": self.collector_summary["validation_errors"],
        }


@dataclass
class _FixtureContext:
    name: str
    workflow_id: str = field(init=False)
    collector: WorkflowHintCollector = field(init=False)
    engine: DynamicDAGEngine = field(init=False)
    time: float = 0.0
    flow_sequence: int = 0

    def __post_init__(self) -> None:
        self.workflow_id = f"fixture:{self.name}"
        self.collector = WorkflowHintCollector()
        self.engine = DynamicDAGEngine(collector=self.collector)
        self.engine.register_workflow(
            self.workflow_id,
            deadline_hint=1000.0,
            timestamp=self.time,
            source="dynamic_fixture",
        )

    def tick(self) -> float:
        self.time += 1.0
        return self.time

    def add(
        self,
        step_id: str,
        *,
        parents: Tuple[str, ...] = (),
        dependency_kinds: Dict[str, str] | None = None,
        request_type: str = "tool",
        size_hint: float = 1.0,
        speculation_level: float = 0.0,
        retry_limit: int = 0,
    ) -> None:
        self.engine.add_step(
            self.workflow_id,
            StepSpec(
                step_id=step_id,
                parents=parents,
                dependency_kinds=dependency_kinds or {},
                request_type=request_type,
                size_hint=size_hint,
                speculation_level=speculation_level,
                retry_limit=retry_limit,
                source="dynamic_fixture",
            ),
            timestamp=self.time,
        )

    def start(self, step_id: str) -> str:
        flow_id = f"flow:{self.name}:{self.flow_sequence}"
        self.flow_sequence += 1
        self.engine.start_step(
            self.workflow_id,
            step_id,
            flow_id=flow_id,
            timestamp=self.tick(),
        )
        return flow_id

    def complete(self, flow_id: str) -> Tuple[str, ...]:
        return self.engine.complete_flow(flow_id, timestamp=self.tick())

    def run(self, step_id: str) -> Tuple[str, ...]:
        return self.complete(self.start(step_id))

    def fail(self, flow_id: str) -> None:
        self.engine.fail_flow(flow_id, timestamp=self.tick())

    def finish(
        self,
        *,
        cancellations: Tuple[FlowCancellation, ...] = (),
    ) -> FixtureResult:
        self.engine.finalize_workflow(
            self.workflow_id,
            timestamp=self.tick(),
            status="completed",
        )
        self.collector.validate_all(require_terminal=True)
        return FixtureResult(
            name=self.name,
            snapshot=self.engine.snapshot(self.workflow_id),
            collector_summary=self.collector.summary(),
            events=tuple(self.collector.event_dicts()),
            flow_cancellations=cancellations,
        )


def run_rag_supplemental_fixture() -> FixtureResult:
    """Planner adds a second retrieval after an evidence check."""

    context = _FixtureContext("rag_supplemental")
    context.add("planner", request_type="planner", size_hint=2.0)
    context.run("planner")
    context.add(
        "retrieval:0",
        parents=("planner",),
        dependency_kinds={"planner": "control_trigger"},
        request_type="retrieval",
        size_hint=4.0,
        speculation_level=0.5,
    )
    context.run("retrieval:0")
    context.add(
        "evidence_check",
        parents=("retrieval:0",),
        request_type="judge",
    )
    context.run("evidence_check")

    # This step is intentionally created only after the first result is judged
    # insufficient, demonstrating online graph growth.
    context.add(
        "retrieval:1",
        parents=("evidence_check",),
        dependency_kinds={"evidence_check": "control_trigger"},
        request_type="retrieval",
        size_hint=6.0,
        speculation_level=0.8,
    )
    context.run("retrieval:1")
    context.add(
        "llm",
        parents=("retrieval:0", "retrieval:1"),
        dependency_kinds={
            "retrieval:0": "optional_evidence",
            "retrieval:1": "optional_evidence",
        },
        request_type="llm",
        size_hint=8.0,
    )
    context.run("llm")
    context.add("judge", parents=("llm",), request_type="judge")
    context.run("judge")
    context.engine.select_step(
        context.workflow_id,
        "retrieval:0",
        timestamp=context.tick(),
    )
    context.engine.select_step(
        context.workflow_id,
        "retrieval:1",
        timestamp=context.tick(),
    )
    return context.finish()


def run_coding_retry_fixture() -> FixtureResult:
    """A tool attempt fails once, retries, and then unlocks patch generation."""

    context = _FixtureContext("coding_retry")
    context.add("planner", request_type="planner")
    context.run("planner")
    context.add("test", parents=("planner",), request_type="tool")
    context.run("test")
    context.add("tool", parents=("test",), request_type="tool", retry_limit=1)
    first_attempt = context.start("tool")
    context.fail(first_attempt)
    context.engine.retry_step(
        context.workflow_id,
        "tool",
        timestamp=context.tick(),
    )
    context.run("tool")
    context.add("patch_llm", parents=("tool",), request_type="llm", size_hint=8.0)
    context.run("patch_llm")
    context.add("judge", parents=("patch_llm",), request_type="judge")
    context.run("judge")
    return context.finish()


def run_judge_pruning_fixture() -> FixtureResult:
    """Judge keeps a completed candidate and prunes two running branches."""

    context = _FixtureContext("judge_pruning")
    context.add("planner", request_type="planner")
    context.run("planner")
    branch_flows: Dict[str, str] = {}
    for branch in ("branch:a", "branch:b", "branch:c"):
        context.add(
            branch,
            parents=("planner",),
            dependency_kinds={"planner": "control_trigger"},
            request_type="llm",
            size_hint=5.0,
            speculation_level=1.0,
        )
    for branch in ("branch:a", "branch:b", "branch:c"):
        branch_flows[branch] = context.start(branch)
    context.complete(branch_flows["branch:a"])
    context.add(
        "judge",
        parents=("branch:a", "branch:b", "branch:c"),
        dependency_kinds={
            "branch:a": "hard_dependency",
            "branch:b": "optional_evidence",
            "branch:c": "optional_evidence",
        },
        request_type="judge",
    )
    context.run("judge")
    context.engine.select_step(
        context.workflow_id,
        "branch:a",
        timestamp=context.tick(),
    )
    cancellations = context.engine.prune_subgraph(
        context.workflow_id,
        ("branch:b", "branch:c"),
        timestamp=context.tick(),
        reason="judge_pruned",
    )
    return context.finish(cancellations=cancellations)


def run_parallel_join_fixture() -> FixtureResult:
    """Three parallel required steps must all complete before aggregation."""

    context = _FixtureContext("parallel_join")
    context.add("planner", request_type="planner")
    context.run("planner")
    parallel_steps = ("retrieval", "tool", "llm_branch")
    request_types = {"retrieval": "retrieval", "tool": "tool", "llm_branch": "llm"}
    for step_id in parallel_steps:
        context.add(
            step_id,
            parents=("planner",),
            dependency_kinds={"planner": "control_trigger"},
            request_type=request_types[step_id],
            size_hint=4.0,
        )
    flows = {step_id: context.start(step_id) for step_id in parallel_steps}
    context.add(
        "aggregator",
        parents=parallel_steps,
        request_type="tool",
        size_hint=2.0,
    )
    for step_id in ("tool", "retrieval", "llm_branch"):
        context.complete(flows[step_id])
    context.run("aggregator")
    context.add("judge", parents=("aggregator",), request_type="judge")
    context.run("judge")
    return context.finish()


FIXTURE_RUNNERS = {
    "rag_supplemental": run_rag_supplemental_fixture,
    "coding_retry": run_coding_retry_fixture,
    "judge_pruning": run_judge_pruning_fixture,
    "parallel_join": run_parallel_join_fixture,
}


def run_all_fixtures() -> Tuple[FixtureResult, ...]:
    return tuple(runner() for runner in FIXTURE_RUNNERS.values())


def main() -> None:
    print(json.dumps([result.summary_dict() for result in run_all_fixtures()], indent=2))


if __name__ == "__main__":
    main()

