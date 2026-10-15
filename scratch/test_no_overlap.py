import sqlite3
import numpy as np

con = sqlite3.connect("file:D:/hjh/pumpscope/db/custom.db?mode=ro", uri=True)
cur = con.cursor()

token_rows = cur.execute("""
    SELECT t.id, t.symbol, count(s.id) as cnt
    FROM Token t
    JOIN Snapshot s ON s.tokenId = t.id
    GROUP BY t.id
    HAVING cnt >= 6
    ORDER BY cnt DESC
    LIMIT 4000
""").fetchall()

tokens = []
for t_id, sym, cnt in token_rows:
    snaps = cur.execute("""
        SELECT ts, priceUsd, marketCap, buysM5, sellsM5, migrationProgress
        FROM Snapshot
        WHERE tokenId = ?
        ORDER BY ts ASC
    """, (t_id,)).fetchall()
    
    prices = [s[1] if s[1] and s[1] > 0 else (s[2]/1e9 if s[2] and s[2] > 0 else 0) for s in snaps]
    mcs = [s[2] or 0 for s in snaps]
    migs = [s[5] or 0.0 for s in snaps]
    buys = [s[3] or 0 for s in snaps]
    
    if len(prices) >= 6 and min(prices) > 0:
        tokens.append({
            "id": t_id,
            "symbol": sym,
            "prices": np.array(prices, dtype=float),
            "mcs": np.array(mcs, dtype=float),
            "buys": np.array(buys, dtype=int),
            "migs": np.array(migs, dtype=float),
        })

SL_REAL = -33.49
FEE_DRAG = 6.0

def eval_trade(fwd, entry_p, trail_act=20.0, trail_dist=15.0, tp=50.0, max_bars=12):
    peak = entry_p
    for idx, p in enumerate(fwd[:max_bars]):
        ret = ((p - entry_p) / entry_p) * 100.0
        if ret <= SL_REAL:
            return SL_REAL, False, idx + 1
        if p > peak:
            peak = p
        peak_gain = ((peak - entry_p) / entry_p) * 100.0
        pullback = ((p - peak) / peak) * 100.0
        if peak_gain >= trail_act and pullback <= -trail_dist:
            return ret, ret > 0, idx + 1
        if ret >= tp:
            return tp, True, idx + 1
    last_idx = min(len(fwd) - 1, max_bars - 1)
    final_ret = ((fwd[last_idx] - entry_p) / entry_p) * 100.0
    return final_ret, final_ret > 0, last_idx + 1

# Probar varias estrategias con NO-OVERLAP ESTRICTO
strategies = {
    "1. Grad_Run (Mig>=40%, MC>=25k, Buys>=6)": lambda t, k: t["migs"][k] >= 40.0 and t["mcs"][k] >= 25000 and t["buys"][k] >= 6 and t["prices"][k] > t["prices"][k-1],
    "2. MidCap_Volume_Surge (MC 15k-60k, Buys>=10, +15%)": lambda t, k: 15000 <= t["mcs"][k] <= 60000 and t["buys"][k] >= 10 and ((t["prices"][k]-t["prices"][k-1])/t["prices"][k-1])*100 >= 15.0,
    "3. Dip_Bounce (MC>=15k, Pullback -15% a -35%, Bounce +5%)": lambda t, k: t["mcs"][k] >= 15000 and -35.0 <= ((t["prices"][k-1] - np.max(t["prices"][:k])) / np.max(t["prices"][:k]))*100 <= -15.0 and ((t["prices"][k]-t["prices"][k-1])/t["prices"][k-1])*100 >= 5.0 and t["buys"][k] >= 6,
    "4. Clean_Momentum (MC 12k-45k, Buys>=8, Change>=10%)": lambda t, k: 12000 <= t["mcs"][k] <= 45000 and t["buys"][k] >= 8 and ((t["prices"][k]-t["prices"][k-1])/t["prices"][k-1])*100 >= 10.0,
    "5. Early_Base_Breakout (MC 10k-30k, 3 bars flat <6%, Breakout +8%)": lambda t, k: k >= 3 and 10000 <= t["mcs"][k] <= 30000 and (np.max(t["prices"][k-3:k]) - np.min(t["prices"][k-3:k]))/np.min(t["prices"][k-3:k])*100 < 6.0 and ((t["prices"][k]-t["prices"][k-1])/t["prices"][k-1])*100 >= 8.0 and t["buys"][k] >= 6,
}

print("\n--- RESULTADOS CON REGLA ESTRICTA DE 1 POSICIÓN POR TOKEN (NO-OVERLAP) ---")
for s_name, cond in strategies.items():
    trades = []
    for t in tokens:
        prices = t["prices"]
        n = len(prices)
        k = 1
        while k < n - 2:
            if cond(t, k):
                entry_p = prices[k]
                fwd = prices[k+1:]
                ret, win, bars_held = eval_trade(fwd, entry_p)
                trades.append((ret, win, t["symbol"]))
                k += bars_held  # SALTO DE BARRAS: No se puede entrar mientras la posición está abierta
            else:
                k += 1
    
    if len(trades) >= 5:
        rets = [tr[0] for tr in trades]
        wr = sum(1 for tr in trades if tr[1]) / len(trades) * 100
        avg_ret = float(np.mean(rets))
        ev = avg_ret - FEE_DRAG
        gw = sum(r for r in rets if r > 0)
        gl = abs(sum(r for r in rets if r < 0))
        pf = (gw / gl) if gl > 0 else 999.0
        print(f"[{s_name:55s}] N={len(trades):3d} | WR={wr:5.1f}% | AvgRet={avg_ret:5.1f}% | EV={ev:5.1f}% | PF={pf:5.3f}")
