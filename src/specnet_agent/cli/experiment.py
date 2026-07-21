"""Command-line orchestration for SpecNet-Agent experiments."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..config import (
    CONTROLLER_VARIANT_FEATURES, DEFAULT_QUALITY_WEIGHTS, DEFAULT_SLACK_QUEUE_BASIS,
    DEFAULT_SLACK_QUEUE_WEIGHT, LOAD_CONFIG, SLACK_ESTIMATORS, SLACK_LOOSE_THRESHOLD,
    SLACK_QUEUE_BASES, SLACK_TIGHT_THRESHOLD,
)
from ..outputs import aggregate_summaries, write_csv, write_json
from ..policies import SpecNetAgentBanditPolicy, make_policy
from ..simulator import Simulator
from ..training import (
    parse_checkpoint_episodes, parse_controller_variants, parse_quality_weights,
    parse_train_seeds, train_specnet_agent, quality_weight_policy_name,
)
from ..workload import generate_workload


def _build_parser() -> argparse.ArgumentParser:
    default_quality_weights_text = ",".join(str(weight) for weight in DEFAULT_QUALITY_WEIGHTS)
    parser = argparse.ArgumentParser(description="Run SpecNet-Agent simulation experiments.")
    parser.add_argument(
        "--config",
        default="",
        help="Optional schema_version=1 JSON configuration. Explicit CLI arguments take precedence.",
    )
    parser.add_argument("--output-dir", default="specnet_agent_experiments/results", help="Directory for CSV/JSON outputs.")
    parser.add_argument("--seed", type=int, default=7, help="Base random seed used when train/eval seeds are not set.")
    parser.add_argument(
        "--train-seed",
        type=int,
        default=None,
        help="Training seed for SpecNet-Agent. Defaults to --seed.",
    )
    parser.add_argument(
        "--train-seeds",
        default="",
        help="Comma-separated training seeds. When set, trains one SpecNet-Agent per quality weight and seed.",
    )
    parser.add_argument(
        "--eval-seed",
        type=int,
        default=None,
        help="Evaluation workload seed base. Defaults to --seed.",
    )
    parser.add_argument("--train-episodes", type=int, default=45, help="Training episodes for SpecNet-Agent bandit.")
    parser.add_argument(
        "--epsilon-schedule",
        choices=("fixed", "linear"),
        default="linear",
        help="Exploration schedule during training. Use fixed with 0.18 to reproduce the legacy trainer.",
    )
    parser.add_argument("--epsilon-start", type=float, default=0.20, help="Initial training exploration rate.")
    parser.add_argument("--epsilon-end", type=float, default=0.03, help="Final exploration rate for linear decay.")
    parser.add_argument(
        "--epsilon-decay-fraction",
        type=float,
        default=0.80,
        help="Fraction of training over which linear epsilon decay is completed.",
    )
    parser.add_argument(
        "--learning-rate-schedule",
        choices=("fixed", "visit_decay"),
        default="visit_decay",
        help="Q-value learning-rate schedule.",
    )
    parser.add_argument("--learning-rate-start", type=float, default=0.25, help="Initial Q-value learning rate.")
    parser.add_argument(
        "--learning-rate-min",
        type=float,
        default=0.03,
        help="Minimum Q-value learning rate for visit-count decay.",
    )
    parser.add_argument(
        "--checkpoint-episodes",
        default="30,45,60,75,90",
        help="Comma-separated 1-based training episodes at which to record model snapshots.",
    )
    parser.add_argument(
        "--checkpoint-selection",
        choices=("last", "best_validation"),
        default="last",
        help="Use the final model or select a recorded checkpoint on held-out validation workloads.",
    )
    parser.add_argument(
        "--validation-seed",
        type=int,
        default=None,
        help="Held-out checkpoint-validation seed. Defaults to eval seed + 500000 and never reuses eval workloads.",
    )
    parser.add_argument(
        "--checkpoint-eval-runs",
        type=int,
        default=5,
        help="Validation runs per load and checkpoint when best_validation selection is enabled.",
    )
    parser.add_argument("--eval-runs", type=int, default=5, help="Evaluation runs per load and policy.")
    parser.add_argument("--duration", type=int, default=2600, help="Workflow arrival duration in simulator time units.")
    parser.add_argument("--max-time", type=int, default=7000, help="Maximum simulator time per run.")
    parser.add_argument("--max-workflows", type=int, default=120, help="Maximum workflows per run.")
    parser.add_argument(
        "--quality-weight",
        type=float,
        default=1.60,
        help="Reward penalty weight for quality loss when training/evaluating SpecNet-Agent.",
    )
    parser.add_argument(
        "--quality-weights",
        default="",
        help=(
            "Comma-separated quality-loss reward weights. When set, the simulator trains "
            f"one SpecNet-Agent per weight, e.g. {default_quality_weights_text}."
        ),
    )
    parser.add_argument(
        "--controller-variants",
        default="full",
        help=(
            "Comma-separated SpecNet controller state variants. Valid values: "
            f"{','.join(CONTROLLER_VARIANT_FEATURES)}."
        ),
    )
    parser.add_argument(
        "--slack-queue-basis",
        choices=SLACK_QUEUE_BASES,
        default=DEFAULT_SLACK_QUEUE_BASIS,
        help=(
            "Queue-work estimator used by deadline Slack. 'total' preserves Slack v2; "
            "'policy_weighted' enables the role-aware Slack v2.1 candidate."
        ),
    )
    parser.add_argument(
        "--slack-queue-weight",
        type=float,
        default=DEFAULT_SLACK_QUEUE_WEIGHT,
        help="Non-negative multiplier applied to the selected Slack queue-work estimate.",
    )
    parser.add_argument(
        "--loads",
        default="light,medium,heavy",
        help="Comma-separated loads to evaluate: light,medium,heavy.",
    )
    return parser


def _validate_config_value(action: argparse.Action, value: object) -> None:
    if value is None:
        if action.default is None:
            return
        raise SystemExit(f"Config key '{action.dest}' may not be null.")
    if action.type is int and (not isinstance(value, int) or isinstance(value, bool)):
        raise SystemExit(f"Config key '{action.dest}' must be an integer.")
    if action.type is float and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise SystemExit(f"Config key '{action.dest}' must be numeric.")
    if action.type is None and isinstance(action.default, str) and not isinstance(value, str):
        raise SystemExit(f"Config key '{action.dest}' must be a string.")
    if action.choices is not None and value not in action.choices:
        raise SystemExit(
            f"Invalid value for config key '{action.dest}': {value!r}; "
            f"expected one of {list(action.choices)!r}."
        )


def _config_defaults(parser: argparse.ArgumentParser, path: str) -> Dict[str, object]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read config '{path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Experiment config must be a JSON object.")
    schema_version = payload.pop("schema_version", None)
    if schema_version != 1:
        raise SystemExit("Experiment config requires schema_version=1.")
    actions = {action.dest: action for action in parser._actions if action.dest != "help"}
    unknown = sorted(set(payload) - set(actions))
    if unknown:
        raise SystemExit(f"Unknown experiment config keys: {unknown}")
    for key, value in payload.items():
        _validate_config_value(actions[key], value)
    return payload


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default="")
    pre_args, _ = pre_parser.parse_known_args(argv)
    parser = _build_parser()
    if pre_args.config:
        parser.set_defaults(**_config_defaults(parser, pre_args.config))
    return parser.parse_args(argv)


def _code_version() -> Tuple[str, str]:
    try:
        package_version = metadata.version("specnet-agent")
    except metadata.PackageNotFoundError:
        package_version = "0.1.0"
    repository = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return package_version, commit


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    loads = [item.strip() for item in args.loads.split(",") if item.strip()]
    quality_weights = parse_quality_weights(args)
    train_seeds = parse_train_seeds(args)
    controller_variants = parse_controller_variants(args)
    checkpoint_episodes = parse_checkpoint_episodes(args.checkpoint_episodes)
    eval_seed = args.eval_seed if args.eval_seed is not None else args.seed
    validation_seed = args.validation_seed if args.validation_seed is not None else eval_seed + 500000
    multi_weight = len(quality_weights) > 1 or bool(args.quality_weights)
    multi_train_seed = len(train_seeds) > 1 or bool(args.train_seeds)
    multi_controller_variant = len(controller_variants) > 1
    invalid_loads = [load for load in loads if load not in LOAD_CONFIG]
    if invalid_loads:
        raise SystemExit(f"Invalid loads: {invalid_loads}")
    if args.slack_queue_weight < 0.0:
        raise SystemExit("--slack-queue-weight must be non-negative")

    os.makedirs(args.output_dir, exist_ok=True)
    trained_policies: Dict[str, Tuple[float, int, str, SpecNetAgentBanditPolicy]] = {}
    trained_agent_rows: List[Dict[str, object]] = []
    for train_seed in train_seeds:
        for quality_weight in quality_weights:
            for controller_variant in controller_variants:
                policy_name = quality_weight_policy_name(
                    quality_weight,
                    multi_weight,
                    train_seed=train_seed,
                    multi_train_seed=multi_train_seed,
                    controller_variant=controller_variant,
                    multi_controller_variant=multi_controller_variant,
                )
                policy = train_specnet_agent(
                    episodes=args.train_episodes,
                    loads=loads,
                    duration=args.duration,
                    max_workflows=args.max_workflows,
                    max_time=args.max_time,
                    seed=train_seed,
                    quality_weight=quality_weight,
                    policy_name=policy_name,
                    controller_variant=controller_variant,
                    epsilon_schedule=args.epsilon_schedule,
                    epsilon_start=args.epsilon_start,
                    epsilon_end=args.epsilon_end,
                    epsilon_decay_fraction=args.epsilon_decay_fraction,
                    learning_rate_schedule=args.learning_rate_schedule,
                    learning_rate_start=args.learning_rate_start,
                    learning_rate_min=args.learning_rate_min,
                    checkpoint_episodes=checkpoint_episodes,
                    checkpoint_selection=args.checkpoint_selection,
                    validation_seed=validation_seed,
                    checkpoint_eval_runs=args.checkpoint_eval_runs,
                    slack_queue_basis=args.slack_queue_basis,
                    slack_queue_weight=args.slack_queue_weight,
                )

                state_features = ",".join(CONTROLLER_VARIANT_FEATURES[controller_variant])
                training_info = {
                    "policy": policy_name,
                    "controller_variant": controller_variant,
                    "state_features": state_features,
                    "quality_weight": quality_weight,
                    "slack_queue_basis": args.slack_queue_basis,
                    "slack_queue_weight": args.slack_queue_weight,
                    "train_seed": train_seed,
                    "eval_seed": eval_seed,
                    "train_episodes": args.train_episodes,
                    "training_loads": ",".join(loads),
                    "duration": args.duration,
                    "max_workflows": args.max_workflows,
                    "max_time": args.max_time,
                    "epsilon_schedule": args.epsilon_schedule,
                    "epsilon_start": args.epsilon_start,
                    "epsilon_end": args.epsilon_end,
                    "epsilon_decay_fraction": args.epsilon_decay_fraction,
                    "learning_rate_schedule": args.learning_rate_schedule,
                    "learning_rate_start": args.learning_rate_start,
                    "learning_rate_min": args.learning_rate_min,
                    "checkpoint_selection": args.checkpoint_selection,
                    "saved_checkpoint_episodes": ",".join(
                        str(record["episode"]) for record in policy.training_checkpoints
                    ),
                    "selected_checkpoint_episode": policy.selected_checkpoint_episode,
                    "validation_seed": validation_seed if args.checkpoint_selection == "best_validation" else "",
                    "checkpoint_eval_runs": args.checkpoint_eval_runs
                    if args.checkpoint_selection == "best_validation"
                    else 0,
                }
                policy.training_info = {
                    **policy.training_info,
                    **training_info,
                    "state_features": list(policy.state_features),
                }
                trained_policies[policy_name] = (quality_weight, train_seed, controller_variant, policy)
                trained_agent_rows.append(training_info)

    policies = [
        "fifo",
        "static_priority",
        "critical_path_only",
        "rule_aggressive",
        "rule_balanced",
        "rule_quality_preserving",
    ] + list(trained_policies.keys())

    summaries: List[Dict[str, object]] = []
    workflow_rows: List[Dict[str, object]] = []
    action_rows: List[Dict[str, object]] = []

    for load in loads:
        for run_index in range(args.eval_runs):
            workload_seed = eval_seed + 20000 + 1000 * run_index + 17 * list(LOAD_CONFIG).index(load)
            specs = generate_workload(workload_seed, load, args.duration, args.max_workflows)
            for policy_name in policies:
                if policy_name in trained_policies:
                    # Reuse learned Q values but reset per-run counters.
                    quality_weight, train_seed, controller_variant, policy = trained_policies[policy_name]
                    state_features = ",".join(policy.state_features)
                    policy.reset_for_run()
                else:
                    quality_weight = ""
                    train_seed = ""
                    controller_variant = ""
                    state_features = ""
                    policy = make_policy(policy_name, seed=eval_seed + run_index)
                sim = Simulator(
                    specs,
                    policy,
                    load,
                    workload_seed,
                    args.duration,
                    args.max_time,
                    quality_weight=float(quality_weight) if quality_weight != "" else args.quality_weight,
                    slack_queue_basis=args.slack_queue_basis,
                    slack_queue_weight=args.slack_queue_weight,
                )
                summary = sim.run()
                summary["policy"] = policy_name
                summary["controller_variant"] = controller_variant
                summary["state_features"] = state_features
                summary["quality_weight"] = quality_weight
                summary["slack_queue_basis"] = args.slack_queue_basis
                summary["slack_queue_weight"] = args.slack_queue_weight
                summary["train_seed"] = train_seed
                summary["eval_seed"] = eval_seed
                summary["run"] = run_index
                summaries.append({k: v for k, v in summary.items() if k not in ("workflow_records", "action_counts")})
                for row in summary["workflow_records"]:
                    row_with_context = dict(row)
                    row_with_context.update(
                        {
                            "load": load,
                            "policy": policy_name,
                            "controller_variant": controller_variant,
                            "state_features": state_features,
                            "quality_weight": quality_weight,
                            "slack_queue_basis": args.slack_queue_basis,
                            "slack_queue_weight": args.slack_queue_weight,
                            "train_seed": train_seed,
                            "eval_seed": eval_seed,
                            "run": run_index,
                            "seed": workload_seed,
                        }
                    )
                    workflow_rows.append(row_with_context)
                for action, count in summary["action_counts"].items():
                    action_rows.append(
                        {
                            "load": load,
                            "policy": policy_name,
                            "controller_variant": controller_variant,
                            "state_features": state_features,
                            "quality_weight": quality_weight,
                            "slack_queue_basis": args.slack_queue_basis,
                            "slack_queue_weight": args.slack_queue_weight,
                            "train_seed": train_seed,
                            "eval_seed": eval_seed,
                            "run": run_index,
                            "action": action,
                            "count": count,
                        }
                    )

    aggregate_rows = aggregate_summaries(summaries)
    write_csv(os.path.join(args.output_dir, "summary_by_run.csv"), summaries)
    write_csv(os.path.join(args.output_dir, "summary_aggregate.csv"), aggregate_rows)
    write_csv(os.path.join(args.output_dir, "workflow_results.csv"), workflow_rows)
    write_csv(os.path.join(args.output_dir, "action_counts.csv"), action_rows)
    write_csv(os.path.join(args.output_dir, "trained_agents.csv"), trained_agent_rows)
    write_json(
        os.path.join(args.output_dir, "specnet_agent_model.json"),
        {
            "slack_estimator": SLACK_ESTIMATORS[args.slack_queue_basis],
            "slack_queue_basis": args.slack_queue_basis,
            "slack_queue_weight": args.slack_queue_weight,
            "slack_thresholds": {
                "tight_below": SLACK_TIGHT_THRESHOLD,
                "loose_at_or_above": SLACK_LOOSE_THRESHOLD,
            },
            "controller_variants": controller_variants,
            "quality_weights": quality_weights,
            "train_seeds": train_seeds,
            "eval_seed": eval_seed,
            "train_episodes": args.train_episodes,
            "training_schedule": {
                "epsilon_schedule": args.epsilon_schedule,
                "epsilon_start": args.epsilon_start,
                "epsilon_end": args.epsilon_end,
                "epsilon_decay_fraction": args.epsilon_decay_fraction,
                "learning_rate_schedule": args.learning_rate_schedule,
                "learning_rate_start": args.learning_rate_start,
                "learning_rate_min": args.learning_rate_min,
            },
            "checkpointing": {
                "requested_episodes": checkpoint_episodes,
                "selection": args.checkpoint_selection,
                "validation_seed": validation_seed if args.checkpoint_selection == "best_validation" else None,
                "validation_runs_per_load": args.checkpoint_eval_runs
                if args.checkpoint_selection == "best_validation"
                else 0,
            },
            "loads": loads,
            "policies": {
                policy_name: {
                    "controller_variant": controller_variant,
                    "state_features": list(policy.state_features),
                    "quality_weight": quality_weight,
                    "train_seed": train_seed,
                    "eval_seed": eval_seed,
                    "model": policy.metadata(),
                }
                for policy_name, (quality_weight, train_seed, controller_variant, policy) in trained_policies.items()
            },
        },
    )
    package_version, commit = _code_version()
    write_json(
        os.path.join(args.output_dir, "run_manifest.json"),
        {
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "package_version": package_version,
            "git_commit": commit,
            "python_version": sys.version.split()[0],
            "entrypoint": "specnet-run",
            "resolved_config": vars(args),
            "seed_matrix": {
                "train_seeds": train_seeds,
                "eval_seed": eval_seed,
                "validation_seed": validation_seed,
            },
            "output_files": [
                "summary_by_run.csv",
                "summary_aggregate.csv",
                "workflow_results.csv",
                "action_counts.csv",
                "trained_agents.csv",
                "specnet_agent_model.json",
                "run_manifest.json",
            ],
        },
    )

    print("Wrote results to:", os.path.abspath(args.output_dir))
    print()
    print("Aggregate summary:")
    for row in aggregate_rows:
        print(
            f"{row['load']:>6} | {row['policy']:<19} "
            f"p99={row['p99_latency']:.2f} "
            f"miss={row['deadline_miss_ratio']:.3f} "
            f"waste={row['wasted_speculative_bytes_per_workflow']:.2f} "
            f"quality={row['avg_quality']:.3f}"
        )


if __name__ == "__main__":
    main()
