#!/usr/bin/env python3
"""Tests for fixed quality targets, Safety Guard, and window-level lambda."""

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


class InfeasibleSimulator(experiment.Simulator):
    def quality_for_action(
        self,
        workflow: experiment.WorkflowRuntime,
        action: str,
        branch_count: int,
    ) -> float:
        del workflow, action, branch_count
        return 0.80


class QualityConstraintTest(unittest.TestCase):
    def make_simulator(
        self,
        guard: bool,
        simulator_class=experiment.Simulator,
    ) -> tuple[experiment.Simulator, experiment.WorkflowRuntime]:
        spec = experiment.generate_workload(141, "medium", 500, 1)[0]
        simulator = simulator_class(
            [spec],
            FixedActionPolicy("critical_only"),
            "medium",
            141,
            500,
            1200,
            quality_target=0.95,
            quality_hard_floor=0.90,
            safety_guard=guard,
        )
        return simulator, simulator.workflows[spec.workflow_id]

    def test_guard_overrides_action_below_hard_floor(self) -> None:
        simulator, workflow = self.make_simulator(True)

        simulator.spawn_branches(workflow)

        self.assertEqual(workflow.raw_action, "critical_only")
        self.assertNotEqual(workflow.safe_action, workflow.raw_action)
        self.assertGreaterEqual(workflow.predicted_quality, 0.90)
        self.assertTrue(workflow.guard_overridden)
        self.assertEqual(
            workflow.override_reason,
            "predicted_quality_below_hard_floor",
        )
        self.assertEqual(simulator.policy.raw_action_counter["critical_only"], 1)
        self.assertEqual(simulator.policy.action_counter[workflow.safe_action], 1)

    def test_guard_off_preserves_raw_action(self) -> None:
        simulator, workflow = self.make_simulator(False)

        simulator.spawn_branches(workflow)

        self.assertEqual(workflow.raw_action, "critical_only")
        self.assertEqual(workflow.safe_action, "critical_only")
        self.assertFalse(workflow.guard_overridden)
        self.assertAlmostEqual(workflow.predicted_quality, experiment.BASE_REQUIRED_QUALITY)

    def test_no_feasible_action_is_recorded_explicitly(self) -> None:
        simulator, workflow = self.make_simulator(True, InfeasibleSimulator)

        simulator.spawn_branches(workflow)

        self.assertTrue(workflow.quality_constraint_infeasible)
        self.assertEqual(workflow.override_reason, "quality_constraint_infeasible")
        self.assertEqual(workflow.safe_action, "full")

    def test_realized_hard_floor_failure_is_violation_not_debt(self) -> None:
        simulator, workflow = self.make_simulator(False)

        simulator.finalize_quality_and_speculation(workflow)

        self.assertTrue(workflow.quality_violation)
        self.assertLess(workflow.quality, simulator.quality_hard_floor)
        self.assertFalse(hasattr(workflow, "quality_debt"))

    def test_workflow_update_does_not_change_lambda(self) -> None:
        policy = experiment.SpecNetAgentBanditPolicy(
            seed=1,
            train=True,
            lambda_initial=1.0,
        )
        spec = experiment.generate_workload(9, "light", 300, 1)[0]
        simulator = experiment.Simulator([spec], policy, "light", 9, 300, 1000)
        workflow = simulator.workflows[spec.workflow_id]
        workflow.complete_time = 20
        workflow.decision_state = ("low", "loose", "low_spec")
        workflow.action = "full"
        workflow.quality = 0.80

        policy.on_workflow_complete(workflow, simulator)

        self.assertEqual(policy.quality_lagrange_multiplier, 1.0)
        self.assertEqual(policy.counts[workflow.decision_state]["full"], 1)

    def test_lambda_updates_once_per_complete_load_cycle(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=3,
            loads=["light", "medium", "heavy"],
            duration=100,
            max_workflows=4,
            max_time=400,
            seed=17,
            quality_weight=1.6,
            checkpoint_episodes=[3],
            quality_target=0.95,
            lambda_initial=1.0,
            lambda_learning_rate=2.0,
            lambda_max=4.0,
        )

        self.assertEqual(len(policy.lambda_updates), 1)
        self.assertEqual(policy.lambda_updates[0]["episode"], 3)
        self.assertGreaterEqual(policy.quality_lagrange_multiplier, 0.0)
        self.assertLessEqual(policy.quality_lagrange_multiplier, 4.0)

    def test_lambda_uses_worst_load_average_gap(self) -> None:
        policy = experiment.SpecNetAgentBanditPolicy(
            quality_target=0.95,
            lambda_initial=1.0,
            lambda_learning_rate=2.0,
            lambda_max=4.0,
        )

        policy.update_quality_multiplier(
            {
                "light": {"completed": 4, "avg_quality": 0.97},
                "medium": {"completed": 4, "avg_quality": 0.94},
                "heavy": {"completed": 4, "avg_quality": 0.96},
            },
            episode=3,
        )

        self.assertAlmostEqual(policy.lambda_updates[0]["quality_gap"], 0.01)
        self.assertAlmostEqual(policy.quality_lagrange_multiplier, 1.02)

    def test_empty_load_window_does_not_create_artificial_quality_gap(self) -> None:
        policy = experiment.SpecNetAgentBanditPolicy(lambda_initial=1.0)

        policy.update_quality_multiplier(
            {
                "light": {"completed": 0, "avg_quality": 0.0},
                "medium": {"completed": 4, "avg_quality": 0.94},
                "heavy": {"completed": 4, "avg_quality": 0.96},
            },
            episode=3,
        )

        self.assertEqual(policy.quality_lagrange_multiplier, 1.0)
        self.assertFalse(policy.lambda_updates[0]["updated"])
        self.assertEqual(policy.lambda_updates[0]["missing_loads"], ["light"])

    def test_validation_uses_fixed_target_without_selecting_it(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=2,
            loads=["light"],
            duration=100,
            max_workflows=4,
            max_time=400,
            seed=19,
            quality_weight=1.6,
            checkpoint_episodes=[1, 2],
            checkpoint_selection="best_validation",
            validation_seed=9003,
            checkpoint_eval_runs=1,
            quality_target=0.95,
        )

        self.assertEqual(policy.training_info["quality_target"], 0.95)
        for checkpoint in policy.training_checkpoints:
            self.assertEqual(checkpoint["validation"]["quality_target"], 0.95)


if __name__ == "__main__":
    unittest.main()
