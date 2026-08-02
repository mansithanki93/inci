from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest


def _game(
    *,
    series_ticker: str = "KXATP",
    sport: str = "Tennis",
    scope: str = "Games",
    milestone_type: str = "tennis_tournament_singles",
):
    from inci_tennis_adapters.shadow_discovery_contracts import (
        KalshiCompetitionProvenance,
        KalshiShadowGame,
        KalshiShadowMarket,
    )

    return KalshiShadowGame(
        provenance=KalshiCompetitionProvenance(
            sport=sport,
            scope=scope,
            milestone_type=milestone_type,
            queried_competitions=("ATP Washington",),
            series_ticker=series_ticker,
            milestone_id="match-1",
            milestone_league="ATP Washington",
        ),
        event_ticker="KXATP-26AUG01-ONE",
        scheduled_start_wall_ns=1_785_607_200_000_000_000,
        game_title="Alice Smith vs Bea Jones",
        markets=(
            KalshiShadowMarket("KXATP-26AUG01-ONE-A", "Alice Smith"),
            KalshiShadowMarket("KXATP-26AUG01-ONE-B", "Bea Jones"),
        ),
        initial_book_state="two_sided",
    )


def _provider(
    *,
    category_id: str,
    competition_type: str,
    sport_id: str = "sr:sport:5",
    sport_name: str = "Tennis",
):
    from inci_tennis_adapters.sportradar_trial_v3 import (
        SportradarCompetitionProvenance,
    )

    return SportradarCompetitionProvenance(
        sport_id=sport_id,
        sport_name=sport_name,
        category_id=category_id,
        category_name="Tour",
        competition_id="sr:competition:1",
        competition_name="Washington",
        competition_type=competition_type,
        gender="men",
        level="professional",
    )


class ShadowProviderCoverageTests(unittest.TestCase):
    def test_registry_is_exact_default_deny_and_observation_only(self) -> None:
        """Catches wildcard coverage or accidental execution authorization."""

        from inci_tennis_adapters.shadow_provider_coverage import (
            assess_provider_route,
        )

        atp = _provider(category_id="sr:category:3", competition_type="singles")
        self.assertEqual(assess_provider_route(_game(), atp).state, "supported")
        self.assertEqual(
            assess_provider_route(_game(series_ticker="KXATP-EXTRA"), atp).state,
            "unclassified",
        )
        self.assertEqual(
            assess_provider_route(
                _game(series_ticker="KXITFMATCH"),
                _provider(
                    category_id="sr:category:785",
                    competition_type="singles",
                ),
            ).state,
            "unsupported",
        )
        self.assertFalse(assess_provider_route(_game(), atp).execution_authorized)
        self.assertEqual(
            assess_provider_route(_game(), atp).authority_scope,
            "observation_only",
        )
        doubles = assess_provider_route(
            _game(milestone_type="tennis_tournament_doubles"),
            atp,
        )
        self.assertEqual(doubles.state, "unclassified")
        self.assertEqual(
            doubles.reason,
            "kalshi_milestone_type_price_only",
        )

    def test_registry_routes_reviewed_and_denied_categories_without_wildcards(self) -> None:
        """Catches a guessed tour/type route becoming eligible for verification."""

        from inci_tennis_adapters.shadow_provider_coverage import (
            assess_provider_route,
        )

        cases = (
            ("KXATP", "sr:category:3", "singles", "supported", "ATP"),
            ("KXWTA", "sr:category:6", "singles", "supported", "WTA"),
            ("KXCHALLENGER", "sr:category:72", "singles", "supported", "Challenger"),
            ("KXWTA125", "sr:category:871", "singles", "supported", "WTA 125K"),
            ("KXITFMATCH", "sr:category:785", "singles", "unsupported", "ITF Men"),
            ("KXITFWMATCH", "sr:category:213", "singles", "unsupported", "ITF Women"),
            ("KXEXHIBITION", "sr:category:79", "singles", "unsupported", "Exhibition"),
            ("KXATP", "sr:category:3", "doubles", "unclassified", None),
            ("KXATP", "sr:category:999", "singles", "unclassified", None),
        )
        for series, category, competition_type, state, tour in cases:
            with self.subTest(series=series, category=category, type=competition_type):
                assessment = assess_provider_route(
                    _game(series_ticker=series),
                    _provider(
                        category_id=category,
                        competition_type=competition_type,
                    ),
                )
                self.assertEqual(
                    (assessment.state, assessment.canonical_tour),
                    (state, tour),
                )

    def test_registry_rejects_contradictory_kalshi_provenance(self) -> None:
        """Catches structured Kalshi provenance being ignored for a known route."""

        from inci_tennis_adapters.shadow_provider_coverage import (
            assess_provider_route,
        )

        assessment = assess_provider_route(
            _game(sport="Soccer"),
            _provider(category_id="sr:category:3", competition_type="singles"),
        )
        self.assertEqual(
            (assessment.state, assessment.reason, assessment.canonical_tour),
            ("unclassified", "kalshi_provenance_unclassified", None),
        )

    def test_registry_rejects_non_tennis_provider_identity(self) -> None:
        """Catches category/type metadata bypassing provider sport identity."""

        from inci_tennis_adapters.shadow_provider_coverage import (
            assess_provider_route,
        )

        for sport_id, sport_name in (
            ("sr:sport:999", "Tennis"),
            ("sr:sport:5", "Soccer"),
        ):
            with self.subTest(sport_id=sport_id, sport_name=sport_name):
                assessment = assess_provider_route(
                    _game(),
                    _provider(
                        category_id="sr:category:3",
                        competition_type="singles",
                        sport_id=sport_id,
                        sport_name=sport_name,
                    ),
                )

                self.assertEqual(
                    (
                        assessment.state,
                        assessment.reason,
                        assessment.canonical_tour,
                    ),
                    ("unclassified", "provider_sport_unclassified", None),
                )

    def test_registry_digest_is_stable_for_rule_table_permutations(self) -> None:
        """Catches a semantically identical rule table changing its audit digest."""

        from inci_tennis_adapters.shadow_provider_coverage import (
            coverage_registry_sha256,
            coverage_registry_sha256_for_tables,
        )

        supported = (
            ("KXWTA", "sr:category:6", "singles", "WTA"),
            ("KXATP", "sr:category:3", "singles", "ATP"),
        )
        unsupported = (
            ("KXITFWMATCH", "sr:category:213", "singles", "ITF Women"),
            ("KXITFMATCH", "sr:category:785", "singles", "ITF Men"),
        )
        self.assertEqual(
            coverage_registry_sha256_for_tables(supported, unsupported),
            coverage_registry_sha256_for_tables(
                tuple(reversed(supported)), tuple(reversed(unsupported))
            ),
        )
        self.assertRegex(coverage_registry_sha256(), r"^[0-9a-f]{64}$")
        self.assertEqual(
            coverage_registry_sha256(),
            "bad294478bcfd0150e961ceaa2539b435aa1a4a72de63f29430abd1539aed81d",
        )

    def test_discovery_contracts_are_frozen_value_objects(self) -> None:
        """Catches a mutable discovery snapshot changing after it is hashed."""

        from inci_tennis_adapters.shadow_discovery_contracts import HybridStatus

        game = _game()
        with self.assertRaises(FrozenInstanceError):
            game.event_ticker = "KXATP-26AUG01-CHANGED"  # type: ignore[misc]
        self.assertEqual(
            tuple(item.value for item in HybridStatus),
            ("VERIFIED", "PRICE_ONLY", "CONFLICT"),
        )

    def test_competition_provenance_requires_canonical_query_keys(self) -> None:
        """Catches a digestable but ambiguous duplicate or order-dependent query tuple."""

        from inci_tennis_adapters.shadow_discovery_contracts import (
            KalshiCompetitionProvenance,
        )

        for keys in (("WTA", "ATP"), ("ATP", "ATP"), ()):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                KalshiCompetitionProvenance(
                    sport="Tennis",
                    scope="Games",
                    milestone_type="tennis_tournament_singles",
                    queried_competitions=keys,
                    series_ticker="KXATP",
                    milestone_id="match-1",
                    milestone_league="ATP",
                )

    def test_competition_provenance_rejects_unknown_milestone_type(self) -> None:
        """Catches unobserved Kalshi milestone types entering trusted provenance."""

        from inci_tennis_adapters.shadow_discovery_contracts import (
            KalshiCompetitionProvenance,
        )

        with self.assertRaises(ValueError):
            KalshiCompetitionProvenance(
                sport="Tennis",
                scope="Games",
                milestone_type="tennis_future_unknown",
                queried_competitions=("ATP",),
                series_ticker="KXATP",
                milestone_id="match-1",
                milestone_league="ATP",
            )


if __name__ == "__main__":
    unittest.main()
