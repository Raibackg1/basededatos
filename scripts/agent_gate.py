#!/usr/bin/env python3
"""Gate de pre-escritura. Exit != 0 => el agente NO puede commitear.
Corre en pre-commit y en CI. Reglas duras, sin avisos."""
import json, re, sys, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors, warns = [], []

# ── G1: STATE.json existe, fresco y no está en stale ────────────────
sp = ROOT / "STATE.json"
if not sp.exists():
    errors.append("G1 STATE.json no existe. Ejecuta scripts/build_state.py")
else:
    st = json.loads(sp.read_text(encoding="utf-8"))
    if st.get("stale"): errors.append("G1 STATE.json stale=true")
    if st.get("edge", {}).get("demostrado") is not False:
        warns.append("G1 edge.demostrado != false: exige evidencia OOS adjunta")

# ── G2: ninguna cifra de rendimiento hardcodeada en el código ───────
BANNED = re.compile(r"(expected_wr|expected_winrate|expected_profit_factor)"
                    r"\s*[:=]\s*([0-9.]+)")
SAFE = {"detector/metrics_registry.py"}
for f in ROOT.rglob("*.py"):
    rel = f.relative_to(ROOT).as_posix()
    if rel.startswith(("archive/", "repos_opensource/", "scratch/", "pumpfun_detector/archive/")): continue
    if rel in SAFE: continue
    for m in BANNED.finditer(f.read_text(encoding="utf-8", errors="ignore")):
        errors.append(f"G2 {rel}: {m.group(0)!r} hardcodeado -> mover a "
                      f"metrics/claimed.json con provenance='claimed'")

# ── G3: rutas absolutas de máquina ──────────────────────────────────
BS = chr(92); COLON = chr(58)
ABS = re.compile(f"[A-Za-z]{COLON}{BS}{BS}|[A-Za-z]:/(?!/)|/Users/[a-z]+|C:{BS}{BS}Users")
EXCLUDE_ABS_DIRS = (".git/", ".work/", "archive/", "repos_opensource/", "scratch/",
                    "node_modules/", "cockpit_desktop/node_modules/",
                    "pumpfun_detector/.work/", "pumpfun_detector/.pytest_cache/",
                    "pumpfun_detector/archive/", "pumpfun_detector/docs/",
                    "pumpfun_detector/scratch/",
                    # Artefactos generados (gitignored, ya excluidos en G5): el snapshot
                    # guarda la ruta RESUELTA de la DB como provenance, no como fuente.
                    "pumpfun_detector/cockpit/runtime/",
                    ".zencoder/", ".zcode/", ".windsurf/", ".vibe/", ".trae/", ".tinycloud/",
                    ".terramind/", ".tabnine/", ".rovodev/", ".roo/", ".reasonix/", ".qwen/",
                    ".qoder/", ".posit/", ".pochi/", ".openhands/", ".ona/", ".neovate/",
                    ".mux/", ".moxby/", ".minimax/", ".mcpjam/", ".lingma/", ".kode/",
                    ".kiro/", ".kimchi/", ".junie/", ".jazz/", ".inferencesh/", ".iflow/",
                    ".hermes/", ".grok/", ".goose/", ".fx/", ".crush/", ".cortex/",
                    ".continue/", ".commandcode/", ".codestudio/", ".codemaker/", ".codebuddy/",
                    ".codeartsdoer/", ".claude/", ".bob/", ".autohand/", ".augment/",
                    ".aider-desk/", ".adal/", ".minimax/", ".manus/")
for f in list(ROOT.rglob("*.py"))+list(ROOT.rglob("*.md"))+list(ROOT.rglob("*.json")):
    rel = f.relative_to(ROOT).as_posix()
    if rel.startswith(("archive/", "repos_opensource/", "STATE.json", "pumpfun_detector/STATUS.md")): continue
    if any(rel.startswith(d) for d in EXCLUDE_ABS_DIRS): continue
    for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if ABS.search(line):
            errors.append(f"G3 {rel}:{i} ruta absoluta -> usar os.environ")
            break

# ── G4: skills duplicadas ───────────────────────────────────────────
reg = {}
EXCLUDE_DIRS = ("archive/", "repos_opensource/", "scratch/", ".git/", ".work/",
                ".zencoder/", ".zcode/", ".windsurf/", ".vibe/", ".trae/", ".tinycloud/",
                ".terramind/", ".tabnine/", ".rovodev/", ".roo/", ".reasonix/", ".qwen/",
                ".qoder/", ".posit/", ".pochi/", ".openhands/", ".ona/", ".neovate/",
                ".mux/", ".moxby/", ".minimax/", ".mcpjam/", ".lingma/", ".kode/",
                ".kiro/", ".kimchi/", ".junie/", ".jazz/", ".inferencesh/", ".iflow/",
                ".hermes/", ".grok/", ".goose/", ".fx/", ".crush/", ".cortex/",
                ".continue/", ".commandcode/", ".codestudio/", ".codemaker/", ".codebuddy/",
                ".codeartsdoer/", ".claude/", ".bob/", ".autohand/", ".augment/",
                ".aider-desk/", ".adal/")
for f in ROOT.rglob("SKILL.md"):
    rel = f.relative_to(ROOT).as_posix()
    if any(rel.startswith(d) for d in EXCLUDE_DIRS): continue
    name = f.parent.name
    reg.setdefault(name, []).append(rel)
for name, paths in reg.items():
    if len(paths) > 1:
        errors.append(f"G4 skill '{name}' duplicada en {len(paths)} rutas: {paths}")

# ── G5: binarios versionados ────────────────────────────────────────
EXCLUDE_BIN_DIRS = (".git/", ".work/", "archive/", "repos_opensource/", "scratch/",
                    ".pytest_cache/", ".manus/", "pumpfun_detector/.work/",
                    "pumpfun_detector/cockpit/runtime/")
EXCLUDE_BIN_FILES = {"custom.db", "demo_pumpfun.db", "live_demo.db", "live_decisions_demo.db",
                     "cockpit_forward.db", "annotations.db", "live_paper.db"}
for f in ROOT.rglob("*"):
    if f.suffix.lower() in {".zip", ".gz", ".db", ".sqlite", ".7z", ".rar"} and f.is_file():
        rel = f.relative_to(ROOT).as_posix()
        if any(rel.startswith(d) for d in EXCLUDE_BIN_DIRS): continue
        if f.name in EXCLUDE_BIN_FILES: continue
        errors.append(f"G5 binario en git: {rel} -> release/LFS")

# ── G6: skills-lock cubre TODO el registro ──────────────────────────
lock = ROOT / "skills-lock.json"
if lock.exists():
    locked = set(json.loads(lock.read_text(encoding="utf-8")).get("skills", {}))
    missing = set(reg) - locked
    if missing: errors.append(f"G6 sin entrada en skills-lock.json: {sorted(missing)}")

# ── G7: STATUS.md no fue editado a mano en este commit ──────────────
try:
    diff = subprocess.check_output(["git","diff","--cached","--name-only"], text=True)
    if "pumpfun_detector/STATUS.md" in diff:
        errors.append("G7 STATUS.md modificado manualmente. Solo build_state.py "
                      "+ re-medición pueden actualizarlo.")
except Exception:
    pass

for w in warns: print(f"WARN  {w}")
for e in errors: print(f"FAIL  {e}", file=sys.stderr)
print(f"\n[gate] {len(errors)} bloqueos · {len(warns)} avisos")
sys.exit(1 if errors else 0)