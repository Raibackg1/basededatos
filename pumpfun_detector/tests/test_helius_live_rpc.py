import unittest

from detector.helius_live_rpc import (
    HeliusLiveRpc,
    HeliusPause,
    sanitize_error,
    subscription_request,
)


class TestHeliusLiveRpc(unittest.TestCase):
    def test_subscription_is_finalized_and_mentions_one_program(self):
        payload = subscription_request(7, "PumpProgramFixture")
        self.assertEqual(payload["method"], "logsSubscribe")
        self.assertEqual(payload["params"][0]["mentions"], ["PumpProgramFixture"])
        self.assertEqual(payload["params"][1]["commitment"], "finalized")

    def test_transaction_uses_finalized_and_returns_inner_result(self):
        calls = []

        def fake(method, params):
            calls.append((method, params))
            return {"jsonrpc": "2.0", "result": {"slot": 99}}

        rpc = HeliusLiveRpc("fake-key", request_json=fake, retry_sleep=lambda _: None)
        self.assertEqual(rpc.get_transaction("sig"), {"slot": 99})
        self.assertEqual(calls[0][0], "getTransaction")
        self.assertEqual(calls[0][1][1]["commitment"], "finalized")
        self.assertEqual(calls[0][1][1]["maxSupportedTransactionVersion"], 0)

    def test_signatures_validates_limit_and_preserves_response(self):
        seen = []

        def fake(method, params):
            seen.append((method, params))
            return {"result": [{"signature": "new", "slot": 4}, {"signature": "old", "slot": 3}]}

        rpc = HeliusLiveRpc("fake-key", request_json=fake, retry_sleep=lambda _: None)
        rows = rpc.get_signatures_for_address("program", before="cursor", limit=2)
        self.assertEqual([r["signature"] for r in rows], ["new", "old"])
        self.assertEqual(seen[0][1][1]["commitment"], "finalized")
        with self.assertRaises(ValueError):
            rpc.get_signatures_for_address("program", limit=0)
        with self.assertRaises(ValueError):
            rpc.get_signatures_for_address("program", limit=1001)

    def test_429_honors_retry_after(self):
        attempts = []
        sleeps = []

        # El transporte inyectado puede expresar HTTP 429 con el error de dominio.
        from detector.helius_live_rpc import HeliusRpcError
        def fake_domain(method, params):
            attempts.append(1)
            if len(attempts) == 1:
                exc = HeliusRpcError("429")
                exc.status = 429
                exc.retry_after = "0.25"
                raise exc
            return {"result": {"slot": 1}}

        rpc = HeliusLiveRpc("fake-key", request_json=fake_domain, retry_sleep=sleeps.append)
        self.assertEqual(rpc.get_transaction("sig"), {"slot": 1})
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 0.25)
        self.assertLess(sleeps[0], 0.32)
        self.assertEqual(len(attempts), 2)

    def test_auth_failure_pauses(self):
        from detector.helius_live_rpc import HeliusRpcError
        def fake(method, params):
            exc = HeliusRpcError("unauthorized")
            exc.status = 401
            raise exc
        rpc = HeliusLiveRpc("fake-key", request_json=fake, retry_sleep=lambda _: None)
        with self.assertRaises(HeliusPause):
            rpc.get_transaction("sig")

    def test_secret_is_redacted(self):
        key = "super-secret-key"
        text = f"POST https://mainnet.helius-rpc.com/?api-key={key} failed"
        safe = sanitize_error(text, key)
        self.assertNotIn(key, safe)
        self.assertNotIn("?api-key=", safe)

if __name__ == "__main__":
    unittest.main()
