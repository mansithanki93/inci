from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal, ROUND_DOWN, localcontext
from hashlib import sha256
from pathlib import Path
import unittest
from unittest import mock

from inci_tennis_expert.contracts import (
    BookLevel,
    ExpertEventKindV1,
    ExpertIgnoredObservationV1,
    ExpertObservationRejectedPayloadV1,
    ExpertRejectedDraftV1,
    ExpertRejectReasonV1,
    ExpertStateV1,
    ExpertSynchronizationAppliedPayloadV1,
    ExpertSynchronizationDraftV1,
    ExpertSynchronizationObservationV1,
    SyncInputKind,
    canonical_expert_bytes,
    expert_contract_sha256,
    expert_state_sha256,
)
from inci_tennis_expert.market_book import book_from_snapshot
from inci_tennis_expert.observation import (
    bind_expert_observation_drafts,
    normalize_expert_parent,
)
from inci_tennis_expert.reducer import (
    initial_expert_state,
    reduce_expert_parent,
)
from inci_tennis_expert.synchronizer import (
    synchronize,
    validate_synchronization_transition,
)
from tests.tennis_v1.test_expert_contracts import (
    book_snapshot,
    synchronization_input,
)
from tests.tennis_v1.test_expert_observation import (
    raw_parent,
    task6_artifacts,
)


SCHEMA_ROOT = (
    Path(__file__).resolve().parents[2]
    / "inci_tennis_expert"
    / "schemas"
)


def _book_input(
    ticker: str,
    *,
    canonical_match_id: str = "canonical-match-9",
    level_count: int = 1,
):
    levels = tuple(
        BookLevel(
            Decimal(1_000 - index) / Decimal(1_000),
            Decimal("10"),
        )
        for index in range(1, level_count + 1)
    )
    snapshot = book_snapshot(
        ticker=ticker,
        yes_bids=levels,
        no_bids=(),
    )
    transition = book_from_snapshot(snapshot)
    return synchronization_input(
        kind=SyncInputKind.BOOK_TRANSITION,
        canonical_match_id=canonical_match_id,
        ticker=ticker,
        previous_state_sha256=None,
        book_event=snapshot,
        book_transition=transition,
    )


def _synchronization_group(manifest, *evidence):
    return bind_expert_observation_drafts(
        manifest,
        raw_parent(),
        manifest.normalizers.fallback,
        tuple(ExpertSynchronizationDraftV1(item) for item in evidence),
    )


class Task6ReducerContractTests(unittest.TestCase):
    def test_expert_state_has_exact_ruled_field_order(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(ExpertStateV1)),
            (
                "schema_version",
                "session_id",
                "expert_manifest_sha256",
                "match_binding_universe_sha256",
                "sync_policy_sha256",
                "initial_synchronization_sha256",
                "synchronization",
                "rejected_parent_count",
                "halted",
                "halt_reason",
            ),
        )

    def test_reducer_surface_and_reason_vocabulary_exist(self) -> None:
        self.assertTrue(callable(initial_expert_state))
        self.assertTrue(callable(reduce_expert_parent))
        self.assertEqual(
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED.value,
            "synchronization_applied",
        )
        self.assertEqual(
            ExpertRejectReasonV1.PRIOR_OUTCOME_HALTED.value,
            "prior_outcome_halted",
        )

    def test_initial_state_is_exactly_bound_to_manifest_and_empty_sync(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        self.assertEqual(state.session_id, manifest.session_id)
        self.assertEqual(
            state.expert_manifest_sha256,
            manifest.manifest_sha256,
        )
        self.assertEqual(
            state.match_binding_universe_sha256,
            universe.universe_sha256,
        )
        self.assertEqual(
            state.sync_policy_sha256,
            expert_contract_sha256(policy),
        )
        self.assertEqual(
            state.initial_synchronization_sha256,
            expert_contract_sha256(state.synchronization),
        )
        self.assertEqual(state.rejected_parent_count, 0)
        self.assertFalse(state.halted)
        self.assertIsNone(state.halt_reason)

    def test_ignored_parent_leaves_state_byte_identical(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        observations = normalize_expert_parent(manifest, raw_parent())
        self.assertEqual(type(observations[0]), ExpertIgnoredObservationV1)
        reduction = reduce_expert_parent(state, observations)
        self.assertEqual(len(reduction.outcomes), 1)
        self.assertEqual(
            reduction.outcomes[0].event_kind,
            ExpertEventKindV1.OBSERVATION_IGNORED,
        )
        self.assertIs(reduction.final_state, state)
        self.assertEqual(
            canonical_expert_bytes(reduction.final_state),
            canonical_expert_bytes(state),
        )
        self.assertFalse(reduction.halt_required)

    def test_explicit_rejection_increments_once_and_halts(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        observations = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (
                ExpertRejectedDraftV1(
                    ExpertRejectReasonV1.PARENT_CONTRACT_INVALID
                ),
            ),
        )
        reduction = reduce_expert_parent(state, observations)
        outcome = reduction.outcomes[0]
        self.assertEqual(
            outcome.event_kind,
            ExpertEventKindV1.OBSERVATION_REJECTED,
        )
        self.assertEqual(
            outcome.payload.reason,
            ExpertRejectReasonV1.PARENT_CONTRACT_INVALID,
        )
        self.assertEqual(reduction.final_state.rejected_parent_count, 1)
        self.assertTrue(reduction.final_state.halted)
        self.assertEqual(
            reduction.final_state.halt_reason,
            ExpertRejectReasonV1.PARENT_CONTRACT_INVALID,
        )

    def test_clock_synchronization_is_applied_and_independently_validated(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        evidence = synchronization_input()
        observations = _synchronization_group(manifest, evidence)
        reduction = reduce_expert_parent(state, observations)
        outcome = reduction.outcomes[0]
        self.assertEqual(
            outcome.event_kind,
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
        )
        self.assertEqual(
            outcome.event_schema_sha256,
            sha256(
                (
                    SCHEMA_ROOT
                    / "expert-synchronization-applied-v1.schema.json"
                ).read_bytes()
            ).hexdigest(),
        )
        payload = outcome.payload
        self.assertEqual(type(payload), ExpertSynchronizationAppliedPayloadV1)
        self.assertEqual(payload.observation.evidence, evidence)
        validate_synchronization_transition(
            state.synchronization,
            payload.transition,
        )
        self.assertEqual(
            reduction.final_state.synchronization.last_observation,
            observations[0].observation,
        )
        self.assertFalse(reduction.halt_required)

    def test_outcome_rejects_schema_hash_for_a_different_event_kind(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        reduction = reduce_expert_parent(
            state,
            _synchronization_group(manifest, synchronization_input()),
        )
        wrong_schema_sha256 = sha256(
            (
                SCHEMA_ROOT / "expert-observation-ignored-v1.schema.json"
            ).read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "event_schema_sha256"):
            replace(
                reduction.outcomes[0],
                event_schema_sha256=wrong_schema_sha256,
            )

    def test_multiple_outputs_chain_through_intermediate_state(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        observations = _synchronization_group(
            manifest,
            _book_input("MATCH-HOME"),
            _book_input("MATCH-AWAY"),
        )
        reduction = reduce_expert_parent(state, observations)
        self.assertEqual(len(reduction.outcomes), 2)
        self.assertEqual(
            reduction.outcomes[1].prior_expert_state_sha256,
            reduction.outcomes[0].post_expert_state_sha256,
        )
        selected = tuple(
            cursor
            for cursor in reduction.final_state.synchronization.book_cursors
            if cursor.canonical_match_id == "canonical-match-9"
        )
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(cursor.book is not None for cursor in selected))
        self.assertFalse(reduction.halt_required)

    def test_forged_transition_is_rejected_by_transition_validator(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        observations = _synchronization_group(
            manifest,
            synchronization_input(),
        )
        actual = synchronize(
            state.synchronization,
            observations[0].evidence,
            now=observations[0].observation,
        )
        forged = replace(actual, prior_session_sha256="0" * 64)
        with mock.patch(
            "inci_tennis_expert.reducer.synchronize",
            return_value=forged,
        ):
            reduction = reduce_expert_parent(state, observations)
        payload = reduction.outcomes[0].payload
        self.assertEqual(type(payload), ExpertObservationRejectedPayloadV1)
        self.assertEqual(
            payload.reason,
            ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT,
        )
        self.assertEqual(reduction.final_state.rejected_parent_count, 1)
        self.assertTrue(reduction.halt_required)

    def test_first_failure_halts_parent_and_later_outputs_are_static_rejections(
        self,
    ) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        observations = _synchronization_group(
            manifest,
            synchronization_input(),
            synchronization_input(ticker="MATCH-AWAY"),
        )
        actual = synchronize(
            state.synchronization,
            observations[0].evidence,
            now=observations[0].observation,
        )
        forged = replace(actual, prior_session_sha256="0" * 64)
        with mock.patch(
            "inci_tennis_expert.reducer.synchronize",
            return_value=forged,
        ):
            reduction = reduce_expert_parent(state, observations)
        self.assertEqual(
            tuple(outcome.payload.reason for outcome in reduction.outcomes),
            (
                ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT,
                ExpertRejectReasonV1.PRIOR_OUTCOME_HALTED,
            ),
        )
        self.assertEqual(reduction.final_state.rejected_parent_count, 1)
        self.assertEqual(
            reduction.final_state.halt_reason,
            ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT,
        )

    def test_already_halted_next_parent_preserves_original_halt_reason(self) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        first = bind_expert_observation_drafts(
            manifest,
            raw_parent(),
            manifest.normalizers.fallback,
            (
                ExpertRejectedDraftV1(
                    ExpertRejectReasonV1.NORMALIZER_EXCEPTION
                ),
            ),
        )
        halted = reduce_expert_parent(state, first).final_state
        next_parent = bind_expert_observation_drafts(
            manifest,
            raw_parent(ingest_seq=3),
            manifest.normalizers.fallback,
            (ExpertSynchronizationDraftV1(synchronization_input()),),
        )
        reduction = reduce_expert_parent(halted, next_parent)
        self.assertEqual(
            reduction.outcomes[0].payload.reason,
            ExpertRejectReasonV1.PRIOR_GROUP_HALTED,
        )
        self.assertEqual(reduction.final_state.rejected_parent_count, 2)
        self.assertEqual(
            reduction.final_state.halt_reason,
            ExpertRejectReasonV1.NORMALIZER_EXCEPTION,
        )

    def test_post_reducer_accumulation_overflow_rolls_back_whole_parent(
        self,
    ) -> None:
        universe, policy, manifest = task6_artifacts(binding_count=19)
        state = initial_expert_state(manifest, universe, policy)
        home = _book_input(
            "MATCH-001-HOME",
            canonical_match_id="canonical-match-001",
        )
        away = _book_input(
            "MATCH-001-AWAY",
            canonical_match_id="canonical-match-001",
        )
        first_only = _synchronization_group(manifest, home)
        first_reduction = reduce_expert_parent(state, first_only)
        self.assertEqual(
            first_reduction.outcomes[0].event_kind,
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
        )

        observations = _synchronization_group(manifest, home, away)
        self.assertTrue(
            all(
                type(item) is ExpertSynchronizationObservationV1
                for item in observations
            )
        )
        reduction = reduce_expert_parent(state, observations)
        self.assertEqual(len(reduction.outcomes), 1)
        self.assertEqual(
            reduction.outcomes[0].payload.reason,
            ExpertRejectReasonV1.GROUP_CAPACITY_EXCEEDED,
        )
        self.assertEqual(
            reduction.final_state.synchronization,
            state.synchronization,
        )
        self.assertEqual(reduction.final_state.rejected_parent_count, 1)
        self.assertTrue(reduction.halt_required)

    def test_initial_state_refuses_genuine_oversized_task1_universe(self) -> None:
        universe, policy, manifest = task6_artifacts(binding_count=21)
        with self.assertRaisesRegex(ValueError, "initial_expert_capacity"):
            initial_expert_state(manifest, universe, policy)

    def test_one_thousand_reductions_are_deterministic_under_hostile_decimal_context(
        self,
    ) -> None:
        universe, policy, manifest = task6_artifacts()
        state = initial_expert_state(manifest, universe, policy)
        observations = _synchronization_group(
            manifest,
            synchronization_input(),
        )
        expected = reduce_expert_parent(state, observations)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            for _ in range(1_000):
                self.assertEqual(
                    reduce_expert_parent(state, observations),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
