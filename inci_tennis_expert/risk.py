from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from enum import Enum

from .contracts import ContractSide


_DECIMAL_CONTEXT = Context(prec=80)


class RiskError(ValueError):
    pass


class RiskReason(str, Enum):
    RESERVED = "reserved"
    MATCH_OCCUPIED = "match_occupied"
    PORTFOLIO_FULL = "portfolio_full"
    SESSION_LOSS_LIMIT = "session_loss_limit"
    ATTEMPT_LIMIT = "attempt_limit"
    COOLDOWN = "cooldown"
    SIGNAL_NOT_RESET = "signal_not_reset"
    SCORE_NOT_ADVANCED = "score_not_advanced"


@dataclass(frozen=True, slots=True)
class FrozenRiskPolicy:
    maximum_occupied_matches: int
    maximum_session_loss: Decimal
    maximum_attempts_per_match: int
    stop_cooldown_ns: int

    def __post_init__(self) -> None:
        if (
            type(self.maximum_occupied_matches) is not int
            or self.maximum_occupied_matches <= 0
        ):
            raise RiskError("maximum_occupied_matches")
        if (
            type(self.maximum_session_loss) is not Decimal
            or not self.maximum_session_loss.is_finite()
            or self.maximum_session_loss <= 0
        ):
            raise RiskError("maximum_session_loss")
        if (
            type(self.maximum_attempts_per_match) is not int
            or self.maximum_attempts_per_match <= 0
        ):
            raise RiskError("maximum_attempts_per_match")
        if type(self.stop_cooldown_ns) is not int or self.stop_cooldown_ns < 0:
            raise RiskError("stop_cooldown_ns")


@dataclass(frozen=True, slots=True)
class RiskRequest:
    request_id: str
    canonical_match_id: str
    contract_side: ContractSide
    entry_debit: Decimal
    provider_revision: int
    requested_monotonic_ns: int
    signal_reset: bool

    def __post_init__(self) -> None:
        if type(self.request_id) is not str or not self.request_id:
            raise RiskError("request_id")
        if type(self.canonical_match_id) is not str or not self.canonical_match_id:
            raise RiskError("canonical_match_id")
        if type(self.contract_side) is not ContractSide:
            raise RiskError("contract_side")
        if (
            type(self.entry_debit) is not Decimal
            or not self.entry_debit.is_finite()
            or not Decimal("0") < self.entry_debit <= Decimal("50")
        ):
            raise RiskError("entry_debit")
        if type(self.provider_revision) is not int or self.provider_revision < 0:
            raise RiskError("provider_revision")
        if (
            type(self.requested_monotonic_ns) is not int
            or self.requested_monotonic_ns < 0
        ):
            raise RiskError("requested_monotonic_ns")
        if type(self.signal_reset) is not bool:
            raise RiskError("signal_reset")


@dataclass(frozen=True, slots=True)
class RiskReservation:
    reason: RiskReason
    request: RiskRequest

    def __post_init__(self) -> None:
        if self.reason is not RiskReason.RESERVED:
            raise RiskError("reason")
        if type(self.request) is not RiskRequest:
            raise RiskError("request")


@dataclass(frozen=True, slots=True)
class RiskRejection:
    reason: RiskReason

    def __post_init__(self) -> None:
        if type(self.reason) is not RiskReason or self.reason is RiskReason.RESERVED:
            raise RiskError("reason")


@dataclass(frozen=True, slots=True)
class StopRecord:
    canonical_match_id: str
    provider_revision: int
    stopped_at_monotonic_ns: int

    def __post_init__(self) -> None:
        if type(self.canonical_match_id) is not str or not self.canonical_match_id:
            raise RiskError("canonical_match_id")
        if type(self.provider_revision) is not int or self.provider_revision < 0:
            raise RiskError("provider_revision")
        if (
            type(self.stopped_at_monotonic_ns) is not int
            or self.stopped_at_monotonic_ns < 0
        ):
            raise RiskError("stopped_at_monotonic_ns")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    canonical_match_id: str
    count: int

    def __post_init__(self) -> None:
        if type(self.canonical_match_id) is not str or not self.canonical_match_id:
            raise RiskError("canonical_match_id")
        if type(self.count) is not int or self.count <= 0:
            raise RiskError("count")


@dataclass(frozen=True, slots=True)
class RiskState:
    reservations: tuple[RiskReservation, ...]
    stops: tuple[StopRecord, ...]
    attempts: tuple[AttemptRecord, ...]
    realized_pnl: Decimal

    def __post_init__(self) -> None:
        for value, expected, name in (
            (self.reservations, RiskReservation, "reservations"),
            (self.stops, StopRecord, "stops"),
            (self.attempts, AttemptRecord, "attempts"),
        ):
            if type(value) is not tuple or any(
                type(item) is not expected for item in value
            ):
                raise RiskError(name)
        if type(self.realized_pnl) is not Decimal or not self.realized_pnl.is_finite():
            raise RiskError("realized_pnl")

        reservation_matches = tuple(
            item.request.canonical_match_id for item in self.reservations
        )
        request_ids = tuple(item.request.request_id for item in self.reservations)
        stop_matches = tuple(item.canonical_match_id for item in self.stops)
        attempt_matches = tuple(item.canonical_match_id for item in self.attempts)
        if len(set(reservation_matches)) != len(reservation_matches):
            raise RiskError("reservations")
        if len(set(request_ids)) != len(request_ids):
            raise RiskError("request_id")
        if len(set(stop_matches)) != len(stop_matches):
            raise RiskError("stops")
        if len(set(attempt_matches)) != len(attempt_matches):
            raise RiskError("attempts")


def initial_risk_state() -> RiskState:
    return RiskState(
        reservations=(),
        stops=(),
        attempts=(),
        realized_pnl=Decimal("0"),
    )


def reserve(
    state: RiskState,
    request: RiskRequest,
    policy: FrozenRiskPolicy,
) -> tuple[RiskState, RiskReservation | RiskRejection]:
    if type(state) is not RiskState:
        raise RiskError("state")
    if type(request) is not RiskRequest:
        raise RiskError("request")
    if type(policy) is not FrozenRiskPolicy:
        raise RiskError("policy")
    if any(
        item.request.canonical_match_id == request.canonical_match_id
        for item in state.reservations
    ):
        return state, RiskRejection(RiskReason.MATCH_OCCUPIED)
    with localcontext(_DECIMAL_CONTEXT):
        session_loss_reached = (
            state.realized_pnl <= -policy.maximum_session_loss
        )
    if session_loss_reached:
        return state, RiskRejection(RiskReason.SESSION_LOSS_LIMIT)
    if len(state.reservations) >= policy.maximum_occupied_matches:
        return state, RiskRejection(RiskReason.PORTFOLIO_FULL)
    previous_attempts = next(
        (
            item.count
            for item in state.attempts
            if item.canonical_match_id == request.canonical_match_id
        ),
        0,
    )
    if previous_attempts >= policy.maximum_attempts_per_match:
        return state, RiskRejection(RiskReason.ATTEMPT_LIMIT)
    previous_stop = next(
        (
            item
            for item in state.stops
            if item.canonical_match_id == request.canonical_match_id
        ),
        None,
    )
    if previous_stop is not None and not request.signal_reset:
        return state, RiskRejection(RiskReason.SIGNAL_NOT_RESET)
    if (
        previous_stop is not None
        and request.provider_revision <= previous_stop.provider_revision
    ):
        return state, RiskRejection(RiskReason.SCORE_NOT_ADVANCED)
    if (
        previous_stop is not None
        and request.requested_monotonic_ns
        < previous_stop.stopped_at_monotonic_ns + policy.stop_cooldown_ns
    ):
        return state, RiskRejection(RiskReason.COOLDOWN)
    reservation = RiskReservation(RiskReason.RESERVED, request)
    attempts = tuple(
        item
        for item in state.attempts
        if item.canonical_match_id != request.canonical_match_id
    )
    attempts = tuple(
        sorted(
            (
                *attempts,
                AttemptRecord(request.canonical_match_id, previous_attempts + 1),
            ),
            key=lambda item: item.canonical_match_id,
        )
    )
    return (
        RiskState(
            reservations=tuple(
                sorted(
                    (*state.reservations, reservation),
                    key=lambda item: item.request.canonical_match_id,
                )
            ),
            stops=state.stops,
            attempts=attempts,
            realized_pnl=state.realized_pnl,
        ),
        reservation,
    )


def close_position(
    state: RiskState,
    *,
    canonical_match_id: str,
    realized_pnl: Decimal,
    provider_revision: int,
    closed_monotonic_ns: int,
    stopped: bool,
) -> RiskState:
    if type(state) is not RiskState:
        raise RiskError("state")
    matching = tuple(
        item
        for item in state.reservations
        if item.request.canonical_match_id == canonical_match_id
    )
    if len(matching) != 1:
        raise RiskError("canonical_match_id")
    if type(realized_pnl) is not Decimal or not realized_pnl.is_finite():
        raise RiskError("realized_pnl")
    if type(provider_revision) is not int or provider_revision < 0:
        raise RiskError("provider_revision")
    if type(closed_monotonic_ns) is not int or closed_monotonic_ns < 0:
        raise RiskError("closed_monotonic_ns")
    if type(stopped) is not bool:
        raise RiskError("stopped")
    reservations = tuple(
        item
        for item in state.reservations
        if item.request.canonical_match_id != canonical_match_id
    )
    stops = tuple(
        item for item in state.stops if item.canonical_match_id != canonical_match_id
    )
    if stopped:
        request = matching[0].request
        if provider_revision < request.provider_revision:
            raise RiskError("provider_revision")
        stops = tuple(
            sorted(
                (
                    *stops,
                    StopRecord(
                        canonical_match_id=canonical_match_id,
                        provider_revision=provider_revision,
                        stopped_at_monotonic_ns=closed_monotonic_ns,
                    ),
                ),
                key=lambda item: item.canonical_match_id,
            )
        )
    with localcontext(_DECIMAL_CONTEXT):
        updated_realized_pnl = state.realized_pnl + realized_pnl
    return RiskState(
        reservations=reservations,
        stops=stops,
        attempts=state.attempts,
        realized_pnl=updated_realized_pnl,
    )
