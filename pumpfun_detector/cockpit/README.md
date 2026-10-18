# Cockpit — observabilidad y visualización 3D del entrenamiento

Ventana de escritorio (o navegador) que muestra **en vivo** cómo el motor
decodifica parámetros y patrones — y, sobre todo, **cómo se distingue un edge
real de una ilusión de lookahead**.

> **Regla de oro:** el cockpit **solo lee**. No decide trades, no escribe en la
> base y **no puede mostrar un número que el pipeline no pueda justificar**.
> Cada métrica va etiquetada con su procedencia y su sesgo.

---

## Qué se ve

| Panel | Contenido |
|---|---|
| **Pantalla de Carga Holográfica** | Radar cuántico animado ("cargando...") con progreso por fases (conexión, lectura SQLite, tensor 3D) |
| **Universo (real)** | 65,423 tokens, % que llega a +50%, winrate real de `PaperPosition`, PnL medio (−16.0%) |
| **Trampa de lookahead (en vivo)** | El mismo filtro **CON** y **SIN** la condición futura (`MC final ≥ 4500`), para ver el colapso 98.15% → 20.57% |
| **Sandbox Cuantitativo & Filtros Interactivos** | Sliders e inputs numéricos con sincronización bidireccional (FMC, Liquidez, Ratio Liq/MC) y toggle con alerta de lookahead |
| **Motor de Predicción Reactivo** | Cálculo en tiempo real (60fps) de $N$, tasa +50% con Intervalo de Confianza al 95% (Wilson/SE), Paper WR, Expected Value ($EV$) **medido** (expectativa con las medias reales de ganancia/pérdida, no un TP/SL nominal) y Criterio de Kelly ($f^*$) con la relación de pago real |
| **Tokens & Selección** | Cableado visible (base, snapshot, feed, loop, ciclo, paper, ML), filtros (texto, MC, liquidez, volumen, score, veredicto), mapa de burbujas MC × liquidez × volumen, embudo del proceso de selección, cadena de decisión E0→E6 por token, memoria de decisiones (168 h) y validación con los criterios de certificación de paper-forward |
| **Análisis de Rendimiento** | Informe real de las operaciones cerradas: KPIs (PnL neto, retorno, win rate medido, profit factor, expectativa, ganancia/pérdida media, drawdown máx.), curva de capital + drawdown, donut ganadoras/perdedoras, histograma de PnL por operación y desglose por motivo de salida. Filtro de período (todo / 90d / 30d / 7d) |
| **Nube Manifold 3D Interactiva** | Cada token = un punto `(firstMC, firstLiq, MC_final)`. Caja 3D dinámica del filtro activo, plano ámbar de lookahead, inspección HUD al pasar el ratón (hover tooltip con Mint, PnL y status) y telemetría de cámara |
| **Umbrales y Arquetipos** | Valores vigentes de `MasterSnipeSignal` y clasificación de `PatternIdentifier` con etiqueta `claimed_unvalidated` |

El visor 3D es **autocontenido**: proyección 3D propia sobre `Canvas 2D`, sin
Three.js, sin CDN, sin red externa. Arrastra para rotar, rueda para zoom, botones de vista cenital/frontal/isométrica.

---

## Uso

```powershell
# 1) Lanzamiento con un solo clic en Windows:
.\launch_cockpit.bat

# 2) O desde terminal:
python -m cockpit.launch --rebuild     # construye/actualiza snapshot (~0.1s)
python -m cockpit.launch               # arranca y abre ventana nativa (WebView2 o Edge App Mode)

# Opciones adicionales
python -m cockpit.launch --no-window          # abre en el navegador
python -m cockpit.launch --port 8787
python -m cockpit.launch --db "$PUMPSCOPE_DB"
python -m cockpit.launch --live-store live_decisions.db
```

**Ventana nativa:** se abre en una ventana de escritorio independiente sin barras de navegador (WebView2 mediante `pywebview` o Microsoft Edge en modo aplicación `--app`).

---

## API local

| Ruta | Método | Devuelve |
|---|---|---|
| `/` | GET | Terminal cuántica interactiva y visor 3D |
| `/api/state` | GET | Snapshot JSON (universo, sesgos, umbrales, puntos 3D) |
| `/api/rebuild` | POST | Reconstruye el snapshot reescaneando la base SQLite |
| `/api/evaluate` | POST | Evalúa filtros en tiempo real sobre los 65k tokens (devuelve $N$, WR, EV, lookahead flag) |
| `/api/filter` | GET / POST | Consulta o persiste la configuración activa de filtros (`active_filter.json`) |
| `/api/performance` | GET | Informe de rendimiento real de las operaciones cerradas (`?period=all`, `90d`, `30d` o `7d`). Solo agrega lo ocurrido: win rate medido, profit factor, expectativa (EV por operación), curva de capital, drawdown máximo, histograma y desglose por motivo. Los timestamps se normalizan a segundos (la base los guarda en ms) |
| `/api/wiring` | GET | Estado de **todos** los subsistemas cableados en una llamada: base (ruta resuelta, columnas faltantes y fallbacks), salud del snapshot, feed real, loop autónomo, ciclo de automejora, paper y modelo ML |

---

## Cableado automático (cero configuración manual)

El cockpit **se configura solo**; no hace falta exportar variables ni ejecutar comandos previos:

1. **Base de datos auto-resuelta.** Orden: ruta explícita → `$PUMPSCOPE_DB` → candidatos relativos al
   cwd, a la raíz del repo y al directorio del paquete. Esto arregla el bug del `.bat`, que hacía
   `cd pumpfun_detector` y dejaba `custom.db` sin encontrar.
2. **Snapshot auto-reparado.** Se reconstruye si falta, está **corrupto** (`ok:false`) o **rancio**
   (más de `SNAPSHOT_MAX_AGE_S`, 15 min por defecto). Antes solo se construía si el archivo no
   existía, así que un snapshot envenenado sobrevivía para siempre.
3. **Esquema adaptativo y honesto.** Si `Token` no tiene `marketCap`, se usa `currentMarketCap` como
   fallback declarado; si no tiene `rise50At`, la tasa de +50% viaja como `None` → la UI muestra
   `N/D — sin dato`, **nunca un 0% falso**. `db_schema` y `/api/wiring` publican qué falta.
4. **Ciclo de automejora desatendido.** `AutoImprovementCycle` corre el ciclo completo
   (universo → barrido causal con gate OOS → `cycle.json`) cada `CYCLE_INTERVAL_S` (30 min),
   refrescando features cada 4 pasadas. Ya no hay que lanzar `python -m cockpit.cycle --loop` a mano.
5. **Loop de trading paper** en marcha con el feed real de DexScreener (fail-closed).

```powershell
python -m cockpit.launch                 # ventana nativa + todo cableado
python -m cockpit.launch --headless      # solo servidor, SIGUE siendo autónomo
python -m cockpit.launch --no-loops      # servidor pasivo (CI / depuración)
```

El servidor enlaza **solo a `127.0.0.1`**: es una herramienta local de análisis, no un
servicio expuesto.

---

## Qué NO es

- **No es un backtest.** No calcula rentabilidad; la *muestra* y la *audita*.
- **No arregla la estrategia.** Si no hay edge, el cockpit lo dice (y por eso
  muestra el universo completo: **12.55% WR, −16% PnL medio**).
- **No es una app C#/WPF.** Es un proceso Python con ventana nativa vía WebView2.
  Motivo: todo el motor es Python; reescribirlo en C# sería duplicar la lógica y
  crear una segunda fuente de verdad (justo el problema que causó los números
  falsos). Ver `STATUS.md` §7.
