"""
Pruebas Unitarias para el Optimizador de Base de Datos y el Identificador de Patrones.
Ejecuta: python -m unittest tests/test_optimizer_and_patterns.py
"""
import os
import sqlite3
import tempfile
import unittest
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.db_optimizer import DatabaseEngineOptimizer, IndexRecommendation
from detector.pattern_identifier import PatternIdentifier, IdentifiedArchetype, PriceDynamicsFingerprint


class TestOptimizerAndPatterns(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_sample.db")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE tokens (
                token_id TEXT PRIMARY KEY,
                symbol TEXT,
                market_cap REAL,
                created_at INTEGER
            );
        """)
        cur.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                price REAL,
                volume REAL,
                timestamp INTEGER,
                FOREIGN KEY (token_id) REFERENCES tokens(token_id)
            );
        """)
        # Insertar filas de prueba
        for i in range(150):
            cur.execute("INSERT INTO tokens VALUES (?, ?, ?, ?)", (f"tok_{i}", f"SYM{i}", 5000.0 + i*10, 1000 + i))
            for j in range(5):
                cur.execute("INSERT INTO trades (token_id, price, volume, timestamp) VALUES (?, ?, ?, ?)",
                            (f"tok_{i}", 1.0 + j*0.1, 10.0, 1000 + i*10 + j))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_database_engine_optimizer_analysis_and_migration(self):
        optimizer = DatabaseEngineOptimizer(self.db_path)
        report = optimizer.analyze()

        self.assertGreater(report.total_size_mb, 0.0)
        self.assertEqual(report.integrity_status, "ok")
        self.assertIn("tokens", report.tables)
        self.assertIn("trades", report.tables)
        self.assertEqual(report.tables["tokens"]["row_count"], 150)
        self.assertEqual(report.tables["trades"]["row_count"], 750)

        # Debería detectar que token_id en trades es una FK sin índice
        unindexed_fk_cols = [u["column"] for u in report.unindexed_foreign_keys]
        self.assertIn("token_id", unindexed_fk_cols)

        # Debería recomendar índice
        rec_tables = [r.table for r in report.recommendations]
        self.assertIn("trades", rec_tables)

        # Generar script de migración SQL
        sql = optimizer.generate_sql_migration(report)
        self.assertIn("PRAGMA journal_mode = WAL;", sql)
        self.assertIn("PRAGMA synchronous = NORMAL;", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS", sql)
        self.assertIn("ANALYZE;", sql)

        # Aplicar optimizaciones
        res = optimizer.apply_optimizations(report)
        self.assertTrue(res["success"])
        self.assertGreater(len(res["applied_indexes"]), 0)

        # Verificar que el índice se creó físicamente en SQLite
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        idx_list = cur.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()
        idx_names = [r[0] for r in idx_list]
        self.assertTrue(any("trades" in name for name in idx_names))
        conn.close()

    def test_pattern_identifier_archetypes(self):
        # 1. Early Expansion Breakout
        feat_win = {
            "market_cap": 6000.0,
            "first_market_cap": 3500.0,
            "first_liquidity": 1200.0,
            "dev_balance_pct": 0.4,
            "real_sol_reserves": 28.0,
        }
        res_win = PatternIdentifier.identify_token_pattern(feat_win)
        self.assertEqual(res_win.name, "MICRO_CAP_BREAKOUT")
        self.assertEqual(res_win.category, "WIN_ALPHA")
        self.assertEqual(res_win.action_recommendation, "ENTER_AGGRESSIVE")
        self.assertGreater(res_win.expected_winrate, 0.70)

        # 2. Catastrophic Slippage Trap
        feat_slip = {
            "market_cap": 8000.0,
            "real_sol_reserves": 6.5,  # < 12 SOL
        }
        res_slip = PatternIdentifier.identify_token_pattern(feat_slip)
        self.assertEqual(res_slip.name, "CATASTROPHIC_SLIPPAGE_TRAP")
        self.assertEqual(res_slip.category, "LOSS_TOXIC")
        self.assertEqual(res_slip.action_recommendation, "VETO_AVOID")
        self.assertTrue(any("BP-001_ILLIQUID_RESERVES" in ap for ap in res_slip.anti_patterns_detected))

        # 3. Dev Dump Risk
        feat_dump = {
            "market_cap": 10000.0,
            "dev_balance_pct": 3.2,  # > 1.5%
        }
        res_dump = PatternIdentifier.identify_token_pattern(feat_dump)
        self.assertEqual(res_dump.name, "DEV_LIQUIDATION_DUMP")
        self.assertEqual(res_dump.action_recommendation, "VETO_AVOID")
        self.assertTrue(any("BP-008_DEV_DUMP_RISK" in ap for ap in res_dump.anti_patterns_detected))

        # 4. Cabal Network
        feat_cabal = {
            "market_cap": 12000.0,
            "cabal_share": 0.45,  # > 30%
        }
        res_cabal = PatternIdentifier.identify_token_pattern(feat_cabal)
        self.assertEqual(res_cabal.name, "CABAL_NETWORK_TRAP")
        self.assertEqual(res_cabal.action_recommendation, "VETO_AVOID")

    def test_price_fingerprint_flash_pnd(self):
        # Simular flash pump and dump: sube 200% en 60s y cae 80% en los siguientes 60s
        ts = np.array([0, 30, 60, 90, 120])
        px = np.array([1.0, 2.0, 3.0, 1.2, 0.6])
        fp = PatternIdentifier.fingerprint_price_path(ts, px)

        self.assertTrue(fp.is_flash_pump_and_dump)
        self.assertAlmostEqual(fp.max_gain_pct, 200.0, places=1)
        self.assertGreater(fp.dump_velocity_pct_per_min, 50.0)
        self.assertGreater(fp.volatility_pct, 10.0)

    def test_batch_identify(self):
        df = pd.DataFrame([
            {"token_id": "tok_1", "market_cap": 6000.0, "first_market_cap": 3000.0, "first_liquidity": 1200.0, "dev_balance_pct": 0.2},
            {"token_id": "tok_2", "market_cap": 3000.0, "first_market_cap": 3000.0, "dev_balance_pct": 0.5},
            {"token_id": "tok_3", "market_cap": 8000.0, "real_sol_reserves": 5.0},
        ])
        enriched = PatternIdentifier.batch_identify(df)
        self.assertIn("archetype_name", enriched.columns)
        self.assertIn("archetype_category", enriched.columns)
        self.assertIn("recommended_action", enriched.columns)
        self.assertIn("anti_patterns_count", enriched.columns)

        self.assertEqual(enriched.loc[0, "archetype_name"], "MICRO_CAP_BREAKOUT")
        self.assertEqual(enriched.loc[1, "archetype_name"], "DEAD_LAUNCH_TIMEOUT")
        self.assertEqual(enriched.loc[2, "archetype_name"], "CATASTROPHIC_SLIPPAGE_TRAP")


if __name__ == "__main__":
    unittest.main()
