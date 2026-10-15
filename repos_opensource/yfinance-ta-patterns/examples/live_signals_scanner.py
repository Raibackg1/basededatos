"""
Live Multi-Asset Candlestick Scanner with AI Confluence Scoring.
Real-time market scanner example (Forex + Crypto) with AI Confluence Scoring.

Features demonstrated:
- MarketDataLoader across 12 Forex & Crypto instruments
- PatternAnalyzer for multi-pattern signal detection on recent bars
- AIPatternScorer with Trend Regime (EMA 20/50/200) and canonical Wilder RSI (14)
- Automated Trade Setup generation: Entry, Stop Loss, Take Profit (1:1.5 Risk/Reward)
- Sorting and highlighting the TOP-1 active market opportunity

Usage:
    python examples/live_signals_scanner.py
    # or
    uv run examples/live_signals_scanner.py
"""

import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from yfinance_ta_patterns import MarketDataLoader, PatternAnalyzer
from yfinance_ta_patterns.ai.scorer import AIPatternScorer

WATCHLIST = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "AUDUSD=X",
    "USDCAD=X",
    "USDCHF=X",
    "NZDUSD=X",
    "EURGBP=X",
    "EURJPY=X",
    "GBPJPY=X",
    "BTC-USD",
    "ETH-USD",
]

TIMEFRAME = "1h"
PERIOD = "30d"
LOOKBACK_BARS = 3  # Scan the last 3 candles
MIN_CONFIDENCE = 0.50  # Confidence threshold (50%+)

all_signals = []

print(f"Scanning {len(WATCHLIST)} instruments on {TIMEFRAME} timeframe...")

for symbol in WATCHLIST:
    try:
        loader = MarketDataLoader(symbol, interval=TIMEFRAME, period=PERIOD, repair=False)
        df = loader.get_data()

        if df.empty or len(df) < 50:
            continue

        analyzer = PatternAnalyzer(df)
        scorer = AIPatternScorer(df)

        recent_timestamps = df.index[-LOOKBACK_BARS:]

        for pat in analyzer.pattern_functions:
            try:
                signals = analyzer.get_signals(pat)
            except NotImplementedError:
                continue

            if signals.empty:
                continue

            for ts in recent_timestamps:
                if ts in signals.index:
                    signal_val = signals.loc[ts]
                    if signal_val == 0:
                        continue

                    res = scorer.score_signal(pat, ts, signal_val)
                    if res.confidence_score >= MIN_CONFIDENCE:
                        setup = res.trade_setup

                        # Safe grade resolution (grade.value or str)
                        grade_label = (
                            res.grade.value if hasattr(res.grade, "value") else str(res.grade)
                        )
                        rsi_val = getattr(res, "rsi", 50.0)

                        all_signals.append(
                            {
                                "Symbol": symbol.replace("=X", ""),
                                "Direction": setup.direction
                                if setup
                                else ("BUY" if signal_val > 0 else "SELL"),
                                "Pattern": pat.replace("CDL", ""),
                                # Quantitative confluence heuristic, not win-rate probability
                                "Confluence": f"{res.confluence_score * 100:.1f}/100",
                                "Score_Raw": res.confidence_score,
                                "Grade": grade_label,
                                "Entry": setup.entry_price if setup else df.loc[ts, "Close"],
                                "StopLoss": setup.stop_loss if setup else 0.0,
                                "TakeProfit_1": setup.take_profit_1 if setup else 0.0,
                                "TakeProfit_2": setup.take_profit_2 if setup else 0.0,
                                "Risk_Reward": f"1:{setup.risk_reward_ratio:.1f}" if setup else "-",
                                "Trend": res.trend_regime,
                                "RSI": round(rsi_val, 1),
                                "Time": ts.strftime("%d.%m %H:%M"),
                            }
                        )
    except Exception as e:
        print(f"Error processing {symbol}: {e}")

if all_signals:
    signals_df = (
        pd.DataFrame(all_signals)
        .sort_values(by="Score_Raw", ascending=False)
        .drop(columns=["Score_Raw"])
    )

    print("\n" + "=" * 105)
    print(f" DETECTED SIGNALS: {len(signals_df)} (ranked by confluence quality)")
    print("=" * 105)

    print(
        signals_df[
            [
                "Time",
                "Symbol",
                "Direction",
                "Pattern",
                "Confluence",
                "Grade",
                "Entry",
                "StopLoss",
                "TakeProfit_1",
                "Risk_Reward",
                "Trend",
            ]
        ].to_string(index=False)
    )

    best = signals_df.iloc[0]
    print("\n" + "*" * 55)
    print(f" TOP-1 SETUP: {best['Symbol']} — {best['Direction']} ({best['Pattern']})")
    print(
        f" Signal Time: {best['Time']} | Confluence: {best['Confluence']} [{best['Grade']}] (quantitative heuristic, not win-rate probability)"
    )
    print(f" Market Trend: {best['Trend']} | RSI: {best['RSI']}")
    print(f" Entry Price: {best['Entry']}")
    print(f" Stop Loss:   {best['StopLoss']}")
    print(f" Take Profit: {best['TakeProfit_1']} (R:R {best['Risk_Reward']})")
    print("*" * 55)
else:
    print(
        f"\nNo setups with Confluence >= {MIN_CONFIDENCE * 100:.0f}/100 detected over the last {LOOKBACK_BARS} bar(s)."
    )
