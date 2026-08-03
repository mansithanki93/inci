from __future__ import annotations

from dataclasses import replace
from decimal import Context, Decimal, ROUND_DOWN, getcontext, setcontext
import unittest

from inci_tennis_expert.contracts import (
    ContractSide,
    FairValueEstimate,
    MatchFormat,
    PlayerSide,
    SetScore,
    expert_contract_sha256,
)
from inci_tennis_expert.first_set_model import (
    BetaDistribution,
    FirstSetBayesianModel,
    FirstSetParameters,
    FirstSetPoint,
    FirstSetReview,
    first_set_review_sha256,
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
    ExitAction,
    ExitReason,
    FiveMinutePathError,
    FiveMinuteForecast,
    ForcedExitReason,
    ForecastAbstentionReason,
    PaperPosition,
    PriceLevel,
    assess_v1_dip,
    assess_exit,
    evaluate_entry,
    five_minute_forecast_sha256,
    price_levels_sha256,
    size_ioc_entry,
)


SECOND_NS = 1_000_000_000
ARTIFACT_SHA256 = "a" * 64


def _supported_first_set_review(
    *,
    consensus_epoch: int = 7,
) -> FirstSetReview:
    model = FirstSetBayesianModel(
        home_prior=BetaDistribution(Decimal("8"), Decimal("2")),
        away_prior=BetaDistribution(Decimal("6"), Decimal("4")),
        parameters=FirstSetParameters(
            version="first-set-service-points-v1",
            evidence_weight=Decimal("0.5"),
        ),
        match_format=MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
    )
    sequence = 0
    for game in range(6):
        server = PlayerSide.HOME if game % 2 == 0 else PlayerSide.AWAY
        for _ in range(4):
            sequence += 1
            model.observe_point(
                FirstSetPoint(
                    point_id=f"point-{sequence}",
                    sequence_number=sequence,
                    set_number=1,
                    server=server,
                    winner=PlayerSide.HOME,
                    consensus_epoch=consensus_epoch,
                    consensus_transition_sha256=f"{sequence:064x}",
                    supporting_source_lineage_sha256s=(
                        "1" * 64,
                        "2" * 64,
                    ),
                )
            )
    return model.complete_set_one(
        terminal_set=SetScore(6, 0, None, None),
    )


class DipAssessmentTests(unittest.TestCase):
    def test_assessment_rejects_inconsistent_reference_arithmetic(self) -> None:
        current = DipObservation(
            observed_monotonic_ns=50 * SECOND_NS,
            contract_side=ContractSide.YES,
            epoch=3,
            set_number=2,
            executable_ask=Decimal("0.40"),
        )
        reference = DipObservation(
            observed_monotonic_ns=49 * SECOND_NS,
            contract_side=ContractSide.YES,
            epoch=3,
            set_number=2,
            executable_ask=Decimal("0.41"),
        )

        with self.assertRaisesRegex(
            FiveMinutePathError,
            "^dip_assessment$",
        ):
            DipAssessment(
                current=current,
                reference=reference,
                dip=Decimal("0.07"),
                reason=DipReason.QUALIFIED,
            )

    def test_reference_excludes_every_observation_at_the_current_tick(self) -> None:
        current = DipObservation(
            observed_monotonic_ns=50 * SECOND_NS,
            contract_side=ContractSide.YES,
            epoch=3,
            set_number=2,
            executable_ask=Decimal("0.58"),
        )
        result = assess_v1_dip(
            (
                DipObservation(
                    observed_monotonic_ns=49 * SECOND_NS,
                    contract_side=ContractSide.YES,
                    epoch=3,
                    set_number=2,
                    executable_ask=Decimal("0.65"),
                ),
                DipObservation(
                    observed_monotonic_ns=50 * SECOND_NS,
                    contract_side=ContractSide.YES,
                    epoch=3,
                    set_number=2,
                    executable_ask=Decimal("0.99"),
                ),
            ),
            current,
        )

        self.assertEqual(result.reference_ask, Decimal("0.65"))
        self.assertEqual(result.dip, Decimal("0.07"))
        self.assertIs(result.reason, DipReason.QUALIFIED)

    def test_reference_includes_exact_boundary_but_excludes_older_asks(self) -> None:
        current = DipObservation(
            observed_monotonic_ns=100 * SECOND_NS,
            contract_side=ContractSide.NO,
            epoch=9,
            set_number=3,
            executable_ask=Decimal("0.63"),
        )
        result = assess_v1_dip(
            (
                DipObservation(
                    observed_monotonic_ns=54 * SECOND_NS,
                    contract_side=ContractSide.NO,
                    epoch=9,
                    set_number=3,
                    executable_ask=Decimal("0.90"),
                ),
                DipObservation(
                    observed_monotonic_ns=55 * SECOND_NS,
                    contract_side=ContractSide.NO,
                    epoch=9,
                    set_number=3,
                    executable_ask=Decimal("0.70"),
                ),
            ),
            current,
        )

        self.assertEqual(result.reference_ask, Decimal("0.70"))
        self.assertEqual(result.dip, Decimal("0.07"))
        self.assertIs(result.reason, DipReason.QUALIFIED)

    def test_reference_does_not_cross_side_epoch_or_set_boundaries(self) -> None:
        current = DipObservation(
            observed_monotonic_ns=100 * SECOND_NS,
            contract_side=ContractSide.YES,
            epoch=5,
            set_number=3,
            executable_ask=Decimal("0.63"),
        )
        result = assess_v1_dip(
            (
                DipObservation(
                    observed_monotonic_ns=99 * SECOND_NS,
                    contract_side=ContractSide.NO,
                    epoch=5,
                    set_number=3,
                    executable_ask=Decimal("0.99"),
                ),
                DipObservation(
                    observed_monotonic_ns=98 * SECOND_NS,
                    contract_side=ContractSide.YES,
                    epoch=4,
                    set_number=3,
                    executable_ask=Decimal("0.98"),
                ),
                DipObservation(
                    observed_monotonic_ns=97 * SECOND_NS,
                    contract_side=ContractSide.YES,
                    epoch=5,
                    set_number=2,
                    executable_ask=Decimal("0.97"),
                ),
                DipObservation(
                    observed_monotonic_ns=96 * SECOND_NS,
                    contract_side=ContractSide.YES,
                    epoch=5,
                    set_number=3,
                    executable_ask=Decimal("0.70"),
                ),
            ),
            current,
        )

        self.assertEqual(result.reference_ask, Decimal("0.70"))
        self.assertEqual(result.dip, Decimal("0.07"))
        self.assertIs(result.reason, DipReason.QUALIFIED)


class EntrySizingTests(unittest.TestCase):
    def test_fee_inclusive_depth_reports_zero_partial_and_full_capacity(self) -> None:
        partial = size_ioc_entry(
            (
                PriceLevel(Decimal("0.40"), Decimal("50")),
                PriceLevel(Decimal("0.60"), Decimal("60")),
            ),
            requested_quantity=Decimal("110"),
            fee=lambda price, _quantity: (
                Decimal("0.25")
                if price == Decimal("0.40")
                else Decimal("0.50")
            ),
        )

        self.assertIs(partial.status, CapacityStatus.PARTIAL)
        self.assertEqual(partial.filled_quantity, Decimal("98"))
        self.assertEqual(
            partial.fills,
            (
                PriceLevel(Decimal("0.40"), Decimal("50")),
                PriceLevel(Decimal("0.60"), Decimal("48")),
            ),
        )
        self.assertEqual(partial.gross_debit, Decimal("48.80"))
        self.assertEqual(partial.entry_fee, Decimal("0.75"))
        self.assertEqual(partial.all_in_debit, Decimal("49.55"))

        zero = size_ioc_entry(
            (PriceLevel(Decimal("0.90"), Decimal("1")),),
            requested_quantity=Decimal("1"),
            fee=lambda _quantity, _gross: Decimal("50.00"),
        )
        self.assertIs(zero.status, CapacityStatus.ZERO)
        self.assertEqual(zero.filled_quantity, Decimal("0"))
        self.assertEqual(zero.all_in_debit, Decimal("0"))
        self.assertEqual(zero.fills, ())

        full = size_ioc_entry(
            (
                PriceLevel(Decimal("0.40"), Decimal("2")),
                PriceLevel(Decimal("0.60"), Decimal("1")),
            ),
            requested_quantity=Decimal("3"),
            fee=lambda price, _quantity: (
                Decimal("0.10")
                if price == Decimal("0.40")
                else Decimal("0.15")
            ),
        )
        self.assertIs(full.status, CapacityStatus.FULL)
        self.assertEqual(full.filled_quantity, Decimal("3"))
        self.assertEqual(full.gross_debit, Decimal("1.40"))
        self.assertEqual(full.entry_fee, Decimal("0.25"))
        self.assertEqual(full.all_in_debit, Decimal("1.65"))

    def test_sizing_is_independent_of_process_decimal_context(self) -> None:
        original = getcontext().copy()
        try:
            setcontext(Context(prec=2, rounding=ROUND_DOWN))
            result = size_ioc_entry(
                (PriceLevel(Decimal("0.333"), Decimal("3")),),
                requested_quantity=Decimal("3"),
                fee=lambda _quantity, _gross: Decimal("0"),
            )
        finally:
            setcontext(original)

        self.assertEqual(result.gross_debit, Decimal("0.999"))
        self.assertEqual(result.all_in_debit, Decimal("0.999"))

    def test_fee_callable_is_applied_to_each_executable_fill_level(self) -> None:
        result = size_ioc_entry(
            (
                PriceLevel(Decimal("0.20"), Decimal("2")),
                PriceLevel(Decimal("0.80"), Decimal("2")),
            ),
            requested_quantity=Decimal("4"),
            fee=lambda price, quantity: price * price * quantity,
        )

        self.assertIs(result.status, CapacityStatus.FULL)
        self.assertEqual(result.gross_debit, Decimal("2.00"))
        self.assertEqual(result.entry_fee, Decimal("1.36"))
        self.assertEqual(result.all_in_debit, Decimal("3.36"))

    def test_sizing_does_not_assume_a_deterministic_fee_is_monotone(self) -> None:
        result = size_ioc_entry(
            (PriceLevel(Decimal("0.10"), Decimal("10")),),
            requested_quantity=Decimal("10"),
            fee=lambda _price, quantity: (
                Decimal("100")
                if quantity < Decimal("6")
                else Decimal("0")
            ),
        )

        self.assertIs(result.status, CapacityStatus.FULL)
        self.assertEqual(result.filled_quantity, Decimal("10"))
        self.assertEqual(result.all_in_debit, Decimal("1.00"))

    def test_enormous_depth_starts_search_at_gross_affordable_quantity(self) -> None:
        fee_calls = 0

        def zero_fee(_price: Decimal, _quantity: Decimal) -> Decimal:
            nonlocal fee_calls
            fee_calls += 1
            if fee_calls > 2:
                raise AssertionError("unbounded fee search")
            return Decimal("0")

        result = size_ioc_entry(
            (
                PriceLevel(
                    Decimal("0.50"),
                    Decimal("1000000000000"),
                ),
            ),
            requested_quantity=Decimal("1000000000000"),
            fee=zero_fee,
        )

        self.assertIs(result.status, CapacityStatus.PARTIAL)
        self.assertEqual(result.filled_quantity, Decimal("100"))
        self.assertEqual(result.all_in_debit, Decimal("50.00"))
        self.assertLessEqual(fee_calls, 2)

    def test_pathological_fee_search_fails_closed_with_bounded_work(self) -> None:
        fee_calls = 0

        def unaffordable_fee(
            _price: Decimal,
            _quantity: Decimal,
        ) -> Decimal:
            nonlocal fee_calls
            fee_calls += 1
            if fee_calls > 10_000:
                raise AssertionError("fee search escaped its work budget")
            return Decimal("50.00")

        with self.assertRaisesRegex(
            FiveMinutePathError,
            "^fee_search_budget$",
        ):
            size_ioc_entry(
                (
                    PriceLevel(
                        Decimal("0.000001"),
                        Decimal("1000000000"),
                    ),
                ),
                requested_quantity=Decimal("1000000000"),
                fee=unaffordable_fee,
            )

        self.assertLessEqual(fee_calls, 10_000)


class EntryGateTests(unittest.TestCase):
    def _fair_value(self) -> FairValueEstimate:
        return FairValueEstimate(
            player_side=PlayerSide.HOME,
            fair_probability=Decimal("0.60"),
            lower_probability=Decimal("0.55"),
            upper_probability=Decimal("0.65"),
            supported=True,
            stratum="hard-bo3-supported",
            model_sha256="d" * 64,
            prematch_artifact_sha256="1" * 64,
            feature_definition_sha256="2" * 64,
            feature_vector_sha256="3" * 64,
            calibration_artifact_sha256="d" * 64,
            abstention_reason=None,
        )

    def _snapshot_binding(
        self,
        fair_value: FairValueEstimate,
        forecast_sha256: str,
        first_set_review: FirstSetReview,
    ) -> EntrySnapshotBinding:
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
            decision_monotonic_ns=100 * SECOND_NS,
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
            session_manifest_sha256="e" * 64,
            outcome_artifact_id="outcome-v1",
            outcome_artifact_sha256="d" * 64,
            fair_value_estimate_sha256=expert_contract_sha256(fair_value),
            markout_artifact_id="markout-v1",
            markout_artifact_sha256=ARTIFACT_SHA256,
            markout_forecast_sha256=forecast_sha256,
            fee_schedule_artifact_id="fees-v1",
            fee_schedule_sha256="c" * 64,
        )

    def _valid_input(self) -> EntryGateInput:
        fair_value = self._fair_value()
        first_set_review = _supported_first_set_review()
        capacity = size_ioc_entry(
            (PriceLevel(Decimal("0.40"), Decimal("10")),),
            requested_quantity=Decimal("10"),
            fee=lambda _quantity, _gross: Decimal("1.00"),
        )
        unbound_forecast = FiveMinuteForecast(
            artifact_version="markout-v1",
            artifact_sha256=ARTIFACT_SHA256,
            supported=True,
            frozen=True,
            calibrated=True,
            quantity=Decimal("10"),
            expected_net_pnl=Decimal("6.00"),
            lower_expected_net_pnl=Decimal("5.00"),
            upper_expected_net_pnl=Decimal("7.00"),
            fill_probability=Decimal("0.80"),
            loss_probability=Decimal("0.20"),
            tail_loss_estimate=Decimal("-8.00"),
            supporting_sample_count=500,
            abstention_reason=None,
            snapshot_binding=None,
        )
        snapshot_binding = self._snapshot_binding(
            fair_value,
            five_minute_forecast_sha256(unbound_forecast),
            first_set_review,
        )
        return EntryGateInput(
            set_number=2,
            score_trusted=True,
            book_trusted=True,
            first_set_review=first_set_review,
            current_ask=Decimal("0.40"),
            conservative_fair_value=Decimal("0.55"),
            fair_value=fair_value,
            dip=DipAssessment(
                current=DipObservation(
                    100 * SECOND_NS,
                    ContractSide.YES,
                    1,
                    2,
                    Decimal("0.40"),
                ),
                reference=DipObservation(
                    99 * SECOND_NS,
                    ContractSide.YES,
                    1,
                    2,
                    Decimal("0.50"),
                ),
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

    def _with_forecast(
        self,
        candidate: EntryGateInput,
        **changes: object,
    ) -> EntryGateInput:
        assert candidate.forecast is not None
        assert candidate.snapshot_binding is not None
        forecast = replace(candidate.forecast, **changes)
        binding = replace(
            candidate.snapshot_binding,
            markout_forecast_sha256=five_minute_forecast_sha256(forecast),
        )
        return replace(
            candidate,
            forecast=replace(forecast, snapshot_binding=binding),
            snapshot_binding=binding,
        )

    def test_scalar_fair_value_cannot_diverge_from_bound_model_output(
        self,
    ) -> None:
        baseline = self._valid_input()

        result = evaluate_entry(
            replace(
                baseline,
                conservative_fair_value=Decimal("0.56"),
            )
        )

        self.assertIs(result.action, EntryAction.ABSTAIN)
        self.assertEqual(result.reason.value, "fair_value_binding_mismatch")

    def test_forecast_output_cannot_change_after_snapshot_binding(self) -> None:
        baseline = self._valid_input()
        assert baseline.forecast is not None

        result = evaluate_entry(
            replace(
                baseline,
                forecast=replace(
                    baseline.forecast,
                    lower_expected_net_pnl=Decimal("5.01"),
                ),
            )
        )

        self.assertIs(result.action, EntryAction.ABSTAIN)
        self.assertIs(result.reason, EntryReason.FORECAST_BINDING_MISMATCH)

    def test_first_set_review_cannot_change_after_snapshot_binding(self) -> None:
        baseline = self._valid_input()

        result = evaluate_entry(
            replace(
                baseline,
                first_set_review=_supported_first_set_review(
                    consensus_epoch=8
                ),
            )
        )

        self.assertIs(result.action, EntryAction.ABSTAIN)
        self.assertIs(
            result.reason,
            EntryReason.FIRST_SET_POSTERIOR_INVALID,
        )

    def test_gate_requires_same_snapshot_binding_for_forecast(self) -> None:
        baseline = self._valid_input()
        assert baseline.forecast is not None
        baseline = self._with_forecast(
            baseline,
            lower_expected_net_pnl=Decimal("5.01"),
        )
        cases = (
            (
                replace(baseline, snapshot_binding=None),
                EntryReason.SNAPSHOT_BINDING_MISSING,
            ),
            (
                replace(
                    baseline,
                    forecast=replace(
                        baseline.forecast,
                        snapshot_binding=None,
                    ),
                ),
                EntryReason.SNAPSHOT_BINDING_MISSING,
            ),
            (
                replace(
                    baseline,
                    forecast=replace(
                        baseline.forecast,
                        snapshot_binding=replace(
                            baseline.snapshot_binding,
                            book_sequence=42,
                        ),
                    ),
                ),
                EntryReason.SNAPSHOT_BINDING_MISMATCH,
            ),
            (
                replace(
                    baseline,
                    snapshot_binding=replace(
                        baseline.snapshot_binding,
                        contract_side=ContractSide.NO,
                    ),
                    forecast=replace(
                        baseline.forecast,
                        snapshot_binding=replace(
                            baseline.snapshot_binding,
                            contract_side=ContractSide.NO,
                        ),
                    ),
                ),
                EntryReason.SNAPSHOT_BINDING_MISMATCH,
            ),
            (
                replace(
                    baseline,
                    snapshot_binding=replace(
                        baseline.snapshot_binding,
                        decision_monotonic_ns=101 * SECOND_NS,
                    ),
                    forecast=replace(
                        baseline.forecast,
                        snapshot_binding=replace(
                            baseline.snapshot_binding,
                            decision_monotonic_ns=101 * SECOND_NS,
                        ),
                    ),
                ),
                EntryReason.SNAPSHOT_BINDING_MISMATCH,
            ),
        )

        for candidate, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = evaluate_entry(candidate)
                self.assertIs(result.action, EntryAction.ABSTAIN)
                self.assertIs(result.reason, expected_reason)

    def test_lower_expected_pnl_must_be_strictly_greater_than_five(self) -> None:
        exact = evaluate_entry(self._valid_input())
        self.assertIs(exact.action, EntryAction.ABSTAIN)
        self.assertIs(exact.reason, EntryReason.LOWER_PNL_NOT_ABOVE_FIVE)
        self.assertEqual(exact.quantity, Decimal("0"))

        candidate = self._valid_input()
        assert candidate.forecast is not None
        above = evaluate_entry(
            self._with_forecast(
                candidate,
                lower_expected_net_pnl=Decimal("5.01"),
            )
        )
        self.assertIs(above.action, EntryAction.RESEARCH_ELIGIBLE)
        self.assertIs(above.reason, EntryReason.RESEARCH_ELIGIBLE)
        self.assertEqual(above.quantity, Decimal("10"))
        self.assertEqual(above.all_in_debit, Decimal("5.00"))

    def test_set_one_mechanically_abstains_even_above_threshold(self) -> None:
        candidate = self._valid_input()
        assert candidate.forecast is not None
        result = evaluate_entry(
            replace(
                self._with_forecast(
                    candidate,
                    lower_expected_net_pnl=Decimal("5.01"),
                ),
                set_number=1,
            )
        )

        self.assertIs(result.action, EntryAction.ABSTAIN)
        self.assertIs(result.reason, EntryReason.SET_NOT_ELIGIBLE)
        self.assertEqual(result.quantity, Decimal("0"))

    def test_missing_or_unsupported_forecast_artifact_abstains(self) -> None:
        candidate = self._valid_input()

        missing = evaluate_entry(replace(candidate, forecast=None))
        self.assertIs(missing.action, EntryAction.ABSTAIN)
        self.assertIs(missing.reason, EntryReason.FORECAST_MISSING)

        assert candidate.forecast is not None
        unsupported = evaluate_entry(
            self._with_forecast(
                candidate,
                supported=False,
                lower_expected_net_pnl=Decimal("5.01"),
                abstention_reason=(
                    ForecastAbstentionReason.UNSUPPORTED_SCORE_STATE
                ),
            )
        )
        self.assertIs(unsupported.action, EntryAction.ABSTAIN)
        self.assertIs(
            unsupported.reason,
            EntryReason.FORECAST_UNSUPPORTED,
        )

    def test_every_trust_capacity_value_and_artifact_gate_is_required(self) -> None:
        baseline = self._valid_input()
        assert baseline.forecast is not None
        baseline = self._with_forecast(
            baseline,
            lower_expected_net_pnl=Decimal("5.01"),
        )
        zero_capacity = size_ioc_entry(
            (PriceLevel(Decimal("0.40"), Decimal("1")),),
            requested_quantity=Decimal("1"),
            fee=lambda _quantity, _gross: Decimal("50.00"),
        )
        assert baseline.fair_value is not None
        no_edge_fair_value = replace(
            baseline.fair_value,
            fair_probability=Decimal("0.45"),
            lower_probability=Decimal("0.40"),
            upper_probability=Decimal("0.50"),
        )
        assert baseline.snapshot_binding is not None
        no_edge_binding = replace(
            baseline.snapshot_binding,
            fair_value_estimate_sha256=expert_contract_sha256(
                no_edge_fair_value
            ),
        )
        no_edge = replace(
            baseline,
            conservative_fair_value=Decimal("0.40"),
            fair_value=no_edge_fair_value,
            snapshot_binding=no_edge_binding,
            forecast=replace(
                baseline.forecast,
                snapshot_binding=no_edge_binding,
            ),
        )
        cases = (
            (
                replace(baseline, score_trusted=False),
                "score_untrusted",
            ),
            (
                replace(baseline, book_trusted=False),
                "book_untrusted",
            ),
            (
                replace(baseline, first_set_review=None),
                "first_set_posterior_invalid",
            ),
            (
                replace(baseline, capacity=zero_capacity),
                "no_fillable_capacity",
            ),
            (
                no_edge,
                "no_fair_value_edge",
            ),
            (
                replace(
                    baseline,
                    dip=DipAssessment(
                        current=baseline.dip.current,
                        reference=replace(
                            baseline.dip.reference,
                            executable_ask=Decimal("0.46"),
                        ),
                        dip=Decimal("0.06"),
                        reason=DipReason.BELOW_THRESHOLD,
                    ),
                ),
                "dip_not_qualified",
            ),
            (
                self._with_forecast(baseline, frozen=False),
                "forecast_unfrozen",
            ),
            (
                self._with_forecast(baseline, calibrated=False),
                "forecast_uncalibrated",
            ),
            (
                self._with_forecast(
                    baseline,
                    quantity=Decimal("1"),
                ),
                "forecast_size_mismatch",
            ),
        )

        for candidate, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                result = evaluate_entry(candidate)
                self.assertIs(result.action, EntryAction.ABSTAIN)
                self.assertEqual(result.reason.value, expected_reason)
                self.assertEqual(result.quantity, Decimal("0"))

    def test_gate_rejects_dip_snapshot_mismatched_to_policy_snapshot(self) -> None:
        baseline = self._valid_input()
        assert baseline.forecast is not None
        baseline = self._with_forecast(
            baseline,
            lower_expected_net_pnl=Decimal("5.01"),
        )

        for candidate in (
            replace(baseline, current_ask=Decimal("0.41")),
            replace(baseline, set_number=3),
        ):
            with self.subTest(candidate=candidate):
                result = evaluate_entry(candidate)
                self.assertIs(result.action, EntryAction.ABSTAIN)
                self.assertEqual(result.reason.value, "dip_input_mismatch")

    def test_fair_value_edge_is_bound_to_actual_ioc_fills_and_first_ask(
        self,
    ) -> None:
        baseline = self._valid_input()
        assert baseline.forecast is not None
        expensive = size_ioc_entry(
            (
                PriceLevel(Decimal("0.40"), Decimal("1")),
                PriceLevel(Decimal("0.90"), Decimal("49")),
            ),
            requested_quantity=Decimal("50"),
            fee=lambda _price, _quantity: Decimal("0"),
        )
        expensive_result = evaluate_entry(
            replace(
                self._with_forecast(
                    baseline,
                    quantity=Decimal("50"),
                    lower_expected_net_pnl=Decimal("5.01"),
                ),
                capacity=expensive,
            )
        )
        self.assertIs(expensive_result.action, EntryAction.ABSTAIN)
        self.assertIs(
            expensive_result.reason,
            EntryReason.NO_FAIR_VALUE_EDGE,
        )

        mismatched_ask = size_ioc_entry(
            (PriceLevel(Decimal("0.41"), Decimal("10")),),
            requested_quantity=Decimal("10"),
            fee=lambda _price, _quantity: Decimal("0"),
        )
        mismatch_result = evaluate_entry(
            replace(
                self._with_forecast(
                    baseline,
                    lower_expected_net_pnl=Decimal("5.01"),
                ),
                capacity=mismatched_ask,
            )
        )
        self.assertIs(mismatch_result.action, EntryAction.ABSTAIN)
        self.assertEqual(
            mismatch_result.reason.value,
            "capacity_ask_mismatch",
        )


class ExitAssessmentTests(unittest.TestCase):
    def test_executable_profit_stop_time_and_hold_thresholds(self) -> None:
        opened = 100 * SECOND_NS
        take_profit = assess_exit(
            PaperPosition(
                opened_monotonic_ns=opened,
                filled_quantity=Decimal("10"),
                entry_gross_debit=Decimal("3.00"),
                allocated_entry_fees=Decimal("1.00"),
            ),
            (
                PriceLevel(Decimal("1.00"), Decimal("6")),
                PriceLevel(Decimal("0.95"), Decimal("4")),
            ),
            now_monotonic_ns=200 * SECOND_NS,
            fee=lambda price, _quantity: (
                Decimal("0.30")
                if price == Decimal("1.00")
                else Decimal("0.50")
            ),
        )
        self.assertIs(take_profit.action, ExitAction.TAKE_PROFIT)
        self.assertIs(take_profit.reason, ExitReason.TAKE_PROFIT_THRESHOLD)
        self.assertEqual(
            take_profit.net_liquidation_pnl,
            Decimal("5.00"),
        )

        stop = assess_exit(
            PaperPosition(
                opened_monotonic_ns=opened,
                filled_quantity=Decimal("10"),
                entry_gross_debit=Decimal("4.15"),
                allocated_entry_fees=Decimal("1.00"),
            ),
            (
                PriceLevel(Decimal("0.02"), Decimal("5")),
                PriceLevel(Decimal("0.01"), Decimal("5")),
            ),
            now_monotonic_ns=200 * SECOND_NS,
            fee=lambda _quantity, _gross: Decimal("0"),
        )
        self.assertIs(stop.action, ExitAction.STOP)
        self.assertIs(stop.reason, ExitReason.STOP_THRESHOLD)
        self.assertEqual(stop.net_liquidation_pnl, Decimal("-5.00"))

        neutral_position = PaperPosition(
            opened_monotonic_ns=opened,
            filled_quantity=Decimal("10"),
            entry_gross_debit=Decimal("4.00"),
            allocated_entry_fees=Decimal("1.00"),
        )
        bids = (PriceLevel(Decimal("0.50"), Decimal("10")),)
        at_deadline = assess_exit(
            neutral_position,
            bids,
            now_monotonic_ns=400 * SECOND_NS,
            fee=lambda _quantity, _gross: Decimal("0"),
        )
        self.assertIs(at_deadline.action, ExitAction.TIME)
        self.assertIs(at_deadline.reason, ExitReason.HOLDING_HORIZON)
        self.assertEqual(at_deadline.net_liquidation_pnl, Decimal("0.00"))

        before_deadline = assess_exit(
            neutral_position,
            bids,
            now_monotonic_ns=400 * SECOND_NS - 1,
            fee=lambda _quantity, _gross: Decimal("0"),
        )
        self.assertIs(before_deadline.action, ExitAction.HOLD)
        self.assertIs(before_deadline.reason, ExitReason.WITHIN_BOUNDS)
        self.assertEqual(before_deadline.net_liquidation_pnl, Decimal("0.00"))

    def test_partial_bid_depth_does_not_invent_full_liquidation_pnl(self) -> None:
        result = assess_exit(
            PaperPosition(
                opened_monotonic_ns=100 * SECOND_NS,
                filled_quantity=Decimal("10"),
                entry_gross_debit=Decimal("4.00"),
                allocated_entry_fees=Decimal("1.00"),
            ),
            (PriceLevel(Decimal("0.50"), Decimal("5")),),
            now_monotonic_ns=200 * SECOND_NS,
            fee=lambda _quantity, _gross: Decimal("0"),
        )

        self.assertEqual(result.action.value, "portfolio_halt")
        self.assertEqual(result.reason.value, "insufficient_bid_depth")
        self.assertEqual(result.executable_quantity, Decimal("5"))
        self.assertEqual(result.residual_quantity, Decimal("5"))
        self.assertIsNone(result.net_liquidation_pnl)

    def test_mandatory_forced_exit_reasons_liquidate_at_trusted_depth(
        self,
    ) -> None:
        expected = {
            ForcedExitReason.THESIS_INVALIDATED: ExitReason.THESIS_INVALIDATED,
            ForcedExitReason.SOURCE_DISAGREEMENT: ExitReason.SOURCE_DISAGREEMENT,
            ForcedExitReason.SOURCE_CORRECTION: ExitReason.SOURCE_CORRECTION,
            ForcedExitReason.MARKET_LIFECYCLE: ExitReason.MARKET_LIFECYCLE,
            ForcedExitReason.RISK_RULE: ExitReason.RISK_RULE,
            ForcedExitReason.FAIR_VALUE_REACHED: ExitReason.FAIR_VALUE_REACHED,
        }
        position = PaperPosition(
            opened_monotonic_ns=100 * SECOND_NS,
            filled_quantity=Decimal("10"),
            entry_gross_debit=Decimal("4.00"),
            allocated_entry_fees=Decimal("1.00"),
        )

        for trigger, reason in expected.items():
            with self.subTest(trigger=trigger):
                result = assess_exit(
                    position,
                    (PriceLevel(Decimal("0.50"), Decimal("10")),),
                    now_monotonic_ns=200 * SECOND_NS,
                    fee=lambda _price, _quantity: Decimal("0"),
                    forced_exit=trigger,
                )

                self.assertIs(result.action, ExitAction.FORCED_EXIT)
                self.assertIs(result.reason, reason)
                self.assertEqual(result.executable_quantity, Decimal("10"))
                self.assertEqual(result.residual_quantity, Decimal("0"))
                self.assertEqual(result.net_liquidation_pnl, Decimal("0.00"))


class InputValidationTests(unittest.TestCase):
    def test_forecast_requires_artifact_identity_and_complete_diagnostics(
        self,
    ) -> None:
        valid = FiveMinuteForecast(
            artifact_version="markout-v1",
            artifact_sha256=ARTIFACT_SHA256,
            supported=True,
            frozen=True,
            calibrated=True,
            quantity=Decimal("10"),
            expected_net_pnl=Decimal("6.00"),
            lower_expected_net_pnl=Decimal("5.01"),
            upper_expected_net_pnl=Decimal("7.00"),
            fill_probability=Decimal("0.80"),
            loss_probability=Decimal("0.20"),
            tail_loss_estimate=Decimal("-8.00"),
            supporting_sample_count=500,
            abstention_reason=None,
        )
        invalid = (
            (lambda: replace(valid, artifact_version=""), "artifact_version"),
            (lambda: replace(valid, artifact_sha256="bad"), "artifact_sha256"),
            (
                lambda: replace(
                    valid,
                    fill_probability=Decimal("1.01"),
                ),
                "fill_probability",
            ),
            (
                lambda: replace(
                    valid,
                    loss_probability=Decimal("0.81"),
                ),
                "probability_consistency",
            ),
            (
                lambda: replace(
                    valid,
                    tail_loss_estimate=Decimal("0.01"),
                ),
                "tail_loss_estimate",
            ),
            (
                lambda: replace(valid, supporting_sample_count=0),
                "supporting_sample_count",
            ),
            (
                lambda: replace(
                    valid,
                    abstention_reason=(
                        ForecastAbstentionReason.DISTRIBUTION_SHIFT
                    ),
                ),
                "abstention_reason",
            ),
            (
                lambda: replace(
                    valid,
                    supported=False,
                    abstention_reason=None,
                ),
                "abstention_reason",
            ),
        )
        for build, message in invalid:
            with self.subTest(message=message):
                with self.assertRaisesRegex(
                    FiveMinutePathError,
                    f"^{message}$",
                ):
                    build()

        unsupported = replace(
            valid,
            supported=False,
            supporting_sample_count=0,
            abstention_reason=(
                ForecastAbstentionReason.INSUFFICIENT_MARKOUT_SUPPORT
            ),
        )
        self.assertFalse(unsupported.supported)
        self.assertIs(
            unsupported.abstention_reason,
            ForecastAbstentionReason.INSUFFICIENT_MARKOUT_SUPPORT,
        )

    def test_invalid_decimal_range_quantity_flag_and_time_inputs_are_rejected(
        self,
    ) -> None:
        invalid_values = (
            lambda: DipObservation(
                1,
                ContractSide.YES,
                0,
                1,
                Decimal("NaN"),
            ),
            lambda: DipObservation(
                -1,
                ContractSide.YES,
                0,
                1,
                Decimal("0.50"),
            ),
            lambda: DipObservation(
                1,
                ContractSide.YES,
                -1,
                1,
                Decimal("0.50"),
            ),
            lambda: DipObservation(
                1,
                ContractSide.YES,
                0,
                4,
                Decimal("0.50"),
            ),
            lambda: PriceLevel(Decimal("0"), Decimal("1")),
            lambda: PriceLevel(Decimal("1.01"), Decimal("1")),
            lambda: PriceLevel(Decimal("0.50"), Decimal("1.5")),
            lambda: FiveMinuteForecast(
                artifact_version="markout-v1",
                artifact_sha256=ARTIFACT_SHA256,
                supported=True,
                frozen=True,
                calibrated=True,
                quantity=Decimal("1"),
                expected_net_pnl=Decimal("4"),
                lower_expected_net_pnl=Decimal("5"),
                upper_expected_net_pnl=Decimal("6"),
                fill_probability=Decimal("0.80"),
                loss_probability=Decimal("0.20"),
                tail_loss_estimate=Decimal("-8"),
                supporting_sample_count=500,
                abstention_reason=None,
            ),
            lambda: PaperPosition(
                1,
                Decimal("1"),
                Decimal("50.00"),
                Decimal("0.01"),
            ),
        )
        for build in invalid_values:
            with self.subTest(build=build):
                with self.assertRaises(FiveMinutePathError):
                    build()

        with self.assertRaisesRegex(TypeError, "^price$"):
            PriceLevel("0.50", Decimal("1"))  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "^supported$"):
            FiveMinuteForecast(
                artifact_version="markout-v1",
                artifact_sha256=ARTIFACT_SHA256,
                supported=1,  # type: ignore[arg-type]
                frozen=True,
                calibrated=True,
                quantity=Decimal("1"),
                expected_net_pnl=Decimal("6"),
                lower_expected_net_pnl=Decimal("5.01"),
                upper_expected_net_pnl=Decimal("7"),
                fill_probability=Decimal("0.80"),
                loss_probability=Decimal("0.20"),
                tail_loss_estimate=Decimal("-8"),
                supporting_sample_count=500,
                abstention_reason=None,
            )

        level = PriceLevel(Decimal("0.40"), Decimal("1"))
        with self.assertRaisesRegex(FiveMinutePathError, "^debit_cap$"):
            size_ioc_entry(
                (level,),
                requested_quantity=Decimal("1"),
                fee=lambda _quantity, _gross: Decimal("0"),
                debit_cap=Decimal("50.01"),
            )
        with self.assertRaisesRegex(FiveMinutePathError, "^ask_levels$"):
            size_ioc_entry(
                (
                    PriceLevel(Decimal("0.50"), Decimal("1")),
                    PriceLevel(Decimal("0.40"), Decimal("1")),
                ),
                requested_quantity=Decimal("2"),
                fee=lambda _quantity, _gross: Decimal("0"),
            )
        with self.assertRaisesRegex(TypeError, "^fee$"):
            size_ioc_entry(
                (level,),
                requested_quantity=Decimal("1"),
                fee=lambda _quantity, _gross: 0,  # type: ignore[return-value]
            )
        with self.assertRaisesRegex(FiveMinutePathError, "^fee$"):
            size_ioc_entry(
                (level,),
                requested_quantity=Decimal("1"),
                fee=lambda _quantity, _gross: Decimal("-0.01"),
            )

        position = PaperPosition(
            10,
            Decimal("1"),
            Decimal("0.40"),
            Decimal("0.10"),
        )
        with self.assertRaisesRegex(FiveMinutePathError, "^bid_levels$"):
            assess_exit(
                position,
                (
                    PriceLevel(Decimal("0.40"), Decimal("1")),
                    PriceLevel(Decimal("0.50"), Decimal("1")),
                ),
                now_monotonic_ns=11,
                fee=lambda _quantity, _gross: Decimal("0"),
            )
        with self.assertRaisesRegex(FiveMinutePathError, "^now_monotonic_ns$"):
            assess_exit(
                position,
                (PriceLevel(Decimal("0.40"), Decimal("1")),),
                now_monotonic_ns=9,
                fee=lambda _quantity, _gross: Decimal("0"),
            )


class DecimalDeterminismTests(unittest.TestCase):
    def test_public_dataclass_invariants_ignore_process_decimal_context(self) -> None:
        original = getcontext().copy()
        try:
            setcontext(Context(prec=2, rounding=ROUND_DOWN))
            with self.assertRaisesRegex(
                FiveMinutePathError,
                "^entry_cash_at_risk$",
            ):
                PaperPosition(
                    0,
                    Decimal("1"),
                    Decimal("49.99"),
                    Decimal("0.02"),
                )
            with self.assertRaisesRegex(
                FiveMinutePathError,
                "^entry_capacity$",
            ):
                EntryCapacity(
                    requested_quantity=Decimal("3"),
                    filled_quantity=Decimal("3"),
                    fills=(
                        PriceLevel(Decimal("0.333"), Decimal("3")),
                    ),
                    gross_debit=Decimal("0.99"),
                    entry_fee=Decimal("0"),
                    all_in_debit=Decimal("0.99"),
                    status=CapacityStatus.FULL,
                )
        finally:
            setcontext(original)

    def test_dip_and_exit_arithmetic_ignore_the_process_decimal_context(self) -> None:
        current = DipObservation(
            2,
            ContractSide.YES,
            1,
            2,
            Decimal("0.222"),
        )
        history = (
            DipObservation(
                1,
                ContractSide.YES,
                1,
                2,
                Decimal("0.333"),
            ),
        )
        position = PaperPosition(
            0,
            Decimal("3"),
            Decimal("0.300"),
            Decimal("0"),
        )
        original = getcontext().copy()
        try:
            setcontext(Context(prec=2, rounding=ROUND_DOWN))
            dip = assess_v1_dip(history, current)
            exit_result = assess_exit(
                position,
                (PriceLevel(Decimal("0.333"), Decimal("3")),),
                now_monotonic_ns=1,
                fee=lambda _quantity, _gross: Decimal("0"),
            )
        finally:
            setcontext(original)

        self.assertEqual(dip.dip, Decimal("0.111"))
        self.assertEqual(exit_result.gross_proceeds, Decimal("0.999"))
        self.assertEqual(
            exit_result.net_liquidation_pnl,
            Decimal("0.699"),
        )


if __name__ == "__main__":
    unittest.main()
