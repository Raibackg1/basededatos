"""Unit tests for AIMarketAnalyst report generation and LLM prompt formatting."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst
from yfinance_ta_patterns.ai.scorer import (
    PatternConfidenceResult,
    SignalGrade,
    TradeSetup,
)


@pytest.fixture
def sample_market_context() -> tuple[pd.DataFrame, list[PatternConfidenceResult]]:
    """Generate sample market data and scored pattern results."""
    dates = pd.date_range("2025-01-01", periods=25, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(25)],
            "High": [102.0 + i for i in range(25)],
            "Low": [99.0 + i for i in range(25)],
            "Close": [101.0 + i for i in range(25)],
            "Volume": [50000.0] * 25,
        },
        index=dates,
    )

    setup = TradeSetup(
        direction="BUY",
        entry_price=125.0,
        stop_loss=122.0,
        take_profit_1=129.5,
        take_profit_2=134.0,
        risk_reward_ratio=2.0,
        risk_per_unit=3.0,
    )

    result = PatternConfidenceResult(
        pattern_name="HAMMER",
        timestamp=dates[-1],
        raw_signal=100,
        confidence_score=0.82,
        grade=SignalGrade.EXCELLENT,
        trend_regime="STRONG_BULLISH",
        rvol=1.8,
        rsi=42.5,
        atr=2.5,
        confluence_factors=["Trend Alignment: Bullish pattern in strong uptrend"],
        risk_factors=[],
        trade_setup=setup,
    )

    return df, [result]


def test_analyst_generate_brief(
    sample_market_context: tuple[pd.DataFrame, list[PatternConfidenceResult]],
) -> None:
    """Verify markdown brief contains expected headers and metric values."""
    df, results = sample_market_context
    analyst = AIMarketAnalyst(df, results)

    brief = analyst.generate_brief("EURUSD", "1h")

    assert "# AI Technical Intelligence Brief: EURUSD (1h)" in brief
    assert "HAMMER" in brief
    assert "[EXCELLENT]" in brief
    assert "`BUY` @ `125.00`" in brief


def test_analyst_to_dict_and_json(
    sample_market_context: tuple[pd.DataFrame, list[PatternConfidenceResult]],
) -> None:
    """Verify JSON export and dictionary serialization."""
    df, results = sample_market_context
    analyst = AIMarketAnalyst(df, results)

    report_dict = analyst.to_dict("AAPL", "1d")
    assert report_dict["symbol"] == "AAPL"
    assert report_dict["timeframe"] == "1d"
    assert len(report_dict["patterns"]) == 1
    assert report_dict["patterns"][0]["pattern"] == "HAMMER"

    json_str = analyst.to_json("AAPL", "1d")
    parsed = json.loads(json_str)
    assert parsed["symbol"] == "AAPL"


def test_analyst_to_llm_prompt(
    sample_market_context: tuple[pd.DataFrame, list[PatternConfidenceResult]],
) -> None:
    """Verify LLM prompt is properly formatted for language model ingestion."""
    df, results = sample_market_context
    analyst = AIMarketAnalyst(df, results)

    prompt = analyst.to_llm_prompt("BTC-USD", "4h")
    assert "Senior Quantitative Portfolio Manager" in prompt
    assert "BTC-USD" in prompt
    assert "Key Pattern Setups" in prompt


def test_analyst_empty_results(
    sample_market_context: tuple[pd.DataFrame, list[PatternConfidenceResult]],
) -> None:
    """Analyst handles empty pattern results cleanly without error."""
    df, _ = sample_market_context
    analyst = AIMarketAnalyst(df, [])

    brief = analyst.generate_brief("SPY", "1d")
    assert "No active patterns identified" in brief


def test_analyst_auto_analyze_and_readme_flow() -> None:
    """Verify AIMarketAnalyst(data, symbol=...) with .analyze() matches README workflow."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 30,
            "High": [105.0] * 30,
            "Low": [95.0] * 30,
            "Close": [100.0] * 30,
            "Volume": [10000.0] * 30,
        },
        index=dates,
    )
    analyst = AIMarketAnalyst(df, symbol="BTC-USD", timeframe="4h")
    results = analyst.analyze(min_confidence=0.0)
    assert isinstance(results, list)

    brief = analyst.generate_brief(results)
    assert "# AI Technical Intelligence Brief: BTC-USD (4h)" in brief

    llm_prompt = analyst.to_llm_prompt(results)
    assert "BTC-USD" in llm_prompt

    json_data = analyst.to_json(results)
    parsed = json.loads(json_data)
    assert parsed["symbol"] == "BTC-USD"
    assert parsed["timeframe"] == "4h"

    # Also verify zero-argument calls work
    brief_default = analyst.generate_brief()
    assert "# AI Technical Intelligence Brief: BTC-USD (4h)" in brief_default
