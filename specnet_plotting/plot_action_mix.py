#!/usr/bin/env python3
"""Plot SpecNet-Agent action mix across loads."""

from __future__ import annotations

import argparse
from collections import defaultdict

import matplotlib.pyplot as plt

from plot_common import (
    LOAD_ORDER,
    add_common_args,
    load_positions,
    percent_axis,
    read_actions,
    save_figure,
    set_load_axis,
    setup_style,
)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot SpecNet-Agent action mix.")
    add_common_args(parser)
    args = parser.parse_args()

    setup_style()
    rows = read_actions(args.input_dir)
    counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if row["policy"] == "specnet_agent":
            counts[row["load"]][row["action"]] += int(row["count"])

    fig, ax = plt.subplots()
    xs = load_positions()
    bottoms = [0.0] * len(xs)
    for action in ACTION_ORDER:
        vals = []
        for load in LOAD_ORDER:
            total = sum(counts[load].values()) or 1
            vals.append(100.0 * counts[load][action] / total)
        ax.bar(
            xs,
            vals,
            bottom=bottoms,
            label=ACTION_LABEL[action],
            color=ACTION_COLOR[action],
            width=0.56,
        )
        bottoms = [b + v for b, v in zip(bottoms, vals)]

    set_load_axis(ax)
    percent_axis(ax)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Action share")
    ax.set_xlabel("Offered load")
    ax.set_title("SpecNet-Agent adapts speculation policy")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)
    save_figure(fig, args.output_dir, "fig_action_mix", args.dpi)


if __name__ == "__main__":
    main()
