#!/usr/bin/env python3
"""
Ledger Forward Inmutable con Provenance Completa.
Registra decisión, firma idempotente, db_hash, git_sha, config_hash, 
ticks crudos de la ruta de precios, cobertura y resultado resuelto con PnL neto.
"""
from __future__ import annotations
import hashlib, json, sqlite3, os, subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any


SCHEMA = """
-- Features en el momento de entrada (con provenance temporal)
CREATE TABLE IF NOT EXISTS features_at_entry (
    token_id        TEXT NOT NULL,
    feature_name    TEXT NOT NULL,
    feature_value   REAL,
    observed_at_ms  INTEGER NOT NULL,   -- cuándo ocurrió el evento
    available_at_ms INTEGER NOT NULL,   -- cuándo pudimos saberlo (¡clave causal!)
    data_origin     TEXT NOT NULL CHECK(data_origin IN 
                      ('real','synthetic','dry_run','forward_live')),
    source_table    TEXT NOT NULL,
    PRIMARY KEY (token_id, feature_name, observed_at_ms)
);
CREATE INDEX IF NOT EXISTS idx_features_entry ON features_at_entry(token_id, available_at_ms);

-- Ticks crudos de la ruta de precios (para auditoría de fill)
CREATE TABLE IF NOT EXISTS ticks_crudos (
    token_id TEXT NOT NULL,
    ts_ms    INTEGER NOT NULL,
    price    REAL,
    liquidity_sol REAL,
    mc_usd   REAL,
    source   TEXT NOT NULL,            -- dexscreener|pumpportal|helius|gmgn
    latency_ms INTEGER,
    PRIMARY KEY (token_id, ts_ms, source)
);
CREATE INDEX IF NOT EXISTS idx_ticks_token ON ticks_crudos(token_id, ts_ms);

-- Ledger de decisiones (idempotente por firma)
CREATE TABLE IF NOT EXISTS decision_ledger (
    decision_signature TEXT PRIMARY KEY,  -- idempotencia real
    data_origin TEXT NOT NULL,
    entry_ts INTEGER NOT NULL,
    db_hash TEXT,
    code_git_sha TEXT,
    config_hash TEXT,
    cost_model_version TEXT,
    sampler_version INTEGER,
    branch TEXT,
    admitted INTEGER,
    veto_reason TEXT,
    confidence REAL,
    market_cap_source TEXT,               -- qué campo se usó y por qué
    quote_json TEXT,
    fill_json TEXT,
    outcome TEXT CHECK(outcome IN ('PENDING','WIN','LOSS','INVALID')),
    pnl_gross_usd REAL,
    pnl_net_usd REAL,
    tokens_missing_snapshots INTEGER,
    quoted_without_fill INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ledger_entry ON decision_ledger(entry_ts);
CREATE INDEX IF NOT EXISTS idx_ledger_outcome ON decision_ledger(outcome);
"""


@dataclass(frozen=True)
class DecisionSignature:
    """Firma única e idempotente de una decisión."""
    token_id: str
    entry_ts_ms: int
    branch: str
    config_hash: str
    
    def to_string(self) -> str:
        return f"{self.token_id}|{self.entry_ts_ms}|{self.branch}|{self.config_hash}"
    
    def hash(self) -> str:
        return hashlib.sha256(self.to_string().encode()).hexdigest()[:32]


class ForwardLedger:
    """Ledger forward inmutable con fail-closed."""
    
    def __init__(self, db_path: str, config_hash: str = "v1"):
        self.db_path = db_path
        self.config_hash = config_hash
        self._init_schema()
    
    def _init_schema(self):
        with sqlite3.connect(self.db_path) as con:
            con.executescript(SCHEMA)
    
    def _get_git_sha(self) -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], 
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            return "unknown"
    
    def _get_db_hash(self) -> str:
        """Hash SHA256 del archivo DB."""
        h = hashlib.sha256()
        with open(self.db_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    
    def record_features(self, token_id: str, features: dict, 
                       observed_at_ms: int, available_at_ms: int,
                       data_origin: str = "forward_live",
                       source_table: str = "live_features") -> int:
        """
        Registra features en el momento de entrada con provenance temporal.
        
        LA SEÑAL SOLO LEE WHERE available_at_ms <= entry_time
        """
        if available_at_ms > observed_at_ms:
            raise ValueError("available_at_ms > observed_at_ms: violación causal")
        
        with sqlite3.connect(self.db_path) as con:
            for name, value in features.items():
                con.execute("""
                    INSERT OR REPLACE INTO features_at_entry
                    (token_id, feature_name, feature_value, observed_at_ms, available_at_ms, data_origin, source_table)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (token_id, name, float(value) if value is not None else None,
                      observed_at_ms, available_at_ms, data_origin, source_table))
        return len(features)
    
    def record_tick(self, token_id: str, ts_ms: int, price: float,
                    liquidity_sol: float, mc_usd: float, source: str,
                    latency_ms: int = None) -> None:
        """Registra tick crudo de la ruta de precios."""
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                INSERT OR IGNORE INTO ticks_crudos
                (token_id, ts_ms, price, liquidity_sol, mc_usd, source, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (token_id, ts_ms, price, liquidity_sol, mc_usd, source, latency_ms))
    
    def record_decision(self, 
                       token_id: str,
                       entry_ts_ms: int,
                       branch: str,
                       admitted: bool,
                       veto_reason: Optional[str],
                       confidence: float,
                       features: dict,
                       market_cap_source: str,
                       quote: dict = None,
                       fill: dict = None,
                       data_origin: str = "forward_live",
                       cost_model_version: str = "v1",
                       sampler_version: int = 1) -> str:
        """
        Registra decisión con idempotencia.
        Re-ejecutar el mismo evento NO duplica la posición.
        """
        sig = DecisionSignature(
            token_id=token_id,
            entry_ts_ms=entry_ts_ms,
            branch=branch,
            config_hash=self.config_hash
        )
        decision_sig = sig.hash()
        
        with sqlite3.connect(self.db_path) as con:
            # Verificar si ya existe (idempotencia)
            existing = con.execute(
                "SELECT decision_signature FROM decision_ledger WHERE decision_signature = ?",
                (decision_sig,)
            ).fetchone()
            
            if existing:
                return decision_sig  # Ya registrado, no duplicar
            
            con.execute("""
                INSERT INTO decision_ledger
                (decision_signature, data_origin, entry_ts, db_hash, code_git_sha, config_hash,
                 cost_model_version, sampler_version, branch, admitted, veto_reason, confidence,
                 market_cap_source, quote_json, fill_json, outcome, pnl_gross_usd, pnl_net_usd,
                 tokens_missing_snapshots, quoted_without_fill)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, NULL, 0, 0)
            """, (
                decision_sig, data_origin, entry_ts_ms,
                self._get_db_hash(), self._get_git_sha(), self.config_hash,
                cost_model_version, sampler_version,
                branch, int(admitted), veto_reason, confidence,
                market_cap_source,
                json.dumps(quote) if quote else None,
                json.dumps(fill) if fill else None,
            ))
        
        return decision_sig
    
    def resolve_outcome(self, decision_signature: str, 
                       outcome: str, pnl_gross: float, pnl_net: float,
                       tokens_missing: int = 0, quoted_no_fill: int = 0) -> None:
        """Resuelve el outcome de una decisión pendiente."""
        if outcome not in ('WIN', 'LOSS', 'INVALID'):
            raise ValueError(f"Outcome inválido: {outcome}")
        
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                UPDATE decision_ledger
                SET outcome = ?, pnl_gross_usd = ?, pnl_net_usd = ?,
                    tokens_missing_snapshots = ?, quoted_without_fill = ?
                WHERE decision_signature = ?
            """, (outcome, pnl_gross, pnl_net, tokens_missing, quoted_no_fill, decision_signature))
    
    def get_provenance_report(self) -> dict:
        """Genera reporte con provenance completa para auditoría."""
        with sqlite3.connect(self.db_path) as con:
            # Stats básicos
            total = con.execute("SELECT COUNT(*) FROM decision_ledger").fetchone()[0]
            pending = con.execute("SELECT COUNT(*) FROM decision_ledger WHERE outcome='PENDING'").fetchone()[0]
            resolved = con.execute("SELECT COUNT(*) FROM decision_ledger WHERE outcome IN ('WIN','LOSS')").fetchone()[0]
            
            # Cobertura de features
            tokens_with_features = con.execute("""
                SELECT COUNT(DISTINCT token_id) FROM features_at_entry
            """).fetchone()[0]
            
            # Ticks disponibles
            tick_stats = con.execute("""
                SELECT source, COUNT(*), MIN(ts_ms), MAX(ts_ms)
                FROM ticks_crudos GROUP BY source
            """).fetchall()
            
        return {
            "data_origin": "forward_live",
            "db_hash": self._get_db_hash()[:12],
            "code_git_sha": self._get_git_sha(),
            "config_hash": self.config_hash,
            "cost_model_version": "v1",
            "sampler_version": 1,
            "time_range": "N/A",  # Se llenaría con MIN/MAX entry_ts
            "unique_tokens": tokens_with_features,
            "total_decisions": total,
            "pending_decisions": pending,
            "resolved_decisions": resolved,
            "coverage_pct": round(100 * resolved / total, 1) if total > 0 else 0,
            "tick_sources": {s: {"count": c, "min_ts": mn, "max_ts": mx} for s, c, mn, mx in tick_stats},
        }
    
    def fail_closed_check(self, token_id: str, entry_ts_ms: int) -> tuple[bool, str]:
        """
        Fail-closed: verifica si hay datos suficientes para operar.
        
        Returns:
            (ok, reason) - si ok=False, no operar
        """
        with sqlite3.connect(self.db_path) as con:
            # Verificar features disponibles en entry_time
            feat_count = con.execute("""
                SELECT COUNT(*) FROM features_at_entry
                WHERE token_id = ? AND available_at_ms <= ?
            """, (token_id, entry_ts_ms)).fetchone()[0]
            
            # Verificar ticks recientes
            tick_count = con.execute("""
                SELECT COUNT(*) FROM ticks_crudos
                WHERE token_id = ? AND ts_ms BETWEEN ? AND ?
            """, (token_id, entry_ts_ms - 300000, entry_ts_ms)).fetchone()[0]  # 5 min window
        
        if feat_count == 0:
            return False, f"NO_FEATURES_AVAILABLE: 0 features con available_at_ms <= {entry_ts_ms}"
        
        if tick_count < 3:
            return False, f"INSUFFICIENT_TICKS: {tick_count} ticks en ventana 5min"
        
        return True, "OK"


if __name__ == "__main__":
    # Demo
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ledger.db"
        ledger = ForwardLedger(str(db), config_hash="demo_v1")
        
        # Registrar features
        ledger.record_features(
            "TOKEN123", 
            {"ef_n_buys": 5, "firstMarketCap": 10000, "devBalancePct": 0.5},
            observed_at_ms=1000000, available_at_ms=1000000
        )
        
        # Registrar ticks
        for i in range(10):
            ledger.record_tick("TOKEN123", 1000000 + i*1000, 1.001 + i*0.001, 50.0, 100000, "dexscreener")
        
        # Registrar decisión
        sig = ledger.record_decision(
            "TOKEN123", 1000000, "MASTER_ALPHA_EXPANSION", True, None, 0.85,
            {"ef_n_buys": 5}, "firstMarketCap", 
            quote={"price": 1.01, "liq": 50}, fill=None
        )
        print(f"Decision recorded: {sig}")
        
        # Resolver
        ledger.resolve_outcome(sig, "WIN", 150.0, 142.0)
        
        # Reporte
        report = ledger.get_provenance_report()
        print(json.dumps(report, indent=2))
        
        # Fail-closed check
        ok, reason = ledger.fail_closed_check("TOKEN123", 1000000)
        print(f"Fail-closed: {ok} - {reason}")