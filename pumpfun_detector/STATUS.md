# STATUS.md — Verdad medida del sistema (SSOT)

> **Este es el ÚNICO lugar donde viven las cifras de rendimiento.**
> Si otro documento (README, ARCHITECTURE, AGENTS, informes) muestra un winrate/PF distinto, **está desactualizado o es una declaración sin validar**.
> Última verificación: **2026-10-15**, sobre `D:\hjh\pumpscope\db\custom.db` en modo **solo lectura**.

---

## 1. Veredicto en una línea

La estrategia publicada **no es rentable hoy**: la base canónica registra **12.55% de winrate real** y **−79,771 USD** en 5,021 operaciones cerradas. Los winrates altos (81.1%, 74.2%) que aparecen en otros documentos son **constantes escritas a mano en el código**, no mediciones.

---

## 2. Cifras reales (medidas en `custom.db`)

### 2.1 Desglose de `PaperPosition` (status = CLOSED)

| reason | n | wins | PnL (USD) | winrate |
|---|---:|---:|---:|---:|
| SL | 1,249 | 0 | **−70,030** | 0.00% |
| TIMEOUT | 3,187 | 236 | **−21,361** | 7.40% |
| TRAIL | 152 | 26 | −3,369 | 17.11% |
| STALL | 60 | 0 | −1,989 | 0.00% |
| BE | 5 | 0 | −169 | 0.00% |
| GREEN | 99 | 99 | +3,306 | 100.00% |
| TP | 269 | 269 | +13,841 | 100.00% |
| **TOTAL** | **5,021** | **630** | **−79,771** | **12.55%** |

- Profit factor real ≈ **0.269** (Σ ganancias / Σ pérdidas, en USD).
- El mayor sangrado es **SL (−70,030 USD)**; el segundo, **TIMEOUT (−21,361 USD)**.

### 2.2 ¿Los tokens suben?

| Métrica | Valor | Fuente |
|---|---|---|
| Tokens que alguna vez llegan a +50% (`rise50At`) | **11.01%** (4,632 / 42,055) | `Token` |
| Tokens que alguna vez llegan a +100% (`rise100At`) | 6.51% | `Token` |
| Mediana del multiplicador máximo (peak / first) | **1.00×** | `Token` |
| Mediana de la subida máxima **durante** el trade (drawup) | **0.00%** | `Snapshot`/`PaperPosition` |
| Trades que tocan +50% **mientras están abiertos** | **4.3%** | `PaperPosition` |

### 2.3 Resolución de los datos

| Métrica | Valor |
|---|---|
| Intervalo medio entre snapshots | **72.24 s** |
| Snapshots con intervalo ≤ 30 s | 36.29% |
| Tokens con ≥ 1 snapshot | 83.98% |

**Consecuencia:** no hay resolución tick-a-tick para decidir a 60 s. Cualquier "simulación por ticks" a 60 s es una **interpolación**, no una medición.

---

## 3. Cifras declaradas que NO son mediciones (no confiar)

| Cifra | Dónde aparece | Realidad |
|---|---|---|
| "Rama A: WR 81.1% / PF 18.93" | `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `detector/master_signal.py:389` | Literal hardcodeado. Corrida honesta de Rama A: **7.81% WR / PF 0.256** (`scratch/out_signal.txt`) |
| "Master Signal completo: WR 74.2% / PF 14.45" | `ARCHITECTURE.md:176-184` | Calculado **in-sample**, sin train/test; PF además en %-points, no USD |
| "Rama B: WR 59.6% / PF 2.69" | `README.md`, `master_signal.py:393` | Hardcodeado **y** rama desactivada (`DISABLED_BRANCHES`) |
| "Slippage medio −56.45%" | `ARCHITECTURE.md`, `loss_learner.py:267` | Es un **string literal**, nunca calculado |
| "96.8% teórico" | (informes externos) | Falso: solo 11.01% de tokens llegan a +50% |
| "75.4% real" | (informes externos) | Falso: el winrate real es 12.55% |

Los números de arquetipos (`expected_wr 0.811/0.596`) en `detector/pattern_identifier.py` son **priors declarados**, no estimaciones.

---

## 4. Estado por componente

| Componente | Estado | Nota |
|---|---|---|
| `master_signal.py` | Funcional pero con métricas declaradas | Umbrales `4285/1725/4500` con pinta de overfit |
| `features.py` | **Con fuga** | Features `*_60s` miden 60 s **desde creación**, no desde entrada |
| `labels.py` | Sesgo optimista | Tie-break `i_tp <= i_sl` favorece TP; TP/SL 50/30 ≠ política 150/15 |
| `costs.py` | Inconsistente | Fees aplicadas de 3 formas; slippage hardcodeado |
| `run_live.py` | **No apto 24/7** | Se cae en la 1ª desconexión; `price=0` liquida a −98% |
| `pumpportal_feed.py` | Sin reconnect | Una sola conexión; sin backoff |
| `live_store.py` | Sin ticks crudos | Solo decisión + MFE/MAE + salida; no la ruta de precios |
| `momentum_exit.py` | **Correcto** | Sin lookahead; usa solo trayectoria observada |
| `database_audit.py` / `columnar_export.py` | **Correcto** | `mode=ro` + `query_only=ON` |
| `docs/superpowers/specs/*` | **Honestos** | Exigen provenance, anti-leakage, no declarar OOS sin test |

---

## 5. Cómo re-verificar (read-only)

```powershell
sqlite3 "D:\hjh\pumpscope\db\custom.db" "SELECT reason, COUNT(*), SUM(CASE WHEN pnlUsd>0 THEN 1 ELSE 0 END), ROUND(SUM(pnlUsd),2) FROM PaperPosition WHERE status='CLOSED' GROUP BY reason;"
sqlite3 "D:\hjh\pumpscope\db\custom.db" "SELECT SUM(rise50At IS NOT NULL)*100.0/COUNT(*) FROM Token;"
python -m unittest discover -s tests -v
```

---

## 6. Regla de oro

**Ninguna métrica se considera válida hasta pasar por walk-forward con `oos_pass = True` y el modelo de costes unificado.** Hasta entonces, todo número de rentabilidad se etiqueta como `claimed` / `in-sample` / `synthetic`.

---

## 7. Verificación de terceros (2026-10-15) — el "96.8%" / "PF 242"

Un informe externo (`AUDITORIA_FORENSE_WINRATE_CUSTOM_DB.md`, del agente Antigravity) reportó:

| Configuración | N | Winrate | EV/trade | PF |
|---|---:|---:|---:|---:|
| Conformal 85% (α=0.15) | 31 | **96.8%** | +43.96% | **242.61** |
| Conformal 95% (α=0.05) | 36 | 91.7% | +39.36% | 84.74 |
| Rama A "real" | 69 | **75.40%** | +40.54% | 7.99 |

**Veredicto: FALSO.** Reproducido y refutado en solo lectura sobre `custom.db` (scripts en `scratch/verify_*.py`).

### 7.1 Los tres errores exactos

1. **Etiqueta = desenlace futuro.** La "ganancia" se define con `Token.rise50At` / `rise100At` ("¿subió +50% *en algún momento de su vida*?"). No es una estrategia; es conocer el futuro.
2. **Filtro con valor futuro.** `MasterSnipeSignal` se alimenta con `marketCap` **FINAL** (`>= 4500`) y demás columnas de ciclo de vida. Es sesgo de selección: solo pasan tokens que sobrevivieron.
3. **La columna "real" está contaminada.** En `audit_oos_42.py`: `actual_gross = paper[..] if has_paper else ideal_gross` → a los tokens **sin** registro en `PaperPosition` se les asigna el **PnL idealizado**. En el filtro auditado, **485 de 506 tokens (96%) no tienen PaperPosition**, así que la columna "real" es mayormente ideal.

### 7.2 El experimento decisivo

| Filtro | tokens | llegan a +50% | con PaperPosition | WR real |
|---|---:|---:|---:|---:|
| **[A]** CON condición futura (`MC final ≥ 4500`) | 506 | **83.60%** | 21 | 85.71% |
| **[B]** SIN condición futura (solo `firstMC≤4285`, `firstLiq≤1725`) | 18,884 | **15.03%** | 510 | **7.65%** |
| **[C]** Universo total | 65,423 | 10.41% | 1,284 | 12.55% (por posición) |

**Al quitar la única condición futura, el "edge" colapsa de 83.60% a 15.03% y el winrate real cae de 85.71% a 7.65%.** Era sesgo de selección, no señal.

### 7.3 Blindaje implementado

- `detector/label_guard.py`: `assert_causal_label()` / `assert_no_future_features()`.
  Rechaza etiquetas derivadas de columnas de ciclo de vida (`rise*`, `peak*`, `ath*`,
  `dead*`, `graduated*`, `status`) y features con desenlaces futuros.
- `tests/test_label_guard.py`: 9 tests (incluye el caso real `hit_50 ← rise50At`).
- **Regla operativa:** todo pipeline de análisis debe llamar `assert_causal_label()`
  antes de calcular métricas. Es la línea que habría detenido el "96.8%".

### 7.4 Lo único medible hoy

El `ConformalMetaLabeler` está bien construido (split conformal, 3 bloques, anti-fuga
de features), pero **se le alimentó una etiqueta con lookahead**, lo que invalida su OOS.
Con etiqueta causal, el sistema **no tiene edge demostrado**.

---

## 8. Mejoras de integridad aplicadas (Ola 2)

| ID | Qué | Estado |
|---|---|---|
| **C1** | Features `*_60s` (`n_buys_60s`, `unique_buyers_60s`, `top5_wallet_share_60s`…) medidas desde la **creación**, no desde la entrada → ~50 s de lookahead con entrada a ~10 s | **CORREGIDO**: movidas a `POST_ENTRY_FEATURES` y quitadas de `ENTRY_SAFE_FEATURES`. La señal usa ahora los fallbacks causales `ef_n_buys` / `ef_n_sells` / `ef_unique_buyers` (cortados en `entry_time` con `searchsorted`) |
| **C4** | Profit Factor calculado en **puntos porcentuales** (un ganador de +150% lo inflaba) | **CORREGIDO**: PF en USD (`gross_profit_sol / gross_loss_sol`) en `main.py` |
| **C8** | `STALL_TIMEOUT_SECONDS` existía pero no lo usaba nadie; el literal `60.0` estaba duplicado | **CORREGIDO**: `main.py` lee `MasterSnipeSignal.STALL_TIMEOUT_SECONDS` |
| **L1** | `price=0` liquidaba posiciones a −98% | **CORREGIDO** (Ola 1) |
| **L2** | El daemon moría en la 1ª desconexión | **CORREGIDO** (Ola 1): reconexión con backoff |
| **Guard** | Nada impedía etiquetas con lookahead | **AÑADIDO**: `detector/label_guard.py` + auditoría en `main.py` |

### 8.1 Pendiente (Ola 3)

- Walk-forward OOS real: minar en Train, calibrar en Val, **sellar** en Test; `oos_pass`
  obligatorio para exportar (`export_gate.py`).
- Cablear `ScalingGate` (hoy existe pero no bloquea).
- Re-medir Rama A/B/C con etiqueta causal + coste unificado → decisión con EV neto real.
- C5 (tie-break TP/SL optimista) y C6/C7 (modelo de coste unificado) siguen abiertos.

---

## 9. Cockpit de observabilidad (`cockpit/`)

Ventana de escritorio (o navegador) que muestra el proceso **en vivo** y, sobre todo,
**la trampa de lookahead**: el mismo filtro CON y SIN la condición futura.

- **Solo lectura**: abre la base con `mode=ro` + `query_only`. No decide ni escribe.
- **3D autocontenido**: proyección propia sobre `Canvas 2D` (sin Three.js/CDN/red).
  Cada token = `(firstMC, firstLiq, MC_final)`; el plano ámbar marca el umbral **futuro**.
- **Ventana nativa** vía `pywebview` (WebView2); si no está, abre el navegador.

```powershell
python -m cockpit.launch --rebuild   # construye el snapshot (escaneo read-only)
python -m cockpit.launch             # arranca y abre la ventana
```

---

## 10. Barrido causal de umbrales (Ola 3, 2026-10-15)

`scripts/causal_sweep.py` — barrido de umbrales **solo con features causales**
(`ef_*`, `firstMarketCap`, `firstLiquidity`…), con partición temporal y gate OOS.

```
N=5021 | Train=3012 | Val=1004 | Test OOS=1005
baseline Train WR=9.46%  |  Test OOS WR=19.4%
candidatos=88 | promovidos=0
```

**Resultado: 0 reglas promovidas.** El gate exige (a) `oos_n >= 30`,
(b) IC95% inferior de winrate > baseline OOS y (c) **EV neto OOS > 0**.

Las 4 reglas que pasaban solo (a)+(b) eran:

| Regla | OOS n | OOS WR | OOS EV | PF |
|---|---:|---:|---:|---:|
| `firstMarketCap >= 7693 AND firstLiquidity >= 3640` | 38 | 36.84% | **−16.77%** | 0.405 |
| `firstMarketCap >= 4846 AND firstLiquidity >= 3640` | 43 | 32.56% | **−25.29%** | 0.285 |

**Lección medida:** más winrate ≠ ganar dinero. Los perdedores son mayores que
los ganadores → EV negativo. El gate las rechaza (por eso `promovidos=0`).

### 10.1 Hallazgo direccional (lo único aprovechable)

Las reglas con mejor winrate apuntan a **firstMarketCap y firstLiquidity ALTOS**,
es decir, **lo contrario** de la tesis original de Rama A (que apostaba por
micro-caps de baja liquidez). Con datos causales, la micro-cap baja es donde
está la pérdida, no el edge.

### 10.2 Conclusión

**No hay edge desplegable hoy.** El baseline OOS (19.4%) es inestable frente al
Train (9.46%), la muestra OOS útil es de decenas de operaciones, y ninguna regla
sobrevive al coste. Antes de hablar de dinero real hacen falta **más datos y de
mejor resolución**.

---

## 11. Mejora de resolución de datos (PumpScope, 2026-10-15)

Problema medido: intervalo medio entre snapshots **72.24 s**, solo 36% ≤30 s.
Con esa resolución no se puede decidir a 10-60 s: cualquier "simulación por
ticks" en esa ventana es interpolación.

Cambio en `D:\hjh\pumpscope\src\lib\sampler.ts`:
- **Ventana temprana**: nuevo timer que muestrea **solo** tokens con antigüedad
  < 180 s cada **12 s** (`EARLY_WINDOW_MS` / `EARLY_INTERVAL_MS` / `EARLY_CAP`),
  reutilizando `sampleMarket(..., { onlyYoungMs, cap })` sin duplicar lógica.
- Se salta si el ciclo principal (45 s) está corriendo → sin solapamiento.
- `SAMPLER_VERSION` 6 → 7 (fuerza el reinicio del timer en el próximo `/api/tick`).
- `getSamplerStatus()` expone `earlyRunning` / `earlySnapshots`.

Resultado esperado: **~12 s de resolución en los primeros 3 minutos** de cada
token (vs ~72 s), que es exactamente la ventana donde decide la estrategia.
Verificado con `tsc --noEmit`: sin errores nuevos.

---

## 12. Skills de GMGN instaladas (2026-10-15)

`npx skills add GMGNAI/gmgn-skills` → **17 skills** en
`D:\mejoradordebase de datos\.agents\skills\gmgn-*` (+ `gmgn-cli` v1.6.6 global).

⚠️ **Ojo:** tres son de **ejecución financiera**:
- `gmgn-swap` — *"es la única skill que guarda una clave privada"* y **envía órdenes reales**.
- `gmgn-token-buy` — compra con sizing/slippage.
- `gmgn-cooking` — **lanza tokens** en launchpads.

Las útiles para este proyecto (análisis, sin ejecución): `gmgn-contract-dd`,
`gmgn-holder-analysis`, `gmgn-kline-pattern`, `gmgn-market`, `gmgn-dev-score`,
`gmgn-portfolio`, `gmgn-heat-rank`, `gmgn-narrative`, `gmgn-wallet-*`, `gmgn-track`.

**Requiere API key propia** (`gmgn-cli config --apply <KEY>`). No configurada.
Si solo quieres análisis, considera borrar las tres de ejecución:
`npx skills remove gmgn-swap gmgn-token-buy gmgn-cooking`.

---

## 13. C5 / C6 / C7 corregidos (2026-10-15)

| ID | Qué estaba mal | Corrección |
|---|---|---|
| **C5** | `i_tp <= i_sl` resolvía el empate TP/SL a favor del **TP**; el SL se llenaba al nivel idealizado aunque el precio hubiera saltado | Empate → **SL**. El SL (orden a mercado) se llena al **precio observado** (`min(nivel, px)`), no al nivel. Aplicado en `labels._tpsl_outcome`, `labels.tpsl_grid` y `advanced_engine.realistic_tpsl_outcome` |
| **C6** | Fees en **3 valores distintos**: 0.008 SOL (`costs`), 0.004 SOL (`advanced_engine`, solo priority ×2) y un 5.64% plano. Además el simulador offline **no cobraba** el fee de plataforma | `costs.ROUNDTRIP_TX_FEE_SOL` = 0.008 como **fuente única**; `advanced_engine.REAL_TX_FEE_SOL` la usa. `platform_fee_roundtrip_frac` pasa a ser campo del modelo y el simulador offline la cobra |
| **C7** | `-56.45%` era un **string literal** presentado como medición; `-56%` en una descripción | El clúster ahora **calcula** `avg_realized_loss_pct` de los datos; los literales eliminados |

Tests: `tests/test_costs_integrity.py` (9) — incluye el fill del SL por gap y la
igualdad de constantes entre módulos.

**Impacto honesto:** el simulador offline ahora cobra más coste que antes
(red completa + plataforma), así que las métricas del backtest **empeoran**, no
mejoran. Es el objetivo: alinear el backtest con el live en vez de maquillarlo.

---

## 14. Ciclo automático (2026-10-15)

`cockpit/cycle.py` — un único comando que deja el estado honesto listo:

```
recolectar (solo lectura) -> snapshot del universo + sesgo -> barrido causal OOS -> cycle.json
```

```powershell
python -m cockpit.cycle                                  # una pasada (~2 s con caché)
python -m cockpit.cycle --loop --interval-hours 24       # desatendido
```

Salida medida:
```
[cycle] universo: 65423 tokens | +50%=10.41% | WR real=12.55%
[cycle] barrido: 88 candidatos | promovidos=0 | SIN EDGE DEMOSTRADO
```

- Escribe `cockpit/runtime/cycle.json` (y `snapshot.json`).
- El servidor del cockpit lo expone en `/api/state` → `cycle` (cambio **aditivo**;
  el resto del cockpit lo está extendiendo otro agente en paralelo).
- **Fail-closed**: si la muestra es insuficiente o falta `created_at`, devuelve
  `ok=false` con el motivo — nunca un falso positivo.
- Tests: `tests/test_cycle.py`.

**Uso previsto:** dejarlo en bucle; cuando el sampler rápido de PumpScope
(§11) acumule series con resolución de ~12 s, el barrido tendrá datos que sí
permiten medir la ventana de 10-60 s. Hasta entonces, seguirá diciendo
"SIN EDGE DEMOSTRADO" — que es la respuesta correcta.




