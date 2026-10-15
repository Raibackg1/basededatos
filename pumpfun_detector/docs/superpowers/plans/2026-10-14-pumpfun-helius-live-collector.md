# Pump.fun Helius Live Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture finalized Pump.fun/PumpSwap trades through Helius into a resumable, pipeline-compatible SQLite database and analyze safe snapshots after the 1,001-event threshold.

**Architecture:** A dedicated Helius RPC/WebSocket client feeds a persistent SQLite queue; a pure IDL-based decoder normalizes supported SOL/wSOL trades; a collector creates CSV exports and immutable SQLite snapshots for the existing pipeline. The service is capture-only and remains separate from the existing PumpPortal decision/execution path.

**Tech Stack:** Python 3.14; `sqlite3`, `urllib.request`, `asyncio`, `unittest`; one approved dependency, `websockets>=15,<17`, isolated in `requirements-live.txt`.

**Spec:** `docs/superpowers/specs/2026-10-14-pumpfun-live-helius-collector-design.md`

## Global Constraints

- Use `finalized` commitment for both Pump.fun and PumpSwap; do not sign or submit transactions.
- Keep the Helius API key only in `HELIUS_API_KEY`; never place it in CLI arguments, SQLite, files, exception logs, or printed URLs.
- Use a separate database at `outputs/live_pumpfun.sqlite`; do not open or alter `D:\hjh\pumpscope\db\custom.db`, demo databases, or the existing `detector/live_store.py`.
- Preserve all unrelated pre-existing working-tree changes; stage only exact files listed for a task and never use `git add -A`.
- Count an event only when it is a unique, successfully decoded, finalized SOL/wSOL trade with required fields; first pipeline run requires 1,001 such rows.
- Keep the established loader columns `token_mint`, `wallet`, `side`, `sol_amount`, `token_amount`, `price`, `block_time`, `tx_signature`; use unique key `(tx_signature, event_index)`.
- Preserve full raw Helius transaction JSON and unsupported events for audit; never fabricate missing time, amount, USD, or token metadata.
- Rate limit to 8 HTTP RPC calls/s, 5,000 transaction fetches/day, and 150,000 HTTP RPC calls per rolling 30 days; pause instead of exceeding a cap.
- Pause when the full `outputs/live_pumpfun/` tree reaches 2 GB; do not delete stored events or artifacts automatically.
- Run the pipeline on SQLite backup snapshots with `--data-origin real`; omit `--optimize-db`.
- Export UTF-8 CSV as a derivative of SQLite; SQLite remains the source of truth.
- Do not create a Windows startup task. The Desktop must remain awake and online while the collector runs.
- Declare only the user-approved `websockets` dependency in `requirements-live.txt`; install into `.work/pydeps`, not global Python.

---

## File map

| Path | Responsibility |
|---|---|
| `requirements-live.txt` | Isolated declaration of the approved WebSocket client dependency. |
| `detector/helius_live_store.py` | SQLite schema, pending-signature queue, checkpoints, trades, raw transactions, quota counters, and storage accounting. Kept separate from the existing decision/outcome `detector/live_store.py`. |
| `detector/idl/pump.json` and `detector/idl/pump_amm.json` | Pinned official IDL snapshots for Pump.fun and PumpSwap; record source commit and SHA-256 in `detector/idl/README.md`. |
| `detector/helius_live_decoder.py` | Pure offline IDL/Borsh event decoder and conversion to loader-compatible trade/token rows. |
| `detector/helius_live_rpc.py` | Helius JSON-RPC and WebSocket transport, subscriptions, rate limiter, retry policy, and secret-safe errors. |
| `detector/helius_live_collector.py` | Queue worker, recovery/backfill, state transitions, quota/storage stops, snapshot schedule, and CSV export orchestration. |
| `scripts/collect_helius_live.py` | CLI parsing, environment validation, dependency-free help path, process lifecycle, and safe startup/shutdown. |
| `tests/test_helius_live_store.py` | Temporary-SQLite schema, uniqueness, queue, checkpoint, quota, and storage tests. |
| `tests/test_helius_live_decoder.py` | Synthetic fixtures built against the pinned official IDLs; no network. |
| `tests/test_helius_live_rpc.py` | Injected HTTP/WebSocket transports, subscription payloads, rate limits, retry, and secret-redaction tests. |
| `tests/test_helius_live_collector.py` | Mocked collector flow, gap recovery, threshold, snapshots, CSV, pause/resume, and pipeline command tests. |
| `README.md` | Operator instructions for key rotation, install, start/stop, outputs, quotas, and limitations. |

Do not modify `detector/pumpportal_feed.py`, `scripts/run_live_detector.py`, `detector/live_store.py`, or their existing tests to route this read-only capture through the trading/decision daemon. `main.py`, `detector/inspector.py`, and `detector/loader.py` are inputs to the design and should remain unchanged unless a compatibility test proves a narrowly scoped defect.

## Task 1: Persistent Helius event store

**Files:**
- Create: `detector/helius_live_store.py`
- Test: `tests/test_helius_live_store.py`

**Interfaces:**
- `LiveSignatureStore(db_path: str, output_dir: str, max_storage_bytes: int = 2_000_000_000)` owns one SQLite connection and the capture schema.
- `initialize() -> None` creates the tables and indexes idempotently.
- `enqueue_signature(program_id: str, signature: str, slot: int, received_at: float) -> bool` inserts or merges a pending signature idempotently.
- `list_pending(limit: int = 100) -> list[dict]` returns pending signatures ordered by slot and insertion order.
- `save_raw_transaction(signature: str, slot: int, block_time: int | None, raw_json: str, parse_status: str, parse_error: str | None, idl_hash: str) -> None` stores an auditable transaction.
- `insert_tokens(rows: list[dict]) -> int` and `insert_trades(rows: list[dict]) -> int` use insert-ignore semantics and return newly inserted counts.
- `mark_signature_terminal(program_id: str, signature: str, slot: int, status: str) -> None` records a terminal result and advances that program's checkpoint only through processed queue rows.
- `get_checkpoint(program_id: str) -> dict | None`, `count_trades() -> int`, `record_rpc_call(kind: str, now: float) -> None`, `usage(now: float) -> dict`, `storage_bytes() -> int`, `close() -> None` expose state and limits.

- [ ] **Step 1: Add failing tests for schema compatibility and event uniqueness**

```python
import os
import tempfile
import unittest

from detector.helius_live_store import LiveSignatureStore
from detector.inspector import inspect_db


def trade_row(signature: str, event_index: int) -> dict:
    return {
        "token_mint": "MintFixture", "wallet": "WalletFixture", "side": "buy",
        "sol_amount": 0.1, "token_amount": 25.0, "price": 0.004,
        "block_time": "2026-01-01T00:00:00+00:00", "tx_signature": signature,
        "slot": 100, "program_id": "PumpFixture", "instruction_index": 0,
        "event_index": event_index, "quote_mint": "So11111111111111111111111111111111111111112",
        "sol_lamports": 100000000, "token_amount_raw": "25000000",
        "token_decimals": 6, "parser_version": "fixture-v1",
    }


class HeliusLiveStoreTests(unittest.TestCase):
    def test_schema_maps_to_existing_pipeline_roles(self):
        with tempfile.TemporaryDirectory() as td:
            store = LiveSignatureStore(os.path.join(td, "live.sqlite"), td)
            store.initialize()
            store.insert_tokens([{"token_mint": "MintFixture", "token_name": "Fixture", "token_symbol": "FIX", "created_at": "2026-01-01T00:00:00+00:00", "source": "fixture"}])
            store.insert_trades([trade_row("sig-schema", 0)])
            schema = inspect_db(store.db_path)
            self.assertEqual(schema.table_of("tokens"), "tokens")
            self.assertEqual(schema.table_of("trades"), "trades")
            self.assertEqual(schema.role("trades", "token_id"), "token_mint")
            store.close()

    def test_same_signature_can_hold_two_distinct_event_indexes(self):
        with tempfile.TemporaryDirectory() as td:
            store = LiveSignatureStore(os.path.join(td, "live.sqlite"), td)
            try:
                self.assertEqual(store.insert_trades([trade_row("sig-1", 0), trade_row("sig-1", 1)]), 2)
                self.assertEqual(store.count_trades(), 2)
                self.assertEqual(store.insert_trades([trade_row("sig-1", 1)]), 0)
            finally:
                store.close()
```

- [ ] **Step 2: Run the targeted tests and verify they fail because the store API/schema is absent**

Run in PowerShell:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p test_helius_live_store.py -v
```

Expected: import failure for `detector.helius_live_store`.

- [ ] **Step 3: Implement the SQLite schema and the smallest store API**

Create `tokens`, `trades`, `raw_transactions`, `pending_signatures`, `collector_state`, and `rpc_usage` (one durable timestamped row per HTTP RPC call). Use WAL and `busy_timeout`; add unique constraints on `trades(tx_signature,event_index)`, `raw_transactions(signature)`, and `pending_signatures(program_id,signature)`. Store amounts as loader-compatible numeric UI values plus raw integer units. Retain processed queue rows and RPC usage so checkpoints and rolling caps survive restarts; never delete raw transaction rows.

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  token_mint TEXT PRIMARY KEY, token_name TEXT, token_symbol TEXT,
  created_at TEXT, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rpc_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_rpc_usage_ts ON rpc_usage(ts);
CREATE TABLE IF NOT EXISTS trades (
  token_mint TEXT NOT NULL, wallet TEXT NOT NULL, side TEXT NOT NULL,
  sol_amount REAL NOT NULL, token_amount REAL NOT NULL, price REAL NOT NULL,
  block_time TEXT NOT NULL, tx_signature TEXT NOT NULL,
  slot INTEGER NOT NULL, program_id TEXT NOT NULL,
  instruction_index INTEGER, event_index INTEGER NOT NULL,
  quote_mint TEXT NOT NULL, sol_lamports INTEGER NOT NULL,
  token_amount_raw TEXT NOT NULL, token_decimals INTEGER NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE(tx_signature, event_index)
);
CREATE INDEX IF NOT EXISTS idx_live_trades_mint_time
  ON trades(token_mint, block_time);
"""
```

- [ ] **Step 4: Run store and existing loader tests; verify all new tests pass**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -v
```

Expected: all selected tests pass and `inspect_db` selects the canonical `trades` table.

- [ ] **Step 5: Commit only the store and its test**

```powershell
git add -- detector/helius_live_store.py tests/test_helius_live_store.py
git commit -m "feat: add persistent Helius live store" -m "Co-authored-by: Manus <dev-agent@manus.ai>"
```

## Task 2: Pinned Pump.fun/PumpSwap IDLs and pure decoder

**Files:**
- Create: `detector/idl/pump.json`, `detector/idl/pump_amm.json`, `detector/idl/README.md`
- Create: `detector/helius_live_decoder.py`
- Test: `tests/test_helius_live_decoder.py`

**Interfaces:**
- `DecodedTransaction` is a dataclass with `tokens: list[dict]`, `trades: list[dict]`, `parse_status: str`, `parse_error: str | None`.
- `decode_helius_transaction(signature: str, response: dict, *, program_ids: set[str], idls: dict[str, dict], parser_version: str) -> DecodedTransaction` is deterministic and performs no network I/O.
- Unsupported events return no fabricated rows and set an explicit parse status; raw transaction persistence remains the collector's responsibility.

- [ ] **Step 1: Pin the two official IDL source revisions before writing parser code**

Read the official `pump-fun/pump-public-docs` repository, select the current Pump.fun and PumpSwap IDL files, record the upstream commit SHA and each local file's SHA-256 in `detector/idl/README.md`, and save the files unchanged. Do not silently refresh the IDLs at runtime.

- [ ] **Step 2: Add failing decoder tests from synthetic Borsh fixtures**

Create fixtures for one buy, one sell, two events in a single signature, a failed transaction, an unsupported quote mint, a missing Helius `blockTime` with an event timestamp, and missing timestamps in both sources. Each fixture must be labeled synthetic in its docstring. Use an IDL event timestamp when present; if both sources are absent, reject the trade rather than inventing one. Generate event discriminators with the Anchor convention:

```python
def event_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"event:{name}".encode("utf-8")).digest()[:8]
```

Assert exact mint, wallet, side, raw amounts, UI amounts, slot, signature, and UTC `block_time`; assert failed/unsupported rows do not enter `trades`.

- [ ] **Step 3: Run the decoder tests to confirm the expected failures**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p test_helius_live_decoder.py -v
```

Expected: import failure for `decode_helius_transaction`.

- [ ] **Step 4: Implement IDL-driven Borsh event decoding**

Decode only `Program data:` logs whose program ID is in the two pinned IDLs. Derive event discriminators from the IDL event names; parse Borsh primitives and defined structs used by the pinned trade/create events. A schema containing an unknown primitive must produce `parse_status="unsupported"` rather than guessing. Reject transactions with `meta.err`, require the timestamp/mint/wallet/side/amount fields needed by the pipeline, accept only SOL/wSOL quote trades, and calculate `price = sol_amount / token_amount` only after validating both values are positive.

```python
@dataclass(frozen=True)
class DecodedTransaction:
    tokens: list[dict]
    trades: list[dict]
    parse_status: str
    parse_error: str | None = None

def decode_helius_transaction(signature, response, *, program_ids, idls, parser_version):
    meta = response.get("meta") or {}
    if meta.get("err") is not None:
        return DecodedTransaction([], [], "failed", str(meta["err"]))
    event_index = build_event_index(idls, program_ids)  # (program_id, discriminator) -> IDL event
    program_stack, trades, tokens = [], [], []
    for line in meta.get("logMessages") or []:
        update_program_stack(program_stack, line)  # mantiene el contexto de CPI anidado
        if not line.startswith("Program data: ") or not program_stack:
            continue
        program_id = program_stack[-1]
        if program_id not in program_ids:
            continue
        payload = base64.b64decode(line.partition(":")[2].strip(), validate=True)
        spec = event_index.get((program_id, payload[:8]))
        if spec is None:
            continue
        event = BorshReader(payload[8:]).read_fields(spec.fields)
        append_supported_rows(event, signature, response, program_id,
                             parser_version, trades, tokens)
    return DecodedTransaction(tokens, trades, "ok" if trades else "no_trade")
```

`build_event_index`, `update_program_stack`, `BorshReader`, and `append_supported_rows` are private helpers in this module. `BorshReader` supports exactly the primitive/defined field types present in the pinned trade and token-create event definitions and raises an explicit unsupported-schema error for anything else.

- [ ] **Step 5: Run decoder and store tests**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p 'test_helius_live_*.py' -v
```

Expected: all offline tests pass; malformed/unknown payloads are classified and never create a trade row.

- [ ] **Step 6: Commit only IDLs, decoder, fixtures/tests, and IDL provenance**

```powershell
git add -- detector/idl detector/helius_live_decoder.py tests/test_helius_live_decoder.py
git commit -m "feat: decode pinned Pump.fun live events" -m "Co-authored-by: Manus <dev-agent@manus.ai>"
```

## Task 3: Helius WebSocket and JSON-RPC client

**Files:**
- Create: `requirements-live.txt`
- Create: `detector/helius_live_rpc.py`
- Test: `tests/test_helius_live_rpc.py`

**Interfaces:**
- `HeliusLiveRpc(api_key: str, *, max_rps: float = 8.0, request_json: Callable | None = None, websocket_connect: Callable | None = None)` receives injectable transports for offline tests.
- `subscription_request(request_id: int, program_id: str) -> dict` creates one finalized `logsSubscribe` payload for one program ID.
- `get_transaction(signature: str) -> dict | None` returns the JSON-RPC `result` object (not the outer envelope), using finalized commitment and `maxSupportedTransactionVersion: 0`.
- `get_signatures_for_address(program_id: str, *, before: str | None = None, until: str | None = None, limit: int = 1000) -> list[dict]` validates page size and preserves signature/slot order.
- `sanitize_error(message: str, api_key: str) -> str` redacts the key and query string before any log call.

- [ ] **Step 1: Declare the approved single dependency**

Create `requirements-live.txt` containing only:

```text
websockets>=15,<17
```

- [ ] **Step 2: Add failing tests for wire payloads, pagination, limits, and redaction**

Use fake transports; do not open a socket. Verify two separate `logsSubscribe` payloads each contain one `mentions` key and `commitment="finalized"`, a `getTransaction` request uses finalized commitment, invalid page size fails, `429` honors `Retry-After`, and a fake key never appears in `sanitize_error` or captured logs.

```python
payload = subscription_request(7, "PumpProgramFixture")
self.assertEqual(payload["method"], "logsSubscribe")
self.assertEqual(payload["params"][0]["mentions"], ["PumpProgramFixture"])
self.assertEqual(payload["params"][1]["commitment"], "finalized")
```

- [ ] **Step 3: Run RPC tests and confirm they fail before implementation**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p test_helius_live_rpc.py -v
```

Expected: import failure for `detector.helius_live_rpc`.

- [ ] **Step 4: Implement transport, retry policy, and rate limiting**

Use `urllib.request` for HTTPS JSON-RPC, `asyncio.to_thread` for blocking HTTP calls, and `websockets.connect` for WSS with 60-second ping interval, finite open timeout, and bounded receive queue. Build the Helius URL from the environment key in the CLI layer; sanitize every exception before logging. Count every HTTP RPC request against the daily and rolling-30-day counters. Raise a typed pause condition when a cap is reached; do not retry authorization or exhausted-quota responses indefinitely.

```python
async def subscribe(self, program_ids):
    async with self.websocket_connect(
        self.ws_url, ping_interval=60, ping_timeout=30,
        open_timeout=15, max_queue=1000,
    ) as ws:
        for request_id, program_id in enumerate(program_ids, start=1):
            await ws.send(json.dumps(subscription_request(request_id, program_id)))
        async for message in ws:
            yield json.loads(message)

async def _http_json(self, method, params):
    await self.rate_limiter.acquire()
    return await asyncio.to_thread(self.request_json, method, params)
```

- [ ] **Step 5: Run RPC, decoder, and store tests**

`websockets` is imported lazily inside `subscribe`, so the injected fake WebSocket transport keeps the complete unit-test suite offline and importable before the live dependency is installed.

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p 'test_helius_live_*.py' -v
```

Expected: all tests pass without a network connection.

- [ ] **Step 6: Commit only the dependency declaration, RPC client, and tests**

```powershell
git add -- requirements-live.txt detector/helius_live_rpc.py tests/test_helius_live_rpc.py
git commit -m "feat: add rate-limited Helius RPC client" -m "Co-authored-by: Manus <dev-agent@manus.ai>"
```

## Task 4: Resumable capture, recovery, limits, and snapshots

**Files:**
- Create: `detector/helius_live_collector.py`
- Test: `tests/test_helius_live_collector.py`

**Interfaces:**
- `LiveCollector(store: LiveSignatureStore, rpc: HeliusLiveRpc, decoder: Callable, *, output_dir: str, pipeline_runner: Callable, clock: Callable = time.time, limits: CollectorLimits)` is the orchestration boundary.
- `async run() -> None` connects, subscribes, consumes notifications, processes persisted work, and exits only on stop/error/limit.
- `handle_signature(program_id: str, signature: str, slot: int, received_at: float) -> bool` persists an idempotent queue item before any HTTP request.
- `async recover_program(program_id: str) -> int` paginates from a saved signature; with no checkpoint it starts at the current finalized head and does not fetch all history.
- `async process_pending(limit: int = 100) -> int` fetches, decodes, persists raw plus normalized rows, and marks terminal state.
- `maybe_snapshot() -> Path | None` runs at 1,001 total rows initially; later, no more than every 24 hours and only after 1,001 new eligible rows.

- [ ] **Step 1: Add failing tests for persisted notifications and duplicate delivery**

With fake store/RPC/decoder objects, send the same signature through Pump.fun and PumpSwap notifications. Assert the transaction is fetched once, the signature exists in the persistent queue before the fetch callback executes, and two distinct `event_index` rows are retained.

- [ ] **Step 2: Run collector tests to verify the expected missing-interface failure**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p test_helius_live_collector.py -v
```

Expected: import failure for `detector.helius_live_collector`.

- [ ] **Step 3: Implement the persisted worker and checkpoint recovery**

Subscribe before draining the catch-up window so signatures arriving during recovery are queued. Count each `getSignaturesForAddress` page against the HTTP RPC ledger, fetch from newest toward the saved cursor, reverse each collected interval to enqueue oldest-first, and process each program's queue serially. Keep `raw_transactions` unique by signature and `trades` unique by signature/event. Advance a program checkpoint only after its queued event is successfully persisted or has a terminal, audited parse status.

Coalesce pending rows across programs by `signature`: `group_by_signature` is a private stable-order helper that groups pending rows by signature. Fetch and decode a transaction once, then persist all event rows and mark the corresponding Pump.fun/PumpSwap queue rows terminal. A transient HTTP error leaves every affected row pending and does not advance either checkpoint.

```python
async def process_pending(self, limit=100):
    groups = group_by_signature(self.store.list_pending(limit))
    for signature, pending_rows in groups.items():
        self.store.record_rpc_call("getTransaction", self.clock())
        response = await self.rpc.get_transaction(signature)
        if response is None:
            continue
        decoded = self.decoder(signature, response,
            program_ids={row["program_id"] for row in pending_rows},
            idls=self.idls, parser_version=self.parser_version)
        self.store.save_raw_transaction(signature, response["slot"],
            response.get("blockTime"), json.dumps(response, sort_keys=True),
            decoded.parse_status, decoded.parse_error, self.idl_hash)
        self.store.insert_tokens(decoded.tokens)
        self.store.insert_trades(decoded.trades)
        for row in pending_rows:
            self.store.mark_signature_terminal(row["program_id"], signature,
                row["slot"], decoded.parse_status)
        self.maybe_snapshot()
```

- [ ] **Step 4: Add failing quota, storage, and threshold tests**

Test exact boundaries: 1,000 trades does not invoke the pipeline; the 1,001st creates one snapshot; fewer than 1,001 new rows in the next 24 hours do not create another; 1,001 new rows after 24 hours do. At 8.01 attempted HTTP calls/s, 5,001 daily fetches, 150,001 rolling-30-day calls, or 2 GB aggregate output size, the collector pauses and preserves the queue.

- [ ] **Step 5: Implement snapshots, atomic CSV export, and hard stops**

Use `sqlite3.Connection.backup()` for a consistent snapshot under `outputs/live_pumpfun/snapshots/`; place pipeline reports under `outputs/live_pumpfun/analysis/<run_id>`, and write the rolling UTF-8 CSV to a temporary sibling then atomically replace only `trades.csv`. Before each snapshot/export, sum all regular files beneath `outputs/live_pumpfun/` (including the live database, WAL/SHM, prior snapshots, CSV, reports, and temp files) and reserve the estimated snapshot plus export size; pause if the reservation would exceed 2,000,000,000 bytes. Keep prior snapshots and reports; never purge raw events or historical reports.

```python
command = [sys.executable, str(project_root / "main.py"),
           "--db", str(snapshot_path),
           "--out", str(report_dir),
           "--data-origin", "real"]
subprocess.run(command, check=False)
```

- [ ] **Step 6: Run the mocked collector integration tests**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p 'test_helius_live_*.py' -v
```

Expected: all mocked tests pass; the pipeline runner is invoked only on threshold/cadence and never receives `--optimize-db`.

- [ ] **Step 7: Commit only the collector and its tests**

```powershell
git add -- detector/helius_live_collector.py tests/test_helius_live_collector.py
git commit -m "feat: add resumable Pump.fun live collection" -m "Co-authored-by: Manus <dev-agent@manus.ai>"
```

## Task 5: CLI, operator instructions, and end-to-end verification

**Files:**
- Create: `scripts/collect_helius_live.py`
- Modify: `README.md`
- Test: `tests/test_helius_live_collector.py`

**Interfaces:**
- `main(argv: list[str] | None = None) -> int` parses `--db`, `--out`, `--min-events`, `--max-rps`, `--max-fetches-per-day`, `--max-rpc-30d`, `--max-output-bytes`, and `--help`.
- Missing `HELIUS_API_KEY` returns an error before creating a database or opening a socket.
- `--help` never reads or displays the key.
- Ctrl+C closes WebSocket/SQLite cleanly while preserving pending work.

- [ ] **Step 1: Add failing CLI tests for missing key and safe help**

Patch the collector's client factory and environment; assert missing key exits non-zero before the factory is called, and `--help` exits zero without printing an API key. Keep the tests fully offline.

- [ ] **Step 2: Run the new CLI tests and confirm they fail**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -p test_helius_live_collector.py -v
```

Expected: the CLI entrypoint/import is absent.

- [ ] **Step 3: Implement CLI defaults and clean shutdown**

Set defaults to the spec values: database `outputs/live_pumpfun.sqlite`, output directory `outputs/live_pumpfun`, minimum 1,001 trades, 8 HTTP RPC calls/s, 5,000 fetches/day, 150,000 calls/30 days, and 2,000,000,000 bytes. Validate positive bounds before opening SQLite. Catch keyboard interruption, close the WebSocket and store in `finally`, and print only counts, slots, paths, and sanitized errors.

- [ ] **Step 4: Document secret rotation, local installation, start/stop, outputs, and constraints**

Add a README section with the exact PowerShell setup below. It must say to revoke/regenerate the key exposed in chat, set the replacement locally, keep the Desktop online, and understand that capture is read-only and stops at limits.

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m pip install --target .work\pydeps -r requirements-live.txt
$secureKey = Read-Host 'Nueva clave Helius' -AsSecureString
$env:HELIUS_API_KEY = (New-Object System.Net.NetworkCredential('', $secureKey)).Password
python scripts/collect_helius_live.py --help
python scripts/collect_helius_live.py
```

The secure prompt masks the new key; command history contains only the prompt and conversion expression, not the key. Keep the environment variable in the collector terminal only and remove it with `Remove-Item Env:HELIUS_API_KEY` after stopping the process.

- [ ] **Step 5: Run the complete offline test suite and static checks**

Run:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) '.work\pydeps') + ';' + (Get-Location).Path
python -m unittest discover -s tests -v
python -m py_compile detector/helius_live_store.py detector/helius_live_decoder.py detector/helius_live_rpc.py detector/helius_live_collector.py scripts/collect_helius_live.py
```

Expected: exit code 0 for both commands; existing and new tests pass. Confirm no files outside the listed new files and README are staged.

- [ ] **Step 6: Run a bounded real-data smoke test only after local key rotation/configuration**

Use a separate `.work` database/output directory and the command below. Start the feed for up to five minutes, stop it with Ctrl+C after the first finalized signature is fetched, and verify raw JSON persistence plus key redaction. The separate quota ledger allows at most one transaction fetch and five total HTTP RPC calls; the production database and its quota counters remain untouched. Do not run pipeline analysis unless the 1,001-event threshold is reached.

```powershell
python scripts/collect_helius_live.py --db .work\helius-live-smoke\live.sqlite --out .work\helius-live-smoke --max-fetches-per-day 1 --max-rpc-30d 5 --max-rps 1 --max-output-bytes 25000000
```

- [ ] **Step 7: Commit only the CLI, README, and tests**

```powershell
git add -- scripts/collect_helius_live.py README.md tests/test_helius_live_collector.py
git commit -m "feat: add safe Helius live collector CLI" -m "Co-authored-by: Manus <dev-agent@manus.ai>"
```

## Completion checklist

- [ ] The new SQLite schema is selected by `inspect_db` as `tokens` and `trades`, and `load_data` reads its trade rows.
- [ ] A repeated transaction is idempotent, while two event indexes under one signature remain distinct.
- [ ] Both program subscriptions use finalized commitment; reconnect recovery is checkpointed and quota-limited.
- [ ] Unsupported/non-SOL events are retained raw and excluded from SOL-denominated pipeline trades.
- [ ] The first snapshot requires 1,001 valid events; later reports obey the 24-hour/1,001-new-row rule.
- [ ] All local caps pause collection without deleting events or attempting paid upgrade/purchase.
- [ ] No key, trading action, canonical DB, demo DB, or unrelated pre-existing modification is included in the change set.
- [ ] Live service is started only after user selects an execution mode and the rotated key is configured locally.
