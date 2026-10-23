"""Fase 0 â€” Integridad de features: ``None`` != ``0``.

Contratos fijados aquÃ­ (anti-falacias, AGENTS.md Â§1):
1. Un flotante/concentraciÃ³n no medible se representa como ``None`` + fuente
   declarada, nunca como ``0.0`` ni como un valor sintetizado de otro score.
2. ``sniper_pct`` deja de presentarse como riesgo independiente: su procedencia
   (proxy del clÃºster de bundlers) queda declarada en ``sniper_source``.
3. El historial del creador desconocido es ``None`` (no ``0`` lanzamientos),
   porque ``0`` activaba ``dev_factory_flag`` de forma espuria.
4. SQLite puede almacenar NULL: el esquema y la migraciÃ³n preservan la ausencia.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector import gmgn_dossier_scanner as scanner  # noqa: E402
from detector.live_store import LiveStore  # noqa: E402

SCANNER_SRC = Path(scanner.__file__).read_text(encoding="utf-8")


class TestTop10FloatIntegrity(unittest.TestCase):
    def test_empty_holders_is_unknown_not_zero(self):
        chip = scanner.detect_bundler_and_cluster([])
        self.assertIsNone(chip["top10_float_pct"])
        self.assertIsNone(chip["top10_raw_pct"])
        self.assertIsNone(chip["estimated_bundler_pct"])
        self.assertIsNone(chip["insider_transfer_pct"])
        self.assertEqual(chip["chip_source"], "unavailable")

    def test_measured_value_matches_formula(self):
        value, source, flag = scanner.resolve_top10_float(20.0, 0.0)
        self.assertEqual(value, 20.0)
        self.assertEqual(source, "measured")
        self.assertIsNone(flag)

    def test_measured_value_excludes_contract_share(self):
        # 30% en top10, 50% bloqueado en LP => denominador flotante 50%
        value, source, _ = scanner.resolve_top10_float(30.0, 50.0)
        self.assertEqual(source, "measured")
        self.assertAlmostEqual(value, 60.0, places=2)

    def test_unreliable_denominator_returns_none(self):
        value, source, flag = scanner.resolve_top10_float(20.0, 95.0)
        self.assertIsNone(value)
        self.assertEqual(source, "unavailable")
        self.assertEqual(flag, "TOP10_DENOMINATOR_UNRELIABLE")

    def test_over_100_percent_returns_none(self):
        value, source, flag = scanner.resolve_top10_float(150.0, 0.0)
        self.assertIsNone(value)
        self.assertEqual(source, "unavailable")
        self.assertEqual(flag, "TOP10_EXCEEDS_FLOAT")

    def test_rugcheck_proxy_no_longer_synthesizes_concentration(self):
        """El proxy `score * 2.5` / `else 20.0` inventaba concentraciÃ³n."""
        self.assertNotIn("rc_score * 2.5", SCANNER_SRC)
        self.assertNotIn("else 20.0", SCANNER_SRC)
        self.assertIn("top10_source", SCANNER_SRC)
        self.assertIn("chip_source", SCANNER_SRC)

    def test_admission_vetoes_when_top10_unknown(self):
        """Fail-closed: sin concentraciÃ³n medible no hay admisiÃ³n."""
        self.assertIn("VETO_TOP10_UNVERIFIED", SCANNER_SRC)

    def test_sniper_provenance_declared(self):
        """`sniper_pct` no puede pasar por mediciÃ³n independiente del bundler."""
        self.assertIn("sniper_source", SCANNER_SRC)
        self.assertIn("sniper_is_proxy", SCANNER_SRC)


class TestCreatorHistoryIntegrity(unittest.TestCase):
    def test_unknown_history_is_none_not_zero(self):
        res = scanner.evaluate_creator_lineage_and_ceiling(
            creator_address="unknown", current_mc_usd=10_000.0, dev_stats=None
        )
        self.assertIsNone(res["total_launches"])
        self.assertIsNone(res["graduated_count"])
        self.assertIsNone(res["best_ath_mc_usd"])
        self.assertFalse(res["history_known"])
        self.assertFalse(res["dev_factory_flag"])
        self.assertFalse(res["near_dev_ceiling_flag"])

    def test_known_history_keeps_values_and_flags(self):
        res = scanner.evaluate_creator_lineage_and_ceiling(
            creator_address="dev1",
            current_mc_usd=10_000.0,
            dev_stats={
                "total_launches": 3,
                "graduated_count": 0,
                "rugs_count": 0,
                "best_ath_mc_usd": 50_000.0,
            },
        )
        self.assertEqual(res["total_launches"], 3)
        self.assertEqual(res["graduated_count"], 0)
        self.assertEqual(res["best_ath_mc_usd"], 50_000.0)
        self.assertTrue(res["history_known"])
        self.assertTrue(res["dev_factory_flag"])


def _dossier_defaults(**overrides):
    base = {
        "mint": "mint-integrity-1",
        "chain": "sol",
        "symbol": "TST",
        "name": "Test",
        "market_cap_usd": 1_000.0,
        "liquidity_usd": 100.0,
        "holder_count": 10,
        "top10_raw_pct": None,
        "top10_float_pct": None,
        "tradeable_float_pct": None,
        "dev_balance_pct": 0.0,
        "bundler_pct": None,
        "sniper_pct": None,
        "dev_total_launches": None,
        "dev_graduated_count": None,
        "dev_best_ath_mc_usd": None,
        "composite_dd_score": 50.0,
        "admitted": False,
        "verdict": "VETO",
    }
    base.update(overrides)
    return base


class TestLiveStoreNullability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "integrity.db")
        self._stores = []

    def tearDown(self):
        for store in self._stores:
            try:
                store.close()
            except Exception:
                pass
        self._tmp.cleanup()

    def _open(self, path=None):
        store = LiveStore(path or self.db)
        self._stores.append(store)
        return store

    def test_schema_accepts_null_for_unmeasured_features(self):
        store = self._open()
        cols = {r[1]: r for r in store._con.execute("PRAGMA table_info(token_dossiers)")}
        for name in ("top10_raw_pct", "top10_float_pct", "dev_total_launches",
                     "dev_graduated_count", "dev_best_ath_mc_usd"):
            self.assertIn(name, cols)
            self.assertEqual(cols[name][3], 0, f"{name} sigue siendo NOT NULL")
        for name in ("top10_source", "sniper_source", "dev_history_known"):
            self.assertIn(name, cols)

    def test_none_roundtrip_is_null_not_zero(self):
        store = self._open()
        store.save_token_dossier(_dossier_defaults())
        row = store._con.execute(
            "SELECT top10_raw_pct, top10_float_pct, dev_total_launches, "
            "dev_graduated_count, dev_best_ath_mc_usd, dev_history_known, "
            "top10_source, sniper_source FROM token_dossiers WHERE mint = ?",
            ("mint-integrity-1",),
        ).fetchone()
        self.assertIsNone(row["top10_raw_pct"])
        self.assertIsNone(row["top10_float_pct"])
        self.assertIsNone(row["dev_total_launches"])
        self.assertIsNone(row["dev_graduated_count"])
        self.assertIsNone(row["dev_best_ath_mc_usd"])
        self.assertEqual(row["dev_history_known"], 0)
        self.assertEqual(row["top10_source"], "unavailable")
        self.assertEqual(row["sniper_source"], "unavailable")

    def test_measured_values_are_persisted(self):
        store = self._open()
        store.save_token_dossier(_dossier_defaults(
            top10_raw_pct=22.5,
            top10_float_pct=25.0,
            top10_source="measured",
            dev_total_launches=7,
            dev_graduated_count=1,
            dev_best_ath_mc_usd=44_000.0,
            dev_history_known=True,
            sniper_pct=9.0,
            sniper_source="proxy:bundler_uniform_cluster",
        ))
        row = store._con.execute(
            "SELECT top10_float_pct, dev_total_launches, dev_history_known, sniper_source "
            "FROM token_dossiers WHERE mint = ?",
            ("mint-integrity-1",),
        ).fetchone()
        self.assertEqual(row["top10_float_pct"], 25.0)
        self.assertEqual(row["dev_total_launches"], 7)
        self.assertEqual(row["dev_history_known"], 1)
        self.assertEqual(row["sniper_source"], "proxy:bundler_uniform_cluster")

    def test_legacy_not_null_table_is_migrated_without_data_loss(self):
        legacy = """
        CREATE TABLE token_dossiers (
         mint TEXT PRIMARY KEY, chain TEXT NOT NULL DEFAULT 'sol', symbol TEXT NOT NULL,
         name TEXT NOT NULL, first_seen_ts REAL NOT NULL, updated_ts REAL NOT NULL,
         market_cap_usd REAL NOT NULL DEFAULT 0.0, liquidity_usd REAL NOT NULL DEFAULT 0.0,
         holder_count INTEGER NOT NULL DEFAULT 0, top10_raw_pct REAL NOT NULL DEFAULT 0.0,
         top10_float_pct REAL NOT NULL DEFAULT 0.0, dev_balance_pct REAL NOT NULL DEFAULT 0.0,
         bundler_pct REAL NOT NULL DEFAULT 0.0, sniper_pct REAL NOT NULL DEFAULT 0.0,
         bundler_flag INTEGER NOT NULL DEFAULT 0, wash_trading_flag INTEGER NOT NULL DEFAULT 0,
         avg_tx_m5_usd REAL NOT NULL DEFAULT 0.0, same_name_count INTEGER NOT NULL DEFAULT 1,
         dead_clones_count INTEGER NOT NULL DEFAULT 0, clone_factory_flag INTEGER NOT NULL DEFAULT 0,
         avatar_hash TEXT, avatar_reused_count INTEGER NOT NULL DEFAULT 1,
         creator_address TEXT, creator_funder TEXT,
         dev_total_launches INTEGER NOT NULL DEFAULT 0,
         dev_graduated_count INTEGER NOT NULL DEFAULT 0,
         dev_best_ath_mc_usd REAL NOT NULL DEFAULT 0.0,
         dev_factory_flag INTEGER NOT NULL DEFAULT 0,
         composite_dd_score REAL NOT NULL DEFAULT 0.0, admitted INTEGER NOT NULL DEFAULT 0,
         verdict TEXT NOT NULL DEFAULT 'VETO', veto_reasons_json TEXT,
         why_price_moves TEXT, actionable_playbook TEXT, dossier_json TEXT NOT NULL
        );
        """
        con = sqlite3.connect(self.db)
        con.executescript(legacy)
        con.execute(
            "INSERT INTO token_dossiers (mint, symbol, name, first_seen_ts, updated_ts, "
            "top10_float_pct, dev_total_launches, dossier_json) VALUES (?,?,?,?,?,?,?,?)",
            ("legacy-1", "LEG", "Legacy", 1.0, 2.0, 31.5, 4, "{}"),
        )
        con.commit()
        con.close()

        store = self._open()
        cols = {r[1]: r for r in store._con.execute("PRAGMA table_info(token_dossiers)")}
        self.assertEqual(cols["top10_float_pct"][3], 0)
        self.assertEqual(cols["dev_total_launches"][3], 0)
        row = store._con.execute(
            "SELECT top10_float_pct, dev_total_launches FROM token_dossiers WHERE mint = ?",
            ("legacy-1",),
        ).fetchone()
        self.assertEqual(row["top10_float_pct"], 31.5)
        self.assertEqual(row["dev_total_launches"], 4)

        # Idempotencia: reabrir no debe destruir ni duplicar.
        self._open()
        total = store._con.execute("SELECT COUNT(*) FROM token_dossiers").fetchone()[0]
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()

