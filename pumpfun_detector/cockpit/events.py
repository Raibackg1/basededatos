"""Bus de eventos del cockpit: ring buffer + JSONL + Server-Sent Events (solo stdlib).

Contrato (P0.3 del Plan Maestro Cockpit V2):

- Cada evento es ``{"id": int, "ts": float, "kind": str, "data": dict}``.
- ``kind`` pertenece a ``EVENT_KINDS``: ``stage``, ``decision``, ``trade_open``,
  ``trade_close``, ``evo``, ``feed``, ``error``.
- El *ring buffer* guarda los últimos ``ring_size`` eventos (500 por defecto) para
  ``GET /api/logs?since=``.
- Cada evento se persiste como una línea JSON en ``runtime/events.jsonl`` y al
  arrancar se recargan las últimas ``load_tail`` líneas, de modo que el log no
  queda vacío tras un reinicio.
- ``GET /api/events`` sirve el MISMO stream como ``text/event-stream`` (SSE) con
  ``event:``/``data:``/``id:``; el exe C# usa ``/api/logs`` con *polling*.

Ley anti-falacia: si no hay eventos, la API responde una lista vacía (nunca
rellena con eventos ficticios).
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence

# Tipos canónicos de evento (contrato cerrado: un `kind` desconocido es un bug).
EVENT_KINDS = (
    "stage",
    "decision",
    "trade_open",
    "trade_close",
    "evo",
    "feed",
    "error",
)

DEFAULT_RING_SIZE = 500
DEFAULT_LOAD_TAIL = 200


class EventBus:
    """Ring buffer thread-safe de eventos con persistencia JSONL y SSE."""

    def __init__(
        self,
        ring_size: int = DEFAULT_RING_SIZE,
        jsonl_path: Optional[str] = None,
        load_tail: int = DEFAULT_LOAD_TAIL,
    ) -> None:
        self.ring_size = max(1, int(ring_size))
        self._events: Deque[Dict[str, Any]] = deque(maxlen=self.ring_size)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._next_id = 1
        self._emitted_total = 0
        self.jsonl_path: Optional[Path] = Path(jsonl_path) if jsonl_path else None
        if self.jsonl_path is not None and int(load_tail) > 0:
            self._load_tail(int(load_tail))

    # ── Persistencia ────────────────────────────────────────────────────

    def _load_tail(self, n: int) -> None:
        """Recarga las últimas ``n`` líneas del JSONL para no arrancar vacío."""
        path = self.jsonl_path
        if path is None or not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-n:]
        except Exception:
            return
        last_id = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict) or "id" not in event:
                continue
            self._events.append(event)
            last_id = max(last_id, int(event.get("id") or 0))
        if last_id:
            self._next_id = last_id + 1
            self._emitted_total = last_id

    def _append_jsonl(self, event: Dict[str, Any]) -> None:
        if self.jsonl_path is None:
            return
        try:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self.jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            # El bus nunca rompe el loop por un fallo de disco (pero tampoco lo oculta:
            # el evento sigue en el ring buffer y es visible vía /api/logs).
            pass

    # ── API ────────────────────────────────────────────────────────────

    def emit(self, kind: str, data: Optional[Dict[str, Any]] = None, ts: Optional[float] = None) -> Dict[str, Any]:
        """Registra un evento y despierta a los suscriptores SSE."""
        kind_norm = str(kind or "").strip().lower()
        if kind_norm not in EVENT_KINDS:
            raise ValueError(f"kind de evento no canónico: {kind!r} (válidos: {EVENT_KINDS})")
        payload = dict(data or {})
        with self._cond:
            event = {
                "id": self._next_id,
                "ts": round(float(time.time() if ts is None else ts), 3),
                "kind": kind_norm,
                "data": payload,
            }
            self._next_id += 1
            self._emitted_total += 1
            self._events.append(event)
            self._append_jsonl(event)
            self._cond.notify_all()
        return event

    def since(self, since_id: int = 0, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Eventos con ``id > since_id`` (más antiguos primero)."""
        try:
            cursor = int(since_id)
        except (TypeError, ValueError):
            cursor = 0
        with self._lock:
            out = [e for e in self._events if int(e.get("id") or 0) > cursor]
        if limit is not None:
            out = out[: max(0, int(limit))]
        return out

    def wait_for(self, since_id: int, timeout: float = 15.0) -> List[Dict[str, Any]]:
        """Bloquea hasta que existan eventos nuevos o venza ``timeout`` (SSE)."""
        with self._cond:
            events = [e for e in self._events if int(e.get("id") or 0) > int(since_id)]
            if events:
                return events
            self._cond.wait(timeout=max(0.0, float(timeout)))
            return [e for e in self._events if int(e.get("id") or 0) > int(since_id)]

    def latest_id(self) -> int:
        with self._lock:
            return int(self._events[-1]["id"]) if self._events else 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            by_kind: Dict[str, int] = {}
            for e in self._events:
                by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
            return {
                "emitted_total": self._emitted_total,
                "buffered": len(self._events),
                "ring_size": self.ring_size,
                "latest_id": int(self._events[-1]["id"]) if self._events else 0,
                "by_kind": by_kind,
                "jsonl_path": str(self.jsonl_path) if self.jsonl_path else None,
            }

    # ── SSE ───────────────────────────────────────────────────────────

    @staticmethod
    def format_sse(events: Sequence[Dict[str, Any]]) -> str:
        """Formatea eventos como bloques SSE (``event:``/``data:``/``id:``)."""
        chunks: List[str] = []
        for event in events:
            chunks.append(
                "event: {kind}\ndata: {payload}\nid: {id}\n\n".format(
                    kind=event.get("kind", "message"),
                    payload=json.dumps(event.get("data") or {}, ensure_ascii=False),
                    id=int(event.get("id") or 0),
                )
            )
        return "".join(chunks)
