"""Tests for TA-Lib status diagnostics, installation hints, and CLI diagnostic options (Issue #7)."""

import sys
from unittest.mock import patch

import pandas as pd
import pytest

from yfinance_ta_patterns import (
    HAS_NATIVE_TALIB,
    PatternAnalyzer,
    get_talib_install_hint,
    get_talib_status,
)
from yfinance_ta_patterns.cli import parse_args, run_cli


def test_get_talib_status_structure():
    """Verify get_talib_status returns expected dictionary keys and values."""
    status = get_talib_status()
    assert isinstance(status, dict)
    assert "has_native_talib" in status
    assert "import_error" in status
    assert "is_pypy" in status
    assert "is_windows" in status
    assert "is_macos" in status
    assert "is_linux" in status
    assert "is_free_threaded" in status
    assert "gil_enabled" in status
    assert "python_version" in status
    assert "implementation" in status
    assert "reason" in status

    assert status["has_native_talib"] is HAS_NATIVE_TALIB
    assert status["implementation"] == sys.implementation.name
    assert isinstance(status["is_free_threaded"], bool)
    assert status["gil_enabled"] in (True, False, None)


def test_get_talib_status_simulated_scenarios():
    """Verify get_talib_status reason on native present vs PyPy Windows missing."""
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", True):
        status = get_talib_status()
        assert status["has_native_talib"] is True
        assert "Native C TA-Lib successfully loaded." in status["reason"]

    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "pypy"
    ), patch("sys.platform", "win32"):
        status = get_talib_status()
        assert status["has_native_talib"] is False
        assert status["is_pypy"] is True
        assert status["is_windows"] is True
        assert "--find-links" in status["reason"]


def test_get_talib_install_hint_formatting():
    """Verify get_talib_install_hint provides actionable uv and pip commands."""
    # When native ta-lib is present:
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", True):
        hint = get_talib_install_hint()
        assert hint == "Native TA-Lib is already installed and available."

    # When native ta-lib is absent on PyPy + Windows:
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "pypy"
    ), patch("sys.platform", "win32"):
        hint = get_talib_install_hint(release_tag="v0.3.30")
        assert "--find-links" in hint
        assert "github.com/eminsk/yfinance-ta-patterns/releases/expanded_assets/v0.3.30" in hint
        assert 'uv add "yfinance-ta-patterns[all]"' in hint
        assert "[tool.uv]" in hint

    # When native ta-lib is absent on CPython + Windows:
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "cpython"
    ), patch("sys.platform", "win32"), patch(
        "yfinance_ta_patterns.talib_compat.sys.version_info", (3, 14, 0, "final", 0)
    ), patch("yfinance_ta_patterns.talib_compat.sysconfig.get_config_var", return_value=0):
        hint = get_talib_install_hint(release_tag="v0.3.30")
        assert "pip install ta-lib" in hint
        assert "--find-links" in hint

    # When native ta-lib is absent on Linux:
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "cpython"
    ), patch("sys.platform", "linux"):
        hint = get_talib_install_hint()
        assert "apt-get install" in hint
        assert "libta-lib-dev" in hint

    # When native ta-lib is absent on macOS:
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "cpython"
    ), patch("sys.platform", "darwin"):
        hint = get_talib_install_hint()
        assert "brew install ta-lib" in hint


def test_free_threaded_windows_install_hint_uses_wheelhouse():
    """Free-threaded Windows must not fall back to a local C-extension build."""
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "cpython"
    ), patch("sys.platform", "win32"), patch(
        "yfinance_ta_patterns.talib_compat.sysconfig.get_config_var", return_value=1
    ):
        hint = get_talib_install_hint()

    assert "PYTHON_GIL" in hint
    assert "expanded_assets/v0.3.26" in hint
    assert "pandas, curl_cffi, and TA-Lib" in hint
    assert "3.14t and 3.15t" in hint


def test_standard_windows_315_install_hint_mentions_pandas_build():
    """Standard CPython 3.15 needs a pandas source build until upstream ships a wheel."""
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.implementation.name", "cpython"
    ), patch("sys.platform", "win32"), patch(
        "yfinance_ta_patterns.talib_compat.sys.version_info", (3, 15, 0, "final", 0)
    ), patch("yfinance_ta_patterns.talib_compat.sysconfig.get_config_var", return_value=0):
        hint = get_talib_install_hint()

    assert "TA-Lib cp315" in hint
    assert "pandas cp315" in hint
    assert "built from source" in hint


def test_pattern_analyzer_fallback_patterns():
    """Verify PatternAnalyzer exposes supported fallback patterns."""
    patterns = PatternAnalyzer.get_supported_fallback_patterns()
    assert isinstance(patterns, list)
    assert len(patterns) >= 6
    assert "CDLDOJI" in patterns
    assert "CDLHAMMER" in patterns
    assert "CDLHANGINGMAN" in patterns
    assert "CDLENGULFING" in patterns
    for pat in patterns:
        assert pat.startswith("CDL")


def test_cli_check_talib_option(capsys):
    """Verify `yftp --check-talib` displays diagnostics without needing ticker."""
    args = parse_args(["--check-talib"])
    assert args.check_talib is True
    exit_code = run_cli(args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "=== TA-Lib Environment & Binary Status ===" in captured.out
    assert "Native TA-Lib Available:" in captured.out
    assert "Python Implementation:" in captured.out
    assert "Platform:" in captured.out
    assert "Status Details:" in captured.out


@pytest.fixture
def mock_ohlcv_data():
    idx = pd.date_range("2025-01-01 00:00", periods=10, freq="1d", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0] * 10,
            "High": [105.0] * 10,
            "Low": [95.0] * 10,
            "Close": [100.0] * 10,
            "Volume": [1000.0] * 10,
        },
        index=idx,
    )


def test_fallback_notice_in_single_symbol_cli(capsys, mock_ohlcv_data):
    """Verify CLI notifies user with install hint in stderr when native TA-Lib is missing in all_patterns mode."""
    with patch(
        "yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_data
    ), patch("yfinance_ta_patterns.cli.HAS_NATIVE_TALIB", False):
        args = parse_args(["--symbol", "EURUSD", "--all-patterns"])
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Notice: Native TA-Lib binary not found" in captured.err


def test_fallback_notice_in_multi_symbol_cli(capsys, mock_ohlcv_data):
    """Verify multi-symbol CLI notifies user with install hint in stderr when native TA-Lib is missing."""
    with patch(
        "yfinance_ta_patterns.cli.MarketDataLoader.get_data", return_value=mock_ohlcv_data
    ), patch("yfinance_ta_patterns.cli.HAS_NATIVE_TALIB", False):
        args = parse_args(["--symbol", "EURUSD,GBPUSD", "--timeframe", "1h"])
        exit_code = run_cli(args)
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Native TA-Lib binary not found. Scanning" in captured.err
