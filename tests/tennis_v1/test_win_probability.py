from __future__ import annotations

from dataclasses import replace
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
    WinProbabilityError,
    service_game_win_probability,
    standard_bo3_prematch_probabilities,
    standard_bo3_live_probabilities,
    tiebreak_home_win_probability,
)


def _live_state() -> object:
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
    return state_from_snapshot(snapshot)


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

    def test_impossible_terminal_overruns_are_rejected(self) -> None:
        for points_home, points_away in (
            (8, 0),
            (0, 8),
            (10, 1),
            (1, 10),
            (9, 6),
            (6, 9),
        ):
            with self.subTest(score=(points_home, points_away)):
                with self.assertRaisesRegex(
                    WinProbabilityError,
                    "^tiebreak_score$",
                ):
                    tiebreak_home_win_probability(
                        Decimal("0.5"),
                        Decimal("0.5"),
                        points_home=points_home,
                        points_away=points_away,
                        first_server=PlayerSide.HOME,
                    )

    def test_exact_terminal_and_extended_scores_remain_supported(self) -> None:
        for score, expected in (
            ((7, 0), Decimal("1")),
            ((0, 7), Decimal("0")),
            ((8, 6), Decimal("1")),
            ((6, 8), Decimal("0")),
        ):
            with self.subTest(score=score):
                self.assertEqual(
                    tiebreak_home_win_probability(
                        Decimal("0.5"),
                        Decimal("0.5"),
                        points_home=score[0],
                        points_away=score[1],
                        first_server=PlayerSide.HOME,
                    ),
                    expected,
                )
        live_extended = tiebreak_home_win_probability(
            Decimal("0.5"),
            Decimal("0.5"),
            points_home=8,
            points_away=7,
            first_server=PlayerSide.HOME,
        )
        self.assertGreater(live_extended, Decimal("0"))
        self.assertLess(live_extended, Decimal("1"))


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
        estimate = standard_bo3_live_probabilities(
            _live_state(),
            Decimal("0.5"),
            Decimal("0.5"),
        )

        self.assertEqual(estimate.home_current_set_probability, Decimal("0.5"))
        self.assertEqual(estimate.home_match_probability, Decimal("0.75"))

    def test_impossible_live_states_are_rejected_before_modeling(self) -> None:
        base = _live_state()
        cases = (
            replace(base, games_home=7, games_away=0),
            replace(
                base,
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_points_home=8,
                tiebreak_points_away=0,
                tiebreak_first_server=PlayerSide.HOME,
            ),
            replace(
                base,
                completed_sets=(
                    *base.completed_sets,
                    SetScore(6, 4, None, None),
                ),
            ),
        )

        for state in cases:
            with self.subTest(state=state):
                with self.assertRaisesRegex(
                    WinProbabilityError,
                    "^state_invalid$",
                ):
                    standard_bo3_live_probabilities(
                        state,
                        Decimal("0.5"),
                        Decimal("0.5"),
                    )


if __name__ == "__main__":
    unittest.main()
