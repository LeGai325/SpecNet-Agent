"""Controller training, validation, checkpointing, and argument helpers."""
from __future__ import annotations

import argparse
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from .config import ACTIONS, CONTROLLER_VARIANT_FEATURES, DEFAULT_SLACK_QUEUE_BASIS, DEFAULT_SLACK_QUEUE_WEIGHT, StateKey
from .policies import SpecNetAgentBanditPolicy
from .simulator import Simulator
from .workload import generate_workload


def quality_weight_policy_name(
    weight: float,
    multi_weight: bool,
    train_seed: Optional[int] = None,
    multi_train_seed: bool = False,
    controller_variant: str = "full",
    multi_controller_variant: bool = False,
) -> str:
    include_variant = multi_controller_variant or controller_variant != "full"
    include_weight = multi_weight or include_variant
    name_parts = ["specnet_agent"]
    if include_variant:
        name_parts.append(controller_variant)
    if include_weight:
        name_parts.extend(("qw", f"{weight:.2f}".replace(".", "_")))
    if multi_train_seed:
        name_parts.extend(("ts", str(train_seed)))
    return "_".join(name_parts)


def parse_quality_weights(args: argparse.Namespace) -> List[float]:
    if not args.quality_weights:
        return [args.quality_weight]
    weights: List[float] = []
    for item in args.quality_weights.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            weights.append(float(item))
        except ValueError as exc:
            raise SystemExit(f"Invalid quality weight: {item}") from exc
    if not weights:
        raise SystemExit("At least one quality weight is required.")
    return weights


def parse_int_list(text: str, label: str) -> List[int]:
    values: List[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise SystemExit(f"Invalid {label}: {item}") from exc
    if not values:
        raise SystemExit(f"At least one {label} is required.")
    return values


def parse_train_seeds(args: argparse.Namespace) -> List[int]:
    if args.train_seeds:
        return parse_int_list(args.train_seeds, "train seed")
    if args.train_seed is not None:
        return [args.train_seed]
    return [args.seed]


def parse_controller_variants(args: argparse.Namespace) -> List[str]:
    variants = [item.strip() for item in args.controller_variants.split(",") if item.strip()]
    if not variants:
        raise SystemExit("At least one controller variant is required.")
    invalid_variants = [variant for variant in variants if variant not in CONTROLLER_VARIANT_FEATURES]
    if invalid_variants:
        valid_text = ",".join(CONTROLLER_VARIANT_FEATURES)
        raise SystemExit(f"Invalid controller variants: {invalid_variants}. Valid variants: {valid_text}")
    if len(set(variants)) != len(variants):
        raise SystemExit("Controller variants must not contain duplicates.")
    return variants


def parse_checkpoint_episodes(text: str) -> List[int]:
    if not text.strip():
        return []
    episodes = parse_int_list(text, "checkpoint episode")
    if any(episode <= 0 for episode in episodes):
        raise SystemExit("Checkpoint episodes must be positive.")
    return sorted(set(episodes))


def serialize_model_snapshot(snapshot: Dict[str, object]) -> Dict[str, object]:
    q_values = snapshot["q_values"]
    counts = snapshot["counts"]
    return {
        "q_values": {str(state): dict(values) for state, values in q_values.items()},
        "counts": {str(state): dict(values) for state, values in counts.items()},
    }


def summarize_training_window(summaries: List[Dict[str, object]]) -> Dict[str, object]:
    by_load: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    action_counts: Counter[str] = Counter()
    for summary in summaries:
        by_load[str(summary["load"])].append(summary)
        action_counts.update(summary["action_counts"])

    metrics = (
        "p99_latency",
        "deadline_miss_ratio",
        "wasted_speculative_bytes_per_workflow",
        "avg_quality",
    )
    return {
        "episodes": len(summaries),
        "loads": {
            load: {
                metric: statistics.mean(float(summary[metric]) for summary in load_summaries)
                for metric in metrics
            }
            for load, load_summaries in sorted(by_load.items())
        },
        "action_counts": dict(action_counts),
    }


def policy_from_snapshot(
    source: SpecNetAgentBanditPolicy,
    snapshot: Dict[str, object],
    seed: int,
) -> SpecNetAgentBanditPolicy:
    policy = SpecNetAgentBanditPolicy(
        seed=seed,
        epsilon=source.epsilon_start,
        learning_rate=source.learning_rate_start,
        train=False,
        name=source.name,
        quality_weight=source.quality_weight,
        controller_variant=source.controller_variant,
        epsilon_schedule=source.epsilon_schedule,
        epsilon_end=source.epsilon_end,
        epsilon_decay_fraction=source.epsilon_decay_fraction,
        learning_rate_schedule=source.learning_rate_schedule,
        learning_rate_min=source.learning_rate_min,
        slack_queue_basis=source.slack_queue_basis,
        slack_queue_weight=source.slack_queue_weight,
    )
    policy.restore_snapshot(snapshot)
    policy.set_evaluation_mode()
    return policy


def evaluate_training_checkpoint(
    source: SpecNetAgentBanditPolicy,
    snapshot: Dict[str, object],
    loads: List[str],
    duration: int,
    max_workflows: int,
    max_time: int,
    validation_seed: int,
    validation_runs: int,
) -> Dict[str, object]:
    by_load: Dict[str, List[Dict[str, float]]] = defaultdict(list)

    run_rewards: List[float] = []
    for load_index, load in enumerate(loads):
        for run_index in range(validation_runs):
            workload_seed = validation_seed + 30000 + 1000 * run_index + 17 * load_index
            specs = generate_workload(workload_seed, load, duration, max_workflows)
            policy = policy_from_snapshot(source, snapshot, workload_seed)
            sim = Simulator(
                specs,
                policy,
                load,
                workload_seed,
                duration,
                max_time,
                quality_weight=source.quality_weight,
                slack_queue_basis=source.slack_queue_basis,
                slack_queue_weight=source.slack_queue_weight,
            )
            summary = sim.run()
            rewards = [sim.workflow_reward(workflow) for workflow in sim.completed_workflows]
            mean_reward = statistics.mean(rewards) if rewards else -10.0
            run_rewards.append(mean_reward)
            by_load[load].append(
                {
                    "mean_reward": mean_reward,
                    "p99_latency": float(summary["p99_latency"]),
                    "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
                    "wasted_speculative_bytes_per_workflow": float(
                        summary["wasted_speculative_bytes_per_workflow"]
                    ),
                    "avg_quality": float(summary["avg_quality"]),
                }
            )

    return {
        "score": statistics.mean(run_rewards),
        "seed": validation_seed,
        "runs_per_load": validation_runs,
        "loads": {
            load: {
                metric: statistics.mean(item[metric] for item in items)
                for metric in items[0]
            }
            for load, items in sorted(by_load.items())
        },
    }


def train_specnet_agent(
    episodes: int,
    loads: List[str],
    duration: int,
    max_workflows: int,
    max_time: int,
    seed: int,
    quality_weight: float,
    policy_name: str = "specnet_agent",
    controller_variant: str = "full",
    epsilon_schedule: str = "linear",
    epsilon_start: float = 0.20,
    epsilon_end: float = 0.03,
    epsilon_decay_fraction: float = 0.80,
    learning_rate_schedule: str = "visit_decay",
    learning_rate_start: float = 0.25,
    learning_rate_min: float = 0.03,
    checkpoint_episodes: Optional[List[int]] = None,
    checkpoint_selection: str = "last",
    validation_seed: int = 500007,
    checkpoint_eval_runs: int = 5,
    slack_queue_basis: str = DEFAULT_SLACK_QUEUE_BASIS,
    slack_queue_weight: float = DEFAULT_SLACK_QUEUE_WEIGHT,
) -> SpecNetAgentBanditPolicy:
    if episodes <= 0:
        raise ValueError("training episodes must be positive")
    if checkpoint_selection not in {"last", "best_validation"}:
        raise ValueError(f"unknown checkpoint selection: {checkpoint_selection}")
    if checkpoint_eval_runs <= 0:
        raise ValueError("checkpoint evaluation runs must be positive")
    policy = SpecNetAgentBanditPolicy(
        seed=seed,
        train=True,
        epsilon=epsilon_start,
        learning_rate=learning_rate_start,
        name=policy_name,
        quality_weight=quality_weight,
        controller_variant=controller_variant,
        epsilon_schedule=epsilon_schedule,
        epsilon_end=epsilon_end,
        epsilon_decay_fraction=epsilon_decay_fraction,
        learning_rate_schedule=learning_rate_schedule,
        learning_rate_min=learning_rate_min,
        slack_queue_basis=slack_queue_basis,
        slack_queue_weight=slack_queue_weight,
    )
    requested_checkpoints = checkpoint_episodes or []
    checkpoints = sorted({episode for episode in requested_checkpoints if episode <= episodes} | {episodes})
    checkpoint_models: Dict[int, Dict[str, object]] = {}
    training_window: List[Dict[str, object]] = []
    window_start_episode = 1
    for episode in range(episodes):
        policy.set_training_progress(episode, episodes)
        load = loads[episode % len(loads)]
        workload_seed = seed + 10000 + episode
        specs = generate_workload(workload_seed, load, duration, max_workflows)
        sim = Simulator(
            specs,
            policy,
            load,
            workload_seed,
            duration,
            max_time,
            quality_weight=quality_weight,
            slack_queue_basis=slack_queue_basis,
            slack_queue_weight=slack_queue_weight,
        )
        training_window.append(sim.run())
        episode_number = episode + 1
        if episode_number in checkpoints:
            snapshot = policy.model_snapshot()
            checkpoint_models[episode_number] = snapshot
            total_updates = sum(sum(values.values()) for values in policy.counts.values())
            checkpoint_record = {
                "episode": episode_number,
                "epsilon": policy.epsilon,
                "total_updates": total_updates,
                "states_seen": len(policy.counts),
                "window_start_episode": window_start_episode,
                "window_metrics": summarize_training_window(training_window),
                **serialize_model_snapshot(snapshot),
            }
            policy.training_checkpoints.append(checkpoint_record)
            training_window = []
            window_start_episode = episode_number + 1

    selected_episode = episodes
    if checkpoint_selection == "best_validation":
        for checkpoint_record in policy.training_checkpoints:
            episode_number = int(checkpoint_record["episode"])
            checkpoint_record["validation"] = evaluate_training_checkpoint(
                policy,
                checkpoint_models[episode_number],
                loads,
                duration,
                max_workflows,
                max_time,
                validation_seed,
                checkpoint_eval_runs,
            )
        selected_record = max(
            policy.training_checkpoints,
            key=lambda record: float(record["validation"]["score"]),
        )
        selected_episode = int(selected_record["episode"])

    policy.restore_snapshot(checkpoint_models[selected_episode])
    policy.selected_checkpoint_episode = selected_episode
    policy.training_info = {
        "checkpoint_selection": checkpoint_selection,
        "requested_checkpoint_episodes": requested_checkpoints,
        "saved_checkpoint_episodes": checkpoints,
        "selected_checkpoint_episode": selected_episode,
        "validation_seed": validation_seed if checkpoint_selection == "best_validation" else None,
        "checkpoint_eval_runs": checkpoint_eval_runs if checkpoint_selection == "best_validation" else 0,
    }
    policy.set_evaluation_mode()
    return policy
