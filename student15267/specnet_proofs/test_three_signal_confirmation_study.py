import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from . import proof_harness as h
from .three_signal_confirmation_study import (
    ACTION_PROFILES,
    POLICY_CLASSES,
    QUALITY_FLOOR,
    SAFE_ACTIONS,
    balanced_evaluation_matrix,
    cluster_bootstrap_ci,
    load_policy_checkpoint,
    save_policy_checkpoint,
)


class ThreeSignalConfirmationTests(unittest.TestCase):
    def test_safe_actions_obey_static_quality_floor(self):
        self.assertEqual(SAFE_ACTIONS, ("full", "recovery"))
        self.assertTrue(
            all(h.up.ACTION_CONFIG[action]["quality_floor"] >= QUALITY_FLOOR for action in SAFE_ACTIONS)
        )
        self.assertEqual(
            ACTION_PROFILES["bounded_quality"],
            ("full", "moderate", "recovery"),
        )

    def test_each_ablation_removes_exactly_one_state_dimension(self):
        simulator = SimpleNamespace(
            congestion_level=lambda: "high",
            workflow_slack_bucket=lambda workflow: "tight",
            pressure_bucket=lambda workflow: "high_spec",
        )
        workflow = SimpleNamespace()
        self.assertEqual(
            POLICY_CLASSES["full"]().state_key(simulator, workflow),
            ("high", "tight", "high_spec"),
        )
        self.assertEqual(
            POLICY_CLASSES["no_congestion"]().state_key(simulator, workflow),
            ("all_congestion", "tight", "high_spec"),
        )
        self.assertEqual(
            POLICY_CLASSES["no_slack"]().state_key(simulator, workflow),
            ("high", "all_slack", "high_spec"),
        )
        self.assertEqual(
            POLICY_CLASSES["no_pressure"]().state_key(simulator, workflow),
            ("high", "tight", "all_spec"),
        )

    def test_orthogonal_holdout_covers_every_factor_level(self):
        matrix = h.scenarios("full")
        selected = balanced_evaluation_matrix(matrix, 27)
        self.assertEqual(len(selected), 27)
        for factor in range(4):
            self.assertEqual(Counter(row[factor] for row in selected), Counter({value: 9 for value in set(row[factor] for row in matrix)}))

    def test_checkpoint_round_trip_preserves_policy_class(self):
        policy = POLICY_CLASSES["no_pressure"](seed=4101, train=False)
        state = ("high", "tight", "all_spec")
        policy.q_values[state]["recovery"] = 2.5
        policy.counts[state]["recovery"] = 7
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            save_policy_checkpoint(
                path,
                policy,
                {
                    "policy": "no_pressure",
                    "train_seed": 4101,
                    "protocol_fingerprint": "frozen",
                },
            )
            loaded = load_policy_checkpoint(path, "no_pressure", 4101, "frozen")
        self.assertEqual(loaded.state_key(SimpleNamespace(congestion_level=lambda: "high", workflow_slack_bucket=lambda workflow: "tight", pressure_bucket=lambda workflow: "low_spec"), SimpleNamespace()), state)
        self.assertEqual(loaded.q_values[state]["recovery"], 2.5)
        self.assertEqual(loaded.counts[state]["recovery"], 7)

    def test_unknown_training_schedule_is_rejected(self):
        from .three_signal_confirmation_study import train_policy

        with self.assertRaisesRegex(ValueError, "unknown training schedule"):
            train_policy(
                "full",
                1,
                1,
                10,
                1,
                20,
                [("light", 1.0, 1.0, 1.0)],
                training_schedule="invalid",
            )

    def test_cluster_bootstrap_resamples_replicates_not_cells(self):
        values = [
            (0, 0, 1.0),
            (0, 1, 1.0),
            (1, 0, 3.0),
            (1, 1, 3.0),
        ]
        low, high = cluster_bootstrap_ci(values, seed=1, draws=1000)
        self.assertAlmostEqual(low, 1.0)
        self.assertAlmostEqual(high, 3.0)


if __name__ == "__main__":
    unittest.main()
