#!/usr/bin/env python3
"""Consolidar skills en .agents/skills y eliminar duplicados."""
import shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

print("=== CONSOLIDANDO SKILLS EN .agents/skills ===")

# 1. Crear estructura destino
dest = ROOT / ".agents" / "skills"
quarantine = ROOT / ".agents" / "skills-quarantine"
dest.mkdir(parents=True, exist_ok=True)
quarantine.mkdir(parents=True, exist_ok=True)

# 2. Copiar desde skills/ (fuente canónica del lock) a .agents/skills/
print("Copiando desde skills/ -> .agents/skills/")
src_skills = ROOT / "skills"
if src_skills.exists():
    for item in src_skills.iterdir():
        if item.is_dir():
            shutil.copytree(item, dest / item.name, dirs_exist_ok=True)

# 3. Cuarentena: skills de ejecución financiera
exec_skills = ['gmgn-swap', 'gmgn-token-buy', 'gmgn-cooking']
for s in exec_skills:
    src = dest / s
    dst = quarantine / s
    if src.exists():
        print(f"Cuarentena: {s}")
        shutil.move(str(src), str(dst))

# 4. Borrar duplicados físicos y dejar punteros README.md
dup_dirs = [
    'skills',
    'agent/skills',
    'data/skills',
    '.devin/skills',
    '.forge/skills'
]

readme_content = """# NO EDITAR AQUÍ

Única fuente de verdad de skills: `.agents/skills/`
Validado por `scripts/agent_gate.py` (regla G4).
"""

for d in dup_dirs:
    full_path = ROOT / d
    if full_path.exists():
        print(f"Limpiando: {d}")
        shutil.rmtree(full_path, ignore_errors=True)
        full_path.mkdir(parents=True, exist_ok=True)
        (full_path / "README.md").write_text(readme_content, encoding="utf-8")

# 5. Binarios fuera del repo
print("Removiendo binarios del index de git...")
to_remove = ['archive', 'repos_opensource', 'scratch/gmgn-skills-main', '.manus']
for item in to_remove:
    full_path = ROOT / item
    if full_path.exists():
        subprocess.run(['git', 'rm', '-r', '--cached', item], cwd=ROOT, capture_output=True)
        print(f"  git rm --cached {item}")

# 6. Actualizar .gitignore
gitignore_add = """
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
"""
with (ROOT / ".gitignore").open("a", encoding="utf-8") as f:
    f.write(gitignore_add)

# 7. Mover archive a fuera del repo (backup)
archive_path = ROOT / "archive"
if archive_path.exists():
    backup_path = ROOT.parent / "_basededatos_archive"
    if backup_path.exists():
        shutil.rmtree(backup_path)
    shutil.move(str(archive_path), str(backup_path))
    print("Movido archive/ a ../_basededatos_archive")

print("\n=== DEDUPLICACIÓN COMPLETA ===")
print("Ahora ejecuta:")
print("  python scripts/build_state.py --db $PUMPSCOPE_DB")
print("  python scripts/agent_gate.py")
print("  pwsh scripts/verify.ps1")