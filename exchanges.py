"""Spot REST adapters. No funding endpoints, retries, or implicit order conversion."""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SpotAPI:
    HOSTS = {"bybit": "https://api.bybit.com", "mexc": "https://api.mexc.com",
             "okx": "https://www.okx.com"}

    def __init__(self, exchange, key="", secret="", passphrase="", pair=None):
        if exchange not in self.HOSTS:
            raise ValueError("Unsupported spot exchange")
        self.exchange = exchange
        self.key, self.secret, self.passphrase = key, secret, passphrase
        self.pair = pair
        self.validation_mode = "server" if exchange == "mexc" else "local"
        self._specs = {}
        self._orders = {}
        self._snapshot = None

    def _request(self, method, path, params=None, private=False):
        params = dict(params or {})
        headers = {"Content-Type": "application/json", "User-Agent": "exchange-trades/1"}
        body = ""
        if private and (not self.key or not self.secret or
                        (self.exchange == "okx" and not self.passphrase)):
            raise ValueError("Missing API credentials")
        timestamp = str(int(time.time() * 1000))
        if self.exchange == "mexc":
            if private:
                params.update(timestamp=timestamp, recvWindow="5000")
            query = urlencode(params)
            if private:
                signature = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                query += "&signature=" + signature
                headers["X-MEXC-APIKEY"] = self.key
            target = path + ("?" + query if query else "")
        else:
            query = urlencode(params) if method == "GET" else ""
            body = json.dumps(params, separators=(",", ":")) if method != "GET" else ""
            target = path + ("?" + query if query else "")
            if private and self.exchange == "bybit":
                payload = timestamp + self.key + "5000" + (query if method == "GET" else body)
                headers.update({"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": timestamp,
                                "X-BAPI-RECV-WINDOW": "5000",
                                "X-BAPI-SIGN": hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()})
            elif private:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                payload = timestamp + method + target + body
                headers.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-PASSPHRASE": self.passphrase,
                                "OK-ACCESS-TIMESTAMP": timestamp,
                                "OK-ACCESS-SIGN": base64.b64encode(hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).digest()).decode()})
        req = Request(self.HOSTS[self.exchange] + target, data=body.encode() if body else None,
                      headers=headers, method=method)
        try:
            with urlopen(req, timeout=20) as response:
                data = json.load(response)
        except HTTPError as exc:
            raise ValueError(f"{self.exchange.upper()} HTTP {exc.code}; request not retried. "
                             "Check order status before retrying a live action.") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ValueError(f"{self.exchange.upper()} connection/response failure; request not retried. "
                             "Check order status before retrying a live action.") from None
        # Do not echo server messages: some services include signed URLs or request headers.
        if self.exchange == "bybit":
            if str(data.get("retCode")) != "0":
                raise ValueError(f"BYBIT API error code {data.get('retCode', 'missing')}")
            return data["result"]
        if self.exchange == "okx":
            if str(data.get("code")) != "0":
                raise ValueError(f"OKX API error code {data.get('code', 'missing')}")
            rows = data["data"]
            for row in rows:
                if str(row.get("sCode", "0")) != "0":
                    raise ValueError(f"OKX order error code {row['sCode']}")
            return rows
        if isinstance(data, dict) and "code" in data and str(data["code"]) not in ("0", "200"):
            raise ValueError(f"MEXC API error code {data['code']}")
        return data

    @staticmethod
    def _decimal(value, field, positive=True):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"Invalid {field}") from None
        if not value.is_finite() or (positive and value <= 0):
            raise ValueError(f"{field} must be finite" + (" and positive" if positive else ""))
        return value

    def _spec(self, pair):
        if pair in self._specs:
            return self._specs[pair]
        if self.exchange == "bybit":
            rows = self._request("GET", "/v5/market/instruments-info", {"category": "spot", "symbol": pair})["list"]
            raw = next((r for r in rows if r["symbol"] == pair), None)
            if not raw or raw.get("status") != "Trading":
                raise ValueError(f"{pair}: spot pair unavailable")
            lot, price = raw["lotSizeFilter"], raw["priceFilter"]
            base, quote = raw["baseCoin"], raw["quoteCoin"]
            step, tick = lot["basePrecision"], price["tickSize"]
            minimum, cost = lot["minOrderQty"], lot["minOrderAmt"]
            maximum = lot.get("maxLimitOrderQty", lot.get("maxOrderQty"))
            market_max = lot.get("maxMarketOrderQty", lot.get("maxOrderQty"))
            cost_max = lot.get("maxOrderAmt")
        elif self.exchange == "mexc":
            rows = self._request("GET", "/api/v3/exchangeInfo", {"symbol": pair})["symbols"]
            raw = next((r for r in rows if r["symbol"] == pair), None)
            if not raw or str(raw.get("status")) not in ("1", "ENABLED", "TRADING") or not raw.get("isSpotTradingAllowed"):
                raise ValueError(f"{pair}: API spot trading unavailable")
            base, quote = raw["baseAsset"], raw["quoteAsset"]
            step = format(Decimal(1).scaleb(-int(raw["baseAssetPrecision"])), "f")
            tick = format(Decimal(1).scaleb(-int(raw["quotePrecision"])), "f")
            minimum, cost = raw["baseSizePrecision"], raw["quoteAmountPrecision"]
            maximum = market_max = None
            cost_max = raw.get("maxQuoteAmount")
        else:
            rows = self._request("GET", "/api/v5/public/instruments", {"instType": "SPOT", "instId": pair})
            raw = next((r for r in rows if r["instId"] == pair), None)
            if not raw or raw.get("state") != "live":
                raise ValueError(f"{pair}: spot pair unavailable")
            base, quote = raw["baseCcy"], raw["quoteCcy"]
            step, tick = raw["lotSz"], raw["tickSz"]
            minimum, cost = raw["minSz"], "0"
            maximum, market_max = raw.get("maxLmtSz"), raw.get("maxMktSz")
            cost_max = None
        step_d, tick_d = self._decimal(step, "lot step"), self._decimal(tick, "price step")
        spec = {"altname": pair, "wsname": pair, "base": base, "quote": quote,
                "ordermin": minimum, "costmin": cost, "lot_step": str(step), "price_step": str(tick),
                "lot_decimals": max(0, -step_d.normalize().as_tuple().exponent),
                "pair_decimals": max(0, -tick_d.normalize().as_tuple().exponent),
                "max_volume": maximum, "max_market_volume": market_max, "max_cost": cost_max,
                "raw": raw}
        self._specs[pair] = spec
        return spec

    def _ticker(self, pair):
        if self.exchange == "bybit":
            rows = self._request("GET", "/v5/market/tickers", {"category": "spot", "symbol": pair})["list"]
            r = next(r for r in rows if r["symbol"] == pair)
            values = [r[k] for k in ("lastPrice", "bid1Price", "ask1Price", "lowPrice24h", "highPrice24h", "volume24h")]
        elif self.exchange == "mexc":
            r = self._request("GET", "/api/v3/ticker/24hr", {"symbol": pair})
            values = [r[k] for k in ("lastPrice", "bidPrice", "askPrice", "lowPrice", "highPrice", "volume")]
        else:
            rows = self._request("GET", "/api/v5/market/ticker", {"instId": pair})
            r = next(r for r in rows if r["instId"] == pair)
            values = [r[k] for k in ("last", "bidPx", "askPx", "low24h", "high24h", "vol24h")]
        return {k: [str(v), str(v)] for k, v in zip(("c", "b", "a", "l", "h", "v"), values)}

    def query_public(self, endpoint, params=None):
        try:
            pairs = (params or {})["pair"].split(",")
            if endpoint == "AssetPairs":
                result = {p: self._spec(p) for p in pairs}
            elif endpoint == "Ticker":
                result = {p: self._ticker(p) for p in pairs}
            else:
                raise ValueError("Unsupported public endpoint")
            return {"error": [], "result": result}
        except (ValueError, KeyError, TypeError, StopIteration, InvalidOperation) as exc:
            return self._error(exc)

    def _error(self, exc):
        message = str(exc) if isinstance(exc, ValueError) else "Unexpected exchange response; no retry performed"
        return {"error": [message], "result": {}}

    def validate_order(self, order):
        allowed = {"label", "pair", "type", "ordertype", "volume", "price", "oflags", "timeinforce"}
        if set(order) - allowed:
            raise ValueError(f"{self.exchange.upper()}: unsupported order fields {sorted(set(order) - allowed)}")
        if not {"pair", "type", "ordertype", "volume"} <= set(order):
            raise ValueError("Missing required order fields")
        if any(not isinstance(v, str) for v in order.values()):
            raise ValueError("All order values must be strings")
        side, kind = order["type"], order["ordertype"]
        if side not in ("buy", "sell") or kind not in ("market", "limit"):
            raise ValueError(f"{self.exchange.upper()} supports spot limit/market buy/sell orders only")
        tif, post = order.get("timeinforce", "GTC"), order.get("oflags", "")
        if tif not in ("GTC", "IOC", "FOK") or post not in ("", "post"):
            raise ValueError("Unsupported timeinforce or oflags")
        if post and (kind != "limit" or tif != "GTC"):
            raise ValueError("Post-only requires a GTC limit order")
        if kind == "market" and ("price" in order or post or "timeinforce" in order):
            raise ValueError("Market orders must omit price, oflags and timeinforce")
        if self.exchange == "mexc" and kind == "market" and side == "buy":
            raise ValueError("MEXC market buys require a quote-currency budget; base-volume market buys are unsupported. Use a limit buy.")
        spec = self._spec(order["pair"])
        vol = self._decimal(order["volume"], "volume")
        if vol % Decimal(spec["lot_step"]) or vol < Decimal(spec["ordermin"]):
            raise ValueError("Volume is below the minimum or off the lot-size grid")
        maxvol = spec["max_market_volume" if kind == "market" else "max_volume"]
        if maxvol and Decimal(maxvol) > 0 and vol > Decimal(maxvol):
            raise ValueError("Volume exceeds exchange maximum")
        if kind == "limit":
            price = self._decimal(order.get("price", ""), "limit price")
            if price % Decimal(spec["price_step"]):
                raise ValueError("Price is off the tick-size grid")
        else:
            price = self._decimal(self._ticker(order["pair"])["c"][0], "market price")
        minimum, maximum = spec["costmin"], spec["max_cost"]
        if self.exchange == "mexc":
            raw = spec["raw"]
            side_type = str(raw.get("tradeSideType", "1"))
            if side_type == "4" or (side_type == "2" and side != "buy") or (side_type == "3" and side != "sell"):
                raise ValueError("Pair does not currently allow this order side")
            native = "MARKET" if kind == "market" else ("LIMIT_MAKER" if post else {"GTC": "LIMIT", "IOC": "IMMEDIATE_OR_CANCEL", "FOK": "FILL_OR_KILL"}[tif])
            if native not in raw.get("orderTypes", []):
                raise ValueError(f"Pair does not support {native}")
            if kind == "market":
                minimum = raw.get("quoteAmountPrecisionMarket", minimum)
                maximum = raw.get("maxQuoteAmountMarket", maximum)
        if vol * price < Decimal(minimum):
            raise ValueError("Order notional is below exchange minimum")
        if maximum and Decimal(maximum) > 0 and vol * price > Decimal(maximum):
            raise ValueError("Order notional exceeds exchange maximum")

    def _add_order(self, params):
        order = {k: v for k, v in params.items() if k != "validate"}
        self.validate_order(order)
        test = params.get("validate") in (True, "true")
        if "validate" in params and not test:
            raise ValueError("Invalid validation flag; refusing live submission")
        if test and self.validation_mode == "local":
            raise ValueError("Server-side validation unavailable; do local preflight only")
        pair, kind, side = order["pair"], order["ordertype"], order["type"]
        tif, post = order.get("timeinforce", "GTC"), order.get("oflags") == "post"
        if self.exchange == "bybit":
            payload = {"category": "spot", "symbol": pair, "side": side.title(),
                       "orderType": kind.title(), "qty": order["volume"], "isLeverage": 0,
                       "orderFilter": "Order"}
            if kind == "market":
                payload["marketUnit"] = "baseCoin"
            else:
                payload.update(price=order["price"], timeInForce="PostOnly" if post else tif)
            result = self._request("POST", "/v5/order/create", payload, True)
            txid = result.get("orderId")
        elif self.exchange == "mexc":
            native = "MARKET" if kind == "market" else ("LIMIT_MAKER" if post else {"GTC": "LIMIT", "IOC": "IMMEDIATE_OR_CANCEL", "FOK": "FILL_OR_KILL"}[tif])
            payload = {"symbol": pair, "side": side.upper(), "type": native, "quantity": order["volume"]}
            if kind == "limit":
                payload["price"] = order["price"]
            result = self._request("POST", "/api/v3/order/test" if test else "/api/v3/order", payload, True)
            if test:
                return {"descr": {"order": "MEXC test endpoint accepted request"}}
            txid = result.get("orderId")
        else:
            native = "market" if kind == "market" else ("post_only" if post else {"GTC": "limit", "IOC": "ioc", "FOK": "fok"}[tif])
            payload = {"instId": pair, "tdMode": "cash", "side": side, "ordType": native, "sz": order["volume"]}
            if kind == "market":
                payload.update(tgtCcy="base_ccy", banAmend=True)
            else:
                payload["px"] = order["price"]
            result = self._request("POST", "/api/v5/trade/order", payload, True)
            txid = result[0].get("ordId")
        if not txid:
            raise ValueError("Order response omitted ID; outcome unknown. Check orders before retrying.")
        return {"txid": [str(txid)], "descr": {"order": f"{side} {order['volume']} {pair} {kind}"}}

    def _balance(self):
        if self.exchange == "mexc":
            data = self._request("GET", "/api/v3/account", private=True)
            return {r["asset"]: r["free"] for r in data["balances"]}
        if self.exchange == "okx":
            data = self._request("GET", "/api/v5/account/balance", private=True)
            return {r["ccy"]: r["availBal"] for account in data for r in account["details"]}
        data = self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, True)
        balances = {}
        for account in data["list"]:
            for coin in account["coin"]:
                # Cash available without borrowing; do not treat collateral buying power as cash.
                wallet = self._decimal(coin["walletBalance"], "wallet balance", False)
                locked = self._decimal(coin["locked"], "locked balance", False)
                borrowed = self._decimal(coin.get("spotBorrow", "0"), "spot borrow", False)
                balances[coin["coin"]] = str(max(Decimal(0), wallet - locked - borrowed))
        return balances

    def _require_pair(self):
        if not self.pair:
            raise ValueError(f"{self.exchange.upper()}: this command requires --pair")
        return self.pair

    def _bybit_pages(self, path, params):
        rows, seen = [], set()
        params = dict(params, category="spot", limit=50)
        while True:
            data = self._request("GET", path, params, True)
            rows.extend(data["list"])
            cursor = data.get("nextPageCursor")
            if not cursor:
                return rows
            if cursor in seen or len(seen) >= 200:
                raise ValueError("Could not fetch a complete order snapshot; refusing truncated results")
            seen.add(cursor)
            params["cursor"] = cursor

    def _okx_pages(self, path, params):
        rows, seen = [], set()
        params = dict(params, instType="SPOT", limit="100")
        while True:
            batch = self._request("GET", path, params, True)
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            cursor = batch[-1]["ordId"]
            if cursor in seen or len(seen) >= 200:
                raise ValueError("Could not fetch a complete order snapshot; refusing truncated results")
            seen.add(cursor)
            params["after"] = cursor

    def _order_rows(self, closed=False):
        if self.exchange == "bybit":
            params = {"orderFilter": "Order"}
            if self.pair:
                params["symbol"] = self.pair
            if not closed:
                params["openOnly"] = 0
            return self._bybit_pages("/v5/order/history" if closed else "/v5/order/realtime", params)
        if self.exchange == "okx":
            params = {"instId": self.pair} if self.pair else {}
            return self._okx_pages("/api/v5/trade/orders-history" if closed else "/api/v5/trade/orders-pending", params)
        params = {"symbol": self._require_pair()}
        if closed:
            params["limit"] = 1000
        rows = self._request("GET", "/api/v3/allOrders" if closed else "/api/v3/openOrders", params, True)
        if not closed and len(rows) >= 1000:
            raise ValueError("MEXC open-order response may be truncated; refusing cancellation snapshot")
        return rows

    def _normalize_order(self, r):
        if self.exchange == "bybit":
            ident, pair, side, kind = r["orderId"], r["symbol"], r["side"], r["orderType"]
            price, vol, filled, status, ts = r.get("price", ""), r["qty"], r.get("cumExecQty", "0"), r["orderStatus"], r.get("updatedTime", 0)
        elif self.exchange == "mexc":
            ident, pair, side, kind = r["orderId"], r["symbol"], r["side"], r["type"]
            price, vol, filled, status, ts = r.get("price", ""), r["origQty"], r["executedQty"], r["status"], r.get("updateTime", r.get("time", 0))
        else:
            ident, pair, side, kind = r["ordId"], r["instId"], r["side"], r["ordType"]
            price, vol, filled, status, ts = r.get("px", ""), r["sz"], r.get("accFillSz", "0"), r["state"], r.get("uTime", 0)
        return str(ident), {"descr": {"pair": pair, "type": side.lower(), "ordertype": kind.lower(), "price": str(price)},
                            "vol": str(vol), "vol_exec": str(filled), "status": status,
                            "closetm": int(ts or 0) / 1000}

    @staticmethod
    def _is_open(order):
        return order["status"].lower() in {"new", "partiallyfilled", "partially_filled", "live", "untriggered"}

    def _list_orders(self, closed=False):
        orders = dict(self._normalize_order(r) for r in self._order_rows(closed))
        orders = {k: v for k, v in orders.items() if self._is_open(v) != closed}
        self._orders.update(orders)
        if not closed:
            self._snapshot = dict(orders)
        return {"closed" if closed else "open": orders}

    def _query_orders(self, ids):
        if self.exchange == "okx" and not self.pair:
            orders = self._list_orders()["open"]
            return {ident: orders[ident] for ident in ids if ident in orders}
        found = {}
        for ident in ids:
            if self.exchange == "bybit":
                params = {"category": "spot", "orderId": ident, "orderFilter": "Order"}
                if self.pair:
                    params["symbol"] = self.pair
                rows = self._request("GET", "/v5/order/realtime", params, True)["list"]
            elif self.exchange == "mexc":
                rows = [self._request("GET", "/api/v3/order", {"symbol": self._require_pair(), "orderId": ident}, True)]
            else:
                rows = self._request("GET", "/api/v5/trade/order", {"instId": self._require_pair(), "ordId": ident}, True)
            for row in rows:
                key, normalized = self._normalize_order(row)
                if key == ident and (not self.pair or normalized["descr"]["pair"] == self.pair):
                    found[key] = normalized
        self._orders.update(found)
        return found

    def _cancel_order(self, ident):
        if ident not in self._orders:
            raise ValueError("Order was not fetched for confirmation; refusing cancellation")
        pair = self._orders[ident]["descr"]["pair"]
        if self.exchange == "bybit":
            self._request("POST", "/v5/order/cancel", {"category": "spot", "symbol": pair, "orderId": ident, "orderFilter": "Order"}, True)
        elif self.exchange == "mexc":
            self._request("DELETE", "/api/v3/order", {"symbol": pair, "orderId": ident}, True)
        else:
            self._request("POST", "/api/v5/trade/cancel-order", {"instId": pair, "ordId": ident}, True)
        return {"count": 1}

    def _trades(self):
        if self.exchange == "bybit":
            params = {"category": "spot", "limit": 100}
            if self.pair:
                params["symbol"] = self.pair
            rows = self._request("GET", "/v5/execution/list", params, True)["list"]
        elif self.exchange == "mexc":
            rows = self._request("GET", "/api/v3/myTrades", {"symbol": self._require_pair(), "limit": 1000}, True)
        else:
            params = {"instType": "SPOT", "limit": "100"}
            if self.pair:
                params["instId"] = self.pair
            rows = self._request("GET", "/api/v5/trade/fills-history", params, True)
        trades = {}
        for r in rows:
            if self.exchange == "bybit":
                ident, pair, side = r["execId"], r["symbol"], r["side"].lower()
                price, vol, fee, ts = r["execPrice"], r["execQty"], r["execFee"], r["execTime"]
                cost = r.get("execValue", str(Decimal(price) * Decimal(vol)))
            elif self.exchange == "mexc":
                ident, pair, side = r["id"], r["symbol"], "buy" if r["isBuyer"] else "sell"
                price, vol, fee, ts = r["price"], r["qty"], r["commission"], r["time"]
                cost = r.get("quoteQty", str(Decimal(price) * Decimal(vol)))
            else:
                ident, pair, side = r.get("billId", r["tradeId"]), r["instId"], r["side"]
                price, vol, fee, ts = r["fillPx"], r["fillSz"], r["fee"], r["ts"]
                cost = str(Decimal(price) * Decimal(vol))
            trades[str(ident)] = {"pair": pair, "type": side, "price": price, "vol": vol,
                                  "fee": fee, "cost": cost, "time": int(ts) / 1000}
        return {"trades": trades}

    def query_private(self, endpoint, params=None):
        params = params or {}
        try:
            if endpoint == "Balance":
                result = self._balance()
            elif endpoint in ("OpenOrders", "ClosedOrders"):
                result = self._list_orders(endpoint == "ClosedOrders")
            elif endpoint == "TradesHistory":
                result = self._trades()
            elif endpoint == "AddOrder":
                result = self._add_order(params)
            elif endpoint == "QueryOrders":
                result = self._query_orders(params["txid"].split(","))
            elif endpoint == "CancelOrder":
                result = self._cancel_order(params["txid"])
            elif endpoint == "CancelAll":
                if self._snapshot is None:
                    raise ValueError("Fetch the open-order snapshot before cancellation")
                count = 0
                for ident in self._snapshot:
                    try:
                        self._cancel_order(ident)
                        count += 1
                    except (ValueError, KeyError, TypeError, InvalidOperation):
                        return {"error": [f"Cancellation stopped after {count} accepted requests; inspect open orders before retrying"], "result": {"count": count}}
                self._snapshot = None
                result = {"count": count}
            else:
                raise ValueError("Unsupported private endpoint; only spot queries and orders are allowed")
            return {"error": [], "result": result}
        except (ValueError, KeyError, TypeError, StopIteration, IndexError, InvalidOperation) as exc:
            return self._error(exc)
