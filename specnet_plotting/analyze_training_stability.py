#!/usr/bin/env python3
"""Compare controller training schedules and evaluate saved checkpoints."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple


EXPERIMENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "specnet_agent_experiments"))
if EXPERIMENT_DIR not in sys.path:
    sys.path.insert(0, EXPERIMENT_DIR)

import specnet_agent_experiment as experiment  # noqa: E402


METRICS = (
    "p99_latency",
    "deadline_miss_ratio",
    "wasted_speculative_bytes_per_workflow",
    "avg_quality",
)


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: str, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def mean_and_std(values: Iterable[float]) -> Tuple[float, float]:
    items = list(values)
    return statistics.mean(items), statistics.stdev(items) if len(items) > 1 else 0.0


def final_seed_rows(group_dirs: Dict[str, str]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for group, directory in group_dirs.items():
        summaries = read_csv(os.path.join(directory, "summary_by_run.csv"))
        grouped: Dict[Tuple[str, str, int], List[Dict[str, str]]] = defaultdict(list)
        for summary in summaries:
            variant = summary["controller_variant"]
            if variant:
                grouped[(variant, summary["load"], int(summary["train_seed"]))].append(summary)

        for (variant, load, train_seed), items in sorted(grouped.items()):
            row: Dict[str, object] = {
                "group": group,
                "controller_variant": variant,
                "load": load,
                "train_seed": train_seed,
                "eval_runs": len(items),
            }
            for metric in METRICS:
                row[metric] = statistics.mean(float(item[metric]) for item in items)
            rows.append(row)
    return rows


def aggregate_seed_rows(seed_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in seed_rows:
        grouped[(str(row["group"]), str(row["controller_variant"]), str(row["load"]))].append(row)

    rows: List[Dict[str, object]] = []
    for (group, variant, load), items in sorted(grouped.items()):
        output: Dict[str, object] = {
            "group": group,
            "controller_variant": variant,
            "load": load,
            "train_seeds": len(items),
        }
        for metric in METRICS:
            mean, std = mean_and_std(float(item[metric]) for item in items)
            output[f"{metric}_mean"] = mean
            output[f"{metric}_seed_std"] = std
        rows.append(output)
    return rows


def delta_rows(seed_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    indexed = {
        (
            str(row["group"]),
            str(row["controller_variant"]),
            str(row["load"]),
            int(row["train_seed"]),
        ): row
        for row in seed_rows
    }
    rows: List[Dict[str, object]] = []
    comparison_groups = sorted({str(row["group"]) for row in seed_rows} - {"S0"})
    for group in comparison_groups:
        for key, baseline in sorted(indexed.items()):
            baseline_group, variant, load, train_seed = key
            if baseline_group != "S0":
                continue
            current = indexed[(group, variant, load, train_seed)]
            row: Dict[str, object] = {
                "group": group,
                "baseline": "S0",
                "controller_variant": variant,
                "load": load,
                "train_seed": train_seed,
            }
            for metric in METRICS:
                row[f"delta_{metric}"] = float(current[metric]) - float(baseline[metric])
            rows.append(row)
    return rows


def deserialize_snapshot(checkpoint: Dict[str, object]) -> Dict[str, object]:
    return {
        "q_values": {
            ast.literal_eval(state): {action: float(value) for action, value in values.items()}
            for state, values in checkpoint["q_values"].items()
        },
        "counts": {
            ast.literal_eval(state): {action: int(value) for action, value in values.items()}
            for state, values in checkpoint["counts"].items()
        },
    }


def source_policy(policy_record: Dict[str, object]) -> experiment.SpecNetAgentBanditPolicy:
    model = policy_record["model"]
    schedule = model["training_schedule"]
    return experiment.SpecNetAgentBanditPolicy(
        seed=int(policy_record["train_seed"]),
        epsilon=float(schedule["epsilon_start"]),
        learning_rate=float(schedule["learning_rate_start"]),
        train=False,
        name=str(model["name"]),
        quality_weight=float(policy_record["quality_weight"]),
        controller_variant=str(policy_record["controller_variant"]),
        epsilon_schedule=str(schedule["epsilon_schedule"]),
        epsilon_end=float(schedule["epsilon_end"]),
        epsilon_decay_fraction=float(schedule["epsilon_decay_fraction"]),
        learning_rate_schedule=str(schedule["learning_rate_schedule"]),
        learning_rate_min=float(schedule["learning_rate_min"]),
    )


def checkpoint_validation_rows(
    group_dirs: Dict[str, str],
    validation_seed: int,
    validation_runs: int,
) -> Tuple[List[Dict[str, object]], Dict[Tuple[str, str, int, int], Dict[str, object]]]:
    rows: List[Dict[str, object]] = []
    snapshots: Dict[Tuple[str, str, int, int], Dict[str, object]] = {}
    for group, directory in group_dirs.items():
        with open(os.path.join(directory, "specnet_agent_model.json"), encoding="utf-8") as handle:
            artifact = json.load(handle)
        for policy_record in artifact["policies"].values():
            variant = str(policy_record["controller_variant"])
            train_seed = int(policy_record["train_seed"])
            policy = source_policy(policy_record)
            training_info = policy_record["model"]["training_info"]
            for checkpoint in policy_record["model"]["training_checkpoints"]:
                episode = int(checkpoint["episode"])
                snapshot = deserialize_snapshot(checkpoint)
                snapshots[(group, variant, train_seed, episode)] = snapshot
                result = experiment.evaluate_training_checkpoint(
                    policy,
                    snapshot,
                    list(artifact["loads"]),
                    int(training_info["duration"]),
                    int(training_info["max_workflows"]),
                    int(training_info["max_time"]),
                    validation_seed,
                    validation_runs,
                )
                for load, metrics in result["loads"].items():
                    rows.append(
                        {
                            "group": group,
                            "controller_variant": variant,
                            "train_seed": train_seed,
                            "episode": episode,
                            "load": load,
                            "validation_seed": validation_seed,
                            "validation_runs": validation_runs,
                            "overall_mean_reward": result["score"],
                            **metrics,
                        }
                    )
    return rows, snapshots


def aggregate_checkpoint_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, int, str], List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["group"]),
                str(row["controller_variant"]),
                int(row["episode"]),
                str(row["load"]),
            )
        ].append(row)

    metrics = ("mean_reward",) + METRICS
    output_rows: List[Dict[str, object]] = []
    for (group, variant, episode, load), items in sorted(grouped.items()):
        output: Dict[str, object] = {
            "group": group,
            "controller_variant": variant,
            "episode": episode,
            "load": load,
            "train_seeds": len(items),
        }
        for metric in metrics:
            mean, std = mean_and_std(float(item[metric]) for item in items)
            output[f"{metric}_mean"] = mean
            output[f"{metric}_seed_std"] = std
        output_rows.append(output)
    return output_rows


def checkpoint_45_to_90_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    indexed = {
        (
            str(row["group"]),
            str(row["controller_variant"]),
            int(row["train_seed"]),
            int(row["episode"]),
            str(row["load"]),
        ): row
        for row in rows
    }
    output: List[Dict[str, object]] = []
    identities = sorted({(key[0], key[1], key[2], key[4]) for key in indexed})
    for group, variant, train_seed, load in identities:
        earlier = indexed[(group, variant, train_seed, 45, load)]
        later = indexed[(group, variant, train_seed, 90, load)]
        row: Dict[str, object] = {
            "group": group,
            "controller_variant": variant,
            "train_seed": train_seed,
            "load": load,
        }
        for metric in ("mean_reward",) + METRICS:
            row[f"delta_{metric}"] = float(later[metric]) - float(earlier[metric])
        output.append(row)
    return output


def aggregate_checkpoint_delta_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["group"]), str(row["controller_variant"]), str(row["load"]))].append(row)

    metrics = ("mean_reward",) + METRICS
    output_rows: List[Dict[str, object]] = []
    for (group, variant, load), items in sorted(grouped.items()):
        output: Dict[str, object] = {
            "group": group,
            "controller_variant": variant,
            "load": load,
            "train_seeds": len(items),
        }
        for metric in metrics:
            mean, std = mean_and_std(float(item[f"delta_{metric}"]) for item in items)
            output[f"delta_{metric}_mean"] = mean
            output[f"delta_{metric}_seed_std"] = std
        output_rows.append(output)
    return output_rows


def checkpoint_score_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    unique_scores = {
        (
            str(row["group"]),
            str(row["controller_variant"]),
            int(row["train_seed"]),
            int(row["episode"]),
        ): float(row["overall_mean_reward"])
        for row in rows
    }
    grouped: Dict[Tuple[str, str, int], List[float]] = defaultdict(list)
    for (group, variant, _train_seed, episode), score in unique_scores.items():
        grouped[(group, variant, episode)].append(score)

    output: List[Dict[str, object]] = []
    for (group, variant, episode), scores in sorted(grouped.items()):
        mean, std = mean_and_std(scores)
        output.append(
            {
                "group": group,
                "controller_variant": variant,
                "episode": episode,
                "train_seeds": len(scores),
                "overall_mean_reward_mean": mean,
                "overall_mean_reward_seed_std": std,
            }
        )
    return output


def greedy_action(values: Dict[str, float]) -> str:
    return max(experiment.ACTIONS, key=lambda action: (values.get(action, 0.0), -experiment.ACTIONS.index(action)))


def policy_churn_rows(
    snapshots: Dict[Tuple[str, str, int, int], Dict[str, object]]
) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    identities = sorted({key[:3] for key in snapshots})
    for group, variant, train_seed in identities:
        episodes = sorted(key[3] for key in snapshots if key[:3] == (group, variant, train_seed))
        for earlier_episode, later_episode in zip(episodes, episodes[1:]):
            earlier = snapshots[(group, variant, train_seed, earlier_episode)]["q_values"]
            later = snapshots[(group, variant, train_seed, later_episode)]["q_values"]
            states = sorted(set(earlier) | set(later))
            changed = sum(
                greedy_action(earlier.get(state, {})) != greedy_action(later.get(state, {}))
                for state in states
            )
            squared_differences = [
                (earlier.get(state, {}).get(action, 0.0) - later.get(state, {}).get(action, 0.0)) ** 2
                for state in states
                for action in experiment.ACTIONS
            ]
            output.append(
                {
                    "group": group,
                    "controller_variant": variant,
                    "train_seed": train_seed,
                    "from_episode": earlier_episode,
                    "to_episode": later_episode,
                    "states": len(states),
                    "changed_greedy_states": changed,
                    "greedy_policy_churn": changed / max(1, len(states)),
                    "q_value_rmse": math.sqrt(statistics.mean(squared_differences)),
                }
            )
    return output


def aggregate_policy_churn_rows(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, int, int], List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["group"]),
                str(row["controller_variant"]),
                int(row["from_episode"]),
                int(row["to_episode"]),
            )
        ].append(row)

    output: List[Dict[str, object]] = []
    for (group, variant, from_episode, to_episode), items in sorted(grouped.items()):
        churn_mean, churn_std = mean_and_std(float(item["greedy_policy_churn"]) for item in items)
        rmse_mean, rmse_std = mean_and_std(float(item["q_value_rmse"]) for item in items)
        output.append(
            {
                "group": group,
                "controller_variant": variant,
                "from_episode": from_episode,
                "to_episode": to_episode,
                "train_seeds": len(items),
                "greedy_policy_churn_mean": churn_mean,
                "greedy_policy_churn_seed_std": churn_std,
                "q_value_rmse_mean": rmse_mean,
                "q_value_rmse_seed_std": rmse_std,
            }
        )
    return output


def final_state_coverage_rows(group_dirs: Dict[str, str]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for group, directory in group_dirs.items():
        with open(os.path.join(directory, "specnet_agent_model.json"), encoding="utf-8") as handle:
            artifact = json.load(handle)
        for policy_record in artifact["policies"].values():
            model = policy_record["model"]
            features = list(model["state_features"])
            if "slack" not in features:
                continue
            slack_index = features.index("slack")
            bucket_counts = {bucket: 0 for bucket in ("tight", "normal", "loose")}
            for state_text, action_counts in model["counts"].items():
                state = ast.literal_eval(state_text)
                bucket_counts[state[slack_index]] += sum(int(count) for count in action_counts.values())
            total = sum(bucket_counts.values())
            rows.append(
                {
                    "group": group,
                    "controller_variant": policy_record["controller_variant"],
                    "train_seed": int(policy_record["train_seed"]),
                    "total_updates": total,
                    **{f"{bucket}_updates": bucket_counts[bucket] for bucket in bucket_counts},
                    **{
                        f"{bucket}_share": bucket_counts[bucket] / max(1, total)
                        for bucket in bucket_counts
                    },
                }
            )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s0-dir", required=True)
    parser.add_argument("--s1-dir", required=True)
    parser.add_argument("--s2-dir", required=True)
    parser.add_argument("--s3-dir", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--validation-seed", type=int, default=507007)
    parser.add_argument("--validation-runs", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.validation_runs <= 0:
        raise SystemExit("validation runs must be positive")
    group_dirs = {"S0": args.s0_dir, "S1": args.s1_dir, "S2": args.s2_dir}
    if args.s3_dir:
        group_dirs["S3"] = args.s3_dir

    seed_rows = final_seed_rows(group_dirs)
    write_csv(os.path.join(args.output_dir, "final_metrics_by_seed.csv"), seed_rows)
    write_csv(os.path.join(args.output_dir, "final_metrics_summary.csv"), aggregate_seed_rows(seed_rows))
    write_csv(os.path.join(args.output_dir, "final_deltas_vs_s0.csv"), delta_rows(seed_rows))

    validation_rows, snapshots = checkpoint_validation_rows(
        group_dirs,
        args.validation_seed,
        args.validation_runs,
    )
    write_csv(os.path.join(args.output_dir, "checkpoint_validation_by_seed.csv"), validation_rows)
    write_csv(
        os.path.join(args.output_dir, "checkpoint_validation_summary.csv"),
        aggregate_checkpoint_rows(validation_rows),
    )
    write_csv(
        os.path.join(args.output_dir, "checkpoint_score_summary.csv"),
        checkpoint_score_rows(validation_rows),
    )
    checkpoint_delta_rows = checkpoint_45_to_90_rows(validation_rows)
    write_csv(
        os.path.join(args.output_dir, "checkpoint_45_to_90_deltas.csv"),
        checkpoint_delta_rows,
    )
    write_csv(
        os.path.join(args.output_dir, "checkpoint_45_to_90_summary.csv"),
        aggregate_checkpoint_delta_rows(checkpoint_delta_rows),
    )
    churn_rows = policy_churn_rows(snapshots)
    write_csv(os.path.join(args.output_dir, "checkpoint_policy_churn.csv"), churn_rows)
    write_csv(
        os.path.join(args.output_dir, "checkpoint_policy_churn_summary.csv"),
        aggregate_policy_churn_rows(churn_rows),
    )
    write_csv(
        os.path.join(args.output_dir, "final_state_coverage.csv"),
        final_state_coverage_rows(group_dirs),
    )
    print("Wrote training-stability analysis to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
