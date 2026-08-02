from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import unittest

from inci_tennis_adapters.sportradar_trial_v3 import (
    SportradarLiveSummariesSnapshot,
    SportradarScoreSnapshot,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
START = 1_800_000_000_000_000_000


def score(
    match_id: str,
    *,
    home: str = "Ada Lovelace",
    away: str = "Grace Hopper",
    start: int = START,
    status: str = "live",
    match_status: str | None = None,
) -> SportradarScoreSnapshot:
    return SportradarScoreSnapshot(
        provider_match_id=match_id,
        generated_wall_ns=START,
        start_wall_ns=start,
        best_of=3,
        home_id="sr:competitor:1",
        home_name=home,
        away_id="sr:competitor:2",
        away_name=away,
        status=status,
        match_status=status if match_status is None else match_status,
        sets_home=0,
        sets_away=0,
        games_home=0,
        games_away=0,
        points_home="0",
        points_away="0",
        serving=None,
        in_tiebreak=False,
        payload_sha256=SHA_A,
    )


def provider(*rows: SportradarScoreSnapshot, digest: str = SHA_A) -> SportradarLiveSummariesSnapshot:
    return SportradarLiveSummariesSnapshot(
        generated_wall_ns=START,
        snapshots=tuple(rows),
        payload_sha256=digest,
    )


class ShadowMatchChooserTests(unittest.TestCase):
    def game(
        self,
        event: str = "KXTENNIS-ADA-GRACE",
        *,
        home: str = "Ada Lovelace",
        away: str = "Grace Hopper",
        start: int = START,
        market_order: tuple[str, str] = ("away", "home"),
    ) -> object:
        from inci_tennis_adapters.shadow_match_chooser import (
            KalshiShadowGame,
            KalshiShadowMarket,
        )

        names = {"home": home, "away": away}
        return KalshiShadowGame(
            event_ticker=event,
            scheduled_start_wall_ns=start,
            game_title="Ada v Grace",
            markets=tuple(
                KalshiShadowMarket(
                    ticker=f"{event}-{side.upper()}", yes_player_name=names[side]
                )
                for side in market_order
            ),
        )

    def resolve(self, rows: tuple[SportradarScoreSnapshot, ...], games: tuple[object, ...]) -> object:
        from inci_tennis_adapters.shadow_match_chooser import resolve_shadow_matches

        return resolve_shadow_matches(provider(*rows), games, kalshi_catalog_sha256=SHA_B)

    def test_exact_pair_resolves_and_market_tickers_follow_provider_home_away(self) -> None:
        """Catches returning the Kalshi catalog market sequence instead of player sides."""
        snapshot = self.resolve((score("sr:sport_event:1"),), (self.game(),))

        self.assertEqual(len(snapshot.ready), 1)
        choice = snapshot.ready[0]
        self.assertEqual(choice.provider_match_id, "sr:sport_event:1")
        self.assertEqual(
            choice.market_tickers,
            ("KXTENNIS-ADA-GRACE-HOME", "KXTENNIS-ADA-GRACE-AWAY"),
        )
        self.assertEqual(snapshot.unavailable, ())

    def test_nfkc_whitespace_casefold_match_but_punctuation_and_accent_changes_do_not(self) -> None:
        """Catches identity normalization that strips punctuation or accents."""
        accepted = self.resolve(
            (score("sr:sport_event:1", home="Ａda   Lovelace", away="GRACE\u2003HOPPER"),),
            (self.game(home="Ada Lovelace", away="grace hopper"),),
        )
        punctuation = self.resolve(
            (score("sr:sport_event:2", home="Ada-Lovelace"),), (self.game(),)
        )
        accent = self.resolve(
            (score("sr:sport_event:3", home="Ada Lovelacé"),), (self.game(),)
        )

        self.assertEqual(len(accepted.ready), 1)
        self.assertEqual(punctuation.ready, ())
        self.assertEqual(accent.ready, ())

    def test_start_window_is_inclusive_at_900_seconds(self) -> None:
        """Catches an exclusive start-time tolerance boundary."""
        at_limit = self.resolve(
            (score("sr:sport_event:1"),),
            (self.game(start=START + 900_000_000_000),),
        )
        beyond_limit = self.resolve(
            (score("sr:sport_event:2"),),
            (self.game(start=START + 900_000_000_001),),
        )

        self.assertEqual(len(at_limit.ready), 1)
        self.assertEqual(beyond_limit.ready, ())

    def test_non_live_provider_rows_are_unavailable(self) -> None:
        """Catches treating lifecycle labels other than exactly live as selectable."""
        snapshot = self.resolve(
            (
                score("sr:sport_event:1", home="One", away="Two", status="not_started"),
                score("sr:sport_event:2", home="Three", away="Four", status="ended"),
                score("sr:sport_event:3", home="Five", away="Six", status="mystery"),
            ),
            (
                self.game("KXTENNIS-1", home="One", away="Two"),
                self.game("KXTENNIS-2", home="Three", away="Four"),
                self.game("KXTENNIS-3", home="Five", away="Six"),
            ),
        )

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(
            {(row.identity, row.reason) for row in snapshot.unavailable},
            {
                ("sr:sport_event:1", "provider_not_live"),
                ("sr:sport_event:2", "provider_not_live"),
                ("sr:sport_event:3", "provider_not_live"),
                ("KXTENNIS-1", "kalshi_unmatched"),
                ("KXTENNIS-2", "kalshi_unmatched"),
                ("KXTENNIS-3", "kalshi_unmatched"),
            },
        )

    def test_malformed_values_and_duplicate_names_fail_closed(self) -> None:
        """Catches validation accepting malformed fields or ambiguous player identity."""
        from inci_tennis_adapters.shadow_match_chooser import (
            KalshiShadowGame,
            KalshiShadowMarket,
            normalize_player_name,
            resolve_shadow_matches,
        )

        with self.assertRaises(ValueError):
            normalize_player_name(" \t\n")
        with self.assertRaises(ValueError):
            normalize_player_name("Ada\x00Lovelace")
        with self.assertRaises(ValueError):
            normalize_player_name("TBD")
        with self.assertRaises(ValueError):
            resolve_shadow_matches(provider(score("sr:sport_event:1"), digest="A" * 64), (), kalshi_catalog_sha256=SHA_B)
        with self.assertRaises(ValueError):
            resolve_shadow_matches(provider(score("sr:sport_event:1")), (), kalshi_catalog_sha256="B" * 64)
        malformed = KalshiShadowGame(
            "lowercase",
            True,
            "title",
            (
                KalshiShadowMarket("KX-A", "Ada"),
                KalshiShadowMarket("KX-B", "Grace"),
            ),
        )
        duplicate_names = KalshiShadowGame(
            "KXTENNIS-DUP",
            START,
            "duplicate",
            (
                KalshiShadowMarket("KXTENNIS-DUP-A", "Ada"),
                KalshiShadowMarket("KXTENNIS-DUP-B", "ada"),
            ),
        )
        result = self.resolve((score("sr:sport_event:1", home="Ada", away="Ada"),), (malformed, duplicate_names))
        self.assertEqual(result.ready, ())
        self.assertEqual({row.reason for row in result.unavailable}, {"provider_invalid", "kalshi_invalid"})

    def test_status_subclass_does_not_coerce_to_live(self) -> None:
        """Catches accepting a non-exact status value through string equality."""
        class LiveText(str):
            pass

        row = replace(score("sr:sport_event:1"), status=LiveText("live"))
        snapshot = self.resolve((row,), (self.game(),))

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(snapshot.unavailable[1].reason, "provider_invalid")

    def test_duplicate_ids_tickers_pairs_and_graph_ambiguity_never_pick_a_winner(self) -> None:
        """Catches selecting arbitrary edges in ambiguous matching graphs."""
        duplicate_provider = self.resolve(
            (score("sr:sport_event:1"), score("sr:sport_event:1")), (self.game(),)
        )
        duplicate_event = self.resolve(
            (score("sr:sport_event:2"),),
            (self.game("KXTENNIS-DUP"), self.game("KXTENNIS-DUP")),
        )
        two_to_one = self.resolve(
            (score("sr:sport_event:3"), score("sr:sport_event:4")), (self.game(),)
        )
        duplicate_pair = self.resolve(
            (score("sr:sport_event:5"),),
            (self.game("KXTENNIS-PAIR-1"), self.game("KXTENNIS-PAIR-2")),
        )

        for snapshot in (duplicate_provider, duplicate_event, two_to_one, duplicate_pair):
            self.assertEqual(snapshot.ready, ())
            self.assertTrue(snapshot.unavailable)

    def test_non_live_duplicate_provider_id_blocks_the_live_counterpart(self) -> None:
        """Catches a lifecycle-invalid duplicate ID being ignored during selection."""
        snapshot = self.resolve(
            (
                score("sr:sport_event:1"),
                score("sr:sport_event:1", status="not_started"),
            ),
            (self.game(),),
        )

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(
            [(row.source, row.identity, row.reason) for row in snapshot.unavailable],
            [
                ("kalshi", "KXTENNIS-ADA-GRACE", "kalshi_unmatched"),
                ("provider", "sr:sport_event:1", "provider_duplicate_id"),
                ("provider", "sr:sport_event:1", "provider_duplicate_id"),
            ],
        )

    def test_invalid_duplicate_event_ticker_blocks_the_valid_counterpart(self) -> None:
        """Catches an invalid duplicate event being omitted while its twin becomes ready."""
        from inci_tennis_adapters.shadow_match_chooser import KalshiShadowGame

        invalid_duplicate = KalshiShadowGame(
            event_ticker="KXTENNIS-ADA-GRACE",
            scheduled_start_wall_ns=START,
            game_title="invalid duplicate",
            markets=(),
        )
        snapshot = self.resolve(
            (score("sr:sport_event:1"),),
            (self.game(), invalid_duplicate),
        )

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(
            [(row.source, row.identity, row.reason) for row in snapshot.unavailable],
            [
                ("kalshi", "KXTENNIS-ADA-GRACE", "kalshi_duplicate_ticker"),
                ("kalshi", "KXTENNIS-ADA-GRACE", "kalshi_duplicate_ticker"),
                ("provider", "sr:sport_event:1", "provider_unmatched"),
            ],
        )

    def test_malformed_provider_snapshot_element_is_unavailable(self) -> None:
        """Catches an arbitrary snapshot tuple member escaping as AttributeError."""
        from inci_tennis_adapters.shadow_match_chooser import (
            ShadowUnavailableMatch,
            resolve_shadow_matches,
        )

        snapshot = resolve_shadow_matches(
            provider(object()),
            (),
            kalshi_catalog_sha256=SHA_B,
        )

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(
            snapshot.unavailable,
            (
                ShadowUnavailableMatch(
                    source="provider",
                    identity="provider_row_invalid",
                    display_name="provider_row_invalid",
                    reason="provider_invalid",
                ),
            ),
        )

    def test_malformed_provider_row_is_retained_when_a_valid_id_matches_its_old_sentinel(self) -> None:
        """Catches selected public IDs suppressing an unrelated malformed row."""
        from inci_tennis_adapters.shadow_match_chooser import ShadowUnavailableMatch

        snapshot = self.resolve(
            (score("provider_row_invalid"), object()),
            (self.game(),),
        )

        self.assertEqual(len(snapshot.ready), 1)
        self.assertEqual(snapshot.ready[0].provider_match_id, "provider_row_invalid")
        self.assertEqual(
            snapshot.unavailable,
            (
                ShadowUnavailableMatch(
                    source="provider",
                    identity="provider_row_invalid",
                    display_name="provider_row_invalid",
                    reason="provider_invalid",
                ),
            ),
        )

    def test_unpaired_surrogate_player_name_is_rejected_as_value_error(self) -> None:
        """Catches malformed Unicode leaking a raw encoding exception."""
        from inci_tennis_adapters.shadow_match_chooser import normalize_player_name

        with self.assertRaises(ValueError) as raised:
            normalize_player_name("Ada\ud800")

        self.assertIs(type(raised.exception), ValueError)
        self.assertEqual(str(raised.exception), "shadow_player_name_invalid")

    def test_unpaired_surrogate_game_title_is_unavailable(self) -> None:
        """Catches malformed Unicode in a display title escaping the resolver."""
        from inci_tennis_adapters.shadow_match_chooser import KalshiShadowGame

        malformed = KalshiShadowGame(
            event_ticker="KXTENNIS-SURROGATE",
            scheduled_start_wall_ns=START,
            game_title="bad\ud800title",
            markets=self.game("KXTENNIS-SURROGATE").markets,
        )
        snapshot = self.resolve((score("sr:sport_event:1"),), (malformed,))

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(snapshot.unavailable[0].reason, "kalshi_invalid")

    def test_unmatched_rows_have_stable_unavailable_reasons(self) -> None:
        """Catches silently dropping unmatched live provider or Kalshi rows."""
        snapshot = self.resolve(
            (score("sr:sport_event:1", home="Ada", away="Grace"),),
            (self.game("KXTENNIS-OTHER", home="Marie", away="Katherine"),),
        )

        self.assertEqual(snapshot.ready, ())
        self.assertEqual(
            {(row.source, row.identity, row.reason) for row in snapshot.unavailable},
            {
                ("provider", "sr:sport_event:1", "provider_unmatched"),
                ("kalshi", "KXTENNIS-OTHER", "kalshi_unmatched"),
            },
        )

    def test_public_projection_is_sorted_stable_and_hashed_across_input_permutations(self) -> None:
        """Catches input sequence leaking into chooser output or digest."""
        rows = (
            score(
                "sr:sport_event:2",
                home="Marie Curie",
                away="Katherine Johnson",
                start=START + 1,
            ),
            score("sr:sport_event:1", start=START),
        )
        games = (
            self.game(
                "KXTENNIS-2",
                home="Marie Curie",
                away="Katherine Johnson",
                start=START + 1,
            ),
            self.game("KXTENNIS-1", start=START),
        )
        first = self.resolve(rows, games)
        second = self.resolve(tuple(reversed(rows)), tuple(reversed(games)))

        self.assertEqual(first, second)
        self.assertEqual(
            [choice.provider_match_id for choice in first.ready],
            ["sr:sport_event:1", "sr:sport_event:2"],
        )
        expected_projection = {
            "kalshi_catalog_sha256": SHA_B,
            "provider_payload_sha256": SHA_A,
            "ready": [
                {
                    "away_player_name": "Grace Hopper",
                    "event_ticker": "KXTENNIS-1",
                    "home_player_name": "Ada Lovelace",
                    "market_tickers": ["KXTENNIS-1-HOME", "KXTENNIS-1-AWAY"],
                    "provider_match_id": "sr:sport_event:1",
                    "provider_start_wall_ns": START,
                },
                {
                    "away_player_name": "Katherine Johnson",
                    "event_ticker": "KXTENNIS-2",
                    "home_player_name": "Marie Curie",
                    "market_tickers": ["KXTENNIS-2-HOME", "KXTENNIS-2-AWAY"],
                    "provider_match_id": "sr:sport_event:2",
                    "provider_start_wall_ns": START + 1,
                },
            ],
            "unavailable": [],
        }
        self.assertEqual(
            first.resolver_snapshot_sha256,
            hashlib.sha256(
                json.dumps(
                    expected_projection,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        )

    def test_public_dataclasses_are_frozen(self) -> None:
        """Catches mutable chooser snapshots escaping the pure resolver boundary."""
        snapshot = self.resolve((score("sr:sport_event:1"),), (self.game(),))
        with self.assertRaises(FrozenInstanceError):
            snapshot.ready = ()  # type: ignore[misc]

    def test_source_imports_are_limited_to_the_pure_resolver_boundary(self) -> None:
        """Catches adding transport, storage, environment, or execution dependencies."""
        source = Path(__file__).parents[2] / "inci_tennis_adapters" / "shadow_match_chooser.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertTrue(imported.isdisjoint({"os", "pathlib", "socket", "subprocess", "urllib", "requests"}))


class HybridShadowMatchChooserTests(unittest.TestCase):
    """The Kalshi census is authoritative: provider evidence only annotates it."""

    def game(
        self,
        event: str,
        *,
        home: str = "Ada Lovelace",
        away: str = "Grace Hopper",
        start: int = START,
        series: str = "KXATP",
    ) -> object:
        from inci_tennis_adapters.shadow_discovery_contracts import (
            KalshiCompetitionProvenance,
            KalshiShadowGame,
            KalshiShadowMarket,
        )

        return KalshiShadowGame(
            provenance=KalshiCompetitionProvenance(
                sport="Tennis",
                scope="Games",
                queried_competitions=("ATP Washington",),
                series_ticker=series,
                milestone_id=f"milestone-{event}",
                milestone_league="ATP Washington",
            ),
            event_ticker=event,
            scheduled_start_wall_ns=start,
            game_title=f"{home} vs {away}",
            markets=(
                KalshiShadowMarket(f"{event}-A", home),
                KalshiShadowMarket(f"{event}-B", away),
            ),
            initial_book_state="two_sided",
        )

    def catalog(self, *games: object, digest: str = SHA_B) -> object:
        from inci_tennis_adapters.shadow_discovery_contracts import (
            KalshiShadowCatalogSnapshot,
        )

        return KalshiShadowCatalogSnapshot(
            games=tuple(games), excluded=(), catalog_sha256=digest
        )

    def competition(
        self,
        *,
        category: str = "sr:category:3",
        competition_type: str = "singles",
    ) -> object:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            SportradarCompetitionProvenance,
        )

        return SportradarCompetitionProvenance(
            sport_id="sr:sport:5",
            sport_name="Tennis",
            category_id=category,
            category_name="Tour",
            competition_id="sr:competition:1",
            competition_name="Washington",
            competition_type=competition_type,
            gender="men",
            level="professional",
        )

    def discovery(
        self,
        *rows: SportradarScoreSnapshot,
        digest: str = SHA_A,
        category: str = "sr:category:3",
        competition_type: str = "singles",
    ) -> object:
        from inci_tennis_adapters.sportradar_trial_v3 import (
            SportradarHybridDiscoverySnapshot,
            SportradarHybridMatch,
        )

        provenance = self.competition(
            category=category, competition_type=competition_type
        )
        return SportradarHybridDiscoverySnapshot(
            generated_wall_ns=START,
            matches=tuple(
                SportradarHybridMatch(score=row, competition=provenance)
                for row in rows
            ),
            diagnostics=(),
            payload_sha256=digest,
        )

    def resolve(
        self,
        catalog: object,
        discovery: object | None = None,
        *,
        provider_state: object | None = None,
    ) -> object:
        from inci_tennis_adapters.shadow_match_chooser import (
            resolve_hybrid_shadow_matches,
        )

        return resolve_hybrid_shadow_matches(
            catalog, discovery, provider_state=provider_state
        )

    def provider_state(self, state: str, reason: str, digest: str | None = None) -> object:
        from inci_tennis_adapters.shadow_discovery_contracts import (
            ProviderDiscoveryState,
        )

        return ProviderDiscoveryState(state, reason, digest)

    def test_every_kalshi_game_has_exactly_one_state(self) -> None:
        """Catches provider-centric output that drops unmatched catalog games."""
        catalog = self.catalog(
            self.game("KXATP-ONE"),
            self.game("KXATP-TWO", home="Marie Curie", away="Katherine Johnson"),
            self.game("KXATP-THREE", home="Serena Williams", away="Venus Williams"),
        )
        result = self.resolve(catalog, self.discovery(score("sr:1")))

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        self.assertEqual(
            [row.status for row in result.rows],
            [HybridStatus.VERIFIED, HybridStatus.PRICE_ONLY, HybridStatus.PRICE_ONLY],
        )
        self.assertTrue(all(row.selectable for row in result.rows))
        self.assertEqual(len({row.game.event_ticker for row in result.rows}), 3)

    def test_exact_reversed_normalized_pair_verifies_at_inclusive_900_seconds(self) -> None:
        """Catches order-sensitive matching or an exclusive start tolerance."""
        game = self.game("KXATP-ONE", start=START + 900_000_000_000)
        match = score(
            "sr:1",
            home="GRACE\u2003HOPPER",
            away="Ａda   Lovelace",
            start=START,
        )
        row = self.resolve(self.catalog(game), self.discovery(match)).rows[0]

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        self.assertEqual(row.status, HybridStatus.VERIFIED)
        self.assertEqual(row.provider_match.home_player_name, "GRACE\u2003HOPPER")
        self.assertEqual(row.diagnostics, ())

    def test_901_seconds_or_prestart_provider_is_price_only(self) -> None:
        """Catches accepting a distant match or treating scheduled score data as live."""
        distant = self.resolve(
            self.catalog(self.game("KXATP-DISTANT", start=START + 900_000_000_001)),
            self.discovery(score("sr:1")),
        ).rows[0]
        prestart = self.resolve(
            self.catalog(self.game("KXATP-PRESTART", start=START + 1)),
            self.discovery(score("sr:2", status="not_started")),
        ).rows[0]

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        self.assertEqual((distant.status, distant.provider_match), (HybridStatus.PRICE_ONLY, None))
        self.assertEqual((prestart.status, prestart.provider_match), (HybridStatus.PRICE_ONLY, None))

    def test_ambiguous_or_terminal_disagreement_is_conflict_and_not_selectable(self) -> None:
        """Catches arbitrary winner selection when provider evidence contradicts itself."""
        catalog = self.catalog(self.game("KXATP-ONE"))
        ambiguous = self.resolve(
            catalog, self.discovery(score("sr:1"), score("sr:2"))
        ).rows[0]
        terminal = self.resolve(
            catalog,
            self.discovery(
                score("sr:1"), score("sr:2", status="ended", match_status="ended")
            ),
        ).rows[0]

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        for row in (ambiguous, terminal):
            self.assertEqual(row.status, HybridStatus.CONFLICT)
            self.assertFalse(row.selectable)
            self.assertIsNone(row.provider_match)
            self.assertEqual(row.diagnostics, tuple(sorted(row.diagnostics)))

    def test_duplicate_ids_pairs_and_catalog_identity_are_conflicts(self) -> None:
        """Catches duplicate stable identity or graph nodes silently producing verification."""
        duplicate_id = self.resolve(
            self.catalog(self.game("KXATP-ONE")),
            self.discovery(score("sr:duplicate"), score("sr:duplicate")),
        ).rows[0]
        duplicate_pair = self.resolve(
            self.catalog(self.game("KXATP-ONE"), self.game("KXATP-TWO")),
            self.discovery(score("sr:1")),
        )
        duplicate_ticker = self.resolve(
            self.catalog(self.game("KXATP-DUP"), self.game("KXATP-DUP")),
            self.discovery(score("sr:1")),
        )

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        self.assertEqual(duplicate_id.status, HybridStatus.CONFLICT)
        self.assertEqual([row.status for row in duplicate_pair.rows], [HybridStatus.CONFLICT] * 2)
        self.assertEqual([row.status for row in duplicate_ticker.rows], [HybridStatus.CONFLICT] * 2)

    def test_provider_absence_empty_stale_or_error_remain_selectable_price_only(self) -> None:
        """Catches provider availability failures removing an otherwise valid Kalshi game."""
        catalog = self.catalog(self.game("KXATP-ONE"))
        cases = (
            self.resolve(catalog),
            self.resolve(catalog, self.discovery()),
            self.resolve(
                catalog,
                self.discovery(score("sr:1")),
                provider_state=self.provider_state("stale", "provider_response_stale", SHA_A),
            ),
            self.resolve(
                catalog,
                None,
                provider_state=self.provider_state("error", "provider_transport_error"),
            ),
        )

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        for result in cases:
            row = result.rows[0]
            self.assertEqual((row.status, row.selectable, row.provider_match), (HybridStatus.PRICE_ONLY, True, None))

    def test_coverage_denied_or_default_deny_cannot_verify(self) -> None:
        """Catches a provider route becoming trusted through a wildcard or guessed tour."""
        catalog = self.catalog(self.game("KXATP-ONE"))
        denied = self.resolve(
            catalog,
            self.discovery(score("sr:1"), category="sr:category:785"),
        ).rows[0]
        unknown = self.resolve(
            catalog,
            self.discovery(score("sr:1"), category="sr:category:999"),
        ).rows[0]

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        self.assertEqual((denied.status, denied.selectable), (HybridStatus.PRICE_ONLY, True))
        self.assertEqual((unknown.status, unknown.selectable), (HybridStatus.PRICE_ONLY, True))

    def test_snapshot_is_sorted_hashed_and_provider_only_rows_never_create_choices(self) -> None:
        """Catches input-order leakage or a provider row becoming a catalog choice."""
        games = (
            self.game("KXATP-LATE", start=START + 1, home="Marie Curie", away="Katherine Johnson"),
            self.game("KXATP-EARLY", start=START),
        )
        rows = (
            score("sr:late", home="Katherine Johnson", away="Marie Curie", start=START + 1),
            score("sr:early"),
            score("sr:provider-only", home="One", away="Two"),
        )
        first = self.resolve(self.catalog(*games), self.discovery(*rows))
        second = self.resolve(self.catalog(*reversed(games)), self.discovery(*reversed(rows)))

        self.assertEqual(first, second)
        self.assertEqual([row.game.event_ticker for row in first.rows], ["KXATP-EARLY", "KXATP-LATE"])
        self.assertEqual(len(first.rows), 2)
        self.assertRegex(first.resolver_snapshot_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(first.resolver_version, "kalshi-first-hybrid-v1")


if __name__ == "__main__":
    unittest.main()
