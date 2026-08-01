from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

import inci_tennis_expert.tennis_score as tennis_score_module
from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderLifecycle,
    ProviderLifecycleKind,
    ProviderPoint,
    ProviderSnapshot,
    ScoreValue,
    SetScore,
    TennisState,
    TennisStateInvariantError,
    TennisTransitionError,
    TennisTransitionReason,
    TennisTransitionResult,
    TerminationKind,
    TransitionDisposition,
    canonical_expert_bytes,
)
from inci_tennis_expert.tennis_score import (
    apply_correction,
    apply_lifecycle,
    apply_point,
    state_from_snapshot,
    validate_tennis_state,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
BO3 = MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
BO5 = MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
KNOWN_INITIAL_EVENT_SHA256 = (
    "c287dc44ec947dd0dda478e69616798afdeba17f4c5e7422dfeb10d051cb6483"
)
KNOWN_INITIAL_LINEAGE_SHA256 = (
    "ab302af3cc9a94e32d1b91968cc78568880d94cf64bdf058057d072b71210fdf"
)
KNOWN_CORRECTION_EVENT_SHA256 = (
    "0b762811207cbcd59a55e87759ab52348364229989cee7a271519948af60464c"
)
KNOWN_CORRECTION_LINEAGE_SHA256 = (
    "d2bea783ee0ae20e109340bac5f66dcc718c841940850cdffe691cbdcab6f138"
)


def snapshot(**changes: object) -> ProviderSnapshot:
    values: dict[str, object] = {
        "provider_source_id": "provider-a",
        "revision_domain_id": "revision-a",
        "source_lineage_sha256": SHA_A,
        "provider_event_id": "snapshot-0",
        "provider_match_id": "match-a",
        "home_player_id": "home-a",
        "away_player_id": "away-a",
        "scheduled_start_wall_ns": 1_000,
        "match_format": BO3,
        "status": MatchStatus.SCHEDULED,
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
        "server_for_next_point": None,
        "correction_epoch": 0,
        "revision": 0,
        "source_wall_ns": 2_000,
        "source_generated_wall_ns": 1_900,
        "received_monotonic_ns": 3_000,
        "clock_uncertainty_ns": 7,
        "snapshot_complete": True,
    }
    values.update(changes)
    return ProviderSnapshot(**values)  # type: ignore[arg-type]


def live_snapshot(**changes: object) -> ProviderSnapshot:
    values: dict[str, object] = {
        "status": MatchStatus.LIVE,
        "server_for_next_point": PlayerSide.HOME,
    }
    values.update(changes)
    return snapshot(**values)


def point(
    state: TennisState,
    winner: PlayerSide = PlayerSide.HOME,
    **changes: object,
) -> ProviderPoint:
    values: dict[str, object] = {
        "provider_source_id": state.provider_source_id,
        "revision_domain_id": state.revision_domain_id,
        "source_lineage_sha256": state.source_lineage_sha256,
        "provider_event_id": f"point-{state.revision + 1}",
        "provider_match_id": state.provider_match_id,
        "home_player_id": state.home_player_id,
        "away_player_id": state.away_player_id,
        "scheduled_start_wall_ns": state.scheduled_start_wall_ns,
        "match_format": state.match_format,
        "correction_epoch": state.correction_epoch,
        "revision": state.revision + 1,
        "point_winner": winner,
        "server_before_point": (
            state.server_for_next_point
            if state.server_for_next_point is not None
            else PlayerSide.HOME
        ),
        "source_wall_ns": state.last_source_wall_ns + 1,
        "source_generated_wall_ns": state.last_source_generated_wall_ns + 1,
        "received_monotonic_ns": state.last_received_monotonic_ns + 1,
        "clock_uncertainty_ns": state.last_clock_uncertainty_ns,
    }
    values.update(changes)
    return ProviderPoint(**values)  # type: ignore[arg-type]


def lifecycle(
    state: TennisState,
    kind: ProviderLifecycleKind,
    **changes: object,
) -> ProviderLifecycle:
    values: dict[str, object] = {
        "provider_source_id": state.provider_source_id,
        "revision_domain_id": state.revision_domain_id,
        "source_lineage_sha256": state.source_lineage_sha256,
        "provider_event_id": f"lifecycle-{state.revision + 1}",
        "provider_match_id": state.provider_match_id,
        "home_player_id": state.home_player_id,
        "away_player_id": state.away_player_id,
        "scheduled_start_wall_ns": state.scheduled_start_wall_ns,
        "match_format": state.match_format,
        "correction_epoch": state.correction_epoch,
        "revision": state.revision + 1,
        "kind": kind,
        "winner": None,
        "retired_side": None,
        "server_for_next_point": None,
        "source_wall_ns": state.last_source_wall_ns + 1,
        "source_generated_wall_ns": state.last_source_generated_wall_ns + 1,
        "received_monotonic_ns": state.last_received_monotonic_ns + 1,
        "clock_uncertainty_ns": state.last_clock_uncertainty_ns,
    }
    values.update(changes)
    return ProviderLifecycle(**values)  # type: ignore[arg-type]


def correction_snapshot(
    state: TennisState,
    **changes: object,
) -> ProviderSnapshot:
    values: dict[str, object] = {
        "provider_source_id": state.provider_source_id,
        "revision_domain_id": state.revision_domain_id,
        "source_lineage_sha256": state.source_lineage_sha256,
        "provider_event_id": f"correction-{state.correction_epoch + 1}",
        "provider_match_id": state.provider_match_id,
        "home_player_id": state.home_player_id,
        "away_player_id": state.away_player_id,
        "scheduled_start_wall_ns": state.scheduled_start_wall_ns,
        "match_format": state.match_format,
        "status": state.status,
        "termination_kind": state.termination_kind,
        "winner": state.winner,
        "retired_side": state.retired_side,
        "completed_sets": state.completed_sets,
        "games_home": state.games_home,
        "games_away": state.games_away,
        "points_home": state.points_home,
        "points_away": state.points_away,
        "in_tiebreak": state.in_tiebreak,
        "tiebreak_points_home": state.tiebreak_points_home,
        "tiebreak_points_away": state.tiebreak_points_away,
        "tiebreak_first_server": state.tiebreak_first_server,
        "server_for_next_point": state.server_for_next_point,
        "correction_epoch": state.correction_epoch + 1,
        "revision": 0,
        "source_wall_ns": state.last_source_wall_ns + 10,
        "source_generated_wall_ns": state.last_source_generated_wall_ns + 10,
        "received_monotonic_ns": state.last_received_monotonic_ns + 10,
        "clock_uncertainty_ns": state.last_clock_uncertainty_ns,
        "snapshot_complete": True,
    }
    values.update(changes)
    return ProviderSnapshot(**values)  # type: ignore[arg-type]


def applied_point(
    state: TennisState,
    winner: PlayerSide,
) -> TennisState:
    result = apply_point(state, point(state, winner))
    if result.disposition is not TransitionDisposition.APPLIED:
        raise AssertionError(result)
    return result.state


def next_tiebreak_server(
    first: PlayerSide,
    completed_points: int,
) -> PlayerSide:
    opposite = (
        PlayerSide.AWAY if first is PlayerSide.HOME else PlayerSide.HOME
    )
    if completed_points == 0:
        return first
    if ((completed_points - 1) // 2) % 2 == 0:
        return opposite
    return first


class TennisScoreTests(unittest.TestCase):
    def assert_pair(
        self,
        result: TennisTransitionResult,
        disposition: TransitionDisposition,
        reason: TennisTransitionReason,
    ) -> None:
        self.assertIs(type(result), TennisTransitionResult)
        self.assertIs(result.disposition, disposition)
        self.assertIs(result.reason, reason)

    def assert_block(
        self,
        result: TennisTransitionResult,
        reason: TennisTransitionReason,
    ) -> None:
        self.assert_pair(
            result,
            TransitionDisposition.BLOCKED,
            reason,
        )
        if reason is TennisTransitionReason.CORRECTION_REQUIRED:
            return
        self.assertIs(result.state.block_reason, reason)
        self.assertEqual(
            result.state.blocked_event_semantic_sha256,
            result.event_semantic_sha256,
        )

    def test_bootstrap_all_legal_status_shapes_and_formats(self) -> None:
        legal = (
            snapshot(),
            live_snapshot(),
            live_snapshot(status=MatchStatus.SUSPENDED),
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(
                    SetScore(6, 0, None, None),
                    SetScore(7, 6, 8, 6),
                ),
            ),
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.WALKOVER,
                winner=PlayerSide.AWAY,
            ),
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.RETIREMENT,
                winner=PlayerSide.HOME,
                retired_side=PlayerSide.AWAY,
                games_home=3,
                games_away=2,
                points_home=ScoreValue.FORTY,
                points_away=ScoreValue.THIRTY,
            ),
            snapshot(
                status=MatchStatus.CANCELLED,
                termination_kind=TerminationKind.CANCELLATION,
                games_home=4,
                games_away=1,
                points_home=ScoreValue.FIFTEEN,
                points_away=ScoreValue.THIRTY,
            ),
            live_snapshot(match_format=BO5),
        )
        for item in legal:
            with self.subTest(status=item.status, format=item.match_format):
                state = state_from_snapshot(item)
                self.assertIs(type(state), TennisState)
                self.assertTrue(state.snapshot_complete)
                self.assertIsNone(state.block_reason)
                self.assertEqual(state.last_provider_event_id, item.provider_event_id)
                self.assertEqual(len(state.last_event_semantic_sha256), 64)
                self.assertEqual(len(state.correction_lineage_sha256), 64)

    def test_independently_frozen_semantic_and_lineage_vectors(self) -> None:
        initial = snapshot()
        state = state_from_snapshot(initial)
        self.assertEqual(
            state.last_event_semantic_sha256,
            KNOWN_INITIAL_EVENT_SHA256,
        )
        self.assertEqual(
            state.correction_lineage_sha256,
            KNOWN_INITIAL_LINEAGE_SHA256,
        )
        replacement = snapshot(
            provider_event_id="correction-3",
            status=MatchStatus.LIVE,
            server_for_next_point=PlayerSide.AWAY,
            correction_epoch=3,
            revision=2,
            source_wall_ns=2_010,
            source_generated_wall_ns=1_910,
            received_monotonic_ns=3_010,
        )
        result = apply_correction(state, replacement)
        self.assertEqual(
            result.event_semantic_sha256,
            KNOWN_CORRECTION_EVENT_SHA256,
        )
        self.assertEqual(
            result.state.correction_lineage_sha256,
            KNOWN_CORRECTION_LINEAGE_SHA256,
        )

    def test_bootstrap_mid_tiebreak_terminal_precedence(self) -> None:
        for status, termination, winner, retired in (
            (
                MatchStatus.ENDED,
                TerminationKind.RETIREMENT,
                PlayerSide.HOME,
                PlayerSide.AWAY,
            ),
            (
                MatchStatus.CANCELLED,
                TerminationKind.CANCELLATION,
                None,
                None,
            ),
        ):
            state = state_from_snapshot(
                snapshot(
                    status=status,
                    termination_kind=termination,
                    winner=winner,
                    retired_side=retired,
                    games_home=6,
                    games_away=6,
                    in_tiebreak=True,
                    tiebreak_points_home=4,
                    tiebreak_points_away=3,
                    tiebreak_first_server=PlayerSide.HOME,
                    server_for_next_point=None,
                )
            )
            self.assertTrue(state.in_tiebreak)
            self.assertIs(state.tiebreak_first_server, PlayerSide.HOME)
            self.assertIsNone(state.server_for_next_point)

    def test_unsupported_and_incomplete_bootstrap_precedence(self) -> None:
        unsupported = snapshot(
            match_format=MatchFormat.UNSUPPORTED,
            snapshot_complete=False,
            games_home=99,
        )
        with self.assertRaises(TennisTransitionError) as captured:
            state_from_snapshot(unsupported)
        self.assertIs(
            captured.exception.reason,
            TennisTransitionReason.UNSUPPORTED_FORMAT,
        )
        with self.assertRaises(TennisTransitionError) as captured:
            state_from_snapshot(snapshot(snapshot_complete=False))
        self.assertIs(
            captured.exception.reason,
            TennisTransitionReason.SNAPSHOT_INVALID,
        )

    def test_bootstrap_rejects_every_ruled_unreachable_score_family(self) -> None:
        valid_set = SetScore(6, 4, None, None)
        invalid_cases = {
            "bad_completed_normal": live_snapshot(
                completed_sets=(SetScore(6, 5, None, None),)
            ),
            "normal_seven_six_is_not_completed": live_snapshot(
                completed_sets=(SetScore(7, 6, None, None),)
            ),
            "tiebreak_fields_on_normal_set": live_snapshot(
                completed_sets=(SetScore(6, 4, 7, 5),)
            ),
            "bad_completed_tiebreak_games": live_snapshot(
                completed_sets=(SetScore(6, 6, 7, 5),)
            ),
            "bad_completed_tiebreak_winner": live_snapshot(
                completed_sets=(SetScore(7, 6, 5, 7),)
            ),
            "bad_completed_tiebreak_margin": live_snapshot(
                completed_sets=(SetScore(7, 6, 7, 6),)
            ),
            "current_game_above_six": live_snapshot(games_home=7),
            "current_set_already_won": live_snapshot(
                games_home=6, games_away=2
            ),
            "six_all_outside_tiebreak": live_snapshot(
                games_home=6, games_away=6
            ),
            "tiebreak_not_six_all": live_snapshot(
                games_home=5,
                games_away=5,
                in_tiebreak=True,
                tiebreak_first_server=PlayerSide.HOME,
            ),
            "active_tiebreak_missing_first_server": live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_first_server=None,
                server_for_next_point=PlayerSide.HOME,
            ),
            "tiebreak_already_won": live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_points_home=7,
                tiebreak_points_away=2,
                tiebreak_first_server=PlayerSide.HOME,
            ),
            "tiebreak_wrong_server": live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_points_home=2,
                tiebreak_points_away=1,
                tiebreak_first_server=PlayerSide.HOME,
                server_for_next_point=PlayerSide.AWAY,
            ),
            "scheduled_has_set": snapshot(completed_sets=(valid_set,)),
            "scheduled_has_server": snapshot(
                server_for_next_point=PlayerSide.HOME
            ),
            "active_missing_server": live_snapshot(
                server_for_next_point=None
            ),
            "suspended_missing_server": live_snapshot(
                status=MatchStatus.SUSPENDED,
                server_for_next_point=None,
            ),
            "preterminal_clinch": live_snapshot(
                completed_sets=(valid_set, SetScore(6, 3, None, None))
            ),
            "prefix_after_clinch": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(
                    valid_set,
                    SetScore(6, 3, None, None),
                    SetScore(0, 6, None, None),
                ),
            ),
            "natural_wrong_winner": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.AWAY,
                completed_sets=(valid_set, SetScore(6, 3, None, None)),
            ),
            "natural_not_clinched": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(valid_set,),
            ),
            "natural_has_current_score": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(valid_set, SetScore(6, 3, None, None)),
                points_home=ScoreValue.FIFTEEN,
            ),
            "natural_has_server": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(valid_set, SetScore(6, 3, None, None)),
                server_for_next_point=PlayerSide.HOME,
            ),
            "walkover_has_score": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.WALKOVER,
                winner=PlayerSide.HOME,
                games_home=1,
            ),
            "walkover_has_server": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.WALKOVER,
                winner=PlayerSide.HOME,
                server_for_next_point=PlayerSide.HOME,
            ),
            "retirement_has_server": snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.RETIREMENT,
                winner=PlayerSide.HOME,
                retired_side=PlayerSide.AWAY,
                server_for_next_point=PlayerSide.HOME,
            ),
            "cancelled_clinched": snapshot(
                status=MatchStatus.CANCELLED,
                termination_kind=TerminationKind.CANCELLATION,
                completed_sets=(valid_set, SetScore(6, 3, None, None)),
            ),
        }
        for name, item in invalid_cases.items():
            with self.subTest(name=name):
                with self.assertRaises(TennisTransitionError) as captured:
                    state_from_snapshot(item)
                self.assertIs(
                    captured.exception.reason,
                    TennisTransitionReason.SNAPSHOT_INVALID,
                )

    def test_normal_game_total_algorithm_and_repeated_deuce(self) -> None:
        state = state_from_snapshot(live_snapshot())
        sequence = (
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.AWAY,
            PlayerSide.AWAY,
        )
        for winner in sequence:
            state = applied_point(state, winner)
        self.assertIs(state.points_home, ScoreValue.FORTY)
        self.assertIs(state.points_away, ScoreValue.FORTY)
        for _ in range(3):
            state = applied_point(state, PlayerSide.HOME)
            self.assertIs(state.points_home, ScoreValue.ADVANTAGE)
            state = applied_point(state, PlayerSide.AWAY)
            self.assertIs(state.points_home, ScoreValue.FORTY)
            self.assertIs(state.points_away, ScoreValue.FORTY)
        state = applied_point(state, PlayerSide.AWAY)
        self.assertIs(state.points_away, ScoreValue.ADVANTAGE)
        state = applied_point(state, PlayerSide.AWAY)
        self.assertEqual((state.games_home, state.games_away), (0, 1))
        self.assertEqual(
            (state.points_home, state.points_away),
            (ScoreValue.LOVE, ScoreValue.LOVE),
        )
        self.assertIs(state.server_for_next_point, PlayerSide.AWAY)

    def test_normal_set_scores_both_orientations_and_server_continuity(
        self,
    ) -> None:
        cases = (
            (5, 0, PlayerSide.HOME, (6, 0)),
            (0, 5, PlayerSide.AWAY, (0, 6)),
            (6, 5, PlayerSide.HOME, (7, 5)),
            (5, 6, PlayerSide.AWAY, (5, 7)),
        )
        for home_games, away_games, winner, expected in cases:
            with self.subTest(expected=expected):
                state = state_from_snapshot(
                    live_snapshot(
                        games_home=home_games,
                        games_away=away_games,
                        points_home=(
                            ScoreValue.FORTY
                            if winner is PlayerSide.HOME
                            else ScoreValue.LOVE
                        ),
                        points_away=(
                            ScoreValue.FORTY
                            if winner is PlayerSide.AWAY
                            else ScoreValue.LOVE
                        ),
                    )
                )
                result = apply_point(state, point(state, winner))
                self.assert_pair(
                    result,
                    TransitionDisposition.APPLIED,
                    TennisTransitionReason.POINT_APPLIED,
                )
                self.assertEqual(
                    (
                        result.state.completed_sets[-1].games_home,
                        result.state.completed_sets[-1].games_away,
                    ),
                    expected,
                )
                self.assertIs(
                    result.state.server_for_next_point,
                    PlayerSide.AWAY,
                )

    def test_tiebreak_results_service_sequence_and_next_set_server(self) -> None:
        first = PlayerSide.HOME
        for final_home, final_away in ((7, 0), (7, 5), (8, 6), (12, 10)):
            with self.subTest(score=(final_home, final_away)):
                before_home = final_home - 1
                before_away = final_away
                completed = before_home + before_away
                state = state_from_snapshot(
                    live_snapshot(
                        games_home=6,
                        games_away=6,
                        in_tiebreak=True,
                        tiebreak_points_home=before_home,
                        tiebreak_points_away=before_away,
                        tiebreak_first_server=first,
                        server_for_next_point=next_tiebreak_server(
                            first, completed
                        ),
                    )
                )
                result = apply_point(
                    state,
                    point(state, PlayerSide.HOME),
                )
                self.assertEqual(
                    result.state.completed_sets[-1],
                    SetScore(7, 6, final_home, final_away),
                )
                self.assertIs(
                    result.state.server_for_next_point,
                    PlayerSide.AWAY,
                )
        state = state_from_snapshot(
            live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_first_server=first,
                server_for_next_point=first,
            )
        )
        observed = []
        for winner in (
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.AWAY,
        ):
            observed.append(state.server_for_next_point)
            state = applied_point(state, winner)
        self.assertEqual(
            observed,
            [
                PlayerSide.HOME,
                PlayerSide.AWAY,
                PlayerSide.AWAY,
                PlayerSide.HOME,
                PlayerSide.HOME,
                PlayerSide.AWAY,
            ],
        )

    def test_tiebreak_away_orientation(self) -> None:
        state = state_from_snapshot(
            live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_points_home=5,
                tiebreak_points_away=6,
                tiebreak_first_server=PlayerSide.AWAY,
                server_for_next_point=next_tiebreak_server(
                    PlayerSide.AWAY, 11
                ),
            )
        )
        result = apply_point(state, point(state, PlayerSide.AWAY))
        self.assertEqual(
            result.state.completed_sets[-1],
            SetScore(6, 7, 5, 7),
        )
        self.assertIs(
            result.state.server_for_next_point,
            PlayerSide.HOME,
        )

    def test_entering_tiebreak_uses_already_toggled_server(self) -> None:
        state = state_from_snapshot(
            live_snapshot(
                games_home=5,
                games_away=6,
                points_home=ScoreValue.FORTY,
                server_for_next_point=PlayerSide.HOME,
            )
        )
        result = apply_point(state, point(state, PlayerSide.HOME))
        self.assertTrue(result.state.in_tiebreak)
        self.assertEqual(
            (result.state.games_home, result.state.games_away),
            (6, 6),
        )
        self.assertIs(
            result.state.tiebreak_first_server,
            PlayerSide.AWAY,
        )
        self.assertIs(
            result.state.server_for_next_point,
            PlayerSide.AWAY,
        )

    def test_bo3_and_bo5_end_at_exact_clinch(self) -> None:
        for match_format, prior_sets, winner_sets in (
            (
                BO3,
                (SetScore(6, 0, None, None),),
                2,
            ),
            (
                BO5,
                (
                    SetScore(6, 0, None, None),
                    SetScore(6, 1, None, None),
                ),
                3,
            ),
        ):
            with self.subTest(match_format=match_format):
                state = state_from_snapshot(
                    live_snapshot(
                        match_format=match_format,
                        completed_sets=prior_sets,
                        games_home=5,
                        points_home=ScoreValue.FORTY,
                    )
                )
                result = apply_point(state, point(state, PlayerSide.HOME))
                self.assertIs(result.state.status, MatchStatus.ENDED)
                self.assertIs(
                    result.state.termination_kind,
                    TerminationKind.NATURAL,
                )
                self.assertIs(result.state.winner, PlayerSide.HOME)
                self.assertEqual(
                    sum(
                        set_score.games_home > set_score.games_away
                        for set_score in result.state.completed_sets
                    ),
                    winner_sets,
                )
                self.assertIsNone(result.state.server_for_next_point)

    def test_incremental_validation_precedence_and_revision_machine(self) -> None:
        base = state_from_snapshot(live_snapshot(revision=10))
        cases = (
            (
                {"provider_match_id": "other", "provider_source_id": "other"},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"home_player_id": "other-home"},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"away_player_id": "other-away"},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"scheduled_start_wall_ns": 999},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"provider_source_id": "other", "revision_domain_id": "other"},
                TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
            ),
            (
                {"source_lineage_sha256": SHA_B},
                TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
            ),
            (
                {"source_lineage_sha256": SHA_B, "revision_domain_id": "other"},
                TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
            ),
            (
                {"revision_domain_id": "other", "match_format": BO5},
                TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
            ),
            (
                {"match_format": BO5, "received_monotonic_ns": 1},
                TennisTransitionReason.FORMAT_MISMATCH,
            ),
            (
                {"received_monotonic_ns": 1, "correction_epoch": 9},
                TennisTransitionReason.RECEIVE_TIME_REGRESSION,
            ),
            (
                {"correction_epoch": 1},
                TennisTransitionReason.CORRECTION_EPOCH_AHEAD,
            ),
            (
                {"revision": 9},
                TennisTransitionReason.PROVIDER_EVENT_STALE,
            ),
            (
                {"revision": 12},
                TennisTransitionReason.PROVIDER_EVENT_GAP,
            ),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                result = apply_point(base, point(base, **changes))
                self.assert_block(result, reason)
                if reason is TennisTransitionReason.PROVIDER_EVENT_GAP:
                    self.assertEqual(result.state.expected_revision, 11)
                    self.assertEqual(result.state.observed_revision, 12)
                else:
                    self.assertIsNone(result.state.expected_revision)
                    self.assertIsNone(result.state.observed_revision)

        applied_event = point(base)
        applied = apply_point(base, applied_event)
        duplicate = apply_point(
            applied.state,
            replace(
                applied_event,
                received_monotonic_ns=(
                    applied.state.last_received_monotonic_ns + 10
                ),
            ),
        )
        self.assert_pair(
            duplicate,
            TransitionDisposition.DUPLICATE,
            TennisTransitionReason.EXACT_DUPLICATE,
        )
        self.assertIs(duplicate.state, applied.state)
        conflict = apply_point(
            applied.state,
            replace(
                applied_event,
                received_monotonic_ns=(
                    applied.state.last_received_monotonic_ns + 10
                ),
                clock_uncertainty_ns=99,
            ),
        )
        self.assert_block(
            conflict,
            TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
        )

    def test_lifecycle_retransmission_and_conflict_share_revision_stream(
        self,
    ) -> None:
        scheduled = state_from_snapshot(snapshot())
        event = lifecycle(
            scheduled,
            ProviderLifecycleKind.START,
            server_for_next_point=PlayerSide.HOME,
        )
        applied = apply_lifecycle(scheduled, event)
        duplicate = apply_lifecycle(
            applied.state,
            replace(
                event,
                received_monotonic_ns=(
                    applied.state.last_received_monotonic_ns + 5
                ),
            ),
        )
        self.assert_pair(
            duplicate,
            TransitionDisposition.DUPLICATE,
            TennisTransitionReason.EXACT_DUPLICATE,
        )
        self.assertIs(duplicate.state, applied.state)
        conflict = apply_lifecycle(
            applied.state,
            replace(
                event,
                received_monotonic_ns=(
                    applied.state.last_received_monotonic_ns + 5
                ),
                clock_uncertainty_ns=8,
            ),
        )
        self.assert_block(
            conflict,
            TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
        )

    def test_lower_epoch_and_block_persistence_are_exact(self) -> None:
        state = state_from_snapshot(
            live_snapshot(correction_epoch=2, revision=4)
        )
        first = apply_point(
            state,
            point(state, correction_epoch=1, revision=5),
        )
        self.assert_block(
            first,
            TennisTransitionReason.CORRECTION_EPOCH_STALE,
        )
        second = apply_point(first.state, point(first.state))
        self.assert_pair(
            second,
            TransitionDisposition.BLOCKED,
            TennisTransitionReason.CORRECTION_REQUIRED,
        )
        self.assertIs(second.state, first.state)

    def test_point_status_and_server_rules(self) -> None:
        scheduled = state_from_snapshot(snapshot())
        self.assert_block(
            apply_point(scheduled, point(scheduled)),
            TennisTransitionReason.POINT_WHILE_NOT_LIVE,
        )
        suspended = state_from_snapshot(
            live_snapshot(status=MatchStatus.SUSPENDED)
        )
        self.assert_block(
            apply_point(suspended, point(suspended)),
            TennisTransitionReason.POINT_WHILE_NOT_LIVE,
        )
        live = state_from_snapshot(live_snapshot())
        self.assert_block(
            apply_point(
                live,
                point(
                    live,
                    server_before_point=PlayerSide.AWAY,
                ),
            ),
            TennisTransitionReason.SERVER_MISMATCH,
        )
        ended = state_from_snapshot(
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.WALKOVER,
                winner=PlayerSide.HOME,
            )
        )
        self.assert_block(
            apply_point(ended, point(ended)),
            TennisTransitionReason.TERMINAL_ABSORBING,
        )
        cancelled = state_from_snapshot(
            snapshot(
                status=MatchStatus.CANCELLED,
                termination_kind=TerminationKind.CANCELLATION,
            )
        )
        self.assert_block(
            apply_point(cancelled, point(cancelled)),
            TennisTransitionReason.TERMINAL_ABSORBING,
        )

    def test_every_lifecycle_kind_and_payload_rule(self) -> None:
        scheduled = state_from_snapshot(snapshot())
        started = apply_lifecycle(
            scheduled,
            lifecycle(
                scheduled,
                ProviderLifecycleKind.START,
                server_for_next_point=PlayerSide.AWAY,
            ),
        )
        self.assert_pair(
            started,
            TransitionDisposition.APPLIED,
            TennisTransitionReason.LIFECYCLE_APPLIED,
        )
        self.assertIs(started.state.status, MatchStatus.LIVE)
        self.assertIs(
            started.state.server_for_next_point,
            PlayerSide.AWAY,
        )
        suspended = apply_lifecycle(
            started.state,
            lifecycle(
                started.state,
                ProviderLifecycleKind.SUSPEND,
                server_for_next_point=PlayerSide.AWAY,
            ),
        )
        self.assertIs(suspended.state.status, MatchStatus.SUSPENDED)
        resumed = apply_lifecycle(
            suspended.state,
            lifecycle(
                suspended.state,
                ProviderLifecycleKind.RESUME,
                server_for_next_point=PlayerSide.AWAY,
            ),
        )
        self.assertIs(resumed.state.status, MatchStatus.LIVE)

        walkover = apply_lifecycle(
            state_from_snapshot(snapshot()),
            lifecycle(
                state_from_snapshot(snapshot()),
                ProviderLifecycleKind.WALKOVER,
                winner=PlayerSide.HOME,
            ),
        )
        self.assertIs(walkover.state.termination_kind, TerminationKind.WALKOVER)

        mid = state_from_snapshot(
            live_snapshot(
                games_home=3,
                games_away=2,
                points_home=ScoreValue.FORTY,
                points_away=ScoreValue.THIRTY,
            )
        )
        retirement = apply_lifecycle(
            mid,
            lifecycle(
                mid,
                ProviderLifecycleKind.RETIREMENT,
                winner=PlayerSide.HOME,
                retired_side=PlayerSide.AWAY,
            ),
        )
        self.assertIs(
            retirement.state.termination_kind,
            TerminationKind.RETIREMENT,
        )
        self.assertEqual(
            (
                retirement.state.games_home,
                retirement.state.games_away,
                retirement.state.points_home,
                retirement.state.points_away,
            ),
            (3, 2, ScoreValue.FORTY, ScoreValue.THIRTY),
        )
        self.assertIsNone(retirement.state.server_for_next_point)

        for source in (
            state_from_snapshot(snapshot()),
            state_from_snapshot(live_snapshot(games_home=2)),
            state_from_snapshot(
                live_snapshot(
                    status=MatchStatus.SUSPENDED,
                    games_home=2,
                )
            ),
        ):
            cancelled = apply_lifecycle(
                source,
                lifecycle(source, ProviderLifecycleKind.CANCEL),
            )
            self.assertIs(cancelled.state.status, MatchStatus.CANCELLED)
            self.assertEqual(
                (cancelled.state.games_home, cancelled.state.games_away),
                (source.games_home, source.games_away),
            )
            self.assertIsNone(cancelled.state.server_for_next_point)

        for kind, winner, retired in (
            (
                ProviderLifecycleKind.RETIREMENT,
                PlayerSide.HOME,
                PlayerSide.AWAY,
            ),
            (
                ProviderLifecycleKind.CANCEL,
                None,
                None,
            ),
        ):
            mid_tiebreak = state_from_snapshot(
                live_snapshot(
                    games_home=6,
                    games_away=6,
                    in_tiebreak=True,
                    tiebreak_points_home=4,
                    tiebreak_points_away=3,
                    tiebreak_first_server=PlayerSide.HOME,
                    server_for_next_point=next_tiebreak_server(
                        PlayerSide.HOME, 7
                    ),
                )
            )
            result = apply_lifecycle(
                mid_tiebreak,
                lifecycle(
                    mid_tiebreak,
                    kind,
                    winner=winner,
                    retired_side=retired,
                ),
            )
            self.assertTrue(result.state.in_tiebreak)
            self.assertIs(
                result.state.tiebreak_first_server,
                PlayerSide.HOME,
            )
            self.assertIsNone(result.state.server_for_next_point)

    def test_lifecycle_illegal_pair_payload_and_server_precedence(self) -> None:
        scheduled = state_from_snapshot(snapshot())
        illegal = (
            lifecycle(scheduled, ProviderLifecycleKind.RESUME),
            lifecycle(scheduled, ProviderLifecycleKind.START),
            lifecycle(
                scheduled,
                ProviderLifecycleKind.START,
                server_for_next_point=PlayerSide.HOME,
                winner=PlayerSide.HOME,
            ),
            lifecycle(
                scheduled,
                ProviderLifecycleKind.WALKOVER,
                winner=None,
            ),
        )
        for event in illegal:
            with self.subTest(kind=event.kind):
                self.assert_block(
                    apply_lifecycle(scheduled, event),
                    TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
                )

        live = state_from_snapshot(live_snapshot())
        self.assert_block(
            apply_lifecycle(
                live,
                lifecycle(
                    live,
                    ProviderLifecycleKind.SUSPEND,
                    server_for_next_point=None,
                ),
            ),
            TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
        )
        self.assert_block(
            apply_lifecycle(
                live,
                lifecycle(
                    live,
                    ProviderLifecycleKind.SUSPEND,
                    server_for_next_point=PlayerSide.AWAY,
                ),
            ),
            TennisTransitionReason.SERVER_MISMATCH,
        )

    def test_terminal_lifecycle_precedence_and_natural_confirmation(self) -> None:
        terminal_states = (
            state_from_snapshot(
                snapshot(
                    status=MatchStatus.ENDED,
                    termination_kind=TerminationKind.WALKOVER,
                    winner=PlayerSide.HOME,
                )
            ),
            state_from_snapshot(
                snapshot(
                    status=MatchStatus.CANCELLED,
                    termination_kind=TerminationKind.CANCELLATION,
                )
            ),
        )
        for state in terminal_states:
            for kind in ProviderLifecycleKind:
                event = lifecycle(
                    state,
                    kind,
                    winner=PlayerSide.AWAY,
                    retired_side=PlayerSide.AWAY,
                    server_for_next_point=PlayerSide.AWAY,
                )
                self.assert_block(
                    apply_lifecycle(state, event),
                    TennisTransitionReason.TERMINAL_ABSORBING,
                )

        natural = state_from_snapshot(
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(
                    SetScore(6, 0, None, None),
                    SetScore(6, 1, None, None),
                ),
            )
        )
        malformed = (
            lifecycle(
                natural,
                ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
            ),
            lifecycle(
                natural,
                ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
                winner=PlayerSide.AWAY,
            ),
            lifecycle(
                natural,
                ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
                winner=PlayerSide.HOME,
                server_for_next_point=PlayerSide.HOME,
            ),
        )
        for event in malformed:
            self.assert_block(
                apply_lifecycle(natural, event),
                TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
            )
        for kind in ProviderLifecycleKind:
            if kind is ProviderLifecycleKind.NATURAL_END_CONFIRMATION:
                continue
            self.assert_block(
                apply_lifecycle(
                    natural,
                    lifecycle(
                        natural,
                        kind,
                        winner=PlayerSide.AWAY,
                        retired_side=PlayerSide.AWAY,
                        server_for_next_point=PlayerSide.AWAY,
                    ),
                ),
                TennisTransitionReason.TERMINAL_ABSORBING,
            )
        exact = apply_lifecycle(
            natural,
            lifecycle(
                natural,
                ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
                winner=PlayerSide.HOME,
            ),
        )
        self.assert_pair(
            exact,
            TransitionDisposition.APPLIED,
            TennisTransitionReason.NATURAL_END_CONFIRMED,
        )
        self.assertEqual(exact.state.completed_sets, natural.completed_sets)
        self.assertEqual(exact.state.revision, natural.revision + 1)

    def test_correction_validation_precedence_and_block_preservation(self) -> None:
        state = state_from_snapshot(live_snapshot(revision=5))
        cases = (
            (
                {"provider_match_id": "other", "provider_source_id": "other"},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"home_player_id": "other-home"},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"away_player_id": "other-away"},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"scheduled_start_wall_ns": 999},
                TennisTransitionReason.IDENTITY_MISMATCH,
            ),
            (
                {"provider_source_id": "other", "revision_domain_id": "other"},
                TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
            ),
            (
                {"source_lineage_sha256": SHA_B},
                TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
            ),
            (
                {"revision_domain_id": "other", "match_format": BO5},
                TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
            ),
            (
                {"match_format": MatchFormat.UNSUPPORTED, "games_home": 99},
                TennisTransitionReason.UNSUPPORTED_FORMAT,
            ),
            (
                {"match_format": BO5, "received_monotonic_ns": 1},
                TennisTransitionReason.FORMAT_MISMATCH,
            ),
            (
                {"received_monotonic_ns": 1, "correction_epoch": 9},
                TennisTransitionReason.RECEIVE_TIME_REGRESSION,
            ),
            (
                {"correction_epoch": 0},
                TennisTransitionReason.CORRECTION_EPOCH_NOT_NEWER,
            ),
            (
                {
                    "snapshot_complete": False,
                    "correction_epoch": 1,
                },
                TennisTransitionReason.CORRECTION_SNAPSHOT_INVALID,
            ),
            (
                {
                    "games_home": 6,
                    "games_away": 0,
                    "correction_epoch": 1,
                },
                TennisTransitionReason.CORRECTION_SNAPSHOT_INVALID,
            ),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                result = apply_correction(
                    state,
                    correction_snapshot(state, **changes),
                )
                self.assert_block(result, reason)

        blocked = apply_point(
            state,
            point(state, revision=state.revision + 2),
        ).state
        rejected = apply_correction(
            blocked,
            correction_snapshot(
                blocked,
                provider_match_id="other",
            ),
        )
        self.assertIs(rejected.state, blocked)
        self.assertIs(
            rejected.state.block_reason,
            TennisTransitionReason.PROVIDER_EVENT_GAP,
        )
        epoch_two = state_from_snapshot(
            live_snapshot(correction_epoch=2, revision=3)
        )
        lower = apply_correction(
            epoch_two,
            correction_snapshot(
                epoch_two,
                correction_epoch=1,
            ),
        )
        self.assert_block(
            lower,
            TennisTransitionReason.CORRECTION_EPOCH_NOT_NEWER,
        )
        adjacent = apply_correction(
            state,
            correction_snapshot(
                state,
                correction_epoch=1,
                revision=2,
            ),
        )
        self.assert_pair(
            adjacent,
            TransitionDisposition.APPLIED,
            TennisTransitionReason.CORRECTION_APPLIED,
        )

    def test_correction_receipt_uses_last_and_blocked_monotonic_times(self) -> None:
        state = state_from_snapshot(live_snapshot())
        blocked = apply_point(
            state,
            point(
                state,
                revision=2,
                received_monotonic_ns=4_000,
            ),
        ).state
        self.assertEqual(blocked.blocked_received_monotonic_ns, 4_000)
        replacement = correction_snapshot(
            blocked,
            received_monotonic_ns=3_500,
        )
        rejected = apply_correction(blocked, replacement)
        self.assertIs(rejected.state, blocked)
        self.assertIs(
            rejected.reason,
            TennisTransitionReason.RECEIVE_TIME_REGRESSION,
        )

    def test_applied_correction_epoch_skip_revision_reset_and_clear_block(
        self,
    ) -> None:
        state = state_from_snapshot(live_snapshot(revision=8))
        blocked = apply_point(
            state,
            point(state, revision=10),
        ).state
        replacement = correction_snapshot(
            blocked,
            correction_epoch=4,
            revision=0,
            games_home=2,
            games_away=1,
            points_home=ScoreValue.FIFTEEN,
        )
        result = apply_correction(blocked, replacement)
        self.assert_pair(
            result,
            TransitionDisposition.APPLIED,
            TennisTransitionReason.CORRECTION_APPLIED,
        )
        self.assertEqual(result.state.correction_epoch, 4)
        self.assertEqual(result.state.revision, 0)
        self.assertEqual(result.state.games_home, 2)
        self.assertIsNone(result.state.block_reason)
        self.assertNotEqual(
            result.state.correction_lineage_sha256,
            blocked.correction_lineage_sha256,
        )

    def test_correction_duplicate_and_semantic_receipt_exclusion(self) -> None:
        original = live_snapshot()
        state = state_from_snapshot(original)
        retransmission = replace(
            original,
            received_monotonic_ns=original.received_monotonic_ns + 50,
        )
        result = apply_correction(state, retransmission)
        self.assert_pair(
            result,
            TransitionDisposition.DUPLICATE,
            TennisTransitionReason.EXACT_DUPLICATE,
        )
        self.assertIs(result.state, state)
        self.assertEqual(
            result.event_semantic_sha256,
            state.last_event_semantic_sha256,
        )

    def test_new_epoch_correction_can_replace_every_terminal(self) -> None:
        terminals = (
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.NATURAL,
                winner=PlayerSide.HOME,
                completed_sets=(
                    SetScore(6, 0, None, None),
                    SetScore(6, 1, None, None),
                ),
            ),
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.WALKOVER,
                winner=PlayerSide.HOME,
            ),
            snapshot(
                status=MatchStatus.ENDED,
                termination_kind=TerminationKind.RETIREMENT,
                winner=PlayerSide.HOME,
                retired_side=PlayerSide.AWAY,
                games_home=2,
            ),
            snapshot(
                status=MatchStatus.CANCELLED,
                termination_kind=TerminationKind.CANCELLATION,
                games_home=2,
            ),
        )
        for terminal in terminals:
            with self.subTest(termination=terminal.termination_kind):
                state = state_from_snapshot(terminal)
                replacement = correction_snapshot(
                    state,
                    status=MatchStatus.LIVE,
                    termination_kind=TerminationKind.NONE,
                    winner=None,
                    retired_side=None,
                    completed_sets=(),
                    games_home=1,
                    games_away=0,
                    points_home=ScoreValue.LOVE,
                    points_away=ScoreValue.LOVE,
                    in_tiebreak=False,
                    tiebreak_points_home=0,
                    tiebreak_points_away=0,
                    tiebreak_first_server=None,
                    server_for_next_point=PlayerSide.AWAY,
                )
                result = apply_correction(state, replacement)
                self.assertIs(result.state.status, MatchStatus.LIVE)
                self.assertIs(
                    result.state.server_for_next_point,
                    PlayerSide.AWAY,
                )

    def test_home_away_symmetry(self) -> None:
        home = state_from_snapshot(
            live_snapshot(
                games_home=5,
                points_home=ScoreValue.FORTY,
            )
        )
        away = state_from_snapshot(
            live_snapshot(
                games_away=5,
                points_away=ScoreValue.FORTY,
                server_for_next_point=PlayerSide.AWAY,
            )
        )
        home_result = apply_point(home, point(home, PlayerSide.HOME))
        away_result = apply_point(away, point(away, PlayerSide.AWAY))
        self.assertEqual(
            (
                home_result.state.completed_sets[-1].games_home,
                home_result.state.completed_sets[-1].games_away,
            ),
            (
                away_result.state.completed_sets[-1].games_away,
                away_result.state.completed_sets[-1].games_home,
            ),
        )

    def test_bounded_transition_enumeration_and_determinism(self) -> None:
        sequence = (
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.AWAY,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.HOME,
        )
        first = state_from_snapshot(live_snapshot())
        second = state_from_snapshot(live_snapshot())
        for winner in sequence:
            previous_revision = first.revision
            first_result = apply_point(first, point(first, winner))
            second_result = apply_point(second, point(second, winner))
            first = first_result.state
            second = second_result.state
            self.assertEqual(first.revision, previous_revision + 1)
            if first.status in {MatchStatus.LIVE, MatchStatus.SUSPENDED}:
                self.assertIsNotNone(first.server_for_next_point)
            self.assertEqual(first, second)
            self.assertEqual(
                canonical_expert_bytes(first),
                canonical_expert_bytes(second),
            )
            self.assertEqual(
                canonical_expert_bytes(first_result),
                canonical_expert_bytes(second_result),
            )

        first = state_from_snapshot(
            live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_first_server=PlayerSide.HOME,
                server_for_next_point=PlayerSide.HOME,
            )
        )
        for winner in (
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
        ):
            previous_revision = first.revision
            first = applied_point(first, winner)
            self.assertEqual(first.revision, previous_revision + 1)
            if first.status is MatchStatus.LIVE:
                self.assertIsNotNone(first.server_for_next_point)
            if first.completed_sets:
                break
        self.assertTrue(first.completed_sets)

    def test_bounded_branch_enumeration_accepts_both_point_winners(self) -> None:
        state = state_from_snapshot(live_snapshot())
        deuce_path = (
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.AWAY,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.AWAY,
            PlayerSide.HOME,
            PlayerSide.HOME,
        )
        saw_deuce = False
        for chosen in deuce_path:
            for candidate in PlayerSide:
                branch = apply_point(state, point(state, candidate))
                self.assert_pair(
                    branch,
                    TransitionDisposition.APPLIED,
                    TennisTransitionReason.POINT_APPLIED,
                )
                self.assertEqual(branch.state.revision, state.revision + 1)
                if branch.state.status is MatchStatus.LIVE:
                    self.assertIsNotNone(
                        branch.state.server_for_next_point
                    )
            state = apply_point(state, point(state, chosen)).state
            saw_deuce = saw_deuce or (
                state.points_home is ScoreValue.FORTY
                and state.points_away is ScoreValue.FORTY
            )
        self.assertTrue(saw_deuce)

        state = state_from_snapshot(
            live_snapshot(
                games_home=6,
                games_away=6,
                in_tiebreak=True,
                tiebreak_first_server=PlayerSide.HOME,
                server_for_next_point=PlayerSide.HOME,
            )
        )
        tiebreak_path = (
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
            PlayerSide.HOME,
        )
        for chosen in tiebreak_path:
            for candidate in PlayerSide:
                branch = apply_point(state, point(state, candidate))
                self.assert_pair(
                    branch,
                    TransitionDisposition.APPLIED,
                    TennisTransitionReason.POINT_APPLIED,
                )
                self.assertEqual(branch.state.revision, state.revision + 1)
                if branch.state.status is MatchStatus.LIVE:
                    self.assertIsNotNone(
                        branch.state.server_for_next_point
                    )
            state = apply_point(state, point(state, chosen)).state
        self.assertEqual(
            state.completed_sets[-1],
            SetScore(7, 6, 7, 0),
        )

    def test_new_block_changes_only_ruled_metadata(self) -> None:
        state = state_from_snapshot(
            live_snapshot(
                games_home=2,
                games_away=1,
                points_home=ScoreValue.FIFTEEN,
            )
        )
        result = apply_point(
            state,
            point(state, revision=state.revision + 2),
        )
        changed = {
            "block_reason",
            "expected_revision",
            "observed_revision",
            "blocked_event_semantic_sha256",
            "blocked_received_monotonic_ns",
        }
        for name in state.__dataclass_fields__:
            if name not in changed:
                self.assertEqual(
                    getattr(result.state, name),
                    getattr(state, name),
                    name,
                )

    def test_wall_values_do_not_control_monotonic_acceptance(self) -> None:
        state = state_from_snapshot(
            live_snapshot(
                scheduled_start_wall_ns=10**40,
                source_wall_ns=10**40 + 100,
                source_generated_wall_ns=10**40 + 50,
                received_monotonic_ns=20,
            )
        )
        event = point(
            state,
            source_wall_ns=1,
            source_generated_wall_ns=0,
            received_monotonic_ns=21,
        )
        result = apply_point(state, event)
        self.assert_pair(
            result,
            TransitionDisposition.APPLIED,
            TennisTransitionReason.POINT_APPLIED,
        )

    def test_wrong_exact_types_and_corrupt_current_state_boundary(self) -> None:
        state = state_from_snapshot(live_snapshot())
        for call in (
            lambda: state_from_snapshot(object()),  # type: ignore[arg-type]
            lambda: apply_point(object(), point(state)),  # type: ignore[arg-type]
            lambda: apply_point(state, object()),  # type: ignore[arg-type]
            lambda: apply_lifecycle(state, object()),  # type: ignore[arg-type]
            lambda: apply_correction(state, object()),  # type: ignore[arg-type]
        ):
            with self.subTest(call=call):
                with self.assertRaises(TypeError):
                    call()

        corrupt = TennisState(
            **{
                field: getattr(state, field)
                for field in state.__dataclass_fields__
            }
            | {"games_home": 6, "games_away": 0}
        )
        with self.assertRaises(TennisStateInvariantError):
            apply_point(corrupt, point(corrupt))
        with self.assertRaises(TennisStateInvariantError):
            apply_lifecycle(
                corrupt,
                lifecycle(corrupt, ProviderLifecycleKind.SUSPEND),
            )
        with self.assertRaises(TennisStateInvariantError):
            apply_correction(corrupt, correction_snapshot(corrupt))
        mismatched = point(
            corrupt,
            provider_match_id="other-match",
            provider_source_id="other-provider",
            revision_domain_id="other-revision",
            source_lineage_sha256=SHA_B,
            match_format=BO5,
            received_monotonic_ns=1,
            correction_epoch=9,
            revision=99,
            server_before_point=PlayerSide.AWAY,
        )
        with self.assertRaises(TennisStateInvariantError):
            apply_point(corrupt, mismatched)
        mismatched_lifecycle = lifecycle(
            corrupt,
            ProviderLifecycleKind.NATURAL_END_CONFIRMATION,
            provider_match_id="other-match",
            provider_source_id="other-provider",
            revision_domain_id="other-revision",
            source_lineage_sha256=SHA_B,
            match_format=BO5,
            received_monotonic_ns=1,
            correction_epoch=9,
            revision=99,
            winner=PlayerSide.HOME,
            retired_side=PlayerSide.AWAY,
            server_for_next_point=PlayerSide.AWAY,
        )
        with self.assertRaises(TennisStateInvariantError):
            apply_lifecycle(corrupt, mismatched_lifecycle)
        mismatched_correction = correction_snapshot(
            corrupt,
            provider_match_id="other-match",
            provider_source_id="other-provider",
            revision_domain_id="other-revision",
            source_lineage_sha256=SHA_B,
            match_format=MatchFormat.UNSUPPORTED,
            received_monotonic_ns=1,
            correction_epoch=0,
            revision=99,
            snapshot_complete=False,
        )
        with self.assertRaises(TennisStateInvariantError):
            apply_correction(corrupt, mismatched_correction)

    def test_no_wall_monotonic_cross_domain_comparison(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[2]
            / "inci_tennis_expert"
            / "tennis_score.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        def names(node: ast.AST) -> set[str]:
            return {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name)
            } | {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }

        for node in ast.walk(tree):
            if isinstance(node, (ast.Compare, ast.BinOp)):
                identifiers = names(node)
                self.assertFalse(
                    any("wall_ns" in name for name in identifiers)
                    and any("monotonic_ns" in name for name in identifiers),
                    ast.dump(node),
                )


class Task5ReachabilityRed(unittest.TestCase):
    def test_public_validate_tennis_state_is_present(self) -> None:
        self.assertTrue(
            callable(
                getattr(
                    tennis_score_module,
                    "validate_tennis_state",
                    None,
                )
            )
        )

    def test_public_validator_accepts_reachable_and_rejects_impossible(
        self,
    ) -> None:
        state = state_from_snapshot(live_snapshot())
        self.assertIsNone(validate_tennis_state(state))
        with self.assertRaisesRegex(TypeError, "^state$"):
            validate_tennis_state(object())  # type: ignore[arg-type]
        subclass = type("TennisStateSubclass", (TennisState,), {})
        with self.assertRaisesRegex(TypeError, "^state$"):
            validate_tennis_state(
                object.__new__(subclass)  # type: ignore[arg-type]
            )
        impossible = replace(
            state,
            games_home=7,
            games_away=0,
        )
        with self.assertRaises(TennisStateInvariantError) as caught:
            validate_tennis_state(impossible)
        self.assertEqual(
            str(caught.exception),
            "tennis_state_invariant_error",
        )


if __name__ == "__main__":
    unittest.main()
