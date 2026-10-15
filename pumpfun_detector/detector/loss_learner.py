"""
WinLossLearner - Motor Dual de Aprendizaje Cuantitativo a partir de Ganancias y Pérdidas Reales.

Aprende bidireccionalmente:
1. ARQUETIPOS DE GANANCIA (Winners DNA):
   - ALPHA_EXPANSION_BURST: Expansión explosiva desde micro-cap inicial con baja liquidez y aceleración.
   - POSITIVE_FLOW_MOMENTUM: Media-cap con flujo comprador neto acelerado (buys > sells).
   - WHALE_BREADTH_RUNNER: Acumulación de holders y ballenas con amplia base comunitaria.
   - DECENTRALIZED_ACCUMULATION: Distribución orgánica descentralizada (topHolder <= 70%, dev <= 0.8%).

2. CLUSTERS DE FALLO (Failure Traps & Shields):
   - DEAD_LAUNCH_TIMEOUT: Micro-caps sin tracción que mueren por timeout (91.5% de pérdidas).
   - CATASTROPHIC_SLIPPAGE_RUG: Pérdidas que perforan el stop por evaporación de reservas.
   - DEV_LIQUIDATION_DUMP: Ventas agresivas del dev o balance excesivo (> 1.5%).
   - SATURATION_STALL: Estancamiento en zonas saturadas pre-migración (> 45%).
   - TOXIC_SELL_PRESSURE: Presión de venta desproporcionada en 5m (> 50 sells o sells > 1.5x buys).

3. PROBABILIDAD BAYESIANA LOG-ODDS:
   Calcula la razón de verosimilitud bayesiana P(x|WIN) / P(x|LOSS) para obtener
   la probabilidad posterior calibrada de éxito.
"""
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd


@dataclass
class WinArchetype:
    """Arquetipo estructural descubierto a partir de operaciones ganadoras reales."""
    name: str
    description: str
    trade_count: int
    win_share_pct: float
    total_gain_usd: float
    avg_gain_pct: float
    winrate: float
    key_features: dict
    recommended_action: str


@dataclass
class FailureCluster:
    """Cluster de fallo identificado a partir de operaciones perdedoras reales."""
    name: str
    description: str
    trade_count: int
    loss_share_pct: float
    total_loss_usd: float
    avg_loss_pct: float
    key_features: dict
    recommended_veto: str


class WinLossLearner:
    """
    Motor Dual de Aprendizaje Forense de Ganancias y Pérdidas.
    Aprende tanto lo que genera trades de alta rentabilidad como las trampas que drenan capital.
    """

    def __init__(self, df_trades: pd.DataFrame):
        self.df = df_trades.copy()
        pnl_col = "pnlPct" if "pnlPct" in self.df.columns else "pnl_pct"
        if "is_win" not in self.df.columns:
            self.df["is_win"] = (self.df[pnl_col] > 0).astype(int)
        self.wins = self.df[self.df["is_win"] == 1].copy()
        self.losses = self.df[self.df["is_win"] == 0].copy()
        self.win_archetypes: List[WinArchetype] = []
        self.failure_clusters: List[FailureCluster] = []

    def analyze_wins(self) -> List[WinArchetype]:
        """Extrae los arquetipos estructurales que generan ganancias reales."""
        if len(self.wins) == 0:
            return []

        pnl_pct_col = "pnlPct" if "pnlPct" in self.wins.columns else "pnl_pct"
        pnl_usd_col = "pnlUsd" if "pnlUsd" in self.wins.columns else "pnl_sol"
        mc_col = "marketCap" if "marketCap" in self.wins.columns else "market_cap"
        fmc_col = "firstMarketCap" if "firstMarketCap" in self.wins.columns else "first_market_cap"
        fliq_col = "firstLiquidity" if "firstLiquidity" in self.wins.columns else "first_liquidity"
        buys_col = "buysM5" if "buysM5" in self.wins.columns else "buys_m5"
        sells_col = "sellsM5" if "sellsM5" in self.wins.columns else "sells_m5"
        holders_col = "holderCount" if "holderCount" in self.wins.columns else "holder_count"
        net_b_col = "netBuyers1h" if "netBuyers1h" in self.wins.columns else "net_buyers_1h"
        top_h_col = "topHolderPct" if "topHolderPct" in self.wins.columns else "top_holder_pct"
        dev_b_col = "devBalancePct" if "devBalancePct" in self.wins.columns else "dev_balance_pct"

        tot_win_usd = self.wins[pnl_usd_col].sum() if pnl_usd_col in self.wins.columns else 0.0
        tot_win_mag = self.wins[pnl_pct_col].sum()

        archetypes = []

        # 1. ALPHA EXPANSION BURST (Micro-Cap Early Expansion)
        mask_exp = (
            (self.wins[fmc_col] <= 4285.0) &
            (self.wins[fliq_col] <= 1725.0) &
            (self.wins[mc_col] >= 4500.0)
        ) if (fmc_col in self.wins.columns and fliq_col in self.wins.columns and mc_col in self.wins.columns) else pd.Series(False, index=self.wins.index)

        exp_trades = self.wins[mask_exp]
        if len(exp_trades) > 0:
            all_exp = self.df[
                (self.df[fmc_col] <= 4285.0) &
                (self.df[fliq_col] <= 1725.0) &
                (self.df[mc_col] >= 4500.0)
            ]
            wr = len(exp_trades) / max(len(all_exp), 1)
            c_usd = exp_trades[pnl_usd_col].sum() if pnl_usd_col in exp_trades.columns else 0.0
            archetypes.append(WinArchetype(
                name="ALPHA_EXPANSION_BURST",
                description="Expansión explosiva desde micro-cap inicial de baja liquidez hacia adopción activa.",
                trade_count=len(exp_trades),
                win_share_pct=(exp_trades[pnl_pct_col].sum() / max(tot_win_mag, 1e-6)) * 100.0,
                total_gain_usd=c_usd,
                avg_gain_pct=float(exp_trades[pnl_pct_col].mean()),
                winrate=wr,
                key_features={"firstMarketCap": "<= $4,285", "firstLiquidity": "<= $1,725", "marketCap": ">= $4,500"},
                recommended_action="EJECUTAR ENTRADA INMEDIATA: Máxima prioridad alfa."
            ))

        # 2. POSITIVE FLOW MOMENTUM (Mid-Cap Flow & Order Acceleration)
        if mc_col in self.wins.columns and buys_col in self.wins.columns and sells_col in self.wins.columns:
            mask_flow = (
                (self.wins[mc_col] >= 5000.0) &
                (self.wins[buys_col] >= 15) &
                (self.wins[buys_col] > self.wins[sells_col])
            )
            flow_trades = self.wins[mask_flow]
            if len(flow_trades) > 0:
                all_flow = self.df[
                    (self.df[mc_col] >= 5000.0) &
                    (self.df[buys_col] >= 15) &
                    (self.df[buys_col] > self.df[sells_col])
                ]
                wr = len(flow_trades) / max(len(all_flow), 1)
                c_usd = flow_trades[pnl_usd_col].sum() if pnl_usd_col in flow_trades.columns else 0.0
                archetypes.append(WinArchetype(
                    name="POSITIVE_FLOW_MOMENTUM",
                    description="Flujo comprador neto acelerado en media-cap con presión compradora neta.",
                    trade_count=len(flow_trades),
                    win_share_pct=(flow_trades[pnl_pct_col].sum() / max(tot_win_mag, 1e-6)) * 100.0,
                    total_gain_usd=c_usd,
                    avg_gain_pct=float(flow_trades[pnl_pct_col].mean()),
                    winrate=wr,
                    key_features={"marketCap": ">= $5,000", "buysM5": ">= 15", "flow_ratio": "buys > sells"},
                    recommended_action="APROBAR CON CONFIRMACIÓN DE FLUJO: Exigir buysM5 > sellsM5."
                ))

        # 3. WHALE BREADTH RUNNER (High Holder Base & Whale Accumulation)
        if holders_col in self.wins.columns and net_b_col in self.wins.columns and mc_col in self.wins.columns:
            mask_whale = (
                (self.wins[holders_col] >= 40) &
                (self.wins[net_b_col] >= 20) &
                (self.wins[mc_col] >= 8000.0)
            )
            whale_trades = self.wins[mask_whale]
            if len(whale_trades) > 0:
                all_whale = self.df[
                    (self.df[holders_col] >= 40) &
                    (self.df[net_b_col] >= 20) &
                    (self.df[mc_col] >= 8000.0)
                ]
                wr = len(whale_trades) / max(len(all_whale), 1)
                c_usd = whale_trades[pnl_usd_col].sum() if pnl_usd_col in whale_trades.columns else 0.0
                archetypes.append(WinArchetype(
                    name="WHALE_BREADTH_RUNNER",
                    description="Acumulación de holders y ballenas con amplia base comunitaria y tracción.",
                    trade_count=len(whale_trades),
                    win_share_pct=(whale_trades[pnl_pct_col].sum() / max(tot_win_mag, 1e-6)) * 100.0,
                    total_gain_usd=c_usd,
                    avg_gain_pct=float(whale_trades[pnl_pct_col].mean()),
                    winrate=wr,
                    key_features={"holderCount": ">= 40", "netBuyers1h": ">= 20", "marketCap": ">= $8,000"},
                    recommended_action="SEGUIR TENDENCIA INSTITUCIONAL: Mantener con Trailing Stop dinámico."
                ))

        # 4. DECENTRALIZED ACCUMULATION
        if top_h_col in self.wins.columns and dev_b_col in self.wins.columns and holders_col in self.wins.columns:
            mask_dec = (
                (self.wins[top_h_col] <= 70.0) &
                (self.wins[dev_b_col] <= 0.8) &
                (self.wins[holders_col] >= 20)
            )
            dec_trades = self.wins[mask_dec]
            if len(dec_trades) > 0:
                all_dec = self.df[
                    (self.df[top_h_col] <= 70.0) &
                    (self.df[dev_b_col] <= 0.8) &
                    (self.df[holders_col] >= 20)
                ]
                wr = len(dec_trades) / max(len(all_dec), 1)
                c_usd = dec_trades[pnl_usd_col].sum() if pnl_usd_col in dec_trades.columns else 0.0
                archetypes.append(WinArchetype(
                    name="DECENTRALIZED_ACCUMULATION",
                    description="Distribución limpia y descentralizada de tokens sin concentración de creadores.",
                    trade_count=len(dec_trades),
                    win_share_pct=(dec_trades[pnl_pct_col].sum() / max(tot_win_mag, 1e-6)) * 100.0,
                    total_gain_usd=c_usd,
                    avg_gain_pct=float(dec_trades[pnl_pct_col].mean()),
                    winrate=wr,
                    key_features={"topHolderPct": "<= 70%", "devBalancePct": "<= 0.8%", "holderCount": ">= 20"},
                    recommended_action="APROBAR SNIPE SEGURO: Riesgo de rug mínimo."
                ))

        self.win_archetypes = archetypes
        return archetypes

    def analyze_failures(self) -> List[FailureCluster]:
        """Clasifica todas las pérdidas en clusters causales mutuamente explicativos."""
        if len(self.losses) == 0:
            return []

        pnl_pct_col = "pnlPct" if "pnlPct" in self.losses.columns else "pnl_pct"
        pnl_usd_col = "pnlUsd" if "pnlUsd" in self.losses.columns else "pnl_sol"
        mc_col = "marketCap" if "marketCap" in self.losses.columns else "market_cap"
        reason_col = "reason" if "reason" in self.losses.columns else "exit_reason"
        dev_b_col = "devBalancePct" if "devBalancePct" in self.losses.columns else "dev_balance_pct"
        mig_col = "migrationProgress" if "migrationProgress" in self.losses.columns else "curve_progress"
        sells_col = "sellsM5" if "sellsM5" in self.losses.columns else "sells_m5"
        buys_col = "buysM5" if "buysM5" in self.losses.columns else "buys_m5"

        tot_loss_mag = abs(self.losses[pnl_pct_col].sum())
        tot_loss_usd = abs(self.losses[pnl_usd_col].sum()) if pnl_usd_col in self.losses.columns else 0.0

        clusters = []

        # 1. DEAD LAUNCH TIMEOUT: MC <= $4,500 o TIMEOUT
        # Preferir mc_at_entry_est (entrada-segura, sobrevive al purge anti-fuga
        # P0). marketCap/market_cap pueden haber sido purgados por ser futuros;
        # exit_reason puede no existir en el esquema sintético. Sin ninguna base
        # fiable, el cluster DEAD_LAUNCH no aplica (máscara todo False).
        mc_safe = "mc_at_entry_est" if "mc_at_entry_est" in self.losses.columns else mc_col
        if mc_safe in self.losses.columns:
            mask_dead = self.losses[mc_safe] <= 4500.0
        elif reason_col in self.losses.columns:
            mask_dead = self.losses[reason_col] == "TIMEOUT"
        else:
            mask_dead = pd.Series(False, index=self.losses.index)
        dead_trades = self.losses[mask_dead]
        if len(dead_trades) > 0:
            c_loss = abs(dead_trades[pnl_pct_col].sum())
            c_usd = abs(dead_trades[pnl_usd_col].sum()) if pnl_usd_col in dead_trades.columns else 0.0
            clusters.append(FailureCluster(
                name="DEAD_LAUNCH_TIMEOUT",
                description="Lanzamientos micro-cap abandonados o con tracción nula que drenan capital en salidas lentas.",
                trade_count=len(dead_trades),
                loss_share_pct=(c_loss / max(tot_loss_mag, 1e-6)) * 100.0,
                total_loss_usd=c_usd,
                avg_loss_pct=float(dead_trades[pnl_pct_col].mean()),
                key_features={"marketCap_threshold": "<= 4500 USD", "exit_reason": "TIMEOUT"},
                recommended_veto="VETO_DEAD_LAUNCH: Exigir MarketCap > $4,500 al entrar."
            ))

        # 2. CATASTROPHIC SLIPPAGE RUG: SL <= -35%
        mask_slip = (~mask_dead) & (self.losses[pnl_pct_col] <= -35.0)
        slip_trades = self.losses[mask_slip]
        if len(slip_trades) > 0:
            c_loss = abs(slip_trades[pnl_pct_col].sum())
            c_usd = abs(slip_trades[pnl_usd_col].sum()) if pnl_usd_col in slip_trades.columns else 0.0
            clusters.append(FailureCluster(
                name="CATASTROPHIC_SLIPPAGE_RUG",
                description="Rugs y dump violentos que evaporan la liquidez de la curva antes del stop nominal.",
                trade_count=len(slip_trades),
                loss_share_pct=(c_loss / max(tot_loss_mag, 1e-6)) * 100.0,
                total_loss_usd=c_usd,
                avg_loss_pct=float(slip_trades[pnl_pct_col].mean()),
                # C7: antes era un literal de slippage escrito a mano. Ahora se
                # CALCULA la pérdida media realizada del propio clúster.
                key_features={
                    "avg_realized_loss_pct": round(float(slip_trades[pnl_pct_col].mean()), 2),
                    "exit_reason": "SL",
                },
                recommended_veto="VETO_LOW_RESERVES: Exigir realSolReserves >= 15 SOL y VPIN <= 0.35."
            ))

        # 3. DEV LIQUIDATION DUMP: devBalancePct > 1.5%
        if dev_b_col in self.losses.columns:
            mask_dev = (~mask_dead) & (~mask_slip) & (self.losses[dev_b_col] > 1.5)
            dev_trades = self.losses[mask_dev]
            if len(dev_trades) > 0:
                c_loss = abs(dev_trades[pnl_pct_col].sum())
                c_usd = abs(dev_trades[pnl_usd_col].sum()) if pnl_usd_col in dev_trades.columns else 0.0
                clusters.append(FailureCluster(
                    name="DEV_LIQUIDATION_DUMP",
                    description="El desarrollador retiene inventario significativo y lo liquida sobre los snipers.",
                    trade_count=len(dev_trades),
                    loss_share_pct=(c_loss / max(tot_loss_mag, 1e-6)) * 100.0,
                    total_loss_usd=c_usd,
                    avg_loss_pct=float(dev_trades[pnl_pct_col].mean()),
                    key_features={"dev_balance_threshold": "> 1.5%"},
                    recommended_veto="VETO_DEV_DUMP_RISK: Exigir devBalancePct <= 1.5%."
                ))

        # 4. SATURATION STALL: migrationProgress > 45% (o > 85%)
        if mig_col in self.losses.columns:
            mig_vals = self.losses[mig_col]
            cutoff = 45.0 if mig_vals.max() > 1.0 else 0.45
            mask_sat = (~mask_dead) & (~mask_slip) & (mig_vals > cutoff)
            sat_trades = self.losses[mask_sat]
            if len(sat_trades) > 0:
                c_loss = abs(sat_trades[pnl_pct_col].sum())
                c_usd = abs(sat_trades[pnl_usd_col].sum()) if pnl_usd_col in sat_trades.columns else 0.0
                clusters.append(FailureCluster(
                    name="SATURATION_STALL",
                    description="Entrada tardía en techo de curva donde la liquidez se satura y se estanca.",
                    trade_count=len(sat_trades),
                    loss_share_pct=(c_loss / max(tot_loss_mag, 1e-6)) * 100.0,
                    total_loss_usd=c_usd,
                    avg_loss_pct=float(sat_trades[pnl_pct_col].mean()),
                    key_features={"migration_progress": "> 45%"},
                    recommended_veto="VETO_SATURATION: Exigir migrationProgress <= 45% (BP-007)."
                ))

        # 5. TOXIC SELL PRESSURE: sellsM5 > 50 o sells > 1.5x buys
        if sells_col in self.losses.columns and buys_col in self.losses.columns:
            mask_sp = (~mask_dead) & (~mask_slip) & ((self.losses[sells_col] > 50) | (self.losses[sells_col] > self.losses[buys_col] * 1.5))
            sp_trades = self.losses[mask_sp]
            if len(sp_trades) > 0:
                c_loss = abs(sp_trades[pnl_pct_col].sum())
                c_usd = abs(sp_trades[pnl_usd_col].sum()) if pnl_usd_col in sp_trades.columns else 0.0
                clusters.append(FailureCluster(
                    name="TOXIC_SELL_PRESSURE",
                    description="Fuerte desbalance de presión vendedora en la ventana de 5 minutos.",
                    trade_count=len(sp_trades),
                    loss_share_pct=(c_loss / max(tot_loss_mag, 1e-6)) * 100.0,
                    total_loss_usd=c_usd,
                    avg_loss_pct=float(sp_trades[pnl_pct_col].mean()),
                    key_features={"sellsM5": "> 50 o > 1.5x buysM5"},
                    recommended_veto="VETO_SELL_PRESSURE: Exigir sellsM5 <= 45 y buysM5 >= sellsM5."
                ))

        self.failure_clusters = clusters
        return clusters

    def calculate_bayesian_log_odds(self, feature_vector: dict) -> Tuple[float, float]:
        """
        Calcula el Log-Likelihood Ratio bayesiano P(x|WIN) / P(x|LOSS)
        y la probabilidad posterior calibrada de éxito.
        """
        p_win_prior = len(self.wins) / max(len(self.df), 1)
        p_loss_prior = 1.0 - p_win_prior
        log_prior = float(np.log(max(p_win_prior, 1e-6) / max(p_loss_prior, 1e-6)))

        log_lr = 0.0

        # 1. Market Cap
        mc = float(feature_vector.get("marketCap", feature_vector.get("market_cap", 0.0)) or 0.0)
        if mc < 4500.0:
            log_lr += float(np.log(0.05 / 0.65))  # Penalización severa por micro-cap muerto
        elif 8000.0 <= mc <= 40000.0:
            log_lr += float(np.log(0.45 / 0.15))  # Evidencia fuerte a favor de media-cap activa

        # 2. Buy / Sell Flow
        buys = float(feature_vector.get("buysM5", feature_vector.get("buys_m5", 0)) or 0)
        sells = float(feature_vector.get("sellsM5", feature_vector.get("sells_m5", 0)) or 0)
        if buys >= 20 and buys > sells:
            log_lr += float(np.log(0.55 / 0.18))
        elif buys < 5 or (sells > buys * 1.5):
            log_lr += float(np.log(0.08 / 0.40))

        # 3. Dev Balance
        dev_b = float(feature_vector.get("devBalancePct", feature_vector.get("dev_balance_pct", 0.5)) or 0.5)
        if dev_b <= 0.8:
            log_lr += float(np.log(0.40 / 0.20))
        elif dev_b > 1.5:
            log_lr += float(np.log(0.05 / 0.35))

        # 4. Real SOL Reserves
        res = float(feature_vector.get("realSolReserves", feature_vector.get("sol_reserves", 30.0)) or 30.0)
        if res >= 25.0:
            log_lr += float(np.log(0.45 / 0.20))
        elif res < 15.0:
            log_lr += float(np.log(0.08 / 0.35))

        posterior_log_odds = log_prior + log_lr
        posterior_p_win = float(1.0 / (1.0 + np.exp(-posterior_log_odds)))
        return log_lr, posterior_p_win

    def summary_markdown(self) -> str:
        """Genera un reporte markdown con la descomposición forense dual (Wins & Losses)."""
        if not self.win_archetypes:
            self.analyze_wins()
        if not self.failure_clusters:
            self.analyze_failures()

        lines = [
            "### 🧬 Aprendizaje Cuantitativo Dual de Ganancias y Pérdidas",
            f"- **Operaciones Totales:** {len(self.df):,} | **Ganadoras:** {len(self.wins):,} | **Perdedoras:** {len(self.losses):,}",
            "",
            "#### 🏆 Arquetipos de Ganancia Aprendidos (Winners DNA)",
            "| Arquetipo de Ganancia | Trades | WR % | Retorno Medio % | Ganancia USD | Acción Recomendada |",
            "|---|:---:|:---:|:---:|:---:|---|",
        ]
        for wa in self.win_archetypes:
            lines.append(
                f"| **{wa.name}** | {wa.trade_count:,} | {wa.winrate*100:.1f}% | "
                f"+{wa.avg_gain_pct:.1f}% | ${wa.total_gain_usd:,.1f} | `{wa.recommended_action}` |"
            )

        lines.extend([
            "",
            "#### 🩸 Descomposición Forense de Pérdidas y Escudos de Veto",
            "| Cluster de Fallo | Trades | % Pérdida Total | Pérdida USD | Pérdida Media % | Veto Recomendado |",
            "|---|:---:|:---:|:---:|:---:|---|",
        ])
        for c in self.failure_clusters:
            lines.append(
                f"| **{c.name}** | {c.trade_count:,} | {c.loss_share_pct:.1f}% | "
                f"${c.total_loss_usd:,.1f} | {c.avg_loss_pct:.1f}% | `{c.recommended_veto}` |"
            )
        return "\n".join(lines)


# Alias canónico para compatibilidad histórica con todo el ecosistema
LossPostMortemLearner = WinLossLearner
