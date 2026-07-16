#!/usr/bin/env python3
"""Shared plotting utilities for SpecNet-Agent figures."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "specnet-matplotlib-cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter


LOAD_ORDER = ["light", "medium", "heavy"]

POLICY_ORDER = [
    "fifo",
    "static_priority",
    "critical_path_only",
    "rule_based_feedback",
    "specnet_agent",
]

POLICY_LABEL = {
    "fifo": "FIFO",
    "static_priority": "Static priority",
    "critical_path_only": "Critical-path only",
    "rule_based_feedback": "Rule feedback",
    "specnet_agent": "SpecNet-Agent",
}

POLICY_SHORT = {
    "fifo": "FIFO",
    "static_priority": "Static",
    "critical_path_only": "CritPath",
    "rule_based_feedback": "Rule",
    "specnet_agent": "SpecNet",
}

POLICY_COLOR = {
    "fifo": "#64748b",
    "static_priority": "#7c3aed",
    "critical_path_only": "#2563eb",
    "rule_based_feedback": "#f59e0b",
    "specnet_agent": "#059669",
}

POLICY_MARKER = {
    "fifo": "o",
    "static_priority": "s",
    "critical_path_only": "^",
    "rule_based_feedback": "D",
    "specnet_agent": "P",
}

TEMPLATE_LABEL = {
    "rag_qa": "RAG-QA",
    "coding": "Coding",
    "research": "Research",
    "debate": "Debate",
}


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-dir",
        default="outputs/results",
        help="Directory containing SpecNet-Agent result CSV files.",
    )
    parser.add_argument(
        "--output-dir",
        default="figures_matplotlib",
        help="Directory where PNG and PDF figures will be written.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="PNG output DPI.",
    )


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_summary(input_dir: str) -> List[Dict[str, str]]:
    return read_csv(os.path.join(input_dir, "summary_aggregate.csv"))


def read_workflows(input_dir: str) -> List[Dict[str, str]]:
    return read_csv(os.path.join(input_dir, "workflow_results.csv"))


def read_actions(input_dir: str) -> List[Dict[str, str]]:
    return read_csv(os.path.join(input_dir, "action_counts.csv"))


def summary_lookup(rows: Sequence[Dict[str, str]], metric: str) -> Dict[str, Dict[str, float]]:
    data: Dict[str, Dict[str, float]] = defaultdict(dict)
    for row in rows:
        data[row["policy"]][row["load"]] = float(row[metric])
    return data


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(values) - 1)
    weight = rank - lo
    return values[lo] * (1 - weight) + values[hi] * weight


def setup_style() -> None:
    plt.rcParams.update(
        {
            "figure.figsize": (7.2, 4.2),
            "figure.dpi": 120,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.04,
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.labelsize": 10.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#e2e8f0",
            "grid.linewidth": 0.8,
            "grid.alpha": 1.0,
            "legend.frameon": False,
            "lines.linewidth": 2.0,
            "lines.markersize": 6.0,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def save_figure(fig, output_dir: str, basename: str, dpi: int = 300) -> None:
    os.makedirs(output_dir, exist_ok=True)
    png_path = os.path.join(output_dir, f"{basename}.png")
    pdf_path = os.path.join(output_dir, f"{basename}.pdf")
    fig.savefig(png_path, dpi=dpi)
    fig.savefig(pdf_path)
    print(f"Wrote {png_path}")
    print(f"Wrote {pdf_path}")


def load_positions() -> List[int]:
    return list(range(len(LOAD_ORDER)))


def set_load_axis(ax) -> None:
    ax.set_xticks(load_positions())
    ax.set_xticklabels([load.capitalize() for load in LOAD_ORDER])


def add_policy_legend(ax, ncol: int = 3) -> None:
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=ncol)


def percent_axis(ax) -> None:
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
