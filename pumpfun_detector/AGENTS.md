# AGENTS.md — Protocolo Canónico para Agentes Autónomos de IA

> **Ecosistema:** Pump.fun Detector & Cockpit (Solana High-Frequency Quantitative Trading)  
> **Modo Operativo Obligatorio:** DRY-RUN / PAPER FORWARD (Fondos reales estrictamente bloqueados)  
> **Base de Datos Canónica:** `D:\hjh\pumpscope\db\custom.db` (52,169 tokens, 5,021 operaciones cerradas, modo `file:...mode=ro`)  
> **SSOT Canónico de Rendimiento:** [`STATUS.md`](./STATUS.md)  
> **SSOT Canónico de Supervisión:** [`instrucciones/PROTOCOLO_SUPERVISION_ANTIGRAVITY.md`](file:///d:/hjh/instrucciones/PROTOCOLO_SUPERVISION_ANTIGRAVITY.md)

---

## 1. Mandato Supremo y Ground Truth

Cualquier modelo de lenguaje o agente autónomo (Claude, Gemini, Antigravity, AutoGPT) que opere en este repositorio debe asumir como verdad física:
1. **La verdad empírica vive exclusivamente en [`STATUS.md`](./STATUS.md).**
   - La estrategia actual registra **12.55% de winrate real** y **−79,771 USD** en 5,021 operaciones cerradas.
   - Los valores de winrates superiores (81.1%, 74.2%) mencionados en versiones anteriores son **priors teóricos declarados en código, NO mediciones fuera de muestra**.
   - Queda terminantemente prohibido asumir o prometer expectativas matemáticas positivas sin validación walk-forward sellada con `oos_pass = True`.
2. **§3 Sin Mocks Silenciosos ni Simulaciones Complacientes:**
   - La liquidez de salida en memecoins de Pump.fun se evapora. Toda salida por Stop-Loss de pánico debe modelarse con una pérdida real de **`-30% a -35%`** (media empírica auditada: `-33.49%`), nunca al `-8%` ni `-15%` nominal.
   - Las salidas por `STALL` a 60s acumulan una pérdida media auditada de **`-28.93%`** por la latencia del sampler (intervalo medio 72.24s).
   - Incluir siempre comisiones reales de red (0.002 SOL por transacción) y slippage de entrada y salida.
3. **Bloqueo Estricto de Fondos Reales:**
   - El modo Live (`dryRun = false`) está estrictamente bloqueado por software.
   - Se exige certificar en base de datos una muestra out-of-sample forward de $N \ge 100$ trades cerrados, $EV > 0\%$ neto y Profit Factor $> 1.25$.
4. **Significancia Estadística Innegociable ($N \ge 30$):**
   - Prohibido inferir rentabilidad, alterar umbrales o certificar ramas con muestras menores a 30 operaciones forward.
5. **Paper Trading en Vivo sobre Tokens Reales y Automatización Total (Cero Mocks, Cero Manual):**
   - **Modo Paper Trading en Vivo:** El bot opera en **PAPER TRADING REAL FORWARD** (fondos reales bloqueados y protegidos, sin riesgo financiero), pero **TODOS los tokens, precios, volumen, liquidez y curvas de bonding son 100% REALES**, extraídos en vivo desde la blockchain Solana / DEX (DexScreener API / Pump.fun / PumpPortal / Helius / GMGN).
   - **Prohibición Total de Mocks o Tokens Simulados:** Queda terminantemente prohibido inyectar tokens ficticios (`QUANT_SURGE`, `ALPHA_MEME`, etc.) o inventar ticks de precios sintéticos en el Cockpit, radar o bot. Si una fuente de datos externa no responde, el sistema debe reportar explícitamente el estado sin conexión (`fail-closed`), jamás falsear actividad.
   - **Decisión 100% Autónoma Basada en Base de Datos y Autoaprendizaje:**
     * La decisión de comprar o vetar se toma **automáticamente sin intervención manual**, sustentada en:
       a) **Base de datos canónica** (`custom.db` con 65,423 tokens on-chain y 36,998 muestras de entrenamiento): distribución histórica empírica y patrones de fallas.
       b) **Parámetros aprendidos**: umbrales cuantitativos calibrados con significancia estadística OOS (liquidez mínima, top 10 concentración < 30%, dev holding < 1%, snipers bloque 0 < 15%).
       c) **Autoaprendizaje continuo**: modelo de Machine Learning (`AIQuantModel` con HistGradientBoosting) recalibrable con `/boost` y `LossPostMortemLearner` forense tras cada trade cerrado.
   - **Cero Intervención Manual:** Todo el ciclo (descubrimiento de nuevos tokens -> extracción de features -> inferencia de decisión ML -> apertura paper -> seguimiento de precios en tiempo real -> ejecución de TP1 +50%, BE +25%, TP2 +150%, SL -33.49% -> persistencia de resultados y retroalimentación del modelo) corre en segundo plano de manera 100% desatendida.

---

## 2. Arquitectura del Sistema y Puntos de Contacto (SSOT)

No dupliques lógica ni esquemas en documentación. La única fuente de verdad es el código:

| Responsabilidad | Módulo / Clase Canónica | Ubicación |
| :--- | :--- | :--- |
| **Decisor Maestro de Entrada** | `MasterSnipeSignal` | [`detector/master_signal.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/detector/master_signal.py) |
| **Contrato de Salida de Decisión** | `MasterSignalDecision` (dataclass) | [`detector/master_signal.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/detector/master_signal.py#L58-L68) |
| **Umbrales y Reglas Activas** | Archivo dinámico JSON | `resultados/reglas_produccion.json` |
| **Matriz de Confluencia y GMGN Bridge** | `GMGNBridge` / `TokenBuyRecommendation` | [`detector/gmgn_bridge.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/detector/gmgn_bridge.py) |
| **Feed de Precios Real (DEX)** | `MarketFeed` (DexScreener API) | [`detector/market_feed.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/detector/market_feed.py) |
| **Paper-Forward Broker** | `LivePaperTrader` | [`detector/paper_broker.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/detector/paper_broker.py) |
| **Auditor Forense de Fallas** | `LossPostMortemLearner` | [`detector/loss_learner.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/detector/loss_learner.py) |
| **Cockpit Interactivo de Control** | Servidor HTTP / Snapshot Causal | [`cockpit/server.py`](file:///D:/mejoradordebase%20de%20datos/pumpfun_detector/cockpit/server.py) |

### Estructura de Decisión Canónica (`MasterSignalDecision`)
```python
@dataclass(frozen=True)
class MasterSignalDecision:
    admitted: bool                  # Veredicto binario de entrada
    branch: str                     # Rama que validó (o identificador de rechazo)
    confidence: float               # Nivel de confianza empírico (0.0 a 1.0)
    expected_winrate: float         # Prior declarado (ver STATUS.md)
    expected_profit_factor: float   # Prior declarado (ver STATUS.md)
    veto_reason: Optional[str]      # Motivo explícito de descarte si admitted == False
    metrics: dict                   # Snapshot de features evaluadas
```

---

## 3. Lista Negra Inmutable: Antipatrones Prohibidos (BP-001 a BP-012)

Queda terminantemente prohibido proponer o implementar:
- **BP-001:** SL Nominal Ilusorio en Micro-Cap (asumir SL $\le -10\%$ sin modelar caída a $-33.49\%$).
- **BP-002:** Entrada Tardía en Techo de Curva (exigir holders $\ge 30$ y netBuyers $\ge 15$ en $< 2\text{m}$).
- **BP-003:** Filtro Estrangulador con muestra insignificante ($N < 5$).
- **BP-004:** Sesgo de Supervivencia (reportar métricas solo considerando salidas `GREEN`/`TP`).
- **BP-005:** Concentración de Cabal ($> 30\%$ en billeteras coludidas por grafo).
- **BP-006:** Trampa de Bloque 0 (snipers con $> 25\%$ tomado en bloque 0 vía Jito).
- **BP-007:** Saturación Pre-Migración ($> 85\%$ de bonding curve completada).
- **BP-008:** Creador Serial Tóxico (`RUGGER` con $\ge 3$ rugs previos).
- **BP-009:** Entrada en ATH sin Pullback ($> 150\%$ y $\ge 95\%$ del ATH sin soporte).
- **BP-010:** STALL Nominal con Polling Lento (asumir $-2\%$ con sampler de 45s a 120s).
- **BP-011:** Robbins-Monro Ciego de Winrate (perseguir $WR=65\%$ binario ignorando cola de stops).
- **BP-012:** Contaminación de Test Temporal (early stopping y selección de umbral en el mismo bloque).

---

## 4. Protocolo de Ejecución y Validación Obligatoria

Cuando recibas una tarea o propongas una modificación en este repositorio:

1. **Lectura de Estado:** Consulta [`STATUS.md`](./STATUS.md) y `resultados/reglas_produccion.json`.
2. **Desarrollo Guiado por Pruebas (§7 TDD):**
   - Escribe o adapta tests primero si introduces un cambio funcional.
   - Prohibido dejar tests fallando o silenciar aserciones con mocks falsos.
3. **Límite de Modificación (§8):**
   - No toques más de 3 archivos en una misma tarea sin autorización expresa.
   - No instales dependencias en `requirements.txt` o `pyproject.toml` sin justificación matemática.
4. **Suite de Verificación Determinista Obligatoria:**
   Antes de dar por concluida cualquier tarea, es obligatorio ejecutar la suite y obtener código de salida `0`:
   ```powershell
   python -m pytest tests -q
   ```
   *Debe reportar 100% de tests pasando (398+ passed).*
