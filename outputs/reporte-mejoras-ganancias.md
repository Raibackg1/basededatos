# ⚠️ SUPERSEDED — SUPERADO POR ANÁLISIS POSTERIOR

> **Este informe ha sido SUPERADO** por el barrido causal (`scripts/causal_sweep.py`).
> Cita cifra no validada OOS: **81.1% WR / PF 18.93** (línea 9).
> La verdad medida: **12.55% WR / PF 0.269 / −79,771 USD** (5,021 trades, `custom.db`).
> Mantener solo para historial. Ver `outputs/INDEX.md` y `STATUS.md`.

---

# Informe de mejoras para aumentar ganancia neta

**Proyecto:** `pumpfun_detector`  \\
**Objetivo:** maximizar expectancia neta post-costes/slippage, no winrate aislado.  
**Bases:** auditoría local, arquitectura vigente, código de `MasterSnipeSignal`, `costs`, `live_store`, `recalibrator`, tests y referencias técnicas externas.

## 1. Veredicto ejecutivo

El sistema ya tiene valor real: filtro maestro O(1), vetos forenses, TP/SL multi-etapa, ledger forward y recalibración. Sin embargo, el rendimiento histórico alto (81.1% WR / PF 18.93) no puede promocionarse hasta cerrar la medición. El mayor riesgo de ganancia no es falta de señal, sino falsa evidencia, leakage temporal, slippage subestimado y sizing agresivo.

Mejora principal: convertir el detector en un sistema **auditable, reproducible y neto**, donde cada decisión tenga `data_origin`, hash de base, timestamp de entrada, outcome resuelto y PnL neto por el mismo `ExecutionCostModel`.

## 2. Problemas que hoy pueden destruir ganancia

### P0 — Medición y leakage

1. `market_cap` final puede influir a la señal (`features.py` + `MasterSnipeSignal.evaluate()` prioriza mejor que `mc_at_entry_est`).
2. `graduated` y outcomes (`win_*`, `future_ret_*`, `mfe_*`, `mae_*`, `exit_*`, `pnl_*`, `hold_s`, `status`) no deben entrar como features.
3. Meta-labeler evalúa sobre todo `trades_df`, no únicamente test/forward sellado.
4. `reglas_produccion.json` puede exportar reglas sin representación ejecutable ni validación OOS real.
5. Costes inconsistentes: `costs.py` usa 5.64% roundtrip, mientras simuladores usan haircuts fijos y fees 2x 0.002 SOL.

### P1 — Ejecución y capacidad

1. Sizing por liquidez real y riesgo máximo, permitiendo `0.0`.
2. Veto por `priceImpactPct`, quote stale, profundidad y latencia.
3. Registrar quote-outcome, fill, retry, route y tiempo firma→confirmación.
4. Separar PnL neto por rama, hora, creador, cabal y mercado.
5. Detectar drift y recalibrar solo con muestra suficiente.

## 3. Capa de base de datos

### 3.1. Integridad de entrada

Crear tabla invariant:

```sql
CREATE TABLE features_at_entry (
  token_id TEXT NOT NULL,
  feature_name TEXT NOT NULL,
  feature_value REAL,
  observed_at_ms INTEGER NOT NULL,
  available_at_ms INTEGER NOT NULL,
  data_origin TEXT NOT NULL,
  source_table TEXT NOT NULL,
  PRIMARY KEY (token_id, feature_name, observed_at_ms)
);
```

Regla: señal solo lee `available_at_ms <= entry_time`. `market_cap` final queda prohibido salvo `asof_time <= entry_time`.

### 3.2. Ledger de decisiones inmutable

`live_store.py` ya persiste decisión por firma. Mejorar:

- idempotencia por `(decision_signature, data_origin, entry_ts)`;
- `db_hash` (SHA-256);
- `code_git_sha`, `config_hash`;
- cobertura (`tokens_missing_snapshots`, `quoted_without_fill`);
- resultado: `PENDING / WIN / LOSS / INVALID`;
- PnL neto con fees, priority fee, slippage real y settlement.

### 3.3. DB optimization focalizada

No migrar a DuckDB/Parquet por moda. Solo si volumen multi-hora justifica. Primero:

- PRAGMA `journal_mode=WAL`, `synchronous=NORMAL`;
- índices por `(token_id, block_slot/timestamp)` y `(data_origin, decision_ts)`;
- `ANALYZE`;
- exportaciones columnares por periodo;
- compactación periódica con `VACUUM`;
- integridad mediante checksums y validación de vacíos.

### 3.4. Gobierno de resultados

Cada reporte (`reporte.html`, markdown o JSON) debe incluir:

```json
{
  "data_origin": "historical | synthetic | dry_run | forward_live",
  "db_hash": "...",
  "time_range": ["2026-...", "2026-..."],
  "unique_tokens": 52169,
  "trades": 4990,
  "eligible_trades": 128,
  "cost_model_version": "..."
}
```

## 4. Código

### 4.1. Anti-lookahead real

Implementar `FeatureAvailabilityGuard` que:

- bloquea `graduated`, `win_*`, `fut_ret_*`, `mfe_*`, `mae_*`, `tpsl_*`, `exit_*`, `pnl_*`, `hold_s`, `status`;
- valida que ningún feature cambie cuando se añaden filas futuras;
- falla tests si una feature post-entrada entra al conjunto.

### 4.2. Congelar `market_cap` de entrada

En `MasterSnipeSignal.evaluate()` queda ya la prioridad favorables. Requisito adicional:

- no usar fallback `market_cap` final si `market_cap_at_entry`/`mc_at_entry_est`/`marketCapAtEntry` son >0;
- registrar por métrica qué campo se usó (`market_cap_source`);
- rechazar si fuente final sin `asof_time`.

### 4.3. Meta-labeler por bloque temporal

Separar explícitamente:

```text
train: descubrir reglas/modelo
validation: calibrar umbral/política
test: evaluación final única
forward: datos nuevos posteriores
```

No llamar “OOS” a métricas calculadas sobre todo `trades_df`.

### 4.4. Exportación de reglas

`reglas_produccion.json` solo admite cada regla si tiene:

- soporte mínimo train y test;
- q-value calculado en train;
- lift neto positivo en validation y test;
- representación estructurada ejecutable, no texto;
- `oos_pass=true`;
- intervalo/bootstrap y estabilidad por bloques.

### 4.5. Costos y sizing

Un único `ExecutionCostModel` recibe:

- tamaño;
- liquidez;
- route;
- prioridad;
- impacto esperado;
- slippage por cuantil.

Retorna PnL neto. Mismas funciones para labels, TP/SL, backtest, reporte y sizing.

Sizing:

```text
if expected_net_ev <= 0: return 0
if daily_drawdown >= daily_limit: return 0
target = portfolio * quarter_kelly
return min(max_cap, max(0.01, target)) # o 0 si liquidez real < min viable
```

Añadir kill-switch por correlación createdo/cluster, max drawdown diario y número mínimo de trades forward.

## 5. Integraciones expertas

### 5.1. Validación temporal inspirada en Freqtrade/scikit-learn

Adoptar validador interno sin dependencia nueva:

- `TimeSeriesSplit` con `gap` entre calibración y test;
- validación purgada para labels de horizonte variable;
- lookahead-check recalculando features fila por fila vs batch;
- block bootstrap.

### 5.2. Quote y price impact (Jupiter)

`QuoteProvider` debe vetar cuando:

- `priceImpactPct > max`;
- quote age > límite;
- otra amount por debajo del threshold;
- route no permitida;
- cambio de reservas entre quote y envío.

Guardar route, impacto, slippage real para reconstruir PnL.

### 5.3. Latencia y listeners (Chainstack / PumpPortal)

Definir `latency_budget`:

```text
detección → extracción → quote → firma → inclusión
```

Si evento incompleto o curva no refrescable en presupuesto, **no comprar**. Esto sacrifica volumen y mejora ganancia.

### 5.4. Arquitectura de ejecución (Hummingbot-style)

Separar adapters:

- `SignalProvider`;
- `QuoteProvider`;
- `OrderSubmitter`;
- `PositionManager`;
- `TradeRecorder`.

La señal nunca conoce RPC, wallet, route ni confirmación. Esto reduce riesgo y acelera integraciones.

### 5.5. Features on-chain de riesgo (paper Solana)

Añadir primero como etiquetas, luego medir valor incremental neto:

- `TVL drawdown`;
- `idle duration`;
- migración;
- `bundled_buy_share`;
- historial de creador;
- supervivencia de liquidez.

No convertirlas automáticamente en señales de compra.

## 6. Protocolo de validación y promoción

Promocionar una estrategia solo si cumple simultáneamente:

1. ≥100 observaciones forward o mínimo estadístico justificado por IC.
2. PnL neto positivo en test y forward.
3. PF y expectancy neta positivos después de fees, slippage, priority fees.
4. Sin leakage detectado por replay incremental.
5. Estabilidad en varios bloques temporales.
6. Drawdown compatible con capital.
7. Fill reproducible con mismo contrato de datos.
8. Sizing limitado por liquidez y kill-switch activo.

Winrate por sí solo nunca debe ser criterio.

## 7. KPI reales a optimizar

- Net expectancy por trade.
- Profit factor neto.
- Max drawdown.
- CAGR/Sortino.
- Winrate solo como diagnóstico.
- Fill rate.
- Slippage realized vs previsto.
- Latencia evento→confirmación.
- Capacity por creador/cluster/cabal.
- Drift de umbrales.

## 8. Roadmap

### Fase P0 (obligatoria)

- `FeatureAvailabilityGuard`.
- congelar `market_cap_at_entry`.
- excluir outcomes y `graduated`.
- separar train/validation/test/forward.
- reglas ejecutables y `oos_pass`.
- `ExecutionCostModel` único.
- tests de no-fuga y no-exportación.
- ledger con provenance.

### Fase P1 (más PnL)

- price impact, quote stale, profundidad, latencia.
- sizing real con `0` posible.
- registro quote/fill/retries.
- branch slicing.
- límites por creador/cabal.
- drift/recalibración condicionada.

### Fase P2

- modelos complejos o meta-labeling adicional.
- features de redes/keywords (solo valor incremental).
- DuckDB/Parquet si el volumen lo justifica.
- Hummingbot integration modular.

## 9. Tests mínimos

```text
python -m unittest discover -s tests -v
```

Añadir pruebas para:

- no leakage incremental;
- `graduated` excluido;
- `market_cap` final vetado;
- reglas sin representación no se exportan;
- cost model consistente entre módulos;
- sizing devuelve 0 si EV/líquidez/drawdown invalidan;
- resultados marcados por `data_origin`.

## 10. Conclusión

La mayor mejora de ganancia no viene de añadir más ramas, sino de **eliminar falsas ventajas** y medir PnL neto de punta a punta. Una estrategia con menor winrate pero mayor fill rate, menor slippage y sizing prudente puede ser más rentable que el 81.1% actual, especialmente frente a rugs y cabales.
"""
write to=$PUMPSCOPE_SKILLS_DIR  # placeholder ignored