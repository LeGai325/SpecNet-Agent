"""Tests for the simulator-neutral dynamic workflow DAG engine."""

import unittest

try:
    from dynamic_dag import (
        DynamicDAGEngine,
        DynamicDAGError,
        DynamicDAGFlowBridge,
        StepSpec,
    )
    from dynamic_dag_fixtures import run_all_fixtures
    from dynamic_dag_preflight import run_network_preflight
    import specnet_agent_experiment as experiment
    from workflow_hints import WorkflowHintCollector
except ImportError:  # pragma: no cover - package-style imports
    from .dynamic_dag import (
        DynamicDAGEngine,
        DynamicDAGError,
        DynamicDAGFlowBridge,
        StepSpec,
    )
    from .dynamic_dag_fixtures import run_all_fixtures
    from .dynamic_dag_preflight import run_network_preflight
    from . import specnet_agent_experiment as experiment
    from .workflow_hints import WorkflowHintCollector


class DynamicDAGEngineTest(unittest.TestCase):
    def make_engine(self):
        collector = WorkflowHintCollector()
        engine = DynamicDAGEngine(collector=collector)
        engine.register_workflow(7, deadline_hint=100.0, timestamp=0.0)
        return engine, collector

    def add(
        self,
        engine,
        step_id,
        *,
        parents=(),
        dependency_kinds=None,
        speculation_level=0.0,
        retry_limit=0,
        timestamp=0.0,
    ):
        return engine.add_step(
            7,
            StepSpec(
                step_id=step_id,
                parents=tuple(parents),
                dependency_kinds=dependency_kinds or {},
                request_type="tool",
                size_hint=4.0,
                speculation_level=speculation_level,
                retry_limit=retry_limit,
            ),
            timestamp=timestamp,
        )

    def run_step(self, engine, step_id, start, end):
        flow_id = f"flow:{step_id}:{start}"
        engine.start_step(7, step_id, flow_id=flow_id, timestamp=start)
        return engine.complete_flow(flow_id, timestamp=end)

    def test_hard_dependencies_unlock_and_flow_completion_is_bidirectional(self):
        engine, collector = self.make_engine()
        self.add(engine, "planner")
        child = self.add(engine, "tool", parents=("planner",))

        self.assertEqual([step.step_id for step in engine.ready_steps(7)], ["planner"])
        self.assertEqual(child.state, "created")
        unlocked = self.run_step(engine, "planner", 1.0, 2.0)

        self.assertEqual(unlocked, ("tool",))
        self.assertEqual(child.state, "ready")
        self.assertEqual(collector.step(7, "tool").state, "ready")
        self.run_step(engine, "tool", 3.0, 4.0)
        engine.finalize_workflow(7, timestamp=5.0)
        collector.validate_all(require_terminal=True)

    def test_optional_and_control_dependencies_do_not_block_ready(self):
        engine, _ = self.make_engine()
        self.add(engine, "planner")
        optional = self.add(
            engine,
            "optional_consumer",
            parents=("planner",),
            dependency_kinds={"planner": "optional_evidence"},
        )
        triggered = self.add(
            engine,
            "triggered",
            parents=("planner",),
            dependency_kinds={"planner": "control_trigger"},
        )

        self.assertEqual(optional.state, "ready")
        self.assertEqual(triggered.state, "ready")

    def test_multiple_hard_parents_form_join_barrier(self):
        engine, _ = self.make_engine()
        self.add(engine, "left")
        self.add(engine, "right")
        join = self.add(engine, "join", parents=("left", "right"))

        self.run_step(engine, "left", 1.0, 2.0)
        self.assertEqual(join.state, "created")
        self.run_step(engine, "right", 3.0, 4.0)
        self.assertEqual(join.state, "ready")

    def test_failure_retry_uses_new_attempt_and_new_flow(self):
        engine, collector = self.make_engine()
        step = self.add(engine, "tool", retry_limit=1)
        engine.start_step(7, "tool", flow_id="flow:tool:0", timestamp=1.0)
        engine.fail_flow("flow:tool:0", timestamp=2.0)

        self.assertEqual(step.state, "failed")
        self.assertEqual(step.failure_count, 1)
        engine.retry_step(7, "tool", timestamp=3.0)
        self.assertEqual(step.attempt_id, 1)
        self.assertEqual(step.state, "ready")
        engine.start_step(7, "tool", flow_id="flow:tool:1", timestamp=4.0)
        engine.fail_flow("flow:tool:1", timestamp=5.0)

        events = [(event.event, event.attempt_id) for event in collector.events]
        self.assertIn(("failed", 0), events)
        self.assertIn(("retried", 1), events)
        self.assertIn(("failed", 1), events)
        with self.assertRaisesRegex(DynamicDAGError, "retry limit"):
            engine.retry_step(7, "tool", timestamp=6.0)

    def test_pruning_running_optional_steps_returns_flow_cancellations(self):
        engine, collector = self.make_engine()
        self.add(engine, "planner")
        self.run_step(engine, "planner", 1.0, 2.0)
        for branch in ("branch:a", "branch:b"):
            self.add(
                engine,
                branch,
                parents=("planner",),
                dependency_kinds={"planner": "control_trigger"},
                speculation_level=1.0,
                timestamp=2.0,
            )
        for branch in ("branch:a", "branch:b"):
            engine.start_step(7, branch, flow_id=f"flow:{branch}", timestamp=3.0)

        cancellations = engine.prune_subgraph(
            7,
            ("branch:a", "branch:b"),
            timestamp=4.0,
        )

        self.assertEqual({item.flow_id for item in cancellations}, {"flow:branch:a", "flow:branch:b"})
        self.assertEqual(engine.workflows["7"].steps["branch:a"].state, "cancelled")
        self.assertEqual(collector.step(7, "branch:b").state, "cancelled")

    def test_pruning_required_step_is_rejected(self):
        engine, _ = self.make_engine()
        self.add(engine, "required")
        with self.assertRaisesRegex(DynamicDAGError, "required"):
            engine.prune_subgraph(7, "required", timestamp=1.0)

    def test_pruning_hard_parent_of_active_child_is_rejected(self):
        engine, _ = self.make_engine()
        self.add(engine, "optional", speculation_level=1.0)
        self.add(engine, "required_child", parents=("optional",), speculation_level=0.0)
        with self.assertRaisesRegex(DynamicDAGError, "required"):
            engine.prune_subgraph(7, "optional", timestamp=1.0)

    def test_selection_and_snapshot_expose_active_graph_state(self):
        engine, collector = self.make_engine()
        step = self.add(engine, "candidate", speculation_level=1.0)
        initial_version = engine.snapshot(7)["graph_version"]
        self.run_step(engine, "candidate", 1.0, 2.0)
        engine.select_step(7, "candidate", timestamp=3.0)
        snapshot = engine.snapshot(7)

        self.assertGreater(snapshot["graph_version"], initial_version)
        self.assertTrue(snapshot["steps"]["candidate"]["selected_by_judge"])
        self.assertTrue(step.selected_by_judge)
        self.assertTrue(collector.step(7, "candidate").selected)

    def test_invalid_steps_and_lifecycle_transitions_are_rejected(self):
        engine, _ = self.make_engine()
        with self.assertRaisesRegex(DynamicDAGError, "missing parent"):
            self.add(engine, "orphan", parents=("missing",))
        with self.assertRaisesRegex(DynamicDAGError, "itself"):
            self.add(engine, "self", parents=("self",))
        self.add(engine, "planner")
        with self.assertRaisesRegex(DynamicDAGError, "already registered"):
            self.add(engine, "planner")
        with self.assertRaisesRegex(DynamicDAGError, "from state ready"):
            engine.complete_step(7, "planner", timestamp=1.0)

    def test_finalize_rejects_non_terminal_steps(self):
        engine, _ = self.make_engine()
        self.add(engine, "planner")
        with self.assertRaisesRegex(DynamicDAGError, "non-terminal"):
            engine.finalize_workflow(7, timestamp=1.0)


class DynamicDAGFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {result.name: result for result in run_all_fixtures()}

    def test_all_fixtures_finish_with_valid_collector_output(self):
        self.assertEqual(
            set(self.results),
            {"rag_supplemental", "coding_retry", "judge_pruning", "parallel_join"},
        )
        for name, result in self.results.items():
            with self.subTest(name=name):
                self.assertEqual(result.snapshot["status"], "completed")
                self.assertEqual(result.snapshot["ready_steps"], [])
                self.assertEqual(result.collector_summary["validation_errors"], 0)
                self.assertEqual(result.collector_summary["workflows_finalized"], 1)

    def test_rag_fixture_adds_second_retrieval_after_evidence_check(self):
        events = self.results["rag_supplemental"].events
        created = {
            event["step_id"]: event["sequence"]
            for event in events
            if event["event"] == "created"
        }
        completed = {
            event["step_id"]: event["sequence"]
            for event in events
            if event["event"] == "completed"
        }
        self.assertGreater(created["retrieval:1"], completed["evidence_check"])

    def test_coding_fixture_records_two_tool_attempts(self):
        tool_events = [
            (event["event"], event["attempt_id"])
            for event in self.results["coding_retry"].events
            if event["step_id"] == "tool"
        ]
        self.assertIn(("failed", 0), tool_events)
        self.assertIn(("retried", 1), tool_events)
        self.assertIn(("completed", 1), tool_events)
        reason_by_event = {
            event["event"]: event["reason"]
            for event in self.results["coding_retry"].events
            if event["step_id"] == "tool" and event["reason"]
        }
        self.assertEqual(reason_by_event["failed"], "execution_failed")
        self.assertEqual(reason_by_event["retried"], "retry_requested")

    def test_judge_fixture_selects_one_and_cancels_two(self):
        result = self.results["judge_pruning"]
        self.assertEqual(len(result.flow_cancellations), 2)
        self.assertTrue(result.snapshot["steps"]["branch:a"]["selected_by_judge"])
        self.assertEqual(result.snapshot["steps"]["branch:b"]["state"], "cancelled")
        self.assertEqual(result.snapshot["steps"]["branch:c"]["state"], "cancelled")
        cancelled_reasons = {
            event["reason"]
            for event in result.events
            if event["event"] == "cancelled"
        }
        self.assertEqual(cancelled_reasons, {"judge_pruned"})

    def test_parallel_join_becomes_ready_after_all_parents_complete(self):
        events = self.results["parallel_join"].events
        aggregator_ready = next(
            event["sequence"]
            for event in events
            if event["step_id"] == "aggregator" and event["event"] == "ready"
        )
        parent_completions = [
            event["sequence"]
            for event in events
            if event["step_id"] in {"retrieval", "tool", "llm_branch"}
            and event["event"] == "completed"
        ]
        self.assertGreater(aggregator_ready, max(parent_completions))


class DynamicDAGSimulatorFlowTest(unittest.TestCase):
    def test_bridge_binds_real_simulator_flows_and_unlocks_child(self):
        spec = experiment.WorkflowSpec(
            workflow_id=0,
            arrival_time=0,
            template="coding",
            deadline=100.0,
            planner_size=1.0,
            branches=[],
            llm_size=1.0,
            judge_size=1.0,
            background_sizes=[],
        )
        simulator = experiment.Simulator(
            [spec],
            experiment.FIFOPolicy(),
            "light",
            1,
            10,
            20,
            single_bottleneck_capacity=16.0,
            workflow_hints="record",
        )
        workflow = simulator.workflows[0]
        workflow.stage = "dynamic_fixture"
        engine = DynamicDAGEngine(collector=simulator.workflow_hint_collector)
        engine.register_workflow(
            0,
            deadline_hint=100.0,
            timestamp=0.0,
            source="dynamic_fixture",
        )

        def create_flow(_workflow_id, step):
            required = step.spec.speculation_level == 0.0
            return simulator.new_flow(
                workflow,
                step.spec.request_type,
                step.spec.size_hint,
                role="critical_control" if required else "speculative",
                stage="dynamic_dag",
                required=required,
                speculative=not required,
            )

        bridge = DynamicDAGFlowBridge(engine, create_flow=create_flow)
        engine.add_step(
            0,
            StepSpec(
                step_id="planner",
                request_type="planner",
                size_hint=1.0,
                source="dynamic_fixture",
            ),
            timestamp=0.0,
        )
        engine.add_step(
            0,
            StepSpec(
                step_id="llm",
                parents=("planner",),
                request_type="llm",
                size_hint=1.0,
                source="dynamic_fixture",
            ),
            timestamp=0.0,
        )

        planner_binding = bridge.dispatch_ready(0, timestamp=0.0)[0]
        self.assertIsInstance(simulator.flows[int(planner_binding.flow_id)], experiment.Flow)
        simulator.serve_active_flows()
        self.assertEqual(simulator.flows[int(planner_binding.flow_id)].completed_at, 1)

        simulator.time = 1
        llm_bindings = bridge.on_flow_completed(planner_binding.flow_id, timestamp=1.0)
        self.assertEqual(len(llm_bindings), 1)
        self.assertEqual(engine.workflows["0"].steps["llm"].state, "running")
        simulator.serve_active_flows()
        simulator.time = 2
        bridge.on_flow_completed(llm_bindings[0].flow_id, timestamp=2.0)
        engine.finalize_workflow(0, timestamp=2.0)

        self.assertEqual(engine.snapshot(0)["status"], "completed")
        simulator.workflow_hint_collector.validate_all(require_terminal=True)

    def test_network_preflight_is_stable_across_three_capacities(self):
        results = run_network_preflight()
        self.assertEqual(len(results), 12)
        by_fixture = {}
        for result in results:
            by_fixture.setdefault(result.fixture, []).append(result)
            self.assertEqual(result.snapshot["status"], "completed")
            self.assertEqual(result.collector_summary["validation_errors"], 0)
            self.assertGreater(result.completion_time, 0)

        for fixture_results in by_fixture.values():
            self.assertEqual({result.capacity for result in fixture_results}, {8.0, 16.0, 32.0})
            completion_by_capacity = {
                result.capacity: result.completion_time for result in fixture_results
            }
            self.assertLessEqual(completion_by_capacity[32.0], completion_by_capacity[16.0])
            self.assertLessEqual(completion_by_capacity[16.0], completion_by_capacity[8.0])

        for result in by_fixture["coding_retry"]:
            self.assertEqual(result.logical_failures, 1)
            self.assertEqual(result.retries, 1)
            self.assertEqual(
                {
                    event["reason"]
                    for event in result.events
                    if event["event"] in {"failed", "retried"}
                },
                {"execution_failed", "retry_requested"},
            )
        for result in by_fixture["judge_pruning"]:
            self.assertEqual(result.cancelled_flows, 2)
            self.assertEqual(
                {
                    event["reason"]
                    for event in result.events
                    if event["event"] == "cancelled"
                },
                {"judge_pruned"},
            )


if __name__ == "__main__":
    unittest.main()
