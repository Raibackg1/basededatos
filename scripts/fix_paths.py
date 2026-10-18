import re
import os
from pathlib import Path

# Fix pumpfun_detector/ARCHITECTURE.md - already done
# Fix pumpfun_detector/README.md
p = Path(os.environ.get("PUMPSCOPE_DETECTOR_README", r"pumpfun_detector\README.md"))
text = p.read_text(encoding='utf-8')
text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
prefix = "file:" + "///" + "d:" + "/mejoradordebase%20de%20datos"
text = re.sub(r'\(' + re.escape(prefix) + r'/pumpfun_detector/([^)]+)\)', r'(\1)', text)
p.write_text(text, encoding='utf-8')
print('Fixed README.md')

# Fix outputs files with absolute paths
outputs_dir = Path(os.environ.get("PUMPSCOPE_OUTPUTS_DIR", r"outputs"))
for f in outputs_dir.glob('*.md'):
    text = f.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
    f.write_text(text, encoding='utf-8')
    print(f'Fixed {f.name}')

# Fix db-pattern-analyzer profile.json
p = Path(os.environ.get("PUMPSCOPE_DB_PATTERN", r"db-pattern-analyzer\resultados_demo\profile.json"))
if p.exists():
    text = p.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
    p.write_text(text, encoding='utf-8')
    print('Fixed profile.json')

# Fix outputs/db_analisis_demo/profile.json
p = Path(os.environ.get("PUMPSCOPE_DB_ANALISIS", r"outputs\db_analisis_demo\profile.json"))
if p.exists():
    text = p.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
    p.write_text(text, encoding='utf-8')
    print('Fixed outputs/db_analisis_demo/profile.json')

# Fix benchmark_opensource_real_report.json
p = Path(os.environ.get("PUMPSCOPE_BENCHMARK_REPORT", r"outputs\benchmark_opensource_real_report.json"))
if p.exists():
    text = p.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
    p.write_text(text, encoding='utf-8')
    print('Fixed benchmark_opensource_real_report.json')

# Fix informe_patrones_opensource_custom_db.json
p = Path(os.environ.get("PUMPSCOPE_PATRONES_REPORT", r"outputs\informe_patrones_opensource_custom_db.json"))
if p.exists():
    text = p.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
    p.write_text(text, encoding='utf-8')
    print('Fixed informe_patrones_opensource_custom_db.json')

# Fix profit_learning/manifest.json
p = Path(os.environ.get("PUMPSCOPE_MANIFEST", r"pumpfun_detector\outputs\profit_learning\manifest.json"))
if p.exists():
    text = p.read_text(encoding='utf-8')
    text = text.replace("D:" + "\\hjh\\pumpscope\\db\\custom.db", "$PUMPSCOPE_DB")
    p.write_text(text, encoding='utf-8')
    print('Fixed profit_learning/manifest.json')

print('All fixes applied')