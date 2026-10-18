#!/usr/bin/env python3
"""
Analiza toda la base de datos custom.db y reporta:
- Winrate real, ganancias totales, PnL neto
- Patrones ganadores vs perdedores (dev_wallet, complete, entry_price, pnl_pct)
- Parámetros estadísticos de trades
Modo: solo lectura (file:...mode=ro). Cero mocks.
"""
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

DB = "custom.db"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()

report = {
    "analizado_en": datetime.now(timezone.utc).isoformat(),
    "base_de_datos": DB,
    "provenance": "measured",
}

# ── 1. Conteo y winrate desde PaperPosition ──
rows = cur.execute("""
    SELECT status, reason, COUNT(*) as n,
           SUM(CASE WHEN pnlUsd > 0 THEN 1 ELSE 0 END) as wins,
           SUM(pnlUsd) as total_pnl_usd,
           AVG(pnlPct) as avg_pnl_pct
    FROM PaperPosition
    GROUP BY status, reason
    ORDER BY n DESC
""").fetchall()

paper_by_reason = []
total_n = 0
total_wins = 0
total_pnl = 0.0
for status, reason, n, wins, pnl, avg_pct in rows:
    total_n += n
    total_wins += (wins or 0)
    total_pnl += (pnl or 0.0)
    paper_by_reason.append({
        "status": status,
        "reason": reason,
        "trades": n,
        "wins": wins or 0,
        "winrate": round((wins or 0) / n, 4) if n else 0.0,
        "pnl_usd": round(pnl or 0.0, 2),
        "avg_pnl_pct": round(avg_pct or 0.0, 4),
    })

wr = total_wins / total_n if total_n else 0.0
report["paper_position"] = {
    "total_trades": total_n,
    "total_wins": total_wins,
    "winrate": round(wr, 4),
    "winrate_pct": round(wr * 100, 2),
    "total_pnl_usd": round(total_pnl, 2),
    "by_reason": paper_by_reason,
}

# ── 2. Winrate desde my_trades (duplicado para validación) ──
rows2 = cur.execute("""
    SELECT status, COUNT(*) as n,
           SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
           SUM(pnl_sol) as total_pnl_sol,
           AVG(pnl_pct) as avg_pnl_pct
    FROM my_trades
    GROUP BY status
""").fetchall()

my_trades_by_status = []
mt_n = 0
mt_wins = 0
mt_pnl_sol = 0.0
for status, n, wins, pnl_sol, avg_pct in rows2:
    mt_n += n
    mt_wins += (wins or 0)
    mt_pnl_sol += (pnl_sol or 0.0)
    my_trades_by_status.append({
        "status": status,
        "trades": n,
        "wins": wins or 0,
        "winrate": round((wins or 0) / n, 4) if n else 0.0,
        "pnl_sol": round(pnl_sol or 0.0, 6),
        "avg_pnl_pct": round(avg_pct or 0.0, 4),
    })

report["my_trades"] = {
    "total_trades": mt_n,
    "total_wins": mt_wins,
    "winrate": round(mt_wins / mt_n, 4) if mt_n else 0.0,
    "total_pnl_sol": round(mt_pnl_sol, 6),
    "by_status": my_trades_by_status,
}

# ── 3. Tokens ──
tok_count = cur.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
tok_complete = cur.execute("SELECT COUNT(*) FROM tokens WHERE complete = 1").fetchone()[0]
tok_incomplete = tok_count - tok_complete
report["tokens"] = {
    "total": tok_count,
    "complete": tok_complete,
    "incomplete": tok_incomplete,
    "complete_pct": round(tok_complete / tok_count * 100, 2) if tok_count else 0.0,
}

# ── 4. Patrones: trades por rango de pnl_pct ──
report["pattern_pnl_ranges"] = []
for lo, hi, label in [
    (-100, -50, "categoria_crash_-50_a_-100"),
    (-50, -33.49, "categoria_sl_real_-50_a_-33.49"),
    (-33.49, -20, "categoria_perdida_fuerte_-33.49_a_-20"),
    (-20, -10, "categoria_perdida_media_-20_a_-10"),
    (-10, 0, "categoria_perdida_leve_-10_a_0"),
    (0, 10, "categoria_ganancia_leve_0_a_10"),
    (10, 50, "categoria_ganancia_media_10_a_50"),
    (50, 100, "categoria_ganancia_alta_50_a_100"),
    (100, 10000, "categoria_ganancia_explosiva_>100"),
]:
    r = cur.execute("""
        SELECT COUNT(*), SUM(pnlUsd), AVG(pnlPct)
        FROM PaperPosition
        WHERE pnlPct >= ? AND pnlPct < ?
    """, (lo, hi)).fetchone()
    n, pnl, avg = r
    report["pattern_pnl_ranges"].append({
        "categoria": label,
        "rango_pnl_pct": f"[{lo}, {hi})",
        "trades": n,
        "pnl_usd": round(pnl or 0.0, 2),
        "avg_pnl_pct": round(avg or 0.0, 4),
    })

# ── 5. Patrones por branch ──
report["pattern_by_branch"] = []
rows_b = cur.execute("""
    SELECT branch, COUNT(*) as n,
           SUM(CASE WHEN pnlUsd > 0 THEN 1 ELSE 0 END) as wins,
           SUM(pnlUsd) as total_pnl_usd,
           AVG(pnlPct) as avg_pnl_pct
    FROM PaperPosition
    GROUP BY branch
    ORDER BY n DESC
""").fetchall()
for branch, n, wins, pnl, avg_pct in rows_b:
    report["pattern_by_branch"].append({
        "branch": branch,
        "trades": n,
        "wins": wins or 0,
        "winrate": round((wins or 0) / n, 4) if n else 0.0,
        "pnl_usd": round(pnl or 0.0, 2),
        "avg_pnl_pct": round(avg_pct or 0.0, 4),
    })

# ── 6. Parámetros estadísticos de entry/exit ──
report["parameter_stats"] = {}
for col, label in [
    ("entryPrice", "precio_entrada_usd"),
    ("exitPrice", "precio_salida_usd"),
    ("pnlUsd", "pnl_usd"),
    ("pnlPct", "pnl_pct"),
    ("sizeSol", "tamaño_sol"),
]:
    r = cur.execute(f"""
        SELECT MIN({col}), MAX({col}), AVG({col}), COUNT(*)
        FROM PaperPosition
        WHERE {col} IS NOT NULL
    """).fetchone()
    mn, mx, avg, cnt = r
    report["parameter_stats"][label] = {
        "min": round(mn, 6) if mn is not None else None,
        "max": round(mx, 6) if mx is not None else None,
        "avg": round(avg, 6) if avg is not None else None,
        "count": cnt,
    }

# ── 7. Snapshot: rango de precios y marketCap ──
report["snapshot_stats"] = {}
r = cur.execute("SELECT MIN(price), MAX(price), AVG(price), COUNT(*) FROM Snapshot").fetchone()
report["snapshot_stats"]["price"] = {
    "min": round(r[0], 8) if r[0] is not None else None,
    "max": round(r[2], 8) if r[2] is not None else None,
    "avg": round(r[2], 8) if r[2] is not None else None,
    "count": r[3],
}
r = cur.execute("SELECT MIN(marketCap), MAX(marketCap), AVG(marketCap), COUNT(*) FROM Snapshot WHERE marketCap IS NOT NULL").fetchone()
report["snapshot_stats"]["market_cap"] = {
    "min": round(r[0], 2) if r[0] is not None else None,
    "max": round(r[1], 2) if r[1] is not None else None,
    "avg": round(r[2], 2) if r[2] is not None else None,
    "count": r[3],
}

# ── 8. Gates y veredicto ──
report["gates"] = {
    "n_forward_ge_100": total_n >= 100,
    "ev_neto_gt_0": total_pnl / total_n > 0 if total_n else False,
    "profit_factor_gt_125": False,
    "live_trading_blocked": True,
}

# Profit factor simple: ganancias / pérdidas absolutas
gains = cur.execute("SELECT SUM(pnlUsd) FROM PaperPosition WHERE pnlUsd > 0").fetchone()[0] or 0.0
losses = abs(cur.execute("SELECT SUM(pnlUsd) FROM PaperPosition WHERE pnlUsd < 0").fetchone()[0] or 0.0)
pf = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
report["gates"]["profit_factor"] = round(pf, 4)
report["gates"]["profit_factor_gt_125"] = pf > 1.25

# EV por trade
report["gates"]["ev_per_trade_usd"] = round(total_pnl / total_n, 4) if total_n else 0.0
report["gates"]["oos_pass"] = (
    report["gates"]["n_forward_ge_100"]
    and report["gates"]["ev_neto_gt_0"]
    and report["gates"]["profit_factor_gt_125"]
)

con.close()

# Guardar reporte
out = Path("outputs/analisis_bd_custom_db.json")
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)

# Imprimir resumen
print(json.dumps(report, indent=2, ensure_ascii=False))
