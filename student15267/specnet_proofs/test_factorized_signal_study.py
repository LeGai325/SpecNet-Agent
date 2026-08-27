import unittest
from types import SimpleNamespace

from .factorized_signal_study import (
    FactorizedSignalRule,
    candidate_rules,
    select_candidate,
)


class FactorizedSignalStudyTests(unittest.TestCase):
    def test_candidate_grid_is_frozen(self):
        self.assertEqual(len(candidate_rules()), 18)

    def test_pressure_controls_only_safe_admission_actions(self):
        params = {
            "congestion_critical_boost": 1.5,
            "congestion_optional_scale": 0.5,
            "slack_critical_boost": 1.5,
        }
        workflow = SimpleNamespace(decision_state=None)
        high = SimpleNamespace(
            pressure_bucket=lambda workflow: "high_spec",
            congestion_level=lambda: "high",
            workflow_slack_bucket=lambda workflow: "tight",
        )
        self.assertEqual(FactorizedSignalRule(params).decide_action(high, workflow), "recovery")
        self.assertEqual(FactorizedSignalRule(params, "no_pressure").decide_action(high, workflow), "full")

    def test_congestion_and_slack_have_distinct_weight_effects(self):
        params = {
            "congestion_critical_boost": 2.0,
            "congestion_optional_scale": 0.5,
            "slack_critical_boost": 1.5,
        }
        owner = SimpleNamespace(observable_state=("high", "tight", "high_spec"))
        simulator = SimpleNamespace(
            congestion_level=lambda: "high",
            workflows={1: owner},
        )
        critical = SimpleNamespace(
            role="critical_bulk",
            required=True,
            speculative=False,
            background=False,
            workflow_id=1,
        )
        full = FactorizedSignalRule(params)
        no_congestion = FactorizedSignalRule(params, "no_congestion")
        no_slack = FactorizedSignalRule(params, "no_slack")
        self.assertAlmostEqual(full.flow_weight(critical, simulator), 24.0)
        self.assertAlmostEqual(no_congestion.flow_weight(critical, simulator), 12.0)
        self.assertAlmostEqual(no_slack.flow_weight(critical, simulator), 16.0)

    def test_background_boost_is_optional_and_ablation_invariant(self):
        params = {
            "congestion_critical_boost": 2.0,
            "congestion_optional_scale": 0.5,
            "slack_critical_boost": 1.5,
            "background_weight_boost": 4.0,
        }
        simulator = SimpleNamespace(
            congestion_level=lambda: "low",
            workflows={},
        )
        background = SimpleNamespace(
            role="background",
            required=False,
            speculative=False,
            background=True,
            workflow_id=1,
        )
        self.assertAlmostEqual(
            FactorizedSignalRule(params).flow_weight(background, simulator), 2.0
        )
        self.assertAlmostEqual(
            FactorizedSignalRule(params, "no_pressure").flow_weight(
                background, simulator
            ),
            2.0,
        )
        without_boost = dict(params)
        without_boost.pop("background_weight_boost")
        self.assertAlmostEqual(
            FactorizedSignalRule(without_boost).flow_weight(background, simulator),
            0.5,
        )

    def test_background_deficit_boost_stops_at_original_size_target(self):
        params = {
            "congestion_critical_boost": 2.0,
            "congestion_optional_scale": 0.5,
            "slack_critical_boost": 1.5,
            "background_weight_boost": 4.0,
            "background_target_ratio": 0.25,
        }
        owner = SimpleNamespace(action="recovery")
        simulator = SimpleNamespace(
            congestion_level=lambda: "low",
            workflows={1: owner},
        )
        background = SimpleNamespace(
            role="background",
            required=False,
            speculative=False,
            background=True,
            workflow_id=1,
            size=100.0,
            served=24.0,
        )
        rule = FactorizedSignalRule(params)
        self.assertAlmostEqual(rule.flow_weight(background, simulator), 2.0)
        background.served = 25.0
        self.assertAlmostEqual(rule.flow_weight(background, simulator), 0.5)

    def test_selection_requires_three_broad_and_nonjoint_directions(self):
        valid = {
            "candidate_id": 0,
            "adaptive": 1,
            "quality_feasible_fraction": 1.0,
            "positive_primary_count": 3,
            "positive_nonjoint_primary_count": 3,
            "minimum_normalized_effect": 0.01,
            "minimum_nonjoint_normalized_effect": 0.01,
            "mean_normalized_effect": 0.02,
        }
        invalid = dict(valid, candidate_id=1, positive_primary_count=2)
        self.assertEqual(select_candidate([invalid, valid])["candidate_id"], 0)


if __name__ == "__main__":
    unittest.main()
