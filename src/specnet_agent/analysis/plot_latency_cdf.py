
#!/usr/bin/env python3
"""Plot workflow latency CDF for a selected load."""

from __future__ import annotations

import argparse
from collections import defaultdict

import matplotlib.pyplot as plt

from .common import (
    POLICY_COLOR,
    POLICY_LABEL,
    POLICY_MARKER,
    POLICY_ORDER,
    add_common_args,
    add_policy_legend,
    read_workflows,
    save_figure,
    setup_style,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot workflow latency CDF.")
    add_common_args(parser)
    parser.add_argument("--load", default="heavy", choices=["light", "medium", "heavy"])
    args = parser.parse_args()

    setup_style()
    rows = [row for row in read_workflows(args.input_dir) if row["load"] == args.load]
    latencies = defaultdict(list)
    for row in rows:
        latencies[row["policy"]].append(float(row["latency"]))

    fig, ax = plt.subplots()
    for policy in POLICY_ORDER:
        values = sorted(latencies[policy])
        if not values:
            continue
        n = len(values)
        ys = [(idx + 1) / n for idx in range(n)]
        ax.plot(
            values,
            ys,
            label=POLICY_LABEL[policy],
            color=POLICY_COLOR[policy],
            marker=POLICY_MARKER[policy],
            markevery=max(1, n // 12),
            linewidth=2.0,
        )

    ax.set_xscale("log")
    ax.set_ylim(0, 1.01)
    ax.set_xlabel("Workflow latency (time units, log scale)")
    ax.set_ylabel("CDF")
    ax.set_title(f"Workflow latency distribution ({args.load} load)")
    add_policy_legend(ax)
    save_figure(fig, args.output_dir, f"fig_latency_cdf_{args.load}", args.dpi)


if __name__ == "__main__":
    main()
