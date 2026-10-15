import sqlite3
import numpy as np

con = sqlite3.connect("file:D:/hjh/pumpscope/db/custom.db?mode=ro", uri=True)
cur = con.cursor()

token_rows = cur.execute("""
    SELECT t.id, t.symbol, count(s.id) as cnt
    FROM Token t
    JOIN Snapshot s ON s.tokenId = t.id
    GROUP BY t.id
    HAVING cnt >= 8
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
    buys = [s[3] or 0 for s in snaps]
    sells = [s[4] or 0 for s in snaps]
    migs = [s[5] or 0.0 for s in snaps]
    
    if len(prices) >= 8 and min(prices) > 0:
        tokens.append({
            "id": t_id,
            "prices": np.array(prices, dtype=float),
            "mcs": np.array(mcs, dtype=float),
            "buys": np.array(buys, dtype=int),
            "sells": np.array(sells, dtype=int),
            "migs": np.array(migs, dtype=float),
        })

SL_REAL = -33.49
FEE_DRAG = 6.0

def eval_exit(fwd, entry_p, tp=40.0, sl=SL_REAL, trail_dist=12.0):
    peak = entry_p
    for idx, p in enumerate(fwd[:12]):
        ret = ((p - entry_p) / entry_p) * 100.0
        if ret <= sl:
            return sl, False
        if p > peak:
            peak = p
        peak_gain = ((peak - entry_p) / entry_p) * 100.0
        pullback = ((p - peak) / peak) * 100.0
        if peak_gain >= 20.0 and pullback <= -trail_dist:
            return ret, ret > 0
        if ret >= tp:
            return tp, True
    final_ret = ((fwd[min(len(fwd)-1, 11)] - entry_p) / entry_p) * 100.0
    return final_ret, final_ret > 0

results = []

# TEST 1: PULLBACK BOUNCE (Rebote tras caída del 15-30% en token con MC > 15k)
pb_trades = []
for t in tokens:
    prices = t["prices"]
    mcs = t["mcs"]
    buys = t["buys"]
    for k in range(3, len(prices) - 2):
        if mcs[k] < 12000:
            continue
        p_peak = np.max(prices[:k])
        drop = ((prices[k-1] - p_peak) / p_peak) * 100.0
        # Tuvo una caída entre -15% y -40% desde su pico reciente
        if -40.0 <= drop <= -15.0:
            # Y la barra k rebota (verde) con compras activas
            bounce = ((prices[k] - prices[k-1]) / prices[k-1]) * 100.0
            if bounce >= 5.0 and buys[k] >= 4:
                entry_p = prices[k]
                fwd = prices[k+1:]
                ret, win = eval_exit(fwd, entry_p)
                pb_trades.append((ret, win))

if pb_trades:
    rets = [r[0] for r in pb_trades]
    wr = sum(1 for r in pb_trades if r[1]) / len(pb_trades) * 100
    avg_ret = float(np.mean(rets))
    ev = avg_ret - FEE_DRAG
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    pf = (gw / gl) if gl > 0 else 999.0
    results.append(("PULLBACK_DIP_BOUNCE", len(pb_trades), wr, avg_ret, ev, pf))

# TEST 2: CONSOLIDATION BASE BREAKOUT (Ruptura tras compresión)
cb_trades = []
for t in tokens:
    prices = t["prices"]
    mcs = t["mcs"]
    buys = t["buys"]
    for k in range(4, len(prices) - 2):
        if mcs[k] < 15000:
            continue
        window = prices[k-4:k]
        rng = (np.max(window) - np.min(window)) / np.min(window) * 100.0
        # Rango comprimido < 8% durante 4 barras
        if rng <= 8.0:
            # Ruptura hacia arriba con expansión de compras
            breakout = ((prices[k] - np.max(window)) / np.max(window)) * 100.0
            if breakout >= 6.0 and buys[k] >= 8:
                entry_p = prices[k]
                fwd = prices[k+1:]
                ret, win = eval_exit(fwd, entry_p)
                cb_trades.append((ret, win))

if cb_trades:
    rets = [r[0] for r in cb_trades]
    wr = sum(1 for r in cb_trades if r[1]) / len(cb_trades) * 100
    avg_ret = float(np.mean(rets))
    ev = avg_ret - FEE_DRAG
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    pf = (gw / gl) if gl > 0 else 999.0
    results.append(("CONSOLIDATION_BREAKOUT", len(cb_trades), wr, avg_ret, ev, pf))

# TEST 3: HIGH LIQUIDITY GRADUATION RUN (Curva > 50%, MC > 35k)
grad_trades = []
for t in tokens:
    prices = t["prices"]
    mcs = t["mcs"]
    migs = t["migs"]
    buys = t["buys"]
    for k in range(2, len(prices) - 2):
        if migs[k] >= 45.0 and mcs[k] >= 30000:
            if buys[k] >= 8 and prices[k] > prices[k-1]:
                entry_p = prices[k]
                fwd = prices[k+1:]
                ret, win = eval_exit(fwd, entry_p, tp=50.0, trail_dist=15.0)
                grad_trades.append((ret, win))

if grad_trades:
    rets = [r[0] for r in grad_trades]
    wr = sum(1 for r in grad_trades if r[1]) / len(grad_trades) * 100
    avg_ret = float(np.mean(rets))
    ev = avg_ret - FEE_DRAG
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    pf = (gw / gl) if gl > 0 else 999.0
    results.append(("GRADUATION_CONTINUATION", len(grad_trades), wr, avg_ret, ev, pf))

print("\n--- RESULTADOS DE HIPÓTESIS AVANZADAS ---")
for name, n, wr, avg_ret, ev, pf in results:
    print(f"[{name:25s}] N={n:4d} | WR={wr:5.1f}% | AvgRet={avg_ret:5.1f}% | EV={ev:5.1f}% | PF={pf:5.3f}")
