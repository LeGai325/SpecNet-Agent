#!/usr/bin/env python3
"""Plot latency-quality tradeoff under a selected load."""

from __future__ import annotations

import argparse

import matplotlib.pyplot as plt

from plot_common import (
    POLICY_COLOR,
    POLICY_LABEL,
    POLICY_MARKER,
    POLICY_ORDER,
    add_common_args,
    read_summary,
    save_figure,
    setup_style,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot latency-quality tradeoff.")
    add_common_args(parser)
    parser.add_argument("--load", default="heavy", choices=["light", "medium", "heavy"])
    args = parser.parse_args()

    setup_style()
    rows = [row for row in read_summary(args.input_dir) if row["load"] == args.load]
    by_policy = {row["policy"]: row for row in rows}

    fig, ax = plt.subplots()
    for policy in POLICY_ORDER:
        row = by_policy[policy]
        x = float(row["p99_latency"])
        y = float(row["avg_quality"])
        size = 120 if policy == "specnet_agent" else 78
        ax.scatter(
            [x],
            [y],
            s=size,
            color=POLICY_COLOR[policy],
            marker=POLICY_MARKER[policy],
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
        )
        ax.annotate(
            POLICY_LABEL[policy],
            (x, y),
            xytext=(7, 6),
            textcoords="offset points",
            fontsize=8.8,
        )

    ax.set_xscale("log")
    ax.set_xlabel("p99 workflow latency (time units, log scale)")
    ax.set_ylabel("Average quality proxy")
    ax.set_ylim(0.82, 1.02)
    ax.set_title(f"Latency-quality tradeoff ({args.load} load)")
    save_figure(fig, args.output_dir, f"fig_quality_tradeoff_{args.load}", args.dpi)


if __name__ == "__main__":
    main()
