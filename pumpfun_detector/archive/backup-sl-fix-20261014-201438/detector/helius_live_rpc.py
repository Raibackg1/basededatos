"""Cliente Helius RPC/WebSocket para captura live de solo lectura.

El módulo mantiene el transporte HTTP inyectable para pruebas offline y carga
``websockets`` de forma diferida: instalar la dependencia live no es necesario
para usar el resto del detector ni para importar este módulo.
"""
from __future__ import annotations

import asyncio
import base64
import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any


DEFAULT_RPC_HOST = "https://mainnet.helius-rpc.com"
DEFAULT_WS_HOST = "wss://mainnet.helius-rpc.com"


class HeliusRpcError(RuntimeError):
    """Error sanitizado de transporte o respuesta JSON-RPC."""


class HeliusPause(RuntimeError):
    """Indica que el recolector debe pausarse, no reintentar indefinidamente."""


def sanitize_error(message: str, api_key: str) -> str:
    """Elimina la clave y cualquier query string de un mensaje de error."""
    text = str(message or "")
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    # Evita filtrar api-key aunque el texto proceda de una URL distinta.
    lower = text.lower()
    marker = "?api-key="
    pos = lower.find(marker)
    if pos >= 0:
        end = text.find(" ", pos)
        text = text[:pos] + ("[REDACTED]" if end < 0 else "[REDACTED]" + text[end:])
    return text.replace("api_key=", "api_key=[REDACTED]").replace("api-key=", "api-key=[REDACTED]")


def subscription_request(request_id: int, program_id: str) -> dict[str, Any]:
    """Construye una suscripción finalized para exactamente un programa."""
    if not program_id or not isinstance(program_id, str):
        raise ValueError("program_id debe ser una dirección no vacía")
    return {
        "jsonrpc": "2.0",
        "id": int(request_id),
        "method": "logsSubscribe",
        "params": [
            {"mentions": [program_id]},
            {"commitment": "finalized"},
        ],
    }


class _RateLimiter:
    def __init__(self, max_rps: float, clock: Callable[[], float] = time.monotonic):
        if max_rps <= 0:
            raise ValueError("max_rps debe ser positivo")
        self.interval = 1.0 / float(max_rps)
        self.clock = clock
        self.next_allowed = 0.0

    def wait(self) -> None:
        now = self.clock()
        delay = self.next_allowed - now
        if delay > 0:
            time.sleep(delay)
            now = self.clock()
        self.next_allowed = max(now, self.next_allowed) + self.interval


class HeliusLiveRpc:
    """Transporte RPC Helius con commitment finalized y reintentos acotados."""

    def __init__(
        self,
        api_key: str,
        *,
        max_rps: float = 8.0,
        request_json: Callable[[str, list[Any]], dict[str, Any]] | None = None,
        websocket_connect: Callable[..., Any] | None = None,
        max_retries: int = 3,
        retry_sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key es obligatorio")
        if max_retries < 0:
            raise ValueError("max_retries no puede ser negativo")
        self.api_key = api_key
        self.max_retries = max_retries
        self.request_json = request_json or self._request_json
        self.websocket_connect = websocket_connect
        self._sleep = retry_sleep or time.sleep
        self._limiter = _RateLimiter(max_rps)

    @property
    def rpc_url(self) -> str:
        return f"{DEFAULT_RPC_HOST}/?api-key={self.api_key}"

    @property
    def ws_url(self) -> str:
        return f"{DEFAULT_WS_HOST}/?api-key={self.api_key}"

    def _request_json(self, method: str, params: list[Any]) -> dict[str, Any]:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.rpc_url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            err = HeliusRpcError(f"HTTP {exc.code}" + (f" Retry-After={retry_after}" if retry_after else ""))
            setattr(err, "status", exc.code)
            setattr(err, "retry_after", retry_after)
            raise err from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise HeliusRpcError(sanitize_error(str(exc), self.api_key)) from exc

    def _call(self, method: str, params: list[Any]) -> Any:
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            try:
                envelope = self.request_json(method, params)
                if not isinstance(envelope, dict):
                    raise HeliusRpcError("respuesta RPC inválida")
                if envelope.get("error"):
                    error = envelope["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    msg = error.get("message", "RPC error") if isinstance(error, dict) else str(error)
                    err = HeliusRpcError(sanitize_error(msg, self.api_key))
                    setattr(err, "status", code)
                    raise err
                return envelope.get("result")
            except HeliusRpcError as exc:
                status = getattr(exc, "status", None)
                retry_after = getattr(exc, "retry_after", None)
                if status in (401, 403):
                    raise HeliusPause("autorización Helius rechazada") from exc
                if status in (402, 429) or status is None:
                    if attempt >= self.max_retries:
                        raise
                    try:
                        delay = float(retry_after) if retry_after is not None else min(60.0, 2.0 ** attempt)
                    except (TypeError, ValueError):
                        delay = min(60.0, 2.0 ** attempt)
                    # Si el servidor indica Retry-After exacto, respetarlo sin jitter.
                    # El jitter solo suaviza el backoff exponencial calculado localmente.
                    if retry_after is not None:
                        sleep_for = delay
                    else:
                        sleep_for = delay + random.uniform(0.0, min(0.25, delay / 4))
                    self._sleep(sleep_for)
                    continue
                raise
        raise AssertionError("bucle de reintento inalcanzable")

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        """Obtiene una transacción finalized sin devolver el envelope RPC."""
        if not signature:
            raise ValueError("signature es obligatoria")
        result = self._call("getTransaction", [signature, {
            "commitment": "finalized",
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
        }])
        return result if isinstance(result, dict) else None

    def get_signatures_for_address(
        self,
        program_id: str,
        *,
        before: str | None = None,
        until: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not program_id:
            raise ValueError("program_id es obligatorio")
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit debe estar entre 1 y 1000")
        options: dict[str, Any] = {"commitment": "finalized", "limit": int(limit)}
        if before:
            options["before"] = before
        if until:
            options["until"] = until
        result = self._call("getSignaturesForAddress", [program_id, options])
        return result if isinstance(result, list) else []

    async def subscribe(self, program_ids: Iterable[str]):
        """Suscribe dos o más programas y produce notificaciones JSON."""
        ids = list(program_ids)
        if not ids or any(not isinstance(pid, str) or not pid for pid in ids):
            raise ValueError("program_ids debe contener direcciones no vacías")
        connect = self.websocket_connect
        if connect is None:
            import websockets
            connect = websockets.connect
        async with connect(self.ws_url, ping_interval=60, ping_timeout=30,
                           open_timeout=15, max_queue=1000) as ws:
            for request_id, program_id in enumerate(ids, start=1):
                await ws.send(json.dumps(subscription_request(request_id, program_id)))
            async for message in ws:
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                yield json.loads(message)
