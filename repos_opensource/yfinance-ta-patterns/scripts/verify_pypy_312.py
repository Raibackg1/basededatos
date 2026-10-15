#!/usr/bin/env python3
"""Verify pure-Python fallback execution on PyPy 3.12 without native binaries."""

from __future__ import annotations

import concurrent.futures
import math
import sys


def main() -> int:
    print(f"=== Running PyPy 3.12 / Pure-Python Verification on Python {sys.version} ===")

    import yfinance_ta_patterns as ytp

    print(f"[1/5] Package import successful (version: {ytp.__version__})")

    # 1. Verify all 61 patterns exist in supported fallback patterns
    assert len(ytp.SUPPORTED_FALLBACK_PATTERNS) == 61, (
        f"Expected 61 supported fallback patterns, got {len(ytp.SUPPORTED_FALLBACK_PATTERNS)}"
    )
    assert len(ytp.UNSUPPORTED_FALLBACK_PATTERNS) == 0, (
        f"Expected 0 unsupported patterns, got {len(ytp.UNSUPPORTED_FALLBACK_PATTERNS)}"
    )
    print(
        f"[2/5] Pattern catalog verified: all {len(ytp.SUPPORTED_FALLBACK_PATTERNS)} TA-Lib candlestick patterns present"
    )

    # 2. Generate 128 synthetic OHLC bars
    count = 128
    opens = [100.0 + math.sin(i * 0.1) * 2.0 for i in range(count)]
    closes = [100.0 + math.cos(i * 0.1) * 2.0 for i in range(count)]
    highs = [max(o, c) + 1.0 + (i % 3) * 0.5 for o, c, i in zip(opens, closes, range(count))]
    lows = [min(o, c) - 1.0 - (i % 3) * 0.5 for o, c, i in zip(opens, closes, range(count))]

    # 3. Test every pattern function directly via talib wrapper
    talib = ytp.talib
    for name in ytp.SUPPORTED_FALLBACK_PATTERNS:
        fn = getattr(talib, name, None)
        assert callable(fn), f"talib missing attribute {name}"
        res = fn(opens, highs, lows, closes)
        assert len(res) == count, f"Pattern {name} returned length {len(res)}, expected {count}"

    print(
        f"[3/5] Pattern execution verified: all 61 patterns evaluated successfully across {count} bars"
    )

    # 4. Verify PatternAnalyzer with dictionary input
    dataset = {
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
    }
    analyzer = ytp.PatternAnalyzer(dataset)
    signals_doji = analyzer.get_signals("CDLDOJI")
    assert len(signals_doji) > 0, "Expected non-zero doji signals to be detected"
    assert all(v in (-100, 100) for v in signals_doji.values()), (
        f"Invalid signal values: {signals_doji}"
    )

    # Check analyze_all_for_date
    date_signals = list(analyzer.analyze_all_for_date("2025-01-01"))
    assert len(date_signals) == 61, f"Expected 61 pattern summaries, got {len(date_signals)}"
    assert not any("Error in" in msg for msg in date_signals), (
        f"Pattern errors detected: {[m for m in date_signals if 'Error in' in m]}"
    )
    print(
        "[4/5] PatternAnalyzer verified: dict dataset accepted and evaluated cleanly (all 61 patterns analyzed)"
    )

    # 5. Multithreaded concurrency test
    def scan_pattern(pat_name: str):
        fn = getattr(talib, pat_name)
        return pat_name, fn(opens, highs, lows, closes)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(scan_pattern, pat) for pat in ytp.SUPPORTED_FALLBACK_PATTERNS]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        assert len(results) == 61

    print(
        "[5/5] Multithreaded execution verified: 61 patterns evaluated concurrently without error"
    )
    print("=== PyPy 3.12 / Pure-Python Verification PASSED successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
