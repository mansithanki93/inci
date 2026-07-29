"""Immutable event, capture, and session value contracts for Tennis v1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from types import MappingProxyType
from typing import Literal, Protocol
import uuid


SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")
CONTENT_TYPE = re.compile(
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,127}\Z"
)
PAYLOAD_ENCODING = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
CAPTURE_PAYLOAD_TRANSFORMS = frozenset(
    {
        "identity-public-market-v1",
        "json-secret-redaction-v1",
        "sanitized-transport-error-v1",
    }
)
RECORD_PAYLOAD_TRANSFORMS = CAPTURE_PAYLOAD_TRANSFORMS | {
    "derived-canonical-v1"
}


class EventValidationError(ValueError):
    """Raised when an immutable event value violates the v1 contract."""


class SourceKind(str, Enum):
    PROVIDER = "provider"
    KALSHI = "kalshi"
    TIMER = "timer"
    SYSTEM = "system"


class RecordKind(str, Enum):
    RAW = "raw"
    DERIVED = "derived"
    CONTROL = "control"


class ProvenanceState(str, Enum):
    ABSENT = "absent"
    SAFE_ORIGINAL = "safe_original"
    REDACTED = "redacted"


@dataclass(frozen=True, slots=True)
class ControlRecordContract:
    content_type: str
    event_version: int = 1
    payload_encoding: str = "canonical-json-v1"
    payload_transform: str = "identity-public-market-v1"


CONTROL_RECORD_CONTRACTS = MappingProxyType(
    {
        "SESSION_START": ControlRecordContract(
            "application/vnd.inci.session-manifest+json"
        ),
        "SESSION_HALT": ControlRecordContract(
            "application/vnd.inci.session-terminal+json"
        ),
    }
)


def _exact_nonnegative_integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}: nonnegative_integer_required")
    if value < 0:
        raise EventValidationError(f"{field_name}: nonnegative_integer_required")
    return value


def _optional_nonnegative_integer(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _exact_nonnegative_integer(value, field_name)


def _safe_identifier(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}: safe_identifier_required")
    if SAFE_IDENTIFIER.fullmatch(value) is None:
        raise EventValidationError(f"{field_name}: safe_identifier_required")
    return value


def _optional_safe_identifier(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _safe_identifier(value, field_name)


def _sha256(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}: sha256_required")
    if SHA256_HEX.fullmatch(value) is None:
        raise EventValidationError(f"{field_name}: sha256_required")
    return value


def _enum(value: object, enum_type: type[Enum], field_name: str):
    if type(value) is not enum_type:
        raise TypeError(f"{field_name}: exact_enum_required")
    return value


def _validate_provenance(
    value: object, state: object, field_name: str
) -> tuple[str | None, ProvenanceState]:
    _enum(state, ProvenanceState, f"{field_name}_state")
    if state is ProvenanceState.ABSENT:
        if value is not None:
            raise EventValidationError(f"{field_name}: absent_value_mismatch")
    elif state is ProvenanceState.REDACTED:
        if value != "<redacted>" or type(value) is not str:
            raise EventValidationError(f"{field_name}: redacted_value_mismatch")
    else:
        _safe_identifier(value, field_name)
    return value, state  # type: ignore[return-value]


def _validate_content_type(value: object) -> str:
    if type(value) is not str:
        raise TypeError("content_type: normalized_media_type_required")
    if CONTENT_TYPE.fullmatch(value) is None:
        raise EventValidationError("content_type: normalized_media_type_required")
    return value


def _validate_payload_encoding(value: object) -> str:
    if type(value) is not str:
        raise TypeError("payload_encoding: safe_identifier_required")
    if PAYLOAD_ENCODING.fullmatch(value) is None:
        raise EventValidationError("payload_encoding: safe_identifier_required")
    return value


def _validate_session_id(value: object) -> str:
    text = _safe_identifier(value, "session_id")
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        raise EventValidationError("session_id: canonical_uuid_required") from None
    if str(parsed) != text:
        raise EventValidationError("session_id: canonical_uuid_required")
    return text


@dataclass(frozen=True, slots=True, init=False)
class ProvenanceEvidence:
    value: str | None
    state: ProvenanceState

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use a safe provenance factory")


@dataclass(frozen=True, slots=True)
class SessionManifest:
    schema_version: int
    session_id: str
    created_wall_ns: int
    config_file_sha256: str
    config_canonical_sha256: str
    code_sha256: str
    research_request_sha256: str
    provider_id: str
    product_tier: str
    source_lineage_id: str
    provider_manifest_file_sha256: str
    provider_manifest_canonical_sha256: str
    entitlement_id_sha256: str
    terms_version: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    adapter_code_sha256: str
    auth_contract_sha256: str
    quota_contract_sha256: str
    session_end_ns: int
    required_retention_until_ns: int
    access_expires_at_ns: int
    analysis_expires_at_ns: int
    research_evaluable: Literal[False]

    def __post_init__(self) -> None:
        if type(self) is not SessionManifest:
            raise TypeError("exact SessionManifest required")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise EventValidationError("session_manifest: unsupported_schema_version")
        _validate_session_id(self.session_id)
        for field_name in (
            "created_wall_ns",
            "session_end_ns",
            "required_retention_until_ns",
            "access_expires_at_ns",
            "analysis_expires_at_ns",
        ):
            _exact_nonnegative_integer(getattr(self, field_name), field_name)
        if not (
            self.created_wall_ns < self.session_end_ns
            <= self.access_expires_at_ns
            <= self.analysis_expires_at_ns
            and self.session_end_ns < self.required_retention_until_ns
            <= self.analysis_expires_at_ns
        ):
            raise EventValidationError("session_manifest: invalid_time_window")
        for field_name in (
            "config_file_sha256",
            "config_canonical_sha256",
            "code_sha256",
            "research_request_sha256",
            "provider_manifest_file_sha256",
            "provider_manifest_canonical_sha256",
            "entitlement_id_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
            "adapter_code_sha256",
            "auth_contract_sha256",
            "quota_contract_sha256",
        ):
            _sha256(getattr(self, field_name), field_name)
        for field_name in (
            "provider_id",
            "product_tier",
            "source_lineage_id",
            "terms_version",
        ):
            _safe_identifier(getattr(self, field_name), field_name)
        if self.research_evaluable is not False:
            raise EventValidationError(
                "session_manifest: research_evaluable_must_be_literal_false"
            )


class SessionCaptureAuthorizer(Protocol):
    @property
    def session_manifest(self) -> SessionManifest: ...

    def authorize_capture(
        self, authority: "CaptureAuthority", captured: "CapturedInput"
    ) -> None: ...


@dataclass(frozen=True, slots=True, init=False)
class CapturedInput:
    session_id: str
    event_type: str
    event_version: int
    source_kind: SourceKind
    source_id: str
    source_entity_id: str
    endpoint_id: str | None
    endpoint_state: ProvenanceState
    channel_id: str | None
    channel_state: ProvenanceState
    request_id: str | None
    request_id_state: ProvenanceState
    source_wall_ns: int | None
    source_generated_ns: int | None
    local_wall_ns: int
    local_monotonic_ns: int
    clock_uncertainty_ns: int
    connection_epoch: int
    provider_sequence: str | None
    content_type: str
    payload_encoding: str
    payload_transform: str
    retention_delete_by_ns: int | None
    payload: bytes = field(repr=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CapturedInput must be created by a safe capture factory")


@dataclass(frozen=True, slots=True, init=False)
class CaptureAuthority:
    session_id: str
    source_kind: SourceKind
    source_id: str
    source_entity_id: str
    endpoint_id: str | None
    endpoint_state: ProvenanceState
    channel_id: str | None
    channel_state: ProvenanceState
    connection_epoch: int
    _session_authorizer: SessionCaptureAuthorizer = field(
        repr=False, compare=False
    )
    _wall_clock_ns: Callable[[], int] = field(repr=False, compare=False)
    _monotonic_clock_ns: Callable[[], int] = field(repr=False, compare=False)
    _clock_uncertainty_ns: Callable[[], int] = field(
        repr=False, compare=False
    )
    _allowed_content_types: tuple[str, ...] = field(repr=False)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("CaptureAuthority must be issued by the session runtime")


@dataclass(frozen=True, slots=True)
class DerivedDraft:
    event_type: str
    event_version: int
    payload_encoding: str
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _safe_identifier(self.event_type, "event_type")
        if type(self.event_version) is not int:
            raise TypeError("event_version: positive_integer_required")
        if self.event_version < 1:
            raise EventValidationError("event_version: positive_integer_required")
        _validate_payload_encoding(self.payload_encoding)
        if type(self.payload) is not bytes:
            raise TypeError("payload: exact_bytes_required")


@dataclass(frozen=True, slots=True)
class PersistedEvent:
    journal_version: int
    record_kind: RecordKind
    ingest_seq: int
    session_id: str
    event_type: str
    event_version: int
    source_kind: SourceKind
    source_id: str
    source_entity_id: str
    endpoint_id: str | None
    endpoint_state: ProvenanceState
    channel_id: str | None
    channel_state: ProvenanceState
    request_id: str | None
    request_id_state: ProvenanceState
    source_wall_ns: int | None
    source_generated_ns: int | None
    local_wall_ns: int
    local_monotonic_ns: int
    clock_uncertainty_ns: int
    connection_epoch: int
    provider_sequence: str | None
    parent_ingest_seq: int | None
    content_type: str
    payload_encoding: str
    payload_transform: str
    retention_delete_by_ns: int | None
    payload_sha256: str
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not PersistedEvent:
            raise TypeError("exact PersistedEvent required")
        if type(self.journal_version) is not int or self.journal_version != 1:
            raise EventValidationError("journal_version: unsupported_version")
        _enum(self.record_kind, RecordKind, "record_kind")
        if type(self.ingest_seq) is not int:
            raise TypeError("ingest_seq: positive_integer_required")
        if self.ingest_seq < 1:
            raise EventValidationError("ingest_seq: positive_integer_required")
        _validate_session_id(self.session_id)
        for field_name in ("event_type", "source_id", "source_entity_id"):
            _safe_identifier(getattr(self, field_name), field_name)
        if type(self.event_version) is not int:
            raise TypeError("event_version: positive_integer_required")
        if self.event_version < 1:
            raise EventValidationError("event_version: positive_integer_required")
        _enum(self.source_kind, SourceKind, "source_kind")
        _validate_provenance(self.endpoint_id, self.endpoint_state, "endpoint_id")
        _validate_provenance(self.channel_id, self.channel_state, "channel_id")
        _validate_provenance(self.request_id, self.request_id_state, "request_id")
        _optional_nonnegative_integer(self.source_wall_ns, "source_wall_ns")
        _optional_nonnegative_integer(
            self.source_generated_ns, "source_generated_ns"
        )
        for field_name in (
            "local_wall_ns",
            "local_monotonic_ns",
            "clock_uncertainty_ns",
            "connection_epoch",
        ):
            _exact_nonnegative_integer(getattr(self, field_name), field_name)
        _optional_safe_identifier(self.provider_sequence, "provider_sequence")
        _validate_content_type(self.content_type)
        _validate_payload_encoding(self.payload_encoding)
        _safe_identifier(self.payload_transform, "payload_transform")
        if self.payload_transform not in RECORD_PAYLOAD_TRANSFORMS:
            raise EventValidationError("payload_transform: unknown_transform")
        if type(self.payload) is not bytes:
            raise TypeError("payload: exact_bytes_required")
        _sha256(self.payload_sha256, "payload_sha256")
        if not hashlib.sha256(self.payload).hexdigest() == self.payload_sha256:
            raise EventValidationError("payload_sha256: payload_digest_mismatch")

        if self.record_kind is RecordKind.DERIVED:
            if (
                type(self.parent_ingest_seq) is not int
                or self.parent_ingest_seq < 1
                or self.parent_ingest_seq >= self.ingest_seq
            ):
                raise EventValidationError(
                    "parent_ingest_seq: derived_parent_must_precede"
                )
            if (
                self.content_type != "application/vnd.inci.derived+json"
                or self.payload_transform != "derived-canonical-v1"
            ):
                raise EventValidationError("derived_record: contract_mismatch")
        elif self.parent_ingest_seq is not None:
            raise EventValidationError(
                "parent_ingest_seq: only_derived_records_have_parents"
            )

        _optional_nonnegative_integer(
            self.retention_delete_by_ns, "retention_delete_by_ns"
        )
        provider_bytes = (
            self.source_kind is SourceKind.PROVIDER
            and self.record_kind in (RecordKind.RAW, RecordKind.DERIVED)
        )
        if provider_bytes:
            if (
                self.retention_delete_by_ns is None
                or self.retention_delete_by_ns <= self.local_wall_ns
            ):
                raise EventValidationError(
                    "retention_delete_by_ns: provider_future_deadline_required"
                )
        elif self.retention_delete_by_ns is not None:
            raise EventValidationError(
                "retention_delete_by_ns: nonprovider_deadline_forbidden"
            )

        if self.record_kind is RecordKind.CONTROL:
            contract = CONTROL_RECORD_CONTRACTS.get(self.event_type)
            if (
                contract is None
                or self.source_kind is not SourceKind.SYSTEM
                or self.source_id != "tennis-v1"
                or self.source_entity_id != self.session_id
                or self.endpoint_id is not None
                or self.endpoint_state is not ProvenanceState.ABSENT
                or self.channel_id != "session-control"
                or self.channel_state is not ProvenanceState.SAFE_ORIGINAL
                or self.request_id is not None
                or self.request_id_state is not ProvenanceState.ABSENT
                or self.source_wall_ns is not None
                or self.source_generated_ns is not None
                or self.clock_uncertainty_ns != 0
                or self.connection_epoch != 0
                or self.provider_sequence is not None
                or self.retention_delete_by_ns is not None
                or self.event_version != contract.event_version
                or self.content_type != contract.content_type
                or self.payload_encoding != contract.payload_encoding
                or self.payload_transform != contract.payload_transform
            ):
                raise EventValidationError("control_record: contract_mismatch")
        elif self.record_kind is RecordKind.RAW:
            if self.payload_transform not in CAPTURE_PAYLOAD_TRANSFORMS:
                raise EventValidationError("raw_record: transform_mismatch")
            if (
                self.payload_transform == "sanitized-transport-error-v1"
                and self.content_type
                != "application/vnd.inci.transport-error+json"
            ):
                raise EventValidationError("raw_record: transport_contract_mismatch")
