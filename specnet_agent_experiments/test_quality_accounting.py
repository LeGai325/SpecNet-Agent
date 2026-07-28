#!/usr/bin/env python3
"""Tests for realized optional-branch quality and true speculative waste."""

from __future__ import annotations

import unittest

import specnet_agent_experiment as experiment


class FixedActionPolicy(experiment.FIFOPolicy):
    def __init__(self, action: str) -> None:
        super().__init__(seed=0)
        self.action = action

    def decide_action(
        self,
        simulator: experiment.Simulator,
        workflow: experiment.WorkflowRuntime,
    ) -> str:
        del simulator, workflow
        return self.action


class QualityAccountingTest(unittest.TestCase):
    def make_spec(self) -> experiment.WorkflowSpec:
        return experiment.WorkflowSpec(
            workflow_id=0,
            arrival_time=0,
            template="coding",
            deadline=100.0,
            planner_size=1.0,
            branches=[
                experiment.BranchSpec("tool", 10.0, True, branch_index=0),
                experiment.BranchSpec(
                    "retrieval",
                    12.0,
                    False,
                    branch_index=1,
                    selection_probability=0.9,
                    expected_utility=1.0,
                ),
                experiment.BranchSpec(
                    "llm",
                    14.0,
                    False,
                    branch_index=2,
                    selection_probability=0.8,
                    expected_utility=0.8,
                ),
                experiment.BranchSpec(
                    "storage",
                    16.0,
                    False,
                    branch_index=3,
                    selection_probability=0.4,
                    expected_utility=0.3,
                ),
            ],
            llm_size=1.0,
            judge_size=1.0,
            background_sizes=[],
        )

    def make_simulator(self):
        spec = self.make_spec()
        simulator = experiment.Simulator(
            [spec],
            experiment.FIFOPolicy(),
            "light",
            1,
            10,
            100,
        )
        return simulator, simulator.workflows[0]

    def add_optional_flow(
        self,
        simulator: experiment.Simulator,
        workflow: experiment.WorkflowRuntime,
        utility: float,
        served: float,
        completed: bool,
    ) -> experiment.Flow:
        flow_id = simulator.new_flow(
            workflow,
            "retrieval",
            served,
            role="speculative",
            stage="branch",
            speculative=True,
            selection_probability=0.8,
            expected_utility=utility,
        )
        workflow.speculative_branch_flows.append(flow_id)
        flow = simulator.flows[flow_id]
        flow.served = served
        flow.remaining = 0.0 if completed else max(1.0, served / 2.0)
        flow.completed_at = 1 if completed else None
        return flow

    def test_used_completed_optional_bytes_are_quality_not_waste(self) -> None:
        simulator, workflow = self.make_simulator()
        high = self.add_optional_flow(simulator, workflow, 1.0, 12.0, True)
        middle = self.add_optional_flow(simulator, workflow, 0.8, 14.0, True)
        low = self.add_optional_flow(simulator, workflow, 0.3, 16.0, True)

        simulator.finalize_quality_and_speculation(workflow)

        self.assertTrue(high.used_by_judge)
        self.assertTrue(middle.used_by_judge)
        self.assertFalse(low.used_by_judge)
        self.assertAlmostEqual(workflow.quality, 1.0)
        self.assertAlmostEqual(workflow.useful_speculative_bytes, 26.0)
        self.assertAlmostEqual(workflow.wasted_speculative_bytes, 16.0)
        self.assertEqual(workflow.retained_optional_count, 2)

    def test_partial_cancelled_optional_bytes_are_waste(self) -> None:
        simulator, workflow = self.make_simulator()
        flow = self.add_optional_flow(simulator, workflow, 1.0, 8.0, False)

        simulator.finalize_quality_and_speculation(workflow)

        self.assertTrue(flow.cancelled)
        self.assertAlmostEqual(workflow.quality, experiment.BASE_REQUIRED_QUALITY)
        self.assertAlmostEqual(workflow.useful_speculative_bytes, 0.0)
        self.assertAlmostEqual(workflow.wasted_speculative_bytes, 8.0)
        self.assertAlmostEqual(workflow.unused_speculative_bytes, 8.0)

    def test_required_flow_does_not_enter_speculative_accounting(self) -> None:
        simulator, workflow = self.make_simulator()
        flow_id = simulator.new_flow(
            workflow,
            "tool",
            20.0,
            role="critical_bulk",
            stage="branch",
            required=True,
        )
        workflow.required_branch_flows.append(flow_id)
        flow = simulator.flows[flow_id]
        flow.served = 20.0
        flow.remaining = 0.0
        flow.completed_at = 1

        simulator.finalize_quality_and_speculation(workflow)

        self.assertAlmostEqual(workflow.wasted_speculative_bytes, 0.0)
        self.assertAlmostEqual(workflow.useful_speculative_bytes, 0.0)

    def test_generated_utilities_are_deterministic_without_changing_sizes(self) -> None:
        first = experiment.generate_workload(77, "medium", 400, 8)
        second = experiment.generate_workload(77, "medium", 400, 8)

        self.assertEqual(first, second)
        optional = [branch for spec in first for branch in spec.branches if not branch.required]
        self.assertTrue(optional)
        self.assertTrue(all(branch.expected_utility > 0.0 for branch in optional))

    def spawned_workflow(
        self,
        action: str,
        coupling: str,
    ) -> tuple[experiment.Simulator, experiment.WorkflowRuntime]:
        spec = experiment.generate_workload(91, "medium", 400, 1)[0]
        simulator = experiment.Simulator(
            [spec],
            FixedActionPolicy(action),
            "medium",
            91,
            400,
            1000,
            action_coupling=coupling,
        )
        workflow = simulator.workflows[spec.workflow_id]
        simulator.spawn_branches(workflow)
        return simulator, workflow

    def test_decoupling_changes_background_without_changing_speculation(self) -> None:
        legacy_sim, legacy = self.spawned_workflow("moderate", "legacy")
        decoupled_sim, decoupled = self.spawned_workflow("moderate", "decoupled")

        self.assertEqual(len(legacy.branch_flows), len(decoupled.branch_flows))
        self.assertEqual(
            len(legacy.speculative_branch_flows),
            len(decoupled.speculative_branch_flows),
        )
        self.assertAlmostEqual(legacy.predicted_quality, decoupled.predicted_quality)
        legacy_background = sum(legacy_sim.flows[flow_id].size for flow_id in legacy.background_flows)
        decoupled_background = sum(
            decoupled_sim.flows[flow_id].size for flow_id in decoupled.background_flows
        )
        self.assertGreater(legacy_background, decoupled_background)

    def test_recovery_no_longer_implies_full_background_load(self) -> None:
        legacy_sim, legacy = self.spawned_workflow("recovery", "legacy")
        decoupled_sim, decoupled = self.spawned_workflow("recovery", "decoupled")

        legacy_background = sum(legacy_sim.flows[flow_id].size for flow_id in legacy.background_flows)
        decoupled_background = sum(
            decoupled_sim.flows[flow_id].size for flow_id in decoupled.background_flows
        )
        self.assertAlmostEqual(
            decoupled_background,
            legacy_background * experiment.DECOUPLED_BACKGROUND_SCALE["recovery"],
        )

    def test_conservative_action_can_suppress_background_only(self) -> None:
        simulator, workflow = self.spawned_workflow("conservative", "decoupled")

        self.assertTrue(workflow.speculative_branch_flows)
        self.assertFalse(workflow.background_flows)
        self.assertEqual(simulator.background_scale_for_action("conservative"), 0.0)

    def test_invalid_action_coupling_is_rejected(self) -> None:
        spec = self.make_spec()
        with self.assertRaisesRegex(ValueError, "unknown action coupling"):
            experiment.Simulator(
                [spec],
                experiment.FIFOPolicy(),
                "light",
                1,
                10,
                100,
                action_coupling="combined",
            )


if __name__ == "__main__":
    unittest.main()
