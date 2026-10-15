"""Command-line interface for scanning TA-Lib candlestick patterns with AI intelligence."""

from __future__ import annotations

import argparse
import contextlib
import sys
import unicodedata
from collections.abc import Sequence
from typing import Any

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None  # type: ignore[assignment]
    HAS_PANDAS = False

from . import __version__

try:
    from .ai.analyst import AIMarketAnalyst
    from .ai.scorer import AIPatternScorer, PatternConfidenceResult
except ImportError:
    AIMarketAnalyst = None  # type: ignore[assignment, misc]
    AIPatternScorer = None  # type: ignore[assignment, misc]
    PatternConfidenceResult = None  # type: ignore[assignment, misc]

try:
    from .data import MarketDataLoader, format_price, format_timestamp, normalize_interval
except ImportError:
    MarketDataLoader = None  # type: ignore[assignment, misc]
    format_price = None  # type: ignore[assignment]
    format_timestamp = None  # type: ignore[assignment]
    normalize_interval = None  # type: ignore[assignment]

try:
    from .forex_data_loader import FOREX_56_PAIRS
except ImportError:
    FOREX_56_PAIRS = ()  # type: ignore[assignment]

try:
    from .pattern_analyzer import PatternAnalyzer
except ImportError:
    PatternAnalyzer = None  # type: ignore[assignment, misc]

from .talib_compat import HAS_NATIVE_TALIB, get_talib_install_hint, get_talib_status

TIMEFRAME_MAP: dict[str, str] = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}
VALID_INTERVALS: set[str] = {
    "1m",
    "2m",
    "5m",
    "15m",
    "30m",
    "60m",
    "90m",
    "1h",
    "4h",
    "1d",
    "5d",
    "1wk",
    "1mo",
    "3mo",
}


def normalize_timeframe(timeframe: str) -> str:
    """Convert human-friendly timeframe names into yfinance intervals."""
    interval = normalize_interval(timeframe)
    if interval not in VALID_INTERVALS:
        allowed = ", ".join(sorted(VALID_INTERVALS))
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Allowed: {allowed}")
    return interval


def _display_width(text: str) -> int:
    """Calculate terminal display width of a string taking wide characters into account."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _pad_cell(text: str, target_width: int) -> str:
    """Pad a string with trailing spaces to match target display width."""
    current_width = _display_width(text)
    padding = max(0, target_width - current_width)
    return text + (" " * padding)


def _markdown_cell(value: Any) -> str:
    """Render a single markdown table cell, escaping pipes that would split the row."""
    text = "" if value is None else str(value).strip().replace("\r", "").replace("\n", " ")
    return text.replace("|", "\\|")


def format_markdown_table(df: pd.DataFrame) -> str:
    """Render a DataFrame as an aligned GitHub-flavored markdown table.

    Rendered locally because pandas' `to_markdown` requires the optional `tabulate`
    dependency, which this package does not ship.
    """
    if df.empty and len(df.columns) == 0:
        return ""

    headers = [str(col) for col in df.columns]
    rows = [
        [_markdown_cell(value) for value in row] for row in df.itertuples(index=False, name=None)
    ]

    widths = [
        max(_display_width(h), max((_display_width(r[i]) for r in rows), default=0), 3)
        for i, h in enumerate(headers)
    ]

    header_line = "| " + " | ".join(_pad_cell(h, w) for h, w in zip(headers, widths)) + " |"
    separator_line = "| " + " | ".join("-" * w for w in widths) + " |"
    data_lines = [
        "| " + " | ".join(_pad_cell(cell, w) for cell, w in zip(row, widths)) + " |" for row in rows
    ]

    return "\n".join([header_line, separator_line, *data_lines])


def resolve_symbols(symbol_arg: str | None, all_pairs_flag: bool = False) -> list[str]:
    """Resolve target symbols from CLI arguments.

    If symbol_arg is None, empty, 'ALL', 'FOREX', 'PAIRS', or all_pairs_flag is True,
    returns all 56 major Forex currency pairs.
    If symbol_arg contains comma-separated values (e.g. 'EURUSD,GBPUSD'), returns the list.
    Otherwise returns [symbol_arg].
    """
    if all_pairs_flag:
        return list(FOREX_56_PAIRS)
    if not symbol_arg or symbol_arg.strip().upper() in (
        "ALL",
        "FOREX",
        "PAIRS",
        "ALL_PAIRS",
        "ALL-PAIRS",
        "*",
    ):
        return list(FOREX_56_PAIRS)
    if "," in symbol_arg:
        return [s.strip() for s in symbol_arg.split(",") if s.strip()]
    return [symbol_arg.strip()]


def render_rich_opportunity_ranking(
    df: pd.DataFrame,
    best: pd.Series,
    best_entry: str,
    best_sl: str,
    best_tp1: str,
    best_tp2: str,
) -> None:
    """Render Opportunity Ranking table and top setup panel using rich."""
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    detected_width = Console().width or 135
    console = Console(width=max(detected_width, 135))

    table = Table(
        title=f"📊 OPPORTUNITY RANKING ({len(df)} setups, sorted by confluence quality)",
        title_style="bold cyan",
        box=box.ROUNDED,
        header_style="bold magenta",
        expand=False,
    )

    table.add_column("Time", justify="center", style="dim", no_wrap=True)
    table.add_column("Symbol", justify="center", style="bold yellow", no_wrap=True)
    table.add_column("Direction", justify="center", no_wrap=True)
    table.add_column("Pattern", justify="left", style="bold", no_wrap=True)
    table.add_column("Confluence", justify="right", style="bold", no_wrap=True)
    table.add_column("Grade", justify="center", no_wrap=True)
    table.add_column("Entry", justify="right", no_wrap=True)
    table.add_column("StopLoss", justify="right", style="red", no_wrap=True)
    table.add_column("TP1", justify="right", style="green", no_wrap=True)
    table.add_column("RR", justify="center", no_wrap=True)
    table.add_column("Trend", justify="center", no_wrap=True)
    table.add_column("RSI", justify="right", no_wrap=True)

    for _, row in df.iterrows():
        grade = str(row["Grade"])
        grade_style = (
            "bold green"
            if grade in ("EXCELLENT", "HIGH")
            else ("yellow" if grade == "MODERATE" else "dim")
        )
        conf_val = str(row["Confluence"])
        try:
            conf_num = float(conf_val.rstrip("%"))
        except ValueError:
            conf_num = 0.0
        conf_style = "bold green" if conf_num >= 60 else ("yellow" if conf_num >= 50 else "white")
        trend_val = str(row["Trend"])
        trend_style = "green" if "BULL" in trend_val else ("red" if "BEAR" in trend_val else "dim")
        tp1_val = str(row.get("TakeProfit_1", row.get("TP1", "-")))
        dir_val = str(row["Direction"])
        dir_styled = (
            "[bold green]BUY[/bold green]" if "BUY" in dir_val else "[bold red]SELL[/bold red]"
        )

        table.add_row(
            str(row["Time"]),
            str(row["Symbol"]),
            dir_styled,
            str(row["Pattern"]),
            f"[{conf_style}]{conf_val}[/{conf_style}]",
            f"[{grade_style}]{grade}[/{grade_style}]",
            str(row["Entry"]),
            str(row["StopLoss"]),
            tp1_val,
            str(row["RR"]),
            f"[{trend_style}]{trend_val}[/{trend_style}]",
            str(row["RSI"]),
        )

    console.print()
    console.print(table)

    best_dir = str(best["Direction"])
    best_dir_styled = (
        "[bold green]BUY[/bold green]" if "BUY" in best_dir else "[bold red]SELL[/bold red]"
    )
    best_border = "green" if "BUY" in best_dir else "red"
    panel_lines = [
        f"  • [bold]Trade Direction:[/bold]    {best_dir_styled} (Pattern: [bold cyan]{best['Pattern']}[/bold cyan])",
        f"  • [bold]Confluence Score:[/bold]   [bold green]{best['Confluence']}[/bold green] [Grade: [cyan]{best['Grade']}[/cyan]]",
        f"  • [bold]Trend Alignment:[/bold]    [green]{best['Trend']}[/green]",
        f"  • [bold]RSI(14) Momentum:[/bold]   {best['RSI']}",
        f"  • [bold]Entry Price:[/bold]        [bold]{best_entry}[/bold]",
        f"  • [bold]Invalidation (SL):[/bold]  [red]{best_sl}[/red]",
        f"  • [bold]Target 1 (TP1):[/bold]     [green]{best_tp1}[/green] (R/R {best['RR']})",
        f"  • [bold]Target 2 (TP2):[/bold]     [green]{best_tp2}[/green]",
        f"  • [bold]Signal Candle:[/bold]      {best['Time']}",
    ]
    panel = Panel(
        "\n".join(panel_lines),
        title=f"🏆 [bold gold1]TOP RECOMMENDED SETUP RIGHT NOW: {best['Symbol']}[/bold gold1]",
        border_style=best_border,
        expand=False,
    )
    console.print()
    console.print(panel)


def render_rich_ai_analyst_brief(analyst: AIMarketAnalyst, symbol: str, interval: str) -> None:
    """Render an executive AI Market Intelligence Brief in the terminal using Rich."""
    from rich import box
    from rich.console import Console
    from rich.panel import Panel

    from .ai.scorer import SignalGrade

    console = Console(safe_box=True)
    regime = analyst.get_market_regime_summary()
    results = analyst.scored_results
    high_conv = sum(1 for r in results if r.grade in (SignalGrade.EXCELLENT, SignalGrade.STRONG))

    clean_sym = symbol.replace("=X", "")
    curr_price = format_price(regime.get("current_price"))
    ret_20 = regime.get("return_20_bars_pct", 0.0)
    ret_color = "green" if ret_20 >= 0 else "red"
    ret_str = f"[{ret_color}]{ret_20:+.2f}%[/{ret_color}]"

    summary_text = (
        f"  • [bold]Asset:[/bold] [bold yellow]{clean_sym}[/bold yellow]  |  "
        f"[bold]Timeframe:[/bold] [bold cyan]{interval}[/bold cyan]  |  "
        f"[bold]Current Price:[/bold] [bold white]{curr_price}[/bold white]  |  "
        f"[bold]20-Bar Return:[/bold] {ret_str}\n"
        f"  • [bold]Signals Analyzed:[/bold] {len(results)}  |  "
        f"[bold]High Conviction Setups:[/bold] [bold green]{high_conv}[/bold green]"
    )

    console.print()
    console.print(
        Panel(
            summary_text,
            title=f"[bold cyan]AI Technical Intelligence Brief: {clean_sym} ({interval})[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    if not results:
        console.print(
            Panel(
                "No active patterns identified matching the selected criteria.",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
        return

    for i, res in enumerate(results[:5], 1):
        grade_val = res.grade.value if hasattr(res.grade, "value") else str(res.grade)
        grade_color = (
            "green" if grade_val == "EXCELLENT" else ("cyan" if grade_val == "STRONG" else "yellow")
        )
        grade_badge = f"[{grade_color}][{grade_val}][/{grade_color}]"
        conf_pct = f"[{grade_color}]{res.confidence * 100:.1f}%[/{grade_color}]"

        ts_str = format_timestamp(res.timestamp, interval)
        regime_color = (
            "green"
            if "BULL" in res.trend_regime.upper()
            else ("red" if "BEAR" in res.trend_regime.upper() else "yellow")
        )
        regime_str = f"[{regime_color}]{res.trend_regime}[/{regime_color}]"

        atr_str = format_price(res.atr) if res.atr is not None else "-"
        lines = [
            f"  • [bold]Signal Time:[/bold] {ts_str}  |  [bold]Regime:[/bold] {regime_str}",
            f"  • [bold]Momentum & Volatility:[/bold] RSI(14): [bold]{res.rsi:.1f}[/bold]  |  RVOL: {res.rvol:.2f}x  |  ATR: {atr_str}",
        ]

        if res.confluence_factors:
            lines.append("  • [bold]Confluence Drivers:[/bold]")
            for c in res.confluence_factors:
                lines.append(f"     [green]+[/green] {c}")

        if res.risk_factors:
            lines.append("  • [bold]Risk & Caveats:[/bold]")
            for rf in res.risk_factors:
                lines.append(f"     [yellow]![/yellow] {rf}")

        border_style = "cyan"
        if res.trade_setup:
            ts = res.trade_setup
            dir_color = "green" if ts.direction.upper() == "BUY" else "red"
            border_style = dir_color
            dir_str = f"[bold {dir_color}]{ts.direction.upper()}[/bold {dir_color}]"
            entry_str = format_price(ts.entry_price)
            sl_str = format_price(ts.stop_loss)
            tp1_str = format_price(ts.take_profit_1)
            tp2_str = format_price(ts.take_profit_2)
            lines.append(
                f"  • [bold]Trade Setup:[/bold] {dir_str} @ [bold]{entry_str}[/bold]  |  "
                f"Stop: [red]{sl_str}[/red]  |  "
                f"TP1: [green]{tp1_str}[/green]  |  "
                f"TP2: [green]{tp2_str}[/green]  "
                f"(R/R: [bold cyan]1:{ts.risk_reward_ratio:.1f}[/bold cyan])"
            )

        panel_title = (
            f"#{i} [bold]{res.pattern_name}[/bold] — {grade_badge} (Confluence: {conf_pct})"
        )
        console.print(
            Panel(
                "\n".join(lines),
                title=panel_title,
                border_style=border_style,
                box=box.ROUNDED,
            )
        )


def get_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="yfinance-ta-patterns",
        description="Scan candlestick patterns with optional AI multi-factor confluence scoring and trade setups.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # AI-scored patterns with trade setups\n"
            "  yftp --all-patterns --symbol EURUSD --timeframe 1h --ai --min-confidence 0.65\n\n"
            "  # AI Market Analyst executive brief\n"
            "  yftp --all-patterns --symbol AAPL --timeframe 1d --ai-analyst\n\n"
            "  # Export structured JSON for trading bots or LLMs\n"
            "  yftp --all-patterns --symbol BTC-USD --timeframe 4h --ai --format json\n\n"
            "  # Single pattern classic scan\n"
            "  yftp --pattern HAMMER --symbol EURUSD --timeframe 15m --period 30d\n"
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--check-talib",
        action="store_true",
        help="Check native TA-Lib binary status and display diagnostic installation hints.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help=(
            "Ticker symbol (e.g. EURUSD, AAPL, BTC-USD, NVDA, GC=F) or comma-separated list. "
            "If omitted or set to 'ALL'/'FOREX', scans all 56 major Forex currency pairs automatically."
        ),
    )
    parser.add_argument(
        "--symbols",
        dest="symbol_alias",
        help="Alias for --symbol (supports comma-separated list of symbols or 'ALL'/'FOREX').",
    )
    parser.add_argument(
        "--all-pairs",
        "--forex",
        dest="all_pairs",
        action="store_true",
        help="Scan all 56 major Forex currency pairs (equivalent to omitting --symbol).",
    )
    parser.add_argument(
        "--lookback-bars",
        type=int,
        default=None,
        help="Number of recent bars to scan (default: 2 for multi-symbol, 50 for --ai-analyst; 0 for all bars).",
    )
    parser.add_argument("--period", default="60d", help="History period, e.g. 60d, 1y, max")
    parser.add_argument(
        "--timeframe",
        default="15m",
        help=(
            "Timeframe (M1, M5, M15, M30, H1, H4, D1) "
            "or raw yfinance interval (1m, 5m, 15m, 1h, 4h, 1d...)."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--pattern",
        help="Single candlestick pattern, e.g. HAMMER (CDL prefix optional).",
    )
    group.add_argument(
        "--all-patterns",
        action="store_true",
        default=False,
        help="Scan and show signals for all TA-Lib candlestick patterns (default).",
    )
    parser.add_argument(
        "--date",
        help="Optional filter by date (YYYY-MM-DD). If omitted, show all signals.",
    )
    parser.add_argument(
        "--start-date",
        help="Optional start date (YYYY-MM-DD) for range filter (inclusive).",
    )
    parser.add_argument(
        "--end-date",
        help="Optional end date (YYYY-MM-DD) for range filter (inclusive).",
    )
    # AI Options
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable AI multi-factor confluence scoring and automated trade setup calculation.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Minimum AI multi-factor confluence score threshold (0.0 to 1.0, e.g. 0.65; default: 0.55 for multi-pair, 0.0 for single symbol).",
    )
    parser.add_argument(
        "--auto-adjust",
        action="store_true",
        default=False,
        help="Adjust OHLC prices for splits and dividends (total-return analysis). Default is raw unadjusted prices.",
    )
    repair_group = parser.add_mutually_exclusive_group()
    repair_group.add_argument(
        "--repair",
        dest="repair",
        action="store_true",
        default=None,
        help="Explicitly enable yfinance price anomaly repair (requires scikit-learn).",
    )
    repair_group.add_argument(
        "--no-repair",
        dest="repair",
        action="store_false",
        help="Explicitly disable yfinance price anomaly repair.",
    )
    parser.add_argument(
        "--execution",
        choices=["next_open", "close"],
        default="next_open",
        help="Trade execution timing: 'next_open' (unbiased, enters on bar i+1) or 'close' (legacy).",
    )
    parser.add_argument(
        "--min-signals",
        type=int,
        default=1,
        help="Minimum total signals required to include a pattern in ranking (reduces overfitting).",
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=0.0,
        help="Transaction fee deducted per completed trade in backtester.",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=0.0,
        help="Slippage in price units applied adversely to entries and exits in backtester.",
    )
    parser.add_argument(
        "--ai-analyst",
        action="store_true",
        help="Generate an executive AI market intelligence brief.",
    )
    parser.add_argument(
        "--prompt",
        action="store_true",
        help="Generate a structured prompt optimized for LLMs (ChatGPT, Claude, Gemini).",
    )
    parser.add_argument(
        "--calendar",
        action="store_true",
        help="Fetch and display upcoming high-impact macroeconomic calendar events for the symbol.",
    )
    parser.add_argument(
        "--news",
        action="store_true",
        help="Fetch and display recent market news headlines for the symbol.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output presentation format.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Launch native Model Context Protocol (MCP) stdio server for AI agents.",
    )
    parser.add_argument(
        "positional_symbols",
        nargs="*",
        default=[],
        help="Optional positional ticker symbol(s), e.g. 'yftp SPY' or 'yftp NVDA AAPL MSFT'.",
    )
    return parser


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    return get_parser().parse_args(args)


build_parser = get_parser


def _display_calendar_for_symbol(symbol: str, fmt: str = "text") -> None:
    """Fetch and display upcoming macroeconomic calendar events for a symbol."""
    try:
        from .economic_calendar import InvestingCalendar

        with InvestingCalendar(timeout=3.0) as cal:
            cal_res = cal.get_events_for_symbol(symbol=symbol, days=7, importances=["2", "3"], limit=10)
            events = cal_res.get("events", [])
            source = cal_res.get("source", "unknown")
            if fmt == "markdown":
                print(f"### 📅 Upcoming Macroeconomic Events: {symbol} (Feed: `{source}`)\n")
                if not events:
                    print("_No upcoming high-impact macroeconomic events found._\n")
                else:
                    for ev in events:
                        imp = ev.get("importance", "1")
                        stars = "⭐" * int(imp) if str(imp).isdigit() else str(imp)
                        time_str = ev.get("time", "")
                        print(
                            f"- **{time_str}** `[{ev.get('currency', '')}]` {ev.get('event', '')} ({stars}) | "
                            f"Forecast: `{ev.get('forecast', '—')}` | Previous: `{ev.get('previous', '—')}`"
                        )
                    print()
            else:
                print(f"\n📅 UPCOMING MACROECONOMIC EVENTS: {symbol} (Feed: {source})")
                print("=" * 76)
                if not events:
                    print("   No upcoming high-impact economic events found.")
                else:
                    for ev in events:
                        imp = ev.get("importance", "1")
                        stars = "⭐" * int(imp) if str(imp).isdigit() else str(imp)
                        time_str = ev.get("time", "")
                        curr = ev.get("currency", "")
                        ev_name = ev.get("event", "")
                        forecast = ev.get("forecast", "—")
                        print(f"   • [{time_str}] ({curr}) {ev_name} [{stars}] Forecast: {forecast}")
                print("=" * 76)
    except Exception as exc:
        if fmt != "json":
            print(f"\n⚠️ Economic calendar unavailable for {symbol}: {exc}", file=sys.stderr)


def _display_news_for_symbol(symbol: str, fmt: str = "text") -> None:
    """Fetch and display recent market news headlines for a symbol."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        raw_news = ticker.get_news(count=5) if hasattr(ticker, "get_news") else getattr(ticker, "news", [])
        if fmt == "markdown":
            print(f"### 📰 Recent Market News: {symbol}\n")
            if not raw_news:
                print("_No recent news headlines available._\n")
            else:
                for art in (raw_news or [])[:5]:
                    if isinstance(art, dict):
                        content = art.get("content", {}) if isinstance(art.get("content"), dict) else {}
                        title = content.get("title") or art.get("title") or art.get("headline") or "News Headline"
                        pub = (content.get("provider", {}) or {}).get("displayName") or art.get("publisher") or ""
                        link = (content.get("canonicalUrl", {}) or {}).get("url") or art.get("link") or ""
                        pub_str = f" *({pub})*" if pub else ""
                        link_str = f" — [link]({link})" if link else ""
                        print(f"- **{title}**{pub_str}{link_str}")
                print()
        else:
            print(f"\n📰 RECENT MARKET NEWS HEADLINES: {symbol}")
            print("=" * 76)
            if not raw_news:
                print("   No recent news headlines available.")
            else:
                for art in (raw_news or [])[:5]:
                    if isinstance(art, dict):
                        content = art.get("content", {}) if isinstance(art.get("content"), dict) else {}
                        title = content.get("title") or art.get("title") or art.get("headline") or "News Headline"
                        pub = (content.get("provider", {}) or {}).get("displayName") or art.get("publisher") or ""
                        link = (content.get("canonicalUrl", {}) or {}).get("url") or art.get("link") or ""
                        pub_str = f" ({pub})" if pub else ""
                        print(f"   • {title}{pub_str}")
                        if link:
                            print(f"     {link}")
            print("=" * 76)
    except Exception as exc:
        if fmt != "json":
            print(f"\n⚠️ Market news unavailable for {symbol}: {exc}", file=sys.stderr)


def run_cli(args: argparse.Namespace) -> int:
    """Run CLI logic with parsed arguments."""
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    if getattr(args, "mcp", False):
        from .mcp_server import main_mcp

        return main_mcp()

    if getattr(args, "check_talib", False):
        status = get_talib_status()
        print("=== TA-Lib Environment & Binary Status ===")
        print(f"Native TA-Lib Available: {status['has_native_talib']}")
        print(f"Python Implementation:   {status['implementation']} ({status['python_version']})")
        print(f"Platform:                {sys.platform}")
        if status["is_free_threaded"]:
            print("Free-Threaded Build:     True")
            print(f"GIL Currently Enabled:   {status['gil_enabled']}")
            if status.get("gil_cause"):
                print(f"GIL Status Detail:       {status['gil_cause']}")
            if status.get("gil_recommendation"):
                print(f"Recommendation:          {status['gil_recommendation']}")
            elif status["gil_enabled"]:
                print(
                    "Runtime Note:            Set PYTHON_GIL=0 or pass -X gil=0 to enable no-GIL mode."
                )
        print(f"Status Details:          {status['reason']}")
        if not status["has_native_talib"]:
            print("\n" + get_talib_install_hint())
        return 0

    interval = normalize_timeframe(args.timeframe)

    if not args.pattern and not args.all_patterns:
        args.all_patterns = True

    symbol_input = args.symbol or getattr(args, "symbol_alias", None)
    if not symbol_input and getattr(args, "positional_symbols", None):
        symbol_input = ",".join(args.positional_symbols)
    symbols = resolve_symbols(symbol_input, getattr(args, "all_pairs", False))

    min_confidence: float = (
        float(args.min_confidence)
        if args.min_confidence is not None
        else (0.55 if len(symbols) > 1 else 0.0)
    )

    if args.date and (args.start_date or args.end_date):
        raise ValueError("Use either --date or --start-date/--end-date, not both.")

    start_arg = args.start_date
    end_arg = args.end_date

    if args.date:
        # Scale lookback buffer based on timeframe so indicators (EMA200, RSI, ATR) have enough warm-up history
        target_dt = pd.to_datetime(args.date)
        if interval in ("1d", "1wk", "1mo"):
            lookback_days = 365  # 1 full year (~252 trading days) to ensure full EMA200 warm-up
        elif interval in ("4h", "1h", "60m"):
            lookback_days = 90
        elif interval in ("15m", "30m"):
            lookback_days = 60
        elif interval == "5m":
            lookback_days = 30
        else:  # 1m, 2m
            lookback_days = 7

        start_arg = (target_dt - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end_arg = (target_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    use_ai = args.ai or args.ai_analyst or args.prompt or (len(symbols) > 1 and not args.pattern)

    # ---------------------------------------------------------
    # MULTI-SYMBOL / FOREX PORTFOLIO SCANNING MODE
    # ---------------------------------------------------------
    if len(symbols) > 1:
        print("=" * 95)
        print(
            f"🌍 SCANNING {len(symbols)} FOREX CURRENCY PAIRS (Timeframe: {interval}, Period: {args.period})"
        )
        print(
            "Identifying high-probability setups via AI Confluence Scoring (Trend + RSI + Volume + ATR)"
        )
        print("=" * 95)

        if not HAS_NATIVE_TALIB and not args.pattern:
            fallback_count = len(PatternAnalyzer.get_supported_fallback_patterns())
            print(
                f"ℹ️ Native TA-Lib binary not found. Scanning {fallback_count} pure-Python fallback patterns.\n"
                f"{get_talib_install_hint()}\n",
                file=sys.stderr,
            )

        all_signals_records: list[dict[str, Any]] = []
        active_pairs_count = 0
        skipped_symbols: list[tuple[str, str]] = []
        lookback = args.lookback_bars if args.lookback_bars is not None else 2

        for idx, sym in enumerate(symbols, 1):
            clean_sym = sym.replace("=X", "")
            if sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
                print(
                    f"[{idx:2d}/{len(symbols)}] Querying {clean_sym:<7} ...", end="\r", flush=True
                )

            try:
                loader = MarketDataLoader(
                    sym,
                    period=args.period,
                    interval=interval,
                    start=start_arg,
                    end=end_arg,
                    auto_adjust=args.auto_adjust,
                    repair=args.repair,
                )
                df = loader.get_data()
                if df.empty or len(df) < 20:
                    skipped_symbols.append((clean_sym, "insufficient history (fewer than 20 bars)"))
                    continue

                active_pairs_count += 1
                analyzer = PatternAnalyzer(df)
                scorer = AIPatternScorer(df) if use_ai else None

                if args.date or args.start_date or args.end_date or lookback <= 0:
                    recent_timestamps = df.index
                else:
                    recent_timestamps = df.index[-lookback:] if len(df) >= lookback else df.index

                patterns_to_scan = (
                    [args.pattern] if args.pattern else sorted(analyzer.pattern_functions)
                )

                for pat in patterns_to_scan:
                    try:
                        sig_series = analyzer.get_signals(
                            pat,
                            date=args.date,
                            start_date=args.start_date,
                            end_date=args.end_date,
                        )
                    except NotImplementedError:
                        continue

                    if sig_series.empty:
                        continue

                    for ts in recent_timestamps:
                        if ts not in sig_series.index:
                            continue
                        signal_val = sig_series.loc[ts]
                        if signal_val == 0:
                            continue

                        clean_pat = pat.replace("CDL", "")
                        if scorer:
                            res = scorer.score_signal(pat, ts, signal_val)
                            score = res.confidence
                            if score >= min_confidence:
                                setup = res.trade_setup
                                grade_label = (
                                    res.grade.value
                                    if hasattr(res.grade, "value")
                                    else str(res.grade)
                                )
                                direction = (
                                    setup.direction
                                    if setup
                                    else ("BUY" if signal_val > 0 else "SELL")
                                )
                                entry = setup.entry_price if setup else float(df.loc[ts, "Close"])
                                sl = setup.stop_loss if setup else 0.0
                                tp1 = setup.take_profit_1 if setup else 0.0
                                tp2 = setup.take_profit_2 if setup else 0.0
                                rr = setup.risk_reward_ratio if setup else 1.5

                                all_signals_records.append(
                                    {
                                        "Symbol": clean_sym,
                                        "RawSymbol": sym,
                                        "Time": (
                                            ts.strftime("%d.%m %H:%M")
                                            if hasattr(ts, "strftime")
                                            else str(ts)
                                        ),
                                        "Direction": direction,
                                        "Pattern": clean_pat,
                                        "Confluence": f"{score * 100:.1f}%",
                                        "Grade": grade_label,
                                        "Entry": entry,
                                        "StopLoss": sl,
                                        "TakeProfit_1": tp1,
                                        "TakeProfit_2": tp2,
                                        "RR": f"1:{rr:.1f}",
                                        "Trend": res.trend_regime,
                                        "RSI": round(float(res.rsi), 1) if res.rsi is not None else None,
                                        "Score_Raw": score,
                                        "RR_Raw": rr,
                                        "df": df,
                                        "result": res,
                                    }
                                )
                        else:
                            all_signals_records.append(
                                {
                                    "Symbol": clean_sym,
                                    "RawSymbol": sym,
                                    "Time": (
                                        ts.strftime("%d.%m %H:%M")
                                        if hasattr(ts, "strftime")
                                        else str(ts)
                                    ),
                                    "Direction": "BUY" if signal_val > 0 else "SELL",
                                    "Pattern": clean_pat,
                                    "Price": float(df.loc[ts, "Close"]),
                                    "df": df,
                                }
                            )
            except Exception as exc:
                # A per-symbol failure must never vanish: the summary below lists it, so a
                # systematic problem (bad interval, missing history, API change) is visible.
                skipped_symbols.append((clean_sym, f"{type(exc).__name__}: {exc}"))
                continue

        if sys.stdout and hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            print(" " * 60, end="\r")
        print(f"✔ Scanned: {active_pairs_count} active pairs out of {len(symbols)}")

        if skipped_symbols:
            print(
                f"⚠️ {len(skipped_symbols)} of {len(symbols)} symbols skipped:",
                file=sys.stderr,
            )
            for skipped_sym, reason in skipped_symbols:
                print(f"   • {skipped_sym}: {reason}", file=sys.stderr)

        if not all_signals_records:
            bars_info = (
                f"over the last {lookback} bar(s)" if lookback > 0 else "over the selected period"
            )
            print(
                f"\n⚠️ No reliable setups with confidence >= {min_confidence * 100:.0f}% found {bars_info}."
            )
            print(
                "The market is consolidating / range-bound. Try switching timeframe to 15m or 1h."
            )
            return 0

        if not use_ai:
            classic_df = pd.DataFrame(all_signals_records)[
                ["Time", "Symbol", "Direction", "Pattern", "Price"]
            ]
            print("\n" + "=" * 80)
            print(f"📊 DETECTED SIGNALS ({len(classic_df)} patterns):")
            print("=" * 80)
            print(classic_df.to_string(index=False))
            return 0

        signals_df = pd.DataFrame(all_signals_records).sort_values(
            by=["Score_Raw", "RR_Raw"], ascending=[False, False]
        )

        display_cols = [
            "Time",
            "Symbol",
            "Direction",
            "Pattern",
            "Confluence",
            "Grade",
            "Entry",
            "StopLoss",
            "TakeProfit_1",
            "RR",
            "Trend",
            "RSI",
        ]
        best = signals_df.iloc[0]

        if args.format == "json":
            import json

            export_data = [
                {k: v for k, v in row.items() if k not in ("df", "result")}
                for row in signals_df.to_dict(orient="records")
            ]
            print(json.dumps(export_data, indent=2, ensure_ascii=False))
            return 0

        display_df = signals_df[display_cols].copy()
        for price_col in ("Entry", "StopLoss", "TakeProfit_1"):
            if price_col in display_df.columns:
                display_df[price_col] = display_df[price_col].apply(format_price)

        best_entry = format_price(best["Entry"])
        best_sl = format_price(best["StopLoss"])
        best_tp1 = format_price(best["TakeProfit_1"])
        best_tp2 = format_price(best["TakeProfit_2"])

        if args.format == "markdown":
            print(
                f"## 📊 Opportunity Ranking — {len(signals_df)} setups, "
                "sorted by confluence quality\n"
            )
            print(format_markdown_table(display_df))
            print(f"\n### 🏆 Top Recommended Setup: {best['Symbol']}\n")
            print(f"- **Trade Direction:** {best['Direction']} (Pattern: `{best['Pattern']}`)")
            print(f"- **Confluence Score:** {best['Confluence']} (Grade: `{best['Grade']}`)")
            print(f"- **Trend Alignment:** {best['Trend']}")
            print(f"- **RSI(14) Momentum:** {best['RSI']}")
            print(f"- **Entry Price:** `{best_entry}`")
            print(f"- **Invalidation (SL):** `{best_sl}`")
            print(f"- **Target 1 (TP1):** `{best_tp1}` (R/R `{best['RR']}`)")
            print(f"- **Target 2 (TP2):** `{best_tp2}`")
            print(f"- **Signal Candle:** `{best['Time']}`")
        else:
            try:
                render_rich_opportunity_ranking(
                    display_df, best, best_entry, best_sl, best_tp1, best_tp2
                )
            except Exception:
                print("\n" + "=" * 105)
                print(
                    f"📊 OPPORTUNITY RANKING ({len(signals_df)} setups, sorted by confluence quality):"
                )
                print("=" * 105)
                print(display_df.to_string(index=False))

                print("\n" + "🔥" * 38)
                print(f"  🏆 TOP RECOMMENDED SETUP RIGHT NOW: {best['Symbol']}")
                print("🔥" * 38)
                print(f"  • Trade Direction:    {best['Direction']} (Pattern: {best['Pattern']})")
                print(f"  • Confluence Score:   {best['Confluence']} [Grade: {best['Grade']}]")
                print(f"  • Trend Alignment:    {best['Trend']}")
                print(f"  • RSI(14) Momentum:   {best['RSI']}")
                print(f"  • Entry Price:        {best_entry}")
                print(f"  • Invalidation (SL):  {best_sl}")
                print(f"  • Target 1 (TP1):     {best_tp1} (R/R {best['RR']})")
                print(f"  • Target 2 (TP2):     {best_tp2}")
                print(f"  • Signal Candle:      {best['Time']}")
                print("=" * 76)

        if args.ai_analyst:
            analyst = AIMarketAnalyst(best["df"], [best["result"]])
            if args.format == "json":
                print(analyst.to_json(best["Symbol"], interval))
            elif args.format == "markdown":
                print(analyst.generate_brief(best["Symbol"], interval))
            else:
                try:
                    render_rich_ai_analyst_brief(analyst, best["Symbol"], interval)
                except Exception:
                    print(analyst.generate_brief(best["Symbol"], interval))

        if args.prompt:
            print("\n" + "=" * 76)
            print(f"🤖 LLM PROMPT FOR TOP SETUP: {best['Symbol']}")
            print("=" * 76 + "\n")
            analyst = AIMarketAnalyst(best["df"], [best["result"]])
            print(analyst.to_llm_prompt(best["Symbol"], interval))

        if args.format != "json":
            if getattr(args, "calendar", False):
                _display_calendar_for_symbol(best["Symbol"], args.format)
            if getattr(args, "news", False):
                _display_news_for_symbol(best["Symbol"], args.format)

        return 0

    # ---------------------------------------------------------
    # SINGLE-SYMBOL MODE (100% Backwards Compatible)
    # ---------------------------------------------------------
    symbol = symbols[0]

    def _finish(code: int = 0) -> int:
        if args.format != "json":
            if getattr(args, "calendar", False):
                _display_calendar_for_symbol(symbol, args.format)
            if getattr(args, "news", False):
                _display_news_for_symbol(symbol, args.format)
        return code

    loader = MarketDataLoader(
        symbol,
        period=args.period,
        interval=interval,
        start=start_arg,
        end=end_arg,
        auto_adjust=args.auto_adjust,
        repair=args.repair,
    )
    data = loader.get_data()
    period = loader.period

    if data.empty:
        if getattr(args, "calendar", False) or getattr(args, "news", False):
            return _finish(0)
        print(
            f"Error: No data retrieved for symbol '{symbol}' ({period}, {interval}).",
            file=sys.stderr,
        )
        return 1

    analyzer = PatternAnalyzer(data)
    range_info = ""
    if args.date:
        range_info = f" on {args.date}"
    elif args.start_date or args.end_date:
        range_info = f" from {args.start_date or 'beginning'} to {args.end_date or 'end'}"

    patterns_to_scan = [args.pattern] if args.pattern else sorted(analyzer.pattern_functions)
    if not HAS_NATIVE_TALIB and args.all_patterns:
        print(
            f"Notice: Native TA-Lib binary not found. Scanning {len(analyzer.pattern_functions)} pure-Python fallback patterns.\n"
            f"{get_talib_install_hint()}",
            file=sys.stderr,
        )

    single_lookback = args.lookback_bars
    if (
        single_lookback is None
        and (args.ai_analyst or args.prompt)
        and not args.date
        and not args.start_date
    ):
        single_lookback = 50

    recent_ts: set[Any] | None = None
    if single_lookback is not None and single_lookback > 0:
        recent_ts = (
            set(data.index[-single_lookback:]) if len(data) >= single_lookback else set(data.index)
        )

    all_scored_results: list[PatternConfidenceResult] = []
    classic_signals: dict[str, Any] = {}

    scorer = AIPatternScorer(data) if use_ai else None

    for pat in patterns_to_scan:
        signals = analyzer.get_signals(
            pat,
            date=args.date,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        if signals.empty:
            continue

        if recent_ts is not None:
            signals = signals[signals.index.isin(recent_ts)]
            if signals.empty:
                continue

        clean_name = pat.replace("CDL", "")
        if scorer:
            scored = scorer.score_all_signals(signals, clean_name, min_confidence=min_confidence)
            all_scored_results.extend(scored)
        else:
            classic_signals[clean_name] = signals

    # Align historical analysis when a historical --date is specified (Issue 20)
    analyst_data = data
    if args.date and not data.empty:
        target_cutoff = pd.to_datetime(args.date) + pd.Timedelta(days=1)
        if (
            isinstance(data.index, pd.DatetimeIndex)
            and data.index.tz is not None
            and target_cutoff.tz is None
        ):
            target_cutoff = target_cutoff.tz_localize(data.index.tz)
        sliced = data[data.index < target_cutoff]
        if not sliced.empty:
            analyst_data = sliced

    # 1. LLM Prompt Output
    if args.prompt:
        analyst = AIMarketAnalyst(analyst_data, all_scored_results)
        print(analyst.to_llm_prompt(symbol, interval))
        return _finish(0)

    # 2. AI Analyst Brief
    if args.ai_analyst:
        analyst = AIMarketAnalyst(analyst_data, all_scored_results)
        if args.format == "json":
            print(analyst.to_json(symbol, interval))
        elif args.format == "markdown":
            print(analyst.generate_brief(symbol, interval))
        else:
            try:
                render_rich_ai_analyst_brief(analyst, symbol, interval)
            except Exception:
                print(analyst.generate_brief(symbol, interval))
        return _finish(0)

    # 3. AI Mode Output
    if args.ai:
        if not all_scored_results:
            print(f"No AI-scored signals found for {symbol} matching criteria{range_info}.")
            return _finish(0)

        if args.format == "json":
            analyst = AIMarketAnalyst(analyst_data, all_scored_results)
            print(analyst.to_json(symbol, interval))
            return 0

        if args.format == "markdown":
            print(f"## 🧠 AI Pattern Intelligence: {symbol} ({interval}, {period}){range_info}\n")
            for res in all_scored_results:
                print(f"### {res.pattern_name} — `{res.grade.value}`\n")
                ts_str = format_timestamp(res.timestamp, interval)
                print(f"- **Timestamp:** `{ts_str}`")
                print(f"- **AI Confluence:** `{res.confidence * 100:.1f}/100`")
                atr_str = format_price(res.atr) if res.atr is not None else "-"
                print(
                    f"- **Regime:** `{res.trend_regime}` | RVOL: `{res.rvol:.2f}x` | "
                    f"RSI: `{res.rsi:.1f}` | ATR: `{atr_str}`"
                )
                if res.trade_setup:
                    ts = res.trade_setup
                    print(
                        f"- **Setup:** `{ts.direction}` @ `{format_price(ts.entry_price)}` | "
                        f"Stop: `{format_price(ts.stop_loss)}` | TP1: `{format_price(ts.take_profit_1)}` | "
                        f"TP2: `{format_price(ts.take_profit_2)}` (R/R `{ts.risk_reward_ratio:.1f}:1`)"
                    )
                if res.confluence_factors:
                    print(f"- **Confluence:** {', '.join(res.confluence_factors)}")
                if res.risk_factors:
                    print(f"- **Risks:** {', '.join(res.risk_factors)}")
                print()
            return _finish(0)

        print(f"=== AI Pattern Intelligence: {symbol} ({interval}, {period}){range_info} ===")
        for res in all_scored_results:
            conf_score = f"{res.confidence * 100:.1f}/100"
            ts_str = format_timestamp(res.timestamp, interval)
            print(
                f"\n[{res.grade.value}] {res.pattern_name} at {ts_str} | AI Confluence: {conf_score}"
            )
            atr_str = format_price(res.atr) if res.atr is not None else "-"
            print(
                f"  Regime: {res.trend_regime} | RVOL: {res.rvol:.2f}x | RSI: {res.rsi:.1f} | ATR: {atr_str}"
            )
            if res.trade_setup:
                ts = res.trade_setup
                print(
                    f"  Setup: {ts.direction} @ {format_price(ts.entry_price)} | "
                    f"Stop: {format_price(ts.stop_loss)} | TP1: {format_price(ts.take_profit_1)} | TP2: {format_price(ts.take_profit_2)} "
                    f"(R/R: {ts.risk_reward_ratio:.1f}:1)"
                )
            if res.confluence_factors:
                print(f"  Confluence: {', '.join(res.confluence_factors)}")
            if res.risk_factors:
                print(f"  Risks: {', '.join(res.risk_factors)}")
            print("-" * 60)
        return _finish(0)

    # 4. Classic TA-Lib Mode Output
    if not classic_signals:
        if args.pattern:
            print(
                f"No signals for pattern {args.pattern} "
                f"on period {period} timeframe {interval}{range_info}"
            )
        else:
            print(f"No signals for any pattern on period {period} timeframe {interval}{range_info}")
        return _finish(0)

    if args.format == "markdown":
        print(f"## 📈 Pattern Scan: {symbol} ({interval}, {period}){range_info}\n")
        for pat_name, sig in classic_signals.items():
            table = sig.rename("Signal").rename_axis("Timestamp").reset_index()
            print(f"### {pat_name}\n")
            print(format_markdown_table(table))
            print()
        return _finish(0)

    print(f"Scanning patterns for {symbol} ({interval}, {period}){range_info}...")
    for pat_name, sig in classic_signals.items():
        print(f"{pat_name}:")
        print(sig.to_string())
        print("-" * 40)

    return _finish(0)


def main(argv: Sequence[str] | None = None) -> None:
    """Main CLI entrypoint."""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parsed = parse_args(argv)
    try:
        sys.exit(run_cli(parsed))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
