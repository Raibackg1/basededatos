"""
Minería de patrones para mejorar el winrate:

- Univariado: winrate por bin de cada característica + lift + significancia.
- Reglas de árbol de decisión: IF-THEN legibles ordenadas por lift.
- Modelo ML: AUC cross-validated + importancia de características.
- Interacciones: pares de características con efecto combinado extremo.
"""
import numpy as np
import pandas as pd
from scipy.stats import binomtest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.tree import DecisionTreeClassifier, _tree

from .advanced_engine import SubgroupMiner, benjamini_hochberg, compute_q_values
from .config import Cfg
from .features import FORBIDDEN_ENTRY_PREFIXES, POST_ENTRY_FEATURES, desc


def _binom_p(wins, n, base):
    if n == 0 or not (0 < base < 1):
        return np.nan
    return float(binomtest(int(wins), int(n), base).pvalue)


def numeric_feature_cols(df: pd.DataFrame, exclude) -> list:
    exclude = set(exclude).union(POST_ENTRY_FEATURES)
    cols = []
    for c in df.columns:
        sc = str(c)
        if sc in exclude or sc.startswith(FORBIDDEN_ENTRY_PREFIXES) or sc.startswith("raw_") or sc in ("status", "virtual", "fees"):
            continue
        if df[c].dtype.kind in "ifb" and df[c].notna().sum() >= 30:
            cols.append(c)
    return cols


def categorical_feature_cols(df: pd.DataFrame, exclude, max_levels: int = 10) -> list:
    exclude = set(exclude).union(POST_ENTRY_FEATURES)
    out = []
    for c in df.columns:
        sc = str(c)
        if sc in exclude or sc.startswith(FORBIDDEN_ENTRY_PREFIXES) or sc.startswith("raw_") or sc in ("status", "virtual", "fees"):
            continue
        if df[c].dtype == object or str(df[c].dtype) == "category":
            if 2 <= df[c].nunique(dropna=True) <= max_levels and df[c].notna().sum() >= 30:
                out.append(c)
    return out


# ---------------------------------------------------------------------------
# Análisis univariado
# ---------------------------------------------------------------------------
def univariate(df: pd.DataFrame, label: str, cfg: Cfg) -> pd.DataFrame:
    base = df[label].mean()
    rows = []
    for col in numeric_feature_cols(df, cfg.exclude_features):
        mask = df[col].notna() & df[label].notna()
        s = df.loc[mask, col]
        y = df.loc[mask, label]
        nu = s.nunique()
        if nu < 2:
            continue
        edges = None
        if nu <= 6:
            gser = s.map(lambda v: f"{v:g}")
        else:
            try:
                q = pd.qcut(s.rank(method="first"), cfg.n_bins, labels=False, duplicates="drop")
            except ValueError:
                continue
            gser = q.map(lambda i: f"Q{int(i)+1}")
            edges = s.groupby(q).agg(["min", "max"])
        for g in gser.unique():
            sel = (gser == g)
            n = int(sel.sum())
            if n < cfg.min_bin_n:
                continue
            w = float(y[sel].sum())
            wr = w / n
            if edges is not None:
                qi = int(str(g)[1:]) - 1
                bin_label = f"{g} [{edges['min'].iloc[qi]:.3g} — {edges['max'].iloc[qi]:.3g}]"
            else:
                bin_label = str(g)
            rows.append({
                "feature": col, "desc": desc(col), "bin": bin_label,
                "n": n, "wins": int(w), "winrate": wr, "baseline": base,
                "lift": wr / base if base > 0 else np.nan,
                "p_value": _binom_p(w, n, base),
            })
    for col in categorical_feature_cols(df, cfg.exclude_features):
        sub = pd.DataFrame({"g": df[col].astype(str), "y": df[label]}).dropna(subset=["y"])
        for g, gg in sub.groupby("g"):
            n = len(gg)
            if n < cfg.min_bin_n or g in ("nan", "None"):
                continue
            w = gg["y"].sum()
            wr = w / n
            rows.append({
                "feature": col, "desc": desc(col), "bin": g, "n": n, "wins": int(w),
                "winrate": wr, "baseline": base,
                "lift": wr / base if base > 0 else np.nan,
                "p_value": _binom_p(w, n, base),
            })
    uni = pd.DataFrame(rows)
    if len(uni):
        uni["abs_dev"] = (uni["lift"] - 1).abs()
        p_vals = uni["p_value"].to_numpy(float)
        uni["q_value"] = compute_q_values(p_vals)
        uni["fdr_sig"] = benjamini_hochberg(p_vals, alpha=cfg.pvalue_threshold)
        uni = uni.sort_values(["p_value", "abs_dev"], ascending=[True, False])
    return uni


def feature_spread(uni: pd.DataFrame) -> pd.DataFrame:
    """Ranking de características por rango de winrate entre bins (con señal)."""
    if not len(uni):
        return pd.DataFrame()
    g = uni.groupby(["feature", "desc"]).agg(
        wr_min=("winrate", "min"), wr_max=("winrate", "max"),
        n_total=("n", "sum"), p_min=("p_value", "min"),
    ).reset_index()
    g["spread"] = g["wr_max"] - g["wr_min"]
    g = g[g["p_min"] < 0.2]
    return g.sort_values("spread", ascending=False)


# ---------------------------------------------------------------------------
# Reglas de árbol de decisión
# ---------------------------------------------------------------------------
def _fmt_cond(feature, op, thr, df):
    d = desc(feature)
    if feature in df.columns and set(np.unique(df[feature].dropna())) <= {0.0, 1.0}:
        if op == ">":
            return f"{d} = SÍ" if thr < 0.5 else f"{d} = NO"
        return f"{d} = NO" if thr < 0.5 else f"{d} = SÍ"
    return f"{d} {op} {thr:.4g}"


def tree_rules(df: pd.DataFrame, label: str, cfg: Cfg) -> pd.DataFrame:
    feats = numeric_feature_cols(df, cfg.exclude_features)
    if not feats:
        return pd.DataFrame()
    sub = df[feats + [label]].dropna(subset=[label])
    if sub[label].nunique() < 2 or len(sub) < 2 * cfg.min_rule_support:
        return pd.DataFrame()
    X = sub[feats].astype(float)
    X = X.fillna(X.median())
    y = sub[label].astype(int)
    base = y.mean()
    clf = DecisionTreeClassifier(
        max_depth=cfg.max_tree_depth,
        min_samples_leaf=max(cfg.min_rule_support, int(0.02 * len(X))),
        random_state=cfg.random_state,
    )
    clf.fit(X, y)
    t = clf.tree_
    rules = []

    def recurse(node, conds):
        if t.feature[node] == _tree.TREE_UNDEFINED:
            n = int(t.n_node_samples[node])
            vals = t.value[node][0]
            tot = float(vals.sum())
            # sklearn puede dar recuentos o proporciones normalizadas:
            w = float(vals[1]) / tot * n if tot > 0 else 0.0
            if n >= cfg.min_rule_support and conds:
                wr = w / n
                lift = wr / base if base > 0 else np.nan
                if lift >= cfg.lift_min or lift <= 1 / cfg.lift_min:
                    rules.append({
                        "conds": list(conds), "n": n, "wins": int(w), "winrate": wr,
                        "lift": lift, "p_value": _binom_p(w, n, base),
                    })
            return
        f = feats[t.feature[node]]
        thr = float(t.threshold[node])
        recurse(t.children_left[node], conds + [(f, "<=", thr)])
        recurse(t.children_right[node], conds + [(f, ">", thr)])

    recurse(0, [])
    out = pd.DataFrame(rules)
    if len(out):
        out["rule"] = out["conds"].map(
            lambda cs: "  Y  ".join(_fmt_cond(f, op, thr, df) for f, op, thr in cs)
        )
        out["label"] = label
        out["baseline"] = base
        p_vals = out["p_value"].to_numpy(float)
        out["q_value"] = compute_q_values(p_vals)
        out["fdr_sig"] = benjamini_hochberg(p_vals, alpha=cfg.pvalue_threshold)
        out = out.sort_values("lift", key=np.abs, ascending=False)
    return out


# ---------------------------------------------------------------------------
# Minería de Subgrupos (Subgroup Discovery WRAcc)
# ---------------------------------------------------------------------------
def subgroup_rules(df: pd.DataFrame, label: str, cfg: Cfg) -> pd.DataFrame:
    """
    Descubre reglas combinadas no voraces optimizando Weighted Relative Accuracy (WRAcc)
    con control estricto de False Discovery Rate (FDR).
    """
    feats = numeric_feature_cols(df, cfg.exclude_features)
    if not feats or label not in df.columns:
        return pd.DataFrame()
    sub = df[feats + [label]].dropna(subset=[label])
    if sub[label].nunique() < 2 or len(sub) < 2 * cfg.min_rule_support:
        return pd.DataFrame()

    miner = SubgroupMiner(
        min_support=cfg.min_rule_support,
        max_depth=2,
        beam_width=20,
        alpha_fdr=cfg.pvalue_threshold,
        jaccard_sim_max=0.65,
    )
    mined = miner.mine(sub, target_col=label, feature_cols=feats)
    if not mined:
        return pd.DataFrame()

    out = pd.DataFrame(mined)
    out["label"] = label
    # Formatear regla con descripciones legibles
    def _readable_rule(r_str):
        parts = r_str.split(" Y ")
        readable_parts = []
        for p in parts:
            for f in feats:
                if p.startswith(f):
                    p = p.replace(f, desc(f), 1)
                    break
            readable_parts.append(p)
        return "  Y  ".join(readable_parts)

    out["rule"] = out["rule"].map(_readable_rule)
    return out.sort_values("lift", ascending=False)


# ---------------------------------------------------------------------------
# Modelo ML
# ---------------------------------------------------------------------------
def model_eval(df: pd.DataFrame, label: str, cfg: Cfg) -> dict:
    feats = numeric_feature_cols(df, cfg.exclude_features)
    if not feats:
        return {"label": label, "ok": False, "reason": "sin features numéricas"}
    sub = df[feats + [label]].dropna(subset=[label])
    y = sub[label].astype(int)
    if y.nunique() < 2 or len(y) < 5 * cfg.cv_folds * 2:
        return {"label": label, "ok": False, "reason": "muestra insuficiente o sin varianza"}
    X = sub[feats].astype(float).fillna(sub[feats].astype(float).median())
    model = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=250,
        min_samples_leaf=max(10, int(0.02 * len(X))),
        random_state=cfg.random_state,
    )
    skf = StratifiedKFold(n_splits=cfg.cv_folds, shuffle=True, random_state=cfg.random_state)
    try:
        proba = cross_val_predict(model, X, y, cv=skf, method="predict_proba")[:, 1]
        auc = roc_auc_score(y, proba)
    except Exception as e:  # noqa: BLE001
        return {"label": label, "ok": False, "reason": str(e)}
    model.fit(X, y)
    try:
        pi = permutation_importance(
            model, X, y, n_repeats=5, random_state=cfg.random_state, scoring="roc_auc", n_jobs=1
        )
        imp = pd.Series(pi.importances_mean, index=feats).sort_values(ascending=False)
    except Exception:  # noqa: BLE001
        imp = pd.Series(dtype=float)
    return {
        "label": label, "ok": True, "auc": float(auc), "n": int(len(y)),
        "baseline_wr": float(y.mean()), "importance": imp.head(15),
        "proba": proba, "y": y.values, "feats": feats,
    }


# ---------------------------------------------------------------------------
# Interacciones de pares
# ---------------------------------------------------------------------------
def interactions(df: pd.DataFrame, label: str, imp_feats: list, cfg: Cfg) -> pd.DataFrame:
    base = df[label].mean()
    rows = []
    feats = [f for f in imp_feats[:10] if f in df.columns]
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            a, b = feats[i], feats[j]
            sub = df[[a, b, label]].dropna()
            if len(sub) < 4 * cfg.min_bin_n:
                continue
            try:
                qa = pd.qcut(sub[a].rank(method="first"), 3, labels=["bajo", "medio", "alto"], duplicates="drop")
                qb = pd.qcut(sub[b].rank(method="first"), 3, labels=["bajo", "medio", "alto"], duplicates="drop")
            except ValueError:
                continue
            gg = sub.groupby([qa, qb], observed=True)[label].agg(["mean", "sum", "count"])
            for idx, r in gg.iterrows():
                if r["count"] < cfg.min_bin_n:
                    continue
                lift = r["mean"] / base if base > 0 else np.nan
                if lift >= cfg.lift_min * 1.1 or lift <= 1 / (cfg.lift_min * 1.1):
                    rows.append({
                        "combo": f"{desc(a)}={idx[0]} + {desc(b)}={idx[1]}",
                        "n": int(r["count"]), "winrate": float(r["mean"]),
                        "lift": float(lift),
                        "p_value": _binom_p(r["sum"], r["count"], base),
                    })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("lift", key=np.abs, ascending=False).head(15)
    return out


# ---------------------------------------------------------------------------
# Ola 3: Minería de Confluencias Multivariante (A ∧ B ∧ C)
# ---------------------------------------------------------------------------
def confluence_rules(df: pd.DataFrame, label: str, imp_feats: list, cfg: Cfg,
                     max_depth: int = 3, top_features: int = 8) -> pd.DataFrame:
    """
    Busca CONFLUENCIAS: conjunciones de 2..max_depth condiciones (tercil alto
    de A ∧ tercil medio de B ∧ ...) cuyo winrate combinado supera el lift
    mínimo con control FDR (Benjamini-Hochberg) y significancia binomial.

    Inspirado en el hallazgo cruzado entre proyectos: la confluencia
    A∧B∧C (top10HoldersPct ∧ velocity ∧ buySellRatio) alcanza AUC 0.828 y
    lift 4.9-9.5× frente a ~1.1-1.3× del univariante Q1-Q5.

    Se evalúa sobre features numéricas conocidas al entrar (exclude_features).
    Devuelve DataFrame con columnas: confluencia, n, wins, winrate, baseline,
    lift, p_value, q_value, fdr_sig, depth.
    """
    from itertools import combinations

    from .advanced_engine import compute_q_values

    base = df[label].mean()
    feats = [f for f in imp_feats if f in df.columns][:top_features]
    if len(feats) < 2 or not (0.0 < base < 1.0):
        return pd.DataFrame()

    # Tercilización de cada feature (bajo/medio/alto) una sola vez.
    bins = {}
    for f in feats:
        s = df[f]
        if s.nunique() < 3 or s.notna().sum() < 3 * cfg.min_bin_n:
            continue
        try:
            q = pd.qcut(s.rank(method="first"), 3, labels=False, duplicates="drop")
        except ValueError:
            continue
        bins[f] = q  # Series alineada con el índice de df
    if len(bins) < 2:
        return pd.DataFrame()

    rows = []
    feat_names = list(bins.keys())
    y = df[label]
    for depth in range(2, max_depth + 1):
        for combo in combinations(feat_names, depth):
            qb = [bins[f] for f in combo]
            mask_all = y.notna()
            for q in qb:
                mask_all &= q.notna()
            if int(mask_all.sum()) < cfg.min_bin_n * (2 ** depth):
                continue
            grp_df = pd.DataFrame(
                {f"q{k}": qb[k] for k in range(depth)}, index=df.index
            ).loc[mask_all].copy()
            grp_df["_y"] = y.loc[mask_all].astype(float)
            grouped = grp_df.groupby(
                [f"q{k}" for k in range(depth)], observed=True
            )["_y"].agg(["sum", "count"])
            for idx, r in grouped.iterrows():
                n = int(r["count"])
                if n < cfg.min_bin_n:
                    continue
                wr = float(r["sum"]) / n
                lift = wr / base
                if not (lift >= cfg.lift_min or lift <= 1.0 / cfg.lift_min):
                    continue
                levels = idx if isinstance(idx, tuple) else (idx,)
                bounds = []  # umbrales numéricos por condición (para eval OOS)
                for f, lv in zip(combo, levels):
                    g = bins[f]
                    sel = (g == lv)
                    s_f = df.loc[sel, f]
                    bounds.append((f, float(s_f.min()), float(s_f.max())))
                conds = " ∧ ".join(
                    f"{desc(f)}=tercil{int(lv)}" for f, lv in zip(combo, levels)
                )
                rows.append({
                    "confluencia": conds,
                    "depth": depth,
                    "n": n, "wins": int(r["sum"]),
                    "winrate": wr, "baseline": base, "lift": lift,
                    "p_value": _binom_p(r["sum"], n, base),
                    "bounds": bounds,
                })

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    # Control FDR global sobre todas las conjunciones evaluadas
    qv = compute_q_values(out["p_value"].to_numpy(float))
    out["q_value"] = qv
    out["fdr_sig"] = qv <= 0.05
    return out.sort_values("lift", ascending=False).head(25)


def evaluate_confluence_oos(conf_df: pd.DataFrame, test_df: pd.DataFrame,
                            label: str, min_oos_n: int = 5) -> pd.DataFrame:
    """
    Re-aplica las confluencias (umbrales `bounds` de terciles fijados en Train)
    sobre el bloque OOS sellado y reporta n/winrate/lift/pasado.
    """
    if conf_df is None or len(conf_df) == 0 or test_df is None or len(test_df) == 0 \
            or label not in test_df.columns:
        return conf_df
    out = conf_df.copy()
    base_oos = float(test_df[label].mean())
    ns, wrs, lifts, passes = [], [], [], []
    for r in out.itertuples():
        mask = pd.Series(True, index=test_df.index)
        ok = True
        for f, lo, hi in (getattr(r, "bounds", None) or []):
            if f not in test_df.columns:
                ok = False
                break
            mask &= test_df[f].between(lo, hi)
        n_o = int(mask.sum()) if ok else 0
        if ok and n_o >= min_oos_n and base_oos > 0:
            wr_o = float(test_df.loc[mask, label].mean())
            lift_o = wr_o / base_oos
            ns.append(n_o)
            wrs.append(wr_o)
            lifts.append(lift_o)
            passes.append(bool(lift_o >= 1.05))
        else:
            ns.append(n_o if ok else np.nan)
            wrs.append(np.nan)
            lifts.append(np.nan)
            passes.append(False)
    out["oos_n"] = ns
    out["oos_winrate"] = wrs
    out["oos_lift"] = lifts
    out["oos_pass"] = passes
    return out
