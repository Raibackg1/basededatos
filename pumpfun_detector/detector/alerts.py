"""Gestor de alertas operacionales (drift, breaker, gate, regime).

Por que existe
--------------
`DriftMonitor`, `BranchGate` y `RegimeClassifier` producen eventos, pero cada
uno los deja en su propio contenedor. No hay un canal unico donde un operador
(o un test) pueda suscribirse y ver TODO lo que esta pasando en orden, sin
duplicados, con severidad y timestamp.

Que hace
--------
`AlertManager` es un hub pub/sub ligero y determinista:

- ``emit(kind, severity, message, payload)`` publica un `Alert`.
- ``subscribe(sink_fn)`` registra un consumidor (callable). Todos los sinks
  reciben cada alerta nueva.
- Dedup: la misma (kind, message) no se re-emite dentro de ``dedup_window_s``
  (por defecto 60 s). Evita spam cuando un drift persiste tick a tick.
- ``recent()`` devuelve las últimas N alertas (para UI o tests).
- Sin red, sin threads, sin dependencias: sink es un callable simple.

Integración: live_runner suscribe un sink que imprime/loguea; DriftMonitor y
BranchGate llaman a ``emit`` cuando cambian de estado.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    kind: str            # ej. "feature_drift", "branch_gate", "circuit_breaker", "regime"
    severity: AlertSeverity
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


class AlertManager:
    """Hub pub/sub de alertas con dedup por ventana temporal."""

    def __init__(self, dedup_window_s: float = 60.0, max_buffer: int = 500):
        self.dedup_window_s = float(dedup_window_s)
        self._buffer: Deque[Alert] = deque(maxlen=max(int(max_buffer), 1))
        self._sinks: List[Callable[[Alert], None]] = []
        # clave dedup -> ts de la ultima emision
        self._last_emitted: Dict[Tuple[str, str], float] = {}

    def subscribe(self, sink: Callable[[Alert], None]) -> None:
        """Registra un consumidor. Se llama con cada alerta nueva (no deduplicada)."""
        if not callable(sink):
            raise TypeError("sink debe ser callable")
        self._sinks.append(sink)

    def emit(
        self,
        kind: str,
        severity: AlertSeverity,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> Optional[Alert]:
        """Publica una alerta. Devuelve el Alert emitido, o None si fue deduplicada."""
        now = float(ts if ts is not None else time.time())
        key = (str(kind), str(message))
        last = self._last_emitted.get(key)
        if last is not None and (now - last) < self.dedup_window_s:
            return None
        self._last_emitted[key] = now
        alert = Alert(kind=str(kind), severity=severity, message=str(message),
                      payload=dict(payload or {}), ts=now)
        self._buffer.append(alert)
        for sink in self._sinks:
            try:
                sink(alert)
            except Exception:
                # Un sink roto no debe tumbar el pipeline
                pass
        return alert

    def recent(self, n: int = 50, kind: Optional[str] = None,
               min_severity: Optional[AlertSeverity] = None) -> List[Alert]:
        """Últimas n alertas, opcionalmente filtradas por kind y severidad mínima."""
        items = list(self._buffer)
        if kind is not None:
            items = [a for a in items if a.kind == str(kind)]
        if min_severity is not None:
            order = {AlertSeverity.INFO: 0, AlertSeverity.WARNING: 1, AlertSeverity.CRITICAL: 2}
            threshold = order[min_severity]
            items = [a for a in items if order.get(a.severity, 0) >= threshold]
        return items[-int(n):]

    def clear(self) -> None:
        self._buffer.clear()
        self._last_emitted.clear()

    @property
    def count(self) -> int:
        return len(self._buffer)

    def close_all(self) -> None:
        """Cierra todos los sinks suscritos que tengan método close().

        Evita handles de archivo abiertos al terminar el proceso (Windows
        bloquea el unlink del directorio temporal si el fh sigue abierto).
        """
        for sink in self._sinks:
            close_fn = getattr(sink, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass


class FileAlertSink:
    """Sink que persiste alertas en un archivo JSONL (una por línea).

    Crea el directorio si no existe. `flush()` fuerza escritura inmediata.
    Diseñado para suscribirse a AlertManager en rutas de producción donde
    el buffer en memoria moriría con el proceso.
    """

    def __init__(self, path: str):
        self.path = str(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def __call__(self, alert: "Alert") -> None:
        try:
            self._fh.write(json.dumps(alert.as_dict(), ensure_ascii=False) + "\n")
            self._fh.flush()
        except Exception:
            pass

    def flush(self) -> None:
        try:
            self._fh.flush()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
