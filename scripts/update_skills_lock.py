import hashlib, json
from pathlib import Path

ROOT = Path(os.environ.get("PUMPSCOPE_ROOT", "."))

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

# Read existing lock
with (ROOT / 'skills-lock.json').open(encoding='utf-8') as f:
    lock = json.load(f)

# Update skill paths and add missing
skills_dir = ROOT / '.agents' / 'skills'
quarantine_dir = ROOT / '.agents' / 'skills-quarantine'

# Update existing skills
for name, info in lock['skills'].items():
    if name in ['gmgn-swap', 'gmgn-token-buy', 'gmgn-cooking']:
        new_path = quarantine_dir / name / 'SKILL.md'
        info['quarantine'] = True
    else:
        new_path = skills_dir / name / 'SKILL.md'
        info['quarantine'] = False
    
    if new_path.exists():
        info['skillPath'] = new_path.relative_to(ROOT).as_posix()
        info['computedHash'] = sha256(new_path)
        print('Updated {}: {} -> {}'.format(name, info['skillPath'], info['computedHash'][:12]))
    else:
        print('MISSING: {} at {}'.format(name, new_path))

# Add missing skills
missing_skills = {
    'db-pattern-analyzer': {
        'source': 'local',
        'sourceType': 'local',
        'skillPath': '.agents/skills/db-pattern-analyzer/SKILL.md',
        'category': 'data',
        'quarantine': False
    },
    'db-pattern-optimizer': {
        'source': 'local',
        'sourceType': 'local',
        'skillPath': '.agents/skills/db-pattern-optimizer/SKILL.md',
        'category': 'data',
        'quarantine': False
    },
    'honest-edge': {
        'source': 'local',
        'sourceType': 'local',
        'skillPath': '.agents/skills/honest-edge/SKILL.md',
        'category': 'core',
        'quarantine': False
    }
}

for name, info in missing_skills.items():
    new_path = skills_dir / name / 'SKILL.md'
    if new_path.exists():
        info['computedHash'] = sha256(new_path)
        lock['skills'][name] = info
        print('Added {}: {} -> {}'.format(name, info['skillPath'], info['computedHash'][:12]))
    else:
        print('MISSING skill to add: {} at {}'.format(name, new_path))

# Update version
lock['version'] = 2
lock['registry'] = '.agents/skills'
lock['quarantine'] = '.agents/skills-quarantine'

# Write back
with (ROOT / 'skills-lock.json').open('w', encoding='utf-8') as f:
    json.dump(lock, f, indent=2, ensure_ascii=False)

print('skills-lock.json updated')