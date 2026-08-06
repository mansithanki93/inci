from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

from inci_tennis_adapters.kalshi_v2 import (
    UnqualifiedCandidateL2Market,
    UnqualifiedTwoTickerL2State,
)
from inci_tennis_expert.contracts import PlayerSide
from inci_tennis_expert.fee_schedule import FrozenFeeSchedule
from inci_tennis_expert.live_paper_contracts import (
    LivePaperContractError,
    LivePaperMarketBinding,
)
from inci_tennis_expert.live_paper_execution import (
    PaperActionKind,
    PaperDecisionReason,
    LivePaperExecutionError,
    PaperPortfolioState,
    evaluate_live_paper_entry,
    project_paper_l2,
    reduce_paper_book,
)
from inci_tennis_expert.live_two_model import (
    LiveArtifactAuthority,
    LiveEdgeClaim,
    LiveTwoModelForecast,
)
from inci_tennis_expert.pilot_contracts import PilotOutcomeEstimate, PilotSupportReason
from inci_tennis_expert.live_paper_contracts import PaperScoreTrust


HOME_ID = "9b0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a1"
AWAY_ID = "8a0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a2"


def _binding() -> LivePaperMarketBinding:
    return LivePaperMarketBinding(
        canonical_match_id="match-1",
        scheduled_start_wall_ns=10,
        home_player_id="home-1",
        away_player_id="away-1",
        home_ticker="KXTENNIS-HOME",
        home_market_id=HOME_ID,
        home_yes_player_side=PlayerSide.HOME,
        away_ticker="KXTENNIS-AWAY",
        away_market_id=AWAY_ID,
        away_yes_player_side=PlayerSide.AWAY,
    )


def _raw_book(sequence: int = 1) -> UnqualifiedTwoTickerL2State:
    return UnqualifiedTwoTickerL2State(
        markets=(
            UnqualifiedCandidateL2Market(
                ticker="KXTENNIS-HOME", market_id=HOME_ID,
                yes_levels=((Decimal("0.30"), Decimal("10")),),
                no_levels=((Decimal("0.70"), Decimal("10")),),
            ),
            UnqualifiedCandidateL2Market(
                ticker="KXTENNIS-AWAY", market_id=AWAY_ID,
                yes_levels=((Decimal("0.20"), Decimal("10")),),
                no_levels=((Decimal("0.80"), Decimal("10")),),
            ),
        ),
        physical_connection_generation=1,
        subscription_id=2,
        global_sequence=sequence,
        state_sha256="a" * 64,
    )


def _raw_book_with_home_bid(sequence: int, price: Decimal) -> UnqualifiedTwoTickerL2State:
    original = _raw_book(sequence)
    home = replace(
        original.markets[0],
        yes_levels=((price, Decimal("10")),),
        no_levels=((Decimal("1") - price, Decimal("10")),),
    )
    return replace(original, markets=(home, original.markets[1]))


def _forecast() -> LiveTwoModelForecast:
    estimate = PilotOutcomeEstimate(
        model_version="model-v1", supported=True,
        home_next_point_probability=Decimal("0.8"),
        home_current_set_probability=Decimal("0.8"),
        home_match_probability=Decimal("0.9"),
        lower_home_match_probability=Decimal("0.9"),
        upper_home_match_probability=Decimal("0.9"), abstention_reason=None,
    )
    return LiveTwoModelForecast(
        trust=PaperScoreTrust.SINGLE_SOURCE_PAPER,
        forecast_label="UPDATED_PAPER",
        artifact_authority=LiveArtifactAuthority.TRAINED_ARTIFACT,
        edge_claim=LiveEdgeClaim.RESEARCH_ONLY,
        model_1=estimate, model_2=estimate,
        model_2_prior_belief_sha256="b" * 64,
        model_2_posterior_belief_sha256="c" * 64,
        static_artifact_sha256="d" * 64, dynamic_artifact_sha256="e" * 64,
        source_sha256="f" * 64, anchor_sha256="1" * 64,
        transition_sha256=None, resulting_state_sha256="2" * 64,
        supported=True, abstention_reason=None,
    )


def _state() -> PaperPortfolioState:
    return PaperPortfolioState(
        binding=_binding(), completed_sets=1, match_live=True,
        fee_schedule=FrozenFeeSchedule(
            schedule_id="fees-v1", series_tickers=("KXTENNIS",),
            taker_rate=Decimal("0"), maker_rate=Decimal("0"),
            taker_multiplier=Decimal("1"), maker_multiplier=Decimal("1"),
            trade_fee_precision=Decimal("0.0001"), balance_precision=Decimal("0.0001"),
            effective_from_wall_ns=1, effective_until_wall_ns=None,
        ),
        fee_series_ticker="KXTENNIS",
    )


class LivePaperExecutionTests(unittest.TestCase):
    def test_binding_rejects_non_uuid_and_wrapper_market_identity_drift(self) -> None:
        """Catches a caller relabelling a different market as the frozen match."""
        with self.assertRaises(LivePaperContractError):
            replace(_binding(), home_market_id="not-a-kalshi-uuid")
        binding = _binding()
        book = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        with self.assertRaises(LivePaperExecutionError):
            replace(book, home=replace(book.home, ticker="KXTENNIS-OTHER"))
        with self.assertRaises(LivePaperExecutionError):
            replace(book, away=replace(book.away, market_id=HOME_ID))
        with self.assertRaises(LivePaperExecutionError):
            replace(book, home=replace(book.home, yes_player_side=PlayerSide.AWAY))

    def test_monotonic_freshness_includes_uncertainty_at_exact_boundary(self) -> None:
        """Catches accepting a five-second-old capture whose uncertainty exceeds the limit."""
        binding = _binding()
        book = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=1, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        stale = evaluate_live_paper_entry(
            _forecast(), book, _state(), decision_wall_ns=6_000_000_000,
            decision_monotonic_ns=6_000_000_000,
        )
        self.assertEqual(stale.reason, PaperDecisionReason.BOOK_STALE)
        exact = evaluate_live_paper_entry(
            _forecast(), replace(book, clock_uncertainty_ns=0), _state(),
            decision_wall_ns=6_000_000_000, decision_monotonic_ns=6_000_000_000,
        )
        self.assertEqual(exact.reason, PaperDecisionReason.ACCEPTED)
        future = evaluate_live_paper_entry(
            _forecast(), replace(book, captured_monotonic_ns=6_000_000_001), _state(),
            decision_wall_ns=6_000_000_000, decision_monotonic_ns=6_000_000_000,
        )
        self.assertEqual(future.reason, PaperDecisionReason.BOOK_STALE)
        inconsistent = evaluate_live_paper_entry(
            _forecast(), replace(book, clock_uncertainty_ns=0), _state(),
            decision_wall_ns=6_000_000_001, decision_monotonic_ns=1_000_000_001,
        )
        self.assertEqual(inconsistent.reason, PaperDecisionReason.BOOK_STALE)

    def test_policy_rejects_when_only_smaller_size_meets_edge(self) -> None:
        """Catches silently downsizing after the fee-constrained maximum fails its edge gate."""
        binding = _binding()
        raw = _raw_book()
        home = replace(
            raw.markets[0], yes_levels=(),
            no_levels=((Decimal("0.01"), Decimal("1")), (Decimal("0.80"), Decimal("9"))),
        )
        away = replace(raw.markets[1], yes_levels=(), no_levels=())
        book = project_paper_l2(
            replace(raw, markets=(home, away)), binding=binding,
            raw_parent_receipt_sha256="3" * 64, captured_wall_ns=1_000_000_000,
            captured_monotonic_ns=1_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        model = replace(
            _forecast().model_1, home_match_probability=Decimal("0.77"),
            lower_home_match_probability=Decimal("0.77"),
            upper_home_match_probability=Decimal("0.77"),
        )
        forecast = replace(_forecast(), model_1=model, model_2=model)
        decision = evaluate_live_paper_entry(
            forecast, book, _state(), decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        self.assertEqual(decision.reason, PaperDecisionReason.EDGE_BELOW_MINIMUM)

    def test_raw_endpoint_levels_project_but_are_not_executable(self) -> None:
        """Catches rejecting reducer-valid 0/1 levels instead of excluding them from fills."""
        binding = _binding()
        raw = _raw_book()
        home = replace(
            raw.markets[0], yes_levels=((Decimal("0"), Decimal("10")),),
            no_levels=((Decimal("1"), Decimal("10")),),
        )
        book = project_paper_l2(
            replace(raw, markets=(home, raw.markets[1])), binding=binding,
            raw_parent_receipt_sha256="3" * 64, captured_wall_ns=1_000_000_000,
            captured_monotonic_ns=1_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        self.assertEqual(book.home.yes_bids[0].price, Decimal("0"))
        self.assertEqual(book.home.yes_asks[0].price, Decimal("0"))

    def test_entry_gates_cover_incomplete_book_first_set_and_model_support(self) -> None:
        """Catches an entry bypassing a typed policy gate."""
        binding = _binding()
        book = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        first_set = evaluate_live_paper_entry(
            _forecast(), book, replace(_state(), completed_sets=0),
            decision_wall_ns=1_000_000_000, decision_monotonic_ns=1_000_000_000,
        )
        self.assertEqual(first_set.reason, PaperDecisionReason.BEFORE_COMPLETED_SET)
        incomplete = evaluate_live_paper_entry(
            _forecast(), replace(book, complete=False), _state(),
            decision_wall_ns=1_000_000_000, decision_monotonic_ns=1_000_000_000,
        )
        self.assertEqual(incomplete.reason, PaperDecisionReason.BOOK_INCOMPLETE)
        unsupported_model = replace(
            _forecast().model_1, supported=False,
            home_next_point_probability=None, home_current_set_probability=None,
            home_match_probability=None, lower_home_match_probability=None,
            upper_home_match_probability=None, abstention_reason=PilotSupportReason.UNSUPPORTED_NO_MARKOUT_KERNEL,
        )
        unsupported = evaluate_live_paper_entry(
            replace(_forecast(), model_1=unsupported_model, supported=False), book, _state(),
            decision_wall_ns=1_000_000_000, decision_monotonic_ns=1_000_000_000,
        )
        self.assertEqual(unsupported.reason, PaperDecisionReason.FORECAST_UNSUPPORTED)

    def test_fee_constrained_largest_quantity_and_partial_depth_are_exact(self) -> None:
        """Catches ignoring taker fees, fractional depth, or reused displayed size."""
        binding = _binding()
        raw = _raw_book()
        home = replace(raw.markets[0], yes_levels=(), no_levels=((Decimal("0.70"), Decimal("100")),))
        away = replace(raw.markets[1], yes_levels=(), no_levels=())
        first = project_paper_l2(
            replace(raw, markets=(home, away)), binding=binding,
            raw_parent_receipt_sha256="3" * 64, captured_wall_ns=1_000_000_000,
            captured_monotonic_ns=1_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        costly = replace(
            _state(),
            fee_schedule=replace(_state().fee_schedule, taker_rate=Decimal("1")),
        )
        decision = evaluate_live_paper_entry(
            _forecast(), first, costly, decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        self.assertEqual(decision.action.quantity, Decimal("98"))

        partial_home = replace(home, no_levels=((Decimal("0.70"), Decimal("3")),))
        partial = project_paper_l2(
            replace(raw, markets=(partial_home, away), global_sequence=2), binding=binding,
            raw_parent_receipt_sha256="4" * 64, captured_wall_ns=2_000_000_000,
            captured_monotonic_ns=2_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        reduced, events = reduce_paper_book(
            decision.state, partial, observed_wall_ns=2_000_000_000,
            observed_monotonic_ns=2_000_000_000,
        )
        self.assertEqual(events[0].fill.quantity, Decimal("3"))
        self.assertEqual(reduced.pending_action.remaining_quantity, Decimal("95"))
        reused, events = reduce_paper_book(
            reduced, partial, observed_wall_ns=2_000_000_001,
            observed_monotonic_ns=2_000_000_001,
        )
        self.assertFalse(events)
        self.assertEqual(reused.pending_action.remaining_quantity, Decimal("95"))

    def test_invalid_reducer_transport_numbers_fail_before_projection(self) -> None:
        """Catches incomplete or non-positive reducer transport identity reaching policy."""
        binding = _binding()
        for raw in (
            replace(_raw_book(), physical_connection_generation=0),
            replace(_raw_book(), subscription_id=0),
            replace(_raw_book(), global_sequence=0),
            replace(_raw_book(), markets=(_raw_book().markets[0],)),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(LivePaperExecutionError):
                    project_paper_l2(
                        raw, binding=binding, raw_parent_receipt_sha256="3" * 64,
                        captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
                        clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
                        away_ticker=binding.away_ticker,
                    )

    def test_loss_and_horizon_exits_are_delayed_and_insufficient_bids_leave_residual(self) -> None:
        """Catches fabricating a flatten when a -$5 or horizon SELL lacks bid depth."""
        binding = _binding()
        raw = _raw_book()
        home = replace(raw.markets[0], yes_levels=((Decimal("0.30"), Decimal("50")),), no_levels=((Decimal("0.70"), Decimal("50")),))
        first = project_paper_l2(
            replace(raw, markets=(home, raw.markets[1])), binding=binding,
            raw_parent_receipt_sha256="3" * 64, captured_wall_ns=1_000_000_000,
            captured_monotonic_ns=1_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        decision = evaluate_live_paper_entry(
            _forecast(), first, _state(), decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        entered, events = reduce_paper_book(
            decision.state,
            project_paper_l2(
                replace(raw, markets=(home, raw.markets[1]), global_sequence=2), binding=binding,
                raw_parent_receipt_sha256="4" * 64, captured_wall_ns=2_000_000_000,
                captured_monotonic_ns=2_000_000_000, clock_uncertainty_ns=0,
                home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
            ),
            observed_wall_ns=2_000_000_000, observed_monotonic_ns=2_000_000_000,
        )
        self.assertEqual(events[0].fill.quantity, Decimal("50"))
        loss_home = replace(home, yes_levels=((Decimal("0.10"), Decimal("50")),), no_levels=((Decimal("0.90"), Decimal("50")),))
        loss_mark = project_paper_l2(
            replace(raw, markets=(loss_home, raw.markets[1]), global_sequence=3), binding=binding,
            raw_parent_receipt_sha256="5" * 64, captured_wall_ns=3_000_000_000,
            captured_monotonic_ns=3_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        pending, events = reduce_paper_book(
            entered, loss_mark, observed_wall_ns=3_000_000_000,
            observed_monotonic_ns=3_000_000_000,
        )
        self.assertFalse(events)
        self.assertEqual(pending.pending_action.kind, PaperActionKind.SELL)
        shallow_home = replace(loss_home, yes_levels=((Decimal("0.10"), Decimal("3")),))
        shallow = project_paper_l2(
            replace(raw, markets=(shallow_home, raw.markets[1]), global_sequence=4), binding=binding,
            raw_parent_receipt_sha256="6" * 64, captured_wall_ns=4_000_000_000,
            captured_monotonic_ns=4_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        residual, events = reduce_paper_book(
            pending, shallow, observed_wall_ns=4_000_000_000,
            observed_monotonic_ns=4_000_000_000,
        )
        self.assertEqual(events[0].fill.quantity, Decimal("3"))
        self.assertEqual(residual.position.quantity, Decimal("47"))
        self.assertEqual(residual.pending_action.remaining_quantity, Decimal("47"))

        horizon = reduce_paper_book(
            entered,
            project_paper_l2(
                replace(raw, markets=(home, raw.markets[1]), global_sequence=3), binding=binding,
                raw_parent_receipt_sha256="7" * 64, captured_wall_ns=302_000_000_000,
                captured_monotonic_ns=302_000_000_000, clock_uncertainty_ns=0,
                home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
            ),
            observed_wall_ns=302_000_000_000, observed_monotonic_ns=302_000_000_000,
        )[0]
        self.assertEqual(horizon.pending_action.kind, PaperActionKind.SELL)

    def test_yes_no_ladders_are_executable_and_fill_only_from_later_book(self) -> None:
        """Catches filling the decision snapshot or reusing visible depth."""
        binding = _binding()
        book = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        self.assertEqual(book.home.yes_asks[0].price, Decimal("0.30"))
        self.assertEqual(book.home.yes_bids[0].price, Decimal("0.30"))

        decision = evaluate_live_paper_entry(
            _forecast(), book, _state(), decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        self.assertEqual(decision.action.kind, PaperActionKind.BUY)
        self.assertEqual(decision.action.quantity, Decimal("10"))
        self.assertEqual(decision.claim, "SETTLEMENT_VALUE_PROXY")

        unchanged, events = reduce_paper_book(
            decision.state, book, observed_wall_ns=2_000_000_000,
            observed_monotonic_ns=2_000_000_000,
        )
        self.assertFalse(events)
        self.assertEqual(unchanged.pending_action, decision.action)

        later = project_paper_l2(
            _raw_book(sequence=2), binding=binding, raw_parent_receipt_sha256="4" * 64,
            captured_wall_ns=2_000_000_001, captured_monotonic_ns=2_000_000_001,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        filled, events = reduce_paper_book(
            unchanged, later, observed_wall_ns=2_000_000_001,
            observed_monotonic_ns=2_000_000_001,
        )
        self.assertEqual(events[0].fill.quantity, Decimal("10"))
        self.assertEqual(filled.pending_action, None)
        self.assertEqual(filled.position.quantity, Decimal("10"))

    def test_arrival_uses_a_frame_captured_after_the_one_second_due_time(self) -> None:
        """Catches treating a pre-arrival frame as eligible just because it is observed late."""
        binding = _binding()
        decision_book = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        decision = evaluate_live_paper_entry(
            _forecast(), decision_book, _state(), decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        early_book = project_paper_l2(
            _raw_book(sequence=2), binding=binding, raw_parent_receipt_sha256="4" * 64,
            captured_wall_ns=1_500_000_000, captured_monotonic_ns=1_500_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        state, events = reduce_paper_book(
            decision.state, early_book, observed_wall_ns=2_000_000_000,
            observed_monotonic_ns=2_000_000_000,
        )
        self.assertFalse(events)
        self.assertEqual(state.pending_action, decision.action)

    def test_profitable_exit_is_delayed_and_never_fills_its_mark_book(self) -> None:
        """Catches a simulated SELL consuming the bid frame that triggered it."""
        binding = _binding()
        first = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        decision = evaluate_live_paper_entry(
            _forecast(), first, _state(), decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        second = project_paper_l2(
            _raw_book(sequence=2), binding=binding, raw_parent_receipt_sha256="4" * 64,
            captured_wall_ns=2_000_000_000, captured_monotonic_ns=2_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        entered, events = reduce_paper_book(
            decision.state, second, observed_wall_ns=2_000_000_000,
            observed_monotonic_ns=2_000_000_000,
        )
        self.assertEqual(events[0].fill.action_kind, PaperActionKind.BUY)

        mark = project_paper_l2(
            _raw_book_with_home_bid(3, Decimal("0.90")), binding=binding,
            raw_parent_receipt_sha256="5" * 64, captured_wall_ns=3_000_000_000,
            captured_monotonic_ns=3_000_000_000, clock_uncertainty_ns=0,
            home_ticker=binding.home_ticker, away_ticker=binding.away_ticker,
        )
        pending_sell, events = reduce_paper_book(
            entered, mark, observed_wall_ns=3_000_000_000,
            observed_monotonic_ns=3_000_000_000,
        )
        self.assertFalse(events)
        self.assertEqual(pending_sell.pending_action.kind, PaperActionKind.SELL)

        closed, events = reduce_paper_book(
            pending_sell, mark, observed_wall_ns=4_000_000_000,
            observed_monotonic_ns=4_000_000_000,
        )
        self.assertFalse(events)
        self.assertIsNotNone(closed.position)

    def test_generation_change_cannot_fill_a_pending_action(self) -> None:
        """Catches reconnect depth being treated as continuous with the decision book."""
        binding = _binding()
        first = project_paper_l2(
            _raw_book(), binding=binding, raw_parent_receipt_sha256="3" * 64,
            captured_wall_ns=1_000_000_000, captured_monotonic_ns=1_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        decision = evaluate_live_paper_entry(
            _forecast(), first, _state(), decision_wall_ns=1_000_000_000,
            decision_monotonic_ns=1_000_000_000,
        )
        reconnect = project_paper_l2(
            replace(_raw_book(sequence=2), physical_connection_generation=2),
            binding=binding, raw_parent_receipt_sha256="4" * 64,
            captured_wall_ns=2_000_000_000, captured_monotonic_ns=2_000_000_000,
            clock_uncertainty_ns=0, home_ticker=binding.home_ticker,
            away_ticker=binding.away_ticker,
        )
        unchanged, events = reduce_paper_book(
            decision.state, reconnect, observed_wall_ns=2_000_000_000,
            observed_monotonic_ns=2_000_000_000,
        )
        self.assertFalse(events)
        self.assertEqual(unchanged.pending_action, decision.action)


if __name__ == "__main__":
    unittest.main()
