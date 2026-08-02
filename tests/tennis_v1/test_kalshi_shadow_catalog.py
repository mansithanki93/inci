from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


_ORIGIN = "https://external-api.kalshi.com"
_EVENT = "KXATP-26JUL26-ONE"


def _market(ticker: str, player: str, *, event_ticker: str = _EVENT, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_type": "binary",
        "yes_sub_title": player,
        "no_sub_title": "Field",
        "status": "active",
        "close_time": "2026-07-27T00:00:00Z",
        "can_close_early": False,
        "notional_value_dollars": "1.0000",
        "yes_bid_dollars": "0.4900",
        "yes_ask_dollars": "0.5100",
        "yes_bid_size_fp": "10.00",
        "yes_ask_size_fp": "11.00",
    }
    row.update(overrides)
    return row


def _event(
    event_ticker: str = _EVENT,
    *,
    markets: list[dict[str, object]] | None = None,
    title: str = "Alice Smith vs Béa Jones",
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "event_ticker": event_ticker,
        "series_ticker": "KXATP",
        "category": "Sports",
        "title": title,
        "markets": markets
        if markets is not None
        else [
            _market(event_ticker + "-A", "Alice Smith", event_ticker=event_ticker),
            _market(event_ticker + "-B", "Béa Jones", event_ticker=event_ticker),
        ],
    }
    row.update(overrides)
    return row


def _milestone(
    event_ticker: str = _EVENT,
    *,
    identity: str = "match-1",
    start: str = "2026-07-26T18:00:00Z",
    kind: str = "game",
) -> dict[str, object]:
    return {
        "id": identity,
        "category": "Sports",
        "type": kind,
        "start_date": start,
        "title": "Washington match",
        "details": {
            "league": "ATP Washington",
            "main_game_event_ticker": event_ticker,
        },
        "primary_event_tickers": [event_ticker],
        "related_event_tickers": [event_ticker],
    }


def _base_pages(*, events: list[dict[str, object]] | None = None, milestones: list[dict[str, object]] | None = None) -> dict[str, list[dict[str, object]]]:
    return {
        "/trade-api/v2/search/filters_by_sport": [
            {
                "filters_by_sports": {
                    "All sports": {"competitions": {}, "scopes": ["Games"]},
                    "Tennis": {
                        "competitions": {"ATP Washington": {"scopes": ["Games"]}},
                        "scopes": ["Games"],
                    },
                },
                "sport_ordering": ["All sports", "Tennis"],
            }
        ],
        "/trade-api/v2/series": [
            {"series": [{"ticker": "KXATP", "category": "Sports", "tags": ["Tennis"]}]}
        ],
        "/trade-api/v2/milestones": [
            {"milestones": list(milestones or [_milestone()]), "cursor": ""}
        ],
        "/trade-api/v2/events": [
            {"events": list(events or [_event()]), "cursor": ""}
        ],
    }


class _Response:
    def __init__(
        self,
        payload: object = None,
        *,
        raw: bytes | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        if raw is None:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.content = raw
        self.status_code = status
        self.headers = headers or {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Encoding": "identity",
            "Content-Length": str(len(raw)),
        }


class _Session:
    def __init__(self, pages: dict[str, list[object]]) -> None:
        self.trust_env = True
        self.pages = {path: list(values) for path, values in pages.items()}
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> _Response:
        path = urlsplit(url).path
        self.calls.append({"method": method, "url": url, **kwargs})
        values = self.pages.get(path)
        if not values:
            raise AssertionError("unexpected request path")
        value = values.pop(0)
        return value if isinstance(value, _Response) else _Response(value)


def _transport(session: _Session):
    from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogTransport

    with patch("inci_tennis_io.kalshi_shadow_catalog.requests.Session", return_value=session):
        return KalshiShadowCatalogTransport()


class KalshiShadowCatalogTests(unittest.TestCase):
    def test_exact_get_pipeline_produces_two_player_game_and_digest(self) -> None:
        """Catches path/query widening and loss of strict YES player identity."""

        session = _Session(_base_pages())
        transport = _transport(session)
        games, digest = transport.discover_tennis_games(
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
        )

        self.assertFalse(session.trust_env)
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].event_ticker, _EVENT)
        self.assertEqual(games[0].scheduled_start_wall_ns, 1_785_088_800_000_000_000)
        self.assertEqual(
            tuple((row.ticker, row.yes_player_name) for row in games[0].markets),
            ((_EVENT + "-A", "Alice Smith"), (_EVENT + "-B", "Béa Jones")),
        )
        self.assertEqual(
            digest,
            "f5d1884af29ee881e32f7f908f43f2f7f94d6b77b8a67e55462f05668cc6cd82",
        )
        self.assertEqual(
            [(call["method"], urlsplit(str(call["url"])).path, call["params"]) for call in session.calls],
            [
                ("GET", "/trade-api/v2/search/filters_by_sport", {}),
                ("GET", "/trade-api/v2/series", {"category": "Sports"}),
                (
                    "GET",
                    "/trade-api/v2/milestones",
                    {
                        "category": "Sports",
                        "minimum_start_date": "2026-07-26T00:00:00Z",
                        "competition": "ATP Washington",
                        "limit": 500,
                    },
                ),
                (
                    "GET",
                    "/trade-api/v2/events",
                    {
                        "series_ticker": "KXATP",
                        "status": "open",
                        "with_nested_markets": "true",
                        "limit": 200,
                    },
                ),
            ],
        )
        for call in session.calls:
            self.assertTrue(str(call["url"]).startswith(_ORIGIN + "/trade-api/v2/"))
            self.assertEqual(call["allow_redirects"], False)
            self.assertEqual(call["timeout"], (3, 10))
            self.assertEqual(
                call["headers"],
                {"Accept": "application/json", "Accept-Encoding": "identity"},
            )

    def test_pagination_is_exhaustive_and_page_permutations_are_canonical(self) -> None:
        """Catches first-page truncation and order-sensitive output digests."""

        events = []
        milestones = []
        for index in range(7):
            event_ticker = f"KXATP-26JUL26-G{index}"
            events.append(_event(event_ticker, title=f"Player {index}A vs Player {index}B"))
            milestones.append(_milestone(event_ticker, identity=f"match-{index}", start=f"2026-07-26T{10 + index:02d}:00:00Z"))
        pages = _base_pages()
        pages["/trade-api/v2/milestones"] = [
            {"milestones": milestones[3:], "cursor": "milestones-next"},
            {"milestones": milestones[:3], "cursor": ""},
        ]
        pages["/trade-api/v2/events"] = [
            {"events": events[::2], "cursor": "events-next"},
            {"events": events[1::2], "cursor": ""},
        ]
        first_session = _Session(copy.deepcopy(pages))
        first = _transport(first_session).discover_tennis_games(
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
        )

        reversed_pages = _base_pages()
        reversed_pages["/trade-api/v2/milestones"] = [
            {"milestones": list(reversed(milestones)), "cursor": ""}
        ]
        reversed_pages["/trade-api/v2/events"] = [
            {"events": list(reversed(events)), "cursor": ""}
        ]
        second = _transport(_Session(reversed_pages)).discover_tennis_games(
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
        )

        self.assertEqual(len(first[0]), 7)
        self.assertEqual(first, second)
        self.assertEqual(
            [call["params"].get("cursor") for call in first_session.calls[-4:]],
            [None, "milestones-next", None, "events-next"],
        )

    def test_repeated_malformed_and_excessive_cursors_fail_without_catalog(self) -> None:
        """Catches incomplete pagination being returned as a usable catalog."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        cases: list[list[dict[str, object]]] = [
            [
                {"milestones": [_milestone()], "cursor": "again"},
                {"milestones": [], "cursor": "again"},
            ],
            [{"milestones": [_milestone()], "cursor": 7}],
            [{"milestones": [], "cursor": f"p{index}"} for index in range(20)],
        ]
        for pages_for_case in cases:
            with self.subTest(case=len(pages_for_case)):
                pages = _base_pages()
                pages["/trade-api/v2/milestones"] = pages_for_case
                with self.assertRaisesRegex(KalshiShadowCatalogError, "kalshi_catalog_(schema|pagination)_invalid"):
                    _transport(_Session(pages)).discover_tennis_games(
                        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
                    )

    def test_series_cursor_incompleteness_is_sanitized(self) -> None:
        """Catches incomplete or malformed Series inventory being accepted."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        for cursor in ("series-next", 7):
            with self.subTest(cursor=cursor):
                pages = _base_pages()
                pages["/trade-api/v2/series"] = [{
                    "series": [{
                        "ticker": "KXATP",
                        "category": "Sports",
                        "tags": ["Tennis"],
                    }],
                    "cursor": cursor,
                }]
                result = None
                with self.assertRaisesRegex(
                    KalshiShadowCatalogError,
                    "kalshi_catalog_schema_invalid",
                ):
                    result = _transport(_Session(pages)).discover_tennis_games(
                        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
                    )
                self.assertIsNone(result)

    def test_event_cursor_failures_never_return_page_one_games(self) -> None:
        """Catches malformed, repeated, or excessive Event pagination."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        cases: tuple[tuple[str, list[object]], ...] = (
            (
                "repeated",
                [
                    {"events": [_event()], "cursor": "again"},
                    {"events": [], "cursor": "again"},
                ],
            ),
            (
                "malformed",
                [{"events": [_event()], "cursor": 7}],
            ),
            (
                "excessive",
                [
                    {
                        "events": [_event()] if index == 0 else [],
                        "cursor": f"event-page-{index}",
                    }
                    for index in range(20)
                ],
            ),
        )
        for name, event_pages in cases:
            with self.subTest(name=name):
                pages = _base_pages()
                pages["/trade-api/v2/events"] = event_pages
                result = None
                with self.assertRaisesRegex(
                    KalshiShadowCatalogError,
                    "kalshi_catalog_(schema|pagination)_invalid",
                ):
                    result = _transport(_Session(pages)).discover_tennis_games(
                        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
                    )
                self.assertIsNone(result)

    def test_later_event_page_failure_never_returns_page_one_games(self) -> None:
        """Catches usable first-page rows escaping a later boundary failure."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        second_pages = (
            _Response(raw=b"private response", status=503),
            {"events": "malformed", "cursor": ""},
        )
        for second_page in second_pages:
            with self.subTest(second_page=type(second_page).__name__):
                pages = _base_pages()
                pages["/trade-api/v2/events"] = [
                    {"events": [_event()], "cursor": "events-next"},
                    second_page,
                ]
                result = None
                with self.assertRaises(KalshiShadowCatalogError) as caught:
                    result = _transport(_Session(pages)).discover_tennis_games(
                        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
                    )
                self.assertIsNone(result)
                self.assertRegex(
                    str(caught.exception),
                    r"\Akalshi_catalog_[a-z_]+\Z",
                )
                self.assertNotIn("private response", str(caught.exception))

    def test_response_failures_are_sanitized(self) -> None:
        """Catches response text, query values, and player names escaping errors."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        secret = "Sensitive Player Name"
        cases = (
            _Response(raw=b'{"series":[],"series":[]}'),
            _Response(raw=b""),
            _Response(raw=b"{}", headers={"Content-Type": "text/html"}),
            _Response(raw=b"{}", headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}),
            _Response(raw=b"x" * 8_388_609),
            _Response(raw=secret.encode(), status=302),
            _Response(raw=secret.encode(), status=503),
            _Response(raw=b"[]"),
        )
        for response in cases:
            with self.subTest(status=response.status_code, size=len(response.content)):
                pages = _base_pages()
                pages["/trade-api/v2/search/filters_by_sport"] = [response]
                with self.assertRaises(KalshiShadowCatalogError) as caught:
                    _transport(_Session(pages)).discover_tennis_games(
                        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
                    )
                message = str(caught.exception)
                self.assertRegex(message, r"\Akalshi_catalog_[a-z_]+\Z")
                self.assertNotIn(secret, message)
                self.assertNotIn("http", message)

    def test_get_429_backoff_and_public_pacing_are_bounded(self) -> None:
        """Catches altered retry delays or a needless post-catalog sleep."""

        pages = _base_pages()
        pages["/trade-api/v2/search/filters_by_sport"] = [
            _Response({}, status=429),
            _Response({}, status=429),
            pages["/trade-api/v2/search/filters_by_sport"][0],
        ]
        session = _Session(pages)
        transport = _transport(session)
        sleeps: list[float] = []
        transport._sleep = sleeps.append
        transport._monotonic = lambda: 0.0

        transport.discover_tennis_games(
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
        )

        self.assertEqual(sleeps, [0.25, 0.5, 0.05, 0.05, 0.05])
        self.assertEqual(len(session.calls), 6)

    def test_invalid_sibling_groups_are_excluded_and_schema_drift_aborts(self) -> None:
        """Catches partial games and permissive player-name/product fallbacks."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        good = _event()
        invalid_events = [
            _event("KXATP-26JUL26-ONEWAY", markets=[_market("KXATP-26JUL26-ONEWAY-A", "One", event_ticker="KXATP-26JUL26-ONEWAY")]),
            _event("KXATP-26JUL26-THREE", markets=[_market(f"KXATP-26JUL26-THREE-{i}", f"P{i}", event_ticker="KXATP-26JUL26-THREE") for i in range(3)]),
            _event("KXATP-26JUL26-DUP", markets=[_market("KXATP-26JUL26-DUP-A", "Same", event_ticker="KXATP-26JUL26-DUP"), _market("KXATP-26JUL26-DUP-B", " same ", event_ticker="KXATP-26JUL26-DUP")]),
            _event("KXATP-26JUL26-TBD", markets=[_market("KXATP-26JUL26-TBD-A", "TBD", event_ticker="KXATP-26JUL26-TBD"), _market("KXATP-26JUL26-TBD-B", "Known", event_ticker="KXATP-26JUL26-TBD")]),
            _event("KXATP-26JUL26-NOBOOK", markets=[_market("KXATP-26JUL26-NOBOOK-A", "One", event_ticker="KXATP-26JUL26-NOBOOK", yes_bid_size_fp="0.00"), _market("KXATP-26JUL26-NOBOOK-B", "Two", event_ticker="KXATP-26JUL26-NOBOOK")]),
            _event("KXATP-26JUL26-PARTIAL3", markets=[
                _market("KXATP-26JUL26-PARTIAL3-A", "One", event_ticker="KXATP-26JUL26-PARTIAL3"),
                _market("KXATP-26JUL26-PARTIAL3-B", "Two", event_ticker="KXATP-26JUL26-PARTIAL3"),
                _market("KXATP-26JUL26-PARTIAL3-C", "Three", event_ticker="KXATP-26JUL26-PARTIAL3", yes_ask_size_fp="0.00"),
            ]),
        ]
        off_games = _event("KXATP-26JUL26-FUTURE")
        milestones = [_milestone()]
        milestones.extend(_milestone(str(event["event_ticker"]), identity=f"bad-{index}") for index, event in enumerate(invalid_events))
        milestones.append(_milestone(
            str(off_games["event_ticker"]), identity="not-a-game", kind="future"
        ))
        games, _ = _transport(_Session(_base_pages(events=[good, *invalid_events, off_games], milestones=milestones))).discover_tennis_games(
            now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(tuple(game.event_ticker for game in games), (_EVENT,))

        malformed = _base_pages()
        malformed["/trade-api/v2/events"] = [{"events": [_event(markets=[_market(_EVENT + "-BAD", "Bad", market_type="mystery")])], "cursor": ""}]
        with self.assertRaisesRegex(KalshiShadowCatalogError, "kalshi_catalog_schema_invalid"):
            _transport(_Session(malformed)).discover_tennis_games(
                now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
            )

    def test_timestamp_nanosecond_boundaries_fail_closed(self) -> None:
        """Catches float rounding, bool acceptance, and unsafe wall times."""

        from inci_tennis_io.kalshi_shadow_catalog import _seconds_to_wall_ns

        self.assertEqual(_seconds_to_wall_ns(1.000000001), 1_000_000_001)
        for value in (True, float("nan"), float("inf"), 0.0000000001, -1, 9_223_372_036_854_775_808):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "kalshi_catalog_timestamp_invalid"):
                _seconds_to_wall_ns(value)

    def test_source_boundary_exposes_no_mutation_or_credential_capability(self) -> None:
        """Catches accidental authority expansion in the catalog transport."""

        source_path = Path(__file__).parents[2] / "inci_tennis_io" / "kalshi_shadow_catalog.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_symbols: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_symbols.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported_symbols.add(module)
                imported_symbols.update(
                    f"{module}.{alias.name}" for alias in node.names
                )
        self.assertFalse(
            any(
                part == "kalshi_client"
                for symbol in imported_symbols
                for part in symbol.split(".")
            ),
            imported_symbols,
        )

        request_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "request"
        ]
        self.assertEqual(len(request_calls), 1)
        for call in request_calls:
            self.assertGreaterEqual(len(call.args), 1)
            self.assertIsInstance(call.args[0], ast.Constant)
            self.assertEqual(call.args[0].value, "GET")

        forbidden = {
            "credential", "credentials", "sign", "signing", "signature",
            "portfolio", "order", "orders", "fill", "fills", "position",
            "positions", "websocket", "post", "put", "patch", "delete",
        }
        forbidden_verbs = {"post", "put", "patch", "delete"}
        string_literals = {
            node.value.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertFalse(forbidden_verbs & string_literals)

        executable_identifiers: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                executable_identifiers.append(node.name)
            elif isinstance(node, ast.alias):
                executable_identifiers.extend(
                    (node.name, node.asname or node.name.rsplit(".", 1)[-1])
                )
            elif isinstance(node, ast.arg):
                executable_identifiers.append(node.arg)
            elif isinstance(node, ast.Name):
                executable_identifiers.append(node.id)
            elif isinstance(node, ast.Attribute):
                executable_identifiers.append(node.attr)

        def identifier_words(value: str) -> set[str]:
            return {
                word.casefold()
                for word in re.findall(
                    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+",
                    value,
                )
            }

        violations = {
            word
            for identifier in executable_identifiers
            for word in identifier_words(identifier)
            if word in forbidden
        }
        self.assertEqual(violations, set())

        constants: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[node.targets[0].id] = node.value.value

        def string_expression(node: ast.expr) -> str:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name) and node.id in constants:
                return constants[node.id]
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                return string_expression(node.left) + string_expression(node.right)
            self.fail(f"dynamic catalog path expression: {ast.dump(node)}")

        path_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"_read", "_pages"}
        ]
        allowed_paths = {
            "/trade-api/v2/search/filters_by_sport",
            "/trade-api/v2/series",
            "/trade-api/v2/milestones",
            "/trade-api/v2/events",
        }
        dynamic_path_calls = [
            call
            for call in path_calls
            if isinstance(call.args[0], ast.Name)
            and call.args[0].id == "path"
        ]
        pages_functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_pages"
        ]
        self.assertEqual(len(pages_functions), 1)
        self.assertEqual(dynamic_path_calls, [
            node
            for node in ast.walk(pages_functions[0])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_read"
        ])
        resolved_paths = {
            string_expression(call.args[0])
            for call in path_calls
            if call not in dynamic_path_calls
        }
        self.assertEqual(resolved_paths, allowed_paths)
        self.assertEqual(constants.get("_ORIGIN"), _ORIGIN)
        request_url = request_calls[0].args[1]
        self.assertIsInstance(request_url, ast.BinOp)
        self.assertIsInstance(request_url.op, ast.Add)
        self.assertEqual(ast.dump(request_url.left), ast.dump(ast.Name(id="_ORIGIN")))
        self.assertEqual(ast.dump(request_url.right), ast.dump(ast.Name(id="path")))


if __name__ == "__main__":
    unittest.main()
