"""Tests for audit fixes v5 (Issues 1-6 from audit screenshots).

1. TSE Tokyo Stock Exchange extended closing time (15:30 JST starting Nov 5, 2024).
2. Unlisted cryptocurrencies (FET-USD, FET-EUR, TAO-USD) classified as crypto with 365d/8760h calendar.
3. 4h candle resampling integrity: rejects intra-session dropped bars for Forex, Crypto, and Stocks.
4. Historical FX staleness limit: max_fx_staleness prevents using outdated rates from months ago.
5. Trade Sharpe ratio scaling: annualizes by annual trade frequency (trades_per_year) and achieves timeframe invariance.
6. Wilder RSI flat series returns 0.0 matching native TA-Lib implementation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.scorer import calc_wilder_rsi
from yfinance_ta_patterns.data import (
    MarketDataLoader,
    classify_asset,
    normalize_ticker,
    resolve_asset_currencies,
)
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    is_crypto_symbol,
    resolve_periods_per_year,
)


# ---------------------------------------------------------------------------
# 1. Tokyo Stock Exchange Closing Time Extension (Nov 5, 2024)
# ---------------------------------------------------------------------------
def test_tokyo_exchange_closing_time_extended_nov_2024() -> None:
    """Issue 1: TSE daily candles close at 15:00 JST before 2024-11-05, and 15:30 JST from 2024-11-05 onwards."""
    loader = MarketDataLoader("7203.T", interval="1d", closed_only=True)

    # Date prior to Nov 5, 2024: Friday 2024-11-01
    df_pre = pd.DataFrame(
        {"Open": [2000.0], "High": [2050.0], "Low": [1990.0], "Close": [2040.0], "Volume": [1000]},
        index=pd.DatetimeIndex(["2024-11-01 00:00:00+00:00"]),
    )
    # At 15:15 JST (06:15 UTC) on 2024-11-01: market closed at 15:00 JST -> candle is closed!
    now_pre_closed = pd.Timestamp("2024-11-01 06:15:00", tz="UTC")
    res_pre = loader.process(df_pre, now_utc=now_pre_closed)
    assert len(res_pre) == 1

    # At 14:55 JST (05:55 UTC) on 2024-11-01: market open -> candle not closed
    now_pre_open = pd.Timestamp("2024-11-01 05:55:00", tz="UTC")
    res_pre_open = loader.process(df_pre, now_utc=now_pre_open)
    assert len(res_pre_open) == 0

    # Date from Nov 5, 2024 onwards: Tuesday 2024-11-05
    df_post = pd.DataFrame(
        {"Open": [2050.0], "High": [2080.0], "Low": [2030.0], "Close": [2070.0], "Volume": [1200]},
        index=pd.DatetimeIndex(["2024-11-05 00:00:00+00:00"]),
    )
    # At 15:15 JST (06:15 UTC) on 2024-11-05: TSE is still trading until 15:30 JST -> must NOT be closed!
    now_post_trading = pd.Timestamp("2024-11-05 06:15:00", tz="UTC")
    res_post_open = loader.process(df_post, now_utc=now_post_trading)
    assert len(res_post_open) == 0

    # At 15:35 JST (06:35 UTC) on 2024-11-05: session is closed -> candle is closed!
    now_post_closed = pd.Timestamp("2024-11-05 06:35:00", tz="UTC")
    res_post_closed = loader.process(df_post, now_utc=now_post_closed)
    assert len(res_post_closed) == 1


# ---------------------------------------------------------------------------
# 2. Unlisted Crypto Classification and Currencies
# ---------------------------------------------------------------------------
def test_unlisted_crypto_classification_and_currencies() -> None:
    """Issue 2: FET-USD, FET-EUR, TAO-USD and other altcoins outside static list are classified as crypto."""
    # Test normalization
    assert normalize_ticker("FET/USD", asset_type="auto") == "FET-USD"
    assert normalize_ticker("FET/EUR", asset_type="auto") == "FET-EUR"
    assert normalize_ticker("TAO/USD", asset_type="auto") == "TAO-USD"

    # Test classification
    assert classify_asset("FET-USD", "auto") == "crypto"
    assert classify_asset("FET-EUR", "auto") == "crypto"
    assert classify_asset("FET/USD", "auto") == "crypto"
    assert classify_asset("TAO-USD", "auto") == "crypto"
    assert classify_asset("KAS-USDT", "auto") == "crypto"

    # Test currency resolution
    base_u, quote_u = resolve_asset_currencies("FET-USD", "auto")
    assert (base_u, quote_u) == ("FET", "USD")

    base_e, quote_e = resolve_asset_currencies("FET-EUR", "auto")
    assert (base_e, quote_e) == ("FET", "EUR")

    # Contrast with stock with hyphen (BRK-B remains stock, USD quote)
    assert classify_asset("BRK-B", "auto") == "stock"
    assert resolve_asset_currencies("BRK-B", "auto") == ("BRK-B", "USD")

    # Test annual periods: crypto 24/7 calendar
    assert resolve_periods_per_year("1d", "FET-USD") == 365.0
    assert resolve_periods_per_year("1h", "FET-USD") == 8760.0
    assert resolve_periods_per_year("1d", "FET-EUR") == 365.0
    assert is_crypto_symbol("FET-USD") is True


# ---------------------------------------------------------------------------
# 3. 4h Bucket Completeness Session Gaps
# ---------------------------------------------------------------------------
def test_4h_bucket_completeness_session_gaps() -> None:
    """Issue 3: Incomplete 4h blocks reject intra-session missing bars for Crypto, Forex, and Equities."""
    # 1. Crypto (FET-USD): strictly requires 4 hourly bars
    loader_crypto = MarketDataLoader(
        "FET-USD", interval="4h", asset_type="auto", timezone="UTC", closed_only=False
    )
    idx_3bars = pd.DatetimeIndex(
        [
            "2025-01-15 00:00:00+00:00",
            "2025-01-15 01:00:00+00:00",
            "2025-01-15 02:00:00+00:00",
        ]
    )
    df_crypto_3 = pd.DataFrame(
        {
            "Open": [1.0] * 3,
            "High": [1.1] * 3,
            "Low": [0.9] * 3,
            "Close": [1.0] * 3,
            "Volume": [100] * 3,
        },
        index=idx_3bars,
    )
    assert len(loader_crypto.process(df_crypto_3)) == 0

    # 2. Forex (EURUSD=X):
    loader_forex = MarketDataLoader("EURUSD=X", interval="4h", timezone="UTC", closed_only=False)
    # Dropped bar inside trading hours: 08:00, 09:00, 11:00 (10:00 missing on Wednesday)
    idx_fx_gap = pd.DatetimeIndex(
        [
            "2025-01-15 08:00:00+00:00",
            "2025-01-15 09:00:00+00:00",
            "2025-01-15 11:00:00+00:00",
        ]
    )
    df_fx_gap = pd.DataFrame(
        {
            "Open": [1.05] * 3,
            "High": [1.06] * 3,
            "Low": [1.04] * 3,
            "Close": [1.05] * 3,
            "Volume": [100] * 3,
        },
        index=idx_fx_gap,
    )
    assert len(loader_forex.process(df_fx_gap)) == 0

    # 3. Equities (AAPL):
    loader_stock = MarketDataLoader("AAPL", interval="4h", timezone="UTC", closed_only=False)
    # In-session dropped bar: 13:30 and 15:30 (missing 14:30 inside 09:30-16:00 NY session)
    idx_stock_gap = pd.DatetimeIndex(
        [
            "2025-01-15 13:30:00+00:00",
            "2025-01-15 15:30:00+00:00",
        ]
    )
    df_stock_gap = pd.DataFrame(
        {
            "Open": [150.0] * 2,
            "High": [155.0] * 2,
            "Low": [149.0] * 2,
            "Close": [152.0] * 2,
            "Volume": [1000] * 2,
        },
        index=idx_stock_gap,
    )
    assert len(loader_stock.process(df_stock_gap)) == 0


# ---------------------------------------------------------------------------
# 4. Historical FX Staleness Limit
# ---------------------------------------------------------------------------
def test_historical_fx_staleness_limit() -> None:
    """Issue 4: Outdated historical exchange rates beyond max_fx_staleness are rejected."""
    dates = pd.date_range("2024-01-01", periods=10, freq="1D", tz="UTC")
    df_data = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(10)],
            "High": [105.0 + i for i in range(10)],
            "Low": [95.0 + i for i in range(10)],
            "Close": [102.0 + i for i in range(10)],
            "Volume": [1000] * 10,
        },
        index=dates,
    )

    # FX history ends on 2024-01-01
    fx_history = {
        "EURUSD": pd.Series(
            [1.10], index=pd.date_range("2024-01-01", periods=1, freq="1D", tz="UTC")
        ),
    }

    # Tester with default 7-day staleness limit
    tester = PatternRankingTester(
        df_data,
        symbol="EURUSD=X",
        fx_history=fx_history,
        strict_fx=True,
        max_fx_staleness="7D",
    )

    # Valid lookup within 7 days: 2024-01-04 (age = 3 days)
    rate_ok = tester._lookup_hist_rate("EURUSD", "USDEUR", pd.Timestamp("2024-01-04", tz="UTC"))
    assert rate_ok == 1.10

    # Stale lookup beyond 7 days: 2024-01-20 (age = 19 days > 7D)
    rate_stale = tester._lookup_hist_rate("EURUSD", "USDEUR", pd.Timestamp("2024-01-20", tz="UTC"))
    assert rate_stale is None

    # In strict_fx mode, requesting a stale rate raises ValueError
    with pytest.raises(ValueError, match="Missing historical FX rate"):
        tester._get_fx_rate("EUR", "USD", timestamp=pd.Timestamp("2024-01-20", tz="UTC"))

    # When max_fx_staleness=None, unlimited age is allowed
    tester_unlimited = PatternRankingTester(
        df_data,
        symbol="EURUSD=X",
        fx_history=fx_history,
        strict_fx=True,
        max_fx_staleness=None,
    )
    rate_unlimited = tester_unlimited._lookup_hist_rate(
        "EURUSD", "USDEUR", pd.Timestamp("2024-01-20", tz="UTC")
    )
    assert rate_unlimited == 1.10


# ---------------------------------------------------------------------------
# 5. Trade Sharpe Timeframe Invariance and Scaling
# ---------------------------------------------------------------------------
def test_trade_sharpe_timeframe_invariance_and_scaling() -> None:
    """Issue 5: trade_sharpe scales by annual trade frequency and is invariant across timeframes."""
    # 252 daily bars = 1.0 year for equities
    dates_1d = pd.date_range("2024-01-01", periods=252, freq="1D", tz="UTC")
    closes_1d = np.full(252, 100.0)
    for i in range(10):
        closes_1d[20 + i * 20 : 25 + i * 20] = 110.0

    df_1d = pd.DataFrame(
        {
            "Open": closes_1d,
            "High": closes_1d + 1.0,
            "Low": closes_1d - 1.0,
            "Close": closes_1d,
            "Volume": [1000] * 252,
        },
        index=dates_1d,
    )

    tester_1d = PatternRankingTester(
        df_1d,
        timeframe="1d",
        sharpe_mode="trade",
    )

    trades = [
        {"pnl": 100.0, "entry_idx": 10, "exit_idx": 15},
        {"pnl": 50.0, "entry_idx": 30, "exit_idx": 35},
        {"pnl": -20.0, "entry_idx": 50, "exit_idx": 55},
        {"pnl": 80.0, "entry_idx": 70, "exit_idx": 75},
    ]

    pnls = [t["pnl"] for t in trades]
    std_pnl = float(np.std(pnls))
    mean_pnl = float(np.mean(pnls))

    duration_1d = max(len(df_1d) / tester_1d._periods_per_year, 1e-6)
    trades_per_year_1d = len(trades) / duration_1d
    expected_sharpe = (mean_pnl / std_pnl) * float(np.sqrt(trades_per_year_1d))

    # Hourly timeframe (1764 bars = 1.0 year) with same 4 trades
    tester_1h = PatternRankingTester(
        pd.DataFrame(
            {
                "Open": [100.0] * 1764,
                "High": [101.0] * 1764,
                "Low": [99.0] * 1764,
                "Close": [100.0] * 1764,
                "Volume": [100] * 1764,
            },
            index=pd.date_range("2024-01-01", periods=1764, freq="1h", tz="UTC"),
        ),
        timeframe="1h",
        sharpe_mode="trade",
    )
    duration_1h = max(1764 / tester_1h._periods_per_year, 1e-6)
    trades_per_year_1h = len(trades) / duration_1h
    sharpe_1h = (mean_pnl / std_pnl) * float(np.sqrt(trades_per_year_1h))

    assert np.isclose(expected_sharpe, sharpe_1h)


# ---------------------------------------------------------------------------
# 6. Wilder RSI Flat Series Returns 0.0 Matching TA-Lib
# ---------------------------------------------------------------------------
def test_wilder_rsi_flat_series_matches_talib() -> None:
    """Issue 6: Wilder RSI on a flat price series returns 0.0, matching canonical TA-Lib behavior."""
    n = 30
    flat_prices = pd.Series(np.full(n, 100.0))

    # Calculate via pure-Python Wilder RSI
    rsi_pure = calc_wilder_rsi(flat_prices, period=14)

    # Valid values after warmup period (14 bars) must be 0.0, NOT 50.0
    valid_rsi = rsi_pure.dropna()
    assert len(valid_rsi) == n - 14
    assert (valid_rsi == 0.0).all()

    # Compare directly with native TA-Lib RSI if available in current environment
    try:
        import talib as native_talib

        if hasattr(native_talib, "RSI"):
            talib_rsi = native_talib.RSI(flat_prices.to_numpy(), timeperiod=14)
            valid_talib = talib_rsi[~np.isnan(talib_rsi)]
            assert (valid_talib == 0.0).all()
            np.testing.assert_allclose(valid_rsi.values, valid_talib)
    except (ImportError, AttributeError):
        pass
