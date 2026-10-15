#!/usr/bin/env python3
"""
Analyze a Solana wallet's pump.fun + Raydium Launchpad activity:
- Find pump.fun BUY/SELL events by the wallet, grouped per token mint
- Compute time from first BUY -> first SELL (seconds)
- Compute cost, proceeds, net profit (incl. fees) per token
- Optional Raydium Launchpad filter (best-effort log detection)
- Summaries + correlations, CSV export

Inputs:
  --wallet            Base58 pubkey of the dev wallet
  --limit             Max signatures to scan (default 25000)
  --days              Restrict history to last N days
  --fetch-metadata    Also fetch token name/symbol via Helius DAS (slower)
  --max-mints         Stop after analyzing this many mints (for quicker tests)
  --raydium-launchpad Restrict output to coins this wallet launched on Raydium Launchpad (best-effort log detection)
Env:
  HELIUS_API_KEY      If set, used for Enhanced TX + DAS (recommended)
  RPC_URL             JSON-RPC endpoint (fallback + log fetch)
"""

import os
import sys
import time
import math
import json
import argparse
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional, Set
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import pandas as pd
from tqdm import tqdm
from dateutil import tz
from scipy.stats import pearsonr, spearmanr
from dotenv import load_dotenv

PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"  # pump.fun program (source-cited)

WSOL_MINT = "So11111111111111111111111111111111111111112"

# ---------- Config helpers ----------

load_dotenv()  # load settings from .env if present

def now_utc() -> int:
    return int(time.time())

def days_ago_epoch(days: int) -> int:
    return now_utc() - days * 86400

def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(name)
    return val if val else default

HELIUS_API_KEY = get_env("HELIUS_API_KEY")
RPC_URL = get_env("RPC_URL", f"https://api.mainnet-beta.solana.com")  # any mainnet RPC ok

if not RPC_URL:
    print("ERROR: Please set RPC_URL (or provide HELIUS_API_KEY to derive one).", file=sys.stderr)
    sys.exit(1)

if HELIUS_API_KEY:
    HELIUS_ENHANCED_BY_ADDR = f"https://api.helius.xyz/v0/addresses/{{address}}/transactions?api-key={HELIUS_API_KEY}"
    HELIUS_TRANSACTIONS = f"https://api.helius.xyz/v0/transactions?api-key={HELIUS_API_KEY}"
    HELIUS_DAS = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
else:
    HELIUS_ENHANCED_BY_ADDR = None
    HELIUS_TRANSACTIONS = None
    HELIUS_DAS = None

_THREAD_LOCAL = threading.local()


def _get_session() -> requests.Session:
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update({"Content-Type": "application/json"})
        _THREAD_LOCAL.session = sess
    return sess


class CacheManager:
    def __init__(self, root: Optional[str], read_only: bool = False):
        self.root = Path(root).expanduser() if root else None
        self.read_only = read_only
        if self.root and not self.read_only:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Optional[Path]:
        if not self.root:
            return None
        return self.root / name

    def load_json(self, name: str) -> Optional[Any]:
        path = self._path(name)
        if not path or not path.exists():
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_json(self, name: str, data: Any) -> None:
        if self.read_only:
            return
        path = self._path(name)
        if not path:
            return
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)

class RpcError(Exception):
    pass


def sanitize_for_fs(value: str) -> str:
    """Return a filesystem-safe name derived from the supplied value."""
    allowed = []
    for ch in value.strip():
        if ch.isalnum() or ch in ("-", "_", "."):
            allowed.append(ch)
        else:
            allowed.append("_")
    name = "".join(allowed)
    return name or "wallet"


def _serialize_program_map(prog_map: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    return {sig: sorted(list(progs)) for sig, progs in prog_map.items()}

# ---------- RPC helpers ----------

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=0.5, min=0.5, max=6), reraise=True)
def rpc(method: str, params: Any) -> Any:
    """Generic JSON-RPC"""
    payload = {"jsonrpc": "2.0", "id": "x", "method": method, "params": params}
    r = _get_session().post(RPC_URL, data=json.dumps(payload), timeout=30)
    if r.status_code != 200:
        raise RpcError(f"RPC HTTP {r.status_code}: {r.text}")
    out = r.json()
    if "error" in out:
        raise RpcError(out["error"])
    return out["result"]

def get_signatures_for_address(address: str, before: Optional[str], limit: int) -> List[Dict[str, Any]]:
    params = [address, {"limit": limit}]
    if before:
        params[1]["before"] = before
    return rpc("getSignaturesForAddress", params)

def get_transaction(signature: str, max_version: int = 0) -> Dict[str, Any]:
    return rpc("getTransaction", [signature, {"maxSupportedTransactionVersion": max_version}])

# ---------- Helius Enhanced helpers ----------

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=0.5, min=0.5, max=6), reraise=True)
def helius_get_transactions_by_address(address: str, before: Optional[str], limit: int = 1000) -> List[Dict[str, Any]]:
    assert HELIUS_ENHANCED_BY_ADDR
    url = HELIUS_ENHANCED_BY_ADDR.format(address=address)
    q = {"limit": limit}
    if before:
        q["before"] = before
    r = _get_session().get(url, params=q, timeout=30)
    r.raise_for_status()
    return r.json()

@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=0.5, min=0.5, max=6), reraise=True)
def helius_get_transactions_batch(signatures: List[str]) -> List[Dict[str, Any]]:
    assert HELIUS_TRANSACTIONS
    r = _get_session().post(HELIUS_TRANSACTIONS, data=json.dumps({"transactions": signatures}), timeout=60)
    r.raise_for_status()
    return r.json()

def helius_get_asset(mint: str) -> Optional[Dict[str, Any]]:
    if not HELIUS_DAS:
        return None
    body = {"jsonrpc": "2.0", "id": "getAsset", "method": "getAsset", "params": {"id": mint}}
    r = _get_session().post(HELIUS_DAS, data=json.dumps(body), timeout=20)
    if r.status_code != 200:
        return None
    j = r.json()
    return j.get("result")

# ---------- Launchpad detection helpers ----------
# Goal: best-effort inference of coins created via Raydium Launchpad by this wallet.
# Strategy:
#   1) Scan the wallet's transactions (signatures we already fetched) with getTransaction.
#   2) Require Raydium launch-specific program IDs and/or log keywords.
#   3) Extract candidate mints from meta.postTokenBalances; intersect with the per-mint analysis set.
# Tuning via env vars:
#   RAYDIUM_LAUNCHPAD_KEYWORDS           keywords searched for in log messages (lowercase)
#   RAYDIUM_LAUNCHPAD_PROGRAM_IDS        Raydium launchpad program IDs (optional)
#   RAYDIUM_LAUNCHPAD_TRIGGER_PROGRAM_IDS additional programs whose presence hints at launchpad flows

RAYDIUM_LAUNCHPAD_KEYWORDS: List[str] = [
    "raydium launchpad",
    "acceleraytor",
    "raydium accelerator",
    "raydium ido",
    "instruction: launch",
]

# Program IDs observed in Raydium launch flows. Extend/tune as better references surface.
RAYDIUM_LAUNCHPAD_PROGRAM_IDS: Set[str] = {
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj",  # launchpad orchestrator
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",  # Raydium launch instruction router
}

# Trigger programs that frequently appear alongside launchpad activity.
RAYDIUM_LAUNCHPAD_TRIGGER_PROGRAM_IDS: Set[str] = {
    "RVKd61ztZW9uA1s4x1tChX7Pyip8nHqUrw1uuJAvV5F",  # Raydium AMM
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN",  # Raydium authority utility program
}

def _account_keys_as_strs(tx: Dict[str, Any]) -> List[str]:
    """Return all account key base58 strings from a getTransaction result."""
    msg = (tx.get("transaction", {}) or {}).get("message", {})
    keys = msg.get("accountKeys", []) or []
    out: List[str] = []
    for k in keys:
        if isinstance(k, str):
            out.append(k)
        elif isinstance(k, dict) and k.get("pubkey"):
            out.append(k["pubkey"])
    return out

def _logs_concat_lower(tx: Dict[str, Any]) -> str:
    logs = ((tx.get("meta", {}) or {}).get("logMessages", []) or [])
    return "\n".join([str(x) for x in logs]).lower()

def _extract_mints_from_tx(tx: Dict[str, Any]) -> Set[str]:
    mints: Set[str] = set()
    meta = (tx.get("meta", {}) or {})
    for b in (meta.get("postTokenBalances", []) or []):
        mint = b.get("mint")
        if mint:
            mints.add(mint)
    return mints


def _collect_program_ids(tx: Dict[str, Any]) -> Set[str]:
    programs: Set[str] = set()
    for ins in (tx.get("instructions") or []):
        pid = ins.get("programId")
        if pid:
            programs.add(pid)
        for inner in ins.get("innerInstructions") or []:
            pid_inner = inner.get("programId")
            if pid_inner:
                programs.add(pid_inner)
    return programs

def detect_launchpad_mints(wallet: str,
                           signatures: List[str],
                           candidate_mints: Optional[Set[str]] = None,
                           signature_mints: Optional[Dict[str, Set[str]]] = None,
                           signature_programs: Optional[Dict[str, Set[str]]] = None,
                           required_program_ids: Optional[Set[str]] = None,
                           trigger_program_ids: Optional[Set[str]] = None,
                           keywords: Optional[List[str]] = None,
                           tqdm_label: str = "launchpad",
                           max_rpc_workers: int = 8) -> Set[str]:
    """Generic launchpad detector driven by program IDs / log keywords.

    Fast path: use cached program IDs to accept/reject signatures without extra RPC.
    Slow path: fetch remaining transactions (optionally in parallel) to inspect logs.
    """
    required_program_ids = set(required_program_ids or [])
    trigger_program_ids = set(trigger_program_ids or [])
    keywords = [kw.lower() for kw in (keywords or [])]

    candidate_mints = set(candidate_mints or [])
    signature_mints = signature_mints or {}

    if signature_programs is None:
        program_map: Dict[str, Set[str]] = {}
    else:
        program_map = signature_programs
        for sig, progs in list(program_map.items()):
            if not isinstance(progs, set):
                program_map[sig] = set(progs)

    found: Set[str] = set()
    pending_mints: Dict[str, Set[str]] = {}
    to_fetch: List[str] = []
    to_fetch_set: Set[str] = set()

    for sig in tqdm(signatures, desc=f"Pre-scanning {tqdm_label}", leave=False):
        mints = set(signature_mints.get(sig, set()))
        if candidate_mints:
            mints = mints.intersection(candidate_mints)
        pending_mints[sig] = mints

        programs = program_map.get(sig)
        if programs is None:
            programs = set()
            program_map[sig] = programs

        has_required = bool(required_program_ids.intersection(programs)) if required_program_ids else False
        has_trigger = bool(trigger_program_ids.intersection(programs)) if trigger_program_ids else False

        if has_required or has_trigger:
            if mints:
                found.update(mints)
                continue
            # Need the transaction to determine mints; defer to RPC fetch
            if sig not in to_fetch_set:
                to_fetch_set.add(sig)
                to_fetch.append(sig)
            continue

        if not keywords:
            continue

        if sig not in to_fetch_set:
            to_fetch_set.add(sig)
            to_fetch.append(sig)

    if not to_fetch:
        return found

    workers = max(1, min(max_rpc_workers, len(to_fetch)))
    fetch_desc = f"{tqdm_label} RPC"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(get_transaction, sig): sig for sig in to_fetch}
        for fut in tqdm(as_completed(future_map), total=len(future_map), desc=fetch_desc, leave=False):
            sig = future_map[fut]
            try:
                tx = fut.result()
            except Exception:
                print(f"ERROR: failed to fetch signature {sig} during {tqdm_label} detection", file=sys.stderr)
                traceback.print_exc()
                continue
            if not tx:
                print(f"WARNING: getTransaction returned null for {sig}; skipping", file=sys.stderr)
                continue

            programs = program_map.setdefault(sig, set())
            programs.update(_collect_program_ids(tx))

            account_keys = set(_account_keys_as_strs(tx))
            key_union = programs.union(account_keys)
            has_required = bool(required_program_ids.intersection(key_union)) if required_program_ids else False
            has_trigger = bool(trigger_program_ids.intersection(key_union)) if trigger_program_ids else False
            logs_text = _logs_concat_lower(tx) if keywords else ""
            has_keyword = any(kw in logs_text for kw in keywords) if keywords else False

            if not (has_required or has_trigger or has_keyword):
                continue

            mints = pending_mints.get(sig)
            if not mints:
                mints = _extract_mints_from_tx(tx)
                if candidate_mints:
                    mints = mints.intersection(candidate_mints)
            if not mints:
                continue
            found.update(mints)

    return found

# ---------- Core logic ----------

def tx_uses_program(enhanced_tx: Dict[str, Any], program_ids: Set[str]) -> bool:
    """Check top-level + inner instructions for any of the specified programs."""
    if not program_ids:
        return False
    target = set(program_ids)
    for ins in enhanced_tx.get("instructions", []):
        pid = ins.get("programId")
        if pid in target:
            return True
        for inner in ins.get("innerInstructions", []) or []:
            if inner.get("programId") in target:
                return True
    return False


def is_pump_tx(enhanced_tx: Dict[str, Any]) -> bool:
    """Check if any instruction (or inner) uses the pump.fun program."""
    return tx_uses_program(enhanced_tx, {PUMP_FUN_PROGRAM_ID})

def native_change_for_wallet(enhanced_tx: Dict[str, Any], wallet: str) -> int:
    """Lamports delta for wallet in this tx (includes fees)."""
    for acct in enhanced_tx.get("accountData", []):
        if acct.get("account") == wallet:
            return int(acct.get("nativeBalanceChange") or 0)
    return 0

def token_changes_for_wallet(enhanced_tx: Dict[str, Any], wallet: str) -> List[Dict[str, Any]]:
    out = []
    for acct in enhanced_tx.get("accountData", []):
        for tbc in acct.get("tokenBalanceChanges", []) or []:
            if tbc.get("userAccount") == wallet:
                out.append(tbc)
    return out

def classify_event(sol_delta_lamports: int, token_delta_raw: int) -> Optional[str]:
    """
    BUY: token +, SOL -, SELL: token -, SOL +
    (Return None if inconsistent)
    """
    if token_delta_raw > 0 and sol_delta_lamports < 0:
        return "BUY"
    if token_delta_raw < 0 and sol_delta_lamports > 0:
        return "SELL"
    # mixed signals (skip)
    return None

def lamports_to_sol(lamports: int) -> float:
    return lamports / 1e9

def parse_ts(e: Dict[str, Any]) -> int:
    return int(e.get("timestamp") or 0)

def analyze_wallet(wallet: str,
                   limit: int = 25000,
                   days: Optional[int] = None,
                   fetch_metadata: bool = False,
                   max_mints: Optional[int] = None,
                   raydium_launchpad_only: bool = False,
                   include_raydium_launchpad: bool = False,
                   cache_dir: Optional[str] = None,
                   resume_cache: Optional[str] = None,
                   helius_workers: int = 1) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Returns:
        df (per-mint summary), stats dict, and metadata (e.g., per-mint decimals)
    """
    cache = CacheManager(cache_dir) if cache_dir else None
    resume = CacheManager(resume_cache, read_only=True) if resume_cache else None

    if cache and not resume:
        print(f"Caching decoded data under {cache.root}")
    if resume:
        print(f"Resuming from cache {resume.root}")

    if raydium_launchpad_only or include_raydium_launchpad:
        program_filter_ids: Optional[Set[str]] = None
    else:
        program_filter_ids = {PUMP_FUN_PROGRAM_ID}

    token_events: List[Dict[str, Any]] = []
    sig_list: List[str] = []
    signature_program_ids: Dict[str, Set[str]] = {}
    raydium_launchpad_mints: Set[str] = set()
    mint_decimals: Dict[str, int] = {}

    if resume:
        cached_sig_list = resume.load_json("signatures.json")
        cached_events = resume.load_json("pump_events.json")
        meta = resume.load_json("cache_meta.json")
        if cached_sig_list is None or cached_events is None or meta is None:
            print("ERROR: resume cache missing required files (signatures.json, pump_events.json, cache_meta.json).", file=sys.stderr)
            sys.exit(3)
        if not meta.get("complete"):
            print("ERROR: resume cache not marked complete; re-run without --resume-cache or rebuild the cache.", file=sys.stderr)
            sys.exit(4)
        sig_list = cached_sig_list
        token_events = cached_events
        expected = meta.get("signature_count")
        if expected and expected != len(sig_list):
            print("ERROR: resume cache signature count mismatch.", file=sys.stderr)
            sys.exit(5)
        print(f"Loaded {len(sig_list)} signatures and {len(token_events)} cached token events.")

        cached_programs = resume.load_json("signature_programs.json")
        if cached_programs:
            signature_program_ids = {sig: set(progs) for sig, progs in cached_programs.items()}
        else:
            signature_program_ids = {}
            cache_root = resume.root
            if cache_root:
                batch_paths = sorted(Path(cache_root).glob("helius_batch_*.json"))
                for batch_path in tqdm(batch_paths, desc="Rebuilding program cache", leave=False):
                    try:
                        with batch_path.open("r", encoding="utf-8") as fh:
                            txs = json.load(fh)
                    except Exception:
                        continue
                    for tx in txs:
                        sig = tx.get("signature")
                        if not sig:
                            continue
                        signature_program_ids.setdefault(sig, set()).update(_collect_program_ids(tx))
            if cache and signature_program_ids:
                cache.save_json("signature_programs.json", _serialize_program_map(signature_program_ids))
                meta_update = resume.load_json("cache_meta.json") or {}
                meta_update["program_map_count"] = len(signature_program_ids)
                meta_update["updated"] = datetime.now(timezone.utc).isoformat()
                cache.save_json("cache_meta.json", meta_update)
        for evt in token_events:
            if "is_pumpfun_tx" not in evt:
                sig = evt.get("signature")
                programs = signature_program_ids.get(sig) or set()
                evt["is_pumpfun_tx"] = PUMP_FUN_PROGRAM_ID in programs
        for evt in token_events:
            mint = evt.get("mint")
            if not mint or mint in mint_decimals:
                continue
            dec_val = evt.get("decimals")
            try:
                mint_decimals[mint] = int(dec_val if dec_val is not None else 0)
            except (TypeError, ValueError):
                mint_decimals[mint] = 0
    else:
        # Step 1: collect signatures (fast path: use getSignaturesForAddress)
        # We’ll chunk them into 100s and call Helius /v0/transactions for decoded data.
        print("Fetching signatures…")
        signatures: List[Dict[str, Any]] = []
        before_sig = None
        fetched = 0
        cutoff = days_ago_epoch(days) if days else None

        while fetched < limit:
            batch = get_signatures_for_address(wallet, before_sig, min(1000, limit - fetched))
            if not batch:
                break
            signatures.extend(batch)
            fetched += len(batch)
            before_sig = batch[-1]["signature"]
            # Stop early if time cutoff
            if cutoff and (batch[-1].get("blockTime") and batch[-1]["blockTime"] < cutoff):
                break

        if not signatures:
            raise SystemExit("No signatures found for this wallet in the requested window.")

        # Keep latest->oldest order
        sig_list = [s["signature"] for s in signatures]
        print(f"Total signatures collected: {len(sig_list)}")
        if cache:
            cache.save_json("signatures.json", sig_list)
            cache.save_json(
                "cache_meta.json",
                {
                    "complete": False,
                    "signature_count": len(sig_list),
                    "pump_events_count": 0,
                    "program_map_count": 0,
                    "updated": datetime.now(timezone.utc).isoformat(),
                },
            )

        # Step 2: decode with Helius (batch)
        if not HELIUS_API_KEY:
            print("WARNING: No HELIUS_API_KEY set. Falling back to raw RPC-only mode is not implemented here; please set a Helius key for enhanced decoding.")
            sys.exit(2)

        print("Decoding transactions via Helius Enhanced…")
        helius_workers = max(1, int(helius_workers or 1))
        batch_size = 100
        total_batches = math.ceil(len(sig_list) / batch_size)

        with tqdm(total=total_batches, desc="Decoding", leave=False) as progress:

            def process_chunk_txs(batch_txs: List[Dict[str, Any]]) -> None:
                chunk_events: List[Dict[str, Any]] = []
                for tx in batch_txs:
                    sig = tx.get("signature")
                    if sig:
                        signature_program_ids.setdefault(sig, set()).update(_collect_program_ids(tx))
                    if program_filter_ids and not tx_uses_program(tx, program_filter_ids):
                        continue

                    ts = parse_ts(tx)
                    sol_delta = native_change_for_wallet(tx, wallet)
                    is_pumpfun_tx = tx_uses_program(tx, {PUMP_FUN_PROGRAM_ID})

                    changes = token_changes_for_wallet(tx, wallet)
                    for c in changes:
                        mint = c.get("mint")
                        if not mint or mint == WSOL_MINT:
                            continue
                        raw_info = c.get("rawTokenAmount") or {}
                        token_amt_str = raw_info.get("tokenAmount") or "0"
                        try:
                            token_delta_raw = int(token_amt_str)
                        except ValueError:
                            token_delta_raw = int(float(token_amt_str))
                        if token_delta_raw == 0:
                            continue
                        decimals = int(raw_info.get("decimals") or 0)
                        side = classify_event(sol_delta, token_delta_raw)
                        if not side:
                            continue
                        chunk_events.append({
                            "signature": tx.get("signature"),
                            "timestamp": ts,
                            "mint": mint,
                            "decimals": decimals,
                            "token_delta_raw": token_delta_raw,
                            "sol_delta_lamports": sol_delta,
                            "side": side,
                            "is_pumpfun_tx": is_pumpfun_tx
                        })
                        if mint not in mint_decimals:
                            mint_decimals[mint] = decimals

                token_events.extend(chunk_events)
                if cache:
                    cache.save_json("pump_events.json", token_events)
                    cache.save_json("signature_programs.json", _serialize_program_map(signature_program_ids))
                progress.update(1)

            missing_batches: List[Tuple[int, str, List[str]]] = []

            for batch_idx in range(total_batches):
                start = batch_idx * batch_size
                chunk = sig_list[start:start + batch_size]
                cache_key = f"helius_batch_{batch_idx:06d}.json"
                cached_txs = cache.load_json(cache_key) if cache else None
                if cached_txs is not None:
                    process_chunk_txs(cached_txs)
                    continue
                missing_batches.append((batch_idx, cache_key, chunk))

            if missing_batches:
                worker_count = min(len(missing_batches), helius_workers)
                if worker_count > 1:
                    print(f"Helius decoding: using {worker_count} parallel workers…")

                if worker_count == 1:
                    for batch_idx, cache_key, chunk in missing_batches:
                        txs = helius_get_transactions_batch(chunk)
                        if cache:
                            cache.save_json(cache_key, txs)
                        process_chunk_txs(txs)
                else:
                    with ThreadPoolExecutor(max_workers=worker_count) as pool:
                        future_map = {
                            pool.submit(helius_get_transactions_batch, chunk): (batch_idx, cache_key, chunk)
                            for batch_idx, cache_key, chunk in missing_batches
                        }
                        for fut in as_completed(future_map):
                            batch_idx, cache_key, chunk = future_map[fut]
                            try:
                                txs = fut.result()
                            except Exception as exc:
                                print(f"ERROR: failed to fetch Helius batch {batch_idx}: {exc}", file=sys.stderr)
                                try:
                                    txs = helius_get_transactions_batch(chunk)
                                except Exception as retry_exc:
                                    print(f"ERROR: retry failed for Helius batch {batch_idx}: {retry_exc}", file=sys.stderr)
                                    progress.update(1)
                                    continue
                            if cache:
                                cache.save_json(cache_key, txs)
                            process_chunk_txs(txs)

        if cache:
            cache.save_json(
                "cache_meta.json",
                {
                    "complete": True,
                    "signature_count": len(sig_list),
                    "pump_events_count": len(token_events),
                    "program_map_count": len(signature_program_ids),
                    "updated": datetime.now(timezone.utc).isoformat(),
                },
            )
            print(
                f"Cache updated ({len(sig_list)} signatures, {len(token_events)} token events) → {cache.root}"
            )

    if not token_events:
        raise SystemExit("No relevant token balance changes for this wallet were found (in the scanned history).")

    # Step 3: group per mint and compute metrics
    df = pd.DataFrame(token_events)
    if "is_pumpfun_tx" not in df.columns:
        df["is_pumpfun_tx"] = df["signature"].map(
            lambda s: PUMP_FUN_PROGRAM_ID in (signature_program_ids.get(s) or set())
        )
    signature_to_mints: Dict[str, Set[str]] = {}
    mint_first_buy_signature: Dict[str, str] = {}
    for evt in token_events:
        sig = evt.get("signature")
        mint = evt.get("mint")
        if sig and mint:
            signature_to_mints.setdefault(sig, set()).add(mint)
    # Keep only BUY/SELL rows
    df = df[df["side"].isin(["BUY", "SELL"])]

    # Aggregate per mint
    rows = []
    analyzed_mints = 0

    for mint, g in df.sort_values("timestamp").groupby("mint"):
        analyzed_mints += 1
        if max_mints and analyzed_mints > max_mints:
            break

        # First BUY by this wallet
        gb = g[g["side"] == "BUY"].sort_values("timestamp")
        gs = g[g["side"] == "SELL"].sort_values("timestamp")

        if gb.empty:
            continue

        first_buy = gb.iloc[0]
        first_buy_ts = int(first_buy["timestamp"])
        if isinstance(first_buy.get("signature"), str):
            mint_first_buy_signature[mint] = first_buy["signature"]

        # First SELL strictly after first BUY
        first_sell_ts = None
        if not gs.empty:
            after = gs[gs["timestamp"] > first_buy_ts]
            if not after.empty:
                first_sell_ts = int(after.iloc[0]["timestamp"])

        delay_seconds = (first_sell_ts - first_buy_ts) if first_sell_ts else None

        # Buy/Sell totals (SOL deltas are per-tx net for the wallet, so sum for PnL incl. fees)
        lamports_list = g["sol_delta_lamports"].tolist()
        net_profit_sol = lamports_to_sol(sum(lamports_list))

        sol_spent_total = lamports_to_sol(-sum([x for x in g["sol_delta_lamports"] if x < 0]))
        sol_received_total = lamports_to_sol(sum([x for x in g["sol_delta_lamports"] if x > 0]))

        first_buy_sol = lamports_to_sol(-first_buy["sol_delta_lamports"]) if first_buy["sol_delta_lamports"] < 0 else 0.0
        sells_count = int((g["side"] == "SELL").sum())
        is_pumpfun_mint = bool(g["is_pumpfun_tx"].any())

        rows.append({
            "mint": mint,
            "first_buy_ts": first_buy_ts,
            "first_sell_ts": first_sell_ts,
            "delay_seconds": delay_seconds,
            "sells_count": sells_count,
            "first_buy_sol": round(first_buy_sol, 6),
            "sol_spent_total": round(sol_spent_total, 6),
            "sol_received_total": round(sol_received_total, 6),
            "net_profit_sol": round(net_profit_sol, 6),
            "is_pumpfun_mint": is_pumpfun_mint
        })

    out_df = pd.DataFrame(rows).sort_values(["first_buy_ts"]).reset_index(drop=True)
    if (raydium_launchpad_only or include_raydium_launchpad) and not out_df.empty:
        candidate = set(out_df["mint"].unique())
        base_signatures = [mint_first_buy_signature.get(m) for m in candidate if mint_first_buy_signature.get(m)]
        if not base_signatures:
            base_signatures = list(signature_to_mints.keys()) or sig_list
        if not base_signatures:
            print("WARNING: No signatures available for Raydium launchpad detection; skipping filter.")
        else:
            print(f"Raydium launchpad detection: scanning {len(base_signatures)} signatures…")
            raydium_launchpad_mints = detect_launchpad_mints(
                wallet,
                base_signatures,
                candidate_mints=candidate,
                signature_mints=signature_to_mints,
                signature_programs=signature_program_ids,
                required_program_ids=RAYDIUM_LAUNCHPAD_PROGRAM_IDS,
                trigger_program_ids=RAYDIUM_LAUNCHPAD_TRIGGER_PROGRAM_IDS,
                keywords=RAYDIUM_LAUNCHPAD_KEYWORDS,
                tqdm_label="raydium launchpad",
                max_rpc_workers=8,
            )
            print(f"Raydium launchpad detection found {len(raydium_launchpad_mints)} mint(s).")
    out_df["is_raydium_launchpad"] = out_df["mint"].isin(raydium_launchpad_mints)

    if raydium_launchpad_only and not out_df.empty:
        before_count = len(out_df)
        out_df = out_df[out_df["is_raydium_launchpad"]].reset_index(drop=True)
        print(f"Raydium launchpad filter kept {len(out_df)}/{before_count} mints.")
        if out_df.empty:
            print("No Raydium Launchpad-created mints detected for this wallet in the scanned window.")
    elif include_raydium_launchpad and not out_df.empty:
        before_count = len(out_df)
        mask = out_df["is_pumpfun_mint"] | out_df["is_raydium_launchpad"]
        out_df = out_df[mask].reset_index(drop=True)
        pump_count = int(out_df["is_pumpfun_mint"].sum())
        ray_only = int((out_df["is_raydium_launchpad"] & ~out_df["is_pumpfun_mint"]).sum())
        print(
            f"Combined pump.fun + Raydium launchpad kept {len(out_df)}/{before_count} mints "
            f"({pump_count} pump.fun, {ray_only} Raydium-only)."
        )
    else:
        if not out_df.empty:
            before_count = len(out_df)
            out_df = out_df[out_df["is_pumpfun_mint"]].reset_index(drop=True)
            if len(out_df) != before_count:
                print(f"Filtered out {before_count - len(out_df)} non-pump.fun mints.")

    # Step 4: optional metadata fetch (name/symbol)
    if fetch_metadata and not out_df.empty:
        print("Fetching token metadata (name/symbol) via DAS…")
        names = {}
        for m in tqdm(out_df["mint"].tolist(), desc="Metadata"):
            info = helius_get_asset(m) or {}
            content = info.get("content") or {}
            md = content.get("metadata") or {}
            name = md.get("name")
            symbol = md.get("symbol")
            names[m] = (name, symbol)
        out_df["name"] = out_df["mint"].map(lambda m: (names.get(m) or (None, None))[0])
        out_df["symbol"] = out_df["mint"].map(lambda m: (names.get(m) or (None, None))[1])

    # Step 5: summaries & correlations
    stats = {}
    if not out_df.empty:
        recent = out_df.dropna(subset=["delay_seconds"])
        stats["count_with_sell"] = int(len(recent))
        stats["sell_within_60s"] = int((recent["delay_seconds"] <= 60).sum())
        stats["sell_60_120s"] = int(((recent["delay_seconds"] > 60) & (recent["delay_seconds"] <= 120)).sum())
        stats["sell_gt_120s"] = int((recent["delay_seconds"] > 120).sum())
        stats["median_delay_s"] = float(recent["delay_seconds"].median()) if not recent.empty else None
        stats["median_net_profit_sol"] = float(out_df["net_profit_sol"].median()) if not out_df.empty else None
        # Correlations
        try:
            pear = pearsonr(recent["delay_seconds"], recent["net_profit_sol"])
            spear = spearmanr(recent["delay_seconds"], recent["net_profit_sol"])
            stats["pearson_r_delay_vs_profit"] = float(pear.statistic) if hasattr(pear, "statistic") else float(pear[0])
            stats["spearman_r_delay_vs_profit"] = float(spear.statistic) if hasattr(spear, "statistic") else float(spear[0])
        except Exception:
            stats["pearson_r_delay_vs_profit"] = None
            stats["spearman_r_delay_vs_profit"] = None

        # Two-batch sells detection
        stats["two_batch_sell_count"] = int((out_df["sells_count"] == 2).sum())

    if cache and signature_program_ids:
        cache.save_json("signature_programs.json", _serialize_program_map(signature_program_ids))
        meta_update = cache.load_json("cache_meta.json") or {}
        meta_update["program_map_count"] = len(signature_program_ids)
        meta_update["updated"] = datetime.now(timezone.utc).isoformat()
        cache.save_json("cache_meta.json", meta_update)

    metadata: Dict[str, Any] = {
        "mint_decimals": mint_decimals,
        "raydium_launchpad_mints": sorted(raydium_launchpad_mints),
    }

    return out_df, stats, metadata


def _extract_account_owner_map(enhanced_tx: Dict[str, Any], mint: str) -> Dict[str, str]:
    """Build map of token account -> owner for the specified mint."""
    owners: Dict[str, str] = {}
    for acct in enhanced_tx.get("accountData", []) or []:
        for tbc in acct.get("tokenBalanceChanges", []) or []:
            if tbc.get("mint") != mint:
                continue
            token_account = tbc.get("tokenAccount")
            user_account = tbc.get("userAccount")
            if token_account and user_account and token_account not in owners:
                owners[token_account] = user_account
    return owners


def _mint_transfer_rows_from_tx(enhanced_tx: Dict[str, Any],
                                mint: str,
                                creator_wallet: str) -> List[Dict[str, Any]]:
    """Extract pump.fun mint transfer rows involving this mint."""
    rows: List[Dict[str, Any]] = []
    transfers = enhanced_tx.get("tokenTransfers") or []
    if not transfers:
        return rows

    sig = enhanced_tx.get("signature")
    ts = parse_ts(enhanced_tx)
    owner_map = _extract_account_owner_map(enhanced_tx, mint)
    native_transfers = enhanced_tx.get("nativeTransfers") or []

    def _native_totals(user: Optional[str]) -> Tuple[int, int]:
        if not user:
            return 0, 0
        outgoing = sum(int(nt.get("amount") or 0) for nt in native_transfers if nt.get("fromUserAccount") == user)
        incoming = sum(int(nt.get("amount") or 0) for nt in native_transfers if nt.get("toUserAccount") == user)
        return outgoing, incoming

    for tf in transfers:
        if tf.get("mint") != mint:
            continue
        token_amount_raw = tf.get("tokenAmount")
        if token_amount_raw is None:
            continue
        try:
            token_amount = float(token_amount_raw)
        except (TypeError, ValueError):
            try:
                token_amount = float(str(token_amount_raw))
            except Exception:
                continue
        from_user = tf.get("fromUserAccount")
        to_user = tf.get("toUserAccount")
        from_token_account = tf.get("fromTokenAccount")
        to_token_account = tf.get("toTokenAccount")

        resolved_from_user = from_user
        resolved_to_user = to_user

        if not resolved_from_user and from_token_account:
            resolved_from_user = owner_map.get(from_token_account)
        if not resolved_to_user and to_token_account:
            resolved_to_user = owner_map.get(to_token_account)

        from_out, from_in = _native_totals(resolved_from_user)
        to_out, to_in = _native_totals(resolved_to_user)

        rows.append({
            "mint": mint,
            "signature": sig,
            "timestamp": ts,
            "from_user": from_user,
            "to_user": to_user,
            "from_user_resolved": resolved_from_user,
            "to_user_resolved": resolved_to_user,
            "from_token_account": from_token_account,
            "to_token_account": to_token_account,
            "token_amount": token_amount,
            "token_amount_abs": abs(token_amount),
            "token_direction": "out" if token_amount < 0 else "in",
            "sol_from_user_spent": lamports_to_sol(from_out),
            "sol_from_user_received": lamports_to_sol(from_in),
            "sol_to_user_spent": lamports_to_sol(to_out),
            "sol_to_user_received": lamports_to_sol(to_in),
            "is_creator_involved": bool(resolved_from_user == creator_wallet or resolved_to_user == creator_wallet),
        })
    return rows


def _unique_preserve(items: List[str]) -> List[str]:
    """Return list without duplicates while preserving order."""
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def collect_mint_transfers(mint: str,
                           creator_wallet: str,
                           signature_limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Fetch decoded transactions touching the mint and pull token transfer rows that
    involve the mint. Returns a list of dict rows suitable for CSV export.
    """
    if signature_limit is not None and signature_limit < 0:
        return []

    collected: List[Dict[str, Any]] = []
    signatures: List[str] = []
    before_sig: Optional[str] = None

    def _remaining() -> Optional[int]:
        if signature_limit is None or signature_limit == 0:
            return None
        return max(signature_limit - len(signatures), 0)

    while True:
        remaining = _remaining()
        if remaining is not None and remaining <= 0:
            break
        fetch_limit = 1000 if remaining is None else min(1000, remaining)
        batch = get_signatures_for_address(mint, before_sig, fetch_limit)
        if not batch:
            break
        signatures.extend([b["signature"] for b in batch if b.get("signature")])
        before_sig = batch[-1]["signature"]
        if len(batch) < fetch_limit:
            break

    if not signatures:
        return collected

    unique_sigs = _unique_preserve(signatures)

    for start in range(0, len(unique_sigs), 100):
        chunk = unique_sigs[start:start + 100]
        txs = helius_get_transactions_batch(chunk)
        for tx in txs:
            collected.extend(_mint_transfer_rows_from_tx(tx, mint, creator_wallet))

    return collected


def export_mint_tx_csv(mint: str,
                       creator_wallet: str,
                       output_dir: Path,
                       signature_limit: Optional[int] = None,
                       token_decimals: Optional[int] = None) -> Tuple[Optional[Path], int]:
    """
    Collect mint token transfers and persist them as CSV.
    Returns (path, row_count); path is None if nothing was exported.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = collect_mint_transfers(mint, creator_wallet, signature_limit=signature_limit)
    if not rows:
        return None, 0

    rows.sort(key=lambda r: (r.get("timestamp") or 0, str(r.get("signature") or "")))
    df = pd.DataFrame(rows)
    df["time_utc"] = df["timestamp"].map(human_time)
    if token_decimals is not None:
        df["token_decimals"] = token_decimals
    columns = [
        "mint",
        "signature",
        "timestamp",
        "time_utc",
        "from_user",
        "from_user_resolved",
        "to_user",
        "to_user_resolved",
        "from_token_account",
        "to_token_account",
        "token_amount",
        "token_amount_abs",
        "token_direction",
        "token_decimals",
        "sol_from_user_spent",
        "sol_from_user_received",
        "sol_to_user_spent",
        "sol_to_user_received",
        "is_creator_involved",
    ]
    cols_present = [c for c in columns if c in df.columns]

    out_path = output_dir / f"{sanitize_for_fs(mint)}_mint_txs.csv"
    df[cols_present].to_csv(out_path, index=False)
    return out_path, len(df)

# ---------- CLI ----------

def human_time(ts: Optional[Any]) -> str:
    if ts is None or pd.isna(ts):
        return "-"

    if isinstance(ts, datetime):
        dt = ts.astimezone(timezone.utc)
    else:
        try:
            ts_float = float(ts)
        except (TypeError, ValueError):
            return "-"

        if math.isnan(ts_float) or math.isinf(ts_float):
            return "-"

        try:
            dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
        except (ValueError, OSError):
            return "-"

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wallet", required=True, help="Dev wallet address to analyze")
    ap.add_argument("--limit", type=int, default=25000, help="Max signatures to scan")
    ap.add_argument("--days", type=int, default=None, help="Only scan last N days")
    ap.add_argument("--fetch-metadata", action="store_true", help="Fetch token name/symbol via DAS (slower)")
    ap.add_argument("--max-mints", type=int, default=None, help="Limit number of mints processed for testing")
    ap.add_argument("--raydium-launchpad", action="store_true",
                    help="Restrict to coins this wallet launched on Raydium Launchpad (best-effort log detection)")
    ap.add_argument("--include-raydium-launchpad", action="store_true",
                    help="Include Raydium Launchpad mints in addition to pump.fun results")
    ap.add_argument("--cache-dir", type=str, default=None,
                    help="Directory to persist decoded signatures/transactions for reuse (default: mode-specific cache)")
    ap.add_argument("--resume-cache", type=str, default=None,
                    help="Path to a cache directory created by a previous run (skips re-decoding)")
    ap.add_argument("--export-mint-txs", action="store_true",
                    help="Export per-mint transaction CSVs capturing all token transfers for each mint")
    ap.add_argument("--mint-tx-limit", type=int, default=None, dest="mint_tx_limit",
                    help="Max signatures per mint when exporting mint transaction CSVs (0 => unlimited)")
    ap.add_argument("--sniper-limit", type=int, dest="mint_tx_limit", help=argparse.SUPPRESS)
    ap.add_argument("--mint-tx-output-dir", type=str, default=None, dest="mint_tx_output_dir",
                    help="Directory to write per-mint mint transaction CSVs (default: mint_txs/<wallet>)")
    ap.add_argument("--sniper-output-dir", type=str, dest="mint_tx_output_dir", help=argparse.SUPPRESS)
    default_helius_workers = max(1, min(4, os.cpu_count() or 1))
    ap.add_argument("--helius-workers", type=int, default=default_helius_workers,
                    help="Concurrent Helius batch requests (default: %(default)s). Use 1 to disable parallel decoding.")
    args = ap.parse_args()

    if args.raydium_launchpad and args.include_raydium_launchpad:
        ap.error("Use either --raydium-launchpad or --include-raydium-launchpad, not both.")

    mode_suffix = (
        "raydium_launchpad"
        if args.raydium_launchpad
        else "pumpfun_plus_raydium"
        if args.include_raydium_launchpad
        else "pumpfun"
    )
    default_cache_root = Path(".cache") / f"{sanitize_for_fs(args.wallet)}_{mode_suffix}"

    cache_dir_path = Path(args.cache_dir).expanduser() if args.cache_dir else default_cache_root
    resume_cache_path: Optional[Path] = Path(args.resume_cache).expanduser() if args.resume_cache else None

    if resume_cache_path is None:
        meta_path = cache_dir_path / "cache_meta.json"
        if meta_path.exists():
            try:
                with meta_path.open("r", encoding="utf-8") as fh:
                    cached_meta = json.load(fh)
            except Exception:
                cached_meta = None
            if cached_meta and cached_meta.get("complete"):
                resume_cache_path = cache_dir_path
        elif not args.cache_dir:
            # Backwards compatibility: attempt to auto-detect legacy cache without suffix
            legacy_root = Path(".cache") / sanitize_for_fs(args.wallet)
            legacy_meta = legacy_root / "cache_meta.json"
            if legacy_meta.exists():
                try:
                    with legacy_meta.open("r", encoding="utf-8") as fh:
                        cached_meta = json.load(fh)
                except Exception:
                    cached_meta = None
                if cached_meta and cached_meta.get("complete"):
                    print(f"Legacy cache detected at {legacy_root}; using it for this run.")
                    resume_cache_path = legacy_root
                    cache_dir_path = legacy_root

    df, stats, metadata = analyze_wallet(
        wallet=args.wallet,
        limit=args.limit,
        days=args.days,
        fetch_metadata=args.fetch_metadata,
        max_mints=args.max_mints,
        raydium_launchpad_only=args.raydium_launchpad,
        include_raydium_launchpad=args.include_raydium_launchpad,
        cache_dir=str(cache_dir_path),
        resume_cache=str(resume_cache_path) if resume_cache_path else None,
        helius_workers=args.helius_workers,
    )

    if df.empty:
        print("\nNo pump.fun BUY/SELL activity detected for this wallet.")
        return

    # Save CSV
    csv_suffix = mode_suffix
    csv_path = f"{args.wallet}_{csv_suffix}.csv"
    df_out = df.copy()
    # Add human-readable times
    df_out["first_buy_time"] = df_out["first_buy_ts"].map(human_time)
    df_out["first_sell_time"] = df_out["first_sell_ts"].map(human_time)
    cols = [
        "mint",
        "name",
        "symbol",
        "first_buy_time",
        "first_sell_time",
        "delay_seconds",
        "sells_count",
        "first_buy_sol",
        "sol_spent_total",
        "sol_received_total",
        "net_profit_sol",
        "is_pumpfun_mint",
        "is_raydium_launchpad",
    ]
    cols = [c for c in cols if c in df_out.columns]
    df_out[cols].to_csv(csv_path, index=False)
    print(f"\n✔ Saved per-mint results → {csv_path}")

    mint_decimals_map = metadata.get("mint_decimals", {}) if isinstance(metadata, dict) else {}
    mint_tx_limit_raw = args.mint_tx_limit
    export_mint_txs = bool(args.export_mint_txs)
    mint_tx_limit: Optional[int]

    if mint_tx_limit_raw is None:
        mint_tx_limit = None
    elif mint_tx_limit_raw < 0:
        mint_tx_limit = None
        export_mint_txs = False
    elif mint_tx_limit_raw == 0:
        mint_tx_limit = None
        export_mint_txs = True
    else:
        mint_tx_limit = mint_tx_limit_raw
        export_mint_txs = True

    if export_mint_txs and not df.empty:
        limit_text = "no limit" if mint_tx_limit is None else f"limit {mint_tx_limit}"
        mint_tx_root = Path(args.mint_tx_output_dir).expanduser() if args.mint_tx_output_dir else Path("mint_txs") / sanitize_for_fs(args.wallet)
        print(f"\nExporting mint transaction CSVs ({limit_text} per mint)…")
        for mint in df["mint"].tolist():
            try:
                out_path, row_count = export_mint_tx_csv(
                    mint,
                    args.wallet,
                    mint_tx_root,
                    signature_limit=mint_tx_limit,
                    token_decimals=mint_decimals_map.get(mint),
                )
            except Exception as exc:
                print(f"  {mint}: ERROR exporting mint tx data ({exc})", file=sys.stderr)
                continue
            if out_path:
                print(f"  {mint}: wrote {row_count} rows → {out_path}")
            else:
                print(f"  {mint}: no qualifying token transfers found (within signature criteria).")

    # Print a compact summary
    print("\n===== SUMMARY =====")
    print(f"Total mints analyzed: {len(df)} (unique: {df['mint'].nunique()})")
    if stats:
        print(f"Mints with at least one SELL: {stats.get('count_with_sell', 0)}")
        print(f"SELL delay: ≤60s={stats.get('sell_within_60s', 0)}, 60–120s={stats.get('sell_60_120s', 0)}, >120s={stats.get('sell_gt_120s', 0)}")
        print(f"Median delay (s): {stats.get('median_delay_s')}")
        print(f"Median net profit (SOL): {stats.get('median_net_profit_sol')}")
        print(f"Two-batch sells (sells_count==2): {stats.get('two_batch_sell_count', 0)}")
        print("Correlation delay vs net profit (SOL):")
        print(f"  Pearson r:  {stats.get('pearson_r_delay_vs_profit')}")
        print(f"  Spearman r: {stats.get('spearman_r_delay_vs_profit')}")
    if "is_raydium_launchpad" in df_out.columns:
        pump_col = df_out.get("is_pumpfun_mint")
        pump_count = int(pump_col.sum()) if pump_col is not None else 0
        ray_col = df_out["is_raydium_launchpad"]
        ray_count = int(ray_col.sum())
        ray_only = int((ray_col & (~pump_col if pump_col is not None else True)).sum()) if ray_count else 0
        if args.raydium_launchpad:
            print(f"Raydium Launchpad mints: {ray_count}")
        elif args.include_raydium_launchpad:
            print(f"pump.fun mints: {pump_count}, Raydium launchpad mints: {ray_count} (Raydium-only: {ray_only})")

    # Show a few examples (top/bottom by net profit)
    top = df_out.sort_values("net_profit_sol", ascending=False).head(5)
    low = df_out.sort_values("net_profit_sol", ascending=True).head(5)
    def short(m): 
        return f"{m[:4]}…{m[-4:]}" if isinstance(m, str) else m
    print("\nTop 5 by net profit (SOL):")
    for _, r in top.iterrows():
        print(f"  {short(r.get('symbol') or r.get('name') or r['mint'])}  +{r['net_profit_sol']} SOL  delay={r['delay_seconds']}s sells={r['sells_count']}")
    print("\nBottom 5 by net profit (SOL):")
    for _, r in low.iterrows():
        print(f"  {short(r.get('symbol') or r.get('name') or r['mint'])}  {r['net_profit_sol']} SOL  delay={r['delay_seconds']}s sells={r['sells_count']}")

if __name__ == "__main__":
    main()
