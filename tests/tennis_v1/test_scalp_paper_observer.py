from __future__ import annotations

from decimal import Decimal
import sys
import unittest

sys.dont_write_bytecode = True

from inci_tennis_expert.contracts import (
    BookLevel,
    DecisionAction,
    DecisionReason,
    PlayerSide,
    SyncReason,
)
from inci_tennis_expert.engine import (
    fair_value_for_opportunity,
    make_default_clip_bundle,
    observe_clip_on_opportunity,
)
from inci_tennis_expert.prematch_model import PrematchPrior
from inci_tennis_expert.synchronizer import (
    synchronization_session_from_artifacts,
    synchronize,
)
from inci_tennis_runtime.scalp_paper_observer import PaperClipSession
from tests.tennis_v1.test_synchronizer import (
    START_WALL_NS,
    book_input,
    book_origin,
    initial_book_input,
    observation,
    origin_input,
    policy,
    provider_origin,
    universe,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def matching_prior(binding: object) -> PrematchPrior:
    return PrematchPrior(
        player_home_id=binding.provider_home_player_id,
        player_away_id=binding.provider_away_player_id,
        surface="hard",
        scheduled_start_wall_ns=binding.scheduled_start_wall_ns,
        home_serve_point_probability=Decimal("0.72"),
        home_serve_point_lower=Decimal("0.70"),
        home_serve_point_upper=Decimal("0.74"),
        away_serve_point_probability=Decimal("0.55"),
        away_serve_point_lower=Decimal("0.53"),
        away_serve_point_upper=Decimal("0.57"),
        home_effective_sample_size=Decimal("80"),
        away_effective_sample_size=Decimal("80"),
        supported=True,
        support_status="supported",
        training_cutoff_wall_ns=binding.scheduled_start_wall_ns - 10,
        model_sha256=SHA_A,
        prematch_artifact_sha256=SHA_B,
        feature_definition_sha256=SHA_C,
        feature_vector_sha256=SHA_D,
        abstention_reason=None,
    )


def deep_cheap_book(ticker: str, *, sequence: int, observed_monotonic_ns: int):
    # YES ask = 1 - 0.60 = 0.40 with 50 contracts of depth.
    return book_origin(
        ticker,
        sequence=sequence,
        observed_monotonic_ns=observed_monotonic_ns,
        yes_bids=(BookLevel(Decimal("0.35"), Decimal("50")),),
        no_bids=(BookLevel(Decimal("0.60"), Decimal("50")),),
    )


def trusted_home_transition():
    value = universe()
    binding = value.bindings[0]
    state = synchronization_session_from_artifacts(
        value,
        policy(value, large_book_move_threshold=Decimal("0.50")),
    )
    state = synchronize(
        state,
        origin_input(provider_origin(binding), binding.canonical_match_id),
        now=observation(110),
    ).state
    # Away book first so the home-book transition is the one that emits
    # TRUSTED_SYNCHRONIZED for the home ticker.
    away = deep_cheap_book(
        binding.away_market_ticker,
        sequence=1,
        observed_monotonic_ns=109,
    )
    state = synchronize(
        state,
        initial_book_input(binding.canonical_match_id, away),
        now=observation(111),
    ).state
    home = deep_cheap_book(
        binding.home_market_ticker,
        sequence=1,
        observed_monotonic_ns=110,
    )
    transition = synchronize(
        state,
        initial_book_input(binding.canonical_match_id, home),
        now=observation(112),
    )
    return transition, binding, matching_prior(binding)


def yes_bid_delta(
    book,
    *,
    price: str,
    quantity: str = "50",
    observed_monotonic_ns: int,
):
    from inci_tennis_expert.contracts import BookDelta, ContractSide

    return BookDelta(
        ticker=book.ticker,
        connection_epoch=book.connection_epoch,
        sequence=book.sequence + 1,
        source_wall_ns=book.book_source_wall_ns + 1,
        observed_monotonic_ns=observed_monotonic_ns,
        clock_uncertainty_ns=1,
        contract_side=ContractSide.YES,
        price=Decimal(price),
        quantity=Decimal(quantity),
    )


class EngineClipWiringTests(unittest.TestCase):
    def test_fair_value_and_entry_from_trusted_opportunity(self) -> None:
        transition, binding, prior = trusted_home_transition()
        trusted = [
            result
            for result in transition.results
            if result.reason is SyncReason.TRUSTED_SYNCHRONIZED
            and result.opportunity is not None
            and result.opportunity.ticker == binding.home_market_ticker
        ]
        self.assertEqual(len(trusted), 1)
        opportunity = trusted[0].opportunity
        assert opportunity is not None
        fair = fair_value_for_opportunity(opportunity, prior)
        self.assertTrue(fair.supported)
        self.assertIs(fair.player_side, PlayerSide.HOME)

        bundle = make_default_clip_bundle(require_calibration=False)
        observation = observe_clip_on_opportunity(
            opportunity,
            prior,
            bundle,
        )
        self.assertIs(observation.action, DecisionAction.PAPER_BUY)
        self.assertIs(
            observation.reason,
            DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE,
        )
        self.assertIsNotNone(observation.position)
        self.assertEqual(observation.ticker, binding.home_market_ticker)


class PaperClipSessionTests(unittest.TestCase):
    def test_session_opens_and_holds_position_across_transitions(self) -> None:
        transition, binding, prior = trusted_home_transition()
        session = PaperClipSession.with_default_bundle(
            require_calibration=False,
        )
        first = session.observe(transition, prior)
        buys = [
            item
            for item in first
            if item.action is DecisionAction.PAPER_BUY
        ]
        self.assertGreaterEqual(len(buys), 1)
        self.assertIn(binding.home_market_ticker, session.open_tickers())

        # Small YES bid bump that does not converge to fair: keep holding.
        state = transition.state
        cursor_book = None
        for cursor in state.book_cursors:
            if cursor.ticker == binding.home_market_ticker:
                cursor_book = cursor.book
                break
        self.assertIsNotNone(cursor_book)
        assert cursor_book is not None
        hold_transition = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor_book,
                yes_bid_delta(
                    cursor_book,
                    price="0.36",
                    observed_monotonic_ns=114,
                ),
            ),
            now=observation(115),
        )
        second = session.observe(hold_transition, prior)
        home_obs = [
            item
            for item in second
            if item.ticker == binding.home_market_ticker
        ]
        self.assertEqual(len(home_obs), 1)
        self.assertIs(home_obs[0].action, DecisionAction.ABSTAIN)
        self.assertIs(
            home_obs[0].reason,
            DecisionReason.SIGNAL_NOT_TRIGGERED,
        )
        self.assertIn(binding.home_market_ticker, session.open_tickers())

    def test_session_exits_when_holding_horizon_elapses(self) -> None:
        transition, binding, prior = trusted_home_transition()
        session = PaperClipSession.with_default_bundle(
            require_calibration=False,
            max_holding_wall_ns=100,
        )
        session.observe(transition, prior)
        self.assertIn(binding.home_market_ticker, session.open_tickers())

        state = transition.state
        cursor_book = None
        for cursor in state.book_cursors:
            if cursor.ticker == binding.home_market_ticker:
                cursor_book = cursor.book
                break
        self.assertIsNotNone(cursor_book)
        assert cursor_book is not None

        # Advance wall time past the sealed holding horizon with a small
        # trusted book bump so the synchronizer emits a fresh opportunity.
        exit_transition = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor_book,
                yes_bid_delta(
                    cursor_book,
                    price="0.36",
                    observed_monotonic_ns=200,
                ),
            ),
            now=observation(
                201,
                wall_ns=START_WALL_NS + 101,
            ),
        )
        observations = session.observe(exit_transition, prior)
        home_obs = [
            item
            for item in observations
            if item.ticker == binding.home_market_ticker
        ]
        self.assertEqual(len(home_obs), 1)
        self.assertIs(home_obs[0].action, DecisionAction.PAPER_SELL)
        self.assertIs(
            home_obs[0].reason,
            DecisionReason.HOLDING_HORIZON_REACHED,
        )
        self.assertNotIn(binding.home_market_ticker, session.open_tickers())

    def test_uncalibrated_default_bundle_abstains(self) -> None:
        transition, binding, prior = trusted_home_transition()
        session = PaperClipSession.with_default_bundle(
            require_calibration=True,
        )
        observations = session.observe(transition, prior)
        home_obs = [
            item
            for item in observations
            if item.ticker == binding.home_market_ticker
        ]
        self.assertEqual(len(home_obs), 1)
        self.assertIs(home_obs[0].action, DecisionAction.ABSTAIN)
        self.assertIs(
            home_obs[0].reason,
            DecisionReason.MODEL_UNSUPPORTED,
        )
        self.assertEqual(session.open_tickers(), ())


class ClipJournalTests(unittest.TestCase):
    def test_session_journals_and_verifies_records(self) -> None:
        transition, binding, prior = trusted_home_transition()
        session = PaperClipSession.with_default_bundle(
            require_calibration=False,
            session_id="clip-journal-test",
        )
        observations = session.observe(transition, prior)
        records = session.journal_records()
        self.assertEqual(len(records), len(observations))
        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0].session_id, "clip-journal-test")
        self.assertEqual(records[0].record_sequence, 1)
        self.assertEqual(records[0].ticker, binding.home_market_ticker)
        bundle_bytes = session.journal_bundle_bytes()
        self.assertTrue(bundle_bytes.endswith(b"\n"))
        self.assertEqual(len(bundle_bytes.strip()), 64)

        from inci_tennis_expert.clip_journal import (
            verify_clip_record_matches_observation,
        )

        for record, observation in zip(records, observations, strict=True):
            verify_clip_record_matches_observation(
                record,
                observation,
                prior=prior,
                bundle=session.bundle,
            )

    def test_journal_detects_mismatched_observation(self) -> None:
        transition, _binding, prior = trusted_home_transition()
        buying = PaperClipSession.with_default_bundle(
            require_calibration=False,
        )
        abstaining = PaperClipSession.with_default_bundle(
            require_calibration=True,
        )
        buy_obs = buying.observe(transition, prior)[0]
        abstain_obs = abstaining.observe(transition, prior)[0]
        record = buying.journal_records()[0]
        from inci_tennis_expert.clip_journal import (
            verify_clip_record_matches_observation,
        )
        from inci_tennis_expert.contracts import ExpertContractError

        self.assertIs(buy_obs.action, DecisionAction.PAPER_BUY)
        self.assertIs(abstain_obs.action, DecisionAction.ABSTAIN)
        with self.assertRaises(ExpertContractError):
            verify_clip_record_matches_observation(
                record,
                abstain_obs,
                prior=prior,
                bundle=buying.bundle,
            )


if __name__ == "__main__":
    unittest.main()
