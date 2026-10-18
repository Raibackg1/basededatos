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

DEFAULT_DB = os.environ.get("PUMPSCOPE_DB", "custom.db")
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


# Columnas canónicas del universo PumpScope y fallbacks declarados.
TOKEN_COLUMNS_REQUIRED = ("firstMarketCap", "firstLiquidity", "marketCap", "rise50At")
TOKEN_COLUMN_FALLBACKS = {"marketCap": ("currentMarketCap",)}

# Rutas candidatas de la base, relativas a cada base de búsqueda.
_DB_CANDIDATES = (
    "custom.db",
    os.path.join("pumpfun_detector", "custom.db"),
    os.path.join("data", "custom.db"),
)


def resolve_db_path(explicit: Optional[str] = None) -> Dict[str, Any]:
    """Resuelve la base canónica SIN exigir configuración manual.

    Orden: ruta explícita existente -> ``$PUMPSCOPE_DB`` -> candidatos relativos
    al cwd, a la raíz del repo y al directorio del paquete. Devuelve
    ``{"path", "source", "tried", "exists"}``; ``path`` es absoluto o ``None``.
    """
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(pkg_parent)
    bases = [os.getcwd(), repo_root, pkg_parent]
    tried: List[str] = []

    def _candidate(raw: str, source: str) -> Optional[Dict[str, Any]]:
        path = raw if os.path.isabs(raw) else os.path.abspath(os.path.expanduser(raw))
        if path not in tried:
            tried.append(path)
        if os.path.exists(path):
            return {"path": path, "source": source, "tried": list(tried), "exists": True}
        return None

    if explicit:
        hit = _candidate(explicit, "explicit")
        if hit:
            return hit

    env_db = os.environ.get("PUMPSCOPE_DB")
    if env_db:
        hit = _candidate(env_db, "env:PUMPSCOPE_DB")
        if hit:
            return hit

    for base in bases:
        for rel in _DB_CANDIDATES:
            hit = _candidate(os.path.join(base, rel), f"candidate:{base}")
            if hit:
                return hit

    return {"path": None, "source": None, "tried": tried, "exists": False}


def effective_db(db_path: str = DEFAULT_DB) -> Tuple[str, Optional[str]]:
    """Resuelve la base SOLO cuando se está usando la ruta por defecto.

    Así ``python -m cockpit.cycle`` funciona desde cualquier cwd (el bug del
    ``.bat``) sin sorprender a quien pasa una ruta explícita: una ruta
    explícita que no existe sigue reportándose como error, nunca se sustituye
    en silencio por otra base.
    """
    if db_path != DEFAULT_DB or os.path.exists(db_path):
        return db_path, None
    hit = resolve_db_path(db_path)
    if hit.get("path"):
        return hit["path"], hit.get("source")
    return db_path, None


def schema_report(db_path: Optional[str]) -> Dict[str, Any]:
    """Qué tablas/columnas canónicas existen de verdad (para degradar con honestidad).

    Nunca asume: si ``Token`` es una vista parcial, lo declara y dice qué falta,
    en lugar de dejar que el escaneo reviente o de rellenar ceros silenciosos.
    """
    base = {
        "db": os.path.basename(db_path) if db_path else None,
        "available": False,
        "error": None,
        "has_token": False,
        "has_paper": False,
        "token_columns": [],
        "paper_columns": [],
        "missing_token_columns": list(TOKEN_COLUMNS_REQUIRED),
        "token_fallbacks": {},
        "universe_measurable": False,
        "rise50_measurable": False,
        "paper_measurable": False,
    }
    if not db_path or not os.path.exists(db_path):
        base["error"] = "base no encontrada"
        return base
    try:
        con = _open_ro(db_path)
        try:
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            has_token = "Token" in names
            has_paper = "PaperPosition" in names
            cols = sorted(_table_columns(con, "Token")) if has_token else []
            paper_cols = sorted(_table_columns(con, "PaperPosition")) if has_paper else []
        finally:
            con.close()
    except sqlite3.Error as exc:
        base["error"] = f"sqlite: {exc}"
        return base

    colset = set(cols)
    missing = [c for c in TOKEN_COLUMNS_REQUIRED if c not in colset]
    base.update({
        "available": True,
        "has_token": has_token,
        "has_paper": has_paper,
        "token_columns": cols,
        "paper_columns": paper_cols,
        "missing_token_columns": missing,
        "token_fallbacks": {
            c: [f for f in TOKEN_COLUMN_FALLBACKS.get(c, ()) if f in colset] for c in missing
        },
        "universe_measurable": has_token and {"firstMarketCap", "firstLiquidity"} <= colset,
        "rise50_measurable": has_token and "rise50At" in colset,
        "paper_measurable": has_paper and "pnlPct" in set(paper_cols),
    })
    return base


def _scan_tokens(con: sqlite3.Connection) -> List[Tuple[str, float, float, float, Optional[bool]]]:
    """Un escaneo de Token -> (mint, firstMC, firstLiq, MC_final, hit50).

    Adaptativo al esquema real: si falta ``marketCap`` usa ``currentMarketCap``
    como fallback declarado, y si falta ``rise50At`` el acierto a +50% viaja
    como ``None`` (no medible) en vez de inventar un ``0`` que se leería como
    "0% de acierto".
    """
    cols = _table_columns(con, "Token")
    if not cols:
        raise sqlite3.OperationalError("tabla Token ausente")
    mint_col = "mint" if "mint" in cols else ("id" if "id" in cols else None)
    fmc_col = "firstMarketCap" if "firstMarketCap" in cols else None
    fliq_col = "firstLiquidity" if "firstLiquidity" in cols else None
    mc_col = ("marketCap" if "marketCap" in cols
              else ("currentMarketCap" if "currentMarketCap" in cols else None))
    r50_col = "rise50At" if "rise50At" in cols else None
    if mint_col is None or fmc_col is None:
        # Sin identificador o sin mercado inicial no hay universo que medir.
        return []

    wanted = [mint_col, fmc_col]
    for extra in (fliq_col, mc_col, r50_col):
        if extra and extra not in wanted:
            wanted.append(extra)
    idx = {name: i for i, name in enumerate(wanted)}
    rows = con.execute(f"SELECT {', '.join(wanted)} FROM Token").fetchall()

    out: List[Tuple[str, float, float, float, Optional[bool]]] = []
    for row in rows:
        mint = row[idx[mint_col]]
        fused = _pfnum(row[idx[fmc_col]]) or 0.0
        fliq = _pfnum(row[idx[fliq_col]]) if fliq_col else None
        mc = _pfnum(row[idx[mc_col]]) if mc_col else None
        hit = (row[idx[r50_col]] is not None) if r50_col else None
        out.append((
            str(mint) if mint is not None else "",
            fused,
            fliq or 0.0,
            mc or 0.0,
            hit,
        ))
    return out


def _scan_paper(con: sqlite3.Connection) -> Dict[str, List[float]]:
    """mint -> lista de pnlPct de posiciones cerradas (tolerante al esquema).

    Reutiliza el escaneo robusto de operaciones cerradas para que un esquema
    parcial (sin ``reason``/``pnlUsd``) no rompa el universo.
    """
    paper: Dict[str, List[float]] = {}
    for trade in _scan_closed_trades(con):
        paper.setdefault(trade["mint"], []).append(trade["pnl_pct"])
    return paper


def _paper_win(paper: Dict[str, List[float]], mint: str) -> Optional[bool]:
    vals = paper.get(mint)
    if not vals:
        return None
    return (sum(vals) / len(vals)) > 0.0


# ── Analítica de rendimiento real (SOLO LECTURA) ───────────────────────────
PERF_SCHEMA = "performance/1"
PERF_PERIODS: Dict[str, Optional[int]] = {"all": None, "90d": 90, "30d": 30, "7d": 7}
PERF_INITIAL_USD = 10_000.0


def _pfnum(val: Any) -> Optional[float]:
    """Float finito o ``None`` (nunca NaN/inf: eso no es un dato medido)."""
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    if out == float("inf") or out == float("-inf"):
        return None
    return out


def _epoch_seconds(val: Any) -> Optional[float]:
    """Normaliza un timestamp epoch a SEGUNDOS, tolerando ms/µs/ns.

    La base canónica guarda ``exitTime`` en milisegundos; comparar crudo contra
    ``time.time()`` (segundos) haría que el filtro de período no filtrara nada.
    """
    v = _pfnum(val)
    if v is None:
        return None
    av = abs(v)
    if av >= 1e17:      # ns (1.79e18 ≈ hoy)
        return v / 1e9
    if av >= 1e14:      # µs (1.79e15 ≈ hoy)
        return v / 1e6
    if av >= 1e11:      # ms (1.79e12 ≈ hoy)
        return v / 1e3
    return v            # ya está en segundos


def _table_columns(con: sqlite3.Connection, table: str) -> set:
    """Columnas reales de una tabla (para tolerar esquemas parciales)."""
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _scan_closed_trades(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Operaciones cerradas (``status != OPEN``) con sus columnas reales.

    Tolerante a esquemas parciales: si ``PaperPosition`` no tiene ``reason`` o
    ``pnlUsd``, el campo viaja como ``None`` en vez de inventarse.
    """
    cols = _table_columns(con, "PaperPosition")
    if not cols or "pnlPct" not in cols:
        return []
    wanted = ("mint", "tokenId", "pnlPct", "pnlUsd", "sizeSol", "status",
              "reason", "exitTime", "entryTime")
    present = [c for c in wanted if c in cols]
    out: List[Dict[str, Any]] = []
    for row in con.execute(f"SELECT {', '.join(present)} FROM PaperPosition"):
        rec = dict(zip(present, row))
        status = rec.get("status")
        if status is not None and str(status).strip().upper() == "OPEN":
            continue
        pnl_pct = _pfnum(rec.get("pnlPct"))
        if pnl_pct is None:
            continue
        out.append({
            "mint": rec.get("mint") or rec.get("tokenId") or "",
            "pnl_pct": pnl_pct,
            "pnl_usd": _pfnum(rec.get("pnlUsd")),
            "size_sol": _pfnum(rec.get("sizeSol")),
            "reason": (str(rec["reason"]) if rec.get("reason") not in (None, "") else None),
            "exit_time": _epoch_seconds(rec.get("exitTime")),
            "entry_time": _epoch_seconds(rec.get("entryTime")),
        })
    return out


def compute_performance(
    rows: List[Dict[str, Any]],
    *,
    period: str = "all",
    now: Optional[float] = None,
    initial_usd: float = PERF_INITIAL_USD,
) -> Dict[str, Any]:
    """Agrega el rendimiento REAL de las operaciones paper ya cerradas.

    Función pura (sin IO). No proyecta rentabilidad futura ni promete edge:
    solo resume lo que ya ocurrió. Devuelve conteos, win rate medido, profit
    factor, ganancia/pérdida media, expectativa (EV por operación), curva de
    capital, drawdown máximo, histograma de PnL y desglose por motivo de salida.
    """
    import math

    if period not in PERF_PERIODS:
        return {"ok": False, "error": f"period inválido: {period}"}
    now_ts = float(time.time() if now is None else now)
    days = PERF_PERIODS[period]

    trades: List[Dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        pnl = _pfnum(r.get("pnl_pct"))
        if pnl is None:
            continue
        t_exit = _epoch_seconds(r.get("exit_time"))
        if days is not None and t_exit is not None and t_exit < now_ts - days * 86400.0:
            continue
        trades.append({
            "t": t_exit,
            "pnl_pct": pnl,
            "pnl_usd": _pfnum(r.get("pnl_usd")),
            "reason": r.get("reason") or None,
        })

    # Orden temporal determinista (las sin timestamp conservan su orden relativo).
    indexed = list(enumerate(trades))
    indexed.sort(key=lambda p: (p[1]["t"] is None, p[1]["t"] if p[1]["t"] is not None else p[0]))
    trades = [t for _, t in indexed]

    n = len(trades)
    result: Dict[str, Any] = {
        "ok": True,
        "schema": PERF_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "n_trades": n,
        "metrics_known": {
            "pnl_pct": n > 0,
            "pnl_usd": n > 0 and all(t["pnl_usd"] is not None for t in trades),
            "reason": n > 0 and any(t["reason"] for t in trades),
            "exit_time": n > 0 and all(t["t"] is not None for t in trades),
        },
    }

    empty_equity = {"unit": None, "initial": round(float(initial_usd), 2),
                    "points": [], "max_drawdown": None, "max_drawdown_pct": None}
    if n == 0:
        result.update({
            "wins": 0, "losses": 0, "breakeven": 0, "win_rate_pct": None,
            "gross_profit_pct": None, "gross_loss_pct": None, "profit_factor": None,
            "avg_win_pct": None, "avg_loss_pct": None, "expectancy_pct": None,
            "expectancy_usd": None, "net_pnl_pct": None, "net_pnl_usd": None,
            "return_pct": None, "equity": empty_equity, "histogram": [], "by_reason": [],
        })
        return result

    pnl_pcts = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnl_pcts if p > 0]
    losses = [p for p in pnl_pcts if p < 0]
    breakeven = n - len(wins) - len(losses)
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    n_usd = sum(1 for t in trades if t["pnl_usd"] is not None)
    sum_usd = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] is not None)

    avg_win = round(gross_profit / len(wins), 2) if wins else None
    avg_loss = round(gross_loss / len(losses), 2) if losses else None
    p_win = len(wins) / n
    # Expectativa (EV por operación) = p·ganancia media + (1−p)·pérdida media.
    ev_pct = round(
        p_win * (gross_profit / len(wins) if wins else 0.0)
        + (1.0 - p_win) * (gross_loss / len(losses) if losses else 0.0), 2)

    # Curva de capital: en USD si TODAS las operaciones tienen pnlUsd; si no,
    # acumulado en % (nunca mezclar unidades).
    use_usd = n_usd == n
    if use_usd:
        unit, base = "usd", float(initial_usd)
        values = [float(t["pnl_usd"]) for t in trades]
    else:
        unit, base = "pct", 0.0
        values = list(pnl_pcts)
    cum = 0.0
    peak = base
    max_dd = 0.0
    max_dd_pct = 0.0
    points: List[Dict[str, float]] = []
    for i, t in enumerate(trades):
        cum += values[i]
        equity = base + cum if use_usd else cum
        ts = t["t"] if t["t"] is not None else now_ts - (n - i) * 60.0
        points.append({"t": round(ts, 3), "value": round(equity, 2)})
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100.0)

    lo, hi = min(pnl_pcts), max(pnl_pcts)
    if hi == lo:
        bins = [{"from": round(lo, 2), "to": round(hi, 2), "count": n}]
    else:
        k = min(9, max(3, int(math.sqrt(n)) + 1))
        width = (hi - lo) / k
        bins = [{"from": round(lo + width * i, 2),
                 "to": round(lo + width * (i + 1), 2) if i < k - 1 else round(hi, 2),
                 "count": 0} for i in range(k)]
        for p in pnl_pcts:
            idx = min(k - 1, max(0, int((p - lo) / width)))
            bins[idx]["count"] += 1

    grouped: Dict[str, Dict[str, float]] = {}
    for t in trades:
        key = t["reason"] or "N/D"
        b = grouped.setdefault(key, {"count": 0.0, "total_pct": 0.0, "total_usd": 0.0, "n_usd": 0.0})
        b["count"] += 1
        b["total_pct"] += t["pnl_pct"]
        if t["pnl_usd"] is not None:
            b["total_usd"] += t["pnl_usd"]
            b["n_usd"] += 1
    by_reason = [
        {"reason": key, "count": int(b["count"]),
         "avg_pnl_pct": round(b["total_pct"] / b["count"], 2),
         "total_pnl_usd": round(b["total_usd"], 2) if b["n_usd"] else None}
        for key, b in grouped.items()
    ]
    by_reason.sort(key=lambda x: (-x["count"], x["reason"]))

    result.update({
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": breakeven,
        "win_rate_pct": round(p_win * 100.0, 2),
        "gross_profit_pct": round(gross_profit, 2),
        "gross_loss_pct": round(gross_loss, 2),
        "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "expectancy_pct": ev_pct,
        "expectancy_usd": round(sum_usd / n_usd, 2) if n_usd else None,
        "net_pnl_pct": round(sum(pnl_pcts), 2),
        "net_pnl_usd": round(sum_usd, 2) if n_usd else None,
        "return_pct": round(sum_usd / float(initial_usd) * 100.0, 2) if (n_usd and initial_usd) else None,
        "equity": {
            "unit": unit,
            "initial": round(base, 2),
            "points": points,
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
        },
        "histogram": bins,
        "by_reason": by_reason,
    })
    return result


def performance_report(
    db_path: str = DEFAULT_DB,
    *,
    period: str = "all",
    now: Optional[float] = None,
    initial_usd: float = PERF_INITIAL_USD,
) -> Dict[str, Any]:
    """Lee las operaciones cerradas de la base (solo lectura) y agrega el informe."""
    if not os.path.exists(db_path):
        return {"ok": False, "schema": PERF_SCHEMA, "error": f"base no encontrada: {db_path}"}
    try:
        con = _open_ro(db_path)
        try:
            rows = _scan_closed_trades(con)
        finally:
            con.close()
    except sqlite3.Error as exc:
        return {"ok": False, "schema": PERF_SCHEMA, "error": f"sqlite: {exc}"}
    report = compute_performance(rows, period=period, now=now, initial_usd=initial_usd)
    report["source"] = {"db": os.path.basename(db_path), "read_only": True}
    return report


def _segment(label: str, tokens, paper, cond) -> Dict[str, Any]:
    grp = [t for t in tokens if cond(t)]
    n = len(grp)
    measurable = [t for t in grp if t[4] is not None]
    hit = sum(1 for t in measurable if t[4])
    withp = [t for t in grp if t[0] in paper]
    wins = sum(1 for t in withp if _paper_win(paper, t[0]))
    return {
        "label": label,
        "n_tokens": n,
        "n_hit50_measurable": len(measurable),
        "hit50_pct": round(hit / len(measurable) * 100.0, 2) if measurable else None,
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
            -1 if hit is None else (1 if hit else 0),
            0 if w is None else 1,
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
    db_path, resolved_from = effective_db(db_path)
    snap: Dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"db": os.path.basename(db_path), "path": db_path,
                   "resolved_from": resolved_from, "read_only": True},
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
    measurable = [t for t in tokens if t[4] is not None]
    r50 = sum(1 for t in measurable if t[4])
    all_pnl = [p for v in paper.values() for p in v]
    wins = sum(1 for p in all_pnl if p > 0)

    # Diagnóstico del esquema real: si la base es parcial, el cockpit lo dice.
    snap["db_schema"] = schema_report(db_path)
    snap["universe"] = {
        "n_tokens": n_all,
        "n_tokens_hit50_measurable": len(measurable),
        "n_paper_positions": len(all_pnl),
        "n_paper_mints": len(paper),
        # ``None`` = no medible en esta base (no es lo mismo que 0% de acierto).
        "rise50_pct": round(r50 / len(measurable) * 100.0, 2) if measurable else None,
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
    - Expected Value (EV) MEDIDO: expectativa con las medias reales de ganancia y
      pérdida de la muestra (no un TP/SL nominal hardcodeado)
    - Criterio de Kelly (fracción óptima con la relación de pago real)
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
    measurable = [t for t in matched if t[4] is not None]
    n_hit50 = len(measurable)
    hit50 = sum(1 for t in measurable if t[4])
    # ``None`` si la base no permite medir el +50% (no es 0%).
    hit50_pct = round(hit50 / n_hit50 * 100.0, 2) if n_hit50 else None

    # Intervalo de confianza al 95% para la tasa de +50% (solo medibles)
    ci95 = None
    if n_hit50 > 0:
        p_val = hit50 / n_hit50
        se = math.sqrt(p_val * (1.0 - p_val) / n_hit50) if n_hit50 > 1 else 0.0
        ci_low = round(max(0.0, (p_val - 1.96 * se) * 100.0), 2)
        ci_high = round(min(100.0, (p_val + 1.96 * se) * 100.0), 2)
        ci95 = [ci_low, ci_high]

    with_paper = [t for t in matched if t[0] in paper]
    wins = sum(1 for t in with_paper if _paper_win(paper, t[0]))
    all_pnls = [p for t in with_paper for p in paper.get(t[0], [])]

    wr_paper = (wins / len(with_paper) * 100.0) if with_paper else None
    avg_pnl = (sum(all_pnls) / len(all_pnls)) if all_pnls else None

    # Expected Value MEDIDO: expectativa = p·ganancia_media + (1−p)·pérdida_media.
    # Usa las medias REALES de la muestra (no un TP/SL nominal hardcodeado) sobre un
    # win rate trade-based, coherente con el ladder dinámico del broker real
    # (TP1 +50% / BE +25% / TP2 +150% / SL −33.49% · BP-001).
    ev_pct = None
    kelly_pct = None
    avg_win_pct = None
    avg_loss_pct = None
    trade_wr_pct = None
    if all_pnls:
        trade_wins = [p for p in all_pnls if p > 0]
        trade_losses = [p for p in all_pnls if p < 0]
        p_win = len(trade_wins) / len(all_pnls)
        trade_wr_pct = round(p_win * 100.0, 2)
        if trade_wins:
            avg_win_pct = sum(trade_wins) / len(trade_wins)
        if trade_losses:
            avg_loss_pct = sum(trade_losses) / len(trade_losses)
        ev_pct = round(
            p_win * (avg_win_pct if avg_win_pct is not None else 0.0)
            + (1.0 - p_win) * (avg_loss_pct if avg_loss_pct is not None else 0.0), 2)
        if avg_win_pct is not None and avg_loss_pct not in (None, 0.0):
            # Criterio de Kelly con la relación de pago REAL (ganancia/pérdida media).
            b_ratio = abs(avg_win_pct / avg_loss_pct)
            if b_ratio > 0:
                f_star = (p_win * b_ratio - (1.0 - p_win)) / b_ratio
                kelly_pct = round(max(0.0, f_star * 100.0), 2)

    has_lookahead = min_final_mc is not None and min_final_mc > 0

    # Métricas de referencia del Universo total con datos válidos
    valid_universe = [t for t in tokens if t[1] > 0 and t[2] > 0]
    univ_measurable = [t for t in valid_universe if t[4] is not None]
    univ_hit = sum(1 for t in univ_measurable if t[4])
    univ_hit_pct = (round(univ_hit / len(univ_measurable) * 100.0, 2)
                    if univ_measurable else None)
    univ_with_paper = [t for t in valid_universe if t[0] in paper]
    univ_wins = sum(1 for t in univ_with_paper if _paper_win(paper, t[0]))
    univ_wr = round(univ_wins / len(univ_with_paper) * 100.0, 2) if univ_with_paper else None

    edge_vs_universe = {
        "delta_hit50_pct": (round(hit50_pct - univ_hit_pct, 2)
                            if (hit50_pct is not None and univ_hit_pct is not None) else None),
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
        "n_hit50_measurable": n_hit50,
        "hit50_pct": hit50_pct,
        "hit50_ci95": ci95,
        "n_with_paper": len(with_paper),
        "paper_wr_pct": round(wr_paper, 2) if wr_paper is not None else None,
        "paper_avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else None,
        "trade_wr_pct": trade_wr_pct,
        "avg_win_pct": round(avg_win_pct, 2) if avg_win_pct is not None else None,
        "avg_loss_pct": round(avg_loss_pct, 2) if avg_loss_pct is not None else None,
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
