"""Unit tests for AIPatternScorer and TradeSetup calculations."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.scorer import (
    AIPatternScorer,
    PatternConfidenceResult,
    SignalGrade,
    TradeSetup,
)


@pytest.fixture
def sample_market_data() -> pd.DataFrame:
    """Generate realistic synthetic OHLCV data for testing."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D", tz="UTC")
    np.random.seed(42)

    # Steady upward trending prices
    closes = np.linspace(100.0, 130.0, 30) + np.random.normal(0, 0.5, 30)
    highs = closes + np.random.uniform(0.5, 2.0, 30)
    lows = closes - np.random.uniform(0.5, 2.0, 30)
    opens = (highs + lows) / 2.0
    volumes = np.full(30, 100000.0)
    # Give the last candle high volume surge
    volumes[-1] = 250000.0

    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )


def test_scorer_indicator_initialization(sample_market_data: pd.DataFrame) -> None:
    """Verify indicators (EMA, RSI, ATR, RVOL) are computed properly."""
    scorer = AIPatternScorer(sample_market_data)

    assert "_EMA20" in scorer.df.columns
    assert "_EMA50" in scorer.df.columns
    assert "_EMA200" in scorer.df.columns
    assert "_ATR14" in scorer.df.columns
    assert "_RSI14" in scorer.df.columns
    assert "_RVOL" in scorer.df.columns

    # Assert valid ranges on non-NaN values
    valid_rsi = scorer.df["_RSI14"].dropna()
    assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()
    valid_atr = scorer.df["_ATR14"].dropna()
    assert (valid_atr > 0).all()
    assert (scorer.df["_RVOL"] > 0).all()
    # Confirm initial lookback period is NaN as per TA-Lib invariants
    assert np.isnan(scorer.df["_RSI14"].iloc[:14]).all()
    assert np.isnan(scorer.df["_ATR14"].iloc[:14]).all()


def test_bullish_signal_with_strong_confluence(sample_market_data: pd.DataFrame) -> None:
    """A bullish signal with trend and volume surge should receive high confidence."""
    scorer = AIPatternScorer(sample_market_data)
    last_ts = sample_market_data.index[-1]

    result = scorer.score_signal("HAMMER", last_ts, raw_signal=100)

    assert isinstance(result, PatternConfidenceResult)
    assert result.pattern_name == "HAMMER"
    assert result.confidence_score >= 0.65
    assert result.grade in (SignalGrade.MODERATE, SignalGrade.STRONG, SignalGrade.EXCELLENT)
    assert len(result.confluence_factors) > 0

    assert result.trade_setup is not None
    assert isinstance(result.trade_setup, TradeSetup)
    assert result.trade_setup.direction == "BUY"
    assert result.trade_setup.stop_loss < result.trade_setup.entry_price
    assert result.trade_setup.take_profit_1 > result.trade_setup.entry_price
    assert result.trade_setup.take_profit_2 > result.trade_setup.take_profit_1


def test_counter_trend_bearish_signal_penalized(sample_market_data: pd.DataFrame) -> None:
    """A counter-trend signal in a strong uptrend should receive a confidence penalty."""
    scorer = AIPatternScorer(sample_market_data)
    last_ts = sample_market_data.index[-2]  # normal volume candle

    result = scorer.score_signal("SHOOTINGSTAR", last_ts, raw_signal=-100)

    assert result.trade_setup is not None
    assert result.trade_setup.direction == "SELL"
    # Should flag counter-trend risk
    assert any("Counter-Trend Warning" in r for r in result.risk_factors)
    assert result.trade_setup.stop_loss > result.trade_setup.entry_price


def test_score_all_signals_filtering(sample_market_data: pd.DataFrame) -> None:
    """Test evaluating multiple signals with min_confidence threshold."""
    scorer = AIPatternScorer(sample_market_data)
    signals = pd.Series(0, index=sample_market_data.index)
    signals.iloc[10] = 100
    signals.iloc[-1] = 100

    results = scorer.score_all_signals(signals, "CDLHAMMER", min_confidence=0.50)
    assert len(results) >= 1
    assert all(r.confidence_score >= 0.50 for r in results)


def test_scorer_raises_on_too_few_rows() -> None:
    """Scorer requires at least 5 candles."""
    df_tiny = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [1, 1], "Close": [2, 2]})
    with pytest.raises(ValueError, match="at least 5 candles"):
        AIPatternScorer(df_tiny)


def test_score_all_active_and_properties(sample_market_data: pd.DataFrame) -> None:
    """Verify score_all_active and convenience alias properties match README."""
    scorer = AIPatternScorer(sample_market_data)
    results = scorer.score_all_active(min_confidence=0.0)
    assert isinstance(results, list)
    if results:
        sig = results[0]
        assert sig.confidence == sig.confidence_score
        assert sig.setup == sig.trade_setup
        assert sig.confluences == sig.confluence_factors
        assert sig.risks == sig.risk_factors
        assert sig.action in ("BUY", "SELL", "HOLD")
