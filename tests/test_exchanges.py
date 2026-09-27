"""Offline wire-contract and safety regressions; all HTTP calls are mocked."""
import base64
import hashlib
import hmac
import io
import json
import unittest
from unittest.mock import patch, Mock
from urllib.error import URLError
from urllib.parse import urlsplit

from exchanges import SpotAPI


SPEC = {"altname": "BTCUSDT", "wsname": "BTCUSDT", "base": "BTC", "quote": "USDT",
        "ordermin": "0.001", "costmin": "1", "lot_step": "0.001", "price_step": "0.1",
        "lot_decimals": 3, "pair_decimals": 1, "max_volume": "100", "max_market_volume": "10",
        "max_cost": "1000000", "raw": {"orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "IMMEDIATE_OR_CANCEL", "FILL_OR_KILL"]}}
ORDER = {"pair": "BTCUSDT", "type": "buy", "ordertype": "limit", "volume": "0.01", "price": "50000"}


def client(exchange):
    api = SpotAPI(exchange, key="test-key", secret="test-secret", passphrase="test-pass")
    api._specs["BTCUSDT"] = dict(SPEC)
    api._specs["BTC-USDT"] = dict(SPEC)
    return api


class AdapterSafetyTests(unittest.TestCase):
    def test_bybit_and_okx_validate_flag_cannot_submit(self):
        for exchange in ("bybit", "okx"):
            with self.subTest(exchange=exchange):
                api = client(exchange)
                with patch.object(api, "_request") as request:
                    response = api.query_private("AddOrder", dict(ORDER, validate="true"))
                self.assertTrue(response["error"])
                request.assert_not_called()

    def test_mexc_dry_run_only_uses_test_endpoint(self):
        api = client("mexc")
        with patch.object(api, "_request", return_value={}) as request:
            response = api.query_private("AddOrder", dict(ORDER, validate="true"))
        self.assertFalse(response["error"])
        request.assert_called_once_with("POST", "/api/v3/order/test",
                                       {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "quantity": "0.01", "price": "50000"}, True)

    def test_bad_validation_flag_never_falls_through_to_live(self):
        api = client("mexc")
        with patch.object(api, "_request") as request:
            self.assertTrue(api.query_private("AddOrder", dict(ORDER, validate="false"))["error"])
        request.assert_not_called()

    def test_mexc_market_buy_rejected_without_quote_conversion(self):
        api = client("mexc")
        order = {k: v for k, v in ORDER.items() if k != "price"}
        order["ordertype"] = "market"
        with patch.object(api, "_request") as request:
            self.assertTrue(api.query_private("AddOrder", order)["error"])
        request.assert_not_called()

    def test_base_quantity_and_cash_only_for_market_buys(self):
        order = {k: v for k, v in ORDER.items() if k != "price"}
        order["ordertype"] = "market"
        for exchange in ("bybit", "okx"):
            api = client(exchange)
            result = {"orderId": "id"} if exchange == "bybit" else [{"ordId": "id"}]
            with patch.object(api, "_ticker", return_value={"c": ["50000"]}), patch.object(api, "_request", return_value=result) as request:
                self.assertFalse(api.query_private("AddOrder", order)["error"])
            payload = request.call_args.args[2]
            if exchange == "bybit":
                self.assertEqual(payload["marketUnit"], "baseCoin")
                self.assertEqual(payload["isLeverage"], 0)
                self.assertEqual(payload["category"], "spot")
            else:
                self.assertEqual(payload["tgtCcy"], "base_ccy")
                self.assertEqual(payload["tdMode"], "cash")
                self.assertTrue(payload["banAmend"])

    def test_unsupported_options_and_invalid_numbers_never_submit(self):
        variants = [{"displayvol": "0.001"}, {"price2": "45000"}, {"trigger": "last"},
                    {"oflags": "post,fcib"}, {"timeinforce": "GTD"}, {"ordertype": "stop-loss"},
                    {"volume": "NaN"}, {"volume": "-1"}, {"volume": "Infinity"},
                    {"price": "0"}, {"price": "50000.01"}, {"volume": "0.0015"},
                    {"oflags": "post", "timeinforce": "IOC"}]
        for exchange in ("bybit", "mexc", "okx"):
            for change in variants:
                with self.subTest(exchange=exchange, change=change):
                    api = client(exchange)
                    with patch.object(api, "_request") as request:
                        self.assertTrue(api.query_private("AddOrder", dict(ORDER, **change))["error"])
                    request.assert_not_called()

    def test_minimum_notional_rejected(self):
        api = client("bybit")
        with self.assertRaisesRegex(ValueError, "notional"):
            api.validate_order(dict(ORDER, volume="0.001", price="100"))

    def test_post_only_payloads(self):
        for exchange, field, expected, result in (
                ("bybit", "timeInForce", "PostOnly", {"orderId": "id"}),
                ("mexc", "type", "LIMIT_MAKER", {"orderId": "id"}),
                ("okx", "ordType", "post_only", [{"ordId": "id"}])):
            api = client(exchange)
            with patch.object(api, "_request", return_value=result) as request:
                self.assertFalse(api.query_private("AddOrder", dict(ORDER, oflags="post"))["error"])
            self.assertEqual(request.call_args.args[2][field], expected)

    def test_missing_order_id_is_unknown_outcome(self):
        api = client("bybit")
        with patch.object(api, "_request", return_value={}):
            self.assertIn("outcome unknown", api.query_private("AddOrder", ORDER)["error"][0])

    def test_bybit_open_orders_paginate_before_snapshot(self):
        api = client("bybit")
        row = {"orderId": "1", "symbol": "BTCUSDT", "side": "Buy", "orderType": "Limit",
               "qty": "1", "price": "50000", "orderStatus": "New"}
        with patch.object(api, "_request", side_effect=[{"list": [row], "nextPageCursor": "next"},
                                                       {"list": [dict(row, orderId="2")], "nextPageCursor": ""}]) as request:
            response = api.query_private("OpenOrders")
        self.assertEqual(set(response["result"]["open"]), {"1", "2"})
        self.assertEqual(request.call_args.args[2]["cursor"], "next")
        self.assertEqual(set(api._snapshot), {"1", "2"})

    def test_cancel_all_only_uses_displayed_snapshot(self):
        api = client("okx")
        order = {"descr": {"pair": "BTC-USDT"}}
        api._snapshot = {"displayed": order}
        api._orders = {"displayed": order, "new-not-displayed": order}
        with patch.object(api, "_request", return_value=[{"ordId": "displayed"}]) as request:
            response = api.query_private("CancelAll")
        self.assertEqual(response["result"]["count"], 1)
        request.assert_called_once_with("POST", "/api/v5/trade/cancel-order", {"instId": "BTC-USDT", "ordId": "displayed"}, True)

    def test_partial_cancel_failure_preserves_accepted_count(self):
        api = client("bybit")
        api._snapshot = {"1": {}, "2": {}}
        with patch.object(api, "_cancel_order", side_effect=[{"count": 1}, ValueError("failed")]):
            response = api.query_private("CancelAll")
        self.assertTrue(response["error"])
        self.assertEqual(response["result"]["count"], 1)

    def test_cancel_without_snapshot_and_funding_endpoints_rejected(self):
        api = client("mexc")
        with patch.object(api, "_request") as request:
            for endpoint in ("CancelAll", "Withdraw", "Transfer"):
                self.assertTrue(api.query_private(endpoint)["error"])
            self.assertTrue(api.query_private("CancelOrder", {"txid": "unknown"})["error"])
        request.assert_not_called()

    def test_mexc_history_requires_pair(self):
        api = client("mexc")
        with patch.object(api, "_request") as request:
            self.assertTrue(api.query_private("ClosedOrders")["error"])
            self.assertTrue(api.query_private("TradesHistory")["error"])
        request.assert_not_called()

    def test_bybit_available_cash_excludes_locks_and_borrowing(self):
        api = client("bybit")
        response = {"list": [{"coin": [{"coin": "USDT", "walletBalance": "100", "locked": "30", "spotBorrow": "20"}]}]}
        with patch.object(api, "_request", return_value=response):
            self.assertEqual(api.query_private("Balance")["result"], {"USDT": "50"})

    def test_mexc_quantity_minimum_is_not_the_lot_grid(self):
        api = SpotAPI("mexc")
        response = {"symbols": [{"symbol": "TESTUSDT", "status": "1", "isSpotTradingAllowed": True,
                                  "baseAsset": "TEST", "quoteAsset": "USDT", "baseAssetPrecision": 2,
                                  "quotePrecision": 3, "baseSizePrecision": "0.1", "quoteAmountPrecision": "1"}]}
        with patch.object(api, "_request", return_value=response):
            spec = api.query_public("AssetPairs", {"pair": "TESTUSDT"})["result"]["TESTUSDT"]
        self.assertEqual(spec["ordermin"], "0.1")
        self.assertEqual(spec["lot_step"], "0.01")


class SigningTests(unittest.TestCase):
    def capture(self, exchange, method, path, params, response):
        api = client(exchange)
        with patch("exchanges.time.time", return_value=1658384314.791), patch("exchanges.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            api._request(method, path, params, True)
        return send.call_args.args[0]

    def test_bybit_signs_exact_get_and_post_payload(self):
        for method in ("GET", "POST"):
            req = self.capture("bybit", method, "/test", {"category": "spot", "symbol": "BTCUSDT"}, {"retCode": 0, "result": {}})
            headers = dict((k.lower(), v) for k, v in req.header_items())
            payload = urlsplit(req.full_url).query if method == "GET" else req.data.decode()
            message = headers["x-bapi-timestamp"] + "test-key5000" + payload
            expected = hmac.new(b"test-secret", message.encode(), hashlib.sha256).hexdigest()
            self.assertEqual(headers["x-bapi-sign"], expected)

    def test_mexc_signs_all_query_fields(self):
        req = self.capture("mexc", "POST", "/api/v3/order/test", {"symbol": "BTCUSDT"}, {})
        query, signature = urlsplit(req.full_url).query.rsplit("&signature=", 1)
        self.assertIn("timestamp=1658384314791", query)
        self.assertEqual(signature, hmac.new(b"test-secret", query.encode(), hashlib.sha256).hexdigest())

    def test_okx_signs_timestamp_method_path_and_body(self):
        for method in ("GET", "POST"):
            req = self.capture("okx", method, "/api/v5/trade/order", {"instId": "BTC-USDT"}, {"code": "0", "data": []})
            headers = dict((k.lower(), v) for k, v in req.header_items())
            parts = urlsplit(req.full_url)
            target = parts.path + ("?" + parts.query if parts.query else "")
            message = headers["ok-access-timestamp"] + method + target + (req.data.decode() if req.data else "")
            expected = base64.b64encode(hmac.new(b"test-secret", message.encode(), hashlib.sha256).digest()).decode()
            self.assertEqual(headers["ok-access-sign"], expected)
            self.assertEqual(headers["ok-access-passphrase"], "test-pass")

    def test_okx_per_order_failure_not_treated_as_success(self):
        api = client("okx")
        raw = {"code": "0", "data": [{"sCode": "51008", "sMsg": "private details"}]}
        with patch("exchanges.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            response = api.query_private("AddOrder", ORDER)
        self.assertIn("51008", response["error"][0])
        self.assertNotIn("private details", response["error"][0])

    def test_network_failure_does_not_retry_or_echo_credentials(self):
        api = client("bybit")
        with patch("exchanges.urlopen", side_effect=URLError("test-secret in signed URL")) as send:
            response = api.query_private("AddOrder", ORDER)
        send.assert_called_once()
        self.assertTrue(response["error"])
        self.assertNotIn("test-secret", response["error"][0])


if __name__ == "__main__":
    unittest.main()
