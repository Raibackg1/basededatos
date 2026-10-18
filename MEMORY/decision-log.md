# Decision Log

Append-only ADRs. Una entrada por tarea completada.

## 2026-10-15 19:06:19 · test decision
- **agente**: test_agent
- **commit**: 53fab57
- **archivos** (≤3): test_file.py
- **decisión**: test decision logged
- **evidencia**: test evidence
- **cifras tocadas**: ninguna
- **rechazado**: ninguno
- **pendiente**: ninguno
- **status**: OK
- **verify**: exit 0 · 1 passed · 0 failed

## 2026-10-15 20:18:00 · Construcción e integración de GMGNDossierScanner, Dossier DB, Fail-Closed ML y Cockpit Annotation Studio
- **agente**: antigravity
- **commit**: HEAD
- **archivos**: `pumpfun_detector/detector/gmgn_dossier_scanner.py`, `pumpfun_detector/detector/ai_quant_model.py`, `pumpfun_detector/detector/gmgn_bridge.py`, `pumpfun_detector/detector/live_store.py`, `pumpfun_detector/detector/manual_annotation.py`, `pumpfun_detector/detector/browser_agent.py`, `pumpfun_detector/cockpit/server.py`, `pumpfun_detector/cockpit/static/index.html`, `pumpfun_detector/tests/test_gmgn_dossier_scanner.py`
- **decisión**: Implementar paridad forense GMGN de extremo a extremo: (1) `GMGNDossierScanner` detectando Bundlers ocultos por uniformidad de saldos en el Top 10 ($\text{CV} < 0.035$) y re-base sobre flotante negociable, Wash-Trading en escalera ($<\text{\$5/tx}$ en 5m), fábricas de clones homónimos (`...4444`/`...ffff`), linaje de creador (`0/73` graduados y techo de ATH MC) y hash perceptual `dHash` de 64 bits para avatares reutilizados; (2) blindaje fail-closed en `AIQuantModel` (con split temporal 60/20/20 BP-012 y costes round-trip de `costs.py`) y `GMGNBridge` cuando `metrics_known=False`; (3) tablas SQLite `token_dossiers`, `token_snapshots_hf` y `avatar_fingerprint_index` en `LiveStore`; (4) intercepción pasiva de JSON en `BrowserAgent` y endpoints `/api/dossiers`, `/api/dossier/inspect` y `/api/annotations` conectados a la UI del Cockpit.
- **evidencia**: Validación empírica sobre payloads reales de GoPlus y DexScreener para el contrato `0x8a5d7de9b3b77f0a2fb2865cd72525f32a164444` (`币安银行`), detectando 9 billeteras no-contrato con `2.2709%` idéntico (`68.12%` bundler estimado vs `68.1%` en GMGN), `$2.44/tx` wash-trading y 15 clones `...4444`/`...ffff`, vetando la entrada con diagnóstico causal.
- **cifras tocadas**: ninguna en `STATUS.md` (se mantiene baseline empírico: 5021 cierres, WR 12.55%, PF 0.269, PnL -79771 USD, 0 reglas promovidas).
- **rechazado**: Asumir `0.0` (seguro) cuando las métricas de holders o seguridad son desconocidas (`metrics_known=False`), o usar `train_test_split` aleatorio en lugar de split temporal.
- **pendiente**: Recolectar $\ge 100$ operaciones paper forward con `metrics_known=True` y `oos_pass=True` antes de considerar promoción de reglas.
- **verify**: exit 0

## 2026-10-15 21:05:00 · Auto-Evolution Engine, Shadow Veto Ledger, OpenRouter Multi-LLM, Salidas Dinámicas y Desktop App Nativa de Windows (PumpfunCockpit.exe)
- **agente**: antigravity
- **commit**: HEAD
- **archivos**: `pumpfun_detector/detector/auto_evolution.py`, `pumpfun_detector/detector/live_store.py`, `pumpfun_detector/detector/gmgn_bridge.py`, `pumpfun_detector/detector/ai_engine.py`, `pumpfun_detector/cockpit/server.py`, `pumpfun_detector/cockpit/static/index.html`, `pumpfun_detector/tests/test_gmgn_dossier_scanner.py`, `scripts/PumpfunCockpitLauncher.cs`, `PumpfunCockpit.exe`
- **decisión**: Implementar bucle cerrado de autoaprendizaje autónomo, evaluación contrafactual y ejecución nativa en Windows sin mocks:
  1. `AutoEvolutionEngine` ([`detector/auto_evolution.py`](pumpfun_detector/detector/auto_evolution.py)):
     - Libro contrafactual en sombra (`shadow_veto_ledger` en SQLite WAL): registra cada token vetado con su MC al veto, consulta periódicamente DexScreener en vivo, calcula PnL contrafactual con fricción real (-33.49% SL, comisión de red 0.008 SOL) y clasifica objetivamente `TRUE_SAVE` (el veto evitó una pérdida) vs `FALSE_VETO` (el veto se perdió una corrida).
     - Diagnóstico forense post-mortem (`diagnose_and_learn_from_closed_trade`): al cerrarse cualquier trade paper (victoria o stop), diagnostica la causa raíz causal (`BUNDLER_DUMP`, `WASH_COLLAPSE`, `STALL_BLEED`, `DEV_CEILING`), autoajusta las reglas adaptativas (`adaptive_rules.json`), persiste eventos en SQLite `auto_evolution_log` y JSONL `autonomous_evolution.jsonl`, e inyecta etiquetas supervisadas en `AnnotationDB` para reentrenamiento continuo de `AIQuantModel`.
  2. Salidas dinámicas cuantitativas en `LivePaperTrader` ([`detector/gmgn_bridge.py`](pumpfun_detector/detector/gmgn_bridge.py)):
     - `PEAK_TRAILING_STOP_PROFIT_LOCKED`: si el precio alcanzó TP1 (+50%) y posteriormente cae $\ge 18\%$ desde su máximo histórico local (ATH), cierra asegurando ganancia residual en lugar de esperar retroceso a breakeven (+25%).
     - `STALL_TIMEOUT_EXIT`: salida forzada a los 300s si la moneda no rompe niveles.
     - Persistencia de posiciones paper en SQLite (`paper_positions_state`) para tolerancia a reinicios del servidor.
  3. Integración OpenRouter Multi-LLM y Terminal en `AIEngine` ([`detector/ai_engine.py`](pumpfun_detector/detector/ai_engine.py)):
     - Conexión nativa sin librerías externas a `openrouter.ai` (465+ modelos: Claude 3.5 Sonnet, DeepSeek R1/V3, Gemini 2.5 Flash, Llama 3.3 70B).
     - Comandos `/evolve` (diagnóstico causal y autoajuste) y `/shadow` (auditoría contrafactual de capital salvado).
  4. Aplicación de escritorio nativa para Windows (`PumpfunCockpit.exe`):
     - Compilada limpiamente en C# con `csc.exe` (`scripts/PumpfunCockpitLauncher.cs`) a un ejecutable WinForms standalone de 19.9 KB.
     - Gestor de ciclo de vida del backend (arranque/parada con 1 clic), tarjetas KPI en tiempo real (saldo paper, posiciones, true saves en sombra, auto-ajustes), botón para abrir la web UI, terminal IA integrada con OpenRouter y notificaciones en la bandeja del sistema (`NotifyIcon` balloon tooltips).
- **evidencia**: Suite de verificación completa `scripts/verify.ps1` aprobada con código de salida 0 (434 tests passing, 0 bloqueos en `agent_gate.py`, ciclo honesto `cockpit.cycle` exitoso). Ejecutable `PumpfunCockpit.exe` probado y verificado en Windows.
- **cifras tocadas**: ninguna en `STATUS.md` (baseline inmutable: 5021 cierres, WR 12.55%, PF 0.269, PnL -79771 USD, fondos reales bloqueados).
- **rechazado**: Falsear trades o usar datos sintéticos; optimizaciones sin validación contrafactual; suposiciones de SL nominal al -8%.
- **pendiente**: Dejar correr el bucle autónomo en segundo plano acumulando muestras forward para alcanzar $N \ge 100$ trades certificados.
- **status**: OK
- **verify**: exit 0


