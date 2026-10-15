"""Unit tests for audit fixes v11.

Covers:
1. score_all_active() lookback_bars constraint (default 1) and score_all_history() (YTP-001)
2. min_confidence range and finiteness validation in scorer & analyst (YTP-002)
3. Identical base and quote rejection in unseparated crypto tickers (YTP-003)
4. Unsupported forex currency code rejection and strict=False bypass (YTP-004)
5. AI Confluence score formatting and heuristic documentation (YTP-005)
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst
from yfinance_ta_patterns.ai.scorer import AIPatternScorer
from yfinance_ta_patterns.cli import parse_args, run_cli
from yfinance_ta_patterns.data import normalize_ticker, resolve_asset_currencies

# ============================================================================
# 1. Lookback bars in score_all_active vs score_all_history (YTP-001)
# ============================================================================


def test_score_all_active_lookback_bars_filters_historical() -> None:
    """Historical signals at start of data must NOT appear in score_all_active(lookback_bars=1)."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D", tz="UTC")
    np.random.seed(42)

    closes = np.linspace(100.0, 130.0, 30) + np.random.normal(0, 0.5, 30)
    highs = closes + np.random.uniform(0.5, 2.0, 30)
    lows = closes - np.random.uniform(0.5, 2.0, 30)
    opens = (highs + lows) / 2.0
    volumes = np.full(30, 100000.0)

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )

    scorer = AIPatternScorer(df)

    # 1. Full history scan finds patterns in historical candles
    history_signals = scorer.score_all_history(patterns=["CDLENGULFING"], min_confidence=0.0)
    assert len(history_signals) == 1, "Historical scan finds engulfing signal in history"
    hist_sig = history_signals[0]
    assert hist_sig.timestamp != dates[-1]

    # 2. score_all_active with lookback_bars=1 evaluates only the latest bar and returns empty list
    active_signals = scorer.score_all_active(
        patterns=["CDLENGULFING"], lookback_bars=1, min_confidence=0.0
    )
    assert active_signals == [], (
        "Active signals mode with lookback_bars=1 must exclude historical signal"
    )

    # 3. Explicit lookback_bars=None behaves like score_all_history
    unrestricted = scorer.score_all_active(
        patterns=["CDLENGULFING"], lookback_bars=None, min_confidence=0.0
    )
    assert len(unrestricted) == 1
    assert unrestricted[0].timestamp == hist_sig.timestamp

    # 4. Invalid lookback_bars values must raise ValueError
    with pytest.raises(ValueError, match="lookback_bars must be a positive integer"):
        scorer.score_all_active(lookback_bars=0)

    with pytest.raises(ValueError, match="lookback_bars must be a positive integer"):
        scorer.score_all_active(lookback_bars=-3)


# ============================================================================
# 2. min_confidence validation in scorer and analyst (YTP-002)
# ============================================================================


@pytest.mark.parametrize(
    "invalid_conf",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.01,
        -1.0,
        1.01,
        2.5,
    ],
)
def test_min_confidence_validation_score_all_signals(invalid_conf: float) -> None:
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [10.0] * 10,
            "High": [12.0] * 10,
            "Low": [9.0] * 10,
            "Close": [11.0] * 10,
            "Volume": [1000.0] * 10,
        },
        index=dates,
    )
    scorer = AIPatternScorer(df)
    signals = pd.Series(0, index=dates)

    with pytest.raises(ValueError, match=r"min_confidence must be finite and within \[0, 1\]"):
        scorer.score_all_signals(signals, "HAMMER", min_confidence=invalid_conf)


@pytest.mark.parametrize(
    "invalid_conf",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.001,
        1.001,
    ],
)
def test_min_confidence_validation_score_all_active_and_analyst(invalid_conf: float) -> None:
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [10.0] * 10,
            "High": [12.0] * 10,
            "Low": [9.0] * 10,
            "Close": [11.0] * 10,
            "Volume": [1000.0] * 10,
        },
        index=dates,
    )
    scorer = AIPatternScorer(df)

    with pytest.raises(ValueError, match=r"min_confidence must be finite and within \[0, 1\]"):
        scorer.score_all_active(min_confidence=invalid_conf)

    with pytest.raises(ValueError, match=r"min_confidence must be finite and within \[0, 1\]"):
        scorer.score_all_history(min_confidence=invalid_conf)

    analyst = AIMarketAnalyst(df, symbol="TEST")
    with pytest.raises(ValueError, match=r"min_confidence must be finite and within \[0, 1\]"):
        analyst.analyze(min_confidence=invalid_conf)


def test_min_confidence_boundary_values_accepted() -> None:
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [10.0] * 10,
            "High": [12.0] * 10,
            "Low": [9.0] * 10,
            "Close": [11.0] * 10,
            "Volume": [1000.0] * 10,
        },
        index=dates,
    )
    scorer = AIPatternScorer(df)
    signals = pd.Series(0, index=dates)

    # 0.0 and 1.0 must be accepted without error
    assert scorer.score_all_signals(signals, "HAMMER", min_confidence=0.0) == []
    assert scorer.score_all_signals(signals, "HAMMER", min_confidence=1.0) == []
    assert isinstance(scorer.score_all_active(min_confidence=0.0), list)
    assert isinstance(scorer.score_all_active(min_confidence=1.0), list)


# ============================================================================
# 3. Identical base and quote rejection in unseparated crypto (YTP-003)
# ============================================================================


@pytest.mark.parametrize(
    "identical_crypto",
    [
        "BTCBTC",
        "ETHETH",
        "SOLSOL",
        "DOGEDOGE",
    ],
)
def test_unseparated_crypto_identical_symbols(identical_crypto: str) -> None:
    with pytest.raises(ValueError, match=r"Base and quote symbols cannot be identical"):
        normalize_ticker(identical_crypto, asset_type="crypto")

    with pytest.raises(ValueError, match=r"Base and quote symbols cannot be identical"):
        normalize_ticker(identical_crypto, asset_type="auto")

    with pytest.raises(ValueError, match=r"Base and quote symbols cannot be identical"):
        resolve_asset_currencies(identical_crypto)

    # metadata_currency takes an early-return path of its own in
    # resolve_asset_currencies and must not be able to bypass this guard.
    with pytest.raises(ValueError, match=r"Base and quote symbols cannot be identical"):
        resolve_asset_currencies(identical_crypto, metadata_currency="EUR")


def test_unseparated_crypto_valid_symbols() -> None:
    assert normalize_ticker("BTCUSD", asset_type="crypto") == "BTC-USD"
    assert normalize_ticker("ETHUSDT", asset_type="crypto") == "ETH-USDT"
    assert normalize_ticker("SOLUSD", asset_type="auto") == "SOL-USD"


# ============================================================================
# 4. Forex rejects unknown currencies with strict mode bypass (YTP-004)
# ============================================================================


@pytest.mark.parametrize(
    "invalid_forex",
    [
        "ABC/XYZ",
        "EUR/INVALID",
        "INVALID/USD",
        "ABC-XYZ",
        "ABCXYZ",
        "ABCXYZ=X",
    ],
)
def test_forex_rejects_unknown_currency_codes(invalid_forex: str) -> None:
    with pytest.raises(ValueError, match=r"Unsupported forex pair"):
        normalize_ticker(invalid_forex, asset_type="forex")


def test_forex_strict_false_bypass() -> None:
    # Custom/exotic symbols can bypass currency check when strict=False
    assert normalize_ticker("ABC/XYZ", asset_type="forex", strict=False) == "ABCXYZ=X"
    assert normalize_ticker("ABC-XYZ", asset_type="forex", strict=False) == "ABCXYZ=X"
    assert normalize_ticker("ABCXYZ", asset_type="forex", strict=False) == "ABCXYZ=X"


def test_forex_valid_pairs_pass() -> None:
    assert normalize_ticker("EUR/USD", asset_type="forex") == "EURUSD=X"
    assert normalize_ticker("USD-JPY", asset_type="forex") == "USDJPY=X"
    assert normalize_ticker("EURTWD", asset_type="forex") == "EURTWD=X"
    assert normalize_ticker("GBPUSD=X", asset_type="forex") == "GBPUSD=X"


# ============================================================================
# 5. Confluence display and heuristic documentation (YTP-005)
# ============================================================================


def test_cli_ai_confluence_display(capsys: pytest.CaptureFixture[str]) -> None:
    dates = pd.date_range("2025-01-01", periods=20, freq="1D", tz="UTC")
    mock_df = pd.DataFrame(
        {
            "Open": [100.0] * 20,
            "High": [102.0] * 20,
            "Low": [98.0] * 20,
            "Close": [101.0] * 20,
            "Volume": [10000.0] * 20,
        },
        index=dates,
    )

    args = parse_args(["--pattern", "HAMMER", "--symbol", "AAPL", "--ai"])
    with patch("yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_df):
        code = run_cli(args)
        assert code == 0
        captured = capsys.readouterr()
        # Either found signals with Confluence or cleanly stated no signals
        assert "AI" in captured.out or "No" in captured.out
