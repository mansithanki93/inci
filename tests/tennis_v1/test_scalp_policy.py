from __future__ import annotations

from decimal import Decimal
import sys
import unittest

sys.dont_write_bytecode = True

from inci_tennis_expert.contracts import (
    BookLevel,
    BookState,
    ContractSide,
    DecisionAction,
    DecisionReason,
    FairValueEstimate,
    MarketStatus,
    PlayerSide,
)
from inci_tennis_expert.fee_schedule import (
    fill_fee_usd,
    sealed_kalshi_taker_fee_schedule,
    trade_fee_usd,
)
from inci_tennis_expert.market_book import book_from_snapshot
from inci_tennis_expert.contracts import BookSnapshot
from inci_tennis_expert.scalp_policy import (
    decide_scalp_entry,
    decide_scalp_exit,
    estimate_scalp_entry,
    open_scalp_position,
    sealed_short_horizon_scalp_policy,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def fair(**changes: object) -> FairValueEstimate:
    values: dict[str, object] = {
        "player_side": PlayerSide.HOME,
        "fair_probability": Decimal("0.62"),
        "lower_probability": Decimal("0.60"),
        "upper_probability": Decimal("0.64"),
        "supported": True,
        "stratum": "bo3-hard-live",
        "model_sha256": SHA_A,
        "prematch_artifact_sha256": SHA_B,
        "feature_definition_sha256": SHA_C,
        "feature_vector_sha256": SHA_D,
        "calibration_artifact_sha256": SHA_E,
        "abstention_reason": None,
    }
    values.update(changes)
    return FairValueEstimate(**values)  # type: ignore[arg-type]


def book(
    *,
    yes_bid: str = "0.40",
    no_bid: str = "0.52",
    yes_qty: str = "50",
    no_qty: str = "50",
    status: MarketStatus = MarketStatus.OPEN,
) -> BookState:
    snapshot = BookSnapshot(
        ticker="MATCH-HOME",
        connection_epoch=1,
        sequence=10,
        market_status=status,
        scheduled_close_wall_ns=10_000_000_000_000,
        source_wall_ns=100,
        observed_monotonic_ns=200,
        clock_uncertainty_ns=2,
        yes_bids=(BookLevel(Decimal(yes_bid), Decimal(yes_qty)),),
        no_bids=(BookLevel(Decimal(no_bid), Decimal(no_qty)),),
    )
    return book_from_snapshot(snapshot).state


class FeeScheduleTests(unittest.TestCase):
    def test_taker_fee_matches_kalshi_peak_formula(self) -> None:
        schedule = sealed_kalshi_taker_fee_schedule()
        # 100 contracts at 0.50 -> ceil(0.07 * 100 * 0.25) = 1.75
        fee = trade_fee_usd(
            schedule,
            Decimal("0.50"),
            Decimal("100"),
            taker=True,
        )
        self.assertEqual(fee, Decimal("1.7500"))

    def test_fill_fee_includes_balance_rounding(self) -> None:
        schedule = sealed_kalshi_taker_fee_schedule()
        fee = fill_fee_usd(
            schedule,
            Decimal("0.52"),
            Decimal("30"),
            side="BUY",
            taker=True,
        )
        self.assertGreater(fee, Decimal("0"))


class ScalpEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fees = sealed_kalshi_taker_fee_schedule()
        self.policy = sealed_short_horizon_scalp_policy(
            self.fees,
            target_net_pnl_usd=Decimal("3.00"),
            quantity=Decimal("50"),
        )

    def test_entry_when_lower_fair_clears_ask_fees_and_target(self) -> None:
        # YES ask = 1 - 0.52 = 0.48; lower fair 0.60; exit at 0.62
        decision = decide_scalp_entry(
            fair(),
            book(no_bid="0.52", no_qty="50"),
            PlayerSide.HOME,
            self.fees,
            self.policy,
        )
        self.assertIs(decision.action, DecisionAction.PAPER_BUY)
        self.assertIs(
            decision.reason,
            DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE,
        )
        self.assertTrue(decision.estimate.supported)
        self.assertEqual(decision.estimate.contract_side, ContractSide.YES)
        self.assertGreaterEqual(
            decision.estimate.lower_projected_net_pnl,
            Decimal("3.00"),
        )

    def test_abstain_when_uncalibrated(self) -> None:
        decision = decide_scalp_entry(
            fair(calibration_artifact_sha256=None),
            book(),
            PlayerSide.HOME,
            self.fees,
            self.policy,
        )
        self.assertIs(decision.action, DecisionAction.ABSTAIN)
        self.assertIs(decision.reason, DecisionReason.MODEL_UNSUPPORTED)

    def test_abstain_when_edge_below_three_dollar_target(self) -> None:
        # Tiny edge: ask 0.59, fair 0.62 / lower 0.60
        decision = decide_scalp_entry(
            fair(
                fair_probability=Decimal("0.62"),
                lower_probability=Decimal("0.60"),
                upper_probability=Decimal("0.64"),
            ),
            book(no_bid="0.41", no_qty="50"),  # ask = 0.59
            PlayerSide.HOME,
            self.fees,
            self.policy,
        )
        self.assertIs(decision.action, DecisionAction.ABSTAIN)
        self.assertIs(decision.reason, DecisionReason.EDGE_BELOW_COST)

    def test_abstain_insufficient_depth(self) -> None:
        decision = decide_scalp_entry(
            fair(),
            book(no_bid="0.48", no_qty="10"),
            PlayerSide.HOME,
            self.fees,
            self.policy,
        )
        self.assertIs(decision.action, DecisionAction.ABSTAIN)
        self.assertIs(decision.reason, DecisionReason.INSUFFICIENT_DEPTH)

    def test_buy_no_when_yes_is_away_player(self) -> None:
        decision = decide_scalp_entry(
            fair(player_side=PlayerSide.HOME),
            book(yes_bid="0.52", yes_qty="50", no_bid="0.40", no_qty="50"),
            PlayerSide.AWAY,
            self.fees,
            self.policy,
        )
        self.assertIs(decision.action, DecisionAction.PAPER_BUY)
        self.assertEqual(decision.estimate.contract_side, ContractSide.NO)


class ScalpExitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fees = sealed_kalshi_taker_fee_schedule()
        self.policy = sealed_short_horizon_scalp_policy(
            self.fees,
            target_net_pnl_usd=Decimal("3.00"),
            quantity=Decimal("50"),
            max_holding_wall_ns=300_000_000_000,
        )
        entry_fair = fair()
        decision = decide_scalp_entry(
            entry_fair,
            book(no_bid="0.52", no_qty="50"),
            PlayerSide.HOME,
            self.fees,
            self.policy,
        )
        self.assertIs(decision.action, DecisionAction.PAPER_BUY)
        self.position = open_scalp_position(
            decision,
            entry_fair,
            ticker="MATCH-HOME",
            entry_wall_ns=1_000,
        )

    def test_exit_on_fair_value_convergence(self) -> None:
        # Book has moved: YES bid near fair 0.62
        current = fair(
            fair_probability=Decimal("0.62"),
            lower_probability=Decimal("0.60"),
            upper_probability=Decimal("0.64"),
        )
        exit_decision = decide_scalp_exit(
            self.position,
            current,
            book(yes_bid="0.61", yes_qty="50", no_bid="0.35", no_qty="50"),
            self.fees,
            self.policy,
            decision_wall_ns=2_000,
        )
        self.assertIs(exit_decision.action, DecisionAction.PAPER_SELL)
        self.assertIs(
            exit_decision.reason,
            DecisionReason.FAIR_VALUE_CONVERGED,
        )
        self.assertGreater(exit_decision.projected_net_pnl, Decimal("0"))

    def test_exit_on_thesis_invalidation(self) -> None:
        current = fair(
            fair_probability=Decimal("0.45"),
            lower_probability=Decimal("0.40"),
            upper_probability=Decimal("0.48"),
        )
        exit_decision = decide_scalp_exit(
            self.position,
            current,
            book(yes_bid="0.40", yes_qty="50", no_bid="0.55", no_qty="50"),
            self.fees,
            self.policy,
            decision_wall_ns=2_000,
        )
        self.assertIs(exit_decision.action, DecisionAction.PAPER_SELL)
        self.assertIs(
            exit_decision.reason,
            DecisionReason.THESIS_INVALIDATED,
        )

    def test_exit_on_holding_horizon(self) -> None:
        current = fair()
        exit_decision = decide_scalp_exit(
            self.position,
            current,
            book(yes_bid="0.50", yes_qty="50", no_bid="0.45", no_qty="50"),
            self.fees,
            self.policy,
            decision_wall_ns=1_000 + 300_000_000_000,
        )
        self.assertIs(exit_decision.action, DecisionAction.PAPER_SELL)
        self.assertIs(
            exit_decision.reason,
            DecisionReason.HOLDING_HORIZON_REACHED,
        )

    def test_hold_when_still_waiting_for_swing(self) -> None:
        current = fair()
        exit_decision = decide_scalp_exit(
            self.position,
            current,
            book(yes_bid="0.53", yes_qty="50", no_bid="0.40", no_qty="50"),
            self.fees,
            self.policy,
            decision_wall_ns=2_000,
        )
        self.assertIs(exit_decision.action, DecisionAction.ABSTAIN)
        self.assertIs(
            exit_decision.reason,
            DecisionReason.SIGNAL_NOT_TRIGGERED,
        )


class EstimateHelpers(unittest.TestCase):
    def test_estimate_matches_decide_support(self) -> None:
        fees = sealed_kalshi_taker_fee_schedule()
        policy = sealed_short_horizon_scalp_policy(fees)
        estimate = estimate_scalp_entry(
            fair(),
            book(no_bid="0.52", no_qty="50"),
            PlayerSide.HOME,
            fees,
            policy,
        )
        self.assertTrue(estimate.supported)
        self.assertGreaterEqual(estimate.edge_vs_ask, Decimal("0.10"))


if __name__ == "__main__":
    unittest.main()
