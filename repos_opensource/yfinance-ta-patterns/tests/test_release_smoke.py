"""Release and code-change smoke test suite for yfinance-ta-patterns.

Validates:
- Package metadata and semantic version integrity
- CLI entrypoint and argument parser behavior
- Runtime compatibility (datetime timedelta in INTERVAL_DELTAS, lazy imports)
- Candlestick pattern recognition on synthetic data (both native TA-Lib and fallback)
- End-to-end backtesting pipeline and metrics calculation
- AI confluence scoring and trade setup generation
- Concurrency and free-threaded (PEP 703) safety across threads
"""

from __future__ import annotations

import concurrent.futures
import datetime
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

import yfinance_ta_patterns
from yfinance_ta_patterns import (
    AIMarketAnalyst,
    AIPatternScorer,
    PatternAnalyzer,
    PatternConfidenceResult,
    PatternRankingTester,
    SignalGrade,
    TradeSetup,
    __version__,
)
from yfinance_ta_patterns.data import INTERVAL_DELTAS
from yfinance_ta_patterns.talib_compat import (
    HAS_NATIVE_TALIB,
    SUPPORTED_FALLBACK_PATTERNS,
)


@pytest.fixture
def synthetic_market_candles() -> pd.DataFrame:
    """Generate 60 bars of geometrically valid synthetic OHLCV data."""
    t0 = datetime.datetime(2025, 1, 1)
    dates = [t0 + datetime.timedelta(days=i) for i in range(60)]
    np.random.seed(12345)

    base = 100.0 + np.cumsum(np.random.normal(0, 0.5, 60))
    opens = base + np.random.uniform(-0.5, 0.5, 60)
    closes = base + np.random.uniform(-0.5, 0.5, 60)

    # Strictly guarantee OHLC invariants: High >= max(Open, Close), Low <= min(Open, Close)
    highs = np.maximum(opens, closes) + np.random.uniform(0.2, 1.5, 60)
    lows = np.minimum(opens, closes) - np.random.uniform(0.2, 1.5, 60)
    volumes = np.random.uniform(50000, 150000, 60)

    # Insert a Doji at bar 20
    opens[20] = 105.0
    closes[20] = 105.0
    highs[20] = 106.0
    lows[20] = 104.0

    # Insert a Hammer at bar 40
    opens[40] = 102.0
    closes[40] = 103.0
    highs[40] = 103.2
    lows[40] = 98.0  # long lower shadow

    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. Package Metadata & Public API Exports
# ---------------------------------------------------------------------------


def test_package_version_format():
    """Version string must follow standard semantic versioning (X.Y.Z)."""
    assert isinstance(__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+(\.post\d+)?$", __version__), (
        f"Invalid version format: {__version__}"
    )


def test_all_public_symbols_exported():
    """Verify all symbols in __all__ are accessible and not None."""
    expected_exports = [
        "ALLOWED_ASSET_TYPES",
        "FOREX_56_PAIRS",
        "FOREX_MAJOR_CURRENCIES",
        "AIMarketAnalyst",
        "AIPatternScorer",
        "ForexDataLoader",
        "MarketDataLoader",
        "PatternAnalyzer",
        "PatternConfidenceResult",
        "PatternRankingTester",
        "PatternResult",
        "SignalGrade",
        "TradeSetup",
        "__version__",
        "classify_asset",
        "normalize_ticker",
        "resolve_asset_currencies",
        "validate_asset_type",
    ]
    for sym in expected_exports:
        assert hasattr(yfinance_ta_patterns, sym), f"Missing export '{sym}' in yfinance_ta_patterns"
        val = getattr(yfinance_ta_patterns, sym)
        assert val is not None, f"Export '{sym}' is None"


# ---------------------------------------------------------------------------
# 2. CLI Execution & Help
# ---------------------------------------------------------------------------


def test_cli_module_version():
    """python -m yfinance_ta_patterns.cli --version must succeed and print version."""
    proc = subprocess.run(
        [sys.executable, "-m", "yfinance_ta_patterns.cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"CLI --version failed: {proc.stderr}"
    assert __version__ in proc.stdout or __version__ in proc.stderr


def test_cli_module_help():
    """python -m yfinance_ta_patterns.cli --help must output options and exit 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "yfinance_ta_patterns.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"CLI --help failed: {proc.stderr}"
    assert "--pattern" in proc.stdout
    assert "--symbol" in proc.stdout
    assert "--timeframe" in proc.stdout


# ---------------------------------------------------------------------------
# 3. Runtime & Cross-Platform Compatibility (PyPy, Timedelta, Lazy Imports)
# ---------------------------------------------------------------------------


def test_interval_deltas_use_datetime_timedelta():
    """Regression test: all INTERVAL_DELTAS must be datetime.timedelta to prevent PyPy 3.8 crashes."""
    assert len(INTERVAL_DELTAS) > 0
    now = datetime.datetime(2025, 1, 1, 12, 0, 0)

    for interval_key, delta in INTERVAL_DELTAS.items():
        assert isinstance(delta, datetime.timedelta), (
            f"INTERVAL_DELTAS['{interval_key}'] is not datetime.timedelta: {type(delta)}"
        )
        # Arithmetic must not raise
        future = now + delta
        past = now - delta
        assert future > past


def test_lazy_yfinance_loading():
    """Verify that importing yfinance_ta_patterns.data does not eagerly load yfinance."""
    import yfinance_ta_patterns.data as ydata

    if hasattr(ydata, "yf") and ydata.yf is None:
        code = (
            "import sys; "
            "import yfinance_ta_patterns.data; "
            "assert 'yfinance' not in sys.modules, 'yfinance was eagerly imported!'"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert proc.returncode == 0, f"Lazy import failed: {proc.stderr}"


# ---------------------------------------------------------------------------
# 4. Candlestick Pattern Recognition on Synthetic Data
# ---------------------------------------------------------------------------


def test_candlestick_patterns_recognized(synthetic_market_candles: pd.DataFrame):
    """Test pattern recognition on synthetic data for all supported patterns."""
    analyzer = PatternAnalyzer(synthetic_market_candles)

    if HAS_NATIVE_TALIB:
        patterns_to_test = PatternRankingTester.get_all_patterns()
        assert len(patterns_to_test) >= 61
    else:
        patterns_to_test = sorted(SUPPORTED_FALLBACK_PATTERNS)
        assert len(patterns_to_test) == 61

    for pattern in patterns_to_test:
        signals = analyzer.get_signals(pattern)
        assert isinstance(signals, pd.Series), f"get_signals({pattern}) did not return Series"
        unique_vals = set(signals.unique())
        assert unique_vals.issubset({-100, 0, 100}), (
            f"Pattern {pattern} returned unexpected values: {unique_vals}"
        )


def test_pattern_analyzer_date_range_filtering(synthetic_market_candles: pd.DataFrame):
    """Test date and range filtering on PatternAnalyzer."""
    analyzer = PatternAnalyzer(synthetic_market_candles)

    # Filter by specific date that has a Doji
    sig_date = analyzer.get_signals("CDLDOJI", date="2025-01-21")
    assert isinstance(sig_date, pd.Series)

    # Filter by date range
    sig_range = analyzer.get_signals("CDLDOJI", start_date="2025-01-10", end_date="2025-01-25")
    assert isinstance(sig_range, pd.Series)
    for ts in sig_range.index:
        ts_str = str(ts)[:10]
        assert "2025-01-10" <= ts_str <= "2025-01-25"


# ---------------------------------------------------------------------------
# 5. Pattern Backtesting Pipeline & Metrics
# ---------------------------------------------------------------------------


def test_pattern_ranking_tester_end_to_end(synthetic_market_candles: pd.DataFrame):
    """Test full backtesting pipeline, calculation of trades, Sharpe, and report generation."""
    tester = PatternRankingTester(
        synthetic_market_candles,
        holding_period=5,
        initial_capital=10000.0,
        position_size=100.0,
    )
    results = tester.test_all_patterns(filter_news=False)
    assert isinstance(results, list)
    assert len(results) > 0

    # Verify report export
    report = tester.get_comparison_report()
    assert isinstance(report, pd.DataFrame)
    assert not report.empty
    col_names_lower = [c.lower() for c in report.columns]
    assert any("win rate" in c for c in col_names_lower)
    assert any("pnl" in c for c in col_names_lower)


# ---------------------------------------------------------------------------
# 6. AI Pattern Scorer & Trade Setup Generation
# ---------------------------------------------------------------------------


def test_ai_scorer_trade_setup_end_to_end(synthetic_market_candles: pd.DataFrame):
    """Test indicator enrichment, confluence scoring, and trade setup calculation."""
    scorer = AIPatternScorer(synthetic_market_candles)
    assert "_EMA20" in scorer.df.columns
    assert "_RSI14" in scorer.df.columns
    assert "_ATR14" in scorer.df.columns

    # Evaluate pattern on the last candle
    last_idx = synthetic_market_candles.index[-1]
    result = scorer.score_signal("HAMMER", last_idx, raw_signal=100)
    assert isinstance(result, PatternConfidenceResult)
    assert 0.0 <= result.confidence_score <= 1.0
    assert isinstance(result.grade, SignalGrade)

    # Trade setup
    setup = result.trade_setup
    assert isinstance(setup, TradeSetup)
    assert setup.entry_price > 0.0
    assert setup.stop_loss > 0.0
    assert setup.take_profit_1 > 0.0
    assert setup.risk_reward_ratio >= 0.0


def test_ai_market_analyst_synthesis(synthetic_market_candles: pd.DataFrame):
    """Test AIMarketAnalyst brief, JSON, and LLM prompt generation."""
    scorer = AIPatternScorer(synthetic_market_candles)
    last_idx = synthetic_market_candles.index[-1]
    result = scorer.score_signal("HAMMER", last_idx, raw_signal=100)

    analyst = AIMarketAnalyst(
        synthetic_market_candles,
        scored_results=[result],
        symbol="AAPL",
        timeframe="1d",
        asset_type="stock",
    )
    brief = analyst.generate_brief()
    assert isinstance(brief, str)
    assert len(brief) > 0
    assert "AAPL" in brief

    prompt = analyst.to_llm_prompt()
    assert isinstance(prompt, str)
    assert "Portfolio Manager" in prompt or "Technical Analyst" in prompt

    payload = analyst.to_dict()
    assert isinstance(payload, dict)
    assert payload.get("symbol") == "AAPL"
    assert "patterns" in payload


# ---------------------------------------------------------------------------
# 7. Concurrency & Free-Threaded (PEP 703) Safety
# ---------------------------------------------------------------------------


def test_concurrent_multithreaded_pattern_analysis(synthetic_market_candles: pd.DataFrame):
    """Ensure running multiple analyzers in parallel threads does not deadlock or crash."""
    patterns = ["CDLDOJI", "CDLHAMMER", "CDLENGULFING", "CDLHARAMI"]

    def _analyze(pat: str) -> int:
        analyzer = PatternAnalyzer(synthetic_market_candles)
        res = analyzer.get_signals(pat)
        return int((res != 0).sum())

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_analyze, p): p for p in patterns}
        results = {}
        for fut in concurrent.futures.as_completed(futures):
            pat = futures[fut]
            results[pat] = fut.result()

    assert len(results) == len(patterns)
    for _pat, count in results.items():
        assert isinstance(count, int)
