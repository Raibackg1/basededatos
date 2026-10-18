#!/usr/bin/env python3
"""Valida que no haya cifras CLAIMED sin etiqueta en .md/.py, y que los informes tengan front-matter."""
import re, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors, warns = [], []

# Patrones de cifras que DEBEN llevar etiqueta [MEASURED]/[CLAIMED]/[SYNTHETIC]/[IN-SAMPLE]/[GROSS]/[UNKNOWN]
METRIC_PATTERNS = [
    (r"\b\d+\.\d+%\s*(?:WR|winrate)\b", "winrate"),
    (r"\bPF\s*[\d.]+\b", "profit factor"),
    (r"\bPnL\s*[-+]?\d[\d,]*\.?\d*\s*USD\b", "pnl"),
    (r"\bEV\s*[-+]?\d[\d,]*\.?\d*\s*(?:USD|/trade)\b", "ev"),
    (r"\bprofit factor\s*[\d.]+\b", "profit factor"),
]

# Cifras en lista negra que NUNCA deben aparecer sin etiqueta [CLAIMED — REFUTADO]
BLACKLIST = [
    (r"81\.1%\s*(?:WR|winrate)", "81.1% WR"),
    (r"96\.8%\s*(?:WR|winrate)", "96.8% WR"),
    (r"74\.2%\s*(?:WR|winrate)", "74.2% WR"),
    (r"59\.6%\s*(?:WR|winrate)", "59.6% WR"),
    (r"PF\s*18\.93", "PF 18.93"),
    (r"PF\s*242\.61", "PF 242.61"),
    (r"PF\s*14\.45", "PF 14.45"),
    (r"PF\s*2\.69", "PF 2.69"),
    (r"-56\.45%", "-56.45% slippage"),
]

def has_label_nearby(text: str, pos: int, window: int = 80) -> bool:
    """Verifica si hay etiqueta de provenance en ventana ±window chars."""
    start = max(0, pos - window)
    end = min(len(text), pos + window)
    snippet = text[start:end]
    return bool(re.search(r"\[(MEASURED|CLAIMED|SYNTHETIC|IN-SAMPLE|GROSS|UNKNOWN)\]", snippet))

def check_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return
    
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith(("archive/", "repos_opensource/", "scratch/", ".git/")):
        return
    
    # Verificar lista negra
    for pattern, desc in BLACKLIST:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if not has_label_nearby(text, m.start()):
                errors.append(f"{rel}:{text[:m.start()].count(chr(10))+1} → cifra blacklist '{desc}' sin etiqueta [CLAIMED — REFUTADO]")
    
    # Verificar front-matter en outputs/*.md
    if rel.startswith("outputs/") and path.suffix == ".md":
        if not text.startswith("---"):
            errors.append(f"{rel}: FALTA front-matter YAML obligatorio (status, fecha, db_hash, git_sha, data_origin, oos_pass, edge_demostrado)")
        else:
            # Verificar campos obligatorios
            required = ["status", "fecha", "db_hash", "git_sha", "data_origin", "oos_pass", "edge_demostrado"]
            for field in required:
                if f"{field}:" not in text[:500]:
                    warns.append(f"{rel}: front-matter falta campo '{field}'")

def main():
    strict = "--strict" in sys.argv
    
    for f in ROOT.rglob("*.md"):
        check_file(f)
    for f in ROOT.rglob("*.py"):
        check_file(f)
    
    for w in warns:
        print(f"WARN  {w}")
    for e in errors:
        print(f"FAIL  {e}", file=sys.stderr)
    
    print(f"\n[check_claims] {len(errors)} errores · {len(warns)} avisos")
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()