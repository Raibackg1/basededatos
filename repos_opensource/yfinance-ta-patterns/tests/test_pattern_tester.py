import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from yfinance_ta_patterns.pattern_tester import PatternRankingTester, PatternResult


@pytest.fixture
def sample_ohlc_dataset() -> pd.DataFrame:
    """Generate OHLC data with multiple candles."""
    dates = pd.date_range("2025-01-01", periods=30, freq="1d", tz="UTC")
    np.random.seed(42)
    base = 100.0 + np.cumsum(np.random.randn(30))
    highs = base + np.random.uniform(0.5, 2.0, 30)
    lows = base - np.random.uniform(0.5, 2.0, 30)
    opens = base + np.random.uniform(-0.5, 0.5, 30)
    closes = base + np.random.uniform(-0.5, 0.5, 30)

    # Insert a few hammer / doji patterns
    opens[5] = closes[5]
    opens[15] = closes[15]

    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": np.random.randint(1000, 5000, 30),
        },
        index=dates,
    )


def test_pattern_result_dataclass():
    res = PatternResult(
        pattern_name="DOJI",
        total_signals=10,
        winning_trades=6,
        losing_trades=4,
        win_rate=60.0,
        total_pnl=150.0,
        avg_pnl=15.0,
        max_profit=40.0,
        max_loss=-20.0,
        sharpe_ratio=1.45,
    )
    assert res.pattern_name == "DOJI"
    assert res.win_rate == 60.0


def test_get_all_patterns():
    patterns = PatternRankingTester.get_all_patterns()
    assert len(patterns) > 0
    assert all(p.startswith("CDL") for p in patterns)
    assert "CDLDOJI" in patterns
    assert "CDLHAMMER" in patterns


def test_test_all_patterns(sample_ohlc_dataset):
    tester = PatternRankingTester(
        sample_ohlc_dataset,
        initial_capital=5000.0,
        position_size=200.0,
    )
    results = tester.test_all_patterns(filter_news=False)
    assert isinstance(results, list)


def test_calculate_trades(sample_ohlc_dataset):
    tester = PatternRankingTester(sample_ohlc_dataset)
    # 1: BUY, -1: SELL
    signals = np.zeros(len(sample_ohlc_dataset))
    signals[2] = 1
    signals[5] = -1
    signals[10] = 1
    signals[12] = -1

    trades = tester._calculate_trades(signals)
    assert len(trades) == 2
    for trade in trades:
        assert "entry_time" in trade
        assert "exit_time" in trade
        assert "entry_price" in trade
        assert "exit_price" in trade
        assert "pnl" in trade


def test_comparison_report_and_export(sample_ohlc_dataset):
    tester = PatternRankingTester(
        sample_ohlc_dataset,
        news_dates=["2025-01-05", "2025-01-10"],
    )
    tester.test_all_patterns()

    report_df = tester.get_comparison_report()
    assert isinstance(report_df, pd.DataFrame)

    top_patterns = tester.get_top_patterns(5)
    assert len(top_patterns) <= 5

    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = os.path.join(tmp_dir, "test_ranking.csv")
        tester.export_results(csv_path)
        assert os.path.exists(csv_path)
        exported_df = pd.read_csv(csv_path)
        assert "Pattern" in exported_df.columns


def test_calculate_trades_holding_period(sample_ohlc_dataset):
    # Only buy signals (like CDLHAMMER)
    signals = np.zeros(len(sample_ohlc_dataset))
    signals[2] = 1
    signals[8] = 1
    signals[15] = 1

    # Without holding_period: single long held until the end
    tester_default = PatternRankingTester(sample_ohlc_dataset, holding_period=None)
    trades_default = tester_default._calculate_trades(signals)
    assert len(trades_default) == 1

    # With holding_period=3: exits after 3 bars, allowing subsequent entries
    tester_holding = PatternRankingTester(sample_ohlc_dataset, holding_period=3)
    trades_holding = tester_holding._calculate_trades(signals)
    assert len(trades_holding) == 3
    for trade in trades_holding:
        assert trade["time_exit"] is True
