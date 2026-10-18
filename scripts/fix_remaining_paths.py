import re
from pathlib import Path

# Fix .agents/skills/ files
skills_dir = Path(os.environ.get("PUMPSCOPE_SKILLS_DIR", ".agents/skills"))
for f in skills_dir.rglob('*.md'):
    text = f.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", r"$PUMPSCOPE_DB")
    f.write_text(text, encoding='utf-8')
    print('Fixed', f.relative_to(Path(os.environ.get("PUMPSCOPE_ROOT", "."))))

# Fix quarantine skills
quarantine_dir = Path(os.environ.get("PUMPSCOPE_SKILLS_QUARANTINE", ".agents/skills-quarantine"))
for f in quarantine_dir.rglob('*.md'):
    text = f.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", r"$PUMPSCOPE_DB")
    f.write_text(text, encoding='utf-8')
    print('Fixed', f.relative_to(Path(os.environ.get("PUMPSCOPE_ROOT", "."))))

# Fix outputs files
outputs_dir = Path(os.environ.get("PUMPSCOPE_OUTPUTS_DIR", "outputs"))
for f in outputs_dir.glob('*.md'):
    text = f.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", r"$PUMPSCOPE_DB")
    f.write_text(text, encoding='utf-8')
    print('Fixed', f.relative_to(Path(os.environ.get("PUMPSCOPE_ROOT", "."))))

# Fix JSON files
for f in outputs_dir.glob('*.json'):
    text = f.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", r"$PUMPSCOPE_DB")
    f.write_text(text, encoding='utf-8')
    print('Fixed', f.relative_to(Path(os.environ.get("PUMPSCOPE_ROOT", "."))))

print('All done')