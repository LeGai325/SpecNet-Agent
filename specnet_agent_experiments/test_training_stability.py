#!/usr/bin/env python3
"""Focused tests for stable controller training schedules and checkpoints."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import specnet_agent_experiment as experiment


class TrainingScheduleTest(unittest.TestCase):
    def test_linear_epsilon_reaches_floor_and_stays_there(self) -> None:
        policy = experiment.SpecNetAgentBanditPolicy(
            epsilon=0.20,
            epsilon_end=0.03,
            epsilon_decay_fraction=0.80,
            epsilon_schedule="linear",
        )

        policy.set_training_progress(0, 90)
        self.assertAlmostEqual(policy.epsilon, 0.20)

        policy.set_training_progress(44, 90)
        self.assertGreater(policy.epsilon, 0.03)
        self.assertLess(policy.epsilon, 0.20)

        policy.set_training_progress(72, 90)
        self.assertAlmostEqual(policy.epsilon, 0.03)
        policy.set_training_progress(89, 90)
        self.assertAlmostEqual(policy.epsilon, 0.03)

    def test_visit_decay_preserves_learning_for_rare_state_actions(self) -> None:
        policy = experiment.SpecNetAgentBanditPolicy(
            learning_rate=0.25,
            learning_rate_min=0.03,
            learning_rate_schedule="visit_decay",
        )
        state = ("high", "tight", "high_spec")

        self.assertAlmostEqual(policy.effective_learning_rate(state, "critical_only"), 0.25)
        policy.counts[state]["critical_only"] = 3
        self.assertAlmostEqual(policy.effective_learning_rate(state, "critical_only"), 0.125)
        policy.counts[state]["critical_only"] = 99
        self.assertAlmostEqual(policy.effective_learning_rate(state, "critical_only"), 0.03)

    def test_fixed_schedule_reproduces_legacy_q_update(self) -> None:
        policy = experiment.SpecNetAgentBanditPolicy(
            epsilon=0.18,
            epsilon_schedule="fixed",
            learning_rate=0.25,
            learning_rate_schedule="fixed",
        )
        state = ("low", "loose", "low_spec")
        workflow = SimpleNamespace(decision_state=state, action="full")
        simulator = SimpleNamespace(workflow_reward=lambda _: -2.0)

        policy.on_workflow_complete(workflow, simulator)
        self.assertAlmostEqual(policy.q_values[state]["full"], -0.5)
        policy.on_workflow_complete(workflow, simulator)
        self.assertAlmostEqual(policy.q_values[state]["full"], -0.875)
        self.assertEqual(policy.counts[state]["full"], 2)

    def test_training_records_requested_and_final_checkpoints(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=3,
            loads=["light"],
            duration=100,
            max_workflows=3,
            max_time=500,
            seed=19,
            quality_weight=1.6,
            checkpoint_episodes=[1, 2],
        )

        self.assertEqual([item["episode"] for item in policy.training_checkpoints], [1, 2, 3])
        self.assertEqual(policy.selected_checkpoint_episode, 3)
        self.assertFalse(policy.train)
        self.assertEqual(policy.epsilon, 0.0)

    def test_best_checkpoint_uses_separate_validation_workloads(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=2,
            loads=["light"],
            duration=80,
            max_workflows=2,
            max_time=400,
            seed=23,
            quality_weight=1.6,
            checkpoint_episodes=[1],
            checkpoint_selection="best_validation",
            validation_seed=9001,
            checkpoint_eval_runs=1,
        )

        self.assertIn(policy.selected_checkpoint_episode, {1, 2})
        self.assertTrue(all("validation" in item for item in policy.training_checkpoints))
        self.assertEqual(policy.training_info["validation_seed"], 9001)

    def test_role_weighted_slack_configuration_reaches_checkpoint_validation(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=2,
            loads=["light"],
            duration=80,
            max_workflows=2,
            max_time=400,
            seed=29,
            quality_weight=1.6,
            controller_variant="full",
            checkpoint_episodes=[1],
            checkpoint_selection="best_validation",
            validation_seed=9011,
            checkpoint_eval_runs=1,
            slack_queue_basis="policy_weighted",
            slack_queue_weight=0.5,
        )

        metadata = policy.metadata()
        self.assertEqual(metadata["slack_estimator"], "role_weighted_queue_v2_1")
        self.assertEqual(metadata["slack_queue_basis"], "policy_weighted")
        self.assertEqual(metadata["slack_queue_weight"], 0.5)
        self.assertTrue(all("validation" in item for item in policy.training_checkpoints))


if __name__ == "__main__":
    unittest.main()
