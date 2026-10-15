"""Pruebas offline del adaptador Helius; las respuestas se identifican como fixtures sintéticas."""
import csv
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from scripts.ingest_helius_history import (
    ingest,
    iter_address_transactions,
    normalize_swap,
    read_mints,
)


def fixture_tx(mint="Mint111", signature="sig-1", timestamp=1_700_000_000, side="buy"):
    """Respuesta fixture deliberadamente sintética, no datos on-chain reales."""
    token = {"mint": mint, "tokenAmount": {"amount": "1250", "decimals": 0, "uiAmount": 1250.0}}
    swap = {
        "tokenInputs": [token] if side == "sell" else [],
        "tokenOutputs": [token] if side == "buy" else [],
        "nativeInput": {"amount": "0.25", "account": "Wallet111"} if side == "buy" else None,
        "nativeOutput": {"amount": "0.25", "account": "Wallet111"} if side == "sell" else None,
    }
    return {
        "signature": signature,
        "timestamp": timestamp,
        "feePayer": "Wallet111",
        "events": {"swap": swap},
    }


class HeliusHistoryTests(unittest.TestCase):
    def test_normalizes_buy_and_sell_without_fabricating_missing_fields(self):
        buy = normalize_swap(fixture_tx(), "Mint111")
        sell = normalize_swap(fixture_tx(signature="sig-2", side="sell"), "Mint111")
        self.assertEqual(buy["side"], "buy")
        self.assertEqual(buy["sol_amount"], 0.25)
        self.assertEqual(buy["token_amount"], 1250.0)
        self.assertAlmostEqual(buy["price"], 0.0002)
        self.assertEqual(sell["side"], "sell")
        self.assertIsNone(normalize_swap({"signature": "x", "events": {}}, "Mint111"))
        self.assertIsNone(normalize_swap(fixture_tx(mint="OtherMint"), "Mint111"))

    def test_reads_dune_csv_and_deduplicates_mints(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "dune.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["token_mint", "n_swaps"])
                w.writeheader()
                w.writerows([{"token_mint": "Mint111", "n_swaps": 4}, {"token_mint": "Mint111", "n_swaps": 5}, {"token_mint": "Mint222", "n_swaps": 1}])
            self.assertEqual(read_mints(path), ["Mint111", "Mint222"])

    def test_paginates_with_before_signature_until_short_page(self):
        calls = []
        def fake_get(url):
            calls.append(url)
            cursor = parse_qs(urlparse(url).query).get("before-signature", [None])[0]
            if cursor is None:
                return [fixture_tx(signature=f"sig-{i}") for i in range(2)]
            return [fixture_tx(signature="sig-final")]
        rows = list(iter_address_transactions("Mint111", "fixture-key", limit=2, request_json=fake_get))
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(calls), 2)
        self.assertEqual(parse_qs(urlparse(calls[1]).query)["before-signature"], ["sig-1"])
        self.assertNotIn("fixture-key", repr(rows))

    def test_ingest_enforces_minimum_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "history.db")
            txs = [fixture_tx(signature=f"sig-{i}") for i in range(1001)]
            def fake_get(_url):
                return txs
            # Un lote de una sola página debe respetar el máximo de 100; usa 20 mints
            # con una fila fixture cada uno, evitando hacer llamadas de red.
            mints = [f"Mint{i}" for i in range(20)]
            def per_mint(url):
                address = urlparse(url).path.split("/")[-2]
                return [fixture_tx(mint=address, signature=f"{address}-sig-{i}") for i in range(51)]
            with self.assertRaisesRegex(RuntimeError, "Umbral no alcanzado"):
                ingest(mints, db, "fixture-key", min_events=2000, limit=100, request_json=per_mint)
            stats = ingest(mints, db, "fixture-key", min_events=1000, limit=100, request_json=per_mint)
            self.assertEqual(stats["swaps_inserted"], 0)
            self.assertEqual(stats["total_events"], 1020)
            # Segunda pasada no duplica firmas ya guardadas.
            again = ingest(mints, db, "fixture-key", min_events=1000, limit=100, request_json=per_mint)
            self.assertEqual(again["swaps_inserted"], 0)
            self.assertEqual(again["total_events"], 1020)

    def test_rejects_invalid_page_size(self):
        with self.assertRaises(ValueError):
            list(iter_address_transactions("Mint111", "fixture-key", limit=101, request_json=lambda _: []))


if __name__ == "__main__":
    unittest.main()
