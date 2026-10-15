"""AI Intelligence module for yfinance-ta-patterns."""

from __future__ import annotations

from .analyst import AIMarketAnalyst
from .scorer import (
    PATTERN_CANDLE_COUNTS,
    AIPatternScorer,
    PatternConfidenceResult,
    SignalGrade,
    TradeSetup,
    get_pattern_lookback,
)

__all__ = [
    "PATTERN_CANDLE_COUNTS",
    "AIMarketAnalyst",
    "AIPatternScorer",
    "PatternConfidenceResult",
    "SignalGrade",
    "TradeSetup",
    "get_pattern_lookback",
]
