from __future__ import annotations
import json, tempfile
from pathlib import Path
import pandas as pd
from detector.onchain_risk import entry_risk_labels
from detector.meta_labeler import fit_candidate
from detector.columnar_export import export_sqlite_table

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'demo_pumpfun.db'
OUT = ROOT.parent / 'outputs' / 'p2_safe_comparison.md'

def main():
    df = pd.DataFrame({'created_at': range(120), 'flow': [i % 7 for i in range(120)], 'liq': [10 + i % 5 for i in range(120)], 'future_ret_5': [1] * 120, 'label': [int(i % 3 == 0) for i in range(120)]})
    meta = fit_candidate(df, 'label', min_train=80, min_test=20)
    risk = entry_risk_labels({'topHolderPct': 31, 'devBalancePct': 5, 'firstLiquidity': 2, 'n_buys_60s': 9, 'n_sells_60s': 0})
    with tempfile.TemporaryDirectory() as td:
        csv = Path(td) / 'tokens.csv'
        export = export_sqlite_table(str(DB), 'tokens', str(csv), limit=100)
    text = ['# Comparación P2 segura', '', '- La base original no se modifica.', '- Todo modelo queda `candidate_only`.', '', '## Resultados', '', f"- Etiquetas on-chain generadas: `{len(risk)}`; bundle-like detectado: `{risk['bundle_like_flow_flag']}`.", f"- Meta-labeler: estado `{meta['status']}`, production_ready=`{meta['production_ready']}`, train={meta.get('n_train', 0)}, test={meta.get('n_test', 0)}, OOS AUC={meta.get('oos_auc')}." , f"- Exportación CSV de control: `{'OK' if export.get('ok') else export.get('reason')}`.", '', '## Interpretación', '', 'Las etiquetas amplían la observabilidad pero no prueban alpha. El meta-labeler demuestra un flujo temporal reproducible y permanece bloqueado para producción. DuckDB/Parquet depende de librerías opcionales; no se instalaron dependencias ni se alteró el entorno.', '']
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text('\n'.join(text), encoding='utf-8')
    print(json.dumps({'report': str(OUT), 'meta': {k:v for k,v in meta.items() if k != 'model'}, 'export': export}, default=str, ensure_ascii=False))
if __name__ == '__main__': main()
