"""
Generador de base de datos SINTÉTICA de demostración (estilo pump.fun).

Crea demo_pumpfun.db con tablas: tokens, trades, my_trades.
Los datos contienen patrones "ocultos" a propósito para que el detector
los redescubra y puedas validar que la herramienta funciona:

  P1. Tokens donde el dev compra >= 2 SOL gradúan/ganan mucho más.
  P2. Más compradores únicos en los primeros 60s => mejor rendimiento.
  P3. Entrar en los primeros <10s (snipe ultra-temprano) => peor winrate
      (el dev y otros snipers usan esa liquidez para salir).
  P4. Tokens con Twitter + website rinden mejor.
  P5. Tokens creados de madrugada (UTC) rinden peor.
  P6. Alta concentración del top-5 de wallets en 60s => peor (wash/inner circle).
"""
import random
import sqlite3

import numpy as np
import pandas as pd

RNG = np.random.default_rng(7)

ADJ = ["Moon", "Degen", "Chad", "Based", "Turbo", "Giga", "Sigma", "Alpha", "Hyper", "Mega",
       "Cosmic", "Sneaky", "Fat", "Dark", "Golden", "Electric", "Silent", "Angry", "Lucky", "Crazy"]
NOUN = ["Doge", "Pepe", "Cat", "Frog", "Ape", "Whale", "Shark", "Bull", "Bear", "Wolf",
        "Robot", "GPT", "Agent", "Inu", "Coin", "Sol", "Moon", "Trump", "Elon", "Chad"]
SUF = ["", "2.0", "X", "AI", "SOL", "Inu", "Fi", "", "3.0", "Max"]


def _name():
    a, n, s = RNG.choice(ADJ), RNG.choice(NOUN), RNG.choice(SUF)
    name = f"{a} {n}{(' ' + s) if s else ''}"
    sym = (a[:2] + n[:3] + s[:2]).upper()
    if RNG.random() < 0.15:
        sym += str(RNG.integers(0, 99))
    return name, sym


def _wallet():
    return "".join(RNG.choice(list("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"), 44))


def simulate_token(t0: float, regime: str, n_buyers_60s: int, dev_buy_sol: float, dev_wallet: str):
    """Simula la ruta de trades de un token. Devuelve lista de trades y flags."""
    trades = []
    # Escala de precio realista pump.fun: supply 1e9, MC inicial ~$4,500
    # (el generador anterior usaba 3e-8 => FDV $30, invisible para los umbrales
    # auditados de la Señal Maestra y causaba veto DEAD_LAUNCH del 100%).
    # Los retornos son invariantes a la escala, solo cambia el market_cap.
    price = 4.5e-6 * (1 + RNG.normal(0, 0.1))
    p0 = price

    if regime == "moon":
        dur, peak_mult, end_frac = RNG.uniform(40, 120) * 60, RNG.uniform(8, 40), RNG.uniform(0.45, 0.9)
    elif regime == "pump_dump":
        dur, peak_mult, end_frac = RNG.uniform(8, 25) * 60, RNG.uniform(2.5, 8), RNG.uniform(0.05, 0.25)
    elif regime == "slow_bleed":
        dur, peak_mult, end_frac = RNG.uniform(15, 45) * 60, RNG.uniform(1.2, 2.5), RNG.uniform(0.15, 0.45)
    else:  # fast_rug
        dur, peak_mult, end_frac = RNG.uniform(2, 8) * 60, RNG.uniform(1.1, 3), RNG.uniform(0.01, 0.08)

    peak_t = dur * RNG.uniform(0.15, 0.5) if regime != "moon" else dur * RNG.uniform(0.4, 0.8)

    def price_at(t):
        if t <= peak_t:
            frac = t / max(peak_t, 1e-9)
            return p0 * (peak_mult ** frac) * np.exp(RNG.normal(0, 0.03))
        frac = (t - peak_t) / max(dur - peak_t, 1e-9)
        frac = min(frac, 1.0)
        target = p0 * peak_mult * end_frac
        return max(target, p0 * peak_mult * (end_frac / max(peak_mult, 1e-9)) ** (frac * peak_mult / 2)) \
            * np.exp(RNG.normal(0, 0.04))

    n_trades = int(RNG.uniform(120, 260) if regime == "moon" else RNG.uniform(50, 150))
    ts = np.sort(RNG.uniform(0, dur, n_trades))
    # sesgo: más trades al principio (actividad de lanzamiento)
    ts = np.sort(np.concatenate([ts[: n_trades // 2], ts[: n_trades // 2] * RNG.uniform(0.05, 0.2)]))
    wallets = [_wallet() for _ in range(max(8, int(n_buyers_60s * RNG.uniform(1.5, 3))))]
    # algunos wallets "insiders" repiten mucho al inicio (concentración)
    insiders = wallets[: max(2, len(wallets) // 12)]
    size_mu = {"moon": -0.5, "pump_dump": -1.1, "slow_bleed": -1.6, "fast_rug": -1.7}[regime]
    net_sol = 0.0
    graduated = 0
    for t in ts:
        p = max(price_at(t), 1e-12)
        is_buy = RNG.random() < (0.72 if t < 60 else (0.58 if t < peak_t else 0.3))
        if RNG.random() < 0.12 and t < 90:
            w = RNG.choice(insiders)
            is_buy = True
        else:
            w = RNG.choice(wallets)
        sol = float(np.clip(RNG.lognormal(size_mu, 1.05), 0.02, 8.0))
        if is_buy:
            net_sol += sol
        else:
            net_sol -= sol * 0.85
            sol = sol * 0.8
        if net_sol >= 85 and not graduated:
            graduated = 1
        tok_amount = sol / p
        trades.append((t0 + t, w, "buy" if is_buy else "sell", round(sol, 6),
                       round(tok_amount, 2), p))
    # compra del dev
    dev_trades = []
    if dev_buy_sol > 0:
        tdev = RNG.uniform(0.5, 8)
        pdev = max(price_at(tdev), 1e-12)
        dev_trades.append((t0 + tdev, dev_wallet, "buy", dev_buy_sol, dev_buy_sol / pdev, pdev))
        net_sol += dev_buy_sol
        # el dev vende según régimen
        if regime in ("fast_rug", "pump_dump") or RNG.random() < 0.4:
            n_sells = RNG.integers(1, 4)
            for k in range(n_sells):
                tsell = tdev + RNG.uniform(60, min(dur, 30 * 60)) * RNG.uniform(0.2, 0.8)
                ps = max(price_at(tsell), 1e-12)
                amt = dev_buy_sol / n_sells * RNG.uniform(0.7, 1.2) / ps
                dev_trades.append((t0 + tsell, dev_wallet, "sell", amt * ps, amt, ps))
        if net_sol >= 85:
            graduated = 1
    all_trades = sorted(trades + dev_trades, key=lambda x: x[0])
    # graduación: curva completa (85 SOL netos) o moon convincente
    if regime == "moon" and RNG.random() < 0.65:
        graduated = 1
    path_t = np.array([x[0] - t0 for x in all_trades])
    path_p = np.array([x[5] for x in all_trades])
    return all_trades, path_t, path_p, graduated


def generate_demo_db(db_path: str, n_tokens: int = 1200, verbose: bool = True) -> str:
    now = pd.Timestamp.now(tz="UTC").timestamp()
    tokens_rows, trades_rows, my_rows = [], [], []
    wallets_dev = [_wallet() for _ in range(n_tokens)]

    for k in range(n_tokens):
        t0 = now - RNG.uniform(1, 60) * 86400  # últimos 60 días
        # hora: 25% de madrugada UTC (0-6h) -> patrón P5
        hour = RNG.integers(0, 6) if RNG.random() < 0.25 else RNG.integers(6, 24)
        ts_created = t0
        dt = pd.Timestamp(ts_created, unit="s", tz="UTC").replace(hour=int(hour), minute=int(RNG.integers(0, 60)))
        ts_created = dt.timestamp()

        mint = _wallet()
        name, sym = _name()
        dev = wallets_dev[k]
        has_tw = RNG.random() < 0.55
        has_tg = RNG.random() < 0.35
        has_web = RNG.random() < 0.40

        # dev compra (P1): 40% de los devs compran; algunos fuerte
        r = RNG.random()
        dev_buy = 0.0 if r < 0.6 else (float(RNG.uniform(0.3, 2.0)) if r < 0.85 else float(RNG.uniform(2.0, 5.0)))

        # probabilidad de "moon" influenciada por los patrones ocultos
        p_moon = 0.10
        if dev_buy >= 2.0:
            p_moon += 0.25                      # P1
        if has_tw and has_web:
            p_moon += 0.07                      # P4
        if int(dt.hour) < 6:
            p_moon -= 0.05                      # P5
        p_moon = float(np.clip(p_moon, 0.01, 0.6))
        u = RNG.random()
        if u < p_moon:
            regime = "moon"
        elif u < p_moon + 0.22:
            regime = "pump_dump"
        elif u < p_moon + 0.55:
            regime = "slow_bleed"
        else:
            regime = "fast_rug"

        # compradores 60s correlacionados con régimen (P2)
        mu = {"moon": 55, "pump_dump": 34, "slow_bleed": 18, "fast_rug": 13}[regime]
        n_buyers_60s = max(4, int(RNG.normal(mu, mu * 0.3)))

        all_trades, path_t, path_p, graduated = simulate_token(ts_created, regime, n_buyers_60s, dev_buy, dev)

        # concentración insiders (P6): a veces un token con muchas compras es wash
        wash = RNG.random() < 0.15

        tokens_rows.append((
            mint, name, sym, dt.isoformat(), dev,
            "https://x.com/" + sym.lower() if has_tw else None,
            "https://t.me/" + sym.lower() if has_tg else None,
            "https://" + sym.lower() + ".fun" if has_web else None,
            1_000_000_000, int(graduated),
        ))
        sig_ctr = 0
        for (ts, w, side, sol, amt, p) in all_trades:
            sig_ctr += 1
            wallet_used = insiders_pick(w, wash, RNG, all_trades) if wash else w
            trades_rows.append((
                mint, wallet_used, side, round(sol, 6), round(amt, 2), float(p),
                pd.Timestamp(ts, unit="s", tz="UTC").isoformat(),
                f"{sig_ctr:04d}{_wallet()[:40]}",
            ))

        # ---- my_trades: el usuario snipea ~55% de los tokens ----
        if RNG.random() < 0.55 and len(path_t) > 10:
            # preferencia por tokens con actividad temprana alta (sesgo realista)
            early = int((path_t <= 60).sum())
            if RNG.random() < min(0.97, early / 55.0):
                # P3: a veces entra ultra-temprano (<10s), a veces más tarde
                delay = float(RNG.uniform(2, 10)) if RNG.random() < 0.35 else float(RNG.uniform(10, 240))
                i = int(np.searchsorted(path_t, delay, side="right")) - 1
                i = max(i, 0)
                e_rel, e_price = path_t[i], path_p[i] * (1 + RNG.normal(0.005, 0.01))
                if e_price <= 0:
                    continue
                e_time = ts_created + e_rel
                size = float(np.clip(RNG.lognormal(-0.9, 0.6), 0.08, 2.5))
                # política de salida del usuario: TP/SL personales + pánico manual
                tp_u = RNG.uniform(0.35, 1.5)
                sl_u = RNG.uniform(0.25, 0.6)
                exit_rel, exit_price = None, None
                for j in range(i, len(path_t)):
                    rel = path_p[j] / e_price - 1.0
                    if rel >= tp_u or rel <= -sl_u:
                        exit_rel, exit_price = path_t[j], e_price * (1 + rel)
                        break
                    if RNG.random() < 0.004:  # salida manual aleatoria
                        exit_rel, exit_price = path_t[j], path_p[j]
                        break
                if exit_price is None:
                    exit_rel, exit_price = path_t[-1], path_p[-1]
                fee = size * 0.01 + size * (exit_price / e_price) * 0.01
                pnl = size * (exit_price / e_price - 1.0) - fee
                my_rows.append((
                    mint,
                    pd.Timestamp(e_time, unit="s", tz="UTC").isoformat(), float(e_price),
                    pd.Timestamp(ts_created + exit_rel, unit="s", tz="UTC").isoformat(), float(exit_price),
                    round(size, 4), round(pnl, 4), round((exit_price / e_price - 1.0) * 100, 2),
                    "closed",
                ))
        if verbose and (k + 1) % 300 == 0:
            print(f"  demo: {k+1}/{n_tokens} tokens simulados...")

    con = sqlite3.connect(db_path)
    pd.DataFrame(tokens_rows, columns=[
        "mint", "name", "symbol", "created_at", "dev_wallet",
        "twitter", "telegram", "website", "total_supply", "complete",
    ]).to_sql("tokens", con, index=False, if_exists="replace")
    pd.DataFrame(trades_rows, columns=[
        "token_mint", "wallet", "side", "sol_amount", "token_amount",
        "price", "block_time", "tx_signature",
    ]).to_sql("trades", con, index=False, if_exists="replace")
    pd.DataFrame(my_rows, columns=[
        "token_mint", "entry_time", "entry_price", "exit_time", "exit_price",
        "size_sol", "pnl_sol", "pnl_pct", "status",
    ]).to_sql("my_trades", con, index=False, if_exists="replace")
    con.close()
    if verbose:
        print(f"  demo: {len(tokens_rows):,} tokens · {len(trades_rows):,} trades · {len(my_rows):,} posiciones propias -> {db_path}")
    return db_path


def insiders_pick(w, wash, rng, all_trades):
    """En tokens 'wash', muchas compras provienen de pocas wallets (P6)."""
    if wash and rng.random() < 0.5:
        return all_trades[int(rng.integers(0, 5))][1]
    return w


if __name__ == "__main__":
    generate_demo_db("demo_pumpfun.db")
