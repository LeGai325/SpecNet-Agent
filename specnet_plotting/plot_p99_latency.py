#!/usr/bin/env python3
"""Plot p99 workflow latency across loads."""

from __future__ import annotations

import argparse

import matplotlib.pyplot as plt

from plot_common import (
    LOAD_ORDER,
    POLICY_COLOR,
    POLICY_LABEL,
    POLICY_MARKER,
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
    parser = argparse.ArgumentParser(description="Plot p99 workflow latency.")
    add_common_args(parser)
    args = parser.parse_args()

    setup_style()
    rows = read_summary(args.input_dir)
    data = summary_lookup(rows, "p99_latency")

    fig, ax = plt.subplots()
    xs = load_positions()
    for policy in POLICY_ORDER:
        ys = [data[policy][load] for load in LOAD_ORDER]
        ax.plot(
            xs,
            ys,
            label=POLICY_LABEL[policy],
            color=POLICY_COLOR[policy],
            marker=POLICY_MARKER[policy],
        )

    set_load_axis(ax)
    ax.set_yscale("log")
    ax.set_ylabel("p99 workflow latency (time units, log scale)")
    ax.set_xlabel("Offered load")
    ax.set_title("Tail workflow latency")
    add_policy_legend(ax)
    save_figure(fig, args.output_dir, "fig_p99_latency", args.dpi)


if __name__ == "__main__":
    main()
