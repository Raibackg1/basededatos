"""Registro persistente de reputación de creadores y cabales on-chain (BP-005, BP-008, BP-017).

Gestiona en SQLite listas dinámicas:
- WHITELIST: Creadores con historial probado de graduación y distribución orgánica (>= 2 éxitos, 0 rugs).
- GREYLIST: Creadores nuevos o sin muestra estadística suficiente (modo cautelar).
- DENYLIST: Creadores tóxicos seriales (>= 2 rugs o pérdida catastrófica provocada).
Incluye además el cálculo de la entropía de Shannon de la distribución de holders.
"""
from __future__ import annotations
import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


def holder_shannon_entropy(holder_balances: Iterable[float]) -> float:
    """
    Calcula la entropía de Shannon normalizada H_norm en el intervalo [0.0, 1.0].
    H = - sum(p_i * ln(p_i))
    H_norm = H / ln(K), donde K es el número de holders.
    
    Interpretación:
    - H_norm ~ 1.0: Distribución completamente equitativa y descentralizada.
    - H_norm < 0.35: Concentración extrema coludida (cabal oculta simulando múltiples wallets).
    """
    bals = [float(b) for b in (holder_balances or []) if float(b) > 0.0]
    k = len(bals)
    if k <= 1:
        return 0.0
    total = sum(bals)
    if total <= 0.0:
        return 0.0
    probs = [b / total for b in bals]
    h = -sum(p * math.log(p) for p in probs if p > 0.0)
    max_h = math.log(k)
    return round(float(h / max_h) if max_h > 0 else 0.0, 4)


@dataclass(frozen=True)
class CreatorReputation:
    wallet: str
    status: str  # WHITELIST, GREYLIST, DENYLIST
    n_launches: int
    n_rugs: int
    n_successes: int
    reputation_score: float
    last_pnl_pct: float
    is_blocked: bool
    reason: Optional[str] = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS creator_reputation (
    wallet TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    n_launches INTEGER NOT NULL DEFAULT 0,
    n_rugs INTEGER NOT NULL DEFAULT 0,
    n_successes INTEGER NOT NULL DEFAULT 0,
    reputation_score REAL NOT NULL DEFAULT 0.0,
    last_pnl_pct REAL NOT NULL DEFAULT 0.0,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_creator_rep_status ON creator_reputation(status);
"""


class CreatorRegistry:
    """Gestor persistente de listas y reputación de desarrolladores."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._con = sqlite3.connect(db_path)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def get_reputation(self, wallet: Optional[str]) -> CreatorReputation:
        """Consulta el estado de reputación de un creador en O(1)."""
        if not wallet or str(wallet).strip() == "":
            return CreatorReputation(
                wallet="unknown", status="GREYLIST", n_launches=0,
                n_rugs=0, n_successes=0, reputation_score=0.0,
                last_pnl_pct=0.0, is_blocked=False, reason="CREATOR_UNKNOWN"
            )
        w = str(wallet).strip()
        row = self._con.execute(
            "SELECT * FROM creator_reputation WHERE wallet = ?", (w,)
        ).fetchone()
        if not row:
            return CreatorReputation(
                wallet=w, status="GREYLIST", n_launches=0,
                n_rugs=0, n_successes=0, reputation_score=0.0,
                last_pnl_pct=0.0, is_blocked=False, reason="CREATOR_UNSEEN_GREYLIST"
            )
        status = str(row["status"])
        is_blocked = (status == "DENYLIST")
        reason = "VETO_CREATOR_DENYLISTED" if is_blocked else None
        return CreatorReputation(
            wallet=w,
            status=status,
            n_launches=int(row["n_launches"]),
            n_rugs=int(row["n_rugs"]),
            n_successes=int(row["n_successes"]),
            reputation_score=float(row["reputation_score"]),
            last_pnl_pct=float(row["last_pnl_pct"]),
            is_blocked=is_blocked,
            reason=reason,
        )

    def record_outcome(
        self,
        wallet: str,
        is_win: bool,
        pnl_pct: float,
        is_rug: bool = False,
        now_ts: Optional[float] = None
    ) -> CreatorReputation:
        """Registra el outcome de un token creado por esta wallet y recalibra su status."""
        if not wallet:
            return self.get_reputation(wallet)
        w = str(wallet).strip()
        ts = float(now_ts if now_ts is not None else time.time())
        current = self.get_reputation(w)

        n_launches = current.n_launches + 1
        n_rugs = current.n_rugs + (1 if is_rug or pnl_pct <= -50.0 else 0)
        n_successes = current.n_successes + (1 if is_win and pnl_pct >= 25.0 else 0)

        # Score continuo: penalización severa a rugs (-1.5x) frente a éxitos (+1.0x)
        score = (n_successes * 1.0 - n_rugs * 1.5) / max(1, n_launches)

        # Reglas de transición de estados:
        # BP-008: Creador Serial Tóxico (>= 2 rugs o score < -0.40) -> DENYLIST
        if n_rugs >= 2 or score <= -0.40 or (n_launches >= 3 and n_successes == 0):
            status = "DENYLIST"
        elif n_successes >= 2 and n_rugs == 0 and score >= 0.50:
            status = "WHITELIST"
        else:
            status = "GREYLIST"

        self._con.execute(
            """
            INSERT INTO creator_reputation (
                wallet, status, n_launches, n_rugs, n_successes,
                reputation_score, last_pnl_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                status = excluded.status,
                n_launches = excluded.n_launches,
                n_rugs = excluded.n_rugs,
                n_successes = excluded.n_successes,
                reputation_score = excluded.reputation_score,
                last_pnl_pct = excluded.last_pnl_pct,
                updated_at = excluded.updated_at
            """,
            (w, status, n_launches, n_rugs, n_successes, score, float(pnl_pct), ts)
        )
        self._con.commit()
        return self.get_reputation(w)
