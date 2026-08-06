"""Inci v6 paper-only test suite. Contract fixtures follow official V2 shapes
specified in review (create on /portfolio/events/orders with side=bid|ask,
separate price, fp string count, time_in_force, self_trade_prevention_type;
acks without status; poll/list on /portfolio/orders; fp/dollar portfolio
fields). HTTP wiring uses fake sessions; neither demo nor production order
endpoints are called. Every reviewed issue has a regression. Run: python tests.py
"""
# TASK9_ROUND19_FROZEN_V6_PATH_PROBE_BEGIN_V1
import os as _task9_probe_os
import sys as _task9_probe_sys

if _task9_probe_os.environ.get("INCI_TASK9_BOOTSTRAP_PATH_PROBE") == "FROZEN_V6_SCRIPT":
    import hashlib as _task9_probe_hashlib
    import json as _task9_probe_json

    _task9_probe_policy_sha = (
        "4fc73c4632f1af17183e3d164bdefad21955eabd4b7c248e797d28242f466b79"
    )
    _task9_probe_expected = (
        "/Users/mthanki/Downloads/inci-tennis-v1",
        "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/"
        "Python.framework/Versions/3.14/lib/python314.zip",
        "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/"
        "Python.framework/Versions/3.14/lib/python3.14",
        "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/"
        "Python.framework/Versions/3.14/lib/python3.14/lib-dynload",
        "/Users/mthanki/.venvs/inci-expert-py314/lib/python3.14/site-packages",
    )
    _task9_probe_roles = (
        "COMMAND_CWD", "ABSENT_STDLIB_ZIP", "RESOLVED_STDLIB",
        "STDLIB_DYNLOAD", "VENV_PURELIB",
    )
    _task9_probe_states = ("PRESENT", "ABSENT", "PRESENT", "PRESENT", "PRESENT")
    _task9_probe_actual = tuple(
        _task9_probe_os.path.abspath(value or _task9_probe_os.getcwd())
        for value in _task9_probe_sys.path
    )
    if _task9_probe_actual != _task9_probe_expected:
        raise RuntimeError("task9_bootstrap_path_probe_invalid")

    def _task9_probe_identity(path):
        value = _task9_probe_os.stat(path, follow_symlinks=False)
        return (
            value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_gid, value.st_nlink, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns,
        )

    _task9_probe_rows = tuple(
        {
            "index": index,
            "absolute_path": path,
            "role": _task9_probe_roles[index],
            "state": _task9_probe_states[index],
            "path_stat_identity": (
                None if _task9_probe_states[index] == "ABSENT"
                else _task9_probe_identity(path)
            ),
        }
        for index, path in enumerate(_task9_probe_expected)
    )
    if _task9_probe_os.path.lexists(_task9_probe_expected[1]):
        raise RuntimeError("task9_bootstrap_path_probe_invalid")
    _task9_probe_projection = {
        "schema_version": 1,
        "policy_sha256": _task9_probe_policy_sha,
        "rows": _task9_probe_rows,
    }
    _task9_probe_payload = _task9_probe_json.dumps(
        _task9_probe_projection,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    _task9_probe_sha = _task9_probe_hashlib.sha256(
        b"INCI-TASK-9-IMPORT-SEARCH-ROW-PROJECTION-V1\0" + _task9_probe_payload
    ).hexdigest()
    print(f"INCI_TASK9_CHILD_PATH_V1 FROZEN_V6_SCRIPT {_task9_probe_sha}")
    raise SystemExit(0)
# TASK9_ROUND19_FROZEN_V6_PATH_PROBE_END_V1

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


def current_sports_filters():
    return {
        "filters_by_sports": {
            "All sports": {"competitions": {},
                           "scopes": ["Games", "Futures"]},
            "Tennis": {
                "competitions": {
                    "ATP Washington": {"scopes": ["Games", "Set 1 Winner"]},
                },
                "scopes": ["Games", "Set Winner"],
            },
        },
        "sport_ordering": ["All sports", "Tennis"],
    }


def current_series(ticker="KXATP", **overrides):
    row = {"ticker": ticker, "category": "Sports", "tags": ["Tennis"]}
    row.update(overrides)
    return row


def current_milestone(milestone_id="milestone-1", **overrides):
    row = {
        "id": milestone_id,
        "category": "Sports",
        "type": "game",
        "start_date": "2026-07-26T18:00:00Z",
        "title": "Washington match",
        "details": {"league": "ATP Washington",
                    "main_game_event_ticker": "KXATP-26JUL26-ONE"},
        "primary_event_tickers": ["KXATP-26JUL26-ONE"],
        "related_event_tickers": ["KXATP-26JUL26-TOTAL"],
    }
    row.update(overrides)
    return row


def current_event(event_ticker="KXATP-26JUL26-ONE", **overrides):
    row = {
        "event_ticker": event_ticker,
        "series_ticker": "KXATP",
        "category": "Sports",
        "title": "Player A vs Player B",
        "markets": [current_market(ticker="KXATP-26JUL26-ONE-M1",
                                    event_ticker=event_ticker)],
    }
    row.update(overrides)
    return row


def test_current_sports_filters_contract():
    from schemas import parse_sports_filters_response

    parsed = parse_sports_filters_response(current_sports_filters())
    assert parsed == {
        "sport_ordering": ("All sports", "Tennis"),
        "sports": {
            "All sports": {"scopes": frozenset(("Games", "Futures")),
                           "competitions": {}},
            "Tennis": {
                "scopes": frozenset(("Games", "Set Winner")),
                "competitions": {
                    "ATP Washington": frozenset(("Games", "Set 1 Winner")),
                },
            },
        },
    }
    print("PASS current sports filters contract is normalized strictly")


def test_sports_filters_reject_malformed_scopes_and_competitions():
    from schemas import parse_sports_filters_response

    malformed = (
        {"filters_by_sports": {"Tennis": {"competitions": {},
                                             "scopes": ["Games", "Games"]}},
         "sport_ordering": ["Tennis"]},
        {"filters_by_sports": {"Tennis": {"competitions": {"ATP": {
            "scopes": ["Games", "Games"]}}, "scopes": ["Games"]}},
         "sport_ordering": ["Tennis"]},
        {"filters_by_sports": {"Tennis": {"competitions": [],
                                             "scopes": ["Games"]}},
         "sport_ordering": ["Tennis"]},
        {"filters_by_sports": {"Tennis": {"competitions": {},
                                             "scopes": "Games"}},
         "sport_ordering": ["Tennis"]},
        {"filters_by_sports": {"Tennis": {"competitions": {},
                                             "scopes": ["Games"]}},
         "sport_ordering": ["tennis"]},
    )
    for response in malformed:
        try:
            parse_sports_filters_response(response)
            assert False, response
        except SchemaError:
            pass
    print("PASS malformed sports scopes and competitions fail closed")


def test_current_sports_series_contract():
    from schemas import parse_series_list_response

    assert parse_series_list_response({"series": [current_series()]}) == (
        {"series_ticker": "KXATP", "category": "Sports",
         "tags": ("Tennis",)},)
    print("PASS current sports series contract is normalized strictly")


def test_series_response_accepts_off_category_and_null_tags():
    from schemas import parse_series_list_response

    missing_tags = current_series("KXWEATHER", category="Weather")
    del missing_tags["tags"]
    parsed = parse_series_list_response({"series": [
        current_series("KXATP"),
        current_series("KXPOL", category="Politics", tags=None),
        missing_tags,
    ]})
    assert parsed[1:] == (
        {"series_ticker": "KXPOL", "category": "Politics", "tags": ()},
        {"series_ticker": "KXWEATHER", "category": "Weather", "tags": ()},
    )
    print("PASS off-category and null-tag series remain valid metadata")


def test_series_response_rejects_duplicate_tickers_and_bad_tags():
    from schemas import parse_series_list_response

    for response in (
            {"series": [current_series(), current_series()]},
            {"series": [current_series(tags="Tennis")]},
            {"series": [current_series(tags=["Tennis", "Tennis"])]},
            {"series": [current_series(tags=[""])]},
            {"series": [current_series(category="")]},
    ):
        try:
            parse_series_list_response(response)
            assert False, response
        except SchemaError:
            pass
    print("PASS duplicate series tickers and malformed tags fail closed")


def test_series_response_rejects_nonempty_cursor_as_incomplete():
    from schemas import parse_series_list_response

    try:
        parse_series_list_response({
            "series": [current_series()],
            "cursor": "more-series",
        })
        assert False
    except SchemaError as error:
        assert "cursor" in str(error)
        assert "incomplete" in str(error)
    print("PASS nonempty Series cursor cannot masquerade as complete inventory")


def test_current_milestone_contract():
    from schemas import parse_milestone, parse_milestones_page

    parsed = parse_milestone(current_milestone())
    assert parsed == {
        "milestone_id": "milestone-1", "category": "Sports", "type": "game",
        "start_ts": 1785088800.0, "title": "Washington match",
        "league": "ATP Washington", "main_game_event_ticker": "KXATP-26JUL26-ONE",
        "primary_event_tickers": ("KXATP-26JUL26-ONE",),
        "related_event_tickers": ("KXATP-26JUL26-TOTAL",),
    }
    rows, cursor = parse_milestones_page(
        {"milestones": [current_milestone()], "cursor": "next"})
    assert rows == (parsed,) and cursor == "next"
    print("PASS current milestone contract is normalized strictly")


def test_milestone_accepts_empty_event_ticker_lists():
    """Main-game metadata remains valid when optional Event lists are empty."""
    from schemas import parse_milestone

    parsed = parse_milestone(current_milestone(
        primary_event_tickers=[], related_event_tickers=[]))

    assert parsed["main_game_event_ticker"] == "KXATP-26JUL26-ONE"
    assert parsed["primary_event_tickers"] == ()
    assert parsed["related_event_tickers"] == ()
    print("PASS milestone accepts empty optional Event ticker lists")


def test_milestone_contract_rejects_bad_details_dates_and_tickers():
    from schemas import parse_milestone

    malformed = (
        current_milestone(details=[]),
        current_milestone(start_date="2026-07-26T18:00:00"),
        current_milestone(details={"league": "", "main_game_event_ticker": None}),
        current_milestone(details={"league": None,
                                   "main_game_event_ticker": 7}),
        current_milestone(primary_event_tickers=["EVENT", "EVENT"]),
        current_milestone(related_event_tickers="EVENT"),
        current_milestone(id=""),
    )
    for row in malformed:
        try:
            parse_milestone(row)
            assert False, row
        except SchemaError:
            pass
    parsed = parse_milestone(current_milestone(
        details={"league": None, "main_game_event_ticker": None}))
    assert parsed["league"] is None and parsed["main_game_event_ticker"] is None
    print("PASS malformed milestone details, dates, and tickers fail closed")


def test_current_nested_event_contract_uses_market_parser():
    from schemas import parse_event, parse_event_response, parse_events_page

    event = current_event()
    parsed = parse_event(event)
    assert parsed["event_ticker"] == "KXATP-26JUL26-ONE"
    assert parsed["category"] == "Sports"
    assert parsed["markets"][0]["ticker"] == "KXATP-26JUL26-ONE-M1"
    assert parsed["market_skips"] == {}
    rows, cursor = parse_events_page({"events": [event], "cursor": ""})
    assert rows == (parsed,) and cursor == ""
    direct = parse_event_response({"event": event, "markets": event["markets"]})
    assert direct == parsed
    broken = current_event(markets=[current_market(
        event_ticker="KXATP-26JUL26-ONE", close_time="not-a-date")])
    try:
        parse_event(broken)
        assert False
    except SchemaError as error:
        assert "not-a-date" in str(error)
    try:
        parse_event_response({"event": event,
                              "markets": [current_market(ticker="OTHER",
                                                          event_ticker=event["event_ticker"])]})
        assert False
    except SchemaError:
        pass
    print("PASS nested events use the strict Market parser")


def test_direct_event_uses_nested_markets_when_top_level_is_empty():
    from schemas import parse_event_response

    event = current_event()
    parsed = parse_event_response({"event": event, "markets": []})
    assert parsed["event_ticker"] == "KXATP-26JUL26-ONE"
    assert [market["ticker"] for market in parsed["markets"]] == [
        "KXATP-26JUL26-ONE-M1"]
    print("PASS direct event uses populated nested Markets over empty wrapper")


def test_direct_event_rejects_nonempty_wrapper_when_nested_markets_empty():
    from schemas import parse_event_response

    event = current_event(markets=[])
    for top_markets in (
            [current_market(event_ticker=event["event_ticker"])],
            [{"ticker": 7}],
    ):
        try:
            parse_event_response({"event": event, "markets": top_markets})
            assert False, top_markets
        except SchemaError:
            pass
    print("PASS populated direct wrapper Markets require matching nested Markets")


def test_nested_event_counts_only_recognized_unsupported_markets():
    from schemas import parse_event

    event = current_event(markets=[
        current_market(ticker="BINARY", event_ticker="KXATP-26JUL26-ONE"),
        current_market(ticker="SCALAR", event_ticker="KXATP-26JUL26-ONE",
                       market_type="scalar"),
        current_market(ticker="KXMVE-ONE", event_ticker="KXMVE-EVENT",
                       mve_collection_ticker="COLLECTION"),
    ])
    # Unsupported products are skipped only after their own identity parses;
    # their mismatched event ticker must not be able to conceal malformed data.
    event["markets"][2]["event_ticker"] = event["event_ticker"]
    parsed = parse_event(event)
    assert [m["ticker"] for m in parsed["markets"]] == ["BINARY"]
    assert parsed["market_skips"] == {"mve": 1, "scalar": 1}
    try:
        parse_event(current_event(markets=[current_market(
            event_ticker="OTHER")]))
        assert False
    except SchemaError:
        pass
    print("PASS nested events skip only recognized unsupported Markets")


def test_sports_client_uses_documented_public_queries():
    from datetime import datetime, timezone
    from kalshi_client import KalshiClient

    class Client(KalshiClient):
        def __init__(self):
            self.cfg = Config(); self.base = self.cfg.api_base; self.calls = []
            self.responses = [
                current_sports_filters(), {"series": [current_series()]},
                {"milestones": [current_milestone()], "cursor": ""},
                {"milestones": [current_milestone("milestone-2")], "cursor": ""},
                {"events": [current_event()], "cursor": ""},
                {"event": current_event(), "markets": current_event()["markets"]},
            ]

        def _request(self, method, endpoint, params=None, body=None, auth=False):
            self.calls.append((method, endpoint, dict(params or {}), auth))
            return self.responses.pop(0)

    client = Client()
    start = datetime(2026, 7, 26, tzinfo=timezone.utc)
    assert client.get_sports_filters()["sport_ordering"] == ("All sports", "Tennis")
    assert client.get_sports_series()[0]["series_ticker"] == "KXATP"
    milestones, metadata = client.get_sports_milestones(
        minimum_start_date=start, competition="ATP Washington")
    assert milestones[0]["milestone_id"] == "milestone-1"
    assert metadata == {"pages": 1, "rows": 1, "raw_rows": 1,
                        "market_skips": {}}
    related, _ = client.get_sports_milestones(
        minimum_start_date=start, related_event_ticker="KXATP-26JUL26-ONE")
    assert related[0]["milestone_id"] == "milestone-2"
    events, event_metadata = client.get_open_events(series_ticker="KXATP")
    assert events[0]["event_ticker"] == "KXATP-26JUL26-ONE"
    assert event_metadata["market_skips"] == {}
    assert client.last_sports_market_skips == {}
    assert client.get_event("KXATP-26JUL26-ONE")["event_ticker"] == "KXATP-26JUL26-ONE"
    assert client.calls == [
        ("GET", "/search/filters_by_sport", {}, False),
        ("GET", "/series", {"category": "Sports"}, False),
        ("GET", "/milestones", {"category": "Sports", "minimum_start_date": "2026-07-26T00:00:00Z", "competition": "ATP Washington", "limit": 500}, False),
        ("GET", "/milestones", {"category": "Sports", "minimum_start_date": "2026-07-26T00:00:00Z", "related_event_ticker": "KXATP-26JUL26-ONE", "limit": 500}, False),
        ("GET", "/events", {"series_ticker": "KXATP", "status": "open", "with_nested_markets": "true", "limit": 200}, False),
        ("GET", "/events/KXATP-26JUL26-ONE", {"with_nested_markets": "true"}, False),
    ]
    for competition, related in ((None, None), ("ATP", "EVENT"), ("", None)):
        try:
            client.get_sports_milestones(minimum_start_date=start,
                                         competition=competition,
                                         related_event_ticker=related)
            assert False
        except SchemaError:
            pass
    print("PASS sports client emits only documented public queries")


def test_public_discovery_metadata_requests_are_paced():
    from datetime import datetime, timezone
    from kalshi_client import KalshiClient

    client = KalshiClient(Config())
    responses = [
        current_sports_filters(),
        {"series": [current_series()]},
        {"milestones": [current_milestone()], "cursor": "next"},
        {"milestones": [current_milestone("milestone-2")], "cursor": ""},
        {"market": current_market()},
    ]
    calls = []

    def request(method, endpoint, params=None, body=None, auth=False):
        calls.append((method, endpoint))
        return responses.pop(0)

    now = [0.0]
    delays = []

    def sleep(delay):
        delays.append(delay)
        now[0] += delay

    client._request = request
    client._monotonic = lambda: now[0]
    client._sleep = sleep
    client.get_sports_filters()
    client.get_sports_series()
    rows, metadata = client.get_sports_milestones(
        competition="ATP Washington",
        minimum_start_date=datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert hasattr(client, "get_market_for_discovery")
    market = client.get_market_for_discovery("T")

    assert len(rows) == 2 and metadata["pages"] == 2
    assert market["ticker"] == "T"
    assert calls == [
        ("GET", "/search/filters_by_sport"),
        ("GET", "/series"),
        ("GET", "/milestones"),
        ("GET", "/milestones"),
        ("GET", "/markets/T"),
    ]
    assert delays == [0.05, 0.05, 0.05, 0.05]
    print("PASS public Sports discovery GETs use conservative pacing")


def test_discovery_cursors_missing_nonstring_repeated_and_capped_fail():
    from kalshi_client import KalshiClient
    import kalshi_client as kc

    class Client(KalshiClient):
        def __init__(self, pages):
            self.cfg = Config(); self.base = self.cfg.api_base; self.pages = pages
            self.calls = 0

        def _request(self, method, endpoint, params=None, body=None, auth=False):
            response = self.pages[min(self.calls, len(self.pages) - 1)]
            self.calls += 1
            return response

    for pages in (
            [{"events": [current_event()]}],
            [{"events": [current_event()], "cursor": None}],
            [{"events": [current_event()], "cursor": "same"},
             {"events": [current_event()], "cursor": "same"}],
    ):
        try:
            Client(pages).get_open_events(series_ticker="KXATP")
            assert False, pages
        except SchemaError:
            pass
    old_cap = kc.MAX_PAGES
    kc.MAX_PAGES = 1
    try:
        try:
            Client([{"events": [current_event()], "cursor": "more"}]).get_open_events(
                series_ticker="KXATP")
            assert False
        except SchemaError as error:
            assert "pagination exceeded 1 pages" in str(error)
    finally:
        kc.MAX_PAGES = old_cap
    print("PASS discovery pagination rejects incomplete inventories")


RESEARCH_HEADER = [
    "schema_version", "session_id", "starting_daily_pnl_usd",
    "starting_utc_day", "utc_day", "config_fingerprint",
    "code_fingerprint", "selected_sports", "ts", "ticker",
    "sport", "league", "series_ticker", "milestone_id", "event_ticker",
    "scheduled_start_ts", "event", "detail", "close_ts",
    "can_close_early", "mid", "bid", "ask", "bid_qty", "ask_qty",
]


def research_row(*, cfg=None, session="S", starting_pnl=0,
                 starting_day="1970-01-01", day="1970-01-01", ts=1,
                 selected_sports=("Tennis",), selected_sports_text=None,
                 ticker="T", sport="Tennis", league="League",
                 series_ticker="KXSERIES", milestone_id="M",
                 event_ticker="E", scheduled_start_ts=3600,
                 event="quote", detail="",
                 close_ts=4070908800.0, can_close_early="false",
                 mid=50, bid=49, ask=51, bid_qty=10, ask_qty=10):
    import json
    from research_log import config_fingerprint, code_fingerprint
    selected_sports = tuple(selected_sports)
    cfg = cfg or Config(sports=list(selected_sports))
    if not cfg.sports:
        cfg.sports = list(selected_sports)
    selected_text = (selected_sports_text
                     if selected_sports_text is not None
                     else json.dumps(list(selected_sports),
                                     separators=(",", ":")))
    if event in ("session_end", "session_halt"):
        ticker = sport = league = series_ticker = milestone_id = ""
        event_ticker = scheduled_start_ts = ""
        close_ts = can_close_early = ""
        mid = bid = ask = bid_qty = ask_qty = ""
    elif event != "quote":
        close_ts, can_close_early = "", ""
        mid = bid = ask = bid_qty = ask_qty = ""
    return [6, session, starting_pnl, starting_day, day,
            config_fingerprint(cfg), code_fingerprint(), selected_text, ts,
            ticker, sport, league, series_ticker, milestone_id,
            event_ticker, scheduled_start_ts, event, detail,
            close_ts, can_close_early,
            mid, bid, ask, bid_qty, ask_qty]


def research_provenance(
        *, sport="Tennis", league="League", series_ticker="KXSERIES",
        milestone_id="M", event_ticker="E", scheduled_start_ts=3600):
    from sports_discovery import ContractProvenance

    return ContractProvenance(
        sport=sport, league=league, series_ticker=series_ticker,
        milestone_id=milestone_id, event_ticker=event_ticker,
        scheduled_start_ts=scheduled_start_ts)


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


def test_public_sports_client_loads_private_key_only_for_auth():
    """Bad credentials must not block unauthenticated Sports discovery."""
    from kalshi_client import KalshiClient

    class Response:
        status_code = 200
        text = "ok"

        def raise_for_status(self):
            return None

        def json(self):
            return current_sports_filters()

    class PublicSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response()

    with tempfile.TemporaryDirectory() as directory:
        key_path = os.path.join(directory, "invalid.pem")
        with open(key_path, "w", encoding="utf-8") as handle:
            handle.write("not a private key")
        cfg = Config()
        cfg.api_key_id = "KEY"
        cfg.private_key_path = key_path

        client = KalshiClient(cfg)
        session = PublicSession()
        client.session = session
        filters = client.get_sports_filters()

        assert filters["sport_ordering"] == ("All sports", "Tennis")
        assert len(session.calls) == 1
        assert session.calls[0][2]["headers"] == {}
        try:
            client.get_balance()
            assert False
        except SchemaError as error:
            assert "private key" in str(error)
        assert len(session.calls) == 1
    print("PASS public Sports metadata does not load private credentials")


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
        except SchemaError as error:
            assert "pagination exceeded 2 pages" in str(error)
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
    feed.install_discovery(_task4_discovery(
        _task4_contract(ticker="A", event_ticker="EVENT-A")))
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
            ts=13, event_ticker="", ticker="", event="session_end",
            detail="operator interrupt", mid="", bid="", ask="",
            bid_qty="", ask_qty=""))
    series, groups, selected_sports, provenance = load(path)
    assert [p[0] for p in series["T"]] == [1.0, 10.0, 12.0]
    assert groups == {"T": "E"}
    assert selected_sports == ("Tennis",)
    assert provenance["T"] == research_provenance()
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
                    ts=2, event_ticker="", ticker="", event=terminal,
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
    cfg = Config(sports=["Tennis"])
    provenance = {"T": research_provenance(event_ticker="EVENT-7")}
    log = ResearchLog(
        directory, clock=lambda: 123.5, session_id="SESSION-A",
        config=cfg, provenance_by_ticker=provenance)
    log.tick("T", None, None, None, None, None,
             ts=123.5, event="no_quote")
    with open(log.tick_path) as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["schema_version"] == "6"
    assert rows[0]["session_id"] == "SESSION-A"
    assert rows[0]["starting_daily_pnl_usd"] == "0"
    assert rows[0]["starting_utc_day"] == "1970-01-01"
    assert rows[0]["utc_day"] == "1970-01-01"
    assert rows[0]["ts"] == "123.5"
    assert rows[0]["selected_sports"] == '["Tennis"]'
    assert rows[0]["event_ticker"] == "EVENT-7"
    assert rows[0]["sport"] == "Tennis"
    assert rows[0]["event"] == "no_quote"
    assert rows[0]["mid"] == ""
    assert len(rows[0]["config_fingerprint"]) == 64
    assert len(rows[0]["code_fingerprint"]) == 64
    assert "ticks_v6_" in log.tick_path
    from replay import replay
    result = replay(log.tick_path)
    assert result["data_gaps"] == 1 and not result["evaluable"]
    second = ResearchLog(
        directory, clock=lambda: 123.5, session_id="SESSION-B",
        config=cfg, provenance_by_ticker=provenance)
    assert second.tick_path != log.tick_path
    try:
        ResearchLog(
            directory, clock=lambda: 123.5, session_id="SESSION-A",
            config=cfg, provenance_by_ticker=provenance)
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
    assert raised is not None and "v6" in str(raised)
    print("PASS analyzer rejects legacy logs outside strict v6 validation")


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
    try:
        load_log(path)
        assert False
    except ValueError as error:
        assert "quote" in str(error) or "ask" in str(error)
    print("PASS malformed quote row fails strict analysis and replay")


def test_analyzer_end_to_end_v6_smoke():
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
                    event_ticker=event, ticker=f"T-{bucket}",
                    bid_qty=100, ask_qty=100))
        writer.writerow(research_row(
            session="SMOKE", ts=200, event_ticker="", ticker="",
            event="session_end", detail="operator interrupt",
            mid="", bid="", ask="", bid_qty="", ask_qty=""))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        analyze_main(path)
    text = output.getvalue()
    assert "MARK-OUTS (NON-EXECUTABLE" in text
    assert "FULL REPLAY" in text and "TRAIN:" in text and "TEST:" in text
    print("PASS analyzer runs end-to-end on a v6 two-partition session")


def test_analyzer_replays_shared_portfolio_exactly_once():
    import contextlib
    import csv as _csv
    import io
    import analyze as analyzer
    from replay import replay as real_replay

    groups = {}
    candidate = 0
    while set(groups) != {"TRAIN", "TEST"}:
        group = f"PORTFOLIO-{candidate}"
        groups.setdefault(analyzer.split_bucket(group), group)
        candidate += 1
    cfg = Config(max_open_positions=1, tp_trail_cents=0)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)

        def quote(ts, ticker, event, mid, bid, ask):
            writer.writerow(research_row(
                cfg=cfg, session="SHARED", ts=ts, event_ticker=event,
                ticker=ticker, mid=mid, bid=bid, ask=ask,
                bid_qty=100, ask_qty=100))

        for ts in range(1, 21):
            quote(float(ts), "ACTIVE", groups["TRAIN"], 60, 59, 61)
            quote(ts + 0.1, "BLOCKED", groups["TEST"], 60, 59, 61)
        # Delayed IOC BUY/SELL at touch. ACTIVE dips first and holds the
        # single shared slot the whole time, so BLOCKED never enters.
        quote(21.0, "ACTIVE", groups["TRAIN"], 52, 51, 53)   # A IOC BUY due@22
        quote(21.1, "BLOCKED", groups["TEST"], 52, 51, 53)   # blocked: A pending
        quote(22.0, "ACTIVE", groups["TRAIN"], 52, 51, 53)   # ask<=cap -> A fills@53
        quote(22.1, "BLOCKED", groups["TEST"], 52, 51, 53)   # blocked: A holds slot
        quote(23.0, "ACTIVE", groups["TRAIN"], 52, 51, 53)
        quote(23.1, "BLOCKED", groups["TEST"], 52, 51, 53)   # blocked
        quote(24.0, "ACTIVE", groups["TRAIN"], 52, 51, 53)
        quote(24.1, "BLOCKED", groups["TEST"], 52, 51, 53)   # blocked
        quote(25.0, "ACTIVE", groups["TRAIN"], 59, 58, 60)   # TP: A IOC SELL due@26
        quote(25.1, "BLOCKED", groups["TEST"], 59, 58, 60)   # blocked: A still open
        quote(26.0, "ACTIVE", groups["TRAIN"], 59, 58, 60)   # bid>=floor -> A SELL@58
        # BLOCKED's quiet quote arrives only after ACTIVE fully traded. In
        # isolation BLOCKED completes BUY->SELL on its own dip/TP path; in the
        # shared replay it already missed every slot and does not dip here.
        quote(26.1, "BLOCKED", groups["TEST"], 61, 60, 62)
        writer.writerow(research_row(
            cfg=cfg, session="SHARED", ts=27, event_ticker="", ticker="",
            event="session_end", detail="operator interrupt",
            mid="", bid="", ask="", bid_qty="", ask_qty=""))

    full = real_replay(path, cfg=cfg)
    isolated = real_replay(path, tickers={"BLOCKED"}, cfg=cfg)
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
    original_load = analyzer.load
    original_replay = analyzer.replay
    load_calls = []
    replay_calls = []

    def counted_load(*args, **kwargs):
        load_calls.append((args, kwargs))
        return original_load(*args, **kwargs)

    def counted_replay(*args, **kwargs):
        replay_calls.append((args, kwargs))
        assert "tickers" not in kwargs
        return real_replay(*args, **kwargs)

    analyzer.load = counted_load
    analyzer.replay = counted_replay
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            analyzer.main(path)
    finally:
        analyzer.load = original_load
        analyzer.replay = original_replay
        analyzer.CFG = old_cfg
        analyzer.HORIZONS = old_horizons
        analyzer.MARKOUT_TOLERANCE_S = old_tolerance
    text = output.getvalue()
    assert load_calls == [((path,), {})]
    assert replay_calls == [((path,), {"cfg": cfg})]
    assert "TRAIN: 1 markets, 1 exits" in text
    assert "TEST: 1 markets, 0 exits" in text
    print("PASS analyzer replays one shared portfolio exactly once")


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
    # Delayed IOC path: entry at ask, TP exit at entry+TP (no adverse slip).
    entry = Decimal(50)
    exit_fill = entry + Config().take_profit
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
    # IOC BUY: unknown ask depth cannot fill; the one attempt is canceled
    # (never fabricated into an unlimited fill, never retained as GTC).
    ex = Executor(cfg, None,
                  BookFeed(bid=Decimal(50), bq=None,
                           ask=Decimal(50), aq=None),
                  clock=lambda: 0.0, sleep=lambda _: None)
    ex.submit_paper("T", "BUY", 20, now=0.0)
    assert ex.process_due_paper_orders(0.0, ticker="T")[0][1] is None
    assert not ex.has_pending("T", side="BUY")
    # Marketable stop-loss SELL: unknown bid depth cannot fill either.
    ex.submit_paper("T", "SELL", 20, "stop-loss", now=0.0)
    assert ex.process_due_paper_orders(0.0, ticker="T")[0][1] is None
    assert not ex.has_pending("T", side="SELL")
    try:
        ex.execute("T", "BUY", 20)
        assert False
    except HaltError as error:
        assert "blocking paper execution" in str(error)
    print("PASS unknown depth cannot fill; IOC miss cancels; blocking "
          "paper bypass rejected")


def test_stop_deadline_does_not_slide_while_pending():
    """A pending stop must keep its original due_at across later quotes."""
    from collections import defaultdict
    from strategy import Position

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.book = (Decimal(40), Decimal(20),
                         Decimal(42), Decimal(20))

        def top_of_book(self, ticker):
            return self.book

    now = [10.0]
    cfg = Config(); cfg.sim_latency_s = 1.0
    feed = Feed()
    ex = Executor(cfg, None, feed, clock=lambda: now[0], sleep=lambda _: None)
    strat = ScalpStrategy(cfg)
    strat.positions["T"] = Position(
        "T", Decimal(50), Decimal(5), opened_at=0.0,
        entry_fee_usd=Decimal("0.01"))
    ctx = Context(cfg, feed, strat, ex, None, Safety(cfg),
                  clock=lambda: now[0])

    process_tick(ctx, "T", Decimal(41), Decimal(40), Decimal(42),
                 observed_at=10.0)
    pending = ex.get_pending("T", side="SELL")
    assert pending is not None
    assert "stop-loss" in pending.reason
    assert pending.due_at == 11.0

    # Later quotes while the stop is still pending must NOT cancel/resubmit
    # with a fresh due_at (deadline sliding).
    for ts in (10.3, 10.6, 10.9):
        now[0] = ts
        process_tick(ctx, "T", Decimal(41), Decimal(40), Decimal(42),
                     observed_at=ts)
        still = ex.get_pending("T", side="SELL")
        assert still is pending
        assert still.due_at == 11.0

    now[0] = 11.0
    process_tick(ctx, "T", Decimal(41), Decimal(40), Decimal(42),
                 observed_at=11.0)
    assert "T" not in strat.positions
    assert not ex.has_pending("T")
    print("PASS pending stop due_at does not slide across quotes")


def test_time_exit_upgrades_working_take_profit():
    """Max-hold must replace a pending TP so the 300s bound is binding."""
    from collections import defaultdict
    from strategy import Position

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.book = (Decimal(52), Decimal(20),
                         Decimal(54), Decimal(20))

        def top_of_book(self, ticker):
            return self.book

    now = [299.0]
    cfg = Config(tp_trail_cents=0); cfg.sim_latency_s = 5.0; cfg.max_hold_seconds = 300
    feed = Feed()
    ex = Executor(cfg, None, feed, clock=lambda: now[0], sleep=lambda _: None)
    strat = ScalpStrategy(cfg)
    # Bid already at TP (+5) near max-hold; TP is submitted with due_at
    # after the hold limit so it is still pending when time exit fires.
    strat.positions["T"] = Position(
        "T", Decimal(47), Decimal(4), opened_at=0.0,
        entry_fee_usd=Decimal("0.01"))
    ctx = Context(cfg, feed, strat, ex, None, Safety(cfg),
                  clock=lambda: now[0])
    process_tick(ctx, "T", Decimal(53), Decimal(52), Decimal(54),
                 observed_at=299.0)
    tp = ex.get_pending("T", side="SELL")
    assert tp is not None and "take-profit" in tp.reason
    assert tp.due_at == 304.0

    # Past max hold while TP is still pending: time exit upgrades once.
    now[0] = 300.5
    feed.book = (Decimal(50), Decimal(20), Decimal(52), Decimal(20))
    process_tick(ctx, "T", Decimal(51), Decimal(50), Decimal(52),
                 observed_at=300.5)
    timed = ex.get_pending("T", side="SELL")
    assert timed is not None
    assert timed.reason.startswith("time exit")
    assert timed.due_at == 305.5
    assert timed is not tp
    print("PASS time exit upgrades a pending take-profit once")


def test_ioc_ask_cap_miss_cancels_entry():
    """If the ask worsens past the signal cap, the delayed IOC misses."""
    from collections import defaultdict

    class MutableFeed:
        def __init__(self):
            self.history = defaultdict(list)
            self.book = (Decimal(50), Decimal(20),
                         Decimal(53), Decimal(20))

        def top_of_book(self, ticker):
            return self.book

    cfg = Config(); cfg.sim_latency_s = 0
    feed = MutableFeed()
    ex = Executor(cfg, None, feed, clock=lambda: 0.0, sleep=lambda _: None)
    order = ex.submit_paper("T", "BUY", 10, "dip", now=0.0)
    assert order.limit_price == Decimal(53)
    # Worse ask at due time: one attempt, miss, canceled.
    feed.book = (Decimal(54), Decimal(20), Decimal(56), Decimal(20))
    assert ex.process_due_paper_orders(0.0, ticker="T")[0][1] is None
    assert not ex.has_pending("T")
    print("PASS IOC entry miss on worsened ask cancels remainder")

def test_entry_edge_uses_executable_ask_depth():
    """Edge gate and submitted size must use visible ask depth, not hope."""
    cfg = Config(); cfg.contracts_per_trade = 20
    strat = ScalpStrategy(cfg)
    hist = [(float(ts), Decimal(60)) for ts in range(-20, 0)]
    # Thin book: only 3 contracts offered — size and projection must use 3.
    sig = strat.check_entry(
        "T", hist, 0.0, Decimal(52), Decimal(51), Decimal(53),
        ask_qty=Decimal(3))
    assert sig is not None
    assert sig["contracts"] == Decimal(3)
    assert "size 3" in sig["reason"]
    # Unknown / zero depth cannot enter.
    assert strat.check_entry(
        "T", hist, 0.0, Decimal(52), Decimal(51), Decimal(53),
        ask_qty=None) is None
    assert strat.check_entry(
        "T", hist, 0.0, Decimal(52), Decimal(51), Decimal(53),
        ask_qty=0) is None
    print("PASS entry sizes and edges against executable ask depth")


def test_trailing_tp_lets_runners_extend_past_arm():
    """Arm at take_profit, hold through a spike, sell on trail giveback."""
    cfg = Config(tp_trail_cents=2)
    strat = ScalpStrategy(cfg)
    strat.record_fill(
        "T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20), now=0.0)
    # Arm floor (+5) alone does not exit while trail > 0.
    assert strat.check_exit("T", Decimal(55), now=1.0) is None
    assert strat.positions["T"].peak_bid == Decimal(55)
    # Set-driven spike: peak runs to +12, still no sell.
    assert strat.check_exit("T", Decimal(62), now=2.0) is None
    assert strat.positions["T"].peak_bid == Decimal(62)
    # Giveback of trail (2c) while still above the arm floor → variable TP.
    sig = strat.check_exit("T", Decimal(60), now=3.0)
    assert sig is not None
    assert sig["reason"].startswith("take-profit trail")
    assert "peak +12c" in sig["reason"]
    assert "giveback 2c" in sig["reason"]
    # Hard stop still wins immediately on a dump.
    strat2 = ScalpStrategy(cfg)
    strat2.record_fill(
        "T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20), now=0.0)
    strat2.check_exit("T", Decimal(60), now=1.0)  # build peak
    assert strat2.check_exit("T", Decimal(40), now=2.0)["reason"].startswith(
        "stop-loss")
    print("PASS trailing TP holds runners and sells on giveback")


def test_trailing_tp_zero_keeps_fixed_arm_exit():
    cfg = Config(tp_trail_cents=0)
    strat = ScalpStrategy(cfg)
    strat.record_fill(
        "T", "BUY", Decimal(50), Decimal(20), fee_usd(50, 20), now=0.0)
    sig = strat.check_exit("T", Decimal(55), now=1.0)
    assert sig is not None
    assert sig["reason"].startswith("take-profit,")
    print("PASS tp_trail_cents=0 preserves fixed take-profit at arm")


def test_pricefeed_uses_installed_event_identity():
    from market_data import PriceFeed

    class Client:
        def get_market(self, ticker):
            return {"ticker": ticker, "event_ticker": "MATCH-123",
                    "status": "active",
                    "yes_bid": Decimal(50),
                    "yes_ask": Decimal(52), "yes_bid_size": Decimal(10),
                    "yes_ask_size": Decimal(10)}

    feed = PriceFeed(Config(), Client(), clock=lambda: 1.0)
    feed.install_discovery(_task4_discovery(
        _task4_contract(ticker="CONTRACT-A", event_ticker="MATCH-123")))
    feed.get_quote("CONTRACT-A")
    assert feed.group_id("CONTRACT-A") == "MATCH-123"
    print("PASS PriceFeed uses preinstalled immutable event identity")


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

    cfg = Config(sports=["Tennis"])
    feed = PriceFeed(cfg, Client(), clock=lambda: 7.25)
    feed.install_discovery(_task4_discovery(
        _task4_contract(
            ticker="CONTRACT-A", event_ticker="MATCH-123"),
        _task4_contract(
            ticker="CONTRACT-B", event_ticker="MATCH-123")))
    directory = tempfile.mkdtemp()
    log = ResearchLog(
        directory, clock=lambda: 7.25, session_id="SESSION-E2E",
        config=cfg, provenance_by_ticker=feed.provenance_by_ticker)
    for ticker in ("CONTRACT-A", "CONTRACT-B"):
        mid, bid, ask, observed_at = feed.get_quote(ticker)
        _, bid_qty, _, ask_qty = feed.top_of_book(ticker)
        log.tick(ticker, mid, bid, ask, bid_qty, ask_qty,
                 ts=observed_at,
                 close_ts=feed.lifecycle(ticker)[0],
                 can_close_early=feed.lifecycle(ticker)[1])
    with open(log.tick_path) as handle:
        rows = list(_csv.DictReader(handle))
    assert {row["event_ticker"] for row in rows} == {"MATCH-123"}
    assert {row["ticker"] for row in rows} == {"CONTRACT-A", "CONTRACT-B"}
    assert {row["ts"] for row in rows} == {"7.25"}
    try:
        feed.group_id("UNKNOWN")
        assert False
    except SchemaError:
        pass
    print("PASS official market envelope preserves event identity to CSV")


def test_replay_exact_paper_path_and_residual():
    import csv as _csv
    from replay import replay
    cfg = Config(sports=["Tennis"], tp_trail_cents=0)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(RESEARCH_HEADER)
        t = 0.0
        for i in range(80):
            t += 1.5
            w.writerow(research_row(
                cfg=cfg, session="RESIDUAL", ts=t,
                mid=60, bid=59, ask=61, bid_qty=500, ask_qty=500))
        # Dip triggers a delayed IOC BUY (signal ask cap 53).
        w.writerow(research_row(
            cfg=cfg, session="RESIDUAL", ts=t + 1.5,
            mid=52, bid=51, ask=53, bid_qty=500, ask_qty=500))
        # Due quote: BUY fills at the then-current ask (51), depth-limited
        # to the ask size (6); unfilled remainder is canceled (IOC).
        w.writerow(research_row(
            cfg=cfg, session="RESIDUAL", ts=t + 2.6,
            mid=50, bid=49, ask=51, bid_qty=500, ask_qty=6))
        # crash with ZERO bid depth: the stop-loss market exit cannot fill
        for k in range(40):
            w.writerow(research_row(
                cfg=cfg, session="RESIDUAL", ts=t + 4.1 + k * 1.5,
                mid=40, bid=39, ask=41, bid_qty=0, ask_qty=500))
    r = replay(path, cfg=cfg)
    buys = [tr for tr in r["trades"] if tr[1] == "BUY"]
    assert buys and buys[0][2] == Decimal(51)       # current ask at fill
    assert buys[0][3] == Decimal(6)                 # depth-limited
    assert not any(tr[1] == "SELL" for tr in r["trades"])   # zero bid depth
    assert r["residual_contracts"] == Decimal(6)
    assert r["residual_marked"] < 0                 # loss NOT hidden
    print("PASS replay: exact paper path (IOC fill at current ask, depth "
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
    cfg = Config(tp_trail_cents=0); cfg.sim_latency_s = 1.0
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
    # First observed DUE quote for T with ask still at/below the signal cap
    # (53): BUY fills at the current ask (53), depth-limited to size (7).
    feed.apply(1.1, "T", Decimal(52), Decimal(51), Decimal(50),
               Decimal(53), Decimal(7))
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53))
    assert strat.positions["T"].entry_price == Decimal(53)
    assert strat.positions["T"].contracts == Decimal(7)
    assert not ex.has_pending("T")
    print("PASS pending paper order fills only on first observed due quote")


def test_quote_timestamp_is_causal_at_latency_boundary():
    """Processing delay cannot turn a pre-due quote into a due fill."""
    from collections import defaultdict

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            # Locked book so the IOC BUY is always fillable; only the
            # due-time boundary decides whether it fills.
            self.book = (Decimal(51), Decimal(20),
                         Decimal(51), Decimal(20))

        def top_of_book(self, ticker):
            return self.book

    wall_clock = [0.0]
    cfg = Config(tp_trail_cents=0); cfg.sim_latency_s = 1.0
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
    process_tick(ctx, "T", Decimal(51), Decimal(51), Decimal(51),
                 observed_at=0.99)
    assert ex.has_pending("T") and not strat.positions

    wall_clock[0] = 1.11
    process_tick(ctx, "T", Decimal(51), Decimal(51), Decimal(51),
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

    cfg = Config(tp_trail_cents=0); cfg.max_open_positions = 1
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
    cfg = Config(sports=["Tennis"])
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(RESEARCH_HEADER)
        w.writerow(research_row(
            cfg=cfg, session="FILTER", ticker="ONLY",
            event_ticker="ONLY-EVENT"))
        w.writerow(research_row(
            cfg=cfg, session="FILTER", ts=2, ticker="",
            event="session_end", detail="operator interrupt"))
    result = replay(path, tickers=set(), cfg=cfg)
    assert result["rows_processed"] == 0
    assert result["trades"] == []
    assert not result["evaluable"]
    print("PASS empty ticker selection remains empty")


def test_replay_eof_never_fabricates_flatten_fills():
    import csv as _csv
    from replay import replay
    cfg = Config(sports=["Tennis"], tp_trail_cents=0)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(RESEARCH_HEADER)
        for ts in range(1, 21):
            w.writerow(research_row(
                cfg=cfg, session="EOF", ts=ts, mid=60, bid=59, ask=61,
                bid_qty=100, ask_qty=100))
        w.writerow(research_row(
            cfg=cfg, session="EOF", ts=21, mid=52, bid=51, ask=53,
            bid_qty=100, ask_qty=100))
        # Ask still at/below the signal cap; depth-limited IOC fill.
        w.writerow(research_row(
            cfg=cfg, session="EOF", ts=22, mid=52, bid=51, ask=53,
            bid_qty=100, ask_qty=5))
    result = replay(path, cfg=cfg)
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
        # Dip: delayed IOC BUY (signal ask cap 53).
        (21.0, "T", Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(100)),
        # Due: depth-limited IOC fill at current ask 53.
        (22.0, "T", Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(6)),
        # Take-profit: delayed IOC SELL (signal bid floor 58).
        (24.0, "T", Decimal(59), Decimal(58), Decimal(60),
         Decimal(6), Decimal(100)),
        # Due: IOC SELL fills at current bid 58.
        (25.0, "T", Decimal(59), Decimal(58), Decimal(60),
         Decimal(6), Decimal(100)),
    ]

    def drive(feed, clock, apply):
        cfg = Config(tp_trail_cents=0); cfg.sim_latency_s = 1.0
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
            return {"ticker": ticker, "event_ticker": "EVENT-T",
                    "status": "active",
                    "yes_bid": row[3], "yes_ask": row[4],
                    "yes_bid_size": row[5], "yes_ask_size": row[6],
                    "close_ts": 4070908800.0, "can_close_early": False}

    real_clock = VirtualClock()
    client = StreamClient()
    price_feed = PriceFeed(Config(tp_trail_cents=0), client, clock=real_clock.time)
    price_feed.install_discovery(_task4_discovery(
        _task4_contract(ticker="T", event_ticker="EVENT-T")))

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
        # Dip: delayed IOC BUY (signal ask cap 53).
        (21.0, "T", Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(100)),
        # Due: depth-limited IOC fill at current ask 53.
        (22.0, "T", Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(6)),
        # Take-profit: delayed IOC SELL (signal bid floor 58).
        (24.0, "T", Decimal(59), Decimal(58), Decimal(60),
         Decimal(6), Decimal(100)),
        # Due: IOC SELL fills at current bid 58.
        (25.0, "T", Decimal(59), Decimal(58), Decimal(60),
         Decimal(6), Decimal(100)),
    ]
    cfg = Config(sports=["Tennis"], tp_trail_cents=0); cfg.sim_latency_s = 1.0
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        for ts, ticker, mid, bid, ask, bid_qty, ask_qty in rows:
            writer.writerow(research_row(
                cfg=cfg, session="DRIVER", ts=ts, ticker=ticker,
                event_ticker="EVENT-T", mid=mid, bid=bid, ask=ask,
                bid_qty=bid_qty, ask_qty=ask_qty))
        writer.writerow(research_row(
            cfg=cfg, session="DRIVER", ts=26, ticker="",
            event="session_end", detail="operator interrupt"))
    replay_result = replay(path, cfg=cfg)

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


def test_cli_parses_sports_without_hardcoded_choices():
    """The CLI keeps operator spelling until public metadata resolves it."""
    from bot import parse_cli

    options = parse_cli(["--sports", " tennis ,Basketball "])
    assert options.mode == "paper"
    assert options.requested_sports == ("tennis", "Basketball")
    print("PASS CLI preserves uncanonicalized Sports selections")


def test_cli_rejects_empty_or_duplicate_sports():
    """Blank components and case-folded duplicates are unusable selections."""
    from bot import parse_cli

    for argv in (("--sports", "tennis,,Basketball"),
                 ("--sports", "tennis,TENNIS")):
        try:
            parse_cli(argv)
            assert False, argv
        except ValueError as error:
            assert "usage:" in str(error)
    for argv in (("--check", "--list-sports"),
                 ("--check", "--sports", "Tennis")):
        try:
            parse_cli(argv)
            assert False, argv
        except ValueError as error:
            assert "usage:" in str(error)
    print("PASS CLI rejects empty/duplicate Sports and invalid modes")


def test_config_validates_unique_selection_lists():
    """Configuration selections must be explicit, nonblank, and unique."""
    for kwargs in ({"sports": ["Tennis", "Tennis"]},
                   {"sports": [" "]},
                   {"tickers": ["KX", "KX"]},
                   {"tickers": [""]}):
        try:
            Config(**kwargs)
            assert False, kwargs
        except ValueError:
            pass
    assert Config().sports == []
    print("PASS config validates unique nonblank Sports/tickers")


def test_cli_rejects_sports_with_configured_tickers():
    """A paper session chooses either dynamic Sports or exact tickers."""
    import contextlib
    import io
    import bot as bot_module

    state_root = tempfile.mkdtemp()
    constructed = []

    class Client:
        pass

    def config_factory():
        return Config(tickers=["KXGAME-1"], state_root=state_root)

    def client_factory(cfg):
        constructed.append(cfg)
        return Client()

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert bot_module.main(["--sports", "Tennis"],
                               config_factory=config_factory,
                               client_factory=client_factory) == 1
    assert constructed and constructed[0].sports == ["Tennis"]
    assert "both configured tickers and Sports" in output.getvalue()
    print("PASS paper startup rejects simultaneous tickers and Sports")


def test_paper_startup_requires_sports_or_explicit_tickers():
    """A paper session cannot fall back to an implicit discovery selection."""
    import contextlib
    import io
    import bot as bot_module

    state_root = tempfile.mkdtemp()

    class Client:
        pass

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert bot_module.main([],
                               config_factory=lambda: Config(
                                   state_root=state_root),
                               client_factory=lambda cfg: Client()) == 1
    assert "select at least one Sport or configure explicit tickers" \
        in output.getvalue()
    print("PASS paper startup requires an explicit discovery selection")


def test_list_sports_is_public_and_creates_no_session_artifact():
    """Listing Sports succeeds/fails without constructing session state."""
    import contextlib
    import io
    import bot as bot_module

    original_feed = bot_module.PriceFeed
    old_cwd = os.getcwd()
    try:
        bot_module.PriceFeed = lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError("PriceFeed must not be constructed")))
        for should_fail in (False, True):
            workdir = tempfile.mkdtemp()
            os.chdir(workdir)
            calls = []

            class Client:
                def get_sports_filters(self):
                    calls.append("filters")
                    if should_fail:
                        raise SchemaError("filters unavailable")
                    return {
                        "sport_ordering":
                            ("All sports", "Tennis", "Cricket")}

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = bot_module.main(
                    ["--list-sports"],
                    config_factory=lambda: Config(
                        state_root=os.path.join(workdir, "state")),
                    client_factory=lambda cfg: Client())
            assert result == (1 if should_fail else 0)
            assert calls == ["filters"]
            assert os.listdir(workdir) == []
            if should_fail:
                assert "filters unavailable" in output.getvalue()
                assert "Traceback" not in output.getvalue()
            else:
                assert output.getvalue().splitlines()[-3:] == [
                    "All sports", "Tennis", "Cricket"]
    finally:
        bot_module.PriceFeed = original_feed
        os.chdir(old_cwd)
    print("PASS --list-sports success/failure is public and artifact-free")


def test_cli_reports_unknown_arguments_without_traceback():
    """Operator input errors are reported as usage, never a Python traceback."""
    import contextlib
    import io
    import bot as bot_module

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert bot_module.main(["--unknown"]) == 2
    assert "usage:" in output.getvalue()
    assert "Traceback" not in output.getvalue()
    print("PASS unknown CLI arguments fail cleanly")


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
    feed.install_discovery(_task4_discovery(_task4_contract()))
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
    executor.submit_paper("T", "SELL", 1, "stop-loss", now=0.0)
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


class _Task8PreflightClient:
    """Strict public-page preflight double; all HTTP-shaped calls are recorded."""

    last_market_skips = {}
    last_sports_market_skips = {}

    def __init__(self, *, filters_response=None, series_response=None,
                 milestone_response=None, event_response=None):
        from kalshi_client import KalshiClient

        self._production_type = KalshiClient
        self.cfg = Config()
        self.base = self.cfg.api_base
        self.calls = []
        self.portfolio_calls = []
        self.responses = {
            "/search/filters_by_sport": (
                current_sports_filters() if filters_response is None
                else filters_response),
            "/series": (
                {"series": [current_series()]} if series_response is None
                else series_response),
            "/milestones": (
                {"milestones": [current_milestone()],
                 "cursor": "milestone-next"} if milestone_response is None
                else milestone_response),
            "/events": (
                {"events": [current_event()], "cursor": "event-next"}
                if event_response is None else event_response),
        }

    def _request(self, method, endpoint, params=None, body=None, auth=False):
        self.calls.append(
            (method, endpoint, dict(params or {}), body, bool(auth)))
        if endpoint not in self.responses:
            raise AssertionError(f"unexpected preflight request: {endpoint}")
        return self.responses[endpoint]

    def _request_public_metadata(self, endpoint, *, params=None):
        return self._production_type._request_public_metadata(
            self, endpoint, params=params)

    def get_sports_filters(self):
        return self._production_type.get_sports_filters(self)

    def get_sports_series(self):
        return self._production_type.get_sports_series(self)

    def get_sports_milestones_page(self, **kwargs):
        return self._production_type.get_sports_milestones_page(self, **kwargs)

    def get_open_events_page(self, **kwargs):
        return self._production_type.get_open_events_page(self, **kwargs)

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_markets_sample(self, **_kwargs):
        return [parse_market(current_market())]

    def get_balance(self):
        self.portfolio_calls.append("balance")
        return {"balance": 0}

    def get_open_orders(self):
        self.portfolio_calls.append("orders")
        return []

    def get_fills(self):
        self.portfolio_calls.append("fills")
        return []

    def get_positions(self):
        self.portfolio_calls.append("positions")
        return []

    def get_sports_milestones(self, **_kwargs):
        raise AssertionError("preflight followed exhaustive milestone inventory")

    def get_open_events(self, **_kwargs):
        raise AssertionError("preflight followed exhaustive Event inventory")

    def get_order(self, *_args, **_kwargs):
        raise AssertionError("preflight polled an individual order")

    def create_order(self, *_args, **_kwargs):
        raise AssertionError("preflight attempted order creation")

    def cancel_order(self, *_args, **_kwargs):
        raise AssertionError("preflight attempted order cancellation")

    def scan_markets(self, *_args, **_kwargs):
        raise AssertionError("preflight attempted discovery/ranking")


def _run_task8_preflight(client, *, authenticated=True):
    import contextlib
    import io
    import bot
    from sports_discovery import LocalDayWindow

    cfg = Config()
    cfg.api_key_id = "KEY" if authenticated else ""
    original = getattr(bot, "local_day_window", None)
    bot.local_day_window = lambda: LocalDayWindow(
        "America/Los_Angeles",
        "2026-07-26T00:00:00-07:00",
        "2026-07-27T00:00:00-07:00",
        1785049200.0,
        1785135600.0)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            result = bot.preflight(cfg, client)
    finally:
        if original is None:
            delattr(bot, "local_day_window")
        else:
            bot.local_day_window = original
    return result, output.getvalue()


def test_preflight_validates_public_sports_metadata_without_orders():
    client = _Task8PreflightClient()
    result, text = _run_task8_preflight(client)

    assert result
    assert client.calls == [
        ("GET", "/search/filters_by_sport", {}, None, False),
        ("GET", "/series", {"category": "Sports"}, None, False),
        ("GET", "/milestones", {
            "category": "Sports",
            "minimum_start_date": "2026-07-26T07:00:00Z",
            "competition": "ATP Washington",
            "limit": 500,
        }, None, False),
        ("GET", "/events", {
            "series_ticker": "KXATP",
            "status": "open",
            "with_nested_markets": "true",
            "limit": 200,
        }, None, False),
    ]
    assert all("cursor" not in call[2] for call in client.calls)
    assert "Sports filters OK: 1 canonical Sports; Games-capable: Tennis" in text
    assert "Sports series schema OK: 1 rows" in text
    assert ("Sports milestone page OK: competition=ATP Washington rows=1 "
            "more_pages=true; not followed by --check") in text
    assert "Sports event page OK: series=KXATP events=1 " in text
    assert "more_pages=true; not followed by --check" in text
    print("PASS --check validates bounded public Sports pages without orders")


def test_preflight_reports_metadata_skips_and_unobserved_portfolio_rows():
    event_ticker = "KXATP-26JUL26-ONE"
    event = current_event(markets=[
        current_market(ticker="BINARY", event_ticker=event_ticker),
        current_market(ticker="SCALAR", event_ticker=event_ticker,
                       market_type="scalar"),
    ])
    client = _Task8PreflightClient(
        event_response={"events": [event], "cursor": ""})
    result, text = _run_task8_preflight(client)

    assert result
    assert "unsupported skipped: total=1 types=scalar=1" in text
    assert ("WARNING row schemas not observed for empty collections "
            "['orders', 'fills', 'positions']") in text
    assert "portfolio row schemas observed live: none" in text
    print("PASS --check reports Sports skips and empty portfolio row coverage")


def test_preflight_never_calls_order_mutation_endpoints():
    import bot

    client = _Task8PreflightClient()
    forbidden = ("PriceFeed", "ProcessLock", "ResearchLog", "Executor")
    originals = {name: getattr(bot, name) for name in forbidden}

    def forbidden_constructor(*_args, **_kwargs):
        raise AssertionError("preflight constructed a session/discovery object")

    with tempfile.TemporaryDirectory() as directory:
        before = tuple(os.listdir(directory))
        old_cwd = os.getcwd()
        try:
            os.chdir(directory)
            for name in forbidden:
                setattr(bot, name, forbidden_constructor)
            result, _ = _run_task8_preflight(client)
        finally:
            os.chdir(old_cwd)
            for name, value in originals.items():
                setattr(bot, name, value)
        after = tuple(os.listdir(directory))

    assert result
    assert before == after == ()
    assert all(method == "GET" and body is None and not auth
               for method, _endpoint, _params, body, auth in client.calls)
    assert not any(endpoint.startswith("/portfolio/events/orders")
                   for _method, endpoint, *_rest in client.calls)
    assert not any(endpoint.startswith("/portfolio/orders/")
                   for _method, endpoint, *_rest in client.calls)
    print("PASS --check never mutates/orders/discovers or creates artifacts")


def test_preflight_counts_sports_without_assuming_pseudo_row():
    filters = current_sports_filters()
    del filters["filters_by_sports"]["All sports"]
    filters["sport_ordering"] = ["Tennis"]
    client = _Task8PreflightClient(filters_response=filters)

    result, text = _run_task8_preflight(client)

    assert result
    assert "Sports filters OK: 1 canonical Sports" in text
    print("PASS --check counts Sports explicitly without a pseudo-row assumption")


def test_preflight_warns_when_games_metadata_is_unavailable():
    no_games = current_sports_filters()
    no_games["filters_by_sports"]["Tennis"]["competitions"][
        "ATP Washington"]["scopes"] = ["Futures"]
    scenarios = (
        (_Task8PreflightClient(filters_response=no_games),
         "no Games-capable Sport/competition is currently available"),
        (_Task8PreflightClient(milestone_response={
            "milestones": [], "cursor": ""}),
         "no usable Sports milestone"),
        (_Task8PreflightClient(series_response={
            "series": [current_series("KXOTHER")]}),
         "no resolvable official Sports Series"),
    )
    for client, warning in scenarios:
        result, text = _run_task8_preflight(client)
        assert result, text
        assert f"[check] WARNING {warning}" in text
    assert not any(call[1] == "/milestones" for call in scenarios[0][0].calls)
    assert not any(call[1] == "/events" for call in scenarios[1][0].calls)
    assert not any(call[1] == "/events" for call in scenarios[2][0].calls)
    print("PASS unavailable Games metadata warns and skips only downstream sample")


def test_preflight_rejects_sampled_sports_schema_drift():
    clients = (
        _Task8PreflightClient(filters_response={
            "filters_by_sports": [], "sport_ordering": ["Tennis"]}),
        _Task8PreflightClient(series_response={"series": "bad"}),
        _Task8PreflightClient(milestone_response={
            "milestones": [current_milestone()], "cursor": 7}),
        _Task8PreflightClient(event_response={
            "events": [current_event(category=7)], "cursor": ""}),
    )
    for client in clients:
        result, text = _run_task8_preflight(client)
        assert not result, text
        assert "[check] FAIL Sports " in text
        counts = {}
        for _method, endpoint, _params, _body, _auth in client.calls:
            counts[endpoint] = counts.get(endpoint, 0) + 1
        assert all(count == 1 for count in counts.values()), counts
    print("PASS sampled Sports schema/cursor drift fails bounded preflight")


def test_preflight_public_checks_run_without_credentials():
    client = _Task8PreflightClient()
    result, text = _run_task8_preflight(client, authenticated=False)

    assert not result
    assert [call[1] for call in client.calls] == [
        "/search/filters_by_sport", "/series", "/milestones", "/events"]
    assert client.portfolio_calls == []
    assert "Sports event page OK" in text
    assert "FAIL no API key" in text
    print("PASS public Sports preflight runs before missing-auth failure")


def test_readme_documents_sports_commands_and_v6_break():
    with open(os.path.join(os.path.dirname(__file__), "README.md"),
              encoding="utf-8") as handle:
        text = handle.read()

    required = (
        "python bot.py --list-sports",
        "python bot.py --sports Tennis,Basketball",
        "python bot.py --check",
        "python analyze.py logs/ticks_v6_<YYYYMMDD>_<session-id>.csv",
        "comma-separated", "case-insensitive", "API-canonicalized",
        "only Games contracts", "local `[midnight, next midnight)`",
        "best ten", "selected once at startup", "no rotation",
        "Config.tickers", "mutually exclusive", "capped at ten",
        "Market → Event → official Series → current-day Games Milestone",
        "v6 rows", "shared-portfolio TEST", "demo/live remain disabled",
    )
    for phrase in required:
        assert phrase in text, phrase
    assert "ticks_v5" not in text
    assert "title keyword" not in text.casefold()
    assert "tennis-only" not in text.casefold()
    print("PASS README documents Sports operation and the strict v6 break")


def test_preflight_warns_but_accepts_valid_empty_portfolio_collections():
    from bot import preflight

    class CheckClient(_Task8PreflightClient):
        pass

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

    assert format_market_skips({}) == \
        "unsupported skipped: total=0 types=none"
    assert format_market_skips({"mve": 2, "scalar": 1}) == \
        "unsupported skipped: total=3 types=mve=2, scalar=1"

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
    print("PASS formatter/--check loudly report unsupported type counts")


def test_discovery_stops_before_generic_pagination_cap_when_first_page_fills_cap():
    """A useful first page must not force discovery through 20 more pages."""
    from kalshi_client import KalshiClient

    class Response:
        text = "fake response"

        def __init__(self, payload):
            self.payload = payload
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Session:
        def __init__(self, pages):
            self.pages = list(pages)
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response(self.pages.pop(0))

    pages = []
    for index in range(21):
        row = (current_market(ticker="ATP-FIRST", event_ticker="EVENT-1",
                              title="ATP tennis match")
               if index == 0 else
               current_market(ticker=f"OTHER-{index}",
                              title="unrelated listing"))
        pages.append({"markets": [row], "cursor": f"cursor-{index}"})
    client = KalshiClient(Config(max_monitored_markets=1))
    client.session = Session(pages)
    selected, metadata = client.scan_markets(
        lambda market: market["ticker"].startswith("ATP"), 1,
        status="open", limit=1000, mve_filter="exclude")
    assert [market["ticker"] for market in selected] == ["ATP-FIRST"]
    assert len(client.session.calls) == 1
    assert metadata == {
        "pages": 1, "rows": 1, "selected": 1, "truncated": True,
        "complete": False, "stop_reason": "selected_cap"}
    print("PASS client scan stops after its first qualifying page")


def test_discovery_filters_page_by_page_and_aggregates_skip_counts():
    from kalshi_client import KalshiClient

    class Response:
        text = "fake response"
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.pages = [
                {"markets": [
                    current_market(ticker="OTHER", title="unrelated"),
                    current_market(ticker="SCALAR", market_type="scalar"),
                ], "cursor": "page-2"},
                {"markets": [
                    current_market(ticker="ATP-ONE", event_ticker="EVENT-1",
                                   title="ATP tennis one"),
                    current_market(ticker="MVE",
                                   mve_collection_ticker="MVE-COLLECTION"),
                ], "cursor": "page-3"},
                {"markets": [
                    current_market(ticker="ATP-TWO", event_ticker="EVENT-2",
                                   title="ATP tennis two"),
                    current_market(ticker="ATP-THREE", event_ticker="EVENT-3",
                                   title="ATP tennis three"),
                ], "cursor": "more-pages"},
            ]
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response(self.pages.pop(0))

    cfg = Config(max_monitored_markets=3)
    client = KalshiClient(cfg)
    client.session = Session()
    selected, metadata = client.scan_markets(
        lambda market: market["ticker"].startswith("ATP"), 3,
        status="open", limit=1000, mve_filter="exclude")
    assert [market["ticker"] for market in selected] == [
        "ATP-ONE", "ATP-TWO", "ATP-THREE"]
    assert len(client.session.calls) == 3
    assert client.last_market_skips == {"mve": 1, "scalar": 1}
    assert metadata == {
        "pages": 3, "rows": 6, "selected": 3, "truncated": True,
        "complete": False, "stop_reason": "selected_cap"}
    assert client.last_market_scan == metadata
    print("PASS client scan filters each page and aggregates unsupported skips")


def test_discovery_page_cap_returns_explicit_truncated_partial_scan():
    from kalshi_client import KalshiClient

    class Response:
        text = "fake response"
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Session:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, **kwargs):
            page = self.calls
            self.calls += 1
            return Response({
                "markets": [current_market(ticker=f"OTHER-{page}",
                                            title="unrelated")],
                "cursor": f"cursor-{page}",
            })

    client = KalshiClient(Config())
    client.session = Session()
    selected, metadata = client.scan_markets(
        lambda market: market["ticker"].startswith("ATP"), 3,
        status="open", limit=1000, mve_filter="exclude")
    assert selected == []
    assert metadata == {
        "pages": 20, "rows": 20, "selected": 0, "truncated": True,
        "complete": False, "stop_reason": "page_cap"}
    assert client.last_market_scan == metadata
    assert client.session.calls == 20
    print("PASS discovery page cap returns a loud truncated partial scan")


def test_discovery_rejects_malformed_and_repeated_cursors():
    from kalshi_client import KalshiClient

    class Client(KalshiClient):
        def __init__(self, responses):
            self.cfg = Config()
            self.responses = list(responses)
            self.calls = 0
            self.last_market_skips = {}

        def _request(self, method, endpoint, params=None, body=None,
                     auth=False):
            response = self.responses[self.calls]
            self.calls += 1
            return response

    bad_cases = [
        [{"markets": [current_market(ticker="ATP")]}],
        [{"markets": [current_market(ticker="ATP")], "cursor": 7}],
        [{"markets": [current_market(ticker="OTHER")], "cursor": "same"},
         {"markets": [current_market(ticker="OTHER-2")], "cursor": "same"}],
    ]
    for responses in bad_cases:
        try:
            Client(responses).scan_markets(lambda market: False, 1)
            assert False, responses
        except SchemaError:
            pass
    print("PASS discovery rejects malformed and repeated cursors")


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
    cfg = Config(max_daily_loss_usd=1, tp_trail_cents=0)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        for ts, mid, bid, ask, bid_qty, ask_qty in rows:
            day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            writer.writerow(research_row(
                cfg=cfg, session="MIDNIGHT", starting_pnl=100,
                starting_day="2026-01-01", day=day, ts=ts,
                event_ticker="EVENT-T", mid=mid, bid=bid, ask=ask,
                bid_qty=bid_qty, ask_qty=ask_qty))
        terminal_ts = rows[-1][0] + 1
        writer.writerow(research_row(
            cfg=cfg, session="MIDNIGHT", starting_pnl=100,
            starting_day="2026-01-01", day="2026-01-02", ts=terminal_ts,
            event_ticker="", ticker="", event="session_end",
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
        writer.writerow(research_row(session="NOEVENT", event_ticker=""))
    try:
        replay(missing_event)
        assert False
    except ValueError as error:
        assert "event_ticker" in str(error)
    print("PASS malformed/nonfinite/crossed/ungrouped books fail closed")


def test_replay_honors_logged_same_day_starting_loss():
    import csv as _csv
    from replay import replay

    cfg = Config(max_daily_loss_usd=30, tp_trail_cents=0)
    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(RESEARCH_HEADER)
        writer.writerow(research_row(
            cfg=cfg, session="RESTART", starting_pnl=-31))
        writer.writerow(research_row(
            cfg=cfg, session="RESTART", starting_pnl=-31, ts=2,
            event_ticker="", ticker="", event="session_end",
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
    cfg = Config(max_daily_loss_usd=30, sports=["Tennis"], tp_trail_cents=0)
    log = ResearchLog(
        tempfile.mkdtemp(), clock=lambda: now[0],
        session_id="DELAYED-FIRST", starting_pnl=-31, config=cfg,
        provenance_by_ticker={"T": research_provenance()})
    now[0] = after
    log.tick("T", Decimal(50), Decimal(49), Decimal(51),
             Decimal(10), Decimal(10),
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
    cfg = Config(sports=["Tennis"])
    log = ResearchLog(
        tempfile.mkdtemp(), clock=lambda: now[0],
        session_id="TERMINAL", config=cfg,
        provenance_by_ticker={"T": research_provenance()})
    log.tick("T", Decimal(50), Decimal(49), Decimal(51),
             Decimal(10), Decimal(10),
             close_ts=4070908800.0, can_close_early=False)
    incomplete = replay(log.tick_path, cfg=cfg)
    assert not incomplete["evaluable"]
    assert incomplete["terminal_status"] == "missing"
    now[0] = 2.0
    log.end(clean=True, reason="operator interrupt")
    complete = replay(log.tick_path, cfg=cfg)
    assert complete["terminal_status"] == "clean"
    assert complete["evaluable"]
    print("PASS replay requires one durable clean terminal record")


def test_replay_rejects_nonmonotonic_observation_order():
    from research_log import ResearchLog
    from replay import replay

    cfg = Config(sports=["Tennis"])
    log = ResearchLog(
        tempfile.mkdtemp(), clock=lambda: 0.0,
        session_id="ORDER", config=cfg,
        provenance_by_ticker={"T": research_provenance()})
    for ts in (2.0, 1.0):
        log.tick("T", Decimal(50), Decimal(49), Decimal(51),
                 Decimal(10), Decimal(10), ts=ts,
                 close_ts=4070908800.0, can_close_early=False)
    log.end(clean=True, reason="operator interrupt", ts=3.0)
    try:
        replay(log.tick_path, cfg=cfg)
        assert False
    except ValueError as error:
        assert "non-monotonic" in str(error)
    print("PASS replay rejects reordered observations instead of sorting")


def test_replay_rejects_config_or_code_provenance_mismatch():
    from research_log import ResearchLog
    from replay import replay

    original = Config(sports=["Tennis"])
    log = ResearchLog(
        tempfile.mkdtemp(), clock=lambda: 1.0,
        session_id="PROVENANCE", config=original,
        provenance_by_ticker={"T": research_provenance()})
    log.tick("T", Decimal(50), Decimal(49), Decimal(51),
             Decimal(10), Decimal(10),
             close_ts=4070908800.0, can_close_early=False)
    log.end(clean=True, reason="operator interrupt", ts=2.0)
    for changed in (
            Config(dip_threshold=8), Config(poll_interval=2.0),
            Config(tickers=["OTHER"]),
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

    cfg = Config(max_hold_seconds=300, close_buffer_seconds=60, tp_trail_cents=0)
    feed = PriceFeed(cfg, MarketClient(), clock=lambda: 100.0)
    feed.install_discovery(_task4_discovery(_task4_contract()))
    feed.subscribe(["T"])
    mid, bid, ask, observed_at = feed.get_quote("T")
    for ts in range(80, 100):
        feed.history["T"].append((float(ts), Decimal(60)))
    ctx = Context(cfg, feed, ScalpStrategy(cfg), Executor(cfg, None, feed),
                  None, Safety(cfg), clock=lambda: 100.0)
    process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                 observed_at=observed_at)
    assert not ctx.executor.pending_paper
    assert ctx.entry_status["T"] == "blocked:close_horizon"

    nonpaper_cfg = Config(max_hold_seconds=300, close_buffer_seconds=60, tp_trail_cents=0)
    nonpaper_cfg.paper_trading = False
    nonpaper_ctx = Context(
        nonpaper_cfg, feed, ScalpStrategy(nonpaper_cfg), object(),
        None, Safety(nonpaper_cfg), clock=lambda: 100.0)
    process_tick(
        nonpaper_ctx, "T", Decimal(52), Decimal(51), Decimal(53),
        observed_at=observed_at)
    assert nonpaper_ctx.entry_status["T"] == "blocked:close_horizon"

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
    executor, journal = make_exec(Config(tp_trail_cents=0), client, UnsafeRequote())
    try:
        executor.execute("T", "BUY", 20,
                         expected_pre_position=Decimal(0),
                         max_entry_price=Decimal(53))
        assert False
    except HaltError as error:
        assert "signal cap" in str(error)
    assert client.created_bodies == [] and journal.load() == []
    print("PASS close horizon and unsafe live requote block new entries")


def test_paper_early_close_risk_is_visible_but_does_not_block_entry():
    from collections import defaultdict
    from contextlib import redirect_stdout
    from io import StringIO

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.history["T"].extend(
                (float(ts), Decimal(60)) for ts in range(1, 21))
            self.history["T"].append((21.0, Decimal(52)))

        def top_of_book(self, ticker):
            return Decimal(51), Decimal(20), Decimal(53), Decimal(20)

        def entry_allowed(self, ticker, now, required_seconds):
            return True

        def early_close_risk(self, ticker):
            return True

    cfg = Config(tp_trail_cents=0)
    feed = Feed()
    executor = Executor(cfg, None, feed)
    ctx = Context(cfg, feed, ScalpStrategy(cfg), executor, None, Safety(cfg),
                  clock=lambda: 21.0)
    output = StringIO()
    with redirect_stdout(output):
        process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                     observed_at=21.0)

    assert len(executor.pending_paper) == 1
    assert executor.pending_paper[0].ticker == "T"
    assert ctx.entry_status["T"] == "paper_allowed:can_close_early"
    executor.cancel_pending_paper()
    feed.history["T"] = [
        (21.0, Decimal(52)), (22.0, Decimal(52))]
    with redirect_stdout(output):
        process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                     observed_at=22.0)
    assert output.getvalue().count(
        "PAPER-ONLY T: can_close_early=true; entry remains enabled") == 1
    print("PASS paper early-close risk is visible without blocking research")


def test_nonpaper_early_close_risk_is_visible_and_blocks_entry():
    from collections import defaultdict
    from contextlib import redirect_stdout
    from io import StringIO

    class Feed:
        def __init__(self):
            self.history = defaultdict(list)
            self.history["T"].extend(
                (float(ts), Decimal(60)) for ts in range(1, 21))
            self.history["T"].append((21.0, Decimal(52)))

        def top_of_book(self, ticker):
            return Decimal(51), Decimal(20), Decimal(53), Decimal(20)

        def entry_allowed(self, ticker, now, required_seconds):
            return True

        def early_close_risk(self, ticker):
            return True

    cfg = Config()
    cfg.paper_trading = False
    feed = Feed()
    ctx = Context(cfg, feed, ScalpStrategy(cfg), object(), None, Safety(cfg),
                  clock=lambda: 21.0)
    output = StringIO()
    with redirect_stdout(output):
        process_tick(ctx, "T", Decimal(52), Decimal(51), Decimal(53),
                     observed_at=21.0)

    assert ctx.entry_status["T"] == "blocked:can_close_early"
    assert "BLOCKED T: can_close_early=true outside paper mode" \
        in output.getvalue()
    print("PASS non-paper early-close risk remains visibly blocked")


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

    cfg = Config(max_daily_loss_usd=2, tp_trail_cents=0)
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

    cfg = Config(max_hold_seconds=300, close_buffer_seconds=60, tp_trail_cents=0)
    results = {}
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
            # Ask still at/below signal cap so the delayed IOC BUY fills.
            writer.writerow(research_row(
                cfg=cfg, session=name, ts=22, mid=52, bid=51, ask=53,
                close_ts=close_ts, can_close_early=early))
            # Take-profit: delayed IOC SELL at the bid floor.
            writer.writerow(research_row(
                cfg=cfg, session=name, ts=23, mid=59, bid=58, ask=60,
                close_ts=close_ts, can_close_early=early))
            # Bid still at/above floor so the IOC SELL fills.
            writer.writerow(research_row(
                cfg=cfg, session=name, ts=24, mid=59, bid=58, ask=60,
                close_ts=close_ts, can_close_early=early))
            writer.writerow(research_row(
                cfg=cfg, session=name, ts=25, event_ticker="", ticker="",
                event="session_end", detail="operator interrupt",
                mid="", bid="", ask="", bid_qty="", ask_qty=""))
        results[name] = replay(path, cfg=cfg)

    assert results["near-close"]["trades"] == []
    assert results["near-close"]["evaluable"]
    assert [trade[1] for trade in results["early-close"]["trades"]] == [
        "BUY", "SELL"]
    assert results["early-close"]["evaluable"]
    print("PASS replay blocks close horizon but permits paper early-close data")


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


def test_sport_selection_is_case_insensitive_and_canonical():
    """API spelling/order, not operator casing/order, is the identity."""
    from sports_discovery import canonicalize_sports, list_supported_sports

    filters = {
        "sport_ordering": ("All sports", "Tennis", "Cricket"),
        "sports": {
            "All sports": {"scopes": frozenset(("Games",)),
                           "competitions": {}},
            "Tennis": {"scopes": frozenset(("Games",)),
                       "competitions": {"ATP": frozenset(("Games",))}},
            "Cricket": {"scopes": frozenset(("Games",)),
                        "competitions": {"IPL": frozenset(("Games",))}},
        },
    }

    class Client:
        def get_sports_filters(self):
            return filters

    assert list_supported_sports(Client()) == ("All sports", "Tennis", "Cricket")
    assert canonicalize_sports(("cricket", "TENNIS"), filters) == (
        "Tennis", "Cricket")
    print("PASS Sports selection is API-canonical and case-insensitive")


def test_all_sports_and_unknown_sports_are_rejected_with_choices():
    """Pseudo and unknown selections must show the live canonical choices."""
    from sports_discovery import canonicalize_sports

    filters = {
        "sport_ordering": ("All sports", "Tennis"),
        "sports": {
            "All sports": {"scopes": frozenset(("Games",)),
                           "competitions": {}},
            "Tennis": {"scopes": frozenset(("Games",)),
                       "competitions": {"ATP": frozenset(("Games",))}},
        },
    }
    for requested in (("All sports",), ("Soccer",)):
        try:
            canonicalize_sports(requested, filters)
            assert False, requested
        except ValueError as error:
            assert "All sports, Tennis" in str(error)
    print("PASS pseudo/unknown Sports include canonical choices in errors")


def test_selected_sport_requires_games_scope_and_competition():
    """A Sport needs Games both at its own scope and at a competition."""
    from sports_discovery import canonicalize_sports

    for sport in (
            {"scopes": frozenset(("Futures",)),
             "competitions": {"ATP": frozenset(("Games",))}},
            {"scopes": frozenset(("Games",)),
             "competitions": {"ATP": frozenset(("Futures",))}}):
        filters = {"sport_ordering": ("Tennis",),
                   "sports": {"Tennis": sport}}
        try:
            canonicalize_sports(("tennis",), filters)
            assert False, sport
        except ValueError as error:
            assert "Games" in str(error)
    invalid_details = {"sport_ordering": ("All sports", "Tennis"),
                       "sports": {"All sports": {"scopes": frozenset(("Games",)),
                                                 "competitions": {}},
                                  "Tennis": []}}
    try:
        canonicalize_sports(("Tennis",), invalid_details)
        assert False
    except ValueError as error:
        assert "choices: All sports, Tennis" in str(error)
    print("PASS selected Sports require Games scope and competition")


def test_local_day_window_is_half_open_and_dst_safe():
    """A local calendar day is not always 86,400 seconds in UTC."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from sports_discovery import local_day_window

    zone = ZoneInfo("America/Los_Angeles")
    spring = local_day_window(datetime(2026, 3, 8, 12, tzinfo=zone))
    assert spring.local_timezone == "America/Los_Angeles"
    assert spring.session_start_local == "2026-03-08T00:00:00-08:00"
    assert spring.session_end_local == "2026-03-09T00:00:00-07:00"
    assert spring.session_end_utc - spring.session_start_utc == 23 * 3600
    fall = local_day_window(datetime(2026, 11, 1, 12, tzinfo=zone))
    assert fall.session_end_utc - fall.session_start_utc == 25 * 3600
    print("PASS local day window remains half-open through DST changes")


def test_default_local_day_window_uses_system_dst_rules():
    """The no-argument runtime path must not freeze today's UTC offset."""
    import os
    import time as system_time
    import sports_discovery

    real_datetime = sports_discovery.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 3, 8, 12)
            return value if tz is None else value.replace(tzinfo=tz)

    old_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/Los_Angeles"
        system_time.tzset()
        sports_discovery.datetime = FrozenDateTime
        window = sports_discovery.local_day_window()
    finally:
        sports_discovery.datetime = real_datetime
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        system_time.tzset()

    assert window.session_start_local == "2026-03-08T00:00:00-08:00"
    assert window.session_end_local == "2026-03-09T00:00:00-07:00"
    assert window.session_end_utc - window.session_start_utc == 23 * 3600
    print("PASS default local-day path applies system DST rules per midnight")


def test_contract_ranking_uses_all_five_tie_breakers():
    """Selection must be deterministic after every executable-book tie."""
    from sports_discovery import (ContractProvenance, SelectedContract,
                                  rank_contracts)

    def contract(ticker, *, bid=10, ask=20, bid_size=10, ask_size=10,
                 start=10):
        return SelectedContract(
            ticker=ticker, title=ticker, game_title="game",
            bid=Decimal(bid), ask=Decimal(ask),
            bid_size=Decimal(bid_size), ask_size=Decimal(ask_size),
            provenance=ContractProvenance(
                sport="Tennis", league=None, series_ticker="KXATP",
                milestone_id="m-" + ticker, event_ticker="e-" + ticker,
                scheduled_start_ts=start))

    rank = lambda rows: tuple(item.ticker for item in rank_contracts(
        rows, Decimal(10)))
    assert rank((contract("cap-low", bid_size=9, ask_size=50),
                 contract("cap-high", bid_size=10, ask_size=10,
                          ask=99))) == ("cap-high", "cap-low")
    assert rank((contract("wide", ask=21), contract("tight", ask=20))) == (
        "tight", "wide")
    assert rank((contract("shallow", bid_size=10, ask_size=10),
                 contract("deep", bid_size=20, ask_size=20))) == (
        "deep", "shallow")
    assert rank((contract("late", start=11), contract("early", start=10))) == (
        "early", "late")
    assert rank((contract("B"), contract("A"))) == ("A", "B")
    print("PASS contract ranking uses all five deterministic tie-breakers")


def test_series_resolution_uses_unique_longest_official_prefix():
    """Only the official delimiter prefix, never event text, resolves a Series."""
    from sports_discovery import (build_series_index, resolve_series)

    stats = {}
    index = build_series_index((
        {"series_ticker": "KXGAME", "category": "Sports",
         "tags": ("Tennis",)},
        {"series_ticker": "KXGAME-PLAY", "category": "Sports",
         "tags": ("Tennis",)},
        {"series_ticker": "KXOTHER", "category": "Weather", "tags": ()},
    ), ("Tennis",), stats)
    assert resolve_series("KXGAME-PLAY-26JUL", index.official_series_tickers) \
        == "KXGAME-PLAY"
    assert resolve_series("KXGAMEPLAY-26JUL", index.official_series_tickers) \
        is None
    assert stats["skip_series_off_category"] == 1
    assert resolve_series("OTHER-1", ("KXGAME", "KXGAME")) is None
    try:
        resolve_series("KXGAME-26JUL", ("KXGAME", "KXGAME"))
        assert False
    except ValueError:
        pass
    print("PASS Series resolution uses only unique longest official prefixes")


def test_domain_values_reject_invalid_identity_numeric_and_duplicates():
    """Malformed immutable provenance cannot enter a discovery result."""
    from sports_discovery import (ContractProvenance, DiscoveryResult,
                                  SelectedContract)

    try:
        ContractProvenance("", None, "KX", "m", "e", 1)
        assert False
    except ValueError:
        pass
    provenance = ContractProvenance("Tennis", None, "KX", "m", "e", 1)
    try:
        SelectedContract("KX-1", "yes", "game", Decimal("NaN"),
                         Decimal(20), Decimal(1), Decimal(1), provenance)
        assert False
    except ValueError:
        pass
    contract = SelectedContract("KX-1", "yes", "game", Decimal(10),
                                Decimal(20), Decimal(1), Decimal(1), provenance)
    try:
        DiscoveryResult((contract, contract), ("Tennis",), "UTC",
                        "2026-01-01T00:00:00+00:00",
                        "2026-01-02T00:00:00+00:00", 0, 86400, {})
        assert False
    except ValueError:
        pass
    print("PASS invalid domain identities, values, and duplicate tickers fail")


def _dynamic_discovery_client(*, sports=("Tennis",), competitions=None,
                              series=(), milestones=None, events=None):
    """Task 2-normalized public discovery double; no raw HTTP payloads."""
    competitions = competitions or {
        sport: {sport + " League": frozenset(("Games",))}
        for sport in sports
    }
    filters = {
        "sport_ordering": ("All sports",) + tuple(sports),
        "sports": {"All sports": {"scopes": frozenset(("Games",)),
                                  "competitions": {}}},
    }
    for sport in sports:
        filters["sports"][sport] = {
            "scopes": frozenset(("Games",)),
            "competitions": competitions[sport],
        }

    class Client:
        def __init__(self):
            self.calls = []

        def get_sports_filters(self):
            self.calls.append(("filters",))
            return filters

        def get_sports_series(self):
            self.calls.append(("series",))
            return tuple(series)

        def get_sports_milestones(self, *, competition, minimum_start_date):
            self.calls.append(("milestones", competition, minimum_start_date))
            return tuple((milestones or {}).get(competition, ())), {
                "pages": 1, "rows": len((milestones or {}).get(competition, ())),
                "raw_rows": len((milestones or {}).get(competition, ())),
                "market_skips": {},
            }

        def get_open_events(self, *, series_ticker):
            self.calls.append(("events", series_ticker))
            rows = tuple((events or {}).get(series_ticker, ()))
            market_skips = {}
            for row in rows:
                for market_type, count in row["market_skips"].items():
                    market_skips[market_type] = market_skips.get(market_type, 0) + count
            return rows, {"pages": 1, "rows": len(rows), "raw_rows": len(rows),
                          "market_skips": market_skips}

    return Client()


def _normalized_game(*, milestone_id, event_ticker, start=1785088800.0,
                     league=None, main=True, primary=None, related=None,
                     title="Game"):
    return {
        "milestone_id": milestone_id, "category": "Sports", "type": "game",
        "start_ts": start, "title": title, "league": league,
        "main_game_event_ticker": event_ticker if main else None,
        "primary_event_tickers": tuple(primary if primary is not None
                                         else (event_ticker,)),
        "related_event_tickers": tuple(
            related if related is not None else (event_ticker,)),
    }


def _normalized_event(*, event_ticker, series_ticker, markets=(), title="Game",
                      market_skips=None):
    return {"event_ticker": event_ticker, "series_ticker": series_ticker,
            "category": "Sports", "title": title, "markets": tuple(markets),
            "market_skips": dict(market_skips or {})}


def _normalized_market(*, ticker, event_ticker, bid=Decimal(50), ask=Decimal(52),
                       bid_size=Decimal(10), ask_size=Decimal(10), status="active",
                       title="Yes", close_ts=1893456000.0,
                       can_close_early=False):
    return {"ticker": ticker, "event_ticker": event_ticker, "title": title,
            "yes_sub_title": "Yes", "no_sub_title": "No",
            "market_type": "binary", "status": status,
            "notional_value": Decimal(1), "close_ts": close_ts,
            "can_close_early": can_close_early,
            "yes_bid": bid, "yes_ask": ask, "yes_bid_size": bid_size,
            "yes_ask_size": ask_size}


def _discover_cfg(*, sports=(), tickers=(), max_markets=10, max_spread=3):
    return Config(sports=list(sports), tickers=list(tickers),
                  max_monitored_markets=max_markets, max_spread=max_spread,
                  state_root=tempfile.mkdtemp())


def test_api_only_new_sport_works_without_source_change():
    """Removing Basketball's API metadata must make dynamic discovery fail."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXBASKET-26JUL26-GAME"
    client = _dynamic_discovery_client(
        sports=("Basketball",),
        series=({"series_ticker": "KXBASKET", "category": "Sports",
                 "tags": ("Basketball",)},),
        milestones={"Basketball League": (_normalized_game(
            milestone_id="basketball-1", event_ticker=event),)},
        events={"KXBASKET": (_normalized_event(
            event_ticker=event, series_ticker="KXBASKET", markets=(
                _normalized_market(ticker=event + "-M", event_ticker=event),)),)},)
    result = discover_game_contracts(
        _discover_cfg(sports=("basketball",)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert result.selected_sports == ("Basketball",)
    assert result.tickers == (event + "-M",)
    print("PASS API-only Sports metadata supports a newly selected Sport")


def test_wimbledon_soccer_is_not_classified_as_tennis():
    """Changing the Series tag, not the Wimbledon title, changes inclusion."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXCLUBF-26JUL26-WIMB"
    series = ({"series_ticker": "KXCLUBF", "category": "Sports",
               "tags": ("Soccer",)},)
    milestone = _normalized_game(milestone_id="soccer-wimbledon",
                                 event_ticker=event,
                                 title="AFC Wimbledon vs Team B")
    # The Tennis query sees the same valid row first but its Series tag says
    # Soccer. Identity dedupe must not suppress its later Soccer query.
    games = {"Tennis League": (milestone,), "Soccer League": (milestone,)}
    events = {"KXCLUBF": (_normalized_event(
        event_ticker=event, series_ticker="KXCLUBF", title="AFC Wimbledon vs Team B",
        markets=(_normalized_market(ticker=event + "-M", event_ticker=event),)),)}
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    tennis = discover_game_contracts(_discover_cfg(sports=("Tennis",)),
        _dynamic_discovery_client(sports=("Tennis", "Soccer"), series=series,
            milestones=games, events=events), now=now)
    soccer = discover_game_contracts(_discover_cfg(sports=("Soccer",)),
        _dynamic_discovery_client(sports=("Tennis", "Soccer"), series=series,
            milestones=games, events=events), now=now)
    combined = discover_game_contracts(_discover_cfg(sports=("Tennis", "Soccer")),
        _dynamic_discovery_client(sports=("Tennis", "Soccer"), series=series,
            milestones=games, events=events), now=now)
    assert tennis.tickers == ()
    assert tennis.stats["skip_competition_sport_mismatch"] == 1
    assert "skip_event_unmapped_sport" not in tennis.stats
    assert soccer.tickers == (event + "-M",)
    assert combined.tickers == (event + "-M",)
    print("PASS Wimbledon text does not classify Soccer as Tennis")


def test_series_mapping_uses_every_canonical_api_sport():
    """An unselected Sport tag must still make a Series assignment ambiguous."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXMULTI-26JUL26-GAME"
    client = _dynamic_discovery_client(
        sports=("Tennis", "Soccer"),
        series=({"series_ticker": "KXMULTI", "category": "Sports",
                 "tags": ("Tennis", "Soccer")},),
        milestones={"Tennis League": (_normalized_game(
            milestone_id="multi-tag", event_ticker=event),)},
        events={"KXMULTI": (_normalized_event(
            event_ticker=event, series_ticker="KXMULTI",
            markets=(_normalized_market(
                ticker=event + "-M", event_ticker=event),)),)})
    result = discover_game_contracts(
        _discover_cfg(sports=("Tennis",)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert result.tickers == ()
    assert result.stats["skip_series_ambiguous_sport"] == 1
    assert result.stats["skip_event_unmapped_sport"] == 1
    print("PASS Series mapping uses the full canonical API Sport set")


def test_main_game_event_is_preferred_over_props():
    """Replacing main-game selection with primary-list selection is a bug."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    game, prop = "KXATP-26JUL26-GAME", "KXATP-26JUL26-TOTAL"
    milestone = _normalized_game(milestone_id="main-wins", event_ticker=game,
        primary=(prop, game))
    client = _dynamic_discovery_client(series=({"series_ticker": "KXATP",
        "category": "Sports", "tags": ("Tennis",)},),
        milestones={"Tennis League": (milestone,)}, events={"KXATP": (
            _normalized_event(event_ticker=game, series_ticker="KXATP", markets=(
                _normalized_market(ticker=game + "-M", event_ticker=game),)),
            _normalized_event(event_ticker=prop, series_ticker="KXATP", markets=(
                _normalized_market(ticker=prop + "-M", event_ticker=prop),)),)})
    result = discover_game_contracts(_discover_cfg(sports=("Tennis",)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert result.tickers == (game + "-M",)
    print("PASS main Games event wins over listed prop events")


def test_sole_primary_game_fallback_and_ambiguous_skip():
    """A sole primary is usable; a multi-primary milestone must be skipped."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    good, other = "KXATP-26JUL26-GOOD", "KXATP-26JUL26-OTHER"
    client = _dynamic_discovery_client(series=({"series_ticker": "KXATP",
        "category": "Sports", "tags": ("Tennis",)},),
        milestones={"Tennis League": (
            _normalized_game(milestone_id="fallback", event_ticker=good,
                             main=False, primary=(good,)),
            _normalized_game(milestone_id="ambiguous", event_ticker=other,
                             main=False, primary=(good, other)),)},
        events={"KXATP": (_normalized_event(event_ticker=good,
            series_ticker="KXATP", markets=(
                _normalized_market(ticker=good + "-M", event_ticker=good),)),)})
    result = discover_game_contracts(_discover_cfg(sports=("Tennis",)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert result.tickers == (good + "-M",)
    assert result.stats["skip_games_ambiguous"] == 1
    print("PASS sole primary Games fallback skips ambiguous primary lists")


def test_empty_primary_list_uses_main_or_skips_as_ambiguous():
    """Zero primary Events are valid metadata, not a schema failure."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    main_event = "KXATP-26JUL26-MAIN"
    ambiguous_event = "KXATP-26JUL26-AMBIGUOUS"
    client = _dynamic_discovery_client(
        series=({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Tennis",)},),
        milestones={"Tennis League": (
            _normalized_game(
                milestone_id="main-empty-primary",
                event_ticker=main_event, primary=(), related=()),
            _normalized_game(
                milestone_id="no-main-empty-primary",
                event_ticker=ambiguous_event, main=False, primary=(),
                related=()),
        )},
        events={"KXATP": (_normalized_event(
            event_ticker=main_event, series_ticker="KXATP",
            markets=(_normalized_market(
                ticker=main_event + "-M", event_ticker=main_event),)),)})

    result = discover_game_contracts(
        _discover_cfg(sports=("Tennis",)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))

    assert result.tickers == (main_event + "-M",)
    assert result.stats["skip_games_ambiguous"] == 1
    print("PASS empty primary list uses main Event or skips as ambiguous")


def test_duplicate_metadata_must_be_identical():
    """Conflicting duplicates must abort instead of silently selecting either."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    first = _normalized_game(milestone_id="same", event_ticker=event,
                             title="First game")
    second = dict(first, title="Conflicting game")
    client = _dynamic_discovery_client(
        competitions={"Tennis": {"A": frozenset(("Games",)),
                                  "B": frozenset(("Games",))}},
        series=({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Tennis",)},),
        milestones={"A": (first,), "B": (second,)})
    try:
        discover_game_contracts(_discover_cfg(sports=("Tennis",)), client,
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
        assert False
    except ValueError as error:
        assert "duplicate milestone" in str(error)
    print("PASS conflicting duplicate milestone metadata fails discovery")


def test_identical_cross_competition_milestone_without_league_dedupes():
    """Competition query names must not become invented league provenance."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    milestone = _normalized_game(
        milestone_id="shared", event_ticker=event, league=None)
    client = _dynamic_discovery_client(
        competitions={"Tennis": {
            "Competition A": frozenset(("Games",)),
            "Competition B": frozenset(("Games",)),
        }},
        series=({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Tennis",)},),
        milestones={
            "Competition A": (milestone,),
            "Competition B": (milestone,),
        },
        events={"KXATP": (_normalized_event(
            event_ticker=event, series_ticker="KXATP",
            markets=(_normalized_market(
                ticker=event + "-M", event_ticker=event),)),)})

    result = discover_game_contracts(
        _discover_cfg(sports=("Tennis",)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))

    assert result.tickers == (event + "-M",)
    assert result.contracts[0].provenance.league is None
    print("PASS identical cross-competition milestone dedupes without league")


def test_incomplete_inventory_prevents_ranking():
    """An event-inventory error must propagate before candidates are ranked."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    client = _dynamic_discovery_client(series=({"series_ticker": "KXATP",
        "category": "Sports", "tags": ("Tennis",)},), milestones={
            "Tennis League": (_normalized_game(milestone_id="one",
                event_ticker=event),)})
    def incomplete(*, series_ticker):
        raise ValueError("events inventory cursor repeated")
    client.get_open_events = incomplete
    try:
        discover_game_contracts(_discover_cfg(sports=("Tennis",)), client,
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
        assert False
    except ValueError as error:
        assert "cursor repeated" in str(error)
    print("PASS incomplete events inventory prevents ranking")


def test_best_ten_are_global_across_selected_sports():
    """Per-Sport caps would incorrectly retain the shallow Tennis contract."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    tennis_event = "KXTEN-26JUL26-GAME"
    basketball_events = tuple("KXBASKET-26JUL26-%02d" % index
                              for index in range(10))
    series = ({"series_ticker": "KXTEN", "category": "Sports", "tags": ("Tennis",)},
              {"series_ticker": "KXBASKET", "category": "Sports", "tags": ("Basketball",)})
    milestones = {"Tennis League": (_normalized_game(milestone_id="tennis",
                  event_ticker=tennis_event),), "Basketball League": tuple(
        _normalized_game(milestone_id="basket-%02d" % index, event_ticker=ticker)
        for index, ticker in enumerate(basketball_events))}
    events = {"KXTEN": (_normalized_event(event_ticker=tennis_event,
              series_ticker="KXTEN", markets=(_normalized_market(
                  ticker=tennis_event + "-M", event_ticker=tennis_event,
                  bid_size=Decimal(1), ask_size=Decimal(1)),)),),
              "KXBASKET": tuple(_normalized_event(event_ticker=ticker,
                  series_ticker="KXBASKET", markets=(_normalized_market(
                      ticker=ticker + "-M", event_ticker=ticker),))
                  for ticker in basketball_events)}
    result = discover_game_contracts(_discover_cfg(sports=("Tennis", "Basketball")),
        _dynamic_discovery_client(sports=("Tennis", "Basketball"), series=series,
            milestones=milestones, events=events),
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert len(result.contracts) == 10
    assert tennis_event + "-M" not in result.tickers
    assert result.stats["candidates"] == 11 and result.stats["selected"] == 10
    print("PASS best ten selection is global across selected Sports")


def test_dynamic_contract_cap_allows_siblings_from_one_game():
    """The cap counts contracts, so one Games Event may occupy two slots."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    sibling_event = "KXGAME-26JUL26-SIBLINGS"
    other_event = "KXGAME-26JUL26-OTHER"
    client = _dynamic_discovery_client(
        series=({"series_ticker": "KXGAME", "category": "Sports",
                 "tags": ("Tennis",)},),
        milestones={"Tennis League": (
            _normalized_game(
                milestone_id="siblings", event_ticker=sibling_event),
            _normalized_game(
                milestone_id="other", event_ticker=other_event),
        )},
        events={"KXGAME": (
            _normalized_event(
                event_ticker=sibling_event, series_ticker="KXGAME",
                markets=(
                    _normalized_market(
                        ticker=sibling_event + "-A",
                        event_ticker=sibling_event,
                        bid=Decimal(50), ask=Decimal(51),
                        bid_size=Decimal(20), ask_size=Decimal(20)),
                    _normalized_market(
                        ticker=sibling_event + "-B",
                        event_ticker=sibling_event,
                        bid=Decimal(50), ask=Decimal(51),
                        bid_size=Decimal(15), ask_size=Decimal(15)),
                )),
            _normalized_event(
                event_ticker=other_event, series_ticker="KXGAME",
                markets=(_normalized_market(
                    ticker=other_event + "-A", event_ticker=other_event,
                    bid=Decimal(50), ask=Decimal(51),
                    bid_size=Decimal(1), ask_size=Decimal(1)),)),
        )})

    result = discover_game_contracts(
        _discover_cfg(sports=("Tennis",), max_markets=2), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))

    assert result.tickers == (
        sibling_event + "-A", sibling_event + "-B")
    assert result.stats["candidates"] == 3
    assert result.stats["selected"] == 2
    print("PASS dynamic cap allows sibling contracts from one Games Event")


def test_dynamic_discovery_filters_books_and_reports_stable_stats():
    """Each book eligibility branch and summary counter is externally visible."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    markets = (
        _normalized_market(ticker="ACTIVE", event_ticker=event, title=""),
        _normalized_market(ticker="INACTIVE", event_ticker=event, status="closed"),
        _normalized_market(ticker="NOQUOTE", event_ticker=event, bid=None),
        _normalized_market(ticker="NODEPTH", event_ticker=event,
                           bid_size=Decimal(0)),
        _normalized_market(ticker="MISSINGDEPTH", event_ticker=event,
                           ask_size=None),
        _normalized_market(ticker="WIDE", event_ticker=event, ask=Decimal(60)),
    )
    off_day = _normalized_game(milestone_id="tomorrow", event_ticker=event,
                               start=1785196800.0)
    client = _dynamic_discovery_client(series=(
        {"series_ticker": "KXATP", "category": "Sports", "tags": ("Tennis",)},
        {"series_ticker": "KXWEATHER", "category": "Weather", "tags": ()},),
        milestones={"Tennis League": (
            _normalized_game(milestone_id="game", event_ticker=event), off_day,
            dict(_normalized_game(milestone_id="off-category", event_ticker=event),
                 category="Weather"),)},
        events={"KXATP": (_normalized_event(event_ticker=event,
            series_ticker="KXATP", markets=markets,
            market_skips={"scalar": 2}),)})
    try:
        result = discover_game_contracts(_discover_cfg(sports=("Tennis",)), client,
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    except ValueError as error:
        assert False, f"missing parsed depth must be skipped, got {error}"
    assert result.tickers == ("ACTIVE",)
    assert result.contracts[0].title == "Yes"
    assert result.stats == {
        "candidates": 1, "event_pages": 1, "event_rows": 1,
        "milestone_pages": 1, "milestone_rows": 3, "selected": 1,
        "series_rows": 2, "skip_market_inactive": 1,
        "skip_market_missing_quote": 1, "skip_market_no_depth": 2,
        "skip_market_wide_spread": 1, "skip_milestone_off_category": 1,
        "skip_milestone_outside_day": 1, "skip_series_off_category": 1,
        "skip_unsupported_market_scalar": 2,
    }
    malformed = _dynamic_discovery_client(
        series=({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Tennis",)},),
        milestones={"Tennis League": (_normalized_game(
            milestone_id="bad-depth", event_ticker=event),)},
        events={"KXATP": (_normalized_event(
            event_ticker=event, series_ticker="KXATP",
            markets=(_normalized_market(
                ticker="BADDEPTH", event_ticker=event,
                bid_size=None, ask_size="10"),)),)})
    try:
        discover_game_contracts(_discover_cfg(sports=("Tennis",)), malformed,
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
        assert False
    except ValueError as error:
        assert "invalid parsed book" in str(error)
    print("PASS dynamic discovery filters books and reports stable stats")


def _explicit_discovery_client(*, sports=("Tennis",), filters=None, series=(),
                               markets=None, events=None, milestones=None,
                               milestone_metadata=None):
    """Task 2-normalized explicit-discovery double; no raw HTTP payloads."""
    if filters is None:
        filters = {
            "sport_ordering": ("All sports",) + tuple(sports),
            "sports": {
                "All sports": {
                    "scopes": frozenset(("Games",)), "competitions": {}},
            },
        }
        for sport in sports:
            filters["sports"][sport] = {
                "scopes": frozenset(("Games",)),
                "competitions": {
                    sport + " League": frozenset(("Games",))},
            }
    markets = dict(markets or {})
    events = dict(events or {})
    milestones = dict(milestones or {})
    milestone_metadata = dict(milestone_metadata or {})

    class Client:
        def __init__(self):
            self.calls = []

        def get_sports_filters(self):
            self.calls.append(("filters",))
            return filters

        def get_sports_series(self):
            self.calls.append(("series",))
            return tuple(series)

        def get_market(self, ticker):
            self.calls.append(("market", ticker))
            return markets[ticker]

        def get_event(self, event_ticker, *, with_nested_markets=True):
            self.calls.append(("event", event_ticker, with_nested_markets))
            return events[event_ticker]

        def get_sports_milestones(self, *, related_event_ticker,
                                   minimum_start_date):
            self.calls.append(
                ("milestones", related_event_ticker, minimum_start_date))
            rows = tuple(milestones.get(related_event_ticker, ()))
            metadata = milestone_metadata.get(related_event_ticker, {
                "pages": 1, "rows": len(rows), "raw_rows": len(rows),
                "market_skips": {},
            })
            return rows, metadata

    return Client()


def test_discovery_requires_exactly_one_source():
    """The domain entry point must reject both and neither selection source."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    for cfg in (_discover_cfg(),
                _discover_cfg(sports=("Tennis",), tickers=("T",))):
        try:
            discover_game_contracts(
                cfg, object(),
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, cfg
        except ValueError as error:
            assert "exactly one" in str(error)
    print("PASS discovery requires exactly one initial source")


def test_explicit_tickers_must_be_today_games_and_within_cap():
    """A configured ticker needs one current-day Games milestone proof."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    ticker = event + "-YES"
    market = _normalized_market(ticker=ticker, event_ticker=event)
    series = ({"series_ticker": "KXATP", "category": "Sports",
               "tags": ("Tennis",)},)
    event_row = _normalized_event(
        event_ticker=event, series_ticker="KXATP", markets=(market,))
    today = _normalized_game(
        milestone_id="today", event_ticker=event, league="ATP")
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    client = _explicit_discovery_client(
        series=series, markets={ticker: market}, events={event: event_row},
        milestones={event: (today,)})
    result = discover_game_contracts(
        _discover_cfg(tickers=(ticker,), max_markets=1), client, now=now)
    assert result.tickers == (ticker,)
    assert result.contracts[0].provenance.milestone_id == "today"

    tomorrow = dict(today, start_ts=1785196800.0)
    client = _explicit_discovery_client(
        series=series, markets={ticker: market}, events={event: event_row},
        milestones={event: (tomorrow,)})
    try:
        discover_game_contracts(
            _discover_cfg(tickers=(ticker,), max_markets=1), client, now=now)
        assert False
    except ValueError as error:
        assert ticker in str(error) or event in str(error)
    print("PASS explicit tickers require a current-day Games proof within cap")


def test_explicit_tickers_reject_duplicates_and_over_cap_before_network():
    """Invalid requested cardinality must not be truncated or touch metadata."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sports_discovery import discover_game_contracts

    cases = (
        (SimpleNamespace(sports=[], tickers=["T", "T"],
                         max_monitored_markets=10, max_spread=3),
         "duplicate"),
        (SimpleNamespace(sports=[], tickers=["T1", "T2"],
                         max_monitored_markets=1, max_spread=3),
         "monitoring cap"),
    )
    for cfg, expected in cases:
        client = _explicit_discovery_client()
        try:
            discover_game_contracts(
                cfg, client,
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, cfg.tickers
        except ValueError as error:
            assert expected in str(error).lower()
        assert client.calls == []
    print("PASS duplicate/over-cap explicit tickers fail before network")


def test_explicit_tickers_reject_unordered_or_lazy_inputs_before_network():
    """Only an ordered materialized ticker sequence can define result order."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sports_discovery import discover_game_contracts

    invalid = (
        {"T1", "T2"},
        {"T1": True, "T2": True},
        (ticker for ticker in ("T1", "T2")),
        "T1",
    )
    for tickers in invalid:
        cfg = SimpleNamespace(
            sports=[], tickers=tickers, max_monitored_markets=10,
            max_spread=3)
        client = _explicit_discovery_client()
        try:
            discover_game_contracts(
                cfg, client,
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, type(tickers)
        except ValueError as error:
            assert "ordered" in str(error)
        assert client.calls == []
    print("PASS unordered/lazy explicit ticker inputs fail before network")


def test_explicit_tickers_preserve_order_and_derive_api_ordered_sports():
    """Contract order follows config; canonical Sports follow API ordering."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    tennis_event = "KXTEN-26JUL26-GAME"
    soccer_event = "KXSOCCER-26JUL26-WIMB"
    tennis_ticker = tennis_event + "-YES"
    soccer_ticker = soccer_event + "-YES"
    direct_soccer = _normalized_market(
        ticker=soccer_ticker, event_ticker=soccer_event,
        bid=Decimal(40), ask=Decimal(43), title="Direct display")
    nested_soccer = _normalized_market(
        ticker=soccer_ticker, event_ticker=soccer_event,
        bid=Decimal(50), ask=Decimal(52), title="Nested display",
        close_ts=1893456100.0, can_close_early=True)
    tennis_market = _normalized_market(
        ticker=tennis_ticker, event_ticker=tennis_event)
    client = _explicit_discovery_client(
        sports=("Tennis", "Soccer"),
        series=(
            {"series_ticker": "KXTEN", "category": "Sports",
             "tags": ("Tennis",)},
            {"series_ticker": "KXSOCCER", "category": "Sports",
             "tags": ("Soccer",)},
        ),
        markets={soccer_ticker: direct_soccer, tennis_ticker: tennis_market},
        events={
            soccer_event: _normalized_event(
                event_ticker=soccer_event, series_ticker="KXSOCCER",
                title="AFC Wimbledon vs Team B", markets=(nested_soccer,)),
            tennis_event: _normalized_event(
                event_ticker=tennis_event, series_ticker="KXTEN",
                markets=(tennis_market,)),
        },
        milestones={
            soccer_event: (_normalized_game(
                milestone_id="soccer", event_ticker=soccer_event),),
            tennis_event: (_normalized_game(
                milestone_id="tennis", event_ticker=tennis_event),),
        })
    result = discover_game_contracts(
        _discover_cfg(tickers=(soccer_ticker, tennis_ticker)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert result.tickers == (soccer_ticker, tennis_ticker)
    assert result.selected_sports == ("Tennis", "Soccer")
    assert result.contracts[0].provenance.sport == "Soccer"
    assert result.contracts[0].title == "Nested display"
    assert result.contracts[0].bid == Decimal(50)
    print("PASS explicit order is preserved while Sports use API ordering")


def test_explicit_ticker_requires_matching_market_event_series_and_nested_identity():
    """Every direct identity must converge with exactly one nested Market."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    ticker = event + "-YES"
    market = _normalized_market(ticker=ticker, event_ticker=event)
    series = ({"series_ticker": "KXATP", "category": "Sports",
               "tags": ("Tennis",)},)
    proof = _normalized_game(milestone_id="proof", event_ticker=event)

    cases = (
        (_normalized_market(ticker="WRONG", event_ticker=event),
         _normalized_event(event_ticker=event, series_ticker="KXATP",
                           markets=(market,))),
        (market, _normalized_event(
            event_ticker="KXATP-26JUL26-OTHER", series_ticker="KXATP",
            markets=(market,))),
        (market, _normalized_event(
            event_ticker=event, series_ticker="KXOTHER", markets=(market,))),
        (market, _normalized_event(
            event_ticker=event, series_ticker="KXATP", markets=())),
        (market, _normalized_event(
            event_ticker=event, series_ticker="KXATP",
            markets=(market, dict(market)))),
    )
    for direct, event_row in cases:
        client = _explicit_discovery_client(
            series=series, markets={ticker: direct}, events={event: event_row},
            milestones={event: (proof,)})
        try:
            discover_game_contracts(
                _discover_cfg(tickers=(ticker,)), client,
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, event_row
        except ValueError as error:
            assert "Task 3C" not in str(error)
        assert any(call[0] == "market" for call in client.calls)
    print("PASS explicit Market/Event/Series/nested identities must converge")


def test_explicit_ticker_requires_games_capable_canonical_sport():
    """Series tags and both Games scopes must prove a canonical Sport."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    ticker = event + "-YES"
    market = _normalized_market(ticker=ticker, event_ticker=event)
    event_row = _normalized_event(
        event_ticker=event, series_ticker="KXATP", markets=(market,))
    proof = _normalized_game(milestone_id="proof", event_ticker=event)
    bad_filters = (
        {
            "sport_ordering": ("All sports", "Tennis"),
            "sports": {
                "All sports": {"scopes": frozenset(("Games",)),
                               "competitions": {}},
                "Tennis": {"scopes": frozenset(("Futures",)),
                           "competitions": {
                               "ATP": frozenset(("Games",))}},
            },
        },
        {
            "sport_ordering": ("All sports", "Tennis"),
            "sports": {
                "All sports": {"scopes": frozenset(("Games",)),
                               "competitions": {}},
                "Tennis": {"scopes": frozenset(("Games",)),
                           "competitions": {
                               "ATP": frozenset(("Futures",))}},
            },
        },
    )
    cases = (
        (None, ({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Unknown",)},)),
        (bad_filters[0], ({"series_ticker": "KXATP", "category": "Sports",
                           "tags": ("Tennis",)},)),
        (bad_filters[1], ({"series_ticker": "KXATP", "category": "Sports",
                           "tags": ("Tennis",)},)),
    )
    for filters, series in cases:
        client = _explicit_discovery_client(
            filters=filters, series=series, markets={ticker: market},
            events={event: event_row}, milestones={event: (proof,)})
        try:
            discover_game_contracts(
                _discover_cfg(tickers=(ticker,)), client,
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, filters
        except ValueError as error:
            assert "Sport" in str(error) or "Series" in str(error)
    print("PASS explicit Series requires one Games-capable canonical Sport")


def test_explicit_ticker_requires_unique_main_or_sole_primary_milestone():
    """Main and sole-primary are valid; zero or multiple proofs are not."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    ticker = event + "-YES"
    market = _normalized_market(ticker=ticker, event_ticker=event)
    series = ({"series_ticker": "KXATP", "category": "Sports",
               "tags": ("Tennis",)},)
    event_row = _normalized_event(
        event_ticker=event, series_ticker="KXATP", markets=(market,))
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)

    for proof in (
            _normalized_game(
                milestone_id="main", event_ticker=event,
                primary=(event + "-TOTAL", event)),
            _normalized_game(
                milestone_id="sole", event_ticker=event, main=False,
                primary=(event,))):
        result = discover_game_contracts(
            _discover_cfg(tickers=(ticker,)),
            _explicit_discovery_client(
                series=series, markets={ticker: market},
                events={event: event_row}, milestones={event: (proof,)}),
            now=now)
        assert result.contracts[0].provenance.milestone_id == \
            proof["milestone_id"]

    ambiguous = _normalized_game(
        milestone_id="ambiguous", event_ticker=event, main=False,
        primary=(event, event + "-TOTAL"))
    two = (
        _normalized_game(milestone_id="one", event_ticker=event),
        _normalized_game(milestone_id="two", event_ticker=event),
    )
    for rows in ((ambiguous,), two):
        try:
            discover_game_contracts(
                _discover_cfg(tickers=(ticker,)),
                _explicit_discovery_client(
                    series=series, markets={ticker: market},
                    events={event: event_row}, milestones={event: rows}),
                now=now)
            assert False, rows
        except ValueError:
            pass
    print("PASS explicit Games proof requires unique main or sole primary")


def test_explicit_ticker_rejects_wrong_day_unrelated_and_conflicting_milestones():
    """Wrong-day, ignored-filter, and conflicting proof metadata fail closed."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    ticker = event + "-YES"
    market = _normalized_market(ticker=ticker, event_ticker=event)
    series = ({"series_ticker": "KXATP", "category": "Sports",
               "tags": ("Tennis",)},)
    event_row = _normalized_event(
        event_ticker=event, series_ticker="KXATP", markets=(market,))
    base = _normalized_game(milestone_id="proof", event_ticker=event)
    cases = (
        (dict(base, start_ts=1785196800.0),),
        (dict(base, related_event_tickers=("SOME-OTHER-EVENT",)),),
        (base, dict(base, title="conflicting proof")),
    )
    for rows in cases:
        try:
            discover_game_contracts(
                _discover_cfg(tickers=(ticker,)),
                _explicit_discovery_client(
                    series=series, markets={ticker: market},
                    events={event: event_row}, milestones={event: rows}),
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, rows
        except ValueError as error:
            assert "Task 3C" not in str(error)
    print("PASS wrong-day, unrelated, and conflicting milestones fail closed")


def test_explicit_milestone_identity_conflicts_across_event_queries():
    """One milestone ID cannot describe two different rows across Events."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    first_event = "KXATP-26JUL26-FIRST"
    second_event = "KXATP-26JUL26-SECOND"
    first_ticker = first_event + "-YES"
    second_ticker = second_event + "-YES"
    first_market = _normalized_market(
        ticker=first_ticker, event_ticker=first_event)
    second_market = _normalized_market(
        ticker=second_ticker, event_ticker=second_event)
    first_proof = _normalized_game(
        milestone_id="shared-id", event_ticker=first_event,
        title="First proof")
    conflicting_proof = _normalized_game(
        milestone_id="shared-id", event_ticker=second_event,
        title="Second proof")
    client = _explicit_discovery_client(
        series=({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Tennis",)},),
        markets={
            first_ticker: first_market, second_ticker: second_market},
        events={
            first_event: _normalized_event(
                event_ticker=first_event, series_ticker="KXATP",
                markets=(first_market,)),
            second_event: _normalized_event(
                event_ticker=second_event, series_ticker="KXATP",
                markets=(second_market,)),
        },
        milestones={
            first_event: (first_proof,),
            second_event: (conflicting_proof,),
        })
    try:
        discover_game_contracts(
            _discover_cfg(tickers=(first_ticker, second_ticker)), client,
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
        assert False
    except ValueError as error:
        assert "duplicate milestone" in str(error)
        assert "conflicting metadata" in str(error)
    assert [call[0] for call in client.calls].count("milestones") == 2
    print("PASS milestone identity conflicts across Event queries fail")


def test_explicit_ticker_rejects_each_ineligible_book_condition():
    """Requested inactive, unquoted, shallow, or wide Markets cannot disappear."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    ticker = event + "-YES"
    direct = _normalized_market(ticker=ticker, event_ticker=event)
    series = ({"series_ticker": "KXATP", "category": "Sports",
               "tags": ("Tennis",)},)
    proof = _normalized_game(milestone_id="proof", event_ticker=event)
    books = (
        _normalized_market(ticker=ticker, event_ticker=event, status="closed"),
        _normalized_market(ticker=ticker, event_ticker=event, bid=None),
        _normalized_market(ticker=ticker, event_ticker=event,
                           bid_size=Decimal(0)),
        _normalized_market(ticker=ticker, event_ticker=event, ask_size=None),
        _normalized_market(ticker=ticker, event_ticker=event,
                           bid=Decimal(40), ask=Decimal(50)),
    )
    for book in books:
        client = _explicit_discovery_client(
            series=series, markets={ticker: direct}, events={
                event: _normalized_event(
                    event_ticker=event, series_ticker="KXATP",
                    markets=(book,))},
            milestones={event: (proof,)})
        try:
            discover_game_contracts(
                _discover_cfg(tickers=(ticker,), max_spread=3), client,
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
            assert False, book
        except ValueError as error:
            assert ticker in str(error)
    print("PASS every ineligible requested book condition aborts discovery")


def test_explicit_tickers_share_event_queries_and_count_stats_once():
    """Sibling contracts share one Event/proof and one unsupported count."""
    from datetime import datetime, timezone
    from sports_discovery import discover_game_contracts

    event = "KXATP-26JUL26-GAME"
    first, second = event + "-A", event + "-B"
    markets = (
        _normalized_market(ticker=first, event_ticker=event),
        _normalized_market(ticker=second, event_ticker=event,
                           bid=Decimal(49), ask=Decimal(51)),
    )
    proof = _normalized_game(
        milestone_id="shared", event_ticker=event, league="ATP")
    client = _explicit_discovery_client(
        series=({"series_ticker": "KXATP", "category": "Sports",
                 "tags": ("Tennis",)},),
        markets={first: markets[0], second: markets[1]},
        events={event: _normalized_event(
            event_ticker=event, series_ticker="KXATP", markets=markets,
            market_skips={"scalar": 2})},
        milestones={event: (proof, dict(proof))},
        milestone_metadata={
            event: {"pages": 2, "rows": 2, "raw_rows": 2,
                    "market_skips": {}}})
    result = discover_game_contracts(
        _discover_cfg(tickers=(second, first)), client,
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc))
    assert result.tickers == (second, first)
    assert result.contracts[0].provenance == result.contracts[1].provenance
    assert [call[:2] for call in client.calls].count(("event", event)) == 1
    assert [call[:2] for call in client.calls].count(("milestones", event)) == 1
    assert result.stats == {
        "candidates": 2, "event_pages": 1, "event_rows": 1,
        "milestone_pages": 2, "milestone_rows": 2, "selected": 2,
        "series_rows": 1, "skip_unsupported_market_scalar": 2,
    }
    print("PASS sibling explicit contracts share proof calls and stats once")


def _task4_contract(ticker="T", event_ticker="E", sport="Tennis",
                    league="League", series_ticker="KXSERIES",
                    milestone_id="M", start=3600.0, title="Yes",
                    game_title="Game"):
    from sports_discovery import ContractProvenance, SelectedContract

    return SelectedContract(
        ticker=ticker, title=title, game_title=game_title,
        bid=Decimal(50), ask=Decimal(52),
        bid_size=Decimal(10), ask_size=Decimal(11),
        provenance=ContractProvenance(
            sport=sport, league=league, series_ticker=series_ticker,
            milestone_id=milestone_id, event_ticker=event_ticker,
            scheduled_start_ts=start))


def _task4_discovery(*contracts, selected_sports=("Tennis",), stats=None):
    from sports_discovery import DiscoveryResult

    return DiscoveryResult(
        contracts=tuple(contracts), selected_sports=tuple(selected_sports),
        local_timezone="UTC",
        session_start_local="1970-01-01T00:00:00+00:00",
        session_end_local="1970-01-02T00:00:00+00:00",
        session_start_utc=0.0, session_end_utc=86400.0,
        stats=stats or {
            "series_rows": 1, "milestone_pages": 1, "milestone_rows": 1,
            "event_pages": 1, "event_rows": 1, "candidates": len(contracts),
            "selected": len(contracts),
        })


class _Task4RunFeed:
    def __init__(self, result, *, subscribe_error=None):
        self.result = result
        self.subscribe_error = subscribe_error
        self.discover_calls = 0
        self.subscribe_calls = []

    def discover(self, *, now=None):
        self.discover_calls += 1
        return self.result

    def discover_tickers(self):
        raise AssertionError("production startup called compatibility discovery")

    def subscribe(self, tickers):
        self.subscribe_calls.append(tuple(tickers))
        if self.subscribe_error:
            raise self.subscribe_error


def _task4_clean_run_loop(ctx, reconciler, tickers):
    ctx.log.end(clean=True, reason="operator interrupt")
    return True


def test_feed_installs_immutable_discovery_provenance():
    """Discovery identity installs atomically once, including an empty result."""
    from market_data import PriceFeed

    contract = _task4_contract()
    discovery = _task4_discovery(contract)
    feed = PriceFeed(Config(), client=None)
    feed.install_discovery(discovery)
    assert feed.provenance("T") == contract.provenance
    assert feed.group_id("T") == "E"
    for mapping in (feed.contracts_by_ticker, feed.provenance_by_ticker):
        try:
            mapping["OTHER"] = contract
            assert False
        except TypeError:
            pass
    try:
        feed.install_discovery(discovery)
        assert False
    except ValueError as error:
        assert "already installed" in str(error)

    empty_feed = PriceFeed(Config(), client=None)
    empty_feed.install_discovery(_task4_discovery())
    try:
        empty_feed.install_discovery(discovery)
        assert False
    except ValueError:
        pass

    invalid = _task4_discovery(
        contract, selected_sports=("Soccer",))
    atomic = PriceFeed(Config(), client=None)
    try:
        atomic.install_discovery(invalid)
        assert False
    except ValueError:
        pass
    atomic.install_discovery(discovery)
    assert atomic.group_id("T") == "E"
    print("PASS feed installs immutable discovery provenance exactly once")


def test_feed_rejects_unknown_subscription_ticker():
    """Subscription validates its full input before starting any stale clocks."""
    from market_data import PriceFeed

    uninstalled = PriceFeed(Config(), client=None)
    try:
        uninstalled.subscribe(())
        assert False
    except SchemaError as error:
        assert "not installed" in str(error)
    assert uninstalled.last_good == {}

    feed = PriceFeed(Config(), client=None, clock=lambda: 10.0)
    feed.install_discovery(_task4_discovery(_task4_contract()))
    for tickers in (("T", "T"), ("UNKNOWN",), ("T", 7), "T"):
        before = dict(feed.last_good)
        try:
            feed.subscribe(tickers)
            assert False, tickers
        except (ValueError, SchemaError):
            pass
        assert feed.last_good == before
    feed.subscribe(ticker for ticker in ("T",))
    assert feed.last_good == {"T": 10.0}
    print("PASS subscription rejects invalid/unknown tickers atomically")


def test_quote_event_mismatch_fails_closed():
    """A quote cannot teach or mutate immutable discovery identity."""
    from market_data import PriceFeed

    class Client:
        def __init__(self):
            self.calls = []

        def get_market(self, ticker):
            self.calls.append(ticker)
            return _normalized_market(
                ticker=ticker, event_ticker="DIFFERENT",
                close_ts=99.0, can_close_early=True)

    client = Client()
    feed = PriceFeed(Config(), client, clock=lambda: 10.0)
    feed.install_discovery(_task4_discovery(_task4_contract()))
    feed.subscribe(("T",))
    before = dict(feed.last_good)
    try:
        feed.get_quote("T")
        assert False
    except SchemaError as error:
        assert "event" in str(error).lower()
    assert feed.group_id("T") == "E"
    assert feed.last_good == before
    assert not feed.history["T"] and feed.last_book == {}
    assert feed.close_times == {} and feed.can_close_early == {}

    try:
        feed.get_quote("UNKNOWN")
        assert False
    except SchemaError:
        pass
    assert client.calls == ["T"]

    class WrongTickerClient:
        def get_market(self, ticker):
            return _normalized_market(
                ticker="OTHER", event_ticker="E")

    wrong_ticker = PriceFeed(
        Config(), WrongTickerClient(), clock=lambda: 10.0)
    wrong_ticker.install_discovery(
        _task4_discovery(_task4_contract()))
    wrong_ticker.subscribe(("T",))
    try:
        wrong_ticker.get_quote("T")
        assert False
    except SchemaError as error:
        assert "identity mismatch" in str(error)
    assert wrong_ticker.close_times == {}
    assert wrong_ticker.can_close_early == {}
    assert wrong_ticker.last_book == {}
    print("PASS quote identity mismatch fails before mutable feed state")


def test_discovery_failure_is_durable_and_returns_nonzero():
    """Pre-canonical discovery failures write only the operational halt."""
    import contextlib
    import io
    import json
    import bot as bot_module

    class Client:
        def get_sports_filters(self):
            raise SchemaError("filters drift")

        def get_sports_series(self):
            raise AssertionError("must fail at filters")

        def get_sports_milestones(self, **kwargs):
            raise AssertionError("must fail at filters")

        def get_open_events(self, **kwargs):
            raise AssertionError("must fail at filters")

    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        cfg = Config(sports=["tennis"],
                     state_root=os.path.join(workdir, "state"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert bot_module.run_session(cfg, Client()) == 1
        path = os.path.join("logs", "startup_halts_v6.jsonl")
        with open(path) as handle:
            rows = [json.loads(line) for line in handle]
        assert rows == [{
            "event": "session_halt", "reason":
                "market discovery failed: SchemaError: filters drift",
            "requested_sports": ["tennis"], "schema_version": 6,
            "tickers": [], "ts": rows[0]["ts"],
        }]
        assert rows[0]["ts"] >= 0
        assert not [name for name in os.listdir("logs")
                    if name.startswith(("ticks_", "trades_"))]
        assert "STARTUP FAILED" in output.getvalue()

        original = bot_module.write_startup_halt
        bot_module.write_startup_halt = lambda *args, **kwargs: (
            (_ for _ in ()).throw(OSError("halt disk failure")))
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                assert bot_module.run_session(cfg, Client()) == 1
            assert "filters drift" in output.getvalue()
            assert "halt disk failure" in output.getvalue()
        finally:
            bot_module.write_startup_halt = original
    finally:
        os.chdir(old_cwd)
    print("PASS pre-canonical failure is durable without masking primary error")


def test_post_discovery_session_uses_canonical_sports():
    """Research construction sees API-canonical Sports, including ticker mode."""
    import bot as bot_module

    result = _task4_discovery(
        _task4_contract(sport="Tennis"),
        selected_sports=("Basketball", "Tennis"))
    feed = _Task4RunFeed(result)
    captured = {}
    original_feed = bot_module.PriceFeed
    original_log = bot_module.ResearchLog
    original_loop = bot_module.run_loop

    def log_factory(*args, **kwargs):
        captured["sports"] = tuple(kwargs["config"].sports)
        return original_log(*args, **kwargs)

    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        bot_module.PriceFeed = lambda cfg, client: feed
        bot_module.ResearchLog = log_factory
        bot_module.run_loop = _task4_clean_run_loop
        cfg = Config(tickers=["T"], state_root=os.path.join(workdir, "state"))
        assert bot_module.run_session(cfg, object()) == 0
        assert captured["sports"] == ("Basketball", "Tennis")
        assert cfg.sports == ["Basketball", "Tennis"]
    finally:
        bot_module.PriceFeed = original_feed
        bot_module.ResearchLog = original_log
        bot_module.run_loop = original_loop
        os.chdir(old_cwd)
    print("PASS post-discovery config and ResearchLog use canonical Sports")


def test_run_session_discovers_and_reports_only_once():
    """Production startup uses one quiet discover call and one pure formatter."""
    import contextlib
    import io
    import bot as bot_module

    result = _task4_discovery(
        _task4_contract(),
        stats={
            "series_rows": 3, "milestone_pages": 2, "milestone_rows": 4,
            "event_pages": 1, "event_rows": 2, "candidates": 1,
            "selected": 1, "skip_z": 2, "skip_a": 1,
        })
    expected = "\n".join((
        "[discover] Sports=Tennis",
        "[discover] day timezone=UTC",
        "  local=[1970-01-01T00:00:00+00:00, "
        "1970-01-02T00:00:00+00:00)",
        "  utc=[1970-01-01T00:00:00Z, 1970-01-02T00:00:00Z)",
        "[discover] series_rows=3 milestone_pages=2 milestone_rows=4 "
        "event_pages=1 event_rows=2 candidates=1 selected=1",
        "  skips=skip_a=1, skip_z=2",
        "[discover] Tennis | League | Game | T | "
        "1970-01-01T01:00:00Z | bid=50 ask=52 spread=2 "
        "depth=(10,11)",
    ))
    assert bot_module.format_discovery_telemetry(result) == expected

    feed = _Task4RunFeed(result)
    formatter_calls = []
    original_feed = bot_module.PriceFeed
    original_formatter = bot_module.format_discovery_telemetry
    original_loop = bot_module.run_loop
    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        bot_module.PriceFeed = lambda cfg, client: feed
        bot_module.format_discovery_telemetry = lambda discovery: (
            formatter_calls.append(discovery) or expected)
        bot_module.run_loop = _task4_clean_run_loop
        cfg = Config(sports=["Tennis"],
                     state_root=os.path.join(workdir, "state"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert bot_module.run_session(cfg, object()) == 0
        assert feed.discover_calls == 1
        assert formatter_calls == [result]
        assert feed.subscribe_calls == [("T",)]
        assert output.getvalue().count("[discover] Sports=Tennis") == 1
    finally:
        bot_module.PriceFeed = original_feed
        bot_module.format_discovery_telemetry = original_formatter
        bot_module.run_loop = original_loop
        os.chdir(old_cwd)
    print("PASS startup discovers and formats exactly once")


def test_complete_empty_discovery_writes_clean_terminal():
    """Empty complete discovery succeeds only after one durable clean terminal."""
    import csv
    import bot as bot_module

    result = _task4_discovery(selected_sports=("Tennis",))
    feed = _Task4RunFeed(result)
    original_feed = bot_module.PriceFeed
    original_loop = bot_module.run_loop
    original_log = bot_module.ResearchLog
    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        bot_module.PriceFeed = lambda cfg, client: feed
        bot_module.run_loop = lambda *args, **kwargs: (
            (_ for _ in ()).throw(AssertionError("run loop entered")))
        cfg = Config(sports=["Tennis"],
                     state_root=os.path.join(workdir, "state"))
        assert bot_module.run_session(cfg, object()) == 0
        assert feed.subscribe_calls == []
        tick_path = os.path.join("logs", [
            name for name in os.listdir("logs")
            if name.startswith("ticks_v6_")][0])
        with open(tick_path, newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert [(row["event"], row["detail"]) for row in rows] == [(
            "session_end", "no eligible Games contracts for selected Sports")]

        class BrokenTerminalLog(original_log):
            def end(self, **kwargs):
                raise OSError("terminal disk failure")

        broken = _Task4RunFeed(result)
        bot_module.PriceFeed = lambda cfg, client: broken
        bot_module.ResearchLog = BrokenTerminalLog
        assert bot_module.run_session(cfg, object()) == 1
        assert broken.subscribe_calls == []
    finally:
        bot_module.PriceFeed = original_feed
        bot_module.run_loop = original_loop
        bot_module.ResearchLog = original_log
        os.chdir(old_cwd)
    print("PASS empty discovery requires one durable clean terminal")


def test_subscription_failure_ends_canonical_log_noncleanly():
    """Failures after ResearchLog construction use its non-clean terminal."""
    import csv
    import bot as bot_module

    result = _task4_discovery(_task4_contract())
    feed = _Task4RunFeed(result, subscribe_error=ValueError("bad subscribe"))
    original_feed = bot_module.PriceFeed
    original_loop = bot_module.run_loop
    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        bot_module.PriceFeed = lambda cfg, client: feed
        bot_module.run_loop = lambda *args, **kwargs: (
            (_ for _ in ()).throw(AssertionError("run loop entered")))
        cfg = Config(sports=["Tennis"],
                     state_root=os.path.join(workdir, "state"))
        assert bot_module.run_session(cfg, object()) == 1
        assert not os.path.exists(
            os.path.join("logs", "startup_halts_v6.jsonl"))
        tick_path = os.path.join("logs", [
            name for name in os.listdir("logs")
            if name.startswith("ticks_v6_")][0])
        with open(tick_path, newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[-1]["event"] == "session_halt"
        assert "bad subscribe" in rows[-1]["detail"]
    finally:
        bot_module.PriceFeed = original_feed
        bot_module.run_loop = original_loop
        os.chdir(old_cwd)
    print("PASS post-log subscription failure writes non-clean terminal")


def test_keyboard_interrupt_and_system_exit_are_not_swallowed_by_discovery():
    """BaseException control flow cannot become an ordinary startup failure."""
    import bot as bot_module

    original_feed = bot_module.PriceFeed
    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        cfg = Config(sports=["Tennis"],
                     state_root=os.path.join(workdir, "state"))
        for raised in (KeyboardInterrupt(), SystemExit(7)):
            class Feed:
                def __init__(self, config, client):
                    pass

                def discover(self):
                    raise raised

            bot_module.PriceFeed = Feed
            try:
                bot_module.run_session(cfg, object())
                assert False, type(raised)
            except type(raised) as error:
                if isinstance(error, SystemExit):
                    assert error.code == 7
        assert not os.path.exists("logs")
    finally:
        bot_module.PriceFeed = original_feed
        os.chdir(old_cwd)
    print("PASS discovery preserves KeyboardInterrupt and SystemExit")


def _write_research_csv(rows, header=None):
    import csv

    path = tempfile.mktemp(suffix=".csv")
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(RESEARCH_HEADER if header is None else header)
        writer.writerows(rows)
    return path


def _clean_v6_rows(*, cfg=None, selected_sports=("Tennis",),
                   quotes=None, session="V6"):
    cfg = cfg or Config(sports=list(selected_sports))
    rows = []
    for i, overrides in enumerate(quotes or ({},), start=1):
        params = {
            "cfg": cfg, "session": session, "ts": i,
            "selected_sports": selected_sports,
        }
        params.update(overrides)
        rows.append(research_row(**params))
    rows.append(research_row(
        cfg=cfg, session=session, ts=len(rows) + 1,
        selected_sports=selected_sports, ticker="",
        event="session_end", detail="operator interrupt"))
    return rows


def test_v6_config_fingerprint_uses_canonical_sports():
    from research_log import config_fingerprint

    first = Config(sports=["Basketball", "Tennis"])
    same = Config(sports=["Basketball", "Tennis"])
    reordered = Config(sports=["Tennis", "Basketball"])
    different = Config(sports=["Tennis"])
    assert config_fingerprint(first) == config_fingerprint(same)
    assert config_fingerprint(first) != config_fingerprint(reordered)
    assert config_fingerprint(first) != config_fingerprint(different)
    print("PASS v6 config fingerprint records canonical Sports order")


def test_v6_quote_and_trade_rows_share_full_provenance():
    import csv
    from research_log import ResearchLog, TICK_HEADER, TRADE_HEADER

    directory = tempfile.mkdtemp()
    cfg = Config(sports=["Basketball", "Tennis"])
    provenance = {
        "T": research_provenance(
            sport="Tennis", league=None, series_ticker="KXATP",
            milestone_id="MATCH-1", event_ticker="EVENT-1",
            scheduled_start_ts=100),
    }
    log = ResearchLog(
        directory, clock=lambda: 1.0, session_id="V6-PROVENANCE",
        config=cfg, provenance_by_ticker=provenance)
    log.tick(
        "T", Decimal(50), Decimal(49), Decimal(51),
        Decimal(10), Decimal(11), close_ts=1000,
        can_close_early=False)
    log.trade(
        "T", "BUY", Decimal(52), Decimal(2), "dip",
        fee=Decimal("0.02"), ts=2)
    log.event("T", "api_error", ts=3, detail="timeout")
    log.event("T", "quarantined", ts=4, detail="bounded failures")
    log.end(clean=True, reason="operator interrupt", ts=5)
    with open(log.tick_path, newline="") as handle:
        tick_reader = csv.DictReader(handle)
        ticks = list(tick_reader)
        assert tick_reader.fieldnames == TICK_HEADER == RESEARCH_HEADER
    with open(log.trade_path, newline="") as handle:
        trade_reader = csv.DictReader(handle)
        trades = list(trade_reader)
        assert trade_reader.fieldnames == TRADE_HEADER
    expected = {
        "sport": "Tennis", "league": "",
        "series_ticker": "KXATP", "milestone_id": "MATCH-1",
        "event_ticker": "EVENT-1", "scheduled_start_ts": "100.0",
    }
    for row in ticks[:-1] + trades:
        assert {field: row[field] for field in expected} == expected
        assert row["selected_sports"] == '["Basketball","Tennis"]'
    assert [row["event"] for row in ticks] == [
        "quote", "api_error", "quarantined", "session_end"]
    assert all(ticks[-1][field] == "" for field in (
        "ticker", "sport", "league", "series_ticker", "milestone_id",
        "event_ticker", "scheduled_start_ts", "close_ts",
        "can_close_early", "mid", "bid", "ask", "bid_qty", "ask_qty"))
    print("PASS v6 quote, trade, API-error and quarantine provenance is complete")


def test_delayed_paper_fill_logs_full_provenance():
    from collections import defaultdict, deque
    from research_log import ResearchLog

    class Feed:
        def __init__(self):
            self.history = defaultdict(lambda: deque(maxlen=600))
            self.history["T"].append((1.0, Decimal(50)))

        def top_of_book(self, ticker):
            # Ask still at/below the signal cap so the IOC BUY can fill.
            return Decimal(49), Decimal(10), Decimal(49), Decimal(10)

        def lifecycle(self, ticker):
            return 1000.0, False

    cfg = Config(sports=["Tennis"])
    cfg.sim_latency_s = 1.0
    feed = Feed()
    strategy = ScalpStrategy(cfg)
    executor = Executor(cfg, None, feed, clock=lambda: 2.0,
                        sleep=lambda _: None)
    log = ResearchLog(
        tempfile.mkdtemp(), clock=lambda: 2.0, session_id="DELAYED",
        config=cfg,
        provenance_by_ticker={"T": research_provenance(
            event_ticker="EVENT-DELAYED")})
    executor.submit_paper("T", "BUY", Decimal(2), "dip", now=1.0)
    ctx = Context(
        cfg, feed, strategy, executor, log, Safety(cfg), clock=lambda: 2.0)
    process_tick(
        ctx, "T", Decimal(49), Decimal(49), Decimal(49),
        observed_at=2.0)
    import csv
    with open(log.trade_path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["side"] == "BUY"
    assert rows[0]["sport"] == "Tennis"
    assert rows[0]["event_ticker"] == "EVENT-DELAYED"
    print("PASS delayed paper fill uses logger-owned full provenance")


def test_immediate_buy_sell_paths_log_full_provenance():
    from collections import defaultdict
    from types import SimpleNamespace
    from research_log import ResearchLog

    class Feed:
        history = defaultdict(list)

        def top_of_book(self, ticker):
            return Decimal(50), Decimal(10), Decimal(52), Decimal(10)

        def lifecycle(self, ticker):
            return 1000.0, False

        def entry_allowed(self, *args):
            return True

        def early_close_risk(self, ticker):
            return False

    class Strategy:
        realized_pnl = Decimal(0)

        def __init__(self, side):
            self.side = side
            self.positions = (
                {"T": SimpleNamespace(
                    contracts=Decimal(2), entry_price=Decimal(50),
                    entry_fee_usd=Decimal(0))}
                if side == "SELL" else {})

        def refresh_daily_pnl(self, now):
            return None

        def check_exit(self, ticker, bid, now=None):
            return ({"reason": "stop"} if self.side == "SELL" else None)

        def check_entry(self, *args, **kwargs):
            return ({"reason": "dip"} if self.side == "BUY" else None)

        def record_fill(self, ticker, side, price, count, fee, **kwargs):
            self.positions.pop(ticker, None)

    class Immediate:
        journal = None
        last_outcome_id = None
        last_observation = None

        def execute(self, ticker, side, contracts, **kwargs):
            return Decimal(51), Decimal(contracts), Decimal("0.01")

    directory = tempfile.mkdtemp()
    cfg = Config(paper_trading=False, sports=["Tennis"])
    log = ResearchLog(
        directory, clock=lambda: 1.0, session_id="IMMEDIATE",
        config=cfg,
        provenance_by_ticker={"T": research_provenance(
            event_ticker="EVENT-IMMEDIATE")})
    for side in ("BUY", "SELL"):
        ctx = Context(
            cfg, Feed(), Strategy(side), Immediate(), log, Safety(cfg),
            clock=lambda: 1.0)
        process_tick(
            ctx, "T", Decimal(51), Decimal(50), Decimal(52),
            observed_at=1.0)
    import csv
    with open(log.trade_path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["side"] for row in rows] == ["BUY", "SELL"]
    assert {row["event_ticker"] for row in rows} == {"EVENT-IMMEDIATE"}
    print("PASS immediate BUY/SELL paths use logger-owned provenance")


def test_v6_logger_rejects_missing_or_unknown_ticker_provenance():
    from research_log import ResearchLog

    for cfg, provenance in (
            (Config(), {}),
            (Config(sports=["Tennis"]), {"T": object()}),
            (Config(sports=["Tennis"]), {
                "T": research_provenance(sport="Basketball")}),
            (Config(sports=["Tennis"], tickers=["T"]), {}),
            (Config(sports=["Tennis"], tickers=["T"]), {
                "OTHER": research_provenance()}),
    ):
        directory = tempfile.mkdtemp()
        before = os.listdir(directory)
        try:
            ResearchLog(
                directory, config=cfg,
                provenance_by_ticker=provenance)
            assert False, (cfg.sports, provenance)
        except (TypeError, ValueError):
            pass
        assert os.listdir(directory) == before

    empty = ResearchLog(
        tempfile.mkdtemp(), config=Config(sports=["Tennis"]),
        provenance_by_ticker={})
    empty.end(clean=True, reason="empty discovery")

    directory = tempfile.mkdtemp()
    log = ResearchLog(
        directory, config=Config(sports=["Tennis"]),
        provenance_by_ticker={"T": research_provenance()})
    before = os.path.getsize(log.tick_path)
    for writer in (
            lambda: log.tick(
                "UNKNOWN", 50, 49, 51, 10, 10,
                close_ts=1000, can_close_early=False),
            lambda: log.trade(
                "UNKNOWN", "BUY", 51, 1, "dip", fee=0),
            lambda: log.event("UNKNOWN", "api_error", detail="bad"),
    ):
        try:
            writer()
            assert False
        except ValueError:
            pass
        assert os.path.getsize(log.tick_path) == before
    print("PASS v6 logger validates/freeze provenance before appending")


def test_v6_terminal_rows_are_unscoped_and_final():
    import csv
    from research_log import ResearchLog

    log = ResearchLog(
        tempfile.mkdtemp(), config=Config(sports=["Tennis"]),
        provenance_by_ticker={"T": research_provenance()})
    for invalid in (
            lambda: log.event("", "api_error", detail="unscoped"),
            lambda: log.end(clean=True, reason=""),
    ):
        try:
            invalid()
            assert False
        except ValueError:
            pass
    log.end(clean=False, reason="loss limit", ts=2)
    with open(log.tick_path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    row = rows[-1]
    assert row["event"] == "session_halt"
    assert row["detail"] == "loss limit"
    assert row["selected_sports"] == '["Tennis"]'
    assert all(row[field] == "" for field in (
        "ticker", "sport", "league", "series_ticker", "milestone_id",
        "event_ticker", "scheduled_start_ts", "close_ts",
        "can_close_early", "mid", "bid", "ask", "bid_qty", "ask_qty"))
    for writer in (
            lambda: log.tick(
                "T", 50, 49, 51, 10, 10,
                close_ts=1000, can_close_early=False),
            lambda: log.trade("T", "BUY", 51, 1, "dip", fee=0),
            lambda: log.event("T", "api_error", detail="late"),
            lambda: log.end(clean=True, reason="again"),
    ):
        try:
            writer()
            assert False
        except ValueError:
            pass
    print("PASS v6 terminal rows are unscoped, reasoned, and final")


def test_replay_rejects_v5_and_mixed_schema_logs():
    from replay import load_log

    v5_header = [
        "schema_version", "session_id", "starting_daily_pnl_usd",
        "starting_utc_day", "utc_day", "config_fingerprint",
        "code_fingerprint", "ts", "event_id", "ticker", "event",
        "detail", "close_ts", "can_close_early", "mid", "bid", "ask",
        "bid_qty", "ask_qty",
    ]
    v5_row = [
        5, "OLD", 0, "1970-01-01", "1970-01-01", "cfg",
        "archived-code-fingerprint", 1, "E", "T", "quote", "",
        1000, "false", 50, 49, 51, 10, 10,
    ]
    path = _write_research_csv([v5_row], header=v5_header)
    before = open(path, "rb").read()
    try:
        load_log(path)
        assert False
    except ValueError as error:
        text = str(error)
        assert "archived v5" in text
        assert "archived-code-fingerprint" in text
    assert open(path, "rb").read() == before

    mixed = research_row()
    mixed[0] = 5
    path = _write_research_csv([mixed])
    try:
        load_log(path)
        assert False
    except ValueError as error:
        assert "archived v5" in str(error)
    print("PASS replay rejects legacy/mixed schemas without modifying input")


def test_replay_rejects_whitespace_only_terminal_reason():
    from replay import load_log

    path = _write_research_csv([
        research_row(ts=1),
        research_row(
            ts=2, ticker="", event="session_end", detail="   "),
    ])
    try:
        load_log(path)
        assert False
    except ValueError as error:
        assert "reason" in str(error)
    print("PASS replay rejects a whitespace-only terminal reason")


def test_replay_rejects_each_missing_provenance_field():
    from replay import load_log

    for index in (10, 12, 13, 14):
        row = research_row()
        row[index] = ""
        try:
            load_log(_write_research_csv([row]))
            assert False, RESEARCH_HEADER[index]
        except ValueError as error:
            assert RESEARCH_HEADER[index] in str(error)
    for raw in ("", "NaN", "-1"):
        row = research_row()
        row[15] = raw
        try:
            load_log(_write_research_csv([row]))
            assert False, raw
        except ValueError as error:
            assert "scheduled_start_ts" in str(error)
    print("PASS strict v6 replay requires every provenance field")


def test_replay_rejects_invalid_or_drifting_provenance():
    from replay import load_log

    cfg = Config(sports=["Basketball", "Tennis"])
    malformed_selected = (
        '["Tennis", "Basketball"]',
        '["Tennis","Tennis"]',
        '["Tennis",""]',
        '"Tennis"',
    )
    for text in malformed_selected:
        row = research_row(
            cfg=cfg, selected_sports=("Basketball", "Tennis"),
            selected_sports_text=text)
        try:
            load_log(_write_research_csv([row]), cfg=cfg)
            assert False, text
        except ValueError as error:
            assert "selected_sports" in str(error)

    drift = [
        research_row(
            cfg=cfg, ts=1,
            selected_sports=("Basketball", "Tennis")),
        research_row(
            cfg=cfg, ts=2, ticker="T", sport="Basketball",
            selected_sports=("Basketball", "Tennis")),
    ]
    try:
        load_log(_write_research_csv(drift), cfg=cfg)
        assert False
    except ValueError as error:
        assert "provenance" in str(error)

    cross_event = [
        research_row(
            cfg=cfg, ts=1, ticker="A", sport="Tennis",
            event_ticker="SHARED",
            selected_sports=("Basketball", "Tennis")),
        research_row(
            cfg=cfg, ts=2, ticker="B", sport="Basketball",
            series_ticker="KXNBA", milestone_id="M2",
            event_ticker="SHARED", scheduled_start_ts=7200,
            selected_sports=("Basketball", "Tennis")),
    ]
    try:
        load_log(_write_research_csv(cross_event), cfg=cfg)
        assert False
    except ValueError as error:
        assert "event" in str(error) and "provenance" in str(error)

    # Filtering is applied only after every row has passed strict validation.
    hidden = [
        research_row(cfg=cfg, ts=1, ticker="A", sport="Tennis",
                     event_ticker="A-EVENT",
                     selected_sports=("Basketball", "Tennis")),
        research_row(cfg=cfg, ts=2, ticker="B", sport="Basketball",
                     series_ticker="", event_ticker="B-EVENT",
                     selected_sports=("Basketball", "Tennis")),
    ]
    try:
        load_log(_write_research_csv(hidden), tickers=["A"], cfg=cfg)
        assert False
    except ValueError as error:
        assert "series_ticker" in str(error)

    duplicate = research_row(
        cfg=cfg, selected_sports=("Basketball", "Tennis"))
    try:
        load_log(_write_research_csv([duplicate, duplicate]), cfg=cfg)
        assert False
    except ValueError as error:
        assert "duplicate" in str(error)
    print("PASS replay rejects selected-Sports/provenance drift before filtering")


def test_replay_accepts_empty_league_but_preserves_supplied_league():
    from replay import load_log

    cfg = Config(sports=["Tennis"])
    rows = _clean_v6_rows(
        cfg=cfg,
        quotes=(
            {"ticker": "A", "league": "", "event_ticker": "EA"},
            {"ticker": "B", "league": "ATP",
             "event_ticker": "EB", "ts": 2},
        ))
    metadata = load_log(
        _write_research_csv(rows), cfg=cfg, include_metadata=True)
    provenance = metadata[7]
    assert provenance["A"].league is None
    assert provenance["B"].league == "ATP"
    print("PASS replay round-trips unavailable and supplied leagues")


def test_replay_exposes_market_provenance():
    from replay import replay

    cfg = Config(sports=["Basketball", "Tennis"])
    shared = {
        "sport": "Tennis", "league": "ATP",
        "series_ticker": "KXATP", "milestone_id": "M1",
        "event_ticker": "EVENT-1", "scheduled_start_ts": 100,
        "selected_sports": ("Basketball", "Tennis"),
    }
    rows = _clean_v6_rows(
        cfg=cfg, selected_sports=("Basketball", "Tennis"),
        quotes=(
            dict(shared, ticker="A", ts=1),
            dict(shared, ticker="B", ts=2),
        ))
    result = replay(_write_research_csv(rows), cfg=cfg)
    assert result["selected_sports"] == ("Basketball", "Tennis")
    assert set(result["market_provenance"]) == {"A", "B"}
    assert (result["market_provenance"]["A"].event_ticker
            == result["market_provenance"]["B"].event_ticker
            == "EVENT-1")
    print("PASS replay exposes typed provenance and selected Sports")


def test_runtime_v6_log_replays_same_fills_and_pnl():
    from collections import defaultdict, deque
    from replay import ReplayFeed, VirtualClock, replay
    from research_log import ResearchLog

    rows = [
        (float(ts), Decimal(60), Decimal(59), Decimal(61),
         Decimal(100), Decimal(100))
        for ts in range(1, 21)
    ] + [
        (21.0, Decimal(52), Decimal(51), Decimal(53),
         Decimal(100), Decimal(100)),
        (22.0, Decimal("51.75"), Decimal(51), Decimal("52.5"),
         Decimal(100), Decimal(6)),
        (24.0, Decimal(61), Decimal(60), Decimal(62),
         Decimal(6), Decimal(100)),
        (25.0, Decimal(61), Decimal(60), Decimal(62),
         Decimal(6), Decimal(100)),
    ]
    cfg = Config(sports=["Tennis"], tp_trail_cents=0)
    cfg.sim_latency_s = 1.0
    clock = VirtualClock()
    feed = ReplayFeed(clock)
    strategy = ScalpStrategy(cfg)
    executor = Executor(cfg, None, feed, clock=clock.time,
                        sleep=clock.sleep)
    log = ResearchLog(
        tempfile.mkdtemp(), clock=clock.time, session_id="RUNTIME-V6",
        config=cfg, provenance_by_ticker={"T": research_provenance(
            event_ticker="EVENT-T")})
    ctx = Context(
        cfg, feed, strategy, executor, log, Safety(cfg), clock=clock.time)
    runtime_trades = []
    original = strategy.record_fill

    def capture(ticker, side, price, count, fee, now=None):
        runtime_trades.append((ticker, side, price, count))
        return original(ticker, side, price, count, fee, now=now)

    strategy.record_fill = capture
    for ts, mid, bid, ask, bid_qty, ask_qty in rows:
        clock.t = ts
        feed.apply(
            ts, "T", mid, bid, ask, bid_qty, ask_qty,
            close_ts=4070908800.0, can_close_early=False)
        process_tick(ctx, "T", mid, bid, ask, observed_at=ts)
    clock.t = 26
    log.end(clean=True, reason="operator interrupt")
    result = replay(log.tick_path, cfg=cfg)
    assert result["trades"] == runtime_trades
    assert result["realized"] == strategy.realized_pnl
    print("PASS runtime v6 log replays identical fills and realized P&L")


def test_analyzer_delegates_to_strict_v6_loader():
    import analyze

    cfg = Config(sports=["Tennis"])
    path = _write_research_csv(_clean_v6_rows(cfg=cfg))
    original_cfg = analyze.CFG
    original_loader = getattr(analyze, "load_log", None)
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return original_loader(*args, **kwargs)

    try:
        analyze.CFG = cfg
        analyze.load_log = capture
        series, groups, selected_sports, provenance = analyze.load(path)
        assert calls and calls[0][1]["include_metadata"] is True
        assert list(series) == ["T"]
        assert groups == {"T": "E"}
        assert selected_sports == ("Tennis",)
        assert provenance["T"] == research_provenance()
    finally:
        analyze.CFG = original_cfg
        analyze.load_log = original_loader
    print("PASS analyzer delegates to the one strict v6 replay loader")


def _task7_result(*, selected_sports, provenance, totals=None, trades=(),
                  evaluable=True):
    totals = dict(totals or {})
    return {
        "trades": list(trades),
        "per_ticker_total": totals,
        "residuals": {},
        "residual_contracts": Decimal(0),
        "pending_orders": 0,
        "data_gaps": 0,
        "halted": False,
        "halt_reason": None,
        "terminal_status": "clean",
        "terminal_reason": "operator interrupt",
        "rows_processed": max(1, len(provenance)),
        "rows_available": max(1, len(provenance)),
        "evaluable": evaluable,
        "selected_sports": tuple(selected_sports),
        "market_provenance": dict(provenance),
    }


def _task7_series(*tickers):
    return {
        ticker: [(1.0, Decimal(50), Decimal(49), Decimal(51))]
        for ticker in tickers
    }


def test_analyzer_requires_complete_consistent_v6_provenance():
    import analyze

    series = _task7_series("TENNIS-NAME-IS-NOT-PROVENANCE")
    provenance = {
        "TENNIS-NAME-IS-NOT-PROVENANCE": research_provenance(
            sport="Tennis", event_ticker="EVENT-0"),
    }
    groups = {"TENNIS-NAME-IS-NOT-PROVENANCE": "EVENT-0"}
    partitions = analyze.build_partitions(
        series, groups, ("Tennis",), provenance)
    assert partitions["overall"]["TEST"] == (
        "TENNIS-NAME-IS-NOT-PROVENANCE",)

    invalid = (
        (series, groups, ("Tennis",), {}),
        (series, {}, ("Tennis",), provenance),
        (series, {"TENNIS-NAME-IS-NOT-PROVENANCE": "OTHER"},
         ("Tennis",), provenance),
        (series, groups, ("Basketball",), provenance),
    )
    for args in invalid:
        try:
            analyze.build_partitions(*args)
            assert False, args
        except ValueError:
            pass

    result = _task7_result(
        selected_sports=("Tennis",), provenance=provenance)
    analyze.validate_replay_metadata(
        ("Tennis",), provenance, result)
    for changed in (
            dict(result, selected_sports=("Basketball",)),
            dict(result, market_provenance={}),
    ):
        try:
            analyze.validate_replay_metadata(
                ("Tennis",), provenance, changed)
            assert False
        except ValueError:
            pass
    print("PASS analyzer requires complete consistent v6 provenance")


def test_analyzer_groups_sibling_contracts_by_event_ticker():
    import analyze

    siblings = {
        "WINNER": research_provenance(
            sport="Tennis", event_ticker="EVENT-0"),
        "MARGIN": research_provenance(
            sport="Tennis", event_ticker="EVENT-0"),
    }
    groups = {ticker: "EVENT-0" for ticker in siblings}
    baseline = analyze.build_partitions(
        _task7_series("WINNER", "MARGIN"), groups,
        ("Tennis",), siblings)
    assert baseline["bucket_by_ticker"] == {
        "WINNER": "TEST", "MARGIN": "TEST"}

    extended_provenance = dict(siblings)
    extended_provenance["UNRELATED"] = research_provenance(
        sport="Tennis", event_ticker="EVENT-5")
    extended_groups = dict(groups, UNRELATED="EVENT-5")
    extended = analyze.build_partitions(
        _task7_series("UNRELATED", "MARGIN", "WINNER"),
        extended_groups, ("Tennis",), extended_provenance)
    assert extended["bucket_by_ticker"]["WINNER"] == "TEST"
    assert extended["bucket_by_ticker"]["MARGIN"] == "TEST"
    assert extended["bucket_by_ticker"]["UNRELATED"] == "TRAIN"
    print("PASS analyzer keeps sibling contracts in one stable Event bucket")


def test_analyzer_reports_overall_and_each_sport_train_test():
    import analyze

    selected = ("Basketball", "Tennis")
    provenance = {
        "T-TEST": research_provenance(
            sport="Tennis", event_ticker="TENNIS-0"),
        "T-TRAIN": research_provenance(
            sport="Tennis", event_ticker="TENNIS-1"),
        "B-TEST": research_provenance(
            sport="Basketball", event_ticker="BASKET-0"),
        "B-TRAIN": research_provenance(
            sport="Basketball", event_ticker="BASKET-1"),
    }
    series = _task7_series("T-TEST", "B-TRAIN", "T-TRAIN", "B-TEST")
    groups = {
        ticker: item.event_ticker for ticker, item in provenance.items()}
    partitions = analyze.build_partitions(
        series, groups, selected, provenance)
    result = _task7_result(
        selected_sports=selected, provenance=provenance,
        totals={
            "B-TRAIN": Decimal("0.25"), "B-TEST": Decimal("1.00"),
            "T-TRAIN": Decimal("-0.25"), "T-TEST": Decimal("0.50"),
        },
        trades=(
            ("B-TRAIN", "SELL", Decimal(55), Decimal(1)),
            ("B-TEST", "SELL", Decimal(55), Decimal(1)),
            ("T-TRAIN", "SELL", Decimal(55), Decimal(1)),
            ("T-TEST", "SELL", Decimal(55), Decimal(1)),
        ))
    text = analyze.format_replay_report(partitions, result)
    assert text.startswith(
        "FULL REPLAY through one shared portfolio path:\n\nOVERALL\n")
    assert text.index("SPORT: Basketball") < text.index("SPORT: Tennis")
    assert text.count("SPORT: ") == 2
    for section in ("OVERALL", "SPORT: Basketball", "SPORT: Tennis"):
        body = text.split(section, 1)[1]
        if section != "SPORT: Tennis":
            body = body.split("\n\n", 1)[0]
        assert "  TRAIN:" in body and "  TEST:" in body
    assert "1 markets, 1 exits" in text
    print("PASS analyzer reports overall and every Sport TRAIN/TEST partition")


def test_positive_overall_does_not_qualify_nonpositive_sport():
    import analyze

    selected = ("Basketball", "Tennis")
    provenance = {
        "B": research_provenance(
            sport="Basketball", event_ticker="BASKET-0"),
        "T": research_provenance(
            sport="Tennis", event_ticker="TENNIS-0"),
    }
    groups = {"B": "BASKET-0", "T": "TENNIS-0"}
    partitions = analyze.build_partitions(
        _task7_series("B", "T"), groups, selected, provenance)
    result = _task7_result(
        selected_sports=selected, provenance=provenance,
        totals={"B": Decimal("2.00"), "T": Decimal("-1.00")})
    text = analyze.format_replay_report(partitions, result)
    overall = text.split("OVERALL\n", 1)[1].split(
        "\n\nSPORT: Basketball", 1)[0]
    basketball = text.split("SPORT: Basketball\n", 1)[1].split(
        "\n\nSPORT: Tennis", 1)[0]
    tennis = text.split("SPORT: Tennis\n", 1)[1]
    assert "net P&L +1.00 USD" in overall
    assert basketball.endswith(
        "Held-out: SUPPORTED HYPOTHESIS: TEST is evaluable "
        "and net P&L > 0")
    assert tennis.endswith(
        "Held-out: NOT SUPPORTED: TEST <= 0, empty, or not evaluable")
    print("PASS positive overall TEST cannot qualify a losing Sport")


def test_selected_sport_with_empty_test_or_zero_markets_is_not_supported():
    import analyze

    selected = ("Basketball", "Tennis", "Cricket")
    provenance = {
        "B-TRAIN": research_provenance(
            sport="Basketball", event_ticker="BASKET-1"),
        "T-TEST": research_provenance(
            sport="Tennis", event_ticker="TENNIS-0"),
    }
    groups = {
        ticker: item.event_ticker for ticker, item in provenance.items()}
    partitions = analyze.build_partitions(
        _task7_series("B-TRAIN", "T-TEST"),
        groups, selected, provenance)
    result = _task7_result(
        selected_sports=selected, provenance=provenance,
        totals={"B-TRAIN": Decimal(1), "T-TEST": Decimal(1)})
    text = analyze.format_replay_report(partitions, result)
    basketball = text.split("SPORT: Basketball\n", 1)[1].split(
        "\n\nSPORT: Tennis", 1)[0]
    tennis = text.split("SPORT: Tennis\n", 1)[1].split(
        "\n\nSPORT: Cricket", 1)[0]
    cricket = text.split("SPORT: Cricket\n", 1)[1]
    assert "TEST: 0 markets" in basketball
    assert basketball.endswith(
        "Held-out: NOT SUPPORTED: TEST <= 0, empty, or not evaluable")
    assert tennis.endswith(
        "Held-out: SUPPORTED HYPOTHESIS: TEST is evaluable "
        "and net P&L > 0")
    assert "TRAIN: 0 markets" in cricket and "TEST: 0 markets" in cricket
    assert cricket.endswith(
        "Held-out: NOT SUPPORTED: TEST <= 0, empty, or not evaluable")
    print("PASS empty TEST and zero-market selected Sports remain unsupported")


def test_global_incompleteness_disqualifies_every_sport():
    import copy
    import analyze

    selected = ("Basketball", "Tennis")
    provenance = {
        "A": research_provenance(
            sport="Basketball", event_ticker="BASKET-1"),
        "B": research_provenance(
            sport="Tennis", event_ticker="TENNIS-0"),
    }
    groups = {"A": "BASKET-1", "B": "TENNIS-0"}
    partitions = analyze.build_partitions(
        _task7_series("A", "B"), groups, selected, provenance)
    base = _task7_result(
        selected_sports=selected, provenance=provenance,
        totals={"A": Decimal(0), "B": Decimal(2)})
    variants = []

    residual = copy.deepcopy(base)
    residual.update({
        "residuals": {"A": {
            "contracts": Decimal(1), "marked_pnl": Decimal("-1")}},
        "residual_contracts": Decimal(1),
    })
    variants.append(residual)
    for updates in (
            {"pending_orders": 1},
            {"halted": True, "halt_reason": "loss limit"},
            {"data_gaps": 1},
            {"terminal_status": "missing", "terminal_reason": None},
            {"rows_processed": 1, "rows_available": 2},
    ):
        variant = copy.deepcopy(base)
        variant.update(updates)
        variants.append(variant)

    for result in variants:
        # Even a contradictory optimistic flag cannot override concrete
        # global incompleteness.
        result["evaluable"] = True
        text = analyze.format_replay_report(partitions, result)
        assert "SUPPORTED HYPOTHESIS" not in text
        assert text.count(
            "Held-out: NOT SUPPORTED: TEST <= 0, empty, or not evaluable"
        ) == 2
        assert "[RESEARCH-EVALUABLE" not in text
    print("PASS global incompleteness disqualifies every Sport")


def test_sport_attribution_is_stable_across_input_order():
    import analyze

    selected = ("Tennis", "Basketball")
    first_provenance = {
        "T-Z": research_provenance(
            sport="Tennis", event_ticker="TENNIS-0"),
        "B": research_provenance(
            sport="Basketball", event_ticker="BASKET-0"),
        "T-A": research_provenance(
            sport="Tennis", event_ticker="TENNIS-0"),
    }
    second_provenance = dict(reversed(tuple(first_provenance.items())))
    first_series = _task7_series("T-Z", "B", "T-A")
    second_series = _task7_series("T-A", "B", "T-Z")
    first_groups = {
        ticker: item.event_ticker
        for ticker, item in first_provenance.items()}
    second_groups = {
        ticker: item.event_ticker
        for ticker, item in second_provenance.items()}
    original_points = tuple(first_series["T-Z"])
    first = analyze.build_partitions(
        first_series, first_groups, selected, first_provenance)
    second = analyze.build_partitions(
        second_series, second_groups, selected, second_provenance)
    assert first == second
    assert first["sports"]["Tennis"]["TEST"] == ("T-A", "T-Z")
    assert tuple(first_series["T-Z"]) == original_points

    result = _task7_result(
        selected_sports=selected, provenance=first_provenance,
        totals={"T-A": Decimal(1), "T-Z": Decimal(1), "B": Decimal(1)})
    text = analyze.format_replay_report(first, result)
    assert text.index("SPORT: Tennis") < text.index("SPORT: Basketball")
    print("PASS Sport attribution is stable across mapping input order")


if __name__ == "__main__":
    test_sport_selection_is_case_insensitive_and_canonical()
    test_all_sports_and_unknown_sports_are_rejected_with_choices()
    test_selected_sport_requires_games_scope_and_competition()
    test_local_day_window_is_half_open_and_dst_safe()
    test_default_local_day_window_uses_system_dst_rules()
    test_contract_ranking_uses_all_five_tie_breakers()
    test_series_resolution_uses_unique_longest_official_prefix()
    test_domain_values_reject_invalid_identity_numeric_and_duplicates()
    test_api_only_new_sport_works_without_source_change()
    test_wimbledon_soccer_is_not_classified_as_tennis()
    test_series_mapping_uses_every_canonical_api_sport()
    test_main_game_event_is_preferred_over_props()
    test_sole_primary_game_fallback_and_ambiguous_skip()
    test_empty_primary_list_uses_main_or_skips_as_ambiguous()
    test_duplicate_metadata_must_be_identical()
    test_identical_cross_competition_milestone_without_league_dedupes()
    test_incomplete_inventory_prevents_ranking()
    test_best_ten_are_global_across_selected_sports()
    test_dynamic_contract_cap_allows_siblings_from_one_game()
    test_dynamic_discovery_filters_books_and_reports_stable_stats()
    test_discovery_requires_exactly_one_source()
    test_explicit_tickers_must_be_today_games_and_within_cap()
    test_explicit_tickers_reject_duplicates_and_over_cap_before_network()
    test_explicit_tickers_reject_unordered_or_lazy_inputs_before_network()
    test_explicit_tickers_preserve_order_and_derive_api_ordered_sports()
    test_explicit_ticker_requires_matching_market_event_series_and_nested_identity()
    test_explicit_ticker_requires_games_capable_canonical_sport()
    test_explicit_ticker_requires_unique_main_or_sole_primary_milestone()
    test_explicit_ticker_rejects_wrong_day_unrelated_and_conflicting_milestones()
    test_explicit_milestone_identity_conflicts_across_event_queries()
    test_explicit_ticker_rejects_each_ineligible_book_condition()
    test_explicit_tickers_share_event_queries_and_count_stats_once()
    test_feed_installs_immutable_discovery_provenance()
    test_feed_rejects_unknown_subscription_ticker()
    test_quote_event_mismatch_fails_closed()
    test_discovery_failure_is_durable_and_returns_nonzero()
    test_post_discovery_session_uses_canonical_sports()
    test_run_session_discovers_and_reports_only_once()
    test_complete_empty_discovery_writes_clean_terminal()
    test_subscription_failure_ends_canonical_log_noncleanly()
    test_keyboard_interrupt_and_system_exit_are_not_swallowed_by_discovery()
    test_v6_config_fingerprint_uses_canonical_sports()
    test_v6_quote_and_trade_rows_share_full_provenance()
    test_delayed_paper_fill_logs_full_provenance()
    test_immediate_buy_sell_paths_log_full_provenance()
    test_v6_logger_rejects_missing_or_unknown_ticker_provenance()
    test_v6_terminal_rows_are_unscoped_and_final()
    test_replay_rejects_v5_and_mixed_schema_logs()
    test_replay_rejects_whitespace_only_terminal_reason()
    test_replay_rejects_each_missing_provenance_field()
    test_replay_rejects_invalid_or_drifting_provenance()
    test_replay_accepts_empty_league_but_preserves_supplied_league()
    test_replay_exposes_market_provenance()
    test_runtime_v6_log_replays_same_fills_and_pnl()
    test_analyzer_delegates_to_strict_v6_loader()
    test_create_contract()
    test_ack_vs_poll_contract()
    test_current_orderbook_contract()
    test_current_market_contract_and_empty_book_normalization()
    test_current_sports_filters_contract()
    test_sports_filters_reject_malformed_scopes_and_competitions()
    test_current_sports_series_contract()
    test_series_response_accepts_off_category_and_null_tags()
    test_series_response_rejects_duplicate_tickers_and_bad_tags()
    test_series_response_rejects_nonempty_cursor_as_incomplete()
    test_current_milestone_contract()
    test_milestone_accepts_empty_event_ticker_lists()
    test_milestone_contract_rejects_bad_details_dates_and_tickers()
    test_current_nested_event_contract_uses_market_parser()
    test_direct_event_uses_nested_markets_when_top_level_is_empty()
    test_direct_event_rejects_nonempty_wrapper_when_nested_markets_empty()
    test_nested_event_counts_only_recognized_unsupported_markets()
    test_sports_client_uses_documented_public_queries()
    test_public_discovery_metadata_requests_are_paced()
    test_discovery_cursors_missing_nonstring_repeated_and_capped_fail()
    test_endpoint_separation()
    test_http_boundary_wiring_and_strict_envelopes()
    test_public_sports_client_loads_private_key_only_for_auth()
    test_cli_parses_sports_without_hardcoded_choices()
    test_cli_rejects_empty_or_duplicate_sports()
    test_config_validates_unique_selection_lists()
    test_cli_rejects_sports_with_configured_tickers()
    test_paper_startup_requires_sports_or_explicit_tickers()
    test_list_sports_is_public_and_creates_no_session_artifact()
    test_cli_reports_unknown_arguments_without_traceback()
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
    test_analyzer_end_to_end_v6_smoke()
    test_analyzer_requires_complete_consistent_v6_provenance()
    test_analyzer_groups_sibling_contracts_by_event_ticker()
    test_analyzer_reports_overall_and_each_sport_train_test()
    test_positive_overall_does_not_qualify_nonpositive_sport()
    test_selected_sport_with_empty_test_or_zero_markets_is_not_supported()
    test_global_incompleteness_disqualifies_every_sport()
    test_analyzer_replays_shared_portfolio_exactly_once()
    test_sport_attribution_is_stable_across_input_order()
    test_aggregate_fee_rounding()
    test_residual_valuation_respects_depth_and_slippage()
    test_unknown_depth_never_means_unlimited_fill()
    test_stop_deadline_does_not_slide_while_pending()
    test_time_exit_upgrades_working_take_profit()
    test_ioc_ask_cap_miss_cancels_entry()
    test_entry_edge_uses_executable_ask_depth()
    test_trailing_tp_lets_runners_extend_past_arm()
    test_trailing_tp_zero_keeps_fixed_arm_exit()
    test_subcent_sell_fill_is_never_improved()
    test_pricefeed_uses_installed_event_identity()
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
    test_preflight_validates_public_sports_metadata_without_orders()
    test_preflight_reports_metadata_skips_and_unobserved_portfolio_rows()
    test_preflight_never_calls_order_mutation_endpoints()
    test_preflight_counts_sports_without_assuming_pseudo_row()
    test_preflight_warns_when_games_metadata_is_unavailable()
    test_preflight_rejects_sampled_sports_schema_drift()
    test_preflight_public_checks_run_without_credentials()
    test_readme_documents_sports_commands_and_v6_break()
    test_preflight_warns_but_accepts_valid_empty_portfolio_collections()
    test_preflight_market_sample_uses_exactly_one_page()
    test_market_collections_skip_only_known_unsupported_products()
    test_market_collection_skips_never_hide_schema_drift()
    test_discovery_stops_before_generic_pagination_cap_when_first_page_fills_cap()
    test_discovery_filters_page_by_page_and_aggregates_skip_counts()
    test_discovery_page_cap_returns_explicit_truncated_partial_scan()
    test_discovery_rejects_malformed_and_repeated_cursors()
    test_discovery_and_preflight_report_unsupported_market_counts()
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
    test_paper_early_close_risk_is_visible_but_does_not_block_entry()
    test_nonpaper_early_close_risk_is_visible_and_blocks_entry()
    test_live_fill_risk_uses_executor_requote_immediately()
    test_replay_enforces_logged_market_lifecycle()
    test_termination_signals_route_through_interrupt()
    test_live_and_demo_disabled()
    from test_espn_prob_gate import (
        test_names_match_surname,
        test_neutral_model_rejects_match_already_lost,
        test_parse_espn_competition_live,
        test_gate_blocks_unbound_and_allows_edge,
        test_parse_live_tennis_match_itf,
        test_gate_binds_itf_via_live_tennis_secondary,
    )
    test_names_match_surname()
    test_neutral_model_rejects_match_already_lost()
    test_parse_espn_competition_live()
    test_gate_blocks_unbound_and_allows_edge()
    test_parse_live_tennis_match_itf()
    test_gate_binds_itf_via_live_tennis_secondary()
    print("\nALL TESTS PASS (214 tests)")