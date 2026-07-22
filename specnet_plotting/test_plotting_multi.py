#!/usr/bin/env python3
"""Tests for SpecNet network-model plotting data aggregation."""

from __future__ import annotations

import csv
import shutil
import unittest
from pathlib import Path

from plotting_multi import plot_specnet_networks as plotting


class MultiNetworkPlottingTest(unittest.TestCase):
    artifacts = Path(__file__).resolve().parent / "test_artifacts" / "plotting_multi"

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

    def make_input(self, root: Path) -> None:
        for model, relative in plotting.NETWORK_RELATIVE_DIR.items():
            directory = root / relative
            summaries = []
            actions = []
            workflows = []
            for load_index, load in enumerate(plotting.LOAD_ORDER):
                for seed_index, seed in enumerate((11, 23)):
                    summaries.append(
                        {
                            "policy": f"specnet_agent_ts_{seed}",
                            "load": load,
                            "mean_latency": 10 + load_index + seed_index,
                            "p99_latency": 20 + load_index + seed_index,
                            "deadline_miss_ratio": 0.01 * load_index,
                            "wasted_speculative_bytes_per_workflow": 30 + load_index + seed_index,
                            "avg_quality": 0.9 - 0.01 * load_index,
                        }
                    )
                    actions.append(
                        {
                            "policy": f"specnet_agent_ts_{seed}",
                            "load": load,
                            "action": "conservative",
                            "count": 3 + seed_index,
                        }
                    )
                    workflows.append(
                        {
                            "policy": f"specnet_agent_ts_{seed}",
                            "load": load,
                            "latency": 5 + load_index + seed_index,
                        }
                    )
                summaries.append(
                    {
                        "policy": "fifo",
                        "load": load,
                        "mean_latency": 999,
                        "p99_latency": 999,
                        "deadline_miss_ratio": 1,
                        "wasted_speculative_bytes_per_workflow": 999,
                        "avg_quality": 1,
                    }
                )
            self.write_csv(directory / "summary_by_run.csv", summaries)
            self.write_csv(directory / "action_counts.csv", actions)
            self.write_csv(directory / "workflow_results.csv", workflows)

    def test_aggregation_keeps_only_specnet_and_averages_seeds(self) -> None:
        root = self.artifacts / "aggregate"
        self.make_input(root)

        summaries = plotting.aggregate_summary(root)
        actions = plotting.aggregate_actions(root)
        latencies = plotting.workflow_latencies(root, "heavy")

        self.assertEqual(len(summaries), 12)
        first = next(
            row
            for row in summaries
            if row["network_model"] == "shared_16" and row["load"] == "light"
        )
        self.assertEqual(first["observations"], 2)
        self.assertEqual(first["p99_latency"], 20.5)
        self.assertEqual(len(actions), 60)
        conservative = next(
            row
            for row in actions
            if row["network_model"] == "shared_16"
            and row["load"] == "light"
            and row["action"] == "conservative"
        )
        self.assertEqual(conservative["share"], 1.0)
        self.assertEqual(latencies["shared_16"], [7.0, 8.0])

    def test_missing_load_is_rejected(self) -> None:
        root = self.artifacts / "missing"
        self.make_input(root)
        path = root / plotting.NETWORK_RELATIVE_DIR["shared_16"] / "summary_by_run.csv"
        rows = [row for row in plotting.read_csv(path) if row["load"] != "heavy"]
        self.write_csv(path, rows)

        with self.assertRaisesRegex(ValueError, "heavy"):
            plotting.aggregate_summary(root)


if __name__ == "__main__":
    unittest.main()
