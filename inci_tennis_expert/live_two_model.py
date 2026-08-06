"""State-based paper forecasts for Models 1 and 2.

This module consumes only immutable live-paper score authority.  It never
projects that authority into the offline ``PilotPointEvent`` lineage.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256

from inci_tennis_expert.contracts import PlayerSide, TennisState
from inci_tennis_expert.live_paper_contracts import (
    LivePaperPointTransition,
    LivePaperScoreAnchor,
    PaperScoreTrust,
    live_paper_contract_sha256,
    score_coordinates,
)
from inci_tennis_expert.pilot_contracts import (
    PilotOutcomeEstimate,
    ServeStrengthArtifact,
    compute_serve_strength_artifact_sha256,
    compute_training_match_ids_sha256,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate,
    DynamicPointArtifact,
    DynamicPointModel,
    compute_dynamic_point_artifact_sha256,
    evaluate_dynamic_state,
)
from inci_tennis_expert.pilot_static_model import evaluate_static_state


__all__ = (
    "LiveArtifactAuthority",
    "LiveEdgeClaim",
    "LiveForecastLabel",
    "LiveTwoModelError",
    "LiveTwoModelState",
    "LiveTwoModelForecast",
    "build_operator_bootstrap_artifacts",
    "open_live_two_model",
    "apply_live_paper_transition",
    "rebase_live_two_model",
)


class LiveTwoModelError(ValueError):
    """Fixed-code rejection for a non-contiguous paper-model update."""


class LiveArtifactAuthority(str, Enum):
    TRAINED_ARTIFACT = "TRAINED_ARTIFACT"
    OPERATOR_BOOTSTRAP = "OPERATOR_BOOTSTRAP"


class LiveEdgeClaim(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NO_EDGE_CLAIM = "NO_EDGE_CLAIM"


class LiveForecastLabel(str, Enum):
    ANCHORED_PAPER = "ANCHORED_PAPER"
    UPDATED_PAPER = "UPDATED_PAPER"
    REBASED_PAPER = "REBASED_PAPER"


_BOOTSTRAP_MATRIX = (
    (Decimal(".8"), Decimal(".15"), Decimal(".05")),
    (Decimal(".1"), Decimal(".8"), Decimal(".1")),
    (Decimal(".05"), Decimal(".15"), Decimal(".8")),
)
_BOOTSTRAP_WEIGHTS = (Decimal(".2"), Decimal(".6"), Decimal(".2"))
_BOOTSTRAP_OFFSETS = (Decimal("-.5"), Decimal("0"), Decimal(".5"))
_BOOTSTRAP_STATIC_TRAINING_IDS = ("operator-bootstrap-static-v1",)
_BOOTSTRAP_DYNAMIC_TRAINING_IDS = ("operator-bootstrap-dynamic-train-v1",)
_BOOTSTRAP_DYNAMIC_VALIDATION_IDS = ("operator-bootstrap-dynamic-validate-v1",)


def _fail(code: str) -> None:
    raise LiveTwoModelError(code)


def _sha(label: str) -> str:
    return sha256(b"INCI-LIVE-TWO-MODEL-BOOTSTRAP-V1\0" + label.encode("ascii")).hexdigest()


def _edge_claim(authority: LiveArtifactAuthority) -> LiveEdgeClaim:
    return (
        LiveEdgeClaim.NO_EDGE_CLAIM
        if authority is LiveArtifactAuthority.OPERATOR_BOOTSTRAP
        else LiveEdgeClaim.RESEARCH_ONLY
    )


def _artifact_bound_to_anchor(
    static_artifact: ServeStrengthArtifact,
    dynamic_artifact: DynamicPointArtifact,
    anchor: LivePaperScoreAnchor,
) -> bool:
    try:
        return (
            static_artifact.target_canonical_match_id == anchor.canonical_match_id
            == dynamic_artifact.target_canonical_match_id
            and static_artifact.target_scheduled_start_wall_ns
            == anchor.state.scheduled_start_wall_ns
            == dynamic_artifact.target_scheduled_start_wall_ns
            and static_artifact.cutoff_wall_ns < anchor.state.scheduled_start_wall_ns
            and dynamic_artifact.cutoff_wall_ns < anchor.state.scheduled_start_wall_ns
        )
    except Exception:
        return False


def _operator_bootstrap_components(
    static_artifact: ServeStrengthArtifact,
    dynamic_artifact: DynamicPointArtifact,
) -> tuple[bool, bool]:
    """Recognize each template component so the weaker authority cannot erase."""
    return (
        static_artifact.version == "operator-bootstrap-serve-v1",
        dynamic_artifact.version == "operator-bootstrap-dynamic-v1",
    )


def _state_digest(
    *, canonical_match_id: str, state: TennisState, local_point_ordinal: int,
    consensus_epoch: int, correction_epoch: int, rebase_epoch: int,
    static_artifact_sha256: str, dynamic_artifact_sha256: str,
    belief_sha256: str, source_sha256: str, anchor_sha256: str,
    transition_sha256: str | None, authority: LiveArtifactAuthority,
) -> str:
    return live_paper_contract_sha256({
        "canonical_match_id": canonical_match_id,
        "state": state,
        "local_point_ordinal": local_point_ordinal,
        "consensus_epoch": consensus_epoch,
        "correction_epoch": correction_epoch,
        "rebase_epoch": rebase_epoch,
        "static_artifact_sha256": static_artifact_sha256,
        "dynamic_artifact_sha256": dynamic_artifact_sha256,
        "belief_sha256": belief_sha256,
        "source_sha256": source_sha256,
        "anchor_sha256": anchor_sha256,
        "transition_sha256": transition_sha256,
        "authority": authority,
    })


def _source_digest(
    *, supporting_lineage_sha256s: tuple[str, ...],
    parent_receipt_sha256s: tuple[str, ...],
) -> str:
    return live_paper_contract_sha256({
        "supporting_lineage_sha256s": supporting_lineage_sha256s,
        "parent_receipt_sha256s": parent_receipt_sha256s,
    })


@dataclass(frozen=True, slots=True)
class LiveTwoModelState:
    canonical_match_id: str
    static_artifact: ServeStrengthArtifact
    dynamic_artifact: DynamicPointArtifact
    current_state: TennisState
    dynamic_model: DynamicPointModel
    local_point_ordinal: int
    consensus_epoch: int
    correction_epoch: int
    rebase_epoch: int
    source_sha256: str
    anchor_sha256: str
    transition_sha256: str | None
    artifact_authority: LiveArtifactAuthority
    state_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.canonical_match_id) is not str or not self.canonical_match_id
            or type(self.static_artifact) is not ServeStrengthArtifact
            or type(self.dynamic_artifact) is not DynamicPointArtifact
            or type(self.current_state) is not TennisState
            or type(self.dynamic_model) is not DynamicPointModel
            or type(self.local_point_ordinal) is not int or self.local_point_ordinal < 0
            or any(type(epoch) is not int or epoch < 0 for epoch in (
                self.consensus_epoch, self.correction_epoch, self.rebase_epoch,
            ))
            or type(self.artifact_authority) is not LiveArtifactAuthority
        ):
            _fail("state")
        if (
            self.dynamic_model.serve_artifact != self.static_artifact
            or self.dynamic_model.dynamic_artifact != self.dynamic_artifact
            or self.correction_epoch != self.current_state.correction_epoch
            or self.state_sha256 != _state_digest(
                canonical_match_id=self.canonical_match_id,
                state=self.current_state,
                local_point_ordinal=self.local_point_ordinal,
                consensus_epoch=self.consensus_epoch,
                correction_epoch=self.correction_epoch,
                rebase_epoch=self.rebase_epoch,
                static_artifact_sha256=self.static_artifact.artifact_sha256,
                dynamic_artifact_sha256=self.dynamic_artifact.artifact_sha256,
                belief_sha256=self.dynamic_model.belief.belief_sha256,
                source_sha256=self.source_sha256,
                anchor_sha256=self.anchor_sha256,
                transition_sha256=self.transition_sha256,
                authority=self.artifact_authority,
            )
        ):
            _fail("state_sha256")


@dataclass(frozen=True, slots=True)
class LiveTwoModelForecast:
    trust: PaperScoreTrust
    forecast_label: str
    artifact_authority: LiveArtifactAuthority
    edge_claim: LiveEdgeClaim
    model_1: PilotOutcomeEstimate
    model_2: PilotOutcomeEstimate
    model_2_prior_belief_sha256: str
    model_2_posterior_belief_sha256: str
    static_artifact_sha256: str
    dynamic_artifact_sha256: str
    source_sha256: str
    anchor_sha256: str
    transition_sha256: str | None
    resulting_state_sha256: str
    supported: bool
    abstention_reason: str | None

    @property
    def authority(self) -> LiveArtifactAuthority:
        """Compatibility-friendly short form of the visible authority label."""
        return self.artifact_authority

    @property
    def authority_label(self) -> str:
        """The operator-visible authority/edge boundary for this forecast."""
        return f"{self.artifact_authority.value} / {self.edge_claim.value}"

    @property
    def rebase_state(self) -> str:
        """Expose the paper/rebase label under its operational name."""
        return self.forecast_label

    @property
    def model_1_next_point_probability(self) -> Decimal | None:
        return self.model_1.home_next_point_probability

    @property
    def model_1_current_set_probability(self) -> Decimal | None:
        return self.model_1.home_current_set_probability

    @property
    def model_1_match_probability(self) -> Decimal | None:
        return self.model_1.home_match_probability

    @property
    def model_2_next_point_probability(self) -> Decimal | None:
        return self.model_2.home_next_point_probability

    @property
    def model_2_current_set_probability(self) -> Decimal | None:
        return self.model_2.home_current_set_probability

    @property
    def model_2_match_probability(self) -> Decimal | None:
        return self.model_2.home_match_probability


def _make_state(
    *, static_artifact: ServeStrengthArtifact, dynamic_artifact: DynamicPointArtifact,
    current_state: TennisState, dynamic_model: DynamicPointModel,
    local_point_ordinal: int, consensus_epoch: int, correction_epoch: int,
    rebase_epoch: int, source_sha256: str, anchor_sha256: str,
    transition_sha256: str | None, authority: LiveArtifactAuthority,
) -> LiveTwoModelState:
    values = dict(
        canonical_match_id=dynamic_artifact.target_canonical_match_id,
        static_artifact=static_artifact, dynamic_artifact=dynamic_artifact,
        current_state=current_state, dynamic_model=dynamic_model,
        local_point_ordinal=local_point_ordinal, consensus_epoch=consensus_epoch,
        correction_epoch=correction_epoch, rebase_epoch=rebase_epoch,
        source_sha256=source_sha256, anchor_sha256=anchor_sha256,
        transition_sha256=transition_sha256, artifact_authority=authority,
    )
    return LiveTwoModelState(
        state_sha256=_state_digest(
            canonical_match_id=values["canonical_match_id"], state=current_state,
            local_point_ordinal=local_point_ordinal, consensus_epoch=consensus_epoch,
            correction_epoch=correction_epoch, rebase_epoch=rebase_epoch,
            static_artifact_sha256=static_artifact.artifact_sha256,
            dynamic_artifact_sha256=dynamic_artifact.artifact_sha256,
            belief_sha256=dynamic_model.belief.belief_sha256,
            source_sha256=source_sha256, anchor_sha256=anchor_sha256,
            transition_sha256=transition_sha256, authority=authority,
        ),
        **values,
    )  # type: ignore[arg-type]


def _forecast(
    state: LiveTwoModelState, *, trust: PaperScoreTrust, label: LiveForecastLabel,
    prior_belief_sha256: str,
) -> LiveTwoModelForecast:
    model_1 = evaluate_static_state(
        canonical_match_id=state.canonical_match_id, state=state.current_state,
        artifact=state.static_artifact,
    )
    model_2 = evaluate_dynamic_state(
        model=state.dynamic_model, canonical_match_id=state.canonical_match_id,
        state=state.current_state,
    )
    supported = model_1.supported and model_2.supported
    reasons = (model_1.abstention_reason, model_2.abstention_reason)
    return LiveTwoModelForecast(
        trust=trust, forecast_label=label.value,
        artifact_authority=state.artifact_authority,
        edge_claim=_edge_claim(state.artifact_authority), model_1=model_1, model_2=model_2,
        model_2_prior_belief_sha256=prior_belief_sha256,
        model_2_posterior_belief_sha256=state.dynamic_model.belief.belief_sha256,
        static_artifact_sha256=state.static_artifact.artifact_sha256,
        dynamic_artifact_sha256=state.dynamic_artifact.artifact_sha256,
        source_sha256=state.source_sha256, anchor_sha256=state.anchor_sha256,
        transition_sha256=state.transition_sha256,
        resulting_state_sha256=state.state_sha256,
        supported=supported,
        abstention_reason=None if supported else next(
            reason.value for reason in reasons if reason is not None
        ),
    )


def build_operator_bootstrap_artifacts(
    *, canonical_match_id: str, scheduled_start_wall_ns: int, cutoff_wall_ns: int,
    home_serve_point_probability: Decimal, away_serve_point_probability: Decimal,
) -> tuple[ServeStrengthArtifact, DynamicPointArtifact]:
    """Freeze the documented, non-edge bootstrap parameters for one target."""
    if type(canonical_match_id) is not str or not canonical_match_id:
        _fail("canonical_match_id")
    if (
        type(scheduled_start_wall_ns) is not int or scheduled_start_wall_ns <= 0
        or type(cutoff_wall_ns) is not int or cutoff_wall_ns < 0
        or cutoff_wall_ns >= scheduled_start_wall_ns
    ):
        _fail("cutoff_wall_ns")
    priors = (home_serve_point_probability, away_serve_point_probability)
    if any(
        type(value) is not Decimal or not value.is_finite()
        or not Decimal("0") < value < Decimal("1") for value in priors
    ):
        _fail("serve_probability")
    static_values = {
        "version": "operator-bootstrap-serve-v1",
        "target_canonical_match_id": canonical_match_id,
        "target_scheduled_start_wall_ns": scheduled_start_wall_ns,
        "cutoff_wall_ns": cutoff_wall_ns,
        "training_match_ids": _BOOTSTRAP_STATIC_TRAINING_IDS,
        "training_match_ids_sha256": compute_training_match_ids_sha256(_BOOTSTRAP_STATIC_TRAINING_IDS),
        "source_data_sha256": _sha("operator-supplied-serve-priors"),
        "feature_definition_sha256": _sha("no-market-prior-bootstrap"),
        "code_sha256": _sha("operator-bootstrap-template-v1"),
        "home_serve_point_probability": home_serve_point_probability,
        "away_serve_point_probability": away_serve_point_probability,
    }
    static = ServeStrengthArtifact(
        artifact_sha256=compute_serve_strength_artifact_sha256(**static_values),
        **static_values,
    )
    selected = DynamicParameterCandidate(
        transition_matrix=_BOOTSTRAP_MATRIX,
        home_initial_weights=_BOOTSTRAP_WEIGHTS,
        away_initial_weights=_BOOTSTRAP_WEIGHTS,
        logit_offsets=_BOOTSTRAP_OFFSETS,
    )
    dynamic_values = {
        "version": "operator-bootstrap-dynamic-v1",
        "target_canonical_match_id": canonical_match_id,
        "target_scheduled_start_wall_ns": scheduled_start_wall_ns,
        "cutoff_wall_ns": cutoff_wall_ns,
        "training_match_ids": _BOOTSTRAP_DYNAMIC_TRAINING_IDS,
        "validation_match_ids": _BOOTSTRAP_DYNAMIC_VALIDATION_IDS,
        "source_data_sha256": _sha("operator-bootstrap-dynamic-template"),
        "feature_definition_sha256": _sha("three-state-synthetic-plumbing"),
        "code_sha256": _sha("operator-bootstrap-template-v1"),
        "selected": selected,
    }
    dynamic = DynamicPointArtifact(
        artifact_sha256=compute_dynamic_point_artifact_sha256(**dynamic_values),
        **dynamic_values,
    )
    return static, dynamic


def open_live_two_model(
    *, static_artifact: ServeStrengthArtifact, dynamic_artifact: DynamicPointArtifact,
    anchor: LivePaperScoreAnchor, artifact_authority: LiveArtifactAuthority,
) -> tuple[LiveTwoModelState, LiveTwoModelForecast]:
    """Open both models at a durable anchor without inventing prior points."""
    if (
        type(static_artifact) is not ServeStrengthArtifact
        or type(dynamic_artifact) is not DynamicPointArtifact
        or type(anchor) is not LivePaperScoreAnchor
        or type(artifact_authority) is not LiveArtifactAuthority
        or not _artifact_bound_to_anchor(static_artifact, dynamic_artifact, anchor)
    ):
        _fail("artifact_mismatch")
    static_bootstrap, dynamic_bootstrap = _operator_bootstrap_components(
        static_artifact, dynamic_artifact
    )
    if (
        static_bootstrap != dynamic_bootstrap
        or static_bootstrap
        != (artifact_authority is LiveArtifactAuthority.OPERATOR_BOOTSTRAP)
    ):
        _fail("artifact_authority")
    model = DynamicPointModel.initialize(
        serve_artifact=static_artifact, dynamic_artifact=dynamic_artifact,
    )
    state = _make_state(
        static_artifact=static_artifact, dynamic_artifact=dynamic_artifact,
        current_state=anchor.state, dynamic_model=model, local_point_ordinal=0,
        consensus_epoch=anchor.consensus_epoch, correction_epoch=anchor.correction_epoch,
        rebase_epoch=anchor.rebase_epoch,
        source_sha256=_source_digest(
            supporting_lineage_sha256s=anchor.supporting_lineage_sha256s,
            parent_receipt_sha256s=anchor.parent_receipt_sha256s,
        ),
        anchor_sha256=anchor.anchor_sha256, transition_sha256=None,
        authority=artifact_authority,
    )
    return state, _forecast(
        state, trust=anchor.trust, label=LiveForecastLabel.ANCHORED_PAPER,
        prior_belief_sha256=model.belief.belief_sha256,
    )


def apply_live_paper_transition(
    state: LiveTwoModelState, transition: LivePaperPointTransition,
) -> tuple[LiveTwoModelState, LiveTwoModelForecast]:
    """Accept exactly one durable point and update only its server posterior."""
    if type(state) is not LiveTwoModelState or type(transition) is not LivePaperPointTransition:
        _fail("transition")
    if (
        transition.canonical_match_id != state.canonical_match_id
        or transition.local_point_ordinal != state.local_point_ordinal + 1
        or score_coordinates(transition.before_state) != score_coordinates(state.current_state)
        or transition.consensus_epoch != state.consensus_epoch
        or transition.correction_epoch != state.correction_epoch
        or transition.rebase_epoch != state.rebase_epoch
    ):
        _fail("local_point_ordinal")
    prior = state.dynamic_model.belief.belief_sha256
    try:
        model, _ = state.dynamic_model.observe_state_point(
            canonical_match_id=state.canonical_match_id,
            point_id=transition.transition_sha256,
            belief_point_event_sha256=transition.transition_sha256,
            server=transition.server, winner=transition.winner,
        )
    except Exception as error:
        raise LiveTwoModelError("transition") from error
    next_state = _make_state(
        static_artifact=state.static_artifact, dynamic_artifact=state.dynamic_artifact,
        current_state=transition.after_state, dynamic_model=model,
        local_point_ordinal=transition.local_point_ordinal,
        consensus_epoch=transition.consensus_epoch,
        correction_epoch=transition.correction_epoch,
        rebase_epoch=transition.rebase_epoch,
        source_sha256=_source_digest(
            supporting_lineage_sha256s=transition.supporting_lineage_sha256s,
            parent_receipt_sha256s=transition.parent_receipt_sha256s,
        ),
        anchor_sha256=state.anchor_sha256,
        transition_sha256=transition.transition_sha256,
        authority=state.artifact_authority,
    )
    return next_state, _forecast(
        next_state, trust=transition.trust, label=LiveForecastLabel.UPDATED_PAPER,
        prior_belief_sha256=prior,
    )


def rebase_live_two_model(
    state: LiveTwoModelState, anchor: LivePaperScoreAnchor,
) -> tuple[LiveTwoModelState, LiveTwoModelForecast]:
    """Reset Model 2 from frozen belief after an explicit score discontinuity."""
    if (
        type(state) is not LiveTwoModelState or type(anchor) is not LivePaperScoreAnchor
        or anchor.canonical_match_id != state.canonical_match_id
        or anchor.rebase_epoch != state.rebase_epoch + 1
        or not _artifact_bound_to_anchor(state.static_artifact, state.dynamic_artifact, anchor)
    ):
        _fail("rebase")
    model = DynamicPointModel.initialize(
        serve_artifact=state.static_artifact, dynamic_artifact=state.dynamic_artifact,
    )
    next_state = _make_state(
        static_artifact=state.static_artifact, dynamic_artifact=state.dynamic_artifact,
        current_state=anchor.state, dynamic_model=model, local_point_ordinal=0,
        consensus_epoch=anchor.consensus_epoch, correction_epoch=anchor.correction_epoch,
        rebase_epoch=anchor.rebase_epoch,
        source_sha256=_source_digest(
            supporting_lineage_sha256s=anchor.supporting_lineage_sha256s,
            parent_receipt_sha256s=anchor.parent_receipt_sha256s,
        ),
        anchor_sha256=anchor.anchor_sha256, transition_sha256=None,
        authority=state.artifact_authority,
    )
    return next_state, _forecast(
        next_state, trust=anchor.trust, label=LiveForecastLabel.REBASED_PAPER,
        prior_belief_sha256=model.belief.belief_sha256,
    )
