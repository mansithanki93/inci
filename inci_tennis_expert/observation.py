from __future__ import annotations

from tennis_v1.codec import canonical_record_sha256
from tennis_v1.events import PersistedEvent, RecordKind

from .contracts import (
    BindingUniverse,
    ExpertCapacityProofV1,
    ExpertContractError,
    ExpertIgnoredDraftV1,
    ExpertIgnoredObservationV1,
    ExpertNormalizerPinV1,
    ExpertObservationDraftV1,
    ExpertObservationV1,
    ExpertParentEvidenceV1,
    ExpertRejectedDraftV1,
    ExpertRejectedObservationV1,
    ExpertRejectReasonV1,
    ExpertSessionManifestV1,
    ExpertSynchronizationDraftV1,
    ExpertSynchronizationObservationV1,
    PairedTimeObservation,
    SyncPolicy,
    canonical_expert_bytes,
    compute_expert_capacity_proof_sha256,
    expert_contract_sha256,
)
from .match_binding import binding_universe_sha256
from .task6_fallback_normalizer import normalize_task6_fallback


__all__ = (
    "MAX_EXPERT_EVENT_PAYLOAD_BYTES",
    "MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES",
    "MAX_EXPERT_OUTCOMES_PER_PARENT",
    "bind_expert_observation_drafts",
    "normalize_expert_parent",
    "prove_expert_capacity",
)


MAX_EXPERT_EVENT_PAYLOAD_BYTES = 131_064
MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES = 8_388_608
MAX_EXPERT_OUTCOMES_PER_PARENT = 64


def _parent_evidence(parent: PersistedEvent) -> ExpertParentEvidenceV1:
    if type(parent) is not PersistedEvent:
        raise TypeError("parent")
    PersistedEvent.__post_init__(parent)
    if parent.record_kind is not RecordKind.RAW:
        raise ExpertContractError("parent_record_kind")
    return ExpertParentEvidenceV1(
        session_id=parent.session_id,
        ingest_seq=parent.ingest_seq,
        record_sha256=canonical_record_sha256(parent),
        event_type=parent.event_type,
        event_version=parent.event_version,
        local_wall_ns=parent.local_wall_ns,
        local_monotonic_ns=parent.local_monotonic_ns,
        clock_uncertainty_ns=parent.clock_uncertainty_ns,
    )


def _validate_group_shape(
    drafts: tuple[ExpertObservationDraftV1, ...],
) -> type[object]:
    if type(drafts) is not tuple:
        raise TypeError("drafts")
    if not drafts or len(drafts) > MAX_EXPERT_OUTCOMES_PER_PARENT:
        raise ExpertContractError("normalizer_output_shape")
    exact_types = tuple(type(item) for item in drafts)
    allowed = (
        ExpertSynchronizationDraftV1,
        ExpertIgnoredDraftV1,
        ExpertRejectedDraftV1,
    )
    if any(item not in allowed for item in exact_types):
        raise TypeError("drafts")
    first = exact_types[0]
    if first is ExpertSynchronizationDraftV1:
        if any(item is not first for item in exact_types):
            raise ExpertContractError("normalizer_output_shape")
    elif len(drafts) != 1:
        raise ExpertContractError("normalizer_output_shape")
    for draft in drafts:
        type(draft).__post_init__(draft)
    return first


def _capacity_rejection(
    *,
    parent: ExpertParentEvidenceV1,
    pin: ExpertNormalizerPinV1,
) -> tuple[ExpertObservationV1, ...]:
    return (
        ExpertRejectedObservationV1(
            parent=parent,
            parent_output_index=0,
            parent_output_count=1,
            normalizer_id=pin.normalizer_id,
            normalizer_code_sha256=pin.normalizer_code_sha256,
            normalizer_schema_sha256=pin.normalizer_schema_sha256,
            reason=ExpertRejectReasonV1.GROUP_CAPACITY_EXCEEDED,
        ),
    )


def bind_expert_observation_drafts(
    manifest: ExpertSessionManifestV1,
    parent: PersistedEvent,
    pin: ExpertNormalizerPinV1,
    drafts: tuple[ExpertObservationDraftV1, ...],
) -> tuple[ExpertObservationV1, ...]:
    shape = _validate_group_shape(drafts)
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    if type(pin) is not ExpertNormalizerPinV1:
        raise TypeError("pin")
    ExpertSessionManifestV1.__post_init__(manifest)
    ExpertNormalizerPinV1.__post_init__(pin)
    if pin not in (manifest.normalizers.fallback, *manifest.normalizers.entries):
        raise ExpertContractError("normalizer_unpinned")
    evidence = _parent_evidence(parent)
    if (
        evidence.session_id != manifest.session_id
        or (
            parent.retention_delete_by_ns is not None
            and parent.retention_delete_by_ns
            != manifest.retention.retention_delete_by_ns
        )
    ):
        raise ExpertContractError("parent_manifest_binding")
    if pin is not manifest.normalizers.fallback and (
        pin.source_kind != parent.source_kind.value
        or pin.source_id != parent.source_id
        or pin.event_type != parent.event_type
        or pin.event_version != parent.event_version
    ):
        raise ExpertContractError("normalizer_parent_binding")

    encoded = tuple(canonical_expert_bytes(draft) for draft in drafts)
    if any(len(item) > MAX_EXPERT_EVENT_PAYLOAD_BYTES for item in encoded):
        return _capacity_rejection(parent=evidence, pin=pin)
    aggregate = sum(8 + len(item) for item in encoded)
    if aggregate > MAX_EXPERT_GROUP_PAYLOAD_AREA_BYTES:
        return _capacity_rejection(parent=evidence, pin=pin)

    count = len(drafts)
    if shape is ExpertSynchronizationDraftV1:
        paired = PairedTimeObservation(
            wall_ns=evidence.local_wall_ns,
            monotonic_ns=evidence.local_monotonic_ns,
            clock_uncertainty_ns=evidence.clock_uncertainty_ns,
        )
        return tuple(
            ExpertSynchronizationObservationV1(
                parent=evidence,
                parent_output_index=index,
                parent_output_count=count,
                normalizer_id=pin.normalizer_id,
                normalizer_code_sha256=pin.normalizer_code_sha256,
                normalizer_schema_sha256=pin.normalizer_schema_sha256,
                evidence=draft.evidence,
                observation=paired,
            )
            for index, draft in enumerate(drafts)
        )
    draft = drafts[0]
    if type(draft) is ExpertIgnoredDraftV1:
        return (
            ExpertIgnoredObservationV1(
                parent=evidence,
                parent_output_index=0,
                parent_output_count=1,
                normalizer_id=pin.normalizer_id,
                normalizer_code_sha256=pin.normalizer_code_sha256,
                normalizer_schema_sha256=pin.normalizer_schema_sha256,
                reason=draft.reason,
            ),
        )
    assert type(draft) is ExpertRejectedDraftV1
    return (
        ExpertRejectedObservationV1(
            parent=evidence,
            parent_output_index=0,
            parent_output_count=1,
            normalizer_id=pin.normalizer_id,
            normalizer_code_sha256=pin.normalizer_code_sha256,
            normalizer_schema_sha256=pin.normalizer_schema_sha256,
            reason=draft.reason,
        ),
    )


def normalize_expert_parent(
    manifest: ExpertSessionManifestV1,
    parent: PersistedEvent,
) -> tuple[ExpertObservationV1, ...]:
    if type(manifest) is not ExpertSessionManifestV1:
        raise TypeError("manifest")
    ExpertSessionManifestV1.__post_init__(manifest)
    evidence = _parent_evidence(parent)
    if evidence.session_id != manifest.session_id:
        raise ExpertContractError("parent_manifest_binding")
    key = (
        parent.source_kind.value,
        parent.source_id,
        parent.event_type,
        parent.event_version,
    )
    selected = tuple(
        pin
        for pin in manifest.normalizers.entries
        if (
            pin.source_kind,
            pin.source_id,
            pin.event_type,
            pin.event_version,
        )
        == key
    )
    if selected:
        # Task 6 has no registered production entry. A future reviewed task
        # must extend this static branch together with the registry.
        drafts: tuple[ExpertObservationDraftV1, ...] = (
            ExpertRejectedDraftV1(
                ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION
            ),
        )
        pin = selected[0]
    else:
        pin = manifest.normalizers.fallback
        drafts = normalize_task6_fallback(parent)
    return bind_expert_observation_drafts(manifest, parent, pin, drafts)


def prove_expert_capacity(
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> ExpertCapacityProofV1:
    if type(universe) is not BindingUniverse:
        raise TypeError("universe")
    if type(policy) is not SyncPolicy:
        raise TypeError("policy")
    BindingUniverse.__post_init__(universe)
    SyncPolicy.__post_init__(policy)
    universe_sha256 = binding_universe_sha256(universe)
    policy_sha256 = expert_contract_sha256(policy)
    if policy.universe_sha256 != universe_sha256:
        raise ExpertContractError("capacity_artifacts")
    values: dict[str, object] = {
        "schema_version": 1,
        "match_binding_universe_sha256": universe_sha256,
        "sync_policy_sha256": policy_sha256,
        "maximum_output_count": 64,
        "maximum_synchronization_state_bytes": 131_064,
        "maximum_transition_payload_bytes": 131_064,
        "maximum_rejected_payload_bytes": 131_064,
        "maximum_event_payload_bytes": 131_064,
        "maximum_group_payload_area_bytes": 8_388_608,
        "maximum_group_metadata_bytes": 8_388_532,
        "maximum_group_frame_bytes": 16_777_216,
        "maximum_terminal_metadata_bytes": 1_048_576,
        "maximum_terminal_frame_bytes": 1_048_652,
        "emergency_reserve_bytes": 17_825_868,
    }
    values["proof_sha256"] = compute_expert_capacity_proof_sha256(**values)
    return ExpertCapacityProofV1(**values)  # type: ignore[arg-type]
