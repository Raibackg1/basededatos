"""Tests for Issue #12: Detection of corrupted zero-body Forex daily bars and clean 1h resampling."""

import warnings

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.data import MarketDataLoader, validate_ohlc
from yfinance_ta_patterns.forex_data_loader import ForexDataLoader


def test_validate_ohlc_zero_body_warning():
    """Issue #12: validate_ohlc emits warning when median body is < 5% of median range."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    # Simulate corrupted Yahoo Forex daily candles: Open ~ Close, large High - Low
    opens = np.full(10, 1.1000)
    closes = np.full(10, 1.1001)  # 1 pip body
    highs = np.full(10, 1.1080)  # 80 pips range
    lows = np.full(10, 1.1000)

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
        },
        index=dates,
    )

    with pytest.warns(UserWarning, match="Corrupted OHLC data anomaly detected"):
        cleaned = validate_ohlc(df, check_anomalies=True)

    assert cleaned.attrs.get("zero_body_anomaly") is True


def test_validate_ohlc_normal_candles_no_warning():
    """Issue #12: Normal candles with healthy bodies should not trigger zero-body warning."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    opens = np.linspace(100.0, 110.0, 10)
    closes = opens + 2.0  # body = 2.0
    highs = closes + 1.0  # range = 4.0
    lows = opens - 1.0

    df = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
        },
        index=dates,
    )

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        cleaned = validate_ohlc(df)

    anomaly_warnings = [
        w for w in recorded if "Corrupted OHLC data anomaly detected" in str(w.message)
    ]
    assert len(anomaly_warnings) == 0
    assert "zero_body_anomaly" not in cleaned.attrs


def test_market_data_loader_clean_forex_daily_initialization():
    """Issue #12: MarketDataLoader with clean_forex_daily=True sets up 1h download and 1D resample."""
    loader = MarketDataLoader("EURUSD=X", interval="1d", clean_forex_daily=True)
    assert loader.interval == "1d"
    assert loader.clean_forex_daily is True
    assert loader._download_interval == "1h"
    assert loader._resample_rule == "1D"

    # Default is clean_forex_daily=False -> direct 1d download
    loader_default = MarketDataLoader("EURUSD=X", interval="1d")
    assert loader_default.clean_forex_daily is False
    assert loader_default._download_interval == "1d"
    assert loader_default._resample_rule is None


def test_forex_data_loader_clean_forex_daily():
    """Issue #12: ForexDataLoader inherits and passes clean_forex_daily."""
    f_loader = ForexDataLoader("GBPUSD=X", interval="1d", clean_forex_daily=True)
    assert f_loader.clean_forex_daily is True
    assert f_loader._download_interval == "1h"
    assert f_loader._resample_rule == "1D"


def test_market_data_loader_process_clean_forex_daily():
    """Issue #12: Resampling 1h data with clean_forex_daily=True produces valid daily bars."""
    dates = pd.date_range("2025-01-06 00:00", periods=48, freq="1h", tz="UTC")
    # Hourly candles with natural movement
    base = 1.1000 + np.sin(np.linspace(0, 3.14 * 2, 48)) * 0.0050
    opens = base
    closes = base + 0.0005
    highs = np.maximum(opens, closes) + 0.0010
    lows = np.minimum(opens, closes) - 0.0010

    df_1h = pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(48, 1000),
        },
        index=dates,
    )

    loader = MarketDataLoader(
        "EURUSD=X",
        interval="1d",
        clean_forex_daily=True,
        closed_only=False,
    )
    res = loader.process(df_1h)
    assert len(res) == 2
    # Check that daily candle bodies are non-zero and healthy
    bodies = (res["Close"] - res["Open"]).abs()
    ranges = res["High"] - res["Low"]
    assert (bodies > 0).all()
    assert (bodies / ranges > 0.05).all()
