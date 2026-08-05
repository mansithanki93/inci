from __future__ import annotations

from decimal import Decimal
import json
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
    ServeStrengthArtifact,
    canonical_pilot_contract_bytes,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
    pilot_contract_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    compute_dynamic_point_artifact_sha256,
)
from inci_tennis_expert.pilot_training import (
    PilotTrainingError,
    canonical_dynamic_point_artifact_json_bytes,
    fit_dynamic_point_parameters,
    freeze_dynamic_point_artifact,
)
from inci_tennis_expert.tennis_score import apply_point


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _initial_state(
    match_id: str,
    scheduled_start_wall_ns: int,
    *,
    revision: int = 1,
) -> TennisState:
    return TennisState(
        provider_source_id="primary",
        revision_domain_id="primary-revisions",
        source_lineage_sha256=SHA_A,
        provider_match_id=f"provider-{match_id}",
        home_player_id=f"home-{match_id}",
        away_player_id=f"away-{match_id}",
        scheduled_start_wall_ns=scheduled_start_wall_ns,
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
        revision=revision,
        snapshot_complete=True,
        last_provider_event_id=f"{match_id}-event-0",
        last_event_semantic_sha256=SHA_B,
        correction_lineage_sha256=SHA_C,
        last_source_wall_ns=scheduled_start_wall_ns - 100,
        last_source_generated_wall_ns=scheduled_start_wall_ns - 100,
        last_received_monotonic_ns=1,
        last_clock_uncertainty_ns=0,
        block_reason=None,
        expected_revision=None,
        observed_revision=None,
        blocked_event_semantic_sha256=None,
        blocked_received_monotonic_ns=None,
    )


def _match_events(
    match_id: str,
    scheduled_start_wall_ns: int,
    winners: tuple[PlayerSide, ...] = (PlayerSide.HOME,),
    *,
    first_sequence_number: int = 1,
) -> tuple[PilotPointEvent, ...]:
    state = _initial_state(
        match_id,
        scheduled_start_wall_ns,
        revision=first_sequence_number,
    )
    events: list[PilotPointEvent] = []
    for sequence_number, winner in enumerate(
        winners,
        start=first_sequence_number,
    ):
        server = state.server_for_next_point
        assert server in (PlayerSide.HOME, PlayerSide.AWAY)
        point_id = f"{match_id}-point-{sequence_number}"
        after = apply_point(
            state,
            ProviderPoint(
                provider_source_id=state.provider_source_id,
                revision_domain_id=state.revision_domain_id,
                source_lineage_sha256=state.source_lineage_sha256,
                provider_event_id=f"{match_id}-event-{sequence_number}",
                provider_match_id=state.provider_match_id,
                home_player_id=state.home_player_id,
                away_player_id=state.away_player_id,
                scheduled_start_wall_ns=state.scheduled_start_wall_ns,
                match_format=state.match_format,
                correction_epoch=state.correction_epoch,
                revision=state.revision + 1,
                point_winner=winner,
                server_before_point=server,
                source_wall_ns=scheduled_start_wall_ns + sequence_number,
                source_generated_wall_ns=scheduled_start_wall_ns + sequence_number,
                received_monotonic_ns=sequence_number + 1,
                clock_uncertainty_ns=0,
            ),
        ).state
        events.append(
            PilotPointEvent(
                canonical_match_id=match_id,
                point_id=point_id,
                sequence_number=sequence_number,
                before_state=state,
                after_state=after,
                server=server,
                winner=winner,
                consensus_epoch=0,
                consensus_transition_sha256=SHA_D,
                supporting_source_lineage_sha256s=(SHA_A, SHA_B),
                received_wall_ns=scheduled_start_wall_ns + sequence_number,
                accepted_monotonic_ns=sequence_number + 1,
            )
        )
        state = after
    return tuple(events)


def _serve_artifact(
    match_id: str,
    scheduled_start_wall_ns: int,
    *,
    home: Decimal = Decimal("0.5"),
    away: Decimal = Decimal("0.5"),
) -> ServeStrengthArtifact:
    training_ids = (f"prior-{match_id}",)
    values = {
        "version": "pilot-serve-v1",
        "target_canonical_match_id": match_id,
        "target_scheduled_start_wall_ns": scheduled_start_wall_ns,
        "cutoff_wall_ns": scheduled_start_wall_ns - 1,
        "training_match_ids": training_ids,
        "training_match_ids_sha256": compute_training_match_ids_sha256(
            training_ids
        ),
        "source_data_sha256": SHA_A,
        "feature_definition_sha256": SHA_B,
        "code_sha256": SHA_C,
        "home_serve_point_probability": home,
        "away_serve_point_probability": away,
    }
    return ServeStrengthArtifact(
        artifact_sha256=compute_serve_strength_artifact_sha256(**values),
        **values,
    )


def _candidate(
    *,
    initial: tuple[Decimal, Decimal, Decimal] = (
        Decimal("0.2"),
        Decimal("0.6"),
        Decimal("0.2"),
    ),
    offsets: tuple[Decimal, Decimal, Decimal] = (
        Decimal("-0.5"),
        Decimal("0"),
        Decimal("0.5"),
    ),
) -> DynamicParameterCandidate:
    return DynamicParameterCandidate(
        transition_matrix=(
            (Decimal("1"), Decimal("0"), Decimal("0")),
            (Decimal("0"), Decimal("1"), Decimal("0")),
            (Decimal("0"), Decimal("0"), Decimal("1")),
        ),
        home_initial_weights=initial,
        away_initial_weights=initial,
        logit_offsets=offsets,
    )


def _equal_score_candidates() -> tuple[
    DynamicParameterCandidate, DynamicParameterCandidate
]:
    candidates = (
        _candidate(
            initial=(Decimal("0.1"), Decimal("0.8"), Decimal("0.1")),
            offsets=(Decimal("0"), Decimal("0"), Decimal("0")),
        ),
        _candidate(
            initial=(Decimal("0.3"), Decimal("0.4"), Decimal("0.3")),
            offsets=(Decimal("0"), Decimal("0"), Decimal("0")),
        ),
    )
    return tuple(
        sorted(candidates, key=canonical_pilot_contract_bytes)
    )  # type: ignore[return-value]


def _fixture() -> dict[str, object]:
    return {
        "events": _match_events("match-a", 1_000)
        + _match_events("match-b", 2_000),
        "training_match_ids": ("match-a",),
        "validation_match_ids": ("match-b",),
        "candidates": (_candidate(),),
        "serve_strength_artifacts": (
            _serve_artifact("match-a", 1_000),
            _serve_artifact("match-b", 2_000),
        ),
    }


class PilotTrainingTests(unittest.TestCase):
    def test_unpartitioned_future_match_is_rejected_before_scoring(self) -> None:
        values = _fixture()
        values["events"] = values["events"] + _match_events(  # type: ignore[operator]
            "future-match", 3_000
        )

        with self.assertRaisesRegex(PilotTrainingError, "^partition_coverage$"):
            fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_training_partition_must_precede_validation_partition(self) -> None:
        values = _fixture()
        values["events"] = _match_events("match-a", 3_000) + _match_events(
            "match-b", 2_000
        )
        values["serve_strength_artifacts"] = (
            _serve_artifact("match-a", 3_000),
            _serve_artifact("match-b", 2_000),
        )

        with self.assertRaisesRegex(PilotTrainingError, "^partition_chronology$"):
            fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_equal_scores_choose_lexicographically_first_candidate(self) -> None:
        first, second = _equal_score_candidates()
        values = _fixture()
        values["candidates"] = (second, first)

        actual = fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

        self.assertEqual(actual.selected_candidate, first)
        self.assertEqual(
            actual.canonical_key,
            canonical_pilot_contract_bytes(first).decode("ascii"),
        )

    def test_partitions_are_nonempty_disjoint_and_exact(self) -> None:
        cases = (
            ((), ("match-b",), "match_partitions"),
            (("match-a",), (), "match_partitions"),
            (("match-a",), ("match-a", "match-b"), "match_partitions"),
            (("match-a", "missing"), ("match-b",), "partition_coverage"),
        )
        for training, validation, code in cases:
            with self.subTest(code=code, training=training, validation=validation):
                values = _fixture()
                values["training_match_ids"] = training
                values["validation_match_ids"] = validation
                with self.assertRaisesRegex(PilotTrainingError, f"^{code}$"):
                    fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_point_sequence_is_strict_and_state_contiguous(self) -> None:
        reversed_events = _match_events(
            "match-a", 1_000, (PlayerSide.HOME, PlayerSide.AWAY)
        )
        first_event = _match_events("match-a", 1_000)
        gap_events = first_event + _match_events(
            "match-a",
            1_000,
            first_sequence_number=3,
        )
        discontinuous_events = first_event + _match_events(
            "match-a",
            1_000,
            first_sequence_number=2,
        )
        cases = (
            (reversed_events[1], reversed_events[0]),
            gap_events,
            discontinuous_events,
        )
        for malformed in cases:
            with self.subTest(sequence=tuple(row.sequence_number for row in malformed)):
                values = _fixture()
                values["events"] = malformed + _match_events("match-b", 2_000)
                with self.assertRaisesRegex(PilotTrainingError, "^point_sequence$"):
                    fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_serve_prior_map_is_complete_before_candidate_validation(self) -> None:
        extra = _serve_artifact("extra-match", 1_500)
        cases = (
            ((_serve_artifact("match-a", 1_000),), "serve_artifact_missing"),
            (
                (
                    _serve_artifact("match-a", 1_000),
                    _serve_artifact("match-b", 2_000),
                    _serve_artifact("match-b", 2_000),
                ),
                "serve_artifact_duplicate",
            ),
            (
                (
                    _serve_artifact("match-a", 1_000),
                    _serve_artifact("match-b", 2_000),
                    extra,
                ),
                "serve_artifact_coverage",
            ),
        )
        for artifacts, code in cases:
            with self.subTest(code=code):
                values = _fixture()
                values["candidates"] = ()
                values["serve_strength_artifacts"] = artifacts
                with self.assertRaisesRegex(PilotTrainingError, f"^{code}$"):
                    fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_serve_priors_must_be_authentic_prior_and_non_self_including(self) -> None:
        post_start = _serve_artifact("match-a", 1_000)
        object.__setattr__(post_start, "cutoff_wall_ns", 1_000)
        self_including = _serve_artifact("match-a", 1_000)
        object.__setattr__(self_including, "training_match_ids", ("match-a",))
        tampered = _serve_artifact("match-a", 1_000)
        object.__setattr__(
            tampered, "home_serve_point_probability", Decimal("0.6")
        )
        structurally_forged = _serve_artifact("match-a", 1_000)
        object.__setattr__(
            structurally_forged, "training_match_ids", ("unrelated-prior",)
        )
        object.__setattr__(
            structurally_forged, "training_match_ids_sha256", SHA_D
        )
        object.__setattr__(
            structurally_forged,
            "artifact_sha256",
            compute_serve_strength_artifact_sha256(
                version=structurally_forged.version,
                target_canonical_match_id=structurally_forged.target_canonical_match_id,
                target_scheduled_start_wall_ns=(
                    structurally_forged.target_scheduled_start_wall_ns
                ),
                cutoff_wall_ns=structurally_forged.cutoff_wall_ns,
                training_match_ids=structurally_forged.training_match_ids,
                training_match_ids_sha256=structurally_forged.training_match_ids_sha256,
                source_data_sha256=structurally_forged.source_data_sha256,
                feature_definition_sha256=structurally_forged.feature_definition_sha256,
                code_sha256=structurally_forged.code_sha256,
                home_serve_point_probability=(
                    structurally_forged.home_serve_point_probability
                ),
                away_serve_point_probability=(
                    structurally_forged.away_serve_point_probability
                ),
            ),
        )
        cases = (
            (post_start, "serve_artifact_post_start"),
            (self_including, "serve_artifact_self_including"),
            (tampered, "serve_artifact_authenticity"),
            (structurally_forged, "serve_artifact_authenticity"),
        )
        for artifact, code in cases:
            with self.subTest(code=code):
                values = _fixture()
                values["candidates"] = ()
                values["serve_strength_artifacts"] = (
                    artifact,
                    _serve_artifact("match-b", 2_000),
                )
                with self.assertRaisesRegex(PilotTrainingError, f"^{code}$"):
                    fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_structurally_tampered_event_is_rejected_before_candidate_validation(
        self,
    ) -> None:
        values = _fixture()
        events = values["events"]
        assert type(events) is tuple
        object.__setattr__(events[-1], "winner", PlayerSide.AWAY)
        values["candidates"] = ()

        with self.assertRaisesRegex(PilotTrainingError, "^event_authenticity$"):
            fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_nested_state_block_metadata_is_revalidated(self) -> None:
        values = _fixture()
        events = values["events"]
        assert type(events) is tuple
        object.__setattr__(events[0].before_state, "expected_revision", 123)
        values["candidates"] = ()

        with self.assertRaisesRegex(PilotTrainingError, "^event_authenticity$"):
            fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_nested_completed_set_contract_is_revalidated(self) -> None:
        values = _fixture()
        events = values["events"]
        assert type(events) is tuple
        forged_set = SetScore(6, 0, None, None)
        object.__setattr__(forged_set, "games_away", False)
        object.__setattr__(
            events[0].before_state,
            "completed_sets",
            (forged_set,),
        )
        object.__setattr__(
            events[0].after_state,
            "completed_sets",
            (forged_set,),
        )
        values["candidates"] = ()

        with self.assertRaisesRegex(PilotTrainingError, "^event_authenticity$"):
            fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_structurally_tampered_candidate_is_rejected_before_scoring(
        self,
    ) -> None:
        candidate = _candidate()
        object.__setattr__(
            candidate,
            "home_initial_weights",
            (Decimal("1"), Decimal("1"), Decimal("0")),
        )
        values = _fixture()
        values["candidates"] = (candidate,)

        with self.assertRaisesRegex(PilotTrainingError, "^candidate_grid$"):
            fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

    def test_predictions_are_pre_observation_and_beliefs_reset_each_match(self) -> None:
        symmetric = _candidate(
            offsets=(
                Decimal("-1.386294361119890618834464243"),
                Decimal("0"),
                Decimal("1.386294361119890618834464243"),
            )
        )
        values = _fixture()
        values["events"] = (
            values["events"] + _match_events("match-c", 3_000)  # type: ignore[operator]
        )
        values["validation_match_ids"] = ("match-b", "match-c")
        values["serve_strength_artifacts"] = (
            _serve_artifact("match-a", 1_000),
            _serve_artifact("match-b", 2_000),
            _serve_artifact("match-c", 3_000),
        )
        values["candidates"] = (symmetric,)

        actual = fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

        self.assertAlmostEqual(actual.training_brier_score, Decimal("0.25"), places=25)
        self.assertEqual(actual.training_brier_score, actual.validation_brier_score)
        self.assertAlmostEqual(
            actual.training_log_loss,
            actual.validation_log_loss,
            places=75,
        )
        self.assertEqual(actual.training_row_count, 1)
        self.assertEqual(actual.validation_row_count, 2)

    def test_training_result_binds_sources_partitions_and_fingerprints(self) -> None:
        actual = fit_dynamic_point_parameters(**_fixture())  # type: ignore[arg-type]

        self.assertEqual(actual.training_match_ids, ("match-a",))
        self.assertEqual(actual.validation_match_ids, ("match-b",))
        self.assertEqual(actual.training_match_count, 1)
        self.assertEqual(actual.validation_match_count, 1)
        self.assertEqual(actual.cutoff_wall_ns, 2_001)
        for digest in (
            actual.source_data_sha256,
            actual.serve_strength_artifacts_sha256,
            actual.feature_definition_sha256,
            actual.code_sha256,
        ):
            self.assertRegex(digest, "^[0-9a-f]{64}$")

    def test_cutoff_covers_all_supplied_wall_time_provenance(self) -> None:
        values = _fixture()
        events = values["events"]
        assert type(events) is tuple
        object.__setattr__(events[-1], "received_wall_ns", 1)

        actual = fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]

        self.assertEqual(actual.cutoff_wall_ns, 2_001)

    def test_freeze_binds_result_to_absent_strictly_future_target(self) -> None:
        result = fit_dynamic_point_parameters(**_fixture())  # type: ignore[arg-type]

        artifact = freeze_dynamic_point_artifact(
            training_result=result,
            target_canonical_match_id="future-match",
            target_scheduled_start_wall_ns=3_000,
        )
        encoded = canonical_dynamic_point_artifact_json_bytes(artifact)

        self.assertEqual(artifact.selected, result.selected_candidate)
        self.assertEqual(artifact.training_match_ids, result.training_match_ids)
        self.assertEqual(artifact.validation_match_ids, result.validation_match_ids)
        self.assertEqual(artifact.source_data_sha256, result.source_data_sha256)
        self.assertEqual(artifact.cutoff_wall_ns, result.cutoff_wall_ns)
        self.assertNotEqual(artifact.artifact_sha256, artifact.source_data_sha256)
        self.assertEqual(encoded, canonical_dynamic_point_artifact_json_bytes(artifact))
        self.assertIsInstance(json.loads(encoded), dict)

    def test_freeze_rejects_fitted_or_not_strictly_future_target(self) -> None:
        result = fit_dynamic_point_parameters(**_fixture())  # type: ignore[arg-type]
        cases = (
            ("match-a", 3_000, "target_match_partition"),
            ("future-match", result.cutoff_wall_ns, "target_match_chronology"),
        )
        for match_id, scheduled_start, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(PilotTrainingError, f"^{code}$"):
                    freeze_dynamic_point_artifact(
                        training_result=result,
                        target_canonical_match_id=match_id,
                        target_scheduled_start_wall_ns=scheduled_start,
                    )

    def test_freeze_revalidates_training_result_invariants(self) -> None:
        result = fit_dynamic_point_parameters(**_fixture())  # type: ignore[arg-type]
        object.__setattr__(result, "training_match_count", 99)
        projection = {
            name: getattr(result, name)
            for name in result.__dataclass_fields__
            if name != "training_result_sha256"
        }
        object.__setattr__(
            result,
            "training_result_sha256",
            pilot_contract_sha256(projection),
        )

        with self.assertRaisesRegex(PilotTrainingError, "^training_result$"):
            freeze_dynamic_point_artifact(
                training_result=result,
                target_canonical_match_id="future-match",
                target_scheduled_start_wall_ns=3_000,
            )

    def test_canonical_artifact_bytes_revalidate_nested_candidate(self) -> None:
        result = fit_dynamic_point_parameters(**_fixture())  # type: ignore[arg-type]
        artifact = freeze_dynamic_point_artifact(
            training_result=result,
            target_canonical_match_id="future-match",
            target_scheduled_start_wall_ns=3_000,
        )
        matrix = artifact.selected.transition_matrix
        object.__setattr__(
            artifact.selected,
            "transition_matrix",
            (
                (Decimal("1"), Decimal("1"), Decimal("0")),
                matrix[1],
                matrix[2],
            ),
        )
        object.__setattr__(
            artifact,
            "artifact_sha256",
            compute_dynamic_point_artifact_sha256(
                version=artifact.version,
                target_canonical_match_id=artifact.target_canonical_match_id,
                target_scheduled_start_wall_ns=(
                    artifact.target_scheduled_start_wall_ns
                ),
                cutoff_wall_ns=artifact.cutoff_wall_ns,
                training_match_ids=artifact.training_match_ids,
                validation_match_ids=artifact.validation_match_ids,
                source_data_sha256=artifact.source_data_sha256,
                feature_definition_sha256=artifact.feature_definition_sha256,
                code_sha256=artifact.code_sha256,
                selected=artifact.selected,
            ),
        )

        with self.assertRaisesRegex(PilotTrainingError, "^dynamic_artifact$"):
            canonical_dynamic_point_artifact_json_bytes(artifact)

    def test_endpoint_serve_priors_precede_candidate_grid_validation(self) -> None:
        cases: list[tuple[str, ServeStrengthArtifact]] = []
        for field_name in (
            "home_serve_point_probability",
            "away_serve_point_probability",
        ):
            endpoint_one = _serve_artifact("match-a", 1_000)
            object.__setattr__(endpoint_one, field_name, Decimal("1"))
            values = {
                name: getattr(endpoint_one, name)
                for name in endpoint_one.__dataclass_fields__
                if name != "artifact_sha256"
            }
            object.__setattr__(
                endpoint_one,
                "artifact_sha256",
                compute_serve_strength_artifact_sha256(**values),
            )
            cases.append((f"{field_name}=1", endpoint_one))

            endpoint_zero = _serve_artifact("match-a", 1_000)
            object.__setattr__(endpoint_zero, field_name, Decimal("0"))
            values = {
                name: getattr(endpoint_zero, name)
                for name in endpoint_zero.__dataclass_fields__
                if name != "artifact_sha256"
            }
            object.__setattr__(
                endpoint_zero,
                "artifact_sha256",
                compute_serve_strength_artifact_sha256(**values),
            )
            cases.append((f"{field_name}=0", endpoint_zero))

        for label, endpoint_artifact in cases:
            with self.subTest(endpoint=label):
                values = _fixture()
                values["candidates"] = ()
                values["serve_strength_artifacts"] = (
                    endpoint_artifact,
                    _serve_artifact("match-b", 2_000),
                )
                with self.assertRaisesRegex(
                    PilotTrainingError,
                    "^serve_artifact_probability$",
                ):
                    fit_dynamic_point_parameters(**values)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
