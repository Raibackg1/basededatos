"""Persistencia y auditoría neta de decisiones de ejecución."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional
import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, mint TEXT NOT NULL,
 data_origin TEXT NOT NULL, admitted INTEGER NOT NULL, branch TEXT NOT NULL,
 confidence REAL, expected_winrate REAL, expected_profit_factor REAL,
 veto_reason TEXT, features_json TEXT NOT NULL, sig TEXT NOT NULL UNIQUE,
 db_hash TEXT, code_git_sha TEXT, config_hash TEXT, cost_model_version TEXT,
 quote_json TEXT, execution_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_live_dec_mint ON live_decisions(mint);
CREATE INDEX IF NOT EXISTS idx_live_dec_branch ON live_decisions(branch);
CREATE INDEX IF NOT EXISTS idx_live_dec_admit ON live_decisions(admitted);
CREATE TABLE IF NOT EXISTS live_outcomes (
 decision_id INTEGER PRIMARY KEY, pnl_pct REAL, pnl_sol REAL,
 exit_reason TEXT, resolved_at REAL NOT NULL, mfe_pct REAL, mae_pct REAL,
 FOREIGN KEY(decision_id) REFERENCES live_decisions(id)
);
CREATE TABLE IF NOT EXISTS branch_gate_state (
 branch TEXT PRIMARY KEY, status TEXT NOT NULL, n INTEGER NOT NULL,
 ev_sol REAL, winrate REAL, p_profitable REAL, reason TEXT,
 decided_at REAL NOT NULL
);
"""


def _decision_sig(mint: str, branch: str, admitted: bool, data_origin: str = "live",
                  entry_ts: Optional[float] = None) -> str:
    raw = f"{mint}|{branch}|{int(bool(admitted))}|{data_origin}|{entry_ts if entry_ts is not None else ''}".encode()
    return hashlib.sha256(raw).hexdigest()


class LiveStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._con = sqlite3.connect(db_path)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._migrate_optional_columns()
        self._con.commit()

    def _migrate_optional_columns(self) -> None:
        existing = {r[1] for r in self._con.execute("PRAGMA table_info(live_decisions)").fetchall()}
        additions = {"db_hash": "TEXT", "code_git_sha": "TEXT", "config_hash": "TEXT",
                     "cost_model_version": "TEXT", "quote_json": "TEXT", "execution_json": "TEXT"}
        for name, typ in additions.items():
            if name not in existing:
                self._con.execute(f"ALTER TABLE live_decisions ADD COLUMN {name} {typ}")
        # MFE/MAE reales por outcome (policy de salida por rama)
        oc_existing = {r[1] for r in self._con.execute("PRAGMA table_info(live_outcomes)").fetchall()}
        for name, typ in {"mfe_pct": "REAL", "mae_pct": "REAL"}.items():
            if name not in oc_existing:
                self._con.execute(f"ALTER TABLE live_outcomes ADD COLUMN {name} {typ}")

    def record_decision(self, mint: str, feats: Dict[str, Any], decision: Any,
                        ts: Optional[float] = None, provenance: Optional[Dict[str, Any]] = None) -> int:
        ts = float(ts if ts is not None else time.time())
        admitted = bool(getattr(decision, "admitted", False)) if decision is not None else False
        branch = str(getattr(decision, "branch", "NO_SIGNAL")) if decision is not None else "NO_SIGNAL"
        conf = float(getattr(decision, "confidence", 0.0) or 0.0)
        ewr = float(getattr(decision, "expected_winrate", 0.0) or 0.0)
        epf = float(getattr(decision, "expected_profit_factor", 0.0) or 0.0)
        veto = getattr(decision, "veto_reason", None) if decision is not None else None
        origin = str(feats.get("data_origin", "live")) if isinstance(feats, dict) else "live"
        prov = dict(provenance or {})
        sig = _decision_sig(str(mint), branch, admitted, origin, ts if provenance and provenance.get("include_ts_in_sig") else None)
        cur = self._con.execute(
            """INSERT OR IGNORE INTO live_decisions
            (ts,mint,data_origin,admitted,branch,confidence,expected_winrate,
             expected_profit_factor,veto_reason,features_json,sig,db_hash,code_git_sha,
             config_hash,cost_model_version,quote_json,execution_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, str(mint), origin, int(admitted), branch, conf, ewr, epf, veto,
             json.dumps(feats or {}, ensure_ascii=False, sort_keys=True, default=str), sig,
             prov.get("db_hash"), prov.get("code_git_sha"), prov.get("config_hash"),
             prov.get("cost_model_version"),
             json.dumps(prov.get("quote"), sort_keys=True, default=str) if prov.get("quote") is not None else None,
             json.dumps(prov.get("execution"), sort_keys=True, default=str) if prov.get("execution") is not None else None))
        self._con.commit()
        row = self._con.execute("SELECT id FROM live_decisions WHERE sig=?", (sig,)).fetchone()
        return int(row["id"]) if row is not None else int(cur.lastrowid or 0)

    def record_execution_trace(self, decision_id: int, trace: Dict[str, Any]) -> None:
        self._con.execute("UPDATE live_decisions SET execution_json=? WHERE id=?",
                          (json.dumps(trace or {}, sort_keys=True, default=str), int(decision_id)))
        self._con.commit()

    def record_outcome(self, decision_id: int, pnl_pct: Optional[float] = None,
                       pnl_sol: Optional[float] = None, exit_reason: Optional[str] = None,
                       resolved_at: Optional[float] = None,
                       mfe_pct: Optional[float] = None,
                       mae_pct: Optional[float] = None) -> None:
        self._con.execute("""INSERT OR REPLACE INTO live_outcomes
            (decision_id,pnl_pct,pnl_sol,exit_reason,resolved_at,mfe_pct,mae_pct)
            VALUES (?,?,?,?,?,?,?)""",
            (int(decision_id), pnl_pct, pnl_sol, exit_reason,
             float(resolved_at if resolved_at is not None else time.time()),
             mfe_pct, mae_pct))
        self._con.commit()

    def get_outcome(self, decision_id: int) -> Optional[Dict[str, Any]]:
        row = self._con.execute(
            "SELECT decision_id,pnl_pct,pnl_sol,exit_reason,resolved_at,mfe_pct,mae_pct "
            "FROM live_outcomes WHERE decision_id=?", (int(decision_id),)).fetchone()
        return dict(row) if row is not None else None

    def resolve_latest(self, mint: str, pnl_pct: Optional[float] = None,
                       pnl_sol: Optional[float] = None, exit_reason: Optional[str] = None,
                       admitted_only: bool = True) -> Optional[int]:
        q = "SELECT id FROM live_decisions WHERE mint=?" + (" AND admitted=1" if admitted_only else "") + " ORDER BY ts DESC,id DESC LIMIT 1"
        row = self._con.execute(q, (str(mint),)).fetchone()
        if row is None:
            return None
        did = int(row["id"])
        self.record_outcome(did, pnl_pct, pnl_sol, exit_reason)
        return did

    def net_audit(self, origin: Optional[str] = "live", admitted_only: bool = True) -> Dict[str, Any]:
        q = """SELECT o.pnl_sol,o.pnl_pct FROM live_decisions d JOIN live_outcomes o ON o.decision_id=d.id WHERE 1=1"""
        params: List[Any] = []
        if origin is not None:
            q += " AND d.data_origin=?"; params.append(str(origin))
        if admitted_only:
            q += " AND d.admitted=1"
        df = pd.read_sql_query(q, self._con, params=params)
        if len(df) == 0:
            return {"origin": origin, "admitted_only": admitted_only, "n": 0, "resolved": 0,
                    "winrate": 0.0, "profit_factor": 0.0, "total_pnl_sol": 0.0,
                    "avg_pnl_sol": 0.0, "expectancy_r": 0.0}
        pnl = df["pnl_sol"].fillna(df["pnl_pct"]).astype(float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        gw, gl = float(wins.sum()), float(abs(losses.sum()))
        return {"origin": origin, "admitted_only": admitted_only, "n": len(pnl), "resolved": len(pnl),
                "winrate": float((pnl > 0).mean()), "profit_factor": gw / gl if gl > 1e-9 else float("inf"),
                "total_pnl_sol": float(pnl.sum()), "avg_pnl_sol": float(pnl.mean()),
                "expectancy_r": float(pnl.mean()), "gross_win_sol": gw, "gross_loss_sol": gl}

    def net_audit_by_branch(self, origin: Optional[str] = "live", admitted_only: bool = True) -> List[Dict[str, Any]]:
        rows = self._resolved_rows(origin=origin, admitted_only=admitted_only)
        grouped: Dict[str, List[Any]] = {}
        for r in rows:
            grouped.setdefault(r["branch"], []).append(float(r["pnl"]))
        out = []
        for branch, pnls in grouped.items():
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            gw, gl = float(sum(wins)), float(abs(sum(losses)))
            out.append({"branch": str(branch), "n": len(pnls),
                        "winrate": (len(wins) / len(pnls)) if pnls else 0.0,
                        "profit_factor": gw / gl if gl > 1e-9 else float("inf"),
                        "total_pnl_sol": float(sum(pnls)),
                        "expectancy_r": float(sum(pnls) / len(pnls)) if pnls else 0.0})
        return sorted(out, key=lambda x: x["total_pnl_sol"], reverse=True)

    # ------------------------------------------------------------------
    # Auditorias agrupadas (rama / liquidez / franja horaria)
    # ------------------------------------------------------------------
    #: claves de features con las que se identifica liquidez en SOL (en orden)
    LIQ_FEATURE_KEYS = ("liquidity_sol", "real_sol_reserves", "realSolReserves",
                        "sol_reserves")
    #: buckets de liquidez en SOL (pump.fun opera con reservas pequeñas)
    LIQ_BUCKETS_SOL = ((0.0, 5.0), (5.0, 15.0), (15.0, 30.0), (30.0, 100.0),
                       (100.0, float("inf")))
    LIQ_BUCKET_LABELS = ("<5 SOL", "5-15 SOL", "15-30 SOL", "30-100 SOL", ">=100 SOL")

    def _resolved_rows(self, origin: Optional[str] = "live",
                       admitted_only: bool = True) -> List[Dict[str, Any]]:
        q = """SELECT d.branch AS branch, d.ts AS ts, d.features_json AS features_json,
                      o.pnl_sol AS pnl_sol, o.pnl_pct AS pnl_pct
               FROM live_decisions d JOIN live_outcomes o ON o.decision_id=d.id WHERE 1=1"""
        params: List[Any] = []
        if origin is not None:
            q += " AND d.data_origin=?"; params.append(str(origin))
        if admitted_only:
            q += " AND d.admitted=1"
        rows = []
        for r in self._con.execute(q, params).fetchall():
            pnl = r["pnl_sol"]
            if pnl is None:
                pnl = r["pnl_pct"]
            rows.append({"branch": r["branch"], "ts": float(r["ts"] or 0.0),
                         "features_json": r["features_json"], "pnl": float(pnl or 0.0)})
        return rows

    @classmethod
    def _liquidity_bucket(cls, features_json: Optional[str]) -> str:
        feats: Dict[str, Any] = {}
        if features_json:
            try:
                feats = json.loads(features_json)
            except (ValueError, TypeError):
                feats = {}
        val = None
        for key in cls.LIQ_FEATURE_KEYS:
            v = feats.get(key)
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f == f and f > 0:  # finito y positivo
                val = f
                break
        if val is None:
            return "unknown"
        for (lo, hi), label in zip(cls.LIQ_BUCKETS_SOL, cls.LIQ_BUCKET_LABELS):
            if lo <= val < hi:
                return label
        return "unknown"

    @staticmethod
    def _hour_fringe(ts: float) -> str:
        """Franja horaria UTC de 4 h (00-04, 04-08, ..., 20-24)."""
        h = int(ts // 3600.0) % 24
        fr = (h // 4) * 4
        return f"{fr:02d}-{fr + 4:02d}"

    def net_audit_by(self, group: str = "branch", origin: Optional[str] = "live",
                     admitted_only: bool = True) -> List[Dict[str, Any]]:
        """EV neto agrupado por 'branch' | 'liquidity' | 'hour' (franja UTC 4h)."""
        if group not in ("branch", "liquidity", "hour"):
            raise ValueError(f"group no soportado: {group!r} (usa branch|liquidity|hour)")
        rows = self._resolved_rows(origin=origin, admitted_only=admitted_only)
        buckets: Dict[str, List[float]] = {}
        for r in rows:
            if group == "branch":
                key = str(r["branch"])
            elif group == "hour":
                key = self._hour_fringe(r["ts"])
            else:
                key = self._liquidity_bucket(r["features_json"])
            buckets.setdefault(key, []).append(float(r["pnl"]))
        out = []
        for key, pnls in buckets.items():
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            gw, gl = float(sum(wins)), float(abs(sum(losses)))
            out.append({"group": group, "value": key, "n": len(pnls),
                        "winrate": (len(wins) / len(pnls)) if pnls else 0.0,
                        "profit_factor": gw / gl if gl > 1e-9 else float("inf"),
                        "total_pnl_sol": float(sum(pnls)),
                        "expectancy_r": float(sum(pnls) / len(pnls)) if pnls else 0.0})
        return sorted(out, key=lambda x: x["total_pnl_sol"], reverse=True)

    def branch_exit_policy(self, origin: Optional[str] = "live", admitted_only: bool = True,
                           capture_frac: float = 0.5, min_labeled: int = 3) -> List[Dict[str, Any]]:
        """Policy de salida por rama derivada de MFE/MAE reales persistidos.

        - suggested_take_profit_pct = capture_frac * mediana(MFE)  (captura parcial)
        - suggested_stop_pct        = -|mediana(MAE)|
        - sufficient=False cuando hay menos de ``min_labeled`` trades etiquetados.
        """
        q = """SELECT d.branch AS branch, o.mfe_pct AS mfe_pct, o.mae_pct AS mae_pct
               FROM live_decisions d JOIN live_outcomes o ON o.decision_id=d.id
               WHERE o.mfe_pct IS NOT NULL"""
        params: List[Any] = []
        if origin is not None:
            q += " AND d.data_origin=?"; params.append(str(origin))
        if admitted_only:
            q += " AND d.admitted=1"
        grouped: Dict[str, Dict[str, List[float]]] = {}
        for r in self._con.execute(q, params).fetchall():
            g = grouped.setdefault(str(r["branch"]), {"mfe": [], "mae": []})
            g["mfe"].append(float(r["mfe_pct"]))
            if r["mae_pct"] is not None:
                g["mae"].append(float(r["mae_pct"]))
        out = []
        for branch, g in grouped.items():
            mfe = sorted(g["mfe"])
            mae = sorted(g["mae"]) if g["mae"] else []
            med_mfe = mfe[len(mfe) // 2] if len(mfe) % 2 else 0.5 * (mfe[len(mfe) // 2 - 1] + mfe[len(mfe) // 2])
            med_mae = mae[len(mae) // 2] if len(mae) % 2 else (
                0.5 * (mae[len(mae) // 2 - 1] + mae[len(mae) // 2]) if mae else 0.0)
            n = len(mfe)
            out.append({"branch": branch, "n_labeled": n,
                        "median_mfe_pct": float(med_mfe),
                        "median_mae_pct": float(med_mae),
                        "suggested_take_profit_pct": round(float(capture_frac) * med_mfe, 1),
                        "suggested_stop_pct": round(-abs(float(med_mae)), 1),
                        "sufficient": n >= int(min_labeled)})
        return sorted(out, key=lambda x: x["median_mfe_pct"], reverse=True)

    # ------------------------------------------------------------------
    # Estado persistente del BranchGate (auto-disable de ramas)
    # ------------------------------------------------------------------
    def save_branch_state(self, row: Dict[str, Any]) -> None:
        self._con.execute("""INSERT OR REPLACE INTO branch_gate_state
            (branch,status,n,ev_sol,winrate,p_profitable,reason,decided_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (str(row["branch"]), str(row["status"]), int(row["n"]),
             row.get("ev_sol"), row.get("winrate"), row.get("p_profitable"),
             row.get("reason"), float(row.get("decided_at") or time.time())))
        self._con.commit()

    def get_branch_state(self, branch: str) -> Optional[Dict[str, Any]]:
        row = self._con.execute(
            "SELECT * FROM branch_gate_state WHERE branch=?", (str(branch),)).fetchone()
        return dict(row) if row is not None else None

    def list_branch_states(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._con.execute(
            "SELECT * FROM branch_gate_state ORDER BY decided_at DESC").fetchall()]

    def count_decisions(self, origin: Optional[str] = None, admitted_only: bool = False) -> int:
        q = "SELECT COUNT(*) AS c FROM live_decisions WHERE 1=1"; params: List[Any] = []
        if origin is not None: q += " AND data_origin=?"; params.append(str(origin))
        if admitted_only: q += " AND admitted=1"
        row = self._con.execute(q, params).fetchone()
        return int(row["c"]) if row else 0

    def count_outcomes(self) -> int:
        row = self._con.execute("SELECT COUNT(*) AS c FROM live_outcomes").fetchone()
        return int(row["c"]) if row else 0

    def close(self) -> None:
        try: self._con.close()
        except Exception: pass

    def __enter__(self) -> "LiveStore": return self
    def __exit__(self, exc_type, exc, tb) -> None: self.close()
