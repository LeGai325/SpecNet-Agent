#!/usr/bin/env python3
"""Tests for compact path-aware controller state features."""

from __future__ import annotations

import unittest

import specnet_agent_experiment as experiment


def make_spec(workflow_id: int, required_service: str) -> experiment.WorkflowSpec:
    return experiment.WorkflowSpec(
        workflow_id=workflow_id,
        arrival_time=0,
        template="rag_qa",
        deadline=300.0,
        planner_size=1.0,
        branches=[
            experiment.BranchSpec(
                required_service,
                100.0,
                True,
                branch_index=0,
            )
        ],
        llm_size=1.0,
        judge_size=1.0,
        background_sizes=[],
    )


class PathAwareStateTest(unittest.TestCase):
    def make_simulator(self, network_model: str) -> experiment.Simulator:
        specs = [make_spec(0, "retrieval"), make_spec(1, "llm")]
        policy = experiment.SpecNetAgentBanditPolicy(
            seed=3,
            train=False,
            controller_variant="path_aware_quality",
        )
        simulator = experiment.Simulator(
            specs,
            policy,
            "heavy",
            3,
            100,
            1000,
            network_model=network_model,
        )
        backlog_id = simulator.new_flow(
            simulator.workflows[0],
            "retrieval",
            180.0,
            role="critical_bulk",
            stage="branch",
            required=True,
        )
        simulator.flows[backlog_id].remaining = 180.0
        return simulator

    def test_path_aware_variant_has_compact_four_feature_state(self) -> None:
        simulator = self.make_simulator("service_paths_borrowing")
        policy = simulator.policy
        workflow = simulator.workflows[0]

        state = policy.state_key(simulator, workflow)

        self.assertEqual(
            policy.state_features,
            (
                "slack",
                "required_path_pressure",
                "optional_headroom",
                "spec_pressure",
            ),
        )
        self.assertEqual(len(state), 4)
        self.assertEqual(state[1], simulator.required_path_pressure_bucket(workflow))
        self.assertEqual(state[2], simulator.optional_headroom_bucket(workflow))

    def test_path_pressure_distinguishes_collocated_required_work(self) -> None:
        simulator = self.make_simulator("service_paths")
        data_workflow = simulator.workflows[0]
        model_workflow = simulator.workflows[1]

        data_pressure = simulator.required_path_pressure_ratio(data_workflow)
        model_pressure = simulator.required_path_pressure_ratio(model_workflow)

        self.assertGreater(data_pressure, model_pressure)
        # Spreading required work reduces the hottest-path pressure, but reserves
        # capacity on an additional path and therefore reduces aggregate headroom.
        self.assertLess(
            simulator.optional_headroom_ratio(model_workflow),
            simulator.optional_headroom_ratio(data_workflow),
        )

    def test_single_bottleneck_degenerates_to_one_shared_pressure(self) -> None:
        simulator = self.make_simulator("single_bottleneck")
        data_workflow = simulator.workflows[0]
        model_workflow = simulator.workflows[1]

        self.assertAlmostEqual(
            simulator.required_path_pressure_ratio(data_workflow),
            simulator.required_path_pressure_ratio(model_workflow),
        )
        self.assertAlmostEqual(
            simulator.optional_headroom_ratio(data_workflow),
            simulator.optional_headroom_ratio(model_workflow),
        )

    def test_decision_record_exports_path_diagnostics(self) -> None:
        simulator = self.make_simulator("service_paths_borrowing")
        workflow = simulator.workflows[0]

        simulator.record_slack_decision(workflow)

        self.assertIsNotNone(workflow.decision_required_path_pressure_ratio)
        self.assertIsNotNone(workflow.decision_required_path_pressure_bucket)
        self.assertIsNotNone(workflow.decision_optional_headroom_ratio)
        self.assertIsNotNone(workflow.decision_optional_headroom_bucket)


if __name__ == "__main__":
    unittest.main()
