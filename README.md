# basededatos — Quantitative Memecoin Trading Research

> **⚠️ AVISO DE HONESTIDAD:** Las cifras de winrate/profit factor en documentos antiguos son **declaradas (in-sample o hardcodeadas), NO validadas fuera de muestra**. La verdad medida está en `pumpfun_detector/STATUS.md` y `STATE.json`. No uses este repo como evidencia de rentabilidad.

## Estado Actual (medido, commit `53fab57`)

| Métrica | Valor | Provenance |
|---|---|---|
| **Winrate real** | **12.55%** | [MEASURED] `custom.db` |
| **Profit Factor (USD)** | **0.269** | [MEASURED] `custom.db` |
| **PnL acumulado** | **−79,771 USD** | [MEASURED] 5,021 trades |
| **EV/trade** | **−15.89 USD** | [MEASURED] |
| **Tokens en DB** | **65,423** | [MEASURED] |
| **Resolución snapshots** | **72.24 s** | [MEASURED] > ventana decisión (10–60s) |
| **Edge demostrado** | **NO** | Gate OOS: 0/88 promovidos |
| **Live trading** | **BLOQUEADO** | Por software |

## Inicio Rápido para Agentes

```powershell
# 1. Verificar estado (ÚNICO comando)
pwsh scripts/verify.ps1

# 2. Leer la verdad antes de afirmar nada
cat STATE.json
cat pumpfun_detector/STATUS.md
```

## Arquitectura de Memoria (7 capas)

| Capa | Artefacto | Propósito |
|---|---|---|
| 1 | `STATE.json` | Verdad máquina (generado por `build_state.py`) |
| 2 | `AGENTS.md` | Contrato ≤120 líneas (BP-001…BP-012) |
| 3 | `MEMORY/decision-log.md` | ADRs append-only, handoff entre sesiones |
| 4 | `scripts/agent_gate.py` | Bloquea escritura si hay mentiras |
| 5 | `scripts/verify.ps1` | Único comando de verificación |
| 6 | `.github/workflows/audit.yml` | CI comenta PR con verdad medida |
| 7 | `MEMORY/hypotheses.md` | Cuarentena de ideas no validadas |

## Estructura del Repo (tras limpieza)

```
basededatos/
├─ README.md                    # ← ESTE ARCHIVO
├─ STATE.json                   # ← Verdad máquina (generado)
├─ AGENTS.md                    # ← Contrato único
├─ skills-lock.json             # ← Cubre 17 skills + 12 nuevas
├─ .agents/
│  ├─ skills/                   # ← ÚNICA ubicación real
│  │  ├─ gmgn-*/                #    (14 skills análisis GMGN)
│  │  └─ core-*/                #    (12 skills nuevas epistémicas)
│  └─ skills-quarantine/        # ← gmgn-swap, gmgn-token-buy, gmgn-cooking
├─ MEMORY/
│  ├─ decision-log.md           # Append-only
│  └─ hypotheses.md             # Ideas en cuarentena
├─ metrics/
│  ├─ claimed.json              # 81.1%, PF 18.93… etiquetados CLAIMED
│  └─ measured.json             # ← Generado, nunca a mano
├─ pumpfun_detector/            # Motor principal
├─ db-pattern-analyzer/         # Skill que importa detector como lib
├─ scripts/                     # build_state.py, agent_gate.py, verify.ps1, etc.
├─ tools/
├─ outputs/                     # Con front-matter: active|superseded|refuted
│  └─ INDEX.md
├─ tests/
└─ .github/workflows/audit.yml  # CI en raíz
```

## Dónde Empezar

| Qué quieres hacer | Por dónde empezar |
|---|---|
| Entender el estado real | `cat STATE.json` + `pumpfun_detector/STATUS.md` |
| Auditar un winrate sospechoso | Usar prompt **P-03** (en auditoría) |
| Minar patrones sin lookahead | Usar prompt **P-04** + skill `core-causal-guard` |
| Revisar código contra antipatrones | Usar prompt **P-05** |
| Cerrar sesión con handoff | Usar prompt **P-08** (skill `gov-decision-log`) |
| Trabajar en paralelo con otro agente | Usar prompt **P-09** + `MEMORY/locks.md` |

## Lo que NO está en este repo

- ❌ Fondos reales operativos (bloqueado por software)
- ❌ Winrate > 12.55% validado OOS
- ❌ Tokens simulados / mocks / datos sintéticos en producción
- ❌ Dependencias sin `pyproject.toml` con lock

## Referencias Canónicas

- **Rendimiento medido**: `pumpfun_detector/STATUS.md` §1–§3
- **Lista negra antipatrones**: `AGENTS.md` §3 (BP-001…BP-012)
- **Gate de integridad**: `scripts/agent_gate.py` (G1–G7)
- **Verificación única**: `scripts/verify.ps1`
- **Skills nuevas (12)**: `.agents/skills/core-*/`, `.agents/skills/data-*/`, `.agents/skills/gov-*/`