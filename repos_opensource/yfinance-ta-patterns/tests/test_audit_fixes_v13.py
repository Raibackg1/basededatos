"""Tests for Audit Fixes v13:
1. Weekly/5d Candle Premature Session Capping (data.py:1374-1405)
2. Scorer Accepts Corrupted OHLC Geometries (ai/scorer.py:273-293)
3. Unsanitized Volume Distorting RVOL (ai/scorer.py:313-323)
4. Sharpe Annualization Uses US Calendar for Asian Markets (pattern_tester.py:101-183)
5. Static FX Fallback Risk & EURGBP Regression (pattern_tester.py:259, 487-535)
6. Scorer Example EMA200 Warm-Up (README.md)
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest
import pytz

from yfinance_ta_patterns.ai.scorer import AIPatternScorer
from yfinance_ta_patterns.data import MarketDataLoader
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    resolve_periods_per_year,
)


@pytest.fixture
def valid_ohlc_df() -> pd.DataFrame:
    """Fixture providing 30 valid monotonic OHLC candles."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D")
    return pd.DataFrame(
        {
            "Open": np.full(30, 100.0),
            "High": np.full(30, 105.0),
            "Low": np.full(30, 95.0),
            "Close": np.full(30, 102.0),
            "Volume": np.full(30, 1000.0),
        },
        index=dates,
    )


# ==============================================================================
# 1. Weekly/5d Candle Premature Session Capping (data.py)
# ==============================================================================


def test_weekly_candle_closed_only_not_capped_on_first_day() -> None:
    """Weekly candle starting on Monday must not be treated as closed on Monday or Tuesday."""
    loader = MarketDataLoader(symbol="AAPL", interval="1wk", closed_only=True)

    # Weekly candle starting Monday 2025-01-06 (America/New_York session)
    idx = pd.DatetimeIndex(["2025-01-06 00:00:00-05:00"])
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

    # 1. Tuesday afternoon (2025-01-07 16:30:00 NY -> 21:30 UTC): weekly candle is NOT closed
    tuesday_utc = pd.Timestamp("2025-01-07 21:30:00", tz=pytz.UTC)
    res_tuesday = loader.process(df.copy(), now_utc=tuesday_utc)
    assert len(res_tuesday) == 0, "Weekly candle should not be considered closed on Tuesday"

    # 2. Friday before session close (2025-01-10 15:59:00 NY -> 20:59 UTC): NOT closed
    friday_before_close = pd.Timestamp("2025-01-10 20:59:00", tz=pytz.UTC)
    res_friday_before = loader.process(df.copy(), now_utc=friday_before_close)
    assert len(res_friday_before) == 0, (
        "Weekly candle should not be closed before Friday 16:00 close"
    )

    # 3. Friday after session close (2025-01-10 16:05:00 NY -> 21:05 UTC): IS closed
    friday_after_close = pd.Timestamp("2025-01-10 21:05:00", tz=pytz.UTC)
    res_friday_after = loader.process(df.copy(), now_utc=friday_after_close)
    assert len(res_friday_after) == 1, "Weekly candle should be closed after Friday 16:00 close"

    # 4. Saturday (2025-01-11 12:00:00 UTC): IS closed
    saturday_utc = pd.Timestamp("2025-01-11 12:00:00", tz=pytz.UTC)
    res_saturday = loader.process(df.copy(), now_utc=saturday_utc)
    assert len(res_saturday) == 1, "Weekly candle should be closed on Saturday"


def test_5d_candle_closed_only_multi_day_closure() -> None:
    """5d candle starting on Monday closes after 5 trading days, not on day 1."""
    loader = MarketDataLoader(symbol="AAPL", interval="5d", closed_only=True)

    idx = pd.DatetimeIndex(["2025-01-06 00:00:00-05:00"])
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

    # Tuesday: 5d candle is not closed
    tuesday_utc = pd.Timestamp("2025-01-07 21:30:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=tuesday_utc)) == 0

    # Saturday: 5d candle is closed
    saturday_utc = pd.Timestamp("2025-01-11 12:00:00", tz=pytz.UTC)
    assert len(loader.process(df.copy(), now_utc=saturday_utc)) == 1


# ==============================================================================
# 2. Strict OHLC Validation in AIPatternScorer (ai/scorer.py)
# ==============================================================================


@pytest.mark.parametrize(
    ("bad_col", "bad_val", "expected_match"),
    [
        ("High", 80.0, "Inconsistent OHLC bar geometry"),  # High < Low (Low is 95)
        ("Open", 110.0, "Inconsistent OHLC bar geometry"),  # Open > High (High is 105)
        ("Close", 110.0, "Inconsistent OHLC bar geometry"),  # Close > High
        ("Close", 80.0, "Inconsistent OHLC bar geometry"),  # Close < Low
        ("Close", -10.0, "non-positive prices found"),  # Negative price
        ("Open", 0.0, "non-positive prices found"),  # Zero price
    ],
)
def test_scorer_rejects_corrupted_ohlc_geometry(
    valid_ohlc_df: pd.DataFrame, bad_col: str, bad_val: float, expected_match: str
) -> None:
    """AIPatternScorer.__init__ must strictly raise ValueError on inverted/broken bar geometry."""
    df_corrupt = valid_ohlc_df.copy()
    df_corrupt.iloc[10, df_corrupt.columns.get_loc(bad_col)] = bad_val

    with pytest.raises(ValueError, match=expected_match):
        AIPatternScorer(df_corrupt)


# ==============================================================================
# 3. Volume Sanitization & Zero-Volume Handling (ai/scorer.py)
# ==============================================================================


def test_scorer_rejects_non_finite_volume(valid_ohlc_df: pd.DataFrame) -> None:
    """AIPatternScorer must raise ValueError if Volume contains inf or -inf."""
    df_inf = valid_ohlc_df.copy()
    df_inf.iloc[10, df_inf.columns.get_loc("Volume")] = np.inf

    with pytest.raises(
        ValueError, match="Invalid Volume values: volume must be finite and non-negative"
    ):
        AIPatternScorer(df_inf)


def test_scorer_rejects_negative_volume(valid_ohlc_df: pd.DataFrame) -> None:
    """AIPatternScorer must raise ValueError if Volume contains negative values."""
    df_neg = valid_ohlc_df.copy()
    df_neg.iloc[10, df_neg.columns.get_loc("Volume")] = -500.0

    with pytest.raises(
        ValueError, match="Invalid Volume values: volume must be finite and non-negative"
    ):
        AIPatternScorer(df_neg)


def test_scorer_zero_volume_is_neutral(valid_ohlc_df: pd.DataFrame) -> None:
    """Forex / tickless index data with all-zero volume must be accepted neutrally without error."""
    df_zero = valid_ohlc_df.copy()
    df_zero["Volume"] = 0.0

    scorer = AIPatternScorer(df_zero)
    assert (scorer.df["_RVOL"] == 1.0).all()

    # Scoring pattern on zero-volume data should not assign Volume Surge or Low Volume Warning
    scored = scorer.score_signal("CDLHAMMER", valid_ohlc_df.index[15], 100)
    for conf in scored.confluences:
        assert "Volume Surge" not in conf
        assert "Volume Confirmation" not in conf
    for risk in scored.risk_factors:
        assert "Low Volume Warning" not in risk


def test_scorer_missing_volume_is_neutral(valid_ohlc_df: pd.DataFrame) -> None:
    """DataFrame without Volume column must initialize and treat volume as neutral."""
    df_no_vol = valid_ohlc_df.drop(columns=["Volume"])
    scorer = AIPatternScorer(df_no_vol)
    assert (scorer.df["_RVOL"] == 1.0).all()


# ==============================================================================
# 4. Asian Markets Sharpe Annualization (pattern_tester.py)
# ==============================================================================


def test_asian_markets_sharpe_annualization() -> None:
    """Tokyo (.T) and Hong Kong (.HK) annualization factors must reflect true market hours."""
    # US Equities baseline: 252 days * 390 min = 98,280
    assert resolve_periods_per_year("1m", "AAPL") == 98280.0

    # Tokyo Stock Exchange (.T): 245 days
    # Post-Nov 5, 2024: 330 min/day -> 245 * 330 = 80,850.0
    post_date = datetime.date(2025, 1, 15)
    assert resolve_periods_per_year("1m", "7203.T", as_of_date=post_date) == 80850.0
    assert resolve_periods_per_year("1m", "7203.T") == 80850.0  # default today >= 2024-11-05
    assert resolve_periods_per_year("1h", "7203.T", as_of_date=post_date) == 1470.0  # 245 * 6
    assert resolve_periods_per_year("1d", "7203.T") == 245.0

    # Pre-Nov 5, 2024: 300 min/day -> 245 * 300 = 73,500.0
    pre_date = datetime.date(2024, 6, 1)
    assert resolve_periods_per_year("1m", "7203.T", as_of_date=pre_date) == 73500.0
    assert resolve_periods_per_year("1h", "7203.T", as_of_date=pre_date) == 1225.0  # 245 * 5

    # Hong Kong Stock Exchange (.HK): 250 days * 330 min = 82,500.0
    assert resolve_periods_per_year("1m", "0700.HK") == 82500.0
    assert resolve_periods_per_year("1h", "0700.HK") == 1500.0  # 250 * 6
    assert resolve_periods_per_year("1d", "0700.HK") == 250.0


def test_pattern_ranking_tester_asian_symbol_initialization(valid_ohlc_df: pd.DataFrame) -> None:
    """PatternRankingTester initializes _periods_per_year correctly for Asian tickers."""
    # Data from 2025
    tester_t = PatternRankingTester(valid_ohlc_df, symbol="7203.T", timeframe="1m")
    assert tester_t._periods_per_year == 80850.0

    tester_hk = PatternRankingTester(valid_ohlc_df, symbol="0700.HK", timeframe="1m")
    assert tester_hk._periods_per_year == 82500.0


# ==============================================================================
# 5. EURGBP Strict FX and Historical Rate Resolution (pattern_tester.py)
# ==============================================================================


def test_eurgbp_strict_fx_requires_fx_history() -> None:
    """strict_fx=True must raise ValueError if fx_history is not provided for cross pairs."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1h")
    df = pd.DataFrame(
        {
            "Open": [0.85] * 10,
            "High": [0.86] * 10,
            "Low": [0.84] * 10,
            "Close": [0.85] * 10,
            "Volume": [1000] * 10,
        },
        index=dates,
    )

    tester = PatternRankingTester(
        df,
        symbol="EURGBP",
        base_currency="EUR",
        quote_currency="GBP",
        strict_fx=True,
        fx_history=None,
    )

    with pytest.raises(ValueError, match="strict_fx=True requires fx_history"):
        tester._resolve_fx_rate_with_source("GBP", "USD", timestamp=dates[2])


def test_eurgbp_historical_rate_resolution_without_lookahead() -> None:
    """strict_fx=True with fx_history must resolve the historical rate without lookahead."""
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

    # FX history for GBPUSD with rates changing each hour
    fx_dates = pd.date_range("2025-01-01 10:00", periods=5, freq="1h")
    fx_rates = pd.Series([1.25, 1.26, 1.27, 1.28, 1.29], index=fx_dates, name="GBPUSD")

    tester = PatternRankingTester(
        df,
        symbol="EURGBP",
        base_currency="EUR",
        quote_currency="GBP",
        strict_fx=True,
        fx_history={"GBPUSD": fx_rates},
    )

    # Rate at 11:00 should resolve to 1.26 (historical rate at 11:00)
    rate_11, src_11 = tester._resolve_fx_rate_with_source("GBP", "USD", timestamp=dates[1])
    assert rate_11 == 1.26
    assert src_11 == "historical"

    # Rate at 12:00 should resolve to 1.27
    rate_12, src_12 = tester._resolve_fx_rate_with_source("GBP", "USD", timestamp=dates[2])
    assert rate_12 == 1.27
    assert src_12 == "historical"


# ==============================================================================
# 6. Scorer Example EMA200 Warm-Up (README.md)
# ==============================================================================


def test_scorer_readme_warmup_period() -> None:
    """Daily data with 60 bars leaves EMA200 uncomputed, while 250+ bars warms it up."""
    # 1. 60 daily candles leaves EMA 200 uncomputed (NaN)
    dates_60 = pd.date_range("2025-01-01", periods=60, freq="1D")
    df_60 = pd.DataFrame(
        {
            "Open": np.full(60, 100.0),
            "High": np.full(60, 105.0),
            "Low": np.full(60, 95.0),
            "Close": np.full(60, 102.0),
            "Volume": np.full(60, 1000.0),
        },
        index=dates_60,
    )

    scorer_60 = AIPatternScorer(df_60)
    assert np.isnan(scorer_60.df["_EMA200"].iloc[-1]), "60 candles should leave EMA200 as NaN"

    # 2. Insufficient history (< 14 bars) suppresses trade setup
    dates_short = pd.date_range("2025-01-01", periods=10, freq="1D")
    df_short = pd.DataFrame(
        {
            "Open": np.full(10, 100.0),
            "High": np.full(10, 105.0),
            "Low": np.full(10, 95.0),
            "Close": np.full(10, 102.0),
            "Volume": np.full(10, 1000.0),
        },
        index=dates_short,
    )
    scorer_short = AIPatternScorer(df_short)
    scored_short = scorer_short.score_signal("CDLHAMMER", df_short.index[-1], 100)
    assert scored_short.insufficient_history is True
    assert scored_short.trade_setup is None

    # 3. Full 2y data (> 200 bars) warms up EMA200
    dates_long = pd.date_range("2023-01-01", periods=300, freq="1D")
    df_long = pd.DataFrame(
        {
            "Open": np.full(300, 100.0),
            "High": np.full(300, 105.0),
            "Low": np.full(300, 95.0),
            "Close": np.full(300, 102.0),
            "Volume": np.full(300, 1000.0),
        },
        index=dates_long,
    )

    scorer_long = AIPatternScorer(df_long)
    assert not np.isnan(scorer_long.df["_EMA200"].iloc[-1]), "300 candles should warm up EMA200"
    scored_long = scorer_long.score_signal("CDLHAMMER", df_long.index[-1], 100)
    assert scored_long.insufficient_history is False
    assert scored_long.trade_setup is not None
