"""Unit tests for Issue #17:
Prevent auto-enabling repair=True on intraday Forex tickers and provide resilient
fallback retry when auto-repair yields empty data.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

pytest.importorskip("yfinance")

from yfinance_ta_patterns.data import MarketDataLoader
from yfinance_ta_patterns.forex_data_loader import ForexDataLoader


def _make_dummy_ohlcv(n: int = 10) -> pd.DataFrame:
    """Generate dummy OHLCV dataframe for testing."""
    index = pd.date_range("2024-01-01 09:00", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [1.08 + 0.001 * i for i in range(n)],
            "High": [1.085 + 0.001 * i for i in range(n)],
            "Low": [1.075 + 0.001 * i for i in range(n)],
            "Close": [1.082 + 0.001 * i for i in range(n)],
            "Volume": [1000] * n,
        },
        index=index,
    )


# ==============================================================================
# 1. Intraday Forex disables auto-repair even when scikit-learn is installed
# ==============================================================================


def test_repair_none_intraday_forex_disables_repair_with_sklearn() -> None:
    """Intraday Forex ticker with repair=None should use repair=False even if HAS_SKLEARN is True."""
    loader = MarketDataLoader(symbol="EURUSD=X", interval="15m")
    assert loader.repair is None

    dummy_df = _make_dummy_ohlcv(5)
    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download"
    ) as mock_dl:
        mock_dl.return_value = dummy_df
        data = loader.fetch()

        assert not data.empty
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is False


def test_forex_data_loader_intraday_disables_repair_with_sklearn() -> None:
    """ForexDataLoader with intraday interval and repair=None should pass repair=False."""
    loader = ForexDataLoader(symbol="EURUSD", interval="15m")
    assert loader.repair is None
    assert loader.ticker == "EURUSD=X"
    assert loader.asset_type == "forex"

    dummy_df = _make_dummy_ohlcv(5)
    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download"
    ) as mock_dl:
        mock_dl.return_value = dummy_df
        data = loader.fetch()

        assert not data.empty
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is False


def test_repair_none_daily_forex_keeps_repair_with_sklearn() -> None:
    """Daily Forex (interval=1d) with repair=None and HAS_SKLEARN=True keeps repair=True."""
    loader = ForexDataLoader(symbol="EURUSD", interval="1d")
    assert loader.repair is None

    dummy_df = _make_dummy_ohlcv(5)
    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download"
    ) as mock_dl:
        mock_dl.return_value = dummy_df
        data = loader.fetch()

        assert not data.empty
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is True


def test_repair_none_intraday_stock_keeps_repair_with_sklearn() -> None:
    """Intraday stock (e.g. AAPL, 15m) with repair=None and HAS_SKLEARN=True keeps repair=True."""
    loader = MarketDataLoader(symbol="AAPL", interval="15m")
    assert loader.repair is None

    dummy_df = _make_dummy_ohlcv(5)
    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download"
    ) as mock_dl:
        mock_dl.return_value = dummy_df
        data = loader.fetch()

        assert not data.empty
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is True


# ==============================================================================
# 2. Resilient fallback retry mechanism
# ==============================================================================


def test_fallback_retry_when_auto_repair_returns_empty() -> None:
    """If repair was auto-enabled (repair=None) and returns empty data, retry with repair=False."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d")
    assert loader.repair is None

    dummy_df = _make_dummy_ohlcv(5)
    # First call returns empty, second call returns valid data
    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download", side_effect=[pd.DataFrame(), dummy_df]
    ) as mock_dl:
        data = loader.fetch()

        assert not data.empty
        assert len(data) == 5
        assert mock_dl.call_count == 2
        # First call was repair=True
        assert mock_dl.call_args_list[0].kwargs.get("repair") is True
        # Second fallback call was repair=False
        assert mock_dl.call_args_list[1].kwargs.get("repair") is False


def test_fallback_retry_with_start_and_end() -> None:
    """Fallback retry preserves start and end parameters when retrying with repair=False."""
    loader = MarketDataLoader(symbol="AAPL", start="2024-01-01", end="2024-01-10")
    dummy_df = _make_dummy_ohlcv(5)

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download", side_effect=[pd.DataFrame(), dummy_df]
    ) as mock_dl:
        data = loader.fetch()

        assert not data.empty
        assert mock_dl.call_count == 2
        assert mock_dl.call_args_list[1].kwargs.get("start") == "2024-01-01"
        assert mock_dl.call_args_list[1].kwargs.get("end") == "2024-01-10"
        assert mock_dl.call_args_list[1].kwargs.get("repair") is False


def test_no_fallback_when_explicit_repair_true() -> None:
    """When repair=True is explicitly specified, do not silently fallback on empty data."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d", repair=True)

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download", return_value=pd.DataFrame()
    ) as mock_dl:
        with pytest.raises(ValueError, match="No market data found"):
            loader.fetch()

        # Should only have been called once - no retry
        assert mock_dl.call_count == 1


def test_fallback_fails_if_retry_also_empty() -> None:
    """If fallback with repair=False also returns empty DataFrame, raise ValueError."""
    loader = MarketDataLoader(symbol="NONEXISTENT", interval="1d")

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance.download", side_effect=[pd.DataFrame(), pd.DataFrame()]
    ) as mock_dl:
        with pytest.raises(
            ValueError, match="No market data found on Yahoo Finance for ticker 'NONEXISTENT'"
        ):
            loader.fetch()

        assert mock_dl.call_count == 2
