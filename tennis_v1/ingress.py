"""Bounded multi-producer ingress for the single-owner Tennis runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from os import getpid
import queue
import threading
import time
import weakref

from .canonical import canonical_json_bytes
from .codec import canonical_record_sha256
from .events import (
    CapturedInput,
    PersistedEvent,
    _exact_nonnegative_integer,
    _safe_identifier,
    _sha256,
)
from .sequencer import EventRuntime, WrongOwnerThread


_BACKPRESSURE = "backpressure"
_OWNER_UNRESPONSIVE = "owner_unresponsive"
_QUEUED = "QUEUED"
_OWNER_CLAIMED = "OWNER_CLAIMED"
_INGESTING = "INGESTING"
_RAW_RETURNED = "RAW_RETURNED"
_PROVISIONAL_QUEUED = _QUEUED
_PROVISIONAL = _QUEUED
_ACTIVE = _INGESTING
_RECEIPT_PUBLISHED = "RECEIPT_PUBLISHED"
_DURABLE_RECEIPTED = _RECEIPT_PUBLISHED
_PRODUCER_ACKNOWLEDGED = "PRODUCER_ACKNOWLEDGED"
_PARENT_ISSUED = "PARENT_ISSUED"
_PARENT_CONSUMED = "PARENT_CONSUMED"
_TIMED_OUT_NO_RAW = "TIMED_OUT_NO_RAW"
_TIMED_OUT = _TIMED_OUT_NO_RAW
_INGEST_FAILED_NO_RETURN = "INGEST_FAILED_NO_RETURN"
_FAILED = _INGEST_FAILED_NO_RETURN
_DURABLE_UNACKNOWLEDGED = "DURABLE_UNACKNOWLEDGED"
_ABORTED = "ABORTED"
_OWNER_TIMEOUT_CAUSE = "OWNER_TIMEOUT"
_ENVELOPE_ISSUED = "ISSUED"
_ENVELOPE_CONSUMED = "CONSUMED"
_COORDINATE_TERMINAL_STAGE = "EVIDENCE_TERMINAL_ISSUED"
_COORDINATE_SOURCE_CLOSE_STAGE = "SOURCE_CLOSE_COMPLETE"
_SIGNED_63_MAX = 9_223_372_036_854_775_807

_INGRESS_ALLOCATION_GUARD = threading.Lock()
_INGRESS_ALLOCATION_COORDINATE = 0


def _next_ingress_allocation_coordinate() -> int:
    global _INGRESS_ALLOCATION_COORDINATE
    with _INGRESS_ALLOCATION_GUARD:
        if _INGRESS_ALLOCATION_COORDINATE >= _SIGNED_63_MAX:
            raise RuntimeError("ingress_allocation_exhausted")
        _INGRESS_ALLOCATION_COORDINATE += 1
        return _INGRESS_ALLOCATION_COORDINATE


def _domain_sha256(domain: str, projection: object) -> str:
    return hashlib.sha256(
        domain.encode("ascii")
        + b"\x00"
        + canonical_json_bytes(projection)
    ).hexdigest()


def _captured_projection(value: CapturedInput) -> dict[str, object]:
    return {
        "session_id": value.session_id,
        "event_type": value.event_type,
        "event_version": value.event_version,
        "source_kind": value.source_kind.value,
        "source_id": value.source_id,
        "source_entity_id": value.source_entity_id,
        "endpoint_id": value.endpoint_id,
        "endpoint_state": value.endpoint_state.value,
        "channel_id": value.channel_id,
        "channel_state": value.channel_state.value,
        "request_id": value.request_id,
        "request_id_state": value.request_id_state.value,
        "source_wall_ns": value.source_wall_ns,
        "source_generated_ns": value.source_generated_ns,
        "local_wall_ns": value.local_wall_ns,
        "local_monotonic_ns": value.local_monotonic_ns,
        "clock_uncertainty_ns": value.clock_uncertainty_ns,
        "connection_epoch": value.connection_epoch,
        "provider_sequence": value.provider_sequence,
        "content_type": value.content_type,
        "payload_encoding": value.payload_encoding,
        "payload_transform": value.payload_transform,
        "retention_delete_by_ns": value.retention_delete_by_ns,
        "payload_sha256": hashlib.sha256(value.payload).hexdigest(),
    }


def _ingress_item_sha256_v1(value: IngressItem) -> str:
    return _domain_sha256(
        "INCI-INGRESS-ITEM-V1",
        {
            "producer_id": value.producer_id,
            "producer_sequence": value.producer_sequence,
            "captured": _captured_projection(value.captured),
        },
    )


def _durable_ingress_receipt_sha256_v1(
    value: DurableIngressReceipt,
) -> str:
    return _domain_sha256(
        "INCI-DURABLE-INGRESS-RECEIPT-V1",
        {
            "producer_id": value.producer_id,
            "producer_sequence": value.producer_sequence,
            "raw_ingest_seq": value.raw_ingest_seq,
            "raw_record_sha256": value.raw_record_sha256,
        },
    )


class IngressClosed(RuntimeError):
    """Ingress admission or its bound runtime is permanently closed."""


class IngressBackpressureHalt(RuntimeError):
    """The bounded queue could not admit an item before its deadline."""


class IngressOwnerUnresponsive(RuntimeError):
    """An admitted item did not receive a durable result in time."""


def _positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}: positive_integer_required")
    if value < 1:
        raise ValueError(f"{field_name}: positive_integer_required")
    return value


def _positive_timeout(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name}: positive_finite_float_required")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{field_name}: positive_finite_float_required")
    return value


@dataclass(frozen=True, slots=True)
class IngressItem:
    producer_id: str
    producer_sequence: int
    captured: CapturedInput

    def __post_init__(self) -> None:
        _safe_identifier(self.producer_id, "producer_id")
        _exact_nonnegative_integer(
            self.producer_sequence,
            "producer_sequence",
        )
        if type(self.captured) is not CapturedInput:
            raise TypeError("captured: exact_CapturedInput_required")


def _validate_exact_ingress_item(item: IngressItem) -> None:
    try:
        producer_id = item.producer_id
        producer_sequence = item.producer_sequence
        captured = item.captured
    except AttributeError:
        raise TypeError("ingress_item_fields_required") from None
    _safe_identifier(producer_id, "producer_id")
    _exact_nonnegative_integer(
        producer_sequence,
        "producer_sequence",
    )
    if type(captured) is not CapturedInput:
        raise TypeError("captured: exact_CapturedInput_required")


@dataclass(frozen=True, slots=True)
class DurableIngressReceipt:
    producer_id: str
    producer_sequence: int
    raw_ingest_seq: int
    raw_record_sha256: str

    def __post_init__(self) -> None:
        _safe_identifier(self.producer_id, "producer_id")
        _exact_nonnegative_integer(
            self.producer_sequence,
            "producer_sequence",
        )
        _positive_integer(self.raw_ingest_seq, "raw_ingest_seq")
        _sha256(self.raw_record_sha256, "raw_record_sha256")


class DurableIngressParentV1:
    """Opaque producer-acknowledged durable RAW parent envelope."""

    __slots__ = (
        "__weakref__",
        "schema_version",
        "item",
        "parent",
        "receipt",
        "envelope_sha256",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("DurableIngressParentV1 is issued by BoundedIngress")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("DurableIngressParentV1 cannot be subclassed")

    def __setattr__(self, _: str, __: object) -> None:
        raise AttributeError("DurableIngressParentV1 is immutable")

    def __repr__(self) -> str:
        return "<DurableIngressParentV1 redacted>"

    def __copy__(self):
        raise TypeError("DurableIngressParentV1 cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("DurableIngressParentV1 cannot be copied")

    def __reduce__(self):
        raise TypeError("DurableIngressParentV1 cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("DurableIngressParentV1 cannot be pickled")


class DurableEvidenceTerminalV1:
    """Opaque ingress-issued evidence terminal envelope."""

    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "ingress_identity_sha256",
        "terminal",
        "terminal_ingest_seq",
        "terminal_record_sha256",
        "evidence_terminal_coordinate_sha256",
        "terminal_reason",
        "envelope_sha256",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("DurableEvidenceTerminalV1 is issued by BoundedIngress")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("DurableEvidenceTerminalV1 cannot be subclassed")

    def __setattr__(self, _: str, __: object) -> None:
        raise AttributeError("DurableEvidenceTerminalV1 is immutable")

    def __repr__(self) -> str:
        return "<DurableEvidenceTerminalV1 redacted>"

    def __copy__(self):
        raise TypeError("DurableEvidenceTerminalV1 cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("DurableEvidenceTerminalV1 cannot be copied")

    def __reduce__(self):
        raise TypeError("DurableEvidenceTerminalV1 cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("DurableEvidenceTerminalV1 cannot be pickled")


class DurableCausalOrderCoordinateV1:
    """Opaque coordinate in one ingress/runtime causal order."""

    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "ingress_identity_sha256",
        "runtime_identity_sha256",
        "stage",
        "ordinal",
        "coordinate_sha256",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(
            "DurableCausalOrderCoordinateV1 is issued by BoundedIngress"
        )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("DurableCausalOrderCoordinateV1 cannot be subclassed")

    def __setattr__(self, _: str, __: object) -> None:
        raise AttributeError("DurableCausalOrderCoordinateV1 is immutable")

    def __repr__(self) -> str:
        return "<DurableCausalOrderCoordinateV1 redacted>"

    def __copy__(self):
        raise TypeError("DurableCausalOrderCoordinateV1 cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("DurableCausalOrderCoordinateV1 cannot be copied")

    def __reduce__(self):
        raise TypeError("DurableCausalOrderCoordinateV1 cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("DurableCausalOrderCoordinateV1 cannot be pickled")


class DurableCausalPrecedesProofV1:
    """Opaque proof binding one source-close coordinate before one terminal."""

    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "before_coordinate_sha256",
        "after_coordinate_sha256",
        "proof_sha256",
        "_proof_authority",
        "_prepared_commit",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("DurableCausalPrecedesProofV1 is privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("DurableCausalPrecedesProofV1 cannot be subclassed")

    def __getattribute__(self, name: str) -> object:
        if name in ("_proof_authority", "_prepared_commit"):
            raise AttributeError(
                "DurableCausalPrecedesProofV1 internal state is opaque"
            )
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in ("_proof_authority", "_prepared_commit"):
            object.__setattr__(self, name, value)
            return
        raise AttributeError("DurableCausalPrecedesProofV1 is immutable")

    def __repr__(self) -> str:
        return "<DurableCausalPrecedesProofV1 redacted>"

    def __copy__(self):
        raise TypeError("DurableCausalPrecedesProofV1 cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("DurableCausalPrecedesProofV1 cannot be copied")

    def __reduce__(self):
        raise TypeError("DurableCausalPrecedesProofV1 cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("DurableCausalPrecedesProofV1 cannot be pickled")


class DeferredEmergencyCommitSubjectV1:
    """Opaque exact-object join subject for the unreachable future commit."""

    __slots__ = (
        "__weakref__",
        "schema_version",
        "session_id",
        "controller_identity_sha256",
        "pending_sha256",
        "durable_parent_envelope_sha256",
        "evidence_terminal_sha256",
        "evidence_terminal_coordinate_sha256",
        "subject_sha256",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("DeferredEmergencyCommitSubjectV1 is privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("DeferredEmergencyCommitSubjectV1 cannot be subclassed")

    def __setattr__(self, _: str, __: object) -> None:
        raise AttributeError("DeferredEmergencyCommitSubjectV1 is immutable")

    def __repr__(self) -> str:
        return "<DeferredEmergencyCommitSubjectV1 redacted>"

    def __copy__(self):
        raise TypeError("DeferredEmergencyCommitSubjectV1 cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("DeferredEmergencyCommitSubjectV1 cannot be copied")

    def __reduce__(self):
        raise TypeError("DeferredEmergencyCommitSubjectV1 cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("DeferredEmergencyCommitSubjectV1 cannot be pickled")


class _PreparedDeferredEmergencyCausalProofCommitV1:
    """Private mutable capability for one deferred causal-proof commit."""

    __slots__ = (
        "__weakref__",
        "ingress",
        "controller",
        "pending_authority",
        "completion_scope",
        "proof_authority",
        "subject_authority",
        "terminal_authority",
        "publication_lock",
        "ingress_lock",
        "owner_pid",
        "owner_thread",
        "target_proof_lifecycle",
        "lifecycle",
        "success_armed",
    )

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(
            "_PreparedDeferredEmergencyCausalProofCommitV1 is privately issued"
        )

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError(
            "_PreparedDeferredEmergencyCausalProofCommitV1 cannot be subclassed"
        )

    def __repr__(self) -> str:
        return "<_PreparedDeferredEmergencyCausalProofCommitV1 redacted>"

    def __copy__(self):
        raise TypeError(
            "_PreparedDeferredEmergencyCausalProofCommitV1 cannot be copied"
        )

    def __deepcopy__(self, _: object):
        raise TypeError(
            "_PreparedDeferredEmergencyCausalProofCommitV1 cannot be copied"
        )

    def __reduce__(self):
        raise TypeError(
            "_PreparedDeferredEmergencyCausalProofCommitV1 cannot be pickled"
        )

    def __reduce_ex__(self, _: int):
        raise TypeError(
            "_PreparedDeferredEmergencyCausalProofCommitV1 cannot be pickled"
        )


@dataclass(frozen=True, slots=True)
class _ParentIssuanceSnapshotV1:
    schema_version: int
    session_id: str
    producer_id: str
    producer_sequence: int
    item_sha256: str
    raw_ingest_seq: int
    parent_record_sha256: str
    receipt_producer_id: str
    receipt_producer_sequence: int
    receipt_raw_ingest_seq: int
    receipt_raw_record_sha256: str
    receipt_sha256: str
    envelope_sha256: str


@dataclass(frozen=True, slots=True)
class _TerminalIssuanceSnapshotV1:
    schema_version: int
    session_id: str
    ingress_identity_sha256: str
    terminal_ingest_seq: int
    terminal_record_sha256: str
    coordinate_sha256: str
    terminal_reason: str
    envelope_sha256: str


@dataclass(frozen=True, slots=True)
class _CoordinateIssuanceSnapshotV1:
    schema_version: int
    session_id: str
    ingress_identity_sha256: str
    runtime_identity_sha256: str
    stage: str
    ordinal: int
    subject_sha256: str
    coordinate_sha256: str


@dataclass(frozen=True, slots=True)
class _SubjectIssuanceSnapshotV1:
    schema_version: int
    session_id: str
    controller_identity_sha256: str
    pending_sha256: str
    durable_parent_envelope_sha256: str
    evidence_terminal_sha256: str
    evidence_terminal_coordinate_sha256: str
    subject_sha256: str
    ingress: BoundedIngress
    runtime: EventRuntime
    ingress_identity_sha256: str
    runtime_identity_sha256: str
    controller: object
    pending: object
    parent: DurableIngressParentV1
    terminal: DurableEvidenceTerminalV1
    coordinate: DurableCausalOrderCoordinateV1
    publication_lock: object
    completion_pending_authority: object
    publication_epoch: int
    owner_pid: int
    owner_thread: threading.Thread


@dataclass(frozen=True, slots=True)
class _ProofIssuanceSnapshotV1:
    schema_version: int
    session_id: str
    before_coordinate_sha256: str
    after_coordinate_sha256: str
    proof_sha256: str
    claim: object
    subject: DeferredEmergencyCommitSubjectV1
    pending: object
    terminal: DurableEvidenceTerminalV1
    before: DurableCausalOrderCoordinateV1
    after: DurableCausalOrderCoordinateV1
    owner_pid: int
    owner_thread: threading.Thread


@dataclass(slots=True)
class _EnvelopeAuthorityV1:
    ingress: BoundedIngress
    runtime: EventRuntime
    session_id: str
    owner_pid: int
    owner_thread: threading.Thread
    consumer: object | None
    kind: str
    item: IngressItem | None
    parent: PersistedEvent | None
    receipt: DurableIngressReceipt | None
    terminal: PersistedEvent | None
    terminal_reason: str | None
    coordinate: DurableCausalOrderCoordinateV1 | None
    node: _IngressNode | None
    issuance: _ParentIssuanceSnapshotV1 | _TerminalIssuanceSnapshotV1
    live_envelope: DurableIngressParentV1 | DurableEvidenceTerminalV1 | None
    lifecycle: str = _ENVELOPE_ISSUED


@dataclass(slots=True)
class _CoordinateAuthorityV1:
    ingress: BoundedIngress
    runtime: EventRuntime
    owner_pid: int
    owner_thread: threading.Thread
    stage: str
    subject: object
    subject_sha256: str
    issuance: _CoordinateIssuanceSnapshotV1


@dataclass(slots=True)
class _SubjectAuthorityV1:
    ingress: BoundedIngress
    runtime: EventRuntime
    controller: object
    pending: object
    terminal: DurableEvidenceTerminalV1
    parent: DurableIngressParentV1
    coordinate: DurableCausalOrderCoordinateV1
    session_id: str
    controller_identity_sha256: str
    pending_sha256: str
    owner_pid: int
    owner_thread: threading.Thread
    publication_epoch: int
    publication_lock: object
    completion_scope: object
    issuance: _SubjectIssuanceSnapshotV1
    lifecycle: str = "FRESH"


@dataclass(slots=True)
class _ProofAuthorityV1:
    before: DurableCausalOrderCoordinateV1
    after: DurableCausalOrderCoordinateV1
    subject: DeferredEmergencyCommitSubjectV1
    claim: object
    pending: object
    terminal: DurableEvidenceTerminalV1
    owner_pid: int
    owner_thread: threading.Thread
    issuance: _ProofIssuanceSnapshotV1
    proof_reference: weakref.ReferenceType[
        DurableCausalPrecedesProofV1
    ] | None = None
    prepared_commit: weakref.ReferenceType[
        _PreparedDeferredEmergencyCausalProofCommitV1
    ] | None = None
    lifecycle: str = "PREPARED"


@dataclass(frozen=True, slots=True)
class _DeferredCommitKernelInputV1:
    claim_lifecycle: str
    subject_lifecycle: str
    pending_lifecycle: str
    proof_lifecycle: str
    completion_scope_lifecycle: str
    same_session: bool
    same_ingress: bool
    same_runtime: bool
    same_controller: bool
    same_pending_parent: bool
    same_publication_epoch: bool
    same_owner: bool
    before_stage: str
    after_stage: str
    before_ordinal: int
    after_ordinal: int


@dataclass(frozen=True, slots=True)
class _DeferredCommitKernelResultV1:
    claim_lifecycle: str
    subject_lifecycle: str
    pending_lifecycle: str
    proof_lifecycle: str
    completion_scope_lifecycle: str


def _deferred_commit_transition_kernel_v1(
    value: _DeferredCommitKernelInputV1,
) -> _DeferredCommitKernelResultV1:
    """Pure transition shared by production records and the sealed oracle."""
    if type(value) is not _DeferredCommitKernelInputV1:
        raise TypeError("exact deferred commit kernel input required")
    if (
        value.claim_lifecycle != "CLAIMED"
        or value.subject_lifecycle != "FRESH"
        or value.pending_lifecycle != "FRESH"
        or value.proof_lifecycle != "PREPARED"
        or value.completion_scope_lifecycle != "ACTIVE"
        or value.same_session is not True
        or value.same_ingress is not True
        or value.same_runtime is not True
        or value.same_controller is not True
        or value.same_pending_parent is not True
        or value.same_publication_epoch is not True
        or value.same_owner is not True
    ):
        raise ValueError("durable_causal_subject_mismatch")
    if (
        value.before_stage != _COORDINATE_SOURCE_CLOSE_STAGE
        or value.after_stage != _COORDINATE_TERMINAL_STAGE
        or type(value.before_ordinal) is not int
        or type(value.after_ordinal) is not int
        or value.before_ordinal <= 0
        or value.before_ordinal >= value.after_ordinal
        or value.after_ordinal > _SIGNED_63_MAX
    ):
        raise ValueError("durable_causal_order_invalid")
    return _DeferredCommitKernelResultV1(
        claim_lifecycle="CONSUMED",
        subject_lifecycle="CONSUMED",
        pending_lifecycle="COMMIT_RESERVED",
        proof_lifecycle="ISSUED",
        completion_scope_lifecycle="RESERVATION_COMMITTED",
    )


@dataclass(frozen=True, slots=True)
class _DeferredProofFinalizationKernelInputV1:
    proof_lifecycle: str
    exact_bindings: bool
    public_fields_valid: bool
    same_owner: bool
    subject_lifecycle: str
    pending_lifecycle: str
    completion_scope_lifecycle: str
    reservation_committed: bool
    reserved_slots_exact: bool
    terminal_lifecycle: str
    append_succeeded: bool


def _deferred_proof_finalization_kernel_v1(
    value: _DeferredProofFinalizationKernelInputV1,
) -> str:
    """Pure one-way proof transition shared with the sealed oracle."""
    if type(value) is not _DeferredProofFinalizationKernelInputV1:
        raise TypeError("exact deferred proof finalization kernel input required")
    if (
        value.exact_bindings is not True
        or value.public_fields_valid is not True
        or value.same_owner is not True
        or value.reserved_slots_exact is not True
    ):
        raise ValueError("durable_causal_subject_mismatch")
    if value.proof_lifecycle != "ISSUED":
        raise ValueError("durable_causal_proof_consumed")
    if (
        value.subject_lifecycle != "CONSUMED"
        or value.pending_lifecycle != "COMMIT_RESERVED"
        or value.completion_scope_lifecycle != "RESERVATION_COMMITTED"
        or value.reservation_committed is not True
        or type(value.append_succeeded) is not bool
        or (
            value.append_succeeded
            and value.terminal_lifecycle != _ENVELOPE_CONSUMED
        )
        or (
            not value.append_succeeded
            and value.terminal_lifecycle
            not in (_ENVELOPE_ISSUED, _ENVELOPE_CONSUMED)
        )
    ):
        raise ValueError("durable_causal_subject_mismatch")
    return (
        "CONSUMED_BY_FUTURE_COMPLETION"
        if value.append_succeeded
        else "APPEND_FAILED_CLOSED"
    )


def _make_identity_registry():
    entries: dict[
        int,
        tuple[weakref.ReferenceType[object], object],
    ] = {}
    guard = threading.RLock()

    def register(item: object, authority: object) -> None:
        key = id(item)

        def discard(reference: weakref.ReferenceType[object]) -> None:
            with guard:
                current = entries.get(key)
                if current is not None and current[0] is reference:
                    entries.pop(key, None)

        reference = weakref.ref(item, discard)
        with guard:
            current = entries.get(key)
            if current is not None and current[0]() is not None:
                raise RuntimeError("durable ingress identity collision")
            entries[key] = (reference, authority)

    def lookup(item: object) -> object | None:
        with guard:
            current = entries.get(id(item))
            if current is None or current[0]() is not item:
                return None
            return current[1]

    def unregister(item: object, authority: object) -> bool:
        with guard:
            current = entries.get(id(item))
            if (
                current is None
                or current[0]() is not item
                or current[1] is not authority
            ):
                return False
            entries.pop(id(item), None)
            return True

    return register, lookup, unregister


(
    _register_envelope_authority,
    _lookup_envelope_authority,
    _unregister_envelope_authority,
) = (
    _make_identity_registry()
)
(
    _register_coordinate_authority,
    _lookup_coordinate_authority,
    _unregister_coordinate_authority,
) = (
    _make_identity_registry()
)
(
    _register_subject_authority,
    _lookup_subject_authority,
    _unregister_subject_authority,
) = (
    _make_identity_registry()
)
(
    _register_proof_authority_registry_v1,
    _lookup_proof_authority_registry_v1,
    _unregister_proof_authority_registry_v1,
) = _make_identity_registry()


def _register_proof_authority(
    proof: DurableCausalPrecedesProofV1,
    authority: _ProofAuthorityV1,
) -> None:
    if (
        type(proof) is not DurableCausalPrecedesProofV1
        or type(authority) is not _ProofAuthorityV1
        or authority.proof_reference is not None
    ):
        raise ValueError("durable_causal_subject_mismatch")
    _register_proof_authority_registry_v1(proof, authority)
    try:
        authority.proof_reference = weakref.ref(proof)
    except BaseException:
        _unregister_proof_authority_registry_v1(proof, authority)
        raise


def _lookup_proof_authority(
    proof: DurableCausalPrecedesProofV1,
) -> _ProofAuthorityV1 | None:
    authority = _lookup_proof_authority_registry_v1(proof)
    if (
        type(authority) is _ProofAuthorityV1
        and authority.proof_reference is not None
        and authority.proof_reference() is proof
    ):
        return authority
    if type(proof) is not DurableCausalPrecedesProofV1:
        return None
    try:
        authority = object.__getattribute__(proof, "_proof_authority")
    except AttributeError:
        return None
    return (
        authority
        if (
            type(authority) is _ProofAuthorityV1
            and authority.proof_reference is not None
            and authority.proof_reference() is proof
        )
        else None
    )


def _unregister_proof_authority(
    proof: DurableCausalPrecedesProofV1,
    authority: _ProofAuthorityV1,
) -> bool:
    removed = _unregister_proof_authority_registry_v1(proof, authority)
    if (
        removed
        and authority.proof_reference is not None
        and authority.proof_reference() is proof
    ):
        authority.proof_reference = None
    return removed


@dataclass(slots=True)
class _PreparedDeferredCommitRegistryCellV1:
    prepared_reference: weakref.ReferenceType[
        _PreparedDeferredEmergencyCausalProofCommitV1
    ]
    proof_reference: weakref.ReferenceType[DurableCausalPrecedesProofV1]
    ingress_identity: int
    controller_identity: int
    pending_authority_identity: int
    completion_scope_identity: int
    proof_authority_identity: int
    subject_authority_identity: int
    terminal_authority_identity: int
    publication_lock_identity: int
    ingress_lock_identity: int
    owner_pid: int
    owner_thread_identity: int
    target_proof_lifecycle: str
    lifecycle: str = "PROVISIONAL"
    success_armed: bool = False


_PREPARED_DEFERRED_COMMIT_ENTRIES_V1: dict[
    int,
    _PreparedDeferredCommitRegistryCellV1,
] = {}


def _register_prepared_deferred_emergency_causal_proof_commit_v1(
    proof: DurableCausalPrecedesProofV1,
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
) -> None:
    if type(proof) is not DurableCausalPrecedesProofV1:
        raise TypeError("exact DurableCausalPrecedesProofV1 required")
    if type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1:
        raise TypeError(
            "exact _PreparedDeferredEmergencyCausalProofCommitV1 required"
        )
    key = id(prepared)

    def discard(
        reference: weakref.ReferenceType[
            _PreparedDeferredEmergencyCausalProofCommitV1
        ],
    ) -> None:
        current = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(key)
        if current is not None and current.prepared_reference is reference:
            _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.pop(key, None)

    prepared_reference = weakref.ref(prepared, discard)
    proof_reference = weakref.ref(proof)
    current = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(key)
    if current is not None and current.prepared_reference() is not None:
        raise RuntimeError("durable ingress identity collision")
    proof_authority = _lookup_proof_authority(proof)
    if (
        type(proof_authority) is not _ProofAuthorityV1
        or proof_authority.prepared_commit is not None
    ):
        raise ValueError("durable_causal_proof_commit_invalid")
    try:
        _PREPARED_DEFERRED_COMMIT_ENTRIES_V1[key] = (
            _PreparedDeferredCommitRegistryCellV1(
                prepared_reference=prepared_reference,
                proof_reference=proof_reference,
                ingress_identity=id(prepared.ingress),
                controller_identity=id(prepared.controller),
                pending_authority_identity=id(prepared.pending_authority),
                completion_scope_identity=id(prepared.completion_scope),
                proof_authority_identity=id(prepared.proof_authority),
                subject_authority_identity=id(prepared.subject_authority),
                terminal_authority_identity=id(prepared.terminal_authority),
                publication_lock_identity=id(prepared.publication_lock),
                ingress_lock_identity=id(prepared.ingress_lock),
                owner_pid=prepared.owner_pid,
                owner_thread_identity=id(prepared.owner_thread),
                target_proof_lifecycle=prepared.target_proof_lifecycle,
            )
        )
        proof_authority.prepared_commit = prepared_reference
    except BaseException:
        _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.pop(key, None)
        proof_authority.prepared_commit = None
        raise


def _prepared_deferred_commit_registry_bindings_valid_v1(
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
    cell: _PreparedDeferredCommitRegistryCellV1,
) -> bool:
    try:
        return (
            type(cell) is _PreparedDeferredCommitRegistryCellV1
            and cell.prepared_reference() is prepared
            and id(prepared.ingress) == cell.ingress_identity
            and id(prepared.controller) == cell.controller_identity
            and id(prepared.pending_authority)
            == cell.pending_authority_identity
            and id(prepared.completion_scope)
            == cell.completion_scope_identity
            and id(prepared.proof_authority) == cell.proof_authority_identity
            and id(prepared.subject_authority)
            == cell.subject_authority_identity
            and id(prepared.terminal_authority)
            == cell.terminal_authority_identity
            and id(prepared.publication_lock)
            == cell.publication_lock_identity
            and id(prepared.ingress_lock) == cell.ingress_lock_identity
            and prepared.owner_pid == cell.owner_pid
            and id(prepared.owner_thread) == cell.owner_thread_identity
            and prepared.target_proof_lifecycle
            == cell.target_proof_lifecycle
            and (
                (cell.lifecycle == "PROVISIONAL" and prepared.lifecycle == "PROVISIONAL")
                or (cell.lifecycle == "LIVE" and prepared.lifecycle == "PREPARED")
                or (cell.lifecycle == "COMMITTED" and prepared.lifecycle == "COMMITTED")
                or (
                    cell.lifecycle == "FAILED_CLOSED"
                    and prepared.lifecycle == "FAILED_CLOSED"
                )
            )
            and prepared.success_armed is cell.success_armed
        )
    except AttributeError:
        return False


def _lookup_prepared_deferred_emergency_causal_proof_commit_v1(
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
) -> _PreparedDeferredEmergencyCausalProofCommitV1 | None:
    current = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(id(prepared))
    if (
        current is None
        or current.prepared_reference() is not prepared
        or current.lifecycle not in ("PROVISIONAL", "LIVE")
    ):
        return None
    proof = current.proof_reference()
    proof_authority = (
        None if proof is None else _lookup_proof_authority(proof)
    )
    if (
        type(proof_authority) is not _ProofAuthorityV1
        or proof_authority.prepared_commit is None
        or proof_authority.prepared_commit() is not prepared
    ):
        return None
    return prepared


def _lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1(
    proof: DurableCausalPrecedesProofV1,
) -> _PreparedDeferredEmergencyCausalProofCommitV1 | None:
    proof_authority = _lookup_proof_authority(proof)
    prepared_reference = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else proof_authority.prepared_commit
    )
    prepared = (
        None
        if prepared_reference is None
        else prepared_reference()
    )
    if type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1:
        return None
    return _lookup_prepared_deferred_emergency_causal_proof_commit_v1(prepared)


def _unregister_prepared_deferred_emergency_causal_proof_commit_v1(
    proof: DurableCausalPrecedesProofV1,
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
) -> bool:
    current = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(id(prepared))
    if (
        current is None
        or current.prepared_reference() is not prepared
        or current.proof_reference() is not proof
        or current.lifecycle != "PROVISIONAL"
    ):
        return False
    proof_authority = _lookup_proof_authority(proof)
    if (
        type(proof_authority) is not _ProofAuthorityV1
        or proof_authority.prepared_commit is None
        or proof_authority.prepared_commit() is not prepared
    ):
        return False
    _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.pop(id(prepared), None)
    proof_authority.prepared_commit = None
    return True


def _build_prepared_deferred_emergency_causal_proof_commit_v1(
    *,
    ingress: BoundedIngress,
    controller: object,
    pending_authority: object,
    completion_scope: object,
    proof_authority: _ProofAuthorityV1,
    subject_authority: _SubjectAuthorityV1,
    terminal_authority: _EnvelopeAuthorityV1,
    publication_lock: object,
) -> _PreparedDeferredEmergencyCausalProofCommitV1:
    prepared = object.__new__(
        _PreparedDeferredEmergencyCausalProofCommitV1
    )
    prepared.ingress = ingress
    prepared.controller = controller
    prepared.pending_authority = pending_authority
    prepared.completion_scope = completion_scope
    prepared.proof_authority = proof_authority
    prepared.subject_authority = subject_authority
    prepared.terminal_authority = terminal_authority
    prepared.publication_lock = publication_lock
    prepared.ingress_lock = ingress._causal_subject_lock
    prepared.owner_pid = getpid()
    prepared.owner_thread = threading.current_thread()
    prepared.target_proof_lifecycle = "CONSUMED_BY_FUTURE_COMPLETION"
    prepared.lifecycle = "PROVISIONAL"
    prepared.success_armed = False
    return prepared


class _SealedDeferredCommitClaimCellV1:
    """Exact-distinct inert claim cell for predecessor kernel evidence."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred commit claim cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred commit claim cells cannot be subclassed")

    def __copy__(self):
        raise TypeError("sealed deferred commit claim cells cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("sealed deferred commit claim cells cannot be copied")

    def __reduce__(self):
        raise TypeError("sealed deferred commit claim cells cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("sealed deferred commit claim cells cannot be pickled")


class _SealedDeferredCommitSubjectCellV1:
    """Exact-distinct inert subject cell for predecessor kernel evidence."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred commit subject cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred commit subject cells cannot be subclassed")


class _SealedDeferredCommitPendingCellV1:
    """Exact-distinct inert pending cell for predecessor kernel evidence."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred commit pending cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred commit pending cells cannot be subclassed")


class _SealedDeferredCommitProofCellV1:
    """Exact-distinct inert proof cell for predecessor kernel evidence."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred commit proof cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred commit proof cells cannot be subclassed")


class _SealedDeferredCommitPreparedCellV1:
    """Exact-distinct inert prepared-commit cell."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed prepared commit cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed prepared commit cells cannot be subclassed")


class _SealedDeferredCommitScopeCellV1:
    """Exact-distinct inert completion-scope cell."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred commit scope cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred commit scope cells cannot be subclassed")


class _SealedDeferredCommitTerminalCellV1:
    """Exact-distinct inert terminal-subject cell."""

    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred commit terminal cells are privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred commit terminal cells cannot be subclassed")


class _SealedOrderedCommitLockV1:
    __slots__ = ("_lock", "_name", "_trace")

    def __init__(self, name: str, trace: list[str]) -> None:
        self._lock = threading.RLock()
        self._name = name
        self._trace = trace

    def __enter__(self):
        self._lock.acquire()
        self._trace.append(self._name)
        return self

    def __exit__(self, *_: object) -> None:
        self._lock.release()

    def _is_owned(self) -> bool:
        return self._lock._is_owned()


@dataclass(slots=True)
class _SealedDeferredCommitLaneV1:
    controller_publication_lock: _SealedOrderedCommitLockV1
    ingress_subject_lock: _SealedOrderedCommitLockV1
    claim_lock: _SealedOrderedCommitLockV1
    lock_trace: list[str]
    completion_scope: weakref.ReferenceType[object] | None = None
    terminal: weakref.ReferenceType[object] | None = None


@dataclass(slots=True)
class _SealedDeferredCommitCellAuthorityV1:
    lane: _SealedDeferredCommitLaneV1
    role: str
    lifecycle: str
    kernel_input: _DeferredCommitKernelInputV1
    reserved_claim: object | None = None
    reserved_subject: object | None = None
    reserved_terminal: object | None = None
    reserved_causal_proof: object | None = None
    reserved_completion_scope: object | None = None
    reservation_committed: bool = False
    causal_proof: object | None = None
    reserved_prepared_commit: object | None = None
    success_armed: bool = False


@dataclass(frozen=True, slots=True)
class _SealedDeferredCommitKernelFixtureV1:
    case_name: str
    claim: _SealedDeferredCommitClaimCellV1
    subject: _SealedDeferredCommitSubjectCellV1
    pending: _SealedDeferredCommitPendingCellV1
    completion_scope: _SealedDeferredCommitScopeCellV1
    terminal: _SealedDeferredCommitTerminalCellV1


@dataclass(frozen=True, slots=True)
class _SealedDeferredCommitKernelResultV1:
    claim: _SealedDeferredCommitClaimCellV1
    subject: _SealedDeferredCommitSubjectCellV1
    pending: _SealedDeferredCommitPendingCellV1
    proof: _SealedDeferredCommitProofCellV1
    prepared: _SealedDeferredCommitPreparedCellV1
    lock_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SealedDeferredCommitObservationV1:
    claim_lifecycle: str
    subject_lifecycle: str
    pending_lifecycle: str
    scope_lifecycle: str
    reserved_claim: object | None
    reserved_subject: object | None
    reserved_terminal: object | None
    reserved_causal_proof: object | None
    reserved_completion_scope: object | None
    scope_reservation_committed: bool
    scope_causal_proof: object | None
    reserved_prepared_commit: object | None


(
    _register_sealed_deferred_commit_cell_authority_v1,
    _lookup_sealed_deferred_commit_cell_authority_v1,
    _unregister_sealed_deferred_commit_cell_authority_v1,
) = _make_identity_registry()


def _allocate_sealed_deferred_commit_proof_cell_v1(
) -> _SealedDeferredCommitProofCellV1:
    return object.__new__(_SealedDeferredCommitProofCellV1)


def _allocate_sealed_deferred_commit_prepared_cell_v1(
) -> _SealedDeferredCommitPreparedCellV1:
    return object.__new__(_SealedDeferredCommitPreparedCellV1)


def _issue_sealed_deferred_commit_kernel_case_v1(
    case_name: str,
    kernel_input: _DeferredCommitKernelInputV1,
) -> _SealedDeferredCommitKernelFixtureV1:
    lock_trace: list[str] = []
    lane = _SealedDeferredCommitLaneV1(
        controller_publication_lock=_SealedOrderedCommitLockV1(
            "controller_publication",
            lock_trace,
        ),
        ingress_subject_lock=_SealedOrderedCommitLockV1(
            "ingress_subject",
            lock_trace,
        ),
        claim_lock=_SealedOrderedCommitLockV1("a5_claim", lock_trace),
        lock_trace=lock_trace,
    )
    cells: list[object] = []
    for role, lifecycle, cell_type in (
        (
            "CLAIM",
            kernel_input.claim_lifecycle,
            _SealedDeferredCommitClaimCellV1,
        ),
        (
            "SUBJECT",
            kernel_input.subject_lifecycle,
            _SealedDeferredCommitSubjectCellV1,
        ),
        (
            "PENDING",
            kernel_input.pending_lifecycle,
            _SealedDeferredCommitPendingCellV1,
        ),
        (
            "SCOPE",
            kernel_input.completion_scope_lifecycle,
            _SealedDeferredCommitScopeCellV1,
        ),
        ("TERMINAL", _ENVELOPE_ISSUED, _SealedDeferredCommitTerminalCellV1),
    ):
        cell = object.__new__(cell_type)
        authority = _SealedDeferredCommitCellAuthorityV1(
            lane=lane,
            role=role,
            lifecycle=lifecycle,
            kernel_input=kernel_input,
        )
        _register_sealed_deferred_commit_cell_authority_v1(cell, authority)
        cells.append(cell)
    lane.completion_scope = weakref.ref(cells[3])
    lane.terminal = weakref.ref(cells[4])
    return _SealedDeferredCommitKernelFixtureV1(
        case_name=case_name,
        claim=cells[0],
        subject=cells[1],
        pending=cells[2],
        completion_scope=cells[3],
        terminal=cells[4],
    )


def _sealed_deferred_commit_kernel_input_v1(
    *,
    claim_lifecycle: str = "CLAIMED",
    subject_lifecycle: str = "FRESH",
    pending_lifecycle: str = "FRESH",
    proof_lifecycle: str = "PREPARED",
    completion_scope_lifecycle: str = "ACTIVE",
    same_session: bool = True,
    same_ingress: bool = True,
    same_runtime: bool = True,
    same_controller: bool = True,
    same_pending_parent: bool = True,
    same_publication_epoch: bool = True,
    same_owner: bool = True,
    before_stage: str = _COORDINATE_SOURCE_CLOSE_STAGE,
    after_stage: str = _COORDINATE_TERMINAL_STAGE,
    before_ordinal: int = 1,
    after_ordinal: int = 2,
) -> _DeferredCommitKernelInputV1:
    return _DeferredCommitKernelInputV1(
        claim_lifecycle=claim_lifecycle,
        subject_lifecycle=subject_lifecycle,
        pending_lifecycle=pending_lifecycle,
        proof_lifecycle=proof_lifecycle,
        completion_scope_lifecycle=completion_scope_lifecycle,
        same_session=same_session,
        same_ingress=same_ingress,
        same_runtime=same_runtime,
        same_controller=same_controller,
        same_pending_parent=same_pending_parent,
        same_publication_epoch=same_publication_epoch,
        same_owner=same_owner,
        before_stage=before_stage,
        after_stage=after_stage,
        before_ordinal=before_ordinal,
        after_ordinal=after_ordinal,
    )


def _issue_sealed_deferred_commit_kernel_fixture_v1(
) -> _SealedDeferredCommitKernelFixtureV1:
    return _issue_sealed_deferred_commit_kernel_case_v1(
        "valid",
        _sealed_deferred_commit_kernel_input_v1(),
    )


def _issue_sealed_deferred_commit_kernel_matrix_v1(
) -> tuple[_SealedDeferredCommitKernelFixtureV1, ...]:
    cases = (
        ("wrong_session", {"same_session": False}),
        ("wrong_ingress", {"same_ingress": False}),
        ("wrong_runtime", {"same_runtime": False}),
        ("wrong_controller", {"same_controller": False}),
        ("wrong_pending_parent", {"same_pending_parent": False}),
        ("wrong_publication_epoch", {"same_publication_epoch": False}),
        ("wrong_owner", {"same_owner": False}),
        ("wrong_claim_lifecycle", {"claim_lifecycle": "CONSUMED"}),
        ("wrong_subject_lifecycle", {"subject_lifecycle": "CONSUMED"}),
        (
            "wrong_pending_lifecycle",
            {"pending_lifecycle": "COMMIT_RESERVED"},
        ),
        (
            "wrong_scope_lifecycle",
            {"completion_scope_lifecycle": "CLEARED"},
        ),
        ("wrong_proof_lifecycle", {"proof_lifecycle": "ISSUED"}),
        ("wrong_before_stage", {"before_stage": "WRONG"}),
        ("wrong_after_stage", {"after_stage": "WRONG"}),
        ("equal_order", {"before_ordinal": 2, "after_ordinal": 2}),
        ("reverse_order", {"before_ordinal": 3, "after_ordinal": 2}),
        ("zero_before_order", {"before_ordinal": 0}),
        ("negative_before_order", {"before_ordinal": -1}),
        ("bool_before_order", {"before_ordinal": True}),
        ("overflow_after_order", {"after_ordinal": _SIGNED_63_MAX + 1}),
    )
    return tuple(
        _issue_sealed_deferred_commit_kernel_case_v1(
            case_name,
            _sealed_deferred_commit_kernel_input_v1(**overrides),
        )
        for case_name, overrides in cases
    )


def _resolve_sealed_deferred_commit_cell_state_v1(
    cell: object,
) -> str:
    if type(cell) not in (
        _SealedDeferredCommitClaimCellV1,
        _SealedDeferredCommitSubjectCellV1,
        _SealedDeferredCommitPendingCellV1,
        _SealedDeferredCommitProofCellV1,
        _SealedDeferredCommitPreparedCellV1,
        _SealedDeferredCommitScopeCellV1,
        _SealedDeferredCommitTerminalCellV1,
    ):
        raise TypeError("exact sealed deferred commit cell required")
    authority = _lookup_sealed_deferred_commit_cell_authority_v1(cell)
    if type(authority) is not _SealedDeferredCommitCellAuthorityV1:
        raise ValueError("sealed_deferred_commit_cell_invalid")
    return authority.lifecycle


def _observe_sealed_deferred_commit_fixture_v1(
    fixture: _SealedDeferredCommitKernelFixtureV1,
) -> _SealedDeferredCommitObservationV1:
    if type(fixture) is not _SealedDeferredCommitKernelFixtureV1:
        raise TypeError("exact sealed deferred commit fixture required")
    claim_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.claim
    )
    subject_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.subject
    )
    pending_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.pending
    )
    scope_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.completion_scope
    )
    if any(
        type(authority) is not _SealedDeferredCommitCellAuthorityV1
        for authority in (
            claim_authority,
            subject_authority,
            pending_authority,
            scope_authority,
        )
    ):
        raise ValueError("sealed_deferred_commit_cell_invalid")
    return _SealedDeferredCommitObservationV1(
        claim_lifecycle=claim_authority.lifecycle,
        subject_lifecycle=subject_authority.lifecycle,
        pending_lifecycle=pending_authority.lifecycle,
        scope_lifecycle=scope_authority.lifecycle,
        reserved_claim=pending_authority.reserved_claim,
        reserved_subject=pending_authority.reserved_subject,
        reserved_terminal=pending_authority.reserved_terminal,
        reserved_causal_proof=pending_authority.reserved_causal_proof,
        reserved_completion_scope=(
            pending_authority.reserved_completion_scope
        ),
        scope_reservation_committed=scope_authority.reservation_committed,
        scope_causal_proof=scope_authority.causal_proof,
        reserved_prepared_commit=(
            pending_authority.reserved_prepared_commit
        ),
    )


def _run_sealed_deferred_commit_kernel_v1(
    claim: _SealedDeferredCommitClaimCellV1,
    subject: _SealedDeferredCommitSubjectCellV1,
    pending: _SealedDeferredCommitPendingCellV1,
) -> _SealedDeferredCommitKernelResultV1:
    if type(claim) is not _SealedDeferredCommitClaimCellV1:
        raise TypeError("exact sealed deferred commit claim cell required")
    if type(subject) is not _SealedDeferredCommitSubjectCellV1:
        raise TypeError("exact sealed deferred commit subject cell required")
    if type(pending) is not _SealedDeferredCommitPendingCellV1:
        raise TypeError("exact sealed deferred commit pending cell required")
    claim_authority = _lookup_sealed_deferred_commit_cell_authority_v1(claim)
    subject_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        subject
    )
    pending_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        pending
    )
    authorities = (claim_authority, subject_authority, pending_authority)
    if (
        any(
            type(authority) is not _SealedDeferredCommitCellAuthorityV1
            for authority in authorities
        )
        or claim_authority.role != "CLAIM"
        or subject_authority.role != "SUBJECT"
        or pending_authority.role != "PENDING"
        or claim_authority.lane is not subject_authority.lane
        or claim_authority.lane is not pending_authority.lane
        or claim_authority.kernel_input is not subject_authority.kernel_input
        or claim_authority.kernel_input is not pending_authority.kernel_input
    ):
        raise ValueError("durable_causal_subject_mismatch")
    lane = claim_authority.lane
    scope = (
        None
        if lane.completion_scope is None
        else lane.completion_scope()
    )
    terminal = None if lane.terminal is None else lane.terminal()
    scope_authority = _lookup_sealed_deferred_commit_cell_authority_v1(scope)
    terminal_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        terminal
    )
    if (
        type(scope) is not _SealedDeferredCommitScopeCellV1
        or type(terminal) is not _SealedDeferredCommitTerminalCellV1
        or type(scope_authority) is not _SealedDeferredCommitCellAuthorityV1
        or type(terminal_authority)
        is not _SealedDeferredCommitCellAuthorityV1
        or scope_authority.role != "SCOPE"
        or terminal_authority.role != "TERMINAL"
        or scope_authority.lane is not lane
        or terminal_authority.lane is not lane
    ):
        raise ValueError("durable_causal_subject_mismatch")
    with lane.controller_publication_lock:
        with lane.ingress_subject_lock:
            with lane.claim_lock:
                if (
                    _lookup_sealed_deferred_commit_cell_authority_v1(claim)
                    is not claim_authority
                    or _lookup_sealed_deferred_commit_cell_authority_v1(subject)
                    is not subject_authority
                    or _lookup_sealed_deferred_commit_cell_authority_v1(pending)
                    is not pending_authority
                    or _lookup_sealed_deferred_commit_cell_authority_v1(scope)
                    is not scope_authority
                    or _lookup_sealed_deferred_commit_cell_authority_v1(
                        terminal
                    )
                    is not terminal_authority
                ):
                    raise ValueError("durable_causal_subject_mismatch")
                kernel_input = claim_authority.kernel_input
                prepared_input = _DeferredCommitKernelInputV1(
                    claim_lifecycle=claim_authority.lifecycle,
                    subject_lifecycle=subject_authority.lifecycle,
                    pending_lifecycle=pending_authority.lifecycle,
                    proof_lifecycle=kernel_input.proof_lifecycle,
                    completion_scope_lifecycle=scope_authority.lifecycle,
                    same_session=kernel_input.same_session,
                    same_ingress=kernel_input.same_ingress,
                    same_runtime=kernel_input.same_runtime,
                    same_controller=kernel_input.same_controller,
                    same_pending_parent=kernel_input.same_pending_parent,
                    same_publication_epoch=(
                        kernel_input.same_publication_epoch
                    ),
                    same_owner=kernel_input.same_owner,
                    before_stage=kernel_input.before_stage,
                    after_stage=kernel_input.after_stage,
                    before_ordinal=kernel_input.before_ordinal,
                    after_ordinal=kernel_input.after_ordinal,
                )
                _deferred_commit_transition_kernel_v1(prepared_input)
                proof = _allocate_sealed_deferred_commit_proof_cell_v1()
                prepared = _allocate_sealed_deferred_commit_prepared_cell_v1()
                proof_authority = _SealedDeferredCommitCellAuthorityV1(
                    lane=lane,
                    role="PROOF",
                    lifecycle=claim_authority.kernel_input.proof_lifecycle,
                    kernel_input=claim_authority.kernel_input,
                )
                prepared_authority = _SealedDeferredCommitCellAuthorityV1(
                    lane=lane,
                    role="PREPARED_COMMIT",
                    lifecycle="PROVISIONAL",
                    kernel_input=claim_authority.kernel_input,
                )
                try:
                    _register_sealed_deferred_commit_cell_authority_v1(
                        proof,
                        proof_authority,
                    )
                    _register_sealed_deferred_commit_cell_authority_v1(
                        prepared,
                        prepared_authority,
                    )
                except BaseException:
                    _unregister_sealed_deferred_commit_cell_authority_v1(
                        prepared,
                        prepared_authority,
                    )
                    _unregister_sealed_deferred_commit_cell_authority_v1(
                        proof,
                        proof_authority,
                    )
                    raise
                try:
                    result = _deferred_commit_transition_kernel_v1(
                        _DeferredCommitKernelInputV1(
                            claim_lifecycle=claim_authority.lifecycle,
                            subject_lifecycle=subject_authority.lifecycle,
                            pending_lifecycle=pending_authority.lifecycle,
                            proof_lifecycle=proof_authority.lifecycle,
                            completion_scope_lifecycle=(
                                scope_authority.lifecycle
                            ),
                            same_session=kernel_input.same_session,
                            same_ingress=kernel_input.same_ingress,
                            same_runtime=kernel_input.same_runtime,
                            same_controller=kernel_input.same_controller,
                            same_pending_parent=(
                                kernel_input.same_pending_parent
                            ),
                            same_publication_epoch=(
                                kernel_input.same_publication_epoch
                            ),
                            same_owner=kernel_input.same_owner,
                            before_stage=kernel_input.before_stage,
                            after_stage=kernel_input.after_stage,
                            before_ordinal=kernel_input.before_ordinal,
                            after_ordinal=kernel_input.after_ordinal,
                        )
                    )
                except BaseException:
                    _unregister_sealed_deferred_commit_cell_authority_v1(
                        prepared,
                        prepared_authority,
                    )
                    _unregister_sealed_deferred_commit_cell_authority_v1(
                        proof,
                        proof_authority,
                    )
                    raise
                claim_authority.lifecycle = result.claim_lifecycle
                subject_authority.lifecycle = result.subject_lifecycle
                pending_authority.lifecycle = result.pending_lifecycle
                pending_authority.reserved_claim = claim
                pending_authority.reserved_subject = subject
                pending_authority.reserved_terminal = terminal
                pending_authority.reserved_causal_proof = proof
                pending_authority.reserved_completion_scope = scope
                pending_authority.reserved_prepared_commit = prepared
                proof_authority.lifecycle = result.proof_lifecycle
                prepared_authority.lifecycle = "PREPARED"
                prepared_authority.success_armed = False
                scope_authority.lifecycle = (
                    result.completion_scope_lifecycle
                )
                scope_authority.reservation_committed = True
                scope_authority.causal_proof = proof
                return _SealedDeferredCommitKernelResultV1(
                    claim=claim,
                    subject=subject,
                    pending=pending,
                    proof=proof,
                    prepared=prepared,
                    lock_order=tuple(lane.lock_trace),
                )


def _consume_sealed_deferred_commit_terminal_v1(
    fixture: _SealedDeferredCommitKernelFixtureV1,
) -> None:
    if type(fixture) is not _SealedDeferredCommitKernelFixtureV1:
        raise TypeError("exact sealed deferred commit fixture required")
    pending_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.pending
    )
    scope_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.completion_scope
    )
    terminal_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.terminal
    )
    if (
        type(pending_authority) is not _SealedDeferredCommitCellAuthorityV1
        or type(scope_authority) is not _SealedDeferredCommitCellAuthorityV1
        or type(terminal_authority)
        is not _SealedDeferredCommitCellAuthorityV1
        or pending_authority.lane is not scope_authority.lane
        or pending_authority.lane is not terminal_authority.lane
    ):
        raise ValueError("durable_causal_subject_mismatch")
    lane = pending_authority.lane
    with lane.controller_publication_lock:
        with lane.ingress_subject_lock:
            if (
                pending_authority.lifecycle != "COMMIT_RESERVED"
                or scope_authority.lifecycle != "RESERVATION_COMMITTED"
                or scope_authority.reservation_committed is not True
                or terminal_authority.role != "TERMINAL"
                or terminal_authority.lifecycle != _ENVELOPE_ISSUED
            ):
                raise ValueError("durable_causal_subject_mismatch")
            terminal_authority.lifecycle = _ENVELOPE_CONSUMED


def _prepare_sealed_deferred_commit_proof_v1(
    fixture: _SealedDeferredCommitKernelFixtureV1,
    proof: _SealedDeferredCommitProofCellV1,
) -> _SealedDeferredCommitPreparedCellV1:
    if type(fixture) is not _SealedDeferredCommitKernelFixtureV1:
        raise TypeError("exact sealed deferred commit fixture required")
    if type(proof) is not _SealedDeferredCommitProofCellV1:
        raise TypeError("exact sealed deferred commit proof cell required")
    proof_authority = _lookup_sealed_deferred_commit_cell_authority_v1(proof)
    pending_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.pending
    )
    prepared = (
        None
        if type(pending_authority) is not _SealedDeferredCommitCellAuthorityV1
        else pending_authority.reserved_prepared_commit
    )
    prepared_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        prepared
    )
    if (
        type(proof_authority) is _SealedDeferredCommitCellAuthorityV1
        and proof_authority.lifecycle != "ISSUED"
    ):
        raise ValueError("durable_causal_proof_commit_consumed")
    if (
        type(prepared) is not _SealedDeferredCommitPreparedCellV1
        or type(prepared_authority) is not _SealedDeferredCommitCellAuthorityV1
        or type(proof_authority) is not _SealedDeferredCommitCellAuthorityV1
        or prepared_authority.role != "PREPARED_COMMIT"
        or prepared_authority.lifecycle != "PREPARED"
        or proof_authority.role != "PROOF"
        or proof_authority.lane is not prepared_authority.lane
        or pending_authority.reserved_causal_proof is not proof
    ):
        raise ValueError("durable_causal_proof_commit_invalid")
    if prepared_authority.success_armed is True:
        raise ValueError("durable_causal_proof_commit_consumed")
    lane = proof_authority.lane
    with lane.controller_publication_lock:
        with lane.ingress_subject_lock:
            if (
                _lookup_sealed_deferred_commit_cell_authority_v1(proof)
                is not proof_authority
                or _lookup_sealed_deferred_commit_cell_authority_v1(prepared)
                is not prepared_authority
                or pending_authority.reserved_prepared_commit is not prepared
                or pending_authority.reserved_causal_proof is not proof
            ):
                raise ValueError("durable_causal_proof_commit_invalid")
            if prepared_authority.success_armed is True:
                raise ValueError("durable_causal_proof_commit_consumed")
            prepared_authority.success_armed = True
            return prepared


def _commit_sealed_prepared_deferred_commit_v1(
    fixture: _SealedDeferredCommitKernelFixtureV1,
    prepared: _SealedDeferredCommitPreparedCellV1,
) -> None:
    if type(fixture) is not _SealedDeferredCommitKernelFixtureV1:
        raise TypeError("exact sealed deferred commit fixture required")
    if type(prepared) is not _SealedDeferredCommitPreparedCellV1:
        raise TypeError("exact sealed prepared commit cell required")
    prepared_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        prepared
    )
    if (
        type(prepared_authority) is _SealedDeferredCommitCellAuthorityV1
        and prepared_authority.lifecycle != "PREPARED"
    ):
        raise ValueError("durable_causal_proof_commit_consumed")
    pending_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.pending
    )
    proof = (
        None
        if type(pending_authority) is not _SealedDeferredCommitCellAuthorityV1
        else pending_authority.reserved_causal_proof
    )
    proof_authority = _lookup_sealed_deferred_commit_cell_authority_v1(proof)
    terminal_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.terminal
    )
    if (
        type(prepared_authority) is not _SealedDeferredCommitCellAuthorityV1
        or type(proof_authority) is not _SealedDeferredCommitCellAuthorityV1
        or type(terminal_authority) is not _SealedDeferredCommitCellAuthorityV1
        or prepared_authority.role != "PREPARED_COMMIT"
        or prepared_authority.lifecycle != "PREPARED"
        or prepared_authority.success_armed is not True
        or proof_authority.lifecycle != "ISSUED"
        or terminal_authority.lifecycle != _ENVELOPE_CONSUMED
        or pending_authority.reserved_prepared_commit is not prepared
    ):
        raise ValueError("durable_causal_proof_commit_invalid")
    lane = prepared_authority.lane
    with lane.controller_publication_lock:
        with lane.ingress_subject_lock:
            if (
                _lookup_sealed_deferred_commit_cell_authority_v1(prepared)
                is not prepared_authority
                or _lookup_sealed_deferred_commit_cell_authority_v1(proof)
                is not proof_authority
            ):
                raise ValueError("durable_causal_proof_commit_invalid")
            proof_authority.lifecycle = "CONSUMED_BY_FUTURE_COMPLETION"
            prepared_authority.lifecycle = "COMMITTED"


def _finalize_sealed_deferred_commit_proof_v1(
    fixture: _SealedDeferredCommitKernelFixtureV1,
    proof: _SealedDeferredCommitProofCellV1,
    *,
    append_succeeded: bool,
) -> None:
    if type(fixture) is not _SealedDeferredCommitKernelFixtureV1:
        raise TypeError("exact sealed deferred commit fixture required")
    if type(proof) is not _SealedDeferredCommitProofCellV1:
        raise TypeError("exact sealed deferred commit proof cell required")
    if type(append_succeeded) is not bool:
        raise TypeError("exact bool append_succeeded required")
    claim_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.claim
    )
    subject_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.subject
    )
    pending_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.pending
    )
    proof_authority = _lookup_sealed_deferred_commit_cell_authority_v1(proof)
    scope_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.completion_scope
    )
    terminal_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        fixture.terminal
    )
    prepared = (
        None
        if type(pending_authority) is not _SealedDeferredCommitCellAuthorityV1
        else pending_authority.reserved_prepared_commit
    )
    prepared_authority = _lookup_sealed_deferred_commit_cell_authority_v1(
        prepared
    )
    if (
        type(proof_authority) is _SealedDeferredCommitCellAuthorityV1
        and proof_authority.lifecycle != "ISSUED"
    ):
        raise ValueError("durable_causal_proof_consumed")
    authorities = (
        claim_authority,
        subject_authority,
        pending_authority,
        proof_authority,
        prepared_authority,
        scope_authority,
        terminal_authority,
    )
    if any(
        type(authority) is not _SealedDeferredCommitCellAuthorityV1
        for authority in authorities
    ):
        raise ValueError("durable_causal_subject_mismatch")
    lane = pending_authority.lane
    with lane.controller_publication_lock:
        with lane.ingress_subject_lock:
            exact_bindings = (
                claim_authority.role == "CLAIM"
                and subject_authority.role == "SUBJECT"
                and pending_authority.role == "PENDING"
                and proof_authority.role == "PROOF"
                and prepared_authority.role == "PREPARED_COMMIT"
                and scope_authority.role == "SCOPE"
                and terminal_authority.role == "TERMINAL"
                and all(authority.lane is lane for authority in authorities)
                and _lookup_sealed_deferred_commit_cell_authority_v1(proof)
                is proof_authority
            )
            target = _deferred_proof_finalization_kernel_v1(
                _DeferredProofFinalizationKernelInputV1(
                    proof_lifecycle=proof_authority.lifecycle,
                    exact_bindings=exact_bindings,
                    public_fields_valid=exact_bindings,
                    same_owner=proof_authority.kernel_input.same_owner,
                    subject_lifecycle=subject_authority.lifecycle,
                    pending_lifecycle=pending_authority.lifecycle,
                    completion_scope_lifecycle=scope_authority.lifecycle,
                    reservation_committed=(
                        scope_authority.reservation_committed
                    ),
                    reserved_slots_exact=(
                        pending_authority.reserved_claim is fixture.claim
                        and pending_authority.reserved_subject
                        is fixture.subject
                        and pending_authority.reserved_terminal
                        is fixture.terminal
                        and pending_authority.reserved_causal_proof is proof
                        and pending_authority.reserved_prepared_commit
                        is prepared
                        and pending_authority.reserved_completion_scope
                        is fixture.completion_scope
                        and scope_authority.causal_proof is proof
                    ),
                    terminal_lifecycle=terminal_authority.lifecycle,
                    append_succeeded=append_succeeded,
                )
            )
            proof_authority.lifecycle = target
            prepared_authority.lifecycle = (
                "COMMITTED" if append_succeeded else "FAILED_CLOSED"
            )
            if append_succeeded:
                prepared_authority.success_armed = True
            else:
                pending_authority.lifecycle = "PUBLICATION_FAILED_CLOSED"
                scope_authority.lifecycle = "APPEND_FAILED_CLOSED"


@dataclass(frozen=True, slots=True)
class _DeferredSubjectRepeatKernelInputV1:
    has_existing_subject: bool
    subject_lifecycle: str
    immutable_bindings_match: bool
    same_scope: bool
    prior_scope_lifecycle: str
    prior_scope_reserved: bool
    prior_scope_subject_matches: bool
    candidate_scope_lifecycle: str
    candidate_scope_reserved: bool
    candidate_scope_subject_clear_or_same: bool


@dataclass(frozen=True, slots=True)
class _DeferredSubjectRepeatKernelResultV1:
    action: str


def _deferred_subject_repeat_kernel_v1(
    value: _DeferredSubjectRepeatKernelInputV1,
) -> _DeferredSubjectRepeatKernelResultV1:
    if type(value) is not _DeferredSubjectRepeatKernelInputV1:
        raise TypeError("exact deferred subject repeat kernel input required")
    if (
        value.immutable_bindings_match is not True
        or value.candidate_scope_lifecycle != "ACTIVE"
        or value.candidate_scope_reserved is not False
        or value.candidate_scope_subject_clear_or_same is not True
    ):
        raise ValueError("durable_causal_subject_mismatch")
    if value.has_existing_subject is False:
        return _DeferredSubjectRepeatKernelResultV1(action="ISSUE")
    if value.has_existing_subject is not True or value.subject_lifecycle != "FRESH":
        raise ValueError("durable_causal_subject_mismatch")
    if value.same_scope is True:
        return _DeferredSubjectRepeatKernelResultV1(action="RETURN_SAME")
    if (
        value.same_scope is not False
        or value.prior_scope_lifecycle != "CLEARED"
        or value.prior_scope_reserved is not False
        or value.prior_scope_subject_matches is not True
    ):
        raise ValueError("durable_causal_subject_mismatch")
    return _DeferredSubjectRepeatKernelResultV1(action="REBIND_RETURN_SAME")


@dataclass(slots=True)
class _DeferredSubjectIndexEntryV1:
    pending_reference: weakref.ReferenceType[object]
    subject_reference: weakref.ReferenceType[object]
    terminal_reference: weakref.ReferenceType[object]
    pending_identity: int
    subject_identity: int
    terminal_identity: int
    lifecycle: str = "LIVE"


def _purge_dead_deferred_subject_index_entries_v1(
    ingress: BoundedIngress,
) -> None:
    if type(ingress) is not BoundedIngress:
        raise TypeError("exact BoundedIngress required")
    index = ingress._deferred_subject_by_pending
    dead_keys = [
        key
        for key, entry in index.items()
        if (
            type(entry) is not _DeferredSubjectIndexEntryV1
            or entry.pending_reference() is None
        )
    ]
    for key in dead_keys:
        index.pop(key, None)


def _lookup_deferred_subject_index_entry_v1(
    ingress: BoundedIngress,
    pending: object,
) -> _DeferredSubjectIndexEntryV1 | None:
    if type(ingress) is not BoundedIngress:
        raise TypeError("exact BoundedIngress required")
    entry = ingress._deferred_subject_by_pending.get(id(pending))
    if (
        type(entry) is not _DeferredSubjectIndexEntryV1
        or entry.pending_reference() is not pending
    ):
        return None
    return entry


def _register_deferred_subject_index_entry_v1(
    ingress: BoundedIngress,
    pending: object,
    subject: object,
    terminal: object,
) -> None:
    if type(ingress) is not BoundedIngress:
        raise TypeError("exact BoundedIngress required")
    try:
        pending_reference = weakref.ref(pending)
        subject_reference = weakref.ref(subject)
        terminal_reference = weakref.ref(terminal)
    except TypeError:
        raise TypeError("deferred subject index values must support weakref") from None
    _purge_dead_deferred_subject_index_entries_v1(ingress)
    index = ingress._deferred_subject_by_pending
    key = id(pending)
    prior = index.get(key)
    if type(prior) is _DeferredSubjectIndexEntryV1:
        if prior.pending_reference() is pending:
            raise ValueError("durable_causal_subject_mismatch")
        index.pop(key, None)
    elif prior is not None:
        index.pop(key, None)
    if len(index) >= 4096:
        raise RuntimeError("durable_causal_subject_registry_full") from None
    entry = _DeferredSubjectIndexEntryV1(
        pending_reference=pending_reference,
        subject_reference=subject_reference,
        terminal_reference=terminal_reference,
        pending_identity=key,
        subject_identity=id(subject),
        terminal_identity=id(terminal),
    )
    try:
        index[key] = entry
    except BaseException:
        if index.get(key) is entry:
            index.pop(key, None)
        raise


def _close_deferred_subject_index_entry_v1(
    ingress: BoundedIngress,
    pending: object,
    subject: object,
    terminal: object,
    *,
    lifecycle: str,
) -> None:
    entry = _lookup_deferred_subject_index_entry_v1(ingress, pending)
    if (
        type(entry) is not _DeferredSubjectIndexEntryV1
        or entry.subject_reference() is not subject
        or entry.terminal_reference() is not terminal
        or entry.lifecycle != "LIVE"
        or lifecycle not in ("CONSUMED", "ABORTED_CLOSED")
    ):
        raise ValueError("durable_causal_subject_mismatch")
    entry.lifecycle = lifecycle


class _SealedDeferredSubjectFixtureV1:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred subject fixture is privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred subject fixture cannot be subclassed")


class _SealedDeferredSubjectV1:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred subject is privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred subject cannot be subclassed")


class _SealedDeferredSubjectScopeV1:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred subject scope is privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred subject scope cannot be subclassed")


class _SealedDeferredSubjectTerminalV1:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("sealed deferred subject terminal is privately issued")

    def __init_subclass__(cls, **_: object) -> None:
        raise TypeError("sealed deferred subject terminal cannot be subclassed")


@dataclass(slots=True)
class _SealedDeferredSubjectScopeAuthorityV1:
    lane: object
    terminal: _SealedDeferredSubjectTerminalV1
    lifecycle: str = "ACTIVE"
    reservation_committed: bool = False
    subject: _SealedDeferredSubjectV1 | None = None


@dataclass(slots=True)
class _SealedDeferredSubjectAuthorityV1:
    lane: object
    terminal: _SealedDeferredSubjectTerminalV1
    lifecycle: str = "FRESH"


@dataclass(slots=True)
class _SealedDeferredSubjectFixtureAuthorityV1:
    lane: object
    terminal: _SealedDeferredSubjectTerminalV1
    current_scope: _SealedDeferredSubjectScopeV1
    subject: _SealedDeferredSubjectV1 | None = None


@dataclass(frozen=True, slots=True)
class _SealedDeferredSubjectObservationV1:
    terminal: _SealedDeferredSubjectTerminalV1
    current_scope: _SealedDeferredSubjectScopeV1
    subject: _SealedDeferredSubjectV1 | None
    subject_lifecycle: str | None


(
    _register_sealed_deferred_subject_fixture_authority_v1,
    _lookup_sealed_deferred_subject_fixture_authority_v1,
    _unregister_sealed_deferred_subject_fixture_authority_v1,
) = _make_identity_registry()
(
    _register_sealed_deferred_subject_authority_v1,
    _lookup_sealed_deferred_subject_authority_v1,
    _unregister_sealed_deferred_subject_authority_v1,
) = _make_identity_registry()
(
    _register_sealed_deferred_subject_scope_authority_v1,
    _lookup_sealed_deferred_subject_scope_authority_v1,
    _unregister_sealed_deferred_subject_scope_authority_v1,
) = _make_identity_registry()


def _allocate_sealed_deferred_subject_v1() -> _SealedDeferredSubjectV1:
    return object.__new__(_SealedDeferredSubjectV1)


def _issue_sealed_deferred_subject_scope_v1(
    *,
    lane: object,
    terminal: _SealedDeferredSubjectTerminalV1,
) -> _SealedDeferredSubjectScopeV1:
    scope = object.__new__(_SealedDeferredSubjectScopeV1)
    _register_sealed_deferred_subject_scope_authority_v1(
        scope,
        _SealedDeferredSubjectScopeAuthorityV1(
            lane=lane,
            terminal=terminal,
        ),
    )
    return scope


def _issue_sealed_deferred_subject_fixture_v1(
) -> _SealedDeferredSubjectFixtureV1:
    lane = object()
    terminal = object.__new__(_SealedDeferredSubjectTerminalV1)
    scope = _issue_sealed_deferred_subject_scope_v1(
        lane=lane,
        terminal=terminal,
    )
    fixture = object.__new__(_SealedDeferredSubjectFixtureV1)
    _register_sealed_deferred_subject_fixture_authority_v1(
        fixture,
        _SealedDeferredSubjectFixtureAuthorityV1(
            lane=lane,
            terminal=terminal,
            current_scope=scope,
        ),
    )
    return fixture


def _observe_sealed_deferred_subject_fixture_v1(
    fixture: _SealedDeferredSubjectFixtureV1,
) -> _SealedDeferredSubjectObservationV1:
    if type(fixture) is not _SealedDeferredSubjectFixtureV1:
        raise TypeError("exact sealed deferred subject fixture required")
    authority = _lookup_sealed_deferred_subject_fixture_authority_v1(fixture)
    if type(authority) is not _SealedDeferredSubjectFixtureAuthorityV1:
        raise ValueError("sealed_deferred_subject_fixture_invalid")
    subject_authority = (
        None
        if authority.subject is None
        else _lookup_sealed_deferred_subject_authority_v1(authority.subject)
    )
    if subject_authority is not None and type(subject_authority) is not _SealedDeferredSubjectAuthorityV1:
        raise ValueError("sealed_deferred_subject_fixture_invalid")
    return _SealedDeferredSubjectObservationV1(
        terminal=authority.terminal,
        current_scope=authority.current_scope,
        subject=authority.subject,
        subject_lifecycle=(
            None if subject_authority is None else subject_authority.lifecycle
        ),
    )


def _issue_sealed_deferred_subject_v1(
    fixture: _SealedDeferredSubjectFixtureV1,
    scope: _SealedDeferredSubjectScopeV1,
    terminal: _SealedDeferredSubjectTerminalV1,
) -> _SealedDeferredSubjectV1:
    if type(fixture) is not _SealedDeferredSubjectFixtureV1:
        raise TypeError("exact sealed deferred subject fixture required")
    if type(scope) is not _SealedDeferredSubjectScopeV1:
        raise TypeError("exact sealed deferred subject scope required")
    if type(terminal) is not _SealedDeferredSubjectTerminalV1:
        raise TypeError("exact sealed deferred subject terminal required")
    fixture_authority = _lookup_sealed_deferred_subject_fixture_authority_v1(
        fixture
    )
    scope_authority = _lookup_sealed_deferred_subject_scope_authority_v1(scope)
    if (
        type(fixture_authority)
        is not _SealedDeferredSubjectFixtureAuthorityV1
        or type(scope_authority)
        is not _SealedDeferredSubjectScopeAuthorityV1
    ):
        raise ValueError("durable_causal_subject_mismatch")
    existing_subject = fixture_authority.subject
    existing_authority = (
        None
        if existing_subject is None
        else _lookup_sealed_deferred_subject_authority_v1(existing_subject)
    )
    prior_scope = fixture_authority.current_scope
    prior_scope_authority = (
        _lookup_sealed_deferred_subject_scope_authority_v1(prior_scope)
    )
    if type(prior_scope_authority) is not _SealedDeferredSubjectScopeAuthorityV1:
        raise ValueError("durable_causal_subject_mismatch")
    decision = _deferred_subject_repeat_kernel_v1(
        _DeferredSubjectRepeatKernelInputV1(
            has_existing_subject=existing_subject is not None,
            subject_lifecycle=(
                "ABSENT"
                if existing_authority is None
                else existing_authority.lifecycle
            ),
            immutable_bindings_match=(
                scope_authority.lane is fixture_authority.lane
                and scope_authority.terminal is terminal
                and terminal is fixture_authority.terminal
                and prior_scope_authority.lane is fixture_authority.lane
                and prior_scope_authority.terminal
                is fixture_authority.terminal
                and (
                    existing_authority is None
                    or (
                        type(existing_authority)
                        is _SealedDeferredSubjectAuthorityV1
                        and existing_authority.lane is fixture_authority.lane
                        and existing_authority.terminal
                        is fixture_authority.terminal
                    )
                )
            ),
            same_scope=scope is prior_scope,
            prior_scope_lifecycle=prior_scope_authority.lifecycle,
            prior_scope_reserved=prior_scope_authority.reservation_committed,
            prior_scope_subject_matches=(
                prior_scope_authority.subject is existing_subject
            ),
            candidate_scope_lifecycle=scope_authority.lifecycle,
            candidate_scope_reserved=scope_authority.reservation_committed,
            candidate_scope_subject_clear_or_same=(
                scope_authority.subject in (None, existing_subject)
            ),
        )
    )
    if decision.action == "ISSUE":
        subject = _allocate_sealed_deferred_subject_v1()
        subject_authority = _SealedDeferredSubjectAuthorityV1(
            lane=fixture_authority.lane,
            terminal=terminal,
        )
        try:
            _register_sealed_deferred_subject_authority_v1(
                subject,
                subject_authority,
            )
        except BaseException:
            _unregister_sealed_deferred_subject_authority_v1(
                subject,
                subject_authority,
            )
            raise
        scope_authority.subject = subject
        fixture_authority.subject = subject
        return subject
    if type(existing_subject) is not _SealedDeferredSubjectV1:
        raise ValueError("durable_causal_subject_mismatch")
    if decision.action == "REBIND_RETURN_SAME":
        scope_authority.subject = existing_subject
        fixture_authority.current_scope = scope
    return existing_subject


def _prepare_sealed_corrected_completion_scope_v1(
    fixture: _SealedDeferredSubjectFixtureV1,
) -> _SealedDeferredSubjectScopeV1:
    authority = _lookup_sealed_deferred_subject_fixture_authority_v1(fixture)
    if type(authority) is not _SealedDeferredSubjectFixtureAuthorityV1:
        raise ValueError("sealed_deferred_subject_fixture_invalid")
    current = _lookup_sealed_deferred_subject_scope_authority_v1(
        authority.current_scope
    )
    if (
        type(current) is not _SealedDeferredSubjectScopeAuthorityV1
        or current.reservation_committed is not False
    ):
        raise ValueError("durable_causal_subject_mismatch")
    current.lifecycle = "CLEARED"
    return _issue_sealed_deferred_subject_scope_v1(
        lane=authority.lane,
        terminal=authority.terminal,
    )


def _prepare_sealed_uncleared_completion_scope_v1(
    fixture: _SealedDeferredSubjectFixtureV1,
) -> _SealedDeferredSubjectScopeV1:
    authority = _lookup_sealed_deferred_subject_fixture_authority_v1(fixture)
    if type(authority) is not _SealedDeferredSubjectFixtureAuthorityV1:
        raise ValueError("sealed_deferred_subject_fixture_invalid")
    return _issue_sealed_deferred_subject_scope_v1(
        lane=authority.lane,
        terminal=authority.terminal,
    )


def _prepare_sealed_reserved_completion_scope_v1(
    fixture: _SealedDeferredSubjectFixtureV1,
) -> _SealedDeferredSubjectScopeV1:
    authority = _lookup_sealed_deferred_subject_fixture_authority_v1(fixture)
    if type(authority) is not _SealedDeferredSubjectFixtureAuthorityV1:
        raise ValueError("sealed_deferred_subject_fixture_invalid")
    current = _lookup_sealed_deferred_subject_scope_authority_v1(
        authority.current_scope
    )
    if type(current) is not _SealedDeferredSubjectScopeAuthorityV1:
        raise ValueError("durable_causal_subject_mismatch")
    current.lifecycle = "RESERVATION_COMMITTED"
    current.reservation_committed = True
    return _issue_sealed_deferred_subject_scope_v1(
        lane=authority.lane,
        terminal=authority.terminal,
    )


def _consume_sealed_deferred_subject_v1(
    fixture: _SealedDeferredSubjectFixtureV1,
    subject: _SealedDeferredSubjectV1,
) -> None:
    authority = _lookup_sealed_deferred_subject_fixture_authority_v1(fixture)
    subject_authority = _lookup_sealed_deferred_subject_authority_v1(subject)
    if (
        type(authority) is not _SealedDeferredSubjectFixtureAuthorityV1
        or type(subject_authority) is not _SealedDeferredSubjectAuthorityV1
        or authority.subject is not subject
        or subject_authority.lifecycle != "FRESH"
    ):
        raise ValueError("durable_causal_subject_mismatch")
    subject_authority.lifecycle = "CONSUMED"


@dataclass(slots=True)
class _SealedDeferredSubjectAbortFixtureV1:
    publication_lock: _SealedOrderedCommitLockV1
    ingress_lock: _SealedOrderedCommitLockV1
    lock_trace: list[str]
    subject_lifecycle: str = "FRESH"
    terminal_lifecycle: str = _ENVELOPE_ISSUED
    terminal_live_envelope_present: bool = True
    index_lifecycle: str | None = "LIVE"
    publication_lock_owned_on_entry: bool = False
    publication_lock_owned_on_return: bool = False
    ingress_lock_owned_on_return: bool = False


@dataclass(frozen=True, slots=True)
class _SealedDeferredSubjectAbortObservationV1:
    subject_lifecycle: str
    terminal_lifecycle: str
    terminal_live_envelope_present: bool
    index_lifecycle: str | None
    publication_lock_owned_on_entry: bool
    publication_lock_owned_on_return: bool
    ingress_lock_owned_on_return: bool
    lock_trace: tuple[str, ...]


def _issue_sealed_deferred_subject_abort_fixture_v1(
) -> _SealedDeferredSubjectAbortFixtureV1:
    trace: list[str] = []
    return _SealedDeferredSubjectAbortFixtureV1(
        publication_lock=_SealedOrderedCommitLockV1(
            "controller_publication",
            trace,
        ),
        ingress_lock=_SealedOrderedCommitLockV1(
            "ingress_subject",
            trace,
        ),
        lock_trace=trace,
    )


def _observe_sealed_deferred_subject_abort_fixture_v1(
    fixture: _SealedDeferredSubjectAbortFixtureV1,
) -> _SealedDeferredSubjectAbortObservationV1:
    if type(fixture) is not _SealedDeferredSubjectAbortFixtureV1:
        raise TypeError("exact sealed deferred subject abort fixture required")
    return _SealedDeferredSubjectAbortObservationV1(
        subject_lifecycle=fixture.subject_lifecycle,
        terminal_lifecycle=fixture.terminal_lifecycle,
        terminal_live_envelope_present=(
            fixture.terminal_live_envelope_present
        ),
        index_lifecycle=fixture.index_lifecycle,
        publication_lock_owned_on_entry=(
            fixture.publication_lock_owned_on_entry
        ),
        publication_lock_owned_on_return=(
            fixture.publication_lock_owned_on_return
        ),
        ingress_lock_owned_on_return=(
            fixture.ingress_lock_owned_on_return
        ),
        lock_trace=tuple(fixture.lock_trace),
    )


def _run_sealed_deferred_subject_abort_v1(
    fixture: _SealedDeferredSubjectAbortFixtureV1,
    *,
    inject_uncertainty: bool = False,
) -> _SealedDeferredSubjectAbortObservationV1:
    if type(fixture) is not _SealedDeferredSubjectAbortFixtureV1:
        raise TypeError("exact sealed deferred subject abort fixture required")
    if type(inject_uncertainty) is not bool:
        raise TypeError("exact bool inject_uncertainty required")
    if (
        fixture.subject_lifecycle != "FRESH"
        or fixture.terminal_lifecycle != _ENVELOPE_ISSUED
        or fixture.terminal_live_envelope_present is not True
        or fixture.index_lifecycle != "LIVE"
    ):
        raise ValueError("durable_causal_subject_consumed")
    with fixture.publication_lock:
        fixture.publication_lock_owned_on_entry = (
            fixture.publication_lock._is_owned()
        )
        try:
            with fixture.ingress_lock:
                if inject_uncertainty:
                    raise RuntimeError("sealed abort uncertainty injection")
                fixture.subject_lifecycle = "ABORTED_CLOSED"
                fixture.terminal_lifecycle = _ENVELOPE_CONSUMED
                fixture.terminal_live_envelope_present = False
                fixture.index_lifecycle = "ABORTED_CLOSED"
        except RuntimeError:
            fixture.ingress_lock_owned_on_return = (
                fixture.ingress_lock._is_owned()
            )
            fixture.publication_lock_owned_on_return = (
                fixture.publication_lock._is_owned()
            )
            raise RuntimeError("durable_causal_subject_abort_uncertain") from None
        fixture.ingress_lock_owned_on_return = fixture.ingress_lock._is_owned()
        fixture.publication_lock_owned_on_return = (
            fixture.publication_lock._is_owned()
        )
        return _observe_sealed_deferred_subject_abort_fixture_v1(fixture)


class _IngressNode:
    __slots__ = (
        "item",
        "completion",
        "acknowledgement",
        "completion_lock",
        "state",
        "receipt",
        "receipt_deadline",
        "acknowledgement_timed_out",
        "raw_parent",
        "parent_envelope",
        "terminal_cause",
    )

    def __init__(self, item: IngressItem) -> None:
        self.item = item
        self.completion = threading.Event()
        self.acknowledgement = threading.Event()
        self.completion_lock = threading.Lock()
        self.state = _PROVISIONAL
        self.receipt: DurableIngressReceipt | None = None
        self.receipt_deadline: float | None = None
        self.acknowledgement_timed_out = False
        self.raw_parent: PersistedEvent | None = None
        self.parent_envelope: DurableIngressParentV1 | None = None
        self.terminal_cause: str | None = None

    def __repr__(self) -> str:
        return f"<_IngressNode state={self.state!r}>"


class BoundedIngress:
    """Serialize bounded concurrent capture into one owner-thread runtime."""

    __slots__ = (
        "_capacity",
        "_producer_timeout_seconds",
        "_receipt_timeout_seconds",
        "_queue",
        "_admission_lock",
        "_condition",
        "_owner_pid",
        "_owner_thread",
        "_runtime",
        "_normal_closed",
        "_fault_reason",
        "_runtime_failed",
        "_terminal_written",
        "_terminal_intent",
        "_poll_in_progress",
        "_active_node",
        "_allocation_coordinate",
        "_ingress_identity_sha256",
        "_runtime_identity_sha256",
        "_causal_ordinal",
        "_causal_subject_lock",
        "_deferred_subject_by_pending",
        "_durable_consumer",
    )

    def __init__(
        self,
        *,
        capacity: int,
        producer_timeout_seconds: float,
        receipt_timeout_seconds: float,
    ) -> None:
        self._capacity = _positive_integer(capacity, "capacity")
        self._producer_timeout_seconds = _positive_timeout(
            producer_timeout_seconds,
            "producer_timeout_seconds",
        )
        self._receipt_timeout_seconds = _positive_timeout(
            receipt_timeout_seconds,
            "receipt_timeout_seconds",
        )
        self._queue: queue.Queue[_IngressNode] = queue.Queue(
            maxsize=self._capacity
        )
        self._admission_lock = threading.Lock()
        self._condition = threading.Condition(self._admission_lock)
        self._owner_pid = getpid()
        self._owner_thread = threading.current_thread()
        self._runtime: EventRuntime | None = None
        self._normal_closed = False
        self._fault_reason: str | None = None
        self._runtime_failed = False
        self._terminal_written = False
        self._terminal_intent: str | None = None
        self._poll_in_progress = False
        self._active_node: _IngressNode | None = None
        self._allocation_coordinate = _next_ingress_allocation_coordinate()
        self._ingress_identity_sha256 = _domain_sha256(
            "INCI-INGRESS-IDENTITY-V1",
            {
                "schema_version": 1,
                "owner_pid": self._owner_pid,
                "allocation_coordinate": self._allocation_coordinate,
            },
        )
        self._runtime_identity_sha256: str | None = None
        self._causal_ordinal = 0
        self._causal_subject_lock = threading.RLock()
        self._deferred_subject_by_pending: dict[
            int,
            tuple[object, DeferredEmergencyCommitSubjectV1],
        ] = {}
        self._durable_consumer: object | None = None

    def __repr__(self) -> str:
        return "<BoundedIngress redacted>"

    @property
    def halt_reason(self) -> str | None:
        with self._admission_lock:
            return self._fault_reason

    def _require_owner(self) -> None:
        if (
            getpid() != self._owner_pid
            or threading.current_thread() is not self._owner_thread
        ):
            raise WrongOwnerThread("ingress_wrong_owner_thread")

    def _admission_open_locked(self) -> bool:
        return (
            not self._normal_closed
            and self._fault_reason is None
            and not self._runtime_failed
            and not self._terminal_written
            and self._terminal_intent is None
        )

    def _closed_error_locked(self) -> RuntimeError:
        if (
            self._runtime_failed
            or self._terminal_written
            or self._terminal_intent is not None
        ):
            return IngressClosed("ingress_closed")
        if self._fault_reason == _BACKPRESSURE:
            return IngressBackpressureHalt("ingress_backpressure")
        if self._fault_reason == _OWNER_UNRESPONSIVE:
            return IngressOwnerUnresponsive("ingress_owner_unresponsive")
        return IngressClosed("ingress_closed")

    def enqueue(self, item: IngressItem) -> DurableIngressReceipt:
        admission_started = time.monotonic()
        if type(item) is not IngressItem:
            raise TypeError("exact IngressItem required")
        _validate_exact_ingress_item(item)
        deadline = admission_started + self._producer_timeout_seconds
        node = _IngressNode(item)

        with self._condition:
            while True:
                if not self._admission_open_locked():
                    raise self._closed_error_locked()
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    self._fault_reason = _BACKPRESSURE
                    self._condition.notify_all()
                    raise IngressBackpressureHalt(
                        "ingress_backpressure"
                    )
                try:
                    self._queue.put_nowait(node)
                except queue.Full:
                    self._condition.wait(remaining)
                    continue
                node.receipt_deadline = (
                    time.monotonic() + self._receipt_timeout_seconds
                )
                self._condition.notify_all()
                break

        if type(node.receipt_deadline) is not float:
            raise IngressClosed("ingress_runtime_unavailable")
        remaining = node.receipt_deadline - time.monotonic()
        if remaining > 0.0:
            node.completion.wait(remaining)
        timeout_won = False
        acknowledgement_invalid = False
        with node.completion_lock:
            if node.state == _RECEIPT_PUBLISHED:
                receipt = node.receipt
                parent = node.raw_parent
                if time.monotonic() >= node.receipt_deadline:
                    node.receipt = None
                    node.raw_parent = None
                    node.state = _DURABLE_UNACKNOWLEDGED
                    node.terminal_cause = _OWNER_TIMEOUT_CAUSE
                    node.acknowledgement.set()
                    timeout_won = True
                elif (
                    type(receipt) is not DurableIngressReceipt
                    or receipt is not node.receipt
                    or type(parent) is not PersistedEvent
                    or receipt.producer_id != node.item.producer_id
                    or receipt.producer_sequence
                    != node.item.producer_sequence
                    or receipt.raw_ingest_seq != parent.ingest_seq
                    or receipt.raw_record_sha256
                    != canonical_record_sha256(parent)
                ):
                    node.receipt = None
                    node.raw_parent = None
                    node.state = _DURABLE_UNACKNOWLEDGED
                    node.acknowledgement.set()
                    acknowledgement_invalid = True
                else:
                    node.state = _PRODUCER_ACKNOWLEDGED
                    node.acknowledgement.set()
                    return receipt
            if node.state in (_INGEST_FAILED_NO_RETURN, _ABORTED):
                if node.terminal_cause == _OWNER_TIMEOUT_CAUSE:
                    timeout_won = True
                else:
                    raise IngressClosed("ingress_runtime_unavailable")
            elif node.state == _QUEUED:
                with self._condition:
                    node.state = _TIMED_OUT_NO_RAW
                    node.terminal_cause = _OWNER_TIMEOUT_CAUSE
                    if (
                        self._fault_reason is None
                        and not self._runtime_failed
                        and not self._terminal_written
                        and self._terminal_intent is None
                    ):
                        self._fault_reason = _OWNER_UNRESPONSIVE
                    self._condition.notify_all()
                node.acknowledgement.set()
                timeout_won = True
            elif node.state in (
                _OWNER_CLAIMED,
                _INGESTING,
                _RAW_RETURNED,
            ):
                node.acknowledgement_timed_out = True
                node.terminal_cause = _OWNER_TIMEOUT_CAUSE
                with self._condition:
                    if (
                        self._fault_reason is None
                        and not self._runtime_failed
                        and not self._terminal_written
                        and self._terminal_intent is None
                    ):
                        self._fault_reason = _OWNER_UNRESPONSIVE
                    self._condition.notify_all()
                node.acknowledgement.set()
                timeout_won = True
            elif node.state in (
                _TIMED_OUT_NO_RAW,
                _DURABLE_UNACKNOWLEDGED,
            ):
                node.terminal_cause = _OWNER_TIMEOUT_CAUSE
                timeout_won = True
            elif node.state in (_PRODUCER_ACKNOWLEDGED, _PARENT_ISSUED):
                receipt = node.receipt
                if type(receipt) is not DurableIngressReceipt:
                    raise IngressClosed("ingress_runtime_unavailable")
                return receipt
            else:
                raise IngressClosed("ingress_runtime_unavailable")

        if acknowledgement_invalid:
            with self._condition:
                if (
                    self._fault_reason is None
                    and not self._runtime_failed
                    and not self._terminal_written
                    and self._terminal_intent is None
                ):
                    self._fault_reason = _OWNER_UNRESPONSIVE
                self._condition.notify_all()
            raise IngressClosed("ingress_runtime_unavailable")
        if timeout_won:
            raise IngressOwnerUnresponsive("ingress_owner_unresponsive")
        raise IngressClosed("ingress_runtime_unavailable")

    def close_inputs(self) -> None:
        with self._condition:
            if (
                self._normal_closed
                or self._fault_reason is not None
                or self._runtime_failed
                or self._terminal_written
            ):
                return
            self._normal_closed = True
            self._condition.notify_all()

    def close_external_halt_terminal(
        self,
        runtime: EventRuntime,
    ) -> DurableEvidenceTerminalV1:
        if type(runtime) is not EventRuntime:
            raise TypeError("exact EventRuntime required")
        self._require_owner()
        with self._condition:
            if self._runtime is None:
                raise IngressClosed("ingress_runtime_unbound")
            if runtime is not self._runtime:
                raise IngressClosed("ingress_runtime_mismatch")
            if (
                self._runtime_failed
                or self._terminal_written
                or self._terminal_intent is not None
            ):
                raise IngressClosed("ingress_closed")
            if (
                self._active_node is not None
                or self._poll_in_progress
                or not self._queue.empty()
            ):
                raise IngressClosed("ingress_not_between_drains")
        runtime.require_owner()
        with self._condition:
            if runtime is not self._runtime:
                raise IngressClosed("ingress_runtime_mismatch")
            if (
                self._runtime_failed
                or self._terminal_written
                or self._terminal_intent is not None
            ):
                raise IngressClosed("ingress_closed")
            if (
                self._active_node is not None
                or self._poll_in_progress
                or not self._queue.empty()
            ):
                raise IngressClosed("ingress_not_between_drains")
            if self._fault_reason == _BACKPRESSURE:
                action = _BACKPRESSURE
            elif self._fault_reason == _OWNER_UNRESPONSIVE:
                action = _OWNER_UNRESPONSIVE
            else:
                action = "operator_halt"
            self._normal_closed = True
            self._terminal_intent = action
            self._condition.notify_all()
        return self._finalize(runtime, action)

    def close_external_halt(
        self,
        runtime: EventRuntime,
    ) -> PersistedEvent:
        envelope = self.close_external_halt_terminal(runtime)
        _consume_durable_envelope_legacy_v1(envelope)
        return envelope.terminal

    def _next_action_locked(self) -> str | None:
        if (
            self._terminal_intent is not None
            or self._active_node is not None
            or not self._queue.empty()
        ):
            return None
        if self._fault_reason == _BACKPRESSURE:
            return _BACKPRESSURE
        if self._fault_reason == _OWNER_UNRESPONSIVE:
            return _OWNER_UNRESPONSIVE
        if self._normal_closed:
            return "operator_stop"
        return None

    def _claim_action_locked(self) -> str | None:
        action = self._next_action_locked()
        if action is not None:
            self._terminal_intent = action
        return action

    def _settle_failed_node(self, node: _IngressNode) -> None:
        with node.completion_lock:
            if node.state not in (
                _PARENT_ISSUED,
                _PARENT_CONSUMED,
                _ABORTED,
            ):
                node.state = _ABORTED
            node.acknowledgement.set()
            node.completion.set()

    def _runtime_failure(self, active: _IngressNode | None) -> None:
        pending: list[_IngressNode] = []
        with self._condition:
            self._runtime_failed = True
            if self._active_node is active:
                self._active_node = None
            while True:
                try:
                    pending.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._condition.notify_all()
        if active is not None:
            self._settle_failed_node(active)
        for node in pending:
            self._settle_failed_node(node)

    def _finalize(
        self,
        runtime: EventRuntime,
        action: str,
    ) -> DurableEvidenceTerminalV1:
        try:
            if action == _BACKPRESSURE:
                terminal = runtime.close_ingress_backpressure()
                terminal_reason = "ingress_backpressure"
            elif action == _OWNER_UNRESPONSIVE:
                terminal = runtime.close_ingress_owner_unresponsive()
                terminal_reason = "ingress_owner_unresponsive"
            elif action == "session_end":
                terminal = runtime.close_ingress_session_end()
                terminal_reason = "session_end"
            elif action == "operator_halt":
                terminal = runtime.close_halted("operator_halt")
                terminal_reason = "operator_halt"
            else:
                terminal = runtime.close_clean("operator_stop")
                terminal_reason = "operator_stop"
        except BaseException:
            self._runtime_failure(None)
            raise
        envelope = _issue_durable_evidence_terminal_v1(
            self,
            runtime,
            terminal,
            terminal_reason,
        )
        with self._condition:
            self._terminal_written = True
            self._condition.notify_all()
        return envelope

    def _process_node(
        self,
        runtime: EventRuntime,
        node: _IngressNode,
    ) -> DurableIngressParentV1 | DurableEvidenceTerminalV1 | None:
        timed_out_before_claim = False
        with node.completion_lock:
            deadline = node.receipt_deadline
            if (
                node.state == _QUEUED
                and type(deadline) is float
                and time.monotonic() < deadline
            ):
                node.state = _OWNER_CLAIMED
            elif node.state in (_QUEUED, _TIMED_OUT_NO_RAW):
                node.state = _TIMED_OUT_NO_RAW
                node.terminal_cause = _OWNER_TIMEOUT_CAUSE
                node.state = _ABORTED
                timed_out_before_claim = True
            else:
                node.state = _ABORTED
                timed_out_before_claim = True

        if timed_out_before_claim:
            self._runtime_failure(node)
            raise IngressClosed("ingress_runtime_unavailable")

        timed_out_before_ingest = False
        with node.completion_lock:
            deadline = node.receipt_deadline
            if (
                node.state == _OWNER_CLAIMED
                and type(deadline) is float
                and time.monotonic() < deadline
                and not node.acknowledgement_timed_out
            ):
                node.state = _INGESTING
            else:
                node.terminal_cause = _OWNER_TIMEOUT_CAUSE
                node.state = _ABORTED
                node.completion.set()
                node.acknowledgement.set()
                timed_out_before_ingest = True

        if timed_out_before_ingest:
            self._runtime_failure(node)
            raise IngressClosed("ingress_runtime_unavailable")

        try:
            raw = runtime.ingest(node.item.captured)
            if type(raw) is not PersistedEvent:
                raise IngressClosed("ingress_runtime_unavailable")
        except BaseException:
            with node.completion_lock:
                node.state = _INGEST_FAILED_NO_RETURN
                node.state = _ABORTED
                node.completion.set()
                node.acknowledgement.set()
            self._runtime_failure(node)
            raise

        with node.completion_lock:
            if node.state != _INGESTING:
                node.state = _ABORTED
                node.completion.set()
                node.acknowledgement.set()
                raw_state_invalid = True
            else:
                node.state = _RAW_RETURNED
                raw_state_invalid = False
        if raw_state_invalid:
            self._runtime_failure(node)
            raise IngressClosed("ingress_runtime_unavailable")

        try:
            receipt = DurableIngressReceipt(
                producer_id=node.item.producer_id,
                producer_sequence=node.item.producer_sequence,
                raw_ingest_seq=raw.ingest_seq,
                raw_record_sha256=canonical_record_sha256(raw),
            )
        except BaseException:
            with node.completion_lock:
                node.state = _DURABLE_UNACKNOWLEDGED
                node.state = _ABORTED
                node.completion.set()
                node.acknowledgement.set()
            self._runtime_failure(node)
            raise

        published = False
        with node.completion_lock:
            deadline = node.receipt_deadline
            if (
                node.state == _RAW_RETURNED
                and type(deadline) is float
                and time.monotonic() < deadline
                and not node.acknowledgement_timed_out
            ):
                node.raw_parent = raw
                node.receipt = receipt
                node.state = _RECEIPT_PUBLISHED
                published = True
            elif node.state == _RAW_RETURNED:
                node.receipt = None
                node.raw_parent = None
                node.state = _DURABLE_UNACKNOWLEDGED
                node.terminal_cause = _OWNER_TIMEOUT_CAUSE
            node.completion.set()

        if published:
            remaining = node.receipt_deadline - time.monotonic()  # type: ignore[operator]
            if remaining > 0.0:
                node.acknowledgement.wait(remaining)

        parent_envelope: DurableIngressParentV1 | None = None
        parent_issue_failed = False
        with node.completion_lock:
            if node.state == _PRODUCER_ACKNOWLEDGED:
                if (
                    node.receipt is not receipt
                    or node.raw_parent is not raw
                    or receipt.producer_id != node.item.producer_id
                    or receipt.producer_sequence
                    != node.item.producer_sequence
                    or receipt.raw_ingest_seq != raw.ingest_seq
                    or receipt.raw_record_sha256
                    != canonical_record_sha256(raw)
                ):
                    node.receipt = None
                    node.raw_parent = None
                    node.state = _DURABLE_UNACKNOWLEDGED
                else:
                    try:
                        parent_envelope = _issue_durable_ingress_parent_v1(
                            self,
                            runtime,
                            node.item,
                            raw,
                            receipt,
                            node,
                        )
                        if type(parent_envelope) is not DurableIngressParentV1:
                            raise IngressClosed(
                                "ingress_runtime_unavailable"
                            )
                        node.parent_envelope = parent_envelope
                        node.state = _PARENT_ISSUED
                    except BaseException:
                        if type(parent_envelope) is DurableIngressParentV1:
                            issued_authority = _lookup_envelope_authority(
                                parent_envelope
                            )
                            if type(issued_authority) is _EnvelopeAuthorityV1:
                                _unregister_envelope_authority(
                                    parent_envelope,
                                    issued_authority,
                                )
                        parent_envelope = None
                        node.parent_envelope = None
                        node.state = _ABORTED
                        parent_issue_failed = True
            elif node.state == _RECEIPT_PUBLISHED:
                node.receipt = None
                node.raw_parent = None
                node.state = _DURABLE_UNACKNOWLEDGED
                node.terminal_cause = _OWNER_TIMEOUT_CAUSE

            durable_unacknowledged = (
                node.state == _DURABLE_UNACKNOWLEDGED
            )
            if durable_unacknowledged:
                node.state = _ABORTED

        if parent_issue_failed:
            self._runtime_failure(node)
            raise IngressClosed("ingress_runtime_unavailable") from None

        with self._condition:
            if self._active_node is node:
                self._active_node = None
            if durable_unacknowledged:
                if (
                    self._fault_reason is None
                    and not self._runtime_failed
                    and not self._terminal_written
                    and self._terminal_intent is None
                ):
                    self._fault_reason = _OWNER_UNRESPONSIVE
            self._condition.notify_all()
        if durable_unacknowledged:
            self._runtime_failure(node)
            raise IngressClosed("ingress_runtime_unavailable")
        if parent_envelope is not None:
            return parent_envelope
        raise IngressClosed("ingress_runtime_unavailable")

    def drain_one_parent(
        self,
        runtime: EventRuntime,
        *,
        timeout_seconds: float,
    ) -> DurableIngressParentV1 | DurableEvidenceTerminalV1 | None:
        timeout = _positive_timeout(timeout_seconds, "timeout_seconds")
        if type(runtime) is not EventRuntime:
            raise TypeError("exact EventRuntime required")
        self._require_owner()
        with self._condition:
            if self._runtime is not None and runtime is not self._runtime:
                raise IngressClosed("ingress_runtime_mismatch")
            if (
                self._terminal_written
                or self._runtime_failed
                or self._terminal_intent is not None
            ):
                raise IngressClosed("ingress_closed")
        runtime.require_owner()
        with self._condition:
            if self._runtime is None:
                self._runtime = runtime
            elif runtime is not self._runtime:
                raise IngressClosed("ingress_runtime_mismatch")
            if (
                self._terminal_written
                or self._runtime_failed
                or self._terminal_intent is not None
            ):
                raise IngressClosed("ingress_closed")
        deadline = time.monotonic() + timeout

        while True:
            node: _IngressNode | None = None
            action: str | None = None
            active_invariant_breach: _IngressNode | None = None
            with self._condition:
                if (
                    self._terminal_written
                    or self._runtime_failed
                    or self._terminal_intent is not None
                ):
                    raise IngressClosed("ingress_closed")
                if self._active_node is not None:
                    active_invariant_breach = self._active_node
                else:
                    try:
                        node = self._queue.get_nowait()
                    except queue.Empty:
                        node = None
                    if node is not None:
                        self._active_node = node
                        self._condition.notify_all()
                    else:
                        action = self._claim_action_locked()
                        if action is None:
                            self._poll_in_progress = True
            if active_invariant_breach is not None:
                self._runtime_failure(active_invariant_breach)
                raise IngressClosed("ingress_runtime_unavailable") from None
            if node is not None:
                outcome = self._process_node(runtime, node)
                if outcome is None:
                    continue
                return outcome
            if action is not None:
                return self._finalize(runtime, action)

            try:
                session_ended = runtime.check_ingress_session_end()
            except BaseException:
                self._runtime_failure(None)
                raise

            with self._condition:
                self._poll_in_progress = False
                active_invariant_breach = self._active_node
                if active_invariant_breach is not None:
                    node = None
                    action = None
                else:
                    try:
                        node = self._queue.get_nowait()
                    except queue.Empty:
                        node = None
                    if node is not None:
                        self._active_node = node
                        self._condition.notify_all()
                    else:
                        action = self._claim_action_locked()
                        if (
                            action is None
                            and session_ended is True
                            and self._admission_open_locked()
                        ):
                            action = "session_end"
                            self._terminal_intent = action
                    if node is None and action is None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0.0:
                            return None
                        action = self._claim_action_locked()
                        if action is None:
                            self._condition.wait(remaining)
                            continue
            if active_invariant_breach is not None:
                self._runtime_failure(active_invariant_breach)
                raise IngressClosed("ingress_runtime_unavailable") from None
            if node is not None:
                outcome = self._process_node(runtime, node)
                if outcome is None:
                    continue
                return outcome
            if action is not None:
                return self._finalize(runtime, action)

    def drain_one(
        self,
        runtime: EventRuntime,
        *,
        timeout_seconds: float,
    ) -> PersistedEvent | None:
        envelope = self.drain_one_parent(
            runtime,
            timeout_seconds=timeout_seconds,
        )
        if envelope is None:
            return None
        _consume_durable_envelope_legacy_v1(envelope)
        if type(envelope) is DurableIngressParentV1:
            return envelope.parent
        if type(envelope) is DurableEvidenceTerminalV1:
            return envelope.terminal
        raise IngressClosed("ingress_runtime_unavailable")


def _runtime_identity_sha256_v1(
    ingress: BoundedIngress,
    runtime: EventRuntime,
    session_id: str,
) -> str:
    with ingress._condition:
        if ingress._runtime is not runtime:
            raise ValueError("durable_causal_coordinate_invalid")
        current = ingress._runtime_identity_sha256
        if current is None:
            current = _domain_sha256(
                "INCI-SHADOW-RUNTIME-IDENTITY-V1",
                {
                    "schema_version": 1,
                    "session_id": session_id,
                    "owner_pid": ingress._owner_pid,
                    "ingress_identity_sha256": (
                        ingress._ingress_identity_sha256
                    ),
                },
            )
            ingress._runtime_identity_sha256 = current
        return current


def _build_opaque_value(value_type: type, fields: dict[str, object]):
    value = object.__new__(value_type)
    for field_name, field_value in fields.items():
        object.__setattr__(value, field_name, field_value)
    return value


def _issue_durable_ingress_parent_v1(
    ingress: BoundedIngress,
    runtime: EventRuntime,
    item: IngressItem,
    parent: PersistedEvent,
    receipt: DurableIngressReceipt,
    node: _IngressNode,
) -> DurableIngressParentV1:
    if (
        type(ingress) is not BoundedIngress
        or type(runtime) is not EventRuntime
        or type(item) is not IngressItem
        or type(parent) is not PersistedEvent
        or type(receipt) is not DurableIngressReceipt
        or type(node) is not _IngressNode
        or node.item is not item
        or node.raw_parent is not parent
        or node.receipt is not receipt
        or receipt.raw_ingest_seq != parent.ingest_seq
        or receipt.raw_record_sha256 != canonical_record_sha256(parent)
    ):
        raise ValueError("durable_ingress_parent_invalid")
    item_sha256 = _ingress_item_sha256_v1(item)
    parent_record_sha256 = canonical_record_sha256(parent)
    receipt_sha256 = _durable_ingress_receipt_sha256_v1(receipt)
    envelope_sha256 = _domain_sha256(
        "INCI-DURABLE-INGRESS-PARENT-ENVELOPE-V1",
        {
            "schema_version": 1,
            "ingress_item_sha256": item_sha256,
            "parent_record_sha256": parent_record_sha256,
            "durable_receipt_sha256": receipt_sha256,
        },
    )
    envelope = _build_opaque_value(
        DurableIngressParentV1,
        {
            "schema_version": 1,
            "item": item,
            "parent": parent,
            "receipt": receipt,
            "envelope_sha256": envelope_sha256,
        },
    )
    authority = _EnvelopeAuthorityV1(
        ingress=ingress,
        runtime=runtime,
        session_id=parent.session_id,
        owner_pid=ingress._owner_pid,
        owner_thread=ingress._owner_thread,
        consumer=ingress._durable_consumer,
        kind="PARENT",
        item=item,
        parent=parent,
        receipt=receipt,
        terminal=None,
        terminal_reason=None,
        coordinate=None,
        node=node,
        issuance=_ParentIssuanceSnapshotV1(
            schema_version=1,
            session_id=parent.session_id,
            producer_id=item.producer_id,
            producer_sequence=item.producer_sequence,
            item_sha256=item_sha256,
            raw_ingest_seq=parent.ingest_seq,
            parent_record_sha256=parent_record_sha256,
            receipt_producer_id=receipt.producer_id,
            receipt_producer_sequence=receipt.producer_sequence,
            receipt_raw_ingest_seq=receipt.raw_ingest_seq,
            receipt_raw_record_sha256=receipt.raw_record_sha256,
            receipt_sha256=receipt_sha256,
            envelope_sha256=envelope_sha256,
        ),
        live_envelope=envelope,
    )
    try:
        _register_envelope_authority(envelope, authority)
    except BaseException:
        _unregister_envelope_authority(envelope, authority)
        raise
    return envelope


def _issue_coordinate_v1(
    ingress: BoundedIngress,
    runtime: EventRuntime,
    *,
    session_id: str,
    stage: str,
    subject: object,
    subject_sha256: str,
) -> DurableCausalOrderCoordinateV1:
    if stage not in (
        _COORDINATE_SOURCE_CLOSE_STAGE,
        _COORDINATE_TERMINAL_STAGE,
    ):
        raise ValueError("durable_causal_coordinate_invalid")
    with ingress._condition:
        if (
            ingress._runtime is not runtime
            or getpid() != ingress._owner_pid
            or threading.current_thread() is not ingress._owner_thread
            or ingress._causal_ordinal >= _SIGNED_63_MAX
        ):
            raise ValueError("durable_causal_coordinate_invalid")
        ingress._causal_ordinal += 1
        ordinal = ingress._causal_ordinal
    runtime_identity_sha256 = _runtime_identity_sha256_v1(
        ingress,
        runtime,
        session_id,
    )
    projection = {
        "schema_version": 1,
        "session_id": session_id,
        "ingress_identity_sha256": ingress._ingress_identity_sha256,
        "runtime_identity_sha256": runtime_identity_sha256,
        "stage": stage,
        "ordinal": ordinal,
    }
    coordinate_sha256 = _domain_sha256(
        "INCI-DURABLE-CAUSAL-ORDER-COORDINATE-V1",
        projection,
    )
    coordinate = _build_opaque_value(
        DurableCausalOrderCoordinateV1,
        {
            **projection,
            "coordinate_sha256": coordinate_sha256,
        },
    )
    _register_coordinate_authority(
        coordinate,
        _CoordinateAuthorityV1(
            ingress=ingress,
            runtime=runtime,
            owner_pid=ingress._owner_pid,
            owner_thread=ingress._owner_thread,
            stage=stage,
            subject=subject,
            subject_sha256=subject_sha256,
            issuance=_CoordinateIssuanceSnapshotV1(
                schema_version=1,
                session_id=session_id,
                ingress_identity_sha256=ingress._ingress_identity_sha256,
                runtime_identity_sha256=runtime_identity_sha256,
                stage=stage,
                ordinal=ordinal,
                subject_sha256=subject_sha256,
                coordinate_sha256=coordinate_sha256,
            ),
        ),
    )
    return coordinate


def _issue_evidence_terminal_coordinate_v1(
    ingress: BoundedIngress,
    terminal: PersistedEvent,
    terminal_record_sha256: str,
) -> DurableCausalOrderCoordinateV1:
    if type(ingress) is not BoundedIngress:
        raise TypeError("exact BoundedIngress required")
    if type(terminal) is not PersistedEvent:
        raise TypeError("exact PersistedEvent required")
    if (
        type(terminal_record_sha256) is not str
        or terminal_record_sha256 != canonical_record_sha256(terminal)
        or type(ingress._runtime) is not EventRuntime
    ):
        raise ValueError("durable_causal_coordinate_invalid")
    return _issue_coordinate_v1(
        ingress,
        ingress._runtime,
        session_id=terminal.session_id,
        stage=_COORDINATE_TERMINAL_STAGE,
        subject=terminal,
        subject_sha256=terminal_record_sha256,
    )


def _issue_durable_evidence_terminal_v1(
    ingress: BoundedIngress,
    runtime: EventRuntime,
    terminal: PersistedEvent,
    terminal_reason: str,
) -> DurableEvidenceTerminalV1:
    if (
        type(ingress) is not BoundedIngress
        or type(runtime) is not EventRuntime
        or type(terminal) is not PersistedEvent
        or runtime is not ingress._runtime
        or terminal_reason
        not in {
            "operator_stop",
            "session_end",
            "ingress_backpressure",
            "ingress_owner_unresponsive",
            "operator_halt",
        }
    ):
        raise ValueError("durable_evidence_terminal_invalid")
    terminal_record_sha256 = canonical_record_sha256(terminal)
    coordinate = _issue_evidence_terminal_coordinate_v1(
        ingress,
        terminal,
        terminal_record_sha256,
    )
    projection = {
        "schema_version": 1,
        "session_id": terminal.session_id,
        "ingress_identity_sha256": ingress._ingress_identity_sha256,
        "terminal_record_sha256": terminal_record_sha256,
        "terminal_ingest_seq": terminal.ingest_seq,
        "evidence_terminal_coordinate_sha256": (
            coordinate.coordinate_sha256
        ),
        "terminal_reason": terminal_reason,
    }
    envelope_sha256 = _domain_sha256(
        "INCI-DURABLE-EVIDENCE-TERMINAL-ENVELOPE-V1",
        projection,
    )
    envelope = _build_opaque_value(
        DurableEvidenceTerminalV1,
        {
            "schema_version": 1,
            "session_id": terminal.session_id,
            "ingress_identity_sha256": ingress._ingress_identity_sha256,
            "terminal": terminal,
            "terminal_ingest_seq": terminal.ingest_seq,
            "terminal_record_sha256": terminal_record_sha256,
            "evidence_terminal_coordinate_sha256": (
                coordinate.coordinate_sha256
            ),
            "terminal_reason": terminal_reason,
            "envelope_sha256": envelope_sha256,
        },
    )
    authority = _EnvelopeAuthorityV1(
        ingress=ingress,
        runtime=runtime,
        session_id=terminal.session_id,
        owner_pid=ingress._owner_pid,
        owner_thread=ingress._owner_thread,
        consumer=ingress._durable_consumer,
        kind="TERMINAL",
        item=None,
        parent=None,
        receipt=None,
        terminal=terminal,
        terminal_reason=terminal_reason,
        coordinate=coordinate,
        node=None,
        issuance=_TerminalIssuanceSnapshotV1(
            schema_version=1,
            session_id=terminal.session_id,
            ingress_identity_sha256=ingress._ingress_identity_sha256,
            terminal_ingest_seq=terminal.ingest_seq,
            terminal_record_sha256=terminal_record_sha256,
            coordinate_sha256=coordinate.coordinate_sha256,
            terminal_reason=terminal_reason,
            envelope_sha256=envelope_sha256,
        ),
        live_envelope=envelope,
    )
    try:
        _register_envelope_authority(envelope, authority)
    except BaseException:
        _unregister_envelope_authority(envelope, authority)
        raise
    return envelope


def _bind_durable_ingress_consumer_v1(
    ingress: BoundedIngress,
    consumer: object,
) -> None:
    if type(ingress) is not BoundedIngress:
        raise TypeError("exact BoundedIngress required")
    if consumer is None:
        raise ValueError("durable_ingress_parent_invalid")
    ingress._require_owner()
    with ingress._condition:
        if (
            ingress._durable_consumer is not None
            or ingress._runtime is not None
            or ingress._normal_closed
            or ingress._fault_reason is not None
            or ingress._terminal_written
            or ingress._runtime_failed
            or ingress._terminal_intent is not None
            or ingress._poll_in_progress
            or ingress._active_node is not None
            or not ingress._queue.empty()
            or ingress._causal_ordinal != 0
        ):
            raise ValueError("durable_ingress_parent_invalid")
        ingress._durable_consumer = consumer


def _coordinate_public_fields_valid_v1(
    coordinate: DurableCausalOrderCoordinateV1,
    authority: _CoordinateAuthorityV1,
) -> bool:
    try:
        snapshot = authority.issuance
        if type(snapshot) is not _CoordinateIssuanceSnapshotV1:
            return False
        projection = {
            "schema_version": coordinate.schema_version,
            "session_id": coordinate.session_id,
            "ingress_identity_sha256": coordinate.ingress_identity_sha256,
            "runtime_identity_sha256": coordinate.runtime_identity_sha256,
            "stage": coordinate.stage,
            "ordinal": coordinate.ordinal,
        }
        return (
            type(coordinate.schema_version) is int
            and coordinate.schema_version == snapshot.schema_version == 1
            and type(coordinate.session_id) is str
            and coordinate.session_id == snapshot.session_id
            and coordinate.ingress_identity_sha256
            == snapshot.ingress_identity_sha256
            == authority.ingress._ingress_identity_sha256
            and coordinate.runtime_identity_sha256
            == snapshot.runtime_identity_sha256
            == authority.ingress._runtime_identity_sha256
            and coordinate.stage == snapshot.stage == authority.stage
            and type(coordinate.ordinal) is int
            and coordinate.ordinal == snapshot.ordinal
            and 0 < snapshot.ordinal <= _SIGNED_63_MAX
            and coordinate.coordinate_sha256
            == snapshot.coordinate_sha256
            and coordinate.coordinate_sha256
            == _domain_sha256(
                "INCI-DURABLE-CAUSAL-ORDER-COORDINATE-V1",
                projection,
            )
            and authority.subject_sha256 == snapshot.subject_sha256
            and authority.ingress._runtime is authority.runtime
            and authority.owner_pid == authority.ingress._owner_pid
            and authority.owner_thread is authority.ingress._owner_thread
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _envelope_public_fields_valid_v1(
    envelope: DurableIngressParentV1 | DurableEvidenceTerminalV1,
    authority: _EnvelopeAuthorityV1,
) -> bool:
    try:
        if authority.kind == "PARENT":
            if type(envelope) is not DurableIngressParentV1:
                return False
            snapshot = authority.issuance
            item = authority.item
            parent = authority.parent
            receipt = authority.receipt
            node = authority.node
            if (
                type(snapshot) is not _ParentIssuanceSnapshotV1
                or type(item) is not IngressItem
                or type(parent) is not PersistedEvent
                or type(receipt) is not DurableIngressReceipt
                or type(node) is not _IngressNode
            ):
                return False
            item_sha256 = _ingress_item_sha256_v1(item)
            parent_record_sha256 = canonical_record_sha256(parent)
            receipt_sha256 = _durable_ingress_receipt_sha256_v1(receipt)
            expected_envelope_sha256 = _domain_sha256(
                "INCI-DURABLE-INGRESS-PARENT-ENVELOPE-V1",
                {
                    "schema_version": 1,
                    "ingress_item_sha256": item_sha256,
                    "parent_record_sha256": parent_record_sha256,
                    "durable_receipt_sha256": receipt_sha256,
                },
            )
            return (
                type(envelope.schema_version) is int
                and envelope.schema_version == snapshot.schema_version == 1
                and envelope.item is item
                and envelope.parent is parent
                and envelope.receipt is receipt
                and envelope.envelope_sha256
                == snapshot.envelope_sha256
                == expected_envelope_sha256
                and authority.session_id
                == snapshot.session_id
                == parent.session_id
                and authority.ingress._runtime is authority.runtime
                and node.item is item
                and node.raw_parent is parent
                and node.receipt is receipt
                and (
                    node.parent_envelope is envelope
                    if authority.lifecycle == _ENVELOPE_ISSUED
                    else (
                        authority.lifecycle == _ENVELOPE_CONSUMED
                        and node.parent_envelope is None
                        and node.state == _PARENT_CONSUMED
                    )
                )
                and item.producer_id
                == snapshot.producer_id
                == receipt.producer_id
                == snapshot.receipt_producer_id
                and item.producer_sequence
                == snapshot.producer_sequence
                == receipt.producer_sequence
                == snapshot.receipt_producer_sequence
                and item_sha256 == snapshot.item_sha256
                and parent.ingest_seq
                == snapshot.raw_ingest_seq
                == receipt.raw_ingest_seq
                == snapshot.receipt_raw_ingest_seq
                and parent_record_sha256
                == snapshot.parent_record_sha256
                == receipt.raw_record_sha256
                == snapshot.receipt_raw_record_sha256
                and receipt_sha256 == snapshot.receipt_sha256
            )

        if (
            authority.kind != "TERMINAL"
            or type(envelope) is not DurableEvidenceTerminalV1
            or type(authority.terminal) is not PersistedEvent
            or type(authority.coordinate)
            is not DurableCausalOrderCoordinateV1
            or type(authority.terminal_reason) is not str
            or type(authority.issuance)
            is not _TerminalIssuanceSnapshotV1
        ):
            return False
        snapshot = authority.issuance
        terminal = authority.terminal
        coordinate = authority.coordinate
        coordinate_authority = _lookup_coordinate_authority(coordinate)
        if (
            type(coordinate_authority) is not _CoordinateAuthorityV1
            or coordinate_authority.ingress is not authority.ingress
            or coordinate_authority.runtime is not authority.runtime
            or coordinate_authority.stage != _COORDINATE_TERMINAL_STAGE
            or coordinate_authority.subject is not terminal
            or not _coordinate_public_fields_valid_v1(
                coordinate,
                coordinate_authority,
            )
        ):
            return False
        terminal_record_sha256 = canonical_record_sha256(terminal)
        projection = {
            "schema_version": 1,
            "session_id": terminal.session_id,
            "ingress_identity_sha256": (
                authority.ingress._ingress_identity_sha256
            ),
            "terminal_record_sha256": terminal_record_sha256,
            "terminal_ingest_seq": terminal.ingest_seq,
            "evidence_terminal_coordinate_sha256": (
                coordinate.coordinate_sha256
            ),
            "terminal_reason": authority.terminal_reason,
        }
        return (
            type(envelope.schema_version) is int
            and envelope.schema_version == snapshot.schema_version == 1
            and envelope.session_id
            == snapshot.session_id
            == terminal.session_id
            and envelope.ingress_identity_sha256
            == snapshot.ingress_identity_sha256
            == authority.ingress._ingress_identity_sha256
            and envelope.terminal is terminal
            and envelope.terminal_ingest_seq
            == snapshot.terminal_ingest_seq
            == terminal.ingest_seq
            and envelope.terminal_record_sha256
            == snapshot.terminal_record_sha256
            == terminal_record_sha256
            and envelope.evidence_terminal_coordinate_sha256
            == snapshot.coordinate_sha256
            == coordinate.coordinate_sha256
            and envelope.terminal_reason
            == snapshot.terminal_reason
            == authority.terminal_reason
            and envelope.envelope_sha256
            == snapshot.envelope_sha256
            == _domain_sha256(
                "INCI-DURABLE-EVIDENCE-TERMINAL-ENVELOPE-V1",
                projection,
            )
            and authority.session_id == snapshot.session_id
            and authority.ingress._runtime is authority.runtime
            and coordinate_authority.subject_sha256
            == snapshot.terminal_record_sha256
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _validate_envelope_for_consumer_v1(
    envelope: DurableIngressParentV1 | DurableEvidenceTerminalV1,
    consumer: object,
    *,
    expected_kind: str,
    legacy: bool = False,
) -> _EnvelopeAuthorityV1:
    authority = _lookup_envelope_authority(envelope)
    invalid_code = (
        "durable_ingress_parent_invalid"
        if expected_kind == "PARENT"
        else "durable_evidence_terminal_invalid"
    )
    consumed_code = (
        "durable_ingress_parent_consumed"
        if expected_kind == "PARENT"
        else "durable_evidence_terminal_consumed"
    )
    if (
        type(authority) is not _EnvelopeAuthorityV1
        or authority.kind != expected_kind
        or (
            authority.consumer is not None
            if legacy
            else authority.consumer is not consumer
        )
        or getpid() != authority.owner_pid
        or threading.current_thread() is not authority.owner_thread
        or not _envelope_public_fields_valid_v1(envelope, authority)
    ):
        raise ValueError(invalid_code)
    if authority.lifecycle == _ENVELOPE_CONSUMED:
        raise ValueError(consumed_code)
    if authority.lifecycle != _ENVELOPE_ISSUED:
        raise ValueError(invalid_code)
    return authority


def _validate_durable_ingress_parent_for_consumer_v1(
    envelope: DurableIngressParentV1,
    consumer: object,
) -> None:
    if type(envelope) is not DurableIngressParentV1:
        raise TypeError("exact DurableIngressParentV1 required")
    authority = _lookup_envelope_authority(envelope)
    if type(authority) is not _EnvelopeAuthorityV1:
        raise ValueError("durable_ingress_parent_invalid")
    node = authority.node
    if type(node) is not _IngressNode:
        raise ValueError("durable_ingress_parent_invalid")
    with node.completion_lock:
        _validate_envelope_for_consumer_v1(
            envelope,
            consumer,
            expected_kind="PARENT",
        )


def _validate_durable_evidence_terminal_for_consumer_v1(
    envelope: DurableEvidenceTerminalV1,
    consumer: object,
) -> None:
    if type(envelope) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    authority = _lookup_envelope_authority(envelope)
    if type(authority) is not _EnvelopeAuthorityV1:
        raise ValueError("durable_evidence_terminal_invalid")
    with authority.ingress._causal_subject_lock:
        _validate_envelope_for_consumer_v1(
            envelope,
            consumer,
            expected_kind="TERMINAL",
        )


def _consume_envelope_v1(
    envelope: DurableIngressParentV1 | DurableEvidenceTerminalV1,
    consumer: object,
    *,
    legacy: bool = False,
) -> None:
    authority = _lookup_envelope_authority(envelope)
    expected_kind = (
        "PARENT"
        if type(envelope) is DurableIngressParentV1
        else "TERMINAL"
    )
    invalid_code = (
        "durable_ingress_parent_invalid"
        if expected_kind == "PARENT"
        else "durable_evidence_terminal_invalid"
    )
    if type(authority) is not _EnvelopeAuthorityV1:
        raise ValueError(invalid_code)
    if authority.node is not None:
        with authority.node.completion_lock:
            authority = _validate_envelope_for_consumer_v1(
                envelope,
                consumer,
                expected_kind=expected_kind,
                legacy=legacy,
            )
            authority.lifecycle = _ENVELOPE_CONSUMED
            authority.node.state = _PARENT_CONSUMED
            authority.node.parent_envelope = None
            authority.live_envelope = None
    else:
        with authority.ingress._causal_subject_lock:
            authority = _validate_envelope_for_consumer_v1(
                envelope,
                consumer,
                expected_kind=expected_kind,
                legacy=legacy,
            )
            authority.lifecycle = _ENVELOPE_CONSUMED
            authority.live_envelope = None


def _consume_durable_ingress_parent_v1(
    envelope: DurableIngressParentV1,
    consumer: object,
) -> DurableIngressParentV1:
    if type(envelope) is not DurableIngressParentV1:
        raise TypeError("exact DurableIngressParentV1 required")
    _consume_envelope_v1(envelope, consumer)
    return envelope


def _consume_durable_evidence_terminal_v1(
    envelope: DurableEvidenceTerminalV1,
    consumer: object,
) -> DurableEvidenceTerminalV1:
    if type(envelope) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    _consume_envelope_v1(envelope, consumer)
    return envelope


def _consume_durable_envelope_legacy_v1(
    envelope: DurableIngressParentV1 | DurableEvidenceTerminalV1,
) -> None:
    if type(envelope) not in (
        DurableIngressParentV1,
        DurableEvidenceTerminalV1,
    ):
        raise IngressClosed("ingress_runtime_unavailable")
    try:
        _consume_envelope_v1(envelope, object(), legacy=True)
    except (TypeError, ValueError):
        raise IngressClosed("ingress_runtime_unavailable")


def _resolve_evidence_terminal_coordinate_v1(
    terminal: DurableEvidenceTerminalV1,
) -> DurableCausalOrderCoordinateV1:
    if type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    authority = _lookup_envelope_authority(terminal)
    if (
        type(authority) is not _EnvelopeAuthorityV1
        or authority.kind != "TERMINAL"
        or type(authority.coordinate) is not DurableCausalOrderCoordinateV1
        or not _envelope_public_fields_valid_v1(terminal, authority)
    ):
        raise ValueError("durable_evidence_terminal_invalid")
    return authority.coordinate


def _issue_source_close_complete_coordinate_v1(
    ingress: BoundedIngress,
    close_set: object,
) -> DurableCausalOrderCoordinateV1:
    if type(ingress) is not BoundedIngress:
        raise TypeError("exact BoundedIngress required")
    try:
        from inci_tennis_runtime.shadow_sources import (
            ShadowSourceCloseSetV1,
            _resolve_shadow_source_close_set_for_causal_coordinate_v1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_coordinate_invalid") from None
    if type(close_set) is not ShadowSourceCloseSetV1:
        raise TypeError("exact ShadowSourceCloseSetV1 required")
    try:
        binding = _resolve_shadow_source_close_set_for_causal_coordinate_v1(
            close_set
        )
        bound_ingress, session_id, runtime_identity, close_set_sha256 = binding
    except Exception:
        raise ValueError("durable_causal_coordinate_invalid") from None
    if (
        bound_ingress is not ingress
        or type(ingress._runtime) is not EventRuntime
        or runtime_identity
        != _runtime_identity_sha256_v1(
            ingress,
            ingress._runtime,
            session_id,
        )
    ):
        raise ValueError("durable_causal_coordinate_invalid")
    return _issue_coordinate_v1(
        ingress,
        ingress._runtime,
        session_id=session_id,
        stage=_COORDINATE_SOURCE_CLOSE_STAGE,
        subject=close_set,
        subject_sha256=close_set_sha256,
    )


def _subject_public_fields_valid_v1(
    subject: DeferredEmergencyCommitSubjectV1,
    authority: _SubjectAuthorityV1,
    *,
    allowed_terminal_lifecycles: tuple[str, ...] = (_ENVELOPE_ISSUED,),
) -> bool:
    try:
        snapshot = authority.issuance
        if type(snapshot) is not _SubjectIssuanceSnapshotV1:
            return False
        terminal_authority = _lookup_envelope_authority(authority.terminal)
        parent_authority = _lookup_envelope_authority(authority.parent)
        coordinate_authority = _lookup_coordinate_authority(
            authority.coordinate
        )
        projection = {
            "schema_version": subject.schema_version,
            "session_id": subject.session_id,
            "controller_identity_sha256": (
                subject.controller_identity_sha256
            ),
            "pending_sha256": subject.pending_sha256,
            "durable_parent_envelope_sha256": (
                subject.durable_parent_envelope_sha256
            ),
            "evidence_terminal_sha256": (
                subject.evidence_terminal_sha256
            ),
            "evidence_terminal_coordinate_sha256": (
                subject.evidence_terminal_coordinate_sha256
            ),
        }
        return (
            type(subject.schema_version) is int
            and subject.schema_version == snapshot.schema_version == 1
            and subject.session_id
            == authority.session_id
            == snapshot.session_id
            and subject.controller_identity_sha256
            == authority.controller_identity_sha256
            == snapshot.controller_identity_sha256
            and subject.pending_sha256
            == authority.pending_sha256
            == snapshot.pending_sha256
            and subject.durable_parent_envelope_sha256
            == authority.parent.envelope_sha256
            == snapshot.durable_parent_envelope_sha256
            and subject.evidence_terminal_sha256
            == authority.terminal.envelope_sha256
            == snapshot.evidence_terminal_sha256
            and subject.evidence_terminal_coordinate_sha256
            == authority.coordinate.coordinate_sha256
            == snapshot.evidence_terminal_coordinate_sha256
            and subject.subject_sha256
            == snapshot.subject_sha256
            == _domain_sha256(
                "INCI-DEFERRED-EMERGENCY-COMMIT-SUBJECT-V1",
                projection,
            )
            and authority.controller is snapshot.controller
            and authority.ingress is snapshot.ingress
            and authority.runtime is snapshot.runtime
            and authority.pending is snapshot.pending
            and authority.parent is snapshot.parent
            and authority.terminal is snapshot.terminal
            and authority.coordinate is snapshot.coordinate
            and authority.publication_lock is snapshot.publication_lock
            and authority.completion_scope.controller is snapshot.controller
            and authority.completion_scope.pending is snapshot.pending
            and authority.completion_scope.pending_authority
            is snapshot.completion_pending_authority
            and authority.completion_scope.terminal is snapshot.terminal
            and authority.completion_scope.publication_lock
            is snapshot.publication_lock
            and authority.completion_scope.publication_epoch
            == snapshot.publication_epoch
            and authority.completion_scope.owner_pid == snapshot.owner_pid
            and authority.completion_scope.owner_thread
            is snapshot.owner_thread
            and authority.publication_epoch == snapshot.publication_epoch
            and authority.owner_pid == snapshot.owner_pid
            and authority.owner_thread is snapshot.owner_thread
            and authority.ingress._ingress_identity_sha256
            == snapshot.ingress_identity_sha256
            and _runtime_identity_sha256_v1(
                authority.ingress,
                authority.runtime,
                authority.session_id,
            )
            == snapshot.runtime_identity_sha256
            and authority.ingress._runtime is authority.runtime
            and getpid() == authority.owner_pid
            and threading.current_thread() is authority.owner_thread
            and type(terminal_authority) is _EnvelopeAuthorityV1
            and type(parent_authority) is _EnvelopeAuthorityV1
            and terminal_authority.lifecycle in allowed_terminal_lifecycles
            and parent_authority.lifecycle == _ENVELOPE_CONSUMED
            and type(coordinate_authority) is _CoordinateAuthorityV1
            and terminal_authority.ingress is authority.ingress
            and terminal_authority.runtime is authority.runtime
            and terminal_authority.consumer is authority.controller
            and terminal_authority.session_id == authority.session_id
            and terminal_authority.coordinate is authority.coordinate
            and parent_authority.ingress is authority.ingress
            and parent_authority.runtime is authority.runtime
            and parent_authority.consumer is authority.controller
            and parent_authority.session_id == authority.session_id
            and coordinate_authority.ingress is authority.ingress
            and coordinate_authority.runtime is authority.runtime
            and coordinate_authority.stage == _COORDINATE_TERMINAL_STAGE
            and coordinate_authority.subject
            is terminal_authority.terminal
            and _envelope_public_fields_valid_v1(
                authority.terminal,
                terminal_authority,
            )
            and _envelope_public_fields_valid_v1(
                authority.parent,
                parent_authority,
            )
            and _coordinate_public_fields_valid_v1(
                authority.coordinate,
                coordinate_authority,
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _issue_deferred_emergency_commit_subject_v1(
    *,
    controller: object,
    pending: object,
    terminal: DurableEvidenceTerminalV1,
) -> DeferredEmergencyCommitSubjectV1:
    if type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    try:
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            ExpertControllerV1,
            PendingDurableCompanionEmergencyV1,
            _resolve_deferred_emergency_subject_inputs_v1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_subject_mismatch") from None
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(pending) is not PendingDurableCompanionEmergencyV1:
        raise TypeError("exact PendingDurableCompanionEmergencyV1 required")
    terminal_authority = _lookup_envelope_authority(terminal)
    if (
        type(terminal_authority) is not _EnvelopeAuthorityV1
        or terminal_authority.kind != "TERMINAL"
        or terminal_authority.lifecycle != _ENVELOPE_ISSUED
        or type(terminal_authority.coordinate)
        is not DurableCausalOrderCoordinateV1
        or not _envelope_public_fields_valid_v1(
            terminal,
            terminal_authority,
        )
    ):
        raise ValueError("durable_causal_subject_mismatch")
    try:
        resolved = _resolve_deferred_emergency_subject_inputs_v1(
            controller,
            pending,
        )
        (
            ingress,
            parent,
            controller_identity_sha256,
            pending_sha256,
            publication_epoch,
            publication_lock,
            active_scope,
        ) = resolved
    except Exception:
        raise ValueError("durable_causal_subject_mismatch") from None
    if (
        ingress is not terminal_authority.ingress
        or ingress._runtime is not terminal_authority.runtime
        or type(ingress._deferred_subject_by_pending) is not dict
        or terminal_authority.consumer is not controller
        or type(parent) is not DurableIngressParentV1
        or type(publication_epoch) is not int
        or type(active_scope) is not _DeferredEmergencyCompletionScopeV1
        or active_scope.terminal is not terminal
        or active_scope.controller is not controller
        or active_scope.pending is not pending
        or type(active_scope.pending_authority)
        is not _PendingDurableCompanionEmergencyAuthorityV1
        or active_scope.publication_lock is not publication_lock
        or active_scope.publication_epoch != publication_epoch
        or active_scope.owner_pid != getpid()
        or active_scope.owner_thread is not threading.current_thread()
        or active_scope.lifecycle != "ACTIVE"
        or active_scope.reservation_committed is not False
        or type(terminal_authority.coordinate)
        is not DurableCausalOrderCoordinateV1
    ):
        raise ValueError("durable_causal_subject_mismatch")
    try:
        _sha256(controller_identity_sha256, "controller_identity_sha256")
        _sha256(pending_sha256, "pending_sha256")
        _exact_nonnegative_integer(publication_epoch, "publication_epoch")
    except (TypeError, ValueError):
        raise ValueError("durable_causal_subject_mismatch") from None
    parent_authority = _lookup_envelope_authority(parent)
    coordinate = terminal_authority.coordinate
    coordinate_authority = _lookup_coordinate_authority(coordinate)
    if (
        type(parent_authority) is not _EnvelopeAuthorityV1
        or parent_authority.kind != "PARENT"
        or parent_authority.lifecycle != _ENVELOPE_CONSUMED
        or parent_authority.ingress is not ingress
        or parent_authority.runtime is not terminal_authority.runtime
        or parent_authority.consumer is not controller
        or parent_authority.session_id != terminal_authority.session_id
        or not _envelope_public_fields_valid_v1(parent, parent_authority)
        or type(coordinate_authority) is not _CoordinateAuthorityV1
        or coordinate_authority.ingress is not ingress
        or coordinate_authority.runtime is not terminal_authority.runtime
        or coordinate_authority.stage != _COORDINATE_TERMINAL_STAGE
        or coordinate_authority.subject is not terminal_authority.terminal
        or coordinate_authority.subject_sha256
        != terminal.terminal_record_sha256
        or not _coordinate_public_fields_valid_v1(
            coordinate,
            coordinate_authority,
        )
        or terminal.session_id != parent_authority.session_id
        or getpid() != ingress._owner_pid
        or threading.current_thread() is not ingress._owner_thread
    ):
        raise ValueError("durable_causal_subject_mismatch")
    projection = {
        "schema_version": 1,
        "session_id": terminal.session_id,
        "controller_identity_sha256": controller_identity_sha256,
        "pending_sha256": pending_sha256,
        "durable_parent_envelope_sha256": parent.envelope_sha256,
        "evidence_terminal_sha256": terminal.envelope_sha256,
        "evidence_terminal_coordinate_sha256": (
            terminal.evidence_terminal_coordinate_sha256
        ),
    }
    subject_sha256 = _domain_sha256(
        "INCI-DEFERRED-EMERGENCY-COMMIT-SUBJECT-V1",
        projection,
    )
    try:
        with publication_lock:
            with ingress._causal_subject_lock:
                current = _resolve_deferred_emergency_subject_inputs_v1(
                    controller,
                    pending,
                )
                if (
                    type(current) is not tuple
                    or len(current) != 7
                    or current[0] is not ingress
                    or current[1] is not parent
                    or current[2] != controller_identity_sha256
                    or current[3] != pending_sha256
                    or current[4] != publication_epoch
                    or current[5] is not publication_lock
                    or current[6] is not active_scope
                ):
                    raise ValueError("durable_causal_subject_mismatch")
                locked_terminal_authority = _lookup_envelope_authority(
                    terminal
                )
                locked_parent_authority = _lookup_envelope_authority(parent)
                locked_coordinate_authority = _lookup_coordinate_authority(
                    coordinate
                )
                locked_projection = {
                    "schema_version": 1,
                    "session_id": terminal.session_id,
                    "controller_identity_sha256": controller_identity_sha256,
                    "pending_sha256": pending_sha256,
                    "durable_parent_envelope_sha256": parent.envelope_sha256,
                    "evidence_terminal_sha256": terminal.envelope_sha256,
                    "evidence_terminal_coordinate_sha256": (
                        terminal.evidence_terminal_coordinate_sha256
                    ),
                }
                if (
                    locked_terminal_authority is not terminal_authority
                    or locked_parent_authority is not parent_authority
                    or locked_coordinate_authority is not coordinate_authority
                    or terminal_authority.lifecycle != _ENVELOPE_ISSUED
                    or parent_authority.lifecycle != _ENVELOPE_CONSUMED
                    or not _envelope_public_fields_valid_v1(
                        terminal,
                        terminal_authority,
                    )
                    or not _envelope_public_fields_valid_v1(
                        parent,
                        parent_authority,
                    )
                    or not _coordinate_public_fields_valid_v1(
                        coordinate,
                        coordinate_authority,
                    )
                    or active_scope.controller is not controller
                    or active_scope.pending is not pending
                    or type(active_scope.pending_authority)
                    is not _PendingDurableCompanionEmergencyAuthorityV1
                    or active_scope.terminal is not terminal
                    or active_scope.publication_lock is not publication_lock
                    or active_scope.publication_epoch != publication_epoch
                    or active_scope.owner_pid != getpid()
                    or active_scope.owner_thread
                    is not threading.current_thread()
                    or active_scope.lifecycle != "ACTIVE"
                    or active_scope.reservation_committed is not False
                    or locked_projection != projection
                    or _domain_sha256(
                        "INCI-DEFERRED-EMERGENCY-COMMIT-SUBJECT-V1",
                        locked_projection,
                    )
                    != subject_sha256
                ):
                    raise ValueError("durable_causal_subject_mismatch")
                existing_entry = _lookup_deferred_subject_index_entry_v1(
                    ingress,
                    pending,
                )
                if existing_entry is not None:
                    existing_subject = existing_entry.subject_reference()
                    existing_authority = _lookup_subject_authority(
                        existing_subject
                    )
                    if not (
                        type(existing_subject)
                        is DeferredEmergencyCommitSubjectV1
                        and existing_entry.lifecycle == "LIVE"
                        and existing_entry.terminal_reference() is terminal
                        and type(existing_authority) is _SubjectAuthorityV1
                        and existing_authority.ingress is ingress
                        and existing_authority.runtime
                        is terminal_authority.runtime
                        and existing_authority.controller is controller
                        and existing_authority.pending is pending
                        and existing_authority.terminal is terminal
                        and existing_authority.parent is parent
                        and existing_authority.coordinate is coordinate
                        and existing_authority.publication_epoch
                        == publication_epoch
                        and existing_authority.publication_lock
                        is publication_lock
                        and existing_authority.lifecycle == "FRESH"
                        and _subject_public_fields_valid_v1(
                            existing_subject,
                            existing_authority,
                        )
                    ):
                        raise ValueError("durable_causal_subject_mismatch")
                    prior_scope = existing_authority.completion_scope
                    snapshot = existing_authority.issuance
                    if type(snapshot) is not _SubjectIssuanceSnapshotV1:
                        raise ValueError("durable_causal_subject_mismatch")
                    immutable_scope_bindings_match = (
                        type(prior_scope)
                        is _DeferredEmergencyCompletionScopeV1
                        and prior_scope.controller is snapshot.controller
                        and prior_scope.pending is snapshot.pending
                        and prior_scope.pending_authority
                        is snapshot.completion_pending_authority
                        and prior_scope.terminal is snapshot.terminal
                        and prior_scope.publication_lock
                        is snapshot.publication_lock
                        and prior_scope.publication_epoch
                        == snapshot.publication_epoch
                        and prior_scope.owner_pid == snapshot.owner_pid
                        and prior_scope.owner_thread
                        is snapshot.owner_thread
                        and active_scope.controller is snapshot.controller
                        and active_scope.pending is snapshot.pending
                        and active_scope.pending_authority
                        is snapshot.completion_pending_authority
                        and active_scope.terminal is snapshot.terminal
                        and active_scope.publication_lock
                        is snapshot.publication_lock
                        and active_scope.publication_epoch
                        == snapshot.publication_epoch
                        and active_scope.owner_pid == snapshot.owner_pid
                        and active_scope.owner_thread
                        is snapshot.owner_thread
                    )
                    decision = _deferred_subject_repeat_kernel_v1(
                        _DeferredSubjectRepeatKernelInputV1(
                            has_existing_subject=True,
                            subject_lifecycle=existing_authority.lifecycle,
                            immutable_bindings_match=(
                                immutable_scope_bindings_match
                            ),
                            same_scope=prior_scope is active_scope,
                            prior_scope_lifecycle=prior_scope.lifecycle,
                            prior_scope_reserved=(
                                prior_scope.reservation_committed
                            ),
                            prior_scope_subject_matches=(
                                prior_scope.subject
                                in (None, existing_subject)
                            ),
                            candidate_scope_lifecycle=(
                                active_scope.lifecycle
                            ),
                            candidate_scope_reserved=(
                                active_scope.reservation_committed
                            ),
                            candidate_scope_subject_clear_or_same=(
                                active_scope.subject
                                in (None, existing_subject)
                            ),
                        )
                    )
                    if decision.action == "REBIND_RETURN_SAME":
                        existing_authority.completion_scope = active_scope
                    return existing_subject

                decision = _deferred_subject_repeat_kernel_v1(
                    _DeferredSubjectRepeatKernelInputV1(
                        has_existing_subject=False,
                        subject_lifecycle="ABSENT",
                        immutable_bindings_match=True,
                        same_scope=True,
                        prior_scope_lifecycle=active_scope.lifecycle,
                        prior_scope_reserved=(
                            active_scope.reservation_committed
                        ),
                        prior_scope_subject_matches=(
                            active_scope.subject is None
                        ),
                        candidate_scope_lifecycle=active_scope.lifecycle,
                        candidate_scope_reserved=(
                            active_scope.reservation_committed
                        ),
                        candidate_scope_subject_clear_or_same=(
                            active_scope.subject is None
                        ),
                    )
                )
                if decision.action != "ISSUE":
                    raise ValueError("durable_causal_subject_mismatch")
                subject = _build_opaque_value(
                    DeferredEmergencyCommitSubjectV1,
                    {
                        **projection,
                        "subject_sha256": subject_sha256,
                    },
                )
                issuance = _SubjectIssuanceSnapshotV1(
                    schema_version=1,
                    session_id=terminal.session_id,
                    controller_identity_sha256=controller_identity_sha256,
                    pending_sha256=pending_sha256,
                    durable_parent_envelope_sha256=parent.envelope_sha256,
                    evidence_terminal_sha256=terminal.envelope_sha256,
                    evidence_terminal_coordinate_sha256=(
                        terminal.evidence_terminal_coordinate_sha256
                    ),
                    subject_sha256=subject_sha256,
                    ingress=ingress,
                    runtime=terminal_authority.runtime,
                    ingress_identity_sha256=(
                        ingress._ingress_identity_sha256
                    ),
                    runtime_identity_sha256=_runtime_identity_sha256_v1(
                        ingress,
                        terminal_authority.runtime,
                        terminal.session_id,
                    ),
                    controller=controller,
                    pending=pending,
                    parent=parent,
                    terminal=terminal,
                    coordinate=coordinate,
                    publication_lock=publication_lock,
                    completion_pending_authority=(
                        active_scope.pending_authority
                    ),
                    publication_epoch=publication_epoch,
                    owner_pid=getpid(),
                    owner_thread=threading.current_thread(),
                )
                authority = _SubjectAuthorityV1(
                    ingress=ingress,
                    runtime=terminal_authority.runtime,
                    controller=controller,
                    pending=pending,
                    terminal=terminal,
                    parent=parent,
                    coordinate=coordinate,
                    session_id=terminal.session_id,
                    controller_identity_sha256=controller_identity_sha256,
                    pending_sha256=pending_sha256,
                    owner_pid=getpid(),
                    owner_thread=threading.current_thread(),
                    publication_epoch=publication_epoch,
                    publication_lock=publication_lock,
                    completion_scope=active_scope,
                    issuance=issuance,
                )
                try:
                    _register_subject_authority(subject, authority)
                    _register_deferred_subject_index_entry_v1(
                        ingress,
                        pending,
                        subject,
                        terminal,
                    )
                except BaseException:
                    _unregister_subject_authority(subject, authority)
                    raise
                return subject
    except (TypeError, ValueError):
        raise
    except Exception:
        raise ValueError("durable_causal_subject_mismatch") from None


def _abort_deferred_emergency_commit_subject_v1(
    subject: DeferredEmergencyCommitSubjectV1 | None,
    controller: object,
    pending: object,
    terminal: DurableEvidenceTerminalV1 | None,
) -> None:
    if subject is None and terminal is None:
        return None
    if subject is not None and type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    if terminal is not None and type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    if subject is not None and terminal is None:
        raise ValueError("durable_causal_subject_mismatch")
    try:
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            ExpertControllerV1,
            PendingDurableCompanionEmergencyV1,
            _resolve_deferred_emergency_pending_for_ingress_commit_v1,
            _resolve_deferred_emergency_subject_inputs_v1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_subject_mismatch") from None
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(pending) is not PendingDurableCompanionEmergencyV1:
        raise TypeError("exact PendingDurableCompanionEmergencyV1 required")
    terminal_authority = _lookup_envelope_authority(terminal)
    if type(terminal_authority) is not _EnvelopeAuthorityV1:
        raise ValueError("durable_causal_subject_mismatch")
    if terminal_authority.lifecycle != _ENVELOPE_ISSUED:
        if terminal_authority.lifecycle == _ENVELOPE_CONSUMED:
            raise ValueError("durable_causal_subject_consumed")
        raise ValueError("durable_causal_subject_mismatch")
    subject_authority = (
        None if subject is None else _lookup_subject_authority(subject)
    )
    if subject is not None and type(subject_authority) is not _SubjectAuthorityV1:
        raise ValueError("durable_causal_subject_mismatch")
    try:
        resolved = _resolve_deferred_emergency_subject_inputs_v1(
            controller,
            pending,
        )
    except Exception:
        raise ValueError("durable_causal_subject_mismatch") from None
    if type(resolved) is not tuple or len(resolved) != 7:
        raise ValueError("durable_causal_subject_mismatch")
    ingress, parent, _, _, publication_epoch, publication_lock, completion_scope = (
        resolved
    )
    publication_owned = getattr(publication_lock, "_is_owned", None)
    if (
        type(ingress) is not BoundedIngress
        or type(completion_scope) is not _DeferredEmergencyCompletionScopeV1
        or type(completion_scope.pending_authority)
        is not _PendingDurableCompanionEmergencyAuthorityV1
        or completion_scope.controller is not controller
        or completion_scope.pending is not pending
        or completion_scope.terminal is not terminal
        or completion_scope.publication_lock is not publication_lock
        or completion_scope.publication_epoch != publication_epoch
        or completion_scope.lifecycle != "CLEARED"
        or completion_scope.reservation_committed is not False
        or terminal_authority.ingress is not ingress
        or terminal_authority.runtime is not ingress._runtime
        or terminal_authority.consumer is not controller
        or publication_owned is None
        or publication_owned() is not True
    ):
        raise ValueError("durable_causal_subject_mismatch")
    index_entry = None
    if subject is not None:
        index_entry = _lookup_deferred_subject_index_entry_v1(ingress, pending)
        if (
            subject_authority.ingress is not ingress
            or subject_authority.controller is not controller
            or subject_authority.pending is not pending
            or subject_authority.terminal is not terminal
            or subject_authority.completion_scope is not completion_scope
            or subject_authority.publication_lock is not publication_lock
            or subject_authority.publication_epoch != publication_epoch
            or subject_authority.lifecycle != "FRESH"
            or type(index_entry) is not _DeferredSubjectIndexEntryV1
            or index_entry.subject_reference() is not subject
            or index_entry.terminal_reference() is not terminal
            or index_entry.lifecycle != "LIVE"
        ):
            raise ValueError("durable_causal_subject_mismatch")
    try:
        with ingress._causal_subject_lock:
            current = _resolve_deferred_emergency_subject_inputs_v1(
                controller,
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
                or _lookup_envelope_authority(terminal)
                is not terminal_authority
                or terminal_authority.lifecycle != _ENVELOPE_ISSUED
                or terminal_authority.live_envelope is not terminal
            ):
                raise ValueError("durable_causal_subject_mismatch")
            if subject is not None:
                pending_resolution = (
                    _resolve_deferred_emergency_pending_for_ingress_commit_v1(
                        controller,
                        pending,
                        subject,
                    )
                )
                if (
                    type(pending_resolution) is not tuple
                    or len(pending_resolution) != 2
                    or pending_resolution[0]
                    is not completion_scope.pending_authority
                    or pending_resolution[1] is not completion_scope
                    or _lookup_subject_authority(subject)
                    is not subject_authority
                    or _lookup_deferred_subject_index_entry_v1(
                        ingress,
                        pending,
                    )
                    is not index_entry
                ):
                    raise ValueError("durable_causal_subject_mismatch")
                if not _unregister_subject_authority(
                    subject,
                    subject_authority,
                ):
                    raise ValueError("durable_causal_subject_mismatch")
            terminal_authority.lifecycle = _ENVELOPE_CONSUMED
            terminal_authority.live_envelope = None
            if subject is not None:
                subject_authority.lifecycle = "ABORTED_CLOSED"
                index_entry.lifecycle = "ABORTED_CLOSED"
        if publication_owned() is not True:
            raise RuntimeError("publication lock ownership lost")
    except (TypeError, ValueError):
        raise
    except Exception:
        raise RuntimeError("durable_causal_subject_abort_uncertain") from None


def _proof_public_fields_valid_v1(
    proof: DurableCausalPrecedesProofV1,
    authority: _ProofAuthorityV1,
    *,
    allowed_terminal_lifecycles: tuple[str, ...] = (_ENVELOPE_ISSUED,),
) -> bool:
    try:
        snapshot = authority.issuance
        if type(snapshot) is not _ProofIssuanceSnapshotV1:
            return False
        before_authority = _lookup_coordinate_authority(authority.before)
        after_authority = _lookup_coordinate_authority(authority.after)
        subject_authority = _lookup_subject_authority(authority.subject)
        if (
            type(subject_authority) is not _SubjectAuthorityV1
            and authority.prepared_commit is not None
        ):
            prepared = authority.prepared_commit()
            if (
                type(prepared)
                is _PreparedDeferredEmergencyCausalProofCommitV1
            ):
                subject_authority = prepared.subject_authority
        projection = {
            "schema_version": proof.schema_version,
            "session_id": proof.session_id,
            "before_coordinate_sha256": proof.before_coordinate_sha256,
            "after_coordinate_sha256": proof.after_coordinate_sha256,
        }
        return (
            authority.proof_reference is not None
            and authority.proof_reference() is proof
            and type(proof.schema_version) is int
            and proof.schema_version == snapshot.schema_version == 1
            and proof.session_id == snapshot.session_id
            and proof.before_coordinate_sha256
            == snapshot.before_coordinate_sha256
            == authority.before.coordinate_sha256
            and proof.after_coordinate_sha256
            == snapshot.after_coordinate_sha256
            == authority.after.coordinate_sha256
            and proof.proof_sha256
            == snapshot.proof_sha256
            == _domain_sha256(
                "INCI-DURABLE-CAUSAL-PRECEDES-PROOF-V1",
                projection,
            )
            and authority.claim is snapshot.claim
            and authority.subject is snapshot.subject
            and authority.pending is snapshot.pending
            and authority.terminal is snapshot.terminal
            and authority.before is snapshot.before
            and authority.after is snapshot.after
            and authority.owner_pid == snapshot.owner_pid == getpid()
            and authority.owner_thread
            is snapshot.owner_thread
            is threading.current_thread()
            and type(before_authority) is _CoordinateAuthorityV1
            and type(after_authority) is _CoordinateAuthorityV1
            and type(subject_authority) is _SubjectAuthorityV1
            and subject_authority.pending is authority.pending
            and subject_authority.terminal is authority.terminal
            and subject_authority.coordinate is authority.after
            and _coordinate_public_fields_valid_v1(
                authority.before,
                before_authority,
            )
            and _coordinate_public_fields_valid_v1(
                authority.after,
                after_authority,
            )
            and _subject_public_fields_valid_v1(
                authority.subject,
                subject_authority,
                allowed_terminal_lifecycles=(
                    allowed_terminal_lifecycles
                ),
            )
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _production_deferred_commit_kernel_input_v1(
    *,
    claim: object,
    claim_authority: object,
    before: DurableCausalOrderCoordinateV1,
    before_authority: _CoordinateAuthorityV1,
    subject: DeferredEmergencyCommitSubjectV1,
    subject_authority: _SubjectAuthorityV1,
    after: DurableCausalOrderCoordinateV1,
    after_authority: _CoordinateAuthorityV1,
    pending_authority: object,
    completion_scope: object,
    proof_lifecycle: str,
) -> _DeferredCommitKernelInputV1:
    try:
        terminal_authority = _lookup_envelope_authority(
            subject_authority.terminal
        )
        if (
            subject_authority.lifecycle != "FRESH"
            or not _subject_public_fields_valid_v1(
                subject,
                subject_authority,
            )
            or not _coordinate_public_fields_valid_v1(
                before,
                before_authority,
            )
            or not _coordinate_public_fields_valid_v1(
                after,
                after_authority,
            )
            or pending_authority.reserved_claim is not None
            or pending_authority.reserved_subject is not None
            or pending_authority.reserved_terminal is not None
            or pending_authority.reserved_causal_proof is not None
            or pending_authority.reserved_completion_scope is not None
            or completion_scope.reservation_committed is not False
            or completion_scope.causal_proof is not None
            or completion_scope.source_close_claim is not claim
            or completion_scope.subject is not subject
            or completion_scope.terminal is not subject_authority.terminal
            or completion_scope.pending_authority is not pending_authority
            or completion_scope.publication_lock
            is not subject_authority.publication_lock
            or completion_scope.owner_pid != subject_authority.owner_pid
            or completion_scope.owner_thread
            is not subject_authority.owner_thread
            or type(terminal_authority) is not _EnvelopeAuthorityV1
            or terminal_authority.lifecycle != _ENVELOPE_ISSUED
            or terminal_authority.consumer is not subject_authority.controller
            or terminal_authority.session_id != subject_authority.session_id
            or not _envelope_public_fields_valid_v1(
                subject_authority.terminal,
                terminal_authority,
            )
        ):
            raise ValueError("durable_causal_subject_mismatch")
        return _DeferredCommitKernelInputV1(
            claim_lifecycle=claim_authority.lifecycle,
            subject_lifecycle=subject_authority.lifecycle,
            pending_lifecycle=pending_authority.lifecycle,
            proof_lifecycle=proof_lifecycle,
            completion_scope_lifecycle=completion_scope.lifecycle,
            same_session=(
                before.session_id
                == after.session_id
                == subject_authority.session_id
                == pending_authority.session_id
                == subject_authority.terminal.session_id
            ),
            same_ingress=(
                before_authority.ingress
                is after_authority.ingress
                is subject_authority.ingress
                is pending_authority.ingress
            ),
            same_runtime=(
                before_authority.runtime
                is after_authority.runtime
                is subject_authority.runtime
                is pending_authority.runtime
            ),
            same_controller=(
                pending_authority.controller
                is subject_authority.controller
                is completion_scope.controller
            ),
            same_pending_parent=(
                pending_authority.pending is subject_authority.pending
                and completion_scope.pending is subject_authority.pending
                and completion_scope.pending_authority
                is pending_authority
                and pending_authority.parent is subject_authority.parent
            ),
            same_publication_epoch=(
                pending_authority.publication_epoch
                == subject_authority.publication_epoch
                == completion_scope.publication_epoch
            ),
            same_owner=(
                before_authority.owner_pid
                == after_authority.owner_pid
                == subject_authority.owner_pid
                == pending_authority.owner_pid
                == completion_scope.owner_pid
                == getpid()
                and before_authority.owner_thread
                is after_authority.owner_thread
                is subject_authority.owner_thread
                is pending_authority.owner_thread
                is completion_scope.owner_thread
                is threading.current_thread()
            ),
            before_stage=before.stage,
            after_stage=after.stage,
            before_ordinal=before.ordinal,
            after_ordinal=after.ordinal,
        )
    except (AttributeError, TypeError):
        raise ValueError("durable_causal_subject_mismatch") from None


def _issue_durable_causal_precedes_for_deferred_commit_v1(
    *,
    claim: object,
    subject: DeferredEmergencyCommitSubjectV1,
) -> DurableCausalPrecedesProofV1:
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    subject_authority = _lookup_subject_authority(subject)
    subject_snapshot = (
        None
        if type(subject_authority) is not _SubjectAuthorityV1
        else subject_authority.issuance
    )
    if (
        type(subject_authority) is not _SubjectAuthorityV1
        or type(subject_snapshot) is not _SubjectIssuanceSnapshotV1
        or subject_authority.lifecycle != "FRESH"
        or subject_authority.publication_lock
        is not subject_snapshot.publication_lock
        or subject_authority.ingress is not subject_snapshot.ingress
        or subject_authority.runtime is not subject_snapshot.runtime
        or not _subject_public_fields_valid_v1(subject, subject_authority)
        or getpid() != subject_authority.owner_pid
        or threading.current_thread() is not subject_authority.owner_thread
    ):
        raise ValueError("durable_causal_subject_mismatch")
    try:
        from inci_tennis_runtime.shadow_sources import (
            _DEFERRED_EMERGENCY_CLAIM_COMMIT_LOCK_V1,
            _DeferredEmergencySourceCloseClaimAuthorityV1,
            DeferredEmergencySourceCloseClaimV1,
            _resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1,
        )
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            _resolve_deferred_emergency_pending_for_ingress_commit_v1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_subject_mismatch") from None
    if type(claim) is not DeferredEmergencySourceCloseClaimV1:
        raise TypeError("exact DeferredEmergencySourceCloseClaimV1 required")
    try:
        claim_resolution = (
            _resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1(
                claim
            )
        )
        if type(claim_resolution) is not tuple or len(claim_resolution) != 2:
            raise ValueError("claim_resolution_invalid")
        claim_authority, before = claim_resolution
    except Exception:
        raise ValueError("durable_causal_subject_mismatch") from None


    after = subject_authority.coordinate
    before_authority = _lookup_coordinate_authority(before)
    after_authority = _lookup_coordinate_authority(after)
    if (
        type(claim_authority)
        is not _DeferredEmergencySourceCloseClaimAuthorityV1
        or
        type(before) is not DurableCausalOrderCoordinateV1
        or type(before_authority) is not _CoordinateAuthorityV1
        or type(after_authority) is not _CoordinateAuthorityV1
        or before_authority.ingress is not subject_authority.ingress
        or after_authority.ingress is not subject_authority.ingress
        or before.stage != _COORDINATE_SOURCE_CLOSE_STAGE
        or after.stage != _COORDINATE_TERMINAL_STAGE
        or not _coordinate_public_fields_valid_v1(before, before_authority)
        or not _coordinate_public_fields_valid_v1(after, after_authority)
    ):
        raise ValueError("durable_causal_order_invalid")
    publication_lock = subject_snapshot.publication_lock
    ingress = subject_snapshot.ingress
    try:
        with publication_lock:
            if (
                _lookup_subject_authority(subject) is not subject_authority
                or subject_authority.issuance is not subject_snapshot
                or subject_authority.publication_lock is not publication_lock
                or subject_authority.ingress is not ingress
                or not _subject_public_fields_valid_v1(
                    subject,
                    subject_authority,
                )
            ):
                raise ValueError("durable_causal_subject_mismatch")
            with ingress._causal_subject_lock:
                with _DEFERRED_EMERGENCY_CLAIM_COMMIT_LOCK_V1:
                    current_subject_authority = _lookup_subject_authority(
                        subject
                    )
                    current_claim_resolution = (
                        _resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1(
                            claim
                        )
                    )
                    pending_resolution = (
                        _resolve_deferred_emergency_pending_for_ingress_commit_v1(
                            subject_authority.controller,
                            subject_authority.pending,
                            subject,
                        )
                    )
                    if (
                        current_subject_authority is not subject_authority
                        or type(current_claim_resolution) is not tuple
                        or len(current_claim_resolution) != 2
                        or current_claim_resolution[0] is not claim_authority
                        or current_claim_resolution[1] is not before
                        or type(pending_resolution) is not tuple
                        or len(pending_resolution) != 2
                    ):
                        raise ValueError("durable_causal_subject_mismatch")
                    pending_authority, completion_scope = pending_resolution
                    if (
                        type(pending_authority)
                        is not _PendingDurableCompanionEmergencyAuthorityV1
                        or type(completion_scope)
                        is not _DeferredEmergencyCompletionScopeV1
                        or subject_authority.completion_scope
                        is not completion_scope
                    ):
                        raise ValueError("durable_causal_subject_mismatch")
                    kernel_input = _production_deferred_commit_kernel_input_v1(
                        claim=claim,
                        claim_authority=claim_authority,
                        before=before,
                        before_authority=before_authority,
                        subject=subject,
                        subject_authority=subject_authority,
                        after=after,
                        after_authority=after_authority,
                        pending_authority=pending_authority,
                        completion_scope=completion_scope,
                        proof_lifecycle="PREPARED",
                    )
                    result = _deferred_commit_transition_kernel_v1(kernel_input)
                    projection = {
                        "schema_version": 1,
                        "session_id": subject_authority.session_id,
                        "before_coordinate_sha256": (
                            before.coordinate_sha256
                        ),
                        "after_coordinate_sha256": after.coordinate_sha256,
                    }
                    proof_sha256 = _domain_sha256(
                        "INCI-DURABLE-CAUSAL-PRECEDES-PROOF-V1",
                        projection,
                    )
                    proof = _build_opaque_value(
                        DurableCausalPrecedesProofV1,
                        {
                            **projection,
                            "proof_sha256": proof_sha256,
                        },
                    )
                    proof_issuance = _ProofIssuanceSnapshotV1(
                        schema_version=1,
                        session_id=subject_authority.session_id,
                        before_coordinate_sha256=before.coordinate_sha256,
                        after_coordinate_sha256=after.coordinate_sha256,
                        proof_sha256=proof_sha256,
                        claim=claim,
                        subject=subject,
                        pending=subject_authority.pending,
                        terminal=subject_authority.terminal,
                        before=before,
                        after=after,
                        owner_pid=getpid(),
                        owner_thread=threading.current_thread(),
                    )
                    proof_authority = _ProofAuthorityV1(
                        before=before,
                        after=after,
                        subject=subject,
                        claim=claim,
                        pending=subject_authority.pending,
                        terminal=subject_authority.terminal,
                        owner_pid=getpid(),
                        owner_thread=threading.current_thread(),
                        issuance=proof_issuance,
                    )
                    terminal_authority = _lookup_envelope_authority(
                        subject_authority.terminal
                    )
                    if type(terminal_authority) is not _EnvelopeAuthorityV1:
                        raise ValueError("durable_causal_subject_mismatch")
                    prepared = (
                        _build_prepared_deferred_emergency_causal_proof_commit_v1(
                            ingress=ingress,
                            controller=subject_authority.controller,
                            pending_authority=pending_authority,
                            completion_scope=completion_scope,
                            proof_authority=proof_authority,
                            subject_authority=subject_authority,
                            terminal_authority=terminal_authority,
                            publication_lock=publication_lock,
                        )
                    )
                    try:
                        _register_proof_authority(proof, proof_authority)
                        _register_prepared_deferred_emergency_causal_proof_commit_v1(
                            proof,
                            prepared,
                        )
                        object.__setattr__(
                            proof,
                            "_proof_authority",
                            proof_authority,
                        )
                        object.__setattr__(
                            proof,
                            "_prepared_commit",
                            prepared,
                        )
                        try:
                            proof._proof_authority
                        except AttributeError:
                            pass
                        else:
                            raise RuntimeError(
                                "durable causal proof internals exposed"
                            )
                    except BaseException:
                        object.__setattr__(proof, "_proof_authority", None)
                        object.__setattr__(proof, "_prepared_commit", None)
                        _unregister_prepared_deferred_emergency_causal_proof_commit_v1(
                            proof,
                            prepared,
                        )
                        _unregister_proof_authority(proof, proof_authority)
                        raise
                    try:
                        repeated_claim_resolution = (
                            _resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1(
                                claim
                            )
                        )
                        repeated_pending_resolution = (
                            _resolve_deferred_emergency_pending_for_ingress_commit_v1(
                                subject_authority.controller,
                                subject_authority.pending,
                                subject,
                            )
                        )
                        if (
                            _lookup_subject_authority(subject)
                            is not subject_authority
                            or _lookup_proof_authority(proof)
                            is not proof_authority
                            or _lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1(
                                proof
                            )
                            is not prepared
                            or type(repeated_claim_resolution) is not tuple
                            or len(repeated_claim_resolution) != 2
                            or repeated_claim_resolution[0]
                            is not claim_authority
                            or repeated_claim_resolution[1] is not before
                            or type(repeated_pending_resolution) is not tuple
                            or len(repeated_pending_resolution) != 2
                            or repeated_pending_resolution[0]
                            is not pending_authority
                            or repeated_pending_resolution[1]
                            is not completion_scope
                            or not _proof_public_fields_valid_v1(
                                proof,
                                proof_authority,
                            )
                        ):
                            raise ValueError(
                                "durable_causal_subject_mismatch"
                            )
                        prepared_registry_cell = (
                            _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(
                                id(prepared)
                            )
                        )
                        if (
                            type(prepared_registry_cell)
                            is not _PreparedDeferredCommitRegistryCellV1
                            or prepared_registry_cell.prepared_reference()
                            is not prepared
                            or prepared_registry_cell.proof_reference()
                            is not proof
                            or prepared_registry_cell.lifecycle != "PROVISIONAL"
                            or not _prepared_deferred_commit_registry_bindings_valid_v1(
                                prepared,
                                prepared_registry_cell,
                            )
                        ):
                            raise ValueError(
                                "durable_causal_subject_mismatch"
                            )
                        kernel_input = (
                            _production_deferred_commit_kernel_input_v1(
                                claim=claim,
                                claim_authority=claim_authority,
                                before=before,
                                before_authority=before_authority,
                                subject=subject,
                                subject_authority=subject_authority,
                                after=after,
                                after_authority=after_authority,
                                pending_authority=pending_authority,
                                completion_scope=completion_scope,
                                proof_lifecycle=proof_authority.lifecycle,
                            )
                        )
                        result = _deferred_commit_transition_kernel_v1(
                            kernel_input
                        )
                    except BaseException:
                        object.__setattr__(proof, "_proof_authority", None)
                        object.__setattr__(proof, "_prepared_commit", None)
                        _unregister_prepared_deferred_emergency_causal_proof_commit_v1(
                            proof,
                            prepared,
                        )
                        _unregister_proof_authority(
                            proof,
                            proof_authority,
                        )
                        raise

                    if (
                        not _unregister_subject_authority(
                            subject,
                            subject_authority,
                        )
                        or not _unregister_proof_authority_registry_v1(
                            proof,
                            proof_authority,
                        )
                    ):
                        raise ValueError("durable_causal_subject_mismatch")
                    _close_deferred_subject_index_entry_v1(
                        ingress,
                        subject_authority.pending,
                        subject,
                        subject_authority.terminal,
                        lifecycle="CONSUMED",
                    )
                    claim_authority.lifecycle = result.claim_lifecycle
                    subject_authority.lifecycle = result.subject_lifecycle
                    pending_authority.lifecycle = result.pending_lifecycle
                    pending_authority.reserved_claim = claim
                    pending_authority.reserved_subject = subject
                    pending_authority.reserved_terminal = (
                        subject_authority.terminal
                    )
                    pending_authority.reserved_causal_proof = proof
                    pending_authority.reserved_completion_scope = (
                        completion_scope
                    )
                    completion_scope.lifecycle = (
                        result.completion_scope_lifecycle
                    )
                    completion_scope.reservation_committed = True
                    completion_scope.causal_proof = proof
                    proof_authority.lifecycle = result.proof_lifecycle
                    prepared.lifecycle = "PREPARED"
                    prepared.success_armed = False
                    prepared_registry_cell.lifecycle = "LIVE"
                    return proof
    except (TypeError, ValueError):
        raise
    except Exception:
        raise ValueError("durable_causal_subject_mismatch") from None


def _finalize_durable_causal_precedes_proof_v1(
    proof: DurableCausalPrecedesProofV1,
    *,
    subject: DeferredEmergencyCommitSubjectV1,
    controller: object,
    pending: object,
    terminal: DurableEvidenceTerminalV1,
    append_succeeded: bool,
) -> None:
    if type(proof) is not DurableCausalPrecedesProofV1:
        raise TypeError("exact DurableCausalPrecedesProofV1 required")
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    if type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    if type(append_succeeded) is not bool:
        raise TypeError("exact bool append_succeeded required")
    try:
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            ExpertControllerV1,
            PendingDurableCompanionEmergencyV1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_subject_mismatch") from None

    proof_authority = _lookup_proof_authority(proof)
    subject_authority = _lookup_subject_authority(subject)
    terminal_authority = _lookup_envelope_authority(terminal)
    proof_snapshot = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else proof_authority.issuance
    )
    subject_snapshot = (
        None
        if type(subject_authority) is not _SubjectAuthorityV1
        else subject_authority.issuance
    )
    if (
        type(proof_authority) is not _ProofAuthorityV1
        or type(subject_authority) is not _SubjectAuthorityV1
        or type(terminal_authority) is not _EnvelopeAuthorityV1
        or type(proof_snapshot) is not _ProofIssuanceSnapshotV1
        or type(subject_snapshot) is not _SubjectIssuanceSnapshotV1
    ):
        raise ValueError("durable_causal_subject_mismatch")
    publication_lock = subject_snapshot.publication_lock
    ingress = subject_snapshot.ingress
    allowed_terminal_lifecycles = (
        (_ENVELOPE_CONSUMED,)
        if append_succeeded
        else (_ENVELOPE_ISSUED, _ENVELOPE_CONSUMED)
    )
    try:
        with publication_lock:
            with ingress._causal_subject_lock:
                current_proof_authority = _lookup_proof_authority(proof)
                current_subject_authority = _lookup_subject_authority(subject)
                current_terminal_authority = _lookup_envelope_authority(
                    terminal
                )
                completion_scope = subject_authority.completion_scope
                pending_authority = completion_scope.pending_authority
                exact_bindings = (
                    current_proof_authority is proof_authority
                    and current_subject_authority is subject_authority
                    and current_terminal_authority is terminal_authority
                    and proof_authority.issuance is proof_snapshot
                    and subject_authority.issuance is subject_snapshot
                    and type(controller) is ExpertControllerV1
                    and type(pending) is PendingDurableCompanionEmergencyV1
                    and type(completion_scope)
                    is _DeferredEmergencyCompletionScopeV1
                    and type(pending_authority)
                    is _PendingDurableCompanionEmergencyAuthorityV1
                    and proof_authority.subject is subject
                    and proof_authority.pending is pending
                    and proof_authority.terminal is terminal
                    and proof_snapshot.subject is subject
                    and proof_snapshot.pending is pending
                    and proof_snapshot.terminal is terminal
                    and subject_authority.controller is controller
                    and subject_authority.pending is pending
                    and subject_authority.terminal is terminal
                    and subject_snapshot.controller is controller
                    and subject_snapshot.pending is pending
                    and subject_snapshot.terminal is terminal
                    and subject_snapshot.publication_lock
                    is publication_lock
                    and subject_snapshot.completion_pending_authority
                    is pending_authority
                    and subject_authority.publication_lock
                    is publication_lock
                    and subject_authority.ingress is ingress
                    and subject_authority.runtime is subject_snapshot.runtime
                    and subject_authority.completion_scope
                    is completion_scope
                    and pending_authority.controller is controller
                    and pending_authority.pending is pending
                    and pending_authority.parent
                    is subject_authority.parent
                    and pending_authority.ingress is ingress
                    and pending_authority.runtime
                    is subject_authority.runtime
                    and pending_authority.session_id
                    == subject_authority.session_id
                    and pending_authority.publication_epoch
                    == subject_authority.publication_epoch
                    and completion_scope.controller is controller
                    and completion_scope.pending is pending
                    and completion_scope.pending_authority
                    is pending_authority
                    and completion_scope.terminal is terminal
                    and completion_scope.publication_lock
                    is publication_lock
                    and completion_scope.publication_epoch
                    == subject_authority.publication_epoch
                    and completion_scope.source_close_claim
                    is proof_authority.claim
                    and completion_scope.subject is subject
                    and terminal_authority.kind == "TERMINAL"
                    and terminal_authority.consumer is controller
                    and terminal_authority.ingress is ingress
                    and terminal_authority.runtime
                    is subject_authority.runtime
                    and terminal_authority.session_id
                    == subject_authority.session_id
                )
                public_fields_valid = (
                    _proof_public_fields_valid_v1(
                        proof,
                        proof_authority,
                        allowed_terminal_lifecycles=(
                            allowed_terminal_lifecycles
                        ),
                    )
                    and _subject_public_fields_valid_v1(
                        subject,
                        subject_authority,
                        allowed_terminal_lifecycles=(
                            allowed_terminal_lifecycles
                        ),
                    )
                    and _envelope_public_fields_valid_v1(
                        terminal,
                        terminal_authority,
                    )
                )
                reserved_slots_exact = (
                    pending_authority.reserved_claim
                    is proof_authority.claim
                    and pending_authority.reserved_subject is subject
                    and pending_authority.reserved_terminal is terminal
                    and pending_authority.reserved_causal_proof is proof
                    and pending_authority.reserved_completion_scope
                    is completion_scope
                    and completion_scope.causal_proof is proof
                )
                same_owner = (
                    proof_authority.owner_pid
                    == subject_authority.owner_pid
                    == pending_authority.owner_pid
                    == completion_scope.owner_pid
                    == terminal_authority.owner_pid
                    == getpid()
                    and proof_authority.owner_thread
                    is subject_authority.owner_thread
                    is pending_authority.owner_thread
                    is completion_scope.owner_thread
                    is terminal_authority.owner_thread
                    is threading.current_thread()
                )
                target = _deferred_proof_finalization_kernel_v1(
                    _DeferredProofFinalizationKernelInputV1(
                        proof_lifecycle=proof_authority.lifecycle,
                        exact_bindings=exact_bindings,
                        public_fields_valid=public_fields_valid,
                        same_owner=same_owner,
                        subject_lifecycle=subject_authority.lifecycle,
                        pending_lifecycle=pending_authority.lifecycle,
                        completion_scope_lifecycle=(
                            completion_scope.lifecycle
                        ),
                        reservation_committed=(
                            completion_scope.reservation_committed
                        ),
                        reserved_slots_exact=reserved_slots_exact,
                        terminal_lifecycle=terminal_authority.lifecycle,
                        append_succeeded=append_succeeded,
                    )
                )
                proof_authority.lifecycle = target
    except (TypeError, ValueError):
        raise
    except Exception:
        raise ValueError("durable_causal_subject_mismatch") from None


def _prepare_durable_causal_precedes_proof_commit_v1(
    proof: DurableCausalPrecedesProofV1,
    *,
    subject: DeferredEmergencyCommitSubjectV1,
    controller: object,
    pending: object,
    terminal: DurableEvidenceTerminalV1,
) -> _PreparedDeferredEmergencyCausalProofCommitV1:
    if type(proof) is not DurableCausalPrecedesProofV1:
        raise TypeError("exact DurableCausalPrecedesProofV1 required")
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    if type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    try:
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            ExpertControllerV1,
            PendingDurableCompanionEmergencyV1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_proof_commit_invalid") from None
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(pending) is not PendingDurableCompanionEmergencyV1:
        raise TypeError("exact PendingDurableCompanionEmergencyV1 required")
    proof_authority = _lookup_proof_authority(proof)
    prepared_reference = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else proof_authority.prepared_commit
    )
    prepared = (
        None
        if prepared_reference is None
        else prepared_reference()
    )
    subject_authority = _lookup_subject_authority(subject)
    if (
        type(subject_authority) is not _SubjectAuthorityV1
        and type(prepared) is _PreparedDeferredEmergencyCausalProofCommitV1
    ):
        subject_authority = prepared.subject_authority
    terminal_authority = _lookup_envelope_authority(terminal)
    registry_cell = (
        None
        if type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1
        else _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(id(prepared))
    )
    if (
        type(prepared) is _PreparedDeferredEmergencyCausalProofCommitV1
        and prepared.lifecycle != "PREPARED"
    ):
        raise ValueError("durable_causal_proof_commit_consumed") from None
    if (
        type(proof_authority) is _ProofAuthorityV1
        and proof_authority.lifecycle != "ISSUED"
    ):
        raise ValueError("durable_causal_proof_commit_consumed") from None
    if (
        type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1
        or type(registry_cell) is not _PreparedDeferredCommitRegistryCellV1
        or registry_cell.lifecycle != "LIVE"
        or registry_cell.success_armed is not False
        or registry_cell.proof_reference() is not proof
        or not _prepared_deferred_commit_registry_bindings_valid_v1(
            prepared,
            registry_cell,
        )
        or _lookup_prepared_deferred_emergency_causal_proof_commit_v1(prepared)
        is not prepared
        or type(subject_authority) is not _SubjectAuthorityV1
        or type(terminal_authority) is not _EnvelopeAuthorityV1
        or type(prepared.pending_authority)
        is not _PendingDurableCompanionEmergencyAuthorityV1
        or type(prepared.completion_scope)
        is not _DeferredEmergencyCompletionScopeV1
        or prepared.proof_authority is not proof_authority
        or proof_authority.proof_reference is None
        or proof_authority.proof_reference() is not proof
        or object.__getattribute__(proof, "_proof_authority")
        is not proof_authority
        or object.__getattribute__(proof, "_prepared_commit") is not prepared
        or prepared.subject_authority is not subject_authority
        or prepared.terminal_authority is not terminal_authority
        or prepared.controller is not controller
        or proof_authority.subject is not subject
        or proof_authority.pending is not pending
        or proof_authority.terminal is not terminal
        or subject_authority.controller is not controller
        or subject_authority.pending is not pending
        or subject_authority.terminal is not terminal
        or terminal_authority.consumer is not controller
        or terminal_authority.lifecycle != _ENVELOPE_CONSUMED
        or prepared.pending_authority.pending is not pending
        or prepared.pending_authority.lifecycle != "COMMIT_RESERVED"
        or prepared.pending_authority.reserved_subject is not subject
        or prepared.pending_authority.reserved_terminal is not terminal
        or prepared.pending_authority.reserved_causal_proof is not proof
        or prepared.pending_authority.reserved_completion_scope
        is not prepared.completion_scope
        or prepared.completion_scope.lifecycle != "RESERVATION_COMMITTED"
        or prepared.completion_scope.reservation_committed is not True
        or prepared.completion_scope.causal_proof is not proof
        or prepared.completion_scope.subject is not subject
        or prepared.completion_scope.terminal is not terminal
        or prepared.ingress is not subject_authority.ingress
        or prepared.ingress_lock is not prepared.ingress._causal_subject_lock
        or prepared.publication_lock is not subject_authority.publication_lock
        or prepared.owner_pid != getpid()
        or prepared.owner_thread is not threading.current_thread()
        or prepared.target_proof_lifecycle
        != "CONSUMED_BY_FUTURE_COMPLETION"
        or prepared.success_armed is not False
        or not _proof_public_fields_valid_v1(
            proof,
            proof_authority,
            allowed_terminal_lifecycles=(_ENVELOPE_CONSUMED,),
        )
        or not _subject_public_fields_valid_v1(
            subject,
            subject_authority,
            allowed_terminal_lifecycles=(_ENVELOPE_CONSUMED,),
        )
        or not _envelope_public_fields_valid_v1(terminal, terminal_authority)
    ):
        raise ValueError("durable_causal_proof_commit_invalid") from None
    publication_owned = getattr(prepared.publication_lock, "_is_owned", None)
    ingress_owned = getattr(prepared.ingress_lock, "_is_owned", None)
    if (
        publication_owned is None
        or ingress_owned is None
        or publication_owned() is not True
        or ingress_owned() is not True
    ):
        raise ValueError("durable_causal_proof_commit_invalid") from None
    registry_cell.success_armed = True
    prepared.success_armed = True
    return prepared


def _commit_prepared_durable_causal_precedes_proof_v1(
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
) -> None:
    if type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1:
        raise TypeError(
            "exact _PreparedDeferredEmergencyCausalProofCommitV1 required"
        )
    prepared_key = id(prepared)
    entry = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(prepared_key)
    if (
        type(entry) is not _PreparedDeferredCommitRegistryCellV1
        or entry.prepared_reference() is not prepared
    ):
        raise ValueError("durable_causal_proof_commit_invalid") from None
    if entry.lifecycle != "LIVE":
        if entry.lifecycle in ("COMMITTED", "FAILED_CLOSED"):
            raise ValueError("durable_causal_proof_commit_consumed") from None
        raise ValueError("durable_causal_proof_commit_invalid") from None
    proof = entry.proof_reference()
    proof_authority = prepared.proof_authority
    publication_owned = getattr(
        prepared.publication_lock,
        "_is_owned",
        None,
    )
    ingress_owned = getattr(
        prepared.ingress_lock,
        "_is_owned",
        None,
    )
    if (
        type(proof_authority) is not _ProofAuthorityV1
        or type(proof) is not DurableCausalPrecedesProofV1
        or proof_authority.proof_reference is None
        or proof_authority.proof_reference() is not proof
        or proof_authority.prepared_commit is None
        or proof_authority.prepared_commit() is not prepared
        or object.__getattribute__(proof, "_proof_authority")
        is not proof_authority
        or object.__getattribute__(proof, "_prepared_commit") is not prepared
        or not _prepared_deferred_commit_registry_bindings_valid_v1(
            prepared,
            entry,
        )
        or proof_authority.lifecycle != "ISSUED"
        or prepared.lifecycle != "PREPARED"
        or prepared.success_armed is not True
        or entry.success_armed is not True
        or prepared.owner_pid != getpid()
        or prepared.owner_thread is not threading.current_thread()
        or prepared.target_proof_lifecycle
        != "CONSUMED_BY_FUTURE_COMPLETION"
        or publication_owned is None
        or ingress_owned is None
        or publication_owned() is not True
        or ingress_owned() is not True
    ):
        raise ValueError("durable_causal_proof_commit_invalid") from None
    target_proof_lifecycle = prepared.target_proof_lifecycle
    object.__setattr__(proof, "_proof_authority", None)
    object.__setattr__(proof, "_prepared_commit", None)
    proof_authority.lifecycle = target_proof_lifecycle
    prepared.lifecycle = "COMMITTED"
    entry.lifecycle = "COMMITTED"
    entry.success_armed = True
    proof_authority.before = None
    proof_authority.after = None
    proof_authority.subject = None
    proof_authority.claim = None
    proof_authority.pending = None
    proof_authority.terminal = None
    proof_authority.owner_thread = None
    proof_authority.issuance = None
    proof_authority.proof_reference = None
    proof_authority.prepared_commit = None
    prepared.ingress = None
    prepared.controller = None
    prepared.pending_authority = None
    prepared.completion_scope = None
    prepared.proof_authority = None
    prepared.subject_authority = None
    prepared.terminal_authority = None
    prepared.publication_lock = None
    prepared.ingress_lock = None
    prepared.owner_thread = None


def _consume_durable_causal_precedes_proof_after_deferred_append_v1(
    proof: DurableCausalPrecedesProofV1,
    *,
    subject: DeferredEmergencyCommitSubjectV1,
    controller: object,
    pending: object,
    terminal: DurableEvidenceTerminalV1,
) -> None:
    if type(proof) is not DurableCausalPrecedesProofV1:
        raise TypeError("exact DurableCausalPrecedesProofV1 required")
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    if type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    proof_authority = _lookup_proof_authority(proof)
    prepared = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else _lookup_prepared_deferred_emergency_causal_proof_commit_for_proof_v1(
            proof
        )
    )
    if (
        type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1
        or prepared.proof_authority is not proof_authority
        or proof_authority.subject is not subject
        or prepared.controller is not controller
        or proof_authority.pending is not pending
        or proof_authority.terminal is not terminal
        or prepared.success_armed is not True
    ):
        raise ValueError("durable_causal_subject_mismatch") from None
    _commit_prepared_durable_causal_precedes_proof_v1(prepared)


def _apply_deferred_append_failure_close_scalars_v1(
    proof: DurableCausalPrecedesProofV1,
    proof_authority: _ProofAuthorityV1,
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
    registry_cell: _PreparedDeferredCommitRegistryCellV1,
    pending_authority: object,
    completion_scope: object,
    target: str,
) -> None:
    """Apply the prevalidated append-failure close as one scalar tail."""
    object.__setattr__(proof, "_proof_authority", None)
    object.__setattr__(proof, "_prepared_commit", None)
    proof_authority.lifecycle = target
    prepared.lifecycle = "FAILED_CLOSED"
    registry_cell.lifecycle = "FAILED_CLOSED"
    pending_authority.lifecycle = "PUBLICATION_FAILED_CLOSED"
    completion_scope.lifecycle = "APPEND_FAILED_CLOSED"
    proof_authority.before = None
    proof_authority.after = None
    proof_authority.subject = None
    proof_authority.claim = None
    proof_authority.pending = None
    proof_authority.terminal = None
    proof_authority.owner_thread = None
    proof_authority.issuance = None
    proof_authority.proof_reference = None
    proof_authority.prepared_commit = None
    prepared.ingress = None
    prepared.controller = None
    prepared.pending_authority = None
    prepared.completion_scope = None
    prepared.proof_authority = None
    prepared.subject_authority = None
    prepared.terminal_authority = None
    prepared.publication_lock = None
    prepared.ingress_lock = None
    prepared.owner_thread = None


def _close_durable_causal_precedes_proof_after_deferred_append_failure_v1(
    proof: DurableCausalPrecedesProofV1,
    *,
    subject: DeferredEmergencyCommitSubjectV1,
    controller: object,
    pending: object,
    terminal: DurableEvidenceTerminalV1,
) -> None:
    if type(proof) is not DurableCausalPrecedesProofV1:
        raise TypeError("exact DurableCausalPrecedesProofV1 required")
    if type(subject) is not DeferredEmergencyCommitSubjectV1:
        raise TypeError("exact DeferredEmergencyCommitSubjectV1 required")
    if type(terminal) is not DurableEvidenceTerminalV1:
        raise TypeError("exact DurableEvidenceTerminalV1 required")
    proof_authority = _lookup_proof_authority(proof)
    prepared_reference = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else proof_authority.prepared_commit
    )
    prepared = (
        None
        if prepared_reference is None
        else prepared_reference()
    )
    if (
        type(proof_authority) is not _ProofAuthorityV1
        or type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1
    ):
        raise ValueError("durable_causal_subject_mismatch") from None
    subject_authority = _lookup_subject_authority(subject)
    if type(subject_authority) is not _SubjectAuthorityV1:
        subject_authority = prepared.subject_authority
    terminal_authority = _lookup_envelope_authority(terminal)
    if proof_authority.lifecycle != "ISSUED":
        raise ValueError("durable_causal_proof_consumed") from None
    if (
        type(subject_authority) is not _SubjectAuthorityV1
        or type(terminal_authority) is not _EnvelopeAuthorityV1
        or prepared.proof_authority is not proof_authority
        or prepared.subject_authority is not subject_authority
        or prepared.terminal_authority is not terminal_authority
        or proof_authority.subject is not subject
        or proof_authority.terminal is not terminal
        or subject_authority.terminal is not terminal
    ):
        raise ValueError("durable_causal_subject_mismatch") from None
    try:
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            ExpertControllerV1,
            PendingDurableCompanionEmergencyV1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_subject_mismatch") from None
    if type(controller) is not ExpertControllerV1:
        raise TypeError("exact ExpertControllerV1 required")
    if type(pending) is not PendingDurableCompanionEmergencyV1:
        raise TypeError("exact PendingDurableCompanionEmergencyV1 required")
    if (
        type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1
        or type(subject_authority) is not _SubjectAuthorityV1
        or type(terminal_authority) is not _EnvelopeAuthorityV1
        or type(prepared.pending_authority)
        is not _PendingDurableCompanionEmergencyAuthorityV1
        or type(prepared.completion_scope)
        is not _DeferredEmergencyCompletionScopeV1
        or prepared.proof_authority is not proof_authority
        or prepared.subject_authority is not subject_authority
        or prepared.terminal_authority is not terminal_authority
        or prepared.controller is not controller
        or prepared.lifecycle != "PREPARED"
        or type(prepared.success_armed) is not bool
        or proof_authority.subject is not subject
        or proof_authority.pending is not pending
        or proof_authority.terminal is not terminal
        or subject_authority.controller is not controller
        or subject_authority.pending is not pending
        or subject_authority.terminal is not terminal
        or subject_authority.lifecycle != "CONSUMED"
        or terminal_authority.consumer is not controller
        or terminal_authority.lifecycle
        not in (_ENVELOPE_ISSUED, _ENVELOPE_CONSUMED)
        or prepared.pending_authority.pending is not pending
        or prepared.pending_authority.lifecycle != "COMMIT_RESERVED"
        or prepared.pending_authority.reserved_subject is not subject
        or prepared.pending_authority.reserved_terminal is not terminal
        or prepared.pending_authority.reserved_causal_proof is not proof
        or prepared.pending_authority.reserved_completion_scope
        is not prepared.completion_scope
        or prepared.completion_scope.lifecycle != "RESERVATION_COMMITTED"
        or prepared.completion_scope.reservation_committed is not True
        or prepared.completion_scope.causal_proof is not proof
        or prepared.completion_scope.subject is not subject
        or prepared.completion_scope.terminal is not terminal
        or prepared.ingress is not subject_authority.ingress
        or prepared.ingress_lock is not prepared.ingress._causal_subject_lock
        or prepared.publication_lock is not subject_authority.publication_lock
        or prepared.owner_pid != getpid()
        or prepared.owner_thread is not threading.current_thread()
        or prepared.target_proof_lifecycle
        != "CONSUMED_BY_FUTURE_COMPLETION"
    ):
        raise ValueError("durable_causal_subject_mismatch") from None
    publication_owned = getattr(prepared.publication_lock, "_is_owned", None)
    ingress_owned = getattr(prepared.ingress_lock, "_is_owned", None)
    registry_cell = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(id(prepared))
    if (
        publication_owned is None
        or ingress_owned is None
        or publication_owned() is not True
        or ingress_owned() is not True
        or type(registry_cell) is not _PreparedDeferredCommitRegistryCellV1
        or registry_cell.prepared_reference() is not prepared
        or registry_cell.proof_reference() is not proof
        or registry_cell.lifecycle != "LIVE"
        or registry_cell.success_armed is not prepared.success_armed
        or proof_authority.proof_reference is None
        or proof_authority.proof_reference() is not proof
        or object.__getattribute__(proof, "_proof_authority")
        is not proof_authority
        or object.__getattribute__(proof, "_prepared_commit") is not prepared
        or not _prepared_deferred_commit_registry_bindings_valid_v1(
            prepared,
            registry_cell,
        )
    ):
        raise ValueError("durable_causal_subject_mismatch") from None
    target = _deferred_proof_finalization_kernel_v1(
        _DeferredProofFinalizationKernelInputV1(
            proof_lifecycle=proof_authority.lifecycle,
            exact_bindings=True,
            public_fields_valid=True,
            same_owner=(
                proof_authority.owner_pid
                == subject_authority.owner_pid
                == prepared.pending_authority.owner_pid
                == prepared.completion_scope.owner_pid
                == terminal_authority.owner_pid
                == getpid()
                and proof_authority.owner_thread
                is subject_authority.owner_thread
                is prepared.pending_authority.owner_thread
                is prepared.completion_scope.owner_thread
                is terminal_authority.owner_thread
                is threading.current_thread()
            ),
            subject_lifecycle=subject_authority.lifecycle,
            pending_lifecycle=prepared.pending_authority.lifecycle,
            completion_scope_lifecycle=prepared.completion_scope.lifecycle,
            reservation_committed=(
                prepared.completion_scope.reservation_committed
            ),
            reserved_slots_exact=True,
            terminal_lifecycle=terminal_authority.lifecycle,
            append_succeeded=False,
        )
    )
    _apply_deferred_append_failure_close_scalars_v1(
        proof,
        proof_authority,
        prepared,
        registry_cell,
        prepared.pending_authority,
        prepared.completion_scope,
        target,
    )


def _converge_durable_causal_precedes_proof_after_deferred_append_failure_v1(
    prepared: _PreparedDeferredEmergencyCausalProofCommitV1,
) -> None:
    """Finish the exact prepared append-failure close after close uncertainty."""
    if type(prepared) is not _PreparedDeferredEmergencyCausalProofCommitV1:
        raise TypeError(
            "exact _PreparedDeferredEmergencyCausalProofCommitV1 required"
        )
    registry_cell = _PREPARED_DEFERRED_COMMIT_ENTRIES_V1.get(id(prepared))
    if (
        type(registry_cell) is not _PreparedDeferredCommitRegistryCellV1
        or registry_cell.prepared_reference() is not prepared
    ):
        raise ValueError("durable_causal_subject_mismatch") from None
    proof = registry_cell.proof_reference()
    if type(proof) is not DurableCausalPrecedesProofV1:
        raise ValueError("durable_causal_subject_mismatch") from None

    if (
        prepared.lifecycle == "FAILED_CLOSED"
        or registry_cell.lifecycle == "FAILED_CLOSED"
    ):
        if (
            prepared.lifecycle != "FAILED_CLOSED"
            or registry_cell.lifecycle != "FAILED_CLOSED"
            or registry_cell.success_armed is not prepared.success_armed
            or object.__getattribute__(proof, "_proof_authority") is not None
            or object.__getattribute__(proof, "_prepared_commit") is not None
            or prepared.ingress is not None
            or prepared.controller is not None
            or prepared.pending_authority is not None
            or prepared.completion_scope is not None
            or prepared.proof_authority is not None
            or prepared.subject_authority is not None
            or prepared.terminal_authority is not None
            or prepared.publication_lock is not None
            or prepared.ingress_lock is not None
            or prepared.owner_thread is not None
        ):
            raise ValueError("durable_causal_subject_mismatch") from None
        return

    try:
        from inci_tennis_runtime.expert_controller import (
            _DeferredEmergencyCompletionScopeV1,
            _PendingDurableCompanionEmergencyAuthorityV1,
            ExpertControllerV1,
            PendingDurableCompanionEmergencyV1,
        )
    except (ImportError, AttributeError):
        raise ValueError("durable_causal_subject_mismatch") from None

    proof_authority = prepared.proof_authority
    subject_authority = prepared.subject_authority
    terminal_authority = prepared.terminal_authority
    pending_authority = prepared.pending_authority
    completion_scope = prepared.completion_scope
    controller = prepared.controller
    subject = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else proof_authority.subject
    )
    pending = (
        None
        if type(pending_authority)
        is not _PendingDurableCompanionEmergencyAuthorityV1
        else pending_authority.pending
    )
    terminal = (
        None
        if type(proof_authority) is not _ProofAuthorityV1
        else proof_authority.terminal
    )
    publication_owned = getattr(prepared.publication_lock, "_is_owned", None)
    ingress_owned = getattr(prepared.ingress_lock, "_is_owned", None)
    if (
        type(controller) is not ExpertControllerV1
        or type(pending) is not PendingDurableCompanionEmergencyV1
        or type(subject) is not DeferredEmergencyCommitSubjectV1
        or type(terminal) is not DurableEvidenceTerminalV1
        or type(proof_authority) is not _ProofAuthorityV1
        or type(subject_authority) is not _SubjectAuthorityV1
        or type(terminal_authority) is not _EnvelopeAuthorityV1
        or type(pending_authority)
        is not _PendingDurableCompanionEmergencyAuthorityV1
        or type(completion_scope)
        is not _DeferredEmergencyCompletionScopeV1
        or prepared.lifecycle != "PREPARED"
        or registry_cell.lifecycle != "LIVE"
        or registry_cell.proof_reference() is not proof
        or registry_cell.success_armed is not prepared.success_armed
        or prepared.proof_authority is not proof_authority
        or prepared.subject_authority is not subject_authority
        or prepared.terminal_authority is not terminal_authority
        or prepared.pending_authority is not pending_authority
        or prepared.completion_scope is not completion_scope
        or proof_authority.subject is not subject
        or proof_authority.pending is not pending
        or proof_authority.terminal is not terminal
        or proof_authority.lifecycle != "ISSUED"
        or proof_authority.proof_reference is None
        or proof_authority.proof_reference() is not proof
        or proof_authority.prepared_commit is None
        or proof_authority.prepared_commit() is not prepared
        or object.__getattribute__(proof, "_proof_authority")
        is not proof_authority
        or object.__getattribute__(proof, "_prepared_commit") is not prepared
        or subject_authority.controller is not controller
        or subject_authority.pending is not pending
        or subject_authority.terminal is not terminal
        or subject_authority.lifecycle != "CONSUMED"
        or terminal_authority.consumer is not controller
        or terminal_authority.lifecycle
        not in (_ENVELOPE_ISSUED, _ENVELOPE_CONSUMED)
        or pending_authority.controller is not controller
        or pending_authority.pending is not pending
        or pending_authority.lifecycle != "COMMIT_RESERVED"
        or pending_authority.reserved_subject is not subject
        or pending_authority.reserved_terminal is not terminal
        or pending_authority.reserved_causal_proof is not proof
        or pending_authority.reserved_completion_scope is not completion_scope
        or completion_scope.controller is not controller
        or completion_scope.pending is not pending
        or completion_scope.pending_authority is not pending_authority
        or completion_scope.lifecycle != "RESERVATION_COMMITTED"
        or completion_scope.reservation_committed is not True
        or completion_scope.causal_proof is not proof
        or completion_scope.subject is not subject
        or completion_scope.terminal is not terminal
        or prepared.ingress is not subject_authority.ingress
        or prepared.ingress_lock is not prepared.ingress._causal_subject_lock
        or prepared.publication_lock is not subject_authority.publication_lock
        or prepared.owner_pid != getpid()
        or prepared.owner_thread is not threading.current_thread()
        or prepared.target_proof_lifecycle
        != "CONSUMED_BY_FUTURE_COMPLETION"
        or type(prepared.success_armed) is not bool
        or publication_owned is None
        or ingress_owned is None
        or publication_owned() is not True
        or ingress_owned() is not True
        or not _prepared_deferred_commit_registry_bindings_valid_v1(
            prepared,
            registry_cell,
        )
        or not _proof_public_fields_valid_v1(
            proof,
            proof_authority,
            allowed_terminal_lifecycles=(
                _ENVELOPE_ISSUED,
                _ENVELOPE_CONSUMED,
            ),
        )
        or not _subject_public_fields_valid_v1(
            subject,
            subject_authority,
            allowed_terminal_lifecycles=(
                _ENVELOPE_ISSUED,
                _ENVELOPE_CONSUMED,
            ),
        )
        or not _envelope_public_fields_valid_v1(
            terminal,
            terminal_authority,
        )
    ):
        raise ValueError("durable_causal_subject_mismatch") from None

    target = _deferred_proof_finalization_kernel_v1(
        _DeferredProofFinalizationKernelInputV1(
            proof_lifecycle=proof_authority.lifecycle,
            exact_bindings=True,
            public_fields_valid=True,
            same_owner=(
                proof_authority.owner_pid
                == subject_authority.owner_pid
                == pending_authority.owner_pid
                == completion_scope.owner_pid
                == terminal_authority.owner_pid
                == getpid()
                and proof_authority.owner_thread
                is subject_authority.owner_thread
                is pending_authority.owner_thread
                is completion_scope.owner_thread
                is terminal_authority.owner_thread
                is threading.current_thread()
            ),
            subject_lifecycle=subject_authority.lifecycle,
            pending_lifecycle=pending_authority.lifecycle,
            completion_scope_lifecycle=completion_scope.lifecycle,
            reservation_committed=completion_scope.reservation_committed,
            reserved_slots_exact=True,
            terminal_lifecycle=terminal_authority.lifecycle,
            append_succeeded=False,
        )
    )
    _apply_deferred_append_failure_close_scalars_v1(
        proof,
        proof_authority,
        prepared,
        registry_cell,
        pending_authority,
        completion_scope,
        target,
    )
