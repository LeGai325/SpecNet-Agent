#!/usr/bin/env python3
"""Tests for V2 RAGPulse and held-out tau3 data adapters."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent
if str(SOURCE_SNAPSHOT_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_SNAPSHOT_DIR))

from specnet_data.ragpulse_v2 import (
    adapt_ragpulse_records,
    summarize_ragpulse,
)
from specnet_data.tau3_benchmark import (
    RESULT_FILES,
    load_precomputed_benchmark,
    summarize_benchmark,
)


def rag_row(timestamp: int, session_id: str) -> dict[str, object]:
    return {
        "timestamp": str(timestamp),
        "input_length": 1200,
        "output_length": 120,
        "hash_ids": {
            "sys_prompt": [1],
            "passages_ids": [2, 3],
            "history": [4],
            "web_search": [],
            "user_input": [5],
        },
        "session_id": session_id,
    }


def tau_simulation(domain: str, trial: int) -> dict[str, object]:
    return {
        "id": f"{domain}-simulation-{trial}",
        "task_id": "task-1",
        "trial": trial,
        "duration": 2.5,
        "termination_reason": "agent_stop",
        "reward_info": {"reward": 1.0 if trial == 0 else 0.0},
        "messages": [
            {
                "role": "assistant",
                "content": "must not be copied",
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "lookup",
                        "arguments": {"private": "must not be copied"},
                    }
                ],
            },
            {
                "role": "tool",
                "id": "call-1",
                "content": "must not be copied",
                "error": False,
            },
        ],
    }


class RagpulseV2AdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        (self.root / "data").mkdir()

    def write_rows(self, rows: list[dict[str, object]]) -> None:
        path = self.root / "data" / "0_trace.jsonl"
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_request_mapping_is_deterministic_and_session_isolated(self) -> None:
        self.write_rows(
            [
                rag_row(10, "session-a"),
                rag_row(20, "session-b"),
                rag_row(3, "session-a"),
                rag_row(7, "session-c"),
            ]
        )
        first = adapt_ragpulse_records(self.root)
        second = adapt_ragpulse_records(self.root)

        self.assertEqual(first, second)
        self.assertEqual(
            [record["source_window_id"] for record in first],
            ["rag-window-0", "rag-window-0", "rag-window-1", "rag-window-1"],
        )
        session_a = [
            record
            for record in first
            if record["session_id"] == first[0]["session_id"]
        ]
        self.assertEqual(len(session_a), 2)
        self.assertEqual(len({record["split"] for record in session_a}), 1)
        self.assertNotEqual(first[0]["workflow_id"], first[2]["workflow_id"])
        self.assertNotIn("session-a", json.dumps(first))

        summary = summarize_ragpulse(first)
        self.assertEqual(
            summary["logical_windows"],
            {"rag-window-0": 2, "rag-window-1": 2},
        )
        self.assertTrue(
            all(value == 0 for value in summary["split_session_overlap"].values())
        )

    def test_missing_component_container_is_rejected(self) -> None:
        row = rag_row(10, "session-a")
        row["hash_ids"] = {"sys_prompt": []}
        self.write_rows([row])
        with self.assertRaisesRegex(ValueError, "hash_ids.passages_ids"):
            adapt_ragpulse_records(self.root)


class Tau3BenchmarkAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        for domain, relative in RESULT_FILES.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "simulations": [
                    tau_simulation(domain, 0),
                    tau_simulation(domain, 1),
                ]
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

    def test_adapter_is_metadata_only_and_never_becomes_training_data(self) -> None:
        records = load_precomputed_benchmark(self.root)
        self.assertEqual(len(records), 6)
        self.assertTrue(
            all(record["usage"] == "adapter_regression_only" for record in records)
        )
        self.assertTrue(
            all(record["benchmark_split"] == "base" for record in records)
        )
        serialized = json.dumps(records)
        self.assertNotIn("must not be copied", serialized)
        self.assertNotIn("arguments", serialized)
        self.assertNotIn('"split"', serialized)
        self.assertTrue(
            all(record["tool_call_count"] == 1 for record in records)
        )
        self.assertTrue(
            all(record["matched_tool_result_count"] == 1 for record in records)
        )

        summary = summarize_benchmark(records)
        self.assertEqual(summary["evaluation_units"], 3)
        self.assertEqual(summary["trials_per_evaluation_unit"], 2.0)
        self.assertIn("controller_training", summary["forbidden_uses"])


if __name__ == "__main__":
    unittest.main()
