import sqlite3, tempfile, unittest
from pathlib import Path
import pandas as pd
from detector.onchain_risk import entry_risk_labels
from detector.meta_labeler import fit_candidate
from detector.columnar_export import export_sqlite_table
from detector.paper_adapters import ReplayOrderSubmitter

class P2SafeTests(unittest.TestCase):
    def test_entry_labels_do_not_use_future_outcomes(self):
        x = entry_risk_labels({'topHolderPct': 30, 'devBalancePct': 4, 'firstLiquidity': 2, 'n_buys_60s': 9})
        self.assertTrue(x['bundle_like_flow_flag']); self.assertNotIn('pnl_sol', x)
    def test_meta_is_candidate_only_and_blocks_forbidden(self):
        df = pd.DataFrame({'created_at': range(60), 'f1': range(60), 'future_ret_5': range(60), 'y': [i%2 for i in range(60)]})
        out = fit_candidate(df, 'y', min_train=35, min_test=10)
        self.assertFalse(out['production_ready']); self.assertEqual(out['status'], 'candidate_only'); self.assertNotIn('future_ret_5', out.get('features', []))
    def test_export_read_only_csv(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'x.db'; out = Path(td) / 'x.csv'
            c = sqlite3.connect(db); c.execute('create table t (id integer)'); c.execute('insert into t values (1)'); c.commit(); c.close()
            r = export_sqlite_table(str(db), 't', str(out)); self.assertTrue(r['ok']); self.assertTrue(out.exists())
    def test_replay_submitter_is_paper_only(self):
        s = ReplayOrderSubmitter(); self.assertFalse(s.live_enabled)
        with self.assertRaises(RuntimeError): ReplayOrderSubmitter(live_enabled=True).submit_order(None)

if __name__ == '__main__': unittest.main()
