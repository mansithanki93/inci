from __future__ import annotations

import copy
from dataclasses import fields, replace
from decimal import (
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_DOWN,
    localcontext,
)
import hashlib
import unittest

import inci_tennis_expert.synchronizer as synchronizer_module
from inci_tennis_expert.contracts import (
    BindingUniverse,
    BookDelta,
    BookSnapshot,
    BookState,
    BookTransitionResult,
    ContractSide,
    ExpertContractError,
    MarketLifecycle,
    MarketStatus,
    MatchStatus,
    OpportunityFrame,
    PairedTimeObservation,
    PlayerSide,
    ProviderLifecycle,
    ProviderLifecycleKind,
    ProviderPoint,
    ProviderSnapshot,
    ScoreValue,
    SyncInputKind,
    SyncPolicy,
    SyncReason,
    SyncResult,
    SynchronizationInput,
    SynchronizationSessionState,
    TrustedSnapshot,
    TennisState,
    TennisTransitionReason,
    TerminationKind,
    TransitionDisposition,
    canonical_expert_bytes,
    expert_contract_sha256,
)
from inci_tennis_expert.market_book import (
    apply_book_delta,
    apply_book_snapshot,
    apply_market_lifecycle,
    book_from_snapshot,
    require_book_resnapshot,
)
from inci_tennis_expert.synchronizer import (
    SynchronizationSessionDriftError,
    assert_synchronization_session_compatible,
    synchronization_session_from_artifacts,
    synchronize,
)
from inci_tennis_expert.tennis_score import (
    apply_correction,
    apply_lifecycle,
    apply_point,
    state_from_snapshot,
)
from tests.tennis_v1.test_match_binding import valid_payloads


SHA_A = "a" * 64
START_WALL_NS = 1_500_000_000
CLOSE_WALL_NS = 3_000_000_000


class Task5AmendedContractRed(unittest.TestCase):
    def test_transition_prior_sequence_and_validator_surface_exist(
        self,
    ) -> None:
        self.assertEqual(
            tuple(
                field.name
                for field in fields(
                    synchronizer_module.SynchronizationTransitionResult
                )
            ),
            (
                "state",
                "input",
                "input_sha256",
                "prior_session_sha256",
                "prior_decision_sequence",
                "observation",
                "results",
            ),
        )
        self.assertTrue(
            callable(
                getattr(
                    synchronizer_module,
                    "validate_synchronization_transition",
                    None,
                )
            )
        )

    def test_constructor_rejects_both_result_substitution_directions(
        self,
    ) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        trusted = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point_event(
                    tennis,
                    received_monotonic_ns=150,
                    event_id="trusted-substitution",
                ),
            ),
            now=observation(151),
        )
        blocked = tuple(
            SyncResult(
                result.canonical_match_id,
                result.ticker,
                None,
                None,
                SyncReason.DUPLICATE_STATE_SUPPRESSED,
            )
            for result in trusted.results
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(trusted, results=blocked)
        unsafe_blocked = copy.deepcopy(trusted)
        object.__setattr__(unsafe_blocked, "results", blocked)
        with self.assertRaisesRegex(
            SynchronizationSessionDriftError,
            "^synchronization_session_drift$",
        ):
            synchronizer_module.validate_synchronization_transition(
                state,
                unsafe_blocked,
            )

        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        prior_observation = state.last_observation
        assert prior_observation is not None
        now = observation(prior_observation.monotonic_ns + 1)
        duplicate = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=now,
        )
        self.assertEqual(
            duplicate.results[0].reason,
            SyncReason.DUPLICATE_STATE_SUPPRESSED,
        )
        tennis = tennis_cursor(
            duplicate.state,
            binding.canonical_match_id,
        ).tennis
        book = book_cursor(
            duplicate.state,
            binding.canonical_match_id,
            ticker,
        )
        assert tennis is not None and book.book is not None
        snapshot = TrustedSnapshot(
            decision_sequence=duplicate.state.decision_sequence,
            decision_time=now,
            tennis=tennis,
            book=book.book,
            binding=binding,
            sync_policy_sha256=duplicate.state.sync_policy_sha256,
            causal_provider_revision=(
                None
                if book.causal_point_witness is None
                else book.causal_point_witness.revision
            ),
        )
        fabricated = SyncResult(
            binding.canonical_match_id,
            ticker,
            snapshot,
            opportunity_for_snapshot(
                duplicate.state,
                snapshot,
            ),
            SyncReason.TRUSTED_SYNCHRONIZED,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(duplicate, results=(fabricated,))
        unsafe_trusted = copy.deepcopy(duplicate)
        object.__setattr__(
            unsafe_trusted,
            "results",
            (fabricated,),
        )
        with self.assertRaisesRegex(
            SynchronizationSessionDriftError,
            "^synchronization_session_drift$",
        ):
            synchronizer_module.validate_synchronization_transition(
                state,
                unsafe_trusted,
            )

    def test_authority_validator_recomputes_against_exact_prior(
        self,
    ) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        prior_observation = state.last_observation
        assert prior_observation is not None
        transition = synchronize(
            state,
            clock_input(
                binding.canonical_match_id,
                binding.home_market_ticker,
            ),
            now=observation(prior_observation.monotonic_ns + 1),
        )
        validator = getattr(
            synchronizer_module,
            "validate_synchronization_transition",
        )
        self.assertIsNone(validator(state, transition))
        forged_claim = replace(
            transition,
            prior_session_sha256=SHA_A,
        )
        with self.assertRaisesRegex(
            SynchronizationSessionDriftError,
            "^synchronization_session_drift$",
        ):
            validator(state, forged_claim)

        ticker = binding.home_market_ticker
        tennis = tennis_cursor(
            transition.state,
            binding.canonical_match_id,
        ).tennis
        book = book_cursor(
            transition.state,
            binding.canonical_match_id,
            ticker,
        )
        assert tennis is not None and book.book is not None
        snapshot = TrustedSnapshot(
            decision_sequence=transition.state.decision_sequence,
            decision_time=transition.observation,
            tennis=tennis,
            book=book.book,
            binding=binding,
            sync_policy_sha256=transition.state.sync_policy_sha256,
            causal_provider_revision=(
                None
                if book.causal_point_witness is None
                else book.causal_point_witness.revision
            ),
        )
        forged_sequence = replace(
            transition,
            prior_decision_sequence=state.decision_sequence - 1,
            results=(
                SyncResult(
                    binding.canonical_match_id,
                    ticker,
                    snapshot,
                    opportunity_for_snapshot(
                        transition.state,
                        snapshot,
                    ),
                    SyncReason.TRUSTED_SYNCHRONIZED,
                ),
            ),
        )
        with self.assertRaisesRegex(
            SynchronizationSessionDriftError,
            "^synchronization_session_drift$",
        ):
            validator(state, forged_sequence)
        malformed_prior = copy.deepcopy(state)
        object.__setattr__(
            malformed_prior,
            "sync_policy_sha256",
            SHA_A,
        )
        with self.assertRaisesRegex(
            SynchronizationSessionDriftError,
            "^synchronization_session_drift$",
        ):
            validator(malformed_prior, transition)
        with self.assertRaisesRegex(TypeError, "^prior$"):
            validator(object(), object())
        with self.assertRaisesRegex(TypeError, "^transition$"):
            validator(state, object())


def universe(count: int = 1) -> BindingUniverse:
    return valid_payloads(count)[-1]


def policy(
    value: BindingUniverse,
    **changes: object,
) -> SyncPolicy:
    values: dict[str, object] = {
        "universe_sha256": value.universe_sha256,
        "max_score_age_ns": 1_000,
        "max_book_age_ns": 1_000,
        "max_lifecycle_age_ns": 1_000,
        "max_score_book_skew_ns": 1_000,
        "max_clock_uncertainty_ns": 100,
        "large_book_move_threshold": Decimal("0.05"),
        "explanation_window_ns": 100,
        "minimum_close_horizon_ns": 100,
    }
    values.update(changes)
    return SyncPolicy(**values)  # type: ignore[arg-type]


def observation(
    monotonic_ns: int,
    *,
    wall_ns: int = START_WALL_NS,
    clock_uncertainty_ns: int = 1,
) -> PairedTimeObservation:
    return PairedTimeObservation(
        wall_ns=wall_ns,
        monotonic_ns=monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
    )


def provider_origin(
    binding: object,
    *,
    event_id: str = "provider-origin",
    status: MatchStatus = MatchStatus.LIVE,
    correction_epoch: int = 0,
    revision: int = 0,
    received_monotonic_ns: int = 100,
    **changes: object,
) -> ProviderSnapshot:
    server = (
        PlayerSide.HOME
        if status in {MatchStatus.LIVE, MatchStatus.SUSPENDED}
        else None
    )
    termination = TerminationKind.NONE
    winner = None
    if status is MatchStatus.ENDED:
        termination = TerminationKind.WALKOVER
        winner = PlayerSide.HOME
    elif status is MatchStatus.CANCELLED:
        termination = TerminationKind.CANCELLATION
    values: dict[str, object] = {
        "provider_source_id": binding.provider_source_id,
        "revision_domain_id": binding.revision_domain_id,
        "source_lineage_sha256": binding.source_lineage_sha256,
        "provider_event_id": event_id,
        "provider_match_id": binding.provider_match_id,
        "home_player_id": binding.provider_home_player_id,
        "away_player_id": binding.provider_away_player_id,
        "scheduled_start_wall_ns": binding.scheduled_start_wall_ns,
        "match_format": binding.match_format,
        "status": status,
        "termination_kind": termination,
        "winner": winner,
        "retired_side": None,
        "completed_sets": (),
        "games_home": 0,
        "games_away": 0,
        "points_home": ScoreValue.LOVE,
        "points_away": ScoreValue.LOVE,
        "in_tiebreak": False,
        "tiebreak_points_home": 0,
        "tiebreak_points_away": 0,
        "tiebreak_first_server": None,
        "server_for_next_point": server,
        "correction_epoch": correction_epoch,
        "revision": revision,
        "source_wall_ns": START_WALL_NS - 20,
        "source_generated_wall_ns": START_WALL_NS - 21,
        "received_monotonic_ns": received_monotonic_ns,
        "clock_uncertainty_ns": 1,
        "snapshot_complete": True,
    }
    values.update(changes)
    return ProviderSnapshot(**values)  # type: ignore[arg-type]


def book_origin(
    ticker: str,
    *,
    connection_epoch: int = 1,
    sequence: int = 1,
    observed_monotonic_ns: int = 101,
    clock_uncertainty_ns: int = 1,
    market_status: MarketStatus = MarketStatus.OPEN,
    yes_bids: tuple[object, ...] | None = None,
    no_bids: tuple[object, ...] | None = None,
) -> BookSnapshot:
    from inci_tennis_expert.contracts import BookLevel

    yes = (
        (
            BookLevel(Decimal("0.45"), Decimal("5")),
            BookLevel(Decimal("0.40"), Decimal("5")),
        )
        if yes_bids is None
        else yes_bids
    )
    no = (
        (
            BookLevel(Decimal("0.45"), Decimal("5")),
            BookLevel(Decimal("0.40"), Decimal("5")),
        )
        if no_bids is None
        else no_bids
    )
    return BookSnapshot(
        ticker=ticker,
        connection_epoch=connection_epoch,
        sequence=sequence,
        market_status=market_status,
        scheduled_close_wall_ns=CLOSE_WALL_NS,
        source_wall_ns=START_WALL_NS - 10,
        observed_monotonic_ns=observed_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
        yes_bids=yes,  # type: ignore[arg-type]
        no_bids=no,  # type: ignore[arg-type]
    )


def origin_input(snapshot: ProviderSnapshot, canonical_match_id: str) -> SynchronizationInput:
    return SynchronizationInput(
        kind=SyncInputKind.TENNIS_ORIGIN,
        canonical_match_id=canonical_match_id,
        ticker=None,
        previous_state_sha256=None,
        provider_event=snapshot,
        tennis_transition=None,
        book_event=None,
        book_transition=None,
        book_resnapshot_state=None,
    )


def tennis_input(
    canonical_match_id: str,
    prior: TennisState,
    event: ProviderPoint | ProviderLifecycle | ProviderSnapshot,
) -> SynchronizationInput:
    if type(event) is ProviderPoint:
        claimed = apply_point(prior, event)
    elif type(event) is ProviderLifecycle:
        claimed = apply_lifecycle(prior, event)
    else:
        claimed = apply_correction(prior, event)
    return SynchronizationInput(
        kind=SyncInputKind.TENNIS_TRANSITION,
        canonical_match_id=canonical_match_id,
        ticker=None,
        previous_state_sha256=expert_contract_sha256(prior),
        provider_event=event,
        tennis_transition=claimed,
        book_event=None,
        book_transition=None,
        book_resnapshot_state=None,
    )


def initial_book_input(
    canonical_match_id: str,
    snapshot: BookSnapshot,
) -> SynchronizationInput:
    return SynchronizationInput(
        kind=SyncInputKind.BOOK_TRANSITION,
        canonical_match_id=canonical_match_id,
        ticker=snapshot.ticker,
        previous_state_sha256=None,
        provider_event=None,
        tennis_transition=None,
        book_event=snapshot,
        book_transition=book_from_snapshot(snapshot),
        book_resnapshot_state=None,
    )


def book_input(
    canonical_match_id: str,
    prior: BookState,
    event: BookSnapshot | BookDelta | MarketLifecycle,
) -> SynchronizationInput:
    if type(event) is BookSnapshot:
        claimed = apply_book_snapshot(prior, event)
    elif type(event) is BookDelta:
        claimed = apply_book_delta(prior, event)
    else:
        claimed = apply_market_lifecycle(prior, event)
    return SynchronizationInput(
        kind=SyncInputKind.BOOK_TRANSITION,
        canonical_match_id=canonical_match_id,
        ticker=event.ticker,
        previous_state_sha256=expert_contract_sha256(prior),
        provider_event=None,
        tennis_transition=None,
        book_event=event,
        book_transition=claimed,
        book_resnapshot_state=None,
    )


def clock_input(canonical_match_id: str, ticker: str) -> SynchronizationInput:
    return SynchronizationInput(
        kind=SyncInputKind.CLOCK,
        canonical_match_id=canonical_match_id,
        ticker=ticker,
        previous_state_sha256=None,
        provider_event=None,
        tennis_transition=None,
        book_event=None,
        book_transition=None,
        book_resnapshot_state=None,
    )


def opportunity_for_snapshot(
    state: SynchronizationSessionState,
    snapshot: TrustedSnapshot,
) -> OpportunityFrame:
    binding_sha256 = expert_contract_sha256(snapshot.binding)
    snapshot_sha256 = expert_contract_sha256(snapshot)
    identity = {
        "universe_sha256": state.universe_sha256,
        "canonical_match_id": snapshot.binding.canonical_match_id,
        "ticker": snapshot.book.ticker,
        "decision_sequence": snapshot.decision_sequence,
        "decision_time": snapshot.decision_time,
        "binding_sha256": binding_sha256,
        "provider_revision": snapshot.tennis.revision,
        "book_connection_epoch": snapshot.book.connection_epoch,
        "book_sequence": snapshot.book.sequence,
        "snapshot_sha256": snapshot_sha256,
    }
    opportunity_id = hashlib.sha256(
        b"INCI-OPPORTUNITY-ID-V1\0"
        + canonical_expert_bytes(identity)
    ).hexdigest()
    return OpportunityFrame(
        opportunity_id=opportunity_id,
        universe_sha256=state.universe_sha256,
        canonical_match_id=snapshot.binding.canonical_match_id,
        ticker=snapshot.book.ticker,
        decision_sequence=snapshot.decision_sequence,
        decision_time=snapshot.decision_time,
        binding_sha256=binding_sha256,
        provider_revision=snapshot.tennis.revision,
        book_connection_epoch=snapshot.book.connection_epoch,
        book_sequence=snapshot.book.sequence,
        snapshot_sha256=snapshot_sha256,
        snapshot=snapshot,
    )


def tennis_cursor(state: SynchronizationSessionState, match_id: str):
    return next(
        item
        for item in state.tennis_cursors
        if item.canonical_match_id == match_id
    )


def book_cursor(
    state: SynchronizationSessionState,
    match_id: str,
    ticker: str,
):
    return next(
        item
        for item in state.book_cursors
        if item.canonical_match_id == match_id and item.ticker == ticker
    )


def point_event(
    tennis: TennisState,
    *,
    received_monotonic_ns: int,
    event_id: str = "provider-point",
) -> ProviderPoint:
    assert tennis.server_for_next_point is not None
    return ProviderPoint(
        provider_source_id=tennis.provider_source_id,
        revision_domain_id=tennis.revision_domain_id,
        source_lineage_sha256=tennis.source_lineage_sha256,
        provider_event_id=event_id,
        provider_match_id=tennis.provider_match_id,
        home_player_id=tennis.home_player_id,
        away_player_id=tennis.away_player_id,
        scheduled_start_wall_ns=tennis.scheduled_start_wall_ns,
        match_format=tennis.match_format,
        correction_epoch=tennis.correction_epoch,
        revision=tennis.revision + 1,
        point_winner=PlayerSide.HOME,
        server_before_point=tennis.server_for_next_point,
        source_wall_ns=START_WALL_NS - 5,
        source_generated_wall_ns=START_WALL_NS - 6,
        received_monotonic_ns=received_monotonic_ns,
        clock_uncertainty_ns=1,
    )


def correction_event(
    tennis: TennisState,
    *,
    received_monotonic_ns: int,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_source_id=tennis.provider_source_id,
        revision_domain_id=tennis.revision_domain_id,
        source_lineage_sha256=tennis.source_lineage_sha256,
        provider_event_id="provider-correction",
        provider_match_id=tennis.provider_match_id,
        home_player_id=tennis.home_player_id,
        away_player_id=tennis.away_player_id,
        scheduled_start_wall_ns=tennis.scheduled_start_wall_ns,
        match_format=tennis.match_format,
        status=tennis.status,
        termination_kind=tennis.termination_kind,
        winner=tennis.winner,
        retired_side=tennis.retired_side,
        completed_sets=tennis.completed_sets,
        games_home=tennis.games_home,
        games_away=tennis.games_away,
        points_home=tennis.points_home,
        points_away=tennis.points_away,
        in_tiebreak=tennis.in_tiebreak,
        tiebreak_points_home=tennis.tiebreak_points_home,
        tiebreak_points_away=tennis.tiebreak_points_away,
        tiebreak_first_server=tennis.tiebreak_first_server,
        server_for_next_point=tennis.server_for_next_point,
        correction_epoch=tennis.correction_epoch + 1,
        revision=0,
        source_wall_ns=START_WALL_NS - 4,
        source_generated_wall_ns=START_WALL_NS - 5,
        received_monotonic_ns=received_monotonic_ns,
        clock_uncertainty_ns=1,
        snapshot_complete=True,
    )


def provider_lifecycle_event(
    tennis: TennisState,
    *,
    received_monotonic_ns: int,
    event_id: str = "provider-lifecycle",
    kind: ProviderLifecycleKind = ProviderLifecycleKind.SUSPEND,
) -> ProviderLifecycle:
    return ProviderLifecycle(
        provider_source_id=tennis.provider_source_id,
        revision_domain_id=tennis.revision_domain_id,
        source_lineage_sha256=tennis.source_lineage_sha256,
        provider_event_id=event_id,
        provider_match_id=tennis.provider_match_id,
        home_player_id=tennis.home_player_id,
        away_player_id=tennis.away_player_id,
        scheduled_start_wall_ns=tennis.scheduled_start_wall_ns,
        match_format=tennis.match_format,
        correction_epoch=tennis.correction_epoch,
        revision=tennis.revision + 1,
        kind=kind,
        winner=None,
        retired_side=None,
        server_for_next_point=tennis.server_for_next_point,
        source_wall_ns=START_WALL_NS - 4,
        source_generated_wall_ns=START_WALL_NS - 5,
        received_monotonic_ns=received_monotonic_ns,
        clock_uncertainty_ns=1,
    )


def large_delta(
    book: BookState,
    *,
    observed_monotonic_ns: int,
    restore: bool = False,
) -> BookDelta:
    return BookDelta(
        ticker=book.ticker,
        connection_epoch=book.connection_epoch,
        sequence=book.sequence + 1,
        source_wall_ns=START_WALL_NS - 3,
        observed_monotonic_ns=observed_monotonic_ns,
        clock_uncertainty_ns=1,
        contract_side=ContractSide.NO,
        price=Decimal("0.45"),
        quantity=Decimal("5") if restore else Decimal("0"),
    )


def tiny_delta(
    book: BookState,
    *,
    observed_monotonic_ns: int,
) -> BookDelta:
    return BookDelta(
        ticker=book.ticker,
        connection_epoch=book.connection_epoch,
        sequence=book.sequence + 1,
        source_wall_ns=START_WALL_NS - 2,
        observed_monotonic_ns=observed_monotonic_ns,
        clock_uncertainty_ns=1,
        contract_side=ContractSide.YES,
        price=Decimal("0.40"),
        quantity=Decimal("4"),
    )


def market_lifecycle_event(
    book: BookState,
    *,
    observed_monotonic_ns: int,
    market_status: MarketStatus | None = None,
    scheduled_close_wall_ns: int | None = None,
) -> MarketLifecycle:
    return MarketLifecycle(
        ticker=book.ticker,
        connection_epoch=book.connection_epoch,
        market_status=(
            book.market_status
            if market_status is None
            else market_status
        ),
        scheduled_close_wall_ns=(
            book.scheduled_close_wall_ns
            if scheduled_close_wall_ns is None
            else scheduled_close_wall_ns
        ),
        source_wall_ns=START_WALL_NS - 1,
        observed_monotonic_ns=observed_monotonic_ns,
        clock_uncertainty_ns=1,
    )


def ready_session(
    *,
    count: int = 1,
) -> tuple[SynchronizationSessionState, BindingUniverse]:
    value = universe(count)
    state = synchronization_session_from_artifacts(value, policy(value))
    monotonic = 110
    for binding in value.bindings:
        origin = provider_origin(binding)
        state = synchronize(
            state,
            origin_input(origin, binding.canonical_match_id),
            now=observation(monotonic),
        ).state
        monotonic += 1
        for ticker in sorted(
            (binding.home_market_ticker, binding.away_market_ticker)
        ):
            snapshot = book_origin(
                ticker,
                observed_monotonic_ns=monotonic - 5,
            )
            state = synchronize(
                state,
                initial_book_input(binding.canonical_match_id, snapshot),
                now=observation(monotonic),
            ).state
            monotonic += 1
    return state, value


def ready_session_with_policy(
    *,
    book_observed_monotonic_ns: int = 100,
    provider_received_monotonic_ns: int = 100,
    provider_clock_uncertainty_ns: int = 1,
    book_clock_uncertainty_ns: int = 1,
    **policy_changes: object,
) -> tuple[SynchronizationSessionState, BindingUniverse]:
    value = universe()
    frozen_policy = policy(value, **policy_changes)
    state = synchronization_session_from_artifacts(
        value,
        frozen_policy,
    )
    binding = value.bindings[0]
    origin = provider_origin(
        binding,
        received_monotonic_ns=provider_received_monotonic_ns,
        clock_uncertainty_ns=provider_clock_uncertainty_ns,
    )
    initial_now = max(
        provider_received_monotonic_ns,
        book_observed_monotonic_ns,
    )
    state = synchronize(
        state,
        origin_input(origin, binding.canonical_match_id),
        now=observation(initial_now),
    ).state
    for ticker in sorted(
        (
            binding.home_market_ticker,
            binding.away_market_ticker,
        )
    ):
        snapshot = book_origin(
            ticker,
            observed_monotonic_ns=book_observed_monotonic_ns,
            clock_uncertainty_ns=book_clock_uncertainty_ns,
        )
        state = synchronize(
            state,
            initial_book_input(binding.canonical_match_id, snapshot),
            now=observation(initial_now),
        ).state
    return state, value


class GenesisAndIntegrityTests(unittest.TestCase):
    def test_genesis_is_exact_empty_sorted_and_compatible(self) -> None:
        value = universe(3)
        frozen_policy = policy(value)
        state = synchronization_session_from_artifacts(
            value,
            frozen_policy,
        )
        self.assertEqual(state.universe, value)
        self.assertEqual(state.policy, frozen_policy)
        self.assertEqual(state.decision_sequence, 0)
        self.assertIsNone(state.last_observation)
        self.assertEqual(
            tuple(item.canonical_match_id for item in state.tennis_cursors),
            tuple(sorted(binding.canonical_match_id for binding in value.bindings)),
        )
        self.assertEqual(
            tuple(
                (item.canonical_match_id, item.ticker)
                for item in state.book_cursors
            ),
            tuple(
                sorted(
                    (
                        binding.canonical_match_id,
                        ticker,
                    )
                    for binding in value.bindings
                    for ticker in (
                        binding.home_market_ticker,
                        binding.away_market_ticker,
                    )
                )
            ),
        )
        self.assertIsNone(
            assert_synchronization_session_compatible(
                state,
                value,
                frozen_policy,
            )
        )

    def test_public_api_type_and_drift_precedence(self) -> None:
        value = universe()
        frozen_policy = policy(value)
        state = synchronization_session_from_artifacts(value, frozen_policy)
        with self.assertRaisesRegex(TypeError, "^universe$"):
            synchronization_session_from_artifacts(
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^policy$"):
            synchronization_session_from_artifacts(
                value,
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronization_session_from_artifacts(
                value,
                policy(value, universe_sha256=SHA_A),
            )
        with self.assertRaisesRegex(TypeError, "^state$"):
            assert_synchronization_session_compatible(
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^evidence$"):
            synchronize(
                state,
                object(),  # type: ignore[arg-type]
                now=object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^now$"):
            synchronize(
                state,
                clock_input(
                    value.bindings[0].canonical_match_id,
                    value.bindings[0].home_market_ticker,
                ),
                now=object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^universe$"):
            assert_synchronization_session_compatible(
                state,
                object(),  # type: ignore[arg-type]
                object(),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "^policy$"):
            assert_synchronization_session_compatible(
                state,
                value,
                object(),  # type: ignore[arg-type]
            )

    def test_global_clock_regression_precedes_evidence(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        prior = state.last_observation
        assert prior is not None
        with self.assertRaises(SynchronizationSessionDriftError) as caught:
            synchronize(
                state,
                clock_input(binding.canonical_match_id, "UNBOUND"),
                now=observation(
                    prior.monotonic_ns - 1,
                    wall_ns=prior.wall_ns,
                ),
            )
        self.assertEqual(
            str(caught.exception),
            "synchronization_session_drift",
        )

    def test_canonical_round_trip_does_not_create_restart_api(self) -> None:
        state, _ = ready_session()
        encoded = canonical_expert_bytes(state)
        self.assertEqual(encoded, canonical_expert_bytes(state))
        self.assertNotIn("resume", tuple(field.name for field in fields(state)))
        self.assertNotIn("restart", tuple(field.name for field in fields(state)))


class OrderedIngestionTests(unittest.TestCase):
    def test_origin_precedence_binding_drift_and_exact_establishment(self) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(value, policy(value))
        mismatch = provider_origin(
            binding,
            provider_match_id="different-match",
        )
        rejected = synchronize(
            state,
            origin_input(mismatch, binding.canonical_match_id),
            now=observation(110),
        )
        self.assertEqual(
            tuple(result.reason for result in rejected.results),
            (SyncReason.BINDING_DRIFT, SyncReason.BINDING_DRIFT),
        )
        self.assertIsNone(
            tennis_cursor(
                rejected.state,
                binding.canonical_match_id,
            ).tennis
        )
        exact = provider_origin(binding)
        accepted = synchronize(
            rejected.state,
            origin_input(exact, binding.canonical_match_id),
            now=observation(111),
        )
        self.assertIsNotNone(
            tennis_cursor(
                accepted.state,
                binding.canonical_match_id,
            ).tennis
        )
        second_mismatch = provider_origin(
            binding,
            event_id="second-origin",
            provider_match_id="different-match",
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                accepted.state,
                origin_input(
                    second_mismatch,
                    binding.canonical_match_id,
                ),
                now=observation(112),
            )

    def test_unbound_membership_advances_only_observation(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        now = observation(state.last_observation.monotonic_ns + 1)  # type: ignore[union-attr]
        transition = synchronize(
            state,
            clock_input(binding.canonical_match_id, "UNBOUND-TICKER"),
            now=now,
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.CONTRACT_MISMATCH,
        )
        self.assertEqual(
            replace(transition.state, last_observation=state.last_observation),
            state,
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                clock_input("unknown-match", "UNBOUND-TICKER"),
                now=now,
            )

    def test_tennis_result_and_previous_digest_are_recomputed(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        cursor = tennis_cursor(state, binding.canonical_match_id)
        assert cursor.tennis is not None
        event = point_event(cursor.tennis, received_monotonic_ns=120)
        evidence = tennis_input(
            binding.canonical_match_id,
            cursor.tennis,
            event,
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                replace(evidence, previous_state_sha256=SHA_A),
                now=observation(121),
            )
        forged = replace(
            evidence.tennis_transition,
            disposition=TransitionDisposition.DUPLICATE,
            reason=TennisTransitionReason.EXACT_DUPLICATE,
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                replace(evidence, tennis_transition=forged),
                now=observation(121),
            )
        accepted = synchronize(
            state,
            evidence,
            now=observation(121),
        )
        self.assertEqual(
            tennis_cursor(
                accepted.state,
                binding.canonical_match_id,
            ).tennis,
            evidence.tennis_transition.state,
        )

    def test_book_result_and_event_chain_are_recomputed(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        event = tiny_delta(cursor.book, observed_monotonic_ns=125)
        evidence = book_input(
            binding.canonical_match_id,
            cursor.book,
            event,
        )
        forged = book_from_snapshot(
            book_origin(
                ticker,
                connection_epoch=9,
                sequence=1,
                observed_monotonic_ns=125,
            )
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                replace(evidence, book_transition=forged),
                now=observation(126),
            )
        accepted = synchronize(
            state,
            evidence,
            now=observation(126),
        )
        self.assertEqual(
            book_cursor(
                accepted.state,
                binding.canonical_match_id,
                ticker,
            ).book,
            evidence.book_transition.state,
        )


class CausalBarrierTests(unittest.TestCase):
    def pending_after_large(
        self,
        *,
        threshold: Decimal = Decimal("0.05"),
        move_time: int = 130,
    ) -> tuple[SynchronizationSessionState, BindingUniverse, str]:
        state, value = ready_session()
        if threshold != state.policy.large_book_move_threshold:
            state = assert_policy_rebuilt(state, threshold)
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        event = large_delta(
            cursor.book,
            observed_monotonic_ns=move_time,
        )
        transition = synchronize(
            state,
            book_input(binding.canonical_match_id, cursor.book, event),
            now=observation(move_time + 1),
        )
        return transition.state, value, ticker

    def test_threshold_equality_is_large_and_zero_never_is(self) -> None:
        state, value, ticker = self.pending_after_large()
        binding = value.bindings[0]
        self.assertIsNotNone(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )
        zero_policy = policy(value, large_book_move_threshold=Decimal("0"))
        zero_state = synchronization_session_from_artifacts(
            value,
            zero_policy,
        )
        origin = provider_origin(value.bindings[0])
        zero_state = synchronize(
            zero_state,
            origin_input(origin, value.bindings[0].canonical_match_id),
            now=observation(110),
        ).state
        snapshot = book_origin(ticker)
        first = synchronize(
            zero_state,
            initial_book_input(value.bindings[0].canonical_match_id, snapshot),
            now=observation(111),
        ).state
        cursor = book_cursor(
            first,
            value.bindings[0].canonical_match_id,
            ticker,
        )
        assert cursor.book is not None
        zero = tiny_delta(cursor.book, observed_monotonic_ns=112)
        after = synchronize(
            first,
            book_input(
                value.bindings[0].canonical_match_id,
                cursor.book,
                zero,
            ),
            now=observation(113),
        ).state
        self.assertIsNone(
            book_cursor(
                after,
                value.bindings[0].canonical_match_id,
                ticker,
            ).pending_move
        )

    def test_directional_window_boundaries_and_delayed_ingestion(self) -> None:
        state, value, ticker = self.pending_after_large(move_time=200)
        binding = value.bindings[0]
        current = tennis_cursor(state, binding.canonical_match_id)
        assert current.tennis is not None
        point = point_event(
            current.tennis,
            received_monotonic_ns=100,
        )
        explained = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                current.tennis,
                point,
            ),
            now=observation(202),
        ).state
        explained_cursor = book_cursor(
            explained,
            binding.canonical_match_id,
            ticker,
        )
        self.assertIsNone(explained_cursor.pending_move)
        self.assertIsNotNone(explained_cursor.causal_point_witness)

        late_state, value, ticker = self.pending_after_large(move_time=130)
        current = tennis_cursor(late_state, binding.canonical_match_id)
        assert current.tennis is not None
        after_move = point_event(
            current.tennis,
            received_monotonic_ns=131,
        )
        blocked = synchronize(
            late_state,
            tennis_input(
                binding.canonical_match_id,
                current.tennis,
                after_move,
            ),
            now=observation(132),
        )
        self.assertIsNotNone(
            book_cursor(
                blocked.state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )
        self.assertIn(
            SyncReason.UNEXPLAINED_BOOK_MOVE,
            tuple(result.reason for result in blocked.results),
        )

    def test_tiny_gap_resnapshot_and_round_trip_preserve_pending(self) -> None:
        state, value, ticker = self.pending_after_large(move_time=130)
        binding = value.bindings[0]
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        pending_bytes = canonical_expert_bytes(cursor.pending_move)
        assert cursor.book is not None
        tiny = tiny_delta(cursor.book, observed_monotonic_ns=132)
        after_tiny = synchronize(
            state,
            book_input(binding.canonical_match_id, cursor.book, tiny),
            now=observation(133),
        ).state
        self.assertEqual(
            canonical_expert_bytes(
                book_cursor(
                    after_tiny,
                    binding.canonical_match_id,
                    ticker,
                ).pending_move
            ),
            pending_bytes,
        )
        current = book_cursor(
            after_tiny,
            binding.canonical_match_id,
            ticker,
        )
        assert current.book is not None
        gap_event = BookDelta(
            ticker=ticker,
            connection_epoch=current.book.connection_epoch,
            sequence=current.book.sequence + 2,
            source_wall_ns=START_WALL_NS,
            observed_monotonic_ns=134,
            clock_uncertainty_ns=1,
            contract_side=ContractSide.YES,
            price=Decimal("0.40"),
            quantity=Decimal("3"),
        )
        after_gap = synchronize(
            after_tiny,
            book_input(
                binding.canonical_match_id,
                current.book,
                gap_event,
            ),
            now=observation(135),
        ).state
        self.assertEqual(
            canonical_expert_bytes(
                book_cursor(
                    after_gap,
                    binding.canonical_match_id,
                    ticker,
                ).pending_move
            ),
            pending_bytes,
        )

    def test_consumed_point_cannot_explain_twice_but_new_point_can(self) -> None:
        state, value, ticker = self.pending_after_large(move_time=130)
        binding = value.bindings[0]
        tcursor = tennis_cursor(state, binding.canonical_match_id)
        assert tcursor.tennis is not None
        first = point_event(
            tcursor.tennis,
            received_monotonic_ns=129,
            event_id="point-one",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tcursor.tennis,
                first,
            ),
            now=observation(132),
        ).state
        bcursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert bcursor.book is not None
        second_move = large_delta(
            bcursor.book,
            observed_monotonic_ns=140,
            restore=True,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                bcursor.book,
                second_move,
            ),
            now=observation(141),
        ).state
        self.assertIsNotNone(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )
        tcursor = tennis_cursor(state, binding.canonical_match_id)
        assert tcursor.tennis is not None
        new_point = point_event(
            tcursor.tennis,
            received_monotonic_ns=139,
            event_id="point-two",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tcursor.tennis,
                new_point,
            ),
            now=observation(142),
        ).state
        self.assertIsNone(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )

    def test_pre_origin_floor_and_paired_complete_reset(self) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(value, policy(value))
        ticker = binding.home_market_ticker
        snapshot = book_origin(ticker, observed_monotonic_ns=101)
        state = synchronize(
            state,
            initial_book_input(binding.canonical_match_id, snapshot),
            now=observation(110),
        ).state
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        move = large_delta(cursor.book, observed_monotonic_ns=120)
        state = synchronize(
            state,
            book_input(binding.canonical_match_id, cursor.book, move),
            now=observation(121),
        ).state
        self.assertIsNone(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move.tennis_correction_epoch_floor  # type: ignore[union-attr]
        )
        origin = provider_origin(binding, received_monotonic_ns=100)
        state = synchronize(
            state,
            origin_input(origin, binding.canonical_match_id),
            now=observation(122),
        ).state
        self.assertEqual(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move.tennis_correction_epoch_floor,  # type: ignore[union-attr]
            0,
        )
        tcursor = tennis_cursor(state, binding.canonical_match_id)
        assert tcursor.tennis is not None
        correction = correction_event(
            tcursor.tennis,
            received_monotonic_ns=120,
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tcursor.tennis,
                correction,
            ),
            now=observation(123),
        ).state
        bcursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert bcursor.book is not None
        required = require_book_resnapshot(bcursor.book)
        resnapshot_input = SynchronizationInput(
            kind=SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
            canonical_match_id=binding.canonical_match_id,
            ticker=ticker,
            previous_state_sha256=expert_contract_sha256(bcursor.book),
            provider_event=None,
            tennis_transition=None,
            book_event=None,
            book_transition=None,
            book_resnapshot_state=required,
        )
        state = synchronize(
            state,
            resnapshot_input,
            now=observation(124),
        ).state
        replacement = book_origin(
            ticker,
            connection_epoch=2,
            sequence=1,
            observed_monotonic_ns=125,
            yes_bids=bcursor.book.yes_bids,
            no_bids=bcursor.book.no_bids,
        )
        bcursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert bcursor.book is not None
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                bcursor.book,
                replacement,
            ),
            now=observation(126),
        ).state
        self.assertIsNone(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )


def assert_policy_rebuilt(
    state: SynchronizationSessionState,
    threshold: Decimal,
) -> SynchronizationSessionState:
    value = state.universe
    return synchronization_session_from_artifacts(
        value,
        policy(value, large_book_move_threshold=threshold),
    )


class PrecedenceAndEmissionTests(unittest.TestCase):
    def test_reachable_first_match_blocker_collisions(self) -> None:
        value = universe()
        binding = value.bindings[0]
        cases = (
            (
                MatchStatus.SCHEDULED,
                MarketStatus.SUSPENDED,
                SyncReason.MATCH_NOT_STARTED,
            ),
            (
                MatchStatus.SUSPENDED,
                MarketStatus.PREOPEN,
                SyncReason.MATCH_SUSPENDED,
            ),
            (
                MatchStatus.ENDED,
                MarketStatus.CLOSED,
                SyncReason.MATCH_ENDED,
            ),
        )
        for match_status, market_status, expected in cases:
            state = synchronization_session_from_artifacts(
                value,
                policy(value),
            )
            origin = provider_origin(binding, status=match_status)
            state = synchronize(
                state,
                origin_input(origin, binding.canonical_match_id),
                now=observation(110),
            ).state
            snapshot = book_origin(
                binding.home_market_ticker,
                market_status=market_status,
            )
            result = synchronize(
                state,
                initial_book_input(binding.canonical_match_id, snapshot),
                now=observation(111),
            ).results[0]
            with self.subTest(match=match_status, market=market_status):
                self.assertEqual(result.reason, expected)

    def test_market_lifecycle_reason_mapping_is_disjoint(self) -> None:
        expected = {
            MarketStatus.SUSPENDED: SyncReason.MARKET_SUSPENDED,
            MarketStatus.PREOPEN: SyncReason.MARKET_NOT_OPEN,
            MarketStatus.CLOSED: SyncReason.MARKET_ENDED,
            MarketStatus.SETTLED: SyncReason.MARKET_ENDED,
            MarketStatus.CANCELLED: SyncReason.MARKET_ENDED,
        }
        value = universe()
        binding = value.bindings[0]
        for market_status, reason in expected.items():
            state = synchronization_session_from_artifacts(
                value,
                policy(value),
            )
            state = synchronize(
                state,
                origin_input(
                    provider_origin(binding),
                    binding.canonical_match_id,
                ),
                now=observation(110),
            ).state
            snapshot = book_origin(
                binding.home_market_ticker,
                market_status=market_status,
            )
            result = synchronize(
                state,
                initial_book_input(binding.canonical_match_id, snapshot),
                now=observation(111),
            ).results[0]
            with self.subTest(market_status=market_status):
                self.assertEqual(result.reason, reason)

    def test_freshness_uncertainty_skew_and_horizon_boundaries(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        tcursor = tennis_cursor(state, binding.canonical_match_id)
        bcursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert tcursor.tennis is not None and bcursor.book is not None
        now_equal = min(
            tcursor.tennis.last_received_monotonic_ns
            + state.policy.max_score_age_ns,
            bcursor.book.book_observed_monotonic_ns
            + state.policy.max_book_age_ns,
            bcursor.book.lifecycle_observed_monotonic_ns
            + state.policy.max_lifecycle_age_ns,
        )
        at_equal = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(now_equal),
        )
        self.assertNotIn(
            at_equal.results[0].reason,
            {
                SyncReason.SCORE_STALE,
                SyncReason.BOOK_STALE,
                SyncReason.LIFECYCLE_STALE,
            },
        )
        one_late = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(
                tcursor.tennis.last_received_monotonic_ns
                + state.policy.max_score_age_ns
                + 1
            ),
        )
        self.assertEqual(one_late.results[0].reason, SyncReason.SCORE_STALE)
        uncertain = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(
                state.last_observation.monotonic_ns + 1,  # type: ignore[union-attr]
                clock_uncertainty_ns=state.policy.max_clock_uncertainty_ns
                + 1,
            ),
        )
        self.assertEqual(
            uncertain.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )
        close_equal_state = replace_book(
            state,
            binding.canonical_match_id,
            ticker,
            scheduled_close_wall_ns=START_WALL_NS
            + state.policy.minimum_close_horizon_ns,
        )
        close_equal = synchronize(
            close_equal_state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(
                close_equal_state.last_observation.monotonic_ns + 1,  # type: ignore[union-attr]
            ),
        )
        self.assertNotEqual(
            close_equal.results[0].reason,
            SyncReason.CLOSE_HORIZON_INSUFFICIENT,
        )
        close_short_state = replace_book(
            state,
            binding.canonical_match_id,
            ticker,
            scheduled_close_wall_ns=START_WALL_NS
            + state.policy.minimum_close_horizon_ns
            - 1,
        )
        close_short = synchronize(
            close_short_state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(
                close_short_state.last_observation.monotonic_ns + 1,  # type: ignore[union-attr]
            ),
        )
        self.assertEqual(
            close_short.results[0].reason,
            SyncReason.CLOSE_HORIZON_INSUFFICIENT,
        )

    def test_empty_book_then_horizon_then_pending_precedence(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        empty = replace_book(
            state,
            binding.canonical_match_id,
            ticker,
            yes_bids=(),
            no_bids=(),
            scheduled_close_wall_ns=START_WALL_NS,
        )
        result = synchronize(
            empty,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(empty.last_observation.monotonic_ns + 1),  # type: ignore[union-attr]
        ).results[0]
        self.assertEqual(result.reason, SyncReason.BOOK_NOT_EXECUTABLE)

    def test_duplicate_suppression_and_global_sequences(self) -> None:
        state, value = ready_session(count=3)
        original_sequence = state.decision_sequence
        for _ in range(1_000):
            prior = state.last_observation
            assert prior is not None
            binding = value.bindings[0]
            transition = synchronize(
                state,
                clock_input(
                    binding.canonical_match_id,
                    binding.home_market_ticker,
                ),
                now=prior,
            )
            self.assertEqual(
                transition.results[0].reason,
                SyncReason.DUPLICATE_STATE_SUPPRESSED,
            )
            state = transition.state
        self.assertEqual(state.decision_sequence, original_sequence)

    def test_independent_reductions_and_decimal_context_are_identical(self) -> None:
        value = universe()
        frozen_policy = policy(value)
        expected = synchronization_session_from_artifacts(
            value,
            frozen_policy,
        )
        expected_bytes = canonical_expert_bytes(expected)
        for _ in range(1_000):
            self.assertEqual(
                canonical_expert_bytes(
                    synchronization_session_from_artifacts(
                        value,
                        frozen_policy,
                    )
                ),
                expected_bytes,
            )
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_DOWN
            context.traps[InvalidOperation] = False
            context.traps[DivisionByZero] = False
            context.traps[Overflow] = False
            self.assertEqual(
                canonical_expert_bytes(
                    synchronization_session_from_artifacts(
                        value,
                        frozen_policy,
                    )
                ),
                expected_bytes,
            )


def replace_book(
    state: SynchronizationSessionState,
    match_id: str,
    ticker: str,
    **changes: object,
) -> SynchronizationSessionState:
    cursors = []
    for cursor in state.book_cursors:
        if (
            cursor.canonical_match_id == match_id
            and cursor.ticker == ticker
        ):
            assert cursor.book is not None
            book = replace(cursor.book, **changes)
            cursor = replace(
                cursor,
                book=book,
                last_state_sha256=expert_contract_sha256(book),
            )
        cursors.append(cursor)
    return replace(state, book_cursors=tuple(cursors))


class AdvancedCausalBarrierTests(unittest.TestCase):
    def _pending(
        self,
        move_time: int = 200,
    ) -> tuple[SynchronizationSessionState, BindingUniverse, str]:
        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        transition = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                large_delta(
                    cursor.book,
                    observed_monotonic_ns=move_time,
                ),
            ),
            now=observation(move_time + 1),
        )
        return transition.state, value, ticker

    def test_window_one_past_and_receipt_after_move_do_not_explain(self) -> None:
        for receipt, expected_time in ((100, 201), (201, 200)):
            state, value, ticker = self._pending(expected_time)
            binding = value.bindings[0]
            cursor = tennis_cursor(state, binding.canonical_match_id)
            assert cursor.tennis is not None
            point = point_event(
                cursor.tennis,
                received_monotonic_ns=receipt,
                event_id=f"point-{receipt}",
            )
            transition = synchronize(
                state,
                tennis_input(
                    binding.canonical_match_id,
                    cursor.tennis,
                    point,
                ),
                now=observation(202),
            )
            with self.subTest(receipt=receipt):
                self.assertIsNotNone(
                    book_cursor(
                        transition.state,
                        binding.canonical_match_id,
                        ticker,
                    ).pending_move
                )
                self.assertEqual(
                    transition.results[
                        tuple(
                            sorted(
                                (
                                    binding.home_market_ticker,
                                    binding.away_market_ticker,
                                )
                            )
                        ).index(ticker)
                    ].reason,
                    SyncReason.UNEXPLAINED_BOOK_MOVE,
                )

    def test_multiple_large_moves_merge_and_one_point_covers_interval(self) -> None:
        state, value, ticker = self._pending(200)
        binding = value.bindings[0]
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                large_delta(
                    cursor.book,
                    observed_monotonic_ns=210,
                    restore=True,
                ),
            ),
            now=observation(211),
        ).state
        pending = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        ).pending_move
        assert pending is not None
        self.assertEqual(pending.first_move_monotonic_ns, 200)
        self.assertEqual(pending.last_move_monotonic_ns, 210)
        self.assertEqual(pending.move_count, 2)
        self.assertEqual(pending.max_magnitude, Decimal("0.05"))
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=110,
            event_id="interval-point",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            now=observation(212),
        ).state
        explained = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        )
        self.assertIsNone(explained.pending_move)
        self.assertEqual(
            explained.causal_point_witness,
            explained.consumed_point_witness,
        )

    def test_causal_revision_persists_and_new_large_move_clears_it(self) -> None:
        state, value, ticker = self._pending(200)
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        first = point_event(
            tennis,
            received_monotonic_ns=150,
            event_id="causal-point",
        )
        transition = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                first,
            ),
            now=observation(202),
        )
        state = transition.state
        causal = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        ).causal_point_witness
        assert causal is not None
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        suspended = provider_lifecycle_event(
            tennis,
            received_monotonic_ns=203,
            event_id="later-suspend",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                suspended,
            ),
            now=observation(204),
        ).state
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        resumed = provider_lifecycle_event(
            tennis,
            received_monotonic_ns=205,
            event_id="later-resume",
            kind=ProviderLifecycleKind.RESUME,
        )
        transition = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                resumed,
            ),
            now=observation(206),
        )
        matching = next(
            result
            for result in transition.results
            if result.ticker == ticker
        )
        self.assertEqual(
            matching.snapshot.causal_provider_revision,  # type: ignore[union-attr]
            causal.revision,
        )
        state = transition.state
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                large_delta(
                    cursor.book,
                    observed_monotonic_ns=210,
                    restore=True,
                ),
            ),
            now=observation(211),
        ).state
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        self.assertIsNone(cursor.causal_point_witness)
        self.assertIsNotNone(cursor.pending_move)
        self.assertEqual(cursor.consumed_point_witness, causal)

    def test_same_point_consumption_is_independent_per_ticker(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        for move_time, ticker in enumerate(
            sorted(
                (
                    binding.home_market_ticker,
                    binding.away_market_ticker,
                )
            ),
            start=200,
        ):
            cursor = book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            )
            assert cursor.book is not None
            state = synchronize(
                state,
                book_input(
                    binding.canonical_match_id,
                    cursor.book,
                    large_delta(
                        cursor.book,
                        observed_monotonic_ns=move_time,
                    ),
                ),
                now=observation(move_time),
            ).state
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=150,
            event_id="shared-point",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            now=observation(202),
        ).state
        cursors = tuple(
            cursor
            for cursor in state.book_cursors
            if cursor.canonical_match_id == binding.canonical_match_id
        )
        self.assertTrue(
            all(cursor.pending_move is None for cursor in cursors)
        )
        self.assertEqual(
            len(
                {
                    cursor.consumed_point_witness
                    for cursor in cursors
                }
            ),
            1,
        )

    def test_correction_clears_explanation_but_preserves_consumption(self) -> None:
        state, value, ticker = self._pending(200)
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=150,
            event_id="pre-correction-point",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            now=observation(202),
        ).state
        before = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        )
        consumed = before.consumed_point_witness
        self.assertIsNotNone(before.causal_point_witness)
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        correction = correction_event(
            tennis,
            received_monotonic_ns=203,
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                correction,
            ),
            now=observation(204),
        ).state
        after = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        )
        self.assertIsNone(after.causal_point_witness)
        self.assertEqual(after.consumed_point_witness, consumed)
        self.assertIsNone(
            tennis_cursor(
                state,
                binding.canonical_match_id,
            ).last_point_witness
        )


class ProtocolAndEmissionMatrixTests(unittest.TestCase):
    def test_transition_before_origins_and_missing_claims_are_global_drift(
        self,
    ) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        origin = provider_origin(binding)
        external = state_from_snapshot(origin)
        point = point_event(
            external,
            received_monotonic_ns=101,
            event_id="transition-before-origin",
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                tennis_input(
                    binding.canonical_match_id,
                    external,
                    point,
                ),
                now=observation(102),
            )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                SynchronizationInput(
                    kind=SyncInputKind.BOOK_TRANSITION,
                    canonical_match_id=binding.canonical_match_id,
                    ticker=binding.home_market_ticker,
                    previous_state_sha256=SHA_A,
                    provider_event=None,
                    tennis_transition=None,
                    book_event=BookDelta(
                        ticker=binding.home_market_ticker,
                        connection_epoch=1,
                        sequence=1,
                        source_wall_ns=START_WALL_NS,
                        observed_monotonic_ns=100,
                        clock_uncertainty_ns=1,
                        contract_side=ContractSide.YES,
                        price=Decimal("0.4"),
                        quantity=Decimal("1"),
                    ),
                    book_transition=None,
                    book_resnapshot_state=None,
                ),
                now=observation(100),
            )
        state = synchronize(
            state,
            origin_input(origin, binding.canonical_match_id),
            now=observation(101),
        ).state
        snapshot = book_origin(binding.home_market_ticker)
        missing_claim = replace(
            initial_book_input(binding.canonical_match_id, snapshot),
            book_transition=None,
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                missing_claim,
                now=observation(102),
            )

    def test_unbound_book_and_resnapshot_advance_only_observation(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        unbound_snapshot = book_origin("UNBOUND-MARKET")
        inputs = (
            initial_book_input(
                binding.canonical_match_id,
                unbound_snapshot,
            ),
            SynchronizationInput(
                kind=SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
                canonical_match_id=binding.canonical_match_id,
                ticker="UNBOUND-MARKET",
                previous_state_sha256=SHA_A,
                provider_event=None,
                tennis_transition=None,
                book_event=None,
                book_transition=None,
                book_resnapshot_state=book_from_snapshot(
                    unbound_snapshot
                ).state,
            ),
        )
        for offset, evidence in enumerate(inputs, start=1):
            now = observation(
                state.last_observation.monotonic_ns + offset  # type: ignore[union-attr]
            )
            transition = synchronize(state, evidence, now=now)
            with self.subTest(kind=evidence.kind):
                self.assertEqual(
                    transition.results[0].reason,
                    SyncReason.CONTRACT_MISMATCH,
                )
                self.assertEqual(
                    replace(
                        transition.state,
                        last_observation=state.last_observation,
                    ),
                    state,
                )

    def test_every_input_is_embedded_with_exact_time_and_prior_digest(
        self,
    ) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        evidence = clock_input(
            binding.canonical_match_id,
            binding.home_market_ticker,
        )
        now = state.last_observation
        assert now is not None
        transition = synchronize(state, evidence, now=now)
        self.assertIs(transition.input, evidence)
        self.assertIs(transition.observation, now)
        self.assertEqual(
            transition.prior_session_sha256,
            expert_contract_sha256(state),
        )
        self.assertEqual(
            transition.input_sha256,
            expert_contract_sha256(evidence),
        )

    def test_three_matches_two_tickers_share_one_gap_free_sequence(self) -> None:
        state, value = ready_session(count=3)
        observed_sequences = []
        for offset, binding in enumerate(value.bindings, start=1):
            tennis = tennis_cursor(
                state,
                binding.canonical_match_id,
            ).tennis
            assert tennis is not None
            event = point_event(
                tennis,
                received_monotonic_ns=200 + offset,
                event_id=f"multi-point-{offset}",
            )
            transition = synchronize(
                state,
                tennis_input(
                    binding.canonical_match_id,
                    tennis,
                    event,
                ),
                now=observation(210 + offset),
            )
            self.assertEqual(
                tuple(result.ticker for result in transition.results),
                tuple(
                    sorted(
                        (
                            binding.home_market_ticker,
                            binding.away_market_ticker,
                        )
                    )
                ),
            )
            observed_sequences.extend(
                result.snapshot.decision_sequence
                for result in transition.results
                if result.snapshot is not None
            )
            state = transition.state
        self.assertEqual(
            observed_sequences,
            list(
                range(
                    observed_sequences[0],
                    observed_sequences[0]
                    + len(observed_sequences),
                )
            ),
        )

    def test_market_lifecycle_only_change_does_not_emit(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        lifecycle = market_lifecycle_event(
            cursor.book,
            observed_monotonic_ns=130,
        )
        transition = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                lifecycle,
            ),
            now=observation(131),
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.DUPLICATE_STATE_SUPPRESSED,
        )
        self.assertEqual(
            transition.state.decision_sequence,
            state.decision_sequence,
        )


class ExactBoundaryMatrixTests(unittest.TestCase):
    def test_each_age_maximum_passes_at_equality_and_fails_at_plus_one(
        self,
    ) -> None:
        cases = (
            (
                "max_score_age_ns",
                SyncReason.SCORE_STALE,
            ),
            (
                "max_book_age_ns",
                SyncReason.BOOK_STALE,
            ),
            (
                "max_lifecycle_age_ns",
                SyncReason.LIFECYCLE_STALE,
            ),
        )
        for field, expected in cases:
            changes = {
                "max_score_age_ns": 1_000,
                "max_book_age_ns": 1_000,
                "max_lifecycle_age_ns": 1_000,
                field: 10,
            }
            state, value = ready_session_with_policy(**changes)
            binding = value.bindings[0]
            ticker = binding.home_market_ticker
            equal = synchronize(
                state,
                clock_input(binding.canonical_match_id, ticker),
                now=observation(110),
            )
            with self.subTest(field=field, boundary="equal"):
                self.assertNotEqual(equal.results[0].reason, expected)
            above = synchronize(
                state,
                clock_input(binding.canonical_match_id, ticker),
                now=observation(111),
            )
            with self.subTest(field=field, boundary="plus_one"):
                self.assertEqual(above.results[0].reason, expected)

    def test_skew_and_all_uncertainties_use_inclusive_maximum(self) -> None:
        for book_time, expected in (
            (110, SyncReason.DUPLICATE_STATE_SUPPRESSED),
            (111, SyncReason.CLOCK_UNCERTAIN),
        ):
            state, value = ready_session_with_policy(
                book_observed_monotonic_ns=book_time,
                max_score_book_skew_ns=10,
            )
            binding = value.bindings[0]
            result = synchronize(
                state,
                clock_input(
                    binding.canonical_match_id,
                    binding.home_market_ticker,
                ),
                now=observation(book_time),
            ).results[0]
            with self.subTest(book_time=book_time):
                self.assertEqual(result.reason, expected)

        for source in ("now", "score", "book"):
            kwargs: dict[str, object] = {
                "max_clock_uncertainty_ns": 10,
            }
            if source == "score":
                kwargs["provider_clock_uncertainty_ns"] = 11
            if source == "book":
                kwargs["book_clock_uncertainty_ns"] = 11
            state, value = ready_session_with_policy(**kwargs)
            binding = value.bindings[0]
            result = synchronize(
                state,
                clock_input(
                    binding.canonical_match_id,
                    binding.home_market_ticker,
                ),
                now=observation(
                    100,
                    clock_uncertainty_ns=(
                        11 if source == "now" else 1
                    ),
                ),
            ).results[0]
            with self.subTest(source=source, boundary="plus_one"):
                self.assertEqual(result.reason, SyncReason.CLOCK_UNCERTAIN)
        equal_state, equal_value = ready_session_with_policy(
            provider_clock_uncertainty_ns=10,
            book_clock_uncertainty_ns=10,
            max_clock_uncertainty_ns=10,
        )
        binding = equal_value.bindings[0]
        equal = synchronize(
            equal_state,
            clock_input(
                binding.canonical_match_id,
                binding.home_market_ticker,
            ),
            now=observation(100, clock_uncertainty_ns=10),
        )
        self.assertNotEqual(
            equal.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )

    def test_now_before_each_domain_observation_is_clock_uncertain(self) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        origin = provider_origin(
            binding,
            received_monotonic_ns=120,
        )
        provider_early = synchronize(
            state,
            origin_input(origin, binding.canonical_match_id),
            now=observation(110),
        )
        self.assertEqual(
            tuple(result.reason for result in provider_early.results),
            (
                SyncReason.SNAPSHOT_INCOMPLETE,
                SyncReason.SNAPSHOT_INCOMPLETE,
            ),
        )
        state = provider_early.state
        snapshot = book_origin(
            binding.home_market_ticker,
            observed_monotonic_ns=130,
        )
        book_early = synchronize(
            state,
            initial_book_input(binding.canonical_match_id, snapshot),
            now=observation(111),
        )
        self.assertEqual(
            book_early.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )

    def test_close_is_strict_even_with_zero_horizon(self) -> None:
        state, value = ready_session_with_policy(
            minimum_close_horizon_ns=0,
        )
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        state = replace_book(
            state,
            binding.canonical_match_id,
            ticker,
            scheduled_close_wall_ns=START_WALL_NS,
        )
        transition = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(
                state.last_observation.monotonic_ns  # type: ignore[union-attr]
            ),
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.CLOSE_HORIZON_INSUFFICIENT,
        )


class BlockerPrecedenceMatrixTests(unittest.TestCase):
    def test_binding_drift_correction_pending_and_provider_gap_ownership(
        self,
    ) -> None:
        cases = (
            (
                {"provider_match_id": "different-provider-match"},
                SyncReason.BINDING_DRIFT,
            ),
            (
                {"server_before_point": PlayerSide.AWAY},
                SyncReason.CORRECTION_PENDING,
            ),
            (
                {"revision_delta": 2},
                SyncReason.SEQUENCE_GAP,
            ),
        )
        for changes, expected in cases:
            state, value = ready_session()
            binding = value.bindings[0]
            tennis = tennis_cursor(
                state,
                binding.canonical_match_id,
            ).tennis
            assert tennis is not None
            event = point_event(
                tennis,
                received_monotonic_ns=200,
                event_id=f"block-{expected.value}",
            )
            changes = dict(changes)
            revision_delta = changes.pop("revision_delta", None)
            if revision_delta is not None:
                changes["revision"] = tennis.revision + revision_delta
            event = replace(event, **changes)
            transition = synchronize(
                state,
                tennis_input(
                    binding.canonical_match_id,
                    tennis,
                    event,
                ),
                now=observation(201),
            )
            with self.subTest(expected=expected):
                self.assertEqual(
                    tuple(result.reason for result in transition.results),
                    (expected, expected),
                )

    def test_correction_pending_precedes_book_sequence_gap(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        gap = BookDelta(
            ticker=ticker,
            connection_epoch=cursor.book.connection_epoch,
            sequence=cursor.book.sequence + 2,
            source_wall_ns=START_WALL_NS,
            observed_monotonic_ns=150,
            clock_uncertainty_ns=1,
            contract_side=ContractSide.YES,
            price=Decimal("0.40"),
            quantity=Decimal("3"),
        )
        state = synchronize(
            state,
            book_input(binding.canonical_match_id, cursor.book, gap),
            now=observation(151),
        ).state
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        wrong_server = point_event(
            tennis,
            received_monotonic_ns=152,
            event_id="wrong-server-after-gap",
        )
        wrong_server = replace(
            wrong_server,
            server_before_point=PlayerSide.AWAY,
        )
        transition = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                wrong_server,
            ),
            now=observation(153),
        )
        selected = next(
            result
            for result in transition.results
            if result.ticker == ticker
        )
        self.assertEqual(
            selected.reason,
            SyncReason.CORRECTION_PENDING,
        )

    def test_incomplete_precedes_market_and_freshness_defects(self) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        snapshot = book_origin(
            binding.home_market_ticker,
            observed_monotonic_ns=1,
            market_status=MarketStatus.SUSPENDED,
        )
        transition = synchronize(
            state,
            initial_book_input(binding.canonical_match_id, snapshot),
            now=observation(2_000),
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.SNAPSHOT_INCOMPLETE,
        )

    def test_clock_and_stale_first_match_collisions(self) -> None:
        state, value = ready_session_with_policy(
            max_score_age_ns=10,
            max_book_age_ns=10,
            max_lifecycle_age_ns=10,
            max_clock_uncertainty_ns=10,
        )
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        uncertain = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(111, clock_uncertainty_ns=11),
        )
        self.assertEqual(
            uncertain.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )
        all_stale = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(111),
        )
        self.assertEqual(
            all_stale.results[0].reason,
            SyncReason.SCORE_STALE,
        )
        book_and_lifecycle, value = ready_session_with_policy(
            max_score_age_ns=1_000,
            max_book_age_ns=10,
            max_lifecycle_age_ns=10,
        )
        binding = value.bindings[0]
        result = synchronize(
            book_and_lifecycle,
            clock_input(
                binding.canonical_match_id,
                binding.home_market_ticker,
            ),
            now=observation(111),
        )
        self.assertEqual(result.results[0].reason, SyncReason.BOOK_STALE)

    def test_lifecycle_stale_precedes_empty_book(self) -> None:
        state, value = ready_session_with_policy(
            max_score_age_ns=1_000,
            max_book_age_ns=1_000,
            max_lifecycle_age_ns=10,
        )
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        state = replace_book(
            state,
            binding.canonical_match_id,
            ticker,
            yes_bids=(),
            no_bids=(),
        )
        transition = synchronize(
            state,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(111),
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.LIFECYCLE_STALE,
        )

    def test_close_horizon_precedes_pending_move(self) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        lifecycle = market_lifecycle_event(
            cursor.book,
            observed_monotonic_ns=202,
            scheduled_close_wall_ns=(
                START_WALL_NS
                + state.policy.minimum_close_horizon_ns
                - 1
            ),
        )
        transition = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                lifecycle,
            ),
            now=observation(203),
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.CLOSE_HORIZON_INSUFFICIENT,
        )


class PairedResetMatrixTests(unittest.TestCase):
    def _require_resnapshot(
        self,
        state: SynchronizationSessionState,
        match_id: str,
        ticker: str,
        *,
        now_ns: int,
    ) -> SynchronizationSessionState:
        cursor = book_cursor(state, match_id, ticker)
        assert cursor.book is not None
        required = require_book_resnapshot(cursor.book)
        evidence = SynchronizationInput(
            kind=SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
            canonical_match_id=match_id,
            ticker=ticker,
            previous_state_sha256=expert_contract_sha256(cursor.book),
            provider_event=None,
            tennis_transition=None,
            book_event=None,
            book_transition=None,
            book_resnapshot_state=required,
        )
        return synchronize(
            state,
            evidence,
            now=observation(now_ns),
        ).state

    def _correct(
        self,
        state: SynchronizationSessionState,
        match_id: str,
        *,
        receipt: int,
        now_ns: int,
    ) -> SynchronizationSessionState:
        tennis = tennis_cursor(state, match_id).tennis
        assert tennis is not None
        return synchronize(
            state,
            tennis_input(
                match_id,
                tennis,
                correction_event(
                    tennis,
                    received_monotonic_ns=receipt,
                ),
            ),
            now=observation(now_ns),
        ).state

    def test_correction_only_and_book_only_never_clear_pending(self) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        corrected = self._correct(
            state,
            binding.canonical_match_id,
            receipt=200,
            now_ns=201,
        )
        self.assertIsNotNone(
            book_cursor(
                corrected,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )

        book_only, value, ticker = (
            AdvancedCausalBarrierTests()._pending(200)
        )
        binding = value.bindings[0]
        book_only = self._require_resnapshot(
            book_only,
            binding.canonical_match_id,
            ticker,
            now_ns=201,
        )
        cursor = book_cursor(
            book_only,
            binding.canonical_match_id,
            ticker,
        )
        assert cursor.book is not None
        replacement = book_origin(
            ticker,
            connection_epoch=2,
            sequence=1,
            observed_monotonic_ns=200,
            yes_bids=cursor.book.yes_bids,
            no_bids=cursor.book.no_bids,
        )
        book_only = synchronize(
            book_only,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                replacement,
            ),
            now=observation(201),
        ).state
        self.assertIsNotNone(
            book_cursor(
                book_only,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )
        book_only = self._correct(
            book_only,
            binding.canonical_match_id,
            receipt=202,
            now_ns=202,
        )
        self.assertIsNotNone(
            book_cursor(
                book_only,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )

    def test_observation_equality_resets_before_replacement_move(self) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        state = self._correct(
            state,
            binding.canonical_match_id,
            receipt=200,
            now_ns=201,
        )
        state = self._require_resnapshot(
            state,
            binding.canonical_match_id,
            ticker,
            now_ns=201,
        )
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        replacement = book_origin(
            ticker,
            connection_epoch=2,
            sequence=1,
            observed_monotonic_ns=200,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                replacement,
            ),
            now=observation(201),
        ).state
        pending = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        ).pending_move
        assert pending is not None
        self.assertEqual(pending.first_connection_epoch, 2)
        self.assertEqual(pending.book_connection_epoch_floor, 2)
        self.assertEqual(pending.tennis_correction_epoch_floor, 1)
        self.assertEqual(pending.move_count, 1)

    def test_tiny_reset_clears_old_pending_and_preserves_consumed(self) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=150,
            event_id="reset-consumed-point",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            now=observation(201),
        ).state
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        consumed = cursor.consumed_point_witness
        assert consumed is not None
        assert cursor.book is not None
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                large_delta(
                    cursor.book,
                    observed_monotonic_ns=202,
                    restore=True,
                ),
            ),
            now=observation(203),
        ).state
        state = self._correct(
            state,
            binding.canonical_match_id,
            receipt=202,
            now_ns=203,
        )
        state = self._require_resnapshot(
            state,
            binding.canonical_match_id,
            ticker,
            now_ns=203,
        )
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        replacement = book_origin(
            ticker,
            connection_epoch=2,
            sequence=1,
            observed_monotonic_ns=202,
            yes_bids=cursor.book.yes_bids,
            no_bids=cursor.book.no_bids,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                replacement,
            ),
            now=observation(203),
        ).state
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        self.assertIsNone(cursor.pending_move)
        self.assertIsNone(cursor.causal_point_witness)
        self.assertEqual(cursor.consumed_point_witness, consumed)

    def test_none_floor_cannot_reset(self) -> None:
        value = universe()
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        snapshot = book_origin(
            ticker,
            observed_monotonic_ns=100,
        )
        state = synchronize(
            state,
            initial_book_input(binding.canonical_match_id, snapshot),
            now=observation(100),
        ).state
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                large_delta(
                    cursor.book,
                    observed_monotonic_ns=110,
                ),
            ),
            now=observation(111),
        ).state
        state = self._require_resnapshot(
            state,
            binding.canonical_match_id,
            ticker,
            now_ns=112,
        )
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        replacement = book_origin(
            ticker,
            connection_epoch=2,
            sequence=1,
            observed_monotonic_ns=110,
            yes_bids=cursor.book.yes_bids,
            no_bids=cursor.book.no_bids,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                replacement,
            ),
            now=observation(113),
        ).state
        pending = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        ).pending_move
        assert pending is not None
        self.assertIsNone(pending.tennis_correction_epoch_floor)


class MandatoryCompletenessMatrixTests(unittest.TestCase):
    def test_pending_survives_every_noncausal_book_and_tennis_update(
        self,
    ) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        original = canonical_expert_bytes(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )

        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        deep_quantity = BookDelta(
            ticker=ticker,
            connection_epoch=cursor.book.connection_epoch,
            sequence=cursor.book.sequence + 1,
            source_wall_ns=START_WALL_NS,
            observed_monotonic_ns=202,
            clock_uncertainty_ns=1,
            contract_side=ContractSide.YES,
            price=Decimal("0.35"),
            quantity=Decimal("2"),
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                deep_quantity,
            ),
            now=observation(203),
        ).state

        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        lifecycle = market_lifecycle_event(
            cursor.book,
            observed_monotonic_ns=204,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                lifecycle,
            ),
            now=observation(205),
        ).state

        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        suspended = provider_lifecycle_event(
            tennis,
            received_monotonic_ns=206,
            event_id="pending-noncausal-lifecycle",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                suspended,
            ),
            now=observation(207),
        ).state

        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        resumed = provider_lifecycle_event(
            tennis,
            received_monotonic_ns=208,
            event_id="pending-noncausal-resume",
            kind=ProviderLifecycleKind.RESUME,
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                resumed,
            ),
            now=observation(209),
        ).state

        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        late_point = point_event(
            tennis,
            received_monotonic_ns=201,
            event_id="pending-late-point",
        )
        state = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                late_point,
            ),
            now=observation(210),
        ).state
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        duplicate = tennis_input(
            binding.canonical_match_id,
            tennis,
            late_point,
        )
        state = synchronize(
            state,
            duplicate,
            now=observation(211),
        ).state

        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        gap = BookDelta(
            ticker=ticker,
            connection_epoch=cursor.book.connection_epoch,
            sequence=cursor.book.sequence + 2,
            source_wall_ns=START_WALL_NS,
            observed_monotonic_ns=212,
            clock_uncertainty_ns=1,
            contract_side=ContractSide.YES,
            price=Decimal("0.40"),
            quantity=Decimal("3"),
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                gap,
            ),
            now=observation(213),
        ).state

        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        resnapshot_state = require_book_resnapshot(cursor.book)
        resnapshot_evidence = SynchronizationInput(
            kind=SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
            canonical_match_id=binding.canonical_match_id,
            ticker=ticker,
            previous_state_sha256=expert_contract_sha256(cursor.book),
            provider_event=None,
            tennis_transition=None,
            book_event=None,
            book_transition=None,
            book_resnapshot_state=resnapshot_state,
        )
        state = synchronize(
            state,
            resnapshot_evidence,
            now=observation(214),
        ).state

        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        replacement = book_origin(
            ticker,
            connection_epoch=cursor.book.connection_epoch + 1,
            sequence=1,
            observed_monotonic_ns=215,
            yes_bids=cursor.book.yes_bids,
            no_bids=cursor.book.no_bids,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                replacement,
            ),
            now=observation(216),
        ).state

        pending = book_cursor(
            state,
            binding.canonical_match_id,
            ticker,
        ).pending_move
        self.assertEqual(canonical_expert_bytes(pending), original)
        self.assertEqual(
            canonical_expert_bytes(pending),
            canonical_expert_bytes(
                copy.deepcopy(pending)
            ),
        )

    def test_every_input_kind_is_embedded_and_duplicates_never_emit(
        self,
    ) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )

        def apply_and_assert(
            prior: SynchronizationSessionState,
            evidence: SynchronizationInput,
            now_ns: int,
        ) -> SynchronizationTransitionResult:
            now = observation(now_ns)
            transition = synchronize(prior, evidence, now=now)
            self.assertIs(transition.input, evidence)
            self.assertIs(transition.observation, now)
            self.assertEqual(
                transition.prior_session_sha256,
                expert_contract_sha256(prior),
            )
            self.assertEqual(
                transition.input_sha256,
                expert_contract_sha256(evidence),
            )
            return transition

        transition = apply_and_assert(
            state,
            origin_input(
                provider_origin(binding),
                binding.canonical_match_id,
            ),
            110,
        )
        state = transition.state
        transition = apply_and_assert(
            state,
            initial_book_input(
                binding.canonical_match_id,
                book_origin(binding.home_market_ticker),
            ),
            111,
        )
        state = transition.state

        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=112,
            event_id="embedded-point",
        )
        transition = apply_and_assert(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            112,
        )
        state = transition.state
        duplicate_sequence = state.decision_sequence
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        transition = apply_and_assert(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            113,
        )
        self.assertTrue(
            all(
                result.reason
                in {
                    SyncReason.DUPLICATE_STATE_SUPPRESSED,
                    SyncReason.SNAPSHOT_INCOMPLETE,
                }
                for result in transition.results
            )
        )
        self.assertEqual(
            transition.state.decision_sequence,
            duplicate_sequence,
        )
        state = transition.state

        cursor = book_cursor(
            state,
            binding.canonical_match_id,
            binding.home_market_ticker,
        )
        assert cursor.book is not None
        required = require_book_resnapshot(cursor.book)
        resnapshot_evidence = SynchronizationInput(
            kind=SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
            canonical_match_id=binding.canonical_match_id,
            ticker=binding.home_market_ticker,
            previous_state_sha256=expert_contract_sha256(cursor.book),
            provider_event=None,
            tennis_transition=None,
            book_event=None,
            book_transition=None,
            book_resnapshot_state=required,
        )
        transition = apply_and_assert(
            state,
            resnapshot_evidence,
            114,
        )
        state = transition.state
        transition = apply_and_assert(
            state,
            clock_input(
                binding.canonical_match_id,
                binding.home_market_ticker,
            ),
            115,
        )
        self.assertEqual(transition.input.kind, SyncInputKind.CLOCK)

    def test_isolated_clock_domains_and_lifecycle_uncertainty(self) -> None:
        value = universe()
        binding = value.bindings[0]

        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        state = synchronize(
            state,
            origin_input(
                provider_origin(
                    binding,
                    received_monotonic_ns=120,
                ),
                binding.canonical_match_id,
            ),
            now=observation(100),
        ).state
        score_future = synchronize(
            state,
            initial_book_input(
                binding.canonical_match_id,
                book_origin(
                    binding.home_market_ticker,
                    observed_monotonic_ns=100,
                ),
            ),
            now=observation(101),
        )
        self.assertEqual(
            score_future.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )

        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        state = synchronize(
            state,
            origin_input(
                provider_origin(
                    binding,
                    received_monotonic_ns=100,
                ),
                binding.canonical_match_id,
            ),
            now=observation(100),
        ).state
        book_future = synchronize(
            state,
            initial_book_input(
                binding.canonical_match_id,
                book_origin(
                    binding.home_market_ticker,
                    observed_monotonic_ns=120,
                ),
            ),
            now=observation(101),
        )
        self.assertEqual(
            book_future.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )

        state, value = ready_session_with_policy(
            max_clock_uncertainty_ns=10,
        )
        binding = value.bindings[0]
        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        uncertain_lifecycle = replace(
            market_lifecycle_event(
                cursor.book,
                observed_monotonic_ns=101,
            ),
            clock_uncertainty_ns=11,
        )
        lifecycle_result = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                uncertain_lifecycle,
            ),
            now=observation(102),
        )
        self.assertEqual(
            lifecycle_result.results[0].reason,
            SyncReason.CLOCK_UNCERTAIN,
        )

    def test_later_blockers_never_outrank_binding_gap_or_market_state(
        self,
    ) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(
            value,
            policy(value, max_clock_uncertainty_ns=10),
        )
        state = synchronize(
            state,
            origin_input(
                provider_origin(
                    binding,
                    status=MatchStatus.SCHEDULED,
                ),
                binding.canonical_match_id,
            ),
            now=observation(100),
        ).state
        snapshot = book_origin(
            binding.home_market_ticker,
            market_status=MarketStatus.SUSPENDED,
        )
        state = synchronize(
            state,
            initial_book_input(
                binding.canonical_match_id,
                snapshot,
            ),
            now=observation(101),
        ).state
        cursor = book_cursor(
            state,
            binding.canonical_match_id,
            binding.home_market_ticker,
        )
        assert cursor.book is not None
        gap = BookDelta(
            ticker=cursor.ticker,
            connection_epoch=cursor.book.connection_epoch,
            sequence=cursor.book.sequence + 2,
            source_wall_ns=START_WALL_NS,
            observed_monotonic_ns=102,
            clock_uncertainty_ns=11,
            contract_side=ContractSide.YES,
            price=Decimal("0.40"),
            quantity=Decimal("2"),
        )
        transition = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                gap,
            ),
            now=observation(103, clock_uncertainty_ns=11),
        )
        self.assertEqual(
            transition.results[0].reason,
            SyncReason.SEQUENCE_GAP,
        )

        for market_status, expected in (
            (MarketStatus.SUSPENDED, SyncReason.MARKET_SUSPENDED),
            (MarketStatus.PREOPEN, SyncReason.MARKET_NOT_OPEN),
            (MarketStatus.CLOSED, SyncReason.MARKET_ENDED),
        ):
            state = synchronization_session_from_artifacts(
                value,
                policy(value, max_clock_uncertainty_ns=10),
            )
            state = synchronize(
                state,
                origin_input(
                    provider_origin(binding),
                    binding.canonical_match_id,
                ),
                now=observation(100),
            ).state
            transition = synchronize(
                state,
                initial_book_input(
                    binding.canonical_match_id,
                    book_origin(
                        binding.home_market_ticker,
                        market_status=market_status,
                        observed_monotonic_ns=1,
                    ),
                ),
                now=observation(
                    2_000,
                    clock_uncertainty_ns=11,
                ),
            )
            with self.subTest(market_status=market_status):
                self.assertEqual(
                    transition.results[0].reason,
                    expected,
                )

        pending, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        prior_sequence = pending.decision_sequence
        result = synchronize(
            pending,
            clock_input(binding.canonical_match_id, ticker),
            now=observation(201),
        )
        self.assertEqual(
            result.results[0].reason,
            SyncReason.UNEXPLAINED_BOOK_MOVE,
        )
        self.assertEqual(result.state.decision_sequence, prior_sequence)

    def test_tennis_one_before_prevents_paired_reset(self) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        state = PairedResetMatrixTests()._correct(
            state,
            binding.canonical_match_id,
            receipt=199,
            now_ns=201,
        )
        state = PairedResetMatrixTests()._require_resnapshot(
            state,
            binding.canonical_match_id,
            ticker,
            now_ns=202,
        )
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        replacement = book_origin(
            ticker,
            connection_epoch=cursor.book.connection_epoch + 1,
            sequence=1,
            observed_monotonic_ns=200,
            yes_bids=cursor.book.yes_bids,
            no_bids=cursor.book.no_bids,
        )
        state = synchronize(
            state,
            book_input(
                binding.canonical_match_id,
                cursor.book,
                replacement,
            ),
            now=observation(203),
        ).state
        self.assertIsNotNone(
            book_cursor(
                state,
                binding.canonical_match_id,
                ticker,
            ).pending_move
        )

    def test_full_stream_and_distinct_clock_determinism(self) -> None:
        value = universe()
        frozen_policy = policy(value)
        binding = value.bindings[0]

        def reduce_stream() -> tuple[
            tuple[bytes, ...],
            bytes,
            tuple[str, ...],
            tuple[str, ...],
        ]:
            state = synchronization_session_from_artifacts(
                value,
                frozen_policy,
            )
            transitions = []
            evidence = origin_input(
                provider_origin(binding),
                binding.canonical_match_id,
            )
            transition = synchronize(
                state,
                evidence,
                now=observation(110),
            )
            transitions.append(transition)
            state = transition.state
            for now_ns, ticker in enumerate(
                sorted(
                    (
                        binding.home_market_ticker,
                        binding.away_market_ticker,
                    )
                ),
                start=111,
            ):
                transition = synchronize(
                    state,
                    initial_book_input(
                        binding.canonical_match_id,
                        book_origin(ticker),
                    ),
                    now=observation(now_ns),
                )
                transitions.append(transition)
                state = transition.state
            tennis = tennis_cursor(
                state,
                binding.canonical_match_id,
            ).tennis
            assert tennis is not None
            transition = synchronize(
                state,
                tennis_input(
                    binding.canonical_match_id,
                    tennis,
                    point_event(
                        tennis,
                        received_monotonic_ns=113,
                        event_id="deterministic-stream-point",
                    ),
                ),
                now=observation(113),
            )
            transitions.append(transition)
            state = transition.state
            opportunities = tuple(
                result.opportunity.opportunity_id
                for item in transitions
                for result in item.results
                if result.opportunity is not None
            )
            return (
                tuple(
                    canonical_expert_bytes(item)
                    for item in transitions
                ),
                canonical_expert_bytes(state),
                opportunities,
                tuple(
                    expert_contract_sha256(item)
                    for item in transitions
                ),
            )

        expected = reduce_stream()
        for _ in range(1_000):
            self.assertEqual(reduce_stream(), expected)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_DOWN
            context.traps[InvalidOperation] = False
            context.traps[DivisionByZero] = False
            context.traps[Overflow] = False
            self.assertEqual(reduce_stream(), expected)

        value = universe()
        long_policy = policy(
            value,
            max_score_age_ns=10_000,
            max_book_age_ns=10_000,
            max_lifecycle_age_ns=10_000,
        )
        state = synchronization_session_from_artifacts(
            value,
            long_policy,
        )
        binding = value.bindings[0]
        state = synchronize(
            state,
            origin_input(
                provider_origin(binding),
                binding.canonical_match_id,
            ),
            now=observation(100),
        ).state
        for ticker in sorted(
            (
                binding.home_market_ticker,
                binding.away_market_ticker,
            )
        ):
            state = synchronize(
                state,
                initial_book_input(
                    binding.canonical_match_id,
                    book_origin(
                        ticker,
                        observed_monotonic_ns=100,
                    ),
                ),
                now=observation(100),
            ).state
        original_sequence = state.decision_sequence
        original_ids = tuple(
            cursor.last_emission.fingerprint_sha256
            for cursor in state.book_cursors
            if cursor.last_emission is not None
        )
        for offset in range(1, 1_001):
            transition = synchronize(
                state,
                clock_input(
                    binding.canonical_match_id,
                    binding.home_market_ticker,
                ),
                now=observation(100 + offset),
            )
            self.assertEqual(
                transition.results[0].reason,
                SyncReason.DUPLICATE_STATE_SUPPRESSED,
            )
            self.assertIsNone(transition.results[0].opportunity)
            state = transition.state
        self.assertEqual(state.decision_sequence, original_sequence)
        self.assertEqual(
            tuple(
                cursor.last_emission.fingerprint_sha256
                for cursor in state.book_cursors
                if cursor.last_emission is not None
            ),
            original_ids,
        )


class AdversarialStateGraphTests(unittest.TestCase):
    def test_none_observation_is_reserved_for_exact_empty_genesis(self) -> None:
        state, _ = ready_session()
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_session_cursors$",
        ):
            replace(state, last_observation=None)
        hostile = copy.deepcopy(state)
        assert hostile.last_observation is not None
        object.__setattr__(
            hostile.last_observation,
            "wall_ns",
            -1,
        )
        with self.assertRaises(SynchronizationSessionDriftError):
            assert_synchronization_session_compatible(
                hostile,
                hostile.universe,
                hostile.policy,
            )

    def test_corrupted_nested_cursor_intrinsics_are_global_drift(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        for cursor_group, field in (
            ("tennis", "last_state_sha256"),
            ("book", "last_state_sha256"),
        ):
            hostile = copy.deepcopy(state)
            selected = (
                hostile.tennis_cursors[0]
                if cursor_group == "tennis"
                else hostile.book_cursors[0]
            )
            object.__setattr__(selected, field, SHA_A)
            with self.subTest(cursor_group=cursor_group):
                with self.assertRaises(
                    SynchronizationSessionDriftError
                ):
                    assert_synchronization_session_compatible(
                        hostile,
                        hostile.universe,
                        hostile.policy,
                    )
                with self.assertRaises(
                    SynchronizationSessionDriftError
                ):
                    synchronize(
                        hostile,
                        clock_input(
                            binding.canonical_match_id,
                            binding.home_market_ticker,
                        ),
                        now=hostile.last_observation,
                    )

    def test_unsafe_mutated_nested_evidence_is_global_drift(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=200,
            event_id="unsafe-provider-point",
        )
        tennis_evidence = tennis_input(
            binding.canonical_match_id,
            tennis,
            point,
        )
        object.__setattr__(point, "point_winner", "home")
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                tennis_evidence,
                now=observation(201),
            )

        ticker = binding.home_market_ticker
        cursor = book_cursor(state, binding.canonical_match_id, ticker)
        assert cursor.book is not None
        delta = tiny_delta(
            cursor.book,
            observed_monotonic_ns=200,
        )
        book_evidence = book_input(
            binding.canonical_match_id,
            cursor.book,
            delta,
        )
        object.__setattr__(delta, "contract_side", "yes")
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                state,
                book_evidence,
                now=observation(201),
            )

        value = universe()
        binding = value.bindings[0]
        empty = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        origin = provider_origin(binding)
        origin_evidence = origin_input(
            origin,
            binding.canonical_match_id,
        )
        object.__setattr__(origin, "status", "live")
        with self.assertRaises(SynchronizationSessionDriftError):
            synchronize(
                empty,
                origin_evidence,
                now=observation(110),
            )

    def test_transition_rejects_sequence_jump_and_missing_emission(self) -> None:
        state, value = ready_session()
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        event = point_event(
            tennis,
            received_monotonic_ns=200,
            event_id="transition-graph-point",
        )
        transition = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                event,
            ),
            now=observation(201),
        )
        jumped = replace(
            transition.state,
            decision_sequence=transition.state.decision_sequence + 5,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, state=jumped)

        emitted_ticker = transition.results[0].ticker
        cursors = tuple(
            replace(cursor, last_emission=None)
            if (
                cursor.canonical_match_id
                == binding.canonical_match_id
                and cursor.ticker == emitted_ticker
            )
            else cursor
            for cursor in transition.state.book_cursors
        )
        missing = replace(
            transition.state,
            book_cursors=cursors,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, state=missing)

        corrupt_reason = copy.deepcopy(transition.results[0])
        object.__setattr__(corrupt_reason, "reason", "garbage")
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(
                transition,
                results=(
                    corrupt_reason,
                    *transition.results[1:],
                ),
            )

        trusted_index = next(
            index
            for index, result in enumerate(transition.results)
            if result.reason is SyncReason.TRUSTED_SYNCHRONIZED
        )
        trusted = transition.results[trusted_index]
        assert trusted.snapshot is not None
        shifted_time = replace(
            trusted.snapshot.decision_time,
            wall_ns=trusted.snapshot.decision_time.wall_ns + 1,
            monotonic_ns=(
                trusted.snapshot.decision_time.monotonic_ns + 1
            ),
        )
        shifted_snapshot = replace(
            trusted.snapshot,
            decision_time=shifted_time,
        )
        shifted_result = SyncResult(
            trusted.canonical_match_id,
            trusted.ticker,
            shifted_snapshot,
            opportunity_for_snapshot(
                transition.state,
                shifted_snapshot,
            ),
            SyncReason.TRUSTED_SYNCHRONIZED,
        )
        shifted_results = list(transition.results)
        shifted_results[trusted_index] = shifted_result
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(
                transition,
                results=tuple(shifted_results),
            )
        corrupt_opportunity = copy.deepcopy(transition.results[0])
        assert corrupt_opportunity.opportunity is not None
        object.__setattr__(
            corrupt_opportunity.opportunity,
            "opportunity_id",
            SHA_A,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(
                transition,
                results=(
                    corrupt_opportunity,
                    *transition.results[1:],
                ),
            )

    def test_tennis_transition_rejects_arbitrary_sorted_tickers(self) -> None:
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(
            value,
            policy(value),
        )
        transition = synchronize(
            state,
            origin_input(
                provider_origin(binding),
                binding.canonical_match_id,
            ),
            now=observation(110),
        )
        hostile_results = (
            SyncResult(
                binding.canonical_match_id,
                "AAA",
                None,
                None,
                SyncReason.SNAPSHOT_INCOMPLETE,
            ),
            SyncResult(
                binding.canonical_match_id,
                "BBB",
                None,
                None,
                SyncReason.SNAPSHOT_INCOMPLETE,
            ),
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, results=hostile_results)

    def test_trusted_transition_binds_universe_causal_and_fingerprint(
        self,
    ) -> None:
        state, value, ticker = AdvancedCausalBarrierTests()._pending(200)
        binding = value.bindings[0]
        tennis = tennis_cursor(state, binding.canonical_match_id).tennis
        assert tennis is not None
        point = point_event(
            tennis,
            received_monotonic_ns=150,
            event_id="graph-causal-point",
        )
        transition = synchronize(
            state,
            tennis_input(
                binding.canonical_match_id,
                tennis,
                point,
            ),
            now=observation(202),
        )
        result_index = next(
            index
            for index, result in enumerate(transition.results)
            if result.ticker == ticker
        )
        result = transition.results[result_index]
        assert result.snapshot is not None
        assert result.opportunity is not None

        wrong_binding = replace(
            result.snapshot.binding,
            binding_artifact_sha256=SHA_A,
        )
        wrong_snapshot = replace(
            result.snapshot,
            binding=wrong_binding,
        )
        wrong_result = SyncResult(
            result.canonical_match_id,
            result.ticker,
            wrong_snapshot,
            opportunity_for_snapshot(
                transition.state,
                wrong_snapshot,
            ),
            SyncReason.TRUSTED_SYNCHRONIZED,
        )
        wrong_results = list(transition.results)
        wrong_results[result_index] = wrong_result
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, results=tuple(wrong_results))

        wrong_causal = replace(
            result.snapshot,
            causal_provider_revision=None,
        )
        causal_result = SyncResult(
            result.canonical_match_id,
            result.ticker,
            wrong_causal,
            opportunity_for_snapshot(
                transition.state,
                wrong_causal,
            ),
            SyncReason.TRUSTED_SYNCHRONIZED,
        )
        wrong_results[result_index] = causal_result
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, results=tuple(wrong_results))

        mutated_cursors = tuple(
            replace(
                cursor,
                last_emission=replace(
                    cursor.last_emission,
                    fingerprint_sha256=SHA_A,
                ),
            )
            if (
                cursor.canonical_match_id
                == binding.canonical_match_id
                and cursor.ticker == ticker
            )
            else cursor
            for cursor in transition.state.book_cursors
        )
        wrong_state = replace(
            transition.state,
            book_cursors=mutated_cursors,
        )
        with self.assertRaisesRegex(
            ExpertContractError,
            "^synchronization_transition_results$",
        ):
            replace(transition, state=wrong_state)


if __name__ == "__main__":
    unittest.main()
