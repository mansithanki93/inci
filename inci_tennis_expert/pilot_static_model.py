"""Static exact-scoring baseline for the research-only tennis pilot."""

from __future__ import annotations

from decimal import Decimal

from inci_tennis_expert.contracts import PlayerSide
from inci_tennis_expert.pilot_contracts import (
    PilotOutcomeEstimate,
    PilotPointEvent,
    PilotSupportReason,
    ServeStrengthArtifact,
    compute_serve_strength_artifact_sha256,
)
from inci_tennis_expert.win_probability import (
    WinProbabilityError,
    standard_bo3_live_probabilities,
)


_MODEL_VERSION = "pilot-static-v1"


def unsupported_static(reason: PilotSupportReason) -> PilotOutcomeEstimate:
    """Return the static model's typed abstention result."""
    return PilotOutcomeEstimate(
        model_version=_MODEL_VERSION,
        supported=False,
        home_next_point_probability=None,
        home_current_set_probability=None,
        home_match_probability=None,
        lower_home_match_probability=None,
        upper_home_match_probability=None,
        abstention_reason=reason,
    )


def _artifact_precedes_event(
    artifact: ServeStrengthArtifact,
    event: PilotPointEvent,
) -> bool:
    """Require a target-bound, unmodified artifact frozen before its match."""
    try:
        return (
            artifact.target_canonical_match_id == event.canonical_match_id
            and artifact.target_scheduled_start_wall_ns
            == event.after_state.scheduled_start_wall_ns
            and artifact.cutoff_wall_ns < artifact.target_scheduled_start_wall_ns
            and artifact.target_canonical_match_id not in artifact.training_match_ids
            and artifact.artifact_sha256
            == compute_serve_strength_artifact_sha256(
                version=artifact.version,
                target_canonical_match_id=artifact.target_canonical_match_id,
                target_scheduled_start_wall_ns=artifact.target_scheduled_start_wall_ns,
                cutoff_wall_ns=artifact.cutoff_wall_ns,
                training_match_ids=artifact.training_match_ids,
                training_match_ids_sha256=artifact.training_match_ids_sha256,
                source_data_sha256=artifact.source_data_sha256,
                feature_definition_sha256=artifact.feature_definition_sha256,
                code_sha256=artifact.code_sha256,
                home_serve_point_probability=artifact.home_serve_point_probability,
                away_serve_point_probability=artifact.away_serve_point_probability,
            )
        )
    except Exception:
        return False


def evaluate_static_outcome(
    event: PilotPointEvent,
    artifact: ServeStrengthArtifact,
) -> PilotOutcomeEstimate:
    """Evaluate a legal live after-state using frozen serve probabilities."""
    if not _artifact_precedes_event(artifact, event):
        return unsupported_static(PilotSupportReason.ARTIFACT_MISMATCH)

    state = event.after_state
    if state.server_for_next_point not in (PlayerSide.HOME, PlayerSide.AWAY):
        return unsupported_static(PilotSupportReason.ARTIFACT_MISMATCH)

    try:
        value = standard_bo3_live_probabilities(
            state,
            artifact.home_serve_point_probability,
            artifact.away_serve_point_probability,
        )
    except WinProbabilityError:
        return unsupported_static(PilotSupportReason.ARTIFACT_MISMATCH)

    home_next_point_probability = (
        artifact.home_serve_point_probability
        if state.server_for_next_point is PlayerSide.HOME
        else Decimal("1") - artifact.away_serve_point_probability
    )
    return PilotOutcomeEstimate(
        model_version=_MODEL_VERSION,
        supported=True,
        home_next_point_probability=home_next_point_probability,
        home_current_set_probability=value.home_current_set_probability,
        home_match_probability=value.home_match_probability,
        lower_home_match_probability=value.home_match_probability,
        upper_home_match_probability=value.home_match_probability,
        abstention_reason=None,
    )
