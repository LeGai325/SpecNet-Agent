#!/usr/bin/env python3
"""Plot per-template p95 workflow latency breakdown."""

from __future__ import annotations

import argparse
from collections import defaultdict

import matplotlib.pyplot as plt

from plot_common import (
    POLICY_COLOR,
    POLICY_LABEL,
    POLICY_ORDER,
    TEMPLATE_LABEL,
    add_common_args,
    add_policy_legend,
    percentile,
    read_workflows,
    save_figure,
    setup_style,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot per-template p95 latency breakdown.")
    add_common_args(parser)
    parser.add_argument("--load", default="heavy", choices=["light", "medium", "heavy"])
    args = parser.parse_args()

    setup_style()
    rows = [row for row in read_workflows(args.input_dir) if row["load"] == args.load]
    templates = ["rag_qa", "coding", "research", "debate"]
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["template"], row["policy"])].append(float(row["latency"]))

    fig, ax = plt.subplots(figsize=(7.6, 4.35))
    xs = list(range(len(templates)))
    width = 0.15
    offsets = [(-2 + i) * width for i in range(len(POLICY_ORDER))]
    for offset, policy in zip(offsets, POLICY_ORDER):
        ys = [percentile(grouped[(template, policy)], 0.95) for template in templates]
        ax.bar(
            [x + offset for x in xs],
            ys,
            width=width * 0.92,
            label=POLICY_LABEL[policy],
            color=POLICY_COLOR[policy],
        )

    ax.set_xticks(xs)
    ax.set_xticklabels([TEMPLATE_LABEL[t] for t in templates])
    ax.set_yscale("log")
    ax.set_ylabel("p95 workflow latency (log scale)")
    ax.set_xlabel("Workflow template")
    ax.set_title(f"Per-template tail latency ({args.load} load)")
    add_policy_legend(ax)
    save_figure(fig, args.output_dir, f"fig_template_breakdown_{args.load}", args.dpi)


if __name__ == "__main__":
    main()
