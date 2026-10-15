# Analisis DB: d:\mejoradordebase de datos\pumpfun_detector\demo_pumpfun.db
Tablas:3 freelist=0

## tokens (1,200 filas)
salud: SIN PK ; SIN INDICES ; SIN FK DECLARADA
  - mint: nulos=0.0% distintos=1,200 | CANDIDATA INDICE/FK
  - twitter: nulos=43.58% distintos=636 | NULOS ALTOS 43.58%
  - telegram: nulos=67.25% distintos=380 | NULOS ALTOS 67.25%
  - website: nulos=55.58% distintos=512 | NULOS ALTOS 55.58%
  - total_supply: nulos=0.0% distintos=1 | CONSTANTE

## trades (135,265 filas)
salud: SIN PK ; SIN INDICES ; SIN FK DECLARADA
  - token_mint: nulos=0.0% distintos=1,200 | CANDIDATA INDICE/FK
  - wallet: nulos=0.0% distintos=50,536 | CANDIDATA INDICE/FK
  - side: nulos=0.0% distintos=2 | ENUM IMPLICITO->lookup
  - sol_amount: nulos=0.0% distintos=122,984 | DINERO REAL->INTEGER
  - token_amount: nulos=0.0% distintos=135,262 | DINERO REAL->INTEGER
  - price: nulos=0.0% distintos=135,265 | DINERO REAL->INTEGER
  - block_time: nulos=0.0% distintos=135,265 | FECHA COMO TEXTO->epoch

## my_trades (441 filas)
salud: SIN PK ; SIN INDICES ; SIN FK DECLARADA
  - token_mint: nulos=0.0% distintos=441 | CANDIDATA INDICE/FK
  - entry_time: nulos=0.0% distintos=441 | FECHA COMO TEXTO->epoch
  - entry_price: nulos=0.0% distintos=441 | DINERO REAL->INTEGER
  - exit_time: nulos=0.0% distintos=441 | FECHA COMO TEXTO->epoch
  - exit_price: nulos=0.0% distintos=441 | DINERO REAL->INTEGER
  - size_sol: nulos=0.0% distintos=427 | DINERO REAL->INTEGER
  - pnl_sol: nulos=0.0% distintos=421 | DINERO REAL->INTEGER
  - pnl_pct: nulos=0.0% distintos=431 | DINERO REAL->INTEGER
  - status: nulos=0.0% distintos=1 | CONSTANTE

## Patrones lift>=1.25 o <=0.8
  - [tokens] total_supply Q1 [1e+09-1e+09] n=240 WR=13.33% base=9.83% lift=1.36x

## Mejoras
  - CREATE INDEX IF NOT EXISTS idx_tokens_mint ON "tokens"("mint");
  - CREATE INDEX IF NOT EXISTS idx_tokens_created_at ON "tokens"("created_at");
  - CREATE INDEX IF NOT EXISTS idx_trades_token_mint ON "trades"("token_mint");
  - CREATE INDEX IF NOT EXISTS idx_trades_wallet ON "trades"("wallet");
  - CREATE INDEX IF NOT EXISTS idx_trades_block_time ON "trades"("block_time");
  - CREATE INDEX IF NOT EXISTS idx_my_trades_token_mint ON "my_trades"("token_mint");
  - CREATE INDEX IF NOT EXISTS idx_my_trades_entry_time ON "my_trades"("entry_time");
  - ANALYZE;

Notas:
  * DRY-RUN: revisa mejoras.sql antes de --apply.
  * WAL solo con aprobacion: PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
  * Si freelist alta: VACUUM INTO 'backup.db';
  * ENUM implicito->lookup, FECHA TEXT->INTEGER epoch, REAL dinero->INTEGER.
  * Todo SCAN en EXPLAIN QUERY PLAN sobre tabla grande -> indice (idea supabase/index_advisor).