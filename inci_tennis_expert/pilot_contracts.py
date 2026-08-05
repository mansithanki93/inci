"""Immutable, deterministic contracts for the research-only tennis pilot."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from re import ASCII as RE_ASCII
from re import compile as pattern_compile
from typing import Final

from inci_tennis_expert.contracts import (
    ContractSide,
    MatchFormat,
    PlayerSide,
    TennisState,
)
from inci_tennis_expert.tennis_score import apply_point
from inci_tennis_expert.contracts import ProviderPoint


__all__ = (
    "PilotContractError",
    "PilotSupportReason",
    "PilotAction",
    "PilotImmediateAction",
    "PilotRoute",
    "PilotPriceLevel",
    "PilotPointEvent",
    "ServeStrengthArtifact",
    "PilotOutcomeEstimate",
    "DynamicBeliefSnapshot",
    "PilotBookSnapshot",
    "PilotExecutionScenario",
    "PilotDecisionFrame",
    "PilotFrameAbstention",
    "PilotFrameProjection",
    "PilotPolicyEstimate",
    "PilotImmediateBaselineEstimate",
    "PilotComparisonRow",
    "pilot_contract_sha256",
    "compute_training_match_ids_sha256",
    "compute_serve_strength_artifact_sha256",
    "compute_execution_scenario_sha256",
    "compute_pilot_book_snapshot_sha256",
    "make_pilot_policy_estimate",
    "make_pilot_immediate_baseline_estimate",
)


_ID = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", RE_ASCII)
_TICKER = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z", RE_ASCII)
_SHA256 = pattern_compile(r"[0-9a-f]{64}\Z", RE_ASCII)
_SERVE_ARTIFACT_DOMAIN: Final[bytes] = b"inci-tennis-pilot-serve-artifact-v1\0"
_EXECUTION_SCENARIO_DOMAIN: Final[bytes] = b"inci-tennis-pilot-execution-scenario-v1\0"
_BOOK_DOMAIN: Final[bytes] = b"inci-tennis-pilot-book-v1\0"
_TRAINING_IDS_DOMAIN: Final[bytes] = b"inci-tennis-pilot-training-match-ids-v1\0"
_HOLDING_HORIZON_NS: Final[int] = 300_000_000_000
_MAX_SIGNED_64: Final[int] = 9_223_372_036_854_775_807


class PilotContractError(ValueError):
    """Fixed-code contract rejection for pilot values."""


class PilotSupportReason(str, Enum):
    DUPLICATE_POINT = "duplicate_point"
    INVALID_POINT_TRANSITION = "invalid_point_transition"
    SCORE_CORRECTED = "score_corrected"
    CONSENSUS_EPOCH_CHANGED = "consensus_epoch_changed"
    BOOK_UNTRUSTED = "book_untrusted"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    UNSUPPORTED_NO_MARKOUT_KERNEL = "unsupported_no_markout_kernel"


class PilotAction(str, Enum):
    ABSTAIN = "abstain"
    WAIT = "wait"
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"


class PilotImmediateAction(str, Enum):
    ABSTAIN = "abstain"
    BUY_NOW = "buy_now"
    HOLD = "hold"
    SELL = "sell"


def _fail(code: str) -> None:
    raise PilotContractError(code)


def _exact(value: object, expected: type[object], name: str) -> None:
    if type(value) is not expected:
        _fail(name)


def _id(value: object, name: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail(name)
    return value


def _ticker(value: object, name: str) -> str:
    if type(value) is not str or _TICKER.fullmatch(value) is None:
        _fail(name)
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(name)
    return value


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if (
        type(value) is not int
        or value < (1 if positive else 0)
        or value > _MAX_SIGNED_64
    ):
        _fail(name)
    return value


def _decimal(
    value: object,
    name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    positive: bool = False,
) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        _fail(name)
    if minimum is not None and (value <= minimum if positive else value < minimum):
        _fail(name)
    if maximum is not None and value > maximum:
        _fail(name)
    return value


def _canonical_digest(domain: bytes, projection: object) -> str:
    return sha256(domain + canonical_pilot_contract_bytes(projection)).hexdigest()


def _canonical_project(value: object, active: set[int]) -> object:
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        _integer(value, "canonical_integer")
        return value
    if type(value) is Decimal:
        if not value.is_finite():
            _fail("canonical_decimal")
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return {"$decimal": "0" if value.is_zero() else text}
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    value_type = type(value)
    if value_type not in (tuple, list, dict) and not is_dataclass(value):
        _fail("canonical_value")
    identity = id(value)
    if identity in active:
        _fail("canonical_cycle")
    active.add(identity)
    try:
        if value_type is tuple:
            return {"$tuple": [_canonical_project(item, active) for item in value]}
        if value_type is list:
            return {"$list": [_canonical_project(item, active) for item in value]}
        if value_type is dict:
            if any(type(key) is not str for key in value):
                _fail("canonical_key")
            return {"$dict": [[key, _canonical_project(value[key], active)] for key in sorted(value)]}
        return {
            "$contract": value_type.__name__,
            "$version": 1,
            "fields": {field.name: _canonical_project(getattr(value, field.name), active) for field in fields(value)},
        }
    finally:
        active.remove(identity)


def canonical_pilot_contract_bytes(value: object) -> bytes:
    return json.dumps(
        {"canonical_version": 1, "domain": "inci-tennis-pilot", "value": _canonical_project(value, set())},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def pilot_contract_sha256(value: object) -> str:
    """Return the repository-standard canonical digest for a pilot value."""
    return sha256(b"INCI-TENNIS-PILOT-CONTRACT-SHA256-V1\0" + canonical_pilot_contract_bytes(value)).hexdigest()


def compute_training_match_ids_sha256(training_match_ids: tuple[str, ...]) -> str:
    if type(training_match_ids) is not tuple or not training_match_ids:
        _fail("training_match_ids")
    if any(type(match_id) is not str for match_id in training_match_ids):
        _fail("training_match_ids")
    ids = tuple(_id(match_id, "training_match_ids") for match_id in training_match_ids)
    if tuple(sorted(ids)) != ids or len(set(ids)) != len(ids):
        _fail("training_match_ids")
    return _canonical_digest(_TRAINING_IDS_DOMAIN, ids)


def compute_serve_strength_artifact_sha256(
    *,
    version: str,
    target_canonical_match_id: str,
    target_scheduled_start_wall_ns: int,
    cutoff_wall_ns: int,
    training_match_ids: tuple[str, ...],
    training_match_ids_sha256: str,
    source_data_sha256: str,
    feature_definition_sha256: str,
    code_sha256: str,
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
) -> str:
    return _canonical_digest(
        _SERVE_ARTIFACT_DOMAIN,
        {
            "version": version,
            "target_canonical_match_id": target_canonical_match_id,
            "target_scheduled_start_wall_ns": target_scheduled_start_wall_ns,
            "cutoff_wall_ns": cutoff_wall_ns,
            "training_match_ids": training_match_ids,
            "training_match_ids_sha256": training_match_ids_sha256,
            "source_data_sha256": source_data_sha256,
            "feature_definition_sha256": feature_definition_sha256,
            "code_sha256": code_sha256,
            "home_serve_point_probability": home_serve_point_probability,
            "away_serve_point_probability": away_serve_point_probability,
        },
    )


def compute_execution_scenario_sha256(
    *,
    version: str,
    decision_to_arrival_ns: int,
    maximum_pair_latency_ns: int,
    flat_wait_horizon_ns: int,
    holding_horizon_ns: int,
) -> str:
    return _canonical_digest(
        _EXECUTION_SCENARIO_DOMAIN,
        {
            "version": version,
            "decision_to_arrival_ns": decision_to_arrival_ns,
            "maximum_pair_latency_ns": maximum_pair_latency_ns,
            "flat_wait_horizon_ns": flat_wait_horizon_ns,
            "holding_horizon_ns": holding_horizon_ns,
        },
    )


@dataclass(frozen=True, slots=True)
class PilotPriceLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _decimal(self.price, "price", minimum=Decimal("0"), maximum=Decimal("1"), positive=True)
        _decimal(self.quantity, "quantity", minimum=Decimal("0"), positive=True)


def _ordered_ladder(value: object, name: str, *, ascending: bool) -> tuple[PilotPriceLevel, ...]:
    if type(value) is not tuple or not value:
        _fail(name)
    if any(type(level) is not PilotPriceLevel for level in value):
        _fail(name)
    prices = tuple(level.price for level in value)
    if len(set(prices)) != len(prices):
        _fail(name)
    expected = tuple(sorted(value, key=lambda level: level.price, reverse=not ascending))
    if value != expected:
        _fail(name)
    return value


def _point_score_coordinates(state: TennisState) -> tuple[object, ...]:
    return (
        state.status,
        state.termination_kind,
        state.winner,
        state.retired_side,
        state.completed_sets,
        state.games_home,
        state.games_away,
        state.points_home,
        state.points_away,
        state.in_tiebreak,
        state.tiebreak_points_home,
        state.tiebreak_points_away,
        state.tiebreak_first_server,
        state.server_for_next_point,
    )


def _expected_exact_next_state(
    before: TennisState,
    after: TennisState,
    winner: PlayerSide,
) -> TennisState:
    """Replay the asserted provider point using all accepted provenance."""
    synthetic = ProviderPoint(
        provider_source_id=before.provider_source_id,
        revision_domain_id=before.revision_domain_id,
        source_lineage_sha256=before.source_lineage_sha256,
        provider_event_id=after.last_provider_event_id,
        provider_match_id=before.provider_match_id,
        home_player_id=before.home_player_id,
        away_player_id=before.away_player_id,
        scheduled_start_wall_ns=before.scheduled_start_wall_ns,
        match_format=before.match_format,
        correction_epoch=before.correction_epoch,
        revision=after.revision,
        point_winner=winner,
        server_before_point=before.server_for_next_point,
        source_wall_ns=after.last_source_wall_ns,
        source_generated_wall_ns=after.last_source_generated_wall_ns,
        received_monotonic_ns=after.last_received_monotonic_ns,
        clock_uncertainty_ns=after.last_clock_uncertainty_ns,
    )
    result = apply_point(before, synthetic)
    return result.state


@dataclass(frozen=True, slots=True)
class PilotPointEvent:
    canonical_match_id: str
    point_id: str
    sequence_number: int
    before_state: TennisState
    after_state: TennisState
    server: PlayerSide
    winner: PlayerSide
    consensus_epoch: int
    consensus_transition_sha256: str
    supporting_source_lineage_sha256s: tuple[str, ...]
    received_wall_ns: int
    accepted_monotonic_ns: int

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _id(self.point_id, "point_id")
        _integer(self.sequence_number, "sequence_number", positive=True)
        if type(self.before_state) is not TennisState or type(self.after_state) is not TennisState:
            _fail("point_transition")
        _exact(self.server, PlayerSide, "point_transition")
        _exact(self.winner, PlayerSide, "point_transition")
        _integer(self.consensus_epoch, "consensus_epoch")
        _digest(self.consensus_transition_sha256, "consensus_transition_sha256")
        lineages = self.supporting_source_lineage_sha256s
        if type(lineages) is not tuple or len(lineages) < 2:
            _fail("supporting_source_lineage_sha256s")
        if any(_SHA256.fullmatch(lineage) is None for lineage in lineages) or len(set(lineages)) != len(lineages):
            _fail("supporting_source_lineage_sha256s")
        _integer(self.received_wall_ns, "received_wall_ns")
        _integer(self.accepted_monotonic_ns, "accepted_monotonic_ns")
        before = self.before_state
        after = self.after_state
        try:
            expected_after = _expected_exact_next_state(
                before, after, self.winner
            )
        except Exception:
            _fail("point_transition")
        if (
            before.match_format
            is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
            or after.match_format
            is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
            or before.server_for_next_point is not self.server
            or before.provider_source_id != after.provider_source_id
            or before.revision_domain_id != after.revision_domain_id
            or before.source_lineage_sha256 != after.source_lineage_sha256
            or before.provider_match_id != after.provider_match_id
            or before.home_player_id != after.home_player_id
            or before.away_player_id != after.away_player_id
            or before.scheduled_start_wall_ns != after.scheduled_start_wall_ns
            or before.match_format != after.match_format
            or before.correction_epoch != after.correction_epoch
            or after != expected_after
            or self.accepted_monotonic_ns < after.last_received_monotonic_ns
        ):
            _fail("point_transition")


@dataclass(frozen=True, slots=True)
class ServeStrengthArtifact:
    version: str
    artifact_sha256: str
    target_canonical_match_id: str
    target_scheduled_start_wall_ns: int
    cutoff_wall_ns: int
    training_match_ids: tuple[str, ...]
    training_match_ids_sha256: str
    source_data_sha256: str
    feature_definition_sha256: str
    code_sha256: str
    home_serve_point_probability: Decimal
    away_serve_point_probability: Decimal

    def __post_init__(self) -> None:
        _id(self.version, "version")
        _id(self.target_canonical_match_id, "target_canonical_match_id")
        _integer(self.target_scheduled_start_wall_ns, "target_scheduled_start_wall_ns", positive=True)
        _integer(self.cutoff_wall_ns, "cutoff_wall_ns")
        if self.cutoff_wall_ns >= self.target_scheduled_start_wall_ns:
            _fail("cutoff_wall_ns")
        if self.target_canonical_match_id in self.training_match_ids:
            _fail("training_match_ids")
        if self.training_match_ids_sha256 != compute_training_match_ids_sha256(self.training_match_ids):
            _fail("training_match_ids_sha256")
        for value, name in (
            (self.source_data_sha256, "source_data_sha256"),
            (self.feature_definition_sha256, "feature_definition_sha256"),
            (self.code_sha256, "code_sha256"),
        ):
            _digest(value, name)
        _decimal(self.home_serve_point_probability, "home_serve_point_probability", minimum=Decimal("0"), maximum=Decimal("1"), positive=True)
        _decimal(self.away_serve_point_probability, "away_serve_point_probability", minimum=Decimal("0"), maximum=Decimal("1"), positive=True)
        if self.artifact_sha256 != compute_serve_strength_artifact_sha256(
            version=self.version,
            target_canonical_match_id=self.target_canonical_match_id,
            target_scheduled_start_wall_ns=self.target_scheduled_start_wall_ns,
            cutoff_wall_ns=self.cutoff_wall_ns,
            training_match_ids=self.training_match_ids,
            training_match_ids_sha256=self.training_match_ids_sha256,
            source_data_sha256=self.source_data_sha256,
            feature_definition_sha256=self.feature_definition_sha256,
            code_sha256=self.code_sha256,
            home_serve_point_probability=self.home_serve_point_probability,
            away_serve_point_probability=self.away_serve_point_probability,
        ):
            _fail("artifact_sha256")


@dataclass(frozen=True, slots=True)
class PilotOutcomeEstimate:
    model_version: str
    supported: bool
    home_next_point_probability: Decimal | None
    home_current_set_probability: Decimal | None
    home_match_probability: Decimal | None
    lower_home_match_probability: Decimal | None
    upper_home_match_probability: Decimal | None
    abstention_reason: PilotSupportReason | None

    def __post_init__(self) -> None:
        _id(self.model_version, "model_version")
        _exact(self.supported, bool, "supported")
        if self.abstention_reason is not None:
            _exact(self.abstention_reason, PilotSupportReason, "abstention_reason")
        values = (
            self.home_next_point_probability,
            self.home_current_set_probability,
            self.home_match_probability,
            self.lower_home_match_probability,
            self.upper_home_match_probability,
        )
        if self.supported:
            if self.abstention_reason is not None or any(value is None for value in values):
                _fail("outcome_estimate")
            for value in values:
                _decimal(value, "outcome_estimate", minimum=Decimal("0"), maximum=Decimal("1"))
            if self.lower_home_match_probability > self.home_match_probability or self.home_match_probability > self.upper_home_match_probability:
                _fail("outcome_estimate")
        elif self.abstention_reason is None or any(value is not None for value in values):
            _fail("outcome_estimate")


@dataclass(frozen=True, slots=True)
class DynamicBeliefSnapshot:
    canonical_match_id: str
    point_event_sha256: str
    dynamic_artifact_sha256: str
    home_weights: tuple[Decimal, Decimal, Decimal]
    away_weights: tuple[Decimal, Decimal, Decimal]
    belief_sha256: str

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _digest(self.point_event_sha256, "point_event_sha256")
        _digest(self.dynamic_artifact_sha256, "dynamic_artifact_sha256")
        for weights in (self.home_weights, self.away_weights):
            if type(weights) is not tuple or len(weights) != 3:
                _fail("belief_weights")
            if any(type(value) is not Decimal or not value.is_finite() or value < Decimal("0") for value in weights) or sum(weights, Decimal("0")) != Decimal("1"):
                _fail("belief_weights")
        _digest(self.belief_sha256, "belief_sha256")
        if self.belief_sha256 != pilot_contract_sha256({
            "canonical_match_id": self.canonical_match_id,
            "point_event_sha256": self.point_event_sha256,
            "dynamic_artifact_sha256": self.dynamic_artifact_sha256,
            "home_weights": self.home_weights,
            "away_weights": self.away_weights,
        }):
            _fail("belief_sha256")


def compute_pilot_book_snapshot_sha256(**values: object) -> str:
    return _canonical_digest(_BOOK_DOMAIN, values)


@dataclass(frozen=True, slots=True)
class PilotBookSnapshot:
    canonical_match_id: str
    player_side: PlayerSide
    market_ticker: str
    market_id: str
    contract_side: ContractSide
    bid_levels: tuple[PilotPriceLevel, ...]
    ask_levels: tuple[PilotPriceLevel, ...]
    captured_wall_ns: int
    captured_monotonic_ns: int
    source_frame_id: str
    source_l2_observation_sha256: str
    physical_connection_generation: int
    subscription_id: int
    global_sequence: int
    consensus_epoch: int
    correction_epoch: int
    accepted_score_sha256: str
    match_binding_sha256: str
    binding_metadata_sha256: str
    trusted: bool
    stale: bool
    book_sha256: str

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _exact(self.player_side, PlayerSide, "player_side")
        _ticker(self.market_ticker, "market_ticker")
        _id(self.market_id, "market_id")
        _exact(self.contract_side, ContractSide, "contract_side")
        if self.contract_side is not ContractSide.YES:
            _fail("contract_side")
        bids = _ordered_ladder(self.bid_levels, "bid_levels", ascending=False)
        asks = _ordered_ladder(self.ask_levels, "ask_levels", ascending=True)
        for value, name, positive in (
            (self.captured_wall_ns, "captured_wall_ns", False),
            (self.captured_monotonic_ns, "captured_monotonic_ns", False),
            (self.physical_connection_generation, "physical_connection_generation", True),
            (self.subscription_id, "subscription_id", True),
            (self.global_sequence, "global_sequence", True),
            (self.consensus_epoch, "consensus_epoch", False),
            (self.correction_epoch, "correction_epoch", False),
        ):
            _integer(value, name, positive=positive)
        for value, name in (
            (self.source_frame_id, "source_frame_id"),
            (self.source_l2_observation_sha256, "source_l2_observation_sha256"),
            (self.accepted_score_sha256, "accepted_score_sha256"),
            (self.match_binding_sha256, "match_binding_sha256"),
            (self.binding_metadata_sha256, "binding_metadata_sha256"),
        ):
            _digest(value, name)
        _exact(self.trusted, bool, "trusted")
        _exact(self.stale, bool, "stale")
        if self.trusted is self.stale:
            _fail("book_trust")
        projection = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "book_sha256"}
        if self.book_sha256 != compute_pilot_book_snapshot_sha256(**projection):
            _fail("book_sha256")


@dataclass(frozen=True, slots=True)
class PilotExecutionScenario:
    version: str
    artifact_sha256: str
    decision_to_arrival_ns: int
    maximum_pair_latency_ns: int
    flat_wait_horizon_ns: int
    holding_horizon_ns: int

    def __post_init__(self) -> None:
        _id(self.version, "version")
        for value, name in (
            (self.decision_to_arrival_ns, "decision_to_arrival_ns"),
            (self.maximum_pair_latency_ns, "maximum_pair_latency_ns"),
            (self.flat_wait_horizon_ns, "flat_wait_horizon_ns"),
        ):
            _integer(value, name)
        if self.holding_horizon_ns != _HOLDING_HORIZON_NS:
            _fail("holding_horizon_ns")
        if self.artifact_sha256 != compute_execution_scenario_sha256(
            version=self.version,
            decision_to_arrival_ns=self.decision_to_arrival_ns,
            maximum_pair_latency_ns=self.maximum_pair_latency_ns,
            flat_wait_horizon_ns=self.flat_wait_horizon_ns,
            holding_horizon_ns=self.holding_horizon_ns,
        ):
            _fail("artifact_sha256")


@dataclass(frozen=True, slots=True)
class PilotDecisionFrame:
    point_event: PilotPointEvent
    home_book: PilotBookSnapshot
    away_book: PilotBookSnapshot
    source_frame_id: str
    source_l2_observation_sha256: str
    consensus_transition_sha256: str
    accepted_score_sha256: str
    match_binding_sha256: str
    binding_metadata_sha256: str
    execution_scenario_sha256: str
    binding_artifact_sha256: str
    consensus_record_sequence: int
    consensus_record_sha256: str
    prior_accepted_score_sha256: str | None
    l2_state_sha256: str
    raw_book_parent_sha256: str
    raw_book_parent_durable_record_sequence: int
    raw_book_parent_durable_record_sha256: str
    raw_book_parent_received_wall_ns: int
    raw_book_parent_received_monotonic_ns: int
    physical_connection_generation: int
    subscription_id: int
    global_sequence: int
    consensus_accepted_wall_ns: int
    consensus_accepted_monotonic_ns: int
    book_captured_wall_ns: int
    book_captured_monotonic_ns: int
    decision_frame_sha256: str

    def __post_init__(self) -> None:
        if type(self.point_event) is not PilotPointEvent or type(self.home_book) is not PilotBookSnapshot or type(self.away_book) is not PilotBookSnapshot:
            _fail("decision_frame")
        if (
            self.home_book.player_side is not PlayerSide.HOME
            or self.away_book.player_side is not PlayerSide.AWAY
            or self.home_book.canonical_match_id != self.point_event.canonical_match_id
            or self.away_book.canonical_match_id != self.point_event.canonical_match_id
            or self.home_book.source_frame_id != self.source_frame_id
            or self.away_book.source_frame_id != self.source_frame_id
            or self.home_book.source_l2_observation_sha256 != self.source_l2_observation_sha256
            or self.away_book.source_l2_observation_sha256 != self.source_l2_observation_sha256
            or self.home_book.consensus_epoch != self.point_event.consensus_epoch
            or self.away_book.consensus_epoch != self.point_event.consensus_epoch
        ):
            _fail("decision_frame")
        for value, name in (
            (self.source_frame_id, "source_frame_id"), (self.source_l2_observation_sha256, "source_l2_observation_sha256"),
            (self.consensus_transition_sha256, "consensus_transition_sha256"), (self.accepted_score_sha256, "accepted_score_sha256"),
            (self.match_binding_sha256, "match_binding_sha256"), (self.binding_metadata_sha256, "binding_metadata_sha256"),
            (self.execution_scenario_sha256, "execution_scenario_sha256"),
            (self.binding_artifact_sha256, "binding_artifact_sha256"),
            (self.consensus_record_sha256, "consensus_record_sha256"),
            (self.l2_state_sha256, "l2_state_sha256"),
            (self.raw_book_parent_sha256, "raw_book_parent_sha256"),
            (self.raw_book_parent_durable_record_sha256, "raw_book_parent_durable_record_sha256"),
        ):
            _digest(value, name)
        if self.prior_accepted_score_sha256 is not None:
            _digest(self.prior_accepted_score_sha256, "prior_accepted_score_sha256")
        for value, name in (
            (self.consensus_record_sequence, "consensus_record_sequence"),
            (self.raw_book_parent_durable_record_sequence, "raw_book_parent_durable_record_sequence"),
            (self.raw_book_parent_received_wall_ns, "raw_book_parent_received_wall_ns"),
            (self.raw_book_parent_received_monotonic_ns, "raw_book_parent_received_monotonic_ns"),
            (self.physical_connection_generation, "physical_connection_generation"),
            (self.subscription_id, "subscription_id"),
            (self.global_sequence, "global_sequence"),
            (self.consensus_accepted_wall_ns, "consensus_accepted_wall_ns"), (self.consensus_accepted_monotonic_ns, "consensus_accepted_monotonic_ns"),
            (self.book_captured_wall_ns, "book_captured_wall_ns"), (self.book_captured_monotonic_ns, "book_captured_monotonic_ns"),
        ):
            _integer(value, name)
        if self.book_captured_monotonic_ns < self.consensus_accepted_monotonic_ns:
            _fail("decision_frame")
        projection = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "decision_frame_sha256"}
        if self.decision_frame_sha256 != pilot_contract_sha256(projection):
            _fail("decision_frame_sha256")


@dataclass(frozen=True, slots=True)
class PilotFrameAbstention:
    reason: PilotSupportReason
    canonical_match_id: str
    source_frame_id: str
    source_l2_observation_sha256: str
    consensus_transition_sha256: str
    accepted_score_sha256: str
    match_binding_sha256: str
    binding_metadata_sha256: str
    binding_artifact_sha256: str
    execution_scenario_sha256: str
    consensus_record_sequence: int
    consensus_record_sha256: str
    prior_accepted_score_sha256: str | None
    l2_state_sha256: str
    raw_book_parent_sha256: str
    raw_book_parent_durable_record_sequence: int
    raw_book_parent_durable_record_sha256: str
    raw_book_parent_received_wall_ns: int
    raw_book_parent_received_monotonic_ns: int
    consensus_accepted_wall_ns: int
    consensus_accepted_monotonic_ns: int
    book_captured_wall_ns: int
    book_captured_monotonic_ns: int
    physical_connection_generation: int
    subscription_id: int
    global_sequence: int
    abstention_sha256: str

    def __post_init__(self) -> None:
        _exact(self.reason, PilotSupportReason, "reason")
        _id(self.canonical_match_id, "canonical_match_id")
        for value, name in (
            (self.source_frame_id, "source_frame_id"), (self.source_l2_observation_sha256, "source_l2_observation_sha256"),
            (self.consensus_transition_sha256, "consensus_transition_sha256"), (self.accepted_score_sha256, "accepted_score_sha256"),
            (self.match_binding_sha256, "match_binding_sha256"), (self.binding_metadata_sha256, "binding_metadata_sha256"),
            (self.binding_artifact_sha256, "binding_artifact_sha256"), (self.execution_scenario_sha256, "execution_scenario_sha256"),
            (self.consensus_record_sha256, "consensus_record_sha256"), (self.l2_state_sha256, "l2_state_sha256"),
            (self.raw_book_parent_sha256, "raw_book_parent_sha256"), (self.raw_book_parent_durable_record_sha256, "raw_book_parent_durable_record_sha256"),
        ):
            _digest(value, name)
        if self.prior_accepted_score_sha256 is not None:
            _digest(self.prior_accepted_score_sha256, "prior_accepted_score_sha256")
        for value, name in ((self.consensus_record_sequence, "consensus_record_sequence"), (self.raw_book_parent_durable_record_sequence, "raw_book_parent_durable_record_sequence"), (self.raw_book_parent_received_wall_ns, "raw_book_parent_received_wall_ns"), (self.raw_book_parent_received_monotonic_ns, "raw_book_parent_received_monotonic_ns"), (self.consensus_accepted_wall_ns, "consensus_accepted_wall_ns"), (self.consensus_accepted_monotonic_ns, "consensus_accepted_monotonic_ns"), (self.book_captured_wall_ns, "book_captured_wall_ns"), (self.book_captured_monotonic_ns, "book_captured_monotonic_ns"), (self.physical_connection_generation, "physical_connection_generation"), (self.subscription_id, "subscription_id"), (self.global_sequence, "global_sequence")):
            _integer(value, name)
        projection = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "abstention_sha256"}
        if self.abstention_sha256 != pilot_contract_sha256(projection):
            _fail("abstention_sha256")


@dataclass(frozen=True, slots=True)
class PilotFrameProjection:
    decision_frame: PilotDecisionFrame | None
    abstention: PilotFrameAbstention | None
    projection_sha256: str

    def __post_init__(self) -> None:
        if (self.decision_frame is None) == (self.abstention is None):
            _fail("frame_projection")
        if self.decision_frame is not None and type(self.decision_frame) is not PilotDecisionFrame:
            _fail("frame_projection")
        if self.abstention is not None and type(self.abstention) is not PilotFrameAbstention:
            _fail("frame_projection")
        projection = {"decision_frame": self.decision_frame, "abstention": self.abstention}
        if self.projection_sha256 != pilot_contract_sha256(projection):
            _fail("projection_sha256")


def _validate_route(
    *, action: PilotAction | PilotImmediateAction, selected_player_side: PlayerSide | None,
    selected_market_ticker: str | None, selected_market_id: str | None,
    selected_contract_side: ContractSide | None, decision_book_sha256: str | None,
    requested_quantity: Decimal, decision_monotonic_ns: int, arrival_due_monotonic_ns: int | None,
    buying: bool,
) -> None:
    if action in ((PilotAction.BUY, PilotAction.HOLD, PilotAction.SELL) if buying else (PilotImmediateAction.BUY_NOW, PilotImmediateAction.HOLD, PilotImmediateAction.SELL)):
        if selected_player_side is None or selected_market_ticker is None or selected_market_id is None or selected_contract_side is None or decision_book_sha256 is None:
            _fail("selected_route")
        _exact(selected_player_side, PlayerSide, "selected_route")
        _ticker(selected_market_ticker, "selected_route")
        _id(selected_market_id, "selected_route")
        if selected_contract_side is not ContractSide.YES:
            _fail("selected_route")
        _digest(decision_book_sha256, "selected_route")
        _decimal(requested_quantity, "requested_quantity", minimum=Decimal("0"), positive=True)
        if arrival_due_monotonic_ns is None or arrival_due_monotonic_ns < decision_monotonic_ns:
            _fail("arrival_due_monotonic_ns")
    elif any(value is not None for value in (selected_player_side, selected_market_ticker, selected_market_id, selected_contract_side, decision_book_sha256, arrival_due_monotonic_ns)) or requested_quantity != Decimal("0"):
        _fail("selected_route")


@dataclass(frozen=True, slots=True)
class PilotRoute:
    player_side: PlayerSide
    market_ticker: str
    market_id: str
    contract_side: ContractSide
    entry_book_sha256: str

    def __post_init__(self) -> None:
        _exact(self.player_side, PlayerSide, "route")
        _ticker(self.market_ticker, "route")
        _id(self.market_id, "route")
        if self.contract_side is not ContractSide.YES:
            _fail("route")
        _digest(self.entry_book_sha256, "route")


def _book_for_side(frame: PilotDecisionFrame, side: PlayerSide) -> PilotBookSnapshot:
    return frame.home_book if side is PlayerSide.HOME else frame.away_book


def _route_for_book(book: PilotBookSnapshot) -> PilotRoute:
    return PilotRoute(book.player_side, book.market_ticker, book.market_id, book.contract_side, book.book_sha256)


@dataclass(frozen=True, slots=True)
class PilotPolicyEstimate:
    supported: bool
    action: PilotAction
    abstention_reason: PilotSupportReason | None
    point_event_sha256: str
    decision_frame: PilotDecisionFrame
    decision_frame_sha256: str
    locked_entry_route: PilotRoute | None
    selected_player_side: PlayerSide | None
    selected_market_ticker: str | None
    selected_market_id: str | None
    selected_contract_side: ContractSide | None
    decision_book_sha256: str | None
    requested_quantity: Decimal
    decision_monotonic_ns: int
    arrival_due_monotonic_ns: int | None
    buy_value: Decimal | None
    wait_value: Decimal | None
    sell_value: Decimal | None
    hold_value: Decimal | None
    buy_branch_holding_horizon_ns: int | None

    def __post_init__(self) -> None:
        _exact(self.supported, bool, "supported")
        _exact(self.action, PilotAction, "action")
        _digest(self.point_event_sha256, "point_event_sha256")
        if type(self.decision_frame) is not PilotDecisionFrame:
            _fail("decision_frame")
        _digest(self.decision_frame_sha256, "decision_frame_sha256")
        if self.decision_frame_sha256 != self.decision_frame.decision_frame_sha256 or self.point_event_sha256 != pilot_contract_sha256(self.decision_frame.point_event):
            _fail("decision_frame")
        if self.locked_entry_route is not None:
            _exact(self.locked_entry_route, PilotRoute, "locked_entry_route")
        _integer(self.decision_monotonic_ns, "decision_monotonic_ns")
        if self.arrival_due_monotonic_ns is not None:
            _integer(self.arrival_due_monotonic_ns, "arrival_due_monotonic_ns")
        if self.abstention_reason is not None:
            _exact(self.abstention_reason, PilotSupportReason, "abstention_reason")
        if self.supported == (self.abstention_reason is not None) or (not self.supported and self.action is not PilotAction.ABSTAIN):
            _fail("policy_support")
        _validate_route(action=self.action, selected_player_side=self.selected_player_side, selected_market_ticker=self.selected_market_ticker, selected_market_id=self.selected_market_id, selected_contract_side=self.selected_contract_side, decision_book_sha256=self.decision_book_sha256, requested_quantity=self.requested_quantity, decision_monotonic_ns=self.decision_monotonic_ns, arrival_due_monotonic_ns=self.arrival_due_monotonic_ns, buying=True)
        if self.action in (PilotAction.BUY, PilotAction.HOLD, PilotAction.SELL):
            assert self.selected_player_side is not None
            book = _book_for_side(self.decision_frame, self.selected_player_side)
            if (self.selected_market_ticker, self.selected_market_id, self.selected_contract_side, self.decision_book_sha256) != (book.market_ticker, book.market_id, book.contract_side, book.book_sha256):
                _fail("selected_route")
            if self.action in (PilotAction.HOLD, PilotAction.SELL):
                if self.locked_entry_route is None or (self.locked_entry_route.player_side, self.locked_entry_route.market_ticker, self.locked_entry_route.market_id, self.locked_entry_route.contract_side) != (book.player_side, book.market_ticker, book.market_id, book.contract_side):
                    _fail("locked_entry_route")
        for value in (self.buy_value, self.wait_value, self.sell_value, self.hold_value):
            if value is not None:
                _decimal(value, "policy_value")
        if self.buy_branch_holding_horizon_ns is not None and self.buy_branch_holding_horizon_ns != _HOLDING_HORIZON_NS:
            _fail("buy_branch_holding_horizon_ns")


@dataclass(frozen=True, slots=True)
class PilotImmediateBaselineEstimate:
    supported: bool
    action: PilotImmediateAction
    abstention_reason: PilotSupportReason | None
    point_event_sha256: str
    decision_frame: PilotDecisionFrame
    decision_frame_sha256: str
    locked_entry_route: PilotRoute | None
    selected_player_side: PlayerSide | None
    selected_market_ticker: str | None
    selected_market_id: str | None
    selected_contract_side: ContractSide | None
    decision_book_sha256: str | None
    requested_quantity: Decimal
    decision_monotonic_ns: int
    arrival_due_monotonic_ns: int | None

    def __post_init__(self) -> None:
        _exact(self.supported, bool, "supported")
        _exact(self.action, PilotImmediateAction, "action")
        _digest(self.point_event_sha256, "point_event_sha256")
        if type(self.decision_frame) is not PilotDecisionFrame:
            _fail("decision_frame")
        _digest(self.decision_frame_sha256, "decision_frame_sha256")
        if self.decision_frame_sha256 != self.decision_frame.decision_frame_sha256 or self.point_event_sha256 != pilot_contract_sha256(self.decision_frame.point_event):
            _fail("decision_frame")
        if self.locked_entry_route is not None:
            _exact(self.locked_entry_route, PilotRoute, "locked_entry_route")
        _integer(self.decision_monotonic_ns, "decision_monotonic_ns")
        if self.arrival_due_monotonic_ns is not None:
            _integer(self.arrival_due_monotonic_ns, "arrival_due_monotonic_ns")
        if self.abstention_reason is not None:
            _exact(self.abstention_reason, PilotSupportReason, "abstention_reason")
        if self.supported == (self.abstention_reason is not None) or (not self.supported and self.action is not PilotImmediateAction.ABSTAIN):
            _fail("baseline_support")
        _validate_route(action=self.action, selected_player_side=self.selected_player_side, selected_market_ticker=self.selected_market_ticker, selected_market_id=self.selected_market_id, selected_contract_side=self.selected_contract_side, decision_book_sha256=self.decision_book_sha256, requested_quantity=self.requested_quantity, decision_monotonic_ns=self.decision_monotonic_ns, arrival_due_monotonic_ns=self.arrival_due_monotonic_ns, buying=False)
        if self.action in (PilotImmediateAction.BUY_NOW, PilotImmediateAction.HOLD, PilotImmediateAction.SELL):
            assert self.selected_player_side is not None
            book = _book_for_side(self.decision_frame, self.selected_player_side)
            if (self.selected_market_ticker, self.selected_market_id, self.selected_contract_side, self.decision_book_sha256) != (book.market_ticker, book.market_id, book.contract_side, book.book_sha256):
                _fail("selected_route")
            if self.action in (PilotImmediateAction.HOLD, PilotImmediateAction.SELL) and (self.locked_entry_route is None or self.locked_entry_route.player_side is not book.player_side or self.locked_entry_route.market_ticker != book.market_ticker or self.locked_entry_route.market_id != book.market_id or self.locked_entry_route.contract_side is not book.contract_side):
                _fail("locked_entry_route")


def make_pilot_policy_estimate(
    *,
    decision_frame: PilotDecisionFrame,
    supported: bool,
    action: PilotAction,
    abstention_reason: PilotSupportReason | None,
    selected_player_side: PlayerSide | None,
    requested_quantity: Decimal,
    decision_monotonic_ns: int,
    arrival_due_monotonic_ns: int | None,
    buy_value: Decimal | None = None,
    wait_value: Decimal | None = None,
    sell_value: Decimal | None = None,
    hold_value: Decimal | None = None,
    buy_branch_holding_horizon_ns: int | None = None,
    locked_entry_route: PilotRoute | None = None,
) -> PilotPolicyEstimate:
    if type(decision_frame) is not PilotDecisionFrame:
        _fail("decision_frame")
    book = _book_for_side(decision_frame, selected_player_side) if selected_player_side is not None else None
    routed = action in (PilotAction.BUY, PilotAction.HOLD, PilotAction.SELL)
    if routed != (book is not None):
        _fail("selected_route")
    return PilotPolicyEstimate(
        supported=supported, action=action, abstention_reason=abstention_reason,
        point_event_sha256=pilot_contract_sha256(decision_frame.point_event),
        decision_frame=decision_frame, decision_frame_sha256=decision_frame.decision_frame_sha256,
        locked_entry_route=locked_entry_route if locked_entry_route is not None else (_route_for_book(book) if action is PilotAction.BUY and book is not None else None),
        selected_player_side=selected_player_side,
        selected_market_ticker=book.market_ticker if book is not None else None,
        selected_market_id=book.market_id if book is not None else None,
        selected_contract_side=book.contract_side if book is not None else None,
        decision_book_sha256=book.book_sha256 if book is not None else None,
        requested_quantity=requested_quantity, decision_monotonic_ns=decision_monotonic_ns,
        arrival_due_monotonic_ns=arrival_due_monotonic_ns, buy_value=buy_value,
        wait_value=wait_value, sell_value=sell_value, hold_value=hold_value,
        buy_branch_holding_horizon_ns=buy_branch_holding_horizon_ns,
    )


def make_pilot_immediate_baseline_estimate(
    *,
    decision_frame: PilotDecisionFrame,
    supported: bool,
    action: PilotImmediateAction,
    abstention_reason: PilotSupportReason | None,
    selected_player_side: PlayerSide | None,
    requested_quantity: Decimal,
    decision_monotonic_ns: int,
    arrival_due_monotonic_ns: int | None,
    locked_entry_route: PilotRoute | None = None,
) -> PilotImmediateBaselineEstimate:
    if type(decision_frame) is not PilotDecisionFrame:
        _fail("decision_frame")
    book = _book_for_side(decision_frame, selected_player_side) if selected_player_side is not None else None
    routed = action in (PilotImmediateAction.BUY_NOW, PilotImmediateAction.HOLD, PilotImmediateAction.SELL)
    if routed != (book is not None):
        _fail("selected_route")
    return PilotImmediateBaselineEstimate(
        supported=supported, action=action, abstention_reason=abstention_reason,
        point_event_sha256=pilot_contract_sha256(decision_frame.point_event),
        decision_frame=decision_frame, decision_frame_sha256=decision_frame.decision_frame_sha256,
        locked_entry_route=locked_entry_route if locked_entry_route is not None else (_route_for_book(book) if action is PilotImmediateAction.BUY_NOW and book is not None else None),
        selected_player_side=selected_player_side,
        selected_market_ticker=book.market_ticker if book is not None else None,
        selected_market_id=book.market_id if book is not None else None,
        selected_contract_side=book.contract_side if book is not None else None,
        decision_book_sha256=book.book_sha256 if book is not None else None,
        requested_quantity=requested_quantity, decision_monotonic_ns=decision_monotonic_ns,
        arrival_due_monotonic_ns=arrival_due_monotonic_ns,
    )


@dataclass(frozen=True, slots=True)
class PilotComparisonRow:
    canonical_match_id: str
    point_event_sha256: str
    point_event: PilotPointEvent
    static: PilotOutcomeEstimate
    belief: DynamicBeliefSnapshot | None
    dynamic: PilotOutcomeEstimate
    policy: PilotPolicyEstimate
    immediate_baseline: PilotImmediateBaselineEstimate
    home_book_sha256: str
    away_book_sha256: str
    comparison_row_sha256: str

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _digest(self.point_event_sha256, "point_event_sha256")
        if type(self.point_event) is not PilotPointEvent or type(self.static) is not PilotOutcomeEstimate or type(self.dynamic) is not PilotOutcomeEstimate or type(self.policy) is not PilotPolicyEstimate or type(self.immediate_baseline) is not PilotImmediateBaselineEstimate:
            _fail("comparison_row")
        if self.belief is not None and type(self.belief) is not DynamicBeliefSnapshot:
            _fail("comparison_row")
        if self.canonical_match_id != self.point_event.canonical_match_id or self.point_event_sha256 != pilot_contract_sha256(self.point_event) or self.policy.point_event_sha256 != self.point_event_sha256 or self.immediate_baseline.point_event_sha256 != self.point_event_sha256:
            _fail("comparison_row")
        _digest(self.home_book_sha256, "home_book_sha256")
        _digest(self.away_book_sha256, "away_book_sha256")
        projection = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "comparison_row_sha256"}
        if self.comparison_row_sha256 != pilot_contract_sha256(projection):
            _fail("comparison_row_sha256")
