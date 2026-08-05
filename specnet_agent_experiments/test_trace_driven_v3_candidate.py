#!/usr/bin/env python3
"""Tests for the isolated SWE-chat V3 candidate data path."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from specnet_agent_experiments import specnet_agent_experiment as experiment
from specnet_data.build_trace_profile_v3 import build_profile, load_swe_records
from specnet_data.swe_chat_v3 import (
    assign_component_splits,
    empty_conversation_state,
    finalize_record,
    map_tool_service,
    process_conversation_row,
)
from specnet_data.trace_driven_v2 import load_profile as load_v2_profile
from specnet_data.trace_driven_v3 import (
    generate_trace_workload as generate_v3_trace_workload,
    load_profile as load_v3_profile,
)


SPLITS = ("train", "validation", "test")
LOADS = ("light", "medium", "heavy")


def trace_record(split: str, index: int) -> dict[str, object]:
    return {
        "sample_id": f"trace-{split}-{index}",
        "current_input_chars": 1000 + index,
        "output_tokens": 120 + index,
        "round_duration_ms": 20000 + index,
        "tools": [],
    }


def rag_record(split: str, index: int) -> dict[str, object]:
    return {
        "sample_id": f"rag-{split}-{index}",
        "session_id": f"rag-session-{split}-{index}",
        "source_window_id": "rag-window",
        "source_arrival_time_ms": float(index),
        "template_hint": "rag_request",
        "input_tokens": 2500,
        "output_tokens": 180,
        "retrieval_document_count": 5,
        "history_component_count": 1,
        "web_search_component_count": 0,
    }


def swe_record(split: str, index: int, component: str | None = None) -> dict[str, object]:
    return {
        "source_dataset": "swe_chat",
        "source_revision": "fixture-revision",
        "sample_id": f"swe-{split}-{index}",
        "split": split,
        "split_component_id": component or f"component-{split}-{index}",
        "split_unit": "repo_user_connected_component",
        "template_hint": "coding",
        "agent": "Claude Code",
        "input_tokens": 1000 + index,
        "output_tokens": 500 + index,
        "turn_count": 8,
        "user_prompt_count": 2,
        "assistant_response_count": 3,
        "tool_call_count": 12,
        "tool_service_counts": {
            "retrieval": 4,
            "storage": 2,
            "tool": 6,
        },
        "tool_latency_ms_by_service": {
            "retrieval": 120.0,
            "storage": 220.0,
            "tool": 320.0,
        },
        "paired_tool_calls": 11,
        "usable_timing_tool_calls": 10,
        "tool_timing_coverage": 10 / 12,
        "timestamp_coverage": 0.95,
        "mapping_boundaries": {
            "session_duration_used_as_service_time": False,
            "session_success_ground_truth": False,
            "network_deadline_queue_present": False,
        },
    }


def v2_profile() -> dict[str, object]:
    return {
        "schema_version": 2,
        "profile_id": "trace_driven_v2",
        "sources": {
            "tracelab": {"release": "fixture"},
            "burstgpt": {"release": "fixture"},
            "ragpulse": {"release": "fixture"},
        },
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
                split: [trace_record(split, index) for index in range(20)]
                for split in SPLITS
            },
            "ragpulse": {
                split: [rag_record(split, index) for index in range(20)]
                for split in SPLITS
            },
        },
        "arrival_windows": {
            split: {
                load: [
                    {
                        "window_id": f"{split}-{load}",
                        "arrival_offsets": [float(index * index + 1) for index in range(50)],
                    }
                ]
                for load in LOADS
            }
            for split in SPLITS
        },
        "mapping_contract": {
            "tracelab": {"round_duration_anchor_ms": 30000.0},
            "ragpulse": {"fixed_template": "rag_qa"},
            "arrival_source": "burstgpt_v2_natural_day_split",
        },
        "external_benchmarks": {
            "tau3_bench": {"included_in_training_profile": False}
        },
    }


class TraceDrivenV3CandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.v2_path = self.root / "v2.json"
        self.swe_path = self.root / "swe.jsonl"
        self.v3_path = self.root / "v3.json"
        self.v2_path.write_text(json.dumps(v2_profile()), encoding="utf-8")
        with self.swe_path.open("w", encoding="utf-8") as destination:
            for split in SPLITS:
                for index in range(20):
                    destination.write(json.dumps(swe_record(split, index)) + "\n")
        load_v2_profile.cache_clear()
        load_v3_profile.cache_clear()

    def build_fixture_profile(self) -> dict[str, object]:
        profile = build_profile(self.v2_path, self.swe_path)
        self.v3_path.write_text(json.dumps(profile), encoding="utf-8")
        load_v3_profile.cache_clear()
        return profile

    def test_tool_mapping_and_idle_gap_cleaning(self) -> None:
        self.assertEqual(map_tool_service("Read"), "retrieval")
        self.assertEqual(map_tool_service("Edit"), "storage")
        self.assertEqual(map_tool_service("Task"), "llm")
        self.assertEqual(map_tool_service("Bash"), "tool")

        state = empty_conversation_state()
        rows = [
            {
                "turn_number": 1,
                "turn_type": "tool_use",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "tool_name": "Read",
                "tool_call_id": "fast",
                "category": "Research",
            },
            {
                "turn_number": 1,
                "turn_type": "tool_result",
                "timestamp": "2026-01-01T00:00:01+00:00",
                "tool_call_id": "fast",
            },
            {
                "turn_number": 2,
                "turn_type": "tool_use",
                "timestamp": "2026-01-01T00:00:02+00:00",
                "tool_name": "Bash",
                "tool_call_id": "idle",
                "category": "Action",
            },
            {
                "turn_number": 2,
                "turn_type": "tool_result",
                "timestamp": "2026-01-01T00:10:02+00:00",
                "tool_call_id": "idle",
            },
        ]
        for row in rows:
            process_conversation_row(state, row)
        record = finalize_record(
            {
                "session_id": "raw-session",
                "agent": "Claude Code",
                "input_tokens": 10,
                "output_tokens": 20,
                "turn_count": 2,
                "session_success": 90,
                "duration_seconds": 602,
            },
            state,
            "train",
            "component-hash",
            "fixture-revision",
            300000.0,
        )
        self.assertEqual(record["paired_tool_calls"], 2)
        self.assertEqual(record["usable_timing_tool_calls"], 1)
        self.assertEqual(record["idle_gap_excluded_tool_calls"], 1)
        self.assertEqual(record["cleaned_tool_duration_ms_p50"], 1000.0)
        self.assertNotIn("session_id", record)

    def test_repo_user_components_are_deterministic_and_isolated(self) -> None:
        rows = [
            {
                "session_id": "a1",
                "repo_id": "repo-a",
                "user_id": "shared-user",
            },
            {
                "session_id": "b1",
                "repo_id": "repo-b",
                "user_id": "shared-user",
            },
            {"session_id": "c1", "repo_id": "repo-c", "user_id": "u-c"},
            {"session_id": "d1", "repo_id": "repo-d", "user_id": "u-d"},
        ]
        first = assign_component_splits(rows)
        second = assign_component_splits(rows)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["a1"], first[0]["b1"])
        self.assertEqual(first[1]["a1"], first[1]["b1"])
        self.assertEqual(set(first[0].values()), set(SPLITS))

    def test_builder_freezes_candidate_mix_and_keeps_v2_arrivals(self) -> None:
        profile = self.build_fixture_profile()
        self.assertEqual(
            profile["training_contract"]["trace_source_mix"],
            {"tracelab": 0.375, "swe_chat": 0.375, "ragpulse": 0.25},
        )
        self.assertEqual(profile["arrival_windows"], v2_profile()["arrival_windows"])
        self.assertTrue(
            profile["training_contract"]["candidate_only_not_final_profile"]
        )
        self.assertFalse(
            profile["external_benchmarks"]["tau3_bench"][
                "included_in_training_profile"
            ]
        )

    def test_component_split_leakage_is_rejected(self) -> None:
        rows = [
            swe_record("train", 1, "shared"),
            swe_record("validation", 2, "shared"),
            swe_record("test", 3),
        ]
        self.swe_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "component split leakage"):
            load_swe_records(self.swe_path)

    def test_runtime_is_deterministic_and_preserves_source_marginals(self) -> None:
        self.build_fixture_profile()
        first = experiment.generate_workload(
            41,
            "heavy",
            960,
            40,
            workload_profile="trace_driven_v3_candidate",
            phase="test",
            trace_profile_path=str(self.v3_path),
        )
        second = experiment.generate_workload(
            41,
            "heavy",
            960,
            40,
            workload_profile="trace_driven_v3_candidate",
            phase="test",
            trace_profile_path=str(self.v3_path),
        )
        self.assertEqual(first, second)
        self.assertEqual(
            Counter(workflow.record_source for workflow in first),
            {"tracelab": 15, "swe_chat": 15, "ragpulse": 10},
        )
        self.assertEqual(
            Counter(workflow.template for workflow in first),
            {"coding": 30, "rag_qa": 10},
        )
        self.assertEqual(
            {workflow.mapping_version for workflow in first},
            {"fixed_template_v3_candidate_a"},
        )
        self.assertEqual(
            [workflow.arrival_time for workflow in first],
            sorted(workflow.arrival_time for workflow in first),
        )

    def test_small_source_mode_allocations_remain_feasible(self) -> None:
        self.build_fixture_profile()
        for phase in SPLITS:
            for count in range(1, 21):
                rows = generate_v3_trace_workload(
                    profile_path=self.v3_path,
                    seed=1000 + count,
                    load="light",
                    duration=960,
                    max_workflows=count,
                    target_count=count,
                    phase=phase,
                )
                self.assertEqual(len(rows), count)


if __name__ == "__main__":
    unittest.main()
