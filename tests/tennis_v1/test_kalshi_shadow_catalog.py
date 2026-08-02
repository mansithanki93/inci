from __future__ import annotations

import copy
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


_ORIGIN = "https://external-api.kalshi.com"
_NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
_EVENT = "KXATP-26JUL26-ONE"


def _market(
    ticker: str,
    player: str,
    *,
    event_ticker: str = _EVENT,
    **overrides: object,
) -> dict[str, object]:
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
    series_ticker: str = "KXATP",
    title: str = "Alice Smith vs Bea Jones",
    markets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "event_ticker": event_ticker,
        "series_ticker": series_ticker,
        "category": "Sports",
        "title": title,
        "markets": markets
        if markets is not None
        else [
            _market(event_ticker + "-A", "Alice Smith", event_ticker=event_ticker),
            _market(event_ticker + "-B", "Bea Jones", event_ticker=event_ticker),
        ],
    }


def _milestone(
    event_ticker: str = _EVENT,
    *,
    identity: str = "match-1",
    league: str = "ATP Washington",
) -> dict[str, object]:
    return {
        "id": identity,
        "category": "Sports",
        "type": "game",
        "start_date": "2026-07-26T18:00:00Z",
        "title": "Washington match",
        "details": {
            "league": league,
            "main_game_event_ticker": event_ticker,
        },
        "primary_event_tickers": [event_ticker],
        "related_event_tickers": [event_ticker],
    }


def _base_pages(
    *,
    competitions: tuple[str, ...] = ("ATP Washington",),
    series: tuple[tuple[str, tuple[str, ...]], ...] = (("KXATP", ("Tennis",)),),
    milestones: list[dict[str, object]] | None = None,
    events: list[dict[str, object]] | None = None,
) -> dict[str, list[object]]:
    rows = events or [_event()]
    pages: dict[str, list[object]] = {
        "/trade-api/v2/search/filters_by_sport": [{
            "filters_by_sports": {
                "All sports": {"competitions": {}, "scopes": ["Games"]},
                "Tennis": {
                    "competitions": {
                        name: {"scopes": ["Games"]} for name in competitions
                    },
                    "scopes": ["Games"],
                },
            },
            "sport_ordering": ["All sports", "Tennis"],
        }],
        "/trade-api/v2/series": [{
            "series": [
                {"ticker": ticker, "category": "Sports", "tags": list(tags)}
                for ticker, tags in series
            ],
        }],
        "/trade-api/v2/milestones": [{
            "milestones": list(milestones or [_milestone()]), "cursor": ""
        }],
    }
    for row in rows:
        pages["/trade-api/v2/events/" + str(row["event_ticker"])] = [{"event": row}]
    return pages


class _Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.status_code = status
        self.headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "identity",
            "Content-Length": str(len(self.content)),
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
            raise AssertionError(f"unexpected request path: {path}")
        value = values.pop(0)
        return value if isinstance(value, _Response) else _Response(value)


def _transport(session: _Session):
    from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogTransport

    with patch("inci_tennis_io.kalshi_shadow_catalog.requests.Session", return_value=session):
        return KalshiShadowCatalogTransport()


class KalshiShadowCatalogTests(unittest.TestCase):
    def test_empty_and_one_sided_books_remain_in_complete_census(self) -> None:
        """Catches a quote-presence filter silently dropping structurally valid games."""

        empty = _event(
            "KXATP-26JUL26-EMPTY",
            markets=[
                _market("KXATP-26JUL26-EMPTY-A", "One", event_ticker="KXATP-26JUL26-EMPTY", yes_bid_size_fp="0.00", yes_ask_size_fp="0.00"),
                _market("KXATP-26JUL26-EMPTY-B", "Two", event_ticker="KXATP-26JUL26-EMPTY", yes_bid_size_fp="0.00", yes_ask_size_fp="0.00"),
            ],
        )
        one_sided = _event(
            "KXATP-26JUL26-ONE-SIDE",
            markets=[
                _market("KXATP-26JUL26-ONE-SIDE-A", "Three", event_ticker="KXATP-26JUL26-ONE-SIDE", yes_ask_size_fp="0.00"),
                _market("KXATP-26JUL26-ONE-SIDE-B", "Four", event_ticker="KXATP-26JUL26-ONE-SIDE", yes_ask_size_fp="0.00"),
            ],
        )
        snapshot = _transport(_Session(_base_pages(
            milestones=[_milestone(empty["event_ticker"], identity="empty"), _milestone(one_sided["event_ticker"], identity="one-sided")],
            events=[empty, one_sided],
        ))).discover_tennis_catalog(now=_NOW)

        self.assertEqual([row.initial_book_state for row in snapshot.games], ["empty", "one_sided"])
        self.assertEqual(snapshot.games[0].markets[0].initial_yes_bid, None)
        self.assertEqual(snapshot.games[1].markets[0].initial_yes_bid_depth, "10.00")
        self.assertEqual(snapshot.games[1].markets[0].initial_yes_ask, None)
        self.assertEqual(snapshot.games[1].markets[0].initial_yes_ask_depth, None)

    def test_event_series_is_explicit_and_never_inferred_from_ticker_prefix(self) -> None:
        """Catches accepting a tennis-looking ticker without its Event Series evidence."""

        event = _event("KXNOTPREFIX-26JUL26-ONE", series_ticker="KXTENNISGAMES")
        session = _Session(_base_pages(
            series=(("KXTENNISGAMES", ("Tennis",)),),
            milestones=[_milestone(event["event_ticker"])],
            events=[event],
        ))
        snapshot = _transport(session).discover_tennis_catalog(now=_NOW)

        self.assertEqual(snapshot.games[0].provenance.series_ticker, "KXTENNISGAMES")
        self.assertEqual(
            urlsplit(str(session.calls[-1]["url"])).path,
            "/trade-api/v2/events/KXNOTPREFIX-26JUL26-ONE",
        )

    def test_competition_queries_and_milestone_league_are_retained_losslessly(self) -> None:
        """Catches collapsing repeated Milestone discovery provenance to one query key."""

        event = _event()
        repeated = _milestone(event["event_ticker"], league="Official ATP League")
        pages = _base_pages(
            competitions=("ATP Washington", "ATP Washington Qualifying"),
            milestones=[repeated],
            events=[event],
        )
        pages["/trade-api/v2/milestones"] = [
            {"milestones": [repeated], "cursor": ""},
            {"milestones": [repeated], "cursor": ""},
        ]
        session = _Session(pages)
        snapshot = _transport(session).discover_tennis_catalog(now=_NOW)

        provenance = snapshot.games[0].provenance
        self.assertEqual(provenance.queried_competitions, ("ATP Washington", "ATP Washington Qualifying"))
        self.assertEqual(provenance.milestone_league, "Official ATP League")
        self.assertEqual(
            [call["params"]["competition"] for call in session.calls if urlsplit(str(call["url"])).path.endswith("/milestones")],
            ["ATP Washington", "ATP Washington Qualifying"],
        )

    def test_each_rejected_expected_event_has_stable_identity_and_provenance(self) -> None:
        """Catches aggregating away which current-day expected Event was excluded."""

        bad = _event("KXATP-26JUL26-BAD", markets=[
            _market("KXATP-26JUL26-BAD-A", "One", event_ticker="KXATP-26JUL26-BAD"),
        ])
        snapshot = _transport(_Session(_base_pages(
            milestones=[_milestone(bad["event_ticker"], league="ATP Official")], events=[bad]
        ))).discover_tennis_catalog(now=_NOW)

        self.assertEqual(snapshot.games, ())
        self.assertEqual(len(snapshot.excluded), 1)
        excluded = snapshot.excluded[0]
        self.assertEqual((excluded.reason, excluded.event_ticker), ("active_binary_sibling_count_invalid", "KXATP-26JUL26-BAD"))
        self.assertEqual(excluded.provenance.milestone_league, "ATP Official")

    def test_non_dollar_binary_sibling_is_a_stable_structural_exclusion(self) -> None:
        """Catches one non-$1 product aborting the complete census instead of being excluded."""

        event = _event(markets=[
            _market(_EVENT + "-A", "Alice Smith", notional_value_dollars="2.0000"),
            _market(_EVENT + "-B", "Bea Jones"),
        ])
        snapshot = _transport(_Session(_base_pages(events=[event]))).discover_tennis_catalog(now=_NOW)

        self.assertEqual(snapshot.games, ())
        self.assertEqual(
            [(row.event_ticker, row.reason) for row in snapshot.excluded],
            [(_EVENT, "active_binary_sibling_count_invalid")],
        )

    def test_direct_event_fetches_are_exhaustive_and_permutation_stable(self) -> None:
        """Catches old series-wide Event pagination or order-dependent census hashes."""

        events = [_event(f"KXATP-26JUL26-{index}", title=f"Player {index} A vs Player {index} B") for index in range(3)]
        milestones = [_milestone(str(row["event_ticker"]), identity=f"m-{index}") for index, row in enumerate(events)]
        pages = _base_pages(milestones=milestones, events=events)
        pages["/trade-api/v2/milestones"] = [
            {"milestones": milestones[1:], "cursor": "next"},
            {"milestones": milestones[:1], "cursor": ""},
        ]
        session = _Session(copy.deepcopy(pages))
        first = _transport(session).discover_tennis_catalog(now=_NOW)
        second = _transport(_Session(_base_pages(
            milestones=list(reversed(milestones)), events=list(reversed(events))
        ))).discover_tennis_catalog(now=_NOW)

        self.assertEqual(first, second)
        event_calls = [call for call in session.calls if "/events/" in urlsplit(str(call["url"])).path]
        self.assertEqual(len(event_calls), 3)
        self.assertEqual({call["params"].get("with_nested_markets") for call in event_calls}, {"true"})
        self.assertFalse(any(urlsplit(str(call["url"])).path == "/trade-api/v2/events" for call in session.calls))

    def test_compatibility_wrapper_returns_catalog_games_and_digest(self) -> None:
        """Catches legacy consumers receiving a changed return shape."""

        transport = _transport(_Session(_base_pages()))
        games, digest = transport.discover_tennis_games(now=_NOW)

        self.assertEqual(len(games), 1)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_milestone_pagination_is_exhaustive_and_incomplete_pages_abort(self) -> None:
        """Catches a partial current-day census escaping Milestone pagination failure."""

        events = [_event("KXATP-26JUL26-A"), _event("KXATP-26JUL26-B")]
        milestones = [_milestone(str(row["event_ticker"]), identity=str(index)) for index, row in enumerate(events)]
        pages = _base_pages(milestones=milestones, events=events)
        pages["/trade-api/v2/milestones"] = [
            {"milestones": milestones[:1], "cursor": "next"},
            {"milestones": milestones[1:], "cursor": ""},
        ]
        self.assertEqual(len(_transport(_Session(pages)).discover_tennis_catalog(now=_NOW).games), 2)

        for cursor in (7, "next"):
            with self.subTest(cursor=cursor):
                bad_pages = _base_pages()
                bad_pages["/trade-api/v2/milestones"] = [
                    {"milestones": [_milestone()], "cursor": cursor},
                    {"milestones": [], "cursor": cursor},
                ]
                with self.assertRaisesRegex(Exception, r"kalshi_catalog_(schema|pagination)_invalid"):
                    _transport(_Session(bad_pages)).discover_tennis_catalog(now=_NOW)

    def test_series_and_direct_event_boundary_failures_are_sanitized(self) -> None:
        """Catches malformed or private upstream content escaping catalog errors."""

        from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogError

        bad_series = _base_pages()
        bad_series["/trade-api/v2/series"] = [{
            "series": [{"ticker": "KXATP", "category": "Sports", "tags": ["Tennis"]}],
            "cursor": "unexpected",
        }]
        private_event = _base_pages()
        private_event["/trade-api/v2/events/" + _EVENT] = [_Response({"private": "Alice Smith"}, status=503)]
        for pages in (bad_series, private_event):
            with self.subTest(pages=pages is private_event), self.assertRaises(KalshiShadowCatalogError) as caught:
                _transport(_Session(pages)).discover_tennis_catalog(now=_NOW)
            self.assertRegex(str(caught.exception), r"\Akalshi_catalog_[a-z_]+\Z")
            self.assertNotIn("Alice Smith", str(caught.exception))

    def test_public_pacing_and_429_retry_remain_bounded_for_direct_census(self) -> None:
        """Catches unbounded metadata retries or a post-census pacing delay."""

        pages = _base_pages()
        first = pages["/trade-api/v2/search/filters_by_sport"][0]
        pages["/trade-api/v2/search/filters_by_sport"] = [
            _Response({}, status=429), _Response({}, status=429), first,
        ]
        transport = _transport(_Session(pages))
        sleeps: list[float] = []
        transport._sleep = sleeps.append
        transport._monotonic = lambda: 0.0

        transport.discover_tennis_catalog(now=_NOW)

        self.assertEqual(sleeps, [0.25, 0.5, 0.05, 0.05, 0.05])

    def test_invalid_sibling_groups_are_excluded_without_dropping_valid_games(self) -> None:
        """Catches accepting partial, MVE, scalar, or duplicate-player game groups."""

        good = _event()
        invalid = [
            _event("KXATP-26JUL26-ONLY", markets=[_market("KXATP-26JUL26-ONLY-A", "One", event_ticker="KXATP-26JUL26-ONLY")]),
            _event("KXATP-26JUL26-THREE", markets=[_market(f"KXATP-26JUL26-THREE-{index}", f"P{index}", event_ticker="KXATP-26JUL26-THREE") for index in range(3)]),
            _event("KXATP-26JUL26-DUP", markets=[_market("KXATP-26JUL26-DUP-A", "Same", event_ticker="KXATP-26JUL26-DUP"), _market("KXATP-26JUL26-DUP-B", " same ", event_ticker="KXATP-26JUL26-DUP")]),
        ]
        milestones = [_milestone(str(row["event_ticker"]), identity=f"m-{index}") for index, row in enumerate([good, *invalid])]
        snapshot = _transport(_Session(_base_pages(milestones=milestones, events=[good, *invalid]))).discover_tennis_catalog(now=_NOW)

        self.assertEqual([game.event_ticker for game in snapshot.games], [_EVENT])
        self.assertEqual([row.event_ticker for row in snapshot.excluded], ["KXATP-26JUL26-DUP", "KXATP-26JUL26-ONLY", "KXATP-26JUL26-THREE"])

    def test_catalog_hash_changes_for_provenance_books_and_exclusions(self) -> None:
        """Catches an audit digest omitting retained evidence that changes the census."""

        baseline = _transport(_Session(_base_pages())).discover_tennis_catalog(now=_NOW)
        changed_book = _event(markets=[
            _market(_EVENT + "-A", "Alice Smith", yes_bid_dollars="0.4800"),
            _market(_EVENT + "-B", "Bea Jones"),
        ])
        changed = _transport(_Session(_base_pages(events=[changed_book]))).discover_tennis_catalog(now=_NOW)
        excluded = _transport(_Session(_base_pages(events=[_event(markets=[_market(_EVENT + "-A", "Alice Smith")])]))).discover_tennis_catalog(now=_NOW)

        self.assertNotEqual(baseline.catalog_sha256, changed.catalog_sha256)
        self.assertNotEqual(baseline.catalog_sha256, excluded.catalog_sha256)

    def test_timestamp_conversion_rejects_nonrepresentable_wall_times(self) -> None:
        """Catches unsafe timestamp coercion at the catalog boundary."""

        from inci_tennis_io.kalshi_shadow_catalog import _seconds_to_wall_ns

        self.assertEqual(_seconds_to_wall_ns(1.000000001), 1_000_000_001)
        for value in (True, float("nan"), float("inf"), 0.0000000001, -1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "kalshi_catalog_timestamp_invalid"):
                _seconds_to_wall_ns(value)

    def test_source_boundary_remains_public_get_only_without_credentials(self) -> None:
        """Catches catalog transport authority expanding beyond public metadata GETs."""

        source = Path(__file__).parents[2] / "inci_tennis_io" / "kalshi_shadow_catalog.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        request_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "request"
        ]
        self.assertEqual(len(request_calls), 1)
        self.assertEqual(request_calls[0].args[0].value, "GET")
        identifiers = {
            node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        self.assertFalse(identifiers & {"credential", "credentials", "signature", "portfolio", "orders", "fills", "positions"})
if __name__ == "__main__":
    unittest.main()
