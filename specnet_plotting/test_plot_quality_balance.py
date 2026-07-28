#!/usr/bin/env python3
"""Tests for quality-balance plotting data aggregation."""

from __future__ import annotations

import csv
import shutil
import unittest
from pathlib import Path

from plotting_multi import plot_quality_balance as plotting


class QualityBalancePlottingTest(unittest.TestCase):
    artifacts = Path(__file__).resolve().parent / "test_artifacts" / "quality_balance"

    def setUp(self) -> None:
        shutil.rmtree(self.artifacts, ignore_errors=True)
        self.artifacts.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.artifacts, ignore_errors=True)

    def write_csv(self, path: Path, rows) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def make_input(self) -> None:
        directories = {
            "borrowing_guard_off",
            "borrowing_guard_on",
            *plotting.NETWORK_DIR.values(),
        }
        for directory in directories:
            summaries = []
            actions = []
            raw_actions = []
            for load_index, load in enumerate(plotting.LOAD_ORDER):
                for policy_index, policy in enumerate(
                    ("rule_balanced", "specnet_agent_ts_11", "specnet_agent_ts_23")
                ):
                    summaries.append(
                        {
                            "policy": policy,
                            "load": load,
                            "p99_latency": 20 + load_index + policy_index,
                            "avg_quality": 0.96 - 0.01 * load_index,
                            "quality_violation_ratio": 0.02 * load_index,
                            "wasted_speculative_bytes_per_workflow": 5 + policy_index,
                            "guard_override_ratio": 0.1 * policy_index,
                        }
                    )
                    if policy.startswith("specnet_agent"):
                        actions.append(
                            {
                                "policy": policy,
                                "load": load,
                                "action": "full",
                                "count": 4,
                            }
                        )
                        raw_actions.append(
                            {
                                "policy": policy,
                                "load": load,
                                "action": "critical_only",
                                "count": 4,
                            }
                        )
            path = self.artifacts / directory
            self.write_csv(path / "summary_by_run.csv", summaries)
            self.write_csv(path / "action_counts.csv", actions)
            self.write_csv(path / "raw_action_counts.csv", raw_actions)
            self.write_csv(
                path / "lambda_updates.csv",
                [
                    {
                        "train_seed": seed,
                        "episode": episode,
                        "updated": True,
                        "lambda_after": 0.1 * episode + seed / 1000,
                    }
                    for seed in (11, 23)
                    for episode in (3, 6)
                ],
            )

    def test_aggregation_builds_2x2_and_network_views(self) -> None:
        self.make_input()

        mechanisms = plotting.aggregate_mechanisms(self.artifacts)
        networks = plotting.aggregate_networks(self.artifacts)
        actions = plotting.aggregate_guard_actions(self.artifacts)
        lambdas = plotting.aggregate_lambda(self.artifacts)
        evidence = plotting.paired_evidence(self.artifacts)

        self.assertEqual(len(mechanisms), 12)
        self.assertEqual(len(networks), 9)
        self.assertEqual(len(actions), 30)
        self.assertEqual(len(lambdas), 6)
        self.assertEqual(len(evidence), 60)
        bandit_light = next(
            row
            for row in mechanisms
            if row["mechanism"] == "bandit_on" and row["load"] == "light"
        )
        self.assertEqual(bandit_light["observations"], 2)
        self.assertEqual(bandit_light["p99_latency"], 21.5)
        raw_critical = next(
            row
            for row in actions
            if row["stage"] == "raw"
            and row["load"] == "heavy"
            and row["action"] == "critical_only"
        )
        self.assertEqual(raw_critical["share"], 1.0)

    def test_missing_experiment_file_is_rejected(self) -> None:
        self.make_input()
        path = self.artifacts / "borrowing_guard_off" / "summary_by_run.csv"
        path.unlink()

        with self.assertRaises(FileNotFoundError):
            plotting.aggregate_mechanisms(self.artifacts)


if __name__ == "__main__":
    unittest.main()
