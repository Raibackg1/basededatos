"""Test suite for v0.3.55: PyPy 3.11+ compatibility without forced C-extension dependencies."""

import sys

import yfinance_ta_patterns as ytp
from yfinance_ta_patterns.talib_compat import ALL_CDL_PATTERNS, talib


def test_version_matches():
    assert ytp.__version__ == "0.3.60"



def test_all_61_patterns_present():
    assert len(ALL_CDL_PATTERNS) == 61
    for name in ALL_CDL_PATTERNS:
        fn = getattr(talib, name, None)
        assert callable(fn), f"Pattern {name} must be callable"


def test_pattern_execution_with_lists():
    from yfinance_ta_patterns.talib_compat import HAS_NATIVE_TALIB, TALibWrapper

    n = 30
    open_vals = [100.0 + (i * 0.1) for i in range(n)]
    high_vals = [105.0 + (i * 0.1) for i in range(n)]
    low_vals = [95.0 + (i * 0.1) for i in range(n)]
    close_vals = [102.0 + (i * 0.1) for i in range(n)]

    # Test pure-Python fallback list support directly
    fallback = TALibWrapper(force_fallback=True)
    res = fallback.CDLDOJI(open_vals, high_vals, low_vals, close_vals)
    assert len(res) == n

    # Test talib entry point based on environment
    if HAS_NATIVE_TALIB:
        import numpy as np

        res_native = talib.CDLDOJI(
            np.asarray(open_vals),
            np.asarray(high_vals),
            np.asarray(low_vals),
            np.asarray(close_vals),
        )
        assert len(res_native) == n
    else:
        res_compat = talib.CDLDOJI(open_vals, high_vals, low_vals, close_vals)
        assert len(res_compat) == n


def test_windows_pypy_hook_safety():
    """Verify that importing yfinance_ta_patterns never crashes on Windows PyPy."""
    if sys.platform == "win32" and sys.implementation.name == "pypy":
        try:
            import dateutil.tz.win

            # Verify load_name is callable and patched if tzres exists
            if hasattr(dateutil.tz.win, "tzres"):
                assert hasattr(dateutil.tz.win.tzres, "load_name")
        except (ImportError, AttributeError):
            pass
