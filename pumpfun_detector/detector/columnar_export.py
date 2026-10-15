"""Exportación P2 opcional; ausencia de DuckDB/Arrow no rompe SQLite."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict
import sqlite3
import pandas as pd


def export_sqlite_table(db_path: str, table: str, out_path: str, limit: int | None = None) -> Dict[str, Any]:
    if not table.replace("_", "").isalnum(): return {"ok": False, "reason": "invalid_table"}
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        q = f'SELECT * FROM "{table}"' + (f" LIMIT {int(limit)}" if limit else "")
        df = pd.read_sql_query(q, conn)
    finally: conn.close()
    path = Path(out_path); path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        try: df.to_parquet(path, index=False, engine="pyarrow")
        except (ImportError, ModuleNotFoundError) as exc: return {"ok": False, "reason": "pyarrow_unavailable", "error": str(exc)}
    elif suffix == ".csv": df.to_csv(path, index=False)
    elif suffix == ".duckdb":
        try:
            import duckdb
            duckdb.connect(str(path)).execute("CREATE OR REPLACE TABLE snapshot AS SELECT * FROM df").close()
        except (ImportError, ModuleNotFoundError) as exc: return {"ok": False, "reason": "duckdb_unavailable", "error": str(exc)}
    else: return {"ok": False, "reason": "unsupported_output", "suffix": suffix}
    return {"ok": True, "rows": len(df), "path": str(path), "format": suffix[1:]}
