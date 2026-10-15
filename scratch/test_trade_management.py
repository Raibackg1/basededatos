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

def eval_advanced_exit(fwd, entry_p, be_act=12.0, trail_act=20.0, trail_dist=12.0, tp1=30.0, tp2=60.0, max_bars=12):
    """
    Gestión de trade profesional:
    - SL inicial a -33.49% (slippage real de pánico).
    - Si la ganancia supera 'be_act' (+12%), el stop se mueve a BREAKEVEN (+1.0% para cubrir comisiones).
    - Trailing stop una vez superado 'trail_act'.
    - Toma de beneficios parcial o plena en TP1/TP2.
    """
    peak = entry_p
    current_sl = SL_REAL
    is_be_activated = False
    
    for idx, p in enumerate(fwd[:max_bars]):
        ret = ((p - entry_p) / entry_p) * 100.0
        
        # Chequeo de Stop Loss actual (fijo o breakeven)
        if ret <= current_sl:
            return current_sl, current_sl > 0, idx + 1
            
        if p > peak:
            peak = p
            
        peak_gain = ((peak - entry_p) / entry_p) * 100.0
        pullback = ((p - peak) / peak) * 100.0
        
        # Mover a Breakeven si supera el umbral
        if peak_gain >= be_act and not is_be_activated:
            current_sl = 1.0  # Asegura breakeven positivo
            is_be_activated = True
            
        # Trailing stop si ya estuvo alto
        if peak_gain >= trail_act and pullback <= -trail_dist:
            return ret, ret > 0, idx + 1
            
        # Take Profit 2 (pleno)
        if ret >= tp2:
            return tp2, True, idx + 1
            
    last_idx = min(len(fwd) - 1, max_bars - 1)
    final_ret = ((fwd[last_idx] - entry_p) / entry_p) * 100.0
    return final_ret, final_ret > 0, last_idx + 1

# Probar en Grad_Run y Dip_Bounce
strategies = [
    ("Grad_Run (Mig 40-75%, MC>=25k, Buys>=6)", lambda t, k: 40.0 <= t["migs"][k] <= 75.0 and t["mcs"][k] >= 25000 and t["buys"][k] >= 6 and t["prices"][k] > t["prices"][k-1]),
    ("Grad_Run (Mig 50-80%, MC>=30k, Buys>=8)", lambda t, k: 50.0 <= t["migs"][k] <= 80.0 and t["mcs"][k] >= 30000 and t["buys"][k] >= 8 and t["prices"][k] > t["prices"][k-1]),
    ("Dip_Bounce (MC>=15k, Dip -15% a -35%, Bounce +5%)", lambda t, k: t["mcs"][k] >= 15000 and -35.0 <= ((t["prices"][k-1] - np.max(t["prices"][:k])) / np.max(t["prices"][:k]))*100 <= -15.0 and ((t["prices"][k]-t["prices"][k-1])/t["prices"][k-1])*100 >= 5.0 and t["buys"][k] >= 6),
    ("Candle_Hammer_MidCap (MC 15k-60k, Buys>=6, Hammer)", lambda t, k: 15000 <= t["mcs"][k] <= 60000 and t["buys"][k] >= 6 and t["prices"][k-1] < t["prices"][k-2] and t["prices"][k] > t["prices"][k-1] and 0.03 <= (t["prices"][k]-t["prices"][k-1])/t["prices"][k-1] <= 0.15),
    ("Candle_Engulfing_MidCap (MC 15k-60k, Buys>=6, Engulfing)", lambda t, k: 15000 <= t["mcs"][k] <= 60000 and t["buys"][k] >= 6 and t["prices"][k-1] < t["prices"][k-2] and t["prices"][k] > t["prices"][k-2]),
]

print("\n--- EVALUACIÓN CON GESTIÓN DE BREAKEVEN STOP DINÁMICO ---")
for s_name, cond in strategies:
    for be_threshold in [10.0, 15.0]:
        for trail_d in [10.0, 15.0]:
            trades = []
            for t in tokens:
                prices = t["prices"]
                n = len(prices)
                k = 1
                while k < n - 2:
                    if cond(t, k):
                        entry_p = prices[k]
                        fwd = prices[k+1:]
                        ret, win, bars = eval_advanced_exit(fwd, entry_p, be_act=be_threshold, trail_act=20.0, trail_dist=trail_d, tp2=60.0)
                        trades.append((ret, win))
                        k += bars
                    else:
                        k += 1
            if len(trades) >= 20:
                rets = [tr[0] for tr in trades]
                wins = sum(1 for tr in trades if tr[1])
                wr = (wins / len(trades)) * 100.0
                avg_ret = float(np.mean(rets))
                # Evaluamos con comisiones de 0.25 SOL (~37.5 USD -> 3.2% fee drag) y 0.5 SOL (~75 USD -> 1.6% fee drag)
                ev_025 = avg_ret - 3.2
                ev_050 = avg_ret - 1.6
                gw = sum(r for r in rets if r > 0)
                gl = abs(sum(r for r in rets if r < 0))
                pf = (gw / gl) if gl > 0 else 999.0
                if wr >= 45.0 or ev_050 > 0:
                    print(f"[{s_name[:40]:40s}] BE={be_threshold}% T={trail_d}% | N={len(trades):3d} | WR={wr:5.1f}% | AvgRet={avg_ret:5.1f}% | EV(0.5SOL)={ev_050:5.1f}% | PF={pf:5.3f}")
