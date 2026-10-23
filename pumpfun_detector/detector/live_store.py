"""Persistencia y auditoría neta de decisiones de ejecución."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS token_dossiers (
 mint TEXT PRIMARY KEY,
 chain TEXT NOT NULL DEFAULT 'sol',
 symbol TEXT NOT NULL,
 name TEXT NOT NULL,
 first_seen_ts REAL NOT NULL,
 updated_ts REAL NOT NULL,
 market_cap_usd REAL NOT NULL DEFAULT 0.0,
 liquidity_usd REAL NOT NULL DEFAULT 0.0,
 holder_count INTEGER NOT NULL DEFAULT 0,
 top10_raw_pct REAL,
 top10_float_pct REAL,
 top10_source TEXT,
 top10_flag TEXT,
 dev_balance_pct REAL,
 dev_balance_known INTEGER NOT NULL DEFAULT 0,
 bundler_pct REAL,
 bundler_source TEXT,
 sniper_pct REAL,
 sniper_source TEXT,
 sniper_is_proxy INTEGER NOT NULL DEFAULT 0,
 bundler_flag INTEGER NOT NULL DEFAULT 0,
 wash_trading_flag INTEGER NOT NULL DEFAULT 0,
 avg_tx_m5_usd REAL NOT NULL DEFAULT 0.0,
 same_name_count INTEGER NOT NULL DEFAULT 1,
 dead_clones_count INTEGER NOT NULL DEFAULT 0,
 clone_factory_flag INTEGER NOT NULL DEFAULT 0,
 avatar_hash TEXT,
 avatar_reused_count INTEGER NOT NULL DEFAULT 1,
 creator_address TEXT,
 creator_funder TEXT,
 dev_total_launches INTEGER,
 dev_graduated_count INTEGER,
 dev_best_ath_mc_usd REAL,
 dev_history_known INTEGER NOT NULL DEFAULT 0,
 dev_factory_flag INTEGER NOT NULL DEFAULT 0,
 composite_dd_score REAL NOT NULL DEFAULT 0.0,
 admitted INTEGER NOT NULL DEFAULT 0,
 verdict TEXT NOT NULL DEFAULT 'VETO',
 veto_reasons_json TEXT,
 why_price_moves TEXT,
 actionable_playbook TEXT,
 dossier_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dossiers_updated ON token_dossiers(updated_ts DESC);
CREATE INDEX IF NOT EXISTS idx_dossiers_verdict ON token_dossiers(verdict);
CREATE TABLE IF NOT EXISTS token_snapshots_hf (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mint TEXT NOT NULL,
 ts REAL NOT NULL,
 mc_usd REAL NOT NULL DEFAULT 0.0,
 liquidity_usd REAL NOT NULL DEFAULT 0.0,
 buys_m5 INTEGER NOT NULL DEFAULT 0,
 sells_m5 INTEGER NOT NULL DEFAULT 0,
 vol_m5_usd REAL NOT NULL DEFAULT 0.0,
 avg_tx_usd REAL NOT NULL DEFAULT 0.0,
 holder_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hf_snap_mint_ts ON token_snapshots_hf(mint, ts ASC);
CREATE TABLE IF NOT EXISTS avatar_fingerprint_index (
 avatar_hash TEXT NOT NULL,
 mint TEXT NOT NULL,
 symbol TEXT NOT NULL,
 seen_ts REAL NOT NULL,
 PRIMARY KEY (avatar_hash, mint)
);
CREATE INDEX IF NOT EXISTS idx_avatar_hash ON avatar_fingerprint_index(avatar_hash);
CREATE TABLE IF NOT EXISTS shadow_veto_ledger (
 mint TEXT PRIMARY KEY,
 symbol TEXT NOT NULL,
 veto_reason TEXT NOT NULL,
 veto_ts REAL NOT NULL,
 entry_mc_usd REAL NOT NULL DEFAULT 0.0,
 current_mc_usd REAL NOT NULL DEFAULT 0.0,
 peak_mc_usd REAL NOT NULL DEFAULT 0.0,
 trough_mc_usd REAL NOT NULL DEFAULT 0.0,
 last_check_ts REAL NOT NULL,
 checks_count INTEGER NOT NULL DEFAULT 0,
 counterfactual_pnl_pct REAL NOT NULL DEFAULT 0.0,
 verdict TEXT NOT NULL DEFAULT 'PENDING',
 summary_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_shadow_veto_ts ON shadow_veto_ledger(veto_ts DESC);
CREATE TABLE IF NOT EXISTS paper_positions_state (
 pos_id TEXT PRIMARY KEY,
 mint TEXT NOT NULL,
 symbol TEXT NOT NULL,
 status TEXT NOT NULL,
 entry_time REAL NOT NULL,
 updated_ts REAL NOT NULL,
 payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_pos_status ON paper_positions_state(status, updated_ts DESC);
CREATE TABLE IF NOT EXISTS auto_evolution_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts REAL NOT NULL,
 event_type TEXT NOT NULL,
 mint TEXT NOT NULL,
 symbol TEXT NOT NULL,
 pnl_pct REAL NOT NULL DEFAULT 0.0,
 diagnosis TEXT NOT NULL,
 rule_adjusted TEXT NOT NULL DEFAULT '',
 old_val TEXT NOT NULL DEFAULT '',
 new_val TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auto_evo_ts ON auto_evolution_log(ts DESC);
CREATE TABLE IF NOT EXISTS decision_traces (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 trace_id TEXT NOT NULL,
 mint TEXT NOT NULL,
 symbol TEXT NOT NULL DEFAULT '',
 ts REAL NOT NULL,
 stage TEXT NOT NULL,
 value REAL,
 threshold REAL,
 operator TEXT,
 verdict TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT '',
 source_kind TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_decision_traces_ts ON decision_traces(ts DESC);
CREATE INDEX IF NOT EXISTS idx_decision_traces_mint ON decision_traces(mint, ts DESC);
CREATE INDEX IF NOT EXISTS idx_decision_traces_trace ON decision_traces(trace_id);
"""


def _decision_sig(mint: str, branch: str, admitted: bool, data_origin: str = "live",
                  entry_ts: Optional[float] = None) -> str:
    raw = f"{mint}|{branch}|{int(bool(admitted))}|{data_origin}|{entry_ts if entry_ts is not None else ''}".encode()
    return hashlib.sha256(raw).hexdigest()


def _opt_float(val: Any) -> Optional[float]:
    """Convierte a float o None (anti-falacia: nunca inventa 0.0 para un dato ausente)."""
    if val is None or isinstance(val, bool):
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    return out


def _opt_int(val: Any) -> Optional[int]:
    """Convierte a int o None (misma ley: ausencia != cero)."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


# Columnas de medición que DEBEN poder expresar "sin dato" (NULL).
_DOSSIER_NULLABLE = (
    "top10_raw_pct", "top10_float_pct", "dev_balance_pct",
    "bundler_pct", "sniper_pct",
    "dev_total_launches", "dev_graduated_count", "dev_best_ath_mc_usd",
)

# Procedencia explícita: qué se midió, con qué método, y qué es solo un proxy.
_DOSSIER_PROVENANCE = (
    ("top10_source", "TEXT"),
    ("top10_flag", "TEXT"),
    ("dev_balance_known", "INTEGER NOT NULL DEFAULT 0"),
    ("bundler_source", "TEXT"),
    ("sniper_source", "TEXT"),
    ("sniper_is_proxy", "INTEGER NOT NULL DEFAULT 0"),
    ("dev_history_known", "INTEGER NOT NULL DEFAULT 0"),
)


class LiveStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # SQLite NO es thread-safe: compartir una única conexión
        # (``check_same_thread=False``) entre el bucle autónomo, el ciclo de
        # automejora y los hilos HTTP producía pérdidas silenciosas de trazas
        # ("bad parameter or other API misuse" / commit NULL). Una conexión por
        # hilo + ``busy_timeout`` elimina el problema sin cambiar el esquema.
        self._local = threading.local()
        self._cons_lock = threading.Lock()
        self._all_cons: List[sqlite3.Connection] = []
        self._migrate_optional_columns()
        self._con.commit()

    def _new_conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA synchronous=NORMAL;")
            con.execute("PRAGMA busy_timeout=10000;")
        except Exception:
            pass
        try:
            con.executescript(_SCHEMA)
        except Exception:
            pass  # el esquema ya existe en el archivo compartido
        with self._cons_lock:
            self._all_cons.append(con)
        return con

    @property
    def _con(self) -> sqlite3.Connection:
        """Conexión del hilo actual (creada bajo demanda)."""
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._new_conn()
            self._local.con = con
        return con

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
        self._migrate_dossiers_nullable()

    def _migrate_dossiers_nullable(self) -> None:
        """Permite NULL donde el dato puede no existir y añade procedencia.

        SQLite no elimina ``NOT NULL`` con ``ALTER``: una tabla legacy se
        reconstruye copiando las filas tal cual (jamás se inventan valores).
        """
        info = self._con.execute("PRAGMA table_info(token_dossiers)").fetchall()
        if not info:
            return
        cols = {r[1]: r for r in info}
        needs_rebuild = any(
            name in cols and int(cols[name][3] or 0) != 0 for name in _DOSSIER_NULLABLE
        )
        if needs_rebuild:
            self._rebuild_dossiers_nullable(info)
            return
        for name, decl in _DOSSIER_PROVENANCE:
            if name not in cols:
                self._con.execute(f"ALTER TABLE token_dossiers ADD COLUMN {name} {decl}")
        self._con.commit()

    def _rebuild_dossiers_nullable(self, info: List[Any]) -> None:
        old_names = [r[1] for r in info]
        decl: List[str] = []
        for r in info:
            name = r[1]
            typ = r[2] or "TEXT"
            notnull = int(r[3] or 0)
            dflt = r[4]
            pk = int(r[5] or 0)
            if name in _DOSSIER_NULLABLE:
                notnull = 0
            parts = [name, typ]
            if pk:
                parts.append("PRIMARY KEY")
            elif notnull:
                parts.append("NOT NULL")
            if dflt is not None:
                parts.append(f"DEFAULT {dflt}")
            decl.append(" ".join(parts))
        for name, ddl in _DOSSIER_PROVENANCE:
            if name not in old_names:
                decl.append(f"{name} {ddl}")
        tmp = "token_dossiers__nullable"
        select_cols = ", ".join(old_names)
        try:
            self._con.execute(f"DROP TABLE IF EXISTS {tmp}")
            self._con.execute(f"CREATE TABLE {tmp} ({', '.join(decl)})")
            self._con.execute(
                f"INSERT INTO {tmp} ({select_cols}) SELECT {select_cols} FROM token_dossiers"
            )
            self._con.execute("DROP TABLE token_dossiers")
            self._con.execute(f"ALTER TABLE {tmp} RENAME TO token_dossiers")
            if "top10_source" not in old_names:
                # Filas legacy: su procedencia real es desconocida. Se etiqueta
                # como no verificada, jamás como medición limpia.
                self._con.execute(
                    "UPDATE token_dossiers SET top10_source = CASE "
                    "WHEN top10_float_pct IS NULL THEN 'unavailable' ELSE 'legacy_unverified' END"
                )
                self._con.execute(
                    "UPDATE token_dossiers SET bundler_source = CASE "
                    "WHEN bundler_pct IS NULL THEN 'unavailable' ELSE 'legacy_unverified' END"
                )
                self._con.execute(
                    "UPDATE token_dossiers SET sniper_source = CASE "
                    "WHEN sniper_pct IS NULL THEN 'unavailable' ELSE 'legacy_unverified' END"
                )
                self._con.execute(
                    "UPDATE token_dossiers SET sniper_is_proxy = 1 "
                    "WHERE sniper_pct IS NOT NULL AND bundler_pct IS NOT NULL "
                    "AND ABS(sniper_pct - bundler_pct) < 1e-9"
                )
                self._con.execute("UPDATE token_dossiers SET dev_history_known = 0")
                self._con.execute("UPDATE token_dossiers SET dev_balance_known = 0")
            self._con.executescript(
                "CREATE INDEX IF NOT EXISTS idx_dossiers_updated ON token_dossiers(updated_ts DESC);"
                "CREATE INDEX IF NOT EXISTS idx_dossiers_verdict ON token_dossiers(verdict);"
            )
            self._con.commit()
        except Exception as exc:
            self._con.rollback()
            raise RuntimeError(
                f"Migración de token_dossiers a columnas NULL falló: {exc}"
            ) from exc

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

    # ------------------------------------------------------------------
    # Expedientes Forenses Estilo GMGN (Token Dossiers & HF Snapshots)
    # ------------------------------------------------------------------
    def save_token_dossier(self, dossier: Dict[str, Any]) -> None:
        """Autoguarda o actualiza el expediente forense completo de un token estilo GMGN."""
        mint = str(dossier.get("mint") or "").strip()
        if not mint:
            return
        now = float(dossier.get("timestamp") or time.time())
        existing = self._con.execute(
            "SELECT first_seen_ts FROM token_dossiers WHERE mint = ?", (mint,)
        ).fetchone()
        first_seen = float(existing["first_seen_ts"]) if existing else now
        veto_reasons = dossier.get("veto_reasons") or []
        top10_raw = _opt_float(dossier.get("top10_raw_pct"))
        top10_float = _opt_float(dossier.get("top10_float_pct"))
        dev_balance = _opt_float(dossier.get("dev_balance_pct"))
        bundler = _opt_float(dossier.get("bundler_pct"))
        sniper = _opt_float(dossier.get("sniper_pct"))
        launches = _opt_int(dossier.get("dev_total_launches"))
        graduated = _opt_int(dossier.get("dev_graduated_count"))
        best_ath = _opt_float(dossier.get("dev_best_ath_mc_usd"))

        def _source(key: str, measured: bool, fallback: str) -> str:
            explicit = dossier.get(key)
            if explicit:
                return str(explicit)
            return fallback if measured else "unavailable"

        top10_source = _source("top10_source", top10_float is not None, "legacy_unverified")
        bundler_source = _source("bundler_source", bundler is not None, "legacy_unverified")
        sniper_source = _source("sniper_source", sniper is not None, "legacy_unverified")
        sniper_is_proxy = bool(dossier.get("sniper_is_proxy"))
        if (not sniper_is_proxy and sniper is not None and bundler is not None
                and abs(sniper - bundler) < 1e-9):
            sniper_is_proxy = True
            if not dossier.get("sniper_source"):
                sniper_source = "proxy:bundler_uniform_cluster"
        dev_balance_known = bool(
            dossier.get("dev_balance_known", dev_balance is not None)
        )
        dev_history_known = bool(
            dossier.get("dev_history_known",
                        any(v is not None for v in (launches, graduated, best_ath)))
        )
        self._con.execute(
            """
            INSERT OR REPLACE INTO token_dossiers (
                mint, chain, symbol, name, first_seen_ts, updated_ts,
                market_cap_usd, liquidity_usd, holder_count,
                top10_raw_pct, top10_float_pct, top10_source, top10_flag,
                dev_balance_pct, dev_balance_known,
                bundler_pct, bundler_source,
                sniper_pct, sniper_source, sniper_is_proxy,
                bundler_flag, wash_trading_flag, avg_tx_m5_usd,
                same_name_count, dead_clones_count, clone_factory_flag,
                avatar_hash, avatar_reused_count,
                creator_address, creator_funder, dev_total_launches,
                dev_graduated_count, dev_best_ath_mc_usd, dev_history_known,
                dev_factory_flag, composite_dd_score, admitted, verdict,
                veto_reasons_json, why_price_moves, actionable_playbook, dossier_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mint,
                str(dossier.get("chain") or "sol"),
                str(dossier.get("symbol") or "UNKNOWN"),
                str(dossier.get("name") or "Unknown"),
                first_seen,
                now,
                float(dossier.get("market_cap_usd") or 0.0),
                float(dossier.get("liquidity_usd") or 0.0),
                int(dossier.get("holder_count") or 0),
                top10_raw,
                top10_float,
                top10_source,
                dossier.get("top10_flag"),
                dev_balance,
                int(dev_balance_known),
                bundler,
                bundler_source,
                sniper,
                sniper_source,
                int(sniper_is_proxy),
                int(bool(dossier.get("bundler_flag"))),
                int(bool(dossier.get("wash_trading_flag"))),
                float(dossier.get("avg_tx_m5_usd") or 0.0),
                int(dossier.get("same_name_count") or 1),
                int(dossier.get("dead_clones_count") or 0),
                int(bool(dossier.get("clone_factory_flag"))),
                str(dossier.get("avatar_hash") or ""),
                int(dossier.get("avatar_reused_count") or 1),
                str(dossier.get("creator_address") or "unknown"),
                str(dossier.get("creator_funder") or ""),
                launches,
                graduated,
                best_ath,
                int(dev_history_known),
                int(bool(dossier.get("dev_factory_flag"))),
                float(dossier.get("composite_dd_score") or 0.0),
                int(bool(dossier.get("admitted"))),
                str(dossier.get("verdict") or "VETO"),
                json.dumps(veto_reasons, ensure_ascii=False),
                str(dossier.get("why_price_moves") or ""),
                str(dossier.get("actionable_playbook") or ""),
                json.dumps(dossier, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        self._con.commit()

    def get_token_dossier(self, mint: str) -> Optional[Dict[str, Any]]:
        """Recupera el expediente forense de un token por mint/address."""
        row = self._con.execute(
            "SELECT dossier_json FROM token_dossiers WHERE mint = ?", (str(mint).strip(),)
        ).fetchone()
        if not row or not row["dossier_json"]:
            return None
        try:
            return json.loads(row["dossier_json"])
        except Exception:
            return None

    def list_recent_dossiers(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Lista los expedientes autoguardados más recientes."""
        rows = self._con.execute(
            "SELECT dossier_json FROM token_dossiers ORDER BY updated_ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r["dossier_json"]))
            except Exception:
                continue
        return out

    def record_hf_snapshot(
        self,
        mint: str,
        mc_usd: float,
        liquidity_usd: float,
        buys_m5: int = 0,
        sells_m5: int = 0,
        vol_m5_usd: float = 0.0,
        holder_count: int = 0,
        ts: Optional[float] = None,
    ) -> None:
        """Guarda un punto de serie temporal de alta frecuencia para auditar la trayectoria del token."""
        now = float(ts if ts is not None else time.time())
        tx_cnt = max(1, int(buys_m5) + int(sells_m5))
        avg_tx = float(vol_m5_usd) / tx_cnt if (buys_m5 + sells_m5) > 0 else 0.0
        self._con.execute(
            """
            INSERT INTO token_snapshots_hf
            (mint, ts, mc_usd, liquidity_usd, buys_m5, sells_m5, vol_m5_usd, avg_tx_usd, holder_count)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                str(mint).strip(),
                now,
                float(mc_usd),
                float(liquidity_usd),
                int(buys_m5),
                int(sells_m5),
                float(vol_m5_usd),
                round(avg_tx, 2),
                int(holder_count),
            ),
        )
        self._con.commit()

    def get_token_trajectory(self, mint: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Devuelve la serie temporal de snapshots guardados para un token."""
        rows = self._con.execute(
            """
            SELECT ts, mc_usd, liquidity_usd, buys_m5, sells_m5, vol_m5_usd, avg_tx_usd, holder_count
            FROM token_snapshots_hf
            WHERE mint = ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (str(mint).strip(), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def register_avatar(self, mint: str, symbol: str, avatar_hash: str, ts: Optional[float] = None) -> int:
        """Registra el hash perceptual del avatar de un token y devuelve cuántos tokens distintos lo reutilizan."""
        if not avatar_hash or not mint:
            return 1
        now = float(ts if ts is not None else time.time())
        self._con.execute(
            """
            INSERT OR IGNORE INTO avatar_fingerprint_index (avatar_hash, mint, symbol, seen_ts)
            VALUES (?,?,?,?)
            """,
            (str(avatar_hash), str(mint).strip(), str(symbol), now),
        )
        self._con.commit()
        row = self._con.execute(
            "SELECT COUNT(DISTINCT mint) AS c FROM avatar_fingerprint_index WHERE avatar_hash = ?",
            (str(avatar_hash),),
        ).fetchone()
        return int(row["c"]) if row else 1

    def upsert_shadow_veto(
        self,
        mint: str,
        symbol: str,
        veto_reason: str,
        entry_mc_usd: float,
        ts: Optional[float] = None,
    ) -> None:
        """Registra un token vetado en el libro de sombra para evaluar más tarde si el veto salvó capital o perdió subida."""
        if not mint or float(entry_mc_usd or 0.0) <= 0.0:
            return
        now = float(ts if ts is not None else time.time())
        mc = float(entry_mc_usd)
        self._con.execute(
            """
            INSERT OR IGNORE INTO shadow_veto_ledger
            (mint, symbol, veto_reason, veto_ts, entry_mc_usd, current_mc_usd, peak_mc_usd, trough_mc_usd, last_check_ts, checks_count, counterfactual_pnl_pct, verdict, summary_text)
            VALUES (?,?,?,?,?,?,?,?,?,0,0.0,'PENDING',?)
            """,
            (
                str(mint).strip(),
                str(symbol or "UNKNOWN"),
                str(veto_reason or "VETO"),
                now,
                mc,
                mc,
                mc,
                mc,
                now,
                f"Vetado a ${mc:,.0f} MC ({veto_reason}) — monitoreando desenlace real...",
            ),
        )
        self._con.commit()

    def update_shadow_veto_price(
        self,
        mint: str,
        current_mc_usd: float,
        ts: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Actualiza el precio real de un token vetado y dictamina: 'ese trade vetado hubiera perdido/ganado'."""
        row = self._con.execute(
            "SELECT * FROM shadow_veto_ledger WHERE mint = ?",
            (str(mint).strip(),),
        ).fetchone()
        if not row:
            return None
        now = float(ts if ts is not None else time.time())
        entry_mc = float(row["entry_mc_usd"] or 1.0)
        cur_mc = float(current_mc_usd or 0.0)
        if cur_mc <= 0.0:
            return dict(row)

        peak_mc = max(float(row["peak_mc_usd"] or entry_mc), cur_mc)
        trough_mc = min(float(row["trough_mc_usd"] or entry_mc), cur_mc) if float(row["trough_mc_usd"] or 0.0) > 0 else cur_mc
        checks = int(row["checks_count"] or 0) + 1
        # Restamos fricción canónica (~5.2% round-trip en micro-cap) para saber qué habría pasado realmente
        raw_pnl_pct = ((cur_mc - entry_mc) / max(entry_mc, 1.0)) * 100.0
        net_cf_pnl_pct = round(raw_pnl_pct - 5.2, 2)
        drawdown_from_entry_pct = ((trough_mc - entry_mc) / max(entry_mc, 1.0)) * 100.0

        if net_cf_pnl_pct <= -12.0 or drawdown_from_entry_pct <= -25.0:
            verdict = "TRUE_SAVE"
            summary = (
                f"SALVADA REAL (TRUE_SAVE): ese trade vetado ({row['symbol']}) hubiera PERDIDO "
                f"{net_cf_pnl_pct:+.1f}% neto (MC cayó de ${entry_mc:,.0f} a ${cur_mc:,.0f}). Veto correcto: {row['veto_reason']}."
            )
        elif net_cf_pnl_pct >= 28.0 and drawdown_from_entry_pct > -22.0:
            verdict = "FALSE_VETO"
            summary = (
                f"FALSO VETO (FALSE_VETO): ese trade vetado ({row['symbol']}) subió {net_cf_pnl_pct:+.1f}% neto "
                f"(MC de ${entry_mc:,.0f} a ${cur_mc:,.0f}) sin tocar SL. Revisar severidad de: {row['veto_reason']}."
            )
        elif net_cf_pnl_pct < 0.0:
            verdict = "TRUE_SAVE"
            summary = (
                f"SALVADA POR FRICCIÓN: ese trade vetado ({row['symbol']}) daría {net_cf_pnl_pct:+.1f}% neto "
                f"tras costes (${entry_mc:,.0f} -> ${cur_mc:,.0f})."
            )
        else:
            verdict = "NEUTRAL_HOLD"
            summary = (
                f"EN OBSERVACIÓN: {row['symbol']} se mueve {net_cf_pnl_pct:+.1f}% neto (${entry_mc:,.0f} -> ${cur_mc:,.0f})."
            )

        self._con.execute(
            """
            UPDATE shadow_veto_ledger
            SET current_mc_usd = ?, peak_mc_usd = ?, trough_mc_usd = ?, last_check_ts = ?,
                checks_count = ?, counterfactual_pnl_pct = ?, verdict = ?, summary_text = ?
            WHERE mint = ?
            """,
            (
                round(cur_mc, 2),
                round(peak_mc, 2),
                round(trough_mc, 2),
                now,
                checks,
                net_cf_pnl_pct,
                verdict,
                summary,
                str(mint).strip(),
            ),
        )
        self._con.commit()
        updated = self._con.execute(
            "SELECT * FROM shadow_veto_ledger WHERE mint = ?", (str(mint).strip(),)
        ).fetchone()
        return dict(updated) if updated else None

    def list_shadow_vetos(self, limit: int = 30) -> List[Dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM shadow_veto_ledger ORDER BY veto_ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_shadow_vetos_for_check(self, limit: int = 12) -> List[Dict[str, Any]]:
        """Vetos que toca re-evaluar: los menos recientemente chequeados primero.

        Evita el hambre de los vetos viejos (siempre elegir los más nuevos dejaría
        a los antiguos con `checks_count=0` para siempre).
        """
        rows = self._con.execute(
            "SELECT * FROM shadow_veto_ledger ORDER BY last_check_ts ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- trazas de decisión (P1)
    def append_decision_trace(self, trace_id: str, mint: str, symbol: str, ts: float,
                              stages: List[Dict[str, Any]], max_rows: int = 20000) -> int:
        """Persiste una decisión completa del pipeline (una fila por etapa E0→E6).

        Retención acotada: tras insertar poda la tabla a ``max_rows`` filas.
        Devuelve el número de etapas insertadas.
        """
        trace_id = str(trace_id or "").strip()
        mint = str(mint or "").strip()
        if not trace_id or not mint:
            return 0
        now = float(ts if ts is not None else time.time())
        payload = []
        for s in stages or []:
            if not isinstance(s, dict) or not s.get("stage"):
                continue
            payload.append((
                trace_id, mint, str(symbol or ""), now, str(s.get("stage")),
                _opt_float(s.get("value")), _opt_float(s.get("threshold")),
                s.get("operator"), str(s.get("verdict") or "info"),
                str(s.get("reason") or ""), str(s.get("source") or ""),
                str(s.get("source_kind") or ""),
            ))
        if not payload:
            return 0
        self._con.executemany(
            """INSERT INTO decision_traces
               (trace_id, mint, symbol, ts, stage, value, threshold, operator,
                verdict, reason, source, source_kind)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )
        high_water = self._con.execute("SELECT MAX(id) FROM decision_traces").fetchone()[0]
        if high_water is not None:
            self._con.execute("DELETE FROM decision_traces WHERE id <= ?",
                              (int(high_water) - int(max_rows),))
        self._con.commit()
        return len(payload)

    def prune_decision_traces(self, max_rows: int = 20000) -> int:
        """Recorta la tabla a las últimas ``max_rows`` filas. Devuelve filas borradas."""
        high_water = self._con.execute("SELECT MAX(id) FROM decision_traces").fetchone()[0]
        if high_water is None:
            return 0
        cur = self._con.execute("DELETE FROM decision_traces WHERE id <= ?",
                                (int(high_water) - int(max_rows),))
        self._con.commit()
        return int(cur.rowcount or 0)

    def _traces_from_ids(self, trace_ids: List[str]) -> List[Dict[str, Any]]:
        if not trace_ids:
            return []
        marks = ",".join("?" for _ in trace_ids)
        rows = self._con.execute(
            f"SELECT * FROM decision_traces WHERE trace_id IN ({marks}) ORDER BY id ASC",
            [str(t) for t in trace_ids],
        ).fetchall()
        grouped: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            item = dict(r)
            t_id = item["trace_id"]
            g = grouped.setdefault(t_id, {
                "trace_id": t_id, "mint": item["mint"], "symbol": item["symbol"],
                "ts": item["ts"], "stages": [],
            })
            g["stages"].append({
                "stage": item["stage"], "value": item["value"],
                "threshold": item["threshold"], "operator": item["operator"],
                "verdict": item["verdict"], "reason": item["reason"],
                "source": item["source"], "source_kind": item["source_kind"],
            })
        # Orden por ts descendente (más reciente primero), igual que la selección de ids.
        ordered = sorted(grouped.values(), key=lambda g: float(g.get("ts") or 0.0), reverse=True)
        return ordered

    def list_decision_traces(self, limit: int = 30, max_age_s: Optional[float] = None) -> List[Dict[str, Any]]:
        """Últimas ``limit`` decisiones completas (agrupadas por trace_id), más nuevas primero."""
        params: List[Any] = []
        where = ""
        if max_age_s is not None:
            where = "WHERE ts >= ?"
            params.append(time.time() - float(max_age_s))
        id_rows = self._con.execute(
            f"""SELECT trace_id, MAX(ts) AS mts FROM decision_traces {where}
                GROUP BY trace_id ORDER BY mts DESC LIMIT ?""",
            params + [int(limit)],
        ).fetchall()
        return self._traces_from_ids([r["trace_id"] for r in id_rows])

    def get_decision_trace(self, mint: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Trazas de un mint concreto, más recientes primero."""
        id_rows = self._con.execute(
            """SELECT trace_id, MAX(ts) AS mts FROM decision_traces
               WHERE mint = ? GROUP BY trace_id ORDER BY mts DESC LIMIT ?""",
            (str(mint or "").strip(), int(limit)),
        ).fetchall()
        return self._traces_from_ids([r["trace_id"] for r in id_rows])

    def save_paper_position(self, pos_dict: Dict[str, Any]) -> None:
        """Persiste el estado completo de una posición Paper para sobrevivir reinicios."""
        pos_id = str(pos_dict.get("id") or pos_dict.get("pos_id") or "")
        if not pos_id:
            return
        now = time.time()
        self._con.execute(
            """
            INSERT INTO paper_positions_state (pos_id, mint, symbol, status, entry_time, updated_ts, payload_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(pos_id) DO UPDATE SET
                status = excluded.status,
                updated_ts = excluded.updated_ts,
                payload_json = excluded.payload_json
            """,
            (
                pos_id,
                str(pos_dict.get("mint") or ""),
                str(pos_dict.get("symbol") or "UNKNOWN"),
                str(pos_dict.get("status") or "OPEN"),
                float(pos_dict.get("entry_time") or now),
                now,
                json.dumps(pos_dict, ensure_ascii=False),
            ),
        )
        self._con.commit()

    def load_paper_positions(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Recupera las posiciones Paper persistidas en SQLite."""
        rows = self._con.execute(
            "SELECT payload_json FROM paper_positions_state ORDER BY entry_time ASC LIMIT ?",
            (int(limit),),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r["payload_json"]))
            except Exception:
                continue
        return out

    def record_evolution_event(
        self,
        event_type: str,
        mint: str,
        symbol: str,
        pnl_pct: float,
        diagnosis: str,
        rule_adjusted: str = "",
        old_val: str = "",
        new_val: str = "",
        notes: str = "",
        ts: Optional[float] = None,
    ) -> int:
        """Guarda un evento de auto-mejora / aprendizaje post-trade en SQLite."""
        now = float(ts if ts is not None else time.time())
        cur = self._con.execute(
            """
            INSERT INTO auto_evolution_log
            (ts, event_type, mint, symbol, pnl_pct, diagnosis, rule_adjusted, old_val, new_val, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now,
                str(event_type),
                str(mint),
                str(symbol),
                round(float(pnl_pct or 0.0), 2),
                str(diagnosis),
                str(rule_adjusted),
                str(old_val),
                str(new_val),
                str(notes),
            ),
        )
        self._con.commit()
        return int(cur.lastrowid or 0)

    def list_evolution_events(self, limit: int = 40) -> List[Dict[str, Any]]:
        rows = self._con.execute(
            "SELECT * FROM auto_evolution_log ORDER BY ts DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        """Cierra TODAS las conexiones por hilo abiertas por este store."""
        with self._cons_lock:
            cons, self._all_cons = self._all_cons, []
        self._local = threading.local()
        for con in cons:
            try: con.close()
            except Exception: pass

    def __enter__(self) -> "LiveStore": return self
    def __exit__(self, exc_type, exc, tb) -> None: self.close()

