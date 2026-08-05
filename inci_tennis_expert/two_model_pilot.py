"""Deterministic, offline comparison of the two research-only pilot models."""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import Enum
from re import ASCII as RE_ASCII
from re import compile as pattern_compile

from inci_tennis_expert.contracts import SetScore, TennisState
from inci_tennis_expert.pilot_contracts import (
    DynamicBeliefSnapshot,
    PilotOutcomeEstimate,
    PilotPointEvent,
    PilotSupportReason,
    ServeStrengthArtifact,
    canonical_pilot_contract_bytes,
    compute_serve_strength_artifact_sha256,
    pilot_contract_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    DynamicPointArtifact,
    DynamicPointModel,
    DynamicPointModelError,
    compute_dynamic_point_artifact_sha256,
    unsupported_dynamic,
)
from inci_tennis_expert.pilot_static_model import (
    evaluate_static_outcome,
    unsupported_static,
)


__all__ = (
    "TwoModelAbstentionReason",
    "TwoModelComparisonRow",
    "TwoModelPilotError",
    "TwoModelPilotState",
    "TwoModelRowStatus",
    "encode_two_model_rows",
    "initialize_two_model_pilot",
    "run_two_model_event",
)


_CODE_VERSION = "two-model-pilot-v1"
_SCHEMA_VERSION = "two-model-comparison-v1"
_CLAIM = "PLUMBING_ONLY"
_AUTHORITY = "RESEARCH_ONLY / NO_ORDERS"
_ID = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", RE_ASCII)
_MAX_SIGNED_64 = 9_223_372_036_854_775_807


class TwoModelPilotError(ValueError):
    """Fixed-code rejection for an invalid pilot initialization or value."""


class TwoModelRowStatus(str, Enum):
    MODELS_EVALUATED = "models_evaluated"
    ABSTAINED = "abstained"


class TwoModelAbstentionReason(str, Enum):
    DUPLICATE_POINT = "duplicate_point"
    SEQUENCE_GAP = "sequence_gap"
    CORRECTION_EPOCH_CHANGED = "correction_epoch_changed"
    STATE_DISCONTINUITY = "state_discontinuity"
    MATCH_MISMATCH = "match_mismatch"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    INVALID_EVENT = "invalid_event"


def _fail(code: str) -> None:
    raise TwoModelPilotError(code)


def _serve_artifact_is_authentic(artifact: object) -> bool:
    if type(artifact) is not ServeStrengthArtifact:
        return False
    try:
        rebuilt = ServeStrengthArtifact(
            **{field.name: getattr(artifact, field.name) for field in fields(artifact)}
        )
        return (
            rebuilt == artifact
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


def _dynamic_artifact_is_authentic(artifact: object) -> bool:
    if type(artifact) is not DynamicPointArtifact:
        return False
    try:
        selected = DynamicParameterCandidate(
            **{
                field.name: getattr(artifact.selected, field.name)
                for field in fields(DynamicParameterCandidate)
            }
        )
        values = {
            field.name: getattr(artifact, field.name)
            for field in fields(DynamicPointArtifact)
        }
        values["selected"] = selected
        rebuilt = DynamicPointArtifact(**values)
        return (
            rebuilt == artifact
            and artifact.artifact_sha256
            == compute_dynamic_point_artifact_sha256(
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
        )
    except Exception:
        return False


def _event_is_authentic(event: object) -> bool:
    if type(event) is not PilotPointEvent:
        return False
    try:
        states: list[TennisState] = []
        for state in (event.before_state, event.after_state):
            if type(state) is not TennisState or type(state.completed_sets) is not tuple:
                return False
            completed_sets = tuple(
                SetScore(
                    **{
                        field.name: getattr(set_score, field.name)
                        for field in fields(SetScore)
                    }
                )
                for set_score in state.completed_sets
                if type(set_score) is SetScore
            )
            if len(completed_sets) != len(state.completed_sets):
                return False
            values = {
                field.name: getattr(state, field.name)
                for field in fields(TennisState)
            }
            values["completed_sets"] = completed_sets
            states.append(TennisState(**values))
        values = {
            field.name: getattr(event, field.name)
            for field in fields(PilotPointEvent)
        }
        values["before_state"], values["after_state"] = states
        return PilotPointEvent(**values) == event
    except Exception:
        return False


def _rebuilt_tennis_state(state: object) -> TennisState:
    if type(state) is not TennisState or type(state.completed_sets) is not tuple:
        _fail("state")
    completed_sets = tuple(
        SetScore(
            **{
                field.name: getattr(set_score, field.name)
                for field in fields(SetScore)
            }
        )
        for set_score in state.completed_sets
        if type(set_score) is SetScore
    )
    if len(completed_sets) != len(state.completed_sets):
        _fail("state")
    values = {
        field.name: getattr(state, field.name)
        for field in fields(TennisState)
    }
    values["completed_sets"] = completed_sets
    return TennisState(**values)


def _state_is_authentic(state: TwoModelPilotState) -> bool:
    try:
        belief = DynamicBeliefSnapshot(
            **{
                field.name: getattr(state.dynamic_model.belief, field.name)
                for field in fields(DynamicBeliefSnapshot)
            }
        )
        dynamic_model = DynamicPointModel(
            serve_artifact=state.static_artifact,
            dynamic_artifact=state.dynamic_artifact,
            belief=belief,
            observed_point_ids=state.dynamic_model.observed_point_ids,
        )
        last_after_state = (
            None
            if state.last_after_state is None
            else _rebuilt_tennis_state(state.last_after_state)
        )
        rebuilt = TwoModelPilotState(
            schema_version=state.schema_version,
            code_version=state.code_version,
            static_artifact=state.static_artifact,
            dynamic_artifact=state.dynamic_artifact,
            dynamic_model=dynamic_model,
            last_valid_sequence_number=state.last_valid_sequence_number,
            seen_point_ids=state.seen_point_ids,
            correction_epoch=state.correction_epoch,
            last_after_state=last_after_state,
            state_sha256=state.state_sha256,
        )
        return rebuilt == state
    except Exception:
        return False


def _state_projection(state: TwoModelPilotState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "code_version": state.code_version,
        "static_artifact": state.static_artifact,
        "dynamic_artifact": state.dynamic_artifact,
        "dynamic_model": state.dynamic_model,
        "last_valid_sequence_number": state.last_valid_sequence_number,
        "seen_point_ids": state.seen_point_ids,
        "correction_epoch": state.correction_epoch,
        "last_after_state": state.last_after_state,
    }


@dataclass(frozen=True, slots=True)
class TwoModelPilotState:
    schema_version: str
    code_version: str
    static_artifact: ServeStrengthArtifact
    dynamic_artifact: DynamicPointArtifact
    dynamic_model: DynamicPointModel
    last_valid_sequence_number: int
    seen_point_ids: tuple[str, ...]
    correction_epoch: int
    last_after_state: TennisState | None
    state_sha256: str

    def __post_init__(self) -> None:
        if (
            self.schema_version != _SCHEMA_VERSION
            or self.code_version != _CODE_VERSION
            or type(self.static_artifact) is not ServeStrengthArtifact
            or type(self.dynamic_artifact) is not DynamicPointArtifact
            or type(self.dynamic_model) is not DynamicPointModel
            or type(self.last_valid_sequence_number) is not int
            or self.last_valid_sequence_number < 0
            or type(self.seen_point_ids) is not tuple
            or any(type(point_id) is not str for point_id in self.seen_point_ids)
            or len(set(self.seen_point_ids)) != len(self.seen_point_ids)
            or len(self.seen_point_ids) != self.last_valid_sequence_number
            or type(self.correction_epoch) is not int
            or self.correction_epoch < 0
            or (
                self.last_after_state is not None
                and type(self.last_after_state) is not TennisState
            )
            or (self.last_valid_sequence_number == 0) != (self.last_after_state is None)
            or self.dynamic_model.serve_artifact != self.static_artifact
            or self.dynamic_model.dynamic_artifact != self.dynamic_artifact
            or self.dynamic_model.observed_point_ids != self.seen_point_ids
            or self.state_sha256 != pilot_contract_sha256(_state_projection(self))
        ):
            _fail("state")


def _row_projection(row: TwoModelComparisonRow) -> dict[str, object]:
    return {
        "schema_version": row.schema_version,
        "code_version": row.code_version,
        "claim": row.claim,
        "authority": row.authority,
        "status": row.status,
        "abstention_reason": row.abstention_reason,
        "canonical_match_id": row.canonical_match_id,
        "point_id": row.point_id,
        "sequence_number": row.sequence_number,
        "point_event_sha256": row.point_event_sha256,
        "static_artifact_sha256": row.static_artifact_sha256,
        "dynamic_artifact_sha256": row.dynamic_artifact_sha256,
        "prior_state_sha256": row.prior_state_sha256,
        "resulting_state_sha256": row.resulting_state_sha256,
        "dynamic_pre_home_point_probability": row.dynamic_pre_home_point_probability,
        "dynamic_prior_belief": row.dynamic_prior_belief,
        "dynamic_post_belief": row.dynamic_post_belief,
        "model_1_static": row.model_1_static,
        "model_2_dynamic": row.model_2_dynamic,
    }


@dataclass(frozen=True, slots=True)
class TwoModelComparisonRow:
    schema_version: str
    code_version: str
    claim: str
    authority: str
    status: TwoModelRowStatus
    abstention_reason: TwoModelAbstentionReason | None
    canonical_match_id: str
    point_id: str
    sequence_number: int
    point_event_sha256: str
    static_artifact_sha256: str
    dynamic_artifact_sha256: str
    prior_state_sha256: str
    resulting_state_sha256: str
    dynamic_pre_home_point_probability: Decimal | None
    dynamic_prior_belief: DynamicBeliefSnapshot
    dynamic_post_belief: DynamicBeliefSnapshot
    model_1_static: PilotOutcomeEstimate
    model_2_dynamic: PilotOutcomeEstimate
    row_sha256: str

    def __post_init__(self) -> None:
        digests = (
            self.point_event_sha256,
            self.static_artifact_sha256,
            self.dynamic_artifact_sha256,
            self.prior_state_sha256,
            self.resulting_state_sha256,
            self.row_sha256,
        )
        if (
            self.schema_version != _SCHEMA_VERSION
            or self.code_version != _CODE_VERSION
            or self.claim != _CLAIM
            or self.authority != _AUTHORITY
            or type(self.status) is not TwoModelRowStatus
            or (
                self.abstention_reason is not None
                and type(self.abstention_reason) is not TwoModelAbstentionReason
            )
            or type(self.canonical_match_id) is not str
            or _ID.fullmatch(self.canonical_match_id) is None
            or type(self.point_id) is not str
            or _ID.fullmatch(self.point_id) is None
            or type(self.sequence_number) is not int
            or self.sequence_number <= 0
            or self.sequence_number > _MAX_SIGNED_64
            or any(
                type(digest) is not str
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                for digest in digests
            )
            or (
                self.dynamic_pre_home_point_probability is not None
                and (
                    type(self.dynamic_pre_home_point_probability) is not Decimal
                    or not self.dynamic_pre_home_point_probability.is_finite()
                    or not Decimal("0")
                    <= self.dynamic_pre_home_point_probability
                    <= Decimal("1")
                )
            )
            or type(self.dynamic_prior_belief) is not DynamicBeliefSnapshot
            or type(self.dynamic_post_belief) is not DynamicBeliefSnapshot
            or type(self.model_1_static) is not PilotOutcomeEstimate
            or type(self.model_2_dynamic) is not PilotOutcomeEstimate
            or self.row_sha256 != pilot_contract_sha256(_row_projection(self))
        ):
            _fail("row")
        if self.status is TwoModelRowStatus.MODELS_EVALUATED:
            if (
                self.abstention_reason is not None
                or self.dynamic_pre_home_point_probability is None
            ):
                _fail("row_status")
        elif (
            self.abstention_reason is None
            or self.dynamic_pre_home_point_probability is not None
            or self.prior_state_sha256 != self.resulting_state_sha256
            or self.dynamic_prior_belief != self.dynamic_post_belief
            or self.model_1_static.supported
            or self.model_2_dynamic.supported
        ):
            _fail("row_status")


def _make_state(**values: object) -> TwoModelPilotState:
    return TwoModelPilotState(
        state_sha256=pilot_contract_sha256(values),
        **values,  # type: ignore[arg-type]
    )


def _make_row(**values: object) -> TwoModelComparisonRow:
    return TwoModelComparisonRow(
        row_sha256=pilot_contract_sha256(values),
        **values,  # type: ignore[arg-type]
    )


def initialize_two_model_pilot(
    static_artifact: ServeStrengthArtifact,
    dynamic_artifact: DynamicPointArtifact,
) -> TwoModelPilotState:
    """Initialize the immutable two-model replay state from frozen artifacts."""
    if (
        not _serve_artifact_is_authentic(static_artifact)
        or not _dynamic_artifact_is_authentic(dynamic_artifact)
        or static_artifact.target_canonical_match_id
        != dynamic_artifact.target_canonical_match_id
        or static_artifact.target_scheduled_start_wall_ns
        != dynamic_artifact.target_scheduled_start_wall_ns
    ):
        _fail("artifact_mismatch")
    try:
        dynamic_model = DynamicPointModel.initialize(
            serve_artifact=static_artifact,
            dynamic_artifact=dynamic_artifact,
        )
    except DynamicPointModelError as error:
        raise TwoModelPilotError("artifact_mismatch") from error
    return _make_state(
        schema_version=_SCHEMA_VERSION,
        code_version=_CODE_VERSION,
        static_artifact=static_artifact,
        dynamic_artifact=dynamic_artifact,
        dynamic_model=dynamic_model,
        last_valid_sequence_number=0,
        seen_point_ids=(),
        correction_epoch=0,
        last_after_state=None,
    )


def _validation_reason(
    state: TwoModelPilotState,
    event: PilotPointEvent,
) -> TwoModelAbstentionReason | None:
    if (
        not _serve_artifact_is_authentic(state.static_artifact)
        or not _dynamic_artifact_is_authentic(state.dynamic_artifact)
        or state.dynamic_model.serve_artifact != state.static_artifact
        or state.dynamic_model.dynamic_artifact != state.dynamic_artifact
    ):
        return TwoModelAbstentionReason.ARTIFACT_MISMATCH
    if not _state_is_authentic(state):
        _fail("state")
    if not _event_is_authentic(event):
        return TwoModelAbstentionReason.INVALID_EVENT
    if event.canonical_match_id != state.static_artifact.target_canonical_match_id:
        return TwoModelAbstentionReason.MATCH_MISMATCH
    if event.point_id in state.seen_point_ids:
        return TwoModelAbstentionReason.DUPLICATE_POINT
    if event.sequence_number != state.last_valid_sequence_number + 1:
        return TwoModelAbstentionReason.SEQUENCE_GAP
    if (
        event.consensus_epoch != state.correction_epoch
        or event.before_state.correction_epoch != state.correction_epoch
        or event.after_state.correction_epoch != state.correction_epoch
    ):
        return TwoModelAbstentionReason.CORRECTION_EPOCH_CHANGED
    if (
        state.last_after_state is not None
        and event.before_state != state.last_after_state
    ):
        return TwoModelAbstentionReason.STATE_DISCONTINUITY
    if (
        event.before_state.scheduled_start_wall_ns
        != state.static_artifact.target_scheduled_start_wall_ns
        or event.after_state.scheduled_start_wall_ns
        != state.static_artifact.target_scheduled_start_wall_ns
    ):
        return TwoModelAbstentionReason.MATCH_MISMATCH
    return None


def _support_reason(reason: TwoModelAbstentionReason) -> PilotSupportReason:
    if reason is TwoModelAbstentionReason.DUPLICATE_POINT:
        return PilotSupportReason.DUPLICATE_POINT
    if reason is TwoModelAbstentionReason.CORRECTION_EPOCH_CHANGED:
        return PilotSupportReason.SCORE_CORRECTED
    if reason is TwoModelAbstentionReason.ARTIFACT_MISMATCH:
        return PilotSupportReason.ARTIFACT_MISMATCH
    return PilotSupportReason.INVALID_POINT_TRANSITION


def _abstention_row(
    state: TwoModelPilotState,
    event: PilotPointEvent,
    reason: TwoModelAbstentionReason,
) -> TwoModelComparisonRow:
    support_reason = _support_reason(reason)
    event_sha256 = pilot_contract_sha256(event)
    canonical_match_id = (
        event.canonical_match_id
        if type(event.canonical_match_id) is str
        and _ID.fullmatch(event.canonical_match_id) is not None
        else f"invalid-match-{event_sha256[:16]}"
    )
    point_id = (
        event.point_id
        if type(event.point_id) is str
        and _ID.fullmatch(event.point_id) is not None
        else f"invalid-point-{event_sha256[:16]}"
    )
    sequence_number = (
        event.sequence_number
        if type(event.sequence_number) is int
        and 0 < event.sequence_number <= _MAX_SIGNED_64
        else 1
    )
    return _make_row(
        schema_version=_SCHEMA_VERSION,
        code_version=_CODE_VERSION,
        claim=_CLAIM,
        authority=_AUTHORITY,
        status=TwoModelRowStatus.ABSTAINED,
        abstention_reason=reason,
        canonical_match_id=canonical_match_id,
        point_id=point_id,
        sequence_number=sequence_number,
        point_event_sha256=event_sha256,
        static_artifact_sha256=state.static_artifact.artifact_sha256,
        dynamic_artifact_sha256=state.dynamic_artifact.artifact_sha256,
        prior_state_sha256=state.state_sha256,
        resulting_state_sha256=state.state_sha256,
        dynamic_pre_home_point_probability=None,
        dynamic_prior_belief=state.dynamic_model.belief,
        dynamic_post_belief=state.dynamic_model.belief,
        model_1_static=unsupported_static(support_reason),
        model_2_dynamic=unsupported_dynamic(support_reason),
    )


def run_two_model_event(
    state: TwoModelPilotState,
    event: PilotPointEvent,
) -> tuple[TwoModelPilotState, TwoModelComparisonRow]:
    """Validate and evaluate one ordered event without mutating prior state."""
    if type(state) is not TwoModelPilotState or type(event) is not PilotPointEvent:
        _fail("event")
    reason = _validation_reason(state, event)
    if reason is not None:
        return state, _abstention_row(state, event, reason)

    prior_belief = state.dynamic_model.belief
    try:
        dynamic_pre = state.dynamic_model.predictive_home_point_probability(event)
        next_dynamic_model, post_belief = state.dynamic_model.observe(event)
    except DynamicPointModelError:
        reason = TwoModelAbstentionReason.ARTIFACT_MISMATCH
        return state, _abstention_row(state, event, reason)
    static_outcome = evaluate_static_outcome(event, state.static_artifact)
    dynamic_outcome = next_dynamic_model.evaluate(event)
    if not static_outcome.supported or not dynamic_outcome.supported:
        reason = TwoModelAbstentionReason.ARTIFACT_MISMATCH
        return state, _abstention_row(state, event, reason)

    next_state = _make_state(
        schema_version=_SCHEMA_VERSION,
        code_version=_CODE_VERSION,
        static_artifact=state.static_artifact,
        dynamic_artifact=state.dynamic_artifact,
        dynamic_model=next_dynamic_model,
        last_valid_sequence_number=event.sequence_number,
        seen_point_ids=(*state.seen_point_ids, event.point_id),
        correction_epoch=event.consensus_epoch,
        last_after_state=event.after_state,
    )
    row = _make_row(
        schema_version=_SCHEMA_VERSION,
        code_version=_CODE_VERSION,
        claim=_CLAIM,
        authority=_AUTHORITY,
        status=TwoModelRowStatus.MODELS_EVALUATED,
        abstention_reason=None,
        canonical_match_id=event.canonical_match_id,
        point_id=event.point_id,
        sequence_number=event.sequence_number,
        point_event_sha256=pilot_contract_sha256(event),
        static_artifact_sha256=state.static_artifact.artifact_sha256,
        dynamic_artifact_sha256=state.dynamic_artifact.artifact_sha256,
        prior_state_sha256=state.state_sha256,
        resulting_state_sha256=next_state.state_sha256,
        dynamic_pre_home_point_probability=dynamic_pre,
        dynamic_prior_belief=prior_belief,
        dynamic_post_belief=post_belief,
        model_1_static=static_outcome,
        model_2_dynamic=dynamic_outcome,
    )
    return next_state, row


def encode_two_model_rows(rows: tuple[TwoModelComparisonRow, ...]) -> bytes:
    """Encode comparison rows as byte-stable canonical JSONL."""
    if type(rows) is not tuple or any(
        type(row) is not TwoModelComparisonRow for row in rows
    ):
        _fail("rows")
    return b"".join(canonical_pilot_contract_bytes(row) + b"\n" for row in rows)
