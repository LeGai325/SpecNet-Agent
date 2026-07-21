
#!/usr/bin/env python3
"""Focused regression tests for the controller slack estimator."""

from __future__ import annotations

import unittest
from dataclasses import replace

import specnet_agent as experiment


class SlackEstimationTest(unittest.TestCase):
    def make_simulator(self):
        spec = experiment.generate_workload(123, "light", 300, 8)[0]
        simulator = experiment.Simulator(
            [spec],
            experiment.CriticalPathOnlyPolicy(),
            "light",
            123,
            300,
            1000,
        )
        workflow = simulator.workflows[spec.workflow_id]
        simulator.time = spec.arrival_time + 10
        return spec, simulator, workflow

    def test_slack_decreases_with_time_work_and_queue(self) -> None:
        spec, simulator, workflow = self.make_simulator()
        base_slack = simulator.workflow_slack_ratio(workflow)

        simulator.time += 40
        self.assertLess(simulator.workflow_slack_ratio(workflow), base_slack)

        larger_spec = replace(spec, llm_size=spec.llm_size + 160)
        larger_simulator = experiment.Simulator(
            [larger_spec],
            experiment.CriticalPathOnlyPolicy(),
            "light",
            123,
            300,
            1000,
        )
        larger_workflow = larger_simulator.workflows[larger_spec.workflow_id]
        larger_simulator.time = spec.arrival_time + 10
        self.assertLess(larger_simulator.workflow_slack_ratio(larger_workflow), base_slack)

        simulator.time = spec.arrival_time + 10
        simulator.new_flow(
            workflow,
            "background",
            160,
            role="background",
            stage="diagnostic",
            background=True,
        )
        self.assertLess(simulator.workflow_slack_ratio(workflow), base_slack)

    def test_semantic_buckets_and_action_independence(self) -> None:
        _, simulator, workflow = self.make_simulator()
        estimated = simulator.workflow_estimated_remaining_time(workflow)

        simulator.time = workflow.deadline_time - 3.0 * estimated
        self.assertEqual(simulator.workflow_slack_bucket(workflow), "loose")

        simulator.time = workflow.deadline_time - 1.5 * estimated
        self.assertEqual(simulator.workflow_slack_bucket(workflow), "normal")

        simulator.time = workflow.deadline_time
        self.assertEqual(simulator.workflow_slack_bucket(workflow), "tight")
        before_action = simulator.workflow_slack_ratio(workflow)
        workflow.action = "full"
        self.assertEqual(simulator.workflow_slack_ratio(workflow), before_action)

    def test_legacy_budget_ratio_is_preserved_for_rule_baselines(self) -> None:
        spec, simulator, workflow = self.make_simulator()
        expected = (workflow.deadline_time - simulator.time) / spec.deadline
        self.assertAlmostEqual(simulator.workflow_budget_ratio(workflow), expected)

    def test_queue_diagnostics_partition_active_work_without_changing_slack(self) -> None:
        _, simulator, workflow = self.make_simulator()
        simulator.new_flow(
            workflow,
            "llm",
            20,
            role="critical_control",
            stage="diagnostic",
        )
        simulator.new_flow(
            workflow,
            "tool",
            30,
            role="normal",
            stage="diagnostic",
        )
        simulator.new_flow(
            workflow,
            "retrieval",
            40,
            role="speculative",
            stage="diagnostic",
            speculative=True,
        )
        simulator.new_flow(
            workflow,
            "background",
            50,
            role="background",
            stage="diagnostic",
            background=True,
        )

        slack_before = simulator.workflow_slack_ratio(workflow)
        simulator.record_slack_decision(workflow)

        self.assertAlmostEqual(simulator.workflow_slack_ratio(workflow), slack_before)
        self.assertEqual(workflow.decision_active_flow_count, 4)
        self.assertAlmostEqual(workflow.decision_active_critical_work, 20.0)
        self.assertAlmostEqual(workflow.decision_active_normal_work, 30.0)
        self.assertAlmostEqual(workflow.decision_active_speculative_work, 40.0)
        self.assertAlmostEqual(workflow.decision_active_background_work, 50.0)
        self.assertAlmostEqual(workflow.decision_active_other_work, 0.0)
        partition_total = sum(
            (
                workflow.decision_active_critical_work,
                workflow.decision_active_normal_work,
                workflow.decision_active_speculative_work,
                workflow.decision_active_background_work,
                workflow.decision_active_other_work,
            )
        )
        self.assertAlmostEqual(partition_total, workflow.decision_active_work)
        self.assertAlmostEqual(workflow.decision_active_weighted_work, 391.0)
        self.assertAlmostEqual(workflow.decision_active_weight_sum, 16.4)
        self.assertAlmostEqual(workflow.decision_queue_time, 140.0 / 16.0)
        self.assertAlmostEqual(workflow.decision_link_capacity, simulator.capacity)
        self.assertAlmostEqual(workflow.decision_congestion_ratio, 140.0 / (16.0 * 12.0))
        self.assertEqual(workflow.decision_congestion_bucket, "low")
        self.assertAlmostEqual(workflow.decision_spec_pressure_ratio, 40.0 / 140.0)
        self.assertEqual(workflow.decision_spec_pressure_bucket, "mid_spec")

    def test_policy_weighted_queue_basis_uses_scheduler_weights(self) -> None:
        spec, _, _ = self.make_simulator()
        simulator = experiment.Simulator(
            [spec],
            experiment.CriticalPathOnlyPolicy(),
            "light",
            123,
            300,
            1000,
            slack_queue_basis="policy_weighted",
            slack_queue_weight=0.5,
        )
        workflow = simulator.workflows[spec.workflow_id]
        simulator.time = spec.arrival_time + 10
        simulator.new_flow(
            workflow,
            "llm",
            20,
            role="critical_control",
            stage="diagnostic",
        )
        simulator.new_flow(
            workflow,
            "background",
            50,
            role="background",
            stage="diagnostic",
            background=True,
        )

        required_time = simulator.workflow_required_work(workflow) / simulator.capacity
        expected_weighted_work = 20.0 * 12.0 + 50.0 * 0.5
        expected = required_time + 0.5 * expected_weighted_work / simulator.capacity

        self.assertAlmostEqual(simulator.slack_queue_work(), expected_weighted_work)
        self.assertAlmostEqual(simulator.workflow_estimated_remaining_time(workflow), expected)
        simulator.record_slack_decision(workflow)
        self.assertAlmostEqual(
            workflow.decision_queue_time,
            expected_weighted_work / simulator.capacity,
        )

    def test_invalid_slack_queue_configuration_is_rejected(self) -> None:
        spec = experiment.generate_workload(123, "light", 300, 8)[0]
        with self.assertRaises(ValueError):
            experiment.Simulator(
                [spec],
                experiment.CriticalPathOnlyPolicy(),
                "light",
                123,
                300,
                1000,
                slack_queue_basis="unknown",
            )
        with self.assertRaises(ValueError):
            experiment.SpecNetAgentBanditPolicy(slack_queue_weight=-0.1)


if __name__ == "__main__":
    unittest.main()

