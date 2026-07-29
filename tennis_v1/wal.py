"""Capability-only framed write-ahead journal for Tennis v1 events."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import re
import struct
from typing import Generator, Iterator

from .canonical import CanonicalJsonError, canonical_json_bytes
from .capture import (
    MAX_CAPTURE_BYTES,
    CaptureValidationError,
    validate_captured_input,
)
from .codec import RecordCodecError, decode_record, encode_record
from .events import (
    CapturedInput,
    DerivedDraft,
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SessionManifest,
    SourceKind,
)
from .retention import (
    ProviderWalReadCapability,
    ProviderWalWriteCapability,
    RESERVE_BYTES,
    RetentionPrewriteCapacityError,
    RetentionCoordinator,
    _ack_provider_wal_clean_terminal,
    _claim_provider_wal_reader,
    _claim_provider_wal_writer,
    _claim_provider_wal_runtime,
)
from .session import (
    canonical_session_manifest_bytes,
    session_manifest_sha256,
)


FILE_PREFIX = struct.Struct(">8sHHI")
FILE_MAGIC = b"INCIWAL\x00"
FILE_VERSION = 1
FILE_FLAGS = 0

FRAME_PREFIX = struct.Struct(">4sBBHQQII")
FRAME_MAGIC = b"EVT1"
FRAME_VERSION = 1
FRAME_FLAGS = 0
FRAME_KIND = {
    RecordKind.RAW: 1,
    RecordKind.DERIVED: 2,
    RecordKind.CONTROL: 3,
}
FRAME_TRAILER = struct.Struct(">Q4s")
TRAILER_MAGIC = b"1TVE"
FRAME_DIGEST_DOMAIN = b"INCI-FRAME-V1\0"
MAX_FRAME_BYTES = 16 * 1024 * 1024
MIN_FREE_BYTES = 64 * 1024 * 1024
DISK_HALT_RESERVE_BYTES = RESERVE_BYTES
_CLEAN_TERMINAL_REASONS = frozenset({"operator_stop", "session_end"})
_HALTED_TERMINAL_REASONS = frozenset(
    {
        "operator_halt",
        "initialization_failure",
        "capture_contract_violation",
        "provider_gate_denied",
        "retention_global_halt",
        "disk_low",
        "reducer_exception",
        "derived_validation_failure",
        "trace_exception",
        "ingress_backpressure",
        "ingress_owner_unresponsive",
    }
)

_KIND_FROM_NUMBER = {value: key for key, value in FRAME_KIND.items()}
_FRAME_FIXED_BYTES = FRAME_PREFIX.size + 32 + FRAME_TRAILER.size
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL_KEYS = frozenset(
    {
        "terminal_version",
        "clean",
        "reason",
        "trace_sha256",
        "final_state_sha256",
        "record_count_before_terminal",
        "raw_count",
        "derived_count",
        "last_applied_raw_seq",
        "config_file_sha256",
        "config_canonical_sha256",
        "code_sha256",
        "session_manifest_sha256",
        "provider_manifest_file_sha256",
        "provider_manifest_canonical_sha256",
        "entitlement_id_sha256",
        "permission_artifact_sha256",
        "qualification_artifact_sha256",
        "qualification_trace_sha256",
        "adapter_code_sha256",
        "auth_contract_sha256",
        "quota_contract_sha256",
        "required_retention_until_ns",
        "research_evaluable",
    }
)
_SESSION_MANIFEST_KEYS = frozenset(
    item.name for item in fields(SessionManifest)
)


class ScanIssue(str, Enum):
    MISSING_TERMINAL = "missing_terminal"
    HALTED_TERMINAL = "halted_terminal"
    TORN_TAIL = "torn_tail"
    CORRUPT_TAIL = "corrupt_tail"


class JournalValidationError(ValueError):
    """Raised before a candidate WAL write is attempted."""


class JournalDurabilityError(OSError):
    """Raised after a WAL write may have changed durable bytes."""


class JournalCorruptionError(RuntimeError):
    """Raised for non-tail or contract-level journal corruption."""


class DiskLowError(RuntimeError):
    """Raised for a proven no-write capacity denial."""


@dataclass(frozen=True, slots=True)
class ScanSummary:
    file_size: int
    last_good_offset: int
    last_good_ingest_seq: int
    record_count: int
    raw_count: int
    derived_count: int
    terminal_clean: bool
    issue: ScanIssue | None
    wal_valid: bool


@dataclass(frozen=True, slots=True)
class _FrameRead:
    event: PersistedEvent | None
    end_offset: int
    observed_size: int
    issue: ScanIssue | None


@dataclass(frozen=True, slots=True)
class _WalkItem:
    event: PersistedEvent | None = None
    summary: ScanSummary | None = None


def _stable_validation_error(error: BaseException) -> JournalValidationError:
    return JournalValidationError("journal_candidate_invalid")


def _control_event(
    manifest: SessionManifest,
    *,
    ingest_seq: int,
    event_type: str,
    payload: bytes,
) -> PersistedEvent:
    content_type = {
        "SESSION_START": "application/vnd.inci.session-manifest+json",
        "SESSION_HALT": "application/vnd.inci.session-terminal+json",
    }[event_type]
    return PersistedEvent(
        journal_version=1,
        record_kind=RecordKind.CONTROL,
        ingest_seq=ingest_seq,
        session_id=manifest.session_id,
        event_type=event_type,
        event_version=1,
        source_kind=SourceKind.SYSTEM,
        source_id="tennis-v1",
        source_entity_id=manifest.session_id,
        endpoint_id=None,
        endpoint_state=ProvenanceState.ABSENT,
        channel_id="session-control",
        channel_state=ProvenanceState.SAFE_ORIGINAL,
        request_id=None,
        request_id_state=ProvenanceState.ABSENT,
        source_wall_ns=None,
        source_generated_ns=None,
        local_wall_ns=manifest.created_wall_ns,
        local_monotonic_ns=0,
        clock_uncertainty_ns=0,
        connection_epoch=0,
        provider_sequence=None,
        parent_ingest_seq=None,
        content_type=content_type,
        payload_encoding="canonical-json-v1",
        payload_transform="identity-public-market-v1",
        retention_delete_by_ns=None,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )


def _encode_frame(event: PersistedEvent) -> bytes:
    try:
        metadata, payload = encode_record(event)
        decoded = decode_record(metadata, payload)
    except (TypeError, ValueError, RecordCodecError) as error:
        raise _stable_validation_error(error) from None
    if decoded != event:
        raise JournalValidationError("journal_candidate_invalid")
    total = _FRAME_FIXED_BYTES + len(metadata) + len(payload)
    if total > MAX_FRAME_BYTES:
        raise JournalValidationError("journal_frame_too_large")
    try:
        prefix = FRAME_PREFIX.pack(
            FRAME_MAGIC,
            FRAME_VERSION,
            FRAME_KIND[event.record_kind],
            FRAME_FLAGS,
            event.ingest_seq,
            total,
            len(metadata),
            len(payload),
        )
    except (KeyError, struct.error, OverflowError) as error:
        raise _stable_validation_error(error) from None
    digest = hashlib.sha256(
        FRAME_DIGEST_DOMAIN + prefix + metadata + payload
    ).digest()
    frame = (
        prefix
        + metadata
        + payload
        + digest
        + FRAME_TRAILER.pack(total, TRAILER_MAGIC)
    )
    if len(frame) != total:
        raise JournalValidationError("journal_frame_length_invalid")
    return frame


def _require_digest(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise JournalValidationError("terminal_witness_invalid")
    return value


def _strict_json_object(
    content: bytes,
    *,
    expected_keys: frozenset[str],
    label: str,
) -> dict[str, object]:
    if type(content) is not bytes or content.startswith(b"\xef\xbb\xbf"):
        raise JournalCorruptionError(f"{label}_invalid")
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except JournalCorruptionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise JournalCorruptionError(f"{label}_invalid") from None
    if type(value) is not dict or set(value) != expected_keys:
        raise JournalCorruptionError(f"{label}_keys_invalid")
    try:
        canonical = canonical_json_bytes(value)
    except CanonicalJsonError as error:
        raise JournalCorruptionError(f"{label}_value_invalid") from error
    if canonical != content:
        raise JournalCorruptionError(f"{label}_noncanonical")
    return value


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JournalCorruptionError("journal_json_duplicate_key")
        result[key] = value
    return result


def _reject_json_number(_: str) -> object:
    raise JournalCorruptionError("journal_json_number_invalid")


class JournalWriter:
    __slots__ = (
        "_write_capability",
        "_session_manifest",
        "_session_start",
        "_next_seq",
        "_record_count",
        "_raw_count",
        "_derived_count",
        "_latest_raw",
        "_closed",
        "_poisoned",
        "_runtime_claimed",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use JournalWriter.create")

    @classmethod
    def create(
        cls,
        *,
        write_capability: ProviderWalWriteCapability,
        session_manifest: SessionManifest,
    ) -> "JournalWriter":
        if type(write_capability) is not ProviderWalWriteCapability:
            raise TypeError("coordinator-issued write capability required")
        _claim_provider_wal_writer(
            write_capability=write_capability,
            session_manifest=session_manifest,
        )
        if type(session_manifest) is not SessionManifest:
            raise TypeError("exact SessionManifest required")
        if session_manifest.research_evaluable is not False:
            raise JournalValidationError(
                "research_evaluable_must_be_literal_false"
            )
        try:
            start_payload = canonical_session_manifest_bytes(session_manifest)
            start = _control_event(
                session_manifest,
                ingest_seq=1,
                event_type="SESSION_START",
                payload=start_payload,
            )
            start_frame = _encode_frame(start)
        except JournalValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise _stable_validation_error(error) from None

        instance = object.__new__(cls)
        instance._write_capability = write_capability
        instance._session_manifest = session_manifest
        instance._session_start = start
        instance._next_seq = 1
        instance._record_count = 0
        instance._raw_count = 0
        instance._derived_count = 0
        instance._latest_raw = None
        instance._closed = False
        instance._poisoned = False
        instance._runtime_claimed = False
        prefix = FILE_PREFIX.pack(
            FILE_MAGIC,
            FILE_VERSION,
            FILE_FLAGS,
            FILE_PREFIX.size,
        )
        try:
            write_capability.write_all(prefix)
        except BaseException as error:
            instance._poison_after_attempt()
            raise JournalDurabilityError(
                "journal_durability_uncertain"
            ) from error
        try:
            instance._write_encoded_event(start, start_frame)
        except DiskLowError:
            try:
                write_capability.close()
            except BaseException as close_error:
                instance._poisoned = True
                raise JournalDurabilityError(
                    "journal_durability_uncertain"
                ) from close_error
            raise
        return instance

    @property
    def session_manifest(self) -> SessionManifest:
        return self._session_manifest

    @property
    def session_start(self) -> PersistedEvent:
        return self._session_start

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    @property
    def latest_raw(self) -> PersistedEvent | None:
        return self._latest_raw

    def claim_runtime(
        self,
        *,
        persistence_authorizer,
        coordinator: RetentionCoordinator,
    ) -> None:
        self._require_writable()
        if self._runtime_claimed:
            raise JournalValidationError("journal_runtime_already_claimed")
        try:
            _claim_provider_wal_runtime(
                write_capability=self._write_capability,
                persistence_authorizer=persistence_authorizer,
                coordinator=coordinator,
                session_manifest=self._session_manifest,
            )
        except BaseException:
            self._poisoned = True
            raise
        self._runtime_claimed = True

    def _require_writable(self) -> None:
        if self._poisoned:
            raise JournalDurabilityError("journal_durability_uncertain")
        if self._closed:
            raise JournalValidationError("journal_already_closed")

    def _poison_after_attempt(self) -> None:
        self._poisoned = True
        try:
            self._write_capability.close()
        except BaseException:
            pass

    def _write_encoded_event(
        self,
        event: PersistedEvent,
        frame: bytes,
        *,
        halted: bool = False,
    ) -> PersistedEvent:
        self._require_writable()
        if event.ingest_seq != self._next_seq:
            raise JournalValidationError("journal_sequence_invalid")
        try:
            if halted:
                self._write_capability.write_halt_control(frame)
            else:
                self._write_capability.write_all(frame)
        except RetentionPrewriteCapacityError as error:
            if halted:
                self._next_seq += 1
                self._poison_after_attempt()
                raise JournalDurabilityError(
                    "journal_durability_uncertain"
                ) from error
            raise DiskLowError("journal_disk_low") from None
        except BaseException as error:
            self._next_seq += 1
            self._poison_after_attempt()
            raise JournalDurabilityError(
                "journal_durability_uncertain"
            ) from error
        self._next_seq += 1
        self._record_count += 1
        if event.record_kind is RecordKind.RAW:
            self._raw_count += 1
            self._latest_raw = event
        elif event.record_kind is RecordKind.DERIVED:
            self._derived_count += 1
        return event

    def append_raw(self, captured: CapturedInput) -> PersistedEvent:
        self._require_writable()
        if type(captured) is not CapturedInput:
            raise TypeError("exact CapturedInput required")
        try:
            manifest = self._session_manifest
            try:
                validate_captured_input(captured, manifest)
            except CaptureValidationError:
                raise JournalValidationError(
                    "captured_input_invalid"
                ) from None
            if (
                type(captured.payload) is not bytes
                or len(captured.payload) > MAX_CAPTURE_BYTES
            ):
                raise JournalValidationError("captured_payload_invalid")
            if captured.session_id != manifest.session_id:
                raise JournalValidationError("captured_session_mismatch")
            if captured.source_kind is SourceKind.PROVIDER:
                if (
                    captured.source_id != manifest.provider_id
                    or captured.retention_delete_by_ns
                    != manifest.required_retention_until_ns
                ):
                    raise JournalValidationError(
                        "captured_provider_binding_mismatch"
                    )
            elif captured.retention_delete_by_ns is not None:
                raise JournalValidationError(
                    "captured_nonprovider_retention_invalid"
                )
            event = PersistedEvent(
                journal_version=1,
                record_kind=RecordKind.RAW,
                ingest_seq=self._next_seq,
                session_id=captured.session_id,
                event_type=captured.event_type,
                event_version=captured.event_version,
                source_kind=captured.source_kind,
                source_id=captured.source_id,
                source_entity_id=captured.source_entity_id,
                endpoint_id=captured.endpoint_id,
                endpoint_state=captured.endpoint_state,
                channel_id=captured.channel_id,
                channel_state=captured.channel_state,
                request_id=captured.request_id,
                request_id_state=captured.request_id_state,
                source_wall_ns=captured.source_wall_ns,
                source_generated_ns=captured.source_generated_ns,
                local_wall_ns=captured.local_wall_ns,
                local_monotonic_ns=captured.local_monotonic_ns,
                clock_uncertainty_ns=captured.clock_uncertainty_ns,
                connection_epoch=captured.connection_epoch,
                provider_sequence=captured.provider_sequence,
                parent_ingest_seq=None,
                content_type=captured.content_type,
                payload_encoding=captured.payload_encoding,
                payload_transform=captured.payload_transform,
                retention_delete_by_ns=captured.retention_delete_by_ns,
                payload_sha256=hashlib.sha256(captured.payload).hexdigest(),
                payload=captured.payload,
            )
            frame = _encode_frame(event)
        except JournalValidationError:
            raise
        except (TypeError, ValueError, AttributeError) as error:
            raise _stable_validation_error(error) from None
        return self._write_encoded_event(event, frame)

    def append_derived(
        self,
        parent: PersistedEvent,
        draft: DerivedDraft,
    ) -> PersistedEvent:
        self._require_writable()
        if type(parent) is not PersistedEvent or type(draft) is not DerivedDraft:
            raise TypeError("exact raw parent and DerivedDraft required")
        if (
            parent.record_kind is not RecordKind.RAW
            or parent.session_id != self._session_manifest.session_id
            or self._latest_raw != parent
        ):
            raise JournalValidationError("derived_parent_invalid")
        try:
            event = PersistedEvent(
                journal_version=1,
                record_kind=RecordKind.DERIVED,
                ingest_seq=self._next_seq,
                session_id=parent.session_id,
                event_type=draft.event_type,
                event_version=draft.event_version,
                source_kind=parent.source_kind,
                source_id=parent.source_id,
                source_entity_id=parent.source_entity_id,
                endpoint_id=parent.endpoint_id,
                endpoint_state=parent.endpoint_state,
                channel_id=parent.channel_id,
                channel_state=parent.channel_state,
                request_id=parent.request_id,
                request_id_state=parent.request_id_state,
                source_wall_ns=parent.source_wall_ns,
                source_generated_ns=parent.source_generated_ns,
                local_wall_ns=parent.local_wall_ns,
                local_monotonic_ns=parent.local_monotonic_ns,
                clock_uncertainty_ns=parent.clock_uncertainty_ns,
                connection_epoch=parent.connection_epoch,
                provider_sequence=parent.provider_sequence,
                parent_ingest_seq=parent.ingest_seq,
                content_type="application/vnd.inci.derived+json",
                payload_encoding=draft.payload_encoding,
                payload_transform="derived-canonical-v1",
                retention_delete_by_ns=parent.retention_delete_by_ns,
                payload_sha256=hashlib.sha256(draft.payload).hexdigest(),
                payload=draft.payload,
            )
            frame = _encode_frame(event)
        except JournalValidationError:
            raise
        except (TypeError, ValueError, AttributeError) as error:
            raise _stable_validation_error(error) from None
        return self._write_encoded_event(event, frame)

    def _terminal_payload(
        self,
        *,
        clean: bool,
        reason: str,
        trace_sha256: str,
        final_state_sha256: str,
        last_applied_raw_seq: int,
    ) -> bytes:
        if type(reason) is not str or not reason:
            raise JournalValidationError("terminal_reason_invalid")
        allowed_reasons = (
            _CLEAN_TERMINAL_REASONS
            if clean
            else _HALTED_TERMINAL_REASONS
        )
        if reason not in allowed_reasons:
            raise JournalValidationError("terminal_reason_invalid")
        trace = _require_digest(trace_sha256)
        final_state = _require_digest(final_state_sha256)
        if (
            type(last_applied_raw_seq) is not int
            or last_applied_raw_seq < 0
        ):
            raise JournalValidationError("terminal_last_applied_invalid")
        latest_raw_seq = (
            0
            if self._latest_raw is None
            else self._latest_raw.ingest_seq
        )
        if clean and last_applied_raw_seq != latest_raw_seq:
            raise JournalValidationError("terminal_last_applied_invalid")
        if not clean and last_applied_raw_seq > latest_raw_seq:
            raise JournalValidationError("terminal_last_applied_invalid")
        manifest = self._session_manifest
        return canonical_json_bytes(
            {
                "terminal_version": 1,
                "clean": clean,
                "reason": reason,
                "trace_sha256": trace,
                "final_state_sha256": final_state,
                "record_count_before_terminal": self._record_count,
                "raw_count": self._raw_count,
                "derived_count": self._derived_count,
                "last_applied_raw_seq": last_applied_raw_seq,
                "config_file_sha256": manifest.config_file_sha256,
                "config_canonical_sha256": manifest.config_canonical_sha256,
                "code_sha256": manifest.code_sha256,
                "session_manifest_sha256": session_manifest_sha256(manifest),
                "provider_manifest_file_sha256": (
                    manifest.provider_manifest_file_sha256
                ),
                "provider_manifest_canonical_sha256": (
                    manifest.provider_manifest_canonical_sha256
                ),
                "entitlement_id_sha256": manifest.entitlement_id_sha256,
                "permission_artifact_sha256": (
                    manifest.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    manifest.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    manifest.qualification_trace_sha256
                ),
                "adapter_code_sha256": manifest.adapter_code_sha256,
                "auth_contract_sha256": manifest.auth_contract_sha256,
                "quota_contract_sha256": manifest.quota_contract_sha256,
                "required_retention_until_ns": (
                    manifest.required_retention_until_ns
                ),
                "research_evaluable": False,
            }
        )

    def _close(
        self,
        *,
        clean: bool,
        reason: str,
        trace_sha256: str,
        final_state_sha256: str,
        last_applied_raw_seq: int,
    ) -> PersistedEvent:
        self._require_writable()
        try:
            payload = self._terminal_payload(
                clean=clean,
                reason=reason,
                trace_sha256=trace_sha256,
                final_state_sha256=final_state_sha256,
                last_applied_raw_seq=last_applied_raw_seq,
            )
            event = _control_event(
                self._session_manifest,
                ingest_seq=self._next_seq,
                event_type="SESSION_HALT",
                payload=payload,
            )
            frame = _encode_frame(event)
        except JournalValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise _stable_validation_error(error) from None
        stored = self._write_encoded_event(
            event,
            frame,
            halted=not clean,
        )
        if clean:
            try:
                _ack_provider_wal_clean_terminal(
                    write_capability=self._write_capability,
                )
            except BaseException as error:
                self._closed = True
                self._poisoned = True
                try:
                    self._write_capability.close()
                except BaseException:
                    pass
                raise JournalDurabilityError(
                    "journal_clean_ack_uncertain"
                ) from error
        self._closed = True
        try:
            self._write_capability.close()
        except BaseException as error:
            self._poisoned = True
            raise JournalDurabilityError(
                "journal_durability_uncertain"
            ) from error
        return stored

    def close_clean(
        self,
        *,
        reason: str,
        trace_sha256: str,
        final_state_sha256: str,
        last_applied_raw_seq: int,
    ) -> PersistedEvent:
        return self._close(
            clean=True,
            reason=reason,
            trace_sha256=trace_sha256,
            final_state_sha256=final_state_sha256,
            last_applied_raw_seq=last_applied_raw_seq,
        )

    def close_halted(
        self,
        *,
        reason: str,
        trace_sha256: str,
        final_state_sha256: str,
        last_applied_raw_seq: int,
    ) -> PersistedEvent:
        return self._close(
            clean=False,
            reason=reason,
            trace_sha256=trace_sha256,
            final_state_sha256=final_state_sha256,
            last_applied_raw_seq=last_applied_raw_seq,
        )


class JournalReader:
    __slots__ = ("_read_capability", "_closed")

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use JournalReader.open")

    @classmethod
    def create(
        cls,
        *,
        read_capability: ProviderWalReadCapability,
    ) -> "JournalReader":
        if type(read_capability) is not ProviderWalReadCapability:
            raise TypeError("coordinator-issued read capability required")
        _claim_provider_wal_reader(read_capability=read_capability)
        try:
            probe = read_capability.pread(offset=0, length=0)
            if type(probe) is not bytes or probe:
                raise JournalCorruptionError(
                    "journal_read_contract_invalid"
                )
            instance = object.__new__(cls)
            instance._read_capability = read_capability
            instance._closed = False
            return instance
        except BaseException as creation_error:
            try:
                read_capability.close()
            except BaseException as close_error:
                raise close_error from creation_error
            raise

    @classmethod
    def open(
        cls,
        *,
        read_capability: ProviderWalReadCapability,
    ) -> "JournalReader":
        return cls.create(read_capability=read_capability)

    def __enter__(self) -> "JournalReader":
        if self._closed:
            raise JournalValidationError("journal_reader_closed")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._read_capability.close()

    def _pread(self, *, offset: int, length: int) -> bytes:
        if self._closed:
            raise JournalValidationError("journal_reader_closed")
        content = self._read_capability.pread(offset=offset, length=length)
        if type(content) is not bytes or len(content) > length:
            raise JournalCorruptionError("journal_read_contract_invalid")
        return content

    def _read_file_header(self) -> ScanIssue | None:
        content = self._pread(offset=0, length=FILE_PREFIX.size)
        if len(content) < FILE_PREFIX.size:
            return ScanIssue.TORN_TAIL
        try:
            magic, version, flags, header_length = FILE_PREFIX.unpack(content)
        except struct.error as error:
            raise JournalCorruptionError("journal_file_header_invalid") from error
        if (
            magic != FILE_MAGIC
            or version != FILE_VERSION
            or flags != FILE_FLAGS
            or header_length != FILE_PREFIX.size
        ):
            raise JournalCorruptionError("journal_file_header_invalid")
        return None

    def _tail_or_interior_corruption(
        self,
        *,
        next_offset: int,
    ) -> _FrameRead:
        following = self._pread(
            offset=next_offset,
            length=FRAME_PREFIX.size,
        )
        if not following:
            return _FrameRead(
                event=None,
                end_offset=next_offset,
                observed_size=next_offset,
                issue=ScanIssue.CORRUPT_TAIL,
            )
        raise JournalCorruptionError("journal_interior_corruption")

    def _read_frame(self, offset: int) -> _FrameRead:
        prefix = self._pread(offset=offset, length=FRAME_PREFIX.size)
        if not prefix:
            return _FrameRead(
                event=None,
                end_offset=offset,
                observed_size=offset,
                issue=None,
            )
        if len(prefix) < FRAME_PREFIX.size:
            return _FrameRead(
                event=None,
                end_offset=offset,
                observed_size=offset + len(prefix),
                issue=ScanIssue.TORN_TAIL,
            )
        try:
            (
                magic,
                version,
                numeric_kind,
                flags,
                ingest_seq,
                total,
                metadata_length,
                payload_length,
            ) = FRAME_PREFIX.unpack(prefix)
        except struct.error as error:
            raise JournalCorruptionError("journal_frame_prefix_invalid") from error
        if (
            magic != FRAME_MAGIC
            or version != FRAME_VERSION
            or flags != FRAME_FLAGS
            or numeric_kind not in _KIND_FROM_NUMBER
            or total != _FRAME_FIXED_BYTES + metadata_length + payload_length
            or total < _FRAME_FIXED_BYTES
            or total > MAX_FRAME_BYTES
        ):
            raise JournalCorruptionError("journal_frame_prefix_invalid")
        metadata_offset = offset + FRAME_PREFIX.size
        payload_offset = metadata_offset + metadata_length
        digest_offset = payload_offset + payload_length
        trailer_offset = digest_offset + 32
        end_offset = trailer_offset + FRAME_TRAILER.size
        metadata = self._pread(
            offset=metadata_offset,
            length=metadata_length,
        )
        if len(metadata) < metadata_length:
            return _FrameRead(
                None,
                offset,
                metadata_offset + len(metadata),
                ScanIssue.TORN_TAIL,
            )
        payload = self._pread(
            offset=payload_offset,
            length=payload_length,
        )
        if len(payload) < payload_length:
            return _FrameRead(
                None,
                offset,
                payload_offset + len(payload),
                ScanIssue.TORN_TAIL,
            )
        digest = self._pread(offset=digest_offset, length=32)
        if len(digest) < 32:
            return _FrameRead(
                None,
                offset,
                digest_offset + len(digest),
                ScanIssue.TORN_TAIL,
            )
        trailer = self._pread(
            offset=trailer_offset,
            length=FRAME_TRAILER.size,
        )
        if len(trailer) < FRAME_TRAILER.size:
            return _FrameRead(
                None,
                offset,
                trailer_offset + len(trailer),
                ScanIssue.TORN_TAIL,
            )
        try:
            repeated, trailer_magic = FRAME_TRAILER.unpack(trailer)
        except struct.error as error:
            raise JournalCorruptionError("journal_frame_trailer_invalid") from error
        expected_digest = hashlib.sha256(
            FRAME_DIGEST_DOMAIN + prefix + metadata + payload
        ).digest()
        if (
            repeated != total
            or trailer_magic != TRAILER_MAGIC
            or digest != expected_digest
        ):
            return self._tail_or_interior_corruption(
                next_offset=end_offset,
            )
        try:
            event = decode_record(metadata, payload)
        except (TypeError, ValueError, RecordCodecError) as error:
            raise JournalCorruptionError("journal_record_invalid") from error
        if (
            FRAME_KIND[event.record_kind] != numeric_kind
            or event.ingest_seq != ingest_seq
        ):
            raise JournalCorruptionError("journal_frame_record_mismatch")
        return _FrameRead(
            event=event,
            end_offset=end_offset,
            observed_size=end_offset,
            issue=None,
        )

    @staticmethod
    def _manifest_from_start(event: PersistedEvent) -> SessionManifest:
        if (
            event.ingest_seq != 1
            or event.record_kind is not RecordKind.CONTROL
            or event.event_type != "SESSION_START"
        ):
            raise JournalCorruptionError("journal_session_start_missing")
        raw = _strict_json_object(
            event.payload,
            expected_keys=_SESSION_MANIFEST_KEYS,
            label="session_manifest",
        )
        try:
            manifest = SessionManifest(**raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise JournalCorruptionError(
                "journal_session_manifest_invalid"
            ) from error
        if manifest.session_id != event.session_id:
            raise JournalCorruptionError(
                "journal_session_manifest_mismatch"
            )
        return manifest

    def _validate_terminal(
        self,
        event: PersistedEvent,
        *,
        manifest: SessionManifest,
        record_count_before_terminal: int,
        raw_count: int,
        derived_count: int,
        last_raw_seq: int,
    ) -> bool:
        if (
            event.record_kind is not RecordKind.CONTROL
            or event.event_type != "SESSION_HALT"
            or event.session_id != manifest.session_id
        ):
            raise JournalCorruptionError("journal_terminal_invalid")
        raw = _strict_json_object(
            event.payload,
            expected_keys=_TERMINAL_KEYS,
            label="session_terminal",
        )
        if (
            type(raw["terminal_version"]) is not int
            or raw["terminal_version"] != 1
            or type(raw["clean"]) is not bool
            or type(raw["reason"]) is not str
            or not raw["reason"]
            or raw["research_evaluable"] is not False
        ):
            raise JournalCorruptionError("journal_terminal_contract_invalid")
        permitted_reasons = (
            _CLEAN_TERMINAL_REASONS
            if raw["clean"]
            else _HALTED_TERMINAL_REASONS
        )
        if raw["reason"] not in permitted_reasons:
            raise JournalCorruptionError("journal_terminal_reason_invalid")
        for name in (
            "trace_sha256",
            "final_state_sha256",
            "config_file_sha256",
            "config_canonical_sha256",
            "code_sha256",
            "session_manifest_sha256",
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
            if type(raw[name]) is not str or _SHA256.fullmatch(raw[name]) is None:
                raise JournalCorruptionError(
                    "journal_terminal_digest_invalid"
                )
        for name in (
            "record_count_before_terminal",
            "raw_count",
            "derived_count",
            "last_applied_raw_seq",
            "required_retention_until_ns",
        ):
            if type(raw[name]) is not int or raw[name] < 0:
                raise JournalCorruptionError(
                    "journal_terminal_count_invalid"
                )
        expected = {
            "record_count_before_terminal": record_count_before_terminal,
            "raw_count": raw_count,
            "derived_count": derived_count,
            "config_file_sha256": manifest.config_file_sha256,
            "config_canonical_sha256": manifest.config_canonical_sha256,
            "code_sha256": manifest.code_sha256,
            "session_manifest_sha256": session_manifest_sha256(manifest),
            "provider_manifest_file_sha256": (
                manifest.provider_manifest_file_sha256
            ),
            "provider_manifest_canonical_sha256": (
                manifest.provider_manifest_canonical_sha256
            ),
            "entitlement_id_sha256": manifest.entitlement_id_sha256,
            "permission_artifact_sha256": (
                manifest.permission_artifact_sha256
            ),
            "qualification_artifact_sha256": (
                manifest.qualification_artifact_sha256
            ),
            "qualification_trace_sha256": (
                manifest.qualification_trace_sha256
            ),
            "adapter_code_sha256": manifest.adapter_code_sha256,
            "auth_contract_sha256": manifest.auth_contract_sha256,
            "quota_contract_sha256": manifest.quota_contract_sha256,
            "required_retention_until_ns": (
                manifest.required_retention_until_ns
            ),
        }
        if any(raw[name] != value for name, value in expected.items()):
            raise JournalCorruptionError("journal_terminal_binding_invalid")
        last_applied = raw["last_applied_raw_seq"]
        if not raw["clean"] and last_applied > last_raw_seq:
            raise JournalCorruptionError("journal_terminal_count_invalid")
        if raw["clean"] and last_applied != last_raw_seq:
            raise JournalCorruptionError("journal_terminal_count_invalid")
        return raw["clean"]  # type: ignore[return-value]

    def _validate_replay_terminal_grammar(
        self,
        event: PersistedEvent,
        *,
        manifest: SessionManifest,
    ) -> bool:
        if (
            event.record_kind is not RecordKind.CONTROL
            or event.event_type != "SESSION_HALT"
            or event.session_id != manifest.session_id
        ):
            raise JournalCorruptionError("journal_terminal_invalid")
        raw = _strict_json_object(
            event.payload,
            expected_keys=_TERMINAL_KEYS,
            label="session_terminal",
        )
        if (
            type(raw["terminal_version"]) is not int
            or raw["terminal_version"] != 1
            or type(raw["clean"]) is not bool
            or type(raw["reason"]) is not str
            or not raw["reason"]
            or raw["research_evaluable"] is not False
        ):
            raise JournalCorruptionError("journal_terminal_contract_invalid")
        for name in (
            "trace_sha256",
            "final_state_sha256",
            "config_file_sha256",
            "config_canonical_sha256",
            "code_sha256",
            "session_manifest_sha256",
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
            if type(raw[name]) is not str or _SHA256.fullmatch(raw[name]) is None:
                raise JournalCorruptionError(
                    "journal_terminal_digest_invalid"
                )
        for name in (
            "record_count_before_terminal",
            "raw_count",
            "derived_count",
            "last_applied_raw_seq",
            "required_retention_until_ns",
        ):
            if type(raw[name]) is not int or raw[name] < 0:
                raise JournalCorruptionError(
                    "journal_terminal_count_invalid"
                )
        return raw["clean"]  # type: ignore[return-value]

    def _walk(self) -> Iterator[_WalkItem]:
        header_issue = self._read_file_header()
        if header_issue is not None:
            yield _WalkItem(
                summary=ScanSummary(
                    file_size=len(
                        self._pread(offset=0, length=FILE_PREFIX.size)
                    ),
                    last_good_offset=0,
                    last_good_ingest_seq=0,
                    record_count=0,
                    raw_count=0,
                    derived_count=0,
                    terminal_clean=False,
                    issue=header_issue,
                    wal_valid=False,
                )
            )
            return
        offset = FILE_PREFIX.size
        expected_seq = 1
        record_count = 0
        raw_count = 0
        derived_count = 0
        last_good_seq = 0
        last_raw_seq = 0
        manifest: SessionManifest | None = None
        terminal_clean = False
        terminal_seen = False
        while True:
            frame = self._read_frame(offset)
            if terminal_seen and not (
                frame.event is None
                and frame.issue is None
                and frame.observed_size == offset
            ):
                raise JournalCorruptionError("journal_bytes_after_terminal")
            if frame.event is None:
                if frame.issue is ScanIssue.CORRUPT_TAIL:
                    issue = ScanIssue.CORRUPT_TAIL
                    wal_valid = False
                    file_size = frame.observed_size
                elif frame.issue is ScanIssue.TORN_TAIL:
                    issue = ScanIssue.TORN_TAIL
                    wal_valid = False
                    file_size = frame.observed_size
                else:
                    file_size = frame.observed_size
                    if terminal_seen:
                        issue = (
                            None
                            if terminal_clean
                            else ScanIssue.HALTED_TERMINAL
                        )
                    else:
                        issue = ScanIssue.MISSING_TERMINAL
                    wal_valid = True
                yield _WalkItem(
                    summary=ScanSummary(
                        file_size=file_size,
                        last_good_offset=offset,
                        last_good_ingest_seq=last_good_seq,
                        record_count=record_count,
                        raw_count=raw_count,
                        derived_count=derived_count,
                        terminal_clean=terminal_clean,
                        issue=issue,
                        wal_valid=wal_valid,
                    )
                )
                return
            event = frame.event
            if event.ingest_seq != expected_seq:
                raise JournalCorruptionError("journal_sequence_invalid")
            if expected_seq == 1:
                manifest = self._manifest_from_start(event)
            else:
                assert manifest is not None
                if event.session_id != manifest.session_id:
                    raise JournalCorruptionError(
                        "journal_session_mismatch"
                    )
                if event.event_type == "SESSION_START":
                    raise JournalCorruptionError(
                        "journal_duplicate_session_start"
                    )
                if event.record_kind is RecordKind.RAW:
                    last_raw_seq = event.ingest_seq
                    raw_count += 1
                elif event.record_kind is RecordKind.DERIVED:
                    if event.parent_ingest_seq != last_raw_seq:
                        raise JournalCorruptionError(
                            "journal_derived_parent_invalid"
                        )
                    derived_count += 1
                elif event.event_type == "SESSION_HALT":
                    terminal_clean = self._validate_terminal(
                        event,
                        manifest=manifest,
                        record_count_before_terminal=record_count,
                        raw_count=raw_count,
                        derived_count=derived_count,
                        last_raw_seq=last_raw_seq,
                    )
                    terminal_seen = True
                else:
                    raise JournalCorruptionError(
                        "journal_control_record_invalid"
                    )
            offset = frame.end_offset
            expected_seq += 1
            record_count += 1
            last_good_seq = event.ingest_seq
            yield _WalkItem(event=event)

    def read_session_manifest(self) -> SessionManifest:
        issue = self._read_file_header()
        if issue is not None:
            raise JournalCorruptionError("journal_file_header_incomplete")
        frame = self._read_frame(FILE_PREFIX.size)
        if frame.event is None:
            raise JournalCorruptionError("journal_session_start_missing")
        return self._manifest_from_start(frame.event)

    def scan(self, *, require_clean: bool = False) -> ScanSummary:
        summary: ScanSummary | None = None
        for item in self._walk():
            if item.summary is not None:
                summary = item.summary
        if summary is None:
            raise JournalCorruptionError("journal_scan_incomplete")
        if require_clean and (
            summary.issue is not None or not summary.terminal_clean
        ):
            raise JournalCorruptionError("journal_clean_terminal_required")
        return summary

    def iter_records(
        self,
        *,
        diagnostic_prefix: bool = False,
    ) -> Iterator[PersistedEvent]:
        summary: ScanSummary | None = None
        for item in self._walk():
            if item.event is not None:
                yield item.event
            elif item.summary is not None:
                summary = item.summary
        if summary is None:
            raise JournalCorruptionError("journal_iteration_incomplete")
        if not diagnostic_prefix and summary.issue is not None:
            raise JournalCorruptionError("journal_incomplete")

    def iter_replay_records(
        self,
    ) -> Generator[PersistedEvent, None, ScanSummary]:
        header_issue = self._read_file_header()
        if header_issue is not None:
            return ScanSummary(
                file_size=len(
                    self._pread(offset=0, length=FILE_PREFIX.size)
                ),
                last_good_offset=0,
                last_good_ingest_seq=0,
                record_count=0,
                raw_count=0,
                derived_count=0,
                terminal_clean=False,
                issue=header_issue,
                wal_valid=False,
            )
        offset = FILE_PREFIX.size
        expected_seq = 1
        record_count = 0
        raw_count = 0
        derived_count = 0
        last_good_seq = 0
        last_raw_seq = 0
        manifest: SessionManifest | None = None
        terminal_clean = False
        terminal_seen = False
        while True:
            frame = self._read_frame(offset)
            if terminal_seen and not (
                frame.event is None
                and frame.issue is None
                and frame.observed_size == offset
            ):
                raise JournalCorruptionError("journal_bytes_after_terminal")
            if frame.event is None:
                if frame.issue is ScanIssue.CORRUPT_TAIL:
                    issue = ScanIssue.CORRUPT_TAIL
                    wal_valid = False
                    file_size = frame.observed_size
                elif frame.issue is ScanIssue.TORN_TAIL:
                    issue = ScanIssue.TORN_TAIL
                    wal_valid = False
                    file_size = frame.observed_size
                else:
                    file_size = frame.observed_size
                    if terminal_seen:
                        issue = (
                            None
                            if terminal_clean
                            else ScanIssue.HALTED_TERMINAL
                        )
                    else:
                        issue = ScanIssue.MISSING_TERMINAL
                    wal_valid = True
                return ScanSummary(
                    file_size=file_size,
                    last_good_offset=offset,
                    last_good_ingest_seq=last_good_seq,
                    record_count=record_count,
                    raw_count=raw_count,
                    derived_count=derived_count,
                    terminal_clean=terminal_clean,
                    issue=issue,
                    wal_valid=wal_valid,
                )
            event = frame.event
            if event.ingest_seq != expected_seq:
                raise JournalCorruptionError("journal_sequence_invalid")
            if expected_seq == 1:
                manifest = self._manifest_from_start(event)
            else:
                assert manifest is not None
                if event.session_id != manifest.session_id:
                    raise JournalCorruptionError(
                        "journal_session_mismatch"
                    )
                if event.event_type == "SESSION_START":
                    raise JournalCorruptionError(
                        "journal_duplicate_session_start"
                    )
                if event.record_kind is RecordKind.RAW:
                    last_raw_seq = event.ingest_seq
                    raw_count += 1
                elif event.record_kind is RecordKind.DERIVED:
                    if event.parent_ingest_seq != last_raw_seq:
                        raise JournalCorruptionError(
                            "journal_derived_parent_invalid"
                        )
                    derived_count += 1
                elif event.event_type == "SESSION_HALT":
                    terminal_clean = (
                        self._validate_replay_terminal_grammar(
                            event,
                            manifest=manifest,
                        )
                    )
                    terminal_seen = True
                else:
                    raise JournalCorruptionError(
                        "journal_control_record_invalid"
                    )
            offset = frame.end_offset
            expected_seq += 1
            record_count += 1
            last_good_seq = event.ingest_seq
            yield event
