from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

from inci_tennis_expert.contracts import (
    ContractSide,
    FairValueEstimate,
    PlayerSide,
    expert_contract_sha256,
)
from inci_tennis_expert.fee_schedule import (
    FillSide,
    LiquidityRole,
    fee_for_fill,
)
from inci_tennis_expert.five_minute_path import (
    CapacityStatus,
    DipAssessment,
    DipObservation,
    DipReason,
    EntryAction,
    EntryCapacity,
    EntryGateInput,
    EntryReason,
    EntrySnapshotBinding,
    FiveMinuteForecast,
    PriceLevel,
    five_minute_forecast_sha256,
    evaluate_entry,
    price_levels_sha256,
    size_ioc_entry,
)
from inci_tennis_expert.first_set_model import (
    FirstSetReview,
    first_set_review_sha256,
)
from inci_tennis_expert.paper_policy import (
    PaperEntryCandidate,
    PaperEntryOutcome,
    PaperPolicyError,
    evaluate_and_reserve,
)
from inci_tennis_expert.risk import (
    FrozenRiskPolicy,
    initial_risk_state,
)
from inci_tennis_expert.strategy_artifacts import (
    VerifiedStrategyArtifacts,
    verify_strategy_artifacts,
)
from tests.tennis_v1.test_strategy_artifacts import (
    _fee_document,
    _manifest_with,
    _model_document,
    _payload,
)
from tests.tennis_v1.test_five_minute_path import (
    _supported_first_set_review,
)


class PaperPolicyIntegrationTests(unittest.TestCase):
    def authority(
        self,
        *,
        calibration_cutoff_wall_ns: int = 1,
    ) -> VerifiedStrategyArtifacts:
        outcome = _payload(
            "outcome-v1",
            _model_document(
                "outcome-v1",
                "outcome_model",
                calibration_cutoff_wall_ns=(
                    calibration_cutoff_wall_ns
                ),
            ),
        )
        markout = _payload(
            "markout-v1",
            _model_document(
                "markout-v1",
                "five_minute_markout",
                calibration_cutoff_wall_ns=(
                    calibration_cutoff_wall_ns
                ),
            ),
        )
        fee = _payload("fees-v1", _fee_document())
        return verify_strategy_artifacts(
            _manifest_with(outcome.pin, markout.pin, fee.pin),
            outcome=outcome,
            markout=markout,
            fee_schedule=fee,
        )

    def fair_value(
        self,
        authority: VerifiedStrategyArtifacts | None = None,
    ) -> FairValueEstimate:
        authority = authority or self.authority()
        return FairValueEstimate(
            player_side=PlayerSide.HOME,
            fair_probability=Decimal("0.60"),
            lower_probability=Decimal("0.55"),
            upper_probability=Decimal("0.65"),
            supported=True,
            stratum="hard-bo3-supported",
            model_sha256=authority.outcome_pin.artifact_sha256,
            prematch_artifact_sha256="1" * 64,
            feature_definition_sha256="2" * 64,
            feature_vector_sha256="3" * 64,
            calibration_artifact_sha256=(
                authority.outcome_pin.artifact_sha256
            ),
            abstention_reason=None,
        )

    def binding(
        self,
        fair_value: FairValueEstimate,
        forecast_sha256: str,
        first_set_review: FirstSetReview,
        authority: VerifiedStrategyArtifacts | None = None,
    ) -> EntrySnapshotBinding:
        authority = authority or self.authority()
        return EntrySnapshotBinding(
            canonical_match_id="match-1",
            contract_side=ContractSide.YES,
            player_side=PlayerSide.HOME,
            provider_revision=11,
            provider_correction_epoch=2,
            book_epoch=7,
            book_sequence=41,
            book_snapshot_sha256="b" * 64,
            fee_series_ticker="KXWTAMATCH",
            decision_wall_ns=2,
            decision_monotonic_ns=100,
            entry_ask_levels_sha256=price_levels_sha256(
                (PriceLevel(Decimal("0.40"), Decimal("10")),)
            ),
            first_set_review_sha256=first_set_review_sha256(
                first_set_review
            ),
            first_set_point_history_sha256=(
                first_set_review.point_history_sha256
            ),
            first_set_consensus_epoch=first_set_review.consensus_epoch,
            session_manifest_sha256=(
                authority.session_manifest_sha256
            ),
            outcome_artifact_id=authority.outcome_pin.artifact_id,
            outcome_artifact_sha256=authority.outcome_pin.artifact_sha256,
            fair_value_estimate_sha256=expert_contract_sha256(fair_value),
            markout_artifact_id=authority.markout_pin.artifact_id,
            markout_artifact_sha256=authority.markout_pin.artifact_sha256,
            markout_forecast_sha256=forecast_sha256,
            fee_schedule_artifact_id=(
                authority.fee_schedule_pin.artifact_id
            ),
            fee_schedule_sha256=(
                authority.fee_schedule_pin.artifact_sha256
            ),
        )

    def gate(
        self,
        *,
        set_number: int,
        authority: VerifiedStrategyArtifacts | None = None,
    ) -> EntryGateInput:
        authority = authority or self.authority()
        fair_value = self.fair_value(authority)
        first_set_review = _supported_first_set_review()
        capacity = size_ioc_entry(
            (PriceLevel(Decimal("0.40"), Decimal("10")),),
            requested_quantity=Decimal("10"),
            fee=lambda price, quantity: fee_for_fill(
                authority.fee_schedule,
                series_ticker="KXWTAMATCH",
                price=price,
                quantity=quantity,
                role=LiquidityRole.TAKER,
                side=FillSide.BUY,
                fill_wall_ns=2,
            ),
        )
        current = DipObservation(
            observed_monotonic_ns=100,
            contract_side=ContractSide.YES,
            epoch=1,
            set_number=set_number,
            executable_ask=Decimal("0.40"),
        )
        reference = DipObservation(
            observed_monotonic_ns=90,
            contract_side=ContractSide.YES,
            epoch=1,
            set_number=set_number,
            executable_ask=Decimal("0.50"),
        )
        unbound_forecast = FiveMinuteForecast(
            artifact_version=authority.markout_pin.artifact_id,
            artifact_sha256=authority.markout_pin.artifact_sha256,
            supported=True,
            frozen=True,
            calibrated=True,
            quantity=Decimal("10"),
            expected_net_pnl=Decimal("6.50"),
            lower_expected_net_pnl=Decimal("5.01"),
            upper_expected_net_pnl=Decimal("8.00"),
            fill_probability=Decimal("1"),
            loss_probability=Decimal("0.10"),
            tail_loss_estimate=Decimal("-5.00"),
            supporting_sample_count=100,
            abstention_reason=None,
            snapshot_binding=None,
        )
        snapshot_binding = self.binding(
            fair_value,
            five_minute_forecast_sha256(unbound_forecast),
            first_set_review,
            authority,
        )
        return EntryGateInput(
            set_number=set_number,
            score_trusted=True,
            book_trusted=True,
            first_set_review=first_set_review,
            current_ask=Decimal("0.40"),
            conservative_fair_value=Decimal("0.55"),
            fair_value=fair_value,
            dip=DipAssessment(
                current=current,
                reference=reference,
                dip=Decimal("0.10"),
                reason=DipReason.QUALIFIED,
            ),
            capacity=capacity,
            forecast=replace(
                unbound_forecast,
                snapshot_binding=snapshot_binding,
            ),
            snapshot_binding=snapshot_binding,
        )

    def candidate(
        self,
        *,
        set_number: int,
        authority: VerifiedStrategyArtifacts | None = None,
    ) -> PaperEntryCandidate:
        authority = authority or self.authority()
        gate = self.gate(set_number=set_number, authority=authority)
        assert gate.snapshot_binding is not None
        return PaperEntryCandidate(
            request_id="request-1",
            canonical_match_id="match-1",
            contract_side=ContractSide.YES,
            provider_revision=11,
            requested_monotonic_ns=100,
            signal_reset=True,
            gate=gate,
            session_snapshot_binding=gate.snapshot_binding,
            strategy_authority=authority,
            entry_ask_levels=(
                PriceLevel(Decimal("0.40"), Decimal("10")),
            ),
            fee_series_ticker="KXWTAMATCH",
            fill_wall_ns=2,
        )

    def policy(self) -> FrozenRiskPolicy:
        return FrozenRiskPolicy(
            maximum_occupied_matches=2,
            maximum_session_loss=Decimal("30"),
            maximum_attempts_per_match=2,
            stop_cooldown_ns=60,
        )

    def test_valid_candidate_abstains_until_paper_model_is_promoted(
        self,
    ) -> None:
        before = initial_risk_state()
        state, outcome = evaluate_and_reserve(
            before,
            self.candidate(set_number=2),
            self.policy(),
        )

        self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
        self.assertIs(
            outcome.entry.reason,
            EntryReason.PAPER_MODEL_NOT_PROMOTED,
        )
        self.assertIs(outcome.action, EntryAction.ABSTAIN)
        self.assertFalse(outcome.authorized)
        self.assertIsNone(outcome.risk_result)
        self.assertIs(state, before)
        self.assertEqual(state.reservations, ())

    def test_future_calibration_cutoff_abstains_before_gate_scoring(
        self,
    ) -> None:
        authority = self.authority(calibration_cutoff_wall_ns=2)
        before = initial_risk_state()

        after, outcome = evaluate_and_reserve(
            before,
            self.candidate(set_number=2, authority=authority),
            self.policy(),
        )

        self.assertIs(
            outcome.entry.reason,
            EntryReason.MODEL_ARTIFACT_NOT_CAUSAL,
        )
        self.assertIsNone(outcome.risk_result)
        self.assertIs(after, before)

    def test_missing_session_authority_never_reserves(self) -> None:
        cases = (
            (
                {"session_snapshot_binding": None},
                "snapshot_binding_missing",
            ),
            (
                {"strategy_authority": None},
                "strategy_authority_missing",
            ),
        )

        for changes, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                before = initial_risk_state()
                candidate = replace(
                    self.candidate(set_number=2),
                    **changes,
                )

                after, outcome = evaluate_and_reserve(
                    before,
                    candidate,
                    self.policy(),
                )

                self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
                self.assertEqual(outcome.entry.reason.value, expected_reason)
                self.assertIsNone(outcome.risk_result)
                self.assertIs(after, before)

    def test_session_artifact_or_manifest_mismatch_never_reserves(
        self,
    ) -> None:
        candidate = self.candidate(set_number=2)
        assert candidate.gate.snapshot_binding is not None
        assert candidate.gate.forecast is not None
        cases = (
            ("session_manifest_sha256", "f" * 64),
            ("outcome_artifact_sha256", "e" * 64),
            ("markout_artifact_sha256", "e" * 64),
            ("fee_schedule_sha256", "e" * 64),
        )

        for field, value in cases:
            with self.subTest(field=field):
                binding = replace(
                    candidate.gate.snapshot_binding,
                    **{field: value},
                )
                gate = replace(
                    candidate.gate,
                    snapshot_binding=binding,
                    forecast=replace(
                        candidate.gate.forecast,
                        snapshot_binding=binding,
                    ),
                )
                before = initial_risk_state()
                mismatched = replace(
                    candidate,
                    gate=gate,
                    session_snapshot_binding=binding,
                )

                after, outcome = evaluate_and_reserve(
                    before,
                    mismatched,
                    self.policy(),
                )

                self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
                self.assertEqual(
                    outcome.entry.reason.value,
                    "strategy_authority_mismatch",
                )
                self.assertIsNone(outcome.risk_result)
                self.assertIs(after, before)

    def test_forecast_artifact_id_or_sha_mismatch_never_reserves(self) -> None:
        candidate = self.candidate(set_number=2)
        assert candidate.gate.forecast is not None
        changes = (
            {"artifact_version": "markout-v2"},
            {"artifact_sha256": "b" * 64},
        )

        for change in changes:
            with self.subTest(change=change):
                before = initial_risk_state()
                gate = replace(
                    candidate.gate,
                    forecast=replace(candidate.gate.forecast, **change),
                )

                after, outcome = evaluate_and_reserve(
                    before,
                    replace(candidate, gate=gate),
                    self.policy(),
                )

                self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
                self.assertEqual(
                    outcome.entry.reason.value,
                    "strategy_authority_mismatch",
                )
                self.assertIsNone(outcome.risk_result)
                self.assertIs(after, before)

    def test_fee_capacity_is_recomputed_inside_authority_boundary(self) -> None:
        candidate = self.candidate(set_number=2)
        capacity = candidate.gate.capacity
        forged = EntryCapacity(
            requested_quantity=capacity.requested_quantity,
            filled_quantity=capacity.filled_quantity,
            fills=capacity.fills,
            gross_debit=capacity.gross_debit,
            entry_fee=Decimal("0"),
            all_in_debit=capacity.gross_debit,
            status=CapacityStatus.FULL,
        )

        before = initial_risk_state()
        after, outcome = evaluate_and_reserve(
            before,
            replace(candidate, gate=replace(candidate.gate, capacity=forged)),
            self.policy(),
        )

        self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
        self.assertEqual(
            outcome.entry.reason.value,
            "fee_schedule_capacity_mismatch",
        )
        self.assertIsNone(outcome.risk_result)
        self.assertIs(after, before)

    def test_stale_session_or_candidate_snapshot_never_reserves(self) -> None:
        candidate = self.candidate(set_number=2)
        assert candidate.session_snapshot_binding is not None
        cases = (
            replace(
                candidate,
                session_snapshot_binding=replace(
                    candidate.session_snapshot_binding,
                    book_sequence=42,
                ),
            ),
            replace(candidate, canonical_match_id="match-2"),
            replace(candidate, provider_revision=12),
            replace(candidate, requested_monotonic_ns=101),
            replace(candidate, fee_series_ticker="KXATPMATCH"),
            replace(candidate, fill_wall_ns=3),
            replace(
                candidate,
                entry_ask_levels=(
                    PriceLevel(Decimal("0.41"), Decimal("10")),
                ),
            ),
        )

        for stale_candidate in cases:
            with self.subTest(stale_candidate=stale_candidate):
                before = initial_risk_state()
                after, outcome = evaluate_and_reserve(
                    before,
                    stale_candidate,
                    self.policy(),
                )

                self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
                self.assertIs(
                    outcome.entry.reason,
                    EntryReason.SNAPSHOT_BINDING_MISMATCH,
                )
                self.assertIsNone(outcome.risk_result)
                self.assertIs(after, before)

    def test_contract_side_mismatch_never_reserves(self) -> None:
        before = initial_risk_state()
        candidate = replace(
            self.candidate(set_number=2),
            contract_side=ContractSide.NO,
        )

        after, outcome = evaluate_and_reserve(
            before,
            candidate,
            self.policy(),
        )

        self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
        self.assertIs(outcome.entry.reason, EntryReason.CONTRACT_SIDE_MISMATCH)
        self.assertIs(outcome.action, EntryAction.ABSTAIN)
        self.assertFalse(outcome.authorized)
        self.assertIsNone(outcome.risk_result)
        self.assertIs(after, before)

    def test_first_set_abstention_never_creates_a_risk_reservation(self) -> None:
        before = initial_risk_state()

        after, outcome = evaluate_and_reserve(
            before,
            self.candidate(set_number=1),
            self.policy(),
        )

        self.assertIs(outcome.entry.action, EntryAction.ABSTAIN)
        self.assertIs(outcome.action, EntryAction.ABSTAIN)
        self.assertFalse(outcome.authorized)
        self.assertIsNone(outcome.risk_result)
        self.assertIs(after, before)

    def test_repeated_valid_candidates_cannot_reach_risk_reservation(
        self,
    ) -> None:
        before = initial_risk_state()

        for request_id in ("request-1", "request-2"):
            candidate = replace(
                self.candidate(set_number=2),
                request_id=request_id,
            )
            after, outcome = evaluate_and_reserve(
                before,
                candidate,
                self.policy(),
            )

            self.assertIs(
                outcome.entry.reason,
                EntryReason.PAPER_MODEL_NOT_PROMOTED,
            )
            self.assertIsNone(outcome.risk_result)
            self.assertIs(after, before)
            self.assertEqual(after.reservations, ())

    def test_public_outcome_cannot_promote_a_research_signal(self) -> None:
        diagnostic = evaluate_entry(self.candidate(set_number=2).gate)
        self.assertIs(
            diagnostic.action,
            EntryAction.RESEARCH_ELIGIBLE,
        )

        with self.assertRaisesRegex(
            PaperPolicyError,
            "^paper_execution_disabled$",
        ):
            PaperEntryOutcome(entry=diagnostic)


if __name__ == "__main__":
    unittest.main()
