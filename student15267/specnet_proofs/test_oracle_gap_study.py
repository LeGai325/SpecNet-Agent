import unittest
from collections import Counter
from types import SimpleNamespace

from .oracle_gap_study import CounterfactualPolicy, overall_summary


class OracleGapStudyTests(unittest.TestCase):
    def test_target_action_only_overrides_target_workflow(self):
        base = SimpleNamespace(
            reset_for_run=lambda: None,
            flow_weight=lambda flow, sim: 2.0,
            decide_action=lambda sim, workflow: "moderate",
        )
        policy = CounterfactualPolicy(base, target_workflow_id=3, target_action="full")
        simulator = SimpleNamespace(observable_state=lambda workflow: ("low", "loose", "low_spec"))
        target = SimpleNamespace(spec=SimpleNamespace(workflow_id=3), decision_state=None)
        other = SimpleNamespace(spec=SimpleNamespace(workflow_id=4), decision_state=None)
        self.assertEqual(policy.decide_action(simulator, target), "full")
        self.assertEqual(policy.decide_action(simulator, other), "moderate")
        self.assertEqual(policy.flow_weight(None, None), 2.0)

    def test_overall_summary_reports_positive_gaps(self):
        rows = [
            {"oracle_gap": 0.0, "oracle_action": "full", "baseline_action": "full", "baseline_quality": 1.0, "oracle_quality": 1.0, "baseline_latency": 10, "oracle_latency": 10, "baseline_waste": 3, "oracle_waste": 3},
            {"oracle_gap": 0.5, "oracle_action": "moderate", "baseline_action": "full", "baseline_quality": 1.0, "oracle_quality": 0.94, "baseline_latency": 20, "oracle_latency": 15, "baseline_waste": 8, "oracle_waste": 4},
        ]
        summary = overall_summary(rows)[0]
        self.assertEqual(summary["workflows"], 2)
        self.assertAlmostEqual(summary["mean_oracle_gap"], 0.25)
        self.assertAlmostEqual(summary["positive_gap_fraction"], 0.5)
        self.assertEqual(summary["oracle_action_counts"], Counter({"full": 1, "moderate": 1}))


if __name__ == "__main__":
    unittest.main()
