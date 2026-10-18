---
id: plan-maestro-cockpit-v2
status: active
fecha: 2026-10-15
db_hash: 5e2b8f1cd866
git_sha: 53fab57
data_origin: real
cost_model_version: costs.py (0.008 SOL roundtrip, platform 5.64%)
cobertura_pct: 36.7
resolucion_datos_s: 72.24
refuta_a: []
refutado_por: []
oos_pass: n/a
edge_demostrado: false
---

# Plan Maestro — Cockpit V2 "Caja de Cristal" (Desktop + Web, Pipeline vivo, Aprendizaje cableado, Anti-falacias, Enterprise)

> **Encargo:** el escritorio actual (4 tarjetas + log vacío) no sirve. Se pide:
> (1) diagramar **la decisión de pensamiento filtrado en vivo**,
> (2) un sitio donde el **proceso de aprendizaje filtrado funcione y esté cableado**,
> (3) todo **automático**, (4) **ver falacias/mentiras** del propio sistema,
> (5) hacer lo básico **más real**, con meta de **dinero real o nivel enterprise**.
>
> **Regla de oro que gobierna este plan:** nada se muestra como dato si no es medible.
> Si falta el dato → `N/D — sin dato` (fail-closed), jamás un default bonito.
> Referencias: `AGENTS.md` (BP-001…BP-012), `pumpfun_detector/STATUS.md` (SSOT), `STATE.json`.

---

## 1. Veredicto ejecutivo

El **motor** ya es rico: servidor HTTP local con bucle autónomo, screening GMGN/DexScreener,
dossier forense (bundler/wash/clones), modelo ML `HistGradientBoosting`, paper-forward con
TP1/BE/TP2/SL real, shadow-veto ledger, motor de auto-evolución y una web UI de ~99 KB con
5 modos (radar, IA, smartmoney, bot, 3D).

Lo que **no sirve** es la capa que el usuario ve en la captura: `PumpfunCockpit.exe` (WinForms)
está **desalineado del API** del servidor — sus 4 tarjetas nunca se actualizan con datos reales
y el log no tiene fuente. Y el "aprendizaje" que se muestra **no está cableado al decisor**:
las reglas adaptativas que el motor ajusta no las lee ningún filtro de entrada (solo un mensaje
de chat las pinta). Es decir: el sistema *finge* aprender sin que ese aprendizaje toque la decisión.

**Conclusión:** el gap no es "rehacer todo", es (a) cerrar el contrato Desktop↔Server,
(b) trazar y dibujar el pipeline de decisión en vivo, (c) cablear el loop de aprendizaje de
punta a punta, (d) volver la procedencia una ley de UI, (e) endurecer para enterprise.
Nada de esto autoriza promesas de rentabilidad: el edge sigue **no demostrado**
(`edge.demostrado=false`, `oos_pass=false`, live bloqueado) y así debe permanecer hasta
cumplir el gate: **N≥100 forward OOS, EV neto > 0, PF > 1.25** [MEASURED — gate pendiente].

---

## 2. Estado verificado (evidencia, no opinión)

### 2.1 Dos puertas de entrada al mismo servidor (hoy inconsistentes)

| Entrada | Comando | Puerto | Qué muestra |
|---|---|---|---|
| `.bat` + `launch.py` | `python -m cockpit.launch` | 8787 | Ventana Chromium app-mode con la **web UI completa** (index.html) |
| `PumpfunCockpit.exe` | spawn de `python -m pumpfun_detector.cockpit.server` | 8000 | **4 tarjetas rotas + log vacío** (la captura del usuario) |

Ambas levantan `CockpitState` con `auto_loop=True` (`server.py:686`, `launch.py:118`), es decir,
el backend es el mismo; solo el **front** del exe está desconectado.

### 2.2 Desalineación de contrato Desktop ↔ Server (causa raíz de "no sirve")

| # | El exe lee… | El server entrega… | Efecto |
|---|---|---|---|
| F1a | `balance_usd` / `balance_sol` (`PumpfunCockpitLauncher.cs:339-343`) | `total_pnl_usd` / `total_pnl_sol` (`gmgn_bridge.py:331-332`) | Tarjeta "SALDO PAPER" queda con default **10,000.00 USD (0 ops)** — un número **inventado** mostrado como si fuera real |
| F1b | Cuenta ocurrencias de `"mint"` en todo el JSON (`Launcher.cs:346`) | `open_positions` + `recent_closed` (ambos con `mint`) | Conteo de posiciones inflado (cuenta cerradas también) |
| F1c | `true_saves`, `false_vetos`, `net_saved_usd`, `total_evolution_events` (`Launcher.cs:350-355`) | `shadow_summary.true_saves`, `shadow_summary.false_vetos`, `shadow_summary.avoided_loss_sum_pct`; no existe `total_evolution_events` (`auto_evolution.py:321-329`) | Tarjetas "SHADOW VETO SAVES" y "AUTO-APRENDIZAJE" muestran vacío o `+$` truncado |
| F1d | Envía `{"prompt": ...}` (`Launcher.cs:467`) | Lee `params.get("message")` (`server.py:578`) | El chat IA del escritorio **nunca responde** |
| F1e | Log multilínea del exe | No existe `/api/logs` ni stream; el loop autónomo solo imprime a consola (`server.py:108-109`) | El panel grande queda vacío para siempre |

### 2.3 El loop de aprendizaje no está cableado (hallazgo central para "cableado")

`AutoEvolutionEngine.diagnose_and_learn_from_closed_trade()` ajusta y persiste reglas
(`adaptive_rules.json`): `max_bundler_pct`, `min_avg_tx_m5_usd`, `max_top10_float_pct`,
`min_vol_to_mc_h1_pct`, `min_liquidity_usd` (`auto_evolution.py:189-232`).

**Nadie en la ruta de decisión las lee.** El único consumidor es `ai_engine.py:937`
(un mensaje de chat que las *muestra*, con defaults hardcodeados 8500/28.0/18.0/5.5).
El decisor real usa sus propios umbrales fijos: `score >= 85.0` para auto-snipe
(`gmgn_bridge.py:1021`), `bundler_flag = estimated_bundler_pct >= 20.0`
(`gmgn_dossier_scanner.py:182-183`), `confidence_pct=85.0` hardcodeado (`gmgn_bridge.py:493`).

→ **Consecuencia:** el sistema exhibe "auto-aprendizaje" y "reglas adaptativas" que **no
gobiernan ninguna compra/veto**. Es una falacia de presentación. Cerrado ese cable, el loop
pasa a ser real: trade cierra → post-mortem → ajusta regla → **la regla cambia el próximo filtro**.

### 2.4 Otros huecos verificados

| # | Hallazgo | Evidencia |
|---|---|---|
| F2 | **Sin traza de decisión por etapa** (no se puede re-ver *por qué* se vetó/admitió paso a paso). Solo hay campos agregados `veto_reason`, `confluence_score` (`gmgn_bridge.py:66-96`) | Persistencia en `live_store.py` guarda decisión + outcome, no la secuencia de filtros |
| F3 | **Sin push**: todo es polling (web 2.5 s, exe 3 s). No hay SSE/WebSocket | `index.html:1570-1578`; `Launcher.cs:45` |
| F4 | **Procedencia parcial**: no existe un bloque systemático `provenance {source, ts, age, stale}` en `/api/bot/status`; `/boost` pinta `test_roc_auc` con **default 0.91** si falta el dato (`ai_engine.py:776`) | Riesgo de repetir el patrón "96.8% WR" [CLAIMED] — refutado en STATUS.md §7 |
| F5 | **No existe banca paper** como concepto: sin capital inicial, equity, exposición o ROI acumulado | `LivePaperTrader.__init__` (`gmgn_bridge.py:144-159`) solo tiene posiciones |
| F6 | **Doble puerto** (8000/8787) y **exe sin script de build** versionado (no hay `.csproj`; `csc` no está en PATH) | `server.py:679` (8000) vs `launch.py:103` (8787) |
| F7 | **Progreso de certificación invisible**: el gate `N>=100, EV>0, PF>1.25` existe (`STATE.json.edge`) pero ninguna vista muestra cuánto falta | `STATE.json` |
| F8 | `ScalingGate` existe pero **no bloquea** (pendiente ya anotado en STATUS.md §8.1) | STATUS.md |

---

## 3. Arquitectura objetivo — "Caja de Cristal"

### 3.1 Pipeline de decisión (una fila de tiempo por token, en vivo)

```
  FUENTES ──────► E0 Descubrimiento ─► E1 Filtros base ─► E2 Dossier forense ─► E3 Seguridad
  DexScreener      (edad, fuente,       (MC, Liq, ratio,    (bundler%, wash,      (honeypot, rug,
  PumpPortal        freshness)          reglas activas)     clones, dev hold)      GoPlus/RugCheck)
  GMGN                                                             │
                                                                   ▼
  APRENDIZAJE ◄── E8 Cierre + post-mortem ◄─ E7 Salida ──◄ E6 Ejecución paper ──◄ E5 VETO / ADMIT
  (reglas v11)     (autopsia, evento evo)   (TP1/BE/TP2/SL    (CPU: apertura,      (veto_reason
   │                                        traza, PnL)        sizing, origen)      explícito)
   └───────────────── realimenta umbrales de E1/E2/E3/E5 (¡hoy no ocurre!) ◄─────────
```

Cada etapa emite un **evento de etapa** con: `stage`, `value`, `threshold`, `operator`,
`pass|fail|skip`, `reason`, `ts`, `source`. La suma ordenada de eventos = **traza de decisión**
reproducible (auditoría y replay).

### 3.2 Vista 1 — "Pipeline en vivo" (la petición principal)

- Diagrama DAG horizontal (SVG/Canvas propio, **sin CDNs**) con las etapas E0→E8.
- Un token seleccionado (o el stream) anima su avance; rojo en la etapa que vetó, verde al admitir.
- Panel lateral: ficha de la traza — cada filtro con `valor vs umbral`, quién lo definió
  (default, regla adaptativa v11, usuario) y desde cuándo rige.
- Historial: últimas 100 trazas; clic = replay paso a paso de esa decisión histórica.
- Contador de embudo en vivo: `descubiertos → pasan E1 → pasan E2 → … → admitidos → ejecutados`.

### 3.3 Vista 2 — "Lente de aprendizaje" (el sitio donde el loop está cableado)

Ciclo visible con estado de última ejecución por nodo:

```
[Trade cierra] → [Autopsia (LossPostMortemLearner/diagnose)] → [Evento evo: regla X 18%→15%]
      → [Regla activa en decisor (wire)] → [Efecto medible: vetos con la regla nueva vs vieja]
      → [Reentreno /boost] → [Modelo: ROC-AUC OOS real, n train/test] → [Shadow ledger: TRUE_SAVE/FALSE_VETO]
```

- Tabla de reglas adaptativas: valor actual, valor anterior, fecha, quién la cambió, **y su
  efecto medido** (n decisiones afectadas, WR/EV de las vetadas por esa regla).
- Progreso del **gate de certificación**: `N_forward / 100`, `EV neto`, `PF`, `oos_pass`
  (semáforo; hoy `false`).
- Botón/estado de `/boost` con métricas **solo si son reales** (`N/D` si no se midieron).

### 3.4 Vista 3 — "Integridad / Anti-falacias" (ver mentiras del propio sistema)

- Tablero de procedencia por panel: `[MEASURED]` / `[CLAIMED]` / `[SYNTHETIC]` / `[IN-SAMPLE]` / `STALE` / `N/D`.
- Trampa de lookahead integrada (ya existe como concepto en el cockpit read-only): mismo filtro
  CON vs SIN condición futura → colapso visible (83.60% → 15.03% [MEASURED — STATUS.md §7.2]).
- Registro visible de cifras refutadas (`metrics/claimed.json` + `STATE.json.claimed_metrics_blacklist`).
- Checks automáticos `/api/integrity` (semáforo por check):
  1. `label_guard` sobre el último dataset usado (causalidad de etiquetas/features);
  2. constantes de costo idénticas entre módulos (`costs.py` fuente única);
  3. feed vivo o `STALE` (DexScreener con edad del último tick por token);
  4. coherencia aritmética: Σ PnL por posiciones == `net_audit` (tolerancia 0);
  5. contrato Desktop↔Server vigente (golden schema test);
  6. muestra mínima por regla mostrada (N≥30; si no, la UI la marca `INSUFICIENTE`, BP-003).
- **Ley de UI:** ninguna tarjeta puede renderizar un literal de default como si fuera dato.
  Sin dato → `N/D — sin dato` con causa y timestamp.

---

## 4. Plan por fases

### P0 — Integridad inmediata (≈1-2 días) — "hacer lo básico real"

| # | Tarea | Archivos | Criterio de aceptación |
|---|---|---|---|
| P0.1 | Bloque canónico `dashboard` en `/api/bot/status`: `{paper_wallet {initial_usd, equity_usd, pnl_usd, pnl_sol, open_count, exposure_pct}, evolution {true_saves,false_vetos, avoided_loss_pct}, runtime {feed_source, feed_age_s, stale, loop_alive}}` | `cockpit/server.py` (+ `gmgn_bridge.get_summary`) | JSON estable documentado; test de contrato lo valida |
| P0.2 | Corregir el exe: usar SOLO claves del bloque `dashboard`; conteo por `open_count`; chat envía `message`; si falta clave → "N/D — sin dato" | `scripts/PumpfunCockpitLauncher.cs` | Con server caído las tarjetas dicen `N/D`, nunca 10,000 USD |
| P0.3 | `/api/logs` (ring buffer 500 eventos + JSONL `runtime/events.jsonl`) y `GET /api/events` (SSE `text/event-stream`): eventos `stage`, `decision`, `trade_open`, `trade_close`, `evo`, `feed`, `error` | `cockpit/server.py`, `cockpit/events.py` (nuevo) | Exe y web ven el MISMO stream; el log deja de estar vacío |
| P0.4 | Script de build versionado del exe (`scripts/build_cockpit_exe.ps1` con `dotnet`/Roslyn) + `PumpfunCockpit.exe` regenerado | `scripts/build_cockpit_exe.ps1` (nuevo) | `pwsh scripts/build_cockpit_exe.ps1` produce exe reproducible |
| P0.5 | **Test de contrato** golden: `tests/test_desktop_contract.py` — valida que las claves que consume el C# existen en el payload real (regresión de F1a–F1e) | `pumpfun_detector/tests/` | Romper el contrato = CI rojo |

### P1 — Pipeline en vivo (≈3-5 días) — "la decisión de pensamiento filtrado"

| # | Tarea | Detalle | Criterio |
|---|---|---|---|
| P1.1 | Persistir **trazas de decisión**: tabla `decision_traces(mint, ts, stage, value, threshold, operator, verdict, reason, source)` en LiveStore | Emitida por `screen_tokens_to_buy` y el dossier scanner en cada evaluación | Replay de cualquier decisión de las últimas 24 h |
| P1.2 | Endpoint `/api/pipeline/live` + `/api/pipeline/trace/{mint}` y embudo `{seen, pass_e1..e5, admitted, executed}` | `cockpit/server.py` | Latencia < 200 ms con 200 tokens |
| P1.3 | Vista "Pipeline" en la web UI: DAG E0→E8 animado + ficha por token + embudo en vivo | `static/index.html` (JS propio, sin CDN) | En pantalla se ve el motivo exacto de cada veto en < 1 s desde la evaluación |
| P1.4 | Replay histórico de trazas (slider temporal) | idem | Seleccionar un trade cerrado y ver su traza completa |

### P2 — Aprendizaje cableado (≈4-6 días) — "que el filtrado aprenda de verdad"

| # | Tarea | Detalle | Criterio |
|---|---|---|---|
| P2.1 | **Cablear reglas adaptativas al decisor**: `GMGNBridge` y `GMGNDossierScanner` leen `adaptive_rules.json` (con default declarado + provenance "regla" vs "default") | `gmgn_bridge.py`, `gmgn_dossier_scanner.py`, `auto_evolution.py` | Cambiar `max_bundler_pct` en el JSON cambia el conjunto de admitidos (test) |
| P2.2 | Aplicar el gate BP-003: reglas con muestra < 30 no pueden cambiar umbrales (solo proponer) | `auto_evolution.py` | Evento evo marcado `applied=false, reason=BELOW_MIN_N` |
| P2.3 | `/boost` automático por política: cada 25 trades cerrados (configurable) + evento + diff de métricas antes/después | `auto_evolution.py`, `ai_engine.py`, `server.py` | Sin intervención manual; evento visible en Vista 2 |
| P2.4 | Vista "Lente de aprendizaje" completa (reglas con efecto medido, shadow ledger con contrafactual, curva del modelo por reentreno, progreso del gate) | `static/index.html` | Cada nodo del loop muestra última ejecución y próxima |
| P2.5 | Efecto de cada regla medido: para cada regla activa, `n_vetados`, `WR/EV contrafactual` de lo vetado | `live_store.py` + vista | Regla sin efecto medido = badge `SIN EFECTO` |

### P3 — Anti-falacias (≈2-3 días) — "ver mentiras"

| # | Tarea | Detalle | Criterio |
|---|---|---|---|
| P3.1 | `/api/integrity` con los 6 checks del §3.4 | `cockpit/server.py`, `detector/label_guard.py` | Semáforo por check, fail-closed |
| P3.2 | Vista "Integridad" + trampa de lookahead interactiva | `static/index.html` | Cifras refutadas visibles con su etiqueta `[CLAIMED — REFUTADO]` |
| P3.3 | Quitar defaults numéricos de presentación (`/boost` 0.91, confidence 85.0 fija): `N/D` cuando no hay medición | `ai_engine.py:776`, `gmgn_bridge.py:493` | grep de defaults numéricos en respuestas UI = 0 |
| P3.4 | Badge `STALE`/`OFFLINE` global y por panel (feed, DB, modelo, store) | server + UI | Matar DexScreener simulado = banner rojo en < 5 s |
| P3.5 | Contadores desde fuente primaria o con timestamp (nunca doble conteo) | UI + server | Test: Σ open+closed == reportado |

### P4 — Enterprise (≈1-2 semanas)

| # | Tarea | Detalle |
|---|---|---|
| P4.1 | Auth local por token (header `X-Auth`) + CORS cerrado; bind 127.0.0.1 (ya) documentado | `server.py` |
| P4.2 | Audit log append-only con hash-chain (decisiones, acciones UI, cambios de config/reglas) | `live_store.py` |
| P4.3 | Logs estructurados JSONL + rotación + `X-Request-Id` | server |
| P4.4 | Watchdog del loop autónomo (dead-man switch: si una etapa no corre en X s → evento `error` + banner) | server |
| P4.5 | Instalación como servicio Windows (Task Scheduler/servicio) + auto-arranque + actualización | `scripts/install_service.ps1` |
| P4.6 | CI: `verify.ps1` + contrato + build exe + `check_claims` en modo estricto para `outputs/` | CI |
| P4.7 | Empaquetado versionado (semver en `/api/version`, changelog) | repo |

### P5 — Camino honesto a dinero real (cuando el gate pase; no antes)

1. **Certificar el gate**: `N_forward ≥ 100`, `EV neto > 0`, `PF > 1.25` con walk-forward
   sellado (`export_gate.py`, `walkforward_seal.py`) y etiqueta causal (`label_guard`).
   Hoy: `oos_pass=false`, `edge_demostrado=false` [MEASURED] — el live sigue bloqueado.
2. Cablear `ScalingGate` (hoy no bloquea) y el registro de decisiones selladas.
3. Plan de microlive SOLO post-gate: límites duros por trade/día, kill-switch, latencia de
   ejecución (Jito/priority fee), reconciliación on-chain, presupuesto de riesgo explícito.
4. Mantener el laboratorio separado: la mejora de motores nunca toca la cuenta viva sin gate.

---

## 5. Contratos de API nuevos (borradores)

```jsonc
// GET /api/bot/status  (bloque canónico nuevo)
{ "ok": true,
  "dashboard": {
    "paper_wallet": { "initial_usd": 10000.0, "equity_usd": 10512.30, "pnl_usd": 512.30,
                      "pnl_sol": 3.415, "open_count": 2, "exposure_pct": 4.1 },
    "evolution": { "true_saves": 7, "false_vetos": 2, "avoided_loss_pct": 63.1,
                   "evolutions_count": 4, "last_rule_change": {"rule":"max_bundler_pct","old":18.0,"new":15.0,"ts":1750000000} },
    "runtime": { "feed_source": "dexscreener", "feed_age_s": 4.2, "stale": false,
                 "loop_alive": true, "last_stage": {"screen": 1750000001, "tick": 1750000002, "shadow": 1750000000} }
  },
  "provenance": { "source": "live", "ts": 1750000002, "age_s": 0.4, "stale": false } }

// GET /api/pipeline/trace/{mint}
{ "ok": true, "mint": "...", "verdict": "VETO", "veto_reason": "BUNDLER_UNIFORM_TRAP",
  "stages": [
    { "stage": "E0_discovery", "value": 42.0, "threshold": null, "verdict": "pass", "source": "dexscreener", "ts": 1750000010 },
    { "stage": "E1_base_filters", "value": 3100.0, "threshold": 4285.0, "operator": "<=", "verdict": "pass", "source": "rule_default" },
    { "stage": "E2_dossier", "value": 22.4, "threshold": 18.0, "operator": "<=", "verdict": "fail",
      "reason": "bundler 22.4% > 18.0% (regla adaptativa v11)", "source": "adaptive_rule:v11" } ] }

// GET /api/events  (SSE)
event: stage      data: {"mint":"...","stage":"E2_dossier","verdict":"fail","reason":"..."}
event: trade_close data: {"mint":"...","pnl_pct":-33.5,"exit_reason":"PANIC_SL_SLIPPAGE_EXIT"}
event: evo        data: {"rule":"max_bundler_pct","old":18.0,"new":15.0,"applied":true,"evidence_n":31}
event: feed       data: {"source":"dexscreener","age_s":4.2,"stale":false}

// GET /api/integrity
{ "ok": true, "checks": [
    { "id": "label_guard", "pass": true,  "detail": "0 etiquetas con lookahead" },
    { "id": "cost_single_source", "pass": true, "detail": "0.008 SOL roundtrip" },
    { "id": "feed_live", "pass": false, "detail": "STALE: último tick hace 92 s" },
    { "id": "pnl_reconciliation", "pass": true, "detail": "delta 0.00 USD" },
    { "id": "desktop_contract", "pass": true, "detail": "golden schema OK" },
    { "id": "min_sample_rules", "pass": false, "detail": "2 reglas mostradas con N<30" } ] }
```

---

## 6. Leyes anti-falacia (permanentes, verificables)

1. **Sin dato → `N/D`.** Prohibido renderizar literales default (`10,000.00 USD`, `0.91`,
   `85.0`) como si fueran métricas. (Origen: F1a, F4.)
2. **Todo número visible lleva procedencia**: etiqueta + timestamp + fuente. (INDEX.md ya lo exige.)
3. **Claimed ≠ Measured.** Cifras históricas falsas solo pueden aparecer con `[CLAIMED — REFUTADO]`
   y banner (patrón ya usado en `outputs/INDEX.md`).
4. **Regla mostrada = regla aplicada.** Toda "regla adaptativa" visible debe tener un punto de
   consumo en la ruta de decisión demostrable por test. (Origen: §2.3.)
5. **Fail-closed**: feed caído, modelo ausente o muestra < 30 ⇒ estado explícito
   (`STALE`/`INSUFFICIENTE`), nunca un verde.
6. **Contrato con test**: la UI (exe y web) consume solo esquemas con golden test. (Origen: F1.)

---

## 7. Riesgos y no-objetivos

- **Riesgo:** "mejorar la UI" podría maquillar EV negativo. Mitigación: P3 antes que P1 en
  cualquier release público; badges `[CLAIMED]` obligatorios.
- **Riesgo:** cablear reglas adaptativas sube la varianza si las muestras son chicas.
  Mitigación: P2.2 (N≥30) y shadow evaluation previa.
- **No-objetivo:** prometer rentabilidad. El único mensaje permitido hoy es
  `SIN EDGE DEMOSTRADO` hasta que el gate selle lo contrario.
- **No-objetivo:** reescribir el motor en otro lenguaje; el servidor stdlib + web UI es la SSOT.
- **Restricción:** máx. 3 archivos por PR (regla del repo) ⇒ las fases se cortan en PRs pequeños.

---

## 8. Orden de ataque recomendado (por impacto visible)

1. **P0 completo** (1-2 días): el exe deja de mentir, el log cobra vida, contrato con test.
2. **P1.1-P1.3** (3-4 días): el diagrama de decisión en vivo (petición #1 del usuario).
3. **P2.1-P2.4** (4-5 días): el loop de aprendizaje cableado y visible (petición #2).
4. **P3** (2-3 días): anti-falacias visible (petición #4) — y cierre del riesgo de maquillaje.
5. **P4/P5** según recursos.

**Definición de éxito de este plan:** abrir el escritorio y ver, en vivo y sin mentir:
qué tokens entran, en qué filtro cae cada uno y por qué, qué reglas aprendió el sistema esta
semana y qué cambió en la decisión a causa de ellas, y cuánto falta (número exacto) para poder
aspirar a dinero real.

---

## Anexo A — Mapa hallazgo → fase

| Hallazgo | Fase que lo cierra |
|---|---|
| F1a-F1e contrato Desktop↔Server | P0.1, P0.2, P0.5 |
| Log/panel vacío | P0.3 |
| Sin diagrama de decisión | P1 |
| Loop de aprendizaje decorativo | P2.1, P2.2, P2.5 |
| Sin panel de progreso del gate | P2.4, P5 |
| Defaults/claims sin etiqueta | P3.1, P3.3 |
| Doble puerto / build exe | P0.4, P4.5 |
| Sin banca paper | P0.1 |

## Anexo B — Archivos por fase (máx. 3 por PR)

- **P0 PR-1:** `cockpit/server.py` + `scripts/PumpfunCockpitLauncher.cs` + `scripts/build_cockpit_exe.ps1`
- **P0 PR-2:** `cockpit/events.py` (nuevo) + `cockpit/server.py` + `tests/test_desktop_contract.py`
- **P1 PR-1:** `live_store.py` (tabla trazas) + `gmgn_bridge.py` (emitir trazas) + `cockpit/server.py`
- **P1 PR-2:** `static/index.html` (vista pipeline) + test de UI
- **P2 PR-1:** `auto_evolution.py` + `gmgn_bridge.py` + `gmgn_dossier_scanner.py`
- **P2 PR-2:** `ai_engine.py` + `server.py` + `static/index.html`
- **P3 PR-1:** `server.py` + `label_guard.py` + `static/index.html`

---

## Anexo C — Estado P0 (implementado 2026-10-15, sin commit)

| # | Estado | Evidencia verificable |
|---|---|---|
| P0.1 | ✅ | `compose_dashboard()` pura en `cockpit/server.py`; claves exactas fijadas por `TestComposeDashboard.test_golden_key_contract`. Extensiones declaradas: `open_positions`, `shadow_vetos`, `sol_price_usd`, `sol_price_source`, `metrics_known`, `exposure_usd`. |
| P0.2 | ✅ | El exe lee SOLO `dashboard.*` (test regex `TestExeSourceContract`); claves muertas (`signal_action`, `balance_usd`, `net_saved_usd`) bloqueadas por test; chat envía `{"message": ...}` (el server lee `params["message"]`). |
| P0.3 | ✅ | `cockpit/events.py` (ring 500 + `runtime/events.jsonl` + SSE) y rutas `/api/logs`, `/api/events`; smoke tests de endpoint y SSE. La web consume el stream en P1/P2 (Anexo B), el exe ya lo hace vía `/api/logs?since=`. |
| P0.4 | ✅ | `scripts/build_cockpit_exe.ps1` compila con `csc.exe` de .NET Framework 4.0 (no hay Roslyn/dotnet SDK de proyecto en esta máquina); `PumpfunCockpit.exe` regenerado (41.984 bytes, 2026-10-15 21:56). |
| P0.5 | ✅ | `pumpfun_detector/tests/test_desktop_contract.py` (23 tests): contrato golden, EventBus/JSONL/SSE, whitelist de claves del C#, endpoints. |

**Anti-falacia aplicada en P0:** el precio SOL (150 USD) del paper broker no es una medición en vivo y ahora viaja con `sol_price_source="assumed_default"` (la UI lo etiqueta); `equity_usd` solo se calcula si hay PnL medido; ninguna métrica ausente se pinta con un default.

**Pendiente inmediato (P1):** migrar `static/index.html` al bloque `dashboard` y al stream SSE (hoy lee las claves raíz del summary y no tiene consola de eventos).

---

## Anexo D — Hotfix H1: ticker contrafactual de Shadow Vetos (2026-10-15/16, sin commit)

**Bug de producción (evidencia):** `AutoEvolutionEngine.tick_shadow_veto_outcomes()`
sólo actualizaba precios si el feed exponía un método `fetch_token()` que
**`MarketFeed` no tiene** (`market_cap_usd()` / `token_snapshot()` sí). Resultado:
`shadow_veto_ledger` en `cockpit_forward.db` → **28 filas, `sum(checks_count)=0`,
28/28 `PENDING`**, con el loop vivo llamando al ticker cada 15 s (no-op silencioso).
El test viejo pasaba en verde porque su doble `_StepFeed` sí tenía `fetch_token`
(drift mock↔real). `true_saves / false_vetos / avoided_loss_pct` del dashboard
nunca podían moverse.

**Fix (3 archivos):**

| Archivo | Cambio |
|---|---|
| `detector/auto_evolution.py` | Adapter `feed_market_cap_usd(feed, mint)`: usa `market_cap_usd()` / `token_snapshot()` (dict/número/atributo) con compat legacy `fetch_token()`; sin `except: pass` — fallas contadas en `last_shadow_tick` (`checked/hits/misses/has_feed`) y `shadow_miss_total`. |
| `detector/live_store.py` | `list_shadow_vetos_for_check()` — rotación `ORDER BY last_check_ts ASC`: los vetos viejos ya no mueren en `PENDING` detrás de los 5 más nuevos. |
| `tests/test_shadow_ticker.py` (nuevo, 6 tests) | Incluye test **anti-drift contra la clase REAL `MarketFeed`** (transporte HTTP inyectado) + rotación + miss-counting. |

**Evidencia en vivo (mismo par proceso/DB, antes → después):**

| Métrica | Antes | Después (~50 s de loop) |
|---|---|---|
| `sum(checks_count)` | 0 | 19 |
| Veredictos | 28 `PENDING` | 17 `TRUE_SAVE`, 1 `FALSE_VETO`, 1 `NEUTRAL_HOLD`, 12 `PENDING` (rotando) |
| `dashboard.evolution` | saves 0 / false 0 / avoided 0 | saves 16 / false 2 / avoided 883.3% |

**Primer hallazgo del loop revivido:** veto `VETO_SAME_NAME_CLONE` sobre `ROOMS`
resultó **FALSE_VETO +192.13%** (runner perdido). N=2 false-vetos < 30 → **no se
toca ningún umbral** (BP-003); queda como insumo de la Vista 2 (efecto por regla).

**Deuda declarada:** veredictos `SALVADA POR FRICCIÓN` (net −5.2% con MC plano)
hoy cuentan como `TRUE_SAVE`; revisar taxonomía (fricción vs caída real) en P2/P3.

**Verificación:** `scripts/verify.ps1` → exit 0 (463 passed + 17 subtests; gate 0
bloqueos; ciclo `SIN EDGE DEMOSTRADO`).

---

## Anexo E — P1 (backend): Trazas de decisión E0→E6 + pipeline en vivo (2026-10-16, sin commit)

**Objetivo:** que "el pensamiento filtrado" de cada decisión sea visible y
replayable en tiempo real — qué etapa evaluó, con qué valor, contra qué umbral,
si pasó, falló o **no se ejecutó** (tras el primer veto las etapas restantes se
marcan `skip`: espejo fiel del `elif` real de `AIQuantModel.predict_token`; no se
inventan evaluaciones que no ocurrieron).

**Entregado (3 fuentes + 1 test):**

| Archivo | Cambio |
|---|---|
| `detector/live_store.py` | Tabla `decision_traces` (1 fila por etapa) + `append_decision_trace()` (retención: poda a 20k filas), `list_decision_traces()`, `get_decision_trace(mint)`, `prune_decision_traces()`. |
| `detector/gmgn_bridge.py` | `build_decision_stages()` (pura): E0 discovery · E1 métricas fail-closed · E3 cadena forense (bundler→dev_factory→wash→clone→dev_holding→cabal→liquidez) · E4 EV neto · E4b P(éxito) · E5 veredicto. Emisión por token en `screen_tokens_to_buy()` + etapa **E6 ejecución** (score≥85, auto-bot, ALREADY_OPEN, NO_LIVE_PRICE) — refactor del auto-sniper sin cambio de comportamiento. |
| `cockpit/server.py` | `GET /api/pipeline/live?limit=&window_s=` (trazas + embudo: seen/veto/neutral/admitted/executed + `veto_by_stage`) y `GET /api/pipeline/trace/{mint}`; `summarize_pipeline_trace()`; `PIPELINE_SCHEMA="pipeline/1"`. |
| `tests/test_pipeline_traces.py` (9 tests) | Store (agrupado/orden/poda/por-mint), builder (veto bundler→skip posterior, metrics_known fail-closed, admitido, neutral EV≤0), endpoints (funnel + por mint + mint inexistente). |

**Evidencia en vivo (server :8000, ~75 s de loop):**
`available=True, schema=pipeline/1, traces=10, funnel.seen=10, veto=10`, desglose
real: **6× `E1_metrics_known`** (candidatos DexScreener sin métricas de holders →
fail-closed), **3× `E3_clone_factory`**, **1× `E3_cabal_top10`**. Traza completa
verificada (p.ej. `BOT`): E0 `info` MC $16,666 (fuente `dexscreener`) → E1 `fail`
→ E3/E4/E6 `skip` → E5 `info` veredicto. `/api/pipeline/trace/{mint}` devuelve
historial (5 evaluaciones del mismo mint en la ventana).

**Verificación:** `scripts/verify.ps1` → exit 0 (472 passed + 17 subtests; gate 0
bloqueos; ciclo `SIN EDGE DEMOSTRADO`). `check_claims.py`: 77 errores (baseline, 0 nuevos).

**Pendiente de P1 (movido al ítem 5 del orden):** vista "Pipeline en vivo" en
`static/index.html` (consume `/api/pipeline/live` + migración al bloque
`dashboard` + consola SSE). Backend ya operativo; la UI web queda como trabajo
visual sin bloquear al resto.

**Siguiente:** 5 — UI web (pipeline + dashboard + consola SSE) → 6 — P3 anti-falacias.

---

## Anexo F — Ítems 3-4: umbrales adaptativos cableados al decisor con puerta BP-003 + auto-/boost ML (2026-10-16, sin commit)

**Objetivo:** que el bucle de aprendizaje deje de ser decorativo SIN violar la
ley de significancia (AGENTS.md §4): **ningún umbral adaptado puede mover al
decisor con N < 30 operaciones forward cerradas**. Antes de esta tarea,
`adaptive_rules.json` se escribía pero nadie lo leía para decidir (el decisor
usaba literales hardcodeados): bucle roto. Ahora está cableado con puerta dura.

**Entregado (5 fuentes + 1 test):**

| Archivo | Cambio |
|---|---|
| `detector/auto_evolution.py` | `closed_trades_seen` (muestra forward acumulada, persistida) + `adjusted_keys` (valor ajustado con su `n_support` y timestamp) + `get_effective_decider_thresholds()`: la **puerta BP-003** — regla ajustada sólo desbloquea si `n_support >= 30`; si no, literal del código. Visible en `/api/evolution/status.decider_thresholds`. |
| `detector/ai_quant_model.py` | `predict_token(thresholds=...)`: umbrales efectivos inyectables (bundler/dev/top10/liq-ratio). Cualquier valor ausente, no numérico o ≤0 cae al literal (fail-closed: dato inválido jamás relaja un escudo). Devuelve `thresholds_used` para que la traza P1 declare el umbral EFECTIVO. |
| `detector/gmgn_bridge.py` | El decisor resuelve umbrales desde `AutoEvolutionEngine` antes de inferir; `build_decision_stages(..., thresholds_meta=...)` marca cada etapa con `source_kind="adaptive_rule"` vs `"default_literal"`. Auto-`/boost` ML: recalibración completa cada **30 cierres paper** (`AUTO_BOOST_EVERY_N=30`, alineado a N≥30), declarada en `last_auto_boost` (éxito o error, nunca silencio) y expuesta en `get_summary()`. |
| `detector/ai_engine.py` + `cockpit/server.py` | El chat manual (`/audit`, auditoría forense) usa los MISMOS umbrales que el decisor (`_effective_decider_thresholds`); se eliminaron dos comparaciones hardcodeadas (`top10 <= 30`, `dev <= 1.5`) que habrían mostrado una realidad falsa si el gate desbloquea reglas. `CockpitState` inyecta el engine en `AIEngine`. |
| `tests/test_adaptive_decider.py` (10 tests) | Gate bloqueado con N=1 (literal 20.0) · desbloqueado con N=30 (valor apretado < 20.0, `adaptive_rule`) · mapeo top10 (`max_top10_float_pct`→`max_top10_pct`) · overrides de `predict_token` cambian veto real (bundler, cabal) · traza muestra umbral efectivo + procedencia · auto-boost exactamente a los 30 cierres (no antes) · consistencia AIEngine (sólo reglas desbloqueadas pasan). |

**Evidencia en vivo (server :8000):** `/api/evolution/status` →
`decider_thresholds` con `max_bundler_pct=20.0` / `max_top10_pct=30.0`,
`source="default_literal"`, `unlocked=false`, `min_n=30`, `closed_trades_seen=0`
(la muestra forward empieza a contar por cierre post-reinicio — honesto).
Trazas P1 renderizan `E3_bundler threshold=20.0 source_kind=default_literal`:
mientras el gate esté cerrado, la traza demuestra que el decisor usa literales.

**Ley respetada:** el ajuste por post-mortem individual sigue ocurriendo (aprende
de cada cierre), pero **no toca al decisor** hasta que su N de respaldo ≥30 —
antes era un bucle inerte; ahora es un bucle armado pero disparado sólo con
significancia. Auto-`/boost` también espera N=30.

**Verificación:** `scripts/verify.ps1` → exit 0 (480 passed + 17 subtests; gate 0
bloqueos; ciclo `SIN EDGE DEMOSTRADO`). `check_claims.py`: 77 errores (baseline, 0 nuevos).

**Nota deuda declarada:** `closed_trades_seen` cuenta cierres procesados por el
motor desde su arranque (no lee la tabla histórica). Al reiniciar, el contador
vuelve a 0 aunque existan cierres previos — decisión conservadora (exigir 30
cierres forward NUEVOS por sesión de aprendizaje); revisar en P3 si conviene
sembrarlo desde `live_outcomes`.

---

## Anexo G — Ítem 5: UI web migrada a la API real + paridad con la app nativa + candado de contrato (2026-10-18, sin commit)

**Objetivo:** que la UI web (`cockpit/static/index.html`) consuma EXACTAMENTE los
mismos contratos que la app nativa Windows (WinForms C#): mismos endpoints, mismos
nombres de campo, misma honestidad de datos. Antes, la web leía campos muertos
(`data.summary`, `features_snapshot`, `same_name_count_24h`, `dd.entry_mc`,
`d.chain_id`, `d.price_usd`...) que el server jamás emite, y mostraba literales
fabricados como fallback (`|| 0.91`, `|| 29598`, `$150.00`, badges estáticos
"GEMINI CONECTADO") cuando un fetch fallaba. Un revisor adversarial produjo 19
hallazgos (F1–F19): 16 corregidos en cliente, 2 declarados riesgo residual, 1 INFO.

**Entregado (4 archivos + este anexo):**

| Archivo | Cambio |
|---|---|
| `cockpit/static/index.html` | Migración completa: bot-status Tramos C/D, badge+tabla de evolución, `renderDeciderThresholds` (dict plano por regla: value/source/unlocked/n_support/min_n/closed_trades_seen), bloque Pipeline (funnel + traces + ladder + historial), consola SSE, bloque ML con guardas `> 0` (0.0 = "sin dato", no métrica). Correcciones de contrato: POST anotación envía `verdict:"buy"/"veto"` y lee `data.signal_id`/`data.reviews`; mapeo dossier a campos reales de `TokenDossier` (`d.chain`, `d.volume_m5_usd`, `d.composite_dd_score`, `d.dev_balance_pct`, `d.same_name_count`, `d.veto_reasons`); SSE `dd.entry_mc_usd` y `evo old/new`; `escapeHtml()` en todo texto externo (símbolos, nombres, motivos, diagnósticos); badges estáticos arrancan `N/D — sin dato`; funnel null-aware (`fnum()`, sin ceros sintéticos); `EventSource.CLOSED` → recreación a 5 s; guardas `nd()` en TP/SL y score de inspección. |
| `tests/test_web_contract.py` | +9 candados (sección 5, contratos campo UI↔server): anotación (`verdict`/`signal_id`), mapeo dossier canónico, `same_name_count` canónico, campos SSE, escapes HTML, guardas UI (chip-all/EventSource/SL), badges estáticos honestos, métricas ML gated, y escaneo regex con espaciado variable de fallbacks fabricados (`\|\|\s*0\.91` etc.). Total web: 20 tests. |
| `cockpit/launch.py` | **Bloqueador del gate G3** (verify paso 2): 4 rutas absolutas con letra de unidad fija (Chrome/Edge x86/x64) → `os.path.expandvars(r"%ProgramFiles%...")`. Sin esto `verify.ps1` no cierra 0. |
| `outputs/PLAN_MAESTRO_COCKPIT_V2.md` | Este anexo. |

**Paridad nativa:** `scripts/PumpfunCockpitLauncher.cs` NO se tocó (contrato C#
congelado); `tests/test_desktop_contract.py` sigue verde. La web ahora es espejo
de los mismos shapes que consume la app nativa.

**Evidencia:**
- `node --check` sobre JS inline extraído: 1 bloque, 114.139 bytes → exit 0.
- `pytest tests/test_web_contract.py tests/test_desktop_contract.py -q` → 46 passed (aislado).
- Suite completa → **534 passed + 17 subtests**.
- Server :8000 reiniciado con código final: `/`, `/api/bot/status`, `/api/pipeline/live`, `/api/evolution/status`, `/api/ai/context`, `/api/ai/schema`, `/api/system/telemetry` → 200; `/api/events` → `text/event-stream`; página servida 203.964 bytes con marcadores finales (`escapeHtml`, `chip-cat-all`, `verdict buy/veto`).
- `scripts/verify.ps1` → **exit 0** (gate 0 bloqueos · 0 avisos; VERIFY OK; ciclo read-only OK con `edge_demostrado=False -> live bloqueado`).

**Incidente de entorno (máquina con Reboot Restore):** entre sesiones la máquina
revirtió al baseline: temp limpiado, y `pandas`/`matplotlib`/`pyarrow` ausentes de
la venv raíz (26 errores de colección en la suite completa + barrido parquet
roto). Restaurados en sesión: `pandas 3.0.6`, `matplotlib 3.11.2`, `pyarrow 26.0.0`.
**Bajo Reboot Restore son efímeros**: si una futura sesión ve
`ModuleNotFoundError: pandas` en la suite, reinstalar antes de sospechar del código.

**Riesgos residuales declarados:**
1. Interpolación de valores de terceros (mint/símbolo) dentro de atributos `onclick` (`copyText('...')`): el escape HTML no cubre contexto JS-string. Aceptado: los valores vienen de la API (mints base58/strings cortos) y el vector exigiría comprometer la fuente; deuda para P3 si se quiere `JSON.stringify` sistemático.
2. Evento SSE `decision` listado en `EVENT_KINDS` y con rama de consola, hoy no emitido por el server (INFO, sin efecto visual).
3. RAF del visor 3D sigue corriendo con la vista oculta (deuda de rendimiento, sin impacto de correctitud).

**Ley respetada:** cero literales fabricados (candados de test + regex con
espaciado variable); dato ausente → `N/D — sin dato`; ninguna promesa de EV; el
gate de dinero real sigue bloqueado (`edge_demostrado=False`).

---

## Anexo H — Ítem 6: anti-falacias lado Python (Copiloto IA + contexto de agentes) (2026-10-18, sin commit)

**Problema:** el copiloto IA y el contexto para agentes fabricaban métricas cuando
el dato no existía: `/boost` mostraba `ROC-AUC 0.9100`, `29,598` tokens, `1.80s`;
la rama general citaba `ROC-AUC 0.9100 / 29,598`; `_find_token_in_db` rellenaba
`market_cap=7500.0`, `liquidez=2200.0`, `dev=0.5`, `top10=22.0`; un token ausente
recibía una **auditoría sintética completa**; el contexto de agentes publicaba
`equity_usd=10000.0` por defecto. Violación directa de la ley anti-falacia (§1.5).

**Entregado (3 archivos):**

| Archivo | Cambio |
|---|---|
| `detector/ai_engine.py` | Helpers `ND_METRIC`, `_metric_num` (>0 = medida), `_num_or_none` (≥0, 0 válido en mercado), `_metric_str`, `_first_field`. `_find_token_in_db` con alias reales (`currentMarketCap`/`firstMarketCap`/`firstLiquidity`/`devBalancePct`/`top10HoldersPct`) y **sin** coerción: si el campo no está, queda `None`. Token ausente → mensaje honesto "no está en la base canónica" (sin token sintético); features incompletas → "Auditoría incompleta" nombrando lo que falta. Rama general: línea de entrenamiento `N/D` vs real; umbrales de cabal/dev citados desde `get_effective_decider_thresholds()`. `/boost`: métricas `N/D` cuando faltan + línea `Estado`. `get_ai_context`: `paper_wallet`/`evolution_engine`/`quant_model_metadata` pass-through (null ≠ 0 fabricado), alias `pnl_usd`→`total_pnl_usd`. |
| `cockpit/server.py` | Fix de plumming: `/api/ai/context` leía `state.state()`, que **no** incluye `dashboard` → todo `None`. Se extrajo `compose_bot_dashboard()` (mismo bloque canónico que `/api/bot/status`) y ahora el copiloto ve el `paper_wallet`/`evolution` reales. Paridad verificada en vivo. |
| `tests/test_ai_cockpit.py` | 6 tests nuevos: `N/D` sin metadata (con providers mockeados), métricas reales cuando existen, `/boost` sin fabricar, contexto null/real, alias `pnl_usd`, y **candado estático** con `inspect.getsource` que prohíbe reintroducir los literales (`0.91`, `29598`, `7500.0`, `2200.0`, `6000.0`, `1800.0`, `"top10_concentration_pct": 22.0`, `"dev_balance_pct": 0.5`). |

**Evidencia:**
- Suite completa → **540 passed + 17 subtests** (534 → 540, +6 exactos).
- Live `:8000/api/ai/context`: `equity_usd=10164.7`, `total_pnl_usd=164.7`, `closed_count=8` (idénticos a `dashboard.paper_wallet` de `/api/bot/status`); `true_saves=10 / false_vetos=3`; `roc_auc=1.0 / train_samples=720` = metadata real del modelo del servidor (no default).
- Gate de integridad: 0 bloqueos · 0 avisos (2 rutas literales del Anexo G/G.6 saneadas en este doc para pasar G3).

**Riesgos residuales declarados:**
1. `roc_auc=1.0` es la métrica **real almacenada** del modelo (N=720, probable sobreajuste): es honesta como medición, pero no es evidencia de edge — el gate OOS sigue en `edge_demostrado=False`.
2. `evolution_engine.adaptive_rules` en el contexto de agentes es `None` (el `get_status_summary()` no las incluye); se expone como no disponible en vez de `{}`.
3. La rama C usa el primer candidato tras stopwords: tokens homónimos de palabras comunes (p. ej. "COPILOTO") devuelven "no está en la base canónica" — comportamiento honesto, mejor ruta vía `mint` explícito.

**Ley respetada:** dato no medido → `N/D — sin dato` o `null`; cero tokens
sintéticos; cero promesas de EV; mismo bloque `dashboard` para exe, web y agentes.

---

## Anexo I — P1: anti-falacias en `gmgn_bridge.py` (A quién seguir / Qué comprar / Auditoría) (2026-10-18, sin commit)

**Problema:** el puente GMGN fabricaba datos en los tres flujos hacia el Cockpit:

1. `audit_token` sin CLI ni escáner devolvía un **PASS sintético** con literales (`rug_ratio 0.15`, `top10 22.0`, `dev 0.5`, `snipers 10.0`, MC/liq `15000/4500`).
2. `rank_wallets_to_follow` mostraba el catálogo demo **siempre** (aun con `demo_mode=False`), y `_load_wallets_from_db` inventaba `PnL = solBalance × 150`, `WR30 = WR7 × 0.95`, holding `2490/134 s` y floor `max(trades, 5)`.
3. La rama de esquema `tokens` (sin métricas on-chain) generaba candidatos completos inventados (`MC 6800`, `top10 21.5`, sparkline sintética, "Ballena Verificada"); la rama `Token` producía sparkline sintética y marcaba `metrics_known=True` con columnas ausentes; E0/E4b mostraban `$0` / `0.0%` cuando el dato faltaba.

**Entregado (3 archivos):**

| Archivo | Cambio |
|---|---|
| `detector/gmgn_bridge.py` | **`audit_token`**: sin fuente verificable → `verdict "UNVERIFIED"`, `is_safe False`, métricas `None`, patrón `UNVERIFIED_METRICS` (nunca PASS falso); fuente parcial → `UNVERIFIED` nombrando las métricas faltantes; PASS solo con las 4 críticas completas. **`rank_wallets_to_follow`**: catálogo demo solo con `demo_mode=True` (en vivo `[]`); DB sin PnL/WR30/holding inventados ni floor de trades; escalera tri-estado (`is_copyable: bool/None`) con razón explícita de datos incompletos; sort `None`-safe. **Tokens**: rama `tokens` fail-closed (omite + warning, cero candidatos); rama `Token` solo columnas reales, `sparkline: []`, `bundler_pct` real, `metrics_known` = presencia de top10/dev/bundler. **`screen_tokens_to_buy`**: sin sparkline default, sin `vol5m/10` para avg-tx, `holder_count` 0 si ausente. **`build_decision_stages`**: E0/E3/E4b con `value=None` + `N/D` cuando el dato falta. `SmartWalletProfile` con campos `Optional`. |
| `tests/test_market_feed.py` | 9 tests anti-falacias: wallets fail-closed en vivo; demo solo explícito; DB de wallets sin campos inventados; esquema `tokens` sin fabricación; esquema `Token` medido con `metrics_known` real; `audit_token` UNVERIFIED sin datos; dossier real sin MC/liq sustitutos; trazas E0/E4b con `N/D`; **candado estático** que prohíbe reintroducir los literales erradicados (`6800.0`, `2100.0`, `21.5`, `sol_bal * 150.0`, `wr * 0.95`, `max(trades, 5)`, `vol5m / 10.0`, sparkline default). |
| `cockpit/static/index.html` | `renderWallets` vía `nd()`: WR7/WR30/PnL/trades/holding muestran `N/D` con clase neutra si son `null`; `is_copyable` tri-estado (`SÍ` / `NO (MEV)` / `N/D`) en vez de tratar `null` como MEV. |

**Evidencia:**
- Suite completa → **549 passed + 17 subtests** (540 → 549, +9 exactos).
- `scripts/verify.ps1` → VERIFY OK (exit 0): STATE.json fresco, gate 0 bloqueos, guard causal 9 passed, integridad de costes 9 passed, ciclo honesto OK.
- `scripts/check_claims.py`: 77 errores = baseline exacto (0 nuevos).
- En vivo (`demo_mode=False`): `/api/gmgn/wallets` responde `[]` cuando no hay fuente; las ramas offline `custom.db` (`tokens`) y `demo_pumpfun.db` no fabrican candidatos.

**Riesgos residuales declarados:**
1. `WATCH` por datos incompletos es estado honesto, **no** recomendación: `is_copyable=null` indica ausencia de evidencia; el gate de dinero real (N≥100 OOS, EV>0, PF>1.25) sigue cerrado (`edge_demostrado=False`).
2. El catálogo demo permanece para tests/offline tras el contrato `demo_mode` (default False en servidor, launch.py y launchers); su aparición en vivo sería un bug de configuración, no de datos.
3. `audit_token` por CLI con campos parciales ahora devuelve `UNVERIFIED` (antes fabricaba PASS): consumidores deben leer `metrics_known`.
4. El alias bundler (`sniper_pct` si ≥25%, si no 0.0) se conserva como proxy medido (continuidad BP-006) cuando la fuente no expone `bundler_pct`.

