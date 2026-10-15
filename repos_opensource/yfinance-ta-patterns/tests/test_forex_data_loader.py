from unittest.mock import patch

import pandas as pd
import pytest

from yfinance_ta_patterns.forex_data_loader import ForexDataLoader


def test_resample_to_4h():
    loader = ForexDataLoader("EURUSD", interval="4h")
    assert loader._download_interval == "1h"

    idx = pd.date_range("2024-01-01 00:00", periods=4, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {
            "Open": [1.0, 2.0, 3.0, 4.0],
            "High": [2.0, 3.0, 5.0, 10.0],
            "Low": [1.0, 0.0, 2.0, 3.0],
            "Close": [1.1, 2.2, 3.3, 4.4],
            "Volume": [10, 20, 30, 40],
        },
        index=idx,
    )

    resampled = loader._resample_if_needed(data)
    assert len(resampled) == 1

    row = resampled.iloc[0]
    assert row["Open"] == 1.0
    assert row["High"] == 10.0
    assert row["Low"] == 0.0
    assert row["Close"] == 4.4
    assert row["Volume"] == 100


def test_process_timezone_and_multiindex():
    loader = ForexDataLoader("GBPUSD=X", timezone="Europe/London")
    assert loader.ticker == "GBPUSD=X"

    # MultiIndex columns (like yfinance download format)
    cols = pd.MultiIndex.from_tuples(
        [
            ("Open", "GBPUSD=X"),
            ("High", "GBPUSD=X"),
            ("Low", "GBPUSD=X"),
            ("Close", "GBPUSD=X"),
        ]
    )
    idx = pd.date_range("2024-01-01 12:00", periods=2, freq="1h")
    df = pd.DataFrame([[1.2, 1.25, 1.15, 1.22], [1.22, 1.28, 1.20, 1.25]], index=idx, columns=cols)

    processed = loader.process(df)
    assert list(processed.columns) == ["Open", "High", "Low", "Close"]
    assert str(processed.index.tz) == "Europe/London"


def test_get_data_with_mock():
    pytest.importorskip("yfinance")
    loader = ForexDataLoader("EURUSD", period="5d", interval="15m")
    assert loader.ticker == "EURUSD=X"

    idx = pd.date_range("2024-01-01 00:00", periods=5, freq="15min", tz="UTC")
    mock_df = pd.DataFrame(
        {
            "Open": [1.0, 1.1, 1.2, 1.3, 1.4],
            "High": [1.1, 1.2, 1.3, 1.4, 1.5],
            "Low": [0.9, 1.0, 1.1, 1.2, 1.3],
            "Close": [1.05, 1.15, 1.25, 1.35, 1.45],
            "Volume": [100, 200, 300, 400, 500],
        },
        index=idx,
    )

    with patch("yfinance.download", return_value=mock_df):
        data = loader.get_data()
        assert not data.empty
        assert len(data) == 5
        assert str(data.index.tz) == "Europe/Moscow"
