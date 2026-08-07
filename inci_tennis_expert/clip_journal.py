from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
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


_CLIP_DOCUMENT_SCHEMA: Final[str] = "clip_journal_document_v1"
_DEFAULT_TARGET_NET_PNL_USD: Final[Decimal] = Decimal("3.00")


def _wire_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if type(value) is not Decimal or not value.is_finite():
        raise ExpertContractError("decimal")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if value.is_zero():
        rendered = "0"
    return rendered


def _parse_wire_decimal(value: object, name: str) -> Decimal:
    if type(value) is not str or not value:
        raise ExpertContractError(name)
    try:
        number = Decimal(value)
    except Exception as exc:
        raise ExpertContractError(name) from exc
    if not number.is_finite():
        raise ExpertContractError(name)
    if _wire_decimal(number) != value:
        raise ExpertContractError(name)
    return number


def _parse_optional_wire_decimal(
    value: object,
    name: str,
) -> Decimal | None:
    if value is None:
        return None
    return _parse_wire_decimal(value, name)


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


def _validated_record_payload(
    records: tuple[ClipJournalRecordV1, ...],
) -> list[dict[str, object]]:
    if type(records) is not tuple:
        raise TypeError("records")
    payload: list[dict[str, object]] = []
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
    return payload


def encode_clip_journal_records(
    records: tuple[ClipJournalRecordV1, ...],
) -> bytes:
    payload = _validated_record_payload(records)
    digest = expert_contract_sha256(
        {"schema": "clip_journal_bundle_v1", "records": tuple(payload)}
    )
    return (digest + "\n").encode("ascii")


def clip_journal_record_wire_payload(
    record: ClipJournalRecordV1,
) -> dict[str, object]:
    _exact_self(record, ClipJournalRecordV1)
    return {
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
        "quantity": _wire_decimal(record.quantity),
        "limit_price": _wire_decimal(record.limit_price),
        "expected_entry_price": _wire_decimal(record.expected_entry_price),
        "expected_exit_price": _wire_decimal(record.expected_exit_price),
        "edge_vs_ask": _wire_decimal(record.edge_vs_ask),
        "projected_net_pnl": _wire_decimal(record.projected_net_pnl),
        "lower_projected_net_pnl": _wire_decimal(
            record.lower_projected_net_pnl
        ),
        "open_position_present": record.open_position_present,
        "open_position_sha256": record.open_position_sha256,
        "prior_sha256": record.prior_sha256,
        "fee_schedule_sha256": record.fee_schedule_sha256,
        "clip_artifact_sha256": record.clip_artifact_sha256,
        "calibration_artifact_sha256": record.calibration_artifact_sha256,
        "observation_sha256": record.observation_sha256,
        "record_sha256": record.record_sha256,
    }


def clip_journal_record_from_wire(
    payload: dict[str, object],
) -> ClipJournalRecordV1:
    if type(payload) is not dict:
        raise TypeError("payload")
    try:
        action = DecisionAction(payload["action"])
        reason = DecisionReason(payload["reason"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExpertContractError("wire_enum") from exc
    contract_side_raw = payload.get("contract_side")
    if contract_side_raw is None:
        contract_side = None
    else:
        try:
            contract_side = ContractSide(contract_side_raw)
        except (TypeError, ValueError) as exc:
            raise ExpertContractError("contract_side") from exc
    try:
        return ClipJournalRecordV1(
            schema_version=payload["schema_version"],  # type: ignore[arg-type]
            session_id=payload["session_id"],  # type: ignore[arg-type]
            record_sequence=payload["record_sequence"],  # type: ignore[arg-type]
            opportunity_id=payload["opportunity_id"],  # type: ignore[arg-type]
            canonical_match_id=payload["canonical_match_id"],  # type: ignore[arg-type]
            ticker=payload["ticker"],  # type: ignore[arg-type]
            decision_sequence=payload["decision_sequence"],  # type: ignore[arg-type]
            fair_value_sha256=payload["fair_value_sha256"],  # type: ignore[arg-type]
            action=action,
            reason=reason,
            contract_side=contract_side,
            player_side_name=payload.get("player_side_name"),  # type: ignore[arg-type]
            quantity=_parse_wire_decimal(payload["quantity"], "quantity"),
            limit_price=_parse_optional_wire_decimal(
                payload.get("limit_price"),
                "limit_price",
            ),
            expected_entry_price=_parse_optional_wire_decimal(
                payload.get("expected_entry_price"),
                "expected_entry_price",
            ),
            expected_exit_price=_parse_optional_wire_decimal(
                payload.get("expected_exit_price"),
                "expected_exit_price",
            ),
            edge_vs_ask=_parse_wire_decimal(
                payload["edge_vs_ask"],
                "edge_vs_ask",
            ),
            projected_net_pnl=_parse_wire_decimal(
                payload["projected_net_pnl"],
                "projected_net_pnl",
            ),
            lower_projected_net_pnl=_parse_wire_decimal(
                payload["lower_projected_net_pnl"],
                "lower_projected_net_pnl",
            ),
            open_position_present=payload["open_position_present"],  # type: ignore[arg-type]
            open_position_sha256=payload.get("open_position_sha256"),  # type: ignore[arg-type]
            prior_sha256=payload["prior_sha256"],  # type: ignore[arg-type]
            fee_schedule_sha256=payload["fee_schedule_sha256"],  # type: ignore[arg-type]
            clip_artifact_sha256=payload["clip_artifact_sha256"],  # type: ignore[arg-type]
            calibration_artifact_sha256=payload.get(
                "calibration_artifact_sha256"
            ),  # type: ignore[arg-type]
            observation_sha256=payload["observation_sha256"],  # type: ignore[arg-type]
            record_sha256=payload["record_sha256"],  # type: ignore[arg-type]
        )
    except KeyError as exc:
        raise ExpertContractError("wire_field") from exc


def serialize_clip_journal_document(
    records: tuple[ClipJournalRecordV1, ...],
) -> bytes:
    integrity = encode_clip_journal_records(records).strip().decode("ascii")
    document = {
        "schema": _CLIP_DOCUMENT_SCHEMA,
        "integrity_sha256": integrity,
        "records": [
            clip_journal_record_wire_payload(record) for record in records
        ],
    }
    return (
        json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def deserialize_clip_journal_document(
    document: bytes,
) -> tuple[ClipJournalRecordV1, ...]:
    if type(document) is not bytes:
        raise TypeError("document")
    try:
        text = document.decode("ascii")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExpertContractError("clip_journal_document") from exc
    if type(payload) is not dict:
        raise ExpertContractError("clip_journal_document")
    if payload.get("schema") != _CLIP_DOCUMENT_SCHEMA:
        raise ExpertContractError("schema")
    integrity = payload.get("integrity_sha256")
    rows = payload.get("records")
    if type(integrity) is not str or len(integrity) != 64:
        raise ExpertContractError("integrity_sha256")
    if type(rows) is not list:
        raise ExpertContractError("records")
    records: list[ClipJournalRecordV1] = []
    for row in rows:
        if type(row) is not dict:
            raise ExpertContractError("records")
        records.append(clip_journal_record_from_wire(row))
    materialized = tuple(records)
    expected = encode_clip_journal_records(materialized).strip().decode("ascii")
    if integrity != expected:
        raise ExpertContractError("integrity_sha256")
    return materialized


@dataclass(frozen=True, slots=True)
class ClipSessionScorecard:
    """Paper-only rollup of companion clip journal decisions."""

    session_id: str
    record_count: int
    paper_buy_count: int
    paper_sell_count: int
    abstain_count: int
    open_at_end: bool
    projected_entry_net_pnl: Decimal
    projected_exit_net_pnl: Decimal
    target_net_pnl_usd: Decimal
    entry_target_clear_count: int
    exit_target_clear_count: int
    scorecard_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ClipSessionScorecard)
        _safe_id(self.session_id, "session_id")
        _integer(self.record_count, "record_count")
        _integer(self.paper_buy_count, "paper_buy_count")
        _integer(self.paper_sell_count, "paper_sell_count")
        _integer(self.abstain_count, "abstain_count")
        _boolean(self.open_at_end, "open_at_end")
        if type(self.projected_entry_net_pnl) is not Decimal:
            raise TypeError("projected_entry_net_pnl")
        if type(self.projected_exit_net_pnl) is not Decimal:
            raise TypeError("projected_exit_net_pnl")
        if type(self.target_net_pnl_usd) is not Decimal:
            raise TypeError("target_net_pnl_usd")
        if (
            not self.projected_entry_net_pnl.is_finite()
            or not self.projected_exit_net_pnl.is_finite()
            or not self.target_net_pnl_usd.is_finite()
            or self.target_net_pnl_usd < Decimal("0")
        ):
            raise ExpertContractError("scorecard_decimal")
        _integer(self.entry_target_clear_count, "entry_target_clear_count")
        _integer(self.exit_target_clear_count, "exit_target_clear_count")
        _sha256(self.scorecard_sha256, "scorecard_sha256")
        if (
            self.paper_buy_count
            + self.paper_sell_count
            + self.abstain_count
            != self.record_count
        ):
            raise ExpertContractError("action_counts")
        expected = clip_session_scorecard_sha256(self)
        if self.scorecard_sha256 != expected:
            raise ExpertContractError("scorecard_sha256")


def clip_session_scorecard_identity_payload(
    scorecard: ClipSessionScorecard,
) -> dict[str, object]:
    _exact_self(scorecard, ClipSessionScorecard)
    return {
        "schema": "clip_session_scorecard_v1",
        "session_id": scorecard.session_id,
        "record_count": scorecard.record_count,
        "paper_buy_count": scorecard.paper_buy_count,
        "paper_sell_count": scorecard.paper_sell_count,
        "abstain_count": scorecard.abstain_count,
        "open_at_end": scorecard.open_at_end,
        "projected_entry_net_pnl": scorecard.projected_entry_net_pnl,
        "projected_exit_net_pnl": scorecard.projected_exit_net_pnl,
        "target_net_pnl_usd": scorecard.target_net_pnl_usd,
        "entry_target_clear_count": scorecard.entry_target_clear_count,
        "exit_target_clear_count": scorecard.exit_target_clear_count,
    }


def clip_session_scorecard_sha256(scorecard: ClipSessionScorecard) -> str:
    return expert_contract_sha256(
        clip_session_scorecard_identity_payload(scorecard)
    )


def scorecard_from_clip_records(
    records: tuple[ClipJournalRecordV1, ...],
    *,
    session_id: str | None = None,
    target_net_pnl_usd: Decimal = _DEFAULT_TARGET_NET_PNL_USD,
) -> ClipSessionScorecard:
    _validated_record_payload(records)
    if type(target_net_pnl_usd) is not Decimal:
        raise TypeError("target_net_pnl_usd")
    if (
        not target_net_pnl_usd.is_finite()
        or target_net_pnl_usd < Decimal("0")
    ):
        raise ExpertContractError("target_net_pnl_usd")
    if not records:
        if session_id is None:
            raise ExpertContractError("session_id")
        _safe_id(session_id, "session_id")
        resolved_session_id = session_id
        open_at_end = False
    else:
        resolved_session_id = records[0].session_id
        if session_id is not None and session_id != resolved_session_id:
            raise ExpertContractError("session_id")
        open_at_end = records[-1].open_position_present

    if not records:
        identity = {
            "schema": "clip_session_scorecard_v1",
            "session_id": resolved_session_id,
            "record_count": 0,
            "paper_buy_count": 0,
            "paper_sell_count": 0,
            "abstain_count": 0,
            "open_at_end": False,
            "projected_entry_net_pnl": Decimal("0"),
            "projected_exit_net_pnl": Decimal("0"),
            "target_net_pnl_usd": target_net_pnl_usd,
            "entry_target_clear_count": 0,
            "exit_target_clear_count": 0,
        }
        digest = expert_contract_sha256(identity)
        return ClipSessionScorecard(
            session_id=resolved_session_id,
            record_count=0,
            paper_buy_count=0,
            paper_sell_count=0,
            abstain_count=0,
            open_at_end=False,
            projected_entry_net_pnl=Decimal("0"),
            projected_exit_net_pnl=Decimal("0"),
            target_net_pnl_usd=target_net_pnl_usd,
            entry_target_clear_count=0,
            exit_target_clear_count=0,
            scorecard_sha256=digest,
        )
    buys = 0
    sells = 0
    abstains = 0
    entry_pnl = Decimal("0")
    exit_pnl = Decimal("0")
    entry_clear = 0
    exit_clear = 0
    for record in records:
        if record.action is DecisionAction.PAPER_BUY:
            buys += 1
            entry_pnl += record.lower_projected_net_pnl
            if record.lower_projected_net_pnl >= target_net_pnl_usd:
                entry_clear += 1
        elif record.action is DecisionAction.PAPER_SELL:
            sells += 1
            exit_pnl += record.projected_net_pnl
            if record.projected_net_pnl >= target_net_pnl_usd:
                exit_clear += 1
        elif record.action is DecisionAction.ABSTAIN:
            abstains += 1
        else:
            raise ExpertContractError("action")
    identity = {
        "schema": "clip_session_scorecard_v1",
        "session_id": resolved_session_id,
        "record_count": len(records),
        "paper_buy_count": buys,
        "paper_sell_count": sells,
        "abstain_count": abstains,
        "open_at_end": open_at_end,
        "projected_entry_net_pnl": entry_pnl,
        "projected_exit_net_pnl": exit_pnl,
        "target_net_pnl_usd": target_net_pnl_usd,
        "entry_target_clear_count": entry_clear,
        "exit_target_clear_count": exit_clear,
    }
    digest = expert_contract_sha256(identity)
    return ClipSessionScorecard(
        session_id=resolved_session_id,
        record_count=len(records),
        paper_buy_count=buys,
        paper_sell_count=sells,
        abstain_count=abstains,
        open_at_end=open_at_end,
        projected_entry_net_pnl=entry_pnl,
        projected_exit_net_pnl=exit_pnl,
        target_net_pnl_usd=target_net_pnl_usd,
        entry_target_clear_count=entry_clear,
        exit_target_clear_count=exit_clear,
        scorecard_sha256=digest,
    )


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
    "ClipSessionScorecard",
    "clip_journal_record_from_wire",
    "clip_journal_record_sha256",
    "clip_journal_record_wire_payload",
    "clip_record_from_observation",
    "clip_session_scorecard_sha256",
    "deserialize_clip_journal_document",
    "encode_clip_journal_records",
    "observation_projection_sha256",
    "position_sha256",
    "scorecard_from_clip_records",
    "serialize_clip_journal_document",
    "verify_clip_record_matches_observation",
)
