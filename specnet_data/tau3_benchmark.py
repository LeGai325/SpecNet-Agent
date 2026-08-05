"""Held-out tau3-bench adapter that never emits raw conversations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SOURCE_ID = "tau3_bench"
SOURCE_VERSION = "v1.0.1"
RESULT_FILES = {
    "airline": (
        "data/tau2/results/final/"
        "gpt-4.1-mini-2025-04-14_airline_base_"
        "gpt-4.1-2025-04-14_4trials.json"
    ),
    "retail": (
        "data/tau2/results/final/"
        "gpt-4.1-mini-2025-04-14_retail_base_"
        "gpt-4.1-2025-04-14_4trials.json"
    ),
    "telecom": (
        "data/tau2/results/final/"
        "gpt-4.1-mini-2025-04-14_telecom_base_"
        "gpt-4.1-2025-04-14_4trials.json"
    ),
}


def _stable_id(namespace: str, value: str) -> str:
    payload = f"tau3-adapter:{namespace}:{value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def adapt_simulation(
    domain: str,
    simulation: dict[str, Any],
) -> dict[str, Any]:
    """Convert one trajectory to privacy-safe benchmark metadata."""
    for field in ("id", "task_id", "trial", "messages", "reward_info"):
        if simulation.get(field) is None:
            raise ValueError(f"tau3 {domain} simulation missing {field}")
    messages = simulation["messages"]
    if not isinstance(messages, list):
        raise ValueError(f"tau3 {domain} simulation messages must be a list")

    roles: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    call_ids: set[str] = set()
    result_ids: set[str] = set()
    tool_errors = 0
    tool_call_count = 0
    prompt_tokens = 0
    completion_tokens = 0
    messages_with_usage = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown")
        roles[role] += 1
        usage = message.get("usage")
        if isinstance(usage, dict):
            messages_with_usage += 1
            prompt_tokens += int(_numeric(usage.get("prompt_tokens")) or 0)
            completion_tokens += int(
                _numeric(usage.get("completion_tokens")) or 0
            )
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                tool_call_count += 1
                if call.get("id") is not None:
                    call_ids.add(str(call["id"]))
                if call.get("name") is not None:
                    tool_names[str(call["name"])] += 1
        if role == "tool":
            if message.get("id") is not None:
                result_ids.add(str(message["id"]))
            if message.get("error") is True:
                tool_errors += 1

    reward_info = simulation["reward_info"]
    if not isinstance(reward_info, dict):
        raise ValueError(f"tau3 {domain} reward_info must be an object")
    reward = _numeric(reward_info.get("reward"))
    task_id = str(simulation["task_id"])
    simulation_id = str(simulation["id"])
    return {
        "source_dataset": SOURCE_ID,
        "source_version": SOURCE_VERSION,
        "benchmark_split": "base",
        "usage": "adapter_regression_only",
        "domain": domain,
        "task_id": task_id,
        "evaluation_unit": f"{domain}:{task_id}",
        "trial": int(simulation["trial"]),
        "simulation_id": f"tau-sim-{_stable_id('simulation', simulation_id)}",
        "duration_ms": (_numeric(simulation.get("duration")) or 0.0) * 1000.0,
        "outcome_score": reward,
        "success": reward is not None and reward >= 1.0,
        "termination_reason": str(
            simulation.get("termination_reason") or "unknown"
        ),
        "message_count": len(messages),
        "message_roles": dict(sorted(roles.items())),
        "messages_with_usage": messages_with_usage,
        "prompt_tokens_total": prompt_tokens,
        "completion_tokens_total": completion_tokens,
        "tool_call_count": tool_call_count,
        "tool_result_count": len(result_ids),
        "matched_tool_result_count": len(call_ids.intersection(result_ids)),
        "tool_error_count": tool_errors,
        "tool_names": dict(sorted(tool_names.items())),
        "field_provenance": {
            "task_id": "real",
            "duration_ms": "mapped",
            "outcome_score": "real",
            "message_count": "mapped",
            "tool_call_count": "mapped",
            "deadline_or_slo": "missing",
            "network_telemetry": "missing",
            "counterfactual_controller_actions": "missing",
        },
    }


def load_precomputed_benchmark(root: Path) -> list[dict[str, Any]]:
    """Load fixed precomputed base runs for adapter regression only."""
    records: list[dict[str, Any]] = []
    for domain, relative_path in RESULT_FILES.items():
        path = root / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"missing tau3 result file: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        simulations = payload.get("simulations")
        if not isinstance(simulations, list):
            raise ValueError(f"tau3 result has no simulations list: {path}")
        records.extend(
            adapt_simulation(domain, simulation)
            for simulation in simulations
            if isinstance(simulation, dict)
        )
    if not records:
        raise ValueError("tau3 precomputed benchmark has no records")
    ids = [str(record["simulation_id"]) for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("tau3 precomputed benchmark has duplicate simulation IDs")
    return records


def summarize_benchmark(records: list[dict[str, Any]]) -> dict[str, Any]:
    domains = Counter(str(record["domain"]) for record in records)
    evaluation_units = {
        str(record["evaluation_unit"]) for record in records
    }
    rewards = [
        float(record["outcome_score"])
        for record in records
        if record["outcome_score"] is not None
    ]
    tool_calls = sum(int(record["tool_call_count"]) for record in records)
    matched_results = sum(
        int(record["matched_tool_result_count"]) for record in records
    )
    incomplete = [
        record
        for record in records
        if int(record["matched_tool_result_count"])
        != int(record["tool_call_count"])
    ]
    return {
        "source": SOURCE_ID,
        "version": SOURCE_VERSION,
        "precomputed_trajectories": len(records),
        "evaluation_units": len(evaluation_units),
        "domains": dict(sorted(domains.items())),
        "trials_per_evaluation_unit": len(records) / len(evaluation_units),
        "reward_coverage": len(rewards) / len(records),
        "reward_pass_rate": (
            sum(reward >= 1.0 for reward in rewards) / len(rewards)
            if rewards
            else None
        ),
        "tool_calls": tool_calls,
        "matched_tool_results": matched_results,
        "unmatched_tool_calls": tool_calls - matched_results,
        "incomplete_trajectories": len(incomplete),
        "incomplete_termination_reasons": dict(
            sorted(
                Counter(
                    str(record["termination_reason"]) for record in incomplete
                ).items()
            )
        ),
        "incomplete_successes": sum(bool(record["success"]) for record in incomplete),
        "tool_call_result_match_ratio": (
            matched_results / tool_calls
            if tool_calls
            else None
        ),
        "fit_decision": "retain_as_heldout_external_benchmark_only",
        "fit_strength": "high_for_outcome_low_for_workload_calibration",
        "allowed_uses": [
            "benchmark_adapter_regression",
            "heldout_external_task_evaluation_after_runner_integration",
        ],
        "forbidden_uses": [
            "controller_training",
            "checkpoint_selection",
            "workload_parameter_fitting",
            "production_arrival_or_network_calibration",
        ],
        "current_blocker": (
            "SpecNet does not yet execute tau3 tasks through its controlled "
            "network path; precomputed runs are not a SpecNet result"
        ),
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False))
            handle.write("\n")
