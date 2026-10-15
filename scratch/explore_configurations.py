import sqlite3
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from collections import defaultdict
import json

con = sqlite3.connect("file:D:/hjh/pumpscope/db/custom.db?mode=ro", uri=True)
cur = con.cursor()

# Cargar tokens y snapshots
print("[*] Cargando tokens con snapshots en custom.db...")
token_rows = cur.execute("""
    SELECT t.id, t.symbol, t.mint, t.devBalancePct, count(s.id) as cnt
    FROM Token t
    JOIN Snapshot s ON s.tokenId = t.id
    GROUP BY t.id
    HAVING cnt >= 6
    ORDER BY cnt DESC
    LIMIT 4000
""").fetchall()

tokens = []
for t_id, sym, mint, dev_bal, cnt in token_rows:
    snaps = cur.execute("""
        SELECT ts, priceUsd, marketCap, buysM5, sellsM5, migrationProgress
        FROM Snapshot
        WHERE tokenId = ?
        ORDER BY ts ASC
    """, (t_id,)).fetchall()
    
    prices = [s[1] if s[1] and s[1] > 0 else (s[2]/1e9 if s[2] and s[2] > 0 else 0) for s in snaps]
    mcs = [s[2] or 0 for s in snaps]
    buys = [s[3] or 0 for s in snaps]
    sells = [s[4] or 0 for s in snaps]
    migs = [s[5] or 0.0 for s in snaps]
    
    if len(prices) >= 6 and min(prices) > 0:
        tokens.append({
            "id": t_id,
            "symbol": sym,
            "mint": mint,
            "dev_bal": dev_bal or 0.0,
            "prices": np.array(prices, dtype=float),
            "mcs": np.array(mcs, dtype=float),
            "buys": np.array(buys, dtype=int),
            "sells": np.array(sells, dtype=int),
            "migs": np.array(migs, dtype=float),
        })

print(f"[+] {len(tokens)} tokens cargados.")

SL_REAL = -33.49
FEE_DRAG = 6.0

def evaluate_trade(forward_prices, entry_p, trailing_act=18.0, trailing_dist=12.0, max_bars=12, stall_bars=3, stall_min_gain=4.0):
    if len(forward_prices) == 0:
        return 0.0, False
    peak = entry_p
    for idx, p in enumerate(forward_prices[:max_bars]):
        ret = ((p - entry_p) / entry_p) * 100.0
        if ret <= SL_REAL:
            return SL_REAL, False
        if p > peak:
            peak = p
        peak_gain = ((peak - entry_p) / entry_p) * 100.0
        pullback = ((p - peak) / peak) * 100.0
        if peak_gain >= trailing_act and pullback <= -trailing_dist:
            return ret, ret > 0
        if idx >= stall_bars and peak_gain < stall_min_gain:
            # Stall cut
            stall_ret = ret if ret > -10.0 else -28.93
            return stall_ret, stall_ret > 0
        if ret >= 100.0:
            return 100.0, True
    last_p = forward_prices[min(len(forward_prices)-1, max_bars-1)]
    final_ret = ((last_p - entry_p) / entry_p) * 100.0
    return final_ret, final_ret > 0

# Evaluar estrategias en zona dorada (Market Cap actual en el momento de la señal entre 15k y 60k)
configs_results = []

for mc_min, mc_max in [(12000, 60000), (15000, 50000), (20000, 70000)]:
    for min_buys in [6, 8, 12]:
        for trail_act in [15.0, 20.0]:
            for trail_dist in [10.0, 14.0]:
                trades = []
                for t in tokens:
                    prices = t["prices"]
                    mcs = t["mcs"]
                    buys = t["buys"]
                    n = len(prices)
                    for k in range(2, n - 2):
                        # Condición: Market Cap en ese instante k está en la zona dorada
                        if not (mc_min <= mcs[k] <= mc_max):
                            continue
                        # Momentum surge o aceleración de compras
                        p_prev = prices[k-1]
                        gain = ((prices[k] - p_prev) / p_prev) * 100.0
                        if gain >= 12.0 and buys[k] >= min_buys:
                            entry_p = prices[k]
                            fwd = prices[k+1:]
                            ret, win = evaluate_trade(fwd, entry_p, trailing_act=trail_act, trailing_dist=trail_dist)
                            trades.append((ret, win))
                
                if len(trades) >= 25:
                    rets = [tr[0] for tr in trades]
                    wins = sum(1 for tr in trades if tr[1])
                    n_tr = len(trades)
                    wr = (wins / n_tr) * 100.0
                    avg_ret = float(np.mean(rets))
                    ev = avg_ret - FEE_DRAG
                    gw = sum(r for r in rets if r > 0)
                    gl = abs(sum(r for r in rets if r < 0))
                    pf = (gw / gl) if gl > 0 else 999.0
                    configs_results.append({
                        "mc_range": f"{mc_min}-{mc_max}",
                        "min_buys": min_buys,
                        "trail_act": trail_act,
                        "trail_dist": trail_dist,
                        "N": n_tr,
                        "WR": round(wr, 1),
                        "AvgRet": round(avg_ret, 1),
                        "EV": round(ev, 1),
                        "PF": round(pf, 3),
                    })

configs_results.sort(key=lambda x: x["EV"], reverse=True)
print("\n--- TOP CONFIGURACIONES OPTIMIZADAS ---")
for c in configs_results[:15]:
    print(f"MC: {c['mc_range']:12s} | Buys>={c['min_buys']:2d} | TrailAct={c['trail_act']} Dist={c['trail_dist']} | N={c['N']:4d} | WR={c['WR']:5.1f}% | EV={c['EV']:5.1f}% | PF={c['PF']}")
