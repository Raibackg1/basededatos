#!/usr/bin/env python3
"""
Replay incremental para detectar feature leakage.
Recalcula cada feature truncando el dataframe en t y compara con el cálculo batch.
Si un valor cambia al añadir filas futuras, la feature está contaminada.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from typing import Callable, Any

import pandas as pd
import numpy as np


def compute_features_batch(df: pd.DataFrame, asof_col: str = "entry_time") -> pd.DataFrame:
    """
    Cálculo batch de features (referencia).
    DEBE ser reemplazado por la función real de features del proyecto.
    """
    # Placeholder: en producción, importar detector.features.compute_features
    # from detector.features import compute_features
    # return compute_features(df, asof_col=asof_col)
    raise NotImplementedError("Importar compute_features real del proyecto")


def compute_features_incremental(df_past: pd.DataFrame, asof_col: str = "entry_time") -> pd.DataFrame:
    """
    Cálculo incremental de features (solo datos hasta t).
    DEBE usar la MISMA lógica que compute_features_batch.
    """
    # Placeholder: en producción, importar detector.features.compute_features
    # return compute_features(df_past, asof_col=asof_col)
    raise NotImplementedError("Importar compute_features real del proyecto")


def compare_features(f_batch: pd.DataFrame, f_inc: pd.DataFrame, 
                     tol: float = 1e-10) -> dict[str, Any]:
    """
    Compara features batch vs incremental.
    
    Returns:
        Dict con diferencias detectadas
    """
    diffs = {}
    
    # Alinear índices
    common_idx = f_batch.index.intersection(f_inc.index)
    common_cols = f_batch.columns.intersection(f_inc.columns)
    
    if len(common_idx) == 0 or len(common_cols) == 0:
        return {"error": "No common indices/columns"}
    
    fb = f_batch.loc[common_idx, common_cols]
    fi = f_inc.loc[common_idx, common_cols]
    
    for col in common_cols:
        b = fb[col].values
        i = fi[col].values
        
        # Solo comparar donde ambos no son NaN
        mask = ~(pd.isna(b) | pd.isna(i))
        if not mask.any():
            continue
            
        b_valid = b[mask]
        i_valid = i[mask]
        
        # Diferencia absoluta
        abs_diff = np.abs(b_valid - i_valid)
        max_diff = np.max(abs_diff)
        mean_diff = np.mean(abs_diff)
        
        if max_diff > tol:
            diffs[col] = {
                "max_abs_diff": float(max_diff),
                "mean_abs_diff": float(mean_diff),
                "n_diff": int(np.sum(abs_diff > tol)),
                "n_total": int(mask.sum()),
            }
    
    return diffs


def replay_incremental(df: pd.DataFrame, 
                       compute_fn: Callable[[pd.DataFrame], pd.DataFrame],
                       entry_times: pd.Series,
                       step: int = 50,
                       asof_col: str = "entry_time") -> dict:
    """
    Ejecuta replay incremental.
    
    Args:
        df: DataFrame completo ordenado por entry_time
        compute_fn: Función que calcula features (batch e incremental usan la misma)
        entry_times: Serie con timestamps de entrada
        step: Cada cuántos eventos verificar
        asof_col: Nombre de columna de timestamp
    
    Returns:
        Dict con resultados de leakage
    """
    leaks = {}
    n_total = len(entry_times)
    
    print(f"[replay] Iniciando replay incremental: {n_total} eventos, step={step}")
    
    for i in range(0, n_total, step):
        t = entry_times.iloc[i]
        
        # Datos disponibles en t
        past = df[df[asof_col] <= t].copy()
        
        if len(past) < 10:
            continue
        
        # Cálculo batch (con todo el futuro)
        f_full = compute_fn(df)
        
        # Cálculo incremental (solo pasado)
        f_past = compute_fn(past)
        
        # Comparar para el evento actual
        if i < len(f_full) and i < len(f_past):
            diffs = compare_features(f_full.iloc[[i]], f_past.iloc[[i]])
            if diffs:
                leaks[f"t_{i}_({t})"] = diffs
                print(f"  [LEAK] t={i} ({t}): {list(diffs.keys())}")
    
    return {
        "total_events_checked": n_total // step,
        "leaks_found": len(leaks),
        "leaks": leaks,
    }


def main():
    ap = argparse.ArgumentParser(description="Replay incremental anti-leakage")
    ap.add_argument("--db", default=None, help="Path a custom.db (opcional, para demo)")
    ap.add_argument("--rule", default=None, help="Regla específica a testear")
    ap.add_argument("--step", type=int, default=50, help="Step entre checks")
    ap.add_argument("--asof", default="entry_time", help="Columna asof")
    args = ap.parse_args()
    
    print("[replay] ADVERTENCIA: Este script requiere importar compute_features real")
    print("[replay] Ejemplo de uso en pipeline:")
    print("  from detector.features import compute_features")
    print("  from detector.label_guard import assert_causal_label")
    print("  leaks = replay_incremental(df, compute_features, df['entry_time'])")
    
    # Demo con datos sintéticos
    if args.db is None:
        print("\n[replay] Modo demo con datos sintéticos...")
        demo_synthetic()


def demo_synthetic():
    """Demo con features sintéticas que SÍ tienen leak."""
    np.random.seed(42)
    n = 200
    df = pd.DataFrame({
        "entry_time": pd.date_range("2024-01-01", periods=n, freq="1min"),
        "price": np.cumsum(np.random.randn(n)) + 100,
        "volume": np.random.randint(100, 1000, n),
    })
    
    # Feature SIN leak: media móvil solo pasado
    df["sma_5_safe"] = df["price"].rolling(5, min_periods=1).mean()
    
    # Feature CON leak: usa datos futuros (shift -1)
    df["sma_5_leaky"] = df["price"].rolling(5, min_periods=1).mean().shift(-1)
    
    def compute_demo(d):
        return d[["sma_5_safe", "sma_5_leaky"]]
    
    leaks = replay_incremental(df, compute_demo, df["entry_time"], step=20)
    
    print(f"\n[replay] Leaks detectados: {leaks['leaks_found']}")
    for k, v in leaks["leaks"].items():
        print(f"  {k}: {list(v.keys())}")


if __name__ == "__main__":
    main()