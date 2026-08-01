"""Canonical metadata and durable payload encoding for Tennis v1 records."""

from __future__ import annotations

from dataclasses import fields
import hashlib
import json

from .canonical import CanonicalJsonError, canonical_json_bytes
from .events import (
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SessionManifest,
    SourceKind,
)


CANONICAL_RECORD_DOMAIN = b"INCI-CANONICAL-RECORD-V1\0"
METADATA_KEYS = frozenset(
    field.name for field in fields(PersistedEvent) if field.name != "payload"
)
SESSION_MANIFEST_KEYS = frozenset(
    field.name for field in fields(SessionManifest)
)


class RecordCodecError(ValueError):
    """Raised when canonical record bytes fail strict decoding."""


def _metadata_projection(event: PersistedEvent) -> dict[str, object]:
    return {
        "journal_version": event.journal_version,
        "record_kind": event.record_kind.value,
        "ingest_seq": event.ingest_seq,
        "session_id": event.session_id,
        "event_type": event.event_type,
        "event_version": event.event_version,
        "source_kind": event.source_kind.value,
        "source_id": event.source_id,
        "source_entity_id": event.source_entity_id,
        "endpoint_id": event.endpoint_id,
        "endpoint_state": event.endpoint_state.value,
        "channel_id": event.channel_id,
        "channel_state": event.channel_state.value,
        "request_id": event.request_id,
        "request_id_state": event.request_id_state.value,
        "source_wall_ns": event.source_wall_ns,
        "source_generated_ns": event.source_generated_ns,
        "local_wall_ns": event.local_wall_ns,
        "local_monotonic_ns": event.local_monotonic_ns,
        "clock_uncertainty_ns": event.clock_uncertainty_ns,
        "connection_epoch": event.connection_epoch,
        "provider_sequence": event.provider_sequence,
        "parent_ingest_seq": event.parent_ingest_seq,
        "content_type": event.content_type,
        "payload_encoding": event.payload_encoding,
        "payload_transform": event.payload_transform,
        "retention_delete_by_ns": event.retention_delete_by_ns,
        "payload_sha256": hashlib.sha256(event.payload).hexdigest(),
    }


def canonical_metadata(event: PersistedEvent) -> bytes:
    if type(event) is not PersistedEvent:
        raise TypeError("canonical metadata requires PersistedEvent")
    return canonical_json_bytes(_metadata_projection(event))


def canonical_record_sha256(event: PersistedEvent) -> str:
    metadata, payload = encode_record(event)
    return hashlib.sha256(
        CANONICAL_RECORD_DOMAIN
        + len(metadata).to_bytes(8, "big")
        + metadata
        + len(payload).to_bytes(8, "big")
        + payload
    ).hexdigest()


def encode_record(event: PersistedEvent) -> tuple[bytes, bytes]:
    if type(event) is not PersistedEvent:
        raise TypeError("record encoding requires PersistedEvent")
    if event.payload_sha256 != hashlib.sha256(event.payload).hexdigest():
        raise RecordCodecError("payload_digest_mismatch")
    return canonical_metadata(event), event.payload


def _reject_float(_: str) -> object:
    raise RecordCodecError("floating_point_not_permitted")


def _reject_constant(_: str) -> object:
    raise RecordCodecError("nonstandard_constant_not_permitted")


def _duplicate_free_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecordCodecError("duplicate_json_key")
        result[key] = value
    return result


def _strict_object(
    content: bytes, expected_keys: frozenset[str], label: str
) -> dict[str, object]:
    if type(content) is not bytes:
        raise TypeError(f"{label}: exact_bytes_required")
    if content.startswith(b"\xef\xbb\xbf"):
        raise RecordCodecError(f"{label}: utf8_bom_not_permitted")
    try:
        decoded = content.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_free_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except RecordCodecError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RecordCodecError(f"{label}: invalid_strict_json") from None
    if type(value) is not dict or set(value) != expected_keys:
        raise RecordCodecError(f"{label}: schema_keys_mismatch")
    try:
        canonical = canonical_json_bytes(value)
    except CanonicalJsonError as error:
        raise RecordCodecError(f"{label}: unsupported_json_value") from error
    if canonical != content:
        raise RecordCodecError(f"{label}: noncanonical_json")
    return value


def _decode_enum(value: object, enum_type: type, field_name: str):
    if type(value) is not str:
        raise RecordCodecError(f"{field_name}: invalid_enum")
    try:
        return enum_type(value)
    except ValueError:
        raise RecordCodecError(f"{field_name}: invalid_enum") from None


def _require_session_start_payload(event: PersistedEvent) -> None:
    if event.record_kind is not RecordKind.CONTROL or event.event_type != "SESSION_START":
        return
    raw = _strict_object(event.payload, SESSION_MANIFEST_KEYS, "session_manifest")
    try:
        SessionManifest(**raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise RecordCodecError("session_manifest: invalid_contract") from error
    if raw["research_evaluable"] is not False:
        raise RecordCodecError(
            "session_manifest: research_evaluable_must_be_literal_false"
        )


def decode_record(metadata: bytes, payload: bytes) -> PersistedEvent:
    if type(payload) is not bytes:
        raise TypeError("payload: exact_bytes_required")
    raw = _strict_object(metadata, METADATA_KEYS, "record_metadata")
    if raw["payload_sha256"] != hashlib.sha256(payload).hexdigest():
        raise RecordCodecError("payload_digest_mismatch")
    try:
        event = PersistedEvent(
            **{
                **raw,
                "record_kind": _decode_enum(
                    raw["record_kind"], RecordKind, "record_kind"
                ),
                "source_kind": _decode_enum(
                    raw["source_kind"], SourceKind, "source_kind"
                ),
                "endpoint_state": _decode_enum(
                    raw["endpoint_state"], ProvenanceState, "endpoint_state"
                ),
                "channel_state": _decode_enum(
                    raw["channel_state"], ProvenanceState, "channel_state"
                ),
                "request_id_state": _decode_enum(
                    raw["request_id_state"], ProvenanceState, "request_id_state"
                ),
                "payload": payload,
            }
        )
    except RecordCodecError:
        raise
    except (TypeError, ValueError) as error:
        raise RecordCodecError("record_metadata: invalid_contract") from error
    _require_session_start_payload(event)
    return event
