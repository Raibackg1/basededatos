"""Splits temporales reproducibles y validación mínima de reglas."""
from __future__ import annotations
from typing import Any, Dict
import pandas as pd


def temporal_splits(df: pd.DataFrame, time_col: str, train: float = 0.6,
                    validation: float = 0.2, test: float = 0.2, gap: int = 0) -> Dict[str, pd.DataFrame]:
    if len(df) == 0 or abs(train + validation + test - 1.0) > 1e-9:
        raise ValueError("dataset vacío o proporciones inválidas")
    ordered = df.sort_values(time_col, kind="mergesort").reset_index(drop=True)
    n = len(ordered)
    n_train, n_val = int(n * train), int(n * validation)
    train_end = n_train
    val_start = min(n, train_end + gap)
    val_end = val_start + n_val
    test_start = min(n, val_end + gap)
    return {"train": ordered.iloc[:train_end].copy(),
            "validation": ordered.iloc[val_start:val_end].copy(),
            "test": ordered.iloc[test_start:].copy()}


def rule_is_production_ready(rule: Dict[str, Any], min_support: int = 30) -> bool:
    """Solo acepta reglas estructuradas con evidencia OOS explícita."""
    if not isinstance(rule, dict):
        return False
    conds = rule.get("conditions")
    return bool(isinstance(conds, list) and conds and
                int(rule.get("support_n", 0)) >= min_support and
                bool(rule.get("oos_pass", False)) and
                rule.get("test_lift_net", 0) > 0 and
                rule.get("data_origin") in {"historical", "forward_live"})
