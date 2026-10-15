"""Tests for Audit Fixes v12:
1. Timezone Incompatibility in Date Filter (pattern_analyzer.py:44)
2. Static FX Rates Distorting Historical P&L & FX Source Transparency (pattern_tester.py:559)
3. European 4h Annualization Factor recalculated to 756.0 (pattern_tester.py:155)
4. Missing OHLC Validation in PatternAnalyzer (pattern_analyzer.py:20)
5. Fractional and Boolean lookback_bars Type Validation (ai/scorer.py:637, ai/analyst.py:66)
6. Asset Type Validation across data and tester (data.py:496, data.py:214, pattern_tester.py)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst
from yfinance_ta_patterns.ai.scorer import AIPatternScorer
from yfinance_ta_patterns.data import (
    ALLOWED_ASSET_TYPES,
    MarketDataLoader,
    classify_asset,
    normalize_ticker,
    validate_asset_type,
)
from yfinance_ta_patterns.pattern_analyzer import PatternAnalyzer
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    resolve_periods_per_year,
)


@pytest.fixture
def valid_ohlc_df() -> pd.DataFrame:
    """Fixture providing valid monotonic OHLC data."""
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
# 1. Timezone Incompatibility in Date Filter (pattern_analyzer.py)
# ==============================================================================


def test_tz_aware_date_against_tz_naive_index_raises_value_error(valid_ohlc_df):
    """Test that passing a timezone-aware date string against tz-naive index raises ValueError."""
    analyzer = PatternAnalyzer(valid_ohlc_df)
    # valid_ohlc_df has tz-naive index
    with pytest.raises(ValueError, match="Timezone-aware date requires a timezone-aware index"):
        analyzer.get_signals("DOJI", date="2025-01-15T00:00:00Z")

    with pytest.raises(ValueError, match="Timezone-aware date requires a timezone-aware index"):
        analyzer.get_signals("DOJI", start_date="2025-01-10T00:00:00+00:00")

    with pytest.raises(ValueError, match="Timezone-aware date requires a timezone-aware index"):
        analyzer.get_signals("DOJI", end_date="2025-01-20T00:00:00+03:00")


def test_tz_aware_date_against_tz_aware_index_succeeds(valid_ohlc_df):
    """Test that timezone-aware date strings work smoothly when index is timezone-aware."""
    tz_df = valid_ohlc_df.copy()
    tz_df.index = tz_df.index.tz_localize("UTC")
    analyzer = PatternAnalyzer(tz_df)

    # Filtering with tz-aware string matching UTC
    signals = analyzer.get_signals("DOJI", date="2025-01-15T00:00:00Z")
    assert isinstance(signals, pd.Series)

    # Filtering with tz-aware string in another timezone (e.g. +05:00) converts properly
    signals_converted = analyzer.get_signals("DOJI", date="2025-01-15T05:00:00+05:00")
    assert isinstance(signals_converted, pd.Series)


def test_tz_naive_date_against_tz_aware_index_localizes(valid_ohlc_df):
    """Test that tz-naive date string is localized to the index timezone."""
    tz_df = valid_ohlc_df.copy()
    tz_df.index = tz_df.index.tz_localize("America/New_York")
    analyzer = PatternAnalyzer(tz_df)

    signals = analyzer.get_signals("DOJI", date="2025-01-15")
    assert isinstance(signals, pd.Series)


# ==============================================================================
# 2. PatternAnalyzer OHLC Input Validation (pattern_analyzer.py:20)
# ==============================================================================


def test_pattern_analyzer_non_dataframe_raises():
    """Test that non-DataFrame input raises TypeError."""
    with pytest.raises(TypeError, match="data must be a pandas DataFrame"):
        PatternAnalyzer({"Open": [1, 2]})  # type: ignore


def test_pattern_analyzer_missing_columns():
    """Test that missing OHLC columns raise ValueError."""
    df = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5]})
    with pytest.raises(ValueError, match=r"Missing OHLC columns: \['Close'\]"):
        PatternAnalyzer(df)


def test_pattern_analyzer_non_numeric_columns():
    """Test that non-numeric column data types raise TypeError."""
    dates = pd.date_range("2025-01-01", periods=3)
    df = pd.DataFrame(
        {
            "Open": ["100.0", "101.0", "102.0"],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
        },
        index=dates,
    )
    with pytest.raises(TypeError, match="Column 'Open' must be numeric"):
        PatternAnalyzer(df)


def test_pattern_analyzer_non_finite_values():
    """Test that NaN or inf values in OHLC raise ValueError."""
    dates = pd.date_range("2025-01-01", periods=3)
    # NaN
    df_nan = pd.DataFrame(
        {
            "Open": [100.0, np.nan, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
        },
        index=dates,
    )
    with pytest.raises(ValueError, match=r"OHLC data contains non-finite values \(NaN or inf\)"):
        PatternAnalyzer(df_nan)

    # Inf
    df_inf = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, np.inf, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
        },
        index=dates,
    )
    with pytest.raises(ValueError, match=r"OHLC data contains non-finite values \(NaN or inf\)"):
        PatternAnalyzer(df_inf)


def test_pattern_analyzer_duplicate_timestamps():
    """Test that duplicate timestamps in the index raise ValueError."""
    dates = pd.to_datetime(["2025-01-01", "2025-01-01", "2025-01-02"])
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
        },
        index=dates,
    )
    with pytest.raises(ValueError, match="Index contains duplicate timestamps"):
        PatternAnalyzer(df)


def test_pattern_analyzer_unsorted_index():
    """Test that non-monotonic index timestamps raise ValueError."""
    dates = pd.to_datetime(["2025-01-02", "2025-01-01", "2025-01-03"])
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [95.0, 96.0, 97.0],
            "Close": [102.0, 103.0, 104.0],
        },
        index=dates,
    )
    with pytest.raises(
        ValueError, match="Index must be monotonically sorted in chronological order"
    ):
        PatternAnalyzer(df)


def test_pattern_analyzer_empty_dataframe():
    """Test that empty DataFrame with OHLC columns initializes and returns empty Series cleanly."""
    empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    analyzer = PatternAnalyzer(empty_df)
    signals = analyzer.get_signals("DOJI")
    assert signals.empty
    assert signals.name == "CDLDOJI"


# ==============================================================================
# 3. Static FX Rates & FX Source Transparency (pattern_tester.py:559)
# ==============================================================================


def test_fx_source_in_export_results_and_comparison_report(tmp_path):
    """Test that FX Source is included in export_results and get_comparison_report."""
    dates = pd.date_range("2025-01-01", periods=20, freq="1D")
    opens = np.full(20, 100.0)
    highs = np.full(20, 110.0)
    lows = np.full(20, 90.0)
    closes = np.full(20, 105.0)
    opens[10] = 100.0
    closes[10] = 100.0  # Doji
    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
        },
        index=dates,
    )
    tester = PatternRankingTester(df, symbol="AAPL", account_currency="USD")
    res = tester.test_pattern("CDLDOJI")
    assert res is not None

    # 1. export_results CSV verification
    csv_file = tmp_path / "ranking.csv"
    tester.export_results(str(csv_file))
    exported_df = pd.read_csv(csv_file)
    assert "FX Source" in exported_df.columns
    assert (exported_df["FX Source"] == "same_currency").all()

    # 2. get_comparison_report verification
    comp_df = tester.get_comparison_report()
    assert "FX Source" in comp_df.columns


def test_strict_fx_halts_on_missing_historical_rate():
    """Test that strict_fx=True raises ValueError if historical FX rate is missing for timestamp."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 10,
            "High": [105.0] * 10,
            "Low": [95.0] * 10,
            "Close": [100.0] * 10,
        },
        index=dates,
    )
    # Provide fx_history that does NOT cover 2025-01 dates
    fx_hist = {"EURUSD": pd.Series([1.08], index=[pd.Timestamp("2024-01-01")])}

    tester = PatternRankingTester(
        df,
        symbol="AAPL",
        base_currency="AAPL",
        quote_currency="USD",
        account_currency="EUR",
        strict_fx=True,
        fx_history=fx_hist,
    )

    with pytest.raises(ValueError, match=r"Missing historical FX rate|strict_fx=True"):
        tester._convert_pnl_with_source(100.0, 100.0, exit_time=dates[5])


def test_historical_fx_rates_affect_converted_pnl():
    """Test that two different historical rates produce distinct converted PnL."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 10,
            "High": [105.0] * 10,
            "Low": [95.0] * 10,
            "Close": [100.0] * 10,
        },
        index=dates,
    )

    fx_rate_1 = 1.05
    fx_hist_1 = {"EURUSD": pd.Series([fx_rate_1] * 10, index=dates)}
    tester_1 = PatternRankingTester(
        df,
        symbol="AAPL",
        base_currency="AAPL",
        quote_currency="USD",
        account_currency="EUR",
        fx_history=fx_hist_1,
    )
    conv_1, _, src_1 = tester_1._convert_pnl_with_source(1000.0, 150.0, dates[5])
    assert src_1 == "historical"
    assert conv_1 == pytest.approx(1000.0 / 1.05)

    fx_rate_2 = 1.35
    fx_hist_2 = {"EURUSD": pd.Series([fx_rate_2] * 10, index=dates)}
    tester_2 = PatternRankingTester(
        df,
        symbol="AAPL",
        base_currency="AAPL",
        quote_currency="USD",
        account_currency="EUR",
        fx_history=fx_hist_2,
    )
    conv_2, _, src_2 = tester_2._convert_pnl_with_source(1000.0, 150.0, dates[5])
    assert src_2 == "historical"
    assert conv_2 == pytest.approx(1000.0 / 1.35)
    assert conv_1 != conv_2


# ==============================================================================
# 4. European 4h Annualization Factor (pattern_tester.py:155)
# ==============================================================================


def test_european_4h_annualization_factor():
    """Verify European exchanges (.L, .DE, .PA, etc.) use 756.0 periods/year for 4h timeframe."""
    factor_lse = resolve_periods_per_year("4h", "VOD.L")
    assert factor_lse == 756.0  # 252.0 * 3.0

    factor_frankfurt = resolve_periods_per_year("4h", "SAP.DE")
    assert factor_frankfurt == 756.0

    factor_paris = resolve_periods_per_year("4h", "MC.PA")
    assert factor_paris == 756.0

    # Non-European stock uses standard 252 * 2 = 504.0
    factor_us = resolve_periods_per_year("4h", "AAPL")
    assert factor_us == 504.0


def test_resampled_4h_bars_per_day_is_exactly_three():
    """Simulate 1 year of LSE 30m bars resampled to 4h UTC and confirm exactly 3 bars per day."""
    all_dates = []
    for day in pd.date_range("2025-01-06", periods=5, freq="B"):
        bars = pd.date_range(
            day + pd.Timedelta(hours=8), day + pd.Timedelta(hours=16, minutes=30), freq="30min"
        )
        all_dates.extend(bars)

    df_30m = pd.DataFrame({"Close": np.arange(len(all_dates))}, index=pd.DatetimeIndex(all_dates))
    resampled_4h = df_30m.resample("4h").last().dropna()

    assert len(resampled_4h) == 5 * 3
    assert len(resampled_4h) / 5 * 252 == 756.0


# ==============================================================================
# 5. Fractional & Boolean lookback_bars Type Validation (scorer.py, analyst.py)
# ==============================================================================


def test_lookback_bars_type_validation_in_scorer(valid_ohlc_df):
    """Test that score_all_active rejects floats, booleans, and non-positive numbers."""
    scorer = AIPatternScorer(valid_ohlc_df)

    # Floats -> TypeError
    with pytest.raises(TypeError, match="lookback_bars must be an integer"):
        scorer.score_all_active(lookback_bars=1.5)  # type: ignore

    # Booleans -> TypeError
    with pytest.raises(TypeError, match="lookback_bars must be an integer"):
        scorer.score_all_active(lookback_bars=True)  # type: ignore

    with pytest.raises(TypeError, match="lookback_bars must be an integer"):
        scorer.score_all_active(lookback_bars=False)  # type: ignore

    # Strings -> TypeError
    with pytest.raises(TypeError, match="lookback_bars must be an integer"):
        scorer.score_all_active(lookback_bars="3")  # type: ignore

    # Non-positive integers -> ValueError
    with pytest.raises(ValueError, match="lookback_bars must be a positive integer"):
        scorer.score_all_active(lookback_bars=0)

    with pytest.raises(ValueError, match="lookback_bars must be a positive integer"):
        scorer.score_all_active(lookback_bars=-5)

    # Valid integers and numpy integers -> Accepted
    res_int = scorer.score_all_active(lookback_bars=1)
    assert isinstance(res_int, list)

    res_np = scorer.score_all_active(lookback_bars=np.int64(2))
    assert isinstance(res_np, list)

    res_none = scorer.score_all_active(lookback_bars=None)
    assert isinstance(res_none, list)


def test_lookback_bars_type_validation_in_analyst(valid_ohlc_df):
    """Test that AIMarketAnalyst.analyze rejects invalid lookback_bars types."""
    analyst = AIMarketAnalyst(valid_ohlc_df)

    with pytest.raises(TypeError, match="lookback_bars must be an integer"):
        analyst.analyze(lookback_bars=2.5)  # type: ignore

    with pytest.raises(TypeError, match="lookback_bars must be an integer"):
        analyst.analyze(lookback_bars=True)  # type: ignore

    with pytest.raises(ValueError, match="lookback_bars must be a positive integer"):
        analyst.analyze(lookback_bars=-1)

    res = analyst.analyze(lookback_bars=1)
    assert isinstance(res, list)


# ==============================================================================
# 6. Unknown asset_type Accepted Without Validation (data.py:496)
# ==============================================================================


def test_validate_asset_type_helper():
    """Test validate_asset_type helper directly."""
    assert validate_asset_type("stock") == "stock"
    assert validate_asset_type(" STOCK ") == "stock"
    assert validate_asset_type("crypto") == "crypto"
    assert validate_asset_type("forex") == "forex"
    assert validate_asset_type("commodity") == "commodity"
    assert validate_asset_type("index") == "index"
    assert validate_asset_type("auto") == "auto"
    assert validate_asset_type(None) == "auto"

    assert {
        "auto",
        "crypto",
        "forex",
        "stock",
        "index",
        "commodity",
    } == ALLOWED_ASSET_TYPES

    with pytest.raises(ValueError, match="Unsupported asset_type: crytpo"):
        validate_asset_type("crytpo")

    with pytest.raises(ValueError, match="Unsupported asset_type: invalid"):
        validate_asset_type("invalid")


def test_classify_asset_validates_asset_type():
    """Test classify_asset rejects typos and unknown asset types."""
    with pytest.raises(ValueError, match="Unsupported asset_type: crytpo"):
        classify_asset("BTC-USD", asset_type="crytpo")

    with pytest.raises(ValueError, match="Unsupported asset_type: forex_pair"):
        classify_asset("EURUSD=X", asset_type="forex_pair")

    assert classify_asset("AAPL", asset_type=" STOCK ") == "stock"
    assert classify_asset("BTC-USD", asset_type="crypto") == "crypto"
    assert classify_asset("EURUSD=X", asset_type="forex") == "forex"
    assert classify_asset("CL=F", asset_type="commodity") == "commodity"
    assert classify_asset("^GSPC", asset_type="index") == "index"


def test_normalize_ticker_validates_asset_type():
    """Test normalize_ticker rejects unknown asset_type."""
    with pytest.raises(ValueError, match="Unsupported asset_type: unknown"):
        normalize_ticker("AAPL", asset_type="unknown")


def test_market_data_loader_validates_asset_type():
    """Test MarketDataLoader constructor rejects invalid asset_type."""
    with pytest.raises(ValueError, match="Unsupported asset_type: crytpo"):
        MarketDataLoader("AAPL", asset_type="crytpo")


def test_pattern_ranking_tester_validates_asset_type(valid_ohlc_df):
    """Test PatternRankingTester constructor and resolve_periods_per_year reject invalid asset_type."""
    with pytest.raises(ValueError, match="Unsupported asset_type: crytpo"):
        PatternRankingTester(valid_ohlc_df, asset_type="crytpo")

    with pytest.raises(ValueError, match="Unsupported asset_type: invalid"):
        resolve_periods_per_year("1d", asset_type="invalid")
