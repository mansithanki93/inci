"""Owner-bound serial controller for the diagnostic expert companion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from os import getpid
from threading import RLock, current_thread
import weakref

from inci_tennis_expert.contracts import (
    BindingUniverse,
    DurableExpertAppendReceiptV1,
    DurableExpertEmergencyReceiptV1,
    DurableExpertTerminalReceiptV1,
    ExpertEventKindV1,
    ExpertJournalCursorV1,
    ExpertJournalGroupV1,
    ExpertJournalRecordV1,
    ExpertParentEvidenceV1,
    ExpertPayloadDescriptorV1,
    ExpertProviderDomainBindingV1,
    ExpertReductionV1,
    ExpertRejectedObservationV1,
    ExpertRejectReasonV1,
    ExpertRetentionBindingV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertStateV1,
    ExpertTraceStepV1,
    SyncPolicy,
    canonical_expert_bytes,
    compute_expert_journal_group_sha256,
    compute_expert_journal_record_sha256,
    compute_expert_provider_domain_binding_sha256,
    compute_expert_provider_source_lineage_sha256,
    compute_expert_retention_binding_sha256,
    compute_expert_trace_step_sha256,
    expert_contract_sha256,
    expert_state_sha256,
    expert_trace_seed_sha256,
)
from inci_tennis_expert.journal_codec import (
    EXPERT_EMERGENCY_RESERVE_BYTES,
    EXPERT_FILE_HEADER_BYTES,
    MAX_EXPERT_FRAME_BYTES,
    MAX_EXPERT_TERMINAL_FRAME_BYTES,
    encode_expert_group_frame,
    encode_expert_manifest_frame,
    encode_expert_terminal_frame,
    validate_expert_group_against_cursor,
    validate_expert_terminal_against_cursor,
)
from inci_tennis_expert.match_binding import binding_universe_sha256
from inci_tennis_expert.observation import (
    normalize_expert_parent,
    prove_expert_capacity,
)
from inci_tennis_expert.reducer import reduce_expert_parent
from inci_tennis_expert.state import initial_expert_state
from inci_tennis_expert.synchronizer import (
    synchronization_session_from_artifacts,
)
from inci_tennis_io.facade import (
    abort_expert_writer,
    acknowledge_expert_publication,
    append_expert_emergency_group_and_terminal,
    append_expert_group,
    append_expert_terminal,
    build_aligned_expert_terminal,
    collect_expert_current_environment,
    create_expert_journal,
    issue_expert_append_permit,
    issue_expert_emergency_append_permit,
    issue_expert_environment_collection_authority,
    issue_expert_terminal_permit,
    prove_expert_live_evidence_tail,
)
from inci_tennis_io.ports import (
    ExpertJournalRootAuthorityV1,
    ExpertJournalWriteCapabilityV1,
    ExpertLiveAuthorizationDenied,
    ExpertPrewriteCapacityError,
)
from tennis_v1.codec import canonical_record_sha256
from tennis_v1.events import (
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SessionManifest,
    SourceKind,
)
from tennis_v1.ingress import (
    BoundedIngress,
    DurableEvidenceTerminalV1,
    DurableIngressParentV1,
)
from tennis_v1.retention import RetentionCoordinator
from tennis_v1.sequencer import (
    EventRuntime,
    ProviderPersistenceAuthorizer,
    WrongOwnerThread,
)
from tennis_v1.session import (
    canonical_session_manifest_bytes,
    session_manifest_sha256,
)
from tennis_v1.state import initial_state


_RESULT = tuple[
    ExpertStateV1,
    ExpertJournalCursorV1,
    ExpertSessionTerminalV1 | None,
]

_SIGNED_63_MAX = 9_223_372_036_854_775_807

_CONTROLLER_ACTIVE = "ACTIVE"
_CONTROLLER_EMERGENCY_PENDING = "EMERGENCY_PENDING"
_CONTROLLER_TERMINAL = "TERMINAL"
_CONTROLLER_HALTED_UNCLEAN = "HALTED_UNCLEAN"
_CONTROLLER_DURABILITY_UNCERTAIN_HALTED = (
    "DURABILITY_UNCERTAIN_HALTED"
)

_PENDING_FRESH = "FRESH"
_PENDING_COMMIT_RESERVED = "COMMIT_RESERVED"
_PENDING_PUBLISHED = "PUBLISHED"
_PENDING_PUBLISHED_PROOF_UNAVAILABLE_CLOSED = (
    "PUBLISHED_PROOF_UNAVAILABLE_CLOSED"
)
_PENDING_PUBLICATION_FAILED_CLOSED = "PUBLICATION_FAILED_CLOSED"
_PENDING_ABORTED_NONPUBLICATION = "ABORTED_NONPUBLICATION"
_PENDING_ABORT_FAILED_CLOSED = "ABORT_FAILED_CLOSED"

_SCOPE_ACTIVE = "ACTIVE"
_SCOPE_CLEARED = "CLEARED"
_SCOPE_RESERVATION_COMMITTED = "RESERVATION_COMMITTED"
_SCOPE_PUBLISHED_CLOSED = "PUBLISHED_CLOSED"
_SCOPE_APPEND_FAILED_CLOSED = "APPEND_FAILED_CLOSED"

_ACK_ACTIVE = "ACTIVE"
_ACK_BOTH_CLAIMED_CLOSED = "BOTH_CLAIMED_CLOSED"
_ACK_DISCARDED_LEGACY_CLOSED = "DISCARDED_LEGACY_CLOSED"
_PROOF_ISSUED = "ISSUED"
_PROOF_PROJECTION_CONSUMED = "PROJECTION_CONSUMED"

_EXPERT_CONTROLLER_IDENTITY_DOMAIN_V1 = (
    "INCI-EXPERT-CONTROLLER-IDENTITY-V1"
)
_CAPACITY_DENIAL_OBSERVATION_DOMAIN_V1 = (
    "INCI-DURABLE-COMPANION-CAPACITY-DENIAL-OBSERVATION-V1"
)
_PUBLICATION_ACK_DOMAIN_V1 = (
    "INCI-DURABLE-COMPANION-PUBLICATION-ACK-V1"
)
_PENDING_EMERGENCY_DOMAIN_V1 = (
    "INCI-PENDING-DURABLE-COMPANION-EMERGENCY-V1"
)
_EMERGENCY_PUBLICATION_DOMAIN_V1 = (
    "INCI-DURABLE-COMPANION-EMERGENCY-PUBLICATION-V1"
)
_PENDING_ABORT_RECEIPT_DOMAIN_V1 = (
    "INCI-PENDING-DURABLE-COMPANION-EMERGENCY-ABORT-RECEIPT-V1"
)


class _OpaqueControllerValueV1:
    __slots__ = ()

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(f"{type(self).__name__} is privately issued")

    def __setattr__(self, _: str, __: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    def __copy__(self):
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __reduce__(self):
        raise TypeError(f"{type(self).__name__} cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError(f"{type(self).__name__} cannot be pickled")


class ExpertControllerIdentityV1(_OpaqueControllerValueV1):
    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "expert_manifest_sha256",
        "owner_pid",
        "allocation_coordinate",
        "controller_identity_sha256",
    )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("ExpertControllerIdentityV1 cannot be subclassed")


class DurableCompanionCapacityDenialObservationV1(
    _OpaqueControllerValueV1
):
    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "durable_parent_envelope_sha256",
        "candidate_state_sha256",
        "candidate_cursor_sha256",
        "requested_bytes",
        "available_bytes",
        "emergency_reserve_bytes",
        "publication_epoch",
        "observation_sha256",
    )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "DurableCompanionCapacityDenialObservationV1 cannot be subclassed"
        )


class DurableCompanionPublicationAckV1(_OpaqueControllerValueV1):
    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "durable_parent_envelope_sha256",
        "append_receipt_sha256",
        "candidate_state_sha256",
        "candidate_cursor_sha256",
        "publication_epoch",
        "ack_sha256",
    )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "DurableCompanionPublicationAckV1 cannot be subclassed"
        )


class PendingDurableCompanionEmergencyV1(_OpaqueControllerValueV1):
    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "durable_parent_envelope_sha256",
        "candidate_state_sha256",
        "candidate_cursor_sha256",
        "capacity_denial_sha256",
        "publication_epoch",
        "pending_sha256",
    )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "PendingDurableCompanionEmergencyV1 cannot be subclassed"
        )


class DurableCompanionEmergencyPublicationProofV1(
    _OpaqueControllerValueV1
):
    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "durable_parent_envelope_sha256",
        "group_receipt_sha256",
        "terminal_receipt_sha256",
        "group_sha256",
        "terminal_sha256",
        "candidate_state_sha256",
        "candidate_cursor_sha256",
        "publication_epoch",
        "proof_sha256",
    )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "DurableCompanionEmergencyPublicationProofV1 cannot be subclassed"
        )


class PendingDurableCompanionEmergencyAbortReceiptV1(
    _OpaqueControllerValueV1
):
    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "controller_identity_sha256",
        "pending_sha256",
        "capacity_denial_sha256",
        "abort_reason",
        "publication_epoch",
        "receipt_sha256",
    )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "PendingDurableCompanionEmergencyAbortReceiptV1 cannot be subclassed"
        )


class PendingEmergencyAbortReasonV1(str, Enum):
    LEGACY_PROCESS_ONE_CAPACITY_DENIAL = (
        "legacy_process_one_capacity_denial"
    )
    RECORDED_CAPACITY_CONTRACT_VIOLATION = (
        "recorded_capacity_contract_violation"
    )
    CALLER_CLOSE_WITH_PENDING = "caller_close_with_pending"


class ExpertCapacityExceeded(RuntimeError):
    def __init__(self) -> None:
        super().__init__("expert_legacy_process_one_capacity_denied")


class _PrivateControllerRecordV1:
    __slots__ = ()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    def __copy__(self):
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError(f"{type(self).__name__} cannot be copied")

    def __reduce__(self):
        raise TypeError(f"{type(self).__name__} cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError(f"{type(self).__name__} cannot be pickled")


@dataclass(slots=True, eq=False, repr=False)
class _DurableCompanionCapacityDenialObservationAuthorityV1(
    _PrivateControllerRecordV1
):
    controller: ExpertControllerV1
    controller_identity: ExpertControllerIdentityV1
    issuance_snapshot: object
    parent: DurableIngressParentV1
    store_error: ExpertPrewriteCapacityError | None
    prior_state: ExpertStateV1
    prior_cursor: ExpertJournalCursorV1
    denied_candidate_state: ExpertStateV1
    denied_candidate_cursor: ExpertJournalCursorV1
    denied_group: ExpertJournalGroupV1
    denied_payloads: tuple[bytes, ...]
    emergency_candidate_state: ExpertStateV1
    emergency_candidate_cursor: ExpertJournalCursorV1
    emergency_group: ExpertJournalGroupV1
    emergency_payloads: tuple[bytes, ...]
    requested_bytes: int
    available_bytes: int
    emergency_reserve_bytes: int
    publication_epoch: int
    owner_pid: int
    owner_thread: object
    lifecycle: str


@dataclass(slots=True, eq=False, repr=False)
class _PendingDurableCompanionEmergencyAuthorityV1(
    _PrivateControllerRecordV1
):
    controller: ExpertControllerV1
    controller_identity: ExpertControllerIdentityV1
    pending: PendingDurableCompanionEmergencyV1
    observation: DurableCompanionCapacityDenialObservationV1
    parent: DurableIngressParentV1
    ingress: BoundedIngress
    runtime: EventRuntime
    session_id: str
    prior_state: ExpertStateV1
    prior_cursor: ExpertJournalCursorV1
    denied_candidate_state: ExpertStateV1
    denied_candidate_cursor: ExpertJournalCursorV1
    denied_group: ExpertJournalGroupV1
    denied_payloads: tuple[bytes, ...]
    emergency_candidate_state: ExpertStateV1
    emergency_candidate_cursor: ExpertJournalCursorV1
    emergency_group: ExpertJournalGroupV1
    emergency_payloads: tuple[bytes, ...]
    publication_epoch: int
    publication_lock: object
    owner_pid: int
    owner_thread: object
    lifecycle: str
    active_completion_scope: object | None
    retry_subject: object | None
    reserved_claim: object | None
    reserved_subject: object | None
    reserved_terminal: object | None
    reserved_causal_proof: object | None
    reserved_completion_scope: object | None
    prepared_abort_reason: PendingEmergencyAbortReasonV1 | None
    prepared_abort_receipt: PendingDurableCompanionEmergencyAbortReceiptV1 | None
    abort_reason: PendingEmergencyAbortReasonV1 | None
    abort_receipt: PendingDurableCompanionEmergencyAbortReceiptV1 | None


@dataclass(slots=True, eq=False, repr=False)
class _DeferredEmergencyCompletionScopeV1(_PrivateControllerRecordV1):
    controller: ExpertControllerV1
    pending: PendingDurableCompanionEmergencyV1
    pending_authority: _PendingDurableCompanionEmergencyAuthorityV1
    terminal: DurableEvidenceTerminalV1
    publication_lock: object
    publication_epoch: int
    owner_pid: int
    owner_thread: object
    lifecycle: str
    reservation_committed: bool
    source_close_claim: object | None
    subject: DeferredEmergencyCommitSubjectV1 | None
    causal_proof: DurableCausalPrecedesProofV1 | None


@dataclass(slots=True, eq=False, repr=False)
class _DurableCompanionPublicationAckAuthorityV1(
    _PrivateControllerRecordV1
):
    controller: ExpertControllerV1
    controller_identity: ExpertControllerIdentityV1
    issuance_snapshot: object
    parent: DurableIngressParentV1
    append_receipt: DurableExpertAppendReceiptV1
    candidate_state: ExpertStateV1
    candidate_cursor: ExpertJournalCursorV1
    publication_epoch: int
    publication_lock: object
    owner_pid: int
    owner_thread: object
    lifecycle: str
    cursor_issued: bool
    facts_claimed: bool


@dataclass(slots=True, eq=False, repr=False)
class _DurableCompanionEmergencyPublicationProofAuthorityV1(
    _PrivateControllerRecordV1
):
    controller: ExpertControllerV1
    controller_identity: ExpertControllerIdentityV1
    issuance_snapshot: object
    pending: PendingDurableCompanionEmergencyV1
    parent: DurableIngressParentV1
    source_close_claim: object
    subject: DeferredEmergencyCommitSubjectV1
    causal_proof: DurableCausalPrecedesProofV1
    terminal_envelope: DurableEvidenceTerminalV1
    emergency_receipt: DurableExpertEmergencyReceiptV1
    group_receipt: DurableExpertAppendReceiptV1
    terminal_receipt: DurableExpertTerminalReceiptV1
    candidate_state: ExpertStateV1
    candidate_cursor: ExpertJournalCursorV1
    expert_terminal: ExpertSessionTerminalV1
    publication_epoch: int
    publication_lock: object
    owner_pid: int
    owner_thread: object
    lifecycle: str


@dataclass(slots=True, eq=False, repr=False)
class _DurableExpertTerminalReceiptHandoffV1(_PrivateControllerRecordV1):
    controller: ExpertControllerV1
    controller_identity: ExpertControllerIdentityV1
    terminal_issuance_snapshot: object
    terminal_receipt: DurableExpertTerminalReceiptV1
    lane: str
    publication_epoch: int
    owner_pid: int
    owner_thread: object
    lifecycle: str


@dataclass(slots=True, eq=False, repr=False)
class _DefinitelyDurableOrdinaryReconciliationV1(
    _PrivateControllerRecordV1
):
    lane: str
    receipt: DurableExpertAppendReceiptV1 | DurableExpertTerminalReceiptV1 | None
    candidate_state: ExpertStateV1
    candidate_cursor: ExpertJournalCursorV1
    expert_terminal: ExpertSessionTerminalV1 | None
    publication_epoch: int
    durable_end_offset: int
    store_acknowledged: bool


@dataclass(slots=True, eq=False, repr=False)
class _DurableCompanionPublicationAckWaveCBindingV1(
    _PrivateControllerRecordV1
):
    controller: ExpertControllerV1
    authority: _DurableCompanionPublicationAckAuthorityV1
    acknowledgement: DurableCompanionPublicationAckV1
    parent: DurableIngressParentV1
    append_receipt: DurableExpertAppendReceiptV1
    candidate_state: ExpertStateV1
    candidate_cursor: ExpertJournalCursorV1
    publication_epoch: int
    publication_lock: object
    owner_pid: int
    owner_thread: object


@dataclass(slots=True, eq=False, repr=False)
class _DurableCompanionEmergencyProofWaveCBindingV1(
    _PrivateControllerRecordV1
):
    controller: ExpertControllerV1
    authority: _DurableCompanionEmergencyPublicationProofAuthorityV1
    proof: DurableCompanionEmergencyPublicationProofV1
    pending: PendingDurableCompanionEmergencyV1
    emergency_receipt: DurableExpertEmergencyReceiptV1
    group_receipt: DurableExpertAppendReceiptV1
    terminal_receipt: DurableExpertTerminalReceiptV1
    candidate_state: ExpertStateV1
    candidate_cursor: ExpertJournalCursorV1
    expert_terminal: ExpertSessionTerminalV1
    publication_epoch: int
    publication_lock: object
    owner_pid: int
    owner_thread: object


@dataclass(frozen=True, slots=True)
class _ControllerValueIssuanceSnapshotV1:
    value_type: str
    public_fields: tuple[object, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class _ControllerTerminalTombstoneV1:
    value_type: str
    lifecycle: str
    public_fields: tuple[object, ...]
    diagnostic: str
    retained_receipt: object | None = None


class _WeakIdentityRegistryV1:
    __slots__ = ("_entries", "_lock")

    def __init__(self) -> None:
        self._entries: dict[
            int,
            tuple[weakref.ReferenceType[object], object],
        ] = {}
        self._lock = RLock()

    def register(self, key: object, value: object) -> None:
        identity = id(key)

        def discard(reference: weakref.ReferenceType[object]) -> None:
            with self._lock:
                current = self._entries.get(identity)
                if current is not None and current[0] is reference:
                    self._entries.pop(identity, None)

        reference = weakref.ref(key, discard)
        with self._lock:
            current = self._entries.get(identity)
            if current is not None and current[0]() is not None:
                raise RuntimeError("expert_controller_identity_collision")
            self._entries[identity] = (reference, value)

    def lookup(self, key: object) -> object | None:
        with self._lock:
            current = self._entries.get(id(key))
            if current is None or current[0]() is not key:
                return None
            return current[1]

    def unregister(self, key: object, value: object) -> bool:
        with self._lock:
            current = self._entries.get(id(key))
            if (
                current is None
                or current[0]() is not key
                or current[1] is not value
            ):
                return False
            self._entries.pop(id(key), None)
            return True

    def swap(self, key: object, prior: object, value: object) -> None:
        with self._lock:
            current = self._entries.get(id(key))
            if (
                current is None
                or current[0]() is not key
                or current[1] is not prior
            ):
                raise ValueError("expert_controller_identity_invalid")
            self._entries[id(key)] = (current[0], value)

    def converge_replace_after_uncertain(
        self,
        key: object,
        prior: object,
        value: object,
    ) -> None:
        with self._lock:
            current = self._entries.get(id(key))
            if current is None or current[0]() is not key:
                raise ValueError("expert_controller_identity_invalid")
            if current[1] is value:
                return
            if current[1] is not prior:
                raise ValueError("expert_controller_identity_invalid")
            self._entries[id(key)] = (current[0], value)


_CONTROLLER_AUTHORITIES_V1 = _WeakIdentityRegistryV1()
_CONTROLLER_IDENTITY_AUTHORITIES_V1 = _WeakIdentityRegistryV1()
_CAPACITY_OBSERVATION_AUTHORITIES_V1 = _WeakIdentityRegistryV1()
_PENDING_EMERGENCY_AUTHORITIES_V1 = _WeakIdentityRegistryV1()
_PUBLICATION_ACK_AUTHORITIES_V1 = _WeakIdentityRegistryV1()
_EMERGENCY_PROOF_AUTHORITIES_V1 = _WeakIdentityRegistryV1()
_ORDINARY_TERMINAL_HANDOFFS_V1 = _WeakIdentityRegistryV1()
_EMERGENCY_TERMINAL_HANDOFFS_V1 = _WeakIdentityRegistryV1()

_CONTROLLER_IDENTITY_ALLOCATION_LOCK_V1 = RLock()
_CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1 = 0


def _next_controller_identity_allocation_coordinate_v1() -> int:
    global _CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1
    with _CONTROLLER_IDENTITY_ALLOCATION_LOCK_V1:
        if _CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1 >= _SIGNED_63_MAX:
            raise RuntimeError("expert_controller_identity_exhausted") from None
        _CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1 += 1
        return _CONTROLLER_IDENTITY_ALLOCATION_COORDINATE_V1


def _build_opaque_controller_value_v1(
    value_type: type[object],
    values: dict[str, object],
) -> object:
    value = object.__new__(value_type)
    for field_name, field_value in values.items():
        object.__setattr__(value, field_name, field_value)
    return value


def _controller_domain_sha256_v1(
    domain: str,
    projection: object,
) -> str:
    return sha256(
        domain.encode("ascii")
        + b"\0"
        + canonical_expert_bytes(projection)
    ).hexdigest()


def _expert_cursor_sha256_v1(cursor: ExpertJournalCursorV1) -> str:
    if type(cursor) is not ExpertJournalCursorV1:
        raise TypeError("exact ExpertJournalCursorV1 required")
    ExpertJournalCursorV1.__post_init__(cursor)
    return _controller_domain_sha256_v1(
        "INCI-EXPERT-JOURNAL-CURSOR-V1",
        {
            "schema_version": cursor.schema_version,
            "session_id": cursor.session_id,
            "group_count": cursor.group_count,
            "record_count": cursor.record_count,
            "last_parent_ingest_seq": cursor.last_parent_ingest_seq,
            "last_parent_record_sha256": cursor.last_parent_record_sha256,
            "expert_seq": cursor.expert_seq,
            "expert_record_sha256": cursor.expert_record_sha256,
            "expert_state_sha256": cursor.expert_state_sha256,
            "expert_trace_sha256": cursor.expert_trace_sha256,
        },
    )


def _durable_expert_append_receipt_sha256_v1(
    receipt: DurableExpertAppendReceiptV1,
) -> str:
    if type(receipt) is not DurableExpertAppendReceiptV1:
        raise TypeError("exact DurableExpertAppendReceiptV1 required")
    DurableExpertAppendReceiptV1.__post_init__(receipt)
    return _controller_domain_sha256_v1(
        "INCI-DURABLE-EXPERT-APPEND-RECEIPT-V1",
        {
            "session_id": receipt.session_id,
            "group_sequence": receipt.group_sequence,
            "group_sha256": receipt.group_sha256,
            "last_parent_record_sha256": receipt.last_parent_record_sha256,
            "last_expert_seq": receipt.last_expert_seq,
            "final_expert_record_sha256": receipt.final_expert_record_sha256,
            "post_expert_state_sha256": receipt.post_expert_state_sha256,
            "post_expert_trace_sha256": receipt.post_expert_trace_sha256,
            "durable_end_offset": receipt.durable_end_offset,
        },
    )


def _durable_expert_terminal_receipt_sha256_v1(
    receipt: DurableExpertTerminalReceiptV1,
) -> str:
    if type(receipt) is not DurableExpertTerminalReceiptV1:
        raise TypeError("exact DurableExpertTerminalReceiptV1 required")
    DurableExpertTerminalReceiptV1.__post_init__(receipt)
    return _controller_domain_sha256_v1(
        "INCI-DURABLE-EXPERT-TERMINAL-RECEIPT-V1",
        {
            "session_id": receipt.session_id,
            "terminal_sha256": receipt.terminal_sha256,
            "terminal_frame_sequence": receipt.terminal_frame_sequence,
            "durable_end_offset": receipt.durable_end_offset,
            "reserve_already_consumed": receipt.reserve_already_consumed,
        },
    )


def _exact_nonnegative_signed_63_v1(value: object) -> bool:
    return type(value) is int and 0 <= value <= _SIGNED_63_MAX


def _set_controller_lifecycle_v1(
    controller: ExpertControllerV1,
    lifecycle: str,
) -> None:
    controller._controller_lifecycle = lifecycle
    controller._lifecycle = lifecycle


def _issue_controller_identity_v1(
    manifest: ExpertSessionManifestV1,
    owner_pid: int,
) -> ExpertControllerIdentityV1:
    coordinate = _next_controller_identity_allocation_coordinate_v1()
    projection = {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "expert_manifest_sha256": manifest.manifest_sha256,
        "owner_pid": owner_pid,
        "allocation_coordinate": coordinate,
    }
    digest = _controller_domain_sha256_v1(
        _EXPERT_CONTROLLER_IDENTITY_DOMAIN_V1,
        projection,
    )
    identity = _build_opaque_controller_value_v1(
        ExpertControllerIdentityV1,
        {**projection, "controller_identity_sha256": digest},
    )
    snapshot = _ControllerValueIssuanceSnapshotV1(
        value_type="ExpertControllerIdentityV1",
        public_fields=tuple(projection.values()),
        digest=digest,
    )
    try:
        _CONTROLLER_IDENTITY_AUTHORITIES_V1.register(identity, snapshot)
        return identity
    except BaseException:
        if _CONTROLLER_IDENTITY_AUTHORITIES_V1.lookup(identity) is snapshot:
            _CONTROLLER_IDENTITY_AUTHORITIES_V1.unregister(
                identity,
                snapshot,
            )
        raise


def _controller_identity_public_fields_valid_v1(
    identity: ExpertControllerIdentityV1,
) -> bool:
    try:
        projection = {
            "schema_version": identity.schema_version,
            "session_id": identity.session_id,
            "expert_manifest_sha256": identity.expert_manifest_sha256,
            "owner_pid": identity.owner_pid,
            "allocation_coordinate": identity.allocation_coordinate,
        }
        snapshot = _CONTROLLER_IDENTITY_AUTHORITIES_V1.lookup(identity)
        return (
            type(snapshot) is _ControllerValueIssuanceSnapshotV1
            and snapshot.value_type == "ExpertControllerIdentityV1"
            and type(identity.schema_version) is int
            and identity.schema_version == 1
            and type(identity.session_id) is str
            and type(identity.expert_manifest_sha256) is str
            and _exact_nonnegative_signed_63_v1(identity.owner_pid)
            and type(identity.allocation_coordinate) is int
            and 0 < identity.allocation_coordinate <= _SIGNED_63_MAX
            and identity.controller_identity_sha256 == snapshot.digest
            and identity.controller_identity_sha256
            == _controller_domain_sha256_v1(
                _EXPERT_CONTROLLER_IDENTITY_DOMAIN_V1,
                projection,
            )
            and tuple(projection.values()) == snapshot.public_fields
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _capacity_observation_public_fields_valid_v1(
    observation: DurableCompanionCapacityDenialObservationV1,
    authority: _DurableCompanionCapacityDenialObservationAuthorityV1,
) -> bool:
    try:
        projection = {
            "schema_version": observation.schema_version,
            "session_id": observation.session_id,
            "durable_parent_envelope_sha256": (
                observation.durable_parent_envelope_sha256
            ),
            "candidate_state_sha256": observation.candidate_state_sha256,
            "candidate_cursor_sha256": observation.candidate_cursor_sha256,
            "requested_bytes": observation.requested_bytes,
            "available_bytes": observation.available_bytes,
            "emergency_reserve_bytes": observation.emergency_reserve_bytes,
            "publication_epoch": observation.publication_epoch,
        }
        return (
            type(authority.issuance_snapshot)
            is _ControllerValueIssuanceSnapshotV1
            and authority.issuance_snapshot.value_type
            == "DurableCompanionCapacityDenialObservationV1"
            and observation.schema_version == 1
            and observation.session_id == authority.controller._manifest.session_id
            and observation.durable_parent_envelope_sha256
            == authority.parent.envelope_sha256
            and observation.candidate_state_sha256
            == expert_state_sha256(authority.denied_candidate_state)
            and observation.candidate_cursor_sha256
            == _expert_cursor_sha256_v1(authority.denied_candidate_cursor)
            and observation.requested_bytes == authority.requested_bytes
            and observation.available_bytes == authority.available_bytes
            and observation.emergency_reserve_bytes
            == authority.emergency_reserve_bytes
            == EXPERT_EMERGENCY_RESERVE_BYTES
            and observation.publication_epoch == authority.publication_epoch
            and all(
                _exact_nonnegative_signed_63_v1(value)
                for value in (
                    observation.requested_bytes,
                    observation.available_bytes,
                    observation.emergency_reserve_bytes,
                    observation.publication_epoch,
                )
            )
            and observation.observation_sha256
            == authority.issuance_snapshot.digest
            == _controller_domain_sha256_v1(
                _CAPACITY_DENIAL_OBSERVATION_DOMAIN_V1,
                projection,
            )
            and tuple(projection.values())
            == authority.issuance_snapshot.public_fields
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _pending_public_fields_valid_v1(
    pending: PendingDurableCompanionEmergencyV1,
    authority: _PendingDurableCompanionEmergencyAuthorityV1,
) -> bool:
    try:
        projection = {
            "schema_version": pending.schema_version,
            "session_id": pending.session_id,
            "durable_parent_envelope_sha256": (
                pending.durable_parent_envelope_sha256
            ),
            "candidate_state_sha256": pending.candidate_state_sha256,
            "candidate_cursor_sha256": pending.candidate_cursor_sha256,
            "capacity_denial_sha256": pending.capacity_denial_sha256,
            "publication_epoch": pending.publication_epoch,
        }
        return (
            authority.pending is pending
            and pending.schema_version == 1
            and pending.session_id == authority.session_id
            and pending.durable_parent_envelope_sha256
            == authority.parent.envelope_sha256
            and pending.candidate_state_sha256
            == expert_state_sha256(authority.denied_candidate_state)
            and pending.candidate_cursor_sha256
            == _expert_cursor_sha256_v1(authority.denied_candidate_cursor)
            and pending.capacity_denial_sha256
            == authority.observation.observation_sha256
            and pending.publication_epoch == authority.publication_epoch
            and _exact_nonnegative_signed_63_v1(pending.publication_epoch)
            and pending.pending_sha256
            == _controller_domain_sha256_v1(
                _PENDING_EMERGENCY_DOMAIN_V1,
                projection,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _issue_pending_durable_companion_emergency_v1(
    observation: DurableCompanionCapacityDenialObservationV1,
    *,
    candidate_state: ExpertStateV1,
    candidate_cursor: ExpertJournalCursorV1,
) -> PendingDurableCompanionEmergencyV1:
    if type(observation) is not DurableCompanionCapacityDenialObservationV1:
        raise TypeError(
            "exact DurableCompanionCapacityDenialObservationV1 required"
        )
    authority = _CAPACITY_OBSERVATION_AUTHORITIES_V1.lookup(observation)
    if type(authority) is not (
        _DurableCompanionCapacityDenialObservationAuthorityV1
    ):
        if (
            type(authority) is _ControllerTerminalTombstoneV1
            and authority.lifecycle == "CONSUMED_INTO_PENDING"
        ):
            raise ValueError(
                "durable_companion_capacity_denial_observation_consumed"
            )
        raise ValueError(
            "durable_companion_capacity_denial_observation_invalid"
        )
    if type(candidate_state) is not ExpertStateV1:
        raise ValueError("durable_companion_capacity_denial_observation_invalid")
    if type(candidate_cursor) is not ExpertJournalCursorV1:
        raise ValueError("durable_companion_capacity_denial_observation_invalid")
    controller = authority.controller
    if (
        authority.lifecycle != "ISSUED"
        or authority.denied_candidate_state is not candidate_state
        or authority.denied_candidate_cursor is not candidate_cursor
        or authority.controller_identity is not controller._controller_identity
        or authority.owner_pid != getpid()
        or authority.owner_thread is not current_thread()
        or controller._controller_lifecycle != _CONTROLLER_ACTIVE
        or controller._publication_epoch + 1 != authority.publication_epoch
        or not controller._publication_lock._is_owned()
        or not _controller_identity_public_fields_valid_v1(
            authority.controller_identity
        )
        or not _capacity_observation_public_fields_valid_v1(
            observation,
            authority,
        )
    ):
        raise ValueError(
            "durable_companion_capacity_denial_observation_invalid"
        )
    projection = {
        "schema_version": 1,
        "session_id": observation.session_id,
        "durable_parent_envelope_sha256": (
            observation.durable_parent_envelope_sha256
        ),
        "candidate_state_sha256": observation.candidate_state_sha256,
        "candidate_cursor_sha256": observation.candidate_cursor_sha256,
        "capacity_denial_sha256": observation.observation_sha256,
        "publication_epoch": authority.publication_epoch,
    }
    digest = _controller_domain_sha256_v1(
        _PENDING_EMERGENCY_DOMAIN_V1,
        projection,
    )
    pending = _build_opaque_controller_value_v1(
        PendingDurableCompanionEmergencyV1,
        {**projection, "pending_sha256": digest},
    )
    pending_authority = _PendingDurableCompanionEmergencyAuthorityV1(
        controller=controller,
        controller_identity=authority.controller_identity,
        pending=pending,
        observation=observation,
        parent=authority.parent,
        ingress=controller._ingress,
        runtime=controller._runtime,
        session_id=observation.session_id,
        prior_state=authority.prior_state,
        prior_cursor=authority.prior_cursor,
        denied_candidate_state=authority.denied_candidate_state,
        denied_candidate_cursor=authority.denied_candidate_cursor,
        denied_group=authority.denied_group,
        denied_payloads=authority.denied_payloads,
        emergency_candidate_state=authority.emergency_candidate_state,
        emergency_candidate_cursor=authority.emergency_candidate_cursor,
        emergency_group=authority.emergency_group,
        emergency_payloads=authority.emergency_payloads,
        publication_epoch=authority.publication_epoch,
        publication_lock=controller._publication_lock,
        owner_pid=authority.owner_pid,
        owner_thread=authority.owner_thread,
        lifecycle=_PENDING_FRESH,
        active_completion_scope=None,
        retry_subject=None,
        reserved_claim=None,
        reserved_subject=None,
        reserved_terminal=None,
        reserved_causal_proof=None,
        reserved_completion_scope=None,
        prepared_abort_reason=None,
        prepared_abort_receipt=None,
        abort_reason=None,
        abort_receipt=None,
    )
    consumed_observation_tombstone = _ControllerTerminalTombstoneV1(
        value_type="DurableCompanionCapacityDenialObservationV1",
        lifecycle="CONSUMED_INTO_PENDING",
        public_fields=authority.issuance_snapshot.public_fields,
        diagnostic=observation.observation_sha256,
    )
    try:
        _PENDING_EMERGENCY_AUTHORITIES_V1.register(
            pending,
            pending_authority,
        )
        if not _pending_public_fields_valid_v1(pending, pending_authority):
            raise ValueError("pending public fields invalid")
        _CAPACITY_OBSERVATION_AUTHORITIES_V1.swap(
            observation,
            authority,
            consumed_observation_tombstone,
        )
        authority.store_error = None
        authority.lifecycle = "CONSUMED_INTO_PENDING"
        controller._publication_epoch = authority.publication_epoch
        controller._controller_lifecycle = _CONTROLLER_EMERGENCY_PENDING
        controller._lifecycle = _CONTROLLER_EMERGENCY_PENDING
        controller._active_pending = pending
        return pending
    except BaseException:
        if (
            _PENDING_EMERGENCY_AUTHORITIES_V1.lookup(pending)
            is pending_authority
        ):
            _PENDING_EMERGENCY_AUTHORITIES_V1.unregister(
                pending,
                pending_authority,
            )
        try:
            current_observation = (
                _CAPACITY_OBSERVATION_AUTHORITIES_V1.lookup(observation)
            )
            if current_observation is consumed_observation_tombstone:
                _CAPACITY_OBSERVATION_AUTHORITIES_V1.converge_replace_after_uncertain(
                    observation,
                    consumed_observation_tombstone,
                    authority,
                )
                current_observation = authority
            if current_observation is authority:
                _CAPACITY_OBSERVATION_AUTHORITIES_V1.unregister(
                    observation,
                    authority,
                )
        except BaseException:
            pass
        authority.store_error = None
        authority.lifecycle = "ISSUANCE_FAILED_CLOSED"
        raise


def _issue_publication_ack_v1(
    controller: ExpertControllerV1,
    parent: DurableIngressParentV1,
    receipt: DurableExpertAppendReceiptV1,
    candidate_state: ExpertStateV1,
    candidate_cursor: ExpertJournalCursorV1,
    publication_epoch: int,
) -> DurableCompanionPublicationAckV1:
    projection = {
        "schema_version": 1,
        "session_id": controller._manifest.session_id,
        "durable_parent_envelope_sha256": parent.envelope_sha256,
        "append_receipt_sha256": (
            _durable_expert_append_receipt_sha256_v1(receipt)
        ),
        "candidate_state_sha256": expert_state_sha256(candidate_state),
        "candidate_cursor_sha256": _expert_cursor_sha256_v1(candidate_cursor),
        "publication_epoch": publication_epoch,
    }
    digest = _controller_domain_sha256_v1(
        _PUBLICATION_ACK_DOMAIN_V1,
        projection,
    )
    acknowledgement = _build_opaque_controller_value_v1(
        DurableCompanionPublicationAckV1,
        {**projection, "ack_sha256": digest},
    )
    authority = _DurableCompanionPublicationAckAuthorityV1(
        controller=controller,
        controller_identity=controller._controller_identity,
        issuance_snapshot=_ControllerValueIssuanceSnapshotV1(
            value_type="DurableCompanionPublicationAckV1",
            public_fields=tuple(projection.values()),
            digest=digest,
        ),
        parent=parent,
        append_receipt=receipt,
        candidate_state=candidate_state,
        candidate_cursor=candidate_cursor,
        publication_epoch=publication_epoch,
        publication_lock=controller._publication_lock,
        owner_pid=controller._owner_pid,
        owner_thread=controller._owner_thread,
        lifecycle=_ACK_ACTIVE,
        cursor_issued=False,
        facts_claimed=False,
    )
    try:
        _PUBLICATION_ACK_AUTHORITIES_V1.register(
            acknowledgement,
            authority,
        )
        return acknowledgement
    except BaseException:
        if (
            _PUBLICATION_ACK_AUTHORITIES_V1.lookup(acknowledgement)
            is authority
        ):
            _PUBLICATION_ACK_AUTHORITIES_V1.unregister(
                acknowledgement,
                authority,
            )
        raise


def _issue_pending_abort_receipt_v1(
    authority: _PendingDurableCompanionEmergencyAuthorityV1,
    reason: PendingEmergencyAbortReasonV1,
) -> PendingDurableCompanionEmergencyAbortReceiptV1:
    projection = {
        "schema_version": 1,
        "session_id": authority.session_id,
        "controller_identity_sha256": (
            authority.controller_identity.controller_identity_sha256
        ),
        "pending_sha256": authority.pending.pending_sha256,
        "capacity_denial_sha256": (
            authority.observation.observation_sha256
        ),
        "abort_reason": reason.value,
        "publication_epoch": authority.publication_epoch,
    }
    digest = _controller_domain_sha256_v1(
        _PENDING_ABORT_RECEIPT_DOMAIN_V1,
        projection,
    )
    return _build_opaque_controller_value_v1(
        PendingDurableCompanionEmergencyAbortReceiptV1,
        {
            **projection,
            "abort_reason": reason,
            "receipt_sha256": digest,
        },
    )


def _pending_terminal_tombstone_v1(
    pending: PendingDurableCompanionEmergencyV1,
    lifecycle: str,
    *,
    diagnostic: str | None = None,
    retained_receipt: object | None = None,
) -> _ControllerTerminalTombstoneV1:
    return _ControllerTerminalTombstoneV1(
        value_type="PendingDurableCompanionEmergencyV1",
        lifecycle=lifecycle,
        public_fields=(
            pending.pending_sha256,
            pending.capacity_denial_sha256,
            pending.publication_epoch,
        ),
        diagnostic=(
            pending.pending_sha256 if diagnostic is None else diagnostic
        ),
        retained_receipt=retained_receipt,
    )


def _copy_state(state: ExpertStateV1) -> ExpertStateV1:
    if type(state) is not ExpertStateV1:
        raise TypeError("state")
    ExpertStateV1.__post_init__(state)
    return ExpertStateV1(
        schema_version=state.schema_version,
        session_id=state.session_id,
        expert_manifest_sha256=state.expert_manifest_sha256,
        match_binding_universe_sha256=(
            state.match_binding_universe_sha256
        ),
        sync_policy_sha256=state.sync_policy_sha256,
        initial_synchronization_sha256=(
            state.initial_synchronization_sha256
        ),
        synchronization=state.synchronization,
        rejected_parent_count=state.rejected_parent_count,
        halted=state.halted,
        halt_reason=state.halt_reason,
    )


def _expected_session_start_sha256(manifest: SessionManifest) -> str:
    if type(manifest) is not SessionManifest:
        raise TypeError("phase1 manifest")
    SessionManifest.__post_init__(manifest)
    payload = canonical_session_manifest_bytes(manifest)
    start = PersistedEvent(
        journal_version=1,
        record_kind=RecordKind.CONTROL,
        ingest_seq=1,
        session_id=manifest.session_id,
        event_type="SESSION_START",
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
        content_type="application/vnd.inci.session-manifest+json",
        payload_encoding="canonical-json-v1",
        payload_transform="identity-public-market-v1",
        retention_delete_by_ns=None,
        payload_sha256=sha256(payload).hexdigest(),
        payload=payload,
    )
    return canonical_record_sha256(start)


def _expected_provider_domain(
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    phase1: SessionManifest,
) -> ExpertProviderDomainBindingV1:
    identities = {
        (
            binding.provider_source_id,
            binding.revision_domain_id,
            binding.source_lineage_sha256,
        )
        for binding in universe.bindings
    }
    lineage = compute_expert_provider_source_lineage_sha256(
        phase1.provider_id,
        phase1.product_tier,
        phase1.source_lineage_id,
        phase1.provider_manifest_canonical_sha256,
    )
    if len(identities) != 1:
        raise ValueError("expert_provider_domain_binding_invalid")
    source_id, revision_domain_id, source_lineage = next(iter(identities))
    if source_id != phase1.provider_id or source_lineage != lineage:
        raise ValueError("expert_provider_domain_binding_invalid")
    values: dict[str, object] = {
        "schema_version": 1,
        "phase1_session_manifest_sha256": session_manifest_sha256(phase1),
        "match_binding_universe_sha256": universe.universe_sha256,
        "provider_id": phase1.provider_id,
        "product_tier": phase1.product_tier,
        "source_lineage_id": phase1.source_lineage_id,
        "provider_manifest_canonical_sha256": (
            phase1.provider_manifest_canonical_sha256
        ),
        "provider_source_lineage_sha256": lineage,
        "revision_domain_id": revision_domain_id,
    }
    values["provider_domain_binding_sha256"] = (
        compute_expert_provider_domain_binding_sha256(**values)
    )
    expected = ExpertProviderDomainBindingV1(
        **values  # type: ignore[arg-type]
    )
    if manifest.provider_domain != expected:
        raise ValueError("expert_provider_domain_binding_invalid")
    return expected


def _expected_retention(
    manifest: ExpertSessionManifestV1,
    phase1: SessionManifest,
    persistence_authorizer: ProviderPersistenceAuthorizer,
) -> ExpertRetentionBindingV1:
    decision = persistence_authorizer.bound_decision
    request_binding = decision.provider_request_binding_sha256
    if type(request_binding) is not str:
        raise ValueError("expert_retention_binding_invalid")
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": phase1.session_id,
        "evidence_session_manifest_sha256": session_manifest_sha256(phase1),
        "provider_request_binding_sha256": request_binding,
        "permission_artifact_sha256": phase1.permission_artifact_sha256,
        "qualification_artifact_sha256": (
            phase1.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": phase1.qualification_trace_sha256,
        "retention_delete_by_ns": phase1.required_retention_until_ns,
        "access_expires_at_ns": phase1.access_expires_at_ns,
        "analysis_expires_at_ns": phase1.analysis_expires_at_ns,
    }
    values["retention_binding_sha256"] = (
        compute_expert_retention_binding_sha256(**values)
    )
    expected = ExpertRetentionBindingV1(
        **values  # type: ignore[arg-type]
    )
    if manifest.retention != expected:
        raise ValueError("expert_retention_binding_invalid")
    return expected


def _validate_factory_bindings(
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
    runtime: EventRuntime,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ExpertStateV1:
    ExpertSessionManifestV1.__post_init__(manifest)
    BindingUniverse.__post_init__(universe)
    SyncPolicy.__post_init__(policy)
    phase1 = persistence_authorizer.session_manifest
    if type(phase1) is not SessionManifest:
        raise TypeError("exact SessionManifest required")
    SessionManifest.__post_init__(phase1)
    universe_digest = binding_universe_sha256(universe)
    policy_digest = expert_contract_sha256(policy)
    if policy.universe_sha256 != universe_digest:
        raise ValueError("expert_controller_binding_invalid")
    synchronization = synchronization_session_from_artifacts(
        universe,
        policy,
    )
    expected_capacity = prove_expert_capacity(universe, policy)
    expected_phase1_digest = session_manifest_sha256(phase1)
    decision = persistence_authorizer.bound_decision
    request_binding = decision.provider_request_binding_sha256
    if (
        persistence_authorizer.coordinator is not coordinator
        or runtime.state != initial_state(phase1.session_id)
        or manifest.session_id != phase1.session_id
        or manifest.evidence_session_manifest_sha256
        != expected_phase1_digest
        or manifest.evidence_session_start_record_sha256
        != _expected_session_start_sha256(phase1)
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
        or type(request_binding) is not str
        or manifest.provider_request_binding_sha256 != request_binding
        or manifest.match_binding_universe_sha256 != universe_digest
        or manifest.binding_raw_artifact_id != universe.raw_artifact_id
        or manifest.binding_raw_artifact_sha256
        != universe.raw_artifact_sha256
        or manifest.binding_review_artifact_id
        != universe.review.review_artifact_id
        or manifest.binding_review_artifact_sha256
        != universe.review.review_artifact_sha256
        or manifest.sync_policy_sha256 != policy_digest
        or manifest.initial_synchronization_sha256
        != expert_contract_sha256(synchronization)
        or manifest.capacity != expected_capacity
    ):
        raise ValueError("expert_controller_binding_invalid")
    _expected_provider_domain(manifest, universe, phase1)
    _expected_retention(manifest, phase1, persistence_authorizer)
    state = initial_expert_state(manifest, universe, policy)
    if type(state) is not ExpertStateV1:
        raise TypeError("initial expert state")
    ExpertStateV1.__post_init__(state)
    return state


def _genesis_cursor(
    manifest: ExpertSessionManifestV1,
    state: ExpertStateV1,
) -> ExpertJournalCursorV1:
    state_digest = expert_state_sha256(state)
    return ExpertJournalCursorV1(
        schema_version=1,
        session_id=manifest.session_id,
        group_count=0,
        record_count=0,
        last_parent_ingest_seq=0,
        last_parent_record_sha256=(
            manifest.evidence_session_start_record_sha256
        ),
        expert_seq=0,
        expert_record_sha256=manifest.manifest_sha256,
        expert_state_sha256=state_digest,
        expert_trace_sha256=expert_trace_seed_sha256(
            manifest.session_id,
            manifest.manifest_sha256,
            state_digest,
        ),
    )


def _require_exact_parent(
    parent: PersistedEvent,
    manifest: ExpertSessionManifestV1,
    cursor: ExpertJournalCursorV1,
) -> None:
    if type(parent) is not PersistedEvent:
        raise TypeError("parent")
    PersistedEvent.__post_init__(parent)
    if (
        parent.record_kind is not RecordKind.RAW
        or parent.session_id != manifest.session_id
        or parent.ingest_seq != cursor.last_parent_ingest_seq + 2
    ):
        raise ValueError("expert_parent_invalid")


def _require_parent_projection(
    parent: PersistedEvent,
    projection: ExpertParentEvidenceV1,
) -> None:
    if type(projection) is not ExpertParentEvidenceV1:
        raise TypeError("parent projection")
    ExpertParentEvidenceV1.__post_init__(projection)
    if (
        projection.session_id != parent.session_id
        or projection.ingest_seq != parent.ingest_seq
        or projection.record_sha256 != canonical_record_sha256(parent)
        or projection.event_type != parent.event_type
        or projection.event_version != parent.event_version
        or projection.local_wall_ns != parent.local_wall_ns
        or projection.local_monotonic_ns != parent.local_monotonic_ns
        or projection.clock_uncertainty_ns
        != parent.clock_uncertainty_ns
    ):
        raise ValueError("expert_parent_projection_invalid")


def _candidate_cursor(
    prior: ExpertJournalCursorV1,
    group: ExpertJournalGroupV1,
) -> ExpertJournalCursorV1:
    values: dict[str, object] = {
        "schema_version": 1,
        "session_id": group.session_id,
        "group_count": prior.group_count + 1,
        "record_count": prior.record_count + len(group.records),
        "last_parent_ingest_seq": group.parent.ingest_seq,
        "last_parent_record_sha256": group.parent.record_sha256,
        "expert_seq": group.records[-1].expert_seq,
        "expert_record_sha256": group.final_expert_record_sha256,
        "expert_state_sha256": group.post_expert_state_sha256,
        "expert_trace_sha256": group.post_trace_sha256,
    }
    candidate = ExpertJournalCursorV1(
        **values  # type: ignore[arg-type]
    )
    if (
        candidate.schema_version != values["schema_version"]
        or candidate.session_id != values["session_id"]
        or candidate.group_count != values["group_count"]
        or candidate.record_count != values["record_count"]
        or candidate.last_parent_ingest_seq
        != values["last_parent_ingest_seq"]
        or candidate.last_parent_record_sha256
        != values["last_parent_record_sha256"]
        or candidate.expert_seq != values["expert_seq"]
        or candidate.expert_record_sha256
        != values["expert_record_sha256"]
        or candidate.expert_state_sha256
        != values["expert_state_sha256"]
        or candidate.expert_trace_sha256
        != values["expert_trace_sha256"]
    ):
        raise ValueError("expert_candidate_cursor_invalid")
    ExpertJournalCursorV1.__post_init__(candidate)
    return candidate


def _build_group(
    manifest: ExpertSessionManifestV1,
    prior_cursor: ExpertJournalCursorV1,
    reduction: ExpertReductionV1,
    parent: ExpertParentEvidenceV1,
) -> tuple[
    ExpertJournalGroupV1,
    tuple[bytes, ...],
    ExpertJournalCursorV1,
    ExpertStateV1,
]:
    if type(reduction) is not ExpertReductionV1:
        raise TypeError("reduction")
    ExpertReductionV1.__post_init__(reduction)
    _require_parent_projection_from_reduction(parent, reduction)
    if (
        reduction.prior_expert_state_sha256
        != prior_cursor.expert_state_sha256
    ):
        raise ValueError("expert_reduction_prior_invalid")
    payloads = tuple(
        canonical_expert_bytes(outcome.payload)
        for outcome in reduction.outcomes
    )
    records: list[ExpertJournalRecordV1] = []
    traces: list[ExpertTraceStepV1] = []
    prior_record = prior_cursor.expert_record_sha256
    prior_trace = prior_cursor.expert_trace_sha256
    count = len(reduction.outcomes)
    for index, (outcome, payload) in enumerate(
        zip(reduction.outcomes, payloads, strict=True)
    ):
        descriptor = ExpertPayloadDescriptorV1(
            schema_version=1,
            content_type="application/vnd.inci.expert+json",
            payload_encoding="canonical-json-v1",
            payload_contract_name=type(outcome.payload).__name__,
            payload_length=len(payload),
            payload_sha256=sha256(payload).hexdigest(),
        )
        record_values: dict[str, object] = {
            "schema_version": 1,
            "session_id": manifest.session_id,
            "expert_manifest_sha256": manifest.manifest_sha256,
            "provider_request_binding_sha256": (
                manifest.provider_request_binding_sha256
            ),
            "match_binding_universe_sha256": (
                manifest.match_binding_universe_sha256
            ),
            "retention_binding_sha256": (
                manifest.retention.retention_binding_sha256
            ),
            "expert_seq": prior_cursor.expert_seq + index + 1,
            "parent": parent,
            "parent_output_index": index,
            "parent_output_count": count,
            "event_kind": outcome.event_kind,
            "event_version": outcome.event_version,
            "event_schema_sha256": outcome.event_schema_sha256,
            "prior_expert_record_sha256": prior_record,
            "prior_expert_state_sha256": (
                outcome.prior_expert_state_sha256
            ),
            "payload": descriptor,
            "post_expert_state_sha256": (
                outcome.post_expert_state_sha256
            ),
        }
        record_values["record_sha256"] = (
            compute_expert_journal_record_sha256(**record_values)
        )
        record = ExpertJournalRecordV1(
            **record_values  # type: ignore[arg-type]
        )
        trace_values: dict[str, object] = {
            "schema_version": 1,
            "expert_seq": record.expert_seq,
            "prior_trace_sha256": prior_trace,
            "expert_record_sha256": record.record_sha256,
            "post_expert_state_sha256": (
                record.post_expert_state_sha256
            ),
        }
        trace_values["post_trace_sha256"] = (
            compute_expert_trace_step_sha256(**trace_values)
        )
        trace = ExpertTraceStepV1(
            **trace_values  # type: ignore[arg-type]
        )
        records.append(record)
        traces.append(trace)
        prior_record = record.record_sha256
        prior_trace = trace.post_trace_sha256
    group_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": manifest.session_id,
        "expert_manifest_sha256": manifest.manifest_sha256,
        "group_sequence": prior_cursor.group_count + 1,
        "parent": parent,
        "parent_output_count": count,
        "first_expert_seq": prior_cursor.expert_seq + 1,
        "prior_expert_record_sha256": (
            prior_cursor.expert_record_sha256
        ),
        "prior_expert_state_sha256": prior_cursor.expert_state_sha256,
        "records": tuple(records),
        "trace_steps": tuple(traces),
        "final_expert_record_sha256": records[-1].record_sha256,
        "post_expert_state_sha256": (
            reduction.final_expert_state_sha256
        ),
        "post_trace_sha256": traces[-1].post_trace_sha256,
    }
    group_values["group_sha256"] = (
        compute_expert_journal_group_sha256(**group_values)
    )
    group = ExpertJournalGroupV1(
        **group_values  # type: ignore[arg-type]
    )
    validate_expert_group_against_cursor(
        group,
        payloads,
        prior_cursor,
    )
    candidate_cursor = _candidate_cursor(prior_cursor, group)
    candidate_state = _copy_state(reduction.final_state)
    if (
        expert_state_sha256(candidate_state)
        != candidate_cursor.expert_state_sha256
        or candidate_state != reduction.final_state
    ):
        raise ValueError("expert_candidate_state_invalid")
    return group, payloads, candidate_cursor, candidate_state


def _require_parent_projection_from_reduction(
    parent: ExpertParentEvidenceV1,
    reduction: ExpertReductionV1,
) -> None:
    if type(parent) is not ExpertParentEvidenceV1:
        raise TypeError("parent")
    ExpertParentEvidenceV1.__post_init__(parent)
    for outcome in reduction.outcomes:
        payload = outcome.payload
        observation = payload.observation
        if observation.parent != parent:
            raise ValueError("expert_reduction_parent_invalid")


def _validate_append_receipt(
    receipt: DurableExpertAppendReceiptV1,
    group: ExpertJournalGroupV1,
    candidate: ExpertJournalCursorV1,
    expected_end_offset: int,
) -> None:
    if type(receipt) is not DurableExpertAppendReceiptV1:
        raise TypeError("append receipt")
    DurableExpertAppendReceiptV1.__post_init__(receipt)
    if (
        receipt.session_id != group.session_id
        or receipt.group_sequence != group.group_sequence
        or receipt.group_sha256 != group.group_sha256
        or receipt.last_parent_record_sha256
        != group.parent.record_sha256
        or receipt.last_expert_seq != candidate.expert_seq
        or receipt.final_expert_record_sha256
        != candidate.expert_record_sha256
        or receipt.post_expert_state_sha256
        != candidate.expert_state_sha256
        or receipt.post_expert_trace_sha256
        != candidate.expert_trace_sha256
        or receipt.durable_end_offset != expected_end_offset
    ):
        raise ValueError("expert_append_receipt_invalid")


def _validate_terminal_material(
    manifest: ExpertSessionManifestV1,
    state: ExpertStateV1,
    cursor: ExpertJournalCursorV1,
    known_evidence: PersistedEvent | None,
    evidence: PersistedEvent,
    terminal: ExpertSessionTerminalV1,
) -> None:
    if type(evidence) is not PersistedEvent:
        raise TypeError("evidence terminal")
    if type(terminal) is not ExpertSessionTerminalV1:
        raise TypeError("expert terminal")
    PersistedEvent.__post_init__(evidence)
    ExpertSessionTerminalV1.__post_init__(terminal)
    validate_expert_terminal_against_cursor(terminal, cursor)
    if (
        evidence.record_kind is not RecordKind.CONTROL
        or evidence.session_id != manifest.session_id
        or evidence.event_type != "SESSION_HALT"
        or terminal.session_id != manifest.session_id
        or terminal.expert_manifest_sha256 != manifest.manifest_sha256
        or terminal.provider_request_binding_sha256
        != manifest.provider_request_binding_sha256
        or terminal.match_binding_universe_sha256
        != manifest.match_binding_universe_sha256
        or terminal.retention_binding_sha256
        != manifest.retention.retention_binding_sha256
        or terminal.evidence_terminal_ingest_seq != evidence.ingest_seq
        or terminal.evidence_terminal_record_sha256
        != canonical_record_sha256(evidence)
        or terminal.final_expert_state_sha256
        != expert_state_sha256(state)
        or terminal.final_expert_state_sha256
        != cursor.expert_state_sha256
    ):
        raise ValueError("expert_terminal_alignment_invalid")
    if known_evidence is not None:
        if type(known_evidence) is not PersistedEvent:
            raise TypeError("known evidence terminal")
        PersistedEvent.__post_init__(known_evidence)
        if (
            evidence != known_evidence
            or canonical_record_sha256(evidence)
            != canonical_record_sha256(known_evidence)
        ):
            raise ValueError("expert_terminal_alignment_invalid")


def _validate_terminal_receipt(
    receipt: DurableExpertTerminalReceiptV1,
    terminal: ExpertSessionTerminalV1,
    cursor: ExpertJournalCursorV1,
    expected_end_offset: int,
) -> None:
    if type(receipt) is not DurableExpertTerminalReceiptV1:
        raise TypeError("terminal receipt")
    DurableExpertTerminalReceiptV1.__post_init__(receipt)
    if (
        receipt.session_id != terminal.session_id
        or receipt.terminal_sha256 != terminal.terminal_sha256
        or receipt.terminal_frame_sequence != cursor.group_count + 1
        or receipt.durable_end_offset != expected_end_offset
        or receipt.reserve_already_consumed is not True
    ):
        raise ValueError("expert_terminal_receipt_invalid")


def _validate_emergency_receipt(
    receipt: DurableExpertEmergencyReceiptV1,
    group: ExpertJournalGroupV1,
    cursor: ExpertJournalCursorV1,
    terminal: ExpertSessionTerminalV1,
    expected_group_end_offset: int,
    expected_terminal_end_offset: int,
) -> None:
    if type(receipt) is not DurableExpertEmergencyReceiptV1:
        raise TypeError("emergency receipt")
    DurableExpertEmergencyReceiptV1.__post_init__(receipt)
    if (
        receipt.session_id != terminal.session_id
        or receipt.reserve_already_consumed is not True
    ):
        raise ValueError("expert_emergency_receipt_invalid")
    _validate_append_receipt(
        receipt.group_receipt,
        group,
        cursor,
        expected_group_end_offset,
    )
    _validate_terminal_receipt(
        receipt.terminal_receipt,
        terminal,
        cursor,
        expected_terminal_end_offset,
    )


def _ack_public_fields_valid_v1(
    acknowledgement: DurableCompanionPublicationAckV1,
    authority: _DurableCompanionPublicationAckAuthorityV1,
) -> bool:
    try:
        projection = {
            "schema_version": acknowledgement.schema_version,
            "session_id": acknowledgement.session_id,
            "durable_parent_envelope_sha256": (
                acknowledgement.durable_parent_envelope_sha256
            ),
            "append_receipt_sha256": acknowledgement.append_receipt_sha256,
            "candidate_state_sha256": acknowledgement.candidate_state_sha256,
            "candidate_cursor_sha256": (
                acknowledgement.candidate_cursor_sha256
            ),
            "publication_epoch": acknowledgement.publication_epoch,
        }
        return (
            type(authority.issuance_snapshot)
            is _ControllerValueIssuanceSnapshotV1
            and authority.issuance_snapshot.value_type
            == "DurableCompanionPublicationAckV1"
            and acknowledgement.schema_version == 1
            and acknowledgement.session_id
            == authority.controller._manifest.session_id
            and acknowledgement.durable_parent_envelope_sha256
            == authority.parent.envelope_sha256
            and acknowledgement.append_receipt_sha256
            == _durable_expert_append_receipt_sha256_v1(
                authority.append_receipt
            )
            and acknowledgement.candidate_state_sha256
            == expert_state_sha256(authority.candidate_state)
            and acknowledgement.candidate_cursor_sha256
            == _expert_cursor_sha256_v1(authority.candidate_cursor)
            and acknowledgement.publication_epoch
            == authority.publication_epoch
            and acknowledgement.ack_sha256
            == authority.issuance_snapshot.digest
            == _controller_domain_sha256_v1(
                _PUBLICATION_ACK_DOMAIN_V1,
                projection,
            )
            and tuple(projection.values())
            == authority.issuance_snapshot.public_fields
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _resolve_durable_companion_publication_ack_for_wave_c_v1(
    acknowledgement: object,
) -> _DurableCompanionPublicationAckWaveCBindingV1:
    if type(acknowledgement) is not DurableCompanionPublicationAckV1:
        raise TypeError("exact DurableCompanionPublicationAckV1 required")
    authority = _PUBLICATION_ACK_AUTHORITIES_V1.lookup(acknowledgement)
    if type(authority) is _ControllerTerminalTombstoneV1:
        if authority.lifecycle == _ACK_DISCARDED_LEGACY_CLOSED:
            raise ValueError("durable_companion_publication_ack_discarded")
        raise ValueError("durable_companion_publication_ack_invalid")
    if type(authority) is not _DurableCompanionPublicationAckAuthorityV1:
        raise ValueError("durable_companion_publication_ack_invalid")
    controller = authority.controller
    if (
        authority.lifecycle != _ACK_ACTIVE
        or authority.controller_identity is not controller._controller_identity
        or authority.publication_lock is not controller._publication_lock
        or authority.owner_pid != getpid()
        or authority.owner_thread is not current_thread()
        or controller._owner_pid != getpid()
        or controller._owner_thread is not current_thread()
        or controller._publication_epoch < authority.publication_epoch
        or not _ack_public_fields_valid_v1(acknowledgement, authority)
    ):
        raise ValueError("durable_companion_publication_ack_invalid")
    return _DurableCompanionPublicationAckWaveCBindingV1(
        controller=controller,
        authority=authority,
        acknowledgement=acknowledgement,
        parent=authority.parent,
        append_receipt=authority.append_receipt,
        candidate_state=authority.candidate_state,
        candidate_cursor=authority.candidate_cursor,
        publication_epoch=authority.publication_epoch,
        publication_lock=authority.publication_lock,
        owner_pid=authority.owner_pid,
        owner_thread=authority.owner_thread,
    )


def _validate_ack_binding_for_claim_v1(
    acknowledgement: DurableCompanionPublicationAckV1,
    binding: _DurableCompanionPublicationAckWaveCBindingV1,
) -> _DurableCompanionPublicationAckAuthorityV1:
    authority = _PUBLICATION_ACK_AUTHORITIES_V1.lookup(acknowledgement)
    if type(authority) is _ControllerTerminalTombstoneV1:
        if authority.lifecycle == _ACK_DISCARDED_LEGACY_CLOSED:
            raise ValueError("durable_companion_publication_ack_discarded")
        raise ValueError("durable_companion_publication_ack_invalid")
    if (
        type(authority) is not _DurableCompanionPublicationAckAuthorityV1
        or type(binding) is not _DurableCompanionPublicationAckWaveCBindingV1
        or binding.authority is not authority
        or binding.acknowledgement is not acknowledgement
        or binding.controller is not authority.controller
        or binding.parent is not authority.parent
        or binding.append_receipt is not authority.append_receipt
        or binding.candidate_state is not authority.candidate_state
        or binding.candidate_cursor is not authority.candidate_cursor
        or binding.publication_epoch != authority.publication_epoch
        or binding.publication_lock is not authority.publication_lock
        or binding.owner_pid != authority.owner_pid
        or binding.owner_thread is not authority.owner_thread
        or authority.lifecycle != _ACK_ACTIVE
        or not authority.publication_lock._is_owned()
        or not _ack_public_fields_valid_v1(acknowledgement, authority)
    ):
        raise ValueError("durable_companion_publication_ack_invalid")
    return authority


def _close_ack_if_complete_v1(
    acknowledgement: DurableCompanionPublicationAckV1,
    authority: _DurableCompanionPublicationAckAuthorityV1,
) -> None:
    if authority.cursor_issued and authority.facts_claimed:
        authority.lifecycle = _ACK_BOTH_CLAIMED_CLOSED
        _PUBLICATION_ACK_AUTHORITIES_V1.swap(
            acknowledgement,
            authority,
            _ControllerTerminalTombstoneV1(
                value_type="DurableCompanionPublicationAckV1",
                lifecycle=_ACK_BOTH_CLAIMED_CLOSED,
                public_fields=authority.issuance_snapshot.public_fields,
                diagnostic=acknowledgement.ack_sha256,
            ),
        )


def _claim_durable_companion_publication_ack_cursor_v1(
    acknowledgement: object,
    binding: _DurableCompanionPublicationAckWaveCBindingV1,
) -> None:
    if type(acknowledgement) is not DurableCompanionPublicationAckV1:
        raise TypeError("exact DurableCompanionPublicationAckV1 required")
    known = _PUBLICATION_ACK_AUTHORITIES_V1.lookup(acknowledgement)
    if type(known) is _ControllerTerminalTombstoneV1:
        if known.lifecycle == _ACK_DISCARDED_LEGACY_CLOSED:
            raise ValueError("durable_companion_publication_ack_discarded")
        if known.lifecycle == _ACK_BOTH_CLAIMED_CLOSED:
            raise ValueError(
                "durable_companion_publication_ack_cursor_consumed"
            )
        raise ValueError("durable_companion_publication_ack_invalid")
    authority = _validate_ack_binding_for_claim_v1(
        acknowledgement,
        binding,
    )
    if authority.cursor_issued:
        raise ValueError(
            "durable_companion_publication_ack_cursor_consumed"
        )
    authority.cursor_issued = True
    _close_ack_if_complete_v1(acknowledgement, authority)


def _claim_durable_companion_publication_ack_facts_v1(
    acknowledgement: object,
    binding: _DurableCompanionPublicationAckWaveCBindingV1,
) -> None:
    if type(acknowledgement) is not DurableCompanionPublicationAckV1:
        raise TypeError("exact DurableCompanionPublicationAckV1 required")
    known = _PUBLICATION_ACK_AUTHORITIES_V1.lookup(acknowledgement)
    if type(known) is _ControllerTerminalTombstoneV1:
        if known.lifecycle == _ACK_DISCARDED_LEGACY_CLOSED:
            raise ValueError("durable_companion_publication_ack_discarded")
        if known.lifecycle == _ACK_BOTH_CLAIMED_CLOSED:
            raise ValueError(
                "durable_companion_publication_ack_facts_consumed"
            )
        raise ValueError("durable_companion_publication_ack_invalid")
    authority = _validate_ack_binding_for_claim_v1(
        acknowledgement,
        binding,
    )
    if authority.facts_claimed:
        raise ValueError(
            "durable_companion_publication_ack_facts_consumed"
        )
    authority.facts_claimed = True
    _close_ack_if_complete_v1(acknowledgement, authority)


def _discard_legacy_companion_publication_ack_v1(
    controller: ExpertControllerV1,
    acknowledgement: DurableCompanionPublicationAckV1,
) -> None:
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(acknowledgement) is not DurableCompanionPublicationAckV1:
        raise TypeError("exact DurableCompanionPublicationAckV1 required")
    controller._require_owner()
    with controller._publication_lock:
        authority = _PUBLICATION_ACK_AUTHORITIES_V1.lookup(acknowledgement)
        if type(authority) is _ControllerTerminalTombstoneV1:
            expected = (
                controller._controller_identity.controller_identity_sha256
                + ":"
                + acknowledgement.ack_sha256
            )
            if (
                authority.lifecycle == _ACK_DISCARDED_LEGACY_CLOSED
                and authority.diagnostic == expected
            ):
                return None
            if authority.lifecycle == _ACK_DISCARDED_LEGACY_CLOSED:
                raise ValueError(
                    "durable_companion_publication_ack_discarded"
                )
            raise ValueError("durable_companion_publication_ack_invalid")
        if (
            type(authority) is not _DurableCompanionPublicationAckAuthorityV1
            or authority.controller is not controller
            or authority.controller_identity
            is not controller._controller_identity
            or authority.lifecycle != _ACK_ACTIVE
            or authority.cursor_issued is not False
            or authority.facts_claimed is not False
            or not _ack_public_fields_valid_v1(
                acknowledgement,
                authority,
            )
        ):
            raise ValueError("durable_companion_publication_ack_invalid")
        authority.lifecycle = _ACK_DISCARDED_LEGACY_CLOSED
        _PUBLICATION_ACK_AUTHORITIES_V1.swap(
            acknowledgement,
            authority,
            _ControllerTerminalTombstoneV1(
                value_type="DurableCompanionPublicationAckV1",
                lifecycle=_ACK_DISCARDED_LEGACY_CLOSED,
                public_fields=authority.issuance_snapshot.public_fields,
                diagnostic=(
                    controller._controller_identity.controller_identity_sha256
                    + ":"
                    + acknowledgement.ack_sha256
                ),
            ),
        )


def _resolve_deferred_emergency_subject_inputs_v1(
    controller: ExpertControllerV1,
    pending: PendingDurableCompanionEmergencyV1,
) -> tuple[
    BoundedIngress,
    DurableIngressParentV1,
    str,
    str,
    int,
    object,
    _DeferredEmergencyCompletionScopeV1,
]:
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(pending) is not PendingDurableCompanionEmergencyV1:
        raise TypeError("exact PendingDurableCompanionEmergencyV1 required")
    try:
        authority = _PENDING_EMERGENCY_AUTHORITIES_V1.lookup(pending)
        if (
            type(authority)
            is not _PendingDurableCompanionEmergencyAuthorityV1
            or authority.controller is not controller
            or authority.pending is not pending
            or authority.controller_identity
            is not controller._controller_identity
            or authority.ingress is not controller._ingress
            or authority.runtime is not controller._runtime
            or authority.publication_lock is not controller._publication_lock
            or authority.publication_epoch != controller._publication_epoch
            or authority.owner_pid != getpid()
            or authority.owner_thread is not current_thread()
            or controller._controller_lifecycle
            != _CONTROLLER_EMERGENCY_PENDING
            or authority.lifecycle
            not in (_PENDING_FRESH, _PENDING_COMMIT_RESERVED)
            or not _pending_public_fields_valid_v1(pending, authority)
        ):
            raise ValueError
        scope = authority.active_completion_scope
        if (
            type(scope) is not _DeferredEmergencyCompletionScopeV1
            or scope.controller is not controller
            or scope.pending is not pending
            or scope.pending_authority is not authority
            or scope.publication_lock is not controller._publication_lock
            or scope.publication_epoch != authority.publication_epoch
            or scope.owner_pid != getpid()
            or scope.owner_thread is not current_thread()
            or scope.lifecycle not in (_SCOPE_ACTIVE, _SCOPE_CLEARED)
            or scope.reservation_committed is not False
        ):
            raise ValueError
        return (
            authority.ingress,
            authority.parent,
            authority.controller_identity.controller_identity_sha256,
            pending.pending_sha256,
            authority.publication_epoch,
            authority.publication_lock,
            scope,
        )
    except (AttributeError, TypeError, ValueError):
        raise ValueError("durable_causal_subject_mismatch") from None


def _resolve_deferred_emergency_pending_for_ingress_commit_v1(
    controller: ExpertControllerV1,
    pending: PendingDurableCompanionEmergencyV1,
    subject: DeferredEmergencyCommitSubjectV1,
) -> tuple[
    _PendingDurableCompanionEmergencyAuthorityV1,
    _DeferredEmergencyCompletionScopeV1,
]:
    from tennis_v1.ingress import DeferredEmergencyCommitSubjectV1

    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(pending) is not PendingDurableCompanionEmergencyV1:
        raise TypeError("exact PendingDurableCompanionEmergencyV1 required")
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    try:
        resolved = _resolve_deferred_emergency_subject_inputs_v1(
            controller,
            pending,
        )
        ingress, _, _, _, _, publication_lock, scope = resolved
        authority = scope.pending_authority
        if (
            not publication_lock._is_owned()
            or not ingress._causal_subject_lock._is_owned()
            or authority.active_completion_scope is not scope
            or scope.subject is not subject
            or authority.retry_subject not in (None, subject)
            or authority.lifecycle != _PENDING_FRESH
            or scope.lifecycle not in (_SCOPE_ACTIVE, _SCOPE_CLEARED)
        ):
            raise ValueError
        return authority, scope
    except (AttributeError, TypeError, ValueError):
        raise ValueError("durable_causal_subject_mismatch") from None


def _consume_deferred_emergency_source_close_claim_v1(
    claim: object,
    subject: object,
) -> object:
    """Cross the future A5 boundary without granting eager A5 authority."""
    try:
        from tennis_v1.ingress import (
            DeferredEmergencyCommitSubjectV1,
            DurableCausalPrecedesProofV1,
        )
        from inci_tennis_runtime.shadow_sources import (
            DeferredEmergencySourceCloseClaimV1,
            consume_deferred_emergency_source_close_before_terminal_v1,
        )
    except (AttributeError, ImportError):
        raise ValueError(
            "durable_companion_emergency_source_close_claim_unavailable"
        ) from None
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise ValueError("durable_causal_subject_mismatch") from None
    if type(claim) is not DeferredEmergencySourceCloseClaimV1:
        raise ValueError(
            "durable_companion_emergency_source_close_claim_unavailable"
        ) from None
    try:
        consumed = consume_deferred_emergency_source_close_before_terminal_v1(
            claim,
            subject,
        )
    except (TypeError, ValueError):
        raise ValueError(
            "durable_companion_emergency_source_close_claim_unavailable"
        ) from None
    if (
        type(consumed) is not tuple
        or len(consumed) != 3
        or consumed[0] is not claim
        or consumed[1] is not subject
        or type(consumed[2]) is not DurableCausalPrecedesProofV1
    ):
        raise ValueError(
            "durable_companion_emergency_source_close_claim_unavailable"
        ) from None
    return consumed[2]


def _ordinary_terminal_handoff_valid_v1(
    controller: ExpertControllerV1,
    terminal: ExpertSessionTerminalV1,
    handoff: _DurableExpertTerminalReceiptHandoffV1,
) -> bool:
    try:
        receipt = handoff.terminal_receipt
        ExpertSessionTerminalV1.__post_init__(terminal)
        DurableExpertTerminalReceiptV1.__post_init__(receipt)
        validate_expert_terminal_against_cursor(
            terminal,
            controller._published_cursor,
        )
        snapshot = handoff.terminal_issuance_snapshot
        expected_snapshot = (
            controller._controller_identity.controller_identity_sha256,
            controller._manifest.session_id,
            "ORDINARY",
            controller._publication_epoch,
            terminal.terminal_sha256,
            expert_state_sha256(controller._published_state),
            _expert_cursor_sha256_v1(controller._published_cursor),
            _durable_expert_terminal_receipt_sha256_v1(receipt),
            receipt.durable_end_offset,
        )
        return (
            type(snapshot) is tuple
            and snapshot == expected_snapshot
            and handoff.controller is controller
            and handoff.controller_identity
            is controller._controller_identity
            and _controller_identity_public_fields_valid_v1(
                handoff.controller_identity
            )
            and handoff.lane == "ORDINARY"
            and handoff.publication_epoch
            == controller._publication_epoch
            and handoff.owner_pid == controller._owner_pid == getpid()
            and handoff.owner_thread
            is controller._owner_thread
            is current_thread()
            and terminal.session_id == controller._manifest.session_id
            and terminal.expert_manifest_sha256
            == controller._manifest.manifest_sha256
            and terminal.final_expert_state_sha256
            == expert_state_sha256(controller._published_state)
            and receipt.session_id == terminal.session_id
            and receipt.terminal_sha256 == terminal.terminal_sha256
            and receipt.terminal_frame_sequence
            == controller._published_cursor.group_count + 1
            and receipt.durable_end_offset
            == controller._durable_end_offset
            and receipt.reserve_already_consumed is True
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _claim_ordinary_durable_expert_terminal_receipt_for_alignment_v1(
    controller: ExpertControllerV1,
    terminal: ExpertSessionTerminalV1,
) -> DurableExpertTerminalReceiptV1:
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(terminal) is not ExpertSessionTerminalV1:
        raise TypeError("exact ExpertSessionTerminalV1 required")
    controller._require_owner()
    with controller._publication_lock:
        handoff_key = controller._ordinary_terminal_handoff_key
        if type(handoff_key) is not DurableEvidenceTerminalV1:
            raise ValueError(
                "durable_companion_ordinary_terminal_receipt_invalid"
            )
        handoff = _ORDINARY_TERMINAL_HANDOFFS_V1.lookup(handoff_key)
        if type(handoff) is _ControllerTerminalTombstoneV1:
            if handoff.lifecycle == "CLAIMED_BY_WAVE_C":
                raise ValueError(
                    "durable_companion_ordinary_terminal_receipt_consumed"
                )
            raise ValueError(
                "durable_companion_ordinary_terminal_receipt_invalid"
            )
        if (
            type(handoff) is not _DurableExpertTerminalReceiptHandoffV1
            or handoff.controller is not controller
            or handoff.controller_identity
            is not controller._controller_identity
            or handoff.lane != "ORDINARY"
            or handoff.lifecycle != "FRESH"
            or handoff.publication_epoch != controller._publication_epoch
            or handoff.owner_pid != getpid()
            or handoff.owner_thread is not current_thread()
            or controller._terminal is not terminal
            or controller._controller_lifecycle != _CONTROLLER_TERMINAL
            or not _ordinary_terminal_handoff_valid_v1(
                controller,
                terminal,
                handoff,
            )
        ):
            raise ValueError(
                "durable_companion_ordinary_terminal_receipt_invalid"
            )
        receipt = handoff.terminal_receipt
        if type(receipt) is not DurableExpertTerminalReceiptV1:
            raise ValueError(
                "durable_companion_ordinary_terminal_receipt_invalid"
            )
        handoff.lifecycle = "CLAIMED_BY_WAVE_C"
        _ORDINARY_TERMINAL_HANDOFFS_V1.swap(
            handoff_key,
            handoff,
            _ControllerTerminalTombstoneV1(
                value_type="DurableExpertTerminalReceiptV1",
                lifecycle="CLAIMED_BY_WAVE_C",
                public_fields=handoff.terminal_issuance_snapshot,
                diagnostic=terminal.terminal_sha256,
            ),
        )
        return receipt


def _emergency_proof_public_fields_valid_v1(
    proof: DurableCompanionEmergencyPublicationProofV1,
    authority: _DurableCompanionEmergencyPublicationProofAuthorityV1,
) -> bool:
    try:
        controller = authority.controller
        identity = authority.controller_identity
        issuance = authority.issuance_snapshot
        emergency_receipt = authority.emergency_receipt
        group_receipt = authority.group_receipt
        terminal_receipt = authority.terminal_receipt
        candidate_state = authority.candidate_state
        candidate_cursor = authority.candidate_cursor
        expert_terminal = authority.expert_terminal
        projection = {
            "schema_version": proof.schema_version,
            "session_id": proof.session_id,
            "durable_parent_envelope_sha256": (
                proof.durable_parent_envelope_sha256
            ),
            "group_receipt_sha256": proof.group_receipt_sha256,
            "terminal_receipt_sha256": proof.terminal_receipt_sha256,
            "group_sha256": proof.group_sha256,
            "terminal_sha256": proof.terminal_sha256,
            "candidate_state_sha256": proof.candidate_state_sha256,
            "candidate_cursor_sha256": proof.candidate_cursor_sha256,
            "publication_epoch": proof.publication_epoch,
        }
        ExpertStateV1.__post_init__(candidate_state)
        ExpertJournalCursorV1.__post_init__(candidate_cursor)
        ExpertSessionTerminalV1.__post_init__(expert_terminal)
        DurableExpertEmergencyReceiptV1.__post_init__(emergency_receipt)
        DurableExpertAppendReceiptV1.__post_init__(group_receipt)
        DurableExpertTerminalReceiptV1.__post_init__(terminal_receipt)
        _validate_terminal_material(
            controller._manifest,
            candidate_state,
            candidate_cursor,
            authority.terminal_envelope.terminal,
            authority.terminal_envelope.terminal,
            expert_terminal,
        )
        return (
            type(controller) is ExpertControllerV1
            and type(identity) is ExpertControllerIdentityV1
            and identity is controller._controller_identity
            and _controller_identity_public_fields_valid_v1(identity)
            and type(issuance) is _ControllerValueIssuanceSnapshotV1
            and issuance.value_type
            == "DurableCompanionEmergencyPublicationProofV1"
            and type(authority.pending)
            is PendingDurableCompanionEmergencyV1
            and type(authority.parent) is DurableIngressParentV1
            and type(authority.terminal_envelope)
            is DurableEvidenceTerminalV1
            and emergency_receipt.group_receipt is group_receipt
            and emergency_receipt.terminal_receipt is terminal_receipt
            and proof.schema_version == 1
            and proof.session_id
            == controller._manifest.session_id
            == authority.pending.session_id
            == emergency_receipt.session_id
            == group_receipt.session_id
            == terminal_receipt.session_id
            == expert_terminal.session_id
            == candidate_cursor.session_id
            and proof.durable_parent_envelope_sha256
            == authority.parent.envelope_sha256
            == authority.pending.durable_parent_envelope_sha256
            and proof.group_receipt_sha256
            == _durable_expert_append_receipt_sha256_v1(group_receipt)
            and proof.terminal_receipt_sha256
            == _durable_expert_terminal_receipt_sha256_v1(
                terminal_receipt
            )
            and proof.group_sha256 == group_receipt.group_sha256
            and proof.terminal_sha256
            == terminal_receipt.terminal_sha256
            == expert_terminal.terminal_sha256
            and proof.candidate_state_sha256
            == expert_state_sha256(candidate_state)
            == candidate_cursor.expert_state_sha256
            == group_receipt.post_expert_state_sha256
            == expert_terminal.final_expert_state_sha256
            and proof.candidate_cursor_sha256
            == _expert_cursor_sha256_v1(candidate_cursor)
            and proof.publication_epoch
            == authority.publication_epoch
            == authority.pending.publication_epoch
            == controller._publication_epoch
            and _exact_nonnegative_signed_63_v1(proof.publication_epoch)
            and group_receipt.group_sequence
            == candidate_cursor.group_count
            and group_receipt.last_parent_record_sha256
            == candidate_cursor.last_parent_record_sha256
            and group_receipt.last_expert_seq == candidate_cursor.expert_seq
            and group_receipt.final_expert_record_sha256
            == candidate_cursor.expert_record_sha256
            and group_receipt.post_expert_trace_sha256
            == candidate_cursor.expert_trace_sha256
            and terminal_receipt.terminal_frame_sequence
            == candidate_cursor.group_count + 1
            and group_receipt.durable_end_offset
            < terminal_receipt.durable_end_offset
            == controller._durable_end_offset
            and emergency_receipt.reserve_already_consumed is True
            and terminal_receipt.reserve_already_consumed is True
            and authority.publication_lock is controller._publication_lock
            and authority.owner_pid == controller._owner_pid == getpid()
            and authority.owner_thread
            is controller._owner_thread
            is current_thread()
            and controller._published_state is candidate_state
            and controller._published_cursor is candidate_cursor
            and controller._terminal is expert_terminal
            and controller._controller_lifecycle == _CONTROLLER_TERMINAL
            and proof.proof_sha256
            == issuance.digest
            == _controller_domain_sha256_v1(
                _EMERGENCY_PUBLICATION_DOMAIN_V1,
                projection,
            )
            and tuple(projection.values()) == issuance.public_fields
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _emergency_terminal_handoff_valid_v1(
    controller: ExpertControllerV1,
    proof: DurableCompanionEmergencyPublicationProofV1,
    handoff: _DurableExpertTerminalReceiptHandoffV1,
) -> bool:
    try:
        receipt = handoff.terminal_receipt
        terminal = controller._terminal
        candidate_state = controller._published_state
        candidate_cursor = controller._published_cursor
        projection = {
            "schema_version": proof.schema_version,
            "session_id": proof.session_id,
            "durable_parent_envelope_sha256": (
                proof.durable_parent_envelope_sha256
            ),
            "group_receipt_sha256": proof.group_receipt_sha256,
            "terminal_receipt_sha256": proof.terminal_receipt_sha256,
            "group_sha256": proof.group_sha256,
            "terminal_sha256": proof.terminal_sha256,
            "candidate_state_sha256": proof.candidate_state_sha256,
            "candidate_cursor_sha256": proof.candidate_cursor_sha256,
            "publication_epoch": proof.publication_epoch,
        }
        ExpertSessionTerminalV1.__post_init__(terminal)
        DurableExpertTerminalReceiptV1.__post_init__(receipt)
        validate_expert_terminal_against_cursor(terminal, candidate_cursor)
        snapshot = handoff.terminal_issuance_snapshot
        expected_snapshot = (
            controller._controller_identity.controller_identity_sha256,
            controller._manifest.session_id,
            "EMERGENCY",
            controller._publication_epoch,
            proof.proof_sha256,
            terminal.terminal_sha256,
            expert_state_sha256(candidate_state),
            _expert_cursor_sha256_v1(candidate_cursor),
            proof.group_receipt_sha256,
            _durable_expert_terminal_receipt_sha256_v1(receipt),
            receipt.durable_end_offset,
        )
        proof_record = _EMERGENCY_PROOF_AUTHORITIES_V1.lookup(proof)
        proof_record_valid = (
            type(proof_record)
            is _DurableCompanionEmergencyPublicationProofAuthorityV1
            and _emergency_proof_public_fields_valid_v1(
                proof,
                proof_record,
            )
        ) or (
            type(proof_record) is _ControllerTerminalTombstoneV1
            and proof_record.value_type
            == "DurableCompanionEmergencyPublicationProofV1"
            and proof_record.lifecycle == _PROOF_PROJECTION_CONSUMED
            and proof_record.public_fields == tuple(projection.values())
            and proof_record.diagnostic == proof.proof_sha256
        )
        return (
            proof_record_valid
            and type(snapshot) is tuple
            and snapshot == expected_snapshot
            and handoff.controller is controller
            and handoff.controller_identity
            is controller._controller_identity
            and _controller_identity_public_fields_valid_v1(
                handoff.controller_identity
            )
            and handoff.lane == "EMERGENCY"
            and handoff.publication_epoch
            == proof.publication_epoch
            == controller._publication_epoch
            and handoff.owner_pid == controller._owner_pid == getpid()
            and handoff.owner_thread
            is controller._owner_thread
            is current_thread()
            and controller._controller_lifecycle == _CONTROLLER_TERMINAL
            and proof.schema_version == 1
            and proof.session_id == controller._manifest.session_id
            and proof.candidate_state_sha256
            == expert_state_sha256(candidate_state)
            == candidate_cursor.expert_state_sha256
            == terminal.final_expert_state_sha256
            and proof.candidate_cursor_sha256
            == _expert_cursor_sha256_v1(candidate_cursor)
            and proof.terminal_sha256
            == terminal.terminal_sha256
            == receipt.terminal_sha256
            and proof.terminal_receipt_sha256
            == _durable_expert_terminal_receipt_sha256_v1(receipt)
            and receipt.session_id == proof.session_id
            and receipt.terminal_frame_sequence
            == candidate_cursor.group_count + 1
            and receipt.durable_end_offset
            == controller._durable_end_offset
            and receipt.reserve_already_consumed is True
            and proof.proof_sha256
            == _controller_domain_sha256_v1(
                _EMERGENCY_PUBLICATION_DOMAIN_V1,
                projection,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _claim_emergency_durable_expert_terminal_receipt_for_alignment_v1(
    controller: ExpertControllerV1,
    proof: DurableCompanionEmergencyPublicationProofV1,
) -> DurableExpertTerminalReceiptV1:
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(proof) is not DurableCompanionEmergencyPublicationProofV1:
        raise TypeError(
            "exact DurableCompanionEmergencyPublicationProofV1 required"
        )
    controller._require_owner()
    with controller._publication_lock:
        handoff = _EMERGENCY_TERMINAL_HANDOFFS_V1.lookup(proof)
        if type(handoff) is _ControllerTerminalTombstoneV1:
            if handoff.lifecycle == "CLAIMED_BY_WAVE_C":
                raise ValueError(
                    "durable_companion_emergency_terminal_receipt_consumed"
                )
            raise ValueError(
                "durable_companion_emergency_terminal_receipt_invalid"
            )
        if (
            type(handoff) is not _DurableExpertTerminalReceiptHandoffV1
            or handoff.controller is not controller
            or handoff.controller_identity
            is not controller._controller_identity
            or handoff.lane != "EMERGENCY"
            or handoff.lifecycle != "FRESH"
            or handoff.publication_epoch != proof.publication_epoch
            or handoff.owner_pid != getpid()
            or handoff.owner_thread is not current_thread()
            or not _emergency_terminal_handoff_valid_v1(
                controller,
                proof,
                handoff,
            )
        ):
            raise ValueError(
                "durable_companion_emergency_terminal_receipt_invalid"
            )
        receipt = handoff.terminal_receipt
        handoff.lifecycle = "CLAIMED_BY_WAVE_C"
        _EMERGENCY_TERMINAL_HANDOFFS_V1.swap(
            proof,
            handoff,
            _ControllerTerminalTombstoneV1(
                value_type="DurableExpertTerminalReceiptV1",
                lifecycle="CLAIMED_BY_WAVE_C",
                public_fields=handoff.terminal_issuance_snapshot,
                diagnostic=proof.proof_sha256,
            ),
        )
        return receipt


def _resolve_durable_companion_emergency_proof_for_wave_c_v1(
    proof: object,
) -> _DurableCompanionEmergencyProofWaveCBindingV1:
    if type(proof) is not DurableCompanionEmergencyPublicationProofV1:
        raise TypeError(
            "exact DurableCompanionEmergencyPublicationProofV1 required"
        )
    authority = _EMERGENCY_PROOF_AUTHORITIES_V1.lookup(proof)
    if type(authority) is _ControllerTerminalTombstoneV1:
        raise ValueError("durable_companion_emergency_proof_consumed")
    if (
        type(authority)
        is not _DurableCompanionEmergencyPublicationProofAuthorityV1
        or authority.lifecycle != _PROOF_ISSUED
        or authority.controller_identity
        is not authority.controller._controller_identity
        or authority.publication_lock
        is not authority.controller._publication_lock
        or authority.owner_pid != getpid()
        or authority.owner_thread is not current_thread()
        or not _emergency_proof_public_fields_valid_v1(proof, authority)
    ):
        raise ValueError("durable_companion_publication_proof_invalid")
    return _DurableCompanionEmergencyProofWaveCBindingV1(
        controller=authority.controller,
        authority=authority,
        proof=proof,
        pending=authority.pending,
        emergency_receipt=authority.emergency_receipt,
        group_receipt=authority.group_receipt,
        terminal_receipt=authority.terminal_receipt,
        candidate_state=authority.candidate_state,
        candidate_cursor=authority.candidate_cursor,
        expert_terminal=authority.expert_terminal,
        publication_epoch=authority.publication_epoch,
        publication_lock=authority.publication_lock,
        owner_pid=authority.owner_pid,
        owner_thread=authority.owner_thread,
    )


def _claim_durable_companion_emergency_proof_projection_v1(
    proof: object,
    binding: _DurableCompanionEmergencyProofWaveCBindingV1,
) -> None:
    if type(proof) is not DurableCompanionEmergencyPublicationProofV1:
        raise TypeError(
            "exact DurableCompanionEmergencyPublicationProofV1 required"
        )
    authority = _EMERGENCY_PROOF_AUTHORITIES_V1.lookup(proof)
    if type(authority) is _ControllerTerminalTombstoneV1:
        raise ValueError("durable_companion_emergency_proof_consumed")
    if (
        type(authority)
        is not _DurableCompanionEmergencyPublicationProofAuthorityV1
        or type(binding)
        is not _DurableCompanionEmergencyProofWaveCBindingV1
        or binding.authority is not authority
        or binding.proof is not proof
        or binding.controller is not authority.controller
        or binding.pending is not authority.pending
        or binding.emergency_receipt is not authority.emergency_receipt
        or binding.group_receipt is not authority.group_receipt
        or binding.terminal_receipt is not authority.terminal_receipt
        or binding.candidate_state is not authority.candidate_state
        or binding.candidate_cursor is not authority.candidate_cursor
        or binding.expert_terminal is not authority.expert_terminal
        or binding.publication_epoch != authority.publication_epoch
        or binding.publication_lock is not authority.publication_lock
        or binding.owner_pid != authority.owner_pid
        or binding.owner_thread is not authority.owner_thread
        or authority.lifecycle != _PROOF_ISSUED
        or not authority.publication_lock._is_owned()
        or not _emergency_proof_public_fields_valid_v1(proof, authority)
    ):
        raise ValueError("durable_companion_publication_proof_invalid")
    authority.lifecycle = _PROOF_PROJECTION_CONSUMED
    _EMERGENCY_PROOF_AUTHORITIES_V1.swap(
        proof,
        authority,
        _ControllerTerminalTombstoneV1(
            value_type="DurableCompanionEmergencyPublicationProofV1",
            lifecycle=_PROOF_PROJECTION_CONSUMED,
            public_fields=authority.issuance_snapshot.public_fields,
            diagnostic=proof.proof_sha256,
        ),
    )


def _issue_emergency_publication_proof_and_handoff_v1(
    controller: ExpertControllerV1,
    pending: PendingDurableCompanionEmergencyV1,
    pending_authority: _PendingDurableCompanionEmergencyAuthorityV1,
    terminal_envelope: DurableEvidenceTerminalV1,
    expert_terminal: ExpertSessionTerminalV1,
    source_close_claim: object,
    subject: DeferredEmergencyCommitSubjectV1,
    causal_proof: DurableCausalPrecedesProofV1,
    receipt: DurableExpertEmergencyReceiptV1,
) -> DurableCompanionEmergencyPublicationProofV1:
    group_receipt = receipt.group_receipt
    terminal_receipt = receipt.terminal_receipt
    candidate_state = pending_authority.emergency_candidate_state
    candidate_cursor = pending_authority.emergency_candidate_cursor
    projection = {
        "schema_version": 1,
        "session_id": pending_authority.session_id,
        "durable_parent_envelope_sha256": (
            pending_authority.parent.envelope_sha256
        ),
        "group_receipt_sha256": (
            _durable_expert_append_receipt_sha256_v1(group_receipt)
        ),
        "terminal_receipt_sha256": (
            _durable_expert_terminal_receipt_sha256_v1(terminal_receipt)
        ),
        "group_sha256": group_receipt.group_sha256,
        "terminal_sha256": terminal_receipt.terminal_sha256,
        "candidate_state_sha256": expert_state_sha256(candidate_state),
        "candidate_cursor_sha256": _expert_cursor_sha256_v1(candidate_cursor),
        "publication_epoch": pending_authority.publication_epoch,
    }
    digest = _controller_domain_sha256_v1(
        _EMERGENCY_PUBLICATION_DOMAIN_V1,
        projection,
    )
    proof = _build_opaque_controller_value_v1(
        DurableCompanionEmergencyPublicationProofV1,
        {**projection, "proof_sha256": digest},
    )
    authority = _DurableCompanionEmergencyPublicationProofAuthorityV1(
        controller=controller,
        controller_identity=controller._controller_identity,
        issuance_snapshot=_ControllerValueIssuanceSnapshotV1(
            value_type="DurableCompanionEmergencyPublicationProofV1",
            public_fields=tuple(projection.values()),
            digest=digest,
        ),
        pending=pending,
        parent=pending_authority.parent,
        source_close_claim=source_close_claim,
        subject=subject,
        causal_proof=causal_proof,
        terminal_envelope=terminal_envelope,
        emergency_receipt=receipt,
        group_receipt=group_receipt,
        terminal_receipt=terminal_receipt,
        candidate_state=candidate_state,
        candidate_cursor=candidate_cursor,
        expert_terminal=expert_terminal,
        publication_epoch=pending_authority.publication_epoch,
        publication_lock=controller._publication_lock,
        owner_pid=controller._owner_pid,
        owner_thread=controller._owner_thread,
        lifecycle=_PROOF_ISSUED,
    )
    handoff = _DurableExpertTerminalReceiptHandoffV1(
        controller=controller,
        controller_identity=controller._controller_identity,
        terminal_issuance_snapshot=(
            controller._controller_identity.controller_identity_sha256,
            controller._manifest.session_id,
            "EMERGENCY",
            pending_authority.publication_epoch,
            proof.proof_sha256,
            expert_terminal.terminal_sha256,
            expert_state_sha256(candidate_state),
            _expert_cursor_sha256_v1(candidate_cursor),
            _durable_expert_append_receipt_sha256_v1(group_receipt),
            _durable_expert_terminal_receipt_sha256_v1(terminal_receipt),
            terminal_receipt.durable_end_offset,
        ),
        terminal_receipt=terminal_receipt,
        lane="EMERGENCY",
        publication_epoch=pending_authority.publication_epoch,
        owner_pid=controller._owner_pid,
        owner_thread=controller._owner_thread,
        lifecycle="FRESH",
    )
    try:
        _EMERGENCY_PROOF_AUTHORITIES_V1.register(proof, authority)
        _EMERGENCY_TERMINAL_HANDOFFS_V1.register(proof, handoff)
        return proof
    except BaseException:
        if _EMERGENCY_PROOF_AUTHORITIES_V1.lookup(proof) is authority:
            _EMERGENCY_PROOF_AUTHORITIES_V1.unregister(proof, authority)
        _EMERGENCY_TERMINAL_HANDOFFS_V1.unregister(proof, handoff)
        raise


class ExpertControllerV1:
    __slots__ = (
        "__weakref__",
        "_manifest",
        "_universe",
        "_policy",
        "_ingress",
        "_runtime",
        "_persistence_authorizer",
        "_coordinator",
        "_writer",
        "_published_state",
        "_published_cursor",
        "_owner_pid",
        "_owner_thread",
        "_publication_lock",
        "_terminal",
        "_poisoned",
        "_closed",
        "_aborted",
        "_durable_end_offset",
        "_publication_epoch",
        "_controller_identity",
        "_controller_lifecycle",
        "_lifecycle",
        "_active_pending",
        "_ordinary_terminal_handoff_key",
        "_ordinary_reconciliation",
        "_emergency_reconciliation_terminal",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("expert controller is privately constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("expert controller cannot be subclassed")

    def __repr__(self) -> str:
        return "<ExpertControllerV1 redacted>"

    def __copy__(self):
        raise TypeError("expert controller cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("expert controller cannot be copied")

    def __reduce__(self):
        raise TypeError("expert controller cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("expert controller cannot be pickled")

    def __getstate__(self):
        raise TypeError("expert controller cannot be pickled")

    def _require_owner(self) -> None:
        if (
            getpid() != self._owner_pid
            or current_thread() is not self._owner_thread
        ):
            raise WrongOwnerThread("expert_controller_wrong_owner_thread")

    def _snapshot_unchecked(self) -> _RESULT:
        snapshot_terminal = None
        if self._controller_lifecycle == _CONTROLLER_TERMINAL:
            snapshot_terminal = self._terminal
        elif (
            self._controller_lifecycle
            == _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
            and type(self._emergency_reconciliation_terminal)
            is ExpertSessionTerminalV1
            and self._emergency_reconciliation_terminal is self._terminal
        ):
            snapshot_terminal = self._emergency_reconciliation_terminal
        return (
            self._published_state,
            self._published_cursor,
            snapshot_terminal,
        )

    def _require_available(self) -> None:
        if self._poisoned or self._closed or self._terminal is not None:
            raise RuntimeError("expert_controller_unavailable")

    def _abort_once(self) -> None:
        if self._aborted:
            return
        self._aborted = True
        try:
            abort_expert_writer(self._writer)
        except BaseException:
            pass

    def _poison(self) -> None:
        self._poisoned = True
        self._closed = True
        if self._controller_lifecycle not in (
            _CONTROLLER_TERMINAL,
            _CONTROLLER_DURABILITY_UNCERTAIN_HALTED,
        ):
            _set_controller_lifecycle_v1(
                self,
                _CONTROLLER_HALTED_UNCLEAN,
            )
        self._abort_once()

    def _require_analysis_live(self) -> None:
        try:
            decision = self._persistence_authorizer.authorize_analysis()
            if decision is not self._persistence_authorizer.bound_decision:
                raise ValueError
            if self._persistence_authorizer.poll_session() is not False:
                raise ValueError
        except Exception:
            raise ExpertLiveAuthorizationDenied() from None

    def _authorize_parent(self, parent: PersistedEvent) -> None:
        try:
            self._coordinator.require_provider_operation()
            self._persistence_authorizer.authorize_transform(parent)
            self._require_analysis_live()
        except Exception:
            raise ExpertLiveAuthorizationDenied() from None

    def _prepare_parent(
        self,
        parent: PersistedEvent,
        prior_state: ExpertStateV1,
        prior_cursor: ExpertJournalCursorV1,
    ) -> tuple[
        ExpertReductionV1,
        tuple[object, ...],
        ExpertJournalGroupV1,
        tuple[bytes, ...],
        ExpertJournalCursorV1,
        ExpertStateV1,
    ]:
        _require_exact_parent(parent, self._manifest, prior_cursor)
        self._authorize_parent(parent)
        observations = normalize_expert_parent(self._manifest, parent)
        if type(observations) is not tuple or not observations:
            raise TypeError("observations")
        _require_parent_projection(parent, observations[0].parent)
        if any(
            observation.parent != observations[0].parent
            for observation in observations
        ):
            raise ValueError("expert_observation_parent_invalid")
        reduction = reduce_expert_parent(prior_state, observations)
        group, payloads, candidate_cursor, candidate_state = _build_group(
            self._manifest,
            prior_cursor,
            reduction,
            observations[0].parent,
        )
        return (
            reduction,
            observations,
            group,
            payloads,
            candidate_cursor,
            candidate_state,
        )

    def _commit_ordinary(
        self,
        prior_state: ExpertStateV1,
        prior_cursor: ExpertJournalCursorV1,
        group: ExpertJournalGroupV1,
        payloads: tuple[bytes, ...],
        candidate_cursor: ExpertJournalCursorV1,
        candidate_state: ExpertStateV1,
    ) -> None:
        frame = encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=prior_cursor,
        )
        expected_end_offset = self._durable_end_offset + len(frame)
        with self._publication_lock:
            if (
                self._published_state is not prior_state
                or self._published_cursor is not prior_cursor
            ):
                raise ValueError("expert_append_cas_failed")
            permit = issue_expert_append_permit(
                self._writer,
                prior_cursor.expert_state_sha256,
                prior_cursor,
                group,
                payloads,
            )
            receipt = append_expert_group(permit)
            _validate_append_receipt(
                receipt,
                group,
                candidate_cursor,
                expected_end_offset,
            )
            acknowledge_expert_publication(
                self._writer,
                receipt=receipt,
                candidate_state_sha256=(
                    candidate_cursor.expert_state_sha256
                ),
                candidate_cursor=candidate_cursor,
            )
            self._published_state = candidate_state
            self._published_cursor = candidate_cursor
            self._durable_end_offset = receipt.durable_end_offset

    def _capacity_reduction(
        self,
        prior_state: ExpertStateV1,
        observations: tuple[object, ...],
    ) -> ExpertReductionV1:
        first = observations[0]
        rejected = ExpertRejectedObservationV1(
            parent=first.parent,
            parent_output_index=0,
            parent_output_count=1,
            normalizer_id=first.normalizer_id,
            normalizer_code_sha256=first.normalizer_code_sha256,
            normalizer_schema_sha256=first.normalizer_schema_sha256,
            reason=ExpertRejectReasonV1.PERSISTENCE_CAPACITY_EXCEEDED,
        )
        return reduce_expert_parent(prior_state, (rejected,))

    def _issue_capacity_pending(
        self,
        durable_parent: DurableIngressParentV1,
        store_error: ExpertPrewriteCapacityError,
        prior_state: ExpertStateV1,
        prior_cursor: ExpertJournalCursorV1,
        denied_group: ExpertJournalGroupV1,
        denied_payloads: tuple[bytes, ...],
        denied_candidate_cursor: ExpertJournalCursorV1,
        denied_candidate_state: ExpertStateV1,
        observations: tuple[object, ...],
        publication_epoch: int,
    ) -> PendingDurableCompanionEmergencyV1:
        capacity_values = (
            store_error.requested_bytes,
            store_error.available_bytes,
            store_error.emergency_reserve_bytes,
        )
        store_error.__traceback__ = None
        store_error.__cause__ = None
        store_error.__context__ = None
        if (
            not all(
                _exact_nonnegative_signed_63_v1(value)
                for value in capacity_values
            )
            or store_error.emergency_reserve_bytes
            != EXPERT_EMERGENCY_RESERVE_BYTES
        ):
            _set_controller_lifecycle_v1(
                self,
                _CONTROLLER_HALTED_UNCLEAN,
            )
            self._poisoned = True
            self._closed = True
            self._abort_once()
            raise RuntimeError(
                "expert_capacity_observation_invalid"
            ) from None
        try:
            emergency_reduction = self._capacity_reduction(
                prior_state,
                observations,
            )
            (
                emergency_group,
                emergency_payloads,
                emergency_candidate_cursor,
                emergency_candidate_state,
            ) = _build_group(
                self._manifest,
                prior_cursor,
                emergency_reduction,
                observations[0].parent,
            )
            projection = {
                "schema_version": 1,
                "session_id": self._manifest.session_id,
                "durable_parent_envelope_sha256": (
                    durable_parent.envelope_sha256
                ),
                "candidate_state_sha256": (
                    expert_state_sha256(denied_candidate_state)
                ),
                "candidate_cursor_sha256": (
                    _expert_cursor_sha256_v1(denied_candidate_cursor)
                ),
                "requested_bytes": store_error.requested_bytes,
                "available_bytes": store_error.available_bytes,
                "emergency_reserve_bytes": (
                    store_error.emergency_reserve_bytes
                ),
                "publication_epoch": publication_epoch,
            }
            digest = _controller_domain_sha256_v1(
                _CAPACITY_DENIAL_OBSERVATION_DOMAIN_V1,
                projection,
            )
            observation = _build_opaque_controller_value_v1(
                DurableCompanionCapacityDenialObservationV1,
                {**projection, "observation_sha256": digest},
            )
            authority = (
                _DurableCompanionCapacityDenialObservationAuthorityV1(
                    controller=self,
                    controller_identity=self._controller_identity,
                    issuance_snapshot=_ControllerValueIssuanceSnapshotV1(
                        value_type=(
                            "DurableCompanionCapacityDenialObservationV1"
                        ),
                        public_fields=tuple(projection.values()),
                        digest=digest,
                    ),
                    parent=durable_parent,
                    store_error=store_error,
                    prior_state=prior_state,
                    prior_cursor=prior_cursor,
                    denied_candidate_state=denied_candidate_state,
                    denied_candidate_cursor=denied_candidate_cursor,
                    denied_group=denied_group,
                    denied_payloads=denied_payloads,
                    emergency_candidate_state=emergency_candidate_state,
                    emergency_candidate_cursor=emergency_candidate_cursor,
                    emergency_group=emergency_group,
                    emergency_payloads=emergency_payloads,
                    requested_bytes=store_error.requested_bytes,
                    available_bytes=store_error.available_bytes,
                    emergency_reserve_bytes=(
                        store_error.emergency_reserve_bytes
                    ),
                    publication_epoch=publication_epoch,
                    owner_pid=self._owner_pid,
                    owner_thread=self._owner_thread,
                    lifecycle="ISSUED",
                )
            )
            try:
                _CAPACITY_OBSERVATION_AUTHORITIES_V1.register(
                    observation,
                    authority,
                )
                return _issue_pending_durable_companion_emergency_v1(
                    observation,
                    candidate_state=denied_candidate_state,
                    candidate_cursor=denied_candidate_cursor,
                )
            except BaseException:
                if (
                    _CAPACITY_OBSERVATION_AUTHORITIES_V1.lookup(observation)
                    is authority
                ):
                    _CAPACITY_OBSERVATION_AUTHORITIES_V1.unregister(
                        observation,
                        authority,
                    )
                authority.store_error = None
                authority.lifecycle = "ISSUANCE_FAILED_CLOSED"
                raise
        except BaseException:
            _set_controller_lifecycle_v1(
                self,
                _CONTROLLER_HALTED_UNCLEAN,
            )
            self._poisoned = True
            self._closed = True
            self._abort_once()
            raise RuntimeError(
                "durable_companion_capacity_pending_unavailable"
            ) from None

    def process_durable_parent(
        self,
        durable_parent: DurableIngressParentV1,
    ) -> tuple[
        ExpertStateV1,
        ExpertJournalCursorV1,
        DurableCompanionPublicationAckV1
        | PendingDurableCompanionEmergencyV1,
    ]:
        if type(durable_parent) is not DurableIngressParentV1:
            raise TypeError("exact DurableIngressParentV1 required")
        self._require_owner()
        from tennis_v1.ingress import (
            _consume_durable_ingress_parent_v1,
            _validate_durable_ingress_parent_for_consumer_v1,
        )

        with self._publication_lock:
            if self._controller_lifecycle != _CONTROLLER_ACTIVE:
                raise RuntimeError("expert_controller_unavailable")
            if self._publication_epoch >= _SIGNED_63_MAX:
                _set_controller_lifecycle_v1(
                    self,
                    _CONTROLLER_HALTED_UNCLEAN,
                )
                self._poisoned = True
                self._closed = True
                self._abort_once()
                raise RuntimeError(
                    "durable_companion_publication_epoch_exhausted"
                ) from None
            _validate_durable_ingress_parent_for_consumer_v1(
                durable_parent,
                self,
            )
            _consume_durable_ingress_parent_v1(durable_parent, self)
            publication_epoch = self._publication_epoch + 1
            prior_state = self._published_state
            prior_cursor = self._published_cursor
            try:
                (
                    _,
                    observations,
                    group,
                    payloads,
                    candidate_cursor,
                    candidate_state,
                ) = self._prepare_parent(
                    durable_parent.parent,
                    prior_state,
                    prior_cursor,
                )
                frame = encode_expert_group_frame(
                    group,
                    payloads,
                    prior_cursor=prior_cursor,
                )
                expected_end_offset = self._durable_end_offset + len(frame)
            except BaseException:
                _set_controller_lifecycle_v1(
                    self,
                    _CONTROLLER_HALTED_UNCLEAN,
                )
                self._poisoned = True
                self._closed = True
                self._abort_once()
                raise RuntimeError(
                    "expert_consumed_parent_processing_failed"
                ) from None
            try:
                reconciliation = _DefinitelyDurableOrdinaryReconciliationV1(
                    lane="PARENT",
                    receipt=None,
                    candidate_state=candidate_state,
                    candidate_cursor=candidate_cursor,
                    expert_terminal=None,
                    publication_epoch=publication_epoch,
                    durable_end_offset=expected_end_offset,
                    store_acknowledged=False,
                )
                permit = issue_expert_append_permit(
                    self._writer,
                    prior_cursor.expert_state_sha256,
                    prior_cursor,
                    group,
                    payloads,
                )
            except ExpertPrewriteCapacityError as store_error:
                if type(store_error) is not ExpertPrewriteCapacityError:
                    _set_controller_lifecycle_v1(
                        self,
                        _CONTROLLER_HALTED_UNCLEAN,
                    )
                    self._poisoned = True
                    self._closed = True
                    self._abort_once()
                    raise RuntimeError(
                        "expert_capacity_observation_invalid"
                    ) from None
                return (
                    prior_state,
                    prior_cursor,
                    self._issue_capacity_pending(
                        durable_parent,
                        store_error,
                        prior_state,
                        prior_cursor,
                        group,
                        payloads,
                        candidate_cursor,
                        candidate_state,
                        observations,
                        publication_epoch,
                    ),
                )
            except BaseException:
                _set_controller_lifecycle_v1(
                    self,
                    _CONTROLLER_HALTED_UNCLEAN,
                )
                self._poisoned = True
                self._closed = True
                self._abort_once()
                raise RuntimeError(
                    "expert_consumed_parent_processing_failed"
                ) from None
            try:
                receipt = append_expert_group(permit)
                _validate_append_receipt(
                    receipt,
                    group,
                    candidate_cursor,
                    expected_end_offset,
                )
                reconciliation.receipt = receipt
                self._ordinary_reconciliation = reconciliation
                acknowledge_expert_publication(
                    self._writer,
                    receipt=receipt,
                    candidate_state_sha256=(
                        candidate_cursor.expert_state_sha256
                    ),
                    candidate_cursor=candidate_cursor,
                )
                reconciliation.store_acknowledged = True
            except BaseException:
                self._controller_lifecycle = (
                    _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                )
                self._lifecycle = _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                self._poisoned = True
                self._closed = True
                self._abort_once()
                raise RuntimeError(
                    "expert_consumed_parent_processing_failed"
                ) from None
            self._published_state = candidate_state
            self._published_cursor = candidate_cursor
            self._durable_end_offset = receipt.durable_end_offset
            self._publication_epoch = publication_epoch
            try:
                acknowledgement = _issue_publication_ack_v1(
                    self,
                    durable_parent,
                    receipt,
                    candidate_state,
                    candidate_cursor,
                    publication_epoch,
                )
            except BaseException:
                _set_controller_lifecycle_v1(
                    self,
                    _CONTROLLER_HALTED_UNCLEAN,
                )
                self._poisoned = True
                self._closed = True
                self._abort_once()
                raise RuntimeError(
                    "durable_companion_publication_ack_unavailable"
                ) from None
            self._ordinary_reconciliation = None
            return candidate_state, candidate_cursor, acknowledgement

    def process_evidence_terminal(
        self,
        terminal: DurableEvidenceTerminalV1,
    ) -> tuple[
        ExpertStateV1,
        ExpertJournalCursorV1,
        ExpertSessionTerminalV1,
    ]:
        if type(terminal) is not DurableEvidenceTerminalV1:
            raise TypeError("exact DurableEvidenceTerminalV1 required")
        self._require_owner()
        from tennis_v1.ingress import (
            _consume_durable_evidence_terminal_v1,
            _validate_durable_evidence_terminal_for_consumer_v1,
        )

        with self._publication_lock:
            if self._controller_lifecycle == _CONTROLLER_EMERGENCY_PENDING:
                raise RuntimeError("expert_controller_emergency_pending")
            if self._controller_lifecycle != _CONTROLLER_ACTIVE:
                raise RuntimeError("expert_controller_unavailable")
            _validate_durable_evidence_terminal_for_consumer_v1(
                terminal,
                self,
            )
            _consume_durable_evidence_terminal_v1(terminal, self)
            state = self._published_state
            cursor = self._published_cursor
            entered_append = False
            try:
                unseen = prove_expert_live_evidence_tail(
                    self._writer,
                    published_cursor=cursor,
                )
                if unseen is not None:
                    raise ValueError("expert_unacknowledged_evidence_tail")
                evidence, expert_terminal = build_aligned_expert_terminal(
                    self._writer,
                    final_state=state,
                    final_cursor=cursor,
                )
                _validate_terminal_material(
                    self._manifest,
                    state,
                    cursor,
                    terminal.terminal,
                    evidence,
                    expert_terminal,
                )
                terminal_frame = encode_expert_terminal_frame(
                    expert_terminal,
                    final_cursor=cursor,
                )
                permit = issue_expert_terminal_permit(
                    self._writer,
                    expert_terminal,
                )
                reconciliation = _DefinitelyDurableOrdinaryReconciliationV1(
                    lane="TERMINAL",
                    receipt=None,
                    candidate_state=state,
                    candidate_cursor=cursor,
                    expert_terminal=expert_terminal,
                    publication_epoch=self._publication_epoch,
                    durable_end_offset=(
                        self._durable_end_offset + len(terminal_frame)
                    ),
                    store_acknowledged=True,
                )
                entered_append = True
                receipt = append_expert_terminal(permit)
                _validate_terminal_receipt(
                    receipt,
                    expert_terminal,
                    cursor,
                    self._durable_end_offset + len(terminal_frame),
                )
                reconciliation.receipt = receipt
                self._ordinary_reconciliation = reconciliation
            except ValueError as error:
                _set_controller_lifecycle_v1(
                    self,
                    (
                        _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                        if entered_append
                        else _CONTROLLER_HALTED_UNCLEAN
                    ),
                )
                self._poisoned = True
                self._closed = True
                self._abort_once()
                if str(error) == "expert_unacknowledged_evidence_tail":
                    raise ValueError(
                        "expert_unacknowledged_evidence_tail"
                    ) from None
                raise RuntimeError(
                    "expert_consumed_terminal_processing_failed"
                ) from None
            except BaseException:
                _set_controller_lifecycle_v1(
                    self,
                    (
                        _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                        if entered_append
                        else _CONTROLLER_HALTED_UNCLEAN
                    ),
                )
                self._poisoned = True
                self._closed = True
                self._abort_once()
                raise RuntimeError(
                    "expert_consumed_terminal_processing_failed"
                ) from None
            handoff: _DurableExpertTerminalReceiptHandoffV1 | None = None
            try:
                handoff = _DurableExpertTerminalReceiptHandoffV1(
                    controller=self,
                    controller_identity=self._controller_identity,
                    terminal_issuance_snapshot=(
                        self._controller_identity.controller_identity_sha256,
                        self._manifest.session_id,
                        "ORDINARY",
                        self._publication_epoch,
                        expert_terminal.terminal_sha256,
                        expert_state_sha256(state),
                        _expert_cursor_sha256_v1(cursor),
                        _durable_expert_terminal_receipt_sha256_v1(receipt),
                        receipt.durable_end_offset,
                    ),
                    terminal_receipt=receipt,
                    lane="ORDINARY",
                    publication_epoch=self._publication_epoch,
                    owner_pid=self._owner_pid,
                    owner_thread=self._owner_thread,
                    lifecycle="FRESH",
                )
                _ORDINARY_TERMINAL_HANDOFFS_V1.register(
                    terminal,
                    handoff,
                )
            except BaseException:
                if (
                    type(handoff)
                    is _DurableExpertTerminalReceiptHandoffV1
                    and _ORDINARY_TERMINAL_HANDOFFS_V1.lookup(terminal)
                    is handoff
                ):
                    _ORDINARY_TERMINAL_HANDOFFS_V1.unregister(
                        terminal,
                        handoff,
                    )
                _set_controller_lifecycle_v1(
                    self,
                    _CONTROLLER_HALTED_UNCLEAN,
                )
                self._poisoned = True
                self._abort_once()
                raise RuntimeError(
                    "durable_companion_terminal_receipt_unavailable"
                ) from None
            self._ordinary_terminal_handoff_key = terminal
            self._terminal = expert_terminal
            self._durable_end_offset = receipt.durable_end_offset
            self._closed = True
            _set_controller_lifecycle_v1(self, _CONTROLLER_TERMINAL)
            self._ordinary_reconciliation = None
            return state, cursor, expert_terminal

    def _converge_reserved_emergency_failure_close_v1(
        self,
        causal_proof: object,
        prepared: object,
        subject: object,
        pending: PendingDurableCompanionEmergencyV1,
        terminal: DurableEvidenceTerminalV1,
    ) -> None:
        from tennis_v1.ingress import (
            _close_durable_causal_precedes_proof_after_deferred_append_failure_v1,
            _converge_durable_causal_precedes_proof_after_deferred_append_failure_v1,
        )

        try:
            _close_durable_causal_precedes_proof_after_deferred_append_failure_v1(
                causal_proof,
                subject=subject,
                controller=self,
                pending=pending,
                terminal=terminal,
            )
        except BaseException:
            _converge_durable_causal_precedes_proof_after_deferred_append_failure_v1(
                prepared
            )

    def _prepare_reserved_emergency_before_causal_commit_v1(
        self,
        pending: PendingDurableCompanionEmergencyV1,
        terminal: DurableEvidenceTerminalV1,
        source_close_claim: object,
        subject: object,
        authority: _PendingDurableCompanionEmergencyAuthorityV1,
        scope: _DeferredEmergencyCompletionScopeV1,
        resolved: tuple[object, ...],
        publication_failed_tombstone: _ControllerTerminalTombstoneV1,
    ) -> tuple[object, ExpertSessionTerminalV1, DurableExpertEmergencyReceiptV1, object]:
        from tennis_v1.ingress import (
            _consume_durable_evidence_terminal_v1,
            _lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1,
            _prepare_durable_causal_precedes_proof_commit_v1,
        )

        causal_proof = None
        prepared = None
        try:
            current = _resolve_deferred_emergency_subject_inputs_v1(
                self,
                pending,
            )
            if (
                type(current) is not tuple
                or len(current) != 7
                or any(
                    current[index] is not resolved[index]
                    for index in (0, 1, 5, 6)
                )
                or current[2:5] != resolved[2:5]
                or authority.active_completion_scope is not scope
                or authority.lifecycle != _PENDING_FRESH
                or scope.lifecycle != _SCOPE_ACTIVE
            ):
                raise ValueError("durable_causal_subject_mismatch")
            scope.source_close_claim = source_close_claim
            scope.subject = subject
            authority.retry_subject = subject
            causal_proof = _consume_deferred_emergency_source_close_claim_v1(
                source_close_claim,
                subject,
            )
            authority.retry_subject = None
            if (
                authority.lifecycle != _PENDING_COMMIT_RESERVED
                or scope.lifecycle != _SCOPE_RESERVATION_COMMITTED
                or scope.reservation_committed is not True
                or scope.subject is not subject
                or scope.causal_proof is not causal_proof
                or authority.reserved_subject is not subject
                or authority.reserved_terminal is not terminal
                or authority.reserved_causal_proof is not causal_proof
                or authority.reserved_completion_scope is not scope
            ):
                raise ValueError("durable_causal_subject_mismatch")
            _consume_durable_evidence_terminal_v1(terminal, self)
            unseen = prove_expert_live_evidence_tail(
                self._writer,
                published_cursor=authority.prior_cursor,
            )
            if unseen is not authority.parent.parent:
                raise ValueError("expert_emergency_parent_invalid")
            validate_expert_group_against_cursor(
                authority.emergency_group,
                authority.emergency_payloads,
                authority.prior_cursor,
            )
            evidence, expert_terminal = build_aligned_expert_terminal(
                self._writer,
                final_state=authority.emergency_candidate_state,
                final_cursor=authority.emergency_candidate_cursor,
            )
            _validate_terminal_material(
                self._manifest,
                authority.emergency_candidate_state,
                authority.emergency_candidate_cursor,
                terminal.terminal,
                evidence,
                expert_terminal,
            )
            group_frame = encode_expert_group_frame(
                authority.emergency_group,
                authority.emergency_payloads,
                prior_cursor=authority.prior_cursor,
            )
            terminal_frame = encode_expert_terminal_frame(
                expert_terminal,
                final_cursor=authority.emergency_candidate_cursor,
            )
            if (
                len(group_frame) > MAX_EXPERT_FRAME_BYTES
                or len(terminal_frame) > MAX_EXPERT_TERMINAL_FRAME_BYTES
                or len(group_frame) + len(terminal_frame)
                > EXPERT_EMERGENCY_RESERVE_BYTES
            ):
                raise ValueError("expert_emergency_frame_capacity_invalid")
            permit = issue_expert_emergency_append_permit(
                self._writer,
                expected_state_sha256=(
                    authority.prior_cursor.expert_state_sha256
                ),
                expected_cursor=authority.prior_cursor,
                evidence_terminal=evidence,
                group=authority.emergency_group,
                payloads=authority.emergency_payloads,
                terminal=expert_terminal,
            )
            prepared = _prepare_durable_causal_precedes_proof_commit_v1(
                causal_proof,
                subject=subject,
                controller=self,
                pending=pending,
                terminal=terminal,
            )
            receipt = append_expert_emergency_group_and_terminal(permit)
            _validate_emergency_receipt(
                receipt,
                authority.emergency_group,
                authority.emergency_candidate_cursor,
                expert_terminal,
                self._durable_end_offset + len(group_frame),
                self._durable_end_offset + len(group_frame) + len(terminal_frame),
            )
            return prepared, expert_terminal, receipt, causal_proof
        except BaseException:
            if (
                authority.lifecycle == _PENDING_FRESH
                and scope.reservation_committed is False
            ):
                scope.lifecycle = _SCOPE_CLEARED
                raise
            if causal_proof is None:
                causal_proof = authority.reserved_causal_proof
            if prepared is None and causal_proof is not None:
                prepared = (
                    _lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1(
                        causal_proof
                    )
                )
            self._converge_reserved_emergency_failure_close_v1(
                causal_proof,
                prepared,
                subject,
                pending,
                terminal,
            )
            authority.active_completion_scope = None
            authority.retry_subject = None
            authority.reserved_claim = None
            authority.reserved_subject = None
            authority.reserved_terminal = None
            authority.reserved_causal_proof = None
            authority.reserved_completion_scope = None
            self._active_pending = None
            self._poisoned = True
            self._closed = True
            self._controller_lifecycle = _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
            self._lifecycle = _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
            try:
                _PENDING_EMERGENCY_AUTHORITIES_V1.swap(
                    pending,
                    authority,
                    publication_failed_tombstone,
                )
            except BaseException:
                try:
                    _PENDING_EMERGENCY_AUTHORITIES_V1.converge_replace_after_uncertain(
                        pending,
                        authority,
                        publication_failed_tombstone,
                    )
                except BaseException:
                    pass
            self._abort_once()
            raise RuntimeError(
                "durable_companion_emergency_publication_uncertain"
            ) from None

    def complete_pending_emergency(
        self,
        pending: PendingDurableCompanionEmergencyV1,
        terminal: DurableEvidenceTerminalV1,
        source_close_claim: object,
    ) -> tuple[
        ExpertStateV1,
        ExpertJournalCursorV1,
        ExpertSessionTerminalV1,
        DurableCompanionEmergencyPublicationProofV1,
    ]:
        if type(pending) is not PendingDurableCompanionEmergencyV1:
            raise TypeError(
                "exact PendingDurableCompanionEmergencyV1 required"
            )
        if type(terminal) is not DurableEvidenceTerminalV1:
            raise TypeError("exact DurableEvidenceTerminalV1 required")
        self._require_owner()
        from tennis_v1.ingress import (
            _commit_prepared_durable_causal_precedes_proof_v1,
            _issue_deferred_emergency_commit_subject_v1,
            _validate_durable_evidence_terminal_for_consumer_v1,
        )

        with self._publication_lock:
            authority = _PENDING_EMERGENCY_AUTHORITIES_V1.lookup(pending)
            if (
                type(authority)
                is not _PendingDurableCompanionEmergencyAuthorityV1
                or authority.controller is not self
                or authority.pending is not pending
                or authority.controller_identity
                is not self._controller_identity
                or authority.lifecycle != _PENDING_FRESH
                or self._controller_lifecycle
                != _CONTROLLER_EMERGENCY_PENDING
                or self._active_pending is not pending
                or authority.publication_epoch != self._publication_epoch
                or authority.owner_pid != getpid()
                or authority.owner_thread is not current_thread()
                or authority.active_completion_scope is not None
                and authority.active_completion_scope.lifecycle
                not in (_SCOPE_CLEARED,)
                or not _pending_public_fields_valid_v1(pending, authority)
            ):
                raise ValueError("durable_companion_emergency_pending_invalid")
            _validate_durable_evidence_terminal_for_consumer_v1(
                terminal,
                self,
            )
            scope = _DeferredEmergencyCompletionScopeV1(
                controller=self,
                pending=pending,
                pending_authority=authority,
                terminal=terminal,
                publication_lock=self._publication_lock,
                publication_epoch=authority.publication_epoch,
                owner_pid=self._owner_pid,
                owner_thread=self._owner_thread,
                lifecycle=_SCOPE_ACTIVE,
                reservation_committed=False,
                source_close_claim=None,
                subject=None,
                causal_proof=None,
            )
            authority.active_completion_scope = scope
        try:
            publication_failed_tombstone = _pending_terminal_tombstone_v1(
                pending,
                _PENDING_PUBLICATION_FAILED_CLOSED,
            )
            proof_unavailable_tombstone = _pending_terminal_tombstone_v1(
                pending,
                _PENDING_PUBLISHED_PROOF_UNAVAILABLE_CLOSED,
            )
            published_tombstone = _pending_terminal_tombstone_v1(
                pending,
                _PENDING_PUBLISHED,
            )
            subject = _issue_deferred_emergency_commit_subject_v1(
                controller=self,
                pending=pending,
                terminal=terminal,
            )
            resolved = _resolve_deferred_emergency_subject_inputs_v1(
                self,
                pending,
            )
            if type(resolved) is not tuple or len(resolved) != 7:
                raise ValueError("durable_causal_subject_mismatch")
            (
                ingress,
                resolved_parent,
                resolved_controller_identity_sha256,
                resolved_pending_sha256,
                resolved_publication_epoch,
                publication_lock,
                resolved_scope,
            ) = resolved
            if (
                ingress is not authority.ingress
                or resolved_parent is not authority.parent
                or resolved_controller_identity_sha256
                != self._controller_identity.controller_identity_sha256
                or resolved_pending_sha256 != pending.pending_sha256
                or resolved_publication_epoch != authority.publication_epoch
                or publication_lock is not self._publication_lock
                or resolved_scope is not scope
            ):
                raise ValueError("durable_causal_subject_mismatch")
        except BaseException:
            with self._publication_lock:
                if (
                    authority.lifecycle == _PENDING_FRESH
                    and authority.active_completion_scope is scope
                    and scope.reservation_committed is False
                ):
                    scope.lifecycle = _SCOPE_CLEARED
            raise

        with publication_lock:
            with ingress._causal_subject_lock:
                (
                    prepared,
                    expert_terminal,
                    receipt,
                    causal_proof,
                ) = self._prepare_reserved_emergency_before_causal_commit_v1(
                    pending,
                    terminal,
                    source_close_claim,
                    subject,
                    authority,
                    scope,
                    resolved,
                    publication_failed_tombstone,
                )
                _commit_prepared_durable_causal_precedes_proof_v1(prepared)
                authority.lifecycle = _PENDING_PUBLISHED
                scope.lifecycle = _SCOPE_PUBLISHED_CLOSED
                self._published_state = authority.emergency_candidate_state
                self._published_cursor = authority.emergency_candidate_cursor
                self._terminal = expert_terminal
                self._emergency_reconciliation_terminal = expert_terminal
                self._durable_end_offset = (
                    receipt.terminal_receipt.durable_end_offset
                )
                self._closed = True
                self._active_pending = None
                self._controller_lifecycle = _CONTROLLER_TERMINAL
                self._lifecycle = _CONTROLLER_TERMINAL

            try:
                _PENDING_EMERGENCY_AUTHORITIES_V1.swap(
                    pending,
                    authority,
                    published_tombstone,
                )
                proof = _issue_emergency_publication_proof_and_handoff_v1(
                    self,
                    pending,
                    authority,
                    terminal,
                    expert_terminal,
                    source_close_claim,
                    subject,
                    causal_proof,
                    receipt,
                )
            except BaseException:
                authority.lifecycle = (
                    _PENDING_PUBLISHED_PROOF_UNAVAILABLE_CLOSED
                )
                scope.lifecycle = _SCOPE_PUBLISHED_CLOSED
                self._poisoned = True
                self._controller_lifecycle = (
                    _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                )
                self._lifecycle = (
                    _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                )
                authority.active_completion_scope = None
                authority.retry_subject = None
                authority.reserved_claim = None
                authority.reserved_subject = None
                authority.reserved_terminal = None
                authority.reserved_causal_proof = None
                authority.reserved_completion_scope = None
                try:
                    current = _PENDING_EMERGENCY_AUTHORITIES_V1.lookup(
                        pending
                    )
                    if current is authority:
                        _PENDING_EMERGENCY_AUTHORITIES_V1.converge_replace_after_uncertain(
                            pending,
                            authority,
                            proof_unavailable_tombstone,
                        )
                    elif current is published_tombstone:
                        _PENDING_EMERGENCY_AUTHORITIES_V1.converge_replace_after_uncertain(
                            pending,
                            published_tombstone,
                            proof_unavailable_tombstone,
                        )
                    elif current is not proof_unavailable_tombstone:
                        raise ValueError(
                            "durable_companion_emergency_pending_invalid"
                        )
                except BaseException:
                    pass
                self._abort_once()
                raise RuntimeError(
                    "durable_companion_emergency_publication_ack_unavailable"
                ) from None
            authority.active_completion_scope = None
            self._emergency_reconciliation_terminal = None
            return (
                authority.emergency_candidate_state,
                authority.emergency_candidate_cursor,
                expert_terminal,
                proof,
            )

    def abort_pending_durable_companion_emergency_v1(
        self,
        pending: PendingDurableCompanionEmergencyV1,
        *,
        reason: PendingEmergencyAbortReasonV1,
    ) -> PendingDurableCompanionEmergencyAbortReceiptV1:
        if type(pending) is not PendingDurableCompanionEmergencyV1:
            raise TypeError(
                "exact PendingDurableCompanionEmergencyV1 required"
            )
        if type(reason) is not PendingEmergencyAbortReasonV1:
            raise TypeError("exact PendingEmergencyAbortReasonV1 required")
        self._require_owner()
        from tennis_v1.ingress import (
            _abort_deferred_emergency_commit_subject_v1,
        )

        with self._publication_lock:
            authority = _PENDING_EMERGENCY_AUTHORITIES_V1.lookup(pending)
            if type(authority) is _ControllerTerminalTombstoneV1:
                retained = authority.retained_receipt
                if (
                    authority.lifecycle == _PENDING_ABORTED_NONPUBLICATION
                    and type(retained)
                    is PendingDurableCompanionEmergencyAbortReceiptV1
                    and retained.abort_reason is reason
                    and authority.diagnostic
                    == self._controller_identity.controller_identity_sha256
                    + ":"
                    + pending.pending_sha256
                ):
                    return retained
                raise ValueError(
                    "durable_companion_emergency_pending_abort_consumed"
                )
            if (
                type(authority)
                is not _PendingDurableCompanionEmergencyAuthorityV1
                or authority.controller is not self
                or authority.pending is not pending
                or authority.controller_identity
                is not self._controller_identity
                or authority.lifecycle != _PENDING_FRESH
                or self._controller_lifecycle
                != _CONTROLLER_EMERGENCY_PENDING
                or self._active_pending is not pending
                or authority.publication_epoch != self._publication_epoch
                or authority.owner_pid != getpid()
                or authority.owner_thread is not current_thread()
                or not _pending_public_fields_valid_v1(pending, authority)
            ):
                raise ValueError(
                    "durable_companion_emergency_pending_abort_invalid"
                )
            receipt = _issue_pending_abort_receipt_v1(authority, reason)
            abort_failure_tombstone = _pending_terminal_tombstone_v1(
                pending,
                _PENDING_ABORT_FAILED_CLOSED,
            )
            abort_success_tombstone = _ControllerTerminalTombstoneV1(
                value_type="PendingDurableCompanionEmergencyV1",
                lifecycle=_PENDING_ABORTED_NONPUBLICATION,
                public_fields=(
                    pending.pending_sha256,
                    pending.capacity_denial_sha256,
                    pending.publication_epoch,
                    reason.value,
                ),
                diagnostic=(
                    self._controller_identity.controller_identity_sha256
                    + ":"
                    + pending.pending_sha256
                ),
                retained_receipt=receipt,
            )
            authority.prepared_abort_reason = reason
            authority.prepared_abort_receipt = receipt
            scope = authority.active_completion_scope
            terminal = None
            subject = authority.retry_subject
            if type(scope) is _DeferredEmergencyCompletionScopeV1:
                terminal = scope.terminal
                scope.lifecycle = _SCOPE_CLEARED
                scope.reservation_committed = False
            abort_uncertain = False
            try:
                _abort_deferred_emergency_commit_subject_v1(
                    subject,
                    self,
                    pending,
                    terminal,
                )
            except RuntimeError as error:
                if (
                    type(error) is not RuntimeError
                    or error.args
                    != ("durable_causal_subject_abort_uncertain",)
                ):
                    authority.prepared_abort_reason = None
                    authority.prepared_abort_receipt = None
                    raise
                abort_uncertain = True
            if abort_uncertain:
                authority.lifecycle = _PENDING_ABORT_FAILED_CLOSED
                authority.prepared_abort_reason = None
                authority.prepared_abort_receipt = None
                authority.active_completion_scope = None
                authority.retry_subject = None
                _set_controller_lifecycle_v1(
                    self,
                    _CONTROLLER_DURABILITY_UNCERTAIN_HALTED,
                )
                self._active_pending = None
                self._poisoned = True
                self._closed = True
                try:
                    _PENDING_EMERGENCY_AUTHORITIES_V1.swap(
                        pending,
                        authority,
                        abort_failure_tombstone,
                    )
                except BaseException:
                    try:
                        _PENDING_EMERGENCY_AUTHORITIES_V1.converge_replace_after_uncertain(
                            pending,
                            authority,
                            abort_failure_tombstone,
                        )
                    except BaseException:
                        pass
                self._abort_once()
                raise RuntimeError(
                    "durable_companion_emergency_pending_abort_uncertain"
                ) from None
            authority.lifecycle = _PENDING_ABORTED_NONPUBLICATION
            authority.abort_reason = reason
            authority.abort_receipt = receipt
            authority.prepared_abort_reason = None
            authority.prepared_abort_receipt = None
            authority.active_completion_scope = None
            authority.retry_subject = None
            self._controller_lifecycle = _CONTROLLER_HALTED_UNCLEAN
            self._lifecycle = _CONTROLLER_HALTED_UNCLEAN
            self._active_pending = None
            self._poisoned = True
            self._closed = True
            abort_publication_uncertain = False
            try:
                _PENDING_EMERGENCY_AUTHORITIES_V1.swap(
                    pending,
                    authority,
                    abort_success_tombstone,
                )
            except BaseException:
                abort_publication_uncertain = True
            if abort_publication_uncertain:
                authority.lifecycle = _PENDING_ABORT_FAILED_CLOSED
                authority.abort_reason = None
                authority.abort_receipt = None
                authority.prepared_abort_reason = None
                authority.prepared_abort_receipt = None
                try:
                    current = _PENDING_EMERGENCY_AUTHORITIES_V1.lookup(
                        pending
                    )
                    if current is abort_success_tombstone:
                        _PENDING_EMERGENCY_AUTHORITIES_V1.converge_replace_after_uncertain(
                            pending,
                            abort_success_tombstone,
                            abort_failure_tombstone,
                        )
                    elif current is authority:
                        _PENDING_EMERGENCY_AUTHORITIES_V1.converge_replace_after_uncertain(
                            pending,
                            authority,
                            abort_failure_tombstone,
                        )
                    elif current is not abort_failure_tombstone:
                        raise ValueError(
                            "durable_companion_emergency_pending_abort_invalid"
                        )
                except BaseException:
                    pass
                self._controller_lifecycle = (
                    _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                )
                self._lifecycle = (
                    _CONTROLLER_DURABILITY_UNCERTAIN_HALTED
                )
                self._abort_once()
                raise RuntimeError(
                    "durable_companion_emergency_pending_abort_uncertain"
                ) from None
            self._abort_once()
            return receipt

    def _commit_emergency(
        self,
        parent: PersistedEvent,
        prior_state: ExpertStateV1,
        prior_cursor: ExpertJournalCursorV1,
        observations: tuple[object, ...],
        *,
        known_evidence: PersistedEvent | None,
        tail_already_proven: bool,
    ) -> _RESULT:
        evidence_terminal = known_evidence
        if not tail_already_proven:
            evidence_terminal = self._ingress.close_external_halt(
                self._runtime
            )
            if type(evidence_terminal) is not PersistedEvent:
                raise ValueError("expert_emergency_terminal_missing")
            unseen = prove_expert_live_evidence_tail(
                self._writer,
                published_cursor=prior_cursor,
            )
        else:
            unseen = parent
        if type(unseen) is not PersistedEvent:
            raise ValueError("expert_emergency_parent_missing")
        _require_exact_parent(unseen, self._manifest, prior_cursor)
        if (
            canonical_record_sha256(unseen)
            != canonical_record_sha256(parent)
            or unseen != parent
        ):
            raise ValueError("expert_emergency_parent_invalid")
        reduction = self._capacity_reduction(
            prior_state,
            observations,
        )
        group, payloads, candidate_cursor, candidate_state = _build_group(
            self._manifest,
            prior_cursor,
            reduction,
            observations[0].parent,
        )
        built_evidence, terminal = build_aligned_expert_terminal(
            self._writer,
            final_state=candidate_state,
            final_cursor=candidate_cursor,
        )
        _validate_terminal_material(
            self._manifest,
            candidate_state,
            candidate_cursor,
            evidence_terminal,
            built_evidence,
            terminal,
        )
        group_frame = encode_expert_group_frame(
            group,
            payloads,
            prior_cursor=prior_cursor,
        )
        terminal_frame = encode_expert_terminal_frame(
            terminal,
            final_cursor=candidate_cursor,
        )
        if (
            len(group_frame) > MAX_EXPERT_FRAME_BYTES
            or len(terminal_frame) > MAX_EXPERT_TERMINAL_FRAME_BYTES
            or len(group_frame) + len(terminal_frame)
            > EXPERT_EMERGENCY_RESERVE_BYTES
        ):
            raise ValueError("expert_emergency_frame_capacity_invalid")
        with self._publication_lock:
            if (
                self._published_state is not prior_state
                or self._published_cursor is not prior_cursor
            ):
                raise ValueError("expert_emergency_cas_failed")
            permit = issue_expert_emergency_append_permit(
                self._writer,
                expected_state_sha256=(
                    prior_cursor.expert_state_sha256
                ),
                expected_cursor=prior_cursor,
                evidence_terminal=built_evidence,
                group=group,
                payloads=payloads,
                terminal=terminal,
            )
            receipt = append_expert_emergency_group_and_terminal(permit)
            _validate_emergency_receipt(
                receipt,
                group,
                candidate_cursor,
                terminal,
                self._durable_end_offset + len(group_frame),
                self._durable_end_offset
                + len(group_frame)
                + len(terminal_frame),
            )
            self._published_state = candidate_state
            self._published_cursor = candidate_cursor
            self._terminal = terminal
            self._durable_end_offset = (
                receipt.terminal_receipt.durable_end_offset
            )
            self._closed = True
            return self._snapshot_unchecked()

    def _process_raw(
        self,
        parent: PersistedEvent,
        prior_state: ExpertStateV1,
        prior_cursor: ExpertJournalCursorV1,
        *,
        bridge_on_halt: bool,
        terminal_evidence: PersistedEvent | None = None,
        tail_already_proven: bool = False,
    ) -> _RESULT:
        (
            reduction,
            observations,
            group,
            payloads,
            candidate_cursor,
            candidate_state,
        ) = self._prepare_parent(parent, prior_state, prior_cursor)
        try:
            self._commit_ordinary(
                prior_state,
                prior_cursor,
                group,
                payloads,
                candidate_cursor,
                candidate_state,
            )
        except ExpertPrewriteCapacityError:
            return self._commit_emergency(
                parent,
                prior_state,
                prior_cursor,
                observations,
                known_evidence=terminal_evidence,
                tail_already_proven=tail_already_proven,
            )
        if reduction.halt_required:
            if bridge_on_halt:
                evidence = self._ingress.close_external_halt(
                    self._runtime
                )
                return self._terminalize(evidence)
        return self._snapshot_unchecked()

    def _append_terminal(
        self,
        known_evidence: PersistedEvent | None,
    ) -> _RESULT:
        state = self._published_state
        cursor = self._published_cursor
        evidence, terminal = build_aligned_expert_terminal(
            self._writer,
            final_state=state,
            final_cursor=cursor,
        )
        _validate_terminal_material(
            self._manifest,
            state,
            cursor,
            known_evidence,
            evidence,
            terminal,
        )
        terminal_frame = encode_expert_terminal_frame(
            terminal,
            final_cursor=cursor,
        )
        with self._publication_lock:
            if (
                self._published_state is not state
                or self._published_cursor is not cursor
            ):
                raise ValueError("expert_terminal_cas_failed")
            permit = issue_expert_terminal_permit(
                self._writer,
                terminal,
            )
            receipt = append_expert_terminal(permit)
            _validate_terminal_receipt(
                receipt,
                terminal,
                cursor,
                self._durable_end_offset + len(terminal_frame),
            )
            self._terminal = terminal
            self._durable_end_offset = receipt.durable_end_offset
            self._closed = True
            return self._snapshot_unchecked()

    def _terminalize(self, evidence: PersistedEvent | None) -> _RESULT:
        prior_state = self._published_state
        prior_cursor = self._published_cursor
        unseen = prove_expert_live_evidence_tail(
            self._writer,
            published_cursor=prior_cursor,
        )
        if unseen is not None:
            result = self._process_raw(
                unseen,
                prior_state,
                prior_cursor,
                bridge_on_halt=False,
                terminal_evidence=evidence,
                tail_already_proven=True,
            )
            if result[2] is not None:
                return result
        return self._append_terminal(evidence)

    def process_one(
        self,
        *,
        timeout_seconds: float,
    ) -> tuple[
        ExpertStateV1,
        ExpertJournalCursorV1,
        ExpertSessionTerminalV1 | None,
    ]:
        self._require_owner()
        if type(timeout_seconds) is not float or timeout_seconds <= 0.0:
            raise TypeError("timeout_seconds")
        if self._controller_lifecycle != _CONTROLLER_ACTIVE:
            raise RuntimeError("expert_controller_unavailable")
        try:
            item = self._ingress.drain_one_parent(
                self._runtime,
                timeout_seconds=timeout_seconds,
            )
        except BaseException:
            self._poison()
            raise
        if item is None:
            return self.snapshot()
        if type(item) is DurableIngressParentV1:
            result = self.process_durable_parent(item)
            authority = result[2]
            if type(authority) is DurableCompanionPublicationAckV1:
                _discard_legacy_companion_publication_ack_v1(
                    self,
                    authority,
                )
                return result[0], result[1], None
            if type(authority) is PendingDurableCompanionEmergencyV1:
                self.abort_pending_durable_companion_emergency_v1(
                    authority,
                    reason=(
                        PendingEmergencyAbortReasonV1
                        .LEGACY_PROCESS_ONE_CAPACITY_DENIAL
                    ),
                )
                raise ExpertCapacityExceeded() from None
            raise RuntimeError("expert_controller_unavailable")
        if type(item) is DurableEvidenceTerminalV1:
            return self.process_evidence_terminal(item)
        self._poison()
        raise TypeError("drain result")

    def snapshot(
        self,
    ) -> tuple[
        ExpertStateV1,
        ExpertJournalCursorV1,
        ExpertSessionTerminalV1 | None,
    ]:
        self._require_owner()
        with self._publication_lock:
            return self._snapshot_unchecked()

    def close(
        self,
    ) -> tuple[
        ExpertStateV1,
        ExpertJournalCursorV1,
        ExpertSessionTerminalV1 | None,
    ]:
        self._require_owner()
        lifecycle = self._controller_lifecycle
        if lifecycle in (
            _CONTROLLER_TERMINAL,
            _CONTROLLER_HALTED_UNCLEAN,
            _CONTROLLER_DURABILITY_UNCERTAIN_HALTED,
        ):
            return self.snapshot()
        if lifecycle == _CONTROLLER_EMERGENCY_PENDING:
            pending = self._active_pending
            if type(pending) is not PendingDurableCompanionEmergencyV1:
                self._poison()
                return self.snapshot()
            self.abort_pending_durable_companion_emergency_v1(
                pending,
                reason=(
                    PendingEmergencyAbortReasonV1.CALLER_CLOSE_WITH_PENDING
                ),
            )
            return self.snapshot()
        if lifecycle != _CONTROLLER_ACTIVE:
            raise RuntimeError("expert_controller_unavailable")
        try:
            self._ingress.close_inputs()
            while self._controller_lifecycle == _CONTROLLER_ACTIVE:
                result = self.process_one(timeout_seconds=1.0)
                if result[2] is not None:
                    return result
            return self.snapshot()
        except BaseException:
            if self._controller_lifecycle == _CONTROLLER_ACTIVE:
                self._poison()
            raise


abort_pending_durable_companion_emergency_v1 = (
    ExpertControllerV1.abort_pending_durable_companion_emergency_v1
)


def create_expert_controller(
    *,
    authority: ExpertJournalRootAuthorityV1,
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
    ingress: BoundedIngress,
    runtime: EventRuntime,
    persistence_authorizer: ProviderPersistenceAuthorizer,
    coordinator: RetentionCoordinator,
) -> ExpertControllerV1:
    exact = (
        (authority, ExpertJournalRootAuthorityV1),
        (manifest, ExpertSessionManifestV1),
        (universe, BindingUniverse),
        (policy, SyncPolicy),
        (ingress, BoundedIngress),
        (runtime, EventRuntime),
        (persistence_authorizer, ProviderPersistenceAuthorizer),
        (coordinator, RetentionCoordinator),
    )
    if any(type(value) is not expected for value, expected in exact):
        raise TypeError("expert controller requires exact arguments")
    runtime.require_owner()
    environment_authority = (
        issue_expert_environment_collection_authority(
            authority,
            persistence_authorizer=persistence_authorizer,
            coordinator=coordinator,
        )
    )
    collected = collect_expert_current_environment(
        environment_authority
    )
    if (
        manifest.environment != collected.current
        or manifest.normalizers != collected.normalizers
        or manifest.structural_schemas != collected.structural_schemas
        or manifest.event_schemas != collected.event_schemas
    ):
        raise ValueError("expert_controller_environment_mismatch")
    state = _validate_factory_bindings(
        manifest,
        universe,
        policy,
        runtime,
        persistence_authorizer,
        coordinator,
    )
    cursor = _genesis_cursor(manifest, state)
    writer = create_expert_journal(
        authority,
        manifest,
        cursor,
        persistence_authorizer=persistence_authorizer,
        coordinator=coordinator,
    )
    if type(writer) is not ExpertJournalWriteCapabilityV1:
        raise TypeError("exact expert writer required")
    controller: ExpertControllerV1 | None = None
    identity: ExpertControllerIdentityV1 | None = None
    controller_authority: object | None = None
    try:
        owner_pid = getpid()
        owner_thread = current_thread()
        identity = _issue_controller_identity_v1(manifest, owner_pid)
        controller = object.__new__(ExpertControllerV1)
        controller._manifest = manifest
        controller._universe = universe
        controller._policy = policy
        controller._ingress = ingress
        controller._runtime = runtime
        controller._persistence_authorizer = persistence_authorizer
        controller._coordinator = coordinator
        controller._published_state = state
        controller._published_cursor = cursor
        controller._owner_pid = owner_pid
        controller._owner_thread = owner_thread
        controller._publication_lock = RLock()
        controller._terminal = None
        controller._poisoned = False
        controller._closed = False
        controller._aborted = False
        controller._durable_end_offset = (
            EXPERT_FILE_HEADER_BYTES
            + len(encode_expert_manifest_frame(manifest))
        )
        controller._writer = writer
        controller._publication_epoch = 0
        controller._controller_identity = identity
        controller._controller_lifecycle = _CONTROLLER_ACTIVE
        controller._lifecycle = _CONTROLLER_ACTIVE
        controller._active_pending = None
        controller._ordinary_terminal_handoff_key = None
        controller._ordinary_reconciliation = None
        controller._emergency_reconciliation_terminal = None
        controller_authority = _ControllerValueIssuanceSnapshotV1(
            value_type="ExpertControllerV1",
            public_fields=(
                identity.controller_identity_sha256,
                manifest.session_id,
                owner_pid,
            ),
            digest=identity.controller_identity_sha256,
        )
        _CONTROLLER_AUTHORITIES_V1.register(
            controller,
            controller_authority,
        )
        from tennis_v1.ingress import _bind_durable_ingress_consumer_v1

        _bind_durable_ingress_consumer_v1(ingress, controller)
        return controller
    except BaseException:
        if controller is not None and controller_authority is not None:
            _CONTROLLER_AUTHORITIES_V1.unregister(
                controller,
                controller_authority,
            )
        if identity is not None:
            identity_authority = (
                _CONTROLLER_IDENTITY_AUTHORITIES_V1.lookup(identity)
            )
            if identity_authority is not None:
                _CONTROLLER_IDENTITY_AUTHORITIES_V1.unregister(
                    identity,
                    identity_authority,
                )
        try:
            abort_expert_writer(writer)
        except BaseException:
            pass
        raise
