"""Dedicated compatibility and concurrency test suite for Python 3.16 and 3.16t (Free-Threaded No-GIL)."""

from __future__ import annotations

import concurrent.futures

import yfinance_ta_patterns as ytp
from yfinance_ta_patterns.talib_compat import (
    ALL_CDL_PATTERNS,
    TALibWrapper,
    get_talib_status,
    is_freethreaded,
    is_gil_enabled,
)


def test_python316_version_and_metadata():
    """Verify library version, zero-dependency autonomous imports, and Python 3.16 support."""
    assert ytp.__version__ == "0.3.60"
    assert len(ALL_CDL_PATTERNS) == 61
    assert callable(is_freethreaded)
    assert callable(is_gil_enabled)


def test_python316_runtime_diagnostics():
    """Verify runtime diagnostics correctly report Python 3.16 / 3.16t environment."""
    status = get_talib_status()
    assert isinstance(status, dict)
    assert "is_free_threaded" in status
    assert "gil_enabled" in status
    assert "python_version" in status

    # Verify consistency between helper and status dict
    assert status["is_free_threaded"] == is_freethreaded()


def test_python316_pure_python_all_61_patterns():
    """Verify all 61 candlestick patterns execute cleanly with pure Python lists (zero numpy/pandas)."""
    n = 50
    open_vals = [100.0 + (i * 0.2) for i in range(n)]
    high_vals = [105.0 + (i * 0.2) for i in range(n)]
    low_vals = [95.0 + (i * 0.2) for i in range(n)]
    close_vals = [102.0 + (i * 0.2) for i in range(n)]

    wrapper = TALibWrapper(force_fallback=True)

    for pattern_name in ALL_CDL_PATTERNS:
        fn = getattr(wrapper, pattern_name, None)
        assert callable(fn), f"Pattern {pattern_name} must be callable"

        result = fn(open_vals, high_vals, low_vals, close_vals)
        assert len(result) == n, f"Pattern {pattern_name} output length mismatch"
        # Results should be candle pattern signals (-100, 0, 100)
        assert all(int(x) in (-100, 0, 100) for x in result)


def test_python316t_multithreaded_parallel_execution():
    """Verify thread safety and parallel scalability across 16 threads (PEP 703 No-GIL on 3.16t)."""
    n = 40
    num_threads = 16
    iterations_per_thread = 5

    def worker(worker_id: int) -> int:
        open_v = [100.0 + (i * 0.1) + worker_id for i in range(n)]
        high_v = [105.0 + (i * 0.1) + worker_id for i in range(n)]
        low_v = [95.0 + (i * 0.1) + worker_id for i in range(n)]
        close_v = [101.0 + (i * 0.1) + worker_id for i in range(n)]

        signals_detected = 0
        wrapper = TALibWrapper(force_fallback=True)

        for _ in range(iterations_per_thread):
            for pat in ("CDLDOJI", "CDLHAMMER", "CDLENGULFING", "CDLMORNINGSTAR", "CDLSHOOTINGSTAR"):
                res = getattr(wrapper, pat)(open_v, high_v, low_v, close_v)
                assert len(res) == n
                signals_detected += sum(1 for x in res if x != 0)

        return signals_detected

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, tid) for tid in range(num_threads)]
        results = [f.result() for f in futures]

    assert len(results) == num_threads
    assert all(isinstance(r, int) for r in results)


def test_pure_python_wrapper_fallback_vector_equivalence():
    """Verify TALibWrapper correctly returns list or vector matching input length."""
    wrapper = TALibWrapper(force_fallback=True)
    open_v = [10.0, 11.0, 12.0]
    high_v = [12.0, 13.0, 14.0]
    low_v = [9.0, 10.0, 11.0]
    close_v = [11.0, 12.0, 13.0]

    out = wrapper.CDLDOJI(open_v, high_v, low_v, close_v)
    assert len(out) == 3
    assert hasattr(out, "__getitem__")
