"""Tests for Audit Fixes v14:
1. Inconsistent OHLC Adjustment in use_adj_close (pattern_tester.py:419-422)
2. Time Offset in FX Conversion for Naive Series (pattern_tester.py:526-533)
3. 5-Day Candle Closure Skips Weekends (data.py:1381-1407)
4. Data Gap Warning in Non-Strict validate_ohlc (data.py:760-785)
5. Session-End Closure for Monthly & 3-Month Candles (data.py:1323-1326)
"""

from __future__ import annotations

import pandas as pd
import pytest
import pytz

from yfinance_ta_patterns.data import MarketDataLoader, validate_ohlc
from yfinance_ta_patterns.forex_data_loader import ForexDataLoader
from yfinance_ta_patterns.pattern_tester import PatternRankingTester

# ==============================================================================
# 1. Inconsistent OHLC Adjustment in use_adj_close (pattern_tester.py)
# ==============================================================================


def test_use_adj_close_scales_ohl_proportionally() -> None:
    """When use_adj_close=True, Open, High, and Low must scale by (Adj Close / Close)."""
    dates = pd.date_range("2025-01-01", periods=3, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0, 200.0, 150.0],
            "High": [110.0, 220.0, 160.0],
            "Low": [90.0, 180.0, 140.0],
            "Close": [100.0, 200.0, 150.0],
            "Adj Close": [50.0, 100.0, 150.0],  # 2:1 split on bars 0 & 1, 1:1 on bar 2
            "Volume": [1000, 1000, 1000],
        },
        index=dates,
    )

    tester = PatternRankingTester(df, symbol="AAPL", use_adj_close=True)
    adj_data = tester._data

    # Bar 0: ratio = 0.5
    assert adj_data["Open"].iloc[0] == 50.0
    assert adj_data["High"].iloc[0] == 55.0
    assert adj_data["Low"].iloc[0] == 45.0
    assert adj_data["Close"].iloc[0] == 50.0

    # Bar 1: ratio = 0.5
    assert adj_data["Open"].iloc[1] == 100.0
    assert adj_data["High"].iloc[1] == 110.0
    assert adj_data["Low"].iloc[1] == 90.0
    assert adj_data["Close"].iloc[1] == 100.0

    # Bar 2: ratio = 1.0 (unchanged)
    assert adj_data["Open"].iloc[2] == 150.0
    assert adj_data["High"].iloc[2] == 160.0
    assert adj_data["Low"].iloc[2] == 140.0
    assert adj_data["Close"].iloc[2] == 150.0

    # Bar geometry invariants preserved
    assert (adj_data["Low"] <= adj_data["Open"]).all()
    assert (adj_data["Low"] <= adj_data["Close"]).all()
    assert (adj_data["Open"] <= adj_data["High"]).all()
    assert (adj_data["Close"] <= adj_data["High"]).all()


# ==============================================================================
# 2. Time Offset in FX Conversion for Naive Series (pattern_tester.py)
# ==============================================================================


def test_fx_lookup_tz_aware_converts_to_utc_before_naive_lookup() -> None:
    """Tz-aware timestamp (e.g. 15:00+03:00 Moscow) must convert to 12:00 UTC before naive lookup."""
    dates = pd.date_range("2025-01-01 10:00", periods=5, freq="1h")
    df = pd.DataFrame(
        {
            "Open": [0.85] * 5,
            "High": [0.86] * 5,
            "Low": [0.84] * 5,
            "Close": [0.85] * 5,
            "Volume": [1000] * 5,
        },
        index=dates,
    )

    # FX history with naive UTC timestamps
    fx_dates = pd.to_datetime(
        [
            "2025-01-01 11:00:00",
            "2025-01-01 12:00:00",
            "2025-01-01 13:00:00",
            "2025-01-01 14:00:00",
            "2025-01-01 15:00:00",
        ]
    )
    fx_rates = pd.Series([1.20, 1.25, 1.30, 1.35, 1.40], index=fx_dates, name="GBPUSD")

    tester = PatternRankingTester(
        df,
        symbol="EURGBP",
        base_currency="EUR",
        quote_currency="GBP",
        strict_fx=True,
        fx_history={"GBPUSD": fx_rates},
    )

    # Trade timestamp is in Europe/Moscow (+03:00): 15:00:00+03:00 == 12:00:00 UTC
    msk_tz = pytz.timezone("Europe/Moscow")
    trade_ts_msk = pd.Timestamp("2025-01-01 15:00:00", tz=msk_tz)

    rate, src = tester._resolve_fx_rate_with_source("GBP", "USD", timestamp=trade_ts_msk)
    # Must match 12:00 UTC (1.25), NOT 15:00 naive (1.40)!
    assert rate == 1.25, f"Expected 1.25 (at 12:00 UTC), got {rate}"
    assert src == "historical"


# ==============================================================================
# 3. 5-Day Candle Closure Skips Weekends (data.py)
# ==============================================================================


def test_5d_candle_closure_skips_weekends() -> None:
    """5d candle starting on Wednesday requires 5 trading days (Wed, Thu, Fri, Mon, Tue)."""
    loader = MarketDataLoader(symbol="AAPL", interval="5d", closed_only=True)

    # Candle starts on Wednesday 2025-01-08 00:00:00-05:00
    idx = pd.DatetimeIndex(["2025-01-08 00:00:00-05:00"])
    df = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [105.0],
            "Low": [95.0],
            "Close": [102.0],
            "Volume": [10000.0],
        },
        index=idx,
    )

    # 1. Sunday 2025-01-12 12:00 UTC (4 calendar days later): NOT closed!
    sunday_utc = pd.Timestamp("2025-01-12 12:00:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=sunday_utc)) == 0

    # 2. Monday 2025-01-13 16:30 NY (21:30 UTC, 4th trading day): NOT closed!
    monday_utc = pd.Timestamp("2025-01-13 21:30:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=monday_utc)) == 0

    # 3. Tuesday 2025-01-14 15:55 NY (20:55 UTC, 5th trading day before close): NOT closed!
    tuesday_before_close = pd.Timestamp("2025-01-14 20:55:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=tuesday_before_close)) == 0

    # 4. Tuesday 2025-01-14 16:05 NY (21:05 UTC, 5th trading day after close): IS closed!
    tuesday_after_close = pd.Timestamp("2025-01-14 21:05:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=tuesday_after_close)) == 1

    # 5. Wednesday 2025-01-15 12:00 UTC: IS closed!
    wednesday_utc = pd.Timestamp("2025-01-15 12:00:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=wednesday_utc)) == 1


# ==============================================================================
# 4. Data Gap Warning in Non-Strict validate_ohlc (data.py)
# ==============================================================================


def test_validate_ohlc_emits_warning_on_dropped_rows() -> None:
    """Dropping rows in strict=False must emit UserWarning about candle continuity."""
    dates = pd.date_range("2025-01-01", periods=5, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0, 100.0, 100.0, 100.0, 100.0],
            "High": [105.0, 80.0, 105.0, 105.0, 105.0],  # row 1 has High < Low (corrupt)
            "Low": [95.0, 95.0, 95.0, 95.0, 95.0],
            "Close": [102.0, 102.0, 102.0, 102.0, 102.0],
            "Volume": [1000, 1000, 1000, 1000, 1000],
        },
        index=dates,
    )

    with pytest.warns(UserWarning, match=r"dropped 1 invalid OHLC row"):
        cleaned = validate_ohlc(df, strict=False)

    assert len(cleaned) == 4
    assert cleaned.attrs.get("dropped_rows") == 1


# ==============================================================================
# 5. Session-End Closure for Monthly and 3-Month Candles (data.py)
# ==============================================================================


def test_monthly_candle_closure_on_last_trading_day() -> None:
    """Monthly candle must close at Friday/last session close, not wait for next month calendar boundary."""
    loader = MarketDataLoader(symbol="AAPL", interval="1mo", closed_only=True)

    # January 2025: last trading day is Friday 2025-01-31 (16:00 NY -> 21:00 UTC)
    idx_jan = pd.DatetimeIndex(["2025-01-01 00:00:00-05:00"])
    df_jan = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [105.0],
            "Low": [95.0],
            "Close": [102.0],
            "Volume": [10000.0],
        },
        index=idx_jan,
    )

    # 1. Friday Jan 31 at 15:55 NY (20:55 UTC): NOT closed
    before_close_jan = pd.Timestamp("2025-01-31 20:55:00", tz=pytz.UTC)
    assert len(loader.process(df_jan.copy(), now_utc=before_close_jan)) == 0

    # 2. Friday Jan 31 at 16:05 NY (21:05 UTC): IS closed!
    after_close_jan = pd.Timestamp("2025-01-31 21:05:00", tz=pytz.UTC)
    assert len(loader.process(df_jan.copy(), now_utc=after_close_jan)) == 1

    # 3. Saturday Feb 01 at 12:00 UTC: IS closed!
    sat_feb = pd.Timestamp("2025-02-01 12:00:00", tz=pytz.UTC)
    assert len(loader.process(df_jan.copy(), now_utc=sat_feb)) == 1


def test_monthly_candle_closure_when_month_ends_on_weekend() -> None:
    """August 2025 ends on Sunday Aug 31; candle must close on Friday Aug 29 at 16:00 NY."""
    loader = MarketDataLoader(symbol="AAPL", interval="1mo", closed_only=True)

    # August 2025 (ends on Sunday 2025-08-31; last session is Friday 2025-08-29)
    # EDT: UTC-4 -> 16:00 EDT == 20:00 UTC
    idx_aug = pd.DatetimeIndex(["2025-08-01 00:00:00-04:00"])
    df_aug = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [105.0],
            "Low": [95.0],
            "Close": [102.0],
            "Volume": [10000.0],
        },
        index=idx_aug,
    )

    # Friday Aug 29 at 15:55 EDT (19:55 UTC): NOT closed
    before_close = pd.Timestamp("2025-08-29 19:55:00", tz=pytz.UTC)
    assert len(loader.process(df_aug.copy(), now_utc=before_close)) == 0

    # Friday Aug 29 at 16:05 EDT (20:05 UTC): IS closed!
    after_close = pd.Timestamp("2025-08-29 20:05:00", tz=pytz.UTC)
    assert len(loader.process(df_aug.copy(), now_utc=after_close)) == 1

    # Saturday Aug 30: IS closed!
    saturday_utc = pd.Timestamp("2025-08-30 12:00:00", tz=pytz.UTC)
    assert len(loader.process(df_aug.copy(), now_utc=saturday_utc)) == 1

    # Sunday Aug 31: IS closed!
    sunday_utc = pd.Timestamp("2025-08-31 12:00:00", tz=pytz.UTC)
    assert len(loader.process(df_aug.copy(), now_utc=sunday_utc)) == 1


def test_3mo_quarterly_candle_closure() -> None:
    """3mo quarterly candle closes at session close of last trading day of the quarter."""
    loader = MarketDataLoader(symbol="AAPL", interval="3mo", closed_only=True)

    # Q1 2025 (Jan 1 to Mar 31; Mar 31 2025 is Monday, closes 16:00 NY -> 20:00 UTC)
    idx_q1 = pd.DatetimeIndex(["2025-01-01 00:00:00-05:00"])
    df_q1 = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [105.0],
            "Low": [95.0],
            "Close": [102.0],
            "Volume": [10000.0],
        },
        index=idx_q1,
    )

    before_close = pd.Timestamp("2025-03-31 19:55:00", tz=pytz.UTC)
    assert len(loader.process(df_q1.copy(), now_utc=before_close)) == 0

    after_close = pd.Timestamp("2025-03-31 20:05:00", tz=pytz.UTC)
    assert len(loader.process(df_q1.copy(), now_utc=after_close)) == 1


# ==============================================================================
# 6. Incomplete OHLC Rejection in process() (data.py)
# ==============================================================================


def test_process_rejects_missing_ohlc_columns() -> None:
    """MarketDataLoader.process() must reject DataFrames missing required OHLC columns."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d")
    idx = pd.date_range("2025-01-01", periods=3, freq="1D")

    # Missing 'Close'
    df_no_close = pd.DataFrame(
        {"Open": [100.0, 101.0, 102.0], "High": [105.0, 106.0, 107.0], "Low": [95.0, 96.0, 97.0]},
        index=idx,
    )
    with pytest.raises(ValueError, match=r"Missing required OHLC columns: .*Close"):
        loader.process(df_no_close)

    # Missing all OHLC columns (e.g. Volume only)
    df_volume_only = pd.DataFrame({"Volume": [1000, 2000, 3000]}, index=idx)
    with pytest.raises(ValueError, match="Missing required OHLC columns"):
        loader.process(df_volume_only)

    # Direct call to validate_ohlc with require_ohlc=True
    with pytest.raises(ValueError, match="Missing required OHLC columns"):
        validate_ohlc(df_volume_only, require_ohlc=True)


# ==============================================================================
# 7. ForexDataLoader Full Parameter Support (forex_data_loader.py)
# ==============================================================================


def test_forex_data_loader_accepts_all_parameters() -> None:
    """ForexDataLoader accepts start, end, closed_only, auto_adjust, repair without TypeError."""
    loader = ForexDataLoader(
        symbol="EURUSD",
        period="30d",
        interval="1h",
        timezone="America/New_York",
        start="2025-01-01",
        end="2025-01-31",
        auto_adjust=True,
        repair=False,
        closed_only=False,
    )

    assert loader.symbol == "EURUSD"
    assert loader.ticker == "EURUSD=X"
    assert loader.asset_type == "forex"
    assert loader.start == "2025-01-01"
    assert loader.end == "2025-01-31"
    assert loader.auto_adjust is True
    assert loader.repair is False
    assert loader.closed_only is False
