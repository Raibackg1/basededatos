"""Universal market data loader module supporting Stocks, Forex, Crypto, Indices, and Commodities."""

from __future__ import annotations

import datetime
import importlib.util
import warnings
from typing import Any, cast

try:
    import numpy as np
    HAS_NUMPY = True
except (ImportError, ModuleNotFoundError, RuntimeError, TypeError):  # pragma: no cover
    from .talib_compat import np  # type: ignore[no-redef]
    HAS_NUMPY = False

try:
    import pandas as pd
    HAS_PANDAS = True
except (ImportError, ModuleNotFoundError, RuntimeError, TypeError):  # pragma: no cover
    pd = None  # type: ignore[assignment]
    HAS_PANDAS = False

import pytz

# yfinance is loaded lazily on demand in _fetch_yfinance
yf: Any = None

try:
    HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None
except Exception:
    HAS_SKLEARN = False


# Common standard 6-character currency pairs
_COMMON_FOREX_PAIRS = {
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "EURCHF",
    "AUDJPY",
    "EURAUD",
    "CADJPY",
    "GBPAUD",
    "NZDJPY",
    "AUDNZD",
    "AUDCAD",
    "USDRUB",
    "EURRUB",
}


# Standard ISO 4217 fiat and crypto currency 3-letter codes for Forex validation
_CURRENCY_CODES = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "CAD",
    "AUD",
    "NZD",
    "RUB",
    "CNH",
    "CNY",
    "SEK",
    "NOK",
    "SGD",
    "HKD",
    "TRY",
    "ZAR",
    "MXN",
    "PLN",
    "INR",
    "BRL",
    "KRW",
    "DKK",
    "THB",
    "IDR",
    "HUF",
    "CZK",
    "ILS",
    "CLP",
    "PHP",
    "AED",
    "COP",
    "SAR",
    "MYR",
    "RON",
    "TWD",  # Taiwan New Dollar (Issue 1)
    "ARS",  # Argentine Peso
    "PEN",  # Peruvian Sol
    "PKR",  # Pakistani Rupee
    "EGP",  # Egyptian Pound
    "VND",  # Vietnamese Dong
    "NGN",  # Nigerian Naira
    "KWD",  # Kuwaiti Dinar
    "QAR",  # Qatari Riyal
    "OMR",  # Omani Rial
    "BHD",  # Bahraini Dinar
    "JOD",  # Jordanian Dinar
    "UAH",  # Ukrainian Hryvnia
    "KZT",  # Kazakhstani Tenge
    "MAD",  # Moroccan Dirham
    "BGN",  # Bulgarian Lev
    "HRK",  # Croatian Kuna
    "RSD",  # Serbian Dinar
    "ISK",  # Icelandic Krona
}

_KNOWN_CRYPTO_SYMBOLS: set[str] = {
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "DOGE",
    "ADA",
    "BNB",
    "AVAX",
    "DOT",
    "MATIC",
    "LINK",
    "LTC",
    "BCH",
    "UNI",
    "NEAR",
    "SHIB",
    "TRX",
    "TON",
    "XLM",
    "ATOM",
    "XMR",
    "ETC",
    "ALGO",
    "FIL",
    "ICP",
    "APT",
    "SUI",
    "RENDER",
    "PEPE",
    "HBAR",
    "FET",
    "TAO",
    "INJ",
    "TIA",
    "SEI",
    "KAS",
}


_KNOWN_CRYPTO_QUOTES: tuple[str, ...] = (
    "USDT",
    "USDC",
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CAD",
    "AUD",
    "BTC",
    "ETH",
)

_EXCHANGE_SUFFIX_MAP: dict[str, str] = {
    # Germany / Eurozone
    ".DE": "EUR",
    ".F": "EUR",
    ".PA": "EUR",
    ".AS": "EUR",
    ".BR": "EUR",
    ".LS": "EUR",
    ".MI": "EUR",
    ".MC": "EUR",
    ".VI": "EUR",
    ".IR": "EUR",
    ".HE": "EUR",
    ".AT": "EUR",
    # UK
    ".L": "GBp",
    # Canada
    ".TO": "CAD",
    ".V": "CAD",
    ".CN": "CAD",
    # Australia & New Zealand
    ".AX": "AUD",
    ".NZ": "NZD",
    # Japan
    ".T": "JPY",
    # Hong Kong & China
    ".HK": "HKD",
    ".SS": "CNY",
    ".SZ": "CNY",
    # Switzerland
    ".SW": "CHF",
    # Nordic
    ".ST": "SEK",
    ".OL": "NOK",
    ".CO": "DKK",
    # Global
    ".KS": "KRW",
    ".KQ": "KRW",
    ".TW": "TWD",
    ".TWO": "TWD",
    ".SA": "BRL",
    ".MX": "MXN",
    ".TA": "ILS",
    ".SI": "SGD",
    ".SG": "SGD",
    ".JK": "IDR",
    ".BK": "THB",
    ".KL": "MYR",
    ".NS": "INR",
    ".BO": "INR",
    ".IS": "TRY",
    ".JO": "ZAR",
}

ALLOWED_ASSET_TYPES: frozenset[str] = frozenset(
    {"auto", "crypto", "forex", "stock", "index", "commodity"}
)


def validate_asset_type(asset_type: str | None) -> str:
    """Validate and normalize asset_type against allowed types."""
    kind = (asset_type or "auto").strip().lower()
    if kind not in ALLOWED_ASSET_TYPES:
        raise ValueError(f"Unsupported asset_type: {asset_type}")
    return kind


def format_price(val: Any) -> str:
    """Format price with appropriate precision for financial assets."""
    if val is None or val == "":
        return "-"
    try:
        f = float(val)
        if f == 0.0:
            return "0.00"
        abs_f = abs(f)
        if abs_f < 0.001:
            return f"{f:.6f}".strip()
        elif abs_f < 10.0:
            return f"{f:.5f}".strip()
        elif abs_f < 1000.0:
            formatted = f"{f:.4f}".rstrip("0")
            if formatted.endswith("."):
                formatted += "00"
            elif len(formatted.split(".")[1]) < 2:
                formatted += "0"
            return formatted.strip()
        else:
            return f"{f:.2f}".strip()
    except (ValueError, TypeError):
        return str(val).strip()


def format_timestamp(ts: Any, timeframe: str = "", include_tz: bool = False) -> str:
    """Format timestamp into a clean, human-readable string without noisy offsets."""
    if ts is None:
        return "-"
    try:
        dt = pd.to_datetime(ts)
        tf_lower = str(timeframe).lower()
        if tf_lower in ("1d", "1w", "1wk", "1mo", "3mo", "5d", "d1", "w1", "m1", "mn1"):
            return str(dt.strftime("%Y-%m-%d"))
        base = str(dt.strftime("%Y-%m-%d %H:%M"))
        if include_tz and dt.tzinfo is not None:
            tz_label = dt.tzname() or str(dt.strftime("%z"))
            if tz_label:
                return f"{base} {tz_label}"
        return base
    except Exception:
        return str(ts)


def normalize_ticker(symbol: str, asset_type: str = "auto", strict: bool = True) -> str:
    """Intelligently normalize symbol for Yahoo Finance API.

    Rules:
    - Normalizes slashes:
      - Crypto: "BTC/USD" -> "BTC-USD", "ETH/USD" -> "ETH-USD"
      - Forex: "EUR/USD" -> "EURUSD=X"
    - If asset_type is 'crypto' or auto-detected as crypto pair, format with hyphen (e.g. "BTC-USD").
      - If asset_type is 'forex' or auto-detected as valid currency pair, append '=X'.
    - If asset_type is 'stock', validates that symbol is not a forex pair or crypto pair.
    - Rejects identical base and quote currencies (e.g. "USDUSD", "EUR/EUR", "BTCBTC").
    - Rejects unknown currency codes in forex mode when strict=True (e.g. "ABC/XYZ").
    - If already formatted with suffix (=X, =F, -USD, ^) or standard stock ticker, preserve.
    """
    if not symbol or not symbol.strip():
        raise ValueError("Symbol cannot be empty.")

    clean = symbol.strip().upper()
    a_type = validate_asset_type(asset_type)

    # 1. Explicit crypto handling: converts slashes to hyphen, preserves existing hyphens
    if a_type == "crypto":
        if "/" in clean:
            parts = clean.split("/")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"Invalid crypto ticker format: '{symbol}'. Expected format like 'BTC/USD' or 'BTC-USD'."
                )
            if parts[0] == parts[1]:
                raise ValueError(
                    f"Base and quote symbols cannot be identical: '{parts[0]}/{parts[1]}'."
                )
            return f"{parts[0]}-{parts[1]}"
        if "-" in clean:
            parts = clean.split("-")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"Invalid crypto ticker format: '{symbol}'. Expected format like 'BTC-USD'."
                )
            if parts[0] == parts[1]:
                raise ValueError(
                    f"Base and quote symbols cannot be identical: '{parts[0]}/{parts[1]}'."
                )
            return clean
        if len(clean) % 2 == 0:
            half = len(clean) // 2
            if clean[:half] == clean[half:] and clean[:half] in _KNOWN_CRYPTO_SYMBOLS:
                raise ValueError(
                    f"Base and quote symbols cannot be identical: '{clean[:half]}/{clean[half:]}'."
                )
        for quote in _KNOWN_CRYPTO_QUOTES:
            if clean.endswith(quote) and len(clean) > len(quote):
                base = clean[: -len(quote)]
                if base == quote:
                    raise ValueError(
                        f"Base and quote symbols cannot be identical: '{base}/{quote}'."
                    )
                return f"{base}-{quote}"
        return f"{clean}-USD"

    # 2. Explicit forex handling: rejects crypto symbols and ensures valid currency pair format
    if a_type == "forex":
        if "/" in clean:
            parts = clean.split("/")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"Invalid forex ticker format: '{symbol}'. Slashes must separate two currency codes."
                )
            base, quote = parts[0], parts[1]
            if (
                base in _KNOWN_CRYPTO_SYMBOLS
                or quote in _KNOWN_CRYPTO_SYMBOLS
                or quote in ("USDT", "USDC")
            ):
                raise ValueError(
                    f"Cannot normalize crypto pair '{symbol}' when asset_type='forex'."
                )
            if base == quote:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{base}/{quote}'."
                )
            if strict and (base not in _CURRENCY_CODES or quote not in _CURRENCY_CODES):
                raise ValueError(f"Unsupported forex pair: {base}/{quote}")
            return f"{base}{quote}=X"

        if "-" in clean:
            parts = clean.split("-")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"Invalid forex ticker format: '{symbol}'.")
            base, quote = parts[0], parts[1]
            if (
                base in _KNOWN_CRYPTO_SYMBOLS
                or quote in _KNOWN_CRYPTO_SYMBOLS
                or quote in ("USDT", "USDC")
            ):
                raise ValueError(
                    f"Cannot normalize crypto pair '{symbol}' when asset_type='forex'."
                )
            if base == quote:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{base}/{quote}'."
                )
            if strict and (base not in _CURRENCY_CODES or quote not in _CURRENCY_CODES):
                raise ValueError(f"Unsupported forex pair: {base}/{quote}")
            return f"{base}{quote}=X"

        if clean.endswith("=X"):
            raw_pair = clean[:-2]
            if len(raw_pair) == 6 and raw_pair.isalpha():
                base, quote = raw_pair[:3], raw_pair[3:]
                if (
                    base in _KNOWN_CRYPTO_SYMBOLS
                    or quote in _KNOWN_CRYPTO_SYMBOLS
                    or quote in ("USDT", "USDC")
                ):
                    raise ValueError(
                        f"Cannot normalize crypto pair '{symbol}' when asset_type='forex'."
                    )
                if base == quote:
                    raise ValueError(
                        f"Base and quote currencies cannot be identical: '{base}/{quote}'."
                    )
                if strict and (base not in _CURRENCY_CODES or quote not in _CURRENCY_CODES):
                    raise ValueError(f"Unsupported forex pair: {base}/{quote}")
            return clean

        if len(clean) == 6 and clean.isalpha():
            base, quote = clean[:3], clean[3:]
            if (
                base in _KNOWN_CRYPTO_SYMBOLS
                or quote in _KNOWN_CRYPTO_SYMBOLS
                or quote in ("USDT", "USDC")
            ):
                raise ValueError(
                    f"Cannot normalize crypto pair '{symbol}' when asset_type='forex'."
                )
            if base == quote:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{base}/{quote}'."
                )
            if strict and (base not in _CURRENCY_CODES or quote not in _CURRENCY_CODES):
                raise ValueError(f"Unsupported forex pair: {base}/{quote}")
            return f"{clean}=X"

        for quote in _KNOWN_CRYPTO_QUOTES:
            if clean.endswith(quote) and len(clean) > len(quote):
                base = clean[: -len(quote)]
                if base in _KNOWN_CRYPTO_SYMBOLS:
                    raise ValueError(
                        f"Cannot normalize crypto pair '{symbol}' when asset_type='forex'."
                    )

        if strict:
            raise ValueError(f"Unsupported forex pair: {clean}")
        return f"{clean}=X"

    # 3. Explicit stock handling: rejects currency pairs, crypto pairs, and forex =X suffixes
    if a_type == "stock":
        if clean.endswith("=X"):
            raise ValueError(f"Cannot normalize forex ticker '{symbol}' when asset_type='stock'.")
        if "/" in clean:
            parts = clean.split("/")
            if len(parts) == 2 and parts[0] and parts[1]:
                base, quote = parts[0], parts[1]
                if (base in _CURRENCY_CODES and quote in _CURRENCY_CODES) or (
                    base == quote and base in _CURRENCY_CODES
                ):
                    raise ValueError(
                        f"Cannot normalize forex currency pair '{symbol}' when asset_type='stock'."
                    )
                if base in _KNOWN_CRYPTO_SYMBOLS or quote in _KNOWN_CRYPTO_SYMBOLS:
                    raise ValueError(
                        f"Cannot normalize crypto pair '{symbol}' when asset_type='stock'."
                    )
                if len(base) <= 5 and len(quote) <= 2:
                    return f"{base}-{quote}"
            raise ValueError(
                f"Invalid stock ticker format: '{symbol}'. Slashes are not supported for stocks."
            )
        if len(clean) == 6 and clean.isalpha():
            base, quote = clean[:3], clean[3:]
            if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
                raise ValueError(
                    f"Cannot normalize forex currency pair '{symbol}' when asset_type='stock'."
                )
        return clean

    # 4. Auto detection mode
    if "/" in clean:
        parts = clean.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Invalid ticker format: '{symbol}'. Slashes must separate exactly two symbols (e.g. 'EUR/USD' or 'BTC/USD')."
            )
        base, quote = parts[0], parts[1]
        if base == quote and (base in _CURRENCY_CODES or base in _KNOWN_CRYPTO_SYMBOLS):
            raise ValueError(f"Base and quote currencies cannot be identical: '{base}/{quote}'.")
        if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
            return f"{base}{quote}=X"
        if (
            base in _KNOWN_CRYPTO_SYMBOLS
            or quote in _KNOWN_CRYPTO_SYMBOLS
            or quote in _KNOWN_CRYPTO_QUOTES
            or quote in _CURRENCY_CODES
            or quote in ("USDT", "USDC")
        ):
            return f"{base}-{quote}"
        if len(base) <= 5 and len(quote) <= 2:
            return f"{base}-{quote}"
        if len(base) == 3 and len(quote) == 3 and base.isalpha() and quote.isalpha():
            if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
                return f"{base}{quote}=X"
            if strict:
                raise ValueError(f"Unsupported forex pair: {base}/{quote}")
            return f"{base}{quote}=X"

    if "-" in clean:
        parts = clean.split("-")
        if len(parts) == 2 and (not parts[0] or not parts[1]):
            raise ValueError(
                f"Invalid ticker format: '{symbol}'. A hyphen must separate two non-empty "
                "symbols (e.g. 'BTC-USD')."
            )
        if len(parts) == 2 and parts[0] in _CURRENCY_CODES and parts[1] in _CURRENCY_CODES:
            if parts[0] == parts[1]:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{parts[0]}/{parts[1]}'."
                )
            return f"{parts[0]}{parts[1]}=X"
        return clean

    if clean.endswith("=X"):
        raw_pair = clean[:-2]
        if len(raw_pair) == 6 and raw_pair.isalpha():
            base, quote = raw_pair[:3], raw_pair[3:]
            if base == quote:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{base}/{quote}'."
                )
    if clean.endswith("=F") or clean.startswith("^"):
        return clean

    if len(clean) % 2 == 0:
        half = len(clean) // 2
        if clean[:half] == clean[half:]:
            if clean[:half] in _CURRENCY_CODES:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{clean[:half]}/{clean[half:]}'."
                )
            if clean[:half] in _KNOWN_CRYPTO_SYMBOLS:
                raise ValueError(
                    f"Base and quote symbols cannot be identical: '{clean[:half]}/{clean[half:]}'."
                )

    for quote in _KNOWN_CRYPTO_QUOTES:
        if clean.endswith(quote) and len(clean) > len(quote):
            base = clean[: -len(quote)]
            if base == quote:
                raise ValueError(f"Base and quote symbols cannot be identical: '{base}/{quote}'.")
            if base in _KNOWN_CRYPTO_SYMBOLS:
                return f"{base}-{quote}"

    if clean in _COMMON_FOREX_PAIRS:
        base, quote = clean[:3], clean[3:]
        if base == quote:
            raise ValueError(f"Base and quote currencies cannot be identical: '{base}/{quote}'.")
        return f"{clean}=X"

    if len(clean) == 6 and clean.isalpha():
        base, quote = clean[:3], clean[3:]
        if base == quote:
            raise ValueError(f"Base and quote currencies cannot be identical: '{base}/{quote}'.")
        if base in _KNOWN_CRYPTO_SYMBOLS and (
            quote in _CURRENCY_CODES or quote in ("USDT", "USDC")
        ):
            return f"{base}-{quote}"
        if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
            return f"{clean}=X"

    return clean


def classify_asset(symbol: str, asset_type: str = "auto") -> str:
    """Classify an asset into 'crypto', 'forex', 'commodity', 'index', or 'stock'.

    Works seamlessly whether symbol is raw ('BTC/EUR', 'ETH-BTC', 'EURUSD=X', 'BTCUSDT') or normalized.
    """
    kind = validate_asset_type(asset_type)
    if kind != "auto":
        return kind

    # Normalize first to detect unseparated cryptos like BTCUSDT, DOGEUSD, SHIBUSDT
    norm = normalize_ticker(symbol, asset_type="auto")
    clean = norm.strip().upper()
    if clean.endswith("=F"):
        return "commodity"
    if clean.startswith("^"):
        return "index"
    if clean.endswith("=X"):
        return "forex"

    # Check separators: '-' or '/'
    sep = "-" if "-" in clean else ("/" if "/" in clean else None)
    if sep:
        parts = clean.split(sep)
        if len(parts) == 2:
            base, quote = parts[0], parts[1]
            if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
                return "forex"
            if (
                base in _KNOWN_CRYPTO_SYMBOLS
                or quote in _KNOWN_CRYPTO_SYMBOLS
                or quote in {"USDT", "USDC"}
            ):
                return "crypto"
            if (
                (quote in _CURRENCY_CODES or quote in _KNOWN_CRYPTO_QUOTES)
                and base not in _CURRENCY_CODES
                and len(quote) >= 3
            ):
                return "crypto"

    # Check 6-letter FX e.g. EURUSD
    if clean in _COMMON_FOREX_PAIRS:
        return "forex"
    if len(clean) == 6 and clean.isalpha():
        base, quote = clean[:3], clean[3:]
        if base in _KNOWN_CRYPTO_SYMBOLS and (
            quote in _CURRENCY_CODES or quote in {"USDT", "USDC", "BTC", "ETH"}
        ):
            return "crypto"
        if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
            return "forex"

    return "stock"


def resolve_asset_currencies(
    symbol: str,
    asset_type: str = "auto",
    metadata_currency: str | None = None,
) -> tuple[str, str]:
    """Resolve base and quote currency for any asset symbol.

    Returns (base_currency, quote_currency).
    Examples:
      'BTC-EUR' -> ('BTC', 'EUR')
      'ETH-BTC' -> ('ETH', 'BTC')
      'EURUSD=X' -> ('EUR', 'USD')
      'EUR/GBP' -> ('EUR', 'GBP')
      'USDJPY' -> ('USD', 'JPY')
      'SAP.DE' -> ('SAP.DE', 'EUR')
      'VOD.L' -> ('VOD.L', 'GBp')
      'AAPL' -> ('AAPL', 'USD')
    """
    if not symbol or not symbol.strip():
        raise ValueError("Symbol cannot be empty.")

    clean = symbol.strip().upper()

    # If instrument metadata specifies currency, respect it directly
    if metadata_currency is not None and metadata_currency.strip():
        meta_q = metadata_currency.strip()
        if meta_q in ("GBp", "GBX", "GBx"):
            meta_q = "GBp"
        elif meta_q in ("ZAc", "ZAC", "Zac", "zac"):
            meta_q = "ZAc"
        elif meta_q.upper() == "ILA":
            meta_q = "ILA"
        if clean.endswith("=X"):
            clean_fx = clean[:-2]
            if len(clean_fx) == 6:
                if clean_fx[:3] == clean_fx[3:]:
                    raise ValueError(
                        f"Base and quote currencies cannot be identical: '{clean_fx[:3]}/{clean_fx[3:]}'."
                    )
                return clean_fx[:3], meta_q
        sep = "-" if "-" in clean else ("/" if "/" in clean else None)
        if sep:
            parts = clean.split(sep)
            if len(parts) == 2:
                if parts[0] == parts[1] and (
                    parts[0] in _CURRENCY_CODES or parts[0] in _KNOWN_CRYPTO_SYMBOLS
                ):
                    raise ValueError(
                        f"Base and quote currencies cannot be identical: '{parts[0]}/{parts[1]}'."
                    )
                return parts[0], meta_q
        # Unseparated symbol (e.g. 'BTCBTC'): same identical-halves guard as the
        # no-metadata path below -- metadata_currency must not bypass it.
        if len(clean) % 2 == 0:
            half = len(clean) // 2
            if clean[:half] == clean[half:]:
                if clean[:half] in _CURRENCY_CODES:
                    raise ValueError(
                        f"Base and quote currencies cannot be identical: '{clean[:half]}/{clean[half:]}'."
                    )
                if clean[:half] in _KNOWN_CRYPTO_SYMBOLS:
                    raise ValueError(
                        f"Base and quote symbols cannot be identical: '{clean[:half]}/{clean[half:]}'."
                    )
        return clean, meta_q

    if clean.endswith("=X"):
        clean_fx = clean[:-2]
        if len(clean_fx) == 6:
            if clean_fx[:3] == clean_fx[3:]:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{clean_fx[:3]}/{clean_fx[3:]}'."
                )
            return clean_fx[:3], clean_fx[3:]

    # Check exchange suffix for stocks (e.g. .DE -> EUR, .L -> GBp, .TO -> CAD)
    for suffix, quote_curr in _EXCHANGE_SUFFIX_MAP.items():
        if clean.endswith(suffix):
            warnings.warn(
                f"Quote currency for '{symbol}' inferred as '{quote_curr}' using exchange suffix heuristic. "
                "For multi-currency exchanges (e.g. LSE with GBP/USD instruments), pass metadata_currency or explicit quote_currency.",
                UserWarning,
                stacklevel=2,
            )
            return clean, quote_curr

    # If explicitly or auto-classified as stock, treat entire ticker as base and USD as quote
    a_type = validate_asset_type(asset_type)
    if a_type == "stock" or (a_type == "auto" and classify_asset(clean, "auto") == "stock"):
        return clean, "USD"

    sep = "-" if "-" in clean else ("/" if "/" in clean else None)
    if sep:
        parts = clean.split(sep)
        if len(parts) == 2:
            base, quote = parts[0], parts[1]
            if base == quote and (base in _CURRENCY_CODES or base in _KNOWN_CRYPTO_SYMBOLS):
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{base}/{quote}'."
                )
            if (
                base in _KNOWN_CRYPTO_SYMBOLS
                or quote in _KNOWN_CRYPTO_SYMBOLS
                or quote in _KNOWN_CRYPTO_QUOTES
                or (base in _CURRENCY_CODES and quote in _CURRENCY_CODES)
            ):
                return base, quote
            if a_type in ("crypto", "forex"):
                return base, quote

    if len(clean) % 2 == 0:
        half = len(clean) // 2
        if clean[:half] == clean[half:]:
            if clean[:half] in _CURRENCY_CODES:
                raise ValueError(
                    f"Base and quote currencies cannot be identical: '{clean[:half]}/{clean[half:]}'."
                )
            if clean[:half] in _KNOWN_CRYPTO_SYMBOLS:
                raise ValueError(
                    f"Base and quote symbols cannot be identical: '{clean[:half]}/{clean[half:]}'."
                )

    # Check unseparated crypto (e.g. BTCUSDT, DOGEUSD)
    for quote in _KNOWN_CRYPTO_QUOTES:
        if clean.endswith(quote) and len(clean) > len(quote):
            base = clean[: -len(quote)]
            if base in _KNOWN_CRYPTO_SYMBOLS:
                if base == quote:
                    raise ValueError(
                        f"Base and quote symbols cannot be identical: '{base}/{quote}'."
                    )
                return base, quote

    if len(clean) == 6 and clean.isalpha():
        base, quote = clean[:3], clean[3:]
        if base == quote and (base in _CURRENCY_CODES or base in _KNOWN_CRYPTO_SYMBOLS):
            raise ValueError(f"Base and quote currencies cannot be identical: '{base}/{quote}'.")
        if (base in _CURRENCY_CODES and quote in _CURRENCY_CODES) or (
            base in _KNOWN_CRYPTO_SYMBOLS and quote in _KNOWN_CRYPTO_SYMBOLS
        ):
            return base, quote

    # Default to USD quote for standard equities, commodities, etc.
    return clean, "USD"


TIMEFRAME_MAP: dict[str, str] = {
    "M1": "1m",
    "M2": "2m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
    "W1": "1wk",
    "MN1": "1mo",
}

INTERVAL_DELTAS: dict[str, datetime.timedelta] = {
    "1m": datetime.timedelta(minutes=1),
    "2m": datetime.timedelta(minutes=2),
    "5m": datetime.timedelta(minutes=5),
    "15m": datetime.timedelta(minutes=15),
    "30m": datetime.timedelta(minutes=30),
    "60m": datetime.timedelta(hours=1),
    "1h": datetime.timedelta(hours=1),
    "90m": datetime.timedelta(minutes=90),
    "4h": datetime.timedelta(hours=4),
    "1d": datetime.timedelta(days=1),
    "5d": datetime.timedelta(days=5),
    "1wk": datetime.timedelta(days=7),
}


def normalize_interval(interval: str) -> str:
    """Normalize interval notation (e.g. 'm1', 'M1' -> '1m', 'h4', 'H4' -> '4h')."""
    clean = interval.strip()
    upper = clean.upper()
    if upper in TIMEFRAME_MAP:
        return TIMEFRAME_MAP[upper]
    lower = clean.lower()
    if lower in ("m1", "m2", "m5", "m15", "m30"):
        return f"{lower[1:]}m"
    if lower in ("h1", "h4"):
        return f"{lower[1:]}h"
    if lower == "d1":
        return "1d"
    return lower


def validate_ohlc(
    data: pd.DataFrame,
    strict: bool = False,
    require_ohlc: bool = False,
    check_anomalies: bool = False,
) -> pd.DataFrame:
    """Validate OHLC price integrity, drop corrupted rows, and check invariants.

    Parameters:
    -----------
    data : pd.DataFrame
        OHLCV market dataframe
    strict : bool
        If True, raises ValueError on bad data or missing OHLC; if False, validates available columns
    require_ohlc : bool
        If True, raises ValueError if required OHLC columns are missing, even when strict=False

    Returns:
    --------
    pd.DataFrame: Cleaned and validated dataframe
    """
    if data.empty:
        return data

    if isinstance(data.columns, pd.MultiIndex):
        cleaned_data = data.copy()
        lvl0_names = list(cleaned_data.columns.get_level_values(0))
        ohlc_names = {"Open", "High", "Low", "Close"}
        if any(c in ohlc_names for c in lvl0_names):
            cleaned_data.columns = cleaned_data.columns.get_level_values(0)
        elif cleaned_data.columns.nlevels > 1 and any(
            c in ohlc_names for c in cleaned_data.columns.get_level_values(1)
        ):
            cleaned_data.columns = cleaned_data.columns.get_level_values(1)
        else:
            cleaned_data.columns = cast(pd.MultiIndex, cleaned_data.columns).droplevel(1)

        if cleaned_data.columns.has_duplicates:
            dup_cols = [c for c in ohlc_names if list(cleaned_data.columns).count(c) > 1]
            if dup_cols:
                raise ValueError(
                    f"MultiIndex OHLC data contains multiple tickers or duplicate columns: {dup_cols}. "
                    "Please select or pass data for a single ticker."
                )
        data = cleaned_data

    if strict and hasattr(data.index, "has_duplicates") and data.index.has_duplicates:
        raise ValueError("Corrupted OHLC data: duplicate candle timestamps detected in index.")

    req_cols = [c for c in ["Open", "High", "Low", "Close"] if c in data.columns]

    if (strict or require_ohlc) and len(req_cols) < 4:
        missing = [c for c in ["Open", "High", "Low", "Close"] if c not in data.columns]
        raise ValueError(f"Missing required OHLC columns: {missing}")

    if not req_cols:
        return data

    # Check for non-finite values (np.inf, -np.inf, nan)
    finite_mask = np.isfinite(data[req_cols]).all(axis=1)
    if strict and not finite_mask.all():
        raise ValueError("Corrupted OHLC data: non-finite values (NaN or inf/-inf) found.")

    cleaned = data.loc[cast(Any, finite_mask)].copy()

    # Check for non-positive prices
    bad_prices = (cleaned[req_cols] <= 0).any(axis=1)
    if bad_prices.any():
        if strict:
            raise ValueError("Corrupted OHLC data: non-positive prices found.")
        cleaned = cleaned[~bad_prices]

    # Check bar geometry invariants if High and Low are present
    if "High" in cleaned.columns and "Low" in cleaned.columns:
        broken_bars = cleaned["High"] < cleaned["Low"]
        compare_cols = [c for c in ["Open", "Close"] if c in cleaned.columns]
        if compare_cols:
            top = cleaned[compare_cols].max(axis=1)
            bot = cleaned[compare_cols].min(axis=1)
            broken_bars = broken_bars | (cleaned["High"] < top) | (cleaned["Low"] > bot)

        if broken_bars.any():
            if strict:
                raise ValueError("Inconsistent OHLC bar geometry detected.")
            cleaned = cleaned[~broken_bars]

    dropped = len(data) - len(cleaned)
    if dropped > 0:
        if not strict:
            warnings.warn(
                f"validate_ohlc dropped {dropped} invalid OHLC row(s) (non-finite, non-positive, or broken bar geometry). "
                f"Note that dropping rows may create artificial time gaps that glue non-consecutive candles in multi-candle pattern analysis.",
                UserWarning,
                stacklevel=2,
            )
        cleaned.attrs["dropped_rows"] = dropped

    # Anomaly detection: flag corrupted near-zero body candles (Issue #12)
    if (
        check_anomalies
        and len(cleaned) >= 5
        and "Open" in cleaned.columns
        and "Close" in cleaned.columns
        and "High" in cleaned.columns
        and "Low" in cleaned.columns
    ):
        bodies = (cleaned["Close"] - cleaned["Open"]).abs()
        ranges = cleaned["High"] - cleaned["Low"]
        med_body = float(bodies.median())
        med_range = float(ranges.median())

        if med_range > 0:
            body_ratio = med_body / med_range
            if body_ratio < 0.05:
                warnings.warn(
                    f"Corrupted OHLC data anomaly detected: median candle body ({med_body:.5f}) is only {body_ratio:.1%} "
                    f"of median range ({med_range:.5f}). On Yahoo Finance, daily Forex data (e.g. EURUSD=X) often has "
                    f"corrupted Open prices near Close, making almost all bars false Dojis. "
                    f"Consider using clean resampled bars (clean_forex_daily=True) or intraday intervals (1h/4h).",
                    UserWarning,
                    stacklevel=2,
                )
                cleaned.attrs["zero_body_anomaly"] = True

    return cast(pd.DataFrame, cleaned)


def _get_market_session_hours(
    symbol: str, d: datetime.date
) -> tuple[str, datetime.time, datetime.time]:
    """Get exchange timezone, open time, and close time for an equity symbol."""
    clean_sym = symbol.strip().upper()
    if clean_sym.endswith(".L"):
        return "Europe/London", datetime.time(8, 0), datetime.time(16, 30)
    if any(
        clean_sym.endswith(sfx)
        for sfx in (".DE", ".PA", ".AS", ".BR", ".LS", ".MI", ".MC", ".VI", ".HE", ".F", ".AT")
    ):
        return "Europe/Berlin", datetime.time(9, 0), datetime.time(17, 30)
    if clean_sym.endswith(".SW"):
        return "Europe/Zurich", datetime.time(9, 0), datetime.time(17, 30)
    if any(clean_sym.endswith(sfx) for sfx in (".ST", ".OL", ".CO")):
        return "Europe/Stockholm", datetime.time(9, 0), datetime.time(17, 30)
    if clean_sym.endswith(".T"):
        close_min = 30 if d >= datetime.date(2024, 11, 5) else 0
        return "Asia/Tokyo", datetime.time(9, 0), datetime.time(15, close_min)
    if clean_sym.endswith(".HK"):
        return "Asia/Hong_Kong", datetime.time(9, 30), datetime.time(16, 0)
    if any(clean_sym.endswith(sfx) for sfx in (".SS", ".SZ")):
        return "Asia/Shanghai", datetime.time(9, 30), datetime.time(15, 0)
    if clean_sym.endswith(".AX"):
        return "Australia/Sydney", datetime.time(10, 0), datetime.time(16, 0)
    if clean_sym.endswith(".NZ"):
        return "Pacific/Auckland", datetime.time(10, 0), datetime.time(16, 45)
    if any(clean_sym.endswith(sfx) for sfx in (".TO", ".V", ".CN")):
        return "America/Toronto", datetime.time(9, 30), datetime.time(16, 0)
    if any(clean_sym.endswith(sfx) for sfx in (".KS", ".KQ")):
        return "Asia/Seoul", datetime.time(9, 0), datetime.time(15, 30)
    if any(clean_sym.endswith(sfx) for sfx in (".TW", ".TWO")):
        return "Asia/Taipei", datetime.time(9, 0), datetime.time(13, 30)
    if any(clean_sym.endswith(sfx) for sfx in (".SI", ".SG")):
        return "Asia/Singapore", datetime.time(9, 0), datetime.time(17, 0)
    if any(clean_sym.endswith(sfx) for sfx in (".NS", ".BO")):
        return "Asia/Kolkata", datetime.time(9, 15), datetime.time(15, 30)
    if clean_sym.endswith(".SA"):
        return "America/Sao_Paulo", datetime.time(10, 0), datetime.time(17, 0)
    if clean_sym.endswith(".MX"):
        return "America/Mexico_City", datetime.time(8, 30), datetime.time(15, 0)
    if clean_sym.endswith(".JO"):
        return "Africa/Johannesburg", datetime.time(9, 0), datetime.time(17, 0)
    if clean_sym.endswith(".TA"):
        return "Asia/Jerusalem", datetime.time(10, 0), datetime.time(17, 25)
    if clean_sym.endswith(".IS"):
        return "Europe/Istanbul", datetime.time(10, 0), datetime.time(18, 0)
    if clean_sym.endswith(".JK"):
        return "Asia/Jakarta", datetime.time(9, 0), datetime.time(16, 0)
    if clean_sym.endswith(".BK"):
        return "Asia/Bangkok", datetime.time(10, 0), datetime.time(16, 30)
    if clean_sym.endswith(".KL"):
        return "Asia/Kuala_Lumpur", datetime.time(9, 0), datetime.time(17, 0)

    # Check for unrecognized foreign exchange suffix (e.g. SYM.XYZ)
    if "." in clean_sym and not any(clean_sym.endswith(sfx) for sfx in ("-USD", "=X")):
        suffix = clean_sym.rsplit(".", 1)[-1]
        if suffix.isalpha():
            warnings.warn(
                f"Unknown exchange suffix '.{suffix}' in '{clean_sym}'. "
                f"Defaulting to US market session (America/New_York, 09:30-16:00).",
                UserWarning,
                stacklevel=2,
            )

    # Default US Equities
    is_early_close = False
    if d.month == 11 and d.weekday() == 4 and 23 <= d.day <= 29:
        is_early_close = True  # Black Friday
    elif d.month == 12 and d.day == 24 and d.weekday() < 5:
        is_early_close = True  # Christmas Eve
    elif d.month == 7 and d.day == 3 and d.weekday() < 4:
        is_early_close = True  # Day before July 4th
    close_hour = 13 if is_early_close else 16
    return "America/New_York", datetime.time(9, 30), datetime.time(close_hour, 0)


def _get_market_lunch_break(
    symbol: str, d: datetime.date
) -> tuple[datetime.time, datetime.time] | None:
    """Return lunch break start and end times in exchange local timezone if applicable."""
    clean_sym = symbol.strip().upper()
    if clean_sym.endswith(".T"):
        return datetime.time(11, 30), datetime.time(12, 30)
    if clean_sym.endswith(".HK"):
        return datetime.time(12, 0), datetime.time(13, 0)
    if any(clean_sym.endswith(sfx) for sfx in (".SS", ".SZ")):
        return datetime.time(11, 30), datetime.time(13, 0)
    return None


class MarketDataLoader:
    """Universal market data loader for Yahoo Finance tickers."""

    def __init__(
        self,
        symbol: str,
        period: str = "60d",
        interval: str = "15m",
        timezone: str = "Europe/Moscow",
        asset_type: str = "auto",
        start: str | None = None,
        end: str | None = None,
        auto_adjust: bool = False,
        repair: bool | None = None,
        timeframe: str | None = None,
        closed_only: bool = True,
        clean_forex_daily: bool = False,
    ) -> None:
        """Initialize data loader with symbol and timeframe parameters.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'EURUSD', 'BTC-USD').
            period: History period (e.g. '60d', '1y', 'max').
            interval: Bar interval (e.g. '15m', '1h', '4h', '1d').
            timezone: Output timezone for candle index (default 'Europe/Moscow').
            asset_type: 'auto', 'stock', 'forex', 'crypto', 'commodity', 'index'.
            start: Optional start date string (YYYY-MM-DD).
            end: Optional end date string (YYYY-MM-DD).
            auto_adjust: If False (default), preserves raw historical traded prices and supplies
                separate 'Adj Close' column. Appropriate for price action and chart patterns.
                If True, adjusts all OHLC prices for corporate actions (splits and dividends),
                producing a total-return series suitable for total-return quantitative backtesting.
            repair: If True, attempts price anomaly correction via yfinance (requires scikit-learn).
                If None (default), enables repair only if scikit-learn is installed without warning.
                If False, repair is disabled.
            timeframe: Alias keyword for interval.
            closed_only: If True, filters out unclosed (forming) candles based on market session.
            clean_forex_daily: If True and interval is '1d' for Forex, downloads 1h bars and resamples
                them to construct clean daily bars with genuine candle bodies (counteracting Yahoo's
                corrupted daily Forex Open prices).
        """
        effective_interval = timeframe if timeframe is not None else interval
        norm_interval = normalize_interval(effective_interval)
        self.symbol: str = symbol
        self.asset_type: str = validate_asset_type(asset_type)
        self.closed_only: bool = closed_only
        self.ticker: str = normalize_ticker(symbol, asset_type=self.asset_type)
        # Yahoo Finance restricts intraday lookback windows:
        # - 1m: max 7-8 days
        # - 2m, 5m, 15m, 30m, 90m: max 60 days
        # - 60m, 1h, 4h (resampled from 1h): max 730 days (2y)
        self.period: str = self._clamp_period_for_interval(period, norm_interval)
        self.interval: str = norm_interval
        self.timezone: str = timezone
        self.start: str | None = start
        self.end: str | None = end
        self.start_date: str | None = start
        self.end_date: str | None = end
        self.auto_adjust: bool = auto_adjust
        self.repair: bool | None = repair
        self.clean_forex_daily: bool = clean_forex_daily

        is_fx = self.asset_type == "forex" or self.ticker.endswith("=X")
        if clean_forex_daily and norm_interval == "1d" and is_fx:
            self._download_interval: str = "1h"
            self._resample_rule: str | None = "1D"
        else:
            self._download_interval = self._resolve_download_interval(norm_interval)
            self._resample_rule = "4h" if norm_interval == "4h" else None

    @staticmethod
    def _clamp_period_for_interval(period: str, norm_interval: str) -> str:
        """Clamp lookback period to Yahoo Finance's maximum supported window for intraday intervals."""
        p_clean = (period or "60d").strip()
        p_lower = p_clean.lower()
        if norm_interval == "1m":
            if p_lower in (
                "14d",
                "15d",
                "30d",
                "60d",
                "90d",
                "1mo",
                "3mo",
                "6mo",
                "1y",
                "2y",
                "5y",
                "10y",
                "ytd",
                "max",
            ):
                return "7d"
        elif norm_interval in ("2m", "5m", "15m", "30m", "90m"):
            if p_lower in ("90d", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"):
                return "60d"
        elif norm_interval in ("60m", "1h", "4h") and p_lower in ("5y", "10y", "max"):
            return "730d"
        return p_clean

    @staticmethod
    def _resolve_download_interval(interval: str) -> str:
        """Map custom intervals to supported yfinance download intervals."""
        if interval == "4h":
            return "1h"  # fetch 1h data and resample to 4h
        return interval

    def fetch(self) -> pd.DataFrame:
        """Fetch raw market data via yfinance."""
        start_val = self.start or self.start_date
        end_val = self.end or self.end_date
        is_intraday_forex = (
            self.asset_type == "forex" or self.ticker.endswith("=X")
        ) and self._download_interval not in ("1d", "5d", "1wk", "1mo", "3mo")

        if self.repair is True:
            if not HAS_SKLEARN:
                warnings.warn(
                    "Data repair was requested (repair=True), but 'scikit-learn' is not installed. "
                    "Disabling yfinance data repair. Install scikit-learn via 'pip install scikit-learn' to enable repair.",
                    UserWarning,
                    stacklevel=2,
                )
                repair_opt = False
            else:
                repair_opt = True
        elif self.repair is False:
            repair_opt = False
        else:
            # self.repair is None: silently enable repair if scikit-learn is available,
            # except for intraday Forex where yfinance's repair algorithm erroneously discards all rows (Issue #17).
            repair_opt = HAS_SKLEARN and not is_intraday_forex

        global yf
        if yf is None:
            import yfinance as yf

        if start_val or end_val:
            data = yf.download(
                self.ticker,
                start=start_val,
                end=end_val,
                interval=self._download_interval,
                auto_adjust=self.auto_adjust,
                repair=repair_opt,
                progress=False,
            )
        else:
            data = yf.download(
                self.ticker,
                period=self.period,
                interval=self._download_interval,
                auto_adjust=self.auto_adjust,
                repair=repair_opt,
                progress=False,
            )

        # Resilient fallback: if repair was auto-enabled (repair=None) and returned an empty DataFrame,
        # retry once with repair=False before raising (Issue #17).
        if data.empty and self.repair is None and repair_opt is True:
            if start_val or end_val:
                data = yf.download(
                    self.ticker,
                    start=start_val,
                    end=end_val,
                    interval=self._download_interval,
                    auto_adjust=self.auto_adjust,
                    repair=False,
                    progress=False,
                )
            else:
                data = yf.download(
                    self.ticker,
                    period=self.period,
                    interval=self._download_interval,
                    auto_adjust=self.auto_adjust,
                    repair=False,
                    progress=False,
                )

        if data.empty:
            raise ValueError(
                f"No market data found on Yahoo Finance for ticker '{self.ticker}' "
                f"(interval='{self.interval}', period='{self.period}'). "
                "Verify that the symbol is valid and active, or check network connectivity to Yahoo Finance."
            )
        return cast(pd.DataFrame, data)

    def _resample_if_needed(
        self, data: pd.DataFrame, now_utc: pd.Timestamp | None = None
    ) -> pd.DataFrame:
        """Resample OHLCV data when the requested interval is not natively supported."""
        if not self._resample_rule or data.empty:
            return data

        agg: dict[Any, Any] = {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
        }
        if "Adj Close" in data.columns:
            agg["Adj Close"] = "last"
        if "Volume" in data.columns:
            agg["Volume"] = "sum"

        for col in data.columns:
            col_str = str(col)
            if col_str not in agg:
                agg[col_str] = "last"

        rule_str = str(self._resample_rule or "4h").lower()
        rule_to_use = "24h" if rule_str in ("1d", "d1") else (self._resample_rule or "4h")
        # Anchor resampling in UTC (origin='epoch') to preserve institutional candle bounds
        resampled = data.resample(rule_to_use, origin="epoch").agg(agg).dropna()

        # Check input bar completeness per bucket (Issue 3: YF-003, Screenshot 2 & Screenshot 5)
        bar_counts = data.resample(rule_to_use, origin="epoch")["Close"].count()
        resolved_type = classify_asset(self.ticker, self.asset_type)
        is_crypto = resolved_type == "crypto"
        is_forex = resolved_type == "forex"

        def _is_bucket_complete(ts: pd.Timestamp, count: int) -> bool:
            rule_str = str(self._resample_rule or "4h")
            bucket_start = ts
            bucket_end = ts + pd.Timedelta(rule_str)
            dt_index = cast(pd.DatetimeIndex, data.index)
            bucket_stamps = dt_index[(dt_index >= bucket_start) & (dt_index < bucket_end)]
            if len(bucket_stamps) == 0:
                return False

            if rule_str.lower() in ("1d", "d1"):
                if is_crypto:
                    return len(bucket_stamps) >= 20
                if is_forex:
                    return len(bucket_stamps) >= 15
                return len(bucket_stamps) >= 5

            if is_crypto:
                # Crypto trades 24/7. Exactly 4 hourly bars expected on the regular 1h grid:
                # ts, ts+1h, ts+2h, ts+3h.
                # Must have exactly 4 bars, no irregular displaced timestamps, and match expected slots 1-to-1.
                if len(bucket_stamps) != 4:
                    return False
                expected_crypto = [bucket_start + pd.Timedelta(hours=k) for k in range(4)]
                matched_indices = set()
                for exp in expected_crypto:
                    found = None
                    for i, act in enumerate(bucket_stamps):
                        if i in matched_indices:
                            continue
                        if abs((act - exp).total_seconds()) <= 300:  # within 5 minutes of the hour
                            found = i
                            break
                    if found is None:
                        return False
                    matched_indices.add(found)
                return True

            if is_forex:
                open_slots: list[pd.Timestamp] = []
                for k in range(4):
                    slot = ts + pd.Timedelta(hours=k)
                    slot_ny = (
                        slot.tz_convert("America/New_York")
                        if slot.tz is not None
                        else slot.tz_localize("UTC").tz_convert("America/New_York")
                    )
                    wd = slot_ny.weekday()
                    hr = slot_ny.hour
                    is_open = (
                        (wd == 6 and hr >= 17) or (wd in (0, 1, 2, 3)) or (wd == 4 and hr < 17)
                    )
                    if is_open:
                        open_slots.append(slot)
                if len(open_slots) == 0:
                    return False
                if len(bucket_stamps) < len(open_slots):
                    return False
                matched_fx_indices = set()
                for exp in open_slots:
                    found = None
                    for i, act in enumerate(bucket_stamps):
                        if i in matched_fx_indices:
                            continue
                        if abs((act - exp).total_seconds()) <= 300:
                            found = i
                            break
                    if found is None:
                        return False
                    matched_fx_indices.add(found)
                if len(bucket_stamps) > 1:
                    diffs = np.diff(bucket_stamps.values).astype("timedelta64[m]").astype(int)
                    if np.any(diffs > 75):
                        return False
                return True

            # Stocks and other assets:
            # Build expected hourly session grid from exchange open_time to close_time,
            # taking scheduled lunch breaks into account for Asian exchanges.
            tz_sample, _, _ = _get_market_session_hours(self.ticker, bucket_start.date())
            b_start_loc = (
                bucket_start.tz_convert(tz_sample)
                if bucket_start.tz is not None
                else bucket_start.tz_localize("UTC").tz_convert(tz_sample)
            )
            b_end_loc = (
                (bucket_end - pd.Timedelta(seconds=1)).tz_convert(tz_sample)
                if bucket_end.tz is not None
                else (bucket_end - pd.Timedelta(seconds=1)).tz_localize("UTC").tz_convert(tz_sample)
            )
            dates_to_check = {b_start_loc.date(), b_end_loc.date()}
            expected_stock_slots: list[pd.Timestamp] = []
            break_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []

            for d in sorted(dates_to_check):
                if d.weekday() >= 5:
                    continue  # Weekend
                tz_name, open_time, close_time = _get_market_session_hours(self.ticker, d)
                lunch_break = _get_market_lunch_break(self.ticker, d)
                s_open = pd.Timestamp(
                    year=d.year,
                    month=d.month,
                    day=d.day,
                    hour=open_time.hour,
                    minute=open_time.minute,
                    tz=tz_name,
                )
                s_close = pd.Timestamp(
                    year=d.year,
                    month=d.month,
                    day=d.day,
                    hour=close_time.hour,
                    minute=close_time.minute,
                    tz=tz_name,
                )

                if lunch_break is not None:
                    br_start_t, br_end_t = lunch_break
                    s_br_start = pd.Timestamp(
                        year=d.year,
                        month=d.month,
                        day=d.day,
                        hour=br_start_t.hour,
                        minute=br_start_t.minute,
                        tz=tz_name,
                    )
                    s_br_end = pd.Timestamp(
                        year=d.year,
                        month=d.month,
                        day=d.day,
                        hour=br_end_t.hour,
                        minute=br_end_t.minute,
                        tz=tz_name,
                    )
                    br_utc_start = (
                        s_br_start.tz_convert("UTC")
                        if bucket_start.tz is not None
                        else s_br_start.tz_convert("UTC").tz_localize(None)
                    )
                    br_utc_end = (
                        s_br_end.tz_convert("UTC")
                        if bucket_start.tz is not None
                        else s_br_end.tz_convert("UTC").tz_localize(None)
                    )
                    break_windows.append((br_utc_start, br_utc_end))

                    # Morning session
                    curr = s_open
                    while curr < s_br_start:
                        curr_utc = (
                            curr.tz_convert("UTC")
                            if bucket_start.tz is not None
                            else curr.tz_convert("UTC").tz_localize(None)
                        )
                        if bucket_start <= curr_utc < bucket_end:
                            expected_stock_slots.append(curr_utc)
                        curr += pd.Timedelta(hours=1)

                    # Afternoon session
                    curr = s_br_end
                    while curr < s_close:
                        curr_utc = (
                            curr.tz_convert("UTC")
                            if bucket_start.tz is not None
                            else curr.tz_convert("UTC").tz_localize(None)
                        )
                        if bucket_start <= curr_utc < bucket_end:
                            expected_stock_slots.append(curr_utc)
                        curr += pd.Timedelta(hours=1)
                else:
                    curr = s_open
                    while curr < s_close:
                        curr_utc = (
                            curr.tz_convert("UTC")
                            if bucket_start.tz is not None
                            else curr.tz_convert("UTC").tz_localize(None)
                        )
                        if bucket_start <= curr_utc < bucket_end:
                            expected_stock_slots.append(curr_utc)
                        curr += pd.Timedelta(hours=1)

            if len(expected_stock_slots) > 0:
                if len(bucket_stamps) < len(expected_stock_slots):
                    return False
                matched_stock_indices = set()
                for exp in expected_stock_slots:
                    found = None
                    for i, act in enumerate(bucket_stamps):
                        if i in matched_stock_indices:
                            continue
                        if abs((act - exp).total_seconds()) <= 300:
                            found = i
                            break
                    if found is None:
                        return False
                    matched_stock_indices.add(found)

            if len(bucket_stamps) > 1:
                # Check for unexpected gaps between consecutive bars.
                # If gap spans across a scheduled lunch break, allow break duration + 75m.
                for i in range(len(bucket_stamps) - 1):
                    t1 = bucket_stamps[i]
                    t2 = bucket_stamps[i + 1]
                    gap_minutes = int((t2 - t1).total_seconds() / 60)
                    is_scheduled_break = False
                    for b_s, b_e in break_windows:
                        if t1 <= b_s and t2 >= b_e:
                            br_duration = int((b_e - b_s).total_seconds() / 60)
                            if gap_minutes <= 75 + br_duration:
                                is_scheduled_break = True
                                break
                    if not is_scheduled_break and gap_minutes > 75:
                        return False

            return True

        valid_buckets = [
            ts
            for ts, count in bar_counts.items()
            if _is_bucket_complete(cast(pd.Timestamp, ts), int(count))
        ]
        resampled = resampled.loc[resampled.index.isin(valid_buckets)]

        if (
            self.closed_only
            and not resampled.empty
            and isinstance(resampled.index, pd.DatetimeIndex)
        ):
            now = now_utc if now_utc is not None else pd.Timestamp.now(tz=pytz.UTC)
            ends_tz = getattr(resampled.index, "tz", None)
            if ends_tz is not None and now.tz is None:
                now = now.tz_localize(pytz.UTC)
            elif ends_tz is None and now.tz is not None:
                now = now.tz_localize(None)

            input_td = pd.Timedelta(self._download_interval)
            max_input_time = cast(pd.Timestamp, data.index.max())
            max_input_end = max_input_time + input_td
            if ends_tz is not None and max_input_end.tz is None:
                max_input_end = max_input_end.tz_localize(pytz.UTC)
            elif ends_tz is None and max_input_end.tz is not None:
                max_input_end = max_input_end.tz_localize(None)

            clean_sym = self.ticker.strip().upper()
            valid_completed_mask = []
            for b_start in resampled.index:
                b_end = b_start + pd.Timedelta(str(self._resample_rule or "4h"))
                if not is_crypto and not is_forex:
                    tz_name, _, _ = _get_market_session_hours(clean_sym, b_start.date())
                    b_loc = (
                        b_start.tz_convert(tz_name)
                        if b_start.tz is not None
                        else b_start.tz_localize("UTC").tz_convert(tz_name)
                    )
                    d = b_loc.date()
                    tz_name, _, close_t = _get_market_session_hours(clean_sym, d)
                    s_close = pd.Timestamp(
                        year=d.year,
                        month=d.month,
                        day=d.day,
                        hour=close_t.hour,
                        minute=close_t.minute,
                        tz=tz_name,
                    )
                    s_close_cmp = (
                        s_close.tz_convert(ends_tz)
                        if ends_tz is not None
                        else s_close.tz_localize(None)
                    )
                    effective_end = min(b_end, s_close_cmp) if b_start <= s_close_cmp else b_end
                else:
                    effective_end = b_end

                is_complete = (effective_end <= now) and (effective_end <= max_input_end)
                valid_completed_mask.append(is_complete)

            resampled = resampled.loc[valid_completed_mask]

        return cast(pd.DataFrame, resampled)

    def process(self, data: pd.DataFrame, now_utc: pd.Timestamp | None = None) -> pd.DataFrame:
        """Normalize column indexing, validate OHLC, resample in UTC, and convert timezone."""
        if data.empty:
            return data

        # Drop extra MultiIndex level if present (yfinance >= 0.2.40)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)

        # Check for duplicate timestamps in index
        if hasattr(data.index, "has_duplicates") and data.index.has_duplicates:
            raise ValueError(
                "Corrupted market data: duplicate candle timestamps detected in index."
            )

        if not data.index.is_monotonic_increasing:
            data = data.sort_index()

        is_fx = self.asset_type == "forex" or self.ticker.endswith("=X")
        check_anomaly = is_fx and self.interval == "1d" and not self.clean_forex_daily
        data = validate_ohlc(data, require_ohlc=True, check_anomalies=check_anomaly)
        if data.empty:
            return data

        idx = data.index
        # Calendar days of the raw index, in the exchange's own timezone. These are only
        # trustworthy while the index is left untouched, so they are dropped when
        # resampling rebuilds it (see below).
        orig_dates = list(idx.date) if isinstance(idx, pd.DatetimeIndex) else None
        if isinstance(idx, pd.DatetimeIndex):
            if idx.tz is None:
                data.index = idx.tz_localize(pytz.UTC)
            else:
                data.index = idx.tz_convert(pytz.UTC)

        # Resample in UTC before local timezone conversion (Issue 8)
        if self._resample_rule:
            data = self._resample_if_needed(data, now_utc=now_utc)
            # The resampled index is a fresh UTC-anchored grid, so the raw days captured
            # above are no longer positionally aligned with `data.index` — indexing them
            # returned a calendar day from the first hours of the download window and made
            # the closed_only filtering below a no-op. Clearing them lets every branch fall
            # back to its own `ts`-derived date, which is correct for a UTC-anchored grid.
            orig_dates = None

        # Universal closed-only filtering in UTC / market session time before user timezone conversion
        if self.closed_only and not data.empty and isinstance(data.index, pd.DatetimeIndex):
            now = now_utc if now_utc is not None else pd.Timestamp.now(tz=pytz.UTC)
            if now.tz is None:
                now = now.tz_localize(pytz.UTC)

            if self.interval in ("1mo", "3mo"):
                num_months = 1 if self.interval == "1mo" else 3
                asset_class = classify_asset(self.ticker, self.asset_type)
                clean_sym = self.ticker.strip().upper()
                candle_end_list = []
                for i, ts in enumerate(data.index):
                    d = (
                        orig_dates[i]
                        if orig_dates is not None and i < len(orig_dates)
                        else (ts.date() if ts.tz is None else ts.tz_localize(None).date())
                    )
                    start_m = pd.Timestamp(year=d.year, month=d.month, day=1)
                    next_m = (start_m + pd.DateOffset(months=num_months)).date()
                    last_cal_day = next_m - datetime.timedelta(days=1)

                    if asset_class == "crypto":
                        close_ts = pd.Timestamp(next_m, tz=pytz.UTC)
                        if ts.tz is None:
                            close_ts = close_ts.tz_localize(None)
                    elif asset_class == "forex":
                        curr = last_cal_day
                        while curr.weekday() >= 5:  # Sat, Sun
                            curr -= datetime.timedelta(days=1)
                        close_ts = pd.Timestamp(
                            year=curr.year,
                            month=curr.month,
                            day=curr.day,
                            hour=17,
                            minute=0,
                            tz="America/New_York",
                        ).tz_convert(pytz.UTC)
                        if ts.tz is None:
                            close_ts = close_ts.tz_localize(None)
                    else:
                        # Stock / Commodity / Index
                        curr = last_cal_day
                        is_israel = clean_sym.endswith(".TA")
                        while (curr.weekday() in (4, 5)) if is_israel else (curr.weekday() >= 5):
                            curr -= datetime.timedelta(days=1)
                        last_session = curr
                        tz_name, _, close_t = _get_market_session_hours(clean_sym, last_session)
                        s_close = pd.Timestamp(
                            year=last_session.year,
                            month=last_session.month,
                            day=last_session.day,
                            hour=close_t.hour,
                            minute=close_t.minute,
                            tz=tz_name,
                        )
                        if ts.tz is not None:
                            close_ts = s_close.tz_convert(ts.tz)
                        else:
                            close_ts = s_close.tz_localize(None)

                    candle_end_list.append(close_ts)
                candle_ends = pd.DatetimeIndex(candle_end_list)
            elif self.interval in ("1d", "1D"):
                # Session-aware daily candle close
                asset_class = classify_asset(self.ticker, self.asset_type)
                clean_sym = self.ticker.strip().upper()

                candle_end_list = []
                for i, ts in enumerate(data.index):
                    if asset_class == "crypto":
                        # 24/7 calendar: closes at next day 00:00 UTC
                        d = ts.date()
                        close_ts = pd.Timestamp(d, tz=pytz.UTC) + pd.Timedelta(days=1)
                    elif asset_class == "forex":
                        # Forex daily rollover: 17:00 America/New_York
                        d = (
                            orig_dates[i]
                            if orig_dates is not None and i < len(orig_dates)
                            else ts.date()
                        )
                        close_ts = pd.Timestamp(
                            year=d.year,
                            month=d.month,
                            day=d.day,
                            hour=17,
                            minute=0,
                            tz="America/New_York",
                        ).tz_convert(pytz.UTC)
                    else:
                        # Stock / Commodity / Index
                        # Asian exchanges and international markets: determine session date in local exchange timezone
                        tz_name, _open_t, _ = _get_market_session_hours(clean_sym, ts.date())
                        ts_local = ts.tz_convert(tz_name) if ts.tz is not None else ts
                        d = ts_local.date()
                        if orig_dates is not None and i < len(orig_dates):
                            d = orig_dates[i]
                        tz_name, _open_t, close_t = _get_market_session_hours(clean_sym, d)
                        close_ts = pd.Timestamp(
                            year=d.year,
                            month=d.month,
                            day=d.day,
                            hour=close_t.hour,
                            minute=close_t.minute,
                            tz=tz_name,
                        ).tz_convert(pytz.UTC)

                    candle_end_list.append(close_ts)

                candle_ends = pd.DatetimeIndex(candle_end_list)
            elif self.interval in INTERVAL_DELTAS:
                delta = INTERVAL_DELTAS[self.interval]
                asset_class = classify_asset(self.ticker, self.asset_type)
                if asset_class not in ("crypto", "forex"):
                    clean_sym = self.ticker.strip().upper()
                    capped_ends = []
                    for ts in data.index:
                        tz_name, _, _ = _get_market_session_hours(clean_sym, ts.date())
                        ts_loc = (
                            ts.tz_convert(tz_name)
                            if ts.tz is not None
                            else ts.tz_localize("UTC").tz_convert(tz_name)
                        )
                        d = ts_loc.date()
                        if self.interval == "1wk":
                            if clean_sym.endswith(".TA"):
                                days_ahead = (
                                    (3 - d.weekday()) if d.weekday() <= 3 else (3 - d.weekday() + 7)
                                )
                            else:
                                days_ahead = (
                                    (4 - d.weekday()) if d.weekday() <= 4 else (4 - d.weekday() + 7)
                                )
                            end_d = d + datetime.timedelta(days=days_ahead)
                        elif self.interval == "5d":
                            curr = d
                            days_added = 0
                            is_israel = clean_sym.endswith(".TA")
                            while days_added < 4:
                                curr += datetime.timedelta(days=1)
                                if is_israel:
                                    if curr.weekday() not in (4, 5):
                                        days_added += 1
                                else:
                                    if curr.weekday() < 5:
                                        days_added += 1
                            end_d = curr
                        else:
                            end_d = d

                        tz_name, _, close_t = _get_market_session_hours(clean_sym, end_d)
                        s_close = pd.Timestamp(
                            year=end_d.year,
                            month=end_d.month,
                            day=end_d.day,
                            hour=close_t.hour,
                            minute=close_t.minute,
                            tz=tz_name,
                        )
                        if ts.tz is not None:
                            s_close_cmp = s_close.tz_convert(ts.tz)
                        else:
                            s_close_cmp = s_close.tz_localize(None)

                        if self.interval in ("1wk", "5d"):
                            capped_ends.append(s_close_cmp)
                        else:
                            raw_end = ts + delta
                            capped_ends.append(
                                min(raw_end, s_close_cmp) if ts <= s_close_cmp else raw_end
                            )
                    candle_ends = pd.DatetimeIndex(capped_ends)
                else:
                    candle_ends = data.index + delta
            else:
                raise ValueError(f"Unsupported interval for closed_only filtering: {self.interval}")

            if candle_ends.tz is not None and now.tz is not None:
                now_cmp = now.tz_convert(candle_ends.tz)
            elif candle_ends.tz is not None and now.tz is None:
                now_cmp = now.tz_localize(pytz.UTC).tz_convert(candle_ends.tz)
            elif candle_ends.tz is None and now.tz is not None:
                now_cmp = now.tz_localize(None)
            else:
                now_cmp = now
            data = data.loc[candle_ends <= now_cmp]

        # Convert to target user timezone after closed-only filtering
        if isinstance(data.index, pd.DatetimeIndex):
            data.index = data.index.tz_convert(self.timezone)

        # Store metadata in DataFrame attributes
        data.attrs["symbol"] = self.symbol
        data.attrs["ticker"] = self.ticker
        data.attrs["auto_adjust"] = self.auto_adjust

        return data

    def get_data(self) -> pd.DataFrame:
        """Full pipeline: fetch raw OHLCV and process with validation and resampling."""
        return self.process(self.fetch())

    def load_data(self) -> pd.DataFrame:
        """Alias for get_data to ensure CLI and script compatibility."""
        return self.get_data()
