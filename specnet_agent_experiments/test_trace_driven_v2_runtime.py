#!/usr/bin/env python3
"""Tests for fixed-template trace_driven_v2 simulator integration."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import specnet_agent_experiment as experiment
from specnet_data.trace_driven_v2 import load_profile


def trace_record(split: str, index: int) -> dict[str, object]:
    return {
        "sample_id": f"trace-{split}-{index}",
        "current_input_chars": 1000 + 20 * index,
        "output_tokens": 120 + index,
        "reasoning_output_tokens": 12 + index,
        "prefix_tokens": 10000 + 100 * index,
        "round_duration_ms": 20000 + 50 * index,
        "tools": [
            {
                "service_type": "tool",
                "latency_ms": 150 + index,
                "is_error": False,
            }
        ],
    }


def rag_record(split: str, index: int) -> dict[str, object]:
    return {
        "sample_id": f"rag-{split}-{index}",
        "session_id": f"rag-session-{split}-{index}",
        "source_window_id": "rag-window-0",
        "source_arrival_time_ms": float(index * 1000),
        "template_hint": "rag_request",
        "input_tokens": 2500 + 50 * index,
        "output_tokens": 180 + 5 * index,
        "retrieval_document_count": 4 + index % 3,
        "history_component_count": 1 + index % 2,
        "web_search_component_count": index % 2,
    }


def fixture_profile() -> dict[str, object]:
    splits = ("train", "validation", "test")
    loads = ("light", "medium", "heavy")
    return {
        "schema_version": 2,
        "profile_id": "trace_driven_v2",
        "training_contract": {
            "trace_source_mix": {"tracelab": 0.75, "ragpulse": 0.25},
            "overall_mode_mix": {
                "train": {"trace": 0.60, "augmented": 0.25, "stress": 0.15},
                "validation": {"trace": 0.70, "stress": 0.30},
                "test": {"trace": 1.00},
            },
            "frozen_before_controller_metrics": True,
        },
        "split_policy": {"ragpulse_temporal_arrival_use": "disabled"},
        "source_records": {
            "tracelab": {
                split: [trace_record(split, index) for index in range(50)]
                for split in splits
            },
            "ragpulse": {
                split: [rag_record(split, index) for index in range(20)]
                for split in splits
            },
        },
        "arrival_windows": {
            split: {
                load: [
                    {
                        "window_id": f"{split}-{load}",
                        "arrival_offsets": [
                            float(index * index + 1) for index in range(100)
                        ],
                    }
                ]
                for load in loads
            }
            for split in splits
        },
        "mapping_contract": {
            "tracelab": {"round_duration_anchor_ms": 30000.0},
            "arrival_source": "burstgpt_v2_natural_day_split",
        },
        "external_benchmarks": {
            "tau3_bench": {"included_in_training_profile": False}
        },
    }


class TraceDrivenV2RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.profile_path = Path(self.tempdir.name) / "profile.json"
        self.profile_path.write_text(
            json.dumps(fixture_profile()), encoding="utf-8"
        )
        load_profile.cache_clear()

    def generate(self, phase: str) -> list[experiment.WorkflowSpec]:
        return experiment.generate_workload(
            41,
            "heavy",
            960,
            40,
            workload_profile="trace_driven_v2",
            phase=phase,
            trace_profile_path=str(self.profile_path),
        )

    def test_v2_generation_is_deterministic_and_source_balanced(self) -> None:
        first = self.generate("test")
        second = self.generate("test")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 40)
        self.assertEqual(
            Counter(spec.record_source for spec in first),
            {"tracelab": 30, "ragpulse": 10},
        )
        self.assertEqual({spec.workload_source for spec in first}, {"trace"})
        self.assertEqual({spec.source_split for spec in first}, {"test"})
        self.assertEqual(
            [spec.arrival_time for spec in first],
            sorted(spec.arrival_time for spec in first),
        )
        self.assertTrue(all(0 < spec.arrival_time < 960 for spec in first))

    def test_v2_uses_fixed_coding_and_rag_templates(self) -> None:
        workload = self.generate("test")
        by_source = {
            source: [spec for spec in workload if spec.record_source == source]
            for source in ("tracelab", "ragpulse")
        }

        self.assertTrue(
            all(spec.template == "coding" for spec in by_source["tracelab"])
        )
        self.assertTrue(
            all(spec.template == "rag_qa" for spec in by_source["ragpulse"])
        )
        self.assertTrue(
            all(len(spec.branches) == 7 for spec in by_source["tracelab"])
        )
        self.assertTrue(
            all(len(spec.branches) == 8 for spec in by_source["ragpulse"])
        )
        self.assertTrue(
            all(sum(branch.required for branch in spec.branches) == 3 for spec in workload)
        )
        self.assertTrue(
            all(
                branch.expected_utility > 0.0
                for spec in workload
                for branch in spec.branches
                if not branch.required
            )
        )
        self.assertEqual(
            {spec.mapping_version for spec in workload},
            {"fixed_template_v2_a"},
        )

    def test_train_and_validation_use_frozen_mode_mixes(self) -> None:
        train = self.generate("train")
        validation = self.generate("validation")

        self.assertEqual(
            Counter(spec.workload_source for spec in train),
            {"trace": 24, "augmented": 10, "stress": 6},
        )
        self.assertEqual(
            Counter(spec.workload_source for spec in validation),
            {"trace": 28, "stress": 12},
        )
        self.assertEqual({spec.source_split for spec in train}, {"train"})
        self.assertEqual(
            {spec.source_split for spec in validation}, {"validation"}
        )

    def test_v2_profile_reaches_checkpoint_validation(self) -> None:
        policy = experiment.train_specnet_agent(
            episodes=2,
            loads=["light"],
            duration=100,
            max_workflows=3,
            max_time=500,
            seed=43,
            quality_weight=1.6,
            checkpoint_episodes=[1],
            checkpoint_selection="best_validation",
            validation_seed=9201,
            checkpoint_eval_runs=1,
            workload_profile="trace_driven_v2",
            trace_profile_path=str(self.profile_path),
        )
        self.assertEqual(policy.training_info["workload_profile"], "trace_driven_v2")
        self.assertTrue(
            all("validation" in item for item in policy.training_checkpoints)
        )


if __name__ == "__main__":
    unittest.main()
