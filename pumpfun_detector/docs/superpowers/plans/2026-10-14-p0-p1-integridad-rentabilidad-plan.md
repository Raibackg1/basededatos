# P0+P1 Integridad y Rentabilidad — Implementation Plan

> **For agentic workers:** ejecutar este plan en línea, tarea por tarea, con pruebas después de cada unidad.

**Goal:** impedir leakage, medir PnL neto reproducible y aplicar gates de ejecución/riesgo sin habilitar trading real.

**Architecture:** Se añaden módulos pequeños para disponibilidad de features, provenance, validación temporal, quote/latency/sizing y exportación de reglas. `MasterSnipeSignal` permanece como SSOT y `LiveStore` como ledger. Los cambios SQLite son aditivos.

**Tech Stack:** Python 3, sqlite3, unittest, pandas/numpy existentes.

**Spec:** `docs/superpowers/specs/2026-10-14-p0-p1-integridad-rentabilidad-design.md`

## Global Constraints

- No añadir dependencias externas.
- No ejecutar órdenes on-chain ni habilitar fondos reales.
- No mezclar `synthetic` con `live` o `forward_live`.
- Toda feature post-entrada debe ser rechazada.
- Todo sizing inválido debe devolver cero.
- La comparación debe usar el mismo benchmark antes/después.

---

### Task 1: Availability guard y provenance

**Files:**
- Create: `detector/data_integrity.py`
- Modify: `detector/config.py`
- Test: `tests/test_data_integrity.py`

**Interfaces:**
- `FeatureAvailabilityGuard.validate(features, entry_time=None) -> dict`
- `FeatureAvailabilityGuard.filter_frame(df, entry_time_col='entry_time') -> DataFrame`
- `build_provenance(db_path, data_origin, config=None) -> dict`

- [ ] Escribir tests para outcomes prohibidos, timestamps incompatibles y hash/provenance.
- [ ] Implementar guard con patrones explícitos (`win_`, `future_`, `mfe_`, `mae_`, `pnl_`, `exit_`, `tpsl_`) y campos exactos.
- [ ] Añadir todos los nombres prohibidos a `Cfg.exclude_features` sin retirar compatibilidad existente.
- [ ] Ejecutar los tests focalizados.

### Task 2: Features as-of-entry y market cap

**Files:**
- Modify: `detector/features.py`
- Modify: `detector/master_signal.py`
- Test: `tests/test_data_integrity.py`

**Interfaces:**
- `entry_features(...)` devuelve `market_cap_source` y `market_cap_at_entry_ts` cuando los datos lo permiten.
- `MasterSnipeSignal.evaluate()` rechaza fallback final sin marca as-of.

- [ ] Añadir selección explícita de market cap de entrada.
- [ ] Rechazar `market_cap` sin `market_cap_at_entry`, `mc_at_entry_est`, `marketCapAtEntry` o `asof_time <= entry_time`.
- [ ] Añadir tests de aceptación/rechazo.

### Task 3: Validación temporal y reglas ejecutables

**Files:**
- Create: `detector/temporal_validation.py`
- Modify: `detector/advanced_engine.py` o exportador de reglas localizado por tests.
- Test: `tests/test_temporal_validation.py`

**Interfaces:**
- `temporal_splits(df, time_col, train=0.6, validation=0.2, test=0.2, gap=0) -> dict`
- `rule_is_production_ready(rule) -> bool`

- [ ] Escribir tests de bloques ordenados, gap y regla no ejecutable.
- [ ] Implementar splits deterministas y purge básico por timestamps.
- [ ] Añadir metadata de split y `oos_pass` sin falsificarlo cuando no exista test/forward.

### Task 4: Quote gate, latency y sizing

**Files:**
- Create: `detector/execution_guards.py`
- Modify: `detector/master_signal.py`
- Modify: `detector/costs.py`
- Test: `tests/test_execution_guards.py`

**Interfaces:**
- `QuoteGate.evaluate(quote, now_ms, ...) -> GateDecision`
- `LatencyBudget.evaluate(stages) -> GateDecision`
- `safe_position_size(portfolio_sol, expected_net_ev, liquidity_sol, drawdown_pct, limits) -> float`

- [ ] Probar quote stale, impacto, profundidad, route inválida y latencia.
- [ ] Implementar gates fail-closed.
- [ ] Integrar solo los campos opcionales ya soportados por `MasterSnipeSignal`.
- [ ] Hacer que sizing devuelva `0.0` ante EV/drawdown/liquidez inválidos.

### Task 5: Ledger auditable

**Files:**
- Modify: `detector/live_store.py`
- Test: `tests/test_live_store.py`

**Interfaces:**
- Migración aditiva de columnas provenance/execution.
- Firma idempotente basada en mint, branch, admitted, origin y entry timestamp.
- `record_execution_trace(...)` y `net_audit(...)` conservan filtro por origin.

- [ ] Añadir migración segura para DB existente.
- [ ] Extender persistencia y tests de idempotencia/procedencia.
- [ ] No eliminar ni sobrescribir datos históricos automáticamente.

### Task 6: SQLite y benchmark before/after

**Files:**
- Modify: `detector/db_optimizer.py` solo si la API existente no cubre la migración.
- Create: `scripts/benchmark_p0_p1.py`
- Create: `outputs/p0_p1_comparison.md`
- Test: `tests/test_benchmark_p0_p1.py`

- [ ] Medir baseline en copia temporal sin mutar la base del usuario.
- [ ] Aplicar índices en copia, ejecutar `ANALYZE` y medir planes/tiempos.
- [ ] Ejecutar tests completos.
- [ ] Generar comparación con etiquetas `synthetic`/`historical`/`forward` y declarar no probado lo que no sea forward.

### Task 7: Verificación final

- [ ] Ejecutar `python -m unittest discover -s tests -v`.
- [ ] Ejecutar benchmark reproducible dos veces y comprobar determinismo.
- [ ] Revisar diff y archivos generados.
- [ ] Informar métricas antes/después y limitaciones.
