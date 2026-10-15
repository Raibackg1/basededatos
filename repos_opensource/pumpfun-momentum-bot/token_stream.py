"""
Simulador de estrategia "monitorar tokens" (sem execucao, sem Helius).

Conecta no WebSocket do PumpPortal, escuta TODO token novo que nasce e os
trades de cada um em tempo real. Aplica filtros de entrada por momentum e
sai por REVERSAO (trailing stop a partir do pico) — nada de take-profit fixo.

Nao compra/vende de verdade: so registra "entraria/sairia aqui" e o PnL
teorico (com taxa estimada), pra avaliar e calibrar os filtros antes de
plugar execucao.

Saidas:
  - token_stream.log : eventos legiveis (WATCH/ENTER/EXIT) com timestamp
  - token_trades.csv : uma linha por trade fechado, pra analise/tuning

Uso:
    python token_stream.py
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import websockets
from dotenv import load_dotenv

load_dotenv()

# O stream de TRADES exige uma API key do PumpPortal com >= 0.02 SOL depositados.
# Sem ela, o WS so entrega 'subscribeNewToken' (nascimentos), sem momentum.
PUMPPORTAL_API_KEY = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
WS_BASE = "wss://pumpportal.fun/api/data"
WS_URI = f"{WS_BASE}?api-key={PUMPPORTAL_API_KEY}" if PUMPPORTAL_API_KEY else WS_BASE
# modo REAL: TS_LIVE=1 no .env ou flag --live na linha de comando
LIVE = os.environ.get("TS_LIVE", "0") == "1" or "--live" in sys.argv
LOG_FILE = Path(__file__).parent / "token_stream.log"
CSV_FILE = Path(__file__).parent / ("token_trades_live.csv" if LIVE else "token_trades_v2.csv")

# Todos os parametros sao configuraveis por .env (prefixo TS_). Defaults v2 abaixo.
def _f(name, default): return float(os.environ.get(name, default))
def _i(name, default): return int(os.environ.get(name, default))

# ---- FILTROS DE ENTRADA ----------------------------------------------------
ENTRY_TRIGGER_PCT  = _f("TS_ENTRY_TRIGGER_PCT", 20.0)   # entra quando sobe +X% sobre o ref
ENTRY_WINDOW_SEC   = _i("TS_ENTRY_WINDOW_SEC", 45)      # janela pra disparar (era 60)
MIN_BUYERS         = _i("TS_MIN_BUYERS", 8)             # compradores unicos minimos
MAX_AGE_ENTER_SEC  = _i("TS_MAX_AGE_ENTER_SEC", 90)     # idade maxima pra entrar
ANTI_DUMP_PCT      = _f("TS_ANTI_DUMP_PCT", 15.0)       # descarta se cair X% antes de disparar
# corte precoce: token sem tracao e descartado rapido (corta custo do stream)
EARLY_CHECK_SEC    = _i("TS_EARLY_CHECK_SEC", 15)       # apos X s...
EARLY_MIN_BUYERS   = _i("TS_EARLY_MIN_BUYERS", 4)       # ...precisa ter >= Y buyers, senao dropa

# ---- SAIDA POR REVERSAO ----------------------------------------------------
HARD_STOP_PCT      = _f("TS_HARD_STOP_PCT", 12.0)       # stop duro (era 15; mais curto corta duds)
TRAIL_ACTIVATE_PCT = _f("TS_TRAIL_ACTIVATE_PCT", 12.0)  # so ativa o trailing apos +X% de lucro
TRAIL_DROP_PCT     = _f("TS_TRAIL_DROP_PCT", 13.0)      # SAI ao cair X% do pico (era 15)
INACTIVITY_SEC     = _i("TS_INACTIVITY_SEC", 25)        # sem trade ha X s -> sai (token morto)

# ---- CARTEIRA SIMULADA -----------------------------------------------------
STAKE_SOL          = _f("TS_STAKE_SOL", 0.05)
MAX_POSITIONS      = _i("TS_MAX_POSITIONS", 8)
MAX_WATCH          = _i("TS_MAX_WATCH", 60)             # menos assinaturas = menos custo de stream (era 200)

# ---- MODO REAL (live) — travas de seguranca --------------------------------
LIVE_MAX_POSITIONS = _i("TS_LIVE_MAX_POSITIONS", 8)    # menos posicoes no real (exposicao <= 0.30 SOL)
MAX_LOSS_SOL       = _f("TS_MAX_LOSS_SOL", 0.99)       # disjuntor: para apos -X SOL realizado

# ---- MODELO DE ATRITO (custos reais) ---------------------------------------
FEE_PCT_PER_SIDE   = _f("TS_FEE_PCT_PER_SIDE", 1.5)     # taxa pump.fun + PumpPortal por lado
ENTRY_SLIP_PCT     = _f("TS_ENTRY_SLIP_PCT", 2.0)       # latencia+slippage: voce preenche +X% acima do tick
EXIT_SLIP_PCT      = _f("TS_EXIT_SLIP_PCT", 2.0)        # na saida recebe X% abaixo do tick
FIXED_COST_SOL     = _f("TS_FIXED_COST_SOL", 0.0006)    # priority fee + dust de ATA por trade


def now() -> float:
    return time.time()


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


# parametros que a GUI pode reconfigurar antes de iniciar o bot
_CONFIGURABLE = [
    "ENTRY_TRIGGER_PCT", "ENTRY_WINDOW_SEC", "MIN_BUYERS", "MAX_AGE_ENTER_SEC", "ANTI_DUMP_PCT",
    "EARLY_CHECK_SEC", "EARLY_MIN_BUYERS", "HARD_STOP_PCT", "TRAIL_ACTIVATE_PCT", "TRAIL_DROP_PCT",
    "INACTIVITY_SEC", "STAKE_SOL", "MAX_POSITIONS", "MAX_WATCH", "LIVE_MAX_POSITIONS",
    "MAX_LOSS_SOL", "FEE_PCT_PER_SIDE", "ENTRY_SLIP_PCT", "EXIT_SLIP_PCT", "FIXED_COST_SOL", "LIVE",
]


def configure(params: dict):
    """Atualiza os parametros globais a partir de um dict (usado pela GUI)."""
    g = globals()
    for k, v in params.items():
        if k in _CONFIGURABLE:
            g[k] = v


def refresh_env():
    """Re-le segredos do ambiente (apos o cofre injeta-los em os.environ)."""
    global PUMPPORTAL_API_KEY, WS_URI
    PUMPPORTAL_API_KEY = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
    WS_URI = f"{WS_BASE}?api-key={PUMPPORTAL_API_KEY}" if PUMPPORTAL_API_KEY else WS_BASE


class TokenStreamSim:
    def __init__(self, log_callback=None):
        self.ws = None
        self.watching = {}   # mint -> dados em observacao
        self.holding = {}    # mint -> posicao aberta (simulada)
        # estatisticas
        self.n_seen = 0
        self.n_entered = 0
        self.closed = []        # pnl liquido (com atrito) por trade
        self.closed_gross = []  # pnl bruto (so o sinal, sem atrito) por trade
        self._warned_key = False
        self.log_callback = log_callback

        # ---- controle de ciclo de vida (GUI) ----
        self.stop_requested = False
        self.close_on_stop = True   # no real, fecha posicoes ao parar
        self.csv_file = Path(__file__).parent / ("token_trades_live.csv" if LIVE else "token_trades_v2.csv")

        # ---- modo real ----
        self.live = LIVE
        self.executor = None
        self.entering = set()   # mints com COMPRA real em andamento
        self.exiting = set()    # mints com VENDA real em andamento
        self.realized_pnl = 0.0
        self.start_balance = None
        self.halted = False
        if self.live:
            from live_executor import LiveExecutor
            self.executor = LiveExecutor()
            self.start_balance = self.executor.sol_balance()

        self._ensure_csv()

    def request_stop(self):
        self.stop_requested = True

    def get_state(self) -> dict:
        """Snapshot thread-safe pra GUI ler (defensivo contra mutacao concorrente)."""
        try:
            holding = []
            for mint in list(self.holding):
                p = self.holding.get(mint)
                if not p:
                    continue
                entry = p.get("entry_mcap") or 0
                cur = p.get("current") or 0
                ret = (cur / entry - 1) * 100 if entry else 0
                peak = (p.get("peak", 0) / entry - 1) * 100 if entry else 0
                holding.append({"name": p.get("name", "?"), "mint": mint,
                                "ret_pct": ret, "peak_pct": peak})
        except RuntimeError:
            holding = []
        n = len(self.closed)
        wins = sum(1 for x in self.closed if x > 0)
        return {
            "live": self.live,
            "n_seen": self.n_seen, "n_entered": self.n_entered,
            "n_holding": len(self.holding), "n_watching": len(self.watching),
            "n_closed": n, "win_rate": (wins / n * 100) if n else 0,
            "pnl_net": sum(self.closed), "pnl_gross": sum(self.closed_gross),
            "realized_pnl": self.realized_pnl, "halted": self.halted,
            "holding": holding, "running": not self.stop_requested,
        }

    # ---- IO ---------------------------------------------------------------

    def log(self, msg: str):
        line = f"{stamp()}  {msg}"
        print(line, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        if self.log_callback:
            try:
                self.log_callback(line)
            except Exception:
                pass

    def _ensure_csv(self):
        if not self.csv_file.exists():
            with open(self.csv_file, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "exit_time", "mint", "name", "entry_mcap", "peak_mcap", "exit_mcap",
                    "return_pct", "peak_pct", "pnl_net", "pnl_gross", "exit_reason",
                    "buyers_at_entry", "age_at_entry_s", "hold_s",
                ])

    def _csv_row(self, pos, exit_mcap, ret_pct, pnl_net, pnl_gross, reason):
        peak_pct = (pos["peak"] / pos["entry_mcap"] - 1) * 100 if pos["entry_mcap"] else 0
        with open(self.csv_file, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                datetime.now().isoformat(timespec="seconds"), pos["mint"], pos["name"],
                f"{pos['entry_mcap']:.3f}", f"{pos['peak']:.3f}", f"{exit_mcap:.3f}",
                f"{ret_pct:.1f}", f"{peak_pct:.1f}", f"{pnl_net:.5f}", f"{pnl_gross:.5f}", reason,
                pos["buyers_at_entry"], int(pos["age_at_entry"]),
                int(now() - pos["entry_ts"]),
            ])

    # ---- WS subscriptions -------------------------------------------------

    async def _subscribe_trades(self, mint: str):
        if self.ws:
            await self.ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

    async def _unsubscribe_trades(self, mint: str):
        if self.ws:
            await self.ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))

    # ---- handlers ---------------------------------------------------------

    async def _on_create(self, d: dict):
        mint = d.get("mint")
        mcap = d.get("marketCapSol")
        if not mint or not mcap:
            return
        if len(self.watching) + len(self.holding) >= MAX_WATCH:
            return  # cap de carga; ignora ate liberar slot
        if mint in self.watching or mint in self.holding:
            return
        self.n_seen += 1
        self.watching[mint] = {
            "mint": mint,
            "name": d.get("name") or d.get("symbol") or "?",
            "ref_mcap": float(mcap),
            "current": float(mcap),
            "first_ts": now(),
            "last_trade_ts": now(),
            "buyers": {d.get("traderPublicKey")} if d.get("traderPublicKey") else set(),
        }
        await self._subscribe_trades(mint)

    async def _on_trade(self, d: dict):
        mint = d.get("mint")
        mcap = d.get("marketCapSol")
        if not mint or mcap is None:
            return
        mcap = float(mcap)
        t = now()

        if mint in self.holding:
            pos = self.holding[mint]
            pos["current"] = mcap
            pos["last_trade_ts"] = t
            if mcap > pos["peak"]:
                pos["peak"] = mcap
            await self._maybe_exit(mint)
            return

        if mint in self.watching:
            w = self.watching[mint]
            w["current"] = mcap
            w["last_trade_ts"] = t
            if d.get("txType") == "buy" and d.get("traderPublicKey"):
                w["buyers"].add(d["traderPublicKey"])

            # anti-dump: caiu antes de disparar -> descarta
            if mcap <= w["ref_mcap"] * (1 - ANTI_DUMP_PCT / 100):
                await self._drop_watch(mint, "anti-dump")
                return

            ret_pct = (mcap / w["ref_mcap"] - 1) * 100
            age = t - w["first_ts"]
            if (ret_pct >= ENTRY_TRIGGER_PCT
                    and len(w["buyers"]) >= MIN_BUYERS
                    and age <= MAX_AGE_ENTER_SEC):
                await self._enter(mint, mcap, ret_pct, age, len(w["buyers"]))

    async def _enter(self, mint, mcap, ret_pct, age, buyers):
        cap = LIVE_MAX_POSITIONS if self.live else MAX_POSITIONS
        if self.halted or len(self.holding) + len(self.entering) >= cap:
            await self._drop_watch(mint, "sem-slot")
            return
        w = self.watching.pop(mint)

        if self.live:
            # dispara a COMPRA real sem travar o WS; conta o slot via self.entering
            self.entering.add(mint)
            self.log(f"[COMPRA…] {w['name'][:18]:18} {mint[:10]}… +{ret_pct:.0f}% "
                     f"({buyers} buyers, {age:.0f}s) — enviando")
            asyncio.create_task(self._live_buy(mint, w["name"], mcap, age, buyers))
            return

        # atrito de entrada: voce preenche acima do preco do tick (latencia+slippage)
        eff_entry = mcap * (1 + ENTRY_SLIP_PCT / 100)
        self.holding[mint] = {
            "mint": mint, "name": w["name"],
            "entry_mcap": eff_entry, "current": mcap, "peak": mcap,
            "entry_ts": now(), "last_trade_ts": now(),
            "buyers_at_entry": buyers, "age_at_entry": age,
        }
        self.n_entered += 1
        self.log(f"[ENTER ] {w['name'][:20]:20} {mint[:10]}… +{ret_pct:.0f}% "
                 f"({buyers} buyers, {age:.0f}s)  [{len(self.holding)} abertas]")

    # ---- execucao REAL ---------------------------------------------------

    async def _live_buy(self, mint, name, decision_mcap, age, buyers):
        try:
            res = await asyncio.to_thread(self.executor.buy, mint, STAKE_SOL)
        except Exception as e:
            self.log(f"[COMPRA falhou] {mint[:10]}… {str(e)[:70]}")
            self.entering.discard(mint)
            return
        self.entering.discard(mint)
        tokens = res.get("tokens", 0)
        spent = res.get("spent_sol", 0) or STAKE_SOL
        if tokens <= 0:
            self.log(f"[COMPRA] confirmou sem tokens ({mint[:10]}…)")
            return
        # ancora stop/trailing no preco REAL de fill (pump.fun: mcap = preco * 1e9)
        entry_mcap = (spent / tokens) * 1e9
        self.holding[mint] = {
            "mint": mint, "name": name,
            "entry_mcap": entry_mcap, "current": decision_mcap, "peak": decision_mcap,
            "entry_ts": now(), "last_trade_ts": now(),
            "buyers_at_entry": buyers, "age_at_entry": age,
            "tokens": tokens, "spent": spent,
        }
        self.n_entered += 1
        self.log(f"[COMPRA] {name[:18]:18} {mint[:10]}… {spent:.4f} SOL -> {tokens:,.0f} tk "
                 f"sig={res['sig'][:10]}…  [{len(self.holding)} abertas]")

    async def _live_sell(self, mint, reason):
        pos = self.holding.get(mint)
        if not pos:
            self.exiting.discard(mint)
            return
        try:
            res = await asyncio.to_thread(self.executor.sell, mint, pos["tokens"])
        except Exception as e:
            self.log(f"[VENDA falhou] {reason} {mint[:10]}… {str(e)[:60]} — retentara")
            self.exiting.discard(mint)  # fica em holding pra tentar de novo no proximo gatilho
            return
        proceeds = res.get("proceeds_sol", 0)
        pnl = proceeds - pos["spent"]
        self.holding.pop(mint, None)
        self.exiting.discard(mint)
        self.realized_pnl += pnl
        self.closed.append(pnl)
        self.closed_gross.append(pnl)  # no real, bruto = liquido (PnL de verdade)
        ret = (pos["current"] / pos["entry_mcap"] - 1) * 100 if pos["entry_mcap"] else 0
        self._csv_row(pos, pos["current"], ret, pnl, pnl, reason)
        self.log(f"[VENDA ] {pos['name'][:18]:18} {mint[:10]}… {reason} "
                 f"pnl={pnl:+.4f} SOL ({proceeds:.4f}-{pos['spent']:.4f}) "
                 f"sig={res['sig'][:10]}…  realizado={self.realized_pnl:+.4f}")
        await self._unsubscribe_trades(mint)
        self._check_breaker()

    def _check_breaker(self):
        if self.halted or self.realized_pnl > -MAX_LOSS_SOL:
            return
        self.halted = True
        self.log(f"[DISJUNTOR] prejuizo realizado {self.realized_pnl:+.4f} <= -{MAX_LOSS_SOL} SOL. "
                 f"Parando entradas e fechando as {len(self.holding)} posicoes abertas.")
        for mint in list(self.holding):
            asyncio.create_task(self._exit(mint, "disjuntor"))

    async def _maybe_exit(self, mint):
        pos = self.holding[mint]
        entry = pos["entry_mcap"]
        cur = pos["current"]
        ret_pct = (cur / entry - 1) * 100
        peak_pct = (pos["peak"] / entry - 1) * 100
        reason = None

        if ret_pct <= -HARD_STOP_PCT:
            reason = "stop_loss"
        elif peak_pct >= TRAIL_ACTIVATE_PCT and cur <= pos["peak"] * (1 - TRAIL_DROP_PCT / 100):
            reason = "trailing"  # reverteu do pico
        if reason:
            await self._exit(mint, reason)

    async def _exit(self, mint, reason):
        if self.live:
            if mint in self.exiting or mint not in self.holding:
                return  # ja vendendo ou ja vendido
            self.exiting.add(mint)
            asyncio.create_task(self._live_sell(mint, reason))
            return

        pos = self.holding.pop(mint)
        raw_entry = pos["entry_mcap"] / (1 + ENTRY_SLIP_PCT / 100)  # preco do tick (sem slippage)
        raw_exit = pos["current"]
        # BRUTO: so o movimento de preco, sem nenhum atrito
        pnl_gross = STAKE_SOL * (raw_exit / raw_entry - 1)
        # LIQUIDO: com slippage de saida + taxa + custo fixo
        exit_mcap = raw_exit * (1 - EXIT_SLIP_PCT / 100)
        ret = exit_mcap / pos["entry_mcap"] - 1
        notional = STAKE_SOL * (1 + ret)
        fees = STAKE_SOL * FEE_PCT_PER_SIDE / 100 + notional * FEE_PCT_PER_SIDE / 100
        pnl_net = notional - STAKE_SOL - fees - FIXED_COST_SOL
        self.closed.append(pnl_net)
        self.closed_gross.append(pnl_gross)
        self._csv_row(pos, exit_mcap, ret * 100, pnl_net, pnl_gross, reason)
        self.log(f"[EXIT  ] {pos['name'][:20]:20} {mint[:10]}… {reason}  "
                 f"ret={ret * 100:+.0f}% pico={ (pos['peak']/pos['entry_mcap']-1)*100:+.0f}% "
                 f"pnl={pnl_net:+.4f} (bruto {pnl_gross:+.4f})")
        await self._unsubscribe_trades(mint)

    async def _drop_watch(self, mint, why):
        self.watching.pop(mint, None)
        await self._unsubscribe_trades(mint)

    # ---- janitor (timeouts) ----------------------------------------------

    async def _janitor(self):
        last_summary = now()
        while True:
            await asyncio.sleep(3)
            # parada solicitada pela GUI: fecha posicoes (no real) e encerra o WS
            if self.stop_requested:
                if self.live and self.close_on_stop and self.holding:
                    self.log(f"Parando: fechando {len(self.holding)} posicao(oes)…")
                    for mint in list(self.holding):
                        await self._exit(mint, "parada")
                    await asyncio.sleep(8)
                if self.ws:
                    await self.ws.close()
                return
            t = now()
            # observacoes que estouraram a janela OU sem tracao -> descarta (corta custo)
            for mint in list(self.watching):
                w = self.watching[mint]
                age = t - w["first_ts"]
                if age > ENTRY_WINDOW_SEC:
                    await self._drop_watch(mint, "expirou")
                elif age > EARLY_CHECK_SEC and len(w["buyers"]) < EARLY_MIN_BUYERS:
                    await self._drop_watch(mint, "sem-tracao")  # token morto cedo
            # posicoes sem trade ha muito tempo -> token morto
            for mint in list(self.holding):
                if t - self.holding[mint]["last_trade_ts"] > INACTIVITY_SEC:
                    await self._exit(mint, "inativo")
            # resumo periodico
            if t - last_summary > 60:
                last_summary = t
                self._summary()

    def _summary(self):
        n = len(self.closed)
        wins = sum(1 for p in self.closed if p > 0)
        net = sum(self.closed)
        gross = sum(self.closed_gross)
        wr = (wins / n * 100) if n else 0
        self.log(f"[RESUMO] vistos={self.n_seen} entrou={self.n_entered} "
                 f"abertas={len(self.holding)} obs={len(self.watching)} "
                 f"fechados={n} win={wr:.0f}% | bruto(sinal)={gross:+.4f} "
                 f"liquido(c/atrito)={net:+.4f} SOL")

    # ---- loop principal ---------------------------------------------------

    async def run(self):
        self.log("=" * 60)
        if self.live:
            self.log(f"### MODO REAL (LIVE) ### carteira {self.executor.pubkey} "
                     f"saldo {self.start_balance:.4f} SOL")
            self.log(f"stake {STAKE_SOL} | max {LIVE_MAX_POSITIONS} posicoes | "
                     f"disjuntor -{MAX_LOSS_SOL} SOL")
        else:
            self.log("### MODO SIMULADO ###")
        self.log("API key PumpPortal: " + ("presente" if PUMPPORTAL_API_KEY
                 else "AUSENTE — sem stream de trades, nada vai disparar (precisa de key c/ >=0.02 SOL)"))
        self.log(f"Simulador v2. entrada: +{ENTRY_TRIGGER_PCT:.0f}% / janela {ENTRY_WINDOW_SEC}s / "
                 f"min {MIN_BUYERS} buyers (corte precoce <{EARLY_MIN_BUYERS} em {EARLY_CHECK_SEC}s) | "
                 f"saida: trailing -{TRAIL_DROP_PCT:.0f}% do pico (ativa +{TRAIL_ACTIVATE_PCT:.0f}%), "
                 f"stop -{HARD_STOP_PCT:.0f}%, inativo {INACTIVITY_SEC}s")
        self.log(f"atrito: taxa {FEE_PCT_PER_SIDE:.1f}%/lado + slippage entrada {ENTRY_SLIP_PCT:.0f}%/"
                 f"saida {EXIT_SLIP_PCT:.0f}% + fixo {FIXED_COST_SOL:.4f} SOL/trade | "
                 f"stake {STAKE_SOL} x max {MAX_POSITIONS} | watch {MAX_WATCH}")
        while not self.stop_requested:  # reconecta se cair
            try:
                async with websockets.connect(WS_URI, ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    # re-assina trades dos tokens que ja estavam em jogo (apos reconexao)
                    for mint in list(self.watching) + list(self.holding):
                        await self._subscribe_trades(mint)
                    self.log("conectado ao PumpPortal WS.")
                    janitor = asyncio.create_task(self._janitor())
                    try:
                        async for raw in ws:
                            if self.stop_requested:
                                break
                            await self._dispatch(raw)
                    finally:
                        janitor.cancel()
            except Exception as e:
                if self.stop_requested:
                    break
                self.log(f"[WS caiu] {type(e).__name__}: {str(e)[:120]} — reconectando em 5s")
                await asyncio.sleep(5)
        self.log("Bot parado.")

    async def _dispatch(self, raw):
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if "message" in d:  # ack de subscription ou aviso do servidor
            msg = d["message"]
            if "only available" in msg or "api key" in msg.lower():
                if not self._warned_key:
                    self._warned_key = True
                    self.log("[ATENCAO] O stream de TRADES exige PUMPPORTAL_API_KEY com >=0.02 SOL. "
                             "Sem isso so vejo nascimentos e NADA dispara. Veja o README.")
            return
        tx = d.get("txType")
        if tx == "create":
            await self._on_create(d)
        elif tx in ("buy", "sell"):
            await self._on_trade(d)


def main():
    try:
        sim = TokenStreamSim()
    except Exception as e:
        sys.exit(f"[erro ao iniciar] {e}\n"
                 "Modo live precisa de LIVE_RPC valido + LIVE_SEED_PHRASE no .env.")

    if sim.live:
        ex, bal = sim.executor, sim.start_balance
        print("=" * 60)
        print(" MODO REAL (LIVE) — DINHEIRO DE VERDADE")
        print(f" Carteira : {ex.pubkey}")
        print(f" Saldo    : {bal:.4f} SOL")
        print(f" Config   : stake {STAKE_SOL} | max {LIVE_MAX_POSITIONS} pos | disjuntor -{MAX_LOSS_SOL} SOL")
        print("=" * 60)
        if bal < STAKE_SOL * 2 + 0.02:
            sys.exit(f"[erro] saldo insuficiente. Funda a carteira {ex.pubkey} (precisa de "
                     f">= {STAKE_SOL * 2 + 0.02:.3f} SOL).")
        if input(" Digite REAL para confirmar (qualquer outra coisa cancela): ").strip() != "REAL":
            sys.exit("Abortado.")

    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        sim._summary()
        sim.log("Encerrado pelo usuario.")


if __name__ == "__main__":
    main()
