#!/usr/bin/env python3
"""Plot the multi-path quality-balance ablation and network comparisons."""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PLOTTING_DIR = SCRIPT_DIR.parent
REPO_ROOT = PLOTTING_DIR.parent
if str(PLOTTING_DIR) not in sys.path:
    sys.path.insert(0, str(PLOTTING_DIR))

import matplotlib.pyplot as plt

from plot_common import (
    LOAD_ORDER,
    load_positions,
    percent_axis,
    save_figure,
    set_load_axis,
    setup_style,
)


QUALITY_TARGET = 0.95
QUALITY_HARD_FLOOR = 0.90
ACTION_ORDER = ["full", "moderate", "conservative", "critical_only", "recovery"]
ACTION_LABEL = {
    "full": "Full",
    "moderate": "Moderate",
    "conservative": "Conservative",
    "critical_only": "Critical only",
    "recovery": "Recovery",
}
ACTION_COLOR = {
    "full": "#0ea5e9",
    "moderate": "#22c55e",
    "conservative": "#f59e0b",
    "critical_only": "#ef4444",
    "recovery": "#8b5cf6",
}

MECHANISM_ORDER = ["rule_off", "rule_on", "bandit_off", "bandit_on"]
MECHANISM_LABEL = {
    "rule_off": "Rule",
    "rule_on": "Rule + Guard",
    "bandit_off": "Bandit",
    "bandit_on": "Bandit + Guard",
}
MECHANISM_COLOR = {
    "rule_off": "#64748b",
    "rule_on": "#2563eb",
    "bandit_off": "#f59e0b",
    "bandit_on": "#059669",
}
MECHANISM_MARKER = {
    "rule_off": "o",
    "rule_on": "^",
    "bandit_off": "s",
    "bandit_on": "P",
}

NETWORK_ORDER = ["shared48", "fixed_paths", "borrowing_paths"]
NETWORK_LABEL = {
    "shared48": "Shared 1×48",
    "fixed_paths": "Fixed 3×16",
    "borrowing_paths": "Borrowing 3×16",
}
NETWORK_COLOR = {
    "shared48": "#7c3aed",
    "fixed_paths": "#2563eb",
    "borrowing_paths": "#059669",
}
NETWORK_MARKER = {
    "shared48": "s",
    "fixed_paths": "^",
    "borrowing_paths": "P",
}
NETWORK_DIR = {
    "shared48": "single48_guard_on",
    "fixed_paths": "fixed_guard_on",
    "borrowing_paths": "borrowing_guard_on",
}

METRICS = (
    "p99_latency",
    "avg_quality",
    "quality_violation_ratio",
    "wasted_speculative_bytes_per_workflow",
    "guard_override_ratio",
)
LOWER_IS_BETTER = {
    "p99_latency",
    "quality_violation_ratio",
    "wasted_speculative_bytes_per_workflow",
    "guard_override_ratio",
}


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"missing experiment file: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def is_specnet(policy: str) -> bool:
    return policy == "specnet_agent" or policy.startswith("specnet_agent_")


def mean_rows(
    rows: Sequence[Mapping[str, str]],
    label_key: str,
    label_value: str,
) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["load"])].append(row)
    missing = [load for load in LOAD_ORDER if not grouped[load]]
    if missing:
        raise ValueError(f"{label_value} has no rows for loads: {missing}")
    output: List[Dict[str, object]] = []
    for load in LOAD_ORDER:
        selected = grouped[load]
        item: Dict[str, object] = {
            label_key: label_value,
            "load": load,
            "observations": len(selected),
        }
        for metric in METRICS:
            item[metric] = sum(float(row[metric]) for row in selected) / len(selected)
        output.append(item)
    return output


def aggregate_mechanisms(input_root: Path) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    definitions = (
        ("rule_off", "borrowing_guard_off", lambda row: row["policy"] == "rule_balanced"),
        ("rule_on", "borrowing_guard_on", lambda row: row["policy"] == "rule_balanced"),
        ("bandit_off", "borrowing_guard_off", lambda row: is_specnet(row["policy"])),
        ("bandit_on", "borrowing_guard_on", lambda row: is_specnet(row["policy"])),
    )
    for mechanism, directory, predicate in definitions:
        rows = [
            row
            for row in read_csv(input_root / directory / "summary_by_run.csv")
            if predicate(row)
        ]
        output.extend(mean_rows(rows, "mechanism", mechanism))
    return output


def aggregate_networks(input_root: Path) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for network in NETWORK_ORDER:
        rows = [
            row
            for row in read_csv(
                input_root / NETWORK_DIR[network] / "summary_by_run.csv"
            )
            if is_specnet(row["policy"])
        ]
        output.extend(mean_rows(rows, "network", network))
    return output


def aggregate_guard_actions(input_root: Path) -> List[Dict[str, object]]:
    directory = input_root / "borrowing_guard_on"
    output: List[Dict[str, object]] = []
    for stage, filename in (
        ("raw", "raw_action_counts.csv"),
        ("safe", "action_counts.csv"),
    ):
        counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in read_csv(directory / filename):
            if is_specnet(row["policy"]):
                counts[row["load"]][row["action"]] += int(row["count"])
        for load in LOAD_ORDER:
            total = sum(counts[load].values())
            if total == 0:
                raise ValueError(f"{stage} actions missing for load: {load}")
            for action in ACTION_ORDER:
                output.append(
                    {
                        "stage": stage,
                        "load": load,
                        "action": action,
                        "count": counts[load][action],
                        "share": counts[load][action] / total,
                    }
                )
    return output


def aggregate_lambda(input_root: Path) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for network in NETWORK_ORDER:
        grouped: Dict[int, List[float]] = defaultdict(list)
        path = input_root / NETWORK_DIR[network] / "lambda_updates.csv"
        for row in read_csv(path):
            if row["updated"].lower() == "true":
                grouped[int(row["episode"])].append(float(row["lambda_after"]))
        for episode, values in sorted(grouped.items()):
            output.append(
                {
                    "network": network,
                    "episode": episode,
                    "lambda": sum(values) / len(values),
                    "train_seeds": len(values),
                }
            )
    return output


def percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def paired_comparison(
    left_rows: Sequence[Mapping[str, str]],
    right_rows: Sequence[Mapping[str, str]],
    comparison: str,
    left_label: str,
    right_label: str,
    rng: random.Random,
    bootstrap_samples: int = 10_000,
) -> List[Dict[str, object]]:
    key_fields = ("load", "policy", "train_seed", "run")
    left = {tuple(row.get(field, "") for field in key_fields): row for row in left_rows}
    right = {tuple(row.get(field, "") for field in key_fields): row for row in right_rows}
    if set(left) != set(right):
        raise ValueError(f"paired keys differ for {comparison}")
    output: List[Dict[str, object]] = []
    for load in LOAD_ORDER:
        keys = [key for key in sorted(left) if key[0] == load]
        for metric in METRICS:
            left_values = [float(left[key][metric]) for key in keys]
            right_values = [float(right[key][metric]) for key in keys]
            differences = [
                right_value - left_value
                for left_value, right_value in zip(left_values, right_values)
            ]
            bootstrap_means = []
            for _ in range(bootstrap_samples):
                bootstrap_means.append(
                    sum(differences[rng.randrange(len(differences))] for _ in differences)
                    / len(differences)
                )
            left_mean = sum(left_values) / len(left_values)
            right_mean = sum(right_values) / len(right_values)
            if metric in LOWER_IS_BETTER:
                wins = sum(right_value < left_value for left_value, right_value in zip(left_values, right_values))
            else:
                wins = sum(right_value > left_value for left_value, right_value in zip(left_values, right_values))
            output.append(
                {
                    "comparison": comparison,
                    "left": left_label,
                    "right": right_label,
                    "load": load,
                    "metric": metric,
                    "pairs": len(keys),
                    "left_mean": left_mean,
                    "right_mean": right_mean,
                    "absolute_change": right_mean - left_mean,
                    "relative_change_percent": (
                        100.0 * (right_mean - left_mean) / left_mean
                        if abs(left_mean) > 1e-12
                        else ""
                    ),
                    "ci95_low": percentile(bootstrap_means, 0.025),
                    "ci95_high": percentile(bootstrap_means, 0.975),
                    "right_win_rate": wins / len(keys),
                }
            )
    return output


def paired_evidence(input_root: Path) -> List[Dict[str, object]]:
    rng = random.Random(20260728)
    off = read_csv(input_root / "borrowing_guard_off" / "summary_by_run.csv")
    on = read_csv(input_root / "borrowing_guard_on" / "summary_by_run.csv")
    fixed = read_csv(input_root / "fixed_guard_on" / "summary_by_run.csv")
    shared = read_csv(input_root / "single48_guard_on" / "summary_by_run.csv")
    evidence: List[Dict[str, object]] = []
    for family, predicate in (
        ("Rule", lambda row: row["policy"] == "rule_balanced"),
        ("Bandit", lambda row: is_specnet(row["policy"])),
    ):
        evidence.extend(
            paired_comparison(
                [row for row in off if predicate(row)],
                [row for row in on if predicate(row)],
                f"{family}: Guard off -> on",
                "Guard off",
                "Guard on",
                rng,
            )
        )
    for comparison, left_rows, left_label in (
        ("Bandit + Guard: Fixed -> Borrowing", fixed, "Fixed 3x16"),
        ("Bandit + Guard: Shared48 -> Borrowing", shared, "Shared 1x48"),
    ):
        evidence.extend(
            paired_comparison(
                [row for row in left_rows if is_specnet(row["policy"])],
                [row for row in on if is_specnet(row["policy"])],
                comparison,
                left_label,
                "Borrowing 3x16",
                rng,
            )
        )
    return evidence


def lookup(
    rows: Sequence[Mapping[str, object]],
    group_key: str,
) -> Dict[str, Dict[str, Mapping[str, object]]]:
    return {
        group: {
            str(row["load"]): row
            for row in rows
            if row[group_key] == group
        }
        for group in {str(row[group_key]) for row in rows}
    }


def plot_mechanism_metric(
    rows: Sequence[Mapping[str, object]],
    metric: str,
    ylabel: str,
    title: str,
    basename: str,
    output_dir: str,
    dpi: int,
    percent: bool = False,
) -> None:
    data = lookup(rows, "mechanism")
    fig, ax = plt.subplots()
    xs = load_positions()
    for mechanism in MECHANISM_ORDER:
        multiplier = 100.0 if percent else 1.0
        ys = [
            multiplier * float(data[mechanism][load][metric])
            for load in LOAD_ORDER
        ]
        ax.plot(
            xs,
            ys,
            label=MECHANISM_LABEL[mechanism],
            color=MECHANISM_COLOR[mechanism],
            marker=MECHANISM_MARKER[mechanism],
        )
    set_load_axis(ax)
    if metric == "avg_quality":
        ax.axhline(QUALITY_TARGET, color="#111827", linestyle="--", label="Q target")
        ax.axhline(
            QUALITY_HARD_FLOOR,
            color="#94a3b8",
            linestyle=":",
            label="Q hard (per workflow)",
        )
        ax.set_ylim(0.89, 0.97)
    else:
        ax.set_ylim(bottom=0)
    if percent:
        percent_axis(ax)
    ax.set_xlabel("Offered load")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)
    save_figure(fig, output_dir, basename, dpi)
    plt.close(fig)


def plot_network_summary(
    rows: Sequence[Mapping[str, object]],
    output_dir: str,
    dpi: int,
) -> None:
    data = lookup(rows, "network")
    fig, axes = plt.subplots(1, 3, figsize=(11.4, 3.7))
    xs = load_positions()
    panels = (
        ("p99_latency", "p99 latency", None),
        ("avg_quality", "Average quality", (0.89, 0.97)),
        ("wasted_speculative_bytes_per_workflow", "True waste / workflow", None),
    )
    for ax, (metric, ylabel, ylim) in zip(axes, panels):
        for network in NETWORK_ORDER:
            ax.plot(
                xs,
                [float(data[network][load][metric]) for load in LOAD_ORDER],
                label=NETWORK_LABEL[network],
                color=NETWORK_COLOR[network],
                marker=NETWORK_MARKER[network],
            )
        set_load_axis(ax)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Offered load")
        if ylim:
            ax.set_ylim(*ylim)
            ax.axhline(QUALITY_TARGET, color="#111827", linestyle="--")
        else:
            ax.set_ylim(bottom=0)
    fig.suptitle("Bandit + Guard across capacity models", fontweight="bold")
    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=3,
        frameon=False,
    )
    fig.subplots_adjust(bottom=0.27, wspace=0.30)
    save_figure(fig, output_dir, "fig_quality_balance_specnet_networks", dpi)
    plt.close(fig)


def plot_guard_actions(
    rows: Sequence[Mapping[str, object]],
    output_dir: str,
    dpi: int,
) -> None:
    shares = {
        (str(row["stage"]), str(row["load"]), str(row["action"])): 100.0
        * float(row["share"])
        for row in rows
    }
    fig, axes = plt.subplots(1, len(LOAD_ORDER), figsize=(10.8, 3.8), sharey=True)
    for ax, load in zip(axes, LOAD_ORDER):
        bottoms = [0.0, 0.0]
        for action in ACTION_ORDER:
            values = [
                shares[(stage, load, action)]
                for stage in ("raw", "safe")
            ]
            ax.bar(
                [0, 1],
                values,
                bottom=bottoms,
                color=ACTION_COLOR[action],
                label=ACTION_LABEL[action],
                width=0.60,
            )
            bottoms = [bottom + value for bottom, value in zip(bottoms, values)]
        ax.set_xticks([0, 1], ["Raw", "After guard"])
        ax.set_title(load.capitalize())
        ax.set_ylim(0, 100)
        percent_axis(ax)
    axes[0].set_ylabel("Action share")
    fig.suptitle("SpecNet action distribution before and after Safety Guard", fontweight="bold")
    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=5,
        frameon=False,
    )
    fig.subplots_adjust(bottom=0.27, wspace=0.16)
    save_figure(fig, output_dir, "fig_quality_balance_guard_actions", dpi)
    plt.close(fig)


def plot_lambda(
    rows: Sequence[Mapping[str, object]],
    output_dir: str,
    dpi: int,
) -> None:
    fig, ax = plt.subplots()
    for network in NETWORK_ORDER:
        selected = [row for row in rows if row["network"] == network]
        ax.plot(
            [int(row["episode"]) for row in selected],
            [float(row["lambda"]) for row in selected],
            label=NETWORK_LABEL[network],
            color=NETWORK_COLOR[network],
            marker=NETWORK_MARKER[network],
            markevery=3,
        )
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Training episode")
    ax.set_ylabel("Quality multiplier λ")
    ax.set_title("Window-level quality constraint response")
    ax.legend(loc="upper left")
    save_figure(fig, output_dir, "fig_quality_balance_lambda", dpi)
    plt.close(fig)


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot quality-balance ablations and capacity-model comparisons."
    )
    parser.add_argument(
        "--input-root",
        default=str(REPO_ROOT / "outputs" / "quality_balance_20260728"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(SCRIPT_DIR / "figures_quality_balance_20260728"),
    )
    parser.add_argument("--dpi", type=int, default=300)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = Path(args.input_root).resolve()
    output_dir = str(Path(args.output_dir).resolve())
    setup_style()

    mechanisms = aggregate_mechanisms(input_root)
    networks = aggregate_networks(input_root)
    actions = aggregate_guard_actions(input_root)
    lambdas = aggregate_lambda(input_root)
    evidence = paired_evidence(input_root)

    write_csv(Path(output_dir) / "quality_balance_mechanisms.csv", mechanisms)
    write_csv(Path(output_dir) / "quality_balance_networks.csv", networks)
    write_csv(Path(output_dir) / "quality_balance_actions.csv", actions)
    write_csv(Path(output_dir) / "quality_balance_lambda.csv", lambdas)
    write_csv(Path(output_dir) / "quality_balance_paired_evidence.csv", evidence)

    plot_mechanism_metric(
        mechanisms,
        "avg_quality",
        "Average realized quality",
        "Rule/Bandit × Safety Guard quality",
        "fig_quality_balance_2x2_quality",
        output_dir,
        args.dpi,
    )
    plot_mechanism_metric(
        mechanisms,
        "p99_latency",
        "p99 workflow latency",
        "Rule/Bandit × Safety Guard tail latency",
        "fig_quality_balance_2x2_p99",
        output_dir,
        args.dpi,
    )
    plot_mechanism_metric(
        mechanisms,
        "wasted_speculative_bytes_per_workflow",
        "True wasted speculative bytes / workflow",
        "Rule/Bandit × Safety Guard true waste",
        "fig_quality_balance_2x2_waste",
        output_dir,
        args.dpi,
    )
    plot_mechanism_metric(
        mechanisms,
        "quality_violation_ratio",
        "Workflow quality violation ratio",
        "Realized Q < 0.90 remains observable",
        "fig_quality_balance_2x2_violations",
        output_dir,
        args.dpi,
        percent=True,
    )
    plot_network_summary(networks, output_dir, args.dpi)
    plot_guard_actions(actions, output_dir, args.dpi)
    plot_lambda(lambdas, output_dir, args.dpi)
    print(f"Wrote quality-balance figures to: {output_dir}")


if __name__ == "__main__":
    main()
