#!/usr/bin/env python3
"""Verify native extras keep the GIL disabled on free-threaded CPython."""

from __future__ import annotations

import sys
import sysconfig


def _gil_enabled() -> bool:
    probe = getattr(sys, "_is_gil_enabled", None)
    if not callable(probe):
        raise RuntimeError("This Python build does not expose sys._is_gil_enabled().")
    return bool(probe())


def main() -> int:
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise RuntimeError("This verifier requires a free-threaded CPython build.")
    if _gil_enabled():
        raise RuntimeError(
            "GIL is enabled. Set PYTHON_GIL=0 or pass -X gil=0 when starting Python."
        )

    try:
        import numpy as np
        import pandas as pd
        has_pandas = True
    except ImportError:
        has_pandas = False

    if not has_pandas:
        from yfinance_ta_patterns.talib_compat import ALL_CDL_PATTERNS, TALibWrapper

        tw = TALibWrapper()
        count = 64
        opens = [1.0 + (i * 0.001) for i in range(count)]
        closes = [1.001 + (i * 0.001) for i in range(count)]
        highs = [max(o, c) + 0.002 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 0.002 for o, c in zip(opens, closes)]
        for pat in ALL_CDL_PATTERNS:
            fn = getattr(tw, pat)
            res = fn(opens, highs, lows, closes)
            assert len(res) == count
        if _gil_enabled():
            raise AssertionError("A dependency enabled the GIL during verification.")
        print(
            "Free-threaded pure-Python verification passed: "
            f"Python {sys.version.split()[0]}, patterns: {len(ALL_CDL_PATTERNS)}, GIL disabled."
        )
        return 0

    try:
        import sklearn

        sklearn_ver = getattr(sklearn, "__version__", "installed")
    except ImportError:
        sklearn_ver = "optional (not installed)"

    try:
        import talib

        talib_ver = getattr(talib, "__version__", "installed")
    except ImportError:
        talib_ver = "fallback (pure-python)"

    try:
        import yfinance

        yf_ver = getattr(yfinance, "__version__", "installed")
    except ImportError:
        yf_ver = "optional (not installed)"

    from yfinance_ta_patterns.pattern_analyzer import PatternAnalyzer
    from yfinance_ta_patterns.talib_compat import ALL_CDL_PATTERNS, HAS_NATIVE_TALIB

    count = 64
    base = np.linspace(1.0, 1.2, count)
    opens = base + np.sin(np.arange(count)) * 0.001
    closes = base + np.cos(np.arange(count)) * 0.001
    data = pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) + 0.002,
            "Low": np.minimum(opens, closes) - 0.002,
            "Close": closes,
        }
    )

    analyzer = PatternAnalyzer(data)
    assert len(analyzer.pattern_functions) == 61, "PatternAnalyzer must expose 61 patterns"
    if HAS_NATIVE_TALIB and set(analyzer.pattern_functions) != set(ALL_CDL_PATTERNS):
        raise AssertionError("PatternAnalyzer does not expose all 61 native TA-Lib patterns.")

    for pattern in analyzer.pattern_functions:
        analyzer.get_signals(pattern)

    if _gil_enabled():
        raise AssertionError("A native dependency enabled the GIL during verification.")

    print(
        "Free-threaded verification passed: "
        f"Python {sys.version.split()[0]}, TA-Lib: {talib_ver}, "
        f"scikit-learn: {sklearn_ver}, yfinance: {yf_ver}, "
        f"patterns: {len(analyzer.pattern_functions)}, GIL disabled."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
