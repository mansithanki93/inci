from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from .contracts import (
    DecisionReason,
    ExpertContractError,
    FairValueEstimate,
    PlayerSide,
    TennisState,
    _boolean,
    _exact,
    _exact_self,
    _integer,
    _probability,
    _quantity,
    _safe_id,
    _sha256,
    expert_contract_sha256,
)


_DECIMAL_PLACES = Decimal("0.000000000001")


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise ExpertContractError(name)
    return value


def _quantize(value: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return value.quantize(_DECIMAL_PLACES)


def _clamp_probability(value: Decimal) -> Decimal:
    if value < Decimal("0"):
        return Decimal("0")
    if value > Decimal("1"):
        return Decimal("1")
    return _quantize(value)


@dataclass(frozen=True, slots=True)
class PredictionOutcome:
    prediction_id: str
    prediction_wall_ns: int
    outcome_wall_ns: int
    raw_estimate: FairValueEstimate
    winning_side: PlayerSide

    def __post_init__(self) -> None:
        _exact_self(self, PredictionOutcome)
        _safe_id(self.prediction_id, "prediction_id")
        _integer(self.prediction_wall_ns, "prediction_wall_ns")
        _integer(self.outcome_wall_ns, "outcome_wall_ns")
        _exact(self.raw_estimate, FairValueEstimate, "raw_estimate")
        _exact(self.winning_side, PlayerSide, "winning_side")
        if self.prediction_wall_ns > self.outcome_wall_ns:
            raise ExpertContractError("chronology")


@dataclass(frozen=True, slots=True)
class CalibrationPolicy:
    policy_id: str
    stratum: str
    training_cutoff_wall_ns: int
    minimum_samples: int
    raw_model_sha256: str
    prematch_artifact_sha256: str
    feature_definition_sha256: str
    maximum_abs_adjustment: Decimal
    uncertainty_widening: Decimal

    def __post_init__(self) -> None:
        _exact_self(self, CalibrationPolicy)
        _safe_id(self.policy_id, "policy_id")
        _safe_id(self.stratum, "stratum")
        _integer(self.training_cutoff_wall_ns, "training_cutoff_wall_ns")
        _integer(self.minimum_samples, "minimum_samples", positive=True)
        _sha256(self.raw_model_sha256, "raw_model_sha256")
        _sha256(
            self.prematch_artifact_sha256,
            "prematch_artifact_sha256",
        )
        _sha256(
            self.feature_definition_sha256,
            "feature_definition_sha256",
        )
        _probability(self.maximum_abs_adjustment, "maximum_abs_adjustment")
        _quantity(self.uncertainty_widening, "uncertainty_widening")
        if self.uncertainty_widening > Decimal("0.5"):
            raise ExpertContractError("uncertainty_widening")


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    policy_id: str
    stratum: str
    training_cutoff_wall_ns: int
    sample_count: int
    mean_raw_probability: Decimal
    empirical_probability: Decimal
    intercept_adjustment: Decimal
    uncertainty_widening: Decimal
    raw_model_sha256: str
    prematch_artifact_sha256: str
    feature_definition_sha256: str
    supported: bool
    reason: str
    calibration_artifact_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, CalibrationArtifact)
        _safe_id(self.policy_id, "policy_id")
        _safe_id(self.stratum, "stratum")
        _integer(self.training_cutoff_wall_ns, "training_cutoff_wall_ns")
        _integer(self.sample_count, "sample_count")
        _probability(self.mean_raw_probability, "mean_raw_probability")
        _probability(self.empirical_probability, "empirical_probability")
        _decimal(self.intercept_adjustment, "intercept_adjustment")
        if abs(self.intercept_adjustment) > Decimal("1"):
            raise ExpertContractError("intercept_adjustment")
        _quantity(self.uncertainty_widening, "uncertainty_widening")
        _sha256(self.raw_model_sha256, "raw_model_sha256")
        _sha256(
            self.prematch_artifact_sha256,
            "prematch_artifact_sha256",
        )
        _sha256(
            self.feature_definition_sha256,
            "feature_definition_sha256",
        )
        _boolean(self.supported, "supported")
        _safe_id(self.reason, "reason")
        _sha256(
            self.calibration_artifact_sha256,
            "calibration_artifact_sha256",
        )
        if self.supported != (self.reason == "supported"):
            raise ExpertContractError("reason")


def _artifact_payload(
    *,
    policy: CalibrationPolicy,
    sample_count: int,
    mean_raw_probability: Decimal,
    empirical_probability: Decimal,
    intercept_adjustment: Decimal,
    supported: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "policy_id": policy.policy_id,
        "stratum": policy.stratum,
        "training_cutoff_wall_ns": policy.training_cutoff_wall_ns,
        "sample_count": sample_count,
        "mean_raw_probability": mean_raw_probability,
        "empirical_probability": empirical_probability,
        "intercept_adjustment": intercept_adjustment,
        "uncertainty_widening": policy.uncertainty_widening,
        "raw_model_sha256": policy.raw_model_sha256,
        "prematch_artifact_sha256": policy.prematch_artifact_sha256,
        "feature_definition_sha256": policy.feature_definition_sha256,
        "supported": supported,
        "reason": reason,
    }


def _policy_matches(
    estimate: FairValueEstimate,
    policy: CalibrationPolicy,
) -> bool:
    return (
        estimate.supported
        and estimate.stratum == policy.stratum
        and estimate.model_sha256 == policy.raw_model_sha256
        and estimate.prematch_artifact_sha256
        == policy.prematch_artifact_sha256
        and estimate.feature_definition_sha256
        == policy.feature_definition_sha256
    )


def calibrate_chronologically(
    predictions: tuple[PredictionOutcome, ...],
    policy: CalibrationPolicy,
) -> CalibrationArtifact:
    if type(predictions) is not tuple:
        raise TypeError("predictions")
    for prediction in predictions:
        if type(prediction) is not PredictionOutcome:
            raise TypeError("predictions")
    if type(policy) is not CalibrationPolicy:
        raise TypeError("policy")

    previous_prediction_wall_ns = -1
    sample_count = 0
    raw_sum = Decimal("0")
    outcome_sum = Decimal("0")
    for prediction in predictions:
        if prediction.prediction_wall_ns < previous_prediction_wall_ns:
            raise ExpertContractError("chronology")
        previous_prediction_wall_ns = prediction.prediction_wall_ns
        if prediction.outcome_wall_ns > policy.training_cutoff_wall_ns:
            raise ExpertContractError("training_cutoff_wall_ns")
        if not _policy_matches(prediction.raw_estimate, policy):
            continue
        sample_count += 1
        raw_sum += prediction.raw_estimate.fair_probability
        if prediction.winning_side is prediction.raw_estimate.player_side:
            outcome_sum += Decimal("1")

    if sample_count == 0:
        mean_raw = Decimal("0.5")
        empirical = Decimal("0.5")
    else:
        with localcontext() as context:
            context.prec = 50
            mean_raw = _clamp_probability(raw_sum / Decimal(sample_count))
            empirical = _clamp_probability(outcome_sum / Decimal(sample_count))
    with localcontext() as context:
        context.prec = 50
        raw_adjustment = empirical - mean_raw
    if raw_adjustment > policy.maximum_abs_adjustment:
        adjustment = policy.maximum_abs_adjustment
    elif raw_adjustment < -policy.maximum_abs_adjustment:
        adjustment = -policy.maximum_abs_adjustment
    else:
        adjustment = _quantize(raw_adjustment)
    supported = sample_count >= policy.minimum_samples
    reason = "supported" if supported else "insufficient_samples"
    payload = _artifact_payload(
        policy=policy,
        sample_count=sample_count,
        mean_raw_probability=mean_raw,
        empirical_probability=empirical,
        intercept_adjustment=adjustment,
        supported=supported,
        reason=reason,
    )
    return CalibrationArtifact(
        **payload,
        calibration_artifact_sha256=expert_contract_sha256(
            {"schema": "calibration_artifact_v1", **payload}
        ),
    )


def _unsupported_calibrated(
    raw_estimate: FairValueEstimate,
    reason: DecisionReason,
) -> FairValueEstimate:
    return FairValueEstimate(
        player_side=raw_estimate.player_side,
        fair_probability=raw_estimate.fair_probability,
        lower_probability=raw_estimate.lower_probability,
        upper_probability=raw_estimate.upper_probability,
        supported=False,
        stratum=raw_estimate.stratum,
        model_sha256=raw_estimate.model_sha256,
        prematch_artifact_sha256=raw_estimate.prematch_artifact_sha256,
        feature_definition_sha256=raw_estimate.feature_definition_sha256,
        feature_vector_sha256=raw_estimate.feature_vector_sha256,
        calibration_artifact_sha256=None,
        abstention_reason=reason,
    )


def apply_calibration(
    raw_estimate: FairValueEstimate,
    artifact: CalibrationArtifact,
    *,
    state: TennisState,
) -> FairValueEstimate:
    if type(raw_estimate) is not FairValueEstimate:
        raise TypeError("raw_estimate")
    if type(artifact) is not CalibrationArtifact:
        raise TypeError("artifact")
    if type(state) is not TennisState:
        raise TypeError("state")
    if not raw_estimate.supported:
        return raw_estimate
    if not artifact.supported:
        return _unsupported_calibrated(
            raw_estimate,
            DecisionReason.MODEL_UNCERTAIN,
        )
    if raw_estimate.stratum != artifact.stratum:
        return _unsupported_calibrated(
            raw_estimate,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
        )
    if (
        raw_estimate.model_sha256 != artifact.raw_model_sha256
        or raw_estimate.prematch_artifact_sha256
        != artifact.prematch_artifact_sha256
        or raw_estimate.feature_definition_sha256
        != artifact.feature_definition_sha256
    ):
        return _unsupported_calibrated(
            raw_estimate,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
        )
    if state.last_source_wall_ns <= artifact.training_cutoff_wall_ns:
        return _unsupported_calibrated(
            raw_estimate,
            DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
        )

    center = _clamp_probability(
        raw_estimate.fair_probability + artifact.intercept_adjustment
    )
    lower = _clamp_probability(
        raw_estimate.lower_probability
        + artifact.intercept_adjustment
        - artifact.uncertainty_widening
    )
    upper = _clamp_probability(
        raw_estimate.upper_probability
        + artifact.intercept_adjustment
        + artifact.uncertainty_widening
    )
    if lower > center:
        lower = center
    if upper < center:
        upper = center
    return FairValueEstimate(
        player_side=raw_estimate.player_side,
        fair_probability=center,
        lower_probability=lower,
        upper_probability=upper,
        supported=True,
        stratum=raw_estimate.stratum,
        model_sha256=raw_estimate.model_sha256,
        prematch_artifact_sha256=raw_estimate.prematch_artifact_sha256,
        feature_definition_sha256=raw_estimate.feature_definition_sha256,
        feature_vector_sha256=raw_estimate.feature_vector_sha256,
        calibration_artifact_sha256=artifact.calibration_artifact_sha256,
        abstention_reason=None,
    )


__all__ = (
    "CalibrationArtifact",
    "CalibrationPolicy",
    "PredictionOutcome",
    "apply_calibration",
    "calibrate_chronologically",
)
