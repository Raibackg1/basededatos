---
name: db-pattern-analyzer
description: Analizador, identificador y mejorador generico de bases de datos SQLite/DuckDB. Inspecciona esquema agnostico, perfila calidad, mina patrones con Subgroup Discovery + FDR y propone mejoras versionadas con backup. Usar cuando pidan analizar, auditar, mejorar, optimizar o entender cualquier .db/.sqlite/.duckdb.
---

# Skill: DB Pattern Analyzer / Mejorador / Identificador

> Version: 1.0.0 | Base probada: `pumpfun_detector` (9/9 tests OK) + open-source: pysubgroup, subgroups, soda-core, great_expectations, sqlite-utils, supabase/index_advisor, pg_qualstats, ydata-profiling, data-checker-fixer

## Cuando activar
- "analiza mi base", "que patrones hay", "mejora mi db", "identifica problemas", "optimiza sqlite", "audita datos", "por que pierdo / gano".

## Workflow obligatorio en 4 fases

### FASE 1 — INSPECT (agnostico, como `detector/inspector.py`)
1. No asumir nombres. Listar `sqlite_master`, `PRAGMA table_info(t)`, `PRAGMA foreign_key_list(t)`, `PRAGMA index_list(t)`, `SELECT COUNT(*)`.
2. Mapeo semantico por aliases + score (reutilizar `TABLE_PROFILES` de inspector.py, extender a generico: users/orders/events).
3. Detectar FK implicitas por nombre (`*_id`, `*_mint`, `token_*`) aunque no haya constraint.
4. Salida: `schema_report` + `mapping.json`.

### FASE 2 — PROFILE (calidad estilo Soda/GX + ydata-profiling)
Por cada tabla/columna calcular:
- `null_pct`, `distinct_count`, `distinct_pct`, `duplicados`, `min/max/avg` (numericos), `top_5 valores` (texto), `longitud media` (texto).
- Tipos sospechosos: fecha guardada como TEXT ISO, dinero en REAL/FLOAT, enum implicito (<=10 distintos en TEXT), IDs sin indice.
- Salud: tablas sin PK, sin indices, god-table (>30 cols), sin FK declarada, huerfanos (`LEFT JOIN IS NULL`), `VACUUM/ANALYZE` pendiente, paginas/fragmentacion (`PRAGMA page_count/freelist`).
- Generar `soda_contract.yml` (SodaCL) + `expectations.json` (Great Expectations) automaticamente.

### FASE 3 — MINE (patrones + antipatrones, como `advanced_engine.py` + pysubgroup)
1. **Patrones de exito (Winners DNA):** si hay columna etiqueta (`pnl>0`, `win`, `graduated`, o `complete`), hacer:
   - Univariado por cuantiles (Q1-Q5) con `winrate, lift, binomtest p-value` (ya existe en `patterns.univariate`).
   - Subgroup Discovery Beam Search con `WRAcc = P(S)*(P(win|S)-P(win))`, deduplicar por Jaccard, corregir con Benjamini-Hochberg FDR `q<=0.05` (ya existe en `SubgroupMiner`).
   - Referencia open-source: `flemmerich/pysubgroup` (WRAcc, DFS/BestFirst) y `antoniolopezmc/subgroups` (comparar calidad). No reinventar: envolver.
2. **Antipatrones / Failure Traps (como `loss_learner.py`):** clusterizar perdidas/errores en: `DEAD_* (sin traccion)`, `SLIPPAGE/RUG`, `TOXIC_* (concentracion)`, `STALL (estancamiento)`, `ORPHAN/DUP/NUL`.
3. **Microestructura (si hay trades/eventos):** HHI, burstiness B, VPIN, Amihud, grafo cabal Adamic-Adar, sniper bloque-0, CPMM Kyle lambda (todo ya en `advanced_engine.py`).
4. Particion temporal OOS sellada 60/20/20 (BP-012) — nunca reportar solo in-sample.

### FASE 4 — IMPROVE (mejorador con versionado tipo git, como data-checker-fixer)
Nunca tocar la original sin backup. Orden:
1. `backup.db` + `VACUUM INTO`.
2. Generar `mejoras.sql` (dry-run) y `mejoras_aplicadas.log`:
   - `CREATE INDEX IF NOT EXISTS idx_<tabla>_<col>` para FK implicitas y columnas de filtro/join (logica `supabase/index_advisor`: indexa `WHERE/JOIN/GROUP BY` frecuentes via `pg_qualstats`-like conteo).
   - `ANALYZE`, `VACUUM`, `PRAGMA journal_mode=WAL; synchronous=NORMAL` (solo si el usuario aprueba).
   - Proponer `ALTER TABLE ... ADD CONSTRAINT FOREIGN KEY`, normalizar enums a tabla lookup, migrar TEXT-fecha a INTEGER epoch, REAL-dinero a INTEGER centimos si hay precision critica.
   - Vistas materializadas / tablas resumen para queries lentas (`EXPLAIN QUERY PLAN` con `SCAN` -> indice).
3. Re-chequear con FASE 2 (antes vs despues) y versionar: `fixes.db` estilo git (tabla `fixes_log`: fecha, tabla, pk, col, antes, despues, motivo, plugin).
4. Exportar `reporte_mejora.html` autocontenido (matplotlib base64, como `report.py`) + `hallazgos.csv` + `reglas_produccion.json`.

## Reglas duras (del proyecto base)
- §3 Sin mocks silenciosos: todo numero viene de la DB real o se declara `sin datos suficientes`.
- Un solo SSOT de decision si hay trading (`MasterSnipeSignal`); no crear senales paralelas.
- Slippage/fees realistas si se simula PnL (-33.49% SL, 0.002 SOL fee, STALL -14.46%).
- Tests: `py -m unittest discover -s tests -v` debe quedar verde antes de entregar.

## Comandos canonicos
```powershell
# demo generica
py tools\db_analyzer.py --db "ruta\mi.db" --out resultados_db
py tools\db_analyzer.py --db "pumpfun_detector\demo_pumpfun.db" --label complete --out resultados_db
# proyecto existente (trading)
py pumpfun_detector\main.py --db "ruta\custom.db" --out resultados
py -m unittest discover -s pumpfun_detector\tests -v
```

## Mapa de reutilizacion open-source
| Necesidad | Repo | Que copiar |
|---|---|---|
| Subgroup Discovery | flemmerich/pysubgroup, antoniolopezmc/subgroups | WRAcc, q-values, beam search, `to_dataframe()` |
| Calidad/contratos | sodadata/soda-core, great-expectations/great_expectations | SodaCL YAML, Expectations suite, profiling |
| Inspeccion SQLite | simonw/sqlite-utils | `tables --counts`, `schema`, `indexes`, transforms |
| Index advisor | supabase/index_advisor, powa-team/pg_qualstats | contar quals (WHERE/JOIN), hipoteticos `hypopg`, adaptar a SQLite `EXPLAIN QUERY PLAN` |
| Perfilado EDA | ydataai/ydata-profiling | reporte HTML de nulos/correlaciones/duplicados |
| Fix versionado + GUI | FulgentMcGuffin/data-checker-fixer | semantics YAML, plugins imputacion Polars, fixes.db git-like |
| Transformaciones | SQLMesh/sqlmesh | migraciones versionadas, auditorias |
