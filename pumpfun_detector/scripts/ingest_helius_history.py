#!/usr/bin/env python3
"""Importa swaps históricos de Pump.fun desde Helius para el pipeline SQLite.

El CSV de Dune se usa como universo de mints (requiere una columna mint/address
configurable). Helius Enhanced Transactions aporta el historial de cada mint;
solo se insertan swaps con cantidades y dirección de wallet observables. Nunca
se rellenan métricas ausentes con valores inventados.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.helius.xyz/v0/addresses"
PAGE_SIZE_MAX = 100
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _http_json(url: str, *, timeout: float = 45.0, retries: int = 5) -> Any:
    """GET JSON con backoff acotado; no registra ni imprime la URL con API key."""
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"accept": "application/json", "user-agent": "pumpfun-detector/1.0"})
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code not in RETRY_STATUSES or attempt >= retries:
                raise RuntimeError(f"Helius devolvió HTTP {exc.code}; comprueba API key, cuota y dirección.") from exc
            delay = min(2 ** attempt, 30)
        except (URLError, TimeoutError) as exc:
            if attempt >= retries:
                raise RuntimeError(f"No se pudo conectar con Helius: {exc.reason if isinstance(exc, URLError) else exc}") from exc
            delay = min(2 ** attempt, 30)
        time.sleep(delay)
    raise AssertionError("bucle de reintentos agotado")


def read_mints(csv_path: str | Path, mint_column: str = "") -> list[str]:
    """Lee mints únicos de un CSV exportado (sin intentar adivinar valores de datos)."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("El CSV de Dune debe incluir encabezados y al menos una fila de datos.")
        normalized = {name.strip().lower(): name for name in reader.fieldnames if name}
        candidates = [mint_column.lower()] if mint_column else ["mint", "token_mint", "token_address", "address", "contract_address"]
        actual = next((normalized[name] for name in candidates if name in normalized), None)
        if actual is None:
            raise ValueError("No se encontró la columna de mint. Indícala con --mint-column (p. ej. token_mint).")
        mints = list(dict.fromkeys((row.get(actual) or "").strip() for row in reader))
    mints = [mint for mint in mints if mint]
    if not mints:
        raise ValueError("El CSV de Dune no contiene mints no vacíos.")
    return mints


def iter_address_transactions(address: str, api_key: str, *, limit: int = PAGE_SIZE_MAX,
                              request_json=_http_json, pause: float = 0.0) -> Iterable[dict[str, Any]]:
    """Recorre el endpoint Enhanced Transactions por cursor before-signature."""
    if not 1 <= limit <= PAGE_SIZE_MAX:
        raise ValueError(f"limit debe estar entre 1 y {PAGE_SIZE_MAX}.")
    before = None
    seen_cursors: set[str] = set()
    while True:
        params = {"api-key": api_key, "type": "SWAP", "limit": str(limit), "sort-order": "asc"}
        if before:
            params["before-signature"] = before
        url = f"{API_ROOT}/{address}/transactions?{urlencode(params)}"
        page = request_json(url)
        if not isinstance(page, list):
            raise RuntimeError("Respuesta inesperada de Helius: se esperaba una lista de transacciones.")
        for tx in page:
            if isinstance(tx, dict):
                yield tx
        if len(page) < limit:
            break
        last = page[-1].get("signature") if isinstance(page[-1], dict) else None
        if not last or last in seen_cursors:
            raise RuntimeError("Helius no entregó un cursor de paginación nuevo; se detiene para evitar duplicados.")
        seen_cursors.add(last)
        before = last
        if pause:
            time.sleep(pause)


def _amount(obj: Any) -> float | None:
    if not isinstance(obj, dict):
        return None
    raw = obj.get("amount")
    if raw is None:
        raw = obj.get("tokenAmount")
    if isinstance(raw, dict):
        raw = raw.get("uiAmount") or raw.get("uiAmountString") or raw.get("amount")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _native_sol(obj: Any) -> float | None:
    """Los nativeInput/nativeOutput de Helius expresan SOL, no lamports."""
    value = _amount(obj)
    if value is not None:
        return value
    if isinstance(obj, dict):
        for key in ("solAmount", "amountSol"):
            try:
                n = float(obj[key])
                if n > 0:
                    return n
            except (KeyError, TypeError, ValueError):
                pass
    return None


def normalize_swap(tx: dict[str, Any], mint: str) -> dict[str, Any] | None:
    """Convierte un SWAP parseado que incluye el mint a la fila canónica del loader."""
    swap = tx.get("events", {}).get("swap", {}) if isinstance(tx.get("events"), dict) else {}
    if not isinstance(swap, dict):
        return None
    ins = [item for item in (swap.get("tokenInputs") or []) if isinstance(item, dict) and item.get("mint") == mint]
    outs = [item for item in (swap.get("tokenOutputs") or []) if isinstance(item, dict) and item.get("mint") == mint]
    # Un mint concreto debe estar en exactamente uno de los lados y tener una cantidad interpretable.
    if bool(ins) == bool(outs):
        return None
    token = ins[0] if ins else outs[0]
    token_amount = _amount(token)
    sol_amount = _native_sol(swap.get("nativeInput")) or _native_sol(swap.get("nativeOutput"))
    if not token_amount or not sol_amount:
        return None
    side = "buy" if outs else "sell"
    wallet = token.get("userAccount") or tx.get("feePayer") or ""
    signature = tx.get("signature")
    block_time = tx.get("timestamp")
    if not wallet or not signature or block_time is None:
        return None
    try:
        ts = float(block_time)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        ts /= 1000
    if ts <= 0:
        return None
    return {
        "token_mint": mint,
        "wallet": str(wallet),
        "side": side,
        "sol_amount": sol_amount,
        "token_amount": token_amount,
        "price": sol_amount / token_amount,
        "block_time": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "tx_signature": str(signature),
    }


def initialize_db(db_path: str | Path) -> None:
    """Crea tablas compatibles con detector.inspector sin tocar otras bases."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
      with db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS tokens (
                mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, created_at TEXT,
                dev_wallet TEXT, twitter TEXT, telegram TEXT, website TEXT,
                total_supply REAL, complete INTEGER
            );
            CREATE TABLE IF NOT EXISTS trades (
                token_mint TEXT NOT NULL, wallet TEXT NOT NULL, side TEXT NOT NULL,
                sol_amount REAL NOT NULL, token_amount REAL NOT NULL, price REAL NOT NULL,
                block_time TEXT NOT NULL, tx_signature TEXT NOT NULL,
                UNIQUE(token_mint, tx_signature)
            );
            CREATE INDEX IF NOT EXISTS idx_trades_mint_time ON trades(token_mint, block_time);
        """)


def ingest(mints: Iterable[str], db_path: str | Path, api_key: str, *, min_events: int = 1000,
           limit: int = PAGE_SIZE_MAX, pause: float = 0.0, request_json=_http_json) -> dict[str, int]:
    """Descarga e inserta swaps reales; valida umbral antes de habilitar análisis."""
    initialize_db(db_path)
    counts = {"mints": 0, "transactions_seen": 0, "swaps_inserted": 0, "total_events": 0}
    with closing(sqlite3.connect(db_path)) as db:
      with db:
        for mint in dict.fromkeys(mints):
            if not mint:
                continue
            counts["mints"] += 1
            db.execute("INSERT OR IGNORE INTO tokens(mint) VALUES (?)", (mint,))
            for tx in iter_address_transactions(mint, api_key, limit=limit, request_json=request_json, pause=pause):
                counts["transactions_seen"] += 1
                row = normalize_swap(tx, mint)
                if row is None:
                    continue
                cur = db.execute("""INSERT OR IGNORE INTO trades
                    (token_mint,wallet,side,sol_amount,token_amount,price,block_time,tx_signature)
                    VALUES (:token_mint,:wallet,:side,:sol_amount,:token_amount,:price,:block_time,:tx_signature)""", row)
                counts["swaps_inserted"] += cur.rowcount
            db.commit()
        counts["total_events"] = db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if counts["total_events"] < min_events:
        raise RuntimeError(f"Umbral no alcanzado: {counts['total_events']} swaps únicos persistidos < {min_events}. DB guardada para reanudar/inspeccionar: {db_path}")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Descarga historial Pump.fun desde Helius usando mints del dump CSV de Dune.")
    ap.add_argument("--dune-csv", required=True, help="CSV exportado desde Dune Analytics que contiene el universo de mints.")
    ap.add_argument("--mint-column", default="", help="Nombre de columna mint; auto-detecta mint/token_mint/token_address/address.")
    ap.add_argument("--db", default="outputs/pumpfun_helius.db", help="SQLite de salida nueva/aislada.")
    ap.add_argument("--out", default="outputs/helius_pipeline", help="Directorio de artefactos del pipeline.")
    ap.add_argument("--min-events", type=int, default=1000, help="Mínimo de swaps únicos aceptados (por defecto 1000).")
    ap.add_argument("--page-size", type=int, default=100, help="Página Enhanced Transactions (1-100).")
    ap.add_argument("--pause", type=float, default=0.15, help="Pausa entre páginas para contener la tasa de peticiones.")
    ap.add_argument("--skip-pipeline", action="store_true", help="Solo descargar y preparar SQLite.")
    args = ap.parse_args(argv)
    api_key = os.getenv("HELIUS_API_KEY")
    if not api_key:
        ap.error("Define HELIUS_API_KEY en el entorno; no se acepta la clave como argumento ni se guarda en archivos.")
    try:
        mints = read_mints(args.dune_csv, args.mint_column)
        stats = ingest(mints, args.db, api_key, min_events=args.min_events, limit=args.page_size, pause=args.pause)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error de ingesta: {exc}", file=sys.stderr)
        return 2
    print(f"Descarga lista: {stats['total_events']} swaps únicos persistidos ({stats['swaps_inserted']} nuevos en esta corrida) de {stats['mints']} mints; vistos={stats['transactions_seen']}.")
    if args.skip_pipeline:
        return 0
    if stats["total_events"] < 1000:
        print("Pipeline omitido: el requisito mínimo de 1.000 eventos no se cumplió.", file=sys.stderr)
        return 2
    import subprocess
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), "--db", str(Path(args.db).resolve()), "--out", str(Path(args.out).resolve())]
    print("Ejecutando pipeline con la base de Helius y el dump Dune como universo de mints…")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
