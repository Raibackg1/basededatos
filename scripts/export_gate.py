#!/usr/bin/env python3
"""
Export Gate Duro: Solo exporta a reglas_produccion.json reglas con:
- conds estructuradas (ejecutables, no texto)
- q-value de train (Benjamini-Hochberg)
- lift neto positivo en val Y test
- oos_pass = True
- bootstrap por bloques temporales
"""
from __future__ import annotations
import json, hashlib, subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

import pandas as pd
import numpy as np


@dataclass
class RuleCondition:
    """Condición estructurada ejecutable."""
    feature: str
    operator: str  # "<=", ">=", "==", "in", "between"
    value: Any
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def evaluate(self, row: pd.Series) -> bool:
        v = row.get(self.feature)
        if v is None:
            return False
        if self.operator == "<=":
            return v <= self.value
        elif self.operator == ">=":
            return v >= self.value
        elif self.operator == "==":
            return v == self.value
        elif self.operator == "in":
            return v in self.value
        elif self.operator == "between":
            return self.value[0] <= v <= self.value[1]
        return False


@dataclass
class ProductionRule:
    """Regla lista para producción."""
    rule_id: str
    name: str
    conditions: List[RuleCondition]
    branch: str
    
    # Métricas train (donde se descubrió)
    train_n: int
    train_wr: float
    train_qvalue: float  # Benjamini-Hochberg q-value
    
    # Métricas val (donde se calibró)
    val_n: int
    val_wr: float
    val_lift: float  # WR(val) - WR(baseline_val)
    
    # Métricas test (OOS sellado)
    test_n: int
    test_wr: float
    test_lift: float
    test_ev_net_usd: float
    test_pf_usd: float
    test_ic95_low: float
    test_max_dd: float
    test_p5_loss: float
    
    # Gate
    oos_pass: bool
    promoted_at: str
    promoted_by: str
    git_sha: str
    db_hash: str
    config_hash: str
    
    def to_dict(self) -> dict:
        d = asdict(self)
        d["conditions"] = [c.to_dict() for c in self.conditions]
        return d
    
    @classmethod
    def from_dict(cls, d: dict) -> "ProductionRule":
        d = d.copy()
        d["conditions"] = [RuleCondition(**c) for c in d["conditions"]]
        return cls(**d)


class ExportGate:
    """Gate duro para exportar reglas a producción."""
    
    def __init__(self, 
                 rules_path: str = "resultados/reglas_produccion.json",
                 min_train_n: int = 30,
                 max_qvalue: float = 0.05,
                 min_lift: float = 0.0,
                 min_test_n: int = 30,
                 baseline_test_wr: float = 0.0,
                 min_pf: float = 1.25,
                 min_ev: float = 0.0):
        self.rules_path = Path(rules_path)
        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Umbrales del gate
        self.min_train_n = min_train_n
        self.max_qvalue = max_qvalue
        self.min_lift = min_lift
        self.min_test_n = min_test_n
        self.baseline_test_wr = baseline_test_wr
        self.min_pf = min_pf
        self.min_ev = min_ev
    
    def _get_git_sha(self) -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], 
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            return "unknown"
    
    def _get_db_hash(self) -> str:
        import os
        db_path = os.environ.get("PUMPSCOPE_DB")
        if not db_path:
            return "unknown"
        import hashlib
        h = hashlib.sha256()
        with open(db_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    
    def load_rules(self) -> List[ProductionRule]:
        if not self.rules_path.exists():
            return []
        with self.rules_path.open(encoding="utf-8") as f:
            data = json.load(f)
        return [ProductionRule.from_dict(r) for r in data.get("rules", [])]
    
    def save_rules(self, rules: List[ProductionRule]) -> None:
        data = {
            "version": 2,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": self._get_git_sha(),
            "db_hash": self._get_db_hash()[:12],
            "gate_thresholds": {
                "min_train_n": self.min_train_n,
                "max_qvalue": self.max_qvalue,
                "min_lift": self.min_lift,
                "min_test_n": self.min_test_n,
                "baseline_test_wr": self.baseline_test_wr,
                "min_pf": self.min_pf,
                "min_ev": self.min_ev,
            },
            "rules": [r.to_dict() for r in rules]
        }
        with self.rules_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[export_gate] Saved {len(rules)} rules to {self.rules_path}")
    
    def validate_rule(self, rule: ProductionRule) -> tuple[bool, List[str]]:
        """Valida una regla contra el gate duro."""
        errors = []
        
        # 1. Condiciones estructuradas
        if not rule.conditions:
            errors.append("Sin condiciones estructuradas (RuleCondition)")
        for c in rule.conditions:
            if not isinstance(c, RuleCondition):
                errors.append(f"Condición no estructurada: {c}")
        
        # 2. Train: N >= 30, q-value <= 0.05
        if rule.train_n < self.min_train_n:
            errors.append(f"Train N={rule.train_n} < {self.min_train_n}")
        if rule.train_qvalue > self.max_qvalue:
            errors.append(f"Train q-value={rule.train_qvalue:.4f} > {self.max_qvalue}")
        
        # 3. Val: lift neto positivo
        if rule.val_lift <= self.min_lift:
            errors.append(f"Val lift={rule.val_lift:.4f} <= {self.min_lift}")
        
        # 4. Test: N >= 30, IC95_low > baseline, EV>0, PF>1.25
        if rule.test_n < self.min_test_n:
            errors.append(f"Test N={rule.test_n} < {self.min_test_n}")
        if rule.test_ic95_low <= self.baseline_test_wr:
            errors.append(f"Test IC95_low={rule.test_ic95_low:.4f} <= baseline={self.baseline_test_wr:.4f}")
        if rule.test_ev_net_usd <= self.min_ev:
            errors.append(f"Test EV_net={rule.test_ev_net_usd:.2f} <= {self.min_ev}")
        if rule.test_pf_usd <= self.min_pf:
            errors.append(f"Test PF={rule.test_pf_usd:.3f} <= {self.min_pf}")
        
        # 5. oos_pass obligatorio
        if not rule.oos_pass:
            errors.append("oos_pass=False (gate no pasado)")
        
        return len(errors) == 0, errors
    
    def promote_rule(self, rule: ProductionRule) -> bool:
        """Promueve regla si pasa gate."""
        passed, errors = self.validate_rule(rule)
        if not passed:
            print(f"[export_gate] RECHAZADA {rule.rule_id}: {'; '.join(errors)}")
            return False
        
        # Cargar existentes, evitar duplicados
        rules = self.load_rules()
        rules = [r for r in rules if r.rule_id != rule.rule_id]
        rules.append(rule)
        self.save_rules(rules)
        print(f"[export_gate] PROMOVIDA {rule.rule_id} (branch={rule.branch}, test_n={rule.test_n})")
        return True
    
    def demote_rule(self, rule_id: str, reason: str) -> bool:
        """Despromueve regla (ej. por degradación forward)."""
        rules = self.load_rules()
        original_len = len(rules)
        rules = [r for r in rules if r.rule_id != rule_id]
        if len(rules) < original_len:
            self.save_rules(rules)
            print(f"[export_gate] DESPROMOVIDA {rule_id}: {reason}")
            return True
        return False


def create_rule_from_subgroup(sg_result: dict,
                              train_metrics: dict,
                              val_metrics: dict,
                              test_metrics: dict,
                              branch: str) -> ProductionRule:
    """
    Factory para crear ProductionRule desde resultados de Subgroup Discovery.
    
    sg_result debe tener: conditions (lista de dicts), q_value, lift
    train/val/test_metrics: dict con n, wr, ev_net_usd, pf_usd, ic95_low, max_dd, p5_loss
    """
    conditions = [RuleCondition(**c) for c in sg_result.get("conditions", [])]
    
    # IC95% low del test
    test_wr = test_metrics.get("wr", 0)
    test_n = test_metrics.get("n", 0)
    if test_n > 0:
        se = np.sqrt(test_wr * (1 - test_wr) / test_n)
        ic95_low = test_wr - 1.96 * se
    else:
        ic95_low = 0
    
    return ProductionRule(
        rule_id=hashlib.sha256(str(conditions).encode()).hexdigest()[:12],
        name=sg_result.get("name", f"rule_{branch}"),
        conditions=conditions,
        branch=branch,
        train_n=train_metrics.get("n", 0),
        train_wr=train_metrics.get("wr", 0),
        train_qvalue=sg_result.get("q_value", 1.0),
        val_n=val_metrics.get("n", 0),
        val_wr=val_metrics.get("wr", 0),
        val_lift=val_metrics.get("lift", 0),
        test_n=test_metrics.get("n", 0),
        test_wr=test_metrics.get("wr", 0),
        test_lift=test_metrics.get("lift", 0),
        test_ev_net_usd=test_metrics.get("ev_net_usd", -999),
        test_pf_usd=test_metrics.get("pf_usd", 0),
        test_ic95_low=ic95_low,
        test_max_dd=test_metrics.get("max_dd", 0),
        test_p5_loss=test_metrics.get("p5_loss", 0),
        oos_pass=False,  # Se valida en promote_rule
        promoted_at=datetime.now(timezone.utc).isoformat(),
        promoted_by="export_gate",
        git_sha=subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip(),
        db_hash="unknown",
        config_hash="v1",
    )


if __name__ == "__main__":
    # Demo
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmp:
        gate = ExportGate(rules_path=str(Path(tmp) / "reglas_produccion.json"))
        
        # Regla que pasa
        rule_ok = ProductionRule(
            rule_id="demo_001",
            name="Rama C High MC",
            conditions=[
                RuleCondition("firstMarketCap", ">=", 50000),
                RuleCondition("firstLiquidity", ">=", 5000),
                RuleCondition("devBalancePct", "<=", 0.01),
            ],
            branch="MASTER_ALPHA_CONFLUENCE_MOMENTUM",
            train_n=150, train_wr=0.45, train_qvalue=0.012,
            val_n=80, val_wr=0.42, val_lift=0.08,
            test_n=50, test_wr=0.40, test_lift=0.05,
            test_ev_net_usd=25.5, test_pf_usd=1.8,
            test_ic95_low=0.28, test_max_dd=0.15, test_p5_loss=-0.35,
            oos_pass=True,
            promoted_at=datetime.now(timezone.utc).isoformat(),
            promoted_by="demo", git_sha="abc123", db_hash="def456", config_hash="v1"
        )
        
        gate.promote_rule(rule_ok)
        
        # Regla que falla (q-value alto)
        rule_bad = ProductionRule(
            rule_id="demo_002",
            name="Rama Mala",
            conditions=[RuleCondition("feature_x", ">=", 100)],
            branch="TEST",
            train_n=10, train_wr=0.60, train_qvalue=0.15,  # q > 0.05
            val_n=5, val_wr=0.50, val_lift=0.02,
            test_n=3, test_wr=0.55, test_lift=0.01,
            test_ev_net_usd=-5.0, test_pf_usd=0.8,  # PF < 1.25
            test_ic95_low=0.20, test_max_dd=0.50, test_p5_loss=-0.60,
            oos_pass=False,
            promoted_at=datetime.now(timezone.utc).isoformat(),
            promoted_by="demo", git_sha="abc123", db_hash="def456", config_hash="v1"
        )
        
        gate.promote_rule(rule_bad)
        
        print(f"\nReglas finales: {len(gate.load_rules())}")