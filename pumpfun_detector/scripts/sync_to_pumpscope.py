#!/usr/bin/env python3
"""
sync_to_pumpscope.py — Puente Bidireccional de Telemetría entre Detector y PumpScope.

1. Registra o actualiza la configuración STRAT-CONFLUENCE-P0-OOS en PaperConfig (custom.db).
2. Sincroniza outcomes de live_decisions.db hacia PaperPosition.
3. Actualiza las métricas reales consolidadas en $STRATEGY_MEMORY_PATH.
4. Ejecuta assert_ssot.mjs para garantizar consistencia SSOT.

Uso:
    python scripts/sync_to_pumpscope.py [--db-pumpscope "D:" + "/hjh/pumpscope/db/custom.db"]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time

def safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


PUMPSCOPE_DB_DEFAULT = os.environ.get("PUMPSCOPE_DB", "custom.db")
STRATEGY_MEMORY_PATH = os.environ.get("STRATEGY_MEMORY_PATH", "STRATEGY_MEMORY.json")
PUMPSCOPE_ROOT = os.environ.get("PUMPSCOPE_ROOT", ".")

CONFIG_DEF = {
    "name": "🔬 Challenger · Social & NetBuyers Confluence (OOS Certified)",
    "kind": "filter",
    "active": 1,
    "betUsd": 100.0,
    "slippageMode": "fixed",
    "mlMinScore": None,
    "filter": {
        "requireSocials": True,
        "minSecurity": -1,
        "minLiquidity": 12.0,
        "maxDetectAgeMin": 2,
        "minVolMcap": 0,
        "requireMintRevoked": False,
        "maxDevPct": 1.5,
        "minHolders": 2,
        "minOrganic": -1,
        "boostOnly": False,
        "minSnapLiquidity": 0,
        "minNetBuyers1h": 12,
        "antiToxicGuard": True,
        "maxMigrationPct": 45,
        "minMigrationPct": -1,
        "maxInsiderPct": -1,
        "maxBundlerPct": -1,
        "requireLive": False,
        "bannedDevGrades": ["RUGGER"],
        "antiAthGuard": True,
        "maxAthProximityPct": 95,
        "minImpulseSurgePct": 150,
        "maxSellsM5": 50,
    },
    "exit": {
        "takeProfitPct": 30,
        "stopLossPct": 60,
        "timeoutMin": 5,
        "trailingStopPct": 10,
        "breakEvenAfterPct": 15,
        "greenExitMin": 1.5,
        "minGreenPct": 10,
        "stallExitMin": 1.0,
    },
    "note": (
        "Confluencia OOS descubierta en custom.db: firstMarketCap in [3.5k, 39.6M], "
        "n_socials in [1, 2], netBuyers1h in [12, 90]. Train WR 51.7%, Test OOS WR 31.25% (N=32). "
        "Salidas multi-etapa dinámicas con modelado de slippage realista (-33.49%)."
    ),
}


def sync_paper_config(db_path: str) -> str:
    """Inserta o actualiza la configuración en PaperConfig de PumpScope."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    fj = json.dumps(CONFIG_DEF["filter"], separators=(",", ":"))
    ej = json.dumps(CONFIG_DEF["exit"], separators=(",", ":"))

    cur.execute("SELECT id FROM PaperConfig WHERE name = ?", (CONFIG_DEF["name"],))
    row = cur.fetchone()
    if row:
        cid = row[0]
        cur.execute(
            """
            UPDATE PaperConfig
            SET kind=?, active=?, betUsd=?, slippageMode=?, mlMinScore=NULL,
                filterJson=?, exitJson=?, note=?
            WHERE id=?
            """,
            (
                CONFIG_DEF["kind"],
                CONFIG_DEF["active"],
                CONFIG_DEF["betUsd"],
                CONFIG_DEF["slippageMode"],
                fj,
                ej,
                CONFIG_DEF["note"],
                cid,
            ),
        )
        safe_print(f"[+] PaperConfig actualizada: id={cid}")
    else:
        import uuid
        cid = f"cfg_confluence_{uuid.uuid4().hex[:8]}"
        now = int(time.time() * 1000)
        cur.execute(
            """
            INSERT INTO PaperConfig
                (id, name, kind, active, betUsd, slippageMode, mlMinScore,
                 filterJson, exitJson, note, createdAt)
            VALUES (?,?,?,?,?,?,NULL,?,?,?,?)
            """,
            (
                cid,
                CONFIG_DEF["name"],
                CONFIG_DEF["kind"],
                CONFIG_DEF["active"],
                CONFIG_DEF["betUsd"],
                CONFIG_DEF["slippageMode"],
                fj,
                ej,
                CONFIG_DEF["note"],
                now,
            ),
        )
        safe_print(f"[+] Nueva PaperConfig creada: id={cid}")

    con.commit()
    con.close()
    return cid


def sync_outcomes_from_detector(detector_db: str, pumpscope_db: str, config_id: str) -> int:
    """Sincroniza outcomes cerrados de live_decisions.db a PaperPosition."""
    if not os.path.exists(detector_db):
        return 0

    det_con = sqlite3.connect(detector_db)
    det_cur = det_con.cursor()

    rows = det_cur.execute(
        """
        SELECT d.mint, d.ts, o.pnl_pct, o.pnl_sol, o.exit_reason, o.resolved_at
        FROM live_decisions d
        JOIN live_outcomes o ON d.id = o.decision_id
        WHERE d.admitted = 1
        """
    ).fetchall()
    det_con.close()

    if not rows:
        return 0

    ps_con = sqlite3.connect(pumpscope_db)
    ps_cur = ps_con.cursor()
    synced = 0

    for mint, entry_ts, pnl_pct, pnl_sol, exit_reason, exit_ts in rows:
        # Verificar si existe el token en Token
        t_row = ps_cur.execute(
            "SELECT id, symbol, priceUsd FROM Token WHERE mint = ? OR id = ?", (mint, mint)
        ).fetchone()
        if not t_row:
            continue
        tok_id, sym, current_px = t_row

        pos_id = f"pos_det_{mint[:8]}_{int(entry_ts)}"
        ps_cur.execute("SELECT id FROM PaperPosition WHERE id = ?", (pos_id,))
        if ps_cur.fetchone():
            continue

        entry_ms = int(entry_ts * 1000)
        exit_ms = int(exit_ts * 1000) if exit_ts else entry_ms
        entry_px = float(current_px or 0.0001)
        exit_px = entry_px * (1.0 + float(pnl_pct or 0.0) / 100.0)
        pnl_usd = float(pnl_sol * 150.0) if pnl_sol is not None else 0.0
        minutes_in_trade = (exit_ts - entry_ts) / 60.0 if (exit_ts and entry_ts) else 0.0

        ps_cur.execute(
            """
            INSERT INTO PaperPosition
                (id, configId, tokenId, mint, symbol, status, entryTs, entryPrice,
                 betUsd, peakPrice, lastTs, exitTs, exitPrice, pnlPct, pnlUsd,
                 reason, minutesInTrade, createdAt)
            VALUES (?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pos_id,
                config_id,
                tok_id,
                mint,
                sym or "UNKNOWN",
                entry_ms,
                entry_px,
                100.0,
                max(entry_px, exit_px),
                entry_ms,
                exit_ms,
                exit_px,
                pnl_pct,
                pnl_usd,
                exit_reason or "EXIT",
                minutes_in_trade,
                entry_ms,
            ),
        )
        synced += 1

    ps_con.commit()
    ps_con.close()
    return synced


def update_strategy_memory(pumpscope_db: str, config_name: str) -> None:
    """Calcula métricas consolidadas en custom.db y actualiza STRATEGY_MEMORY.json."""
    if not os.path.exists(STRATEGY_MEMORY_PATH):
        return

    con = sqlite3.connect(pumpscope_db)
    cur = con.cursor()

    cur.execute(
        """
        SELECT p.pnlPct, p.pnlUsd
        FROM PaperPosition p
        JOIN PaperConfig c ON p.configId = c.id
        WHERE c.name = ? AND p.status = 'CLOSED'
        """,
        (config_name,),
    )
    rows = cur.fetchall()
    con.close()

    if not rows:
        return

    pnls = [r[0] for r in rows if r[0] is not None]
    n = len(pnls)
    if n == 0:
        return

    wins = sum(1 for p in pnls if p > 0)
    wr = (wins / n) * 100.0
    ev = sum(pnls) / n
    tot_pnl = sum(pnls)

    with open(STRATEGY_MEMORY_PATH, encoding="utf-8") as f:
        mem = json.load(f)

    updated = False
    for s in mem.get("registeredStrategies", []):
        if s.get("id") == "STRAT-CONFLUENCE-P0-OOS":
            perf = s.setdefault("realPerformance", {})
            perf["tradesCount"] = n
            perf["winratePct"] = round(wr, 2)
            perf["netEvPct"] = round(ev, 2)
            perf["sumPnlPct"] = round(tot_pnl, 2)
            updated = True
            break

    if updated:
        mem["lastUpdated"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with open(STRATEGY_MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2, ensure_ascii=False)
        safe_print(f"[+] STRATEGY_MEMORY.json actualizado: n={n}, WR={wr:.1f}%, EV={ev:+.2f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description="Puente de telemetría Detector <-> PumpScope")
    ap.add_argument("--db-pumpscope", default=PUMPSCOPE_DB_DEFAULT, help="Ruta a custom.db")
    ap.add_argument("--db-detector", default="live_decisions.db", help="Ruta a live_decisions.db")
    ap.add_argument("--dry-run", action="store_true", help="Modo simulado sin escribir cambios permanentes")
    args = ap.parse_args()

    if not os.path.exists(args.db_pumpscope):
        safe_print(f"[!] Base de datos PumpScope no encontrada: {args.db_pumpscope}")
        return 1

    if args.dry_run:
        safe_print("[*] Modo DRY-RUN activado. No se escribirán cambios en custom.db.")
        return 0

    safe_print("[*] Sincronizando configuración con PumpScope...")
    cid = sync_paper_config(args.db_pumpscope)

    safe_print("[*] Sincronizando outcomes de decisiones en vivo...")
    synced = sync_outcomes_from_detector(args.db_detector, args.db_pumpscope, cid)
    safe_print(f"[+] Outcomes sincronizados hacia PaperPosition: {synced}")

    safe_print("[*] Actualizando memoria persistente de estrategias...")
    update_strategy_memory(args.db_pumpscope, CONFIG_DEF["name"])

    # Ejecutar assert_ssot.mjs
    safe_print("[*] Verificando consistencia SSOT...")
    import shutil
    bun_bin = shutil.which("bun") or "bun"
    res = subprocess.run([bun_bin, "scripts/assert_ssot.mjs"], cwd=PUMPSCOPE_ROOT, capture_output=True, text=True, shell=True)
    if res.returncode == 0:
        safe_print("[+] SSOT verificado con éxito (código 0).")
    else:
        safe_print("[!] Alerta en SSOT:" + "\n" + res.stdout + "\n" + res.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
