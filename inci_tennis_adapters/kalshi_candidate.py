"""Pure synthetic Kalshi candidate protocol and frame classifier.

This module is deliberately offline and unregistered.  Its schemas and test
fixtures are synthetic research contracts; they are not claims about a live
Kalshi transport or wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
from re import Pattern
from re import compile as pattern_compile
from typing import Final, Literal
import weakref

from tennis_v1.canonical import canonical_json_bytes


_MAX_SIGNED_64: Final[int] = 9_223_372_036_854_775_807
_MAX_RAW_BYTES: Final[int] = 1_048_576
_MAX_JSON_DEPTH: Final[int] = 16
_MAX_JSON_NODES: Final[int] = 8_192
_MAX_KEY_BYTES: Final[int] = 128
_MAX_STRING_BYTES: Final[int] = 4_096
_MAX_LADDER_LEVELS: Final[int] = 1_024
_MAX_CANONICAL_BYTES: Final[int] = 131_064
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000")
_SHA256_RE: Final[Pattern[str]] = pattern_compile(r"[0-9a-f]{64}\Z")
_NONCE_RE: Final[Pattern[str]] = pattern_compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
)
_TICKER_RE: Final[Pattern[str]] = pattern_compile(
    r"[A-Z0-9][A-Z0-9._-]{0,63}\Z"
)
_OPAQUE_RE: Final[Pattern[str]] = pattern_compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
)
_PRICE_RE: Final[Pattern[str]] = pattern_compile(
    r"(?:0|1|0\.[0-9]{0,3}[1-9])\Z"
)
_QUANTITY_RE: Final[Pattern[str]] = pattern_compile(
    r"(?:0|[1-9][0-9]{0,11}(?:\.[0-9]{0,3}[1-9])?|"
    r"0\.[0-9]{0,3}[1-9])\Z"
)
_INTEGER_RE: Final[Pattern[str]] = pattern_compile(
    r"(?:0|-[1-9][0-9]{0,18}|[1-9][0-9]{0,18})\Z"
)
_BARRIER_DOMAIN: Final[bytes] = (
    b"INCI-KALSHI-SYNTHETIC-BARRIER-V1\0"
)
_STATE_DOMAIN: Final[bytes] = (
    b"INCI-KALSHI-SYNTHETIC-PROTOCOL-STATE-V1\0"
)
_EVENT_DOMAIN: Final[bytes] = (
    b"INCI-KALSHI-SYNTHETIC-PROTOCOL-EVENT-V1\0"
)
_TRANSITION_DOMAIN: Final[bytes] = (
    b"INCI-KALSHI-SYNTHETIC-PROTOCOL-TRANSITION-V1\0"
)
_FRAME_DOMAIN: Final[bytes] = (
    b"INCI-KALSHI-SYNTHETIC-CANDIDATE-FRAME-V1\0"
)
_INITIAL_STATE_SENTINEL: Final[object] = object()
_TRANSITION_SENTINEL: Final[object] = object()
_CONSTRUCTION_PROVENANCE: Final[
    dict[
        int,
        tuple[
            weakref.ReferenceType[object],
            tuple[object, ...],
        ],
    ]
] = {}


class KalshiCandidatePhysicalStateV1(str, Enum):
    ABSENT = "ABSENT"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    CLOSING = "CLOSING"
    UNCERTAIN = "UNCERTAIN"
    CLOSED = "CLOSED"
    HALTED = "HALTED"


class KalshiCandidateProtocolEventKindV1(str, Enum):
    OPEN_REQUESTED = "OPEN_REQUESTED"
    OPEN_SUCCEEDED = "OPEN_SUCCEEDED"
    OPEN_FAILED = "OPEN_FAILED"
    RECEIVE_ARMED = "RECEIVE_ARMED"
    RECEIVE_COMPLETED = "RECEIVE_COMPLETED"
    RECEIVE_FAILED = "RECEIVE_FAILED"
    CLOSE_REQUESTED = "CLOSE_REQUESTED"
    CLOSE_PROVEN = "CLOSE_PROVEN"
    CLOSE_TIMEOUT = "CLOSE_TIMEOUT"
    CLOSE_FAILED = "CLOSE_FAILED"
    BARRIER_REQUIRED = "BARRIER_REQUIRED"
    BARRIER_WITNESSED = "BARRIER_WITNESSED"
    HALT_REQUESTED = "HALT_REQUESTED"


class KalshiCandidateProtocolActionV1(str, Enum):
    NONE = "NONE"
    CONNECTION_OPENED = "CONNECTION_OPENED"
    CONNECTION_CLOSED = "CONNECTION_CLOSED"
    BARRIER_REQUIRED = "BARRIER_REQUIRED"
    TRANSITION_REJECTED = "TRANSITION_REJECTED"
    HALTED = "HALTED"


class KalshiCandidateProtocolReasonV1(str, Enum):
    PROTOCOL_TRANSITION_INVALID = "PROTOCOL_TRANSITION_INVALID"
    GENERATION_OVERFLOW = "GENERATION_OVERFLOW"
    OPEN_FAILED = "OPEN_FAILED"
    RECEIVE_FAILED = "RECEIVE_FAILED"
    CLOSE_UNCERTAIN = "CLOSE_UNCERTAIN"
    HALT_REQUESTED = "HALT_REQUESTED"


class KalshiCandidateParseStatusV1(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class KalshiCandidateFrameKindV1(str, Enum):
    ORDERBOOK_SNAPSHOT = "ORDERBOOK_SNAPSHOT"
    ORDERBOOK_DELTA = "ORDERBOOK_DELTA"
    MARKET_LIFECYCLE = "MARKET_LIFECYCLE"
    PUBLIC_TRADE = "PUBLIC_TRADE"


class KalshiCandidateErrorScopeV1(str, Enum):
    TICKER = "TICKER"
    CONNECTION = "CONNECTION"


class KalshiCandidateRejectReasonV1(str, Enum):
    PAYLOAD_INVALID = "PAYLOAD_INVALID"
    SCHEMA_UNKNOWN = "SCHEMA_UNKNOWN"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    IDENTITY_INVALID = "IDENTITY_INVALID"
    BOOK_INVALID = "BOOK_INVALID"


class KalshiCandidateSideV1(str, Enum):
    YES = "YES"
    NO = "NO"


class KalshiCandidateMarketStatusV1(str, Enum):
    OPEN = "OPEN"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"


class _PrivateCandidateValue:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("kalshi_candidate_contract_invalid")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    def __copy__(self) -> object:
        raise TypeError("kalshi_candidate_contract_invalid")

    def __deepcopy__(self, _: object) -> object:
        raise TypeError("kalshi_candidate_contract_invalid")

    def __reduce__(self) -> object:
        raise TypeError("kalshi_candidate_contract_invalid")

    def __init_subclass__(cls, **_: object) -> None:
        if _PrivateCandidateValue not in cls.__bases__:
            raise TypeError("kalshi_candidate_contract_invalid")


class _CanonicalCapacity(ValueError):
    pass


def _build(contract: type, values: dict[str, object]) -> object:
    expected = tuple(item.name for item in fields(contract))
    if type(values) is not dict or tuple(values) != expected:
        raise ValueError("kalshi_candidate_contract_invalid")
    instance = object.__new__(contract)
    for name in expected:
        object.__setattr__(instance, name, values[name])
    identity = id(instance)

    def release(
        reference: weakref.ReferenceType[object],
        *,
        identity: int = identity,
    ) -> None:
        current = _CONSTRUCTION_PROVENANCE.get(identity)
        if current is not None and current[0] is reference:
            _CONSTRUCTION_PROVENANCE.pop(identity, None)

    _CONSTRUCTION_PROVENANCE[identity] = (
        weakref.ref(instance, release),
        _construction_snapshot(instance),
    )
    return instance


def _construction_atom(value: object) -> object:
    if isinstance(value, _PrivateCandidateValue):
        return ("candidate", id(value))
    if type(value) is tuple:
        return ("tuple", tuple(_construction_atom(item) for item in value))
    if isinstance(value, Enum):
        return ("enum", type(value), value.value)
    if type(value) is Decimal:
        return ("decimal", value.as_tuple())
    if value is None or type(value) in {str, int, bool}:
        return ("scalar", value)
    return ("unsupported", type(value), id(value))


def _construction_snapshot(value: object) -> tuple[object, ...]:
    return tuple(
        (item.name, _construction_atom(getattr(value, item.name)))
        for item in fields(type(value))
    )


def _was_factory_built(value: object) -> bool:
    record = _CONSTRUCTION_PROVENANCE.get(id(value))
    return (
        record is not None
        and record[0]() is value
        and record[1] == _construction_snapshot(value)
    )


def _canonical(value: dict[str, object]) -> bytes:
    encoded = canonical_json_bytes(value)
    if len(encoded) > _MAX_CANONICAL_BYTES:
        raise _CanonicalCapacity("kalshi_candidate_contract_invalid")
    return encoded


def _digest(domain: bytes, value: dict[str, object]) -> str:
    return sha256(domain + _canonical(value)).hexdigest()


def _valid_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _valid_generation(value: object) -> bool:
    return (
        type(value) is int
        and 0 <= value <= _MAX_SIGNED_64
    )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiSyntheticBarrierIdentityV1(_PrivateCandidateValue):
    schema_version: int
    physical_connection_generation: int
    barrier_nonce: str
    identity_sha256: str

    @classmethod
    def _create(
        cls,
        physical_connection_generation: int,
        barrier_nonce: str,
    ) -> KalshiSyntheticBarrierIdentityV1:
        if cls is not KalshiSyntheticBarrierIdentityV1:
            raise TypeError("barrier_identity")
        if not _valid_generation(physical_connection_generation):
            raise ValueError("kalshi_candidate_barrier_invalid")
        if type(barrier_nonce) is not str:
            raise TypeError("barrier_nonce")
        if _NONCE_RE.fullmatch(barrier_nonce) is None:
            raise ValueError("kalshi_candidate_barrier_invalid")
        unsigned = {
            "schema_version": 1,
            "physical_connection_generation":
                physical_connection_generation,
            "barrier_nonce": barrier_nonce,
        }
        value = _build(
            cls,
            {
                **unsigned,
                "identity_sha256": _digest(_BARRIER_DOMAIN, unsigned),
            },
        )
        _validate_barrier(value)
        return value


def _barrier_unsigned(
    value: KalshiSyntheticBarrierIdentityV1,
) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "physical_connection_generation":
            value.physical_connection_generation,
        "barrier_nonce": value.barrier_nonce,
    }


def _validate_barrier(value: object) -> None:
    if type(value) is not KalshiSyntheticBarrierIdentityV1:
        raise TypeError("barrier_identity")
    if (
        not _was_factory_built(value)
        or
        value.schema_version != 1
        or not _valid_generation(value.physical_connection_generation)
        or type(value.barrier_nonce) is not str
        or _NONCE_RE.fullmatch(value.barrier_nonce) is None
        or not _valid_sha256(value.identity_sha256)
        or value.identity_sha256
        != _digest(_BARRIER_DOMAIN, _barrier_unsigned(value))
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _barrier_full(
    value: KalshiSyntheticBarrierIdentityV1,
) -> dict[str, object]:
    _validate_barrier(value)
    return {
        **_barrier_unsigned(value),
        "identity_sha256": value.identity_sha256,
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateProtocolStateV1(_PrivateCandidateValue):
    schema_version: int
    physical_state: KalshiCandidatePhysicalStateV1
    physical_connection_generation: int
    active_receive: bool
    pending_barrier_identity: KalshiSyntheticBarrierIdentityV1 | None
    state_sha256: str

    @classmethod
    def _create(
        cls,
        physical_state: KalshiCandidatePhysicalStateV1,
        physical_connection_generation: int,
        active_receive: bool,
        pending_barrier_identity: (
            KalshiSyntheticBarrierIdentityV1 | None
        ),
    ) -> KalshiCandidateProtocolStateV1:
        if cls is not KalshiCandidateProtocolStateV1:
            raise TypeError("state")
        if physical_state is KalshiCandidatePhysicalStateV1.ABSENT:
            raise ValueError("kalshi_candidate_contract_invalid")
        return _create_state(
            physical_state,
            physical_connection_generation,
            active_receive,
            pending_barrier_identity,
            allow_absent=False,
        )

    @classmethod
    def _create_initial_absent(
        cls,
        sentinel: object,
    ) -> KalshiCandidateProtocolStateV1:
        if (
            cls is not KalshiCandidateProtocolStateV1
            or sentinel is not _INITIAL_STATE_SENTINEL
        ):
            raise TypeError("state")
        return _create_state(
            KalshiCandidatePhysicalStateV1.ABSENT,
            0,
            False,
            None,
            allow_absent=True,
        )


def _state_semantics(
    physical_state: object,
    generation: object,
    active_receive: object,
    pending_barrier_identity: object,
    *,
    allow_absent: bool,
) -> None:
    if type(physical_state) is not KalshiCandidatePhysicalStateV1:
        raise ValueError("kalshi_candidate_contract_invalid")
    if not _valid_generation(generation):
        raise ValueError("kalshi_candidate_contract_invalid")
    if type(active_receive) is not bool:
        raise ValueError("kalshi_candidate_contract_invalid")
    if pending_barrier_identity is not None:
        _validate_barrier(pending_barrier_identity)
        if pending_barrier_identity.physical_connection_generation != generation:
            raise ValueError("kalshi_candidate_contract_invalid")
    if physical_state is KalshiCandidatePhysicalStateV1.ABSENT:
        if (
            not allow_absent
            or generation != 0
            or active_receive
            or pending_barrier_identity is not None
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
    elif physical_state is KalshiCandidatePhysicalStateV1.CONNECTING:
        if active_receive or pending_barrier_identity is not None:
            raise ValueError("kalshi_candidate_contract_invalid")
    elif physical_state is KalshiCandidatePhysicalStateV1.CONNECTED:
        if generation < 1 or (
            active_receive and pending_barrier_identity is not None
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
    elif physical_state is KalshiCandidatePhysicalStateV1.CLOSING:
        if generation < 1 or active_receive:
            raise ValueError("kalshi_candidate_contract_invalid")
    elif physical_state is KalshiCandidatePhysicalStateV1.CLOSED:
        if generation < 1 or active_receive:
            raise ValueError("kalshi_candidate_contract_invalid")
    elif physical_state in {
        KalshiCandidatePhysicalStateV1.UNCERTAIN,
        KalshiCandidatePhysicalStateV1.HALTED,
    }:
        if active_receive:
            raise ValueError("kalshi_candidate_contract_invalid")


def _state_unsigned_values(
    physical_state: KalshiCandidatePhysicalStateV1,
    generation: int,
    active_receive: bool,
    pending_barrier_identity: (
        KalshiSyntheticBarrierIdentityV1 | None
    ),
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "physical_state": physical_state.value,
        "physical_connection_generation": generation,
        "active_receive": active_receive,
        "pending_barrier_identity": (
            None
            if pending_barrier_identity is None
            else _barrier_full(pending_barrier_identity)
        ),
    }


def _create_state(
    physical_state: KalshiCandidatePhysicalStateV1,
    generation: int,
    active_receive: bool,
    pending_barrier_identity: (
        KalshiSyntheticBarrierIdentityV1 | None
    ),
    *,
    allow_absent: bool,
) -> KalshiCandidateProtocolStateV1:
    _state_semantics(
        physical_state,
        generation,
        active_receive,
        pending_barrier_identity,
        allow_absent=allow_absent,
    )
    unsigned = _state_unsigned_values(
        physical_state,
        generation,
        active_receive,
        pending_barrier_identity,
    )
    value = _build(
        KalshiCandidateProtocolStateV1,
        {
            "schema_version": 1,
            "physical_state": physical_state,
            "physical_connection_generation": generation,
            "active_receive": active_receive,
            "pending_barrier_identity": pending_barrier_identity,
            "state_sha256": _digest(_STATE_DOMAIN, unsigned),
        },
    )
    _validate_state(value)
    return value


def _state_unsigned(
    value: KalshiCandidateProtocolStateV1,
) -> dict[str, object]:
    return _state_unsigned_values(
        value.physical_state,
        value.physical_connection_generation,
        value.active_receive,
        value.pending_barrier_identity,
    )


def _validate_state(value: object) -> None:
    if type(value) is not KalshiCandidateProtocolStateV1:
        raise TypeError("state")
    if not _was_factory_built(value):
        raise ValueError("kalshi_candidate_contract_invalid")
    _state_semantics(
        value.physical_state,
        value.physical_connection_generation,
        value.active_receive,
        value.pending_barrier_identity,
        allow_absent=True,
    )
    if (
        value.schema_version != 1
        or not _valid_sha256(value.state_sha256)
        or value.state_sha256 != _digest(_STATE_DOMAIN, _state_unsigned(value))
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _state_full(
    value: KalshiCandidateProtocolStateV1,
) -> dict[str, object]:
    _validate_state(value)
    return {
        **_state_unsigned(value),
        "state_sha256": value.state_sha256,
    }


_BARRIER_EVENT_KINDS: Final[
    frozenset[KalshiCandidateProtocolEventKindV1]
] = frozenset(
    {
        KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED,
        KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED,
        KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED,
        KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED,
    }
)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateProtocolEventV1(_PrivateCandidateValue):
    schema_version: int
    kind: KalshiCandidateProtocolEventKindV1
    barrier_identity: KalshiSyntheticBarrierIdentityV1 | None
    event_sha256: str

    @classmethod
    def _create(
        cls,
        kind: KalshiCandidateProtocolEventKindV1,
        barrier_identity: (
            KalshiSyntheticBarrierIdentityV1 | None
        ) = None,
    ) -> KalshiCandidateProtocolEventV1:
        if cls is not KalshiCandidateProtocolEventV1:
            raise TypeError("event")
        if type(kind) is not KalshiCandidateProtocolEventKindV1:
            raise TypeError("kind")
        if barrier_identity is not None:
            if type(barrier_identity) is not KalshiSyntheticBarrierIdentityV1:
                raise TypeError("barrier_identity")
            _validate_barrier(barrier_identity)
        if (kind in _BARRIER_EVENT_KINDS) != (
            barrier_identity is not None
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
        unsigned = _event_unsigned_values(kind, barrier_identity)
        value = _build(
            cls,
            {
                "schema_version": 1,
                "kind": kind,
                "barrier_identity": barrier_identity,
                "event_sha256": _digest(_EVENT_DOMAIN, unsigned),
            },
        )
        _validate_event(value)
        return value


def _event_unsigned_values(
    kind: KalshiCandidateProtocolEventKindV1,
    barrier_identity: KalshiSyntheticBarrierIdentityV1 | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": kind.value,
        "barrier_identity": (
            None
            if barrier_identity is None
            else _barrier_full(barrier_identity)
        ),
    }


def _event_unsigned(
    value: KalshiCandidateProtocolEventV1,
) -> dict[str, object]:
    return _event_unsigned_values(value.kind, value.barrier_identity)


def _validate_event(value: object) -> None:
    if type(value) is not KalshiCandidateProtocolEventV1:
        raise TypeError("event")
    if (
        not _was_factory_built(value)
        or type(value.kind) is not KalshiCandidateProtocolEventKindV1
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    if value.barrier_identity is not None:
        _validate_barrier(value.barrier_identity)
    if (
        value.schema_version != 1
        or (value.kind in _BARRIER_EVENT_KINDS)
        != (value.barrier_identity is not None)
        or not _valid_sha256(value.event_sha256)
        or value.event_sha256 != _digest(_EVENT_DOMAIN, _event_unsigned(value))
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _event_full(
    value: KalshiCandidateProtocolEventV1,
) -> dict[str, object]:
    _validate_event(value)
    return {
        **_event_unsigned(value),
        "event_sha256": value.event_sha256,
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateProtocolTransitionV1(_PrivateCandidateValue):
    schema_version: int
    prior_state: KalshiCandidateProtocolStateV1
    event: KalshiCandidateProtocolEventV1
    state: KalshiCandidateProtocolStateV1
    action: KalshiCandidateProtocolActionV1
    reason: KalshiCandidateProtocolReasonV1 | None
    transition_sha256: str

    @classmethod
    def _create(
        cls,
        prior_state: KalshiCandidateProtocolStateV1,
        event: KalshiCandidateProtocolEventV1,
        state: KalshiCandidateProtocolStateV1,
        action: KalshiCandidateProtocolActionV1,
        reason: KalshiCandidateProtocolReasonV1 | None,
        sentinel: object = None,
    ) -> KalshiCandidateProtocolTransitionV1:
        if (
            cls is not KalshiCandidateProtocolTransitionV1
            or sentinel is not _TRANSITION_SENTINEL
        ):
            raise TypeError("result")
        _validate_state(prior_state)
        _validate_event(event)
        _validate_state(state)
        if type(action) is not KalshiCandidateProtocolActionV1:
            raise ValueError("kalshi_candidate_contract_invalid")
        if (
            reason is not None
            and type(reason) is not KalshiCandidateProtocolReasonV1
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
        unsigned = _transition_unsigned_values(
            prior_state,
            event,
            state,
            action,
            reason,
        )
        value = _build(
            cls,
            {
                "schema_version": 1,
                "prior_state": prior_state,
                "event": event,
                "state": state,
                "action": action,
                "reason": reason,
                "transition_sha256": _digest(
                    _TRANSITION_DOMAIN,
                    unsigned,
                ),
            },
        )
        _validate_kalshi_candidate_protocol_transition(value)
        return value


def _transition_unsigned_values(
    prior_state: KalshiCandidateProtocolStateV1,
    event: KalshiCandidateProtocolEventV1,
    state: KalshiCandidateProtocolStateV1,
    action: KalshiCandidateProtocolActionV1,
    reason: KalshiCandidateProtocolReasonV1 | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "prior_state": _state_full(prior_state),
        "event": _event_full(event),
        "state": _state_full(state),
        "action": action.value,
        "reason": None if reason is None else reason.value,
    }


def _validate_kalshi_candidate_protocol_transition(
    value: object,
) -> None:
    if type(value) is not KalshiCandidateProtocolTransitionV1:
        raise TypeError("result")
    if not _was_factory_built(value):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_state(value.prior_state)
    _validate_event(value.event)
    _validate_state(value.state)
    if (
        value.schema_version != 1
        or type(value.action) is not KalshiCandidateProtocolActionV1
        or (
            value.reason is not None
            and type(value.reason) is not KalshiCandidateProtocolReasonV1
        )
        or not _valid_sha256(value.transition_sha256)
        or value.transition_sha256
        != _digest(
            _TRANSITION_DOMAIN,
            _transition_unsigned_values(
                value.prior_state,
                value.event,
                value.state,
                value.action,
                value.reason,
            ),
        )
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    if not _transition_relation_matches(value):
        raise ValueError("kalshi_candidate_contract_invalid")


def initial_kalshi_candidate_protocol_state(
) -> KalshiCandidateProtocolStateV1:
    return KalshiCandidateProtocolStateV1._create_initial_absent(
        _INITIAL_STATE_SENTINEL
    )


def make_kalshi_synthetic_barrier_identity(
    state: KalshiCandidateProtocolStateV1,
    *,
    barrier_nonce: str,
) -> KalshiSyntheticBarrierIdentityV1:
    if type(state) is not KalshiCandidateProtocolStateV1:
        raise TypeError("state")
    _validate_state(state)
    return KalshiSyntheticBarrierIdentityV1._create(
        state.physical_connection_generation,
        barrier_nonce,
    )


def _same_barrier(
    left: KalshiSyntheticBarrierIdentityV1 | None,
    right: KalshiSyntheticBarrierIdentityV1 | None,
) -> bool:
    if left is None or right is None:
        return False
    _validate_barrier(left)
    _validate_barrier(right)
    return _barrier_full(left) == _barrier_full(right)


def _transition_relation_matches(
    value: KalshiCandidateProtocolTransitionV1,
) -> bool:
    prior = value.prior_state
    event = value.event
    physical = prior.physical_state
    generation = prior.physical_connection_generation
    pending = prior.pending_barrier_identity

    def barrier_equal(
        left: KalshiSyntheticBarrierIdentityV1 | None,
        right: KalshiSyntheticBarrierIdentityV1 | None,
    ) -> bool:
        if left is None or right is None:
            return left is right
        return _barrier_full(left) == _barrier_full(right)

    def matches(
        expected_physical: KalshiCandidatePhysicalStateV1,
        expected_generation: int,
        expected_active: bool,
        expected_pending: KalshiSyntheticBarrierIdentityV1 | None,
        expected_action: KalshiCandidateProtocolActionV1,
        expected_reason: KalshiCandidateProtocolReasonV1 | None,
    ) -> bool:
        state = value.state
        return (
            state.physical_state is expected_physical
            and state.physical_connection_generation
            == expected_generation
            and state.active_receive is expected_active
            and barrier_equal(
                state.pending_barrier_identity,
                expected_pending,
            )
            and value.action is expected_action
            and value.reason is expected_reason
        )

    kind = event.kind
    barrier = event.barrier_identity
    barrier_current = (
        barrier is not None
        and barrier.physical_connection_generation == generation
    )

    if physical is KalshiCandidatePhysicalStateV1.HALTED:
        legal = False
    elif kind is KalshiCandidateProtocolEventKindV1.HALT_REQUESTED:
        return matches(
            KalshiCandidatePhysicalStateV1.HALTED,
            generation,
            False,
            pending,
            KalshiCandidateProtocolActionV1.HALTED,
            KalshiCandidateProtocolReasonV1.HALT_REQUESTED,
        )
    elif (
        physical in {
            KalshiCandidatePhysicalStateV1.ABSENT,
            KalshiCandidatePhysicalStateV1.CLOSED,
        }
        and kind is KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
        and not prior.active_receive
        and pending is None
    ):
        return matches(
            KalshiCandidatePhysicalStateV1.CONNECTING,
            generation,
            False,
            None,
            KalshiCandidateProtocolActionV1.NONE,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTING
        and kind is KalshiCandidateProtocolEventKindV1.OPEN_SUCCEEDED
    ):
        if generation == _MAX_SIGNED_64:
            return matches(
                KalshiCandidatePhysicalStateV1.HALTED,
                generation,
                False,
                None,
                KalshiCandidateProtocolActionV1.HALTED,
                KalshiCandidateProtocolReasonV1.GENERATION_OVERFLOW,
            )
        return matches(
            KalshiCandidatePhysicalStateV1.CONNECTED,
            generation + 1,
            False,
            None,
            KalshiCandidateProtocolActionV1.CONNECTION_OPENED,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTING
        and kind is KalshiCandidateProtocolEventKindV1.OPEN_FAILED
    ):
        return matches(
            KalshiCandidatePhysicalStateV1.HALTED,
            generation,
            False,
            None,
            KalshiCandidateProtocolActionV1.HALTED,
            KalshiCandidateProtocolReasonV1.OPEN_FAILED,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTED
        and kind is KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
        and not prior.active_receive
        and pending is None
    ):
        return matches(
            physical,
            generation,
            True,
            None,
            KalshiCandidateProtocolActionV1.NONE,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTED
        and kind is KalshiCandidateProtocolEventKindV1.RECEIVE_COMPLETED
        and prior.active_receive
        and pending is None
    ):
        return matches(
            physical,
            generation,
            False,
            None,
            KalshiCandidateProtocolActionV1.NONE,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTED
        and kind is KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED
        and prior.active_receive
        and pending is None
        and barrier_current
    ):
        return matches(
            KalshiCandidatePhysicalStateV1.CLOSING,
            generation,
            False,
            barrier,
            KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
            KalshiCandidateProtocolReasonV1.RECEIVE_FAILED,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTED
        and kind is KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED
        and not prior.active_receive
        and pending is None
        and barrier_current
    ):
        return matches(
            physical,
            generation,
            False,
            barrier,
            KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CONNECTED
        and kind is KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED
        and not prior.active_receive
        and barrier_current
        and (
            pending is None
            or _same_barrier(pending, barrier)
        )
    ):
        return matches(
            KalshiCandidatePhysicalStateV1.CLOSING,
            generation,
            False,
            barrier,
            KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
            None,
        )
    elif (
        physical in {
            KalshiCandidatePhysicalStateV1.CONNECTED,
            KalshiCandidatePhysicalStateV1.CLOSING,
            KalshiCandidatePhysicalStateV1.CLOSED,
        }
        and kind is KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED
        and not prior.active_receive
        and pending is not None
        and _same_barrier(pending, barrier)
    ):
        return matches(
            physical,
            generation,
            False,
            None,
            KalshiCandidateProtocolActionV1.NONE,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CLOSING
        and kind is KalshiCandidateProtocolEventKindV1.CLOSE_PROVEN
        and not prior.active_receive
    ):
        return matches(
            KalshiCandidatePhysicalStateV1.CLOSED,
            generation,
            False,
            pending,
            KalshiCandidateProtocolActionV1.CONNECTION_CLOSED,
            None,
        )
    elif (
        physical is KalshiCandidatePhysicalStateV1.CLOSING
        and kind in {
            KalshiCandidateProtocolEventKindV1.CLOSE_TIMEOUT,
            KalshiCandidateProtocolEventKindV1.CLOSE_FAILED,
        }
    ):
        return matches(
            KalshiCandidatePhysicalStateV1.HALTED,
            generation,
            False,
            pending,
            KalshiCandidateProtocolActionV1.HALTED,
            KalshiCandidateProtocolReasonV1.CLOSE_UNCERTAIN,
        )
    else:
        legal = False
    return (
        not legal
        and value.state is prior
        and value.action
        is KalshiCandidateProtocolActionV1.TRANSITION_REJECTED
        and value.reason
        is KalshiCandidateProtocolReasonV1.PROTOCOL_TRANSITION_INVALID
    )


def _transition(
    prior_state: KalshiCandidateProtocolStateV1,
    event: KalshiCandidateProtocolEventV1,
    state: KalshiCandidateProtocolStateV1,
    action: KalshiCandidateProtocolActionV1,
    reason: KalshiCandidateProtocolReasonV1 | None = None,
) -> KalshiCandidateProtocolTransitionV1:
    return KalshiCandidateProtocolTransitionV1._create(
        prior_state,
        event,
        state,
        action,
        reason,
        _TRANSITION_SENTINEL,
    )


def _rejected_transition(
    state: KalshiCandidateProtocolStateV1,
    event: KalshiCandidateProtocolEventV1,
) -> KalshiCandidateProtocolTransitionV1:
    return _transition(
        state,
        event,
        state,
        KalshiCandidateProtocolActionV1.TRANSITION_REJECTED,
        KalshiCandidateProtocolReasonV1.PROTOCOL_TRANSITION_INVALID,
    )


def transition_kalshi_candidate_protocol(
    state: KalshiCandidateProtocolStateV1,
    event: KalshiCandidateProtocolEventV1,
) -> KalshiCandidateProtocolTransitionV1:
    if type(state) is not KalshiCandidateProtocolStateV1:
        raise TypeError("state")
    if type(event) is not KalshiCandidateProtocolEventV1:
        raise TypeError("event")
    _validate_state(state)
    _validate_event(event)
    physical = state.physical_state
    kind = event.kind
    generation = state.physical_connection_generation
    pending = state.pending_barrier_identity

    if physical is KalshiCandidatePhysicalStateV1.HALTED:
        return _rejected_transition(state, event)

    if kind is KalshiCandidateProtocolEventKindV1.HALT_REQUESTED:
        halted = KalshiCandidateProtocolStateV1._create(
            KalshiCandidatePhysicalStateV1.HALTED,
            generation,
            False,
            pending,
        )
        return _transition(
            state,
            event,
            halted,
            KalshiCandidateProtocolActionV1.HALTED,
            KalshiCandidateProtocolReasonV1.HALT_REQUESTED,
        )

    if (
        physical in {
            KalshiCandidatePhysicalStateV1.ABSENT,
            KalshiCandidatePhysicalStateV1.CLOSED,
        }
        and kind is KalshiCandidateProtocolEventKindV1.OPEN_REQUESTED
        and not state.active_receive
        and pending is None
    ):
        opening = KalshiCandidateProtocolStateV1._create(
            KalshiCandidatePhysicalStateV1.CONNECTING,
            generation,
            False,
            None,
        )
        return _transition(
            state,
            event,
            opening,
            KalshiCandidateProtocolActionV1.NONE,
        )

    if (
        physical is KalshiCandidatePhysicalStateV1.CONNECTING
        and kind is KalshiCandidateProtocolEventKindV1.OPEN_SUCCEEDED
    ):
        if generation == _MAX_SIGNED_64:
            halted = KalshiCandidateProtocolStateV1._create(
                KalshiCandidatePhysicalStateV1.HALTED,
                generation,
                False,
                None,
            )
            return _transition(
                state,
                event,
                halted,
                KalshiCandidateProtocolActionV1.HALTED,
                KalshiCandidateProtocolReasonV1.GENERATION_OVERFLOW,
            )
        connected = KalshiCandidateProtocolStateV1._create(
            KalshiCandidatePhysicalStateV1.CONNECTED,
            generation + 1,
            False,
            None,
        )
        return _transition(
            state,
            event,
            connected,
            KalshiCandidateProtocolActionV1.CONNECTION_OPENED,
        )

    if (
        physical is KalshiCandidatePhysicalStateV1.CONNECTING
        and kind is KalshiCandidateProtocolEventKindV1.OPEN_FAILED
    ):
        halted = KalshiCandidateProtocolStateV1._create(
            KalshiCandidatePhysicalStateV1.HALTED,
            generation,
            False,
            None,
        )
        return _transition(
            state,
            event,
            halted,
            KalshiCandidateProtocolActionV1.HALTED,
            KalshiCandidateProtocolReasonV1.OPEN_FAILED,
        )

    if physical is KalshiCandidatePhysicalStateV1.CONNECTED:
        if (
            kind is KalshiCandidateProtocolEventKindV1.RECEIVE_ARMED
            and not state.active_receive
            and pending is None
        ):
            armed = KalshiCandidateProtocolStateV1._create(
                physical,
                generation,
                True,
                None,
            )
            return _transition(
                state,
                event,
                armed,
                KalshiCandidateProtocolActionV1.NONE,
            )
        if (
            kind is KalshiCandidateProtocolEventKindV1.RECEIVE_COMPLETED
            and state.active_receive
            and pending is None
        ):
            completed = KalshiCandidateProtocolStateV1._create(
                physical,
                generation,
                False,
                None,
            )
            return _transition(
                state,
                event,
                completed,
                KalshiCandidateProtocolActionV1.NONE,
            )
        if (
            kind is KalshiCandidateProtocolEventKindV1.RECEIVE_FAILED
            and state.active_receive
            and pending is None
            and event.barrier_identity is not None
            and event.barrier_identity.physical_connection_generation
            == generation
        ):
            closing = KalshiCandidateProtocolStateV1._create(
                KalshiCandidatePhysicalStateV1.CLOSING,
                generation,
                False,
                event.barrier_identity,
            )
            return _transition(
                state,
                event,
                closing,
                KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
                KalshiCandidateProtocolReasonV1.RECEIVE_FAILED,
            )
        if (
            kind is KalshiCandidateProtocolEventKindV1.BARRIER_REQUIRED
            and not state.active_receive
            and pending is None
            and event.barrier_identity is not None
            and event.barrier_identity.physical_connection_generation
            == generation
        ):
            waiting = KalshiCandidateProtocolStateV1._create(
                physical,
                generation,
                False,
                event.barrier_identity,
            )
            return _transition(
                state,
                event,
                waiting,
                KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
            )
        if (
            kind is KalshiCandidateProtocolEventKindV1.CLOSE_REQUESTED
            and not state.active_receive
            and event.barrier_identity is not None
            and event.barrier_identity.physical_connection_generation
            == generation
            and (
                pending is None
                or _same_barrier(pending, event.barrier_identity)
            )
        ):
            closing = KalshiCandidateProtocolStateV1._create(
                KalshiCandidatePhysicalStateV1.CLOSING,
                generation,
                False,
                event.barrier_identity,
            )
            return _transition(
                state,
                event,
                closing,
                KalshiCandidateProtocolActionV1.BARRIER_REQUIRED,
            )

    if (
        physical in {
            KalshiCandidatePhysicalStateV1.CONNECTED,
            KalshiCandidatePhysicalStateV1.CLOSING,
            KalshiCandidatePhysicalStateV1.CLOSED,
        }
        and kind is KalshiCandidateProtocolEventKindV1.BARRIER_WITNESSED
        and not state.active_receive
        and pending is not None
        and _same_barrier(pending, event.barrier_identity)
    ):
        witnessed = KalshiCandidateProtocolStateV1._create(
            physical,
            generation,
            False,
            None,
        )
        return _transition(
            state,
            event,
            witnessed,
            KalshiCandidateProtocolActionV1.NONE,
        )

    if (
        physical is KalshiCandidatePhysicalStateV1.CLOSING
        and kind is KalshiCandidateProtocolEventKindV1.CLOSE_PROVEN
        and not state.active_receive
    ):
        closed = KalshiCandidateProtocolStateV1._create(
            KalshiCandidatePhysicalStateV1.CLOSED,
            generation,
            False,
            pending,
        )
        return _transition(
            state,
            event,
            closed,
            KalshiCandidateProtocolActionV1.CONNECTION_CLOSED,
        )

    if (
        physical is KalshiCandidatePhysicalStateV1.CLOSING
        and kind in {
            KalshiCandidateProtocolEventKindV1.CLOSE_TIMEOUT,
            KalshiCandidateProtocolEventKindV1.CLOSE_FAILED,
        }
    ):
        halted = KalshiCandidateProtocolStateV1._create(
            KalshiCandidatePhysicalStateV1.HALTED,
            generation,
            False,
            pending,
        )
        return _transition(
            state,
            event,
            halted,
            KalshiCandidateProtocolActionV1.HALTED,
            KalshiCandidateProtocolReasonV1.CLOSE_UNCERTAIN,
        )

    return _rejected_transition(state, event)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateBookLevelV1(_PrivateCandidateValue):
    price: Decimal
    quantity: Decimal

    @classmethod
    def _create(
        cls,
        price: Decimal,
        quantity: Decimal,
    ) -> KalshiCandidateBookLevelV1:
        if cls is not KalshiCandidateBookLevelV1:
            raise TypeError("result")
        _validate_price_decimal(price)
        _validate_positive_quantity_decimal(quantity)
        return _build(cls, {"price": price, "quantity": quantity})


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateOrderbookSnapshotV1(_PrivateCandidateValue):
    schema_version: int
    ticker: str
    opaque_sequence: str
    complete: Literal[True]
    yes_bids: tuple[KalshiCandidateBookLevelV1, ...]
    no_bids: tuple[KalshiCandidateBookLevelV1, ...]

    @classmethod
    def _create(
        cls,
        ticker: str,
        opaque_sequence: str,
        complete: bool,
        yes_bids: tuple[KalshiCandidateBookLevelV1, ...],
        no_bids: tuple[KalshiCandidateBookLevelV1, ...],
    ) -> KalshiCandidateOrderbookSnapshotV1:
        if cls is not KalshiCandidateOrderbookSnapshotV1:
            raise TypeError("result")
        value = _build(
            cls,
            {
                "schema_version": 1,
                "ticker": ticker,
                "opaque_sequence": opaque_sequence,
                "complete": complete,
                "yes_bids": yes_bids,
                "no_bids": no_bids,
            },
        )
        _validate_snapshot(value)
        return value


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateOrderbookDeltaV1(_PrivateCandidateValue):
    schema_version: int
    ticker: str
    opaque_sequence: str
    side: KalshiCandidateSideV1
    price: Decimal
    quantity_delta: Decimal

    @classmethod
    def _create(
        cls,
        ticker: str,
        opaque_sequence: str,
        side: KalshiCandidateSideV1,
        price: Decimal,
        quantity_delta: Decimal,
    ) -> KalshiCandidateOrderbookDeltaV1:
        if cls is not KalshiCandidateOrderbookDeltaV1:
            raise TypeError("result")
        value = _build(
            cls,
            {
                "schema_version": 1,
                "ticker": ticker,
                "opaque_sequence": opaque_sequence,
                "side": side,
                "price": price,
                "quantity_delta": quantity_delta,
            },
        )
        _validate_delta(value)
        return value


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateMarketLifecycleV1(_PrivateCandidateValue):
    schema_version: int
    ticker: str
    opaque_sequence: str
    status: KalshiCandidateMarketStatusV1

    @classmethod
    def _create(
        cls,
        ticker: str,
        opaque_sequence: str,
        status: KalshiCandidateMarketStatusV1,
    ) -> KalshiCandidateMarketLifecycleV1:
        if cls is not KalshiCandidateMarketLifecycleV1:
            raise TypeError("result")
        value = _build(
            cls,
            {
                "schema_version": 1,
                "ticker": ticker,
                "opaque_sequence": opaque_sequence,
                "status": status,
            },
        )
        _validate_lifecycle(value)
        return value


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidatePublicTradeV1(_PrivateCandidateValue):
    schema_version: int
    ticker: str
    opaque_sequence: str
    trade_id: str
    side: KalshiCandidateSideV1
    price: Decimal
    quantity: Decimal

    @classmethod
    def _create(
        cls,
        ticker: str,
        opaque_sequence: str,
        trade_id: str,
        side: KalshiCandidateSideV1,
        price: Decimal,
        quantity: Decimal,
    ) -> KalshiCandidatePublicTradeV1:
        if cls is not KalshiCandidatePublicTradeV1:
            raise TypeError("result")
        value = _build(
            cls,
            {
                "schema_version": 1,
                "ticker": ticker,
                "opaque_sequence": opaque_sequence,
                "trade_id": trade_id,
                "side": side,
                "price": price,
                "quantity": quantity,
            },
        )
        _validate_trade(value)
        return value


KalshiCandidateFrameV1 = (
    KalshiCandidateOrderbookSnapshotV1
    | KalshiCandidateOrderbookDeltaV1
    | KalshiCandidateMarketLifecycleV1
    | KalshiCandidatePublicTradeV1
)


def _validate_identity(ticker: object, opaque_sequence: object) -> None:
    if (
        type(ticker) is not str
        or _TICKER_RE.fullmatch(ticker) is None
        or type(opaque_sequence) is not str
        or _OPAQUE_RE.fullmatch(opaque_sequence) is None
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _validate_price_decimal(value: object) -> None:
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or _PRICE_RE.fullmatch(_decimal_text(value)) is None
        or value < 0
        or value > 1
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _validate_positive_quantity_decimal(value: object) -> None:
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or _QUANTITY_RE.fullmatch(_decimal_text(value)) is None
        or value <= 0
        or value > _MAX_QUANTITY
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _validate_signed_quantity_decimal(value: object) -> None:
    if type(value) is not Decimal or not value.is_finite() or value == 0:
        raise ValueError("kalshi_candidate_contract_invalid")
    text = _decimal_text(value)
    absolute = text[1:] if text.startswith("-") else text
    if (
        _QUANTITY_RE.fullmatch(absolute) is None
        or abs(value) > _MAX_QUANTITY
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _validate_level(value: object) -> None:
    if (
        type(value) is not KalshiCandidateBookLevelV1
        or not _was_factory_built(value)
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_price_decimal(value.price)
    _validate_positive_quantity_decimal(value.quantity)


def _validate_snapshot(value: object) -> None:
    if (
        type(value) is not KalshiCandidateOrderbookSnapshotV1
        or not _was_factory_built(value)
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_identity(value.ticker, value.opaque_sequence)
    if (
        value.schema_version != 1
        or value.complete is not True
        or type(value.yes_bids) is not tuple
        or type(value.no_bids) is not tuple
        or len(value.yes_bids) > _MAX_LADDER_LEVELS
        or len(value.no_bids) > _MAX_LADDER_LEVELS
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    for ladder in (value.yes_bids, value.no_bids):
        previous: Decimal | None = None
        for level in ladder:
            _validate_level(level)
            if previous is not None and level.price >= previous:
                raise ValueError("kalshi_candidate_contract_invalid")
            previous = level.price
    if (
        value.yes_bids
        and value.no_bids
        and value.yes_bids[0].price + value.no_bids[0].price > 1
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _validate_delta(value: object) -> None:
    if (
        type(value) is not KalshiCandidateOrderbookDeltaV1
        or not _was_factory_built(value)
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_identity(value.ticker, value.opaque_sequence)
    if (
        value.schema_version != 1
        or type(value.side) is not KalshiCandidateSideV1
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_price_decimal(value.price)
    _validate_signed_quantity_decimal(value.quantity_delta)


def _validate_lifecycle(value: object) -> None:
    if (
        type(value) is not KalshiCandidateMarketLifecycleV1
        or not _was_factory_built(value)
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_identity(value.ticker, value.opaque_sequence)
    if (
        value.schema_version != 1
        or type(value.status) is not KalshiCandidateMarketStatusV1
    ):
        raise ValueError("kalshi_candidate_contract_invalid")


def _validate_trade(value: object) -> None:
    if (
        type(value) is not KalshiCandidatePublicTradeV1
        or not _was_factory_built(value)
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_identity(value.ticker, value.opaque_sequence)
    if (
        value.schema_version != 1
        or type(value.trade_id) is not str
        or _TICKER_RE.fullmatch(value.trade_id) is None
        or type(value.side) is not KalshiCandidateSideV1
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_price_decimal(value.price)
    _validate_positive_quantity_decimal(value.quantity)


def _validate_frame(value: object) -> None:
    if type(value) is KalshiCandidateOrderbookSnapshotV1:
        _validate_snapshot(value)
    elif type(value) is KalshiCandidateOrderbookDeltaV1:
        _validate_delta(value)
    elif type(value) is KalshiCandidateMarketLifecycleV1:
        _validate_lifecycle(value)
    elif type(value) is KalshiCandidatePublicTradeV1:
        _validate_trade(value)
    else:
        raise ValueError("kalshi_candidate_contract_invalid")


def _frame_projection(value: KalshiCandidateFrameV1) -> dict[str, object]:
    _validate_frame(value)
    if type(value) is KalshiCandidateOrderbookSnapshotV1:
        return {
            "schema_version": 1,
            "type": "ORDERBOOK_SNAPSHOT",
            "ticker": value.ticker,
            "sequence": value.opaque_sequence,
            "complete": True,
            "yes_bids": [
                {
                    "price": _decimal_text(level.price),
                    "quantity": _decimal_text(level.quantity),
                }
                for level in value.yes_bids
            ],
            "no_bids": [
                {
                    "price": _decimal_text(level.price),
                    "quantity": _decimal_text(level.quantity),
                }
                for level in value.no_bids
            ],
        }
    if type(value) is KalshiCandidateOrderbookDeltaV1:
        return {
            "schema_version": 1,
            "type": "ORDERBOOK_DELTA",
            "ticker": value.ticker,
            "sequence": value.opaque_sequence,
            "side": value.side.value,
            "price": _decimal_text(value.price),
            "quantity_delta": _decimal_text(value.quantity_delta),
        }
    if type(value) is KalshiCandidateMarketLifecycleV1:
        return {
            "schema_version": 1,
            "type": "MARKET_LIFECYCLE",
            "ticker": value.ticker,
            "sequence": value.opaque_sequence,
            "status": value.status.value,
        }
    return {
        "schema_version": 1,
        "type": "PUBLIC_TRADE",
        "ticker": value.ticker,
        "sequence": value.opaque_sequence,
        "trade_id": value.trade_id,
        "side": value.side.value,
        "price": _decimal_text(value.price),
        "quantity": _decimal_text(value.quantity),
    }


@dataclass(frozen=True, slots=True, init=False, repr=False)
class KalshiCandidateFrameResultV1(_PrivateCandidateValue):
    schema_version: int
    status: KalshiCandidateParseStatusV1
    kind: KalshiCandidateFrameKindV1 | None
    scope: KalshiCandidateErrorScopeV1 | None
    reason: KalshiCandidateRejectReasonV1 | None
    raw_sha256: str
    ticker: str | None
    opaque_sequence: str | None
    frame: KalshiCandidateFrameV1 | None
    result_sha256: str

    @classmethod
    def _create(
        cls,
        status: KalshiCandidateParseStatusV1,
        kind: KalshiCandidateFrameKindV1 | None,
        scope: KalshiCandidateErrorScopeV1 | None,
        reason: KalshiCandidateRejectReasonV1 | None,
        raw_sha256: str,
        ticker: str | None,
        opaque_sequence: str | None,
        frame: KalshiCandidateFrameV1 | None,
    ) -> KalshiCandidateFrameResultV1:
        if cls is not KalshiCandidateFrameResultV1:
            raise TypeError("result")
        _validate_result_values(
            status,
            kind,
            scope,
            reason,
            raw_sha256,
            ticker,
            opaque_sequence,
            frame,
        )
        unsigned = _result_unsigned_values(
            status,
            kind,
            scope,
            reason,
            raw_sha256,
            ticker,
            opaque_sequence,
            frame,
        )
        result_sha256 = _digest(_FRAME_DOMAIN, unsigned)
        _canonical({**unsigned, "result_sha256": result_sha256})
        value = _build(
            cls,
            {
                "schema_version": 1,
                "status": status,
                "kind": kind,
                "scope": scope,
                "reason": reason,
                "raw_sha256": raw_sha256,
                "ticker": ticker,
                "opaque_sequence": opaque_sequence,
                "frame": frame,
                "result_sha256": result_sha256,
            },
        )
        _validate_result(value)
        return value


def _validate_result_values(
    status: object,
    kind: object,
    scope: object,
    reason: object,
    raw_sha256: object,
    ticker: object,
    opaque_sequence: object,
    frame: object,
) -> None:
    if not _valid_sha256(raw_sha256):
        raise ValueError("kalshi_candidate_contract_invalid")
    if status is KalshiCandidateParseStatusV1.ACCEPTED:
        if (
            type(kind) is not KalshiCandidateFrameKindV1
            or scope is not None
            or reason is not None
            or type(ticker) is not str
            or type(opaque_sequence) is not str
            or frame is None
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
        _validate_frame(frame)
        frame_kind = _kind_for_frame(frame)
        if (
            kind is not frame_kind
            or frame.ticker != ticker
            or frame.opaque_sequence != opaque_sequence
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
    elif status is KalshiCandidateParseStatusV1.REJECTED:
        if (
            kind is not None
            or type(scope) is not KalshiCandidateErrorScopeV1
            or type(reason) is not KalshiCandidateRejectReasonV1
            or opaque_sequence is not None
            or frame is not None
            or (
                scope is KalshiCandidateErrorScopeV1.CONNECTION
                and ticker is not None
            )
            or (
                scope is KalshiCandidateErrorScopeV1.TICKER
                and (
                    type(ticker) is not str
                    or _TICKER_RE.fullmatch(ticker) is None
                )
            )
        ):
            raise ValueError("kalshi_candidate_contract_invalid")
    else:
        raise ValueError("kalshi_candidate_contract_invalid")


def _result_unsigned_values(
    status: KalshiCandidateParseStatusV1,
    kind: KalshiCandidateFrameKindV1 | None,
    scope: KalshiCandidateErrorScopeV1 | None,
    reason: KalshiCandidateRejectReasonV1 | None,
    raw_sha256: str,
    ticker: str | None,
    opaque_sequence: str | None,
    frame: KalshiCandidateFrameV1 | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": status.value,
        "kind": None if kind is None else kind.value,
        "scope": None if scope is None else scope.value,
        "reason": None if reason is None else reason.value,
        "raw_sha256": raw_sha256,
        "ticker": ticker,
        "opaque_sequence": opaque_sequence,
        "frame": None if frame is None else _frame_projection(frame),
    }


def _validate_result(value: object) -> None:
    if type(value) is not KalshiCandidateFrameResultV1:
        raise TypeError("result")
    if not _was_factory_built(value):
        raise ValueError("kalshi_candidate_contract_invalid")
    _validate_result_values(
        value.status,
        value.kind,
        value.scope,
        value.reason,
        value.raw_sha256,
        value.ticker,
        value.opaque_sequence,
        value.frame,
    )
    unsigned = _result_unsigned_values(
        value.status,
        value.kind,
        value.scope,
        value.reason,
        value.raw_sha256,
        value.ticker,
        value.opaque_sequence,
        value.frame,
    )
    if (
        value.schema_version != 1
        or not _valid_sha256(value.result_sha256)
        or value.result_sha256 != _digest(_FRAME_DOMAIN, unsigned)
    ):
        raise ValueError("kalshi_candidate_contract_invalid")
    _canonical({**unsigned, "result_sha256": value.result_sha256})


def candidate_unsigned_frame_projection_v1(
    result: KalshiCandidateFrameResultV1,
) -> dict[str, object]:
    if type(result) is not KalshiCandidateFrameResultV1:
        raise TypeError("result")
    _validate_result(result)
    return _result_unsigned_values(
        result.status,
        result.kind,
        result.scope,
        result.reason,
        result.raw_sha256,
        result.ticker,
        result.opaque_sequence,
        result.frame,
    )


def _kind_for_frame(
    frame: KalshiCandidateFrameV1,
) -> KalshiCandidateFrameKindV1:
    if type(frame) is KalshiCandidateOrderbookSnapshotV1:
        return KalshiCandidateFrameKindV1.ORDERBOOK_SNAPSHOT
    if type(frame) is KalshiCandidateOrderbookDeltaV1:
        return KalshiCandidateFrameKindV1.ORDERBOOK_DELTA
    if type(frame) is KalshiCandidateMarketLifecycleV1:
        return KalshiCandidateFrameKindV1.MARKET_LIFECYCLE
    if type(frame) is KalshiCandidatePublicTradeV1:
        return KalshiCandidateFrameKindV1.PUBLIC_TRADE
    raise ValueError("kalshi_candidate_contract_invalid")


class _JsonFailure(Exception):
    def __init__(self, reason: KalshiCandidateRejectReasonV1) -> None:
        self.reason = reason


class _BoundedJsonParser:
    __slots__ = ("_text", "_index", "_nodes")

    def __init__(self, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            prefix = payload[:error.start].decode(
                "utf-8",
                errors="strict",
            )
            self._text = prefix
            self._index = 0
            self._nodes = 0
            try:
                self.parse()
            except _JsonFailure as prefix_error:
                if (
                    prefix_error.reason
                    is KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED
                ):
                    raise prefix_error from error
            raise _JsonFailure(
                KalshiCandidateRejectReasonV1.PAYLOAD_INVALID
            ) from error
        if text.startswith("\ufeff"):
            raise _JsonFailure(
                KalshiCandidateRejectReasonV1.PAYLOAD_INVALID
            )
        self._text = text
        self._index = 0
        self._nodes = 0

    def parse(self) -> object:
        self._space()
        value = self._value(0)
        self._space()
        if self._index != len(self._text):
            self._payload()
        return value

    def _capacity(self) -> None:
        raise _JsonFailure(
            KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED
        )

    def _payload(self) -> None:
        raise _JsonFailure(
            KalshiCandidateRejectReasonV1.PAYLOAD_INVALID
        )

    def _node(self) -> None:
        if self._nodes >= _MAX_JSON_NODES:
            self._capacity()
        self._nodes += 1

    def _space(self) -> None:
        while (
            self._index < len(self._text)
            and self._text[self._index] in " \t\r\n"
        ):
            self._index += 1

    def _value(
        self,
        depth: int,
        *,
        allow_boolean: bool = False,
    ) -> object:
        if self._index >= len(self._text):
            self._payload()
        character = self._text[self._index]
        if character == "{":
            return self._object(depth)
        if character == "[":
            return self._array(depth)
        self._node()
        if character == '"':
            return self._string(is_key=False)
        if character == "t" and self._consume("true"):
            if not allow_boolean:
                self._payload()
            return True
        if character == "f" and self._consume("false"):
            if not allow_boolean:
                self._payload()
            return False
        if character == "n" and self._consume("null"):
            return None
        return self._integer()

    def _object(self, depth: int) -> dict[str, object]:
        if depth > _MAX_JSON_DEPTH:
            self._capacity()
        self._node()
        self._index += 1
        result: dict[str, object] = {}
        self._space()
        if self._peek("}"):
            self._index += 1
            return result
        while True:
            if not self._peek('"'):
                self._payload()
            self._node()
            key = self._string(is_key=True)
            if key in result:
                self._payload()
            self._space()
            if not self._peek(":"):
                self._payload()
            self._index += 1
            self._space()
            result[key] = self._value(
                depth + 1,
                allow_boolean=(depth == 0 and key == "complete"),
            )
            self._space()
            if self._peek("}"):
                self._index += 1
                return result
            if not self._peek(","):
                self._payload()
            self._index += 1
            self._space()

    def _array(self, depth: int) -> list[object]:
        if depth > _MAX_JSON_DEPTH:
            self._capacity()
        self._node()
        self._index += 1
        result: list[object] = []
        self._space()
        if self._peek("]"):
            self._index += 1
            return result
        while True:
            result.append(self._value(depth + 1))
            self._space()
            if self._peek("]"):
                self._index += 1
                return result
            if not self._peek(","):
                self._payload()
            self._index += 1
            self._space()

    def _string(self, *, is_key: bool) -> str:
        self._index += 1
        characters: list[str] = []
        byte_length = 0
        limit = _MAX_KEY_BYTES if is_key else _MAX_STRING_BYTES
        while self._index < len(self._text):
            character = self._text[self._index]
            if character == '"':
                self._index += 1
                return "".join(characters)
            if character == "\\":
                self._index += 1
                if self._index >= len(self._text):
                    self._payload()
                escape = self._text[self._index]
                mapped = {
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "b": "\b",
                    "f": "\f",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }.get(escape)
                if mapped is None:
                    if escape != "u":
                        self._payload()
                    end = self._index + 5
                    digits = self._text[self._index + 1:end]
                    if (
                        len(digits) != 4
                        or any(
                            item not in "0123456789abcdefABCDEF"
                            for item in digits
                        )
                    ):
                        self._payload()
                    codepoint = int(digits, 16)
                    if 0xD800 <= codepoint <= 0xDFFF:
                        self._payload()
                    mapped = chr(codepoint)
                    self._index = end
                else:
                    self._index += 1
                character = mapped
            else:
                if ord(character) < 0x20:
                    self._payload()
                self._index += 1
            byte_length += len(character.encode("utf-8"))
            if byte_length > limit:
                self._capacity()
            characters.append(character)
        self._payload()

    def _integer(self) -> int:
        start = self._index
        if self._peek("-"):
            self._index += 1
        if (
            self._index >= len(self._text)
            or not ("0" <= self._text[self._index] <= "9")
        ):
            self._payload()
        first_digit = self._text[self._index]
        digit_count = 0
        while self._index < len(self._text):
            item = self._text[self._index]
            if not ("0" <= item <= "9"):
                if item not in " \t\r\n,]}":
                    self._payload()
                break
            digit_count += 1
            if digit_count > 19:
                self._capacity()
            if first_digit == "0" and digit_count > 1:
                self._payload()
            self._index += 1
        token = self._text[start:self._index]
        if _INTEGER_RE.fullmatch(token) is None:
            self._payload()
        value = int(token)
        if value < -_MAX_SIGNED_64 - 1 or value > _MAX_SIGNED_64:
            self._payload()
        return value

    def _consume(self, token: str) -> bool:
        if self._text.startswith(token, self._index):
            end = self._index + len(token)
            if (
                end < len(self._text)
                and self._text[end] not in " \t\r\n,]}"
            ):
                return False
            self._index = end
            return True
        return False

    def _peek(self, character: str) -> bool:
        return (
            self._index < len(self._text)
            and self._text[self._index] == character
        )


def _validate_boolean_positions(value: object) -> None:
    def walk(current: object, *, root: bool) -> None:
        if type(current) is dict:
            for key, item in current.items():
                if type(item) is bool:
                    if not (root and key == "complete"):
                        raise _JsonFailure(
                            KalshiCandidateRejectReasonV1.PAYLOAD_INVALID
                        )
                else:
                    walk(item, root=False)
        elif type(current) is list:
            for item in current:
                if type(item) is bool:
                    raise _JsonFailure(
                        KalshiCandidateRejectReasonV1.PAYLOAD_INVALID
                    )
                walk(item, root=False)

    walk(value, root=True)


_FRAME_KEYS: Final[dict[str, frozenset[str]]] = {
    "ORDERBOOK_SNAPSHOT": frozenset(
        {
            "type",
            "ticker",
            "sequence",
            "complete",
            "yes_bids",
            "no_bids",
        }
    ),
    "ORDERBOOK_DELTA": frozenset(
        {
            "type",
            "ticker",
            "sequence",
            "side",
            "price",
            "quantity_delta",
        }
    ),
    "MARKET_LIFECYCLE": frozenset(
        {"type", "ticker", "sequence", "status"}
    ),
    "PUBLIC_TRADE": frozenset(
        {
            "type",
            "ticker",
            "sequence",
            "trade_id",
            "side",
            "price",
            "quantity",
        }
    ),
}
_KIND_BY_TYPE: Final[dict[str, KalshiCandidateFrameKindV1]] = {
    kind.value: kind for kind in KalshiCandidateFrameKindV1
}


def _rejection(
    raw_sha256: str,
    scope: KalshiCandidateErrorScopeV1,
    reason: KalshiCandidateRejectReasonV1,
    *,
    ticker: str | None = None,
) -> KalshiCandidateFrameResultV1:
    return KalshiCandidateFrameResultV1._create(
        KalshiCandidateParseStatusV1.REJECTED,
        None,
        scope,
        reason,
        raw_sha256,
        ticker if scope is KalshiCandidateErrorScopeV1.TICKER else None,
        None,
        None,
    )


def _ticker_rejection(
    raw_sha256: str,
    ticker: str,
    reason: KalshiCandidateRejectReasonV1,
) -> KalshiCandidateFrameResultV1:
    return _rejection(
        raw_sha256,
        KalshiCandidateErrorScopeV1.TICKER,
        reason,
        ticker=ticker,
    )


def _parse_price(value: object) -> Decimal | None:
    if type(value) is not str or _PRICE_RE.fullmatch(value) is None:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    if not result.is_finite() or result < 0 or result > 1:
        return None
    return result


def _parse_quantity(
    value: object,
    *,
    signed: bool,
) -> Decimal | None:
    if type(value) is not str:
        return None
    negative = value.startswith("-")
    absolute = value[1:] if negative else value
    if (
        _QUANTITY_RE.fullmatch(absolute) is None
        or (negative and not signed)
    ):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    if (
        not result.is_finite()
        or abs(result) > _MAX_QUANTITY
        or result == 0
    ):
        return None
    return result


def _parse_snapshot(
    root: dict[str, object],
    ticker: str,
    opaque_sequence: str,
    raw_sha256: str,
) -> KalshiCandidateFrameResultV1:
    if (
        type(root["yes_bids"]) is not list
        or type(root["no_bids"]) is not list
    ):
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
    raw_yes = root["yes_bids"]
    raw_no = root["no_bids"]
    for raw_ladder in (raw_yes, raw_no):
        if len(raw_ladder) > _MAX_LADDER_LEVELS:
            return _ticker_rejection(
                raw_sha256,
                ticker,
                KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
            )
    for raw_ladder in (raw_yes, raw_no):
        for raw_level in raw_ladder:
            if (
                type(raw_level) is not dict
                or set(raw_level) != {"price", "quantity"}
            ):
                return _ticker_rejection(
                    raw_sha256,
                    ticker,
                    KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
                )
    if root["complete"] is not True:
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.BOOK_INVALID,
        )
    ladders: list[tuple[KalshiCandidateBookLevelV1, ...]] = []
    for raw_ladder in (raw_yes, raw_no):
        levels: list[KalshiCandidateBookLevelV1] = []
        previous: Decimal | None = None
        for raw_level in raw_ladder:
            price = _parse_price(raw_level["price"])
            quantity = _parse_quantity(
                raw_level["quantity"],
                signed=False,
            )
            if (
                price is None
                or quantity is None
                or (previous is not None and price >= previous)
            ):
                return _ticker_rejection(
                    raw_sha256,
                    ticker,
                    KalshiCandidateRejectReasonV1.BOOK_INVALID,
                )
            levels.append(
                KalshiCandidateBookLevelV1._create(price, quantity)
            )
            previous = price
        ladders.append(tuple(levels))
    if (
        ladders[0]
        and ladders[1]
        and ladders[0][0].price + ladders[1][0].price > 1
    ):
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.BOOK_INVALID,
        )
    frame = KalshiCandidateOrderbookSnapshotV1._create(
        ticker,
        opaque_sequence,
        True,
        ladders[0],
        ladders[1],
    )
    return KalshiCandidateFrameResultV1._create(
        KalshiCandidateParseStatusV1.ACCEPTED,
        KalshiCandidateFrameKindV1.ORDERBOOK_SNAPSHOT,
        None,
        None,
        raw_sha256,
        ticker,
        opaque_sequence,
        frame,
    )


def _parse_delta(
    root: dict[str, object],
    ticker: str,
    opaque_sequence: str,
    raw_sha256: str,
) -> KalshiCandidateFrameResultV1:
    side_value = root["side"]
    try:
        side = (
            KalshiCandidateSideV1(side_value)
            if type(side_value) is str
            else None
        )
    except ValueError:
        side = None
    price = _parse_price(root["price"])
    quantity = _parse_quantity(
        root["quantity_delta"],
        signed=True,
    )
    if side is None or price is None or quantity is None:
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.BOOK_INVALID,
        )
    frame = KalshiCandidateOrderbookDeltaV1._create(
        ticker,
        opaque_sequence,
        side,
        price,
        quantity,
    )
    return KalshiCandidateFrameResultV1._create(
        KalshiCandidateParseStatusV1.ACCEPTED,
        KalshiCandidateFrameKindV1.ORDERBOOK_DELTA,
        None,
        None,
        raw_sha256,
        ticker,
        opaque_sequence,
        frame,
    )


def _parse_lifecycle(
    root: dict[str, object],
    ticker: str,
    opaque_sequence: str,
    raw_sha256: str,
) -> KalshiCandidateFrameResultV1:
    status_value = root["status"]
    try:
        status = (
            KalshiCandidateMarketStatusV1(status_value)
            if type(status_value) is str
            else None
        )
    except ValueError:
        status = None
    if status is None:
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.PAYLOAD_INVALID,
        )
    frame = KalshiCandidateMarketLifecycleV1._create(
        ticker,
        opaque_sequence,
        status,
    )
    return KalshiCandidateFrameResultV1._create(
        KalshiCandidateParseStatusV1.ACCEPTED,
        KalshiCandidateFrameKindV1.MARKET_LIFECYCLE,
        None,
        None,
        raw_sha256,
        ticker,
        opaque_sequence,
        frame,
    )


def _parse_trade(
    root: dict[str, object],
    ticker: str,
    opaque_sequence: str,
    raw_sha256: str,
) -> KalshiCandidateFrameResultV1:
    trade_id = root["trade_id"]
    if (
        type(trade_id) is not str
        or _TICKER_RE.fullmatch(trade_id) is None
    ):
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
        )
    side_value = root["side"]
    try:
        side = (
            KalshiCandidateSideV1(side_value)
            if type(side_value) is str
            else None
        )
    except ValueError:
        side = None
    price = _parse_price(root["price"])
    quantity = _parse_quantity(root["quantity"], signed=False)
    if side is None or price is None or quantity is None:
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.PAYLOAD_INVALID,
        )
    frame = KalshiCandidatePublicTradeV1._create(
        ticker,
        opaque_sequence,
        trade_id,
        side,
        price,
        quantity,
    )
    return KalshiCandidateFrameResultV1._create(
        KalshiCandidateParseStatusV1.ACCEPTED,
        KalshiCandidateFrameKindV1.PUBLIC_TRADE,
        None,
        None,
        raw_sha256,
        ticker,
        opaque_sequence,
        frame,
    )


def classify_kalshi_synthetic_frame(
    payload: bytes,
    *,
    expected_raw_sha256: str,
) -> KalshiCandidateFrameResultV1:
    if type(payload) is not bytes:
        raise TypeError("payload")
    if type(expected_raw_sha256) is not str:
        raise TypeError("expected_raw_sha256")
    raw_sha256 = sha256(payload).hexdigest()
    if (
        _SHA256_RE.fullmatch(expected_raw_sha256) is None
        or expected_raw_sha256 != raw_sha256
    ):
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
        )
    if len(payload) > _MAX_RAW_BYTES:
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )
    try:
        root = _BoundedJsonParser(payload).parse()
        _validate_boolean_positions(root)
    except _JsonFailure as error:
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            error.reason,
        )
    if type(root) is not dict:
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
    frame_type = root.get("type")
    if (
        type(frame_type) is not str
        or frame_type not in _KIND_BY_TYPE
    ):
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
    ticker = root.get("ticker")
    opaque_sequence = root.get("sequence")
    if (
        type(ticker) is not str
        or _TICKER_RE.fullmatch(ticker) is None
        or type(opaque_sequence) is not str
        or _OPAQUE_RE.fullmatch(opaque_sequence) is None
    ):
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            KalshiCandidateRejectReasonV1.IDENTITY_INVALID,
        )
    if set(root) != _FRAME_KEYS[frame_type]:
        return _ticker_rejection(
            raw_sha256,
            ticker,
            KalshiCandidateRejectReasonV1.SCHEMA_UNKNOWN,
        )
    try:
        if frame_type == "ORDERBOOK_SNAPSHOT":
            return _parse_snapshot(
                root,
                ticker,
                opaque_sequence,
                raw_sha256,
            )
        if frame_type == "ORDERBOOK_DELTA":
            return _parse_delta(
                root,
                ticker,
                opaque_sequence,
                raw_sha256,
            )
        if frame_type == "MARKET_LIFECYCLE":
            return _parse_lifecycle(
                root,
                ticker,
                opaque_sequence,
                raw_sha256,
            )
        return _parse_trade(
            root,
            ticker,
            opaque_sequence,
            raw_sha256,
        )
    except _CanonicalCapacity:
        return _rejection(
            raw_sha256,
            KalshiCandidateErrorScopeV1.CONNECTION,
            KalshiCandidateRejectReasonV1.CAPACITY_EXCEEDED,
        )
