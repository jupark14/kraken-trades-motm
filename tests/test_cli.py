import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kraken


class FakeSpotAPI(kraken.SpotAPI):
    def __init__(self, exchange="bybit", validation_mode="local"):
        self.exchange = exchange
        self.validation_mode = validation_mode
        self.private_calls = []
        self.validated_orders = []

    def validate_order(self, order):
        self.validated_orders.append(order)

    def query_private(self, endpoint, params=None):
        self.private_calls.append((endpoint, dict(params or {})))
        if endpoint == "AddOrder":
            return {"error": [], "result": {"descr": {"order": "accepted"}}}
        if endpoint == "Balance":
            return {"error": [], "result": {"USD": "1000", "SOL": "10"}}
        if endpoint == "QueryOrders":
            return {"error": [], "result": {"TX1": self.open_order_row()}}
        if endpoint == "OpenOrders":
            return {"error": [], "result": {"open": {"TX1": self.open_order_row()}}}
        if endpoint == "CancelOrder":
            return {"error": [], "result": {"count": 1}}
        if endpoint == "CancelAll":
            return {"error": [], "result": {"count": 1}}
        return {"error": [], "result": {}}

    def open_order_row(self):
        return {
            "descr": {
                "pair": "SOLUSD",
                "type": "sell",
                "ordertype": "limit",
                "price": "100",
            },
            "vol": "1",
            "vol_exec": "0",
            "status": "open",
        }


class FakeLiveAPI(FakeSpotAPI):
    def __init__(self, responses):
        super().__init__(exchange="bybit")
        self.responses = list(responses)

    def query_private(self, endpoint, params=None):
        self.private_calls.append((endpoint, dict(params or {})))
        if endpoint == "AddOrder":
            return self.responses.pop(0)
        return super().query_private(endpoint, params)


class FakeKrakenAPI:
    exchange = "kraken"

    def __init__(self):
        self.private_calls = []

    def query_private(self, endpoint, params=None):
        self.private_calls.append((endpoint, dict(params or {})))
        return {"error": [], "result": {"descr": {"order": "accepted"}}}


class CLISafetyTests(unittest.TestCase):
    def setUp(self):
        self.order = {
            "label": "limit buy",
            "pair": "SOLUSD",
            "type": "buy",
            "ordertype": "limit",
            "price": "100",
            "volume": "1",
        }
        self.doc = {
            "exchange": "kraken",
            "account": "main",
            "orders": [dict(self.order)],
        }

    def write_order_file(self, doc):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        with handle:
            json.dump(doc, handle)
        return handle.name

    def assert_aborts(self, func, expected_code=1):
        with self.assertRaises(SystemExit) as raised:
            func()
        self.assertEqual(raised.exception.code, expected_code)

    def test_get_api_defaults_to_kraken_when_account_has_no_exchange(self):
        api = SimpleNamespace()
        accounts = {
            "main": {
                "key_env": "KRAKEN_API_KEY",
                "secret_env": "KRAKEN_API_SECRET",
                "description": "Main account",
            }
        }

        with patch.object(kraken, "load_accounts", return_value=accounts), \
                patch.object(kraken, "load_dotenv") as load_dotenv, \
                patch.dict(os.environ, {
                    "KRAKEN_API_KEY": "key",
                    "KRAKEN_API_SECRET": "secret",
                }, clear=True), \
                patch.object(kraken.krakenex, "API", return_value=api) as api_factory:
            result = kraken.get_api("main")

        self.assertIs(result, api)
        self.assertEqual(result.exchange, "kraken")
        load_dotenv.assert_called_once_with(kraken.SCRIPT_DIR / ".env")
        api_factory.assert_called_once_with(key="key", secret="secret")

    def test_get_api_rejects_kraken_pair_scope_before_dotenv(self):
        accounts = {
            "main": {
                "key_env": "KRAKEN_API_KEY",
                "secret_env": "KRAKEN_API_SECRET",
            }
        }

        with patch.object(kraken, "load_accounts", return_value=accounts), \
                patch.object(kraken, "load_dotenv") as load_dotenv:
            self.assert_aborts(lambda: kraken.get_api("main", pair="SOLUSD"))

        load_dotenv.assert_not_called()

    def test_okx_account_requires_passphrase_env(self):
        accounts = {
            "okx": {
                "exchange": "okx",
                "key_env": "OKX_API_KEY",
                "secret_env": "OKX_API_SECRET",
            }
        }

        with patch.object(kraken, "load_accounts", return_value=accounts), \
                patch.object(kraken, "load_dotenv"), \
                patch.dict(os.environ, {
                    "OKX_API_KEY": "key",
                    "OKX_API_SECRET": "secret",
                }, clear=True):
            self.assert_aborts(lambda: kraken.get_api("okx"))

    def test_cmd_place_rejects_exchange_mismatch_before_preflight(self):
        path = self.write_order_file({
            "exchange": "bybit",
            "account": "okx",
            "orders": [dict(self.order)],
        })
        api = FakeSpotAPI(exchange="okx")
        args = SimpleNamespace(file=path, account="okx", live=False)

        with patch.object(kraken, "get_api", return_value=api), \
                patch.object(kraken, "run_preflight") as run_preflight:
            self.assert_aborts(lambda: kraken.cmd_place(args))

        run_preflight.assert_not_called()
        self.assertEqual(api.private_calls, [])

    def test_place_live_refusal_does_not_add_order(self):
        api = FakeSpotAPI(exchange="bybit")

        with patch("builtins.input", return_value="no"):
            self.assert_aborts(
                lambda: kraken.place_live(api, self.doc, "bybit", "orders.json"),
                expected_code=0,
            )

        self.assertNotIn("AddOrder", [endpoint for endpoint, _ in api.private_calls])

    def test_cmd_cancel_declined_confirmation_does_not_cancel_order(self):
        api = FakeSpotAPI(exchange="bybit")
        args = SimpleNamespace(account="bybit", pair="SOLUSD", txids=["TX1"])

        with patch.object(kraken, "get_api", return_value=api), \
                patch("builtins.input", return_value="n"), \
                patch.object(kraken, "write_log") as write_log:
            kraken.cmd_cancel(args)

        endpoints = [endpoint for endpoint, _ in api.private_calls]
        self.assertEqual(endpoints, ["QueryOrders"])
        write_log.assert_not_called()

    def test_cmd_cancel_all_declined_confirmation_does_not_cancel_orders(self):
        api = FakeSpotAPI(exchange="bybit")
        args = SimpleNamespace(account="bybit", pair="SOLUSD")

        with patch.object(kraken, "get_api", return_value=api), \
                patch("builtins.input", return_value="no"), \
                patch.object(kraken, "write_log") as write_log:
            kraken.cmd_cancel_all(args)

        endpoints = [endpoint for endpoint, _ in api.private_calls]
        self.assertEqual(endpoints, ["OpenOrders"])
        write_log.assert_not_called()

    def test_place_live_success_logs_exchange_and_order_result(self):
        api = FakeLiveAPI([{
            "error": [],
            "result": {
                "txid": ["OID-1"],
                "descr": {"order": "buy 1 SOLUSD @ 100"},
            },
        }])

        with patch("builtins.input", return_value="CONFIRM"), \
                patch.object(kraken, "_utc_now", return_value="2026-09-28T00:00:00Z"), \
                patch.object(kraken, "write_log") as write_log:
            kraken.place_live(api, self.doc, "bybit", "orders.json")

        self.assertEqual(api.private_calls, [("AddOrder", {
            "pair": "SOLUSD",
            "type": "buy",
            "ordertype": "limit",
            "price": "100",
            "volume": "1",
        })])
        write_log.assert_called_once_with({
            "ts": "2026-09-28T00:00:00Z",
            "event": "add_order",
            "account": "bybit",
            "exchange": "bybit",
            "source_file": "orders.json",
            "label": "limit buy",
            "params": {
                "pair": "SOLUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "100",
                "volume": "1",
            },
            "txid": "OID-1",
            "descr": "buy 1 SOLUSD @ 100",
            "error": None,
        })

    def test_place_live_failure_logs_exchange_and_aborts(self):
        api = FakeLiveAPI([{
            "error": ["EOrder:Invalid price"],
            "result": {},
        }])

        with patch("builtins.input", return_value="CONFIRM"), \
                patch.object(kraken, "_utc_now", return_value="2026-09-28T00:00:00Z"), \
                patch.object(kraken, "write_log") as write_log:
            self.assert_aborts(
                lambda: kraken.place_live(api, self.doc, "bybit", "orders.json"),
                expected_code=1,
            )

        self.assertEqual(api.private_calls, [("AddOrder", {
            "pair": "SOLUSD",
            "type": "buy",
            "ordertype": "limit",
            "price": "100",
            "volume": "1",
        })])
        write_log.assert_called_once_with({
            "ts": "2026-09-28T00:00:00Z",
            "event": "add_order",
            "account": "bybit",
            "exchange": "bybit",
            "source_file": "orders.json",
            "label": "limit buy",
            "params": {
                "pair": "SOLUSD",
                "type": "buy",
                "ordertype": "limit",
                "price": "100",
                "volume": "1",
            },
            "txid": None,
            "descr": "",
            "error": ["EOrder:Invalid price"],
        })

    def test_run_validate_skips_add_order_for_local_spot_validation(self):
        for exchange in ("bybit", "okx"):
            with self.subTest(exchange=exchange):
                api = FakeSpotAPI(exchange=exchange, validation_mode="local")

                kraken.run_validate(api, self.doc)

                self.assertNotIn("AddOrder", [endpoint for endpoint, _ in api.private_calls])

    def test_run_validate_sends_validate_true_for_kraken_dry_run(self):
        api = FakeKrakenAPI()

        kraken.run_validate(api, self.doc)

        self.assertEqual(len(api.private_calls), 1)
        endpoint, params = api.private_calls[0]
        self.assertEqual(endpoint, "AddOrder")
        self.assertEqual(params["validate"], "true")
        self.assertEqual(params["pair"], "SOLUSD")

    def test_write_order_doc_binds_generated_file_to_account_exchange(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "order.json"
            args = SimpleNamespace(account="bybit", output=str(output), force=False)
            api = FakeSpotAPI(exchange="bybit")

            with patch.object(kraken, "account_config", return_value={"exchange": "bybit"}), \
                    patch.object(kraken, "get_public_api", return_value=api), \
                    patch.object(kraken.sys, "argv", ["kraken.py", "split", "chunk"]):
                kraken._write_order_doc(args, [dict(self.order)], "generated order")

            doc = json.loads(output.read_text())

        self.assertEqual(doc["exchange"], "bybit")
        self.assertEqual(doc["account"], "bybit")
        self.assertEqual(api.validated_orders, [self.order])

    def test_load_order_file_rejects_unknown_order_keys(self):
        order = dict(self.order, client_order_id="abc")
        path = self.write_order_file({
            "exchange": "kraken",
            "account": "main",
            "orders": [order],
        })

        self.assert_aborts(lambda: kraken.load_order_file(path, "main"))

    def test_load_order_file_rejects_account_mismatch(self):
        path = self.write_order_file({
            "exchange": "kraken",
            "account": "secondary",
            "orders": [dict(self.order)],
        })

        self.assert_aborts(lambda: kraken.load_order_file(path, "main"))

    def test_allocate_volumes_preserves_total_on_lot_step(self):
        shares = kraken.allocate_volumes(
            Decimal("0.025"),
            [Decimal("1"), Decimal("1"), Decimal("3")],
            lot_decimals=3,
            lot_step="0.005",
        )

        self.assertEqual(sum(shares), Decimal("0.025"))
        self.assertEqual(shares, [Decimal("0.005"), Decimal("0.005"), Decimal("0.015")])

    def test_allocate_volumes_rejects_total_outside_lot_step(self):
        self.assert_aborts(lambda: kraken.allocate_volumes(
            Decimal("0.026"),
            [Decimal("1"), Decimal("1")],
            lot_decimals=3,
            lot_step="0.005",
        ))

    def test_allocate_volumes_rejects_invalid_weights(self):
        self.assert_aborts(lambda: kraken.allocate_volumes(
            Decimal("0.025"),
            [Decimal("1"), Decimal("0")],
            lot_decimals=3,
            lot_step="0.005",
        ))


if __name__ == "__main__":
    unittest.main()
