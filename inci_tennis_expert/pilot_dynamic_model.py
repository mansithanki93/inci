"""Immutable three-state Bayesian point model for the tennis pilot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from enum import Enum
from re import ASCII as RE_ASCII
from re import compile as pattern_compile

from inci_tennis_expert.contracts import PlayerSide
from inci_tennis_expert.pilot_contracts import (
    DynamicBeliefSnapshot,
    PilotOutcomeEstimate,
    PilotPointEvent,
    PilotSupportReason,
    ServeStrengthArtifact,
    compute_serve_strength_artifact_sha256,
    pilot_contract_sha256,
)
from inci_tennis_expert.win_probability import (
    WinProbabilityError,
    standard_bo3_live_probabilities,
)


__all__ = (
    "DynamicParameterCandidate",
    "DynamicPointArtifact",
    "DynamicPointModel",
    "DynamicPointModelError",
    "EffectivenessState",
    "compute_dynamic_point_artifact_sha256",
    "unsupported_dynamic",
)


_MODEL_VERSION = "pilot-dynamic-v1"
_CONTEXT = Context(prec=50)
_INTEGRATION_CONTEXT = Context(prec=110)
_WEIGHT_QUANTUM = Decimal("1e-24")
_SCORER_EPSILON = Decimal("1e-24")
_ID = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", RE_ASCII)
_SHA256 = pattern_compile(r"[0-9a-f]{64}\Z", RE_ASCII)


class DynamicPointModelError(ValueError):
    """Fixed-code rejection raised by the dynamic point filter."""


class EffectivenessState(str, Enum):
    BELOW_BASELINE = "below_baseline"
    BASELINE = "baseline"
    ABOVE_BASELINE = "above_baseline"


def _fail(code: str) -> None:
    raise DynamicPointModelError(code)


def _weights(value: object, name: str) -> tuple[Decimal, Decimal, Decimal]:
    if type(value) is not tuple or len(value) != 3:
        _fail(name)
    if any(
        type(weight) is not Decimal or not weight.is_finite() or weight < 0
        for weight in value
    ):
        _fail(name)
    if sum(value, Decimal("0")) != Decimal("1"):
        _fail(name)
    return value


@dataclass(frozen=True, slots=True)
class DynamicParameterCandidate:
    transition_matrix: tuple[
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
    ]
    home_initial_weights: tuple[Decimal, Decimal, Decimal]
    away_initial_weights: tuple[Decimal, Decimal, Decimal]
    logit_offsets: tuple[Decimal, Decimal, Decimal]

    def __post_init__(self) -> None:
        matrix = self.transition_matrix
        if type(matrix) is not tuple or len(matrix) != 3:
            _fail("transition_matrix")
        for row in matrix:
            _weights(row, "transition_matrix")
        _weights(self.home_initial_weights, "home_initial_weights")
        _weights(self.away_initial_weights, "away_initial_weights")
        offsets = self.logit_offsets
        if (
            type(offsets) is not tuple
            or len(offsets) != 3
            or any(
                type(offset) is not Decimal or not offset.is_finite()
                for offset in offsets
            )
        ):
            _fail("logit_offsets")


def compute_dynamic_point_artifact_sha256(
    *,
    version: str,
    target_canonical_match_id: str,
    target_scheduled_start_wall_ns: int,
    cutoff_wall_ns: int,
    training_match_ids: tuple[str, ...],
    validation_match_ids: tuple[str, ...],
    source_data_sha256: str,
    feature_definition_sha256: str,
    code_sha256: str,
    selected: DynamicParameterCandidate,
) -> str:
    """Return the canonical identity of a frozen dynamic-model artifact."""
    return pilot_contract_sha256(
        {
            "version": version,
            "target_canonical_match_id": target_canonical_match_id,
            "target_scheduled_start_wall_ns": target_scheduled_start_wall_ns,
            "cutoff_wall_ns": cutoff_wall_ns,
            "training_match_ids": training_match_ids,
            "validation_match_ids": validation_match_ids,
            "source_data_sha256": source_data_sha256,
            "feature_definition_sha256": feature_definition_sha256,
            "code_sha256": code_sha256,
            "selected": selected,
        }
    )


@dataclass(frozen=True, slots=True)
class DynamicPointArtifact:
    version: str
    artifact_sha256: str
    target_canonical_match_id: str
    target_scheduled_start_wall_ns: int
    cutoff_wall_ns: int
    training_match_ids: tuple[str, ...]
    validation_match_ids: tuple[str, ...]
    source_data_sha256: str
    feature_definition_sha256: str
    code_sha256: str
    selected: DynamicParameterCandidate

    def __post_init__(self) -> None:
        if type(self.version) is not str or _ID.fullmatch(self.version) is None:
            _fail("version")
        if (
            type(self.target_canonical_match_id) is not str
            or _ID.fullmatch(self.target_canonical_match_id) is None
        ):
            _fail("target_canonical_match_id")
        if (
            type(self.target_scheduled_start_wall_ns) is not int
            or self.target_scheduled_start_wall_ns <= 0
            or type(self.cutoff_wall_ns) is not int
            or self.cutoff_wall_ns < 0
            or self.cutoff_wall_ns >= self.target_scheduled_start_wall_ns
        ):
            _fail("cutoff_wall_ns")
        partitions = (self.training_match_ids, self.validation_match_ids)
        if any(
            type(partition) is not tuple or not partition
            for partition in partitions
        ):
            _fail("match_partitions")
        all_ids = self.training_match_ids + self.validation_match_ids
        if (
            any(
                type(match_id) is not str or _ID.fullmatch(match_id) is None
                for match_id in all_ids
            )
            or len(set(all_ids)) != len(all_ids)
            or self.target_canonical_match_id in all_ids
        ):
            _fail("match_partitions")
        if any(
            type(value) is not str or _SHA256.fullmatch(value) is None
            for value in (
                self.source_data_sha256,
                self.feature_definition_sha256,
                self.code_sha256,
            )
        ):
            _fail("artifact_digest")
        if type(self.selected) is not DynamicParameterCandidate:
            _fail("selected")
        if (
            type(self.artifact_sha256) is not str
            or self.artifact_sha256
            != compute_dynamic_point_artifact_sha256(
                version=self.version,
                target_canonical_match_id=self.target_canonical_match_id,
                target_scheduled_start_wall_ns=self.target_scheduled_start_wall_ns,
                cutoff_wall_ns=self.cutoff_wall_ns,
                training_match_ids=self.training_match_ids,
                validation_match_ids=self.validation_match_ids,
                source_data_sha256=self.source_data_sha256,
                feature_definition_sha256=self.feature_definition_sha256,
                code_sha256=self.code_sha256,
                selected=self.selected,
            )
        ):
            _fail("artifact_sha256")


def unsupported_dynamic(reason: PilotSupportReason) -> PilotOutcomeEstimate:
    """Return the dynamic model's typed abstention result."""
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


def _serve_artifact_is_authentic(artifact: ServeStrengthArtifact) -> bool:
    try:
        return artifact.artifact_sha256 == compute_serve_strength_artifact_sha256(
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
    except Exception:
        return False


def _event_matches_artifacts(
    event: PilotPointEvent,
    serve_artifact: ServeStrengthArtifact,
    dynamic_artifact: DynamicPointArtifact,
) -> bool:
    try:
        scheduled_start = event.after_state.scheduled_start_wall_ns
        return (
            event.canonical_match_id == serve_artifact.target_canonical_match_id
            == dynamic_artifact.target_canonical_match_id
            and scheduled_start == serve_artifact.target_scheduled_start_wall_ns
            == dynamic_artifact.target_scheduled_start_wall_ns
            and serve_artifact.cutoff_wall_ns < scheduled_start
            and dynamic_artifact.cutoff_wall_ns < scheduled_start
            and event.canonical_match_id not in serve_artifact.training_match_ids
            and event.canonical_match_id not in dynamic_artifact.training_match_ids
            and event.canonical_match_id not in dynamic_artifact.validation_match_ids
            and _serve_artifact_is_authentic(serve_artifact)
            and dynamic_artifact.artifact_sha256
            == compute_dynamic_point_artifact_sha256(
                version=dynamic_artifact.version,
                target_canonical_match_id=dynamic_artifact.target_canonical_match_id,
                target_scheduled_start_wall_ns=dynamic_artifact.target_scheduled_start_wall_ns,
                cutoff_wall_ns=dynamic_artifact.cutoff_wall_ns,
                training_match_ids=dynamic_artifact.training_match_ids,
                validation_match_ids=dynamic_artifact.validation_match_ids,
                source_data_sha256=dynamic_artifact.source_data_sha256,
                feature_definition_sha256=dynamic_artifact.feature_definition_sha256,
                code_sha256=dynamic_artifact.code_sha256,
                selected=dynamic_artifact.selected,
            )
        )
    except Exception:
        return False


def _state_probability(baseline: Decimal, offset: Decimal) -> Decimal:
    """Apply an offset in log-odds space without producing scorer endpoints."""
    if (
        type(baseline) is not Decimal
        or not baseline.is_finite()
        or not Decimal("0") < baseline < Decimal("1")
    ):
        _fail("serve_probability")
    try:
        with localcontext(_CONTEXT):
            log_odds = baseline.ln() - (Decimal("1") - baseline).ln()
            shifted = log_odds + offset
            if shifted >= 0:
                probability = Decimal("1") / (Decimal("1") + (-shifted).exp())
            else:
                exponential = shifted.exp()
                probability = exponential / (Decimal("1") + exponential)
            lower = _SCORER_EPSILON
            upper = Decimal("1") - _SCORER_EPSILON
            return +min(max(probability, lower), upper)
    except DecimalException as error:
        raise DynamicPointModelError("serve_probability") from error


def _transition(
    weights: tuple[Decimal, Decimal, Decimal],
    matrix: tuple[
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
    ],
) -> tuple[Decimal, Decimal, Decimal]:
    with localcontext(_CONTEXT):
        return tuple(
            sum(
                (weights[old] * matrix[old][new] for old in range(3)),
                Decimal("0"),
            )
            for new in range(3)
        )  # type: ignore[return-value]


def _persist_weights(
    normalized: tuple[Decimal, Decimal, Decimal],
) -> tuple[Decimal, Decimal, Decimal]:
    with localcontext(_CONTEXT):
        persisted = [
            value.quantize(_WEIGHT_QUANTUM, rounding=ROUND_HALF_EVEN)
            for value in normalized
        ]
        residual = Decimal("1") - sum(persisted, Decimal("0"))
        recipient = max(range(3), key=lambda index: (normalized[index], -index))
        persisted[recipient] += residual
        result = tuple(persisted)
    if (
        any(value < 0 for value in result)
        or sum(result, Decimal("0")) != Decimal("1")
    ):
        _fail("belief_normalization")
    return result  # type: ignore[return-value]


def _forward_update(
    weights: tuple[Decimal, Decimal, Decimal],
    matrix: tuple[
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
    ],
    likelihoods: tuple[Decimal, Decimal, Decimal],
) -> tuple[Decimal, Decimal, Decimal]:
    """Apply one transition and one emission using log-sum-exp normalization."""
    predicted = _transition(weights, matrix)
    try:
        with localcontext(_CONTEXT):
            log_scores = tuple(
                None
                if predicted[index].is_zero()
                else predicted[index].ln() + likelihoods[index].ln()
                for index in range(3)
            )
            maximum = max(score for score in log_scores if score is not None)
            scaled = tuple(
                Decimal("0") if score is None else (score - maximum).exp()
                for score in log_scores
            )
            total = sum(scaled, Decimal("0"))
            normalized = tuple(value / total for value in scaled)
    except (DecimalException, ValueError) as error:
        raise DynamicPointModelError("belief_normalization") from error
    return _persist_weights(normalized)  # type: ignore[arg-type]


def _belief_snapshot(
    *,
    canonical_match_id: str,
    point_event_sha256: str,
    dynamic_artifact_sha256: str,
    home_weights: tuple[Decimal, Decimal, Decimal],
    away_weights: tuple[Decimal, Decimal, Decimal],
) -> DynamicBeliefSnapshot:
    values = {
        "canonical_match_id": canonical_match_id,
        "point_event_sha256": point_event_sha256,
        "dynamic_artifact_sha256": dynamic_artifact_sha256,
        "home_weights": home_weights,
        "away_weights": away_weights,
    }
    return DynamicBeliefSnapshot(
        belief_sha256=pilot_contract_sha256(values),
        **values,
    )


@dataclass(frozen=True, slots=True)
class DynamicPointModel:
    serve_artifact: ServeStrengthArtifact
    dynamic_artifact: DynamicPointArtifact
    belief: DynamicBeliefSnapshot
    observed_point_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.serve_artifact) is not ServeStrengthArtifact
            or type(self.dynamic_artifact) is not DynamicPointArtifact
            or type(self.belief) is not DynamicBeliefSnapshot
            or type(self.observed_point_ids) is not tuple
            or any(type(point_id) is not str for point_id in self.observed_point_ids)
            or len(set(self.observed_point_ids)) != len(self.observed_point_ids)
            or self.belief.canonical_match_id
            != self.dynamic_artifact.target_canonical_match_id
            or self.belief.dynamic_artifact_sha256
            != self.dynamic_artifact.artifact_sha256
        ):
            _fail("model_state")

    @classmethod
    def initialize(
        cls,
        *,
        serve_artifact: ServeStrengthArtifact,
        dynamic_artifact: DynamicPointArtifact,
    ) -> DynamicPointModel:
        """Create a fresh immutable filter from target-bound frozen artifacts."""
        if (
            serve_artifact.target_canonical_match_id
            != dynamic_artifact.target_canonical_match_id
            or serve_artifact.target_scheduled_start_wall_ns
            != dynamic_artifact.target_scheduled_start_wall_ns
            or not _serve_artifact_is_authentic(serve_artifact)
        ):
            _fail("artifact_mismatch")
        initial_event_sha256 = pilot_contract_sha256(
            {
                "initial_belief": dynamic_artifact.artifact_sha256,
                "canonical_match_id": dynamic_artifact.target_canonical_match_id,
            }
        )
        belief = _belief_snapshot(
            canonical_match_id=dynamic_artifact.target_canonical_match_id,
            point_event_sha256=initial_event_sha256,
            dynamic_artifact_sha256=dynamic_artifact.artifact_sha256,
            home_weights=dynamic_artifact.selected.home_initial_weights,
            away_weights=dynamic_artifact.selected.away_initial_weights,
        )
        return cls(
            serve_artifact=serve_artifact,
            dynamic_artifact=dynamic_artifact,
            belief=belief,
        )

    def state_serve_probabilities(
        self,
        side: PlayerSide,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return finite, open-interval serve probabilities for each state."""
        if side is PlayerSide.HOME:
            baseline = self.serve_artifact.home_serve_point_probability
        elif side is PlayerSide.AWAY:
            baseline = self.serve_artifact.away_serve_point_probability
        else:
            _fail("player_side")
        return tuple(
            _state_probability(baseline, offset)
            for offset in self.dynamic_artifact.selected.logit_offsets
        )  # type: ignore[return-value]

    def predictive_home_point_probability(self, event: PilotPointEvent) -> Decimal:
        """Predict this point before its emission, without mutating the filter."""
        if event.point_id in self.observed_point_ids:
            _fail("duplicate_point")
        if not _event_matches_artifacts(
            event,
            self.serve_artifact,
            self.dynamic_artifact,
        ):
            _fail("artifact_mismatch")
        matrix = self.dynamic_artifact.selected.transition_matrix
        if event.server is PlayerSide.HOME:
            weights = _transition(self.belief.home_weights, matrix)
            probabilities = self.state_serve_probabilities(PlayerSide.HOME)
            with localcontext(_CONTEXT):
                return +sum(
                    (
                        weight * probability
                        for weight, probability in zip(
                            weights,
                            probabilities,
                            strict=True,
                        )
                    ),
                    Decimal("0"),
                )
        if event.server is PlayerSide.AWAY:
            weights = _transition(self.belief.away_weights, matrix)
            probabilities = self.state_serve_probabilities(PlayerSide.AWAY)
            with localcontext(_CONTEXT):
                away_win = sum(
                    (
                        weight * probability
                        for weight, probability in zip(
                            weights,
                            probabilities,
                            strict=True,
                        )
                    ),
                    Decimal("0"),
                )
                return +(Decimal("1") - away_win)
        _fail("player_side")

    def observe(
        self,
        event: PilotPointEvent,
    ) -> tuple[DynamicPointModel, DynamicBeliefSnapshot]:
        """Return a new model after one server transition and emission."""
        if event.point_id in self.observed_point_ids:
            _fail("duplicate_point")
        if not _event_matches_artifacts(
            event,
            self.serve_artifact,
            self.dynamic_artifact,
        ):
            _fail("artifact_mismatch")
        candidate = self.dynamic_artifact.selected
        probabilities = self.state_serve_probabilities(event.server)
        server_won = event.winner is event.server
        likelihoods = tuple(
            probability if server_won else Decimal("1") - probability
            for probability in probabilities
        )
        home_weights = self.belief.home_weights
        away_weights = self.belief.away_weights
        if event.server is PlayerSide.HOME:
            home_weights = _forward_update(
                home_weights,
                candidate.transition_matrix,
                likelihoods,  # type: ignore[arg-type]
            )
        elif event.server is PlayerSide.AWAY:
            away_weights = _forward_update(
                away_weights,
                candidate.transition_matrix,
                likelihoods,  # type: ignore[arg-type]
            )
        else:
            _fail("player_side")
        belief = _belief_snapshot(
            canonical_match_id=event.canonical_match_id,
            point_event_sha256=pilot_contract_sha256(event),
            dynamic_artifact_sha256=self.dynamic_artifact.artifact_sha256,
            home_weights=home_weights,
            away_weights=away_weights,
        )
        next_model = replace(
            self,
            belief=belief,
            observed_point_ids=(*self.observed_point_ids, event.point_id),
        )
        return next_model, belief

    def evaluate(self, event: PilotPointEvent) -> PilotOutcomeEstimate:
        """Integrate all 3 x 3 latent-state pairs through exact scoring."""
        if not _event_matches_artifacts(
            event,
            self.serve_artifact,
            self.dynamic_artifact,
        ):
            return unsupported_dynamic(PilotSupportReason.ARTIFACT_MISMATCH)
        next_server = event.after_state.server_for_next_point
        if next_server not in (PlayerSide.HOME, PlayerSide.AWAY):
            return unsupported_dynamic(PilotSupportReason.ARTIFACT_MISMATCH)
        try:
            home_probabilities = self.state_serve_probabilities(PlayerSide.HOME)
            away_probabilities = self.state_serve_probabilities(PlayerSide.AWAY)
            pair_match_probabilities: list[Decimal] = []
            with localcontext(_INTEGRATION_CONTEXT):
                match_probability = Decimal("0")
                set_probability = Decimal("0")
                for home_index, home_weight in enumerate(self.belief.home_weights):
                    for away_index, away_weight in enumerate(self.belief.away_weights):
                        value = standard_bo3_live_probabilities(
                            event.after_state,
                            home_probabilities[home_index],
                            away_probabilities[away_index],
                        )
                        joint = home_weight * away_weight
                        match_probability += joint * value.home_match_probability
                        set_probability += joint * value.home_current_set_probability
                        pair_match_probabilities.append(value.home_match_probability)
                if next_server is PlayerSide.HOME:
                    next_point_probability = sum(
                        (
                            weight * probability
                            for weight, probability in zip(
                                self.belief.home_weights,
                                home_probabilities,
                                strict=True,
                            )
                        ),
                        Decimal("0"),
                    )
                else:
                    away_win = sum(
                        (
                            weight * probability
                            for weight, probability in zip(
                                self.belief.away_weights,
                                away_probabilities,
                                strict=True,
                            )
                        ),
                        Decimal("0"),
                    )
                    next_point_probability = Decimal("1") - away_win
                lower = min(pair_match_probabilities)
                upper = max(pair_match_probabilities)
                match_probability = min(max(match_probability, lower), upper)
                set_probability = min(
                    max(set_probability, Decimal("0")),
                    Decimal("1"),
                )
        except (DynamicPointModelError, WinProbabilityError, DecimalException):
            return unsupported_dynamic(PilotSupportReason.ARTIFACT_MISMATCH)
        return PilotOutcomeEstimate(
            model_version=_MODEL_VERSION,
            supported=True,
            home_next_point_probability=next_point_probability,
            home_current_set_probability=set_probability,
            home_match_probability=match_probability,
            lower_home_match_probability=lower,
            upper_home_match_probability=upper,
            abstention_reason=None,
        )
