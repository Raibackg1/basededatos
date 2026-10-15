"""Detector de patrones de trading para memecoins de pump.fun.

Las exportaciones de trading se cargan bajo demanda para que herramientas
auxiliares de solo lectura no importen bibliotecas científicas innecesarias.
"""
from importlib import import_module

__version__ = "3.0.0"
__all__ = [
    "MasterSnipeSignal",
    "MasterSignalDecision",
    "LossPostMortemLearner",
    "FailureCluster",
    "RegimeClassifier",
    "MarketRegime",
    "AlertManager",
    "AlertSeverity",
    "FileAlertSink",
    "oos_export_gate",
    "ScalingGate",
    "ScalingVerdict",
    "MomentumStallExit",
    "MomentumDecision",
    "EXIT_MOMENTUM_STALL",
    "LiquidityGuard",
    "LiquidityVerdict",
    "GMGNBridge",
]
_EXPORTS = {
    "GMGNBridge": (".gmgn_bridge", "GMGNBridge"),
    "MasterSnipeSignal": (".master_signal", "MasterSnipeSignal"),
    "MasterSignalDecision": (".master_signal", "MasterSignalDecision"),
    "LossPostMortemLearner": (".loss_learner", "LossPostMortemLearner"),
    "FailureCluster": (".loss_learner", "FailureCluster"),
    "RegimeClassifier": (".regime", "RegimeClassifier"),
    "MarketRegime": (".regime", "MarketRegime"),
    "AlertManager": (".alerts", "AlertManager"),
    "AlertSeverity": (".alerts", "AlertSeverity"),
    "FileAlertSink": (".alerts", "FileAlertSink"),
    "oos_export_gate": (".export_gate", "oos_export_gate"),
    "ScalingGate": (".scaling_gate", "ScalingGate"),
    "ScalingVerdict": (".scaling_gate", "ScalingVerdict"),
    "MomentumStallExit": (".momentum_exit", "MomentumStallExit"),
    "MomentumDecision": (".momentum_exit", "MomentumDecision"),
    "EXIT_MOMENTUM_STALL": (".momentum_exit", "EXIT_MOMENTUM_STALL"),
    "LiquidityGuard": (".liquidity_guard", "LiquidityGuard"),
    "LiquidityVerdict": (".liquidity_guard", "LiquidityVerdict"),
}


def __getattr__(name: str):
    """Carga las clases históricamente reexportadas solo al solicitarlas."""
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
