"""Opaque I/O capabilities for the diagnostic expert companion journal."""

from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
import re
import uuid

from inci_tennis_expert.contracts import (
    DurableExpertAppendReceiptV1,
    DurableExpertEmergencyReceiptV1,
    DurableExpertTerminalReceiptV1,
    ExpertJournalScanSummaryV1,
    ExpertNormalizerPinV1,
    ExpertPhysicalFileIdentityV1,
    ExpertPurgeReportV1,
    canonical_expert_bytes,
)

_SHA256_PATTERN = r"[0-9a-f]{64}\Z"
_SAFE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
_TRACE_RECORD_TYPES = frozenset(
    {"permit", "capture", "parser_result", "failure", "terminal"}
)
_TERMINAL_REASONS = frozenset(
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


class CandidatePortContractError(ValueError):
    def __init__(self) -> None:
        super().__init__("candidate_port_contract_invalid")


def _fail() -> None:
    raise CandidatePortContractError()


def _exact_int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail()
    return value


def _exact_bool(value: object) -> bool:
    if type(value) is not bool:
        _fail()
    return value


def _exact_text(value: object) -> str:
    if type(value) is not str:
        _fail()
    return value


def _digest(value: object) -> str:
    text = _exact_text(value)
    if re.fullmatch(_SHA256_PATTERN, text, flags=re.ASCII) is None:
        _fail()
    return text


def _safe_id(value: object) -> str:
    text = _exact_text(value)
    if re.fullmatch(_SAFE_ID_PATTERN, text, flags=re.ASCII) is None:
        _fail()
    return text


def _session_id(value: object) -> str:
    text = _exact_text(value)
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, TypeError, ValueError):
        _fail()
    if str(parsed) != text:
        _fail()
    return text


def _private_construct(cls: type[object], values: dict[str, object]) -> object:
    expected = tuple(item.name for item in fields(cls))
    if set(values) != set(expected):
        _fail()
    instance = object.__new__(cls)
    for name in expected:
        object.__setattr__(instance, name, values[name])
    instance._validate()  # type: ignore[attr-defined]
    return instance


def _candidate_digest(domain: bytes, projection: object) -> str:
    return sha256(domain + canonical_expert_bytes(projection)).hexdigest()


class ExpertLiveAuthorizationDenied(RuntimeError):
    def __init__(self) -> None:
        super().__init__("expert_live_authorization_denied")


class ExpertPrewriteCapacityError(Exception):
    __slots__ = (
        "requested_bytes",
        "available_bytes",
        "emergency_reserve_bytes",
    )

    def __init__(
        self,
        *,
        requested_bytes: int | None = None,
        available_bytes: int | None = None,
        emergency_reserve_bytes: int | None = None,
    ) -> None:
        values = (
            requested_bytes,
            available_bytes,
            emergency_reserve_bytes,
        )
        if any(value is not None for value in values):
            if any(type(value) is not int for value in values):
                raise TypeError("expert_prewrite_capacity_observation")
            if any(
                value < 0 or value > 9_223_372_036_854_775_807
                for value in values
            ):
                raise ValueError("expert_prewrite_capacity_observation")
        self.requested_bytes = requested_bytes
        self.available_bytes = available_bytes
        self.emergency_reserve_bytes = emergency_reserve_bytes
        super().__init__("expert_prewrite_capacity_low")


class ExpertReplayAccessDenied(RuntimeError):
    def __init__(self) -> None:
        super().__init__("expert_replay_access_denied")


class _OpaqueCapability:
    __slots__ = ("__weakref__",)

    _label = "expert capability"

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError(f"{self._label} is privately issued")

    def __init_subclass__(cls, **kwargs: object) -> None:
        if cls.__module__ != __name__:
            raise TypeError("expert capabilities cannot be subclassed")
        super().__init_subclass__(**kwargs)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    def __copy__(self):
        raise TypeError("expert capabilities cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("expert capabilities cannot be copied")

    def __reduce__(self):
        raise TypeError("expert capabilities cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("expert capabilities cannot be pickled")

    def __getstate__(self):
        raise TypeError("expert capabilities cannot be pickled")


class ExpertJournalRootAuthorityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert journal root authority"


class ExpertEnvironmentCollectionAuthorityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert environment collection authority"


class ExpertReplayConstructionAuthorityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert replay construction authority"


class ExpertJournalWriteCapabilityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert journal write capability"


class ExpertJournalReadCapabilityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert journal read capability"


class ExpertJournalPurgeCapabilityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert journal purge capability"


class ExpertJournalAppendPermitV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert journal append permit"


class ExpertJournalTerminalPermitV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert journal terminal permit"


class ExpertEmergencyAppendPermitV1(_OpaqueCapability):
    __slots__ = ()
    _label = "expert emergency append permit"


class CandidateObservationStartupAuthorityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "candidate observation startup authority"


class CandidateSourceSealCollectionAuthorityV1(_OpaqueCapability):
    __slots__ = ()
    _label = "candidate source seal collection authority"


class CandidateQualificationOutputWriterV1(_OpaqueCapability):
    __slots__ = ()
    _label = "candidate qualification output writer"


class SportradarCandidatePreparedReadV1(_OpaqueCapability):
    __slots__ = ()
    _label = "sportradar candidate prepared read"


@dataclass(frozen=True, slots=True, init=False)
class CandidateQualificationAppendReceiptV1:
    schema_version: int
    session_id: str
    record_index: int
    record_type: str
    record_sha256: str
    trace_prefix_sha256: str
    durable_trace_length: int
    retention_delete_by_ns: int
    fsynced: bool
    receipt_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        if type(self) is not CandidateQualificationAppendReceiptV1:
            _fail()
        if self.schema_version != 1:
            _fail()
        _session_id(self.session_id)
        _exact_int(self.record_index, minimum=1)
        if self.record_type not in _TRACE_RECORD_TYPES:
            _fail()
        _digest(self.record_sha256)
        _digest(self.trace_prefix_sha256)
        _exact_int(self.durable_trace_length, minimum=1)
        _exact_int(self.retention_delete_by_ns, minimum=1)
        if _exact_bool(self.fsynced) is not True:
            _fail()
        _digest(self.receipt_sha256)
        expected = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-TRACE-RECEIPT-V1\0",
            (
                ("schema_version", self.schema_version),
                ("session_id", self.session_id),
                ("record_index", self.record_index),
                ("record_type", self.record_type),
                ("record_sha256", self.record_sha256),
                ("trace_prefix_sha256", self.trace_prefix_sha256),
                ("durable_trace_length", self.durable_trace_length),
                ("retention_delete_by_ns", self.retention_delete_by_ns),
                ("fsynced", self.fsynced),
            ),
        )
        if self.receipt_sha256 != expected:
            _fail()


def _create_candidate_qualification_append_receipt_v1(
    **values: object,
) -> CandidateQualificationAppendReceiptV1:
    return _private_construct(  # type: ignore[return-value]
        CandidateQualificationAppendReceiptV1,
        values,
    )


@dataclass(frozen=True, slots=True, init=False)
class CandidateQualificationCommitReceiptV1:
    schema_version: int
    session_id: str
    final_basename: str
    summary_sha256: str
    summary_length: int
    trace_sha256: str
    trace_length: int
    trace_record_count: int
    terminal_record_sha256: str
    terminal_reason: str
    retention_delete_by_ns: int
    files_fsynced: bool
    staging_directory_fsynced: bool
    parent_fsynced: bool
    receipt_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        if type(self) is not CandidateQualificationCommitReceiptV1:
            _fail()
        if self.schema_version != 1:
            _fail()
        _session_id(self.session_id)
        if (
            self.final_basename
            != f"sportradar-candidate-qualification-{self.session_id}"
        ):
            _fail()
        _digest(self.summary_sha256)
        _exact_int(self.summary_length, minimum=1)
        _digest(self.trace_sha256)
        _exact_int(self.trace_length, minimum=1)
        _exact_int(self.trace_record_count, minimum=1)
        _digest(self.terminal_record_sha256)
        if self.terminal_reason not in _TERMINAL_REASONS:
            _fail()
        _exact_int(self.retention_delete_by_ns, minimum=1)
        if not (
            _exact_bool(self.files_fsynced)
            and _exact_bool(self.staging_directory_fsynced)
            and _exact_bool(self.parent_fsynced)
        ):
            _fail()
        _digest(self.receipt_sha256)
        expected = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-OUTPUT-COMMIT-RECEIPT-V1\0",
            (
                ("schema_version", self.schema_version),
                ("session_id", self.session_id),
                ("final_basename", self.final_basename),
                ("summary_sha256", self.summary_sha256),
                ("summary_length", self.summary_length),
                ("trace_sha256", self.trace_sha256),
                ("trace_length", self.trace_length),
                ("trace_record_count", self.trace_record_count),
                ("terminal_record_sha256", self.terminal_record_sha256),
                ("terminal_reason", self.terminal_reason),
                ("retention_delete_by_ns", self.retention_delete_by_ns),
                ("files_fsynced", self.files_fsynced),
                (
                    "staging_directory_fsynced",
                    self.staging_directory_fsynced,
                ),
                ("parent_fsynced", self.parent_fsynced),
            ),
        )
        if self.receipt_sha256 != expected:
            _fail()


def _create_candidate_qualification_commit_receipt_v1(
    **values: object,
) -> CandidateQualificationCommitReceiptV1:
    return _private_construct(  # type: ignore[return-value]
        CandidateQualificationCommitReceiptV1,
        values,
    )


@dataclass(frozen=True, slots=True, init=False)
class CandidateSourceSealsV1:
    schema_version: int
    normalizer_pins: tuple[ExpertNormalizerPinV1, ...]
    candidate_adapter_inventory_sha256: str
    candidate_io_bridge_inventory_sha256: str
    provider_transport_source_sha256: str
    qualification_controller_source_sha256: str
    qualification_tool_source_sha256: str
    candidate_manifest_schema_sha256: str
    candidate_authorization_schema_sha256: str
    candidate_output_schema_sha256: str
    qualification_protocol_sha256: str
    candidate_source_seals_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        if type(self) is not CandidateSourceSealsV1:
            _fail()
        if self.schema_version != 1 or type(self.normalizer_pins) is not tuple:
            _fail()
        if len(self.normalizer_pins) != 3:
            _fail()
        for pin in self.normalizer_pins:
            if type(pin) is not ExpertNormalizerPinV1:
                _fail()
            ExpertNormalizerPinV1.__post_init__(pin)
        route_order = tuple(
            (pin.source_kind, pin.source_id, pin.event_type, pin.event_version)
            for pin in self.normalizer_pins
        )
        if route_order != (
            ("provider", "sportradar", "sportradar_tennis_summary_v3", 1),
            ("provider", "sportradar", "sportradar_tennis_timeline_v3", 1),
            (
                "provider",
                "sportradar",
                "sportradar_tennis_transport_error_v1",
                1,
            ),
        ):
            _fail()
        digest_names = tuple(
            item.name
            for item in fields(self)
            if item.name not in {"schema_version", "normalizer_pins"}
        )
        for name in digest_names:
            _digest(getattr(self, name))
        projection = (
            ("schema_version", self.schema_version),
            (
                "normalizer_pins",
                tuple(
                    (
                        ("normalizer_id", pin.normalizer_id),
                        ("source_kind", pin.source_kind),
                        ("source_id", pin.source_id),
                        ("event_type", pin.event_type),
                        ("event_version", pin.event_version),
                        (
                            "normalizer_code_sha256",
                            pin.normalizer_code_sha256,
                        ),
                        (
                            "normalizer_schema_sha256",
                            pin.normalizer_schema_sha256,
                        ),
                    )
                    for pin in self.normalizer_pins
                ),
            ),
            (
                "candidate_adapter_inventory_sha256",
                self.candidate_adapter_inventory_sha256,
            ),
            (
                "candidate_io_bridge_inventory_sha256",
                self.candidate_io_bridge_inventory_sha256,
            ),
            (
                "provider_transport_source_sha256",
                self.provider_transport_source_sha256,
            ),
            (
                "qualification_controller_source_sha256",
                self.qualification_controller_source_sha256,
            ),
            (
                "qualification_tool_source_sha256",
                self.qualification_tool_source_sha256,
            ),
            (
                "candidate_manifest_schema_sha256",
                self.candidate_manifest_schema_sha256,
            ),
            (
                "candidate_authorization_schema_sha256",
                self.candidate_authorization_schema_sha256,
            ),
            (
                "candidate_output_schema_sha256",
                self.candidate_output_schema_sha256,
            ),
            (
                "qualification_protocol_sha256",
                self.qualification_protocol_sha256,
            ),
        )
        expected = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-SOURCE-SEALS-V1\0",
            projection,
        )
        if self.candidate_source_seals_sha256 != expected:
            _fail()


def _create_candidate_source_seals_v1(
    **values: object,
) -> CandidateSourceSealsV1:
    return _private_construct(CandidateSourceSealsV1, values)  # type: ignore[return-value]


__all__ = (
    "CandidateObservationStartupAuthorityV1",
    "CandidateQualificationAppendReceiptV1",
    "CandidateQualificationCommitReceiptV1",
    "CandidateQualificationOutputWriterV1",
    "CandidateSourceSealCollectionAuthorityV1",
    "CandidateSourceSealsV1",
    "DurableExpertAppendReceiptV1",
    "DurableExpertEmergencyReceiptV1",
    "DurableExpertTerminalReceiptV1",
    "ExpertEmergencyAppendPermitV1",
    "ExpertEnvironmentCollectionAuthorityV1",
    "ExpertJournalAppendPermitV1",
    "ExpertJournalPurgeCapabilityV1",
    "ExpertJournalReadCapabilityV1",
    "ExpertJournalRootAuthorityV1",
    "ExpertJournalScanSummaryV1",
    "ExpertJournalTerminalPermitV1",
    "ExpertJournalWriteCapabilityV1",
    "ExpertLiveAuthorizationDenied",
    "ExpertPhysicalFileIdentityV1",
    "ExpertPrewriteCapacityError",
    "ExpertPurgeReportV1",
    "ExpertReplayAccessDenied",
    "ExpertReplayConstructionAuthorityV1",
    "SportradarCandidatePreparedReadV1",
)
