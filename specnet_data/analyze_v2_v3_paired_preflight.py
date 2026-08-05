#!/usr/bin/env python3
"""Summarize a paired V2/V3 simulator preflight without third-party packages."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


LOADS = ("light", "medium", "heavy")
SLACK_BUCKETS = ("loose", "normal", "tight")
FIXED_POLICIES = (
    "fifo",
    "static_priority",
    "critical_path_only",
    "rule_aggressive",
    "rule_balanced",
    "rule_quality_preserving",
)
EXPECTED_SOURCE_MIX = {
    "v2": {"tracelab": 0.75, "ragpulse": 0.25},
    "v3_candidate": {
        "tracelab": 0.375,
        "swe_chat": 0.375,
        "ragpulse": 0.25,
    },
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(value: str) -> float:
    return float(value)


def percentile(values: Iterable[float], fraction: float) -> float | None:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return None
    if len(finite) == 1:
        return finite[0]
    rank = fraction * (len(finite) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    weight = rank - lower
    return finite[lower] * (1.0 - weight) + finite[upper] * weight


def stats(values: Iterable[float]) -> dict[str, float | int | None]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "mean": statistics.fmean(finite) if finite else None,
        "sample_std": statistics.stdev(finite) if len(finite) > 1 else 0.0,
        "min": min(finite, default=None),
        "max": max(finite, default=None),
    }


def ratio_summary(counter: Counter[str]) -> dict[str, dict[str, float | int]]:
    total = sum(counter.values())
    return {
        key: {"count": value, "ratio": value / total if total else 0.0}
        for key, value in sorted(counter.items())
    }


def summarize_slack(rows: list[dict[str, str]]) -> dict[str, Any]:
    buckets: dict[str, Any] = {}
    for bucket in SLACK_BUCKETS:
        selected = [row for row in rows if row["decision_slack_bucket"] == bucket]
        buckets[bucket] = {
            "count": len(selected),
            "ratio": len(selected) / len(rows) if rows else 0.0,
            "deadline_miss_ratio": (
                statistics.fmean(number(row["deadline_miss"]) for row in selected)
                if selected
                else None
            ),
        }
    risks = [buckets[bucket]["deadline_miss_ratio"] for bucket in SLACK_BUCKETS]
    risk_order = all(value is not None for value in risks) and risks == sorted(risks)
    return {"workflows": len(rows), "buckets": buckets, "risk_order_holds": risk_order}


def summarize_source(rows: list[dict[str, str]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["load"], row["record_source"])].append(row)
    result = {}
    for (load, source), selected in sorted(grouped.items()):
        result[f"{load}/{source}"] = {
            "workflows": len(selected),
            "pooled_workflow_p99_latency": percentile(
                (number(row["latency"]) for row in selected), 0.99
            ),
            "deadline_miss_ratio": statistics.fmean(
                number(row["deadline_miss"]) for row in selected
            ),
            "avg_quality": statistics.fmean(number(row["quality"]) for row in selected),
        }
    return result


def selected_checkpoint_and_training_slack(model_path: Path) -> dict[str, Any]:
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    result = {}
    for policy, policy_payload in payload["policies"].items():
        model = policy_payload["model"]
        slack_counts: Counter[str] = Counter()
        for state_text, actions in model["counts"].items():
            state = ast.literal_eval(state_text)
            if len(state) < 2:
                continue
            slack_counts[str(state[1])] += sum(int(value) for value in actions.values())
        total = sum(slack_counts.values())
        result[policy] = {
            "train_seed": int(policy_payload["train_seed"]),
            "selected_checkpoint_episode": int(model["selected_checkpoint_episode"]),
            "slack_buckets": {
                bucket: {
                    "count": slack_counts[bucket],
                    "ratio": slack_counts[bucket] / total if total else 0.0,
                }
                for bucket in SLACK_BUCKETS
            },
            "all_slack_buckets_visited": all(slack_counts[bucket] for bucket in SLACK_BUCKETS),
        }
    return result


def experiment_parameters(directory: Path) -> dict[str, Any]:
    trained = read_csv(directory / "trained_agents.csv")
    summaries = read_csv(directory / "summary_by_run.csv")
    model = json.loads(
        (directory / "specnet_agent_model.json").read_text(encoding="utf-8")
    )

    def unique(field: str) -> str:
        values = {row[field] for row in trained}
        if len(values) != 1:
            raise ValueError(f"{directory}: expected one {field}, got {values}")
        return values.pop()

    return {
        "train_seeds": sorted(int(row["train_seed"]) for row in trained),
        "train_episodes": int(unique("train_episodes")),
        "checkpoint_episodes": [
            int(value) for value in unique("saved_checkpoint_episodes").split(",")
        ],
        "checkpoint_selection": unique("checkpoint_selection"),
        "checkpoint_eval_runs": int(unique("checkpoint_eval_runs")),
        "eval_runs": 1 + max(int(row["run"]) for row in summaries),
        "eval_seed": int(unique("eval_seed")),
        "validation_seed": int(unique("validation_seed")),
        "duration": int(unique("duration")),
        "max_workflows": int(unique("max_workflows")),
        "max_time": int(unique("max_time")),
        "quality_weight": float(unique("quality_weight")),
        "controller_variant": unique("controller_variant"),
        "slack_queue_basis": unique("slack_queue_basis"),
        "slack_queue_weight": float(unique("slack_queue_weight")),
        "network_model": model["network_model"],
        "action_coupling": unique("action_coupling"),
        "safety_guard": unique("safety_guard"),
        "loads": list(model["loads"]),
    }


def summarize_profile(name: str, directory: Path) -> dict[str, Any]:
    aggregate = read_csv(directory / "summary_aggregate.csv")
    workflows = read_csv(directory / "workflow_results.csv")
    learned_aggregate = [row for row in aggregate if row["policy"].startswith("specnet_agent")]
    learned_workflows = [row for row in workflows if row["policy"].startswith("specnet_agent")]

    learned_by_load: dict[str, Any] = {}
    for load in LOADS:
        rows = [row for row in learned_aggregate if row["load"] == load]
        learned_by_load[load] = {
            metric: stats(number(row[metric]) for row in rows)
            for metric in (
                "p99_latency",
                "deadline_miss_ratio",
                "avg_quality",
                "wasted_speculative_bytes_per_workflow",
            )
        }

    per_seed_load = {}
    for row in learned_aggregate:
        per_seed_load[f"{row['train_seed']}/{row['load']}"] = {
            "policy": row["policy"],
            "p99_latency": number(row["p99_latency"]),
            "deadline_miss_ratio": number(row["deadline_miss_ratio"]),
            "avg_quality": number(row["avg_quality"]),
            "wasted_speculative_bytes_per_workflow": number(
                row["wasted_speculative_bytes_per_workflow"]
            ),
        }

    fixed = {}
    for row in aggregate:
        if row["policy"] in FIXED_POLICIES:
            fixed[f"{row['policy']}/{row['load']}"] = {
                "p99_latency": number(row["p99_latency"]),
                "deadline_miss_ratio": number(row["deadline_miss_ratio"]),
                "avg_quality": number(row["avg_quality"]),
                "wasted_speculative_bytes_per_workflow": number(
                    row["wasted_speculative_bytes_per_workflow"]
                ),
            }

    slack_by_load = {
        load: summarize_slack(
            [row for row in learned_workflows if row["load"] == load]
        )
        for load in LOADS
    }
    slack_by_seed_load = {
        f"{seed}/{load}": summarize_slack(
            [
                row
                for row in learned_workflows
                if row["train_seed"] == seed and row["load"] == load
            ]
        )
        for seed in sorted({row["train_seed"] for row in learned_workflows})
        for load in LOADS
    }
    source_counts = Counter(row["record_source"] for row in learned_workflows)
    action_by_load = {
        load: ratio_summary(
            Counter(
                row["action"]
                for row in learned_workflows
                if row["load"] == load
            )
        )
        for load in LOADS
    }
    action_by_seed_load = {
        f"{seed}/{load}": ratio_summary(
            Counter(
                row["action"]
                for row in learned_workflows
                if row["train_seed"] == seed and row["load"] == load
            )
        )
        for seed in sorted({row["train_seed"] for row in learned_workflows})
        for load in LOADS
    }
    expected = EXPECTED_SOURCE_MIX[name]
    observed = ratio_summary(source_counts)
    source_mix_ok = set(observed) == set(expected) and all(
        abs(observed[source]["ratio"] - ratio) <= 0.02
        for source, ratio in expected.items()
    )
    training = selected_checkpoint_and_training_slack(
        directory / "specnet_agent_model.json"
    )
    pressure_gradient = {}
    for seed in sorted({row["train_seed"] for row in learned_aggregate}):
        selected = {
            row["load"]: row
            for row in learned_aggregate
            if row["train_seed"] == seed
        }
        p99 = [number(selected[load]["p99_latency"]) for load in LOADS]
        miss = [number(selected[load]["deadline_miss_ratio"]) for load in LOADS]
        pressure_gradient[seed] = {
            "p99_nondecreasing": p99 == sorted(p99),
            "miss_nondecreasing": miss == sorted(miss),
        }
    monopolies = []
    for key, actions in action_by_seed_load.items():
        for action, action_data in actions.items():
            if action_data["ratio"] >= 0.95:
                monopolies.append({"seed_load": key, "action": action, **action_data})

    checks = {
        "source_mix_within_2pct": source_mix_ok,
        "evaluation_split_is_test_only": {
            row["source_split"] for row in learned_workflows
        } == {"test"},
        "all_training_seeds_visit_all_slack_buckets": all(
            item["all_slack_buckets_visited"] for item in training.values()
        ),
        "all_training_seeds_have_at_least_5pct_tight": all(
            item["slack_buckets"]["tight"]["ratio"] >= 0.05
            for item in training.values()
        ),
        "medium_combined_slack_risk_order_holds": slack_by_load["medium"]["risk_order_holds"],
        "heavy_combined_slack_risk_order_holds": slack_by_load["heavy"]["risk_order_holds"],
        "heavy_slack_risk_order_holds_for_every_seed": all(
            slack_by_seed_load[f"{seed}/heavy"]["risk_order_holds"]
            for seed in {row["train_seed"] for row in learned_aggregate}
        ),
        "pressure_gradient_holds_for_every_seed": all(
            item["p99_nondecreasing"] and item["miss_nondecreasing"]
            for item in pressure_gradient.values()
        ),
        "no_95pct_action_monopoly": not monopolies,
    }
    return {
        "directory": str(directory.resolve()),
        "experiment_parameters": experiment_parameters(directory),
        "checks": checks,
        "learned_metrics_across_training_seeds": learned_by_load,
        "learned_per_seed_load": per_seed_load,
        "fixed_policy_metrics": fixed,
        "combined_slack_risk": slack_by_load,
        "slack_risk_by_seed_load": slack_by_seed_load,
        "training_state_coverage": training,
        "record_source_mix": observed,
        "source_metrics": summarize_source(learned_workflows),
        "action_mix": action_by_load,
        "action_monopolies": monopolies,
        "pressure_gradient": pressure_gradient,
    }


def delta(v2: float, v3: float) -> dict[str, float | None]:
    return {
        "v2": v2,
        "v3_candidate": v3,
        "absolute_change": v3 - v2,
        "relative_change": (v3 - v2) / v2 if v2 else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-dir", type=Path, required=True)
    parser.add_argument("--v3-dir", type=Path, required=True)
    parser.add_argument("--workload-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--experiment-kind",
        choices=("preflight", "formal"),
        default="preflight",
        help="Label the report and apply the appropriate interpretation boundary.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profiles = {
        "v2": summarize_profile("v2", args.v2_dir),
        "v3_candidate": summarize_profile("v3_candidate", args.v3_dir),
    }
    control_parameters_match = (
        profiles["v2"]["experiment_parameters"]
        == profiles["v3_candidate"]["experiment_parameters"]
    )
    learned_delta = {}
    for load in LOADS:
        learned_delta[load] = {}
        for metric in (
            "p99_latency",
            "deadline_miss_ratio",
            "avg_quality",
            "wasted_speculative_bytes_per_workflow",
        ):
            learned_delta[load][metric] = delta(
                profiles["v2"]["learned_metrics_across_training_seeds"][load][metric]["mean"],
                profiles["v3_candidate"]["learned_metrics_across_training_seeds"][load][metric]["mean"],
            )
    fixed_delta = {
        key: {
            metric: delta(
                profiles["v2"]["fixed_policy_metrics"][key][metric],
                profiles["v3_candidate"]["fixed_policy_metrics"][key][metric],
            )
            for metric in profiles["v2"]["fixed_policy_metrics"][key]
        }
        for key in profiles["v2"]["fixed_policy_metrics"]
    }
    workload_report = json.loads(args.workload_report.read_text(encoding="utf-8"))
    v3_checks = profiles["v3_candidate"]["checks"]
    entry_gate = {
        "experiment_control_parameters_match": control_parameters_match,
        "paired_workload_checks_pass": all(workload_report["checks"].values()),
        "v3_source_and_split_checks_pass": (
            v3_checks["source_mix_within_2pct"]
            and v3_checks["evaluation_split_is_test_only"]
        ),
        "v3_training_slack_coverage_pass": (
            v3_checks["all_training_seeds_visit_all_slack_buckets"]
            and v3_checks["all_training_seeds_have_at_least_5pct_tight"]
        ),
        "v3_medium_and_heavy_slack_risk_order_pass": (
            v3_checks["medium_combined_slack_risk_order_holds"]
            and v3_checks["heavy_combined_slack_risk_order_holds"]
        ),
        "v3_pressure_gradient_pass": v3_checks[
            "pressure_gradient_holds_for_every_seed"
        ],
        "v3_no_action_monopoly": v3_checks["no_95pct_action_monopoly"],
    }
    report = {
        "schema_version": 1,
        "generated_at": "2026-08-01",
        "purpose": (
            "V2 vs V3 candidate paired formal experiment"
            if args.experiment_kind == "formal"
            else "V2 vs V3 candidate paired simulator preflight"
        ),
        "experiment_kind": args.experiment_kind,
        "parameters": profiles["v3_candidate"]["experiment_parameters"],
        "entry_gate": entry_gate,
        "profiles": profiles,
        "learned_mean_delta_v3_vs_v2": learned_delta,
        "fixed_policy_delta_v3_vs_v2": fixed_delta,
        "interpretation_boundary": (
            "This formal paired experiment compares workload profiles, not Controller "
            "algorithms. Metric changes include the effect of the record-to-template "
            "mapping and must not be described as a Controller improvement."
            if args.experiment_kind == "formal"
            else "This is a candidate-screening preflight. Its training episodes and "
            "evaluation runs are insufficient for a formal Controller claim unless "
            "they match a separately preregistered formal experiment."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(entry_gate, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
