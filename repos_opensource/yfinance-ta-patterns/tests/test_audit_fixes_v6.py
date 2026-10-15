"""Regression tests for audit fixes v6 (Issues 1-6).

Covers:
1. TSE / Asian exchange daily candle session date determination in local exchange timezone (avoiding UTC previous-day shift).
2. Infinite FX rate validation in pattern_tester.py (rejecting non-finite/inf rates).
3. 4h stock candle session integrity using actual exchange trading grid without bar reuse (AAPL winter 14:30 without 15:30 rejected).
4. Non-standard Forex ticker normalization (EUR-USD -> EURUSD=X while preserving BRK-B).
5. Crypto 4h candle regular hourly grid validation (rejecting irregular timestamps 00:00, 00:10, 01:00, 02:00).
6. Crypto ticker validation for malformed slash format (BTC/USD/EXTRA, /USD, BTC/ raising ValueError).
"""

from __future__ import annotations

import pandas as pd
import pytest

from yfinance_ta_patterns.data import (
    MarketDataLoader,
    classify_asset,
    normalize_ticker,
    resolve_asset_currencies,
)
from yfinance_ta_patterns.pattern_tester import PatternRankingTester


# ---------------------------------------------------------------------------
# 1. Asian Exchange Daily Session Date Shift
# ---------------------------------------------------------------------------
def test_asian_exchange_daily_session_date_shift() -> None:
    """Issue 1: Daily candle for 7203.T with local timestamp 2026-05-19 00:00 Asia/Tokyo
    must determine session date as May 19th (not May 18th due to UTC conversion),
    and must be excluded prior to TSE close at 15:30 JST (06:30 UTC).
    """
    loader = MarketDataLoader("7203.T", interval="1d", closed_only=True)

    idx = pd.DatetimeIndex(["2026-05-19 00:00:00"], tz="Asia/Tokyo")
    df = pd.DataFrame(
        {
            "Open": [2500.0],
            "High": [2550.0],
            "Low": [2480.0],
            "Close": [2520.0],
            "Volume": [100000],
        },
        index=idx,
    )

    # At 02:00 UTC (11:00 AM JST) on 2026-05-19: TSE is still trading -> candle must NOT be closed!
    now_before_close = pd.Timestamp("2026-05-19 02:00:00", tz="UTC")
    res_before = loader.process(df, now_utc=now_before_close)
    assert len(res_before) == 0, (
        "Unclosed Asian daily candle must be excluded before exchange close"
    )

    # At 06:35 UTC (15:35 JST) on 2026-05-19: TSE closed at 15:30 JST -> candle is closed!
    now_after_close = pd.Timestamp("2026-05-19 06:35:00", tz="UTC")
    res_after = loader.process(df, now_utc=now_after_close)
    assert len(res_after) == 1, "Closed Asian daily candle must be included after exchange close"


# ---------------------------------------------------------------------------
# 2. Infinite FX Rate Validation
# ---------------------------------------------------------------------------
def test_infinite_fx_rate_rejected() -> None:
    """Issue 2: Infinite (+inf) FX rate in fx_history or fx_rates must be rejected
    and never return inf or 0.0 for inverse pairs or corrupt PnL.
    """
    ts = pd.Timestamp("2025-01-15 12:00:00", tz="UTC")
    df_data = pd.DataFrame(
        {
            "Open": [100.0, 102.0, 101.0],
            "High": [103.0, 104.0, 103.0],
            "Low": [99.0, 101.0, 100.0],
            "Close": [102.0, 101.0, 102.0],
            "Volume": [1000.0, 1000.0, 1000.0],
        },
        index=pd.date_range("2025-01-15 10:00:00", periods=3, freq="1h", tz="UTC"),
    )

    # 1. Direct pair has +inf in fx_history under strict_fx=True -> must raise ValueError
    fx_hist_inf = {
        "EURUSD": pd.Series([float("inf")], index=[ts]),
    }
    tester_strict = PatternRankingTester(
        data=df_data,
        account_currency="USD",
        base_currency="EUR",
        quote_currency="EUR",
        fx_history=fx_hist_inf,
        strict_fx=True,
    )
    with pytest.raises(ValueError, match=r"Missing historical FX rate|Unable to convert"):
        tester_strict._get_fx_rate("EUR", "USD", ts)

    # 2. Inverse pair has +inf in fx_history under strict_fx=True -> must NOT return 1.0 / inf = 0.0
    fx_hist_inv_inf = {
        "USDEUR": pd.Series([float("inf")], index=[ts]),
    }
    tester_inv = PatternRankingTester(
        data=df_data,
        account_currency="USD",
        base_currency="EUR",
        quote_currency="EUR",
        fx_history=fx_hist_inv_inf,
        strict_fx=True,
    )
    with pytest.raises(ValueError, match=r"Missing historical FX rate|Unable to convert"):
        tester_inv._get_fx_rate("EUR", "USD", ts)

    # 3. Static fx_rates dictionary with inf must be filtered out
    tester_static = PatternRankingTester(
        data=df_data,
        account_currency="USD",
        base_currency="EUR",
        quote_currency="EUR",
        fx_rates={"EURUSD": float("inf"), "USDEUR": float("inf")},
        strict_fx=False,
    )
    assert "EURUSD" not in tester_static._fx_rates


# ---------------------------------------------------------------------------
# 3. 4h Stock Candle Session Integrity (Grid without Bar Reuse)
# ---------------------------------------------------------------------------
def test_stock_4h_grid_rejects_missing_hour() -> None:
    """Issue 3: In AAPL winter 12:00-16:00 UTC bucket (session opens 14:30 UTC),
    having only 14:30 UTC must NOT satisfy both 14:00 and 15:00 slots.
    Since 15:30 UTC is missing inside trading hours, the bucket must be rejected.
    """
    loader = MarketDataLoader("AAPL", interval="4h", timezone="UTC", closed_only=False)

    # Only 14:30 UTC present in 12:00-16:00 UTC (15:30 UTC missing)
    idx_missing_1530 = pd.DatetimeIndex(["2025-01-15 14:30:00+00:00"])
    df_missing = pd.DataFrame(
        {"Open": [150.0], "High": [155.0], "Low": [149.0], "Close": [152.0], "Volume": [1000]},
        index=idx_missing_1530,
    )
    res_missing = loader.process(df_missing)
    assert len(res_missing) == 0, "4h bucket with missing intra-session 15:30 bar must be rejected"

    # Both 14:30 and 15:30 UTC present -> bucket is complete
    idx_complete = pd.DatetimeIndex(
        [
            "2025-01-15 14:30:00+00:00",
            "2025-01-15 15:30:00+00:00",
        ]
    )
    df_complete = pd.DataFrame(
        {
            "Open": [150.0, 152.0],
            "High": [155.0, 156.0],
            "Low": [149.0, 151.0],
            "Close": [152.0, 154.0],
            "Volume": [1000, 1200],
        },
        index=idx_complete,
    )
    res_complete = loader.process(df_complete)
    assert len(res_complete) == 1, "4h bucket with all session hours must be accepted"


# ---------------------------------------------------------------------------
# 4. Non-standard Forex Ticker Normalization
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "asset_type", "expected"),
    [
        ("EUR-USD", "auto", "EURUSD=X"),
        ("EUR-USD", "forex", "EURUSD=X"),
        ("GBP-JPY", "auto", "GBPJPY=X"),
        ("GBP-JPY", "forex", "GBPJPY=X"),
        ("EUR/USD", "auto", "EURUSD=X"),
        ("EUR/USD", "forex", "EURUSD=X"),
        ("BRK-B", "auto", "BRK-B"),
        ("BRK-B", "stock", "BRK-B"),
        ("BF-B", "auto", "BF-B"),
    ],
)
def test_forex_hyphen_ticker_normalization(raw: str, asset_type: str, expected: str) -> None:
    """Issue 4: Forex pairs with hyphens (EUR-USD) normalize to EURUSD=X while preserving stock tickers like BRK-B."""
    assert normalize_ticker(raw, asset_type=asset_type) == expected


# ---------------------------------------------------------------------------
# 5. Crypto 4h Candle Regular Hourly Grid Validation
# ---------------------------------------------------------------------------
def test_crypto_4h_irregular_timestamps_rejected() -> None:
    """Issue 5: Four irregular timestamps (00:00, 00:10, 01:00, 02:00) in a 4h crypto bucket
    must be rejected because 03:00 is missing and 00:10 is off-grid.
    """
    loader = MarketDataLoader("BTC-USD", interval="4h", timezone="UTC", closed_only=False)

    # 4 bars but irregular: 00:00, 00:10, 01:00, 02:00 (03:00 is missing)
    idx_irregular = pd.DatetimeIndex(
        [
            "2025-01-15 00:00:00+00:00",
            "2025-01-15 00:10:00+00:00",
            "2025-01-15 01:00:00+00:00",
            "2025-01-15 02:00:00+00:00",
        ]
    )
    df_irregular = pd.DataFrame(
        {
            "Open": [50000.0] * 4,
            "High": [50100.0] * 4,
            "Low": [49900.0] * 4,
            "Close": [50050.0] * 4,
            "Volume": [100.0] * 4,
        },
        index=idx_irregular,
    )
    res_irregular = loader.process(df_irregular)
    assert len(res_irregular) == 0, "Crypto 4h bucket with irregular timestamps must be rejected"

    # 4 regular bars: 00:00, 01:00, 02:00, 03:00 -> accepted
    idx_regular = pd.DatetimeIndex(
        [
            "2025-01-15 00:00:00+00:00",
            "2025-01-15 01:00:00+00:00",
            "2025-01-15 02:00:00+00:00",
            "2025-01-15 03:00:00+00:00",
        ]
    )
    df_regular = pd.DataFrame(
        {
            "Open": [50000.0] * 4,
            "High": [50100.0] * 4,
            "Low": [49900.0] * 4,
            "Close": [50050.0] * 4,
            "Volume": [100.0] * 4,
        },
        index=idx_regular,
    )
    res_regular = loader.process(df_regular)
    assert len(res_regular) == 1, "Crypto 4h bucket with regular hourly grid must be accepted"


# ---------------------------------------------------------------------------
# 6. Malformed Crypto Ticker Validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad_ticker",
    [
        "BTC/USD/EXTRA",
        "/USD",
        "BTC/",
        "BTC-USD-EXTRA",
        "-USD",
        "BTC-",
    ],
)
def test_crypto_malformed_ticker_raises_value_error(bad_ticker: str) -> None:
    """Issue 6: Malformed crypto tickers with extra parts or empty parts raise ValueError."""
    with pytest.raises(ValueError, match=r"Invalid crypto ticker format|Invalid ticker format"):
        normalize_ticker(bad_ticker, asset_type="crypto")


def test_crypto_valid_slash_ticker_normalizes() -> None:
    """Valid crypto slash ticker formats convert correctly."""
    assert normalize_ticker("BTC/USD", asset_type="crypto") == "BTC-USD"
    assert normalize_ticker("ETH/USD", asset_type="crypto") == "ETH-USD"


@pytest.mark.parametrize("bad_ticker", ["-USD", "BTC-", "-", "USD-"])
def test_malformed_hyphen_ticker_raises_in_auto_mode(bad_ticker: str) -> None:
    """A leading/trailing hyphen with an empty base or quote must raise in asset_type='auto'
    too, not just in the explicit 'crypto'/'forex' modes covered above -- 'auto' is the
    default used by MarketDataLoader/PatternRankingTester/classify_asset whenever the
    caller doesn't pin down an asset_type, so this is the path real callers hit. Before this
    fix, normalize_ticker('auto') silently passed these through unchanged, which made
    classify_asset guess 'crypto' and resolve_asset_currencies return an empty base or quote
    currency (e.g. resolve_asset_currencies('-USD') == ('', 'USD')).
    """
    with pytest.raises(ValueError, match="Invalid ticker format"):
        normalize_ticker(bad_ticker, asset_type="auto")
    with pytest.raises(ValueError, match="Invalid ticker format"):
        classify_asset(bad_ticker, asset_type="auto")
    with pytest.raises(ValueError, match="Invalid ticker format"):
        resolve_asset_currencies(bad_ticker, asset_type="auto")
