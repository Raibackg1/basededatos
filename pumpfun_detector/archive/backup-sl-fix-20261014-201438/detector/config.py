"""Configuración central del detector de patrones."""
from dataclasses import dataclass, field


@dataclass
class Cfg:
    # --- Definiciones de "win" (todas se calculan si hay datos) ---
    tp_pct: float = 50.0          # Take profit por defecto para win_tp_sl (%)
    sl_pct: float = 30.0          # Stop loss por defecto para win_tp_sl (%)
    fut_horizons_min: tuple = (5, 15, 30)   # horizontes de retorno futuro (minutos)
    fut_threshold_pct: float = 20.0         # umbral de ganancia para win_future (%)
    mfe_window_min: float = 30.0  # ventana para MFE/MAE y grid TP/SL (minutos)

    # Si no existe tabla de trades propios, se simula una entrada virtual
    virtual_entry_s: float = 10.0  # segundos tras la creación del token

    # --- Grid de optimización de salidas TP/SL ---
    tp_grid: tuple = (20, 30, 50, 75, 100, 150, 200, 300)   # %
    sl_grid: tuple = (15, 20, 30, 40, 50, 60)               # %

    # --- Minería de patrones ---
    min_rule_support: int = 25     # mínimo de operaciones por regla
    max_tree_depth: int = 3
    n_bins: int = 5                # bins por cuantiles en análisis univariado
    min_bin_n: int = 15
    lift_min: float = 1.25         # lift mínimo para reportar una regla
    top_features_charts: int = 8
    pvalue_threshold: float = 0.05

    # --- Modelo ML ---
    cv_folds: int = 5
    random_state: int = 42

    # --- Rutas de salida ---
    out_dir: str = "resultados"

    # Curva de bonding de pump.fun: se completa con ~85 SOL netos
    curve_sol: float = 85.0

    exclude_features: tuple = field(
        default_factory=lambda: (
            "token_id", "token_name", "token_symbol", "wallet",
            "entry_time", "exit_time", "created_at",
            "graduated", "market_cap", "mc_at_entry_est",
            "exit_price", "exit_reason", "pnl_sol", "pnl_pct", "fees",
            "hold_s", "status", "virtual", "win", "win_real", "win_real_net",
            "win_tp_sl", "tpsl_timeout", "size_bucket", "future_ret_5", "future_ret_15",
            "future_ret_30", "fut_ret_5", "fut_ret_15", "fut_ret_30", "mfe_30m", "mae_30m",
            "exit_time", "exit_reason", "market_cap_final",
        )
    )
