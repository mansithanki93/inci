"""Frozen deterministic foundation state for the Tennis v1 event core."""

from __future__ import annotations

from dataclasses import dataclass

from .canonical import canonical_json_bytes
from .events import (
    SourceKind,
    _exact_nonnegative_integer,
    _safe_identifier,
    _validate_session_id,
)


class StateValidationError(ValueError):
    """Raised when foundation state violates its canonical v1 contract."""


@dataclass(frozen=True, slots=True)
class FoundationState:
    session_id: str
    last_applied_raw_seq: int
    raw_count: int
    derived_count: int
    source_epochs: tuple[tuple[SourceKind, str, int], ...]

    def __post_init__(self) -> None:
        _validate_state(self)


def _state_error(reason: str) -> StateValidationError:
    return StateValidationError(reason)


def _validate_state(state: FoundationState) -> None:
    if type(state) is not FoundationState:
        raise TypeError("exact FoundationState required")
    try:
        _validate_session_id(state.session_id)
        last_applied = _exact_nonnegative_integer(
            state.last_applied_raw_seq,
            "last_applied_raw_seq",
        )
        raw_count = _exact_nonnegative_integer(state.raw_count, "raw_count")
        derived_count = _exact_nonnegative_integer(
            state.derived_count,
            "derived_count",
        )
    except (TypeError, ValueError) as error:
        raise _state_error("foundation_state_scalar_invalid") from error
    if type(state.source_epochs) is not tuple:
        raise TypeError("source_epochs: exact_tuple_required")

    normalized: list[tuple[SourceKind, str, int]] = []
    seen: set[tuple[SourceKind, str]] = set()
    for entry in state.source_epochs:
        if type(entry) is not tuple or len(entry) != 3:
            raise _state_error("source_epochs_entry_invalid")
        source_kind, source_id, connection_epoch = entry
        if type(source_kind) is not SourceKind:
            raise TypeError("source_epochs: exact_SourceKind_required")
        try:
            checked_source_id = _safe_identifier(source_id, "source_id")
            checked_epoch = _exact_nonnegative_integer(
                connection_epoch,
                "connection_epoch",
            )
        except (TypeError, ValueError) as error:
            raise _state_error("source_epochs_entry_invalid") from error
        key = (source_kind, checked_source_id)
        if key in seen:
            raise _state_error("source_epochs_duplicate_key")
        seen.add(key)
        normalized.append((source_kind, checked_source_id, checked_epoch))

    expected_order = sorted(
        normalized,
        key=lambda item: (item[0].value, item[1]),
    )
    if normalized != expected_order:
        raise _state_error("source_epochs_not_sorted")

    if raw_count != derived_count:
        raise _state_error("foundation_state_count_mismatch")
    if raw_count == 0:
        if last_applied != 0 or normalized:
            raise _state_error("foundation_state_empty_invariant")
    elif (
        last_applied == 0
        or raw_count > last_applied
        or not normalized
        or len(normalized) > raw_count
    ):
        raise _state_error("foundation_state_sequence_invariant")


def initial_state(session_id: str) -> FoundationState:
    return FoundationState(
        session_id=session_id,
        last_applied_raw_seq=0,
        raw_count=0,
        derived_count=0,
        source_epochs=(),
    )


def canonical_state_bytes(state: FoundationState) -> bytes:
    _validate_state(state)
    return canonical_json_bytes(
        {
            "state_version": 1,
            "session_id": state.session_id,
            "last_applied_raw_seq": state.last_applied_raw_seq,
            "raw_count": state.raw_count,
            "derived_count": state.derived_count,
            "source_epochs": [
                {
                    "source_kind": source_kind.value,
                    "source_id": source_id,
                    "connection_epoch": connection_epoch,
                }
                for source_kind, source_id, connection_epoch in state.source_epochs
            ],
        }
    )
