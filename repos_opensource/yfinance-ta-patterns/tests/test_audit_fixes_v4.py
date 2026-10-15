"""Unit and regression tests for all 6 audit fixes in v0.3.9."""

from __future__ import annotations

import pandas as pd
import pytest

from yfinance_ta_patterns.ai.scorer import (
    PatternConfidenceResult,
    SignalGrade,
)
from yfinance_ta_patterns.data import (
    MarketDataLoader,
    resolve_asset_currencies,
)
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    resolve_periods_per_year,
)

# ==============================================================================
# 1. Timezone Does Not Shift Daily Candle Close Bounds
# ==============================================================================


def test_timezone_does_not_shift_daily_candle_close() -> None:
    """Issue 1: closed_only daily filtering is identical whether timezone is UTC or America/Los_Angeles."""
    dt_candle = pd.Timestamp("2024-05-15 00:00:00", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [65000.0],
            "High": [66000.0],
            "Low": [64500.0],
            "Close": [65500.0],
            "Volume": [1000],
        },
        index=[dt_candle],
    )

    # Middle of current day (12:00 UTC) -> candle is still open
    now_midday = pd.Timestamp("2024-05-15 12:00:00", tz="UTC")

    loader_utc = MarketDataLoader(symbol="BTC-USD", interval="1d", timezone="UTC", closed_only=True)
    loader_la = MarketDataLoader(
        symbol="BTC-USD", interval="1d", timezone="America/Los_Angeles", closed_only=True
    )

    res_utc_midday = loader_utc.process(df.copy(), now_utc=now_midday)
    res_la_midday = loader_la.process(df.copy(), now_utc=now_midday)

    # Both must discard the unclosed candle
    assert len(res_utc_midday) == 0
    assert len(res_la_midday) == 0

    # Past midnight UTC of next day (00:05 UTC) -> candle is now closed
    now_next_day = pd.Timestamp("2024-05-16 00:05:00", tz="UTC")

    res_utc_closed = loader_utc.process(df.copy(), now_utc=now_next_day)
    res_la_closed = loader_la.process(df.copy(), now_utc=now_next_day)

    assert len(res_utc_closed) == 1
    assert len(res_la_closed) == 1

    # Ensure LA loader converted index to America/Los_Angeles
    assert str(res_la_closed.index.tz) == "America/Los_Angeles"


# ==============================================================================
# 2. Unknown FX Exchange Rate Fallback Raises Error
# ==============================================================================


def test_unknown_fx_rate_raises_error() -> None:
    """Issue 2: Unknown exchange rate raises ValueError instead of silently using 1.0."""
    dates = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 5,
            "High": [105.0] * 5,
            "Low": [95.0] * 5,
            "Close": [100.0] * 5,
            "Volume": [1000] * 5,
        },
        index=dates,
    )

    # Missing rate for BTC -> USD (not in DEFAULT_FX_USD_RATES)
    tester = PatternRankingTester(df, symbol="BTC-USD", account_currency="USD")

    with pytest.raises(ValueError, match="no exchange rate available"):
        tester._get_fx_rate("BTC", "USD")

    # Missing exotic currency HKD
    with pytest.raises(ValueError, match="no exchange rate available"):
        tester._get_fx_rate("HKD", "USD")

    # Direct rate provided in fx_rates
    tester_with_rate = PatternRankingTester(
        df,
        symbol="BTC-USD",
        account_currency="USD",
        fx_rates={"BTCUSD": 65000.0},
    )
    assert tester_with_rate._get_fx_rate("BTC", "USD") == 65000.0
    assert tester_with_rate._get_fx_rate("USD", "BTC") == 1.0 / 65000.0

    # Same currency conversion always returns 1.0
    assert tester._get_fx_rate("BTC", "BTC") == 1.0
    assert tester._get_fx_rate("USD", "USD") == 1.0


# ==============================================================================
# 3. Hyphenated Stock Tickers (BRK-B, BF-B) Not Parsed as Currency Pairs
# ==============================================================================


def test_hyphenated_stocks_not_parsed_as_pairs() -> None:
    """Issue 3: Hyphenated stocks like BRK-B and BF-B are preserved as stock symbols with USD quote."""
    assert resolve_asset_currencies("BRK-B", asset_type="stock") == ("BRK-B", "USD")
    assert resolve_asset_currencies("BRK-B", asset_type="auto") == ("BRK-B", "USD")
    assert resolve_asset_currencies("BF-B", asset_type="auto") == ("BF-B", "USD")

    # True crypto pairs remain parsed correctly
    assert resolve_asset_currencies("BTC-USD", asset_type="auto") == ("BTC", "USD")
    assert resolve_asset_currencies("ETH-BTC", asset_type="auto") == ("ETH", "BTC")

    # Foreign stocks with exchange suffixes
    assert resolve_asset_currencies("VOD.L", asset_type="auto") == ("VOD.L", "GBp")
    assert resolve_asset_currencies("SAP.DE", asset_type="auto") == ("SAP.DE", "EUR")


# ==============================================================================
# 4. 90m Interval Candle Duration and Sharpe Ratios
# ==============================================================================


def test_90m_interval_candle_duration_and_sharpe() -> None:
    """Issue 4: 90m interval uses 90-minute delta (not 1D) and has correct Sharpe annual factors."""
    loader = MarketDataLoader(symbol="AAPL", interval="90m", closed_only=True)

    dt_start = pd.Timestamp("2024-05-15 10:00:00", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [180.0],
            "High": [182.0],
            "Low": [179.0],
            "Close": [181.0],
            "Volume": [50000],
        },
        index=[dt_start],
    )

    # 90m candle from 10:00 ends at 11:30 UTC
    # At 11:15 UTC -> open
    res_open = loader.process(df.copy(), now_utc=pd.Timestamp("2024-05-15 11:15:00", tz="UTC"))
    assert len(res_open) == 0

    # At 11:31 UTC -> closed
    res_closed = loader.process(df.copy(), now_utc=pd.Timestamp("2024-05-15 11:31:00", tz="UTC"))
    assert len(res_closed) == 1

    # Annual Sharpe periods
    assert (
        resolve_periods_per_year("90m", "AAPL") == 252.0 * 5.0
    )  # 1260.0 (5 candles per US trading day)
    assert resolve_periods_per_year("90m", "BTC-USD") == 365.0 * 16.0  # 5840.0
    assert resolve_periods_per_year("90m", "EURUSD=X") == 260.0 * 16.0  # 4160.0


# ==============================================================================
# 5. 3mo and 5d Candle Boundaries
# ==============================================================================


def test_3mo_and_5d_candle_boundaries() -> None:
    """Issue 5: 3mo uses 3-month DateOffset, 5d uses 5-day delta, and unknown intervals raise ValueError."""
    dt_jan1 = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [110.0],
            "Low": [90.0],
            "Close": [105.0],
            "Volume": [1000],
        },
        index=[dt_jan1],
    )

    # 3mo quarterly candle
    loader_3mo = MarketDataLoader(symbol="AAPL", interval="3mo", closed_only=True)

    # Jan 15 -> not closed yet
    res_jan15 = loader_3mo.process(df.copy(), now_utc=pd.Timestamp("2024-01-15 00:00:00", tz="UTC"))
    assert len(res_jan15) == 0

    # April 1 00:01 UTC -> closed (Q1 ended March 31)
    res_apr1 = loader_3mo.process(df.copy(), now_utc=pd.Timestamp("2024-04-01 00:01:00", tz="UTC"))
    assert len(res_apr1) == 1

    # 5d candle
    loader_5d = MarketDataLoader(symbol="AAPL", interval="5d", closed_only=True)

    # Jan 3 -> not closed
    res_jan3 = loader_5d.process(df.copy(), now_utc=pd.Timestamp("2024-01-03 00:00:00", tz="UTC"))
    assert len(res_jan3) == 0

    # Jan 6 00:01 UTC -> closed
    res_jan6 = loader_5d.process(df.copy(), now_utc=pd.Timestamp("2024-01-06 00:01:00", tz="UTC"))
    assert len(res_jan6) == 1

    # Unsupported interval raises ValueError
    loader_bad = MarketDataLoader(symbol="AAPL", interval="unknown_custom", closed_only=True)
    with pytest.raises(ValueError, match="Unsupported interval for closed_only filtering"):
        loader_bad.process(df.copy(), now_utc=pd.Timestamp("2024-01-10 00:00:00", tz="UTC"))


# ==============================================================================
# 6. Confluence Score Naming and Backward Compatibility
# ==============================================================================


def test_confluence_score_naming_and_compatibility() -> None:
    """Issue 6: PatternConfidenceResult supports confluence_score with full backward compatibility."""
    dt = pd.Timestamp("2024-05-15 10:00:00", tz="UTC")

    # Initialized with confidence_score
    res1 = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=dt,
        raw_signal=100,
        confidence_score=0.82,
        grade=SignalGrade.EXCELLENT,
    )
    assert res1.confluence_score == 0.82
    assert res1.confidence_score == 0.82
    assert res1.confluence == 0.82
    assert res1.confidence == 0.82

    d1 = res1.to_dict()
    assert d1["confluence_score"] == 0.82
    assert d1["confidence_score"] == 0.82

    # Initialized with confluence_score
    res2 = PatternConfidenceResult(
        pattern_name="ENGULFING",
        timestamp=dt,
        raw_signal=-100,
        confluence_score=0.74,
        grade=SignalGrade.STRONG,
    )
    assert res2.confluence_score == 0.74
    assert res2.confidence_score == 0.74
    assert res2.confluence == 0.74
    assert res2.confidence == 0.74
