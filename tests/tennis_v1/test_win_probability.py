from __future__ import annotations

from decimal import Decimal, localcontext
import unittest

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderSnapshot,
    ScoreValue,
    SetScore,
    TerminationKind,
)
from inci_tennis_expert.tennis_score import state_from_snapshot
from inci_tennis_expert.win_probability import (
    service_game_win_probability,
    standard_bo3_prematch_probabilities,
    standard_bo3_live_probabilities,
    tiebreak_home_win_probability,
)


class ServiceGameProbabilityTests(unittest.TestCase):
    def test_deuce_probability_uses_two_point_absorption(self) -> None:
        with localcontext() as context:
            context.prec = 50
            expected = Decimal("0.36") / Decimal("0.52")

        actual = service_game_win_probability(
            Decimal("0.60"),
            server_points=3,
            receiver_points=3,
        )

        self.assertEqual(actual, expected)


class TiebreakProbabilityTests(unittest.TestCase):
    def test_even_point_strength_is_symmetric_from_tiebreak_start(self) -> None:
        actual = tiebreak_home_win_probability(
            Decimal("0.5"),
            Decimal("0.5"),
            points_home=0,
            points_away=0,
            first_server=PlayerSide.HOME,
        )

        self.assertEqual(actual, Decimal("0.5"))


class PrematchProbabilityTests(unittest.TestCase):
    def test_equal_point_strength_produces_coherent_bo3_probabilities(self) -> None:
        estimate = standard_bo3_prematch_probabilities(
            Decimal("0.5"),
            Decimal("0.5"),
            first_server=None,
        )

        self.assertEqual(estimate.home_set1_probability, Decimal("0.5"))
        self.assertEqual(estimate.home_set2_probability, Decimal("0.5"))
        self.assertEqual(estimate.deciding_set_reach_probability, Decimal("0.5"))
        self.assertEqual(
            estimate.home_set3_probability_given_reached,
            Decimal("0.5"),
        )
        self.assertEqual(estimate.home_match_probability, Decimal("0.5"))


class LiveProbabilityTests(unittest.TestCase):
    def test_first_set_winner_has_three_quarter_match_probability_at_even_skill(
        self,
    ) -> None:
        snapshot = ProviderSnapshot(
            provider_source_id="provider-a",
            revision_domain_id="revision-a",
            source_lineage_sha256="a" * 64,
            provider_event_id="snapshot-1",
            provider_match_id="match-a",
            home_player_id="home-a",
            away_player_id="away-a",
            scheduled_start_wall_ns=1_000,
            match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
            status=MatchStatus.LIVE,
            termination_kind=TerminationKind.NONE,
            winner=None,
            retired_side=None,
            completed_sets=(SetScore(6, 4, None, None),),
            games_home=0,
            games_away=0,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            in_tiebreak=False,
            tiebreak_points_home=0,
            tiebreak_points_away=0,
            tiebreak_first_server=None,
            server_for_next_point=PlayerSide.HOME,
            correction_epoch=0,
            revision=1,
            source_wall_ns=2_000,
            source_generated_wall_ns=1_900,
            received_monotonic_ns=3_000,
            clock_uncertainty_ns=7,
            snapshot_complete=True,
        )

        estimate = standard_bo3_live_probabilities(
            state_from_snapshot(snapshot),
            Decimal("0.5"),
            Decimal("0.5"),
        )

        self.assertEqual(estimate.home_current_set_probability, Decimal("0.5"))
        self.assertEqual(estimate.home_match_probability, Decimal("0.75"))


if __name__ == "__main__":
    unittest.main()
