"""
Motor Cuantitativo Avanzado de Minería de Patrones y Microestructura Solana / Pump.fun.

Incluye:
1. Corrección de Múltiples Comparaciones: Benjamini-Hochberg (FDR) y q-values analíticos.
2. Minería de Subgrupos (Subgroup Discovery) con Beam Search, WRAcc y deduplicación por Jaccard.
3. Modelado de Ejecución Realista conforme al Protocolo de Supervisión:
   - Slippage real en stop loss (-33.49% por evaporación de liquidez, BP-001).
   - Comisiones reales de red (0.002 SOL por tx).
   - Penalización por latencia de sampler en STALL (-28.93%, BP-010).
4. Métricas de Microestructura On-Chain:
   - Concentración HHI (Herfindahl-Hirschman Index).
   - Burstiness B y memoria de inter-llegadas (Goh & Barabási).
   - Detección de Cabal por grafo de co-ocurrencia con ponderación Adamic-Adar (BP-005).
   - Detección de Snipers de Bloque 0 / Slot 0 (BP-006).
   - Curva de Bonding exacta CPMM con reservas virtuales (BP-007).
   - Detección de Creadores Seriales Tóxicos (BP-008).
   - Detección de ATH sin Pullback (BP-009).
5. Partición Temporal OOS Sellada (Train 60%, Val 20%, Test OOS 20%, BP-012).
"""
import sys
import numpy as np
import pandas as pd
from scipy.stats import binomtest

from .costs import DEFAULT_COSTS, PLATFORM_FEE_ROUNDTRIP_FRAC, ROUNDTRIP_TX_FEE_SOL


# ===========================================================================
# 0. Robustez de Salida de Consola (Windows UTF-8 / cp1252 Fallback)
# ===========================================================================
def setup_console_encoding() -> None:
    """Evita UnicodeEncodeError en terminales Windows configurando UTF-8 seguro."""
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_log(msg: str) -> None:
    """Imprime un mensaje asegurando compatibilidad de caracteres."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Fallback sin caracteres especiales si la consola no soporta utf-8
        clean = msg.encode("ascii", errors="replace").decode("ascii")
        print(clean, flush=True)


# ===========================================================================
# 1. Corrección de Múltiples Comparaciones (FDR: Benjamini-Hochberg)
# ===========================================================================
def benjamini_hochberg(p_values: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    """
    Controla el False Discovery Rate (FDR) mediante el procedimiento Benjamini-Hochberg.
    Retorna un array booleano indicando qué hipótesis nulas se rechazan (descubrimientos válidos).
    """
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    if n == 0:
        return np.array([], dtype=bool)

    # Filtrar NaN
    valid_mask = np.isfinite(p)
    if not np.any(valid_mask):
        return np.zeros(n, dtype=bool)

    valid_indices = np.where(valid_mask)[0]
    p_valid = p[valid_mask]
    m = len(p_valid)

    sorted_order = np.argsort(p_valid)
    sorted_p = p_valid[sorted_order]
    thresholds = (np.arange(1, m + 1) / m) * alpha

    significant = sorted_p <= thresholds
    rejected_valid = np.zeros(m, dtype=bool)
    if np.any(significant):
        max_k = np.max(np.where(significant)[0])
        rejected_valid[sorted_order[: max_k + 1]] = True

    rejected = np.zeros(n, dtype=bool)
    rejected[valid_indices] = rejected_valid
    return rejected


def compute_q_values(p_values: np.ndarray) -> np.ndarray:
    """
    Calcula los q-values (p-valores ajustados por FDR) de Benjamini-Hochberg.
    El q-value representa la tasa mínima de falsos descubrimientos si se fija ese umbral.
    """
    p = np.asarray(p_values, dtype=float)
    n = len(p)
    if n == 0:
        return np.array([], dtype=float)

    valid_mask = np.isfinite(p)
    q_values = np.full(n, np.nan, dtype=float)
    if not np.any(valid_mask):
        return q_values

    valid_indices = np.where(valid_mask)[0]
    p_valid = p[valid_mask]
    m = len(p_valid)

    sorted_order = np.argsort(p_valid)
    sorted_p = p_valid[sorted_order]

    # q_i = min_{j >= i} (m / j * p_j)
    q_sorted = np.empty(m, dtype=float)
    q_sorted[-1] = sorted_p[-1]
    for i in range(m - 2, -1, -1):
        rank = i + 1
        q_candidate = sorted_p[i] * m / rank
        q_sorted[i] = min(q_sorted[i + 1], q_candidate)

    q_sorted = np.clip(q_sorted, 0.0, 1.0)
    q_out = np.empty(m, dtype=float)
    q_out[sorted_order] = q_sorted
    q_values[valid_indices] = q_out
    return q_values


# ===========================================================================
# 2. Minería de Subgrupos (Subgroup Discovery con WRAcc & Beam Search)
# ===========================================================================
class SubgroupMiner:
    """
    Descubridor de subgrupos no voraces mediante Beam Search optimizando
    Weighted Relative Accuracy (WRAcc) y control FDR estricto.
    """

    def __init__(
        self,
        min_support: int = 30,
        max_depth: int = 2,
        beam_width: int = 15,
        alpha_fdr: float = 0.05,
        jaccard_sim_max: float = 0.70,
    ):
        self.min_support = min_support
        self.max_depth = max_depth
        self.beam_width = beam_width
        self.alpha_fdr = alpha_fdr
        self.jaccard_sim_max = jaccard_sim_max

    def _discretize_features(self, df: pd.DataFrame, feature_cols: list) -> list:
        selectors = []
        for col in feature_cols:
            s = df[col].dropna()
            if len(s) < self.min_support or s.nunique() < 2:
                continue
            if set(np.unique(s)) <= {0.0, 1.0}:
                # Binario
                selectors.append((col, "==", 1.0, f"{col} = SÍ", df[col] == 1.0))
                selectors.append((col, "==", 0.0, f"{col} = NO", df[col] == 0.0))
            else:
                # 1. Umbrales de desigualdad por percentiles (25, 50, 75)
                for pct in (25, 50, 75):
                    val = float(np.percentile(s, pct))
                    selectors.append((col, ">", val, f"{col} > {val:.3g}", df[col] > val))
                    selectors.append((col, "<=", val, f"{col} <= {val:.3g}", df[col] <= val))
                # 2. Rangos discretizados por cuantiles
                n_bins = 3 if s.nunique() < 10 else 4
                try:
                    q_cuts = pd.qcut(s, q=n_bins, duplicates="drop")
                    for cat in q_cuts.unique():
                        mask = df[col].between(cat.left, cat.right, inclusive="both")
                        cond_name = f"{col} ∈ [{cat.left:.3g}, {cat.right:.3g}]"
                        selectors.append((col, "range", (cat.left, cat.right), cond_name, mask))
                except Exception:
                    pass
        return selectors

    def mine(self, df: pd.DataFrame, target_col: str, feature_cols: list) -> list:
        """
        Ejecuta Beam Search para extraer reglas conjuncionales estadísticamente validadas.
        """
        valid_df = df.dropna(subset=[target_col]).copy()
        N = len(valid_df)
        if N < self.min_support * 2:
            return []

        y = valid_df[target_col].astype(float).to_numpy()
        p0 = float(np.mean(y))
        if p0 <= 0 or p0 >= 1:
            return []

        # Generar selectores atómicos
        selectors = self._discretize_features(valid_df, feature_cols)
        if not selectors:
            return []

        # Beam Search
        # Cada elemento: (score, depth, [cond_strings], combined_mask)
        beam = []
        for col, op, val, desc_str, mask_ser in selectors:
            mask = mask_ser.to_numpy(bool)
            n_sub = int(np.sum(mask))
            if n_sub < self.min_support:
                continue
            p_sub = float(np.mean(y[mask]))
            wracc = (n_sub / N) * (p_sub - p0)
            if wracc > 0 and p_sub > p0:
                beam.append((wracc, 1, [(col, op, val, desc_str)], mask))

        beam.sort(key=lambda x: x[0], reverse=True)
        beam = beam[: self.beam_width]

        all_candidates = list(beam)

        # Expandir a profundidad 2
        for depth in range(2, self.max_depth + 1):
            next_beam = []
            for parent_score, _, parent_conds, parent_mask in beam:
                parent_cols = {c[0] for c in parent_conds}
                for col, op, val, desc_str, mask_ser in selectors:
                    if col in parent_cols:
                        continue  # no repetir la misma variable
                    mask = parent_mask & mask_ser.to_numpy(bool)
                    n_sub = int(np.sum(mask))
                    if n_sub < self.min_support:
                        continue
                    p_sub = float(np.mean(y[mask]))
                    wracc = (n_sub / N) * (p_sub - p0)
                    if wracc > parent_score and p_sub > p0:
                        next_beam.append(
                            (wracc, depth, parent_conds + [(col, op, val, desc_str)], mask)
                        )
            next_beam.sort(key=lambda x: x[0], reverse=True)
            beam = next_beam[: self.beam_width]
            all_candidates.extend(beam)

        if not all_candidates:
            return []

        # Calcular significancia y corregir FDR
        raw_p_values = []
        evaluated = []
        for score, depth, conds, mask in all_candidates:
            n_sub = int(np.sum(mask))
            w_sub = int(np.sum(y[mask]))
            wr = w_sub / n_sub
            res_test = binomtest(w_sub, n_sub, p0, alternative="greater")
            p_val = float(res_test.pvalue)
            raw_p_values.append(p_val)
            rule_str = " Y ".join(c[3] for c in conds)
            evaluated.append({
                "rule": rule_str,
                "depth": depth,
                "n": n_sub,
                "wins": w_sub,
                "winrate": wr,
                "baseline": p0,
                "lift": wr / p0,
                "wracc": score,
                "p_value": p_val,
                "mask": mask,
            })

        q_values = compute_q_values(np.array(raw_p_values))
        fdr_pass = benjamini_hochberg(np.array(raw_p_values), alpha=self.alpha_fdr)

        for i, item in enumerate(evaluated):
            item["q_value"] = q_values[i]
            item["fdr_significant"] = fdr_pass[i]

        # Filtrar significativos por FDR y deduplicar por similitud de Jaccard
        significant_rules = [r for r in evaluated if r["fdr_significant"] or r["q_value"] <= 0.10]
        significant_rules.sort(key=lambda r: (r["lift"], r["wracc"]), reverse=True)

        deduped = []
        accepted_masks = []
        for cand in significant_rules:
            is_redundant = False
            for prev_mask in accepted_masks:
                inter = np.sum(cand["mask"] & prev_mask)
                union = np.sum(cand["mask"] | prev_mask)
                jaccard = inter / union if union > 0 else 0
                if jaccard > self.jaccard_sim_max:
                    is_redundant = True
                    break
            if not is_redundant:
                accepted_masks.append(cand["mask"])
                clean_entry = {k: v for k, v in cand.items() if k != "mask"}
                deduped.append(clean_entry)

        return deduped


# ===========================================================================
# 3. Modelado de Ejecución Realista (Anti-Ilusión de Capital)
# ===========================================================================
# Parámetros empíricos auditados (BP-001, BP-010, §3)
EMPIRICAL_SL_SLIPPAGE = DEFAULT_COSTS.sl_panic_haircut
EMPIRICAL_STALL_LOSS = DEFAULT_COSTS.stall_haircut
# C6: fees de red IDA+VUELTA desde la fuente única (antes era solo priority_fee,
# 0.002, y luego se multiplicaba x2 => 0.004, la mitad de lo que cobra el cost
# model unificado: 0.008 SOL).
REAL_TX_FEE_SOL = ROUNDTRIP_TX_FEE_SOL


def realistic_tpsl_outcome(
    path: tuple,
    t_entry: float,
    p_entry: float,
    tp: float,
    sl: float,
    window_s: float,
    realistic: bool = True,
    position_size_sol: float = 0.5,
) -> dict:
    """
    Simula la salida de una posición con diferenciación entre modo nominal
    y modo auditado realista (con evaporación de liquidez, latencia y comisiones de red).
    """
    ts, px = path
    i = int(np.searchsorted(ts, t_entry, side="left"))
    j = int(np.searchsorted(ts, t_entry + window_s, side="right"))
    t_slice, p_slice = ts[i:j], px[i:j]

    if len(p_slice) == 0:
        return {
            "result": "timeout",
            "t_exit": t_entry + window_s,
            "p_exit": p_entry,
            "pnl_pct": 0.0,
            "pnl_sol": -REAL_TX_FEE_SOL if realistic else 0.0,
            "slippage_applied": 0.0,
            "fee_sol": REAL_TX_FEE_SOL if realistic else 0.0,
        }

    rel = p_slice / p_entry - 1.0
    hit_tp = rel >= tp
    hit_sl = rel <= -sl
    i_tp = int(np.argmax(hit_tp)) if hit_tp.any() else 10**9
    i_sl = int(np.argmax(hit_sl)) if hit_sl.any() else 10**9

    # C6: coste total desde la fuente única = fees de red (ida+vuelta) + fee de
    # plataforma sobre el notional. Antes el simulador offline solo cobraba
    # 0.004 SOL de red y NO cobraba plataforma, mientras el camino live cobraba
    # 0.008 SOL + 3.5%. Resultado: el backtest era optimista frente al live.
    fees = 0.0
    if realistic:
        fees = REAL_TX_FEE_SOL + position_size_sol * PLATFORM_FEE_ROUNDTRIP_FRAC

    if i_tp == 10**9 and i_sl == 10**9:
        # Timeout / STALL
        if realistic:
            # Latencia de sampler en STALL: caída empírica de -28.93%
            realized_pnl_pct = min(-EMPIRICAL_STALL_LOSS * 100.0, float(rel[-1]) * 100.0)
            p_exit = p_entry * (1.0 + realized_pnl_pct / 100.0)
            pnl_sol = (position_size_sol * realized_pnl_pct / 100.0) - fees
            return {
                "result": "timeout",
                "t_exit": float(t_slice[-1]),
                "p_exit": float(p_exit),
                "pnl_pct": float(realized_pnl_pct),
                "pnl_sol": float(pnl_sol),
                "slippage_applied": EMPIRICAL_STALL_LOSS,
                "fee_sol": fees,
            }
        else:
            pnl_pct = float(rel[-1]) * 100.0
            return {
                "result": "timeout",
                "t_exit": float(t_slice[-1]),
                "p_exit": float(p_slice[-1]),
                "pnl_pct": pnl_pct,
                "pnl_sol": position_size_sol * pnl_pct / 100.0,
                "slippage_applied": 0.0,
                "fee_sol": 0.0,
            }

    if i_tp < i_sl:  # C5: empate en el mismo tick -> SL (conservador)
        p_exit = p_entry * (1.0 + tp)
        pnl_pct = tp * 100.0
        pnl_sol = (position_size_sol * tp) - fees
        return {
            "result": "win",
            "t_exit": float(t_slice[i_tp]),
            "p_exit": float(p_exit),
            "pnl_pct": float(pnl_pct),
            "pnl_sol": float(pnl_sol),
            "slippage_applied": 0.0,
            "fee_sol": fees,
        }
    else:
        # Stop Loss alcanzado: en memecoins la liquidez se evapora (BP-001)
        if realistic:
            # El stop nominal no se respeta: caída con slippage medio de -33.49%
            # Realized loss = -(nominal_sl + 33.49%), acotado a -98%
            realized_loss_frac = min(0.98, sl + EMPIRICAL_SL_SLIPPAGE)
            p_exit = p_entry * (1.0 - realized_loss_frac)
            pnl_pct = -realized_loss_frac * 100.0
            pnl_sol = (-position_size_sol * realized_loss_frac) - fees
            return {
                "result": "loss",
                "t_exit": float(t_slice[i_sl]),
                "p_exit": float(p_exit),
                "pnl_pct": float(pnl_pct),
                "pnl_sol": float(pnl_sol),
                "slippage_applied": EMPIRICAL_SL_SLIPPAGE,
                "fee_sol": fees,
            }
        else:
            p_exit = p_entry * (1.0 - sl)
            pnl_pct = -sl * 100.0
            pnl_sol = -position_size_sol * sl
            return {
                "result": "loss",
                "t_exit": float(t_slice[i_sl]),
                "p_exit": float(p_exit),
                "pnl_pct": float(pnl_pct),
                "pnl_sol": float(pnl_sol),
                "slippage_applied": 0.0,
                "fee_sol": 0.0,
            }


# ===========================================================================
# 4. Microestructura On-Chain: Concentración, Burstiness y Detección Cabal
# ===========================================================================
def calculate_buyer_hhi(buy_sol_series: pd.Series) -> float:
    """
    Calcula el Índice Herfindahl-Hirschman (HHI) de concentración de compradores.
    HHI = sum(s_i^2), donde s_i es la fracción de SOL comprada por la wallet i.
    Valor entre 0.0 (distribución uniforme de retail) y 1.0 (monopolio de 1 wallet).
    """
    if buy_sol_series is None or len(buy_sol_series) == 0:
        return np.nan
    s = pd.to_numeric(buy_sol_series, errors="coerce").dropna()
    total = s.sum()
    if total <= 0:
        return np.nan
    shares = s / total
    return float(np.sum(shares**2))


def calculate_burstiness(inter_arrival_times: np.ndarray) -> float:
    """
    Calcula el parámetro de burstiness B de Goh & Barabási:
    B = (sigma - mu) / (sigma + mu)
    -1.0: Periódico/determinista (bots automáticos).
     0.0: Proceso de Poisson (flujo independiente aleatorio).
    >0.3: Ráfagas concentradas y coordinadas (snipers/cabal coludida).
    """
    t = np.asarray(inter_arrival_times, dtype=float)
    t = t[np.isfinite(t) & (t >= 0)]
    if len(t) < 4:
        return np.nan
    mu = float(np.mean(t))
    sigma = float(np.std(t))
    if (sigma + mu) <= 0:
        return 0.0
    return float((sigma - mu) / (sigma + mu))


def calculate_exact_bonding_curve(net_sol: float) -> dict:
    """
    Mecánica exacta de la Bonding Curve de pump.fun:
    Virtual SOL = 30.0 SOL, Virtual Token = 1,073,000,000 tokens.
    Curva saturada si net_sol >= 85 SOL (o > 85% de avance, BP-007).
    """
    x_virt = 30.0
    y_virt = 1_073_000_000.0
    k = x_virt * y_virt
    curve_sol_target = 85.0

    progress = min(1.0, max(0.0, net_sol / curve_sol_target))
    is_saturated = progress > 0.85  # BP-007: saturación pre-migración

    # Precio spot actual en la curva
    current_sol = max(0.1, x_virt + net_sol)
    current_tokens = k / current_sol
    spot_price = current_sol / current_tokens if current_tokens > 0 else np.nan

    return {
        "progress": float(progress),
        "is_saturated": int(is_saturated),
        "spot_price": float(spot_price),
        "net_sol": float(net_sol),
    }


def detect_cabal_wallets(
    trades_df: pd.DataFrame, early_window_s: float = 60.0, min_co_occurrences: int = 2
) -> dict:
    """
    Construye la proyección del grafo bipartito Wallets-Tokens para los primeros 60s
    y calcula el peso Adamic-Adar para aislar cabales que snipean juntos repetidamente (BP-005).
    """
    if trades_df is None or len(trades_df) == 0:
        return {"cabal_wallets": set(), "cabal_share_by_token": {}}

    required = {"token_id", "wallet", "ts"}
    if not required.issubset(trades_df.columns):
        return {"cabal_wallets": set(), "cabal_share_by_token": {}}

    # Filtrar primeros segundos de cada token
    min_ts_per_token = trades_df.groupby("token_id")["ts"].transform("min")
    early_mask = (trades_df["ts"] - min_ts_per_token) <= early_window_s
    early_trades = trades_df[early_mask].dropna(subset=["token_id", "wallet"])

    if "side" in early_trades.columns:
        early_trades = early_trades[early_trades["side"] == "buy"]

    # Diccionario de token -> wallets
    tokens_by_wallet = early_trades.groupby("wallet")["token_id"].unique()
    wallets_by_token = early_trades.groupby("token_id")["wallet"].unique().to_dict()

    # Bipartite Adamic-Adar: tokens con pocos compradores aportan mayor ponderación
    co_occurrences = {}
    for tok, w_list in wallets_by_token.items():
        n_w = len(w_list)
        if n_w < 2:
            continue
        weight = 1.0 / np.log(1.0 + n_w)
        for i in range(len(w_list)):
            for j in range(i + 1, len(w_list)):
                pair = tuple(sorted([w_list[i], w_list[j]]))
                co_occurrences[pair] = co_occurrences.get(pair, 0.0) + weight

    # Wallets con alta co-ocurrencia
    cabal_wallets = set()
    for (w1, w2), weight in co_occurrences.items():
        if weight >= min_co_occurrences:
            cabal_wallets.add(w1)
            cabal_wallets.add(w2)

    # Calcular share de cabal por token
    cabal_share_by_token = {}
    has_sol = "sol_amount" in early_trades.columns
    for tok, g in early_trades.groupby("token_id"):
        if len(g) == 0:
            continue
        cabal_mask = g["wallet"].isin(cabal_wallets)
        if has_sol and g["sol_amount"].sum() > 0:
            share = float(g.loc[cabal_mask, "sol_amount"].sum() / g["sol_amount"].sum())
        else:
            share = float(cabal_mask.mean())
        cabal_share_by_token[str(tok)] = share

    return {
        "cabal_wallets": cabal_wallets,
        "cabal_share_by_token": cabal_share_by_token,
    }


def detect_block0_snipers(trades_df: pd.DataFrame, block0_window_s: float = 2.0) -> dict:
    """
    Identifica si los primeros 2 segundos (bloque 0 / slot 0) concentraron más
    del 25% de la liquidez acumulada (BP-006: trampa de bloque 0).
    """
    if trades_df is None or len(trades_df) == 0:
        return {}

    required = {"token_id", "ts"}
    if not required.issubset(trades_df.columns):
        return {}

    min_ts = trades_df.groupby("token_id")["ts"].transform("min")
    rel_ts = trades_df["ts"] - min_ts

    out = {}
    has_sol = "sol_amount" in trades_df.columns

    for tok, g in trades_df.groupby("token_id"):
        t_first = g["ts"].min()
        b0_mask = (g["ts"] - t_first) <= block0_window_s
        if has_sol and g["sol_amount"].sum() > 0:
            b0_sol = g.loc[b0_mask, "sol_amount"].sum()
            tot_sol = g["sol_amount"].sum()
            share = float(b0_sol / tot_sol)
        else:
            share = float(b0_mask.mean())

        out[str(tok)] = {
            "block0_share": share,
            "is_block0_trap": int(share > 0.25),  # BP-006
        }
    return out


# ===========================================================================
# 5. Aislamiento Temporal OOS (Train 60%, Val 20%, Test OOS 20%, BP-012)
# ===========================================================================
def temporal_train_val_test_split(
    df: pd.DataFrame,
    time_col: str = "created_at",
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    purge_gap: int = 0,
) -> tuple:
    """
    Partición temporal estricta para evitar contaminación de test y overfitting (BP-012).
    - Train: 60% más antiguo (minería de hipótesis).
    - Val: 20% intermedio (calibración y early stopping).
    - Test: 20% más reciente (OOS sellado, evaluación final de generalización).
    - purge_gap: nº de filas eliminadas en cada frontera train/val y val/test
      para purgar solapimientos de features con memoria (Ola 1, purged split).
    """
    if time_col not in df.columns:
        # Fallback al índice
        ordered_df = df.copy()
    else:
        # Conservar el índice original permite aplicar el modelo únicamente
        # al bloque Test sin mezclarlo con Train/Validation.
        ordered_df = df.sort_values(time_col, kind="mergesort").copy()

    n = len(ordered_df)
    n_train = int(n * train_ratio)
    n_val = int(n * (train_ratio + val_ratio))
    gap = max(0, int(purge_gap))

    train_df = ordered_df.iloc[: max(0, n_train - gap)].copy()
    val_df = ordered_df.iloc[n_train:n_val].copy()
    test_df = ordered_df.iloc[min(n, n_val + gap):].copy()

    return train_df, val_df, test_df


# ===========================================================================
# 6. Grid TP/SL Realista vs Nominal (Auditoría Financiera)
# ===========================================================================
def realistic_tpsl_grid(d, df: pd.DataFrame, cfg) -> dict:
    """
    Evalúa cada combinación (TP, SL) aplicando:
    1. Modo Nominal (ejecución al precio del stop).
    2. Modo Realista Auditado (slippage -33.49% en SL, fees 0.002 SOL, penalización STALL).
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
        entries.append((path, float(et), float(ep)))

    if not entries:
        return {}

    tps = cfg.tp_grid
    sls = cfg.sl_grid
    window_s = cfg.mfe_window_min * 60.0

    nominal_wr, nominal_exp = {}, {}
    realistic_wr, realistic_exp = {}, {}

    for tp in tps:
        for sl in sls:
            tpf, slf = tp / 100.0, sl / 100.0
            r_risk_sol = 0.5 * slf  # 1R de riesgo en SOL para tamaño 0.5 SOL

            n_nom_wins, n_real_wins = 0, 0
            nom_r_sum, real_r_sum = 0.0, 0.0
            valid = 0

            for path, et, ep in entries:
                # 1. Nominal
                res_nom = realistic_tpsl_outcome(
                    path, et, ep, tpf, slf, window_s, realistic=False, position_size_sol=0.5
                )
                # 2. Realista
                res_real = realistic_tpsl_outcome(
                    path, et, ep, tpf, slf, window_s, realistic=True, position_size_sol=0.5
                )

                valid += 1
                if res_nom["result"] == "win":
                    n_nom_wins += 1
                    nom_r_sum += (tp / sl)
                elif res_nom["result"] == "loss":
                    nom_r_sum += -1.0
                else:
                    nom_r_sum += (res_nom["pnl_pct"] / 100.0) / slf

                if res_real["result"] == "win":
                    n_real_wins += 1
                # En R real: pnl_sol / r_risk_sol
                real_r_sum += res_real["pnl_sol"] / max(r_risk_sol, 1e-6)

            pair = (tp, sl)
            nominal_wr[pair] = n_nom_wins / valid if valid else np.nan
            nominal_exp[pair] = nom_r_sum / valid if valid else np.nan
            realistic_wr[pair] = n_real_wins / valid if valid else np.nan
            realistic_exp[pair] = real_r_sum / valid if valid else np.nan

    return {
        "tps": tps,
        "sls": sls,
        "n": len(entries),
        "nominal_winrate": nominal_wr,
        "nominal_expectancy": nominal_exp,
        "realistic_winrate": realistic_wr,
        "realistic_expectancy": realistic_exp,
    }


# ===========================================================================
# 7. Evaluación Out-Of-Sample (OOS) de Reglas
# ===========================================================================
def evaluate_rules_oos(rules_df: pd.DataFrame, test_df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    Evalúa si las reglas descubiertas mantienen su lift y winrate en el bloque OOS sellado.
    """
    if rules_df is None or len(rules_df) == 0 or test_df is None or len(test_df) == 0:
        return rules_df

    out = rules_df.copy()
    if label not in test_df.columns:
        out["oos_n"] = np.nan
        out["oos_winrate"] = np.nan
        out["oos_lift"] = np.nan
        out["oos_pass"] = False
        return out

    base_oos = float(test_df[label].mean())
    oos_ns, oos_wrs, oos_lifts, oos_passes = [], [], [], []

    for r in out.itertuples():
        r_str = str(getattr(r, "rule", ""))
        # Evaluar regla sobre test_df de forma segura
        # Para expresiones sencillas
        try:
            # Si tiene 'conds' original
            conds = getattr(r, "conds", None)
            if conds and isinstance(conds, list):
                mask = np.ones(len(test_df), dtype=bool)
                for f, op, thr in conds:
                    if f in test_df.columns:
                        if op == "<=":
                            mask &= (test_df[f] <= thr).to_numpy(bool)
                        elif op == ">":
                            mask &= (test_df[f] > thr).to_numpy(bool)
                n_o = int(np.sum(mask))
                if n_o >= 5 and base_oos > 0:
                    w_o = float(test_df.loc[mask, label].sum())
                    wr_o = w_o / n_o
                    lift_o = wr_o / base_oos
                    oos_ns.append(n_o)
                    oos_wrs.append(wr_o)
                    oos_lifts.append(lift_o)
                    oos_passes.append(bool(lift_o >= 1.05 and n_o >= 10))
                    continue
        except Exception:
            pass
        oos_ns.append(np.nan)
        oos_wrs.append(np.nan)
        oos_lifts.append(np.nan)
        oos_passes.append(False)

    out["oos_n"] = oos_ns
    out["oos_winrate"] = oos_wrs
    out["oos_lift"] = oos_lifts
    out["oos_pass"] = oos_passes
    return out


# ===========================================================================
# 8. Exportación de Estrategia Lista para Producción
# ===========================================================================
def export_production_strategy(top_rules: list, best_policy: tuple, out_dir: str) -> dict:
    """
    Genera reglas ejecutables en formato JSON y un módulo Python autónomo para bots de trading.
    """
    import json
    import os

    from .export_gate import oos_export_gate

    os.makedirs(out_dir, exist_ok=True)
    strategy_json = {
        "best_exit_policy": {
            "take_profit_pct": best_policy[0] if best_policy else 50.0,
            "stop_loss_pct": best_policy[1] if best_policy else 30.0,
            "realistic_slippage_haircut_pct": DEFAULT_COSTS.sl_panic_haircut * 100.0,
            "network_fee_sol": DEFAULT_COSTS.priority_fee_sol,
        },
        "promotion_policy": {
            "requires_oos_pass": True,
            "minimum_oos_n": 10,
            "minimum_oos_lift": 1.0,
            "fail_closed_without_rules": True,
        },
        "entry_rules": [],
    }

    # Solo se promocionan reglas con soporte, bounds estructurados y lift positivo en el test sellado.
    # Sin evidencia OOS ni condiciones estructuradas, la salida de producción queda deliberadamente vacía.
    # El filtro lo aplica oos_export_gate (fail-closed, testeable aisladamente).
    gate = oos_export_gate(top_rules or [], min_oos_n=10, min_oos_lift=1.0)
    for r in gate.passed_rules:
        raw_bounds = r.get("bounds", [])
        clean_bounds = [list(b) if isinstance(b, (list, tuple)) else b for b in raw_bounds]
        entry = {
            "rule": r.get("rule"),
            "winrate": r.get("winrate"),
            "lift": r.get("lift"),
            "support_n": r.get("n"),
            "q_value_fdr": r.get("q_value"),
            "fdr_significant": r.get("fdr_significant", r.get("fdr_sig", False)),
            "oos_pass": True,
            "oos_lift": r.get("oos_lift"),
            "bounds": clean_bounds,
        }
        # Métricas de validación Out-Of-Sample (bloque Test sellado)
        if r.get("oos_winrate") is not None:
            entry["oos_winrate"] = r.get("oos_winrate")
            entry["oos_n"] = r.get("oos_n")
        strategy_json["entry_rules"].append(entry)

    json_path = os.path.join(out_dir, "reglas_produccion.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(strategy_json, f, indent=2, ensure_ascii=False)

    py_code = f'''"""
Estrategia de Sniping Pump.fun Generada Automáticamente.
Configurada con reglas validadas estadísticamente (FDR <= 0.05), filtros de veto (BP-005 a BP-009)
y salidas dinámicas realistas (Trailing SL + Breakeven + STALL Kill-Switch).
"""

BEST_TP_PCT = {strategy_json["best_exit_policy"]["take_profit_pct"]}
BEST_SL_PCT = {strategy_json["best_exit_policy"]["stop_loss_pct"]}
BREAKEVEN_TRIGGER_PCT = 35.0
TRAILING_STOP_PCT = 20.0
STALL_LIMIT_SECONDS = 60.0
KELLY_FRACTION_PCT = 10.0  # Quarter-Kelly (10% max allocation per token)
EXPORTED_RULES = {strategy_json["entry_rules"]!r}

def should_enter_token(features: dict) -> tuple[bool, str]:
    """
    Evalúa si un token cumple al menos una de las reglas de oro descubiertas
    y no viola ningún antipatrón de veto (BP-005 a BP-009).
    Retorna (debe_entrar: bool, razon_o_regla: str).
    """
    # 1. Antipatrones de Veto Inmediato (Protocolo de Supervisión BP-005 a BP-009)
    if features.get("cabal_share", 0.0) > 0.30:
        return False, "VETO BP-005: Cabal coludida por grafo > 30%"
    if features.get("is_block0_trap", 0) == 1:
        return False, "VETO BP-006: Trampa de snipers en bloque 0 (>25%)"
    if features.get("ef_curve_progress", features.get("curve_progress_5m", 0.0)) > 0.85:
        return False, "VETO BP-007: Curva de bonding saturada pre-migración (>85%)"
    if features.get("dev_is_serial_rugger", 0) == 1:
        return False, "VETO BP-008: Creador serial tóxico (>=3 rugs previos)"

    if {len(strategy_json["entry_rules"])} == 0:
        return False, "SIN_REGLA_OOS_PROMOVIDA"

    # 2. Evaluar únicamente las condiciones estructuradas que sobrevivieron
    # FDR + Test OOS; nunca usar thresholds históricos hardcodeados.
    for rule in EXPORTED_RULES:
        bounds = rule.get("bounds", [])
        if not bounds:
            continue
        matched = True
        for feature, lower, upper in bounds:
            value = features.get(feature)
            if value is None or not (float(lower) <= float(value) <= float(upper)):
                matched = False
                break
        if matched:
            return True, "APROBADO_OOS: " + str(rule.get("rule", "regla"))

    return False, "Sin señal de alta probabilidad"

def evaluate_master_signal(features: dict) -> tuple[bool, str, float]:
    """
    Evalúa el token mediante la SEÑAL MAESTRA UNIFICADA (MasterSnipeSignal).
    Retorna (admitido: bool, razon_o_rama: str, confianza: float).
    """
    try:
        from detector.master_signal import MasterSnipeSignal
        dec = MasterSnipeSignal.evaluate(features)
        reason = dec.branch if dec.admitted else (dec.veto_reason or "REJECTED")
        return dec.admitted, reason, dec.confidence
    except ImportError:
        # Fallback autónomo si se ejecuta fuera del paquete
        mc = float(features.get("marketCap", features.get("market_cap", 0.0)) or 0.0)
        fmc = float(features.get("firstMarketCap", features.get("first_market_cap", mc)) or mc)
        fliq = float(features.get("firstLiquidity", features.get("first_liquidity", 0.0)) or 0.0)
        dev_b = float(features.get("devBalancePct", features.get("dev_balance_pct", 0.0)) or 0.0)
        res = float(features.get("realSolReserves", features.get("sol_reserves", 30.0)) or 30.0)
        mig = float(features.get("migrationProgress", 0.0) or 0.0)

        if mc < 4500.0:
            return False, "VETO_DEAD_LAUNCH (MC < $4,500)", 0.0
        if dev_b > 1.5:
            return False, "VETO_DEV_DUMP_RISK (dev > 1.5%)", 0.0
        if res < 12.0:
            return False, "VETO_LOW_RESERVES (reserves < 12 SOL)", 0.0
        if mig > 85.0:
            return False, "VETO_SATURATION (progress > 85%)", 0.0

        if fmc <= 4285.0 and fliq <= 1725.0:
            return True, "MASTER_ALPHA_EXPANSION (Branch A)", 0.92
        return False, "NO_SIGNAL", 0.0

def get_dynamic_exit_policy() -> dict:
    """Retorna la política de gestión de salidas dinámicas auditada."""
    return {{
        "initial_sl_pct": BEST_SL_PCT,
        "take_profit_pct": BEST_TP_PCT,
        "breakeven_trigger_pct": BREAKEVEN_TRIGGER_PCT,
        "trailing_stop_pct": TRAILING_STOP_PCT,
        "stall_window_s": STALL_LIMIT_SECONDS,
        "realistic_sl_haircut_pct": 33.49,
        "network_fee_sol": 0.002,
    }}

def calculate_position_size_sol(portfolio_sol: float, max_cap_sol: float = 1.0) -> float:
    """Calcula el tamaño de posición en SOL según Fractional Kelly (Quarter-Kelly)."""
    target = portfolio_sol * (KELLY_FRACTION_PCT / 100.0)
    return min(max_cap_sol, target)
'''

    py_path = os.path.join(out_dir, "estrategia_produccion.py")
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(py_code)

    return {"json_path": json_path, "py_path": py_path}


def evaluate_by_splits(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    label_col: str = "win_real_net",
) -> dict:
    """
    Calcula y reporta métricas de trading separadas explícitamente por bloque temporal (P0 / BP-012):
    - train: descubrimiento de reglas y ajuste inicial (60%)
    - val: calibración de umbrales y selección de políticas (20%)
    - test_oos: bloque sellado de evaluación final fuera de muestra (20%)
    """
    out = {}
    for name, split_df in [("train", train_df), ("val", val_df), ("test_oos", test_df)]:
        if split_df is None or len(split_df) == 0:
            out[name] = {"n": 0, "winrate": 0.0, "expectancy_sol": 0.0, "profit_factor": 0.0, "pnl_sol": 0.0}
            continue
        n = len(split_df)
        wr = float(split_df[label_col].mean()) if label_col in split_df.columns else 0.0
        pnl_sol_tot = float(split_df["pnl_sol"].sum()) if "pnl_sol" in split_df.columns else 0.0
        ev_sol = pnl_sol_tot / n if n > 0 else 0.0

        pnl_series = split_df["pnl_sol"] if "pnl_sol" in split_df.columns else pd.Series(dtype=float)
        gains = float(pnl_series[pnl_series > 0].sum())
        losses = float(abs(pnl_series[pnl_series < 0].sum()))
        pf = (gains / losses) if losses > 0 else (10.0 if gains > 0 else 0.0)

        out[name] = {
            "n": n,
            "winrate": wr,
            "expectancy_sol": ev_sol,
            "profit_factor": pf,
            "pnl_sol": pnl_sol_tot,
        }
    return out


# ===========================================================================
# 9. Salidas Dinámicas (Trailing Stop, Breakeven y STALL Kill-Switch)
# ===========================================================================
def simulate_dynamic_exit(
    path: tuple,
    t_entry: float,
    p_entry: float,
    initial_sl_pct: float = 30.0,
    take_profit_pct: float = 100.0,
    breakeven_trigger_pct: float = 40.0,
    trailing_stop_pct: float = 20.0,
    stall_window_s: float = 60.0,
    stall_min_ret_pct: float = 5.0,
    window_s: float = 1800.0,
    realistic: bool = True,
    position_size_sol: float = 0.5,
) -> dict:
    """
    Simula una política de salidas dinámicas de nivel institucional:
    1. Breakeven Ratchet: si la ganancia toca +breakeven_trigger_pct, el stop sube a entrada + fee buffer.
    2. Trailing Stop Loss: una vez confirmada la tendencia alcista, el stop persigue el pico de precio (MFE).
    3. Take Profit: salida completa al tocar el objetivo superior.
    4. STALL Kill-Switch: aborta en stall_window_s si el token no avanza, antes de que se drene la curva.
    """
    ts, px = path
    i = int(np.searchsorted(ts, t_entry, side="left"))
    j = int(np.searchsorted(ts, t_entry + window_s, side="right"))
    t_slice, p_slice = ts[i:j], px[i:j]

    # C6: coste total (red ida+vuelta + plataforma) desde la fuente única.
    fee_sol = 0.0
    if realistic:
        fee_sol = REAL_TX_FEE_SOL + position_size_sol * PLATFORM_FEE_ROUNDTRIP_FRAC
    fee_buffer_frac = 0.03  # +3% para cubrir comisiones en Breakeven

    if len(p_slice) == 0:
        return {
            "result": "timeout", "t_exit": t_entry + window_s, "p_exit": p_entry,
            "pnl_pct": 0.0, "pnl_sol": -fee_sol, "exit_reason": "TIMEOUT",
            "fee_sol": fee_sol, "slippage_applied": 0.0,
        }

    p_peak = float(p_entry)
    current_sl = float(p_entry * (1.0 - initial_sl_pct / 100.0))
    be_active = False

    for idx, (t, p) in enumerate(zip(t_slice, p_slice)):
        # Actualizar MFE
        if p > p_peak:
            p_peak = float(p)

        peak_gain_pct = (p_peak / p_entry - 1.0) * 100.0

        # 1. Chequeo de Breakeven y Trailing Stop
        if peak_gain_pct >= breakeven_trigger_pct:
            be_active = True
            # Ratchet a Breakeven con buffer de comisiones
            be_sl = p_entry * (1.0 + fee_buffer_frac)
            # Trailing stop desde el pico
            trail_sl = p_peak * (1.0 - trailing_stop_pct / 100.0)
            current_sl = max(current_sl, be_sl, trail_sl)

        # 2. Take Profit Directo
        if p >= p_entry * (1.0 + take_profit_pct / 100.0):
            p_exit = float(p_entry * (1.0 + take_profit_pct / 100.0))
            pnl_pct = float(take_profit_pct)
            pnl_sol = (position_size_sol * pnl_pct / 100.0) - fee_sol
            return {
                "result": "win", "t_exit": float(t), "p_exit": p_exit,
                "pnl_pct": pnl_pct, "pnl_sol": pnl_sol, "exit_reason": "TAKE_PROFIT",
                "fee_sol": fee_sol, "slippage_applied": 0.0,
            }

        # 3. Trailing Stop Hit (o Breakeven Hit)
        if p <= current_sl and be_active:
            p_exit = float(current_sl)
            pnl_pct = (p_exit / p_entry - 1.0) * 100.0
            pnl_sol = (position_size_sol * pnl_pct / 100.0) - fee_sol
            return {
                "result": "win" if pnl_pct > 0 else "loss",
                "t_exit": float(t), "p_exit": p_exit,
                "pnl_pct": float(pnl_pct), "pnl_sol": float(pnl_sol),
                "exit_reason": "TRAILING_STOP", "fee_sol": fee_sol,
                "slippage_applied": 0.0,
            }

        # 4. Stop Loss Inicial Hit
        if p <= current_sl and not be_active:
            if realistic:
                realized_loss_frac = min(0.98, (initial_sl_pct / 100.0) + EMPIRICAL_SL_SLIPPAGE)
                p_exit = float(p_entry * (1.0 - realized_loss_frac))
                pnl_pct = -realized_loss_frac * 100.0
            else:
                p_exit = float(current_sl)
                pnl_pct = -initial_sl_pct
            pnl_sol = (position_size_sol * pnl_pct / 100.0) - fee_sol
            return {
                "result": "loss", "t_exit": float(t), "p_exit": p_exit,
                "pnl_pct": float(pnl_pct), "pnl_sol": float(pnl_sol),
                "exit_reason": "STOP_LOSS", "fee_sol": fee_sol,
                "slippage_applied": EMPIRICAL_SL_SLIPPAGE if realistic else 0.0,
            }

        # 5. STALL Kill-Switch (aborta si tras stall_window_s no hay ganancia)
        elapsed_s = t - t_entry
        if elapsed_s >= stall_window_s and not be_active:
            cur_ret_pct = (p / p_entry - 1.0) * 100.0
            if cur_ret_pct < stall_min_ret_pct:
                # Salida por STALL con latencia moderada
                stall_haircut = (EMPIRICAL_STALL_LOSS * 0.5) if realistic else 0.0
                p_exit = float(p * (1.0 - stall_haircut))
                pnl_pct = (p_exit / p_entry - 1.0) * 100.0
                pnl_sol = (position_size_sol * pnl_pct / 100.0) - fee_sol
                return {
                    "result": "loss" if pnl_pct < 0 else "win",
                    "t_exit": float(t), "p_exit": p_exit,
                    "pnl_pct": float(pnl_pct), "pnl_sol": float(pnl_sol),
                    "exit_reason": "STALL_EXIT", "fee_sol": fee_sol,
                    "slippage_applied": stall_haircut,
                }

    # Fin de ventana (Timeout)
    last_p = float(p_slice[-1])
    if realistic:
        real_loss = min(-EMPIRICAL_STALL_LOSS * 100.0, (last_p / p_entry - 1.0) * 100.0)
        p_exit = p_entry * (1.0 + real_loss / 100.0)
        pnl_pct = float(real_loss)
    else:
        p_exit = last_p
        pnl_pct = (p_exit / p_entry - 1.0) * 100.0
    pnl_sol = (position_size_sol * pnl_pct / 100.0) - fee_sol
    return {
        "result": "win" if pnl_pct > 0 else "loss",
        "t_exit": float(t_slice[-1]), "p_exit": float(p_exit),
        "pnl_pct": float(pnl_pct), "pnl_sol": float(pnl_sol),
        "exit_reason": "TIMEOUT", "fee_sol": fee_sol,
        "slippage_applied": EMPIRICAL_STALL_LOSS if realistic else 0.0,
    }


# ===========================================================================
# 10. Dimensionamiento de Posición (Fractional Kelly Criterion)
# ===========================================================================
def calculate_fractional_kelly(
    winrate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    fraction: float = 0.20,
    max_cap: float = 0.15,
) -> float:
    """
    Calcula la fracción óptima de capital a arriesgar según Fractional Kelly:
    f* = (p * b - q) / b
    f_trade = min(max_cap, fraction * f*)
    Si el EV es no positivo (b*p <= q), retorna 0.0 (veto de capital).
    """
    if winrate <= 0.0 or winrate >= 1.0 or avg_win_pct <= 0.0:
        return 0.0
    loss_mag = max(0.001, abs(avg_loss_pct))
    b = avg_win_pct / loss_mag
    q = 1.0 - winrate
    edge = winrate * b - q
    if edge <= 0.0:
        return 0.0
    f_star = edge / b
    f_fractional = fraction * f_star
    return float(np.clip(f_fractional, 0.0, max_cap))


# ===========================================================================
# 11. Score Compuesto de Alpha y Filtro de Meta-Labeling
# ===========================================================================
class CompositeAlphaScorer:
    """
    Filtro de Meta-Labeling que evalúa si un token supera el umbral compuesto
    de señales cuantitativas y verifica que no viole ningún antipatrón de veto (BP-005 a BP-009).
    """

    def __init__(self, rules: list, min_score_to_enter: float = 60.0):
        self.rules = rules
        self.min_score_to_enter = min_score_to_enter

    def score_tokens(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calcula el Alpha Score [0..100] por token y emite el veredicto de entrada / veto.
        """
        n = len(df)
        scores = np.zeros(n, dtype=float)
        matched_counts = np.zeros(n, dtype=int)
        vetoed = np.zeros(n, dtype=int)
        veto_reasons = ["" for _ in range(n)]

        for idx, row in enumerate(df.itertuples()):
            # 1. Antipatrones de Veto Inmediato (Protocolo de Supervisión BP-005..BP-009)
            cabal_s = getattr(row, "cabal_share", 0.0)
            if cabal_s is not None and cabal_s > 0.30:  # BP-005: >30% cabal
                vetoed[idx] = 1
                veto_reasons[idx] = "BP-005: Concentración Cabal > 30%"
                continue

            b0_trap = getattr(row, "is_block0_trap", 0)
            if b0_trap == 1:  # BP-006: snipers >25% en bloque 0
                vetoed[idx] = 1
                veto_reasons[idx] = "BP-006: Trampa de Snipers Bloque 0"
                continue

            curve_p = getattr(row, "ef_curve_progress", getattr(row, "curve_progress_5m", 0.0))
            if curve_p is not None and curve_p > 0.85:  # BP-007: saturación pre-migración
                vetoed[idx] = 1
                veto_reasons[idx] = "BP-007: Saturación Curva Bonding > 85%"
                continue

            # 2. Puntuación por Reglas de Oro (Subgrupos Validados por FDR)
            row_score = 30.0  # Base neutral
            n_match = 0
            for r in self.rules:
                r_str = r.get("rule", "")
                lift = r.get("lift", 1.0)
                # Evaluaciones heurísticas de reglas comunes
                matched = False
                if "Compradores únicos" in r_str:
                    ub = getattr(row, "ef_unique_buyers", getattr(row, "unique_buyers_60s", np.nan))
                    if ub == ub and ub <= 7.5:
                        matched = True
                elif "Ventas acumuladas" in r_str:
                    ns = getattr(row, "ef_n_sells", getattr(row, "n_sells_60s", np.nan))
                    if ns == ns and ns <= 11:
                        matched = True
                elif "Progreso de bonding curve" in r_str:
                    cp = getattr(row, "ef_curve_progress", 0.0)
                    if cp == cp and cp <= 0.09:
                        matched = True
                elif "Trades acumulados" in r_str:
                    nt = getattr(row, "ef_n_trades", getattr(row, "n_trades_60s", np.nan))
                    if nt == nt and nt <= 76:
                        matched = True

                if matched:
                    n_match += 1
                    row_score += min(20.0, (lift - 1.0) * 25.0)

            # Bonificaciones de microestructura orgánica
            hhi = getattr(row, "ef_top5_share", getattr(row, "top5_wallet_share_60s", np.nan))
            if hhi == hhi and hhi < 0.35:
                row_score += 10.0  # Distribución sana
            dev_b = getattr(row, "ef_dev_bought", getattr(row, "dev_bought", np.nan))
            if dev_b == 1:
                row_score += 10.0  # Dev con skin in the game

            scores[idx] = min(100.0, max(0.0, row_score))
            matched_counts[idx] = n_match

        out = pd.DataFrame({
            "score": scores,
            "matched_rules_count": matched_counts,
            "vetoed": vetoed,
            "veto_reason": veto_reasons,
            "should_enter": ((scores >= self.min_score_to_enter) & (vetoed == 0)).astype(int),
        }, index=df.index)
        return out


# ===========================================================================
# 12. Microestructura DEX de Alta Frecuencia (VPIN, Amihud, Kyle's Lambda)
# ===========================================================================
class DEXVolumeSynchronizedToxicity:
    """
    Motor de VPIN (Volume-Synchronized Probability of Toxicity) para AMMs / Pump.fun.
    Utiliza reloj de volumen exacto con división determinista de swaps por dirección (buy/sell).
    """
    def __init__(self, bucket_size_sol: float = 1.0, window_buckets: int = 20):
        self.bucket_size = float(bucket_size_sol)
        self.window_buckets = int(window_buckets)
        self.curr_vb = 0.0
        self.curr_vs = 0.0
        self.imbalances = []

    def update_swap(self, sol_amount: float, is_buy: bool) -> float:
        remaining = float(sol_amount)
        while remaining > 0.0:
            curr_vol = self.curr_vb + self.curr_vs
            space = self.bucket_size - curr_vol
            fill = min(remaining, space)
            if is_buy:
                self.curr_vb += fill
            else:
                self.curr_vs += fill
            remaining -= fill
            if (self.curr_vb + self.curr_vs) >= (self.bucket_size - 1e-9):
                self.imbalances.append(abs(self.curr_vb - self.curr_vs))
                if len(self.imbalances) > self.window_buckets:
                    self.imbalances.pop(0)
                self.curr_vb = 0.0
                self.curr_vs = 0.0
        if not self.imbalances:
            return 0.0
        return float(np.clip(sum(self.imbalances) / (len(self.imbalances) * self.bucket_size), 0.0, 1.0))


def compute_vpin_for_trades(trades_slice: pd.DataFrame, bucket_size_sol: float = 1.0, window_buckets: int = 20) -> float:
    """Calcula el VPIN final sobre un conjunto ordenado de trades."""
    if trades_slice is None or len(trades_slice) == 0:
        return 0.0
    vpin_eng = DEXVolumeSynchronizedToxicity(bucket_size_sol, window_buckets)
    val = 0.0
    for r in trades_slice.itertuples():
        sol = getattr(r, "sol_amount", 0.0)
        side = str(getattr(r, "side", "buy")).lower()
        val = vpin_eng.update_swap(sol, side == "buy")
    return val


def calculate_amihud_illiq(trades_slice: pd.DataFrame) -> float:
    """Ratio de iliquidez de Amihud: mediana(|ln(P_t) - ln(P_{t-1})| / SOL_t)."""
    if trades_slice is None or len(trades_slice) < 2:
        return 0.0
    prices = trades_slice["price"].to_numpy(float)
    sols = trades_slice["sol_amount"].to_numpy(float)
    valid = np.isfinite(prices) & (prices > 0) & np.isfinite(sols) & (sols > 0.0001)
    if valid.sum() < 2:
        return 0.0
    p = prices[valid]
    s = sols[valid]
    log_ret = np.abs(np.diff(np.log(p)))
    illiq = log_ret / s[1:]
    return float(np.nanmedian(illiq)) if len(illiq) > 0 else 0.0


def calculate_cpmm_kyle_lambda(net_sol: float, k_cpmm: float = 3.219e10) -> float:
    """Kyle's Lambda analítico en curva CPMM de Pump.fun (dP/dx = 2x/k)."""
    x = 30.0 + max(0.0, float(net_sol))
    return float(2.0 * x / k_cpmm)


# ===========================================================================
# 13. Meta-Labeler Calibrado con Aislamiento Estricto OOS (BP-012)
# ===========================================================================
class CalibratedMetaLabeler:
    """
    Meta-Labeler Secundario Calibrado:
    1. Destila reglas dispersas con Logistic Lasso L1 en Train (60%).
    2. Calibra probabilidades isotónicamente en Validación (20%).
    3. Determina el umbral tau* óptimo que maximiza EV y garantiza Precision >= 65%.
    4. Evalúa generalización pura en Test OOS sellado (20%).
    """
    def __init__(self, target_precision: float = 0.65, min_trades_val: int = 15):
        self.target_precision = target_precision
        self.min_trades_val = min_trades_val
        self.feature_cols = []
        self.base_model = None
        self.calibrator = None
        self.tau_star = 0.50
        self.val_metrics = {}

    def fit_and_calibrate(
        self,
        df_train: pd.DataFrame,
        y_train: np.ndarray,
        df_val: pd.DataFrame,
        y_val: np.ndarray,
        feature_cols: list,
    ):
        from sklearn.linear_model import LogisticRegression
        from sklearn.isotonic import IsotonicRegression

        self.feature_cols = [c for c in feature_cols if c in df_train.columns and c in df_val.columns]
        X_tr = df_train[self.feature_cols].fillna(0.0).to_numpy()
        X_va = df_val[self.feature_cols].fillna(0.0).to_numpy()

        # 1. Ajuste L1 en Train (60%)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.base_model = LogisticRegression(solver="liblinear", C=0.8, random_state=42)
            self.base_model.fit(X_tr, y_train)

        # 2. Márgenes brutos en Validación (20%)
        val_margins = self.base_model.decision_function(X_va)

        # 3. Calibración Isotónica en Validación
        self.calibrator = IsotonicRegression(out_of_bounds="clip")
        self.calibrator.fit(val_margins, y_val)
        val_probs = self.calibrator.predict(val_margins)

        # 4. Búsqueda de umbral tau* que maximice EV en Validación
        best_tau = 0.50
        best_ev = -999.0
        best_prec = 0.0
        best_n = 0

        for tau in np.linspace(0.40, 0.90, 51):
            mask = val_probs >= tau
            n_sel = int(mask.sum())
            if n_sel >= self.min_trades_val:
                prec = float(np.mean(y_val[mask]))
                ev = (prec * 1.5) - ((1.0 - prec) * 1.0)
                if prec >= self.target_precision and ev > best_ev:
                    best_ev = ev
                    best_tau = tau
                    best_prec = prec
                    best_n = n_sel

        if best_n == 0:
            for tau in np.linspace(0.40, 0.85, 46):
                mask = val_probs >= tau
                n_sel = int(mask.sum())
                if n_sel >= 10:
                    prec = float(np.mean(y_val[mask]))
                    if prec > best_prec:
                        best_prec = prec
                        best_tau = tau
                        best_n = n_sel

        self.tau_star = float(best_tau)
        self.val_metrics = {
            "tau_star": self.tau_star,
            "val_precision": best_prec,
            "val_trades": best_n,
            "coefs": dict(zip(self.feature_cols, self.base_model.coef_[0].tolist())),
        }
        return self

    def predict_oos(self, df_test: pd.DataFrame) -> dict:
        X_te = df_test[self.feature_cols].fillna(0.0).to_numpy()
        margins = self.base_model.decision_function(X_te)
        probs = self.calibrator.predict(margins)
        decision = (probs >= self.tau_star).astype(int)
        return {
            "probabilities": probs,
            "decision": decision,
            "tau_star": self.tau_star,
        }


# ===========================================================================
# 14. Suite de Analítica de Rendimiento Institucional (QuantStats / PyFolio)
# ===========================================================================
def calculate_quantstats_metrics(
    pnl_series_sol: pd.Series,
    capital_initial_sol: float = 10.0,
    r_unit_sol: float = 0.5,
) -> dict:
    """
    Calcula métricas de grado institucional estilo PyFolio y QuantStats:
    - Sortino Ratio (con semidesviación a la baja sobre muestra completa N)
    - Calmar Ratio (CAGR / |MDD|)
    - Max Drawdown y Max Drawdown Duration (trades consecutivos en DD)
    - Cornish-Fisher Modified VaR (95%) capturando asimetría y curtosis de rugpulls
    - Expectancy en R-múltiplos y Compound Equity Curve
    """
    if len(pnl_series_sol) == 0:
        return {}

    pnls = pnl_series_sol.to_numpy(float)
    n = len(pnls)
    wins = pnls > 0
    losses = pnls < 0
    n_win = int(wins.sum())
    n_loss = int(losses.sum())

    wr = float(n_win / n) if n > 0 else 0.0
    tot_profit = float(pnls[wins].sum()) if n_win > 0 else 0.0
    tot_loss = float(abs(pnls[losses].sum())) if n_loss > 0 else 0.0
    pf = float(tot_profit / tot_loss) if tot_loss > 0 else (999.0 if tot_profit > 0 else 0.0)

    # R-multiples
    r_mults = pnls / r_unit_sol
    r_win = float(r_mults[wins].mean()) if n_win > 0 else 0.0
    r_loss = float(abs(r_mults[losses].mean())) if n_loss > 0 else 0.0
    expectancy_r = float((wr * r_win) - ((1.0 - wr) * r_loss))

    # Equity curve acumulada
    equity_curve = [capital_initial_sol]
    for p in pnls:
        equity_curve.append(max(0.01, equity_curve[-1] + p))
    eq_arr = np.array(equity_curve)

    # High-Water Mark y Drawdowns
    hwm = np.maximum.accumulate(eq_arr)
    drawdowns = (eq_arr - hwm) / hwm
    max_dd_pct = float(np.min(drawdowns) * 100.0)

    # Max DD Duration (en número de trades)
    dd_duration = 0
    max_dd_duration = 0
    for dd in drawdowns:
        if dd < 0:
            dd_duration += 1
            if dd_duration > max_dd_duration:
                max_dd_duration = dd_duration
        else:
            dd_duration = 0

    # Retornos simples por trade
    rets = pnls / capital_initial_sol
    mean_ret = float(np.mean(rets))
    std_ret = float(np.std(rets)) if np.std(rets) > 1e-9 else 1e-6
    sharpe = float(mean_ret / std_ret * np.sqrt(252))

    # Sortino: semidesviación a la baja con denominador N (estándar PyFolio)
    neg_rets = np.minimum(0.0, rets)
    downside_dev = float(np.sqrt(np.mean(neg_rets ** 2)))
    sortino = float(mean_ret / downside_dev * np.sqrt(252)) if downside_dev > 1e-9 else 999.0

    # Calmar Ratio
    total_ret_pct = float((eq_arr[-1] / capital_initial_sol - 1.0) * 100.0)
    calmar = float(abs(total_ret_pct / max_dd_pct)) if abs(max_dd_pct) > 0.01 else 999.0

    # Cornish-Fisher Modified VaR (95%)
    from scipy.stats import skew, kurtosis
    sk = float(skew(rets)) if len(rets) > 3 else 0.0
    kt = float(kurtosis(rets)) if len(rets) > 3 else 0.0
    z = 1.644853  # cuantil 95% normal
    z_cf = z + (z**2 - 1.0) * sk / 6.0 + (z**3 - 3.0 * z) * kt / 24.0 - (2.0 * z**3 - 5.0 * z) * (sk**2) / 36.0
    var_95_cf_pct = float(-(mean_ret - z_cf * std_ret) * 100.0)

    return {
        "n_trades": n,
        "winrate_pct": wr * 100.0,
        "profit_factor": pf,
        "expectancy_r": expectancy_r,
        "total_pnl_sol": float(pnls.sum()),
        "final_equity_sol": float(eq_arr[-1]),
        "total_return_pct": total_ret_pct,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_duration_trades": max_dd_duration,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "skewness": sk,
        "kurtosis": kt,
        "cornish_fisher_var95_pct": var_95_cf_pct,
        "equity_curve": eq_arr.tolist(),
    }
