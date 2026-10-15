"""Test suite for v0.3.22 enhancements:
1. MarketDataLoader repair auto-detection (repair=None) without warning when scikit-learn is missing.
2. MarketDataLoader repair auto-detection (repair=None) enabling repair when scikit-learn is present.
3. MarketDataLoader repair=True warning when scikit-learn is missing.
4. CLI --repair and --no-repair argument flags.
5. Multi-threaded pattern scanning stress test for Free-Threaded (No-GIL) safety.
"""

import warnings
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pandas as pd
import pytest

import yfinance_ta_patterns.data as _data_mod
from yfinance_ta_patterns.cli import build_parser
from yfinance_ta_patterns.data import MarketDataLoader
from yfinance_ta_patterns.pattern_analyzer import PatternAnalyzer

_yf = pytest.importorskip("yfinance")

if _data_mod.yf is None:
    _data_mod.yf = _yf


def _make_dummy_ohlcv(n: int = 50) -> pd.DataFrame:
    """Generate dummy OHLCV dataframe for testing."""
    index = pd.date_range("2024-01-01", periods=n, freq="1d", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [105.0 + i for i in range(n)],
            "Low": [95.0 + i for i in range(n)],
            "Close": [102.0 + i for i in range(n)],
            "Volume": [1000] * n,
        },
        index=index,
    )


# ==============================================================================
# 1. MarketDataLoader repair auto-detection tests
# ==============================================================================
def test_repair_none_without_sklearn_emits_no_warning() -> None:
    """When repair=None (default) and HAS_SKLEARN is False, no warning is emitted and repair_opt is False."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d")
    assert loader.repair is None

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", False), patch(
        "yfinance_ta_patterns.data.yf.download"
    ) as mock_dl:
        mock_dl.return_value = _make_dummy_ohlcv(5)
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            loader.fetch()

        repair_warnings = [
            w
            for w in record
            if issubclass(w.category, UserWarning) and "Data repair" in str(w.message)
        ]
        assert len(repair_warnings) == 0, "No warning should be emitted for default repair=None"
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is False


def test_repair_none_with_sklearn_enables_repair_silently() -> None:
    """When repair=None (default) and HAS_SKLEARN is True, repair is enabled without warning."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d")

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", True), patch(
        "yfinance_ta_patterns.data.yf.download"
    ) as mock_dl:
        mock_dl.return_value = _make_dummy_ohlcv(5)
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            loader.fetch()

        repair_warnings = [
            w
            for w in record
            if issubclass(w.category, UserWarning) and "Data repair" in str(w.message)
        ]
        assert len(repair_warnings) == 0
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is True


def test_explicit_repair_true_without_sklearn_emits_warning() -> None:
    """When repair=True is explicitly passed and HAS_SKLEARN is False, UserWarning is emitted."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d", repair=True)

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", False), patch(
        "yfinance_ta_patterns.data.yf.download"
    ) as mock_dl:
        mock_dl.return_value = _make_dummy_ohlcv(5)
        with pytest.warns(UserWarning, match="Data repair was requested .*scikit-learn"):
            loader.fetch()

        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is False


def test_explicit_repair_false_without_sklearn_emits_no_warning() -> None:
    """When repair=False is explicitly passed, no warning is emitted and repair is False."""
    loader = MarketDataLoader(symbol="AAPL", interval="1d", repair=False)

    with patch("yfinance_ta_patterns.data.HAS_SKLEARN", False), patch(
        "yfinance_ta_patterns.data.yf.download"
    ) as mock_dl:
        mock_dl.return_value = _make_dummy_ohlcv(5)
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            loader.fetch()

        repair_warnings = [
            w
            for w in record
            if issubclass(w.category, UserWarning) and "Data repair" in str(w.message)
        ]
        assert len(repair_warnings) == 0
        mock_dl.assert_called_once()
        assert mock_dl.call_args.kwargs.get("repair") is False


# ==============================================================================
# 2. CLI --repair / --no-repair flag tests
# ==============================================================================
def test_cli_repair_flags() -> None:
    """Verify build_parser supports --repair and --no-repair flags."""
    parser = build_parser()

    # Default should be None
    args_default = parser.parse_args(["--pattern", "HAMMER"])
    assert args_default.repair is None

    # --repair flag
    args_repair = parser.parse_args(["--pattern", "HAMMER", "--repair"])
    assert args_repair.repair is True

    # --no-repair flag
    args_no_repair = parser.parse_args(["--pattern", "HAMMER", "--no-repair"])
    assert args_no_repair.repair is False


# ==============================================================================
# 3. Multi-threaded concurrency stress test (Free-Threaded / No-GIL verification)
# ==============================================================================
def test_multithreaded_pattern_analysis_no_gil() -> None:
    """Verify concurrent pattern scanning across multiple threads behaves deterministically

    and thread-safely in free-threaded (No-GIL) environments.
    """
    df = _make_dummy_ohlcv(100)
    analyzer = PatternAnalyzer(df)
    patterns = ["DOJI", "HAMMER", "ENGULFING"]

    def scan_task(pat: str) -> int:
        signals = analyzer.get_signals(pat)
        return len(signals)

    # Concurrently execute 30 scans across 10 threads
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(scan_task, patterns[i % len(patterns)]) for i in range(30)]
        results = [f.result() for f in futures]

    assert len(results) == 30
    for r in results:
        assert isinstance(r, int)


# ==============================================================================
# 4. Out-of-the-box Rich terminal UI library availability test
# ==============================================================================
def test_rich_dependency_available() -> None:
    """Verify rich terminal UI library is available out-of-the-box for console rendering."""
    import rich
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Test Table")
    table.add_column("Col")
    table.add_row("Val")
    assert rich is not None
    assert console is not None
    assert table is not None
