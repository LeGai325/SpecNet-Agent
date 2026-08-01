#!/usr/bin/env python3
"""Tests for the profile-only Trace-driven V2 stage."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent
if str(SOURCE_SNAPSHOT_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_SNAPSHOT_DIR))

from specnet_data.build_trace_profile_v2 import build_profile  # noqa: E402
from specnet_data.trace_driven_v2 import (  # noqa: E402
    load_profile,
    sample_trace_records,
)


def v1_record(sample_id: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "current_input_chars": 1000,
        "output_tokens": 100,
        "round_duration_ms": 1000,
        "tools": [],
    }


def v1_profile() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile_id": "trace_driven_v1",
        "sources": {
            "tracelab": {"release": "fixture"},
            "burstgpt": {"release": "fixture"},
        },
        "mapping": {"simulator_template": "coding"},
        "workflow_records": {
            split: [v1_record(f"{split}-trace")]
            for split in ("train", "validation", "test")
        },
        "arrival_windows": {
            split: {
                load: [
                    {
                        "window_id": f"{split}-{load}",
                        "source_count": 2,
                        "arrival_offsets": [1.0, 2.0],
                    }
                ]
                for load in ("light", "medium", "heavy")
            }
            for split in ("train", "validation", "test")
        },
    }


def rag_record(index: int, split: str, session_id: str) -> dict[str, object]:
    record_id = f"rag-{split}-{index}"
    return {
        "source_dataset": "ragpulse",
        "source_version": "3672232d",
        "source_record_id": record_id,
        "session_id": session_id,
        "workflow_id": record_id,
        "source_window_id": "rag-window-0",
        "source_split_unit": "session_id",
        "split": split,
        "arrival_time_ms": float(index * 1000),
        "input_tokens": 3000,
        "output_tokens": 200,
        "retrieval_document_count": 6,
        "history_component_count": 1,
        "web_search_component_count": 0,
    }


class TraceDrivenV2ProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.v1_path = self.root / "v1.json"
        self.rag_path = self.root / "rag.jsonl"
        self.profile_path = self.root / "v2.json"
        self.v1_path.write_text(json.dumps(v1_profile()), encoding="utf-8")
        rows = [
            rag_record(1, "train", "session-train"),
            rag_record(2, "validation", "session-validation"),
            rag_record(3, "test", "session-test"),
        ]
        self.rag_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def write_profile(self) -> dict[str, object]:
        profile = build_profile(self.v1_path, self.rag_path)
        self.profile_path.write_text(
            json.dumps(profile, sort_keys=True), encoding="utf-8"
        )
        load_profile.cache_clear()
        return profile

    def test_profile_freezes_source_mix_and_excludes_tau3(self) -> None:
        profile = self.write_profile()
        self.assertEqual(
            profile["training_contract"]["trace_source_mix"],
            {"tracelab": 0.75, "ragpulse": 0.25},
        )
        self.assertFalse(
            profile["external_benchmarks"]["tau3_bench"][
                "included_in_training_profile"
            ]
        )
        self.assertNotIn("tau3_bench", profile["source_records"])
        self.assertEqual(
            profile["split_policy"]["ragpulse_temporal_arrival_use"],
            "disabled",
        )

    def test_profile_sampling_is_deterministic_and_phase_isolated(self) -> None:
        self.write_profile()
        first = sample_trace_records(self.profile_path, "test", 40, 17)
        second = sample_trace_records(self.profile_path, "test", 40, 17)
        self.assertEqual(first, second)
        counts = {
            source: sum(row["record_source"] == source for row in first)
            for source in ("tracelab", "ragpulse")
        }
        self.assertEqual(counts, {"tracelab": 30, "ragpulse": 10})
        self.assertTrue(all(row["source_split"] == "test" for row in first))

    def test_ragpulse_session_split_leakage_is_rejected(self) -> None:
        rows = [
            rag_record(1, "train", "shared-session"),
            rag_record(2, "validation", "shared-session"),
            rag_record(3, "test", "session-test"),
        ]
        self.rag_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "session split leakage"):
            build_profile(self.v1_path, self.rag_path)

    def test_profile_build_is_deterministic(self) -> None:
        first = build_profile(self.v1_path, self.rag_path)
        second = build_profile(self.v1_path, self.rag_path)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
