from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .contracts import ContractSide
from .fee_schedule import FillSide, LiquidityRole, fee_for_fill
from .five_minute_path import (
    EntryAction,
    EntryDecision,
    EntryGateInput,
    EntryReason,
    EntrySnapshotBinding,
    PriceLevel,
    evaluate_entry,
    price_levels_sha256,
    size_ioc_entry,
)
from .risk import (
    FrozenRiskPolicy,
    RiskState,
)
from .strategy_artifacts import VerifiedStrategyArtifacts


class PaperPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperEntryCandidate:
    """Authority selected by an already-validated ExpertSessionManifestV1."""

    request_id: str
    canonical_match_id: str
    contract_side: ContractSide
    provider_revision: int
    requested_monotonic_ns: int
    signal_reset: bool
    gate: EntryGateInput
    session_snapshot_binding: EntrySnapshotBinding | None = None
    strategy_authority: VerifiedStrategyArtifacts | None = None
    entry_ask_levels: tuple[PriceLevel, ...] = ()
    fee_series_ticker: str = ""
    fill_wall_ns: int = 0

    def __post_init__(self) -> None:
        if type(self.request_id) is not str or not self.request_id:
            raise PaperPolicyError("request_id")
        if type(self.canonical_match_id) is not str or not self.canonical_match_id:
            raise PaperPolicyError("canonical_match_id")
        if type(self.contract_side) is not ContractSide:
            raise PaperPolicyError("contract_side")
        if type(self.provider_revision) is not int or self.provider_revision < 0:
            raise PaperPolicyError("provider_revision")
        if (
            type(self.requested_monotonic_ns) is not int
            or self.requested_monotonic_ns < 0
        ):
            raise PaperPolicyError("requested_monotonic_ns")
        if type(self.signal_reset) is not bool:
            raise PaperPolicyError("signal_reset")
        if type(self.gate) is not EntryGateInput:
            raise PaperPolicyError("gate")
        EntryGateInput.__post_init__(self.gate)
        if self.session_snapshot_binding is not None and type(
            self.session_snapshot_binding
        ) is not EntrySnapshotBinding:
            raise PaperPolicyError("session_snapshot_binding")
        if self.session_snapshot_binding is not None:
            EntrySnapshotBinding.__post_init__(
                self.session_snapshot_binding
            )
        if self.strategy_authority is not None and type(
            self.strategy_authority
        ) is not VerifiedStrategyArtifacts:
            raise PaperPolicyError("strategy_authority")
        if self.strategy_authority is not None:
            self.strategy_authority.validate()
        if type(self.entry_ask_levels) is not tuple:
            raise PaperPolicyError("entry_ask_levels")
        if any(type(level) is not PriceLevel for level in self.entry_ask_levels):
            raise PaperPolicyError("entry_ask_levels")
        for level in self.entry_ask_levels:
            PriceLevel.__post_init__(level)
        if type(self.fee_series_ticker) is not str:
            raise PaperPolicyError("fee_series_ticker")
        if not self.fee_series_ticker:
            raise PaperPolicyError("fee_series_ticker")
        if type(self.fill_wall_ns) is not int or self.fill_wall_ns < 0:
            raise PaperPolicyError("fill_wall_ns")


@dataclass(frozen=True, slots=True)
class PaperEntryOutcome:
    entry: EntryDecision
    risk_result: None = None

    def __post_init__(self) -> None:
        if type(self.entry) is not EntryDecision:
            raise PaperPolicyError("entry")
        EntryDecision.__post_init__(self.entry)
        if self.entry.action is not EntryAction.ABSTAIN:
            raise PaperPolicyError("paper_execution_disabled")
        if self.risk_result is not None:
            raise PaperPolicyError("risk_result")

    @property
    def authorized(self) -> bool:
        return False

    @property
    def action(self) -> EntryAction:
        return EntryAction.ABSTAIN


def _paper_abstention(reason: EntryReason) -> EntryDecision:
    return EntryDecision(
        action=EntryAction.ABSTAIN,
        reason=reason,
        quantity=Decimal("0"),
        all_in_debit=Decimal("0"),
    )


def evaluate_and_reserve(
    state: RiskState,
    candidate: PaperEntryCandidate,
    policy: FrozenRiskPolicy,
) -> tuple[RiskState, PaperEntryOutcome]:
    if type(state) is not RiskState:
        raise PaperPolicyError("state")
    if type(candidate) is not PaperEntryCandidate:
        raise PaperPolicyError("candidate")
    PaperEntryCandidate.__post_init__(candidate)
    if type(policy) is not FrozenRiskPolicy:
        raise PaperPolicyError("policy")

    if candidate.contract_side is not candidate.gate.dip.current.contract_side:
        entry = _paper_abstention(EntryReason.CONTRACT_SIDE_MISMATCH)
        return state, PaperEntryOutcome(entry=entry, risk_result=None)

    session_binding = candidate.session_snapshot_binding
    gate_binding = candidate.gate.snapshot_binding
    if session_binding is None or gate_binding is None:
        entry = _paper_abstention(EntryReason.SNAPSHOT_BINDING_MISSING)
        return state, PaperEntryOutcome(entry=entry, risk_result=None)
    if (
        session_binding != gate_binding
        or candidate.canonical_match_id != session_binding.canonical_match_id
        or candidate.contract_side is not session_binding.contract_side
        or candidate.provider_revision != session_binding.provider_revision
        or candidate.requested_monotonic_ns
        != session_binding.decision_monotonic_ns
        or candidate.fee_series_ticker
        != session_binding.fee_series_ticker
        or candidate.fill_wall_ns != session_binding.decision_wall_ns
        or price_levels_sha256(candidate.entry_ask_levels)
        != session_binding.entry_ask_levels_sha256
    ):
        entry = _paper_abstention(EntryReason.SNAPSHOT_BINDING_MISMATCH)
        return state, PaperEntryOutcome(entry=entry, risk_result=None)

    authority = candidate.strategy_authority
    if authority is None:
        entry = _paper_abstention(EntryReason.STRATEGY_AUTHORITY_MISSING)
        return state, PaperEntryOutcome(entry=entry, risk_result=None)
    forecast = candidate.gate.forecast
    if forecast is None:
        entry = _paper_abstention(EntryReason.FORECAST_MISSING)
        return state, PaperEntryOutcome(entry=entry, risk_result=None)
    if (
        gate_binding.session_manifest_sha256
        != authority.session_manifest_sha256
        or gate_binding.outcome_artifact_id
        != authority.outcome_pin.artifact_id
        or gate_binding.outcome_artifact_sha256
        != authority.outcome_pin.artifact_sha256
        or gate_binding.markout_artifact_id
        != authority.markout_pin.artifact_id
        or gate_binding.markout_artifact_sha256
        != authority.markout_pin.artifact_sha256
        or gate_binding.fee_schedule_artifact_id
        != authority.fee_schedule_pin.artifact_id
        or gate_binding.fee_schedule_sha256
        != authority.fee_schedule_pin.artifact_sha256
        or forecast.artifact_version != authority.markout_pin.artifact_id
        or forecast.artifact_sha256 != authority.markout_pin.artifact_sha256
    ):
        entry = _paper_abstention(
            EntryReason.STRATEGY_AUTHORITY_MISMATCH
        )
        return state, PaperEntryOutcome(entry=entry, risk_result=None)
    if not authority.models_are_causal_at(
        gate_binding.decision_wall_ns
    ):
        entry = _paper_abstention(EntryReason.MODEL_ARTIFACT_NOT_CAUSAL)
        return state, PaperEntryOutcome(entry=entry, risk_result=None)

    def authorized_fee(price: Decimal, quantity: Decimal) -> Decimal:
        return fee_for_fill(
            authority.fee_schedule,
            series_ticker=candidate.fee_series_ticker,
            price=price,
            quantity=quantity,
            role=LiquidityRole.TAKER,
            side=FillSide.BUY,
            fill_wall_ns=candidate.fill_wall_ns,
        )

    try:
        authoritative_capacity = size_ioc_entry(
            candidate.entry_ask_levels,
            requested_quantity=candidate.gate.capacity.requested_quantity,
            fee=authorized_fee,
        )
    except (TypeError, ValueError):
        entry = _paper_abstention(
            EntryReason.FEE_SCHEDULE_CAPACITY_MISMATCH
        )
        return state, PaperEntryOutcome(entry=entry, risk_result=None)
    if authoritative_capacity != candidate.gate.capacity:
        entry = _paper_abstention(
            EntryReason.FEE_SCHEDULE_CAPACITY_MISMATCH
        )
        return state, PaperEntryOutcome(entry=entry, risk_result=None)

    gate = candidate.gate
    entry = evaluate_entry(
        EntryGateInput(
            set_number=gate.set_number,
            score_trusted=gate.score_trusted,
            book_trusted=gate.book_trusted,
            first_set_review=gate.first_set_review,
            current_ask=gate.current_ask,
            conservative_fair_value=gate.conservative_fair_value,
            fair_value=gate.fair_value,
            dip=gate.dip,
            capacity=authoritative_capacity,
            forecast=gate.forecast,
            snapshot_binding=gate.snapshot_binding,
        )
    )
    if entry.action is EntryAction.ABSTAIN:
        return state, PaperEntryOutcome(entry=entry, risk_result=None)

    # Artifact metadata and self-attested snapshots are sufficient for
    # deterministic research diagnostics, but not for executable authority.
    # Promotion requires a fitted evaluator and a synchronizer-issued trusted
    # opportunity capability. Until then, no paper reservation may be created.
    entry = _paper_abstention(EntryReason.PAPER_MODEL_NOT_PROMOTED)
    return state, PaperEntryOutcome(
        entry=entry,
        risk_result=None,
    )
