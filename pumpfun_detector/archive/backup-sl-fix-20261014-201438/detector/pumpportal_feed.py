"""
Feed en tiempo real PumpPortal + adaptador evento→feature (P1).

Puente entre los eventos websocket de PumpPortal
(``wss://pumpportal.fun/api/data?api-key=...``) y el vector de características
que consume ``MasterSnipeSignal.evaluate()``.

Motivación
----------
El detector original solo razona sobre datos históricos/demo (sintéticos). El
edge real del sniping vive en la latencia: decidir en milisegundos tras el
evento on-chain. Este módulo convierte el stream de trades de PumpPortal en el
mismo diccionario de features que ya entiende la Señal Maestra, de modo que las
reglas validadas OOS se puedan evaluar contra datos reales en caliente.

Esquema de eventos (verificado contra la doc oficial de PumpPortal y el
``TradeEvent`` on-chain de pump.fun):

- Trades (``subscribeTokenTrade`` / ``subscribeAccountTrade``), camelCase::

    {"signature": "...", "mint": "...", "traderPublicKey": "...",
     "txType": "buy"|"sell", "solAmount": 1.2, "tokensAmount": 12345,
     "marketCapSol": 30.0, "vSolInCurve": 45.0, "vTokensInCurve": 1.2e9,
     "pool": "pump"}

- Creación (``subscribeNewToken``): mismos campos base + metadatos del token.

Limitación honesta
-------------------
Los eventos de trade NO incluyen métricas de holders ni del dev
(``devBalancePct``, ``topHolderPct``, ``holderCount``). Esas requieren el
endpoint de holders/token de PumpPortal. Aquí se derivan las que sí se pueden
calcular del flujo (buys/sells/compradores únicos en ventana, progreso de
curva, market cap y reservas). Las de holders quedan en defaults seguros; la
Señal Maestra las trata como "desconocidas" sin romperse.

``sol_usd`` NO viene en el evento. Se usa un precio de referencia configurable
(por defecto 150.0) para expresar el market cap en USD, unidad de los umbrales
de la Señal Maestra. Debe calibrarse o sustituirse por un oráculo en producción.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from .master_signal import _int, _num
from .onchain_risk import entry_risk_labels

# La bonding curve de pump.fun se completa con ~85 SOL netos (== Cfg.curve_sol).
CURVE_COMPLETION_SOL = 85.0

# Precio de referencia SOL/USD (limitación documentada arriba).
SOL_USD_DEFAULT = 150.0

# Ventana por defecto para las métricas de flujo (segundos).
WINDOW_S_DEFAULT = 60.0


@dataclass
class NormalizedTrade:
    """Evento de trade normalizado, independiente del esquema de origen."""
    mint: str
    is_buy: bool
    sol_amount: float
    token_amount: float
    ts: float
    trader: str
    signature: str
    market_cap_sol: float
    v_sol_curve: float
    v_tokens_curve: float
    pool: str


def parse_trade_event(ev: Dict[str, Any], now: Optional[float] = None) -> Optional[NormalizedTrade]:
    """Normaliza un evento de trade de PumpPortal (o su equivalente on-chain).

    Acepta tanto el esquema camelCase de PumpPortal (``txType``, ``solAmount``)
    como el on-chain (``is_buy`` bool, ``sol_amount``). Devuelve ``None`` si el
    evento no es un trade accionable (creación, migración o mensaje de control).
    """
    if not isinstance(ev, dict):
        return None

    mint = str(ev.get("mint") or ev.get("tokenMint") or "")
    if not mint:
        return None

    # Dirección: PumpPortal usa txType ("buy"/"sell"); on-chain usa is_buy (bool).
    tx_type = str(ev.get("txType") or ev.get("ix_name") or "").lower()
    if tx_type in ("buy", "sell"):
        is_buy = tx_type == "buy"
    elif "is_buy" in ev:
        is_buy = bool(ev.get("is_buy"))
    else:
        return None  # no es un trade

    ts = _num(ev.get("timestamp", ev.get("ts", now if now is not None else time.time())))
    return NormalizedTrade(
        mint=mint,
        is_buy=is_buy,
        sol_amount=_num(ev.get("solAmount", ev.get("sol_amount", 0.0))),
        token_amount=_num(ev.get("tokensAmount", ev.get("token_amount", 0.0))),
        ts=ts if ts > 0 else time.time(),
        trader=str(ev.get("traderPublicKey") or ev.get("user") or ""),
        signature=str(ev.get("signature") or ""),
        market_cap_sol=_num(ev.get("marketCapSol", ev.get("market_cap_sol", 0.0))),
        v_sol_curve=_num(ev.get("vSolInCurve", ev.get("vSolInBondingCurve",
                            ev.get("v_sol_curve", ev.get("virtual_sol_reserves", 0.0))))),
        v_tokens_curve=_num(ev.get("vTokensInCurve", ev.get("vTokensInBondingCurve",
                              ev.get("v_tokens_curve", ev.get("virtual_token_reserves", 0.0))))),
        pool=str(ev.get("pool") or "pump"),
    )



@dataclass
class _TokenWindow:
    """Estado rolling de un token dentro de la ventana de flujo."""
    first_market_cap_sol: float = 0.0
    first_liquidity_sol: float = 0.0
    last_market_cap_sol: float = 0.0
    last_v_sol_curve: float = 0.0
    last_ts: float = 0.0
    trades: Deque[tuple] = field(default_factory=deque)  # (ts, is_buy, trader)


class PumpPortalAdapter:
    """Adaptador de eventos PumpPortal a features de la Señal Maestra.

    Mantiene una ventana temporal por token y expone ``features(mint)`` con el
    diccionario listo para ``MasterSnipeSignal.evaluate()``. Es puro (sin red):
    se alimenta con ``ingest()`` de eventos ya recibidos, lo que lo hace
    totalmente testeable.
    """

    def __init__(self, sol_usd: float = SOL_USD_DEFAULT, window_s: float = WINDOW_S_DEFAULT,
                 holders_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
                 sol_usd_oracle: Optional[Callable[[], float]] = None):
        self.sol_usd = _num(sol_usd, SOL_USD_DEFAULT) or SOL_USD_DEFAULT
        self.window_s = _num(window_s, WINDOW_S_DEFAULT) or WINDOW_S_DEFAULT
        # Inyección opcional y honesta (sin red aquí). Si se proveen, features()
        # usa métricas de holders y precio SOL/USD reales; si no, cae a los
        # defaults seguros documentados (la Señal Maestra los trata como
        # "desconocido" sin degradar la decisión).
        self.holders_fetcher = holders_fetcher
        self.sol_usd_oracle = sol_usd_oracle
        self._tokens: Dict[str, _TokenWindow] = defaultdict(_TokenWindow)

    def ingest(self, ev: Dict[str, Any]) -> Optional[NormalizedTrade]:
        """Procesa un evento crudo y actualiza el estado del token. Devuelve el
        trade normalizado, o ``None`` si el evento no es un trade."""
        tr = parse_trade_event(ev)
        if tr is None:
            return None
        w = self._tokens[tr.mint]
        if w.first_market_cap_sol <= 0.0:
            w.first_market_cap_sol = tr.market_cap_sol
            w.first_liquidity_sol = tr.v_sol_curve
        w.last_market_cap_sol = tr.market_cap_sol
        w.last_v_sol_curve = tr.v_sol_curve
        w.last_ts = tr.ts
        w.trades.append((tr.ts, tr.is_buy, tr.trader))
        self._evict(w)
        return tr

    def _evict(self, w: _TokenWindow) -> None:
        """Elimina de la ventana los trades más antiguos que ``window_s``."""
        if w.last_ts <= 0.0:
            return
        cutoff = w.last_ts - self.window_s
        while w.trades and w.trades[0][0] < cutoff:
            w.trades.popleft()

    def window_stats(self, mint: str) -> Dict[str, float]:
        """Estadísticas de flujo dentro de la ventana para un token."""
        w = self._tokens.get(mint)
        if w is None or not w.trades:
            return {"n_buys": 0, "n_sells": 0, "unique_buyers": 0, "buy_sell_ratio": 0.0}
        buys = sum(1 for _, is_buy, _ in w.trades if is_buy)
        sells = len(w.trades) - buys
        uniq_buyers = len({t for _, is_buy, t in w.trades if is_buy and t})
        ratio = (buys + 1.0) / (sells + 1.0)
        return {"n_buys": buys, "n_sells": sells, "unique_buyers": uniq_buyers,
                "buy_sell_ratio": round(ratio, 4)}

    def _sol_usd_now(self) -> float:
        """Precio SOL/USD vigente: oráculo inyectado si existe, si no el estático."""
        if self.sol_usd_oracle is None:
            return self.sol_usd
        try:
            px = _num(self.sol_usd_oracle())
        except Exception:
            return self.sol_usd
        return px if px > 0 else self.sol_usd

    def _holders(self, mint: str) -> Dict[str, Any]:
        """Métricas de holders/dev del token.

        Sin fetcher inyectado (o si falla), devuelve defaults seguros: el stream
        de trades de PumpPortal no las incluye, así que NO se inventan valores
        reales. La Señal Maestra interpreta estos como "desconocido".
        """
        base = {"dev_balance_pct": 0.0, "top_holder_pct": 0.0, "holder_count": 0, "n_socials": 0, "net_buyers_1h": 0}
        if self.holders_fetcher is None:
            return base
        try:
            raw = self.holders_fetcher(mint)
        except Exception:
            return base
        if not isinstance(raw, dict):
            return base
        out = dict(base)
        # Acepta camelCase (API) o snake_case (interno).
        if "devBalancePct" in raw or "dev_balance_pct" in raw:
            out["dev_balance_pct"] = _num(raw.get("devBalancePct", raw.get("dev_balance_pct", 0.0)))
        if "topHolderPct" in raw or "top_holder_pct" in raw:
            out["top_holder_pct"] = _num(raw.get("topHolderPct", raw.get("top_holder_pct", 0.0)))
        if "holderCount" in raw or "holder_count" in raw:
            out["holder_count"] = int(_num(raw.get("holderCount", raw.get("holder_count", 0))))
        if "n_socials" in raw or "nSocials" in raw:
            out["n_socials"] = int(_num(raw.get("n_socials", raw.get("nSocials", 0))))
        elif any(k in raw for k in ("twitter", "telegram", "website")):
            out["n_socials"] = sum(1 for k in ("twitter", "telegram", "website") if raw.get(k))
        if "netBuyers1h" in raw or "net_buyers_1h" in raw:
            out["net_buyers_1h"] = int(_num(raw.get("netBuyers1h", raw.get("net_buyers_1h", 0))))
        return out

    def features(self, mint: str) -> Dict[str, Any]:
        """Construye el diccionario de features compatible con la Señal Maestra.

        El market cap "de entrada" se aproxima con el último market cap observado
        de la curva (correcto para un sniper que decide en el momento del
        evento). Las métricas de holders/dev, ausentes en el stream de trades,
        quedan en defaults seguros.
        """
        w = self._tokens.get(mint)
        if w is None:
            return {}
        st = self.window_stats(mint)
        sol_usd = self._sol_usd_now()
        mc_usd = w.last_market_cap_sol * sol_usd
        fmc_usd = w.first_market_cap_sol * sol_usd
        holders = self._holders(mint)
        curve_frac = min(max(w.last_v_sol_curve / CURVE_COMPLETION_SOL, 0.0), 1.0)
        result = {
            # --- market cap (USD, entrada-seguro: valor actual de la curva) ---
            "market_cap_at_entry": mc_usd,
            "mc_at_entry_est": mc_usd,
            "first_market_cap": fmc_usd,
            "first_liquidity": w.first_liquidity_sol,
            # --- curva / reservas ---
            "curve_progress": round(curve_frac * 100.0, 2),   # 0..100
            "ef_curve_progress": round(curve_frac, 6),         # 0..1
            "realSolReserves": w.last_v_sol_curve,
            # --- flujo derivado en ventana ---
            "n_buys_60s": st["n_buys"],
            "n_sells_60s": st["n_sells"],
            "unique_buyers_60s": max(st["unique_buyers"], 1),
            "buy_sell_ratio_60s": st["buy_sell_ratio"],
            # --- holders/dev: fetcher inyectado, si no defaults seguros ---
            "dev_balance_pct": holders["dev_balance_pct"],
            "top_holder_pct": holders["top_holder_pct"],
            "holder_count": max(int(holders["holder_count"]), st["unique_buyers"], 1),
            # --- confluencia social y flujo neto de compradores ---
            "n_socials": int(holders.get("n_socials", 0)),
            "netBuyers1h": int(holders.get("net_buyers_1h") or max(st["n_buys"] - st["n_sells"], st["unique_buyers"])),
            # --- procedencia P0 ---
            "data_origin": "live",
        }
        # P2: etiquetas descriptivas; no son gates de producción todavía.
        result.update(entry_risk_labels(result))
        return result

    def decide(self, mint: str):
        """Atajo: evalúa un token con la Señal Maestra y devuelve la decisión."""
        from .master_signal import MasterSnipeSignal
        feats = self.features(mint)
        if not feats:
            return None
        return MasterSnipeSignal.evaluate(feats, strict_asof=True)

# ---------------------------------------------------------------------------
# Cliente websocket en vivo + modo replay offline (sin red, determinista).
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"


def subscribe_trade_message(mints: List[str], keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Mensaje de suscripción a trades por token (mints).

    PumpPortal acepta ``{"method":"subscribeTokenTrade","keys":[...]}``. Si no se
    indican mints, se escuchan todos los trades nuevos.
    """
    return {
        "method": "subscribeTokenTrade",
        "keys": list(mints) if mints else [],
    }


def subscribe_new_token_message() -> Dict[str, Any]:
    """Mensaje de suscripción a tokens recién creados."""
    return {"method": "subscribeNewToken", "keys": []}


async def stream_trades(
    adapter: "PumpPortalAdapter",
    mints: Optional[List[str]] = None,
    api_key: str = "",
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_events: Optional[int] = None,
) -> int:
    """Consume el websocket de PumpPortal y alimenta ``adapter`` en caliente.

    Parámetros
    ----------
    adapter : PumpPortalAdapter
        Ventana rolling que acumula los eventos y construye features.
    mints : list[str] | None
        Tokens a escuchar. ``None`` o vacío = todos los trades.
    api_key : str
        Clave de PumpPortal (se añade como query ``?api-key=`` si se provee).
    on_event : callable | None
        Callback por cada evento normalizado (p. ej. para loggear o decidir).
    max_events : int | None
        Corta tras N eventos (útil para pruebas de humo). ``None`` = infinito.

    Devuelve el número de trades normalizados ingeridos.

    Nota: ``websockets`` se importa en diferido para no imponer la dependencia a
    quien solo use el adaptador offline.
    """
    import websockets  # import perezoso

    url = PUMPPORTAL_WS + (f"?api-key={api_key}" if api_key else "")
    ingested = 0
    async with websockets.connect(url, ping_interval=20) as ws:
        await ws.send(json.dumps(subscribe_trade_message(mints)))
        while True:
            if max_events is not None and ingested >= max_events:
                break
            raw = await ws.recv()
            try:
                ev = json.loads(raw)
            except (TypeError, ValueError):
                continue
            tr = adapter.ingest(ev)
            if tr is not None:
                ingested += 1
                if on_event is not None:
                    on_event({"trade": tr, "features": adapter.features(tr.mint)})
    return ingested


def replay_trades(
    adapter: "PumpPortalAdapter",
    events: List[Dict[str, Any]],
) -> int:
    """Reintroduce una lista de eventos (offline) y devuelve cuántos se ingirieron.

    Equivalente síncrono y determinista de :func:`stream_trades`; permite testear
    la tubería completa y generar features sin red.
    """
    ingested = 0
    for ev in events:
        if adapter.ingest(ev) is not None:
            ingested += 1
    return ingested

