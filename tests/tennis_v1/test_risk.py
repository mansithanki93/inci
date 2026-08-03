from __future__ import annotations

from decimal import Context, Decimal, ROUND_DOWN, getcontext, setcontext
import unittest

from inci_tennis_expert.contracts import ContractSide
from inci_tennis_expert.risk import (
    AttemptRecord,
    FrozenRiskPolicy,
    RiskError,
    RiskReason,
    RiskRequest,
    RiskReservation,
    RiskState,
    StopRecord,
    close_position,
    initial_risk_state,
    reserve,
)


class CanonicalMatchRiskTests(unittest.TestCase):
    def stopped_state(self):
        policy = FrozenRiskPolicy(3, Decimal("30"), 3, 60)
        request = RiskRequest(
            "request-1",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            10,
            100,
            True,
        )
        occupied, _ = reserve(initial_risk_state(), request, policy)
        stopped = close_position(
            occupied,
            canonical_match_id="match-1",
            realized_pnl=Decimal("-5"),
            provider_revision=10,
            closed_monotonic_ns=120,
            stopped=True,
        )
        return policy, stopped

    def test_opposing_direction_cannot_reserve_the_same_match(self) -> None:
        policy = FrozenRiskPolicy(
            maximum_occupied_matches=3,
            maximum_session_loss=Decimal("30"),
            maximum_attempts_per_match=3,
            stop_cooldown_ns=60,
        )
        first = RiskRequest(
            request_id="request-1",
            canonical_match_id="match-1",
            contract_side=ContractSide.YES,
            entry_debit=Decimal("50"),
            provider_revision=10,
            requested_monotonic_ns=100,
            signal_reset=True,
        )
        second = RiskRequest(
            request_id="request-2",
            canonical_match_id="match-1",
            contract_side=ContractSide.NO,
            entry_debit=Decimal("50"),
            provider_revision=11,
            requested_monotonic_ns=101,
            signal_reset=True,
        )

        occupied, accepted = reserve(initial_risk_state(), first, policy)
        unchanged, rejected = reserve(occupied, second, policy)

        self.assertEqual(accepted.reason, RiskReason.RESERVED)
        self.assertEqual(rejected.reason, RiskReason.MATCH_OCCUPIED)
        self.assertEqual(unchanged, occupied)

    def test_stopped_match_requires_a_complete_signal_reset(self) -> None:
        policy, stopped = self.stopped_state()
        retry = RiskRequest(
            "request-2",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            11,
            200,
            False,
        )

        unchanged, rejected = reserve(stopped, retry, policy)

        self.assertEqual(rejected.reason, RiskReason.SIGNAL_NOT_RESET)
        self.assertEqual(unchanged, stopped)

    def test_stopped_match_requires_a_new_trusted_score_revision(self) -> None:
        policy, stopped = self.stopped_state()
        retry = RiskRequest(
            "request-2",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            10,
            200,
            True,
        )

        unchanged, rejected = reserve(stopped, retry, policy)

        self.assertEqual(rejected.reason, RiskReason.SCORE_NOT_ADVANCED)
        self.assertEqual(unchanged, stopped)

    def test_stopped_match_cannot_reenter_before_cooldown_deadline(self) -> None:
        policy, stopped = self.stopped_state()
        retry = RiskRequest(
            "request-2",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            11,
            179,
            True,
        )

        unchanged, rejected = reserve(stopped, retry, policy)

        self.assertEqual(rejected.reason, RiskReason.COOLDOWN)
        self.assertEqual(unchanged, stopped)

    def test_portfolio_capacity_counts_pending_reservations(self) -> None:
        policy = FrozenRiskPolicy(1, Decimal("30"), 3, 60)
        first = RiskRequest(
            "request-1",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            10,
            100,
            True,
        )
        second = RiskRequest(
            "request-2",
            "match-2",
            ContractSide.YES,
            Decimal("50"),
            1,
            101,
            True,
        )
        occupied, _ = reserve(initial_risk_state(), first, policy)

        unchanged, rejected = reserve(occupied, second, policy)

        self.assertEqual(rejected.reason, RiskReason.PORTFOLIO_FULL)
        self.assertEqual(unchanged, occupied)

    def test_session_loss_limit_blocks_new_reservations_at_equality(self) -> None:
        policy = FrozenRiskPolicy(3, Decimal("30"), 3, 60)
        first = RiskRequest(
            "request-1",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            10,
            100,
            True,
        )
        occupied, _ = reserve(initial_risk_state(), first, policy)
        losing = close_position(
            occupied,
            canonical_match_id="match-1",
            realized_pnl=Decimal("-30"),
            provider_revision=10,
            closed_monotonic_ns=120,
            stopped=False,
        )
        second = RiskRequest(
            "request-2",
            "match-2",
            ContractSide.YES,
            Decimal("50"),
            1,
            200,
            True,
        )

        unchanged, rejected = reserve(losing, second, policy)

        self.assertEqual(rejected.reason, RiskReason.SESSION_LOSS_LIMIT)
        self.assertEqual(unchanged, losing)

    def test_per_match_attempt_limit_counts_closed_positions(self) -> None:
        policy = FrozenRiskPolicy(3, Decimal("30"), 1, 60)
        first = RiskRequest(
            "request-1",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            10,
            100,
            True,
        )
        occupied, _ = reserve(initial_risk_state(), first, policy)
        closed = close_position(
            occupied,
            canonical_match_id="match-1",
            realized_pnl=Decimal("1"),
            provider_revision=10,
            closed_monotonic_ns=120,
            stopped=False,
        )
        retry = RiskRequest(
            "request-2",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            11,
            200,
            True,
        )

        unchanged, rejected = reserve(closed, retry, policy)

        self.assertEqual(rejected.reason, RiskReason.ATTEMPT_LIMIT)
        self.assertEqual(unchanged, closed)

    def test_successful_post_stop_reservation_consumes_the_reset_barrier(self) -> None:
        policy, stopped = self.stopped_state()
        retry = RiskRequest(
            "request-2",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            11,
            180,
            True,
        )
        occupied, accepted = reserve(stopped, retry, policy)
        closed = close_position(
            occupied,
            canonical_match_id="match-1",
            realized_pnl=Decimal("1"),
            provider_revision=11,
            closed_monotonic_ns=200,
            stopped=False,
        )
        third = RiskRequest(
            "request-3",
            "match-1",
            ContractSide.YES,
            Decimal("50"),
            11,
            220,
            False,
        )

        _, third_result = reserve(closed, third, policy)

        self.assertEqual(accepted.reason, RiskReason.RESERVED)
        self.assertEqual(third_result.reason, RiskReason.RESERVED)


class RiskStateInvariantTests(unittest.TestCase):
    def request(self, request_id: str = "request-1") -> RiskRequest:
        return RiskRequest(
            request_id=request_id,
            canonical_match_id="match-1",
            contract_side=ContractSide.YES,
            entry_debit=Decimal("10"),
            provider_revision=1,
            requested_monotonic_ns=1,
            signal_reset=True,
        )

    def test_records_reject_invalid_payloads(self) -> None:
        request = self.request()

        with self.assertRaises(RiskError):
            RiskReservation(RiskReason.COOLDOWN, request)
        with self.assertRaises(RiskError):
            StopRecord("", 1, 1)
        with self.assertRaises(RiskError):
            AttemptRecord("match-1", 0)

    def test_state_rejects_duplicate_match_and_request_identity(self) -> None:
        reservation = RiskReservation(RiskReason.RESERVED, self.request())
        duplicate_request = RiskReservation(
            RiskReason.RESERVED,
            RiskRequest(
                request_id="request-1",
                canonical_match_id="match-2",
                contract_side=ContractSide.YES,
                entry_debit=Decimal("10"),
                provider_revision=1,
                requested_monotonic_ns=1,
                signal_reset=True,
            ),
        )

        with self.assertRaises(RiskError):
            RiskState(
                reservations=(reservation, reservation),
                stops=(),
                attempts=(),
                realized_pnl=Decimal("0"),
            )
        with self.assertRaises(RiskError):
            RiskState(
                reservations=(reservation, duplicate_request),
                stops=(),
                attempts=(),
                realized_pnl=Decimal("0"),
            )

    def test_state_rejects_nonfinite_pnl_and_duplicate_records(self) -> None:
        stop = StopRecord("match-1", 1, 1)
        attempt = AttemptRecord("match-1", 1)

        with self.assertRaises(RiskError):
            RiskState((), (), (), Decimal("NaN"))
        with self.assertRaises(RiskError):
            RiskState((), (stop, stop), (), Decimal("0"))
        with self.assertRaises(RiskError):
            RiskState((), (), (attempt, attempt), Decimal("0"))

    def test_realized_pnl_accumulation_ignores_process_decimal_context(
        self,
    ) -> None:
        policy = FrozenRiskPolicy(2, Decimal("30"), 2, 0)
        request = self.request()
        seeded = RiskState((), (), (), Decimal("-1.23"))
        occupied, _ = reserve(seeded, request, policy)
        original = getcontext().copy()
        try:
            setcontext(Context(prec=2, rounding=ROUND_DOWN))
            closed = close_position(
                occupied,
                canonical_match_id="match-1",
                realized_pnl=Decimal("-4.56"),
                provider_revision=1,
                closed_monotonic_ns=2,
                stopped=False,
            )
        finally:
            setcontext(original)

        self.assertEqual(closed.realized_pnl, Decimal("-5.79"))


if __name__ == "__main__":
    unittest.main()
