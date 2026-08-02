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
        self._content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.status_code = status
        self.headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "identity",
            "Content-Length": str(len(self._content)),
        }
        self.close_count = 0

    @property
    def content(self) -> bytes:
        raise AssertionError("catalog transport must stream response bytes")

    def iter_content(self, chunk_size: int) -> object:
        self.chunk_size = chunk_size
        yield self._content

    def close(self) -> None:
        self.close_count += 1


class _StreamingResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        content_length: str | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.status_code = status
        self._chunks = list(chunks)
        self.headers: dict[str, object] = {
            "Content-Type": "application/json",
            "Content-Encoding": "identity",
        }
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.close_error = close_error
        self.close_count = 0
        self.chunk_sizes: list[int] = []

    @property
    def content(self) -> bytes:
        raise AssertionError("catalog transport must stream response bytes")

    def iter_content(self, chunk_size: int) -> object:
        self.chunk_sizes.append(chunk_size)
        yield from self._chunks

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class _Session:
    def __init__(
        self,
        pages: dict[str, list[object]],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self.trust_env = True
        self.pages = {path: list(values) for path, values in pages.items()}
        self.calls: list[dict[str, object]] = []
        self.close_count = 0
        self.close_error = close_error

    def request(self, method: str, url: str, **kwargs: object) -> object:
        path = urlsplit(url).path
        self.calls.append({"method": method, "url": url, **kwargs})
        values = self.pages.get(path)
        if not values:
            raise AssertionError(f"unexpected request path: {path}")
        value = values.pop(0)
        return (
            value
            if isinstance(value, (_Response, _StreamingResponse))
            else _Response(value)
        )

    def close(self) -> None:
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


def _transport(session: _Session):
    from inci_tennis_io.kalshi_shadow_catalog import KalshiShadowCatalogTransport

    with patch("inci_tennis_io.kalshi_shadow_catalog.requests.Session", return_value=session):
        return KalshiShadowCatalogTransport()


class KalshiShadowCatalogTests(unittest.TestCase):
    def test_catalog_session_close_is_idempotent_loud_and_terminal(self) -> None:
        """Catches leaking the owned Requests session or reusing it after shutdown."""

        session = _Session(_base_pages())
        transport = _transport(session)
        transport.close()
        transport.close()
        self.assertEqual(session.close_count, 1)
        with self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_transport_invalid$"
        ):
            transport.get_sports_filters()
        self.assertEqual(session.calls, [])

        failing = _Session(
            _base_pages(), close_error=OSError("private close detail")
        )
        retryable = _transport(failing)
        with self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_session_close_invalid$"
        ):
            retryable.close()
        failing.close_error = None
        retryable.close()
        self.assertEqual(failing.close_count, 2)

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

    def test_unrelated_sport_competition_metadata_cannot_block_tennis_census(self) -> None:
        """Catches malformed non-Tennis filter labels aborting Tennis discovery."""

        pages = _base_pages()
        filters = pages["/trade-api/v2/search/filters_by_sport"][0]
        filters["filters_by_sports"]["Soccer"] = {
            "competitions": {
                "Peru Liga 1 ": {"scopes": ["Games"]},
            },
            "scopes": ["Games"],
        }
        filters["sport_ordering"] = ["All sports", "Soccer", "Tennis"]
        session = _Session(pages)

        snapshot = _transport(session).discover_tennis_catalog(now=_NOW)

        self.assertEqual(
            [game.event_ticker for game in snapshot.games],
            [_EVENT],
        )
        self.assertEqual(
            [
                call["params"]["competition"]
                for call in session.calls
                if urlsplit(str(call["url"])).path.endswith("/milestones")
            ],
            ["ATP Washington"],
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
        """Catches legacy resolver rejection of the wrapper's game value type."""

        from inci_tennis_adapters.shadow_match_chooser import resolve_shadow_matches
        from inci_tennis_adapters.sportradar_trial_v3 import (
            SportradarLiveSummariesSnapshot,
            SportradarScoreSnapshot,
        )

        transport = _transport(_Session(_base_pages()))
        games, digest = transport.discover_tennis_games(now=_NOW)
        provider = SportradarLiveSummariesSnapshot(
            generated_wall_ns=1_785_088_800_000_000_000,
            snapshots=(SportradarScoreSnapshot(
                provider_match_id="sr:sport_event:1",
                generated_wall_ns=1_785_088_800_000_000_000,
                start_wall_ns=1_785_088_800_000_000_000,
                best_of=3,
                home_id="sr:competitor:1",
                home_name="Alice Smith",
                away_id="sr:competitor:2",
                away_name="Bea Jones",
                status="live",
                match_status="live",
                sets_home=0,
                sets_away=0,
                games_home=0,
                games_away=0,
                points_home="0",
                points_away="0",
                serving=None,
                in_tiebreak=False,
                payload_sha256="a" * 64,
            ),),
            payload_sha256="a" * 64,
        )
        resolved = resolve_shadow_matches(
            provider, games, kalshi_catalog_sha256=digest
        )

        self.assertEqual(len(games), 1)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(len(resolved.ready), 1)

    def test_series_pagination_is_exhaustive_and_cursor_failures_abort(self) -> None:
        """Catches silently omitting Tennis Series that appear on later pages."""

        pages = _base_pages()
        pages["/trade-api/v2/series"] = [
            {"series": [{"ticker": "KXSOCCER", "category": "Sports", "tags": ["Soccer"]}], "cursor": "series-next"},
            {"series": [{"ticker": "KXATP", "category": "Sports", "tags": ["Tennis"]}], "cursor": ""},
        ]
        session = _Session(pages)
        snapshot = _transport(session).discover_tennis_catalog(now=_NOW)

        self.assertEqual([game.event_ticker for game in snapshot.games], [_EVENT])
        self.assertEqual(
            [call["params"].get("cursor") for call in session.calls if urlsplit(str(call["url"])).path.endswith("/series")],
            [None, "series-next"],
        )
        loop = _base_pages()
        loop["/trade-api/v2/series"] = [
            {"series": [], "cursor": "again"},
            {"series": [], "cursor": "again"},
        ]
        with self.assertRaisesRegex(Exception, "kalshi_catalog_pagination_invalid"):
            _transport(_Session(loop)).discover_tennis_catalog(now=_NOW)
        duplicate = _base_pages()
        duplicate["/trade-api/v2/series"] = [
            {"series": [{"ticker": "KXATP", "category": "Sports", "tags": ["Tennis"]}], "cursor": "again"},
            {"series": [{"ticker": "KXATP", "category": "Sports", "tags": ["Tennis"]}], "cursor": ""},
        ]
        with self.assertRaisesRegex(Exception, "kalshi_catalog_schema_invalid"):
            _transport(_Session(duplicate)).discover_tennis_catalog(now=_NOW)

    def test_book_state_requires_two_sided_quotes_for_each_market(self) -> None:
        """Catches opposing one-sided markets being mislabeled as a two-sided book."""

        event = _event(markets=[
            _market(_EVENT + "-A", "Alice Smith", yes_ask_size_fp="0.00"),
            _market(_EVENT + "-B", "Bea Jones", yes_bid_size_fp="0.00"),
        ])
        snapshot = _transport(_Session(_base_pages(events=[event]))).discover_tennis_catalog(now=_NOW)

        self.assertEqual(snapshot.games[0].initial_book_state, "one_sided")

    def test_player_identity_text_must_be_durably_storable(self) -> None:
        """Catches selectable games whose original player names cannot be journaled."""

        invalid_names = (
            " Alice Smith",
            "Alice Smith ",
            "Alice\nSmith",
            "A" * 257,
        )
        for name in invalid_names:
            with self.subTest(kind=(len(name), repr(name[:16]))):
                event = _event(
                    markets=[
                        _market(_EVENT + "-A", name),
                        _market(_EVENT + "-B", "Bea Jones"),
                    ]
                )
                snapshot = _transport(
                    _Session(_base_pages(events=[event]))
                ).discover_tennis_catalog(now=_NOW)
                self.assertEqual(snapshot.games, ())
                self.assertEqual(
                    [(row.event_ticker, row.reason) for row in snapshot.excluded],
                    [(_EVENT, "player_identity_invalid")],
                )

    def test_provenance_text_must_be_durably_storable(self) -> None:
        """Catches catalog provenance that passes discovery but fails session persistence."""

        cases: list[dict[str, list[object]]] = []
        bad_id = _milestone(identity=" match-1")
        cases.append(_base_pages(milestones=[bad_id]))
        bad_league = _milestone(league="ATP\nOfficial")
        cases.append(_base_pages(milestones=[bad_league]))
        cases.append(_base_pages(competitions=(" ATP Washington",)))
        cases.append(
            _base_pages(
                series=((" KXATP", ("Tennis",)),),
                events=[_event(series_ticker=" KXATP")],
            )
        )
        for index, pages in enumerate(cases):
            with self.subTest(index=index), self.assertRaisesRegex(
                ValueError, "^kalshi_catalog_schema_invalid$"
            ):
                _transport(_Session(pages)).discover_tennis_catalog(now=_NOW)

    def test_quote_text_uses_the_durable_canonical_decimal_grammar(self) -> None:
        """Catches retained quote strings that the evidence journal must reject."""

        for field, value in (
            ("yes_bid_dollars", "00.4900"),
            ("yes_bid_size_fp", "010.00"),
            ("yes_bid_dollars", "0.٤٩٠٠"),
            ("yes_bid_size_fp", "10.००"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "^kalshi_catalog_schema_invalid$"
            ):
                event = _event(
                    markets=[
                        _market(_EVENT + "-A", "Alice Smith", **{field: value}),
                        _market(_EVENT + "-B", "Bea Jones"),
                    ]
                )
                _transport(
                    _Session(_base_pages(events=[event]))
                ).discover_tennis_catalog(now=_NOW)

    def test_prefixes_do_not_classify_mve_and_explicit_mve_does_not_form_game(self) -> None:
        """Catches ticker-prefix MVE inference or accepting explicit MVE siblings."""

        prefixed = _event("KXMVE-26JUL26-NOT-MVE")
        prefixed_snapshot = _transport(_Session(_base_pages(
            milestones=[_milestone(prefixed["event_ticker"])], events=[prefixed]
        ))).discover_tennis_catalog(now=_NOW)
        self.assertEqual([game.event_ticker for game in prefixed_snapshot.games], ["KXMVE-26JUL26-NOT-MVE"])

        explicit_mve = _event(markets=[
            _market(_EVENT + "-A", "Alice Smith", mve_collection_ticker="mve-collection"),
            _market(_EVENT + "-B", "Bea Jones", mve_collection_ticker="mve-collection"),
        ])
        excluded_snapshot = _transport(_Session(_base_pages(events=[explicit_mve]))).discover_tennis_catalog(now=_NOW)
        self.assertEqual(
            [(row.event_ticker, row.reason) for row in excluded_snapshot.excluded],
            [(_EVENT, "active_binary_sibling_count_invalid")],
        )

    def test_ambiguous_primary_event_tickers_abort_instead_of_partial_census(self) -> None:
        """Catches a current-day game Milestone disappearing when no primary Event is unique."""

        milestone = _milestone()
        milestone["details"]["main_game_event_ticker"] = None
        milestone["primary_event_tickers"] = ["KXATP-26JUL26-A", "KXATP-26JUL26-B"]
        with self.assertRaisesRegex(Exception, "kalshi_catalog_schema_invalid"):
            _transport(_Session(_base_pages(milestones=[milestone]))).discover_tennis_catalog(now=_NOW)

    def test_public_route_family_and_origin_are_exact(self) -> None:
        """Catches an origin change or unapproved metadata route widening."""

        session = _Session(_base_pages())
        _transport(session).discover_tennis_catalog(now=_NOW)

        self.assertEqual(
            [(call["method"], urlsplit(str(call["url"])).path, call["params"]) for call in session.calls],
            [
                ("GET", "/trade-api/v2/search/filters_by_sport", {}),
                ("GET", "/trade-api/v2/series", {"category": "Sports"}),
                ("GET", "/trade-api/v2/milestones", {"category": "Sports", "minimum_start_date": "2026-07-26T00:00:00Z", "competition": "ATP Washington", "limit": 500}),
                ("GET", "/trade-api/v2/events/" + _EVENT, {"with_nested_markets": "true"}),
            ],
        )
        self.assertTrue(all(str(call["url"]).startswith(_ORIGIN + "/trade-api/v2/") for call in session.calls))

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

    def test_catalog_streams_success_without_touching_eager_content(self) -> None:
        """Catches requests buffering the full body before the transport cap."""

        payload = json.dumps(
            _base_pages()["/trade-api/v2/search/filters_by_sport"][0],
            separators=(",", ":"),
        ).encode("utf-8")
        response = _StreamingResponse([payload[:13], payload[13:]])
        session = _Session(
            {"/trade-api/v2/search/filters_by_sport": [response]}
        )

        filters = _transport(session).get_sports_filters()

        self.assertEqual(filters["sport_ordering"], ("All sports", "Tennis"))
        self.assertEqual(session.calls[0]["stream"], True)
        self.assertEqual(response.close_count, 1)
        self.assertEqual(len(response.chunk_sizes), 1)

    def test_catalog_stream_cap_closes_oversize_body_without_content_length(
        self,
    ) -> None:
        """Catches chunked or dishonest upstream bodies exhausting memory."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        response = _StreamingResponse([b"12345", b"6789"])
        session = _Session(
            {"/trade-api/v2/search/filters_by_sport": [response]}
        )
        transport = _transport(session)

        with patch.object(catalog, "_MAXIMUM_BODY_BYTES", 8), self.assertRaisesRegex(
            ValueError,
            "kalshi_catalog_body_too_large",
        ):
            transport.get_sports_filters()

        self.assertEqual(session.calls[0]["stream"], True)
        self.assertEqual(response.close_count, 1)

    def test_catalog_primary_error_wins_and_successful_close_error_halts(
        self,
    ) -> None:
        """Catches response cleanup hiding evidence or failing silently."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        oversized = _StreamingResponse(
            [b"12345", b"6789"],
            close_error=OSError("close failed"),
        )
        with patch.object(catalog, "_MAXIMUM_BODY_BYTES", 8), self.assertRaisesRegex(
            ValueError,
            "kalshi_catalog_body_too_large",
        ):
            _transport(
                _Session(
                    {"/trade-api/v2/search/filters_by_sport": [oversized]}
                )
            ).get_sports_filters()
        self.assertEqual(oversized.close_count, 1)

        payload = json.dumps(
            _base_pages()["/trade-api/v2/search/filters_by_sport"][0],
            separators=(",", ":"),
        ).encode("utf-8")
        valid = _StreamingResponse(
            [payload],
            close_error=OSError("close failed"),
        )
        with self.assertRaisesRegex(ValueError, "kalshi_catalog_close_invalid"):
            _transport(
                _Session({"/trade-api/v2/search/filters_by_sport": [valid]})
            ).get_sports_filters()
        self.assertEqual(valid.close_count, 1)

    def test_public_pacing_and_429_retry_remain_bounded_for_direct_census(self) -> None:
        """Catches unbounded metadata retries or a post-census pacing delay."""

        pages = _base_pages()
        first = pages["/trade-api/v2/search/filters_by_sport"][0]
        first_limit = _Response({}, status=429)
        second_limit = _Response({}, status=429)
        pages["/trade-api/v2/search/filters_by_sport"] = [
            first_limit,
            second_limit,
            first,
        ]
        transport = _transport(_Session(pages))
        sleeps: list[float] = []
        transport._sleep = sleeps.append
        transport._monotonic = lambda: 0.0

        transport.discover_tennis_catalog(now=_NOW)

        self.assertEqual(sleeps, [0.25, 0.5, 0.05, 0.05, 0.05])
        self.assertEqual(
            (first_limit.close_count, second_limit.close_count),
            (1, 1),
        )

    def test_discovery_rejects_an_unbounded_competition_fanout(self) -> None:
        """Catches a widened sport filter launching one pagination walk per competition."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        pages = _base_pages(
            competitions=("ATP Washington", "ATP Washington Qualifying")
        )
        pages["/trade-api/v2/milestones"] = [
            {"milestones": [_milestone()], "cursor": ""},
            {"milestones": [_milestone()], "cursor": ""},
        ]
        with patch.object(
            catalog, "_MAXIMUM_COMPETITIONS", 1, create=True
        ), self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_capacity_exceeded$"
        ):
            _transport(_Session(pages)).discover_tennis_catalog(now=_NOW)

    def test_discovery_caps_aggregate_parsed_rows(self) -> None:
        """Catches individually bounded pages accumulating an unbounded in-memory census."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        with patch.object(
            catalog, "_MAXIMUM_AGGREGATE_ROWS", 1, create=True
        ), self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_capacity_exceeded$"
        ):
            _transport(_Session(_base_pages())).discover_tennis_catalog(now=_NOW)

    def test_discovery_caps_total_http_attempts_across_all_routes(self) -> None:
        """Catches per-route limits composing into an unbounded total request count."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        with patch.object(
            catalog, "_MAXIMUM_DISCOVERY_REQUEST_ATTEMPTS", 3, create=True
        ), self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_capacity_exceeded$"
        ):
            _transport(_Session(_base_pages())).discover_tennis_catalog(now=_NOW)

    def test_discovery_has_one_total_wall_clock_deadline(self) -> None:
        """Catches bounded requests still composing into an unbounded discovery duration."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        with patch.object(
            catalog, "_MAXIMUM_DISCOVERY_SECONDS", 0.0, create=True
        ), self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_capacity_exceeded$"
        ):
            _transport(_Session(_base_pages())).discover_tennis_catalog(now=_NOW)

    def test_discovery_deadline_is_enforced_between_streamed_chunks(self) -> None:
        """Catches a slowly streaming body escaping the aggregate deadline."""

        from inci_tennis_io import kalshi_shadow_catalog as catalog

        response = _StreamingResponse(
            [b'{"filters_by_sports":', b'{"Tennis":{}}}'],
        )
        session = _Session(
            {
                "/trade-api/v2/search/filters_by_sport": [response],
            }
        )
        transport = _transport(session)
        clock = {"value": 0.0}

        def advancing_clock() -> float:
            value = clock["value"]
            clock["value"] += 0.3
            return value

        transport._monotonic = advancing_clock
        with patch.object(
            catalog, "_MAXIMUM_DISCOVERY_SECONDS", 1.0
        ), self.assertRaisesRegex(
            ValueError, "^kalshi_catalog_capacity_exceeded$"
        ):
            transport.discover_tennis_catalog(now=_NOW)
        self.assertEqual(response.close_count, 1)

    def test_invalid_sibling_groups_are_excluded_without_dropping_valid_games(self) -> None:
        """Catches accepting partial, MVE, scalar, or duplicate-player game groups."""

        good = _event()
        invalid = [
            _event("KXATP-26JUL26-ONLY", markets=[_market("KXATP-26JUL26-ONLY-A", "One", event_ticker="KXATP-26JUL26-ONLY")]),
            _event("KXATP-26JUL26-THREE", markets=[_market(f"KXATP-26JUL26-THREE-{index}", f"P{index}", event_ticker="KXATP-26JUL26-THREE") for index in range(3)]),
            _event("KXATP-26JUL26-DUP", markets=[_market("KXATP-26JUL26-DUP-A", "Same", event_ticker="KXATP-26JUL26-DUP"), _market("KXATP-26JUL26-DUP-B", " same ", event_ticker="KXATP-26JUL26-DUP")]),
            _event("KXATP-26JUL26-SCALAR", markets=[_market("KXATP-26JUL26-SCALAR-A", "One", event_ticker="KXATP-26JUL26-SCALAR", market_type="scalar"), _market("KXATP-26JUL26-SCALAR-B", "Two", event_ticker="KXATP-26JUL26-SCALAR", market_type="scalar")]),
        ]
        milestones = [_milestone(str(row["event_ticker"]), identity=f"m-{index}") for index, row in enumerate([good, *invalid])]
        snapshot = _transport(_Session(_base_pages(milestones=milestones, events=[good, *invalid]))).discover_tennis_catalog(now=_NOW)

        self.assertEqual([game.event_ticker for game in snapshot.games], [_EVENT])
        self.assertEqual([row.event_ticker for row in snapshot.excluded], ["KXATP-26JUL26-DUP", "KXATP-26JUL26-ONLY", "KXATP-26JUL26-SCALAR", "KXATP-26JUL26-THREE"])

    def test_forbidden_extra_sibling_cannot_hide_behind_two_valid_markets(self) -> None:
        """Catches filtering a third scalar, MVE, or inactive sibling pre-check."""

        extras = {
            "scalar": {"market_type": "scalar"},
            "mve": {"mve_collection_ticker": "mve-collection"},
            "inactive": {"status": "closed"},
        }
        for label, overrides in extras.items():
            with self.subTest(label=label):
                event = _event(
                    markets=[
                        _market(_EVENT + "-A", "Alice Smith"),
                        _market(_EVENT + "-B", "Bea Jones"),
                        _market(_EVENT + "-SIDE", "Side Product", **overrides),
                    ]
                )
                snapshot = _transport(
                    _Session(_base_pages(events=[event]))
                ).discover_tennis_catalog(now=_NOW)

                self.assertEqual(snapshot.games, ())
                self.assertEqual(
                    [
                        (row.event_ticker, row.reason)
                        for row in snapshot.excluded
                    ],
                    [(_EVENT, "active_binary_sibling_count_invalid")],
                )

    def test_catalog_digest_commits_to_skipped_sibling_counts(self) -> None:
        """Catches scalar/MVE skip counts disappearing from provenance."""

        one_scalar = _event(
            markets=[
                _market(
                    _EVENT + "-SIDE-1",
                    "Side One",
                    market_type="scalar",
                )
            ]
        )
        two_scalars = _event(
            markets=[
                _market(
                    _EVENT + "-SIDE-1",
                    "Side One",
                    market_type="scalar",
                ),
                _market(
                    _EVENT + "-SIDE-2",
                    "Side Two",
                    market_type="scalar",
                ),
            ]
        )
        first = _transport(
            _Session(_base_pages(events=[one_scalar]))
        ).discover_tennis_catalog(now=_NOW)
        second = _transport(
            _Session(_base_pages(events=[two_scalars]))
        ).discover_tennis_catalog(now=_NOW)

        self.assertNotEqual(first.catalog_sha256, second.catalog_sha256)
        self.assertNotEqual(
            first.excluded[0].diagnostics,
            second.excluded[0].diagnostics,
        )

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
