<#
.SYNOPSIS
    Compila el lanzador de escritorio nativo (PumpfunCockpit.exe) desde su fuente C#.

.DESCRIPTION
    Usa csc.exe del .NET Framework 4.0 (C# 5, sin dependencias NuGet) para compilar
    scripts/PumpfunCockpitLauncher.cs en PumpfunCockpit.exe (raíz del repositorio).
    El ejecutable es un cliente WinForms que consume el contrato HTTP del Cockpit.

    Reglas del contrato que este exe respeta (Plan Maestro V2 §5/§6):
      - Lee EXCLUSIVAMENTE el bloque canónico `dashboard` de /api/bot/status.
      - Consume /api/logs?since=<id> para el stream de eventos (sin SSE en C#).
      - Datos ausentes se muestran como "N/D — sin dato" (ley anti-falacia).

.EXAMPLE
    pwsh scripts/build_cockpit_exe.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot "PumpfunCockpitLauncher.cs"
$output = Join-Path $repoRoot "PumpfunCockpit.exe"

$cscCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    throw "No se encontró csc.exe (.NET Framework 4.x). Instale el .NET Framework 4.x Developer Pack."
}
if (-not (Test-Path $source)) {
    throw "No se encontró la fuente C#: $source"
}

$cscArgs = @(
    "/nologo",
    "/target:winexe",
    "/optimize+",
    "/codepage:65001",
    "/r:System.dll",
    "/r:System.Core.dll",
    "/r:System.Windows.Forms.dll",
    "/r:System.Drawing.dll",
    "/r:System.Web.Extensions.dll",
    "/out:$output",
    $source
)

Write-Host "Compilando $source -> $output"
& $csc @cscArgs
if ($LASTEXITCODE -ne 0) {
    throw "csc.exe falló con código $LASTEXITCODE"
}

$exe = Get-Item $output
Write-Host ("OK: {0} ({1:N0} bytes, {2})" -f $exe.Name, $exe.Length, $exe.LastWriteTime)
