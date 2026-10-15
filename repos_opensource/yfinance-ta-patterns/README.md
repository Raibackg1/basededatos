# Market Candlestick & AI Pattern Scanner (`yfinance-ta-patterns`)

[![PyPI](https://img.shields.io/pypi/v/yfinance-ta-patterns.svg?style=flat&logo=pypi)](https://pypi.org/project/yfinance-ta-patterns/)
[![Conda-Forge](https://img.shields.io/conda/vn/conda-forge/yfinance-ta-patterns.svg?style=flat&logo=condaforge)](https://anaconda.org/conda-forge/yfinance-ta-patterns)
[![Ubuntu / Debian PPA](https://img.shields.io/badge/Ubuntu%20%2F%20Debian-APT%20PPA-E95420?logo=ubuntu&logoColor=white)](https://eminsk.github.io/ppa/)
[![Downloads](https://static.pepy.tech/badge/yfinance-ta-patterns)](https://pepy.tech/project/yfinance-ta-patterns)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/eminsk/yfinance-ta-patterns/blob/main/notebooks/yfinance_ta_patterns_quickstart.ipynb)
[![Python](https://img.shields.io/pypi/pyversions/yfinance-ta-patterns)](https://pypi.org/project/yfinance-ta-patterns/)
[![PyPy](https://img.shields.io/badge/PyPy-3.8%20--%203.12-orange.svg)](https://www.pypy.org/)
[![No-GIL](https://img.shields.io/badge/No--GIL-3.13t%20--%203.16t-purple.svg)](https://peps.python.org/pep-0703/)
[![MCP Server](https://img.shields.io/badge/MCP-Native_Stdio_Server-00a67e.svg)](#native-mcp-model-context-protocol-server-for-ai-agents)
[![CI](https://github.com/eminsk/yfinance-ta-patterns/actions/workflows/ci.yml/badge.svg)](https://github.com/eminsk/yfinance-ta-patterns/actions)
[![GitHub Stars](https://img.shields.io/github/stars/eminsk/yfinance-ta-patterns?style=flat&logo=github)](https://github.com/eminsk/yfinance-ta-patterns/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/eminsk/yfinance-ta-patterns?style=flat&color=red&logo=github)](https://github.com/eminsk/yfinance-ta-patterns/issues)
[![GitHub Discussions](https://img.shields.io/badge/Discussions-Join_Community-blue?style=flat&logo=github)](https://github.com/eminsk/yfinance-ta-patterns/discussions)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Featured on Context Unfiltered](https://img.shields.io/badge/Featured%20on-Context%20Unfiltered-E4405F?style=flat&logo=instagram&logoColor=white)](https://www.instagram.com/reels/Ddz3Qs5IwKH/)
[![Donate USDT](https://img.shields.io/badge/Donate-USDT_(TRC20)-26A17B?style=flat&logo=tether&logoColor=white)](https://tronscan.org/#/address/TDVbEdnpgNgoAhNcn1EwXxWHiR3RKLit5P)

High-performance Python library, CLI, and Native MCP Server (`yfinance-ta-mcp`) that downloads multi-asset market data via `yfinance`, detects TA-Lib candlestick patterns, and enriches raw signals using an **AI/Quant Confluence Engine** to generate multi-factor confluence scores (deterministic quantitative confluence heuristic, not uncalibrated win-rate probability), trade setups, and LLM-ready market briefs.

Universal runtime compatibility across **CPython 3.8 to 3.16, free-threaded No-GIL 3.13t to 3.16t, PyPy 3.8 to 3.12 with high-speed JIT tracing, and legacy Windows 7+ support**, installed directly from PyPI with zero compilation required.

> ### 🤝 Community, Issues & Support
> - 🎬 **Featured on Video:** In-depth walkthrough by **Context Unfiltered (Tech & Science)**: *"New Python Library Combines Candlestick Patterns With AI"* — [Watch on Instagram Reels](https://www.instagram.com/reels/Ddz3Qs5IwKH/)
> - 🐛 **Found a bug or calculation issue?** Please [Open an Issue](https://github.com/eminsk/yfinance-ta-patterns/issues) — reports are tracked and resolved quickly!
> - 💬 **Questions, strategies, or feature requests?** Join the conversation in [GitHub Discussions](https://github.com/eminsk/yfinance-ta-patterns/discussions).
> - ⭐ **Find this project useful?** Give it a star on GitHub — it helps more traders and quants discover the tool!
> - ☕ **Support development (USDT TRC-20):** `TDVbEdnpgNgoAhNcn1EwXxWHiR3RKLit5P` ([Verify on TronScan](https://tronscan.org/#/address/TDVbEdnpgNgoAhNcn1EwXxWHiR3RKLit5P))

---

## Key Features

- **Multi-Asset Data Loader**: Universal fetching and candle normalization for stocks (`AAPL`, `NVDA`), crypto (`BTC-USD`), commodities (`GC=F`), indices (`^GSPC`), and forex pairs (`EURUSD`). Supports custom date ranges (`start`, `end`), timezone conversion, UTC-anchored 4h resampling, and raw OHLC integrity validation.
- **TA-Lib Pattern Detection**: Full recognition engine across **all 61 canonical TA-Lib candlestick patterns** via native C TA-Lib OR built-in pure-Python / NumPy fallback engine (all 61 patterns supported in both modes!).
- **AI Pattern Confluence Scorer**: Multi-factor confluence score ($0.0 - 1.0$) evaluating quantitative confluence (deterministic heuristic, not win-rate probability):
  - Multi-EMA trend alignment (20, 50, 200 EMA) with safe warm-up handling
  - Zero-lookahead Relative Volume surge (RVOL)
  - Canonical Wilder's RSI (14) momentum exhaustion & divergence
  - Canonical Wilder's ATR (14) volatility expansion & candle body dominance
- **Automated Trade Setups**: Computes entry price, ATR-based invalidation stop-loss, and multi-tier take-profit targets (1.5x / 3.0x risk/reward).
- **AI Market Analyst & LLM Integration**: Generates executive markdown briefs, JSON payloads, and engineered reasoning prompts tailored for frontier AI agents and reasoning models (**Claude 3.7 Sonnet**, **OpenAI o3-mini / o1**, **DeepSeek-R1 / V3**, **Gemini 2.5 Flash / Pro**, **GPT-4.5**, Ollama).
- **Quantitative Pattern Ranking & Backtesting**: Unbiased next-open execution (`Open[i+1]`), symmetric Long/Short trading, universal FX quote-currency conversion, transaction cost modeling (commissions & slippage), overfitting filters (`min_signals`), and periodic Sharpe ratio accounting for non-trading hold periods.

---

## 🧩 Universal Compatibility Matrix

| Runtime / Implementation | Supported Versions | Execution Mode | Status | Installation Source |
|:---|:---|:---|:---:|:---:|
| **CPython (Standard)** | 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16 | Standard bytecode + GIL | ✅ 100% Supported | Direct PyPI (`pip` / `uv`) |
| **CPython (Free-Threaded)** | 3.13t, 3.14t, 3.15t, 3.16t | Multi-core No-GIL (PEP 703) | ✅ 100% Supported | Direct PyPI (`pip` / `uv`) |
| **PyPy (JIT Accelerated)** | 3.8, 3.9, 3.10, 3.11, 3.12 | High-speed JIT tracing | ✅ 100% Supported | Direct PyPI (`pip` / `uv`) |
| **Operating Systems** | Windows (7, 8, 10, 11), Linux, macOS (Intel & Apple Silicon) | x86_64, ARM64 | ✅ 100% Supported | Direct PyPI (`pip` / `uv`) |

---

## Quick start

### 1. Installation

`yfinance-ta-patterns` installs in seconds with **zero C build requirements** by default, using an internal vectorized pure-Python/NumPy engine for candlestick pattern recognition.

#### 🚀 Quick Installation by Platform

| Platform / Tool | Command | Description |
|:---|:---|:---|
| **Conda / Mamba** | `conda install -c conda-forge yfinance-ta-patterns` | Recommended for Quant, Finance & Data Science |
| **Conda + Native TA-Lib** | `conda install -c conda-forge yfinance-ta-patterns ta-lib` | Full C-accelerated TA-Lib with **zero compilation** |
| **Pixi** | `pixi add yfinance-ta-patterns` | Modern high-speed reproducible Conda workflow |
| **Ubuntu / Debian (APT PPA)** | `curl -sS https://eminsk.github.io/ppa/setup.sh \| sudo bash`<br>`sudo apt install python3-yfinance-ta-patterns` | Official PPA repository (pure apt, zero URLs) |
| **Ubuntu / Debian (.deb)** | `sudo apt install ./python3-yfinance-ta-patterns_0.3.49-1_all.deb` | Native `.deb` package from [Releases](https://github.com/eminsk/yfinance-ta-patterns/releases) |
| **Ubuntu / Debian (pipx)** | `sudo apt install pipx && pipx install yfinance-ta-patterns` | Isolated global CLI tool install (PEP 668 compliant) |
| **uv (Python)** | `uv add yfinance-ta-patterns` | Sub-second pure Python installation |
| **pip (Python)** | `pip install yfinance-ta-patterns` | Universal PyPI installation |

#### Option A: Using `uv` (Fastest & Recommended)

```bash
# 1. Standard install (100% Autonomous AI + Pattern Engine, zero C compiler or Scikit-Learn required):
uv add yfinance-ta-patterns

# 2. Optional: with C TA-Lib extension if you already have local C headers:
uv add "yfinance-ta-patterns[talib]"

# 3. Instant execution without installing into environment:
uvx --from yfinance-ta-patterns yftp --all-patterns --symbol AAPL --timeframe 1h --ai
```

> [!TIP]
> **Why `--no-config`?** Passing `--no-config` tells `uv` to ignore any local or parent `uv.toml` settings (such as local wheel registries or find-links overrides), ensuring a clean, isolated, and reproducible installation directly from PyPI in any project directory.

> [!IMPORTANT]
> ### ⚡ Free-Threaded Python / No-GIL Guide (PEP 703: 3.13t, 3.14t, 3.15t, 3.16t)
>
> Python 3.13+ introduces experimental free-threaded (No-GIL) builds. `yfinance-ta-patterns` is verified on Windows, Linux, and macOS under free-threaded CPython. Here is what you need to know for a smooth No-GIL setup on Windows:
>
> #### 1. Python Version Selection & Wheel Availability
> - **Python 3.13t, 3.14t, 3.15t & 3.16t (Recommended)**: Fully supported out-of-the-box directly from PyPI. Our built-in vectorized engine runs with zero C compilation and full multi-core parallel scaling with the GIL disabled.
>
> #### 2. Understanding Automatic GIL Re-Enablement
> In free-threaded interpreters, `sys._is_gil_enabled()` may return `True` for two common reasons:
> 1. **Global Environment Variable**: A Windows user/system variable `PYTHON_GIL=1` forces the GIL on at startup. Check in PowerShell:
>    ```powershell
>    [System.Environment]::GetEnvironmentVariable('PYTHON_GIL', 'User')
>    ```
> 2. **PEP 703 C-Extension Protection**: When loading C extensions (such as `talib._ta_lib`) that have not yet declared `Py_MOD_GIL_NOT_USED`, CPython automatically re-enables the GIL to protect thread safety and emits a `RuntimeWarning`.
>
> #### 3. Running in True No-GIL Mode
> To run at full multi-core performance with the GIL disabled:
> - **Command Line Flag**:
>   ```bash
>   python -X gil=0 my_scanner.py
>   ```
> - **PowerShell**:
>   ```powershell
>   $env:PYTHON_GIL = "0"
>   python my_scanner.py
>   ```
> - **CMD**:
>   ```cmd
>   set PYTHON_GIL=0
>   python my_scanner.py
>   ```
> - **Pure-Python Engine**: When native TA-Lib is not installed, the built-in NumPy fallback engine keeps the GIL disabled out-of-the-box without extra flags.
>
> #### 4. Verification
> Run `yftp --check-talib` to inspect the runtime environment:
> ```bash
> yftp --check-talib
> ```
> Or verify programmatically:
> ```python
> from yfinance_ta_patterns import is_freethreaded, is_gil_enabled
> print("Free-Threaded build:", is_freethreaded())
> print("GIL active:", is_gil_enabled())
> ```

#### Option B: Using Standard `pip`

```bash
# Standard install (pure-Python fallback by default):
pip install yfinance-ta-patterns

# Optional: With native C TA-Lib acceleration (requires ta-lib C headers):
pip install "yfinance-ta-patterns[talib]"
```

#### Option C: Native C TA-Lib on Linux & macOS (Optional)

> [!NOTE]
> **Native C TA-Lib is 100% OPTIONAL**: `yfinance-ta-patterns` already includes a built-in high-speed vectorized NumPy pattern recognition engine that works immediately with **zero compilation**.
>
> If you want the full C-accelerated TA-Lib engine without any manual compilation:
> - **Via Conda (Recommended — Zero C builds)**:
>   ```bash
>   conda install -c conda-forge yfinance-ta-patterns ta-lib
>   ```
> - **Or manual C compiler build from source**:

- **macOS (Homebrew):**
  ```bash
  brew install ta-lib
  export TA_INCLUDE_PATH="$(brew --prefix)/include"
  export TA_LIBRARY_PATH="$(brew --prefix)/lib"
  uv pip install ta-lib
  ```

- **Linux (Ubuntu / Debian / Arch):**
  ```bash
  # Ubuntu / Debian:
  sudo apt-get update && sudo apt-get install -y libta-lib0 libta-lib-dev
  # Arch Linux:
  yay -S ta-lib

  # Then install the python wrapper:
  uv pip install ta-lib
  ```




#### 🐍 Python Version Compatibility Matrix

| Python Version | Execution Mode | Installation Status | Recommendation |
|:---:|:---:|:---:|---|
| **Python 3.15t** | **Free-Threaded (No-GIL)** | ✅ **100% Supported** | Start with `PYTHON_GIL=0` or `-X gil=0`; instant sub-second install from PyPI. |
| **Python 3.15** | **Standard (GIL)** | ✅ **100% Supported** | Direct universal PyPI installation with zero C compilation required. |
| **Python 3.14t** | **Free-Threaded (No-GIL)** | ✅ **100% Supported** | Start with `PYTHON_GIL=0` or `-X gil=0`; instant sub-second install from PyPI. |
| **Python 3.14** | **Standard (GIL)** | ✅ **100% Supported** | Direct universal PyPI installation with zero C compilation required. |
| **Python 3.13t** | **Free-Threaded (No-GIL)** | ✅ **100% Supported** | Start with `PYTHON_GIL=0` or `-X gil=0`; instant sub-second install from PyPI. |
| **Python 3.13** | **Standard (GIL)** | ✅ **100% Supported** | Current stable Python release. Full instant PyPI installation. |
| **Python 3.12** | **Standard (GIL)** | ✅ **100% Supported** | Long-Term Support release with instant sub-second installation. |
| **PyPy 3.8–3.12** | **JIT Accelerated** | ✅ **100% Supported** | High-speed JIT tracing with pure-Python fallback (all 61 TA-Lib patterns supported, zero C-API compilation required). |

---

## 🤖 Native MCP (Model Context Protocol) Server

Connect **yfinance-ta-patterns** directly to **Claude Code**, **Antigravity**, **Claude Desktop**, **Cursor**, or **Windsurf** via the built-in `yfinance-ta-mcp` JSON-RPC 2.0 server (`ta_scan_symbol`, `ta_scan_watchlist`, `ta_backtest_patterns`, `ta_list_patterns`, `ta_get_economic_calendar`, `ta_get_news`):

- **Latest Protocol Spec (`2025-11-25`)**: Full MCP lifecycle version negotiation supporting `2024-11-05`, `2025-03-26`, `2025-06-18`, and `2025-11-25`.
- **Institutional Tool Metadata**: Includes human-readable `title`, `annotations` (`readOnlyHint: true`, `openWorldHint`), and detailed JSON `outputSchema` for every tool.
- **Token-Optimized Output**: Emits raw UTF-8 without token-wasting `\uXXXX` escaping for Cyrillic and currency symbols (`€`, `£`, `¥`). `ta_scan_watchlist` features `compact: true` by default to comfortably remain below Claude Code's 10,000-token tool output threshold.
- **Verified Macro Calendar**: `ta_get_economic_calendar` retrieves live macroeconomic events mapped to asset currencies with explicit `UTC` timestamps, returning clean `source: "unavailable"` when feeds are offline (zero mock/hallucinated macroeconomic data).
- **Market News Headlines**: `ta_get_news` retrieves recent financial market headlines, publisher attribution, and publication timestamps directly for the selected ticker symbol with resilient fallback handling.

#### Option 1: Claude Code CLI

```bash
# Recommended: install once as a global tool (~0.4s instant startup, avoids uvx cold timeouts)
uv tool install yfinance-ta-patterns
claude mcp add yfinance-ta -- yfinance-ta-mcp

# Or zero-install execution via uvx (pinned version recommended for reproducibility):
claude mcp add yfinance-ta -- uvx --from "yfinance-ta-patterns>=0.3.49" yfinance-ta-mcp
```

#### Option 2: Antigravity / Claude Desktop / Cursor / Windsurf (`mcp_config.json`)

```json
{
  "mcpServers": {
    "yfinance-ta": {
      "command": "yfinance-ta-mcp"
    }
  }
}
```

Or zero-install via `uvx`:

```json
{
  "mcpServers": {
    "yfinance-ta": {
      "command": "uvx",
      "args": ["--from", "yfinance-ta-patterns", "yfinance-ta-mcp"]
    }
  }
}
```

---

## 🚀 Ready-to-Use Examples (`examples/`)

The repository includes production-ready backtesting and real-time scanning scripts in the [`examples/`](examples/) directory:

### 1. Multi-Pair Historical Backtesting (`examples/multi_pair_backtest.py`)

A rigorous multi-pair portfolio backtest across 6 major and cross Forex pairs (`EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `EURGBP`, `EURJPY`) over 2 years on hourly (`1h`) candles.

**Key capabilities demonstrated:**
- **Unbiased Execution**: Fills trades at `Open[i+1]` (next candle open) to eliminate lookahead bias.
- **Fixed Holding Period (`holding_period=5`)**: Automatically closes trades after 5 bars. Essential for single-direction candlestick patterns (Hammer, Inverted Hammer, 3 White Soldiers) which otherwise hold positions indefinitely without reverse exit signals.
- **Universal Forex PnL & Position Sizing**: Automatic base-currency normalization for USD-base pairs (`USDJPY`) and cross-rates (`EURGBP`), applying commissions in account currency ($0.05 ECN micro-lot).
- **Composite Scoring**: Ranks combinations by combining Win Rate, Profit Factor, Sharpe Ratio, and logarithmic trade volume.

```bash
# Run with Python:
python examples/multi_pair_backtest.py

# Or run with uv:
uv run examples/multi_pair_backtest.py
```

**Sample Output:**
```text
==================================================================================================
 TOP-10 BEST COMBINATIONS (PAIR + PATTERN) OVER 2y:
==================================================================================================
  Pair        Pattern  Signals WinRate TotalPnL  ProfitFactor  Sharpe  MaxDD MaxDD_%
EURJPY INVERTEDHAMMER       82   63.0%  $+55.34          2.55    0.38  $7.11   0.07%
EURJPY    MORNINGSTAR       46   63.0%  $+21.49          2.66    0.34  $2.87   0.03%
EURUSD         INNECK       12   75.0%   $+6.31          3.81    0.25  $1.14   0.01%
EURUSD STALLEDPATTERN       26   65.4%  $+23.44          3.09    0.29  $5.17   0.05%
EURUSD  STICKSANDWICH        8   62.5%   $+8.13          5.01    0.22  $1.00   0.01%
EURUSD         ONNECK       11   63.6%  $+11.53          4.44    0.17  $1.97   0.02%
USDJPY    MATCHINGLOW      195   55.6%  $+50.36          1.40    0.24 $15.09   0.15%
EURJPY GRAVESTONEDOJI      226   52.4%  $+58.74          1.44    0.24 $20.81   0.21%
EURJPY        HIKKAKE     1604   52.7%  $+50.97          1.06    0.09 $75.80   0.76%
USDJPY       BELTHOLD     1596   47.7%  $+78.87          1.09    0.13 $83.89   0.84%

Full ranked report saved to: all_pairs_ranked.csv
```

### 2. Live Multi-Asset Signal Scanner (`examples/live_signals_scanner.py`)

Scans a 12-asset watchlist across Forex and Cryptocurrencies (`BTC-USD`, `ETH-USD`) for active candlestick patterns over recent candles, scoring each opportunity with the AI Confluence Engine.

**Key capabilities demonstrated:**
- **AI Confluence Scoring**: Quantifies trend alignment (EMA 20/50/200), volume expansion, and Wilder RSI (14) momentum into a 0–100% confidence score.
- **Automated Trade Setups**: Calculates exact Entry Price, ATR-based Stop Loss, and Take Profit targets (1:1.5 Risk/Reward ratio).
- **Automated Opportunity Ranking**: Sorts all live setups and highlights the **TOP-1 Highest Confidence Trade Setup**.

```bash
# Run with Python:
python examples/live_signals_scanner.py

# Or run with uv:
uv run examples/live_signals_scanner.py
```

**Sample Output:**
```text
=========================================================================================================
 SIGNALS FOUND: 26 (Ranked by AI Confluence / Quality)
=========================================================================================================
       Time  Symbol Direction        Pattern Confluence     Grade        Entry     StopLoss  TakeProfit_1 Risk_Reward          Trend
08.09 19:00 ETH-USD       BUY 3WHITESOLDIERS   80.0/100 EXCELLENT  2499.389893  2483.227621   2523.633300       1:1.5        BULLISH
08.09 17:00  EURUSD       BUY 3WHITESOLDIERS   70.0/100    STRONG     1.163332     1.162101      1.165178       1:1.5 STRONG_BULLISH
08.09 17:00  USDCAD      SELL     HANGINGMAN   70.0/100    STRONG     1.377840     1.378940      1.376190       1:1.5 STRONG_BEARISH

**********************************************************
 TOP-1 SIGNAL: ETH-USD — BUY (3WHITESOLDIERS)
 Candle Time: 08.09 19:00 | Confluence: 80.0/100 [EXCELLENT] (deterministic heuristic, not win probability)
 Market Trend: BULLISH | RSI: 56.3
 Entry Price: 2499.39
 Stop Loss:   2483.23
 Take Profit: 2523.63 (R:R 1:1.5)
**********************************************************
```

---

### 2. Run CLI

```bash
# 1. Multi-Currency Portfolio Scanner (automatically scans all 56 Forex pairs without specifying --symbol):
yftp --timeframe 5m --period 30d --all-patterns --ai-analyst

# 2. Or explicitly scan all 56 pairs with custom confluence threshold:
yftp --all-pairs --timeframe 1h --period 60d --ai --min-confidence 0.60

# 3. Scan a custom comma-separated portfolio of symbols:
yftp --symbol EURUSD,GBPUSD,USDJPY --timeframe 15m --ai

# 4. Single symbol AI-scored analysis with Executive Brief:
yftp --symbol GBPUSD --timeframe 5m --period 30d --all-patterns --ai-analyst

# 5. Detect a single pattern with classic output:
yftp --pattern HAMMER --symbol AAPL --timeframe 1h --period 60d

# 6. Generate an LLM-ready prompt template for Frontier Reasoning Models (Claude 3.7 Sonnet / o3-mini / DeepSeek-R1):
yftp --all-patterns --symbol EURUSD --timeframe 1h --period 60d --prompt
```

---


## CLI Reference

```text
yfinance-ta-patterns [-h] [-v] (--pattern PATTERN | --all-patterns)
                     [--symbol SYMBOL] [--period PERIOD] [--timeframe TIMEFRAME]
                     [--date YYYY-MM-DD] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
                     [--execution {next_open,close}] [--min-signals MIN_SIGNALS]
                     [--commission COMMISSION] [--slippage SLIPPAGE] [--auto-adjust]
                     [--ai] [--min-confidence MIN_CONFIDENCE]
                     [--ai-analyst] [--prompt] [--format {text,json,markdown}]
```

### Options

| Flag | Description |
|------|-------------|
| `-v`, `--version` | Show package version. |
| `--pattern` | Single candlestick pattern (e.g. `HAMMER`, `DOJI`, `CDLKICKING`). |
| `--all-patterns` | Scan and display signals for all available candlestick patterns. |
| `--symbol` | Ticker symbol (e.g. `AAPL`, `BTC-USD`, `GC=F`, `EURUSD`). |
| `--period` | History period (`5d`, `60d`, `1y`, `max`). |
| `--timeframe` | Interval alias (`M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`) or `yfinance` interval (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`). |
| `--date` | Filter signals for a specific date (`YYYY-MM-DD`). |
| `--start-date` / `--end-date` | Date range filter (`YYYY-MM-DD`). |
| `--execution` | Execution timing: `next_open` (unbiased, enters on bar i+1) or `close` (legacy). |
| `--min-signals` | Minimum signal count required for ranking (reduces overfitting from 1-trade samples). |
| `--commission` | Fixed transaction cost per round-trip trade in backtester. |
| `--slippage` | Slippage in price units applied adversely to entries and exits. |
| `--auto-adjust` | Enable dividend and split adjustments (recommended for long multi-year stock backtests). |
| `--ai` | Enrich detected patterns with AI confidence scoring, signal grade, and trade setups. |
| `--min-confidence` | Minimum confidence threshold for AI scoring ($0.0$ to $1.0$, default: $0.0$). |
| `--ai-analyst` | Run executive AI market analysis with synthesis and trade setups. |
| `--prompt` | Generate an LLM prompt ready to pass to ChatGPT, Claude, or local LLMs. |
| `--format` | Output format: `text` (default), `json`, or `markdown`. |

---

## Python API

### 1. Universal Multi-Asset Data Loader (`MarketDataLoader`)

Fetches and normalizes OHLC data across equities, crypto, forex, and commodities:

```python
from yfinance_ta_patterns import MarketDataLoader

# Stocks
loader = MarketDataLoader(symbol="NVDA", period="60d", interval="1h")
df = loader.get_data()

# Crypto
crypto_loader = MarketDataLoader(symbol="BTC-USD", period="30d", interval="15m")
crypto_df = crypto_loader.get_data()

# Forex (ForexDataLoader is fully compatible alias)
from yfinance_ta_patterns import ForexDataLoader

forex_loader = ForexDataLoader(symbol="EURUSD", period="60d", interval="1h")
forex_df = forex_loader.get_data()
```

### 2. AI Pattern Confidence Scorer (`AIPatternScorer`)

Evaluates technical confluence (trend, momentum, volume, volatility) and builds complete risk-managed trade setups:

```python
from yfinance_ta_patterns import MarketDataLoader, AIPatternScorer

# Use period="2y" (or >= 200 daily candles) to ensure complete EMA200 indicator warm-up
data = MarketDataLoader("AAPL", period="2y", interval="1d").get_data()

scorer = AIPatternScorer(data)
scored_signals = scorer.score_all_active(min_confidence=0.60)

for sig in scored_signals:
    print(f"Pattern: {sig.pattern_name}")
    print(f"Confidence: {sig.confidence * 100:.1f}% ({sig.grade.value})")
    print(f"Action: {sig.action}")
    if sig.setup:
        print(f"Entry: {sig.setup.entry_price:.2f}")
        print(f"Stop Loss: {sig.setup.stop_loss:.2f}")
        print(f"Target 1: {sig.setup.take_profit_1:.2f} (R:R {sig.setup.risk_reward_ratio:.1f})")
    print("Confluences:", ", ".join(sig.confluences))
    print("-" * 40)
```

### 3. AI Market Analyst & LLM Prompting (`AIMarketAnalyst`)

Generates structured briefs and prompt templates for external LLM reasoning agents:

```python
from yfinance_ta_patterns import MarketDataLoader, AIMarketAnalyst

data = MarketDataLoader("BTC-USD", period="30d", interval="4h").get_data()

analyst = AIMarketAnalyst(data, symbol="BTC-USD")
results = analyst.analyze(min_confidence=0.65)

# 1. Executive Markdown Brief
brief = analyst.generate_brief(results)
print(brief)

# 2. Prompt for Frontier Reasoning Models (Claude 3.7 Sonnet / o3-mini / DeepSeek-R1 / Gemini 2.5)
llm_prompt = analyst.to_llm_prompt(results)

# 3. JSON Payload for APIs / Microservices
json_data = analyst.to_json(results)
```

### 4. Quantitative Backtesting & Pattern Ranking (`PatternRankingTester`)

Tests all candlestick patterns and ranks them by quantitative performance metrics:

```python
from yfinance_ta_patterns import MarketDataLoader, PatternRankingTester

data = MarketDataLoader("EURUSD", period="60d", interval="1h").get_data()

tester = PatternRankingTester(
    data,
    symbol="EURUSD",
    initial_capital=10000.0,
    position_size=1000.0,
    execution="next_open",  # Unbiased: enters on Open of bar i+1
    allow_short=True,  # Full symmetric short trades on bearish signals
    commission=1.50,  # Transaction fee per trade
    slippage=0.0001,  # Execution slippage in price units
    min_signals=5,  # Exclude patterns with < 5 signals (reduces overfitting)
    sharpe_mode="periodic",  # Accounts for 0% returns during idle hold periods
)
results = tester.test_all_patterns(min_signals=5)

top_patterns = tester.get_top_patterns(5)
for r in top_patterns:
    print(
        f"{r.pattern_name}: Win Rate={r.win_rate:.1f}%, PnL=${r.total_pnl:.2f}, "
        f"Periodic Sharpe={r.periodic_sharpe:.2f}, MaxDD=${r.max_drawdown:.2f}, "
        f"Strength={r.avg_strength:.0f}"
    )

# Export comparison report across news filters
report_df = tester.get_comparison_report()
print(report_df)
```

---

<a id="native-mcp-model-context-protocol-server-for-ai-agents"></a>
## 🤖 Native MCP (Model Context Protocol) Server for AI Agents

`yfinance-ta-patterns` includes a built-in, zero-overhead **Native MCP JSON-RPC 2.0 Server** (`yfinance-ta-mcp`) that exposes institutional technical analysis, candlestick pattern scanning, AI confluence scoring, trade setups, macroeconomic calendars, and market news directly to AI coding assistants and autonomous agent runtimes (**Claude Code**, **Claude Desktop**, **Cursor**, **Windsurf**, **Google Antigravity**, **Gemini CLI**, and any client supporting the [Model Context Protocol](https://modelcontextprotocol.io/)).

Optimized for **Frontier Reasoning Models** (**Claude 3.7 Sonnet** with Extended Thinking, **OpenAI o3-mini / o1**, **DeepSeek-R1**, **Gemini 2.5 Pro / Flash**, and **GPT-4.5**).

### 🚀 Instant Launch

```bash
# Direct execution without prior installation (via uvx):
uvx --from yfinance-ta-patterns yfinance-ta-mcp

# Or if installed in your Python environment:
yfinance-ta-mcp
```

### ⚙️ Client Configurations

#### 1. Claude Desktop (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "yfinance-ta": {
      "command": "uvx",
      "args": ["--from", "yfinance-ta-patterns", "yfinance-ta-mcp"]
    }
  }
}
```

#### 2. Cursor / Windsurf (`mcp.json` or Extension Settings)
```json
{
  "mcpServers": {
    "yfinance-ta": {
      "command": "uvx",
      "args": ["--from", "yfinance-ta-patterns", "yfinance-ta-mcp"]
    }
  }
}
```

### 🛠️ Available MCP Tools

| Tool | Description | Key Arguments |
|:---|:---|:---|
| `ta_scan_symbol` | Scans a symbol for active candlestick patterns, computes deterministic AI Confluence Scores (0.0–1.0), and generates ATR-based Trade Setups (Entry, Stop-Loss, TP1, TP2). | `symbol`, `timeframe`, `period`, `min_confidence`, `compact` |
| `ta_scan_watchlist` | High-throughput concurrent scanner across up to 25 symbols with isolated timeouts and aggregated trade opportunities. | `symbols` (list), `timeframe`, `min_confidence` |
| `ta_backtest_patterns` | Institutional unbiased next-open backtester ranking patterns by win rate, total PnL, periodic Sharpe, and max drawdown. | `symbol`, `period`, `timeframe`, `min_signals`, `execution` |
| `ta_get_economic_calendar` | Live high-impact macroeconomic calendar events relevant to the asset's currency with in-memory caching. | `symbol` (e.g. 'EURUSD', 'AAPL') |
| `ta_get_news` | Live market news headlines, publishers, and publication timestamps for an asset. | `symbol`, `limit` |

### 📚 MCP Resources & Prompts

* **Resources:**
  * `ta://patterns/catalog` — Canonical catalog of all 61 candlestick patterns with recognition rules and runtime mode.
  * `ta://forex/56-pairs` — Canonical universe of 56 major and cross currency pairs.
* **Prompts:**
  * `ta_market_analyst_prompt` — Generates structured quantitative portfolio manager reasoning prompts tailored for Claude 3.7 Sonnet, o3-mini, and DeepSeek-R1.

---

## Development & Testing

Run the test suite and quality checks:

```bash
# Run pytest across full test suite (429 tests)
uv run --extra dev pytest -v
# or with standard pytest
pytest -v

# Linter and formatting check
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .

# Static type check
uv run --extra dev mypy yfinance_ta_patterns

# Build source distribution and binary wheel
uv build
```

All 469 tests pass with 100% success rate across **Python 3.8 through 3.16 (including No-GIL free-threaded 3.13t–3.16t)** and **PyPy 3.8 through 3.12**.

---

## 🌐 High-Performance Systems Ecosystem

`yfinance-ta-patterns` is developed by [**@eminsk**](https://github.com/eminsk) as part of an open-source engineering ecosystem:

* ⚡ [**NanoVector**](https://github.com/eminsk/nanovector) — Bare-metal C99/AVX2 vector search & episodic memory engine (~120KB) with Native MCP Server (`pip install nanovector`).
* 🧠 [**AgentJIT**](https://github.com/eminsk/agentjit) — Just-In-Time Compiler for AI Agent Trajectories with speculative de-optimization guards (`pip install agentjit`).
* ⚡ [**NanoGEMM**](https://github.com/eminsk/nanogemm) — Minimalist, bare-metal AVX2+FMA SIMD matrix multiplication engine in ~100KB for sub-microsecond CPU neural network inference (`pip install nanogemm`).
* 🖥️ [**NanoRecall**](https://github.com/eminsk/nanorecall) — 100% Private, offline desktop memory & semantic screen search engine powered by NanoVector (`pip install nanorecall`).
* 🛒 [**avito-sdk**](https://github.com/eminsk/avito-sdk) — Headless Avito scraping & data extraction SDK with price drop tracking, Playwright cookies, Telegram/VK bots, and Native MCP Server (`pip install avito-sdk`).
* 📊 [**xlsx_vievers**](https://github.com/eminsk/xlsx_vievers) — Headless Excel formula engine (129+ functions), desktop spreadsheet processor, SIMD SSE2 math engine, and Native MCP Server (`pip install xlsx-viewer-pro`).
* 🎥 [**screenvideo**](https://github.com/eminsk/screenvideo) — Lightweight desktop screen recorder with WASAPI audio and a standalone pure x64 Flat Assembler (FASM) native edition.

---

## ☕ Support, Community & Donations

If you find this project valuable, encounter issues, or would like to support ongoing development:

* ⭐ **Star the Repository**: If yfinance-ta-patterns powers your market analysis or trading algorithms, give us a star on GitHub — it helps more traders and quants discover the tool!
* 🐛 **Report Issues & Bugs**: Found a bug, pattern calculation discrepancy, or edge case? Please [open an Issue](https://github.com/eminsk/yfinance-ta-patterns/issues)!
* 💬 **Join Discussions**: Have strategies, ideas, or custom patterns to share? Start or join a thread in [GitHub Discussions](https://github.com/eminsk/yfinance-ta-patterns/discussions)!
* ☕ **Donate (USDT TRC-20)**:  
  `TDVbEdnpgNgoAhNcn1EwXxWHiR3RKLit5P`  
  *(Network: TRON / TRC-20 | [Verify on TronScan](https://tronscan.org/#/address/TDVbEdnpgNgoAhNcn1EwXxWHiR3RKLit5P))*

[![GitHub Repo stars](https://img.shields.io/github/stars/eminsk/yfinance-ta-patterns?style=social)](https://github.com/eminsk/yfinance-ta-patterns)
[![GitHub Issues](https://img.shields.io/github/issues/eminsk/yfinance-ta-patterns?color=red&style=flat-square)](https://github.com/eminsk/yfinance-ta-patterns/issues)
[![GitHub Discussions](https://img.shields.io/badge/Discussions-Join_Community-blue?logo=github&style=flat-square)](https://github.com/eminsk/yfinance-ta-patterns/discussions)
[![Donate USDT](https://img.shields.io/badge/Donate-USDT_(TRC20)-26A17B?style=flat-square&logo=tether&logoColor=white)](https://tronscan.org/#/address/TDVbEdnpgNgoAhNcn1EwXxWHiR3RKLit5P)

---

## License

MIT License. See [LICENSE](LICENSE) for details.
