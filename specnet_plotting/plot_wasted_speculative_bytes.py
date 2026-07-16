#!/usr/bin/env python3
"""Plot wasted speculative bytes per workflow."""

from __future__ import annotations

import argparse

import matplotlib.pyplot as plt

from plot_common import (
    LOAD_ORDER,
    POLICY_COLOR,
    POLICY_LABEL,
    POLICY_ORDER,
    add_common_args,
    add_policy_legend,
    load_positions,
    read_summary,
    save_figure,
    set_load_axis,
    setup_style,
    summary_lookup,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot wasted speculative bytes.")
    add_common_args(parser)
    args = parser.parse_args()

    setup_style()
    rows = read_summary(args.input_dir)
    data = summary_lookup(rows, "wasted_speculative_bytes_per_workflow")

    fig, ax = plt.subplots()
    xs = load_positions()
    width = 0.15
    offsets = [(-2 + i) * width for i in range(len(POLICY_ORDER))]
    for offset, policy in zip(offsets, POLICY_ORDER):
        ys = [data[policy][load] for load in LOAD_ORDER]
        ax.bar(
            [x + offset for x in xs],
            ys,
            width=width * 0.92,
            label=POLICY_LABEL[policy],
            color=POLICY_COLOR[policy],
        )

    set_load_axis(ax)
    ax.set_ylabel("Unused speculative bytes per workflow")
    ax.set_xlabel("Offered load")
    ax.set_title("Wasted speculative traffic")
    ax.set_ylim(bottom=0)
    add_policy_legend(ax)
    save_figure(fig, args.output_dir, "fig_wasted_speculative_bytes", args.dpi)


if __name__ == "__main__":
    main()
