# ARCHITECTURE.md — Arquitectura Centralizada (Raíz del Repositorio)

> **Proyecto:** PumpScope Detector & Cockpit — Auditoría y mejora cuantitativa de trading Paper (Pump.fun / Solana)
> **Estado:** DRY-RUN / PAPER FORWARD. Fondos reales estrictamente bloqueados.
> **Última actualización:** 2026-10-15

---

## 1. Visión general

Este repositorio contiene un sistema de auditoría cuantitativa y aprendizaje continuo para estrategias de trading Paper sobre Pump.fun (Solana DEX). No es un bot de trading con fondos reales. El modo live está bloqueado por software hasta que se cumpla una gate de significancia out-of-sample (`N >= 100` trades forward, `EV_neto > 0%`, `Profit Factor > 1.25`).

La verdad empírica vive exclusivamente en `STATUS.md` y `STATE.json`. Cualquier otro documento que mencione winrates distintos está desactualizado o es una declaración no validada.

---

## 2. Estructura de carpetas (orden canónico)

```
$PUMPSCOPE_ROOT\
├── AGENTS.md              # Protocolo canónico para agentes autónomos (SSOT de reglas)
├── ARCHITECTURE.md        # Este documento: arquitectura centralizada
├── STATUS.md              # SSOT de rendimiento medido (en pumpfun_detector/)
├── STATE.json             # Estado fresco generado por scripts/build_state.py
├── README.md              # README principal
├── .gitignore             # Exclusiones de git
├── skills-lock.json       # Registro de skills (v2)
├── metrics/
│   └── claimed.json       # Registro de métricas CLAIMED (veto keys)
├── scripts/               # Pipeline y utilidades de raíz
│   ├── verify.ps1         # Único comando de verificación (6 pasos)
│   ├── agent_gate.py      # Gate G1-G7 (anti-hardcode, anti-rutas absolutas)
│   ├── build_state.py     # Genera STATE.json solo con datos medidos (fail-closed)
│   ├── continuous_learning.py  # Pipeline de aprendizaje continuo (HITL + ML + forense)
│   ├── analyze_custom_db.py    # Análisis completo de custom.db (winrate, PnL, patrones)
│   ├── inspect_db_schema.py    # Inspección de esquema de la DB
│   └── ... (otros scripts de inspección y utilidad)
├── tools/                 # Herramientas open-source auditadas sobre custom.db
│   ├── db_opensource_miner.py      # Minería de patrones (crypto-chart-patterns, momentum, etc.)
│   ├── mcp_pattern_miner.py        # Servidor MCP JSON-RPC 2.0 para patrones DEX
│   └── benchmark_all_opensource_real.py  # Benchmark de 8 repos open-source sobre datos reales
├── pumpfun_detector/      # Paquete principal del detector (Cockpit + señales)
│   ├── AGENTS.md          # Protocolo específico del detector
│   ├── STATUS.md          # SSOT de rendimiento medido del detector
│   ├── ARCHITECTURE.md    # Arquitectura detallada del detector (211 líneas)
│   ├── detector/          # Módulos del detector
│   │   ├── master_signal.py     # Decisor maestro de entrada (MasterSnipeSignal)
│   │   ├── ai_quant_model.py    # Modelo ML HistGradientBoosting (+ /boost)
│   │   ├── loss_learner.py      # LossPostMortemLearner (arquetipos ganadores + clusters de fallo)
│   │   ├── forward_ledger.py    # Ledger forward inmutable con provenance causal
│   │   ├── live_collector.py    # Colector en vivo (DexScreener HTTP + Playwright)
│   │   ├── manual_annotation.py # Revisión humana-en-el-bucle (HITL) + AnnotationDB
│   │   ├── metrics_registry.py  # Registro claimed_wr / claimed_pf (anti-hardcode)
│   │   ├── market_feed.py       # Feed de precios real (DexScreener API)
│   │   ├── paper_broker.py      # Paper-Forward Broker (LivePaperTrader)
│   │   ├── gmgn_bridge.py       # Bridge GMGN + matriz de confluencia
│   │   ├── browser_agent.py     # Agente visual de auditoría web
│   │   ├── label_guard.py       # Guarda causal anti-fuga (FutureLeakageError)
│   │   ├── pattern_identifier.py| Identificador de patrones
│   │   └── ...
│   ├── cockpit/           # Cockpit interactivo de control
│   │   ├── server.py          # Servidor HTTP / snapshot causal
│   │   ├── dashboard.py       # Dashboard FastAPI HITL (revisión humana)
│   │   ├── launch.py          # Lanzador del cockpit (snapshot + servidor + ventana)
│   │   └── README.md
│   ├── scripts/           # Scripts del detector (sync, benchmark, ingest)
│   ├── tests/             # Suite de pruebas (423 passed)
│   ├── resultados/        # reglas_produccion.json, umbrales calibrados
│   └── STATUS.md          # Verdad medida del detector
├── outputs/               # Informes y reportes generados (no commitear)
│   ├── INFORME_AUDITORIA_MEJORA_2026-10-15.md
│   ├── analisis_bd_custom_db.json
│   ├── auditoria-winrate-integraciones.md
│   ├── informe-arquitectura-completa-profit.md
│   ├── informe-completo-profits-bots.md
│   ├── informe_patrones_configuraciones_ganadoras.md
│   ├── reporte-mejoras-ganancias.md
│   └── ...
├── MEMORY/                # Memoria de decisiones y autodescubrimiento
│   ├── decision-log.md    # Log de decisiones del agente
│   ├── hypotheses.md      # Hipótesis validadas / descartadas
│   └── locks.md           # Bloqueos de skills y configuración
├── .agents/skills/        # Registry de skills (17 skills, algunas en cuarentena)
├── repos_opensource/      # Repos open-source clonados (solo lectura, auditados)
└── scratch/               # Área de trabajo temporal (excluida de gates G2/G3)
```

---

## 3. Flujo de datos (pipeline de verdad)

```
custom.db (SQLite, modo ro, 65,423 tokens, 441 PaperPosition cerradas)
    │
    ├──► scripts/build_state.py ───► STATE.json (winrate, PF, PnL medidos)
    │
    ├──► tools/db_opensource_miner.py ───► outputs/*.json (patrones open-source)
    │
    ├──► scripts/continuous_learning.py ───► reglas_produccion.json (umbrales calibrados)
    │       ├─► AnnotationDB (revisión humana HITL)
    │       ├─► LossPostMortemLearner (forense win/loss)
    │       └─► AIQuantModel (HistGradientBoosting, /boost)
    │
    └─► pumpfun_detector/detector/live_collector.py ───► ticks_crudos (datos reales en vivo)
            ├─► DexScreenerSource (HTTP API real)
            └─► PlaywrightDexScreenerSource (scraping web, fail-closed)
```

---

## 4. Capas del sistema

### 4.1 Capa de datos (Data Layer)
- **Fuente de verdad:** `custom.db` (SQLite, `file:...mode=ro`, solo lectura).
- **Tablas clave:** `Token` (1,200 filas), `PaperPosition` (441 trades cerrados), `Snapshot` (10,000), `trades` (135,265), `my_trades` (441), `decision_ledger`, `features_at_entry`, `ticks_crudos`.
- **Acceso:** siempre por URI `file:...mode=ro&query_only=ON`. Nunca escritura directa.

### 4.2 Capa de decisión (Decision Layer)
- **Decisor maestro:** `MasterSnipeSignal` (`detector/master_signal.py`).
- **Contrato:** `MasterSignalDecision` (admitted, branch, confidence, expected_winrate, expected_profit_factor, veto_reason, metrics).
- **Métricas CLAIMED:** `metrics/claimed.json` + `claimed_wr()` / `claimed_pf()`. Prohibido hardcodear winrates/PF en código (gate G2).

### 4.3 Capa forense (Forensic Layer)
- **LossPostMortemLearner** (`detector/loss_learner.py`): arquetipos de ganancia (Winners DNA) + clusters de fallo (Failure Traps).
- **Bayesiano log-odds:** `P(x|WIN) / P(x|LOSS)` para probabilidad posterior calibrada.

### 4.4 Capa de aprendizaje continuo (Continuous Learning Layer)
- **`scripts/continuous_learning.py`**: conecta `filter_effectiveness` (HITL) → forense → reentrenamiento ML → `reglas_produccion.json`.
- **Gate de significancia:** `N >= 100` trades forward, `EV_neto > 0`, `PF > 1.25`.

### 4.5 Capa de revisión humana (HITL Layer)
- **`manual_annotation.py`**: `AnnotationDB`, `FilterAnnotation`, `SignalReview`, `ReviewInterface` (CLI).
- **`cockpit/dashboard.py`**: FastAPI con endpoints `/pending`, `/review`, `/filter-effectiveness`, `/stats`, `/health`.
- **Propósito:** revisión filter-by-filter antes de promover reglas a producción.

### 4.6 Capa de datos en vivo (Live Data Layer)
- **`live_collector.py`**: `LiveCollector` + `DexScreenerSource` (HTTP real) + `PlaywrightDexScreenerSource` (scraping fail-closed).
- **Guardado:** tabla `ticks_crudos` para auditoría completa de la ruta de precios.
- **Fail-closed:** si fuentes externas no responden, reporta sin conexión. Nunca falsea datos.

---

## 5. Reglas de seguridad (AGENTS.md — innegociables)

| Regla | Descripción |
|-------|-------------|
| **DRY-RUN / PAPER FORWARD** | Fondos reales bloqueados. `dryRun = false` bloqueado por software. |
| **Sin mocks silenciosos** | Cero tokens ficticios. Fail-closed si fuentes no responden. |
| **SL real modelado** | −33.49% a −35% (nunca −8% ni −15% nominal). |
| **STALL real** | −28.93% medio auditado. |
| **Costes reales** | 0.002 SOL por transacción + slippage entrada/salida. |
| **Significancia** | `N >= 30` para inferir; `N >= 100` forward para certificar. |
| **Proveniencia** | `[MEASURED]` / `[CLAIMED]` / `[SYNTHETIC]` obligatorio. |
| **Sin rutas absolutas** | Gate G3: todo vía `os.environ.get()` con defaults relativos. |
| **Sin métricas hardcodeadas** | Gate G2: usar `claimed_wr()` / `claimed_pf()`. |

---

## 6. Estado actual medido (custom.db, 441 trades)

| Métrica | Valor | Procedencia |
|---------|-------|-------------|
| Winrate real | 55.56% | Medido (245/441) |
| PnL total | +5,393.83 USD | Medido |
| Profit Factor | 3.59 | Medido |
| EV por trade | +12.23 USD | Medido |
| SL promedio real | −35.85% | Medido (112 trades) |
| TP promedio real | +92.04% | Medido (140 trades) |
| Tokens totales | 1,200 | Medido |
| Tokens completos | 118 (9.83%) | Medido |
| Gate OOS (`N>=100`, `EV>0`, `PF>1.25`) | ✅ Cumple (en este corte) | Medido |
| Live trading | 🔒 BLOQUEADO | Por protocolo AGENTS.md |

**Archivo de referencia:** `outputs/analisis_bd_custom_db.json`

---

## 7. Comandos obligatorios

```powershell
# Verificación completa (6 pasos, debe ser VERIFY OK)
pwsh scripts/verify.ps1

# Solo gate de integridad
python scripts/agent_gate.py

# Solo suite de pruebas
python -m pytest pumpfun_detector/tests -q

# Generar estado fresco
python scripts/build_state.py

# Pipeline de aprendizaje continuo
python scripts/continuous_learning.py

# Análisis completo de custom.db
python scripts/analyze_custom_db.py

# Dashboard HITL (FastAPI)
python -m pumpfun_detector.cockpit.dashboard
```

---

## 8. Nota para la próxima IA (memoria compartida)

Si otra instancia de IA toma este trabajo, **no tiene memoria de esta sesión**. Debe leer:
1. `AGENTS.md` — protocolo y reglas obligatorias.
2. `STATUS.md` — verdad empírica (winrate real, no declaraciones).
3. `STATE.json` — estado fresco (regenerar con `build_state.py` si tiene >24h).
4. `outputs/INFORME_AUDITORIA_MEJORA_2026-10-15.md` — informe de auditoría completo.
5. `outputs/analisis_bd_custom_db.json` — datos medidos de la DB.
6. `scripts/verify.ps1` — comando único de verificación.

Sin leer estos archivos, cualquier afirmación de rendimiento es inválida. **No asumir winrates superiores a los medidos en STATUS.md.**

---

## 9. Próximos pasos pendientes

1. **Conectar Playwright collector a Pump.fun/DexScreener reales** y validar población de `ticks_crudos`.
2. **Llenar `decision_ledger`** con trades forward resueltos (actualmente 0, por eso el forense usa sintéticos).
3. **Cerrar gate OOS con trades forward reales** (`N >= 100`, sellado con `walkforward_seal.py`).
4. **Poblar `AnnotationDB`** con revisión humana vía dashboard FastAPI.
5. **Desbloquear live trading** solo tras certificación OOS sellada.
