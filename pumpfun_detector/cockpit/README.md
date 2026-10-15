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
| **Motor de Predicción Reactivo** | Cálculo en tiempo real (60fps) de $N$, tasa +50% con Intervalo de Confianza al 95% (Wilson/SE), Paper WR, Expected Value ($EV$) con slippage real (−33.49%) y Criterio de Kelly ($f^*$) |
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
python -m cockpit.launch --db "D:\hjh\pumpscope\db\custom.db"
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
