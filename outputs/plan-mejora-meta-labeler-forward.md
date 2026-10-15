# Diagnóstico y plan de mejora del meta-labeler

**Fecha:** 2026-10-14  
**Estado actual:** `candidate_only`; no autorizado para producción.

## 1. Lectura correcta del AUC 0,534

El valor actual es una señal débil y todavía no es una estimación confiable de rendimiento:

- El benchmark usó datos sintéticos, no outcomes forward reales.
- La evaluación usa un solo corte temporal fijo: `80` observaciones de train y `35` de test.
- No se guardaron las predicciones fila a fila ni un intervalo de confianza reproducible.
- El AUC puede cambiar mucho con pocos positivos/negativos o con un solo régimen de mercado.
- El dataset de prueba tiene una relación artificial débil (`label` periódico), por lo que no permite concluir que las features reales tengan o no poder predictivo.

**Conclusión:** el objetivo inmediato no debe ser “forzar AUC > 0,534”, sino medir si el modelo supera de forma estable a un baseline simple y mejora la economía neta después de costes.

## 2. Características actuales que deben mejorar

### 2.1 El meta-labeler no valida disponibilidad temporal por sí mismo

`_safe_features()` solo excluye prefijos como `future_`, `pnl_`, `exit_`, `mfe_`, `mae_` y `win_`. Si el DataFrame llega contaminado, podría utilizar columnas numéricas como:

- `market_cap` final o `market_cap_final`;
- `created_at` como proxy de régimen/orden temporal;
- `available_at_ms` o timestamps sin comprobar su relación con la entrada;
- agregados calculados después de la decisión pero sin prefijo prohibido.

**Mejora:** hacer que `fit_candidate()` llame a `FeatureAvailabilityGuard.filter_frame()`/`validate()` y que falle cerrado si falta `entry_time` o si existe una columna `market_cap` sin `asof_time`.

### 2.2 Falta de variable de selección de la señal base

El meta-labeling debe responder: “dada una señal base admitida, ¿qué señales conviene ejecutar?”. El benchmark actual entrena sobre un DataFrame genérico y no exige que las filas representen candidatos comparables.

**Mejora:** almacenar explícitamente:

- `base_signal_admitted`;
- `base_branch`;
- score/confianza de la señal;
- motivo de veto si no fue admitida;
- contexto de la cotización y latencia.

Entrenar inicialmente solo sobre el universo definido —por ejemplo, candidatos que superan el gate base— y mantener un análisis separado de selección contra todos los candidatos.

### 2.3 Modelo lineal único

La regresión logística captura efectos aditivos y lineales, pero las señales de microestructura probablemente contienen interacciones:

- liquidez × tamaño de la orden;
- buy/sell ratio × número de compradores únicos;
- progreso de curva × reservas;
- creator concentration × actividad inicial;
- impacto esperado × latencia/edad del quote.

**Mejora:** comparar, sin promoción automática:

1. baseline de la señal base;
2. regresión logística regularizada;
3. `HistGradientBoostingClassifier` con límites de complejidad;
4. Random Forest/Extra Trees solo como modelo de sensibilidad.

La selección debe hacerse por walk-forward, no por la mejor métrica de un único corte.

### 2.4 Tratamiento de missingness

Los defaults `0.0` pueden significar tanto “medición real igual a cero” como “dato no disponible”. Eso crea una falsa señal.

**Mejora:** añadir indicadores de disponibilidad para cada grupo:

- `has_holders_snapshot`;
- `has_dev_balance`;
- `has_quote`;
- `has_social_snapshot`;
- `has_reserves_snapshot`.

Los valores ausentes deben imputarse dentro del pipeline y acompañarse siempre de su indicador de missingness.

### 2.5 Etiqueta única y potencialmente ambigua

Un único `label` mezcla decisiones y horizontes. Deben separarse:

- `y_net_5m`;
- `y_net_15m`;
- `y_net_30m`;
- `y_exit_net`;
- `y_survives_costs`.

Cada etiqueta debe calcularse con fills, fees, slippage y priority fees observados o conservadoramente modelados. Las operaciones censuradas deben marcarse como `unresolved`, no como pérdidas ni éxitos.

### 2.6 Falta de calibración y métricas de utilidad

AUC no indica si las probabilidades son utilizables para sizing. Hay que medir también:

- PR-AUC, especialmente si los positivos son escasos;
- Brier score y reliability curve;
- lift del decil superior;
- precision@top-k;
- expectancy neta por decil de probabilidad;
- profit factor y drawdown del replay;
- estabilidad por rama, semana y régimen.

## 3. Instrumentación forward necesaria

Añadir o confirmar en `live_decisions`/una tabla de snapshots versionada:

| Campo | Propósito |
|---|---|
| `decision_ts` | Tiempo exacto de decisión |
| `entry_time` / `asof_time` | Verificar disponibilidad temporal |
| `feature_version` | Reproducir el vector usado |
| `base_branch` y `base_signal_admitted` | Definir el universo del meta-labeler |
| `features_json` | Snapshot inmutable de entrada |
| `quote_json` | Route, quote age, impact y profundidad |
| `execution_json` | Fill, latencia, retries y error |
| `cost_model_version` | Recalcular PnL neto |
| `data_origin` | Separar `paper_replay`, `forward_live` y `synthetic` |
| `label_status` | `pending`, `resolved`, `censored`, `invalid` |
| `y_net_5m/15m/30m` | Labels multi-horizonte |

El sistema debe guardar **también los candidatos rechazados**, sin convertirlos en trades. Esto evita sesgo de selección y permite estudiar qué habría ocurrido con los vetos.

## 4. Plan de acumulación durante las próximas semanas

### Semana 0 — Preparación y congelación

- Congelar `feature_version=p2-entry-v1`.
- Añadir guard temporal dentro de `fit_candidate()`.
- Separar `synthetic`, `paper_replay` y `forward_live`.
- Crear la tabla de labels multi-horizonte o una extensión equivalente.
- Registrar candidatos admitidos y rechazados, quotes y motivos.
- No cambiar umbrales durante la recolección inicial.

**Salida:** dataset forward reproducible y esquema versionado.

### Semana 1 — Recolección sin tuning

- Ejecutar únicamente paper/replay o forward sin capital real.
- Registrar todos los candidatos elegibles, no solo los que abren posición.
- Resolver outcomes a 5, 15 y 30 minutos, además del cierre final.
- Objetivo mínimo: `50–75` candidatos resueltos y al menos `20` positivos y `20` negativos.
- Medir cobertura de features y porcentaje de quotes inválidos.

**No hacer:** ajustar features o umbrales mirando los resultados diarios.

### Semana 2 — Primera evaluación walk-forward

- Usar una ventana expansiva: train histórico hasta el día anterior y test de los últimos 7 días.
- Aplicar purge gap igual al mayor horizonte de label más margen de latencia.
- Comparar baseline, logística y boosting de baja complejidad.
- Reportar AUC, PR-AUC, Brier, calibration, lift top-decile y PnL neto.
- Ejecutar ablación por grupos: flujo, liquidez, holders/dev, curva, ejecución.

**Objetivo:** detectar si alguna familia de features aporta señal estable, no maximizar una métrica aislada.

### Semana 3 — Confirmación y robustez

- Llegar a `150–200` candidatos resueltos acumulados.
- Mantener el último bloque semanal como test sellado.
- Evaluar por rama, régimen de liquidez, hora y origen de datos.
- Calcular bootstrap por bloques temporales o intervalos de confianza por semana.
- Revisar drift de distribución y proporción de missingness.

**Acción:** retirar features que solo funcionan en una semana o una rama.

### Semana 4 — Decisión de promoción o continuación

Objetivo recomendado: `250–400` candidatos resueltos, con al menos `50` positivos y `50` negativos para que el AUC sea interpretable, idealmente más.

Promover a una fase shadow/paper ampliada solo si se cumplen simultáneamente:

- AUC walk-forward superior al baseline y estable en al menos 2 bloques;
- mejora mínima objetivo de `+0,05` frente al baseline, no solo frente al azar;
- PR-AUC y lift top-decile también mejoran;
- Brier/calibración no empeoran materialmente;
- expectancy neta después de costes no es negativa;
- ninguna rama importante presenta deterioro severo;
- no hay alertas de leakage, drift o contaminación de procedencia.

Si no se cumplen, el modelo permanece `candidate_only` y se continúa recolectando datos. No se debe optimizar repetidamente contra el mismo test sellado.

## 5. Cadencia semanal de reevaluación

Cada semana generar un informe con:

1. filas nuevas, resueltas y censuradas;
2. porcentaje de candidatos con quote/fill válido;
3. cobertura por branch y régimen;
4. prevalencia de cada label;
5. AUC/PR-AUC/Brier/calibration por bloque temporal;
6. lift y expectancy por deciles;
7. PnL neto, profit factor y drawdown del replay/paper;
8. drift de features y missingness;
9. comparación contra baseline congelado;
10. decisión: continuar, ajustar solo en train, o descartar candidato.

## 6. Qué no debe hacerse

- No usar el AUC sintético actual para activar trading.
- No reentrenar y evaluar sobre las mismas semanas.
- No convertir `market_cap` final en feature de entrada.
- No imputar missingness como cero sin un indicador separado.
- No etiquetar una operación no resuelta como pérdida.
- No comparar bots o modelos con diferentes universos de candidatos sin normalizar la selección.
- No cambiar simultáneamente features, label, costes y umbrales; se pierde trazabilidad.

## 7. Resultado esperado realista

La mejora más probable en el corto plazo no es un gran salto de AUC, sino:

- una estimación menos sesgada;
- menor varianza entre semanas;
- mejor calibración;
- identificación de subgrupos donde la señal base sí tiene lift;
- rechazo de señales con EV neto negativo;
- trazabilidad completa para saber si una eventual mejora es de modelo o de ejecución.

Solo después de esa evidencia debe plantearse una promoción posterior a shadow/live controlado.
