# Informe arquitectura completa para maximizar rentabilidad real

**Alcance:** velocidad, app nativa Windows/web, lenguaje, código, base de datos, estilo, recolección, filtrado, análisis, bots, IA, repositorios open source, proyección de ganancia.  
**Regla:** no se promociona nada como rentabilidad real sin origen de datos, hash de base, partición temporal, PnL neto y validación forward.  
**Nota legal:** trading de memecoins es de alto riesgo. Esto no es asesoramiento financiero.

---

## 1. Visión de arquitectura recomendada

### 1.1. Dos productos separados

1. **Control plane / desktop app nativa**: Tauri 2 + Rust + React/TypeScript, SQLite + DuckDB.
2. **Motor de trading Rust daemon**: ingestor, quote, submitter, position manager, ledger auditable.
3. **Capa de investigación Python**: offline, reproducible, leakage guard, métricas, ML, reportes.

Principio: la UI y el motor nunca comparten el mismo proceso ni secretos. La señal emite una intención; el executor decide si es ejecutable.

### 1.2. Stack recomendado

| Capa | Tecnología | Por qué |
|---|---|---|
| UI nativa / web | Tauri 2 + React + TypeScript + Vite | Nativo Windows, WebView2, bundles pequeños, mismo código web [1][2] |
| Backend de bajo nivel | Rust + Tokio | Hot path sin heap, seguridad de memoria, async multiplataforma [3][4] |
| Base caliente de estado | Rust structs + canales Tokio | Menor latencia para decisiones [4] |
| Base transaccional | SQLite WAL | Decisión, ledger, crash recovery [5] |
| Base analítica | DuckDB | OLAP local, agregados por segmento, sin servidor [1] |
| IPC | JSON-RPC 2.0 | Contratos estables entre UI y engine [6] |
| Secretos | OS Keychain + AES-256-GCM | No código ni archivos planos [1][7] |
| Research | Python + NumPy + pandas + scikit-learn | Estadística y validación temporal |
| Event bus | Tokio broadcast / Redis Streams | Bajo acoplamiento, auditoría |

Fuentes:
- [1] OpenAlgo Desktop (Tauri 2 + Rust + DuckDB + SQLite + OS keychain) https://github.com/openalgo-desktop/openalgo-desktop
- [2] PortfolioNow (Tauri + Rust + React + SQLite) https://github.com/rullmann/portfolio-now
- [3] Private Terminal (Tauri + Rust + React, local-first) https://github.com/cerkon1/private-terminal
- [4] Rust Finance (Rust daemon Tokio, RPC latency benchmark, benchmarks) https://github.com/Ashutosh0x/rust-finance
- [5] HoneyBear Folio (Tauri + Rust + SQLite) https://github.com/honeybearfolio/HoneyBear-Folio
- [6] ai-invest.app (Rust engine + React/Tauri, JSON-RPC, plugin WASM) https://github.com/kpopdotlink/ai-invest.app
- [7] BasicTradingJournal (Tauri + SvelteKit, SQLite local) https://github.com/shelteredcorgi/basictradingjournal

### 1.3. Por qué no Electron

Electron consume más RAM, startup más lento y mayor superficie de ataque. Tauri usa runtime Rust y WebView del sistema. Esto mejora velocidad y distribución en Windows con installer NSIS/portable [3][5].

### 1.4. Privacidad y rendimiento

- Local-first: datos, estrategia y secretos en la máquina.
- Sin telemetría ni cuenta, auditable.
- DB en `%APPDATA%`, SQLite WAL, copia manual/export.

---

## 2. Velocidad: hot path real

### 2.1. Pipeline objetivo

```text
Geyser/ShredStream raw event
 -> zero-copy parse
 -> normalización compact_str
 -> risk sync checks
 -> quote provider cache-aware
 -> signing precomputado
 -> Jito bundle / zero-slot / nozomi / 0slot
 -> latency tracker
 -> confirmation watcher
 -> TradeRecorder immutable
```

### 2.2. Latencia: mediciones y optimizaciones

Objetivo real: medir cada etapa, no asumir.

- WebSocket or Yellowstone gRPC parallel stream.
- Eventos incompletos se descartan.
- Quote por proveedor real con `priceImpactPct`, `outAmount`, `slippageBps`, route age.
- Pre-sign en paralelo con quote final.
- Retry con backoff exponencial.
- Rebroadcast hasta confirmación.
- Selección de RPC por benchmark (Helius/Triton/QuickNode).

Rust Finance muestra el concepto de latency relay y benchmarks RPC [4]. Memecoin bots como gemtoast/slightlyuseless describen queue Redis, ZeroSlot, Jupiter liquidation, PumpPortal y rebroadcast [8][9].

### 2.3. Cambios arquitectónicos para ganancia

- Eliminar trabajo pesado del hot path.
- Precomputar metadata de wallet, blockhash refresh y fee.
- Cache de bonding curve por mint.
- Coalescencia de eventos por mint (dedupe).
- Saturación de quote dentro de latency budget (ej. 200-400 ms).
- Si evento incompleto o quote stale: no comprar.
- Esto reduce fill basura y preserva capital.

### 2.4. Métricas mínimas de hot path

- `detect_ms`
- `extract_ms`
- `quote_ms`
- `sign_ms`
- `submit_ms`
- `inclusion_ms`
- `confirm_ms`
- `quote_age_ms`
- `slippage_expected_bps`
- `slippage_real_bps`
- `route_provider`
- `retry_count`

Esto es lo único que permite promocionar una estrategia.

---

## 3. Código y estructura

### 3.1. Contrato de módulos

```text
detector/
  signal/              immutable signal rules
  features/            availability guard and as-of data
  costs/               single ExecutionCostModel
  adapters/
    signal_provider.py
    quote_provider.py
    order_submitter.rs
    position_manager.rs
    trade_recorder.rs
  analytics/            SQL + duckdb + leakage tests
  ui/                   tauri frontend
```

### 3.2. Invariantes de código

1. Single source of truth para señal.
2. Un solo cost model en PnL, sizing, labels y reportes.
3. Features solo con `available_at_ms <= entry_time`.
4. `data_origin`, `db_hash`, `code_git_sha`, `config_hash` en cada decisión.
5. `dry_run` por defecto.
6. Kill switch global.
7. Simulate-before-send obligatorio.
8. Trade ledger append-only con hash chain.

Rust Finance menciona merkle hash-chain audit trail y simulate guard, útil como pattern [4]. Fenrir menciona merkle hash-chain audit trail, simulate guard y tests de cadena [10].

### 3.3. Tests extendidos

- Unit: math, cost, sizing.
- Contract: signal schema.
- Integration: mock RPC quote.
- Replay: event stream reproducible.
- Leakage: recalc feature fila por fila.
- DB: integridad, índices, vacíos.
- UI: snapshot de componentes.

### 3.4. CI/CD

- GitHub Actions.
- Matrix Python tests.
- Clippy/Rust tests.
- Lint TypeScript.
- Artifact build Tauri.
- Dependency audit.

---

## 4. Base de datos y storage

### 4.1. SQLite optimizado

Configuración recomendada:

```text
PRAGMA journal_mode=WAL
PRAGMA synchronous=NORMAL
PRAGMA temp_store=MEMORY
PRAGMA wal_autocheckpoint=1000
PRAGMA cache_size=-100000
ANALYZE
VACUUM (maintenance)
```

Índices recomendados:

```sql
CREATE INDEX idx_tokens_mint ON Token(mint);
CREATE INDEX idx_tokens_creator ON Token(creator);
CREATE INDEX idx_token_pp ON PaperPosition(tokenId);
CREATE INDEX idx_snapshot_token_ts ON Snapshot(tokenId, ts);
CREATE INDEX idx_livetrade_mint ON LiveTrade(mint);
CREATE INDEX idx_features_entry ON features_at_entry(token_id, available_at_ms);
```

### 4.2. DuckDB para analytics

Usar DuckDB para:

- Segmentos por creator, cabal, hora, rango market cap.
- Análisis longitudinal por token.
- Reportes sin bloquear SQLite.
- Import de Arrow/Parquet.

### 4.3. Compresión y archivado

- SQLite para datos recientes transaccionales.
- Parquet/Zstd por periodo.
- Snapshots archivados por fecha.
- DB backup versionado.
- Verificación de restore.

Ya se ha generado una copia `custom.optimized.db` con índices, sin modificar la base original. Este es el camino seguro: comparar queries antes/después.

### 4.4. Esquema recomendado nuevo

```sql
CREATE TABLE decision_ledger (
  decision_signature TEXT PRIMARY KEY,
  data_origin TEXT NOT NULL,
  entry_ts_ms INTEGER NOT NULL,
  db_hash TEXT NOT NULL,
  code_git_sha TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  cost_model_version TEXT NOT NULL,
  feature_json TEXT NOT NULL,
  market_cap_source TEXT NOT NULL,
  verdict TEXT NOT NULL,
  branch TEXT,
  confidence REAL,
  risk_score REAL
);

CREATE TABLE execution_trace (
  trace_id TEXT PRIMARY KEY,
  decision_signature TEXT NOT NULL,
  quote_provider TEXT,
  route_id TEXT,
  out_amount TEXT,
  price_impact_pct REAL,
  quote_age_ms INTEGER,
  detect_ms INTEGER,
  quote_ms INTEGER,
  sign_ms INTEGER,
  submit_ms INTEGER,
  confirm_ms INTEGER,
  retry_count INTEGER,
  FOREIGN KEY (decision_signature) REFERENCES decision_ledger(decision_signature)
);
```

### 4.5. Integridad de datos

- Check `PRAGMA foreign_keys`.
- Checksum del DB.
- Backups atómicos.
- Query test: no outliers negativos imposibles.
- Report de cobertura (`tokens_missing_snapshots`, `quoted_without_fill`).
- Provenance en reporte HTML.

---

## 5. Recolección, filtrado y análisis

### 5.1. Fuentes de datos

| Fuente | Uso | Ventaja |
|---|---|---|
| PumpPortal WebSocket | Token creation/trades | Bajo coste, amplio uso [11] |
| Helius REST/WS | RPC, webhooks, token metadata | API amplia [12] |
| Yellowstone gRPC | Streams de baja latencia | Menor lag [8] |
| Jito Shred stream | Entrada ultra temprana | Edge, high risk [8] |
| DexScreener | Price/volume safety | Contexto de mercado [13] |
| Jupiter Quote API | outAmount, impact, route | Cotización real [14] |
| On-chain features | Rugs, dev, holders | Señal causal [15] |

Repos:
- Chainstack pumpfun-bonkfun-bot (log/block/geyser/PumpPortal, skip incomplete, latency budget) https://github.com/chainstacklabs/pumpfun-bonkfun-bot [11]
- Jupiter Quote docs https://dev.jup.ag/docs/swap/v1/get-quote [14]
- Paper Solana memecoin risk https://arxiv.org/html/2608.20271 [15]

### 5.2. Filtros de entrada real

1. MC mayor a 4,500 USD.
2. Dev balance <= 1.5%.
3. Reservas >= 12 SOL.
4. Migration < 85%.
5. First MC <= 4,285 (Branch A).
6. First liquidity <= 1,725.
7. Branch B: MC 8k-30k, reservas >=15, buyer/seller ratio >=0.6.
8. Quote `priceImpactPct` menor a umbral por liquidez.
9. Quote age menor a límite.
10. Reservas no cambiaron entre quote y submit.
11. Mint/freeze authority revocada cuando aplique.
12. Top holders <= umbral.
13. Creator score y bundler share.
14. Latency budget no excedido.

### 5.3. Validación temporal (anti-leakage)

- `TimeSeriesSplit` con gap.
- Validación purgada.
- Labels no pueden entrar como feature.
- `POST_ENTRY_FEATURES` guard.
- `market_cap` congelado por as-of.
- `graduated` prohibida.
- La regla se exporta solo con `oos_pass`.
- `train/validation/test/forward` separados.
- `lookahead-analysis` recalc incremental.

Freqtrade documenta lookahead bias y validación temporal [16]. scikit-learn `TimeSeriesSplit` soporta `gap` [17].

### 5.4. Filters on-chain de riesgo (paper)

Implementar primero como etiquetas:

- TVL drawdown
- idle duration
- migration flags
- bundled buy share
- creator history
- liquidity survival
- Sybil ratio
- net buyers 1h

El paper sobre memecoins de Solana usa labels rolling y prueba final reservada. No se deben convertir automáticamente en señal sin medir valor incremental neto [15].

### 5.5. Análisis porque ganancia no es winrate

Métricas:

- net expectancy per trade
- profit factor neto
- max drawdown
- CAGR/Sortino
- fill rate
- slippage real vs previsto
- latency event->confirm
- capacity por creador/cluster
- drift de umbrales

Una estrategia con WR 55% y menor slippage puede ser más rentable que un 81% con latencia/fuga.

---

## 6. Bots open source: recomendados y no recomendados

### 6.1. Matriz de bots

| Repo | Lenguaje | Fortaleza | Riesgo | Recomendación |
|---|---|---|---|---|
| SalahMessaoud/mako [13] | Python | 4 estrategias, SQLite, rug detector, copy-sell, 9 exits, `--paper` | PumpPortal, API nuevo, no audit formal | Laboratorio/paper |
| D3G3N-4LPH4/fenrir-v2 [10] | Python | multi-chain scout, 16 estrategias, simulate guard, CI 684 tests, merkle audit | token API costs, complejidad | Laboratorio, tomar patterns |
| slightlyuseless memecoin bot [9] | TypeScript | strategy-first, safety checks, observation, persistence | PumpPortal dependency | Ejecución/observación |
| gemtoast bot [8] | TypeScript | Redis queue, trailing, dry-run, copy trading | audit manual | Copy trading/backtesting |
| charllysb momentum [18] | Python | friction model honesto, live run documentado negativo | estrategia no sobrevive latencia | Calibrar expectativas |
| KubaBrzezinskiDev bot [19] | TypeScript | Claude anti-scam filter | API latency/cost | Pre-filtro only |
| Chainstack bot [11] | Node/Rust | listeners completos | es infraestructura | Latencia/research |
| OpenAlgo Desktop [1] | Rust/Tauri | nativa, seguro, local-first | broker oriented | UI architecture |
| ai-invest.app [6] | Rust/Tauri | plugin WASM, RPC, local DB | crypto focus? | Arquitectura engine |

### 6.2. Bots no usar ciegamente

- Niranjanprasad1 bot por binarios y bundles [20].
- bigmacman repo por MEV/copy and claims [21].
- JadenMassari por repo muy nuevo y métricas no auditadas [22].
- Zipsey custodial [23].
- Reinasboo ROI claims no verificables [24].
- HZCX404 volume bots by ToS/operational risk [25].

### 6.3. Bots con "honestidad de mercado"

charllysb's bot es extremadamente útil porque documenta señal positiva en sim pero pérdida live por latencia. Conclusión: no copiar estrategia, sino adoptar la metodología de friction model y slippage meter [18].

### 6.4. Integraciones expertas

- Hummingbot architecture: separar connector/strategy/executors/positions. Hummingbot is open source framework. https://hummingbot.org/ [26]
- Freqtrade: backtesting assumptions and lookahead analysis.
- Jupiter: quote real.
- Solana program risk, bundles, priority, Jito.

---

## 7. Inteligencia artificial aplicada con rigor

### 7.1. Dónde sí usar IA

1. Filtro de scams basado en holdings, metadata, creator.
2. Resumen de creador/cluster y anomalía.
3. Clasificación de eventos de Twitter/Telegram.
4. Calibración de riesgo y alerts.

### 7.2. Dónde no usar IA

- Alpha directo sin validación temporal.
- Auto-trading sin control de coste.
- Señales black-box sin provenance.

### 7.3. Coste vs latencia

Claude/GPT para cada evento en tiempo real añade latencia y coste. Kuba bot documenta Claude API rate/cost and 400ms Helius. Eso es útil solo para pre-filtro asincrónico, no hot path [19].

### 7.4. Métricas AI

- Precision/Recall real en test.
- Drift del modelo.
- Latencia por inferencia.
- Cost per trade decision.
- Impacto incremental en PnL.

---

## 8. Proyección de ganancias por arquitectura

### 8.1. Base de cálculo

Usar los datos reales ya disponibles del pumpscope DB:

- Paper positions: 5,021 cerradas.
- Winrate actual medido: 12.55%.
- PnL neto actual: -79,770.96 USD.
- Profit factor: 0.2687.
- Exit reasons: TIMEOUT 3,187; SL 1,249; TP 269; TRAIL 152; GREEN 99; STALL 60; BE 5.

Interpretación: el problema no es falta de tokens, sino estrategia/execution destructiva.

### 8.2. Cuánto mejorar cada workstream

| Workstream | Efecto estimado sobre PnL | Riesgo que elimina |
|---|---:|---|
| Stop leakage P0 | Evita señales falsas (reportado PnL puede bajar primero) | overstated WR |
| Quote veto + slippage p95 | +8 a +15 pp netos | fills basura |
| Latency budget | elimina trades sin captura rentable | FOMO |
| Creator/cabal blacklist | -15 a -25% loss rate | rugs |
| Sizing/Kelly capped | -35 a -55% drawdown | ruin |
| Fast native app | fill rate +5 a +10% | latencia |
| Analytics DuckDB + segmentos | mejor asignación capital ±? | ceguera |
| Bots en paper mode | permite comparación real | black box |
| AI scam filter offline | -? loss rate | honeypots |
| Fix temporal validation | puede reducir WR 20-40% inicial | overfit |

### 8.3. Escenarios mensuales sobre 10 SOL

| Escenario | Trades/mes | EV neto por trade | Fill | Profit | Retorno monthly | Anual compuesto aprox |
|---|---:|---:|---:|---:|---:|---:|
| Basal real actual (paper) | 8 | -0.158 SOL | 100% | -1.27 SOL | -12.7% | - |
| P0+P1 prudente | 5 | 0.012 SOL | 85% | 0.051 SOL | 0.51% | 6.3% |
| Mejorado completo | 6 | 0.025 SOL | 90% | 0.135 SOL | 1.35% | 17.4% |
| Alta calidad | 7 | 0.035 SOL | 92% | 0.228 SOL | 2.28% | 30.8% |

Estos números son hipotéticos, requieren validación forward.

### 8.4. Kill criteria (no escalar sin esto)

- 100 forward trades minimum.
- WR >= 65% neto post-costes.
- PF >= 1.30.
- Max drawdown compatible.
- Fill rate > 80%.
- Slippage real <= modelo p90.
- PnL por creador/cluster estable.
- No leakage.
- Latencia estable.

---

## 9. Plan para convertir en app nativa de Windows

### 9.1. Fases

**Fase A (2-4 semanas)**
- Tauri shell + UI básica.
- SQLite local.
- Import/export CSV y DB.
- Dashboards read-only.
- Secret storage.
- Backtests panel.

**Fase B (4-8 semanas)**
- Rust daemon sidecar.
- Jupiter quote integration.
- PumpPortal stream.
- Paper mode.
- Ledger auditable.
- Live trade one-key approval.

**Fase C (8+ semanas)**
- Jito bundle.
- ZeroSlot/Nozomi.
- Geyser/Shred.
- AI anti-scam offline.
- WASM plugins.
- Multi-wallet, health checks.

### 9.2. Distribución Windows

- NSIS installer.
- Portable `.exe`.
- Auto-update.
- Signing cert si posible.
- Minimal permissions.
- Local database path `%APPDATA%`.

Forte/Felixin style projects show Tauri private deployment [5][3].

---

## 10. Checklist de ejecución segura de bots

1. Wallet nueva, fondos pequeños.
2. Dry-run 7+ días.
3. Git commit hash pinned.
4. No binarios compilados de terceros.
5. Audit deps.
6. Env secrets.
7. Simulate tx.
8. Kill switch.
9. Fee/slippage ledger.
10. Withdrawal emergency.
11. Max open positions.
12. Correlation cap por creator.

---

## 11. Fuentes principales (enlaces)

1. OpenAlgo Desktop https://github.com/openalgo-desktop/openalgo-desktop
2. rullmann/portfolio-now (Tauri + Rust + React) https://github.com/rullmann/portfolio-now
3. cerkon1/private-terminal (Tauri + Rust, scanner, local-first) https://github.com/cerkon1/private-terminal
4. Rust Finance (Rust low-latency daemon) https://github.com/Ashutosh0x/rust-finance
5. HoneyBear Folio (Tauri + Rust + SQLite) https://github.com/honeybearfolio/HoneyBear-Folio
6. ai-invest.app (Rust engine + Tauri + WASM) https://github.com/kpopdotlink/ai-invest.app
7. BasicTradingJournal (Tauri + SvelteKit) https://github.com/shelteredcorgi/basictradingjournal
8. gemtoast/solana-trading-bot https://github.com/gemtoast/solana-trading-bot
9. slightlyuseless memecoin-trading-bot https://github.com/slightlyuseless/memecoin-trading-bot
10. D3G3N-4LPH4 fenrir-v2 https://github.com/D3G3N-4LPH4/fenrir-v2
11. Chainstack pumpfun-bonkfun-bot https://github.com/chainstacklabs/pumpfun-bonkfun-bot
12. Jupiter Swap Quote https://dev.jup.ag/docs/swap/v1/get-quote
13. SalahMessaoud mako https://github.com/SalahMessaoud/mako
14. Jupiter Docs https://dev.jup.ag/docs/swap/v1/get-quote
15. Paper Solana memecoin risk https://arxiv.org/html/2608.20271
16. Freqtrade backtesting https://www.freqtrade.io/en/stable/backtesting/
17. Freqtrade lookahead https://www.freqtrade.io/en/stable/lookahead-analysis/
18. scikit-learn TimeSeriesSplit https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
19. charllysb pumpfun-momentum-bot https://github.com/charllysb/pumpfun-momentum-bot
20. KubaBrzezinskiDev trading-monitor-bot https://github.com/KubaBrzezinskiDev/trading-monitor-bot
21. Niranjanprasad1 bot https://github.com/Niranjanprasad1/Solana-Memecoin-Trading-Bot
22. bigmacman bot https://github.com/bigmacman1129/solana-sniper-copy-trading-bot
23. JadenMassari
24. Grid https://github.com/jadenmassari/pumpfun-memecoin-sniper
25. Zipsey bot https://github.com/zipseysol/Zipsey---Your-Automated-Trading-Agent
26. Reinasboo https://github.com/Reinasboo/the-tradooor
27. HZCX404 suite https://github.com/HZCX404/memecoin-trading-bots
28. Hummingbot docs https://hummingbot.org/
29. Fortuna desktop app https://github.com/cosiato/Fortuna
30. AI-invest app https://github.com/kpopdotlink/ai-invest.app

---

## 12. Conclusión

La forma de aumentar ganancias reales no es "más velocidad a ciegas" ni "más IA". Es:

1. App nativa rápida, local-first.
2. Rust hot path auditado.
3. P0 anti-leakage.
4. Cotización real y latencia medida.
5. Base SQLite + DuckDB con segmentos.
6. Paper mode honesto.
7. Bots abiertos como laboratorio, no alpha ciego.
8. Escalar tamaño por evidencia.
9. Kill switches, fund limits.
10. Proyección solo con métricas auditables.
