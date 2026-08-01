from __future__ import annotations

from .contracts import (
    ExpertContractError,
    ExpertEventKindV1,
    ExpertIgnoredObservationV1,
    ExpertObservationIgnoredPayloadV1,
    ExpertObservationRejectedPayloadV1,
    ExpertObservationV1,
    ExpertOutcomeDraftV1,
    ExpertReductionV1,
    ExpertRejectedObservationV1,
    ExpertRejectReasonV1,
    ExpertStateV1,
    ExpertSynchronizationAppliedPayloadV1,
    ExpertSynchronizationObservationV1,
    canonical_expert_bytes,
    expert_event_schema_resource_sha256,
    expert_state_sha256,
)
from .state import initial_expert_state
from .synchronizer import (
    SynchronizationSessionDriftError,
    synchronize,
    validate_synchronization_transition,
)


__all__ = ("initial_expert_state", "reduce_expert_parent")


_MAX_EVENT_PAYLOAD_BYTES = 131_064
_MAX_GROUP_PAYLOAD_AREA_BYTES = 8_388_608


def _state_with_synchronization(
    prior: ExpertStateV1,
    synchronization: object,
) -> ExpertStateV1:
    return ExpertStateV1(
        schema_version=prior.schema_version,
        session_id=prior.session_id,
        expert_manifest_sha256=prior.expert_manifest_sha256,
        match_binding_universe_sha256=prior.match_binding_universe_sha256,
        sync_policy_sha256=prior.sync_policy_sha256,
        initial_synchronization_sha256=prior.initial_synchronization_sha256,
        synchronization=synchronization,  # type: ignore[arg-type]
        rejected_parent_count=prior.rejected_parent_count,
        halted=prior.halted,
        halt_reason=prior.halt_reason,
    )


def _halted_state(
    prior: ExpertStateV1,
    reason: ExpertRejectReasonV1,
    *,
    increment: bool,
) -> ExpertStateV1:
    return ExpertStateV1(
        schema_version=prior.schema_version,
        session_id=prior.session_id,
        expert_manifest_sha256=prior.expert_manifest_sha256,
        match_binding_universe_sha256=prior.match_binding_universe_sha256,
        sync_policy_sha256=prior.sync_policy_sha256,
        initial_synchronization_sha256=prior.initial_synchronization_sha256,
        synchronization=prior.synchronization,
        rejected_parent_count=(
            prior.rejected_parent_count + (1 if increment else 0)
        ),
        halted=True,
        halt_reason=prior.halt_reason or reason,
    )


def _outcome(
    *,
    kind: ExpertEventKindV1,
    payload: object,
    prior_state_sha256: str,
    post_state: ExpertStateV1,
) -> ExpertOutcomeDraftV1:
    return ExpertOutcomeDraftV1(
        event_kind=kind,
        event_version=1,
        event_schema_sha256=expert_event_schema_resource_sha256(kind),
        payload=payload,  # type: ignore[arg-type]
        prior_expert_state_sha256=prior_state_sha256,
        post_state=post_state,
        post_expert_state_sha256=expert_state_sha256(post_state),
    )


def _validate_observation_group(
    observations: tuple[ExpertObservationV1, ...],
) -> type[object]:
    if type(observations) is not tuple:
        raise TypeError("observations")
    if not observations or len(observations) > 64:
        raise ExpertContractError("observation_group_shape")
    exact_types = tuple(type(item) for item in observations)
    allowed = (
        ExpertSynchronizationObservationV1,
        ExpertIgnoredObservationV1,
        ExpertRejectedObservationV1,
    )
    if any(item not in allowed for item in exact_types):
        raise TypeError("observations")
    first = exact_types[0]
    if first is ExpertSynchronizationObservationV1:
        if any(item is not first for item in exact_types):
            raise ExpertContractError("observation_group_shape")
    elif len(observations) != 1:
        raise ExpertContractError("observation_group_shape")
    parent = observations[0].parent
    count = len(observations)
    for index, observation in enumerate(observations):
        type(observation).__post_init__(observation)
        if (
            observation.parent != parent
            or observation.parent_output_index != index
            or observation.parent_output_count != count
        ):
            raise ExpertContractError("observation_group_shape")
    return first


def _rejected_from_observation(
    observation: ExpertObservationV1,
    reason: ExpertRejectReasonV1,
) -> ExpertRejectedObservationV1 | ExpertSynchronizationObservationV1:
    if type(observation) is ExpertSynchronizationObservationV1 and reason in {
        ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT,
        ExpertRejectReasonV1.REDUCER_EXCEPTION,
        ExpertRejectReasonV1.PRIOR_OUTCOME_HALTED,
    }:
        return observation
    return ExpertRejectedObservationV1(
        parent=observation.parent,
        parent_output_index=0,
        parent_output_count=1,
        normalizer_id=observation.normalizer_id,
        normalizer_code_sha256=observation.normalizer_code_sha256,
        normalizer_schema_sha256=observation.normalizer_schema_sha256,
        reason=reason,
    )


def _single_rejection(
    prior: ExpertStateV1,
    observation: ExpertObservationV1,
    reason: ExpertRejectReasonV1,
) -> ExpertReductionV1:
    prior_sha256 = expert_state_sha256(prior)
    post = _halted_state(prior, reason, increment=True)
    rejected = _rejected_from_observation(observation, reason)
    payload = ExpertObservationRejectedPayloadV1(
        observation=rejected,
        reason=reason,
    )
    outcome = _outcome(
        kind=ExpertEventKindV1.OBSERVATION_REJECTED,
        payload=payload,
        prior_state_sha256=prior_sha256,
        post_state=post,
    )
    return ExpertReductionV1(
        prior_expert_state_sha256=prior_sha256,
        outcomes=(outcome,),
        final_state=post,
        final_expert_state_sha256=expert_state_sha256(post),
        halt_required=True,
    )


def _capacity_exceeded(
    prior: ExpertStateV1,
    observation: ExpertObservationV1,
) -> ExpertReductionV1:
    return _single_rejection(
        prior,
        observation,
        ExpertRejectReasonV1.GROUP_CAPACITY_EXCEEDED,
    )


def reduce_expert_parent(
    state: ExpertStateV1,
    observations: tuple[ExpertObservationV1, ...],
) -> ExpertReductionV1:
    if type(state) is not ExpertStateV1:
        raise TypeError("state")
    ExpertStateV1.__post_init__(state)
    shape = _validate_observation_group(observations)
    first = observations[0]
    if first.parent.session_id != state.session_id:
        raise ExpertContractError("parent_session")
    if state.halted:
        return _single_rejection(
            state,
            first,
            ExpertRejectReasonV1.PRIOR_GROUP_HALTED,
        )
    prior_sha256 = expert_state_sha256(state)

    if shape is ExpertIgnoredObservationV1:
        assert type(first) is ExpertIgnoredObservationV1
        payload = ExpertObservationIgnoredPayloadV1(first)
        outcome = _outcome(
            kind=ExpertEventKindV1.OBSERVATION_IGNORED,
            payload=payload,
            prior_state_sha256=prior_sha256,
            post_state=state,
        )
        return ExpertReductionV1(
            prior_expert_state_sha256=prior_sha256,
            outcomes=(outcome,),
            final_state=state,
            final_expert_state_sha256=prior_sha256,
            halt_required=False,
        )
    if shape is ExpertRejectedObservationV1:
        assert type(first) is ExpertRejectedObservationV1
        return _single_rejection(state, first, first.reason)

    intermediate = state
    outcomes: list[ExpertOutcomeDraftV1] = []
    halted_in_parent = False
    for observation in observations:
        assert type(observation) is ExpertSynchronizationObservationV1
        current_sha256 = expert_state_sha256(intermediate)
        if halted_in_parent:
            reason = ExpertRejectReasonV1.PRIOR_OUTCOME_HALTED
            payload = ExpertObservationRejectedPayloadV1(
                observation=observation,
                reason=reason,
            )
            outcomes.append(
                _outcome(
                    kind=ExpertEventKindV1.OBSERVATION_REJECTED,
                    payload=payload,
                    prior_state_sha256=current_sha256,
                    post_state=intermediate,
                )
            )
            continue
        try:
            transition = synchronize(
                intermediate.synchronization,
                observation.evidence,
                now=observation.observation,
            )
            validate_synchronization_transition(
                intermediate.synchronization,
                transition,
            )
        except SynchronizationSessionDriftError:
            reason = ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT
        except Exception:
            reason = ExpertRejectReasonV1.REDUCER_EXCEPTION
        else:
            candidate = _state_with_synchronization(
                intermediate,
                transition.state,
            )
            payload = ExpertSynchronizationAppliedPayloadV1(
                observation=observation,
                transition=transition,
            )
            if (
                len(canonical_expert_bytes(payload))
                > _MAX_EVENT_PAYLOAD_BYTES
                or len(canonical_expert_bytes(candidate))
                > _MAX_EVENT_PAYLOAD_BYTES
            ):
                return _capacity_exceeded(state, first)
            outcomes.append(
                _outcome(
                    kind=ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
                    payload=payload,
                    prior_state_sha256=current_sha256,
                    post_state=candidate,
                )
            )
            intermediate = candidate
            continue

        intermediate = _halted_state(
            intermediate,
            reason,
            increment=True,
        )
        payload = ExpertObservationRejectedPayloadV1(
            observation=observation,
            reason=reason,
        )
        outcomes.append(
            _outcome(
                kind=ExpertEventKindV1.OBSERVATION_REJECTED,
                payload=payload,
                prior_state_sha256=current_sha256,
                post_state=intermediate,
            )
        )
        halted_in_parent = True

    payload_sizes = tuple(
        len(canonical_expert_bytes(outcome.payload))
        for outcome in outcomes
    )
    if (
        any(size > _MAX_EVENT_PAYLOAD_BYTES for size in payload_sizes)
        or sum(8 + size for size in payload_sizes)
        > _MAX_GROUP_PAYLOAD_AREA_BYTES
    ):
        return _capacity_exceeded(state, first)
    return ExpertReductionV1(
        prior_expert_state_sha256=prior_sha256,
        outcomes=tuple(outcomes),
        final_state=intermediate,
        final_expert_state_sha256=expert_state_sha256(intermediate),
        halt_required=intermediate.halted,
    )
