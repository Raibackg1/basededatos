# Diseño P0+P1 — Integridad temporal, PnL neto y ejecución segura

**Fecha:** 2026-10-14  
**Proyecto:** `pumpfun_detector`  
**Estado:** aprobado en conversación; pendiente de revisión del documento antes de implementar.

## 1. Objetivo

Convertir el detector en un sistema medible y reproducible, sin afirmar rentabilidad que dependa de datos posteriores a la entrada, datos sintéticos mezclados con datos reales o costes de ejecución inconsistentes.

El alcance incluye mejoras P0 y P1 útiles para la calidad de decisión y el PnL neto. No incluye migrar a DuckDB/Parquet, añadir nuevos modelos ML ni conectar fondos reales.

## 2. Alcance funcional

### P0 — Integridad y evidencia

1. **Availability guard**
   - Crear una validación reutilizable de features.
   - Prohibir outcomes y campos post-entrada: `graduated`, `win_*`, `future_ret_*`, `fut_ret_*`, `mfe_*`, `mae_*`, `tpsl_*`, `exit_*`, `pnl_*`, `hold_s`, `status`.
   - Exigir `available_at_ms <= entry_time` cuando exista metadata de disponibilidad.
   - Detectar contaminación comparando features calculadas con un dataset truncado frente al dataset completo.

2. **Market cap as-of-entry**
   - Preferir `market_cap_at_entry`, `mc_at_entry_est` o `marketCapAtEntry`.
   - Rechazar el fallback a `market_cap` final cuando no exista timestamp `asof` compatible.
   - Registrar `market_cap_source` y `market_cap_at_entry_ts`.

3. **Validación temporal**
   - Separar explícitamente `train`, `validation`, `test` y `forward`.
   - Evitar que las etiquetas de horizontes solapados crucen los límites sin purge/gap.
   - No etiquetar como OOS ninguna métrica calculada sobre todo el dataset.

4. **Reglas ejecutables**
   - Exportar condiciones estructuradas además del texto humano.
   - Añadir soporte, bloque temporal, lift neto, intervalo bootstrap y `oos_pass`.
   - No exportar una regla como producción si carece de validación test/forward suficiente.

5. **Modelo único de costes**
   - Usar `ExecutionCostModel` en labels, TP/SL, backtest, sizing, reportes y forward.
   - Incorporar fees, priority fee, network fee, slippage, price impact y ruta.
   - Mantener por separado gross PnL y net PnL.

6. **Provenance**
   - Cada reporte debe declarar `data_origin`, `db_hash`, `code_git_sha`, `config_hash`, rango temporal, cobertura y versión del modelo de costes.
   - Demo/synthetic no se presenta como evidencia real.

### P1 — Ejecución y control de riesgo

1. **Quote gate**
   - Rechazar quote inválido, viejo, con price impact superior al umbral, profundidad insuficiente o ruta no permitida.
   - Registrar `route`, `price_impact_pct`, `quote_age_ms`, `out_amount`, `slippage_bps` y motivo de rechazo.

2. **Latency budget**
   - Medir detección → extracción → quote → firma → inclusión → confirmación.
   - Rechazar eventos incompletos o fuera de presupuesto.

3. **Sizing seguro**
   - Permitir tamaño cero cuando EV neto, liquidez, drawdown o datos no sean válidos.
   - Aplicar cap por posición, creador/cabal, correlación y pérdida diaria.
   - No usar Kelly sin cap ni como único criterio.

4. **Ledger auditable**
   - Extender el ledger con timestamps de entrada, procedencia, hash DB/código/config, versión de costes y trazas quote/fill/retry.
   - Conservar outcomes resueltos sin mezclar demo con live.
   - Mantener idempotencia por firma que incluya decisión, procedencia y timestamp de entrada.

5. **Segmentación de resultados**
   - Calcular PnL neto por rama, hora, creador, cabal/cluster, route y proveedor.
   - Reportar fill rate y slippage previsto frente a realizado.

## 3. Flujo de datos

```text
fuente SQLite/feed
      │
      ▼
perfil + provenance + hash
      │
      ▼
features as-of-entry ──► AvailabilityGuard ──► validación temporal
      │                                         │
      └─────────────────────────────────────────┘
                         ▼
                 MasterSnipeSignal
                         │
              QuoteGate + LatencyBudget
                         │
                 Sizing / kill-switch
                         │
                  decisión persistida
                         │
                outcome neto persistido
                         │
             auditoría por segmentos
```

## 4. Compatibilidad y seguridad

- Se conserva `MasterSnipeSignal` como único punto de decisión.
- Los cambios de esquema SQLite serán aditivos y migrables.
- No se ejecutarán operaciones on-chain ni se habilitará trading real.
- Las PRAGMA de concurrencia se aplicarán solo en conexiones configuradas para ello y se medirán; no se supondrá que mejoran la rentabilidad.
- No se añadirán dependencias externas.

## 5. Verificación

### Tests automatizados

- No leakage incremental.
- `graduated` y outcomes excluidos.
- `market_cap` final rechazado sin as-of.
- Reglas sin condiciones ejecutables no exportadas.
- Cost model idéntico entre módulos.
- Sizing devuelve `0` en EV, liquidez o drawdown inválidos.
- Quote stale/impacto/profundidad bloqueados.
- Provenance obligatorio.
- Ledger idempotente y outcomes aislados por procedencia.

### Comparación antes/después

Se ejecutará el mismo benchmark determinista sobre la misma base sintética y, si está disponible, sobre los mismos datos históricos etiquetados. Se compararán:

- número de observaciones elegibles;
- winrate solo como diagnóstico;
- expectancy neta;
- profit factor neto;
- drawdown;
- trades rechazados por leakage/quote/liquidez;
- fill rate y slippage;
- tiempo de consultas SQLite;
- resultados por bloque temporal.

La comparación marcará explícitamente qué métricas son sintéticas, históricas o forward. No se declarará mejora de rentabilidad real sin datos forward nuevos.

## 6. Criterio de aceptación

El trabajo se considera correcto si:

1. La suite existente y los nuevos tests terminan con código 0.
2. Las métricas contaminadas dejan de exportarse como evidencia OOS.
3. El benchmark reproducible produce resultados deterministas.
4. La instrumentación demuestra el PnL neto y los vetos P1 sin mocks silenciosos.
5. El reporte final distingue claramente mejoras de medición, rendimiento técnico y rentabilidad aún no demostrada.
