"""
Ingeniería de características para sniping de memecoins en pump.fun.

Nivel token (selección): métricas de los primeros minutos de vida del token
(presión compradora, concentración, comportamiento del dev, socials, hora...).
Nivel operación (ejecución): retraso de entrada, tamaño, precio relativo...
"""
import re

import numpy as np
import pandas as pd

from .config import Cfg
from .loader import Data

KEYWORDS = {
    "ai": ["ai", "gpt", "bot", "agent", "intel"],
    "animal": ["dog", "cat", "pepe", "frog", "shib", "inu", "doge", "bird", "bear",
                "bull", "monkey", "ape", "wolf", "lion", "rat", "fish", "whale", "pengu"],
    "politic": ["trump", "biden", "maga", "elon", "musk", "president", "vote", "kama", "donald"],
    "money": ["gold", "cash", "money", "rich", "moon", "lambo", "bank", "coin", "dollar", "sol"],
    "meme_clasic": ["pepe", "wojak", "chad", "mem", "lol", "kek", "bonk", "biden", "sbf"],
}


def _has_social(v) -> int:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null", "0", "false", "-"):
        return 0
    return 1


def _str_stats(name: str, symbol: str) -> dict:
    name = "" if name is None or (isinstance(name, float) and np.isnan(name)) else str(name)
    symbol = "" if symbol is None or (isinstance(symbol, float) and np.isnan(symbol)) else str(symbol)
    nl, sl = name.lower(), symbol.lower()
    out = {
        "name_len": len(name),
        "symbol_len": len(symbol),
        "symbol_has_digits": int(bool(re.search(r"\d", symbol))),
        "name_all_caps": int(name.isupper() and len(name) > 2),
    }
    for cat, kws in KEYWORDS.items():
        out[f"kw_{cat}"] = int(any(k in nl or k in sl for k in kws))
    return out


def token_features(d: Data, cfg: Cfg) -> pd.DataFrame:
    """Construye el DataFrame de características a nivel de token."""
    rows = []
    paths = d.paths

    tok_meta = {}
    if d.tokens is not None and "token_id" in d.tokens.columns:
        for r in d.tokens.itertuples():
            tok_meta[str(r.token_id)] = r

    trades_by_token = None
    if d.trades is not None and len(d.trades):
        trades_by_token = {tid: g for tid, g in d.trades.groupby("token_id", sort=False)}

    tids = set(paths.keys()) | set(tok_meta.keys())
    for tid in tids:
        f = {"token_id": tid}
        meta = tok_meta.get(tid)

        # ---- metadatos ----
        created = np.nan
        if meta is not None:
            created = getattr(meta, "created_at", np.nan)
            f["has_twitter"] = _has_social(getattr(meta, "twitter", None))
            f["has_telegram"] = _has_social(getattr(meta, "telegram", None))
            f["has_website"] = _has_social(getattr(meta, "website", None))
            f["n_socials"] = f["has_twitter"] + f["has_telegram"] + f["has_website"]
            f.update(_str_stats(getattr(meta, "token_name", None),
                                getattr(meta, "token_symbol", None)))
            grad = getattr(meta, "graduated", np.nan)
            f["graduated"] = grad
            mc = getattr(meta, "market_cap", np.nan)
            f["market_cap"] = mc
            dev = getattr(meta, "dev_wallet", None)
            dev = str(dev) if dev is not None and str(dev) not in ("nan", "None", "") else None

            # Transferir métricas on-chain ricas (ej. custom.db de pumpscope)
            for col_name in [
                "firstMarketCap", "firstLiquidity", "devBalancePct", "migrationProgress",
                "realSolReserves", "topHolderPct", "holderCount", "bundlerHoldingPct",
                "securityScore", "organicScore", "netBuyers1h"
            ]:
                for prefix in ["raw_", ""]:
                    val = getattr(meta, f"{prefix}{col_name}", None)
                    if val is not None and isinstance(val, (int, float)) and np.isfinite(val):
                        f[col_name] = float(val)
                        break
        else:
            f.update({"has_twitter": np.nan, "has_telegram": np.nan, "has_website": np.nan,
                      "n_socials": np.nan, "graduated": np.nan})
            dev = None

        # ---- microestructura desde trades ----
        g = trades_by_token.get(tid) if trades_by_token else None
        ts0 = created if (created == created and created is not None) else np.nan  # not NaN
        if g is not None and len(g):
            ts = g["ts"].to_numpy(float)
            price = g["price"].to_numpy(float) if "price" in g else None
            side = g["side"].to_numpy() if "side" in g else None
            sol = g["sol_amount"].to_numpy(float) if "sol_amount" in g else None
            wallet = g["wallet"].astype(str).to_numpy() if "wallet" in g else None

            if not (ts0 == ts0):  # NaN -> usar primer trade
                ts0 = ts[0]
            rel = ts - ts0

            if price is not None:
                p_first = price[np.isfinite(price)][0] if np.isfinite(price).any() else np.nan
                f["p_first"] = p_first
                supply = getattr(meta, "supply", getattr(meta, "total_supply", 1_000_000_000.0)) if meta is not None else 1_000_000_000.0
                if supply is not None and isinstance(supply, (int, float)) and np.isfinite(supply) and supply > 0 and p_first == p_first:
                    f["firstMarketCap"] = float(p_first) * float(supply)
                mc_cur = f.get("market_cap", np.nan)
                if supply is not None and isinstance(supply, (int, float)) and np.isfinite(supply) \
                        and supply > 0 and p_first == p_first and not (isinstance(mc_cur, float) and np.isfinite(mc_cur)):
                    f["market_cap"] = float(p_first) * float(supply)
            buys = (side == "buy") if side is not None else np.ones(len(ts), bool)
            sells = (side == "sell") if side is not None else ~buys

            # primeros 60 segundos
            m60 = rel <= 60
            _fb = rel[buys & (rel >= 0)]
            f["first_trade_delay_s"] = float(_fb[0]) if len(_fb) else np.nan
            f["n_trades_60s"] = int(m60.sum())
            f["n_buys_60s"] = int((m60 & buys).sum())
            f["n_sells_60s"] = int((m60 & sells).sum())
            f["buy_sell_ratio_60s"] = f["n_buys_60s"] / max(f["n_sells_60s"], 1)
            if wallet is not None:
                ub60 = wallet[m60 & buys]
                f["unique_buyers_60s"] = len(np.unique(ub60))
                # concentración: share de SOL de compra del top-5 wallets en 60s
                if sol is not None and len(ub60):
                    per = pd.Series(sol[m60 & buys]).groupby(pd.Series(wallet[m60 & buys])).sum()
                    tot = per.sum()
                    f["top5_wallet_share_60s"] = float(per.nlargest(5).sum() / tot) if tot > 0 else np.nan
                else:
                    f["top5_wallet_share_60s"] = np.nan
            if sol is not None:
                f["buy_vol_sol_60s"] = float(np.nansum(sol[m60 & buys]))
                f["avg_buy_sol_60s"] = float(np.nanmean(sol[m60 & buys])) if (m60 & buys).any() else np.nan
                f["max_buy_sol_60s"] = float(np.nanmax(sol[m60 & buys])) if (m60 & buys).any() else np.nan
                m300 = rel <= 300
                bv, sv = np.nansum(sol[m300 & buys]), np.nansum(sol[m300 & sells])
                f["sell_pressure_5m"] = float(sv / (bv + sv)) if (bv + sv) > 0 else np.nan
                # progreso de la bonding curve (proxy: SOL netos acumulados)
                net5 = float(np.nansum(sol[m300 & buys]) - np.nansum(sol[m300 & sells]))
                f["curve_progress_5m"] = min(1.0, max(0.0, net5 / cfg.curve_sol))
                m30w = rel <= 1800
                net30 = float(np.nansum(sol[m30w & buys]) - np.nansum(sol[m30w & sells]))
                f["curve_progress_30m"] = min(1.0, max(0.0, net30 / cfg.curve_sol))

            # ---- comportamiento del dev ----
            if dev is not None and wallet is not None:
                dm = wallet == dev
                dev_buys = dm & buys
                dev_sells = dm & sells
                f["dev_bought"] = int(dev_buys.any())
                if sol is not None:
                    f["dev_buy_sol"] = float(np.nansum(sol[dev_buys]))
                    f["dev_sold_sol"] = float(np.nansum(sol[dev_sells]))
                    f["dev_sold_pct"] = float(f["dev_sold_sol"] / f["dev_buy_sol"]) if f["dev_buy_sol"] > 0 else np.nan
                f["dev_first_sell_s"] = float(rel[dev_sells][0]) if dev_sells.any() else np.nan
                f["dev_held_30m"] = int((not dev_sells.any()) or (rel[dev_sells][0] > 1800))
            else:
                f.update({"dev_bought": np.nan, "dev_buy_sol": np.nan,
                          "dev_sold_pct": np.nan, "dev_first_sell_s": np.nan})

            # ---- trayectoria de precio 30m ----
            if price is not None:
                m30 = (rel <= 1800) & np.isfinite(price)
                if m30.any():
                    p30 = price[m30]
                    f["max_mult_30m"] = float(np.nanmax(p30) / p_first) if p_first > 0 else np.nan
                    cummax = np.maximum.accumulate(np.nan_to_num(p30, nan=p_first))
                    f["max_dd_30m"] = float(np.nanmin(p30 / cummax - 1.0))
                    f["end_mult_30m"] = float(p30[-1] / p_first) if p_first > 0 else np.nan
                    up = np.where(p30 >= 2.0 * p_first)[0]
                    f["time_to_2x_s"] = float(rel[m30][up][0]) if len(up) else np.nan
                    up5 = np.where(p30 >= 5.0 * p_first)[0]
                    f["time_to_5x_s"] = float(rel[m30][up5][0]) if len(up5) else np.nan
                w = wallet if wallet is not None else None
                if w is not None:
                    f["unique_buyers_30m"] = len(np.unique(w[(rel <= 1800) & buys]))
                f["n_trades_30m"] = int((rel <= 1800).sum())

        # ---- hora de creación ----
        if ts0 == ts0:  # not NaN
            f["created_at"] = ts0
            hh = pd.Timestamp(ts0, unit="s", tz="UTC")
            f["hour_utc"] = hh.hour
            f["is_weekend"] = int(hh.dayofweek >= 5)
            f["is_night_utc"] = int(hh.hour < 6)
        rows.append(f)

    df = pd.DataFrame(rows)
    return df


def entry_features(d: Data, m: pd.DataFrame, cfg: Cfg) -> pd.DataFrame:
    """
    Características calculadas SOLO con información disponible hasta el momento
    de la entrada (sin fuga de datos futuros): microestructura acumulada desde
    la creación hasta entry_time, momentum previo, comportamiento del dev, etc.
    """
    arr = {}
    if d.trades is not None and len(d.trades):
        for tid, g in d.trades.groupby("token_id", sort=False):
            arr[tid] = {
                "ts": g["ts"].to_numpy(float),
                "side": g["side"].to_numpy() if "side" in g.columns else None,
                "sol": g["sol_amount"].to_numpy(float) if "sol_amount" in g.columns else None,
                "wallet": g["wallet"].astype(str).to_numpy() if "wallet" in g.columns else None,
                "price": g["price"].to_numpy(float) if "price" in g.columns else None,
            }
    dev_by_tok = {}
    if d.tokens is not None and {"token_id", "dev_wallet"} <= set(d.tokens.columns):
        dev_by_tok = dict(zip(d.tokens["token_id"].astype(str),
                              d.tokens["dev_wallet"].astype(str)))

    rows = []
    for r in m.itertuples():
        tid = str(getattr(r, "token_id", ""))
        et = getattr(r, "entry_time", np.nan)
        f = {}
        a = arr.get(tid)
        if a is not None and et == et:
            i = int(np.searchsorted(a["ts"], et, side="right"))
            ts = a["ts"][:i]
            side, sol, wallet, price = a["side"][:i] if a["side"] is not None else None, \
                a["sol"][:i] if a["sol"] is not None else None, \
                a["wallet"][:i] if a["wallet"] is not None else None, \
                a["price"][:i] if a["price"] is not None else None
            t0 = a["ts"][0]
            buys = (side == "buy") if side is not None else np.ones(len(ts), bool)
            f["ef_n_trades"] = len(ts)
            f["ef_n_buys"] = int(buys.sum())
            f["ef_n_sells"] = int((~buys).sum())
            f["ef_buy_sell_ratio"] = float(buys.sum() / max((~buys).sum(), 1))
            f["ef_secs_since_launch"] = float(et - t0)
            if sol is not None and len(sol):
                f["ef_buy_vol_sol"] = float(np.nansum(sol[buys]))
                f["ef_avg_buy_sol"] = float(np.nanmean(sol[buys])) if buys.any() else np.nan
                f["ef_max_buy_sol"] = float(np.nanmax(sol[buys])) if buys.any() else np.nan
                tot = float(np.nansum(sol))
                f["ef_sell_pressure"] = float(np.nansum(sol[~buys]) / tot) if tot > 0 else np.nan
                net = float(np.nansum(sol[buys]) - np.nansum(sol[~buys]))
                f["ef_curve_progress"] = min(1.0, max(0.0, net / cfg.curve_sol))
            if wallet is not None and len(wallet):
                ub = wallet[buys]
                f["ef_unique_buyers"] = len(np.unique(ub))
                if sol is not None and len(ub):
                    per = pd.Series(sol[buys]).groupby(pd.Series(ub)).sum()
                    tot2 = per.sum()
                    f["ef_top5_share"] = float(per.nlargest(5).sum() / tot2) if tot2 > 0 else np.nan
                dev = dev_by_tok.get(tid)
                if dev and dev not in ("nan", "None", ""):
                    dm = wallet == dev
                    f["ef_dev_bought"] = int(bool((dm & buys).any()))
                    f["ef_dev_already_sold"] = int(bool((dm & ~buys).any()))
                    if sol is not None:
                        f["ef_dev_buy_sol"] = float(np.nansum(sol[dm & buys]))
            if price is not None and len(price):
                valid = price[np.isfinite(price)]
                if len(valid) and valid[0] > 0:
                    f["ef_price_mult"] = float(valid[-1] / valid[0])
                j = int(np.searchsorted(ts, et - 15))
                if j < len(price) and price[j] > 0:
                    f["ef_momentum_15s"] = float(price[-1] / price[j] - 1.0)

            # ---- Microestructura avanzada: VPIN, Amihud, Kyle's Lambda, OFI ----
            if sol is not None and len(sol) and side is not None:
                bv = float(np.nansum(sol[buys]))
                sv = float(np.nansum(sol[~buys]))
                f["ef_order_flow_imbalance"] = float((bv - sv) / (bv + sv)) if (bv + sv) > 0 else 0.0
                
                # VPIN en reloj de volumen exacto
                bucket_size = 0.5
                curr_vb, curr_vs = 0.0, 0.0
                imbalances = []
                for s_amt, is_b in zip(sol, buys):
                    rem = float(s_amt)
                    while rem > 0:
                        cap = bucket_size - (curr_vb + curr_vs)
                        fill = min(rem, cap)
                        if is_b:
                            curr_vb += fill
                        else:
                            curr_vs += fill
                        rem -= fill
                        if (curr_vb + curr_vs) >= (bucket_size - 1e-9):
                            imbalances.append(abs(curr_vb - curr_vs))
                            if len(imbalances) > 15:
                                imbalances.pop(0)
                            curr_vb, curr_vs = 0.0, 0.0
                if len(imbalances):
                    f["ef_vpin_30s"] = float(np.clip(sum(imbalances) / (len(imbalances) * bucket_size), 0.0, 1.0))
                else:
                    f["ef_vpin_30s"] = float(abs(bv - sv) / (bv + sv)) if (bv + sv) > 0 else 0.0

                # Amihud Illiquidity Ratio
                if price is not None and len(price) >= 2:
                    val_idx = np.where(np.isfinite(price) & (price > 0) & np.isfinite(sol) & (sol > 0.001))[0]
                    if len(val_idx) >= 2:
                        p_sub = price[val_idx]
                        s_sub = sol[val_idx]
                        log_rets = np.abs(np.diff(np.log(p_sub)))
                        illiq = log_rets / s_sub[1:]
                        f["ef_amihud_illiq"] = float(np.nanmedian(illiq)) if len(illiq) else 0.0
                    else:
                        f["ef_amihud_illiq"] = 0.0
                else:
                    f["ef_amihud_illiq"] = 0.0

                # Kyle's Lambda analítico en curva CPMM
                net_s = bv - sv
                f["ef_cpmm_kyle_lambda"] = float(2.0 * (30.0 + max(0.0, net_s)) / 3.219e10)
        rows.append(f)
    ef = pd.DataFrame(rows)
    return pd.concat([m.reset_index(drop=True), ef], axis=1)


# Columnas de token_features que son estáticas o conocidas en el momento de la
# entrada (el resto son métricas post-entrada y se excluyen del minado).
ENTRY_SAFE = [
    "token_id", "has_twitter", "has_telegram", "has_website", "n_socials",
    "name_len", "symbol_len", "symbol_has_digits", "name_all_caps",
    "kw_ai", "kw_animal", "kw_politic", "kw_money", "kw_meme_clasic",
    "hour_utc", "is_weekend", "is_night_utc", "created_at", "p_first",
    "first_trade_delay_s",
]

POST_ENTRY_FEATURES = frozenset({
    "graduated", "market_cap", "marketCap", "max_mult_30m", "max_dd_30m", "end_mult_30m",
    "time_to_2x_s", "time_to_5x_s", "unique_buyers_30m", "n_trades_30m",
    "sell_pressure_5m", "curve_progress_5m", "curve_progress_30m",
    "dev_sold_pct", "dev_first_sell_s", "dev_held_30m",
})

FORBIDDEN_ENTRY_PREFIXES = (
    "win_", "pnl_", "exit_", "fut_ret", "mfe_", "mae_", "tpsl_", "hold_"
)

ENTRY_SAFE_FEATURES = frozenset({
    "token_id", "created_at", "entry_time", "hour_utc", "is_weekend", "is_night_utc",
    "has_twitter", "has_telegram", "has_website", "n_socials", "name_len", "symbol_len",
    "symbol_has_digits", "name_all_caps", "kw_ai", "kw_animal", "kw_politic", "kw_money", "kw_meme_clasic",
    "first_trade_delay_s", "p_first", "firstMarketCap", "firstLiquidity", "first_market_cap", "first_liquidity",
    "mc_at_entry_est", "market_cap_at_entry", "marketCapAtEntry", "n_trades_60s", "n_buys_60s", "n_sells_60s",
    "buy_sell_ratio_60s", "unique_buyers_60s", "top5_wallet_share_60s", "buy_vol_sol_60s", "avg_buy_sol_60s",
    "max_buy_sol_60s", "ef_n_trades", "ef_n_buys", "ef_n_sells", "ef_buy_sell_ratio", "ef_secs_since_launch",
    "ef_buy_vol_sol", "ef_avg_buy_sol", "ef_max_buy_sol", "ef_sell_pressure", "ef_curve_progress",
    "ef_unique_buyers", "ef_top5_share", "ef_dev_bought", "ef_dev_buy_sol", "ef_dev_already_sold",
    "ef_price_mult", "ef_momentum_15s", "ef_vpin_30s", "ef_amihud_illiq", "ef_cpmm_kyle_lambda",
    "ef_order_flow_imbalance", "cabal_share", "is_block0_trap", "entry_delay_s", "entry_delay_bucket",
    "entry_mult_vs_first", "size_sol", "size_bucket", "realSolReserves", "sol_reserves", "devBalancePct",
    "dev_balance_pct", "topHolderPct", "top_holder_pct", "holderCount", "unique_buyers"
})


class FeatureAvailabilityGuard:
    """Guardián estricto de disponibilidad temporal de características (P0 anti-fuga)."""

    @classmethod
    def validate(cls, df_or_cols, stage: str = "training") -> None:
        """Verifica que no existan columnas futuras ni etiquetas en el conjunto de variables."""
        if hasattr(df_or_cols, "columns"):
            cols = list(df_or_cols.columns)
        elif isinstance(df_or_cols, (list, tuple, set, frozenset)):
            cols = list(df_or_cols)
        elif isinstance(df_or_cols, dict):
            cols = list(df_or_cols.keys())
        else:
            cols = [str(df_or_cols)]

        leaked = set()
        for c in cols:
            sc = str(c)
            if sc in POST_ENTRY_FEATURES:
                leaked.add(sc)
            elif sc.startswith(FORBIDDEN_ENTRY_PREFIXES):
                leaked.add(sc)
            elif sc in ("status", "virtual", "fees", "exit_reason", "exit_price"):
                leaked.add(sc)

        if leaked:
            raise ValueError(
                f"Fuga temporal detectada en etapa '{stage}': features posteriores a la entrada: "
                + ", ".join(sorted(leaked))
            )

    @classmethod
    def filter_entry_safe(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Filtra el DataFrame reteniendo únicamente columnas formalmente seguras a la entrada."""
        safe_cols = [
            c for c in df.columns
            if str(c) not in POST_ENTRY_FEATURES
            and not str(c).startswith(FORBIDDEN_ENTRY_PREFIXES)
            and str(c) not in ("status", "virtual", "fees", "exit_reason", "exit_price")
        ]
        return df[safe_cols].copy()

    @classmethod
    def audit_incremental_consistency(cls, records: list, time_key: str = "ts") -> tuple[bool, list]:
        """
        Validador de lookahead (inspirado en Freqtrade lookahead-analysis):
        Verifica que el cálculo acumulado en el tiempo no varíe al incluir datos futuros.
        """
        diffs = []
        sorted_records = sorted(records, key=lambda r: r.get(time_key, 0))
        for i in range(1, len(sorted_records) + 1):
            subset = sorted_records[:i]
            if i > 1 and subset[-1].get(time_key, 0) < subset[-2].get(time_key, 0):
                diffs.append(f"Inversión temporal en índice {i}")
        return len(diffs) == 0, diffs


def assert_no_post_entry_features(columns) -> None:
    """Falla explícitamente si una feature futura llega a entrenamiento."""
    FeatureAvailabilityGuard.validate(columns, stage="assert_no_post_entry_features")


def trade_features(d: Data, tokfeat: pd.DataFrame, cfg: Cfg,
                   base_df: pd.DataFrame = None) -> pd.DataFrame:
    """Une características conocidas-al-entrar con las de ejecución de cada operación."""
    src = base_df if base_df is not None else d.my
    if src is None or not len(src):
        return pd.DataFrame()
    m = src.copy()
    m = entry_features(d, m, cfg)
    if "token_id" in m.columns and tokfeat is not None and len(tokfeat):
        leaks = POST_ENTRY_FEATURES
        safe = [c for c in tokfeat.columns if c not in leaks]
        m = m.merge(tokfeat[safe], on="token_id", how="left")

    # retraso de entrada respecto a la creación
    if {"entry_time", "created_at"} <= set(m.columns):
        m["entry_delay_s"] = m["entry_time"] - m["created_at"]
        m["entry_delay_bucket"] = pd.cut(
            m["entry_delay_s"],
            bins=[-1, 5, 15, 30, 60, 180, 600, np.inf],
            labels=["<5s", "5-15s", "15-30s", "30-60s", "1-3m", "3-10m", ">10m"],
        ).astype(str)
    # precio de entrada relativo al primer precio
    if {"entry_price", "p_first"} <= set(m.columns):
        m["entry_mult_vs_first"] = m["entry_price"] / m["p_first"]
    # Market cap estimado a la entrada: usar únicamente el market cap inicial.
    # El market_cap actual/final queda prohibido porque puede contener futuro.
    if {"entry_price", "firstMarketCap", "p_first"} <= set(m.columns):
        with np.errstate(divide="ignore", invalid="ignore"):
            m["mc_at_entry_est"] = m["firstMarketCap"] * (m["entry_price"] / m["p_first"])
    # duración de la posición
    if {"entry_time", "exit_time"} <= set(m.columns):
        m["hold_s"] = m["exit_time"] - m["entry_time"]
    if "size_sol" in m.columns:
        m["size_bucket"] = pd.cut(
            m["size_sol"], bins=[-np.inf, 0.25, 0.5, 1.0, 2.0, np.inf],
            labels=["<0.25", "0.25-0.5", "0.5-1", "1-2", ">2 SOL"],
        ).astype(str)
    return m


# Nombres legibles en español para el reporte
FEATURE_DESC = {
    "entry_delay_s": "Retraso de mi entrada (s desde creación)",
    "entry_mult_vs_first": "Precio de entrada / primer precio",
    "size_sol": "Tamaño de posición (SOL)",
    "hold_s": "Duración de la posición (s)",
    "first_trade_delay_s": "Retraso del primer trade (s)",
    "n_trades_60s": "Nº trades en 60s",
    "n_buys_60s": "Nº compras en 60s",
    "n_sells_60s": "Nº ventas en 60s",
    "buy_sell_ratio_60s": "Ratio compra/venta en 60s",
    "unique_buyers_60s": "Compradores únicos en 60s",
    "unique_buyers_30m": "Compradores únicos en 30m",
    "top5_wallet_share_60s": "Concentración top-5 wallets (60s)",
    "buy_vol_sol_60s": "Volumen de compra en 60s (SOL)",
    "avg_buy_sol_60s": "Ticket medio de compra en 60s (SOL)",
    "max_buy_sol_60s": "Compra máxima en 60s (SOL)",
    "sell_pressure_5m": "Presión vendedora 5m (share de ventas)",
    "curve_progress_5m": "Progreso bonding curve en 5m",
    "curve_progress_30m": "Progreso bonding curve en 30m",
    "dev_bought": "El dev compró su propio token",
    "dev_buy_sol": "SOL comprado por el dev",
    "dev_sold_pct": "% del stack del dev vendido",
    "dev_first_sell_s": "Segundos hasta la 1ª venta del dev",
    "dev_held_30m": "El dev mantuvo +30m",
    "max_mult_30m": "Máximo múltiplo de precio en 30m",
    "max_dd_30m": "Máximo drawdown en 30m",
    "end_mult_30m": "Múltiplo final a 30m",
    "time_to_2x_s": "Segundos hasta 2x",
    "time_to_5x_s": "Segundos hasta 5x",
    "n_trades_30m": "Nº trades en 30m",
    "has_twitter": "Tiene Twitter/X",
    "has_telegram": "Tiene Telegram",
    "has_website": "Tiene website",
    "n_socials": "Nº de redes sociales",
    "name_len": "Longitud del nombre",
    "symbol_len": "Longitud del símbolo",
    "symbol_has_digits": "El símbolo tiene dígitos",
    "name_all_caps": "Nombre en mayúsculas",
    "kw_ai": "Keyword 'AI'",
    "kw_animal": "Keyword animal (dog/cat/pepe...)",
    "kw_politic": "Keyword política (trump/musk...)",
    "kw_money": "Keyword dinero (moon/gold...)",
    "kw_meme_clasic": "Keyword meme clásico",
    "hour_utc": "Hora de creación (UTC)",
    "is_weekend": "Creado en fin de semana",
    "is_night_utc": "Creado de madrugada (UTC)",
    "market_cap": "Market cap registrado",
    "graduated": "Graduó a Raydium",
    "mc_at_entry_est": "Market cap estimado a mi entrada",
    # --- features conocidas al momento de entrar (ef_) ---
    "ef_n_trades": "Trades acumulados hasta mi entrada",
    "ef_n_buys": "Compras acumuladas hasta mi entrada",
    "ef_n_sells": "Ventas acumuladas hasta mi entrada",
    "ef_buy_sell_ratio": "Ratio compra/venta hasta mi entrada",
    "ef_secs_since_launch": "Segundos desde el lanzamiento (a mi entrada)",
    "ef_buy_vol_sol": "Volumen de compra acumulado (SOL) hasta mi entrada",
    "ef_avg_buy_sol": "Ticket medio de compra hasta mi entrada (SOL)",
    "ef_max_buy_sol": "Compra máxima vista hasta mi entrada (SOL)",
    "ef_sell_pressure": "Presión vendedora hasta mi entrada",
    "ef_curve_progress": "Progreso de bonding curve a mi entrada",
    "ef_unique_buyers": "Compradores únicos hasta mi entrada",
    "ef_top5_share": "Concentración top-5 wallets hasta mi entrada",
    "ef_dev_bought": "El dev ya había comprado (a mi entrada)",
    "ef_dev_buy_sol": "SOL comprado por el dev (hasta mi entrada)",
    "ef_dev_already_sold": "El dev ya había vendido algo (a mi entrada)",
    "ef_price_mult": "Múltiplo de precio desde el 1er trade hasta mi entrada",
    "ef_momentum_15s": "Momentum de precio en los 15s previos a mi entrada",
    "first_trade_delay_s": "Retraso del primer trade (s)",
    "entry_delay_s": "Retraso de mi entrada (s desde creación)",
    "entry_delay_bucket": "Bucket de retraso de entrada",
    "size_bucket": "Bucket de tamaño de posición",
    "entry_mult_vs_first": "Mi precio de entrada / primer precio",
}


def desc(feature: str) -> str:
    return FEATURE_DESC.get(feature, feature)
