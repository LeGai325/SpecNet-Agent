#!/usr/bin/env python3
"""Backward-compatible wrapper for workload export."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from specnet_agent.cli.export_workloads import load_experiment_module, main  # noqa: E402,F401


if __name__ == "__main__":
    main()
