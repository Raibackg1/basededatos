import sqlite3
import os
conn = sqlite3.connect(os.environ.get("PUMPSCOPE_DB", "custom.db"))
cursor = conn.cursor()

# Sample tokens with correct column names
cursor.execute("SELECT mint, name, symbol, createdAtChain, creator, firstMarketCap, firstLiquidity FROM Token LIMIT 5")
print("=== Sample tokens ===")
for row in cursor.fetchall():
    print(f'  {row}')

# PaperPosition closed stats
cursor.execute("""
    SELECT reason, COUNT(*) as n,
           SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END) as wins,
           ROUND(SUM(pnlUsd),2) as pnl_usd,
           ROUND(100.0*SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END)/COUNT(*),2) as wr
    FROM PaperPosition WHERE status='CLOSED' GROUP BY reason
""")
print("\n=== PaperPosition by reason ===")
for row in cursor.fetchall():
    print(f'  {row}')

# Total stats
cursor.execute("""
    SELECT COUNT(*) as n,
           SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END) as wins,
           ROUND(SUM(pnlUsd),2) as pnl_usd,
           ROUND(100.0*SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END)/COUNT(*),2) as wr,
           ROUND(SUM(CASE WHEN pnlUsd>0 THEN pnlUsd END)/NULLIF(ABS(SUM(CASE WHEN pnlUsd<0 THEN pnlUsd END)),0),3) as pf
    FROM PaperPosition WHERE status='CLOSED'
""")
row = cursor.fetchone()
print(f"\n=== TOTAL: n={row[0]}, wins={row[1]}, pnl={row[2]}, wr={row[3]}%, pf={row[4]} ===")

# Resolution check
cursor.execute("""
    SELECT ROUND(AVG(gap)/1000.0,2) FROM (
        SELECT (julianday(ts) - julianday(LAG(ts) OVER (PARTITION BY tokenId ORDER BY ts))) * 86400.0 gap 
        FROM Snapshot
    ) WHERE gap>0
""")
res = cursor.fetchone()[0]
print(f"\n=== Snapshot interval mean: {res} s ===")

# Tokens that hit +50%
cursor.execute("SELECT COUNT(*) FROM Token WHERE rise50At IS NOT NULL")
hit50 = cursor.fetchone()[0]
total = cursor.execute("SELECT COUNT(*) FROM Token").fetchone()[0]
print(f"Tokens hitting +50%: {hit50}/{total} = {100*hit50/total:.2f}%")

conn.close()