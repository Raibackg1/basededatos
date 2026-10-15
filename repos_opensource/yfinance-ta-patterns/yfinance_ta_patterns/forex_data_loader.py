"""Forex data loader (maintained for backwards compatibility, inherits MarketDataLoader)."""

from __future__ import annotations

from typing import Any

from .data import MarketDataLoader

FOREX_MAJOR_CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "USD", "AUD", "CAD", "CHF", "JPY", "NZD")
FOREX_56_PAIRS: tuple[str, ...] = tuple(
    f"{a}{b}=X" for a in FOREX_MAJOR_CURRENCIES for b in FOREX_MAJOR_CURRENCIES if a != b
)


class ForexDataLoader(MarketDataLoader):
    """Data loader specifically targeted for Currency / Forex pairs.

    Maintains 100% backwards compatibility with earlier versions of yfinance-ta-patterns
    while supporting the full MarketDataLoader API (start, end, closed_only, etc.).
    """

    def __init__(
        self,
        symbol: str,
        period: str = "60d",
        interval: str = "15m",
        timezone: str = "Europe/Moscow",
        start: str | None = None,
        end: str | None = None,
        auto_adjust: bool = False,
        repair: bool | None = None,
        timeframe: str | None = None,
        closed_only: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize ForexDataLoader forcing forex asset type."""
        super().__init__(
            symbol=symbol,
            period=period,
            interval=interval,
            timezone=timezone,
            asset_type="forex",
            start=start,
            end=end,
            auto_adjust=auto_adjust,
            repair=repair,
            timeframe=timeframe,
            closed_only=closed_only,
            **kwargs,
        )
