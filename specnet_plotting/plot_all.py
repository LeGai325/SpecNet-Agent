#!/usr/bin/env python3
"""Run all SpecNet-Agent plotting scripts."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from plot_common import add_common_args


SCRIPTS = [
    "plot_p99_latency.py",
    "plot_deadline_miss.py",
    "plot_wasted_speculative_bytes.py",
    "plot_quality_tradeoff.py",
    "plot_action_mix.py",
    "plot_latency_cdf.py",
    "plot_template_breakdown.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate all SpecNet-Agent PNG/PDF figures.")
    add_common_args(parser)
    parser.add_argument("--load", default="heavy", choices=["light", "medium", "heavy"])
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    for script in SCRIPTS:
        cmd = [
            sys.executable,
            os.path.join(script_dir, script),
            "--input-dir",
            args.input_dir,
            "--output-dir",
            args.output_dir,
            "--dpi",
            str(args.dpi),
        ]
        if script in {"plot_quality_tradeoff.py", "plot_latency_cdf.py", "plot_template_breakdown.py"}:
            cmd.extend(["--load", args.load])
        print("Running:", " ".join(cmd))
        subprocess.check_call(cmd)


if __name__ == "__main__":
    main()
