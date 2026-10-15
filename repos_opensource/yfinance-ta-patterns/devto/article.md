If you have ever attempted to build an algorithmic trading bot in Python, you have almost certainly walked this exact path:

1. You install `TA-Lib` (after wrestling with C compilers, missing headers, and broken Windows wheels for an hour).
2. You write a script scanning for classic candlestick patterns: **Bullish Engulfing**, **Hammer**, **Morning Star**.
3. You backtest it on your favorite stock or crypto pair.
4. **Result:** A disappointing, downward-sloping equity curve with a win rate hovering around 48% to 51%.

Why? Because in institutional quantitative finance, **naked candlestick patterns are treated as little more than random noise**.

A "Hammer" appearing in the middle of a low-volume consolidation against a cascading 200 EMA downtrend has almost zero statistical edge. But that *same* Hammer forming at the 200 EMA support, accompanied by a 2.5x Relative Volume (RVOL) spike and an oversold RSI (14) rebound, represents an institutional accumulation footprint.

Today, I’m open-sourcing **[yfinance-ta-patterns](https://github.com/eminsk/yfinance-ta-patterns)** — an institutional-grade Python framework and CLI designed to bridge the gap between classic technical analysis, quantitative confluence modeling, and modern LLM-driven market intelligence.

---

## 🚀 Key Highlights of `yfinance-ta-patterns`

- **Zero-C Build Installation by Default:** Includes a built-in vectorized pure-Python/NumPy engine for core candlestick patterns — install in seconds with `pip` or `uv` without needing C compilers or TA-Lib binaries.
- **Full 61-Pattern Scanning:** Native TA-Lib acceleration supported with pre-built binary wheels for Windows x64 across Python 3.12, 3.13, 3.14, and Python 3.15 (including Free-Threaded No-GIL).
- **Multi-Factor AI Confluence Engine:** Calculates a deterministic quantitative confluence score ($0.0 - 1.0$) evaluating EMA trend alignment (20/50/200), zero-lookahead Relative Volume (RVOL), canonical Wilder's RSI (14), and ATR volatility expansion.
- **Automated Trade Setup Generator:** Instantly calculates Entry, ATR-based Invalidation Stop-Loss, and Multi-Tier Take-Profit targets (1.5R and 3.0R risk/reward).
- **LLM-Ready Market Intelligence:** Generates executive Markdown briefs and structured JSON payloads tailored for autonomous AI agents and frontier reasoning models (Claude 3.7 Sonnet, OpenAI o3-mini/o1, DeepSeek-R1, Gemini 2.5, Ollama).
- **Unbiased Quantitative Backtester:** Simulates realistic execution on the next bar open (`Open[i+1]`), accounting for slippage, trading fees, FX currency conversion, and periodic Sharpe ratios.
- **Python 3.8 to 3.15 & Free-Threaded (PEP 703 No-GIL) Ready:** Optimized for high-throughput multi-pair parallel scanning without GIL contention.

```
       [Raw Multi-Asset Data (yfinance)]
           Stocks | Crypto | Forex | Commodities
                       │
                       ▼
          ┌───────────────────────────┐
          │  Candle Normalizer & QA   │ ── Zero-lookahead, UTC 4h resample
          └───────────────────────────┘
                       │
                       ▼
          ┌───────────────────────────┐
          │ Pattern Recognition Engine│ ── 61 TA-Lib Patterns + Pure NumPy Engine
          └───────────────────────────┘
                       │
                       ▼
          ┌───────────────────────────┐
          │   AI Confluence Scorer    │ ── EMA 20/50/200 + RVOL + RSI + ATR
          └───────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
┌──────────────────┐        ┌───────────────────────┐
│ Algorithmic Setups│        │  AI Agent Markdown    │
│ Entry, SL, TP1/2 │        │  Briefs & JSON Schema │
└──────────────────┘        └───────────────────────┘
```

---

## 🛠️ The Architecture: Quantitative Confluence vs. Naked Signals

Traditional libraries treat a candlestick pattern as a binary boolean: `pattern detected: True/False`.

In `yfinance-ta-patterns`, detecting a pattern is merely step one. The signal is then routed into the **AIPatternScorer**, which computes a multi-dimensional quantitative confluence score based on four objective market factors:

### 1. Multi-EMA Trend Alignment
The engine verifies alignment across three exponential moving averages:
- **Fast EMA (20):** Short-term momentum
- **Medium EMA (50):** Swing trend
- **Slow EMA (200):** Institutional macro regime

Bullish patterns receive maximum scoring when price action trades above an ascending 200 EMA with confirmed 20/50 bullish alignment.

### 2. Relative Volume Surge (RVOL)
Institutional accumulation leaves volume footprints. The scorer computes zero-lookahead Relative Volume ($RVOL = \frac{Volume_t}{SMA(Volume, 20)}$). Patterns accompanied by $RVOL > 1.8x$ receive significant scoring weight, filtering out low-liquidity false breaks.

### 3. Canonical Wilder's RSI (14) Momentum Exhaustion
Using J. Welles Wilder's exact smoothing algorithm, the engine measures whether the reversal pattern occurs at momentum extremes (oversold $< 35$ for bullish reversals, overbought $> 65$ for bearish reversals) or exhibits momentum divergence.

### 4. Canonical Wilder's ATR (14) Volatility Expansion
Evaluates whether the pattern candle body is dominant relative to recent Average True Range (filtering out doji indecision candles where decisive expansion was required).

---

## ⚡ 10-Second Quickstart

### Installation

`yfinance-ta-patterns` installs out-of-the-box with pure-Python fallbacks:

```bash
pip install yfinance-ta-patterns
```

Or with `uv`:

```bash
uv add yfinance-ta-patterns
```

*(Optional: Native TA-Lib acceleration can be installed via `pip install "yfinance-ta-patterns[talib]"` or using pre-built wheels).*

---

### Python Code Example: Live Market Scanner

Here is how you scan multi-asset pairs, detect patterns, score confluence, and print an automated trade setup in just a few lines of code:

```python
from yfinance_ta_patterns import MarketDataLoader, PatternAnalyzer
from yfinance_ta_patterns.ai.scorer import AIPatternScorer

# 1. Fetch multi-asset data (Crypto, Stocks, Forex, Commodities)
loader = MarketDataLoader(symbol="NVDA", interval="1h", period="30d")
df = loader.get_data()

# 2. Detect candlestick patterns
analyzer = PatternAnalyzer(df)
pattern_signals = analyzer.find_patterns(last_n_bars=3)

# 3. Score confluence with the AI Quantitative Engine
scorer = AIPatternScorer(df)

for signal in pattern_signals:
    score = scorer.score_pattern(
        pattern_name=signal["pattern"], bar_idx=signal["index"], signal_type=signal["direction"]
    )

    # Filter for high-confluence institutional setups
    if score.confluence_score >= 0.70:
        print(f"🔥 HIGH CONFLUENCE SETUP: {signal['pattern']} on {signal['timestamp']}")
        print(f"   Confluence Score: {score.confluence_score:.2f} / 1.00")
        print(f"   Trend Regime:     {score.trend_alignment}")
        print(f"   Relative Volume:  {score.rvol:.2f}x")
        print(f"   Wilder RSI (14):  {score.rsi:.1f}")

        # Automated Trade Setup
        setup = score.trade_setup
        print(f"   Entry:       ${setup['entry']:.2f}")
        print(f"   Stop Loss:   ${setup['stop_loss']:.2f} (ATR-based)")
        print(f"   Take Profit: ${setup['take_profit_1']:.2f} (1.5R)")
```

---

## 🤖 Generating LLM Market Briefs for AI Agents

Modern trading architectures increasingly rely on LLM agents (Claude, GPT, Gemini, local Ollama models) for executive synthesis.

`yfinance-ta-patterns` includes an **AI Market Analyst** module that transforms technical data into structured briefs and JSON schemas:

```python
from yfinance_ta_patterns.ai.analyst import AIMarketAnalyst

analyst = AIMarketAnalyst()
brief = analyst.generate_market_brief(df, pattern_signals, symbol="BTC-USD")

# Print executive Markdown brief ready for consumption by humans or AI agents
print(brief.markdown)
```

The output gives your LLM agent everything it needs — macroeconomic context, multi-timeframe trend status, pattern confluence, and risk parameters — without hallucinated indicators.

---

## 🖥️ Instant CLI Execution (No Code Required)

Prefer running from the terminal? `yfinance-ta-patterns` includes a lightning-fast CLI:

```bash
# Scan NVIDIA 1-hour candles with AI confluence
yftp --symbol NVDA --timeframe 1h --ai

# Scan Bitcoin with all 61 patterns
yftp --symbol BTC-USD --all-patterns --timeframe 4h

# Run directly without installing into your local environment via uvx:
uvx --from yfinance-ta-patterns yftp --symbol AAPL --timeframe 1d --ai
```

---

## 🧵 Python 3.14 & Free-Threaded No-GIL (PEP 703) Ready

High-frequency market scanners often monitor hundreds of currency pairs or crypto tickers simultaneously. 

`yfinance-ta-patterns` is designed for modern Python environments:
- Fully compatible from **Python 3.8 up to Python 3.15**.
- Tested and verified on **Free-Threaded CPython (3.13t, 3.14t, 3.15t No-GIL)**: Run parallel scanning threads across all CPU cores without Python GIL bottlenecks.
- Pre-built binary wheels available for Windows x64, macOS (Apple Silicon & Intel), and Linux.

---

## 📦 Open Source Links & Resources

- 📦 **PyPI:** [pypi.org/project/yfinance-ta-patterns](https://pypi.org/project/yfinance-ta-patterns/)
- 🐙 **GitHub:** [github.com/eminsk/yfinance-ta-patterns](https://github.com/eminsk/yfinance-ta-patterns)
- 📝 **License:** MIT

If you're interested in algorithmic trading, quantitative finance, or building AI trading agents, give **yfinance-ta-patterns** a try!

If you find the project useful, please consider dropping a **Star ⭐ on [GitHub](https://github.com/eminsk/yfinance-ta-patterns)** — it helps the project grow and reach more developers!
