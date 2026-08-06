"""Network-backed preflight for the dynamic DAG runtime.

The preflight runs deterministic graph-growth fixtures through the existing
Simulator Flow object and weighted-capacity scheduler.  It validates execution
semantics only; it does not train or evaluate the RL controller.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

try:
    import specnet_agent_experiment as experiment
    from dynamic_dag import DynamicDAGEngine, DynamicDAGFlowBridge, StepRuntime, StepSpec
except ImportError:  # pragma: no cover - package-style imports
    from . import specnet_agent_experiment as experiment
    from .dynamic_dag import DynamicDAGEngine, DynamicDAGFlowBridge, StepRuntime, StepSpec


PREFLIGHT_CAPACITIES = {
    "high_capacity": 32.0,
    "nominal_capacity": 16.0,
    "constrained_capacity": 8.0,
}
FIXTURE_NAMES = (
    "rag_supplemental",
    "coding_retry",
    "judge_pruning",
    "parallel_join",
)


@dataclass(frozen=True)
class NetworkPreflightResult:
    fixture: str
    capacity_label: str
    capacity: float
    completion_time: int
    flows_created: int
    cancelled_flows: int
    logical_failures: int
    retries: int
    snapshot: Dict[str, object]
    collector_summary: Dict[str, object]
    events: Tuple[Dict[str, object], ...]

    def summary_dict(self) -> Dict[str, object]:
        return {
            "fixture": self.fixture,
            "capacity_label": self.capacity_label,
            "capacity": self.capacity,
            "completion_time": self.completion_time,
            "flows_created": self.flows_created,
            "cancelled_flows": self.cancelled_flows,
            "logical_failures": self.logical_failures,
            "retries": self.retries,
            "steps": len(self.snapshot["steps"]),
            "events": len(self.events),
            "workflow_status": self.snapshot["status"],
            "validation_errors": self.collector_summary["validation_errors"],
        }


class NetworkFixtureRunner:
    """Drive one deterministic fixture through real Simulator Flow objects."""

    def __init__(self, fixture: str, capacity: float, capacity_label: str) -> None:
        if fixture not in FIXTURE_NAMES:
            raise ValueError(f"unknown dynamic DAG fixture: {fixture}")
        self.fixture = fixture
        self.capacity = capacity
        self.capacity_label = capacity_label
        spec = experiment.WorkflowSpec(
            workflow_id=0,
            arrival_time=0,
            template="coding",
            deadline=1000.0,
            planner_size=1.0,
            branches=[],
            llm_size=1.0,
            judge_size=1.0,
            background_sizes=[],
        )
        self.simulator = experiment.Simulator(
            [spec],
            experiment.FIFOPolicy(seed=0),
            "light",
            0,
            1,
            2000,
            single_bottleneck_capacity=capacity,
            workflow_hints="record",
        )
        self.workflow = self.simulator.workflows[0]
        self.workflow.stage = "dynamic_fixture"
        self.engine = DynamicDAGEngine(collector=self.simulator.workflow_hint_collector)
        self.engine.register_workflow(
            0,
            deadline_hint=1000.0,
            timestamp=0.0,
            source="dynamic_fixture",
        )
        self.bridge = DynamicDAGFlowBridge(
            self.engine,
            create_flow=self._create_flow,
            cancel_flow=self._cancel_flow,
        )
        self.processed_flows: set[int] = set()
        self.cancelled_flows = 0
        self.logical_failures = 0
        self.retry_count = 0
        self.policy_complete = False

    @property
    def graph(self):
        return self.engine.workflows["0"]

    def _create_flow(self, _workflow_id: str, step: StepRuntime) -> int:
        required = step.spec.speculation_level == 0.0
        if required and step.spec.request_type in {"planner", "judge"}:
            role = "critical_control"
        elif required:
            role = "critical_bulk"
        else:
            role = "speculative"
        return self.simulator.new_flow(
            self.workflow,
            step.spec.request_type,
            step.spec.size_hint,
            role=role,
            stage="dynamic_dag",
            required=required,
            speculative=not required,
        )

    def _cancel_flow(self, flow_id: str, _reason: str) -> None:
        flow = self.simulator.flows[int(flow_id)]
        if not flow.cancelled and flow.completed_at is None:
            flow.cancelled = True
            self.cancelled_flows += 1

    def add(
        self,
        step_id: str,
        timestamp: float,
        *,
        parents: Tuple[str, ...] = (),
        dependency_kinds: Dict[str, str] | None = None,
        request_type: str = "tool",
        size_hint: float = 1.0,
        speculation_level: float = 0.0,
        retry_limit: int = 0,
    ) -> None:
        self.engine.add_step(
            0,
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
            timestamp=timestamp,
        )

    def has(self, step_id: str) -> bool:
        return step_id in self.graph.steps

    def completed(self, step_id: str) -> bool:
        return self.has(step_id) and self.graph.steps[step_id].state == "completed"

    def _bootstrap(self) -> None:
        self.add("planner", 0.0, request_type="planner", size_hint=2.0)
        self.bridge.dispatch_ready(0, timestamp=0.0)

    def observe_epoch(self, _timestamp: float) -> None:
        """Optional read-only hook used by shadow-mode diagnostics."""

    def _react(self, timestamp: float) -> None:
        """Apply deterministic Planner/Judge fixture rules until quiescent."""

        changed = True
        while changed:
            changed = False
            if self.fixture == "rag_supplemental":
                if self.completed("planner") and not self.has("retrieval:0"):
                    self.add(
                        "retrieval:0",
                        timestamp,
                        parents=("planner",),
                        dependency_kinds={"planner": "control_trigger"},
                        request_type="retrieval",
                        size_hint=28.0,
                        speculation_level=0.5,
                    )
                    changed = True
                elif self.completed("retrieval:0") and not self.has("evidence_check"):
                    self.add(
                        "evidence_check",
                        timestamp,
                        parents=("retrieval:0",),
                        request_type="judge",
                        size_hint=4.0,
                    )
                    changed = True
                elif self.completed("evidence_check") and not self.has("retrieval:1"):
                    self.add(
                        "retrieval:1",
                        timestamp,
                        parents=("evidence_check",),
                        dependency_kinds={"evidence_check": "control_trigger"},
                        request_type="retrieval",
                        size_hint=42.0,
                        speculation_level=0.8,
                    )
                    changed = True
                elif self.completed("retrieval:1") and not self.has("llm"):
                    self.add(
                        "llm",
                        timestamp,
                        parents=("retrieval:0", "retrieval:1"),
                        dependency_kinds={
                            "retrieval:0": "optional_evidence",
                            "retrieval:1": "optional_evidence",
                        },
                        request_type="llm",
                        size_hint=46.0,
                    )
                    changed = True
                elif self.completed("llm") and not self.has("judge"):
                    self.add(
                        "judge",
                        timestamp,
                        parents=("llm",),
                        request_type="judge",
                        size_hint=14.0,
                    )
                    changed = True
                elif self.completed("judge") and not self.policy_complete:
                    self.engine.select_step(0, "retrieval:0", timestamp=timestamp)
                    self.engine.select_step(0, "retrieval:1", timestamp=timestamp)
                    self.policy_complete = True
                    changed = True

            elif self.fixture == "coding_retry":
                if self.completed("planner") and not self.has("test"):
                    self.add("test", timestamp, parents=("planner",), size_hint=10.0)
                    changed = True
                elif self.completed("test") and not self.has("tool"):
                    self.add(
                        "tool",
                        timestamp,
                        parents=("test",),
                        request_type="tool",
                        size_hint=42.0,
                        retry_limit=1,
                    )
                    changed = True
                elif self.completed("tool") and not self.has("patch_llm"):
                    self.add(
                        "patch_llm",
                        timestamp,
                        parents=("tool",),
                        request_type="llm",
                        size_hint=46.0,
                    )
                    changed = True
                elif self.completed("patch_llm") and not self.has("judge"):
                    self.add(
                        "judge",
                        timestamp,
                        parents=("patch_llm",),
                        request_type="judge",
                        size_hint=14.0,
                    )
                    changed = True
                elif self.completed("judge") and not self.policy_complete:
                    self.policy_complete = True
                    changed = True

            elif self.fixture == "judge_pruning":
                if self.completed("planner") and not self.has("branch:a"):
                    for branch, size in (("branch:a", 2.0), ("branch:b", 200.0), ("branch:c", 200.0)):
                        self.add(
                            branch,
                            timestamp,
                            parents=("planner",),
                            dependency_kinds={"planner": "control_trigger"},
                            request_type="llm",
                            size_hint=size,
                            speculation_level=1.0,
                        )
                    changed = True
                elif self.completed("branch:a") and not self.has("judge"):
                    self.add(
                        "judge",
                        timestamp,
                        parents=("branch:a", "branch:b", "branch:c"),
                        dependency_kinds={
                            "branch:a": "hard_dependency",
                            "branch:b": "optional_evidence",
                            "branch:c": "optional_evidence",
                        },
                        request_type="judge",
                        size_hint=4.0,
                    )
                    changed = True
                elif self.completed("judge") and not self.policy_complete:
                    self.engine.select_step(0, "branch:a", timestamp=timestamp)
                    self.bridge.prune_subgraph(
                        0,
                        ("branch:b", "branch:c"),
                        timestamp=timestamp,
                    )
                    self.policy_complete = True
                    changed = True

            elif self.fixture == "parallel_join":
                if self.completed("planner") and not self.has("retrieval"):
                    parallel = (
                        ("retrieval", "retrieval", 28.0),
                        ("tool", "tool", 42.0),
                        ("llm_branch", "llm", 46.0),
                    )
                    for step_id, request_type, size in parallel:
                        self.add(
                            step_id,
                            timestamp,
                            parents=("planner",),
                            dependency_kinds={"planner": "control_trigger"},
                            request_type=request_type,
                            size_hint=size,
                        )
                    self.add(
                        "aggregator",
                        timestamp,
                        parents=("retrieval", "tool", "llm_branch"),
                        request_type="tool",
                        size_hint=8.0,
                    )
                    changed = True
                elif self.completed("aggregator") and not self.has("judge"):
                    self.add(
                        "judge",
                        timestamp,
                        parents=("aggregator",),
                        request_type="judge",
                        size_hint=14.0,
                    )
                    changed = True
                elif self.completed("judge") and not self.policy_complete:
                    self.policy_complete = True
                    changed = True

    def _process_flow_completion(self, flow_id: int, timestamp: float) -> None:
        binding = self.engine.flow_binding(flow_id)
        if (
            self.fixture == "coding_retry"
            and binding.step_id == "tool"
            and binding.attempt_id == 0
        ):
            self.bridge.on_flow_failed(flow_id, timestamp=timestamp)
            self.logical_failures += 1
            self.engine.retry_step(0, "tool", timestamp=timestamp)
            self.retry_count += 1
            return
        self.bridge.on_flow_completed(
            flow_id,
            timestamp=timestamp,
            dispatch_unlocked=False,
        )

    def run(self, *, max_time: int = 2000) -> NetworkPreflightResult:
        self._bootstrap()
        self.observe_epoch(0.0)
        completion_time = 0
        for current_time in range(max_time):
            self.simulator.time = current_time
            self.simulator.serve_active_flows()
            event_time = float(current_time + 1)
            newly_completed = sorted(
                flow.flow_id
                for flow in self.simulator.flows.values()
                if flow.completed_at is not None and flow.flow_id not in self.processed_flows
            )
            for flow_id in newly_completed:
                self._process_flow_completion(flow_id, event_time)
                self.processed_flows.add(flow_id)
            self._react(event_time)
            self.bridge.dispatch_ready(0, timestamp=event_time)
            self.observe_epoch(event_time)
            if self.policy_complete and not self.simulator.active_flows():
                completion_time = current_time + 1
                self.engine.finalize_workflow(0, timestamp=event_time)
                break
        else:
            raise RuntimeError(
                f"dynamic DAG fixture did not finish: {self.fixture}/{self.capacity_label}"
            )

        self.engine.validate_workflow(0, require_terminal=True)
        self.simulator.workflow_hint_collector.validate_all(require_terminal=True)
        return NetworkPreflightResult(
            fixture=self.fixture,
            capacity_label=self.capacity_label,
            capacity=self.capacity,
            completion_time=completion_time,
            flows_created=len(self.simulator.flows),
            cancelled_flows=self.cancelled_flows,
            logical_failures=self.logical_failures,
            retries=self.retry_count,
            snapshot=self.engine.snapshot(0),
            collector_summary=self.simulator.workflow_hint_collector.summary(),
            events=tuple(self.simulator.workflow_hint_collector.event_dicts()),
        )


def run_network_preflight(
    capacities: Dict[str, float] | None = None,
) -> Tuple[NetworkPreflightResult, ...]:
    selected = capacities or PREFLIGHT_CAPACITIES
    return tuple(
        NetworkFixtureRunner(fixture, capacity, label).run()
        for fixture in FIXTURE_NAMES
        for label, capacity in selected.items()
    )


def write_preflight_outputs(
    results: Tuple[NetworkPreflightResult, ...],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = [result.summary_dict() for result in results]
    (output_dir / "dynamic_dag_preflight_summary.json").write_text(
        json.dumps(summaries, indent=2) + "\n",
        encoding="utf-8",
    )
    snapshots = [
        {
            "fixture": result.fixture,
            "capacity_label": result.capacity_label,
            "capacity": result.capacity,
            "snapshot": result.snapshot,
        }
        for result in results
    ]
    (output_dir / "dynamic_dag_preflight_snapshots.json").write_text(
        json.dumps(snapshots, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "dynamic_dag_preflight_events.jsonl").open(
        "w",
        encoding="utf-8",
    ) as handle:
        for result in results:
            for event in result.events:
                row = {
                    "fixture": result.fixture,
                    "capacity_label": result.capacity_label,
                    "capacity": result.capacity,
                    **event,
                }
                handle.write(json.dumps(row, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional directory for summary, snapshots, and JSONL events.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = run_network_preflight()
    if args.output_dir:
        write_preflight_outputs(results, Path(args.output_dir))
    print(json.dumps([result.summary_dict() for result in results], indent=2))


if __name__ == "__main__":
    main()
