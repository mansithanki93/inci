"""Descriptor-relative durable store for the diagnostic expert journal."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import errno
import fcntl
from hashlib import sha256
from importlib.machinery import ModuleSpec, SourceFileLoader
import json
import os
from pathlib import Path
import platform
import stat
import struct
import sys
import sysconfig
import threading
from types import ModuleType
from typing import Any
import uuid

from inci_tennis_expert.contracts import (
    DurableExpertAppendReceiptV1,
    DurableExpertEmergencyReceiptV1,
    DurableExpertTerminalReceiptV1,
    EvidenceReplayContextV1,
    ExpertCollectedEnvironmentV1,
    ExpertCurrentEnvironmentV1,
    ExpertEventKindV1,
    ExpertEventSchemaBundleV1,
    ExpertEventSchemaPinV1,
    ExpertJournalCursorV1,
    ExpertJournalGroupV1,
    ExpertJournalScanIssueV1,
    ExpertJournalScanSummaryV1,
    ExpertNormalizerPinV1,
    ExpertNormalizerRegistryV1,
    ExpertPhysicalFileIdentityV1,
    ExpertPurgeReportV1,
    ExpertReplayAccumulatorV1,
    ExpertReplayBeginReadyV1,
    ExpertReplayDeniedV1,
    ExpertReplayDiagnosticIssueV1,
    ExpertReplayDiagnosticRoleV1,
    ExpertReplayDiagnosticProofV1,
    ExpertReplayMismatchV1,
    ExpertReplayResultV1,
    ExpertSchemaPinV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertStateV1,
    ExpertStructuralSchemaBundleV1,
    RetentionReplayAuthorizationV1,
    canonical_expert_bytes,
    compute_expert_physical_file_identity_sha256,
    compute_expert_replay_diagnostic_file_proof_sha256,
    compute_expert_replay_diagnostic_proof_sha256,
    compute_expert_retention_binding_sha256,
    compute_retention_replay_authorization_sha256,
    compute_expert_session_terminal_sha256,
    expert_contract_sha256,
    expert_event_schema_bundle_sha256,
    expert_phase1_replay_summary_sha256,
    expert_normalizer_registry_sha256,
    expert_state_sha256,
    expert_structural_schema_bundle_sha256,
    _create_expert_collected_environment_v1,
    _create_evidence_replay_context_v1,
    _create_expert_replay_begin_ready_v1,
    _create_expert_replay_denied_v1,
    _create_expert_replay_diagnostic_file_proof_v1,
    _create_expert_replay_diagnostic_proof_v1,
    _create_expert_physical_file_identity_v1,
    _create_retention_replay_authorization_v1,
)
from inci_tennis_expert.facade import (
    begin_expert_replay,
    finish_expert_replay,
    replay_expert_parent_group,
)
from inci_tennis_expert.journal_codec import (
    EXPERT_EMERGENCY_RESERVE_BYTES,
    EXPERT_FILE_HEADER_BYTES,
    EXPERT_FRAME_DIGEST_DOMAIN,
    EXPERT_FRAME_KIND_PARENT_GROUP,
    EXPERT_FRAME_KIND_TERMINAL,
    EXPERT_FRAME_PREFIX_BYTES,
    EXPERT_FRAME_TRAILER_BYTES,
    EXPERT_MIN_FREE_BYTES,
    MAX_EXPERT_EVENT_PAYLOAD_BYTES,
    MAX_EXPERT_FRAME_BYTES,
    MAX_EXPERT_OUTCOMES_PER_PARENT,
    MAX_EXPERT_TERMINAL_FRAME_BYTES,
    decode_expert_complete_frame,
    decode_expert_file_header,
    decode_expert_frame_prefix,
    decode_expert_group_frame_structural,
    decode_expert_manifest_frame,
    decode_expert_terminal_frame_replay_material,
    decode_expert_terminal_frame_structural,
    encode_expert_file_header,
    encode_expert_group_frame,
    encode_expert_manifest_frame,
    encode_expert_terminal_frame,
    validate_expert_frame_parts,
    validate_expert_group_against_cursor,
    validate_expert_group_metadata_diagnostic,
    validate_expert_streamed_frame_trailer,
    validate_expert_terminal_against_cursor,
)
from inci_tennis_io.ports import (
    CandidateQualificationAppendReceiptV1,
    CandidateQualificationCommitReceiptV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealCollectionAuthorityV1,
    CandidateSourceSealsV1,
    ExpertEmergencyAppendPermitV1,
    ExpertEnvironmentCollectionAuthorityV1,
    ExpertJournalAppendPermitV1,
    ExpertJournalPurgeCapabilityV1,
    ExpertJournalReadCapabilityV1,
    ExpertJournalRootAuthorityV1,
    ExpertJournalTerminalPermitV1,
    ExpertJournalWriteCapabilityV1,
    ExpertLiveAuthorizationDenied,
    ExpertPrewriteCapacityError,
    ExpertReplayAccessDenied,
    ExpertReplayConstructionAuthorityV1,
    _create_candidate_qualification_append_receipt_v1,
    _create_candidate_qualification_commit_receipt_v1,
    _create_candidate_source_seals_v1,
)
from tennis_v1.adapter_contract import load_active_adapter_contract
from tennis_v1.codec import (
    canonical_json_bytes,
    canonical_record_sha256,
    decode_record,
)
from tennis_v1.events import CapturedInput, PersistedEvent, RecordKind, SessionManifest
from tennis_v1.fingerprints import CODE_FINGERPRINT_DOMAIN
from tennis_v1.retention import (
    ExpertStateRootAccountLockRequestV1,
    RetentionMarker,
    RetentionCoordinator,
    RetentionDueDeleteError,
    RetentionError,
    _consume_expert_state_root_account_lock_request,
    _revoke_expert_state_root_account_lock_grant,
    sample_expert_retention_wall_ns as _phase1_sample_wall_ns,
)
from tennis_v1.replay_core import replay_exact
from tennis_v1.sequencer import ProviderPersistenceAuthorizer
from tennis_v1.session import session_manifest_sha256
from tennis_v1.wal import JournalReader

_phase1_session_manifest_sha256 = session_manifest_sha256


EXPERT_MARKER_FIELDS = (
    "schema_version",
    "session_id",
    "journal_basename",
    "reserve_basename",
    "expert_manifest_sha256",
    "evidence_session_manifest_sha256",
    "evidence_session_start_record_sha256",
    "provider_request_binding_sha256",
    "retention_binding_sha256",
    "retention_delete_by_ns",
    "created_at_ns",
)

_LOCK = threading.RLock()
_LOCAL_ROOT_GENERATION = 0
_OPEN_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_OPEN_FILE_READ_FLAGS = (
    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
)
_OPEN_FILE_CREATE_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
)
_OPEN_FILE_APPEND_FLAGS = (
    os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
)
_SESSION_SUFFIX = ".expert-journal-v1"
_RESERVE_SUFFIX = ".expert-reserve-v1"
_MARKER_SUFFIX = ".expert-retention-v1.json"
_STRUCTURAL_SPEC = (
    (
        "session_manifest",
        "ExpertSessionManifestV1",
        "expert-session-manifest-v1.schema.json",
    ),
    (
        "journal_record",
        "ExpertJournalRecordV1",
        "expert-journal-record-v1.schema.json",
    ),
    (
        "parent_group",
        "ExpertJournalGroupV1",
        "expert-journal-group-v1.schema.json",
    ),
    (
        "session_terminal",
        "ExpertSessionTerminalV1",
        "expert-session-terminal-v1.schema.json",
    ),
)
_EVENT_SPEC = (
    (
        ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
        "ExpertSynchronizationAppliedPayloadV1",
        "expert-synchronization-applied-v1.schema.json",
    ),
    (
        ExpertEventKindV1.OBSERVATION_IGNORED,
        "ExpertObservationIgnoredPayloadV1",
        "expert-observation-ignored-v1.schema.json",
    ),
    (
        ExpertEventKindV1.OBSERVATION_REJECTED,
        "ExpertObservationRejectedPayloadV1",
        "expert-observation-rejected-v1.schema.json",
    ),
)
_EXPERT_INVENTORY = (
    "inci_tennis_expert/__init__.py",
    "inci_tennis_expert/contracts.py",
    "inci_tennis_expert/tennis_score.py",
    "inci_tennis_expert/market_book.py",
    "inci_tennis_expert/match_binding.py",
    "inci_tennis_expert/prematch_model.py",
    "inci_tennis_expert/synchronizer.py",
    "inci_tennis_expert/task6_fallback_normalizer.py",
    "inci_tennis_expert/win_probability.py",
    "inci_tennis_expert/calibration.py",
    "inci_tennis_expert/fee_schedule.py",
    "inci_tennis_expert/scalp_policy.py",
    "inci_tennis_expert/engine.py",
    "inci_tennis_expert/clip_journal.py",
    "inci_tennis_expert/state.py",
    "inci_tennis_expert/observation.py",
    "inci_tennis_expert/reducer.py",
    "inci_tennis_expert/journal_codec.py",
    "inci_tennis_expert/replay.py",
    "inci_tennis_expert/facade.py",
)
_IO_INVENTORY = (
    "inci_tennis_io/__init__.py",
    "inci_tennis_io/pinned_artifacts.py",
    "inci_tennis_io/ports.py",
    "inci_tennis_io/historical_store.py",
    "inci_tennis_io/expert_journal_store.py",
    "inci_tennis_io/facade.py",
    "inci_tennis_io/provider_readonly.py",
    "inci_tennis_io/sportradar_trial_transport.py",
    "inci_tennis_io/account_lock.py",
)
_KALSHI_CANDIDATE_ADAPTER_INVENTORY = (
    "inci_tennis_adapters/kalshi_candidate.py",
    (
        "inci_tennis_adapters/schemas/"
        "kalshi-orderbook-snapshot-synthetic-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "kalshi-orderbook-delta-synthetic-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "kalshi-market-lifecycle-synthetic-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "kalshi-public-trade-synthetic-candidate-v1.schema.json"
    ),
)
_KALSHI_CANDIDATE_SCHEMA_INVENTORY = (
    _KALSHI_CANDIDATE_ADAPTER_INVENTORY[1:]
)
_ADAPTER_INVENTORY = (
    "inci_tennis_adapters/__init__.py",
    "inci_tennis_adapters/candidate_contracts.py",
    "inci_tennis_adapters/registry.py",
    "inci_tennis_adapters/sportradar_tennis_v3.py",
    "inci_tennis_adapters/sportradar_trial_v3.py",
    *_KALSHI_CANDIDATE_ADAPTER_INVENTORY,
)
_RUNTIME_INVENTORY = (
    "inci_tennis_runtime/__init__.py",
    "inci_tennis_runtime/expert_controller.py",
    "inci_tennis_runtime/replay_service.py",
    "inci_tennis_runtime/provider_qualification_controller.py",
    "inci_tennis_runtime/shadow_runtime.py",
    "inci_tennis_runtime/shadow_cli.py",
    "inci_tennis_runtime/scalp_paper_observer.py",
    "inci_tennis_runtime/sportradar_trial_cli.py",
)
_DEPENDENCY_INVENTORY = ("pyproject.toml", "requirements.txt")
_PHASE1_INVENTORY = (
    "tennis_v1/__init__.py",
    "tennis_v1/adapter_contract.py",
    "tennis_v1/canonical.py",
    "tennis_v1/capture.py",
    "tennis_v1/codec.py",
    "tennis_v1/config.py",
    "tennis_v1/entitlements.py",
    "tennis_v1/events.py",
    "tennis_v1/fingerprints.py",
    "tennis_v1/ingress.py",
    "tennis_v1/mailbox.py",
    "tennis_v1/pinned_file.py",
    "tennis_v1/preflight.py",
    "tennis_v1/qualification_protocol.py",
    "tennis_v1/reducer.py",
    "tennis_v1/replay_core.py",
    "tennis_v1/retention.py",
    "tennis_v1/schemas/provider-entitlement-v1.schema.json",
    "tennis_v1/schemas/provider-permission-v1.schema.json",
    "tennis_v1/schemas/provider-qualification-trace-v1.schema.json",
    "tennis_v1/schemas/provider-qualification-v1.schema.json",
    "tennis_v1/schemas/retention-marker-v1.schema.json",
    "tennis_v1/sequencer.py",
    "tennis_v1/session.py",
    "tennis_v1/state.py",
    "tennis_v1/wal.py",
)
_EXPERT_RESOURCE_INVENTORY = (
    "inci_tennis_expert/schemas/binding-review-v1.schema.json",
    "inci_tennis_expert/schemas/expert-journal-group-v1.schema.json",
    "inci_tennis_expert/schemas/expert-journal-record-v1.schema.json",
    "inci_tennis_expert/schemas/expert-observation-ignored-v1.schema.json",
    "inci_tennis_expert/schemas/expert-observation-rejected-v1.schema.json",
    "inci_tennis_expert/schemas/expert-session-manifest-v1.schema.json",
    "inci_tennis_expert/schemas/expert-session-terminal-v1.schema.json",
    "inci_tennis_expert/schemas/expert-synchronization-applied-v1.schema.json",
    "inci_tennis_expert/schemas/match-binding-v1.schema.json",
    "inci_tennis_expert/schemas/task6-fallback-no-payload-v1.schema.json",
)
_IO_RESOURCE_INVENTORY = (
    "inci_tennis_io/schemas/historical-dataset-manifest-v1.schema.json",
    "inci_tennis_io/schemas/historical-entitlement-v1.schema.json",
)
_SOURCE_PACKAGE_NAMES = (
    "tennis_v1",
    "inci_tennis_expert",
    "inci_tennis_io",
    "inci_tennis_adapters",
    "inci_tennis_runtime",
)

_CANDIDATE_ADAPTER_SOURCE_INVENTORY = (
    "inci_tennis_adapters/candidate_contracts.py",
    "inci_tennis_adapters/registry.py",
    "inci_tennis_adapters/sportradar_tennis_v3.py",
)
_CANDIDATE_SCHEMA_INVENTORY = (
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-summary-v3-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-timeline-v3-candidate-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-transport-error-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-candidate-manifest-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-candidate-authorization-v1.schema.json"
    ),
    (
        "inci_tennis_adapters/schemas/"
        "sportradar-tennis-qualification-output-v1.schema.json"
    ),
)
_CANDIDATE_IO_SOURCE_INVENTORY = (
    "inci_tennis_io/ports.py",
    "inci_tennis_io/expert_journal_store.py",
    "inci_tennis_io/facade.py",
    "inci_tennis_io/provider_readonly.py",
)
_CANDIDATE_RUNTIME_SOURCE = (
    "inci_tennis_runtime/provider_qualification_controller.py"
)
_CANDIDATE_TOOL_SOURCE = "tools/qualify_sportradar_tennis_v3.py"
_CANDIDATE_TRACE_MAX_BYTES = 268_435_456
_CANDIDATE_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_CANDIDATE_TERMINAL_REASONS = frozenset(
    {
        "completed",
        "operator_stop",
        "authorization_expired",
        "session_expired",
        "retention_expired",
        "quota_denied",
        "transport_failed",
        "capture_failed",
        "parser_rejected",
        "source_seal_failed",
        "output_failed",
        "internal_contract_failure",
    }
)
_CANDIDATE_FAILURE_PAIRS = frozenset(
    {
        ("permit", "quota_denied"),
        ("permit", "contract_failed"),
        ("transport", "transport_failed"),
        ("transport", "contract_failed"),
        ("capture", "capture_failed"),
        ("capture", "contract_failed"),
        ("parser", "parser_rejected"),
        ("parser", "contract_failed"),
        ("output", "output_failed"),
        ("output", "contract_failed"),
    }
)
_CANDIDATE_FAILURE_TERMINAL_REASONS = {
    "quota_denied": "quota_denied",
    "transport_failed": "transport_failed",
    "capture_failed": "capture_failed",
    "parser_rejected": "parser_rejected",
    "output_failed": "output_failed",
    "contract_failed": "internal_contract_failure",
}
_CANDIDATE_PARSER_EVIDENCE_DOMAIN = (
    b"INCI-SPORTRADAR-CANDIDATE-PARSER-EVIDENCE-V1\0"
)
_CANDIDATE_CAPTURE_EVENT_TYPES_BY_ROUTE = {
    "summary": frozenset(
        {
            "sportradar_tennis_summary_v3",
            "sportradar_tennis_transport_error_v1",
        }
    ),
    "timeline": frozenset(
        {
            "sportradar_tennis_timeline_v3",
            "sportradar_tennis_transport_error_v1",
        }
    ),
}
_CANDIDATE_PARSER_REJECT_REASONS = frozenset(
    {
        "normalizer_schema_unknown",
        "normalizer_payload_invalid",
        "normalizer_contract_violation",
        "normalizer_exception",
    }
)
_SOURCE_PACKAGE_INVENTORIES = {
    "tennis_v1": _PHASE1_INVENTORY,
    "inci_tennis_expert": tuple(
        sorted((*_EXPERT_INVENTORY, *_EXPERT_RESOURCE_INVENTORY))
    ),
    "inci_tennis_io": tuple(sorted((*_IO_INVENTORY, *_IO_RESOURCE_INVENTORY))),
    "inci_tennis_adapters": tuple(
        sorted((*_ADAPTER_INVENTORY, *_CANDIDATE_SCHEMA_INVENTORY))
    ),
    "inci_tennis_runtime": _RUNTIME_INVENTORY,
}


@dataclass(frozen=True, slots=True)
class _SourcePackageAuthority:
    name: str
    module: ModuleType
    loader: SourceFileLoader
    origin: str
    package_path: str
    directory_fd: int
    directory_identity: tuple[int, ...]
    init_fd: int
    init_identity: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _SourceDependencyAuthority:
    basename: str
    fd: int
    identity: tuple[int, ...]


@dataclass(slots=True)
class _Root:
    token: ExpertJournalRootAuthorityV1
    grant: object
    coordinator: RetentionCoordinator
    clock_capability: object
    state_fd: int
    evidence_sessions_fd: int
    evidence_markers_fd: int
    account_lock_fd: int
    expert_fd: int
    sessions_fd: int
    markers_fd: int
    source_root_fd: int
    source_root_identity: tuple[int, ...]
    source_root_path: str
    source_packages: tuple[_SourcePackageAuthority, ...]
    source_dependencies: tuple[_SourceDependencyAuthority, ...]
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    active: bool = True
    last_environment: ExpertCurrentEnvironmentV1 | None = None
    last_validation_sampled_wall_ns: int | None = None
    source_content_cache: dict[
        str,
        tuple[tuple[int, ...], bytes],
    ] = field(default_factory=dict)


@dataclass(slots=True)
class _EnvironmentAuthority:
    root: _Root
    authorizer: ProviderPersistenceAuthorizer
    coordinator: RetentionCoordinator
    owner_pid: int
    owner_thread: threading.Thread
    consumed: bool = False


@dataclass(slots=True)
class _CandidateSourceSealAuthority:
    root: _Root
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    consumed: bool = False


@dataclass(slots=True)
class _CandidateOutputWriter:
    token: CandidateQualificationOutputWriterV1
    root: _Root
    parent_fd: int
    staging_fd: int
    trace_fd: int
    summary_fd: int
    staging_basename: str
    final_basename: str
    session_manifest: SessionManifest
    session_manifest_sha256: str
    candidate_manifest_sha256: str
    manifest_core_sha256: str
    candidate_authorization_sha256: str
    candidate_decision_sha256: str
    candidate_binding_sha256: str
    quota_closure_sha256: str
    candidate_source_seals_sha256: str
    match_binding_universe_sha256: str
    requested_provider_match_ids: tuple[str, ...]
    session_start_wall_ns: int
    maximum_candidate_trace_bytes: int
    retention_delete_by_ns: int
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    parent_identity: tuple[int, ...]
    staging_identity: tuple[int, ...]
    trace_identity: tuple[int, ...]
    summary_identity: tuple[int, ...]
    previous_record_sha256: str
    trace_prefix_bytes: bytearray = field(default_factory=bytearray)
    current_receipt: CandidateQualificationAppendReceiptV1 | None = None
    record_index: int = 0
    permit_count: int = 0
    capture_count: int = 0
    accepted_count: int = 0
    ignored_count: int = 0
    rejected_count: int = 0
    failure_count: int = 0
    last_recorded_wall_ns: int | None = None
    unmatched_permit: CandidateQualificationAppendReceiptV1 | None = None
    unmatched_permit_route: str | None = None
    unmatched_permit_provider_match_id: str | None = None
    unmatched_capture: CandidateQualificationAppendReceiptV1 | None = None
    unmatched_capture_event_type: str | None = None
    unmatched_capture_payload_sha256: str | None = None
    unmatched_capture_envelope_sha256: str | None = None
    failure_terminal_reason: str | None = None
    renamed: bool = False
    active: bool = True


@dataclass(slots=True)
class _Writer:
    token: ExpertJournalWriteCapabilityV1
    root: _Root
    manifest: ExpertSessionManifestV1
    cursor: ExpertJournalCursorV1
    initial_cursor: ExpertJournalCursorV1
    authorizer: ProviderPersistenceAuthorizer
    coordinator: RetentionCoordinator
    journal_fd: int
    reserve_fd: int
    journal_basename: str
    reserve_basename: str
    marker_basename: str
    marker_bytes: bytes
    owner_pid: int
    owner_thread: threading.Thread
    generation: int
    reserve_identity: _ReserveIdentity | None
    journal_identity: _JournalIdentity
    state: str = "ordinary_ready"
    pending_receipt: DurableExpertAppendReceiptV1 | None = None
    pending_cursor: ExpertJournalCursorV1 | None = None
    tail: object | None = None
    terminal_pair: tuple[PersistedEvent, ExpertSessionTerminalV1] | None = None
    prewrite_capacity_denied: bool = False


@dataclass(frozen=True, slots=True)
class _ReserveIdentity:
    session_id: str
    basename: str
    device: int
    inode: int
    uid: int
    mode: int
    link_count: int
    logical_size: int
    allocated_bytes: int
    mtime_ns: int
    ctime_ns: int
    writer_generation: int


@dataclass(frozen=True, slots=True)
class _JournalIdentity:
    device: int
    inode: int
    uid: int
    mode: int
    link_count: int
    size: int
    mtime_ns: int
    ctime_ns: int
    writer_generation: int


@dataclass(slots=True)
class _Reader:
    token: ExpertJournalReadCapabilityV1
    root: _Root
    manifest: ExpertSessionManifestV1
    fd: int
    offset: int
    cursor: ExpertJournalCursorV1 | None
    owner_pid: int
    owner_thread: threading.Thread
    fd_identity: tuple[int, int, int, int, int]
    closed: bool = False
    manifest_read: bool = False
    terminal: ExpertSessionTerminalV1 | None = None
    group_count: int = 0
    record_count: int = 0
    last_good_offset: int = 0
    last_frame_sequence: int = 0
    issue: ExpertJournalScanIssueV1 | None = None
    replay_authority: ExpertReplayConstructionAuthorityV1 | None = None


@dataclass(slots=True)
class _Purge:
    root: _Root
    manifest: ExpertSessionManifestV1
    consumed: bool = False


@dataclass(slots=True)
class _AppendPermit:
    writer: _Writer
    frame: bytes
    group: ExpertJournalGroupV1
    payloads: tuple[bytes, ...]
    candidate_cursor: ExpertJournalCursorV1
    consumed: bool = False


@dataclass(slots=True)
class _TerminalPermit:
    writer: _Writer
    frame: bytes
    terminal: ExpertSessionTerminalV1
    consumed: bool = False


@dataclass(slots=True)
class _EmergencyPermit:
    writer: _Writer
    group_frame: bytes
    terminal_frame: bytes
    group: ExpertJournalGroupV1
    terminal: ExpertSessionTerminalV1
    candidate_cursor: ExpertJournalCursorV1
    consumed: bool = False


_ROOTS: dict[ExpertJournalRootAuthorityV1, _Root] = {}
_ENVIRONMENTS: dict[
    ExpertEnvironmentCollectionAuthorityV1, _EnvironmentAuthority
] = {}
_WRITERS: dict[ExpertJournalWriteCapabilityV1, _Writer] = {}
_READERS: dict[ExpertJournalReadCapabilityV1, _Reader] = {}
_PURGES: dict[ExpertJournalPurgeCapabilityV1, _Purge] = {}
_APPEND_PERMITS: dict[ExpertJournalAppendPermitV1, _AppendPermit] = {}
_TERMINAL_PERMITS: dict[ExpertJournalTerminalPermitV1, _TerminalPermit] = {}
_EMERGENCY_PERMITS: dict[
    ExpertEmergencyAppendPermitV1, _EmergencyPermit
] = {}
_REPLAYS: dict[ExpertReplayConstructionAuthorityV1, dict[str, object]] = {}
_CANDIDATE_SOURCE_SEALS: dict[
    CandidateSourceSealCollectionAuthorityV1,
    _CandidateSourceSealAuthority,
] = {}
_CANDIDATE_OUTPUT_WRITERS: dict[
    CandidateQualificationOutputWriterV1,
    _CandidateOutputWriter,
] = {}


def _token(cls: type[Any]) -> Any:
    return object.__new__(cls)


def _mode(value: os.stat_result) -> int:
    return stat.S_IMODE(value.st_mode)


def _require_directory(fd: int) -> os.stat_result:
    value = os.fstat(fd)
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.getuid()
        or _mode(value) != 0o700
    ):
        raise ValueError("expert_directory_invalid")
    return value


def _source_directory_stat(value: os.stat_result) -> tuple[int, ...]:
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid():
        raise ValueError("expert_source_root_invalid")
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _source_root_identity(fd: int) -> tuple[int, ...]:
    return _source_directory_stat(os.fstat(fd))


def _next_local_root_generation() -> int:
    global _LOCAL_ROOT_GENERATION
    _LOCAL_ROOT_GENERATION += 1
    return _LOCAL_ROOT_GENERATION


def _require_file(
    fd: int,
    *,
    expected_size: int | None = None,
    physical: bool = False,
) -> os.stat_result:
    value = os.fstat(fd)
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or _mode(value) != 0o600
        or value.st_nlink != 1
        or expected_size is not None
        and value.st_size != expected_size
        or physical
        and value.st_blocks * 512 < (expected_size or 0)
    ):
        raise ValueError("expert_file_invalid")
    return value


def _mkdir_open(parent_fd: int, name: str) -> tuple[int, bool]:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    fd = os.open(name, _OPEN_DIRECTORY_FLAGS, dir_fd=parent_fd)
    if created:
        os.fchmod(fd, 0o700)
    _require_directory(fd)
    return fd, created


def _same_file_identity(
    descriptor: os.stat_result,
    named: os.stat_result,
) -> bool:
    return _stat_identity_observation(descriptor) == (
        _stat_identity_observation(named)
    )


def _stat_identity_observation(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _descriptor_identity_observation(
    value: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
    )


def _is_missing_entry_error(error: OSError) -> bool:
    return error.errno in {errno.ENOENT, errno.ENOTDIR}


def _named_file_identity_observation(
    directory_fd: int,
    basename: str,
) -> tuple[int, int, int, int, int, int, int, int] | None:
    try:
        value = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except OSError as error:
        if _is_missing_entry_error(error):
            return None
        raise
    return _stat_identity_observation(value)


def _valid_bootstrap_file_observation(
    value: tuple[int, int, int, int, int, int, int, int] | None,
) -> bool:
    return (
        value is not None
        and stat.S_ISREG(value[3])
        and value[2] == os.getuid()
        and stat.S_IMODE(value[3]) == 0o600
        and value[4] == 1
    )


def _stable_named_file_read(
    *,
    fd: int,
    directory_fd: int,
    basename: str,
    offset: int,
    length: int,
) -> tuple[bytes, os.stat_result]:
    before = _require_file(fd)
    named_before = os.stat(
        basename,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    if not _same_file_identity(before, named_before):
        raise ValueError("expert_named_read_identity_invalid")
    content = _pread_exact(fd, offset, length)
    after = _require_file(fd)
    named_after = os.stat(
        basename,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    if (
        not _same_file_identity(before, after)
        or not _same_file_identity(after, named_after)
    ):
        raise ValueError("expert_named_read_identity_invalid")
    return content, after


def _reserve_identity(
    root: _Root,
    *,
    fd: int,
    session_id: str,
    basename: str,
    generation: int,
) -> _ReserveIdentity:
    descriptor = _require_file(
        fd,
        expected_size=EXPERT_EMERGENCY_RESERVE_BYTES,
        physical=True,
    )
    named = os.stat(
        basename,
        dir_fd=root.sessions_fd,
        follow_symlinks=False,
    )
    if not _same_file_identity(descriptor, named):
        raise ValueError("expert_reserve_identity_invalid")
    return _ReserveIdentity(
        session_id,
        basename,
        descriptor.st_dev,
        descriptor.st_ino,
        descriptor.st_uid,
        _mode(descriptor),
        descriptor.st_nlink,
        descriptor.st_size,
        descriptor.st_blocks * 512,
        descriptor.st_mtime_ns,
        descriptor.st_ctime_ns,
        generation,
    )


def _validate_reserve(state: _Writer) -> None:
    identity = state.reserve_identity
    if (
        identity is None
        or state.reserve_fd < 0
        or identity.session_id != state.manifest.session_id
        or identity.basename != state.reserve_basename
        or identity.writer_generation != state.generation
        or _reserve_identity(
            state.root,
            fd=state.reserve_fd,
            session_id=state.manifest.session_id,
            basename=state.reserve_basename,
            generation=state.generation,
        )
        != identity
    ):
        raise ValueError("expert_reserve_identity_invalid")


def _journal_identity(
    root: _Root,
    *,
    fd: int,
    basename: str,
    generation: int,
) -> _JournalIdentity:
    descriptor = _require_file(fd)
    named = os.stat(
        basename,
        dir_fd=root.sessions_fd,
        follow_symlinks=False,
    )
    if not _same_file_identity(descriptor, named):
        raise ValueError("expert_journal_identity_invalid")
    return _JournalIdentity(
        descriptor.st_dev,
        descriptor.st_ino,
        descriptor.st_uid,
        _mode(descriptor),
        descriptor.st_nlink,
        descriptor.st_size,
        descriptor.st_mtime_ns,
        descriptor.st_ctime_ns,
        generation,
    )


def _validate_journal(state: _Writer) -> None:
    if (
        state.generation != state.root.generation
        or _journal_identity(
            state.root,
            fd=state.journal_fd,
            basename=state.journal_basename,
            generation=state.generation,
        )
        != state.journal_identity
    ):
        raise ValueError("expert_journal_identity_invalid")


def _complete_write(fd: int, content: bytes) -> None:
    view = memoryview(content)
    written = 0
    while written < len(content):
        count = os.write(fd, view[written:])
        if type(count) is not int or count <= 0:
            raise OSError("expert_complete_write_failed")
        written += count


def _pread_exact(fd: int, offset: int, length: int) -> bytes:
    chunks: list[bytes] = []
    consumed = 0
    while consumed < length:
        chunk = os.pread(fd, length - consumed, offset + consumed)
        if not chunk:
            break
        chunks.append(chunk)
        consumed += len(chunk)
    return b"".join(chunks)


def _gated_pread_exact(
    fd: int,
    offset: int,
    length: int,
    *,
    gate: Callable[[], None],
) -> bytes:
    chunks: list[bytes] = []
    consumed = 0
    while consumed < length:
        gate()
        chunk = os.pread(fd, length - consumed, offset + consumed)
        gate()
        if not chunk:
            break
        chunks.append(chunk)
        consumed += len(chunk)
    return b"".join(chunks)


def _close_quietly(fd: int) -> None:
    if fd >= 0:
        try:
            os.close(fd)
        except OSError:
            pass


def _close_root_governed_temporary(
    root: _Root,
    fd: int,
    *,
    message: str,
) -> None:
    """Consume a temporary descriptor and surface uncertain close state."""
    if fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        if root.active:
            _fatal_root(root)
        raise OSError(message) from None


def _fatal_root(root: _Root) -> None:
    if not root.active:
        return
    root.active = False
    replay_sessions = {
        manifest.session_id
        for replay in _REPLAYS.values()
        if replay.get("root") is root
        and not replay.get("closed")
        and type(
            manifest := replay.get("manifest")
        ) is SessionManifest
    }
    direct_purge_success: dict[str, bool] = {}
    # Purge companion artifacts while the directory descriptors are still
    # usable.  Do not invalidate the replay objects here: their next
    # rightful-owner operation must expose the typed item-6 denial.
    for session_id in replay_sessions:
        try:
            _unlink_if_present(
                root.sessions_fd,
                _journal_basename(session_id),
            )
            _unlink_if_present(
                root.sessions_fd,
                _reserve_basename(session_id),
            )
            os.fsync(root.sessions_fd)
            _unlink_if_present(
                root.markers_fd,
                _marker_basename(session_id),
            )
            os.fsync(root.markers_fd)
            direct_purge_success[session_id] = True
        except Exception:
            direct_purge_success[session_id] = False
    try:
        root.coordinator.recover_and_purge()
    except Exception:
        pass
    for writer in tuple(_WRITERS.values()):
        if writer.root is root:
            _close_quietly(writer.reserve_fd)
            writer.reserve_fd = -1
            writer.reserve_identity = None
            _close_quietly(writer.journal_fd)
            writer.journal_fd = -1
            writer.state = "poisoned"
    for reader in tuple(_READERS.values()):
        if (
            reader.root is root
            and reader.replay_authority is None
            and not reader.closed
        ):
            _close_quietly(reader.fd)
            reader.closed = True
    for environment in tuple(_ENVIRONMENTS.values()):
        if environment.root is root:
            environment.consumed = True
    for authority in tuple(_CANDIDATE_SOURCE_SEALS.values()):
        if authority.root is root:
            authority.consumed = True
    for candidate_writer in tuple(_CANDIDATE_OUTPUT_WRITERS.values()):
        if candidate_writer.root is root:
            candidate_writer.active = False
            for candidate_fd in (
                candidate_writer.trace_fd,
                candidate_writer.summary_fd,
                candidate_writer.staging_fd,
                candidate_writer.parent_fd,
            ):
                _close_quietly(candidate_fd)
    for purge in tuple(_PURGES.values()):
        if purge.root is root:
            purge.consumed = True
    for replay in tuple(_REPLAYS.values()):
        if replay.get("root") is root:
            replay_manifest = replay.get("manifest")
            purge_proven = (
                type(replay_manifest) is SessionManifest
                and direct_purge_success.get(
                    replay_manifest.session_id
                )
                is True
            )
            try:
                _close_replay_owned_readers(replay)
            except _ReplayCloseUncertain:
                pass
            finally:
                _purge_replay_denial_payloads(replay)
            # Preserve the logical replay edge so its next governed
            # operation can publish the required typed item-6 denial.
            replay["root_failed"] = True
            replay["root_failed_purge_proven"] = purge_proven
            replay["root_failed_purge_uncertain"] = not purge_proven
    for fd in (
        *(
            descriptor
            for package in root.source_packages
            for descriptor in (package.init_fd, package.directory_fd)
        ),
        *(dependency.fd for dependency in root.source_dependencies),
        root.markers_fd,
        root.sessions_fd,
        root.expert_fd,
        root.source_root_fd,
    ):
        _close_quietly(fd)
    try:
        _revoke_expert_state_root_account_lock_grant(root.grant)
    except Exception:
        pass


def _require_root(authority: ExpertJournalRootAuthorityV1) -> _Root:
    if type(authority) is not ExpertJournalRootAuthorityV1:
        raise TypeError("authority")
    root = _ROOTS.get(authority)
    if (
        root is None
        or not root.active
        or root.owner_pid != os.getpid()
        or root.owner_thread is not threading.current_thread()
    ):
        raise ValueError("expert_root_authority_invalid")
    try:
        sampled = _phase1_sample_wall_ns(root.clock_capability)
        if type(sampled) is not int or sampled < 0:
            raise ValueError
    except Exception:
        _fatal_root(root)
        raise ValueError("expert_root_authority_invalid")
    root.last_validation_sampled_wall_ns = sampled
    try:
        _require_directory(root.state_fd)
        _require_directory(root.evidence_sessions_fd)
        _require_directory(root.evidence_markers_fd)
        _require_directory(root.expert_fd)
        _require_directory(root.sessions_fd)
        _require_directory(root.markers_fd)
        _validate_source_root(root)
    except Exception:
        _fatal_root(root)
        raise ValueError("expert_root_authority_invalid") from None
    return root


def acquire_expert_journal_root(
    request: ExpertStateRootAccountLockRequestV1,
) -> ExpertJournalRootAuthorityV1:
    if type(request) is not ExpertStateRootAccountLockRequestV1:
        raise TypeError("request")
    grant = None
    opened: list[int] = []
    created_expert = False
    created_sessions = False
    created_markers = False
    try:
        grant = _consume_expert_state_root_account_lock_request(request)
        state_fd = object.__getattribute__(grant, "_state_fd")
        evidence_sessions_fd = object.__getattribute__(grant, "_sessions_fd")
        evidence_markers_fd = object.__getattribute__(grant, "_markers_fd")
        account_lock_fd = object.__getattribute__(grant, "_lock_fd")
        clock_capability = object.__getattribute__(grant, "_clock_capability")
        coordinator = object.__getattribute__(grant, "_dispatch")
        (
            source_root_fd,
            source_identity,
            source_root_path,
            source_packages,
            source_dependencies,
        ) = _acquire_source_distribution(opened)
        generation = _next_local_root_generation()
        try:
            os.mkdir("expert-v1", 0o700, dir_fd=state_fd)
            created_expert = True
        except FileExistsError:
            pass
        expert_fd = os.open(
            "expert-v1",
            _OPEN_DIRECTORY_FLAGS,
            dir_fd=state_fd,
        )
        opened.append(expert_fd)
        if created_expert:
            os.fchmod(expert_fd, 0o700)
        sessions_fd, created_sessions = _mkdir_open(expert_fd, "sessions")
        opened.append(sessions_fd)
        markers_fd, created_markers = _mkdir_open(expert_fd, "markers")
        opened.append(markers_fd)
        identities = [
            (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            for fd in (
                state_fd,
                evidence_sessions_fd,
                evidence_markers_fd,
                expert_fd,
                sessions_fd,
                markers_fd,
            )
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("expert_root_identity_invalid")
        token = _token(ExpertJournalRootAuthorityV1)
        root = _Root(
            token,
            grant,
            coordinator,
            clock_capability,
            state_fd,
            evidence_sessions_fd,
            evidence_markers_fd,
            account_lock_fd,
            expert_fd,
            sessions_fd,
            markers_fd,
            source_root_fd,
            source_identity,
            source_root_path,
            source_packages,
            source_dependencies,
            os.getpid(),
            threading.current_thread(),
            generation,
        )
        _ROOTS[token] = root
        return token
    except Exception as acquisition_error:
        if grant is not None and "expert_fd" in locals():
            for created, name in (
                (created_markers, "markers"),
                (created_sessions, "sessions"),
            ):
                if created:
                    try:
                        os.rmdir(name, dir_fd=expert_fd)
                    except OSError:
                        pass
        for fd in reversed(opened):
            _close_quietly(fd)
        if created_expert and grant is not None:
            try:
                os.rmdir("expert-v1", dir_fd=object.__getattribute__(grant, "_state_fd"))
            except OSError:
                pass
        if grant is not None:
            try:
                _revoke_expert_state_root_account_lock_grant(grant)
            except Exception:
                pass
        if (
            type(acquisition_error) is OSError
            and str(acquisition_error)
            == "expert_source_descriptor_close_uncertain"
        ):
            raise OSError(
                "expert_source_descriptor_close_uncertain"
            ) from None
        raise ValueError("expert_root_acquisition_failed") from None


def sample_expert_retention_wall_ns(
    authority: ExpertJournalRootAuthorityV1,
) -> int:
    with _LOCK:
        root = _require_root(authority)
        try:
            value = _phase1_sample_wall_ns(root.clock_capability)
        except Exception as error:
            _fatal_root(root)
            raise
        if type(value) is not int or value < 0:
            _fatal_root(root)
            raise ValueError("expert_retention_clock_invalid")
        return value


def _sample_replay_prepare_wall_ns(
    root: _Root,
    *,
    deadline_ns: int,
) -> int:
    if type(deadline_ns) is not int or deadline_ns <= 0:
        raise ValueError("expert_root_authority_invalid")
    try:
        value = _phase1_sample_wall_ns(root.clock_capability)
    except Exception:
        raise ValueError("expert_root_authority_invalid") from None
    if type(value) is not int or value < 0:
        raise ValueError("expert_root_authority_invalid")
    root.last_validation_sampled_wall_ns = value
    return value


def _last_valid_replay_sample(
    root: _Root,
    state: dict[str, object] | None = None,
) -> int:
    candidates = (
        (state.get("_last_sampled_wall_ns"),)
        if state is not None
        else (root.last_validation_sampled_wall_ns,)
    )
    for candidate in candidates:
        if type(candidate) is int and candidate >= 0:
            return candidate
    raise ValueError("expert_replay_sample_invalid")


def _bound_authorizer_manifest(
    root: _Root,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> SessionManifest:
    if (
        type(persistence_authorizer) is not ProviderPersistenceAuthorizer
        or type(coordinator) is not RetentionCoordinator
        or coordinator is not root.coordinator
        or persistence_authorizer.coordinator is not coordinator
        or type(persistence_authorizer.session_manifest) is not SessionManifest
    ):
        raise ExpertLiveAuthorizationDenied()
    return persistence_authorizer.session_manifest


def _require_authorizer(
    root: _Root,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> SessionManifest:
    manifest = _bound_authorizer_manifest(
        root,
        persistence_authorizer,
        coordinator,
    )
    try:
        coordinator.require_provider_operation()
        decision = persistence_authorizer.authorize_analysis()
        if decision is not persistence_authorizer.bound_decision:
            raise ValueError
    except Exception:
        raise ExpertLiveAuthorizationDenied() from None
    if persistence_authorizer.session_manifest is not manifest:
        raise ExpertLiveAuthorizationDenied()
    return manifest


def _require_prepare_replay_authorizer(
    root: _Root,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> SessionManifest:
    manifest = _bound_authorizer_manifest(
        root,
        persistence_authorizer,
        coordinator,
    )
    try:
        # Prepare must diagnose invalid Phase-1 evidence through the root's
        # bounded read-only descriptor authority.  The normal coordinator
        # operation gate validates the Phase-1 inventory itself, so using it
        # here would turn a missing evidence entry into authorization loss
        # before the diagnostic proof can be constructed.
        decision = persistence_authorizer.authorize_analysis()
        if decision is not persistence_authorizer.bound_decision:
            raise ValueError
    except Exception:
        raise ExpertLiveAuthorizationDenied() from None
    if persistence_authorizer.session_manifest is not manifest:
        raise ExpertLiveAuthorizationDenied()
    return manifest


def _validate_replay_prepare_root_after_access_gate(root: _Root) -> None:
    try:
        if (
            not root.active
            or _ROOTS.get(root.token) is not root
            or root.owner_pid != os.getpid()
            or root.owner_thread is not threading.current_thread()
        ):
            raise ValueError
        _require_directory(root.state_fd)
        _require_directory(root.evidence_sessions_fd)
        _require_directory(root.evidence_markers_fd)
        _require_directory(root.expert_fd)
        _require_directory(root.sessions_fd)
        _require_directory(root.markers_fd)
        _validate_source_descriptor_root(root)
    except OSError:
        raise
    except Exception:
        raise ValueError("expert_root_authority_invalid") from None


def issue_expert_environment_collection_authority(
    authority: ExpertJournalRootAuthorityV1,
    *,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ExpertEnvironmentCollectionAuthorityV1:
    with _LOCK:
        root = _require_root(authority)
        manifest = _require_authorizer(
            root,
            persistence_authorizer,
            coordinator,
        )
        if sample_expert_retention_wall_ns(authority) >= min(
            manifest.required_retention_until_ns,
            manifest.analysis_expires_at_ns,
        ):
            raise ExpertLiveAuthorizationDenied()
        token = _token(ExpertEnvironmentCollectionAuthorityV1)
        _ENVIRONMENTS[token] = _EnvironmentAuthority(
            root,
            persistence_authorizer,
            coordinator,
            os.getpid(),
            threading.current_thread(),
        )
        return token


def _inventory_digest(
    source_root: Path,
    inventory: tuple[str, ...],
    domain: bytes,
) -> str:
    projection: list[tuple[str, str]] = []
    for logical in inventory:
        path = source_root / logical
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_nlink != 1
        ):
            raise ValueError("expert_environment_inventory_invalid")
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("expert_environment_inventory_invalid")
        projection.append((logical, sha256(content).hexdigest()))
    return sha256(domain + canonical_expert_bytes(tuple(projection))).hexdigest()


def _validate_source_descriptor_root(root: _Root) -> None:
    if (
        _source_root_identity(root.source_root_fd)
        != root.source_root_identity
    ):
        raise ValueError("expert_source_root_invalid")


def _source_file_stat(value: os.stat_result) -> tuple[int, ...]:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or value.st_nlink != 1
        or value.st_size > 16_777_216
    ):
        raise ValueError("expert_environment_inventory_invalid")
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _absolute_real_source_path(value: object) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError("expert_source_root_invalid")
    if (
        not os.path.isabs(value)
        or os.path.abspath(value) != value
        or os.path.normpath(value) != value
        or os.path.realpath(value) != value
    ):
        raise ValueError("expert_source_root_invalid")
    return value


def _loaded_source_coordinates(
    package_name: str,
) -> tuple[
    ModuleType,
    ModuleSpec,
    SourceFileLoader,
    str,
    str,
    str,
]:
    if package_name not in _SOURCE_PACKAGE_NAMES:
        raise ValueError("expert_source_root_invalid")
    module = sys.modules.get(package_name)
    if module is None:
        if package_name == "tennis_v1":
            module = __import__("tennis_v1")
        elif package_name == "inci_tennis_expert":
            import inci_tennis_expert as module
        elif package_name == "inci_tennis_io":
            import inci_tennis_io as module
        elif package_name == "inci_tennis_adapters":
            import inci_tennis_adapters as module
        elif package_name == "inci_tennis_runtime":
            module = __import__("inci_tennis_runtime")
        else:
            raise ValueError("expert_source_root_invalid")
        if sys.modules.get(package_name) is not module:
            raise ValueError("expert_source_root_invalid")
    if type(module) is not ModuleType:
        raise ValueError("expert_source_root_invalid")
    spec = getattr(module, "__spec__", None)
    if type(spec) is not ModuleSpec or spec.name != package_name:
        raise ValueError("expert_source_root_invalid")
    origin = _absolute_real_source_path(
        getattr(module, "__file__", None)
    )
    if spec.origin != origin:
        raise ValueError("expert_source_root_invalid")
    package_path_value = getattr(module, "__path__", None)
    spec_paths = spec.submodule_search_locations
    if (
        type(package_path_value) is not list
        or len(package_path_value) != 1
        or type(spec_paths) is not list
        or len(spec_paths) != 1
    ):
        raise ValueError("expert_source_root_invalid")
    package_path = _absolute_real_source_path(package_path_value[0])
    if spec_paths[0] != package_path:
        raise ValueError("expert_source_root_invalid")
    parent = _absolute_real_source_path(os.path.dirname(package_path))
    if (
        package_path != os.path.join(parent, package_name)
        or origin != os.path.join(package_path, "__init__.py")
    ):
        raise ValueError("expert_source_root_invalid")
    loader = getattr(module, "__loader__", None)
    if (
        type(loader) is not SourceFileLoader
        or spec.loader is not loader
        or loader.name != package_name
        or loader.path != origin
    ):
        raise ValueError("expert_source_root_invalid")
    return module, spec, loader, origin, package_path, parent


def _source_named_file_identity(
    directory_fd: int,
    basename: str,
) -> tuple[int, ...]:
    named_before = os.stat(
        basename,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    expected = _source_file_stat(named_before)
    descriptor = os.open(
        basename,
        _OPEN_FILE_READ_FLAGS,
        dir_fd=directory_fd,
    )
    try:
        if _source_file_stat(os.fstat(descriptor)) != expected:
            raise ValueError("expert_source_root_invalid")
        named_after = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if _source_file_stat(named_after) != expected:
            raise ValueError("expert_source_root_invalid")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise OSError(
                "expert_source_descriptor_close_uncertain"
            ) from None
    return expected


def _acquire_source_dependency(
    source_root_fd: int,
    basename: str,
    opened: list[int],
) -> _SourceDependencyAuthority:
    expected = _source_named_file_identity(source_root_fd, basename)
    descriptor = os.open(
        basename,
        _OPEN_FILE_READ_FLAGS,
        dir_fd=source_root_fd,
    )
    opened.append(descriptor)
    identity = _source_file_stat(os.fstat(descriptor))
    named_after = _source_file_stat(
        os.stat(
            basename,
            dir_fd=source_root_fd,
            follow_symlinks=False,
        )
    )
    if identity != expected or named_after != identity:
        raise ValueError("expert_source_root_invalid")
    return _SourceDependencyAuthority(
        basename,
        descriptor,
        identity,
    )


def _acquire_source_distribution(
    opened: list[int],
) -> tuple[
    int,
    tuple[int, ...],
    str,
    tuple[_SourcePackageAuthority, ...],
    tuple[_SourceDependencyAuthority, ...],
]:
    coordinates = tuple(
        (package_name, *_loaded_source_coordinates(package_name))
        for package_name in _SOURCE_PACKAGE_NAMES
    )
    parents = {item[-1] for item in coordinates}
    if len(parents) != 1:
        raise ValueError("expert_source_root_invalid")
    source_root_path = parents.pop()
    source_root_fd = os.open(
        source_root_path,
        _OPEN_DIRECTORY_FLAGS,
    )
    opened.append(source_root_fd)
    source_root_identity = _source_root_identity(source_root_fd)
    packages: list[_SourcePackageAuthority] = []
    for (
        package_name,
        module,
        _,
        loader,
        origin,
        package_path,
        _,
    ) in coordinates:
        named_directory = os.stat(
            package_name,
            dir_fd=source_root_fd,
            follow_symlinks=False,
        )
        expected_directory = _source_directory_stat(named_directory)
        directory_fd = os.open(
            package_name,
            _OPEN_DIRECTORY_FLAGS,
            dir_fd=source_root_fd,
        )
        opened.append(directory_fd)
        directory_identity = _source_directory_stat(
            os.fstat(directory_fd)
        )
        if (
            directory_identity != expected_directory
            or _source_directory_stat(
                os.stat(package_path, follow_symlinks=False)
            )
            != directory_identity
        ):
            raise ValueError("expert_source_root_invalid")
        named_init = os.stat(
            "__init__.py",
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        expected_init = _source_file_stat(named_init)
        init_fd = os.open(
            "__init__.py",
            _OPEN_FILE_READ_FLAGS,
            dir_fd=directory_fd,
        )
        opened.append(init_fd)
        init_identity = _source_file_stat(os.fstat(init_fd))
        if (
            init_identity != expected_init
            or _source_file_stat(
                os.stat(origin, follow_symlinks=False)
            )
            != init_identity
        ):
            raise ValueError("expert_source_root_invalid")
        packages.append(
            _SourcePackageAuthority(
                package_name,
                module,
                loader,
                origin,
                package_path,
                directory_fd,
                directory_identity,
                init_fd,
                init_identity,
            )
        )
    dependencies = tuple(
        _acquire_source_dependency(
            source_root_fd,
            basename,
            opened,
        )
        for basename in _DEPENDENCY_INVENTORY
    )
    for package in packages:
        (
            module,
            _,
            loader,
            origin,
            package_path,
            parent,
        ) = _loaded_source_coordinates(package.name)
        if (
            module is not package.module
            or loader is not package.loader
            or origin != package.origin
            or package_path != package.package_path
            or parent != source_root_path
        ):
            raise ValueError("expert_source_root_invalid")
        directory_identity = _source_directory_stat(
            os.fstat(package.directory_fd)
        )
        init_identity = _source_file_stat(os.fstat(package.init_fd))
        if (
            directory_identity != package.directory_identity
            or _source_directory_stat(
                os.stat(
                    package.name,
                    dir_fd=source_root_fd,
                    follow_symlinks=False,
                )
            )
            != directory_identity
            or _source_directory_stat(
                os.stat(
                    package.package_path,
                    follow_symlinks=False,
                )
            )
            != directory_identity
            or init_identity != package.init_identity
            or _source_file_stat(
                os.stat(
                    "__init__.py",
                    dir_fd=package.directory_fd,
                    follow_symlinks=False,
                )
            )
            != init_identity
            or _source_file_stat(
                os.stat(
                    package.origin,
                    follow_symlinks=False,
                )
            )
            != init_identity
        ):
            raise ValueError("expert_source_root_invalid")
    if _source_root_identity(source_root_fd) != source_root_identity:
        raise ValueError("expert_source_root_invalid")
    for basename, dependency in zip(
        _DEPENDENCY_INVENTORY,
        dependencies,
        strict=True,
    ):
        if (
            dependency.basename != basename
            or _source_file_stat(os.fstat(dependency.fd))
            != dependency.identity
            or _source_file_stat(
                os.stat(
                    basename,
                    dir_fd=source_root_fd,
                    follow_symlinks=False,
                )
            )
            != dependency.identity
        ):
            raise ValueError("expert_source_root_invalid")
    return (
        source_root_fd,
        source_root_identity,
        source_root_path,
        tuple(packages),
        dependencies,
    )


def _validate_source_distribution(root: _Root) -> None:
    try:
        if (
            _source_root_identity(root.source_root_fd)
            != root.source_root_identity
            or _absolute_real_source_path(root.source_root_path)
            != root.source_root_path
            or len(root.source_packages) != len(_SOURCE_PACKAGE_NAMES)
            or len(root.source_dependencies)
            != len(_DEPENDENCY_INVENTORY)
        ):
            raise ValueError
        for package_name, package in zip(
            _SOURCE_PACKAGE_NAMES,
            root.source_packages,
            strict=True,
        ):
            (
                module,
                _,
                loader,
                origin,
                package_path,
                parent,
            ) = _loaded_source_coordinates(package_name)
            if (
                package.name != package_name
                or module is not package.module
                or loader is not package.loader
                or origin != package.origin
                or package_path != package.package_path
                or parent != root.source_root_path
            ):
                raise ValueError
            directory_identity = _source_directory_stat(
                os.fstat(package.directory_fd)
            )
            if (
                directory_identity != package.directory_identity
                or _source_directory_stat(
                    os.stat(
                        package_name,
                        dir_fd=root.source_root_fd,
                        follow_symlinks=False,
                    )
                )
                != directory_identity
                or _source_directory_stat(
                    os.stat(
                        package.package_path,
                        follow_symlinks=False,
                    )
                )
                != directory_identity
            ):
                raise ValueError
            init_identity = _source_file_stat(
                os.fstat(package.init_fd)
            )
            if (
                init_identity != package.init_identity
                or _source_file_stat(
                    os.stat(
                        "__init__.py",
                        dir_fd=package.directory_fd,
                        follow_symlinks=False,
                    )
                )
                != init_identity
                or _source_file_stat(
                    os.stat(
                        package.origin,
                        follow_symlinks=False,
                    )
                )
                != init_identity
            ):
                raise ValueError
        for basename, dependency in zip(
            _DEPENDENCY_INVENTORY,
            root.source_dependencies,
            strict=True,
        ):
            if (
                dependency.basename != basename
                or _source_file_stat(os.fstat(dependency.fd))
                != dependency.identity
                or _source_file_stat(
                    os.stat(
                        basename,
                        dir_fd=root.source_root_fd,
                        follow_symlinks=False,
                    )
                )
                != dependency.identity
            ):
                raise ValueError
        if (
            _source_root_identity(root.source_root_fd)
            != root.source_root_identity
        ):
            raise ValueError
    except Exception:
        raise ValueError("expert_source_root_invalid") from None


def _validate_source_root(root: _Root) -> None:
    _validate_source_distribution(root)


def _run_environment_io_gate(
    gate: Callable[[], None] | None,
) -> None:
    if gate is not None:
        gate()


def _close_environment_descriptor(root: _Root, descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        root.source_content_cache.clear()
        root.last_environment = None
        _fatal_root(root)
        raise OSError(
            "expert_environment_descriptor_close_uncertain"
        ) from None


def _read_source_named_file(
    root: _Root,
    directory_fd: int,
    basename: str,
    *,
    logical: str,
    gate: Callable[[], None] | None = None,
) -> bytes:
    # Metadata and cached bytes are covered by the enclosing logical-file
    # and fingerprint gates.  Every actual byte read remains immediately
    # bracketed inside _gated_pread_exact.
    _validate_source_descriptor_root(root)
    named_before = os.stat(
        basename,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    expected = _source_file_stat(named_before)
    cached = root.source_content_cache.get(logical)
    if cached is not None and cached[0] == expected:
        named_after = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if _source_file_stat(named_after) != expected:
            raise ValueError("expert_environment_inventory_invalid")
        _validate_source_descriptor_root(root)
        return cached[1]
    descriptor = os.open(
        basename,
        _OPEN_FILE_READ_FLAGS,
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(descriptor)
        if _source_file_stat(before) != expected:
            raise ValueError("expert_environment_inventory_invalid")
        content = (
            _pread_exact(descriptor, 0, before.st_size)
            if gate is None
            else _gated_pread_exact(
                descriptor,
                0,
                before.st_size,
                gate=gate,
            )
        )
        after = os.fstat(descriptor)
        named_after = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            len(content) != before.st_size
            or _source_file_stat(after) != expected
            or _source_file_stat(named_after) != expected
        ):
            raise ValueError("expert_environment_inventory_invalid")
    finally:
        _close_environment_descriptor(root, descriptor)
    _validate_source_descriptor_root(root)
    root.source_content_cache[logical] = (expected, content)
    return content


def _open_source_directory(
    root: _Root,
    parent_fd: int,
    basename: str,
    *,
    gate: Callable[[], None] | None = None,
) -> int:
    # Directory acquisition is metadata-only.  The enclosing logical-file
    # gate and the immediate pread gates govern all source bytes.
    _validate_source_descriptor_root(root)
    named = os.stat(
        basename,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    descriptor = os.open(
        basename,
        _OPEN_DIRECTORY_FLAGS,
        dir_fd=parent_fd,
    )
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid != os.getuid()
            or not _same_file_identity(observed, named)
        ):
            raise ValueError("expert_environment_inventory_invalid")
        return descriptor
    except BaseException:
        _close_environment_descriptor(root, descriptor)
        raise


def _read_source_file(
    root: _Root,
    logical: str,
    *,
    gate: Callable[[], None] | None = None,
) -> bytes:
    parts = logical.split("/")
    if (
        not parts
        or any(
            not part or part in {".", ".."} or "/" in part or "\x00" in part
            for part in parts
        )
    ):
        raise ValueError("expert_environment_inventory_invalid")
    _run_environment_io_gate(gate)
    directory_fd = os.dup(root.source_root_fd)
    try:
        for part in parts[:-1]:
            child = _open_source_directory(
                root,
                directory_fd,
                part,
                gate=gate,
            )
            previous_fd = directory_fd
            directory_fd = child
            _close_environment_descriptor(root, previous_fd)
        return _read_source_named_file(
            root,
            directory_fd,
            parts[-1],
            logical=logical,
            gate=gate,
        )
    finally:
        current_fd = directory_fd
        directory_fd = -1
        _close_environment_descriptor(root, current_fd)


def _inventory_digest_fd(
    root: _Root,
    inventory: tuple[str, ...],
    domain: bytes,
    *,
    gate: Callable[[], None] | None = None,
) -> str:
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    projection = tuple(
        (
            logical,
            sha256(
                _read_source_file(root, logical, gate=gate)
            ).hexdigest(),
        )
        for logical in inventory
    )
    digest = sha256(
        domain + canonical_expert_bytes(projection)
    ).hexdigest()
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    return digest


def issue_sportradar_candidate_source_seal_collection_authority(
    authority: ExpertJournalRootAuthorityV1,
) -> CandidateSourceSealCollectionAuthorityV1:
    with _LOCK:
        root = _require_root(authority)
        token = _token(CandidateSourceSealCollectionAuthorityV1)
        _CANDIDATE_SOURCE_SEALS[token] = _CandidateSourceSealAuthority(
            root=root,
            owner_pid=os.getpid(),
            owner_thread=threading.current_thread(),
            generation=root.generation,
        )
        return token


def _candidate_raw_sha256(root: _Root, logical: str) -> str:
    return sha256(_read_source_file(root, logical)).hexdigest()


def _candidate_inventory_sha256(
    root: _Root,
    *,
    domain: bytes,
    inventory: tuple[str, ...],
) -> str:
    projection = tuple(
        (logical, _candidate_raw_sha256(root, logical))
        for logical in inventory
    )
    return sha256(
        domain + canonical_expert_bytes(projection)
    ).hexdigest()


def _candidate_pin_projection(
    pin: ExpertNormalizerPinV1,
) -> tuple[tuple[str, object], ...]:
    return (
        ("normalizer_id", pin.normalizer_id),
        ("source_kind", pin.source_kind),
        ("source_id", pin.source_id),
        ("event_type", pin.event_type),
        ("event_version", pin.event_version),
        ("normalizer_code_sha256", pin.normalizer_code_sha256),
        ("normalizer_schema_sha256", pin.normalizer_schema_sha256),
    )


def collect_sportradar_candidate_source_seals(
    authority: CandidateSourceSealCollectionAuthorityV1,
) -> CandidateSourceSealsV1:
    if type(authority) is not CandidateSourceSealCollectionAuthorityV1:
        raise TypeError("authority")
    with _LOCK:
        state = _CANDIDATE_SOURCE_SEALS.pop(authority, None)
        if (
            state is None
            or state.consumed
            or state.owner_pid != os.getpid()
            or state.owner_thread is not threading.current_thread()
        ):
            raise ValueError("candidate_source_seal_collection_failed")
        state.consumed = True
        root = state.root
        try:
            if (
                _require_root(root.token) is not root
                or state.generation != root.generation
            ):
                raise ValueError
            registry_source = _read_source_file(
                root,
                "inci_tennis_adapters/registry.py",
            )
            parser_source = _read_source_file(
                root,
                "inci_tennis_adapters/sportradar_tennis_v3.py",
            )
            normalizer_code_sha256 = sha256(
                b"INCI-EXPERT-NORMALIZER-CODE-V1\0"
                + canonical_expert_bytes(
                    (
                        (
                            "inci_tennis_adapters/registry.py",
                            sha256(registry_source).hexdigest(),
                        ),
                        (
                            "inci_tennis_adapters/sportradar_tennis_v3.py",
                            sha256(parser_source).hexdigest(),
                        ),
                    )
                )
            ).hexdigest()
            route_specs = (
                (
                    "sportradar-tennis-summary-v3",
                    "sportradar_tennis_summary_v3",
                    _CANDIDATE_SCHEMA_INVENTORY[0],
                ),
                (
                    "sportradar-tennis-timeline-v3",
                    "sportradar_tennis_timeline_v3",
                    _CANDIDATE_SCHEMA_INVENTORY[1],
                ),
                (
                    "sportradar-tennis-transport-error-v1",
                    "sportradar_tennis_transport_error_v1",
                    _CANDIDATE_SCHEMA_INVENTORY[2],
                ),
            )
            pins = tuple(
                ExpertNormalizerPinV1(
                    normalizer_id=normalizer_id,
                    source_kind="provider",
                    source_id="sportradar",
                    event_type=event_type,
                    event_version=1,
                    normalizer_code_sha256=normalizer_code_sha256,
                    normalizer_schema_sha256=_candidate_raw_sha256(
                        root,
                        schema_logical,
                    ),
                )
                for normalizer_id, event_type, schema_logical in route_specs
            )
            candidate_adapter_inventory_sha256 = (
                _candidate_inventory_sha256(
                    root,
                    domain=(
                        b"INCI-SPORTRADAR-CANDIDATE-ADAPTER-INVENTORY-V1\0"
                    ),
                    inventory=(
                        *_CANDIDATE_ADAPTER_SOURCE_INVENTORY,
                        *_CANDIDATE_SCHEMA_INVENTORY,
                    ),
                )
            )
            candidate_io_bridge_inventory_sha256 = (
                _candidate_inventory_sha256(
                    root,
                    domain=(
                        b"INCI-SPORTRADAR-CANDIDATE-IO-BRIDGE-INVENTORY-V1\0"
                    ),
                    inventory=_CANDIDATE_IO_SOURCE_INVENTORY,
                )
            )
            provider_transport_source_sha256 = _candidate_raw_sha256(
                root,
                "inci_tennis_io/provider_readonly.py",
            )
            qualification_controller_source_sha256 = _candidate_raw_sha256(
                root,
                _CANDIDATE_RUNTIME_SOURCE,
            )
            qualification_tool_source_sha256 = _candidate_raw_sha256(
                root,
                _CANDIDATE_TOOL_SOURCE,
            )
            candidate_manifest_schema_sha256 = _candidate_raw_sha256(
                root,
                _CANDIDATE_SCHEMA_INVENTORY[3],
            )
            candidate_authorization_schema_sha256 = _candidate_raw_sha256(
                root,
                _CANDIDATE_SCHEMA_INVENTORY[4],
            )
            candidate_output_schema_sha256 = _candidate_raw_sha256(
                root,
                _CANDIDATE_SCHEMA_INVENTORY[5],
            )
            qualification_protocol_sha256 = sha256(
                b"INCI-SPORTRADAR-CANDIDATE-QUALIFICATION-PROTOCOL-V1\0"
                + canonical_expert_bytes(
                    (
                        (
                            "candidate_adapter_inventory_sha256",
                            candidate_adapter_inventory_sha256,
                        ),
                        (
                            "candidate_io_bridge_inventory_sha256",
                            candidate_io_bridge_inventory_sha256,
                        ),
                        (
                            "provider_transport_source_sha256",
                            provider_transport_source_sha256,
                        ),
                        (
                            "qualification_controller_source_sha256",
                            qualification_controller_source_sha256,
                        ),
                        (
                            "qualification_tool_source_sha256",
                            qualification_tool_source_sha256,
                        ),
                        (
                            "candidate_manifest_schema_sha256",
                            candidate_manifest_schema_sha256,
                        ),
                        (
                            "candidate_authorization_schema_sha256",
                            candidate_authorization_schema_sha256,
                        ),
                        (
                            "candidate_output_schema_sha256",
                            candidate_output_schema_sha256,
                        ),
                        ("duration_max_seconds", 3_600),
                        ("polling_interval_seconds", 10),
                        (
                            "transport_origin",
                            "https://api.sportradar.com",
                        ),
                        (
                            "output_protocol",
                            "candidate-qualification-output-v1",
                        ),
                    )
                )
            ).hexdigest()
            projection = (
                ("schema_version", 1),
                (
                    "normalizer_pins",
                    tuple(_candidate_pin_projection(pin) for pin in pins),
                ),
                (
                    "candidate_adapter_inventory_sha256",
                    candidate_adapter_inventory_sha256,
                ),
                (
                    "candidate_io_bridge_inventory_sha256",
                    candidate_io_bridge_inventory_sha256,
                ),
                (
                    "provider_transport_source_sha256",
                    provider_transport_source_sha256,
                ),
                (
                    "qualification_controller_source_sha256",
                    qualification_controller_source_sha256,
                ),
                (
                    "qualification_tool_source_sha256",
                    qualification_tool_source_sha256,
                ),
                (
                    "candidate_manifest_schema_sha256",
                    candidate_manifest_schema_sha256,
                ),
                (
                    "candidate_authorization_schema_sha256",
                    candidate_authorization_schema_sha256,
                ),
                (
                    "candidate_output_schema_sha256",
                    candidate_output_schema_sha256,
                ),
                (
                    "qualification_protocol_sha256",
                    qualification_protocol_sha256,
                ),
            )
            result = _create_candidate_source_seals_v1(
                schema_version=1,
                normalizer_pins=pins,
                candidate_adapter_inventory_sha256=(
                    candidate_adapter_inventory_sha256
                ),
                candidate_io_bridge_inventory_sha256=(
                    candidate_io_bridge_inventory_sha256
                ),
                provider_transport_source_sha256=(
                    provider_transport_source_sha256
                ),
                qualification_controller_source_sha256=(
                    qualification_controller_source_sha256
                ),
                qualification_tool_source_sha256=(
                    qualification_tool_source_sha256
                ),
                candidate_manifest_schema_sha256=(
                    candidate_manifest_schema_sha256
                ),
                candidate_authorization_schema_sha256=(
                    candidate_authorization_schema_sha256
                ),
                candidate_output_schema_sha256=(
                    candidate_output_schema_sha256
                ),
                qualification_protocol_sha256=(
                    qualification_protocol_sha256
                ),
                candidate_source_seals_sha256=sha256(
                    b"INCI-SPORTRADAR-CANDIDATE-SOURCE-SEALS-V1\0"
                    + canonical_expert_bytes(projection)
                ).hexdigest(),
            )
            if _require_root(root.token) is not root:
                raise ValueError
            return result
        except Exception:
            raise ValueError(
                "candidate_source_seal_collection_failed"
            ) from None


def _candidate_digest_text(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("candidate_output_contract_invalid")
    return value


def _candidate_safe_id(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 64
        or not value[0].isalnum()
        or any(
            not (
                character.isascii()
                and (character.isalnum() or character in "._-")
            )
            for character in value
        )
    ):
        raise ValueError("candidate_output_contract_invalid")
    return value


def _candidate_node_identity(
    value: os.stat_result,
    *,
    directory: bool,
) -> tuple[int, ...]:
    if (
        (
            not stat.S_ISDIR(value.st_mode)
            if directory
            else not stat.S_ISREG(value.st_mode)
        )
        or value.st_uid != os.getuid()
        or _mode(value) != (0o700 if directory else 0o600)
        or (value.st_nlink < 1 if directory else value.st_nlink != 1)
    ):
        raise ValueError("candidate_output_contract_invalid")
    common = (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        _mode(value),
    )
    return common if directory else (*common, value.st_nlink)


def _candidate_same_node(
    fd: int,
    expected: tuple[int, ...],
    *,
    directory: bool,
) -> None:
    if _candidate_node_identity(
        os.fstat(fd),
        directory=directory,
    ) != expected:
        raise ValueError("candidate_output_contract_invalid")


def _candidate_validate_output_ancestry(
    descriptor: int,
    *,
    forbidden_directory_fds: tuple[int, ...],
) -> None:
    forbidden = frozenset(
        (value.st_dev, value.st_ino)
        for value in (
            os.fstat(forbidden_fd)
            for forbidden_fd in forbidden_directory_fds
        )
    )
    current = os.dup(descriptor)
    seen: set[tuple[int, int]] = set()
    try:
        for _ in range(4_096):
            observed = os.fstat(current)
            if not stat.S_ISDIR(observed.st_mode):
                raise ValueError("candidate_output_contract_invalid")
            identity = (observed.st_dev, observed.st_ino)
            if identity in forbidden or identity in seen:
                raise ValueError("candidate_output_contract_invalid")
            seen.add(identity)
            try:
                os.stat(".git", dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("candidate_output_contract_invalid")
            parent = os.open("..", _OPEN_DIRECTORY_FLAGS, dir_fd=current)
            try:
                parent_observed = os.fstat(parent)
                if not stat.S_ISDIR(parent_observed.st_mode):
                    raise ValueError("candidate_output_contract_invalid")
                parent_identity = (
                    parent_observed.st_dev,
                    parent_observed.st_ino,
                )
                if parent_identity == identity:
                    return
            except BaseException:
                _close_quietly(parent)
                raise
            _close_quietly(current)
            current = parent
        raise ValueError("candidate_output_contract_invalid")
    finally:
        _close_quietly(current)


def _candidate_output_parent(
    root: _Root,
    output_parent: str,
) -> tuple[int, tuple[int, ...]]:
    if (
        type(output_parent) is not str
        or not output_parent
        or not os.path.isabs(output_parent)
        or os.path.normpath(output_parent) != output_parent
        or "\x00" in output_parent
    ):
        raise ValueError("candidate_output_contract_invalid")
    source_root = os.path.normpath(root.source_root_path)
    if os.path.commonpath((source_root, output_parent)) == source_root:
        raise ValueError("candidate_output_contract_invalid")
    candidate = Path(output_parent)
    for ancestor in (candidate, *candidate.parents):
        try:
            marker = os.lstat(ancestor / ".git")
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode):
            raise ValueError("candidate_output_contract_invalid")
    before = os.lstat(output_parent)
    if stat.S_ISLNK(before.st_mode):
        raise ValueError("candidate_output_contract_invalid")
    descriptor = os.open(output_parent, _OPEN_DIRECTORY_FLAGS)
    try:
        identity = _candidate_node_identity(
            os.fstat(descriptor),
            directory=True,
        )
        if not _same_file_identity(before, os.fstat(descriptor)):
            raise ValueError("candidate_output_contract_invalid")
        forbidden = tuple(
            os.fstat(fd)
            for fd in (
                root.state_fd,
                root.expert_fd,
                root.sessions_fd,
                root.markers_fd,
                root.source_root_fd,
            )
        )
        observed = os.fstat(descriptor)
        if any(_same_file_identity(observed, value) for value in forbidden):
            raise ValueError("candidate_output_contract_invalid")
        _candidate_validate_output_ancestry(
            descriptor,
            forbidden_directory_fds=(
                root.state_fd,
                root.source_root_fd,
            ),
        )
        return descriptor, identity
    except BaseException:
        _close_quietly(descriptor)
        raise


def _candidate_session_name_sha256(
    *,
    candidate_manifest_sha256: str,
    manifest_core_sha256: str,
    candidate_authorization_sha256: str,
    candidate_source_seals_sha256: str,
    quota_closure_sha256: str,
    match_binding_universe_sha256: str,
    requested_provider_match_ids: tuple[str, ...],
    session_start_wall_ns: int,
    session_end_wall_ns: int,
    retention_delete_by_ns: int,
) -> str:
    return sha256(
        b"INCI-SPORTRADAR-CANDIDATE-SESSION-NAME-V1\0"
        + canonical_expert_bytes(
            (
                (
                    "candidate_manifest_sha256",
                    candidate_manifest_sha256,
                ),
                ("manifest_core_sha256", manifest_core_sha256),
                (
                    "candidate_authorization_sha256",
                    candidate_authorization_sha256,
                ),
                (
                    "candidate_source_seals_sha256",
                    candidate_source_seals_sha256,
                ),
                ("quota_closure_sha256", quota_closure_sha256),
                (
                    "match_binding_universe_sha256",
                    match_binding_universe_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    requested_provider_match_ids,
                ),
                ("session_start_wall_ns", session_start_wall_ns),
                ("session_end_wall_ns", session_end_wall_ns),
                ("retention_delete_by_ns", retention_delete_by_ns),
            )
        )
    ).hexdigest()


def _candidate_preobservation_trace_sha256(
    *,
    session_id: str,
    candidate_manifest_sha256: str,
    manifest_core_sha256: str,
    candidate_authorization_sha256: str,
    candidate_source_seals_sha256: str,
    quota_closure_sha256: str,
    match_binding_universe_sha256: str,
    requested_provider_match_ids: tuple[str, ...],
    retention_delete_by_ns: int,
) -> str:
    return sha256(
        b"INCI-SPORTRADAR-CANDIDATE-PREOBSERVATION-TRACE-V1\0"
        + canonical_expert_bytes(
            (
                ("trace_state", "empty_pre_observation"),
                ("session_id", session_id),
                (
                    "candidate_manifest_sha256",
                    candidate_manifest_sha256,
                ),
                ("manifest_core_sha256", manifest_core_sha256),
                (
                    "candidate_authorization_sha256",
                    candidate_authorization_sha256,
                ),
                (
                    "candidate_source_seals_sha256",
                    candidate_source_seals_sha256,
                ),
                ("quota_closure_sha256", quota_closure_sha256),
                (
                    "match_binding_universe_sha256",
                    match_binding_universe_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    requested_provider_match_ids,
                ),
                ("retention_delete_by_ns", retention_delete_by_ns),
            )
        )
    ).hexdigest()


def create_sportradar_candidate_output_writer(
    root_authority: ExpertJournalRootAuthorityV1,
    *,
    output_parent: str,
    session_manifest: SessionManifest,
    session_manifest_sha256: str,
    candidate_manifest_sha256: str,
    manifest_core_sha256: str,
    candidate_authorization_sha256: str,
    candidate_decision_sha256: str,
    candidate_binding_sha256: str,
    quota_closure_sha256: str,
    candidate_source_seals_sha256: str,
    match_binding_universe_sha256: str,
    requested_provider_match_ids: tuple[str, ...],
    session_start_wall_ns: int,
    maximum_candidate_trace_bytes: int,
) -> CandidateQualificationOutputWriterV1:
    if type(session_manifest) is not SessionManifest:
        raise TypeError("session_manifest")
    if type(requested_provider_match_ids) is not tuple:
        raise TypeError("requested_provider_match_ids")
    if (
        type(session_start_wall_ns) is not int
        or type(maximum_candidate_trace_bytes) is not int
    ):
        raise TypeError("candidate_output_integer")
    with _LOCK:
        root = _require_root(root_authority)
        parent_fd = -1
        staging_fd = -1
        trace_fd = -1
        summary_fd = -1
        staging_basename = ""
        try:
            SessionManifest.__post_init__(session_manifest)
            for value in (
                session_manifest_sha256,
                candidate_manifest_sha256,
                manifest_core_sha256,
                candidate_authorization_sha256,
                candidate_decision_sha256,
                candidate_binding_sha256,
                quota_closure_sha256,
                candidate_source_seals_sha256,
                match_binding_universe_sha256,
            ):
                _candidate_digest_text(value)
            if (
                not 1 <= len(requested_provider_match_ids) <= 10
                or requested_provider_match_ids
                != tuple(sorted(requested_provider_match_ids))
                or len(set(requested_provider_match_ids))
                != len(requested_provider_match_ids)
            ):
                raise ValueError
            for provider_match_id in requested_provider_match_ids:
                _candidate_safe_id(provider_match_id)
            if (
                session_start_wall_ns < 0
                or session_manifest.created_wall_ns
                > session_start_wall_ns
                or session_start_wall_ns
                >= session_manifest.session_end_ns
                or maximum_candidate_trace_bytes < 4_096
                or maximum_candidate_trace_bytes
                > _CANDIDATE_TRACE_MAX_BYTES
                or session_manifest_sha256
                != _phase1_session_manifest_sha256(session_manifest)
                or session_manifest.config_file_sha256
                != candidate_manifest_sha256
                or session_manifest.config_canonical_sha256
                != manifest_core_sha256
                or session_manifest.code_sha256
                != candidate_source_seals_sha256
                or session_manifest.provider_id != "sportradar"
                or session_manifest.provider_manifest_file_sha256
                != candidate_manifest_sha256
                or session_manifest.provider_manifest_canonical_sha256
                != manifest_core_sha256
                or session_manifest.qualification_artifact_sha256
                != candidate_authorization_sha256
                or session_manifest.quota_contract_sha256
                != quota_closure_sha256
                or session_manifest.research_evaluable is not False
            ):
                raise ValueError
            session_name = _candidate_session_name_sha256(
                candidate_manifest_sha256=candidate_manifest_sha256,
                manifest_core_sha256=manifest_core_sha256,
                candidate_authorization_sha256=(
                    candidate_authorization_sha256
                ),
                candidate_source_seals_sha256=(
                    candidate_source_seals_sha256
                ),
                quota_closure_sha256=quota_closure_sha256,
                match_binding_universe_sha256=(
                    match_binding_universe_sha256
                ),
                requested_provider_match_ids=requested_provider_match_ids,
                session_start_wall_ns=session_start_wall_ns,
                session_end_wall_ns=session_manifest.session_end_ns,
                retention_delete_by_ns=(
                    session_manifest.required_retention_until_ns
                ),
            )
            expected_session_id = str(
                uuid.uuid5(
                    uuid.UUID("8f4c1777-5fea-521a-aaab-60afdc79e328"),
                    session_name,
                )
            )
            expected_trace = _candidate_preobservation_trace_sha256(
                session_id=expected_session_id,
                candidate_manifest_sha256=candidate_manifest_sha256,
                manifest_core_sha256=manifest_core_sha256,
                candidate_authorization_sha256=(
                    candidate_authorization_sha256
                ),
                candidate_source_seals_sha256=(
                    candidate_source_seals_sha256
                ),
                quota_closure_sha256=quota_closure_sha256,
                match_binding_universe_sha256=(
                    match_binding_universe_sha256
                ),
                requested_provider_match_ids=requested_provider_match_ids,
                retention_delete_by_ns=(
                    session_manifest.required_retention_until_ns
                ),
            )
            if (
                session_manifest.session_id != expected_session_id
                or session_manifest.qualification_trace_sha256
                != expected_trace
            ):
                raise ValueError
            parent_fd, parent_identity = _candidate_output_parent(
                root,
                output_parent,
            )
            final_basename = (
                "sportradar-candidate-qualification-"
                f"{session_manifest.session_id}"
            )
            staging_basename = f".{final_basename}.staging"
            try:
                os.stat(
                    final_basename,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise ValueError
            os.mkdir(staging_basename, 0o700, dir_fd=parent_fd)
            staging_fd = os.open(
                staging_basename,
                _OPEN_DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            )
            staging_identity = _candidate_node_identity(
                os.fstat(staging_fd),
                directory=True,
            )
            trace_fd = os.open(
                "qualification-captures-v1.jsonl",
                _OPEN_FILE_CREATE_FLAGS,
                0o600,
                dir_fd=staging_fd,
            )
            summary_fd = os.open(
                "qualification-output-v1.json",
                _OPEN_FILE_CREATE_FLAGS,
                0o600,
                dir_fd=staging_fd,
            )
            trace_identity = _candidate_node_identity(
                os.fstat(trace_fd),
                directory=False,
            )
            summary_identity = _candidate_node_identity(
                os.fstat(summary_fd),
                directory=False,
            )
            token = _token(CandidateQualificationOutputWriterV1)
            _CANDIDATE_OUTPUT_WRITERS[token] = _CandidateOutputWriter(
                token=token,
                root=root,
                parent_fd=parent_fd,
                staging_fd=staging_fd,
                trace_fd=trace_fd,
                summary_fd=summary_fd,
                staging_basename=staging_basename,
                final_basename=final_basename,
                session_manifest=session_manifest,
                session_manifest_sha256=session_manifest_sha256,
                candidate_manifest_sha256=candidate_manifest_sha256,
                manifest_core_sha256=manifest_core_sha256,
                candidate_authorization_sha256=(
                    candidate_authorization_sha256
                ),
                candidate_decision_sha256=candidate_decision_sha256,
                candidate_binding_sha256=candidate_binding_sha256,
                quota_closure_sha256=quota_closure_sha256,
                candidate_source_seals_sha256=(
                    candidate_source_seals_sha256
                ),
                match_binding_universe_sha256=(
                    match_binding_universe_sha256
                ),
                requested_provider_match_ids=requested_provider_match_ids,
                session_start_wall_ns=session_start_wall_ns,
                maximum_candidate_trace_bytes=maximum_candidate_trace_bytes,
                retention_delete_by_ns=(
                    session_manifest.required_retention_until_ns
                ),
                owner_pid=os.getpid(),
                owner_thread=threading.current_thread(),
                generation=root.generation,
                parent_identity=parent_identity,
                staging_identity=staging_identity,
                trace_identity=trace_identity,
                summary_identity=summary_identity,
                previous_record_sha256=expected_trace,
            )
            return token
        except Exception:
            if parent_fd >= 0 and staging_basename:
                try:
                    os.unlink(
                        "qualification-captures-v1.jsonl",
                        dir_fd=staging_fd,
                    )
                except Exception:
                    pass
                try:
                    os.unlink(
                        "qualification-output-v1.json",
                        dir_fd=staging_fd,
                    )
                except Exception:
                    pass
                try:
                    os.rmdir(staging_basename, dir_fd=parent_fd)
                except Exception:
                    pass
            for descriptor in (
                trace_fd,
                summary_fd,
                staging_fd,
                parent_fd,
            ):
                _close_quietly(descriptor)
            raise ValueError("candidate_output_contract_invalid") from None


def _require_candidate_writer(
    writer: CandidateQualificationOutputWriterV1,
) -> _CandidateOutputWriter:
    if type(writer) is not CandidateQualificationOutputWriterV1:
        raise TypeError("writer")
    state = _CANDIDATE_OUTPUT_WRITERS.get(writer)
    if (
        state is None
        or not state.active
        or state.owner_pid != os.getpid()
        or state.owner_thread is not threading.current_thread()
        or state.generation != state.root.generation
        or _require_root(state.root.token) is not state.root
    ):
        raise ValueError("candidate_output_contract_invalid")
    _candidate_same_node(
        state.parent_fd,
        state.parent_identity,
        directory=True,
    )
    _candidate_same_node(
        state.staging_fd,
        state.staging_identity,
        directory=True,
    )
    _candidate_same_node(
        state.trace_fd,
        state.trace_identity,
        directory=False,
    )
    _candidate_same_node(
        state.summary_fd,
        state.summary_identity,
        directory=False,
    )
    return state


def _require_candidate_prior(
    state: _CandidateOutputWriter,
    prior_receipt: CandidateQualificationAppendReceiptV1 | None,
    *,
    allow_none: bool,
) -> None:
    if state.current_receipt is None:
        if not allow_none or prior_receipt is not None:
            raise ValueError("candidate_output_contract_invalid")
        return
    if (
        type(prior_receipt) is not CandidateQualificationAppendReceiptV1
        or prior_receipt is not state.current_receipt
    ):
        raise ValueError("candidate_output_contract_invalid")


def _candidate_recorded_wall_ns(state: _CandidateOutputWriter) -> int:
    sampled = _phase1_sample_wall_ns(state.root.clock_capability)
    if (
        type(sampled) is not int
        or sampled < state.session_start_wall_ns
        or sampled >= state.retention_delete_by_ns
        or state.last_recorded_wall_ns is not None
        and sampled < state.last_recorded_wall_ns
    ):
        raise ValueError("candidate_output_contract_invalid")
    state.last_recorded_wall_ns = sampled
    return sampled


def _append_candidate_row(
    state: _CandidateOutputWriter,
    *,
    record_type: str,
    variant: dict[str, object],
) -> CandidateQualificationAppendReceiptV1:
    record_index = state.record_index + 1
    common: dict[str, object] = {
        "schema_version": 1,
        "record_type": record_type,
        "session_id": state.session_manifest.session_id,
        "record_index": record_index,
        "recorded_wall_ns": _candidate_recorded_wall_ns(state),
        "retention_delete_by_ns": state.retention_delete_by_ns,
        "previous_record_sha256": state.previous_record_sha256,
    }
    if set(common).intersection(variant):
        raise ValueError("candidate_output_contract_invalid")
    row = {**common, **variant}
    encoded = canonical_json_bytes(row) + b"\n"
    per_row_limit = {
        "permit": 4_096,
        "capture": 2 * 1_048_576 + 4_096,
        "parser_result": 8_192,
        "failure": 8_192,
        "terminal": 4_096,
    }.get(record_type)
    if (
        per_row_limit is None
        or len(encoded) > per_row_limit
        or len(state.trace_prefix_bytes) + len(encoded)
        > state.maximum_candidate_trace_bytes
    ):
        raise ValueError("candidate_output_contract_invalid")
    before_size = os.fstat(state.trace_fd).st_size
    if before_size != len(state.trace_prefix_bytes):
        raise ValueError("candidate_output_contract_invalid")
    _complete_write(state.trace_fd, encoded)
    os.fsync(state.trace_fd)
    _candidate_same_node(
        state.trace_fd,
        state.trace_identity,
        directory=False,
    )
    expected_size = before_size + len(encoded)
    if os.fstat(state.trace_fd).st_size != expected_size:
        raise ValueError("candidate_output_contract_invalid")
    state.trace_prefix_bytes.extend(encoded)
    record_sha256 = sha256(encoded).hexdigest()
    trace_prefix_sha256 = sha256(state.trace_prefix_bytes).hexdigest()
    receipt_projection = (
        ("schema_version", 1),
        ("session_id", state.session_manifest.session_id),
        ("record_index", record_index),
        ("record_type", record_type),
        ("record_sha256", record_sha256),
        ("trace_prefix_sha256", trace_prefix_sha256),
        ("durable_trace_length", expected_size),
        ("retention_delete_by_ns", state.retention_delete_by_ns),
        ("fsynced", True),
    )
    receipt = _create_candidate_qualification_append_receipt_v1(
        schema_version=1,
        session_id=state.session_manifest.session_id,
        record_index=record_index,
        record_type=record_type,
        record_sha256=record_sha256,
        trace_prefix_sha256=trace_prefix_sha256,
        durable_trace_length=expected_size,
        retention_delete_by_ns=state.retention_delete_by_ns,
        fsynced=True,
        receipt_sha256=sha256(
            b"INCI-SPORTRADAR-CANDIDATE-TRACE-RECEIPT-V1\0"
            + canonical_expert_bytes(receipt_projection)
        ).hexdigest(),
    )
    state.record_index = record_index
    state.previous_record_sha256 = record_sha256
    state.current_receipt = receipt
    return receipt


def append_sportradar_candidate_permit(
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1 | None,
    route: str,
    provider_match_id: str,
    resync: bool,
    connection_epoch: int,
    permit_sha256: str,
    quota_coordinates: tuple[tuple[str, int], ...],
) -> CandidateQualificationAppendReceiptV1:
    with _LOCK:
        state = _require_candidate_writer(writer)
        _require_candidate_prior(
            state,
            prior_receipt,
            allow_none=True,
        )
        if (
            state.failure_terminal_reason is not None
            or state.unmatched_permit is not None
            or state.unmatched_permit_route is not None
            or state.unmatched_permit_provider_match_id is not None
            or state.unmatched_capture is not None
            or state.unmatched_capture_event_type is not None
            or state.unmatched_capture_payload_sha256 is not None
            or state.unmatched_capture_envelope_sha256 is not None
            or route not in {"summary", "timeline"}
            or type(resync) is not bool
            or type(connection_epoch) is not int
            or connection_epoch != 1
            or route == "timeline"
            and resync
            or provider_match_id
            not in state.requested_provider_match_ids
        ):
            raise ValueError("candidate_output_contract_invalid")
        _candidate_safe_id(provider_match_id)
        _candidate_digest_text(permit_sha256)
        expected_names = (
            "rolling_second_attempts",
            "rolling_60_seconds_attempts",
            "utc_day_attempts",
            "active_connections",
            "active_subscriptions",
            "rolling_hour_resync_attempts",
        )
        if (
            type(quota_coordinates) is not tuple
            or tuple(name for name, _ in quota_coordinates)
            != expected_names
        ):
            raise ValueError("candidate_output_contract_invalid")
        quota_object: dict[str, int] = {}
        for name, value in quota_coordinates:
            if type(name) is not str or type(value) is not int or value < 0:
                raise ValueError("candidate_output_contract_invalid")
            quota_object[name] = value
        if quota_object["active_subscriptions"] != len(
            state.requested_provider_match_ids
        ):
            raise ValueError("candidate_output_contract_invalid")
        previous = state.previous_record_sha256
        expected_permit = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-READ-PERMIT-V1\0"
            + canonical_expert_bytes(
                (
                    ("session_id", state.session_manifest.session_id),
                    ("route", route),
                    ("provider_match_id", provider_match_id),
                    ("resync", resync),
                    ("connection_epoch", connection_epoch),
                    ("quota_coordinates", quota_coordinates),
                    ("previous_record_sha256", previous),
                )
            )
        ).hexdigest()
        if permit_sha256 != expected_permit:
            raise ValueError("candidate_output_contract_invalid")
        receipt = _append_candidate_row(
            state,
            record_type="permit",
            variant={
                "route": route,
                "provider_match_id": provider_match_id,
                "resync": resync,
                "connection_epoch": connection_epoch,
                "permit_sha256": permit_sha256,
                "quota_coordinates": quota_object,
            },
        )
        state.permit_count += 1
        state.unmatched_permit = receipt
        state.unmatched_permit_route = route
        state.unmatched_permit_provider_match_id = provider_match_id
        return receipt


def _candidate_capture_projection(
    captured: CapturedInput,
) -> tuple[tuple[str, object], ...]:
    return (
        ("session_id", captured.session_id),
        ("event_type", captured.event_type),
        ("event_version", captured.event_version),
        ("source_kind", captured.source_kind.value),
        ("source_id", captured.source_id),
        ("source_entity_id", captured.source_entity_id),
        ("endpoint_id", captured.endpoint_id),
        ("endpoint_state", captured.endpoint_state.value),
        ("channel_id", captured.channel_id),
        ("channel_state", captured.channel_state.value),
        ("request_id", captured.request_id),
        ("request_id_state", captured.request_id_state.value),
        ("source_wall_ns", captured.source_wall_ns),
        ("source_generated_ns", captured.source_generated_ns),
        ("local_wall_ns", captured.local_wall_ns),
        ("local_monotonic_ns", captured.local_monotonic_ns),
        ("clock_uncertainty_ns", captured.clock_uncertainty_ns),
        ("connection_epoch", captured.connection_epoch),
        ("provider_sequence", captured.provider_sequence),
        ("content_type", captured.content_type),
        ("payload_encoding", captured.payload_encoding),
        ("payload_transform", captured.payload_transform),
        ("retention_delete_by_ns", captured.retention_delete_by_ns),
        ("payload_sha256", sha256(captured.payload).hexdigest()),
    )


def append_sportradar_candidate_capture(
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1,
    captured: CapturedInput,
) -> CandidateQualificationAppendReceiptV1:
    if type(captured) is not CapturedInput:
        raise TypeError("captured")
    with _LOCK:
        state = _require_candidate_writer(writer)
        _require_candidate_prior(state, prior_receipt, allow_none=False)
        if (
            state.failure_terminal_reason is not None
            or state.unmatched_permit is not prior_receipt
            or state.unmatched_permit_route
            not in _CANDIDATE_CAPTURE_EVENT_TYPES_BY_ROUTE
            or state.unmatched_permit_provider_match_id is None
            or state.unmatched_capture is not None
            or state.unmatched_capture_event_type is not None
            or state.unmatched_capture_payload_sha256 is not None
            or state.unmatched_capture_envelope_sha256 is not None
            or captured.session_id != state.session_manifest.session_id
            or captured.event_version != 1
            or captured.event_type
            not in _CANDIDATE_CAPTURE_EVENT_TYPES_BY_ROUTE[
                state.unmatched_permit_route
            ]
            or captured.source_kind.value != "provider"
            or captured.source_id != "sportradar"
            or captured.source_entity_id
            != state.unmatched_permit_provider_match_id
            or captured.endpoint_id != "sportradar-api"
            or captured.endpoint_state.value != "safe_original"
            or captured.channel_id != "sportradar-rest"
            or captured.channel_state.value != "safe_original"
            or captured.request_id != "<redacted>"
            or captured.request_id_state.value != "redacted"
            or captured.connection_epoch != 1
            or captured.retention_delete_by_ns
            != state.retention_delete_by_ns
            or len(captured.payload) > 1_048_576
        ):
            raise ValueError("candidate_output_contract_invalid")
        payload_sha256 = sha256(captured.payload).hexdigest()
        envelope_sha256 = sha256(
            b"INCI-SPORTRADAR-CANDIDATE-CAPTURE-ENVELOPE-V1\0"
            + canonical_expert_bytes(_candidate_capture_projection(captured))
        ).hexdigest()
        receipt = _append_candidate_row(
            state,
            record_type="capture",
            variant={
                "permit_record_sha256": prior_receipt.record_sha256,
                "event_type": captured.event_type,
                "event_version": captured.event_version,
                "source_kind": captured.source_kind.value,
                "source_id": captured.source_id,
                "source_entity_id": captured.source_entity_id,
                "endpoint_id": captured.endpoint_id,
                "endpoint_state": captured.endpoint_state.value,
                "channel_id": captured.channel_id,
                "channel_state": captured.channel_state.value,
                "request_id": captured.request_id,
                "request_id_state": captured.request_id_state.value,
                "source_wall_ns": captured.source_wall_ns,
                "source_generated_ns": captured.source_generated_ns,
                "local_wall_ns": captured.local_wall_ns,
                "local_monotonic_ns": captured.local_monotonic_ns,
                "clock_uncertainty_ns": captured.clock_uncertainty_ns,
                "connection_epoch": captured.connection_epoch,
                "provider_sequence": captured.provider_sequence,
                "content_type": captured.content_type,
                "payload_encoding": captured.payload_encoding,
                "payload_transform": captured.payload_transform,
                "payload_hex": captured.payload.hex(),
                "payload_sha256": payload_sha256,
                "capture_envelope_sha256": envelope_sha256,
            },
        )
        state.capture_count += 1
        state.unmatched_permit = None
        state.unmatched_permit_route = None
        state.unmatched_permit_provider_match_id = None
        state.unmatched_capture = receipt
        state.unmatched_capture_event_type = captured.event_type
        state.unmatched_capture_payload_sha256 = payload_sha256
        state.unmatched_capture_envelope_sha256 = envelope_sha256
        return receipt


def append_sportradar_candidate_parser_result(
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1,
    capture_receipt: CandidateQualificationAppendReceiptV1,
    evidence_sha256: str,
    parser_outcome: str,
    reason: str | None,
    output_contract_sha256s: tuple[str, ...],
    capabilities: tuple[tuple[str, bool], ...],
    first_correction_epoch: int | None,
    first_revision: int | None,
    last_correction_epoch: int | None,
    last_revision: int | None,
) -> CandidateQualificationAppendReceiptV1:
    with _LOCK:
        state = _require_candidate_writer(writer)
        _require_candidate_prior(state, prior_receipt, allow_none=False)
        if (
            state.failure_terminal_reason is not None
            or capture_receipt is not prior_receipt
            or state.unmatched_capture is not capture_receipt
            or state.unmatched_capture_event_type
            not in {
                "sportradar_tennis_summary_v3",
                "sportradar_tennis_timeline_v3",
                "sportradar_tennis_transport_error_v1",
            }
            or state.unmatched_capture_payload_sha256 is None
            or state.unmatched_capture_envelope_sha256 is None
            or parser_outcome not in {"accepted", "ignored", "rejected"}
            or type(output_contract_sha256s) is not tuple
            or type(capabilities) is not tuple
        ):
            raise ValueError("candidate_output_contract_invalid")
        _candidate_digest_text(evidence_sha256)
        _candidate_digest_text(state.unmatched_capture_payload_sha256)
        _candidate_digest_text(state.unmatched_capture_envelope_sha256)
        capture_event_type = state.unmatched_capture_event_type
        coordinates = (
            first_correction_epoch,
            first_revision,
            last_correction_epoch,
            last_revision,
        )
        if parser_outcome == "accepted":
            if (
                reason is not None
                or capture_event_type
                == "sportradar_tennis_transport_error_v1"
                or not 1 <= len(output_contract_sha256s) <= 64
                or capture_event_type == "sportradar_tennis_summary_v3"
                and len(output_contract_sha256s) != 1
                or any(
                    type(value) is not int
                    or not 0 <= value <= _CANDIDATE_MAX_SIGNED_64
                    for value in coordinates
                )
            ):
                raise ValueError("candidate_output_contract_invalid")
            assert all(type(value) is int for value in coordinates)
            if (
                (last_correction_epoch, last_revision)
                < (first_correction_epoch, first_revision)
                or capture_event_type
                == "sportradar_tennis_timeline_v3"
                and (first_revision < 1 or last_revision < 1)
            ):
                raise ValueError("candidate_output_contract_invalid")
        elif parser_outcome == "ignored":
            if (
                capture_event_type
                != "sportradar_tennis_transport_error_v1"
                or reason != "event_not_relevant"
                or output_contract_sha256s
                or any(value is not None for value in coordinates)
            ):
                raise ValueError("candidate_output_contract_invalid")
        elif (
            reason not in _CANDIDATE_PARSER_REJECT_REASONS
            or output_contract_sha256s
            or any(value is not None for value in coordinates)
        ):
            raise ValueError("candidate_output_contract_invalid")
        for digest in output_contract_sha256s:
            _candidate_digest_text(digest)
        expected_capabilities = (
            "correction_semantics",
            "current_server",
            "match_format",
            "monotonic_sequence_or_revision",
            "point_state",
            "provider_generated_time",
            "resync_snapshot",
            "source_event_time",
            "stable_match_ids",
            "stable_player_ids",
        )
        if tuple(name for name, _ in capabilities) != expected_capabilities:
            raise ValueError("candidate_output_contract_invalid")
        for name, enabled in capabilities:
            _candidate_safe_id(name)
            if type(enabled) is not bool:
                raise ValueError("candidate_output_contract_invalid")
        expected_evidence_sha256 = sha256(
            _CANDIDATE_PARSER_EVIDENCE_DOMAIN
            + canonical_expert_bytes(
                (
                    ("schema_version", 1),
                    ("event_type", capture_event_type),
                    ("event_version", 1),
                    (
                        "payload_sha256",
                        state.unmatched_capture_payload_sha256,
                    ),
                    (
                        "capture_envelope_sha256",
                        state.unmatched_capture_envelope_sha256,
                    ),
                    ("parser_outcome", parser_outcome),
                    ("reason", reason),
                    (
                        "output_contract_sha256s",
                        output_contract_sha256s,
                    ),
                    ("capabilities", capabilities),
                    (
                        "first_correction_epoch",
                        first_correction_epoch,
                    ),
                    ("first_revision", first_revision),
                    (
                        "last_correction_epoch",
                        last_correction_epoch,
                    ),
                    ("last_revision", last_revision),
                )
            )
        ).hexdigest()
        if evidence_sha256 != expected_evidence_sha256:
            raise ValueError("candidate_output_contract_invalid")
        receipt = _append_candidate_row(
            state,
            record_type="parser_result",
            variant={
                "capture_record_sha256": capture_receipt.record_sha256,
                "evidence_sha256": evidence_sha256,
                "parser_outcome": parser_outcome,
                "reason": reason,
                "output_contract_sha256s": list(output_contract_sha256s),
                "capabilities": [
                    [name, enabled] for name, enabled in capabilities
                ],
                "first_correction_epoch": first_correction_epoch,
                "first_revision": first_revision,
                "last_correction_epoch": last_correction_epoch,
                "last_revision": last_revision,
            },
        )
        if parser_outcome == "accepted":
            state.accepted_count += 1
        elif parser_outcome == "ignored":
            state.ignored_count += 1
        else:
            state.rejected_count += 1
        state.unmatched_capture = None
        state.unmatched_capture_event_type = None
        state.unmatched_capture_payload_sha256 = None
        state.unmatched_capture_envelope_sha256 = None
        return receipt


def append_sportradar_candidate_failure(
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1 | None,
    stage: str,
    failure_code: str,
    permit_receipt: CandidateQualificationAppendReceiptV1 | None,
    capture_receipt: CandidateQualificationAppendReceiptV1 | None,
) -> CandidateQualificationAppendReceiptV1:
    with _LOCK:
        state = _require_candidate_writer(writer)
        _require_candidate_prior(state, prior_receipt, allow_none=True)
        if (
            state.failure_terminal_reason is not None
            or (stage, failure_code) not in _CANDIDATE_FAILURE_PAIRS
            or permit_receipt is not None
            and type(permit_receipt)
            is not CandidateQualificationAppendReceiptV1
            or capture_receipt is not None
            and type(capture_receipt)
            is not CandidateQualificationAppendReceiptV1
        ):
            raise ValueError("candidate_output_contract_invalid")
        if state.unmatched_capture is not None:
            if (
                state.unmatched_capture_event_type is None
                or state.unmatched_capture_payload_sha256 is None
                or state.unmatched_capture_envelope_sha256 is None
                or state.unmatched_permit is not None
                or state.unmatched_permit_route is not None
                or state.unmatched_permit_provider_match_id is not None
                or stage not in {"parser", "output"}
                or capture_receipt is not state.unmatched_capture
                or prior_receipt is not state.unmatched_capture
                or permit_receipt is not None
            ):
                raise ValueError("candidate_output_contract_invalid")
        elif state.unmatched_permit is not None:
            if (
                state.unmatched_permit_route is None
                or state.unmatched_permit_provider_match_id is None
                or state.unmatched_capture_event_type is not None
                or state.unmatched_capture_payload_sha256 is not None
                or state.unmatched_capture_envelope_sha256 is not None
                or stage not in {"transport", "capture"}
                or permit_receipt is not state.unmatched_permit
                or prior_receipt is not state.unmatched_permit
                or capture_receipt is not None
            ):
                raise ValueError("candidate_output_contract_invalid")
        elif (
            state.unmatched_permit_route is not None
            or state.unmatched_permit_provider_match_id is not None
            or state.unmatched_capture_event_type is not None
            or state.unmatched_capture_payload_sha256 is not None
            or state.unmatched_capture_envelope_sha256 is not None
            or stage != "permit"
            or permit_receipt is not None
            or capture_receipt is not None
        ):
            raise ValueError("candidate_output_contract_invalid")
        receipt = _append_candidate_row(
            state,
            record_type="failure",
            variant={
                "stage": stage,
                "failure_code": failure_code,
                "permit_record_sha256": (
                    None
                    if permit_receipt is None
                    else permit_receipt.record_sha256
                ),
                "capture_record_sha256": (
                    None
                    if capture_receipt is None
                    else capture_receipt.record_sha256
                ),
            },
        )
        state.failure_count += 1
        state.unmatched_permit = None
        state.unmatched_permit_route = None
        state.unmatched_permit_provider_match_id = None
        state.unmatched_capture = None
        state.unmatched_capture_event_type = None
        state.unmatched_capture_payload_sha256 = None
        state.unmatched_capture_envelope_sha256 = None
        state.failure_terminal_reason = (
            _CANDIDATE_FAILURE_TERMINAL_REASONS[failure_code]
        )
        return receipt


def _abort_candidate_writer_state(state: _CandidateOutputWriter) -> None:
    state.active = False
    try:
        os.unlink(
            "qualification-captures-v1.jsonl",
            dir_fd=state.staging_fd,
        )
    except Exception:
        pass
    try:
        os.unlink(
            "qualification-output-v1.json",
            dir_fd=state.staging_fd,
        )
    except Exception:
        pass
    directory_basename = (
        state.final_basename if state.renamed else state.staging_basename
    )
    try:
        os.rmdir(directory_basename, dir_fd=state.parent_fd)
        os.fsync(state.parent_fd)
    except Exception:
        pass
    for descriptor in (
        state.trace_fd,
        state.summary_fd,
        state.staging_fd,
        state.parent_fd,
    ):
        _close_quietly(descriptor)
    state.trace_fd = -1
    state.summary_fd = -1
    state.staging_fd = -1
    state.parent_fd = -1


def finalize_sportradar_candidate_output(
    writer: CandidateQualificationOutputWriterV1,
    *,
    prior_receipt: CandidateQualificationAppendReceiptV1,
    terminal_reason: str,
) -> CandidateQualificationCommitReceiptV1:
    with _LOCK:
        state = _require_candidate_writer(writer)
        try:
            _require_candidate_prior(state, prior_receipt, allow_none=False)
            if (
                terminal_reason not in _CANDIDATE_TERMINAL_REASONS
                or state.unmatched_permit is not None
                or state.unmatched_permit_route is not None
                or state.unmatched_permit_provider_match_id is not None
                or state.unmatched_capture is not None
                or state.unmatched_capture_event_type is not None
                or state.unmatched_capture_payload_sha256 is not None
                or state.unmatched_capture_envelope_sha256 is not None
                or state.failure_terminal_reason is None
                and state.failure_count != 0
                or state.failure_terminal_reason is not None
                and (
                    state.failure_count != 1
                    or terminal_reason != state.failure_terminal_reason
                )
                or terminal_reason == "completed"
                and (
                    state.failure_count != 0
                    or not (
                        state.permit_count
                        == state.capture_count
                        == (
                            state.accepted_count
                            + state.ignored_count
                            + state.rejected_count
                        )
                    )
                )
            ):
                raise ValueError("candidate_output_contract_invalid")
            terminal_receipt = _append_candidate_row(
                state,
                record_type="terminal",
                variant={
                    "terminal_reason": terminal_reason,
                    "permit_count": state.permit_count,
                    "capture_count": state.capture_count,
                    "accepted_count": state.accepted_count,
                    "ignored_count": state.ignored_count,
                    "rejected_count": state.rejected_count,
                    "failure_count": state.failure_count,
                },
            )
            trace_record_count = state.record_index
            parser_result_count = (
                state.accepted_count
                + state.ignored_count
                + state.rejected_count
            )
            if (
                trace_record_count
                != (
                    state.permit_count
                    + state.capture_count
                    + parser_result_count
                    + state.failure_count
                    + 1
                )
                or state.capture_count > state.permit_count
                or parser_result_count > state.capture_count
            ):
                raise ValueError("candidate_output_contract_invalid")
            trace_bytes = bytes(state.trace_prefix_bytes)
            trace_sha256 = sha256(trace_bytes).hexdigest()
            completed_wall_ns = state.last_recorded_wall_ns
            if type(completed_wall_ns) is not int:
                raise ValueError("candidate_output_contract_invalid")
            summary = {
                "schema_version": 1,
                "record_type": "qualification_summary",
                "session_id": state.session_manifest.session_id,
                "session_manifest_sha256": (
                    state.session_manifest_sha256
                ),
                "production_preflight_status": (
                    "not_run_candidate_only"
                ),
                "candidate_manifest_sha256": (
                    state.candidate_manifest_sha256
                ),
                "manifest_core_sha256": state.manifest_core_sha256,
                "candidate_authorization_sha256": (
                    state.candidate_authorization_sha256
                ),
                "candidate_decision_sha256": (
                    state.candidate_decision_sha256
                ),
                "candidate_binding_sha256": (
                    state.candidate_binding_sha256
                ),
                "quota_closure_sha256": state.quota_closure_sha256,
                "candidate_source_seals_sha256": (
                    state.candidate_source_seals_sha256
                ),
                "match_binding_universe_sha256": (
                    state.match_binding_universe_sha256
                ),
                "requested_provider_match_ids": list(
                    state.requested_provider_match_ids
                ),
                "session_start_wall_ns": state.session_start_wall_ns,
                "session_end_wall_ns": (
                    state.session_manifest.session_end_ns
                ),
                "retention_delete_by_ns": state.retention_delete_by_ns,
                "terminal_reason": terminal_reason,
                "permit_count": state.permit_count,
                "capture_count": state.capture_count,
                "accepted_count": state.accepted_count,
                "ignored_count": state.ignored_count,
                "rejected_count": state.rejected_count,
                "failure_count": state.failure_count,
                "trace_record_count": trace_record_count,
                "trace_sha256": trace_sha256,
                "last_trace_receipt_sha256": (
                    terminal_receipt.receipt_sha256
                ),
                "completed_wall_ns": completed_wall_ns,
            }
            summary_bytes = canonical_json_bytes(summary)
            if len(summary_bytes) > 16_384:
                raise ValueError("candidate_output_contract_invalid")
            if os.fstat(state.summary_fd).st_size != 0:
                raise ValueError("candidate_output_contract_invalid")
            _complete_write(state.summary_fd, summary_bytes)
            os.fsync(state.summary_fd)
            os.fsync(state.trace_fd)
            _candidate_same_node(
                state.summary_fd,
                state.summary_identity,
                directory=False,
            )
            _candidate_same_node(
                state.trace_fd,
                state.trace_identity,
                directory=False,
            )
            if (
                os.fstat(state.summary_fd).st_size != len(summary_bytes)
                or os.fstat(state.trace_fd).st_size != len(trace_bytes)
            ):
                raise ValueError("candidate_output_contract_invalid")
            os.fsync(state.staging_fd)
            _candidate_same_node(
                state.staging_fd,
                state.staging_identity,
                directory=True,
            )
            os.rename(
                state.staging_basename,
                state.final_basename,
                src_dir_fd=state.parent_fd,
                dst_dir_fd=state.parent_fd,
            )
            state.renamed = True
            named = os.stat(
                state.final_basename,
                dir_fd=state.parent_fd,
                follow_symlinks=False,
            )
            if not _same_file_identity(named, os.fstat(state.staging_fd)):
                raise ValueError("candidate_output_contract_invalid")
            os.fsync(state.parent_fd)
            _candidate_same_node(
                state.parent_fd,
                state.parent_identity,
                directory=True,
            )
            projection = (
                ("schema_version", 1),
                ("session_id", state.session_manifest.session_id),
                ("final_basename", state.final_basename),
                ("summary_sha256", sha256(summary_bytes).hexdigest()),
                ("summary_length", len(summary_bytes)),
                ("trace_sha256", trace_sha256),
                ("trace_length", len(trace_bytes)),
                ("trace_record_count", trace_record_count),
                (
                    "terminal_record_sha256",
                    terminal_receipt.record_sha256,
                ),
                ("terminal_reason", terminal_reason),
                (
                    "retention_delete_by_ns",
                    state.retention_delete_by_ns,
                ),
                ("files_fsynced", True),
                ("staging_directory_fsynced", True),
                ("parent_fsynced", True),
            )
            receipt = _create_candidate_qualification_commit_receipt_v1(
                schema_version=1,
                session_id=state.session_manifest.session_id,
                final_basename=state.final_basename,
                summary_sha256=sha256(summary_bytes).hexdigest(),
                summary_length=len(summary_bytes),
                trace_sha256=trace_sha256,
                trace_length=len(trace_bytes),
                trace_record_count=trace_record_count,
                terminal_record_sha256=terminal_receipt.record_sha256,
                terminal_reason=terminal_reason,
                retention_delete_by_ns=state.retention_delete_by_ns,
                files_fsynced=True,
                staging_directory_fsynced=True,
                parent_fsynced=True,
                receipt_sha256=sha256(
                    b"INCI-SPORTRADAR-CANDIDATE-OUTPUT-COMMIT-RECEIPT-V1\0"
                    + canonical_expert_bytes(projection)
                ).hexdigest(),
            )
            state.active = False
            _CANDIDATE_OUTPUT_WRITERS.pop(writer, None)
            for descriptor in (
                state.trace_fd,
                state.summary_fd,
                state.staging_fd,
                state.parent_fd,
            ):
                _close_quietly(descriptor)
            state.trace_fd = -1
            state.summary_fd = -1
            state.staging_fd = -1
            state.parent_fd = -1
            return receipt
        except Exception:
            _CANDIDATE_OUTPUT_WRITERS.pop(writer, None)
            _abort_candidate_writer_state(state)
            raise ValueError("candidate_output_contract_invalid") from None


def abort_sportradar_candidate_output(
    writer: CandidateQualificationOutputWriterV1,
) -> None:
    if type(writer) is not CandidateQualificationOutputWriterV1:
        raise TypeError("writer")
    with _LOCK:
        state = _CANDIDATE_OUTPUT_WRITERS.pop(writer, None)
        if state is None:
            raise ValueError("candidate_output_contract_invalid")
        _abort_candidate_writer_state(state)


def _exact_source_package_entries(
    root: _Root,
    *,
    directory_fd: int,
    expected: frozenset[str],
    relative_directory: str,
) -> frozenset[str]:
    _validate_source_descriptor_root(root)
    directory_before = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(directory_before.st_mode)
        or directory_before.st_uid != os.getuid()
    ):
        raise ValueError("expert_environment_inventory_invalid")
    observed: set[str] = set()
    for name in sorted(os.listdir(directory_fd)):
        if (
            type(name) is not str
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\x00" in name
        ):
            raise ValueError("expert_environment_inventory_invalid")
        relative = (
            name
            if not relative_directory
            else f"{relative_directory}/{name}"
        )
        value = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        expected_file = relative in expected
        expected_directory = any(
            logical.startswith(relative + "/")
            for logical in expected
        )
        if stat.S_ISDIR(value.st_mode):
            if not expected_directory or expected_file:
                raise ValueError("expert_environment_inventory_invalid")
            child = _open_source_directory(
                root,
                directory_fd,
                name,
            )
            try:
                observed.update(
                    _exact_source_package_entries(
                        root,
                        directory_fd=child,
                        expected=expected,
                        relative_directory=relative,
                    )
                )
            finally:
                _close_environment_descriptor(root, child)
            continue
        if (
            not stat.S_ISREG(value.st_mode)
            or not expected_file
            or expected_directory
        ):
            raise ValueError("expert_environment_inventory_invalid")
        _source_file_stat(value)
        observed.add(relative)
    directory_after = os.fstat(directory_fd)
    if not _same_file_identity(directory_before, directory_after):
        raise ValueError("expert_environment_inventory_invalid")
    _validate_source_descriptor_root(root)
    return frozenset(observed)


def _validate_exact_source_inventory(root: _Root) -> None:
    for package in root.source_packages:
        prefix = package.name + "/"
        root_relative_inventory = _SOURCE_PACKAGE_INVENTORIES.get(
            package.name
        )
        if (
            type(root_relative_inventory) is not tuple
            or not root_relative_inventory
            or any(
                type(logical) is not str
                or not logical.startswith(prefix)
                or logical == prefix
                for logical in root_relative_inventory
            )
        ):
            raise ValueError("expert_environment_inventory_invalid")
        expected = frozenset(
            logical.removeprefix(prefix)
            for logical in root_relative_inventory
        )
        if len(expected) != len(root_relative_inventory):
            raise ValueError("expert_environment_inventory_invalid")
        observed = _exact_source_package_entries(
            root,
            directory_fd=package.directory_fd,
            expected=expected,
            relative_directory="",
        )
        if observed != expected:
            raise ValueError("expert_environment_inventory_invalid")


def _source_package_entries(
    root: _Root,
    *,
    directory_fd: int,
    relative_directory: str,
    gate: Callable[[], None] | None = None,
) -> list[tuple[str, bytes]]:
    # Directory enumeration is metadata-only.  Individual source bytes
    # remain protected by _gated_pread_exact.
    _validate_source_descriptor_root(root)
    directory_before = os.fstat(directory_fd)
    entries: list[tuple[str, bytes]] = []
    names = sorted(os.listdir(directory_fd))
    for name in names:
        value = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        relative = (
            name
            if not relative_directory
            else f"{relative_directory}/{name}"
        )
        if stat.S_ISDIR(value.st_mode):
            if name == "__pycache__":
                raise ValueError("expert_environment_inventory_invalid")
            child = _open_source_directory(
                root,
                directory_fd,
                name,
                gate=gate,
            )
            try:
                entries.extend(
                    _source_package_entries(
                        root,
                        directory_fd=child,
                        relative_directory=relative,
                        gate=gate,
                    )
                )
            finally:
                _close_environment_descriptor(root, child)
            continue
        if name.endswith((".pyc", ".pyo")):
            raise ValueError("expert_environment_inventory_invalid")
        allowed = name.endswith(".py") or (
            name.endswith(".json")
            and "schemas" in relative.split("/")[:-1]
        )
        if not allowed:
            raise ValueError("expert_environment_inventory_invalid")
        entries.append(
            (
                relative,
                _read_source_named_file(
                    root,
                    directory_fd,
                    name,
                    logical=f"tennis_v1/{relative}",
                    gate=gate,
                ),
            )
        )
    directory_after = os.fstat(directory_fd)
    if not _same_file_identity(directory_before, directory_after):
        raise ValueError("expert_environment_inventory_invalid")
    _validate_source_descriptor_root(root)
    return entries


def _phase1_code_sha256_fd(
    root: _Root,
    *,
    gate: Callable[[], None] | None = None,
) -> str:
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    package_fd = _open_source_directory(
        root,
        root.source_root_fd,
        "tennis_v1",
        gate=gate,
    )
    try:
        entries = _source_package_entries(
            root,
            directory_fd=package_fd,
            relative_directory="",
            gate=gate,
        )
    finally:
        _close_environment_descriptor(root, package_fd)
    digest = sha256(CODE_FINGERPRINT_DOMAIN)
    for relative, content in sorted(entries):
        relative_bytes = relative.encode("utf-8")
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    result = digest.hexdigest()
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    return result


def _schema_objects(
    source_root: Path,
) -> tuple[
    ExpertStructuralSchemaBundleV1,
    ExpertEventSchemaBundleV1,
    ExpertNormalizerRegistryV1,
]:
    schema_root = source_root / "inci_tennis_expert" / "schemas"
    structural_pins = tuple(
        ExpertSchemaPinV1(
            role,
            contract,
            resource,
            sha256((schema_root / resource).read_bytes()).hexdigest(),
        )
        for role, contract, resource in _STRUCTURAL_SPEC
    )
    structural_values = {"schema_version": 1, "pins": structural_pins}
    structural = ExpertStructuralSchemaBundleV1(
        **structural_values,
        bundle_sha256=expert_structural_schema_bundle_sha256(
            **structural_values
        ),
    )
    event_pins = tuple(
        ExpertEventSchemaPinV1(
            kind,
            1,
            contract,
            resource,
            sha256((schema_root / resource).read_bytes()).hexdigest(),
        )
        for kind, contract, resource in _EVENT_SPEC
    )
    event_values = {"schema_version": 1, "pins": event_pins}
    event = ExpertEventSchemaBundleV1(
        **event_values,
        bundle_sha256=expert_event_schema_bundle_sha256(**event_values),
    )
    fallback_source = (
        source_root
        / "inci_tennis_expert"
        / "task6_fallback_normalizer.py"
    ).read_bytes()
    fallback_code = sha256(
        b"INCI-EXPERT-NORMALIZER-CODE-V1\0"
        + canonical_expert_bytes(
            (
                (
                    "inci_tennis_expert/task6_fallback_normalizer.py",
                    sha256(fallback_source).hexdigest(),
                ),
            )
        )
    ).hexdigest()
    fallback_schema = (
        schema_root / "task6-fallback-no-payload-v1.schema.json"
    ).read_bytes()
    fallback = ExpertNormalizerPinV1(
        "task6-fallback-v1",
        "fallback",
        "task6",
        "unregistered",
        1,
        fallback_code,
        sha256(fallback_schema).hexdigest(),
    )
    normalizer_values = {
        "schema_version": 1,
        "fallback": fallback,
        "entries": (),
    }
    normalizers = ExpertNormalizerRegistryV1(
        **normalizer_values,
        registry_sha256=expert_normalizer_registry_sha256(
            **normalizer_values
        ),
    )
    return structural, event, normalizers


def _schema_objects_fd(
    root: _Root,
    *,
    gate: Callable[[], None] | None = None,
) -> tuple[
    ExpertStructuralSchemaBundleV1,
    ExpertEventSchemaBundleV1,
    ExpertNormalizerRegistryV1,
]:
    schema_prefix = "inci_tennis_expert/schemas/"
    structural_pins = tuple(
        ExpertSchemaPinV1(
            role,
            contract,
            resource,
            sha256(
                _read_source_file(
                    root,
                    schema_prefix + resource,
                    gate=gate,
                )
            ).hexdigest(),
        )
        for role, contract, resource in _STRUCTURAL_SPEC
    )
    structural_values = {"schema_version": 1, "pins": structural_pins}
    structural = ExpertStructuralSchemaBundleV1(
        **structural_values,
        bundle_sha256=expert_structural_schema_bundle_sha256(
            **structural_values
        ),
    )
    event_pins = tuple(
        ExpertEventSchemaPinV1(
            kind,
            1,
            contract,
            resource,
            sha256(
                _read_source_file(
                    root,
                    schema_prefix + resource,
                    gate=gate,
                )
            ).hexdigest(),
        )
        for kind, contract, resource in _EVENT_SPEC
    )
    event_values = {"schema_version": 1, "pins": event_pins}
    event = ExpertEventSchemaBundleV1(
        **event_values,
        bundle_sha256=expert_event_schema_bundle_sha256(**event_values),
    )
    fallback_logical = (
        "inci_tennis_expert/task6_fallback_normalizer.py"
    )
    fallback_source = _read_source_file(
        root,
        fallback_logical,
        gate=gate,
    )
    fallback_code = sha256(
        b"INCI-EXPERT-NORMALIZER-CODE-V1\0"
        + canonical_expert_bytes(
            (
                (
                    fallback_logical,
                    sha256(fallback_source).hexdigest(),
                ),
            )
        )
    ).hexdigest()
    fallback_schema = _read_source_file(
        root,
        schema_prefix + "task6-fallback-no-payload-v1.schema.json",
        gate=gate,
    )
    fallback = ExpertNormalizerPinV1(
        "task6-fallback-v1",
        "fallback",
        "task6",
        "unregistered",
        1,
        fallback_code,
        sha256(fallback_schema).hexdigest(),
    )
    normalizer_values = {
        "schema_version": 1,
        "fallback": fallback,
        "entries": (),
    }
    normalizers = ExpertNormalizerRegistryV1(
        **normalizer_values,
        registry_sha256=expert_normalizer_registry_sha256(
            **normalizer_values
        ),
    )
    return structural, event, normalizers


def _python_runtime_digest() -> str:
    fields = (
        ("implementation_name", sys.implementation.name),
        (
            "implementation_version",
            ".".join(str(item) for item in sys.implementation.version[:3]),
        ),
        ("cache_tag", str(sys.implementation.cache_tag)),
        ("hexversion", str(sys.hexversion)),
        ("soabi", str(sysconfig.get_config_var("SOABI"))),
        ("platform_system", platform.system()),
        ("platform_machine", platform.machine()),
    )
    return sha256(
        b"INCI-EXPERT-PYTHON-RUNTIME-INVENTORY-V1\0"
        + canonical_expert_bytes(fields)
    ).hexdigest()


def _installed_environment(
    root: _Root,
    manifest: SessionManifest,
    *,
    gate: Callable[[], None] | None = None,
) -> tuple[
    ExpertCurrentEnvironmentV1,
    ExpertNormalizerRegistryV1,
    ExpertStructuralSchemaBundleV1,
    ExpertEventSchemaBundleV1,
]:
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _validate_exact_source_inventory(root)
    _run_environment_io_gate(gate)
    for name in _SOURCE_PACKAGE_NAMES:
        _read_source_file(
            root,
            f"{name}/__init__.py",
            gate=gate,
        )
    structural, event, normalizers = _schema_objects_fd(
        root,
        gate=gate,
    )
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    adapter_package_before = _read_source_file(
        root,
        "tennis_v1/__init__.py",
        gate=gate,
    )
    _run_environment_io_gate(gate)
    adapter = load_active_adapter_contract(
        provider_id=manifest.provider_id,
        product_tier=manifest.product_tier,
    )
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    adapter_package_after = _read_source_file(
        root,
        "tennis_v1/__init__.py",
        gate=gate,
    )
    _run_environment_io_gate(gate)
    if adapter_package_after != adapter_package_before:
        raise ValueError("expert_environment_inventory_invalid")
    current = ExpertCurrentEnvironmentV1(
        schema_version=1,
        phase1_code_sha256=_phase1_code_sha256_fd(
            root,
            gate=gate,
        ),
        phase1_adapter_code_sha256=adapter.adapter_code_sha256,
        expert_code_sha256=_inventory_digest_fd(
            root,
            _EXPERT_INVENTORY,
            b"INCI-EXPERT-CODE-INVENTORY-V1\0",
            gate=gate,
        ),
        io_code_sha256=_inventory_digest_fd(
            root,
            _IO_INVENTORY,
            b"INCI-EXPERT-IO-CODE-INVENTORY-V1\0",
            gate=gate,
        ),
        expert_adapter_code_sha256=_inventory_digest_fd(
            root,
            _ADAPTER_INVENTORY,
            b"INCI-EXPERT-ADAPTER-CODE-INVENTORY-V1\0",
            gate=gate,
        ),
        runtime_code_sha256=_inventory_digest_fd(
            root,
            _RUNTIME_INVENTORY,
            b"INCI-EXPERT-RUNTIME-CODE-INVENTORY-V1\0",
            gate=gate,
        ),
        dependency_lock_sha256=_inventory_digest_fd(
            root,
            _DEPENDENCY_INVENTORY,
            b"INCI-EXPERT-DEPENDENCY-INVENTORY-V1\0",
            gate=gate,
        ),
        python_runtime_sha256=_python_runtime_digest(),
        normalizer_registry_sha256=normalizers.registry_sha256,
        structural_schema_bundle_sha256=structural.bundle_sha256,
        event_schema_bundle_sha256=event.bundle_sha256,
    )
    if (
        current.phase1_code_sha256 != manifest.code_sha256
        or current.phase1_adapter_code_sha256
        != manifest.adapter_code_sha256
    ):
        raise ValueError("expert_environment_phase1_mismatch")
    _run_environment_io_gate(gate)
    _validate_source_root(root)
    _run_environment_io_gate(gate)
    return current, normalizers, structural, event


def collect_expert_current_environment(
    authority: ExpertEnvironmentCollectionAuthorityV1,
) -> ExpertCollectedEnvironmentV1:
    if type(authority) is not ExpertEnvironmentCollectionAuthorityV1:
        raise TypeError("authority")
    with _LOCK:
        state = _ENVIRONMENTS.pop(authority, None)
        if (
            state is None
            or state.consumed
            or state.owner_pid != os.getpid()
            or state.owner_thread is not threading.current_thread()
        ):
            raise ValueError("expert_environment_authority_invalid")
        state.consumed = True
        root = state.root
        manifest = _require_authorizer(
            root,
            state.authorizer,
            state.coordinator,
        )
        deadline = min(
            manifest.required_retention_until_ns,
            manifest.analysis_expires_at_ns,
        )

        def environment_gate() -> None:
            if _phase1_sample_wall_ns(root.clock_capability) >= deadline:
                raise ExpertLiveAuthorizationDenied()
            try:
                if (
                    _require_authorizer(
                        root,
                        state.authorizer,
                        state.coordinator,
                    )
                    is not manifest
                ):
                    raise ExpertLiveAuthorizationDenied()
            except Exception:
                if (
                    _phase1_sample_wall_ns(root.clock_capability)
                    >= deadline
                ):
                    raise ExpertLiveAuthorizationDenied() from None
                raise ExpertLiveAuthorizationDenied() from None
            if _phase1_sample_wall_ns(root.clock_capability) >= deadline:
                raise ExpertLiveAuthorizationDenied()

        environment_gate()
        try:
            current, normalizers, structural, event = _installed_environment(
                root,
                manifest,
                gate=environment_gate,
            )
            environment_gate()
            result = _create_expert_collected_environment_v1(
                current=current,
                normalizers=normalizers,
                structural_schemas=structural,
                event_schemas=event,
            )
            environment_gate()
            root.last_environment = deepcopy(current)
            return result
        except ExpertLiveAuthorizationDenied:
            raise
        except OSError as error:
            if str(error) == (
                "expert_environment_descriptor_close_uncertain"
            ):
                raise
            raise ValueError(
                "expert_environment_collection_invalid"
            ) from None
        except Exception as error:
            raise ValueError("expert_environment_collection_invalid") from None


def _journal_basename(session_id: str) -> str:
    return f"{session_id}{_SESSION_SUFFIX}"


def _reserve_basename(session_id: str) -> str:
    return f"{session_id}{_RESERVE_SUFFIX}"


def _marker_basename(session_id: str) -> str:
    return f"{session_id}{_MARKER_SUFFIX}"


def _encode_expert_marker(values: dict[str, object]) -> bytes:
    if tuple(values) != EXPERT_MARKER_FIELDS:
        raise ValueError("expert_marker_invalid")
    return json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _decode_expert_marker(content: bytes) -> dict[str, object]:
    if type(content) is not bytes or not content or content[:1].isspace():
        raise ValueError("expert_marker_invalid")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("expert_marker_invalid")
            result[key] = value
        return result

    try:
        text = content.decode("ascii")
        value = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_float=lambda _: (_ for _ in ()).throw(ValueError()),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except Exception:
        raise ValueError("expert_marker_invalid") from None
    if (
        type(value) is not dict
        or set(value) != set(EXPERT_MARKER_FIELDS)
        or _encode_expert_marker(
            {name: value[name] for name in EXPERT_MARKER_FIELDS}
        )
        != content
    ):
        raise ValueError("expert_marker_invalid")
    session_id = value["session_id"]
    digest_fields = (
        "expert_manifest_sha256",
        "evidence_session_manifest_sha256",
        "evidence_session_start_record_sha256",
        "provider_request_binding_sha256",
        "retention_binding_sha256",
    )
    if (
        type(session_id) is not str
        or not session_id
        or "/" in session_id
        or "\x00" in session_id
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["retention_delete_by_ns"]) is not int
        or type(value["created_at_ns"]) is not int
        or value["created_at_ns"] < 0
        or value["retention_delete_by_ns"] <= value["created_at_ns"]
        or value["journal_basename"] != _journal_basename(session_id)
        or value["reserve_basename"] != _reserve_basename(session_id)
        or any(
            type(value[name]) is not str
            or len(value[name]) != 64
            or any(character not in "0123456789abcdef" for character in value[name])
            for name in digest_fields
        )
    ):
        raise ValueError("expert_marker_invalid")
    return value


def _validate_manifest_phase1(
    manifest: ExpertSessionManifestV1,
    phase1: SessionManifest,
    authorizer: ProviderPersistenceAuthorizer,
) -> None:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    ExpertSessionManifestV1.__post_init__(manifest)
    decision = authorizer.bound_decision
    expected_manifest_digest = session_manifest_sha256(phase1)
    if (
        manifest.session_id != phase1.session_id
        or manifest.evidence_session_manifest_sha256
        != expected_manifest_digest
        or manifest.provider_id != phase1.provider_id
        or manifest.product_tier != phase1.product_tier
        or manifest.source_lineage_id != phase1.source_lineage_id
        or manifest.provider_manifest_file_sha256
        != phase1.provider_manifest_file_sha256
        or manifest.provider_manifest_canonical_sha256
        != phase1.provider_manifest_canonical_sha256
        or manifest.entitlement_id_sha256 != phase1.entitlement_id_sha256
        or manifest.permission_artifact_sha256
        != phase1.permission_artifact_sha256
        or manifest.qualification_artifact_sha256
        != phase1.qualification_artifact_sha256
        or manifest.qualification_trace_sha256
        != phase1.qualification_trace_sha256
        or manifest.provider_request_binding_sha256
        != decision.provider_request_binding_sha256
        or manifest.retention.retention_delete_by_ns
        != phase1.required_retention_until_ns
        or manifest.retention.access_expires_at_ns
        != phase1.access_expires_at_ns
        or manifest.retention.analysis_expires_at_ns
        != phase1.analysis_expires_at_ns
    ):
        raise ValueError("expert_manifest_phase1_binding_invalid")


def _live_gate(writer_or_root: _Writer | _Root, *, creation: bool) -> int:
    if type(writer_or_root) is _Writer:
        writer = writer_or_root
        root = writer.root
        authorizer = writer.authorizer
        coordinator = writer.coordinator
        deadline = writer.manifest.retention.retention_delete_by_ns
    else:
        raise TypeError("live gate requires writer")
    try:
        manifest = _require_authorizer(root, authorizer, coordinator)
        poll = authorizer.poll_session()
        if type(poll) is not bool or poll is not False:
            raise ValueError
        if creation:
            coordinator.require_expert_companion_creation_live(
                persistence_authorizer=authorizer
            )
        sampled = _phase1_sample_wall_ns(root.clock_capability)
        if sampled >= deadline:
            raise ValueError
        _validate_manifest_phase1(writer.manifest, manifest, authorizer)
        _require_root(root.token)
        return sampled
    except Exception:
        raise ExpertLiveAuthorizationDenied() from None


def _creation_gate(
    root: _Root,
    manifest: ExpertSessionManifestV1,
    authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> int:
    phase1 = _require_authorizer(root, authorizer, coordinator)
    try:
        poll = authorizer.poll_session()
        if type(poll) is not bool or poll is not False:
            raise ValueError
        coordinator.require_expert_companion_creation_live(
            persistence_authorizer=authorizer
        )
        sampled = _phase1_sample_wall_ns(root.clock_capability)
        _validate_manifest_phase1(manifest, phase1, authorizer)
        if sampled >= manifest.retention.retention_delete_by_ns:
            raise ValueError
        return sampled
    except Exception:
        raise ExpertLiveAuthorizationDenied() from None


def _allocate_reserve(fd: int) -> None:
    if hasattr(os, "posix_fallocate"):
        os.posix_fallocate(fd, 0, EXPERT_EMERGENCY_RESERVE_BYTES)
    elif sys.platform == "darwin":
        allocation = struct.pack(
            "=IIqqQ",
            4,
            3,
            0,
            EXPERT_EMERGENCY_RESERVE_BYTES,
            0,
        )
        fcntl.fcntl(fd, 42, allocation)
    else:
        raise OSError("expert_physical_allocation_unavailable")
    os.ftruncate(fd, EXPERT_EMERGENCY_RESERVE_BYTES)
    _require_file(
        fd,
        expected_size=EXPERT_EMERGENCY_RESERVE_BYTES,
        physical=True,
    )


def _unlink_if_present(directory_fd: int, basename: str) -> None:
    try:
        os.unlink(basename, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def create_expert_journal(
    authority: ExpertJournalRootAuthorityV1,
    manifest: ExpertSessionManifestV1,
    initial_cursor: ExpertJournalCursorV1,
    *,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ExpertJournalWriteCapabilityV1:
    if type(initial_cursor) is not ExpertJournalCursorV1:
        raise TypeError("initial_cursor")
    ExpertJournalCursorV1.__post_init__(initial_cursor)
    with _LOCK:
        root = _require_root(authority)
        created_at = _creation_gate(
            root,
            manifest,
            persistence_authorizer,
            coordinator,
        )
        if (
            initial_cursor.session_id != manifest.session_id
            or initial_cursor.group_count != 0
            or initial_cursor.record_count != 0
            or initial_cursor.last_parent_record_sha256
            != manifest.evidence_session_start_record_sha256
            or initial_cursor.expert_record_sha256 != manifest.manifest_sha256
        ):
            raise ValueError("expert_initial_cursor_invalid")
        journal_basename = _journal_basename(manifest.session_id)
        reserve_basename = _reserve_basename(manifest.session_id)
        marker_basename = _marker_basename(manifest.session_id)
        marker_values: dict[str, object] = {
            "schema_version": 1,
            "session_id": manifest.session_id,
            "journal_basename": journal_basename,
            "reserve_basename": reserve_basename,
            "expert_manifest_sha256": manifest.manifest_sha256,
            "evidence_session_manifest_sha256": (
                manifest.evidence_session_manifest_sha256
            ),
            "evidence_session_start_record_sha256": (
                manifest.evidence_session_start_record_sha256
            ),
            "provider_request_binding_sha256": (
                manifest.provider_request_binding_sha256
            ),
            "retention_binding_sha256": (
                manifest.retention.retention_binding_sha256
            ),
            "retention_delete_by_ns": (
                manifest.retention.retention_delete_by_ns
            ),
            "created_at_ns": created_at,
        }
        marker_bytes = _encode_expert_marker(marker_values)
        marker_fd = reserve_fd = journal_fd = -1
        created_marker = created_reserve = created_journal = False
        try:
            marker_fd = os.open(
                marker_basename,
                _OPEN_FILE_CREATE_FLAGS,
                0o600,
                dir_fd=root.markers_fd,
            )
            created_marker = True
            os.fchmod(marker_fd, 0o600)
            _complete_write(marker_fd, marker_bytes)
            os.fsync(marker_fd)
            _require_file(marker_fd, expected_size=len(marker_bytes))
            os.close(marker_fd)
            marker_fd = -1
            os.fsync(root.markers_fd)

            _creation_gate(
                root,
                manifest,
                persistence_authorizer,
                coordinator,
            )
            reserve_fd = os.open(
                reserve_basename,
                _OPEN_FILE_CREATE_FLAGS,
                0o600,
                dir_fd=root.sessions_fd,
            )
            created_reserve = True
            os.fchmod(reserve_fd, 0o600)
            _allocate_reserve(reserve_fd)
            os.fsync(reserve_fd)
            os.fsync(root.sessions_fd)

            _creation_gate(
                root,
                manifest,
                persistence_authorizer,
                coordinator,
            )
            journal_fd = os.open(
                journal_basename,
                _OPEN_FILE_CREATE_FLAGS | os.O_APPEND,
                0o600,
                dir_fd=root.sessions_fd,
            )
            created_journal = True
            os.fchmod(journal_fd, 0o600)
            _complete_write(journal_fd, encode_expert_file_header())
            _complete_write(journal_fd, encode_expert_manifest_frame(manifest))
            os.fsync(journal_fd)
            os.fsync(root.sessions_fd)
            _creation_gate(
                root,
                manifest,
                persistence_authorizer,
                coordinator,
            )
            reserve_identity = _reserve_identity(
                root,
                fd=reserve_fd,
                session_id=manifest.session_id,
                basename=reserve_basename,
                generation=root.generation,
            )
            journal_identity = _journal_identity(
                root,
                fd=journal_fd,
                basename=journal_basename,
                generation=root.generation,
            )
            token = _token(ExpertJournalWriteCapabilityV1)
            state = _Writer(
                token,
                root,
                manifest,
                initial_cursor,
                initial_cursor,
                persistence_authorizer,
                coordinator,
                journal_fd,
                reserve_fd,
                journal_basename,
                reserve_basename,
                marker_basename,
                marker_bytes,
                os.getpid(),
                threading.current_thread(),
                root.generation,
                reserve_identity,
                journal_identity,
            )
            _WRITERS[token] = state
            return token
        except Exception as error:
            _close_quietly(marker_fd)
            _close_quietly(reserve_fd)
            _close_quietly(journal_fd)
            if created_journal:
                _unlink_if_present(root.sessions_fd, journal_basename)
            if created_reserve:
                _unlink_if_present(root.sessions_fd, reserve_basename)
            if created_journal or created_reserve:
                try:
                    os.fsync(root.sessions_fd)
                except OSError:
                    pass
            if created_marker:
                _unlink_if_present(root.markers_fd, marker_basename)
                try:
                    os.fsync(root.markers_fd)
                except OSError:
                    pass
            if isinstance(error, OSError):
                raise OSError("expert_journal_durability_failed") from None
            raise


def _read_named_content(directory_fd: int, basename: str, maximum: int) -> bytes:
    fd = os.open(basename, _OPEN_FILE_READ_FLAGS, dir_fd=directory_fd)
    try:
        value = _require_file(fd)
        if value.st_size > maximum:
            raise ValueError("expert_file_oversized")
        content = _pread_exact(fd, 0, value.st_size)
        if len(content) != value.st_size:
            raise ValueError("expert_file_truncated")
        after = os.fstat(fd)
        if (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("expert_file_replaced")
        return content
    finally:
        os.close(fd)


def _validate_writer(
    writer: ExpertJournalWriteCapabilityV1,
    *,
    states: tuple[str, ...],
    terminal: bool = False,
) -> _Writer:
    if type(writer) is not ExpertJournalWriteCapabilityV1:
        raise TypeError("writer")
    state = _WRITERS.get(writer)
    if (
        state is None
        or state.owner_pid != os.getpid()
        or state.owner_thread is not threading.current_thread()
        or state.state not in states
    ):
        raise ValueError("expert_writer_invalid")
    try:
        _require_root(state.root.token)
        marker = _read_named_content(
            state.root.markers_fd,
            state.marker_basename,
            16_384,
        )
        if marker != state.marker_bytes:
            raise ValueError("expert_writer_identity_invalid")
        _validate_journal(state)
        if state.reserve_fd >= 0:
            _validate_reserve(state)
    except Exception:
        _poison_writer(state)
        raise ValueError("expert_writer_identity_invalid") from None
    if not terminal:
        _live_gate(state, creation=False)
    return state


def _poison_writer(state: _Writer) -> None:
    state.state = "poisoned"
    _close_quietly(state.reserve_fd)
    state.reserve_fd = -1
    _close_quietly(state.journal_fd)
    state.journal_fd = -1


def _candidate_cursor(
    prior: ExpertJournalCursorV1,
    group: ExpertJournalGroupV1,
) -> ExpertJournalCursorV1:
    return ExpertJournalCursorV1(
        schema_version=1,
        session_id=group.session_id,
        group_count=group.group_sequence,
        record_count=prior.record_count + len(group.records),
        last_parent_ingest_seq=group.parent.ingest_seq,
        last_parent_record_sha256=group.parent.record_sha256,
        expert_seq=group.records[-1].expert_seq,
        expert_record_sha256=group.final_expert_record_sha256,
        expert_state_sha256=group.post_expert_state_sha256,
        expert_trace_sha256=group.post_trace_sha256,
    )


def _validate_available_capacity(
    values: object,
    *,
    candidate_group_frame_bytes: int,
) -> None:
    if (
        type(candidate_group_frame_bytes) is not int
        or candidate_group_frame_bytes < 1
        or type(getattr(values, "f_bavail", None)) is not int
        or type(getattr(values, "f_frsize", None)) is not int
        or values.f_bavail < 0
        or values.f_frsize < 1
    ):
        raise ValueError("expert_capacity_probe_invalid")
    available = values.f_bavail * values.f_frsize
    required = (
        EXPERT_MIN_FREE_BYTES
        + MAX_EXPERT_TERMINAL_FRAME_BYTES
        + candidate_group_frame_bytes
    )
    if available <= required:
        raise ExpertPrewriteCapacityError(
            requested_bytes=candidate_group_frame_bytes,
            available_bytes=available,
            emergency_reserve_bytes=EXPERT_EMERGENCY_RESERVE_BYTES,
        )


def _available_bytes(values: object) -> int:
    blocks = getattr(values, "f_bavail", None)
    fragment = getattr(values, "f_frsize", None)
    if (
        type(blocks) is not int
        or type(fragment) is not int
        or blocks < 0
        or fragment < 1
    ):
        raise OSError("expert_capacity_probe_invalid")
    return blocks * fragment


def issue_expert_append_permit(
    writer: ExpertJournalWriteCapabilityV1,
    expected_state_sha256: str,
    expected_cursor: ExpertJournalCursorV1,
    group: ExpertJournalGroupV1,
    payloads: tuple[bytes, ...],
) -> ExpertJournalAppendPermitV1:
    if (
        type(expected_state_sha256) is not str
        or type(expected_cursor) is not ExpertJournalCursorV1
        or type(group) is not ExpertJournalGroupV1
        or type(payloads) is not tuple
        or any(type(item) is not bytes for item in payloads)
    ):
        raise TypeError("expert_append_arguments")
    with _LOCK:
        state = _validate_writer(writer, states=("ordinary_ready",))
        if state.prewrite_capacity_denied:
            _poison_writer(state)
            raise ValueError("expert_prewrite_capacity_already_denied")
        frame = encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=state.cursor,
        )
        candidate = _candidate_cursor(state.cursor, group)
        tail = state.tail
        if type(tail) is dict:
            unseen = tail.get("unseen")
            if (
                type(unseen) is not PersistedEvent
                or tail.get("ordinary_ack_cursor") is not None
                or group.parent.record_sha256
                != canonical_record_sha256(unseen)
                or group.parent.ingest_seq != unseen.ingest_seq
                or group.parent.event_type != unseen.event_type
                or group.parent.event_version != unseen.event_version
                or group.parent.local_wall_ns != unseen.local_wall_ns
                or group.parent.local_monotonic_ns
                != unseen.local_monotonic_ns
                or group.parent.clock_uncertainty_ns
                != unseen.clock_uncertainty_ns
            ):
                _poison_writer(state)
                raise ValueError("expert_live_tail_append_invalid")
        if (
            expected_cursor != state.cursor
            or expected_state_sha256 != state.cursor.expert_state_sha256
            or group.prior_expert_state_sha256 != expected_state_sha256
        ):
            _poison_writer(state)
            raise ValueError("expert_append_cas_failed")
        try:
            _validate_available_capacity(
                os.fstatvfs(state.journal_fd),
                candidate_group_frame_bytes=len(frame),
            )
        except ExpertPrewriteCapacityError:
            state.prewrite_capacity_denied = True
            raise
        except Exception as error:
            _poison_writer(state)
            raise ValueError("expert_capacity_probe_invalid") from None
        token = _token(ExpertJournalAppendPermitV1)
        _APPEND_PERMITS[token] = _AppendPermit(
            state,
            frame,
            group,
            payloads,
            candidate,
        )
        state.state = "append_permit_claimed"
        return token


def append_expert_group(
    permit: ExpertJournalAppendPermitV1,
) -> DurableExpertAppendReceiptV1:
    if type(permit) is not ExpertJournalAppendPermitV1:
        raise TypeError("permit")
    with _LOCK:
        bound = _APPEND_PERMITS.pop(permit, None)
        if bound is None or bound.consumed:
            raise ValueError("expert_append_permit_invalid")
        bound.consumed = True
        state = bound.writer
        _validate_writer(
            state.token,
            states=("append_permit_claimed",),
        )
        try:
            before = os.lseek(state.journal_fd, 0, os.SEEK_END)
            _complete_write(state.journal_fd, bound.frame)
            os.fsync(state.journal_fd)
            end = os.lseek(state.journal_fd, 0, os.SEEK_END)
            if end != before + len(bound.frame):
                raise OSError("expert_append_end_offset")
            state.journal_identity = _journal_identity(
                state.root,
                fd=state.journal_fd,
                basename=state.journal_basename,
                generation=state.generation,
            )
            receipt = DurableExpertAppendReceiptV1(
                session_id=state.manifest.session_id,
                group_sequence=bound.group.group_sequence,
                group_sha256=bound.group.group_sha256,
                last_parent_record_sha256=(
                    bound.group.parent.record_sha256
                ),
                last_expert_seq=bound.group.records[-1].expert_seq,
                final_expert_record_sha256=(
                    bound.group.final_expert_record_sha256
                ),
                post_expert_state_sha256=(
                    bound.group.post_expert_state_sha256
                ),
                post_expert_trace_sha256=bound.group.post_trace_sha256,
                durable_end_offset=end,
            )
            state.cursor = bound.candidate_cursor
            state.pending_cursor = bound.candidate_cursor
            state.pending_receipt = receipt
            state.state = "receipt_pending"
            return receipt
        except Exception as error:
            _poison_writer(state)
            if isinstance(error, OSError):
                raise OSError("expert_journal_durability_failed") from None
            raise


def acknowledge_expert_publication(
    writer: ExpertJournalWriteCapabilityV1,
    *,
    receipt: DurableExpertAppendReceiptV1,
    candidate_state_sha256: str,
    candidate_cursor: ExpertJournalCursorV1,
) -> None:
    if (
        type(receipt) is not DurableExpertAppendReceiptV1
        or type(candidate_state_sha256) is not str
        or type(candidate_cursor) is not ExpertJournalCursorV1
    ):
        raise TypeError("expert_publication_arguments")
    with _LOCK:
        state = _validate_writer(writer, states=("receipt_pending",))
        tail = state.tail
        if type(tail) is dict:
            try:
                _validate_tail_static_identities(state, tail)
            except Exception:
                _poison_writer(state)
                raise ValueError(
                    "expert_publication_identity_invalid"
                ) from None
        if (
            receipt != state.pending_receipt
            or candidate_cursor != state.pending_cursor
            or candidate_state_sha256 != candidate_cursor.expert_state_sha256
        ):
            _poison_writer(state)
            raise ValueError("expert_publication_receipt_invalid")
        state.pending_receipt = None
        state.pending_cursor = None
        if type(tail) is dict:
            tail["ordinary_ack_cursor"] = candidate_cursor
        state.state = "ordinary_ready"


def _validate_tail_static_identities(
    state: _Writer,
    tail: dict[str, object],
) -> None:
    identities = tail.get("identities")
    if type(identities) is not tuple or len(identities) != 4:
        raise ValueError
    current_phase1 = inspect_phase1_evidence_file_identities(
        state.root.token,
        session_manifest=tail["phase1_manifest"],
        session_start=tail["session_start"],
    )
    current_companion = inspect_expert_companion_file_identities(
        state.root.token,
        manifest=state.manifest,
    )
    if (
        current_phase1 != identities[:2]
        or current_companion[0] != identities[2]
    ):
        raise ValueError


def _terminal_material_gate(
    state: _Writer,
    tail: dict[str, object],
) -> None:
    payload = tail.get("terminal_payload")
    try:
        _require_authorizer(
            state.root,
            state.authorizer,
            state.coordinator,
        )
        if type(payload) is not dict:
            raise ValueError
        expected_poll = (
            payload["clean"] is True
            and payload["reason"] == "session_end"
        )
        poll = state.authorizer.poll_session()
        if type(poll) is not bool or poll is not expected_poll:
            raise ValueError
        sampled = _phase1_sample_wall_ns(state.root.clock_capability)
        if sampled >= state.manifest.retention.retention_delete_by_ns:
            raise ValueError
        _validate_tail_static_identities(state, tail)
    except Exception:
        raise ExpertLiveAuthorizationDenied() from None


def _terminal_gate(
    state: _Writer,
    terminal: ExpertSessionTerminalV1,
    *,
    final_cursor: ExpertJournalCursorV1 | None = None,
) -> None:
    if type(terminal) is not ExpertSessionTerminalV1:
        raise TypeError("terminal")
    ExpertSessionTerminalV1.__post_init__(terminal)
    try:
        _require_authorizer(state.root, state.authorizer, state.coordinator)
        poll = state.authorizer.poll_session()
        expected_poll = (
            terminal.clean
            and terminal.evidence_terminal_reason == "session_end"
        )
        if type(poll) is not bool or poll is not expected_poll:
            raise ValueError
        sampled = _phase1_sample_wall_ns(state.root.clock_capability)
        if sampled >= state.manifest.retention.retention_delete_by_ns:
            raise ValueError
        validate_expert_terminal_against_cursor(
            terminal,
            state.cursor if final_cursor is None else final_cursor,
        )
        if (
            terminal.session_id != state.manifest.session_id
            or terminal.expert_manifest_sha256
            != state.manifest.manifest_sha256
        ):
            raise ValueError
        tail = state.tail
        if type(tail) is not dict:
            raise ValueError
        _validate_tail_static_identities(state, tail)
    except Exception:
        raise ExpertLiveAuthorizationDenied() from None


def _release_reserve(state: _Writer) -> None:
    _validate_reserve(state)
    reserve_fd = state.reserve_fd
    state.reserve_fd = -1
    state.reserve_identity = None
    os.close(reserve_fd)
    os.unlink(state.reserve_basename, dir_fd=state.root.sessions_fd)
    os.fsync(state.root.sessions_fd)


def issue_expert_terminal_permit(
    writer: ExpertJournalWriteCapabilityV1,
    terminal: ExpertSessionTerminalV1,
) -> ExpertJournalTerminalPermitV1:
    with _LOCK:
        state = _validate_writer(
            writer,
            states=("ordinary_ready",),
            terminal=True,
        )
        if (
            state.terminal_pair is None
            or state.terminal_pair[1] is not terminal
        ):
            _poison_writer(state)
            raise ValueError("expert_terminal_alignment_invalid")
        _terminal_gate(state, terminal)
        frame = encode_expert_terminal_frame(
            terminal,
            final_cursor=state.cursor,
        )
        try:
            _release_reserve(state)
        except Exception as error:
            _poison_writer(state)
            if isinstance(error, OSError):
                raise OSError("expert_journal_durability_failed") from None
            raise
        token = _token(ExpertJournalTerminalPermitV1)
        _TERMINAL_PERMITS[token] = _TerminalPermit(state, frame, terminal)
        state.state = "ordinary_terminal_bound"
        return token


def append_expert_terminal(
    permit: ExpertJournalTerminalPermitV1,
) -> DurableExpertTerminalReceiptV1:
    if type(permit) is not ExpertJournalTerminalPermitV1:
        raise TypeError("permit")
    with _LOCK:
        bound = _TERMINAL_PERMITS.pop(permit, None)
        if bound is None or bound.consumed:
            raise ValueError("expert_terminal_permit_invalid")
        bound.consumed = True
        state = _validate_writer(
            bound.writer.token,
            states=("ordinary_terminal_bound",),
            terminal=True,
        )
        _terminal_gate(state, bound.terminal)
        try:
            values = os.fstatvfs(state.journal_fd)
            available = _available_bytes(values)
            if available < len(bound.frame):
                raise OSError("expert_terminal_capacity_low")
            before = os.lseek(state.journal_fd, 0, os.SEEK_END)
            _complete_write(state.journal_fd, bound.frame)
            os.fsync(state.journal_fd)
            end = os.lseek(state.journal_fd, 0, os.SEEK_END)
            if end != before + len(bound.frame):
                raise OSError("expert_terminal_end_offset")
            state.journal_identity = _journal_identity(
                state.root,
                fd=state.journal_fd,
                basename=state.journal_basename,
                generation=state.generation,
            )
            _terminal_gate(state, bound.terminal)
            journal_fd = state.journal_fd
            state.journal_fd = -1
            os.close(journal_fd)
            state.state = "closed"
            return DurableExpertTerminalReceiptV1(
                state.manifest.session_id,
                bound.terminal.terminal_sha256,
                bound.terminal.expert_group_count + 1,
                end,
                True,
            )
        except Exception as error:
            _poison_writer(state)
            if isinstance(error, OSError):
                raise OSError("expert_journal_durability_failed") from None
            raise


def issue_expert_emergency_append_permit(
    writer: ExpertJournalWriteCapabilityV1,
    *,
    expected_state_sha256: str,
    expected_cursor: ExpertJournalCursorV1,
    evidence_terminal: PersistedEvent,
    group: ExpertJournalGroupV1,
    payloads: tuple[bytes, ...],
    terminal: ExpertSessionTerminalV1,
) -> ExpertEmergencyAppendPermitV1:
    if (
        type(expected_state_sha256) is not str
        or type(expected_cursor) is not ExpertJournalCursorV1
        or type(evidence_terminal) is not PersistedEvent
        or type(group) is not ExpertJournalGroupV1
        or type(payloads) is not tuple
        or type(terminal) is not ExpertSessionTerminalV1
    ):
        raise TypeError("expert_emergency_arguments")
    with _LOCK:
        state = _validate_writer(
            writer,
            states=("ordinary_ready",),
            terminal=True,
        )
        tail = state.tail
        if (
            expected_cursor != state.cursor
            or expected_state_sha256 != state.cursor.expert_state_sha256
            or not state.prewrite_capacity_denied
            or type(tail) is not dict
            or tail.get("consumed") is not True
            or tail.get("unseen") is None
            or state.terminal_pair is None
            or state.terminal_pair[0] is not evidence_terminal
            or state.terminal_pair[1] is not terminal
        ):
            _poison_writer(state)
            raise ValueError("expert_emergency_cas_invalid")
        group_frame = encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=state.cursor,
        )
        candidate = _candidate_cursor(state.cursor, group)
        unseen = tail["unseen"]
        if (
            type(unseen) is not PersistedEvent
            or group.parent.record_sha256 != canonical_record_sha256(unseen)
            or group.parent.ingest_seq != unseen.ingest_seq
            or group.parent.event_type != unseen.event_type
            or group.parent.event_version != unseen.event_version
            or group.parent.local_wall_ns != unseen.local_wall_ns
            or group.parent.local_monotonic_ns != unseen.local_monotonic_ns
            or group.parent.clock_uncertainty_ns
            != unseen.clock_uncertainty_ns
        ):
            _poison_writer(state)
            raise ValueError("expert_emergency_parent_invalid")
        terminal_frame = encode_expert_terminal_frame(
            terminal,
            final_cursor=candidate,
        )
        if len(group_frame) + len(terminal_frame) > EXPERT_EMERGENCY_RESERVE_BYTES:
            _poison_writer(state)
            raise ValueError("expert_emergency_frames_oversized")
        _terminal_gate(state, terminal, final_cursor=candidate)
        try:
            _release_reserve(state)
        except Exception as error:
            _poison_writer(state)
            if isinstance(error, OSError):
                raise OSError("expert_journal_durability_failed") from None
            raise
        token = _token(ExpertEmergencyAppendPermitV1)
        _EMERGENCY_PERMITS[token] = _EmergencyPermit(
            state,
            group_frame,
            terminal_frame,
            group,
            terminal,
            candidate,
        )
        state.state = "emergency_bound"
        return token


def append_expert_emergency_group_and_terminal(
    permit: ExpertEmergencyAppendPermitV1,
) -> DurableExpertEmergencyReceiptV1:
    if type(permit) is not ExpertEmergencyAppendPermitV1:
        raise TypeError("permit")
    with _LOCK:
        bound = _EMERGENCY_PERMITS.pop(permit, None)
        if bound is None or bound.consumed:
            raise ValueError("expert_emergency_permit_invalid")
        bound.consumed = True
        state = _validate_writer(
            bound.writer.token,
            states=("emergency_bound",),
            terminal=True,
        )
        try:
            _terminal_gate(
                state,
                bound.terminal,
                final_cursor=bound.candidate_cursor,
            )
            total = len(bound.group_frame) + len(bound.terminal_frame)
            values = os.fstatvfs(state.journal_fd)
            if _available_bytes(values) < total:
                raise OSError("expert_emergency_capacity_low")
            before = os.lseek(state.journal_fd, 0, os.SEEK_END)
            _complete_write(state.journal_fd, bound.group_frame)
            os.fsync(state.journal_fd)
            group_end = os.lseek(state.journal_fd, 0, os.SEEK_END)
            state.journal_identity = _journal_identity(
                state.root,
                fd=state.journal_fd,
                basename=state.journal_basename,
                generation=state.generation,
            )
            _terminal_gate(
                state,
                bound.terminal,
                final_cursor=bound.candidate_cursor,
            )
            _complete_write(state.journal_fd, bound.terminal_frame)
            os.fsync(state.journal_fd)
            terminal_end = os.lseek(state.journal_fd, 0, os.SEEK_END)
            if (
                group_end != before + len(bound.group_frame)
                or terminal_end != group_end + len(bound.terminal_frame)
            ):
                raise OSError("expert_emergency_end_offset")
            state.journal_identity = _journal_identity(
                state.root,
                fd=state.journal_fd,
                basename=state.journal_basename,
                generation=state.generation,
            )
            _terminal_gate(
                state,
                bound.terminal,
                final_cursor=bound.candidate_cursor,
            )
            journal_fd = state.journal_fd
            state.journal_fd = -1
            os.close(journal_fd)
            state.cursor = bound.candidate_cursor
            state.state = "closed"
            group_receipt = DurableExpertAppendReceiptV1(
                state.manifest.session_id,
                bound.group.group_sequence,
                bound.group.group_sha256,
                bound.group.parent.record_sha256,
                bound.group.records[-1].expert_seq,
                bound.group.final_expert_record_sha256,
                bound.group.post_expert_state_sha256,
                bound.group.post_trace_sha256,
                group_end,
            )
            terminal_receipt = DurableExpertTerminalReceiptV1(
                state.manifest.session_id,
                bound.terminal.terminal_sha256,
                bound.terminal.expert_group_count + 1,
                terminal_end,
                True,
            )
            return DurableExpertEmergencyReceiptV1(
                state.manifest.session_id,
                group_receipt,
                terminal_receipt,
                True,
            )
        except Exception as error:
            _poison_writer(state)
            if isinstance(error, OSError):
                raise OSError("expert_journal_durability_failed") from None
            raise


def issue_expert_read_capability(
    authority: ExpertJournalRootAuthorityV1,
    manifest: ExpertSessionManifestV1,
) -> ExpertJournalReadCapabilityV1:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    with _LOCK:
        root = _require_root(authority)
        try:
            marker = _decode_expert_marker(
                _read_named_content(
                    root.markers_fd,
                    _marker_basename(manifest.session_id),
                    16_384,
                )
            )
        except (OSError, ValueError):
            raise ValueError("expert_reader_manifest_invalid") from None
        if marker["expert_manifest_sha256"] != manifest.manifest_sha256:
            raise ValueError("expert_reader_manifest_invalid")
        try:
            fd = os.open(
                _journal_basename(manifest.session_id),
                _OPEN_FILE_READ_FLAGS,
                dir_fd=root.sessions_fd,
            )
        except OSError:
            raise ValueError("expert_reader_manifest_invalid") from None
        try:
            descriptor_stat = _require_file(fd)
            token = _token(ExpertJournalReadCapabilityV1)
            _READERS[token] = _Reader(
                token,
                root,
                manifest,
                fd,
                EXPERT_FILE_HEADER_BYTES,
                None,
                os.getpid(),
                threading.current_thread(),
                _descriptor_identity_observation(descriptor_stat),
                last_good_offset=EXPERT_FILE_HEADER_BYTES,
            )
            return token
        except Exception:
            os.close(fd)
            raise


def _reader_state(reader: ExpertJournalReadCapabilityV1) -> _Reader:
    if type(reader) is not ExpertJournalReadCapabilityV1:
        raise TypeError("reader")
    state = _READERS.get(reader)
    if state is None:
        raise ValueError("expert_reader_invalid")
    if state.replay_authority is not None:
        if (
            state.owner_pid != os.getpid()
            or state.owner_thread is not threading.current_thread()
        ):
            raise ValueError("expert_reader_invalid")
        _reader_replay_access_gate(state)
        if state.closed:
            raise ValueError("expert_reader_invalid")
    else:
        if (
            state.closed
            or state.owner_pid != os.getpid()
            or state.owner_thread is not threading.current_thread()
        ):
            raise ValueError("expert_reader_invalid")
        _require_root(state.root.token)
    return state


def _read_frame(fd: int, offset: int) -> tuple[bytes | None, int]:
    prefix = _pread_exact(fd, offset, EXPERT_FRAME_PREFIX_BYTES)
    if not prefix:
        return None, offset
    if len(prefix) != EXPERT_FRAME_PREFIX_BYTES:
        raise ValueError("expert_frame_torn")
    _, _, total, metadata_size, payload_size = decode_expert_frame_prefix(
        prefix
    )
    if total > MAX_EXPERT_FRAME_BYTES:
        raise ValueError("expert_frame_oversized")
    body = _pread_exact(
        fd,
        offset + EXPERT_FRAME_PREFIX_BYTES,
        total - EXPERT_FRAME_PREFIX_BYTES,
    )
    if len(body) != total - EXPERT_FRAME_PREFIX_BYTES:
        raise ValueError("expert_frame_torn")
    frame = prefix + body
    metadata_end = EXPERT_FRAME_PREFIX_BYTES + metadata_size
    payload_end = metadata_end + payload_size
    validate_expert_frame_parts(
        prefix,
        frame[EXPERT_FRAME_PREFIX_BYTES:metadata_end],
        frame[metadata_end:payload_end],
        frame[payload_end:],
    )
    return frame, offset + total


def _reader_replay_access_gate(state: _Reader) -> None:
    authority = state.replay_authority
    if authority is None:
        return
    replay = _REPLAYS.get(authority)
    if (
        type(replay) is not dict
        or replay.get("closed")
        or replay.get("companion_reader") is not state.token
        or type(replay.get("state")) is not str
    ):
        raise ValueError("expert_replay_companion_invalid")
    _replay_access_gate(replay)


def _reader_replay_full_integrity_gate(state: _Reader) -> None:
    authority = state.replay_authority
    if authority is None:
        return
    replay = _REPLAYS.get(authority)
    if (
        type(replay) is not dict
        or replay.get("closed")
        or replay.get("companion_reader") is not state.token
        or type(replay.get("state")) is not str
    ):
        raise ValueError("expert_replay_companion_invalid")
    if replay["state"] == "new":
        _take_prepare_replay_full_integrity_snapshot(replay)
    else:
        _replay_state(authority, replay["state"])


def _reader_pread(
    state: _Reader,
    *,
    offset: int,
    length: int,
) -> bytes:
    _reader_replay_access_gate(state)
    before = os.fstat(state.fd)
    _reader_replay_full_integrity_gate(state)
    content = _gated_pread_exact(
        state.fd,
        offset,
        length,
        gate=lambda: _reader_replay_access_gate(state),
    )
    _reader_replay_access_gate(state)
    after = os.fstat(state.fd)
    _reader_replay_access_gate(state)
    named_after = os.stat(
        _journal_basename(state.manifest.session_id),
        dir_fd=state.root.sessions_fd,
        follow_symlinks=False,
    )
    _reader_replay_access_gate(state)
    if (
        not _same_file_identity(before, after)
        or not _same_file_identity(after, named_after)
        or _descriptor_identity_observation(after) != state.fd_identity
    ):
        raise ValueError("expert_replay_companion_invalid")
    return content


def _read_reader_frame(
    state: _Reader,
    offset: int,
) -> tuple[bytes | None, int]:
    prefix = _reader_pread(
        state,
        offset=offset,
        length=EXPERT_FRAME_PREFIX_BYTES,
    )
    if not prefix:
        return None, offset
    if len(prefix) != EXPERT_FRAME_PREFIX_BYTES:
        raise ValueError("expert_frame_torn")
    _, _, total, metadata_size, payload_size = decode_expert_frame_prefix(
        prefix
    )
    if total > MAX_EXPERT_FRAME_BYTES:
        raise ValueError("expert_frame_oversized")
    body = _reader_pread(
        state,
        offset=offset + EXPERT_FRAME_PREFIX_BYTES,
        length=total - EXPERT_FRAME_PREFIX_BYTES,
    )
    if len(body) != total - EXPERT_FRAME_PREFIX_BYTES:
        raise ValueError("expert_frame_torn")
    frame = prefix + body
    metadata_end = EXPERT_FRAME_PREFIX_BYTES + metadata_size
    payload_end = metadata_end + payload_size
    validate_expert_frame_parts(
        prefix,
        frame[EXPERT_FRAME_PREFIX_BYTES:metadata_end],
        frame[metadata_end:payload_end],
        frame[payload_end:],
    )
    return frame, offset + total


def read_expert_manifest(
    reader: ExpertJournalReadCapabilityV1,
) -> ExpertSessionManifestV1:
    with _LOCK:
        state = _reader_state(reader)
        if state.manifest_read:
            raise ValueError("expert_manifest_already_read")
        header = _reader_pread(
            state,
            offset=0,
            length=EXPERT_FILE_HEADER_BYTES,
        )
        decode_expert_file_header(header)
        frame, end = _read_reader_frame(
            state,
            EXPERT_FILE_HEADER_BYTES,
        )
        if frame is None:
            raise ValueError("expert_manifest_missing")
        manifest = decode_expert_manifest_frame(frame)
        if manifest != state.manifest:
            raise ValueError("expert_manifest_mismatch")
        _reader_replay_access_gate(state)
        state.manifest_read = True
        state.offset = end
        state.last_good_offset = end
        return manifest


def read_next_expert_group(
    reader: ExpertJournalReadCapabilityV1,
) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...]] | None:
    with _LOCK:
        state = _reader_state(reader)
        if not state.manifest_read:
            raise ValueError("expert_manifest_not_read")
        frame, end = _read_reader_frame(state, state.offset)
        if frame is None:
            _reader_replay_access_gate(state)
            return None
        kind, frame_sequence, _, _, _ = decode_expert_complete_frame(frame)
        if kind == 3:
            terminal = (
                decode_expert_terminal_frame_replay_material(frame)
                if state.replay_authority is not None
                else decode_expert_terminal_frame_structural(frame)
            )
            _reader_replay_access_gate(state)
            state.terminal = terminal
            state.offset = end
            state.last_good_offset = end
            state.last_frame_sequence = frame_sequence
            return None
        group, payloads = decode_expert_group_frame_structural(frame)
        _reader_replay_access_gate(state)
        state.offset = end
        state.last_good_offset = end
        state.last_frame_sequence = frame_sequence
        state.group_count += 1
        state.record_count += len(group.records)
        return group, payloads


def read_expert_terminal_and_summary(
    reader: ExpertJournalReadCapabilityV1,
) -> tuple[ExpertSessionTerminalV1 | None, ExpertJournalScanSummaryV1]:
    with _LOCK:
        state = _reader_state(reader)
        if not state.manifest_read:
            raise ValueError("expert_manifest_not_read")
        terminal = state.terminal
        if terminal is None:
            frame, end = _read_reader_frame(state, state.offset)
            if frame is not None:
                kind, frame_sequence, _, _, _ = (
                    decode_expert_complete_frame(frame)
                )
                if kind != 3:
                    raise ValueError("expert_groups_remain")
                terminal = (
                    decode_expert_terminal_frame_replay_material(frame)
                    if state.replay_authority is not None
                    else decode_expert_terminal_frame_structural(frame)
                )
                _reader_replay_access_gate(state)
                state.offset = end
                state.last_good_offset = end
                state.last_frame_sequence = frame_sequence
        _reader_replay_access_gate(state)
        size = os.fstat(state.fd).st_size
        _reader_replay_access_gate(state)
        if terminal is None:
            issue = ExpertJournalScanIssueV1.MISSING_TERMINAL
        elif state.offset != size:
            issue = ExpertJournalScanIssueV1.CORRUPT_TAIL
        elif not terminal.clean:
            issue = ExpertJournalScanIssueV1.HALTED_TERMINAL
        else:
            issue = None
        summary = ExpertJournalScanSummaryV1(
            1,
            size,
            state.last_good_offset,
            state.last_frame_sequence,
            state.group_count,
            state.record_count,
            bool(terminal is not None and terminal.clean and issue is None),
            issue,
            issue is None,
        )
        _reader_replay_access_gate(state)
        return terminal, summary


class _ReplayDiagnosticTorn(RuntimeError):
    pass


class _ReplayCloseUncertain(RuntimeError):
    pass


def _read_replay_begin_diagnostic_frame(
    state: _Reader,
    offset: int,
) -> tuple[int, int, bytes, int, int] | None:
    """Frame and hash one unread frame without decoding a group or payload."""

    prefix = _reader_pread(
        state,
        offset=offset,
        length=EXPERT_FRAME_PREFIX_BYTES,
    )
    if not prefix:
        return None
    if len(prefix) != EXPERT_FRAME_PREFIX_BYTES:
        raise _ReplayDiagnosticTorn()
    (
        kind,
        frame_sequence,
        total,
        metadata_size,
        payload_size,
    ) = decode_expert_frame_prefix(prefix)
    digest = sha256(EXPERT_FRAME_DIGEST_DOMAIN + prefix)
    metadata_offset = offset + EXPERT_FRAME_PREFIX_BYTES
    metadata_remaining = metadata_size
    metadata_parts: list[bytes] = []
    while metadata_remaining:
        chunk_length = min(metadata_remaining, 65_536)
        chunk = _reader_pread(
            state,
            offset=metadata_offset,
            length=chunk_length,
        )
        if len(chunk) != chunk_length:
            raise _ReplayDiagnosticTorn()
        digest.update(chunk)
        metadata_parts.append(chunk)
        metadata_offset += chunk_length
        metadata_remaining -= chunk_length

    metadata = b"".join(metadata_parts)
    metadata_record_count = 0
    payload_descriptors: tuple[tuple[int, str], ...] = ()
    if kind == EXPERT_FRAME_KIND_PARENT_GROUP:
        (
            metadata_sequence,
            metadata_record_count,
            payload_descriptors,
        ) = validate_expert_group_metadata_diagnostic(metadata)
        if metadata_sequence != frame_sequence:
            raise ValueError("expert_group_payload_area_invalid")

    payload_offset = metadata_offset
    payload_remaining = payload_size
    payload_count = 0
    if kind == EXPERT_FRAME_KIND_PARENT_GROUP:
        while payload_remaining:
            if (
                payload_remaining < 8
                or payload_count >= MAX_EXPERT_OUTCOMES_PER_PARENT
            ):
                raise ValueError("expert_group_payload_area_invalid")
            length_prefix = _reader_pread(
                state,
                offset=payload_offset,
                length=8,
            )
            if len(length_prefix) != 8:
                raise _ReplayDiagnosticTorn()
            digest.update(length_prefix)
            payload_offset += 8
            payload_remaining -= 8
            payload_length = struct.unpack(">Q", length_prefix)[0]
            if (
                payload_length > MAX_EXPERT_EVENT_PAYLOAD_BYTES
                or payload_length > payload_remaining
            ):
                raise ValueError("expert_group_payload_area_invalid")
            unread = payload_length
            payload_digest = sha256()
            while unread:
                chunk_length = min(unread, 65_536)
                chunk = _reader_pread(
                    state,
                    offset=payload_offset,
                    length=chunk_length,
                )
                if len(chunk) != chunk_length:
                    raise _ReplayDiagnosticTorn()
                digest.update(chunk)
                payload_digest.update(chunk)
                payload_offset += chunk_length
                payload_remaining -= chunk_length
                unread -= chunk_length
            if (
                payload_count >= len(payload_descriptors)
                or payload_length
                != payload_descriptors[payload_count][0]
                or payload_digest.hexdigest()
                != payload_descriptors[payload_count][1]
            ):
                raise ValueError("expert_group_payload_area_invalid")
            payload_count += 1
        if payload_count == 0:
            raise ValueError("expert_group_payload_area_invalid")
    elif payload_size != 0:
        raise ValueError("expert_frame_payload_invalid")

    trailer = _reader_pread(
        state,
        offset=payload_offset,
        length=EXPERT_FRAME_TRAILER_BYTES,
    )
    if len(trailer) != EXPERT_FRAME_TRAILER_BYTES:
        raise _ReplayDiagnosticTorn()
    validate_expert_streamed_frame_trailer(
        prefix,
        payload_area_bytes=payload_size,
        trailer=trailer,
        computed_digest=digest.digest(),
    )
    if kind == EXPERT_FRAME_KIND_PARENT_GROUP:
        if (
            metadata_record_count != payload_count
        ):
            raise ValueError("expert_group_payload_area_invalid")
    end = offset + total
    if payload_offset + EXPERT_FRAME_TRAILER_BYTES != end:
        raise ValueError("expert_frame_size_invalid")
    terminal_frame = (
        prefix + metadata + trailer
        if kind == EXPERT_FRAME_KIND_TERMINAL
        else b""
    )
    return kind, frame_sequence, terminal_frame, end, payload_count


def _read_replay_begin_diagnostic_material(
    reader: ExpertJournalReadCapabilityV1,
) -> tuple[ExpertSessionTerminalV1 | None, ExpertJournalScanSummaryV1]:
    """Scan unread companion bytes without materializing groups or payloads."""

    state = _reader_state(reader)
    if not state.manifest_read:
        raise ValueError("expert_manifest_not_read")
    terminal = state.terminal
    local_offset = state.offset
    local_last_good_offset = state.last_good_offset
    local_group_count = state.group_count
    local_record_count = state.record_count
    local_last_frame_sequence = state.last_frame_sequence
    scan_issue: ExpertJournalScanIssueV1 | None = None
    try:
        while terminal is None:
            item = _read_replay_begin_diagnostic_frame(state, local_offset)
            if item is None:
                break
            kind, frame_sequence, frame_without_payload, end, payload_count = (
                item
            )
            if kind == EXPERT_FRAME_KIND_PARENT_GROUP:
                _reader_replay_access_gate(state)
                local_offset = end
                local_last_good_offset = end
                local_group_count += 1
                local_record_count += payload_count
                local_last_frame_sequence = frame_sequence
                continue
            if kind != EXPERT_FRAME_KIND_TERMINAL:
                raise ValueError("expert_journal_frame_order_invalid")
            candidate_terminal = decode_expert_terminal_frame_replay_material(
                frame_without_payload
            )
            _reader_replay_access_gate(state)
            terminal = candidate_terminal
            local_offset = end
            local_last_good_offset = end
            local_last_frame_sequence = frame_sequence
    except ExpertReplayAccessDenied:
        raise
    except _ReplayDiagnosticTorn:
        _reader_replay_access_gate(state)
        scan_issue = ExpertJournalScanIssueV1.TORN_TAIL
    except ValueError:
        _reader_replay_access_gate(state)
        scan_issue = ExpertJournalScanIssueV1.CORRUPT_TAIL

    _reader_replay_access_gate(state)
    size = os.fstat(state.fd).st_size
    _reader_replay_access_gate(state)
    if scan_issue is not None:
        issue = scan_issue
    elif local_offset > size:
        issue = ExpertJournalScanIssueV1.CORRUPT_TAIL
    elif terminal is None:
        issue = ExpertJournalScanIssueV1.MISSING_TERMINAL
    elif local_offset != size:
        issue = ExpertJournalScanIssueV1.CORRUPT_TAIL
    elif not terminal.clean:
        issue = ExpertJournalScanIssueV1.HALTED_TERMINAL
    else:
        issue = None
    summary = ExpertJournalScanSummaryV1(
        schema_version=1,
        file_size=size,
        last_good_offset=local_last_good_offset,
        last_frame_sequence=local_last_frame_sequence,
        group_count=local_group_count,
        record_count=local_record_count,
        terminal_clean=bool(
            terminal is not None and terminal.clean and issue is None
        ),
        issue=issue,
        journal_valid=issue is None,
    )
    _reader_replay_access_gate(state)
    state.terminal = terminal
    state.offset = local_offset
    state.last_good_offset = local_last_good_offset
    state.last_frame_sequence = local_last_frame_sequence
    state.group_count = local_group_count
    state.record_count = local_record_count
    return terminal, summary


def close_expert_reader(reader: ExpertJournalReadCapabilityV1) -> None:
    if type(reader) is not ExpertJournalReadCapabilityV1:
        raise TypeError("reader")
    with _LOCK:
        state = _READERS.get(reader)
        if (
            state is None
            or state.closed
            or state.owner_pid != os.getpid()
            or state.owner_thread is not threading.current_thread()
            or not state.root.active
            or _ROOTS.get(state.root.token) is not state.root
        ):
            raise ValueError("expert_reader_invalid")
        try:
            if _require_root(state.root.token) is not state.root:
                raise ValueError
        except Exception:
            raise ValueError("expert_reader_invalid") from None
        state.closed = True
        descriptor = state.fd
        state.fd = -1
        _READERS.pop(reader, None)
        try:
            os.close(descriptor)
        except OSError:
            _fatal_root(state.root)
            raise OSError(
                "expert_reader_close_uncertain"
            ) from None


def revoke_expert_reader(reader: ExpertJournalReadCapabilityV1) -> None:
    close_expert_reader(reader)


def issue_expert_purge_capability(
    authority: ExpertJournalRootAuthorityV1,
    manifest: ExpertSessionManifestV1,
) -> ExpertJournalPurgeCapabilityV1:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    with _LOCK:
        root = _require_root(authority)
        token = _token(ExpertJournalPurgeCapabilityV1)
        _PURGES[token] = _Purge(root, manifest)
        return token


def _invalidate_session_descendants(
    root: _Root,
    session_id: str,
    *,
    preserve_replay: dict[str, object] | None = None,
) -> None:
    close_uncertain = False
    for writer in _WRITERS.values():
        if (
            writer.root is root
            and writer.manifest.session_id == session_id
        ):
            descriptors = (writer.reserve_fd, writer.journal_fd)
            writer.reserve_fd = -1
            writer.journal_fd = -1
            for descriptor in descriptors:
                if descriptor < 0:
                    continue
                try:
                    os.close(descriptor)
                except OSError:
                    close_uncertain = True
            writer.state = "poisoned"
    for reader in _READERS.values():
        if (
            reader.root is root
            and reader.manifest.session_id == session_id
            and not reader.closed
        ):
            descriptor = reader.fd
            reader.fd = -1
            reader.closed = True
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    close_uncertain = True
    for replay in _REPLAYS.values():
        if replay is preserve_replay:
            continue
        manifest = replay.get("manifest")
        if (
            replay.get("root") is not root
            or not isinstance(manifest, SessionManifest)
            or manifest.session_id != session_id
            or replay.get("closed")
        ):
            continue
        try:
            _close_replay_owned_readers(replay)
        except _ReplayCloseUncertain:
            close_uncertain = True
        finally:
            _purge_replay_denial_payloads(replay)
            replay.pop("denial", None)
            replay["closed"] = True
            replay["state"] = "aborted_closed"
    if close_uncertain:
        if root.active:
            _fatal_root(root)
        raise OSError("expert_session_invalidation_close_uncertain") from None


def _purge_names(
    root: _Root,
    session_id: str,
    *,
    preserve_replay: dict[str, object] | None = None,
) -> None:
    _invalidate_session_descendants(
        root,
        session_id,
        preserve_replay=preserve_replay,
    )
    _unlink_if_present(root.sessions_fd, _journal_basename(session_id))
    _unlink_if_present(root.sessions_fd, _reserve_basename(session_id))
    os.fsync(root.sessions_fd)
    _unlink_if_present(root.markers_fd, _marker_basename(session_id))
    os.fsync(root.markers_fd)
    try:
        root.coordinator.recover_and_purge()
    except Exception:
        pass


def purge_expert_session(
    capability: ExpertJournalPurgeCapabilityV1,
) -> None:
    if type(capability) is not ExpertJournalPurgeCapabilityV1:
        raise TypeError("capability")
    with _LOCK:
        state = _PURGES.pop(capability, None)
        if state is None or state.consumed:
            raise ValueError("expert_purge_capability_invalid")
        state.consumed = True
        _require_root(state.root.token)
        try:
            _purge_names(state.root, state.manifest.session_id)
        except OSError:
            raise OSError("expert_journal_durability_failed") from None


def abort_expert_writer(
    writer: ExpertJournalWriteCapabilityV1,
) -> None:
    if type(writer) is not ExpertJournalWriteCapabilityV1:
        raise TypeError("writer")
    with _LOCK:
        state = _WRITERS.get(writer)
        if state is None:
            raise ValueError("expert_writer_invalid")
        _close_quietly(state.reserve_fd)
        state.reserve_fd = -1
        _close_quietly(state.journal_fd)
        state.journal_fd = -1
        state.state = "poisoned"
        _purge_names(state.root, state.manifest.session_id)


def _validate_phase1_evidence_binding(
    root: _Root,
    expert_marker: dict[str, object],
) -> None:
    session_id = expert_marker["session_id"]
    marker_name = f"{session_id}.marker.json"
    wal_name = f"{session_id}.wal"
    marker_fd = os.open(
        marker_name,
        _OPEN_FILE_READ_FLAGS,
        dir_fd=root.evidence_markers_fd,
    )
    wal_fd = os.open(
        wal_name,
        _OPEN_FILE_READ_FLAGS,
        dir_fd=root.evidence_sessions_fd,
    )
    try:
        marker_stat = _require_file(marker_fd)
        marker_named = os.stat(
            marker_name,
            dir_fd=root.evidence_markers_fd,
            follow_symlinks=False,
        )
        wal_stat = _require_file(wal_fd)
        wal_named = os.stat(
            wal_name,
            dir_fd=root.evidence_sessions_fd,
            follow_symlinks=False,
        )
        if (
            not _same_file_identity(marker_stat, marker_named)
            or not _same_file_identity(wal_stat, wal_named)
            or marker_stat.st_size > 16_384
        ):
            raise ValueError
        marker_content = _pread_exact(marker_fd, 0, marker_stat.st_size)
        marker_raw = json.loads(marker_content.decode("ascii"))
        phase1_marker = RetentionMarker(**marker_raw)
        if canonical_json_bytes(marker_raw) != marker_content:
            raise ValueError
        header = _pread_exact(wal_fd, 0, 16)
        if header != struct.pack(">8sHHI", b"INCIWAL\x00", 1, 0, 16):
            raise ValueError
        prefix = _pread_exact(wal_fd, 16, 32)
        (
            magic,
            version,
            numeric_kind,
            flags,
            ingest_seq,
            total,
            metadata_length,
            payload_length,
        ) = struct.unpack(">4sBBHQQII", prefix)
        fixed = 32 + 32 + 12
        if (
            magic != b"EVT1"
            or version != 1
            or numeric_kind != 3
            or flags != 0
            or ingest_seq != 1
            or total != fixed + metadata_length + payload_length
            or total > 16_777_216
        ):
            raise ValueError
        frame = _pread_exact(wal_fd, 16, total)
        if len(frame) != total:
            raise ValueError
        metadata_end = 32 + metadata_length
        payload_end = metadata_end + payload_length
        metadata = frame[32:metadata_end]
        payload = frame[metadata_end:payload_end]
        digest = frame[payload_end : payload_end + 32]
        repeated, trailer_magic = struct.unpack(
            ">Q4s",
            frame[payload_end + 32 :],
        )
        if (
            repeated != total
            or trailer_magic != b"1TVE"
            or digest
            != sha256(
                b"INCI-FRAME-V1\0" + prefix + metadata + payload
            ).digest()
        ):
            raise ValueError
        start = decode_record(metadata, payload)
        phase1_manifest = SessionManifest(
            **json.loads(start.payload.decode("ascii"))
        )
        if (
            start.record_kind is not RecordKind.CONTROL
            or start.event_type != "SESSION_START"
            or start.ingest_seq != 1
            or start.session_id != session_id
            or phase1_marker.session_id != session_id
            or phase1_marker.wal_basename != wal_name
            or phase1_marker.session_manifest_sha256
            != expert_marker["evidence_session_manifest_sha256"]
            or phase1_marker.provider_request_binding_sha256
            != expert_marker["provider_request_binding_sha256"]
            or session_manifest_sha256(phase1_manifest)
            != expert_marker["evidence_session_manifest_sha256"]
            or canonical_record_sha256(start)
            != expert_marker["evidence_session_start_record_sha256"]
        ):
            raise ValueError
        marker_after = os.fstat(marker_fd)
        wal_after = os.fstat(wal_fd)
        if (
            not _same_file_identity(marker_stat, marker_after)
            or not _same_file_identity(wal_stat, wal_after)
        ):
            raise ValueError
    finally:
        os.close(marker_fd)
        os.close(wal_fd)


def recover_and_purge_expert_journals(
    authority: ExpertJournalRootAuthorityV1,
) -> ExpertPurgeReportV1:
    with _LOCK:
        root = _require_root(authority)
        try:
            marker_names = sorted(os.listdir(root.markers_fd))
            session_names = sorted(os.listdir(root.sessions_fd))
        except OSError:
            _fatal_root(root)
            raise ValueError("expert_recovery_inventory_invalid") from None
        due: list[str] = []
        evidence_missing: list[str] = []
        evidence_replaced: list[str] = []
        recovered: list[str] = []
        marker_sessions: set[str] = set()
        now = _phase1_sample_wall_ns(root.clock_capability)
        for name in marker_names:
            if not name.endswith(_MARKER_SUFFIX):
                raise ValueError("expert_recovery_inventory_invalid")
            session_id = name[: -len(_MARKER_SUFFIX)]
            marker_sessions.add(session_id)
            try:
                marker = _decode_expert_marker(
                    _read_named_content(root.markers_fd, name, 16_384)
                )
                if marker["session_id"] != session_id:
                    raise ValueError
            except Exception:
                evidence_replaced.append(session_id)
                _purge_names(root, session_id)
                continue
            journal_name = _journal_basename(session_id)
            has_journal = journal_name in session_names
            if not has_journal:
                recovered.append(session_id)
                _purge_names(root, session_id)
                continue
            if now >= marker["retention_delete_by_ns"]:
                due.append(session_id)
                _purge_names(root, session_id)
                continue
            try:
                _validate_phase1_evidence_binding(root, marker)
            except FileNotFoundError:
                evidence_missing.append(session_id)
                _purge_names(root, session_id)
                continue
            except Exception:
                evidence_replaced.append(session_id)
                _purge_names(root, session_id)
                continue
            # Recovery is diagnostic cleanup, never an adoption path.  An
            # intact marker/journal pair still belongs to a prior process;
            # even a fully fsynced terminal cannot prove that its caller
            # received the completion result before the crash.
            recovered.append(session_id)
            _purge_names(root, session_id)
        for name in session_names:
            if name.endswith(_SESSION_SUFFIX):
                session_id = name[: -len(_SESSION_SUFFIX)]
            elif name.endswith(_RESERVE_SUFFIX):
                session_id = name[: -len(_RESERVE_SUFFIX)]
            else:
                raise ValueError("expert_recovery_inventory_invalid")
            if session_id not in marker_sessions:
                recovered.append(session_id)
                _purge_names(root, session_id)
        return ExpertPurgeReportV1(
            tuple(sorted(set(due))),
            tuple(sorted(set(evidence_missing))),
            tuple(sorted(set(evidence_replaced))),
            tuple(sorted(set(recovered))),
        )


def _identity(
    *,
    fd: int,
    role: str,
    marker_digest: str | None,
    header_digest: str | None,
    session_anchor: str,
    value: os.stat_result | None = None,
) -> ExpertPhysicalFileIdentityV1:
    if value is None:
        value = _require_file(fd)
    fields: dict[str, object] = {
        "schema_version": 1,
        "role": role,
        "device": value.st_dev,
        "inode": value.st_ino,
        "uid": value.st_uid,
        "mode": _mode(value),
        "link_count": value.st_nlink,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
        "canonical_marker_sha256": marker_digest,
        "file_header_sha256": header_digest,
        "session_anchor_sha256": session_anchor,
    }
    return _create_expert_physical_file_identity_v1(
        **fields,
        identity_sha256=compute_expert_physical_file_identity_sha256(
            **fields
        ),
    )


def inspect_phase1_evidence_file_identities(
    authority: ExpertJournalRootAuthorityV1,
    *,
    session_manifest: SessionManifest,
    session_start: PersistedEvent,
) -> tuple[ExpertPhysicalFileIdentityV1, ExpertPhysicalFileIdentityV1]:
    if (
        type(session_manifest) is not SessionManifest
        or type(session_start) is not PersistedEvent
    ):
        raise TypeError("phase1_identity_arguments")
    with _LOCK:
        root = _require_root(authority)
        manifest_digest = session_manifest_sha256(session_manifest)
        start_digest = canonical_record_sha256(session_start)
        if (
            session_start.record_kind is not RecordKind.CONTROL
            or session_start.event_type != "SESSION_START"
            or session_start.session_id != session_manifest.session_id
        ):
            raise ValueError("phase1_session_start_invalid")
        anchor = sha256(
            b"INCI-EXPERT-PHASE1-SESSION-ANCHOR-V1\0"
            + canonical_expert_bytes((manifest_digest, start_digest))
        ).hexdigest()
        marker_name = f"{session_manifest.session_id}.marker.json"
        wal_name = f"{session_manifest.session_id}.wal"
        marker_fd = os.open(
            marker_name,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.evidence_markers_fd,
        )
        wal_fd = os.open(
            wal_name,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.evidence_sessions_fd,
        )
        try:
            marker_size = _require_file(marker_fd).st_size
            if marker_size > 16_384:
                raise ValueError("phase1_marker_oversized")
            marker, marker_stat = _stable_named_file_read(
                fd=marker_fd,
                directory_fd=root.evidence_markers_fd,
                basename=marker_name,
                offset=0,
                length=marker_size,
            )
            header, wal_stat = _stable_named_file_read(
                fd=wal_fd,
                directory_fd=root.evidence_sessions_fd,
                basename=wal_name,
                offset=0,
                length=16,
            )
            return (
                _identity(
                    fd=marker_fd,
                    role="phase1_marker",
                    marker_digest=sha256(marker).hexdigest(),
                    header_digest=None,
                    session_anchor=anchor,
                    value=marker_stat,
                ),
                _identity(
                    fd=wal_fd,
                    role="phase1_wal",
                    marker_digest=None,
                    header_digest=sha256(header).hexdigest(),
                    session_anchor=anchor,
                    value=wal_stat,
                ),
            )
        finally:
            os.close(marker_fd)
            os.close(wal_fd)


def inspect_expert_companion_file_identities(
    authority: ExpertJournalRootAuthorityV1,
    *,
    manifest: ExpertSessionManifestV1,
) -> tuple[ExpertPhysicalFileIdentityV1, ExpertPhysicalFileIdentityV1]:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    with _LOCK:
        root = _require_root(authority)
        marker_fd = os.open(
            _marker_basename(manifest.session_id),
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.markers_fd,
        )
        journal_fd = os.open(
            _journal_basename(manifest.session_id),
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.sessions_fd,
        )
        try:
            marker_size = _require_file(marker_fd).st_size
            if marker_size > 16_384:
                raise ValueError("expert_marker_oversized")
            marker, marker_stat = _stable_named_file_read(
                fd=marker_fd,
                directory_fd=root.markers_fd,
                basename=_marker_basename(manifest.session_id),
                offset=0,
                length=marker_size,
            )
            header, _ = _stable_named_file_read(
                fd=journal_fd,
                directory_fd=root.sessions_fd,
                basename=_journal_basename(manifest.session_id),
                offset=0,
                length=EXPERT_FILE_HEADER_BYTES,
            )
            prefix, _ = _stable_named_file_read(
                fd=journal_fd,
                directory_fd=root.sessions_fd,
                basename=_journal_basename(manifest.session_id),
                offset=EXPERT_FILE_HEADER_BYTES,
                length=EXPERT_FRAME_PREFIX_BYTES,
            )
            if len(prefix) != EXPERT_FRAME_PREFIX_BYTES:
                raise ValueError("expert_manifest_missing")
            _, _, total, _, _ = decode_expert_frame_prefix(prefix)
            frame, journal_stat = _stable_named_file_read(
                fd=journal_fd,
                directory_fd=root.sessions_fd,
                basename=_journal_basename(manifest.session_id),
                offset=EXPERT_FILE_HEADER_BYTES,
                length=total,
            )
            if len(frame) != total:
                raise ValueError("expert_manifest_missing")
            decoded = decode_expert_manifest_frame(frame)
            if decoded != manifest:
                raise ValueError("expert_manifest_mismatch")
            frame_digest = decode_expert_complete_frame(frame)[4]
            anchor = sha256(
                b"INCI-EXPERT-COMPANION-SESSION-ANCHOR-V1\0"
                + canonical_expert_bytes(
                    (manifest.manifest_sha256, frame_digest)
                )
            ).hexdigest()
            return (
                _identity(
                    fd=marker_fd,
                    role="expert_marker",
                    marker_digest=sha256(marker).hexdigest(),
                    header_digest=None,
                    session_anchor=anchor,
                    value=marker_stat,
                ),
                _identity(
                    fd=journal_fd,
                    role="expert_journal",
                    marker_digest=None,
                    header_digest=sha256(header).hexdigest(),
                    session_anchor=anchor,
                    value=journal_stat,
                ),
            )
        finally:
            os.close(marker_fd)
            os.close(journal_fd)


class _ReplayIdentityFailure(RuntimeError):
    def __init__(self, role: ExpertReplayDiagnosticRoleV1) -> None:
        super().__init__("expert_replay_identity_invalid")
        self.role = role


def _guarded_stable_named_file_read(
    *,
    fd: int,
    directory_fd: int,
    basename: str,
    offset: int,
    length: int,
    gate: Callable[[], None],
    byte_gate: Callable[[], None] | None = None,
) -> tuple[bytes, os.stat_result]:
    governed_byte_gate = gate if byte_gate is None else byte_gate
    gate()
    before = _require_file(fd)
    gate()
    named_before = os.stat(
        basename,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    gate()
    if not _same_file_identity(before, named_before):
        raise ValueError("expert_named_read_identity_invalid")
    content = _gated_pread_exact(
        fd,
        offset,
        length,
        gate=governed_byte_gate,
    )
    gate()
    after = _require_file(fd)
    gate()
    named_after = os.stat(
        basename,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    gate()
    if (
        not _same_file_identity(before, after)
        or not _same_file_identity(after, named_after)
    ):
        raise ValueError("expert_named_read_identity_invalid")
    return content, after


def _guarded_phase1_evidence_file_identities(
    root: _Root,
    *,
    session_manifest: SessionManifest,
    session_start: PersistedEvent,
    gate: Callable[[], None],
    byte_gate: Callable[[], None] | None = None,
) -> tuple[ExpertPhysicalFileIdentityV1, ExpertPhysicalFileIdentityV1]:
    manifest_digest = session_manifest_sha256(session_manifest)
    start_digest = canonical_record_sha256(session_start)
    if (
        session_start.record_kind is not RecordKind.CONTROL
        or session_start.event_type != "SESSION_START"
        or session_start.session_id != session_manifest.session_id
    ):
        raise ValueError("phase1_session_start_invalid")
    anchor = sha256(
        b"INCI-EXPERT-PHASE1-SESSION-ANCHOR-V1\0"
        + canonical_expert_bytes((manifest_digest, start_digest))
    ).hexdigest()
    marker_name = f"{session_manifest.session_id}.marker.json"
    wal_name = f"{session_manifest.session_id}.wal"

    marker_fd = -1
    try:
        gate()
        marker_fd = os.open(
            marker_name,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.evidence_markers_fd,
        )
        gate()
        marker_size = _require_file(marker_fd).st_size
        gate()
        if marker_size > 16_384:
            raise ValueError("phase1_marker_oversized")
        marker, marker_stat = _guarded_stable_named_file_read(
            fd=marker_fd,
            directory_fd=root.evidence_markers_fd,
            basename=marker_name,
            offset=0,
            length=marker_size,
            gate=gate,
            byte_gate=byte_gate,
        )
    except (ExpertReplayAccessDenied, _PrepareReplayDenied):
        raise
    except OSError:
        raise
    except Exception:
        raise _ReplayIdentityFailure(
            ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
        ) from None
    finally:
        _close_root_governed_temporary(
            root,
            marker_fd,
            message="expert_replay_close_uncertain",
        )

    wal_fd = -1
    try:
        gate()
        wal_fd = os.open(
            wal_name,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.evidence_sessions_fd,
        )
        gate()
        header, wal_stat = _guarded_stable_named_file_read(
            fd=wal_fd,
            directory_fd=root.evidence_sessions_fd,
            basename=wal_name,
            offset=0,
            length=16,
            gate=gate,
            byte_gate=byte_gate,
        )
    except (ExpertReplayAccessDenied, _PrepareReplayDenied):
        raise
    except OSError:
        raise
    except Exception:
        raise _ReplayIdentityFailure(
            ExpertReplayDiagnosticRoleV1.PHASE1_WAL
        ) from None
    finally:
        _close_root_governed_temporary(
            root,
            wal_fd,
            message="expert_replay_close_uncertain",
        )

    return (
        _identity(
            fd=-1,
            role="phase1_marker",
            marker_digest=sha256(marker).hexdigest(),
            header_digest=None,
            session_anchor=anchor,
            value=marker_stat,
        ),
        _identity(
            fd=-1,
            role="phase1_wal",
            marker_digest=None,
            header_digest=sha256(header).hexdigest(),
            session_anchor=anchor,
            value=wal_stat,
        ),
    )


def _guarded_expert_companion_file_identities(
    root: _Root,
    *,
    manifest: ExpertSessionManifestV1,
    gate: Callable[[], None],
    byte_gate: Callable[[], None] | None = None,
) -> tuple[ExpertPhysicalFileIdentityV1, ExpertPhysicalFileIdentityV1]:
    marker_name = _marker_basename(manifest.session_id)
    journal_name = _journal_basename(manifest.session_id)
    marker_fd = -1
    try:
        gate()
        marker_fd = os.open(
            marker_name,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.markers_fd,
        )
        gate()
        marker_size = _require_file(marker_fd).st_size
        gate()
        if marker_size > 16_384:
            raise ValueError("expert_marker_oversized")
        marker, marker_stat = _guarded_stable_named_file_read(
            fd=marker_fd,
            directory_fd=root.markers_fd,
            basename=marker_name,
            offset=0,
            length=marker_size,
            gate=gate,
            byte_gate=byte_gate,
        )
    except (ExpertReplayAccessDenied, _PrepareReplayDenied):
        raise
    except OSError:
        raise
    except Exception:
        raise _ReplayIdentityFailure(
            ExpertReplayDiagnosticRoleV1.EXPERT_MARKER
        ) from None
    finally:
        _close_root_governed_temporary(
            root,
            marker_fd,
            message="expert_replay_close_uncertain",
        )

    journal_fd = -1
    try:
        gate()
        journal_fd = os.open(
            journal_name,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=root.sessions_fd,
        )
        gate()
        header, _ = _guarded_stable_named_file_read(
            fd=journal_fd,
            directory_fd=root.sessions_fd,
            basename=journal_name,
            offset=0,
            length=EXPERT_FILE_HEADER_BYTES,
            gate=gate,
            byte_gate=byte_gate,
        )
        prefix, _ = _guarded_stable_named_file_read(
            fd=journal_fd,
            directory_fd=root.sessions_fd,
            basename=journal_name,
            offset=EXPERT_FILE_HEADER_BYTES,
            length=EXPERT_FRAME_PREFIX_BYTES,
            gate=gate,
            byte_gate=byte_gate,
        )
        if len(prefix) != EXPERT_FRAME_PREFIX_BYTES:
            raise ValueError("expert_manifest_missing")
        _, _, total, _, _ = decode_expert_frame_prefix(prefix)
        frame, journal_stat = _guarded_stable_named_file_read(
            fd=journal_fd,
            directory_fd=root.sessions_fd,
            basename=journal_name,
            offset=EXPERT_FILE_HEADER_BYTES,
            length=total,
            gate=gate,
            byte_gate=byte_gate,
        )
        if len(frame) != total:
            raise ValueError("expert_manifest_missing")
        decoded = decode_expert_manifest_frame(frame)
        if decoded != manifest:
            raise ValueError("expert_manifest_mismatch")
        frame_digest = decode_expert_complete_frame(frame)[4]
    except (ExpertReplayAccessDenied, _PrepareReplayDenied):
        raise
    except OSError:
        raise
    except Exception:
        raise _ReplayIdentityFailure(
            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL
        ) from None
    finally:
        _close_root_governed_temporary(
            root,
            journal_fd,
            message="expert_replay_close_uncertain",
        )

    anchor = sha256(
        b"INCI-EXPERT-COMPANION-SESSION-ANCHOR-V1\0"
        + canonical_expert_bytes(
            (manifest.manifest_sha256, frame_digest)
        )
    ).hexdigest()
    return (
        _identity(
            fd=-1,
            role="expert_marker",
            marker_digest=sha256(marker).hexdigest(),
            header_digest=None,
            session_anchor=anchor,
            value=marker_stat,
        ),
        _identity(
            fd=-1,
            role="expert_journal",
            marker_digest=None,
            header_digest=sha256(header).hexdigest(),
            session_anchor=anchor,
            value=journal_stat,
        ),
    )


_PHASE1_TERMINAL_FIELDS = (
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
)
_PHASE1_CLEAN_REASONS = frozenset(("operator_stop", "session_end"))
_PHASE1_HALTED_REASONS = frozenset(
    (
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
    )
)


def _decode_phase1_terminal_payload(
    event: PersistedEvent,
    manifest: SessionManifest,
    *,
    raw_count: int,
    derived_count: int,
    last_raw_ingest_seq: int,
) -> dict[str, object]:
    try:
        value = json.loads(
            event.payload.decode("ascii"),
            parse_float=lambda _: (_ for _ in ()).throw(ValueError()),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except Exception:
        raise ValueError("expert_phase1_terminal_invalid") from None
    expected_provenance = {
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
        "permission_artifact_sha256": manifest.permission_artifact_sha256,
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
    if (
        type(value) is not dict
        or set(value) != set(_PHASE1_TERMINAL_FIELDS)
        or canonical_json_bytes(value) != event.payload
        or event.record_kind is not RecordKind.CONTROL
        or event.event_type != "SESSION_HALT"
        or event.session_id != manifest.session_id
        or event.ingest_seq != 2 + raw_count + derived_count
        or event.local_wall_ns != manifest.created_wall_ns
        or event.local_monotonic_ns != 0
        or value["terminal_version"] != 1
        or type(value["terminal_version"]) is not int
        or type(value["clean"]) is not bool
        or type(value["reason"]) is not str
        or value["record_count_before_terminal"]
        != 1 + raw_count + derived_count
        or value["raw_count"] != raw_count
        or value["derived_count"] != derived_count
        or derived_count != raw_count
        or last_raw_ingest_seq != (0 if raw_count == 0 else 2 * raw_count)
        or type(value["last_applied_raw_seq"]) is not int
        or value["last_applied_raw_seq"] < 0
        or value["last_applied_raw_seq"] > last_raw_ingest_seq
        or value["clean"] is True
        and value["last_applied_raw_seq"] != last_raw_ingest_seq
        or value["reason"]
        not in (
            _PHASE1_CLEAN_REASONS
            if value["clean"]
            else _PHASE1_HALTED_REASONS
        )
        or value["research_evaluable"] is not False
        or any(value[name] != expected for name, expected in expected_provenance.items())
        or any(
            type(value[name]) is not str
            or len(value[name]) != 64
            or any(character not in "0123456789abcdef" for character in value[name])
            for name in ("trace_sha256", "final_state_sha256")
        )
    ):
        raise ValueError("expert_phase1_terminal_invalid")
    return value


def prove_expert_live_evidence_tail(
    writer: ExpertJournalWriteCapabilityV1,
    *,
    published_cursor: ExpertJournalCursorV1,
) -> PersistedEvent | None:
    if type(published_cursor) is not ExpertJournalCursorV1:
        raise TypeError("published_cursor")
    with _LOCK:
        state = _validate_writer(
            writer,
            states=("ordinary_ready",),
            terminal=True,
        )
        if state.tail is not None or published_cursor != state.cursor:
            _poison_writer(state)
            raise ValueError("expert_live_evidence_tail_invalid")
        try:
            phase1_manifest = _require_authorizer(
                state.root,
                state.authorizer,
                state.coordinator,
            )
            sampled = _phase1_sample_wall_ns(state.root.clock_capability)
            if sampled >= state.manifest.retention.retention_delete_by_ns:
                raise ExpertLiveAuthorizationDenied()
            capability = state.coordinator.issue_read_capability(
                persistence_authorizer=state.authorizer
            )
            phase1_reader = JournalReader.open(read_capability=capability)
            companion_reader = issue_expert_read_capability(
                state.root.token,
                state.manifest,
            )
            try:
                records = iter(
                    phase1_reader.iter_records(diagnostic_prefix=True)
                )
                start = next(records)
                if (
                    start.record_kind is not RecordKind.CONTROL
                    or start.event_type != "SESSION_START"
                    or canonical_record_sha256(start)
                    != state.manifest.evidence_session_start_record_sha256
                ):
                    raise ValueError
                phase1_identities = (
                    inspect_phase1_evidence_file_identities(
                        state.root.token,
                        session_manifest=phase1_manifest,
                        session_start=start,
                    )
                )
                companion_identities = (
                    inspect_expert_companion_file_identities(
                        state.root.token,
                        manifest=state.manifest,
                    )
                )
                read_expert_manifest(companion_reader)
                rolling = state.initial_cursor
                raw_count = 0
                derived_count = 0
                terminal: PersistedEvent | None = None
                unseen: PersistedEvent | None = None
                covered_count = 0
                last_raw_ingest_seq = 0
                last_raw: PersistedEvent | None = None
                pending_raw: PersistedEvent | None = None
                for event in records:
                    if terminal is not None:
                        raise ValueError
                    if event.record_kind is RecordKind.RAW:
                        raw_count += 1
                        if (
                            pending_raw is not None
                            or event.ingest_seq != 2 * raw_count
                            or event.parent_ingest_seq is not None
                        ):
                            raise ValueError
                        pending_raw = event
                        last_raw = event
                        last_raw_ingest_seq = event.ingest_seq
                        item = read_next_expert_group(companion_reader)
                        if item is None:
                            if unseen is not None:
                                raise ValueError
                            unseen = event
                            continue
                        group, payloads = item
                        if unseen is not None:
                            raise ValueError
                        validate_expert_group_against_cursor(
                            group,
                            payloads,
                            rolling,
                        )
                        if (
                            group.parent.record_sha256
                            != canonical_record_sha256(event)
                            or group.parent.ingest_seq != event.ingest_seq
                            or group.parent.event_type != event.event_type
                            or group.parent.event_version != event.event_version
                            or group.parent.local_wall_ns != event.local_wall_ns
                            or group.parent.local_monotonic_ns
                            != event.local_monotonic_ns
                            or group.parent.clock_uncertainty_ns
                            != event.clock_uncertainty_ns
                        ):
                            raise ValueError
                        rolling = _candidate_cursor(rolling, group)
                        covered_count += 1
                    elif event.record_kind is RecordKind.DERIVED:
                        derived_count += 1
                        if (
                            pending_raw is None
                            or event.ingest_seq
                            != pending_raw.ingest_seq + 1
                            or event.parent_ingest_seq
                            != pending_raw.ingest_seq
                        ):
                            raise ValueError
                        pending_raw = None
                    elif event.record_kind is RecordKind.CONTROL:
                        if (
                            event.event_type != "SESSION_HALT"
                            or pending_raw is not None
                        ):
                            raise ValueError
                        terminal = event
                    else:
                        raise ValueError
                if terminal is None or pending_raw is not None:
                    raise ValueError
                if read_next_expert_group(companion_reader) is not None:
                    raise ValueError
                companion_terminal, companion_summary = (
                    read_expert_terminal_and_summary(companion_reader)
                )
                if (
                    companion_terminal is not None
                    or companion_summary.issue
                    is not ExpertJournalScanIssueV1.MISSING_TERMINAL
                    or companion_summary.last_good_offset
                    != companion_summary.file_size
                ):
                    raise ValueError
                terminal_payload = _decode_phase1_terminal_payload(
                    terminal,
                    phase1_manifest,
                    raw_count=raw_count,
                    derived_count=derived_count,
                    last_raw_ingest_seq=last_raw_ingest_seq,
                )
                if (
                    inspect_phase1_evidence_file_identities(
                        state.root.token,
                        session_manifest=phase1_manifest,
                        session_start=start,
                    )
                    != phase1_identities
                    or inspect_expert_companion_file_identities(
                        state.root.token,
                        manifest=state.manifest,
                    )
                    != companion_identities
                ):
                    raise ValueError
            finally:
                phase1_reader.close()
                close_expert_reader(companion_reader)
            if (
                covered_count != published_cursor.group_count
                or raw_count - covered_count not in (0, 1)
                or rolling != published_cursor
            ):
                raise ValueError
            state.tail = {
                "terminal": terminal,
                "terminal_payload": terminal_payload,
                "phase1_manifest": phase1_manifest,
                "session_start": start,
                "identities": phase1_identities + companion_identities,
                "raw_count": raw_count,
                "derived_count": derived_count,
                "covered_cursor": published_cursor,
                "unseen": unseen,
                "last_raw": last_raw,
                "consumed": False,
            }
            return unseen
        except ExpertLiveAuthorizationDenied:
            _poison_writer(state)
            raise
        except Exception:
            _poison_writer(state)
            raise ValueError("expert_live_evidence_tail_invalid") from None


def build_aligned_expert_terminal(
    writer: ExpertJournalWriteCapabilityV1,
    *,
    final_state: ExpertStateV1,
    final_cursor: ExpertJournalCursorV1,
) -> tuple[PersistedEvent, ExpertSessionTerminalV1]:
    if (
        type(final_state) is not ExpertStateV1
        or type(final_cursor) is not ExpertJournalCursorV1
    ):
        raise TypeError("expert_terminal_arguments")
    with _LOCK:
        state = _validate_writer(
            writer,
            states=("ordinary_ready",),
            terminal=True,
        )
        tail = state.tail
        if type(tail) is not dict or tail.get("consumed") is True:
            _poison_writer(state)
            raise ValueError("expert_terminal_alignment_invalid")
        _terminal_material_gate(state, tail)
        evidence_terminal = tail["terminal"]
        unseen = tail["unseen"]
        ordinary_ack_cursor = tail.get("ordinary_ack_cursor")
        covered_cursor = tail["covered_cursor"]
        last_raw = tail.get("last_raw")
        cursor_aligned = (
            unseen is None
            and ordinary_ack_cursor is None
            and final_cursor == covered_cursor
            and state.cursor == covered_cursor
            or type(unseen) is PersistedEvent
            and type(ordinary_ack_cursor) is ExpertJournalCursorV1
            and final_cursor == ordinary_ack_cursor
            and state.cursor == ordinary_ack_cursor
            or type(unseen) is PersistedEvent
            and ordinary_ack_cursor is None
            and state.cursor == covered_cursor
            and final_cursor.group_count == covered_cursor.group_count + 1
        )
        if (
            type(evidence_terminal) is not PersistedEvent
            or final_state.session_id != state.manifest.session_id
            or final_cursor.session_id != state.manifest.session_id
            or expert_state_sha256(final_state)
            != final_cursor.expert_state_sha256
            or not cursor_aligned
            or final_cursor.group_count != tail["raw_count"]
            or tail["raw_count"] == 0
            and (
                final_cursor.last_parent_ingest_seq != 0
                or final_cursor.last_parent_record_sha256
                != state.manifest.evidence_session_start_record_sha256
            )
            or tail["raw_count"] != 0
            and (
                type(last_raw) is not PersistedEvent
                or final_cursor.last_parent_ingest_seq != last_raw.ingest_seq
                or final_cursor.last_parent_record_sha256
                != canonical_record_sha256(last_raw)
            )
        ):
            _poison_writer(state)
            raise ValueError("expert_terminal_alignment_invalid")
        try:
            payload = tail["terminal_payload"]
            if type(payload) is not dict:
                raise ValueError
            clean = payload["clean"]
            reason = payload["reason"]
            if type(clean) is not bool or type(reason) is not str:
                raise ValueError
            from inci_tennis_expert.contracts import ExpertTerminalReasonV1

            expert_reason = (
                ExpertTerminalReasonV1.OPERATOR_STOP
                if clean and reason == "operator_stop"
                else ExpertTerminalReasonV1.SESSION_END
                if clean and reason == "session_end"
                else ExpertTerminalReasonV1.EXPERT_HALT
            )
            values: dict[str, object] = {
                "schema_version": 1,
                "session_id": state.manifest.session_id,
                "expert_manifest_sha256": state.manifest.manifest_sha256,
                "provider_request_binding_sha256": (
                    state.manifest.provider_request_binding_sha256
                ),
                "match_binding_universe_sha256": (
                    state.manifest.match_binding_universe_sha256
                ),
                "retention_binding_sha256": (
                    state.manifest.retention.retention_binding_sha256
                ),
                "evidence_terminal_ingest_seq": evidence_terminal.ingest_seq,
                "evidence_terminal_record_sha256": (
                    canonical_record_sha256(evidence_terminal)
                ),
                "evidence_terminal_clean": clean,
                "evidence_terminal_reason": reason,
                "evidence_raw_count": tail["raw_count"],
                "evidence_derived_count": tail["derived_count"],
                "expert_group_count": final_cursor.group_count,
                "expert_record_count": final_cursor.record_count,
                "last_parent_ingest_seq": (
                    final_cursor.last_parent_ingest_seq
                ),
                "last_parent_record_sha256": (
                    final_cursor.last_parent_record_sha256
                ),
                "final_expert_seq": final_cursor.expert_seq,
                "final_expert_record_sha256": (
                    final_cursor.expert_record_sha256
                ),
                "final_expert_state_sha256": (
                    final_cursor.expert_state_sha256
                ),
                "final_expert_trace_sha256": (
                    final_cursor.expert_trace_sha256
                ),
                "clean": clean,
                "reason": expert_reason,
                "research_evaluable": False,
            }
            terminal = ExpertSessionTerminalV1(
                **values,
                terminal_sha256=compute_expert_session_terminal_sha256(
                    **values
                ),
            )
            validate_expert_terminal_against_cursor(terminal, final_cursor)
        except Exception:
            _poison_writer(state)
            raise ValueError("expert_terminal_alignment_invalid") from None
        tail["consumed"] = True
        state.terminal_pair = (evidence_terminal, terminal)
        return evidence_terminal, terminal


def _deadline_denial(
    *,
    session_id: str,
    deadline: int,
    sampled: int,
) -> ExpertReplayDeniedV1:
    result = ExpertReplayResultV1(
        state=None,
        trace_sha256=None,
        evidence_raw_count=0,
        evidence_derived_count=0,
        expert_group_count=0,
        expert_record_count=0,
        evidence_exact=False,
        companion_valid=False,
        terminals_aligned=False,
        exact_replay=False,
        mismatch=ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
        final_authorization_sha256=None,
        evaluation_input_eligible=False,
        research_evaluable=False,
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": session_id,
        "mismatch": ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
        "phase1_replay_summary_sha256": None,
        "file_proofs": (),
        "companion_scan": None,
        "common_deadline_ns": deadline,
        "final_sampled_wall_ns": sampled,
        "acknowledged_parent_count": 0,
        "acknowledged_expert_record_count": 0,
    }
    proof = _create_expert_replay_diagnostic_proof_v1(
        **values,
        proof_sha256=compute_expert_replay_diagnostic_proof_sha256(
            **values
        ),
    )
    return _create_expert_replay_denied_v1(result=result, proof=proof)


def _authorization_denial(
    *,
    session_id: str,
    deadline: int,
    sampled: int,
    mismatch: ExpertReplayMismatchV1 = (
        ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
    ),
) -> ExpertReplayDeniedV1:
    result = ExpertReplayResultV1(
        state=None,
        trace_sha256=None,
        evidence_raw_count=0,
        evidence_derived_count=0,
        expert_group_count=0,
        expert_record_count=0,
        evidence_exact=False,
        companion_valid=False,
        terminals_aligned=False,
        exact_replay=False,
        mismatch=mismatch,
        final_authorization_sha256=None,
        evaluation_input_eligible=False,
        research_evaluable=False,
    )
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": session_id,
        "mismatch": mismatch,
        "phase1_replay_summary_sha256": None,
        "file_proofs": (),
        "companion_scan": None,
        "common_deadline_ns": deadline,
        "final_sampled_wall_ns": sampled,
        "acknowledged_parent_count": 0,
        "acknowledged_expert_record_count": 0,
    }
    proof = _create_expert_replay_diagnostic_proof_v1(
        **values,
        proof_sha256=compute_expert_replay_diagnostic_proof_sha256(
            **values
        ),
    )
    return _create_expert_replay_denied_v1(result=result, proof=proof)


def _contextual_replay_denial(
    state: dict[str, object],
    *,
    mismatch: ExpertReplayMismatchV1,
    sampled: int,
    file_proofs: tuple[object, ...] = (),
    companion_scan: ExpertJournalScanSummaryV1 | None = None,
) -> ExpertReplayDeniedV1:
    phase1_result = state.get("phase1_replay_result")
    evidence = state.get("evidence")
    if phase1_result is None and evidence is not None:
        phase1_result = getattr(evidence, "replay_result", None)
    phase1_summary = None
    evidence_raw_count = 0
    evidence_derived_count = 0
    evidence_exact = False
    if phase1_result is not None:
        phase1_summary = expert_phase1_replay_summary_sha256(phase1_result)
        evidence_raw_count = phase1_result.raw_count
        evidence_derived_count = phase1_result.derived_count
        evidence_exact = phase1_result.exact_replay

    acknowledged_parent_count = 0
    acknowledged_expert_record_count = 0
    accumulator = state.get("accumulator")
    if type(accumulator) is ExpertReplayAccumulatorV1:
        acknowledged_parent_count = accumulator.cursor.group_count
        acknowledged_expert_record_count = accumulator.cursor.record_count

    deadline = state["deadline"]
    preserved_mismatch = (
        accumulator.mismatch
        if (
            type(accumulator) is ExpertReplayAccumulatorV1
            and accumulator.mismatch
            in {
                ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
                ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
                ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
                ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
            }
        )
        else None
    )
    proven_mismatch = state.get("proven_mismatch")
    proven_sampled = state.get("proven_mismatch_sampled")
    if (
        type(proven_mismatch) is ExpertReplayMismatchV1
        and proven_mismatch is ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
        and type(proven_sampled) is int
    ):
        mismatch = proven_mismatch
        sampled = proven_sampled
        file_proofs = ()
    elif preserved_mismatch is not None:
        mismatch = preserved_mismatch
        file_proofs = ()
    elif sampled >= deadline:
        mismatch = ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
        file_proofs = ()
    result = ExpertReplayResultV1(
        state=None,
        trace_sha256=None,
        evidence_raw_count=evidence_raw_count,
        evidence_derived_count=evidence_derived_count,
        expert_group_count=acknowledged_parent_count,
        expert_record_count=acknowledged_expert_record_count,
        evidence_exact=evidence_exact,
        companion_valid=False,
        terminals_aligned=False,
        exact_replay=False,
        mismatch=mismatch,
        final_authorization_sha256=None,
        evaluation_input_eligible=False,
        research_evaluable=False,
    )
    manifest = state.get("manifest", state.get("expert_manifest"))
    if manifest is None:
        raise ValueError("expert_replay_manifest_missing")
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "mismatch": mismatch,
        "phase1_replay_summary_sha256": phase1_summary,
        "file_proofs": file_proofs,
        "companion_scan": (
            companion_scan if phase1_summary is not None else None
        ),
        "common_deadline_ns": deadline,
        "final_sampled_wall_ns": sampled,
        "acknowledged_parent_count": acknowledged_parent_count,
        "acknowledged_expert_record_count": (
            acknowledged_expert_record_count
        ),
    }
    proof = _create_expert_replay_diagnostic_proof_v1(
        **values,
        proof_sha256=compute_expert_replay_diagnostic_proof_sha256(
            **values
        ),
    )
    return _create_expert_replay_denied_v1(result=result, proof=proof)


def _purge_replay_denial_payloads(state: dict[str, object]) -> None:
    for name in (
        "accumulator",
        "cardinality_side",
        "current_group",
        "current_group_sha256",
        "current_parent",
        "current_parent_record_sha256",
        "current_payload_seals",
        "evidence",
        "evidence_terminal",
        "expected_environment",
        "expert_manifest",
        "finish_material",
        "identity_set",
        "last_evidence_parent_ingest_seq",
        "last_phase1_ingest_seq",
        "outstanding_authorization_sha256",
        "phase1_bootstrap_identities",
        "phase1_physical_eof",
        "phase1_records",
        "phase1_replay_result",
        "phase1_terminal_seen",
        "prepare_companion_journal_identity",
        "prepare_companion_marker_identity",
        "prepare_expected_environment",
        "proven_mismatch",
        "proven_mismatch_sampled",
    ):
        state.pop(name, None)
    state["outstanding"] = None


def _pop_replay_pair(state: dict[str, object]) -> None:
    for name in (
        "current_group",
        "current_group_sha256",
        "current_parent",
        "current_parent_record_sha256",
        "current_payload_seals",
    ):
        state.pop(name, None)


def _replay_parent_record_sha256(parent: object) -> str:
    if type(parent) is not PersistedEvent:
        raise ValueError("expert_replay_authority_invalid")
    PersistedEvent.__post_init__(parent)
    return canonical_record_sha256(parent)


def _replay_group_seals(
    item: object,
) -> tuple[str, tuple[tuple[int, str], ...]]:
    if type(item) is not tuple or len(item) != 2:
        raise ValueError("expert_replay_authority_invalid")
    group, payloads = item
    if type(group) is not ExpertJournalGroupV1:
        raise ValueError("expert_replay_authority_invalid")
    ExpertJournalGroupV1.__post_init__(group)
    if (
        type(payloads) is not tuple
        or not payloads
        or any(type(payload) is not bytes for payload in payloads)
    ):
        raise ValueError("expert_replay_authority_invalid")
    return (
        group.group_sha256,
        tuple(
            (len(payload), sha256(payload).hexdigest())
            for payload in payloads
        ),
    )


def _validated_replay_parent_snapshot(
    state: dict[str, object],
) -> PersistedEvent:
    snapshot = deepcopy(state.get("current_parent"))
    if (
        _replay_parent_record_sha256(snapshot)
        != state.get("current_parent_record_sha256")
    ):
        raise ValueError("expert_replay_authority_invalid")
    return snapshot


def _validated_replay_pair_snapshots(
    state: dict[str, object],
) -> tuple[
    PersistedEvent,
    tuple[ExpertJournalGroupV1, tuple[bytes, ...]],
]:
    parent = _validated_replay_parent_snapshot(state)
    item = deepcopy(state.get("current_group"))
    group_sha256, payload_seals = _replay_group_seals(item)
    if (
        group_sha256 != state.get("current_group_sha256")
        or payload_seals != state.get("current_payload_seals")
    ):
        raise ValueError("expert_replay_authority_invalid")
    assert type(item) is tuple
    return parent, item


def _close_replay_owned_readers(state: dict[str, object]) -> None:
    state.pop("outstanding_authorization_sha256", None)
    _pop_replay_pair(state)
    close_uncertain = False
    companion = state.pop("companion_reader", None)
    if type(companion) is ExpertJournalReadCapabilityV1:
        companion_state = _READERS.pop(companion, None)
        if type(companion_state) is _Reader:
            if not companion_state.closed:
                companion_state.closed = True
                descriptor = companion_state.fd
                companion_state.fd = -1
                try:
                    os.close(descriptor)
                except OSError:
                    close_uncertain = True
        else:
            root = state.get("root")
            if (
                not state.get("close_uncertain")
                and (type(root) is not _Root or root.active)
            ):
                try:
                    revoke_expert_reader(companion)
                except Exception:
                    close_uncertain = True
    phase1_reader = state.pop("phase1_reader", None)
    state.pop("phase1_records", None)
    root = state.get("root")
    if type(phase1_reader) is JournalReader:
        try:
            phase1_reader.close()
        except RetentionDueDeleteError as error:
            if type(error) is not RetentionDueDeleteError:
                close_uncertain = True
        except Exception:
            close_uncertain = True
    if close_uncertain:
        state["close_uncertain"] = True
        if type(root) is _Root and root.active:
            _fatal_root(root)
        raise _ReplayCloseUncertain(
            "expert_replay_close_uncertain"
        )


def _raise_replay_operational_read_failure(
    state: dict[str, object],
) -> None:
    # A final governed observation may replace the I/O failure with the
    # higher-precedence deadline, authorization, or identity denial.
    _replay_full_integrity_gate(state)
    _raise_replay_operational_read_failure_after_gate(state)


def _raise_replay_operational_read_failure_after_gate(
    state: dict[str, object],
) -> None:
    """Abort after an operational read failure once the final gate ran."""
    try:
        _close_replay_owned_readers(state)
    except _ReplayCloseUncertain:
        state["outstanding"] = None
        state["closed"] = True
        state["state"] = "aborted_closed"
        raise OSError("expert_replay_close_uncertain") from None
    state["outstanding"] = None
    state["closed"] = True
    state["state"] = "aborted_closed"
    raise OSError("expert_replay_read_failed") from None


def _fatal_root_direct_purge_is_proven(
    state: dict[str, object],
) -> bool:
    root = state.get("root")
    return (
        type(root) is _Root
        and not root.active
        and state.get("root_failed") is True
        and state.get("root_failed_purge_proven") is True
        and state.get("root_failed_purge_uncertain") is False
        and state.get("close_uncertain", False) is False
    )


def _transition_replay_denial(
    state: dict[str, object],
    *,
    mismatch: ExpertReplayMismatchV1,
    sampled: int,
    file_proofs: tuple[object, ...] = (),
    companion_scan: ExpertJournalScanSummaryV1 | None = None,
) -> ExpertReplayDeniedV1:
    purge_for_access_loss = (
        sampled >= state["deadline"]
        or mismatch
        in {
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
            ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
        }
    )
    denial = _contextual_replay_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
        file_proofs=file_proofs,
        companion_scan=companion_scan,
    )
    close_uncertain = False
    try:
        _close_replay_owned_readers(state)
    except _ReplayCloseUncertain:
        close_uncertain = True
    finally:
        _purge_replay_denial_payloads(state)
        state.pop("denial", None)
        state["state"] = (
            "aborted_closed" if close_uncertain else "terminal_denied"
        )
        state["closed"] = close_uncertain
    purge_uncertain = False
    if (
        purge_for_access_loss
        and not _fatal_root_direct_purge_is_proven(state)
    ):
        root = state["root"]
        manifest = state["manifest"]
        assert type(root) is _Root
        try:
            _purge_names(
                root,
                manifest.session_id,
                preserve_replay=state,
            )
        except Exception:
            purge_uncertain = True
    if close_uncertain or purge_uncertain:
        _purge_replay_denial_payloads(state)
        state.pop("denial", None)
        state["closed"] = True
        state["state"] = "aborted_closed"
        raise OSError("expert_replay_close_uncertain") from None
    state["denial"] = denial
    return denial


def _close_prepare_with_denial(
    state: dict[str, object],
    *,
    mismatch: ExpertReplayMismatchV1,
    sampled: int,
    file_proofs: tuple[object, ...] = (),
    companion_scan: ExpertJournalScanSummaryV1 | None = None,
) -> ExpertReplayDeniedV1:
    denial = _contextual_replay_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
        file_proofs=file_proofs,
        companion_scan=companion_scan,
    )
    close_uncertain = False
    try:
        _close_replay_owned_readers(state)
    except _ReplayCloseUncertain:
        close_uncertain = True
    finally:
        _purge_replay_denial_payloads(state)
        state.pop("denial", None)
        state["closed"] = True
        state["state"] = (
            "aborted_closed" if close_uncertain else "denied_closed"
        )
    root = state["root"]
    manifest = state["manifest"]
    assert type(root) is _Root
    assert type(manifest) is SessionManifest
    purge_uncertain = False
    if not _fatal_root_direct_purge_is_proven(state):
        try:
            _purge_names(
                root,
                manifest.session_id,
                preserve_replay=state,
            )
        except Exception:
            purge_uncertain = True
    if close_uncertain or purge_uncertain:
        _purge_replay_denial_payloads(state)
        state.pop("denial", None)
        state["closed"] = True
        state["state"] = "aborted_closed"
        raise OSError("expert_replay_close_uncertain") from None
    state["denial"] = denial
    return denial


def _close_prepare_access_denial(
    state: dict[str, object],
    *,
    mismatch: ExpertReplayMismatchV1,
    sampled: int,
) -> ExpertReplayDeniedV1:
    denial = _close_prepare_with_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
    )
    return denial


def _raise_prepare_operational_read_failure(
    state: dict[str, object],
) -> None:
    """Fail closed for prepare-time descriptor and read failures."""
    close_uncertain = False
    try:
        _close_replay_owned_readers(state)
    except _ReplayCloseUncertain:
        close_uncertain = True
    finally:
        _purge_replay_denial_payloads(state)
        state.pop("denial", None)
        state["closed"] = True
        state["state"] = "aborted_closed"
    if close_uncertain:
        raise OSError("expert_replay_close_uncertain") from None
    raise OSError("expert_replay_read_failed") from None


def _identity_file_proof(
    state: dict[str, object],
    role: ExpertReplayDiagnosticRoleV1,
) -> object:
    root = state["root"]
    expert_manifest = state.get("expert_manifest")
    phase1_manifest = state.get("manifest")
    assert type(root) is _Root
    if type(expert_manifest) is ExpertSessionManifestV1:
        session_id = expert_manifest.session_id
    elif type(phase1_manifest) is SessionManifest:
        session_id = phase1_manifest.session_id
    else:
        raise ValueError("expert_replay_manifest_missing")
    mapping = {
        ExpertReplayDiagnosticRoleV1.PHASE1_MARKER: (
            root.evidence_markers_fd,
            f"{session_id}.marker.json",
        ),
        ExpertReplayDiagnosticRoleV1.PHASE1_WAL: (
            root.evidence_sessions_fd,
            f"{session_id}.wal",
        ),
        ExpertReplayDiagnosticRoleV1.EXPERT_MARKER: (
            root.markers_fd,
            _marker_basename(session_id),
        ),
        ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL: (
            root.sessions_fd,
            _journal_basename(session_id),
        ),
    }
    directory_fd, basename = mapping[role]

    def gate() -> None:
        if state.get("state") == "new":
            _require_prepare_replay_access(state)
        else:
            _replay_access_gate(state)

    descriptor = -1
    gate()
    try:
        preopen_named = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except OSError as error:
        if not _is_missing_entry_error(error):
            raise
        preopen_named = None
    gate()
    try:
        descriptor = os.open(
            basename,
            _OPEN_FILE_READ_FLAGS,
            dir_fd=directory_fd,
        )
    except OSError as error:
        if not _is_missing_entry_error(error):
            gate()
            raise
        gate()
        try:
            observed = os.stat(
                basename,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except OSError as post_error:
            gate()
            if not _is_missing_entry_error(post_error):
                raise
            values: dict[str, object] = {
                "schema_version": 1,
                "role": role,
                "entry_present": False,
                "device": None,
                "inode": None,
                "uid": None,
                "mode": None,
                "link_count": None,
                "mtime_ns": None,
                "ctime_ns": None,
                "observed_size": 0,
                "observed_prefix_length": 0,
                "observed_prefix_sha256": sha256(b"").hexdigest(),
                "issue": ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
            }
        else:
            gate()
            issue = (
                ExpertReplayDiagnosticIssueV1.ENTRY_NOT_REGULAR
                if not stat.S_ISREG(observed.st_mode)
                else ExpertReplayDiagnosticIssueV1.ENTRY_IDENTITY_INVALID
            )
            values = {
                "schema_version": 1,
                "role": role,
                "entry_present": True,
                "device": observed.st_dev,
                "inode": observed.st_ino,
                "uid": observed.st_uid,
                "mode": _mode(observed),
                "link_count": observed.st_nlink,
                "mtime_ns": observed.st_mtime_ns,
                "ctime_ns": observed.st_ctime_ns,
                "observed_size": observed.st_size,
                "observed_prefix_length": 0,
                "observed_prefix_sha256": sha256(b"").hexdigest(),
                "issue": issue,
            }
    else:
        try:
            gate()
            observed = os.fstat(descriptor)
            gate()
            try:
                named_before = os.stat(
                    basename,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                gate()
                if not _is_missing_entry_error(error):
                    raise
                named_before = None
            else:
                gate()
            prefix_length = min(observed.st_size, 4096)
            gate()
            prefix = _gated_pread_exact(
                descriptor,
                0,
                prefix_length,
                gate=gate,
            )
            gate()
            after = os.fstat(descriptor)
            gate()
            try:
                named_after = os.stat(
                    basename,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                gate()
                if not _is_missing_entry_error(error):
                    raise
                named_after = None
            else:
                gate()
            stable = (
                named_before is not None
                and named_after is not None
                and _same_file_identity(observed, named_before)
                and _same_file_identity(observed, after)
                and _same_file_identity(after, named_after)
            )
            issue = ExpertReplayDiagnosticIssueV1.ENTRY_IDENTITY_INVALID
            if not stable:
                issue = ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED
            elif (
                not stat.S_ISREG(after.st_mode)
                or after.st_uid != os.getuid()
                or _mode(after) != 0o600
                or after.st_nlink != 1
            ):
                issue = ExpertReplayDiagnosticIssueV1.ENTRY_NOT_REGULAR
            else:
                expected_set = state.get("identity_set")
                expected = None
                if type(expected_set) is tuple:
                    role_index = tuple(ExpertReplayDiagnosticRoleV1).index(
                        role
                    )
                    if role_index < len(expected_set):
                        expected = expected_set[role_index]
                bootstrap_set = state.get(
                    "phase1_bootstrap_identities"
                )
                bootstrap_known = (
                    type(bootstrap_set) is tuple
                    and len(bootstrap_set) == 2
                    and role
                    in (
                        ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                        ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                    )
                )
                bootstrap_expected = None
                if bootstrap_known:
                    bootstrap_index = (
                        0
                        if role
                        is ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
                        else 1
                    )
                    bootstrap_expected = bootstrap_set[
                        bootstrap_index
                    ]
                if (
                    (
                        type(expected) is ExpertPhysicalFileIdentityV1
                        and (
                            after.st_dev,
                            after.st_ino,
                            after.st_uid,
                            _mode(after),
                            after.st_nlink,
                            after.st_size,
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        )
                        != (
                            expected.device,
                            expected.inode,
                            expected.uid,
                            expected.mode,
                            expected.link_count,
                            expected.size,
                            expected.mtime_ns,
                            expected.ctime_ns,
                        )
                    )
                    or (
                        bootstrap_known
                        and _stat_identity_observation(after)
                        != bootstrap_expected
                    )
                ):
                    issue = ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED
                elif role is ExpertReplayDiagnosticRoleV1.PHASE1_WAL:
                    if (
                        len(prefix) < 16
                        or prefix[:16]
                        != struct.pack(">8sHHI", b"INCIWAL\x00", 1, 0, 16)
                    ):
                        issue = (
                            ExpertReplayDiagnosticIssueV1.HEADER_INVALID
                        )
                elif role is ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL:
                    try:
                        decode_expert_file_header(
                            prefix[:EXPERT_FILE_HEADER_BYTES]
                        )
                    except Exception:
                        issue = (
                            ExpertReplayDiagnosticIssueV1.HEADER_INVALID
                        )
                elif role is ExpertReplayDiagnosticRoleV1.EXPERT_MARKER:
                    try:
                        _decode_expert_marker(prefix)
                    except Exception:
                        issue = ExpertReplayDiagnosticIssueV1.SCAN_INVALID
                else:
                    try:
                        raw = json.loads(prefix.decode("ascii"))
                        RetentionMarker(**raw)
                        if canonical_json_bytes(raw) != prefix:
                            raise ValueError
                    except Exception:
                        issue = ExpertReplayDiagnosticIssueV1.SCAN_INVALID
            values = {
                "schema_version": 1,
                "role": role,
                "entry_present": True,
                "device": after.st_dev,
                "inode": after.st_ino,
                "uid": after.st_uid,
                "mode": _mode(after),
                "link_count": after.st_nlink,
                "mtime_ns": after.st_mtime_ns,
                "ctime_ns": after.st_ctime_ns,
                "observed_size": after.st_size,
                "observed_prefix_length": len(prefix),
                "observed_prefix_sha256": sha256(prefix).hexdigest(),
                "issue": issue,
            }
        except OSError as error:
            gate()
            if not _is_missing_entry_error(error):
                raise
            fallback = preopen_named
            if fallback is None:
                try:
                    fallback = os.stat(
                        basename,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError as fallback_error:
                    gate()
                    if not _is_missing_entry_error(fallback_error):
                        raise
                    fallback = None
                else:
                    gate()
            if fallback is None:
                values = {
                    "schema_version": 1,
                    "role": role,
                    "entry_present": False,
                    "device": None,
                    "inode": None,
                    "uid": None,
                    "mode": None,
                    "link_count": None,
                    "mtime_ns": None,
                    "ctime_ns": None,
                    "observed_size": 0,
                    "observed_prefix_length": 0,
                    "observed_prefix_sha256": sha256(b"").hexdigest(),
                    "issue": (
                        ExpertReplayDiagnosticIssueV1.ENTRY_MISSING
                    ),
                }
            else:
                values = {
                    "schema_version": 1,
                    "role": role,
                    "entry_present": True,
                    "device": fallback.st_dev,
                    "inode": fallback.st_ino,
                    "uid": fallback.st_uid,
                    "mode": _mode(fallback),
                    "link_count": fallback.st_nlink,
                    "mtime_ns": fallback.st_mtime_ns,
                    "ctime_ns": fallback.st_ctime_ns,
                    "observed_size": fallback.st_size,
                    "observed_prefix_length": 0,
                    "observed_prefix_sha256": sha256(b"").hexdigest(),
                    "issue": (
                        ExpertReplayDiagnosticIssueV1
                        .ENTRY_IDENTITY_INVALID
                    ),
                }
        finally:
            _close_root_governed_temporary(
                root,
                descriptor,
                message="expert_replay_close_uncertain",
            )
    gate()
    return _create_expert_replay_diagnostic_file_proof_v1(
        **values,
        proof_sha256=compute_expert_replay_diagnostic_file_proof_sha256(
            **values
        ),
    )


def _identity_denial(
    state: dict[str, object],
    *,
    sampled: int,
    role: ExpertReplayDiagnosticRoleV1,
) -> ExpertReplayDeniedV1:
    mismatch = ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
    file_proof = _identity_file_proof(state, role)
    if file_proof.issue not in {
        ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
        ExpertReplayDiagnosticIssueV1.ENTRY_NOT_REGULAR,
        ExpertReplayDiagnosticIssueV1.ENTRY_IDENTITY_INVALID,
        ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED,
    }:
        values = {
            "schema_version": file_proof.schema_version,
            "role": file_proof.role,
            "entry_present": file_proof.entry_present,
            "device": file_proof.device,
            "inode": file_proof.inode,
            "uid": file_proof.uid,
            "mode": file_proof.mode,
            "link_count": file_proof.link_count,
            "mtime_ns": file_proof.mtime_ns,
            "ctime_ns": file_proof.ctime_ns,
            "observed_size": file_proof.observed_size,
            "observed_prefix_length": (
                file_proof.observed_prefix_length
            ),
            "observed_prefix_sha256": (
                file_proof.observed_prefix_sha256
            ),
            "issue": (
                ExpertReplayDiagnosticIssueV1.ENTRY_IDENTITY_INVALID
            ),
        }
        file_proof = _create_expert_replay_diagnostic_file_proof_v1(
            **values,
            proof_sha256=(
                compute_expert_replay_diagnostic_file_proof_sha256(
                    **values
                )
            ),
        )
    return _contextual_replay_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
        file_proofs=(file_proof,),
    )


def _readerless_replay_access_gate(
    root: _Root,
    *,
    manifest: SessionManifest,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> int | ExpertReplayDeniedV1:
    deadline = manifest.required_retention_until_ns
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=_last_valid_replay_sample(root),
        )
    if sampled >= deadline:
        return _deadline_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        if root.active:
            _fatal_root(root)
        raise OSError("expert_replay_read_failed") from None
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root)
        if sampled >= deadline:
            return _deadline_denial(
                session_id=manifest.session_id,
                deadline=deadline,
                sampled=sampled,
            )
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    try:
        if (
            _require_authorizer(
                root,
                persistence_authorizer,
                coordinator,
            )
            is not manifest
        ):
            raise ExpertLiveAuthorizationDenied()
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root)
        if sampled >= deadline:
            return _deadline_denial(
                session_id=manifest.session_id,
                deadline=deadline,
                sampled=sampled,
            )
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=_last_valid_replay_sample(root),
        )
    if sampled >= deadline:
        return _deadline_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        if root.active:
            _fatal_root(root)
        raise OSError("expert_replay_read_failed") from None
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root)
        if sampled >= deadline:
            return _deadline_denial(
                session_id=manifest.session_id,
                deadline=deadline,
                sampled=sampled,
            )
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    try:
        if (
            _require_authorizer(
                root,
                persistence_authorizer,
                coordinator,
            )
            is not manifest
        ):
            raise ExpertLiveAuthorizationDenied()
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root)
        if sampled >= deadline:
            return _deadline_denial(
                session_id=manifest.session_id,
                deadline=deadline,
                sampled=sampled,
            )
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        return _authorization_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=_last_valid_replay_sample(root),
        )
    if sampled >= deadline:
        return _deadline_denial(
            session_id=manifest.session_id,
            deadline=deadline,
            sampled=sampled,
        )
    return sampled


def issue_expert_replay_construction_authority(
    authority: ExpertJournalRootAuthorityV1,
    *,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ExpertReplayConstructionAuthorityV1 | ExpertReplayDeniedV1:
    with _LOCK:
        if type(authority) is not ExpertJournalRootAuthorityV1:
            raise TypeError("authority")
        root = _ROOTS.get(authority)
        if (
            root is None
            or not root.active
            or root.token is not authority
            or root.owner_pid != os.getpid()
            or root.owner_thread is not threading.current_thread()
        ):
            raise ValueError("expert_root_authority_invalid")
        manifest = _bound_authorizer_manifest(
            root,
            persistence_authorizer,
            coordinator,
        )
        deadline = manifest.required_retention_until_ns
        access = _readerless_replay_access_gate(
            root,
            manifest=manifest,
            persistence_authorizer=persistence_authorizer,
            coordinator=coordinator,
        )
        if type(access) is ExpertReplayDeniedV1:
            return access
        token = _token(ExpertReplayConstructionAuthorityV1)
        access = _readerless_replay_access_gate(
            root,
            manifest=manifest,
            persistence_authorizer=persistence_authorizer,
            coordinator=coordinator,
        )
        if type(access) is ExpertReplayDeniedV1:
            return access
        _REPLAYS[token] = {
            "authority": token,
            "root": root,
            "generation": root.generation,
            "owner_pid": os.getpid(),
            "owner_thread": threading.current_thread(),
            "authorizer": persistence_authorizer,
            "coordinator": coordinator,
            "manifest": manifest,
            "deadline": deadline,
            "state": "new",
            "sequence": 0,
            "outstanding": None,
            "evidence_index": 0,
            "group_index": 0,
            "closed": False,
            "_last_sampled_wall_ns": access,
        }
        return token


def _sample_contextual_replay_state(
    state: dict[str, object],
) -> int:
    root = state["root"]
    assert type(root) is _Root
    deadline = state["deadline"]
    if type(deadline) is not int or deadline <= 0:
        raise ValueError("expert_replay_sample_invalid")
    sampled = _sample_replay_prepare_wall_ns(
        root,
        deadline_ns=deadline,
    )
    state["_last_sampled_wall_ns"] = sampled
    return sampled


def _raise_contextual_replay_denial(
    state: dict[str, object],
    *,
    mismatch: ExpertReplayMismatchV1,
    sampled: int,
    file_proofs: tuple[object, ...] = (),
) -> None:
    _transition_replay_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
        file_proofs=file_proofs,
    )
    raise ExpertReplayAccessDenied()


def _raise_contextual_authorization_loss(
    state: dict[str, object],
) -> None:
    root = state["root"]
    assert type(root) is _Root
    try:
        sampled = _sample_contextual_replay_state(state)
    except Exception:
        sampled = _last_valid_replay_sample(root, state)
    mismatch = (
        ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
        if sampled >= state["deadline"]
        else ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
    )
    _raise_contextual_replay_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
    )


def _replay_access_gate(state: dict[str, object]) -> int:
    root = state["root"]
    assert type(root) is _Root
    try:
        sampled = _sample_contextual_replay_state(state)
    except Exception:
        _raise_contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    if sampled >= state["deadline"]:
        _raise_contextual_replay_denial(
            state,
            mismatch=ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            sampled=sampled,
        )
    if (
        not root.active
        or _ROOTS.get(root.token) is not root
        or root.owner_pid != state.get("owner_pid")
        or root.owner_thread is not state.get("owner_thread")
    ):
        _raise_contextual_authorization_loss(state)
    if state.get("generation") != root.generation:
        _raise_contextual_authorization_loss(state)
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        _raise_replay_operational_read_failure_after_gate(state)
    except Exception:
        _raise_contextual_authorization_loss(state)
    try:
        _require_authorizer(
            root,
            state["authorizer"],
            state["coordinator"],
        )
    except Exception:
        _raise_contextual_authorization_loss(state)
    try:
        sampled = _sample_contextual_replay_state(state)
    except Exception:
        _raise_contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    if sampled >= state["deadline"]:
        _raise_contextual_replay_denial(
            state,
            mismatch=ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            sampled=sampled,
        )
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        _raise_replay_operational_read_failure_after_gate(state)
    except Exception:
        _raise_contextual_authorization_loss(state)
    if state.get("generation") != root.generation:
        _raise_contextual_authorization_loss(state)
    try:
        _require_authorizer(
            root,
            state["authorizer"],
            state["coordinator"],
        )
    except Exception:
        _raise_contextual_authorization_loss(state)
    try:
        sampled = _sample_contextual_replay_state(state)
    except Exception:
        _raise_contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    if sampled >= state["deadline"]:
        _raise_contextual_replay_denial(
            state,
            mismatch=ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            sampled=sampled,
        )
    return sampled


def _replay_snapshot_io_gate(state: dict[str, object]) -> int:
    """Cheap in-snapshot clock/root gate.

    A full replay snapshot performs retention authorization at its two
    boundaries.  Descriptor-relative reads inside that snapshot still need a
    clock and root-identity seam, but must not recursively start another full
    authorization/environment snapshot for every byte read.
    """

    root = state["root"]
    assert type(root) is _Root
    try:
        sampled = _sample_contextual_replay_state(state)
    except Exception:
        _raise_contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    if sampled >= state["deadline"]:
        _raise_contextual_replay_denial(
            state,
            mismatch=ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            sampled=sampled,
        )
    if (
        not root.active
        or _ROOTS.get(root.token) is not root
        or root.owner_pid != state.get("owner_pid")
        or root.owner_thread is not state.get("owner_thread")
        or state.get("generation") != root.generation
    ):
        _raise_contextual_authorization_loss(state)
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        _raise_replay_operational_read_failure_after_gate(state)
    except Exception:
        _raise_contextual_authorization_loss(state)
    return sampled


def _replay_state(
    authority: ExpertReplayConstructionAuthorityV1,
    *allowed: str,
) -> dict[str, object]:
    state = _replay_authority_state(authority, *allowed)
    sampled = _replay_access_gate(state)
    reader_required_states = {
        "begin_ready",
        "begin_auth_outstanding",
        "begin_diagnostic",
        "pair_empty",
        "evidence_parent_ready",
        "evidence_eof_ready",
        "pair_complete",
        "parent_auth_outstanding",
        "cardinality_mismatch",
        "companion_scan_invalid",
        "both_eof",
        "finish_ready",
        "finish_auth_outstanding",
    }
    require_readers = state.get("state") in reader_required_states
    prepared_payload_required = (
        require_readers or state.get("state") == "finish_closing"
    )
    expected_identities = state.get("identity_set")
    evidence = state.get("evidence")
    expert_manifest = state.get("expert_manifest")
    expected_environment = state.get("expected_environment")
    prepared_payload_present = any(
        name in state
        for name in (
            "identity_set",
            "evidence",
            "expert_manifest",
            "expected_environment",
        )
    )
    identities_valid = (
        type(expected_identities) is tuple
        and len(expected_identities) == 4
        and all(
            type(identity) is ExpertPhysicalFileIdentityV1
            for identity in expected_identities
        )
    )
    if (
        (prepared_payload_required or prepared_payload_present)
        and (
            not identities_valid
            or type(evidence) is not EvidenceReplayContextV1
            or type(expert_manifest) is not ExpertSessionManifestV1
            or type(expected_environment)
            is not ExpertCurrentEnvironmentV1
        )
    ):
        _raise_contextual_authorization_loss(state)
    companion = state.get("companion_reader")
    companion_state = (
        _READERS.get(companion)
        if type(companion) is ExpertJournalReadCapabilityV1
        else None
    )
    phase1_reader = state.get("phase1_reader")
    phase1_records = state.get("phase1_records")
    companion_descriptor_invalid = False
    if type(companion_state) is _Reader:
        try:
            descriptor_observation = _descriptor_identity_observation(
                os.fstat(companion_state.fd)
            )
        except OSError as error:
            if error.errno == errno.EBADF:
                companion_descriptor_invalid = True
            else:
                _raise_replay_operational_read_failure_after_gate(state)
        else:
            companion_descriptor_invalid = (
                descriptor_observation != companion_state.fd_identity
            )
        sampled = _replay_snapshot_io_gate(state)
    reader_lifecycle_invalid = (
        (
            require_readers
            and (
                type(companion) is not ExpertJournalReadCapabilityV1
                or type(phase1_reader) is not JournalReader
                or phase1_records is None
            )
        )
        or (
            companion is not None
            and (
                type(companion_state) is not _Reader
                or companion_descriptor_invalid
                or companion_state.closed
                or companion_state.replay_authority is not authority
                or companion_state.owner_pid != state.get("owner_pid")
                or companion_state.owner_thread is not state.get("owner_thread")
            )
        )
        or (
            phase1_reader is not None
            and (
                type(phase1_reader) is not JournalReader
                or phase1_reader._closed
            )
        )
        or (
            phase1_records is not None
            and not callable(
                getattr(phase1_records, "__next__", None)
            )
        )
    )
    if reader_lifecycle_invalid:
        _raise_contextual_authorization_loss(state)
    if identities_valid:
        role = ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
        identity_mismatch = False
        try:
            root = state["root"]
            assert type(root) is _Root
            current_evidence = _guarded_phase1_evidence_file_identities(
                root,
                session_manifest=evidence.session_manifest,
                session_start=evidence.session_start,
                gate=lambda: _replay_snapshot_io_gate(state),
                byte_gate=lambda: _replay_snapshot_io_gate(state),
            )
            for index, candidate_role in enumerate(
                (
                    ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                    ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                )
            ):
                role = candidate_role
                if current_evidence[index] != expected_identities[index]:
                    raise ValueError
            current_companion = (
                _guarded_expert_companion_file_identities(
                    root,
                    manifest=expert_manifest,
                    gate=lambda: _replay_snapshot_io_gate(state),
                    byte_gate=lambda: _replay_snapshot_io_gate(state),
                )
            )
            for offset, candidate_role in enumerate(
                (
                    ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
                    ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                ),
                start=2,
            ):
                role = candidate_role
                if current_companion[offset - 2] != expected_identities[offset]:
                    raise ValueError
        except _PrepareReplayDenied as denied:
            return denied.denial
        except ExpertReplayAccessDenied:
            raise
        except _ReplayIdentityFailure as error:
            role = error.role
            identity_mismatch = True
        except OSError:
            _raise_replay_operational_read_failure_after_gate(state)
        except Exception:
            identity_mismatch = True
        sampled = _replay_snapshot_io_gate(state)
        if identity_mismatch:
            proof = _identity_file_proof(state, role)
            sampled = _replay_access_gate(state)
            _raise_contextual_replay_denial(
                state,
                mismatch=ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
                sampled=sampled,
                file_proofs=(proof,),
            )
    if type(expected_environment) is ExpertCurrentEnvironmentV1:
        environment_mismatch = False
        try:
            root = state["root"]
            assert type(root) is _Root
            installed, _, _, _ = _installed_environment(
                root,
                state["manifest"],
                gate=lambda: _replay_snapshot_io_gate(state),
            )
            if (
                root.last_environment != expected_environment
                or installed != expected_environment
            ):
                raise ValueError
        except ExpertReplayAccessDenied:
            raise
        except OSError:
            _raise_replay_operational_read_failure_after_gate(state)
        except Exception:
            environment_mismatch = True
        sampled = _replay_snapshot_io_gate(state)
        if identities_valid:
            role = ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
            identity_mismatch_after_environment = False
            try:
                current_evidence = (
                    _guarded_phase1_evidence_file_identities(
                        root,
                        session_manifest=evidence.session_manifest,
                        session_start=evidence.session_start,
                        gate=lambda: _replay_snapshot_io_gate(state),
                        byte_gate=lambda: _replay_snapshot_io_gate(state),
                    )
                )
                current_companion = (
                    _guarded_expert_companion_file_identities(
                        root,
                        manifest=expert_manifest,
                        gate=lambda: _replay_snapshot_io_gate(state),
                        byte_gate=lambda: _replay_snapshot_io_gate(state),
                    )
                )
                for index, candidate_role in enumerate(
                    (
                        ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                        ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                        ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
                        ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                    )
                ):
                    role = candidate_role
                    current = (
                        current_evidence[index]
                        if index < 2
                        else current_companion[index - 2]
                    )
                    if current != expected_identities[index]:
                        raise _ReplayIdentityFailure(role)
            except ExpertReplayAccessDenied:
                raise
            except _ReplayIdentityFailure as error:
                role = error.role
                identity_mismatch_after_environment = True
            except OSError:
                _raise_replay_operational_read_failure_after_gate(state)
            except Exception:
                identity_mismatch_after_environment = True
            sampled = _replay_snapshot_io_gate(state)
            if identity_mismatch_after_environment:
                proof = _identity_file_proof(state, role)
                sampled = _replay_access_gate(state)
                _raise_contextual_replay_denial(
                    state,
                    mismatch=(
                        ExpertReplayMismatchV1
                        .EVIDENCE_IDENTITY_MISMATCH
                    ),
                    sampled=sampled,
                    file_proofs=(proof,),
                )
        sampled = _replay_access_gate(state)
        if environment_mismatch:
            _raise_contextual_replay_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
                ),
                sampled=sampled,
            )
    return state


def _replay_full_integrity_gate(state: dict[str, object]) -> None:
    authority = state.get("authority")
    current_state = state.get("state")
    if (
        type(authority) is not ExpertReplayConstructionAuthorityV1
        or type(current_state) is not str
    ):
        raise ValueError("expert_replay_authority_invalid")
    _replay_state(authority, current_state)


def _replay_cached_state(
    authority: ExpertReplayConstructionAuthorityV1,
    *allowed: str,
) -> dict[str, object]:
    """Validate ownership/access without performing an environment snapshot."""
    state = _replay_authority_state(authority, *allowed)
    _replay_access_gate(state)
    identities = state.get("identity_set")
    if (
        type(identities) is not tuple
        or len(identities) != 4
        or any(
            type(identity) is not ExpertPhysicalFileIdentityV1
            for identity in identities
        )
        or type(state.get("evidence")) is not EvidenceReplayContextV1
        or type(state.get("expert_manifest"))
        is not ExpertSessionManifestV1
        or type(state.get("expected_environment"))
        is not ExpertCurrentEnvironmentV1
    ):
        _raise_contextual_authorization_loss(state)
    return state


def _replay_authority_state(
    authority: ExpertReplayConstructionAuthorityV1,
    *allowed: str,
) -> dict[str, object]:
    if type(authority) is not ExpertReplayConstructionAuthorityV1:
        raise TypeError("authority")
    state = _REPLAYS.get(authority)
    if state is None or state["closed"]:
        raise ValueError("expert_replay_authority_invalid")
    root = state["root"]
    assert type(root) is _Root
    # A non-owner must never be allowed to mutate or destroy authority that
    # remains live for the rightful owner.
    if (
        state.get("owner_pid") != os.getpid()
        or state.get("owner_thread") is not threading.current_thread()
    ):
        raise ValueError("expert_replay_authority_invalid")
    if state["state"] not in allowed:
        _close_replay_owned_readers(state)
        state["outstanding"] = None
        state["closed"] = True
        state["state"] = "aborted_closed"
        raise ValueError("expert_replay_authority_invalid")
    return state


def _prepare_replay_access_gate(
    state: dict[str, object],
) -> int | ExpertReplayDeniedV1:
    root = state["root"]
    manifest = state["manifest"]
    authorizer = state["authorizer"]
    coordinator = state["coordinator"]
    deadline = state["deadline"]
    assert type(root) is _Root
    assert type(manifest) is SessionManifest
    if type(deadline) is not int or deadline <= 0:
        raise ValueError("expert_replay_sample_invalid")
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    state["_last_sampled_wall_ns"] = sampled
    if sampled >= deadline:
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ),
            sampled=sampled,
        )
    if (
        not root.active
        or _ROOTS.get(root.token) is not root
        or root.owner_pid != state.get("owner_pid")
        or root.owner_thread is not state.get("owner_thread")
        or state.get("generation") != root.generation
    ):
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=sampled,
        )
    try:
        if (
            _require_prepare_replay_authorizer(
                root,
                authorizer,
                coordinator,
            )
            is not manifest
        ):
            raise ExpertLiveAuthorizationDenied()
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root, state)
        mismatch = (
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            if sampled >= deadline
            else ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
        )
        return _close_prepare_access_denial(
            state,
            mismatch=mismatch,
            sampled=sampled,
        )
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    state["_last_sampled_wall_ns"] = sampled
    if sampled >= deadline:
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ),
            sampled=sampled,
        )
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        _raise_prepare_operational_read_failure(state)
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root, state)
        mismatch = (
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            if sampled >= deadline
            else ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
        )
        return _close_prepare_access_denial(
            state,
            mismatch=mismatch,
            sampled=sampled,
        )
    try:
        if (
            _require_prepare_replay_authorizer(
                root,
                authorizer,
                coordinator,
            )
            is not manifest
        ):
            raise ExpertLiveAuthorizationDenied()
    except Exception:
        try:
            sampled = _sample_replay_prepare_wall_ns(
                root,
                deadline_ns=deadline,
            )
        except Exception:
            sampled = _last_valid_replay_sample(root, state)
        mismatch = (
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            if sampled >= deadline
            else ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
        )
        return _close_prepare_access_denial(
            state,
            mismatch=mismatch,
            sampled=sampled,
        )
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=_last_valid_replay_sample(root, state),
        )
    state["_last_sampled_wall_ns"] = sampled
    if sampled >= deadline:
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
            ),
            sampled=sampled,
        )
    if (
        not root.active
        or _ROOTS.get(root.token) is not root
        or root.owner_pid != state.get("owner_pid")
        or root.owner_thread is not state.get("owner_thread")
        or state.get("generation") != root.generation
    ):
        return _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=sampled,
        )
    return sampled


class _PrepareReplayDenied(RuntimeError):
    def __init__(self, denial: ExpertReplayDeniedV1) -> None:
        super().__init__("expert_replay_prepare_denied")
        self.denial = denial


def _require_prepare_replay_access(
    state: dict[str, object],
) -> int:
    access = _prepare_replay_access_gate(state)
    if type(access) is ExpertReplayDeniedV1:
        raise _PrepareReplayDenied(access)
    return access


def _require_prepare_replay_snapshot_io_gate(
    state: dict[str, object],
) -> int:
    """Cheap clock/root seam used inside one prepare integrity snapshot."""

    root = state["root"]
    deadline = state["deadline"]
    assert type(root) is _Root
    if type(deadline) is not int or deadline <= 0:
        raise ValueError("expert_replay_sample_invalid")
    try:
        sampled = _sample_replay_prepare_wall_ns(
            root,
            deadline_ns=deadline,
        )
    except Exception:
        sampled = _last_valid_replay_sample(root, state)
        denial = _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=sampled,
        )
        raise _PrepareReplayDenied(denial)
    state["_last_sampled_wall_ns"] = sampled
    if sampled >= deadline:
        denial = _close_prepare_access_denial(
            state,
            mismatch=ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            sampled=sampled,
        )
        raise _PrepareReplayDenied(denial)
    if (
        not root.active
        or _ROOTS.get(root.token) is not root
        or root.owner_pid != state.get("owner_pid")
        or root.owner_thread is not state.get("owner_thread")
        or state.get("generation") != root.generation
    ):
        denial = _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=sampled,
        )
        raise _PrepareReplayDenied(denial)
    try:
        _validate_replay_prepare_root_after_access_gate(root)
    except OSError:
        _raise_prepare_operational_read_failure(state)
    except Exception:
        denial = _close_prepare_access_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
            ),
            sampled=sampled,
        )
        raise _PrepareReplayDenied(denial)
    return sampled


def _raise_prepare_replay_denial(
    state: dict[str, object],
    *,
    mismatch: ExpertReplayMismatchV1,
    role: ExpertReplayDiagnosticRoleV1 | None = None,
) -> None:
    sampled = _require_prepare_replay_access(state)
    file_proofs: tuple[object, ...] = ()
    if role is not None:
        file_proofs = (_identity_file_proof(state, role),)
        sampled = _require_prepare_replay_access(state)
    denial = _close_prepare_with_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
        file_proofs=file_proofs,
    )
    raise _PrepareReplayDenied(denial)


def _close_prepare_semantic_denial_after_gate(
    state: dict[str, object],
    mismatch: ExpertReplayMismatchV1,
) -> ExpertReplayDeniedV1:
    sampled = state.get("_last_sampled_wall_ns")
    if type(sampled) is not int:
        raise ValueError("expert_replay_sample_invalid")
    return _close_prepare_with_denial(
        state,
        mismatch=mismatch,
        sampled=sampled,
    )


def _take_prepare_replay_full_integrity_snapshot(
    state: dict[str, object],
) -> int:
    """Gate prepare I/O against its immutable identity/environment baselines."""

    sampled = _require_prepare_replay_access(state)
    root = state["root"]
    manifest = state["manifest"]
    assert type(root) is _Root
    assert type(manifest) is SessionManifest
    evidence_context_ready = type(
        state.get("evidence")
    ) is EvidenceReplayContextV1
    observations: list[
        tuple[
            ExpertReplayDiagnosticRoleV1,
            int,
            str,
            object,
        ]
    ] = []
    phase1 = state.get("phase1_bootstrap_identities")
    if type(phase1) is tuple and len(phase1) == 2:
        observations.extend(
            (
                (
                    ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                    root.evidence_markers_fd,
                    f"{manifest.session_id}.marker.json",
                    phase1[0],
                ),
                (
                    ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                    root.evidence_sessions_fd,
                    f"{manifest.session_id}.wal",
                    phase1[1],
                ),
            )
        )
    companion_marker = state.get(
        "prepare_companion_marker_identity"
    )
    if companion_marker is not None:
        observations.append(
            (
                ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
                root.markers_fd,
                _marker_basename(manifest.session_id),
                companion_marker,
            )
        )
    companion_journal = state.get(
        "prepare_companion_journal_identity"
    )
    if companion_journal is not None:
        observations.append(
            (
                ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                root.sessions_fd,
                _journal_basename(manifest.session_id),
                companion_journal,
            )
        )
    identity_mismatch_role: ExpertReplayDiagnosticRoleV1 | None = None
    try:
        for role, directory_fd, basename, expected in observations:
            _require_prepare_replay_snapshot_io_gate(state)
            current = _named_file_identity_observation(
                directory_fd,
                basename,
            )
            _require_prepare_replay_snapshot_io_gate(state)
            if current != expected and identity_mismatch_role is None:
                identity_mismatch_role = role
    except _PrepareReplayDenied:
        raise
    except OSError:
        _raise_prepare_operational_read_failure(state)

    expected_environment = state.get(
        "prepare_expected_environment"
    )
    environment_mismatch = False
    if (
        evidence_context_ready
        and type(expected_environment) is ExpertCurrentEnvironmentV1
    ):
        installed: object = None
        try:
            installed, _, _, _ = _installed_environment(
                root,
                manifest,
                gate=lambda: (
                    _require_prepare_replay_snapshot_io_gate(state)
                ),
            )
        except _PrepareReplayDenied:
            raise
        except OSError:
            _raise_prepare_operational_read_failure(state)
        except Exception:
            environment_mismatch = True
        try:
            for role, directory_fd, basename, expected in observations:
                _require_prepare_replay_snapshot_io_gate(state)
                current = _named_file_identity_observation(
                    directory_fd,
                    basename,
                )
                _require_prepare_replay_snapshot_io_gate(state)
                if current != expected and identity_mismatch_role is None:
                    identity_mismatch_role = role
        except _PrepareReplayDenied:
            raise
        except OSError:
            _raise_prepare_operational_read_failure(state)
        if (
            root.last_environment != expected_environment
            or installed != expected_environment
        ):
            environment_mismatch = True
    sampled = _require_prepare_replay_access(state)
    if identity_mismatch_role is not None:
        _raise_prepare_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
                if evidence_context_ready
                else ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
            ),
            role=identity_mismatch_role,
        )
    if environment_mismatch:
        _raise_prepare_replay_denial(
            state,
            mismatch=ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
        )
    return sampled


def _require_prepare_replay_full_integrity(
    state: dict[str, object],
) -> int:
    """Compatibility name for a cheap prepare-time metadata seam.

    Full identity/environment snapshots are intentionally restricted to the
    actual pread seam and the final runtime validation.  Existing prepare
    metadata call sites retain this helper name so the large state-machine
    audit remains easy to compare, but they no longer recursively scan the
    entire source environment.
    """

    return _require_prepare_replay_snapshot_io_gate(state)


def _prepare_replay_pread(
    state: dict[str, object],
    fd: int,
    *,
    offset: int,
    length: int,
) -> bytes:
    _require_prepare_replay_snapshot_io_gate(state)
    before = os.fstat(fd)
    _take_prepare_replay_full_integrity_snapshot(state)
    content = _gated_pread_exact(
        fd,
        offset,
        length,
        gate=lambda: _require_prepare_replay_snapshot_io_gate(state),
    )
    _require_prepare_replay_snapshot_io_gate(state)
    after = os.fstat(fd)
    _require_prepare_replay_snapshot_io_gate(state)
    if not _same_file_identity(before, after):
        raise ValueError("expert_file_replaced")
    return content


def _read_prepare_replay_named_content(
    state: dict[str, object],
    directory_fd: int,
    basename: str,
    maximum: int,
    *,
    identity_role: ExpertReplayDiagnosticRoleV1 | None = None,
) -> bytes:
    root = state["root"]
    assert type(root) is _Root
    _require_prepare_replay_full_integrity(state)
    fd = os.open(basename, _OPEN_FILE_READ_FLAGS, dir_fd=directory_fd)
    try:
        _require_prepare_replay_full_integrity(state)
        value = _require_file(fd)
        _require_prepare_replay_full_integrity(state)
        named = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        _require_prepare_replay_full_integrity(state)
        if not _same_file_identity(value, named):
            raise ValueError("expert_file_replaced")
        observed_identity = _stat_identity_observation(value)
        if identity_role is ExpertReplayDiagnosticRoleV1.EXPERT_MARKER:
            prior = state.setdefault(
                "prepare_companion_marker_identity",
                observed_identity,
            )
            if prior != observed_identity:
                _raise_prepare_replay_denial(
                    state,
                    mismatch=(
                        ExpertReplayMismatchV1
                        .EVIDENCE_IDENTITY_MISMATCH
                    ),
                    role=identity_role,
                )
        if value.st_size > maximum:
            raise ValueError("expert_file_oversized")
        content = _prepare_replay_pread(
            state,
            fd,
            offset=0,
            length=value.st_size,
        )
        if len(content) != value.st_size:
            raise ValueError("expert_file_truncated")
        _require_prepare_replay_full_integrity(state)
        after = os.fstat(fd)
        _require_prepare_replay_full_integrity(state)
        named_after = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        _require_prepare_replay_full_integrity(state)
        if (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or not _same_file_identity(after, named_after):
            raise ValueError("expert_file_replaced")
        return content
    finally:
        _close_root_governed_temporary(
            root,
            fd,
            message="expert_replay_close_uncertain",
        )


def _read_prepare_replay_frame(
    state: dict[str, object],
    fd: int,
    offset: int,
) -> tuple[bytes | None, int]:
    prefix = _prepare_replay_pread(
        state,
        fd,
        offset=offset,
        length=EXPERT_FRAME_PREFIX_BYTES,
    )
    if not prefix:
        return None, offset
    if len(prefix) != EXPERT_FRAME_PREFIX_BYTES:
        raise ValueError("expert_frame_torn")
    _, _, total, metadata_size, payload_size = decode_expert_frame_prefix(
        prefix
    )
    if total > MAX_EXPERT_FRAME_BYTES:
        raise ValueError("expert_frame_oversized")
    body = _prepare_replay_pread(
        state,
        fd,
        offset=offset + EXPERT_FRAME_PREFIX_BYTES,
        length=total - EXPERT_FRAME_PREFIX_BYTES,
    )
    if len(body) != total - EXPERT_FRAME_PREFIX_BYTES:
        raise ValueError("expert_frame_torn")
    frame = prefix + body
    metadata_end = EXPERT_FRAME_PREFIX_BYTES + metadata_size
    payload_end = metadata_end + payload_size
    validate_expert_frame_parts(
        prefix,
        frame[EXPERT_FRAME_PREFIX_BYTES:metadata_end],
        frame[metadata_end:payload_end],
        frame[payload_end:],
    )
    return frame, offset + total


def _issue_prepare_replay_companion_reader(
    state: dict[str, object],
    manifest: ExpertSessionManifestV1,
    marker: dict[str, object],
) -> ExpertJournalReadCapabilityV1:
    root = state["root"]
    assert type(root) is _Root
    if marker.get("expert_manifest_sha256") != manifest.manifest_sha256:
        raise ValueError("expert_reader_manifest_invalid")
    _require_prepare_replay_full_integrity(state)
    fd = os.open(
        _journal_basename(manifest.session_id),
        _OPEN_FILE_READ_FLAGS,
        dir_fd=root.sessions_fd,
    )
    try:
        _require_prepare_replay_full_integrity(state)
        descriptor_stat = _require_file(fd)
        _require_prepare_replay_full_integrity(state)
        if (
            _stat_identity_observation(descriptor_stat)
            != state.get("prepare_companion_journal_identity")
        ):
            _raise_prepare_replay_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
                ),
                role=ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
            )
        token = _token(ExpertJournalReadCapabilityV1)
        reader = _Reader(
            token,
            root,
            manifest,
            fd,
            EXPERT_FILE_HEADER_BYTES,
            None,
            os.getpid(),
            threading.current_thread(),
            _descriptor_identity_observation(descriptor_stat),
            last_good_offset=EXPERT_FILE_HEADER_BYTES,
        )
        _require_prepare_replay_full_integrity(state)
        _READERS[token] = reader
        try:
            _require_prepare_replay_full_integrity(state)
        except Exception:
            _READERS.pop(token, None)
            raise
        return token
    except BaseException:
        _close_root_governed_temporary(
            root,
            fd,
            message="expert_replay_close_uncertain",
        )
        raise


def prepare_expert_replay_begin(
    authority: ExpertReplayConstructionAuthorityV1,
) -> ExpertReplayBeginReadyV1 | ExpertReplayDeniedV1:
    with _LOCK:
        entry_state = _replay_authority_state(authority, "new")
        entry_root = entry_state["root"]
        entry_manifest = entry_state["manifest"]
        entry_authorizer = entry_state["authorizer"]
        entry_coordinator = entry_state["coordinator"]
        deadline = entry_state["deadline"]
        assert type(entry_root) is _Root
        assert type(entry_manifest) is SessionManifest
        if type(deadline) is not int or deadline <= 0:
            raise ValueError("expert_replay_sample_invalid")
        access = _prepare_replay_access_gate(entry_state)
        if type(access) is ExpertReplayDeniedV1:
            return access
        sampled = access
        phase1_bootstrap_identities_list: list[object] = []
        for role, directory_fd, basename in (
            (
                ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                entry_root.evidence_markers_fd,
                f"{entry_manifest.session_id}.marker.json",
            ),
            (
                ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                entry_root.evidence_sessions_fd,
                f"{entry_manifest.session_id}.wal",
            ),
        ):
            access = _prepare_replay_access_gate(entry_state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            try:
                observation = _named_file_identity_observation(
                    directory_fd,
                    basename,
                )
            except OSError:
                _raise_prepare_operational_read_failure(entry_state)
            phase1_bootstrap_identities_list.append(observation)
            access = _prepare_replay_access_gate(entry_state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            if observation is None:
                file_proof = _identity_file_proof(entry_state, role)
                access = _prepare_replay_access_gate(entry_state)
                if type(access) is ExpertReplayDeniedV1:
                    return access
                sampled = access
                return _close_prepare_with_denial(
                    entry_state,
                    mismatch=(
                        ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
                    ),
                    sampled=sampled,
                    file_proofs=(file_proof,),
                )
        entry_state["phase1_bootstrap_identities"] = tuple(
            phase1_bootstrap_identities_list
        )
        state = entry_state
        phase1_manifest = state["manifest"]
        authorizer = state["authorizer"]
        coordinator = state["coordinator"]
        root = state["root"]
        assert type(phase1_manifest) is SessionManifest
        assert type(authorizer) is ProviderPersistenceAuthorizer
        assert type(coordinator) is RetentionCoordinator
        assert type(root) is _Root
        try:
            sampled = _require_prepare_replay_full_integrity(state)
        except _PrepareReplayDenied as denied:
            return denied.denial
        bootstrap_identities = state.get(
            "phase1_bootstrap_identities"
        )
        if (
            type(bootstrap_identities) is tuple
            and len(bootstrap_identities) == 2
        ):
            current_bootstrap_identities_list = []
            for directory_fd, basename in (
                (
                    root.evidence_markers_fd,
                    f"{phase1_manifest.session_id}.marker.json",
                ),
                (
                    root.evidence_sessions_fd,
                    f"{phase1_manifest.session_id}.wal",
                ),
            ):
                access = _prepare_replay_access_gate(state)
                if type(access) is ExpertReplayDeniedV1:
                    return access
                sampled = access
                try:
                    current_bootstrap_identities_list.append(
                        _named_file_identity_observation(
                            directory_fd,
                            basename,
                        )
                    )
                except OSError:
                    _raise_prepare_operational_read_failure(state)
                access = _prepare_replay_access_gate(state)
                if type(access) is ExpertReplayDeniedV1:
                    return access
                sampled = access
            current_bootstrap_identities = tuple(
                current_bootstrap_identities_list
            )
            for index, role in enumerate(
                (
                    ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                    ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                )
            ):
                current = current_bootstrap_identities[index]
                if (
                    current != bootstrap_identities[index]
                    or not _valid_bootstrap_file_observation(current)
                ):
                    file_proof = _identity_file_proof(state, role)
                    access = _prepare_replay_access_gate(state)
                    if type(access) is ExpertReplayDeniedV1:
                        return access
                    sampled = access
                    return _close_prepare_with_denial(
                        state,
                        mismatch=(
                            ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
                        ),
                        sampled=sampled,
                        file_proofs=(file_proof,),
                    )
        try:
            _require_prepare_replay_full_integrity(state)
            read_capability = coordinator.issue_read_capability(
                persistence_authorizer=authorizer
            )
            sampled = _require_prepare_replay_full_integrity(state)
            scan_reader = JournalReader.open(read_capability=read_capability)
            try:
                sampled = _require_prepare_replay_full_integrity(state)
                records = iter(
                    scan_reader.iter_records(diagnostic_prefix=True)
                )
                try:
                    _require_prepare_replay_full_integrity(state)
                    session_start = next(records)
                except Exception:
                    sampled = _require_prepare_replay_full_integrity(state)
                    raise
                sampled = _require_prepare_replay_full_integrity(state)
                terminal = None
                while True:
                    sampled = _require_prepare_replay_full_integrity(state)
                    try:
                        event = next(records)
                    except StopIteration:
                        sampled = (
                            _require_prepare_replay_full_integrity(state)
                        )
                        break
                    except Exception:
                        sampled = (
                            _require_prepare_replay_full_integrity(state)
                        )
                        raise
                    sampled = _require_prepare_replay_full_integrity(state)
                    if (
                        event.record_kind is RecordKind.CONTROL
                        and event.event_type == "SESSION_HALT"
                    ):
                        if terminal is not None:
                            raise ValueError
                        terminal = event
            finally:
                scan_reader.close()
            sampled = _require_prepare_replay_full_integrity(state)
            if (
                session_start.record_kind is not RecordKind.CONTROL
                or session_start.event_type != "SESSION_START"
            ):
                raise ValueError
            _require_prepare_replay_full_integrity(state)
            replay_result = replay_exact(
                expected_session_manifest_sha256=session_manifest_sha256(
                    phase1_manifest
                ),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            sampled = _require_prepare_replay_full_integrity(state)
            state["phase1_replay_result"] = replay_result
        except _PrepareReplayDenied as denied:
            return denied.denial
        except ExpertReplayAccessDenied:
            raise
        except OSError:
            _raise_prepare_operational_read_failure(state)
        except Exception:
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                sampled=sampled,
            )

        terminal_is_sole_nonexactness = (
            not replay_result.exact_replay
            and replay_result.wal_valid
            and replay_result.scan_issue is not None
            and replay_result.scan_issue.value
            in {"missing_terminal", "halted_terminal"}
            and (
                replay_result.replay_mismatch is None
                or (
                    replay_result.scan_issue.value == "halted_terminal"
                    and replay_result.replay_mismatch.value
                    == "terminal_reason_mismatch"
                )
            )
        )
        if (
            replay_result.replay_mismatch is not None
            and not terminal_is_sole_nonexactness
        ):
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
                sampled=sampled,
            )
        if not replay_result.exact_replay:
            return _close_prepare_with_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN
                ),
                sampled=sampled,
            )

        evidence_error = False
        try:
            evidence_identities = _guarded_phase1_evidence_file_identities(
                root,
                session_manifest=phase1_manifest,
                session_start=session_start,
                gate=lambda: (
                    _require_prepare_replay_snapshot_io_gate(state)
                ),
                byte_gate=lambda: (
                    _require_prepare_replay_snapshot_io_gate(state)
                ),
            )
            evidence = _create_evidence_replay_context_v1(
                schema_version=1,
                session_manifest=phase1_manifest,
                session_manifest_sha256=session_manifest_sha256(
                    phase1_manifest
                ),
                session_start=session_start,
                session_start_record_sha256=canonical_record_sha256(
                    session_start
                ),
                replay_result=replay_result,
                evidence_terminal=terminal,
                evidence_terminal_record_sha256=(
                    None
                    if terminal is None
                    else canonical_record_sha256(terminal)
                ),
                evidence_marker_identity=evidence_identities[0],
                evidence_wal_identity=evidence_identities[1],
            )
            state["evidence"] = evidence
        except _PrepareReplayDenied as denied:
            return denied.denial
        except OSError:
            _raise_prepare_operational_read_failure(state)
        except Exception:
            evidence_error = True
        try:
            sampled = _require_prepare_replay_full_integrity(state)
        except _PrepareReplayDenied as denied:
            return denied.denial
        if evidence_error:
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
                sampled=sampled,
            )

        # Preserve the live-session seal when this root observed creation.
        # On restart there is no in-memory seal, so collect and retain a
        # fresh Phase-1-bound baseline now that the evidence context is
        # authenticated.  The decoded companion manifest is compared with
        # this baseline before any retention classification.
        sealed_environment = root.last_environment
        try:
            current_environment, _, _, _ = _installed_environment(
                root,
                phase1_manifest,
                gate=lambda: (
                    _require_prepare_replay_snapshot_io_gate(state)
                ),
            )
            sampled = _require_prepare_replay_access(state)
        except _PrepareReplayDenied as denied:
            return denied.denial
        except OSError:
            _raise_prepare_operational_read_failure(state)
        except Exception:
            return _close_prepare_with_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
                ),
                sampled=state["_last_sampled_wall_ns"],
            )
        expected_environment = (
            sealed_environment
            if type(sealed_environment) is ExpertCurrentEnvironmentV1
            else current_environment
        )
        root.last_environment = deepcopy(expected_environment)
        state["prepare_expected_environment"] = deepcopy(
            expected_environment
        )
        if current_environment != expected_environment:
            return _close_prepare_with_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
                ),
                sampled=sampled,
            )

        # Items 6-9 that are already provable from the authenticated
        # Phase-1 context must win before the first companion byte.  The
        # access gate establishes authorization/deadline precedence; a
        # second identity observation closes the race opened by constructing
        # the evidence context; and the environment sealed by the live
        # session is compared with the descriptor-relative installation.
        access = _prepare_replay_access_gate(state)
        if type(access) is ExpertReplayDeniedV1:
            return access
        sampled = access
        identity_role = ExpertReplayDiagnosticRoleV1.PHASE1_MARKER
        identity_mismatch = False
        try:
            current_evidence_identities = (
                _guarded_phase1_evidence_file_identities(
                    root,
                    session_manifest=phase1_manifest,
                    session_start=session_start,
                    gate=lambda: (
                        _require_prepare_replay_snapshot_io_gate(state)
                    ),
                    byte_gate=lambda: (
                        _require_prepare_replay_snapshot_io_gate(state)
                    ),
                )
            )
            for index, candidate_role in enumerate(
                (
                    ExpertReplayDiagnosticRoleV1.PHASE1_MARKER,
                    ExpertReplayDiagnosticRoleV1.PHASE1_WAL,
                )
            ):
                identity_role = candidate_role
                if (
                    current_evidence_identities[index]
                    != evidence_identities[index]
                ):
                    raise ValueError
        except _PrepareReplayDenied as denied:
            return denied.denial
        except _ReplayIdentityFailure as error:
            identity_role = error.role
            identity_mismatch = True
        except OSError:
            _raise_prepare_operational_read_failure(state)
        except Exception:
            identity_mismatch = True
        access = _prepare_replay_access_gate(state)
        if type(access) is ExpertReplayDeniedV1:
            return access
        sampled = access
        if identity_mismatch:
            file_proof = _identity_file_proof(state, identity_role)
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            return _close_prepare_with_denial(
                state,
                mismatch=(
                    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
                ),
                sampled=sampled,
                file_proofs=(file_proof,),
            )

        try:
            sampled = _require_prepare_replay_full_integrity(state)
        except _PrepareReplayDenied as denied:
            return denied.denial

        companion_diagnostic_role = (
            ExpertReplayDiagnosticRoleV1.EXPERT_MARKER
        )
        try:
            marker = _decode_expert_marker(
                _read_prepare_replay_named_content(
                    state,
                    root.markers_fd,
                    _marker_basename(phase1_manifest.session_id),
                    16_384,
                    identity_role=(
                        ExpertReplayDiagnosticRoleV1.EXPERT_MARKER
                    ),
                )
            )
        except _PrepareReplayDenied as denied:
            return denied.denial
        except OSError as error:
            if error.errno != errno.ENOENT:
                _raise_prepare_operational_read_failure(state)
            marker_read_invalid = True
        except Exception:
            marker_read_invalid = True
        else:
            marker_read_invalid = False
        if marker_read_invalid:
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            file_proof = _identity_file_proof(
                state,
                companion_diagnostic_role,
            )
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                sampled=sampled,
                file_proofs=(file_proof,),
            )

        access = _prepare_replay_access_gate(state)
        if type(access) is ExpertReplayDeniedV1:
            return access
        sampled = access
        if (
            marker["session_id"] != phase1_manifest.session_id
            or marker["journal_basename"]
            != _journal_basename(phase1_manifest.session_id)
            or marker["reserve_basename"]
            != _reserve_basename(phase1_manifest.session_id)
        ):
            return _close_prepare_semantic_denial_after_gate(
                state,
                (
                    ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH
                ),
            )

        companion_diagnostic_role = (
            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL
        )
        try:
            _require_prepare_replay_full_integrity(state)
            companion_diagnostic_role = (
                ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL
            )
            companion_fd = os.open(
                marker["journal_basename"],
                _OPEN_FILE_READ_FLAGS,
                dir_fd=root.sessions_fd,
            )
            try:
                _require_prepare_replay_full_integrity(state)
                journal_stat = _require_file(companion_fd)
                _require_prepare_replay_full_integrity(state)
                named_journal_stat = os.stat(
                    marker["journal_basename"],
                    dir_fd=root.sessions_fd,
                    follow_symlinks=False,
                )
                _require_prepare_replay_full_integrity(state)
                if not _same_file_identity(
                    journal_stat,
                    named_journal_stat,
                ):
                    raise ValueError("expert_file_replaced")
                journal_identity = _stat_identity_observation(
                    journal_stat
                )
                prior_journal_identity = state.setdefault(
                    "prepare_companion_journal_identity",
                    journal_identity,
                )
                if prior_journal_identity != journal_identity:
                    _raise_prepare_replay_denial(
                        state,
                        mismatch=(
                            ExpertReplayMismatchV1
                            .EVIDENCE_IDENTITY_MISMATCH
                        ),
                        role=(
                            ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL
                        ),
                    )
                header = _prepare_replay_pread(
                    state,
                    companion_fd,
                    offset=0,
                    length=EXPERT_FILE_HEADER_BYTES,
                )
                decode_expert_file_header(header)
                frame, _ = _read_prepare_replay_frame(
                    state,
                    companion_fd,
                    EXPERT_FILE_HEADER_BYTES,
                )
                if frame is None:
                    raise ValueError
                expert_manifest = decode_expert_manifest_frame(frame)
                _require_prepare_replay_full_integrity(state)
            finally:
                _close_root_governed_temporary(
                    root,
                    companion_fd,
                    message="expert_replay_close_uncertain",
                )
        except _PrepareReplayDenied as denied:
            return denied.denial
        except OSError as error:
            if error.errno != errno.ENOENT:
                _raise_prepare_operational_read_failure(state)
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            file_proof = _identity_file_proof(
                state,
                companion_diagnostic_role,
            )
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                sampled=sampled,
                file_proofs=(file_proof,),
            )
        except Exception:
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            file_proof = _identity_file_proof(
                state,
                companion_diagnostic_role,
            )
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                sampled=sampled,
                file_proofs=(file_proof,),
            )

        if expert_manifest.session_id != phase1_manifest.session_id:
            return _close_prepare_semantic_denial_after_gate(
                state,
                (
                    ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH
                ),
            )
        if (
            expert_manifest.evidence_session_manifest_sha256
            != evidence.session_manifest_sha256
            or expert_manifest.evidence_session_start_record_sha256
            != evidence.session_start_record_sha256
            or expert_manifest.provider_id != phase1_manifest.provider_id
            or expert_manifest.product_tier != phase1_manifest.product_tier
            or expert_manifest.source_lineage_id
            != phase1_manifest.source_lineage_id
            or expert_manifest.provider_manifest_file_sha256
            != phase1_manifest.provider_manifest_file_sha256
            or expert_manifest.provider_manifest_canonical_sha256
            != phase1_manifest.provider_manifest_canonical_sha256
            or expert_manifest.entitlement_id_sha256
            != phase1_manifest.entitlement_id_sha256
            or expert_manifest.permission_artifact_sha256
            != phase1_manifest.permission_artifact_sha256
            or expert_manifest.qualification_artifact_sha256
            != phase1_manifest.qualification_artifact_sha256
            or expert_manifest.qualification_trace_sha256
            != phase1_manifest.qualification_trace_sha256
        ):
            return _close_prepare_semantic_denial_after_gate(
                state,
                (
                    ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH
                ),
            )
        expected_environment = state.get(
            "prepare_expected_environment"
        )
        if (
            type(expected_environment)
            is not ExpertCurrentEnvironmentV1
            or expert_manifest.environment != expected_environment
        ):
            return _close_prepare_semantic_denial_after_gate(
                state,
                (
                    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
                ),
            )
        provider_request_binding_sha256 = (
            authorizer.bound_decision.provider_request_binding_sha256
        )
        if type(provider_request_binding_sha256) is not str:
            return _close_prepare_semantic_denial_after_gate(
                state,
                (
                    ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH
                ),
            )
        expected_retention_values: dict[str, object] = {
            "schema_version": 1,
            "session_id": phase1_manifest.session_id,
            "evidence_session_manifest_sha256": (
                session_manifest_sha256(phase1_manifest)
            ),
            "provider_request_binding_sha256": (
                provider_request_binding_sha256
            ),
            "permission_artifact_sha256": (
                phase1_manifest.permission_artifact_sha256
            ),
            "qualification_artifact_sha256": (
                phase1_manifest.qualification_artifact_sha256
            ),
            "qualification_trace_sha256": (
                phase1_manifest.qualification_trace_sha256
            ),
            "retention_delete_by_ns": (
                phase1_manifest.required_retention_until_ns
            ),
            "access_expires_at_ns": phase1_manifest.access_expires_at_ns,
            "analysis_expires_at_ns": (
                phase1_manifest.analysis_expires_at_ns
            ),
        }
        expected_retention_sha256 = (
            compute_expert_retention_binding_sha256(
                **expected_retention_values
            )
        )
        expected_retention = expert_manifest.retention
        if (
            expert_manifest.provider_request_binding_sha256
            != expected_retention_values[
                "provider_request_binding_sha256"
            ]
            or expected_retention.schema_version != 1
            or expected_retention.session_id
            != expected_retention_values["session_id"]
            or expected_retention.evidence_session_manifest_sha256
            != expected_retention_values[
                "evidence_session_manifest_sha256"
            ]
            or expected_retention.provider_request_binding_sha256
            != expected_retention_values[
                "provider_request_binding_sha256"
            ]
            or expected_retention.permission_artifact_sha256
            != expected_retention_values["permission_artifact_sha256"]
            or expected_retention.qualification_artifact_sha256
            != expected_retention_values[
                "qualification_artifact_sha256"
            ]
            or expected_retention.qualification_trace_sha256
            != expected_retention_values[
                "qualification_trace_sha256"
            ]
            or expected_retention.retention_delete_by_ns
            != expected_retention_values["retention_delete_by_ns"]
            or expected_retention.access_expires_at_ns
            != expected_retention_values["access_expires_at_ns"]
            or expected_retention.analysis_expires_at_ns
            != expected_retention_values["analysis_expires_at_ns"]
            or expected_retention.retention_binding_sha256
            != expected_retention_sha256
        ):
            return _close_prepare_semantic_denial_after_gate(
                state,
                ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
            )
        if (
            marker["evidence_session_manifest_sha256"]
            != evidence.session_manifest_sha256
            or marker["evidence_session_start_record_sha256"]
            != evidence.session_start_record_sha256
            or marker["provider_request_binding_sha256"]
            != provider_request_binding_sha256
            or marker["retention_binding_sha256"]
            != expected_retention_sha256
            or marker["retention_delete_by_ns"]
            != phase1_manifest.required_retention_until_ns
            or marker["expert_manifest_sha256"]
            != expert_manifest.manifest_sha256
        ):
            return _close_prepare_semantic_denial_after_gate(
                state,
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
            )

        try:
            _require_prepare_replay_full_integrity(state)
            companion_reader = _issue_prepare_replay_companion_reader(
                state,
                expert_manifest,
                marker,
            )
            state["companion_reader"] = companion_reader
            companion_reader_state = _READERS.get(companion_reader)
            if type(companion_reader_state) is not _Reader:
                raise ValueError("expert_replay_companion_invalid")
            companion_reader_state.replay_authority = authority
            _require_prepare_replay_full_integrity(state)
            read_expert_manifest(companion_reader)
            _require_prepare_replay_full_integrity(state)
            pair_capability = coordinator.issue_read_capability(
                persistence_authorizer=authorizer
            )
            _require_prepare_replay_full_integrity(state)
            phase1_reader = JournalReader.open(
                read_capability=pair_capability
            )
            state["phase1_reader"] = phase1_reader
            _require_prepare_replay_full_integrity(state)
            phase1_records = iter(phase1_reader.iter_records())
            try:
                _require_prepare_replay_full_integrity(state)
                first_phase1_record = next(phase1_records)
            except Exception:
                _require_prepare_replay_full_integrity(state)
                raise
            _require_prepare_replay_full_integrity(state)
            if first_phase1_record != session_start:
                raise ValueError
            state["expert_manifest"] = expert_manifest
            state["expected_environment"] = expert_manifest.environment
            companion_identities = (
                _guarded_expert_companion_file_identities(
                    root,
                    manifest=expert_manifest,
                    gate=lambda: (
                        _require_prepare_replay_snapshot_io_gate(state)
                    ),
                    byte_gate=lambda: (
                        _require_prepare_replay_snapshot_io_gate(state)
                    ),
                )
            )
            _require_prepare_replay_full_integrity(state)
            for identity, expected, role in (
                (
                    companion_identities[0],
                    state.get("prepare_companion_marker_identity"),
                    ExpertReplayDiagnosticRoleV1.EXPERT_MARKER,
                ),
                (
                    companion_identities[1],
                    state.get("prepare_companion_journal_identity"),
                    ExpertReplayDiagnosticRoleV1.EXPERT_JOURNAL,
                ),
            ):
                observed = (
                    identity.device,
                    identity.inode,
                    identity.uid,
                    identity.mode,
                    identity.link_count,
                    identity.size,
                    identity.mtime_ns,
                    identity.ctime_ns,
                )
                normalized_expected = (
                    expected[0],
                    expected[1],
                    expected[2],
                    stat.S_IMODE(expected[3]),
                    expected[4],
                    expected[5],
                    expected[6],
                    expected[7],
                ) if type(expected) is tuple and len(expected) == 8 else None
                if observed != normalized_expected:
                    _raise_prepare_replay_denial(
                        state,
                        mismatch=(
                            ExpertReplayMismatchV1
                            .EVIDENCE_IDENTITY_MISMATCH
                        ),
                        role=role,
                    )
            state["identity_set"] = (
                evidence_identities + companion_identities
            )
            state["phase1_records"] = phase1_records
            state["last_phase1_ingest_seq"] = session_start.ingest_seq
            state["evidence_terminal"] = terminal
            ready = _create_expert_replay_begin_ready_v1(
                evidence=deepcopy(evidence),
                manifest=deepcopy(expert_manifest),
            )
            _require_prepare_replay_full_integrity(state)
            _replay_state(authority, "new")
            state["state"] = "begin_ready"
            return ready
        except ExpertReplayAccessDenied:
            denial = state.get("denial")
            if type(denial) is not ExpertReplayDeniedV1:
                raise
            state["closed"] = True
            state["state"] = "denied_closed"
            return denial
        except _PrepareReplayDenied as denied:
            return denied.denial
        except OSError:
            _raise_prepare_operational_read_failure(state)
        except Exception:
            access = _prepare_replay_access_gate(state)
            if type(access) is ExpertReplayDeniedV1:
                return access
            sampled = access
            return _close_prepare_with_denial(
                state,
                mismatch=ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
                sampled=sampled,
            )


def _issue_replay_authorization(
    authority: ExpertReplayConstructionAuthorityV1,
    *,
    source_state: str,
    target_state: str,
    operation: str,
    expected_parent: int | None,
) -> RetentionReplayAuthorizationV1:
    state = _replay_cached_state(authority, source_state)
    expert_manifest = state["expert_manifest"]
    evidence = state["evidence"]
    assert type(expert_manifest) is ExpertSessionManifestV1
    expected_identities = state.get("identity_set")
    if type(expected_identities) is not tuple or len(expected_identities) != 4:
        raise ValueError("expert_replay_identity_set_invalid")
    _replay_full_integrity_gate(state)
    sampled = state.get("_last_sampled_wall_ns")
    if type(sampled) is not int:
        raise ValueError("expert_replay_sample_invalid")
    evidence_identities = expected_identities[:2]
    companion_identities = expected_identities[2:]
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": expert_manifest.session_id,
        "authorization_sequence": state["sequence"],
        "authorized_operation": operation,
        "expected_parent_ingest_seq": expected_parent,
        "evidence_session_manifest_sha256": (
            expert_manifest.evidence_session_manifest_sha256
        ),
        "evidence_session_start_record_sha256": (
            expert_manifest.evidence_session_start_record_sha256
        ),
        "evidence_terminal_record_sha256": (
            evidence.evidence_terminal_record_sha256
        ),
        "expert_manifest_sha256": expert_manifest.manifest_sha256,
        "retention_binding_sha256": (
            expert_manifest.retention.retention_binding_sha256
        ),
        "provider_request_binding_sha256": (
            expert_manifest.provider_request_binding_sha256
        ),
        "permission_artifact_sha256": (
            expert_manifest.permission_artifact_sha256
        ),
        "qualification_artifact_sha256": (
            expert_manifest.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            expert_manifest.qualification_trace_sha256
        ),
        "evidence_marker_identity": evidence_identities[0],
        "evidence_wal_identity": evidence_identities[1],
        "companion_marker_identity": companion_identities[0],
        "companion_journal_identity": companion_identities[1],
        "common_deadline_ns": state["deadline"],
        "final_sampled_wall_ns": sampled,
    }
    token = _create_retention_replay_authorization_v1(
        **values,
        authorization_sha256=compute_retention_replay_authorization_sha256(
            **values
        ),
    )
    _replay_full_integrity_gate(state)
    state["outstanding"] = token
    state["outstanding_authorization_sha256"] = (
        token.authorization_sha256
    )
    state["state"] = target_state
    return token


def issue_begin_replay_authorization(
    authority: ExpertReplayConstructionAuthorityV1,
) -> RetentionReplayAuthorizationV1:
    with _LOCK:
        return _issue_replay_authorization(
            authority,
            source_state="begin_ready",
            target_state="begin_auth_outstanding",
            operation="begin",
            expected_parent=None,
        )


def acknowledge_begin_replay(
    authority: ExpertReplayConstructionAuthorityV1,
    *,
    authorization: RetentionReplayAuthorizationV1,
    accumulator: ExpertReplayAccumulatorV1,
) -> None:
    if (
        type(authorization) is not RetentionReplayAuthorizationV1
        or type(accumulator) is not ExpertReplayAccumulatorV1
    ):
        raise TypeError("replay_begin_ack")
    with _LOCK:
        state = _replay_cached_state(
            authority,
            "begin_auth_outstanding",
        )
        expected: ExpertReplayAccumulatorV1 | None = None
        try:
            authorization._validate()
            ExpertReplayAccumulatorV1.__post_init__(accumulator)
            synchronization = accumulator.state.synchronization
            expert_manifest = state["expert_manifest"]
            if (
                state["outstanding"] is not authorization
                or state.get("outstanding_authorization_sha256")
                != authorization.authorization_sha256
                or accumulator.last_authorization_sha256
                != authorization.authorization_sha256
                or accumulator.current_environment
                != state["expected_environment"]
                or synchronization.universe_sha256
                != expert_manifest.match_binding_universe_sha256
                or synchronization.sync_policy_sha256
                != expert_manifest.sync_policy_sha256
            ):
                raise ValueError
            expected = begin_expert_replay(
                manifest=expert_manifest,
                current_environment=accumulator.current_environment,
                universe=synchronization.universe,
                policy=synchronization.policy,
                evidence=state["evidence"],
                authorization=authorization,
            )
        except Exception:
            expected = None
        _replay_full_integrity_gate(state)
        if (
            expected is None
            or expected != accumulator
        ):
            abort_expert_replay_construction(authority)
            raise ValueError("replay_begin_ack_invalid")
        state["outstanding"] = None
        state.pop("outstanding_authorization_sha256", None)
        state["accumulator"] = deepcopy(expected)
        state["sequence"] = 1
        state["state"] = (
            "begin_diagnostic"
            if accumulator.mismatch is not None
            else "pair_empty"
        )


def _next_replay_phase1_parent(
    state: dict[str, object],
) -> PersistedEvent | None:
    def deny_read_failure(error: Exception) -> None:
        _replay_full_integrity_gate(state)
        sampled = _replay_access_gate(state)
        _raise_contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
                if isinstance(error, RetentionError)
                else ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
            ),
            sampled=sampled,
        )

    def deny_consumed_record_failure(error: Exception) -> None:
        sampled = state.get("_last_sampled_wall_ns")
        if type(sampled) is not int:
            raise ValueError("expert_replay_sample_invalid")
        _raise_contextual_replay_denial(
            state,
            mismatch=(
                ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH
                if isinstance(error, RetentionError)
                else ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH
            ),
            sampled=sampled,
        )

    def deny_invalid_record(message: str) -> None:
        deny_consumed_record_failure(ValueError(message))
        raise AssertionError("unreachable")

    records = state["phase1_records"]
    manifest = state["manifest"]
    _replay_full_integrity_gate(state)
    while True:
        _replay_access_gate(state)
        try:
            event = next(records)
        except StopIteration:
            _replay_full_integrity_gate(state)
            state["phase1_physical_eof"] = True
            return None
        except Exception as error:
            deny_read_failure(error)
            raise AssertionError("unreachable")
        if type(event) is not PersistedEvent:
            deny_invalid_record("replay_phase1_record_invalid")
        try:
            PersistedEvent.__post_init__(event)
        except Exception as error:
            deny_consumed_record_failure(error)
            raise AssertionError("unreachable")
        last_ingest_seq = state.get("last_phase1_ingest_seq", 1)
        if (
            event.session_id != manifest.session_id
            or event.ingest_seq != last_ingest_seq + 1
        ):
            deny_invalid_record("replay_phase1_record_invalid")
        if event.record_kind is RecordKind.RAW:
            if event.parent_ingest_seq is not None:
                deny_invalid_record("replay_phase1_record_invalid")
            _replay_access_gate(state)
            state["last_phase1_ingest_seq"] = event.ingest_seq
            state["last_evidence_parent_ingest_seq"] = event.ingest_seq
            return event
        if event.record_kind is RecordKind.DERIVED:
            if (
                event.parent_ingest_seq
                != state.get("last_evidence_parent_ingest_seq")
            ):
                deny_invalid_record("replay_phase1_record_invalid")
            _replay_access_gate(state)
            state["last_phase1_ingest_seq"] = event.ingest_seq
            continue
        if (
            event.record_kind is RecordKind.CONTROL
            and event.event_type == "SESSION_HALT"
        ):
            expected_terminal = state.get("evidence_terminal")
            if (
                type(expected_terminal) is PersistedEvent
                and event != expected_terminal
            ):
                deny_invalid_record("replay_phase1_terminal_invalid")
            _replay_access_gate(state)
            state["last_phase1_ingest_seq"] = event.ingest_seq
            try:
                next(records)
            except StopIteration:
                _replay_full_integrity_gate(state)
                state["phase1_terminal_seen"] = True
                state["phase1_physical_eof"] = True
                return None
            except Exception as error:
                deny_read_failure(error)
                raise AssertionError("unreachable")
            deny_invalid_record("replay_phase1_trailing_record")
        deny_invalid_record("replay_phase1_record_invalid")


def read_next_replay_evidence_parent(
    authority: ExpertReplayConstructionAuthorityV1,
) -> PersistedEvent | None:
    with _LOCK:
        state = _replay_cached_state(authority, "pair_empty")
        event = _next_replay_phase1_parent(state)
        _replay_access_gate(state)
        if event is None:
            state["state"] = "evidence_eof_ready"
            return None
        state["evidence_index"] = state["evidence_index"] + 1
        state["current_parent_record_sha256"] = (
            _replay_parent_record_sha256(event)
        )
        state["current_parent"] = event
        state["state"] = "evidence_parent_ready"
        return event


def read_next_replay_companion_group(
    authority: ExpertReplayConstructionAuthorityV1,
) -> tuple[ExpertJournalGroupV1, tuple[bytes, ...]] | None:
    with _LOCK:
        state = _replay_cached_state(
            authority,
            "evidence_parent_ready",
            "evidence_eof_ready",
        )
        if state["state"] == "evidence_parent_ready":
            try:
                _validated_replay_parent_snapshot(state)
            except Exception:
                _replay_full_integrity_gate(state)
                abort_expert_replay_construction(authority)
                raise ValueError(
                    "expert_replay_authority_invalid"
                ) from None
        reader = state["companion_reader"]
        item_seals: tuple[
            str,
            tuple[tuple[int, str], ...],
        ] | None = None
        try:
            item = read_next_expert_group(reader)
            if (
                item is not None
                and state["state"] == "evidence_parent_ready"
            ):
                item_seals = _replay_group_seals(item)
        except OSError:
            _raise_replay_operational_read_failure(state)
        except ValueError:
            state["proven_mismatch"] = (
                ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
            )
            state["proven_mismatch_sampled"] = state.get(
                "_last_sampled_wall_ns"
            )
            _replay_full_integrity_gate(state)
            reader_state = _READERS.get(reader)
            if type(reader_state) is not _Reader:
                _close_replay_owned_readers(state)
                state["outstanding"] = None
                state["closed"] = True
                state["state"] = "aborted_closed"
                raise ValueError("expert_replay_companion_invalid") from None
            try:
                _replay_access_gate(state)
                file_size = os.fstat(reader_state.fd).st_size
                _replay_access_gate(state)
            except ExpertReplayAccessDenied:
                raise
            except OSError:
                _raise_replay_operational_read_failure(state)
            file_size = max(file_size, reader_state.last_good_offset)
            terminal = reader_state.terminal
            summary = ExpertJournalScanSummaryV1(
                schema_version=1,
                file_size=file_size,
                last_good_offset=reader_state.last_good_offset,
                last_frame_sequence=reader_state.last_frame_sequence,
                group_count=reader_state.group_count,
                record_count=reader_state.record_count,
                terminal_clean=False,
                issue=ExpertJournalScanIssueV1.CORRUPT_TAIL,
                journal_valid=False,
            )
            _replay_access_gate(state)
            _pop_replay_pair(state)
            state["finish_material"] = (terminal, summary)
            state["state"] = "companion_scan_invalid"
            return None
        _replay_access_gate(state)
        if state["state"] == "evidence_parent_ready":
            if item is None:
                _pop_replay_pair(state)
                state["cardinality_side"] = "evidence"
                state["state"] = "cardinality_mismatch"
                return None
            assert item_seals is not None
            state["current_group"] = item
            state["current_group_sha256"] = item_seals[0]
            state["current_payload_seals"] = item_seals[1]
            state["state"] = "pair_complete"
            return item
        if item is None:
            state["state"] = "both_eof"
            return None
        state["cardinality_side"] = "companion"
        state["state"] = "cardinality_mismatch"
        return item


def issue_parent_group_replay_authorization(
    authority: ExpertReplayConstructionAuthorityV1,
) -> RetentionReplayAuthorizationV1:
    with _LOCK:
        state = _replay_cached_state(authority, "pair_complete")
        try:
            parent, _ = _validated_replay_pair_snapshots(state)
        except Exception:
            _replay_full_integrity_gate(state)
            abort_expert_replay_construction(authority)
            raise ValueError("expert_replay_authority_invalid") from None
        return _issue_replay_authorization(
            authority,
            source_state="pair_complete",
            target_state="parent_auth_outstanding",
            operation="parent_group",
            expected_parent=parent.ingest_seq,
        )


def acknowledge_parent_group_replay(
    authority: ExpertReplayConstructionAuthorityV1,
    *,
    authorization: RetentionReplayAuthorizationV1,
    accumulator: ExpertReplayAccumulatorV1,
) -> None:
    if (
        type(authorization) is not RetentionReplayAuthorizationV1
        or type(accumulator) is not ExpertReplayAccumulatorV1
    ):
        raise TypeError("replay_parent_ack")
    with _LOCK:
        state = _replay_cached_state(
            authority,
            "parent_auth_outstanding",
        )
        expected: ExpertReplayAccumulatorV1 | None = None
        try:
            authorization._validate()
            current_parent, current_group = (
                _validated_replay_pair_snapshots(state)
            )
            if (
                state["outstanding"] is not authorization
                or state.get("outstanding_authorization_sha256")
                != authorization.authorization_sha256
                or accumulator.last_authorization_sha256
                != authorization.authorization_sha256
                or type(current_group) is not tuple
                or len(current_group) != 2
            ):
                raise ValueError
            expected = replay_expert_parent_group(
                state["accumulator"],
                authorization=authorization,
                parent=current_parent,
                stored_group=current_group[0],
                stored_payloads=current_group[1],
            )
        except Exception:
            expected = None
        _replay_full_integrity_gate(state)
        if (
            expected is None
            or expected != accumulator
        ):
            abort_expert_replay_construction(authority)
            raise ValueError("replay_parent_ack_invalid")
        state["sequence"] = state["sequence"] + 1
        state["outstanding"] = None
        state.pop("outstanding_authorization_sha256", None)
        state["accumulator"] = deepcopy(expected)
        _pop_replay_pair(state)
        state["state"] = (
            "begin_diagnostic"
            if accumulator.mismatch is not None
            else "pair_empty"
        )


def read_replay_finish_material(
    authority: ExpertReplayConstructionAuthorityV1,
) -> tuple[ExpertSessionTerminalV1 | None, ExpertJournalScanSummaryV1]:
    with _LOCK:
        state = _replay_cached_state(
            authority,
            "begin_diagnostic",
            "both_eof",
            "cardinality_mismatch",
            "companion_scan_invalid",
        )
        reader = state["companion_reader"]
        cached_material = state["state"] == "companion_scan_invalid"
        reader_state = _READERS.get(reader)
        terminal_already_cached = (
            type(reader_state) is _Reader
            and reader_state.terminal is not None
        )
        if cached_material:
            finish_material = state.get("finish_material")
            if (
                type(finish_material) is not tuple
                or len(finish_material) != 2
                or (
                    finish_material[0] is not None
                    and type(finish_material[0])
                    is not ExpertSessionTerminalV1
                )
                or type(finish_material[1])
                is not ExpertJournalScanSummaryV1
            ):
                _close_replay_owned_readers(state)
                state["outstanding"] = None
                state["closed"] = True
                state["state"] = "aborted_closed"
                raise ValueError("expert_replay_finish_material_invalid")
            terminal, summary = finish_material
        else:
            try:
                if state["state"] == "begin_diagnostic":
                    terminal, summary = (
                        _read_replay_begin_diagnostic_material(reader)
                    )
                elif state["state"] == "cardinality_mismatch":
                    if state.get("cardinality_side") == "evidence":
                        while _next_replay_phase1_parent(state) is not None:
                            pass
                    elif state.get("cardinality_side") == "companion":
                        while read_next_expert_group(reader) is not None:
                            pass
                    else:
                        raise ValueError("replay_cardinality_state_invalid")
                    terminal, summary = read_expert_terminal_and_summary(
                        reader
                    )
                else:
                    terminal, summary = read_expert_terminal_and_summary(
                        reader
                    )
            except OSError:
                _raise_replay_operational_read_failure(state)
            except ValueError:
                state["proven_mismatch"] = (
                    ExpertReplayMismatchV1.COMPANION_SCAN_INVALID
                )
                state["proven_mismatch_sampled"] = state.get(
                    "_last_sampled_wall_ns"
                )
                _replay_access_gate(state)
                reader_state = _READERS.get(reader)
                if type(reader_state) is not _Reader:
                    raise
                terminal = reader_state.terminal
                _replay_access_gate(state)
                try:
                    size = os.fstat(reader_state.fd).st_size
                except OSError:
                    _raise_replay_operational_read_failure(state)
                _replay_access_gate(state)
                summary = ExpertJournalScanSummaryV1(
                    schema_version=1,
                    file_size=size,
                    last_good_offset=reader_state.last_good_offset,
                    last_frame_sequence=reader_state.last_frame_sequence,
                    group_count=reader_state.group_count,
                    record_count=reader_state.record_count,
                    terminal_clean=False,
                    issue=ExpertJournalScanIssueV1.CORRUPT_TAIL,
                    journal_valid=False,
                )
        if cached_material or terminal_already_cached:
            _replay_full_integrity_gate(state)
        else:
            _replay_access_gate(state)
        state["finish_material"] = (
            deepcopy(terminal),
            deepcopy(summary),
        )
        state["state"] = "finish_ready"
        return deepcopy(terminal), deepcopy(summary)


def issue_finish_replay_authorization(
    authority: ExpertReplayConstructionAuthorityV1,
) -> RetentionReplayAuthorizationV1:
    with _LOCK:
        return _issue_replay_authorization(
            authority,
            source_state="finish_ready",
            target_state="finish_auth_outstanding",
            operation="finish",
            expected_parent=None,
        )


def acknowledge_finish_replay(
    authority: ExpertReplayConstructionAuthorityV1,
    *,
    authorization: RetentionReplayAuthorizationV1,
    result: ExpertReplayResultV1,
) -> None:
    if (
        type(authorization) is not RetentionReplayAuthorizationV1
        or type(result) is not ExpertReplayResultV1
    ):
        raise TypeError("replay_finish_ack")
    with _LOCK:
        state = _replay_cached_state(
            authority,
            "finish_auth_outstanding",
        )
        expected: ExpertReplayResultV1 | None = None
        try:
            authorization._validate()
            ExpertReplayResultV1.__post_init__(result)
            finish_material = state["finish_material"]
            if (
                state["outstanding"] is not authorization
                or state.get("outstanding_authorization_sha256")
                != authorization.authorization_sha256
                or result.final_authorization_sha256
                != authorization.authorization_sha256
                or type(finish_material) is not tuple
                or len(finish_material) != 2
            ):
                raise ValueError
            expected = finish_expert_replay(
                state["accumulator"],
                final_authorization=authorization,
                companion_terminal=finish_material[0],
                companion_scan=finish_material[1],
            )
        except Exception:
            expected = None
        _replay_full_integrity_gate(state)
        if (
            expected is None
            or expected != result
        ):
            abort_expert_replay_construction(authority)
            raise ValueError("replay_finish_ack_invalid")
        state["outstanding"] = None
        state.pop("outstanding_authorization_sha256", None)
        state["state"] = "finish_closing"
        try:
            _close_replay_owned_readers(state)
        except _ReplayCloseUncertain:
            state.pop("accumulator", None)
            state.pop("finish_material", None)
            state["closed"] = True
            state["state"] = "aborted_closed"
            raise OSError("expert_replay_close_uncertain") from None
        state.pop("accumulator", None)
        state.pop("finish_material", None)
        state["closed"] = True
        state["state"] = "consumed_closed"


def take_expert_replay_denial(
    authority: ExpertReplayConstructionAuthorityV1,
) -> ExpertReplayDeniedV1:
    if type(authority) is not ExpertReplayConstructionAuthorityV1:
        raise TypeError("authority")
    with _LOCK:
        state = _replay_authority_state(authority, "terminal_denied")
        denial = state.get("denial")
        if type(denial) is not ExpertReplayDeniedV1:
            raise ValueError("expert_replay_denial_invalid")
        state["closed"] = True
        state["state"] = "denied_closed"
        return denial


def abort_expert_replay_construction(
    authority: ExpertReplayConstructionAuthorityV1,
) -> None:
    if type(authority) is not ExpertReplayConstructionAuthorityV1:
        raise TypeError("authority")
    with _LOCK:
        state = _REPLAYS.get(authority)
        if state is None or state["closed"]:
            raise ValueError("expert_replay_authority_invalid")
        if (
            state.get("owner_pid") != os.getpid()
            or state.get("owner_thread")
            is not threading.current_thread()
        ):
            raise ValueError("expert_replay_authority_invalid")
        _close_replay_owned_readers(state)
        state["outstanding"] = None
        state["closed"] = True
        state["state"] = "aborted_closed"
