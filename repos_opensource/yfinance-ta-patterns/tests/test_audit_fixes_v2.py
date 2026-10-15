"""Regression unit tests for the 5 audit bug fixes (v2).

Issues covered:
1. Mark-to-market Drawdown & Intra-trade Unrealized Dips (100 -> 50 -> 110 => 50 max_drawdown)
2. Cross-Currency Position Sizing with Dynamic Historical FX Rates
3. Universal Crypto PnL Currency Conversion (BTC-EUR, ETH-BTC)
4. Crypto Cross 4h Completeness in Auto Mode (ETH-BTC requires 4 bars)
5. Completed Trades Threshold (min_trades filter & Total Trades reporting)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.data import (
    MarketDataLoader,
    classify_asset,
    resolve_asset_currencies,
)
from yfinance_ta_patterns.pattern_tester import (
    PatternRankingTester,
)


def test_drawdown_intra_trade_open_positions() -> None:
    """Issue 1: Drawdown must track intra-trade open position dips (100 -> 50 -> 110)."""
    dates = pd.date_range("2025-01-01", periods=3, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0, 50.0, 110.0],
            "High": [100.0, 50.0, 110.0],
            "Low": [100.0, 50.0, 110.0],
            "Close": [100.0, 50.0, 110.0],
            "Volume": [1000.0, 1000.0, 1000.0],
        },
        index=dates,
    )

    tester = PatternRankingTester(
        df,
        initial_capital=100.0,
        position_size=100.0,
        execution="close",
        symbol="TEST",
    )

    signals = np.array([1, 0, -1])
    trades = tester._calculate_trades(signals)
    assert len(trades) == 1
    t = trades[0]
    assert t["entry_price"] == 100.0
    assert t["exit_price"] == 110.0
    assert t["pnl"] == 10.0

    # Test MTM equity curve & drawdown via mock / direct calculation
    # Simulate a pattern returning signals
    class MockTalib:
        @staticmethod
        def CDLMOCK(open_, high, low, close):
            return np.array([100, 0, -100])

    import yfinance_ta_patterns.pattern_tester as pt_module

    orig_talib = pt_module.talib
    pt_module.talib = MockTalib  # type: ignore[assignment]
    try:
        res = tester._test_single_pattern("CDLMOCK", filter_news=False)
        assert res is not None
        # Trade closed with +10 profit, but intra-trade dropped to 50
        # Peak was 100, dip at bar 1 was to 50 -> max_drawdown must be 50.0!
        assert res.max_drawdown == pytest.approx(50.0, abs=1e-3)
        assert res.total_pnl == pytest.approx(10.0, abs=1e-3)
        assert res.equity_curve is not None
        # Equity curve should be [initial_capital, bar0_eq, bar1_eq, bar2_eq]
        assert res.equity_curve[0] == pytest.approx(100.0)
        assert res.equity_curve[1] == pytest.approx(100.0)
        assert res.equity_curve[2] == pytest.approx(50.0)
        assert res.equity_curve[3] == pytest.approx(110.0)
    finally:
        pt_module.talib = orig_talib


def test_cross_currency_position_units_historical() -> None:
    """Issue 2: Cross-currency position sizing with dynamic historical FX rates."""
    dates = pd.date_range("2025-01-01", periods=3, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [0.85, 0.85, 0.85],
            "High": [0.86, 0.86, 0.86],
            "Low": [0.84, 0.84, 0.84],
            "Close": [0.85, 0.85, 0.85],
            "Volume": [1000.0, 1000.0, 1000.0],
        },
        index=dates,
    )

    # Historical rates: USDGBP varying from 0.75 to 0.80
    usd_gbp = pd.Series([0.75, 0.80, 0.78], index=dates)
    fx_hist = {"USDGBP": usd_gbp}

    # Tester with USD account trading EURGBP (base=EUR, quote=GBP)
    tester_usd = PatternRankingTester(
        df,
        symbol="EURGBP",
        account_currency="USD",
        position_size=10000.0,
        fx_history=fx_hist,
        strict_fx=True,
    )

    # At bar 0: rate(USD->GBP) is 0.75. Position size = 10,000 USD -> 7,500 GBP.
    # Units of EUR = 7,500 / 0.85 = 8823.529 EUR
    units_t0 = tester_usd._calc_position_units(0.85, entry_time=dates[0])
    assert units_t0 == pytest.approx(10000.0 * 0.75 / 0.85, rel=1e-4)

    # At bar 1: rate(USD->GBP) is 0.80. Position size = 10,000 USD -> 8,000 GBP.
    # Units of EUR = 8,000 / 0.85 = 9411.764 EUR
    units_t1 = tester_usd._calc_position_units(0.85, entry_time=dates[1])
    assert units_t1 == pytest.approx(10000.0 * 0.80 / 0.85, rel=1e-4)
    assert units_t1 > units_t0

    # Strict FX raises error on missing date
    missing_date = pd.Timestamp("2024-01-01", tz="UTC")
    with pytest.raises(ValueError, match="Missing historical FX rate"):
        tester_usd._calc_position_units(0.85, entry_time=missing_date)

    # JPY account trading EURGBP
    jpy_gbp = pd.Series([0.005, 0.0055, 0.0052], index=dates)
    tester_jpy = PatternRankingTester(
        df,
        symbol="EURGBP",
        account_currency="JPY",
        position_size=1_000_000.0,
        fx_history={"JPYGBP": jpy_gbp},
        strict_fx=True,
    )
    units_jpy = tester_jpy._calc_position_units(0.85, entry_time=dates[0])
    assert units_jpy == pytest.approx((1_000_000.0 * 0.005) / 0.85, rel=1e-4)


def test_crypto_pnl_currency_conversion() -> None:
    """Issue 3: Crypto PnL currency conversion (BTC-EUR, ETH-BTC)."""
    dates = pd.date_range("2025-01-01", periods=2, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [50000.0, 50100.0],
            "High": [50200.0, 50200.0],
            "Low": [49900.0, 49900.0],
            "Close": [50100.0, 50100.0],
            "Volume": [100.0, 100.0],
        },
        index=dates,
    )

    # BTC-EUR: quote currency is EUR. Raw PnL is in EUR.
    # EURUSD rate = 1.10. Converted PnL to USD must be 100 EUR * 1.10 = 110 USD.
    eur_usd = pd.Series([1.10, 1.10], index=dates)
    tester_btc_eur = PatternRankingTester(
        df,
        symbol="BTC-EUR",
        account_currency="USD",
        fx_history={"EURUSD": eur_usd},
        strict_fx=True,
    )
    assert tester_btc_eur._base_currency == "BTC"
    assert tester_btc_eur._quote_currency == "EUR"

    converted_pnl = tester_btc_eur._convert_pnl_to_account_currency(
        raw_pnl=100.0, exit_price=50100.0, exit_time=dates[0]
    )
    assert converted_pnl == pytest.approx(110.0, rel=1e-4)

    # ETH-BTC: quote currency is BTC. Raw PnL is in BTC.
    # BTCUSD rate = 60,000. Converted PnL to USD must be 0.5 BTC * 60,000 = 30,000 USD.
    btc_usd = pd.Series([60000.0, 60000.0], index=dates)
    tester_eth_btc = PatternRankingTester(
        df,
        symbol="ETH-BTC",
        account_currency="USD",
        fx_history={"BTCUSD": btc_usd},
        strict_fx=True,
    )
    assert tester_eth_btc._base_currency == "ETH"
    assert tester_eth_btc._quote_currency == "BTC"

    converted_eth_pnl = tester_eth_btc._convert_pnl_to_account_currency(
        raw_pnl=0.5, exit_price=0.06, exit_time=dates[0]
    )
    assert converted_eth_pnl == pytest.approx(30000.0, rel=1e-4)


def test_crypto_cross_4h_completeness_auto() -> None:
    """Issue 4: Crypto cross ETH-BTC in auto mode strictly requires 4 bars per 4h candle."""
    # Test asset classification and currency resolution
    assert classify_asset("ETH-BTC", "auto") == "crypto"
    assert classify_asset("SOL-BTC", "auto") == "crypto"
    assert classify_asset("BTC-EUR", "auto") == "crypto"
    assert classify_asset("BTC-USDT", "auto") == "crypto"
    assert classify_asset("EURUSD=X", "auto") == "forex"
    assert classify_asset("AAPL", "auto") == "stock"

    base, quote = resolve_asset_currencies("ETH-BTC")
    assert (base, quote) == ("ETH", "BTC")

    # In auto mode, ETH-BTC must be recognized as crypto and reject incomplete 4h buckets (e.g. 1 bar)
    loader = MarketDataLoader("ETH-BTC", interval="4h", asset_type="auto", timezone="UTC")
    assert loader.asset_type.lower() == "auto"

    # Provide only 1 hourly bar inside the [00:00, 04:00) window
    idx = pd.DatetimeIndex(["2025-01-01 00:00:00+00:00"])
    df = pd.DataFrame(
        {
            "Open": [0.05],
            "High": [0.051],
            "Low": [0.049],
            "Close": [0.05],
            "Volume": [10.0],
        },
        index=idx,
    )

    resampled = loader._resample_if_needed(df)
    # The bucket contains only 1 bar out of required 4 -> must be filtered out
    assert len(resampled) == 0

    # With all 4 bars, the bucket must be kept
    idx4 = pd.date_range("2025-01-01 00:00:00+00:00", periods=4, freq="1h")
    df4 = pd.DataFrame(
        {
            "Open": [0.05] * 4,
            "High": [0.051] * 4,
            "Low": [0.049] * 4,
            "Close": [0.05] * 4,
            "Volume": [10.0] * 4,
        },
        index=idx4,
    )
    resampled4 = loader._resample_if_needed(df4)
    assert len(resampled4) == 1


def test_min_trades_threshold_filter(tmp_path: pytest.TempPathFactory) -> None:
    """Issue 5: Completed Trades Threshold (min_trades) and Total Trades reporting."""
    dates = pd.date_range("2025-01-01", periods=10, freq="1d", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(10)],
            "High": [102.0 + i for i in range(10)],
            "Low": [99.0 + i for i in range(10)],
            "Close": [101.0 + i for i in range(10)],
            "Volume": [1000.0] * 10,
        },
        index=dates,
    )

    # Pattern generates 8 bullish signals in a row, but exits only on bar 9 -> 1 trade
    class MockTalibMultiSignal:
        @staticmethod
        def CDLHAMMER(open_, high, low, close):
            # Fires 8 signals
            vals = np.zeros(len(open_))
            vals[1:9] = 100
            return vals

        @staticmethod
        def CDLDOJI(open_, high, low, close):
            # Fires alternating buy and sell signals creating multiple trades
            vals = np.zeros(len(open_))
            vals[1] = 100
            vals[3] = -100
            vals[5] = 100
            vals[7] = -100
            return vals

    import yfinance_ta_patterns.pattern_tester as pt_module

    orig_talib = pt_module.talib
    pt_module.talib = MockTalibMultiSignal  # type: ignore[assignment]

    try:
        # Tester with force_exit_on_last_bar so CDLHAMMER closes its 1 trade
        tester = PatternRankingTester(
            df,
            force_exit_on_last_bar=True,
            min_signals=1,
            min_trades=2,  # Requires at least 2 completed trades
        )

        res_hammer = tester.test_pattern("CDLHAMMER")
        assert res_hammer is not None
        assert res_hammer.total_signals == 8
        assert res_hammer.total_trades == 1

        res_doji = tester.test_pattern("CDLDOJI")
        assert res_doji is not None
        assert res_doji.total_trades >= 2

        # test_all_patterns with min_trades=2 should filter out HAMMER (1 trade) but keep DOJI
        results = tester.test_all_patterns(min_trades=2)
        pattern_names = [r.pattern_name for r in results]
        assert "HAMMER" not in pattern_names
        assert "DOJI" in pattern_names

        # test_all_patterns with min_trades=1 should include both
        results_all = tester.test_all_patterns(min_trades=1)
        all_names = [r.pattern_name for r in results_all]
        assert "HAMMER" in all_names
        assert "DOJI" in all_names

        # Verify export_results includes "Total Trades"
        csv_file = tmp_path / "ranking.csv"
        tester.export_results(str(csv_file))
        exported_df = pd.read_csv(csv_file)
        assert "Total Trades" in exported_df.columns
        assert exported_df["Total Trades"].iloc[0] > 0

        # Verify get_comparison_report includes Trades columns
        comp_df = tester.get_comparison_report()
        assert "Trades (No Filter)" in comp_df.columns
        assert "Trades (With Filter)" in comp_df.columns
    finally:
        pt_module.talib = orig_talib
