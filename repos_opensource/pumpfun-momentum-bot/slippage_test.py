"""
Mede slippage + latencia REAIS de execucao em tokens pump.fun (bonding curve).

Para cada trial:
  1. le o preco de mercado da BONDING CURVE on-chain (referencia "decisao")
  2. compra um valor minimo via PumpPortal (mesma infra do bot)
  3. compara o preco de fill real com a referencia -> slippage de ENTRADA
  4. le o preco de novo e vende -> slippage de SAIDA
  5. cronometra a latencia (decisao -> confirmacao)

Imprime as medias e o veredito: se o slippage/lado < ~0.6%, a estrategia de
momentum tem chance; acima disso, o atrito come a vantagem.

PRE-REQUISITOS:
  - LIVE_SEED_PHRASE / LIVE_PRIVATE_KEY no .env (carteira de execucao, com SOL)
  - MEASURE_RPC no .env apontando pra um RPC que FUNCIONE (o Helius atual esta
    sem cota). Default: RPC publico da Solana. Ideal: key nova do Helius.

USO:
    python slippage_test.py [MINT]      # mint opcional; sem ele, pega tokens novos do WS
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys
import time
import statistics

import requests
from dotenv import load_dotenv

load_dotenv()

# RPC dedicado pro teste (o Helius do projeto esta sem cota)
MEASURE_RPC = os.environ.get("MEASURE_RPC", "https://api.mainnet-beta.solana.com")
os.environ["LIVE_RPC"] = MEASURE_RPC  # forca o LiveExecutor a usar este RPC

from solders.pubkey import Pubkey  # type: ignore  # noqa: E402
from live_executor import LiveExecutor  # noqa: E402

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
WS_URI = "wss://pumpportal.fun/api/data"  # subscribeNewToken e gratuito (sem key)

STAKE_SOL = float(os.environ.get("SLIP_STAKE_SOL", "0.01"))
N_TRIALS  = int(os.environ.get("SLIP_TRIALS", "3"))


def bonding_curve_price(mint: str) -> float | None:
    """Preco de mercado (SOL por token) lido da bonding curve on-chain. None se nao achar."""
    pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                         PUMP_PROGRAM)
    try:
        r = requests.post(MEASURE_RPC, json={
            "jsonrpc": "2.0", "id": 1, "method": "getAccountInfo",
            "params": [str(pda), {"encoding": "base64"}],
        }, timeout=20).json()
    except requests.RequestException:
        return None
    val = (r.get("result") or {}).get("value")
    if not val:
        return None
    data = base64.b64decode(val["data"][0])
    if len(data) < 24:
        return None
    v_token = struct.unpack_from("<Q", data, 8)[0]   # virtual_token_reserves (6 dec)
    v_sol = struct.unpack_from("<Q", data, 16)[0]    # virtual_sol_reserves (9 dec)
    if v_token == 0:
        return None
    # SOL por token (UI): (v_sol/1e9) / (v_token/1e6)
    return (v_sol / 1e9) / (v_token / 1e6)


async def fresh_mints(n: int = 12, timeout: float = 30) -> list[str]:
    """Coleta mints recem-criados via WS gratuito."""
    import websockets
    mints = []
    try:
        async with websockets.connect(WS_URI, ping_interval=20) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            t0 = time.time()
            while len(mints) < n and time.time() - t0 < timeout:
                d = json.loads(await ws.recv())
                if d.get("txType") == "create" and d.get("mint"):
                    mints.append(d["mint"])
    except Exception as e:
        print(f"[warn] WS falhou: {e}")
    return mints


def measure_one(ex: LiveExecutor, mint: str) -> dict | None:
    """Round-trip medido num token. Retorna metricas ou None se nao deu pra testar."""
    p0 = bonding_curve_price(mint)
    if not p0:
        return None  # nao esta na bonding curve (migrou) ou nao existe

    t0 = time.time()
    try:
        buy = ex.buy(mint, STAKE_SOL)
    except Exception as e:
        print(f"  [skip] compra falhou: {str(e)[:80]}")
        return None
    t_buy = time.time()
    if buy["tokens"] <= 0:
        print("  [skip] compra sem tokens")
        return None

    fill_buy = buy["spent_sol"] / buy["tokens"]
    entry_slip = (fill_buy / p0 - 1) * 100   # % a mais que voce pagou vs mercado
    latency = t_buy - t0

    time.sleep(2)
    p1 = bonding_curve_price(mint) or fill_buy
    try:
        sell = ex.sell(mint, buy["tokens"])
    except Exception as e:
        print(f"  [aviso] venda falhou ({str(e)[:60]}); posicao ficou no token.")
        return {"mint": mint, "entry_slip": entry_slip, "exit_slip": None,
                "latency": latency, "rt_pnl": None}

    sold_tokens = buy["tokens"]
    fill_sell = sell["proceeds_sol"] / sold_tokens if sold_tokens else 0
    exit_slip = (1 - fill_sell / p1) * 100 if p1 else None   # % a menos que recebeu vs mercado
    rt_pnl = sell["proceeds_sol"] - buy["spent_sol"]         # custo real do round-trip (inclui tudo)
    return {"mint": mint, "entry_slip": entry_slip, "exit_slip": exit_slip,
            "latency": latency, "rt_pnl": rt_pnl, "spent": buy["spent_sol"]}


def main():
    ex = LiveExecutor()
    print(f"Carteira: {ex.pubkey}")
    print(f"RPC: {MEASURE_RPC}")
    bal = ex.sol_balance()
    print(f"Saldo: {bal:.4f} SOL | stake {STAKE_SOL} x {N_TRIALS} trials\n")
    if bal < STAKE_SOL * N_TRIALS + 0.01:
        sys.exit("[erro] saldo insuficiente pra rodar os trials.")

    # mints a testar: argumento OU recem-criados do WS
    if len(sys.argv) > 1:
        candidates = [sys.argv[1].strip()]
    else:
        print("Coletando tokens novos do WS...")
        candidates = asyncio.run(fresh_mints())
        print(f"  {len(candidates)} candidatos\n")

    results = []
    for mint in candidates:
        if len(results) >= N_TRIALS:
            break
        print(f"--- testando {mint[:12]}… ---")
        r = measure_one(ex, mint)
        if r:
            es = f"{r['entry_slip']:+.2f}%"
            xs = f"{r['exit_slip']:+.2f}%" if r["exit_slip"] is not None else "—"
            print(f"  entrada slip={es}  saida slip={xs}  latencia={r['latency']:.1f}s")
            results.append(r)
        time.sleep(1)

    if not results:
        sys.exit("\nNenhum trial completou. Verifique o RPC (MEASURE_RPC) e o saldo.")

    entradas = [r["entry_slip"] for r in results if r["entry_slip"] is not None]
    saidas = [r["exit_slip"] for r in results if r["exit_slip"] is not None]
    lats = [r["latency"] for r in results]
    print("\n" + "=" * 52)
    print(f"TRIALS completos: {len(results)}")
    if entradas:
        print(f"slippage ENTRADA medio: {statistics.mean(entradas):+.2f}%")
    if saidas:
        print(f"slippage SAIDA medio:   {statistics.mean(saidas):+.2f}%")
    if entradas and saidas:
        por_lado = (statistics.mean(entradas) + statistics.mean(saidas)) / 2
        print(f"slippage MEDIO POR LADO: {por_lado:+.2f}%")
        print(f"latencia media: {statistics.mean(lats):.1f}s")
        print("-" * 52)
        if por_lado < 0.6:
            print(f"VEREDITO: {por_lado:.2f}%/lado < 0.6% -> estrategia tem CHANCE. Vale calibrar.")
        else:
            print(f"VEREDITO: {por_lado:.2f}%/lado >= 0.6% -> o atrito provavelmente come a vantagem.")
    print("=" * 52)


if __name__ == "__main__":
    main()
