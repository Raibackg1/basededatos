# Auditoría de winrate, integraciones y mejoras reales

**Proyecto auditado:** `pumpfun_detector`  
**Fecha:** 2026-10-13  
**Alcance:** revisión estática del código, artefactos generados, pruebas locales y referencias open source. No se modificó la estrategia ni se habilitó trading.

## Veredicto principal

Antes de intentar subir el winrate, hay que corregir la medición. El sistema tiene buenas piezas —entrada temporal (`entry_features`), partición temporal, modelado explícito de costes, vetos de cabales/bloque 0 y auditoría SQLite—, pero hoy no se puede tratar el **81.1% WR / PF 18.93** como rendimiento real generalizable.

La razón principal es que la ruta de producción todavía puede beneficiarse de variables posteriores a la entrada y de métricas calculadas sobre el mismo conjunto donde se descubrieron o ajustaron reglas. Eso puede aumentar artificialmente winrate, profit factor y Kelly.

> Objetivo realista: maximizar **expectancy neta después de costes y slippage**, no perseguir el mayor winrate. Un filtro que toma muy pocas operaciones puede mostrar WR alto y perder dinero por selección, latencia o tamaño insuficiente.

## Problemas críticos encontrados

### 1. `market_cap` puede ser una variable futura dentro de la señal maestra

En `detector/features.py`, `token_features()` copia `market_cap` desde la tabla de tokens. Después `trade_features()` calcula `mc_at_entry_est`, pero `MasterSnipeSignal.evaluate()` prioriza `marketCap`, luego `mc_at_entry_est` y solo después `market_cap`.

En la práctica, si el registro contiene `market_cap` final o actualizado, la señal puede decidir usando información que no existía al momento de entrar. Esto afecta directamente al veto `MC < $4.5k`, a la rama A y al veto ATH.

**Corrección prioritaria:** congelar un esquema `features_at_entry` con timestamp de disponibilidad; la señal solo debe leer campos cuya observación sea anterior o igual a `entry_time`. `market_cap` final debe quedar prohibido salvo que tenga `asof_time <= entry_time`.

### 2. `graduated` no está excluida de la minería

`graduated` se deriva de la metadata del token y puede representar un resultado posterior —migración a Raydium—, no una observación disponible al entrar. Aunque `main.py` excluye varias columnas de resultado, no añade `graduated` a `cfg.exclude_features`.

El propio proyecto la usa también como etiqueta (`win_grad`). Si se deja como feature, se produce una fuga directa o una proxy del futuro.

**Corrección prioritaria:** excluir `graduated`, `win_*`, `fut_ret_*`, `mfe_*`, `mae_*`, `tpsl_*`, `exit_*`, `pnl_*`, `hold_s`, `status` y cualquier campo cuyo `available_at` sea posterior a la entrada. Añadir un test que falle si una feature post-entrada entra en el conjunto de entrenamiento.

### 3. El meta-labeler se evalúa sobre todo el histórico seleccionado, no solo sobre el test OOS

El meta-labeler se ajusta con train y calibra con validation, pero `main.py` llama `predict_oos(alpha_df_master)`, donde `alpha_df_master` contiene candidatas de todo `trades_df`. Luego `m_alpha_dyn` y el Kelly se calculan sobre ese conjunto completo.

Eso no es una evaluación OOS sellada. El resultado puede incluir train, validation y test, y además usa la misma muestra que sirvió para construir la señal maestra.

**Corrección prioritaria:** separar explícitamente:

- `train`: descubrir reglas y ajustar modelo;
- `validation`: calibrar umbral y elegir política;
- `test`: una sola evaluación final, sin recalibrar;
- `forward`: datos nuevos, posteriores al cierre del desarrollo.

El reporte debe mostrar métricas separadas por bloque y nunca llamar “OOS” a un resultado calculado sobre todo el dataset.

### 4. Se exportan reglas no demostradas fuera de muestra

`top_export_rules` incorpora reglas de subgrupos y árboles antes de exigir una validación OOS. Solo una parte de las confluencias pasa por el filtro `fdr_sig & oos_pass`. Además, `evaluate_rules_oos()` solo puede evaluar reglas que conservan `conds`; las reglas de `SubgroupMiner` se convierten a texto y no conservan una representación ejecutable completa.

Por tanto, `reglas_produccion.json` puede contener reglas con FDR significativo in-sample, pero sin prueba temporal válida.

**Corrección prioritaria:** exportar únicamente reglas con:

- soporte mínimo en train y test;
- q-value calculado dentro de train, no en todo el histórico;
- lift neto positivo en validation y test;
- intervalo de confianza o bootstrap temporal;
- representación estructurada ejecutable, no solo texto;
- `oos_pass=true` obligatorio para producción.

### 5. Las métricas impresas como producción no son una prueba forward

El `README` y varios artefactos presentan 81.1% WR, PF 18.93 y +59.69% como datos auditados. El checkout local documenta una base canónica externa (`$PUMPSCOPE_DB`), pero la base disponible aquí es `demo_pumpfun.db`, descrita por el propio diseño como sintética/intencional.

**Corrección prioritaria:** cada reporte debe incluir `data_origin`, hash de base, intervalo temporal, número de tokens únicos, número de operaciones, cobertura, costes, y si el resultado es `synthetic`, `historical`, `backtest`, `dry_run` o `forward_live`. Nunca mezclar demo con evidencia de rentabilidad.

### 6. El modelo de ejecución usa haircuts fijos donde se necesita una distribución condicional

`realistic_tpsl_outcome()` aplica un haircut empírico fijo para stop y timeout. Es mejor que asumir un stop ideal, pero no modela de forma condicional el tamaño de la orden, profundidad, price impact, prioridad, congestión, route, latencia ni probabilidad de no-fill.

**Corrección prioritaria:** registrar por operación quote solicitado, `outAmount`, `priceImpactPct`, slippage permitido, route, priority fee, tiempo de detección, tiempo de firma, tiempo de inclusión, tiempo de confirmación, fill parcial y precio ejecutado. Simular costes por cuantiles condicionados a liquidez y tamaño, no con un único promedio.

### 7. Inconsistencia de costes entre módulos

`detector/costs.py` usa un roundtrip de 5.64%, mientras que varios simuladores usan dos fees fijas de 0.002 SOL y un haircut separado. Estas convenciones no son equivalentes para todos los tamaños de posición.

**Corrección prioritaria:** un único `ExecutionCostModel` que reciba tamaño, liquidez, route y prioridad, y devuelva PnL neto. Las mismas funciones deben usarse para labels, grid TP/SL, backtest, reporte y sizing.

### 8. El sizing actual puede ser demasiado agresivo para una distribución pesada de rugs

`calculate_position_size_sol()` usa 10% del portfolio con mínimo 0.01 SOL. Fractional Kelly solo es confiable si `p`, ganancias y pérdidas están estimados fuera de muestra y con suficiente estabilidad. En memecoins, una pérdida extrema o correlación entre posiciones puede invalidar el sizing.

**Corrección prioritaria:** sizing por riesgo máximo y liquidez ejecutable, con límite diario de pérdida, límite de exposición por creador/cluster, correlación por evento, kill-switch y un mínimo de operaciones forward antes de escalar. El mínimo debe poder ser cero si el EV neto o la liquidez no son suficientes.

## Integraciones open source que sí aportan valor

### Validación temporal y lookahead: Freqtrade / scikit-learn

Freqtrade documenta que el backtest puede asumir fills ideales, stoploss exacto y orden intra-vela desconocido; también ofrece una comprobación específica de lookahead bias. Su recomendación operativa es validar con timeframe más detallado y luego confirmar en dry-run, porque el backtest no sustituye la ejecución simulada real.

`TimeSeriesSplit` de scikit-learn mantiene el orden temporal y permite un `gap` entre train y test. El proyecto ya tiene una partición temporal básica, pero debe extenderse a un protocolo purgado con etiquetas de horizonte variable.

**Integración recomendada:** adoptar un validador interno inspirado en `lookahead-analysis`, sin añadir Freqtrade como dependencia: recalcular features fila por fila o por ventanas crecientes y comparar contra el cálculo batch. Si cambia una feature al añadir datos futuros, queda marcada como contaminada.

### Ejecución modular y gestión de órdenes: Hummingbot

Hummingbot separa conectores, estrategias y ejecutores. Sus ejecutores de posición encapsulan ciclo de vida, stops y salidas. La arquitectura es útil para extraer un `ExecutionAdapter` del detector: la señal no debe conocer detalles de RPC, wallet, route o confirmación.

**Integración recomendada:** crear interfaces separadas para `SignalProvider`, `QuoteProvider`, `OrderSubmitter`, `PositionManager` y `TradeRecorder`. La señal emite una intención; el ejecutor decide si el quote es ejecutable.

### Latencia y escucha de lanzamientos: Chainstack pumpfun-bonkfun-bot

El repositorio ofrece listeners por logs, blocks, Geyser y PumpPortal. Documenta que un evento incompleto debe saltarse, que el modo rápido elimina lecturas RPC intermedias solo cuando el evento on-chain contiene los datos canónicos, y que una lectura de curva no disponible dentro del presupuesto de refresco debe cancelar la compra.

**Integración recomendada:** medir y usar un `latency_budget`: detección → extracción → quote → firma → inclusión. Si los datos del evento no están completos o la curva no puede refrescarse dentro del límite, **no comprar**. Esto puede mejorar PnL aunque reduzca el número de trades.

### Cotización y price impact: Jupiter

La Quote API devuelve ruta, `outAmount`, `priceImpact` y permite `slippageBps`, además de restringir tokens intermedios. La ejecución debe usar el quote real y rechazar operaciones cuyo impacto o salida mínima exceda el riesgo permitido.

**Integración recomendada:** veto por `priceImpactPct`, `otherAmountThreshold`, ruta no permitida, quote demasiado antiguo y cambio de reservas entre quote y envío. Registrar la ruta para poder reconstruir el PnL.

### Detección de rugs y features on-chain: investigación open source reciente

Un trabajo reciente sobre memecoins de Solana usa datos de PumpFun y Raydium, incluyendo bundles, balances del dev, compradores/vendedores únicos, ratios de volumen y cambios de TVL. Define rug pull mediante caída de TVL o inactividad prolongada y usa validación rolling temporal con una prueba final reservada.

**Integración recomendada:** añadir etiquetas de riesgo separadas para `TVL drawdown`, `idle duration`, `migration`, `bundled_buy_share`, `creator history` y `liquidity survival`. No convertirlas automáticamente en señal de compra; primero medir su valor incremental neto.

## Mejoras priorizadas por impacto esperado

### P0 — Imprescindibles antes de seguir optimizando

1. Implementar `FeatureAvailabilityGuard` y bloquear variables posteriores a `entry_time`.
2. Corregir el uso de `market_cap` final en `MasterSnipeSignal`.
3. Excluir `graduated` y cualquier outcome de la minería.
4. Hacer que el meta-labeler reporte solo validation/test/forward por separado.
5. Exportar a producción únicamente reglas con representación ejecutable y validación temporal OOS real.
6. Unificar el modelo de costes netos.
7. Añadir tests de no-fuga y de no-exportación de reglas in-sample.

### P1 — Mejoras que probablemente aumenten el PnL neto, no solo el WR

1. Veto de ejecución por price impact, quote stale, profundidad insuficiente y latencia excedida.
2. Sizing por liquidez ejecutable y riesgo máximo, con posibilidad real de `0`.
3. Registrar y modelar prioridad, route, fill, retries y confirmación on-chain.
4. Recalibrar umbrales por régimen temporal y detectar concept drift.
5. Separar reglas por rama A/B/C/D y medir PnL neto por rama, hora, creador y cluster.
6. Incorporar un límite de pérdidas correlacionadas por cabal/creador, no solo por token.

### P2 — Solo después de P0/P1

1. Modelos más complejos o meta-labeling adicional.
2. Features de nombre, redes sociales y keywords; primero demostrar valor incremental OOS.
3. Migrar a DuckDB/Parquet si el volumen de snapshots lo justifica.
4. Integrar Hummingbot o un ejecutor propio modular, sin mezclarlo con el detector.

## Criterio de promoción a producción

Una regla solo debería promocionarse si cumple simultáneamente:

- al menos 100 observaciones forward o el mínimo estadístico justificado por intervalo de confianza;
- PnL neto positivo en test y forward;
- PF neto y expectancy neta positivos después de fees, slippage y prioridad;
- sin fuga detectada por replay incremental;
- estabilidad en varios bloques temporales;
- pérdida máxima y drawdown compatibles con el capital;
- ejecución real reproducible con el mismo contrato de datos;
- kill-switch activo y sizing limitado por liquidez.

El winrate por sí solo no debe ser criterio de promoción.

## Pruebas locales realizadas

- `python -m unittest tests/test_database_audit.py -v`: **26/26 OK**.
- `python main.py --db demo_pumpfun.db --audit-only --data-origin synthetic --out resultados\\auditoria_actual`: completado en solo lectura.
- La suite cuantitativa histórica requiere NumPy/pandas/scikit-learn en el entorno local; no se instalaron dependencias durante esta auditoría.

## Fuentes

[1]: https://www.freqtrade.io/en/stable/backtesting/ "Freqtrade backtesting assumptions and improved accuracy"
[2]: https://www.freqtrade.io/en/stable/lookahead-analysis/ "Freqtrade lookahead-analysis"
[3]: https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html "scikit-learn TimeSeriesSplit"
[4]: https://github.com/chainstacklabs/pumpfun-bonkfun-bot "Chainstack pumpfun-bonkfun-bot"
[5]: https://github.com/hummingbot/hummingbot "Hummingbot open-source trading framework"
[6]: https://dev.jup.ag/docs/swap/v1/get-quote "Jupiter Swap Quote API"
[7]: https://arxiv.org/html/2608.20271 "Early Prediction of Fraudulent Memecoins on Solana via ..."
[8]: https://www.sqlite.org/eqp.html "SQLite EXPLAIN QUERY PLAN"
[9]: https://www.sqlite.org/lang_analyze.html "SQLite ANALYZE and PRAGMA optimize"
