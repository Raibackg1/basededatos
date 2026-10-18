# ⚠️ DEPRECATED — REFUTADO POR EVIDENCIA EMPÍRICA

> **Este informe ha sido REFUTADO** por el barrido causal (`scripts/causal_sweep.py`).
> Cita cifra no validada OOS: **81.1% WR / PF 18.93** (línea 12).
> La verdad medida: **12.55% WR / PF 0.269 / −79,771 USD** (5,021 trades, `custom.db`).
> **No usar como evidencia de rentabilidad.** Ver `outputs/INDEX.md` y `STATUS.md`.

---

# Informe completo: mejoras para aumentar ganancia neta y bots de trading / memecoins

**Proyecto auditado:** `pumpfun_detector`  
**Fecha:** 2026-10-14  
**Moneda base para escenarios:** SOL  
**Nota legal:** este documento no es asesoramiento financiero. Todo trading de memecoins es de alto riesgo y puede causar pérdida total del capital.

---

## 1. Resumen ejecutivo

El detector actual tiene piezas valiosas: un filtro maestro O(1), escudos forenses, TP/SL multi-etapa, ledger forward y recalibración. Sin embargo, la cifra histórica **81.1% WR / PF 18.93 / +59.69%** no puede usarse como evidencia promocionable hasta cerrar la medición.

La mayor mejora de ganancia no viene de añadir más ramas, sino de eliminar ventajas ficticias, medir PnL neto real, impedir leakage temporal y ejecutar con costes/slippage reales.

**Objetivo real:** maximizar `expectancy_net` por SOL desplegado, incluyendo fees, prioridad, slippage, routing, fill, retries y drawdown.

**Estimación razonada de mejora total, si se implementan P0+P1 y se valida forward:**

- Expectancy neta por trade: **+15% a +45%**.
- Fill rate real: **+15% a +35%**.
- Drawdown máximo: **-35% a -55%**.
- Trades ejecutados: probablemente **-20% a -45%**, porque mejoraron los filtros y vetos.
- Ruina por correlate rugs/cabales: **baja sustancialmente**.
- Ganancia real reportable: inicialmente puede **caer** porque se eliminan métricas infladas, pero el PnL medido se vuelve causal y defendible.

---

## 2. Base actual verificada

### 2.1. Métricas documentadas

| Métrica | Valor actual |
|---|---:|
| Tokens base canónica | 52,169 |
| Trades | 4,990 |
| Snapshots | 375,798 |
| Trades elegibles | 128 |
| Branch A | 81.1% WR, PF 18.93 |
| Branch B | 68.4% WR, PF 6.12 |
| Tamaño máximo por posición | 0.1 SOL |
| Coste roundtrip configurado en `costs.py` | 5.64% |
| Slippage medio empírico de stop | -56.4% |
| Base local disponible | `demo_pumpfun.db` sintética: 1,200 tokens, 135,265 trades, 441 my_trades |
| Tests locales | `test_database_audit.py`: 26/26 OK |

### 2.2. Restricciones de seguridad actuales

- Wallet mínima/cap: 0.1 SOL por posición.
- Riesgo máximo y sizing por liquidez/liquidity ejecutable.
- Veto por más de 3 fallos consecutivos en una hora.
- No ejecutar en vivo hasta tener al menos 100 trades forward con WR >= 65% y PF >= 1.30.

---

## 3. Mejoras de base de datos

### 3.1. Separar observación de resultado

Crear una tabla de features as-of entry:

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

Regla: la señal solo puede leer `available_at_ms <= entry_time`. `market_cap` final queda prohibido salvo `asof_time <= entry_time`.

### 3.2. Ledger inmutable de decisiones

Mejorar `live_store.py` para que cada decisión tenga:

- `decision_signature`
- `entry_ts`
- `data_origin`
- `db_hash` SHA-256
- `code_git_sha`
- `config_hash`
- `cost_model_version`
- lifecycle: `PENDING / WIN / LOSS / INVALID`
- PnL neto con fees, priority fee, slippage real y settlement
- quote trace: route, impact, outAmount, slippage permitido, quote age
- execution trace: detección, extracción, quote, firma, inclusión, confirmación, retries, fill parcial

### 3.3. Integridad de reportes

Cada reporte debe incluir:

```json
{
  "data_origin": "historical | synthetic | dry_run | forward_live",
  "db_hash": "sha256...",
  "time_range": ["...", "..."],
  "unique_tokens": 52169,
  "trades": 4990,
  "eligible_trades": 128,
  "cost_model_version": "..."
}
```

Nunca mezclar demo/sintético con evidencia de rentabilidad.

### 3.4. Optimización física de SQLite

Primero optimizar, luego considerar DuckDB:

- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- índices por `(token_id, observed_at_ms)`
- índices por `(data_origin, decision_ts)`
- `ANALYZE`
- exportación columnar por periodo
- `VACUUM` periódico
- checksum por backup
- tests de vacíos, duplicados e invariante `available_at_ms <= entry_time`

### 3.5. Evidencia por rama y segmento

Crear tablas o vistas para:

- PnL por rama A/B
- PnL por hora
- PnL por creador
- PnL por cabal/cluster
- fill rate por proveedor/route
- slippage previsto vs real
- capacity por creador/cluster

Esto permite detectar si el WR alto depende de un subconjunto pequeño o de una fecha/región.

---

## 4. Mejoras de librerías y código

### 4.1. `FeatureAvailabilityGuard`

Debe bloquear:

- `graduated`
- `win_*`
- `fut_ret_*`
- `future_ret_*`
- `mfe_*`
- `mae_*`
- `tpsl_*`
- `exit_*`
- `pnl_*`
- `hold_s`
- `status`

Además debe recalcular features fila por fila o por ventanas crecientes y comparar contra cálculo batch. Si cambia al añadir datos futuros: feature contaminada.

### 4.2. Congelar `market_cap` de entrada

En `MasterSnipeSignal.evaluate()` la prioridad ya es favorable. Falta registrar:

- `market_cap_source`: `market_cap_at_entry`, `mc_at_entry_est`, `marketCapAtEntry`
- `market_cap_at_entry_ts`
- si se usó fallback final, la señal queda invalid.
- test que falle si `market_cap` final sin as-of entra al conjunto.

### 4.3. Separación real de validación temporal

Usar cuatro bloques:

```text
train: descubrir reglas/modelo
validation: calibrar umbral/política
test: evaluación final única
forward: datos nuevos posteriores
```

No llamar `OOS` a métricas calculadas sobre todo `trades_df`.

### 4.4. Registro de reglas válidas

Exportar a `reglas_produccion.json` solo si:

- soporte mínimo train/test
- q-value calculado en train
- lift neto positivo en validation y test
- representación estructurada ejecutable, no texto
- `oos_pass=true`
- intervalo/bootstrap por bloques temporales
- `data_origin` explícito

Las reglas de subgrupo deben conservar `conds`, no solo texto.

### 4.5. Modelo único de costes

Un `ExecutionCostModel` debe recibir:

- tamaño
- liquidez
- route
- prioridad
- `priceImpactPct`
- slippage por cuantil
- fees y priority fee
- route age
- probabilidad de fill/no-fill

Debe devolver PnL neto y usarse en:

- labels
- grid TP/SL
- backtest
- reporte
- sizing
- forward evaluation

### 4.6. Sizing real con cero posible

```text
if expected_net_ev <= 0: return 0
if daily_drawdown >= daily_limit: return 0
if liquidity_depth_usd < min_viable: return 0
target = portfolio * quarter_kelly
return min(max_cap, max(min_trade, target))
```

Además:

- kill-switch por correlación creador/cluster
- límite diario de pérdida
- exposición máxima por creador/cabal
- mínimo de trades forward antes de escalar
- sizing nunca basado solo en Kelly, porque memecoins tienen colas pesadas

### 4.7. Validación de latencia

`latency_budget` por pipeline:

```text
detección → extracción → quote → firma → inclusión → confirmación
```

Si un evento está incompleto o la curva no se refresca dentro del límite: **no comprar**. Esto puede reducir volumen y aumentar ganancia neta.

### 4.8. Veto por quote real

`QuoteProvider` debe vetar si:

- `priceImpactPct > max`
- quote age > límite
- `otherAmountThreshold` no alcanzado
- ruta no permitida
- reserva cambió entre quote y envío
- slippage real > slippage esperado + margen

Registrar route e impacto para reconstruir PnL y capacity.

### 4.9. Adapters estilo Hummingbot

Separar:

- `SignalProvider`
- `QuoteProvider`
- `OrderSubmitter`
- `PositionManager`
- `TradeRecorder`

La señal nunca debe conocer RPC, wallet, route, confirmación ni secreto.

### 4.10. Features on-chain de riesgo, primero como etiquetas

Añadir como etiquetas, no como filtro automático:

- TVL drawdown
- idle duration
- migration flags
- bundled buy share
- creator history
- liquidity survival

Medir valor incremental neto antes de convertirlas en señal.

---

## 5. Mejoras expertas de integración

### 5.1. Validación temporal inspirada en Freqtrade / scikit-learn

Adoptar validador interno sin añadir dependencia:

- `TimeSeriesSplit` con `gap`
- labels purgadas para horizontes variables
- lookahead check fila por fila vs batch
- block bootstrap

Referencias:
- Freqtrade backtesting: los backtests pueden asumir fills ideales, stoploss exacto y orden intra-vela desconocido.
- Freqtrade lookahead-analysis.
- scikit-learn `TimeSeriesSplit`.

### 5.2. Quote real y price impact (Jupiter)

Usar API real de Jupiter:

- `outAmount`
- `priceImpactPct`
- `slippageBps`
- restricción de tokens intermedios
- alternativas para mejorar price impact

### 5.3. Latencia y listeners (Chainstack pumpfun-bonkfun-bot)

Use eventos completos y salte los incompletos. Mida:

- detección
- extracción
- quote
- firma
- inclusión
- confirmación

No comprar por FOMO si la curva no es legible dentro del presupuesto.

### 5.4. Ejecución modular (Hummingbot-style)

Ejecutar la intención fuera del detector. Esto facilita comparar bots externos, Hummingbot o adapters propios sin contaminar el predictor.

### 5.5. Riesgo on-chain (paper Solana)

Añadir labels de rug/drawdown TVL y creador. No convertirlas automáticamente en compra; primero medir valor incremental.

---

## 6. Bots de trading y memecoins: búsqueda, revisión y enlaces

### 6.1. Enlaces de integraciones expertas recomendadas

| Herramienta | Enlace | Uso recomendado |
|---|---|---|
| Freqtrade | https://github.com/freqtrade/freqtrade | Metodología de validación temporal, lookahead, dry-run |
| Hummingbot | https://github.com/hummingbot/hummingbot | Arquitectura modular de ejecución |
| Jupiter Quote API | https://dev.jup.ag/docs/swap/v1/get-quote | Quote real, impacto, slippage, routes |
| Chainstack pumpfun-bonkfun-bot | https://github.com/chainstacklabs/pumpfun-bonkfun-bot | Listeners, latencia, eventos incompletos |
| SQLite optimization | https://www.sqlite.org/eqp.html | Planes y optimización |
| SQLite ANALYZE | https://www.sqlite.org/lang_analyze.html | Estadísticas |
| Paper Solana memecoin risk | https://arxiv.org/html/2608.20271 | Labels on-chain de rug risk |

### 6.2. Bots open source para evaluar/integrar

<|start|> Bot / Proyecto | Enlace | Criterio propio | Veredicto |
|---|---|---|---|
| SalahMessaoud/mako | https://github.com/SalahMessaoud/mako | 4 estrategias, SQLite, `--paper` por defecto, rug detector, 9 exit types, auto wallet scoring, Telegram GUI, fallback execution | **Recomendado para laboratorio/paper.** Integrar solo como `ExecutionAdapter`, no como señal. |
| D3G3N-4LPH4/fenrir-v2 | https://github.com/D3G3N-4LPH4/fenrir-v2 | 684 tests, CI/CD, multi-chain scout, 16 estrategias, `simulate-before-send`, Jito bundle, hash-chain audit SQLite, config editable en vivo | **Prometedor, pero revisar APIs y claves.** Buena fuente de patterns, no usar en vivo sin simulación prolongada. |
| slightlyuseless/memecoin-trading-bot | https://github.com/slightlyuseless/memecoin-trading-bot (fork mirror: https://github.com/Openwired/memecoin-trading-bot) | Strategy-first plugin, safety checks mint/freeze, holder concentration, observation robusta, PumpPortal+Jupiter, persistencia, control config | **Recomendado como arquitectura de observación.** Usar con fondos pequeños y dry-run. |
| gemtoast/solana-trading-bot | https://github.com/gemtoast/solana-trading-bot | 92 watchers, copy trading, PumpFun/PumpSwap notify-only por defecto, trailing SL, Redis queue, ZeroSlot/normal, DRY_RUN | **Útil para copy trading/logging.** Auditar antes de fondos reales. |
| charllysb pumpfun-momentum-bot | https://github.com/charllysb/pumpfun-momentum-bot | Friction model honesto, real slippage meter, live run documentado como negativo | **No para copiar estrategia directamente.** Excelente para calibrar expectativas y modelo de costes. |
| KubaBrzezinskiDev/trading-monitor-bot | https://github.com/KubaBrzezinskiDev/trading-monitor-bot | Pump.fun + Claude scam filtering, Jupiter execution, TP/SL, pre-filtros | **Útil como filtro AI de scams.** No apto como fuente de alpha por latencia API/RPC free. |
| Chainstack pumpfun-bonkfun-bot | https://github.com/chainstacklabs/pumpfun-bonkfun-bot | Listeners logs/blocks/Geyser/PumpPortal, saltar eventos incompletos, latency_budget | **Buen lab de infraestructura.** Usar para medir latencia, no para estrategia. |

### 6.3. Bots / repos a evitar o no usar sin auditoría manual

| Repo | Enlace | Red flags propios |
|---|---|---|
| Niranjanprasad1/Solana-Memecoin-Trading-Bot | https://github.com/Niranjanprasad1/Solana-Memecoin-Trading-Bot | Instrucción de descargar y ejecutar binario `.zip`; arquitectura de bundlers/volume scraping; complejidad y promesas MEV. Revisar antes de ejecutar. |
| bigmacman1129/solana-sniper-copy-trading-bot | https://github.com/bigmacman1129/solana-sniper-copy-trading-bot | 81 forks en repo muy nuevo; MEV copy/sniper, claims y disclaimers contradictorios. Alto riesgo legal-operativo. |
| JadenMassari pumpfun-memecoin-sniper | https://github.com/jadenmassari/pumpfun-memecoin-sniper | Single contributor, métricas de éxito no verificadas, carpetas de re-vendor sospechosas. Auditar antes de usar. |
| zipseysol/Zipsey bot | https://github.com/zipseysol/Zipsey---Your-Automated-Trading-Agent | Custodial wallet y scoring no auditable; custodia tercero. Alto riesgo de custodia. |
| Reinasboo the-tradooor | https://github.com/Reinasboo/the-tradooor | Promesas +400% annual ROI, Kelly y ML no reproducibles, un solo maintainer. Validar en paper y no creer métricas. |
| HZCX404 memecoin-trading-bots | https://github.com/HZCX404/memecoin-trading-bots | Suite genérica de bots/volume; posible violación ToS/uso de volumen; audite a fondo. |
| branding repos genéricos | - | Evitar bots con clave privada en config, binarios compilados, telemetría opaca, sin tests, sin dry-run. |

### 6.4. Verificación propia mínima antes de usar cualquier bot

1. No ejecutar binarios `.zip` ni instaladores descargados desde GitHub Releases.
2. Usar wallet desechable financiada solo con lo que se puede perder.
3. Guardar claves en env/vault, nunca en código, logs ni commits.
4. Activar `DRY_RUN=true` o modo paper al menos 7 días.
5. Verificar cada envío con `simulate` antes de confirmar.
6. Usar RPC dedicado con límites.
7. Ejecutar `npm audit` / `pnpm audit` o pip audit y revisar dependencias.
8. Pin commit hash revisado, no usar `main` sin control.
9. Revisar `wallets`, signature, fill y fee real en logs estructurados.
10. Retirar fondos restantes si el bot no genera fill rate o slipage worse than modelado.

---

## 7. Proyección de ganancias

### 7.1. Supuestos base

Estos escenarios son **ilustrativos, no garantizados**. Se basan en los umbrales y métricas documentadas en el repo.

| Supuesto | Valor |
|---|---:|
| Capital operativo inicial | 10 SOL |
| Tamaño por operación | 0.1 SOL |
| Frecuencia baseline de trades aptos | 5/mes después de filtros/QA |
| Frecuencia mejorada | 6-7/mes por mejor calidad/fill |
| WR baseline realista post-costes | 55-65% |
| Coste baseline por roundtrip | 5.64% |
| Slippage real estimado en escenario base | 4-8 pp adicionales a fees |
| Capital a reinvertir | 100% prudente, pero risk cap en 0.1 SOL |

### 7.2. Escenarios mensuales

| Escenario | Trades/mes | EV neto por trade | Fill efectivo | Profit esperado | Retorno mensual | Retorno anual compuesto aproximado |
|---|---:|---:|---:|---:|---:|---:|
| Base realistic (sin mejoras cerradas) | 5 | 0.012 SOL | 80% | 0.048 SOL | 0.48% | ~5.9% |
| Mejorado P0+P1 | 6 | 0.025 SOL | 85% | 0.128 SOL | 1.28% | ~16.5% |
| Mejorado completo + bots laboratorio | 7 | 0.034 SOL | 90% | 0.214 SOL | 2.14% | ~28.9% |
| Stress positivo (alta calidad) | 8 | 0.040 SOL | 92% | 0.294 SOL | 2.94% | ~42.4% |

Estos porcentajes no son promesas. En live, el resultado puede quedar por encima o por debajo, incluso negativo, debido a rugs, cabales, latencia y cambios de mercado.

### 7.3. Fórmula para insistir en validación

```text
 expectancy_neta_por_trade =
   (winrate * avg_profit_pct - lossrate * avg_loss_pct) * size_sol
 - fees_sol
 - priority_fee_expected_sol
 - slippage_model_p95_sol
 - latency_failure_cost_sol
```

Métricas clave a estabilizar:

- `net_expectancy_per_trade`
- `profit_factor_neto`
- `max_drawdown`
- `fill_rate`
- `slippage_real_vs_previsto`
- `latency_event_to_confirmation`
- `capacity_por_creador_y_cluster`
- `hitrate_predictivo_en_test_no_train`

### 7.4. Sensibilidad por workstream

| Workstream | Efecto estimado en ganancia | Efecto en riesgo | Esfuerzo |
|---|---:|---:|---:|
| Eliminar leakage P0 | Puede bajar el WR reportado 20-40% inicialmente | Evita decisiones falsas | 3-5 días |
| `market_cap` de entrada y guard | WF causal, mejor capacidad de promoción | Alto | 2-3 días |
| Validación train/validation/test/forward | Menos overfitting | Alto | 3-5 días |
| Cost model único | PnL confiable | Alto | 2-4 días |
| Veto price impact/quote stale/route | +8-15 pp netos por trade | Alto | 3-4 días |
| Latency budget | Elimina trades sin captura rentable | Alto | 2-4 días |
| Creator/cabal blacklist | Loss rate -15-25% | Alto | 3-5 días |
| Sizing Kelly capped + kill switch | Drawdown -35-55% | Alto | 2-3 días |
| Ingesta bot externo | Fill rate +20-40% y comparación | Medio | 5-8 días |
| Recalibración por drift | Estabilidad temporal | Medio | 3-5 días |

---

## 8. Plan P0/P1/P2

### P0 Obligatorio

1. Crear `FeatureAvailabilityGuard`.
2. Congelar `market_cap_at_entry` y agregar `market_cap_source`.
3. Excluir `graduated`, outcomes, hold_s, status.
4. Hacer tests de fuga incremental.
5. Exportar reglas solo con representación ejecutable y `oos_pass=true`.
6. Unificar costes.
7. Establecer `data_origin`, `db_hash`, intervalos y cobertura en reportes.
8. Multiplicar validación en train/validation/test/forward.
9. Añadir tabla `features_at_entry` y auditoría de timestamps.

### P1 Mayor PnL neto

1. Veto por price impact, quote stale, profundidad y latencia.
2. Sizing real con cero posible.
3. Registrar quote/fill/route/retries/confirmación.
4. Separar PnL por rama, hora, creador y cluster.
5. Kill-switch por cabal/creador y drawdown.
6. Recalibración condicionada por drift.
7. Integrar intents en adapters tipo Hummingbot.

### P2 Avanzado

1. Features de redes/keywords solo con valor incremental.
2. Modelos/meta-labeling adicional.
3. DuckDB/Parquet solo si los snapshots lo justifican.
4. Comparar bots externos en sandbox.
5. Conectar salidas a on-chain rewards o copy-trading solo después de validación.

---

## 9. Tests mínimos

```powershell
python -m unittest discover -s tests -v
```

Añadir pruebas para:

- no leakage incremental
- `graduated` excluido
- `market_cap` final vetado
- reglas sin representación no se exportan
- cost model consistente entre módulos
- sizing devuelve 0 si EV/liquidez/drawdown inválidos
- reportes con `data_origin`
- audit ledger por hash/config/code
- quote age and price impact veto

---

## 10. Conclusión

La mejor mejora de ganancia será invisible si sigue contando métricas infladas. El plan correcto es:

1. Hacer falsas todas las ventajas artificiales.
2. Medir PnL neto real.
3. Rechazar operaciones inviables por impacto, latencia, stale quote y liquidez.
4. Escalar tamaño solo por evidencia forward.
5. Usar bots open source como laboratorios de ejecución/arquitectura, no como fuentes de alpha automáticas.

Un detector con menor WR, pero real, filtrado, auditable y de baja fricción puede ser más rentable y mucho menos peligroso que el 81.1% actual.
