"""Capability-only exact and diagnostic replay for Tennis v1 journals."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, fields
from enum import Enum
import hashlib
import json
import re
from typing import Literal

from .codec import encode_record
from .events import (
    DerivedDraft,
    PersistedEvent,
    RecordKind,
    SessionManifest,
)
from .reducer import initial_trace, next_trace, reduce_event
from .retention import (
    RetentionCoordinator,
    RetentionError,
    _reject_expected_replay_manifest,
    _reject_replay_manifest,
)
from .sequencer import (
    ProviderPersistenceAuthorizer,
)
from .session import session_manifest_sha256
from .state import FoundationState, canonical_state_bytes, initial_state
from .wal import (
    JournalCorruptionError,
    JournalReader,
    ScanIssue,
    ScanSummary,
)


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EXACT_CLEAN_REASONS = frozenset({"operator_stop", "session_end"})
_TERMINAL_PROVENANCE_FIELDS = (
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
)


class ReplayMismatch(str, Enum):
    SESSION_MANIFEST = "session_manifest_mismatch"
    DERIVED_MISSING = "derived_missing"
    DERIVED_EXTRA = "derived_extra"
    DERIVED_ORDER = "derived_order_mismatch"
    DERIVED_RECORD = "derived_record_mismatch"
    RAW_REDUCTION = "raw_reduction_mismatch"
    STATE = "state_mismatch"
    TRACE = "trace_mismatch"
    TERMINAL_COUNTS = "terminal_counts_mismatch"
    TERMINAL_PROVENANCE = "terminal_provenance_mismatch"
    TERMINAL_REASON = "terminal_reason_mismatch"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    state: FoundationState | None
    trace_sha256: str | None
    raw_count: int
    derived_count: int
    terminal_clean: bool
    wal_valid: bool
    exact_replay: bool
    scan_issue: ScanIssue | None
    replay_mismatch: ReplayMismatch | None
    research_evaluable: Literal[False] = field(
        default=False,
        init=False,
    )


def _derived_signature(event: PersistedEvent) -> tuple[object, ...]:
    if type(event) is not PersistedEvent:
        raise TypeError("exact PersistedEvent required")
    return tuple(
        getattr(event, item.name)
        for item in fields(PersistedEvent)
        if item.name != "ingest_seq"
    )


def _compare_derived_sequences(
    expected: tuple[PersistedEvent, ...],
    stored: tuple[PersistedEvent, ...],
) -> ReplayMismatch | None:
    if type(expected) is not tuple or type(stored) is not tuple:
        raise TypeError("exact derived tuples required")
    if any(type(item) is not PersistedEvent for item in expected + stored):
        raise TypeError("exact PersistedEvent items required")
    if len(stored) < len(expected):
        return ReplayMismatch.DERIVED_MISSING
    if len(stored) > len(expected):
        return ReplayMismatch.DERIVED_EXTRA
    expected_bytes = tuple(encode_record(item) for item in expected)
    stored_bytes = tuple(encode_record(item) for item in stored)
    if expected_bytes == stored_bytes:
        return None
    expected_signatures = tuple(_derived_signature(item) for item in expected)
    stored_signatures = tuple(_derived_signature(item) for item in stored)
    if (
        expected_signatures != stored_signatures
        and Counter(expected_signatures) == Counter(stored_signatures)
    ):
        return ReplayMismatch.DERIVED_ORDER
    return ReplayMismatch.DERIVED_RECORD


def _reconstruct_derived(
    raw: PersistedEvent,
    draft: DerivedDraft,
    *,
    ingest_seq: int,
) -> PersistedEvent:
    if (
        type(raw) is not PersistedEvent
        or raw.record_kind is not RecordKind.RAW
        or type(draft) is not DerivedDraft
    ):
        raise TypeError("exact raw and DerivedDraft required")
    return PersistedEvent(
        journal_version=1,
        record_kind=RecordKind.DERIVED,
        ingest_seq=ingest_seq,
        session_id=raw.session_id,
        event_type=draft.event_type,
        event_version=draft.event_version,
        source_kind=raw.source_kind,
        source_id=raw.source_id,
        source_entity_id=raw.source_entity_id,
        endpoint_id=raw.endpoint_id,
        endpoint_state=raw.endpoint_state,
        channel_id=raw.channel_id,
        channel_state=raw.channel_state,
        request_id=raw.request_id,
        request_id_state=raw.request_id_state,
        source_wall_ns=raw.source_wall_ns,
        source_generated_ns=raw.source_generated_ns,
        local_wall_ns=raw.local_wall_ns,
        local_monotonic_ns=raw.local_monotonic_ns,
        clock_uncertainty_ns=raw.clock_uncertainty_ns,
        connection_epoch=raw.connection_epoch,
        provider_sequence=raw.provider_sequence,
        parent_ingest_seq=raw.ingest_seq,
        content_type="application/vnd.inci.derived+json",
        payload_encoding=draft.payload_encoding,
        payload_transform="derived-canonical-v1",
        retention_delete_by_ns=raw.retention_delete_by_ns,
        payload_sha256=hashlib.sha256(draft.payload).hexdigest(),
        payload=draft.payload,
    )


def _manifest_from_start(event: PersistedEvent) -> SessionManifest:
    try:
        raw = json.loads(event.payload.decode("ascii"))
        manifest = SessionManifest(**raw)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise JournalCorruptionError(
            "journal_session_manifest_invalid"
        ) from error
    return manifest


def _terminal_payload(event: PersistedEvent) -> dict[str, object]:
    try:
        payload = json.loads(event.payload.decode("ascii"))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise JournalCorruptionError(
            "journal_terminal_contract_invalid"
        ) from error
    if type(payload) is not dict:
        raise JournalCorruptionError("journal_terminal_contract_invalid")
    return payload


def _terminal_provenance(
    manifest: SessionManifest,
) -> dict[str, object]:
    return {
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


def _replay(
    *,
    expected_session_manifest_sha256: str,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ReplayResult:
    if type(coordinator) is not RetentionCoordinator:
        raise TypeError("exact RetentionCoordinator required")
    if type(persistence_authorizer) is not ProviderPersistenceAuthorizer:
        raise TypeError("exact ProviderPersistenceAuthorizer required")
    if persistence_authorizer.coordinator is not coordinator:
        raise ValueError("replay_coordinator_binding_mismatch")
    if (
        type(expected_session_manifest_sha256) is not str
        or _SHA256.fullmatch(expected_session_manifest_sha256) is None
    ):
        raise ValueError("expected_session_manifest_sha256_invalid")
    manifest = persistence_authorizer.session_manifest
    if type(manifest) is not SessionManifest:
        raise ValueError("expected_session_manifest_mismatch")
    if (
        session_manifest_sha256(manifest)
        != expected_session_manifest_sha256
    ):
        _reject_expected_replay_manifest(
            expected_session_manifest_sha256=(
                expected_session_manifest_sha256
            ),
            persistence_authorizer=persistence_authorizer,
            coordinator=coordinator,
        )
        raise ValueError("expected_session_manifest_mismatch")

    coordinator.recover_and_purge()
    analysis_decision = persistence_authorizer.authorize_analysis()
    if analysis_decision is not persistence_authorizer.bound_decision:
        raise RetentionError("replay_analysis_decision_changed")
    read_capability = coordinator.issue_read_capability(
        persistence_authorizer=persistence_authorizer,
    )
    reader = JournalReader.create(read_capability=read_capability)
    state: FoundationState | None = None
    trace: bytes | None = None
    terminal: PersistedEvent | None = None
    replay_mismatch: ReplayMismatch | None = None
    reduction_failed = False
    pending_expected: tuple[PersistedEvent, ...] = ()
    pending_stored: list[PersistedEvent] = []
    pending_stored_count = 0
    saw_start = False

    def finish_pending() -> None:
        nonlocal replay_mismatch, pending_expected
        nonlocal pending_stored, pending_stored_count
        if (
            reduction_failed
            or not pending_expected and pending_stored_count == 0
        ):
            pending_expected = ()
            pending_stored = []
            pending_stored_count = 0
            return
        mismatch = (
            ReplayMismatch.DERIVED_EXTRA
            if pending_stored_count > len(pending_expected)
            else _compare_derived_sequences(
                pending_expected,
                tuple(pending_stored),
            )
        )
        if replay_mismatch is None and mismatch is not None:
            replay_mismatch = mismatch
        pending_expected = ()
        pending_stored = []
        pending_stored_count = 0

    try:
        iterator = reader.iter_replay_records()
        while True:
            try:
                event = next(iterator)
            except StopIteration as stopped:
                summary = stopped.value
                break

            if not saw_start:
                saw_start = True
                persisted_manifest = _manifest_from_start(event)
                if (
                    session_manifest_sha256(persisted_manifest)
                    != expected_session_manifest_sha256
                    or persisted_manifest != manifest
                ):
                    reader.close()
                    _reject_replay_manifest(
                        read_capability=read_capability,
                        persistence_authorizer=persistence_authorizer,
                        coordinator=coordinator,
                        session_id=manifest.session_id,
                    )
                    return ReplayResult(
                        state=None,
                        trace_sha256=None,
                        raw_count=0,
                        derived_count=0,
                        terminal_clean=False,
                        wal_valid=False,
                        exact_replay=False,
                        scan_issue=None,
                        replay_mismatch=(
                            ReplayMismatch.SESSION_MANIFEST
                        ),
                    )
                try:
                    state = initial_state(persisted_manifest.session_id)
                    trace = initial_trace(event)
                except (TypeError, ValueError) as error:
                    raise JournalCorruptionError(
                        "journal_session_start_binding_invalid"
                    ) from error
                continue

            if event.record_kind is RecordKind.RAW:
                finish_pending()
                if reduction_failed:
                    continue
                assert state is not None
                assert trace is not None
                try:
                    reduction = reduce_event(state, event)
                    expected = tuple(
                        _reconstruct_derived(
                            event,
                            draft,
                            ingest_seq=event.ingest_seq + index + 1,
                        )
                        for index, draft in enumerate(reduction.outputs)
                    )
                    next_value = next_trace(
                        trace,
                        event,
                        expected,
                        reduction.state,
                    )
                except (TypeError, ValueError):
                    if replay_mismatch is None:
                        replay_mismatch = ReplayMismatch.RAW_REDUCTION
                    state = None
                    trace = None
                    reduction_failed = True
                    pending_expected = ()
                    pending_stored = []
                    pending_stored_count = 0
                    continue
                state = reduction.state
                trace = next_value
                pending_expected = expected
                pending_stored = []
                pending_stored_count = 0
            elif event.record_kind is RecordKind.DERIVED:
                if not reduction_failed:
                    pending_stored_count += 1
                    if len(pending_stored) < len(pending_expected) + 1:
                        pending_stored.append(event)
            else:
                finish_pending()
                terminal = event
        finish_pending()
    finally:
        reader.close()

    if type(summary) is not ScanSummary:
        raise JournalCorruptionError("journal_replay_iteration_incomplete")
    terminal_payload = (
        None if terminal is None else _terminal_payload(terminal)
    )
    if terminal_payload is not None and replay_mismatch is None:
        expected_last_raw = (
            None if state is None else state.last_applied_raw_seq
        )
        expected_counts = {
            "record_count_before_terminal": summary.record_count - 1,
            "raw_count": summary.raw_count,
            "derived_count": summary.derived_count,
            "last_applied_raw_seq": expected_last_raw,
        }
        if any(
            terminal_payload[name] != value
            for name, value in expected_counts.items()
        ):
            replay_mismatch = ReplayMismatch.TERMINAL_COUNTS
        else:
            provenance = _terminal_provenance(manifest)
            if (
                any(
                    terminal_payload[name] != provenance[name]
                    for name in _TERMINAL_PROVENANCE_FIELDS
                )
                or terminal.local_wall_ns != manifest.created_wall_ns
                or terminal.local_monotonic_ns != 0
            ):
                replay_mismatch = ReplayMismatch.TERMINAL_PROVENANCE
            elif (
                terminal_payload["clean"] is not True
                or terminal_payload["reason"]
                not in _EXACT_CLEAN_REASONS
            ):
                replay_mismatch = ReplayMismatch.TERMINAL_REASON
            elif state is None or (
                terminal_payload["final_state_sha256"]
                != hashlib.sha256(
                    canonical_state_bytes(state)
                ).hexdigest()
            ):
                replay_mismatch = ReplayMismatch.STATE
            elif trace is None or (
                terminal_payload["trace_sha256"] != trace.hex()
            ):
                replay_mismatch = ReplayMismatch.TRACE

    trace_sha256 = None if trace is None else trace.hex()
    exact_replay = (
        state is not None
        and trace_sha256 is not None
        and summary.terminal_clean
        and summary.wal_valid
        and summary.issue is None
        and replay_mismatch is None
    )
    return ReplayResult(
        state=state,
        trace_sha256=trace_sha256,
        raw_count=summary.raw_count,
        derived_count=summary.derived_count,
        terminal_clean=summary.terminal_clean,
        wal_valid=summary.wal_valid,
        exact_replay=exact_replay,
        scan_issue=summary.issue,
        replay_mismatch=replay_mismatch,
    )


def replay_exact(
    *,
    expected_session_manifest_sha256: str,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ReplayResult:
    return _replay(
        expected_session_manifest_sha256=expected_session_manifest_sha256,
        persistence_authorizer=persistence_authorizer,
        coordinator=coordinator,
    )


def scan_diagnostic_prefix(
    *,
    expected_session_manifest_sha256: str,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ReplayResult:
    return _replay(
        expected_session_manifest_sha256=expected_session_manifest_sha256,
        persistence_authorizer=persistence_authorizer,
        coordinator=coordinator,
    )
