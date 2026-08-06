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
from inci_tennis_expert.live_paper_contracts import LivePaperMarketBinding
from inci_tennis_expert.live_paper_execution import (
    PaperActionKind,
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
from inci_tennis_expert.pilot_contracts import PilotOutcomeEstimate
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
