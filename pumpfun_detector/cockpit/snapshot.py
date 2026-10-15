"""Construye el snapshot de observabilidad (SOLO LECTURA) para el cockpit.

Hace UN escaneo de la base canónica y produce un JSON con:
- Agregados reales del universo (tokens, % que llega a +50%, winrate real).
- El **análisis de sesgo**: el mismo "filtro" CON y SIN la condición futura,
  para que se vea en vivo cómo el edge colapsa (ver STATUS.md §7).
- Umbrales vigentes de ``MasterSnipeSignal`` y arquetipos de ``PatternIdentifier``.
- Una muestra de puntos 3D: (firstMC, firstLiq, MC_final, hit50, has_paper).

Nunca escribe en la base. Abre con ``mode=ro`` + ``query_only``.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DB = r"D:\hjh\pumpscope\db\custom.db"
SNAPSHOT_SCHEMA = 1

# Umbrales del filtro "Rama A" tal cual el informe auditado (incluye la
# condición FUTURA marketCap>=4500 que hay que visualizar como trampa).
RAMAA_FMC_MAX = 4285.0
RAMAA_FLIQ_MAX = 1725.0
RAMAA_MC_FUTURE_MIN = 4500.0


def _open_ro(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=8000")
    try:
        con.execute("PRAGMA mmap_size=268435456")  # 256 MB: acelera el escaneo
    except sqlite3.Error:
        pass
    return con


def _scan_tokens(con: sqlite3.Connection) -> List[Tuple[str, float, float, float, bool]]:
    """Un escaneo de Token -> (mint, firstMC, firstLiq, MC_final, hit50)."""
    rows = con.execute(
        "SELECT mint, firstMarketCap, firstLiquidity, marketCap, rise50At FROM Token"
    ).fetchall()
    out: List[Tuple[str, float, float, float, bool]] = []
    for mint, fmc, fliq, mc, r50 in rows:
        out.append((
            str(mint) if mint is not None else "",
            float(fmc) if fmc is not None else 0.0,
            float(fliq) if fliq is not None else 0.0,
            float(mc) if mc is not None else 0.0,
            r50 is not None,
        ))
    return out


def _scan_paper(con: sqlite3.Connection) -> Dict[str, List[float]]:
    """mint -> lista de pnlPct de posiciones cerradas."""
    paper: Dict[str, List[float]] = {}
    for mint, pnl, status in con.execute("SELECT mint, pnlPct, status FROM PaperPosition"):
        if status is None or str(status).lower() == "open" or pnl is None:
            continue
        paper.setdefault(str(mint), []).append(float(pnl))
    return paper


def _paper_win(paper: Dict[str, List[float]], mint: str) -> Optional[bool]:
    vals = paper.get(mint)
    if not vals:
        return None
    return (sum(vals) / len(vals)) > 0.0


def _segment(label: str, tokens, paper, cond) -> Dict[str, Any]:
    grp = [t for t in tokens if cond(t)]
    n = len(grp)
    hit = sum(1 for t in grp if t[4])
    withp = [t for t in grp if t[0] in paper]
    wins = sum(1 for t in withp if _paper_win(paper, t[0]))
    return {
        "label": label,
        "n_tokens": n,
        "hit50_pct": round(hit / n * 100.0, 2) if n else 0.0,
        "n_with_paper": len(withp),
        "paper_wr_pct": round(wins / len(withp) * 100.0, 2) if withp else None,
    }


def _thresholds() -> Dict[str, Any]:
    try:
        from detector.master_signal import MasterSnipeSignal as M
        return {
            "MIN_MARKET_CAP_USD": M.MIN_MARKET_CAP_USD,
            "MAX_FIRST_MARKET_CAP_USD": M.MAX_FIRST_MARKET_CAP_USD,
            "MAX_FIRST_LIQUIDITY_USD": M.MAX_FIRST_LIQUIDITY_USD,
            "MAX_DEV_BALANCE_PCT": M.MAX_DEV_BALANCE_PCT,
            "MAX_MIGRATION_PROGRESS_PCT": M.MAX_MIGRATION_PROGRESS_PCT,
            "MIN_SOL_RESERVES": M.MIN_SOL_RESERVES,
            "STALL_TIMEOUT_SECONDS": M.STALL_TIMEOUT_SECONDS,
            "TAKE_PROFIT_PCT": M.TAKE_PROFIT_PCT,
            "STOP_LOSS_NOMINAL_PCT": M.STOP_LOSS_NOMINAL_PCT,
            "DISABLED_BRANCHES": sorted(M.DISABLED_BRANCHES),
            "metrics_status": M.BRANCH_METRICS_STATUS,
        }
    except Exception as exc:  # pragma: no cover - defensivo
        return {"error": f"{type(exc).__name__}: {exc}"}


def _archetypes() -> List[Dict[str, Any]]:
    try:
        from detector.pattern_identifier import PatternIdentifier as P
        return [
            {
                "name": name,
                "category": a["category"],
                "expected_wr": a["expected_wr"],
                "expected_ev": a["expected_ev"],
                "action": a["action"],
                "status": P.ARCHETYPE_METRICS_STATUS,
            }
            for name, a in P.ARCHETYPES.items()
        ]
    except Exception as exc:  # pragma: no cover - defensivo
        return [{"error": f"{type(exc).__name__}: {exc}"}]


def _sample_points(tokens, paper, limit: int, seed: int = 7) -> Tuple[List[List[Any]], List[Dict[str, Any]]]:
    """Muestra reproducible de puntos para el visor 3D.

    Cada punto: [firstMC, firstLiq, MC_final, hit50(0/1), has_paper(0/1), paper_win(-1/0/1)]
    """
    rnd = random.Random(seed)
    pool = tokens if len(tokens) <= limit else rnd.sample(tokens, limit)
    pts: List[List[Any]] = []
    meta: List[Dict[str, Any]] = []
    for mint, fmc, fliq, mc, hit in pool:
        w = _paper_win(paper, mint)
        pnl_vals = paper.get(mint, [])
        avg_pnl = round(sum(pnl_vals) / len(pnl_vals), 2) if pnl_vals else None
        pts.append([
            round(fmc, 2), round(fliq, 2), round(mc, 2),
            1 if hit else 0, 0 if w is None else 1,
            -1 if w is None else (1 if w else 0),
        ])
        meta.append({
            "mint": mint,
            "pnl": avg_pnl,
        })
    return pts, meta


def build_snapshot(
    db_path: str = DEFAULT_DB,
    *,
    sample: int = 3000,
    live_store_db: Optional[str] = None,
) -> Dict[str, Any]:
    """Escanea la base (solo lectura) y devuelve el snapshot completo."""
    snap: Dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"db": os.path.basename(db_path), "read_only": True},
        "ok": False,
        "error": None,
    }
    if not os.path.exists(db_path):
        snap["error"] = f"base no encontrada: {db_path}"
        return snap

    t0 = time.time()
    try:
        con = _open_ro(db_path)
        try:
            tokens = _scan_tokens(con)
            paper = _scan_paper(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        snap["error"] = f"sqlite: {exc}"
        return snap

    snap["scan_seconds"] = round(time.time() - t0, 2)
    n_all = len(tokens)
    r50 = sum(1 for t in tokens if t[4])
    all_pnl = [p for v in paper.values() for p in v]
    wins = sum(1 for p in all_pnl if p > 0)

    snap["universe"] = {
        "n_tokens": n_all,
        "n_paper_positions": len(all_pnl),
        "n_paper_mints": len(paper),
        "rise50_pct": round(r50 / n_all * 100.0, 2) if n_all else 0.0,
        "paper_wr_pct": round(wins / len(all_pnl) * 100.0, 2) if all_pnl else None,
        "paper_avg_pnl_pct": round(sum(all_pnl) / len(all_pnl), 2) if all_pnl else None,
    }

    snap["bias"] = [
        _segment(
            "CON condición futura (MC final >= 4500) — ILUSIÓN",
            tokens, paper,
            lambda t: t[1] > 0 and t[1] <= RAMAA_FMC_MAX and t[2] > 0 and t[2] <= RAMAA_FLIQ_MAX
            and t[3] >= RAMAA_MC_FUTURE_MIN,
        ),
        _segment(
            "SIN condición futura (solo firstMC/firstLiq) — REAL",
            tokens, paper,
            lambda t: t[1] > 0 and t[1] <= RAMAA_FMC_MAX and t[2] > 0 and t[2] <= RAMAA_FLIQ_MAX,
        ),
        _segment("Universo total", tokens, paper, lambda t: True),
    ]

    snap["thresholds"] = _thresholds()
    snap["archetypes"] = _archetypes()
    pts, pts_meta = _sample_points(tokens, paper, sample)
    snap["points"] = pts
    snap["point_meta"] = pts_meta
    snap["ok"] = True

    if live_store_db and os.path.exists(live_store_db):
        snap["live"] = _live_audit(live_store_db)
    return snap


def evaluate_custom_filter(
    db_path: str = DEFAULT_DB,
    *,
    fmc_min: float = 0.0,
    fmc_max: float = 4285.0,
    fliq_min: float = 0.0,
    fliq_max: float = 1725.0,
    min_final_mc: Optional[float] = None,
    min_liq_ratio: float = 0.0,
    tokens: Optional[List[Tuple[str, float, float, float, bool]]] = None,
    paper: Optional[Dict[str, List[float]]] = None,
) -> Dict[str, Any]:
    """Evalúa un filtro sobre la base SQLite o colecciones en memoria (solo lectura).

    Calcula métricas reales:
    - Tokens que cumplen el filtro vs Universo total
    - Tasa de llegada a +50% e intervalo de confianza del 95% (Wilson / SE)
    - Winrate y PnL medio en PaperTrading
    - Expected Value (EV) con el modelo de riesgo Antigravity (-33.49% real SL)
    - Criterio de Kelly (fracción óptima recomendada de asignación)
    - Comparativa diferencial de edge vs Universo total
    - Detección de sesgo de anticipación (lookahead)
    - Validación estadística (N >= 30, regla BP-003)
    """
    import math

    if fmc_min > fmc_max:
        return {"ok": False, "error": f"fmc_min ({fmc_min}) no puede ser mayor que fmc_max ({fmc_max})"}
    if fliq_min > fliq_max:
        return {"ok": False, "error": f"fliq_min ({fliq_min}) no puede ser mayor que fliq_max ({fliq_max})"}

    t0 = time.time()
    if tokens is None or paper is None:
        if not os.path.exists(db_path):
            return {"ok": False, "error": f"base no encontrada: {db_path}"}
        try:
            con = _open_ro(db_path)
            try:
                tokens = _scan_tokens(con)
                paper = _scan_paper(con)
            finally:
                con.close()
        except sqlite3.Error as exc:
            return {"ok": False, "error": f"sqlite: {exc}"}

    def _matches(t) -> bool:
        mint, fmc, fliq, mc, hit = t
        # Excluir registros con variables observables faltantes (0 o negativas)
        if fmc <= 0 or fliq <= 0:
            return False
        if fmc < fmc_min or fmc > fmc_max:
            return False
        if fliq < fliq_min or fliq > fliq_max:
            return False
        if min_liq_ratio > 0 and (fliq / fmc) < min_liq_ratio:
            return False
        if min_final_mc is not None and min_final_mc > 0:
            if mc < min_final_mc:
                return False
        return True

    matched = [t for t in tokens if _matches(t)]
    n = len(matched)
    n_total = len(tokens)
    hit50 = sum(1 for t in matched if t[4])
    hit50_pct = round(hit50 / n * 100.0, 2) if n else 0.0

    # Intervalo de confianza al 95% para la tasa de +50%
    ci95 = None
    if n > 0:
        p_val = hit50 / n
        se = math.sqrt(p_val * (1.0 - p_val) / n) if n > 1 else 0.0
        ci_low = round(max(0.0, (p_val - 1.96 * se) * 100.0), 2)
        ci_high = round(min(100.0, (p_val + 1.96 * se) * 100.0), 2)
        ci95 = [ci_low, ci_high]

    with_paper = [t for t in matched if t[0] in paper]
    wins = sum(1 for t in with_paper if _paper_win(paper, t[0]))
    all_pnls = [p for t in with_paper for p in paper.get(t[0], [])]

    wr_paper = (wins / len(with_paper) * 100.0) if with_paper else None
    avg_pnl = (sum(all_pnls) / len(all_pnls)) if all_pnls else None

    # Expected Value bajo protocolo Antigravity (slippage real -33.49%, fees -1.5%, TP 25%)
    ev_pct = None
    kelly_pct = None
    if wr_paper is not None:
        p_win = wr_paper / 100.0
        ev_pct = round((p_win * 25.0) - ((1.0 - p_win) * 33.49) - 1.5, 2)
        # Criterio de Kelly: b = TP / SL = 25.0 / 33.49 ≈ 0.7465
        b_ratio = 25.0 / 33.49
        q_loss = 1.0 - p_win
        f_star = (p_win * b_ratio - q_loss) / b_ratio
        kelly_pct = round(max(0.0, f_star * 100.0), 2)

    has_lookahead = min_final_mc is not None and min_final_mc > 0

    # Métricas de referencia del Universo total con datos válidos
    valid_universe = [t for t in tokens if t[1] > 0 and t[2] > 0]
    univ_n = len(valid_universe)
    univ_hit = sum(1 for t in valid_universe if t[4])
    univ_hit_pct = round(univ_hit / univ_n * 100.0, 2) if univ_n else 0.0
    univ_with_paper = [t for t in valid_universe if t[0] in paper]
    univ_wins = sum(1 for t in univ_with_paper if _paper_win(paper, t[0]))
    univ_wr = round(univ_wins / len(univ_with_paper) * 100.0, 2) if univ_with_paper else None

    edge_vs_universe = {
        "delta_hit50_pct": round(hit50_pct - univ_hit_pct, 2) if n else 0.0,
        "delta_wr_pct": round(wr_paper - univ_wr, 2) if (wr_paper is not None and univ_wr is not None) else None,
        "universe_hit50_pct": univ_hit_pct,
        "universe_wr_pct": univ_wr,
    }

    has_genuine_edge = (n >= 30) and (not has_lookahead) and (ev_pct is not None and ev_pct > 0)

    return {
        "ok": True,
        "duration_seconds": round(time.time() - t0, 3),
        "total_universe": n_total,
        "n_matched": n,
        "pct_universe": round(n / n_total * 100.0, 2) if n_total else 0.0,
        "hit50_count": hit50,
        "hit50_pct": hit50_pct,
        "hit50_ci95": ci95,
        "n_with_paper": len(with_paper),
        "paper_wr_pct": round(wr_paper, 2) if wr_paper is not None else None,
        "paper_avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else None,
        "ev_pct": ev_pct,
        "kelly_pct": kelly_pct,
        "edge_vs_universe": edge_vs_universe,
        "has_lookahead": has_lookahead,
        "is_statistically_significant": n >= 30,
        "has_genuine_edge": has_genuine_edge,
        "filter_params": {
            "fmc_min": fmc_min,
            "fmc_max": fmc_max,
            "fliq_min": fliq_min,
            "fliq_max": fliq_max,
            "min_final_mc": min_final_mc,
            "min_liq_ratio": min_liq_ratio,
        },
    }


def _live_audit(live_store_db: str) -> Dict[str, Any]:
    """Resumen del forward run (LiveStore) si existe."""
    try:
        con = _open_ro(live_store_db)
        try:
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM live_decisions")
            n_dec = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM live_outcomes")
            n_out = cur.fetchone()[0]
        finally:
            con.close()
        return {"db": os.path.basename(live_store_db), "decisions": n_dec, "outcomes": n_out}
    except sqlite3.Error as exc:
        return {"db": os.path.basename(live_store_db), "error": str(exc)}


def write_snapshot(snapshot: Dict[str, Any], out_path: str) -> str:
    """Escribe el snapshot de forma atómica."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False)
    os.replace(tmp, out_path)
    return out_path


def load_snapshot(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
