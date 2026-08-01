"""Static qualification-only Sportradar routes.

Nothing in this module is a production provider registration.
"""

from __future__ import annotations

from inci_tennis_adapters.sportradar_tennis_v3 import (
    SportradarTennisV3CandidateError,
    bind_sportradar_tennis_v3_event,
    validate_sportradar_tennis_v3_prior,
    validate_sportradar_tennis_v3_transport_error,
)
from inci_tennis_expert.contracts import (
    BindingUniverse,
    ExpertIgnoreReasonV1,
    ExpertIgnoredDraftV1,
    ExpertObservationDraftV1,
    ExpertRejectReasonV1,
    ExpertRejectedDraftV1,
    ExpertSynchronizationDraftV1,
    ProviderLifecycle,
    ProviderPoint,
    ProviderSnapshot,
    SyncInputKind,
    SynchronizationInput,
    TennisState,
    TennisTransitionError,
    expert_contract_sha256,
)
from inci_tennis_expert.tennis_score import (
    apply_correction,
    apply_lifecycle,
    apply_point,
    state_from_snapshot,
)
from tennis_v1.entitlements import QualifiedProviderBinding
from tennis_v1.events import CapturedInput, PersistedEvent, SourceKind


PRODUCTION_PROVIDER_REGISTRY = ()

CANDIDATE_ROUTES = (
    (
        "provider",
        "sportradar",
        "sportradar_tennis_summary_v3",
        1,
        "sportradar-tennis-summary-v3",
        (
            "inci_tennis_adapters/schemas/"
            "sportradar-tennis-summary-v3-candidate-v1.schema.json"
        ),
    ),
    (
        "provider",
        "sportradar",
        "sportradar_tennis_timeline_v3",
        1,
        "sportradar-tennis-timeline-v3",
        (
            "inci_tennis_adapters/schemas/"
            "sportradar-tennis-timeline-v3-candidate-v1.schema.json"
        ),
    ),
    (
        "provider",
        "sportradar",
        "sportradar_tennis_transport_error_v1",
        1,
        "sportradar-tennis-transport-error-v1",
        (
            "inci_tennis_adapters/schemas/"
            "sportradar-tennis-transport-error-v1.schema.json"
        ),
    ),
)

SPORTRADAR_QUALIFICATION_CANDIDATE = (
    ("classification", "qualification_only"),
    ("provider_id", "sportradar"),
    ("module", "inci_tennis_adapters.sportradar_tennis_v3"),
    ("routes", CANDIDATE_ROUTES),
)

_SUMMARY_ROUTE = (
    "provider",
    "sportradar",
    "sportradar_tennis_summary_v3",
    1,
)
_TIMELINE_ROUTE = (
    "provider",
    "sportradar",
    "sportradar_tennis_timeline_v3",
    1,
)
_TRANSPORT_ERROR_ROUTE = (
    "provider",
    "sportradar",
    "sportradar_tennis_transport_error_v1",
    1,
)
_ROUTE_KEYS = tuple(route[:4] for route in CANDIDATE_ROUTES)

_CANDIDATE_ERROR_REASONS = {
    "candidate_binding_invalid": (
        ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION
    ),
    "candidate_captured_parent_mismatch": (
        ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION
    ),
    "candidate_payload_invalid": (
        ExpertRejectReasonV1.NORMALIZER_PAYLOAD_INVALID
    ),
    "candidate_prior_mismatch": (
        ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION
    ),
    "candidate_received_time_mismatch": (
        ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION
    ),
    "candidate_route_unknown": (
        ExpertRejectReasonV1.NORMALIZER_SCHEMA_UNKNOWN
    ),
    "candidate_schema_unknown": (
        ExpertRejectReasonV1.NORMALIZER_SCHEMA_UNKNOWN
    ),
    "candidate_secret_material": (
        ExpertRejectReasonV1.NORMALIZER_PAYLOAD_INVALID
    ),
}


class _CandidateDispatchContractError(ValueError):
    pass


def _rejected(
    reason: ExpertRejectReasonV1,
) -> tuple[ExpertObservationDraftV1, ...]:
    return (ExpertRejectedDraftV1(reason),)


def _route_key(captured: CapturedInput) -> tuple[str, str, str, int]:
    if (
        type(captured) is not CapturedInput
        or type(captured.source_kind) is not SourceKind
    ):
        raise _CandidateDispatchContractError()
    return (
        captured.source_kind.value,
        captured.source_id,
        captured.event_type,
        captured.event_version,
    )


def _canonical_match_id(
    *,
    provider_binding: QualifiedProviderBinding,
    universe: BindingUniverse,
    captured: CapturedInput,
) -> str:
    selected = tuple(
        binding
        for binding in universe.bindings
        if (
            binding.provider_source_id == provider_binding.provider_id
            and binding.provider_match_id == captured.source_entity_id
        )
    )
    if len(selected) != 1:
        raise _CandidateDispatchContractError()
    return selected[0].canonical_match_id


def _transition_for_event(
    state: TennisState,
    event: ProviderSnapshot | ProviderPoint | ProviderLifecycle,
):
    if type(event) is ProviderSnapshot:
        return apply_correction(state, event)
    if type(event) is ProviderPoint:
        return apply_point(state, event)
    if type(event) is ProviderLifecycle:
        return apply_lifecycle(state, event)
    raise _CandidateDispatchContractError()


def _synchronization_drafts(
    *,
    canonical_match_id: str,
    events: tuple[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle,
        ...,
    ],
    prior: TennisState | None,
) -> tuple[ExpertObservationDraftV1, ...]:
    if (
        type(events) is not tuple
        or not 1 <= len(events) <= 64
        or (prior is not None and type(prior) is not TennisState)
    ):
        raise _CandidateDispatchContractError()
    current = prior
    drafts: list[ExpertObservationDraftV1] = []
    for event in events:
        if type(event) not in (
            ProviderSnapshot,
            ProviderPoint,
            ProviderLifecycle,
        ):
            raise _CandidateDispatchContractError()
        if current is None:
            if type(event) is not ProviderSnapshot:
                raise _CandidateDispatchContractError()
            next_state = state_from_snapshot(event)
            evidence = SynchronizationInput(
                kind=SyncInputKind.TENNIS_ORIGIN,
                canonical_match_id=canonical_match_id,
                ticker=None,
                previous_state_sha256=None,
                provider_event=event,
                tennis_transition=None,
                book_event=None,
                book_transition=None,
                book_resnapshot_state=None,
            )
        else:
            previous_state_sha256 = expert_contract_sha256(current)
            transition = _transition_for_event(current, event)
            next_state = transition.state
            evidence = SynchronizationInput(
                kind=SyncInputKind.TENNIS_TRANSITION,
                canonical_match_id=canonical_match_id,
                ticker=None,
                previous_state_sha256=previous_state_sha256,
                provider_event=event,
                tennis_transition=transition,
                book_event=None,
                book_transition=None,
                book_resnapshot_state=None,
            )
        drafts.append(ExpertSynchronizationDraftV1(evidence))
        current = next_state
    return tuple(drafts)


def normalize_sportradar_candidate_raw(
    *,
    provider_binding: QualifiedProviderBinding,
    universe: BindingUniverse,
    captured: CapturedInput,
    durable_raw: PersistedEvent,
    prior: TennisState | None,
) -> tuple[ExpertObservationDraftV1, ...]:
    """Normalize one exact durable RAW through literal candidate routes."""
    try:
        if (
            type(provider_binding) is not QualifiedProviderBinding
            or type(universe) is not BindingUniverse
            or type(captured) is not CapturedInput
            or type(durable_raw) is not PersistedEvent
            or (prior is not None and type(prior) is not TennisState)
        ):
            raise _CandidateDispatchContractError()
        route = _route_key(captured)
        if route not in _ROUTE_KEYS:
            return _rejected(
                ExpertRejectReasonV1.NORMALIZER_SCHEMA_UNKNOWN
            )
        adapter = bind_sportradar_tennis_v3_event(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
            durable_raw=durable_raw,
        )
        validate_sportradar_tennis_v3_prior(
            adapter=adapter,
            prior=prior,
        )
        if route == _TRANSPORT_ERROR_ROUTE:
            validate_sportradar_tennis_v3_transport_error(
                provider_binding=provider_binding,
                universe=universe,
                captured=captured,
                durable_raw=durable_raw,
            )
            return (
                ExpertIgnoredDraftV1(
                    ExpertIgnoreReasonV1.EVENT_NOT_RELEVANT
                ),
            )

        canonical_match_id = _canonical_match_id(
            provider_binding=provider_binding,
            universe=universe,
            captured=captured,
        )
        if route == _SUMMARY_ROUTE:
            events = (
                adapter.normalize_summary(
                    captured.payload,
                    received_monotonic_ns=captured.local_monotonic_ns,
                ),
            )
        elif route == _TIMELINE_ROUTE:
            events = adapter.normalize_timeline(
                captured.payload,
                prior=prior,
                received_monotonic_ns=captured.local_monotonic_ns,
            )
        else:
            return _rejected(
                ExpertRejectReasonV1.NORMALIZER_SCHEMA_UNKNOWN
            )
        return _synchronization_drafts(
            canonical_match_id=canonical_match_id,
            events=events,
            prior=prior,
        )
    except SportradarTennisV3CandidateError as error:
        reason = _CANDIDATE_ERROR_REASONS.get(
            str(error),
            ExpertRejectReasonV1.NORMALIZER_EXCEPTION,
        )
        return _rejected(reason)
    except (TennisTransitionError, _CandidateDispatchContractError):
        return _rejected(
            ExpertRejectReasonV1.NORMALIZER_CONTRACT_VIOLATION
        )
    except Exception:
        return _rejected(ExpertRejectReasonV1.NORMALIZER_EXCEPTION)


__all__ = (
    "CANDIDATE_ROUTES",
    "PRODUCTION_PROVIDER_REGISTRY",
    "SPORTRADAR_QUALIFICATION_CANDIDATE",
    "normalize_sportradar_candidate_raw",
)
