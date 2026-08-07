from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal
import sys
import unittest

sys.dont_write_bytecode = True

from inci_tennis_expert.contracts import (
    DecisionReason,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SetScore,
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.prematch_model import PrematchPrior
from inci_tennis_expert.win_probability import (
    live_fair_value,
    live_fair_value_for_side,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def state(**changes: object) -> TennisState:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_match_id": "match-1",
        "home_player_id": "player-home",
        "away_player_id": "player-away",
        "scheduled_start_wall_ns": 100,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "status": MatchStatus.LIVE,
        "termination_kind": TerminationKind.NONE,
        "winner": None,
        "retired_side": None,
        "completed_sets": (),
        "games_home": 0,
        "games_away": 0,
        "points_home": ScoreValue.LOVE,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": PlayerSide.HOME,
        "correction_epoch": 0,
        "revision": 1,
        "snapshot_complete": True,
        "last_provider_event_id": "event-1",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 150,
        "last_source_generated_wall_ns": 149,
        "last_received_monotonic_ns": 90,
        "last_clock_uncertainty_ns": 2,
        "block_reason": None,
        "expected_revision": None,
        "observed_revision": None,
        "blocked_event_semantic_sha256": None,
        "blocked_received_monotonic_ns": None,
    }
    values.update(changes)
    return TennisState(**values)  # type: ignore[arg-type]


def unchecked_state(**changes: object) -> TennisState:
    checked = state()
    value = object.__new__(TennisState)
    for field in fields(TennisState):
        object.__setattr__(
            value,
            field.name,
            changes.get(field.name, getattr(checked, field.name)),
        )
    return value


def prior(**changes: object) -> PrematchPrior:
    values: dict[str, object] = {
        "player_home_id": "player-home",
        "player_away_id": "player-away",
        "surface": "hard",
        "scheduled_start_wall_ns": 100,
        "home_serve_point_probability": Decimal("0.64"),
        "home_serve_point_lower": Decimal("0.61"),
        "home_serve_point_upper": Decimal("0.67"),
        "away_serve_point_probability": Decimal("0.58"),
        "away_serve_point_lower": Decimal("0.55"),
        "away_serve_point_upper": Decimal("0.61"),
        "home_effective_sample_size": Decimal("80"),
        "away_effective_sample_size": Decimal("80"),
        "supported": True,
        "support_status": "supported",
        "training_cutoff_wall_ns": 90,
        "model_sha256": SHA_A,
        "prematch_artifact_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "feature_vector_sha256": SHA_D,
        "abstention_reason": None,
    }
    values.update(changes)
    return PrematchPrior(**values)  # type: ignore[arg-type]


def swapped_state(value: TennisState) -> TennisState:
    completed = tuple(
        SetScore(
            set_score.games_away,
            set_score.games_home,
            set_score.tiebreak_points_away,
            set_score.tiebreak_points_home,
        )
        for set_score in value.completed_sets
    )
    winner = None if value.winner is None else (
        PlayerSide.AWAY if value.winner is PlayerSide.HOME else PlayerSide.HOME
    )
    server = None if value.server_for_next_point is None else (
        PlayerSide.AWAY
        if value.server_for_next_point is PlayerSide.HOME
        else PlayerSide.HOME
    )
    first = None if value.tiebreak_first_server is None else (
        PlayerSide.AWAY
        if value.tiebreak_first_server is PlayerSide.HOME
        else PlayerSide.HOME
    )
    return replace(
        value,
        home_player_id=value.away_player_id,
        away_player_id=value.home_player_id,
        winner=winner,
        completed_sets=completed,
        games_home=value.games_away,
        games_away=value.games_home,
        points_home=value.points_away,
        points_away=value.points_home,
        tiebreak_points_home=value.tiebreak_points_away,
        tiebreak_points_away=value.tiebreak_points_home,
        tiebreak_first_server=first,
        server_for_next_point=server,
    )


def swapped_prior(value: PrematchPrior) -> PrematchPrior:
    return replace(
        value,
        player_home_id=value.player_away_id,
        player_away_id=value.player_home_id,
        home_serve_point_probability=value.away_serve_point_probability,
        home_serve_point_lower=value.away_serve_point_lower,
        home_serve_point_upper=value.away_serve_point_upper,
        away_serve_point_probability=value.home_serve_point_probability,
        away_serve_point_lower=value.home_serve_point_lower,
        away_serve_point_upper=value.home_serve_point_upper,
        home_effective_sample_size=value.away_effective_sample_size,
        away_effective_sample_size=value.home_effective_sample_size,
    )


class WinProbabilityTests(unittest.TestCase):
    def test_repeatability_and_away_complement(self) -> None:
        estimate = live_fair_value(state(), prior())
        repeated = live_fair_value(state(), prior())
        away = live_fair_value_for_side(state(), prior(), PlayerSide.AWAY)
        self.assertEqual(estimate, repeated)
        self.assertTrue(estimate.supported)
        self.assertEqual(estimate.player_side, PlayerSide.HOME)
        self.assertEqual(
            away.fair_probability,
            Decimal("1") - estimate.fair_probability,
        )

    def test_symmetry_under_player_swap(self) -> None:
        base_state = state(games_home=2, games_away=1)
        estimate = live_fair_value(base_state, prior())
        swapped = live_fair_value(
            swapped_state(base_state),
            swapped_prior(prior()),
        )
        self.assertEqual(
            estimate.fair_probability + swapped.fair_probability,
            Decimal("1.000000000000"),
        )

    def test_monotonicity_after_home_wins_point(self) -> None:
        before = state(server_for_next_point=PlayerSide.HOME)
        after = state(
            points_home=ScoreValue.FIFTEEN,
            points_away=ScoreValue.LOVE,
            server_for_next_point=PlayerSide.HOME,
            revision=2,
        )
        self.assertGreater(
            live_fair_value(after, prior()).fair_probability,
            live_fair_value(before, prior()).fair_probability,
        )

    def test_terminal_states_are_zero_or_one(self) -> None:
        ended = state(
            status=MatchStatus.ENDED,
            termination_kind=TerminationKind.NATURAL,
            winner=PlayerSide.HOME,
            completed_sets=(
                SetScore(6, 0, None, None),
                SetScore(6, 0, None, None),
            ),
            server_for_next_point=None,
        )
        estimate = live_fair_value(ended, prior())
        self.assertTrue(estimate.supported)
        self.assertEqual(estimate.fair_probability, Decimal("1"))
        self.assertEqual(estimate.lower_probability, Decimal("1"))
        self.assertEqual(estimate.upper_probability, Decimal("1"))

    def test_deuce_tiebreak_and_bo5_are_supported(self) -> None:
        deuce = state(
            games_home=3,
            games_away=3,
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.FORTY,
            server_for_next_point=PlayerSide.AWAY,
        )
        tiebreak = state(
            games_home=6,
            games_away=6,
            points_home=ScoreValue.LOVE,
            points_away=ScoreValue.LOVE,
            in_tiebreak=True,
            tiebreak_points_home=6,
            tiebreak_points_away=6,
            tiebreak_first_server=PlayerSide.HOME,
            server_for_next_point=PlayerSide.HOME,
        )
        bo5 = state(
            match_format=MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
            completed_sets=(SetScore(6, 4, None, None),),
        )
        for value in (deuce, tiebreak, bo5):
            with self.subTest(value=value):
                estimate = live_fair_value(value, prior())
                self.assertTrue(estimate.supported)
                self.assertGreaterEqual(estimate.fair_probability, Decimal("0"))
                self.assertLessEqual(estimate.fair_probability, Decimal("1"))

    def test_unsupported_format_and_low_ess_abstain(self) -> None:
        unsupported = live_fair_value(
            unchecked_state(match_format=MatchFormat.UNSUPPORTED),
            prior(),
        )
        self.assertFalse(unsupported.supported)
        self.assertEqual(
            unsupported.abstention_reason,
            DecisionReason.MODEL_UNSUPPORTED,
        )
        low_ess = live_fair_value(
            state(),
            prior(
                supported=False,
                support_status="low_ess",
                home_effective_sample_size=Decimal("2"),
                away_effective_sample_size=Decimal("2"),
                abstention_reason=DecisionReason.MODEL_UNCERTAIN,
            ),
        )
        self.assertFalse(low_ess.supported)
        self.assertEqual(low_ess.abstention_reason, DecisionReason.MODEL_UNCERTAIN)


if __name__ == "__main__":
    unittest.main()
