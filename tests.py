"""Inci v6 paper-only test suite. Contract fixtures follow official V2 shapes
specified in review (create on /portfolio/events/orders with side=bid|ask,
separate price, fp string count, time_in_force, self_trade_prevention_type;
acks without status; poll/list on /portfolio/orders; fp/dollar portfolio
fields). HTTP wiring uses fake sessions; neither demo nor production order
endpoints are called. Every reviewed issue has a regression. Run: python tests.py
"""
import os
import time
import tempfile
from decimal import Decimal

from config import Config
from fees import fee_usd, net_take_profit, projected_scalp_pnl_usd
from signals import dip_signal
from strategy import ScalpStrategy
from schemas import (SchemaError, UnknownOrderState, parse_market,
                     parse_order, parse_create_ack, parse_fill,
                     parse_position, parse_orderbook_response,
                     build_order_body,
                     CREATE_CANCEL_ENDPOINT, ORDERS_ENDPOINT)
from order_journal import OrderJournal
from executor import Executor, HaltError
from safety import Safety, Reconciler, ExposureError
from engine import Context, process_tick, check_loss_limit, flatten_all


class LiveTestExecutor(Executor):
    """Explicit source-level test harness; production Executor stays locked."""
    def _real_orders_enabled(self):
        return True

    def execute(self, ticker, side, contracts, expected_pre_position=None,
                max_entry_price=None):
        # Lifecycle-focused tests written before the mandatory signal cap use
        # an intentionally permissive cap; cap behavior has its own regression.
        if side == "BUY" and max_entry_price is None:
            max_entry_price = Decimal(99)
        return super().execute(
            ticker, side, contracts,
            expected_pre_position=expected_pre_position,
            max_entry_price=max_entry_price)


def current_market(ticker="T", event_ticker="E", **overrides):
    """Small fixture containing every current Market field Inci consumes."""
    payload = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_type": "binary",
        "yes_sub_title": "Yes side",
        "no_sub_title": "No side",
        "status": "active",
        "close_time": "2099-01-01T00:00:00Z",
        "can_close_early": False,
        "notional_value_dollars": "1.0000",
        "yes_bid_dollars": "0.5000",
        "yes_ask_dollars": "0.5200",
        "yes_bid_size_fp": "10.00",
        "yes_ask_size_fp": "11.00",
    }
    payload.update(overrides)
    return payload


RESEARCH_HEADER = [
    "schema_version", "session_id", "starting_daily_pnl_usd",
    "starting_utc_day", "utc_day", "config_fingerprint",
    "code_fingerprint", "ts", "event_id", "ticker", "event", "detail",
    "close_ts", "can_close_early", "mid", "bid", "ask", "bid_qty",
    "ask_qty",
]


def research_row(*, cfg=None, session="S", starting_pnl=0,
                 starting_day="1970-01-01", day="1970-01-01", ts=1,
                 event_id="E", ticker="T", event="quote", detail="",
                 close_ts=4070908800.0, can_close_early="false",
                 mid=50, bid=49, ask=51, bid_qty=10, ask_qty=10):
    from research_log import config_fingerprint, code_fingerprint
    cfg = cfg or Config()
    if event != "quote":
        close_ts, can_close_early = "", ""
    return [5, session, starting_pnl, starting_day, day,
            config_fingerprint(cfg), code_fingerprint(), ts, event_id,
            ticker, event, detail, close_ts, can_close_early,
            mid, bid, ask, bid_qty, ask_qty]


# ================= contract tests (official fixtures) =================
def test_create_contract():
    cfg = Config()
    assert cfg.time_in_force == "immediate_or_cancel"
    assert cfg.self_trade_prevention_type == "taker_at_cross"
    b = build_order_body("KXT", "cid1", "bid", 20, Decimal("52.5"),
                         cfg.time_in_force, cfg.self_trade_prevention_type)
    assert b == {"ticker": "KXT", "client_order_id": "cid1", "side": "bid",
                 "count": "20", "price": "0.525",
                 "time_in_force": "immediate_or_cancel",
                 "self_trade_prevention_type": "taker_at_cross",
                 "reduce_only": False, "subaccount": 0}, b
    s = build_order_body("KXT", "cid2", "ask", Decimal("12.5"), 47,
                         "good_till_canceled", "maker", reduce_only=True)
    assert (s["side"] == "ask" and s["count"] == "12.5"
            and s["price"] == "0.47" and s["reduce_only"] is True)
    for legacy in ("action", "type", "yes_price", "bid", "ask"):
        assert legacy not in b, f"legacy key {legacy} present"
    assert b["side"] != "yes"
    four_decimal = build_order_body(
        "T", "four", "bid", 1, Decimal("52.55"),
        "immediate_or_cancel", "taker_at_cross")
    assert four_decimal["price"] == "0.5255"
    for bad in [("hold", 20, 50), ("bid", 0, 50), ("bid", 20, 0),
                ("bid", 20, 100), ("bid", Decimal("1.001"), 50),
                ("bid", 20, Decimal("52.555"))]:
        try:
            build_order_body("T", "c", bad[0], bad[1], bad[2],
                             "immediate_or_cancel", "taker_at_cross")
            assert False
        except SchemaError:
            pass
    for tif, stp in (("good_til_canceled", "taker_at_cross"),
                     ("immediate_or_cancel", "cancel_resting"),
                     ("gtc", "stp"), ("", "maker")):
        try:
            build_order_body("T", "c", "bid", 20, 50, tif, stp)
            assert False, (tif, stp)
        except SchemaError:
            pass
    print("PASS create contract: side=bid|ask, separate price, fp count, "
          "tif+stp required, no legacy keys")


def test_ack_vs_poll_contract():
    ack = parse_create_ack({"order_id": "O1", "client_order_id": "c1",
                            "fill_count": "0.00",
                            "remaining_count": "20.00", "ts_ms": 123})
    assert ack["order_id"] == "O1"          # NO status required on acks
    assert ack["fill_count"] == Decimal("0.00")
    assert ack["remaining_count"] == Decimal("20.00")
    assert ack["ts_ms"] == 123
    try:
        parse_create_ack({"order_id": "O1"}); assert False
    except SchemaError:
        pass
    o = parse_order({"order_id": "O1", "client_order_id": "c1",
                     "status": "resting", "ticker": "T",
                     "subaccount_number": 0,
                     "fill_count_fp": "7.50", "remaining_count_fp": "12.50",
                     "initial_count_fp": "20.00"})
    assert o["status"] == "resting"
    assert o["fill_count"] == Decimal("7.50")
    assert o["remaining_count"] == Decimal("12.50")
    assert o["initial_count"] == Decimal("20.00")
    try:
        parse_order({"order_id": "O1"}); assert False   # polls REQUIRE status
    except SchemaError:
        pass
    try:
        parse_order({"order_id": "O1", "status": "frozen"}); assert False
    except UnknownOrderState:
        pass
    assert CREATE_CANCEL_ENDPOINT == "/portfolio/events/orders"
    assert ORDERS_ENDPOINT == "/portfolio/orders"
    print("PASS ack-vs-poll contract: acks status-free, polls status-required")


def test_current_orderbook_contract():
    parsed = parse_orderbook_response({
        "orderbook_fp": {
            "yes_dollars": [["0.5250", "12.50"]],
            "no_dollars": [["0.4700", "7.25"]],
        }
    })
    assert parsed["yes"] == [(Decimal("52.5000"), Decimal("12.50"))]
    assert parsed["no"] == [(Decimal("47.0000"), Decimal("7.25"))]
    for malformed in ({}, {"orderbook_fp": {}},
                      {"orderbook_fp": {"yes_dollars": [["x", "1"]],
                                        "no_dollars": []}},
                      {"orderbook_fp": {"yes_dollars": [[None, "1.00"]],
                                        "no_dollars": []}},
                      {"orderbook_fp": {"yes_dollars": [["0.5", None]],
                                        "no_dollars": []}},
                      {"orderbook_fp": {"yes_dollars": [["0.5", "-1"]],
                                        "no_dollars": []}}):
        try:
            parse_orderbook_response(malformed)
            assert False, malformed
        except SchemaError:
            pass
    print("PASS current fixed-point orderbook response contract")


def test_endpoint_separation():
    """create/cancel hit the events endpoint; poll/list hit /portfolio/orders."""
    from kalshi_client import KalshiClient
    calls = []

    class SpyClient(KalshiClient):
        def __init__(self):
            self.cfg = Config()
            self.base = self.cfg.api_base

        def _request(self, method, endpoint, params=None, body=None,
                     auth=False):
            calls.append((method, endpoint))
            if method == "POST":
                return {"order_id": "O1", "client_order_id": "c",
                        "fill_count": "0.00", "remaining_count": "20.00",
                        "ts_ms": 123}
            if method == "DELETE":
                return {"order_id": "O1", "client_order_id": "c",
                        "reduced_by": "20.00", "ts_ms": 124}
            if endpoint.startswith("/portfolio/orders/"):
                return {"order": {
                    "order_id": "O1", "client_order_id": "c",
                    "status": "resting", "ticker": "T",
                    "subaccount_number": 0, "fill_count_fp": "0.00",
                    "remaining_count_fp": "20.00",
                    "initial_count_fp": "20.00"}}
            return {"orders": [], "cursor": ""}

    c = SpyClient()
    c.create_order(build_order_body("T", "c", "bid", 20, 50,
                                    "immediate_or_cancel",
                                    "taker_at_cross"))
    c.get_order("O1")
    c.get_open_orders()
    c.cancel_order("O1")
    assert calls[0] == ("POST", "/portfolio/events/orders"), calls[0]
    assert calls[1] == ("GET", "/portfolio/orders/O1"), calls[1]
    assert calls[2][0] == "GET" and calls[2][1] == "/portfolio/orders"
    assert calls[3] == ("DELETE", "/portfolio/events/orders/O1"), calls[3]
    print("PASS endpoint separation: events=create/cancel, orders=poll/list")


def test_http_boundary_wiring_and_strict_envelopes():
    """Exercise the real request/sign/parse boundary without network I/O."""
    from kalshi_client import KalshiClient

    class Response:
        def __init__(self, payload=None, invalid_json=False):
            self.payload = payload
            self.invalid_json = invalid_json

        def raise_for_status(self):
            return None

        def json(self):
            if self.invalid_json:
                raise ValueError("bad json")
            return self.payload

    class Session:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.responses.pop(0)

    class Key:
        def __init__(self):
            self.messages = []

        def sign(self, message, *args):
            self.messages.append(message)
            return b"signature"

    market = current_market()
    order = {"order_id": "OID", "client_order_id": "CID", "ticker": "T",
             "status": "executed", "fill_count_fp": "2.00",
             "remaining_count_fp": "0.00", "initial_count_fp": "2.00",
             "subaccount_number": 3}
    resting = {**order, "order_id": "REST", "status": "resting",
               "fill_count_fp": "0.00", "remaining_count_fp": "2.00"}
    fill = {"fill_id": "F", "order_id": "OID", "ticker": "T",
            "count_fp": "2.00", "yes_price_dollars": "0.520000",
            "fee_cost": "0.034567", "subaccount_number": 3, "ts": 123}
    responses = [
        Response({"exchange_active": True, "trading_active": True}),
        Response({"market": market}),
        Response({"order_id": "OID", "client_order_id": "CID",
                  "fill_count": "2.00", "remaining_count": "0.00",
                  "ts_ms": 123}),
        Response({"order": order}),
        Response({"orders": [resting], "cursor": ""}),
        Response({"balance": 1234, "balance_dollars": "12.3486",
                  "portfolio_value": 1500, "updated_ts": 123}),
        Response({"fills": [fill], "cursor": ""}),
        Response({"market_positions": [{"ticker": "T",
                                         "position_fp": "2.00"}],
                  "event_positions": []}),
        Response({"order_id": "OID", "client_order_id": "CID",
                  "reduced_by": "0.00", "ts_ms": 124}),
    ]
    cfg = Config(subaccount=3)
    cfg.api_key_id = "KEY"
    cfg.private_key_path = "/path/that/does/not/exist"
    client = KalshiClient(cfg)
    key = Key(); session = Session(responses)
    client._private_key = key; client.session = session

    assert client.get_exchange_status()["trading_active"] is True
    assert client.get_market("T")["event_ticker"] == "E"
    body = build_order_body("T", "CID", "bid", 2, 52,
                            "immediate_or_cancel", "taker_at_cross",
                            subaccount=3)
    assert client.create_order(body)["fill_count"] == Decimal("2.00")
    assert client.get_order("OID")["status"] == "executed"
    assert client.get_open_orders()[0]["order_id"] == "REST"
    parsed_balance = client.get_balance()
    assert parsed_balance["balance"] == 1234
    assert parsed_balance["balance_dollars"] == Decimal("12.3486")
    assert client.get_fills("OID")[0]["fee"] == Decimal("0.034567")
    assert client.get_positions()[0]["position"] == Decimal("2.00")
    assert client.cancel_order("OID")["reduced_by"] == Decimal("0.00")

    calls = session.calls
    assert all(call[1].startswith(cfg.api_base) for call in calls)
    assert [call[0] for call in calls] == [
        "GET", "GET", "POST", "GET", "GET", "GET", "GET", "GET",
        "DELETE"]
    assert calls[0][1] == cfg.api_base + "/exchange/status"
    assert calls[1][1] == cfg.api_base + "/markets/T"
    assert calls[2][1] == cfg.api_base + "/portfolio/events/orders"
    assert calls[3][1] == cfg.api_base + "/portfolio/orders/OID"
    assert calls[8][1] == cfg.api_base + "/portfolio/events/orders/OID"
    assert all(call[2]["timeout"] == 10 for call in calls)
    assert calls[2][0] == "POST" and calls[2][2]["json"] == body
    assert calls[4][2]["params"] == {"status": "resting",
                                     "subaccount": 3}
    assert calls[6][2]["params"] == {"subaccount": 3,
                                     "order_id": "OID"}
    assert calls[7][2]["params"] == {"subaccount": 3}
    assert calls[8][2]["params"] == {"subaccount": 3}
    assert calls[0][2]["headers"] == {} and calls[1][2]["headers"] == {}
    assert all(call[2]["headers"]["KALSHI-ACCESS-KEY"] == "KEY"
               for call in calls[2:])
    assert all(b"?" not in message for message in key.messages)

    bad = KalshiClient(cfg); bad._private_key = key
    bad.session = Session([Response(invalid_json=True)])
    try:
        bad.get_exchange_status(); assert False
    except SchemaError as error:
        assert "valid JSON" in str(error)
    bad.session = Session([Response(market)])
    try:
        bad.get_market("T"); assert False
    except SchemaError:
        pass
    bad.session = Session([Response(order)])
    try:
        bad.get_order("OID"); assert False
    except SchemaError:
        pass
    try:
        parse_create_ack({"order": {"order_id": "OID"}}); assert False
    except SchemaError:
        pass
    from schemas import parse_cancel_ack
    try:
        parse_cancel_ack({"order_id": "OID"}); assert False
    except SchemaError:
        pass
    try:
        parse_market({**market, "yes_bid_dollars": 0.5}); assert False
    except SchemaError:
        pass
    print("PASS HTTP boundary: URLs/auth/params/body/envelopes all validated")


def test_get_429_retries_with_exponential_backoff():
    """A transient read throttle must recover without hiding the delay."""
    import requests
    from kalshi_client import KalshiClient

    class Response:
        def __init__(self, status, payload=None):
            self.status_code = status
            self.payload = payload
            self.text = "too many requests" if status == 429 else "ok"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(
                    f"HTTP {self.status_code}", response=self)

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.responses = [
                Response(429), Response(429),
                Response(200, {"exchange_active": True,
                               "trading_active": True}),
            ]
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return self.responses.pop(0)

    client = KalshiClient(Config())
    client.session = Session()
    client._private_key = object()
    signed = []

    def sign(method, path):
        signed.append((method, path))
        return {"attempt": str(len(signed))}

    client._sign = sign
    delays = []
    client._sleep = delays.append

    result = client._request(
        "GET", "/exchange/status", params={"cursor": "SAME"}, auth=True)

    assert result == {"exchange_active": True, "trading_active": True}
    assert delays == [0.25, 0.5]
    assert len(client.session.calls) == 3
    assert [call[2]["params"] for call in client.session.calls] == [
        {"cursor": "SAME"}, {"cursor": "SAME"}, {"cursor": "SAME"}]
    assert [call[2]["headers"]["attempt"]
            for call in client.session.calls] == ["1", "2", "3"]
    print("PASS transient GET 429 retries with exponential backoff")


def test_get_429_retry_exhaustion_is_bounded():
    """A persistent throttle must escape to Safety after bounded retries."""
    import requests
    from kalshi_client import KalshiClient

    class Response:
        status_code = 429
        text = "too many requests"

        def raise_for_status(self):
            raise requests.HTTPError("HTTP 429", response=self)

    class Session:
        def __init__(self):
            self.calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            return Response()

    client = KalshiClient(Config())
    client.session = Session()
    delays = []
    client._sleep = delays.append
    try:
        client._request("GET", "/markets")
        assert False
    except requests.HTTPError as error:
        assert error.response.status_code == 429

    assert client.session.calls == 5
    assert delays == [0.25, 0.5, 1.0, 2.0]
    print("PASS persistent GET 429 escapes after bounded retries")


def test_mutating_429_is_never_retried():
    """Writes can be ambiguous, so POST/DELETE must remain single-attempt."""
    import requests
    from kalshi_client import KalshiClient

    class Response:
        status_code = 429
        text = "too many requests"

        def raise_for_status(self):
            raise requests.HTTPError("HTTP 429", response=self)

    class Session:
        def __init__(self):
            self.calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            return Response()

    for method in ("POST", "DELETE"):
        client = KalshiClient(Config())
        client.session = Session()
        delays = []
        client._sleep = delays.append
        try:
            client._request(method, "/portfolio/events/orders", body={})
            assert False
        except requests.HTTPError as error:
            assert error.response.status_code == 429
        assert client.session.calls == 1
        assert delays == []
    print("PASS mutating 429 responses are never automatically retried")


def test_portfolio_contracts_decimal():
    f = parse_fill({"count_fp": "12.50", "yes_price_dollars": "0.525",
                    "fee_cost": "0.09", "order_id": "abc",
                    "fill_id": "F", "ticker": "T",
                    "subaccount_number": 0, "ts": 123})
    assert f["count"] == Decimal("12.5")            # fractional preserved
    assert f["yes_price"] == Decimal("52.500")      # subpenny exact
    assert isinstance(f["yes_price"], Decimal)
    assert f["fee"] == Decimal("0.09")
    try:
        parse_fill({"count_fp": "-3", "yes_price_dollars": "0.5",
                    "fee_cost": "0", "order_id": "O"})
        assert False
    except SchemaError:
        pass
    for malformed in (
            {"count_fp": "1", "yes_price_dollars": "0.5",
             "fee_cost": "0.01"},
            {"count_fp": "1", "yes_price_dollars": "0.5",
             "fee_cost": "NaN", "order_id": "O"},
            {"count_fp": "1", "yes_price_dollars": "0.5000001",
             "fee_cost": "0.01", "order_id": "O"}):
        try:
            parse_fill(malformed); assert False, malformed
        except SchemaError:
            pass
    p = parse_position({"ticker": "T", "position_fp": "-15.25"})
    assert p["position"] == Decimal("-15.25")       # negative NO exposure ok
    m = parse_market(current_market(
        yes_bid_dollars="0.5250", yes_ask_dollars="0.5300",
        yes_bid_size_fp="7.50", yes_ask_size_fp="0.00"))
    assert (m["yes_bid"] == Decimal("52.5000")
            and m["yes_bid_size"] == Decimal("7.5")
            and m["yes_ask"] is None)
    try:
        parse_market(current_market(
            yes_bid_dollars="0.5000", yes_ask_dollars="0.6000",
            yes_bid_size_fp="-4.00", yes_ask_size_fp="1.00")); assert False
    except SchemaError:
        pass
    try:
        parse_market({"ticker": "T", "yes_bid_dollars": "0.5"}); assert False
    except SchemaError:
        pass
    print("PASS portfolio contracts: Decimal subpenny prices, fractional "
          "quantities, fp fields, negative-depth rejection")


def test_pagination():
    from kalshi_client import KalshiClient

    class PagedClient(KalshiClient):
        def __init__(self):
            self.cfg = Config()
            self.base = self.cfg.api_base
            self.pages = [
                {"fills": [{"count_fp": "1.00", "yes_price_dollars": "0.5",
                            "fee_cost": "0.01", "order_id": "O1",
                            "fill_id": "F1", "ticker": "T",
                            "subaccount_number": 0, "ts": 1}],
                 "cursor": "c1"},
                {"fills": [{"count_fp": "2.00", "yes_price_dollars": "0.5",
                            "fee_cost": "0.02", "order_id": "O2",
                            "fill_id": "F2", "ticker": "T",
                            "subaccount_number": 0, "ts": 2}],
                 "cursor": "c2"},
                {"fills": [{"count_fp": "3.00", "yes_price_dollars": "0.5",
                            "fee_cost": "0.03", "order_id": "O3",
                            "fill_id": "F3", "ticker": "T",
                            "subaccount_number": 0, "ts": 3}],
                 "cursor": ""},
            ]
            self.params_seen = []

        def _request(self, method, endpoint, params=None, body=None,
                     auth=False):
            self.params_seen.append(dict(params or {}))
            return self.pages[len(self.params_seen) - 1]

    c = PagedClient()
    fills = c.get_fills()
    assert [f["count"] for f in fills] == [Decimal(1), Decimal(2), Decimal(3)]
    assert c.params_seen[1]["cursor"] == "c1"
    assert c.params_seen[2]["cursor"] == "c2"
    print("PASS pagination: cursor followed across pages, results merged")


def test_pagination_fails_closed():
    from kalshi_client import KalshiClient
    import kalshi_client as kc

    class BrokenClient(KalshiClient):
        def __init__(self, responses):
            self.cfg = Config()
            self.responses = list(responses)
            self.calls = 0

        def _request(self, method, endpoint, params=None, body=None,
                     auth=False):
            response = self.responses[min(self.calls, len(self.responses) - 1)]
            self.calls += 1
            return response

    for responses in ([{}], [{"fills": []}],
                      [{"fills": [], "cursor": "same"},
                              {"fills": [], "cursor": "same"}]):
        try:
            BrokenClient(responses).get_fills()
            assert False, responses
        except SchemaError:
            pass

    old_cap = kc.MAX_PAGES
    kc.MAX_PAGES = 2
    try:
        try:
            BrokenClient([{"fills": [], "cursor": "c1"},
                          {"fills": [], "cursor": "c2"}]).get_fills()
            assert False
        except SchemaError:
            pass
    finally:
        kc.MAX_PAGES = old_cap
    print("PASS malformed/repeated/truncated pagination fails closed")


# ================= scriptable fake client =================
class FakeClient:
    def __init__(self):
        self.order_statuses = ["executed"]
        self.order_fill_counts = [Decimal(0)]
        self.order_snapshots = None
        self.order_calls = 0
        self.active_fill_count = Decimal(0)
        self.active_count = Decimal(20)
        self.ack_fill_count = None
        self.ack_remaining_count = None
        self.terminal_remaining_count = Decimal(0)
        self.ack_client_order_id = None
        self.active_client_order_id = None
        self.active_ticker = "T"
        self.fills = []
        self.fills_by_call = None
        self.fill_calls = 0
        self.cancel_error = None
        self.canceled = []
        self.positions = []
        self.positions_by_call = None
        self.position_calls = 0
        self.open_orders = []
        self.positions_error = None
        self.raise_on_create = None
        self.created_bodies = []

    def create_order(self, body):
        if self.raise_on_create:
            raise self.raise_on_create
        # enforce the official contract at the fake boundary too
        assert body["side"] in ("bid", "ask")
        assert isinstance(body["count"], str) and isinstance(body["price"], str)
        assert "time_in_force" in body and "self_trade_prevention_type" in body
        for legacy in ("action", "type", "yes_price"):
            assert legacy not in body
        self.created_bodies.append(body)
        self.active_client_order_id = body["client_order_id"]
        self.active_ticker = body["ticker"]
        self.active_count = Decimal(body["count"])
        self.active_fill_count = (self.order_fill_counts.pop(0)
                                  if len(self.order_fill_counts) > 1
                                  else self.order_fill_counts[0])
        ack_fill = (self.active_fill_count if self.ack_fill_count is None
                    else Decimal(str(self.ack_fill_count)))
        ack_remaining = (
            self.terminal_remaining_count
            if self.ack_remaining_count is None
            else Decimal(str(self.ack_remaining_count)))
        return parse_create_ack({
            "order_id": "OID1", "client_order_id": body["client_order_id"],
            "fill_count": f"{ack_fill:.2f}",
            "remaining_count": f"{ack_remaining:.2f}",
            "ts_ms": 123,
        } | ({"client_order_id": self.ack_client_order_id}
             if self.ack_client_order_id is not None else {}))

    def get_order(self, oid):
        self.order_calls += 1
        client_id = (self.active_client_order_id
                     or ({"O-LIVE": "c-live"}.get(oid))
                     or ("c" + oid[3:] if oid.startswith("OID") else "cid"))
        if self.order_snapshots is not None:
            idx = min(self.order_calls - 1, len(self.order_snapshots) - 1)
            raw = dict(self.order_snapshots[idx])
            for field in ("fill_count_fp", "remaining_count_fp",
                          "initial_count_fp"):
                if field in raw:
                    raw[field] = f"{Decimal(str(raw[field])):.2f}"
            return parse_order({"ticker": self.active_ticker,
                                "subaccount_number": 0,
                                "client_order_id": client_id, **raw})
        s = (self.order_statuses.pop(0) if len(self.order_statuses) > 1
             else self.order_statuses[0])
        remaining = (self.active_count - self.active_fill_count
                     if s == "resting" else self.terminal_remaining_count)
        return parse_order({
            "order_id": oid, "client_order_id": client_id, "status": s,
            "ticker": self.active_ticker,
            "subaccount_number": 0,
            "fill_count_fp": f"{self.active_fill_count:.2f}",
            "remaining_count_fp": f"{remaining:.2f}",
            "initial_count_fp": f"{self.active_count:.2f}",
        })

    def cancel_order(self, oid):
        if self.cancel_error:
            raise self.cancel_error
        self.canceled.append(oid)
        self.open_orders = [o for o in self.open_orders
                            if o.get("order_id") != oid]
        return {}

    def get_fills(self, order_id=None):
        self.fill_calls += 1
        if self.fills_by_call is not None:
            idx = min(self.fill_calls - 1, len(self.fills_by_call) - 1)
            raw = self.fills_by_call[idx]
        else:
            raw = self.fills
        return [parse_fill({"fill_id": f"F-{i}", "ticker": "T",
                            "subaccount_number": 0, "ts": 123, **f,
                            "count_fp": f"{Decimal(str(f['count_fp'])):.2f}"})
                for i, f in enumerate(raw)]

    def get_positions(self):
        if self.positions_error:
            raise self.positions_error
        self.position_calls += 1
        raw = self.positions
        if self.positions_by_call is not None:
            idx = min(self.position_calls - 1, len(self.positions_by_call) - 1)
            raw = self.positions_by_call[idx]
        parsed = [parse_position({
            **p, "position_fp": f"{Decimal(str(p['position_fp'])):.2f}"})
            for p in raw]
        return [p for p in parsed if p["position"] != 0]

    def get_open_orders(self):
        parsed = []
        for order in self.open_orders:
            raw = dict(order)
            for field in ("fill_count_fp", "remaining_count_fp",
                          "initial_count_fp"):
                raw[field] = f"{Decimal(str(raw[field])):.2f}"
            parsed.append(parse_order({"ticker": "T",
                                       "client_order_id": "cid",
                                       "subaccount_number": 0, **raw}))
        return parsed


class BookFeed:
    def __init__(self, bid=Decimal(50), bq=Decimal(100),
                 ask=Decimal(52), aq=Decimal(100)):
        self.book = (bid, bq, ask, aq)

    def top_of_book(self, t):
        return self.book

    def get_quote(self, ticker):
        bid, _, ask, _ = self.book
        return (bid + ask) / 2, bid, ask, time.time()


FILL52 = {"count_fp": "20", "yes_price_dollars": "0.52",
          "fee_cost": "0.35", "order_id": "OID1", "fill_id": "F52",
          "ticker": "T", "subaccount_number": 0, "ts": 123}
FILL5 = {"count_fp": "5", "yes_price_dollars": "0.52",
         "fee_cost": "0.09", "order_id": "OID1", "fill_id": "F5",
         "ticker": "T", "subaccount_number": 0, "ts": 123}


def make_exec(cfg, client, feed):
    j = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    cfg.paper_trading = False
    return LiveTestExecutor(cfg, client, feed, journal=j,
                            clock=time.time, sleep=lambda s: None), j


# ================= adversarial / regression tests =================
def test_delayed_fill_and_prejournal_reconcile():
    cfg = Config(); cfg.fill_timeout_s = 5.0
    c = FakeClient()
    c.order_statuses = ["resting", "resting", "executed"]
    c.order_fill_counts = [Decimal(20)]
    c.fills_by_call = [[], [FILL52]]
    c.positions_by_call = [[], [], [{"ticker": "T", "position_fp": "20"}]]
    ex, j = make_exec(cfg, c, BookFeed())
    price, filled, fee = ex.execute(
        "T", "BUY", 20, expected_pre_position=Decimal(0))
    assert filled == Decimal(20) and price == Decimal("52.00")
    events = [e["event"] for e in j.load()]
    assert events == ["submitted", "acked", "outcome"]
    outcome = j.load()[-1]
    assert Decimal(outcome["api_position"]) == Decimal(20)
    assert c.fill_calls >= 2
    print("PASS delayed fill: polled via /portfolio/orders; fills + "
          "authoritative positions reconciled BEFORE outcome journaled")


def test_contradictory_truth_stays_unresolved():
    """An executed order cannot resolve as zero-fill while the account gained
    the requested position."""
    cfg = Config(); cfg.reconcile_timeout_s = 0.0
    c = FakeClient()
    c.order_statuses = ["executed"]
    c.order_fill_counts = [Decimal(0)]
    c.fills = []
    c.positions_by_call = [[], [{"ticker": "T", "position_fp": "20"}]]
    ex, j = make_exec(cfg, c, BookFeed())
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
        assert False
    except HaltError as e:
        assert "did not converge" in str(e)
    assert j.unresolved()
    assert not any(e["event"] == "outcome" for e in j.load())
    print("PASS contradictory fills/positions halt and remain unresolved")


def test_ack_fill_is_binding_and_identity_checked():
    cfg = Config(); cfg.reconcile_timeout_s = 0.0
    c = FakeClient()
    c.ack_fill_count = Decimal(20)
    c.order_fill_counts = [Decimal(0)]
    c.order_statuses = ["canceled"]
    c.positions_by_call = [[], []]
    ex, j = make_exec(cfg, c, BookFeed())
    raised = None
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
    except HaltError as e:
        raised = e
    assert raised is not None and "did not converge" in str(raised)
    assert j.unresolved()

    c2 = FakeClient(); c2.ack_client_order_id = "wrong-client-id"
    ex2, j2 = make_exec(Config(), c2, BookFeed())
    raised2 = None
    try:
        ex2.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
    except HaltError as e:
        raised2 = e
    assert raised2 is not None and "client_order_id" in str(raised2)
    assert j2.unresolved()
    print("PASS create ack fill floor and client identity are binding")


def test_oversell_rejected_before_post():
    cfg = Config()
    c = FakeClient(); c.positions = [{"ticker": "T", "position_fp": "10"}]
    ex, j = make_exec(cfg, c, BookFeed())
    raised = None
    try:
        ex.execute("T", "SELL", 20, expected_pre_position=Decimal(10))
    except HaltError as e:
        raised = e
    assert raised is not None and "SELL" in str(raised)
    assert c.created_bodies == [] and j.load() == []
    print("PASS oversell is rejected before any order POST")


def test_order_quantities_and_fill_identity_must_converge():
    cfg = Config(); cfg.reconcile_timeout_s = 0.0
    c = FakeClient(); c.order_fill_counts = [Decimal(5)]
    c.order_snapshots = [{
        "order_id": "OID1", "status": "canceled",
        "fill_count_fp": "5", "remaining_count_fp": "999",
        "initial_count_fp": "20",
    }]
    c.fills = [FILL5]
    c.positions_by_call = [[], [{"ticker": "T", "position_fp": "5"}]]
    ex, j = make_exec(cfg, c, BookFeed())
    raised = None
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
    except HaltError as e:
        raised = e
    assert raised is not None and "did not converge" in str(raised)
    assert j.unresolved()

    c2 = FakeClient(); c2.order_fill_counts = [Decimal(5)]
    c2.order_statuses = ["canceled"]
    bad_fill = dict(FILL5); bad_fill["order_id"] = "SOME-OTHER-ORDER"
    c2.fills = [bad_fill]
    c2.positions_by_call = [[], [{"ticker": "T", "position_fp": "5"}]]
    ex2, j2 = make_exec(cfg, c2, BookFeed())
    raised2 = None
    try:
        ex2.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
    except HaltError as e:
        raised2 = e
    assert raised2 is not None and "wrong order" in str(raised2)
    assert j2.unresolved()
    print("PASS order quantities and fill IDs must match requested order")


def test_terminal_zero_requires_stability_grace():
    cfg = Config(); cfg.reconcile_timeout_s = 5.0
    c = FakeClient(); c.order_fill_counts = [Decimal(0)]
    c.ack_fill_count = Decimal(5)
    c.order_snapshots = [
        {"order_id": "OID1", "status": "canceled", "fill_count_fp": "0",
         "remaining_count_fp": "20", "initial_count_fp": "20"},
        {"order_id": "OID1", "status": "canceled", "fill_count_fp": "5",
         "remaining_count_fp": "0", "initial_count_fp": "20"},
        {"order_id": "OID1", "status": "canceled", "fill_count_fp": "5",
         "remaining_count_fp": "0", "initial_count_fp": "20"},
    ]
    c.fills_by_call = [[], [FILL5], [FILL5]]
    c.positions_by_call = [[], [],
                           [{"ticker": "T", "position_fp": "5"}],
                           [{"ticker": "T", "position_fp": "5"}]]
    ex, j = make_exec(cfg, c, BookFeed())
    _, filled, _ = ex.execute("T", "BUY", 20,
                              expected_pre_position=Decimal(0))
    assert filled == Decimal(5) and c.fill_calls >= 3
    assert Decimal(j.load()[-1]["filled"]) == Decimal(5)
    print("PASS terminal state must remain coherent across stable polls")


def test_cancel_polls_until_terminal():
    """Cancel is async: status stays resting for a few polls, then goes
    canceled — the executor must keep polling, then succeed."""
    cfg = Config(); cfg.fill_timeout_s = 0.0; cfg.cancel_timeout_s = 5.0
    c = FakeClient()
    c.order_statuses = ["resting", "resting", "resting", "canceled"]
    c.fills = []
    ex, j = make_exec(cfg, c, BookFeed())
    result = ex.execute("T", "BUY", 20,
                        expected_pre_position=Decimal(0))
    assert result is None and c.canceled == ["OID1"]
    assert j.load()[-1]["event"] == "outcome"
    print("PASS cancel: polled until terminal before journaling")


def test_cancel_limbo_halts():
    cfg = Config(); cfg.fill_timeout_s = 0.0; cfg.cancel_timeout_s = 0.0
    c = FakeClient(); c.order_statuses = ["resting"]
    ex, _ = make_exec(cfg, c, BookFeed())
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
        assert False
    except HaltError as e:
        assert "not terminal" in str(e)
    print("PASS cancel limbo (never terminal) halts")


def test_cancel_failure_halts():
    cfg = Config(); cfg.fill_timeout_s = 0.0; cfg.cancel_timeout_s = 0.0
    c = FakeClient(); c.order_statuses = ["resting"]
    c.cancel_error = RuntimeError("504")
    ex, _ = make_exec(cfg, c, BookFeed())
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
        assert False
    except HaltError as e:
        assert "cancel error=504" in str(e)
    print("PASS ambiguous cancel error is polled, then raises HaltError")


def test_late_fill_after_cancel():
    cfg = Config(); cfg.fill_timeout_s = 0.0; cfg.cancel_timeout_s = 5.0
    c = FakeClient(); c.order_statuses = ["resting", "canceled"]
    c.order_fill_counts = [Decimal(20)]
    c.fills_by_call = [[], [FILL52]]
    c.positions_by_call = [[], [], [{"ticker": "T", "position_fp": "20"}]]
    ex, j = make_exec(cfg, c, BookFeed())
    price, filled, fee = ex.execute(
        "T", "BUY", 20, expected_pre_position=Decimal(0))
    assert filled == Decimal(20)
    assert c.canceled == ["OID1"]
    assert Decimal(j.load()[-1]["filled"]) == Decimal(20)
    print("PASS late fill racing a cancel is captured and journaled")


def test_unknown_state_halts():
    cfg = Config(); cfg.fill_timeout_s = 5.0
    c = FakeClient(); c.order_statuses = ["frozen"]
    ex, _ = make_exec(cfg, c, BookFeed())
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
        assert False
    except HaltError as e:
        assert "unknown order status" in str(e)
    print("PASS unknown order state halts")


def test_submit_ambiguity():
    cfg = Config()
    c = FakeClient(); c.raise_on_create = RuntimeError("timeout")
    ex, j = make_exec(cfg, c, BookFeed())
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
        assert False
    except HaltError:
        pass
    assert j.load()[0]["event"] == "submitted" and j.unresolved()
    print("PASS ambiguous submit journaled pre-POST, halts, unresolved")


def test_ambiguity_blocks_flatten():
    """Unresolved journal entries must block auto-flatten: a SELL on top
    of an ambiguous SELL is a duplicate-SELL risk."""
    cfg = Config(); cfg.paper_trading = False
    j = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    j.record("submitted", client_order_id="cX", ticker="T", side="SELL",
             count=20, price=50)

    class TrackingExec:
        journal = j
        client = FakeClient()
        calls = []
        def execute(self, *a):
            self.calls.append(a)
            return None
    strat = ScalpStrategy(cfg)
    strat.record_fill("T", "BUY", Decimal(50), Decimal(20),
                      fee_usd(50, 20))
    ex = TrackingExec()
    ctx = Context(cfg, feed=None, strategy=strat, executor=ex,
                  log=None, safety=Safety(cfg))
    raised = None
    try:
        flatten_all(ctx)
    except ExposureError as e:
        raised = e
    assert raised is not None and "unresolved" in str(raised)
    assert ex.calls == []                       # no SELL attempted
    assert "T" in strat.positions
    print("PASS unresolved orders block auto-flatten (no duplicate SELLs)")


def test_flatten_partial_retries_and_authority():
    """Flatten retries partial fills to flat, sized by authoritative
    exchange positions."""
    cfg = Config(); cfg.paper_trading = False; cfg.flatten_retries = 3
    cfg.fill_timeout_s = 5.0
    c = FakeClient()
    c.order_statuses = ["executed"]
    c.order_fill_counts = [Decimal(12), Decimal(8)]
    c.positions_by_call = [
        [{"ticker": "T", "position_fp": "20"}],  # flatten sweep 1
        [{"ticker": "T", "position_fp": "20"}],  # executor pre 1
        [{"ticker": "T", "position_fp": "8"}],   # converge 1a
        [{"ticker": "T", "position_fp": "8"}],   # converge 1b
        [{"ticker": "T", "position_fp": "8"}],   # flatten sweep 2
        [{"ticker": "T", "position_fp": "8"}],   # executor pre 2
        [], [], [],                                   # converge 2 + final
    ]
    fills_seq = [[{"count_fp": "12", "yes_price_dollars": "0.50",
                   "fee_cost": "0.2", "order_id": "OID1"}],
                 [{"count_fp": "12", "yes_price_dollars": "0.50",
                   "fee_cost": "0.2", "order_id": "OID1"}],
                 [{"count_fp": "8", "yes_price_dollars": "0.50",
                   "fee_cost": "0.15", "order_id": "OID1"}],
                 [{"count_fp": "8", "yes_price_dollars": "0.50",
                   "fee_cost": "0.15", "order_id": "OID1"}]]
    call_holder = {"n": 0}
    real_get_fills = c.get_fills
    def seq_fills(order_id=None):
        idx = min(call_holder["n"], len(fills_seq) - 1)
        call_holder["n"] += 1
        return [parse_fill({"fill_id": f"SEQ-{i}", "ticker": "T",
                            "subaccount_number": 0, "ts": 123, **f,
                            "count_fp": f"{Decimal(str(f['count_fp'])):.2f}"})
                for i, f in enumerate(fills_seq[idx])]
    c.get_fills = seq_fills
    j = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    ex = LiveTestExecutor(cfg, c, BookFeed(), journal=j,
                          clock=time.time, sleep=lambda s: None)
    strat = ScalpStrategy(cfg)
    strat.record_fill("T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20))
    ctx = Context(cfg, feed=None, strategy=strat, executor=ex,
                  log=None, safety=Safety(cfg))
    flatten_all(ctx)
    assert "T" not in strat.positions, strat.positions
    assert len(c.created_bodies) == 2           # partial then remainder
    print("PASS flatten: partial fill retried to flat, authority-sized")


def test_restart_reconciliation():
    cfg = Config()
    jA = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    jA.record("submitted", client_order_id="c1", ticker="T", side="BUY",
              count="20", price="52", pre_position="0")
    jA.record("acked", client_order_id="c1", order_id="OID1",
              ack_fill="20", ack_remaining="0")
    cA = FakeClient(); cA.order_statuses = ["canceled"]
    cA.active_fill_count = Decimal(20); cA.fills = [FILL52]
    cA.positions = [{"ticker": "T", "position_fp": "20"}]
    try:
        Reconciler(cfg, cA, ScalpStrategy(cfg), jA).startup(); assert False
    except ExposureError:
        pass
    jB = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    jB.record("submitted", client_order_id="c2", ticker="T", side="BUY",
              count="20", price="52", pre_position="0")
    jB.record("acked", client_order_id="c2", order_id="OID2",
              ack_fill="0", ack_remaining="0")
    cB = FakeClient(); cB.order_statuses = ["resting", "canceled"]
    cB.fills = []
    Reconciler(cfg, cB, ScalpStrategy(cfg), jB).startup()
    assert "OID2" in cB.canceled and not jB.unresolved()
    jC = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    jC.record("submitted", client_order_id="c3", ticker="T", side="BUY",
              count="20", price="52", pre_position="0")
    try:
        Reconciler(cfg, FakeClient(), ScalpStrategy(cfg), jC).startup()
        assert False
    except ExposureError:
        pass
    print("PASS restart: filled-while-down refuses, stale resolved, "
          "unacked refuses")


def test_reconciliation_drains_later_orders_after_earlier_ambiguity():
    cfg = Config(); cfg.cancel_timeout_s = 0.0
    journal = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    journal.record("submitted", client_order_id="amb", ticker="X",
                   side="BUY", count="1", price="50", pre_position="0")
    journal.record("submitted", client_order_id="c2", ticker="T",
                   side="BUY", count="20", price="52", pre_position="0")
    journal.record("acked", client_order_id="c2", order_id="OID2",
                   ack_fill="0", ack_remaining="0")
    client = FakeClient(); client.order_statuses = ["canceled"]
    raised = None
    try:
        Reconciler(cfg, client, ScalpStrategy(cfg), journal).startup()
    except ExposureError as error:
        raised = error
    assert raised is not None and "amb" in str(raised)
    assert any(e.get("event") == "outcome"
               and e.get("order_id") == "OID2" for e in journal.load())
    assert any(e.get("client_order_id") == "amb"
               for e in journal.unresolved())
    print("PASS reconciliation drains known orders despite earlier ambiguity")


def test_unapplied_filled_outcome_blocks_restart():
    cfg = Config()
    journal = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    journal.record("outcome", order_id="OID-LOSS", client_order_id="CID",
                   ticker="T", side="SELL", status="executed",
                   filled="20.00", avg_price="40.00", fee="0.30",
                   api_position="0.00", effective_ts=123.0)
    raised = None
    try:
        Reconciler(cfg, FakeClient(), ScalpStrategy(cfg), journal).startup()
    except ExposureError as error:
        raised = error
    assert raised is not None and "not durably applied" in str(raised)
    journal.record("applied", order_id="OID-LOSS")
    Reconciler(cfg, FakeClient(), ScalpStrategy(cfg), journal).startup()
    print("PASS crash-window filled outcome blocks until durably applied")


def test_startup_cancel_race_and_errors():
    cfg = Config()
    j = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    j.record("submitted", client_order_id="c9", ticker="T", side="BUY",
             count="20", price="52", pre_position="0")
    j.record("acked", client_order_id="c9", order_id="OID9",
             ack_fill="20", ack_remaining="0")
    c = FakeClient(); c.order_statuses = ["resting", "canceled"]
    c.active_fill_count = Decimal(20)
    fill9 = dict(FILL52); fill9["order_id"] = "OID9"
    c.fills_by_call = [[], [fill9], [fill9]]
    c.positions_by_call = [[], [{"ticker": "T", "position_fp": "20"}],
                           [{"ticker": "T", "position_fp": "20"}]]
    try:
        Reconciler(cfg, c, ScalpStrategy(cfg), j).startup(); assert False
    except ExposureError as e:
        assert "filled" in str(e) or "converge" in str(e)
    c2 = FakeClient(); c2.positions_error = RuntimeError("500")
    try:
        Reconciler(cfg, c2, ScalpStrategy(cfg),
                   OrderJournal(tempfile.mktemp(suffix=".jsonl"))).startup()
        assert False
    except ExposureError as e:
        assert "position reconciliation failed" in str(e)
    c3 = FakeClient(); c3.positions = [{"ticker": "T", "position_fp": "10"}]
    try:
        Reconciler(cfg, c3, ScalpStrategy(cfg),
                   OrderJournal(tempfile.mktemp(suffix=".jsonl"))).startup()
        assert False
    except ExposureError:
        pass
    print("PASS startup: cancel race, API error, and exposure all refuse")


def test_startup_cancel_must_reach_terminal():
    cfg = Config(); cfg.cancel_timeout_s = 0.0
    j = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    j.record("submitted", client_order_id="c-live", ticker="T", side="BUY",
             count="20", price="52", pre_position="0")
    j.record("acked", client_order_id="c-live", order_id="O-LIVE",
             ack_fill="0", ack_remaining="0")
    c = FakeClient(); c.order_statuses = ["resting"]
    raised = None
    try:
        Reconciler(cfg, c, ScalpStrategy(cfg), j).startup()
    except ExposureError as e:
        raised = e
    assert raised is not None and "not terminal" in str(raised)
    assert j.unresolved() and not any(
        e["event"] == "outcome" for e in j.load())
    print("PASS startup cannot resolve cancel-acknowledged order still live")


def test_obsolete_pending_order_state_fails_closed():
    try:
        parse_order({"order_id": "O-PENDING", "status": "pending",
                     "ticker": "T", "subaccount_number": 0,
                     "fill_count_fp": "0.00",
                     "remaining_count_fp": "20.00",
                     "initial_count_fp": "20.00"})
        assert False
    except UnknownOrderState as error:
        assert error.status == "pending"
    print("PASS obsolete pending order status fails closed")


def test_open_order_listing_queries_only_resting_in_one_subaccount():
    from kalshi_client import KalshiClient

    class ListingClient(KalshiClient):
        def __init__(self):
            self.cfg = Config()
            self.params = None

        def _paginate(self, endpoint, list_key, params=None, auth=False):
            self.params = params
            return [
                {"order_id": "R", "status": "resting",
                 "client_order_id": "c", "ticker": "T",
                 "subaccount_number": 0,
                 "fill_count_fp": "0.00", "remaining_count_fp": "2.00",
                 "initial_count_fp": "2.00"},
            ]

    c = ListingClient()
    orders = c.get_open_orders()
    assert {o["order_id"] for o in orders} == {"R"}
    assert c.params == {"status": "resting", "subaccount": 0}
    print("PASS open-order listing is resting-only and subaccount-scoped")


class AuthoritativeFlattenClient:
    def __init__(self, position):
        self.position = Decimal(str(position))

    def get_positions(self):
        if self.position == 0:
            return []
        return [{"ticker": "T", "position": self.position}]


class AuthoritativeFlattenExecutor:
    def __init__(self, cfg, position, fills):
        self.cfg = cfg
        self.client = AuthoritativeFlattenClient(position)
        self.journal = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
        self.fills = [Decimal(str(x)) for x in fills]
        self.calls = []

    def execute(self, ticker, side, qty, expected_pre_position=None):
        assert Decimal(str(expected_pre_position)) == self.client.position
        self.calls.append((ticker, side, Decimal(str(qty))))
        filled = self.fills.pop(0) if self.fills else Decimal(0)
        if not filled:
            return None
        if side == "SELL":
            self.client.position -= filled
        else:
            self.client.position += filled
        return Decimal(50), filled, Decimal(0)


def test_flatten_refuses_exchange_only_exposure_without_cost_basis():
    cfg = Config(); cfg.paper_trading = False; cfg.flatten_retries = 3
    ex = AuthoritativeFlattenExecutor(cfg, 20, [12, 8])
    strat = ScalpStrategy(cfg)       # deliberately has no local position
    ctx = Context(cfg, feed=None, strategy=strat, executor=ex,
                  log=None, safety=Safety(cfg))
    try:
        flatten_all(ctx)
        assert False
    except ExposureError as error:
        assert "cost basis" in str(error)
    assert ex.calls == []
    assert ex.client.get_positions() == [{"ticker": "T",
                                          "position": Decimal(20)}]
    print("PASS flatten refuses exchange-only exposure with unknown basis")


def test_flatten_nonflat_final_state_raises():
    cfg = Config(); cfg.paper_trading = False; cfg.flatten_retries = 2
    ex = AuthoritativeFlattenExecutor(cfg, 20, [])
    strategy = ScalpStrategy(cfg)
    strategy.record_fill("T", "BUY", Decimal(50), Decimal(20),
                         fee_usd(50, 20))
    ctx = Context(cfg, feed=None, strategy=strategy, executor=ex,
                  log=None, safety=Safety(cfg))
    raised = None
    try:
        flatten_all(ctx)
    except ExposureError as e:
        raised = e
    assert raised is not None and "not flat" in str(raised)
    assert len(ex.calls) == 2
    print("PASS flatten raises when final authoritative account is non-flat")


def test_periodic_resolves_orders_and_reports_remaining_ambiguity():
    cfg = Config(); cfg.reconcile_every_s = 0.0
    j = OrderJournal(tempfile.mktemp(suffix=".jsonl"))
    j.record("submitted", client_order_id="amb", ticker="T", side="BUY",
             count=20, price=50)
    s = Safety(cfg)
    Reconciler(cfg, FakeClient(), ScalpStrategy(cfg), j).periodic(s)
    assert s.tripped and "unresolved" in s.tripped_reason

    c = FakeClient()
    c.open_orders = [{"order_id": "P", "status": "resting",
                      "fill_count_fp": "0", "remaining_count_fp": "2",
                      "initial_count_fp": "2"}]
    s2 = Safety(cfg)
    Reconciler(cfg, c, ScalpStrategy(cfg),
               OrderJournal(tempfile.mktemp(suffix=".jsonl"))).periodic(s2)
    assert not s2.tripped and c.canceled == ["P"]
    print("PASS periodic reconciliation resolves strays and reports ambiguity")


def test_shutdown_cancel_is_terminal_verified():
    cfg = Config(); cfg.cancel_timeout_s = 0.0
    c = FakeClient()
    c.open_orders = [{"order_id": "R", "status": "resting",
                      "fill_count_fp": "0", "remaining_count_fp": "2",
                      "initial_count_fp": "2"}]
    c.order_statuses = ["resting"]
    raised = None
    try:
        Reconciler(cfg, c, ScalpStrategy(cfg),
                   OrderJournal(tempfile.mktemp(suffix=".jsonl"))).shutdown()
    except ExposureError as e:
        raised = e
    assert raised is not None and "not terminal" in str(raised)
    print("PASS shutdown cancel is polled and failure is surfaced")


def test_runtime_error_and_ctrl_c_preserve_honest_paper_residuals():
    from collections import defaultdict
    from bot import run_loop

    class LoopFeed:
        def __init__(self):
            self.history = defaultdict(list)
            self.last_book = (Decimal(39), Decimal(20),
                              Decimal(41), Decimal(20))

        def get_quote(self, ticker):
            observed_at = time.time()
            self.history[ticker].append((observed_at, Decimal(40)))
            return Decimal(40), Decimal(39), Decimal(41), observed_at

        def top_of_book(self, ticker):
            return self.last_book

        def stale_tickers(self, tickers):
            return []

    class CloseExecutor:
        def __init__(self):
            self.calls = []

        def execute(self, ticker, side, qty, **kwargs):
            self.calls.append((ticker, side, qty))
            return Decimal(39), Decimal(str(qty)), Decimal(0)

    class OneShotFailureLog:
        def __init__(self, error):
            self.error = error

        def tick(self, *args, **kwargs):
            error, self.error = self.error, None
            if error:
                raise error

        def trade(self, *args):
            pass

    for error in (RuntimeError("logger disk error"), KeyboardInterrupt()):
        cfg = Config(); cfg.poll_interval = 0
        strat = ScalpStrategy(cfg)
        strat.record_fill("T", "BUY", Decimal(50), Decimal(2), Decimal(0))
        ex = CloseExecutor()
        ctx = Context(cfg, LoopFeed(), strat, ex,
                      OneShotFailureLog(error), Safety(cfg))
        ok = run_loop(ctx, None, ["T"], sleep=lambda _: None)
        assert ok is isinstance(error, KeyboardInterrupt)
        assert strat.positions["T"].contracts == Decimal(2)
        assert ex.calls == []
    print("PASS runtime error/Ctrl-C preserve paper residuals without fake fills")


def test_per_market_quarantine():
    cfg = Config(); cfg.max_consec_errors = 3
    s = Safety(cfg)
    for _ in range(3):
        s.error("boom", ticker="A")
    assert "A" in s.quarantined and not s.tripped   # others continue
    assert not s.all_quarantined(["A", "B"])
    for _ in range(3):
        s.error("boom", ticker="B")
    assert s.all_quarantined(["A", "B"])            # bot trips on this
    s2 = Safety(cfg)
    for _ in range(3):
        s2.error("global boom")                     # unattributed: global trip
    assert s2.tripped
    print("PASS per-market errors quarantine that market; global errors halt")


def test_critical_market_errors_halt_instead_of_quarantine():
    from collections import defaultdict
    from bot import run_loop

    class BrokenFeed:
        def __init__(self):
            self.history = defaultdict(list)
            self.calls = 0

        def get_quote(self, ticker):
            self.calls += 1
            raise RuntimeError("transport failure")

        def stale_tickers(self, tickers):
            return []

        def top_of_book(self, ticker):
            return None, None, None, None

    for pending_only in (False, True):
        cfg = Config(max_consec_errors=3)
        feed = BrokenFeed()
        strategy = ScalpStrategy(cfg)
        executor = Executor(cfg, None, feed)
        if pending_only:
            executor.submit_paper("T", "BUY", 1, now=0)
        else:
            strategy.record_fill(
                "T", "BUY", Decimal(50), Decimal(1), Decimal(0), now=0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 1.0)
        assert not run_loop(ctx, None, ["T"], sleep=lambda _: None)
        assert "exposed/pending" in ctx.safety.tripped_reason
        assert "T" not in ctx.safety.quarantined and feed.calls == 1
    print("PASS exposed/pending quote errors halt; never quarantine exposure")


def test_real_order_gate_is_inside_executor():
    from executor import OrderExecutionDisabled
    cfg = Config(); cfg.paper_trading = False
    c = FakeClient()
    ex = Executor(cfg, c, BookFeed(),
                  journal=OrderJournal(tempfile.mktemp(suffix=".jsonl")))
    raised = None
    try:
        ex.execute("T", "BUY", 20, expected_pre_position=Decimal(0))
    except OrderExecutionDisabled as e:
        raised = e
    assert raised is not None
    assert c.position_calls == 0 and c.created_bodies == []
    print("PASS config alone cannot reach any real-order API path")


def test_live_executor_requires_fresh_book_for_unseen_ticker():
    class FreshFeed:
        def __init__(self):
            self.calls = []
            self.book = (None, None, None, None)

        def get_quote(self, ticker):
            self.calls.append(ticker)
            self.book = (Decimal(49), Decimal(20),
                         Decimal(51), Decimal(20))
            return Decimal(50), Decimal(49), Decimal(51), time.time()

        def top_of_book(self, ticker):
            return self.book

    cfg = Config()
    client = FakeClient(); client.order_statuses = ["canceled"]
    feed = FreshFeed()
    executor, _ = make_exec(cfg, client, feed)
    assert executor.execute("UNSEEN", "BUY", 20,
                            expected_pre_position=Decimal(0)) is None
    assert feed.calls == ["UNSEEN"]
    assert client.created_bodies[0]["price"] == "0.51"
    print("PASS real executor refreshes an unseen ticker before pricing")


def test_run_session_real_config_stops_before_api():
    from bot import run_session

    class NoMutationClient:
        def __init__(self):
            self.calls = []

        def get_open_orders(self):
            self.calls.append("get_open_orders")
            raise AssertionError("real mode reached API")

    cfg = Config(); cfg.paper_trading = False
    client = NoMutationClient()
    assert run_session(cfg, client) == 1
    assert client.calls == []
    print("PASS real config is rejected before reconciliation or API access")


def test_global_error_not_erased_and_auth_rate_limit_halt():
    import requests
    cfg = Config(); cfg.max_consec_errors = 2
    s = Safety(cfg)
    s.error("global one")
    s.ok("healthy-market")
    s.error("global two")
    assert s.tripped and "consecutive" in s.tripped_reason

    for status in (401, 403, 429):
        response = requests.Response(); response.status_code = status
        error = requests.HTTPError(f"HTTP {status}", response=response)
        sx = Safety(cfg)
        sx.handle_exception(error, ticker="T")
        assert sx.tripped and ("authentication" in sx.tripped_reason
                               or "rate limit" in sx.tripped_reason)
        assert "T" not in sx.quarantined
    print("PASS healthy ticker cannot erase global errors; auth/rate halt")


def test_global_halt_stops_current_market_sweep_immediately():
    from collections import defaultdict
    from bot import run_loop

    class Response:
        status_code = 429

    class RateLimited(Exception):
        response = Response()

    class Feed:
        def __init__(self):
            self.calls = []
            self.history = defaultdict(list)

        def get_quote(self, ticker):
            self.calls.append(ticker)
            if ticker == "FIRST":
                raise RateLimited("slow down")
            return Decimal(50), Decimal(49), Decimal(51), time.time()

        def top_of_book(self, ticker):
            return Decimal(49), Decimal(20), Decimal(51), Decimal(20)

        def stale_tickers(self, tickers):
            return []

    cfg = Config(); cfg.poll_interval = 0
    feed = Feed()
    ctx = Context(cfg, feed, ScalpStrategy(cfg),
                  Executor(cfg, None, feed), None, Safety(cfg))
    assert not run_loop(ctx, None, ["FIRST", "SECOND"], sleep=lambda _: None)
    assert feed.calls == ["FIRST"]
    assert "rate limit" in ctx.safety.tripped_reason
    print("PASS global halt stops the current multi-market sweep")


def test_loss_breach_stops_before_next_market_action():
    from collections import defaultdict
    from bot import run_loop

    class Feed:
        def __init__(self):
            self.calls = []
            self.history = defaultdict(list)
            self.books = {
                "LOSS": (Decimal(10), Decimal(20), Decimal(12), Decimal(20)),
                "NEXT": (Decimal(49), Decimal(20), Decimal(51), Decimal(20)),
            }

        def get_quote(self, ticker):
            self.calls.append(ticker)
            bid, _, ask, _ = self.books[ticker]
            mid = (bid + ask) / 2
            ts = time.time()
            self.history[ticker].append((ts, mid))
            return mid, bid, ask, ts

        def top_of_book(self, ticker):
            return self.books[ticker]

        def stale_tickers(self, tickers):
            return []

    cfg = Config(); cfg.poll_interval = 0; cfg.max_daily_loss_usd = 1
    feed = Feed()
    strat = ScalpStrategy(cfg)
    strat.record_fill("LOSS", "BUY", Decimal(90), Decimal(20), Decimal(0))
    ctx = Context(cfg, feed, strat, Executor(cfg, None, feed), None,
                  Safety(cfg))
    assert not run_loop(ctx, None, ["LOSS", "NEXT"], sleep=lambda _: None)
    assert feed.calls == ["LOSS"]
    assert "loss limit" in ctx.safety.tripped_reason
    print("PASS loss breach stops before the next market can act")


def test_stale_initial_quotes_halt():
    from market_data import PriceFeed
    cfg = Config(); cfg.stale_data_s = 0.05
    feed = PriceFeed(cfg, client=None)
    feed.subscribe(["A"])
    s = Safety(cfg)
    time.sleep(0.06)
    s.check_staleness(feed, ["A"])
    assert s.tripped
    print("PASS stale-from-start quotes trip the halt")


def test_missing_bid_risk_halts():
    """An open position whose market stops quoting cannot be valued —
    that must halt, not silently freeze the mark."""
    cfg = Config(); cfg.stale_data_s = 0.05
    strat = ScalpStrategy(cfg)
    strat.record_fill("T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20))
    ctx = Context(cfg, feed=None, strategy=strat, executor=None,
                  log=None, safety=Safety(cfg))
    ctx.latest_bid["T"] = Decimal(49)
    ctx.bid_ts["T"] = time.time()
    check_loss_limit(ctx)
    assert not ctx.safety.tripped
    ctx.bid_ts["T"] = time.time() - 1.0        # bid went missing/stale
    check_loss_limit(ctx)
    assert ctx.safety.tripped and "cannot value" in ctx.safety.tripped_reason
    print("PASS missing/stale bid on an open position halts")


def test_open_loss_limit_fee_aware():
    cfg = Config(); cfg.max_daily_loss_usd = 3.0
    strat = ScalpStrategy(cfg)
    strat.record_fill("T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20))
    ctx = Context(cfg, feed=BookFeed(bid=Decimal(45), bq=Decimal(20)),
                  strategy=strat, executor=None,
                  log=None, safety=Safety(cfg))
    ctx.bid_ts["T"] = time.time()
    ctx.latest_bid["T"] = Decimal(45)
    check_loss_limit(ctx)
    assert not ctx.safety.tripped
    ctx.latest_bid["T"] = Decimal(38)   # -2.4 gross but ~-3.1 net: breach
    ctx.feed.book = (Decimal(38), Decimal(20), Decimal(40), Decimal(20))
    check_loss_limit(ctx)
    assert ctx.safety.tripped
    print("PASS loss limit counts open losses net of entry+exit fees")


def test_open_loss_mark_respects_zero_depth():
    cfg = Config(); cfg.max_daily_loss_usd = 4.0
    strat = ScalpStrategy(cfg)
    strat.record_fill("T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20))
    feed = BookFeed(bid=Decimal(80), bq=Decimal(0),
                    ask=Decimal(82), aq=Decimal(20))
    ctx = Context(cfg, feed=feed, strategy=strat, executor=None,
                  log=None, safety=Safety(cfg))
    ctx.latest_bid["T"] = Decimal(80); ctx.bid_ts["T"] = time.time()
    check_loss_limit(ctx)
    assert ctx.safety.tripped and "loss limit" in ctx.safety.tripped_reason
    print("PASS zero-depth bid cannot mask open-position loss")


def test_daily_pnl_ledger_survives_strategy_restart():
    from pnl_ledger import DailyPnlLedger
    path = tempfile.mktemp(suffix=".jsonl")
    ledger = DailyPnlLedger(path)
    cfg = Config()
    first = ScalpStrategy(cfg, ledger=ledger)
    first.record_fill("T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20))
    first.record_fill("T", "SELL", Decimal(40), Decimal(20), fee_usd(40, 20))
    second = ScalpStrategy(cfg, ledger=DailyPnlLedger(path))
    assert second.realized_pnl == first.realized_pnl < 0
    print("PASS daily realized loss survives process restart")


def test_daily_pnl_rolls_at_utc_midnight_and_is_idempotent():
    from datetime import datetime, timezone
    from pnl_ledger import DailyPnlLedger

    before = datetime(2026, 1, 1, 23, 59, 59,
                      tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 1, 2, 0, 0, 1,
                     tzinfo=timezone.utc).timestamp()
    now = [before]
    path = tempfile.mktemp(suffix=".jsonl")
    ledger = DailyPnlLedger(path, clock=lambda: now[0])
    assert ledger.record_once("prior-gain", Decimal(100), ts=before)
    strat = ScalpStrategy(Config(), ledger=ledger)
    assert strat.realized_pnl == Decimal(100)

    now[0] = after
    assert ledger.record_once("today-loss", Decimal(-31), ts=after)
    assert not ledger.record_once("today-loss", Decimal(-31), ts=after)
    strat.refresh_daily_pnl(after)
    assert strat.realized_pnl == Decimal(-31)
    try:
        ledger.record_once("today-loss", Decimal(-30), ts=after)
        assert False
    except ValueError as error:
        assert "conflicting" in str(error)
    print("PASS UTC rollover drops prior-day gains; ledger events idempotent")


def test_exclusive_process_lock():
    from process_lock import ProcessLock, ProcessLockError
    path = tempfile.mktemp(suffix=".lock")
    first = ProcessLock(path); second = ProcessLock(path)
    first.acquire()
    raised = None
    try:
        second.acquire()
    except ProcessLockError as e:
        raised = e
    finally:
        first.release(); second.release()
    assert raised is not None
    print("PASS exclusive process/account lock rejects second bot instance")


def test_durable_state_paths_are_absolute_and_cwd_independent():
    from order_journal import OrderJournal
    from pnl_ledger import DailyPnlLedger
    from process_lock import ProcessLock

    root = tempfile.mkdtemp()
    first_cwd = tempfile.mkdtemp()
    second_cwd = tempfile.mkdtemp()
    original = os.getcwd()
    try:
        os.chdir(first_cwd)
        first = Config(state_root=root, subaccount=3)
        os.chdir(second_cwd)
        second = Config(state_root=root, subaccount=3)
    finally:
        os.chdir(original)
    assert first.process_lock_path == second.process_lock_path
    assert first.order_journal_path == second.order_journal_path
    assert first.daily_pnl_path == second.daily_pnl_path
    assert all(os.path.isabs(path) for path in (
        first.process_lock_path, first.order_journal_path,
        first.daily_pnl_path))
    assert len({os.path.dirname(first.process_lock_path),
                os.path.dirname(first.order_journal_path),
                os.path.dirname(first.daily_pnl_path)}) == 1
    for constructor, filename in ((ProcessLock, "lock"),
                                  (OrderJournal, "orders"),
                                  (DailyPnlLedger, "pnl")):
        try:
            constructor(filename)
            assert False
        except ValueError as error:
            assert "absolute" in str(error)
    assert "ACCOUNT" not in first.process_lock_path
    original_subaccount = first.subaccount
    first.subaccount = 4
    try:
        first.validate()
        assert False
    except ValueError as error:
        assert "state identity" in str(error)
    first.subaccount = original_subaccount
    print("PASS durable safety state has one absolute account-safe namespace")


def test_stable_train_test_split():
    """Production split is stable and keeps an event's contracts together."""
    from analyze import split_bucket, split_markets
    groups = {"E1-YES": "EVENT-1", "E1-MARGIN": "EVENT-1",
              "E2-YES": "EVENT-2"}
    train1, test1 = split_markets(set(groups), groups)
    assert (("E1-YES" in train1) == ("E1-MARGIN" in train1))
    assert split_bucket("EVENT-1") == split_bucket("EVENT-1")
    groups2 = dict(groups, **{"E3-YES": "EVENT-3"})
    train2, test2 = split_markets(set(groups2), groups2)
    for ticker in groups:
        assert (ticker in train1) == (ticker in train2)
        assert (ticker in test1) == (ticker in test2)
    print("PASS production event split is stable and group-safe")


def test_signal_rejects_future_history():
    assert dip_signal([(101.0, Decimal(70))], 100.0, Decimal(50),
                      7, 45) is None
    assert dip_signal([(100.0, Decimal(70))], 100.0, Decimal(50),
                      7, 45) is None
    assert dip_signal([(99.0, Decimal(70))], 100.0, Decimal(50),
                      7, 45) == Decimal(20)
    print("PASS future/equal-time history cannot trigger a signal")


def test_analyzer_preserves_order_and_censors_horizons():
    import csv as _csv
    from analyze import load, markouts
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(RESEARCH_HEADER)
        w.writerow(research_row(ts=1, mid=60, bid=59, ask=61))
        w.writerow(research_row(ts=10, mid=50, bid=49, ask=51))
        w.writerow(research_row(ts=12, mid=52, bid=51, ask=53))
        w.writerow(research_row(
            ts=13, event_id="", ticker="", event="session_end",
            detail="operator interrupt", mid="", bid="", ask="",
            bid_qty="", ask_qty=""))
    series, groups = load(path)
    assert [p[0] for p in series["T"]] == [1.0, 10.0, 12.0]
    assert groups == {"T": "E"}
    marks = markouts(series["T"], 1)
    assert 1 in marks and 5 not in marks and 300 not in marks
    far = [(0.0, Decimal(50), Decimal(49), Decimal(51)),
           (100.0, Decimal(50), Decimal(49), Decimal(51))]
    assert markouts(far, 0) == {}
    print("PASS analyzer preserves row order and omits censored horizons")


def test_analyzer_requires_a_clean_terminal_record():
    import csv as _csv
    from analyze import load

    for terminal in (None, "session_halt"):
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w", newline="") as handle:
            writer = _csv.writer(handle)
            writer.writerow(RESEARCH_HEADER)
            writer.writerow(research_row())
            if terminal:
                writer.writerow(research_row(
                    ts=2, event_id="", ticker="", event=terminal,
                    detail="runtime failure", mid="", bid="", ask="",
                    bid_qty="", ask_qty=""))
        try:
            load(path)
            assert False, terminal
        except ValueError as error:
            assert ("terminal" in str(error)
                    or "halted" in str(error))
    print("PASS analyzer rejects missing or halted session terminals")


def test_research_log_preserves_no_quote_and_event_group():
    import csv as _csv
    from research_log import ResearchLog
    directory = tempfile.mkdtemp()
    log = ResearchLog(directory, clock=lambda: 123.5, session_id="SESSION-A")
    log.tick("T", None, None, None, None, None,
             ts=123.5, group_id="EVENT-7", event="no_quote")
    with open(log.tick_path) as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["schema_version"] == "5"
    assert rows[0]["session_id"] == "SESSION-A"
    assert rows[0]["starting_daily_pnl_usd"] == "0"
    assert rows[0]["starting_utc_day"] == "1970-01-01"
    assert rows[0]["utc_day"] == "1970-01-01"
    assert rows[0]["ts"] == "123.5"
    assert rows[0]["event_id"] == "EVENT-7"
    assert rows[0]["event"] == "no_quote"
    assert rows[0]["mid"] == ""
    assert len(rows[0]["config_fingerprint"]) == 64
    assert len(rows[0]["code_fingerprint"]) == 64
    assert "ticks_v5_" in log.tick_path
    from replay import replay
    result = replay(log.tick_path)
    assert result["data_gaps"] == 2 and not result["evaluable"]
    second = ResearchLog(directory, clock=lambda: 123.5,
                         session_id="SESSION-B")
    assert second.tick_path != log.tick_path
    try:
        ResearchLog(directory, clock=lambda: 123.5, session_id="SESSION-A")
        assert False
    except FileExistsError:
        pass
    print("PASS research log preserves gaps/grouping and isolates sessions")


def test_analyzer_rejects_legacy_per_ticker_split():
    import csv as _csv
    from analyze import load
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts", "ticker", "mid", "bid", "ask"])
        w.writerow([1, "T", 50, 49, 51])
    raised = None
    try:
        load(path)
    except ValueError as e:
        raised = e
    assert raised is not None and "event_id" in str(raised)
    print("PASS analyzer rejects legacy logs lacking event-level grouping")


def test_malformed_quote_rows_disqualify_research():
    import csv as _csv
    from analyze import load
    from replay import load_log

    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        writer.writerow(research_row(ask=""))
    try:
        load(path)
        assert False
    except ValueError as error:
        assert "malformed quote" in str(error)
    rows, gaps = load_log(path)
    assert rows == [] and gaps == 2
    print("PASS malformed quote row fails analysis and disqualifies replay")


def test_analyzer_end_to_end_v5_smoke():
    import contextlib
    import csv as _csv
    import io
    from analyze import main as analyze_main, split_bucket

    groups = {}
    candidate = 0
    while set(groups) != {"TRAIN", "TEST"}:
        group = f"EVENT-{candidate}"
        groups.setdefault(split_bucket(group), group)
        candidate += 1
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        for offset, bucket in enumerate(("TRAIN", "TEST")):
            event = groups[bucket]
            for ts in range(1, 26):
                writer.writerow(research_row(
                    session="SMOKE", ts=ts + offset * 100,
                    event_id=event, ticker=f"T-{bucket}",
                    bid_qty=100, ask_qty=100))
        writer.writerow(research_row(
            session="SMOKE", ts=200, event_id="", ticker="",
            event="session_end", detail="operator interrupt",
            mid="", bid="", ask="", bid_qty="", ask_qty=""))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        analyze_main(path)
    text = output.getvalue()
    assert "MARK-OUTS (NON-EXECUTABLE" in text
    assert "FULL REPLAY" in text and "TRAIN:" in text and "TEST:" in text
    print("PASS analyzer runs end-to-end on a v5 two-partition session")


def test_analyzer_attributes_one_shared_portfolio_replay():
    import contextlib
    import csv as _csv
    import io
    import analyze as analyzer
    from replay import replay

    groups = {}
    candidate = 0
    while set(groups) != {"TRAIN", "TEST"}:
        group = f"PORTFOLIO-{candidate}"
        groups.setdefault(analyzer.split_bucket(group), group)
        candidate += 1
    cfg = Config(max_open_positions=1)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)

        def quote(ts, ticker, event, mid, bid, ask):
            writer.writerow(research_row(
                cfg=cfg, session="SHARED", ts=ts, event_id=event,
                ticker=ticker, mid=mid, bid=bid, ask=ask,
                bid_qty=100, ask_qty=100))

        for ts in range(1, 21):
            quote(float(ts), "ACTIVE", groups["TRAIN"], 60, 59, 61)
            quote(ts + 0.1, "BLOCKED", groups["TEST"], 60, 59, 61)
        quote(21.0, "ACTIVE", groups["TRAIN"], 52, 51, 53)
        quote(21.1, "BLOCKED", groups["TEST"], 52, 51, 53)
        quote(22.0, "ACTIVE", groups["TRAIN"], 52, 51, 53)
        quote(22.1, "BLOCKED", groups["TEST"], 52, 51, 53)
        quote(23.0, "ACTIVE", groups["TRAIN"], 61, 60, 62)
        quote(23.1, "BLOCKED", groups["TEST"], 52, 51, 53)
        # BLOCKED sees its recovery while ACTIVE still owns the only slot.
        quote(24.0, "BLOCKED", groups["TEST"], 61, 60, 62)
        quote(24.1, "ACTIVE", groups["TRAIN"], 61, 60, 62)
        quote(25.0, "BLOCKED", groups["TEST"], 61, 60, 62)
        writer.writerow(research_row(
            cfg=cfg, session="SHARED", ts=26, event_id="", ticker="",
            event="session_end", detail="operator interrupt",
            mid="", bid="", ask="", bid_qty="", ask_qty=""))

    full = replay(path, cfg=cfg)
    isolated = replay(path, tickers={"BLOCKED"}, cfg=cfg)
    assert [trade[1] for trade in full["trades"]] == ["BUY", "SELL"]
    assert {trade[0] for trade in full["trades"]} == {"ACTIVE"}
    assert [trade[1] for trade in isolated["trades"]] == ["BUY", "SELL"]
    assert sum(full["per_ticker_total"].values(), Decimal(0)) \
        == full["total_pnl"]

    old_cfg, old_horizons, old_tolerance = (
        analyzer.CFG, analyzer.HORIZONS, analyzer.MARKOUT_TOLERANCE_S)
    analyzer.CFG = cfg
    analyzer.HORIZONS = analyzer.build_horizons(cfg.max_hold_seconds)
    analyzer.MARKOUT_TOLERANCE_S = max(1.0, cfg.poll_interval * 2)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            analyzer.main(path)
    finally:
        analyzer.CFG = old_cfg
        analyzer.HORIZONS = old_horizons
        analyzer.MARKOUT_TOLERANCE_S = old_tolerance
    text = output.getvalue()
    assert "TRAIN: 1 exits" in text
    assert "TEST: 0 exits" in text
    print("PASS analyzer attributes one shared portfolio, not subset replays")


def test_aggregate_fee_rounding():
    from fees import fee_usd, trade_fee_usd
    price = Decimal("52.5")
    count = Decimal(3)
    raw = (Decimal("0.07") * count * (price / 100)
           * (Decimal(1) - price / 100))
    assert trade_fee_usd(price, count) == Decimal("0.0524")
    assert fee_usd(price, count, side="BUY") == Decimal("0.0550")
    assert trade_fee_usd(price, count) < trade_fee_usd(price, 1) * count
    assert fee_usd(price, count, side="BUY") >= raw
    # Official direct-member worked-example mechanics: $0.3301 x 0.03.
    assert fee_usd(Decimal("33.01"), Decimal("0.03"), side="BUY",
                   balance_precision_usd=Decimal("0.0001")) \
        == Decimal("0.000597")
    projected = projected_scalp_pnl_usd(
        Decimal(50), Config().take_profit, Config().contracts_per_trade,
        Config().sim_slippage_cents, Config().balance_precision_usd)
    entry = Decimal(51)
    exit_fill = entry + Config().take_profit - Config().sim_slippage_cents
    expected = ((exit_fill - entry) * Config().contracts_per_trade / 100
                - fee_usd(entry, Config().contracts_per_trade, side="BUY",
                          balance_precision_usd=Config().balance_precision_usd)
                - fee_usd(exit_fill, Config().contracts_per_trade,
                          side="SELL",
                          balance_precision_usd=Config().balance_precision_usd))
    assert projected == expected
    print("PASS paper fee includes aggregate trade and balance rounding fees")


def test_residual_valuation_respects_depth_and_slippage():
    from strategy import Position
    from replay import value_residual
    cfg = Config(); cfg.sim_slippage_cents = 1
    pos = Position("T", Decimal(50), Decimal(20), 0.0,
                   fee_usd(50, 20))
    zero = value_residual(pos, Decimal(80), Decimal(0), cfg)
    assert zero["executable_contracts"] == 0
    assert zero["unpriced_contracts"] == 20
    assert zero["marked_pnl"] < 0
    partial = value_residual(pos, Decimal(80), Decimal(5), cfg)
    assert partial["executable_contracts"] == 5
    assert partial["unpriced_contracts"] == 15
    assert partial["exit_price"] == 79
    print("PASS residual valuation respects bid depth and exit slippage")


def test_unknown_depth_never_means_unlimited_fill():
    cfg = Config(); cfg.sim_latency_s = 0
    ex = Executor(cfg, None,
                  BookFeed(bid=Decimal(50), bq=None,
                           ask=Decimal(52), aq=None),
                  clock=lambda: 0.0, sleep=lambda _: None)
    ex.submit_paper("T", "BUY", 20, now=0.0)
    assert ex.process_due_paper_orders(0.0, ticker="T")[0][1] is None
    ex.submit_paper("T", "SELL", 20, now=0.0)
    assert ex.process_due_paper_orders(0.0, ticker="T")[0][1] is None
    try:
        ex.execute("T", "BUY", 20)
        assert False
    except HaltError as error:
        assert "blocking paper execution" in str(error)
    print("PASS unknown depth cannot fill; blocking paper bypass rejected")


def test_pricefeed_learns_event_id_from_quote():
    from market_data import PriceFeed

    class Client:
        def get_market(self, ticker):
            return {"event_ticker": "MATCH-123", "status": "active",
                    "yes_bid": Decimal(50),
                    "yes_ask": Decimal(52), "yes_bid_size": Decimal(10),
                    "yes_ask_size": Decimal(10)}

    feed = PriceFeed(Config(), Client(), clock=lambda: 1.0)
    feed.get_quote("CONTRACT-A")
    assert feed.group_id("CONTRACT-A") == "MATCH-123"
    print("PASS configured ticker learns match/event ID from market quote")


def test_market_envelope_to_research_log_preserves_event_identity():
    import csv as _csv
    from kalshi_client import KalshiClient
    from market_data import PriceFeed
    from research_log import ResearchLog

    class Client(KalshiClient):
        def __init__(self):
            self.cfg = Config()
            self.base = self.cfg.api_base

        def _request(self, method, endpoint, params=None, body=None,
                     auth=False):
            assert endpoint in ("/markets/CONTRACT-A", "/markets/CONTRACT-B")
            ticker = endpoint.rsplit("/", 1)[-1]
            return {"market": current_market(
                ticker=ticker, event_ticker="MATCH-123",
                yes_sub_title="Player A wins",
                yes_bid_size_fp="10.00", yes_ask_size_fp="12.00")}

    feed = PriceFeed(Config(), Client(), clock=lambda: 7.25)
    directory = tempfile.mkdtemp()
    log = ResearchLog(directory, clock=lambda: 7.25,
                      session_id="SESSION-E2E")
    for ticker in ("CONTRACT-A", "CONTRACT-B"):
        mid, bid, ask, observed_at = feed.get_quote(ticker)
        _, bid_qty, _, ask_qty = feed.top_of_book(ticker)
        log.tick(ticker, mid, bid, ask, bid_qty, ask_qty,
                 ts=observed_at, group_id=feed.group_id(ticker),
                 close_ts=feed.lifecycle(ticker)[0],
                 can_close_early=feed.lifecycle(ticker)[1])
    with open(log.tick_path) as handle:
        rows = list(_csv.DictReader(handle))
    assert {row["event_id"] for row in rows} == {"MATCH-123"}
    assert {row["ticker"] for row in rows} == {"CONTRACT-A", "CONTRACT-B"}
    assert {row["ts"] for row in rows} == {"7.25"}
    assert feed.group_id("UNKNOWN") is None
    print("PASS official market envelope preserves event identity to CSV")


def test_replay_exact_paper_path_and_residual():
    import csv as _csv
    from replay import replay
    cfg = Config()
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts", "ticker", "mid", "bid", "ask", "bid_qty", "ask_qty"])
        t = 0.0
        for i in range(80):
            t += 1.5
            w.writerow([t, "T", 60, 59, 61, 500, 500])
        w.writerow([t + 1.5, "T", 52, 51, 53, 500, 500])   # 8c dip
        w.writerow([t + 2.6, "T", "51.75", 51, "52.5", 500, 6])
        # crash with ZERO bid depth: exits/flatten cannot fill
        for k in range(40):
            w.writerow([t + 3.0 + k * 1.5, "T", 40, 39, 41, 0, 500])
    r = replay(path)
    buys = [tr for tr in r["trades"] if tr[1] == "BUY"]
    assert buys and buys[0][2] == Decimal("52.5") + cfg.sim_slippage_cents
    assert buys[0][3] == Decimal(6)                 # depth-limited
    assert not any(tr[1] == "SELL" for tr in r["trades"])   # zero bid depth
    assert r["residual_contracts"] == Decimal(6)
    assert r["residual_marked"] < 0                 # loss NOT hidden
    print("PASS replay: exact paper path (post-latency subpenny ask, depth "
          "limit) and residual inventory counted in P&L")


def test_pending_paper_order_uses_first_observed_due_quote():
    from collections import defaultdict

    class MutableFeed:
        def __init__(self):
            self.history = defaultdict(list)
            self.book = {}

        def apply(self, ts, ticker, mid, bid, bq, ask, aq):
            self.book[ticker] = (bid, bq, ask, aq)
            self.history[ticker].append((ts, mid))

        def top_of_book(self, ticker):
            return self.book.get(ticker, (None, None, None, None))

    now = [0.0]
    cfg = Config(); cfg.sim_latency_s = 1.0
    feed = MutableFeed()
    for ts in range(-20, 0):
        feed.history["T"].append((float(ts), Decimal(60)))
    ex = Executor(cfg, None, feed, clock=lambda: now[0], sleep=lambda _: None)
    strat = ScalpStrategy(cfg)
    ctx = Context(cfg, feed, strat, ex, log=None, safety=Safety(cfg),
                  clock=lambda: now[0])

    feed.apply(0.0, "T", Decimal(52), Decimal(51), Decimal(50),
               Decimal(53), Decimal(50))
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53))
    assert not strat.positions and ex.has_pending("T")

    now[0] = 0.5
    feed.apply(0.5, "T", Decimal(51), Decimal(50), Decimal(50),
               Decimal(52), Decimal(50))
    process_tick(ctx, "T", Decimal(51), Decimal(50), Decimal(52))
    assert not strat.positions and ex.has_pending("T")

    now[0] = 1.0
    process_tick(ctx, "T", None, None, None)
    assert not strat.positions and ex.has_pending("T")

    feed.apply(1.0, "OTHER", Decimal(50), Decimal(49), Decimal(50),
               Decimal(51), Decimal(50))
    process_tick(ctx, "OTHER", Decimal(50), Decimal(49), Decimal(51))
    assert not strat.positions and ex.has_pending("T")

    now[0] = 1.1
    feed.apply(1.1, "T", Decimal(54), Decimal(53), Decimal(50),
               Decimal(55), Decimal(7))
    process_tick(ctx, "T", Decimal(54), Decimal(53), Decimal(55))
    assert strat.positions["T"].entry_price == Decimal(56)
    assert strat.positions["T"].contracts == Decimal(7)
    assert not ex.has_pending("T")
    print("PASS pending paper order fills only on first observed due quote")


def test_quote_timestamp_is_causal_at_latency_boundary():
    """Processing delay cannot turn a pre-due quote into a due fill."""
    from collections import defaultdict

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.book = (Decimal(51), Decimal(20),
                         Decimal(53), Decimal(20))

        def top_of_book(self, ticker):
            return self.book

    wall_clock = [0.0]
    cfg = Config(); cfg.sim_latency_s = 1.0
    feed = Feed()
    ex = Executor(cfg, None, feed, clock=lambda: wall_clock[0],
                  sleep=lambda _: None)
    strat = ScalpStrategy(cfg)
    ctx = Context(cfg, feed, strat, ex, None, Safety(cfg),
                  clock=lambda: wall_clock[0])
    ex.submit_paper("T", "BUY", 2, "boundary")

    # The quote was observed before due_at=1.0, although processing starts
    # after the boundary. It must not fill or be logged as a post-due quote.
    wall_clock[0] = 1.01
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                 observed_at=0.99)
    assert ex.has_pending("T") and not strat.positions

    wall_clock[0] = 1.11
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                 observed_at=1.10)
    assert not ex.has_pending("T")
    assert strat.positions["T"].contracts == Decimal(2)
    print("PASS one observed quote timestamp governs latency causality")


def test_pending_entries_count_toward_position_limit():
    from collections import defaultdict

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.books = {}

        def top_of_book(self, ticker):
            return self.books[ticker]

    cfg = Config(); cfg.max_open_positions = 1
    feed = Feed()
    for ticker in ("A", "B"):
        feed.history[ticker] = [(-1.0, Decimal(60)),
                                (0.0, Decimal(52))]
        feed.books[ticker] = (Decimal(51), Decimal(20),
                              Decimal(53), Decimal(20))
    ex = Executor(cfg, None, feed, clock=lambda: 0.0, sleep=lambda _: None)
    ctx = Context(cfg, feed, ScalpStrategy(cfg), ex, None, Safety(cfg),
                  clock=lambda: 0.0)
    process_tick(ctx, "A", Decimal(52), Decimal(51), Decimal(53))
    process_tick(ctx, "B", Decimal(52), Decimal(51), Decimal(53))
    assert len(ex.pending_paper) == 1
    assert ex.pending_paper[0].ticker == "A"
    print("PASS pending BUYs count toward max-open-position limit")


def test_replay_empty_ticker_selection_processes_nothing():
    import csv as _csv
    from replay import replay
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts", "ticker", "mid", "bid", "ask",
                    "bid_qty", "ask_qty"])
        w.writerow([1, "ONLY", 50, 49, 51, 10, 10])
    result = replay(path, tickers=set())
    assert result["rows_processed"] == 0
    assert result["trades"] == []
    assert not result["evaluable"]
    print("PASS empty ticker selection remains empty")


def test_replay_eof_never_fabricates_flatten_fills():
    import csv as _csv
    from replay import replay
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["ts", "ticker", "mid", "bid", "ask",
                    "bid_qty", "ask_qty"])
        for ts in range(1, 21):
            w.writerow([ts, "T", 60, 59, 61, 100, 100])
        w.writerow([21, "T", 52, 51, 53, 100, 100])
        w.writerow([22, "T", 52, 51, 53, 100, 5])
    result = replay(path)
    assert [trade[1] for trade in result["trades"]] == ["BUY"]
    assert result["realized"] == 0
    assert result["residual_contracts"] == 5
    assert not result["evaluable"]
    print("PASS replay EOF leaves residual; no fabricated flatten fill")


def test_pricefeed_and_replayfeed_produce_identical_paper_fills():
    from market_data import PriceFeed
    from replay import ReplayFeed, VirtualClock

    rows = []
    for ts in range(1, 21):
        rows.append((float(ts), "T", Decimal(60), Decimal(59),
                     Decimal(61), Decimal(100), Decimal(100)))
    rows += [
        (21.0, "T", Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(100)),
        (22.0, "T", Decimal("51.75"), Decimal(51), Decimal("52.5"),
         Decimal(100), Decimal(6)),
        (24.0, "T", Decimal(61), Decimal(60), Decimal(62),
         Decimal(6), Decimal(100)),
        (25.0, "T", Decimal(61), Decimal(60), Decimal(62),
         Decimal(6), Decimal(100)),
    ]

    def drive(feed, clock, apply):
        cfg = Config(); cfg.sim_latency_s = 1.0
        strat = ScalpStrategy(cfg)
        ex = Executor(cfg, None, feed, clock=clock.time, sleep=clock.sleep)
        ctx = Context(cfg, feed, strat, ex, log=None, safety=Safety(cfg),
                      clock=clock.time)
        fills = []
        original = strat.record_fill

        def capture(ticker, side, price, count, fee, now=None):
            fills.append((ticker, side, price, count, fee, now))
            original(ticker, side, price, count, fee, now=now)
        strat.record_fill = capture
        for row in rows:
            clock.t = row[0]
            mid, bid, ask, observed_at = apply(row)
            process_tick(ctx, row[1], mid, bid, ask,
                         observed_at=observed_at)
        return fills, strat.realized_pnl

    replay_clock = VirtualClock()
    replay_feed = ReplayFeed(replay_clock)
    replay_result = drive(
        replay_feed, replay_clock,
        lambda row: (replay_feed.apply(row[0], row[1], row[2], row[3],
                                      row[4], row[5], row[6],
                                      4070908800.0, False)
                     or (row[2], row[3], row[4], row[0])))

    class StreamClient:
        current = None

        def get_market(self, ticker):
            row = self.current
            return {"event_ticker": "EVENT-T", "status": "active",
                    "yes_bid": row[3], "yes_ask": row[4],
                    "yes_bid_size": row[5], "yes_ask_size": row[6],
                    "close_ts": 4070908800.0, "can_close_early": False}

    real_clock = VirtualClock()
    client = StreamClient()
    price_feed = PriceFeed(Config(), client, clock=real_clock.time)

    def fetch(row):
        client.current = row
        return price_feed.get_quote(row[1])

    price_result = drive(price_feed, real_clock, fetch)
    assert replay_result == price_result
    assert [f[1] for f in replay_result[0]] == ["BUY", "SELL"]
    print("PASS real PriceFeed and ReplayFeed produce identical paper fills")


def test_actual_runtime_driver_matches_replay():
    import csv as _csv
    from collections import defaultdict, deque
    from bot import run_loop
    from replay import VirtualClock, replay

    rows = [(float(ts), "T", Decimal(60), Decimal(59), Decimal(61),
             Decimal(100), Decimal(100)) for ts in range(1, 21)]
    rows += [
        (21.0, "T", Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(100)),
        (22.0, "T", Decimal("51.75"), Decimal(51), Decimal("52.5"),
         Decimal(100), Decimal(6)),
        (24.0, "T", Decimal(61), Decimal(60), Decimal(62),
         Decimal(6), Decimal(100)),
        (25.0, "T", Decimal(61), Decimal(60), Decimal(62),
         Decimal(6), Decimal(100)),
    ]
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(["ts", "ticker", "mid", "bid", "ask",
                         "bid_qty", "ask_qty"])
        writer.writerows(rows)
    replay_result = replay(path)

    clock = VirtualClock()
    class RuntimeFeed:
        def __init__(self):
            self.remaining = deque(rows)
            self.history = defaultdict(lambda: deque(maxlen=600))
            self.books = {}

        def get_quote(self, ticker):
            if not self.remaining:
                raise KeyboardInterrupt()
            row = self.remaining.popleft()
            clock.t = row[0]
            self.history[ticker].append((row[0], row[2]))
            self.books[ticker] = (row[3], row[5], row[4], row[6])
            return row[2], row[3], row[4], row[0]

        def top_of_book(self, ticker):
            return self.books.get(ticker, (None, None, None, None))

        def stale_tickers(self, tickers):
            return []

        def group_id(self, ticker):
            return "EVENT-T"

    cfg = Config(); cfg.sim_latency_s = 1.0
    feed = RuntimeFeed()
    strategy = ScalpStrategy(cfg)
    executor = Executor(cfg, None, feed, clock=clock.time,
                        sleep=clock.sleep)
    ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                  clock=clock.time)
    runtime_trades = []
    original = strategy.record_fill
    def capture(ticker, side, price, count, fee, now=None, event_id=None):
        runtime_trades.append((ticker, side, price, count))
        original(ticker, side, price, count, fee, now=now,
                 event_id=event_id)
    strategy.record_fill = capture
    assert run_loop(ctx, None, ["T"], sleep=lambda _: None)
    assert runtime_trades == replay_result["trades"]
    assert strategy.realized_pnl == replay_result["realized"]
    print("PASS actual run_loop and replay drivers produce identical fills")


def test_core_units():
    assert float(net_take_profit(50, 2)) < 0 < float(
        net_take_profit(50, Config().take_profit))
    assert dip_signal([(0, Decimal(60)), (10, Decimal(60))], 20,
                      Decimal(53), 7, 45) is not None
    assert dip_signal([], 20, Decimal(40), 7, 45) is None
    cfg = Config(); s = ScalpStrategy(cfg)
    s.record_fill("T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20))
    assert s.check_exit("T", Decimal(40))["reason"].startswith("stop-loss")
    s.record_fill("T", "SELL", Decimal(40), Decimal(20), fee_usd(40, 20))
    expected = (Decimal(40 - 50) * 20 / Decimal(100)
                - fee_usd(40, 20) - fee_usd(50, 20))
    assert s.realized_pnl == expected
    print("PASS core units (Decimal fees, causality, gapped-stop actual price)")


def test_live_and_demo_disabled():
    import subprocess, sys as _s
    import bot as bot_module
    src = open("bot.py").read()
    assert "live_enabled" not in src and "INCI_ACK_RISK" not in src
    assert "REAL_ORDER_EXECUTION_ENABLED = False" in open("executor.py").read()
    constructed = []
    def forbidden_config():
        raise AssertionError("config must not be constructed")
    def forbidden_factory(cfg):
        constructed.append(cfg)
        raise AssertionError("client must not be constructed")
    assert bot_module.main(["--live"], client_factory=forbidden_factory,
                           config_factory=forbidden_config) == 2
    assert bot_module.main(["--demo"], client_factory=forbidden_factory,
                           config_factory=forbidden_config) == 2
    assert constructed == []

    env = {k: v for k, v in os.environ.items()
           if k not in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH",
                        "INCI_ACK_RISK")}
    project = os.getcwd()
    env["PYTHONPATH"] = project + os.pathsep + env.get("PYTHONPATH", "")
    env["KALSHI_API_KEY_ID"] = "must-not-be-read"
    env["KALSHI_PRIVATE_KEY_PATH"] = "/definitely/not/a/key.pem"
    for flag, needle in (("--live", "disabled in this build"),
                         ("--demo", "disabled in this build")):
        empty = tempfile.mkdtemp()
        out = subprocess.run([_s.executable, os.path.join(project, "bot.py"),
                              flag], cwd=empty,
                             capture_output=True, text=True, env=env,
                             timeout=30)
        assert needle in out.stdout, (flag, out.stdout)
        assert out.returncode == 2, (flag, out.returncode, out.stderr)
        assert os.listdir(empty) == [], (flag, os.listdir(empty))
    print("PASS --live/--demo refuse nonzero before files, network, or orders")


def test_current_market_contract_and_empty_book_normalization():
    from market_data import PriceFeed, MarketUnavailable

    parsed = parse_market(current_market(
        yes_bid_dollars="0.1234", yes_bid_size_fp="2.00",
        yes_ask_dollars="0.6543", yes_ask_size_fp="0.00"))
    assert parsed["title"] == "Yes side"       # deprecated title is optional
    assert parsed["market_type"] == "binary"
    assert parsed["status"] == "active"
    assert parsed["notional_value"] == Decimal("1.0000")
    assert parsed["yes_bid"] == Decimal("12.3400")
    assert parsed["yes_ask"] is None and parsed["yes_ask_size"] == 0

    for malformed in (
            current_market(market_type="scalar"),
            current_market(status="open"),
            current_market(notional_value_dollars="5.000000"),
            current_market(yes_bid_dollars="0.12345"),
            {k: v for k, v in current_market().items()
             if k != "yes_sub_title"}):
        try:
            parse_market(malformed)
            assert False, malformed
        except SchemaError:
            pass

    class InactiveClient:
        def get_market(self, ticker):
            return parse_market(current_market(status="closed"))

    feed = PriceFeed(Config(), InactiveClient(), clock=lambda: 10.0)
    feed.subscribe(["T"])
    try:
        feed.get_quote("T")
        assert False
    except MarketUnavailable as error:
        assert "unavailable" in str(error)
    assert "T" not in feed.last_book
    print("PASS current Market contract: binary/$1/active and empty-book safe")


def test_ioc_canceled_quantity_is_modeled_explicitly():
    class PartialIocClient(FakeClient):
        def create_order(self, body):
            self.created_bodies.append(body)
            self.active_client_order_id = body["client_order_id"]
            self.active_ticker = body["ticker"]
            self.active_count = Decimal(body["count"])
            self.active_fill_count = Decimal(5)
            return parse_create_ack({
                "order_id": "OID1", "client_order_id": body["client_order_id"],
                "fill_count": "5.00", "remaining_count": "0.00",
                "ts_ms": 123})

        def get_order(self, oid):
            self.order_calls += 1
            return parse_order({
                "order_id": oid,
                "client_order_id": self.active_client_order_id,
                "ticker": self.active_ticker, "status": "canceled",
                "subaccount_number": 0, "fill_count_fp": "5.00",
                "remaining_count_fp": "0.00", "initial_count_fp": "20.00"})

    cfg = Config(); cfg.reconcile_timeout_s = 1
    client = PartialIocClient()
    client.fills = [FILL5]
    client.positions_by_call = [[], [{"ticker": "T", "position_fp": "5"}],
                                [{"ticker": "T", "position_fp": "5"}]]
    executor, journal = make_exec(cfg, client, BookFeed())
    _, filled, _ = executor.execute(
        "T", "BUY", 20, expected_pre_position=Decimal(0))
    assert filled == Decimal(5)
    assert Decimal(journal.load()[-1]["canceled"]) == Decimal(15)
    print("PASS IOC terminal remaining=0 records the unfilled 15 as canceled")


def test_config_rejects_unsafe_research_parameters():
    invalid = (
        {"sim_latency_s": -0.1}, {"sim_slippage_cents": -1},
        {"contracts_per_trade": 0}, {"max_open_positions": 0},
        {"max_daily_loss_usd": 0}, {"dip_threshold": 0},
        {"lookback_seconds": 0}, {"take_profit": 0}, {"stop_loss": 0},
        {"max_hold_seconds": 0}, {"min_price": 90, "max_price": 10},
        {"max_spread": -1}, {"sim_slippage_cents": 100},
        {"max_price": 99, "sim_slippage_cents": 1},
        {"max_price": 90, "take_profit": 11},
        {"sim_latency_s": "1"}, {"subaccount": 33},
        {"balance_precision_usd": "0.003"},
        {"max_monitored_markets": 0},
        {"max_monitored_markets": 11},
    )
    for kwargs in invalid:
        try:
            Config(**kwargs)
            assert False, kwargs
        except ValueError:
            pass
    print("PASS unsafe strategy/risk/simulation configuration fails fast")


def test_subcent_sell_fill_is_never_improved():
    cfg = Config(sim_latency_s=0, sim_slippage_cents=1)
    executor = Executor(
        cfg, None,
        BookFeed(bid=Decimal("0.5"), bq=Decimal(1),
                 ask=Decimal(2), aq=Decimal(1)),
        clock=lambda: 0.0, sleep=lambda _: None)
    executor.submit_paper("T", "SELL", 1, now=0.0)
    _, result = executor.process_due_paper_orders(0.0, ticker="T")[0]
    assert result[0] == Decimal(0)
    print("PASS subcent SELL slippage floors at zero, never a better 1c fill")


def test_staleness_is_checked_between_market_requests():
    from collections import defaultdict
    from bot import run_loop

    class SlowSweepFeed:
        def __init__(self):
            self.calls = []
            self.history = defaultdict(list)

        def get_quote(self, ticker):
            self.calls.append(ticker)
            if self.calls.count("FIRST") > 1:
                raise KeyboardInterrupt()
            return Decimal(50), Decimal(49), Decimal(51), 31.0

        def top_of_book(self, ticker):
            return Decimal(49), Decimal(10), Decimal(51), Decimal(10)

        def stale_tickers(self, tickers):
            return ["SECOND"] if self.calls and "SECOND" in tickers else []

    cfg = Config(); cfg.poll_interval = 0
    feed = SlowSweepFeed()
    ctx = Context(cfg, feed, ScalpStrategy(cfg), Executor(cfg, None, feed),
                  None, Safety(cfg), clock=lambda: 31.0)
    assert run_loop(ctx, None, ["FIRST", "SECOND"], sleep=lambda _: None)
    assert feed.calls == ["FIRST", "FIRST"]
    assert "SECOND" in ctx.safety.quarantined
    print("PASS stale flat market is isolated between blocking requests")


def test_preflight_warns_but_accepts_valid_empty_portfolio_collections():
    from bot import preflight

    class CheckClient:
        def get_exchange_status(self):
            return {"exchange_active": True, "trading_active": True}

        def get_markets_sample(self, **kwargs):
            return [parse_market(current_market())]

        def get_balance(self):
            return {"balance": 0}

        def get_open_orders(self):
            return []

        def get_fills(self):
            return []

        def get_positions(self):
            return []

    unauthenticated = Config()
    unauthenticated.api_key_id = ""
    assert not preflight(unauthenticated, CheckClient())
    authenticated = Config(); authenticated.api_key_id = "KEY"
    assert preflight(authenticated, CheckClient())

    class BrokenClient(CheckClient):
        def get_positions(self):
            raise SchemaError("malformed position row")

    assert not preflight(authenticated, BrokenClient())
    print("PASS preflight accepts empty rows but rejects schema failures")


def test_preflight_market_sample_uses_exactly_one_page():
    from kalshi_client import KalshiClient

    class Client(KalshiClient):
        def __init__(self):
            self.cfg = Config()
            self.base = self.cfg.api_base
            self.calls = []

        def _request(self, method, endpoint, params=None, body=None,
                     auth=False):
            self.calls.append((method, endpoint, dict(params or {})))
            return {"markets": [current_market(ticker=f"T-{i}")
                                for i in range(30)],
                    "cursor": "more-pages-exist"}

    client = Client()
    rows = client.get_markets_sample(status="open", limit=25)
    assert len(rows) == 25
    assert client.calls == [("GET", "/markets",
                             {"status": "open", "limit": 25})]
    print("PASS preflight market sample performs one bounded page request")


def test_market_collections_skip_only_known_unsupported_products():
    from kalshi_client import KalshiClient

    mve = current_market(
        ticker="KXMVE-COMBO", event_ticker="KXMVE-EVENT",
        mve_collection_ticker="KXMVE-COLLECTION")
    scalar = current_market(ticker="SCALAR", market_type="scalar")
    binary = current_market(ticker="BINARY")

    class SampleClient(KalshiClient):
        def __init__(self, rows):
            self.cfg = Config()
            self.base = self.cfg.api_base
            self.rows = rows

        def _request(self, *args, **kwargs):
            return {"markets": self.rows, "cursor": ""}

    client = SampleClient([scalar, binary, mve])
    try:
        parsed = client.get_markets_sample(limit=25)
    except SchemaError as error:
        assert False, f"known unsupported row aborted collection: {error}"
    assert [row["ticker"] for row in parsed] == ["BINARY"]
    assert getattr(client, "last_market_skips", None) == {
        "mve": 1, "scalar": 1}

    class PagedClient(SampleClient):
        def __init__(self):
            super().__init__([])
            self.pages = [
                {"markets": [scalar], "cursor": "next"},
                {"markets": [binary, mve], "cursor": ""},
            ]
            self.calls = 0

        def _request(self, *args, **kwargs):
            page = self.pages[self.calls]
            self.calls += 1
            return page

    paged = PagedClient()
    try:
        parsed = paged.get_markets(status="open")
    except SchemaError as error:
        assert False, f"known unsupported row aborted pagination: {error}"
    assert [row["ticker"] for row in parsed] == ["BINARY"]
    assert paged.last_market_skips == {"mve": 1, "scalar": 1}
    assert paged.calls == 2
    print("PASS market collections skip/count scalar and MVE products")


def test_market_collection_skips_never_hide_schema_drift():
    from kalshi_client import KalshiClient

    class Client(KalshiClient):
        def __init__(self, row, *, direct=False):
            self.cfg = Config()
            self.base = self.cfg.api_base
            self.row = row
            self.direct = direct

        def _request(self, *args, **kwargs):
            return ({"market": self.row} if self.direct else
                    {"markets": [self.row], "cursor": ""})

    malformed = current_market(ticker="BROKEN")
    del malformed["close_time"]
    for row in (malformed, current_market(
            ticker="UNKNOWN", market_type="ternary")):
        try:
            Client(row).get_markets_sample(limit=25)
            assert False, row
        except SchemaError as error:
            assert type(error) is SchemaError

    mixed = Client(malformed)
    mixed._request = lambda *args, **kwargs: {
        "markets": [current_market(ticker="SCALAR", market_type="scalar"),
                    malformed],
        "cursor": "",
    }
    try:
        mixed.get_markets_sample(limit=25)
        assert False
    except SchemaError as error:
        assert "unsupported skipped: total=1 types=scalar=1" in str(error)
        assert mixed.last_market_skips == {"scalar": 1}

    scalar = current_market(ticker="SCALAR", market_type="scalar")
    try:
        Client(scalar, direct=True).get_market("SCALAR")
        assert False
    except SchemaError as error:
        assert type(error).__name__ == "UnsupportedMarketType"
        assert "'scalar'" in str(error)
    print("PASS malformed/unknown listings and direct unsupported markets fail")


def test_discovery_and_preflight_report_unsupported_market_counts():
    import contextlib
    import io
    from bot import preflight
    from kalshi_client import format_market_skips
    from market_data import PriceFeed

    assert format_market_skips({}) == \
        "unsupported skipped: total=0 types=none"

    class DiscoveryClient:
        last_market_skips = {"mve": 2, "scalar": 1}

        def __init__(self):
            self.params = None

        def get_markets(self, **params):
            self.params = params
            return []

    discovery_client = DiscoveryClient()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert PriceFeed(Config(), discovery_client).discover_tickers() == []
    assert discovery_client.params["mve_filter"] == "exclude"
    assert "unsupported skipped: total=3 types=mve=2, scalar=1" \
        in output.getvalue()

    class CheckClient:
        last_market_skips = {"scalar": 2}

        def __init__(self):
            self.params = None

        def get_exchange_status(self):
            return {"exchange_active": True, "trading_active": True}

        def get_markets_sample(self, **kwargs):
            self.params = kwargs
            return []

    check_client = CheckClient()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert not preflight(Config(), check_client)
    assert check_client.params["mve_filter"] == "exclude"
    text = output.getvalue()
    assert "unsupported skipped: total=2 types=scalar=2" in text
    assert "no supported markets returned" in text
    print("PASS discovery/--check loudly report unsupported type counts")


def test_discovery_uses_maximum_page_and_caps_monitored_markets():
    from market_data import PriceFeed

    class DiscoveryClient:
        last_market_skips = {}

        def __init__(self):
            self.params = None

        def get_markets(self, **params):
            self.params = params
            return [
                parse_market(current_market(
                    ticker=f"ATP-{index}", event_ticker=f"EVENT-{index}",
                    title=f"ATP tennis match {index}"))
                for index in range(5)
            ]

    cfg = Config(max_monitored_markets=3)
    client = DiscoveryClient()
    feed = PriceFeed(cfg, client)
    assert feed.discover_tickers() == ["ATP-0", "ATP-1", "ATP-2"]
    assert client.params == {
        "status": "open", "limit": 1000, "mve_filter": "exclude"}
    assert feed.group_ids == {
        "ATP-0": "EVENT-0", "ATP-1": "EVENT-1", "ATP-2": "EVENT-2"}
    print("PASS discovery uses 1000-row pages and caps monitored markets")


def test_explicit_tickers_respect_monitoring_cap():
    from market_data import PriceFeed

    cfg = Config(
        tickers=["T1", "T2", "T3", "T4"],
        max_monitored_markets=3)
    assert PriceFeed(cfg, client=None).discover_tickers() == [
        "T1", "T2", "T3"]
    print("PASS explicit ticker lists respect the monitoring cap")


def test_response_format_errors_include_raw_values():
    malformed = [
        (lambda: parse_fill({
            "count_fp": "100.0000", "yes_price_dollars": "0.5000",
            "fee_cost": "0.01", "order_id": "O", "fill_id": "F",
            "ticker": "T"}), "'100.0000'"),
        (lambda: parse_market(current_market(can_close_early="false")),
         "'false'"),
        (lambda: parse_fill({
            "count_fp": "1.00", "yes_price_dollars": "0.5000",
            "fee_cost": "0.1234567", "order_id": "O", "fill_id": "F",
            "ticker": "T"}), "'0.1234567'"),
        (lambda: parse_position({"ticker": "T", "position_fp": 1.0}),
         "1.0"),
        (lambda: parse_orderbook_response({"orderbook_fp": []}), "[]"),
        (lambda: parse_market(current_market(
            close_time="2099-01-01T00:00:00")),
         "'2099-01-01T00:00:00'"),
    ]
    for invoke, raw in malformed:
        try:
            invoke()
            assert False, raw
        except SchemaError as error:
            assert raw in str(error), (raw, str(error))
    print("PASS response-format errors include raw offending values")


def test_replay_reports_halt_and_resets_daily_risk_at_utc_midnight():
    import csv as _csv
    from datetime import datetime, timezone
    from replay import replay

    before = datetime(2026, 1, 1, 23, 59, 59,
                      tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 1, 2, 0, 0, 1,
                     tzinfo=timezone.utc).timestamp()
    rows = [(before, 60, 59, 61, 100, 100)]
    rows += [(after + i, 60, 59, 61, 100, 100) for i in range(20)]
    rows += [
        (after + 20, 52, 51, 53, 100, 100),
        (after + 21, Decimal("51.75"), 51, Decimal("52.5"), 100, 20),
        (after + 22, 40, 39, 41, 20, 100),
        (after + 23, 40, 39, 41, 20, 100),
        (after + 24, 40, 39, 41, 20, 100),
    ]
    cfg = Config(max_daily_loss_usd=1)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        for ts, mid, bid, ask, bid_qty, ask_qty in rows:
            day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            writer.writerow(research_row(
                cfg=cfg, session="MIDNIGHT", starting_pnl=100,
                starting_day="2026-01-01", day=day, ts=ts,
                event_id="EVENT-T", mid=mid, bid=bid, ask=ask,
                bid_qty=bid_qty, ask_qty=ask_qty))
        terminal_ts = rows[-1][0] + 1
        writer.writerow(research_row(
            cfg=cfg, session="MIDNIGHT", starting_pnl=100,
            starting_day="2026-01-01", day="2026-01-02", ts=terminal_ts,
            event_id="", ticker="", event="session_end",
            detail="operator interrupt", mid="", bid="", ask="",
            bid_qty="", ask_qty=""))
    result = replay(path, cfg=cfg)
    assert result["halted"]
    assert "loss limit" in result["halt_reason"]
    assert result["rows_processed"] < result["rows_available"]
    assert result["ending_daily_pnl"] == Decimal(0), result
    assert not result["evaluable"]
    print("PASS replay exposes early halt and resets UTC-day risk state")


def test_malformed_executable_books_fail_closed():
    import csv as _csv
    from analyze import load
    from replay import load_log, replay

    cases = (
        ("NaN", 49, 51, 10, 10),
        (55, 60, 50, 10, 10),
        (50, 49, 101, 10, 10),
        (50, 49, 51, "NaN", 10),
    )
    for i, (mid, bid, ask, bid_qty, ask_qty) in enumerate(cases):
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w", newline="") as handle:
            writer = _csv.writer(handle)
            writer.writerow(RESEARCH_HEADER)
            writer.writerow(research_row(
                session=f"BAD-{i}", mid=mid, bid=bid, ask=ask,
                bid_qty=bid_qty, ask_qty=ask_qty))
        for loader in (load, load_log):
            try:
                loader(path)
                assert False, (loader.__name__, i)
            except ValueError:
                pass

    missing_event = tempfile.mktemp(suffix=".csv")
    with open(missing_event, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        writer.writerow(research_row(session="NOEVENT", event_id=""))
    result = replay(missing_event)
    assert result["rows_processed"] == 0 and not result["evaluable"]
    print("PASS malformed/nonfinite/crossed/ungrouped books fail closed")


def test_replay_honors_logged_same_day_starting_loss():
    import csv as _csv
    from replay import replay

    cfg = Config(max_daily_loss_usd=30)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        writer.writerow(research_row(
            cfg=cfg, session="RESTART", starting_pnl=-31))
        writer.writerow(research_row(
            cfg=cfg, session="RESTART", starting_pnl=-31, ts=2,
            event_id="", ticker="", event="session_end",
            detail="operator interrupt", mid="", bid="", ask="",
            bid_qty="", ask_qty=""))
    result = replay(path, cfg=cfg)
    assert result["starting_daily_pnl"] == Decimal(-31)
    assert result["halted"] and "loss limit" in result["halt_reason"]
    assert result["trades"] == [] and not result["evaluable"]
    print("PASS replay restores same-day starting loss before first decision")


def test_replay_uses_log_creation_day_when_first_quote_is_after_midnight():
    from datetime import datetime, timezone
    from research_log import ResearchLog
    from replay import replay

    before = datetime(2026, 1, 1, 23, 59, 59,
                      tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 1, 2, 0, 0, 1,
                     tzinfo=timezone.utc).timestamp()
    now = [before]
    cfg = Config(max_daily_loss_usd=30)
    log = ResearchLog(tempfile.mkdtemp(), clock=lambda: now[0],
                      session_id="DELAYED-FIRST", starting_pnl=-31,
                      config=cfg)
    now[0] = after
    log.tick("T", Decimal(50), Decimal(49), Decimal(51),
             Decimal(10), Decimal(10), group_id="E",
             close_ts=4070908800.0, can_close_early=False)
    now[0] = after + 1
    log.end(clean=True, reason="operator interrupt")
    result = replay(log.tick_path, cfg=cfg)
    assert result["starting_daily_pnl"] == Decimal(-31)
    assert not result["halted"] and result["evaluable"], result
    print("PASS first post-midnight quote resets creation-day starting loss")


def test_replay_requires_durable_clean_session_terminal():
    from research_log import ResearchLog
    from replay import replay

    now = [1.0]
    log = ResearchLog(tempfile.mkdtemp(), clock=lambda: now[0],
                      session_id="TERMINAL", config=Config())
    log.tick("T", Decimal(50), Decimal(49), Decimal(51),
             Decimal(10), Decimal(10), group_id="E",
             close_ts=4070908800.0, can_close_early=False)
    incomplete = replay(log.tick_path)
    assert not incomplete["evaluable"]
    assert incomplete["terminal_status"] == "missing"
    now[0] = 2.0
    log.end(clean=True, reason="operator interrupt")
    complete = replay(log.tick_path)
    assert complete["terminal_status"] == "clean"
    assert complete["evaluable"]
    print("PASS replay requires one durable clean terminal record")


def test_replay_rejects_nonmonotonic_observation_order():
    from research_log import ResearchLog
    from replay import replay

    log = ResearchLog(tempfile.mkdtemp(), clock=lambda: 0.0,
                      session_id="ORDER", config=Config())
    for ts in (2.0, 1.0):
        log.tick("T", Decimal(50), Decimal(49), Decimal(51),
                 Decimal(10), Decimal(10), ts=ts, group_id="E",
                 close_ts=4070908800.0, can_close_early=False)
    log.end(clean=True, reason="operator interrupt", ts=3.0)
    try:
        replay(log.tick_path)
        assert False
    except ValueError as error:
        assert "non-monotonic" in str(error)
    print("PASS replay rejects reordered observations instead of sorting")


def test_replay_rejects_config_or_code_provenance_mismatch():
    from research_log import ResearchLog
    from replay import replay

    original = Config()
    log = ResearchLog(tempfile.mkdtemp(), clock=lambda: 1.0,
                      session_id="PROVENANCE", config=original)
    log.tick("T", Decimal(50), Decimal(49), Decimal(51),
             Decimal(10), Decimal(10), group_id="E",
             close_ts=4070908800.0, can_close_early=False)
    log.end(clean=True, reason="operator interrupt", ts=2.0)
    for changed in (Config(dip_threshold=8), Config(poll_interval=2.0),
                    Config(market_keywords=["basketball"]),
                    Config(max_monitored_markets=9)):
        try:
            replay(log.tick_path, cfg=changed)
            assert False
        except ValueError as error:
            assert "fingerprint" in str(error)
    print("PASS replay rejects config/code provenance mismatch")


def test_pnl_ledger_rejects_day_timestamp_corruption():
    import json
    from pnl_ledger import DailyPnlLedger

    path = tempfile.mktemp(suffix=".jsonl")
    with open(path, "w") as handle:
        handle.write(json.dumps({
            "event_id": "bad-day", "effective_ts": 1,
            "utc_day": "2099-01-01", "pnl_usd": "-31"}) + "\n")
    try:
        DailyPnlLedger(path).today_total(1)
        assert False
    except ValueError as error:
        assert "utc_day" in str(error)
    print("PASS P&L ledger rejects day/timestamp corruption")


def test_analyzer_horizons_are_sorted_unique():
    from analyze import build_horizons
    assert build_horizons(30) == [1, 5, 15, 30, 60, 120]
    assert build_horizons(300) == [1, 5, 15, 30, 60, 120, 300]
    print("PASS analyzer horizons remain sorted for short max-hold values")


def test_unavailable_market_isolated_unless_exposed():
    from collections import defaultdict
    from bot import run_loop
    from market_data import MarketUnavailable

    class Feed:
        def __init__(self):
            self.calls = []
            self.history = defaultdict(list)

        def get_quote(self, ticker):
            self.calls.append(ticker)
            if ticker == "CLOSED":
                raise MarketUnavailable("market CLOSED is closed")
            if self.calls.count("HEALTHY") > 1:
                raise KeyboardInterrupt()
            self.history[ticker].append((1.0, Decimal(50)))
            return Decimal(50), Decimal(49), Decimal(51), 1.0

        def top_of_book(self, ticker):
            return Decimal(49), Decimal(10), Decimal(51), Decimal(10)

        def stale_tickers(self, tickers):
            return []

    cfg = Config(); cfg.poll_interval = 0
    flat_feed = Feed()
    flat_ctx = Context(cfg, flat_feed, ScalpStrategy(cfg),
                       Executor(cfg, None, flat_feed), None, Safety(cfg),
                       clock=lambda: 1.0)
    assert run_loop(flat_ctx, None, ["CLOSED", "HEALTHY"],
                    sleep=lambda _: None)
    assert "CLOSED" in flat_ctx.safety.quarantined
    assert flat_feed.calls == ["CLOSED", "HEALTHY", "HEALTHY"]

    exposed_feed = Feed()
    exposed_strategy = ScalpStrategy(cfg)
    exposed_strategy.record_fill(
        "CLOSED", "BUY", Decimal(50), Decimal(1),
        fee_usd(50, 1, side="BUY"))
    exposed_ctx = Context(
        cfg, exposed_feed, exposed_strategy,
        Executor(cfg, None, exposed_feed), None, Safety(cfg), clock=lambda: 1.0)
    assert not run_loop(exposed_ctx, None, ["CLOSED", "HEALTHY"],
                        sleep=lambda _: None)
    assert "unavailable" in exposed_ctx.safety.tripped_reason
    assert exposed_feed.calls == ["CLOSED"]
    print("PASS unavailable flat market isolates; exposed market halts globally")


def test_close_horizon_and_live_requote_block_unsafe_entries():
    from collections import defaultdict
    from market_data import PriceFeed

    class MarketClient:
        def get_market(self, ticker):
            return parse_market(current_market(
                close_time="1970-01-01T00:03:20Z"))

    cfg = Config(max_hold_seconds=300, close_buffer_seconds=60)
    feed = PriceFeed(cfg, MarketClient(), clock=lambda: 100.0)
    feed.subscribe(["T"])
    mid, bid, ask, observed_at = feed.get_quote("T")
    for ts in range(80, 100):
        feed.history["T"].append((float(ts), Decimal(60)))
    ctx = Context(cfg, feed, ScalpStrategy(cfg), Executor(cfg, None, feed),
                  None, Safety(cfg), clock=lambda: 100.0)
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                 observed_at=observed_at)
    assert not ctx.executor.pending_paper

    class UnsafeRequote(BookFeed):
        def get_quote(self, ticker):
            self.book = (Decimal(79), Decimal(20),
                         Decimal(80), Decimal(20))
            return Decimal("79.5"), Decimal(79), Decimal(80), time.time()

        def entry_allowed(self, ticker, now, required_seconds):
            return True

        def early_close_risk(self, ticker):
            return False

    client = FakeClient()
    executor, journal = make_exec(Config(), client, UnsafeRequote())
    try:
        executor.execute("T", "BUY", 20,
                         expected_pre_position=Decimal(0),
                         max_entry_price=Decimal(53))
        assert False
    except HaltError as error:
        assert "signal cap" in str(error)
    assert client.created_bodies == [] and journal.load() == []
    print("PASS close horizon and unsafe live requote block new entries")


def test_live_fill_risk_uses_executor_requote_immediately():
    from collections import defaultdict

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.book = (Decimal(51), Decimal(20),
                         Decimal(53), Decimal(20))

        def top_of_book(self, ticker):
            return self.book

        def entry_allowed(self, ticker, now, required_seconds):
            return True

        def early_close_risk(self, ticker):
            return False

    class FilledOnLowerRequote:
        journal = None
        last_outcome_id = None

        def __init__(self, feed):
            self.feed = feed
            self.last_observation = None

        def execute(self, ticker, side, contracts, **kwargs):
            self.feed.book = (Decimal(10), Decimal(20),
                              Decimal(12), Decimal(20))
            self.last_observation = {
                "ticker": ticker, "bid": Decimal(10),
                "ask": Decimal(12), "observed_at": 22.0,
            }
            return Decimal(52), Decimal(20), Decimal(0)

    cfg = Config(max_daily_loss_usd=2)
    cfg.paper_trading = False
    feed = Feed()
    for ts in range(1, 21):
        feed.history["T"].append((float(ts), Decimal(60)))
    feed.history["T"].append((21.0, Decimal(52)))
    executor = FilledOnLowerRequote(feed)
    ctx = Context(cfg, feed, ScalpStrategy(cfg), executor, None, Safety(cfg),
                  clock=lambda: 22.0)
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                 observed_at=21.0)
    assert ctx.latest_bid["T"] == Decimal(10)
    assert ctx.bid_ts["T"] == 22.0
    assert ctx.safety.tripped and "loss limit" in ctx.safety.tripped_reason
    print("PASS immediate post-fill risk uses the executor's lower requote")


def test_replay_enforces_logged_market_lifecycle():
    import csv as _csv
    from replay import replay

    cfg = Config(max_hold_seconds=300, close_buffer_seconds=60)
    for name, close_ts, early in (
            ("near-close", 350.0, "false"),
            ("early-close", 1000.0, "true")):
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w", newline="") as handle:
            writer = _csv.writer(handle)
            writer.writerow(RESEARCH_HEADER)
            for ts in range(1, 21):
                writer.writerow(research_row(
                    cfg=cfg, session=name, ts=ts, mid=60, bid=59,
                    ask=61, close_ts=close_ts,
                    can_close_early=early))
            writer.writerow(research_row(
                cfg=cfg, session=name, ts=21, mid=52, bid=51, ask=53,
                close_ts=close_ts, can_close_early=early))
            writer.writerow(research_row(
                cfg=cfg, session=name, ts=22, event_id="", ticker="",
                event="session_end", detail="operator interrupt",
                mid="", bid="", ask="", bid_qty="", ask_qty=""))
        result = replay(path, cfg=cfg)
        assert result["trades"] == [] and result["evaluable"], result
    print("PASS replay enforces logged close horizon and early-close risk")


def test_termination_signals_route_through_interrupt():
    import signal as _signal
    from bot import termination_signals_as_interrupt

    original = _signal.getsignal(_signal.SIGTERM)
    interrupted = False
    try:
        with termination_signals_as_interrupt():
            installed = _signal.getsignal(_signal.SIGTERM)
            assert callable(installed)
            installed(_signal.SIGTERM, None)
    except KeyboardInterrupt:
        interrupted = True
    assert interrupted
    assert _signal.getsignal(_signal.SIGTERM) == original
    print("PASS SIGTERM/SIGHUP route through safe-shutdown interrupt path")


if __name__ == "__main__":
    test_create_contract()
    test_ack_vs_poll_contract()
    test_current_orderbook_contract()
    test_current_market_contract_and_empty_book_normalization()
    test_endpoint_separation()
    test_http_boundary_wiring_and_strict_envelopes()
    test_get_429_retries_with_exponential_backoff()
    test_get_429_retry_exhaustion_is_bounded()
    test_mutating_429_is_never_retried()
    test_portfolio_contracts_decimal()
    test_pagination()
    test_pagination_fails_closed()
    test_core_units()
    test_delayed_fill_and_prejournal_reconcile()
    test_contradictory_truth_stays_unresolved()
    test_ack_fill_is_binding_and_identity_checked()
    test_oversell_rejected_before_post()
    test_order_quantities_and_fill_identity_must_converge()
    test_ioc_canceled_quantity_is_modeled_explicitly()
    test_terminal_zero_requires_stability_grace()
    test_cancel_polls_until_terminal()
    test_cancel_limbo_halts()
    test_cancel_failure_halts()
    test_late_fill_after_cancel()
    test_unknown_state_halts()
    test_submit_ambiguity()
    test_ambiguity_blocks_flatten()
    test_flatten_partial_retries_and_authority()
    test_restart_reconciliation()
    test_reconciliation_drains_later_orders_after_earlier_ambiguity()
    test_unapplied_filled_outcome_blocks_restart()
    test_startup_cancel_race_and_errors()
    test_startup_cancel_must_reach_terminal()
    test_obsolete_pending_order_state_fails_closed()
    test_open_order_listing_queries_only_resting_in_one_subaccount()
    test_flatten_refuses_exchange_only_exposure_without_cost_basis()
    test_flatten_nonflat_final_state_raises()
    test_periodic_resolves_orders_and_reports_remaining_ambiguity()
    test_shutdown_cancel_is_terminal_verified()
    test_runtime_error_and_ctrl_c_preserve_honest_paper_residuals()
    test_per_market_quarantine()
    test_critical_market_errors_halt_instead_of_quarantine()
    test_real_order_gate_is_inside_executor()
    test_live_executor_requires_fresh_book_for_unseen_ticker()
    test_run_session_real_config_stops_before_api()
    test_global_error_not_erased_and_auth_rate_limit_halt()
    test_global_halt_stops_current_market_sweep_immediately()
    test_loss_breach_stops_before_next_market_action()
    test_stale_initial_quotes_halt()
    test_missing_bid_risk_halts()
    test_open_loss_limit_fee_aware()
    test_open_loss_mark_respects_zero_depth()
    test_daily_pnl_ledger_survives_strategy_restart()
    test_daily_pnl_rolls_at_utc_midnight_and_is_idempotent()
    test_config_rejects_unsafe_research_parameters()
    test_exclusive_process_lock()
    test_durable_state_paths_are_absolute_and_cwd_independent()
    test_stable_train_test_split()
    test_signal_rejects_future_history()
    test_analyzer_preserves_order_and_censors_horizons()
    test_analyzer_requires_a_clean_terminal_record()
    test_research_log_preserves_no_quote_and_event_group()
    test_analyzer_rejects_legacy_per_ticker_split()
    test_malformed_quote_rows_disqualify_research()
    test_malformed_executable_books_fail_closed()
    test_analyzer_end_to_end_v5_smoke()
    test_analyzer_attributes_one_shared_portfolio_replay()
    test_aggregate_fee_rounding()
    test_residual_valuation_respects_depth_and_slippage()
    test_unknown_depth_never_means_unlimited_fill()
    test_subcent_sell_fill_is_never_improved()
    test_pricefeed_learns_event_id_from_quote()
    test_market_envelope_to_research_log_preserves_event_identity()
    test_replay_exact_paper_path_and_residual()
    test_pending_paper_order_uses_first_observed_due_quote()
    test_quote_timestamp_is_causal_at_latency_boundary()
    test_pending_entries_count_toward_position_limit()
    test_replay_empty_ticker_selection_processes_nothing()
    test_replay_eof_never_fabricates_flatten_fills()
    test_pricefeed_and_replayfeed_produce_identical_paper_fills()
    test_actual_runtime_driver_matches_replay()
    test_staleness_is_checked_between_market_requests()
    test_preflight_warns_but_accepts_valid_empty_portfolio_collections()
    test_preflight_market_sample_uses_exactly_one_page()
    test_market_collections_skip_only_known_unsupported_products()
    test_market_collection_skips_never_hide_schema_drift()
    test_discovery_and_preflight_report_unsupported_market_counts()
    test_discovery_uses_maximum_page_and_caps_monitored_markets()
    test_explicit_tickers_respect_monitoring_cap()
    test_response_format_errors_include_raw_values()
    test_replay_reports_halt_and_resets_daily_risk_at_utc_midnight()
    test_replay_honors_logged_same_day_starting_loss()
    test_replay_uses_log_creation_day_when_first_quote_is_after_midnight()
    test_replay_requires_durable_clean_session_terminal()
    test_replay_rejects_nonmonotonic_observation_order()
    test_replay_rejects_config_or_code_provenance_mismatch()
    test_pnl_ledger_rejects_day_timestamp_corruption()
    test_analyzer_horizons_are_sorted_unique()
    test_unavailable_market_isolated_unless_exposed()
    test_close_horizon_and_live_requote_block_unsafe_entries()
    test_live_fill_risk_uses_executor_requote_immediately()
    test_replay_enforces_logged_market_lifecycle()
    test_termination_signals_route_through_interrupt()
    test_live_and_demo_disabled()
    print("\nALL TESTS PASS (104 tests)")
