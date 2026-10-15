"""
Tests del runner end-to-end del loop de "ganancia real medible" (P1).

Verifican que `detector.live_runner.run_replay` orqueste correctamente
PumpPortalAdapter -> MasterSnipeSignal -> PaperBroker -> LiveStore -> net_audit,
de forma determinista y sin red.
"""
import os
import tempfile
import unittest


def _ev(mint, is_buy, sol, mc_sol, ts, trader, v_sol=10.0, v_tok=1.0e9):
    return {
        "mint": mint,
        "txType": "buy" if is_buy else "sell",
        "solAmount": sol,
        "tokensAmount": 1000.0,
        "marketCapSol": mc_sol,
        "vSolInCurve": v_sol,
        "vTokensInCurve": v_tok,
        "traderPublicKey": trader,
        "signature": f"{mint}-{trader}-{ts}",
        "timestamp": ts,
    }


class TestLiveRunner(unittest.TestCase):
    def _run(self, events, **kw):
        from detector.live_runner import run_replay

        tmp = tempfile.mkdtemp(prefix="runner_")
        db = os.path.join(tmp, "live.db")
        return run_replay(events, db_path=db, **kw), db

    def _accumulation(self, mint, base, mc_sol=60.0, n=46):
        """Fase de acumulación: 40+ compradores únicos, v_sol crece 8->30,
        mc constante -> dispara la rama C (confluencia) al alcanzar 40 holders.
        mc_sol=60 SOL * 150 = 9000 USD (en rango [4500, 50000])."""
        return [
            _ev(mint, True, 0.5, mc_sol, base + i, f"{mint}_buyer{i}",
                v_sol=8.0 + 0.5 * i)
            for i in range(n)
        ]

    def test_winner_replay_yields_positive_net_and_audit(self):
        # Acumulación que dispara rama C (entry mc=60 SOL), luego +33% -> TP.
        base = 1_700_000_000.0
        events = self._accumulation("WIN", base)
        events += [
            _ev("WIN", True, 1.0, 60.0 + 4.0 * i, base + 46 + i, f"WIN_late{i}",
                v_sol=30.0 + i) for i in range(1, 6)
        ]  # mc sube 64->80 SOL (+33% -> toca TP de +30%)
        audit, _ = self._run(events, sol_usd=150.0, entry_sol=1.0,
                             take_profit_pct=0.30, stop_loss_pct=-0.35)
        ctrl = audit["control"]
        self.assertEqual(ctrl["mints_seen"], 1)
        self.assertEqual(ctrl["positions_opened"], 1)
        self.assertGreaterEqual(ctrl["events_ingested"], len(events))
        # Al menos un trade cerrado con outcome real persistido.
        self.assertGreaterEqual(audit.get("n", 0), 1)
        self.assertGreater(audit["total_pnl_sol"], 0.0)
        self.assertGreater(audit["winrate"], 0.0)
        self.assertEqual(audit["origin"], "live")

    def test_loser_replay_closes_on_stop_loss(self):
        # Acumulación (entry mc=60 SOL), luego dump -50% -> SL.
        base = 1_800_000_000.0
        events = self._accumulation("LOSE", base)
        events += [
            _ev("LOSE", False, 1.0, 30.0, base + 46 + i, f"LOSE_seller{i}",
                v_sol=30.0 - i) for i in range(1, 6)
        ]  # mc cae a 30 SOL (-50% -> perfora SL de -35%)
        audit, _ = self._run(events, sol_usd=150.0)
        self.assertGreaterEqual(audit.get("n", 0), 1)
        self.assertLess(audit["total_pnl_sol"], 0.0)
        self.assertEqual(audit["winrate"], 0.0)

    def test_no_signal_mint_produces_no_trades(self):
        # Micro-cap muerta (3 SOL * 150 = 450 USD < 4500) -> VETO_DEAD_LAUNCH.
        base = 1_900_000_000.0
        events = [
            _ev("DEAD", True, 0.05, 3.0, base, "DEAD_dev", v_sol=2.0),
            _ev("DEAD", True, 0.05, 3.0, base + 5, "DEAD_b1", v_sol=2.1),
        ]
        audit, _ = self._run(events, sol_usd=150.0)
        self.assertEqual(audit["control"]["mints_seen"], 1)
        self.assertEqual(audit["control"]["positions_opened"], 0)
        # Vetado: no se abre posición, no hay PnL.
        self.assertEqual(audit.get("n", 0), 0)
        self.assertEqual(audit["total_pnl_sol"], 0.0)

    def test_deterministic_same_replay_same_result(self):
        base = 1_750_000_000.0
        events = self._accumulation("DET", base)
        events += [
            _ev("DET", True, 1.0, 60.0 + 4.0 * i, base + 46 + i, f"DET_late{i}",
                v_sol=30.0 + i) for i in range(1, 6)
        ]
        a1, _ = self._run(events, sol_usd=150.0)
        a2, _ = self._run(events, sol_usd=150.0)
        self.assertEqual(a1["total_pnl_sol"], a2["total_pnl_sol"])
        self.assertEqual(a1.get("n", 0), a2.get("n", 0))


if __name__ == "__main__":
    unittest.main()
