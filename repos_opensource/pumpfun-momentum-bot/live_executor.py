"""
Executor de trades REAIS na Solana via PumpPortal (modo local — a chave
privada nunca sai da maquina). Compartilhado pela GUI/engine (modo live) e
pelo live_test.py.

Carrega a carteira de LIVE_SEED_PHRASE ou LIVE_PRIVATE_KEY no .env e expoe
operacoes sincronas: buy, sell, sol_balance, token_balance.

Cada buy/sell mede o saldo de SOL antes/depois para reportar o custo/recebido
real (slippage + fees ja embutidos).
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import ssl
import struct
import time

import httpx
import requests
from solders.keypair import Keypair  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore
from solders.signature import Signature  # type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solana.rpc.api import Client  # type: ignore
from solana.rpc.types import TxOpts, TokenAccountOpts  # type: ignore

LAMPORTS       = 1_000_000_000
PUMPPORTAL_URL = "https://pumpportal.fun/api/trade-local"
TOKEN_PROGRAM  = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022     = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


def _windows_ssl_context() -> ssl.SSLContext:
    """
    Contexto SSL que confia nos certificados da loja do Windows (inclui o CA do
    proxy corporativo). O httpx usado pelo solana-py ignora a loja do Windows e
    usa o certifi, entao injetamos os certs do sistema aqui.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    loaded = False
    for store in ("ROOT", "CA"):
        try:
            for cert_der, enc, _trust in ssl.enum_certificates(store):
                if enc == "x509_asn":
                    try:
                        ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert_der))
                        loaded = True
                    except ssl.SSLError:
                        pass
        except (AttributeError, OSError):
            pass
    if not loaded:  # fallback: bundle padrao
        ctx.load_default_certs()
    return ctx


class LiveExecutor:
    def __init__(self):
        self.slippage_pct = float(os.environ.get("LIVE_SLIPPAGE_PCT", "10"))
        self.priority_fee = float(os.environ.get("LIVE_PRIORITY_FEE", "0.0001"))
        rpc = os.environ.get("LIVE_RPC", "")
        if not rpc:
            raise RuntimeError("LIVE_RPC nao definido no .env")
        self.rpc_url = rpc
        self.client = Client(rpc)
        # o httpx interno do solana-py usa certifi e nao confia no proxy do
        # Windows — troca a sessao por uma que usa a loja de certificados do SO
        self.client._provider.session = httpx.Client(timeout=30, verify=_windows_ssl_context())
        self.keypair = self._load_keypair()
        self.pubkey = str(self.keypair.pubkey())

    # ---- carregamento da chave -------------------------------------------

    @staticmethod
    def _slip10(seed: bytes, index: int) -> bytes:
        path = f"m/44'/501'/{index}'/0'"
        h = hmac_mod.new(b"ed25519 seed", seed, hashlib.sha512).digest()
        key, chain = h[:32], h[32:]
        for c in path.split("/")[1:]:
            hardened = c.endswith("'")
            idx = int(c.rstrip("'")) + (0x80000000 if hardened else 0)
            data = b"\x00" + key + struct.pack(">I", idx)
            h = hmac_mod.new(chain, data, hashlib.sha512).digest()
            key, chain = h[:32], h[32:]
        return key

    def _load_keypair(self) -> Keypair:
        seed_phrase = os.environ.get("LIVE_SEED_PHRASE", "").strip()
        priv = os.environ.get("LIVE_PRIVATE_KEY", "")
        index = int(os.environ.get("DERIVATION_INDEX", "0"))
        if seed_phrase:
            from mnemonic import Mnemonic
            mnemo = Mnemonic("english")
            if not mnemo.check(seed_phrase):
                raise RuntimeError("LIVE_SEED_PHRASE invalida")
            seed = mnemo.to_seed(seed_phrase, passphrase="")
            return Keypair.from_seed(self._slip10(seed, index))
        if priv:
            return Keypair.from_bytes(bytes(json.loads(priv)))
        raise RuntimeError("Defina LIVE_SEED_PHRASE ou LIVE_PRIVATE_KEY no .env")

    # ---- leitura de saldos -----------------------------------------------

    def sol_balance(self) -> float:
        return self.client.get_balance(self.keypair.pubkey()).value / LAMPORTS

    def transfer_sol(self, to_addr: str, amount_sol: float) -> str:
        """Transfere SOL desta carteira (execucao) para outro endereco. Retorna a signature."""
        from solders.system_program import transfer, TransferParams
        from solders.transaction import Transaction
        to_pk = Pubkey.from_string(to_addr)
        lamports = int(amount_sol * LAMPORTS)
        bal = self.client.get_balance(self.keypair.pubkey()).value
        if bal < lamports + 10000:  # +taxa
            raise RuntimeError(f"saldo insuficiente ({bal/LAMPORTS:.4f} SOL)")
        ix = transfer(TransferParams(from_pubkey=self.keypair.pubkey(),
                                     to_pubkey=to_pk, lamports=lamports))
        bh = self.client.get_latest_blockhash().value.blockhash
        tx = Transaction.new_signed_with_payer([ix], self.keypair.pubkey(), [self.keypair], bh)
        opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed")
        sig = str(self.client.send_raw_transaction(bytes(tx), opts=opts).value)
        sig_obj = Signature.from_string(sig)
        for _ in range(30):
            time.sleep(1)
            status = self.client.get_signature_statuses([sig_obj]).value[0]
            if status and status.confirmation_status:
                if status.err:
                    raise RuntimeError(f"transfer falhou: {status.err}")
                return sig
        raise TimeoutError("transfer nao confirmou")

    def token_balance(self, mint: str) -> float:
        owner = self.keypair.pubkey()
        mint_pk = Pubkey.from_string(mint)
        for program in (TOKEN_PROGRAM, TOKEN_2022):
            opts = TokenAccountOpts(mint=mint_pk, program_id=program, encoding="jsonParsed")
            try:
                resp = self.client.get_token_accounts_by_owner_json_parsed(owner, opts)
            except Exception:
                continue
            for acc in resp.value:
                amt = acc.account.data.parsed["info"]["tokenAmount"]
                ui = float(amt["uiAmountString"] or 0)
                if ui > 0:
                    return ui
        return 0.0

    def wait_for_token(self, mint: str, tries: int = 8, delay: float = 2.0) -> float:
        for _ in range(tries):
            bal = self.token_balance(mint)
            if bal > 0:
                return bal
            time.sleep(delay)
        return 0.0

    # ---- PumpPortal + assinatura -----------------------------------------

    def _pumpportal(self, action: str, mint: str, amount, denominated_in_sol: bool) -> bytes:
        payload = {
            "publicKey": self.pubkey,
            "action": action,
            "mint": mint,
            "amount": amount,
            "denominatedInSol": "true" if denominated_in_sol else "false",
            "slippage": self.slippage_pct,
            "priorityFee": self.priority_fee,
            "pool": "auto",
        }
        resp = requests.post(PUMPPORTAL_URL, json=payload, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"PumpPortal {action} {resp.status_code}: {resp.text[:160]}")
        return resp.content

    def _sign_send_confirm(self, tx_bytes: bytes, timeout_s: int = 90) -> str:
        tx = VersionedTransaction.from_bytes(tx_bytes)
        signed = VersionedTransaction(tx.message, [self.keypair])
        opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed")
        sig = str(self.client.send_raw_transaction(bytes(signed), opts=opts).value)
        sig_obj = Signature.from_string(sig)
        for _ in range(timeout_s):
            time.sleep(1)
            status = self.client.get_signature_statuses([sig_obj]).value[0]
            if status and status.confirmation_status:
                lvl = str(status.confirmation_status).lower()
                if "confirmed" in lvl or "finalized" in lvl:
                    if status.err:
                        raise RuntimeError(f"tx falhou on-chain: {status.err}")
                    return sig
        raise TimeoutError("tx nao confirmou no tempo limite")

    def _tx_deltas(self, sig: str, mint: str):
        """
        Le a transacao confirmada e retorna (sol_delta, token_delta_ui) da carteira.
        Usa o pre/post balance DA PROPRIA tx -> imune a trades concorrentes
        (ao contrario de ler o saldo global antes/depois). None se nao conseguir ler.
        """
        for _ in range(8):
            try:
                r = requests.post(self.rpc_url, json={
                    "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                    "params": [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}],
                }, timeout=20).json()
            except requests.RequestException:
                time.sleep(1)
                continue
            tx = r.get("result")
            if not tx:
                time.sleep(1)
                continue
            meta = tx["meta"]
            keys = [k["pubkey"] if isinstance(k, dict) else k
                    for k in tx["transaction"]["message"]["accountKeys"]]
            try:
                idx = keys.index(self.pubkey)
            except ValueError:
                return 0.0, 0.0
            sol_delta = (meta["postBalances"][idx] - meta["preBalances"][idx]) / 1e9

            def _tok(bals):
                for b in bals or []:
                    if b.get("mint") == mint and b.get("owner") == self.pubkey:
                        return float(b["uiTokenAmount"]["uiAmount"] or 0)
                return 0.0
            token_delta = _tok(meta.get("postTokenBalances")) - _tok(meta.get("preTokenBalances"))
            return sol_delta, token_delta
        return None, None

    # ---- operacoes de alto nivel -----------------------------------------

    def buy(self, mint: str, sol_amount: float) -> dict:
        """Compra sol_amount de SOL do token. Retorna {sig, spent_sol, tokens}."""
        tx = self._pumpportal("buy", mint, sol_amount, denominated_in_sol=True)
        sig = self._sign_send_confirm(tx)
        sol_delta, token_delta = self._tx_deltas(sig, mint)
        if sol_delta is None:  # fallback se nao leu a tx
            token_delta = self.wait_for_token(mint)
            sol_delta = -sol_amount
        return {
            "sig": sig,
            "spent_sol": max(0.0, -sol_delta),
            "tokens": max(0.0, token_delta or 0.0),
        }

    def sell(self, mint: str, tokens_ui: float) -> dict:
        """Vende tokens_ui unidades (cap no saldo real). Retorna {sig, proceeds_sol}."""
        cap = self.token_balance(mint)
        amount = min(tokens_ui, cap) if cap > 0 else tokens_ui
        if amount <= 0:
            raise RuntimeError("saldo do token zerado — nada para vender")
        tx = self._pumpportal("sell", mint, amount, denominated_in_sol=False)
        sig = self._sign_send_confirm(tx)
        sol_delta, _ = self._tx_deltas(sig, mint)
        return {
            "sig": sig,
            "proceeds_sol": max(0.0, sol_delta) if sol_delta is not None else 0.0,
        }
