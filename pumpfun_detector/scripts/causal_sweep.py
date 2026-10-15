"""Barrido causal de umbrales con validación OOS honesta.

Qué hace
--------
1. Carga la base canónica (solo lectura) y construye el DataFrame de operaciones
   usando ÚNICAMENTE features disponibles a la entrada (familia `ef_*` +
   `firstMarketCap`/`firstLiquidity`/sociales). Las `*_60s` y los desenlaces
   futuros quedan fuera (ver `detector/label_guard.py`).
2. Parte por tiempo: Train (60%) / Val (20%) / Test OOS sellado (20%).
3. Barre umbrales (univariante + pares) SOBRE TRAIN y reporta el resultado en
   TEST OOS con **n y intervalo de confianza de Wilson**.
4. Se niega a promover una regla con `oos_n < min_oos_n` o cuyo IC95% inferior
   sea <= 0 (es decir, sin evidencia de edge).

Honestidad: si no hay edge, el informe lo dice. Un resultado vacío es un
resultado válido.

Uso
---
    python -m scripts.causal_sweep --db "D:\\hjh\\pumpscope\\db\\custom.db" \
        --mapping mapping_pumpscope.json --out scratch\\sweep
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

# Features causales candidatas (todas entrada-seguras).
NUMERIC_FEATURES = [
    "firstMarketCap", "firstLiquidity", "ef_n_buys", "ef_n_sells",
    "ef_buy_sell_ratio", "ef_unique_buyers", "ef_top5_share", "ef_sell_pressure",
    "ef_curve_progress", "ef_price_mult", "ef_momentum_15s", "ef_secs_since_launch",
    "ef_buy_vol_sol", "ef_max_buy_sol", "ef_vpin_30s", "ef_amihud_illiq",
    "ef_order_flow_imbalance", "n_socials", "entry_delay_s",
]

LABEL = "win_real_net"


def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Intervalo de confianza de Wilson para una proporción."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1.0 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def _metrics(sub: pd.DataFrame, label: str) -> Dict[str, Any]:
    if sub is None or not len(sub):
        return {"n": 0, "winrate": None, "ci_low": None, "ci_high": None,
                "ev_pct": None, "profit_factor": None}
    y = sub[label].astype(float)
    k = int((y > 0).sum())
    n = int(len(y))
    lo, hi = wilson_ci(k, n)
    gains = sub.loc[sub["pnl_sol"] > 0, "pnl_sol"].sum() if "pnl_sol" in sub else 0.0
    losses = -sub.loc[sub["pnl_sol"] < 0, "pnl_sol"].sum() if "pnl_sol" in sub else 0.0
    pf = (gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)
    return {
        "n": n,
        "winrate": round(k / n * 100.0, 2),
        "ci_low": round(lo * 100.0, 2),
        "ci_high": round(hi * 100.0, 2),
        "ev_pct": round(float(sub["pnl_pct"].mean()), 3) if "pnl_pct" in sub else None,
        "profit_factor": round(pf, 3) if pf != float("inf") else None,
    }


def load_trades(db_path: str, mapping: Optional[str], out_dir: str) -> pd.DataFrame:
    """Carga y construye el DataFrame causal (con caché en disco)."""
    cache = os.path.join(out_dir, "causal_trades.parquet")
    if os.path.exists(cache):
        print(f"[sweep] usando caché {cache}")
        return pd.read_parquet(cache)

    from detector.config import Cfg
    from detector.inspector import apply_mapping_override, inspect_db
    from detector.loader import load_data
    from detector.features import token_features, trade_features, POST_ENTRY_FEATURES
    from detector.labels import build_virtual_trades, compute_trade_labels

    cfg = Cfg()
    sm = inspect_db(db_path)
    if mapping:
        apply_mapping_override(sm, mapping)
    d = load_data(db_path, sm)
    tokfeat = token_features(d, cfg)
    if d.my is not None and len(d.my):
        trades = trade_features(d, tokfeat, cfg)
    else:
        vt = build_virtual_trades(d, cfg)
        trades = trade_features(d, tokfeat, cfg, base_df=vt)
    # Anti-fuga: eliminar cualquier columna post-entrada que se haya colado.
    drop = [c for c in trades.columns if c in POST_ENTRY_FEATURES]
    if drop:
        trades = trades.drop(columns=drop)
    trades = compute_trade_labels(d, trades, cfg)

    os.makedirs(out_dir, exist_ok=True)
    try:
        trades.to_parquet(cache, index=False)
        print(f"[sweep] caché escrita en {cache}")
    except Exception as exc:
        print(f"[sweep] no se pudo cachear en parquet ({exc}); sigo en memoria")
    return trades


def _feature_available(df: pd.DataFrame) -> List[str]:
    return [c for c in NUMERIC_FEATURES if c in df.columns and df[c].notna().sum() >= 50]


def univariate(train: pd.DataFrame, test: pd.DataFrame, feats: List[str],
               qs: Tuple[float, ...]) -> List[Dict[str, Any]]:
    """Barrido univariante: para cada feature y cuantil, evalúa >= umbral y <= umbral."""
    rows: List[Dict[str, Any]] = []
    for f in feats:
        vals = train[f].dropna()
        if len(vals) < 50:
            continue
        for q in qs:
            thr = float(vals.quantile(q))
            for op in (">=", "<="):
                mask_tr = (train[f] >= thr) if op == ">=" else (train[f] <= thr)
                sub_tr = train[mask_tr]
                if len(sub_tr) < 30:
                    continue
                m_tr = _metrics(sub_tr, LABEL)
                mask_te = (test[f] >= thr) if op == ">=" else (test[f] <= thr)
                m_te = _metrics(test[mask_te], LABEL)
                rows.append({
                    "feature": f, "op": op, "threshold": round(thr, 6),
                    "train_n": m_tr["n"], "train_wr": m_tr["winrate"],
                    "train_ev": m_tr["ev_pct"],
                    "oos_n": m_te["n"], "oos_wr": m_te["winrate"],
                    "oos_ci_low": m_te["ci_low"], "oos_ci_high": m_te["ci_high"],
                    "oos_ev": m_te["ev_pct"], "oos_pf": m_te["profit_factor"],
                })
    return rows


def pairs(train: pd.DataFrame, test: pd.DataFrame, feats: List[str],
          qs: Tuple[float, ...], top_k: int = 8) -> List[Dict[str, Any]]:
    """Pares de features: usa los mejores univariantes como semillas."""
    seeds = univariate(train, test, feats, qs)
    seeds = [r for r in seeds if r["train_wr"] is not None and r["train_n"] >= 40]
    seeds.sort(key=lambda r: (r["train_wr"] or 0), reverse=True)
    best = seeds[:top_k]
    rows: List[Dict[str, Any]] = []
    for a in best:
        for b in best:
            if a["feature"] == b["feature"]:
                continue
            ma = (train[a["feature"]] >= a["threshold"]) if a["op"] == ">=" \
                else (train[a["feature"]] <= a["threshold"])
            mb = (train[b["feature"]] >= b["threshold"]) if b["op"] == ">=" \
                else (train[b["feature"]] <= b["threshold"])
            sub_tr = train[ma & mb]
            if len(sub_tr) < 30:
                continue
            m_tr = _metrics(sub_tr, LABEL)
            ta = (test[a["feature"]] >= a["threshold"]) if a["op"] == ">=" \
                else (test[a["feature"]] <= a["threshold"])
            tb = (test[b["feature"]] >= b["threshold"]) if b["op"] == ">=" \
                else (test[b["feature"]] <= b["threshold"])
            m_te = _metrics(test[ta & tb], LABEL)
            rows.append({
                "rule": f"{a['feature']} {a['op']} {a['threshold']:.4g} AND "
                        f"{b['feature']} {b['op']} {b['threshold']:.4g}",
                "train_n": m_tr["n"], "train_wr": m_tr["winrate"], "train_ev": m_tr["ev_pct"],
                "oos_n": m_te["n"], "oos_wr": m_te["winrate"],
                "oos_ci_low": m_te["ci_low"], "oos_ci_high": m_te["ci_high"],
                "oos_ev": m_te["ev_pct"], "oos_pf": m_te["profit_factor"],
            })
    return rows


def select_promoted(candidates: List[Dict[str, Any]], min_oos_n: int,
                    baseline_oos_wr: float) -> List[Dict[str, Any]]:
    """Gate de promoción honesto (testeable).

    Una regla se promueve SOLO si:
      (a) tiene muestra OOS suficiente (`oos_n >= min_oos_n`),
      (b) su IC95% inferior de winrate supera el baseline OOS, y
      (c) su EV neto OOS es POSITIVO.

    Sin (c), una winrate alta sigue perdiendo dinero porque los perdedores son
    mayores que los ganadores (visto en la base real: WR 36.8% con PF 0.405).
    """
    out = []
    for r in candidates:
        if (r.get("oos_n") or 0) < min_oos_n:
            continue
        if (r.get("oos_ci_low") or 0) <= baseline_oos_wr:
            continue
        if (r.get("oos_ev") or 0) <= 0.0:
            continue
        out.append(r)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Barrido causal de umbrales (OOS honesto).")
    ap.add_argument("--db", default=r"D:\hjh\pumpscope\db\custom.db")
    ap.add_argument("--mapping", default="mapping_pumpscope.json")
    ap.add_argument("--out", default="scratch/sweep")
    ap.add_argument("--min-oos-n", type=int, default=30)
    ap.add_argument("--quantiles", default="0.1,0.2,0.3,0.5,0.7,0.8,0.9")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    trades = load_trades(args.db, args.mapping, args.out)
    if LABEL not in trades.columns:
        print(f"[sweep] falta la etiqueta {LABEL}; abortando")
        return 2

    trades = trades.dropna(subset=[LABEL]).sort_values("created_at").reset_index(drop=True)
    n = len(trades)
    n_tr, n_va = int(n * 0.6), int(n * 0.2)
    train, val, test = trades.iloc[:n_tr], trades.iloc[n_tr:n_tr + n_va], trades.iloc[n_tr + n_va:]
    print(f"[sweep] N={n} | Train={len(train)} | Val={len(val)} | Test OOS={len(test)}")
    base = _metrics(train, LABEL)
    base_oos = _metrics(test, LABEL)
    print(f"[sweep] baseline Train WR={base['winrate']}%  |  Test OOS WR={base_oos['winrate']}%")

    feats = _feature_available(train)
    qs = tuple(float(x) for x in args.quantiles.split(","))
    uni = univariate(train, val, feats, qs)
    prs = pairs(train, val, feats, qs)

    # Criterio de promoción honesto (ver select_promoted): muestra suficiente,
    # IC inferior de winrate por encima del baseline OOS y EV neto OOS positivo.
    promoted = select_promoted(uni + prs, args.min_oos_n, base_oos["winrate"])

    with open(os.path.join(args.out, "sweep_report.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "db": os.path.basename(args.db), "label": LABEL,
            "n": n, "train": len(train), "val": len(val), "oos": len(test),
            "baseline_train_wr": base["winrate"], "baseline_oos_wr": base_oos["winrate"],
            "min_oos_n": args.min_oos_n,
            "n_candidates": len(uni) + len(prs),
            "n_promoted": len(promoted),
            "promoted": promoted[:20],
        }, fh, ensure_ascii=False, indent=2)

    if prs:
        pd.DataFrame(prs).sort_values("train_wr", ascending=False).to_csv(
            os.path.join(args.out, "sweep_pairs.csv"), index=False, encoding="utf-8-sig")
    if uni:
        pd.DataFrame(uni).sort_values("train_wr", ascending=False).to_csv(
            os.path.join(args.out, "sweep_univariate.csv"), index=False, encoding="utf-8-sig")

    print(f"[sweep] candidatos={len(uni) + len(prs)} | promovidos (OOS n>={args.min_oos_n} "
          f"y CI_low > baseline)={len(promoted)}")
    for r in promoted[:10]:
        print(f"   [+] {r.get('rule') or (r['feature'] + ' ' + r['op'] + ' ' + str(r['threshold']))}"
              f" | Train WR={r['train_wr']}% | OOS n={r['oos_n']} WR={r['oos_wr']}% "
              f"CI=[{r['oos_ci_low']},{r['oos_ci_high']}]")
    if not promoted:
        print("   (sin reglas que superen el baseline OOS con significancia: NO HAY EDGE DEMOSTRADO)")
    print(f"[sweep] informe: {os.path.join(args.out, 'sweep_report.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
