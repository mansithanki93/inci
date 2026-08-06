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
from inci_tennis_expert.live_paper_contracts import (
    LivePaperScoreAnchor,
    LivePaperPointTransition,
    LivePaperSupport,
    PaperScoreTrust,
    make_live_paper_anchor,
    make_live_paper_transition,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicPointArtifact,
    compute_dynamic_point_artifact_sha256,
)
from inci_tennis_expert.tennis_score import apply_point


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _api() -> object:
    try:
        from inci_tennis_expert import live_two_model
    except ImportError as error:
        raise AssertionError("live two-model API is missing") from error
    return live_two_model


def _state() -> TennisState:
    return TennisState(
        provider_source_id="fixture",
        revision_domain_id="fixture-local",
        source_lineage_sha256=SHA_A,
        provider_match_id="fixture-match",
        home_player_id="home",
        away_player_id="away",
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
        last_provider_event_id="capture-0",
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


def _anchor(state: TennisState, *, consensus_epoch: int = 1, correction_epoch: int = 0, rebase_epoch: int = 0) -> LivePaperScoreAnchor:
    return make_live_paper_anchor(
        canonical_match_id="match-1",
        state=state,
        trust=PaperScoreTrust.SINGLE_SOURCE_PAPER,
        supporting_lineage_sha256s=(SHA_A,),
        parent_receipt_sha256s=(SHA_D,),
        consensus_epoch=consensus_epoch,
        correction_epoch=correction_epoch,
        rebase_epoch=rebase_epoch,
        accepted_wall_ns=1_000,
        accepted_monotonic_ns=1_000,
        supporting_independent_lineage_ids=("lineage-a",),
        supporting_sources=(LivePaperSupport(SHA_D, SHA_A, "lineage-a", True, SHA_B),),
    )


def _transition(before: TennisState, *, ordinal: int, winner: PlayerSide = PlayerSide.HOME, consensus_epoch: int = 1, rebase_epoch: int = 0) -> LivePaperPointTransition:
    after = apply_point(
        before,
        ProviderPoint(
            provider_source_id=before.provider_source_id,
            revision_domain_id=before.revision_domain_id,
            source_lineage_sha256=before.source_lineage_sha256,
            provider_event_id=f"capture-{ordinal}",
            provider_match_id=before.provider_match_id,
            home_player_id=before.home_player_id,
            away_player_id=before.away_player_id,
            scheduled_start_wall_ns=before.scheduled_start_wall_ns,
            match_format=before.match_format,
            correction_epoch=before.correction_epoch,
            revision=before.revision + 1,
            point_winner=winner,
            server_before_point=before.server_for_next_point,
            source_wall_ns=1_000 + ordinal,
            source_generated_wall_ns=1_000 + ordinal,
            received_monotonic_ns=1_000 + ordinal,
            clock_uncertainty_ns=0,
        ),
    ).state
    return make_live_paper_transition(
        canonical_match_id="match-1",
        local_point_ordinal=ordinal,
        before_state=before,
        after_state=after,
        server=before.server_for_next_point,
        winner=winner,
        trust=PaperScoreTrust.SINGLE_SOURCE_PAPER,
        supporting_lineage_sha256s=(SHA_A,),
        parent_receipt_sha256s=(SHA_D,),
        consensus_epoch=consensus_epoch,
        correction_epoch=after.correction_epoch,
        rebase_epoch=rebase_epoch,
        accepted_wall_ns=1_000 + ordinal,
        accepted_monotonic_ns=1_000 + ordinal,
        supporting_independent_lineage_ids=("lineage-a",),
        supporting_sources=(LivePaperSupport(SHA_D, SHA_A, "lineage-a", True, SHA_B),),
    )


class LiveTwoModelTests(unittest.TestCase):
    def test_anchor_forecasts_both_models_before_a_new_point(self) -> None:
        api = _api()
        static, dynamic = api.build_operator_bootstrap_artifacts(
            canonical_match_id="match-1",
            scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999,
            home_serve_point_probability=Decimal("0.64"),
            away_serve_point_probability=Decimal("0.61"),
        )

        anchor = _anchor(_state())
        state, forecast = api.open_live_two_model(
            static_artifact=static,
            dynamic_artifact=dynamic,
            anchor=anchor,
            artifact_authority=api.LiveArtifactAuthority.OPERATOR_BOOTSTRAP,
        )

        self.assertEqual(state.local_point_ordinal, 0)
        self.assertTrue(forecast.model_1.supported)
        self.assertTrue(forecast.model_2.supported)
        self.assertEqual(forecast.artifact_authority.value, "OPERATOR_BOOTSTRAP")
        self.assertEqual(forecast.authority.value, "OPERATOR_BOOTSTRAP")
        self.assertEqual(forecast.edge_claim.value, "NO_EDGE_CLAIM")
        self.assertEqual(forecast.authority_label, "OPERATOR_BOOTSTRAP / NO_EDGE_CLAIM")
        self.assertEqual(forecast.forecast_label, "ANCHORED_PAPER")
        self.assertEqual(forecast.anchor_sha256, anchor.anchor_sha256)
        self.assertIsNone(forecast.transition_sha256)

    def test_bootstrap_artifacts_freeze_template_and_target_binding(self) -> None:
        api = _api()
        static, dynamic = api.build_operator_bootstrap_artifacts(
            canonical_match_id="match-1",
            scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999,
            home_serve_point_probability=Decimal("0.64"),
            away_serve_point_probability=Decimal("0.61"),
        )

        self.assertEqual(static.target_canonical_match_id, "match-1")
        self.assertEqual(static.cutoff_wall_ns, 8_999)
        self.assertEqual(
            dynamic.selected.transition_matrix,
            ((Decimal(".8"), Decimal(".15"), Decimal(".05")), (Decimal(".1"), Decimal(".8"), Decimal(".1")), (Decimal(".05"), Decimal(".15"), Decimal(".8"))),
        )
        self.assertEqual(dynamic.selected.home_initial_weights, (Decimal(".2"), Decimal(".6"), Decimal(".2")))
        self.assertEqual(dynamic.selected.logit_offsets, (Decimal("-.5"), Decimal("0"), Decimal(".5")))
        with self.assertRaisesRegex(api.LiveTwoModelError, "cutoff_wall_ns"):
            api.build_operator_bootstrap_artifacts(
                canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
                cutoff_wall_ns=9_000, home_serve_point_probability=Decimal(".64"),
                away_serve_point_probability=Decimal(".61"),
            )
        with self.assertRaisesRegex(api.LiveTwoModelError, "serve_probability"):
            api.build_operator_bootstrap_artifacts(
                canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
                cutoff_wall_ns=8_999, home_serve_point_probability=Decimal("NaN"),
                away_serve_point_probability=Decimal(".61"),
            )
        with self.assertRaisesRegex(api.LiveTwoModelError, "serve_probability"):
            api.build_operator_bootstrap_artifacts(
                canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
                cutoff_wall_ns=8_999, home_serve_point_probability=Decimal("1"),
                away_serve_point_probability=Decimal(".61"),
            )

    def test_bootstrap_artifacts_cannot_be_relabelled_as_trained(self) -> None:
        api = _api()
        static, dynamic = api.build_operator_bootstrap_artifacts(
            canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999, home_serve_point_probability=Decimal(".64"),
            away_serve_point_probability=Decimal(".61"),
        )

        with self.assertRaisesRegex(api.LiveTwoModelError, "artifact_authority"):
            api.open_live_two_model(
                static_artifact=static, dynamic_artifact=dynamic, anchor=_anchor(_state()),
                artifact_authority=api.LiveArtifactAuthority.TRAINED_ARTIFACT,
            )

    def test_a_mixed_bootstrap_pair_cannot_erase_bootstrap_authority(self) -> None:
        api = _api()
        static, bootstrap_dynamic = api.build_operator_bootstrap_artifacts(
            canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999, home_serve_point_probability=Decimal(".64"),
            away_serve_point_probability=Decimal(".61"),
        )
        values = {
            "version": "trained-dynamic-v1",
            "target_canonical_match_id": "match-1",
            "target_scheduled_start_wall_ns": 9_000,
            "cutoff_wall_ns": 8_999,
            "training_match_ids": ("trained-dynamic",),
            "validation_match_ids": ("trained-validation",),
            "source_data_sha256": SHA_A,
            "feature_definition_sha256": SHA_B,
            "code_sha256": SHA_C,
            "selected": bootstrap_dynamic.selected,
        }
        trained_dynamic = DynamicPointArtifact(
            artifact_sha256=compute_dynamic_point_artifact_sha256(**values), **values
        )

        with self.assertRaisesRegex(api.LiveTwoModelError, "artifact_authority"):
            api.open_live_two_model(
                static_artifact=static, dynamic_artifact=trained_dynamic,
                anchor=_anchor(_state()),
                artifact_authority=api.LiveArtifactAuthority.TRAINED_ARTIFACT,
            )

    def test_exact_transition_updates_only_the_server_belief_and_uses_after_state(self) -> None:
        api = _api()
        static, dynamic = api.build_operator_bootstrap_artifacts(
            canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999, home_serve_point_probability=Decimal(".64"),
            away_serve_point_probability=Decimal(".61"),
        )
        anchor = _anchor(_state())
        initial, _ = api.open_live_two_model(
            static_artifact=static, dynamic_artifact=dynamic, anchor=anchor,
            artifact_authority=api.LiveArtifactAuthority.OPERATOR_BOOTSTRAP,
        )
        accepted = _transition(initial.current_state, ordinal=1)

        next_state, forecast = api.apply_live_paper_transition(initial, accepted)

        self.assertEqual(next_state.local_point_ordinal, 1)
        self.assertNotEqual(next_state.dynamic_model.belief.home_weights, initial.dynamic_model.belief.home_weights)
        self.assertEqual(next_state.dynamic_model.belief.away_weights, initial.dynamic_model.belief.away_weights)
        self.assertEqual(next_state.current_state, accepted.after_state)
        self.assertTrue(forecast.model_1.supported)
        self.assertTrue(forecast.model_2.supported)
        self.assertEqual(forecast.anchor_sha256, anchor.anchor_sha256)
        self.assertEqual(forecast.transition_sha256, accepted.transition_sha256)

    def test_discontinuous_ordinal_fails_closed_and_rebase_resets_dynamic_belief(self) -> None:
        api = _api()
        static, dynamic = api.build_operator_bootstrap_artifacts(
            canonical_match_id="match-1", scheduled_start_wall_ns=9_000,
            cutoff_wall_ns=8_999, home_serve_point_probability=Decimal(".64"),
            away_serve_point_probability=Decimal(".61"),
        )
        initial, _ = api.open_live_two_model(
            static_artifact=static, dynamic_artifact=dynamic, anchor=_anchor(_state()),
            artifact_authority=api.LiveArtifactAuthority.OPERATOR_BOOTSTRAP,
        )
        accepted, _ = api.apply_live_paper_transition(initial, _transition(initial.current_state, ordinal=1))
        with self.assertRaisesRegex(api.LiveTwoModelError, "local_point_ordinal"):
            api.apply_live_paper_transition(accepted, _transition(accepted.current_state, ordinal=3))

        rebased_anchor = _anchor(
            accepted.current_state, consensus_epoch=7, correction_epoch=0, rebase_epoch=1
        )
        rebased, forecast = api.rebase_live_two_model(accepted, rebased_anchor)
        self.assertEqual(rebased.local_point_ordinal, 0)
        self.assertEqual(rebased.consensus_epoch, 7)
        self.assertEqual(rebased.correction_epoch, 0)
        self.assertEqual(rebased.rebase_epoch, 1)
        self.assertEqual(rebased.dynamic_model.belief.home_weights, dynamic.selected.home_initial_weights)
        self.assertEqual(forecast.forecast_label, "REBASED_PAPER")
        self.assertEqual(forecast.rebase_state, "REBASED_PAPER")
