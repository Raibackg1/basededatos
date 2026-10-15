"""Carga y normalización de datos desde SQLite según el mapeo semántico."""
import sqlite3

import numpy as np
import pandas as pd

from .inspector import SchemaMap


def _read_table(db_path: str, table: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql(f"SELECT * FROM '{table}'", con)
    finally:
        con.close()
    return df


def to_epoch_s(s: pd.Series) -> pd.Series:
    """Convierte una columna de tiempo (ISO string, epoch s o epoch ms) a epoch segundos."""
    if s.dtype.kind in "if":
        v = s.astype("float64")
        # >1e12 => milisegundos; >1e10 => segundos (epoch s actual ~1.7e9, con margen)
        out = np.where(v > 1e12, v / 1000.0, v)
        out = np.where(v > 1e10, v / 1000.0, out)  # doble check para 1e10..1e12
        return pd.Series(out, index=s.index)
    dt = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
    if dt.notna().mean() < 0.5:
        # quizá sea epoch numérico guardado como texto
        v = pd.to_numeric(s, errors="coerce")
        if v.notna().mean() > 0.5:
            return to_epoch_s(v)
    return (dt - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()


def norm_side(s: pd.Series) -> pd.Series:
    x = s.astype(str).str.lower().str.strip()
    out = pd.Series(index=s.index, dtype=object)
    out[x.isin(["buy", "b", "purchase", "in", "long", "bid"])] = "buy"
    out[x.isin(["sell", "s", "out", "short", "ask", "sale"])] = "sell"
    out[out.isna() & x.isin(["1", "true"])] = "buy"   # columnas is_buy booleanas
    out[out.isna() & x.isin(["0", "false"])] = "sell"
    return out


class Data:
    """Contenedor de datos normalizados."""

    def __init__(self):
        self.tokens = None      # DataFrame con columnas canónicas
        self.trades = None      # token_id, wallet, side, sol_amount, token_amount, price, ts
        self.my = None          # token_id, entry_time, entry_price, exit_time, exit_price, size_sol, pnl_sol, pnl_pct, status
        self.ohlcv = None
        self.paths = {}         # token_id -> (np.array ts, np.array price)
        self.warnings = []


def _canon(df: pd.DataFrame, colmap: dict) -> pd.DataFrame:
    """Renombra columnas según el mapeo role->col y devuelve solo roles presentes."""
    inv = {v: k for k, v in colmap.items()}
    keep = [c for c in df.columns if c in inv]
    out = df[keep].rename(columns=inv)
    # conservar también columnas sin mapear por si aportan señal (prefijo raw_)
    extra = [c for c in df.columns if c not in inv]
    for c in extra:
        out[f"raw_{c}"] = df[c]
    return out


def load_data(db_path: str, sm: SchemaMap) -> Data:
    d = Data()

    # ---- tokens ----
    if sm.table_of("tokens"):
        raw = _read_table(db_path, sm.table_of("tokens"))
        t = _canon(raw, sm.mapping["tokens"]["columns"])
        if "token_id" in t.columns:
            t["token_id"] = t["token_id"].astype(str)
        if "created_at" in t.columns:
            t["created_at"] = to_epoch_s(t["created_at"])
        if "graduated" in t.columns:
            g = t["graduated"]
            if g.dtype.kind in "if":
                t["graduated"] = (g != 0).astype(int)
            else:
                gs = g.astype(str).str.lower()
                t["graduated"] = gs.isin(["1", "true", "yes", "y", "completed", "complete",
                                           "migrated", "graduated", "t"]).astype(int)
                t.loc[g.isna(), "graduated"] = np.nan
        d.tokens = t

    # ---- trades ----
    if sm.table_of("trades"):
        raw = _read_table(db_path, sm.table_of("trades"))
        tr = _canon(raw, sm.mapping["trades"]["columns"])
        if "ts" in tr.columns:
            tr["ts"] = to_epoch_s(tr["ts"])
        if "side" in tr.columns:
            tr["side"] = norm_side(tr["side"])
        for c in ("sol_amount", "token_amount", "price"):
            if c in tr.columns:
                tr[c] = pd.to_numeric(tr[c], errors="coerce")
        # derivar precio si falta
        if "price" not in tr.columns or tr["price"].isna().all():
            if {"sol_amount", "token_amount"} <= set(tr.columns):
                tr["price"] = tr["sol_amount"] / tr["token_amount"].replace(0, np.nan)
                d.warnings.append("Precio derivado como sol_amount/token_amount.")
        # derivar sol_amount si falta pero hay precio y tokens
        if "sol_amount" not in tr.columns or tr["sol_amount"].isna().all():
            if {"price", "token_amount"} <= set(tr.columns):
                tr["sol_amount"] = tr["price"] * tr["token_amount"]
        tr = tr[tr["ts"].notna()]
        if "token_id" in tr.columns:
            tr = tr[tr["token_id"].notna()]
            tr["token_id"] = tr["token_id"].astype(str)
        d.trades = tr.sort_values("ts", kind="mergesort").reset_index(drop=True)

    # ---- ohlcv ----
    if sm.table_of("ohlcv"):
        raw = _read_table(db_path, sm.table_of("ohlcv"))
        o = _canon(raw, sm.mapping["ohlcv"]["columns"])
        if "ts" in o.columns:
            o["ts"] = to_epoch_s(o["ts"])
        d.ohlcv = o

    # ---- my_trades ----
    if sm.table_of("my_trades"):
        raw = _read_table(db_path, sm.table_of("my_trades"))
        m = _canon(raw, sm.mapping["my_trades"]["columns"])
        for c in ("entry_time", "exit_time"):
            if c in m.columns:
                m[c] = to_epoch_s(m[c])
        for c in ("entry_price", "exit_price", "size_sol", "pnl_sol", "pnl_pct", "fees"):
            if c in m.columns:
                m[c] = pd.to_numeric(m[c], errors="coerce")
        if "token_id" in m.columns:
            m["token_id"] = m["token_id"].astype(str)
        # derivar pnl_pct si falta
        if "pnl_pct" not in m.columns or m["pnl_pct"].isna().all():
            if {"entry_price", "exit_price"} <= set(m.columns):
                m["pnl_pct"] = (m["exit_price"] / m["entry_price"] - 1.0) * 100.0
        if "pnl_sol" not in m.columns or m["pnl_sol"].isna().all():
            if {"pnl_pct", "size_sol"} <= set(m.columns):
                m["pnl_sol"] = m["size_sol"] * m["pnl_pct"] / 100.0
        # filtrar solo cerradas si hay status
        if "status" in m.columns:
            st = m["status"].astype(str).str.lower()
            closed = st.isin(["closed", "cerrada", "cerrado", "done", "completed", "0", "false"])
            open_ = st.isin(["open", "abierta", "abierto", "running", "1", "true"])
            if open_.any():
                d.warnings.append(
                    f"{int(open_.sum())} posiciones abiertas ignoradas en el análisis de PnL real."
                )
            m = m[closed | (~open_)]
        d.my = m.reset_index(drop=True)

    # ---- Reconciliación cruzada de identificadores (ej: cuid vs mint) ----
    if d.tokens is not None and "token_id" in d.tokens.columns:
        id_col = next((c for c in ["raw_id", "id"] if c in d.tokens.columns), None)
        if id_col:
            id_to_mint = dict(zip(d.tokens[id_col].astype(str), d.tokens["token_id"].astype(str)))
            # Si my_trades usa el id interno, mapear a token_id canónico (mint)
            if d.my is not None and "token_id" in d.my.columns:
                mapped_my = d.my["token_id"].astype(str).map(id_to_mint)
                if mapped_my.notna().sum() > 0.5 * len(d.my):
                    d.my["token_id"] = mapped_my.fillna(d.my["token_id"])
            # Si ohlcv usa el id interno, mapear a token_id canónico (mint)
            if d.ohlcv is not None and "token_id" in d.ohlcv.columns:
                mapped_o = d.ohlcv["token_id"].astype(str).map(id_to_mint)
                if mapped_o.notna().sum() > 0.5 * len(d.ohlcv):
                    d.ohlcv["token_id"] = mapped_o.fillna(d.ohlcv["token_id"])
            # Si trades usa el id interno, mapear a token_id canónico (mint)
            if d.trades is not None and "token_id" in d.trades.columns:
                mapped_tr = d.trades["token_id"].astype(str).map(id_to_mint)
                if mapped_tr.notna().sum() > 0.5 * len(d.trades):
                    d.trades["token_id"] = mapped_tr.fillna(d.trades["token_id"])

    # ---- rutas de precio por token ----
    d.paths = build_paths(d)
    if d.tokens is not None and d.trades is not None and "created_at" in d.tokens.columns \
            and "token_id" in d.tokens.columns:
        # si falta created_at para algún token, usar el timestamp de su primer trade
        first = d.trades.groupby("token_id")["ts"].min()
        mask = d.tokens["created_at"].isna()
        if mask.any():
            d.tokens.loc[mask, "created_at"] = d.tokens.loc[mask, "token_id"].map(first)
    return d


def build_paths(d: Data) -> dict:
    """token_id -> (ts array, price array) ordenado, a partir de trades u OHLCV/Snapshot."""
    paths = {}
    if d.trades is not None and "price" in d.trades.columns and len(d.trades):
        tr = d.trades[["token_id", "ts", "price"]].dropna()
        for tid, g in tr.groupby("token_id", sort=False):
            g = g.sort_values("ts", kind="mergesort")
            ts = g["ts"].to_numpy(float)
            px = g["price"].to_numpy(float)
            ok = px > 0
            if ok.any():
                paths[tid] = (ts[ok], px[ok])

    # Si ohlcv/snapshot tiene mayor cobertura de tokens, enriquecer o complementar
    if d.ohlcv is not None and len(d.ohlcv):
        o = d.ohlcv.dropna(subset=["ts", "close"])
        for tid, g in o.groupby("token_id", sort=False):
            if tid in paths and len(paths[tid][0]) >= len(g):
                continue
            g = g.sort_values("ts", kind="mergesort")
            ts_l, px_l = [], []
            has_hl = {"high", "low"} <= set(g.columns) and "open" in g.columns
            for row in g.itertuples():
                if has_hl:
                    if row.close >= row.open:
                        seq = [(row.high, row.ts), (row.low, row.ts + 0.5)]
                    else:
                        seq = [(row.low, row.ts), (row.high, row.ts + 0.5)]
                    for p, t in seq:
                        ts_l.append(t); px_l.append(float(p))
                else:
                    ts_l.append(row.ts); px_l.append(float(row.close))
            t_arr, p_arr = np.array(ts_l, dtype=float), np.array(px_l, dtype=float)
            ok = p_arr > 0
            if ok.any():
                paths[tid] = (t_arr[ok], p_arr[ok])
    return paths
