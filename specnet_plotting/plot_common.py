#!/usr/bin/env python3
"""Backward-compatible wrapper for specnet_agent.analysis.common."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from specnet_agent.analysis.common import *  # noqa: F401,F403,E402
