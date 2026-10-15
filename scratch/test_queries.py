import sqlite3

con = sqlite3.connect("file:D:/hjh/pumpscope/db/custom.db?mode=ro", uri=True)
cur = con.cursor()

whales = cur.execute("SELECT count(*) FROM WhaleSignal WHERE side='BUY'").fetchone()[0]
matched = cur.execute("""
    SELECT count(distinct s.mint) 
    FROM WhaleSignal s 
    JOIN Token t ON t.mint = s.mint 
    JOIN Snapshot sn ON sn.tokenId = t.id 
    WHERE s.side='BUY'
""").fetchone()[0]

print(f"Whale BUY: {whales}, Matched with Snapshots: {matched}")

clean = cur.execute("""
    SELECT count(*) 
    FROM WhaleSignal s 
    JOIN Token t ON t.mint = s.mint 
    JOIN Snapshot sn ON sn.tokenId = t.id 
    WHERE s.side='BUY' AND s.solAmount >= 2.0
""").fetchone()[0]

print(f"Clean Whales solAmount >= 2.0: {clean}")

# Dev balance and insider holding
dev_info = cur.execute("""
    SELECT count(*), avg(t.devBalancePct), avg(t.insiderHoldingPct)
    FROM Token t
    WHERE t.devBalancePct IS NOT NULL
""").fetchone()
print(f"Tokens with dev info: {dev_info}")

# Check sample values of devBalancePct
samples = cur.execute("SELECT devBalancePct, insiderHoldingPct, bundlerHoldingPct FROM Token WHERE devBalancePct > 0 LIMIT 10").fetchall()
print(f"Sample devBalancePct: {samples}")
