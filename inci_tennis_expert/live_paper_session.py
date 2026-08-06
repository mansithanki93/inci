"""Pure durable reduction and replay for one live Models 1+2 paper session.

Raw payload bytes remain in the capture layer.  This module binds only their
durable receipts plus the immutable normalized inputs needed for exact replay.
It intentionally has no filesystem, network, client, or execution transport.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from re import ASCII, compile as pattern_compile
from types import MappingProxyType
from typing import Final, TypeAlias

from inci_tennis_expert.contracts import (
    MatchFormat, MatchStatus, PlayerSide, ScoreValue, SetScore, TennisState,
    TennisTransitionReason, TerminationKind,
)
from inci_tennis_expert.fee_schedule import (
    FillSide,
    FrozenFeeSchedule,
    LiquidityRole,
    fee_for_fill,
)
from inci_tennis_expert.live_paper_contracts import (
    LivePaperMarketBinding,
    LivePaperPointTransition,
    LivePaperRebaseCandidate,
    LivePaperScoreAnchor,
    LivePaperScoreCoordinatorState,
    LivePaperScoreDecision,
    LivePaperScoreDecisionKind,
    LivePaperSourceObservation,
    LivePaperSupport,
    PaperScoreTrust,
    score_coordinates,
)
from inci_tennis_expert.live_paper_execution import (
    LivePaperL2Frame,
    LivePaperL2Level,
    LivePaperL2Market,
    PaperAction,
    PaperActionKind,
    PaperDecision,
    PaperDecisionReason,
    PaperEvent,
    PaperFill,
    PaperPortfolioState,
    PaperPosition,
    PaperProfitClaim,
    _ConsumedDepth,
    evaluate_live_paper_entry,
    reduce_paper_book,
)
from inci_tennis_expert.live_paper_score import (
    initial_live_paper_score_coordinator_state,
    reduce_live_paper_scores,
)
from inci_tennis_expert.live_two_model import (
    LiveArtifactAuthority,
    LiveEdgeClaim,
    LiveTwoModelForecast,
    LiveTwoModelState,
    apply_live_paper_transition,
    open_live_two_model,
    rebase_live_two_model,
)
from inci_tennis_expert.pilot_contracts import (
    DynamicBeliefSnapshot, PilotOutcomeEstimate, PilotSupportReason,
    ServeStrengthArtifact,
)
from inci_tennis_expert.pilot_dynamic_model import (
    DynamicParameterCandidate, DynamicPointArtifact, DynamicPointModel,
)


__all__ = (
    "LivePaperSessionError",
    "LivePaperRecordKind",
    "LivePaperSessionConfig",
    "LivePaperScoreBatchInput",
    "LivePaperL2Input",
    "LivePaperHeartbeatInput",
    "LivePaperTerminalInput",
    "LivePaperInput",
    "LivePaperRecord",
    "LivePaperPositionMark",
    "LivePaperSessionState",
    "LivePaperReplayResult",
    "open_live_paper_session",
    "reduce_live_paper_input",
    "encode_live_paper_records",
    "replay_live_paper_records",
    "encode_live_paper_checkpoint",
    "decode_live_paper_checkpoint",
    "load_live_paper_checkpoint",
)


_RECORD_SCHEMA: Final[str] = "inci.live-paper-record"
_CHECKPOINT_SCHEMA: Final[str] = "inci.live-paper-checkpoint"
_VERSION: Final[int] = 1
_ZERO_SHA256: Final[str] = "0" * 64
_HEARTBEAT_NS: Final[int] = 60_000_000_000
_DECIMAL_RE = pattern_compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", ASCII)
_SHA_RE = pattern_compile(r"[0-9a-f]{64}\Z", ASCII)
_INTEGER_LIMIT = 10**256
_MAX_LOG_BYTES: Final[int] = 32 * 1024 * 1024
_MAX_CHECKPOINT_BYTES: Final[int] = 16 * 1024 * 1024
_MAX_RECORD_LINE_BYTES: Final[int] = 8 * 1024 * 1024
_MAX_RECORDS: Final[int] = 100_000
_MAX_JSON_DEPTH: Final[int] = 128
_MAX_JSON_NODES: Final[int] = 200_000
_MAX_JSON_COLLECTION: Final[int] = 10_000
_MAX_JSON_KEY_BYTES: Final[int] = 4_096
_MAX_JSON_STRING_BYTES: Final[int] = 1 * 1024 * 1024
_MAX_JSON_TOTAL_STRING_BYTES: Final[int] = 8 * 1024 * 1024


class LivePaperSessionError(ValueError):
    """Fixed-code rejection for malformed session evidence or input."""


def _fail(code: str) -> None:
    raise LivePaperSessionError(code)


class LivePaperRecordKind(str, Enum):
    RAW_SCORE_RECEIPT = "raw_score_receipt"
    RAW_L2_RECEIPT = "raw_l2_receipt"
    ANCHOR = "anchor"
    TRANSITION = "transition"
    FORECAST = "forecast"
    ABSTENTION = "abstention"
    REJECTION = "rejection"
    ACTION = "action"
    FILL = "fill"
    MARK = "mark"
    CHECKPOINT = "checkpoint"
    HEARTBEAT = "heartbeat"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class LivePaperSessionConfig:
    canonical_match_id: str
    static_artifact: ServeStrengthArtifact
    dynamic_artifact: DynamicPointArtifact
    artifact_authority: LiveArtifactAuthority
    market_binding: LivePaperMarketBinding
    fee_schedule: FrozenFeeSchedule
    fee_series_ticker: str
    opened_wall_ns: int
    opened_monotonic_ns: int
    decision_latency_ns: int = 1_000_000_000
    score_freshness_ns: int = 5_000_000_000
    book_freshness_ns: int = 5_000_000_000
    maximum_debit: Decimal = Decimal("50")
    minimum_edge: Decimal = Decimal("5")
    exit_profit: Decimal = Decimal("5")
    exit_loss: Decimal = Decimal("5")
    maximum_hold_ns: int = 300_000_000_000
    heartbeat_interval_ns: int = _HEARTBEAT_NS

    def __post_init__(self) -> None:
        if type(self.canonical_match_id) is not str or not self.canonical_match_id:
            _fail("config")
        if (
            type(self.static_artifact) is not ServeStrengthArtifact
            or type(self.dynamic_artifact) is not DynamicPointArtifact
            or type(self.artifact_authority) is not LiveArtifactAuthority
            or type(self.market_binding) is not LivePaperMarketBinding
            or type(self.fee_schedule) is not FrozenFeeSchedule
            or type(self.fee_series_ticker) is not str
            or self.fee_series_ticker not in self.fee_schedule.series_tickers
        ):
            _fail("config")
        if (
            self.market_binding.canonical_match_id != self.canonical_match_id
            or self.static_artifact.target_canonical_match_id != self.canonical_match_id
            or self.dynamic_artifact.target_canonical_match_id != self.canonical_match_id
            or self.market_binding.scheduled_start_wall_ns
            != self.static_artifact.target_scheduled_start_wall_ns
            or self.market_binding.scheduled_start_wall_ns
            != self.dynamic_artifact.target_scheduled_start_wall_ns
        ):
            _fail("config_binding")
        if any(type(value) is not int or value < 0 for value in (self.opened_wall_ns, self.opened_monotonic_ns)):
            _fail("config_clock")
        fixed = (
            (self.decision_latency_ns, 1_000_000_000),
            (self.score_freshness_ns, 5_000_000_000),
            (self.book_freshness_ns, 5_000_000_000),
            (self.maximum_debit, Decimal("50")),
            (self.minimum_edge, Decimal("5")),
            (self.exit_profit, Decimal("5")),
            (self.exit_loss, Decimal("5")),
            (self.maximum_hold_ns, 300_000_000_000),
            (self.heartbeat_interval_ns, _HEARTBEAT_NS),
        )
        if any(actual != expected or type(actual) is not type(expected) for actual, expected in fixed):
            _fail("policy_constants")


@dataclass(frozen=True, slots=True)
class LivePaperScoreBatchInput:
    observations: tuple[LivePaperSourceObservation, ...]
    observed_wall_ns: int
    observed_monotonic_ns: int

    def __post_init__(self) -> None:
        if type(self.observations) is not tuple or any(type(item) is not LivePaperSourceObservation for item in self.observations):
            _fail("score_input")
        _clocks(self.observed_wall_ns, self.observed_monotonic_ns)


@dataclass(frozen=True, slots=True)
class LivePaperL2Input:
    frame: LivePaperL2Frame
    observed_wall_ns: int
    observed_monotonic_ns: int

    def __post_init__(self) -> None:
        if type(self.frame) is not LivePaperL2Frame:
            _fail("l2_input")
        _clocks(self.observed_wall_ns, self.observed_monotonic_ns)


@dataclass(frozen=True, slots=True)
class LivePaperHeartbeatInput:
    observed_wall_ns: int
    observed_monotonic_ns: int

    def __post_init__(self) -> None:
        _clocks(self.observed_wall_ns, self.observed_monotonic_ns)


@dataclass(frozen=True, slots=True)
class LivePaperTerminalInput:
    reason: str
    observed_wall_ns: int
    observed_monotonic_ns: int

    def __post_init__(self) -> None:
        if type(self.reason) is not str or not self.reason:
            _fail("terminal_reason")
        _clocks(self.observed_wall_ns, self.observed_monotonic_ns)


LivePaperInput: TypeAlias = (
    LivePaperScoreBatchInput | LivePaperL2Input | LivePaperHeartbeatInput | LivePaperTerminalInput
)


@dataclass(frozen=True, slots=True)
class _LivePaperPayload:
    config: LivePaperSessionConfig | None
    body: object


@dataclass(frozen=True, slots=True)
class _LivePaperRejection:
    source: str
    reason: str
    detail: object


@dataclass(frozen=True, slots=True)
class LivePaperPositionMark:
    raw_parent_receipt_sha256: str
    global_sequence: int
    ticker: str
    position_quantity: Decimal
    priced_quantity: Decimal
    gross_credit: Decimal
    exit_fees: Decimal
    net_liquidation_value: Decimal
    unrealized_pnl: Decimal | None
    fully_priced: bool
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class _LivePaperCheckpointProjection:
    score_coordinator: LivePaperScoreCoordinatorState
    live_model: LiveTwoModelState | None
    latest_forecast: LiveTwoModelForecast | None
    portfolio: PaperPortfolioState
    previous_record_sha256: str
    previous_record_count: int


@dataclass(frozen=True, slots=True)
class LivePaperRecord:
    schema: str
    version: int
    record_ordinal: int
    previous_record_sha256: str
    kind: LivePaperRecordKind
    payload: _LivePaperPayload
    payload_sha256: str
    record_sha256: str

    def __post_init__(self) -> None:
        if (
            self.schema != _RECORD_SCHEMA
            or type(self.version) is not int
            or self.version != _VERSION
            or type(self.record_ordinal) is not int
            or self.record_ordinal <= 0
            or type(self.kind) is not LivePaperRecordKind
            or type(self.payload) is not _LivePaperPayload
            or not _is_sha(self.previous_record_sha256)
            or not _is_sha(self.payload_sha256)
            or not _is_sha(self.record_sha256)
        ):
            _fail("record")
        payload_projection = _project(self.payload)
        if self.payload_sha256 != _digest(_canonical_json(payload_projection)):
            _fail("payload_sha256")
        if self.record_sha256 != _record_digest(
            record_ordinal=self.record_ordinal,
            previous_record_sha256=self.previous_record_sha256,
            kind=self.kind,
            payload_projection=payload_projection,
            payload_sha256=self.payload_sha256,
        ):
            _fail("record_sha256")


@dataclass(frozen=True, slots=True)
class LivePaperSessionState:
    config: LivePaperSessionConfig
    score_coordinator: LivePaperScoreCoordinatorState
    live_model: LiveTwoModelState | None
    latest_forecast: LiveTwoModelForecast | None
    portfolio: PaperPortfolioState
    score_actionable: bool
    last_score_wall_ns: int | None
    last_score_monotonic_ns: int | None
    last_score_clock_uncertainty_ns: int | None
    record_head_sha256: str
    record_count: int
    next_heartbeat_wall_ns: int
    next_heartbeat_monotonic_ns: int
    terminal: bool

    def __post_init__(self) -> None:
        if (
            type(self.config) is not LivePaperSessionConfig
            or type(self.score_coordinator) is not LivePaperScoreCoordinatorState
            or (self.live_model is not None and type(self.live_model) is not LiveTwoModelState)
            or (self.latest_forecast is not None and type(self.latest_forecast) is not LiveTwoModelForecast)
            or type(self.portfolio) is not PaperPortfolioState
            or type(self.score_actionable) is not bool
            or not _is_sha(self.record_head_sha256)
            or type(self.record_count) is not int
            or self.record_count < 0
            or type(self.next_heartbeat_wall_ns) is not int
            or self.next_heartbeat_wall_ns < 0
            or type(self.next_heartbeat_monotonic_ns) is not int
            or self.next_heartbeat_monotonic_ns < 0
            or type(self.terminal) is not bool
        ):
            _fail("state")
        score_clocks = (
            self.last_score_wall_ns,
            self.last_score_monotonic_ns,
            self.last_score_clock_uncertainty_ns,
        )
        if any(value is not None and (type(value) is not int or value < 0) for value in score_clocks):
            _fail("score_clock")
        if len({value is None for value in score_clocks}) != 1 or (self.score_actionable and score_clocks[0] is None):
            _fail("score_clock")
        if (
            self.score_coordinator.canonical_match_id != self.config.canonical_match_id
            or self.portfolio.binding != self.config.market_binding
            or self.portfolio.fee_schedule != self.config.fee_schedule
            or self.portfolio.fee_series_ticker != self.config.fee_series_ticker
            or (self.record_count == 0) != (self.record_head_sha256 == _ZERO_SHA256)
        ):
            _fail("state_binding")


@dataclass(frozen=True, slots=True)
class LivePaperReplayResult:
    state: LivePaperSessionState
    records: tuple[LivePaperRecord, ...]
    checkpoint_used: bool = False


def _clocks(wall_ns: object, monotonic_ns: object) -> None:
    if type(wall_ns) is not int or wall_ns < 0 or type(monotonic_ns) is not int or monotonic_ns < 0:
        _fail("clock")


def _is_sha(value: object) -> bool:
    return type(value) is str and _SHA_RE.fullmatch(value) is not None


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError, MemoryError) as error:
        raise LivePaperSessionError("canonical_json") from error


def _digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _type_name(value: type[object]) -> str:
    return value.__module__ + "." + value.__qualname__


def _project(value: object) -> object:
    if value is None or type(value) in {bool, str}:
        return value
    if type(value) is int:
        if not -_INTEGER_LIMIT < value < _INTEGER_LIMIT:
            _fail("integer")
        return value
    if type(value) is Decimal:
        if not value.is_finite():
            _fail("decimal")
        return {"$decimal": format(value, "f")}
    if isinstance(value, Enum):
        return {"$enum": _type_name(type(value)), "value": value.value}
    if type(value) is tuple:
        return {"$tuple": [_project(item) for item in value]}
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _fail("mapping")
        return {"$map": {key: _project(item) for key, item in value.items()}}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": _type_name(type(value)),
            "$fields": {field.name: _project(getattr(value, field.name)) for field in fields(value)},
        }
    _fail("unsupported_type")


_V1_TYPES: Final[tuple[type[object], ...]] = (
    MatchFormat, MatchStatus, PlayerSide, ScoreValue, TennisTransitionReason,
    TerminationKind, SetScore, TennisState,
    FrozenFeeSchedule,
    PaperScoreTrust, LivePaperScoreDecisionKind, LivePaperMarketBinding,
    LivePaperSourceObservation, LivePaperSupport, LivePaperScoreAnchor,
    LivePaperPointTransition, LivePaperScoreDecision, LivePaperRebaseCandidate,
    LivePaperScoreCoordinatorState,
    LivePaperL2Level, LivePaperL2Market, LivePaperL2Frame,
    PaperDecisionReason, PaperActionKind, PaperProfitClaim, PaperAction,
    PaperFill, PaperEvent, PaperPosition, _ConsumedDepth, PaperPortfolioState,
    PaperDecision,
    LiveArtifactAuthority, LiveEdgeClaim, LiveTwoModelState,
    LiveTwoModelForecast,
    PilotSupportReason, ServeStrengthArtifact, PilotOutcomeEstimate,
    DynamicBeliefSnapshot, DynamicParameterCandidate, DynamicPointArtifact,
    DynamicPointModel,
    LivePaperRecordKind, LivePaperSessionConfig, LivePaperScoreBatchInput,
    LivePaperL2Input, LivePaperHeartbeatInput, LivePaperTerminalInput,
    _LivePaperPayload, _LivePaperRejection, LivePaperPositionMark,
    _LivePaperCheckpointProjection, LivePaperSessionState,
)
_V1_TYPE_NAMES: Final[tuple[str, ...]] = tuple(_type_name(item) for item in _V1_TYPES)
if len(set(_V1_TYPE_NAMES)) != len(_V1_TYPE_NAMES):
    raise RuntimeError("duplicate_live_paper_v1_type")
_V1_TYPE_REGISTRY: Final = MappingProxyType(dict(zip(_V1_TYPE_NAMES, _V1_TYPES, strict=True)))


def _unproject(value: object) -> object:
    if value is None or type(value) in {bool, str}:
        return value
    if type(value) is int:
        if not -_INTEGER_LIMIT < value < _INTEGER_LIMIT:
            _fail("integer")
        return value
    if type(value) is list:
        _fail("untyped_list")
    if type(value) is not dict:
        _fail("json_type")
    keys = set(value)
    if keys == {"$decimal"}:
        text = value["$decimal"]
        if type(text) is not str or _DECIMAL_RE.fullmatch(text) is None:
            _fail("decimal")
        try:
            result = Decimal(text)
        except InvalidOperation as error:
            raise LivePaperSessionError("decimal") from error
        if not result.is_finite() or format(result, "f") != text:
            _fail("decimal")
        return result
    if keys == {"$tuple"}:
        items = value["$tuple"]
        if type(items) is not list:
            _fail("tuple")
        return tuple(_unproject(item) for item in items)
    if keys == {"$map"}:
        mapping = value["$map"]
        if type(mapping) is not dict:
            _fail("mapping")
        return {key: _unproject(item) for key, item in mapping.items()}
    if keys == {"$enum", "value"}:
        encoded_type = value["$enum"]
        if type(encoded_type) is not str:
            _fail("unknown_type")
        enum_type = _V1_TYPE_REGISTRY.get(encoded_type)
        if enum_type is None or not issubclass(enum_type, Enum):
            _fail("unknown_type")
        try:
            return enum_type(value["value"])
        except (TypeError, ValueError) as error:
            raise LivePaperSessionError("enum") from error
    if keys == {"$type", "$fields"}:
        encoded_type = value["$type"]
        if type(encoded_type) is not str:
            _fail("unknown_type")
        data_type = _V1_TYPE_REGISTRY.get(encoded_type)
        raw_fields = value["$fields"]
        if data_type is None or not is_dataclass(data_type):
            _fail("unknown_type")
        if type(raw_fields) is not dict or set(raw_fields) != {field.name for field in fields(data_type)}:
            _fail("unknown_fields")
        values = {name: _unproject(item) for name, item in raw_fields.items()}
        try:
            return data_type(**values)
        except LivePaperSessionError:
            raise
        except Exception as error:
            raise LivePaperSessionError("typed_value") from error
    _fail("unknown_fields")


def _pairs_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _validate_json_limits(value: object) -> None:
    nodes = 0
    string_bytes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            _fail("json_nodes")
        if depth > _MAX_JSON_DEPTH:
            _fail("json_depth")
        if type(item) is str:
            size = len(item.encode("utf-8"))
            if size > _MAX_JSON_STRING_BYTES:
                _fail("json_string")
            string_bytes += size
        elif type(item) is list:
            if len(item) > _MAX_JSON_COLLECTION:
                _fail("json_collection")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            if len(item) > _MAX_JSON_COLLECTION:
                _fail("json_collection")
            for key, child in item.items():
                key_size = len(key.encode("utf-8"))
                if key_size > _MAX_JSON_KEY_BYTES:
                    _fail("json_key")
                string_bytes += key_size
                stack.append((child, depth + 1))
        if string_bytes > _MAX_JSON_TOTAL_STRING_BYTES:
            _fail("json_strings")


def _parse_json(raw: bytes, *, maximum_bytes: int = _MAX_CHECKPOINT_BYTES) -> object:
    if type(raw) is not bytes or len(raw) > maximum_bytes:
        _fail("json_too_large")
    try:
        text = raw.decode("ascii")
        decoded = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=lambda _: _fail("non_finite_number"),
        )
        _validate_json_limits(decoded)
        return decoded
    except LivePaperSessionError:
        raise
    except RecursionError as error:
        raise LivePaperSessionError("json_depth") from error
    except (OverflowError, MemoryError) as error:
        raise LivePaperSessionError("json_resource") from error
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise LivePaperSessionError("json") from error


def _record_digest(*, record_ordinal: int, previous_record_sha256: str, kind: LivePaperRecordKind, payload_projection: object, payload_sha256: str) -> str:
    core = {
        "schema": _RECORD_SCHEMA,
        "version": _VERSION,
        "record_ordinal": record_ordinal,
        "previous_record_sha256": previous_record_sha256,
        "kind": kind.value,
        "payload": payload_projection,
        "payload_sha256": payload_sha256,
    }
    return _digest(b"INCI-LIVE-PAPER-RECORD-V1\0" + _canonical_json(core))


def _make_record(state: LivePaperSessionState, kind: LivePaperRecordKind, body: object) -> tuple[LivePaperSessionState, LivePaperRecord]:
    payload = _LivePaperPayload(state.config if state.record_count == 0 else None, body)
    projection = _project(payload)
    payload_sha = _digest(_canonical_json(projection))
    ordinal = state.record_count + 1
    record_sha = _record_digest(
        record_ordinal=ordinal,
        previous_record_sha256=state.record_head_sha256,
        kind=kind,
        payload_projection=projection,
        payload_sha256=payload_sha,
    )
    record = LivePaperRecord(
        _RECORD_SCHEMA,
        _VERSION,
        ordinal,
        state.record_head_sha256,
        kind,
        payload,
        payload_sha,
        record_sha,
    )
    return replace(state, record_head_sha256=record_sha, record_count=ordinal), record


def _emit(state: LivePaperSessionState, records: list[LivePaperRecord], kind: LivePaperRecordKind, body: object) -> LivePaperSessionState:
    state, record = _make_record(state, kind, body)
    records.append(record)
    return state


def open_live_paper_session(config: LivePaperSessionConfig) -> LivePaperSessionState:
    if type(config) is not LivePaperSessionConfig:
        _fail("config")
    portfolio = PaperPortfolioState(
        binding=config.market_binding,
        completed_sets=0,
        match_live=False,
        fee_schedule=config.fee_schedule,
        fee_series_ticker=config.fee_series_ticker,
    )
    return LivePaperSessionState(
        config=config,
        score_coordinator=initial_live_paper_score_coordinator_state(config.canonical_match_id),
        live_model=None,
        latest_forecast=None,
        portfolio=portfolio,
        score_actionable=False,
        last_score_wall_ns=None,
        last_score_monotonic_ns=None,
        last_score_clock_uncertainty_ns=None,
        record_head_sha256=_ZERO_SHA256,
        record_count=0,
        next_heartbeat_wall_ns=config.opened_wall_ns + config.heartbeat_interval_ns,
        next_heartbeat_monotonic_ns=config.opened_monotonic_ns + config.heartbeat_interval_ns,
        terminal=False,
    )


def _portfolio_at_score(portfolio: PaperPortfolioState, decision: LivePaperScoreDecision) -> PaperPortfolioState:
    anchor = decision.anchor
    if anchor is None:
        return portfolio
    return replace(
        portfolio,
        completed_sets=len(anchor.state.completed_sets),
        match_live=anchor.state.status is MatchStatus.LIVE,
    )


def _checkpoint_projection(state: LivePaperSessionState) -> _LivePaperCheckpointProjection:
    return _LivePaperCheckpointProjection(
        state.score_coordinator,
        state.live_model,
        state.latest_forecast,
        state.portfolio,
        state.record_head_sha256,
        state.record_count,
    )


def _reduce_score(state: LivePaperSessionState, item: LivePaperScoreBatchInput) -> tuple[LivePaperSessionState, tuple[LivePaperRecord, ...]]:
    if any(observation.canonical_match_id != state.config.canonical_match_id for observation in item.observations):
        _fail("canonical_match_id")
    records: list[LivePaperRecord] = []
    state = _emit(state, records, LivePaperRecordKind.RAW_SCORE_RECEIPT, item)
    coordinator, decision = reduce_live_paper_scores(
        state.score_coordinator,
        item.observations,
        now_wall_ns=item.observed_wall_ns,
        now_monotonic_ns=item.observed_monotonic_ns,
    )
    state = replace(state, score_coordinator=coordinator, portfolio=_portfolio_at_score(state.portfolio, decision))
    actionable_kinds = {
        LivePaperScoreDecisionKind.ANCHORED,
        LivePaperScoreDecisionKind.POINT_ACCEPTED,
        LivePaperScoreDecisionKind.REBASED,
        LivePaperScoreDecisionKind.UNCHANGED,
    }
    invalidated_action: PaperAction | None = None
    if decision.kind in actionable_kinds:
        clock_actionable = True
        if decision.kind is LivePaperScoreDecisionKind.UNCHANGED:
            if decision.anchor is None:
                _fail("score_anchor")
            eligible = tuple(
                observation
                for observation in item.observations
                if observation.captured_monotonic_ns <= item.observed_monotonic_ns
                and item.observed_monotonic_ns - observation.captured_monotonic_ns
                <= state.config.score_freshness_ns
                and observation.captured_wall_ns <= item.observed_wall_ns
                and score_coordinates(observation.state)
                == score_coordinates(decision.anchor.state)
            )
            if not eligible:
                state = replace(state, score_actionable=False)
                clock_actionable = False
                wall_ns = state.last_score_wall_ns
                monotonic_ns = state.last_score_monotonic_ns
                uncertainty_ns = state.last_score_clock_uncertainty_ns
            else:
                wall_ns = max(observation.captured_wall_ns for observation in eligible)
                monotonic_ns = max(observation.captured_monotonic_ns for observation in eligible)
                uncertainty_ns = max(observation.state.last_clock_uncertainty_ns for observation in eligible)
        else:
            if decision.anchor is None:
                _fail("score_anchor")
            wall_ns = decision.anchor.accepted_wall_ns
            monotonic_ns = decision.anchor.accepted_monotonic_ns
            parent_receipts = set(decision.anchor.parent_receipt_sha256s)
            uncertainty_ns = max(
                observation.state.last_clock_uncertainty_ns
                for observation in item.observations
                if observation.raw_receipt_sha256 in parent_receipts
            )
        if clock_actionable and wall_ns is not None and monotonic_ns is not None and uncertainty_ns is not None:
            state = replace(
                state,
                score_actionable=True,
                last_score_wall_ns=wall_ns,
                last_score_monotonic_ns=monotonic_ns,
                last_score_clock_uncertainty_ns=uncertainty_ns,
            )
    else:
        pending = state.portfolio.pending_action
        if pending is not None and pending.kind is PaperActionKind.BUY:
            invalidated_action = pending
            state = replace(state, portfolio=replace(state.portfolio, pending_action=None))
        state = replace(state, score_actionable=False)
    if decision.kind is LivePaperScoreDecisionKind.ANCHORED:
        state = _emit(state, records, LivePaperRecordKind.ANCHOR, decision)
        model, forecast = open_live_two_model(
            static_artifact=state.config.static_artifact,
            dynamic_artifact=state.config.dynamic_artifact,
            anchor=decision.anchor,
            artifact_authority=state.config.artifact_authority,
        )
    elif decision.kind is LivePaperScoreDecisionKind.POINT_ACCEPTED:
        state = _emit(state, records, LivePaperRecordKind.TRANSITION, decision)
        if state.live_model is None:
            _fail("model_not_open")
        model, forecast = apply_live_paper_transition(state.live_model, decision.transition)
    elif decision.kind is LivePaperScoreDecisionKind.REBASED:
        state = _emit(state, records, LivePaperRecordKind.ANCHOR, decision)
        if state.live_model is None:
            model, forecast = open_live_two_model(
                static_artifact=state.config.static_artifact,
                dynamic_artifact=state.config.dynamic_artifact,
                anchor=decision.anchor,
                artifact_authority=state.config.artifact_authority,
            )
        else:
            model, forecast = rebase_live_two_model(state.live_model, decision.anchor)
    else:
        kind = LivePaperRecordKind.ABSTENTION if decision.kind in {
            LivePaperScoreDecisionKind.ABSTAINED,
            LivePaperScoreDecisionKind.QUARANTINED,
        } else LivePaperRecordKind.REJECTION
        state = _emit(state, records, kind, _LivePaperRejection("score", decision.reason, decision))
        if invalidated_action is not None:
            state = _emit(
                state,
                records,
                LivePaperRecordKind.REJECTION,
                _LivePaperRejection("paper_action", "pending_buy_invalidated", invalidated_action),
            )
        return state, tuple(records)
    state = replace(state, live_model=model, latest_forecast=forecast)
    state = _emit(state, records, LivePaperRecordKind.FORECAST, forecast)
    projection = _checkpoint_projection(state)
    state = _emit(state, records, LivePaperRecordKind.CHECKPOINT, projection)
    return state, tuple(records)


def _same_action_identity(left: PaperAction | None, right: PaperAction | None) -> bool:
    if left is None or right is None:
        return left is right
    return (
        left.kind,
        left.player_side,
        left.ticker,
        left.quantity,
        left.decision_global_sequence,
        left.decision_receipt_sha256,
    ) == (
        right.kind,
        right.player_side,
        right.ticker,
        right.quantity,
        right.decision_global_sequence,
        right.decision_receipt_sha256,
    )


def _position_mark(state: LivePaperSessionState, item: LivePaperL2Input, portfolio: PaperPortfolioState) -> LivePaperPositionMark:
    position = portfolio.position
    if position is None:
        _fail("mark_without_position")
    zero = Decimal("0")
    reason: str | None = None
    frame = item.frame
    if not frame.complete:
        reason = "book_incomplete"
    elif not frame.gap_free:
        reason = "book_sequence_gap"
    elif (
        item.observed_wall_ns < frame.captured_wall_ns
        or item.observed_monotonic_ns < frame.captured_monotonic_ns
        or max(
            item.observed_wall_ns - frame.captured_wall_ns,
            item.observed_monotonic_ns - frame.captured_monotonic_ns,
        ) + frame.clock_uncertainty_ns > state.config.book_freshness_ns
    ):
        reason = "book_stale"
    if reason is not None:
        return LivePaperPositionMark(
            frame.raw_parent_receipt_sha256,
            frame.global_sequence,
            position.ticker,
            position.quantity,
            zero,
            zero,
            zero,
            zero,
            None,
            False,
            reason,
        )
    remaining = position.quantity
    pieces: list[tuple[Decimal, Decimal]] = []
    for level in frame.market_for(position.player_side).yes_bids:
        if not zero < level.price < Decimal("1"):
            continue
        consumed = sum(
            (
                entry.quantity
                for entry in portfolio.consumed_depth
                if entry.receipt_sha256 == frame.raw_parent_receipt_sha256
                and entry.ticker == position.ticker
                and entry.kind.value == "SELL"
                and entry.price == level.price
            ),
            zero,
        )
        available = (level.quantity - consumed).to_integral_value(rounding="ROUND_FLOOR")
        quantity = min(remaining, max(zero, available))
        if quantity > zero:
            pieces.append((level.price, quantity))
            remaining -= quantity
        if remaining == zero:
            break
    priced = position.quantity - remaining
    gross = sum((price * quantity for price, quantity in pieces), zero)
    fees = sum(
        (
            fee_for_fill(
                portfolio.fee_schedule,
                series_ticker=portfolio.fee_series_ticker,
                price=price,
                quantity=quantity,
                role=LiquidityRole.TAKER,
                side=FillSide.SELL,
                fill_wall_ns=item.observed_wall_ns,
            )
            for price, quantity in pieces
        ),
        zero,
    )
    net = gross - fees
    fully_priced = remaining == zero
    pnl = net - position.debit - position.entry_fees if fully_priced else None
    return LivePaperPositionMark(
        frame.raw_parent_receipt_sha256,
        frame.global_sequence,
        position.ticker,
        position.quantity,
        priced,
        gross,
        fees,
        net,
        pnl,
        fully_priced,
        None if fully_priced else "bid_depth_insufficient",
    )


def _score_gate_reason(state: LivePaperSessionState, item: LivePaperL2Input) -> str | None:
    if state.score_coordinator.quarantined or not state.score_actionable:
        return "score_untrusted"
    score_wall = state.last_score_wall_ns
    score_monotonic = state.last_score_monotonic_ns
    score_uncertainty = state.last_score_clock_uncertainty_ns
    if score_wall is None or score_monotonic is None or score_uncertainty is None:
        _fail("score_clock")
    if (
        item.observed_wall_ns < score_wall
        or item.observed_monotonic_ns < score_monotonic
        or max(
            item.observed_wall_ns - score_wall,
            item.observed_monotonic_ns - score_monotonic,
        ) + score_uncertainty > state.config.score_freshness_ns
    ):
        return "score_stale"
    if (
        item.frame.captured_monotonic_ns <= score_monotonic
        or item.frame.captured_wall_ns < score_wall
    ):
        return "book_precedes_score"
    return None


def _reduce_l2(state: LivePaperSessionState, item: LivePaperL2Input) -> tuple[LivePaperSessionState, tuple[LivePaperRecord, ...]]:
    if item.frame.binding != state.config.market_binding:
        _fail("market_binding")
    records: list[LivePaperRecord] = []
    state = _emit(state, records, LivePaperRecordKind.RAW_L2_RECEIPT, item)
    blocked_buy = False
    pending = state.portfolio.pending_action
    if pending is not None and pending.kind is PaperActionKind.BUY:
        reason = _score_gate_reason(state, item)
        if reason is not None:
            blocked_buy = True
            state = replace(
                state,
                portfolio=replace(state.portfolio, pending_action=None),
            )
            state = _emit(
                state,
                records,
                LivePaperRecordKind.REJECTION,
                _LivePaperRejection("paper_action", reason, pending),
            )
    before = state.portfolio
    portfolio, events = reduce_paper_book(
        before,
        item.frame,
        observed_wall_ns=item.observed_wall_ns,
        observed_monotonic_ns=item.observed_monotonic_ns,
    )
    state = replace(state, portfolio=portfolio)
    for event in events:
        state = _emit(state, records, LivePaperRecordKind.FILL, event)
    if not _same_action_identity(before.pending_action, portfolio.pending_action) and portfolio.pending_action is not None:
        state = _emit(state, records, LivePaperRecordKind.ACTION, portfolio.pending_action)
    if portfolio.position is not None:
        state = _emit(
            state,
            records,
            LivePaperRecordKind.MARK,
            _position_mark(state, item, portfolio),
        )
    if events or portfolio.pending_action is not None or portfolio.position is not None:
        return state, tuple(records)
    if blocked_buy:
        return state, tuple(records)
    reason = _score_gate_reason(state, item)
    if reason is not None:
        state = _emit(state, records, LivePaperRecordKind.REJECTION, _LivePaperRejection("entry", reason, item.frame.raw_parent_receipt_sha256))
        return state, tuple(records)
    if state.latest_forecast is None:
        state = _emit(state, records, LivePaperRecordKind.REJECTION, _LivePaperRejection("entry", "forecast_unavailable", item.frame.raw_parent_receipt_sha256))
        return state, tuple(records)
    decision: PaperDecision = evaluate_live_paper_entry(
        state.latest_forecast,
        item.frame,
        portfolio,
        decision_wall_ns=item.observed_wall_ns,
        decision_monotonic_ns=item.observed_monotonic_ns,
    )
    state = replace(state, portfolio=decision.state)
    if decision.reason is PaperDecisionReason.ACCEPTED:
        state = _emit(state, records, LivePaperRecordKind.ACTION, decision.action)
    else:
        state = _emit(state, records, LivePaperRecordKind.REJECTION, _LivePaperRejection("entry", decision.reason.value, decision))
    return state, tuple(records)


def _reduce_heartbeat(state: LivePaperSessionState, item: LivePaperHeartbeatInput) -> tuple[LivePaperSessionState, tuple[LivePaperRecord, ...]]:
    if item.observed_monotonic_ns < state.next_heartbeat_monotonic_ns:
        return state, ()
    intervals = (
        item.observed_monotonic_ns - state.next_heartbeat_monotonic_ns
    ) // state.config.heartbeat_interval_ns + 1
    state = replace(
        state,
        next_heartbeat_wall_ns=state.next_heartbeat_wall_ns + intervals * state.config.heartbeat_interval_ns,
        next_heartbeat_monotonic_ns=state.next_heartbeat_monotonic_ns + intervals * state.config.heartbeat_interval_ns,
    )
    next_state, record = _make_record(state, LivePaperRecordKind.HEARTBEAT, item)
    return next_state, (record,)


def reduce_live_paper_input(state: LivePaperSessionState, item: LivePaperInput) -> tuple[LivePaperSessionState, tuple[LivePaperRecord, ...]]:
    """Reduce exactly one immutable causal input and return its durable rows."""
    if type(state) is not LivePaperSessionState:
        _fail("state")
    if state.terminal:
        _fail("post_terminal")
    if type(item) is LivePaperScoreBatchInput:
        return _reduce_score(state, item)
    if type(item) is LivePaperL2Input:
        return _reduce_l2(state, item)
    if type(item) is LivePaperHeartbeatInput:
        return _reduce_heartbeat(state, item)
    if type(item) is LivePaperTerminalInput:
        terminal_state = replace(state, terminal=True)
        terminal_state, record = _make_record(terminal_state, LivePaperRecordKind.TERMINAL, item)
        return terminal_state, (record,)
    _fail("input_type")


def _record_envelope(record: LivePaperRecord) -> dict[str, object]:
    return {
        "schema": record.schema,
        "version": record.version,
        "record_ordinal": record.record_ordinal,
        "previous_record_sha256": record.previous_record_sha256,
        "kind": record.kind.value,
        "payload": _project(record.payload),
        "payload_sha256": record.payload_sha256,
        "record_sha256": record.record_sha256,
    }


def encode_live_paper_records(records: tuple[LivePaperRecord, ...]) -> bytes:
    if type(records) is not tuple or any(type(record) is not LivePaperRecord for record in records):
        _fail("records")
    previous = _ZERO_SHA256
    for ordinal, record in enumerate(records, 1):
        if record.record_ordinal != ordinal or record.previous_record_sha256 != previous:
            _fail("record_chain")
        previous = record.record_sha256
    return b"".join(_canonical_json(_record_envelope(record)) + b"\n" for record in records)


def _decode_records(raw: bytes) -> tuple[LivePaperRecord, ...]:
    if type(raw) is not bytes:
        _fail("records")
    if len(raw) > _MAX_LOG_BYTES:
        _fail("log_too_large")
    if not raw or not raw.endswith(b"\n"):
        _fail("truncated_log")
    if raw.count(b"\n") > _MAX_RECORDS:
        _fail("record_count")
    records: list[LivePaperRecord] = []
    previous = _ZERO_SHA256
    for ordinal, line in enumerate(raw.splitlines(), 1):
        if len(line) > _MAX_RECORD_LINE_BYTES:
            _fail("record_too_large")
        decoded = _parse_json(line, maximum_bytes=_MAX_RECORD_LINE_BYTES)
        if type(decoded) is not dict or set(decoded) != {
            "schema", "version", "record_ordinal", "previous_record_sha256", "kind",
            "payload", "payload_sha256", "record_sha256",
        }:
            _fail("record_fields")
        if _canonical_json(decoded) != line:
            _fail("noncanonical_json")
        try:
            kind = LivePaperRecordKind(decoded["kind"])
        except (TypeError, ValueError) as error:
            raise LivePaperSessionError("record_kind") from error
        payload = _unproject(decoded["payload"])
        if type(payload) is not _LivePaperPayload:
            _fail("payload_type")
        record = LivePaperRecord(
            decoded["schema"], decoded["version"], decoded["record_ordinal"],
            decoded["previous_record_sha256"], kind, payload,
            decoded["payload_sha256"], decoded["record_sha256"],
        )
        if record.record_ordinal != ordinal or record.previous_record_sha256 != previous:
            _fail("record_chain")
        if (ordinal == 1) != (payload.config is not None):
            _fail("config_position")
        previous = record.record_sha256
        records.append(record)
    return tuple(records)


def _input_from_record(record: LivePaperRecord) -> LivePaperInput:
    body = record.payload.body
    expected: dict[LivePaperRecordKind, type[object]] = {
        LivePaperRecordKind.RAW_SCORE_RECEIPT: LivePaperScoreBatchInput,
        LivePaperRecordKind.RAW_L2_RECEIPT: LivePaperL2Input,
        LivePaperRecordKind.HEARTBEAT: LivePaperHeartbeatInput,
        LivePaperRecordKind.TERMINAL: LivePaperTerminalInput,
    }
    data_type = expected.get(record.kind)
    if data_type is None or type(body) is not data_type:
        _fail("causal_input")
    return body  # type: ignore[return-value]


def _replay_from(state: LivePaperSessionState, records: tuple[LivePaperRecord, ...], start: int, stop: int | None = None) -> LivePaperSessionState:
    index = start
    limit = len(records) if stop is None else stop
    if type(limit) is not int or limit < start or limit > len(records):
        _fail("replay_range")
    while index < limit:
        item = _input_from_record(records[index])
        next_state, expected = reduce_live_paper_input(state, item)
        if not expected or index + len(expected) > limit or records[index:index + len(expected)] != expected:
            _fail("replay_mismatch")
        state = next_state
        index += len(expected)
    return state


def replay_live_paper_records(raw: bytes, *, require_terminal: bool = False) -> LivePaperReplayResult:
    """Authenticate the complete chain and recompute every derived record."""
    if type(require_terminal) is not bool:
        _fail("require_terminal")
    records = _decode_records(raw)
    config = records[0].payload.config
    if type(config) is not LivePaperSessionConfig:
        _fail("config")
    state = _replay_from(open_live_paper_session(config), records, 0)
    if require_terminal and not state.terminal:
        _fail("terminal_missing")
    return LivePaperReplayResult(state, records, False)


def _checkpoint_digest(payload_projection: object, payload_sha256: str) -> str:
    core = {
        "schema": _CHECKPOINT_SCHEMA,
        "version": _VERSION,
        "payload": payload_projection,
        "payload_sha256": payload_sha256,
    }
    return _digest(b"INCI-LIVE-PAPER-CHECKPOINT-V1\0" + _canonical_json(core))


def encode_live_paper_checkpoint(state: LivePaperSessionState) -> bytes:
    if type(state) is not LivePaperSessionState:
        _fail("state")
    payload = _project(state)
    payload_sha = _digest(_canonical_json(payload))
    envelope = {
        "schema": _CHECKPOINT_SCHEMA,
        "version": _VERSION,
        "payload": payload,
        "payload_sha256": payload_sha,
        "checkpoint_sha256": _checkpoint_digest(payload, payload_sha),
    }
    return _canonical_json(envelope)


def decode_live_paper_checkpoint(raw: bytes) -> LivePaperSessionState:
    if type(raw) is not bytes or not raw:
        _fail("checkpoint")
    if len(raw) > _MAX_CHECKPOINT_BYTES:
        _fail("checkpoint_too_large")
    decoded = _parse_json(raw, maximum_bytes=_MAX_CHECKPOINT_BYTES)
    if type(decoded) is not dict or set(decoded) != {"schema", "version", "payload", "payload_sha256", "checkpoint_sha256"}:
        _fail("checkpoint_fields")
    if (
        _canonical_json(decoded) != raw
        or decoded["schema"] != _CHECKPOINT_SCHEMA
        or type(decoded["version"]) is not int
        or decoded["version"] != _VERSION
    ):
        _fail("checkpoint_canonical")
    payload_projection = decoded["payload"]
    payload_sha = _digest(_canonical_json(payload_projection))
    if decoded["payload_sha256"] != payload_sha or decoded["checkpoint_sha256"] != _checkpoint_digest(payload_projection, payload_sha):
        _fail("checkpoint_sha256")
    state = _unproject(payload_projection)
    if type(state) is not LivePaperSessionState:
        _fail("checkpoint_state")
    return state


def load_live_paper_checkpoint(checkpoint_raw: bytes | None, records_raw: bytes, *, require_terminal: bool = False) -> LivePaperReplayResult:
    """Use an authenticated checkpoint when possible, otherwise replay the log."""
    records = _decode_records(records_raw)
    checkpoint: LivePaperSessionState | None = None
    if checkpoint_raw is not None:
        try:
            checkpoint = decode_live_paper_checkpoint(checkpoint_raw)
        except LivePaperSessionError:
            checkpoint = None
    if checkpoint is not None and checkpoint.record_count <= len(records):
        prefix_head = _ZERO_SHA256 if checkpoint.record_count == 0 else records[checkpoint.record_count - 1].record_sha256
        first_config = records[0].payload.config
        if checkpoint.record_head_sha256 == prefix_head and checkpoint.config == first_config:
            try:
                if type(first_config) is not LivePaperSessionConfig:
                    _fail("config")
                verified_prefix = _replay_from(
                    open_live_paper_session(first_config),
                    records,
                    0,
                    checkpoint.record_count,
                )
                if verified_prefix != checkpoint:
                    _fail("checkpoint_log_mismatch")
                state = _replay_from(checkpoint, records, checkpoint.record_count)
                if require_terminal and not state.terminal:
                    _fail("terminal_missing")
                return LivePaperReplayResult(state, records, True)
            except LivePaperSessionError:
                pass
    replay = replay_live_paper_records(records_raw, require_terminal=require_terminal)
    return LivePaperReplayResult(replay.state, replay.records, False)
