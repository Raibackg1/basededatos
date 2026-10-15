#!/usr/bin/env python3
"""Analytics for converting historical paper/live outcomes into auditable learning."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def to_csv(path: Path, rows: Iterable[sqlite3.Row]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)


def run_query(con: sqlite3.Connection, name: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return [r for r in con.execute(sql, params)]
    except sqlite3.Error as exc:
        print(f"skip {name}: {exc}")
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Profit learning analytics for pump detector databases")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default="outputs/profit_learning")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    con = connect(args.db)
    timestamp = datetime.now(timezone.utc).isoformat()

    specs: dict[str, str] = {
        "overall_paper": """
            SELECT COUNT(*) AS n, SUM(CASE WHEN pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pnlPct) AS avg_pnl_pct, SUM(pnlUsd) AS total_pnl_usd,
                   AVG(betUsd) AS avg_bet_usd, AVG(minutesInTrade) AS avg_minutes
            FROM PaperPosition
        """,
        "by_reason": """
            SELECT reason, COUNT(*) AS n, SUM(CASE WHEN pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pnlPct) AS avg_pnl_pct, SUM(pnlUsd) AS total_pnl_usd
            FROM PaperPosition GROUP BY reason ORDER BY n DESC
        """,
        "by_security_tier": """
            SELECT t.mintRevoked, t.freezeRevoked, t.securityScore,
                   COUNT(*) AS n, SUM(CASE WHEN pp.pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pp.pnlPct) AS avg_pnl_pct, SUM(pp.pnlUsd) AS total_pnl_usd,
                   AVG(t.topHoldersPct) AS avg_top_holders_pct,
                   AVG(t.devBalancePct) AS avg_dev_balance_pct
            FROM PaperPosition pp JOIN Token t ON t.id = pp.tokenId
            GROUP BY t.mintRevoked, t.freezeRevoked, t.securityScore
            ORDER BY n DESC
        """,
        "by_holder_concentration": """
            SELECT CASE WHEN t.topHoldersPct < 20 THEN 'low'
                        WHEN t.topHoldersPct < 50 THEN 'medium' ELSE 'high' END AS holder_bucket,
                   COUNT(*) AS n, SUM(CASE WHEN pp.pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pp.pnlPct) AS avg_pnl_pct, SUM(pp.pnlUsd) AS total_pnl_usd
            FROM PaperPosition pp JOIN Token t ON t.id = pp.tokenId
            GROUP BY holder_bucket ORDER BY holder_bucket
        """,
        "by_organic_score": """
            SELECT t.organicLabel, COUNT(*) AS n, SUM(CASE WHEN pp.pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pp.pnlPct) AS avg_pnl_pct, SUM(pp.pnlUsd) AS total_pnl_usd
            FROM PaperPosition pp JOIN Token t ON t.id = pp.tokenId
            GROUP BY t.organicLabel ORDER BY n DESC
        """,
        "by_graduation": """
            SELECT t.complete, t.graduatedAt IS NOT NULL AS graduated,
                   COUNT(*) AS n, SUM(CASE WHEN pp.pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pp.pnlPct) AS avg_pnl_pct, SUM(pp.pnlUsd) AS total_pnl_usd
            FROM PaperPosition pp JOIN Token t ON t.id = pp.tokenId
            GROUP BY t.complete, graduated ORDER BY n DESC
        """,
        "creator_summary": """
            SELECT t.creator, COUNT(*) AS n, SUM(CASE WHEN pp.pnlPct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(pp.pnlPct) AS avg_pnl_pct, SUM(pp.pnlUsd) AS total_pnl_usd
            FROM PaperPosition pp JOIN Token t ON t.id = pp.tokenId
            GROUP BY t.creator HAVING COUNT(*) >= 5 ORDER BY total_pnl_usd ASC
        """,
        "live_trade_execution": """
            SELECT mode, status, COUNT(*) AS n, AVG(latencyMs) AS avg_latency_ms,
                   SUM(solAmount) AS total_sol, SUM(feeSol) AS total_fee_sol
            FROM LiveTrade GROUP BY mode, status ORDER BY mode, status
        """,
        "wallet_signals": """
            SELECT label, side, COUNT(*) AS n, AVG(solAmount) AS avg_sol, SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) AS confirmed
            FROM WhaleSignal GROUP BY label, side ORDER BY n DESC
        """,
    }

    manifest: dict[str, Any] = {"db": args.db, "generated_at": timestamp, "queries": {}}
    for name, sql in specs.items():
        rows = run_query(con, name, sql)
        to_csv(out / f"{name}.csv", rows)
        manifest["queries"][name] = {
            "rows": len(rows),
            "file": f"{name}.csv",
        }

    # Profit factor summary from paper positions.
    gains = con.execute("SELECT COALESCE(SUM(pnlUsd),0) FROM PaperPosition WHERE pnlUsd > 0").fetchone()[0]
    losses = con.execute("SELECT COALESCE(SUM(pnlUsd),0) FROM PaperPosition WHERE pnlUsd < 0").fetchone()[0]
    wins = con.execute("SELECT COUNT(*) FROM PaperPosition WHERE pnlPct > 0").fetchone()[0]
    n = con.execute("SELECT COUNT(*) FROM PaperPosition").fetchone()[0]
    manifest["summary"] = {
        "paper_trades": n,
        "winrate_pct": round(100.0 * wins / n, 3) if n else 0.0,
        "sum_gain_usd": round(float(gains), 6),
        "sum_loss_usd": round(float(losses), 6),
        "profit_factor": round(float(gains) / abs(float(losses)), 6) if losses else None,
        "net_usd": round(float(gains) + float(losses), 6),
    }

    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    con.close()
    print(f"Profit learning analytics written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
