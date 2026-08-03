from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import importlib
import json
import unittest


TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
MARKET_IDS = (
    "9b0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a1",
    "8a0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a2",
)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _wire(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _parse(value: object) -> object:
    from inci_tennis_adapters.kalshi_v2 import (
        parse_unqualified_book_message,
    )

    return parse_unqualified_book_message(_wire(value))


def _subscribed(*, request_id: int = 1, sid: int = 2) -> object:
    return _parse(
        {
            "id": request_id,
            "type": "subscribed",
            "msg": {"channel": "orderbook_delta", "sid": sid},
        }
    )


def _snapshot(
    ticker: str,
    market_id: str,
    sequence: int,
    *,
    yes: list[list[str]],
    no: list[list[str]],
    sid: int = 2,
) -> object:
    return _parse(
        {
            "type": "orderbook_snapshot",
            "sid": sid,
            "seq": sequence,
            "msg": {
                "market_ticker": ticker,
                "market_id": market_id,
                "yes_dollars_fp": yes,
                "no_dollars_fp": no,
            },
        }
    )


def _delta(
    ticker: str,
    market_id: str,
    sequence: int,
    *,
    side: str = "yes",
    price: str = "0.2500",
    delta: str = "2.00",
    sid: int = 2,
) -> object:
    return _parse(
        {
            "type": "orderbook_delta",
            "sid": sid,
            "seq": sequence,
            "msg": {
                "market_ticker": ticker,
                "market_id": market_id,
                "price_dollars": price,
                "delta_fp": delta,
                "side": side,
            },
        }
    )


def _ready_reducer(*, lexical_variant: bool = False, generation: int = 1):
    from inci_tennis_adapters.kalshi_v2 import (
        UnqualifiedTwoTickerBookReducer,
    )

    reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
    reducer.begin_subscription(generation, 1)
    reducer.apply(_subscribed(), generation)
    reducer.apply(
        _snapshot(
            TICKERS[0],
            MARKET_IDS[0],
            1,
            yes=(
                [["0.2", "4.00"], ["0.3", "5.00"]]
                if lexical_variant
                else [["0.2000", "4.00"], ["0.3000", "5.00"]]
            ),
            no=(
                [["0.7", "6.00"], ["0.8", "7.00"]]
                if lexical_variant
                else [["0.7000", "6.00"], ["0.8000", "7.00"]]
            ),
        ),
        generation,
    )
    reducer.apply(
        _snapshot(
            TICKERS[1],
            MARKET_IDS[1],
            2,
            yes=[["0.4", "8.00"]] if lexical_variant else [["0.4000", "8.00"]],
            no=[["0.6", "9.00"]] if lexical_variant else [["0.6000", "9.00"]],
        ),
        generation,
    )
    return reducer


def _research_api():
    return importlib.import_module("inci_tennis_expert.consensus_l2_research")


def _tennis_state(**changes: object):
    from inci_tennis_expert.contracts import (
        MatchFormat,
        MatchStatus,
        PlayerSide,
        ScoreValue,
        SetScore,
        TennisState,
        TerminationKind,
    )

    values: dict[str, object] = {
        "provider_source_id": "primary",
        "revision_domain_id": "primary-revisions",
        "source_lineage_sha256": SHA_A,
        "provider_match_id": "primary-match",
        "home_player_id": "private-player-home",
        "away_player_id": "private-player-away",
        "scheduled_start_wall_ns": 900,
        "match_format": MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        "status": MatchStatus.LIVE,
        "termination_kind": TerminationKind.NONE,
        "winner": None,
        "retired_side": None,
        "completed_sets": (SetScore(6, 4, None, None),),
        "games_home": 2,
        "games_away": 1,
        "points_home": ScoreValue.FIFTEEN,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": PlayerSide.HOME,
        "correction_epoch": 1,
        "revision": 7,
        "snapshot_complete": True,
        "last_provider_event_id": "primary-event-7",
        "last_event_semantic_sha256": SHA_B,
        "correction_lineage_sha256": SHA_C,
        "last_source_wall_ns": 990,
        "last_source_generated_wall_ns": 980,
        "last_received_monotonic_ns": 100,
        "last_clock_uncertainty_ns": 2,
        "block_reason": None,
        "expected_revision": None,
        "observed_revision": None,
        "blocked_event_semantic_sha256": None,
        "blocked_received_monotonic_ns": None,
    }
    values.update(changes)
    return TennisState(**values)


def _score_support(
    api: object,
    source_id: str,
    *,
    source_lineage_sha256: str,
    independence_lineage_id: str | None,
    raw_sequence: int,
    received_wall_ns: int,
    received_monotonic_ns: int,
    raw_capture_sha256: str,
    durable_record_sha256: str,
    normalized_state_sha256: str | None = None,
):
    if normalized_state_sha256 is None:
        normalized_state_sha256 = (
            api.normalized_score_coordinates_sha256_v1(
                "canonical-match-1",
                _tennis_state(),
            )
        )
    return api.DurableRawScoreSupportRefV1(
        source_id=source_id,
        source_lineage_sha256=source_lineage_sha256,
        independence_lineage_id=independence_lineage_id,
        normalized_state_sha256=normalized_state_sha256,
        raw_capture_sha256=raw_capture_sha256,
        durable_record_sequence=raw_sequence,
        durable_record_sha256=durable_record_sha256,
        received_wall_ns=received_wall_ns,
        received_monotonic_ns=received_monotonic_ns,
    )


def _supporters(api: object, state: object | None = None):
    accepted_state = state or _tennis_state()
    normalized_state_sha256 = (
        api.normalized_score_coordinates_sha256_v1(
            "canonical-match-1",
            accepted_state,
        )
    )
    return (
        _score_support(
            api,
            "primary",
            source_lineage_sha256=SHA_A,
            independence_lineage_id="independent-a",
            raw_sequence=10,
            received_wall_ns=1_000,
            received_monotonic_ns=100,
            raw_capture_sha256=SHA_D,
            durable_record_sha256=SHA_E,
            normalized_state_sha256=normalized_state_sha256,
        ),
        _score_support(
            api,
            "witness",
            source_lineage_sha256=SHA_F,
            independence_lineage_id="independent-b",
            raw_sequence=20,
            received_wall_ns=1_005,
            received_monotonic_ns=105,
            raw_capture_sha256=SHA_E,
            durable_record_sha256=SHA_F,
            normalized_state_sha256=normalized_state_sha256,
        ),
    )


def _transition(api: object, **changes: object):
    state = changes.pop("accepted_state", _tennis_state())
    supporters = changes.pop("supporters", _supporters(api, state))
    authoritative_result = changes.pop(
        "authoritative_result",
        api.ScoreConsensusResult(
            accepted_state=state,
            supporting_source_ids=tuple(
                sorted(supporter.source_id for supporter in supporters)
            ),
            supporting_lineages=tuple(
                sorted(
                    {
                        supporter.independence_lineage_id
                        for supporter in supporters
                        if supporter.independence_lineage_id is not None
                    }
                )
            ),
            reason=api.ConsensusReason.ACCEPTED,
        ),
    )
    values: dict[str, object] = {
        "canonical_match_id": "canonical-match-1",
        "accepted_state": state,
        "authoritative_result": authoritative_result,
        "consensus_epoch": 3,
        "correction_epoch": state.correction_epoch,
        "supporters": supporters,
        "consensus_record_sequence": 30,
        "consensus_record_sha256": SHA_A,
        "consensus_accepted_wall_ns": 1_100,
        "consensus_accepted_monotonic_ns": 120,
        "market_tickers": TICKERS,
        "market_ids": MARKET_IDS,
        "last_book_physical_connection_generation": 1,
        "last_book_subscription_id": 2,
        "last_book_global_sequence": 2,
    }
    values.update(changes)
    return api.AcceptedScoreConsensusTransitionV1(**values)


def _next_transition(api: object, prior: object):
    next_state = replace(
        prior.accepted_state,
        points_home=prior.accepted_state.points_home.__class__.THIRTY,
        revision=8,
        last_provider_event_id="primary-event-8",
        last_event_semantic_sha256=SHA_C,
        last_received_monotonic_ns=130,
    )
    normalized_state_sha256 = (
        api.normalized_score_coordinates_sha256_v1(
            "canonical-match-1",
            next_state,
        )
    )
    supporters = (
        _score_support(
            api,
            "primary",
            source_lineage_sha256=SHA_A,
            independence_lineage_id="independent-a",
            raw_sequence=40,
            received_wall_ns=1_200,
            received_monotonic_ns=130,
            raw_capture_sha256=SHA_B,
            durable_record_sha256=SHA_C,
            normalized_state_sha256=normalized_state_sha256,
        ),
        _score_support(
            api,
            "witness",
            source_lineage_sha256=SHA_F,
            independence_lineage_id="independent-b",
            raw_sequence=50,
            received_wall_ns=1_205,
            received_monotonic_ns=135,
            raw_capture_sha256=SHA_C,
            durable_record_sha256=SHA_D,
            normalized_state_sha256=normalized_state_sha256,
        ),
    )
    return _transition(
        api,
        accepted_state=next_state,
        supporters=supporters,
        consensus_record_sequence=60,
        consensus_record_sha256=SHA_B,
        consensus_accepted_wall_ns=1_300,
        consensus_accepted_monotonic_ns=150,
        last_book_global_sequence=3,
    )


def _export_at(sequence: int = 3, *, generation: int = 1):
    reducer = _ready_reducer(generation=generation)
    if sequence >= 3:
        reducer.apply(
            _delta(TICKERS[0], MARKET_IDS[0], 3),
            generation,
        )
    if sequence >= 4:
        reducer.apply(
            _delta(
                TICKERS[1],
                MARKET_IDS[1],
                4,
                side="no",
                price="0.5800",
                delta="1.00",
            ),
            generation,
        )
    exported = reducer.full_l2
    if exported is None or exported.global_sequence != sequence:
        raise AssertionError("test export construction failed")
    return exported


def _book_observation(
    api: object,
    *,
    sequence: int = 3,
    generation: int = 1,
    canonical_match_id: str = "canonical-match-1",
    captured_wall_ns: int = 1_101,
    captured_monotonic_ns: int = 120,
    durable_record_sequence: int = 31,
):
    exported = _export_at(sequence, generation=generation)
    markets = tuple(
        api.UnqualifiedL2MarketV1(
            ticker=market.ticker,
            market_id=market.market_id,
            yes_levels=tuple(
                api.ResearchL2LevelV1(price, quantity)
                for price, quantity in market.yes_levels
            ),
            no_levels=tuple(
                api.ResearchL2LevelV1(price, quantity)
                for price, quantity in market.no_levels
            ),
        )
        for market in exported.markets
    )
    parent = api.DurableRawBookParentRefV1(
        raw_frame_sha256=SHA_D,
        durable_record_sequence=durable_record_sequence,
        durable_record_sha256=SHA_E,
        received_wall_ns=captured_wall_ns,
        received_monotonic_ns=captured_monotonic_ns,
    )
    return api.UnqualifiedTwoMarketL2ObservationV1(
        canonical_match_id=canonical_match_id,
        markets=markets,
        physical_connection_generation=(
            exported.physical_connection_generation
        ),
        subscription_id=exported.subscription_id,
        global_sequence=exported.global_sequence,
        l2_state_sha256=exported.state_sha256,
        raw_parent=parent,
        captured_wall_ns=captured_wall_ns,
        captured_monotonic_ns=captured_monotonic_ns,
    )


def _armed(api: object, transition: object | None = None):
    state = api.initial_consensus_l2_barrier_v1(
        "canonical-match-1",
        TICKERS,
        MARKET_IDS,
    )
    accepted = transition or _transition(api)
    result = api.open_consensus_l2_barrier_v1(state, accepted)
    return result.state, accepted


class UnqualifiedFullL2ExportTests(unittest.TestCase):
    def test_exact_full_l2_export_digest_and_delta_update(self) -> None:
        """Catches losing depth/IDs or hashing lexical Decimal spellings."""

        reducer = _ready_reducer()
        exported = reducer.full_l2

        self.assertIsNotNone(exported)
        assert exported is not None
        self.assertEqual(
            (
                exported.physical_connection_generation,
                exported.subscription_id,
                exported.global_sequence,
            ),
            (1, 2, 2),
        )
        self.assertEqual(
            tuple((market.ticker, market.market_id) for market in exported.markets),
            tuple(zip(TICKERS, MARKET_IDS, strict=True)),
        )
        self.assertEqual(
            exported.markets[0].yes_levels,
            (
                (Decimal("0.2000"), Decimal("4.00")),
                (Decimal("0.3000"), Decimal("5.00")),
            ),
        )
        self.assertEqual(
            exported.markets[0].no_levels,
            (
                (Decimal("0.7000"), Decimal("6.00")),
                (Decimal("0.8000"), Decimal("7.00")),
            ),
        )
        self.assertEqual(
            exported.state_sha256,
            "3b43ca84e092a4cba2b62c74e7844fcb8814179e3f4589cf5ba2c01a9ea776b8",
        )
        self.assertEqual(
            _ready_reducer(lexical_variant=True).full_l2.state_sha256,
            exported.state_sha256,
        )

        reducer.apply(
            _delta(TICKERS[0], MARKET_IDS[0], 3),
            1,
        )
        updated = reducer.full_l2
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.global_sequence, 3)
        self.assertEqual(
            updated.markets[0].yes_levels,
            (
                (Decimal("0.2000"), Decimal("4.00")),
                (Decimal("0.2500"), Decimal("2.00")),
                (Decimal("0.3000"), Decimal("5.00")),
            ),
        )
        self.assertNotEqual(updated.state_sha256, exported.state_sha256)

    def test_export_is_absent_in_every_unready_or_invalid_reducer_state(self) -> None:
        """Catches exporting partial, empty, discontinuous, or dead books."""

        from inci_tennis_adapters.kalshi_v2 import (
            UnqualifiedTwoTickerBookReducer,
        )

        reducer = UnqualifiedTwoTickerBookReducer(TICKERS)
        self.assertIsNone(reducer.full_l2)
        reducer.begin_subscription(1, 1)
        self.assertIsNone(reducer.full_l2)
        reducer.apply(_subscribed(), 1)
        self.assertIsNone(reducer.full_l2)
        reducer.apply(
            _snapshot(
                TICKERS[0],
                MARKET_IDS[0],
                1,
                yes=[["0.3000", "5.00"]],
                no=[["0.7000", "6.00"]],
            ),
            1,
        )
        self.assertIsNone(reducer.full_l2)
        reducer.apply(
            _snapshot(
                TICKERS[1],
                MARKET_IDS[1],
                2,
                yes=[],
                no=[["0.6000", "9.00"]],
            ),
            1,
        )
        self.assertEqual(reducer.state.status, "empty_book")
        self.assertIsNone(reducer.full_l2)

        for incoming, reason in (
            (4, "sequence_gap"),
            (2, "sequence_duplicate"),
            (1, "sequence_out_of_order"),
        ):
            with self.subTest(reason=reason):
                invalid = _ready_reducer()
                invalid.apply(
                    _delta(
                        TICKERS[0],
                        MARKET_IDS[0],
                        incoming,
                    ),
                    1,
                )
                self.assertEqual(invalid.state.reason, reason)
                self.assertIsNone(invalid.full_l2)
                invalid.expect_snapshot(1, 2, 9)
                self.assertIsNone(invalid.full_l2)

        disconnected = _ready_reducer()
        disconnected.disconnect(1)
        self.assertIsNone(disconnected.full_l2)

        terminal = _ready_reducer()
        terminal.apply(
            _parse(
                {
                    "id": 8,
                    "type": "error",
                    "msg": {"code": 25, "msg": "sanitized"},
                }
            ),
            1,
        )
        self.assertEqual(terminal.state.status, "terminal")
        self.assertIsNone(terminal.full_l2)

    def test_export_is_immutable_unqualified_and_never_aliases_reducer_state(
        self,
    ) -> None:
        """Catches mutable ladder aliases or accidental execution authority."""

        reducer = _ready_reducer()
        first = reducer.full_l2
        second = reducer.full_l2
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertIsNot(first, second)
        self.assertIsNot(first.markets[0], second.markets[0])
        self.assertTrue(first.research_only)
        self.assertFalse(first.execution_authorized)
        self.assertEqual(first.qualification, "unqualified_shadow")
        self.assertNotIn(TICKERS[0], repr(first))
        self.assertNotIn(MARKET_IDS[0], repr(first))
        with self.assertRaises(FrozenInstanceError):
            first.global_sequence = 99
        with self.assertRaises(FrozenInstanceError):
            first.markets[0].yes_levels = ()
        with self.assertRaisesRegex(KeyError, "kalshi_candidate_ticker_unknown"):
            first.market("SECRET-PLAYER-NAME")

        reducer.apply(
            _delta(TICKERS[0], MARKET_IDS[0], 3),
            1,
        )
        self.assertEqual(first.global_sequence, 2)
        self.assertEqual(len(first.markets[0].yes_levels), 2)


class ConsensusL2ResearchBarrierTests(unittest.TestCase):
    def test_first_post_consensus_book_pairs_once_and_binds_all_parents(self) -> None:
        """Catches stale pairing, parent omission, or later-book replacement."""

        api = _research_api()
        armed, transition = _armed(api)
        observation = _book_observation(api)

        paired = api.observe_consensus_l2_book_v1(armed, observation)

        self.assertIs(paired.disposition, api.ConsensusL2DispositionV1.PAIRED)
        self.assertIsNone(paired.state.pending_transition)
        self.assertIsNotNone(paired.frame)
        self.assertIsNone(paired.coverage)
        frame = paired.frame
        assert frame is not None
        self.assertIs(frame.consensus_transition, transition)
        self.assertIs(frame.l2_observation, observation)
        self.assertRegex(frame.frame_id, r"^[0-9a-f]{64}$")
        self.assertTrue(frame.research_only)
        self.assertFalse(frame.execution_authorized)
        self.assertEqual(frame.qualification, "unqualified_shadow")
        self.assertEqual(
            tuple(market.ticker for market in frame.l2_observation.markets),
            TICKERS,
        )
        self.assertEqual(
            tuple(market.market_id for market in frame.l2_observation.markets),
            MARKET_IDS,
        )
        self.assertEqual(frame.l2_observation.subscription_id, 2)
        self.assertEqual(frame.l2_observation.global_sequence, 3)

        later = api.observe_consensus_l2_book_v1(
            paired.state,
            _book_observation(
                api,
                sequence=4,
                captured_wall_ns=1_102,
                captured_monotonic_ns=121,
                durable_record_sequence=32,
            ),
        )
        self.assertIs(later.disposition, api.ConsensusL2DispositionV1.IGNORED)
        self.assertIsNone(later.frame)
        self.assertIsNone(later.coverage)

    def test_pre_barrier_book_is_ignored_then_first_eligible_book_pairs(self) -> None:
        """Catches pairing a book visible before consensus durability."""

        api = _research_api()
        armed, _ = _armed(api)
        delayed_old_generation = _book_observation(
            api,
            generation=2,
            captured_wall_ns=1_099,
            captured_monotonic_ns=119,
            durable_record_sequence=29,
        )
        pre_barrier = _book_observation(
            api,
            sequence=2,
            captured_wall_ns=1_099,
            captured_monotonic_ns=119,
            durable_record_sequence=29,
        )

        delayed = api.observe_consensus_l2_book_v1(
            armed,
            delayed_old_generation,
        )
        self.assertIs(
            delayed.disposition,
            api.ConsensusL2DispositionV1.IGNORED,
        )
        ignored = api.observe_consensus_l2_book_v1(
            delayed.state,
            pre_barrier,
        )
        self.assertIs(ignored.disposition, api.ConsensusL2DispositionV1.IGNORED)
        self.assertIs(ignored.state.pending_transition, armed.pending_transition)
        paired = api.observe_consensus_l2_book_v1(
            ignored.state,
            _book_observation(api),
        )
        self.assertIs(paired.disposition, api.ConsensusL2DispositionV1.PAIRED)

    def test_otherwise_eligible_book_cannot_regress_durable_barrier(self) -> None:
        """Catches treating an out-of-order cross-stream callback as permissible."""

        api = _research_api()
        armed, _ = _armed(api)
        durable_regression = _book_observation(
            api,
            sequence=3,
            captured_wall_ns=1_100,
            captured_monotonic_ns=120,
            durable_record_sequence=29,
        )

        with self.assertRaises(api.ConsensusL2ResearchError):
            api.observe_consensus_l2_book_v1(armed, durable_regression)

    def test_arming_requires_complete_live_bo3_and_primary_plus_witness(self) -> None:
        """Catches creating a second permissive consensus authority."""

        api = _research_api()
        from inci_tennis_expert.contracts import (
            MatchFormat,
            MatchStatus,
            TennisTransitionReason,
        )

        cases = (
            _tennis_state(
                match_format=(
                    MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS
                )
            ),
            _tennis_state(status=MatchStatus.SUSPENDED),
            _tennis_state(server_for_next_point=None),
            _tennis_state(
                block_reason=TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
                blocked_event_semantic_sha256=SHA_F,
                blocked_received_monotonic_ns=100,
            ),
        )
        for state in cases:
            with self.subTest(state_status=state.status):
                with self.assertRaisesRegex(
                    api.ConsensusL2ResearchError,
                    "consensus_l2_research_invalid",
                ):
                    _transition(api, accepted_state=state)

        missing_primary = tuple(
            ref for ref in _supporters(api) if ref.source_id != "primary"
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            _transition(api, supporters=missing_primary)

    def test_two_proven_independence_lineages_are_mandatory(self) -> None:
        """Catches vendor-name diversity, mirrors, or unknown lineage as proof."""

        api = _research_api()
        primary, witness = _supporters(api)
        mirror = replace(witness, independence_lineage_id="independent-a")
        unknown = replace(witness, independence_lineage_id=None)
        for label, supporters in (
            ("mirror", (primary, mirror)),
            ("unknown", (primary, unknown)),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    api.ConsensusL2ResearchError,
                    "consensus_l2_research_invalid",
                ):
                    _transition(api, supporters=supporters)

    def test_authoritative_result_and_normalized_support_are_exact(self) -> None:
        """Catches attaching unrelated durable records to an accepted result."""

        api = _research_api()
        transition = _transition(api)
        self.assertIs(
            transition.authoritative_result.reason,
            api.ConsensusReason.ACCEPTED,
        )
        self.assertEqual(
            transition.authoritative_result.supporting_source_ids,
            tuple(ref.source_id for ref in transition.supporters),
        )
        wrong_result = api.ScoreConsensusResult(
            accepted_state=transition.accepted_state,
            supporting_source_ids=("primary", "wrong-witness"),
            supporting_lineages=("independent-a", "independent-b"),
            reason=api.ConsensusReason.ACCEPTED,
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            _transition(api, authoritative_result=wrong_result)

        primary, witness = _supporters(api)
        with self.assertRaises(api.ConsensusL2ResearchError):
            _transition(
                api,
                supporters=(
                    primary,
                    replace(witness, normalized_state_sha256=SHA_A),
                ),
            )

    def test_epoch_or_correction_change_requires_explicit_prior_censor(self) -> None:
        """Catches epoch invalidation being mislabeled as ordinary score advance."""

        api = _research_api()
        armed, first = _armed(api)
        ordinary_next = _next_transition(api, first)
        epoch_changed = replace(ordinary_next, consensus_epoch=4)
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.open_consensus_l2_barrier_v1(armed, epoch_changed)
        epoch_censored = api.censor_consensus_l2_barrier_v1(
            armed,
            api.ConsensusL2CensorReasonV1.CONSENSUS_EPOCH_CHANGED,
            event_sha256=epoch_changed.accepted_score_sha256,
            durable_record_sequence=55,
            observed_wall_ns=epoch_changed.consensus_accepted_wall_ns,
            observed_monotonic_ns=(
                epoch_changed.consensus_accepted_monotonic_ns
            ),
        )
        epoch_rearmed = api.open_consensus_l2_barrier_v1(
            epoch_censored.state,
            epoch_changed,
        )
        self.assertIs(
            epoch_rearmed.disposition,
            api.ConsensusL2DispositionV1.ARMED,
        )

        correction_armed, correction_first = _armed(api)
        correction_next = _next_transition(api, correction_first)
        corrected_state = replace(
            correction_next.accepted_state,
            correction_epoch=2,
            revision=1,
            correction_lineage_sha256=SHA_D,
        )
        corrected = _transition(
            api,
            accepted_state=corrected_state,
            supporters=correction_next.supporters,
            consensus_epoch=4,
            consensus_record_sequence=(
                correction_next.consensus_record_sequence
            ),
            consensus_record_sha256=SHA_C,
            consensus_accepted_wall_ns=(
                correction_next.consensus_accepted_wall_ns
            ),
            consensus_accepted_monotonic_ns=(
                correction_next.consensus_accepted_monotonic_ns
            ),
            last_book_global_sequence=3,
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.open_consensus_l2_barrier_v1(
                correction_armed,
                corrected,
            )
        correction_censored = api.censor_consensus_l2_barrier_v1(
            correction_armed,
            api.ConsensusL2CensorReasonV1.SCORE_CORRECTED,
            event_sha256=corrected.accepted_score_sha256,
            durable_record_sequence=55,
            observed_wall_ns=corrected.consensus_accepted_wall_ns,
            observed_monotonic_ns=corrected.consensus_accepted_monotonic_ns,
        )
        correction_rearmed = api.open_consensus_l2_barrier_v1(
            correction_censored.state,
            corrected,
        )
        self.assertIs(
            correction_rearmed.disposition,
            api.ConsensusL2DispositionV1.ARMED,
        )

    def test_every_supporter_must_be_durable_before_acceptance(self) -> None:
        """Catches accepting a raw supporter written after its consensus record."""

        api = _research_api()
        primary, witness = _supporters(api)
        cases = (
            (replace(witness, durable_record_sequence=30),),
            (replace(witness, received_monotonic_ns=121),),
            (replace(witness, received_wall_ns=1_101),),
        )
        for (bad_witness,) in cases:
            with self.subTest(ref=bad_witness.durable_record_sequence):
                with self.assertRaises(api.ConsensusL2ResearchError):
                    _transition(api, supporters=(primary, bad_witness))

    def test_new_score_censors_prior_then_arms_only_newer_transition(self) -> None:
        """Catches silently replacing a pending score barrier."""

        api = _research_api()
        armed, first = _armed(api)
        second = _next_transition(api, first)

        advanced = api.open_consensus_l2_barrier_v1(armed, second)

        self.assertIs(
            advanced.disposition,
            api.ConsensusL2DispositionV1.ADVANCED,
        )
        self.assertIsNotNone(advanced.coverage)
        assert advanced.coverage is not None
        self.assertIs(
            advanced.coverage.reason,
            api.ConsensusL2CensorReasonV1.SCORE_ADVANCED,
        )
        self.assertEqual(
            advanced.coverage.accepted_score_sha256,
            first.accepted_score_sha256,
        )
        self.assertIs(advanced.state.pending_transition, second)

    def test_score_correction_epoch_and_quarantine_censor_permanently(self) -> None:
        """Catches allowing a corrected or disputed score barrier to revive."""

        api = _research_api()
        reasons = (
            api.ConsensusL2CensorReasonV1.SCORE_CORRECTED,
            api.ConsensusL2CensorReasonV1.CONSENSUS_EPOCH_CHANGED,
            api.ConsensusL2CensorReasonV1.CONSENSUS_QUARANTINED,
            api.ConsensusL2CensorReasonV1.CONSENSUS_DISAGREEMENT,
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                armed, transition = _armed(api)
                censored = api.censor_consensus_l2_barrier_v1(
                    armed,
                    reason,
                    event_sha256=SHA_F,
                    durable_record_sequence=31,
                    observed_wall_ns=1_101,
                    observed_monotonic_ns=121,
                )
                self.assertIs(
                    censored.disposition,
                    api.ConsensusL2DispositionV1.CENSORED,
                )
                self.assertEqual(censored.coverage.reason, reason)
                self.assertEqual(
                    censored.coverage.accepted_score_sha256,
                    transition.accepted_score_sha256,
                )
                ignored = api.observe_consensus_l2_book_v1(
                    censored.state,
                    _book_observation(
                        api,
                        captured_wall_ns=1_102,
                        captured_monotonic_ns=122,
                        durable_record_sequence=32,
                    ),
                )
                self.assertIs(
                    ignored.disposition,
                    api.ConsensusL2DispositionV1.IGNORED,
                )
                self.assertIsNone(ignored.frame)

    def test_book_lifecycle_and_session_failures_have_stable_censor_reasons(
        self,
    ) -> None:
        """Catches dropping non-pairable barriers from coverage accounting."""

        api = _research_api()
        reasons = (
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_DUPLICATE,
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_OUT_OF_ORDER,
            api.ConsensusL2CensorReasonV1.BOOK_GENERATION_CHANGED,
            api.ConsensusL2CensorReasonV1.BOOK_RECONNECTED,
            api.ConsensusL2CensorReasonV1.LIFECYCLE_SUSPENDED,
            api.ConsensusL2CensorReasonV1.LIFECYCLE_CLOSED,
            api.ConsensusL2CensorReasonV1.SESSION_ENDED,
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                armed, _ = _armed(api)
                result = api.censor_consensus_l2_barrier_v1(
                    armed,
                    reason,
                    event_sha256=SHA_F,
                    durable_record_sequence=31,
                    observed_wall_ns=1_101,
                    observed_monotonic_ns=121,
                )
                self.assertEqual(result.coverage.reason, reason)
                self.assertRegex(result.coverage.coverage_id, r"^[0-9a-f]{64}$")

        armed, _ = _armed(api)
        changed = api.observe_consensus_l2_book_v1(
            armed,
            _book_observation(
                api,
                generation=2,
                captured_monotonic_ns=121,
            ),
        )
        self.assertIs(
            changed.coverage.reason,
            api.ConsensusL2CensorReasonV1.BOOK_GENERATION_CHANGED,
        )

    def test_replay_duplicate_conflict_regression_and_watermarks(self) -> None:
        """Catches replay changing state or regressing durable evidence."""

        api = _research_api()
        armed, first = _armed(api)
        duplicate = api.open_consensus_l2_barrier_v1(armed, first)
        self.assertIs(
            duplicate.disposition,
            api.ConsensusL2DispositionV1.IGNORED,
        )
        self.assertIs(duplicate.state, armed)

        conflict = _transition(api, consensus_record_sha256=SHA_B)
        regression = _transition(
            api,
            accepted_state=replace(
                first.accepted_state,
                revision=6,
                last_provider_event_id="primary-event-6",
                last_event_semantic_sha256=SHA_D,
            ),
        )
        higher_state = replace(
            first.accepted_state,
            points_home=first.accepted_state.points_home.__class__.THIRTY,
            revision=8,
            last_provider_event_id="primary-event-8",
            last_event_semantic_sha256=SHA_C,
        )
        nonmonotonic_records = _transition(
            api,
            accepted_state=higher_state,
            consensus_record_sequence=31,
            consensus_record_sha256=SHA_B,
            consensus_accepted_wall_ns=1_101,
            consensus_accepted_monotonic_ns=121,
        )
        primary, witness = _supporters(api, higher_state)
        globally_stale_supporters = _transition(
            api,
            accepted_state=higher_state,
            supporters=(
                replace(primary, durable_record_sequence=25),
                replace(witness, durable_record_sequence=26),
            ),
            consensus_record_sequence=31,
            consensus_record_sha256=SHA_C,
            consensus_accepted_wall_ns=1_101,
            consensus_accepted_monotonic_ns=121,
        )
        regressed_clock_state = replace(
            higher_state,
            last_received_monotonic_ns=90,
        )
        regressed_normalized_state_sha256 = (
            api.normalized_score_coordinates_sha256_v1(
                "canonical-match-1",
                regressed_clock_state,
            )
        )
        regressed_clock_supporters = (
            _score_support(
                api,
                "primary",
                source_lineage_sha256=SHA_A,
                independence_lineage_id="independent-a",
                raw_sequence=40,
                received_wall_ns=900,
                received_monotonic_ns=90,
                raw_capture_sha256=SHA_B,
                durable_record_sha256=SHA_C,
                normalized_state_sha256=(
                    regressed_normalized_state_sha256
                ),
            ),
            _score_support(
                api,
                "witness",
                source_lineage_sha256=SHA_F,
                independence_lineage_id="independent-b",
                raw_sequence=50,
                received_wall_ns=905,
                received_monotonic_ns=95,
                raw_capture_sha256=SHA_C,
                durable_record_sha256=SHA_D,
                normalized_state_sha256=(
                    regressed_normalized_state_sha256
                ),
            ),
        )
        nonmonotonic_clocks = _transition(
            api,
            accepted_state=regressed_clock_state,
            supporters=regressed_clock_supporters,
            consensus_record_sequence=60,
            consensus_record_sha256=SHA_C,
            consensus_accepted_wall_ns=1_050,
            consensus_accepted_monotonic_ns=110,
        )
        for label, candidate in (
            ("conflict", conflict),
            ("regression", regression),
            ("durable_sequence", nonmonotonic_records),
            ("global_durable_sequence", globally_stale_supporters),
            ("clock", nonmonotonic_clocks),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    api.ConsensusL2ResearchError,
                    "consensus_l2_research_invalid",
                ):
                    api.open_consensus_l2_barrier_v1(armed, candidate)

        paired = api.observe_consensus_l2_book_v1(
            armed,
            _book_observation(api, durable_record_sequence=70),
        )
        self.assertEqual(paired.state.last_durable_record_sequence, 70)
        self.assertIs(
            paired.state.last_consumed_event_kind,
            api.ConsensusL2DurableEventKindV1.L2_BOOK,
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.open_consensus_l2_barrier_v1(
                paired.state,
                _next_transition(api, first),
            )

        later_book = _book_observation(
            api,
            sequence=4,
            captured_wall_ns=1_302,
            captured_monotonic_ns=151,
            durable_record_sequence=80,
        )
        ignored_book = api.observe_consensus_l2_book_v1(
            paired.state,
            later_book,
        )
        self.assertIs(
            ignored_book.disposition,
            api.ConsensusL2DispositionV1.IGNORED,
        )
        self.assertEqual(
            ignored_book.state.last_durable_record_sequence,
            80,
        )
        exact_book_replay = api.observe_consensus_l2_book_v1(
            ignored_book.state,
            later_book,
        )
        self.assertIs(exact_book_replay.state, ignored_book.state)
        conflicting_book = _book_observation(
            api,
            sequence=4,
            captured_wall_ns=1_303,
            captured_monotonic_ns=152,
            durable_record_sequence=80,
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.observe_consensus_l2_book_v1(
                ignored_book.state,
                conflicting_book,
            )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.censor_consensus_l2_barrier_v1(
                ignored_book.state,
                api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
                event_sha256=SHA_F,
                durable_record_sequence=80,
                observed_wall_ns=1_302,
                observed_monotonic_ns=151,
            )

        censored = api.censor_consensus_l2_barrier_v1(
            armed,
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
            event_sha256=SHA_F,
            durable_record_sequence=70,
            observed_wall_ns=1_301,
            observed_monotonic_ns=151,
        )
        self.assertEqual(censored.state.last_durable_record_sequence, 70)
        self.assertIs(
            censored.state.last_consumed_event_kind,
            api.ConsensusL2DurableEventKindV1.CENSOR,
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.open_consensus_l2_barrier_v1(
                censored.state,
                _next_transition(api, first),
            )

        ignored_censor = api.censor_consensus_l2_barrier_v1(
            paired.state,
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
            event_sha256=SHA_F,
            durable_record_sequence=80,
            observed_wall_ns=1_302,
            observed_monotonic_ns=151,
        )
        self.assertEqual(
            ignored_censor.state.last_durable_record_sequence,
            80,
        )
        exact_censor_replay = api.censor_consensus_l2_barrier_v1(
            ignored_censor.state,
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
            event_sha256=SHA_F,
            durable_record_sequence=80,
            observed_wall_ns=1_302,
            observed_monotonic_ns=151,
        )
        self.assertIs(exact_censor_replay.state, ignored_censor.state)
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.censor_consensus_l2_barrier_v1(
                ignored_censor.state,
                api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
                event_sha256=SHA_E,
                durable_record_sequence=80,
                observed_wall_ns=1_302,
                observed_monotonic_ns=151,
            )

    def test_reconstructed_equal_pending_and_last_transition_replay(self) -> None:
        """Catches relying on in-process object identity after durable decode."""

        api = _research_api()
        pending = _transition(api)
        independently_decoded_last = _transition(api)
        self.assertIsNot(pending, independently_decoded_last)
        self.assertEqual(pending, independently_decoded_last)

        reconstructed = api.ConsensusL2BarrierStateV1(
            canonical_match_id="canonical-match-1",
            market_tickers=TICKERS,
            market_ids=MARKET_IDS,
            pending_transition=pending,
            last_transition=independently_decoded_last,
            supporter_watermarks=pending.supporters,
            last_durable_record_sequence=(
                pending.consensus_record_sequence
            ),
            last_consumed_event_kind=(
                api.ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS
            ),
            last_consumed_event_sha256=pending.accepted_score_sha256,
            session_ended=False,
        )
        paired = api.observe_consensus_l2_book_v1(
            reconstructed,
            _book_observation(api),
        )
        self.assertIs(
            paired.disposition,
            api.ConsensusL2DispositionV1.PAIRED,
        )

    def test_update_contract_rejects_impossible_disposition_payloads(self) -> None:
        """Catches forging paired/censored updates without their required record."""

        api = _research_api()
        armed, _ = _armed(api)
        paired = api.observe_consensus_l2_book_v1(
            armed,
            _book_observation(api),
        )
        censored = api.censor_consensus_l2_barrier_v1(
            armed,
            api.ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
            event_sha256=SHA_F,
            durable_record_sequence=31,
            observed_wall_ns=1_101,
            observed_monotonic_ns=121,
        )
        advanced = api.open_consensus_l2_barrier_v1(
            armed,
            _next_transition(api, armed.last_transition),
        )
        initial = api.initial_consensus_l2_barrier_v1(
            "canonical-match-1",
            TICKERS,
            MARKET_IDS,
        )
        cases = (
            (
                armed,
                api.ConsensusL2DispositionV1.PAIRED,
                None,
                None,
            ),
            (
                armed,
                api.ConsensusL2DispositionV1.CENSORED,
                None,
                None,
            ),
            (
                armed,
                api.ConsensusL2DispositionV1.ARMED,
                paired.frame,
                None,
            ),
            (
                paired.state,
                api.ConsensusL2DispositionV1.ADVANCED,
                None,
                None,
            ),
            (
                initial,
                api.ConsensusL2DispositionV1.PAIRED,
                paired.frame,
                None,
            ),
            (
                initial,
                api.ConsensusL2DispositionV1.CENSORED,
                None,
                censored.coverage,
            ),
            (
                advanced.state,
                api.ConsensusL2DispositionV1.ADVANCED,
                None,
                censored.coverage,
            ),
        )
        for state, disposition, frame, coverage in cases:
            with self.subTest(disposition=disposition):
                with self.assertRaises(api.ConsensusL2ResearchError):
                    api.ConsensusL2BarrierUpdateV1(
                        state=state,
                        disposition=disposition,
                        frame=frame,
                        coverage=coverage,
                    )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.ConsensusL2BarrierUpdateV1(
                state=replace(armed, last_durable_record_sequence=31),
                disposition=api.ConsensusL2DispositionV1.ARMED,
                frame=None,
                coverage=None,
            )

    def test_permissible_callback_schedules_produce_identical_frame_bytes(self) -> None:
        """Catches callback arrival order leaking into deterministic corpus bytes."""

        api = _research_api()
        transition = _transition(api)
        pre = _book_observation(
            api,
            sequence=2,
            captured_wall_ns=1_099,
            captured_monotonic_ns=119,
            durable_record_sequence=29,
        )
        eligible = _book_observation(api)

        initial_a, _ = _armed(api, transition)
        a1 = api.observe_consensus_l2_book_v1(initial_a, pre)
        a2 = api.open_consensus_l2_barrier_v1(a1.state, transition)
        frame_a = api.observe_consensus_l2_book_v1(a2.state, eligible).frame

        initial_b, _ = _armed(api, transition)
        b1 = api.open_consensus_l2_barrier_v1(initial_b, transition)
        b2 = api.observe_consensus_l2_book_v1(b1.state, pre)
        frame_b = api.observe_consensus_l2_book_v1(b2.state, eligible).frame

        self.assertIsNotNone(frame_a)
        self.assertIsNotNone(frame_b)
        self.assertEqual(frame_a, frame_b)
        self.assertEqual(
            api.canonical_consensus_l2_research_bytes_v1(frame_a),
            api.canonical_consensus_l2_research_bytes_v1(frame_b),
        )

    def test_malformed_l2_values_duplicate_prices_crossing_and_capacity_reject(
        self,
    ) -> None:
        """Catches ambiguous or resource-unbounded full-depth observations."""

        api = _research_api()
        level = api.ResearchL2LevelV1
        with self.assertRaises(api.ConsensusL2ResearchError):
            level(Decimal("NaN"), Decimal("1"))
        with self.assertRaises(api.ConsensusL2ResearchError):
            level(Decimal("0.5"), Decimal("Infinity"))
        with self.assertRaises(api.ConsensusL2ResearchError):
            level(Decimal("0.5"), Decimal("0"))

        duplicate = (
            level(Decimal("0.2"), Decimal("1")),
            level(Decimal("0.2"), Decimal("2")),
        )
        crossed_yes = (level(Decimal("0.8"), Decimal("1")),)
        crossed_no = (level(Decimal("0.7"), Decimal("1")),)
        too_many = tuple(
            level(Decimal(index) / Decimal("10000"), Decimal("1"))
            for index in range(1_025)
        )
        cases = (
            (duplicate, (level(Decimal("0.7"), Decimal("1")),)),
            (crossed_yes, crossed_no),
            (too_many, (level(Decimal("0.9"), Decimal("1")),)),
        )
        for yes_levels, no_levels in cases:
            with self.assertRaises(api.ConsensusL2ResearchError):
                api.UnqualifiedL2MarketV1(
                    ticker=TICKERS[0],
                    market_id=MARKET_IDS[0],
                    yes_levels=yes_levels,
                    no_levels=no_levels,
                )

    def test_identity_digest_and_clock_mismatches_fail_with_safe_messages(self) -> None:
        """Catches universe drift/tampering and secret-bearing reprs or errors."""

        api = _research_api()
        transition = _transition(api)
        armed, _ = _armed(api, transition)
        wrong_universe = _transition(
            api,
            market_tickers=(TICKERS[1], TICKERS[0]),
            market_ids=(MARKET_IDS[1], MARKET_IDS[0]),
        )
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.open_consensus_l2_barrier_v1(armed, wrong_universe)

        observation = _book_observation(api)
        with self.assertRaises(api.ConsensusL2ResearchError):
            replace(observation, l2_state_sha256=SHA_A)
        with self.assertRaises(api.ConsensusL2ResearchError):
            api.observe_consensus_l2_book_v1(
                armed,
                _book_observation(api, canonical_match_id="wrong-match"),
            )

        secret = "credential-player-secret"
        with self.assertRaises(api.ConsensusL2ResearchError) as caught:
            replace(_supporters(api)[0], raw_capture_sha256=secret)
        self.assertEqual(str(caught.exception), "consensus_l2_research_invalid")
        self.assertNotIn(secret, repr(caught.exception))
        for value in (transition, observation, observation.markets[0]):
            rendered = repr(value)
            self.assertNotIn("private-player-home", rendered)
            self.assertNotIn(TICKERS[0], rendered)
            self.assertNotIn(MARKET_IDS[0], rendered)
        with self.assertRaises(FrozenInstanceError):
            observation.global_sequence = 99


if __name__ == "__main__":
    unittest.main()
