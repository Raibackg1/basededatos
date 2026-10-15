"""Tests ADVERSARIALES de anti-fuga (lookahead leakage).

Ataques que un pipeline ingenuo dejaría pasar y que el guardia debe bloquear:
- nombres de features post-entrada con mayúsculas/minúsculas rotas ("Win_real")
- prefijos difusos ("pnl_ratio", "fut_ret_1m", "mfe_norm")
- timestamps available_at_ms posteriores a la entrada
- contaminación train/test en partición temporal (purge_gap)
- consistencia incremental de features (lookahead de Freqtrade)
- separación de procedencia live vs synthetic en el BranchGate
"""
import json
import unittest

import pandas as pd

from detector.data_integrity import (
    FeatureAvailabilityError,
    FeatureAvailabilityGuard,
    build_provenance,
)


class TestSneakyLeakNames(unittest.TestCase):
    """Nombres de fuga disfrazados deben ser rechazados igual."""

    ATTACKS = (
        "Win_real",              # mayúscula inicial rota
        "WIN_TP_SL",             # todo mayúsculas
        "Win",                   # exacta post-entrada
        "pnl_pct",               # exacta
        "pnl_ratio",             # prefijo pnl_
        "future_ret_5m",         # prefijo future_
        "fut_ret_1m",            # prefijo fut_ret_
        "mfe_norm",              # prefijo mfe_
        "mae_pct",               # prefijo mae_
        "tpsl_grid",             # prefijo tpsl_
        "exit_price_est",        # prefijo exit_
        "hold_s",                # exacta hold_s
        "exit_reason",           # exacta
    )

    def test_validate_rejects_each_attack(self):
        for name in self.ATTACKS:
            with self.subTest(name=name):
                with self.assertRaises(FeatureAvailabilityError):
                    FeatureAvailabilityGuard.validate({"market_cap": 4600.0, name: 1.0})

    def test_clean_features_pass(self):
        clean = {"market_cap": 4600.0, "real_sol_reserves": 20.0,
                 "unique_buyers": 42, "buyer_seller_ratio": 0.7}
        out = FeatureAvailabilityGuard.validate(clean)
        self.assertEqual(out["market_cap"], 4600.0)

    def test_filter_frame_drops_leak_columns(self):
        df = pd.DataFrame({
            "market_cap": [4600.0, 5000.0],
            "Win_real": [1, 0],
            "pnl_pct": [12.0, -30.0],
            "mfe": [10.0, 20.0],
            "mae": [-5.0, -8.0],
            "exit_reason": ["tp", "sl"],
            "entry_time": [100.0, 200.0],
            "available_at_ms": [99_000.0, 210_000.0],
        })
        out = FeatureAvailabilityGuard.filter_frame(df)
        for leak in ("Win_real", "pnl_pct", "mfe", "mae", "exit_reason"):
            self.assertNotIn(leak, out.columns, f"columna leak {leak} sobrevivió al filtro")
        self.assertIn("market_cap", out.columns)
        # Fila con available_at posterior a entry debe caer; la otra se queda.
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["market_cap"], 4600.0)


class TestTimestampAttacks(unittest.TestCase):
    def test_available_after_entry_rejected(self):
        feats = {"market_cap": 4600.0, "available_at_ms": 200_000.0}
        with self.assertRaises(FeatureAvailabilityError):
            FeatureAvailabilityGuard.validate(feats, entry_time=100.0)

    def test_available_before_entry_accepted(self):
        feats = {"market_cap": 4600.0, "available_at_ms": 99_000.0}
        out = FeatureAvailabilityGuard.validate(feats, entry_time=100.0)
        self.assertEqual(out["market_cap"], 4600.0)

    def test_seconds_vs_ms_entry_normalization(self):
        # entry_time en segundos (epoch < 1e10), disponible_at en ms
        feats = {"market_cap": 4600.0, "available_at_ms": 99_000.0}
        out = FeatureAvailabilityGuard.validate(feats, entry_time=100.0)
        self.assertEqual(out["market_cap"], 4600.0)


class TestPurgedTemporalSplit(unittest.TestCase):
    def test_purge_gap_removes_boundary_rows(self):
        from detector.advanced_engine import temporal_train_val_test_split
        df = pd.DataFrame({"created_at": list(range(100)), "x": list(range(100))})
        train, val, test = temporal_train_val_test_split(df, purge_gap=5)
        # Train termina ANTES de n_train-gap; val empieza en n_train; test en n_val+gap
        self.assertEqual(train["created_at"].max(), 59 - 5)   # 54
        self.assertEqual(val["created_at"].min(), 60)         # 60
        self.assertEqual(val["created_at"].max(), 79)         # 79
        self.assertEqual(test["created_at"].min(), 80 + 5)    # 85
        # Sin solapamiento temporal entre bloques
        self.assertLess(train["created_at"].max(), val["created_at"].min())
        self.assertLess(val["created_at"].max(), test["created_at"].min())

    def test_purge_gap_zero_keeps_full_coverage(self):
        from detector.advanced_engine import temporal_train_val_test_split
        df = pd.DataFrame({"created_at": list(range(10)), "x": list(range(10))})
        train, val, test = temporal_train_val_test_split(df, purge_gap=0)
        self.assertEqual(len(train) + len(val) + len(test), 10)
        self.assertEqual(train["created_at"].max(), 5)   # 60% de 10 -> 6 rows (0..5)
        self.assertEqual(val["created_at"].min(), 6)

    def test_strictly_increasing_order_preserved(self):
        from detector.advanced_engine import temporal_train_val_test_split
        # Datos desordenados: el split debe ordenarlos por tiempo
        df = pd.DataFrame({"created_at": list(range(30))})
        df = df.sample(frac=1.0, random_state=7).reset_index(drop=True)  # shuffle
        train, val, test = temporal_train_val_test_split(df, purge_gap=2)
        # Orden temporal estricto preservado en cada bloque
        self.assertTrue((train["created_at"].diff().dropna() > 0).all())
        self.assertTrue((val["created_at"].diff().dropna() > 0).all())
        self.assertTrue((test["created_at"].diff().dropna() > 0).all())
        # Sin contaminación: train termina antes de val, val antes de test
        self.assertLess(train["created_at"].max(), val["created_at"].min())
        self.assertLess(val["created_at"].max(), test["created_at"].min())


class TestIncrementalConsistency(unittest.TestCase):
    def test_chronological_records_consistent(self):
        from detector.features import FeatureAvailabilityGuard
        recs = [{"ts": t, "mc": 100.0 + t} for t in range(10)]
        ok, diffs = FeatureAvailabilityGuard.audit_incremental_consistency(recs)
        self.assertTrue(ok, diffs)

    def test_unsorted_input_still_returns_tuple(self):
        # La función ordena antes de auditar: siempre devuelve (True, []) si no
        # hay inversión tras ordenar. Solo verificamos que no crashea.
        from detector.features import FeatureAvailabilityGuard
        recs = [{"ts": 5}, {"ts": 1}, {"ts": 3}]
        ok, diffs = FeatureAvailabilityGuard.audit_incremental_consistency(recs)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(diffs, list)


class TestProvenance(unittest.TestCase):
    def test_synthetic_origin_recorded(self):
        prov = build_provenance(None, "synthetic")
        self.assertEqual(prov["data_origin"], "synthetic")
        self.assertIsNone(prov["db_hash"])  # no inventa hash sin archivo

    def test_live_origin_with_file_hashes(self):
        import hashlib
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
            fh.write(b"pumpfun-test-bytes")
            path = fh.name
        try:
            prov = build_provenance(path, "live")
            self.assertEqual(prov["data_origin"], "live")
            self.assertEqual(prov["db_hash"], hashlib.sha256(b"pumpfun-test-bytes").hexdigest())
            self.assertEqual(prov["db_size_bytes"], os.path.getsize(path))
        finally:
            os.unlink(path)


class TestGateOriginIsolation(unittest.TestCase):
    def test_synthetic_outcomes_do_not_disable_live_branch(self):
        from detector.branch_gate import BranchGate
        from detector.live_store import LiveStore
        from tests.test_live_store import _FakeDecision
        store = LiveStore(":memory:")
        # 15 outcomes sintéticos negativos -> la rama NO debe desactivarse
        for i in range(15):
            did = store.record_decision(
                f"M_syn_{i}", {"data_origin": "synthetic"},
                _FakeDecision(True, "MASTER_ALPHA_TEST"), ts=1000.0 + i)
            store.record_outcome(did, pnl_pct=-50.0, pnl_sol=-0.5)
        gate = BranchGate(store, origin="live")
        gate.refresh()
        self.assertTrue(gate.is_enabled("MASTER_ALPHA_TEST"),
                        "synthetic no debe desactivar rama en gate live")
        # Mismo volumen en live SÍ decide
        for i in range(15):
            did = store.record_decision(
                f"M_live_{i}", {"data_origin": "live"},
                _FakeDecision(True, "MASTER_ALPHA_TEST"), ts=2000.0 + i)
            store.record_outcome(did, pnl_pct=-50.0, pnl_sol=-0.5)
        gate.refresh()
        self.assertFalse(gate.is_enabled("MASTER_ALPHA_TEST"),
                         "EV negativo live con n>=10 debe desactivar")


class TestSlippageDoesNotLieAboutFill(unittest.TestCase):
    """El slippage condicionado no debe fabricar fills: q90 > q50 por bucket."""

    def test_tail_above_median_per_bucket(self):
        from detector.costs import ConditionalSlippageModel
        m = ConditionalSlippageModel()
        for liq in (2.0, 10.0, 22.0, 50.0):
            with self.subTest(liq=liq):
                self.assertGreater(m.quantile(liq, 0.90), m.quantile(liq, 0.50))


if __name__ == "__main__":
    unittest.main()
