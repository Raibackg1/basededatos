"""Economic calendar integration for a selected asset with multi-source resiliency and fallback."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import re
import time
import urllib.request
import warnings
from collections.abc import Callable, Iterable, Mapping
from html.parser import HTMLParser
from typing import Any, ClassVar

from .data import _CURRENCY_CODES, normalize_ticker, resolve_asset_currencies

logger = logging.getLogger(__name__)

# Module-level TTL cache for economic calendar feeds: key -> (timestamp, html_content)
_CALENDAR_CACHE: dict[str, tuple[float, str | None]] = {}
_CACHE_TTL_SUCCESS: float = 900.0  # 15 minutes
_CACHE_TTL_FAILURE: float = 60.0   # 1 minute negative caching
# Timeout used by the attempt that produced a negative-cache entry: a caller with a LONGER
# timeout must retry instead of inheriting a failure caused by someone else's short probe.
_CALENDAR_FAIL_TIMEOUT: dict[str, float] = {}

curl_requests: Any = None
try:
    from curl_cffi import requests as _curl_requests

    curl_requests = _curl_requests
except ImportError:
    curl_requests = None

httpx: Any = None
try:
    import httpx as _httpx

    httpx = _httpx
except ImportError:
    httpx = None


class _CellNode:
    """Lightweight representation of a parsed HTML <td> element."""

    __slots__ = ("attrs", "icon_classes", "link_text_parts", "span_titles", "text_parts")

    def __init__(self, attrs: dict[str, str]) -> None:
        self.attrs = attrs
        self.text_parts: list[str] = []
        self.link_text_parts: list[str] = []
        self.span_titles: list[str] = []
        self.icon_classes: list[str] = []

    @property
    def text(self) -> str:
        raw = "".join(self.text_parts).replace("\ufffd", "").replace("\u00ae", "")
        return " ".join(raw.split())

    @property
    def link_text(self) -> str:
        raw = "".join(self.link_text_parts).replace("\ufffd", "").replace("\u00ae", "")
        return " ".join(raw.split())


class _RowNode:
    """Lightweight representation of a parsed HTML <tr> element."""

    __slots__ = ("attrs", "classes", "tds")

    def __init__(self, attrs: dict[str, str]) -> None:
        self.attrs = attrs
        self.classes: list[str] = []
        if attrs.get("class"):
            self.classes.append(attrs["class"])
        self.tds: list[_CellNode] = []


class _TableHTMLParser(HTMLParser):
    """Zero-dependency stdlib HTML parser for economic calendar table rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_RowNode] = []
        self._current_row: _RowNode | None = None
        self._current_cell: _CellNode | None = None
        self._tr_depth: int = 0
        self._td_depth: int = 0
        self._in_a: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict: dict[str, str] = {k.lower(): (v or "") for k, v in attrs}
        tag_lower = tag.lower()

        if tag_lower == "tr":
            if self._current_row is None:
                self._current_row = _RowNode(attr_dict)
                self._current_cell = None
                self._tr_depth = 1
                self._td_depth = 0
                self._in_a = False
            else:
                self._tr_depth += 1
                cls_val = attr_dict.get("class", "")
                if cls_val:
                    self._current_row.classes.append(cls_val)
            return

        if self._current_row is None:
            return

        cls_val = attr_dict.get("class", "")
        if cls_val:
            self._current_row.classes.append(cls_val)

        if tag_lower in ("td", "th"):
            if self._current_cell is None:
                self._current_cell = _CellNode(attr_dict)
                self._td_depth = 1
                self._in_a = False
            else:
                self._td_depth += 1
            return

        if self._current_cell is not None:
            if tag_lower == "a":
                self._in_a = True
            elif tag_lower == "span":
                if "ceFlags" in cls_val and attr_dict.get("title"):
                    self._current_cell.span_titles.append(attr_dict["title"].strip())
            elif tag_lower == "i" and cls_val:
                self._current_cell.icon_classes.append(cls_val)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "a":
            self._in_a = False
        elif tag_lower in ("td", "th") and self._current_cell is not None:
            self._td_depth -= 1
            if self._td_depth <= 0:
                if self._current_row is not None:
                    self._current_row.tds.append(self._current_cell)
                self._current_cell = None
                self._td_depth = 0
                self._in_a = False
        elif tag_lower == "tr" and self._current_row is not None:
            self._tr_depth -= 1
            if self._tr_depth <= 0:
                self.rows.append(self._current_row)
                self._current_row = None
                self._current_cell = None
                self._tr_depth = 0
                self._td_depth = 0
                self._in_a = False

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.text_parts.append(data)
            if self._in_a:
                self._current_cell.link_text_parts.append(data)


_INDEX_CURRENCY_MAP: dict[str, str] = {
    "^GSPC": "USD",
    "^DJI": "USD",
    "^IXIC": "USD",
    "^RUT": "USD",
    "^VIX": "USD",
    "^FTSE": "GBP",
    "^GDAXI": "EUR",
    "^FCHI": "EUR",
    "^STOXX50E": "EUR",
    "^N225": "JPY",
    "^HSI": "HKD",
    "^AXJO": "AUD",
    "^GSPTSE": "CAD",
    "^SSMI": "CHF",
}

_CURRENCY_ALIASES: dict[str, str] = {
    "GBP": "GBP",
    "GBX": "GBP",
    "ZAC": "ZAR",
    "ILA": "ILS",
    "USDT": "USD",
    "USDC": "USD",
    "BUSD": "USD",
    "FDUSD": "USD",
    "CNH": "CNY",
}

_IMPORTANCE_ALIASES: dict[str, str] = {
    "1": "1",
    "2": "2",
    "3": "3",
    "low": "1",
    "medium": "2",
    "med": "2",
    "moderate": "2",
    "high": "3",
}


def normalize_importance_filter(importances: Iterable[Any] | str | None) -> list[str] | None:
    """Normalize importance filter values ('1', '2', '3' or 'low', 'medium', 'high')."""
    if importances is None:
        return None
    raw_items: list[str] = []
    if isinstance(importances, str):
        raw_items = [s.strip() for s in re.split(r"[,;\s]+", importances) if s.strip()]
    else:
        for item in importances:
            for part in re.split(r"[,;\s]+", str(item).strip()):
                if part:
                    raw_items.append(part)
    if not raw_items:
        return None

    normalized: list[str] = []
    for item in raw_items:
        mapped = _IMPORTANCE_ALIASES.get(item.lower())
        if mapped is None:
            raise ValueError(
                f"Invalid importance value {item!r}. Allowed values: '1' (low), '2' (medium), '3' (high)."
            )
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


def resolve_symbol_currencies(symbol: str, asset_type: str = "auto") -> list[str]:
    """Resolve the macroeconomic currency codes that impact a given asset symbol.

    Examples:
        'EURUSD=X' / 'EUR/USD' / 'EURUSD' -> ['EUR', 'USD']
        'GBPJPY' -> ['GBP', 'JPY']
        'AAPL' / 'NVDA' / 'GC=F' -> ['USD']
        'SAP.DE' / '^GDAXI' -> ['EUR']
        'VOD.L' / '^FTSE' -> ['GBP']
        '7203.T' / '^N225' -> ['JPY']
        'BTC-USD' / 'BTCUSDT' -> ['USD']
        'BTC-EUR' -> ['EUR']
    """
    if not symbol or not symbol.strip():
        raise ValueError("Parameter 'symbol' is required and cannot be empty.")

    clean = symbol.strip().upper()

    # Direct index lookup
    if clean in _INDEX_CURRENCY_MAP:
        return [_INDEX_CURRENCY_MAP[clean]]

    # Direct 3-letter currency code or 2-letter ISO country code
    aliased_direct = _CURRENCY_ALIASES.get(clean, clean)
    if aliased_direct in _CURRENCY_CODES:
        return [aliased_direct]
    if clean in InvestingCalendar.ISO_TO_CURRENCY:
        return [InvestingCalendar.ISO_TO_CURRENCY[clean]]
    lower_clean = symbol.strip().lower()
    if lower_clean in InvestingCalendar.COUNTRY_MAP:
        return [InvestingCalendar.COUNTRY_MAP[lower_clean][1]]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        base, quote = resolve_asset_currencies(symbol, asset_type=asset_type)

    currencies: list[str] = []
    for candidate in (base, quote):
        norm_cand = _CURRENCY_ALIASES.get(candidate.strip().upper(), candidate.strip().upper())
        if (
            norm_cand in _CURRENCY_CODES or norm_cand in InvestingCalendar.ISO_TO_CURRENCY.values()
        ) and norm_cand not in currencies:
            currencies.append(norm_cand)

    if not currencies:
        currencies.append("USD")

    return currencies


class InvestingCalendar:
    """
    Economic calendar client supporting real-time macroeconomic event feeds.

    Live events are retrieved via CloudFront mirror (Trading Economics calendar in UTC).
    Dates are client-side filtered against active upcoming events.
    Legacy Investing.com endpoints (PAGE_URL/API_URL) and `parse_events_html` are
    preserved for backward compatibility with offline exports and existing tests.
    """

    __slots__ = ("_client", "_curl_session", "_headers", "_http_fetcher", "_timeout")

    PAGE_URL = "https://ru.investing.com/economic-calendar/"
    API_URL = "https://ru.investing.com/economic-calendar/Service/getCalendarFilteredData"
    TE_URL = "https://d3fy651gv2fhd3.cloudfront.net/calendar/"

    COUNTRY_MAP: ClassVar[dict[str, tuple[str, str]]] = {
        "united states": ("United States", "USD"),
        "usa": ("United States", "USD"),
        "us": ("United States", "USD"),
        "сша": ("United States", "USD"),
        "euro area": ("Eurozone", "EUR"),
        "eurozone": ("Eurozone", "EUR"),
        "european union": ("Eurozone", "EUR"),
        "еврозона": ("Eurozone", "EUR"),
        "евросоюз": ("Eurozone", "EUR"),
        "germany": ("Germany", "EUR"),
        "германия": ("Germany", "EUR"),
        "france": ("France", "EUR"),
        "франция": ("France", "EUR"),
        "italy": ("Italy", "EUR"),
        "италия": ("Italy", "EUR"),
        "spain": ("Spain", "EUR"),
        "испания": ("Spain", "EUR"),
        "united kingdom": ("United Kingdom", "GBP"),
        "uk": ("United Kingdom", "GBP"),
        "великобритания": ("United Kingdom", "GBP"),
        "japan": ("Japan", "JPY"),
        "япония": ("Japan", "JPY"),
        "china": ("China", "CNY"),
        "китай": ("China", "CNY"),
        "canada": ("Canada", "CAD"),
        "канада": ("Canada", "CAD"),
        "australia": ("Australia", "AUD"),
        "австралия": ("Australia", "AUD"),
        "new zealand": ("New Zealand", "NZD"),
        "новая зеландия": ("New Zealand", "NZD"),
        "switzerland": ("Switzerland", "CHF"),
        "швейцария": ("Switzerland", "CHF"),
        "russia": ("Russia", "RUB"),
        "россия": ("Russia", "RUB"),
        "brazil": ("Brazil", "BRL"),
        "бразилия": ("Brazil", "BRL"),
        "mexico": ("Mexico", "MXN"),
        "мексика": ("Mexico", "MXN"),
        "india": ("India", "INR"),
        "индия": ("India", "INR"),
        "south korea": ("South Korea", "KRW"),
        "южная корея": ("South Korea", "KRW"),
        "south africa": ("South Africa", "ZAR"),
        "юар": ("South Africa", "ZAR"),
        "turkey": ("Turkey", "TRY"),
        "турция": ("Turkey", "TRY"),
        "saudi arabia": ("Saudi Arabia", "SAR"),
        "саудовская аравия": ("Saudi Arabia", "SAR"),
        "singapore": ("Singapore", "SGD"),
        "сингапур": ("Singapore", "SGD"),
        "indonesia": ("Indonesia", "IDR"),
        "индонезия": ("Indonesia", "IDR"),
        "argentina": ("Argentina", "ARS"),
        "аргентина": ("Argentina", "ARS"),
        "sweden": ("Sweden", "SEK"),
        "швеция": ("Sweden", "SEK"),
        "hong kong": ("Hong Kong", "HKD"),
        "гонконг": ("Hong Kong", "HKD"),
        "norway": ("Norway", "NOK"),
        "норвегия": ("Norway", "NOK"),
        "denmark": ("Denmark", "DKK"),
        "дания": ("Denmark", "DKK"),
        "poland": ("Poland", "PLN"),
        "польша": ("Poland", "PLN"),
        "czech republic": ("Czech Republic", "CZK"),
        "czechia": ("Czech Republic", "CZK"),
        "чехия": ("Czech Republic", "CZK"),
        "hungary": ("Hungary", "HUF"),
        "венгрия": ("Hungary", "HUF"),
        "israel": ("Israel", "ILS"),
        "израиль": ("Israel", "ILS"),
        "taiwan": ("Taiwan", "TWD"),
        "тайвань": ("Taiwan", "TWD"),
        "thailand": ("Thailand", "THB"),
        "таиланд": ("Thailand", "THB"),
        "malaysia": ("Malaysia", "MYR"),
        "малайзия": ("Malaysia", "MYR"),
        "philippines": ("Philippines", "PHP"),
        "филиппины": ("Philippines", "PHP"),
        "chile": ("Chile", "CLP"),
        "чили": ("Chile", "CLP"),
        "colombia": ("Colombia", "COP"),
        "колумбия": ("Colombia", "COP"),
        "united arab emirates": ("United Arab Emirates", "AED"),
        "uae": ("United Arab Emirates", "AED"),
        "оаэ": ("United Arab Emirates", "AED"),
        "opec": ("OPEC", "USD"),
        "опек": ("OPEC", "USD"),
        "world": ("World", "USD"),
        "мир": ("World", "USD"),
    }

    ISO_TO_CURRENCY: ClassVar[dict[str, str]] = {
        "US": "USD",
        "DE": "EUR",
        "FR": "EUR",
        "IT": "EUR",
        "ES": "EUR",
        "EU": "EUR",
        "GB": "GBP",
        "JP": "JPY",
        "CN": "CNY",
        "CA": "CAD",
        "AU": "AUD",
        "NZ": "NZD",
        "CH": "CHF",
        "RU": "RUB",
        "BR": "BRL",
        "MX": "MXN",
        "IN": "INR",
        "KR": "KRW",
        "ZA": "ZAR",
        "TR": "TRY",
        "SA": "SAR",
        "SG": "SGD",
        "ID": "IDR",
        "AR": "ARS",
        "SE": "SEK",
        "HK": "HKD",
        "NO": "NOK",
        "DK": "DKK",
        "PL": "PLN",
        "CZ": "CZK",
        "HU": "HUF",
        "IL": "ILS",
        "TW": "TWD",
        "TH": "THB",
        "MY": "MYR",
        "PH": "PHP",
        "CL": "CLP",
        "CO": "COP",
        "AE": "AED",
    }

    def __init__(
        self,
        timeout: float = 3.5,
        headers: Mapping[str, str] | None = None,
        http_fetcher: Callable[[str], str | None] | None = None,
    ) -> None:
        self._timeout = timeout
        self._http_fetcher = http_fetcher
        base_headers: dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "ru,en;q=0.9",
        }
        if headers:
            base_headers.update(headers)
        self._headers = base_headers

        self._client: Any = None
        if http_fetcher is None and httpx is not None:
            try:
                self._client = httpx.Client(
                    headers=base_headers,
                    timeout=timeout,
                    follow_redirects=True,
                )
            except Exception:
                self._client = None

        self._curl_session: Any = None
        if http_fetcher is None and curl_requests is not None:
            try:
                self._curl_session = curl_requests.Session(impersonate="chrome124")
            except Exception:
                self._curl_session = None

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
        if self._curl_session is not None:
            with contextlib.suppress(Exception):
                self._curl_session.close()

    def __enter__(self) -> InvestingCalendar:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    @staticmethod
    def _date_str(d: Any) -> str:
        if isinstance(d, str):
            return d.strip()
        if isinstance(d, dt.datetime):
            return d.date().isoformat()
        if isinstance(d, dt.date):
            return d.isoformat()
        return str(d)

    @staticmethod
    def _extract_id(tr: _RowNode) -> str:
        if tr.attrs.get("data-event-id"):
            return tr.attrs["data-event-id"].strip()
        for td in tr.tds:
            if td.attrs.get("data-event-id"):
                return td.attrs["data-event-id"].strip()
        m = re.search(r"eventRowId_(\d+)", tr.attrs.get("id", ""))
        return m.group(1) if m else ""

    @classmethod
    def _row_to_record(
        cls,
        tr: _RowNode,
        current_date: str | None = None,
    ) -> dict[str, Any]:
        eid = cls._extract_id(tr)
        time_text = ""
        currency = ""
        country = ""
        importance = "1"
        event = ""
        actual = "—"
        forecast = "—"
        previous = "—"

        for td in tr.tds:
            td_cls = td.attrs.get("class", "")
            if "time" in td_cls and not time_text:
                time_text = td.text
            elif "flagCur" in td_cls:
                full_text = td.text
                currency = full_text.split()[-1] if full_text else ""
                country = td.span_titles[0] if td.span_titles else ""
            elif "sentiment" in td_cls:
                bull_icons = [c for c in td.icon_classes if "grayFullBullishIcon" in c]
                if bull_icons:
                    importance = str(len(bull_icons))
            elif "event" in td_cls:
                event = td.link_text or td.text
            elif "act" in td_cls or "actual" in td_cls:
                actual = td.text or "—"
            elif "fore" in td_cls or "forecast" in td_cls:
                forecast = td.text or "—"
            elif "prev" in td_cls or "previous" in td_cls:
                previous = td.text or "—"

        if time_text and current_date:
            time_formatted = f"{time_text}:00" if ":" in time_text else time_text
            full_datetime = f"{current_date} {time_formatted} UTC"
        elif time_text:
            full_datetime = f"{time_text} UTC"
        else:
            full_datetime = f"{current_date} UTC" if current_date else ""

        return {
            "id": eid,
            "time": full_datetime,
            "country": country,
            "currency": currency,
            "importance": importance,
            "event": event,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
        }

    @classmethod
    def parse_events_html(cls, html_snippet: str) -> Iterable[Mapping[str, Any]]:
        """Parse Investing.com economic calendar HTML snippet into event records."""
        parser = _TableHTMLParser()
        parser.feed(html_snippet or "")
        current_date: str | None = None

        month_names = {
            "января": "01",
            "февраля": "02",
            "марта": "03",
            "апреля": "04",
            "мая": "05",
            "июня": "06",
            "июля": "07",
            "августа": "08",
            "сентября": "09",
            "октября": "10",
            "ноября": "11",
            "декабря": "12",
            "january": "01",
            "february": "02",
            "march": "03",
            "april": "04",
            "may": "05",
            "june": "06",
            "july": "07",
            "august": "08",
            "september": "09",
            "october": "10",
            "november": "11",
            "december": "12",
        }

        for tr in parser.rows:
            try:
                date_cells = [
                    td
                    for td in tr.tds
                    if "theDay" in td.attrs.get("class", "") or "date" in td.attrs.get("class", "")
                ]
                if not date_cells and (
                    "theDay" in tr.attrs.get("class", "") or "theDay" in tr.attrs.get("id", "")
                ):
                    date_cells = tr.tds[:1]

                if date_cells:
                    date_text = date_cells[0].text
                    if date_text:
                        date_match = re.search(
                            r"(\d{1,2})[\s\.](\d{1,2}|[a-zA-Zа-яА-ЯёЁ]+)[\s\.,]*(\d{2,4})?",
                            date_text,
                        )
                        if date_match:
                            day = date_match.group(1).zfill(2)
                            month_or_name = date_match.group(2)
                            year = date_match.group(3) or str(dt.datetime.now().year)
                            if len(year) == 2:
                                year = f"20{year}"
                            month = month_names.get(month_or_name.lower(), month_or_name.zfill(2))
                            current_date = f"{year}-{month}-{day}"
                    continue

                tr_id = tr.attrs.get("id", "")
                if tr_id and "eventRowId_" in tr_id:
                    record = cls._row_to_record(tr, current_date)
                    if record["event"] and str(record["event"]).strip():
                        yield record
            except Exception:
                continue

    def _download_feed_html(self, url: str) -> str | None:
        """Download raw HTML from calendar feed using caching, injectable fetcher or fast-fail client."""
        if self._http_fetcher is not None:
            return self._http_fetcher(url)

        now = time.monotonic()
        if url in _CALENDAR_CACHE:
            cached_at, cached_html = _CALENDAR_CACHE[url]
            ttl = _CACHE_TTL_SUCCESS if cached_html is not None else _CACHE_TTL_FAILURE
            retry_with_longer_timeout = (
                cached_html is None
                and self._timeout > _CALENDAR_FAIL_TIMEOUT.get(url, self._timeout)
            )
            if now - cached_at < ttl and not retry_with_longer_timeout:
                return cached_html

        content: str | None = None
        # Try primary fast client (curl_cffi impersonate if available)
        if self._curl_session is not None:
            with contextlib.suppress(Exception):
                r = self._curl_session.get(url, timeout=self._timeout)
                if r.status_code == 200 and len(r.text) > 500:
                    content = str(r.text)

        # Fallback 1: httpx if curl was not available or failed
        if content is None and self._client is not None:
            try:
                r = self._client.get(url)
                if r.status_code == 200 and len(r.text) > 500:
                    content = str(r.text)
            except Exception as exc:
                logger.debug("Error requesting live calendar feed via httpx: %s", exc)

        # Fallback 2: urllib if still None and curl/httpx not available
        if content is None and self._curl_session is None and self._client is None:
            try:
                req = urllib.request.Request(url, headers=self._headers)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                    if getattr(resp, "status", 200) == 200:
                        raw_bytes = bytes(resp.read())
                        text = raw_bytes.decode("utf-8", errors="replace")
                        if len(text) > 500:
                            content = str(text)
            except Exception as exc:
                logger.debug("Error requesting live calendar feed via urllib: %s", exc)

        _CALENDAR_CACHE[url] = (now, content)
        if content is None:
            _CALENDAR_FAIL_TIMEOUT[url] = self._timeout
        return content

    def _fetch_from_feed(
        self,
        date_from: str,
        date_to: str,
        countries: Iterable[Any] | None = None,
        importances: Iterable[Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        """Fetch economic events from the primary live feed."""
        html_text = self._download_feed_html(self.TE_URL)
        if html_text is None:
            return [], "unavailable", (
                "Live macroeconomic calendar feed is currently unreachable; "
                "no unverified synthetic data generated."
            )

        parser = _TableHTMLParser()
        parser.feed(html_text)
        rows = [r for r in parser.rows if r.attrs.get("data-id")]
        if not rows:
            logger.warning("No calendar rows with 'data-id' found in feed HTML (anti-bot challenge or layout changed).")
            return [], "unavailable", (
                "Calendar feed page received but no economic event rows could be parsed "
                "(anti-bot challenge, captcha, or changed layout); treated as unavailable."
            )

        events: list[dict[str, Any]] = []

        imp_filter = set(map(str, importances)) if importances else None
        country_filter: set[str] | None = None
        if countries:
            country_filter = set()
            for c in countries:
                c_clean = str(c).lower().strip()
                if c_clean:
                    country_filter.add(c_clean)
                    if c_clean in self.COUNTRY_MAP:
                        c_name, c_curr = self.COUNTRY_MAP[c_clean]
                        country_filter.add(c_name.lower())
                        country_filter.add(c_curr.lower())

        for tr in rows:
            tds = tr.tds
            if len(tds) < 5:
                continue

            c0_class = tds[0].attrs.get("class", "")
            date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", c0_class)
            if not date_match:
                continue

            event_date = date_match.group(1)
            if date_from and event_date < date_from:
                continue
            if date_to and event_date > date_to:
                continue

            raw_time = tds[0].text
            if raw_time:
                try:
                    if re.search(r"(?:AM|PM)", raw_time, re.I):
                        time_part = dt.datetime.strptime(
                            raw_time.upper().strip(), "%I:%M %p"
                        ).strftime("%H:%M")
                    else:
                        time_part = raw_time
                except Exception:
                    time_part = raw_time
            else:
                time_part = ""

            full_time = f"{event_date} {time_part} UTC" if time_part else f"{event_date} UTC"

            raw_country = (tr.attrs.get("data-country") or "").lower().strip()
            iso_code = tds[1].text.strip()
            mapped = self.COUNTRY_MAP.get(raw_country)
            if mapped:
                country_name, currency = mapped
            else:
                country_name = raw_country.title() if raw_country else iso_code
                currency = self.ISO_TO_CURRENCY.get(iso_code, iso_code)

            if country_filter:
                country_candidates = {
                    country_name.lower(),
                    currency.lower(),
                    raw_country,
                    iso_code.lower(),
                }
                if not (country_candidates & country_filter):
                    continue

            raw_event_name = tds[2].text or (tr.attrs.get("data-event") or "").title()
            event_name = raw_event_name.strip()
            if len(event_name) > 120:
                event_name = event_name[:117] + "..."
            if len(country_name) > 60:
                country_name = country_name[:57] + "..."

            actual = (tds[3].text or "—").strip()[:30] if len(tds) > 3 else "—"
            previous = (tds[4].text or "—").strip()[:30] if len(tds) > 4 else "—"
            forecast_val = tds[5].text if len(tds) > 5 else ""
            if not forecast_val and len(tds) > 6:
                forecast_val = tds[6].text
            forecast = (forecast_val or "—").strip()[:30]

            classes = " ".join(tr.classes)
            if "calendar-date-3" in classes:
                importance = "3"
            elif "calendar-date-2" in classes:
                importance = "2"
            else:
                importance = "1"

            if imp_filter and importance not in imp_filter:
                continue

            eid = tr.attrs.get("data-id") or str(len(events) + 1)

            events.append(
                {
                    "id": eid,
                    "time": full_time,
                    "country": country_name,
                    "currency": currency,
                    "importance": importance,
                    "event": event_name,
                    "actual": actual,
                    "forecast": forecast,
                    "previous": previous,
                }
            )

        return events, "live", None

    def get_events(
        self,
        date_from: Any,
        date_to: Any,
        *,
        countries: Iterable[Any] | None = None,
        importances: Iterable[Any] | None = None,
        categories: Iterable[Any] | None = None,
        timezone: int | str = 0,
        time_filter: str = "timeOnly",
        tab: str = "custom",
        limit_from: int | str = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get economic events from live feed (returns empty list if unreachable or no events match)."""
        _ = (categories, timezone, time_filter, tab, limit_from)
        d_from = self._date_str(date_from)
        d_to = self._date_str(date_to)
        imp_list = normalize_importance_filter(importances)

        events, _status, _msg = self._fetch_from_feed(
            d_from, d_to, countries=countries, importances=imp_list
        )
        if not events:
            return []

        if limit is not None:
            events = events[: max(1, int(limit))]
        return events

    def get_events_for_symbol(
        self,
        symbol: str,
        date_from: Any = None,
        date_to: Any = None,
        *,
        days: int = 7,
        importances: Iterable[Any] | str | None = None,
        countries: Iterable[Any] | str | None = None,
        limit: int | None = 30,
        asset_type: str = "auto",
    ) -> dict[str, Any]:
        """Get economic calendar events specifically impacting the selected asset (`symbol`)."""
        currencies = resolve_symbol_currencies(symbol, asset_type=asset_type)
        try:
            normalized_symbol = normalize_ticker(symbol, asset_type=asset_type, strict=False)
        except Exception:
            normalized_symbol = symbol.strip().upper()

        today = dt.datetime.now(dt.timezone.utc).date()
        if date_from is None or not str(date_from).strip():
            d_start = today
        else:
            d_from_str = self._date_str(date_from)
            try:
                d_start = dt.datetime.strptime(d_from_str, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError(
                    f"Invalid 'date_from' format {date_from!r}: expected 'YYYY-MM-DD'."
                ) from exc

        clamped_days = max(0, min(int(days), 60))
        if date_to is None or not str(date_to).strip():
            d_end = d_start + dt.timedelta(days=clamped_days)
        else:
            d_to_str = self._date_str(date_to)
            try:
                d_end = dt.datetime.strptime(d_to_str, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError(
                    f"Invalid 'date_to' format {date_to!r}: expected 'YYYY-MM-DD'."
                ) from exc

        if d_end < d_start:
            raise ValueError(
                f"'date_to' ({d_end.isoformat()}) cannot be earlier than 'date_from' ({d_start.isoformat()})."
            )

        d_from_iso = d_start.isoformat()
        d_to_iso = d_end.isoformat()

        imp_list = normalize_importance_filter(importances)

        if countries is not None:
            if isinstance(countries, str):
                target_filters = [c.strip() for c in re.split(r"[,;\s]+", countries) if c.strip()]
            else:
                target_filters = [str(c).strip() for c in countries if str(c).strip()]
        else:
            target_filters = list(currencies)

        if not target_filters:
            target_filters = list(currencies)

        live_events, fetch_source, fetch_message = self._fetch_from_feed(
            d_from_iso,
            d_to_iso,
            countries=target_filters,
            importances=imp_list,
        )
        source = fetch_source
        all_events = live_events
        message = fetch_message

        total_found = len(all_events)
        if limit is not None:
            max_items = max(1, min(int(limit), 100))
            returned_events = all_events[:max_items]
        else:
            returned_events = all_events

        result: dict[str, Any] = {
            "symbol": symbol.strip(),
            "normalized_symbol": normalized_symbol,
            "currencies": currencies,
            "filter_countries": target_filters,
            "date_from": d_from_iso,
            "date_to": d_to_iso,
            "importances": imp_list if imp_list is not None else ["1", "2", "3"],
            "source": source,
            "total_events": total_found,
            "events": returned_events,
        }
        if message is not None:
            result["message"] = message
        if limit is not None and total_found > len(returned_events):
            result["truncated_to"] = len(returned_events)
        return result


# Aliases for backward compatibility
EconomicCalendar = InvestingCalendar
TradingEconomicsCalendar = InvestingCalendar
InvestingCalendarError = Exception
