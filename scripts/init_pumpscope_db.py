#!/usr/bin/env python3
"""
Inicializa una base de datos compatible (PUMPSCOPE_DB) a partir de demo_pumpfun.db.
Crea las tablas esperadas: Token, PaperPosition, Snapshot, y pobla con datos demo.
"""
import sqlite3, os, shutil
from pathlib import Path

SRC_DB = Path(os.environ.get("PUMPSCOPE_DEMO_DB", "pumpfun_detector/demo_pumpfun.db"))
DST_DB = Path(os.environ.get("PUMPSCOPE_DB", "custom.db"))  # Local custom.db

print(f"Copiando {SRC_DB} -> {DST_DB}")
shutil.copy2(SRC_DB, DST_DB)

conn = sqlite3.connect(str(DST_DB))
conn.execute("PRAGMA journal_mode=WAL")
cursor = conn.cursor()

# 1. Ver qué tablas existen
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
existing = [r[0] for r in cursor.fetchall()]
print(f"Tablas existentes: {existing}")

# 2. Crear tabla Token (esperada por build_state.py)
# La demo tiene 'tokens' (minúscula), necesitamos 'Token' (con mayúscula) o un alias
# build_state.py usa: SELECT COUNT(*) FROM Token
# Usamos una vista o renombramos

if 'Token' not in existing and 'tokens' in existing:
    # Crear vista Token apuntando a tokens
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS Token AS
        SELECT 
            mint as id,
            mint,
            name,
            symbol,
            created_at,
            dev_wallet,
            0 as firstMarketCap,  -- placeholder
            0 as firstLiquidity,
            0 as currentMarketCap
        FROM tokens
    """)
    print("Creada vista Token -> tokens")

# 3. Crear tabla PaperPosition (esperada por build_state.py)
# Queries esperadas:
# - SELECT reason, COUNT(*), SUM(CASE WHEN pnlUsd>0...), ROUND(SUM(pnlUsd),2) FROM PaperPosition WHERE status='CLOSED' GROUP BY reason
# - status, reason, pnlUsd, pnlPct
if 'PaperPosition' not in existing:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS PaperPosition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tokenId TEXT NOT NULL,
            mint TEXT,
            entryTime REAL,
            exitTime REAL,
            entryPrice REAL,
            exitPrice REAL,
            sizeSol REAL,
            pnlUsd REAL,
            pnlPct REAL,
            status TEXT DEFAULT 'OPEN',  -- OPEN, CLOSED
            reason TEXT,  -- SL, TP, TIMEOUT, STALL, BE, GREEN, TRAIL
            branch TEXT,
            features TEXT,  -- JSON
            decisionSignature TEXT
        )
    """)
    # Índices
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pp_status ON PaperPosition(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pp_token ON PaperPosition(tokenId)")
    print("Creada tabla PaperPosition")

# 4. Crear tabla Snapshot (para resolución de datos)
# Query: SELECT ts - LAG(ts) OVER (PARTITION BY tokenId ORDER BY ts) gap FROM Snapshot
if 'Snapshot' not in existing:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tokenId TEXT NOT NULL,
            ts INTEGER NOT NULL,  -- timestamp en ms
            price REAL,
            marketCap REAL,
            liquidity REAL,
            source TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_snap_token_ts ON Snapshot(tokenId, ts)")
    print("Creada tabla Snapshot")

# 5. Poblar PaperPosition con datos de my_trades (demo)
if 'my_trades' in existing:
    cursor.execute("SELECT COUNT(*) FROM my_trades")
    n = cursor.fetchone()[0]
    print(f"my_trades tiene {n} filas")
    
    # Insertar desde my_trades -> PaperPosition
    cursor.execute("""
        INSERT INTO PaperPosition (tokenId, mint, entryTime, exitTime, entryPrice, exitPrice, 
                                   sizeSol, pnlUsd, pnlPct, status, reason, branch)
        SELECT 
            token_mint, token_mint,
            CAST(strftime('%s', entry_time) * 1000 AS INTEGER),
            CAST(strftime('%s', exit_time) * 1000 AS INTEGER),
            entry_price, exit_price,
            size_sol, pnl_sol * 100, pnl_pct,  -- pnlUsd aprox (SOL * 100)
            'CLOSED',
            CASE 
                WHEN pnl_pct < -10 THEN 'SL'
                WHEN pnl_pct > 50 THEN 'TP'
                ELSE 'BE'
            END,
            'DEMO_BRANCH'
        FROM my_trades
        WHERE status = 'closed'
    """)
    print(f"Insertados {cursor.rowcount} PaperPosition desde my_trades")

# 6. Poblar Snapshot desde trades (demo - precios cada trade)
if 'trades' in existing:
    cursor.execute("SELECT COUNT(*) FROM trades")
    n = cursor.fetchone()[0]
    print(f"trades tiene {n} filas")
    
    # Muestrear algunos trades como snapshots
    cursor.execute("""
        INSERT INTO Snapshot (tokenId, ts, price, marketCap, liquidity, source)
        SELECT 
            token_mint,
            CAST(strftime('%s', block_time) * 1000 AS INTEGER),
            price,
            price * 1000000,  -- marketCap aproximado
            50.0,  -- liquidity placeholder
            'demo'
        FROM trades
        WHERE price > 0
        LIMIT 10000
    """)
    print(f"Insertados {cursor.rowcount} Snapshots desde trades")

# 7. Verificar conteos
for table in ['Token', 'PaperPosition', 'Snapshot']:
    try:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {count} filas")
    except Exception as e:
        print(f"{table}: error - {e}")

# 8. Verificar queries de build_state.py
print("\n=== Verificando queries de build_state.py ===")

# Token count
try:
    count = cursor.execute("SELECT COUNT(*) FROM Token").fetchone()[0]
    print(f"Token count: {count}")
except Exception as e:
    print(f"Token count error: {e}")

# PaperPosition closed
try:
    cursor.execute("""
        SELECT reason, COUNT(*),
               SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END),
               ROUND(SUM(pnlUsd),2)
        FROM PaperPosition WHERE status='CLOSED' GROUP BY reason
    """)
    for row in cursor.fetchall():
        print(f"  {row}")
except Exception as e:
    print(f"PaperPosition query error: {e}")

# Snapshot resolution (en segundos como build_state.py)
try:
    cursor.execute("""
        SELECT ROUND(AVG(gap)/1000.0,2) FROM (
            SELECT ts - LAG(ts) OVER (PARTITION BY tokenId ORDER BY ts) gap 
            FROM Snapshot
        ) WHERE gap>0
    """)
    res = cursor.fetchone()[0]
    print(f"Snapshot interval mean: {res} s")
except Exception as e:
    print(f"Snapshot query error: {e}")

conn.commit()
conn.close()
print(f"\n[OK] Base de datos lista: {DST_DB}")
print(f"Usa: \$env:PUMPSCOPE_DB = '{DST_DB}'")