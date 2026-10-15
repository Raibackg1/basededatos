"""Root conftest for yfinance-ta-patterns test suite."""

from __future__ import annotations

import sys
from pathlib import Path

# Add tests directory to sys.path so tests.conftest can be imported
sys.path.insert(0, str(Path(__file__).parent))

from tests.conftest import *  # noqa: F403
