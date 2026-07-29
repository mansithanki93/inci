"""Pure Phase-1 reducer and deterministic trace chain for Tennis v1."""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json

from .canonical import canonical_json_bytes
from .codec import (
    canonical_record_sha256,
    decode_record,
    encode_record,
)
from .events import (
    DerivedDraft,
    PersistedEvent,
    RecordKind,
    SessionManifest,
)
from .state import FoundationState, canonical_state_bytes


TRACE_SEED_DOMAIN = b"INCI-TRACE-V1\0"
TRACE_STEP_DOMAIN = b"INCI-TRACE-STEP-V1\0"


class ReducerValidationError(ValueError):
    """Raised when a reducer or trace input violates the v1 contract."""


@dataclass(frozen=True, slots=True)
class Reduction:
    state: FoundationState
    outputs: tuple[DerivedDraft, ...]

    def __post_init__(self) -> None:
        canonical_state_bytes(self.state)
        if type(self.outputs) is not tuple:
            raise TypeError("outputs: exact_tuple_required")
        if any(type(item) is not DerivedDraft for item in self.outputs):
            raise TypeError("outputs: exact_DerivedDraft_items_required")


def _require_valid_event(event: object) -> PersistedEvent:
    if type(event) is not PersistedEvent:
        raise TypeError("exact PersistedEvent required")
    try:
        metadata, payload = encode_record(event)
        decoded = decode_record(metadata, payload)
    except (TypeError, ValueError) as error:
        raise ReducerValidationError("persisted_event_invalid") from error
    if decoded != event:
        raise ReducerValidationError("persisted_event_invalid")
    return event


def reduce_event(
    state: FoundationState,
    event: PersistedEvent,
) -> Reduction:
    canonical_state_bytes(state)
    raw = _require_valid_event(event)
    if raw.record_kind is not RecordKind.RAW:
        raise ReducerValidationError("raw_record_required")
    if raw.session_id != state.session_id:
        raise ReducerValidationError("reducer_session_mismatch")
    if raw.ingest_seq <= state.last_applied_raw_seq:
        raise ReducerValidationError("raw_sequence_not_increasing")

    epochs = {
        (source_kind, source_id): connection_epoch
        for source_kind, source_id, connection_epoch in state.source_epochs
    }
    source_key = (raw.source_kind, raw.source_id)
    prior_epoch = epochs.get(source_key)
    if prior_epoch is not None and raw.connection_epoch < prior_epoch:
        raise ReducerValidationError("source_epoch_regression")
    epochs[source_key] = raw.connection_epoch
    source_epochs = tuple(
        (
            source_kind,
            source_id,
            epochs[(source_kind, source_id)],
        )
        for source_kind, source_id in sorted(
            epochs,
            key=lambda item: (item[0].value, item[1]),
        )
    )

    output = DerivedDraft(
        event_type="raw_accepted",
        event_version=1,
        payload_encoding="canonical-json-v1",
        payload=canonical_json_bytes(
            {
                "input_event_type": raw.event_type,
                "input_payload_sha256": raw.payload_sha256,
                "parent_ingest_seq": raw.ingest_seq,
                "source_id": raw.source_id,
            }
        ),
    )
    new_state = FoundationState(
        session_id=state.session_id,
        last_applied_raw_seq=raw.ingest_seq,
        raw_count=state.raw_count + 1,
        derived_count=state.derived_count + 1,
        source_epochs=source_epochs,
    )
    return Reduction(state=new_state, outputs=(output,))


def initial_trace(session_start: PersistedEvent) -> bytes:
    start = _require_valid_event(session_start)
    if (
        start.ingest_seq != 1
        or start.record_kind is not RecordKind.CONTROL
        or start.event_type != "SESSION_START"
    ):
        raise ReducerValidationError("sequence_one_session_start_required")
    try:
        raw_manifest = json.loads(start.payload.decode("ascii"))
        if type(raw_manifest) is not dict:
            raise ReducerValidationError("session_start_manifest_invalid")
        session_manifest = SessionManifest(**raw_manifest)
        canonical_manifest = canonical_json_bytes(
            {
                item.name: getattr(session_manifest, item.name)
                for item in fields(SessionManifest)
            }
        )
        if canonical_manifest != start.payload:
            raise ReducerValidationError("session_start_manifest_invalid")
    except ReducerValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ReducerValidationError(
            "session_start_manifest_invalid"
        ) from None
    if (
        session_manifest.session_id != start.session_id
        or start.source_entity_id != session_manifest.session_id
        or start.local_wall_ns != session_manifest.created_wall_ns
        or start.local_monotonic_ns != 0
    ):
        raise ReducerValidationError("session_start_manifest_binding_invalid")
    return hashlib.sha256(
        TRACE_SEED_DOMAIN
        + bytes.fromhex(canonical_record_sha256(start))
    ).digest()


def next_trace(
    prior_trace: bytes,
    raw: PersistedEvent,
    derived: tuple[PersistedEvent, ...],
    state: FoundationState,
) -> bytes:
    if type(prior_trace) is not bytes:
        raise TypeError("prior_trace: exact_bytes_required")
    if len(prior_trace) != hashlib.sha256().digest_size:
        raise ReducerValidationError("prior_trace_length_invalid")
    parent = _require_valid_event(raw)
    if parent.record_kind is not RecordKind.RAW:
        raise ReducerValidationError("trace_raw_record_required")
    canonical_state_bytes(state)
    if (
        state.session_id != parent.session_id
        or state.last_applied_raw_seq != parent.ingest_seq
    ):
        raise ReducerValidationError("trace_state_binding_invalid")
    state_epochs = {
        (source_kind, source_id): connection_epoch
        for source_kind, source_id, connection_epoch in state.source_epochs
    }
    if state_epochs.get((parent.source_kind, parent.source_id), -1) < (
        parent.connection_epoch
    ):
        raise ReducerValidationError("trace_state_epoch_invalid")
    if type(derived) is not tuple:
        raise TypeError("derived: exact_tuple_required")
    output_hashes: list[dict[str, str]] = []
    seen_sequences: set[int] = set()
    for candidate in derived:
        item = _require_valid_event(candidate)
        if (
            item.record_kind is not RecordKind.DERIVED
            or item.session_id != parent.session_id
            or item.parent_ingest_seq != parent.ingest_seq
        ):
            raise ReducerValidationError("trace_derived_binding_invalid")
        if item.ingest_seq in seen_sequences:
            raise ReducerValidationError("trace_derived_sequence_duplicate")
        seen_sequences.add(item.ingest_seq)
        output_hashes.append(
            {"record_sha256": canonical_record_sha256(item)}
        )

    entry = canonical_json_bytes(
        {
            "v": 1,
            "raw_record_sha256": canonical_record_sha256(parent),
            "outputs": output_hashes,
            "state_sha256": hashlib.sha256(
                canonical_state_bytes(state)
            ).hexdigest(),
        }
    )
    return hashlib.sha256(
        TRACE_STEP_DOMAIN
        + prior_trace
        + len(entry).to_bytes(8, "big")
        + entry
    ).digest()
