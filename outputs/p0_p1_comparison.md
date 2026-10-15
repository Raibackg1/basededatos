# Comparación P0+P1 (benchmark local reproducible)

- **Fuente:** `demo_pumpfun.db`
- **Procedencia:** `synthetic` (no evidencia forward)
- **Base original:** no modificada; se usaron copias temporales.

## Gates y anti-leakage

| Prueba | Antes/compatibilidad | Después P0+P1 |
|---|---:|---:|
| Decisiones admitidas en muestras (legacy) | 3 | 1 |
| Rechazo quote stale/impacto | no medido | OK |
| Sizing EV negativo devuelve 0 | no medido | OK |

La muestra contiene dos vectores con `market_cap` final sin as-of y uno con `market_cap_at_entry`; el modo estricto rechaza los dos primeros. Esto demuestra prevención de contaminación, no una mejora de rentabilidad.

## SQLite

| Consulta | Antes ms | Después ms | Variación |
|---|---:|---:|---:|
| trades por token | 26.0924 | 28.8492 | +10.6% |
| trades ordenados | 31.5998 | 32.7477 | +3.6% |
| my_trades por token | 0.0236 | 0.0234 | -0.8% |

Índices aplicados: 3.

### Planes posteriores

- **trades por token:** `SCAN trades`
- **trades ordenados:** `SCAN trades ; USE TEMP B-TREE FOR ORDER BY`
- **my_trades por token:** `SCAN my_trades`

## Conclusión

P0+P1 mejora la integridad de las decisiones y reduce riesgo operativo. La optimización SQLite solo se considera una mejora técnica; el benchmark sintético no prueba que la estrategia gane más dinero. Para demostrar mejora económica faltan outcomes `forward_live` netos con fills y slippage reales.
