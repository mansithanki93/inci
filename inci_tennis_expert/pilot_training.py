"""Leakage-safe fitting and freezing for the pilot dynamic point model."""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Context, Decimal, DecimalException, localcontext
from hashlib import sha256
from pathlib import Path
from re import ASCII as RE_ASCII
from re import compile as pattern_compile

from inci_tennis_expert.contracts import PlayerSide
from inci_tennis_expert.pilot_contracts import (
    PilotPointEvent,
    ServeStrengthArtifact,
    canonical_pilot_contract_bytes,
    pilot_contract_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    DynamicPointArtifact,
    DynamicPointModel,
    compute_dynamic_point_artifact_sha256,
)


__all__ = (
    "DynamicTrainingResult",
    "PilotTrainingError",
    "canonical_dynamic_point_artifact_bytes",
    "canonical_dynamic_point_artifact_json_bytes",
    "fit_dynamic_point_parameters",
    "freeze_dynamic_point_artifact",
)


_TRAINING_VERSION = "pilot-dynamic-training-v1"
_ARTIFACT_VERSION = "pilot-dynamic-v1"
_MAX_CANDIDATES = 4_096
_CONTEXT = Context(prec=80)
_ID = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", RE_ASCII)
_SHA256 = pattern_compile(r"[0-9a-f]{64}\Z", RE_ASCII)
_FEATURE_DEFINITION_SHA256 = pilot_contract_sha256(
    {
        "version": "pilot-dynamic-training-features-v1",
        "prediction_timing": "before_observe",
        "belief_scope": "reset_each_match",
        "labels": "home_point_win",
        "metrics": ("mean_log_loss", "mean_brier_score"),
        "selection": ("validation_log_loss", "candidate_canonical_key"),
    }
)


class PilotTrainingError(ValueError):
    """Fixed-code rejection raised before unsafe model fitting or freezing."""


def _fail(code: str) -> None:
    raise PilotTrainingError(code)


def _code_sha256() -> str:
    try:
        return sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError as error:
        raise PilotTrainingError("code_fingerprint") from error


def _candidate_key(candidate: DynamicParameterCandidate) -> str:
    return canonical_pilot_contract_bytes(candidate).decode("ascii")


def _candidate_is_structurally_valid(
    candidate: DynamicParameterCandidate,
) -> bool:
    try:
        rebuilt = DynamicParameterCandidate(
            **{
                field.name: getattr(candidate, field.name)
                for field in fields(DynamicParameterCandidate)
            }
        )
        return rebuilt == candidate
    except Exception:
        return False


def _event_is_structurally_valid(event: PilotPointEvent) -> bool:
    try:
        rebuilt = PilotPointEvent(
            **{
                field.name: getattr(event, field.name)
                for field in fields(PilotPointEvent)
            }
        )
        return rebuilt == event
    except Exception:
        return False


def _training_result_projection(result: DynamicTrainingResult) -> dict[str, object]:
    return {
        name: getattr(result, name)
        for name in result.__dataclass_fields__
        if name != "training_result_sha256"
    }


@dataclass(frozen=True, slots=True)
class DynamicTrainingResult:
    version: str
    training_result_sha256: str
    selected_candidate: DynamicParameterCandidate
    canonical_key: str
    training_match_ids: tuple[str, ...]
    validation_match_ids: tuple[str, ...]
    training_match_count: int
    validation_match_count: int
    training_row_count: int
    validation_row_count: int
    training_log_loss: Decimal
    validation_log_loss: Decimal
    training_brier_score: Decimal
    validation_brier_score: Decimal
    cutoff_wall_ns: int
    source_data_sha256: str
    serve_strength_artifacts_sha256: str
    feature_definition_sha256: str
    code_sha256: str

    def __post_init__(self) -> None:
        if self.version != _TRAINING_VERSION:
            _fail("training_result")
        if type(self.selected_candidate) is not DynamicParameterCandidate:
            _fail("training_result")
        if self.canonical_key != _candidate_key(self.selected_candidate):
            _fail("training_result")
        partitions = (self.training_match_ids, self.validation_match_ids)
        if (
            any(
                type(partition) is not tuple or not partition
                for partition in partitions
            )
            or any(
                type(match_id) is not str or _ID.fullmatch(match_id) is None
                for partition in partitions
                for match_id in partition
            )
            or len(set(self.training_match_ids + self.validation_match_ids))
            != len(self.training_match_ids + self.validation_match_ids)
        ):
            _fail("training_result")
        if (
            type(self.training_match_count) is not int
            or self.training_match_count != len(self.training_match_ids)
            or type(self.validation_match_count) is not int
            or self.validation_match_count != len(self.validation_match_ids)
            or type(self.training_row_count) is not int
            or self.training_row_count <= 0
            or type(self.validation_row_count) is not int
            or self.validation_row_count <= 0
            or type(self.cutoff_wall_ns) is not int
            or self.cutoff_wall_ns < 0
        ):
            _fail("training_result")
        for metric in (
            self.training_log_loss,
            self.validation_log_loss,
            self.training_brier_score,
            self.validation_brier_score,
        ):
            if type(metric) is not Decimal or not metric.is_finite() or metric < 0:
                _fail("training_result")
        if any(
            type(digest) is not str or _SHA256.fullmatch(digest) is None
            for digest in (
                self.source_data_sha256,
                self.serve_strength_artifacts_sha256,
                self.feature_definition_sha256,
                self.code_sha256,
            )
        ):
            _fail("training_result")
        if (
            type(self.training_result_sha256) is not str
            or self.training_result_sha256
            != pilot_contract_sha256(_training_result_projection(self))
        ):
            _fail("training_result_sha256")


@dataclass(frozen=True, slots=True)
class _ValidatedPartitions:
    training_events: tuple[PilotPointEvent, ...]
    validation_events: tuple[PilotPointEvent, ...]
    training_match_ids: tuple[str, ...]
    validation_match_ids: tuple[str, ...]
    scheduled_starts: dict[str, int]


@dataclass(frozen=True, slots=True)
class _Metrics:
    row_count: int
    log_loss: Decimal
    brier_score: Decimal


def _validate_match_ids(value: object) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or not value
        or any(
            type(match_id) is not str or _ID.fullmatch(match_id) is None
            for match_id in value
        )
        or len(set(value)) != len(value)
    ):
        _fail("match_partitions")
    return value


def _validate_partitioned_chronology(
    events: tuple[PilotPointEvent, ...],
    training_match_ids: tuple[str, ...],
    validation_match_ids: tuple[str, ...],
) -> _ValidatedPartitions:
    training_ids = _validate_match_ids(training_match_ids)
    validation_ids = _validate_match_ids(validation_match_ids)
    if set(training_ids) & set(validation_ids):
        _fail("match_partitions")
    if (
        type(events) is not tuple
        or not events
        or any(type(event) is not PilotPointEvent for event in events)
    ):
        _fail("events")
    if any(not _event_is_structurally_valid(event) for event in events):
        _fail("event_authenticity")

    expected_ids = set(training_ids + validation_ids)
    observed_ids = {event.canonical_match_id for event in events}
    if observed_ids != expected_ids:
        _fail("partition_coverage")

    last_by_match: dict[str, PilotPointEvent] = {}
    starts: dict[str, int] = {}
    for event in events:
        match_id = event.canonical_match_id
        scheduled_start = event.before_state.scheduled_start_wall_ns
        if event.after_state.scheduled_start_wall_ns != scheduled_start:
            _fail("point_sequence")
        existing_start = starts.setdefault(match_id, scheduled_start)
        if existing_start != scheduled_start:
            _fail("point_sequence")
        previous = last_by_match.get(match_id)
        if previous is None:
            if event.sequence_number != 1:
                _fail("point_sequence")
        elif (
            event.sequence_number != previous.sequence_number + 1
            or event.before_state != previous.after_state
        ):
            _fail("point_sequence")
        last_by_match[match_id] = event

    if max(starts[match_id] for match_id in training_ids) >= min(
        starts[match_id] for match_id in validation_ids
    ):
        _fail("partition_chronology")

    training_set = set(training_ids)
    validation_set = set(validation_ids)
    return _ValidatedPartitions(
        training_events=tuple(
            event for event in events if event.canonical_match_id in training_set
        ),
        validation_events=tuple(
            event for event in events if event.canonical_match_id in validation_set
        ),
        training_match_ids=training_ids,
        validation_match_ids=validation_ids,
        scheduled_starts=starts,
    )


def _serve_artifact_is_authentic(artifact: ServeStrengthArtifact) -> bool:
    try:
        rebuilt = ServeStrengthArtifact(
            artifact_sha256=artifact.artifact_sha256,
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
        return rebuilt == artifact
    except Exception:
        return False


def _build_serve_artifact_map(
    *,
    partitions: _ValidatedPartitions,
    serve_strength_artifacts: tuple[ServeStrengthArtifact, ...],
) -> dict[str, ServeStrengthArtifact]:
    if type(serve_strength_artifacts) is not tuple:
        _fail("serve_artifacts")
    artifacts: dict[str, ServeStrengthArtifact] = {}
    for artifact in serve_strength_artifacts:
        if type(artifact) is not ServeStrengthArtifact:
            _fail("serve_artifacts")
        match_id = artifact.target_canonical_match_id
        if match_id in artifacts:
            _fail("serve_artifact_duplicate")
        artifacts[match_id] = artifact

    expected_ids = set(
        partitions.training_match_ids + partitions.validation_match_ids
    )
    actual_ids = set(artifacts)
    if actual_ids < expected_ids:
        _fail("serve_artifact_missing")
    if actual_ids != expected_ids:
        _fail("serve_artifact_coverage")

    for match_id in partitions.training_match_ids + partitions.validation_match_ids:
        artifact = artifacts[match_id]
        scheduled_start = partitions.scheduled_starts[match_id]
        if artifact.target_scheduled_start_wall_ns != scheduled_start:
            _fail("serve_artifact_target")
        if artifact.cutoff_wall_ns >= scheduled_start:
            _fail("serve_artifact_post_start")
        if match_id in artifact.training_match_ids:
            _fail("serve_artifact_self_including")
        if not _serve_artifact_is_authentic(artifact):
            _fail("serve_artifact_authenticity")
    return artifacts


def _validate_candidates(
    candidates: tuple[DynamicParameterCandidate, ...],
) -> tuple[DynamicParameterCandidate, ...]:
    if (
        type(candidates) is not tuple
        or not candidates
        or len(candidates) > _MAX_CANDIDATES
        or any(
            type(candidate) is not DynamicParameterCandidate
            for candidate in candidates
        )
    ):
        _fail("candidate_grid")
    if any(not _candidate_is_structurally_valid(candidate) for candidate in candidates):
        _fail("candidate_grid")
    return candidates


def _placeholder_partition_id(prefix: str, target_match_id: str) -> str:
    suffix = sha256(target_match_id.encode("ascii")).hexdigest()[:24]
    candidate = f"{prefix}-{suffix}"
    if candidate == target_match_id:
        candidate = f"{prefix}-alternate-{suffix}"
    return candidate


def _scoring_artifact(
    *,
    candidate: DynamicParameterCandidate,
    serve_artifact: ServeStrengthArtifact,
    source_data_sha256: str,
    code_sha256: str,
) -> DynamicPointArtifact:
    values = {
        "version": "pilot-dynamic-fit-v1",
        "target_canonical_match_id": serve_artifact.target_canonical_match_id,
        "target_scheduled_start_wall_ns": serve_artifact.target_scheduled_start_wall_ns,
        "cutoff_wall_ns": serve_artifact.cutoff_wall_ns,
        "training_match_ids": (
            _placeholder_partition_id(
                "fit-training", serve_artifact.target_canonical_match_id
            ),
        ),
        "validation_match_ids": (
            _placeholder_partition_id(
                "fit-validation", serve_artifact.target_canonical_match_id
            ),
        ),
        "source_data_sha256": source_data_sha256,
        "feature_definition_sha256": _FEATURE_DEFINITION_SHA256,
        "code_sha256": code_sha256,
        "selected": candidate,
    }
    return DynamicPointArtifact(
        artifact_sha256=compute_dynamic_point_artifact_sha256(**values),
        **values,
    )


def _score_partition(
    *,
    candidate: DynamicParameterCandidate,
    events: tuple[PilotPointEvent, ...],
    match_ids: tuple[str, ...],
    artifacts: dict[str, ServeStrengthArtifact],
    source_data_sha256: str,
    code_sha256: str,
) -> _Metrics:
    loss_sum = Decimal("0")
    brier_sum = Decimal("0")
    row_count = 0
    events_by_match = {
        match_id: tuple(
            event for event in events if event.canonical_match_id == match_id
        )
        for match_id in match_ids
    }
    try:
        with localcontext(_CONTEXT):
            for match_id in match_ids:
                serve_artifact = artifacts[match_id]
                model = DynamicPointModel.initialize(
                    serve_artifact=serve_artifact,
                    dynamic_artifact=_scoring_artifact(
                        candidate=candidate,
                        serve_artifact=serve_artifact,
                        source_data_sha256=source_data_sha256,
                        code_sha256=code_sha256,
                    ),
                )
                for event in events_by_match[match_id]:
                    probability = model.predictive_home_point_probability(event)
                    label = (
                        Decimal("1")
                        if event.winner is PlayerSide.HOME
                        else Decimal("0")
                    )
                    likelihood = (
                        probability
                        if label == 1
                        else Decimal("1") - probability
                    )
                    loss_sum -= likelihood.ln()
                    brier_sum += (probability - label) ** 2
                    row_count += 1
                    model, _ = model.observe(event)
            return _Metrics(
                row_count=row_count,
                log_loss=+(loss_sum / row_count),
                brier_score=+(brier_sum / row_count),
            )
    except (DecimalException, ZeroDivisionError) as error:
        raise PilotTrainingError("candidate_scoring") from error


def _make_training_result(
    *,
    candidate: DynamicParameterCandidate,
    partitions: _ValidatedPartitions,
    artifacts: dict[str, ServeStrengthArtifact],
    source_data_sha256: str,
    serve_artifacts_sha256: str,
    code_sha256: str,
    cutoff_wall_ns: int,
) -> DynamicTrainingResult:
    training = _score_partition(
        candidate=candidate,
        events=partitions.training_events,
        match_ids=partitions.training_match_ids,
        artifacts=artifacts,
        source_data_sha256=source_data_sha256,
        code_sha256=code_sha256,
    )
    validation = _score_partition(
        candidate=candidate,
        events=partitions.validation_events,
        match_ids=partitions.validation_match_ids,
        artifacts=artifacts,
        source_data_sha256=source_data_sha256,
        code_sha256=code_sha256,
    )
    values: dict[str, object] = {
        "version": _TRAINING_VERSION,
        "selected_candidate": candidate,
        "canonical_key": _candidate_key(candidate),
        "training_match_ids": partitions.training_match_ids,
        "validation_match_ids": partitions.validation_match_ids,
        "training_match_count": len(partitions.training_match_ids),
        "validation_match_count": len(partitions.validation_match_ids),
        "training_row_count": training.row_count,
        "validation_row_count": validation.row_count,
        "training_log_loss": training.log_loss,
        "validation_log_loss": validation.log_loss,
        "training_brier_score": training.brier_score,
        "validation_brier_score": validation.brier_score,
        "cutoff_wall_ns": cutoff_wall_ns,
        "source_data_sha256": source_data_sha256,
        "serve_strength_artifacts_sha256": serve_artifacts_sha256,
        "feature_definition_sha256": _FEATURE_DEFINITION_SHA256,
        "code_sha256": code_sha256,
    }
    return DynamicTrainingResult(
        training_result_sha256=pilot_contract_sha256(values),
        **values,  # type: ignore[arg-type]
    )


def fit_dynamic_point_parameters(
    *,
    events: tuple[PilotPointEvent, ...],
    training_match_ids: tuple[str, ...],
    validation_match_ids: tuple[str, ...],
    candidates: tuple[DynamicParameterCandidate, ...],
    serve_strength_artifacts: tuple[ServeStrengthArtifact, ...],
) -> DynamicTrainingResult:
    """Select one candidate using causal per-point validation forecasts."""
    partitions = _validate_partitioned_chronology(
        events,
        training_match_ids,
        validation_match_ids,
    )
    artifacts = _build_serve_artifact_map(
        partitions=partitions,
        serve_strength_artifacts=serve_strength_artifacts,
    )
    grid = _validate_candidates(candidates)

    ordered_artifacts = tuple(
        artifacts[match_id]
        for match_id in partitions.training_match_ids
        + partitions.validation_match_ids
    )
    serve_artifacts_sha256 = pilot_contract_sha256(ordered_artifacts)
    source_data_sha256 = pilot_contract_sha256(
        {
            "training_match_ids": partitions.training_match_ids,
            "validation_match_ids": partitions.validation_match_ids,
            "training_events": partitions.training_events,
            "validation_events": partitions.validation_events,
            "serve_strength_artifacts_sha256": serve_artifacts_sha256,
        }
    )
    code_sha256 = _code_sha256()
    cutoff_wall_ns = max(
        value
        for event in events
        for value in (
            event.received_wall_ns,
            event.before_state.scheduled_start_wall_ns,
            event.before_state.last_source_wall_ns,
            event.before_state.last_source_generated_wall_ns,
            event.after_state.last_source_wall_ns,
            event.after_state.last_source_generated_wall_ns,
            artifacts[event.canonical_match_id].cutoff_wall_ns,
        )
    )
    scored = tuple(
        _make_training_result(
            candidate=candidate,
            partitions=partitions,
            artifacts=artifacts,
            source_data_sha256=source_data_sha256,
            serve_artifacts_sha256=serve_artifacts_sha256,
            code_sha256=code_sha256,
            cutoff_wall_ns=cutoff_wall_ns,
        )
        for candidate in grid
    )
    return min(
        scored,
        key=lambda row: (row.validation_log_loss, row.canonical_key),
    )


def _training_result_is_authentic(result: DynamicTrainingResult) -> bool:
    try:
        if not _candidate_is_structurally_valid(result.selected_candidate):
            return False
        rebuilt = DynamicTrainingResult(
            **{
                field.name: getattr(result, field.name)
                for field in fields(DynamicTrainingResult)
            }
        )
        return rebuilt == result
    except Exception:
        return False


def freeze_dynamic_point_artifact(
    *,
    training_result: DynamicTrainingResult,
    target_canonical_match_id: str,
    target_scheduled_start_wall_ns: int,
) -> DynamicPointArtifact:
    """Bind a fitted result to one absent match strictly after its cutoff."""
    if (
        type(training_result) is not DynamicTrainingResult
        or not _training_result_is_authentic(training_result)
    ):
        _fail("training_result")
    if (
        type(target_canonical_match_id) is not str
        or _ID.fullmatch(target_canonical_match_id) is None
    ):
        _fail("target_match_id")
    if target_canonical_match_id in (
        training_result.training_match_ids + training_result.validation_match_ids
    ):
        _fail("target_match_partition")
    if (
        type(target_scheduled_start_wall_ns) is not int
        or target_scheduled_start_wall_ns <= training_result.cutoff_wall_ns
    ):
        _fail("target_match_chronology")
    values = {
        "version": _ARTIFACT_VERSION,
        "target_canonical_match_id": target_canonical_match_id,
        "target_scheduled_start_wall_ns": target_scheduled_start_wall_ns,
        "cutoff_wall_ns": training_result.cutoff_wall_ns,
        "training_match_ids": training_result.training_match_ids,
        "validation_match_ids": training_result.validation_match_ids,
        "source_data_sha256": training_result.source_data_sha256,
        "feature_definition_sha256": training_result.feature_definition_sha256,
        "code_sha256": training_result.code_sha256,
        "selected": training_result.selected_candidate,
    }
    return DynamicPointArtifact(
        artifact_sha256=compute_dynamic_point_artifact_sha256(**values),
        **values,
    )


def canonical_dynamic_point_artifact_json_bytes(
    artifact: DynamicPointArtifact,
) -> bytes:
    """Return validated canonical ASCII JSON bytes for a frozen artifact."""
    if type(artifact) is not DynamicPointArtifact:
        _fail("dynamic_artifact")
    try:
        expected = compute_dynamic_point_artifact_sha256(
            version=artifact.version,
            target_canonical_match_id=artifact.target_canonical_match_id,
            target_scheduled_start_wall_ns=artifact.target_scheduled_start_wall_ns,
            cutoff_wall_ns=artifact.cutoff_wall_ns,
            training_match_ids=artifact.training_match_ids,
            validation_match_ids=artifact.validation_match_ids,
            source_data_sha256=artifact.source_data_sha256,
            feature_definition_sha256=artifact.feature_definition_sha256,
            code_sha256=artifact.code_sha256,
            selected=artifact.selected,
        )
    except Exception as error:
        raise PilotTrainingError("dynamic_artifact") from error
    if artifact.artifact_sha256 != expected:
        _fail("dynamic_artifact")
    return canonical_pilot_contract_bytes(artifact)


canonical_dynamic_point_artifact_bytes = (
    canonical_dynamic_point_artifact_json_bytes
)
