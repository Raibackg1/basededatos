import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.pattern_analyzer import PatternAnalyzer


@pytest.fixture
def sample_ohlc_data() -> pd.DataFrame:
    """Generate synthetic OHLC data with sufficient history for TA-Lib rolling stats."""
    dates = pd.date_range("2025-01-01", periods=20, freq="1d", tz="Europe/Moscow")
    opens = np.full(20, 100.0)
    highs = np.full(20, 110.0)
    lows = np.full(20, 90.0)
    closes = np.full(20, 105.0)

    # Place a Doji at index 15 (2025-01-16)
    opens[15] = 100.0
    closes[15] = 100.0

    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
        },
        index=dates,
    )


def test_pattern_normalization(sample_ohlc_data):
    analyzer = PatternAnalyzer(sample_ohlc_data)
    assert analyzer._normalize_pattern("hammer") == "CDLHAMMER"
    assert analyzer._normalize_pattern("CDLDOJI") == "CDLDOJI"
    assert analyzer._normalize_pattern("doji") == "CDLDOJI"


def test_unknown_pattern_raises_error(sample_ohlc_data):
    analyzer = PatternAnalyzer(sample_ohlc_data)
    with pytest.raises(ValueError, match="Unknown pattern 'NON_EXISTENT_PATTERN'"):
        analyzer.get_signals("NON_EXISTENT_PATTERN")


def test_get_signals_doji(sample_ohlc_data):
    analyzer = PatternAnalyzer(sample_ohlc_data)
    signals = analyzer.get_signals("DOJI")
    # DOJI should be detected on 2025-01-16
    assert not signals.empty
    assert pd.Timestamp("2025-01-16", tz="Europe/Moscow") in signals.index


def test_get_signals_with_date_filter(sample_ohlc_data):
    analyzer = PatternAnalyzer(sample_ohlc_data)
    # Signal exists on 2025-01-16
    signals = analyzer.get_signals("DOJI", date="2025-01-16")
    assert len(signals) == 1

    # No signal on 2025-01-01
    signals_none = analyzer.get_signals("DOJI", date="2025-01-01")
    assert signals_none.empty


def test_get_signals_with_range_filter(sample_ohlc_data):
    analyzer = PatternAnalyzer(sample_ohlc_data)
    signals = analyzer.get_signals(
        "DOJI",
        start_date="2025-01-15",
        end_date="2025-01-18",
    )
    assert not signals.empty
    assert len(signals) == 1


def test_analyze_all_for_date(sample_ohlc_data):
    analyzer = PatternAnalyzer(sample_ohlc_data)
    messages = list(analyzer.analyze_all_for_date("2025-01-16"))
    assert len(messages) > 0
    # Should have non-zero message specifically for DOJI
    doji_messages = [m for m in messages if m.startswith("DOJI on 2025-01-16 (non-zero values)")]
    assert len(doji_messages) == 1
