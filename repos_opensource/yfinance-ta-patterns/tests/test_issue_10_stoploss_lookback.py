"""Tests for Issue #10: Stop-Loss calculation on multi-candle patterns using pattern extremes."""

import numpy as np
import pandas as pd

from yfinance_ta_patterns.ai import (
    AIPatternScorer,
    get_pattern_lookback,
)


def test_get_pattern_lookback():
    """Verify canonical lookback lengths for various candlestick patterns."""
    assert get_pattern_lookback("HAMMER") == 1
    assert get_pattern_lookback("CDLHAMMER") == 1
    assert get_pattern_lookback("DOJI") == 1
    assert get_pattern_lookback("CDLENGULFING") == 2
    assert get_pattern_lookback("HARAMI") == 2
    assert get_pattern_lookback("CDL3WHITESOLDIERS") == 3
    assert get_pattern_lookback("MORNINGSTAR") == 3
    assert get_pattern_lookback("CDLEVENINGSTAR") == 3
    assert get_pattern_lookback("3LINESTRIKE") == 4
    assert get_pattern_lookback("CDLBREAKAWAY") == 5
    assert get_pattern_lookback("UNKNOWN_CUSTOM") == 1


def test_multi_candle_stoploss_three_white_soldiers():
    """Issue #10: Three White Soldiers stop-loss should anchor to candle i-2 low, not candle i."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D", tz="UTC")
    # Build baseline flat data for indicator warmup
    opens = np.full(30, 100.0)
    highs = np.full(30, 102.0)
    lows = np.full(30, 98.0)
    closes = np.full(30, 100.0)

    # Bars 27, 28, 29 represent Three White Soldiers with rising lows
    # Bar 27 (i-2): low=95.0, close=102.0
    # Bar 28 (i-1): low=101.0, close=106.0
    # Bar 29 (i):   low=105.0, close=110.0
    lows[27] = 95.0
    opens[27] = 96.0
    highs[27] = 103.0
    closes[27] = 102.0

    lows[28] = 101.0
    opens[28] = 102.0
    highs[28] = 107.0
    closes[28] = 106.0

    lows[29] = 105.0
    opens[29] = 106.0
    highs[29] = 111.0
    closes[29] = 110.0

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(30, 5000),
        },
        index=dates,
    )

    scorer = AIPatternScorer(df)
    res = scorer.score_signal("CDL3WHITESOLDIERS", dates[29], raw_signal=100)
    assert res.trade_setup is not None
    setup = res.trade_setup

    # Final candle low is 105.0, but pattern minimum low is 95.0 on candle i-2.
    # The stop loss MUST be below 95.0 (the pattern extreme), not anchored to 105.0.
    assert setup.stop_loss < 95.0
    # Entry price is candle 29's close
    assert setup.entry_price == 110.0
    # Risk per unit must encompass the true pattern depth: entry (110) - stop_loss (< 95) > 15
    assert setup.risk_per_unit > 15.0


def test_multi_candle_stoploss_evening_star():
    """Issue #10: Evening Star (bearish) stop-loss should anchor to candle i-1 star high."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D", tz="UTC")
    opens = np.full(30, 100.0)
    highs = np.full(30, 102.0)
    lows = np.full(30, 98.0)
    closes = np.full(30, 100.0)

    # Bar 27 (i-2): tall bullish, high=105.0
    # Bar 28 (i-1): gap up star, high=118.0 (true extreme high of pattern)
    # Bar 29 (i):   bearish reversal, high=108.0, close=101.0
    highs[27] = 105.0
    highs[28] = 118.0
    highs[29] = 108.0
    closes[29] = 101.0

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(30, 5000),
        },
        index=dates,
    )

    scorer = AIPatternScorer(df)
    res = scorer.score_signal("CDLEVENINGSTAR", dates[29], raw_signal=-100)
    assert res.trade_setup is not None
    setup = res.trade_setup

    # Final candle high is 108.0, but pattern maximum high is 118.0 on star candle i-1.
    # Stop-loss for SELL setup must be above 118.0.
    assert setup.stop_loss > 118.0
    assert setup.entry_price == 101.0
    # Risk must reflect entry to > 118 stop
    assert setup.risk_per_unit > 17.0


def test_build_trade_setup_backwards_compatibility():
    """Verify _build_trade_setup supports legacy positional calling as well as pattern extremes."""
    dates = pd.date_range("2025-01-01", periods=20, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": np.full(20, 100.0),
            "High": np.full(20, 102.0),
            "Low": np.full(20, 98.0),
            "Close": np.full(20, 100.0),
            "Volume": np.full(20, 5000),
        },
        index=dates,
    )
    scorer = AIPatternScorer(df)

    # Legacy 5 positional args
    legacy_setup = scorer._build_trade_setup(True, 100.0, 105.0, 95.0, 2.0)
    # Stop loss = low (95.0) - 0.2*2.0 = 94.6
    assert round(legacy_setup.stop_loss, 2) == 94.6

    # With pattern extremes provided
    pattern_setup = scorer._build_trade_setup(
        True, 100.0, 105.0, 98.0, 2.0, pattern_high=107.0, pattern_low=90.0
    )
    # Stop loss = pattern_low (90.0) - 0.2*2.0 = 89.6
    assert round(pattern_setup.stop_loss, 2) == 89.6
