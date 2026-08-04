#!/usr/bin/env python3
"""Build privacy-safe stage-two artifacts for trace-driven workload v2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SOURCE_SNAPSHOT_DIR = Path(__file__).resolve().parent.parent
if str(SOURCE_SNAPSHOT_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_SNAPSHOT_DIR))

from specnet_data.ragpulse_v2 import (
    adapt_ragpulse_records,
    summarize_ragpulse,
    write_jsonl as write_ragpulse_jsonl,
)
from specnet_data.tau3_benchmark import (
    load_precomputed_benchmark,
    summarize_benchmark,
    write_jsonl as write_tau3_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ragpulse-root", type=Path, required=True)
    parser.add_argument(
        "--tau-root",
        type=Path,
        default=None,
        help=(
            "Optional tau3-bench checkout used only to build a held-out adapter "
            "index; it is not required for the V2/V3 training profiles."
        ),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        required=True,
        help="external_agent_data root; raw outputs are never written here",
    )
    return parser.parse_args()


def load_v1_comparison(
    profile_path: Path,
    ragpulse_summary: dict[str, object],
) -> dict[str, object]:
    """Compare the new RAG request scale with the existing V1 sources."""
    if not profile_path.is_file():
        return {"available": False, "profile_path": str(profile_path)}
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    stats = profile.get("stats") or {}
    burstgpt = stats.get("burstgpt") or {}
    tracelab = stats.get("tracelab") or {}
    burst_input = (burstgpt.get("request_tokens_sample") or {}).get("p50")
    burst_output = (burstgpt.get("response_tokens_sample") or {}).get("p50")
    trace_output = (tracelab.get("output_tokens_sample") or {}).get("p50")
    rag_input = (ragpulse_summary.get("input_tokens") or {}).get("p50")
    rag_output = (ragpulse_summary.get("output_tokens") or {}).get("p50")

    def ratio(numerator: object, denominator: object) -> float | None:
        if not isinstance(numerator, (int, float)):
            return None
        if not isinstance(denominator, (int, float)) or denominator == 0:
            return None
        return float(numerator) / float(denominator)

    return {
        "available": True,
        "profile_id": profile.get("profile_id"),
        "ragpulse_input_p50": rag_input,
        "burstgpt_request_input_p50": burst_input,
        "ragpulse_to_burstgpt_input_p50_ratio": ratio(
            rag_input, burst_input
        ),
        "ragpulse_output_p50": rag_output,
        "burstgpt_response_output_p50": burst_output,
        "ragpulse_to_burstgpt_output_p50_ratio": ratio(
            rag_output, burst_output
        ),
        "tracelab_output_p50": trace_output,
        "ragpulse_to_tracelab_output_p50_ratio": ratio(
            rag_output, trace_output
        ),
        "interpretation": (
            "RAGPulse adds a distinct high-context RAG request distribution; "
            "it does not replace TraceLab step/runtime or BurstGPT scale."
        ),
    }


def main() -> None:
    args = parse_args()
    ragpulse_records = adapt_ragpulse_records(args.ragpulse_root)
    tau3_records = (
        load_precomputed_benchmark(args.tau_root)
        if args.tau_root is not None
        else []
    )
    ragpulse_summary = summarize_ragpulse(ragpulse_records)

    ragpulse_output = (
        args.data_root
        / "processed"
        / "unified_trace_v2"
        / "ragpulse_requests.jsonl"
    )
    tau3_output = (
        args.data_root
        / "processed"
        / "tau3_benchmark_v1_0_1"
        / "precomputed_adapter_index.jsonl"
    )
    report_output = args.data_root / "reports" / "v2_stage2_analysis.json"
    write_ragpulse_jsonl(ragpulse_output, ragpulse_records)
    if args.tau_root is not None:
        write_tau3_jsonl(tau3_output, tau3_records)

    report = {
        "schema_version": 1,
        "generated_at": "2026-08-01",
        "training_data": {
            "ragpulse": ragpulse_summary,
        },
        "external_benchmark": {
            "tau3_bench": (
                summarize_benchmark(tau3_records)
                if args.tau_root is not None
                else {
                    "available": False,
                    "reason": "--tau-root was not supplied; training profile is unaffected",
                }
            ),
        },
        "separation_policy": {
            "tau3_present_in_training_output": False,
            "tau3_present_in_checkpoint_selection": False,
            "tau3_present_in_workload_parameter_fitting": False,
            "precomputed_tau3_is_final_specnet_result": False,
        },
        "v1_comparison": load_v1_comparison(
            args.data_root / "processed" / "trace_driven_v1" / "profile.json",
            ragpulse_summary,
        ),
        "outputs": {
            "ragpulse_requests": str(ragpulse_output),
            "tau3_adapter_index": (
                str(tau3_output) if args.tau_root is not None else None
            ),
        },
    }
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
