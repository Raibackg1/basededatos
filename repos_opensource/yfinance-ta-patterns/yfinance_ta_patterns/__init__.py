"""YFinance TA Patterns: Advanced Technical Candlestick Analysis with AI Intelligence."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("yfinance-ta-patterns")
except PackageNotFoundError:  # pragma: no cover - during editable installs
    __version__ = "0.3.60"

import sys

if sys.version_info < (3, 9) and sys.implementation.name != "pypy":
    from ._compat_hook import install_compat_hook

    install_compat_hook()

if sys.platform == "win32" and sys.implementation.name == "pypy":
    try:
        import ctypes

        import dateutil.tz.win

        dateutil.tz.win.tzres.load_name = lambda self, offset: (
            lambda buf: buf[: self.LoadStringW(int(self._tzres._handle), offset, buf, 1024)]
        )(ctypes.create_unicode_buffer(1024))
    except Exception:
        pass

from .talib_compat import (
    ALL_CDL_PATTERNS,
    CUSTOM_PATTERNS,
    HAS_NATIVE_TALIB,
    SUPPORTED_FALLBACK_PATTERNS,
    TALIB_IMPORT_ERROR,
    UNSUPPORTED_FALLBACK_PATTERNS,
    TALibWrapper,
    get_talib_install_hint,
    get_talib_status,
    is_freethreaded,
    is_gil_enabled,
    talib,
)

try:
    from .pattern_analyzer import PatternAnalyzer
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    PatternAnalyzer = None  # type: ignore[assignment, misc]

try:
    from .pattern_tester import PatternRankingTester, PatternResult
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    PatternRankingTester = None  # type: ignore[assignment, misc]
    PatternResult = None  # type: ignore[assignment, misc]

try:
    from .data import (
        ALLOWED_ASSET_TYPES,
        MarketDataLoader,
        classify_asset,
        format_price,
        format_timestamp,
        normalize_ticker,
        resolve_asset_currencies,
        validate_asset_type,
    )
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    ALLOWED_ASSET_TYPES = ("stocks", "forex", "crypto", "indices", "commodities")  # type: ignore[assignment]
    MarketDataLoader = None  # type: ignore[assignment, misc]
    classify_asset = None  # type: ignore[assignment]
    format_price = None  # type: ignore[assignment]
    format_timestamp = None  # type: ignore[assignment]
    normalize_ticker = None  # type: ignore[assignment]
    resolve_asset_currencies = None  # type: ignore[assignment]
    validate_asset_type = None  # type: ignore[assignment]

try:
    from .forex_data_loader import (
        FOREX_56_PAIRS,
        FOREX_MAJOR_CURRENCIES,
        ForexDataLoader,
    )
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    FOREX_56_PAIRS = []  # type: ignore[assignment]
    FOREX_MAJOR_CURRENCIES = []  # type: ignore[assignment]
    ForexDataLoader = None  # type: ignore[assignment, misc]

try:
    from .ai import (
        AIMarketAnalyst,
        AIPatternScorer,
        PatternConfidenceResult,
        SignalGrade,
        TradeSetup,
    )
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    AIMarketAnalyst = None  # type: ignore[assignment, misc]
    AIPatternScorer = None  # type: ignore[assignment, misc]
    PatternConfidenceResult = None  # type: ignore[assignment, misc]
    SignalGrade = None  # type: ignore[assignment, misc]
    TradeSetup = None  # type: ignore[assignment, misc]

try:
    from .economic_calendar import (
        EconomicCalendar,
        InvestingCalendar,
        resolve_symbol_currencies,
    )
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    EconomicCalendar = None  # type: ignore[assignment, misc]
    InvestingCalendar = None  # type: ignore[assignment, misc]
    resolve_symbol_currencies = None  # type: ignore[assignment]


def __getattr__(name: str) -> object:
    if name == "YFinanceTAMCPServer":
        try:
            from .mcp_server import YFinanceTAMCPServer

            return YFinanceTAMCPServer
        except (ImportError, ModuleNotFoundError):  # pragma: no cover
            return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_all_patterns() -> list[str]:
    """Return a list of all 61 supported candlestick pattern names."""
    return list(ALL_CDL_PATTERNS)


__all__ = [
    "ALLOWED_ASSET_TYPES",
    "ALL_CDL_PATTERNS",
    "CUSTOM_PATTERNS",
    "FOREX_56_PAIRS",
    "FOREX_MAJOR_CURRENCIES",
    "HAS_NATIVE_TALIB",
    "SUPPORTED_FALLBACK_PATTERNS",
    "TALIB_IMPORT_ERROR",
    "UNSUPPORTED_FALLBACK_PATTERNS",
    "AIMarketAnalyst",
    "AIPatternScorer",
    "EconomicCalendar",
    "ForexDataLoader",
    "InvestingCalendar",
    "MarketDataLoader",
    "PatternAnalyzer",
    "PatternConfidenceResult",
    "PatternRankingTester",
    "PatternResult",
    "SignalGrade",
    "TALibWrapper",
    "TradeSetup",
    "YFinanceTAMCPServer",
    "__version__",
    "classify_asset",
    "format_price",
    "format_timestamp",
    "get_all_patterns",
    "get_talib_install_hint",
    "get_talib_status",
    "is_freethreaded",
    "is_gil_enabled",
    "normalize_ticker",
    "resolve_asset_currencies",
    "resolve_symbol_currencies",
    "talib",
    "validate_asset_type",
]
