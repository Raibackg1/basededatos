"""Tests for Issue #11: Directional splitting and indecision candle filtering in PatternRankingTester."""

import numpy as np
import pandas as pd

from yfinance_ta_patterns.pattern_tester import (
    INDECISION_PATTERNS,
    TWO_WAY_PATTERNS,
    PatternRankingTester,
)


def test_indecision_and_two_way_sets():
    """Verify constant sets for indecision and two-way patterns."""
    assert "CDLDOJI" in INDECISION_PATTERNS
    assert "CDLSPINNINGTOP" in INDECISION_PATTERNS
    assert "CDLENGULFING" in TWO_WAY_PATTERNS
    assert "CDLHARAMI" in TWO_WAY_PATTERNS
    assert "CDLMARUBOZU" in TWO_WAY_PATTERNS


def test_indecision_pattern_not_traded_as_directional_buy():
    """Issue #11: CDLDOJI outputs +100 in TA-Lib, but should not trade as a BUY signal by default."""
    dates = pd.date_range("2025-01-01", periods=20, freq="1D", tz="UTC")
    # All candles are Dojis (Open == Close)
    opens = np.full(20, 100.0)
    highs = np.full(20, 105.0)
    lows = np.full(20, 95.0)
    closes = np.full(20, 100.0)

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(20, 5000),
        },
        index=dates,
    )

    tester = PatternRankingTester(df, min_signals=1)

    # When trade_indecision=False is passed, neutral indecision candles produce 0 directional trade signals
    res_filtered = tester.test_pattern("CDLDOJI", trade_indecision=False)
    assert res_filtered is None

    # By default, for backwards compatibility, trade_indecision=True preserves signal evaluation
    res_default = tester.test_pattern("CDLDOJI")
    assert res_default is not None
    assert res_default.total_signals > 0


def test_directional_splitting_engulfing():
    """Issue #11: CDLENGULFING_BULL and CDLENGULFING_BEAR should isolate respective directions."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    # Use same-direction background runs so only the two explicit reversal
    # pairs below can qualify as engulfing patterns across TA-Lib versions.
    opens = np.array([101.0, 101.0, 101.0, 98.0, 99.0, 99.0, 99.0, 102.0, 101.0, 101.0])
    highs = np.full(10, 102.0)
    lows = np.full(10, 98.0)
    closes = np.array([99.0, 99.0, 99.0, 103.0, 101.0, 101.0, 101.0, 97.0, 99.0, 99.0])

    # Bar 2-3: Bullish Engulfing
    # Bar 2: small black candle
    opens[2] = 101.0
    closes[2] = 99.0
    highs[2] = 101.5
    lows[2] = 98.5
    # Bar 3: large white engulfing candle
    opens[3] = 98.0
    closes[3] = 103.0
    highs[3] = 103.5
    lows[3] = 97.5

    # Bar 6-7: Bearish Engulfing
    # Bar 6: small white candle
    opens[6] = 99.0
    closes[6] = 101.0
    highs[6] = 101.5
    lows[6] = 98.5
    # Bar 7: large black engulfing candle
    opens[7] = 102.0
    closes[7] = 97.0
    highs[7] = 102.5
    lows[7] = 96.5

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(10, 5000),
        },
        index=dates,
    )

    tester = PatternRankingTester(df, min_signals=1, holding_period=2)

    # Test Bullish Engulfing only
    res_bull = tester.test_pattern("CDLENGULFING_BULL")
    assert res_bull is not None
    assert res_bull.pattern_name == "ENGULFING_BULL"
    assert res_bull.total_signals == 1
    for trade in tester.trades:
        assert trade["direction"] == "LONG"

    # Test Bearish Engulfing only
    res_bear = tester.test_pattern("CDLENGULFING_BEAR")
    assert res_bear is not None
    assert res_bear.pattern_name == "ENGULFING_BEAR"
    assert res_bear.total_signals == 1
    for trade in tester.trades:
        assert trade["direction"] == "SHORT"

    # Test via direction parameter
    res_param = tester.test_pattern("CDLENGULFING", direction="bullish")
    assert res_param is not None
    assert res_param.total_signals == 1


def test_test_all_patterns_split_directions():
    """Issue #11: test_all_patterns(split_directions=True) evaluates Bull/Bear separately."""
    dates = pd.date_range("2025-01-01", periods=15, freq="1D", tz="UTC")
    opens = np.full(15, 100.0)
    highs = np.full(15, 102.0)
    lows = np.full(15, 98.0)
    closes = np.full(15, 100.0)

    # Insert Bullish Engulfing
    opens[2] = 101.0
    closes[2] = 99.0
    opens[3] = 98.0
    closes[3] = 103.0
    highs[3] = 104.0
    lows[3] = 97.0

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(15, 5000),
        },
        index=dates,
    )

    tester = PatternRankingTester(df, min_signals=1)
    results = tester.test_all_patterns(split_directions=True, exclude_indecision=True)
    names = [r.pattern_name for r in results]

    # Engulfing Bull should be present in results
    assert any("ENGULFING_BULL" in n for n in names)
    # Neutral Doji should NOT be present in ranked results when exclude_indecision=True
    assert "DOJI" not in names
