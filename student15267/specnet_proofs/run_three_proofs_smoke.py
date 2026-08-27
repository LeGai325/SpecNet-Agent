#!/usr/bin/env python3
"""Run the three canonical proof smoke checks from one reproducible entrypoint."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "results" / "three_proofs_smoke")
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    jobs = [
        ("rq1_state_necessity", "proof_harness.py", ["--mode", "smoke"]),
        ("rq2_factorized_mechanism", "factorized_signal_study.py", ["--mode", "smoke"]),
        ("rq3_rule_stability", "three_signal_rule_study.py", ["--mode", "smoke"]),
    ]
    for name, script, mode_args in jobs:
        output = args.output_root / name
        command = [sys.executable, str(ROOT / script), *mode_args, "--output-dir", str(output)]
        print("[three-proofs]", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT.parent)
        if result.returncode:
            return result.returncode
    print(f"[three-proofs] completed: {args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
