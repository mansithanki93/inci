"""Capability-free value contracts for offline provider qualification."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
import uuid

from inci_tennis_expert.contracts import canonical_expert_bytes
from tennis_v1.adapter_contract import AdapterUsagePlan, ProviderQuotas
from tennis_v1.entitlements import QualificationReason


_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_MAXIMUM_TRACE_BYTES = 268_435_456
_TRACE_BYTES_PER_ATTEMPT = 2_113_536
_TRACE_TERMINAL_BYTES = 4_096
_SHA256_PATTERN = r"[0-9a-f]{64}\Z"
_SAFE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"

REQUIRED_CANDIDATE_CAPABILITIES = (
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

_CANDIDATE_USAGE_PROJECTION = (
    ("startup_requests_fixed", 0),
    ("startup_requests_per_match", 1),
    ("steady_requests_per_minute_fixed", 0),
    ("steady_requests_per_minute_per_match", 6),
    ("resync_requests_per_match", 1),
    ("max_resyncs_per_match_per_hour", 2),
    ("max_connections", 1),
    ("subscriptions_per_match", 1),
)
_PARSER_OUTCOMES = frozenset({"accepted", "ignored", "rejected"})
_CANDIDATE_EVENT_TYPES = frozenset(
    {
        "sportradar_tennis_summary_v3",
        "sportradar_tennis_timeline_v3",
        "sportradar_tennis_transport_error_v1",
    }
)
_EXPERT_REJECT_REASONS = frozenset(
    {
        "normalizer_schema_unknown",
        "normalizer_payload_invalid",
        "normalizer_contract_violation",
        "normalizer_exception",
    }
)

_QUOTA_DOMAIN = b"INCI-SPORTRADAR-CANDIDATE-QUOTA-CLOSURE-V1\0"
_BINDING_DOMAIN = b"INCI-SPORTRADAR-CANDIDATE-BINDING-V1\0"
_DECISION_DOMAIN = b"INCI-SPORTRADAR-CANDIDATE-DECISION-V1\0"
_PARSER_EVIDENCE_DOMAIN = (
    b"INCI-SPORTRADAR-CANDIDATE-PARSER-EVIDENCE-V1\0"
)


def _fail() -> None:
    raise ValueError("candidate_contract_invalid")


def _exact_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_SIGNED_64,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
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


def _safe_id(value: object) -> str:
    text = _exact_text(value)
    if re.fullmatch(_SAFE_ID_PATTERN, text, flags=re.ASCII) is None:
        _fail()
    return text


def _digest_text(value: object) -> str:
    text = _exact_text(value)
    if re.fullmatch(_SHA256_PATTERN, text, flags=re.ASCII) is None:
        _fail()
    return text


def _session_id(value: object) -> str:
    text = _exact_text(value)
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, TypeError, ValueError):
        _fail()
    if str(parsed) != text or parsed.version != 5:
        _fail()
    return text


def _candidate_digest(domain: bytes, projection: object) -> str:
    return sha256(domain + canonical_expert_bytes(projection)).hexdigest()


def _validate_usage(value: object) -> AdapterUsagePlan:
    if type(value) is not AdapterUsagePlan:
        _fail()
    for name in (
        "startup_requests_fixed",
        "startup_requests_per_match",
        "steady_requests_per_minute_fixed",
        "steady_requests_per_minute_per_match",
        "resync_requests_per_match",
        "max_resyncs_per_match_per_hour",
    ):
        _exact_int(getattr(value, name))
    for name in ("max_connections", "subscriptions_per_match"):
        _exact_int(getattr(value, name), minimum=1)
    if candidate_usage_projection(value) != _CANDIDATE_USAGE_PROJECTION:
        _fail()
    return value


def _validate_quotas(value: object) -> ProviderQuotas:
    if type(value) is not ProviderQuotas:
        _fail()
    for name in (
        "requests_per_rolling_60_seconds",
        "requests_per_utc_calendar_day",
        "requests_per_rolling_second",
        "max_connections",
        "max_subscriptions",
        "resync_requests_per_rolling_hour",
    ):
        _exact_int(getattr(value, name), minimum=1)
    return value


def _private_set(instance: object, **values: object) -> None:
    for name, value in values.items():
        object.__setattr__(instance, name, value)


class _PrivateCandidateContract:
    __slots__ = ()

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def __init_subclass__(cls, **kwargs: object) -> None:
        if cls.__bases__ != (_PrivateCandidateContract,):
            raise TypeError("candidate contracts cannot be subclassed")
        super().__init_subclass__(**kwargs)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"

    def __copy__(self):
        raise TypeError("candidate contracts cannot be copied")

    def __deepcopy__(self, _: object):
        raise TypeError("candidate contracts cannot be copied")

    def __reduce__(self):
        raise TypeError("candidate contracts cannot be pickled")

    def __reduce_ex__(self, _: int):
        raise TypeError("candidate contracts cannot be pickled")

    def __getstate__(self):
        raise TypeError("candidate contracts cannot be pickled")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CandidateQuotaClosureV1(_PrivateCandidateContract):
    schema_version: int
    usage: AdapterUsagePlan
    declared: ProviderQuotas
    demand: ProviderQuotas
    requested_matches: int
    duration_seconds: int
    polling_interval_seconds: int
    maximum_candidate_trace_bytes: int
    quota_closure_sha256: str

    @classmethod
    def _create(
        cls,
        *,
        schema_version: int,
        usage: AdapterUsagePlan,
        declared: ProviderQuotas,
        demand: ProviderQuotas,
        requested_matches: int,
        duration_seconds: int,
        polling_interval_seconds: int,
        maximum_candidate_trace_bytes: int,
    ) -> CandidateQuotaClosureV1:
        if cls is not CandidateQuotaClosureV1:
            _fail()
        instance = object.__new__(cls)
        _private_set(
            instance,
            schema_version=schema_version,
            usage=usage,
            declared=declared,
            demand=demand,
            requested_matches=requested_matches,
            duration_seconds=duration_seconds,
            polling_interval_seconds=polling_interval_seconds,
            maximum_candidate_trace_bytes=maximum_candidate_trace_bytes,
        )
        digest = _candidate_digest(
            _QUOTA_DOMAIN,
            candidate_quota_projection(instance),
        )
        object.__setattr__(instance, "quota_closure_sha256", digest)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if type(self) is not CandidateQuotaClosureV1:
            _fail()
        candidate_quota_projection(self)
        _digest_text(self.quota_closure_sha256)
        if self.quota_closure_sha256 != _candidate_digest(
            _QUOTA_DOMAIN,
            candidate_quota_projection(self),
        ):
            _fail()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CandidateProviderBindingV1(_PrivateCandidateContract):
    schema_version: int
    authority_scope: str
    provider_id: str
    product_tier: str
    source_lineage_id: str
    candidate_manifest_sha256: str
    provider_manifest_canonical_sha256: str
    provider_source_lineage_sha256: str
    candidate_authorization_sha256: str
    permission_artifact_sha256: str
    match_binding_universe_sha256: str
    binding_raw_artifact_sha256: str
    binding_review_artifact_sha256: str
    candidate_source_seals_sha256: str
    auth_contract_sha256: str
    quota_closure_sha256: str
    candidate_research_request_sha256: str
    session_id: str
    candidate_permission_scope_sha256: str
    candidate_preobservation_trace_sha256: str
    session_start_wall_ns: int
    session_end_wall_ns: int
    retention_delete_by_ns: int
    access_expires_at_ns: int
    analysis_expires_at_ns: int
    binding_sha256: str

    @classmethod
    def _create(
        cls,
        *,
        schema_version: int,
        authority_scope: str,
        provider_id: str,
        product_tier: str,
        source_lineage_id: str,
        candidate_manifest_sha256: str,
        provider_manifest_canonical_sha256: str,
        provider_source_lineage_sha256: str,
        candidate_authorization_sha256: str,
        permission_artifact_sha256: str,
        match_binding_universe_sha256: str,
        binding_raw_artifact_sha256: str,
        binding_review_artifact_sha256: str,
        candidate_source_seals_sha256: str,
        auth_contract_sha256: str,
        quota_closure_sha256: str,
        candidate_research_request_sha256: str,
        session_id: str,
        candidate_permission_scope_sha256: str,
        candidate_preobservation_trace_sha256: str,
        session_start_wall_ns: int,
        session_end_wall_ns: int,
        retention_delete_by_ns: int,
        access_expires_at_ns: int,
        analysis_expires_at_ns: int,
    ) -> CandidateProviderBindingV1:
        if cls is not CandidateProviderBindingV1:
            _fail()
        instance = object.__new__(cls)
        _private_set(
            instance,
            schema_version=schema_version,
            authority_scope=authority_scope,
            provider_id=provider_id,
            product_tier=product_tier,
            source_lineage_id=source_lineage_id,
            candidate_manifest_sha256=candidate_manifest_sha256,
            provider_manifest_canonical_sha256=(
                provider_manifest_canonical_sha256
            ),
            provider_source_lineage_sha256=(
                provider_source_lineage_sha256
            ),
            candidate_authorization_sha256=candidate_authorization_sha256,
            permission_artifact_sha256=permission_artifact_sha256,
            match_binding_universe_sha256=match_binding_universe_sha256,
            binding_raw_artifact_sha256=binding_raw_artifact_sha256,
            binding_review_artifact_sha256=binding_review_artifact_sha256,
            candidate_source_seals_sha256=candidate_source_seals_sha256,
            auth_contract_sha256=auth_contract_sha256,
            quota_closure_sha256=quota_closure_sha256,
            candidate_research_request_sha256=(
                candidate_research_request_sha256
            ),
            session_id=session_id,
            candidate_permission_scope_sha256=(
                candidate_permission_scope_sha256
            ),
            candidate_preobservation_trace_sha256=(
                candidate_preobservation_trace_sha256
            ),
            session_start_wall_ns=session_start_wall_ns,
            session_end_wall_ns=session_end_wall_ns,
            retention_delete_by_ns=retention_delete_by_ns,
            access_expires_at_ns=access_expires_at_ns,
            analysis_expires_at_ns=analysis_expires_at_ns,
        )
        digest = _candidate_digest(
            _BINDING_DOMAIN,
            candidate_binding_projection(instance),
        )
        object.__setattr__(instance, "binding_sha256", digest)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if type(self) is not CandidateProviderBindingV1:
            _fail()
        candidate_binding_projection(self)
        _digest_text(self.binding_sha256)
        if self.binding_sha256 != _candidate_digest(
            _BINDING_DOMAIN,
            candidate_binding_projection(self),
        ):
            _fail()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CandidateQualificationDecisionV1(_PrivateCandidateContract):
    schema_version: int
    eligible_for_candidate_observation: bool
    reasons: tuple[QualificationReason, ...]
    binding: CandidateProviderBindingV1 | None
    quota: CandidateQuotaClosureV1 | None
    decision_sha256: str

    @classmethod
    def _create(
        cls,
        *,
        schema_version: int,
        eligible_for_candidate_observation: bool,
        reasons: tuple[QualificationReason, ...],
        binding: CandidateProviderBindingV1 | None,
        quota: CandidateQuotaClosureV1 | None,
    ) -> CandidateQualificationDecisionV1:
        if cls is not CandidateQualificationDecisionV1:
            _fail()
        if type(reasons) is not tuple or any(
            type(reason) is not QualificationReason for reason in reasons
        ):
            _fail()
        instance = object.__new__(cls)
        _private_set(
            instance,
            schema_version=schema_version,
            eligible_for_candidate_observation=(
                eligible_for_candidate_observation
            ),
            reasons=reasons,
            binding=binding,
            quota=quota,
        )
        digest = _candidate_digest(
            _DECISION_DOMAIN,
            candidate_decision_projection(instance),
        )
        object.__setattr__(instance, "decision_sha256", digest)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if type(self) is not CandidateQualificationDecisionV1:
            _fail()
        candidate_decision_projection(self)
        _digest_text(self.decision_sha256)
        if self.decision_sha256 != _candidate_digest(
            _DECISION_DOMAIN,
            candidate_decision_projection(self),
        ):
            _fail()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CandidateParserEvidenceV1(_PrivateCandidateContract):
    schema_version: int
    event_type: str
    event_version: int
    payload_sha256: str
    capture_envelope_sha256: str
    parser_outcome: str
    reason: str | None
    output_contract_sha256s: tuple[str, ...]
    capabilities: tuple[tuple[str, bool], ...]
    first_correction_epoch: int | None
    first_revision: int | None
    last_correction_epoch: int | None
    last_revision: int | None
    evidence_sha256: str

    @classmethod
    def _create(
        cls,
        *,
        schema_version: int,
        event_type: str,
        event_version: int,
        payload_sha256: str,
        capture_envelope_sha256: str,
        parser_outcome: str,
        reason: str | None,
        output_contract_sha256s: tuple[str, ...],
        capabilities: tuple[tuple[str, bool], ...],
        first_correction_epoch: int | None,
        first_revision: int | None,
        last_correction_epoch: int | None,
        last_revision: int | None,
    ) -> CandidateParserEvidenceV1:
        if cls is not CandidateParserEvidenceV1:
            _fail()
        instance = object.__new__(cls)
        _private_set(
            instance,
            schema_version=schema_version,
            event_type=event_type,
            event_version=event_version,
            payload_sha256=payload_sha256,
            capture_envelope_sha256=capture_envelope_sha256,
            parser_outcome=parser_outcome,
            reason=reason,
            output_contract_sha256s=output_contract_sha256s,
            capabilities=capabilities,
            first_correction_epoch=first_correction_epoch,
            first_revision=first_revision,
            last_correction_epoch=last_correction_epoch,
            last_revision=last_revision,
        )
        digest = _candidate_digest(
            _PARSER_EVIDENCE_DOMAIN,
            candidate_parser_evidence_projection(instance),
        )
        object.__setattr__(instance, "evidence_sha256", digest)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if type(self) is not CandidateParserEvidenceV1:
            _fail()
        candidate_parser_evidence_projection(self)
        _digest_text(self.evidence_sha256)
        if self.evidence_sha256 != _candidate_digest(
            _PARSER_EVIDENCE_DOMAIN,
            candidate_parser_evidence_projection(self),
        ):
            _fail()


def candidate_usage_projection(
    value: AdapterUsagePlan,
) -> tuple[tuple[str, int], ...]:
    if type(value) is not AdapterUsagePlan:
        _fail()
    for name in (
        "startup_requests_fixed",
        "startup_requests_per_match",
        "steady_requests_per_minute_fixed",
        "steady_requests_per_minute_per_match",
        "resync_requests_per_match",
        "max_resyncs_per_match_per_hour",
    ):
        _exact_int(getattr(value, name))
    for name in ("max_connections", "subscriptions_per_match"):
        _exact_int(getattr(value, name), minimum=1)
    return (
        ("startup_requests_fixed", value.startup_requests_fixed),
        ("startup_requests_per_match", value.startup_requests_per_match),
        (
            "steady_requests_per_minute_fixed",
            value.steady_requests_per_minute_fixed,
        ),
        (
            "steady_requests_per_minute_per_match",
            value.steady_requests_per_minute_per_match,
        ),
        ("resync_requests_per_match", value.resync_requests_per_match),
        (
            "max_resyncs_per_match_per_hour",
            value.max_resyncs_per_match_per_hour,
        ),
        ("max_connections", value.max_connections),
        ("subscriptions_per_match", value.subscriptions_per_match),
    )


def candidate_quotas_projection(
    value: ProviderQuotas,
) -> tuple[tuple[str, int], ...]:
    if type(value) is not ProviderQuotas:
        _fail()
    for name in (
        "requests_per_rolling_60_seconds",
        "requests_per_utc_calendar_day",
        "requests_per_rolling_second",
        "max_connections",
        "max_subscriptions",
        "resync_requests_per_rolling_hour",
    ):
        _exact_int(getattr(value, name), minimum=1)
    return (
        (
            "requests_per_rolling_60_seconds",
            value.requests_per_rolling_60_seconds,
        ),
        (
            "requests_per_utc_calendar_day",
            value.requests_per_utc_calendar_day,
        ),
        ("requests_per_rolling_second", value.requests_per_rolling_second),
        ("max_connections", value.max_connections),
        ("max_subscriptions", value.max_subscriptions),
        (
            "resync_requests_per_rolling_hour",
            value.resync_requests_per_rolling_hour,
        ),
    )


def candidate_quota_projection(
    value: CandidateQuotaClosureV1,
) -> tuple[tuple[str, object], ...]:
    if type(value) is not CandidateQuotaClosureV1:
        _fail()
    _exact_int(value.schema_version, minimum=1, maximum=1)
    usage = _validate_usage(value.usage)
    declared = _validate_quotas(value.declared)
    demand = _validate_quotas(value.demand)
    matches = _exact_int(value.requested_matches, minimum=1, maximum=10)
    duration = _exact_int(value.duration_seconds, minimum=1, maximum=3_600)
    if _exact_int(
        value.polling_interval_seconds,
        minimum=10,
        maximum=10,
    ) != 10:
        _fail()
    polls = (duration - 1) // 10
    resyncs = min(2, polls)
    attempts = matches * (1 + polls + resyncs)
    expected_trace_bytes = (
        attempts * _TRACE_BYTES_PER_ATTEMPT + _TRACE_TERMINAL_BYTES
    )
    trace_bytes = _exact_int(value.maximum_candidate_trace_bytes, minimum=1)
    if (
        trace_bytes != expected_trace_bytes
        or expected_trace_bytes > _MAXIMUM_TRACE_BYTES
    ):
        _fail()
    for name, amount in candidate_quotas_projection(demand):
        if amount > getattr(declared, name):
            _fail()
    worst_cluster = 9 * matches
    if (
        demand.requests_per_rolling_60_seconds != worst_cluster
        or demand.requests_per_rolling_second != worst_cluster
        or demand.max_connections != 1
        or demand.max_subscriptions != matches
        or demand.resync_requests_per_rolling_hour != 2 * matches
    ):
        _fail()
    return (
        ("schema_version", value.schema_version),
        ("usage", candidate_usage_projection(usage)),
        ("declared", candidate_quotas_projection(declared)),
        ("demand", candidate_quotas_projection(demand)),
        ("requested_matches", matches),
        ("duration_seconds", duration),
        ("polling_interval_seconds", value.polling_interval_seconds),
        (
            "maximum_candidate_trace_bytes",
            value.maximum_candidate_trace_bytes,
        ),
    )


def candidate_binding_projection(
    value: CandidateProviderBindingV1,
) -> tuple[tuple[str, object], ...]:
    if type(value) is not CandidateProviderBindingV1:
        _fail()
    if (
        _exact_int(value.schema_version, minimum=1, maximum=1) != 1
        or value.authority_scope
        != "candidate_read_only_observation_only"
        or value.provider_id != "sportradar"
    ):
        _fail()
    _safe_id(value.product_tier)
    _safe_id(value.source_lineage_id)
    for digest in (
        value.candidate_manifest_sha256,
        value.provider_manifest_canonical_sha256,
        value.provider_source_lineage_sha256,
        value.candidate_authorization_sha256,
        value.permission_artifact_sha256,
        value.match_binding_universe_sha256,
        value.binding_raw_artifact_sha256,
        value.binding_review_artifact_sha256,
        value.candidate_source_seals_sha256,
        value.auth_contract_sha256,
        value.quota_closure_sha256,
        value.candidate_research_request_sha256,
        value.candidate_permission_scope_sha256,
        value.candidate_preobservation_trace_sha256,
    ):
        _digest_text(digest)
    _session_id(value.session_id)
    start = _exact_int(value.session_start_wall_ns)
    end = _exact_int(value.session_end_wall_ns, minimum=1)
    retention = _exact_int(value.retention_delete_by_ns, minimum=1)
    access = _exact_int(value.access_expires_at_ns, minimum=1)
    analysis = _exact_int(value.analysis_expires_at_ns, minimum=1)
    if not (
        start < end
        and end <= access <= analysis
        and end < retention <= analysis
    ):
        _fail()
    return (
        ("schema_version", value.schema_version),
        ("authority_scope", value.authority_scope),
        ("provider_id", value.provider_id),
        ("product_tier", value.product_tier),
        ("source_lineage_id", value.source_lineage_id),
        ("candidate_manifest_sha256", value.candidate_manifest_sha256),
        (
            "provider_manifest_canonical_sha256",
            value.provider_manifest_canonical_sha256,
        ),
        (
            "provider_source_lineage_sha256",
            value.provider_source_lineage_sha256,
        ),
        (
            "candidate_authorization_sha256",
            value.candidate_authorization_sha256,
        ),
        ("permission_artifact_sha256", value.permission_artifact_sha256),
        (
            "match_binding_universe_sha256",
            value.match_binding_universe_sha256,
        ),
        (
            "binding_raw_artifact_sha256",
            value.binding_raw_artifact_sha256,
        ),
        (
            "binding_review_artifact_sha256",
            value.binding_review_artifact_sha256,
        ),
        (
            "candidate_source_seals_sha256",
            value.candidate_source_seals_sha256,
        ),
        ("auth_contract_sha256", value.auth_contract_sha256),
        ("quota_closure_sha256", value.quota_closure_sha256),
        (
            "candidate_research_request_sha256",
            value.candidate_research_request_sha256,
        ),
        ("session_id", value.session_id),
        (
            "candidate_permission_scope_sha256",
            value.candidate_permission_scope_sha256,
        ),
        (
            "candidate_preobservation_trace_sha256",
            value.candidate_preobservation_trace_sha256,
        ),
        ("session_start_wall_ns", start),
        ("session_end_wall_ns", end),
        ("retention_delete_by_ns", retention),
        ("access_expires_at_ns", access),
        ("analysis_expires_at_ns", analysis),
    )


def candidate_decision_projection(
    value: CandidateQualificationDecisionV1,
) -> tuple[tuple[str, object], ...]:
    if type(value) is not CandidateQualificationDecisionV1:
        _fail()
    _exact_int(value.schema_version, minimum=1, maximum=1)
    eligible = _exact_bool(value.eligible_for_candidate_observation)
    if (
        type(value.reasons) is not tuple
        or not value.reasons
        or any(type(reason) is not QualificationReason for reason in value.reasons)
        or tuple(sorted(reason.value for reason in value.reasons))
        != tuple(reason.value for reason in value.reasons)
        or len(set(value.reasons)) != len(value.reasons)
    ):
        _fail()
    if eligible:
        if (
            value.reasons != (QualificationReason.ELIGIBLE,)
            or type(value.binding) is not CandidateProviderBindingV1
            or type(value.quota) is not CandidateQuotaClosureV1
        ):
            _fail()
        value.binding._validate()
        value.quota._validate()
        if (
            value.binding.quota_closure_sha256
            != value.quota.quota_closure_sha256
        ):
            _fail()
    elif (
        QualificationReason.ELIGIBLE in value.reasons
        or value.binding is not None
        or value.quota is not None
    ):
        _fail()
    return (
        ("schema_version", value.schema_version),
        ("eligible_for_candidate_observation", eligible),
        ("reasons", tuple(reason.value for reason in value.reasons)),
        (
            "binding_sha256",
            None if value.binding is None else value.binding.binding_sha256,
        ),
        (
            "quota_closure_sha256",
            (
                None
                if value.quota is None
                else value.quota.quota_closure_sha256
            ),
        ),
    )


def candidate_parser_evidence_projection(
    value: CandidateParserEvidenceV1,
) -> tuple[tuple[str, object], ...]:
    if type(value) is not CandidateParserEvidenceV1:
        _fail()
    event_type = _exact_text(value.event_type)
    parser_outcome = _exact_text(value.parser_outcome)
    if (
        _exact_int(value.schema_version, minimum=1, maximum=1) != 1
        or event_type not in _CANDIDATE_EVENT_TYPES
        or _exact_int(value.event_version, minimum=1, maximum=1) != 1
        or parser_outcome not in _PARSER_OUTCOMES
    ):
        _fail()
    _digest_text(value.payload_sha256)
    _digest_text(value.capture_envelope_sha256)
    if (
        type(value.output_contract_sha256s) is not tuple
        or any(
            type(item) is not str
            or re.fullmatch(_SHA256_PATTERN, item, flags=re.ASCII) is None
            for item in value.output_contract_sha256s
        )
    ):
        _fail()
    if (
        type(value.capabilities) is not tuple
        or len(value.capabilities) != len(REQUIRED_CANDIDATE_CAPABILITIES)
    ):
        _fail()
    names: list[str] = []
    for item in value.capabilities:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) is not bool
        ):
            _fail()
        names.append(item[0])
    if tuple(names) != REQUIRED_CANDIDATE_CAPABILITIES:
        _fail()
    coordinates = (
        value.first_correction_epoch,
        value.first_revision,
        value.last_correction_epoch,
        value.last_revision,
    )
    if value.parser_outcome == "accepted":
        if (
            value.reason is not None
            or value.event_type
            == "sportradar_tennis_transport_error_v1"
            or not 1 <= len(value.output_contract_sha256s) <= 64
            or any(item is None for item in coordinates)
        ):
            _fail()
        assert all(item is not None for item in coordinates)
        first_epoch = _exact_int(value.first_correction_epoch)
        first_revision = _exact_int(value.first_revision)
        last_epoch = _exact_int(value.last_correction_epoch)
        last_revision = _exact_int(value.last_revision)
        if (last_epoch, last_revision) < (first_epoch, first_revision):
            _fail()
        if (
            value.event_type == "sportradar_tennis_timeline_v3"
            and (first_revision < 1 or last_revision < 1)
        ):
            _fail()
        if (
            value.event_type == "sportradar_tennis_summary_v3"
            and len(value.output_contract_sha256s) != 1
        ):
            _fail()
    else:
        if value.output_contract_sha256s or any(
            item is not None for item in coordinates
        ):
            _fail()
        if type(value.reason) is not str:
            _fail()
        if value.parser_outcome == "ignored":
            if (
                value.event_type
                != "sportradar_tennis_transport_error_v1"
                or value.reason != "event_not_relevant"
            ):
                _fail()
        elif value.reason not in _EXPERT_REJECT_REASONS:
            _fail()
    return (
        ("schema_version", value.schema_version),
        ("event_type", value.event_type),
        ("event_version", value.event_version),
        ("payload_sha256", value.payload_sha256),
        ("capture_envelope_sha256", value.capture_envelope_sha256),
        ("parser_outcome", value.parser_outcome),
        ("reason", value.reason),
        ("output_contract_sha256s", value.output_contract_sha256s),
        ("capabilities", value.capabilities),
        ("first_correction_epoch", value.first_correction_epoch),
        ("first_revision", value.first_revision),
        ("last_correction_epoch", value.last_correction_epoch),
        ("last_revision", value.last_revision),
    )


__all__ = (
    "CandidateParserEvidenceV1",
    "CandidateProviderBindingV1",
    "CandidateQualificationDecisionV1",
    "CandidateQuotaClosureV1",
    "REQUIRED_CANDIDATE_CAPABILITIES",
    "candidate_binding_projection",
    "candidate_decision_projection",
    "candidate_parser_evidence_projection",
    "candidate_quota_projection",
    "candidate_quotas_projection",
    "candidate_usage_projection",
)
