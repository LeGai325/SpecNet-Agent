#!/usr/bin/env python3
"""Tests for trace_driven_v1 workload generation and phase isolation."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from specnet_agent_experiments import specnet_agent_experiment as experiment
from specnet_data.build_trace_profile_v1 import burst_window_key
from specnet_data.trace_driven_v1 import _select_arrivals, load_profile


def fixture_record(sample_id: str, scale: int) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "provider": "fixture",
        "current_input_chars": 1000 * scale,
        "input_tokens_total": 2000 * scale,
        "output_tokens": 200 * scale,
        "reasoning_output_tokens": 20 * scale,
        "prefix_tokens": 10000 * scale,
        "newly_append_tokens": 500 * scale,
        "round_duration_ms": 30000 * scale,
        "tools": [
            {"service_type": "retrieval", "latency_ms": 100 * scale, "is_error": False},
            {"service_type": "tool", "latency_ms": 200 * scale, "is_error": False},
            {"service_type": "storage", "latency_ms": 300 * scale, "is_error": False},
        ],
    }


def fixture_profile() -> dict[str, object]:
    windows = {
        split: {
            load: [
                {
                    "window_id": f"{split}-{load}",
                    "source_count": 300,
                    "arrival_offsets": [float(index * index + 1) for index in range(300)],
                }
            ]
            for load in ("light", "medium", "heavy")
        }
        for split in ("train", "validation", "test")
    }
    return {
        "schema_version": 1,
        "profile_id": "trace_driven_v1",
        "mapping": {"round_duration_anchor_ms": 30000.0},
        "workflow_records": {
            "train": [fixture_record("train-1", 1), fixture_record("train-2", 2)],
            "validation": [fixture_record("validation-1", 1)],
            "test": [fixture_record("test-1", 1)],
        },
        "arrival_windows": windows,
    }


class TraceDrivenWorkloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.profile_path = Path(self.tempdir.name) / "profile.json"
        self.profile_path.write_text(json.dumps(fixture_profile()), encoding="utf-8")
        load_profile.cache_clear()

    def test_default_dispatch_preserves_synthetic_generator(self) -> None:
        expected = experiment.generate_synthetic_workload(17, "medium", 300, 12)
        actual = experiment.generate_workload(17, "medium", 300, 12)
        self.assertEqual(actual, expected)
        self.assertTrue(all(spec.workload_source == "synthetic" for spec in actual))

    def test_burst_windows_never_cross_natural_day_boundaries(self) -> None:
        self.assertEqual(burst_window_key(86399.0, 2600), (0, 33))
        self.assertEqual(burst_window_key(86400.0, 2600), (1, 0))
        self.assertEqual(burst_window_key(86401.0, 2600), (1, 0))

    def test_trace_generation_is_deterministic_and_phase_isolated(self) -> None:
        first = experiment.generate_workload(
            19,
            "heavy",
            2600,
            120,
            workload_profile="trace_driven_v1",
            phase="test",
            trace_profile_path=str(self.profile_path),
        )
        second = experiment.generate_workload(
            19,
            "heavy",
            2600,
            120,
            workload_profile="trace_driven_v1",
            phase="test",
            trace_profile_path=str(self.profile_path),
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 108)
        self.assertTrue(all(spec.source_split == "test" for spec in first))
        self.assertTrue(all(spec.workload_source == "trace" for spec in first))
        self.assertEqual([spec.arrival_time for spec in first], sorted(spec.arrival_time for spec in first))
        self.assertTrue(all(0 < spec.arrival_time < 2600 for spec in first))
        self.assertTrue(all(len(spec.branches) == 7 for spec in first))
        self.assertTrue(all(sum(branch.required for branch in spec.branches) == 3 for spec in first))

    def test_short_trace_window_reaches_target_count(self) -> None:
        profile = fixture_profile()
        profile["arrival_windows"]["test"]["light"][0]["arrival_offsets"] = [1.0, 2.0, 6.0]
        short_path = Path(self.tempdir.name) / "short-window.json"
        short_path.write_text(json.dumps(profile), encoding="utf-8")
        load_profile.cache_clear()

        workload = experiment.generate_workload(
            29,
            "light",
            2600,
            120,
            workload_profile="trace_driven_v1_1",
            phase="test",
            trace_profile_path=str(short_path),
        )

        self.assertEqual(len(workload), 47)
        self.assertEqual(
            [spec.arrival_time for spec in workload],
            sorted(spec.arrival_time for spec in workload),
        )

    def test_v1_preserves_short_window_count_for_reproducibility(self) -> None:
        profile = fixture_profile()
        profile["arrival_windows"]["test"]["light"][0]["arrival_offsets"] = [1.0, 2.0, 6.0]
        short_path = Path(self.tempdir.name) / "legacy-short-window.json"
        short_path.write_text(json.dumps(profile), encoding="utf-8")
        load_profile.cache_clear()

        workload = experiment.generate_workload(
            29,
            "light",
            2600,
            120,
            workload_profile="trace_driven_v1",
            phase="test",
            trace_profile_path=str(short_path),
        )

        self.assertEqual(len(workload), 3)

    def test_short_trace_window_repeats_empirical_gap_pattern(self) -> None:
        arrivals = _select_arrivals(random.Random(31), [10.0, 11.0, 15.0], 7, 1000)
        gaps = [right - left for left, right in zip(arrivals, arrivals[1:])]

        self.assertEqual(len(arrivals), 7)
        large_gap_pattern = [gap > 100 for gap in gaps]
        self.assertTrue(
            all(left != right for left, right in zip(large_gap_pattern, large_gap_pattern[1:]))
        )
        self.assertTrue(all(0 < arrival < 1000 for arrival in arrivals))

    def test_train_and_validation_use_declared_mixes(self) -> None:
        train = experiment.generate_workload(
            23,
            "heavy",
            2600,
            120,
            workload_profile="trace_driven_v1",
            phase="train",
            trace_profile_path=str(self.profile_path),
        )
        validation = experiment.generate_workload(
            23,
            "heavy",
            2600,
            120,
            workload_profile="trace_driven_v1",
            phase="validation",
            trace_profile_path=str(self.profile_path),
        )
        self.assertEqual({spec.source_split for spec in train}, {"train"})
        self.assertEqual({spec.source_split for spec in validation}, {"validation"})
        self.assertEqual({spec.workload_source for spec in train}, {"trace", "augmented", "stress"})
        self.assertEqual({spec.workload_source for spec in validation}, {"trace", "stress"})

    def test_profile_validation_rejects_split_leakage(self) -> None:
        profile = fixture_profile()
        profile["workflow_records"]["validation"][0]["sample_id"] = "train-1"
        invalid_path = Path(self.tempdir.name) / "invalid.json"
        invalid_path.write_text(json.dumps(profile), encoding="utf-8")
        load_profile.cache_clear()
        with self.assertRaisesRegex(ValueError, "split leakage"):
            load_profile(str(invalid_path))

    def test_trace_profile_reaches_checkpoint_validation(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=2,
            loads=["light"],
            duration=100,
            max_workflows=3,
            max_time=500,
            seed=31,
            quality_weight=1.6,
            checkpoint_episodes=[1],
            checkpoint_selection="best_validation",
            validation_seed=9101,
            checkpoint_eval_runs=1,
            workload_profile="trace_driven_v1",
            trace_profile_path=str(self.profile_path),
        )
        self.assertEqual(policy.training_info["workload_profile"], "trace_driven_v1")
        self.assertTrue(all("validation" in item for item in policy.training_checkpoints))


if __name__ == "__main__":
    unittest.main()
