"""Unit tests verifying audit fixes for version 0.3.12.

Issues covered:
1. Risk/Reward ratio calculation when short profit targets are clipped under extreme ATR.
2. International equities trading sessions mapping & unknown suffix warning.
3. Open position equity curve generation when no closed trades exist.
4. Exit price in trade log includes slippage and records market_exit_price.
5. Strict validation of NaN, Inf, and invalid tester parameters.
6. Market-aware annualization factors and custom periods_per_year override.
"""

import datetime
import math
import warnings

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.ai.scorer import AIPatternScorer
from yfinance_ta_patterns.data import _get_market_session_hours
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
    resolve_periods_per_year,
)


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """Create reproducible OHLCV DataFrame for testing."""
    dates = pd.date_range("2024-01-01", periods=20, freq="1D")
    data = {
        "Open": [100.0 + i for i in range(20)],
        "High": [105.0 + i for i in range(20)],
        "Low": [95.0 + i for i in range(20)],
        "Close": [102.0 + i for i in range(20)],
        "Volume": [1000.0] * 20,
    }
    return pd.DataFrame(data, index=dates)


# --- Issue 1: Risk/Reward ratio recalculation on clipped short targets ---
def test_risk_reward_recalculated_on_clipped_targets(sample_ohlcv_df: pd.DataFrame) -> None:
    """Under extreme ATR, short take profit clipping should recalculate actual R:R."""
    scorer = AIPatternScorer(sample_ohlcv_df)

    # Extreme ATR where entry - 1.5 * risk < 0
    # close=10, high=50, low=5, atr=60 -> buffer=12, stop_loss=62, risk=52
    # entry - 1.5 * risk = 10 - 78 = -68 < 0 -> tp1 clipped to close * 0.001 = 0.01
    setup = scorer._build_trade_setup(
        is_bullish=False,
        close=10.0,
        high=50.0,
        low=5.0,
        atr=60.0,
    )

    expected_actual_rr_tp1 = round(float((10.0 - setup.take_profit_1) / setup.risk_per_unit), 2)
    expected_actual_rr_tp2 = round(float((10.0 - setup.take_profit_2) / setup.risk_per_unit), 2)

    assert setup.take_profit_1 > 0
    assert setup.risk_reward_ratio == expected_actual_rr_tp1
    assert setup.rr_tp1 == expected_actual_rr_tp1
    assert setup.rr_tp2 == expected_actual_rr_tp2
    assert setup.risk_reward_ratio < 1.0  # Must reflect actual clipped ratio, not static 1.5

    # Normal bullish setup: should retain standard R:R ratios
    bullish_setup = scorer._build_trade_setup(
        is_bullish=True,
        close=100.0,
        high=105.0,
        low=95.0,
        atr=5.0,
    )
    assert bullish_setup.risk_reward_ratio == 1.5
    assert bullish_setup.rr_tp1 == 1.5
    assert bullish_setup.rr_tp2 == 3.0


# --- Issue 2: International equities trading sessions & unknown suffix warning ---
def test_international_equities_market_session_hours() -> None:
    """Check market sessions for international exchanges and warning on unknown suffixes."""
    d = datetime.date(2025, 1, 15)

    # Australia (ASX)
    tz, open_t, close_t = _get_market_session_hours("BHP.AX", d)
    assert tz == "Australia/Sydney"
    assert open_t == datetime.time(10, 0)
    assert close_t == datetime.time(16, 0)

    # Canada (TSX)
    tz, open_t, close_t = _get_market_session_hours("RY.TO", d)
    assert tz == "America/Toronto"
    assert open_t == datetime.time(9, 30)
    assert close_t == datetime.time(16, 0)

    # Switzerland (SIX)
    tz, open_t, close_t = _get_market_session_hours("NESN.SW", d)
    assert tz == "Europe/Zurich"
    assert open_t == datetime.time(9, 0)
    assert close_t == datetime.time(17, 30)

    # Sweden (Stockholm)
    tz, open_t, close_t = _get_market_session_hours("VOLV-B.ST", d)
    assert tz == "Europe/Stockholm"
    assert open_t == datetime.time(9, 0)
    assert close_t == datetime.time(17, 30)

    # Singapore
    tz, open_t, close_t = _get_market_session_hours("D05.SI", d)
    assert tz == "Asia/Singapore"
    assert open_t == datetime.time(9, 0)
    assert close_t == datetime.time(17, 0)

    # Standard US equities (no suffix): no warning
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        tz, open_t, close_t = _get_market_session_hours("AAPL", d)
        assert tz == "America/New_York"
        assert len(recorded_warnings) == 0

    # Unknown foreign exchange suffix: triggers warning and falls back to US session
    with pytest.warns(UserWarning, match="Unknown exchange suffix '.UNKNOWN' in 'TEST.UNKNOWN'"):
        tz, open_t, close_t = _get_market_session_hours("TEST.UNKNOWN", d)
        assert tz == "America/New_York"
        assert open_t == datetime.time(9, 30)
        assert close_t == datetime.time(16, 0)


# --- Issue 3: Open position equity curve without closed trades ---
def test_open_position_generates_equity_curve_without_closed_trades(
    sample_ohlcv_df: pd.DataFrame,
) -> None:
    """When force_exit_on_last_bar=False and an open trade exists, an MTM curve is generated."""
    tester = PatternRankingTester(
        sample_ohlcv_df,
        force_exit_on_last_bar=False,
        holding_period=10,
    )

    # Synthetic signal triggering entry on bar 17 (out of 20 bars, 0..19)
    # With holding_period=10 and 20 bars, it enters on bar 18 (execution='next_open') and stays open
    signals = np.zeros(len(sample_ohlcv_df))
    signals[17] = 1

    trades = tester._calculate_trades(signals)
    assert len(trades) == 0  # No closed trades
    assert tester._last_open_trade is not None
    assert tester._last_open_trade["direction"] == "LONG"
    assert tester._last_open_trade["entry_idx"] == 18

    # Now verify test execution generates an MTM equity curve
    # Mocking talib CDLHAMMER on tester to return signals
    def mock_cdl(op: np.ndarray, hi: np.ndarray, lo: np.ndarray, cl: np.ndarray) -> np.ndarray:
        return signals * 100

    from yfinance_ta_patterns.talib_compat import talib

    original_func = getattr(talib, "CDLHAMMER", None)
    talib.CDLHAMMER = mock_cdl
    try:
        res = tester.test_pattern("CDLHAMMER")
        assert res is not None
        assert res.total_trades == 0
        assert res.total_signals == 1
        assert res.open_trade is not None
        assert res.equity_curve is not None
        assert len(res.equity_curve) == len(sample_ohlcv_df) + 1
        # Equity curve on bar 19 should reflect unrealized PnL
        assert tester.equity_curve == res.equity_curve
    finally:
        if original_func is not None:
            talib.CDLHAMMER = original_func


# --- Issue 4: Exit price in trade log includes slippage and records market_exit_price ---
def test_exit_price_includes_slippage_in_trade_log(sample_ohlcv_df: pd.DataFrame) -> None:
    """Trade records must reflect eff_exit with slippage, and PnL must reconcile."""
    slippage = 0.5
    commission = 2.0
    tester = PatternRankingTester(
        sample_ohlcv_df,
        holding_period=2,
        slippage=slippage,
        commission=commission,
        force_exit_on_last_bar=True,
    )

    signals = np.zeros(len(sample_ohlcv_df))
    signals[2] = 1  # Long signal
    signals[8] = -1  # Short signal

    trades = tester._calculate_trades(signals)
    assert len(trades) >= 2

    for t in trades:
        assert "exit_price" in t
        assert "market_exit_price" in t
        entry_p = t["entry_price"]
        exit_p = t["exit_price"]
        mkt_exit_p = t["market_exit_price"]
        pos = t["position"]

        if t["direction"] == "LONG":
            assert math.isclose(exit_p, mkt_exit_p - slippage, abs_tol=1e-5)
            expected_pnl = pos * (exit_p - entry_p) - commission
        else:
            assert math.isclose(exit_p, mkt_exit_p + slippage, abs_tol=1e-5)
            expected_pnl = pos * (entry_p - exit_p) - commission

        assert math.isclose(t["pnl"], expected_pnl, abs_tol=1e-4)


# --- Issue 5: Strict validation of NaN, Inf, and invalid tester parameters ---
def test_invalid_tester_parameters_raise_value_error(sample_ohlcv_df: pd.DataFrame) -> None:
    """Invalid parameter values must raise ValueError rather than silently passing."""
    # commission
    with pytest.raises(ValueError, match="commission must be non-negative and finite"):
        PatternRankingTester(sample_ohlcv_df, commission=float("nan"))
    with pytest.raises(ValueError, match="commission must be non-negative and finite"):
        PatternRankingTester(sample_ohlcv_df, commission=-1.0)
    with pytest.raises(ValueError, match="commission must be non-negative and finite"):
        PatternRankingTester(sample_ohlcv_df, commission=float("inf"))

    # slippage
    with pytest.raises(ValueError, match="slippage must be non-negative and finite"):
        PatternRankingTester(sample_ohlcv_df, slippage=float("nan"))
    with pytest.raises(ValueError, match="slippage must be non-negative and finite"):
        PatternRankingTester(sample_ohlcv_df, slippage=-0.1)

    # initial_capital & position_size
    with pytest.raises(ValueError, match="initial_capital must be positive and finite"):
        PatternRankingTester(sample_ohlcv_df, initial_capital=0.0)
    with pytest.raises(ValueError, match="initial_capital must be positive and finite"):
        PatternRankingTester(sample_ohlcv_df, initial_capital=-1000.0)
    with pytest.raises(ValueError, match="position_size must be positive and finite"):
        PatternRankingTester(sample_ohlcv_df, position_size=0.0)

    # holding_period
    with pytest.raises(ValueError, match="holding_period must be None or a positive integer"):
        PatternRankingTester(sample_ohlcv_df, holding_period=0)
    with pytest.raises(ValueError, match="holding_period must be None or a positive integer"):
        PatternRankingTester(sample_ohlcv_df, holding_period=-5)
    with pytest.raises(ValueError, match="holding_period must be None or a positive integer"):
        PatternRankingTester(sample_ohlcv_df, holding_period=True)  # bool is not int

    # min_signals & min_trades
    with pytest.raises(ValueError, match="min_signals must be an integer >= 1"):
        PatternRankingTester(sample_ohlcv_df, min_signals=0)
    with pytest.raises(ValueError, match="min_trades must be None or an integer >= 1"):
        PatternRankingTester(sample_ohlcv_df, min_trades=0)

    # periods_per_year
    with pytest.raises(ValueError, match="periods_per_year must be positive and finite"):
        PatternRankingTester(sample_ohlcv_df, periods_per_year=-10.0)


# --- Issue 6: Market-aware annualization factors and custom periods_per_year ---
def test_market_aware_annualization_and_custom_periods_per_year(
    sample_ohlcv_df: pd.DataFrame,
) -> None:
    """Verify market-aware annualization factors and custom periods_per_year override."""
    # US equities hourly: standard 252 * 7 = 1764.0 (7 observations/day)
    us_hourly = resolve_periods_per_year("1h", symbol="AAPL")
    assert us_hourly == 1764.0

    # LSE equities hourly: 9 observations * 252 days = 2268.0
    lse_hourly = resolve_periods_per_year("1h", symbol="VOD.L")
    assert lse_hourly == 2268.0  # 252 * 9.0

    # European equities hourly: 9 observations * 252 days = 2268.0
    de_hourly = resolve_periods_per_year("1h", symbol="SAP.DE")
    assert de_hourly == 2268.0  # 252 * 9.0

    # Custom override (e.g. 8 hourly bars / day = 2016.0)
    custom_override = 2016.0
    res_override = resolve_periods_per_year("1h", symbol="AAPL", periods_per_year=custom_override)
    assert res_override == 2016.0

    # PatternRankingTester with custom periods_per_year
    tester = PatternRankingTester(sample_ohlcv_df, periods_per_year=1500.0)
    assert tester._periods_per_year == 1500.0
