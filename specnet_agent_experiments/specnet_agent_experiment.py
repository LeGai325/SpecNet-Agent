#!/usr/bin/env python3
"""Backward-compatible wrapper for the installable SpecNet-Agent package."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from specnet_agent import *  # noqa: F401,F403,E402
from specnet_agent.cli.experiment import main, parse_args  # noqa: E402


if __name__ == "__main__":
    main()
