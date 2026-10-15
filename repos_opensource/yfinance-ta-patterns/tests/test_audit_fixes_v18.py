"""Regression tests for the v18 audit fixes.

Covers:

5. Multi-symbol scanning swallowed per-symbol failures with a bare `except Exception:
   continue`, so skipped symbols were invisible in the summary.
6. A stray `.IL` exchange suffix was mapped to USD plus the London session, contradicting
   the real Israeli suffix `.TA` (ILS + Asia/Jerusalem).
7. `--format markdown` was an accepted choice that behaved exactly like `text`.
9. `_safe_get_time` used a redundant `except (AttributeError, Exception)` and off-indent
   blocks inside it.
11. `get_comparison_report()` clobbered the tester's own `_results` / `trades` buffers,
   silently changing what `get_results()` and `export_results()` returned afterwards.
"""

from __future__ import annotations

import datetime
import warnings

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns import cli
from yfinance_ta_patterns.data import _EXCHANGE_SUFFIX_MAP, _get_market_session_hours
from yfinance_ta_patterns.pattern_tester import PatternRankingTester, _safe_get_time

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _marubozu_frame(periods: int = 30) -> pd.DataFrame:
    """Daily frame with baseline candles and textbook bullish marubozu at the end."""
    dates = pd.date_range("2025-01-01", periods=periods, freq="1d", tz="UTC")
    opens = 100.0 + np.arange(periods) * 0.1
    closes = opens + 0.2
    highs = closes + 0.05
    lows = opens - 0.05
    # Strong marubozu candles at the end (exceeds TA-Lib BodyLong average):
    for i in range(-5, 0):
        closes[i] = opens[i] + 3.0
        highs[i] = closes[i]
        lows[i] = opens[i]
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.full(periods, 1000.0),
        },
        index=dates,
    )


def _pattern_frame(periods: int = 30) -> pd.DataFrame:
    """Seeded random-walk frame with injected dojis; yields backtestable patterns."""
    dates = pd.date_range("2025-01-01", periods=periods, freq="1d", tz="UTC")
    rng = np.random.default_rng(42)
    base = 100.0 + np.cumsum(rng.normal(0, 1, periods))
    highs = base + rng.uniform(0.5, 2.0, periods)
    lows = base - rng.uniform(0.5, 2.0, periods)
    opens = base + rng.uniform(-0.5, 0.5, periods)
    closes = base + rng.uniform(-0.5, 0.5, periods)
    opens[5] = closes[5]
    opens[15] = closes[15]
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": rng.integers(1000, 5000, periods),
        },
        index=dates,
    )


def _make_loader_stub(frames: dict[str, pd.DataFrame], errors: dict[str, Exception] | None = None):
    """Build a `MarketDataLoader` stand-in serving prepared frames or raising per symbol."""
    errors = errors or {}

    class _StubLoader:
        def __init__(self, symbol: str, **kwargs: object) -> None:
            self.symbol = symbol
            self.period = kwargs.get("period", "1y")

        def get_data(self) -> pd.DataFrame:
            if self.symbol in errors:
                raise errors[self.symbol]
            return frames[self.symbol]

    return _StubLoader


# --------------------------------------------------------------------------------------
# 7. markdown output format
# --------------------------------------------------------------------------------------


def test_format_markdown_table_renders_github_table():
    df = pd.DataFrame({"Timestamp": ["2025-01-06"], "Signal": [100]})

    rendered = cli.format_markdown_table(df)

    assert rendered.splitlines() == [
        "| Timestamp  | Signal |",
        "| ---------- | ------ |",
        "| 2025-01-06 | 100    |",
    ]


def test_format_markdown_table_escapes_pipes_and_none():
    df = pd.DataFrame({"Symbol": ["A|B"], "Note": [None]})

    rendered = cli.format_markdown_table(df)

    assert r"| A\|B   |      |" in rendered


def test_format_price_precision():
    assert cli.format_price(1.1397310495376587) == "1.13973"
    assert cli.format_price(1.3245558738708496) == "1.32456"
    assert cli.format_price(155.321) == "155.321"
    assert cli.format_price(155.3) == "155.30"
    assert cli.format_price(224.25) == "224.25"
    assert cli.format_price(65100.5) == "65100.50"
    assert cli.format_price(0.0) == "0.00"
    assert cli.format_price(None) == "-"
    assert cli.format_price("N/A") == "N/A"


def test_markdown_format_is_no_longer_identical_to_text(monkeypatch, capsys):
    """`--format markdown` must emit a markdown table where `text` emits a plain block."""
    stub = _make_loader_stub({"AAPL": _marubozu_frame()})
    monkeypatch.setattr(cli, "MarketDataLoader", stub)
    argv = ["--symbol", "AAPL", "--pattern", "CDLMARUBOZU"]

    cli.run_cli(cli.parse_args([*argv, "--format", "markdown"]))
    markdown_out = capsys.readouterr().out

    cli.run_cli(cli.parse_args([*argv, "--format", "text"]))
    text_out = capsys.readouterr().out

    assert "| Timestamp" in markdown_out
    assert "| Signal" in markdown_out
    assert "| ---" in markdown_out
    assert "| ---" not in text_out
    assert "MARUBOZU:" in text_out


def test_markdown_format_renders_opportunity_ranking(monkeypatch, capsys):
    stub = _make_loader_stub({"EURUSD=X": _marubozu_frame(), "GBPUSD=X": _marubozu_frame()})
    monkeypatch.setattr(cli, "MarketDataLoader", stub)

    rc = cli.run_cli(
        cli.parse_args(
            ["--symbol", "EURUSD=X,GBPUSD=X", "--min-confidence", "0", "--format", "markdown"]
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "## 📊 Opportunity Ranking" in out
    assert "| Time" in out
    assert "| Symbol" in out
    assert "| ---" in out
    assert "### 🏆 Top Recommended Setup:" in out

    table_lines = [line for line in out.splitlines() if line.startswith("|")]
    assert len(table_lines) >= 3
    first_pipes = [i for i, c in enumerate(table_lines[0]) if c == "|"]
    for line in table_lines:
        assert [i for i, c in enumerate(line) if c == "|"] == first_pipes
        assert len(line) == len(table_lines[0])


# --------------------------------------------------------------------------------------
# 5. skipped symbols are reported
# --------------------------------------------------------------------------------------


def test_multi_symbol_scan_reports_skipped_symbols(monkeypatch, capsys):
    stub = _make_loader_stub(
        {"EURUSD=X": _marubozu_frame()},
        errors={"GBPUSD=X": ValueError("no data for symbol")},
    )
    monkeypatch.setattr(cli, "MarketDataLoader", stub)

    rc = cli.run_cli(cli.parse_args(["--symbol", "EURUSD=X,GBPUSD=X", "--min-confidence", "0"]))

    captured = capsys.readouterr()
    assert rc == 0
    assert "1 of 2 symbols skipped" in captured.err
    assert "GBPUSD: ValueError: no data for symbol" in captured.err


def test_multi_symbol_scan_reports_short_history(monkeypatch, capsys):
    stub = _make_loader_stub(
        {"EURUSD=X": _marubozu_frame(), "GBPUSD=X": _marubozu_frame(periods=5)}
    )
    monkeypatch.setattr(cli, "MarketDataLoader", stub)

    cli.run_cli(cli.parse_args(["--symbol", "EURUSD=X,GBPUSD=X", "--min-confidence", "0"]))

    err = capsys.readouterr().err
    assert "1 of 2 symbols skipped" in err
    assert "GBPUSD: insufficient history" in err


def test_multi_symbol_scan_is_quiet_when_nothing_is_skipped(monkeypatch, capsys):
    stub = _make_loader_stub({"EURUSD=X": _marubozu_frame(), "GBPUSD=X": _marubozu_frame()})
    monkeypatch.setattr(cli, "MarketDataLoader", stub)

    cli.run_cli(cli.parse_args(["--symbol", "EURUSD=X,GBPUSD=X", "--min-confidence", "0"]))

    assert "skipped" not in capsys.readouterr().err


# --------------------------------------------------------------------------------------
# 6. exchange suffix mappings
# --------------------------------------------------------------------------------------


def test_il_suffix_is_no_longer_mapped_to_usd():
    """`.IL` was listed under a `# UK` comment as USD; it is not a Yahoo suffix at all."""
    assert ".IL" not in _EXCHANGE_SUFFIX_MAP
    assert _EXCHANGE_SUFFIX_MAP[".TA"] == "ILS"


def test_il_suffix_does_not_claim_a_london_session():
    """`.IL` must fall through to the unknown-suffix path instead of reporting London."""
    d = datetime.date(2025, 1, 15)

    with pytest.warns(UserWarning, match=r"Unknown exchange suffix '\.IL'"):
        tz_name, open_t, close_t = _get_market_session_hours("TEVA.IL", d)

    assert tz_name == "America/New_York"
    assert (open_t, close_t) == (datetime.time(9, 30), datetime.time(16, 0))


def test_supported_sessions_are_unchanged():
    """Removing `.IL` must not disturb the neighbouring suffix handlers."""
    d = datetime.date(2025, 1, 15)

    assert _get_market_session_hours("VOD.L", d) == (
        "Europe/London",
        datetime.time(8, 0),
        datetime.time(16, 30),
    )
    assert _get_market_session_hours("TEVA.TA", d) == (
        "Asia/Jerusalem",
        datetime.time(10, 0),
        datetime.time(17, 25),
    )


# --------------------------------------------------------------------------------------
# 9. _safe_get_time cleanup
# --------------------------------------------------------------------------------------


def test_safe_get_time_reads_valid_indices():
    assert _safe_get_time(["a", "b", "c"], 1) == "b"
    assert _safe_get_time(["a", "b", "c"], -1) == "c"


def test_safe_get_time_returns_none_for_missing_inputs():
    assert _safe_get_time(None, 0) is None
    assert _safe_get_time(["a"], None) is None
    assert _safe_get_time(["a"], 5) is None
    assert _safe_get_time(["a"], -5) is None


def test_safe_get_time_swallows_index_failures():
    """The broad guard remains: a tslibs index that rejects len() must not propagate."""

    class _NoLenIndex:
        def __len__(self) -> int:
            raise TypeError("tslibs index has no length on this build")

    assert _safe_get_time(_NoLenIndex(), 0) is None


# --------------------------------------------------------------------------------------
# 11. get_comparison_report must not clobber the caller's run
# --------------------------------------------------------------------------------------


def test_get_comparison_report_preserves_previous_results():
    tester = PatternRankingTester(_pattern_frame(), news_dates=["2025-01-05", "2025-01-10"])
    first_results = tester.test_all_patterns(filter_news=False)
    first_names = [r.pattern_name for r in first_results]
    first_trades = list(tester.trades)

    report = tester.get_comparison_report()

    assert isinstance(report, pd.DataFrame)
    # `test_all_patterns` rebinds `_results` on every call, so this identity only holds if
    # the report restored the caller's buffer.
    assert tester._results is first_results
    assert [r.pattern_name for r in tester._results] == first_names
    assert tester.trades == first_trades


def test_get_comparison_report_restores_the_fx_flag():
    tester = PatternRankingTester(_pattern_frame())
    assert tester._used_approx_fx is False

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        report = tester.get_comparison_report()

    assert "fx_warning" in report.attrs
    assert tester._used_approx_fx is False
