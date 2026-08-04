from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from .contracts import (
    ContractSide,
    DecisionAction,
    DecisionReason,
    ExpertContractError,
    _boolean,
    _exact,
    _exact_self,
    _integer,
    _optional_exact,
    _probability,
    _quantity,
    _safe_id,
    _sha256,
    _ticker,
    expert_contract_sha256,
)
from .engine import ClipBundle, ClipObservation
from .prematch_model import PrematchPrior
from .scalp_policy import ScalpPosition


def _optional_probability(value: object, name: str) -> None:
    if value is not None:
        _probability(value, name)


def _optional_sha256(value: object, name: str) -> None:
    if value is not None:
        _sha256(value, name)


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise ExpertContractError(name)
    return value


@dataclass(frozen=True, slots=True)
class ClipJournalRecordV1:
    """Durable paper-clip observation bound to a trusted opportunity."""

    schema_version: int
    session_id: str
    record_sequence: int
    opportunity_id: str
    canonical_match_id: str
    ticker: str
    decision_sequence: int
    fair_value_sha256: str
    action: DecisionAction
    reason: DecisionReason
    contract_side: ContractSide | None
    player_side_name: str | None
    quantity: Decimal
    limit_price: Decimal | None
    expected_entry_price: Decimal | None
    expected_exit_price: Decimal | None
    edge_vs_ask: Decimal
    projected_net_pnl: Decimal
    lower_projected_net_pnl: Decimal
    open_position_present: bool
    open_position_sha256: str | None
    prior_sha256: str
    fee_schedule_sha256: str
    clip_artifact_sha256: str
    calibration_artifact_sha256: str | None
    observation_sha256: str
    record_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ClipJournalRecordV1)
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise ExpertContractError("schema_version")
        _safe_id(self.session_id, "session_id")
        _integer(self.record_sequence, "record_sequence", positive=True)
        _sha256(self.opportunity_id, "opportunity_id")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _integer(self.decision_sequence, "decision_sequence", positive=True)
        _sha256(self.fair_value_sha256, "fair_value_sha256")
        _exact(self.action, DecisionAction, "action")
        _exact(self.reason, DecisionReason, "reason")
        _optional_exact(self.contract_side, ContractSide, "contract_side")
        if self.player_side_name is not None:
            _safe_id(self.player_side_name, "player_side_name")
        _quantity(self.quantity, "quantity")
        _optional_probability(self.limit_price, "limit_price")
        _optional_probability(
            self.expected_entry_price,
            "expected_entry_price",
        )
        _optional_probability(
            self.expected_exit_price,
            "expected_exit_price",
        )
        _decimal(self.edge_vs_ask, "edge_vs_ask")
        _decimal(self.projected_net_pnl, "projected_net_pnl")
        _decimal(self.lower_projected_net_pnl, "lower_projected_net_pnl")
        _boolean(self.open_position_present, "open_position_present")
        _optional_sha256(
            self.open_position_sha256,
            "open_position_sha256",
        )
        _sha256(self.prior_sha256, "prior_sha256")
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        _sha256(self.clip_artifact_sha256, "clip_artifact_sha256")
        _optional_sha256(
            self.calibration_artifact_sha256,
            "calibration_artifact_sha256",
        )
        _sha256(self.observation_sha256, "observation_sha256")
        _sha256(self.record_sha256, "record_sha256")
        if self.open_position_present != (
            self.open_position_sha256 is not None
        ):
            raise ExpertContractError("open_position")
        if self.action is DecisionAction.ABSTAIN:
            if self.contract_side is not None or self.limit_price is not None:
                raise ExpertContractError("abstain")
        expected = clip_journal_record_sha256(self)
        if self.record_sha256 != expected:
            raise ExpertContractError("record_sha256")


def clip_journal_record_identity_payload(
    record: ClipJournalRecordV1,
) -> dict[str, object]:
    _exact_self(record, ClipJournalRecordV1)
    return {
        "schema": "clip_journal_record_v1",
        "schema_version": record.schema_version,
        "session_id": record.session_id,
        "record_sequence": record.record_sequence,
        "opportunity_id": record.opportunity_id,
        "canonical_match_id": record.canonical_match_id,
        "ticker": record.ticker,
        "decision_sequence": record.decision_sequence,
        "fair_value_sha256": record.fair_value_sha256,
        "action": record.action.value,
        "reason": record.reason.value,
        "contract_side": (
            None
            if record.contract_side is None
            else record.contract_side.value
        ),
        "player_side_name": record.player_side_name,
        "quantity": record.quantity,
        "limit_price": record.limit_price,
        "expected_entry_price": record.expected_entry_price,
        "expected_exit_price": record.expected_exit_price,
        "edge_vs_ask": record.edge_vs_ask,
        "projected_net_pnl": record.projected_net_pnl,
        "lower_projected_net_pnl": record.lower_projected_net_pnl,
        "open_position_present": record.open_position_present,
        "open_position_sha256": record.open_position_sha256,
        "prior_sha256": record.prior_sha256,
        "fee_schedule_sha256": record.fee_schedule_sha256,
        "clip_artifact_sha256": record.clip_artifact_sha256,
        "calibration_artifact_sha256": record.calibration_artifact_sha256,
        "observation_sha256": record.observation_sha256,
    }


def clip_journal_record_sha256(record: ClipJournalRecordV1) -> str:
    return expert_contract_sha256(
        clip_journal_record_identity_payload(record)
    )


def observation_projection_sha256(observation: ClipObservation) -> str:
    _exact(observation, ClipObservation, "observation")
    entry = observation.entry
    exit_decision = observation.exit
    return expert_contract_sha256(
        {
            "schema": "clip_observation_projection_v1",
            "opportunity_id": observation.opportunity_id,
            "canonical_match_id": observation.canonical_match_id,
            "ticker": observation.ticker,
            "decision_sequence": observation.decision_sequence,
            "fair_value_sha256": observation.fair_value_sha256,
            "action": observation.action.value,
            "reason": observation.reason.value,
            "entry_decision_input_sha256": (
                None if entry is None else entry.decision_input_sha256
            ),
            "exit_decision_input_sha256": (
                None
                if exit_decision is None
                else exit_decision.decision_input_sha256
            ),
            "position_present": observation.position is not None,
            "position_sha256": (
                None
                if observation.position is None
                else position_sha256(observation.position)
            ),
        }
    )


def position_sha256(position: ScalpPosition) -> str:
    _exact(position, ScalpPosition, "position")
    return expert_contract_sha256(
        {
            "schema": "scalp_position_projection_v1",
            "ticker": position.ticker,
            "contract_side": position.contract_side.value,
            "player_side": position.player_side.value,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "entry_wall_ns": position.entry_wall_ns,
            "entry_fair_probability": position.entry_fair_probability,
            "entry_lower_probability": position.entry_lower_probability,
            "fair_value_sha256": position.fair_value_sha256,
            "fee_schedule_sha256": position.fee_schedule_sha256,
            "policy_artifact_sha256": position.policy_artifact_sha256,
        }
    )


def clip_record_from_observation(
    observation: ClipObservation,
    *,
    session_id: str,
    record_sequence: int,
    prior: PrematchPrior,
    bundle: ClipBundle,
    calibration_artifact_sha256: str | None = None,
) -> ClipJournalRecordV1:
    _exact(observation, ClipObservation, "observation")
    _exact(prior, PrematchPrior, "prior")
    _exact(bundle, ClipBundle, "bundle")
    entry = observation.entry
    exit_decision = observation.exit
    if entry is not None:
        contract_side = entry.estimate.contract_side
        player_side_name = (
            None
            if entry.estimate.player_side is None
            else entry.estimate.player_side.value
        )
        quantity = entry.estimate.quantity
        limit_price = entry.estimate.limit_price
        expected_entry = entry.estimate.expected_entry_price
        expected_exit = entry.estimate.expected_exit_price
        edge = entry.estimate.edge_vs_ask
        projected = entry.estimate.projected_net_pnl
        lower_projected = entry.estimate.lower_projected_net_pnl
    else:
        assert exit_decision is not None
        contract_side = None
        player_side_name = None
        quantity = exit_decision.executable_quantity
        limit_price = exit_decision.limit_price
        expected_entry = None
        expected_exit = exit_decision.expected_exit_price
        edge = Decimal("0")
        projected = exit_decision.projected_net_pnl
        lower_projected = exit_decision.projected_net_pnl

    prior_digest = expert_contract_sha256(
        {
            "schema": "prematch_prior_digest_v1",
            "player_home_id": prior.player_home_id,
            "player_away_id": prior.player_away_id,
            "scheduled_start_wall_ns": prior.scheduled_start_wall_ns,
            "model_sha256": prior.model_sha256,
            "prematch_artifact_sha256": prior.prematch_artifact_sha256,
            "feature_vector_sha256": prior.feature_vector_sha256,
        }
    )
    observation_digest = observation_projection_sha256(observation)
    open_digest = (
        None
        if observation.position is None
        else position_sha256(observation.position)
    )
    identity = {
        "schema": "clip_journal_record_v1",
        "schema_version": 1,
        "session_id": session_id,
        "record_sequence": record_sequence,
        "opportunity_id": observation.opportunity_id,
        "canonical_match_id": observation.canonical_match_id,
        "ticker": observation.ticker,
        "decision_sequence": observation.decision_sequence,
        "fair_value_sha256": observation.fair_value_sha256,
        "action": observation.action.value,
        "reason": observation.reason.value,
        "contract_side": (
            None if contract_side is None else contract_side.value
        ),
        "player_side_name": player_side_name,
        "quantity": quantity,
        "limit_price": limit_price,
        "expected_entry_price": expected_entry,
        "expected_exit_price": expected_exit,
        "edge_vs_ask": edge,
        "projected_net_pnl": projected,
        "lower_projected_net_pnl": lower_projected,
        "open_position_present": observation.position is not None,
        "open_position_sha256": open_digest,
        "prior_sha256": prior_digest,
        "fee_schedule_sha256": bundle.fee_schedule.schedule_sha256,
        "clip_artifact_sha256": bundle.clip_artifact.policy_artifact_sha256,
        "calibration_artifact_sha256": calibration_artifact_sha256,
        "observation_sha256": observation_digest,
    }
    digest = expert_contract_sha256(identity)
    return ClipJournalRecordV1(
        schema_version=1,
        session_id=session_id,
        record_sequence=record_sequence,
        opportunity_id=observation.opportunity_id,
        canonical_match_id=observation.canonical_match_id,
        ticker=observation.ticker,
        decision_sequence=observation.decision_sequence,
        fair_value_sha256=observation.fair_value_sha256,
        action=observation.action,
        reason=observation.reason,
        contract_side=contract_side,
        player_side_name=player_side_name,
        quantity=quantity,
        limit_price=limit_price,
        expected_entry_price=expected_entry,
        expected_exit_price=expected_exit,
        edge_vs_ask=edge,
        projected_net_pnl=projected,
        lower_projected_net_pnl=lower_projected,
        open_position_present=observation.position is not None,
        open_position_sha256=open_digest,
        prior_sha256=prior_digest,
        fee_schedule_sha256=bundle.fee_schedule.schedule_sha256,
        clip_artifact_sha256=bundle.clip_artifact.policy_artifact_sha256,
        calibration_artifact_sha256=calibration_artifact_sha256,
        observation_sha256=observation_digest,
        record_sha256=digest,
    )


def encode_clip_journal_records(
    records: tuple[ClipJournalRecordV1, ...],
) -> bytes:
    if type(records) is not tuple:
        raise TypeError("records")
    payload = []
    expected_sequence = 1
    previous_session: str | None = None
    for record in records:
        if type(record) is not ClipJournalRecordV1:
            raise TypeError("records")
        ClipJournalRecordV1.__post_init__(record)
        if record.record_sequence != expected_sequence:
            raise ExpertContractError("record_sequence")
        if (
            previous_session is not None
            and record.session_id != previous_session
        ):
            raise ExpertContractError("session_id")
        previous_session = record.session_id
        expected_sequence += 1
        item = clip_journal_record_identity_payload(record)
        item["record_sha256"] = record.record_sha256
        payload.append(item)
    digest = expert_contract_sha256(
        {"schema": "clip_journal_bundle_v1", "records": tuple(payload)}
    )
    return (digest + "\n").encode("ascii")


def verify_clip_record_matches_observation(
    record: ClipJournalRecordV1,
    observation: ClipObservation,
    *,
    prior: PrematchPrior,
    bundle: ClipBundle,
    calibration_artifact_sha256: str | None = None,
) -> None:
    expected = clip_record_from_observation(
        observation,
        session_id=record.session_id,
        record_sequence=record.record_sequence,
        prior=prior,
        bundle=bundle,
        calibration_artifact_sha256=calibration_artifact_sha256,
    )
    if (
        record.opportunity_id != expected.opportunity_id
        or record.action is not expected.action
        or record.reason is not expected.reason
        or record.observation_sha256 != expected.observation_sha256
        or record.record_sha256 != expected.record_sha256
        or record.fair_value_sha256 != expected.fair_value_sha256
        or record.open_position_sha256 != expected.open_position_sha256
    ):
        raise ExpertContractError("clip_journal_mismatch")


__all__: Final[tuple[str, ...]] = (
    "ClipJournalRecordV1",
    "clip_journal_record_sha256",
    "clip_record_from_observation",
    "encode_clip_journal_records",
    "observation_projection_sha256",
    "position_sha256",
    "verify_clip_record_matches_observation",
)
