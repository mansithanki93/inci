from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ProviderPoint,
    ScoreValue,
    SetScore,
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.pilot_contracts import (
    PilotPointEvent,
    PilotSupportReason,
    ServeStrengthArtifact,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
)
from inci_tennis_expert.pilot_static_model import (
    evaluate_static_outcome,
    evaluate_static_state,
)
from inci_tennis_expert.tennis_score import apply_point
from inci_tennis_expert.win_probability import standard_bo3_live_probabilities


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _state() -> TennisState:
    return TennisState(
        provider_source_id="primary",
        revision_domain_id="primary-revisions",
        source_lineage_sha256=SHA_A,
        provider_match_id="provider-match-1",
        home_player_id="provider-home",
        away_player_id="provider-away",
        scheduled_start_wall_ns=9_000,
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        status=MatchStatus.LIVE,
        termination_kind=TerminationKind.NONE,
        winner=None,
        retired_side=None,
        completed_sets=(),
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
        snapshot_complete=True,
        last_provider_event_id="event-1",
        last_event_semantic_sha256=SHA_B,
        correction_lineage_sha256=SHA_C,
        last_source_wall_ns=1_000,
        last_source_generated_wall_ns=1_000,
        last_received_monotonic_ns=1_000,
        last_clock_uncertainty_ns=0,
        block_reason=None,
        expected_revision=None,
        observed_revision=None,
        blocked_event_semantic_sha256=None,
        blocked_received_monotonic_ns=None,
    )


def _point_event(
    *,
    canonical_match_id: str = "match-1",
    after_state: TennisState | None = None,
) -> PilotPointEvent:
    before = _state()
    if after_state is None:
        after_state = apply_point(
            before,
            ProviderPoint(
                provider_source_id=before.provider_source_id,
                revision_domain_id=before.revision_domain_id,
                source_lineage_sha256=before.source_lineage_sha256,
                provider_event_id="event-2",
                provider_match_id=before.provider_match_id,
                home_player_id=before.home_player_id,
                away_player_id=before.away_player_id,
                scheduled_start_wall_ns=before.scheduled_start_wall_ns,
                match_format=before.match_format,
                correction_epoch=before.correction_epoch,
                revision=before.revision + 1,
                point_winner=PlayerSide.HOME,
                server_before_point=before.server_for_next_point,
                source_wall_ns=2_000,
                source_generated_wall_ns=2_000,
                received_monotonic_ns=2_000,
                clock_uncertainty_ns=0,
            ),
        ).state
    return PilotPointEvent(
        canonical_match_id=canonical_match_id,
        point_id="point-1",
        sequence_number=1,
        before_state=before,
        after_state=after_state,
        server=PlayerSide.HOME,
        winner=PlayerSide.HOME,
        consensus_epoch=0,
        consensus_transition_sha256=SHA_D,
        supporting_source_lineage_sha256s=(SHA_A, SHA_B),
        received_wall_ns=2_000,
        accepted_monotonic_ns=2_000,
    )


def _serve_artifact(
    home_serve_point_probability: Decimal = Decimal("0.64"),
    away_serve_point_probability: Decimal = Decimal("0.61"),
    **overrides: object,
) -> ServeStrengthArtifact:
    values: dict[str, object] = {
        "version": "pilot-serve-v1",
        "target_canonical_match_id": "match-1",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": ("match-0",),
        "training_match_ids_sha256": compute_training_match_ids_sha256(("match-0",)),
        "source_data_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "code_sha256": SHA_D,
        "home_serve_point_probability": home_serve_point_probability,
        "away_serve_point_probability": away_serve_point_probability,
    }
    values.update(overrides)
    values["training_match_ids_sha256"] = compute_training_match_ids_sha256(
        values["training_match_ids"]  # type: ignore[arg-type]
    )
    return ServeStrengthArtifact(
        artifact_sha256=compute_serve_strength_artifact_sha256(**values),  # type: ignore[arg-type]
        **values,  # type: ignore[arg-type]
    )


def _match_clinching_event() -> PilotPointEvent:
    before = replace(
        _state(),
        completed_sets=(SetScore(6, 0, None, None),),
        games_home=5,
        games_away=0,
        points_home=ScoreValue.FORTY,
        points_away=ScoreValue.LOVE,
    )
    after = apply_point(
        before,
        ProviderPoint(
            provider_source_id=before.provider_source_id,
            revision_domain_id=before.revision_domain_id,
            source_lineage_sha256=before.source_lineage_sha256,
            provider_event_id="event-2",
            provider_match_id=before.provider_match_id,
            home_player_id=before.home_player_id,
            away_player_id=before.away_player_id,
            scheduled_start_wall_ns=before.scheduled_start_wall_ns,
            match_format=before.match_format,
            correction_epoch=before.correction_epoch,
            revision=before.revision + 1,
            point_winner=PlayerSide.HOME,
            server_before_point=before.server_for_next_point,
            source_wall_ns=2_000,
            source_generated_wall_ns=2_000,
            received_monotonic_ns=2_000,
            clock_uncertainty_ns=0,
        ),
    ).state
    return PilotPointEvent(
        canonical_match_id="match-1",
        point_id="point-1",
        sequence_number=1,
        before_state=before,
        after_state=after,
        server=PlayerSide.HOME,
        winner=PlayerSide.HOME,
        consensus_epoch=0,
        consensus_transition_sha256=SHA_D,
        supporting_source_lineage_sha256s=(SHA_A, SHA_B),
        received_wall_ns=2_000,
        accepted_monotonic_ns=2_000,
    )


class StaticPilotModelTests(unittest.TestCase):
    def test_completed_event_api_delegates_to_identical_state_evaluation(self) -> None:
        event = _point_event()
        artifact = _serve_artifact()

        event_estimate = evaluate_static_outcome(event, artifact)
        state_estimate = evaluate_static_state(
            canonical_match_id=event.canonical_match_id,
            state=event.after_state,
            artifact=artifact,
        )
        self.assertEqual(event_estimate, state_estimate)
        self.assertEqual(
            state_estimate.home_current_set_probability,
            Decimal("0.62431489338675756379549950781453041851047604816177"),
        )
        self.assertEqual(
            state_estimate.home_match_probability,
            Decimal("0.65962452925784292453809878578520856041676173413651"),
        )

    def test_matches_existing_exact_live_probability(self) -> None:
        state = _point_event().after_state
        artifact = _serve_artifact(Decimal("0.64"), Decimal("0.61"))
        expected = standard_bo3_live_probabilities(
            state,
            Decimal("0.64"),
            Decimal("0.61"),
        )

        actual = evaluate_static_outcome(_point_event(after_state=state), artifact)

        self.assertTrue(actual.supported)
        self.assertEqual(actual.home_match_probability, expected.home_match_probability)
        self.assertEqual(
            actual.home_current_set_probability,
            expected.home_current_set_probability,
        )
        expected_next_point = (
            Decimal("0.64")
            if state.server_for_next_point is PlayerSide.HOME
            else Decimal("1") - Decimal("0.61")
        )
        self.assertEqual(actual.home_next_point_probability, expected_next_point)

    def test_rejects_artifact_not_frozen_before_target_match(self) -> None:
        artifact = _serve_artifact()
        object.__setattr__(artifact, "cutoff_wall_ns", 9_000)

        actual = evaluate_static_outcome(_point_event(), artifact)

        self.assertFalse(actual.supported)
        self.assertEqual(actual.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH)

    def test_rejects_artifact_for_other_match_or_schedule(self) -> None:
        match_mismatch = evaluate_static_outcome(
            _point_event(),
            _serve_artifact(target_canonical_match_id="match-other"),
        )
        schedule_mismatch = evaluate_static_outcome(
            _point_event(),
            _serve_artifact(
                target_scheduled_start_wall_ns=9_001,
                cutoff_wall_ns=9_000,
            ),
        )

        self.assertEqual(
            match_mismatch.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH
        )
        self.assertEqual(
            schedule_mismatch.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH
        )

    def test_rejects_tampered_artifact_payload(self) -> None:
        artifact = _serve_artifact()
        object.__setattr__(
            artifact, "home_serve_point_probability", Decimal("0.65")
        )

        actual = evaluate_static_outcome(_point_event(), artifact)

        self.assertFalse(actual.supported)
        self.assertEqual(actual.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH)

    def test_returns_typed_unsupported_for_match_clinching_after_state(self) -> None:
        event = _match_clinching_event()
        self.assertIsNone(event.after_state.server_for_next_point)

        actual = evaluate_static_outcome(event, _serve_artifact())

        self.assertFalse(actual.supported)
        self.assertEqual(actual.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH)


if __name__ == "__main__":
    unittest.main()
