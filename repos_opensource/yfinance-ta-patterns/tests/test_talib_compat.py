"""Tests for TA-Lib compatibility fallback engine and No-GIL multithreading."""

import concurrent.futures

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

from yfinance_ta_patterns.pattern_analyzer import PatternAnalyzer  # noqa: E402
from yfinance_ta_patterns.talib_compat import ALL_CDL_PATTERNS, TALibWrapper  # noqa: E402


@pytest.fixture
def sample_data():
    dates = pd.date_range("2025-01-01", periods=10, freq="1d")
    return pd.DataFrame(
        {
            "Open": [10.0, 10.0, 10.0, 12.0, 15.0, 10.0, 11.0, 10.0, 14.0, 10.0],
            "High": [15.0, 16.0, 12.0, 14.0, 16.0, 12.0, 13.0, 11.0, 15.0, 12.0],
            "Low": [9.0, 9.0, 8.0, 11.0, 14.0, 9.0, 10.0, 9.0, 13.0, 9.0],
            "Close": [10.0, 15.0, 10.0, 13.0, 15.0, 11.0, 12.0, 10.0, 14.0, 11.0],
        },
        index=dates,
    )


def test_fallback_has_all_61_patterns():
    wrapper = TALibWrapper(force_fallback=True)
    cdl_names = [f for f in dir(wrapper) if f.startswith("CDL")]
    assert len(cdl_names) == 61
    assert set(cdl_names) == set(ALL_CDL_PATTERNS)


def test_fallback_cdl_doji(sample_data):
    wrapper = TALibWrapper(force_fallback=True)
    res = wrapper.CDLDOJI(
        sample_data["Open"].values,
        sample_data["High"].values,
        sample_data["Low"].values,
        sample_data["Close"].values,
    )
    assert isinstance(res, np.ndarray)
    assert len(res) == len(sample_data)
    # Index 0 has Open=10, Close=10 -> Doji!
    assert res[0] == 100


def test_fallback_cdl_hammer_insufficient_history_no_signal():
    # A single isolated bar has no prior bars to judge a trend from, so with no
    # trend context available Hammer must stay silent rather than guess (matches
    # TA-Lib's own "unstable period" semantics: no signal until lookback is met).
    wrapper = TALibWrapper(force_fallback=True)
    res = wrapper.CDLHAMMER(
        np.array([100.0]),
        np.array([102.5]),
        np.array([90.0]),
        np.array([102.0]),
    )
    assert res[0] == 0


def test_fallback_cdl_hammer_hangingman_respect_prior_trend():
    # Same hammer-shaped candle (open=100, close=102, low=90, high=102.5) must read
    # as a bullish Hammer after a decline, and as a bearish Hanging Man after an
    # advance -- never both at once for the same bar (that was the bug: the shape
    # alone used to fire CDLHAMMER=+100 and CDLHANGINGMAN=-100 simultaneously,
    # regardless of what price had done beforehand).
    wrapper = TALibWrapper(force_fallback=True)
    hammer_bar = (100.0, 102.5, 90.0, 102.0)  # open, high, low, close

    def build(prior_closes):
        o = np.array([*prior_closes, hammer_bar[0]])
        h = np.array([*prior_closes, hammer_bar[1]])
        lo = np.array([*prior_closes, hammer_bar[2]])
        c = np.array([*prior_closes, hammer_bar[3]])
        return o, h, lo, c

    o, h, lo, c = build([110.0, 108.0, 106.0, 104.0, 102.5])  # decline into the bar
    assert wrapper.CDLHAMMER(o, h, lo, c)[-1] == 100
    assert wrapper.CDLHANGINGMAN(o, h, lo, c)[-1] == 0

    o, h, lo, c = build([94.0, 96.0, 98.0, 100.0, 101.5])  # advance into the bar
    assert wrapper.CDLHAMMER(o, h, lo, c)[-1] == 0
    assert wrapper.CDLHANGINGMAN(o, h, lo, c)[-1] == -100


def test_fallback_cdl_engulfing():
    wrapper = TALibWrapper(force_fallback=True)
    # Bullish engulfing: candle 1 is red (105 -> 95), candle 2 is green and engulfs (94 -> 106)
    res = wrapper.CDLENGULFING(
        np.array([105.0, 94.0]),
        np.array([106.0, 107.0]),
        np.array([94.0, 93.0]),
        np.array([95.0, 106.0]),
    )
    assert res[1] == 100


def test_pattern_analyzer_with_fallback(sample_data):
    import yfinance_ta_patterns.pattern_analyzer as pa

    orig_talib = pa.talib
    try:
        pa.talib = TALibWrapper(force_fallback=True)
        analyzer = PatternAnalyzer(sample_data)
        signals = analyzer.get_signals("DOJI")
        assert not signals.empty
        assert signals.iloc[0] == 100
    finally:
        pa.talib = orig_talib


def test_concurrent_multithreading_without_gil(sample_data):
    wrapper = TALibWrapper(force_fallback=True)

    def worker(i):
        return wrapper.CDLDOJI(
            sample_data["Open"].values,
            sample_data["High"].values,
            sample_data["Low"].values,
            sample_data["Close"].values,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(50)))

    assert len(results) == 50
    assert results[0][0] == 100


def test_fallback_cdl_piercing(sample_data):
    wrapper = TALibWrapper(force_fallback=True)
    res = wrapper.CDLPIERCING(
        sample_data["Open"].values,
        sample_data["High"].values,
        sample_data["Low"].values,
        sample_data["Close"].values,
    )
    assert isinstance(res, np.ndarray)
    assert len(res) == len(sample_data)
    assert set(np.unique(res)).issubset({-100, 0, 100})


def test_fallback_invalid_pattern_raises_error(sample_data):
    wrapper = TALibWrapper(force_fallback=True)
    with pytest.raises(AttributeError) as exc_info:
        wrapper.NON_EXISTENT_PATTERN(
            sample_data["Open"].values,
            sample_data["High"].values,
            sample_data["Low"].values,
            sample_data["Close"].values,
        )
    assert "NON_EXISTENT_PATTERN" in str(exc_info.value)


def test_all_61_patterns_in_fallback_mode(sample_data):
    wrapper = TALibWrapper(force_fallback=True)
    o = sample_data["Open"].values
    h = sample_data["High"].values
    lo = sample_data["Low"].values
    c = sample_data["Close"].values

    for pat in ALL_CDL_PATTERNS:
        fn = getattr(wrapper, pat)
        res = fn(o, h, lo, c)
        assert isinstance(res, np.ndarray), f"Expected ndarray for {pat}"
        assert len(res) == len(sample_data), f"Length mismatch for {pat}"
        unique_vals = set(np.unique(res))
        assert unique_vals.issubset({-100, -80, 0, 80, 100}), (
            f"Unexpected values in {pat}: {unique_vals}"
        )
