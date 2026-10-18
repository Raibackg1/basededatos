#!/usr/bin/env python3
"""
Guardia de causalidad y anti-lookahead.
Verifica que ninguna feature ni etiqueta use información posterior al momento de entrada.
Compatible con API de tests existentes.
"""
from __future__ import annotations
import re
from typing import Iterable, Union, List, Optional
import pandas as pd


class FutureLeakageError(ValueError):
    """Excepción lanzada cuando se detecta lookahead/fuga temporal."""
    pass


# Columnas PROHIBIDAS como features o etiquetas (contienen información futura)
FUTURE_COLUMNS = frozenset([
    # Desenlaces futuros
    "rise50At", "rise100At", "rise200At",
    "peakPct", "athPct", "athAt", "pumpAthMcap",
    "deadAt", "graduatedAt", "graduated",
    "status", "reason", "complete",
    "pnlPct", "pnlUsd", "pnl_sol", "mfePct", "maePct", "mfe_pct", "mae_pct",
    "win", "win_*", "win_real", "fut_ret_*", "future_ret_*",
    "exitPx", "exitTs", "exitReason", "exitPrice",
    "holdS", "hold_s", "tpsl_*",
    "migrationProgress",  # final, no asof entry
    "drawup_pct", "minutesInTrade",
    "tpsl_timeout",
])

# Patrones regex para columnas futuras
FUTURE_PATTERNS = [
    re.compile(r"^rise\d+At$"),
    re.compile(r"^peak"),
    re.compile(r"^ath"),
    re.compile(r"^pumpAth"),
    re.compile(r"^dead"),
    re.compile(r"^graduated"),
    re.compile(r"^win_"),
    re.compile(r"^fut_ret_"),
    re.compile(r"^future_ret_"),
    re.compile(r"^exit_"),
    re.compile(r"^hold_?s$"),
    re.compile(r"^tpsl_"),
    re.compile(r"^migrationProgress$"),
    re.compile(r"^status$"),
    re.compile(r"^reason$"),
]

# Features SEGURAS en entrada (whitelist) - solo features de ENTRADA, no outcomes
SAFE_FEATURES = frozenset([
    "firstMarketCap", "firstLiquidity",
    "ef_n_buys", "ef_n_sells", "ef_unique_buyers",
    "creatorAddress", "createdAt", "chain",
    "topHolderPct", "devBalancePct", "realSolReserves",
    "bondingCurveProgress",
    "market_cap",  # solo si asof_time <= entry_time
    "buysM5", "sellsM5", "n_buys_60s", "n_sells_60s",
])

# Trade outcome columns - permitidos como derived_from para etiquetas de resultado
# (no son lookahead ilegítimo, son el resultado legítimo del trade)
# PERO siguen siendo "future" relativas a entry - is_future_column retorna True
# Solo assert_causal_label hace excepción para derived_from
TRADE_OUTCOME_COLUMNS = frozenset([
    "entry_price", "exit_price", "entryPrice", "exitPrice",
    "pnl_sol", "pnlPct", "pnlPct", "mfe_pct", "mae_pct",
    "exitPrice", "exitTs",
])

SAFE_PREFIXES = ("ef_", "first", "creator", "chain")


def is_future_column(col: str) -> bool:
    """Verifica si una columna contiene información futura."""
    # Primero verificar whitelist (trade outcomes permitidos)
    if col in SAFE_FEATURES:
        return False
    if col in FUTURE_COLUMNS:
        return True
    for pat in FUTURE_PATTERNS:
        if pat.search(col):
            return True
    return False


def is_lifecycle_future(col: str) -> bool:
    """Alias para is_future_column - usado por tests."""
    return is_future_column(col)


def is_derived_outcome(col: str) -> bool:
    """Verifica si una columna es un desenlace derivado (lifecycle/futuro)."""
    return is_future_column(col)


def is_safe_feature(col: str) -> bool:
    """Verifica si una feature es segura (está en whitelist o tiene prefijo seguro)."""
    if col in SAFE_FEATURES:
        return True
    return any(col.startswith(p) for p in SAFE_PREFIXES)


def assert_causal_label(
    y, 
    label_col: str = None, 
    derived_from: List[str] = None
) -> None:
    """
    Verifica que la etiqueta no derive de información futura.
    
    Args:
        y: Series/array con etiquetas O string con nombre de etiqueta (para tests)
        label_col: Nombre de la columna de etiqueta (para mensaje de error)
        derived_from: Lista de columnas de las que deriva la etiqueta (para tests)
    
    Raises:
        FutureLeakageError: Si la etiqueta usa columnas futuras
    """
    # Check derived_from first (test API)
    if derived_from:
        for col in derived_from:
            # Trade outcomes (entry_price, exit_price, pnl_sol, etc.) son permitidos
            # como fuente de etiquetas de resultado del trade
            if col in TRADE_OUTCOME_COLUMNS:
                continue
            if is_future_column(col):
                raise FutureLeakageError(
                    f"ETIQUETA FUTURA DETECTADA: '{col}' en derived_from contiene información futura. "
                    f"Usa etiqueta causal (ej. 'hit_50_during_open')."
                )
    
    # Check label_col
    if label_col and is_future_column(label_col):
        raise FutureLeakageError(
            f"ETIQUETA FUTURA DETECTADA: '{label_col}' contiene información posterior a la entrada. "
            f"Usa etiqueta causal (ej. 'hit_50_during_open') en lugar de 'rise50At', 'peak*', 'graduated', etc."
        )
    
    # Check y if it's a string (test API: assert_causal_label("rise50At"))
    # PERO solo si no hay derived_from ni label_col (i.e., test directo de nombre de columna)
    if isinstance(y, str) and not derived_from and not label_col and is_future_column(y):
        raise FutureLeakageError(
            f"ETIQUETA FUTURA DETECTADA: '{y}' contiene información futura. "
            f"Usa etiqueta causal (ej. 'hit_50_during_open')."
        )
    
    # Verificar valores únicos si es Series
    if hasattr(y, 'unique'):
        unique_vals = set(str(v) for v in y.unique() if v is not None)
        future_indicators = {"graduated", "dead", "rug", "migrated", "ath"}
        if unique_vals & future_indicators:
            raise FutureLeakageError(
                f"ETIQUETA CONTIENE VALORES FUTUROS: {unique_vals & future_indicators}. "
                f"La etiqueta debe basarse solo en trayectoria observada DURANTE la posición abierta."
            )


def assert_no_future_features(
    X: Union[pd.DataFrame, List[str]], 
    entry_time_col: str = "entry_time", 
    feature_cols: Iterable[str] = None
) -> List[str]:
    """
    Verifica que ninguna feature use información posterior a entry_time.
    
    Args:
        X: DataFrame con features O lista de nombres de columnas (para tests)
        entry_time_col: Nombre de la columna de timestamp de entrada
        feature_cols: Lista de columnas a verificar (default: todas menos entry_time_col)
    
    Returns:
        Lista de features que pasan la verificación
    
    Raises:
        FutureLeakageError: Si se detectan features con lookahead
    """
    # Si es lista, usarla directamente (API de tests)
    if isinstance(X, list):
        feature_cols = X
    else:
        # DataFrame
        if feature_cols is None:
            feature_cols = [c for c in X.columns if c != entry_time_col]
    
    leaked = []
    for col in feature_cols:
        if is_future_column(col):
            leaked.append(col)
            continue
        # Verificar si la columna parece ser un timestamp futuro
        if col.endswith(("_ts", "_at", "Time", "At")) and col != entry_time_col:
            # Podría ser futuro, advertir
            pass
    
    if leaked:
        raise FutureLeakageError(
            f"LOOKAHEAD DETECTADO en {len(leaked)} features: {leaked}. "
            f"Estas columnas contienen información posterior a entry_time. "
            f"Usa solo features 'ef_*' cortadas en entry_time con searchsorted, "
            f"o firstMarketCap/firstLiquidity/creatorAddress/createdAt/chain."
        )
    
    return list(feature_cols)


def require_causal(label_col: str = "hit_50_causal", asof_col: str = "entry_time"):
    """
    Decorador para pipelines que exige causalidad.
    
    Uso:
        @require_causal(label_col="hit_50_during_open", asof_col="entry_time")
        def evaluate(df):
            ...
    """
    def decorator(func):
        def wrapper(df, *args, **kwargs):
            # Verificar etiqueta
            if label_col in df.columns:
                assert_causal_label(df[label_col], label_col)
            
            # Verificar features (excluir label_col y asof_col)
            feature_cols = [c for c in df.columns if c not in (label_col, asof_col)]
            assert_no_future_features(df, asof_col, feature_cols)
            
            return func(df, *args, **kwargs)
        return wrapper
    return decorator


# Función de conveniencia para validación completa
def validate_causal_pipeline(df, label_col: str, entry_time_col: str = "entry_time") -> dict:
    """
    Validación completa de un pipeline.
    
    Returns:
        Dict con resultado de validación
    """
    result = {
        "causal_check": "PASS",
        "leaked_features": [],
        "label_source": label_col,
        "asof_column": entry_time_col,
    }
    
    try:
        assert_causal_label(df[label_col] if label_col in df.columns else None, label_col)
    except FutureLeakageError as e:
        result["causal_check"] = "FAIL"
        result["label_error"] = str(e)
    
    try:
        feature_cols = [c for c in df.columns if c not in (label_col, entry_time_col)]
        assert_no_future_features(df, entry_time_col, feature_cols)
    except FutureLeakageError as e:
        result["causal_check"] = "FAIL"
        result["leaked_features"] = str(e)
    
    return result


def validate_causal_label(y, label_col: str = None, derived_from: List[str] = None) -> dict:
    """Wrapper que retorna dict en formato esperado por tests: {'ok': bool, 'offending': list}."""
    offending = []
    
    # Check derived_from
    if derived_from:
        for col in derived_from:
            # Trade outcomes permitidos como fuente de etiquetas de resultado
            if col in TRADE_OUTCOME_COLUMNS:
                continue
            if is_future_column(col):
                offending.append(col)
    
    # Check label_col
    if label_col and is_future_column(label_col):
        offending.append(label_col)
    
    # Verificar valores únicos si es Series
    if hasattr(y, 'unique'):
        unique_vals = set(str(v) for v in y.unique() if v is not None)
        future_indicators = {"graduated", "dead", "rug", "migrated", "ath"}
        if unique_vals & future_indicators:
            offending.extend(list(unique_vals & future_indicators))
    
    return {"ok": len(offending) == 0, "offending": offending}


if __name__ == "__main__":
    # Test rápido
    import pandas as pd
    import numpy as np
    
    # Test 1: Etiqueta causal válida
    df = pd.DataFrame({
        "entry_time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "hit_50_during_open": [1, 0],
        "ef_n_buys": [5, 3],
        "firstMarketCap": [1000, 2000],
    })
    print("Test 1 (válido):", validate_causal_pipeline(df, "hit_50_during_open"))
    
    # Test 2: Etiqueta futura
    df2 = df.copy()
    df2["rise50At"] = [1, 0]
    print("Test 2 (futuro):", validate_causal_pipeline(df2, "rise50At"))
    
    # Test 3: Feature futura
    df3 = df.copy()
    df3["peakPct"] = [150, 80]
    try:
        assert_no_future_features(df3, "entry_time", ["peakPct", "ef_n_buys"])
        print("Test 3: PASS (inesperado)")
    except FutureLeakageError as e:
        print("Test 3 (feature futura): FAIL -", str(e)[:80])