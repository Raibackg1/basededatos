# UNICO comando de verificacion del repo.
# Todos los docs (README, AGENTS.md, SKILL.md) apuntan AQUI y a nada mas.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$fail = 0

function Step($n, $name, $cmd) {
  Write-Host "`n[$n] $name" -ForegroundColor Cyan
  Invoke-Expression $cmd
  if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL" -ForegroundColor Red; $script:fail++ }
  else { Write-Host "  OK" -ForegroundColor Green }
}

# Step 1: Estado (STATE.json fresco)
if ($env:PUMPSCOPE_DB) { Step 1 "Estado (STATE.json fresco)"  "python scripts/build_state.py --db `"$env:PUMPSCOPE_DB`"" } else { Step 1 "Estado (STATE.json fresco)"  "python scripts/build_state.py" }

# Step 2: Gate de integridad
Step 2 "Gate de integridad"          "python scripts/agent_gate.py"

# Step 3: Guard causal (anti-fuga) - run from pumpfun_detector
Step 3 "Guard causal (anti-fuga)"    "python -m pytest pumpfun_detector/tests/test_label_guard.py -q"

# Step 4: Integridad de costes
Step 4 "Integridad de costes"        "python -m pytest pumpfun_detector/tests/test_costs_integrity.py -q"

# Step 5: Suite completa (desde pumpfun_detector)
Step 5 "Suite completa"              "python -m pytest pumpfun_detector/tests -q"

# Step 6: Ciclo honesto (read-only) - desde pumpfun_detector
Step 6 "Ciclo honesto (read-only)"   "Push-Location pumpfun_detector; try { python -m cockpit.cycle } finally { Pop-Location }"

Write-Host "`n========================================"
if ($fail -gt 0) {
  Write-Host " VERIFY FALLO ($fail pasos)" -ForegroundColor Red
  Write-Host " No commitear. No reportar metricas." -ForegroundColor Red
  exit 1
}
Write-Host " VERIFY OK - lee STATE.json antes de afirmar nada" -ForegroundColor Green
Get-Content (Join-Path $root "STATE.json") | ConvertFrom-Json |
  Select-Object -ExpandProperty performance |
  Format-List winrate_pct, profit_factor_usd, pnl_usd, n_trades
exit 0