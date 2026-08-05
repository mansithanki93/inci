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
    TennisState,
    TerminationKind,
)
from inci_tennis_expert.tennis_score import apply_point

from inci_tennis_expert.pilot_contracts import (
    PilotContractError,
    PilotAction,
    PilotImmediateAction,
    PilotExecutionScenario,
    PilotPointEvent,
    make_pilot_policy_estimate,
    ServeStrengthArtifact,
    compute_execution_scenario_sha256,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
    pilot_contract_sha256,
)


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


def _valid_event() -> PilotPointEvent:
    before = _state()
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
            correction_epoch=0,
            revision=2,
            point_winner=PlayerSide.HOME,
            server_before_point=PlayerSide.HOME,
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


class PilotPointEventTests(unittest.TestCase):
    def test_requires_one_legal_point_and_two_independent_lineages(self) -> None:
        before = _state()
        with self.assertRaisesRegex(PilotContractError, "^point_transition$"):
            PilotPointEvent(
                canonical_match_id="match-1",
                point_id="point-1",
                sequence_number=1,
                before_state=before,
                after_state=before,
                server=PlayerSide.HOME,
                winner=PlayerSide.HOME,
                consensus_epoch=0,
                consensus_transition_sha256=SHA_D,
                supporting_source_lineage_sha256s=(SHA_A, SHA_B),
                received_wall_ns=2_000,
                accepted_monotonic_ns=2_000,
            )

    def test_canonical_digest_is_stable(self) -> None:
        event = _valid_event()
        self.assertEqual(pilot_contract_sha256(event), pilot_contract_sha256(event))

    def test_rejects_provenance_or_revision_not_equal_to_exact_successor(self) -> None:
        event = _valid_event()
        with self.assertRaisesRegex(PilotContractError, "^point_transition$"):
            PilotPointEvent(
                **{
                    **{name: getattr(event, name) for name in event.__dataclass_fields__},
                    "after_state": replace(event.after_state, revision=event.after_state.revision + 1),
                }
            )

    def test_rejects_bo5_even_when_the_score_advance_is_legal(self) -> None:
        event = _valid_event()
        before = replace(
            event.before_state,
            match_format=MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
        )
        after = apply_point(
            before,
            ProviderPoint(
                provider_source_id=before.provider_source_id,
                revision_domain_id=before.revision_domain_id,
                source_lineage_sha256=before.source_lineage_sha256,
                provider_event_id="event-bo5",
                provider_match_id=before.provider_match_id,
                home_player_id=before.home_player_id,
                away_player_id=before.away_player_id,
                scheduled_start_wall_ns=before.scheduled_start_wall_ns,
                match_format=before.match_format,
                correction_epoch=before.correction_epoch,
                revision=before.revision + 1,
                point_winner=PlayerSide.HOME,
                server_before_point=PlayerSide.HOME,
                source_wall_ns=before.last_source_wall_ns + 1,
                source_generated_wall_ns=before.last_source_generated_wall_ns + 1,
                received_monotonic_ns=before.last_received_monotonic_ns + 1,
                clock_uncertainty_ns=before.last_clock_uncertainty_ns,
            ),
        ).state
        with self.assertRaisesRegex(PilotContractError, "^point_transition$"):
            PilotPointEvent(
                **{
                    **{name: getattr(event, name) for name in event.__dataclass_fields__},
                    "before_state": before,
                    "after_state": after,
                }
            )


class PilotArtifactTests(unittest.TestCase):
    def test_serve_artifact_rejects_digest_or_target_leakage(self) -> None:
        values = dict(
            version="pilot-serve-v1",
            target_canonical_match_id="match-1",
            target_scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999,
            training_match_ids=("match-0",),
            training_match_ids_sha256=compute_training_match_ids_sha256(("match-0",)),
            source_data_sha256=SHA_B,
            feature_definition_sha256=SHA_C,
            code_sha256=SHA_D,
            home_serve_point_probability=Decimal("0.64"),
            away_serve_point_probability=Decimal("0.61"),
        )
        digest = compute_serve_strength_artifact_sha256(**values)
        artifact = ServeStrengthArtifact(artifact_sha256=digest, **values)
        self.assertEqual(artifact.artifact_sha256, digest)
        with self.assertRaisesRegex(PilotContractError, "^artifact_sha256$"):
            ServeStrengthArtifact(artifact_sha256=SHA_A, **values)

    def test_execution_scenario_is_digest_bound_and_has_fixed_horizon(self) -> None:
        values = dict(
            version="pilot-execution-v1",
            decision_to_arrival_ns=100,
            maximum_pair_latency_ns=50,
            flat_wait_horizon_ns=10_000,
            holding_horizon_ns=300_000_000_000,
        )
        scenario = PilotExecutionScenario(
            artifact_sha256=compute_execution_scenario_sha256(**values),
            **values,
        )
        self.assertEqual(scenario.holding_horizon_ns, 300_000_000_000)
        with self.assertRaisesRegex(PilotContractError, "^holding_horizon_ns$"):
            PilotExecutionScenario(
                artifact_sha256=compute_execution_scenario_sha256(
                    **{**values, "holding_horizon_ns": 1}
                ),
                **{**values, "holding_horizon_ns": 1},
            )
