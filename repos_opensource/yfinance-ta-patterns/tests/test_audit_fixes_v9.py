"""Tests for v0.3.14 audit fixes (v9).

Covers:
1. 90m and European intraday Sharpe annualization factors (1260, 1512, 25704, 64260, 128520).
2. Suffix heuristic and currency resolution (GBp vs GBP vs USD on .L, warning on heuristic fallback).
3. Historical FX tracking and transparency (different USD PnL on different dates, fx_source tracking, strict_fx).
4. Confidence and confluence fields validation (finiteness, [0, 1] range, divergence checks, sync).
5. auto_adjust flag, use_adj_close in tester, ex-dividend comparison.
6. Confluence scoring terminology (heuristic vs uncalibrated win-rate probability).
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst
from yfinance_ta_patterns.ai.scorer import AIPatternScorer, PatternConfidenceResult, SignalGrade
from yfinance_ta_patterns.cli import get_parser
from yfinance_ta_patterns.data import MarketDataLoader, resolve_asset_currencies
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    resolve_periods_per_year,
)

# ==============================================================================
# 1. Inaccurate Sharpe Annualization Factor for 90m and European Intraday
# ==============================================================================


def test_sharpe_factors_90m_and_european_intraday() -> None:
    """Issue 1: US 90m produces 5 candles/day (1260 periods/yr).

    European 8.5h sessions produce:
    - 90m: 6 candles/day (1512 periods/yr)
    - 5m: 102 candles/day (25704 periods/yr)
    - 2m: 255 candles/day (64260 periods/yr)
    - 1m: 510 candles/day (128520 periods/yr)
    """
    # US equities 90m: 6.5h trading day -> 5 full/partial candles -> 1260
    assert resolve_periods_per_year("90m", "AAPL") == 1260.0

    # European equities: 8.5h trading day (e.g. .L, .DE, .PA)
    assert resolve_periods_per_year("90m", "VOD.L") == 1512.0
    assert resolve_periods_per_year("5m", "AZN.L") == 25704.0
    assert resolve_periods_per_year("2m", "SAP.DE") == 64260.0
    assert resolve_periods_per_year("1m", "MC.PA") == 128520.0
    assert resolve_periods_per_year("1h", "VOD.L") == 252.0 * 9.0  # 2268.0


# ==============================================================================
# 2. Stock Currency Inferred from Suffix Heuristic
# ==============================================================================


def test_currency_inference_and_metadata_override() -> None:
    """Issue 2: .L suffix heuristic emits UserWarning and can be overridden by metadata_currency."""
    # Suffix fallback triggers UserWarning
    with pytest.warns(UserWarning, match="using exchange suffix heuristic"):
        _base, quote = resolve_asset_currencies("VOD.L", metadata_currency=None)
        assert quote == "GBp"

    # Providing metadata_currency avoids warning and overrides suffix
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _base, quote = resolve_asset_currencies("VOD.L", metadata_currency="GBP")
        assert quote == "GBP"

        _base, quote = resolve_asset_currencies("VOD.L", metadata_currency="USD")
        assert quote == "USD"


def test_pence_scaling_distinguishes_gbp_from_gbx() -> None:
    """Issue 2: Pence scaling (divide by 100) applies only to GBp/GBX, not GBP or USD."""
    df_dummy = pd.DataFrame(
        {
            "Open": [100.0, 105.0],
            "High": [110.0, 115.0],
            "Low": [95.0, 100.0],
            "Close": [105.0, 110.0],
            "Volume": [1000, 1000],
        },
        index=pd.date_range("2024-01-01", periods=2, freq="1d", tz="UTC"),
    )

    tester = PatternRankingTester(df_dummy, account_currency="USD")

    # GBp -> converts via GBP with 0.01 factor: 1.28 * 0.01 = 0.0128
    rate_gbp, _ = tester._resolve_fx_rate_with_source("GBp", "USD")
    assert rate_gbp == pytest.approx(0.0128)

    # GBX -> same 0.01 factor: 0.0128
    rate_gbx, _ = tester._resolve_fx_rate_with_source("GBX", "USD")
    assert rate_gbx == pytest.approx(0.0128)

    # GBP -> standard rate: 1.28 (no 0.01 factor)
    rate_gbp_std, _ = tester._resolve_fx_rate_with_source("GBP", "USD")
    assert rate_gbp_std == pytest.approx(1.28)

    # USD -> same currency: 1.0
    rate_usd, _ = tester._resolve_fx_rate_with_source("USD", "USD")
    assert rate_usd == pytest.approx(1.0)


# ==============================================================================
# 3. Static FX Rate in Historical PnL & Report Transparency
# ==============================================================================


def test_historical_fx_rate_tracking_and_source_transparency() -> None:
    """Issue 3: Historical FX rates are applied at trade exit time, different rates yield different USD PnL."""
    dates = pd.date_range("2024-01-01", periods=10, freq="1D", tz="UTC")
    close_prices = [100.0, 110.0, 100.0, 120.0, 100.0, 110.0, 100.0, 120.0, 100.0, 100.0]
    df = pd.DataFrame(
        {
            "Open": close_prices,
            "High": [p + 5 for p in close_prices],
            "Low": [p - 5 for p in close_prices],
            "Close": close_prices,
            "Volume": [10000] * 10,
        },
        index=dates,
    )

    # Date 2: rate 1.20; Date 4: rate 1.40
    fx_history = pd.Series(
        [1.20, 1.20, 1.20, 1.40, 1.40, 1.40, 1.40, 1.40, 1.40, 1.40],
        index=dates,
        name="GBPUSD",
    )

    tester = PatternRankingTester(
        df,
        symbol="TEST.L",
        account_currency="USD",
        metadata_currency="GBP",
        fx_history=fx_history,
        initial_capital=10000.0,
        position_size=1000.0,
        execution="close",
        holding_period=1,
        commission=0.0,
        slippage=0.0,
    )

    # Test conversion with source
    conv_pnl_d2, rate_d2, src_d2 = tester._convert_pnl_with_source(100.0, 110.0, exit_time=dates[2])
    conv_pnl_d4, rate_d4, src_d4 = tester._convert_pnl_with_source(100.0, 120.0, exit_time=dates[4])

    assert rate_d2 == pytest.approx(1.20)
    assert src_d2 == "historical"
    assert conv_pnl_d2 == pytest.approx(120.0)

    assert rate_d4 == pytest.approx(1.40)
    assert src_d4 == "historical"
    assert conv_pnl_d4 == pytest.approx(140.0)

    # Differing USD profits for identical 100 GBP profit
    assert conv_pnl_d4 > conv_pnl_d2


def test_strict_fx_raises_on_missing_history() -> None:
    """Issue 3: strict_fx=True raises ValueError when historical rate is missing."""
    dates = pd.date_range("2024-01-01", periods=3, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0, 110.0, 120.0],
            "High": [105.0, 115.0, 125.0],
            "Low": [95.0, 105.0, 115.0],
            "Close": [100.0, 110.0, 120.0],
            "Volume": [1000, 1000, 1000],
        },
        index=dates,
    )

    tester = PatternRankingTester(
        df,
        symbol="TEST.L",
        account_currency="USD",
        metadata_currency="GBP",
        strict_fx=True,  # missing fx_history -> must raise ValueError
    )

    with pytest.raises(ValueError, match="strict_fx=True"):
        tester._convert_pnl_with_source(100.0, 110.0, exit_time=dates[0])


# ==============================================================================
# 4. Confidence and Confluence Fields Divergence & Range Checks
# ==============================================================================


def test_pattern_confidence_result_validation() -> None:
    """Issue 4: PatternConfidenceResult validates finite values, [0, 1] range, divergence, and synchronizes."""
    dt = pd.Timestamp("2024-01-01", tz="UTC")

    # Valid score
    res = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=dt,
        raw_signal=1,
        confidence_score=0.85,
        confluence_score=0.85,
    )
    assert res.confidence_score == pytest.approx(0.85)
    assert res.confluence_score == pytest.approx(0.85)

    # Synchronizes confidence -> confluence when confluence_score is omitted
    res_sync1 = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=dt,
        raw_signal=1,
        confidence_score=0.75,
    )
    assert res_sync1.confluence_score == pytest.approx(0.75)

    # Synchronizes confluence -> confidence when confidence_score is omitted
    res_sync2 = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=dt,
        raw_signal=1,
        confluence_score=0.65,
    )
    assert res_sync2.confidence_score == pytest.approx(0.65)

    # Conflicting scores (including one 0.0 and one non-zero) raise ValueError
    with pytest.raises(ValueError, match="Conflicting scores"):
        PatternConfidenceResult(
            pattern_name="HAMMER",
            timestamp=dt,
            raw_signal=1,
            confidence_score=0.75,
            confluence_score=0.0,
        )

    # Divergent non-zero scores raise ValueError
    with pytest.raises(ValueError, match="Conflicting scores"):
        PatternConfidenceResult(
            pattern_name="HAMMER",
            timestamp=dt,
            raw_signal=1,
            confidence_score=0.85,
            confluence_score=0.45,
        )

    # Out-of-bounds raises ValueError
    with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
        PatternConfidenceResult(
            pattern_name="HAMMER",
            timestamp=dt,
            raw_signal=1,
            confidence_score=1.5,
            confluence_score=1.5,
        )

    with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
        PatternConfidenceResult(
            pattern_name="HAMMER",
            timestamp=dt,
            raw_signal=1,
            confidence_score=-0.1,
            confluence_score=-0.1,
        )

    # Non-finite values raise ValueError
    with pytest.raises(ValueError, match="finite"):
        PatternConfidenceResult(
            pattern_name="HAMMER",
            timestamp=dt,
            raw_signal=1,
            confidence_score=float("nan"),
            confluence_score=float("nan"),
        )


# ==============================================================================
# 5. Stock Price Adjustment auto_adjust and use_adj_close
# ==============================================================================


def test_auto_adjust_and_use_adj_close() -> None:
    """Issue 5: auto_adjust is attached to DataFrame attrs, and use_adj_close uses Adj Close in backtesting."""
    dates = pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0, 102.0, 104.0, 106.0, 108.0],
            "High": [105.0, 107.0, 109.0, 111.0, 113.0],
            "Low": [95.0, 97.0, 99.0, 101.0, 103.0],
            "Close": [100.0, 102.0, 104.0, 106.0, 108.0],
            "Adj Close": [90.0, 91.8, 93.6, 95.4, 97.2],
            "Volume": [1000] * 5,
        },
        index=dates,
    )

    loader = MarketDataLoader("AAPL", auto_adjust=True)
    processed = loader.process(df.copy())
    assert processed.attrs.get("auto_adjust") is True

    # PatternRankingTester with use_adj_close=True replaces Close with Adj Close
    tester = PatternRankingTester(df.copy(), symbol="AAPL", use_adj_close=True)
    assert tester._data["Close"].iloc[0] == pytest.approx(90.0)

    # CLI parses --auto-adjust
    parser = get_parser()
    args_true = parser.parse_args(["--symbol", "AAPL", "--pattern", "HAMMER", "--auto-adjust"])
    assert args_true.auto_adjust is True

    args_false = parser.parse_args(["--symbol", "AAPL", "--pattern", "HAMMER"])
    assert args_false.auto_adjust is False


# ==============================================================================
# 6. Confluence Terminology Clarification
# ==============================================================================


def test_confluence_terminology() -> None:
    """Issue 6: AIMarketAnalyst and AIPatternScorer describe score as multi-factor confluence, not win-rate probability."""
    dates = pd.date_range("2024-01-01", periods=50, freq="1D", tz="UTC")
    closes = np.linspace(100, 150, 50)
    df = pd.DataFrame(
        {
            "Open": closes - 0.5,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": [10000] * 50,
        },
        index=dates,
    )

    analyst = AIMarketAnalyst(df, symbol="AAPL")
    dummy_result = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=dates[-1],
        raw_signal=1,
        confidence_score=0.80,
        confluence_score=0.80,
        grade=SignalGrade.STRONG,
        trend_regime="BULLISH",
        confluence_factors=["EMA alignment", "RVOL surge"],
    )
    brief = analyst.generate_brief([dummy_result])
    assert "Confluence" in brief

    # AIPatternScorer docstring verification
    assert "confluence" in AIPatternScorer.__doc__.lower()
    assert "win-rate probability" in AIPatternScorer.__doc__.lower()
