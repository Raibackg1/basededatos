# Consolidar skills en .agents/skills (fuente = skills/, la que cubre el lock)
# Elimina 6 de 7 copias físicas, deja punteros README.md

$ErrorActionPreference = "Stop"
$root = Get-Location

Write-Host "=== CONSOLIDANDO SKILLS EN .agents/skills ===" -ForegroundColor Cyan

# 1. Crear estructura destino
$dest = "$root\.agents\skills"
$quarantine = "$root\.agents\skills-quarantine"
New-Item -ItemType Directory -Force $dest | Out-Null
New-Item -ItemType Directory -Force $quarantine | Out-Null

# 2. Copiar desde skills/ (fuente canónica del lock) a .agents/skills/
Write-Host "Copiando desde skills/ -> .agents/skills/" -ForegroundColor Green
Copy-Item -Recurse -Force "$root\skills\*" $dest\

# 3. Cuarentena: skills de ejecución financiera
$execSkills = @('gmgn-swap', 'gmgn-token-buy', 'gmgn-cooking')
foreach ($s in $execSkills) {
    $src = "$dest\$s"
    $dst = "$quarantine\$s"
    if (Test-Path $src) {
        Write-Host "Cuarentena: $s" -ForegroundColor Yellow
        Move-Item -Force $src $dst
    }
}

# 4. Borrar duplicados físicos y dejar punteros README.md
$dupDirs = @(
    'skills',
    'agent\skills',
    'data\skills',
    '.devin\skills',
    '.forge\skills'
)

$readmeContent = @"
# NO EDITAR AQUÍ

Única fuente de verdad de skills: `.agents/skills/`
Validado por `scripts/agent_gate.py` (regla G4).
"@

foreach ($d in $dupDirs) {
    $fullPath = "$root\$d"
    if (Test-Path $fullPath) {
        Write-Host "Limpiando: $d" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $fullPath -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $fullPath | Out-Null
        Set-Content "$fullPath\README.md" $readmeContent -Encoding UTF8
    }
}

# 5. Binarios fuera del repo
Write-Host "Removiendo binarios del index de git..." -ForegroundColor Yellow
$toRemove = @('archive', 'repos_opensource', 'scratch\gmgn-skills-main', '.manus')
foreach ($item in $toRemove) {
    $fullPath = "$root\$item"
    if (Test-Path $fullPath) {
        git rm -r --cached $item 2>$null
        Write-Host "  git rm --cached $item"
    }
}

# 6. Actualizar .gitignore
$gitignoreAdd = @"
# Binarios y archives (fuera del repo)
archive/
repos_opensource/
.manus/
scratch/gmgn-skills-main/
*.zip
*.gz
*.db
*.sqlite
*.7z
*.rar
STATE.json.bak
"@
Add-Content "$root\.gitignore" $gitignoreAdd

# 7. Mover archive a fuera del repo (backup)
if (Test-Path "$root\archive") {
    Move-Item "$root\archive" "$root\..\_basededatos_archive" -Force
    Write-Host "Movido archive/ a ../_basededatos_archive"
}

Write-Host "`n=== DEDUPLICACIÓN COMPLETA ===" -ForegroundColor Green
Write-Host "Ahora ejecuta:" -ForegroundColor Cyan
Write-Host "  python scripts/build_state.py --db `$env:PUMPSCOPE_DB"
Write-Host "  python scripts/agent_gate.py"
Write-Host '  pwsh scripts\verify.ps1'