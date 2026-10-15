"""
Tests de SEGURIDAD del loop live (Ola 1).

Cubren los fallos críticos detectados en la auditoría:

- L1: un precio no positivo/no finito NO debe cerrar una posición. Antes,
  `price=0` producía pct=-100% y liquidaba la posición a ~-98% en silencio.
- L2: `stream_trades` debe RECONECTAR tras una desconexión (antes moría).
- L5: el adaptador debe acotar `_tokens` (antes crecía sin límite en 24/7).
- L11: `PaperBroker` debe acotar su historial en memoria.

Offline y determinista: la reconexión se prueba con un websockets falso inyectado.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector.paper_broker import PaperBroker
from detector.live_store import LiveStore
from detector.costs import ExecutionCostModel
from detector.pumpportal_feed import PumpPortalAdapter


class _Decision:
    def __init__(self, admitted=True, branch="MASTER_ALPHA_EXPANSION"):
        self.admitted = admitted
        self.branch = branch
        self.confidence = 0.9
        self.veto_reason = None
        self.metrics = {"market_cap": 5000.0}


def _buy(mint, i, base=1_700_000_000.0, sol=1.0, mc=30.0, v=20.0, trader=None):
    return {
        "mint": mint, "txType": "buy", "solAmount": sol, "tokensAmount": 1000.0,
        "marketCapSol": mc, "vSolInCurve": v,
        "traderPublicKey": trader or f"w{i}", "timestamp": base + i,
    }


class TestZeroPriceGuard(unittest.TestCase):
    """L1: el precio inválido no debe liquidar la posición."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = LiveStore(os.path.join(self.tmp, "safety.db"))
        self.broker = PaperBroker(
            self.store, ExecutionCostModel(),
            entry_sol=1.0, take_profit_pct=0.30, stop_loss_pct=-0.35,
            timeout_s=300.0,
        )
        self.feats = {"mint": "M", "entry_price": 1.0, "realSolReserves": 25.0}

    def test_zero_price_does_not_close(self):
        self.broker.on_signal(self.feats, _Decision(), ts=1000.0)
        self.assertIsNone(self.broker.on_price("M", 0.0, ts=1001.0))
        self.assertEqual(self.broker.open_count, 1)

    def test_negative_and_nan_price_do_not_close(self):
        self.broker.on_signal(self.feats, _Decision(), ts=1000.0)
        self.assertIsNone(self.broker.on_price("M", -5.0, ts=1001.0))
        self.assertIsNone(self.broker.on_price("M", float("nan"), ts=1002.0))
        self.assertEqual(self.broker.open_count, 1)

    def test_valid_price_still_closes_on_tp(self):
        self.broker.on_signal(self.feats, _Decision(), ts=1000.0)
        trade = self.broker.on_price("M", 1.40, ts=1010.0)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.exit_type, "tp")


class TestAdapterMemoryBound(unittest.TestCase):
    """L5: el adaptador acota `_tokens`."""

    def test_adapter_prunes_tokens_beyond_cap(self):
        feed = PumpPortalAdapter(window_s=60.0, max_tokens=10)
        for i in range(60):
            feed.ingest(_buy(f"M{i}", 0, base=1_700_000_000.0 + i))
        self.assertLessEqual(len(feed._tokens), 10)


class TestBrokerHistoryBound(unittest.TestCase):
    """L11: el historial en memoria se acota (el store persiste todo)."""

    def test_closed_history_is_capped(self):
        tmp = tempfile.mkdtemp()
        store = LiveStore(os.path.join(tmp, "cap.db"))
        broker = PaperBroker(
            store, ExecutionCostModel(), entry_sol=1.0,
            take_profit_pct=0.30, stop_loss_pct=-0.35, max_closed_history=5,
        )
        for i in range(20):
            mint = f"T{i}"
            broker.on_signal({"mint": mint, "entry_price": 1.0, "realSolReserves": 25.0},
                             _Decision(), ts=1000.0 + i)
            broker.on_price(mint, 1.40, ts=1005.0 + i)
        self.assertLessEqual(len(broker.closed_trades), 5)


class TestStreamReconnect(unittest.TestCase):
    """L2: `stream_trades` reconecta tras una desconexión en vez de morir."""

    def _run_with_fake_ws(self, script_factory, **kw):
        import types
        from detector import pumpportal_feed

        fake_ws_mod = types.ModuleType("websockets")

        class _FakeWS:
            def __init__(self):
                self.script = script_factory()
                self.i = 0

            async def send(self, _msg):
                return None

            async def recv(self):
                if self.i >= len(self.script):
                    raise ConnectionResetError("forced disconnect")
                val = self.script[self.i]
                self.i += 1
                return val

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        conns = {"n": 0}

        def _connect(url, **kwargs):
            conns["n"] += 1
            return _FakeWS()

        fake_ws_mod.connect = _connect
        saved = sys.modules.get("websockets")
        sys.modules["websockets"] = fake_ws_mod
        try:
            adapter = PumpPortalAdapter(window_s=60.0)
            result = asyncio.run(pumpportal_feed.stream_trades(adapter, **kw))
        finally:
            if saved is None:
                sys.modules.pop("websockets", None)
            else:
                sys.modules["websockets"] = saved
        return result, conns["n"]

    def test_reconnects_after_disconnect(self):
        import json
        msg = json.dumps(_buy("R2", 0))
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            # 1ª conexión: muere sin entregar datos. 2ª: entrega un trade.
            return [] if calls["n"] == 1 else [msg]

        result, conns = self._run_with_fake_ws(
            factory, max_events=1, base_backoff_s=0.0, max_backoff_s=0.0)
        self.assertEqual(result, 1)
        self.assertEqual(conns, 2)  # reconectó y siguió funcionando

    def test_max_reconnects_bounds_dead_connections(self):
        # Conexión que nunca entrega datos -> el backoff acumula y el tope corta
        # (sin esto, un servidor que acepta y corta haría girar el bucle).
        with self.assertRaises(ConnectionResetError):
            self._run_with_fake_ws(
                lambda: [], max_events=None, max_reconnects=2,
                base_backoff_s=0.0, max_backoff_s=0.0,
            )

    def test_max_events_stops_cleanly(self):
        import json
        msg = json.dumps(_buy("R3", 0))
        result, conns = self._run_with_fake_ws(lambda: [msg], max_events=1)
        self.assertEqual(result, 1)
        self.assertEqual(conns, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
