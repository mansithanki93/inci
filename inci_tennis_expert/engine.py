from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from .calibration import CalibrationArtifact, apply_calibration
from .contracts import (
    ContractSide,
    DecisionAction,
    DecisionReason,
    ExpertContractError,
    FairValueEstimate,
    OpportunityFrame,
    SyncReason,
    SynchronizationTransitionResult,
    _exact,
    _exact_self,
    _integer,
    _optional_exact,
    _safe_id,
    _sha256,
    _ticker,
    expert_contract_sha256,
    player_side_for_contract,
)
from .fee_schedule import (
    FrozenFeeSchedule,
    sealed_kalshi_taker_fee_schedule,
)
from .prematch_model import PrematchPrior
from .scalp_policy import (
    FrozenScalpPolicy,
    ScalpDecision,
    ScalpExitDecision,
    ScalpPosition,
    decide_scalp_entry,
    decide_scalp_exit,
    open_scalp_position,
    sealed_short_horizon_scalp_policy,
)
from .win_probability import live_fair_value_for_side


@dataclass(frozen=True, slots=True)
class ClipBundle:
    """Sealed fee + short-horizon clip artifact for paper observation."""

    fee_schedule: FrozenFeeSchedule
    clip_artifact: FrozenScalpPolicy

    def __post_init__(self) -> None:
        _exact_self(self, ClipBundle)
        _exact(self.fee_schedule, FrozenFeeSchedule, "fee_schedule")
        _exact(self.clip_artifact, FrozenScalpPolicy, "clip_artifact")
        if not self.fee_schedule.sealed or not self.clip_artifact.sealed:
            raise ExpertContractError("policy_unsealed")
        if (
            self.clip_artifact.fee_schedule_sha256
            != self.fee_schedule.schedule_sha256
        ):
            raise ExpertContractError("fee_schedule_sha256")


@dataclass(frozen=True, slots=True)
class ClipObservation:
    opportunity_id: str
    canonical_match_id: str
    ticker: str
    decision_sequence: int
    fair_value_sha256: str
    action: DecisionAction
    reason: DecisionReason
    entry: ScalpDecision | None
    exit: ScalpExitDecision | None
    position: ScalpPosition | None

    def __post_init__(self) -> None:
        _exact_self(self, ClipObservation)
        _sha256(self.opportunity_id, "opportunity_id")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _integer(self.decision_sequence, "decision_sequence", positive=True)
        _sha256(self.fair_value_sha256, "fair_value_sha256")
        _exact(self.action, DecisionAction, "action")
        _exact(self.reason, DecisionReason, "reason")
        _optional_exact(self.entry, ScalpDecision, "entry")
        _optional_exact(self.exit, ScalpExitDecision, "exit")
        _optional_exact(self.position, ScalpPosition, "position")
        if (self.entry is None) == (self.exit is None):
            raise ExpertContractError("observation_xor")
        if self.entry is not None:
            if (
                self.entry.action is not self.action
                or self.entry.reason is not self.reason
            ):
                raise ExpertContractError("entry")
        if self.exit is not None:
            if (
                self.exit.action is not self.action
                or self.exit.reason is not self.reason
            ):
                raise ExpertContractError("exit")


def make_default_clip_bundle(
    *,
    target_net_pnl_usd: Decimal = Decimal("3.00"),
    quantity: Decimal = Decimal("50"),
    require_calibration: bool = True,
    max_holding_wall_ns: int = 300_000_000_000,
) -> ClipBundle:
    fee_schedule = sealed_kalshi_taker_fee_schedule()
    clip_artifact = sealed_short_horizon_scalp_policy(
        fee_schedule,
        target_net_pnl_usd=target_net_pnl_usd,
        quantity=quantity,
        require_calibration=require_calibration,
        max_holding_wall_ns=max_holding_wall_ns,
    )
    return ClipBundle(
        fee_schedule=fee_schedule,
        clip_artifact=clip_artifact,
    )


def fair_value_for_opportunity(
    opportunity: OpportunityFrame,
    prior: PrematchPrior,
    calibration: CalibrationArtifact | None = None,
) -> FairValueEstimate:
    _exact(opportunity, OpportunityFrame, "opportunity")
    _exact(prior, PrematchPrior, "prior")
    if calibration is not None:
        _exact(calibration, CalibrationArtifact, "calibration")
    yes_side = player_side_for_contract(
        opportunity.snapshot.binding,
        opportunity.ticker,
        ContractSide.YES,
    )
    raw = live_fair_value_for_side(
        opportunity.snapshot.tennis,
        prior,
        yes_side,
    )
    if calibration is None:
        return raw
    return apply_calibration(
        raw,
        calibration,
        state=opportunity.snapshot.tennis,
    )


def observe_clip_on_opportunity(
    opportunity: OpportunityFrame,
    prior: PrematchPrior,
    bundle: ClipBundle,
    open_position: ScalpPosition | None = None,
    *,
    calibration: CalibrationArtifact | None = None,
) -> ClipObservation:
    _exact(opportunity, OpportunityFrame, "opportunity")
    _exact(prior, PrematchPrior, "prior")
    _exact(bundle, ClipBundle, "bundle")
    if open_position is not None:
        _exact(open_position, ScalpPosition, "open_position")
        if open_position.ticker != opportunity.ticker:
            raise ExpertContractError("ticker")

    fair_value = fair_value_for_opportunity(
        opportunity,
        prior,
        calibration,
    )
    fair_digest = expert_contract_sha256(fair_value)
    yes_side = player_side_for_contract(
        opportunity.snapshot.binding,
        opportunity.ticker,
        ContractSide.YES,
    )
    book = opportunity.snapshot.book

    if open_position is None:
        entry = decide_scalp_entry(
            fair_value,
            book,
            yes_side,
            bundle.fee_schedule,
            bundle.clip_artifact,
        )
        position: ScalpPosition | None = None
        if entry.action is DecisionAction.PAPER_BUY:
            position = open_scalp_position(
                entry,
                fair_value,
                ticker=opportunity.ticker,
                entry_wall_ns=opportunity.decision_time.wall_ns,
            )
        return ClipObservation(
            opportunity_id=opportunity.opportunity_id,
            canonical_match_id=opportunity.canonical_match_id,
            ticker=opportunity.ticker,
            decision_sequence=opportunity.decision_sequence,
            fair_value_sha256=fair_digest,
            action=entry.action,
            reason=entry.reason,
            entry=entry,
            exit=None,
            position=position,
        )

    exit_decision = decide_scalp_exit(
        open_position,
        fair_value,
        book,
        bundle.fee_schedule,
        bundle.clip_artifact,
        opportunity.decision_time.wall_ns,
    )
    remaining = (
        None
        if exit_decision.action is DecisionAction.PAPER_SELL
        else open_position
    )
    return ClipObservation(
        opportunity_id=opportunity.opportunity_id,
        canonical_match_id=opportunity.canonical_match_id,
        ticker=opportunity.ticker,
        decision_sequence=opportunity.decision_sequence,
        fair_value_sha256=fair_digest,
        action=exit_decision.action,
        reason=exit_decision.reason,
        entry=None,
        exit=exit_decision,
        position=remaining,
    )


def observe_clip_on_transition(
    transition: SynchronizationTransitionResult,
    prior: PrematchPrior,
    bundle: ClipBundle,
    open_positions: dict[str, ScalpPosition],
    *,
    calibration: CalibrationArtifact | None = None,
) -> tuple[ClipObservation, ...]:
    _exact(transition, SynchronizationTransitionResult, "transition")
    _exact(prior, PrematchPrior, "prior")
    _exact(bundle, ClipBundle, "bundle")
    if type(open_positions) is not dict:
        raise TypeError("open_positions")

    observations: list[ClipObservation] = []
    for result in transition.results:
        if result.reason is not SyncReason.TRUSTED_SYNCHRONIZED:
            continue
        if result.opportunity is None:
            continue
        ticker = result.opportunity.ticker
        current = open_positions.get(ticker)
        observation = observe_clip_on_opportunity(
            result.opportunity,
            prior,
            bundle,
            current,
            calibration=calibration,
        )
        if (
            observation.action is DecisionAction.PAPER_BUY
            and observation.position is not None
        ):
            open_positions[ticker] = observation.position
        elif observation.action is DecisionAction.PAPER_SELL:
            open_positions.pop(ticker, None)
        observations.append(observation)
    return tuple(observations)


__all__: Final[tuple[str, ...]] = (
    "ClipBundle",
    "ClipObservation",
    "fair_value_for_opportunity",
    "make_default_clip_bundle",
    "observe_clip_on_opportunity",
    "observe_clip_on_transition",
)
