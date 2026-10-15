"""Tests for Issue #9: MultiIndex column handling in validate_ohlc and AIPatternScorer."""

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns import AIPatternScorer
from yfinance_ta_patterns.data import validate_ohlc


def test_validate_ohlc_standard_columns():
    """Regression test: standard single-level OHLC DataFrame works normally."""
    df = pd.DataFrame(
        {
            "Open": [10.0, 11.0, 12.0],
            "High": [12.0, 13.0, 14.0],
            "Low": [9.0, 10.0, 11.0],
            "Close": [11.0, 12.0, 13.0],
        }
    )
    res = validate_ohlc(df, strict=True)
    assert len(res) == 3
    assert list(res.columns) == ["Open", "High", "Low", "Close"]


def test_validate_ohlc_multiindex_level0():
    """Issue #9: yfinance >= 0.2.40 where Level 0 has OHLC and Level 1 has Ticker."""
    cols = pd.MultiIndex.from_tuples(
        [
            ("Open", "EURUSD=X"),
            ("High", "EURUSD=X"),
            ("Low", "EURUSD=X"),
            ("Close", "EURUSD=X"),
            ("Volume", "EURUSD=X"),
        ]
    )
    df = pd.DataFrame(
        [
            [1.1000, 1.1050, 1.0950, 1.1020, 1000],
            [1.1020, 1.1080, 1.1010, 1.1070, 1500],
            [1.1070, 1.1100, 1.1040, 1.1060, 1200],
        ],
        columns=cols,
    )
    # Should not raise ValueError: Operands are not aligned
    res = validate_ohlc(df, strict=True)
    assert len(res) == 3
    assert not isinstance(res.columns, pd.MultiIndex)
    assert "Open" in res.columns
    assert "High" in res.columns
    assert "Low" in res.columns
    assert "Close" in res.columns


def test_validate_ohlc_multiindex_level1():
    """Issue #9: MultiIndex where Level 0 has Ticker and Level 1 has OHLC."""
    cols = pd.MultiIndex.from_tuples(
        [
            ("EURUSD=X", "Open"),
            ("EURUSD=X", "High"),
            ("EURUSD=X", "Low"),
            ("EURUSD=X", "Close"),
        ]
    )
    df = pd.DataFrame(
        [
            [1.1000, 1.1050, 1.0950, 1.1020],
            [1.1020, 1.1080, 1.1010, 1.1070],
        ],
        columns=cols,
    )
    res = validate_ohlc(df, strict=True)
    assert len(res) == 2
    assert not isinstance(res.columns, pd.MultiIndex)
    assert "Open" in res.columns


def test_validate_ohlc_multiindex_multiple_tickers_error():
    """Issue #9: MultiIndex containing multiple tickers should raise in strict mode."""
    cols = pd.MultiIndex.from_tuples(
        [
            ("Open", "AAPL"),
            ("Open", "MSFT"),
            ("High", "AAPL"),
            ("High", "MSFT"),
            ("Low", "AAPL"),
            ("Low", "MSFT"),
            ("Close", "AAPL"),
            ("Close", "MSFT"),
        ]
    )
    df = pd.DataFrame([[100, 200, 105, 205, 95, 195, 102, 202]], columns=cols)
    with pytest.raises(ValueError, match="multiple tickers or duplicate columns"):
        validate_ohlc(df, strict=True)


def test_ai_pattern_scorer_multiindex_yfinance():
    """Issue #9: AIPatternScorer initialized with yfinance MultiIndex DataFrame."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1D", tz="UTC")
    cols = pd.MultiIndex.from_tuples(
        [
            ("Open", "EURUSD=X"),
            ("High", "EURUSD=X"),
            ("Low", "EURUSD=X"),
            ("Close", "EURUSD=X"),
            ("Volume", "EURUSD=X"),
        ]
    )
    np.random.seed(42)
    base = 1.1000 + np.cumsum(np.random.normal(0, 0.001, 30))
    data = []
    for b in base:
        o = float(b)
        c = float(b + 0.0005)
        h = float(max(o, c) + 0.0010)
        lo = float(min(o, c) - 0.0010)
        data.append([o, h, lo, c, 5000])

    df = pd.DataFrame(data, index=dates, columns=cols)

    # Must initialize without raising "ValueError: Operands are not aligned"
    scorer = AIPatternScorer(df)
    assert scorer.df is not None
    assert len(scorer.df) == 30
    assert not isinstance(scorer.df.columns, pd.MultiIndex)
