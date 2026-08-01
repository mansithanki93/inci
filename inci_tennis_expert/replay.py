from __future__ import annotations

from hashlib import sha256

from tennis_v1.codec import canonical_record_sha256
from tennis_v1.events import PersistedEvent, RecordKind

from .contracts import (
    BindingUniverse,
    EvidenceReplayContextV1,
    ExpertCapacityProofV1,
    ExpertCurrentEnvironmentV1,
    ExpertEventSchemaBundleV1,
    ExpertEventKindV1,
    ExpertJournalGroupV1,
    ExpertJournalCursorV1,
    ExpertJournalRecordV1,
    ExpertJournalScanIssueV1,
    ExpertJournalScanSummaryV1,
    ExpertNormalizerRegistryV1,
    ExpertParentEvidenceV1,
    ExpertPayloadDescriptorV1,
    ExpertReplayAccumulatorV1,
    ExpertReplayMismatchV1,
    ExpertReplayResultV1,
    ExpertSessionManifestV1,
    ExpertSessionTerminalV1,
    ExpertStateV1,
    ExpertStructuralSchemaBundleV1,
    ExpertTerminalReasonV1,
    ExpertTraceStepV1,
    RetentionReplayAuthorizationV1,
    SyncPolicy,
    canonical_expert_bytes,
    compute_expert_journal_group_sha256,
    compute_expert_journal_record_sha256,
    compute_expert_session_terminal_sha256,
    compute_expert_trace_step_sha256,
    compute_retention_replay_authorization_sha256,
    expert_event_schema_resource_sha256,
    expert_contract_sha256,
    expert_state_sha256,
    expert_trace_seed_sha256,
)
from .journal_codec import (
    ExpertJournalCodecError,
    decode_expert_event_payload,
)
from .match_binding import binding_universe_sha256
from .reducer import initial_expert_state, reduce_expert_parent
from .observation import normalize_expert_parent
from .synchronizer import synchronization_session_from_artifacts


_MISMATCH_PRECEDENCE: tuple[ExpertReplayMismatchV1, ...] = (
    ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
    ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
    ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
    ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
    ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
    ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
    ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
    ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
    ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
    ExpertReplayMismatchV1.RETENTION_BINDING_MISMATCH,
    ExpertReplayMismatchV1.COMPANION_SCAN_INVALID,
    ExpertReplayMismatchV1.COMPANION_MANIFEST_MISMATCH,
    ExpertReplayMismatchV1.EXPERT_SEQUENCE_MISMATCH,
    ExpertReplayMismatchV1.PARENT_MISSING,
    ExpertReplayMismatchV1.PARENT_EXTRA,
    ExpertReplayMismatchV1.PARENT_ORDER_MISMATCH,
    ExpertReplayMismatchV1.PARENT_KIND_MISMATCH,
    ExpertReplayMismatchV1.PARENT_DIGEST_MISMATCH,
    ExpertReplayMismatchV1.PARENT_GROUP_SHAPE_MISMATCH,
    ExpertReplayMismatchV1.PRIOR_RECORD_CHAIN_MISMATCH,
    ExpertReplayMismatchV1.PRIOR_STATE_MISMATCH,
    ExpertReplayMismatchV1.EVENT_SCHEMA_UNPINNED,
    ExpertReplayMismatchV1.RECORD_DIGEST_MISMATCH,
    ExpertReplayMismatchV1.PAYLOAD_DESCRIPTOR_MISMATCH,
    ExpertReplayMismatchV1.PAYLOAD_BYTES_MISMATCH,
    ExpertReplayMismatchV1.NORMALIZED_OBSERVATION_MISMATCH,
    ExpertReplayMismatchV1.REDUCTION_MISMATCH,
    ExpertReplayMismatchV1.POST_STATE_MISMATCH,
    ExpertReplayMismatchV1.TRACE_MISMATCH,
    ExpertReplayMismatchV1.TERMINAL_MISSING,
    ExpertReplayMismatchV1.TERMINAL_REASON_MISMATCH,
    ExpertReplayMismatchV1.TERMINAL_COUNT_MISMATCH,
    ExpertReplayMismatchV1.TERMINAL_PROVENANCE_MISMATCH,
    ExpertReplayMismatchV1.TERMINAL_STATE_MISMATCH,
    ExpertReplayMismatchV1.TERMINAL_TRACE_MISMATCH,
)


def _require_exact(value: object, expected: type[object], name: str) -> None:
    if type(value) is not expected:
        raise TypeError(name)


def _authorization_digest_is_exact(
    authorization: RetentionReplayAuthorizationV1,
) -> bool:
    values = {
        "schema_version": authorization.schema_version,
        "session_id": authorization.session_id,
        "authorization_sequence": authorization.authorization_sequence,
        "authorized_operation": authorization.authorized_operation,
        "expected_parent_ingest_seq": (
            authorization.expected_parent_ingest_seq
        ),
        "evidence_session_manifest_sha256": (
            authorization.evidence_session_manifest_sha256
        ),
        "evidence_session_start_record_sha256": (
            authorization.evidence_session_start_record_sha256
        ),
        "evidence_terminal_record_sha256": (
            authorization.evidence_terminal_record_sha256
        ),
        "expert_manifest_sha256": (
            authorization.expert_manifest_sha256
        ),
        "retention_binding_sha256": (
            authorization.retention_binding_sha256
        ),
        "provider_request_binding_sha256": (
            authorization.provider_request_binding_sha256
        ),
        "permission_artifact_sha256": (
            authorization.permission_artifact_sha256
        ),
        "qualification_artifact_sha256": (
            authorization.qualification_artifact_sha256
        ),
        "qualification_trace_sha256": (
            authorization.qualification_trace_sha256
        ),
        "evidence_marker_identity": (
            authorization.evidence_marker_identity
        ),
        "evidence_wal_identity": authorization.evidence_wal_identity,
        "companion_marker_identity": (
            authorization.companion_marker_identity
        ),
        "companion_journal_identity": (
            authorization.companion_journal_identity
        ),
        "common_deadline_ns": authorization.common_deadline_ns,
        "final_sampled_wall_ns": authorization.final_sampled_wall_ns,
    }
    return authorization.authorization_sha256 == (
        compute_retention_replay_authorization_sha256(**values)
    )


def _validate_authorization_member_types(
    authorization: RetentionReplayAuthorizationV1,
) -> None:
    if type(authorization.schema_version) is not int:
        raise TypeError("authorization.schema_version")
    if type(authorization.session_id) is not str:
        raise TypeError("authorization.session_id")
    if type(authorization.authorization_sequence) is not int:
        raise TypeError("authorization.authorization_sequence")
    if type(authorization.authorized_operation) is not str:
        raise TypeError("authorization.authorized_operation")
    if (
        authorization.expected_parent_ingest_seq is not None
        and type(authorization.expected_parent_ingest_seq) is not int
    ):
        raise TypeError("authorization.expected_parent_ingest_seq")
    if type(authorization.common_deadline_ns) is not int:
        raise TypeError("authorization.common_deadline_ns")
    if type(authorization.final_sampled_wall_ns) is not int:
        raise TypeError("authorization.final_sampled_wall_ns")
    for name, value in (
        (
            "evidence_session_manifest_sha256",
            authorization.evidence_session_manifest_sha256,
        ),
        (
            "evidence_session_start_record_sha256",
            authorization.evidence_session_start_record_sha256,
        ),
        ("expert_manifest_sha256", authorization.expert_manifest_sha256),
        (
            "retention_binding_sha256",
            authorization.retention_binding_sha256,
        ),
        (
            "provider_request_binding_sha256",
            authorization.provider_request_binding_sha256,
        ),
        (
            "permission_artifact_sha256",
            authorization.permission_artifact_sha256,
        ),
        (
            "qualification_artifact_sha256",
            authorization.qualification_artifact_sha256,
        ),
        (
            "qualification_trace_sha256",
            authorization.qualification_trace_sha256,
        ),
        ("authorization_sha256", authorization.authorization_sha256),
    ):
        if type(value) is not str:
            raise TypeError(f"authorization.{name}")
    if (
        authorization.evidence_terminal_record_sha256 is not None
        and type(authorization.evidence_terminal_record_sha256) is not str
    ):
        raise TypeError("authorization.evidence_terminal_record_sha256")


def _manifest_evidence_relation_is_exact(
    manifest: ExpertSessionManifestV1,
    evidence: EvidenceReplayContextV1,
) -> bool:
    phase1 = evidence.session_manifest
    return (
        manifest.evidence_session_manifest_sha256
        == evidence.session_manifest_sha256
        and manifest.evidence_session_start_record_sha256
        == evidence.session_start_record_sha256
        and manifest.provider_id == phase1.provider_id
        and manifest.product_tier == phase1.product_tier
        and manifest.source_lineage_id == phase1.source_lineage_id
        and manifest.provider_manifest_file_sha256
        == phase1.provider_manifest_file_sha256
        and manifest.provider_manifest_canonical_sha256
        == phase1.provider_manifest_canonical_sha256
        and manifest.entitlement_id_sha256 == phase1.entitlement_id_sha256
        and manifest.permission_artifact_sha256
        == phase1.permission_artifact_sha256
        and manifest.qualification_artifact_sha256
        == phase1.qualification_artifact_sha256
        and manifest.qualification_trace_sha256
        == phase1.qualification_trace_sha256
    )


def _authorization_static_relation_is_exact(
    authorization: RetentionReplayAuthorizationV1,
    manifest: ExpertSessionManifestV1,
    evidence: EvidenceReplayContextV1,
    *,
    operation: str,
    sequence: int,
    expected_parent_ingest_seq: int | None,
) -> bool:
    return (
        authorization.schema_version == 1
        and authorization.authorized_operation == operation
        and authorization.authorization_sequence == sequence
        and authorization.expected_parent_ingest_seq
        == expected_parent_ingest_seq
        and authorization.evidence_session_manifest_sha256
        == evidence.session_manifest_sha256
        and authorization.evidence_session_start_record_sha256
        == evidence.session_start_record_sha256
        and authorization.evidence_terminal_record_sha256
        == evidence.evidence_terminal_record_sha256
        and authorization.expert_manifest_sha256
        == manifest.manifest_sha256
        and authorization.retention_binding_sha256
        == manifest.retention.retention_binding_sha256
        and authorization.provider_request_binding_sha256
        == manifest.provider_request_binding_sha256
        and authorization.permission_artifact_sha256
        == manifest.permission_artifact_sha256
        and authorization.qualification_artifact_sha256
        == manifest.qualification_artifact_sha256
        and authorization.qualification_trace_sha256
        == manifest.qualification_trace_sha256
    )


def _authorization_identities_are_exact(
    authorization: RetentionReplayAuthorizationV1,
    evidence: EvidenceReplayContextV1,
) -> bool:
    identities = (
        authorization.evidence_marker_identity,
        authorization.evidence_wal_identity,
        authorization.companion_marker_identity,
        authorization.companion_journal_identity,
    )
    try:
        for identity in identities:
            identity._validate()
    except BaseException:
        return False
    return (
        authorization.evidence_marker_identity
        == evidence.evidence_marker_identity
        and authorization.evidence_wal_identity
        == evidence.evidence_wal_identity
        and authorization.companion_marker_identity.session_anchor_sha256
        == authorization.companion_journal_identity.session_anchor_sha256
    )


def _retention_relation_is_exact(
    authorization: RetentionReplayAuthorizationV1,
    manifest: ExpertSessionManifestV1,
    evidence: EvidenceReplayContextV1,
) -> bool:
    phase1 = evidence.session_manifest
    retention = manifest.retention
    return (
        retention.session_id == manifest.session_id
        and retention.evidence_session_manifest_sha256
        == evidence.session_manifest_sha256
        and retention.provider_request_binding_sha256
        == manifest.provider_request_binding_sha256
        and retention.permission_artifact_sha256
        == phase1.permission_artifact_sha256
        and retention.qualification_artifact_sha256
        == phase1.qualification_artifact_sha256
        and retention.qualification_trace_sha256
        == phase1.qualification_trace_sha256
        and retention.retention_delete_by_ns
        == phase1.required_retention_until_ns
        and retention.access_expires_at_ns == phase1.access_expires_at_ns
        and retention.analysis_expires_at_ns == phase1.analysis_expires_at_ns
        and authorization.common_deadline_ns
        == retention.retention_delete_by_ns
    )


def _static_companion_manifest_is_decodable(
    manifest: ExpertSessionManifestV1,
) -> bool:
    expected = (
        (manifest.normalizers, ExpertNormalizerRegistryV1),
        (manifest.structural_schemas, ExpertStructuralSchemaBundleV1),
        (manifest.event_schemas, ExpertEventSchemaBundleV1),
        (manifest.capacity, ExpertCapacityProofV1),
    )
    try:
        for value, value_type in expected:
            if type(value) is not value_type:
                return False
            value_type.__post_init__(value)
        if type(manifest.artifact_pins) is not tuple:
            return False
    except BaseException:
        return False
    return True


def _companion_manifest_relations_are_exact(
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
    evidence: EvidenceReplayContextV1,
) -> bool:
    try:
        BindingUniverse.__post_init__(universe)
        SyncPolicy.__post_init__(policy)
        universe_sha256 = binding_universe_sha256(universe)
        policy_sha256 = expert_contract_sha256(policy)
        synchronization = synchronization_session_from_artifacts(
            universe,
            policy,
        )
        initial_sha256 = expert_contract_sha256(synchronization)
    except BaseException:
        return False
    provider = manifest.provider_domain
    if type(provider) is not type(manifest.provider_domain):
        return False
    binding_revisions = {
        item.revision_domain_id for item in universe.bindings
    }
    return (
        manifest.match_binding_universe_sha256 == universe_sha256
        and manifest.binding_raw_artifact_id == universe.raw_artifact_id
        and manifest.binding_raw_artifact_sha256
        == universe.raw_artifact_sha256
        and manifest.binding_review_artifact_id
        == universe.review.review_artifact_id
        and manifest.binding_review_artifact_sha256
        == universe.review.review_artifact_sha256
        and manifest.sync_policy_sha256 == policy_sha256
        and policy.universe_sha256 == universe_sha256
        and manifest.initial_synchronization_sha256 == initial_sha256
        and provider.phase1_session_manifest_sha256
        == evidence.session_manifest_sha256
        and provider.match_binding_universe_sha256 == universe_sha256
        and provider.provider_id == manifest.provider_id
        and provider.product_tier == manifest.product_tier
        and provider.source_lineage_id == manifest.source_lineage_id
        and provider.provider_manifest_canonical_sha256
        == manifest.provider_manifest_canonical_sha256
        and binding_revisions == {provider.revision_domain_id}
        and manifest.normalizers.registry_sha256
        == manifest.environment.normalizer_registry_sha256
        and manifest.structural_schemas.bundle_sha256
        == manifest.environment.structural_schema_bundle_sha256
        and manifest.event_schemas.bundle_sha256
        == manifest.environment.event_schema_bundle_sha256
        and manifest.capacity.match_binding_universe_sha256
        == universe_sha256
        and manifest.capacity.sync_policy_sha256 == policy_sha256
    )


def _fallback_initial_state(
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> ExpertStateV1:
    usable_policy = policy
    universe_sha256 = binding_universe_sha256(universe)
    if policy.universe_sha256 != universe_sha256:
        usable_policy = SyncPolicy(
            universe_sha256=universe_sha256,
            max_score_age_ns=policy.max_score_age_ns,
            max_book_age_ns=policy.max_book_age_ns,
            max_lifecycle_age_ns=policy.max_lifecycle_age_ns,
            max_score_book_skew_ns=policy.max_score_book_skew_ns,
            max_clock_uncertainty_ns=policy.max_clock_uncertainty_ns,
            large_book_move_threshold=policy.large_book_move_threshold,
            explanation_window_ns=policy.explanation_window_ns,
            minimum_close_horizon_ns=policy.minimum_close_horizon_ns,
        )
    synchronization = synchronization_session_from_artifacts(
        universe,
        usable_policy,
    )
    return ExpertStateV1(
        schema_version=1,
        session_id=manifest.session_id,
        expert_manifest_sha256=manifest.manifest_sha256,
        match_binding_universe_sha256=synchronization.universe_sha256,
        sync_policy_sha256=synchronization.sync_policy_sha256,
        initial_synchronization_sha256=expert_contract_sha256(
            synchronization
        ),
        synchronization=synchronization,
        rejected_parent_count=0,
        halted=False,
        halt_reason=None,
    )


def _genesis_cursor(
    manifest: ExpertSessionManifestV1,
    state: ExpertStateV1,
) -> ExpertJournalCursorV1:
    state_sha256 = expert_state_sha256(state)
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
        expert_state_sha256=state_sha256,
        expert_trace_sha256=expert_trace_seed_sha256(
            manifest.session_id,
            manifest.manifest_sha256,
            state_sha256,
        ),
    )


def _unchecked_accumulator(
    *,
    manifest: ExpertSessionManifestV1,
    current_environment: ExpertCurrentEnvironmentV1,
    evidence: EvidenceReplayContextV1,
    state: ExpertStateV1,
    cursor: ExpertJournalCursorV1,
    evidence_raw_count: int,
    evidence_derived_count: int,
    processed_parent_count: int,
    last_authorization_sequence: int,
    last_authorization_sha256: str,
    mismatch: ExpertReplayMismatchV1 | None,
) -> ExpertReplayAccumulatorV1:
    values = {
        "schema_version": 1,
        "manifest": manifest,
        "current_environment": current_environment,
        "evidence": evidence,
        "state": state,
        "cursor": cursor,
        "evidence_raw_count": evidence_raw_count,
        "evidence_derived_count": evidence_derived_count,
        "processed_parent_count": processed_parent_count,
        "last_authorization_sequence": last_authorization_sequence,
        "last_authorization_sha256": last_authorization_sha256,
        "mismatch": mismatch,
    }
    try:
        return ExpertReplayAccumulatorV1(**values)
    except BaseException:
        result = object.__new__(ExpertReplayAccumulatorV1)
        for name, value in values.items():
            object.__setattr__(result, name, value)
        return result


def _payload_contract_name(event_kind: ExpertEventKindV1) -> str:
    return {
        ExpertEventKindV1.SYNCHRONIZATION_APPLIED: (
            "ExpertSynchronizationAppliedPayloadV1"
        ),
        ExpertEventKindV1.OBSERVATION_IGNORED: (
            "ExpertObservationIgnoredPayloadV1"
        ),
        ExpertEventKindV1.OBSERVATION_REJECTED: (
            "ExpertObservationRejectedPayloadV1"
        ),
    }[event_kind]


def _expected_group(
    accumulator: ExpertReplayAccumulatorV1,
    parent: PersistedEvent,
) -> tuple[
    ExpertJournalGroupV1,
    tuple[bytes, ...],
    ExpertJournalCursorV1,
    ExpertStateV1,
]:
    observations = normalize_expert_parent(accumulator.manifest, parent)
    reduction = reduce_expert_parent(accumulator.state, observations)
    payloads = tuple(
        canonical_expert_bytes(outcome.payload)
        for outcome in reduction.outcomes
    )
    parent_evidence = observations[0].parent
    records: list[ExpertJournalRecordV1] = []
    traces: list[ExpertTraceStepV1] = []
    record_head = accumulator.cursor.expert_record_sha256
    trace_head = accumulator.cursor.expert_trace_sha256
    for index, (outcome, payload) in enumerate(
        zip(reduction.outcomes, payloads, strict=True)
    ):
        descriptor = ExpertPayloadDescriptorV1(
            schema_version=1,
            content_type="application/vnd.inci.expert+json",
            payload_encoding="canonical-json-v1",
            payload_contract_name=_payload_contract_name(
                outcome.event_kind
            ),
            payload_length=len(payload),
            payload_sha256=sha256(payload).hexdigest(),
        )
        record_values: dict[str, object] = {
            "schema_version": 1,
            "session_id": accumulator.manifest.session_id,
            "expert_manifest_sha256": (
                accumulator.manifest.manifest_sha256
            ),
            "provider_request_binding_sha256": (
                accumulator.manifest.provider_request_binding_sha256
            ),
            "match_binding_universe_sha256": (
                accumulator.manifest.match_binding_universe_sha256
            ),
            "retention_binding_sha256": (
                accumulator.manifest.retention.retention_binding_sha256
            ),
            "expert_seq": accumulator.cursor.expert_seq + index + 1,
            "parent": parent_evidence,
            "parent_output_index": index,
            "parent_output_count": len(reduction.outcomes),
            "event_kind": outcome.event_kind,
            "event_version": outcome.event_version,
            "event_schema_sha256": outcome.event_schema_sha256,
            "prior_expert_record_sha256": record_head,
            "prior_expert_state_sha256": (
                outcome.prior_expert_state_sha256
            ),
            "payload": descriptor,
            "post_expert_state_sha256": (
                outcome.post_expert_state_sha256
            ),
        }
        record = ExpertJournalRecordV1(
            **record_values,
            record_sha256=compute_expert_journal_record_sha256(
                **record_values
            ),
        )
        trace_values: dict[str, object] = {
            "schema_version": 1,
            "expert_seq": record.expert_seq,
            "prior_trace_sha256": trace_head,
            "expert_record_sha256": record.record_sha256,
            "post_expert_state_sha256": (
                record.post_expert_state_sha256
            ),
        }
        trace = ExpertTraceStepV1(
            **trace_values,
            post_trace_sha256=compute_expert_trace_step_sha256(
                **trace_values
            ),
        )
        records.append(record)
        traces.append(trace)
        record_head = record.record_sha256
        trace_head = trace.post_trace_sha256
    group_values: dict[str, object] = {
        "schema_version": 1,
        "session_id": accumulator.manifest.session_id,
        "expert_manifest_sha256": (
            accumulator.manifest.manifest_sha256
        ),
        "group_sequence": accumulator.cursor.group_count + 1,
        "parent": parent_evidence,
        "parent_output_count": len(records),
        "first_expert_seq": records[0].expert_seq,
        "prior_expert_record_sha256": (
            accumulator.cursor.expert_record_sha256
        ),
        "prior_expert_state_sha256": (
            accumulator.cursor.expert_state_sha256
        ),
        "records": tuple(records),
        "trace_steps": tuple(traces),
        "final_expert_record_sha256": records[-1].record_sha256,
        "post_expert_state_sha256": (
            reduction.final_expert_state_sha256
        ),
        "post_trace_sha256": traces[-1].post_trace_sha256,
    }
    group = ExpertJournalGroupV1(
        **group_values,
        group_sha256=compute_expert_journal_group_sha256(
            **group_values
        ),
    )
    cursor = ExpertJournalCursorV1(
        schema_version=1,
        session_id=accumulator.manifest.session_id,
        group_count=accumulator.cursor.group_count + 1,
        record_count=accumulator.cursor.record_count + len(records),
        last_parent_ingest_seq=parent.ingest_seq,
        last_parent_record_sha256=canonical_record_sha256(parent),
        expert_seq=records[-1].expert_seq,
        expert_record_sha256=records[-1].record_sha256,
        expert_state_sha256=reduction.final_expert_state_sha256,
        expert_trace_sha256=traces[-1].post_trace_sha256,
    )
    return group, payloads, cursor, reduction.final_state


def _record_digest_is_exact(record: ExpertJournalRecordV1) -> bool:
    values = {
        "schema_version": record.schema_version,
        "session_id": record.session_id,
        "expert_manifest_sha256": record.expert_manifest_sha256,
        "provider_request_binding_sha256": (
            record.provider_request_binding_sha256
        ),
        "match_binding_universe_sha256": (
            record.match_binding_universe_sha256
        ),
        "retention_binding_sha256": record.retention_binding_sha256,
        "expert_seq": record.expert_seq,
        "parent": record.parent,
        "parent_output_index": record.parent_output_index,
        "parent_output_count": record.parent_output_count,
        "event_kind": record.event_kind,
        "event_version": record.event_version,
        "event_schema_sha256": record.event_schema_sha256,
        "prior_expert_record_sha256": (
            record.prior_expert_record_sha256
        ),
        "prior_expert_state_sha256": record.prior_expert_state_sha256,
        "payload": record.payload,
        "post_expert_state_sha256": record.post_expert_state_sha256,
    }
    try:
        expected = compute_expert_journal_record_sha256(**values)
    except BaseException:
        return False
    return record.record_sha256 == expected


def _group_digest_is_exact(group: ExpertJournalGroupV1) -> bool:
    values = {
        "schema_version": group.schema_version,
        "session_id": group.session_id,
        "expert_manifest_sha256": group.expert_manifest_sha256,
        "group_sequence": group.group_sequence,
        "parent": group.parent,
        "parent_output_count": group.parent_output_count,
        "first_expert_seq": group.first_expert_seq,
        "prior_expert_record_sha256": (
            group.prior_expert_record_sha256
        ),
        "prior_expert_state_sha256": group.prior_expert_state_sha256,
        "records": group.records,
        "trace_steps": group.trace_steps,
        "final_expert_record_sha256": (
            group.final_expert_record_sha256
        ),
        "post_expert_state_sha256": group.post_expert_state_sha256,
        "post_trace_sha256": group.post_trace_sha256,
    }
    try:
        expected = compute_expert_journal_group_sha256(**values)
    except BaseException:
        return False
    return group.group_sha256 == expected


def _trace_digest_is_exact(trace: ExpertTraceStepV1) -> bool:
    values = {
        "schema_version": trace.schema_version,
        "expert_seq": trace.expert_seq,
        "prior_trace_sha256": trace.prior_trace_sha256,
        "expert_record_sha256": trace.expert_record_sha256,
        "post_expert_state_sha256": trace.post_expert_state_sha256,
    }
    try:
        expected = compute_expert_trace_step_sha256(**values)
    except BaseException:
        return False
    return trace.post_trace_sha256 == expected


def _consumed_parent_mismatch(
    accumulator: ExpertReplayAccumulatorV1,
    authorization: RetentionReplayAuthorizationV1,
    mismatch: ExpertReplayMismatchV1,
) -> ExpertReplayAccumulatorV1:
    return _unchecked_accumulator(
        manifest=accumulator.manifest,
        current_environment=accumulator.current_environment,
        evidence=accumulator.evidence,
        state=accumulator.state,
        cursor=accumulator.cursor,
        evidence_raw_count=accumulator.evidence_raw_count,
        evidence_derived_count=accumulator.evidence_derived_count,
        processed_parent_count=accumulator.processed_parent_count,
        last_authorization_sequence=authorization.authorization_sequence,
        last_authorization_sha256=authorization.authorization_sha256,
        mismatch=mismatch,
    )


def _unconsumed_parent_mismatch(
    accumulator: ExpertReplayAccumulatorV1,
    mismatch: ExpertReplayMismatchV1,
) -> ExpertReplayAccumulatorV1:
    return _unchecked_accumulator(
        manifest=accumulator.manifest,
        current_environment=accumulator.current_environment,
        evidence=accumulator.evidence,
        state=accumulator.state,
        cursor=accumulator.cursor,
        evidence_raw_count=accumulator.evidence_raw_count,
        evidence_derived_count=accumulator.evidence_derived_count,
        processed_parent_count=accumulator.processed_parent_count,
        last_authorization_sequence=(
            accumulator.last_authorization_sequence
        ),
        last_authorization_sha256=accumulator.last_authorization_sha256,
        mismatch=mismatch,
    )


def begin_expert_replay(
    *,
    manifest: ExpertSessionManifestV1,
    current_environment: ExpertCurrentEnvironmentV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
    evidence: EvidenceReplayContextV1,
    authorization: RetentionReplayAuthorizationV1,
) -> ExpertReplayAccumulatorV1:
    for value, value_type, name in (
        (manifest, ExpertSessionManifestV1, "manifest"),
        (
            current_environment,
            ExpertCurrentEnvironmentV1,
            "current_environment",
        ),
        (universe, BindingUniverse, "universe"),
        (policy, SyncPolicy, "policy"),
        (evidence, EvidenceReplayContextV1, "evidence"),
        (
            authorization,
            RetentionReplayAuthorizationV1,
            "authorization",
        ),
    ):
        _require_exact(value, value_type, name)
    _validate_authorization_member_types(authorization)

    mismatch: ExpertReplayMismatchV1 | None = None
    deadline_reached = (
        authorization.final_sampled_wall_ns
        >= authorization.common_deadline_ns
    )
    identities_exact = _authorization_identities_are_exact(
        authorization,
        evidence,
    )
    retention_exact = _retention_relation_is_exact(
        authorization,
        manifest,
        evidence,
    )
    try:
        evidence._validate()
    except BaseException:
        mismatch = _MISMATCH_PRECEDENCE[0]
    terminal_is_sole_nonexactness = (
        not evidence.replay_result.exact_replay
        and evidence.replay_result.wal_valid
        and evidence.replay_result.scan_issue is not None
        and evidence.replay_result.scan_issue.value
        in {"missing_terminal", "halted_terminal"}
        and (
            evidence.replay_result.replay_mismatch is None
            or (
                evidence.replay_result.scan_issue.value
                == "halted_terminal"
                and evidence.replay_result.replay_mismatch.value
                == "terminal_reason_mismatch"
            )
        )
    )
    if (
        mismatch is None
        and evidence.replay_result.replay_mismatch is not None
        and not terminal_is_sole_nonexactness
    ):
        mismatch = _MISMATCH_PRECEDENCE[1]
    if mismatch is None and not evidence.replay_result.exact_replay:
        mismatch = _MISMATCH_PRECEDENCE[2]
    if mismatch is None and (
        manifest.session_id != evidence.session_manifest.session_id
        or authorization.session_id != evidence.session_manifest.session_id
    ):
        mismatch = _MISMATCH_PRECEDENCE[3]
    if (
        mismatch is None
        and not _manifest_evidence_relation_is_exact(manifest, evidence)
    ):
        mismatch = _MISMATCH_PRECEDENCE[4]
    if mismatch is None:
        static_authorization_exact = (
            _authorization_static_relation_is_exact(
                authorization,
                manifest,
                evidence,
                operation="begin",
                sequence=0,
                expected_parent_ingest_seq=None,
            )
        )
        digest_exact = _authorization_digest_is_exact(authorization)
        if not static_authorization_exact or not digest_exact:
            mismatch = _MISMATCH_PRECEDENCE[5]
    if mismatch is None and deadline_reached:
        mismatch = _MISMATCH_PRECEDENCE[6]
    if mismatch is None and not identities_exact:
        mismatch = _MISMATCH_PRECEDENCE[7]
    if (
        mismatch is None
        and current_environment != manifest.environment
    ):
        mismatch = _MISMATCH_PRECEDENCE[8]
    if mismatch is None and not retention_exact:
        mismatch = _MISMATCH_PRECEDENCE[9]
    if (
        mismatch is None
        and not _static_companion_manifest_is_decodable(manifest)
    ):
        mismatch = _MISMATCH_PRECEDENCE[10]
    if mismatch is None and not _companion_manifest_relations_are_exact(
        manifest,
        universe,
        policy,
        evidence,
    ):
        mismatch = _MISMATCH_PRECEDENCE[11]

    try:
        state = initial_expert_state(manifest, universe, policy)
    except BaseException:
        state = _fallback_initial_state(manifest, universe, policy)
    cursor = _genesis_cursor(manifest, state)
    return _unchecked_accumulator(
        manifest=manifest,
        current_environment=current_environment,
        evidence=evidence,
        state=state,
        cursor=cursor,
        evidence_raw_count=evidence.replay_result.raw_count,
        evidence_derived_count=evidence.replay_result.derived_count,
        processed_parent_count=0,
        last_authorization_sequence=0,
        last_authorization_sha256=authorization.authorization_sha256,
        mismatch=mismatch,
    )


def replay_expert_parent_group(
    accumulator: ExpertReplayAccumulatorV1,
    *,
    authorization: RetentionReplayAuthorizationV1,
    parent: PersistedEvent,
    stored_group: ExpertJournalGroupV1,
    stored_payloads: tuple[bytes, ...],
) -> ExpertReplayAccumulatorV1:
    for value, value_type, name in (
        (accumulator, ExpertReplayAccumulatorV1, "accumulator"),
        (
            authorization,
            RetentionReplayAuthorizationV1,
            "authorization",
        ),
        (parent, PersistedEvent, "parent"),
        (stored_group, ExpertJournalGroupV1, "stored_group"),
    ):
        _require_exact(value, value_type, name)
    if type(stored_payloads) is not tuple:
        raise TypeError("stored_payloads")
    if any(type(payload) is not bytes for payload in stored_payloads):
        raise TypeError("stored_payloads")
    _validate_authorization_member_types(authorization)

    if accumulator.mismatch is not None:
        return accumulator

    deadline_reached = (
        authorization.final_sampled_wall_ns
        >= authorization.common_deadline_ns
    )
    identities_exact = _authorization_identities_are_exact(
        authorization,
        accumulator.evidence,
    )
    retention_exact = _retention_relation_is_exact(
        authorization,
        accumulator.manifest,
        accumulator.evidence,
    )
    static_authorization_exact = (
        authorization.session_id == accumulator.manifest.session_id
        and _authorization_static_relation_is_exact(
            authorization,
            accumulator.manifest,
            accumulator.evidence,
            operation="parent_group",
            sequence=accumulator.last_authorization_sequence + 1,
            expected_parent_ingest_seq=parent.ingest_seq,
        )
    )
    digest_exact = _authorization_digest_is_exact(authorization)
    if not static_authorization_exact or not digest_exact:
        return _unconsumed_parent_mismatch(
            accumulator,
            _MISMATCH_PRECEDENCE[5],
        )
    if deadline_reached:
        return _unconsumed_parent_mismatch(
            accumulator,
            _MISMATCH_PRECEDENCE[6],
        )
    if not identities_exact:
        return _unconsumed_parent_mismatch(
            accumulator,
            _MISMATCH_PRECEDENCE[7],
        )
    if accumulator.current_environment != accumulator.manifest.environment:
        return _unconsumed_parent_mismatch(
            accumulator,
            _MISMATCH_PRECEDENCE[8],
        )
    if not retention_exact:
        return _unconsumed_parent_mismatch(
            accumulator,
            _MISMATCH_PRECEDENCE[9],
        )

    expected_group_sequence = accumulator.cursor.group_count + 1
    expected_first_expert_seq = accumulator.cursor.expert_seq + 1
    sequence_mismatch = (
        stored_group.group_sequence != expected_group_sequence
        or stored_group.first_expert_seq != expected_first_expert_seq
        or any(
            record.expert_seq != expected_first_expert_seq + index
            for index, record in enumerate(stored_group.records)
        )
    )
    if sequence_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[12],
        )

    expected_parent_ingest_seq = (
        2 * (accumulator.processed_parent_count + 1)
    )
    if (
        parent.ingest_seq != expected_parent_ingest_seq
        or parent.ingest_seq <= accumulator.cursor.last_parent_ingest_seq
        or stored_group.parent.ingest_seq != parent.ingest_seq
    ):
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[15],
        )
    if parent.record_kind is not RecordKind.RAW:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[16],
        )

    try:
        parent_record_sha256 = canonical_record_sha256(parent)
        expected_parent = ExpertParentEvidenceV1(
            session_id=parent.session_id,
            ingest_seq=parent.ingest_seq,
            record_sha256=parent_record_sha256,
            event_type=parent.event_type,
            event_version=parent.event_version,
            local_wall_ns=parent.local_wall_ns,
            local_monotonic_ns=parent.local_monotonic_ns,
            clock_uncertainty_ns=parent.clock_uncertainty_ns,
        )
    except TypeError:
        raise
    except BaseException:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[17],
        )
    if (
        parent.session_id != accumulator.manifest.session_id
        or stored_group.parent != expected_parent
    ):
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[17],
        )

    expected_group, expected_payloads, expected_cursor, final_state = (
        _expected_group(accumulator, parent)
    )
    expected_count = len(expected_group.records)
    records = stored_group.records
    traces = stored_group.trace_steps
    shape_mismatch = (
        not 1 <= stored_group.parent_output_count <= 64
        or stored_group.parent_output_count != expected_count
        or type(records) is not tuple
        or type(traces) is not tuple
        or len(records) != expected_count
        or len(traces) != expected_count
        or len(stored_payloads) != expected_count
        or any(type(record) is not ExpertJournalRecordV1 for record in records)
        or any(type(trace) is not ExpertTraceStepV1 for trace in traces)
        or any(
            record.parent != stored_group.parent
            or record.parent_output_index != index
            or record.parent_output_count != expected_count
            for index, record in enumerate(records)
        )
    )
    if shape_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[18],
        )

    if (
        stored_group.prior_expert_record_sha256
        != accumulator.cursor.expert_record_sha256
        or any(
            record.prior_expert_record_sha256
            != expected.prior_expert_record_sha256
            for record, expected in zip(
                records,
                expected_group.records,
                strict=True,
            )
        )
    ):
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[19],
        )
    if (
        stored_group.prior_expert_state_sha256
        != accumulator.cursor.expert_state_sha256
        or any(
            record.prior_expert_state_sha256
            != expected.prior_expert_state_sha256
            for record, expected in zip(
                records,
                expected_group.records,
                strict=True,
            )
        )
    ):
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[20],
        )

    schema_mismatch = (
        stored_group.schema_version != 1
        or any(
            record.schema_version != 1
            or record.payload.schema_version != 1
            or trace.schema_version != 1
            or type(record.event_kind) is not ExpertEventKindV1
            or record.event_version != 1
            or record.event_schema_sha256
            != expert_event_schema_resource_sha256(record.event_kind)
            for record, trace in zip(records, traces, strict=True)
        )
    )
    if schema_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[21],
    )

    record_chain_mismatch = False
    for record in records:
        if not _record_digest_is_exact(record):
            record_chain_mismatch = True
            break
    if record_chain_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[22],
        )

    descriptor_mismatch = any(
        record.payload.content_type
        != "application/vnd.inci.expert+json"
        or record.payload.payload_encoding != "canonical-json-v1"
        or record.payload.payload_contract_name
        != _payload_contract_name(record.event_kind)
        for record in records
    )
    if descriptor_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[23],
        )

    decoded_payloads: list[object] = []
    payload_bytes_mismatch = False
    for record, payload in zip(records, stored_payloads, strict=True):
        if (
            record.payload.payload_length != len(payload)
            or record.payload.payload_sha256
            != sha256(payload).hexdigest()
        ):
            payload_bytes_mismatch = True
            break
        try:
            decoded_payloads.append(
                decode_expert_event_payload(
                    payload,
                    event_kind=record.event_kind,
                    event_version=record.event_version,
                )
            )
        except ExpertJournalCodecError:
            payload_bytes_mismatch = True
            break
    if payload_bytes_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[24],
        )

    expected_decoded_payloads = tuple(
        decode_expert_event_payload(
            payload,
            event_kind=record.event_kind,
            event_version=record.event_version,
        )
        for record, payload in zip(
            expected_group.records,
            expected_payloads,
            strict=True,
        )
    )
    decoded_observations = tuple(
        payload.observation  # type: ignore[attr-defined]
        for payload in decoded_payloads
    )
    expected_observations = tuple(
        payload.observation  # type: ignore[attr-defined]
        for payload in expected_decoded_payloads
    )
    if decoded_observations != expected_observations:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[25],
        )
    if any(
        record.event_kind != expected_record.event_kind
        or record.event_version != expected_record.event_version
        or decoded != expected_decoded
        for record, decoded, expected_record, expected_decoded in zip(
            records,
            decoded_payloads,
            expected_group.records,
            expected_decoded_payloads,
            strict=True,
        )
    ):
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[26],
        )

    if (
        stored_group.post_expert_state_sha256
        != expected_group.post_expert_state_sha256
        or any(
            record.post_expert_state_sha256
            != expected.post_expert_state_sha256
            for record, expected in zip(
                records,
                expected_group.records,
                strict=True,
            )
        )
    ):
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[27],
        )

    trace_mismatch = (
        tuple(traces) != expected_group.trace_steps
        or not all(_trace_digest_is_exact(trace) for trace in traces)
        or stored_group.final_expert_record_sha256
        != expected_group.final_expert_record_sha256
        or stored_group.post_trace_sha256
        != expected_group.post_trace_sha256
        or not _group_digest_is_exact(stored_group)
        or stored_group != expected_group
    )
    if trace_mismatch:
        return _consumed_parent_mismatch(
            accumulator,
            authorization,
            _MISMATCH_PRECEDENCE[28],
        )

    return _unchecked_accumulator(
        manifest=accumulator.manifest,
        current_environment=accumulator.current_environment,
        evidence=accumulator.evidence,
        state=final_state,
        cursor=expected_cursor,
        evidence_raw_count=accumulator.evidence_raw_count,
        evidence_derived_count=accumulator.evidence_derived_count,
        processed_parent_count=accumulator.processed_parent_count + 1,
        last_authorization_sequence=authorization.authorization_sequence,
        last_authorization_sha256=authorization.authorization_sha256,
        mismatch=None,
    )


def _terminal_digest_is_exact(
    terminal: ExpertSessionTerminalV1,
) -> bool:
    values = {
        "schema_version": terminal.schema_version,
        "session_id": terminal.session_id,
        "expert_manifest_sha256": terminal.expert_manifest_sha256,
        "provider_request_binding_sha256": (
            terminal.provider_request_binding_sha256
        ),
        "match_binding_universe_sha256": (
            terminal.match_binding_universe_sha256
        ),
        "retention_binding_sha256": terminal.retention_binding_sha256,
        "evidence_terminal_ingest_seq": (
            terminal.evidence_terminal_ingest_seq
        ),
        "evidence_terminal_record_sha256": (
            terminal.evidence_terminal_record_sha256
        ),
        "evidence_terminal_clean": terminal.evidence_terminal_clean,
        "evidence_terminal_reason": terminal.evidence_terminal_reason,
        "evidence_raw_count": terminal.evidence_raw_count,
        "evidence_derived_count": terminal.evidence_derived_count,
        "expert_group_count": terminal.expert_group_count,
        "expert_record_count": terminal.expert_record_count,
        "last_parent_ingest_seq": terminal.last_parent_ingest_seq,
        "last_parent_record_sha256": (
            terminal.last_parent_record_sha256
        ),
        "final_expert_seq": terminal.final_expert_seq,
        "final_expert_record_sha256": (
            terminal.final_expert_record_sha256
        ),
        "final_expert_state_sha256": terminal.final_expert_state_sha256,
        "final_expert_trace_sha256": terminal.final_expert_trace_sha256,
        "clean": terminal.clean,
        "reason": terminal.reason,
        "research_evaluable": terminal.research_evaluable,
    }
    try:
        expected = compute_expert_session_terminal_sha256(**values)
    except BaseException:
        return False
    return terminal.terminal_sha256 == expected


def _finish_result(
    accumulator: ExpertReplayAccumulatorV1,
    *,
    mismatch: ExpertReplayMismatchV1 | None,
    final_authorization_sha256: str | None,
) -> ExpertReplayResultV1:
    exact = mismatch is None
    return ExpertReplayResultV1(
        state=accumulator.state if exact else None,
        trace_sha256=(
            accumulator.cursor.expert_trace_sha256 if exact else None
        ),
        evidence_raw_count=accumulator.evidence_raw_count,
        evidence_derived_count=accumulator.evidence_derived_count,
        expert_group_count=accumulator.cursor.group_count,
        expert_record_count=accumulator.cursor.record_count,
        evidence_exact=accumulator.evidence.replay_result.exact_replay,
        companion_valid=exact,
        terminals_aligned=exact,
        exact_replay=exact,
        mismatch=mismatch,
        final_authorization_sha256=final_authorization_sha256,
        evaluation_input_eligible=exact,
        research_evaluable=False,
    )


def _scan_member_types_are_exact(
    scan: ExpertJournalScanSummaryV1,
) -> None:
    for name, value in (
        ("schema_version", scan.schema_version),
        ("file_size", scan.file_size),
        ("last_good_offset", scan.last_good_offset),
        ("last_frame_sequence", scan.last_frame_sequence),
        ("group_count", scan.group_count),
        ("record_count", scan.record_count),
    ):
        if type(value) is not int:
            raise TypeError(f"companion_scan.{name}")
    for name, value in (
        ("terminal_clean", scan.terminal_clean),
        ("journal_valid", scan.journal_valid),
    ):
        if type(value) is not bool:
            raise TypeError(f"companion_scan.{name}")
    if (
        scan.issue is not None
        and type(scan.issue) is not ExpertJournalScanIssueV1
    ):
        raise TypeError("companion_scan.issue")


def _terminal_member_types_are_exact(
    terminal: ExpertSessionTerminalV1,
) -> None:
    for name, value in (
        ("schema_version", terminal.schema_version),
        (
            "evidence_terminal_ingest_seq",
            terminal.evidence_terminal_ingest_seq,
        ),
        ("evidence_raw_count", terminal.evidence_raw_count),
        ("evidence_derived_count", terminal.evidence_derived_count),
        ("expert_group_count", terminal.expert_group_count),
        ("expert_record_count", terminal.expert_record_count),
        ("last_parent_ingest_seq", terminal.last_parent_ingest_seq),
        ("final_expert_seq", terminal.final_expert_seq),
    ):
        if type(value) is not int:
            raise TypeError(f"companion_terminal.{name}")
    for name, value in (
        ("evidence_terminal_clean", terminal.evidence_terminal_clean),
        ("clean", terminal.clean),
        ("research_evaluable", terminal.research_evaluable),
    ):
        if type(value) is not bool:
            raise TypeError(f"companion_terminal.{name}")
    for name, value in (
        ("session_id", terminal.session_id),
        ("expert_manifest_sha256", terminal.expert_manifest_sha256),
        (
            "provider_request_binding_sha256",
            terminal.provider_request_binding_sha256,
        ),
        (
            "match_binding_universe_sha256",
            terminal.match_binding_universe_sha256,
        ),
        ("retention_binding_sha256", terminal.retention_binding_sha256),
        (
            "evidence_terminal_record_sha256",
            terminal.evidence_terminal_record_sha256,
        ),
        (
            "evidence_terminal_reason",
            terminal.evidence_terminal_reason,
        ),
        (
            "last_parent_record_sha256",
            terminal.last_parent_record_sha256,
        ),
        (
            "final_expert_record_sha256",
            terminal.final_expert_record_sha256,
        ),
        (
            "final_expert_state_sha256",
            terminal.final_expert_state_sha256,
        ),
        (
            "final_expert_trace_sha256",
            terminal.final_expert_trace_sha256,
        ),
        ("terminal_sha256", terminal.terminal_sha256),
    ):
        if type(value) is not str:
            raise TypeError(f"companion_terminal.{name}")
    if type(terminal.reason) is not ExpertTerminalReasonV1:
        raise TypeError("companion_terminal.reason")


def _evidence_terminal_reason(
    accumulator: ExpertReplayAccumulatorV1,
) -> str | None:
    terminal = accumulator.evidence.evidence_terminal
    if terminal is None:
        return None
    for reason in (
        ExpertTerminalReasonV1.OPERATOR_STOP.value,
        ExpertTerminalReasonV1.SESSION_END.value,
    ):
        if f'"reason":"{reason}"'.encode("ascii") in terminal.payload:
            return reason
    return None


def finish_expert_replay(
    accumulator: ExpertReplayAccumulatorV1,
    *,
    final_authorization: RetentionReplayAuthorizationV1,
    companion_terminal: ExpertSessionTerminalV1 | None,
    companion_scan: ExpertJournalScanSummaryV1,
) -> ExpertReplayResultV1:
    _require_exact(
        accumulator,
        ExpertReplayAccumulatorV1,
        "accumulator",
    )
    _require_exact(
        final_authorization,
        RetentionReplayAuthorizationV1,
        "final_authorization",
    )
    if (
        companion_terminal is not None
        and type(companion_terminal) is not ExpertSessionTerminalV1
    ):
        raise TypeError("companion_terminal")
    _require_exact(
        companion_scan,
        ExpertJournalScanSummaryV1,
        "companion_scan",
    )
    _validate_authorization_member_types(final_authorization)
    _scan_member_types_are_exact(companion_scan)
    if companion_terminal is not None:
        _terminal_member_types_are_exact(companion_terminal)

    # A mismatch already proven in layers 1-5 is immutable.  Later
    # authorization sampling cannot rewrite earlier evidence/manifest facts.
    if accumulator.mismatch in _MISMATCH_PRECEDENCE[:5]:
        return _finish_result(
            accumulator,
            mismatch=accumulator.mismatch,
            final_authorization_sha256=(
                final_authorization.authorization_sha256
            ),
        )

    deadline_reached = (
        final_authorization.final_sampled_wall_ns
        >= final_authorization.common_deadline_ns
    )
    identities_exact = _authorization_identities_are_exact(
        final_authorization,
        accumulator.evidence,
    )
    retention_exact = _retention_relation_is_exact(
        final_authorization,
        accumulator.manifest,
        accumulator.evidence,
    )
    static_authorization_exact = (
        final_authorization.session_id
        == accumulator.manifest.session_id
        and _authorization_static_relation_is_exact(
            final_authorization,
            accumulator.manifest,
            accumulator.evidence,
            operation="finish",
            sequence=accumulator.last_authorization_sequence + 1,
            expected_parent_ingest_seq=None,
        )
    )
    digest_exact = _authorization_digest_is_exact(final_authorization)
    if not static_authorization_exact or not digest_exact:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[5],
            final_authorization_sha256=None,
        )
    if deadline_reached:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[6],
            final_authorization_sha256=None,
        )
    if not identities_exact:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[7],
            final_authorization_sha256=None,
        )
    if accumulator.current_environment != accumulator.manifest.environment:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[8],
            final_authorization_sha256=None,
        )
    if not retention_exact:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[9],
            final_authorization_sha256=None,
        )

    final_digest = final_authorization.authorization_sha256
    if accumulator.mismatch in _MISMATCH_PRECEDENCE[:10]:
        return _finish_result(
            accumulator,
            mismatch=accumulator.mismatch,
            final_authorization_sha256=final_digest,
        )
    try:
        ExpertJournalScanSummaryV1.__post_init__(companion_scan)
    except TypeError:
        raise
    except BaseException:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[10],
            final_authorization_sha256=final_digest,
        )
    scan_issue_is_framing_failure = companion_scan.issue not in {
        None,
        ExpertJournalScanIssueV1.MISSING_TERMINAL,
        ExpertJournalScanIssueV1.HALTED_TERMINAL,
    }
    if scan_issue_is_framing_failure:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[10],
            final_authorization_sha256=final_digest,
        )

    if accumulator.mismatch in _MISMATCH_PRECEDENCE[10:13]:
        return _finish_result(
            accumulator,
            mismatch=accumulator.mismatch,
            final_authorization_sha256=final_digest,
        )
    if companion_scan.group_count < accumulator.evidence_raw_count:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[13],
            final_authorization_sha256=final_digest,
        )
    if companion_scan.group_count > accumulator.evidence_raw_count:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[14],
            final_authorization_sha256=final_digest,
        )
    if accumulator.mismatch is not None:
        return _finish_result(
            accumulator,
            mismatch=accumulator.mismatch,
            final_authorization_sha256=final_digest,
        )
    if companion_terminal is None:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[29],
            final_authorization_sha256=final_digest,
        )

    evidence_reason = _evidence_terminal_reason(accumulator)
    expected_reason = (
        ExpertTerminalReasonV1(evidence_reason)
        if evidence_reason
        in {
            ExpertTerminalReasonV1.OPERATOR_STOP.value,
            ExpertTerminalReasonV1.SESSION_END.value,
        }
        else None
    )
    if (
        companion_terminal.schema_version != 1
        or not companion_scan.terminal_clean
        or companion_terminal.evidence_terminal_clean
        != accumulator.evidence.replay_result.terminal_clean
        or companion_terminal.evidence_terminal_reason != evidence_reason
        or companion_terminal.clean
        != accumulator.evidence.replay_result.terminal_clean
        or companion_terminal.reason is not expected_reason
        or companion_terminal.research_evaluable is not False
    ):
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[30],
            final_authorization_sha256=final_digest,
        )

    evidence_terminal = accumulator.evidence.evidence_terminal
    count_mismatch = (
        evidence_terminal is None
        or companion_terminal.evidence_terminal_ingest_seq
        != evidence_terminal.ingest_seq
        or companion_terminal.evidence_raw_count
        != accumulator.evidence_raw_count
        or companion_terminal.evidence_derived_count
        != accumulator.evidence_derived_count
        or companion_terminal.expert_group_count
        != accumulator.cursor.group_count
        or companion_terminal.expert_record_count
        != accumulator.cursor.record_count
        or companion_terminal.last_parent_ingest_seq
        != accumulator.cursor.last_parent_ingest_seq
        or companion_terminal.final_expert_seq
        != accumulator.cursor.expert_seq
        or companion_scan.group_count != accumulator.cursor.group_count
        or companion_scan.record_count != accumulator.cursor.record_count
        or companion_scan.last_frame_sequence
        != companion_scan.group_count + 1
    )
    if count_mismatch:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[31],
            final_authorization_sha256=final_digest,
        )

    provenance_mismatch = (
        companion_terminal.session_id != accumulator.manifest.session_id
        or companion_terminal.expert_manifest_sha256
        != accumulator.manifest.manifest_sha256
        or companion_terminal.provider_request_binding_sha256
        != accumulator.manifest.provider_request_binding_sha256
        or companion_terminal.match_binding_universe_sha256
        != accumulator.manifest.match_binding_universe_sha256
        or companion_terminal.retention_binding_sha256
        != accumulator.manifest.retention.retention_binding_sha256
        or companion_terminal.evidence_terminal_record_sha256
        != accumulator.evidence.evidence_terminal_record_sha256
    )
    if provenance_mismatch:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[32],
            final_authorization_sha256=final_digest,
        )

    state_mismatch = (
        companion_terminal.last_parent_record_sha256
        != accumulator.cursor.last_parent_record_sha256
        or companion_terminal.final_expert_record_sha256
        != accumulator.cursor.expert_record_sha256
        or companion_terminal.final_expert_state_sha256
        != accumulator.cursor.expert_state_sha256
    )
    if state_mismatch:
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[33],
            final_authorization_sha256=final_digest,
        )
    if (
        companion_terminal.final_expert_trace_sha256
        != accumulator.cursor.expert_trace_sha256
        or not _terminal_digest_is_exact(companion_terminal)
    ):
        return _finish_result(
            accumulator,
            mismatch=_MISMATCH_PRECEDENCE[34],
            final_authorization_sha256=final_digest,
        )
    return _finish_result(
        accumulator,
        mismatch=None,
        final_authorization_sha256=final_digest,
    )
