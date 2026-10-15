# AGENTS.md — Protocolo de Agentes y Arquitectura IA-Friendly

> **Versión del Sistema:** 3.0.0 (Master Signal & Loss Learner Engine)  
> **Modo Operativo:** Producción / Forward Audit / Real Data Ground Truth  
> **Base de Datos Canónica:** `D:\hjh\pumpscope\db\custom.db` (52,169 tokens, 4,990 operaciones, 375,798 snapshots)  
> **Filtro Canónico Unificado:** `MasterSnipeSignal` (`detector/master_signal.py`)

---

## 1. Visión General para Agentes Autónomos (IA-Friendly)

Este documento define la interfaz determinista, los esquemas de intercambio de datos, los roles de agentes y los límites de seguridad para cualquier modelo de lenguaje o agente autónomo (ej. Claude, GPT, Antigravity, AutoGPT) que interactúe con el ecosistema de **Pump.fun Detector**.

El principio rector del sistema es **§3 Sin Mocks Silenciosos ni Simulaciones Complacientes**:
1. Todas las métricas se calculan sobre transacciones reales on-chain.
2. Los modelos de Stop-Loss incorporan deslizamiento real empírico de memecoins en DEX (`-56.45%` media empírica, nunca `-10%` o `-15%` nominal).
3. Todas las decisiones de entrada se canalizan a través de **UNA ÚNICA SEÑAL MAESTRA (`MasterSnipeSignal`)**, eliminando señales contradictorias o sobreoptimizadas.

```
                           ┌───────────────────────────────┐
                           │      On-Chain DEX Feed        │
                           │  (Tokens, Swaps, Snapshots)   │
                           └──────────────┬────────────────┘
                                          │
                                          ▼
                           ┌───────────────────────────────┐
                           │  Semantic Inspector & Loader  │
                           │   (detector/inspector.py)     │
                           └──────────────┬────────────────┘
                                          │
                                          ▼
                           ┌───────────────────────────────┐
                           │   Loss Post-Mortem Learner    │
                           │   (detector/loss_learner.py)  │
                           └──────────────┬────────────────┘
                                          │ Veto Rules & Shields
                                          ▼
                           ┌───────────────────────────────┐
                           │   Master Snipe Signal (SSOT)  │
                           │   (detector/master_signal.py) │
                           └──────────────┬────────────────┘
                                          │
                     ┌────────────────────┴────────────────────┐
                     ▼                                         ▼
         ┌────────────────────────┐               ┌────────────────────────┐
         │     Execution Bot      │               │   Monitoring Dashboard │
         │ estrategia_produccion  │               │      reporte.html      │
         └────────────────────────┘               └────────────────────────┘
```

---

## 2. Roles y Especializaciones de Agentes

### Agente 1: `MasterSnipeAgent` (Decisor de Ejecución)
- **Objetivo**: Evaluar tokens en tiempo real ($t < 60\text{s}$) y emitir un veredicto binario (`APPROVED` / `REJECTED`).
- **Entrada**: Vector de características extraído de la curva de enlace y transacciones del token.
- **Salida**: Objeto JSON estructurado `MasterSignalDecision`.
- **Invariante**: Si cualquier escudo de riesgo falla (`risk_veto != None`), la decisión es forzosamente rechazada con confianza 0.0.

### Agente 2: `LossPostMortemAgent` (Auditor de Fallas)
- **Objetivo**: Analizar periódicamente todas las posiciones cerradas en pérdida (`pnl_pct < 0`) y descomponerlas en patrones de fallo raíz.
- **Taxonomía de Fallas**:
  1. `DEAD_LAUNCH_TIMEOUT`: Tokens con market cap inicial $\le \$4,500$ que mueren por inanición de volumen.
  2. `CATASTROPHIC_SLIPPAGE_RUG`: Salidas por SL con pérdida $\le -35\%$ debido a evaporación de liquidez.
  3. `DEV_LIQUIDATION_DUMP`: Dev con balance inicial $> 1.5\%$ liquidando sobre compradores minoristas.
  4. `SATURATION_STALL`: Entradas tardías en bonding curve $> 85\%$ que no logran migrar a Raydium.
- **Salida**: JSON de reglas restrictivas para actualizar los escudos del `MasterSnipeSignal`.

### Agente 3: `RiskGuardianAgent` (Guardián de Capital y Calibración)
- **Objetivo**: Supervisar el drawdown acumulado, la exposición por token y el cumplimiento del protocolo.
- **Límites de Seguridad**:
  - Tamaño máximo por posición: 0.1 SOL (fijo en fase forward).
  - Veto absoluto ante más de 3 fallos consecutivos en 1 hora.
  - Veto de ejecución en vivo hasta certificar $N \ge 100$ trades forward con $WR \ge 65\%$ y $PF \ge 1.30$.

---

## 3. Esquemas de Datos Canónicos (JSON Schema)

### 3.1. Entrada de Características (`TokenFeatures`)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "TokenFeatures",
  "type": "object",
  "required": [
    "market_cap",
    "first_market_cap",
    "first_liquidity",
    "real_sol_reserves",
    "dev_balance_pct",
    "migration_progress"
  ],
  "properties": {
    "token_id": { "type": "string", "description": "Mint address del token en Solana" },
    "market_cap": { "type": "number", "description": "Market Cap actual en USD al momento de evaluación" },
    "first_market_cap": { "type": "number", "description": "Market Cap en USD en el primer swap registrado" },
    "first_liquidity": { "type": "number", "description": "Liquidez inicial en USD en la curva de enlace" },
    "real_sol_reserves": { "type": "number", "description": "Reservas reales de SOL en la bonding curve" },
    "dev_balance_pct": { "type": "number", "description": "Porcentaje de supply en poder del creador (0-100)" },
    "migration_progress": { "type": "number", "description": "Progreso de bonding curve hacia Raydium (0-100)" },
    "unique_buyers": { "type": "integer", "description": "Número de compradores únicos en los primeros 60 segundos" },
    "buyer_seller_ratio": { "type": "number", "description": "Ratio de volumen de compra sobre volumen total (0.0-1.0)" }
  }
}
```

### 3.2. Salida de Decisión (`MasterSignalDecision`)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "MasterSignalDecision",
  "type": "object",
  "required": [
    "verdict",
    "confidence",
    "branch_triggered",
    "passed_risk_checks",
    "rejection_reasons",
    "risk_score"
  ],
  "properties": {
    "verdict": { 
      "type": "string", 
      "enum": ["APPROVED", "REJECTED"], 
      "description": "Veredicto final de compra" 
    },
    "confidence": { 
      "type": "number", 
      "minimum": 0.0, 
      "maximum": 1.0, 
      "description": "Probabilidad calibrada de éxito" 
    },
    "branch_triggered": { 
      "type": ["string", "null"], 
      "enum": ["BRANCH_A_EARLY_EXPANSION", "BRANCH_B_MIDCAP_MOMENTUM", null],
      "description": "Sub-patrón estructural que validó la entrada"
    },
    "passed_risk_checks": { 
      "type": "boolean", 
      "description": "Indica si superó los 5 escudos anti-pérdidas aprendidos" 
    },
    "rejection_reasons": { 
      "type": "array", 
      "items": { "type": "string" }, 
      "description": "Lista de motivos por los que el token fue descartado" 
    },
    "risk_score": { 
      "type": "number", 
      "minimum": 0.0, 
      "maximum": 100.0, 
      "description": "Puntaje de riesgo de 0 (óptimo) a 100 (ruina)" 
    }
  }
}
```

---

## 4. Lógica de Decisión Determinista (El Filtro Único)

Todo agente debe ejecutar de forma exacta el siguiente pseudocódigo determinista:

```python
def evaluar_token(t: TokenFeatures) -> MasterSignalDecision:
    # FASE 1: ESCUDOS ANTI-PÉRDIDAS APRENDIDOS (VETO OBLIGATORIO)
    rejections = []
    
    if t.market_cap <= 4500:
        rejections.append("DEAD_LAUNCH_TIMEOUT: MC <= 4,500 USD (60.9% de pérdidas históricas)")
    if t.dev_balance_pct > 1.5:
        rejections.append("DEV_LIQUIDATION_DUMP: Dev holding > 1.5% del supply")
    if t.real_sol_reserves < 12.0:
        rejections.append("ILLIQUIDITY_SLIPPAGE: Reservas < 12 SOL (slippage medio -56.4%)")
    if t.migration_progress > 85.0:
        rejections.append("SATURATION_STALL: Progreso curva > 85% (riesgo de dump pre-migración)")

    if len(rejections) > 0:
        return MasterSignalDecision(
            verdict="REJECTED",
            confidence=0.0,
            branch_triggered=None,
            passed_risk_checks=False,
            rejection_reasons=rejections,
            risk_score=95.0
        )

    # FASE 2: DETECCIÓN DE PATRÓN ESTRUCTURAL (RAMA A O RAMA B)
    # Rama A: Expansión Temprana de Micro-Cap (WR 81.1%, PF 18.93 en datos reales)
    is_branch_a = (
        t.first_market_cap <= 4285.0 and
        t.first_liquidity <= 1725.0 and
        t.market_cap > 4500.0
    )

    # Rama B: Momentum de Volumen en Media-Cap (WR 68.4%, PF 6.12 en datos reales)
    is_branch_b = (
        t.first_market_cap > 4285.0 and
        t.market_cap >= 8000.0 and
        t.market_cap <= 30000.0 and
        t.real_sol_reserves >= 15.0 and
        t.buyer_seller_ratio >= 0.60
    )

    if is_branch_a:
        return MasterSignalDecision(
            verdict="APPROVED",
            confidence=0.81,
            branch_triggered="BRANCH_A_EARLY_EXPANSION",
            passed_risk_checks=True,
            rejection_reasons=[],
            risk_score=15.0
        )
    elif is_branch_b:
        return MasterSignalDecision(
            verdict="APPROVED",
            confidence=0.68,
            branch_triggered="BRANCH_B_MIDCAP_MOMENTUM",
            passed_risk_checks=True,
            rejection_reasons=[],
            risk_score=28.0
        )
    else:
        return MasterSignalDecision(
            verdict="REJECTED",
            confidence=0.10,
            branch_triggered=None,
            passed_risk_checks=True,
            rejection_reasons=["NO_STRUCTURAL_PATTERN_MATCH"],
            risk_score=50.0
        )
```

---

## 5. Protocolo de Ejecución de Tareas para Agentes IA

Cuando un agente recibe una tarea relacionada con este proyecto:

1. **Lectura de Estado Actual**:
   - Inspeccionar `resultados/reglas_produccion.json` para obtener los umbrales numéricos activos.
   - Revisar `resultados/reporte.html` para auditar el desempeño del último ciclo de forward-testing.
2. **Validación de Código**:
   - Todo cambio en Python debe someterse a la suite de tests:
     ```powershell
     python -m unittest discover -s tests -v
     ```
   - Código de salida obligatorio: `0`.
3. **Restricción de Modificación (§8)**:
   - No añadir dependencias en `requirements.txt` ni `pyproject.toml` sin justificación matemática y consentimiento explícito.
   - Mantener el diseño desacoplado: `MasterSnipeSignal` es el único punto de contacto para señales de trading.
4. **Registro de Pérdidas**:
   - Todo nuevo lote de operaciones ejecutadas debe procesarse por `LossPostMortemLearner.analyze_failures()` antes de reclasificar cualquier parámetro.
