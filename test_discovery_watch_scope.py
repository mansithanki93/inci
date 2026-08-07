"""Focused regressions for bounded, provenance-safe sibling watches."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest

from bot import format_discovery_telemetry
from sports_discovery import discover_game_contracts


NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
START_TS = 1785088800.0


def cfg(*, sports=(), tickers=(), cap=10, one_per_event=True,
        prefer_bind=True, sibling_protection=True):
    return SimpleNamespace(
        sports=list(sports),
        tickers=list(tickers),
        max_monitored_markets=cap,
        max_spread=3,
        contracts_per_trade=20,
        prefer_scoreboard_bind=prefer_bind,
        one_contract_per_event=one_per_event,
        sibling_spike_enabled=sibling_protection,
    )


def market(ticker, event_ticker, *, depth, title=None):
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": title or ticker,
        "yes_sub_title": title or ticker,
        "no_sub_title": "No",
        "market_type": "binary",
        "status": "active",
        "notional_value": Decimal(1),
        "close_ts": 1893456000.0,
        "can_close_early": False,
        "yes_bid": Decimal(49),
        "yes_ask": Decimal(50),
        "yes_bid_size": Decimal(depth),
        "yes_ask_size": Decimal(depth),
    }


def event(event_ticker, markets):
    return {
        "event_ticker": event_ticker,
        "series_ticker": "KXATP",
        "category": "Sports",
        "title": "Ada Ace vs Bea Break",
        "markets": tuple(markets),
        "market_skips": {},
    }


def milestone(event_ticker, suffix):
    return {
        "milestone_id": "game-" + suffix,
        "category": "Sports",
        "type": "game",
        "start_ts": START_TS,
        "title": "Ada Ace vs Bea Break",
        "league": "ATP",
        "main_game_event_ticker": event_ticker,
        "primary_event_tickers": (event_ticker,),
        "related_event_tickers": (event_ticker,),
    }


def score_pair(player_a, player_b, *, match_id):
    """Stable scoreboard identities for the two match-winner orientations."""
    athlete_a = "espn:athlete:" + player_a.rsplit("-", 1)[-1]
    athlete_b = "espn:athlete:" + player_b.rsplit("-", 1)[-1]
    return {
        player_a: (match_id, athlete_a, athlete_b),
        player_b: (match_id, athlete_b, athlete_a),
    }


def five_part_score_pair(player_a, player_b, *, match_id):
    """Production binding provenance including both oriented player names."""
    return {
        player_a: (
            match_id, "espn:athlete:ada", "espn:athlete:bea",
            "Ada Ace", "Bea Break"),
        player_b: (
            match_id, "espn:athlete:bea", "espn:athlete:ada",
            "Bea Break", "Ada Ace"),
    }


def resolver(bindings):
    return lambda contract: bindings.get(contract.ticker)


class DynamicClient:
    def __init__(self, event_rows):
        self.event_rows = tuple(event_rows)

    def get_sports_filters(self):
        return {
            "sport_ordering": ("All sports", "Tennis"),
            "sports": {
                "All sports": {
                    "scopes": frozenset(("Games",)),
                    "competitions": {},
                },
                "Tennis": {
                    "scopes": frozenset(("Games",)),
                    "competitions": {
                        "ATP": frozenset(("Games",)),
                    },
                },
            },
        }

    def get_sports_series(self):
        return ({
            "series_ticker": "KXATP",
            "category": "Sports",
            "tags": ("Tennis",),
        },)

    def get_sports_milestones(self, *, competition, minimum_start_date):
        assert competition == "ATP"
        rows = tuple(
            milestone(row["event_ticker"], str(index))
            for index, row in enumerate(self.event_rows)
        )
        return rows, {
            "pages": 1,
            "rows": len(rows),
            "raw_rows": len(rows),
            "market_skips": {},
        }

    def get_open_events(self, *, series_ticker):
        assert series_ticker == "KXATP"
        return self.event_rows, {
            "pages": 1,
            "rows": len(self.event_rows),
            "raw_rows": len(self.event_rows),
            "market_skips": {},
        }


class ExplicitClient(DynamicClient):
    def __init__(self, event_row):
        super().__init__((event_row,))
        self.event_row = event_row
        self.by_ticker = {
            row["ticker"]: row for row in event_row["markets"]
        }

    def get_market(self, ticker):
        return self.by_ticker[ticker]

    def get_event(self, event_ticker, *, with_nested_markets=True):
        assert event_ticker == self.event_row["event_ticker"]
        assert with_nested_markets is True
        return self.event_row

    def get_sports_milestones(
            self, *, related_event_ticker, minimum_start_date):
        rows = (milestone(related_event_ticker, "explicit"),)
        return rows, {
            "pages": 1,
            "rows": 1,
            "raw_rows": 1,
            "market_skips": {},
        }


class DiscoveryWatchScopeTests(unittest.TestCase):
    def test_five_part_score_bindings_are_preserved_and_immutable(self):
        game = "KXATP-26JUL26-FIVE-PART"
        player_a = game + "-A"
        player_b = game + "-B"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(player_b, game, depth=90, title="Bea Break"),
        ))
        bindings = five_part_score_pair(
            player_a, player_b, match_id="espn:five-part")

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=2), DynamicClient((row,)),
            now=NOW, bind_predicate=resolver(bindings))

        self.assertEqual(result.tickers, (player_a,))
        self.assertEqual(result.watch_tickers, (player_b,))
        self.assertEqual(dict(result.score_bindings), bindings)
        with self.assertRaises(TypeError):
            result.score_bindings[player_a] = bindings[player_b]

    def test_five_part_opponents_require_reversed_names(self):
        game = "KXATP-26JUL26-NAME-ORIENTATION"
        player_a = game + "-A"
        player_b = game + "-B"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(player_b, game, depth=90, title="Bea Break"),
        ))
        bindings = five_part_score_pair(
            player_a, player_b, match_id="espn:name-orientation")
        bindings[player_b] = (
            "espn:name-orientation", "espn:athlete:bea",
            "espn:athlete:ada", "Ada Ace", "Bea Break")

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=2), DynamicClient((row,)),
            now=NOW, bind_predicate=resolver(bindings))

        self.assertEqual(result.tickers, ())
        self.assertEqual(result.watch_tickers, ())
        self.assertEqual(result.score_bindings, {})
        self.assertEqual(result.stats["skipped_unverified_opponent"], 1)

    def test_price_feed_collects_provenance_when_binding_features_are_off(self):
        from market_data import PriceFeed

        game = "KXATP-26JUL26-COMMIT"
        ticker = game + "-A"
        row = event(game, (
            market(ticker, game, depth=100, title="Ada Ace"),
        ))
        binding = (
            "espn:commit", "espn:athlete:ada", "espn:athlete:bea",
            "Ada Ace", "Bea Break")

        class Gate:
            def __init__(self):
                self.calls = []

            @staticmethod
            def enabled():
                return True

            def binding_provenance(self, **identity):
                self.calls.append(identity)
                return binding

            def binding_identity(self, **_identity):
                raise AssertionError(
                    "five-part binding_provenance must take precedence")

        gate = Gate()
        feed = PriceFeed(
            cfg(
                sports=("Tennis",), cap=1, prefer_bind=False,
                sibling_protection=False),
            DynamicClient((row,)))

        result = feed.discover(now=NOW, scoreboard_gate=gate)

        self.assertEqual(result.tickers, (ticker,))
        self.assertEqual(result.score_bindings, {ticker: binding})
        self.assertEqual(feed.score_bindings_by_ticker, {ticker: binding})
        self.assertEqual(len(gate.calls), 1)
        with self.assertRaises(TypeError):
            feed.score_bindings_by_ticker[ticker] = binding

    def test_sibling_protection_rejects_multi_trade_event_mode_at_boundary(self):
        game = "KXATP-26JUL26-UNSAFE-MODE"
        rows = (event(game, (
            market(game + "-A", game, depth=100),
            market(game + "-B", game, depth=90),
        )),)
        with self.assertRaisesRegex(
                ValueError, "sibling protection.*one_contract_per_event"):
            discover_game_contracts(
                cfg(
                    sports=("Tennis",), cap=2,
                    one_per_event=False),
                DynamicClient(rows), now=NOW,
                bind_predicate=lambda _contract: True,
            )

    def test_total_quote_cap_keeps_best_ranked_event_packages(self):
        top = "KXATP-26JUL26-TOP"
        next_event = "KXATP-26JUL26-NEXT"
        third = "KXATP-26JUL26-THIRD"
        rows = (
            event(top, (
                market(top + "-A", top, depth=100),
                market(top + "-B", top, depth=90),
            )),
            event(next_event, (
                market(next_event + "-A", next_event, depth=80),
                market(next_event + "-B", next_event, depth=70),
            )),
            event(third, (
                market(third + "-A", third, depth=60),
                market(third + "-B", third, depth=50),
            )),
        )

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=4),
            DynamicClient(rows),
            now=NOW,
            bind_predicate=resolver({
                **score_pair(top + "-A", top + "-B", match_id="espn:top"),
                **score_pair(
                    next_event + "-A", next_event + "-B",
                    match_id="espn:next"),
                **score_pair(
                    third + "-A", third + "-B", match_id="espn:third"),
            }),
        )

        self.assertEqual(result.tickers, (top + "-A", next_event + "-A"))
        self.assertEqual(
            result.watch_tickers, (top + "-B", next_event + "-B"))
        self.assertLessEqual(
            len(result.tickers) + len(result.watch_tickers), 4)

    def test_unbound_third_market_is_not_an_opponent_watch(self):
        game = "KXATP-26JUL26-GAME"
        player_a = game + "-A"
        player_b = game + "-B"
        unrelated = game + "-PROP"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(player_b, game, depth=90, title="Bea Break"),
            market(unrelated, game, depth=80, title="First set has a tiebreak"),
        ))

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=2),
            DynamicClient((row,)),
            now=NOW,
            bind_predicate=resolver(score_pair(
                player_a, player_b, match_id="espn:game")),
        )

        self.assertEqual(result.tickers, (player_a,))
        self.assertEqual(result.watch_tickers, (player_b,))
        self.assertNotIn(unrelated, result.provenance_by_ticker)

    def test_reversed_player_set_prop_cannot_form_trade_watch_pair(self):
        """Opposite players do not prove that two contract outcomes complement."""
        from config import Config
        from espn_prob_gate import EspnProbGate
        from espn_tennis import parse_competition

        game = "KXATP-26JUL26-PROP-PAIR"
        match_winner = game + "-MATCH"
        set_prop = game + "-SET"
        row = event(game, (
            market(
                match_winner, game, depth=90,
                title="Will Ada Ace win the Ace vs Break match?"),
            market(
                set_prop, game, depth=100,
                title="Will Bea Break win the first set?"),
        ))
        card = parse_competition({
            "id": "prop-pair",
            "status": {"type": {"state": "pre", "detail": "Scheduled"}},
            "competitors": [
                {"id": "ada", "athlete": {"displayName": "Ada Ace"}},
                {"id": "bea", "athlete": {"displayName": "Bea Break"}},
            ],
        }, "atp")

        class Cache:
            def matches(self, force=False):
                return (card,)

        gate = EspnProbGate(Config(), cache=Cache())

        def binding(contract):
            return gate.binding_identity(
                ticker=contract.ticker, player_name=contract.title,
                event_title=contract.game_title)

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=2), DynamicClient((row,)),
            now=NOW, bind_predicate=binding)

        self.assertEqual(result.tickers, ())
        self.assertEqual(result.watch_tickers, ())
        self.assertEqual(result.stats["skipped_unverified_opponent"], 1)

    def test_dynamic_protection_skips_event_without_verified_opponent(self):
        game = "KXATP-26JUL26-ONE-SIDED"
        row = event(game, (
            market(game + "-A", game, depth=100, title="Ada Ace"),
        ))

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=2),
            DynamicClient((row,)),
            now=NOW,
            bind_predicate=resolver({
                game + "-A": (
                    "espn:one-sided", "espn:athlete:a", "espn:athlete:b"),
            }),
        )

        self.assertEqual(result.tickers, ())
        self.assertEqual(result.watch_tickers, ())

    def test_explicit_ticker_resolves_sibling_or_fails_on_quote_cap(self):
        game = "KXATP-26JUL26-EXPLICIT"
        player_a = game + "-A"
        player_b = game + "-B"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(player_b, game, depth=90, title="Bea Break"),
        ))
        predicate = resolver(score_pair(
            player_a, player_b, match_id="espn:explicit"))

        result = discover_game_contracts(
            cfg(tickers=(player_a,), cap=2),
            ExplicitClient(row),
            now=NOW,
            bind_predicate=predicate,
        )
        self.assertEqual(result.tickers, (player_a,))
        self.assertEqual(result.watch_tickers, (player_b,))

        with self.assertRaisesRegex(
                ValueError, "sibling protection.*monitoring cap"):
            discover_game_contracts(
                cfg(tickers=(player_a,), cap=1),
                ExplicitClient(row),
                now=NOW,
                bind_predicate=predicate,
            )

    def test_explicit_bound_pair_ignores_unbound_same_event_prop(self):
        game = "KXATP-26JUL26-PAIR"
        player_a = game + "-A"
        player_b = game + "-B"
        unrelated = game + "-PROP"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(player_b, game, depth=90, title="Bea Break"),
            market(unrelated, game, depth=80,
                   title="First set has a tiebreak"),
        ))

        result = discover_game_contracts(
            cfg(tickers=(player_a, player_b), cap=2),
            ExplicitClient(row),
            now=NOW,
            bind_predicate=resolver(score_pair(
                player_a, player_b, match_id="espn:pair")),
        )

        self.assertEqual(result.tickers, (player_a, player_b))
        self.assertEqual(result.watch_tickers, ())
        self.assertNotIn(unrelated, result.provenance_by_ticker)

    def test_boolean_or_same_orientation_bindings_cannot_prove_opponents(self):
        game = "KXATP-26JUL26-NOT-OPPOSITES"
        player_a = game + "-A"
        same_player_prop = game + "-A-PROP"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(
                same_player_prop, game, depth=90,
                title="Ada Ace wins the first set"),
        ))

        for binding in (
                lambda _contract: True,
                resolver({
                    player_a: (
                        "espn:not-opposites", "espn:athlete:a",
                        "espn:athlete:b"),
                    same_player_prop: (
                        "espn:not-opposites", "espn:athlete:a",
                        "espn:athlete:b"),
                })):
            result = discover_game_contracts(
                cfg(sports=("Tennis",), cap=2),
                DynamicClient((row,)), now=NOW,
                bind_predicate=binding,
            )
            self.assertEqual(result.tickers, ())
            self.assertEqual(result.watch_tickers, ())
            self.assertEqual(
                result.stats["skipped_unverified_opponent"], 1)

    def test_bad_third_binding_invalidates_an_otherwise_valid_pair(self):
        game = "KXATP-26JUL26-BAD-THIRD"
        player_a = game + "-A"
        player_b = game + "-B"
        prop = game + "-PROP"
        row = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(player_b, game, depth=90, title="Bea Break"),
            market(prop, game, depth=80, title="Ambiguous prop"),
        ))
        pair = score_pair(player_a, player_b, match_id="espn:bad-third")

        def error_result(_contract):
            raise RuntimeError("score provider failed")

        for label, bad_result in (
                ("boolean-false", False),
                ("boolean", True),
                ("same-athlete", (
                    "espn:bad-third", "espn:athlete:a",
                    "espn:athlete:a")),
                ("resolver-error", error_result)):
            with self.subTest(label=label):
                def binding(contract):
                    if contract.ticker != prop:
                        return pair[contract.ticker]
                    if callable(bad_result):
                        return bad_result(contract)
                    return bad_result

                result = discover_game_contracts(
                    cfg(sports=("Tennis",), cap=2),
                    DynamicClient((row,)), now=NOW,
                    bind_predicate=binding,
                )
                self.assertEqual(result.tickers, ())
                self.assertEqual(result.watch_tickers, ())
                self.assertEqual(
                    result.stats["skipped_unverified_opponent"], 1)

    def test_explicit_one_sided_or_nonopposite_selection_fails_closed(self):
        game = "KXATP-26JUL26-STRICT"
        player_a = game + "-A"
        same_player_prop = game + "-A-PROP"
        one_sided = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
        ))
        with self.assertRaisesRegex(ValueError, "verified opposite"):
            discover_game_contracts(
                cfg(tickers=(player_a,), cap=2),
                ExplicitClient(one_sided), now=NOW,
                bind_predicate=resolver({
                    player_a: (
                        "espn:strict", "espn:athlete:a",
                        "espn:athlete:b"),
                }),
            )

        same_orientation = event(game, (
            market(player_a, game, depth=100, title="Ada Ace"),
            market(
                same_player_prop, game, depth=90,
                title="Ada Ace wins the first set"),
        ))
        with self.assertRaisesRegex(ValueError, "verified opposite"):
            discover_game_contracts(
                cfg(tickers=(player_a, same_player_prop), cap=2),
                ExplicitClient(same_orientation), now=NOW,
                bind_predicate=resolver({
                    player_a: (
                        "espn:strict", "espn:athlete:a",
                        "espn:athlete:b"),
                    same_player_prop: (
                        "espn:strict", "espn:athlete:a",
                        "espn:athlete:b"),
                }),
            )

    def test_valid_packages_are_ranked_before_the_quote_cap(self):
        invalid = "KXATP-26JUL26-INVALID"
        best_valid = "KXATP-26JUL26-BEST-VALID"
        lower_valid = "KXATP-26JUL26-LOWER-VALID"
        rows = (
            event(invalid, (
                market(invalid + "-A", invalid, depth=100),
            )),
            event(best_valid, (
                market(best_valid + "-A", best_valid, depth=80),
                market(best_valid + "-B", best_valid, depth=70),
            )),
            event(lower_valid, (
                market(lower_valid + "-A", lower_valid, depth=60),
                market(lower_valid + "-B", lower_valid, depth=50),
            )),
        )
        bindings = {
            invalid + "-A": (
                "espn:invalid", "espn:athlete:a", "espn:athlete:b"),
            **score_pair(
                best_valid + "-A", best_valid + "-B",
                match_id="espn:best-valid"),
            **score_pair(
                lower_valid + "-A", lower_valid + "-B",
                match_id="espn:lower-valid"),
        }

        result = discover_game_contracts(
            cfg(sports=("Tennis",), cap=2),
            DynamicClient(rows), now=NOW,
            bind_predicate=resolver(bindings),
        )

        self.assertEqual(result.tickers, (best_valid + "-A",))
        self.assertEqual(result.watch_tickers, (best_valid + "-B",))
        self.assertEqual(result.stats["skipped_unverified_opponent"], 1)
        self.assertIn(
            "skipped_unverified_opponent=1",
            format_discovery_telemetry(result),
        )


if __name__ == "__main__":
    unittest.main()
