"""Comprehensive unit tests for audit v8 fixes.

Issues covered:
1. Static FX fallback in historical backtest issues UserWarning.
2. 4h candles on exchanges with lunch breaks (Tokyo, Hong Kong, etc.) are not dropped.
3. Last intraday stock candle closes at session close without 1h delay in closed_only.
4. Annualization factor for Sharpe ratio uses actual hourly observation counts (1764 US, 2268 European).
5. Last 4h bucket does not disappear from history when ending after market close.
6. Parameter repair=True emits UserWarning when scikit-learn is not installed.
"""

from __future__ import annotations

import datetime
import warnings
from unittest.mock import patch

import pandas as pd
import pytest

from yfinance_ta_patterns.data import (
    MarketDataLoader,
    _get_market_lunch_break,
)
from yfinance_ta_patterns.pattern_tester import (
    DEFAULT_FX_USD_RATES,
    TIMEFRAME_PERIODS_PER_YEAR,
    PatternRankingTester,
    resolve_periods_per_year,
)


# ==============================================================================
# 1. Static FX Fallback UserWarning
# ==============================================================================
def test_static_fx_fallback_emits_warning() -> None:
    """Issue 1: When falling back to DEFAULT_FX_USD_RATES in backtesting, UserWarning is raised."""
    # Data in EUR (e.g. SAP.DE) tested with account in USD
    dates = pd.date_range("2024-01-01", periods=10, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(10)],
            "High": [105.0 + i for i in range(10)],
            "Low": [95.0 + i for i in range(10)],
            "Close": [102.0 + i for i in range(10)],
            "Volume": [1000] * 10,
        },
        index=dates,
    )

    tester = PatternRankingTester(
        df,
        symbol="SAP.DE",
        account_currency="USD",
        strict_fx=False,
    )

    # When resolving EUR to USD without explicit fx_rates, warning must be raised
    with pytest.warns(UserWarning, match="Static default FX rate used for EUR->USD"):
        rate = tester._get_fx_rate("EUR", "USD", timestamp=dates[0])
        assert rate == DEFAULT_FX_USD_RATES["EURUSD"]

    # When explicit fx_rates is provided, NO warning should be raised
    tester_explicit = PatternRankingTester(
        df,
        symbol="SAP.DE",
        account_currency="USD",
        fx_rates={"EURUSD": 1.12},
        strict_fx=False,
    )
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        rate_explicit = tester_explicit._get_fx_rate("EUR", "USD", timestamp=dates[0])
        assert rate_explicit == 1.12
    fx_warnings = [
        w
        for w in record
        if issubclass(w.category, UserWarning) and "Static default" in str(w.message)
    ]
    assert len(fx_warnings) == 0


# ==============================================================================
# 2. Asian Exchanges Lunch Break 4h Buckets
# ==============================================================================
def test_market_lunch_break_detection() -> None:
    """Issue 2: _get_market_lunch_break detects lunch breaks for Tokyo, HK, and Chinese exchanges."""
    d = datetime.date(2024, 3, 15)

    # Tokyo Stock Exchange: 11:30 - 12:30 local (Asia/Tokyo)
    tse_break = _get_market_lunch_break("7203.T", d)
    assert tse_break is not None
    assert tse_break[0] == datetime.time(11, 30)
    assert tse_break[1] == datetime.time(12, 30)

    # Hong Kong Stock Exchange: 12:00 - 13:00 local (Asia/Hong_Kong)
    hk_break = _get_market_lunch_break("0700.HK", d)
    assert hk_break is not None
    assert hk_break[0] == datetime.time(12, 0)
    assert hk_break[1] == datetime.time(13, 0)

    # Shanghai / Shenzhen: 11:30 - 13:00 local (Asia/Shanghai)
    ss_break = _get_market_lunch_break("600519.SS", d)
    assert ss_break is not None
    assert ss_break[0] == datetime.time(11, 30)
    assert ss_break[1] == datetime.time(13, 0)

    # US stocks do not have lunch break
    assert _get_market_lunch_break("AAPL", d) is None


def test_asian_exchange_4h_bucket_with_lunch_break() -> None:
    """Issue 2: 4h bucket spanning lunch break on Tokyo Stock Exchange is recognized as complete."""
    loader = MarketDataLoader(symbol="7203.T", interval="4h", closed_only=True)

    # Tokyo session: 09:00 - 15:00 JST (00:00 - 06:00 UTC) with lunch 11:30 - 12:30 JST (02:30 - 03:30 UTC)
    # Expected hourly slots: 09:00, 10:00, 11:00, 12:30, 13:30, 14:30 JST
    tokyo_bars = [
        pd.Timestamp("2024-03-15 09:00:00", tz="Asia/Tokyo").tz_convert("UTC"),
        pd.Timestamp("2024-03-15 10:00:00", tz="Asia/Tokyo").tz_convert("UTC"),
        pd.Timestamp("2024-03-15 11:00:00", tz="Asia/Tokyo").tz_convert("UTC"),
        pd.Timestamp("2024-03-15 12:30:00", tz="Asia/Tokyo").tz_convert("UTC"),
        pd.Timestamp("2024-03-15 13:30:00", tz="Asia/Tokyo").tz_convert("UTC"),
        pd.Timestamp("2024-03-15 14:30:00", tz="Asia/Tokyo").tz_convert("UTC"),
    ]
    df = pd.DataFrame(
        {
            "Open": [100.0] * len(tokyo_bars),
            "High": [102.0] * len(tokyo_bars),
            "Low": [98.0] * len(tokyo_bars),
            "Close": [101.0] * len(tokyo_bars),
            "Volume": [1000] * len(tokyo_bars),
        },
        index=pd.DatetimeIndex(tokyo_bars),
    )

    now = pd.Timestamp("2024-03-16 00:00:00", tz="UTC")
    resampled = loader._resample_if_needed(df, now_utc=now)

    # The 00:00 UTC bucket (09:00-13:00 JST, crossing lunch break 11:30-12:30) must be formed and complete!
    assert not resampled.empty
    assert pd.Timestamp("2024-03-15 00:00:00", tz="UTC") in resampled.index


# ==============================================================================
# 3. Last Hourly Candle Closes at Session Close
# ==============================================================================
def test_last_hourly_candle_closes_at_session_close() -> None:
    """Issue 3: US stock 15:30-16:00 candle ends at 16:00 and is NOT filtered out between 16:00 and 16:30."""
    loader = MarketDataLoader(symbol="AAPL", interval="1h", closed_only=True)

    # US regular market closes at 16:00 EDT (20:00 UTC in summer EDT)
    # The 15:30 EDT candle is 19:30 UTC
    candle_ts = pd.Timestamp("2024-06-14 19:30:00", tz="UTC")
    df = pd.DataFrame(
        {"Open": [150.0], "High": [151.0], "Low": [149.0], "Close": [150.5], "Volume": [5000]},
        index=pd.DatetimeIndex([candle_ts]),
    )

    # At 16:15 EDT (20:15 UTC): market has closed at 16:00 EDT (20:00 UTC)
    # The 15:30 candle was completed at 16:00 EDT.
    # Previously, candle_end was 15:30 + 1h = 16:30 EDT (20:30 UTC), so at 20:15 UTC it was wrongly excluded!
    now_at_1615 = pd.Timestamp("2024-06-14 20:15:00", tz="UTC")
    processed = loader.process(df, now_utc=now_at_1615)
    assert len(processed) == 1, (
        "Completed 15:30 candle should NOT be filtered out after 16:00 session close"
    )

    # Before session close (e.g. 15:45 EDT / 19:45 UTC), candle is still forming -> should be filtered out
    now_at_1545 = pd.Timestamp("2024-06-14 19:45:00", tz="UTC")
    processed_before = loader.process(df, now_utc=now_at_1545)
    assert len(processed_before) == 0, "Candle should be filtered out before session close"


# ==============================================================================
# 4. Observation Count for Hourly Sharpe Annualization
# ==============================================================================
def test_hourly_sharpe_observation_counts() -> None:
    """Issue 4: Sharpe annualization factors use actual observations count per year."""
    # US stocks: 7 hourly candles/day * 252 days = 1764.0
    assert TIMEFRAME_PERIODS_PER_YEAR["60m"] == 1764.0
    assert TIMEFRAME_PERIODS_PER_YEAR["1h"] == 1764.0
    assert resolve_periods_per_year("1h", "AAPL") == 1764.0
    assert resolve_periods_per_year("60m", "MSFT") == 1764.0

    # European / LSE stocks: 9 hourly candles/day * 252 days = 2268.0
    assert resolve_periods_per_year("1h", "VOD.L") == 2268.0
    assert resolve_periods_per_year("60m", "SAP.DE") == 2268.0
    assert resolve_periods_per_year("1h", "MC.PA") == 2268.0
    assert resolve_periods_per_year("1h", "NESN.SW") == 2268.0

    # Crypto (24/7) remains unchanged: 365 * 24 = 8760.0
    assert resolve_periods_per_year("1h", "BTC-USD") == 8760.0
    # Forex (24/5) remains unchanged: 260 * 24 = 6240.0
    assert resolve_periods_per_year("1h", "EURUSD=X") == 6240.0


# ==============================================================================
# 5. Last 4h Bucket Preserved When Ending Past Session Close
# ==============================================================================
def test_last_4h_bucket_preserved_after_market_close() -> None:
    """Issue 5: Resampling 4h does not drop last completed bucket whose interval ends past market close."""
    loader = MarketDataLoader(symbol="AAPL", interval="4h", closed_only=True)

    # US regular market: 09:30 - 16:00 EDT (13:30 - 20:00 UTC)
    # 7 hourly bars covering the full session from 13:30 to 19:30 UTC
    times = [
        pd.Timestamp("2024-06-14 13:30:00", tz="UTC"),
        pd.Timestamp("2024-06-14 14:30:00", tz="UTC"),
        pd.Timestamp("2024-06-14 15:30:00", tz="UTC"),
        pd.Timestamp("2024-06-14 16:30:00", tz="UTC"),
        pd.Timestamp("2024-06-14 17:30:00", tz="UTC"),
        pd.Timestamp("2024-06-14 18:30:00", tz="UTC"),
        pd.Timestamp("2024-06-14 19:30:00", tz="UTC"),
    ]
    df = pd.DataFrame(
        {
            "Open": [100.0] * len(times),
            "High": [102.0] * len(times),
            "Low": [98.0] * len(times),
            "Close": [101.0] * len(times),
            "Volume": [1000] * len(times),
        },
        index=pd.DatetimeIndex(times),
    )

    # With closed_only=True and now in the evening after market close:
    now_evening = pd.Timestamp("2024-06-14 23:00:00", tz="UTC")
    resampled = loader._resample_if_needed(df, now_utc=now_evening)

    # All buckets formed by this session should be present (nothing dropped)
    assert not resampled.empty
    # The last bucket should include the end of the trading day
    assert resampled.index[-1].date() == datetime.date(2024, 6, 14)


# ==============================================================================
# 6. UserWarning When repair=True without Scikit-learn
# ==============================================================================
def test_repair_without_sklearn_emits_user_warning() -> None:
    """Issue 6: fetch(repair=True) emits UserWarning when HAS_SKLEARN is False."""
    pytest.importorskip("yfinance")
    loader = MarketDataLoader(symbol="AAPL", interval="1d", repair=True)

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", False), patch(
        "yfinance_ta_patterns.data.yf.download"
    ) as mock_dl:
        mock_dl.return_value = pd.DataFrame(
            {"Open": [100.0], "High": [105.0], "Low": [95.0], "Close": [102.0], "Volume": [1000]},
            index=pd.date_range("2024-01-01", periods=1, freq="1d", tz="UTC"),
        )
        with pytest.warns(UserWarning, match="Data repair was requested .*scikit-learn"):
            loader.fetch()
