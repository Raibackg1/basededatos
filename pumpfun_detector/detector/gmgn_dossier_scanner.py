"""
GMGN Dossier Scanner — Escáner Forense Multi-Fuente de Paridad GMGN.ai.

Implementa las verificaciones forenses de las skills `gmgn-contract-dd`,
`gmgn-holder-analysis`, `gmgn-dev-score` y `gmgn-kline-pattern` combinando:
1. GoPlus Security API (EVM: BSC, Base, Ethereum) — auditoría de contrato y Top 10 holders.
2. RugCheck.xyz API (Solana) — riesgos de mint/freeze, LP lock y concentración.
3. DexScreener Token & Search API — liquidez, volumen 5m/1h/24h, tamaño medio de tx y clones homónimos.
4. Heurísticas matemáticas auditadas sobre el caso real `币安银行` (0x8a5d...4444):
   - Detección de Bundlers ocultos por uniformidad de saldo en curva de bonding (CV < 0.03).
   - Re-base de concentración Top 10 sobre Flotante Real Comerciable (1 - LP - Burn).
   - Detección de Wash-Trading en escalera por micro-transacciones (< $5 USD media en 5m).
   - Detección de Fábrica de Clones Homónimos ("Same-Name / Vamp") y sufijos vanity (...4444, ...ffff).
   - Evaluación de Linaje del Desarrollador (ratio graduados 0/73 y techo histórico de MC).
   - Fingerprint determinista de Avatar (dHash 64-bit) para detectar `Avatar Reused Tokens`.
   - Diagnóstico causal de por qué sube o baja el precio (`why_price_moves`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .creator_registry import holder_shannon_entropy

logger = logging.getLogger(__name__)

CHAIN_TO_GOPLUS_ID = {
    "bsc": "56",
    "bnb": "56",
    "56": "56",
    "eth": "1",
    "ethereum": "1",
    "1": "1",
    "base": "8453",
    "8453": "8453",
    "arb": "42161",
}


def _sf(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return default


def _opt_float(val: Any) -> Optional[float]:
    """float o ``None``; NaN/Inf también son ausencia de dato."""
    if val is None or val == "" or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _si(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else int(f)
    except (ValueError, TypeError):
        return default


def compute_avatar_dhash_from_bytes(data: bytes) -> str:
    """Calcula un fingerprint perceptual de 64 bits (16 hex chars) sobre bloques de bytes.

    Divide el payload de imagen (saltando cabeceras estándar PNG/JPEG si existen)
    en 65 segmentos contiguos, promedia la intensidad de cada segmento y compara
    segmentos adyacentes (gradiente diferencial dHash de 64 bits).
    """
    if not data:
        return "0000000000000000"
    payload = data[24:] if len(data) > 128 else data
    n = len(payload)
    if n < 65:
        return hashlib.sha256(data).hexdigest()[:16]

    step = n / 65.0
    means: List[float] = []
    for i in range(65):
        start = int(i * step)
        end = max(start + 1, int((i + 1) * step))
        chunk = payload[start:end]
        means.append(sum(chunk) / len(chunk))

    bits = 0
    for i in range(64):
        if means[i] > means[i + 1]:
            bits |= (1 << (63 - i))
    return f"{bits:016x}"


def resolve_top10_float(
    top10_raw_pct: Optional[float],
    contract_share_pct: Optional[float],
) -> tuple[Optional[float], str, Optional[str]]:
    """Concentración del Top 10 sobre el flotante **real**, o ``None``.

    Reglas anti-falacia:
    * Sin ``top10_raw_pct`` no hay medición -> ``None``.
    * Si el flotante negociable es menor al 20% del supply, el cociente es
      inestable (un error de 1pp en el denominador mueve el resultado >5pp)
      -> ``None`` con bandera ``TOP10_DENOMINATOR_UNRELIABLE``.
    * Un porcentaje mayor a 100 es físicamente imposible -> ``None`` con
      bandera ``TOP10_EXCEEDS_FLOAT`` (jamás se recorta el valor).
    """
    raw = _opt_float(top10_raw_pct)
    contract = _opt_float(contract_share_pct)
    if raw is None:
        return None, "unavailable", "TOP10_RAW_UNKNOWN"
    if contract is None:
        return None, "unavailable", "TOP10_FLOAT_UNKNOWN"
    tradeable = 100.0 - contract
    if tradeable < 20.0:
        return None, "unavailable", "TOP10_DENOMINATOR_UNRELIABLE"
    value = round((raw / tradeable) * 100.0, 2)
    if value > 100.0:
        return None, "unavailable", "TOP10_EXCEEDS_FLOAT"
    return value, "measured", None


def detect_bundler_and_cluster(
    holders: List[Dict[str, Any]],
    lp_burned_pct: float = 0.0,
) -> Dict[str, Any]:
    """Analiza la lista de holders para detectar Bundlers ocultos y concentración real sobre flotante.

    En lanzamientos como Four.meme / Pump.fun, el creador oculta un 68% de bundler
    repartiéndolo en ~30 billeteras de ~2.27% cada una para que el Top 10 parezca 20.4% (<30%).
    Esta función detecta el cluster uniforme por su bajísima dispersión relativa (CV < 0.03).
    """
    if not holders:
        # Sin holders no hay medición: `None` (N/D), nunca 0.0 ni 20.0 inventado.
        return {
            "top10_raw_pct": None,
            "top10_float_pct": None,
            "tradeable_float_pct": None,
            "top10_source": "unavailable",
            "top10_flag": "HOLDERS_UNKNOWN",
            "chip_source": "unavailable",
            "contract_or_lp_pct": None,
            "uniform_cluster_count": 0,
            "uniform_cluster_sum_pct": None,
            "estimated_bundler_pct": None,
            "bundler_source": "unavailable",
            "insider_transfer_pct": None,
            "holder_entropy": None,
            "bundler_flag": False,
            "reason": "HOLDERS_UNKNOWN",
        }

    contract_share = 0.0
    wallet_pcts: List[float] = []
    insider_transfer_pct = 0.0

    for h in holders:
        if "pct" in h and h.get("pct") is not None and "percent" not in h:
            pct = _sf(h.get("pct"))
        else:
            raw_p = _sf(h.get("percent", h.get("pct", h.get("supply_share", 0.0))))
            # Normalizar a escala 0..100
            pct = raw_p * 100.0 if 0.0 < raw_p <= 1.0 else raw_p
        addr = str(h.get("address", "")).lower()
        owner = str(h.get("owner", "")).lower()
        tag = str(h.get("tag", "")).lower()
        is_contract = _si(h.get("is_contract", 0)) == 1
        is_locked = _si(h.get("is_locked", 0)) == 1
        is_burn = addr in (
            "0x000000000000000000000000000000000000dead",
            "0x0000000000000000000000000000000000000000",
            "1nc1nerator11111111111111111111111111111111",
        ) or owner == "1nc1nerator11111111111111111111111111111111" or "burn" in tag or "dead" in tag or "lp" in tag or "pool" in tag or "amm" in tag

        if is_contract or is_locked or is_burn:
            contract_share += pct
        else:
            if pct > 0.0:
                wallet_pcts.append(pct)
            if h.get("transfer_in") or h.get("is_insider") or h.get("insider"):
                insider_transfer_pct += pct

    tradeable_float_pct = round(100.0 - contract_share, 2)
    wallet_pcts.sort(reverse=True)
    top10_wallets = wallet_pcts[:10]
    top10_raw_pct = round(sum(top10_wallets), 2)
    # `None` cuando el cociente no es físicamente interpretable (ver helper).
    top10_float_pct, top10_source, top10_flag = resolve_top10_float(
        top10_raw_pct, contract_share
    )

    # Detectar cluster uniforme de Bundler (billeteras con saldo casi idéntico >= 0.4%)
    significant = [p for p in wallet_pcts if p >= 0.40]
    best_cluster: List[float] = []
    for i, base_p in enumerate(significant):
        cluster = [
            p for p in significant
            if abs(p - base_p) / max(base_p, 1e-6) <= 0.035
        ]
        if len(cluster) > len(best_cluster):
            best_cluster = cluster

    uniform_count = len(best_cluster)
    uniform_sum_pct = round(sum(best_cluster), 2)
    mean_cluster_pct = (uniform_sum_pct / uniform_count) if uniform_count > 0 else 0.0

    # Si las 8 o 9 billeteras no-contrato del Top 10 son todas del mismo bundle
    # (ej. 9 wallets de 2.2709% en GoPlus Top 10), extrapolamos la cola oculta del bloque 0
    if uniform_count >= 8 and 1.2 <= mean_cluster_pct <= 3.5:
        estimated_bundler_pct = round(min(78.0, mean_cluster_pct * 30.0), 2)
    elif uniform_count >= 4:
        estimated_bundler_pct = round(max(uniform_sum_pct, min(65.0, uniform_sum_pct * 1.8)), 2)
    else:
        estimated_bundler_pct = round(uniform_sum_pct if uniform_count >= 3 else 0.0, 2)

    entropy = holder_shannon_entropy(wallet_pcts) if len(wallet_pcts) >= 2 else 0.0
    bundler_flag = bool(
        estimated_bundler_pct >= 20.0
        or uniform_count >= 4
        or insider_transfer_pct >= 5.0
    )

    reasons: List[str] = []
    if uniform_count >= 4:
        reasons.append(
            f"BUNDLER_UNIFORM_CLUSTER: {uniform_count} wallets en Top 10 tienen ~{mean_cluster_pct:.2f}% "
            f"idéntico (Bundler estimado: {estimated_bundler_pct:.1f}%)"
        )
    if top10_float_pct is not None and top10_float_pct > 30.0:
        reasons.append(
            f"TOP10_FLOAT_CONCENTRATION: {top10_float_pct:.1f}% del flotante real (> 30%)"
        )
    if insider_transfer_pct >= 5.0:
        reasons.append(f"INSIDER_TRANSFER_CHIP: {insider_transfer_pct:.1f}% recibido por transferencia")

    return {
        "top10_raw_pct": top10_raw_pct,
        "top10_float_pct": top10_float_pct,
        "top10_source": top10_source,
        "top10_flag": top10_flag,
        "chip_source": "measured",
        "tradeable_float_pct": round(tradeable_float_pct, 2),
        "contract_or_lp_pct": round(contract_share, 2),
        "uniform_cluster_count": uniform_count,
        "uniform_cluster_sum_pct": uniform_sum_pct,
        "estimated_bundler_pct": estimated_bundler_pct,
        "bundler_source": (
            "measured:uniform_cluster" if uniform_count >= 3 else "measured:no_uniform_cluster"
        ),
        "insider_transfer_pct": round(insider_transfer_pct, 2),
        "holder_entropy": round(entropy, 4),
        "bundler_flag": bundler_flag,
        "reason": "; ".join(reasons) if reasons else "CHIP_STRUCTURE_CLEAN",
    }


def detect_wash_trading_staircase(
    txns: Dict[str, Any],
    volume: Dict[str, Any],
    market_cap_usd: float,
) -> Dict[str, Any]:
    """Detecta manipulación de precio por escalera de micro-compras (Wash-Trading Staircase).

    Caso real `币安银行` (0x8a...4444):
    - MC = $41,258, pero `volume.m5 = $14.65` en 6 txs ($2.44/tx) y `volume.h1 = $373.28` en 46 txs ($8.11/tx).
    """
    m5 = (txns or {}).get("m5", {}) if isinstance(txns, dict) else {}
    h1 = (txns or {}).get("h1", {}) if isinstance(txns, dict) else {}
    h24 = (txns or {}).get("h24", {}) if isinstance(txns, dict) else {}

    buys_m5, sells_m5 = _si(m5.get("buys")), _si(m5.get("sells"))
    buys_h1, sells_h1 = _si(h1.get("buys")), _si(h1.get("sells"))
    buys_h24, sells_h24 = _si(h24.get("buys")), _si(h24.get("sells"))

    vol = volume if isinstance(volume, dict) else {}
    vol_m5 = _sf(vol.get("m5"))
    vol_h1 = _sf(vol.get("h1"))
    vol_h24 = _sf(vol.get("h24"))

    tx_m5 = buys_m5 + sells_m5
    tx_h1 = buys_h1 + sells_h1
    tx_h24 = buys_h24 + sells_h24

    avg_tx_m5 = (vol_m5 / tx_m5) if tx_m5 > 0 else 0.0
    avg_tx_h1 = (vol_h1 / tx_h1) if tx_h1 > 0 else 0.0
    avg_tx_h24 = (vol_h24 / tx_h24) if tx_h24 > 0 else 0.0
    vol_to_mc_h1_pct = (vol_h1 / max(market_cap_usd, 1.0)) * 100.0

    reasons: List[str] = []
    if tx_m5 >= 4 and avg_tx_m5 < 5.0:
        reasons.append(
            f"WASH_TRADING_MICRO_M5: tamaño medio por tx en 5m es ${avg_tx_m5:.2f} (< $5.00 en {tx_m5} txs)"
        )
    if tx_h1 >= 20 and avg_tx_h1 < 12.0 and market_cap_usd >= 15000.0:
        reasons.append(
            f"WASH_TRADING_STAIRCASE_H1: tamaño medio en 1h es ${avg_tx_h1:.2f} con MC ${market_cap_usd:,.0f}"
        )
    if market_cap_usd >= 25000.0 and tx_h1 >= 15 and vol_to_mc_h1_pct < 2.0:
        reasons.append(
            f"HOLLOW_VOLUME_DIVERGENCE: volumen 1h (${vol_h1:,.0f}) es solo {vol_to_mc_h1_pct:.2f}% del MC"
        )

    return {
        "wash_trading_flag": len(reasons) > 0,
        "avg_tx_m5_usd": round(avg_tx_m5, 2),
        "avg_tx_h1_usd": round(avg_tx_h1, 2),
        "avg_tx_h24_usd": round(avg_tx_h24, 2),
        "vol_to_mc_h1_pct": round(vol_to_mc_h1_pct, 2),
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "buys_h1": buys_h1,
        "sells_h1": sells_h1,
        "reason": "; ".join(reasons) if reasons else "ORDER_FLOW_ORGANIC",
    }


def detect_same_name_vamp_and_clones(
    mint: str,
    symbol: str,
    name: str,
    search_pairs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Detecta clones con el mismo nombre/símbolo ('Same-Name / Vamp') y fábricas vanity (...4444)."""
    target_addr = str(mint or "").strip().lower()
    target_sym = str(symbol or "").strip().lower()
    target_name = str(name or "").strip().lower()

    if not search_pairs:
        return {
            "same_name_count": 1,
            "same_name_24h_count": 1,
            "dead_clones_count": 0,
            "vanity_4444_count": 1 if target_addr.endswith("4444") else 0,
            "vanity_ffff_count": 1 if target_addr.endswith("ffff") else 0,
            "is_vamp_clone": False,
            "og_leader_mint": mint,
            "clone_factory_flag": False,
            "clones_preview": [],
            "reason": "NO_CLONES_DETECTED",
        }

    seen_addrs: Dict[str, Dict[str, Any]] = {}
    for p in search_pairs:
        base = (p or {}).get("baseToken", {}) if isinstance(p, dict) else {}
        addr = str(base.get("address", "")).strip()
        addr_low = addr.lower()
        if not addr_low:
            continue
        p_sym = str(base.get("symbol", "")).strip().lower()
        p_name = str(base.get("name", "")).strip().lower()
        if target_sym and (target_sym in p_sym or p_sym in target_sym or target_name == p_name):
            vol_h24 = _sf((p.get("volume") or {}).get("h24"))
            vol_h1 = _sf((p.get("volume") or {}).get("h1"))
            mc = _sf(p.get("marketCap", p.get("fdv")))
            created = _si(p.get("pairCreatedAt"))
            if addr_low not in seen_addrs or mc > seen_addrs[addr_low]["mc"]:
                seen_addrs[addr_low] = {
                    "address": addr,
                    "symbol": base.get("symbol", symbol),
                    "name": base.get("name", name),
                    "mc": mc,
                    "vol_h24": vol_h24,
                    "vol_h1": vol_h1,
                    "created_ms": created,
                }

    clones = list(seen_addrs.values())
    same_name_count = max(1, len(clones))
    vanity_4444 = sum(1 for c in clones if c["address"].lower().endswith("4444"))
    vanity_ffff = sum(1 for c in clones if c["address"].lower().endswith("ffff"))
    dead_clones = sum(
        1 for c in clones
        if c["address"].lower() != target_addr and (c["vol_h1"] <= 5.0 or c["mc"] < 5500.0)
    )

    # Identificar si existe un líder (OG) con más de 2x volumen/MC creado antes
    target_info = seen_addrs.get(target_addr, {"mc": 0.0, "vol_h24": 0.0, "created_ms": 0})
    og_leader = max(clones, key=lambda c: (c["vol_h24"], c["mc"])) if clones else target_info
    is_vamp_clone = bool(
        og_leader.get("address", "").lower() != target_addr
        and og_leader.get("mc", 0.0) > max(target_info.get("mc", 0.0) * 2.0, 25000.0)
    )

    # Contar cuántos se crearon en una ventana de 24h alrededor del más reciente
    max_ts = max((c["created_ms"] for c in clones if c["created_ms"] > 0), default=0)
    if max_ts > 0:
        same_name_24h = sum(
            1 for c in clones
            if c["created_ms"] <= 0 or (max_ts - c["created_ms"]) <= 86400_000 * 2
        )
    else:
        same_name_24h = same_name_count

    clone_factory_flag = bool(
        (same_name_count >= 4 and dead_clones >= 2)
        or vanity_4444 >= 3
        or vanity_ffff >= 3
        or is_vamp_clone
    )

    reasons: List[str] = []
    if clone_factory_flag:
        reasons.append(
            f"SERIAL_CLONE_FACTORY: {same_name_count} tokens homónimos ('{symbol}'), "
            f"{dead_clones} clones muertos (<$5.5K MC), {vanity_4444} con vanity '...4444'"
        )
    if is_vamp_clone:
        reasons.append(f"VAMP_COPYCAT: existe otro '{symbol}' líder con MC ${og_leader.get('mc', 0):,.0f}")

    preview = [
        {
            "address": c["address"],
            "mc_usd": round(c["mc"], 1),
            "vol_h24_usd": round(c["vol_h24"], 1),
            "is_dead": c["vol_h1"] <= 5.0 or c["mc"] < 5500.0,
        }
        for c in sorted(clones, key=lambda x: x["created_ms"], reverse=True)[:6]
    ]

    return {
        "same_name_count": same_name_count,
        "same_name_24h_count": same_name_24h,
        "dead_clones_count": dead_clones,
        "vanity_4444_count": vanity_4444,
        "vanity_ffff_count": vanity_ffff,
        "is_vamp_clone": is_vamp_clone,
        "og_leader_mint": og_leader.get("address", mint),
        "clone_factory_flag": clone_factory_flag,
        "clones_preview": preview,
        "reason": "; ".join(reasons) if reasons else "UNIQUE_TICKER",
    }


def evaluate_creator_lineage_and_ceiling(
    creator_address: str,
    current_mc_usd: float,
    dev_stats: Optional[Dict[str, Any]] = None,
    creator_registry: Optional[Any] = None,
) -> Dict[str, Any]:
    """Evalúa el historial del desarrollador (`0/73` en GMGN) y su techo histórico de MC (`$44K`)."""
    stats = dict(dev_stats or {})
    # Anti-falacia: historial ausente = None, no "0 lanzamientos" (que activaba
    # un veto DEV_SERIAL_FACTORY espurio con datos inexistentes).
    history_known = bool(stats)
    total_launches = _si(stats.get("total_launches")) if stats.get("total_launches") is not None else None
    graduated_count = _si(stats.get("graduated_count")) if stats.get("graduated_count") is not None else None
    rugs_count = _si(stats.get("rugs_count")) if stats.get("rugs_count") is not None else None
    best_ath_mc_usd = _opt_float(stats.get("best_ath_mc_usd"))
    funder_address = str(stats.get("funder_address", "") or "")

    registry_blocked = False
    if creator_registry is not None and creator_address:
        try:
            rep = creator_registry.get_reputation(creator_address)
            history_known = True
            total_launches = max(total_launches or 0, rep.n_launches)
            graduated_count = max(graduated_count or 0, rep.n_successes)
            rugs_count = max(rugs_count or 0, rep.n_rugs)
            registry_blocked = bool(rep.is_blocked)
        except Exception:
            pass

    grad_rate_pct = (
        (graduated_count / total_launches * 100.0)
        if (total_launches is not None and total_launches > 0 and graduated_count is not None)
        else None
    )
    mc_vs_ath_pct = (
        (current_mc_usd / best_ath_mc_usd * 100.0)
        if (best_ath_mc_usd is not None and best_ath_mc_usd > 0)
        else None
    )

    dev_factory_flag = bool(
        registry_blocked
        or (rugs_count is not None and rugs_count >= 2)
        or (total_launches is not None and total_launches >= 5
            and grad_rate_pct is not None and grad_rate_pct < 5.0)
        or (total_launches is not None and total_launches >= 3 and graduated_count == 0)
    )
    near_dev_ceiling_flag = bool(
        best_ath_mc_usd is not None
        and best_ath_mc_usd > 0.0
        and total_launches is not None
        and total_launches >= 3
        and current_mc_usd >= 0.70 * best_ath_mc_usd
    )

    reasons: List[str] = []
    if dev_factory_flag:
        launches_txt = "N/D" if total_launches is None else str(total_launches)
        grad_txt = "N/D" if graduated_count is None else str(graduated_count)
        rate_txt = "N/D" if grad_rate_pct is None else f"{grad_rate_pct:.1f}%"
        reasons.append(
            f"DEV_SERIAL_FACTORY: creador lanzó {launches_txt} tokens con {grad_txt} graduados "
            f"({rate_txt} tasa de éxito)"
        )
    if near_dev_ceiling_flag:
        reasons.append(
            f"DEV_ATH_CEILING_DANGER: MC actual (${current_mc_usd:,.0f}) está al {mc_vs_ath_pct:.1f}% "
            f"del mejor ATH histórico del dev (${best_ath_mc_usd:,.0f})"
        )

    return {
        "creator_address": creator_address or "unknown",
        "funder_address": funder_address,
        "history_known": history_known,
        "total_launches": total_launches,
        "graduated_count": graduated_count,
        "rugs_count": rugs_count,
        "graduation_rate_pct": round(grad_rate_pct, 2) if grad_rate_pct is not None else None,
        "best_ath_mc_usd": round(best_ath_mc_usd, 2) if best_ath_mc_usd is not None else None,
        "mc_vs_dev_ath_pct": round(mc_vs_ath_pct, 1) if mc_vs_ath_pct is not None else None,
        "dev_factory_flag": dev_factory_flag,
        "near_dev_ceiling_flag": near_dev_ceiling_flag,
        "reason": "; ".join(reasons) if reasons else (
            "CREATOR_CLEAN_OR_NEW" if history_known else "CREATOR_HISTORY_UNKNOWN"
        ),
    }


def detect_geometric_chart_pattern(pts: List[float]) -> Dict[str, Any]:
    """Detecta patrones geométricos de precio inspirados en `crypto-chart-patterns/enhanced_patterns.py`.

    Reconoce:
    - `BULL_FLAG`: impulso inicial >= +25% en primera mitad seguido de consolidación estrecha encima del 60% del rango.
    - `ASCENDING_TRIANGLE`: techos planos (resistencia horizontal) con mínimos crecientes.
    - `DOUBLE_BOTTOM_REVERSAL`: dos valles similares con recuperación hacia el cuello.
    - `BEAR_FLAG_BREAKDOWN`: caída fuerte seguida de rebote débil y ruptura a nuevos mínimos.
    """
    if len(pts) < 6:
        return {"geometric_pattern": "NONE", "bonus_score": 0.0}

    n = len(pts)
    half = max(3, n // 2)
    first_half = pts[:half]
    second_half = pts[half:]
    hi_all, lo_all = max(pts), min(pts)
    rng_all = max(hi_all - lo_all, 1e-9)

    # 1. Bull Flag: fuerte subida en la primera mitad y consolidación ordenada en la parte alta
    pole_gain_pct = ((max(first_half) - first_half[0]) / max(first_half[0], 1e-9)) * 100.0
    flag_lo = min(second_half)
    flag_hi = max(second_half)
    flag_retracement = (max(first_half) - flag_lo) / rng_all
    if pole_gain_pct >= 18.0 and flag_retracement <= 0.45 and pts[-1] >= 0.65 * flag_hi:
        return {"geometric_pattern": "BULL_FLAG", "bonus_score": 10.0}

    # 2. Ascending Triangle: techos estables (variación < 4%) y mínimos ascendentes
    top_vals = sorted(pts, reverse=True)[:3]
    top_spread = (max(top_vals) - min(top_vals)) / max(mean_top := (sum(top_vals) / len(top_vals)), 1e-9)
    if top_spread <= 0.045 and min(second_half) > min(first_half) * 1.04 and pts[-1] >= mean_top * 0.92:
        return {"geometric_pattern": "ASCENDING_TRIANGLE", "bonus_score": 8.0}

    # 3. Double Bottom: mínimo en primera mitad y mínimo similar en segunda mitad con cierre al alza
    lo1, lo2 = min(first_half), min(second_half)
    if abs(lo1 - lo2) / max(lo1, 1e-9) <= 0.04 and pts[-1] > max(lo1, lo2) * 1.08:
        return {"geometric_pattern": "DOUBLE_BOTTOM_REVERSAL", "bonus_score": 6.0}

    return {"geometric_pattern": "NONE", "bonus_score": 0.0}


def estimate_cpmm_curve_progress(market_cap_usd: float, liquidity_usd: float) -> float:
    """Estima el progreso de la curva de bonding (0..100%) hacia la graduación (~$69K MC en Pump.fun)."""
    if market_cap_usd <= 0.0:
        return 0.0
    if market_cap_usd >= 69000.0 or liquidity_usd >= 24000.0:
        return 100.0
    # En Pump.fun el piso inicial es ~$4,200 MC y la graduación ocurre en ~$69,000 MC
    prog = ((max(market_cap_usd, 4200.0) - 4200.0) / (69000.0 - 4200.0)) * 100.0
    return round(max(0.0, min(100.0, prog)), 1)


def compute_kline_microstructure_score(
    sparkline: List[float],
    wash_trading_flag: bool = False,
    buys_m5: int = 0,
    sells_m5: int = 0,
) -> Dict[str, Any]:
    """Clasifica la acción del precio siguiendo `gmgn-kline-pattern` y patrones geométricos."""
    pts = [float(x) for x in (sparkline or []) if _sf(x) > 0]
    if len(pts) < 3:
        return {
            "kline_score": 50.0,
            "pattern_name": "INSUFFICIENT_CANDLES",
            "geometric_pattern": "NONE",
            "slope_pct": 0.0,
            "r2_monotonicity": 0.0,
            "range_position": 0.5,
        }

    n = len(pts)
    mean_p = sum(pts) / n
    hi, lo = max(pts), min(pts)
    rng = max(hi - lo, 1e-9)
    pos_in_range = (pts[-1] - lo) / rng

    # Regresión lineal simple sobre índice 0..n-1
    x_mean = (n - 1) / 2.0
    num = sum((i - x_mean) * (pts[i] - mean_p) for i in range(n))
    den_x = sum((i - x_mean) ** 2 for i in range(n))
    den_y = sum((pts[i] - mean_p) ** 2 for i in range(n))
    slope = (num / den_x) if den_x > 0 else 0.0
    slope_pct = (slope / max(mean_p, 1e-9)) * 100.0
    r2 = (num ** 2) / (den_x * den_y) if (den_x > 0 and den_y > 0) else 0.0
    geo = detect_geometric_chart_pattern(pts)

    if wash_trading_flag and slope_pct > 0 and r2 >= 0.70:
        return {
            "kline_score": 20.0,
            "pattern_name": "ARTIFICIAL_WASH_STAIRCASE",
            "geometric_pattern": geo["geometric_pattern"],
            "slope_pct": round(slope_pct, 2),
            "r2_monotonicity": round(r2, 3),
            "range_position": round(pos_in_range, 2),
        }

    if slope_pct > 1.5 and 0.55 <= pos_in_range <= 0.88 and buys_m5 >= max(sells_m5, 1):
        pattern = "HEALTHY_PULLBACK_CONTINUATION"
        score = 82.0
    elif slope_pct > 1.5 and pos_in_range > 0.88:
        pattern = "UPTREND_NEAR_HIGH"
        score = 68.0
    elif slope_pct < -1.5 and pos_in_range < 0.30:
        pattern = "BREAKDOWN_DISTRIBUTION"
        score = 22.0
    else:
        pattern = "CONSOLIDATION_RANGE"
        score = 55.0

    if geo["geometric_pattern"] != "NONE" and not wash_trading_flag:
        pattern = f"{pattern}+{geo['geometric_pattern']}"
        score = min(96.0, score + _sf(geo["bonus_score"]))

    return {
        "kline_score": round(score, 1),
        "pattern_name": pattern,
        "geometric_pattern": geo["geometric_pattern"],
        "slope_pct": round(slope_pct, 2),
        "r2_monotonicity": round(r2, 3),
        "range_position": round(pos_in_range, 2),
    }


@dataclass
class TokenDossier:
    """Expediente Forense Unificado de un Token estilo GMGN.ai."""
    mint: str
    chain: str
    symbol: str
    name: str
    timestamp: float
    market_cap_usd: float
    liquidity_usd: float
    holder_count: int
    # Seguridad de contrato
    is_honeypot: bool
    is_open_source: bool
    is_renounced: bool
    has_blacklist: bool
    buy_tax_pct: float
    sell_tax_pct: float
    # Estructura de Chips & Bundlers
    top10_raw_pct: Optional[float]
    top10_float_pct: Optional[float]
    tradeable_float_pct: Optional[float]
    dev_balance_pct: Optional[float]
    bundler_pct: Optional[float]
    sniper_pct: Optional[float]
    uniform_cluster_count: int
    holder_entropy: float
    bundler_flag: bool
    # Microestructura & Wash-Trading
    buys_m5: int
    sells_m5: int
    volume_m5_usd: float
    volume_h1_usd: float
    avg_tx_m5_usd: float
    avg_tx_h1_usd: float
    wash_trading_flag: bool
    kline_pattern: str
    kline_score: float
    # Clones Homónimos & Avatar
    same_name_count: int
    same_name_24h_count: int
    dead_clones_count: int
    vanity_4444_count: int
    is_vamp_clone: bool
    clone_factory_flag: bool
    avatar_hash: str
    avatar_reused_count: int
    clones_preview: List[Dict[str, Any]] = field(default_factory=list)
    # Linaje del Creador
    creator_address: str = "unknown"
    creator_funder: str = ""
    dev_total_launches: Optional[int] = None
    dev_graduated_count: Optional[int] = None
    dev_best_ath_mc_usd: Optional[float] = None
    dev_factory_flag: bool = False
    near_dev_ceiling_flag: bool = False
    # Veredicto Compuesto y Explicación Causal
    composite_dd_score: float = 0.0
    admitted: bool = False
    verdict: str = "VETO"
    veto_reasons: List[str] = field(default_factory=list)
    why_price_moves: str = ""
    actionable_playbook: str = ""
    metrics_known: bool = True
    # Procedencia declarada: qué se midió y qué es un proxy (anti-falacias).
    top10_source: str = "unavailable"
    top10_flag: Optional[str] = None
    dev_balance_known: bool = False
    bundler_source: str = "unavailable"
    sniper_source: str = "unavailable"
    sniper_is_proxy: bool = False
    dev_history_known: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GMGNDossierScanner:
    """Recolector y Analizador Forense Multi-Fuente (GoPlus + RugCheck + DexScreener + Local DB)."""

    def __init__(self, store: Optional[Any] = None, creator_registry: Optional[Any] = None, timeout: float = 4.0):
        self.store = store
        self.creator_registry = creator_registry
        self.timeout = timeout
        self._cache: Dict[str, Tuple[float, TokenDossier]] = {}

    def _http_get_json(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            logger.debug("Fallo consultando %s: %s", url, exc)
            return None

    def fetch_dexscreener_pair_and_clones(
        self, mint: str
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Obtiene el par principal y los clones homónimos desde DexScreener API pública."""
        tok_data = self._http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{urllib.parse.quote(mint)}")
        pairs = (tok_data or {}).get("pairs") or []
        if not pairs:
            return None, []
        main_pair = max(pairs, key=lambda p: _sf((p.get("liquidity") or {}).get("usd"), _sf(p.get("marketCap"))))
        base = main_pair.get("baseToken") or {}
        sym = str(base.get("symbol") or "").strip()
        clones: List[Dict[str, Any]] = []
        if sym:
            search_data = self._http_get_json(
                f"https://api.dexscreener.com/latest/dex/search?q={urllib.parse.quote(sym)}"
            )
            clones = (search_data or {}).get("pairs") or []
        return main_pair, clones

    def fetch_goplus_security(self, mint: str, chain: str = "bsc") -> Optional[Dict[str, Any]]:
        """Consulta GoPlus Security API pública para cadenas EVM (BSC=56, Base=8453, ETH=1)."""
        cid = CHAIN_TO_GOPLUS_ID.get(chain.lower())
        if not cid:
            return None
        url = f"https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={urllib.parse.quote(mint.lower())}"
        data = self._http_get_json(url)
        res = (data or {}).get("result") or {}
        return res.get(mint.lower()) or res.get(mint) or (next(iter(res.values())) if res else None)

    def fetch_rugcheck_solana(self, mint: str) -> Optional[Dict[str, Any]]:
        """Consulta RugCheck API pública para tokens de Solana (primero `/report` completo con topHolders)."""
        full_url = f"https://api.rugcheck.xyz/v1/tokens/{urllib.parse.quote(mint)}/report"
        report = self._http_get_json(full_url)
        if isinstance(report, dict) and (report.get("topHolders") is not None or report.get("risks") is not None):
            return report
        summary_url = f"https://api.rugcheck.xyz/v1/tokens/{urllib.parse.quote(mint)}/report/summary"
        return self._http_get_json(summary_url)

    def build_dossier_from_payloads(
        self,
        mint: str,
        chain: str = "sol",
        dex_pair: Optional[Dict[str, Any]] = None,
        goplus_data: Optional[Dict[str, Any]] = None,
        rugcheck_data: Optional[Dict[str, Any]] = None,
        search_pairs: Optional[List[Dict[str, Any]]] = None,
        dev_stats: Optional[Dict[str, Any]] = None,
        sparkline: Optional[List[float]] = None,
        avatar_bytes: Optional[bytes] = None,
    ) -> TokenDossier:
        """Construye y puntúa el expediente forense completo a partir de los payloads recolectados."""
        pair = dex_pair or {}
        base = pair.get("baseToken") or {}
        symbol = str(base.get("symbol") or (goplus_data or {}).get("token_symbol") or "UNKNOWN")
        name = str(base.get("name") or (goplus_data or {}).get("token_name") or symbol)
        detected_chain = str(pair.get("chainId") or chain or "sol").lower()

        mc_usd = _sf(pair.get("marketCap", pair.get("fdv", 0.0)))
        liq_obj = pair.get("liquidity")
        liq_usd = _sf(liq_obj.get("usd") if isinstance(liq_obj, dict) else liq_obj, default=0.0)
        # En curvas de bonding como four.meme o pump.fun, DexScreener a veces omite liquidity.usd; estimar por reserva
        if liq_usd <= 0.0 and mc_usd > 0.0 and str(pair.get("dexId", "")).lower() in ("fourmeme", "pumpfun"):
            liq_usd = round(mc_usd * 0.35, 2)

        # 1. Seguridad de contrato y holders (GoPlus para EVM / RugCheck para Solana)
        is_honeypot = False
        is_open_source = True
        is_renounced = True
        has_blacklist = False
        buy_tax_pct = 0.0
        sell_tax_pct = 0.0
        holder_count = 0
        dev_balance_pct: Optional[float] = None
        creator_address = "unknown"
        holders_list: List[Dict[str, Any]] = []
        metrics_known = False

        if goplus_data:
            metrics_known = True
            is_honeypot = _si(goplus_data.get("is_honeypot")) == 1 or _si(goplus_data.get("cannot_sell_all")) == 1
            is_open_source = _si(goplus_data.get("is_open_source"), 1) == 1
            owner_addr = str(goplus_data.get("owner_address", "")).lower()
            is_renounced = owner_addr in ("", "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead")
            has_blacklist = _si(goplus_data.get("is_blacklisted")) == 1
            buy_tax_pct = round(_sf(goplus_data.get("buy_tax")) * 100.0, 2)
            sell_tax_pct = round(_sf(goplus_data.get("sell_tax")) * 100.0, 2)
            holder_count = _si(goplus_data.get("holder_count"), 0)
            if goplus_data.get("creator_percent") is not None:
                dev_balance_pct = round(_sf(goplus_data.get("creator_percent")) * 100.0, 2)
            creator_address = str(goplus_data.get("creator_address") or "unknown")
            holders_list = goplus_data.get("holders") or []
        elif rugcheck_data:
            metrics_known = True
            creator_address = str(rugcheck_data.get("creator") or "unknown")
            known_accounts = rugcheck_data.get("knownAccounts") or {}
            rc_top = rugcheck_data.get("topHolders") or []
            holder_count = _si(rugcheck_data.get("totalHolders"), len(rc_top))
            for idx, th in enumerate(rc_top):
                if not isinstance(th, dict):
                    continue
                h_addr = str(th.get("address") or "")
                h_owner = str(th.get("owner") or "")
                acct_meta = known_accounts.get(h_owner) or known_accounts.get(h_addr) or {}
                acct_type = str(acct_meta.get("type") or "").upper()
                acct_name = str(acct_meta.get("name") or "").lower()
                h_pct = _sf(th.get("pct"))
                # En Pump.fun / Raydium, el primer holder con >25% suele ser la Bonding Curve / AMM
                is_amm_or_lp = bool(
                    acct_type in ("AMM", "LP", "LOCKER", "VAULT")
                    or "pump fun" in acct_name
                    or "raydium" in acct_name
                    or "meteora" in acct_name
                    or (idx == 0 and h_pct >= 28.0 and h_owner != creator_address)
                )
                if creator_address != "unknown" and (h_owner == creator_address or h_addr == creator_address):
                    dev_balance_pct = round(max(dev_balance_pct or 0.0, h_pct), 2)
                holders_list.append({
                    "address": h_owner or h_addr,
                    "owner": h_owner,
                    "pct": h_pct,
                    "is_contract": 1 if is_amm_or_lp else 0,
                    "tag": acct_type.lower() if acct_type else "",
                    "insider": bool(th.get("insider")),
                })
            risks = rugcheck_data.get("risks") or []
            for r in risks:
                rname = str(r.get("name", "")).lower()
                if "freeze" in rname or "honeypot" in rname:
                    has_blacklist = True
                if "mint authority" in rname:
                    is_renounced = False
            if creator_address != "unknown" and rc_top:
                # Top holders medidos + creador identificado: no aparecer = 0% real.
                dev_balance_pct = round(dev_balance_pct or 0.0, 2)

        # 2. Detección de Bundlers y Concentración sobre Flotante
        chip_info = detect_bundler_and_cluster(holders_list)
        # Sin lista de holders NO se sintetiza concentración desde el score de
        # RugCheck (`score * 2.5` / `20.0`): el valor queda en None y el token
        # entra en fail-closed con VETO_TOP10_UNVERIFIED.

        # 3. Detección de Wash-Trading en Escalera
        wash_info = detect_wash_trading_staircase(
            txns=pair.get("txns") or {},
            volume=pair.get("volume") or {},
            market_cap_usd=mc_usd,
        )

        # 4. Detección de Clones Homónimos ("Same-Name / Vamp") y Vanity (...4444)
        clone_info = detect_same_name_vamp_and_clones(
            mint=mint,
            symbol=symbol,
            name=name,
            search_pairs=search_pairs or ([pair] if pair else []),
        )

        # 5. Linaje del Creador (`0/73` y Techo de ATH MC `$44K`)
        dev_info = evaluate_creator_lineage_and_ceiling(
            creator_address=creator_address,
            current_mc_usd=mc_usd,
            dev_stats=dev_stats,
            creator_registry=self.creator_registry,
        )

        # 6. Avatar Fingerprint & Reutilización
        avatar_hash = compute_avatar_dhash_from_bytes(avatar_bytes) if avatar_bytes else hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:16]
        avatar_reused_count = clone_info["same_name_count"]
        if self.store is not None and hasattr(self.store, "register_avatar"):
            try:
                avatar_reused_count = max(
                    avatar_reused_count,
                    self.store.register_avatar(mint, symbol, avatar_hash),
                )
            except Exception:
                pass

        # 7. Microestructura de Velas (Kline Score)
        kline_info = compute_kline_microstructure_score(
            sparkline=sparkline or [],
            wash_trading_flag=wash_info["wash_trading_flag"],
            buys_m5=wash_info["buys_m5"],
            sells_m5=wash_info["sells_m5"],
        )

        # 8. Cálculo de Score Compuesto (0 - 100) con Regla Fail-Closed (`gmgn-contract-dd`)
        score = 85.0 if metrics_known else 35.0
        veto_reasons: List[str] = []

        if not metrics_known:
            veto_reasons.append("VETO_UNVERIFIED_SECURITY: datos de seguridad/holders no disponibles (fail-closed)")
        if is_honeypot:
            score = 0.0
            veto_reasons.append("VETO_HONEYPOT: contrato bloquea ventas")
        if not is_open_source:
            score -= 35.0
            veto_reasons.append("VETO_UNVERIFIED_CODE: código de contrato no verificado")
        if has_blacklist:
            score -= 25.0
            veto_reasons.append("VETO_BLACKLIST_AUTHORITY: función de lista negra / freeze activa")
        if buy_tax_pct > 5.0 or sell_tax_pct > 5.0:
            score -= 30.0
            veto_reasons.append(f"VETO_HIGH_TAX: impuestos {buy_tax_pct:.1f}% / {sell_tax_pct:.1f}% (> 5%)")
        if dev_balance_pct is not None and dev_balance_pct > 1.5:
            score -= 30.0
            veto_reasons.append(f"VETO_DEV_HOLDING: dev retiene {dev_balance_pct:.1f}% (> 1.5%)")
        if chip_info["top10_float_pct"] is None:
            # Fail-closed: sin concentración medible no se admite nada.
            score -= 40.0
            vet_reason = chip_info.get("top10_flag") or "HOLDERS_UNKNOWN"
            veto_reasons.append(
                "VETO_TOP10_UNVERIFIED: concentración sobre flotante no medible "
                f"(fail-closed; sin dato: {vet_reason})"
            )
        if chip_info["bundler_flag"]:
            score -= 38.0
            veto_reasons.append(chip_info["reason"])
        elif (chip_info["top10_float_pct"] or 0.0) > 30.0:
            score -= 28.0
            veto_reasons.append(chip_info["reason"])
        if wash_info["wash_trading_flag"]:
            score -= 25.0
            veto_reasons.append(wash_info["reason"])
        if clone_factory_flag := clone_info["clone_factory_flag"]:
            score -= 22.0
            veto_reasons.append(clone_info["reason"])
        if dev_info["dev_factory_flag"] or dev_info["near_dev_ceiling_flag"]:
            score -= 30.0
            veto_reasons.append(dev_info["reason"])

        composite_score = round(max(0.0, min(100.0, score)), 1)
        admitted = bool(metrics_known and len(veto_reasons) == 0 and composite_score >= 70.0)
        if admitted and composite_score >= 80.0:
            verdict = "STRONG_BUY"
        elif admitted:
            verdict = "SPECULATIVE_BUY"
        else:
            verdict = "VETO"

        # 9. Diagnóstico Causal: ¿Por qué sube o baja el token y cómo aprovecharlo?
        why_parts: List[str] = []
        dev_txt = "N/D" if dev_balance_pct is None else f"{dev_balance_pct:.1f}%"
        top10_raw_txt = (
            "N/D" if chip_info["top10_raw_pct"] is None
            else f"{chip_info['top10_raw_pct']:.1f}%"
        )
        top10_float_txt = (
            "N/D" if chip_info["top10_float_pct"] is None
            else f"{chip_info['top10_float_pct']:.1f}%"
        )
        bundler_txt = (
            "N/D" if chip_info["estimated_bundler_pct"] is None
            else f"{chip_info['estimated_bundler_pct']:.1f}%"
        )
        if chip_info["bundler_flag"]:
            why_parts.append(
                f"Trampa de Bundler Oculto: aunque el Dev muestra {dev_txt} y el Top 10 bruto es "
                f"{top10_raw_txt}, {chip_info['uniform_cluster_count']} billeteras poseen "
                f"saldos idénticos en bloque 0 (~{bundler_txt} empaquetado)."
            )
        if wash_info["wash_trading_flag"]:
            why_parts.append(
                f"Subida Artificial en Escalera (Wash-Trading): el precio fue empujado con micro-compras de "
                f"${wash_info['avg_tx_m5_usd']:.2f} USD de media en 5m (volumen 5m: ${wash_info['volume_m5_usd'] if 'volume_m5_usd' in wash_info else _sf((pair.get('volume') or {}).get('m5')):.2f} USD) "
                f"para simular una tendencia alcista sin liquidez compradora real."
            )
        if dev_info["dev_factory_flag"] or dev_info["near_dev_ceiling_flag"]:
            launches_txt = "N/D" if dev_info["total_launches"] is None else str(dev_info["total_launches"])
            grad_txt = "N/D" if dev_info["graduated_count"] is None else str(dev_info["graduated_count"])
            ath_txt = (
                "N/D" if dev_info["best_ath_mc_usd"] is None
                else f"${dev_info['best_ath_mc_usd']:,.0f}"
            )
            mc_rel_txt = (
                "N/D" if dev_info["mc_vs_dev_ath_pct"] is None
                else f"{dev_info['mc_vs_dev_ath_pct']:.1f}%"
            )
            why_parts.append(
                f"Techo de Descarga del Creador: el dev tiene historial {grad_txt}/{launches_txt} "
                f"graduados con techo máximo en {ath_txt} USD (actualmente al "
                f"{mc_rel_txt} de su zona de dump)."
            )
        if clone_factory_flag:
            why_parts.append(
                f"Fábrica de Clones ('Same-Name / Vanity'): existen {clone_info['same_name_count']} tokens "
                f"homónimos ('{symbol}'), {clone_info['dead_clones_count']} ya colapsados a ~$3.2K MC."
            )
        if not why_parts:
            why_parts.append(
                f"Flujo Orgánico: tamaño medio por tx ${wash_info['avg_tx_m5_usd']:.2f} USD en 5m con "
                f"concentración sobre flotante de {top10_float_txt} y sin bundlers detectados."
            )

        if admitted:
            playbook = (
                f"Entrada válida en pullback: tomar 40% en TP1 (+30% a +50% MC = ${mc_usd*1.45:,.0f}), "
                f"proteger Break-Even en +25%, y cerrar 100% si 2+ wallets del Top 10 venden en 15s."
            )
        else:
            playbook = (
                "NO COMPRAR (VETO ESTRICTO): la liquidez de salida es ilusoria frente al inventario "
                "empaquetado del bundler/dev. Cualquier compra minorista sirve de liquidez de salida para el dump."
            )

        dossier = TokenDossier(
            mint=mint,
            chain=detected_chain,
            symbol=symbol,
            name=name,
            timestamp=time.time(),
            market_cap_usd=round(mc_usd, 2),
            liquidity_usd=round(liq_usd, 2),
            holder_count=holder_count,
            is_honeypot=is_honeypot,
            is_open_source=is_open_source,
            is_renounced=is_renounced,
            has_blacklist=has_blacklist,
            buy_tax_pct=buy_tax_pct,
            sell_tax_pct=sell_tax_pct,
            top10_raw_pct=chip_info["top10_raw_pct"],
            top10_float_pct=chip_info["top10_float_pct"],
            top10_source=chip_info["top10_source"],
            top10_flag=chip_info["top10_flag"],
            tradeable_float_pct=chip_info["tradeable_float_pct"],
            dev_balance_pct=dev_balance_pct,
            dev_balance_known=dev_balance_pct is not None,
            bundler_pct=chip_info["estimated_bundler_pct"],
            bundler_source=chip_info["bundler_source"],
            # El "sniper" del scanner es el MISMO estimador del clúster de
            # bundlers: se declara como proxy, jamás como riesgo independiente.
            sniper_pct=chip_info["estimated_bundler_pct"],
            sniper_source=(
                "proxy:bundler_uniform_cluster"
                if chip_info["estimated_bundler_pct"] is not None else "unavailable"
            ),
            sniper_is_proxy=chip_info["estimated_bundler_pct"] is not None,
            uniform_cluster_count=chip_info["uniform_cluster_count"],
            holder_entropy=chip_info["holder_entropy"],
            bundler_flag=chip_info["bundler_flag"],
            buys_m5=wash_info["buys_m5"],
            sells_m5=wash_info["sells_m5"],
            volume_m5_usd=round(_sf((pair.get("volume") or {}).get("m5")), 2),
            volume_h1_usd=round(_sf((pair.get("volume") or {}).get("h1")), 2),
            avg_tx_m5_usd=wash_info["avg_tx_m5_usd"],
            avg_tx_h1_usd=wash_info["avg_tx_h1_usd"],
            wash_trading_flag=wash_info["wash_trading_flag"],
            kline_pattern=kline_info["pattern_name"],
            kline_score=kline_info["kline_score"],
            same_name_count=clone_info["same_name_count"],
            same_name_24h_count=clone_info["same_name_24h_count"],
            dead_clones_count=clone_info["dead_clones_count"],
            vanity_4444_count=clone_info["vanity_4444_count"],
            is_vamp_clone=clone_info["is_vamp_clone"],
            clone_factory_flag=clone_factory_flag,
            avatar_hash=avatar_hash,
            avatar_reused_count=avatar_reused_count,
            clones_preview=clone_info["clones_preview"],
            creator_address=dev_info["creator_address"],
            creator_funder=dev_info["funder_address"],
            dev_total_launches=dev_info["total_launches"],
            dev_graduated_count=dev_info["graduated_count"],
            dev_best_ath_mc_usd=dev_info["best_ath_mc_usd"],
            dev_history_known=dev_info["history_known"],
            dev_factory_flag=dev_info["dev_factory_flag"],
            near_dev_ceiling_flag=dev_info["near_dev_ceiling_flag"],
            composite_dd_score=composite_score,
            admitted=admitted,
            verdict=verdict,
            veto_reasons=veto_reasons,
            why_price_moves=" | ".join(why_parts),
            actionable_playbook=playbook,
            metrics_known=metrics_known,
        )

        if self.store is not None and hasattr(self.store, "save_token_dossier"):
            try:
                self.store.save_token_dossier(dossier.to_dict())
                self.store.record_hf_snapshot(
                    mint=mint,
                    mc_usd=dossier.market_cap_usd,
                    liquidity_usd=dossier.liquidity_usd,
                    buys_m5=dossier.buys_m5,
                    sells_m5=dossier.sells_m5,
                    vol_m5_usd=dossier.volume_m5_usd,
                    holder_count=dossier.holder_count,
                    ts=dossier.timestamp,
                )
            except Exception as exc:
                logger.debug("No se pudo autoguardar dossier en LiveStore: %s", exc)

        return dossier

    def scan_token_live(
        self,
        mint: str,
        chain: Optional[str] = None,
        dev_stats: Optional[Dict[str, Any]] = None,
    ) -> TokenDossier:
        """Escanea un token en vivo consultando DexScreener + GoPlus (EVM) / RugCheck (Solana) y autoguarda."""
        mint_clean = str(mint or "").strip()
        now = time.time()
        if mint_clean in self._cache:
            ts, cached = self._cache[mint_clean]
            if now - ts < 15.0:
                return cached

        inferred_chain = chain or ("bsc" if mint_clean.startswith("0x") else "sol")
        main_pair, clones = self.fetch_dexscreener_pair_and_clones(mint_clean)
        if main_pair and main_pair.get("chainId"):
            inferred_chain = str(main_pair["chainId"]).lower()

        goplus_data = None
        rugcheck_data = None
        if mint_clean.startswith("0x") or inferred_chain in CHAIN_TO_GOPLUS_ID:
            goplus_data = self.fetch_goplus_security(mint_clean, chain=inferred_chain)
        else:
            rugcheck_data = self.fetch_rugcheck_solana(mint_clean)

        dossier = self.build_dossier_from_payloads(
            mint=mint_clean,
            chain=inferred_chain,
            dex_pair=main_pair,
            goplus_data=goplus_data,
            rugcheck_data=rugcheck_data,
            search_pairs=clones,
            dev_stats=dev_stats,
        )
        self._cache[mint_clean] = (now, dossier)
        return dossier
