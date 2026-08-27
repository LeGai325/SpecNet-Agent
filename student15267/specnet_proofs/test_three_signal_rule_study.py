import unittest
from collections import Counter
from types import SimpleNamespace

from . import proof_harness as h
from .three_signal_rule_study import (
    MonotoneRiskRule,
    candidate_rules,
    disjoint_balanced_matrices,
    pivotal_state_audit,
    select_candidate,
)


class ThreeSignalRuleStudyTests(unittest.TestCase):
    def test_rule_is_monotone_and_ablation_uses_neutral_level(self):
        params = {"wc": 1.0, "ws": 1.0, "wp": 1.0, "threshold": 2.0}
        simulator = SimpleNamespace(
            congestion_level=lambda: "high",
            workflow_slack_bucket=lambda workflow: "tight",
            pressure_bucket=lambda workflow: "high_spec",
        )
        workflow = SimpleNamespace()
        self.assertEqual(MonotoneRiskRule(params).risk_components(simulator, workflow), (1.0, 1.0, 1.0))
        self.assertEqual(MonotoneRiskRule(params, "no_pressure").risk_components(simulator, workflow), (1.0, 1.0, 0.5))

    def test_candidate_grid_is_frozen_and_positive(self):
        candidates = candidate_rules()
        self.assertEqual(len(candidates), 35)
        self.assertTrue(all(row["wc"] > 0 and row["ws"] > 0 and row["wp"] > 0 for row in candidates))

    def test_smoke_splits_are_disjoint_and_balanced(self):
        selection, evaluation = disjoint_balanced_matrices(h.scenarios("smoke"), 12)
        self.assertFalse(set(selection) & set(evaluation))
        for split in (selection, evaluation):
            self.assertEqual(Counter(row[0] for row in split), Counter({"light": 4, "medium": 4, "heavy": 4}))

    def test_selection_prefers_all_positive_candidate(self):
        rows = [
            {"candidate_id": 0, "adaptive": 1, "quality_feasible_fraction": 1.0, "positive_primary_count": 2, "positive_nonjoint_primary_count": 3, "minimum_nonjoint_normalized_effect": 0.01, "minimum_normalized_effect": -0.1, "mean_normalized_effect": 0.2, "congestion_nonjoint_pivotal_states": 1, "slack_nonjoint_pivotal_states": 1, "pressure_nonjoint_pivotal_states": 1},
            {"candidate_id": 1, "adaptive": 1, "quality_feasible_fraction": 1.0, "positive_primary_count": 3, "positive_nonjoint_primary_count": 3, "minimum_nonjoint_normalized_effect": 0.01, "minimum_normalized_effect": 0.01, "mean_normalized_effect": 0.02, "congestion_nonjoint_pivotal_states": 1, "slack_nonjoint_pivotal_states": 1, "pressure_nonjoint_pivotal_states": 1},
        ]
        self.assertEqual(select_candidate(rows)["candidate_id"], 1)

    def test_high_joint_only_rule_fails_nonjoint_pivotal_audit(self):
        audit = pivotal_state_audit(
            {"wc": 1.0, "ws": 1.0, "wp": 1.0, "threshold": 2.7}
        )
        self.assertEqual(audit["congestion_nonjoint_pivotal_states"], 0)
        self.assertEqual(audit["slack_nonjoint_pivotal_states"], 0)
        self.assertEqual(audit["pressure_nonjoint_pivotal_states"], 0)


if __name__ == "__main__":
    unittest.main()
