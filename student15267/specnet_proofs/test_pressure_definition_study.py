import unittest
from types import SimpleNamespace

from .pressure_definition_study import (
    PRESSURE_DEFINITIONS,
    PressureSimulator,
    make_bandit_class,
    numeric_bucket,
)


class PressureDefinitionStudyTests(unittest.TestCase):
    def test_all_candidate_definitions_have_frozen_buckets(self):
        self.assertEqual(len(PRESSURE_DEFINITIONS), 6)
        for definition in PRESSURE_DEFINITIONS:
            self.assertEqual(numeric_bucket(definition, 0.0), "low_spec")
        for definition in PRESSURE_DEFINITIONS[:3]:
            self.assertEqual(numeric_bucket(definition, 1.0), "high_spec")

    def test_count_and_age_thresholds_are_not_ratio_thresholds(self):
        self.assertEqual(numeric_bucket("cancelable_queue_length", 0.0), "low_spec")
        self.assertEqual(numeric_bucket("cancelable_queue_length", 3.0), "mid_spec")
        self.assertEqual(numeric_bucket("cancelable_queue_length", 4.0), "high_spec")
        self.assertEqual(numeric_bucket("speculative_age", 2.0), "low_spec")
        self.assertEqual(numeric_bucket("speculative_age", 8.0), "mid_spec")
        self.assertEqual(numeric_bucket("speculative_age", 9.0), "high_spec")

    def test_workflow_optional_ratio_uses_only_optional_branches(self):
        simulator = object.__new__(PressureSimulator)
        simulator.pressure_definition = "workflow_optional_ratio"
        simulator.active_flows = lambda: []
        simulator.active_speculative_flows = lambda: []
        workflow = SimpleNamespace(
            spec=SimpleNamespace(
                branches=[
                    SimpleNamespace(size=30.0, required=True),
                    SimpleNamespace(size=10.0, required=False),
                ]
            )
        )
        self.assertAlmostEqual(simulator.pressure_value(workflow), 0.25)

    def test_pressure_policy_can_remove_only_pressure_dimension(self):
        full_class = make_bandit_class("original_ratio", False)
        no_class = make_bandit_class("original_ratio", True)
        fake_sim = SimpleNamespace(
            congestion_level=lambda: "high",
            workflow_slack_bucket=lambda workflow: "tight",
            pressure_bucket=lambda workflow: "high_spec",
        )
        workflow = SimpleNamespace()
        self.assertEqual(full_class().state_key(fake_sim, workflow), ("high", "tight", "high_spec"))
        self.assertEqual(no_class().state_key(fake_sim, workflow), ("high", "tight", "all_spec"))


if __name__ == "__main__":
    unittest.main()
