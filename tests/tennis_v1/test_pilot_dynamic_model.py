from __future__ import annotations

from dataclasses import replace
from decimal import Context, Decimal, localcontext
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
from inci_tennis_expert.pilot_contracts import (
    PilotPointEvent,
    PilotSupportReason,
    ServeStrengthArtifact,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    DynamicPointArtifact,
    DynamicPointModel,
    DynamicPointModelError,
    compute_dynamic_point_artifact_sha256,
    evaluate_dynamic_state,
)
from inci_tennis_expert.pilot_static_model import evaluate_static_outcome
from inci_tennis_expert.tennis_score import apply_point
from inci_tennis_expert.win_probability import standard_bo3_live_probabilities


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _state(*, server: PlayerSide = PlayerSide.HOME) -> TennisState:
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
        server_for_next_point=server,
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


def _point(
    *,
    point_id: str = "point-1",
    server: PlayerSide = PlayerSide.HOME,
    winner: PlayerSide = PlayerSide.HOME,
    before_state: TennisState | None = None,
) -> PilotPointEvent:
    before = before_state if before_state is not None else _state(server=server)
    after = apply_point(
        before,
        ProviderPoint(
            provider_source_id=before.provider_source_id,
            revision_domain_id=before.revision_domain_id,
            source_lineage_sha256=before.source_lineage_sha256,
            provider_event_id=f"event-{point_id}",
            provider_match_id=before.provider_match_id,
            home_player_id=before.home_player_id,
            away_player_id=before.away_player_id,
            scheduled_start_wall_ns=before.scheduled_start_wall_ns,
            match_format=before.match_format,
            correction_epoch=before.correction_epoch,
            revision=before.revision + 1,
            point_winner=winner,
            server_before_point=server,
            source_wall_ns=2_000,
            source_generated_wall_ns=2_000,
            received_monotonic_ns=2_000,
            clock_uncertainty_ns=0,
        ),
    ).state
    return PilotPointEvent(
        canonical_match_id="match-1",
        point_id=point_id,
        sequence_number=before.revision,
        before_state=before,
        after_state=after,
        server=server,
        winner=winner,
        consensus_epoch=0,
        consensus_transition_sha256=SHA_D,
        supporting_source_lineage_sha256s=(SHA_A, SHA_B),
        received_wall_ns=2_000,
        accepted_monotonic_ns=2_000,
    )


def _serve_artifact(
    home: Decimal = Decimal("0.64"),
    away: Decimal = Decimal("0.61"),
) -> ServeStrengthArtifact:
    values = {
        "version": "pilot-serve-v1",
        "target_canonical_match_id": "match-1",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": ("match-0",),
        "training_match_ids_sha256": compute_training_match_ids_sha256(
            ("match-0",)
        ),
        "source_data_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "code_sha256": SHA_D,
        "home_serve_point_probability": home,
        "away_serve_point_probability": away,
    }
    return ServeStrengthArtifact(
        artifact_sha256=compute_serve_strength_artifact_sha256(**values),
        **values,
    )


def _candidate(
    offsets: tuple[Decimal, Decimal, Decimal] = (
        Decimal("-0.5"),
        Decimal("0"),
        Decimal("0.5"),
    ),
) -> DynamicParameterCandidate:
    return DynamicParameterCandidate(
        transition_matrix=(
            (Decimal("0.8"), Decimal("0.15"), Decimal("0.05")),
            (Decimal("0.1"), Decimal("0.8"), Decimal("0.1")),
            (Decimal("0.05"), Decimal("0.15"), Decimal("0.8")),
        ),
        home_initial_weights=(Decimal("0.2"), Decimal("0.6"), Decimal("0.2")),
        away_initial_weights=(Decimal("0.2"), Decimal("0.6"), Decimal("0.2")),
        logit_offsets=offsets,
    )


def _artifact(candidate: DynamicParameterCandidate) -> DynamicPointArtifact:
    values = {
        "version": "pilot-dynamic-v1",
        "target_canonical_match_id": "match-1",
        "target_scheduled_start_wall_ns": 9_000,
        "cutoff_wall_ns": 8_999,
        "training_match_ids": ("match-train",),
        "validation_match_ids": ("match-validation",),
        "source_data_sha256": SHA_A,
        "feature_definition_sha256": SHA_B,
        "code_sha256": SHA_C,
        "selected": candidate,
    }
    return DynamicPointArtifact(
        artifact_sha256=compute_dynamic_point_artifact_sha256(**values),
        **values,
    )


def _model(
    offsets: tuple[Decimal, Decimal, Decimal] = (
        Decimal("-0.5"),
        Decimal("0"),
        Decimal("0.5"),
    ),
    *,
    serve_artifact: ServeStrengthArtifact | None = None,
) -> DynamicPointModel:
    candidate = _candidate(offsets)
    return DynamicPointModel.initialize(
        serve_artifact=serve_artifact or _serve_artifact(),
        dynamic_artifact=_artifact(candidate),
    )


class DynamicPointModelTests(unittest.TestCase):
    def test_completed_event_api_delegates_to_identical_state_evaluation(self) -> None:
        event = _point()
        model = _model()

        event_estimate = model.evaluate(event)
        state_estimate = evaluate_dynamic_state(
            model=model,
            canonical_match_id=event.canonical_match_id,
            state=event.after_state,
        )
        self.assertEqual(event_estimate, state_estimate)
        self.assertEqual(
            state_estimate.home_next_point_probability,
            Decimal("0.636889562463792044056463464900472908367560738513468"),
        )
        self.assertEqual(
            state_estimate.home_match_probability,
            Decimal("0.597028280820923495924509774338320910849242641967439660"),
        )

    def test_candidate_rejects_tiny_excess_initial_mass_exactly(self) -> None:
        with self.assertRaisesRegex(
            DynamicPointModelError,
            "^home_initial_weights$",
        ):
            replace(
                _candidate(),
                home_initial_weights=(
                    Decimal("1"),
                    Decimal("1e-100"),
                    Decimal("0"),
                ),
            )

    def test_candidate_rejects_tiny_excess_transition_mass_exactly(self) -> None:
        candidate = _candidate()
        with self.assertRaisesRegex(
            DynamicPointModelError,
            "^transition_matrix$",
        ):
            replace(
                candidate,
                transition_matrix=(
                    (
                        Decimal("1"),
                        Decimal("1e-100"),
                        Decimal("0"),
                    ),
                    candidate.transition_matrix[1],
                    candidate.transition_matrix[2],
                ),
            )

    def test_positive_offset_beyond_decimal_emax_is_open_and_finite(self) -> None:
        model = _model(offsets=(Decimal("1e1000000"),) * 3)

        for side in (PlayerSide.HOME, PlayerSide.AWAY):
            for probability in model.state_serve_probabilities(side):
                self.assertTrue(probability.is_finite())
                self.assertGreater(probability, Decimal("0"))
                self.assertLess(probability, Decimal("1"))
        self.assertTrue(model.evaluate(_point()).supported)

    def test_negative_offset_beyond_decimal_emax_is_open_and_finite(self) -> None:
        model = _model(offsets=(Decimal("-1e1000000"),) * 3)

        for side in (PlayerSide.HOME, PlayerSide.AWAY):
            for probability in model.state_serve_probabilities(side):
                self.assertTrue(probability.is_finite())
                self.assertGreater(probability, Decimal("0"))
                self.assertLess(probability, Decimal("1"))
        self.assertTrue(model.evaluate(_point()).supported)

    def test_initialize_rejects_tampered_dynamic_artifact_payload(self) -> None:
        candidate = _candidate()
        cases = (
            ("cutoff", "cutoff_wall_ns", 8_998),
            ("partitions", "training_match_ids", ("other-training",)),
            (
                "selected",
                "selected",
                _candidate(
                    (
                        Decimal("-0.6"),
                        Decimal("0"),
                        Decimal("0.6"),
                    )
                ),
            ),
        )
        for label, field, value in cases:
            with self.subTest(tamper=label):
                artifact = _artifact(candidate)
                object.__setattr__(artifact, field, value)

                with self.assertRaisesRegex(
                    DynamicPointModelError,
                    "^artifact_mismatch$",
                ):
                    DynamicPointModel.initialize(
                        serve_artifact=_serve_artifact(),
                        dynamic_artifact=artifact,
                    )

    def test_server_win_moves_home_mass_upward(self) -> None:
        model = _model()
        before = model.belief

        next_model, after = model.observe(
            _point(server=PlayerSide.HOME, winner=PlayerSide.HOME)
        )

        self.assertGreater(after.home_weights[2], before.home_weights[2])
        self.assertEqual(after.away_weights, before.away_weights)
        self.assertEqual(sum(after.home_weights), Decimal("1"))
        self.assertEqual(model.belief, before)
        self.assertEqual(next_model.belief, after)

    def test_duplicate_point_is_rejected_without_state_change(self) -> None:
        model = _model()
        event = _point(point_id="point-1")
        model, _ = model.observe(event)
        frozen = model.belief

        with self.assertRaisesRegex(DynamicPointModelError, "^duplicate_point$"):
            model.observe(event)

        self.assertEqual(model.belief, frozen)

    def test_observation_uses_observed_server_not_next_server(self) -> None:
        before = replace(
            _state(server=PlayerSide.HOME),
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.LOVE,
        )
        event = _point(
            before_state=before,
            server=PlayerSide.HOME,
            winner=PlayerSide.HOME,
        )
        self.assertIs(event.after_state.server_for_next_point, PlayerSide.AWAY)
        model = _model()
        original = model.belief

        _, after = model.observe(event)

        self.assertNotEqual(after.home_weights, original.home_weights)
        self.assertEqual(after.away_weights, original.away_weights)

    def test_repeated_updates_are_deterministic_and_exactly_normalized(self) -> None:
        event = _point(server=PlayerSide.AWAY, winner=PlayerSide.HOME)

        first_model, first = _model().observe(event)
        second_model, second = _model().observe(event)

        self.assertEqual(first, second)
        self.assertEqual(first_model, second_model)
        self.assertEqual(sum(first.away_weights), Decimal("1"))
        self.assertTrue(
            all(
                weight.as_tuple().exponent >= -24
                for weight in first.away_weights
            )
        )

    def test_pre_observation_prediction_is_pure_and_does_not_leak_result(self) -> None:
        model = _model()
        home_win = _point(point_id="home-win", winner=PlayerSide.HOME)
        home_loss = _point(point_id="home-loss", winner=PlayerSide.AWAY)
        frozen = model.belief

        before_win = model.predictive_home_point_probability(home_win)
        before_loss = model.predictive_home_point_probability(home_loss)
        updated, _ = model.observe(home_win)

        self.assertEqual(before_win, before_loss)
        self.assertEqual(model.belief, frozen)
        self.assertNotEqual(
            updated.predictive_home_point_probability(
                _point(point_id="next-point", winner=PlayerSide.HOME)
            ),
            before_win,
        )

    def test_zero_offsets_equal_static_model(self) -> None:
        model = _model(offsets=(Decimal("0"),) * 3)
        event = _point()

        dynamic = model.evaluate(event)
        static = evaluate_static_outcome(event, _serve_artifact())

        self.assertEqual(
            dynamic.home_match_probability,
            static.home_match_probability,
        )
        self.assertEqual(
            dynamic.home_current_set_probability,
            static.home_current_set_probability,
        )
        self.assertEqual(
            dynamic.home_next_point_probability,
            static.home_next_point_probability,
        )

    def test_evaluate_integrates_all_state_pairs_and_uses_pair_extrema_as_bounds(self) -> None:
        model = _model()
        event = _point()
        home_probabilities = model.state_serve_probabilities(PlayerSide.HOME)
        away_probabilities = model.state_serve_probabilities(PlayerSide.AWAY)
        with localcontext(Context(prec=110)):
            weighted_match = Decimal("0")
            weighted_set = Decimal("0")
            pair_matches: list[Decimal] = []
            for home_index, home_weight in enumerate(model.belief.home_weights):
                for away_index, away_weight in enumerate(model.belief.away_weights):
                    pair = standard_bo3_live_probabilities(
                        event.after_state,
                        home_probabilities[home_index],
                        away_probabilities[away_index],
                    )
                    joint = home_weight * away_weight
                    weighted_match += joint * pair.home_match_probability
                    weighted_set += joint * pair.home_current_set_probability
                    pair_matches.append(pair.home_match_probability)

        actual = model.evaluate(event)

        self.assertEqual(actual.home_match_probability, weighted_match)
        self.assertEqual(actual.home_current_set_probability, weighted_set)
        self.assertEqual(actual.lower_home_match_probability, min(pair_matches))
        self.assertEqual(actual.upper_home_match_probability, max(pair_matches))

    def test_next_point_probability_uses_posterior_and_after_state_server(self) -> None:
        before = replace(
            _state(server=PlayerSide.HOME),
            points_home=ScoreValue.FORTY,
            points_away=ScoreValue.LOVE,
        )
        event = _point(before_state=before, server=PlayerSide.HOME, winner=PlayerSide.HOME)
        model, belief = _model().observe(event)
        away_probabilities = model.state_serve_probabilities(PlayerSide.AWAY)
        with localcontext(Context(prec=110)):
            expected = Decimal("1") - sum(
                (
                    weight * probability
                    for weight, probability in zip(
                        belief.away_weights,
                        away_probabilities,
                        strict=True,
                    )
                ),
                Decimal("0"),
            )

        actual = model.evaluate(event)

        self.assertEqual(actual.home_next_point_probability, expected)

    def test_extreme_offsets_still_produce_strict_finite_scorer_inputs(self) -> None:
        model = _model(
            offsets=(Decimal("-1e6"), Decimal("0"), Decimal("1e6"))
        )

        for side in (PlayerSide.HOME, PlayerSide.AWAY):
            for probability in model.state_serve_probabilities(side):
                self.assertTrue(probability.is_finite())
                self.assertGreater(probability, Decimal("0"))
                self.assertLess(probability, Decimal("1"))
        self.assertTrue(model.evaluate(_point()).supported)

    def test_evaluate_returns_typed_abstention_for_artifact_mismatch(self) -> None:
        model = _model()
        object.__setattr__(
            model.serve_artifact,
            "home_serve_point_probability",
            Decimal("0.65"),
        )

        actual = model.evaluate(_point())

        self.assertFalse(actual.supported)
        self.assertEqual(actual.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH)


if __name__ == "__main__":
    unittest.main()
