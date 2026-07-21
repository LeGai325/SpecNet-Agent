
#!/usr/bin/env python3
"""Plot deadline miss ratio across loads."""

from __future__ import annotations

import argparse

import matplotlib.pyplot as plt

from .common import (
    LOAD_ORDER,
    POLICY_COLOR,
    POLICY_LABEL,
    POLICY_MARKER,
    POLICY_ORDER,
    add_common_args,
    add_policy_legend,
    load_positions,
    percent_axis,
    read_summary,
    save_figure,
    set_load_axis,
    setup_style,
    summary_lookup,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot deadline miss ratio.")
    add_common_args(parser)
    args = parser.parse_args()

    setup_style()
    rows = read_summary(args.input_dir)
    data = summary_lookup(rows, "deadline_miss_ratio")

    fig, ax = plt.subplots()
    xs = load_positions()
    for policy in POLICY_ORDER:
        ys = [100.0 * data[policy][load] for load in LOAD_ORDER]
        ax.plot(
            xs,
            ys,
            label=POLICY_LABEL[policy],
            color=POLICY_COLOR[policy],
            marker=POLICY_MARKER[policy],
        )

    set_load_axis(ax)
    percent_axis(ax)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Deadline miss ratio")
    ax.set_xlabel("Offered load")
    ax.set_title("QoS violation rate")
    add_policy_legend(ax)
    save_figure(fig, args.output_dir, "fig_deadline_miss", args.dpi)


if __name__ == "__main__":
    main()
