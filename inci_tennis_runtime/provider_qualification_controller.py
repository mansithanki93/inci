"""Owner-frame controls for the unregistered Sportradar candidate."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import re
import uuid

from inci_tennis_adapters.candidate_contracts import (
    CandidateParserEvidenceV1,
    CandidateProviderBindingV1,
    CandidateQualificationDecisionV1,
    CandidateQuotaClosureV1,
    candidate_binding_projection,
    candidate_decision_projection,
    candidate_quota_projection,
    candidate_quotas_projection,
    candidate_usage_projection,
)
from inci_tennis_adapters.registry import (
    normalize_sportradar_candidate_raw,
)
from inci_tennis_adapters.sportradar_tennis_v3 import (
    inspect_sportradar_candidate_capture,
)
from inci_tennis_expert.contracts import (
    BindingUniverse,
    ExpertObservationDraftV1,
    TennisState,
    canonical_expert_bytes,
)
from inci_tennis_io import facade
from inci_tennis_io.ports import (
    CandidateQualificationAppendReceiptV1,
    CandidateQualificationOutputWriterV1,
    CandidateSourceSealsV1,
)
from inci_tennis_io.provider_readonly import (
    SPORTRADAR_CANDIDATE_USAGE,
    ValidatedCandidateOfflineArtifactsV1,
    make_sportradar_candidate_evidence_mismatch_decision,
    make_sportradar_candidate_eligible_decision,
    make_sportradar_candidate_offline_denial,
)
from tennis_v1.entitlements import QualifiedProviderBinding
from tennis_v1.events import CapturedInput, PersistedEvent, RecordKind
from tennis_v1.sequencer import EventRuntime


_SAFE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z"
_SHA256_PATTERN = r"[0-9a-f]{64}\Z"
_SESSION_NAMESPACE = uuid.UUID("8f4c1777-5fea-521a-aaab-60afdc79e328")


class CandidateQualificationControllerError(ValueError):
    """A fixed candidate-controller contract denial."""

    def __init__(self) -> None:
        super().__init__("candidate_qualification_controller_invalid")


def _fail() -> None:
    raise CandidateQualificationControllerError()


def _safe_id(value: object) -> str:
    if (
        type(value) is not str
        or re.fullmatch(_SAFE_ID_PATTERN, value, flags=re.ASCII) is None
    ):
        _fail()
    return value


def _digest(value: object) -> str:
    if (
        type(value) is not str
        or re.fullmatch(_SHA256_PATTERN, value, flags=re.ASCII) is None
    ):
        _fail()
    return value


def _candidate_digest(domain: bytes, projection: object) -> str:
    try:
        return sha256(
            domain + canonical_expert_bytes(projection)
        ).hexdigest()
    except (TypeError, ValueError):
        _fail()
    raise AssertionError


def _utc_datetime(value: object) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        _fail()
    return value


def _require_production_binding(
    value: QualifiedProviderBinding,
) -> None:
    if type(value) is not QualifiedProviderBinding:
        raise TypeError("exact QualifiedProviderBinding required")
    if value.provider_id != "sportradar":
        _fail()
    for field_value in (
        value.provider_id,
        value.product_tier,
        value.source_lineage_id,
    ):
        _safe_id(field_value)
    for field_value in (
        value.entitlement_id_sha256,
        value.manifest_file_sha256,
        value.manifest_canonical_sha256,
        value.qualification_artifact_sha256,
        value.permission_artifact_sha256,
        value.qualification_trace_sha256,
        value.adapter_code_sha256,
        value.auth_contract_sha256,
        value.quota_contract_sha256,
    ):
        _digest(field_value)
    values = tuple(
        _utc_datetime(item)
        for item in (
            value.session_end_utc,
            value.required_retention_until,
            value.access_expires_at,
            value.analysis_expires_at,
            value.qualified_until,
        )
    )
    session_end, retention, access, analysis, qualified = values
    if not (
        session_end <= access <= analysis
        and session_end < retention <= analysis
        and session_end <= qualified <= analysis
    ):
        _fail()


def _require_universe(value: BindingUniverse) -> None:
    if type(value) is not BindingUniverse:
        raise TypeError("exact BindingUniverse required")
    try:
        BindingUniverse.__post_init__(value)
    except (TypeError, ValueError):
        _fail()


def _require_prior(value: TennisState | None) -> None:
    if value is None:
        return
    if type(value) is not TennisState:
        raise TypeError("exact TennisState or None required")
    try:
        TennisState.__post_init__(value)
    except (TypeError, ValueError):
        _fail()


def _require_offline_artifacts(
    value: ValidatedCandidateOfflineArtifactsV1,
) -> None:
    if type(value) is not ValidatedCandidateOfflineArtifactsV1:
        raise TypeError("exact validated candidate artifacts required")
    try:
        ValidatedCandidateOfflineArtifactsV1.__post_init__(value)
    except (TypeError, ValueError):
        _fail()


def _require_source_seals(value: CandidateSourceSealsV1) -> None:
    if type(value) is not CandidateSourceSealsV1:
        raise TypeError("exact candidate source seals required")
    try:
        value._validate()
    except (TypeError, ValueError):
        _fail()


def _strata_projection(
    artifacts: ValidatedCandidateOfflineArtifactsV1,
) -> tuple[tuple[tuple[str, object], ...], ...]:
    return tuple(
        (
            ("sport", item.stratum.sport),
            ("tour", item.stratum.tour),
            ("competition_tier", item.stratum.competition_tier),
            ("match_format", item.stratum.match_format),
            ("round_code", item.stratum.round_code),
            ("matches", item.matches),
        )
        for item in artifacts.required_strata
    )


def evaluate_sportradar_candidate_offline(
    *,
    artifacts: ValidatedCandidateOfflineArtifactsV1,
    source_seals: CandidateSourceSealsV1,
) -> CandidateQualificationDecisionV1:
    """Build candidate-only quota, binding, and decision values."""

    _require_offline_artifacts(artifacts)
    _require_source_seals(source_seals)
    try:
        offline_denial = make_sportradar_candidate_offline_denial(
            artifacts=artifacts,
        )
        if offline_denial is not None:
            candidate_decision_projection(offline_denial)
            return offline_denial
        quota = CandidateQuotaClosureV1._create(
            schema_version=1,
            usage=SPORTRADAR_CANDIDATE_USAGE,
            declared=artifacts.declared_quotas,
            demand=artifacts.demand_quotas,
            requested_matches=len(
                artifacts.requested_provider_match_ids
            ),
            duration_seconds=artifacts.duration_seconds,
            polling_interval_seconds=10,
            maximum_candidate_trace_bytes=(
                artifacts.maximum_candidate_trace_bytes
            ),
        )
        candidate_quota_projection(quota)
        strata = _strata_projection(artifacts)
        authorization_evidence_sha256 = _candidate_digest(
            (
                b"INCI-SPORTRADAR-CANDIDATE-"
                b"AUTHORIZATION-EVIDENCE-V1\0"
            ),
            (
                (
                    "candidate_source_seals_sha256",
                    source_seals.candidate_source_seals_sha256,
                ),
                (
                    "permission_artifact_sha256",
                    artifacts.permission_artifact_sha256,
                ),
                ("manifest_core_sha256", artifacts.manifest_core_sha256),
                (
                    "binding_manifest_sha256",
                    artifacts.binding_manifest_sha256,
                ),
                (
                    "binding_review_sha256",
                    artifacts.binding_review_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    artifacts.requested_provider_match_ids,
                ),
                (
                    "required_candidate_capabilities",
                    artifacts.required_candidate_capabilities,
                ),
                ("required_strata", strata),
                ("duration_seconds", artifacts.duration_seconds),
                (
                    "usage",
                    candidate_usage_projection(
                        SPORTRADAR_CANDIDATE_USAGE
                    ),
                ),
                (
                    "declared_quotas",
                    candidate_quotas_projection(
                        artifacts.declared_quotas
                    ),
                ),
            ),
        )
        if (
            authorization_evidence_sha256
            != artifacts.authorization_evidence_sha256
        ):
            decision = (
                make_sportradar_candidate_evidence_mismatch_decision()
            )
            candidate_decision_projection(decision)
            return decision

        request_sha256 = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-RESEARCH-REQUEST-V1\0",
            (
                ("intended_use", "private_paper_evaluation"),
                (
                    "session_start_wall_ns",
                    artifacts.session_start_wall_ns,
                ),
                (
                    "session_end_wall_ns",
                    artifacts.session_end_wall_ns,
                ),
                (
                    "required_retention_until_ns",
                    artifacts.required_retention_until_ns,
                ),
                ("expiry_safety_margin_seconds", 60),
                (
                    "required_raw_retention_seconds",
                    (
                        artifacts.required_retention_until_ns
                        - artifacts.session_start_wall_ns
                    )
                    // 1_000_000_000,
                ),
                (
                    "requested_matches",
                    len(artifacts.requested_provider_match_ids),
                ),
                (
                    "required_candidate_capabilities",
                    artifacts.required_candidate_capabilities,
                ),
                ("required_strata", strata),
            ),
        )
        permission_scope_sha256 = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-PERMISSION-SCOPE-V1\0",
            (
                ("provider_id", "sportradar"),
                ("product_tier", artifacts.product_tier),
                ("terms_version", artifacts.terms_version),
                (
                    "permission_artifact_sha256",
                    artifacts.permission_artifact_sha256,
                ),
                ("intended_use", "private_paper_evaluation"),
                ("publication_allowed", False),
            ),
        )
        session_name_sha256 = _candidate_digest(
            b"INCI-SPORTRADAR-CANDIDATE-SESSION-NAME-V1\0",
            (
                (
                    "candidate_manifest_sha256",
                    artifacts.candidate_manifest_sha256,
                ),
                ("manifest_core_sha256", artifacts.manifest_core_sha256),
                (
                    "candidate_authorization_sha256",
                    artifacts.candidate_authorization_sha256,
                ),
                (
                    "candidate_source_seals_sha256",
                    source_seals.candidate_source_seals_sha256,
                ),
                ("quota_closure_sha256", quota.quota_closure_sha256),
                (
                    "match_binding_universe_sha256",
                    artifacts.universe.universe_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    artifacts.requested_provider_match_ids,
                ),
                (
                    "session_start_wall_ns",
                    artifacts.session_start_wall_ns,
                ),
                (
                    "session_end_wall_ns",
                    artifacts.session_end_wall_ns,
                ),
                (
                    "retention_delete_by_ns",
                    artifacts.required_retention_until_ns,
                ),
            ),
        )
        session_id = str(
            uuid.uuid5(_SESSION_NAMESPACE, session_name_sha256)
        )
        preobservation_trace_sha256 = _candidate_digest(
            (
                b"INCI-SPORTRADAR-CANDIDATE-"
                b"PREOBSERVATION-TRACE-V1\0"
            ),
            (
                ("trace_state", "empty_pre_observation"),
                ("session_id", session_id),
                (
                    "candidate_manifest_sha256",
                    artifacts.candidate_manifest_sha256,
                ),
                ("manifest_core_sha256", artifacts.manifest_core_sha256),
                (
                    "candidate_authorization_sha256",
                    artifacts.candidate_authorization_sha256,
                ),
                (
                    "candidate_source_seals_sha256",
                    source_seals.candidate_source_seals_sha256,
                ),
                ("quota_closure_sha256", quota.quota_closure_sha256),
                (
                    "match_binding_universe_sha256",
                    artifacts.universe.universe_sha256,
                ),
                (
                    "requested_provider_match_ids",
                    artifacts.requested_provider_match_ids,
                ),
                (
                    "retention_delete_by_ns",
                    artifacts.required_retention_until_ns,
                ),
            ),
        )
        binding = CandidateProviderBindingV1._create(
            schema_version=1,
            authority_scope="candidate_read_only_observation_only",
            provider_id="sportradar",
            product_tier=artifacts.product_tier,
            source_lineage_id=artifacts.source_lineage_id,
            candidate_manifest_sha256=(
                artifacts.candidate_manifest_sha256
            ),
            provider_manifest_canonical_sha256=(
                artifacts.manifest_core_sha256
            ),
            provider_source_lineage_sha256=(
                artifacts.provider_source_lineage_sha256
            ),
            candidate_authorization_sha256=(
                artifacts.candidate_authorization_sha256
            ),
            permission_artifact_sha256=(
                artifacts.permission_artifact_sha256
            ),
            match_binding_universe_sha256=(
                artifacts.universe.universe_sha256
            ),
            binding_raw_artifact_sha256=(
                artifacts.binding_manifest_sha256
            ),
            binding_review_artifact_sha256=(
                artifacts.binding_review_sha256
            ),
            candidate_source_seals_sha256=(
                source_seals.candidate_source_seals_sha256
            ),
            auth_contract_sha256=artifacts.auth_contract_sha256,
            quota_closure_sha256=quota.quota_closure_sha256,
            candidate_research_request_sha256=request_sha256,
            session_id=session_id,
            candidate_permission_scope_sha256=(
                permission_scope_sha256
            ),
            candidate_preobservation_trace_sha256=(
                preobservation_trace_sha256
            ),
            session_start_wall_ns=artifacts.session_start_wall_ns,
            session_end_wall_ns=artifacts.session_end_wall_ns,
            retention_delete_by_ns=(
                artifacts.required_retention_until_ns
            ),
            access_expires_at_ns=artifacts.access_expires_at_ns,
            analysis_expires_at_ns=artifacts.analysis_expires_at_ns,
        )
        candidate_binding_projection(binding)
        decision = make_sportradar_candidate_eligible_decision(
            binding=binding,
            quota=quota,
        )
        candidate_decision_projection(decision)
        return decision
    except CandidateQualificationControllerError:
        raise
    except (AttributeError, TypeError, ValueError):
        _fail()
    raise AssertionError


def _captured_parent_equal(
    captured: CapturedInput,
    durable_raw: PersistedEvent,
) -> bool:
    return (
        durable_raw.session_id == captured.session_id
        and durable_raw.event_type == captured.event_type
        and durable_raw.event_version == captured.event_version
        and durable_raw.source_kind is captured.source_kind
        and durable_raw.source_id == captured.source_id
        and durable_raw.source_entity_id == captured.source_entity_id
        and durable_raw.endpoint_id == captured.endpoint_id
        and durable_raw.endpoint_state is captured.endpoint_state
        and durable_raw.channel_id == captured.channel_id
        and durable_raw.channel_state is captured.channel_state
        and durable_raw.request_id == captured.request_id
        and durable_raw.request_id_state is captured.request_id_state
        and durable_raw.source_wall_ns == captured.source_wall_ns
        and durable_raw.source_generated_ns
        == captured.source_generated_ns
        and durable_raw.local_wall_ns == captured.local_wall_ns
        and durable_raw.local_monotonic_ns
        == captured.local_monotonic_ns
        and durable_raw.clock_uncertainty_ns
        == captured.clock_uncertainty_ns
        and durable_raw.connection_epoch == captured.connection_epoch
        and durable_raw.provider_sequence == captured.provider_sequence
        and durable_raw.content_type == captured.content_type
        and durable_raw.payload_encoding == captured.payload_encoding
        and durable_raw.payload_transform == captured.payload_transform
        and durable_raw.retention_delete_by_ns
        == captured.retention_delete_by_ns
        and durable_raw.payload == captured.payload
        and durable_raw.payload_sha256
        == sha256(captured.payload).hexdigest()
    )


def normalize_durable_sportradar_capture_for_qualification(
    *,
    runtime: EventRuntime,
    captured: CapturedInput,
    provider_binding: QualifiedProviderBinding,
    universe: BindingUniverse,
    prior: TennisState | None,
) -> tuple[ExpertObservationDraftV1, ...]:
    """Persist one RAW and normalize only its exact owner-frame return."""

    if type(runtime) is not EventRuntime:
        raise TypeError("exact EventRuntime required")
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    _require_production_binding(provider_binding)
    _require_universe(universe)
    _require_prior(prior)

    durable_raw = runtime.ingest(captured)
    if (
        type(durable_raw) is not PersistedEvent
        or durable_raw.record_kind is not RecordKind.RAW
        or durable_raw.journal_version != 1
        or durable_raw.parent_ingest_seq is not None
        or durable_raw.source_kind is not captured.source_kind
        or captured.source_kind.value != "provider"
        or not _captured_parent_equal(captured, durable_raw)
    ):
        _fail()
    return normalize_sportradar_candidate_raw(
        provider_binding=provider_binding,
        universe=universe,
        captured=captured,
        durable_raw=durable_raw,
        prior=prior,
    )


def record_sportradar_candidate_capture_for_qualification(
    *,
    writer: CandidateQualificationOutputWriterV1,
    permit_receipt: CandidateQualificationAppendReceiptV1,
    binding: CandidateProviderBindingV1,
    universe: BindingUniverse,
    captured: CapturedInput,
    prior: TennisState | None,
) -> tuple[
    CandidateParserEvidenceV1,
    CandidateQualificationAppendReceiptV1,
]:
    """Append+fsync one capture before inspecting that same object."""

    if type(writer) is not CandidateQualificationOutputWriterV1:
        raise TypeError("exact candidate output writer required")
    if type(permit_receipt) is not CandidateQualificationAppendReceiptV1:
        raise TypeError("exact candidate append receipt required")
    if type(binding) is not CandidateProviderBindingV1:
        raise TypeError("exact candidate binding required")
    if type(captured) is not CapturedInput:
        raise TypeError("exact CapturedInput required")
    _require_universe(universe)
    _require_prior(prior)
    try:
        binding._validate()
    except (AttributeError, TypeError, ValueError):
        _fail()

    retained_captured = captured
    capture_receipt = facade.append_sportradar_candidate_capture(
        writer,
        prior_receipt=permit_receipt,
        captured=retained_captured,
    )
    if type(capture_receipt) is not CandidateQualificationAppendReceiptV1:
        _fail()
    evidence = inspect_sportradar_candidate_capture(
        binding=binding,
        universe=universe,
        captured=retained_captured,
        prior=prior,
    )
    if type(evidence) is not CandidateParserEvidenceV1:
        _fail()
    parser_receipt = facade.append_sportradar_candidate_parser_result(
        writer,
        prior_receipt=capture_receipt,
        capture_receipt=capture_receipt,
        evidence_sha256=evidence.evidence_sha256,
        parser_outcome=evidence.parser_outcome,
        reason=evidence.reason,
        output_contract_sha256s=evidence.output_contract_sha256s,
        capabilities=evidence.capabilities,
        first_correction_epoch=evidence.first_correction_epoch,
        first_revision=evidence.first_revision,
        last_correction_epoch=evidence.last_correction_epoch,
        last_revision=evidence.last_revision,
    )
    if type(parser_receipt) is not CandidateQualificationAppendReceiptV1:
        _fail()
    return evidence, parser_receipt


__all__ = (
    "CandidateQualificationControllerError",
    "evaluate_sportradar_candidate_offline",
    "normalize_durable_sportradar_capture_for_qualification",
    "record_sportradar_candidate_capture_for_qualification",
)
