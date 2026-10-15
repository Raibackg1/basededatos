"""Tests for v0.3.15 audit fixes (v10).

Covers:
1. ISO 4217 Currency Normalization (including TWD, ARS, PEN, PKR, VND, etc. returning <PAIR>=X).
2. asset_type='forex' restriction against crypto pairs (BTC/USD, ETH-USD, BTCUSDT).
3. Empty/whitespace symbol validation (preventing '=X' generation and raising ValueError).
4. asset_type='stock' restriction against forex currency pairs and =X tickers.
5. Disallowing identical base and quote currencies (USDUSD, USD/USD, USD-USD, USDUSD=X, EUREUR).
"""

import pytest

from yfinance_ta_patterns.data import MarketDataLoader, normalize_ticker, resolve_asset_currencies

# ==============================================================================
# 1. ISO 4217 Currency Normalization (TWD and expanded fiat currencies)
# ==============================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EUR/TWD", "EURTWD=X"),
        ("USD/TWD", "USDTWD=X"),
        ("GBP/TWD", "GBPTWD=X"),
        ("USDTWD", "USDTWD=X"),
        ("EUR/ARS", "EURARS=X"),
        ("USD/PEN", "USDPEN=X"),
        ("EUR/PKR", "EURPKR=X"),
        ("USD/VND", "USDVND=X"),
        ("EUR/EGP", "EUREGP=X"),
        ("USD/KWD", "USDKWD=X"),
        ("USD/QAR", "USDQAR=X"),
        ("USD/OMR", "USDOMR=X"),
        ("USD/BHD", "USDBHD=X"),
        ("USD/JOD", "USDJOD=X"),
        ("USD/UAH", "USDUAH=X"),
        ("USD/KZT", "USDKZT=X"),
        ("EUR/MAD", "EURMAD=X"),
        ("EUR/BGN", "EURBGN=X"),
        ("EUR/RSD", "EURRSD=X"),
        ("USD/ISK", "USDISK=X"),
    ],
)
def test_expanded_iso4217_currencies_normalization(raw: str, expected: str) -> None:
    """Issue 1: TWD and expanded ISO 4217 currencies correctly normalize to =X forex pairs."""
    assert normalize_ticker(raw, asset_type="auto") == expected
    assert normalize_ticker(raw, asset_type="forex") == expected


# ==============================================================================
# 2. Crypto Pair in Forex Mode Rejection
# ==============================================================================


@pytest.mark.parametrize(
    "crypto_sym",
    [
        "BTC/USD",
        "ETH/USD",
        "BTC-USD",
        "ETH-USD",
        "SOL/USD",
        "BTCUSDT",
        "BTCUSDC",
        "BTCUSD=X",
    ],
)
def test_crypto_pairs_rejected_in_forex_mode(crypto_sym: str) -> None:
    """Issue 2: Crypto pairs bypass forex mode is prevented; raises ValueError."""
    with pytest.raises(ValueError, match="Cannot normalize crypto pair"):
        normalize_ticker(crypto_sym, asset_type="forex")


# ==============================================================================
# 3. Empty / Whitespace Symbol Validation
# ==============================================================================


@pytest.mark.parametrize("empty_sym", ["", "   ", "\t", "\n"])
def test_empty_or_whitespace_symbol_rejected(empty_sym: str) -> None:
    """Issue 3: Empty or whitespace-only symbol raises ValueError rather than returning '=X' or ''."""
    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        normalize_ticker(empty_sym, asset_type="forex")

    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        normalize_ticker(empty_sym, asset_type="auto")

    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        normalize_ticker(empty_sym, asset_type="stock")

    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        normalize_ticker(empty_sym, asset_type="crypto")

    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        resolve_asset_currencies(empty_sym)

    with pytest.raises(ValueError, match="Symbol cannot be empty"):
        MarketDataLoader(empty_sym)


# ==============================================================================
# 4. Forex Pairs in Stock Mode Rejection
# ==============================================================================


@pytest.mark.parametrize(
    "forex_or_crypto_sym",
    [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        "EURUSD=X",
        "USDJPY=X",
        "EURUSD",
        "BTC/USD",
    ],
)
def test_forex_and_crypto_rejected_in_stock_mode(forex_or_crypto_sym: str) -> None:
    """Issue 4: Stock mode does not apply forex rules; raises ValueError for currency/crypto pairs."""
    with pytest.raises(ValueError, match="Cannot normalize"):
        normalize_ticker(forex_or_crypto_sym, asset_type="stock")


def test_valid_stocks_preserved_in_stock_mode() -> None:
    """Issue 4: Valid stock symbols (including share classes like BRK-B and BRK/B) work properly."""
    assert normalize_ticker("AAPL", asset_type="stock") == "AAPL"
    assert normalize_ticker("NVDA", asset_type="stock") == "NVDA"
    assert normalize_ticker("BRK-B", asset_type="stock") == "BRK-B"
    assert normalize_ticker("BRK/B", asset_type="stock") == "BRK-B"
    assert normalize_ticker("BF/B", asset_type="stock") == "BF-B"
    assert normalize_ticker("SAP.DE", asset_type="stock") == "SAP.DE"
    assert normalize_ticker("VOD.L", asset_type="stock") == "VOD.L"


# ==============================================================================
# 5. Identical Base and Quote Currencies Rejection
# ==============================================================================


@pytest.mark.parametrize(
    "identical_pair",
    [
        "USDUSD",
        "USD/USD",
        "USD-USD",
        "USDUSD=X",
        "EUREUR",
        "EUR/EUR",
        "GBPJPY=X".replace("JPY", "GBP"),
    ],
)
def test_identical_currencies_rejected(identical_pair: str) -> None:
    """Issue 5: Base and quote currencies cannot be identical; raises ValueError."""
    with pytest.raises(ValueError, match="Base and quote currencies cannot be identical"):
        normalize_ticker(identical_pair, asset_type="auto")

    with pytest.raises(ValueError, match="Base and quote currencies cannot be identical"):
        normalize_ticker(identical_pair, asset_type="forex")

    with pytest.raises(ValueError, match="Base and quote currencies cannot be identical"):
        resolve_asset_currencies(identical_pair)


def test_identical_crypto_symbols_rejected() -> None:
    """Issue 5: Identical crypto symbols in pair raise ValueError."""
    with pytest.raises(ValueError, match="Base and quote symbols cannot be identical"):
        normalize_ticker("BTC/BTC", asset_type="crypto")

    with pytest.raises(ValueError, match="Base and quote symbols cannot be identical"):
        normalize_ticker("ETH-ETH", asset_type="crypto")
