"""Guardas de disponibilidad temporal y procedencia de datos.

Los prefijos de features prohibidas son canónicos en ``detector.features``
(``FORBIDDEN_ENTRY_PREFIXES``). Los sets exactos difieren a propósito:
- ``features.POST_ENTRY_FEATURES``: fuga en contexto ML (market_cap post-trade).
- ``data_integrity.EXACT_POST_ENTRY``: fuga en contexto live (market_cap a la
  entrada ES válida — la usa MasterSnipeSignal — pero "win", "mfe" sueltos no).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any, Dict, Optional

import pandas as pd

from .features import FORBIDDEN_ENTRY_PREFIXES


# Alias exactos del dominio live. "market_cap" NO está aquí a propósito:
# a la entrada es válida (MasterSnipeSignal la usa como feature).
EXACT_POST_ENTRY = {
    "graduated", "status", "hold_s", "win", "win_real", "win_real_net",
    "win_tp_sl", "tpsl_timeout", "exit_price", "exit_reason", "pnl_sol",
    "pnl_pct", "fees", "mfe", "mae",
}
# Prefijos compartidos con features.py (fuente canónica) + extensiones live.
PREFIXES = FORBIDDEN_ENTRY_PREFIXES + ("future_", "fut_ret_")


class FeatureAvailabilityError(ValueError):
    """Feature ausente, post-entrada o con timestamp incompatible."""


class FeatureAvailabilityGuard:
    """Valida que un vector de features exista antes del instante de entrada."""

    @classmethod
    def _is_forbidden(cls, name: str) -> bool:
        low = str(name).lower()
        return low in EXACT_POST_ENTRY or any(low.startswith(p) for p in PREFIXES)

    @classmethod
    def validate(cls, features: Dict[str, Any], entry_time: Optional[float] = None) -> Dict[str, Any]:
        if not isinstance(features, dict):
            raise FeatureAvailabilityError("features debe ser un diccionario")
        forbidden = sorted(k for k in features if cls._is_forbidden(k))
        if forbidden:
            raise FeatureAvailabilityError(f"features post-entrada prohibidas: {', '.join(forbidden)}")
        available = features.get("available_at_ms", features.get("feature_available_at_ms"))
        if available is not None and entry_time is not None:
            entry_ms = float(entry_time) * 1000.0 if float(entry_time) < 10_000_000_000 else float(entry_time)
            if float(available) > entry_ms:
                raise FeatureAvailabilityError("available_at_ms posterior a entry_time")
        return dict(features)

    @classmethod
    def filter_frame(cls, df: pd.DataFrame, entry_time_col: str = "entry_time") -> pd.DataFrame:
        out = df.copy()
        bad_cols = [c for c in out.columns if cls._is_forbidden(c)]
        if bad_cols:
            out = out.drop(columns=bad_cols)
        if "available_at_ms" in out.columns and entry_time_col in out.columns:
            entry = pd.to_numeric(out[entry_time_col], errors="coerce")
            entry_ms = entry.where(entry >= 10_000_000_000, entry * 1000.0)
            out = out[(pd.to_numeric(out["available_at_ms"], errors="coerce") <= entry_ms) | out["available_at_ms"].isna()]
        return out


def build_provenance(db_path: Optional[str], data_origin: str, config: Any = None) -> Dict[str, Any]:
    """Construye metadata reproducible; no inventa hashes cuando no hay archivo."""
    db_hash = None
    size = None
    if db_path and os.path.isfile(db_path):
        h = hashlib.sha256()
        with open(db_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        db_hash, size = h.hexdigest(), os.path.getsize(db_path)
    try:
        code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=os.path.dirname(db_path) if db_path else None, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        code_sha = None
    cfg_payload = config if isinstance(config, dict) else getattr(config, "__dict__", config)
    cfg_hash = hashlib.sha256(json.dumps(cfg_payload or {}, sort_keys=True, default=str).encode()).hexdigest()
    return {"data_origin": str(data_origin), "db_hash": db_hash, "db_size_bytes": size,
            "code_git_sha": code_sha, "config_hash": cfg_hash, "schema_version": 1}
