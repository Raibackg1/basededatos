"""Unit tests for Issue #19:
Free-Threaded (No-GIL / PEP 703) runtime diagnostics, GIL status reporting,
and installation hints.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from yfinance_ta_patterns import is_freethreaded, is_gil_enabled
from yfinance_ta_patterns.cli import parse_args, run_cli
from yfinance_ta_patterns.talib_compat import get_talib_install_hint, get_talib_status


def test_top_level_exports() -> None:
    """Verify is_freethreaded and is_gil_enabled are callable and exported."""
    import yfinance_ta_patterns as yftp

    assert callable(yftp.is_freethreaded)
    assert callable(yftp.is_gil_enabled)
    assert "is_freethreaded" in yftp.__all__
    assert "is_gil_enabled" in yftp.__all__


def test_is_freethreaded_probe() -> None:
    """Verify is_freethreaded correctly inspects Py_GIL_DISABLED config var."""
    with patch("sysconfig.get_config_var", return_value=1):
        assert is_freethreaded() is True

    with patch("sysconfig.get_config_var", return_value=0):
        assert is_freethreaded() is False

    with patch("sysconfig.get_config_var", return_value=None):
        assert is_freethreaded() is False


def test_is_gil_enabled_probe() -> None:
    """Verify is_gil_enabled returns probe result or True fallback."""
    # When _is_gil_enabled exists and returns False
    with patch.object(sys, "_is_gil_enabled", create=True, return_value=False):
        assert is_gil_enabled() is False

    # When _is_gil_enabled exists and returns True
    with patch.object(sys, "_is_gil_enabled", create=True, return_value=True):
        assert is_gil_enabled() is True

    # When _is_gil_enabled raises an exception
    def _faulty():
        raise RuntimeError("probe error")

    with patch.object(sys, "_is_gil_enabled", create=True, side_effect=_faulty):
        assert is_gil_enabled() is True


def test_get_talib_status_freethreaded_gil_disabled() -> None:
    """Verify diagnostic fields when running free-threaded with GIL disabled."""
    with patch(
        "yfinance_ta_patterns.talib_compat.is_freethreaded", return_value=True
    ), patch.object(sys, "_is_gil_enabled", create=True, return_value=False):
        status = get_talib_status()

        assert status["is_free_threaded"] is True
        assert status["gil_enabled"] is False
        assert "GIL is disabled" in status["gil_cause"]
        assert status["gil_recommendation"] is None


def test_get_talib_status_freethreaded_forced_by_env() -> None:
    """Verify diagnostic fields when GIL was forced on by PYTHON_GIL=1."""
    with patch(
        "yfinance_ta_patterns.talib_compat.is_freethreaded", return_value=True
    ), patch.object(sys, "_is_gil_enabled", create=True, return_value=True), patch.dict(
        "os.environ", {"PYTHON_GIL": "1"}
    ):
        status = get_talib_status()

        assert status["is_free_threaded"] is True
        assert status["gil_enabled"] is True
        assert "PYTHON_GIL=1" in status["gil_cause"]
        assert "PYTHON_GIL=0" in status["gil_recommendation"]
        assert "-X gil=0" in status["gil_recommendation"]


def test_get_talib_status_freethreaded_reenabled_by_talib() -> None:
    """Verify diagnostic fields when GIL was re-enabled by native talib import."""
    with patch(
        "yfinance_ta_patterns.talib_compat.is_freethreaded", return_value=True
    ), patch.object(sys, "_is_gil_enabled", create=True, return_value=True), patch.dict(
        "os.environ", {}, clear=True
    ), patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", True):
        status = get_talib_status()

        assert status["is_free_threaded"] is True
        assert status["gil_enabled"] is True
        assert "talib._ta_lib" in status["gil_cause"]
        assert "Py_MOD_GIL_NOT_USED" in status["gil_cause"]
        assert "-X gil=0" in status["gil_recommendation"]


def test_get_talib_status_freethreaded_reenabled_by_other_extension() -> None:
    """Verify diagnostic fields when GIL was re-enabled without native talib."""
    with patch(
        "yfinance_ta_patterns.talib_compat.is_freethreaded", return_value=True
    ), patch.object(sys, "_is_gil_enabled", create=True, return_value=True), patch.dict(
        "os.environ", {}, clear=True
    ), patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False):
        status = get_talib_status()

        assert status["is_free_threaded"] is True
        assert status["gil_enabled"] is True
        assert "non-free-threaded C extension" in status["gil_cause"]
        assert "-X gil=0" in status["gil_recommendation"]


def test_get_talib_install_hint_windows_313t() -> None:
    """Verify install hint includes missing wheel guidance on Windows Python 3.13t."""
    with patch("yfinance_ta_patterns.talib_compat.HAS_NATIVE_TALIB", False), patch(
        "sys.platform", "win32"
    ), patch("sys.implementation.name", "cpython"), patch(
        "sysconfig.get_config_var", return_value=1
    ), patch("yfinance_ta_patterns.talib_compat.sys.version_info", (3, 13, 1, "final", 0)):
        hint = get_talib_install_hint()

        assert "Windows Free-Threaded Python 3.13t" in hint
        assert "cp313t-win_amd64 wheels" in hint
        assert "lxml, numpy, scipy" in hint
        assert "3.14t or 3.15t" in hint
        assert "uv add yfinance-ta-patterns" in hint


def test_cli_check_talib_freethreaded_output(capsys) -> None:
    """Verify `yftp --check-talib` displays detailed GIL status and recommendation."""
    with patch("yfinance_ta_patterns.cli.get_talib_status") as mock_status:
        mock_status.return_value = {
            "has_native_talib": True,
            "import_error": None,
            "is_pypy": False,
            "is_windows": True,
            "is_macos": False,
            "is_linux": False,
            "is_free_threaded": True,
            "gil_enabled": True,
            "gil_cause": "Forced enabled by PYTHON_GIL=1 environment variable.",
            "gil_recommendation": "Run with 'python -X gil=0 <script>' or set PYTHON_GIL=0 in environment.",
            "python_version": "3.15.0rc2",
            "implementation": "cpython",
            "reason": "Native C TA-Lib successfully loaded.",
        }
        args = parse_args(["--check-talib"])
        exit_code = run_cli(args)

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Free-Threaded Build:     True" in captured.out
        assert "GIL Currently Enabled:   True" in captured.out
        assert (
            "GIL Status Detail:       Forced enabled by PYTHON_GIL=1 environment variable."
            in captured.out
        )
        assert (
            "Recommendation:          Run with 'python -X gil=0 <script>' or set PYTHON_GIL=0 in environment."
            in captured.out
        )
