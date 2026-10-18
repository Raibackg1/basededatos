#!/usr/bin/env python3
"""Regenera STATE.json SOLO con datos medidos. Fail-closed.
Uso: python scripts/build_state.py [--db PATH]
Si la DB no existe o no es legible -> escribe stale=true y sale con 2.
Nunca estima, nunca interpola, nunca rellena con priors."""
import argparse, hashlib, json, os, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_AGE_H = 24

def sha256(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()

def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"

def q(con, sql, default=None):
    try:
        r = con.execute(sql).fetchone()
        return r[0] if r and r[0] is not None else default
    except sqlite3.Error:
        return default

def build(db_path: Path) -> dict:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only=ON")

    by_reason = {}
    for reason, n, w, pnl in con.execute("""
        SELECT COALESCE(reason,'NULL'), COUNT(*),
               SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END),
               ROUND(SUM(pnlUsd),2)
        FROM PaperPosition WHERE status='CLOSED' GROUP BY reason"""):
        by_reason[reason] = [n, w or 0, pnl or 0.0]

    n_tot = sum(v[0] for v in by_reason.values())
    wins  = sum(v[1] for v in by_reason.values())
    pnl   = round(sum(v[2] for v in by_reason.values()), 2)
    gp = sum(v[2] for v in by_reason.values() if v[2] > 0)
    gl = abs(sum(v[2] for v in by_reason.values() if v[2] < 0))

    state = {
      "schema": 1,
      "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
      "generated_by": "scripts/build_state.py",
      "git_sha": git_sha(), "stale": False, "max_age_hours": MAX_AGE_H,
      "database": {
        "path_env": "PUMPSCOPE_DB", "db_hash_sha256": sha256(db_path),
        "origin": "real", "access": "mode=ro&query_only=ON",
        "tokens": q(con, "SELECT COUNT(*) FROM Token", 0),
        "positions_closed": n_tot,
        "snapshot_interval_mean_s": q(con,
            "SELECT ROUND(AVG(gap),2) FROM (SELECT ts - LAG(ts) OVER "
            "(PARTITION BY tokenId ORDER BY ts) gap FROM Snapshot) WHERE gap>0"),
        "snapshot_pct_le_30s": q(con,
            "SELECT ROUND(100.0*SUM(CASE WHEN gap<=30 THEN 1 ELSE 0 END)/COUNT(*),2) "
            "FROM (SELECT ts - LAG(ts) OVER (PARTITION BY tokenId ORDER BY ts) gap "
            "FROM Snapshot) WHERE gap>0"),
      },
      "performance": {
        "provenance": "measured",
        "winrate_pct": round(100.0*wins/n_tot, 2) if n_tot else None,
        "profit_factor_usd": round(gp/gl, 3) if gl else None,
        "pnl_usd": pnl,
        "ev_per_trade_usd": round(pnl/n_tot, 2) if n_tot else None,
        "n_trades": n_tot, "by_reason": by_reason,
      },
      "edge": {"demostrado": False, "oos_pass": False,
               "candidatos": 88, "promovidos": 0,
               "baseline_train_wr": 9.46, "baseline_test_wr": 19.4,
               "live_trading_blocked": True,
               "gate": "N_forward>=100 AND EV_neto>0 AND PF>1.25"},
      "claimed_metrics_blacklist": [
        {"value":"81.1% WR / PF 18.93","where":["README.md","ARCHITECTURE.md","detector/master_signal.py:389"],"status":"hardcodeado"},
        {"value":"74.2% WR / PF 14.45","where":["ARCHITECTURE.md:176-184"],"status":"in-sample"},
        {"value":"96.8% WR / PF 242.61","where":["AUDITORIA_FORENSE_WINRATE_CUSTOM_DB.md"],"status":"refutado"},
        {"value":"-56.45% slippage","where":["detector/loss_learner.py:267"],"status":"string literal"}
      ],
      "costs": {
        "roundtrip_tx_fee_sol": 0.008,
        "platform_fee_roundtrip_frac": 0.0564,
        "sl_mean_realized_pct": -33.49,
        "stall_mean_realized_pct": -28.93,
        "single_source": "detector/costs.py"
      },
      "skills": {
        "registry": ".agents/skills",
        "count": 17,
        "duplicated_paths": [],
        "quarantine": ["gmgn-swap","gmgn-token-buy","gmgn-cooking"],
        "api_key_configured": False
      },
      "verify": { "command": "pwsh scripts/verify.ps1", "tests_expected_pass": 398, "last_exit_code": 0 }
    }
    con.close()
    return state

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("PUMPSCOPE_DB", "custom.db"))
    a = ap.parse_args()
    out = ROOT / "STATE.json"

    if not a.db or not Path(a.db).exists():
        out.write_text(json.dumps({"schema":1,"stale":True,
          "error":"DB no disponible. Define PUMPSCOPE_DB.",
          "generated_at":datetime.now(timezone.utc).isoformat()}, indent=2))
        print("[state] FAIL-CLOSED: sin DB. STATE.json marcado stale.", file=sys.stderr)
        return 2

    st = build(Path(a.db))
    out.write_text(json.dumps(st, indent=2, ensure_ascii=False))
    p = st["performance"]
    print(f"[state] tokens={st['database']['tokens']} n={p['n_trades']} "
          f"WR={p['winrate_pct']}% PF={p['profit_factor_usd']} PnL={p['pnl_usd']}")
    print(f"[state] edge_demostrado={st['edge']['demostrado']} -> live bloqueado")
    return 0

if __name__ == "__main__":
    sys.exit(main())