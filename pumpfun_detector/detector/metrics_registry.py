"""Registro único de métricas con provenance. ÚNICA fuente para expected_* en código."""
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ClaimedMetric:
    key: str
    value: float
    value_display: str
    location: str
    status: str
    provenance: str  # "claimed" | "measured" | "synthetic"
    refuted_by: Optional[str] = None
    context: Optional[str] = None
    replaced_by: Optional[str] = None  # Extra field in JSON, ignore

class MetricsRegistry:
    """Carga claimed.json y expone lookups tipados. No hace I/O en hot path."""
    
    _instance = None
    _metrics: dict[str, ClaimedMetric] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance
    
    def _load(self):
        path = Path(__file__).parent.parent.parent / "metrics" / "claimed.json"
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        for m in data.get("claimed_metrics", []):
            self._metrics[m["key"]] = ClaimedMetric(**m)
    
    def get(self, key: str) -> Optional[ClaimedMetric]:
        return self._metrics.get(key)
    
    def get_value(self, key: str, default: float = 0.0) -> float:
        m = self._metrics.get(key)
        return m.value if m else default
    
    def get_display(self, key: str, default: str = "N/A") -> str:
        m = self._metrics.get(key)
        return m.value_display if m else default
    
    def all(self) -> dict[str, ClaimedMetric]:
        return dict(self._metrics)

# Instancia global (singleton)
registry = MetricsRegistry()

# Helpers para uso en master_signal.py
def claimed_wr(key: str) -> float:
    """Retorna winrate CLAIMED para una rama. Siempre etiquetado como claimed."""
    return registry.get_value(key)

def claimed_pf(key: str) -> float:
    """Retorna profit factor CLAIMED para una rama. Siempre etiquetado como claimed."""
    return registry.get_value(key)

# Keys canónicos
KEYS = {
    "branch_a": ("branch_a_expected_wr", "branch_a_expected_pf"),
    "branch_b": ("branch_b_expected_wr", "branch_b_expected_pf"),
    "branch_c": ("branch_c_expected_wr", "branch_c_expected_pf"),
    "branch_d": ("branch_d_expected_wr", "branch_d_expected_pf"),
    "branch_e": ("branch_e_expected_wr", "branch_e_expected_pf"),
}