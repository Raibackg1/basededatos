"""Feed de mercado REAL (solo lectura) para el paper-forward del cockpit.

Sustituye el paseo aleatorio (``random.uniform``) del bot del cockpit por
**market caps reales** tomados de la API pública de DexScreener.

Reglas de honestidad (§3, sin mocks silenciosos):

- **No fabrica nada.** Si la fuente falla, no hay par o el token no existe,
  devuelve ``None``/``[]`` y el consumidor debe dejar el precio quieto. Nunca
  inventa un drift ni un valor "por defecto".
- **Sin dependencias nuevas ni API keys**: solo ``urllib`` de la stdlib.
- **Cachea con TTL** y limita la frecuencia de llamadas (rate-limit cortés).
- El transporte HTTP, el reloj y el ``sleep`` son **inyectables** para que los
  tests sean offline y deterministas.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

DEXSCREENER_TOKENS = "https://api.dexscreener.com/latest/dex/tokens/"
DEXSCREENER_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
_USER_AGENT = "pumpfun-cockpit/1.0 (+local paper-forward, read-only)"


def _num(v: Any) -> Optional[float]:
    """Convierte a float finito, o ``None`` si no es convertible/NaN/inf."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


class MarketFeed:
    """Precios reales (market cap USD) y descubrimiento vía DexScreener.

    Uso::

        feed = MarketFeed()
        mc = feed.market_cap_usd(mint)          # None si no hay dato real
        tokens = feed.discover_new_tokens(10)   # [] si la fuente no responde
    """

    def __init__(
        self,
        http_get: Optional[Callable[[str], Optional[Any]]] = None,
        *,
        ttl_s: float = 8.0,
        min_interval_s: float = 0.25,
        timeout_s: float = 10.0,
        max_cache: int = 4096,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http_get = http_get
        self.ttl_s = float(ttl_s)
        self.min_interval_s = float(min_interval_s)
        self.timeout_s = float(timeout_s)
        self.max_cache = max(1, int(max_cache))
        self._clock = clock
        self._sleep = sleep
        # mint -> (ts, snapshot | None). Se cachean también los fallos recientes
        # (snapshot None) para no martillar la fuente con mints sin par.
        self._cache: Dict[str, tuple] = {}
        self._last_call = 0.0
        self.calls = 0
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Transporte
    # ------------------------------------------------------------------
    def _default_http_get(self, url: str) -> Optional[Any]:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None

    def _get_json(self, url: str) -> Optional[Any]:
        """GET JSON con rate-limit cortés. Devuelve None ante cualquier fallo."""
        now = self._clock()
        wait = self.min_interval_s - (now - self._last_call)
        if wait > 0:
            self._sleep(wait)
            now = self._clock()
        self._last_call = now
        self.calls += 1
        getter = self._http_get or self._default_http_get
        try:
            data = getter(url)
        except Exception as exc:  # transporte inyectado defectuoso
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if data is None:
            self.errors += 1
        return data

    # ------------------------------------------------------------------
    # Snapshot por token
    # ------------------------------------------------------------------
    @staticmethod
    def _pick_best_pair(pairs: List[Any]) -> Optional[Dict[str, Any]]:
        """Elige el par con mayor liquidez (fallback: mayor market cap)."""
        best: Optional[Dict[str, Any]] = None
        best_key = (-1.0, -1.0)
        for p in pairs:
            if not isinstance(p, dict):
                continue
            mc = _num(p.get("marketCap"))
            if mc is None:
                mc = _num(p.get("fdv"))
            if mc is None or mc <= 0:
                continue
            liq = _num((p.get("liquidity") or {}).get("usd")) or 0.0
            key = (liq, mc)
            if key > best_key:
                best_key = key
                best = p
        return best

    @classmethod
    def _snapshot_from_payload(cls, mint: str, payload: Any) -> Optional[Dict[str, Any]]:
        pairs = payload.get("pairs") if isinstance(payload, dict) else None
        if not pairs:
            return None
        pair = cls._pick_best_pair(pairs)
        if pair is None:
            return None
        mc = _num(pair.get("marketCap"))
        if mc is None:
            mc = _num(pair.get("fdv"))
        if mc is None or mc <= 0:
            return None
        base = pair.get("baseToken") or {}
        return {
            "mint": mint,
            "market_cap_usd": mc,
            "liquidity_usd": _num((pair.get("liquidity") or {}).get("usd")) or 0.0,
            "price_usd": _num(pair.get("priceUsd")) or 0.0,
            "symbol": str(base.get("symbol") or ""),
            "name": str(base.get("name") or ""),
            "price_change_5m": _num((pair.get("priceChange") or {}).get("m5")) or 0.0,
            "volume_5m_usd": _num((pair.get("volume") or {}).get("m5")) or 0.0,
        }

    def _snapshot_cached(self, mint: str) -> Optional[Dict[str, Any]]:
        mint = str(mint or "")
        if not mint:
            return None
        now = self._clock()
        cached = self._cache.get(mint)
        if cached is not None and (now - cached[0]) < self.ttl_s:
            self.hits += 1
            return cached[1]
        payload = self._get_json(DEXSCREENER_TOKENS + mint)
        snap = self._snapshot_from_payload(mint, payload) if payload is not None else None
        if snap is None:
            self.misses += 1
        if len(self._cache) >= self.max_cache:
            self._cache.clear()
        self._cache[mint] = (now, snap)
        return snap

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def market_cap_usd(self, mint: str) -> Optional[float]:
        """Market cap real en USD, o ``None`` si no hay dato verificable."""
        snap = self._snapshot_cached(mint)
        return snap["market_cap_usd"] if snap else None

    def token_snapshot(self, mint: str) -> Optional[Dict[str, Any]]:
        """Snapshot real del token (mc, liquidez, símbolo, volumen 5m)."""
        return self._snapshot_cached(mint)

    def discover_new_tokens(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Tokens Solana recientes con market cap real. ``[]`` si no hay fuente.

        Usa los perfiles más recientes de DexScreener (sin key) y enriquece cada
        uno con su snapshot real. Se descartan los tokens sin par/market cap.
        """
        payload = self._get_json(DEXSCREENER_PROFILES)
        if not isinstance(payload, list):
            return []
        limit = max(1, int(limit))
        out: List[Dict[str, Any]] = []
        seen: set = set()
        for item in payload:
            if len(out) >= limit:
                break
            if not isinstance(item, dict) or item.get("chainId") != "solana":
                continue
            mint = str(item.get("tokenAddress") or "")
            if not mint or mint in seen:
                continue
            seen.add(mint)
            snap = self._snapshot_cached(mint)
            if snap is None:
                continue
            out.append(snap)
        return out

    def health(self) -> Dict[str, Any]:
        """Estado observable de la fuente (para mostrarlo, no para decidir)."""
        return {
            "source": "dexscreener",
            "calls": self.calls,
            "cache_hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "cache_size": len(self._cache),
            "last_error": self.last_error,
        }
