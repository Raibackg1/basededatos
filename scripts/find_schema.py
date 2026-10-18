import re
from pathlib import Path

ROOT = Path(os.environ.get("PUMPSCOPE_DETECTOR_DIR", "pumpfun_detector"))
for f in ROOT.rglob('*.py'):
    try:
        text = f.read_text(encoding='utf-8', errors='ignore')
        if 'class Token' in text or 'class PaperPosition' in text or 'class Snapshot' in text or 'CREATE TABLE' in text:
            rel = f.relative_to(Path(os.environ.get("PUMPSCOPE_ROOT", ".")))
            print(rel)
            for line in text.split('\n'):
                if any(kw in line for kw in ['class Token', 'class PaperPosition', 'class Snapshot', 'CREATE TABLE']):
                    print(f'  {line.strip()[:120]}')
    except:
        pass