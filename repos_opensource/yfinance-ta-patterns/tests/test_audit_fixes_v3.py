"""Unit and regression tests for all 8 audit fixes in v0.3.8."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.scorer import (
    AIPatternScorer,
    calc_wilder_atr,
    calc_wilder_rsi,
)
from yfinance_ta_patterns.data import (
    MarketDataLoader,
    classify_asset,
    normalize_ticker,
    resolve_asset_currencies,
)
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    resolve_periods_per_year,
)

# ==============================================================================
# 1. Foreign Stock Currency & Pence Detection
# ==============================================================================


def test_foreign_stock_currency_detection_and_pence() -> None:
    """Issue 1: Foreign stock exchange suffixes (.DE, .PA, .L, .TO, etc.) and pence (GBp) scaling."""
    assert resolve_asset_currencies("SAP.DE") == ("SAP.DE", "EUR")
    assert resolve_asset_currencies("MC.PA") == ("MC.PA", "EUR")
    assert resolve_asset_currencies("VOD.L") == ("VOD.L", "GBp")
    assert resolve_asset_currencies("SHOP.TO") == ("SHOP.TO", "CAD")
    assert resolve_asset_currencies("BHP.AX") == ("BHP.AX", "AUD")
    assert resolve_asset_currencies("7203.T") == ("7203.T", "JPY")
    assert resolve_asset_currencies("NOVN.SW") == ("NOVN.SW", "CHF")

    # Verify pence scaling in PatternRankingTester
    dates = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 10,
            "High": [105.0] * 10,
            "Low": [95.0] * 10,
            "Close": [100.0] * 10,
            "Volume": [1000] * 10,
        },
        index=dates,
    )
    tester = PatternRankingTester(
        df,
        symbol="VOD.L",
        account_currency="GBP",
        fx_rates={"GBPUSD": 1.25},
    )
    assert tester._quote_currency == "GBp"
    assert tester._account_currency == "GBP"

    # Direct pence exchange rate
    assert tester._get_fx_rate("GBp", "GBP") == 0.01
    assert tester._get_fx_rate("GBP", "GBp") == 100.0
    assert tester._get_fx_rate("GBp", "GBp") == 1.0

    # GBp to USD rate: 1 GBp = 0.01 GBP = 0.0125 USD
    assert pytest.approx(tester._get_fx_rate("GBp", "USD"), 1e-6) == 0.0125

    # PnL conversion: 100 pence profit converted to GBP should be 1.0 GBP
    converted_pnl = tester._convert_pnl_to_account_currency(100.0, 100.0)
    assert pytest.approx(converted_pnl, 1e-6) == 1.0


# ==============================================================================
# 2. Crypto Sharpe Annual Normalization for Pairs
# ==============================================================================


@pytest.mark.parametrize(
    "sym",
    [
        "BTC/GBP",
        "BTC-GBP",
        "BTCUSD",
        "ETH-BTC",
        "BTC-USDC",
        "BTCUSDT",
        "DOGEUSD",
    ],
)
def test_crypto_sharpe_annualization_pairs(sym: str) -> None:
    """Issue 2: Crypto pairs of all formats use 24/7 continuous calendar (8760 hours/yr)."""
    assert resolve_periods_per_year("1h", sym) == 8760.0
    assert resolve_periods_per_year("1d", sym) == 365.0
    assert resolve_periods_per_year("4h", sym) == 2190.0


def test_crypto_sharpe_annualization_stock_and_forex_contrast() -> None:
    """Verify stock and forex retain their standard calendars."""
    assert resolve_periods_per_year("1h", "AAPL") == 1764.0
    assert resolve_periods_per_year("1d", "AAPL") == 252.0
    assert resolve_periods_per_year("1h", "EURUSD=X") == 6240.0
    assert resolve_periods_per_year("1d", "EURUSD=X") == 260.0


# ==============================================================================
# 3. strict_fx Historical Rate Enforcement
# ==============================================================================


def test_strict_fx_rejects_missing_history_and_timestamp() -> None:
    """Issue 3: strict_fx=True forbids static rate fallback and requires fx_history and timestamp."""
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

    # 1. strict_fx=True without fx_history -> raises ValueError
    tester_strict_no_hist = PatternRankingTester(
        df,
        symbol="EURUSD=X",
        account_currency="JPY",
        strict_fx=True,
        fx_history=None,
    )
    with pytest.raises(ValueError, match="strict_fx=True requires fx_history"):
        tester_strict_no_hist._get_fx_rate("USD", "JPY", timestamp=dates[0])

    # 2. strict_fx=True with fx_history but missing timestamp -> raises ValueError
    fx_hist = {"USDJPY": pd.Series([150.0] * 5, index=dates)}
    tester_strict_with_hist = PatternRankingTester(
        df,
        symbol="EURUSD=X",
        account_currency="JPY",
        strict_fx=True,
        fx_history=fx_hist,
    )
    with pytest.raises(ValueError, match="strict_fx=True requires timestamp"):
        tester_strict_with_hist._get_fx_rate("USD", "JPY", timestamp=None)

    # 3. strict_fx=True with valid fx_history and timestamp -> returns historical rate
    rate = tester_strict_with_hist._get_fx_rate("USD", "JPY", timestamp=dates[0])
    assert rate == 150.0

    # 4. Same currency conversion always returns 1.0 even without fx_history
    assert tester_strict_no_hist._get_fx_rate("USD", "USD") == 1.0


# ==============================================================================
# 4. Session Closing Calendar for closed_only Daily Candles
# ==============================================================================


def test_closed_only_session_close_and_dst() -> None:
    """Issue 4: Daily candle availability reflects exchange closing bell and DST."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d", closed_only=True)

    # Day 1: 2024-05-15 EDT (daylight saving time). Close is 16:00 America/New_York = 20:00 UTC
    dt_day1 = pd.Timestamp("2024-05-15", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [180.0],
            "High": [185.0],
            "Low": [179.0],
            "Close": [184.0],
            "Volume": [100000],
        },
        index=[dt_day1],
    )

    # At 19:55 UTC (15:55 EDT) -> market is not closed yet
    res_before_close = loader.process(
        df,
        now_utc=pd.Timestamp("2024-05-15 19:55:00", tz="UTC"),
    )
    assert len(res_before_close) == 0

    # At 20:05 UTC (16:05 EDT) -> market closed, candle complete
    res_after_close = loader.process(
        df,
        now_utc=pd.Timestamp("2024-05-15 20:05:00", tz="UTC"),
    )
    assert len(res_after_close) == 1

    # Early close day: 2024-11-29 Black Friday. Closes at 13:00 America/New_York (EST) = 18:00 UTC
    dt_bf = pd.Timestamp("2024-11-29", tz="UTC")
    df_bf = pd.DataFrame(
        {
            "Open": [230.0],
            "High": [235.0],
            "Low": [229.0],
            "Close": [234.0],
            "Volume": [50000],
        },
        index=[dt_bf],
    )
    res_bf_early = loader.process(
        df_bf,
        now_utc=pd.Timestamp("2024-11-29 17:55:00", tz="UTC"),
    )
    assert len(res_bf_early) == 0

    res_bf_closed = loader.process(
        df_bf,
        now_utc=pd.Timestamp("2024-11-29 18:05:00", tz="UTC"),
    )
    assert len(res_bf_closed) == 1


# ==============================================================================
# 5. Unseparated Crypto Ticker Normalization in Auto Mode
# ==============================================================================


@pytest.mark.parametrize(
    ("raw", "expected_norm", "expected_base", "expected_quote"),
    [
        ("BTCUSDT", "BTC-USDT", "BTC", "USDT"),
        ("DOGEUSD", "DOGE-USD", "DOGE", "USD"),
        ("BTCUSDC", "BTC-USDC", "BTC", "USDC"),
        ("SHIBUSDT", "SHIB-USDT", "SHIB", "USDT"),
        ("SOLUSDT", "SOL-USDT", "SOL", "USDT"),
    ],
)
def test_unseparated_crypto_normalization_auto(
    raw: str, expected_norm: str, expected_base: str, expected_quote: str
) -> None:
    """Issue 5: Unseparated crypto pairs of length != 6 normalize correctly in auto mode."""
    assert normalize_ticker(raw, asset_type="auto") == expected_norm
    assert classify_asset(raw, asset_type="auto") == "crypto"
    base, quote = resolve_asset_currencies(raw, asset_type="auto")
    assert base == expected_base
    assert quote == expected_quote


# ==============================================================================
# 6. Explicit NaN / Finiteness Validation in Wilder RSI & ATR
# ==============================================================================


def test_calc_wilder_rsi_and_atr_rejects_nan() -> None:
    """Issue 6: Wilder RSI and ATR raise ValueError when price series contains NaNs or infs."""
    s_nan = pd.Series([10.0, 11.0, np.nan, 12.0, 13.0, 14.0])
    s_inf = pd.Series([10.0, 11.0, np.inf, 12.0, 13.0, 14.0])
    s_clean = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])

    with pytest.raises(ValueError, match="Input series contains NaN or infinite values"):
        calc_wilder_rsi(s_nan, period=3)

    with pytest.raises(ValueError, match="Input series contains NaN or infinite values"):
        calc_wilder_rsi(s_inf, period=3)

    with pytest.raises(ValueError, match="Input series contains NaN or infinite values"):
        calc_wilder_atr(high=s_nan, low=s_clean, close=s_clean, period=3)

    with pytest.raises(ValueError, match="Input series contains NaN or infinite values"):
        calc_wilder_atr(high=s_clean, low=s_inf, close=s_clean, period=3)


# ==============================================================================
# 7. Preserve Valid OHLC 4h-Candles with Zero Volume
# ==============================================================================


def test_resample_4h_zero_volume_candle_retained() -> None:
    """Issue 7: Valid complete 4h bar with Volume=0 is retained during resampling."""
    loader = MarketDataLoader(symbol="EURUSD=X", interval="4h", closed_only=True)
    # 4 consecutive hourly bars starting at 00:00 UTC with Volume=0
    dt_range = pd.date_range("2024-01-01 00:00", periods=4, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [1.0800, 1.0810, 1.0820, 1.0815],
            "High": [1.0820, 1.0830, 1.0825, 1.0835],
            "Low": [1.0790, 1.0805, 1.0810, 1.0800],
            "Close": [1.0810, 1.0820, 1.0815, 1.0825],
            "Volume": [0, 0, 0, 0],
        },
        index=dt_range,
    )

    res = loader.process(
        df,
        now_utc=pd.Timestamp("2024-01-01 05:00:00", tz="UTC"),
    )
    assert len(res) == 1
    candle = res.iloc[0]
    assert candle["Open"] == 1.0800
    assert candle["High"] == 1.0835
    assert candle["Low"] == 1.0790
    assert candle["Close"] == 1.0825
    assert candle["Volume"] == 0


# ==============================================================================
# 8. Index Uniqueness, Monotonicity & OHLC Validation in AIPatternScorer
# ==============================================================================


def test_ai_pattern_scorer_rejects_duplicate_index_and_nan() -> None:
    """Issue 8: AIPatternScorer rejects duplicate timestamps, unsorted indices, missing OHLC, and NaNs."""
    dt_range = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")

    # 1. Duplicate index (6 bars with two duplicate timestamps)
    dup_index = list(dt_range)
    dup_index[1] = dup_index[0]  # duplicate at index 0 and 1
    df_dup = pd.DataFrame(
        {
            "Open": [10.0] * 6,
            "High": [11.0] * 6,
            "Low": [9.0] * 6,
            "Close": [10.5] * 6,
            "Volume": [100] * 6,
        },
        index=dup_index,
    )
    with pytest.raises(ValueError, match="DataFrame index contains duplicate timestamps"):
        AIPatternScorer(df_dup)

    # 2. Non-monotonic index
    non_mono_index = list(dt_range)
    non_mono_index[2], non_mono_index[3] = non_mono_index[3], non_mono_index[2]
    df_non_mono = pd.DataFrame(
        {
            "Open": [10.0] * 6,
            "High": [11.0] * 6,
            "Low": [9.0] * 6,
            "Close": [10.5] * 6,
            "Volume": [100] * 6,
        },
        index=non_mono_index,
    )
    with pytest.raises(ValueError, match="DataFrame index must be monotonically increasing"):
        AIPatternScorer(df_non_mono)

    # 3. Missing OHLC columns (at least 6 rows)
    df_missing_col = pd.DataFrame(
        {
            "Open": [10.0] * 6,
            "High": [11.0] * 6,
            "Volume": [100] * 6,
        },
        index=dt_range,
    )
    with pytest.raises(ValueError, match="DataFrame must contain required column"):
        AIPatternScorer(df_missing_col)

    # 4. NaN in OHLC
    df_nan = pd.DataFrame(
        {
            "Open": [10.0] * 6,
            "High": [11.0] * 6,
            "Low": [9.0, 9.0, np.nan, 9.0, 9.0, 9.0],
            "Close": [10.5] * 6,
            "Volume": [100] * 6,
        },
        index=dt_range,
    )
    with pytest.raises(ValueError, match="DataFrame contains NaN or infinite values in OHLC"):
        AIPatternScorer(df_nan)
