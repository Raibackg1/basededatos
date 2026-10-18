# Índice de Informes — basededatos

> **Regla:** Todo informe en `outputs/` DEBE tener front-matter con `status`, `fecha`, `db_hash`, `git_sha`, `data_origin`, `oos_pass`, `edge_demostrado`.
> Los informes sin front-matter no se commitean (ver `scripts/check_claims.py`).

| Status | ID | Fecha | DB Hash | Git SHA | Data Origin | OOS Pass | Edge Demostrado | Refuta a | Refutado por |
|---|---|---|---|---|---|---|---|---|---|
| **refuted** | `informe-arquitectura-completa-profit` | 2026-10-14 | — | — | in-sample | false | false | — | causal_sweep.py |
| **refuted** | `informe-completo-profits-bots` | 2026-10-14 | — | — | in-sample | false | false | — | causal_sweep.py |
| **superseded** | `informe_patrones_configuraciones_ganadoras` | 2026-10-15 | — | — | in-sample | false | false | — | causal_sweep.py |
| **active** | `p0_p1_comparison` | 2026-10-14 | — | — | in-sample | n/a | n/a | — | — |
| **active** | `p2_safe_comparison` | 2026-10-14 | — | — | in-sample | n/a | n/a | — | — |
| **active** | `plan-mejora-meta-labeler-forward` | 2026-10-14 | — | — | in-sample | n/a | n/a | — | — |
| **superseded** | `reporte-mejoras-ganancias` | 2026-10-14 | — | — | in-sample | n/a | n/a | — | causal_sweep.py |
| **active** | `auditoria-winrate-integraciones` | 2026-10-13 | — | — | in-sample | n/a | n/a | — | — |
| **active** | `plan-maestro-cockpit-v2` | 2026-10-15 | 5e2b8f1cd866 | 53fab57 | real | n/a | false | — | — |

## Leyenda de Status

| Status | Significado | Acción |
|---|---|---|
| `active` | Vigente, no refutado | Útil como referencia |
| `superseded` | Superado por análisis posterior | Mantener para historial, no citar como evidencia |
| `refuted` | **Refutado por evidencia empírica** | **NO citar**. Banner DEPRECATED obligatorio |
| `draft` | Borrador, no validado | No commitear hasta validar |

## Criterios de Refutación (auditoría 2026-10-15)

Los informes marcados `refuted` contienen **una o más** de estas cifras refutadas:

| Cifra | Origen | Por qué es falsa |
|---|---|---|
| 81.1% WR / PF 18.93 | Rama A declarada | Hardcodeado; corrida honesta = 7.81% / 0.256 |
| 96.8% WR / PF 242.61 | Antigravity | Lookahead: usa `rise50At` + `marketCap` final |
| 74.2% WR / PF 14.45 | ARCHITECTURE.md | In-sample; PF en %-points no USD |
| -56.45% slippage | loss_learner.py histórico | String literal, nunca calculado (real: -33.49%) |

## Cómo Añadir un Informe Nuevo

1. Crear el `.md` con front-matter obligatorio:
```yaml
---
id: informe-<slug>
status: active            # active | superseded | refuted | draft
fecha: 2026-10-15
db_hash: <sha256[:12]>    # desde STATE.json
git_sha: <commit>         # git rev-parse --short HEAD
data_origin: real         # real | synthetic | dry_run | forward_live
cost_model_version: <v>
cobertura_pct: <x>        # % tokens con PaperPosition real
resolucion_datos_s: <x>   # snapshot interval mean
refuta_a: []              # ids de informes que este invalida
refutado_por: []          # ids que invalidan a este
oos_pass: false
edge_demostrado: false
---
```

2. **Ninguna cifra escrita a mano**: inyéctalas desde `STATE.json` con plantillas `{{performance.winrate_pct}}`.

3. Cada número lleva etiqueta inline: `[MEASURED]`, `[CLAIMED]`, `[SYNTHETIC]`, `[IN-SAMPLE]`, `[GROSS]`, `[UNKNOWN]`.

4. Si contradice un informe existente → añadir su `id` a `refuta_a` y poner `status: refuted` en el viejo.

5. Ejecutar `python scripts/check_claims.py --strict` y `pwsh scripts/verify.ps1`.

6. Actualizar esta tabla (`outputs/INDEX.md`).

## Próximos Informes Planeados

| ID Planeado | Tema | Status | Bloqueante |
|---|---|---|---|
| `informe-causal-sweep-results` | Resultados barrido causal 88 candidatos | planned | `causal_sweep.py` output |
| `informe-walkforward-seal` | Partición sellada train/val/test/forward | planned | `core-walkforward-seal` skill |
| `informe-loss-forensics` | Clústeres de fallo medidos | planned | `data-loss-forensics` skill |
| `informe-resolution-audit` | Resolución snapshots vs ventana decisión | planned | Sampler a 12s acumulando datos |