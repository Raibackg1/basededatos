"""
Etiquetado de operaciones: todas las definiciones de 'win' solicitadas.

1. win_real      -> PnL real de la operación cerrado en positivo
2. win_tp_sl     -> toca +TP% antes que -SL% (ruta de precio real)
3. win_fut_{N}m  -> retorno >= umbral en los N minutos siguientes
4. win_grad      -> el token graduó (completó la bonding curve / migró a Raydium)

Además calcula MFE/MAE (excursión favorable/adversa máxima) y un grid
TP x SL para optimizar la política de salidas.
"""
import numpy as np
import pandas as pd

from .config import Cfg
from .costs import DEFAULT_COSTS, CostModel
from .loader import Data


def _slice_after(path, t_entry, window_s):
    """Devuelve (rel_ts, precios) desde t_entry hasta t_entry+window."""
    ts, px = path
    i = int(np.searchsorted(ts, t_entry, side="left"))
    j = int(np.searchsorted(ts, t_entry + window_s, side="right"))
    return ts[i:j], px[i:j]


def _price_at(path, t):
    ts, px = path
    i = int(np.searchsorted(ts, t, side="right")) - 1
    if i < 0:
        return np.nan
    return float(px[i])


def _tpsl_outcome(path, t_entry, p_entry, tp, sl, window_s):
    """
    Devuelve ('win'|'loss'|'timeout', t_exit, p_exit).
    tp/sl en fracción (0.5 = +50%).
    """
    ts, px = _slice_after(path, t_entry, window_s)
    if len(px) == 0:
        return "timeout", np.nan, np.nan
    rel = px / p_entry - 1.0
    hit_tp = rel >= tp
    hit_sl = rel <= -sl
    i_tp = int(np.argmax(hit_tp)) if hit_tp.any() else 10**9
    i_sl = int(np.argmax(hit_sl)) if hit_sl.any() else 10**9
    if i_tp == 10**9 and i_sl == 10**9:
        return "timeout", float(ts[-1]), float(px[-1])
    # C5 (conservador): si TP y SL se tocan en el MISMO tick, se asume SL.
    # Antes `i_tp <= i_sl` resolvía el empate a favor del TP (optimista).
    if i_tp < i_sl:
        return "win", float(ts[i_tp]), float(p_entry * (1 + tp))
    # C5: el SL es una orden a MERCADO. Si el tick saltó el nivel, el fill es
    # PEOR que el nivel; se usa el precio observado (no el nivel idealizado).
    return "loss", float(ts[i_sl]), float(min(p_entry * (1 - sl), float(px[i_sl])))


def _mfe_mae(path, t_entry, p_entry, window_s):
    ts, px = _slice_after(path, t_entry, window_s)
    if len(px) == 0:
        return np.nan, np.nan
    rel = px / p_entry - 1.0
    return float(np.nanmax(rel)), float(np.nanmin(rel))


def compute_trade_labels(d: Data, df: pd.DataFrame, cfg: Cfg,
                         costs: CostModel = DEFAULT_COSTS) -> pd.DataFrame:
    """
    Añade todas las etiquetas de win a un DataFrame que tenga
    token_id, entry_time y entry_price.

    `win_real_net` (Ola 2, informe de mejora): win real DESPUÉS de descontar
    costes ida+vuelta auditados (fees+slippage ~5.64%). Ninguna decisión se
    declara rentable con PnL bruto.
    """
    out = df.copy()
    n = len(out)
    win_tpsl = np.full(n, np.nan)
    win_real = np.full(n, np.nan)
    win_real_net = np.full(n, np.nan)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    hold_timeout = np.full(n, np.nan)
    fut = {h: np.full(n, np.nan) for h in cfg.fut_horizons_min}

    has_pnl = "pnl_sol" in out.columns
    has_pnl_pct = "pnl_pct" in out.columns
    has_px = {"entry_time", "entry_price"} <= set(out.columns)
    window_s = cfg.mfe_window_min * 60.0
    tp, sl = cfg.tp_pct / 100.0, cfg.sl_pct / 100.0

    tok_id = out["token_id"].astype(str).to_numpy() if "token_id" in out.columns else np.array([None] * n)
    e_time = out["entry_time"].to_numpy(float) if has_px else np.full(n, np.nan)
    e_price = out["entry_price"].to_numpy(float) if has_px else np.full(n, np.nan)

    for i in range(n):
        # 1) win real (+ variante NETA de costes ida+vuelta)
        gross_pct = np.nan
        if has_pnl and pd.notna(out["pnl_sol"].iloc[i]):
            win_real[i] = float(out["pnl_sol"].iloc[i] > 0)
            if "size_sol" in out.columns and pd.notna(out["size_sol"].iloc[i]) and out["size_sol"].iloc[i] > 0:
                gross_pct = float(out["pnl_sol"].iloc[i]) / float(out["size_sol"].iloc[i]) * 100.0
        elif has_pnl_pct and pd.notna(out["pnl_pct"].iloc[i]):
            win_real[i] = float(out["pnl_pct"].iloc[i] > 0)
            gross_pct = float(out["pnl_pct"].iloc[i])
        elif has_px and pd.notna(e_price[i]) and "exit_price" in out.columns and pd.notna(out["exit_price"].iloc[i]):
            win_real[i] = float(out["exit_price"].iloc[i] > e_price[i])
            gross_pct = (float(out["exit_price"].iloc[i]) / float(e_price[i]) - 1.0) * 100.0
        if gross_pct == gross_pct:  # not NaN
            win_real_net[i] = float(costs.net_pnl_pct(gross_pct) > 0.0)

        # 2/3) rutas de precio
        path = d.paths.get(tok_id[i]) if tok_id[i] is not None else None
        if path is None or not (e_time[i] == e_time[i]) or not (e_price[i] == e_price[i]) or e_price[i] <= 0:
            continue
        res, _, _ = _tpsl_outcome(path, e_time[i], e_price[i], tp, sl, window_s)
        win_tpsl[i] = {"win": 1.0, "loss": 0.0, "timeout": 0.0}[res]
        hold_timeout[i] = float(res == "timeout")
        m, a = _mfe_mae(path, e_time[i], e_price[i], window_s)
        mfe[i], mae[i] = m, a
        for h in cfg.fut_horizons_min:
            p_fut = _price_at(path, e_time[i] + h * 60.0)
            if p_fut == p_fut and e_price[i] > 0:
                ret = p_fut / e_price[i] - 1.0
                fut[h][i] = ret * 100.0

    out["win_real"] = win_real
    out["win_real_net"] = win_real_net
    out["win_tp_sl"] = win_tpsl
    out[f"win_tp{int(cfg.tp_pct)}_sl{int(cfg.sl_pct)}"] = win_tpsl
    out["mfe_pct"] = mfe * 100.0
    out["mae_pct"] = mae * 100.0
    out["tpsl_timeout"] = hold_timeout
    for h in cfg.fut_horizons_min:
        out[f"fut_ret_{h}m"] = fut[h]
        out[f"win_fut_{h}m"] = (fut[h] >= cfg.fut_threshold_pct).astype(float)
        out.loc[fut[h] != fut[h], f"win_fut_{h}m"] = np.nan

    # 4) graduación
    if "graduated" in out.columns:
        out["win_grad"] = pd.to_numeric(out["graduated"], errors="coerce")
    else:
        out["win_grad"] = np.nan
    return out


def build_virtual_trades(d: Data, cfg: Cfg) -> pd.DataFrame:
    """
    Si la base no contiene trades propios, simula una entrada de sniping
    estándar (a los cfg.virtual_entry_s segundos de la creación) para cada
    token, de modo que el detector pueda evaluar patrones de selección.
    """
    rows = []
    for tid, (ts, px) in d.paths.items():
        if len(ts) < 5:
            continue
        t0 = ts[0]
        t_entry = t0 + cfg.virtual_entry_s
        i = int(np.searchsorted(ts, t_entry, side="right")) - 1
        if i < 0 or i >= len(ts):
            continue
        rows.append({
            "token_id": tid,
            "entry_time": float(ts[i]),
            "entry_price": float(px[i]),
            "virtual": 1,
            "entry_delay_s": float(ts[i] - t0),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Grid TP x SL sobre rutas reales
# ---------------------------------------------------------------------------
def tpsl_grid(d: Data, df: pd.DataFrame, cfg: Cfg) -> dict:
    """
    Para cada combinación (TP, SL) del grid, calcula winrate y expectancy (en R)
    recorriendo la ruta de precio posterior a la entrada.
    Timeout: se cierra al final de la ventana y su PnL cuenta en R.
    """
    entries = []
    for r in df.itertuples():
        tid = str(getattr(r, "token_id", ""))
        path = d.paths.get(tid)
        if path is None:
            continue
        et, ep = getattr(r, "entry_time", np.nan), getattr(r, "entry_price", np.nan)
        if not (et == et) or not (ep == ep) or ep <= 0:
            continue
        ts, px = _slice_after(path, et, cfg.mfe_window_min * 60.0)
        if len(px) == 0:
            continue
        entries.append((px / ep - 1.0,))
    if not entries:
        return {}
    rels = [e[0] for e in entries]
    res = {"tps": cfg.tp_grid, "sls": cfg.sl_grid, "winrate": {}, "expectancy": {}, "n": len(rels)}
    for tp in cfg.tp_grid:
        for sl in cfg.sl_grid:
            tpf, slf = tp / 100.0, sl / 100.0
            wins = 0
            r_sum = 0.0
            valid = 0
            for rel in rels:
                hit_tp = rel >= tpf
                hit_sl = rel <= -slf
                i_tp = int(np.argmax(hit_tp)) if hit_tp.any() else 10**9
                i_sl = int(np.argmax(hit_sl)) if hit_sl.any() else 10**9
                valid += 1
                if i_tp == 10**9 and i_sl == 10**9:
                    r_sum += float(rel[-1]) / slf  # timeout: cerrar al final
                elif i_tp < i_sl:  # C5: empate -> SL (conservador)
                    wins += 1
                    r_sum += tp / sl
                else:
                    r_sum += -1.0
            res["winrate"][(tp, sl)] = wins / valid if valid else np.nan
            res["expectancy"][(tp, sl)] = r_sum / valid if valid else np.nan
    return res


def mfe_mae_summary(df: pd.DataFrame) -> dict:
    """Percentiles de MFE/MAE para dimensionar TP/SL razonables."""
    out = {}
    for col in ("mfe_pct", "mae_pct"):
        if col in df.columns and df[col].notna().any():
            s = df[col].dropna()
            out[col] = {q: float(np.percentile(s, q)) for q in (10, 25, 50, 75, 90)}
    return out
