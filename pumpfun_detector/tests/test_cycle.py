"""Tests del ciclo automático (recolectar -> snapshot -> barrido -> cycle.json)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cockpit.cycle import run_cycle


def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE Token(mint TEXT PRIMARY KEY, firstMarketCap REAL,
                           firstLiquidity REAL, marketCap REAL, rise50At TEXT);
        CREATE TABLE PaperPosition(mint TEXT, pnlPct REAL, status TEXT);
    """)
    for i in range(6):
        con.execute("INSERT INTO Token VALUES(?,?,?,?,?)",
                    (f"T{i}", 3000.0, 1000.0, 6000.0, "2026-01-01" if i < 3 else None))
    con.execute("INSERT INTO PaperPosition VALUES(?,?,?)", ("T0", 50.0, "CLOSED"))
    con.execute("INSERT INTO PaperPosition VALUES(?,?,?)", ("T1", -30.0, "CLOSED"))
    con.commit()
    con.close()


class TestCycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        _make_db(self.db)
        self.out = os.path.join(self.tmp, "runtime")
        self.cycle_path = os.path.join(self.out, "cycle.json")

    def test_cycle_writes_snapshot_and_cycle_json(self):
        cycle = run_cycle(self.db, mapping=None, out_dir=self.out,
                          sample=100, cycle_path=self.cycle_path)
        # snapshot + cycle.json
        self.assertTrue(os.path.exists(os.path.join(self.out, "snapshot.json")))
        self.assertTrue(os.path.exists(self.cycle_path))
        with open(self.cycle_path, encoding="utf-8") as fh:
            disk = json.load(fh)
        self.assertEqual(disk["universe"]["n_tokens"], 6)
        self.assertIn("sweep", cycle)
        self.assertIn("generated_at", cycle)

    def test_cycle_sweep_is_fail_closed_on_tiny_sample(self):
        # Con 6 tokens el barrido NO puede promover nada: debe reportar ok=False
        # con un motivo explícito (nunca un falso positivo).
        cycle = run_cycle(self.db, mapping=None, out_dir=self.out,
                          sample=100, cycle_path=self.cycle_path)
        s = cycle["sweep"]
        self.assertFalse(s["ok"])
        self.assertTrue(s.get("error"))
        self.assertNotIn("promoted", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
