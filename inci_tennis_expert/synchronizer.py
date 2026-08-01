from __future__ import annotations

from decimal import Decimal
from hashlib import sha256

from .contracts import (
    BindingMetadata,
    BindingUniverse,
    BookDelta,
    BookEventKind,
    BookSnapshot,
    BookState,
    BookSyncCursor,
    BookTransitionResult,
    CausalPointWitness,
    LastSyncEmission,
    MarketLifecycle,
    MarketStatus,
    MatchBinding,
    MatchStatus,
    OpportunityFrame,
    PairedTimeObservation,
    PendingBookMove,
    ProviderLifecycle,
    ProviderPoint,
    ProviderSnapshot,
    SyncInputKind,
    SyncPolicy,
    SyncReason,
    SyncResult,
    SynchronizationInput,
    SynchronizationSessionState,
    SynchronizationTransitionResult,
    TennisSyncCursor,
    TennisTransitionReason,
    TransitionDisposition,
    TrustedSnapshot,
    _synchronization_emission_fingerprint,
    canonical_expert_bytes,
    expert_contract_sha256,
)
from .market_book import (
    apply_book_delta,
    apply_book_snapshot,
    apply_market_lifecycle,
    book_from_snapshot,
    require_book_resnapshot,
)
from .match_binding import (
    binding_metadata_for,
    binding_universe_sha256,
)
from .tennis_score import (
    apply_correction,
    apply_lifecycle,
    apply_point,
    state_from_snapshot,
    validate_tennis_state,
)


__all__ = (
    "SynchronizationSessionDriftError",
    "synchronization_session_from_artifacts",
    "assert_synchronization_session_compatible",
    "synchronize",
    "validate_synchronization_transition",
)


class SynchronizationSessionDriftError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("synchronization_session_drift")


def _drift() -> None:
    raise SynchronizationSessionDriftError()


def _tennis_cursor(
    prior: TennisSyncCursor,
    *,
    tennis: object,
    last_state_sha256: str | None,
    last_input_sha256: str | None,
    last_point_witness: CausalPointWitness | None,
) -> TennisSyncCursor:
    return TennisSyncCursor(
        canonical_match_id=prior.canonical_match_id,
        binding_sha256=prior.binding_sha256,
        binding_metadata_sha256=prior.binding_metadata_sha256,
        tennis=tennis,
        last_state_sha256=last_state_sha256,
        last_input_sha256=last_input_sha256,
        last_point_witness=last_point_witness,
    )


def _book_cursor(
    prior: BookSyncCursor,
    *,
    book: BookState | None,
    last_state_sha256: str | None,
    last_input_sha256: str | None,
    pending_move: PendingBookMove | None,
    causal_point_witness: CausalPointWitness | None,
    consumed_point_witness: CausalPointWitness | None,
    last_emission: LastSyncEmission | None,
) -> BookSyncCursor:
    return BookSyncCursor(
        canonical_match_id=prior.canonical_match_id,
        ticker=prior.ticker,
        binding_sha256=prior.binding_sha256,
        binding_metadata_sha256=prior.binding_metadata_sha256,
        book=book,
        last_state_sha256=last_state_sha256,
        last_input_sha256=last_input_sha256,
        pending_move=pending_move,
        causal_point_witness=causal_point_witness,
        consumed_point_witness=consumed_point_witness,
        last_emission=last_emission,
    )


def _session(
    prior: SynchronizationSessionState,
    *,
    decision_sequence: int,
    last_observation: PairedTimeObservation,
    tennis_cursors: tuple[TennisSyncCursor, ...],
    book_cursors: tuple[BookSyncCursor, ...],
) -> SynchronizationSessionState:
    return SynchronizationSessionState(
        universe=prior.universe,
        policy=prior.policy,
        universe_sha256=prior.universe_sha256,
        sync_policy_sha256=prior.sync_policy_sha256,
        decision_sequence=decision_sequence,
        last_observation=last_observation,
        tennis_cursors=tennis_cursors,
        book_cursors=book_cursors,
    )


def _with_observation(
    prior: SynchronizationSessionState,
    observation: PairedTimeObservation,
) -> SynchronizationSessionState:
    return _session(
        prior,
        decision_sequence=prior.decision_sequence,
        last_observation=observation,
        tennis_cursors=prior.tennis_cursors,
        book_cursors=prior.book_cursors,
    )


def _replace_tennis_cursor(
    state: SynchronizationSessionState,
    cursor: TennisSyncCursor,
) -> SynchronizationSessionState:
    cursors = tuple(
        cursor
        if item.canonical_match_id == cursor.canonical_match_id
        else item
        for item in state.tennis_cursors
    )
    return _session(
        state,
        decision_sequence=state.decision_sequence,
        last_observation=state.last_observation,
        tennis_cursors=cursors,
        book_cursors=state.book_cursors,
    )


def _replace_book_cursor(
    state: SynchronizationSessionState,
    cursor: BookSyncCursor,
) -> SynchronizationSessionState:
    cursors = tuple(
        cursor
        if (
            item.canonical_match_id == cursor.canonical_match_id
            and item.ticker == cursor.ticker
        )
        else item
        for item in state.book_cursors
    )
    return _session(
        state,
        decision_sequence=state.decision_sequence,
        last_observation=state.last_observation,
        tennis_cursors=state.tennis_cursors,
        book_cursors=cursors,
    )


def _binding(
    state: SynchronizationSessionState,
    canonical_match_id: str,
) -> tuple[MatchBinding, BindingMetadata]:
    matches = tuple(
        (binding, metadata)
        for binding, metadata in zip(
            state.universe.bindings,
            state.universe.metadata,
            strict=True,
        )
        if binding.canonical_match_id == canonical_match_id
    )
    if len(matches) != 1:
        _drift()
    return matches[0]


def _selected_tennis_cursor(
    state: SynchronizationSessionState,
    canonical_match_id: str,
) -> TennisSyncCursor:
    matches = tuple(
        cursor
        for cursor in state.tennis_cursors
        if cursor.canonical_match_id == canonical_match_id
    )
    if len(matches) != 1:
        _drift()
    return matches[0]


def _selected_book_cursor(
    state: SynchronizationSessionState,
    canonical_match_id: str,
    ticker: str,
) -> BookSyncCursor:
    matches = tuple(
        cursor
        for cursor in state.book_cursors
        if (
            cursor.canonical_match_id == canonical_match_id
            and cursor.ticker == ticker
        )
    )
    if len(matches) != 1:
        _drift()
    return matches[0]


def synchronization_session_from_artifacts(
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> SynchronizationSessionState:
    if type(universe) is not BindingUniverse:
        raise TypeError("universe")
    if type(policy) is not SyncPolicy:
        raise TypeError("policy")
    try:
        universe_sha256 = binding_universe_sha256(universe)
        SyncPolicy.__post_init__(policy)
    except (TypeError, ValueError):
        _drift()
    if policy.universe_sha256 != universe_sha256:
        _drift()
    provider_identities = {
        (
            binding.provider_source_id,
            binding.revision_domain_id,
            binding.source_lineage_sha256,
        )
        for binding in universe.bindings
    }
    if len(provider_identities) != 1:
        _drift()
    indexed = []
    for index, binding in enumerate(universe.bindings):
        try:
            metadata = binding_metadata_for(universe, binding)
        except (TypeError, ValueError):
            _drift()
        if type(metadata) is not BindingMetadata:
            _drift()
        if metadata != universe.metadata[index]:
            _drift()
        indexed.append((binding, metadata))
    tennis_cursors = tuple(
        TennisSyncCursor(
            canonical_match_id=binding.canonical_match_id,
            binding_sha256=expert_contract_sha256(binding),
            binding_metadata_sha256=expert_contract_sha256(metadata),
            tennis=None,
            last_state_sha256=None,
            last_input_sha256=None,
            last_point_witness=None,
        )
        for binding, metadata in sorted(
            indexed,
            key=lambda item: item[0].canonical_match_id,
        )
    )
    book_cursors = tuple(
        BookSyncCursor(
            canonical_match_id=binding.canonical_match_id,
            ticker=ticker,
            binding_sha256=expert_contract_sha256(binding),
            binding_metadata_sha256=expert_contract_sha256(metadata),
            book=None,
            last_state_sha256=None,
            last_input_sha256=None,
            pending_move=None,
            causal_point_witness=None,
            consumed_point_witness=None,
            last_emission=None,
        )
        for binding, metadata in indexed
        for ticker in (
            binding.home_market_ticker,
            binding.away_market_ticker,
        )
    )
    book_cursors = tuple(
        sorted(
            book_cursors,
            key=lambda item: (item.canonical_match_id, item.ticker),
        )
    )
    return SynchronizationSessionState(
        universe=universe,
        policy=policy,
        universe_sha256=universe_sha256,
        sync_policy_sha256=expert_contract_sha256(policy),
        decision_sequence=0,
        last_observation=None,
        tennis_cursors=tennis_cursors,
        book_cursors=book_cursors,
    )


def assert_synchronization_session_compatible(
    state: SynchronizationSessionState,
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> None:
    if type(state) is not SynchronizationSessionState:
        raise TypeError("state")
    if type(universe) is not BindingUniverse:
        raise TypeError("universe")
    if type(policy) is not SyncPolicy:
        raise TypeError("policy")
    try:
        BindingUniverse.__post_init__(universe)
        SyncPolicy.__post_init__(policy)
        SynchronizationSessionState.__post_init__(state)
        computed = binding_universe_sha256(universe)
        if (
            state.universe != universe
            or state.policy != policy
            or state.universe_sha256 != computed
            or state.sync_policy_sha256
            != expert_contract_sha256(policy)
            or policy.universe_sha256 != computed
        ):
            _drift()
        for index, binding in enumerate(universe.bindings):
            metadata = binding_metadata_for(universe, binding)
            if metadata != universe.metadata[index]:
                _drift()
        for cursor in state.tennis_cursors:
            if cursor.tennis is not None:
                validate_tennis_state(cursor.tennis)
        for cursor in state.book_cursors:
            if cursor.book is not None:
                BookState.__post_init__(cursor.book)
    except SynchronizationSessionDriftError:
        raise
    except (TypeError, ValueError, RuntimeError):
        _drift()


def _point_identity(
    witness: CausalPointWitness,
) -> tuple[str, int, int, str]:
    return (
        witness.canonical_match_id,
        witness.correction_epoch,
        witness.revision,
        witness.event_semantic_sha256,
    )


def _explain_pending(
    cursor: BookSyncCursor,
    witness: CausalPointWitness | None,
    tennis: object,
    policy: SyncPolicy,
) -> BookSyncCursor:
    pending = cursor.pending_move
    if pending is None or witness is None:
        return cursor
    if (
        pending.tennis_correction_epoch_floor is None
        or witness.canonical_match_id != pending.canonical_match_id
        or witness.correction_epoch != tennis.correction_epoch
        or (
            cursor.consumed_point_witness is not None
            and _point_identity(witness)
            == _point_identity(cursor.consumed_point_witness)
        )
        or witness.received_monotonic_ns
        > pending.first_move_monotonic_ns
        or pending.last_move_monotonic_ns
        - witness.received_monotonic_ns
        > policy.explanation_window_ns
    ):
        return cursor
    return _book_cursor(
        cursor,
        book=cursor.book,
        last_state_sha256=cursor.last_state_sha256,
        last_input_sha256=cursor.last_input_sha256,
        pending_move=None,
        causal_point_witness=witness,
        consumed_point_witness=witness,
        last_emission=cursor.last_emission,
    )


def _binding_drift(
    tennis: object,
    binding: MatchBinding,
) -> bool:
    return (
        tennis.provider_match_id != binding.provider_match_id
        or tennis.home_player_id != binding.provider_home_player_id
        or tennis.away_player_id != binding.provider_away_player_id
        or tennis.provider_source_id != binding.provider_source_id
        or tennis.revision_domain_id != binding.revision_domain_id
        or tennis.source_lineage_sha256
        != binding.source_lineage_sha256
        or tennis.match_format is not binding.match_format
        or tennis.scheduled_start_wall_ns
        != binding.scheduled_start_wall_ns
    )


def _fingerprint(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    tennis: object,
    book: BookState,
) -> str:
    return _synchronization_emission_fingerprint(
        universe_sha256=state.universe_sha256,
        sync_policy_sha256=state.sync_policy_sha256,
        binding=binding,
        binding_metadata=metadata,
        tennis=tennis,
        book=book,
    )


def _opportunity(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    snapshot: TrustedSnapshot,
) -> OpportunityFrame:
    binding_sha256 = expert_contract_sha256(binding)
    snapshot_sha256 = expert_contract_sha256(snapshot)
    identity = {
        "universe_sha256": state.universe_sha256,
        "canonical_match_id": binding.canonical_match_id,
        "ticker": snapshot.book.ticker,
        "decision_sequence": snapshot.decision_sequence,
        "decision_time": snapshot.decision_time,
        "binding_sha256": binding_sha256,
        "provider_revision": snapshot.tennis.revision,
        "book_connection_epoch": snapshot.book.connection_epoch,
        "book_sequence": snapshot.book.sequence,
        "snapshot_sha256": snapshot_sha256,
    }
    opportunity_id = sha256(
        b"INCI-OPPORTUNITY-ID-V1\0"
        + canonical_expert_bytes(identity)
    ).hexdigest()
    return OpportunityFrame(
        opportunity_id=opportunity_id,
        universe_sha256=state.universe_sha256,
        canonical_match_id=binding.canonical_match_id,
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


_BINDING_BLOCK_REASONS = frozenset(
    {
        TennisTransitionReason.IDENTITY_MISMATCH,
        TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
        TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
        TennisTransitionReason.FORMAT_MISMATCH,
    }
)


def _block_reason(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    tennis_cursor: TennisSyncCursor,
    book_cursor: BookSyncCursor,
    observation: PairedTimeObservation,
) -> SyncReason | None:
    tennis = tennis_cursor.tennis
    book = book_cursor.book
    if tennis is not None and (
        _binding_drift(tennis, binding)
        or tennis.block_reason in _BINDING_BLOCK_REASONS
    ):
        return SyncReason.BINDING_DRIFT
    if tennis is None or book is None:
        return SyncReason.SNAPSHOT_INCOMPLETE
    validate_tennis_state(tennis)
    if (
        tennis.block_reason is not None
        and tennis.block_reason
        not in _BINDING_BLOCK_REASONS
        and tennis.block_reason
        is not TennisTransitionReason.PROVIDER_EVENT_GAP
    ):
        return SyncReason.CORRECTION_PENDING
    if (
        tennis.block_reason
        is TennisTransitionReason.PROVIDER_EVENT_GAP
        or book.sequence_gap
    ):
        return SyncReason.SEQUENCE_GAP
    if not book.trusted:
        return SyncReason.BOOK_UNTRUSTED
    if tennis.status is MatchStatus.SCHEDULED:
        return SyncReason.MATCH_NOT_STARTED
    if tennis.status is MatchStatus.SUSPENDED:
        return SyncReason.MATCH_SUSPENDED
    if tennis.status in {MatchStatus.ENDED, MatchStatus.CANCELLED}:
        return SyncReason.MATCH_ENDED
    if book.market_status is MarketStatus.SUSPENDED:
        return SyncReason.MARKET_SUSPENDED
    if book.market_status is MarketStatus.PREOPEN:
        return SyncReason.MARKET_NOT_OPEN
    if book.market_status in {
        MarketStatus.CLOSED,
        MarketStatus.SETTLED,
        MarketStatus.CANCELLED,
    }:
        return SyncReason.MARKET_ENDED
    if tennis.server_for_next_point is None:
        return SyncReason.UNKNOWN_SERVER
    policy = state.policy
    if (
        observation.monotonic_ns
        < tennis.last_received_monotonic_ns
        or observation.monotonic_ns
        < book.book_observed_monotonic_ns
        or observation.monotonic_ns
        < book.lifecycle_observed_monotonic_ns
        or observation.clock_uncertainty_ns
        > policy.max_clock_uncertainty_ns
        or tennis.last_clock_uncertainty_ns
        > policy.max_clock_uncertainty_ns
        or book.book_clock_uncertainty_ns
        > policy.max_clock_uncertainty_ns
        or book.lifecycle_clock_uncertainty_ns
        > policy.max_clock_uncertainty_ns
        or abs(
            tennis.last_received_monotonic_ns
            - book.book_observed_monotonic_ns
        )
        > policy.max_score_book_skew_ns
    ):
        return SyncReason.CLOCK_UNCERTAIN
    if (
        observation.monotonic_ns
        - tennis.last_received_monotonic_ns
        > policy.max_score_age_ns
    ):
        return SyncReason.SCORE_STALE
    if (
        observation.monotonic_ns
        - book.book_observed_monotonic_ns
        > policy.max_book_age_ns
    ):
        return SyncReason.BOOK_STALE
    if (
        observation.monotonic_ns
        - book.lifecycle_observed_monotonic_ns
        > policy.max_lifecycle_age_ns
    ):
        return SyncReason.LIFECYCLE_STALE
    if not book.yes_bids and not book.no_bids:
        return SyncReason.BOOK_NOT_EXECUTABLE
    horizon = book.scheduled_close_wall_ns - observation.wall_ns
    if (
        observation.wall_ns >= book.scheduled_close_wall_ns
        or horizon < policy.minimum_close_horizon_ns
    ):
        return SyncReason.CLOSE_HORIZON_INSUFFICIENT
    if book_cursor.pending_move is not None:
        return SyncReason.UNEXPLAINED_BOOK_MOVE
    return None


def _evaluate(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    ticker: str,
    observation: PairedTimeObservation,
) -> tuple[SynchronizationSessionState, SyncResult]:
    tennis_cursor = _selected_tennis_cursor(
        state,
        binding.canonical_match_id,
    )
    book_cursor = _selected_book_cursor(
        state,
        binding.canonical_match_id,
        ticker,
    )
    reason = _block_reason(
        state,
        binding,
        tennis_cursor,
        book_cursor,
        observation,
    )
    if reason is not None:
        return state, SyncResult(
            binding.canonical_match_id,
            ticker,
            None,
            None,
            reason,
        )
    tennis = tennis_cursor.tennis
    book = book_cursor.book
    assert tennis is not None and book is not None
    fingerprint = _fingerprint(
        state,
        binding,
        metadata,
        tennis,
        book,
    )
    prior_emission = book_cursor.last_emission
    provider_identity = (
        tennis.correction_epoch,
        tennis.revision,
        tennis.last_event_semantic_sha256,
    )
    book_identity = (book.connection_epoch, book.sequence)
    if prior_emission is not None:
        prior_provider_identity = (
            prior_emission.provider_correction_epoch,
            prior_emission.provider_revision,
            prior_emission.provider_event_semantic_sha256,
        )
        prior_book_identity = (
            prior_emission.book_connection_epoch,
            prior_emission.book_sequence,
        )
        if (
            provider_identity == prior_provider_identity
            and book_identity == prior_book_identity
        ):
            return state, SyncResult(
                binding.canonical_match_id,
                ticker,
                None,
                None,
                SyncReason.DUPLICATE_STATE_SUPPRESSED,
            )
        if fingerprint == prior_emission.fingerprint_sha256:
            _drift()
    decision_sequence = state.decision_sequence + 1
    causal_revision = (
        None
        if book_cursor.causal_point_witness is None
        else book_cursor.causal_point_witness.revision
    )
    snapshot = TrustedSnapshot(
        decision_sequence=decision_sequence,
        decision_time=observation,
        tennis=tennis,
        book=book,
        binding=binding,
        sync_policy_sha256=state.sync_policy_sha256,
        causal_provider_revision=causal_revision,
    )
    opportunity = _opportunity(state, binding, snapshot)
    emission = LastSyncEmission(
        fingerprint_sha256=fingerprint,
        provider_correction_epoch=tennis.correction_epoch,
        provider_revision=tennis.revision,
        provider_event_semantic_sha256=(
            tennis.last_event_semantic_sha256
        ),
        book_connection_epoch=book.connection_epoch,
        book_sequence=book.sequence,
    )
    emitted_cursor = _book_cursor(
        book_cursor,
        book=book_cursor.book,
        last_state_sha256=book_cursor.last_state_sha256,
        last_input_sha256=book_cursor.last_input_sha256,
        pending_move=book_cursor.pending_move,
        causal_point_witness=book_cursor.causal_point_witness,
        consumed_point_witness=book_cursor.consumed_point_witness,
        last_emission=emission,
    )
    state = _session(
        state,
        decision_sequence=decision_sequence,
        last_observation=state.last_observation,
        tennis_cursors=state.tennis_cursors,
        book_cursors=tuple(
            emitted_cursor
            if (
                cursor.canonical_match_id
                == emitted_cursor.canonical_match_id
                and cursor.ticker == emitted_cursor.ticker
            )
            else cursor
            for cursor in state.book_cursors
        ),
    )
    return state, SyncResult(
        binding.canonical_match_id,
        ticker,
        snapshot,
        opportunity,
        SyncReason.TRUSTED_SYNCHRONIZED,
    )


def _results_for_tickers(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    tickers: tuple[str, ...],
    observation: PairedTimeObservation,
) -> tuple[SynchronizationSessionState, tuple[SyncResult, ...]]:
    results = []
    for ticker in tickers:
        state, result = _evaluate(
            state,
            binding,
            metadata,
            ticker,
            observation,
        )
        results.append(result)
    return state, tuple(results)


def _transition(
    prior: SynchronizationSessionState,
    state: SynchronizationSessionState,
    evidence: SynchronizationInput,
    observation: PairedTimeObservation,
    results: tuple[SyncResult, ...],
) -> SynchronizationTransitionResult:
    return SynchronizationTransitionResult(
        state=state,
        input=evidence,
        input_sha256=expert_contract_sha256(evidence),
        prior_session_sha256=expert_contract_sha256(prior),
        prior_decision_sequence=prior.decision_sequence,
        observation=observation,
        results=results,
    )


def _pending_with_floor(
    pending: PendingBookMove,
    correction_epoch: int,
) -> PendingBookMove:
    return PendingBookMove(
        canonical_match_id=pending.canonical_match_id,
        ticker=pending.ticker,
        first_move_monotonic_ns=pending.first_move_monotonic_ns,
        last_move_monotonic_ns=pending.last_move_monotonic_ns,
        first_connection_epoch=pending.first_connection_epoch,
        first_sequence=pending.first_sequence,
        first_event_sha256=pending.first_event_sha256,
        last_connection_epoch=pending.last_connection_epoch,
        last_sequence=pending.last_sequence,
        last_event_sha256=pending.last_event_sha256,
        move_count=pending.move_count,
        max_magnitude=pending.max_magnitude,
        tennis_correction_epoch_floor=correction_epoch,
        book_connection_epoch_floor=(
            pending.book_connection_epoch_floor
        ),
    )


def _new_pending(
    cursor: BookSyncCursor,
    transition: BookTransitionResult,
    tennis: object | None,
) -> PendingBookMove:
    assert transition.move_observed_monotonic_ns is not None
    assert transition.accepted_event_sha256 is not None
    return PendingBookMove(
        canonical_match_id=cursor.canonical_match_id,
        ticker=cursor.ticker,
        first_move_monotonic_ns=(
            transition.move_observed_monotonic_ns
        ),
        last_move_monotonic_ns=(
            transition.move_observed_monotonic_ns
        ),
        first_connection_epoch=transition.connection_epoch,
        first_sequence=transition.sequence,
        first_event_sha256=transition.accepted_event_sha256,
        last_connection_epoch=transition.connection_epoch,
        last_sequence=transition.sequence,
        last_event_sha256=transition.accepted_event_sha256,
        move_count=1,
        max_magnitude=transition.executable_move,
        tennis_correction_epoch_floor=(
            None if tennis is None else tennis.correction_epoch
        ),
        book_connection_epoch_floor=transition.connection_epoch,
    )


def _extended_pending(
    pending: PendingBookMove,
    transition: BookTransitionResult,
) -> PendingBookMove:
    assert transition.move_observed_monotonic_ns is not None
    assert transition.accepted_event_sha256 is not None
    return PendingBookMove(
        canonical_match_id=pending.canonical_match_id,
        ticker=pending.ticker,
        first_move_monotonic_ns=pending.first_move_monotonic_ns,
        last_move_monotonic_ns=(
            transition.move_observed_monotonic_ns
        ),
        first_connection_epoch=pending.first_connection_epoch,
        first_sequence=pending.first_sequence,
        first_event_sha256=pending.first_event_sha256,
        last_connection_epoch=transition.connection_epoch,
        last_sequence=transition.sequence,
        last_event_sha256=transition.accepted_event_sha256,
        move_count=pending.move_count + 1,
        max_magnitude=max(
            pending.max_magnitude,
            transition.executable_move,
        ),
        tennis_correction_epoch_floor=(
            pending.tennis_correction_epoch_floor
        ),
        book_connection_epoch_floor=(
            pending.book_connection_epoch_floor
        ),
    )


def _paired_reset(
    pending: PendingBookMove,
    tennis: object | None,
    book: BookState,
) -> bool:
    return (
        tennis is not None
        and pending.tennis_correction_epoch_floor is not None
        and tennis.correction_epoch
        > pending.tennis_correction_epoch_floor
        and book.connection_epoch
        > pending.book_connection_epoch_floor
        and tennis.last_received_monotonic_ns
        >= pending.last_move_monotonic_ns
        and book.book_observed_monotonic_ns
        >= pending.last_move_monotonic_ns
    )


def _ingest_tennis_origin(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    evidence: SynchronizationInput,
    observation: PairedTimeObservation,
) -> tuple[SynchronizationSessionState, tuple[SyncResult, ...]]:
    cursor = _selected_tennis_cursor(
        state,
        binding.canonical_match_id,
    )
    if cursor.tennis is not None:
        _drift()
    event = evidence.provider_event
    if type(event) is not ProviderSnapshot or not event.snapshot_complete:
        _drift()
    try:
        tennis = state_from_snapshot(event)
        validate_tennis_state(tennis)
    except (TypeError, ValueError, RuntimeError):
        _drift()
    if _binding_drift(tennis, binding):
        return state, tuple(
            SyncResult(
                binding.canonical_match_id,
                ticker,
                None,
                None,
                SyncReason.BINDING_DRIFT,
            )
            for ticker in sorted(
                (
                    binding.home_market_ticker,
                    binding.away_market_ticker,
                )
            )
        )
    input_sha256 = expert_contract_sha256(evidence)
    updated_cursor = _tennis_cursor(
        cursor,
        tennis=tennis,
        last_state_sha256=expert_contract_sha256(tennis),
        last_input_sha256=input_sha256,
        last_point_witness=None,
    )
    updated_books = []
    for book_cursor in state.book_cursors:
        pending = book_cursor.pending_move
        if (
            book_cursor.canonical_match_id
            == binding.canonical_match_id
            and pending is not None
            and pending.tennis_correction_epoch_floor is None
        ):
            pending = _pending_with_floor(
                pending,
                tennis.correction_epoch,
            )
            book_cursor = _book_cursor(
                book_cursor,
                book=book_cursor.book,
                last_state_sha256=book_cursor.last_state_sha256,
                last_input_sha256=book_cursor.last_input_sha256,
                pending_move=pending,
                causal_point_witness=(
                    book_cursor.causal_point_witness
                ),
                consumed_point_witness=(
                    book_cursor.consumed_point_witness
                ),
                last_emission=book_cursor.last_emission,
            )
        updated_books.append(book_cursor)
    state = _session(
        state,
        decision_sequence=state.decision_sequence,
        last_observation=state.last_observation,
        tennis_cursors=tuple(
            updated_cursor
            if item.canonical_match_id
            == updated_cursor.canonical_match_id
            else item
            for item in state.tennis_cursors
        ),
        book_cursors=tuple(updated_books),
    )
    return _results_for_tickers(
        state,
        binding,
        metadata,
        tuple(
            sorted(
                (
                    binding.home_market_ticker,
                    binding.away_market_ticker,
                )
            )
        ),
        observation,
    )


def _ingest_tennis_transition(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    evidence: SynchronizationInput,
    observation: PairedTimeObservation,
) -> tuple[SynchronizationSessionState, tuple[SyncResult, ...]]:
    cursor = _selected_tennis_cursor(
        state,
        binding.canonical_match_id,
    )
    if (
        cursor.tennis is None
        or evidence.previous_state_sha256
        != cursor.last_state_sha256
    ):
        _drift()
    event = evidence.provider_event
    try:
        if type(event) is ProviderPoint:
            recomputed = apply_point(cursor.tennis, event)
        elif type(event) is ProviderLifecycle:
            recomputed = apply_lifecycle(cursor.tennis, event)
        elif type(event) is ProviderSnapshot:
            recomputed = apply_correction(cursor.tennis, event)
        else:
            _drift()
        if recomputed != evidence.tennis_transition:
            _drift()
        validate_tennis_state(recomputed.state)
    except SynchronizationSessionDriftError:
        raise
    except (TypeError, ValueError, RuntimeError):
        _drift()
    witness = cursor.last_point_witness
    new_point_witness = None
    if (
        type(event) is ProviderPoint
        and recomputed.disposition is TransitionDisposition.APPLIED
        and recomputed.reason is TennisTransitionReason.POINT_APPLIED
    ):
        witness = CausalPointWitness(
            canonical_match_id=binding.canonical_match_id,
            correction_epoch=recomputed.state.correction_epoch,
            revision=recomputed.state.revision,
            event_semantic_sha256=recomputed.event_semantic_sha256,
            received_monotonic_ns=(
                recomputed.state.last_received_monotonic_ns
            ),
        )
        new_point_witness = witness
    applied_correction = (
        type(event) is ProviderSnapshot
        and recomputed.disposition is TransitionDisposition.APPLIED
        and recomputed.reason
        is TennisTransitionReason.CORRECTION_APPLIED
    )
    if applied_correction:
        witness = None
    updated_cursor = _tennis_cursor(
        cursor,
        tennis=recomputed.state,
        last_state_sha256=expert_contract_sha256(recomputed.state),
        last_input_sha256=expert_contract_sha256(evidence),
        last_point_witness=witness,
    )
    updated_books = []
    for book_cursor in state.book_cursors:
        if book_cursor.canonical_match_id != binding.canonical_match_id:
            updated_books.append(book_cursor)
            continue
        causal = (
            None
            if applied_correction
            else book_cursor.causal_point_witness
        )
        book_cursor = _book_cursor(
            book_cursor,
            book=book_cursor.book,
            last_state_sha256=book_cursor.last_state_sha256,
            last_input_sha256=book_cursor.last_input_sha256,
            pending_move=book_cursor.pending_move,
            causal_point_witness=causal,
            consumed_point_witness=(
                book_cursor.consumed_point_witness
            ),
            last_emission=book_cursor.last_emission,
        )
        if new_point_witness is not None:
            book_cursor = _explain_pending(
                book_cursor,
                new_point_witness,
                recomputed.state,
                state.policy,
            )
        updated_books.append(book_cursor)
    state = _session(
        state,
        decision_sequence=state.decision_sequence,
        last_observation=state.last_observation,
        tennis_cursors=tuple(
            updated_cursor
            if item.canonical_match_id
            == updated_cursor.canonical_match_id
            else item
            for item in state.tennis_cursors
        ),
        book_cursors=tuple(updated_books),
    )
    return _results_for_tickers(
        state,
        binding,
        metadata,
        tuple(
            sorted(
                (
                    binding.home_market_ticker,
                    binding.away_market_ticker,
                )
            )
        ),
        observation,
    )


def _ingest_book_transition(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    evidence: SynchronizationInput,
    observation: PairedTimeObservation,
) -> tuple[SynchronizationSessionState, tuple[SyncResult, ...]]:
    assert evidence.ticker is not None
    cursor = _selected_book_cursor(
        state,
        binding.canonical_match_id,
        evidence.ticker,
    )
    event = evidence.book_event
    initial = cursor.book is None
    try:
        if initial:
            if (
                evidence.previous_state_sha256 is not None
                or type(event) is not BookSnapshot
            ):
                _drift()
            recomputed = book_from_snapshot(event)
        else:
            if (
                evidence.previous_state_sha256
                != cursor.last_state_sha256
            ):
                _drift()
            assert cursor.book is not None
            if type(event) is BookSnapshot:
                recomputed = apply_book_snapshot(cursor.book, event)
            elif type(event) is BookDelta:
                recomputed = apply_book_delta(cursor.book, event)
            elif type(event) is MarketLifecycle:
                recomputed = apply_market_lifecycle(cursor.book, event)
            else:
                _drift()
        if (
            evidence.book_transition is None
            or recomputed != evidence.book_transition
        ):
            _drift()
    except SynchronizationSessionDriftError:
        raise
    except (TypeError, ValueError, RuntimeError):
        _drift()
    pending = cursor.pending_move
    causal = cursor.causal_point_witness
    consumed = cursor.consumed_point_witness
    tennis_cursor = _selected_tennis_cursor(
        state,
        binding.canonical_match_id,
    )
    tennis = tennis_cursor.tennis
    if (
        not initial
        and type(event) is BookSnapshot
        and recomputed.accepted_event_kind is BookEventKind.SNAPSHOT
        and pending is not None
        and _paired_reset(pending, tennis, recomputed.state)
    ):
        pending = None
    accepted_cursor = _book_cursor(
        cursor,
        book=recomputed.state,
        last_state_sha256=expert_contract_sha256(recomputed.state),
        last_input_sha256=expert_contract_sha256(evidence),
        pending_move=pending,
        causal_point_witness=causal,
        consumed_point_witness=consumed,
        last_emission=cursor.last_emission,
    )
    if (
        not initial
        and recomputed.accepted_event_kind
        in {BookEventKind.SNAPSHOT, BookEventKind.DELTA}
        and recomputed.executable_move > Decimal("0")
        and recomputed.executable_move
        >= state.policy.large_book_move_threshold
    ):
        pending = (
            _new_pending(accepted_cursor, recomputed, tennis)
            if accepted_cursor.pending_move is None
            else _extended_pending(
                accepted_cursor.pending_move,
                recomputed,
            )
        )
        accepted_cursor = _book_cursor(
            accepted_cursor,
            book=accepted_cursor.book,
            last_state_sha256=accepted_cursor.last_state_sha256,
            last_input_sha256=accepted_cursor.last_input_sha256,
            pending_move=pending,
            causal_point_witness=None,
            consumed_point_witness=(
                accepted_cursor.consumed_point_witness
            ),
            last_emission=accepted_cursor.last_emission,
        )
        accepted_cursor = _explain_pending(
            accepted_cursor,
            tennis_cursor.last_point_witness,
            tennis,
            state.policy,
        )
    state = _replace_book_cursor(state, accepted_cursor)
    return _results_for_tickers(
        state,
        binding,
        metadata,
        (evidence.ticker,),
        observation,
    )


def _ingest_resnapshot_required(
    state: SynchronizationSessionState,
    binding: MatchBinding,
    metadata: BindingMetadata,
    evidence: SynchronizationInput,
    observation: PairedTimeObservation,
) -> tuple[SynchronizationSessionState, tuple[SyncResult, ...]]:
    assert evidence.ticker is not None
    cursor = _selected_book_cursor(
        state,
        binding.canonical_match_id,
        evidence.ticker,
    )
    if (
        cursor.book is None
        or evidence.previous_state_sha256
        != cursor.last_state_sha256
    ):
        _drift()
    try:
        required = require_book_resnapshot(cursor.book)
    except (TypeError, ValueError, RuntimeError):
        _drift()
    if required != evidence.book_resnapshot_state:
        _drift()
    updated = _book_cursor(
        cursor,
        book=required,
        last_state_sha256=expert_contract_sha256(required),
        last_input_sha256=expert_contract_sha256(evidence),
        pending_move=cursor.pending_move,
        causal_point_witness=cursor.causal_point_witness,
        consumed_point_witness=cursor.consumed_point_witness,
        last_emission=cursor.last_emission,
    )
    state = _replace_book_cursor(state, updated)
    return _results_for_tickers(
        state,
        binding,
        metadata,
        (evidence.ticker,),
        observation,
    )


def synchronize(
    state: SynchronizationSessionState,
    evidence: SynchronizationInput,
    *,
    now: PairedTimeObservation,
) -> SynchronizationTransitionResult:
    if type(state) is not SynchronizationSessionState:
        raise TypeError("state")
    if type(evidence) is not SynchronizationInput:
        raise TypeError("evidence")
    if type(now) is not PairedTimeObservation:
        raise TypeError("now")
    assert_synchronization_session_compatible(
        state,
        state.universe,
        state.policy,
    )
    try:
        PairedTimeObservation.__post_init__(now)
    except (TypeError, ValueError):
        _drift()
    if state.last_observation is not None and (
        now.monotonic_ns < state.last_observation.monotonic_ns
        or now.wall_ns < state.last_observation.wall_ns
    ):
        _drift()
    try:
        SynchronizationInput.__post_init__(evidence)
    except (TypeError, ValueError):
        _drift()
    binding, metadata = _binding(
        state,
        evidence.canonical_match_id,
    )
    prior = state
    state = _with_observation(state, now)
    bound_tickers = {
        binding.home_market_ticker,
        binding.away_market_ticker,
    }
    if (
        evidence.kind
        in {
            SyncInputKind.BOOK_TRANSITION,
            SyncInputKind.BOOK_RESNAPSHOT_REQUIRED,
            SyncInputKind.CLOCK,
        }
        and evidence.ticker not in bound_tickers
    ):
        assert evidence.ticker is not None
        result = SyncResult(
            binding.canonical_match_id,
            evidence.ticker,
            None,
            None,
            SyncReason.CONTRACT_MISMATCH,
        )
        return _transition(prior, state, evidence, now, (result,))
    if evidence.kind is SyncInputKind.TENNIS_ORIGIN:
        state, results = _ingest_tennis_origin(
            state,
            binding,
            metadata,
            evidence,
            now,
        )
    elif evidence.kind is SyncInputKind.TENNIS_TRANSITION:
        state, results = _ingest_tennis_transition(
            state,
            binding,
            metadata,
            evidence,
            now,
        )
    elif evidence.kind is SyncInputKind.BOOK_TRANSITION:
        state, results = _ingest_book_transition(
            state,
            binding,
            metadata,
            evidence,
            now,
        )
    elif evidence.kind is SyncInputKind.BOOK_RESNAPSHOT_REQUIRED:
        state, results = _ingest_resnapshot_required(
            state,
            binding,
            metadata,
            evidence,
            now,
        )
    else:
        assert evidence.ticker is not None
        state, results = _results_for_tickers(
            state,
            binding,
            metadata,
            (evidence.ticker,),
            now,
        )
    return _transition(prior, state, evidence, now, results)


def validate_synchronization_transition(
    prior: SynchronizationSessionState,
    transition: SynchronizationTransitionResult,
) -> None:
    if type(prior) is not SynchronizationSessionState:
        raise TypeError("prior")
    if type(transition) is not SynchronizationTransitionResult:
        raise TypeError("transition")
    try:
        SynchronizationTransitionResult.__post_init__(transition)
        expected = synchronize(
            prior,
            transition.input,
            now=transition.observation,
        )
    except (TypeError, ValueError, RuntimeError):
        _drift()
    if expected != transition:
        _drift()
