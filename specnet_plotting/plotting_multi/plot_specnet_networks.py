#!/usr/bin/env python3
"""Plot SpecNet-Agent metrics across bottleneck and multi-path models."""

from __future__ import annotations

import argparse
import csv
import os
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


NETWORK_ORDER = ["shared_16", "shared_48", "fixed_paths", "borrowing_paths"]
NETWORK_LABEL = {
    "shared_16": "Shared 1×16",
    "shared_48": "Shared 1×48",
    "fixed_paths": "Fixed 3×16",
    "borrowing_paths": "Borrowing 3×16",
}
NETWORK_TICK_LABEL = {
    "shared_16": "Shared\n1×16",
    "shared_48": "Shared\n1×48",
    "fixed_paths": "Fixed\n3×16",
    "borrowing_paths": "Borrowing\n3×16",
}
NETWORK_COLOR = {
    "shared_16": "#64748b",
    "shared_48": "#7c3aed",
    "fixed_paths": "#2563eb",
    "borrowing_paths": "#059669",
}
NETWORK_MARKER = {
    "shared_16": "o",
    "shared_48": "s",
    "fixed_paths": "^",
    "borrowing_paths": "P",
}
NETWORK_RELATIVE_DIR = {
    "shared_16": Path("single_bottleneck"),
    "shared_48": Path("rerun_20260722") / "single_bottleneck_48",
    "fixed_paths": Path("rerun_20260722") / "service_paths_fixed",
    "borrowing_paths": Path("rerun_20260722") / "service_paths_borrowing",
}

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

SUMMARY_METRICS = (
    "mean_latency",
    "p99_latency",
    "deadline_miss_ratio",
    "wasted_speculative_bytes_per_workflow",
    "avg_quality",
)


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"missing experiment file: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def is_specnet(policy: str) -> bool:
    return policy == "specnet_agent" or policy.startswith("specnet_agent_")


def model_directories(input_root: Path) -> Dict[str, Path]:
    return {model: input_root / NETWORK_RELATIVE_DIR[model] for model in NETWORK_ORDER}


def aggregate_summary(input_root: Path) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for model, directory in model_directories(input_root).items():
        grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for row in read_csv(directory / "summary_by_run.csv"):
            if is_specnet(row["policy"]):
                grouped[row["load"]].append(row)
        missing = [load for load in LOAD_ORDER if not grouped[load]]
        if missing:
            raise ValueError(f"{model} has no SpecNet rows for loads: {missing}")
        for load in LOAD_ORDER:
            row: Dict[str, object] = {
                "network_model": model,
                "network_label": NETWORK_LABEL[model],
                "load": load,
                "observations": len(grouped[load]),
            }
            for metric in SUMMARY_METRICS:
                row[metric] = sum(float(item[metric]) for item in grouped[load]) / len(grouped[load])
            output.append(row)
    return output


def aggregate_actions(input_root: Path) -> List[Dict[str, object]]:
    output: List[Dict[str, object]] = []
    for model, directory in model_directories(input_root).items():
        counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in read_csv(directory / "action_counts.csv"):
            if is_specnet(row["policy"]):
                counts[row["load"]][row["action"]] += int(row["count"])
        for load in LOAD_ORDER:
            total = sum(counts[load].values())
            if total <= 0:
                raise ValueError(f"{model} has no SpecNet actions for load: {load}")
            for action in ACTION_ORDER:
                count = counts[load][action]
                output.append(
                    {
                        "network_model": model,
                        "network_label": NETWORK_LABEL[model],
                        "load": load,
                        "action": action,
                        "count": count,
                        "share": count / total,
                    }
                )
    return output


def workflow_latencies(input_root: Path, load: str) -> Dict[str, List[float]]:
    output: Dict[str, List[float]] = {}
    for model, directory in model_directories(input_root).items():
        values = [
            float(row["latency"])
            for row in read_csv(directory / "workflow_results.csv")
            if row["load"] == load and is_specnet(row["policy"])
        ]
        if not values:
            raise ValueError(f"{model} has no SpecNet workflow rows for load: {load}")
        output[model] = sorted(values)
    return output


def summary_lookup(rows: Sequence[Mapping[str, object]]) -> Dict[str, Dict[str, Mapping[str, object]]]:
    return {
        model: {
            str(row["load"]): row
            for row in rows
            if row["network_model"] == model
        }
        for model in NETWORK_ORDER
    }


def add_network_legend(ax, ncol: int = 2) -> None:
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=ncol)


def plot_metric_lines(
    rows: Sequence[Mapping[str, object]],
    metric: str,
    ylabel: str,
    title: str,
    basename: str,
    output_dir: str,
    dpi: int,
    log_scale: bool = False,
    percent: bool = False,
) -> None:
    data = summary_lookup(rows)
    fig, ax = plt.subplots()
    xs = load_positions()
    for model in NETWORK_ORDER:
        multiplier = 100.0 if percent else 1.0
        ys = [multiplier * float(data[model][load][metric]) for load in LOAD_ORDER]
        ax.plot(
            xs,
            ys,
            label=NETWORK_LABEL[model],
            color=NETWORK_COLOR[model],
            marker=NETWORK_MARKER[model],
        )
    set_load_axis(ax)
    if log_scale:
        ax.set_yscale("log")
    if percent:
        percent_axis(ax)
    ax.set_ylim(bottom=0 if not log_scale else None)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Offered load")
    ax.set_title(title)
    add_network_legend(ax)
    save_figure(fig, output_dir, basename, dpi)
    plt.close(fig)


def plot_waste_bars(
    rows: Sequence[Mapping[str, object]], output_dir: str, dpi: int
) -> None:
    data = summary_lookup(rows)
    fig, ax = plt.subplots()
    xs = load_positions()
    width = 0.19
    offsets = [(-1.5 + index) * width for index in range(len(NETWORK_ORDER))]
    for offset, model in zip(offsets, NETWORK_ORDER):
        ys = [
            float(data[model][load]["wasted_speculative_bytes_per_workflow"])
            for load in LOAD_ORDER
        ]
        ax.bar(
            [x + offset for x in xs],
            ys,
            width=width * 0.92,
            label=NETWORK_LABEL[model],
            color=NETWORK_COLOR[model],
        )
    set_load_axis(ax)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Wasted speculative bytes per workflow")
    ax.set_xlabel("Offered load")
    ax.set_title("SpecNet-Agent speculative traffic by network model")
    add_network_legend(ax)
    save_figure(fig, output_dir, "fig_specnet_waste_by_network", dpi)
    plt.close(fig)


def plot_quality_tradeoff(
    rows: Sequence[Mapping[str, object]], load: str, output_dir: str, dpi: int
) -> None:
    selected = {
        str(row["network_model"]): row for row in rows if row["load"] == load
    }
    fig, ax = plt.subplots()
    for model in NETWORK_ORDER:
        row = selected[model]
        x = float(row["p99_latency"])
        y = float(row["avg_quality"])
        ax.scatter(
            [x],
            [y],
            s=120 if model == "borrowing_paths" else 82,
            color=NETWORK_COLOR[model],
            marker=NETWORK_MARKER[model],
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
        )
        ax.annotate(
            NETWORK_LABEL[model],
            (x, y),
            xytext=(7, 6),
            textcoords="offset points",
            fontsize=8.8,
        )
    ax.set_xscale("log")
    qualities = [float(row["avg_quality"]) for row in selected.values()]
    ax.set_ylim(max(0.0, min(qualities) - 0.03), min(1.02, max(qualities) + 0.03))
    ax.set_xlabel("p99 workflow latency (time units, log scale)")
    ax.set_ylabel("Average quality proxy")
    ax.set_title(f"SpecNet-Agent latency-quality tradeoff ({load} load)")
    save_figure(fig, output_dir, f"fig_specnet_quality_tradeoff_{load}", dpi)
    plt.close(fig)


def plot_action_mix(
    rows: Sequence[Mapping[str, object]], output_dir: str, dpi: int
) -> None:
    shares = {
        (str(row["network_model"]), str(row["load"]), str(row["action"])): 100.0
        * float(row["share"])
        for row in rows
    }
    fig, axes = plt.subplots(1, len(LOAD_ORDER), figsize=(11.2, 3.9), sharey=True)
    xs = list(range(len(NETWORK_ORDER)))
    for ax, load in zip(axes, LOAD_ORDER):
        bottoms = [0.0] * len(NETWORK_ORDER)
        for action in ACTION_ORDER:
            values = [shares[(model, load, action)] for model in NETWORK_ORDER]
            ax.bar(
                xs,
                values,
                bottom=bottoms,
                label=ACTION_LABEL[action],
                color=ACTION_COLOR[action],
                width=0.62,
            )
            bottoms = [bottom + value for bottom, value in zip(bottoms, values)]
        ax.set_xticks(xs)
        ax.set_xticklabels([NETWORK_TICK_LABEL[model] for model in NETWORK_ORDER], fontsize=8)
        ax.set_title(load.capitalize())
        ax.set_ylim(0, 100)
        percent_axis(ax)
    axes[0].set_ylabel("Action share")
    fig.suptitle("SpecNet-Agent action mix across network models", fontweight="bold")
    handles, labels = axes[-1].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, -0.08), ncol=5, frameon=False)
    fig.subplots_adjust(bottom=0.28, wspace=0.12)
    save_figure(fig, output_dir, "fig_specnet_action_mix_by_network", dpi)
    plt.close(fig)


def plot_latency_cdf(
    values_by_model: Mapping[str, Sequence[float]], load: str, output_dir: str, dpi: int
) -> None:
    fig, ax = plt.subplots()
    for model in NETWORK_ORDER:
        values = list(values_by_model[model])
        count = len(values)
        ys = [(index + 1) / count for index in range(count)]
        ax.plot(
            values,
            ys,
            label=NETWORK_LABEL[model],
            color=NETWORK_COLOR[model],
            marker=NETWORK_MARKER[model],
            markevery=max(1, count // 12),
        )
    ax.set_xscale("log")
    ax.set_ylim(0, 1.01)
    ax.set_xlabel("Workflow latency (time units, log scale)")
    ax.set_ylabel("CDF")
    ax.set_title(f"SpecNet-Agent latency distribution ({load} load)")
    add_network_legend(ax)
    save_figure(fig, output_dir, f"fig_specnet_latency_cdf_{load}", dpi)
    plt.close(fig)


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    default_input = REPO_ROOT / "outputs" / "single_vs_service_paths_20260721"
    default_output = SCRIPT_DIR / "figures"
    parser = argparse.ArgumentParser(
        description="Plot SpecNet-Agent results across bottleneck and multi-path models."
    )
    parser.add_argument("--input-root", default=str(default_input))
    parser.add_argument("--output-dir", default=str(default_output))
    parser.add_argument("--tradeoff-load", choices=LOAD_ORDER, default="heavy")
    parser.add_argument("--cdf-load", choices=LOAD_ORDER, default="heavy")
    parser.add_argument("--dpi", type=int, default=300)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = Path(args.input_root).resolve()
    output_dir = str(Path(args.output_dir).resolve())
    setup_style()
    summaries = aggregate_summary(input_root)
    actions = aggregate_actions(input_root)
    latencies = workflow_latencies(input_root, args.cdf_load)

    write_csv(Path(output_dir) / "specnet_network_summary.csv", summaries)
    write_csv(Path(output_dir) / "specnet_action_share.csv", actions)
    plot_metric_lines(
        summaries,
        "p99_latency",
        "p99 workflow latency (time units, log scale)",
        "SpecNet-Agent tail latency across network models",
        "fig_specnet_p99_by_network",
        output_dir,
        args.dpi,
        log_scale=True,
    )
    plot_metric_lines(
        summaries,
        "deadline_miss_ratio",
        "Deadline miss ratio",
        "SpecNet-Agent QoS violations across network models",
        "fig_specnet_deadline_miss_by_network",
        output_dir,
        args.dpi,
        percent=True,
    )
    plot_waste_bars(summaries, output_dir, args.dpi)
    plot_quality_tradeoff(summaries, args.tradeoff_load, output_dir, args.dpi)
    plot_action_mix(actions, output_dir, args.dpi)
    plot_latency_cdf(latencies, args.cdf_load, output_dir, args.dpi)
    print(f"Wrote SpecNet network-model figures to: {output_dir}")


if __name__ == "__main__":
    main()
