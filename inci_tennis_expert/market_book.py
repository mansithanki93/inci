from __future__ import annotations

from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from typing import Final

from inci_tennis_expert.contracts import (
    BookDelta,
    BookEventKind,
    BookLevel,
    BookSnapshot,
    BookState,
    BookTransitionResult,
    ContractSide,
    ExpertContractError,
    MarketLifecycle,
    MarketStatus,
    expert_contract_sha256,
)


__all__ = (
    "book_from_snapshot",
    "require_book_resnapshot",
    "apply_book_snapshot",
    "apply_book_delta",
    "apply_market_lifecycle",
    "executable_buy",
)


_BOOK_DECIMAL_CONTEXT: Final[Context] = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

_ALLOWED_STATUS_TRANSITIONS: Final[
    dict[MarketStatus, frozenset[MarketStatus]]
] = {
    MarketStatus.PREOPEN: frozenset(
        {
            MarketStatus.PREOPEN,
            MarketStatus.OPEN,
            MarketStatus.SUSPENDED,
            MarketStatus.CLOSED,
            MarketStatus.CANCELLED,
        }
    ),
    MarketStatus.OPEN: frozenset(
        {
            MarketStatus.OPEN,
            MarketStatus.SUSPENDED,
            MarketStatus.CLOSED,
            MarketStatus.CANCELLED,
        }
    ),
    MarketStatus.SUSPENDED: frozenset(
        {
            MarketStatus.SUSPENDED,
            MarketStatus.OPEN,
            MarketStatus.CLOSED,
            MarketStatus.CANCELLED,
        }
    ),
    MarketStatus.CLOSED: frozenset(
        {
            MarketStatus.CLOSED,
            MarketStatus.SETTLED,
            MarketStatus.CANCELLED,
        }
    ),
    MarketStatus.SETTLED: frozenset({MarketStatus.SETTLED}),
    MarketStatus.CANCELLED: frozenset({MarketStatus.CANCELLED}),
}


def _gapped_copy(state: BookState) -> BookState:
    return BookState(
        ticker=state.ticker,
        connection_epoch=state.connection_epoch,
        sequence=state.sequence,
        market_status=state.market_status,
        scheduled_close_wall_ns=state.scheduled_close_wall_ns,
        book_source_wall_ns=state.book_source_wall_ns,
        book_observed_monotonic_ns=state.book_observed_monotonic_ns,
        book_clock_uncertainty_ns=state.book_clock_uncertainty_ns,
        lifecycle_source_wall_ns=state.lifecycle_source_wall_ns,
        lifecycle_observed_monotonic_ns=(
            state.lifecycle_observed_monotonic_ns
        ),
        lifecycle_clock_uncertainty_ns=(
            state.lifecycle_clock_uncertainty_ns
        ),
        yes_bids=state.yes_bids,
        no_bids=state.no_bids,
        trusted=False,
        sequence_gap=True,
        last_executable_move=state.last_executable_move,
        last_executable_move_monotonic_ns=(
            state.last_executable_move_monotonic_ns
        ),
        last_snapshot_sha256=state.last_snapshot_sha256,
        last_event_sha256=state.last_event_sha256,
    )


def _no_event(state: BookState) -> BookTransitionResult:
    return BookTransitionResult(
        state=state,
        accepted_event_kind=None,
        accepted_event_sha256=None,
        executable_move=Decimal("0"),
        move_observed_monotonic_ns=None,
        connection_epoch=state.connection_epoch,
        sequence=state.sequence,
        top_of_book_changed=False,
    )


def _status_transition_allowed(
    current: MarketStatus,
    incoming: MarketStatus,
) -> bool:
    return incoming in _ALLOWED_STATUS_TRANSITIONS[current]


def _best_asks(
    yes_bids: tuple[BookLevel, ...],
    no_bids: tuple[BookLevel, ...],
) -> tuple[Decimal | None, Decimal | None]:
    with localcontext(_BOOK_DECIMAL_CONTEXT):
        best_yes_ask = (
            Decimal("1") - no_bids[0].price if no_bids else None
        )
        best_no_ask = (
            Decimal("1") - yes_bids[0].price if yes_bids else None
        )
    return best_yes_ask, best_no_ask


def _one_side_move(
    before: Decimal | None,
    after: Decimal | None,
) -> Decimal:
    with localcontext(_BOOK_DECIMAL_CONTEXT):
        if before is None and after is None:
            return Decimal("0")
        if before is None or after is None:
            return Decimal("1")
        return abs(after - before)


def _executable_move(
    before_yes_bids: tuple[BookLevel, ...],
    before_no_bids: tuple[BookLevel, ...],
    after_yes_bids: tuple[BookLevel, ...],
    after_no_bids: tuple[BookLevel, ...],
) -> Decimal:
    before_yes_ask, before_no_ask = _best_asks(
        before_yes_bids,
        before_no_bids,
    )
    after_yes_ask, after_no_ask = _best_asks(
        after_yes_bids,
        after_no_bids,
    )
    yes_move = _one_side_move(before_yes_ask, after_yes_ask)
    no_move = _one_side_move(before_no_ask, after_no_ask)
    with localcontext(_BOOK_DECIMAL_CONTEXT):
        return max(yes_move, no_move)


def _is_positive(value: Decimal) -> bool:
    with localcontext(_BOOK_DECIMAL_CONTEXT):
        return value > Decimal("0")


def book_from_snapshot(snapshot: BookSnapshot) -> BookTransitionResult:
    if type(snapshot) is not BookSnapshot:
        raise TypeError("snapshot")
    snapshot_sha256 = expert_contract_sha256(snapshot)
    state = BookState(
        ticker=snapshot.ticker,
        connection_epoch=snapshot.connection_epoch,
        sequence=snapshot.sequence,
        market_status=snapshot.market_status,
        scheduled_close_wall_ns=snapshot.scheduled_close_wall_ns,
        book_source_wall_ns=snapshot.source_wall_ns,
        book_observed_monotonic_ns=snapshot.observed_monotonic_ns,
        book_clock_uncertainty_ns=snapshot.clock_uncertainty_ns,
        lifecycle_source_wall_ns=snapshot.source_wall_ns,
        lifecycle_observed_monotonic_ns=snapshot.observed_monotonic_ns,
        lifecycle_clock_uncertainty_ns=snapshot.clock_uncertainty_ns,
        yes_bids=snapshot.yes_bids,
        no_bids=snapshot.no_bids,
        trusted=True,
        sequence_gap=False,
        last_executable_move=Decimal("0"),
        last_executable_move_monotonic_ns=(
            snapshot.observed_monotonic_ns
        ),
        last_snapshot_sha256=snapshot_sha256,
        last_event_sha256=snapshot_sha256,
    )
    return BookTransitionResult(
        state=state,
        accepted_event_kind=BookEventKind.SNAPSHOT,
        accepted_event_sha256=snapshot_sha256,
        executable_move=Decimal("0"),
        move_observed_monotonic_ns=snapshot.observed_monotonic_ns,
        connection_epoch=snapshot.connection_epoch,
        sequence=snapshot.sequence,
        top_of_book_changed=False,
    )


def require_book_resnapshot(state: BookState) -> BookState:
    if type(state) is not BookState:
        raise TypeError("state")
    if not state.trusted and state.sequence_gap:
        return state
    return _gapped_copy(state)


def apply_book_snapshot(
    state: BookState,
    snapshot: BookSnapshot,
) -> BookTransitionResult:
    if type(state) is not BookState:
        raise TypeError("state")
    if type(snapshot) is not BookSnapshot:
        raise TypeError("snapshot")
    if snapshot.ticker != state.ticker:
        raise ExpertContractError("book_ticker_mismatch")
    if state.trusted or not state.sequence_gap:
        raise ExpertContractError("book_snapshot_not_required")
    if snapshot.connection_epoch < state.connection_epoch:
        raise ExpertContractError("book_snapshot_epoch_stale")
    if snapshot.connection_epoch == state.connection_epoch:
        raise ExpertContractError("book_snapshot_epoch_not_newer")
    if snapshot.observed_monotonic_ns < max(
        state.book_observed_monotonic_ns,
        state.lifecycle_observed_monotonic_ns,
    ):
        raise ExpertContractError("book_time_regression")
    if not _status_transition_allowed(
        state.market_status,
        snapshot.market_status,
    ):
        return _no_event(_gapped_copy(state))
    try:
        event_move = _executable_move(
            state.yes_bids,
            state.no_bids,
            snapshot.yes_bids,
            snapshot.no_bids,
        )
        top_of_book_changed = _is_positive(event_move)
    except DecimalException:
        raise ExpertContractError("book_decimal_arithmetic") from None
    snapshot_sha256 = expert_contract_sha256(snapshot)
    replacement = BookState(
        ticker=snapshot.ticker,
        connection_epoch=snapshot.connection_epoch,
        sequence=snapshot.sequence,
        market_status=snapshot.market_status,
        scheduled_close_wall_ns=snapshot.scheduled_close_wall_ns,
        book_source_wall_ns=snapshot.source_wall_ns,
        book_observed_monotonic_ns=snapshot.observed_monotonic_ns,
        book_clock_uncertainty_ns=snapshot.clock_uncertainty_ns,
        lifecycle_source_wall_ns=snapshot.source_wall_ns,
        lifecycle_observed_monotonic_ns=snapshot.observed_monotonic_ns,
        lifecycle_clock_uncertainty_ns=snapshot.clock_uncertainty_ns,
        yes_bids=snapshot.yes_bids,
        no_bids=snapshot.no_bids,
        trusted=True,
        sequence_gap=False,
        last_executable_move=event_move,
        last_executable_move_monotonic_ns=(
            snapshot.observed_monotonic_ns
        ),
        last_snapshot_sha256=snapshot_sha256,
        last_event_sha256=snapshot_sha256,
    )
    return BookTransitionResult(
        state=replacement,
        accepted_event_kind=BookEventKind.SNAPSHOT,
        accepted_event_sha256=snapshot_sha256,
        executable_move=event_move,
        move_observed_monotonic_ns=snapshot.observed_monotonic_ns,
        connection_epoch=snapshot.connection_epoch,
        sequence=snapshot.sequence,
        top_of_book_changed=top_of_book_changed,
    )


def apply_book_delta(
    state: BookState,
    delta: BookDelta,
) -> BookTransitionResult:
    if type(state) is not BookState:
        raise TypeError("state")
    if type(delta) is not BookDelta:
        raise TypeError("delta")
    if delta.ticker != state.ticker:
        raise ExpertContractError("book_ticker_mismatch")
    if not state.trusted or state.sequence_gap:
        raise ExpertContractError("book_resnapshot_required")
    if delta.connection_epoch < state.connection_epoch:
        raise ExpertContractError("book_epoch_stale")
    if delta.connection_epoch > state.connection_epoch:
        return _no_event(_gapped_copy(state))
    if delta.observed_monotonic_ns < state.book_observed_monotonic_ns:
        return _no_event(_gapped_copy(state))
    if delta.sequence <= state.sequence:
        return _no_event(_gapped_copy(state))
    if delta.sequence > state.sequence + 1:
        return _no_event(_gapped_copy(state))
    try:
        with localcontext(_BOOK_DECIMAL_CONTEXT):
            selected = (
                state.yes_bids
                if delta.contract_side is ContractSide.YES
                else state.no_bids
            )
            matching_index: int | None = None
            for index, existing in enumerate(selected):
                if existing.price == delta.price:
                    matching_index = index
                    break
            if delta.quantity == Decimal("0"):
                if matching_index is None:
                    return _no_event(_gapped_copy(state))
                updated_levels = (
                    selected[:matching_index]
                    + selected[matching_index + 1 :]
                )
            elif matching_index is None:
                updated_levels = selected + (
                    BookLevel(delta.price, delta.quantity),
                )
            else:
                updated_levels = (
                    selected[:matching_index]
                    + (BookLevel(delta.price, delta.quantity),)
                    + selected[matching_index + 1 :]
                )
            updated_levels = tuple(
                sorted(
                    updated_levels,
                    key=lambda item: item.price,
                    reverse=True,
                )
            )
            if delta.contract_side is ContractSide.YES:
                yes_bids = updated_levels
                no_bids = state.no_bids
            else:
                yes_bids = state.yes_bids
                no_bids = updated_levels
            if (
                yes_bids
                and no_bids
                and yes_bids[0].price + no_bids[0].price
                > Decimal("1")
            ):
                return _no_event(_gapped_copy(state))
            event_move = _executable_move(
                state.yes_bids,
                state.no_bids,
                yes_bids,
                no_bids,
            )
            top_of_book_changed = _is_positive(event_move)
    except DecimalException:
        raise ExpertContractError("book_decimal_arithmetic") from None
    delta_sha256 = expert_contract_sha256(delta)
    accepted = BookState(
        ticker=state.ticker,
        connection_epoch=delta.connection_epoch,
        sequence=delta.sequence,
        market_status=state.market_status,
        scheduled_close_wall_ns=state.scheduled_close_wall_ns,
        book_source_wall_ns=delta.source_wall_ns,
        book_observed_monotonic_ns=delta.observed_monotonic_ns,
        book_clock_uncertainty_ns=delta.clock_uncertainty_ns,
        lifecycle_source_wall_ns=state.lifecycle_source_wall_ns,
        lifecycle_observed_monotonic_ns=(
            state.lifecycle_observed_monotonic_ns
        ),
        lifecycle_clock_uncertainty_ns=(
            state.lifecycle_clock_uncertainty_ns
        ),
        yes_bids=yes_bids,
        no_bids=no_bids,
        trusted=True,
        sequence_gap=False,
        last_executable_move=event_move,
        last_executable_move_monotonic_ns=(
            delta.observed_monotonic_ns
        ),
        last_snapshot_sha256=state.last_snapshot_sha256,
        last_event_sha256=delta_sha256,
    )
    return BookTransitionResult(
        state=accepted,
        accepted_event_kind=BookEventKind.DELTA,
        accepted_event_sha256=delta_sha256,
        executable_move=event_move,
        move_observed_monotonic_ns=delta.observed_monotonic_ns,
        connection_epoch=delta.connection_epoch,
        sequence=delta.sequence,
        top_of_book_changed=top_of_book_changed,
    )


def apply_market_lifecycle(
    state: BookState,
    lifecycle: MarketLifecycle,
) -> BookTransitionResult:
    if type(state) is not BookState:
        raise TypeError("state")
    if type(lifecycle) is not MarketLifecycle:
        raise TypeError("lifecycle")
    if lifecycle.ticker != state.ticker:
        raise ExpertContractError("book_ticker_mismatch")
    if lifecycle.connection_epoch < state.connection_epoch:
        raise ExpertContractError("book_epoch_stale")
    if lifecycle.connection_epoch > state.connection_epoch:
        return _no_event(_gapped_copy(state))
    if (
        lifecycle.observed_monotonic_ns
        < state.lifecycle_observed_monotonic_ns
    ):
        raise ExpertContractError("lifecycle_time_regression")
    current_values = (
        state.market_status,
        state.scheduled_close_wall_ns,
        state.lifecycle_source_wall_ns,
        state.lifecycle_clock_uncertainty_ns,
    )
    incoming_values = (
        lifecycle.market_status,
        lifecycle.scheduled_close_wall_ns,
        lifecycle.source_wall_ns,
        lifecycle.clock_uncertainty_ns,
    )
    if (
        lifecycle.observed_monotonic_ns
        == state.lifecycle_observed_monotonic_ns
    ):
        if incoming_values == current_values:
            return _no_event(state)
        return _no_event(_gapped_copy(state))
    if not _status_transition_allowed(
        state.market_status,
        lifecycle.market_status,
    ):
        return _no_event(_gapped_copy(state))
    lifecycle_sha256 = expert_contract_sha256(lifecycle)
    accepted = BookState(
        ticker=state.ticker,
        connection_epoch=state.connection_epoch,
        sequence=state.sequence,
        market_status=lifecycle.market_status,
        scheduled_close_wall_ns=lifecycle.scheduled_close_wall_ns,
        book_source_wall_ns=state.book_source_wall_ns,
        book_observed_monotonic_ns=state.book_observed_monotonic_ns,
        book_clock_uncertainty_ns=state.book_clock_uncertainty_ns,
        lifecycle_source_wall_ns=lifecycle.source_wall_ns,
        lifecycle_observed_monotonic_ns=(
            lifecycle.observed_monotonic_ns
        ),
        lifecycle_clock_uncertainty_ns=lifecycle.clock_uncertainty_ns,
        yes_bids=state.yes_bids,
        no_bids=state.no_bids,
        trusted=state.trusted,
        sequence_gap=state.sequence_gap,
        last_executable_move=state.last_executable_move,
        last_executable_move_monotonic_ns=(
            state.last_executable_move_monotonic_ns
        ),
        last_snapshot_sha256=state.last_snapshot_sha256,
        last_event_sha256=lifecycle_sha256,
    )
    return BookTransitionResult(
        state=accepted,
        accepted_event_kind=BookEventKind.LIFECYCLE,
        accepted_event_sha256=lifecycle_sha256,
        executable_move=Decimal("0"),
        move_observed_monotonic_ns=None,
        connection_epoch=accepted.connection_epoch,
        sequence=accepted.sequence,
        top_of_book_changed=False,
    )


def executable_buy(
    state: BookState,
    outcome: ContractSide,
    contracts: Decimal,
    limit_price: Decimal,
) -> tuple[Decimal, Decimal, tuple[BookLevel, ...]]:
    if type(state) is not BookState:
        raise TypeError("state")
    if type(outcome) is not ContractSide:
        raise TypeError("outcome")
    if type(contracts) is not Decimal:
        raise TypeError("contracts")
    if type(limit_price) is not Decimal:
        raise TypeError("limit_price")
    try:
        with localcontext(_BOOK_DECIMAL_CONTEXT):
            if not contracts.is_finite() or contracts < Decimal("0"):
                raise ExpertContractError("contracts")
            if (
                not limit_price.is_finite()
                or limit_price < Decimal("0")
                or limit_price > Decimal("1")
            ):
                raise ExpertContractError("limit_price")
    except DecimalException:
        raise ExpertContractError("book_decimal_arithmetic") from None
    if not state.trusted or state.sequence_gap:
        raise ExpertContractError("book_untrusted")
    if state.market_status is not MarketStatus.OPEN:
        raise ExpertContractError("market_not_open")
    try:
        with localcontext(_BOOK_DECIMAL_CONTEXT):
            if contracts == Decimal("0"):
                return Decimal("0"), Decimal("0"), ()
            complementary = (
                state.no_bids
                if outcome is ContractSide.YES
                else state.yes_bids
            )
            if not complementary:
                return Decimal("0"), Decimal("0"), ()
            remaining = contracts
            consumed: list[BookLevel] = []
            for bid_level in complementary:
                ask_price = Decimal("1") - bid_level.price
                if ask_price > limit_price:
                    break
                quantity = min(remaining, bid_level.quantity)
                consumed.append(BookLevel(ask_price, quantity))
                remaining -= quantity
                if remaining == Decimal("0"):
                    break
            if not consumed:
                return Decimal("0"), Decimal("0"), ()
            filled_contracts = sum(
                (item.quantity for item in consumed),
                Decimal("0"),
            )
            total_cost = sum(
                (item.price * item.quantity for item in consumed),
                Decimal("0"),
            )
            average_price = total_cost / filled_contracts
            return filled_contracts, average_price, tuple(consumed)
    except DecimalException:
        raise ExpertContractError("book_decimal_arithmetic") from None
