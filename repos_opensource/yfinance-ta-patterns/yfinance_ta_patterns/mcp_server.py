"""Native Model Context Protocol (MCP) JSON-RPC 2.0 Server for yfinance-ta-patterns.

Exposes institutional candlestick pattern scanning, AI Confluence Scoring (Trend + RSI + Volume + ATR),
actionable Trade Setups (Entry / Stop-Loss / Take-Profit), and multi-asset backtesting to Claude Code,
Claude Desktop, Cursor, Windsurf, Antigravity, and any MCP client over stdio.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import datetime as dt
import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd

from . import __version__
from .ai.analyst import AIMarketAnalyst, _json_safe
from .ai.scorer import PATTERN_CANDLE_COUNTS
from .data import MarketDataLoader, normalize_interval
from .economic_calendar import InvestingCalendar
from .forex_data_loader import FOREX_56_PAIRS
from .pattern_analyzer import PatternAnalyzer
from .pattern_tester import PatternRankingTester
from .talib_compat import HAS_NATIVE_TALIB, get_talib_status

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
LATEST_PROTOCOL_VERSION = "2025-11-25"

MAX_WATCHLIST_SYMBOLS = 25
DEFAULT_MAX_RESULTS = 25
MAX_RESULTS_LIMIT = 50

SUPPORTED_TIMEFRAMES = [
    "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "4h", "1d", "5d", "1wk", "1mo", "3mo"
]

MCP_SERVER_INSTRUCTIONS = (
    "yfinance-ta-patterns MCP server for multi-asset candlestick pattern scanning, "
    "deterministic AI Confluence Scoring (EMA 20/50/200 + RVOL + Wilder RSI-14 + Wilder ATR-14), "
    "ATR-based Trade Setups (Entry, Stop-Loss, TP1, TP2), unbiased next-open backtesting, "
    "macroeconomic calendar events, and real-time market news. "
    "Optimized for tool-calling and reasoning workflows by frontier AI models "
    "(Claude 3.7 Sonnet, OpenAI o3-mini/o1, DeepSeek-R1, Gemini 2.5). "
    "Supported tickers: Stocks ('AAPL', 'NVDA'), Crypto ('BTC-USD'), Forex ('EURUSD=X'), "
    "Indices ('^GSPC'), Commodities ('GC=F'). Yahoo Finance lookback limits apply to intraday "
    "intervals ('1m' <= '7d', '5m'/'15m'/'30m' <= '60d', '1h'/'4h' <= '730d') and are "
    "automatically clamped when omitted or exceeded."
)

MCP_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "ta_scan_symbol",
        "title": "Scan Symbol Patterns & AI Confluence",
        "description": (
            "Scan a market symbol (Stocks, Crypto, Forex, Indices, Commodities, e.g. 'BTC-USD', "
            "'NVDA', 'AAPL', 'EURUSD=X') for candlestick patterns, compute AI Confluence Scores "
            "(Trend + RSI + Volume + ATR), and generate actionable Trade Setups (Entry, Stop-Loss, TP1, TP2)."
        ),
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Ticker symbol (e.g. 'BTC-USD', 'NVDA', 'EURUSD=X')",
                },
                "timeframe": {
                    "type": "string",
                    "default": "1d",
                    "enum": SUPPORTED_TIMEFRAMES,
                    "description": "Timeframe interval ('15m', '1h', '4h', '1d', '1wk')",
                },
                "period": {
                    "type": "string",
                    "default": "6mo",
                    "description": (
                        "Historical lookback period ('7d' for 1m, '60d' for 5m/15m/30m, "
                        "'6mo'/'1y'/'2y' for 1h/4h/1d; auto-clamped to Yahoo Finance limits)"
                    ),
                },
                "min_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.4,
                    "description": "Minimum AI Confluence score in [0.0, 1.0]",
                },
                "lookback_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 3,
                    "description": "Number of recent bars to evaluate for active patterns (1-60)",
                },
                "include_brief": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, include a human-readable markdown_brief alongside structured JSON patterns",
                },
                "compact": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "If true (default), returns streamlined trade setups to minimize token usage "
                        "and stay well below LLM context limits; if false, returns full indicator dumps."
                    ),
                },
                "max_patterns": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 25,
                    "description": "Maximum number of active pattern setups to return (default: 25, max: 50)",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "engine": {"type": "string", "enum": ["TA-Lib", "NumPy-fallback"]},
                "timeframe": {"type": "string"},
                "period": {"type": "string"},
                "timezone": {"type": "string"},
                "compact": {"type": "boolean"},
                "total_patterns_found": {"type": "integer"},
                "truncated_to": {"type": "integer"},
                "patterns": {"type": "array"},
                "setups": {"type": "array"},
                "market_summary": {"type": "object"},
                "markdown_brief": {"type": "string"},
                "event_risk": {"type": "object"},
            },
        },
    },
    {
        "name": "ta_scan_watchlist",
        "title": "Scan Watchlist Patterns & Confluence Ranking",
        "description": (
            "Scan multiple ticker symbols in batch (up to 25 symbols) and rank all detected "
            "candlestick pattern setups across the watchlist by AI Confluence Score descending."
        ),
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_WATCHLIST_SYMBOLS,
                    "description": (
                        f"List of ticker symbols (max {MAX_WATCHLIST_SYMBOLS}, "
                        "e.g. ['AAPL', 'NVDA', 'MSFT', 'BTC-USD'])"
                    ),
                },
                "timeframe": {
                    "type": "string",
                    "default": "1d",
                    "enum": SUPPORTED_TIMEFRAMES,
                    "description": "Timeframe interval (default: '1d')",
                },
                "period": {
                    "type": "string",
                    "default": "6mo",
                    "description": "Historical lookback period (auto-clamped for intraday intervals)",
                },
                "min_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.55,
                    "description": "Minimum AI Confluence score in [0.0, 1.0]",
                },
                "lookback_bars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 2,
                    "description": "Number of recent bars per symbol to evaluate (default: 2)",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_RESULTS_LIMIT,
                    "default": DEFAULT_MAX_RESULTS,
                    "description": f"Maximum number of ranked setups to return (default: {DEFAULT_MAX_RESULTS}, max: {MAX_RESULTS_LIMIT})",
                },
                "compact": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "If true (default), returns streamlined trade setups to minimize token usage "
                        "and stay well below LLM context limits; if false, returns full indicator dumps."
                    ),
                },
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "symbols_requested": {"type": "integer"},
                "symbols_scanned": {"type": "integer"},
                "engine": {"type": "string", "enum": ["TA-Lib", "NumPy-fallback"]},
                "timeframe": {"type": "string"},
                "period": {"type": "string"},
                "compact": {"type": "boolean"},
                "total_opportunities": {"type": "integer"},
                "opportunities": {"type": "array"},
                "truncated_to": {"type": "integer"},
                "errors": {"type": "object"},
            },
        },
    },
    {
        "name": "ta_backtest_patterns",
        "title": "Backtest Candlestick Patterns",
        "description": (
            "Run quantitative historical backtesting of all candlestick patterns on a symbol "
            "(next-open execution) and return the top-performing patterns ranked by composite score or win rate."
        ),
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Ticker symbol (e.g. 'BTC-USD', 'AAPL')",
                },
                "timeframe": {
                    "type": "string",
                    "default": "1d",
                    "enum": SUPPORTED_TIMEFRAMES,
                    "description": "Timeframe interval (default: '1d')",
                },
                "period": {
                    "type": "string",
                    "default": "1y",
                    "description": "Backtest period (default: '1y'; auto-clamped for intraday intervals)",
                },
                "holding_period": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 5,
                    "description": "Bars to hold each trade (default: 5)",
                },
                "min_signals": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 1,
                    "description": "Minimum total signals required to include a pattern (default: 1)",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["composite", "win_rate"],
                    "default": "composite",
                    "description": "Ranking metric: 'composite' (balances win rate, PF, Sharpe, log trades) or 'win_rate'",
                },
                "top_n": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 61,
                    "default": 10,
                    "description": "Number of top-ranked patterns to return (default: 10)",
                },
                "filter_news": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, skips trades on days with high-impact macroeconomic events using the economic calendar",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "timeframe": {"type": "string"},
                "period": {"type": "string"},
                "holding_period": {"type": "integer"},
                "sort_by": {"type": "string"},
                "filter_news": {"type": "boolean"},
                "news_dates_filtered": {"type": "integer"},
                "total_patterns_evaluated": {"type": "integer"},
                "top_patterns": {"type": "array"},
            },
        },
    },
    {
        "name": "ta_list_patterns",
        "title": "List Supported Candlestick Patterns",
        "description": "List all supported candlestick patterns, TA-Lib binary status, and Python No-GIL runtime status.",
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "version": {"type": "string"},
                "total_patterns": {"type": "integer"},
                "patterns": {"type": "array"},
                "fallback_patterns": {"type": "array"},
                "talib_status": {"type": "object"},
                "talib_available": {"type": "boolean"},
                "gil_disabled": {"type": "boolean"},
            },
        },
    },
    {
        "name": "ta_get_economic_calendar",
        "title": "Get Macroeconomic Calendar for Asset",
        "description": (
            "Get macroeconomic calendar events (live verified feed) "
            "automatically filtered by the currencies impacting a selected asset symbol "
            "(e.g. 'EURUSD=X' -> EUR, USD; 'AAPL' -> USD; 'GBPJPY' -> GBP, JPY; 'BTC-USD' -> USD). "
            "Returns an empty list with source='unavailable' if the live feed is unreachable."
        ),
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Selected asset ticker symbol or currency pair "
                        "(e.g. 'EURUSD=X', 'EUR/USD', 'GBPUSD', 'AAPL', 'BTC-USD', 'SAP.DE')"
                    ),
                },
                "date_from": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format (defaults to today UTC)",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format (defaults to date_from + days)",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 60,
                    "default": 7,
                    "description": "Number of days ahead from date_from when date_to is omitted (default: 7, max: 60)",
                },
                "importances": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Filter by event importance level: '1' (low), '2' (medium), '3' (high). "
                        "Also accepts 'low', 'medium', 'high'. Omit for all levels."
                    ),
                },
                "countries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional override list of currency codes or countries (e.g. ['USD', 'EUR']). "
                        "When omitted, automatically resolved from 'symbol'."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 30,
                    "description": "Maximum number of calendar events to return (default: 30, max: 100)",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "normalized_symbol": {"type": "string"},
                "currencies": {"type": "array", "items": {"type": "string"}},
                "filter_countries": {"type": "array", "items": {"type": "string"}},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
                "importances": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string", "enum": ["live", "unavailable"]},
                "total_events": {"type": "integer"},
                "events": {"type": "array"},
                "message": {"type": "string"},
                "truncated_to": {"type": "integer"},
            },
        },
    },
    {
        "name": "ta_get_news",
        "title": "Get Market News Headlines",
        "description": (
            "Fetch recent market news headlines, publishers, and publication timestamps "
            "for a given ticker symbol (e.g. 'AAPL', 'NVDA', 'BTC-USD')."
        ),
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Ticker symbol (e.g. 'AAPL', 'NVDA', 'BTC-USD')",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "Maximum number of news headlines to return (default: 10, max: 50)",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "total_news": {"type": "integer"},
                "news": {"type": "array"},
                "status": {"type": "string", "enum": ["live", "empty", "unavailable"]},
                "source": {"type": "string", "enum": ["live", "unavailable"]},
                "message": {"type": "string"},
            },
        },
    },
]

MCP_KNOWN_TOOLS: set[str] = {t["name"] for t in MCP_TOOLS_SCHEMA}

MCP_RESOURCES_SCHEMA: list[dict[str, Any]] = [
    {
        "uri": "ta://patterns/catalog",
        "name": "TA-Lib 61 Candlestick Patterns Catalog",
        "description": "Complete list of all 61 canonical TA-Lib candlestick patterns with candle lookback lengths and runtime status.",
        "mimeType": "application/json",
    },
    {
        "uri": "ta://forex/56-pairs",
        "name": "56 Major & Cross Forex Currency Pairs",
        "description": "Canonical list of 56 major and cross Forex ticker symbols supported by yfinance-ta-patterns.",
        "mimeType": "application/json",
    },
]

MCP_PROMPTS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "ta_market_analyst_prompt",
        "description": (
            "Generate an institutional Quantitative Portfolio Manager reasoning prompt "
            "(optimized for Claude 3.7 Sonnet, OpenAI o3-mini, DeepSeek-R1, Gemini 2.5) "
            "with live pattern setups for a symbol."
        ),
        "arguments": [
            {
                "name": "symbol",
                "description": "Ticker symbol (e.g. 'BTC-USD', 'AAPL', 'EURUSD=X')",
                "required": True,
            },
            {
                "name": "timeframe",
                "description": "Timeframe interval (default: '1d')",
                "required": False,
            },
        ],
    }
]


def _round_price(val: Any) -> float | None:
    """Round price: 4 decimals if abs(price) < 1.0 (forex/crypto), else 2 decimals."""
    if val is None:
        return None
    try:
        f = float(val)
        if not np.isfinite(f):
            return None
        return round(f, 4 if abs(f) < 1.0 else 2)
    except (ValueError, TypeError):
        return None


def _round_val(val: Any, decimals: int = 2) -> float | None:
    """Round numeric metric or ratio to specified decimals."""
    if val is None:
        return None
    try:
        f = float(val)
        if not np.isfinite(f):
            return None
        return round(f, decimals)
    except (ValueError, TypeError):
        return None


def _parse_int_param(
    val: Any,
    name: str,
    min_val: int | None = None,
    max_val: int | None = None,
    default: int | None = None,
) -> int:
    """Parse, type-check, and clamp integer parameters with clean schema error messages."""
    if val is None:
        if default is not None:
            return default
        raise ValueError(f"Parameter '{name}' is required.")
    if isinstance(val, bool):
        raise ValueError(f"Invalid parameter '{name}': expected integer, got boolean.")
    try:
        iv = int(val)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid parameter '{name}': expected integer, got {val!r}.") from None
    if min_val is not None and iv < min_val:
        iv = min_val
    if max_val is not None and iv > max_val:
        iv = max_val
    return iv


def _parse_float_param(
    val: Any,
    name: str,
    min_val: float | None = None,
    max_val: float | None = None,
    default: float | None = None,
) -> float:
    """Parse, type-check, and clamp float parameters with clean schema error messages."""
    if val is None:
        if default is not None:
            return default
        raise ValueError(f"Parameter '{name}' is required.")
    if isinstance(val, bool):
        raise ValueError(f"Invalid parameter '{name}': expected number, got boolean.")
    try:
        fv = float(val)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid parameter '{name}': expected number, got {val!r}.") from None
    if min_val is not None and fv < min_val:
        fv = min_val
    if max_val is not None and fv > max_val:
        fv = max_val
    return fv


def _resolve_mcp_period(timeframe: str, period: Any, default_period: str = "6mo") -> str:
    """Resolve and clamp lookback period to match Yahoo Finance's intraday interval limits."""
    norm_tf = normalize_interval(timeframe or "1d")
    raw_period = str(period).strip() if period is not None and str(period).strip() else ""
    if not raw_period:
        if norm_tf == "1m":
            raw_period = "7d"
        elif norm_tf in ("2m", "5m", "15m", "30m", "90m"):
            raw_period = "60d"
        else:
            raw_period = default_period
    return MarketDataLoader._clamp_period_for_interval(raw_period, norm_tf)


def _normalize_watchlist_symbols(raw_symbols: Any) -> list[str]:
    """Validate and normalize watchlist symbols input (accepts list or comma-separated string)."""
    if raw_symbols is None:
        raise ValueError("Parameter 'symbols' is required and must be a non-empty list of tickers.")

    tokens: list[str] = []
    if isinstance(raw_symbols, str):
        tokens = [s.strip() for s in re.split(r"[,;\s]+", raw_symbols) if s.strip()]
    elif isinstance(raw_symbols, Sequence) and not isinstance(raw_symbols, (bytes, bytearray)):
        for item in raw_symbols:
            if not isinstance(item, str):
                raise ValueError(
                    f"Invalid symbol entry {item!r}: each symbol must be a string ticker."
                )
            for part in re.split(r"[,;\s]+", item.strip()):
                if part:
                    tokens.append(part)
    else:
        raise ValueError(
            "Parameter 'symbols' must be an array of ticker strings (e.g. ['AAPL', 'NVDA'])."
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for sym in tokens:
        upper = sym.upper()
        if upper not in seen:
            seen.add(upper)
            deduped.append(sym)

    if not deduped:
        raise ValueError("Parameter 'symbols' must contain at least one valid ticker symbol.")

    if len(deduped) > MAX_WATCHLIST_SYMBOLS:
        raise ValueError(
            f"Watchlist size ({len(deduped)}) exceeds maximum allowed limit of "
            f"{MAX_WATCHLIST_SYMBOLS} symbols per request."
        )

    return deduped


def _parse_event_utc(time_str: Any) -> dt.datetime | None:
    """Parse calendar event timestamp to UTC datetime for accurate temporal filtering."""
    if not time_str or not isinstance(time_str, str):
        return None
    cleaned = time_str.replace("UTC", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(cleaned, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    try:
        d = dt.datetime.strptime(cleaned, "%Y-%m-%d").date()
        return dt.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _format_news_item(article: dict[str, Any]) -> dict[str, Any]:
    """Format and normalize a market news article dictionary from yfinance."""
    content = article.get("content", {}) if isinstance(article.get("content"), dict) else {}
    title = (
        content.get("title")
        or article.get("title")
        or article.get("headline")
        or ""
    )
    link = (
        (content.get("canonicalUrl", {}) or {}).get("url")
        or (content.get("clickThroughUrl", {}) or {}).get("url")
        or article.get("link")
        or article.get("url")
        or ""
    )
    publisher = (
        (content.get("provider", {}) or {}).get("displayName")
        or article.get("publisher")
        or article.get("source")
        or ""
    )
    pub_time = (
        content.get("pubDate")
        or article.get("providerPublishTime")
        or article.get("publish_time")
        or article.get("publishedAt")
        or ""
    )
    summary = (
        content.get("summary")
        or article.get("summary")
        or article.get("description")
        or ""
    )
    return {
        "title": str(title).strip(),
        "publisher": str(publisher).strip(),
        "link": str(link).strip(),
        "publish_time": str(pub_time).strip() if pub_time is not None else "",
        "summary": str(summary).strip()[:300] if summary else "",
    }


class _ToolTimeout(Exception):
    """Raised when a tool call exceeds its wall-clock budget."""


def _run_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    """Run ``fn`` in a daemon thread; return its result or raise _ToolTimeout.

    Unlike ``with ThreadPoolExecutor(): ... return``, this never joins the worker on
    timeout, so a hung network call cannot block the stdio worker or process exit.
    """
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["ok"] = fn()
        except BaseException as exc:  # re-raised in the caller thread
            box["err"] = exc

    t = threading.Thread(target=_target, name="yfinance-mcp-tool", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise _ToolTimeout()
    if "err" in box:
        raise box["err"]
    return box.get("ok")


class YFinanceTAMCPServer:
    """Zero-dependency Model Context Protocol (MCP) JSON-RPC 2.0 server for yfinance-ta-patterns."""

    def __init__(
        self,
        data_fetcher: Callable[[str, str, str], pd.DataFrame] | None = None,
        calendar_fetcher: Callable[[str], str | None] | None = None,
        news_fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
        tool_timeout: float | None = 30.0,
    ) -> None:
        self._data_fetcher = data_fetcher
        self._calendar_fetcher = calendar_fetcher
        self._news_fetcher = news_fetcher
        self._tool_timeout = tool_timeout

    def _fetch_ohlcv(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        if self._data_fetcher is not None:
            return self._data_fetcher(symbol, period, interval)
        loader = MarketDataLoader(symbol, period=period, interval=interval, timezone="UTC")
        return loader.get_data()

    def handle_request(self, request: Any) -> dict[str, Any] | None:
        """Process a single JSON-RPC 2.0 MCP message."""
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
            }

        req_id = request.get("id")
        has_id = "id" in request and req_id is not None
        method = request.get("method")

        if not isinstance(method, str) or not method.strip():
            return {
                "jsonrpc": "2.0",
                "id": req_id if has_id else None,
                "error": {
                    "code": -32600,
                    "message": "Invalid Request: 'method' must be a non-empty string",
                },
            }

        # JSON-RPC 2.0 specification: any request without an id is a notification and must not be replied to
        if not has_id:
            return None

        raw_params = request.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid params: expected JSON object"},
            }
        params: dict[str, Any] = raw_params or {}

        try:
            if method == "initialize":
                requested_version = params.get("protocolVersion")
                negotiated_version = (
                    requested_version
                    if isinstance(requested_version, str)
                    and requested_version in SUPPORTED_PROTOCOL_VERSIONS
                    else LATEST_PROTOCOL_VERSION
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": negotiated_version,
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                            "prompts": {},
                        },
                        "serverInfo": {
                            "name": "yfinance-ta-patterns-mcp",
                            "version": __version__,
                        },
                        "instructions": MCP_SERVER_INSTRUCTIONS,
                    },
                }

            if method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}

            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS_SCHEMA}}

            if method == "resources/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"resources": MCP_RESOURCES_SCHEMA},
                }

            if method == "resources/templates/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"resourceTemplates": []},
                }

            if method == "resources/read":
                uri = str(params.get("uri", ""))
                try:
                    res_payload = _json_safe(self._read_resource(uri))
                except ValueError as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32002, "message": str(exc)},
                    }
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(
                                    res_payload,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    default=str,
                                ),
                            }
                        ]
                    },
                }

            if method == "prompts/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"prompts": MCP_PROMPTS_SCHEMA},
                }

            if method == "prompts/get":
                prompt_name = str(params.get("name", ""))
                prompt_args = params.get("arguments") or {}
                if prompt_name != "ta_market_analyst_prompt":
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": f"Unknown prompt: {prompt_name}"},
                    }
                symbol = str(prompt_args.get("symbol", "")).strip()
                if not symbol:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": "Missing required argument: 'symbol'"},
                    }
                timeframe = normalize_interval(str(prompt_args.get("timeframe", "1d")))
                period = _resolve_mcp_period(timeframe, prompt_args.get("period"), "6mo")
                with contextlib.redirect_stdout(sys.stderr):
                    df = self._fetch_ohlcv(symbol, period, timeframe)
                    analyst = AIMarketAnalyst(df, symbol=symbol, timeframe=timeframe)
                    analyst.analyze(min_confidence=0.4, lookback_bars=3)
                    prompt_text = analyst.to_llm_prompt()
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "description": f"Quantitative technical analysis prompt for {symbol} ({timeframe})",
                        "messages": [
                            {
                                "role": "user",
                                "content": {"type": "text", "text": prompt_text},
                            }
                        ],
                    },
                }

            if method == "tools/call":
                tool_name = params.get("name", "")
                if not tool_name or str(tool_name) not in MCP_KNOWN_TOOLS:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": f"Unknown tool: '{tool_name}'",
                        },
                    }
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "Invalid tool arguments: expected JSON object",
                        },
                    }
                timeout = self._tool_timeout
                with contextlib.redirect_stdout(sys.stderr):
                    if timeout is not None and timeout > 0:
                        try:
                            output = _json_safe(
                                _run_with_timeout(lambda: self._call_tool(str(tool_name), args), timeout)
                            )
                        except _ToolTimeout:
                            return {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": f"Error: Tool '{tool_name}' execution timed out after {timeout}s",
                                        }
                                    ],
                                    "isError": True,
                                },
                            }
                    else:
                        output = _json_safe(self._call_tool(str(tool_name), args))
                call_result: dict[str, Any] = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                output,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                default=str,
                            ),
                        }
                    ],
                    "isError": False,
                }
                if isinstance(output, dict):
                    call_result["structuredContent"] = output
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": call_result,
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        except Exception as exc:
            if method == "tools/call":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }

    def _read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "ta://patterns/catalog":
            patterns = PatternAnalyzer.get_supported_fallback_patterns()
            return {
                "version": __version__,
                "total_patterns": len(patterns),
                "patterns": [
                    {
                        "name": pat.replace("CDL", ""),
                        "talib_function": pat,
                        "candle_lookback": PATTERN_CANDLE_COUNTS.get(pat.replace("CDL", ""), 1),
                    }
                    for pat in patterns
                ],
                "talib_status": get_talib_status(),
            }
        if uri == "ta://forex/56-pairs":
            return {
                "count": len(FOREX_56_PAIRS),
                "pairs": list(FOREX_56_PAIRS),
            }
        raise ValueError(f"Unknown resource URI: {uri}")

    def _call_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == "ta_scan_symbol":
            symbol = str(args.get("symbol", "")).strip()
            if not symbol:
                raise ValueError("Parameter 'symbol' is required and cannot be empty.")
            timeframe = normalize_interval(str(args.get("timeframe", "1d")))
            period = _resolve_mcp_period(timeframe, args.get("period"), default_period="6mo")
            min_conf = _parse_float_param(args.get("min_confidence"), "min_confidence", 0.0, 1.0, default=0.4)
            lookback = _parse_int_param(args.get("lookback_bars"), "lookback_bars", 1, 60, default=3)
            include_brief = bool(args.get("include_brief", False))
            compact = True if args.get("compact") is None else bool(args.get("compact"))
            max_patterns = _parse_int_param(args.get("max_patterns"), "max_patterns", 1, 50, default=25)

            df = self._fetch_ohlcv(symbol, period, timeframe)
            analyst = AIMarketAnalyst(df, symbol=symbol, timeframe=timeframe)
            analyst.analyze(min_confidence=min_conf, lookback_bars=lookback)
            report = analyst.to_dict()
            report["engine"] = "TA-Lib" if HAS_NATIVE_TALIB else "NumPy-fallback"
            report["period"] = period
            tz_obj = getattr(df.index, "tz", None)
            report["timezone"] = str(tz_obj) if tz_obj is not None else "UTC"

            raw_patterns = report.get("patterns", [])
            total_patterns_found = len(raw_patterns)
            market_summary = report.get("market_summary", {})
            curr_price = (
                market_summary.get("current_price")
                if isinstance(market_summary, dict)
                else None
            )
            if curr_price is None and not df.empty and "Close" in df.columns:
                curr_price = float(df["Close"].iloc[-1])

            if isinstance(market_summary, dict):
                for k in ("current_price", "last_high", "last_low"):
                    if k in market_summary:
                        market_summary[k] = _round_price(market_summary[k])
                if "return_20_bars_pct" in market_summary:
                    market_summary["return_20_bars_pct"] = _round_val(market_summary["return_20_bars_pct"], 2)

            # Extract actionable trade setups with clean canonical keys and rounded figures
            extracted_setups: list[dict[str, Any]] = []
            for p in raw_patterns:
                ts = p.get("trade_setup")
                if not ts:
                    continue
                dir_val = ts.get("direction") or (
                    "BULLISH" if p.get("raw_signal", 0) > 0 else "BEARISH" if p.get("raw_signal", 0) < 0 else "NEUTRAL"
                )
                entry_val = ts.get("entry_price") or ts.get("entry") or curr_price
                tp1_val = ts.get("take_profit_1") or ts.get("tp1")
                tp2_val = ts.get("take_profit_2") or ts.get("tp2")
                rr_val = ts.get("risk_reward_ratio") or ts.get("risk_reward")
                score_val = p.get("confluence_score") if p.get("confluence_score") is not None else p.get("confidence_score")

                setup_item = {
                    "pattern": p.get("pattern"),
                    "direction": dir_val,
                    "confluence_score": _round_val(score_val, 2),
                    "timestamp": p.get("timestamp"),
                    "entry": _round_price(entry_val),
                    "stop_loss": _round_price(ts.get("stop_loss")),
                    "tp1": _round_price(tp1_val),
                    "tp2": _round_price(tp2_val),
                    "risk_reward": _round_val(rr_val, 2),
                    "trend": p.get("trend_regime") or p.get("trend"),
                }
                extracted_setups.append(setup_item)

            if compact:
                streamlined_patterns = []
                for p in raw_patterns[:max_patterns]:
                    ts = p.get("trade_setup") or {}
                    metrics = p.get("metrics") or {}
                    dir_val = (
                        ts.get("direction")
                        or ("BULLISH" if p.get("raw_signal", 0) > 0 else "BEARISH" if p.get("raw_signal", 0) < 0 else "NEUTRAL")
                    )
                    entry_val = ts.get("entry_price") or ts.get("entry") or curr_price
                    score_val = p.get("confluence_score") if p.get("confluence_score") is not None else p.get("confidence_score")
                    trend_val = p.get("trend_regime") or p.get("trend")
                    rvol_val = metrics.get("rvol") if metrics.get("rvol") is not None else p.get("rvol")
                    rsi_val = metrics.get("rsi") if metrics.get("rsi") is not None else p.get("rsi")

                    streamlined_patterns.append({
                        "pattern": p.get("pattern"),
                        "direction": dir_val,
                        "confluence_score": _round_val(score_val, 2),
                        "timestamp": p.get("timestamp"),
                        "price": _round_price(entry_val),
                        "trend": trend_val,
                        "rvol": _round_val(rvol_val, 2),
                        "rsi": _round_val(rsi_val, 1),
                    })
                report["patterns"] = streamlined_patterns
                report["setups"] = extracted_setups[:max_patterns]
                report["compact"] = True
            else:
                report["patterns"] = raw_patterns[:max_patterns]
                report["setups"] = extracted_setups[:max_patterns]
                report["compact"] = False

            report["total_patterns_found"] = total_patterns_found
            report["truncated_to"] = len(report["patterns"])

            # Check imminent macroeconomic event risk (importance 3 within 48h)
            now_utc = dt.datetime.now(dt.timezone.utc)
            event_risk: dict[str, Any] = {
                "source": "unavailable",
                "status": "unavailable",
                "has_high_impact_event": None,
                "event_count": 0,
            }
            try:
                with InvestingCalendar(http_fetcher=self._calendar_fetcher, timeout=2.0) as cal:
                    today_utc = now_utc.date()
                    cal_res = cal.get_events_for_symbol(
                        symbol=symbol,
                        date_from=today_utc.isoformat(),
                        days=2,
                        importances=["3"],
                        limit=20,
                    )
                    feed_source = cal_res.get("source", "unavailable")
                    if feed_source == "unavailable":
                        event_risk = {
                            "source": "unavailable",
                            "status": "unavailable",
                            "has_high_impact_event": None,
                            "event_count": 0,
                            "message": cal_res.get("message", "Economic calendar feed is unavailable"),
                        }
                    else:
                        raw_events = cal_res.get("events", [])
                        upcoming: list[tuple[dt.datetime, dict[str, Any]]] = []
                        for ev in raw_events:
                            ev_dt = _parse_event_utc(ev.get("time"))
                            if ev_dt is not None and ev_dt >= now_utc - dt.timedelta(minutes=15):
                                upcoming.append((ev_dt, ev))
                        upcoming.sort(key=lambda x: x[0])
                        if upcoming:
                            nearest_dt, nearest_ev = upcoming[0]
                            hours_until = max(0.0, round((nearest_dt - now_utc).total_seconds() / 3600.0, 1))
                            event_risk = {
                                "source": "live",
                                "status": "ok",
                                "has_high_impact_event": True,
                                "event_count": len(upcoming),
                                "nearest_event": nearest_ev.get("event"),
                                "event_time": nearest_ev.get("time"),
                                "currency": nearest_ev.get("currency"),
                                "hours_until": hours_until,
                                "warning": (
                                    f"High-impact macroeconomic event imminent: "
                                    f"'{nearest_ev.get('event')}' ({nearest_ev.get('currency')}) "
                                    f"in {hours_until}h at {nearest_ev.get('time')}."
                                ),
                            }
                        else:
                            event_risk = {
                                "source": "live",
                                "status": "ok",
                                "has_high_impact_event": False,
                                "event_count": 0,
                            }
            except Exception as exc:
                event_risk = {
                    "source": "unavailable",
                    "status": "unavailable",
                    "has_high_impact_event": None,
                    "event_count": 0,
                    "message": str(exc),
                }
            report["event_risk"] = event_risk

            if include_brief:
                report["markdown_brief"] = analyst.generate_brief()
            return report

        if name == "ta_scan_watchlist":
            symbols = _normalize_watchlist_symbols(args.get("symbols"))
            timeframe = normalize_interval(str(args.get("timeframe", "1d")))
            period = _resolve_mcp_period(timeframe, args.get("period"), default_period="6mo")
            min_conf = _parse_float_param(args.get("min_confidence"), "min_confidence", 0.0, 1.0, default=0.55)
            lookback = _parse_int_param(args.get("lookback_bars"), "lookback_bars", 1, 60, default=2)
            max_results = _parse_int_param(args.get("max_results"), "max_results", 1, 50, default=DEFAULT_MAX_RESULTS)
            compact = True if args.get("compact") is None else bool(args.get("compact"))

            opportunities: list[dict[str, Any]] = []
            errors: dict[str, str] = {}
            symbols_succeeded = 0

            def _scan_one(sym: str) -> tuple[str, list[dict[str, Any]] | None, str | None]:
                try:
                    df = self._fetch_ohlcv(sym, period, timeframe)
                    if df.empty or len(df) < 20:
                        return (
                            sym,
                            None,
                            f"Insufficient market data ({len(df)} bars returned, minimum 20 required).",
                        )
                    analyst = AIMarketAnalyst(df, symbol=sym, timeframe=timeframe)
                    scored = analyst.analyze(min_confidence=min_conf, lookback_bars=lookback)
                    sym_opps: list[dict[str, Any]] = []
                    for r in scored:
                        d = r.to_dict()
                        d["symbol"] = sym
                        if compact:
                            raw_setup = d.get("trade_setup") or d.get("setup")
                            setup_info = raw_setup if isinstance(raw_setup, dict) else {}
                            conf_score = (
                                d.get("confluence_score")
                                if d.get("confluence_score") is not None
                                else d.get("confidence_score")
                            )
                            entry_val = (
                                setup_info.get("entry_price")
                                or setup_info.get("entry")
                                or d.get("price")
                            )
                            tp1_val = setup_info.get("take_profit_1") or setup_info.get("tp1")
                            tp2_val = setup_info.get("take_profit_2") or setup_info.get("tp2")
                            rr_val = setup_info.get("risk_reward_ratio") or setup_info.get("risk_reward")
                            dir_val = (
                                d.get("direction")
                                or setup_info.get("direction")
                                or ("BULLISH" if d.get("raw_signal", 0) > 0 else "BEARISH" if d.get("raw_signal", 0) < 0 else None)
                            )
                            compact_entry: dict[str, Any] = {
                                "symbol": sym,
                                "pattern": d.get("pattern"),
                                "direction": dir_val,
                                "confluence_score": _round_val(conf_score, 2),
                                "timestamp": d.get("timestamp"),
                                "entry": _round_price(entry_val),
                                "stop_loss": _round_price(setup_info.get("stop_loss")),
                                "tp1": _round_price(tp1_val),
                                "tp2": _round_price(tp2_val),
                                "risk_reward": _round_val(rr_val, 2),
                                "trend": d.get("trend_regime"),
                            }
                            sym_opps.append(compact_entry)
                        else:
                            sym_opps.append(d)
                    return sym, sym_opps, None
                except Exception as exc:
                    return sym, None, str(exc)

            max_workers = min(len(symbols), 8)
            timeout_limit = 20.0
            deadline = time.monotonic() + timeout_limit
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
            future_to_sym = {executor.submit(_scan_one, sym): sym for sym in symbols}
            try:
                for future in concurrent.futures.as_completed(future_to_sym, timeout=timeout_limit):
                    sym = future_to_sym[future]
                    try:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            errors[sym] = f"Scan timed out after {timeout_limit:.1f}s"
                            continue
                        res_sym, sym_opps, err = future.result(timeout=max(0.01, remaining))
                        if err is not None:
                            errors[res_sym] = err
                        else:
                            symbols_succeeded += 1
                            if sym_opps:
                                opportunities.extend(sym_opps)
                    except (TimeoutError, concurrent.futures.TimeoutError):
                        errors[sym] = f"Scan timed out after {timeout_limit:.1f}s"
                    except Exception as exc:
                        errors[sym] = str(exc)
            except (TimeoutError, concurrent.futures.TimeoutError):
                logger.warning("ta_scan_watchlist timed out waiting for symbols")
            finally:
                for f, s in future_to_sym.items():
                    if s not in errors and not f.done():
                        errors[s] = f"Scan timed out after {timeout_limit:.1f}s"
                    f.cancel()
                if sys.version_info >= (3, 9):
                    executor.shutdown(wait=False, cancel_futures=True)
                else:
                    executor.shutdown(wait=False)

            if symbols_succeeded == 0 and errors:
                first_sym, first_err = next(iter(errors.items()))
                raise RuntimeError(
                    f"Failed to scan all {len(symbols)} requested symbol(s). "
                    f"First error ({first_sym}): {first_err}"
                )

            opportunities.sort(
                key=lambda x: (
                    float(
                        x.get("confluence_score")
                        or x.get("confidence_score")
                        or x.get("confidence")
                        or 0.0
                    ),
                    str(x.get("timestamp", "")),
                ),
                reverse=True,
            )

            total_found = len(opportunities)
            truncated = opportunities[:max_results]
            result: dict[str, Any] = {
                "symbols_requested": len(symbols),
                "symbols_scanned": symbols_succeeded,
                "engine": "TA-Lib" if HAS_NATIVE_TALIB else "NumPy-fallback",
                "timeframe": timeframe,
                "period": period,
                "compact": compact,
                "total_opportunities": total_found,
                "opportunities": truncated,
            }
            if total_found > max_results:
                result["truncated_to"] = max_results
            if errors:
                result["errors"] = errors
            return result

        if name == "ta_backtest_patterns":
            symbol = str(args.get("symbol", "")).strip()
            if not symbol:
                raise ValueError("Parameter 'symbol' is required and cannot be empty.")
            timeframe = normalize_interval(str(args.get("timeframe", "1d")))
            period = _resolve_mcp_period(timeframe, args.get("period"), default_period="1y")
            holding_period = _parse_int_param(args.get("holding_period"), "holding_period", 1, 60, default=5)
            min_signals = _parse_int_param(args.get("min_signals"), "min_signals", 1, 100, default=1)
            sort_by = str(args.get("sort_by") or "composite").strip().lower()
            if sort_by not in ("composite", "win_rate"):
                raise ValueError("Parameter 'sort_by' must be 'composite' or 'win_rate'.")
            top_n = _parse_int_param(args.get("top_n"), "top_n", 1, 61, default=10)
            filter_news = bool(args.get("filter_news", False))

            df = self._fetch_ohlcv(symbol, period, timeframe)
            news_dates: list[str] | None = None
            if filter_news:
                try:
                    with InvestingCalendar(http_fetcher=self._calendar_fetcher, timeout=2.0) as cal:
                        start_date_str = str(df.index[0])[:10] if not df.empty else None
                        end_date_str = str(df.index[-1])[:10] if not df.empty else None
                        if start_date_str and end_date_str:
                            cal_res = cal.get_events_for_symbol(
                                symbol=symbol,
                                date_from=start_date_str,
                                date_to=end_date_str,
                                importances=["3"],
                                limit=100,
                            )
                            evs = cal_res.get("events", [])
                            news_dates = [str(e.get("time", ""))[:10] for e in evs if e.get("time")]
                except Exception as exc:
                    logger.debug("Failed fetching macroeconomic news dates for backtest: %s", exc)
                    news_dates = None

            tester = PatternRankingTester(
                df,
                timeframe=timeframe,
                holding_period=holding_period,
                min_signals=min_signals,
                news_dates=news_dates,
                symbol=symbol,
                verbose=False,
            )
            ranked = tester.test_all_patterns(filter_news=filter_news, min_signals=min_signals, sort_by=sort_by)
            top = tester.get_top_patterns(n=top_n)
            top_patterns_list = [
                {
                    "pattern_name": r.pattern_name,
                    "total_signals": r.total_signals,
                    "total_trades": r.total_trades,
                    "win_rate": _round_val(r.win_rate, 2),
                    "total_pnl": _round_val(r.total_pnl, 2),
                    "profit_factor": _round_val(r.profit_factor, 2)
                    if pd.notna(r.profit_factor) and r.profit_factor != float("inf")
                    else None,
                    "sharpe_ratio": _round_val(r.sharpe_ratio, 2),
                    "max_drawdown": _round_val(r.max_drawdown, 2),
                    "score": _round_val(r.score, 2),
                }
                for r in top
            ]
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "period": period,
                "holding_period": holding_period,
                "min_signals": min_signals,
                "sort_by": sort_by,
                "filter_news": filter_news,
                "news_dates_filtered": len(news_dates) if news_dates else 0,
                "total_patterns_evaluated": len(ranked),
                "top_patterns": top_patterns_list,
            }

        if name == "ta_list_patterns":
            status = get_talib_status()
            fallback_patterns = PatternAnalyzer.get_supported_fallback_patterns()
            patterns_catalog = [
                {
                    "name": pat.replace("CDL", ""),
                    "talib_function": pat,
                    "candle_lookback": PATTERN_CANDLE_COUNTS.get(pat.replace("CDL", ""), 1),
                }
                for pat in fallback_patterns
            ]
            return {
                "version": __version__,
                "total_patterns": len(fallback_patterns),
                "patterns": patterns_catalog,
                "fallback_patterns": fallback_patterns,
                "talib_status": status,
                "talib_available": bool(status.get("talib_available", False)),
                "gil_disabled": bool(status.get("gil_disabled", False)),
            }

        if name == "ta_get_economic_calendar":
            symbol = str(args.get("symbol", "")).strip()
            if not symbol:
                raise ValueError("Parameter 'symbol' is required and cannot be empty.")
            date_from = args.get("date_from")
            date_to = args.get("date_to")
            days = _parse_int_param(args.get("days"), "days", 1, 60, default=7)
            importances = args.get("importances")
            countries = args.get("countries")
            max_results = _parse_int_param(args.get("max_results"), "max_results", 1, 100, default=30)

            with InvestingCalendar(http_fetcher=self._calendar_fetcher) as calendar:
                return calendar.get_events_for_symbol(
                    symbol=symbol,
                    date_from=date_from,
                    date_to=date_to,
                    days=days,
                    importances=importances,
                    countries=countries,
                    limit=max_results,
                )

        if name == "ta_get_news":
            symbol = str(args.get("symbol", "")).strip()
            if not symbol:
                raise ValueError("Parameter 'symbol' is required and cannot be empty.")
            limit = _parse_int_param(args.get("limit"), "limit", 1, 50, default=10)
            if self._news_fetcher is not None:
                try:
                    raw_news = self._news_fetcher(symbol, limit)
                except Exception as exc:
                    return {
                        "symbol": symbol,
                        "total_news": 0,
                        "news": [],
                        "status": "unavailable",
                        "source": "unavailable",
                        "message": f"News fetcher failed for {symbol}: {exc}",
                    }
            else:
                try:
                    import yfinance as yf
                    ticker = yf.Ticker(symbol)
                    raw_news = ticker.get_news(count=limit) if hasattr(ticker, "get_news") else getattr(ticker, "news", [])
                except Exception as exc:
                    return {
                        "symbol": symbol,
                        "total_news": 0,
                        "news": [],
                        "status": "unavailable",
                        "source": "unavailable",
                        "message": f"News service unreachable or error fetching news for {symbol}: {exc}",
                    }
            articles = [_format_news_item(a) for a in (raw_news or []) if isinstance(a, dict)]
            articles = [a for a in articles if a.get("title")]
            news_result: dict[str, Any] = {
                "symbol": symbol,
                "total_news": len(articles),
                "news": articles[:limit],
                "status": "live" if articles else "empty",
                "source": "live",
            }
            if not articles:
                news_result["message"] = (
                    "Yahoo Finance returned no articles for this symbol. Either none exist or the "
                    "request failed (yfinance hides HTTP/JSON errors); retry later or try another ticker."
                )
            return news_result

        raise ValueError(f"Unknown MCP tool: {name}")

    def run_stdio(
        self,
        stdin: Any | None = None,
        stdout: Any | None = None,
        max_workers: int = 1,
    ) -> None:
        """Run the yfinance-ta-patterns MCP JSON-RPC 2.0 server over standard input/output with non-blocking concurrency."""
        if stdin is None:
            if hasattr(sys.stdin, "reconfigure"):
                with contextlib.suppress(Exception):
                    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            in_stream = sys.stdin
        else:
            in_stream = stdin
        if stdout is None:
            if hasattr(sys.stdout, "reconfigure"):
                with contextlib.suppress(Exception):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            proto_out = sys.stdout
        else:
            proto_out = stdout
        saved_stdout = sys.stdout
        # Redirect global sys.stdout to sys.stderr so third-party libraries (yfinance, etc.)
        # can never write non-JSON text onto the MCP protocol wire.
        sys.stdout = sys.stderr

        write_lock = threading.Lock()

        def _send(obj: dict[str, Any]) -> None:
            raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
            with write_lock:
                proto_out.write(raw)
                proto_out.flush()

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="yfinance-mcp-worker",
        )

        def _worker_task(req: dict[str, Any]) -> None:
            try:
                resp = self.handle_request(req)
                if resp is not None:
                    _send(resp)
            except Exception as exc:
                req_id = req.get("id") if isinstance(req, dict) else None
                _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": f"Internal error: {exc}"},
                })

        try:
            for raw_line in in_stream:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as exc:
                    _send({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc.msg}"},
                    })
                    continue

                if not isinstance(req, dict):
                    _send({
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
                    })
                    continue

                # JSON-RPC notifications (without id) must never be replied to
                if "id" not in req or req.get("id") is None:
                    continue

                method = req.get("method")
                # Immediately answer ping in reader thread without queuing behind worker pool
                if method == "ping":
                    _send({"jsonrpc": "2.0", "id": req.get("id"), "result": {}})
                    continue

                # Offload all tool/resource calls to worker pool to prevent stdio blocking
                executor.submit(_worker_task, req)
        finally:
            executor.shutdown(wait=True)
            sys.stdout = saved_stdout


def main_mcp(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for yfinance-ta-mcp."""
    parser = argparse.ArgumentParser(
        prog="yfinance-ta-mcp",
        description="Start yfinance-ta-patterns Model Context Protocol (MCP) Server over stdio",
    )
    parser.parse_args(argv)
    server = YFinanceTAMCPServer()
    server.run_stdio(max_workers=4)
    return 0


if __name__ == "__main__":
    sys.exit(main_mcp())
