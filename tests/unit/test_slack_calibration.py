
#!/usr/bin/env python3
"""Focused tests for offline Slack calibration analysis."""

from __future__ import annotations

import unittest

from specnet_agent.analysis import slack_calibration as analysis


class SlackCalibrationAnalysisTest(unittest.TestCase):
    def make_decision(self):
        return {
            "policy": "specnet_agent",
            "load": "heavy",
            "template": "research",
            "action": "moderate",
            "run": "0",
            "seed": "1",
            "train_seed": "2",
            "eval_seed": "3",
            "required_work": 160.0,
            "active_work": 160.0,
            "remaining_budget": 30.0,
            "actual_remaining": 25.0,
            "deadline_miss": 0,
            "capacity": 16.0,
            "congestion_ratio": 160.0 / (16.0 * 12.0),
            "congestion_bucket": "low",
            "active_flow_count": None,
            "weighted_work": None,
            "weight_sum": None,
            "critical_work": None,
            "normal_work": None,
            "speculative_work": None,
            "background_work": None,
            "other_work": None,
        }

    def test_queue_weight_recomputes_semantic_bucket(self) -> None:
        decision = self.make_decision()
        loose = analysis.evaluate_candidate(decision, 0.5)
        normal = analysis.evaluate_candidate(decision, 1.0)
        tight = analysis.evaluate_candidate(decision, 2.1)

        self.assertAlmostEqual(loose["estimated_remaining"], 15.0)
        self.assertEqual(loose["slack_bucket"], "loose")
        self.assertAlmostEqual(normal["estimated_remaining"], 20.0)
        self.assertEqual(normal["slack_bucket"], "normal")
        self.assertAlmostEqual(tight["estimated_remaining"], 31.0)
        self.assertEqual(tight["slack_bucket"], "tight")

    def test_policy_weighted_queue_basis_uses_logged_weighted_work(self) -> None:
        decision = dict(self.make_decision(), weighted_work=80.0)

        candidate = analysis.evaluate_candidate(decision, 0.5, "policy_weighted")

        self.assertEqual(candidate["queue_basis"], "policy_weighted")
        self.assertAlmostEqual(candidate["queue_work"], 80.0)
        self.assertAlmostEqual(candidate["queue_time"], 5.0)
        self.assertAlmostEqual(candidate["estimated_remaining"], 12.5)

    def test_metric_summary_reports_estimation_error(self) -> None:
        decision = self.make_decision()
        first = analysis.evaluate_candidate(decision, 1.0)
        second = dict(first, estimated_remaining=30.0, estimation_error=5.0, absolute_error=5.0)
        summary = analysis.metric_summary([first, second])

        self.assertEqual(summary["n"], 2)
        self.assertAlmostEqual(summary["bias"], 0.0)
        self.assertAlmostEqual(summary["mae"], 5.0)
        self.assertEqual(summary["normal_count"], 2)

    def test_seed_summary_keeps_calibration_units_separate(self) -> None:
        first = analysis.evaluate_candidate(self.make_decision(), 1.0)
        second_decision = dict(self.make_decision(), train_seed="7", eval_seed="8")
        second = analysis.evaluate_candidate(second_decision, 1.0)

        rows = analysis.make_seed_summary([first, second])

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["train_seed"] for row in rows}, {"2", "7"})


if __name__ == "__main__":
    unittest.main()

