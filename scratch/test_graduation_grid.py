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
    buys = [s[3] or 0 for s in snaps]
    sells = [s[4] or 0 for s in snaps]
    migs = [s[5] or 0.0 for s in snaps]
    
    if len(prices) >= 6 and min(prices) > 0:
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

def eval_exit(fwd, entry_p, tp=50.0, trail_dist=12.0, trail_act=18.0, max_bars=12):
    peak = entry_p
    for idx, p in enumerate(fwd[:max_bars]):
        ret = ((p - entry_p) / entry_p) * 100.0
        if ret <= SL_REAL:
            return SL_REAL, False
        if p > peak:
            peak = p
        peak_gain = ((peak - entry_p) / entry_p) * 100.0
        pullback = ((p - peak) / peak) * 100.0
        if peak_gain >= trail_act and pullback <= -trail_dist:
            return ret, ret > 0
        if ret >= tp:
            return tp, True
    final_ret = ((fwd[min(len(fwd)-1, max_bars-1)] - entry_p) / entry_p) * 100.0
    return final_ret, final_ret > 0

grid_results = []

for min_mig in [35.0, 45.0, 50.0]:
    for min_mc in [25000, 30000, 35000]:
        for min_b in [6, 8, 12]:
            for trail_d in [10.0, 12.0, 15.0]:
                for tp_target in [40.0, 50.0, 60.0]:
                    trades = []
                    for t in tokens:
                        prices = t["prices"]
                        mcs = t["mcs"]
                        migs = t["migs"]
                        buys = t["buys"]
                        for k in range(2, len(prices) - 2):
                            # Evitar BP-007: curva > 85% saturada pre-migración
                            if min_mig <= migs[k] <= 80.0 and mcs[k] >= min_mc:
                                if buys[k] >= min_b and prices[k] > prices[k-1]:
                                    entry_p = prices[k]
                                    fwd = prices[k+1:]
                                    ret, win = eval_exit(fwd, entry_p, tp=tp_target, trail_dist=trail_d, trail_act=15.0)
                                    trades.append((ret, win))
                    
                    if len(trades) >= 30:
                        rets = [tr[0] for tr in trades]
                        wr = sum(1 for tr in trades if tr[1]) / len(trades) * 100
                        avg_ret = float(np.mean(rets))
                        ev = avg_ret - FEE_DRAG
                        gw = sum(r for r in rets if r > 0)
                        gl = abs(sum(r for r in rets if r < 0))
                        pf = (gw / gl) if gl > 0 else 999.0
                        grid_results.append({
                            "mig": min_mig,
                            "mc": min_mc,
                            "buys": min_b,
                            "trail": trail_d,
                            "tp": tp_target,
                            "N": len(trades),
                            "WR": round(wr, 1),
                            "AvgRet": round(avg_ret, 1),
                            "EV": round(ev, 1),
                            "PF": round(pf, 3),
                        })

grid_results.sort(key=lambda x: (x["EV"], x["WR"]), reverse=True)
print(f"\n--- TOP 10 CALIBRACIONES DE GRADUATION RUN (Total evaluadas: {len(grid_results)}) ---")
for c in grid_results[:10]:
    print(f"Mig>={c['mig']}% | MC>={c['mc']} | Buys>={c['buys']:2d} | Trail={c['trail']} TP={c['tp']} | N={c['N']:4d} | WR={c['WR']:5.1f}% | AvgRet={c['AvgRet']:5.1f}% | EV={c['EV']:5.1f}% | PF={c['PF']}")
