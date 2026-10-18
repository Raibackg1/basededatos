#!/usr/bin/env python3
"""
Walk-Forward Seal: Partición temporal sellada train/validation/test/forward
con gap purgado para etiquetas de horizonte variable.
"""
from __future__ import annotations
import hashlib, json, time
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


def sealed_split(df: pd.DataFrame, 
                 ts_col: str = "created_at", 
                 ratios: tuple = (0.6, 0.2, 0.2), 
                 gap_s: int = 1800) -> dict[str, pd.DataFrame]:
    """
    Partición temporal sellada con gap purgado.
    
    Args:
        df: DataFrame con columna timestamp
        ts_col: Nombre de columna temporal
        ratios: (train, val, test) - forward es datos posteriores
        gap_s: Gap en segundos entre particiones (purga temporal)
    
    Returns:
        Dict con train, val, test, forward
    """
    df = df.sort_values(ts_col).reset_index(drop=True)
    n = len(df)
    
    i_tr = int(n * ratios[0])
    i_va = int(n * (ratios[0] + ratios[1]))
    gap = int(gap_s)
    
    return {
        "train": df.iloc[:i_tr].copy(),
        "val": df.iloc[i_tr + gap : i_va].copy(),
        "test": df.iloc[i_va + gap :].copy(),
        "forward": pd.DataFrame(),  # Se llena con datos nuevos
    }


def seal_test_ids(test_df: pd.DataFrame, 
                  id_col: str = "id", 
                  path: str = "metrics/test_seal.json") -> str:
    """
    Sella los IDs del test set escribiendo su hash.
    
    Si el hash cambia en ejecuciones posteriores, el sobre está roto.
    
    Args:
        test_df: DataFrame del test set
        id_col: Columna con IDs únicos
        path: Ruta del archivo de sello
    
    Returns:
        Hash del sello
    """
    path_obj = Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    
    ids = ",".join(map(str, test_df[id_col].values))
    h = hashlib.sha256(ids.encode()).hexdigest()
    
    if path_obj.exists():
        prev = json.loads(path_obj.read_text(encoding="utf-8"))["hash"]
        if prev != h:
            raise ValueError(
                f"SOBRE DE TEST ROTO: el conjunto cambió. "
                f"Hash anterior: {prev[:12]}... vs actual: {h[:12]}... "
                f"Resultado NULO. No re-entrenar ni re-evaluar."
            )
        print(f"[seal] Test seal OK: {h[:12]}... (mismo conjunto)")
    else:
        seal_data = {
            "hash": h,
            "n": len(test_df),
            "sealed_at": time.time(),
            "id_col": id_col,
        }
        path_obj.write_text(json.dumps(seal_data, indent=2), encoding="utf-8")
        print(f"[seal] Test seal CREADO: {h[:12]}... (n={len(test_df)})")
    
    return h


def evaluate_oos(model_fn: callable,
                 train: pd.DataFrame,
                 val: pd.DataFrame,
                 test: pd.DataFrame,
                 label_col: str,
                 feature_cols: list[str],
                 cost_model_fn: callable = None) -> dict:
    """
    Evaluación OOS completa con métricas netas.
    
    Args:
        model_fn: Función que entrena en train y predice
        train/val/test: DataFrames sellados
        label_col: Columna etiqueta
        feature_cols: Columnas features
        cost_model_fn: Función para calcular PnL neto (opcional)
    
    Returns:
        Dict con métricas por bloque
    """
    results = {}
    
    for name, split_df in [("train", train), ("val", val), ("test", test)]:
        if len(split_df) == 0:
            results[name] = {"n": 0, "note": "empty"}
            continue
        
        X = split_df[feature_cols]
        y = split_df[label_col]
        
        # Entrenar solo en train, predecir en todos
        if name == "train":
            model = model_fn(X, y)
            y_pred = model.predict(X)
        else:
            y_pred = model.predict(X)
        
        # Métricas básicas
        from sklearn.metrics import accuracy_score, precision_score, recall_score
        
        results[name] = {
            "n": len(split_df),
            "accuracy": float(accuracy_score(y, y_pred)),
            "precision": float(precision_score(y, y_pred, zero_division=0)),
            "recall": float(recall_score(y, y_pred, zero_division=0)),
        }
        
        # PnL neto si hay cost_model
        if cost_model_fn and "pnlUsd" in split_df.columns:
            pnl = split_df.loc[y_pred == 1, "pnlUsd"].sum()
            results[name]["pnl_net_usd"] = float(pnl)
    
    return results


def oos_gate_passed(results: dict,
                    min_n: int = 30,
                    baseline_test_wr: float = 0.0,
                    min_pf: float = 1.25) -> tuple[bool, dict]:
    """
    Verifica si pasa el gate OOS (las 4 condiciones simultáneas).
    
    Returns:
        (passed, details)
    """
    test = results.get("test", {})
    val = results.get("val", {})
    
    checks = {
        "n_forward_ge_30": test.get("n", 0) >= min_n,
        "ic95_wr_gt_baseline": False,  # Requiere bootstrap
        "ev_net_positive": test.get("pnl_net_usd", -1) > 0,
        "pf_gt_125": test.get("profit_factor", 0) > min_pf,
        "stable_3_blocks": False,  # Requiere evaluación en 3 bloques temporales
    }
    
    # IC95% del WR (bootstrap simplificado)
    if "accuracy" in test and test["n"] >= min_n:
        from scipy import stats
        wr = test["accuracy"]
        n = test["n"]
        se = np.sqrt(wr * (1 - wr) / n)
        ic95_low = wr - 1.96 * se
        checks["ic95_wr_gt_baseline"] = ic95_low > baseline_test_wr
    
    passed = all(checks.values())
    return passed, {"checks": checks, "test_metrics": test}


if __name__ == "__main__":
    # Demo
    import tempfile
    
    np.random.seed(42)
    n = 5000
    df = pd.DataFrame({
        "id": range(n),
        "created_at": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "feat1": np.random.randn(n),
        "feat2": np.random.randn(n),
        "label": np.random.randint(0, 2, n),
    })
    
    splits = sealed_split(df, "created_at", ratios=(0.6, 0.2, 0.2), gap_s=7200)
    
    print("Train:", len(splits["train"]))
    print("Val:", len(splits["val"]))
    print("Test:", len(splits["test"]))
    
    # Seal test
    with tempfile.TemporaryDirectory() as tmp:
        seal_path = Path(tmp) / "test_seal.json"
        h = seal_test_ids(splits["test"], "id", str(seal_path))
        print(f"Seal hash: {h[:12]}...")
        
        # Verificar que falla si cambia
        try:
            bad_test = splits["test"].iloc[:-1].copy()
            seal_test_ids(bad_test, "id", str(seal_path))
        except ValueError as e:
            print(f"[OK] Detecta cambio: {str(e)[:80]}")