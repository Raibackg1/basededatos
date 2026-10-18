#!/usr/bin/env python3
"""Valida que skills-lock.json cubra TODO el registro de skills y que los hashes coincidan."""
import json, hashlib, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors, warns = [], []

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

# 1. Cargar lock
lock_path = ROOT / "skills-lock.json"
if not lock_path.exists():
    errors.append("skills-lock.json no existe")
    sys.exit(1)

lock = json.loads(lock_path.read_text(encoding="utf-8"))
locked_skills = lock.get("skills", {})

# 2. Escanear SKILL.md en el árbol (excluyendo archive, repos_opensource, scratch)
reg = {}
for f in ROOT.rglob("SKILL.md"):
    rel = f.relative_to(ROOT).as_posix()
    if rel.startswith(("archive/", "repos_opensource/", "scratch/")):
        continue
    name = f.parent.name
    reg.setdefault(name, []).append((rel, sha256(f)))

# 3. Verificar duplicados (G4)
for name, paths in reg.items():
    if len(paths) > 1:
        errors.append(f"G4 skill '{name}' duplicada en {len(paths)} rutas: {[p[0] for p in paths]}")

# 4. Verificar que cada skill en lock existe y hash coincide
for name, info in locked_skills.items():
    skill_path = ROOT / info["skillPath"]
    if not skill_path.exists():
        errors.append(f"G6 skill '{name}' en lock pero no existe en {info['skillPath']}")
        continue
    actual_hash = sha256(skill_path)
    if actual_hash != info.get("computedHash"):
        errors.append(f"G6 skill '{name}' hash diverge: lock={info['computedHash'][:12]}... actual={actual_hash[:12]}...")

# 5. Verificar que cada skill física está en el lock
for name, paths in reg.items():
    if name not in locked_skills:
        # Solo la ruta canónica (.agents/skills/) debería estar en el lock
        canonical = [p for p in paths if p[0].startswith(".agents/skills/")]
        if canonical:
            errors.append(f"G6 skill '{name}' existe en .agents/skills/ pero NO está en skills-lock.json")
        else:
            warns.append(f"skill '{name}' existe solo en ubicaciones no canónicas: {[p[0] for p in paths]}")

# 6. Verificar skills en cuarentena
quarantine_dir = ROOT / ".agents" / "skills-quarantine"
if quarantine_dir.exists():
    for f in quarantine_dir.iterdir():
        if f.is_dir() and (f / "SKILL.md").exists():
            name = f.name
            if name in locked_skills:
                if not locked_skills[name].get("quarantine", False):
                    warns.append(f"skill de cuarentena '{name}' no marcada como quarantine en lock")

for w in warns:
    print(f"WARN  {w}")
for e in errors:
    print(f"FAIL  {e}", file=sys.stderr)

print(f"\n[check_skills_lock] {len(errors)} bloqueos · {len(warns)} avisos")
sys.exit(1 if errors else 0)