from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from enum import Enum
from hashlib import sha256
import json
from re import ASCII as RE_ASCII
from re import Pattern
from re import compile as pattern_compile
from typing import Final, Literal
from uuid import UUID

from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.codec import canonical_record_sha256
from tennis_v1.events import (
    PersistedEvent,
    ProvenanceState,
    RecordKind,
    SessionManifest,
    SourceKind,
)
from tennis_v1.replay_core import ReplayMismatch, ReplayResult
from tennis_v1.session import (
    canonical_session_manifest_bytes,
    session_manifest_sha256,
)
from tennis_v1.state import (
    FoundationState,
    canonical_state_bytes,
)
from tennis_v1.wal import ScanIssue


class ExpertContractError(ValueError):
    pass


class PlayerSide(str, Enum):
    HOME = "home"
    AWAY = "away"


class ContractSide(str, Enum):
    YES = "yes"
    NO = "no"


class MatchStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    SUSPENDED = "suspended"
    ENDED = "ended"
    CANCELLED = "cancelled"


class MatchFormat(str, Enum):
    STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS = (
        "standard_advantage_bo3_tb7_all_sets"
    )
    STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS = (
        "standard_advantage_bo5_tb7_all_sets"
    )
    UNSUPPORTED = "unsupported"


class TerminationKind(str, Enum):
    NONE = "none"
    NATURAL = "natural"
    WALKOVER = "walkover"
    RETIREMENT = "retirement"
    CANCELLATION = "cancellation"


class ProviderLifecycleKind(str, Enum):
    START = "start"
    SUSPEND = "suspend"
    RESUME = "resume"
    WALKOVER = "walkover"
    RETIREMENT = "retirement"
    CANCEL = "cancel"
    NATURAL_END_CONFIRMATION = "natural_end_confirmation"


class ScoreValue(str, Enum):
    LOVE = "love"
    FIFTEEN = "fifteen"
    THIRTY = "thirty"
    FORTY = "forty"
    ADVANTAGE = "advantage"


class TransitionDisposition(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    BLOCKED = "blocked"


class TennisTransitionReason(str, Enum):
    POINT_APPLIED = "point_applied"
    LIFECYCLE_APPLIED = "lifecycle_applied"
    NATURAL_END_CONFIRMED = "natural_end_confirmed"
    CORRECTION_APPLIED = "correction_applied"
    EXACT_DUPLICATE = "exact_duplicate"

    CORRECTION_REQUIRED = "correction_required"
    PROVIDER_EVENT_GAP = "provider_event_gap"
    PROVIDER_EVENT_STALE = "provider_event_stale"
    PROVIDER_EVENT_CONFLICT = "provider_event_conflict"
    CORRECTION_EPOCH_STALE = "correction_epoch_stale"
    CORRECTION_EPOCH_AHEAD = "correction_epoch_ahead"

    IDENTITY_MISMATCH = "identity_mismatch"
    FORMAT_MISMATCH = "format_mismatch"
    SOURCE_LINEAGE_MISMATCH = "source_lineage_mismatch"
    REVISION_DOMAIN_MISMATCH = "revision_domain_mismatch"
    RECEIVE_TIME_REGRESSION = "receive_time_regression"

    SERVER_MISMATCH = "server_mismatch"
    POINT_WHILE_NOT_LIVE = "point_while_not_live"
    TERMINAL_ABSORBING = "terminal_absorbing"
    ILLEGAL_LIFECYCLE_TRANSITION = "illegal_lifecycle_transition"

    UNSUPPORTED_FORMAT = "unsupported_format"
    SNAPSHOT_INVALID = "snapshot_invalid"
    CORRECTION_EPOCH_NOT_NEWER = "correction_epoch_not_newer"
    CORRECTION_SNAPSHOT_INVALID = "correction_snapshot_invalid"


class MarketStatus(str, Enum):
    PREOPEN = "preopen"
    OPEN = "open"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class BookEventKind(str, Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"
    LIFECYCLE = "lifecycle"


class SyncInputKind(str, Enum):
    TENNIS_ORIGIN = "tennis_origin"
    TENNIS_TRANSITION = "tennis_transition"
    BOOK_TRANSITION = "book_transition"
    BOOK_RESNAPSHOT_REQUIRED = "book_resnapshot_required"
    CLOCK = "clock"


class DecisionAction(str, Enum):
    ABSTAIN = "abstain"
    PAPER_BUY = "paper_buy"
    PAPER_SELL = "paper_sell"


class SyncReason(str, Enum):
    TRUSTED_SYNCHRONIZED = "trusted_synchronized"
    DUPLICATE_STATE_SUPPRESSED = "duplicate_state_suppressed"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    SCORE_STALE = "score_stale"
    BOOK_STALE = "book_stale"
    LIFECYCLE_STALE = "lifecycle_stale"
    BOOK_UNTRUSTED = "book_untrusted"
    BOOK_NOT_EXECUTABLE = "book_not_executable"
    MARKET_NOT_OPEN = "market_not_open"
    MARKET_SUSPENDED = "market_suspended"
    MARKET_ENDED = "market_ended"
    UNKNOWN_SERVER = "unknown_server"
    BINDING_AMBIGUOUS = "binding_ambiguous"
    BINDING_DRIFT = "binding_drift"
    SEQUENCE_GAP = "sequence_gap"
    UNEXPLAINED_BOOK_MOVE = "unexplained_book_move"
    MATCH_NOT_STARTED = "match_not_started"
    MATCH_SUSPENDED = "match_suspended"
    MATCH_ENDED = "match_ended"
    CORRECTION_PENDING = "correction_pending"
    CONTRACT_MISMATCH = "contract_mismatch"
    CLOCK_UNCERTAIN = "clock_uncertain"
    CLOSE_HORIZON_INSUFFICIENT = "close_horizon_insufficient"


class DecisionReason(str, Enum):
    CONSERVATIVE_VALUE_POSITIVE = "conservative_value_positive"
    BASELINE_SIGNAL_POSITIVE = "baseline_signal_positive"
    SIMPLE_SCORE_VALUE_POSITIVE = "simple_score_value_positive"

    NO_TRADE_BASELINE = "no_trade_baseline"
    SIGNAL_NOT_TRIGGERED = "signal_not_triggered"
    SYNC_UNTRUSTED = "sync_untrusted"
    MODEL_UNSUPPORTED = "model_unsupported"
    MODEL_UNCERTAIN = "model_uncertain"
    MODEL_OUT_OF_DISTRIBUTION = "model_out_of_distribution"
    POLICY_UNSEALED = "policy_unsealed"
    EDGE_BELOW_COST = "edge_below_cost"
    PRICE_OUTSIDE_LIMIT = "price_outside_limit"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    NOT_SELECTED = "not_selected"
    PENDING_ORDER = "pending_order"
    RISK_CONFLICT = "risk_conflict"
    POST_STOP_COOLDOWN = "post_stop_cooldown"
    SIGNAL_NOT_RESET = "signal_not_reset"
    ATTEMPT_LIMIT = "attempt_limit"
    MATCH_LOSS_LIMIT = "match_loss_limit"
    CONSECUTIVE_LOSS_LIMIT = "consecutive_loss_limit"
    SESSION_LOSS_LIMIT = "session_loss_limit"
    PORTFOLIO_CAPACITY = "portfolio_capacity"
    PORTFOLIO_HALTED = "portfolio_halted"
    DUE_RECHECK_FAILED = "due_recheck_failed"

    FAIR_VALUE_CONVERGED = "fair_value_converged"
    THESIS_INVALIDATED = "thesis_invalidated"
    POLICY_VALUE_NEGATIVE = "policy_value_negative"
    HOLDING_HORIZON_REACHED = "holding_horizon_reached"
    RISK_EXIT_REQUIRED = "risk_exit_required"
    MARKET_SUSPENDED_EXIT = "market_suspended_exit"
    SETTLEMENT_EXIT = "settlement_exit"
    SHUTDOWN_EXIT = "shutdown_exit"


class ExpertEventKindV1(str, Enum):
    SYNCHRONIZATION_APPLIED = "synchronization_applied"
    OBSERVATION_IGNORED = "observation_ignored"
    OBSERVATION_REJECTED = "observation_rejected"


class ExpertReplayDiagnosticRoleV1(str, Enum):
    PHASE1_MARKER = "phase1_marker"
    PHASE1_WAL = "phase1_wal"
    EXPERT_MARKER = "expert_marker"
    EXPERT_JOURNAL = "expert_journal"


class ExpertReplayDiagnosticIssueV1(str, Enum):
    ENTRY_MISSING = "entry_missing"
    ENTRY_NOT_REGULAR = "entry_not_regular"
    ENTRY_IDENTITY_INVALID = "entry_identity_invalid"
    ENTRY_REPLACED = "entry_replaced"
    PREFIX_TRUNCATED = "prefix_truncated"
    HEADER_INVALID = "header_invalid"
    SESSION_START_INVALID = "session_start_invalid"
    MANIFEST_FRAME_INVALID = "manifest_frame_invalid"
    SCAN_INVALID = "scan_invalid"


class ExpertIgnoreReasonV1(str, Enum):
    NORMALIZER_NOT_REGISTERED = "normalizer_not_registered"
    EVENT_NOT_RELEVANT = "event_not_relevant"


class ExpertRejectReasonV1(str, Enum):
    PARENT_CONTRACT_INVALID = "parent_contract_invalid"
    NORMALIZER_SCHEMA_UNKNOWN = "normalizer_schema_unknown"
    NORMALIZER_PAYLOAD_INVALID = "normalizer_payload_invalid"
    NORMALIZER_CONTRACT_VIOLATION = "normalizer_contract_violation"
    NORMALIZER_EXCEPTION = "normalizer_exception"
    GROUP_CAPACITY_EXCEEDED = "group_capacity_exceeded"
    PERSISTENCE_CAPACITY_EXCEEDED = "persistence_capacity_exceeded"
    SYNCHRONIZATION_SESSION_DRIFT = "synchronization_session_drift"
    REDUCER_EXCEPTION = "reducer_exception"
    PRIOR_OUTCOME_HALTED = "prior_outcome_halted"
    PRIOR_GROUP_HALTED = "prior_group_halted"
    STATIC_SESSION_HALT = "static_session_halt"


class ExpertTerminalReasonV1(str, Enum):
    OPERATOR_STOP = "operator_stop"
    SESSION_END = "session_end"
    EXPERT_HALT = "expert_halt"


class ExpertJournalFrameKindV1(str, Enum):
    MANIFEST = "manifest"
    PARENT_GROUP = "parent_group"
    TERMINAL = "terminal"


class ExpertJournalScanIssueV1(str, Enum):
    MISSING_TERMINAL = "missing_terminal"
    HALTED_TERMINAL = "halted_terminal"
    TORN_TAIL = "torn_tail"
    CORRUPT_TAIL = "corrupt_tail"
    DURABLE_UNACKNOWLEDGED = "durable_unacknowledged"
    EVIDENCE_IDENTITY_LOST = "evidence_identity_lost"


class ExpertReplayMismatchV1(str, Enum):
    EVIDENCE_CONTEXT_MISMATCH = "evidence_context_mismatch"
    EVIDENCE_REPLAY_NOT_EXACT = "evidence_replay_not_exact"
    EVIDENCE_TERMINAL_NOT_CLEAN = "evidence_terminal_not_clean"
    EVIDENCE_SESSION_MISMATCH = "evidence_session_mismatch"
    EVIDENCE_MANIFEST_MISMATCH = "evidence_manifest_mismatch"
    RETENTION_AUTHORIZATION_MISMATCH = "retention_authorization_mismatch"
    RETENTION_DEADLINE_REACHED = "retention_deadline_reached"
    EVIDENCE_IDENTITY_MISMATCH = "evidence_identity_mismatch"
    CURRENT_ENVIRONMENT_MISMATCH = "current_environment_mismatch"
    RETENTION_BINDING_MISMATCH = "retention_binding_mismatch"
    COMPANION_SCAN_INVALID = "companion_scan_invalid"
    COMPANION_MANIFEST_MISMATCH = "companion_manifest_mismatch"
    EXPERT_SEQUENCE_MISMATCH = "expert_sequence_mismatch"
    PARENT_MISSING = "parent_missing"
    PARENT_EXTRA = "parent_extra"
    PARENT_ORDER_MISMATCH = "parent_order_mismatch"
    PARENT_KIND_MISMATCH = "parent_kind_mismatch"
    PARENT_DIGEST_MISMATCH = "parent_digest_mismatch"
    PARENT_GROUP_SHAPE_MISMATCH = "parent_group_shape_mismatch"
    PRIOR_RECORD_CHAIN_MISMATCH = "prior_record_chain_mismatch"
    PRIOR_STATE_MISMATCH = "prior_state_mismatch"
    EVENT_SCHEMA_UNPINNED = "event_schema_unpinned"
    RECORD_DIGEST_MISMATCH = "record_digest_mismatch"
    PAYLOAD_DESCRIPTOR_MISMATCH = "payload_descriptor_mismatch"
    PAYLOAD_BYTES_MISMATCH = "payload_bytes_mismatch"
    NORMALIZED_OBSERVATION_MISMATCH = "normalized_observation_mismatch"
    REDUCTION_MISMATCH = "reduction_mismatch"
    POST_STATE_MISMATCH = "post_state_mismatch"
    TRACE_MISMATCH = "trace_mismatch"
    TERMINAL_MISSING = "terminal_missing"
    TERMINAL_REASON_MISMATCH = "terminal_reason_mismatch"
    TERMINAL_COUNT_MISMATCH = "terminal_count_mismatch"
    TERMINAL_PROVENANCE_MISMATCH = "terminal_provenance_mismatch"
    TERMINAL_STATE_MISMATCH = "terminal_state_mismatch"
    TERMINAL_TRACE_MISMATCH = "terminal_trace_mismatch"


class TennisTransitionError(ValueError):
    def __init__(self, reason: TennisTransitionReason) -> None:
        if type(reason) is not TennisTransitionReason:
            raise TypeError("reason")
        if reason not in {
            TennisTransitionReason.UNSUPPORTED_FORMAT,
            TennisTransitionReason.SNAPSHOT_INVALID,
        }:
            raise ExpertContractError("reason")
        self.reason = reason
        super().__init__(reason.value)


class TennisStateInvariantError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("tennis_state_invariant_error")


_INTEGER_BOUND: Final[int] = 10**256
_SAFE_ID_RE: Final[Pattern[str]] = pattern_compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
    RE_ASCII,
)
_TICKER_RE: Final[Pattern[str]] = pattern_compile(
    r"[A-Z0-9][A-Z0-9._-]{0,127}",
    RE_ASCII,
)
_SHA256_RE: Final[Pattern[str]] = pattern_compile(r"[0-9a-f]{64}", RE_ASCII)
_SUPPORTED_MATCH_FORMATS: Final[frozenset[MatchFormat]] = frozenset(
    {
        MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS,
        MatchFormat.STANDARD_ADVANTAGE_BO5_TB7_ALL_SETS,
    }
)
_MODEL_ABSTENTION_REASONS: Final[frozenset[DecisionReason]] = frozenset(
    {
        DecisionReason.MODEL_UNSUPPORTED,
        DecisionReason.MODEL_UNCERTAIN,
        DecisionReason.MODEL_OUT_OF_DISTRIBUTION,
    }
)
_BUY_REASONS: Final[frozenset[DecisionReason]] = frozenset(
    {
        DecisionReason.CONSERVATIVE_VALUE_POSITIVE,
        DecisionReason.BASELINE_SIGNAL_POSITIVE,
        DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE,
    }
)
_SELL_REASONS: Final[frozenset[DecisionReason]] = frozenset(
    {
        DecisionReason.FAIR_VALUE_CONVERGED,
        DecisionReason.THESIS_INVALIDATED,
        DecisionReason.POLICY_VALUE_NEGATIVE,
        DecisionReason.HOLDING_HORIZON_REACHED,
        DecisionReason.RISK_EXIT_REQUIRED,
        DecisionReason.MARKET_SUSPENDED_EXIT,
        DecisionReason.SETTLEMENT_EXIT,
        DecisionReason.SHUTDOWN_EXIT,
    }
)
_TENNIS_STATE_INSTALLED_BLOCK_REASONS: Final[
    frozenset[TennisTransitionReason]
] = frozenset(
    {
        TennisTransitionReason.PROVIDER_EVENT_GAP,
        TennisTransitionReason.PROVIDER_EVENT_STALE,
        TennisTransitionReason.PROVIDER_EVENT_CONFLICT,
        TennisTransitionReason.CORRECTION_EPOCH_STALE,
        TennisTransitionReason.CORRECTION_EPOCH_AHEAD,
        TennisTransitionReason.IDENTITY_MISMATCH,
        TennisTransitionReason.FORMAT_MISMATCH,
        TennisTransitionReason.SOURCE_LINEAGE_MISMATCH,
        TennisTransitionReason.REVISION_DOMAIN_MISMATCH,
        TennisTransitionReason.RECEIVE_TIME_REGRESSION,
        TennisTransitionReason.SERVER_MISMATCH,
        TennisTransitionReason.POINT_WHILE_NOT_LIVE,
        TennisTransitionReason.TERMINAL_ABSORBING,
        TennisTransitionReason.ILLEGAL_LIFECYCLE_TRANSITION,
        TennisTransitionReason.UNSUPPORTED_FORMAT,
        TennisTransitionReason.CORRECTION_EPOCH_NOT_NEWER,
        TennisTransitionReason.CORRECTION_SNAPSHOT_INVALID,
    }
)
_DECIMAL_CONTEXT: Final[Context] = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def _exact_self(value: object, cls: type[object]) -> None:
    if type(value) is not cls:
        raise TypeError(f"exact {cls.__name__} required")


def _exact(value: object, cls: type[object], name: str) -> None:
    if type(value) is not cls:
        raise TypeError(name)


def _integer(
    value: object,
    name: str,
    *,
    nonnegative: bool = True,
    positive: bool = False,
) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if abs(value) >= _INTEGER_BOUND:
        raise ExpertContractError(name)
    if positive and value <= 0:
        raise ExpertContractError(name)
    if nonnegative and value < 0:
        raise ExpertContractError(name)
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(name)
    return value


def _string(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    return value


def _safe_id(value: object, name: str) -> str:
    text = _string(value, name)
    if _SAFE_ID_RE.fullmatch(text) is None:
        raise ExpertContractError(name)
    if text.lower().startswith(("http:", "https:", "file:")):
        raise ExpertContractError(name)
    return text


def _ticker(value: object, name: str) -> str:
    text = _string(value, name)
    if _TICKER_RE.fullmatch(text) is None:
        raise ExpertContractError(name)
    return text


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if _SHA256_RE.fullmatch(text) is None:
        raise ExpertContractError(name)
    return text


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise ExpertContractError(name)
    return value


def _probability(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number < Decimal("0") or number > Decimal("1"):
        raise ExpertContractError(name)
    return number


def _quantity(
    value: object,
    name: str,
    *,
    positive: bool = False,
) -> Decimal:
    number = _decimal(value, name)
    if number < Decimal("0") or (positive and number == Decimal("0")):
        raise ExpertContractError(name)
    return number


def _optional_exact(value: object, cls: type[object], name: str) -> None:
    if value is not None and type(value) is not cls:
        raise TypeError(name)


def _exact_tuple(
    value: object,
    item_cls: type[object],
    name: str,
) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(name)
    for item in value:
        if type(item) is not item_cls:
            raise TypeError(name)
    return value


def _schema_version(value: object, name: str = "schema_version") -> None:
    _integer(value, name)
    if value != 1:
        raise ExpertContractError(name)


def _session_id(value: object, name: str = "session_id") -> str:
    text = _safe_id(value, name)
    try:
        parsed = UUID(text)
    except ValueError:
        raise ExpertContractError(name) from None
    if str(parsed) != text:
        raise ExpertContractError(name)
    return text


def _one_of_strings(
    value: object,
    allowed: frozenset[str],
    name: str,
) -> str:
    text = _string(value, name)
    if text not in allowed:
        raise ExpertContractError(name)
    return text


def _domain_sha256(domain: bytes, projection: object) -> str:
    return sha256(domain + canonical_expert_bytes(projection)).hexdigest()


def _self_digest(
    value: object,
    *,
    digest_field: str,
    domain: bytes,
    name: str,
) -> None:
    projected = tuple(
        getattr(value, item.name)
        for item in fields(value)
        if item.name != digest_field
    )
    expected = _domain_sha256(domain, projected)
    if getattr(value, digest_field) != expected:
        raise ExpertContractError(name)


def _compute_exact_fields_sha256(
    domain: bytes,
    field_names: tuple[str, ...],
    values: dict[str, object],
) -> str:
    if type(values) is not dict or set(values) != set(field_names):
        raise ExpertContractError("digest_projection")
    return _domain_sha256(
        domain,
        tuple(values[name] for name in field_names),
    )


def _validate_identity(
    *,
    provider_source_id: object,
    revision_domain_id: object,
    source_lineage_sha256: object,
    provider_event_id: object | None,
    provider_match_id: object,
    home_player_id: object,
    away_player_id: object,
    scheduled_start_wall_ns: object,
    match_format: object,
) -> None:
    _safe_id(provider_source_id, "provider_source_id")
    _safe_id(revision_domain_id, "revision_domain_id")
    _sha256(source_lineage_sha256, "source_lineage_sha256")
    if provider_event_id is not None:
        _safe_id(provider_event_id, "provider_event_id")
    _safe_id(provider_match_id, "provider_match_id")
    _safe_id(home_player_id, "home_player_id")
    _safe_id(away_player_id, "away_player_id")
    if home_player_id == away_player_id:
        raise ExpertContractError("player_identity")
    _integer(scheduled_start_wall_ns, "scheduled_start_wall_ns")
    _exact(match_format, MatchFormat, "match_format")


def _validate_status(
    *,
    status: object,
    termination_kind: object,
    winner: object,
    retired_side: object,
    server_for_next_point: object,
) -> None:
    _exact(status, MatchStatus, "status")
    _exact(termination_kind, TerminationKind, "termination_kind")
    _optional_exact(winner, PlayerSide, "winner")
    _optional_exact(retired_side, PlayerSide, "retired_side")
    _optional_exact(server_for_next_point, PlayerSide, "server_for_next_point")
    if status in {
        MatchStatus.SCHEDULED,
        MatchStatus.LIVE,
        MatchStatus.SUSPENDED,
    }:
        if (
            termination_kind is not TerminationKind.NONE
            or winner is not None
            or retired_side is not None
        ):
            raise ExpertContractError("status_termination")
        return
    if status is MatchStatus.ENDED:
        if termination_kind in {
            TerminationKind.NATURAL,
            TerminationKind.WALKOVER,
        }:
            if winner is None or retired_side is not None:
                raise ExpertContractError("status_termination")
            return
        if termination_kind is TerminationKind.RETIREMENT:
            if (
                winner is None
                or retired_side is None
                or winner is retired_side
            ):
                raise ExpertContractError("status_termination")
            return
        raise ExpertContractError("status_termination")
    if status is MatchStatus.CANCELLED:
        if (
            termination_kind is not TerminationKind.CANCELLATION
            or winner is not None
            or retired_side is not None
            or server_for_next_point is not None
        ):
            raise ExpertContractError("status_termination")
        return
    raise ExpertContractError("status_termination")


def _validate_score(
    *,
    completed_sets: object,
    games_home: object,
    games_away: object,
    points_home: object,
    points_away: object,
    in_tiebreak: object,
    tiebreak_points_home: object,
    tiebreak_points_away: object,
    tiebreak_first_server: object,
    server_for_next_point: object,
) -> None:
    _exact_tuple(completed_sets, SetScore, "completed_sets")
    _integer(games_home, "games_home")
    _integer(games_away, "games_away")
    _exact(points_home, ScoreValue, "points_home")
    _exact(points_away, ScoreValue, "points_away")
    _boolean(in_tiebreak, "in_tiebreak")
    _integer(tiebreak_points_home, "tiebreak_points_home")
    _integer(tiebreak_points_away, "tiebreak_points_away")
    _optional_exact(tiebreak_first_server, PlayerSide, "tiebreak_first_server")
    _optional_exact(server_for_next_point, PlayerSide, "server_for_next_point")
    if (
        points_home is ScoreValue.ADVANTAGE
        and points_away is not ScoreValue.FORTY
    ) or (
        points_away is ScoreValue.ADVANTAGE
        and points_home is not ScoreValue.FORTY
    ):
        raise ExpertContractError("advantage_score")
    if (
        points_home is ScoreValue.ADVANTAGE
        and points_away is ScoreValue.ADVANTAGE
    ):
        raise ExpertContractError("advantage_score")
    if in_tiebreak:
        if points_home is not ScoreValue.LOVE or points_away is not ScoreValue.LOVE:
            raise ExpertContractError("tiebreak_score")
    elif (
        tiebreak_points_home != 0
        or tiebreak_points_away != 0
        or tiebreak_first_server is not None
    ):
        raise ExpertContractError("tiebreak_score")


def _validate_event_times(
    *,
    source_wall_ns: object,
    source_generated_wall_ns: object,
    received_monotonic_ns: object,
    clock_uncertainty_ns: object,
) -> None:
    _integer(source_wall_ns, "source_wall_ns")
    _integer(source_generated_wall_ns, "source_generated_wall_ns")
    _integer(received_monotonic_ns, "received_monotonic_ns")
    _integer(clock_uncertainty_ns, "clock_uncertainty_ns")


@dataclass(frozen=True, slots=True)
class ArtifactPin:
    artifact_id: str
    artifact_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ArtifactPin)
        _safe_id(self.artifact_id, "artifact_id")
        _sha256(self.artifact_sha256, "artifact_sha256")


@dataclass(frozen=True, slots=True)
class PairedTimeObservation:
    wall_ns: int
    monotonic_ns: int
    clock_uncertainty_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, PairedTimeObservation)
        _integer(self.wall_ns, "wall_ns")
        _integer(self.monotonic_ns, "monotonic_ns")
        _integer(self.clock_uncertainty_ns, "clock_uncertainty_ns")


@dataclass(frozen=True, slots=True)
class SetScore:
    games_home: int
    games_away: int
    tiebreak_points_home: int | None
    tiebreak_points_away: int | None

    def __post_init__(self) -> None:
        _exact_self(self, SetScore)
        _integer(self.games_home, "games_home")
        _integer(self.games_away, "games_away")
        if self.tiebreak_points_home is not None:
            _integer(self.tiebreak_points_home, "tiebreak_points_home")
        if self.tiebreak_points_away is not None:
            _integer(self.tiebreak_points_away, "tiebreak_points_away")
        if (self.tiebreak_points_home is None) != (
            self.tiebreak_points_away is None
        ):
            raise ExpertContractError("tiebreak_points")


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str
    provider_event_id: str

    provider_match_id: str
    home_player_id: str
    away_player_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat

    status: MatchStatus
    termination_kind: TerminationKind
    winner: PlayerSide | None
    retired_side: PlayerSide | None

    completed_sets: tuple[SetScore, ...]
    games_home: int
    games_away: int
    points_home: ScoreValue
    points_away: ScoreValue

    in_tiebreak: bool
    tiebreak_points_home: int
    tiebreak_points_away: int
    tiebreak_first_server: PlayerSide | None
    server_for_next_point: PlayerSide | None

    correction_epoch: int
    revision: int
    source_wall_ns: int
    source_generated_wall_ns: int
    received_monotonic_ns: int
    clock_uncertainty_ns: int
    snapshot_complete: bool

    def __post_init__(self) -> None:
        _exact_self(self, ProviderSnapshot)
        _safe_id(self.provider_event_id, "provider_event_id")
        _validate_identity(
            provider_source_id=self.provider_source_id,
            revision_domain_id=self.revision_domain_id,
            source_lineage_sha256=self.source_lineage_sha256,
            provider_event_id=self.provider_event_id,
            provider_match_id=self.provider_match_id,
            home_player_id=self.home_player_id,
            away_player_id=self.away_player_id,
            scheduled_start_wall_ns=self.scheduled_start_wall_ns,
            match_format=self.match_format,
        )
        _validate_status(
            status=self.status,
            termination_kind=self.termination_kind,
            winner=self.winner,
            retired_side=self.retired_side,
            server_for_next_point=self.server_for_next_point,
        )
        _validate_score(
            completed_sets=self.completed_sets,
            games_home=self.games_home,
            games_away=self.games_away,
            points_home=self.points_home,
            points_away=self.points_away,
            in_tiebreak=self.in_tiebreak,
            tiebreak_points_home=self.tiebreak_points_home,
            tiebreak_points_away=self.tiebreak_points_away,
            tiebreak_first_server=self.tiebreak_first_server,
            server_for_next_point=self.server_for_next_point,
        )
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.revision, "revision")
        _validate_event_times(
            source_wall_ns=self.source_wall_ns,
            source_generated_wall_ns=self.source_generated_wall_ns,
            received_monotonic_ns=self.received_monotonic_ns,
            clock_uncertainty_ns=self.clock_uncertainty_ns,
        )
        _boolean(self.snapshot_complete, "snapshot_complete")


@dataclass(frozen=True, slots=True)
class ProviderPoint:
    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str
    provider_event_id: str

    provider_match_id: str
    home_player_id: str
    away_player_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat

    correction_epoch: int
    revision: int
    point_winner: PlayerSide
    server_before_point: PlayerSide

    source_wall_ns: int
    source_generated_wall_ns: int
    received_monotonic_ns: int
    clock_uncertainty_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, ProviderPoint)
        _safe_id(self.provider_event_id, "provider_event_id")
        _validate_identity(
            provider_source_id=self.provider_source_id,
            revision_domain_id=self.revision_domain_id,
            source_lineage_sha256=self.source_lineage_sha256,
            provider_event_id=self.provider_event_id,
            provider_match_id=self.provider_match_id,
            home_player_id=self.home_player_id,
            away_player_id=self.away_player_id,
            scheduled_start_wall_ns=self.scheduled_start_wall_ns,
            match_format=self.match_format,
        )
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.revision, "revision", positive=True)
        _exact(self.point_winner, PlayerSide, "point_winner")
        _exact(self.server_before_point, PlayerSide, "server_before_point")
        _validate_event_times(
            source_wall_ns=self.source_wall_ns,
            source_generated_wall_ns=self.source_generated_wall_ns,
            received_monotonic_ns=self.received_monotonic_ns,
            clock_uncertainty_ns=self.clock_uncertainty_ns,
        )


@dataclass(frozen=True, slots=True)
class ProviderLifecycle:
    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str
    provider_event_id: str

    provider_match_id: str
    home_player_id: str
    away_player_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat

    correction_epoch: int
    revision: int
    kind: ProviderLifecycleKind
    winner: PlayerSide | None
    retired_side: PlayerSide | None
    server_for_next_point: PlayerSide | None

    source_wall_ns: int
    source_generated_wall_ns: int
    received_monotonic_ns: int
    clock_uncertainty_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, ProviderLifecycle)
        _safe_id(self.provider_event_id, "provider_event_id")
        _validate_identity(
            provider_source_id=self.provider_source_id,
            revision_domain_id=self.revision_domain_id,
            source_lineage_sha256=self.source_lineage_sha256,
            provider_event_id=self.provider_event_id,
            provider_match_id=self.provider_match_id,
            home_player_id=self.home_player_id,
            away_player_id=self.away_player_id,
            scheduled_start_wall_ns=self.scheduled_start_wall_ns,
            match_format=self.match_format,
        )
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.revision, "revision", positive=True)
        _exact(self.kind, ProviderLifecycleKind, "kind")
        _optional_exact(self.winner, PlayerSide, "winner")
        _optional_exact(self.retired_side, PlayerSide, "retired_side")
        _optional_exact(
            self.server_for_next_point,
            PlayerSide,
            "server_for_next_point",
        )
        _validate_event_times(
            source_wall_ns=self.source_wall_ns,
            source_generated_wall_ns=self.source_generated_wall_ns,
            received_monotonic_ns=self.received_monotonic_ns,
            clock_uncertainty_ns=self.clock_uncertainty_ns,
        )


@dataclass(frozen=True, slots=True)
class TennisState:
    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str

    provider_match_id: str
    home_player_id: str
    away_player_id: str
    scheduled_start_wall_ns: int
    match_format: MatchFormat

    status: MatchStatus
    termination_kind: TerminationKind
    winner: PlayerSide | None
    retired_side: PlayerSide | None

    completed_sets: tuple[SetScore, ...]
    games_home: int
    games_away: int
    points_home: ScoreValue
    points_away: ScoreValue

    in_tiebreak: bool
    tiebreak_points_home: int
    tiebreak_points_away: int
    tiebreak_first_server: PlayerSide | None
    server_for_next_point: PlayerSide | None

    correction_epoch: int
    revision: int
    snapshot_complete: bool
    last_provider_event_id: str
    last_event_semantic_sha256: str
    correction_lineage_sha256: str

    last_source_wall_ns: int
    last_source_generated_wall_ns: int
    last_received_monotonic_ns: int
    last_clock_uncertainty_ns: int

    block_reason: TennisTransitionReason | None
    expected_revision: int | None
    observed_revision: int | None
    blocked_event_semantic_sha256: str | None
    blocked_received_monotonic_ns: int | None

    def __post_init__(self) -> None:
        _exact_self(self, TennisState)
        _validate_identity(
            provider_source_id=self.provider_source_id,
            revision_domain_id=self.revision_domain_id,
            source_lineage_sha256=self.source_lineage_sha256,
            provider_event_id=None,
            provider_match_id=self.provider_match_id,
            home_player_id=self.home_player_id,
            away_player_id=self.away_player_id,
            scheduled_start_wall_ns=self.scheduled_start_wall_ns,
            match_format=self.match_format,
        )
        if self.match_format not in _SUPPORTED_MATCH_FORMATS:
            raise ExpertContractError("match_format")
        _validate_status(
            status=self.status,
            termination_kind=self.termination_kind,
            winner=self.winner,
            retired_side=self.retired_side,
            server_for_next_point=self.server_for_next_point,
        )
        _validate_score(
            completed_sets=self.completed_sets,
            games_home=self.games_home,
            games_away=self.games_away,
            points_home=self.points_home,
            points_away=self.points_away,
            in_tiebreak=self.in_tiebreak,
            tiebreak_points_home=self.tiebreak_points_home,
            tiebreak_points_away=self.tiebreak_points_away,
            tiebreak_first_server=self.tiebreak_first_server,
            server_for_next_point=self.server_for_next_point,
        )
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.revision, "revision")
        if _boolean(self.snapshot_complete, "snapshot_complete") is not True:
            raise ExpertContractError("snapshot_complete")
        _safe_id(self.last_provider_event_id, "last_provider_event_id")
        _sha256(self.last_event_semantic_sha256, "last_event_semantic_sha256")
        _sha256(self.correction_lineage_sha256, "correction_lineage_sha256")
        _integer(self.last_source_wall_ns, "last_source_wall_ns")
        _integer(
            self.last_source_generated_wall_ns,
            "last_source_generated_wall_ns",
        )
        _integer(
            self.last_received_monotonic_ns,
            "last_received_monotonic_ns",
        )
        _integer(self.last_clock_uncertainty_ns, "last_clock_uncertainty_ns")
        _optional_exact(
            self.block_reason,
            TennisTransitionReason,
            "block_reason",
        )
        for value, name in (
            (self.expected_revision, "expected_revision"),
            (self.observed_revision, "observed_revision"),
            (
                self.blocked_received_monotonic_ns,
                "blocked_received_monotonic_ns",
            ),
        ):
            if value is not None:
                _integer(value, name)
        if self.blocked_event_semantic_sha256 is not None:
            _sha256(
                self.blocked_event_semantic_sha256,
                "blocked_event_semantic_sha256",
            )
        metadata = (
            self.expected_revision,
            self.observed_revision,
            self.blocked_event_semantic_sha256,
            self.blocked_received_monotonic_ns,
        )
        if self.block_reason is None:
            if any(value is not None for value in metadata):
                raise ExpertContractError("block_metadata")
            return
        if self.block_reason not in _TENNIS_STATE_INSTALLED_BLOCK_REASONS:
            raise ExpertContractError("block_reason")
        if (
            self.blocked_event_semantic_sha256 is None
            or self.blocked_received_monotonic_ns is None
        ):
            raise ExpertContractError("block_metadata")
        if self.block_reason is TennisTransitionReason.PROVIDER_EVENT_GAP:
            if self.expected_revision is None or self.observed_revision is None:
                raise ExpertContractError("block_metadata")
        elif self.expected_revision is not None or self.observed_revision is not None:
            raise ExpertContractError("block_metadata")


@dataclass(frozen=True, slots=True)
class TennisTransitionResult:
    state: TennisState
    disposition: TransitionDisposition
    reason: TennisTransitionReason
    event_semantic_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, TennisTransitionResult)
        _exact(self.state, TennisState, "state")
        _exact(self.disposition, TransitionDisposition, "disposition")
        _exact(self.reason, TennisTransitionReason, "reason")
        _sha256(self.event_semantic_sha256, "event_semantic_sha256")


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _exact_self(self, BookLevel)
        _probability(self.price, "price")
        _quantity(self.quantity, "quantity", positive=True)


def _validate_ladder(value: object, name: str) -> tuple[BookLevel, ...]:
    ladder = _exact_tuple(value, BookLevel, name)
    previous: Decimal | None = None
    for level in ladder:
        assert type(level) is BookLevel
        if previous is not None and level.price >= previous:
            raise ExpertContractError(name)
        previous = level.price
    return ladder  # type: ignore[return-value]


def _validate_book_ladders(
    yes_bids: object,
    no_bids: object,
) -> tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]:
    yes = _validate_ladder(yes_bids, "yes_bids")
    no = _validate_ladder(no_bids, "no_bids")
    if yes and no:
        with localcontext(_DECIMAL_CONTEXT):
            if yes[0].price + no[0].price > Decimal("1"):
                raise ExpertContractError("crossed_book")
    return yes, no


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    ticker: str
    connection_epoch: int
    sequence: int
    market_status: MarketStatus
    scheduled_close_wall_ns: int
    source_wall_ns: int
    observed_monotonic_ns: int
    clock_uncertainty_ns: int
    yes_bids: tuple[BookLevel, ...]
    no_bids: tuple[BookLevel, ...]

    def __post_init__(self) -> None:
        _exact_self(self, BookSnapshot)
        _ticker(self.ticker, "ticker")
        _integer(self.connection_epoch, "connection_epoch")
        _integer(self.sequence, "sequence", positive=True)
        _exact(self.market_status, MarketStatus, "market_status")
        _integer(self.scheduled_close_wall_ns, "scheduled_close_wall_ns")
        _integer(self.source_wall_ns, "source_wall_ns")
        _integer(self.observed_monotonic_ns, "observed_monotonic_ns")
        _integer(self.clock_uncertainty_ns, "clock_uncertainty_ns")
        _validate_book_ladders(self.yes_bids, self.no_bids)


@dataclass(frozen=True, slots=True)
class BookDelta:
    ticker: str
    connection_epoch: int
    sequence: int
    source_wall_ns: int
    observed_monotonic_ns: int
    clock_uncertainty_ns: int
    contract_side: ContractSide
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _exact_self(self, BookDelta)
        _ticker(self.ticker, "ticker")
        _integer(self.connection_epoch, "connection_epoch")
        _integer(self.sequence, "sequence", positive=True)
        _integer(self.source_wall_ns, "source_wall_ns")
        _integer(self.observed_monotonic_ns, "observed_monotonic_ns")
        _integer(self.clock_uncertainty_ns, "clock_uncertainty_ns")
        _exact(self.contract_side, ContractSide, "contract_side")
        _probability(self.price, "price")
        _quantity(self.quantity, "quantity")


@dataclass(frozen=True, slots=True)
class MarketLifecycle:
    ticker: str
    connection_epoch: int
    market_status: MarketStatus
    scheduled_close_wall_ns: int
    source_wall_ns: int
    observed_monotonic_ns: int
    clock_uncertainty_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, MarketLifecycle)
        _ticker(self.ticker, "ticker")
        _integer(self.connection_epoch, "connection_epoch")
        _exact(self.market_status, MarketStatus, "market_status")
        _integer(self.scheduled_close_wall_ns, "scheduled_close_wall_ns")
        _integer(self.source_wall_ns, "source_wall_ns")
        _integer(self.observed_monotonic_ns, "observed_monotonic_ns")
        _integer(self.clock_uncertainty_ns, "clock_uncertainty_ns")


@dataclass(frozen=True, slots=True)
class BookState:
    ticker: str
    connection_epoch: int
    sequence: int
    market_status: MarketStatus
    scheduled_close_wall_ns: int
    book_source_wall_ns: int
    book_observed_monotonic_ns: int
    book_clock_uncertainty_ns: int
    lifecycle_source_wall_ns: int
    lifecycle_observed_monotonic_ns: int
    lifecycle_clock_uncertainty_ns: int
    yes_bids: tuple[BookLevel, ...]
    no_bids: tuple[BookLevel, ...]
    trusted: bool
    sequence_gap: bool
    last_executable_move: Decimal
    last_executable_move_monotonic_ns: int
    last_snapshot_sha256: str
    last_event_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, BookState)
        _ticker(self.ticker, "ticker")
        _integer(self.connection_epoch, "connection_epoch")
        _integer(self.sequence, "sequence", positive=True)
        _exact(self.market_status, MarketStatus, "market_status")
        _integer(self.scheduled_close_wall_ns, "scheduled_close_wall_ns")
        _integer(self.book_source_wall_ns, "book_source_wall_ns")
        _integer(
            self.book_observed_monotonic_ns,
            "book_observed_monotonic_ns",
        )
        _integer(self.book_clock_uncertainty_ns, "book_clock_uncertainty_ns")
        _integer(self.lifecycle_source_wall_ns, "lifecycle_source_wall_ns")
        _integer(
            self.lifecycle_observed_monotonic_ns,
            "lifecycle_observed_monotonic_ns",
        )
        _integer(
            self.lifecycle_clock_uncertainty_ns,
            "lifecycle_clock_uncertainty_ns",
        )
        _validate_book_ladders(self.yes_bids, self.no_bids)
        _boolean(self.trusted, "trusted")
        _boolean(self.sequence_gap, "sequence_gap")
        if self.trusted != (not self.sequence_gap):
            raise ExpertContractError("trusted")
        _probability(self.last_executable_move, "last_executable_move")
        _integer(
            self.last_executable_move_monotonic_ns,
            "last_executable_move_monotonic_ns",
        )
        if (
            self.last_executable_move_monotonic_ns
            > self.book_observed_monotonic_ns
        ):
            raise ExpertContractError("last_executable_move_monotonic_ns")
        _sha256(self.last_snapshot_sha256, "last_snapshot_sha256")
        _sha256(self.last_event_sha256, "last_event_sha256")


@dataclass(frozen=True, slots=True)
class BookTransitionResult:
    state: BookState
    accepted_event_kind: BookEventKind | None
    accepted_event_sha256: str | None
    executable_move: Decimal
    move_observed_monotonic_ns: int | None
    connection_epoch: int
    sequence: int
    top_of_book_changed: bool

    def __post_init__(self) -> None:
        _exact_self(self, BookTransitionResult)
        _exact(self.state, BookState, "state")
        _optional_exact(
            self.accepted_event_kind,
            BookEventKind,
            "accepted_event_kind",
        )
        if self.accepted_event_sha256 is not None:
            _sha256(
                self.accepted_event_sha256,
                "accepted_event_sha256",
            )
        _probability(self.executable_move, "executable_move")
        if self.move_observed_monotonic_ns is not None:
            _integer(
                self.move_observed_monotonic_ns,
                "move_observed_monotonic_ns",
            )
        _integer(self.connection_epoch, "connection_epoch")
        _integer(self.sequence, "sequence", positive=True)
        _boolean(self.top_of_book_changed, "top_of_book_changed")
        if (
            (self.accepted_event_kind is None)
            != (self.accepted_event_sha256 is None)
        ):
            raise ExpertContractError("accepted_event")
        if self.connection_epoch != self.state.connection_epoch:
            raise ExpertContractError("connection_epoch")
        if self.sequence != self.state.sequence:
            raise ExpertContractError("sequence")
        if self.accepted_event_kind is None:
            if (
                self.executable_move != Decimal("0")
                or self.move_observed_monotonic_ns is not None
                or self.top_of_book_changed
            ):
                raise ExpertContractError("event_move")
            return
        if self.accepted_event_kind is BookEventKind.LIFECYCLE:
            if (
                self.executable_move != Decimal("0")
                or self.move_observed_monotonic_ns is not None
                or self.top_of_book_changed
            ):
                raise ExpertContractError("event_move")
            if self.accepted_event_sha256 != self.state.last_event_sha256:
                raise ExpertContractError("accepted_event_sha256")
            return
        if not self.state.trusted or self.state.sequence_gap:
            raise ExpertContractError("accepted_event_state")
        if (
            self.executable_move != self.state.last_executable_move
            or self.move_observed_monotonic_ns is None
            or self.move_observed_monotonic_ns
            != self.state.book_observed_monotonic_ns
            or self.move_observed_monotonic_ns
            != self.state.last_executable_move_monotonic_ns
            or self.top_of_book_changed
            != (self.executable_move > Decimal("0"))
        ):
            raise ExpertContractError("event_move")
        if self.accepted_event_sha256 != self.state.last_event_sha256:
            raise ExpertContractError("accepted_event_sha256")
        if (
            self.accepted_event_kind is BookEventKind.SNAPSHOT
            and self.accepted_event_sha256
            != self.state.last_snapshot_sha256
        ):
            raise ExpertContractError("accepted_event_sha256")


@dataclass(frozen=True, slots=True)
class MatchBinding:
    provider_match_id: str
    canonical_match_id: str
    provider_source_id: str
    revision_domain_id: str
    source_lineage_sha256: str
    provider_home_player_id: str
    provider_away_player_id: str
    kalshi_event_ticker: str
    home_market_ticker: str
    away_market_ticker: str
    match_format: MatchFormat
    scheduled_start_wall_ns: int
    start_tolerance_ns: int
    artifact_created_wall_ns: int
    binding_artifact_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, MatchBinding)
        _safe_id(self.provider_match_id, "provider_match_id")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _safe_id(self.provider_source_id, "provider_source_id")
        _safe_id(self.revision_domain_id, "revision_domain_id")
        _sha256(self.source_lineage_sha256, "source_lineage_sha256")
        _safe_id(self.provider_home_player_id, "provider_home_player_id")
        _safe_id(self.provider_away_player_id, "provider_away_player_id")
        if self.provider_home_player_id == self.provider_away_player_id:
            raise ExpertContractError("player_identity")
        _ticker(self.kalshi_event_ticker, "kalshi_event_ticker")
        _ticker(self.home_market_ticker, "home_market_ticker")
        _ticker(self.away_market_ticker, "away_market_ticker")
        if self.home_market_ticker == self.away_market_ticker:
            raise ExpertContractError("market_tickers")
        _exact(self.match_format, MatchFormat, "match_format")
        if self.match_format not in _SUPPORTED_MATCH_FORMATS:
            raise ExpertContractError("match_format")
        _integer(self.scheduled_start_wall_ns, "scheduled_start_wall_ns")
        _integer(self.start_tolerance_ns, "start_tolerance_ns")
        _integer(self.artifact_created_wall_ns, "artifact_created_wall_ns")
        if self.artifact_created_wall_ns > self.scheduled_start_wall_ns:
            raise ExpertContractError("artifact_created_wall_ns")
        _sha256(self.binding_artifact_sha256, "binding_artifact_sha256")


@dataclass(frozen=True, slots=True)
class BindingRoute:
    player_side: PlayerSide
    market_ticker: str
    contract_side: ContractSide

    def __post_init__(self) -> None:
        _exact_self(self, BindingRoute)
        _exact(self.player_side, PlayerSide, "player_side")
        _ticker(self.market_ticker, "market_ticker")
        _exact(self.contract_side, ContractSide, "contract_side")
        if self.contract_side is not ContractSide.YES:
            raise ExpertContractError("contract_side")


@dataclass(frozen=True, slots=True)
class SettlementSemantics:
    result_authority: str
    natural_completion: str
    retirement_after_point: str
    walkover_before_point: str
    default_after_point: str
    disqualification_after_point: str
    cancellation: str
    postponement: str
    abandonment: str
    amendment: str
    void_treatment: str
    raw_rules_sha256: str
    projection_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, SettlementSemantics)
        _validate_settlement_values(
            result_authority=self.result_authority,
            natural_completion=self.natural_completion,
            retirement_after_point=self.retirement_after_point,
            walkover_before_point=self.walkover_before_point,
            default_after_point=self.default_after_point,
            disqualification_after_point=self.disqualification_after_point,
            cancellation=self.cancellation,
            postponement=self.postponement,
            abandonment=self.abandonment,
            amendment=self.amendment,
            void_treatment=self.void_treatment,
            raw_rules_sha256=self.raw_rules_sha256,
        )
        _sha256(self.projection_sha256, "projection_sha256")
        if self.projection_sha256 != compute_settlement_projection_sha256(
            result_authority=self.result_authority,
            natural_completion=self.natural_completion,
            retirement_after_point=self.retirement_after_point,
            walkover_before_point=self.walkover_before_point,
            default_after_point=self.default_after_point,
            disqualification_after_point=self.disqualification_after_point,
            cancellation=self.cancellation,
            postponement=self.postponement,
            abandonment=self.abandonment,
            amendment=self.amendment,
            void_treatment=self.void_treatment,
            raw_rules_sha256=self.raw_rules_sha256,
        ):
            raise ExpertContractError("projection_sha256")


@dataclass(frozen=True, slots=True)
class BindingMarketMetadata:
    series_ticker: str
    event_ticker: str
    event_id: str
    market_ticker: str
    market_id: str
    yes_player_side: PlayerSide
    yes_provider_player_id: str
    yes_canonical_player_id: str
    product: str
    event_catalog_sha256: str
    membership_source_id: str
    membership_source_version: str
    membership_captured_wall_ns: int
    membership_evidence_sha256: str
    membership_projection_sha256: str
    market_text_sha256: str
    settlement_rule_text_sha256: str
    settlement: SettlementSemantics

    def __post_init__(self) -> None:
        _exact_self(self, BindingMarketMetadata)
        _ticker(self.series_ticker, "series_ticker")
        _ticker(self.event_ticker, "event_ticker")
        _safe_id(self.event_id, "event_id")
        _ticker(self.market_ticker, "market_ticker")
        _safe_id(self.market_id, "market_id")
        _exact(self.yes_player_side, PlayerSide, "yes_player_side")
        _safe_id(self.yes_provider_player_id, "yes_provider_player_id")
        _safe_id(self.yes_canonical_player_id, "yes_canonical_player_id")
        _string(self.product, "product")
        if self.product != "match_winner":
            raise ExpertContractError("product")
        _sha256(self.event_catalog_sha256, "event_catalog_sha256")
        _safe_id(self.membership_source_id, "membership_source_id")
        _safe_id(
            self.membership_source_version,
            "membership_source_version",
        )
        _integer(
            self.membership_captured_wall_ns,
            "membership_captured_wall_ns",
        )
        _sha256(
            self.membership_evidence_sha256,
            "membership_evidence_sha256",
        )
        _sha256(
            self.membership_projection_sha256,
            "membership_projection_sha256",
        )
        _sha256(self.market_text_sha256, "market_text_sha256")
        _sha256(
            self.settlement_rule_text_sha256,
            "settlement_rule_text_sha256",
        )
        _exact(self.settlement, SettlementSemantics, "settlement")
        if self.membership_projection_sha256 != (
            compute_membership_projection_sha256(
                series_ticker=self.series_ticker,
                event_ticker=self.event_ticker,
                event_id=self.event_id,
                market_ticker=self.market_ticker,
                market_id=self.market_id,
                product=self.product,
                event_catalog_sha256=self.event_catalog_sha256,
                membership_source_id=self.membership_source_id,
                membership_source_version=self.membership_source_version,
                membership_captured_wall_ns=self.membership_captured_wall_ns,
                membership_evidence_sha256=self.membership_evidence_sha256,
            )
        ):
            raise ExpertContractError("membership_projection_sha256")
        if (
            self.settlement.raw_rules_sha256
            != self.settlement_rule_text_sha256
        ):
            raise ExpertContractError("settlement_rule_text_sha256")


@dataclass(frozen=True, slots=True)
class BindingMetadata:
    canonical_match_id: str
    canonical_home_player_id: str
    canonical_away_player_id: str
    tournament_id: str
    season_id: str
    draw_id: str
    round_id: str
    tour_id: str
    tier_id: str
    surface: str
    provider_snapshot_sha256: str
    kalshi_event_sha256: str
    markets: tuple[BindingMarketMetadata, ...]
    authorized_routes: tuple[BindingRoute, ...]

    def __post_init__(self) -> None:
        _exact_self(self, BindingMetadata)
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _safe_id(
            self.canonical_home_player_id,
            "canonical_home_player_id",
        )
        _safe_id(
            self.canonical_away_player_id,
            "canonical_away_player_id",
        )
        if self.canonical_home_player_id == self.canonical_away_player_id:
            raise ExpertContractError("canonical_player_identity")
        _safe_id(self.tournament_id, "tournament_id")
        _safe_id(self.season_id, "season_id")
        _safe_id(self.draw_id, "draw_id")
        _safe_id(self.round_id, "round_id")
        _safe_id(self.tour_id, "tour_id")
        _safe_id(self.tier_id, "tier_id")
        _string(self.surface, "surface")
        if self.surface not in {"hard", "clay", "grass", "carpet"}:
            raise ExpertContractError("surface")
        _sha256(self.provider_snapshot_sha256, "provider_snapshot_sha256")
        _sha256(self.kalshi_event_sha256, "kalshi_event_sha256")
        markets = _exact_tuple(
            self.markets,
            BindingMarketMetadata,
            "markets",
        )
        routes = _exact_tuple(
            self.authorized_routes,
            BindingRoute,
            "authorized_routes",
        )
        if len(markets) != 2:
            raise ExpertContractError("markets")
        if len(routes) != 2:
            raise ExpertContractError("authorized_routes")
        home_market, away_market = markets
        if (
            home_market.yes_player_side is not PlayerSide.HOME
            or away_market.yes_player_side is not PlayerSide.AWAY
            or home_market.yes_canonical_player_id
            != self.canonical_home_player_id
            or away_market.yes_canonical_player_id
            != self.canonical_away_player_id
            or home_market.yes_provider_player_id
            == away_market.yes_provider_player_id
            or home_market.market_ticker == away_market.market_ticker
            or home_market.market_id == away_market.market_id
            or home_market.series_ticker != away_market.series_ticker
            or home_market.event_ticker != away_market.event_ticker
            or home_market.event_id != away_market.event_id
            or home_market.event_catalog_sha256
            != away_market.event_catalog_sha256
        ):
            raise ExpertContractError("markets")
        expected_routes = (
            BindingRoute(
                player_side=PlayerSide.HOME,
                market_ticker=home_market.market_ticker,
                contract_side=ContractSide.YES,
            ),
            BindingRoute(
                player_side=PlayerSide.AWAY,
                market_ticker=away_market.market_ticker,
                contract_side=ContractSide.YES,
            ),
        )
        if routes != expected_routes:
            raise ExpertContractError("authorized_routes")


@dataclass(frozen=True, slots=True)
class BindingReviewDecision:
    review_artifact_id: str
    review_artifact_sha256: str
    review_artifact_created_wall_ns: int
    binding_artifact_id: str
    binding_artifact_sha256: str
    decision: str
    reviewer_id: str
    reviewed_wall_ns: int
    review_evidence_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, BindingReviewDecision)
        _safe_id(self.review_artifact_id, "review_artifact_id")
        _sha256(self.review_artifact_sha256, "review_artifact_sha256")
        _integer(
            self.review_artifact_created_wall_ns,
            "review_artifact_created_wall_ns",
        )
        _safe_id(self.binding_artifact_id, "binding_artifact_id")
        _sha256(self.binding_artifact_sha256, "binding_artifact_sha256")
        _string(self.decision, "decision")
        if self.decision != "approved":
            raise ExpertContractError("decision")
        _safe_id(self.reviewer_id, "reviewer_id")
        _integer(self.reviewed_wall_ns, "reviewed_wall_ns")
        _sha256(
            self.review_evidence_sha256,
            "review_evidence_sha256",
        )
        if (
            self.reviewed_wall_ns
            > self.review_artifact_created_wall_ns
        ):
            raise ExpertContractError("review_artifact_created_wall_ns")
        computed = compute_binding_review_artifact_sha256(
            review_artifact_id=self.review_artifact_id,
            review_artifact_created_wall_ns=(
                self.review_artifact_created_wall_ns
            ),
            binding_artifact_id=self.binding_artifact_id,
            binding_artifact_sha256=self.binding_artifact_sha256,
            decision=self.decision,
            reviewer_id=self.reviewer_id,
            reviewed_wall_ns=self.reviewed_wall_ns,
            review_evidence_sha256=self.review_evidence_sha256,
        )
        if self.review_artifact_sha256 != computed:
            raise ExpertContractError("review_artifact_sha256")


@dataclass(frozen=True, slots=True)
class BindingUniverse:
    raw_artifact_id: str
    raw_artifact_sha256: str
    review: BindingReviewDecision
    bindings: tuple[MatchBinding, ...]
    metadata: tuple[BindingMetadata, ...]
    universe_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, BindingUniverse)
        _safe_id(self.raw_artifact_id, "raw_artifact_id")
        _sha256(self.raw_artifact_sha256, "raw_artifact_sha256")
        _exact(self.review, BindingReviewDecision, "review")
        bindings = _exact_tuple(self.bindings, MatchBinding, "bindings")
        metadata = _exact_tuple(
            self.metadata,
            BindingMetadata,
            "metadata",
        )
        _sha256(self.universe_sha256, "universe_sha256")
        if len(bindings) < 1 or len(bindings) > 128:
            raise ExpertContractError("bindings")
        if len(metadata) != len(bindings):
            raise ExpertContractError("metadata")
        if (
            self.review.binding_artifact_id != self.raw_artifact_id
            or self.review.binding_artifact_sha256
            != self.raw_artifact_sha256
        ):
            raise ExpertContractError("review_binding")
        previous_order: tuple[str, str, str, str, str] | None = None
        global_seen: tuple[set[object], ...] = (
            set(),
            set(),
            set(),
            set(),
            set(),
            set(),
        )
        occurrences: list[tuple[str, str, str, str]] = []
        for binding, item in zip(bindings, metadata, strict=True):
            if binding.binding_artifact_sha256 != self.raw_artifact_sha256:
                raise ExpertContractError("binding_artifact_sha256")
            if (
                item.canonical_match_id != binding.canonical_match_id
                or item.markets[0].yes_provider_player_id
                != binding.provider_home_player_id
                or item.markets[1].yes_provider_player_id
                != binding.provider_away_player_id
                or item.markets[0].event_ticker
                != binding.kalshi_event_ticker
                or item.markets[1].event_ticker
                != binding.kalshi_event_ticker
                or item.markets[0].market_ticker
                != binding.home_market_ticker
                or item.markets[1].market_ticker
                != binding.away_market_ticker
            ):
                raise ExpertContractError("binding_projection")
            order_key = (
                binding.canonical_match_id,
                binding.provider_source_id,
                binding.revision_domain_id,
                binding.provider_match_id,
                binding.kalshi_event_ticker,
            )
            if previous_order is not None and order_key <= previous_order:
                raise ExpertContractError("binding_manifest_order")
            previous_order = order_key
            collision_values: tuple[object, ...] = (
                binding.canonical_match_id,
                (
                    binding.provider_source_id,
                    binding.revision_domain_id,
                    binding.provider_match_id,
                ),
                binding.kalshi_event_ticker,
                item.markets[0].event_id,
                tuple(market.market_ticker for market in item.markets),
                tuple(market.market_id for market in item.markets),
            )
            for seen, value in zip(
                global_seen[:4],
                collision_values[:4],
                strict=True,
            ):
                if value in seen:
                    raise ExpertContractError("binding_manifest_collision")
                seen.add(value)
            for market_ticker in collision_values[4]:
                if market_ticker in global_seen[4]:
                    raise ExpertContractError("binding_manifest_collision")
                global_seen[4].add(market_ticker)
            for market_id in collision_values[5]:
                if market_id in global_seen[5]:
                    raise ExpertContractError("binding_manifest_collision")
                global_seen[5].add(market_id)
            if (
                item.markets[0].membership_captured_wall_ns
                > binding.artifact_created_wall_ns
                or item.markets[1].membership_captured_wall_ns
                > binding.artifact_created_wall_ns
            ):
                raise ExpertContractError("binding_manifest_evidence")
            if (
                binding.artifact_created_wall_ns
                > self.review.reviewed_wall_ns
                or self.review.reviewed_wall_ns
                > self.review.review_artifact_created_wall_ns
                or self.review.review_artifact_created_wall_ns
                > binding.scheduled_start_wall_ns
            ):
                raise ExpertContractError("binding_review_time")
            occurrences.extend(
                (
                    (
                        binding.provider_source_id,
                        binding.source_lineage_sha256,
                        binding.provider_home_player_id,
                        item.canonical_home_player_id,
                    ),
                    (
                        binding.provider_source_id,
                        binding.source_lineage_sha256,
                        binding.provider_away_player_id,
                        item.canonical_away_player_id,
                    ),
                )
            )
        _validate_player_bijection(occurrences)
        manifest_pin = ArtifactPin(
            artifact_id=self.raw_artifact_id,
            artifact_sha256=self.raw_artifact_sha256,
        )
        evidence_sha256 = compute_binding_review_evidence_sha256(
            manifest_pin,
            bindings,
            metadata,
        )
        if self.review.review_evidence_sha256 != evidence_sha256:
            raise ExpertContractError("binding_review_evidence")
        computed_universe = compute_binding_universe_sha256(
            manifest_pin,
            self.review,
            bindings,
            metadata,
        )
        if self.universe_sha256 != computed_universe:
            raise ExpertContractError("universe_sha256")


@dataclass(frozen=True, slots=True)
class CausalPointWitness:
    canonical_match_id: str
    correction_epoch: int
    revision: int
    event_semantic_sha256: str
    received_monotonic_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, CausalPointWitness)
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.revision, "revision")
        _sha256(self.event_semantic_sha256, "event_semantic_sha256")
        _integer(self.received_monotonic_ns, "received_monotonic_ns")


@dataclass(frozen=True, slots=True)
class PendingBookMove:
    canonical_match_id: str
    ticker: str
    first_move_monotonic_ns: int
    last_move_monotonic_ns: int
    first_connection_epoch: int
    first_sequence: int
    first_event_sha256: str
    last_connection_epoch: int
    last_sequence: int
    last_event_sha256: str
    move_count: int
    max_magnitude: Decimal
    tennis_correction_epoch_floor: int | None
    book_connection_epoch_floor: int

    def __post_init__(self) -> None:
        _exact_self(self, PendingBookMove)
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _integer(
            self.first_move_monotonic_ns,
            "first_move_monotonic_ns",
        )
        _integer(
            self.last_move_monotonic_ns,
            "last_move_monotonic_ns",
        )
        _integer(self.first_connection_epoch, "first_connection_epoch")
        _integer(self.first_sequence, "first_sequence", positive=True)
        _sha256(self.first_event_sha256, "first_event_sha256")
        _integer(self.last_connection_epoch, "last_connection_epoch")
        _integer(self.last_sequence, "last_sequence", positive=True)
        _sha256(self.last_event_sha256, "last_event_sha256")
        _integer(self.move_count, "move_count", positive=True)
        _probability(self.max_magnitude, "max_magnitude")
        if self.max_magnitude == Decimal("0"):
            raise ExpertContractError("max_magnitude")
        if self.tennis_correction_epoch_floor is not None:
            _integer(
                self.tennis_correction_epoch_floor,
                "tennis_correction_epoch_floor",
            )
        _integer(
            self.book_connection_epoch_floor,
            "book_connection_epoch_floor",
        )

        if self.first_move_monotonic_ns > self.last_move_monotonic_ns:
            raise ExpertContractError("pending_move_order")
        first_identity = (
            self.first_connection_epoch,
            self.first_sequence,
        )
        last_identity = (
            self.last_connection_epoch,
            self.last_sequence,
        )
        if (
            first_identity > last_identity
            or (
                self.move_count == 1
                and (
                    first_identity != last_identity
                    or self.first_move_monotonic_ns
                    != self.last_move_monotonic_ns
                    or self.first_event_sha256
                    != self.last_event_sha256
                )
            )
            or (
                self.move_count > 1
                and first_identity == last_identity
            )
        ):
            raise ExpertContractError("pending_move_identity")
        if self.book_connection_epoch_floor != self.first_connection_epoch:
            raise ExpertContractError("pending_move_floor")


@dataclass(frozen=True, slots=True)
class TennisSyncCursor:
    canonical_match_id: str
    binding_sha256: str
    binding_metadata_sha256: str
    tennis: TennisState | None
    last_state_sha256: str | None
    last_input_sha256: str | None
    last_point_witness: CausalPointWitness | None

    def __post_init__(self) -> None:
        _exact_self(self, TennisSyncCursor)
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _sha256(self.binding_sha256, "binding_sha256")
        _sha256(
            self.binding_metadata_sha256,
            "binding_metadata_sha256",
        )
        _optional_exact(self.tennis, TennisState, "tennis")
        if self.last_state_sha256 is not None:
            _sha256(self.last_state_sha256, "last_state_sha256")
        if self.last_input_sha256 is not None:
            _sha256(self.last_input_sha256, "last_input_sha256")
        _optional_exact(
            self.last_point_witness,
            CausalPointWitness,
            "last_point_witness",
        )

        if self.tennis is None:
            if (
                self.last_state_sha256 is not None
                or self.last_input_sha256 is not None
                or self.last_point_witness is not None
            ):
                raise ExpertContractError("tennis_cursor_state")
            return
        if (
            self.last_state_sha256
            != expert_contract_sha256(self.tennis)
            or self.last_input_sha256 is None
        ):
            raise ExpertContractError("tennis_cursor_state")
        witness = self.last_point_witness
        if witness is not None and (
            witness.canonical_match_id != self.canonical_match_id
            or witness.correction_epoch != self.tennis.correction_epoch
            or witness.revision > self.tennis.revision
            or witness.received_monotonic_ns
            > self.tennis.last_received_monotonic_ns
            or (
                witness.revision == self.tennis.revision
                and (
                    witness.event_semantic_sha256
                    != self.tennis.last_event_semantic_sha256
                    or witness.received_monotonic_ns
                    != self.tennis.last_received_monotonic_ns
                )
            )
        ):
            raise ExpertContractError("tennis_cursor_witness")


@dataclass(frozen=True, slots=True)
class LastSyncEmission:
    fingerprint_sha256: str
    provider_correction_epoch: int
    provider_revision: int
    provider_event_semantic_sha256: str
    book_connection_epoch: int
    book_sequence: int

    def __post_init__(self) -> None:
        _exact_self(self, LastSyncEmission)
        _sha256(self.fingerprint_sha256, "fingerprint_sha256")
        _integer(
            self.provider_correction_epoch,
            "provider_correction_epoch",
        )
        _integer(self.provider_revision, "provider_revision")
        _sha256(
            self.provider_event_semantic_sha256,
            "provider_event_semantic_sha256",
        )
        _integer(self.book_connection_epoch, "book_connection_epoch")
        _integer(self.book_sequence, "book_sequence", positive=True)


@dataclass(frozen=True, slots=True)
class BookSyncCursor:
    canonical_match_id: str
    ticker: str
    binding_sha256: str
    binding_metadata_sha256: str
    book: BookState | None
    last_state_sha256: str | None
    last_input_sha256: str | None
    pending_move: PendingBookMove | None
    causal_point_witness: CausalPointWitness | None
    consumed_point_witness: CausalPointWitness | None
    last_emission: LastSyncEmission | None

    def __post_init__(self) -> None:
        _exact_self(self, BookSyncCursor)
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _sha256(self.binding_sha256, "binding_sha256")
        _sha256(
            self.binding_metadata_sha256,
            "binding_metadata_sha256",
        )
        _optional_exact(self.book, BookState, "book")
        if self.last_state_sha256 is not None:
            _sha256(self.last_state_sha256, "last_state_sha256")
        if self.last_input_sha256 is not None:
            _sha256(self.last_input_sha256, "last_input_sha256")
        _optional_exact(
            self.pending_move,
            PendingBookMove,
            "pending_move",
        )
        _optional_exact(
            self.causal_point_witness,
            CausalPointWitness,
            "causal_point_witness",
        )
        _optional_exact(
            self.consumed_point_witness,
            CausalPointWitness,
            "consumed_point_witness",
        )
        _optional_exact(
            self.last_emission,
            LastSyncEmission,
            "last_emission",
        )

        if self.book is None:
            if any(
                value is not None
                for value in (
                    self.last_state_sha256,
                    self.last_input_sha256,
                    self.pending_move,
                    self.causal_point_witness,
                    self.consumed_point_witness,
                    self.last_emission,
                )
            ):
                raise ExpertContractError("book_cursor_state")
            return
        if self.book.ticker != self.ticker:
            raise ExpertContractError("book_cursor_binding")
        if (
            self.last_state_sha256 != expert_contract_sha256(self.book)
            or self.last_input_sha256 is None
        ):
            raise ExpertContractError("book_cursor_state")
        pending = self.pending_move
        if pending is not None and (
            pending.canonical_match_id != self.canonical_match_id
            or pending.ticker != self.ticker
            or (
                pending.first_connection_epoch,
                pending.first_sequence,
            )
            > (
                pending.last_connection_epoch,
                pending.last_sequence,
            )
            or (
                pending.last_connection_epoch,
                pending.last_sequence,
            )
            > (self.book.connection_epoch, self.book.sequence)
            or pending.last_move_monotonic_ns
            > self.book.book_observed_monotonic_ns
        ):
            raise ExpertContractError("book_cursor_pending")
        witnesses = (
            self.causal_point_witness,
            self.consumed_point_witness,
        )
        if (
            any(
                witness is not None
                and witness.canonical_match_id != self.canonical_match_id
                for witness in witnesses
            )
            or (
                pending is not None
                and self.causal_point_witness is not None
            )
            or (
                self.causal_point_witness is not None
                and self.causal_point_witness
                != self.consumed_point_witness
            )
        ):
            raise ExpertContractError("book_cursor_witness")
        emission = self.last_emission
        if emission is not None and (
            emission.book_connection_epoch,
            emission.book_sequence,
        ) > (self.book.connection_epoch, self.book.sequence):
            raise ExpertContractError("book_cursor_emission")


def _task5_witness_matches_tennis(
    witness: CausalPointWitness,
    tennis: TennisState,
    canonical_match_id: str,
) -> bool:
    coordinates = (witness.correction_epoch, witness.revision)
    current = (tennis.correction_epoch, tennis.revision)
    return not (
        witness.canonical_match_id != canonical_match_id
        or coordinates > current
        or witness.received_monotonic_ns
        > tennis.last_received_monotonic_ns
        or (
            coordinates == current
            and (
                witness.event_semantic_sha256
                != tennis.last_event_semantic_sha256
                or witness.received_monotonic_ns
                != tennis.last_received_monotonic_ns
            )
        )
    )


@dataclass(frozen=True, slots=True)
class SynchronizationSessionState:
    universe: BindingUniverse
    policy: SyncPolicy
    universe_sha256: str
    sync_policy_sha256: str
    decision_sequence: int
    last_observation: PairedTimeObservation | None
    tennis_cursors: tuple[TennisSyncCursor, ...]
    book_cursors: tuple[BookSyncCursor, ...]

    def __post_init__(self) -> None:
        _exact_self(self, SynchronizationSessionState)
        _exact(self.universe, BindingUniverse, "universe")
        _exact(self.policy, SyncPolicy, "policy")
        _sha256(self.universe_sha256, "universe_sha256")
        _sha256(self.sync_policy_sha256, "sync_policy_sha256")
        _integer(self.decision_sequence, "decision_sequence")
        _optional_exact(
            self.last_observation,
            PairedTimeObservation,
            "last_observation",
        )
        tennis_cursors = _exact_tuple(
            self.tennis_cursors,
            TennisSyncCursor,
            "tennis_cursors",
        )
        book_cursors = _exact_tuple(
            self.book_cursors,
            BookSyncCursor,
            "book_cursors",
        )

        try:
            BindingUniverse.__post_init__(self.universe)
            computed_universe_sha256 = compute_binding_universe_sha256(
                ArtifactPin(
                    self.universe.raw_artifact_id,
                    self.universe.raw_artifact_sha256,
                ),
                self.universe.review,
                self.universe.bindings,
                self.universe.metadata,
            )
        except (TypeError, ValueError):
            raise ExpertContractError(
                "synchronization_session_universe"
            ) from None
        provider_triples = {
            (
                binding.provider_source_id,
                binding.revision_domain_id,
                binding.source_lineage_sha256,
            )
            for binding in self.universe.bindings
        }
        if (
            computed_universe_sha256 != self.universe.universe_sha256
            or self.universe_sha256 != computed_universe_sha256
            or len(provider_triples) != 1
        ):
            raise ExpertContractError("synchronization_session_universe")
        try:
            SyncPolicy.__post_init__(self.policy)
        except (TypeError, ValueError):
            raise ExpertContractError(
                "synchronization_session_policy"
            ) from None
        if (
            self.policy.universe_sha256 != computed_universe_sha256
            or self.sync_policy_sha256
            != expert_contract_sha256(self.policy)
        ):
            raise ExpertContractError("synchronization_session_policy")

        if self.last_observation is None and (
            self.decision_sequence != 0
            or any(cursor.tennis is not None for cursor in tennis_cursors)
            or any(cursor.book is not None for cursor in book_cursors)
        ):
            raise ExpertContractError("synchronization_session_cursors")
        has_emission = any(
            cursor.last_emission is not None
            for cursor in book_cursors
        )
        if has_emission != (self.decision_sequence > 0):
            raise ExpertContractError("synchronization_session_cursors")

        try:
            if self.last_observation is not None:
                PairedTimeObservation.__post_init__(
                    self.last_observation
                )
            for cursor in tennis_cursors:
                TennisSyncCursor.__post_init__(cursor)
                if cursor.tennis is not None:
                    TennisState.__post_init__(cursor.tennis)
                if cursor.last_point_witness is not None:
                    CausalPointWitness.__post_init__(
                        cursor.last_point_witness
                    )
            for cursor in book_cursors:
                BookSyncCursor.__post_init__(cursor)
                if cursor.book is not None:
                    BookState.__post_init__(cursor.book)
                if cursor.pending_move is not None:
                    PendingBookMove.__post_init__(cursor.pending_move)
                if cursor.causal_point_witness is not None:
                    CausalPointWitness.__post_init__(
                        cursor.causal_point_witness
                    )
                if cursor.consumed_point_witness is not None:
                    CausalPointWitness.__post_init__(
                        cursor.consumed_point_witness
                    )
                if cursor.last_emission is not None:
                    LastSyncEmission.__post_init__(
                        cursor.last_emission
                    )
        except (TypeError, ValueError):
            raise ExpertContractError(
                "synchronization_session_cursors"
            ) from None

        bindings_by_match = {
            binding.canonical_match_id: (binding, metadata)
            for binding, metadata in zip(
                self.universe.bindings,
                self.universe.metadata,
                strict=True,
            )
        }
        expected_tennis = tuple(sorted(bindings_by_match))
        expected_books = tuple(
            sorted(
                (
                    binding.canonical_match_id,
                    ticker,
                )
                for binding in self.universe.bindings
                for ticker in (
                    binding.home_market_ticker,
                    binding.away_market_ticker,
                )
            )
        )
        if (
            tuple(cursor.canonical_match_id for cursor in tennis_cursors)
            != expected_tennis
            or tuple(
                (cursor.canonical_match_id, cursor.ticker)
                for cursor in book_cursors
            )
            != expected_books
        ):
            raise ExpertContractError("synchronization_session_cursors")
        tennis_by_match = {
            cursor.canonical_match_id: cursor
            for cursor in tennis_cursors
        }
        for cursor in tennis_cursors:
            binding, metadata = bindings_by_match[cursor.canonical_match_id]
            if (
                cursor.binding_sha256
                != expert_contract_sha256(binding)
                or cursor.binding_metadata_sha256
                != expert_contract_sha256(metadata)
            ):
                raise ExpertContractError(
                    "synchronization_session_cursors"
                )
        for cursor in book_cursors:
            binding, metadata = bindings_by_match[cursor.canonical_match_id]
            if (
                cursor.binding_sha256
                != expert_contract_sha256(binding)
                or cursor.binding_metadata_sha256
                != expert_contract_sha256(metadata)
            ):
                raise ExpertContractError(
                    "synchronization_session_cursors"
                )
            tennis = tennis_by_match[cursor.canonical_match_id].tennis
            pending = cursor.pending_move
            if tennis is None:
                if (
                    pending is not None
                    and pending.tennis_correction_epoch_floor is not None
                ):
                    raise ExpertContractError(
                        "synchronization_session_cursors"
                    )
            elif pending is not None and (
                pending.tennis_correction_epoch_floor is None
                or pending.tennis_correction_epoch_floor
                > tennis.correction_epoch
            ):
                raise ExpertContractError(
                    "synchronization_session_cursors"
                )
            witnesses = (
                tennis_by_match[cursor.canonical_match_id].last_point_witness,
                cursor.causal_point_witness,
                cursor.consumed_point_witness,
            )
            if any(witness is not None for witness in witnesses):
                if tennis is None or any(
                    witness is not None
                    and not _task5_witness_matches_tennis(
                        witness,
                        tennis,
                        cursor.canonical_match_id,
                    )
                    for witness in witnesses
                ):
                    raise ExpertContractError(
                        "synchronization_session_cursors"
                    )
                causal = cursor.causal_point_witness
                if (
                    causal is not None
                    and causal.correction_epoch
                    != tennis.correction_epoch
                ):
                    raise ExpertContractError(
                        "synchronization_session_cursors"
                    )
            emission = cursor.last_emission
            if emission is not None:
                if tennis is None:
                    raise ExpertContractError(
                        "synchronization_session_cursors"
                    )
                emission_coordinates = (
                    emission.provider_correction_epoch,
                    emission.provider_revision,
                )
                tennis_coordinates = (
                    tennis.correction_epoch,
                    tennis.revision,
                )
                if (
                    emission_coordinates > tennis_coordinates
                    or (
                        emission_coordinates == tennis_coordinates
                        and emission.provider_event_semantic_sha256
                        != tennis.last_event_semantic_sha256
                    )
                ):
                    raise ExpertContractError(
                        "synchronization_session_cursors"
                    )


def _synchronization_emission_fingerprint(
    *,
    universe_sha256: str,
    sync_policy_sha256: str,
    binding: MatchBinding,
    binding_metadata: BindingMetadata,
    tennis: TennisState,
    book: BookState,
) -> str:
    _sha256(universe_sha256, "universe_sha256")
    _sha256(sync_policy_sha256, "sync_policy_sha256")
    _exact(binding, MatchBinding, "binding")
    _exact(
        binding_metadata,
        BindingMetadata,
        "binding_metadata",
    )
    _exact(tennis, TennisState, "tennis")
    _exact(book, BookState, "book")
    fingerprint_value = {
        "schema": "synchronization_emission_fingerprint_v1",
        "universe_sha256": universe_sha256,
        "sync_policy_sha256": sync_policy_sha256,
        "binding_sha256": expert_contract_sha256(binding),
        "binding_metadata_sha256": expert_contract_sha256(
            binding_metadata
        ),
        "provider_source_id": tennis.provider_source_id,
        "revision_domain_id": tennis.revision_domain_id,
        "source_lineage_sha256": tennis.source_lineage_sha256,
        "provider_correction_epoch": tennis.correction_epoch,
        "provider_revision": tennis.revision,
        "provider_event_semantic_sha256": (
            tennis.last_event_semantic_sha256
        ),
        "ticker": book.ticker,
        "book_connection_epoch": book.connection_epoch,
        "book_sequence": book.sequence,
        "book_event_sha256": book.last_event_sha256,
    }
    return sha256(
        b"INCI-SYNC-EMISSION-FINGERPRINT-V1\0"
        + canonical_expert_bytes(fingerprint_value)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class SynchronizationInput:
    kind: SyncInputKind
    canonical_match_id: str
    ticker: str | None
    previous_state_sha256: str | None
    provider_event: ProviderSnapshot | ProviderPoint | ProviderLifecycle | None
    tennis_transition: TennisTransitionResult | None
    book_event: BookSnapshot | BookDelta | MarketLifecycle | None
    book_transition: BookTransitionResult | None
    book_resnapshot_state: BookState | None

    def __post_init__(self) -> None:
        _exact_self(self, SynchronizationInput)
        _exact(self.kind, SyncInputKind, "kind")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        if self.ticker is not None:
            _ticker(self.ticker, "ticker")
        if self.previous_state_sha256 is not None:
            _sha256(
                self.previous_state_sha256,
                "previous_state_sha256",
            )
        if self.provider_event is not None and type(
            self.provider_event
        ) not in (ProviderSnapshot, ProviderPoint, ProviderLifecycle):
            raise TypeError("provider_event")
        _optional_exact(
            self.tennis_transition,
            TennisTransitionResult,
            "tennis_transition",
        )
        if self.book_event is not None and type(self.book_event) not in (
            BookSnapshot,
            BookDelta,
            MarketLifecycle,
        ):
            raise TypeError("book_event")
        _optional_exact(
            self.book_transition,
            BookTransitionResult,
            "book_transition",
        )
        _optional_exact(
            self.book_resnapshot_state,
            BookState,
            "book_resnapshot_state",
        )

        provider = self.provider_event
        book = self.book_event
        if self.kind is SyncInputKind.TENNIS_ORIGIN:
            valid_shape = (
                self.ticker is None
                and self.previous_state_sha256 is None
                and type(provider) is ProviderSnapshot
                and self.tennis_transition is None
                and book is None
                and self.book_transition is None
                and self.book_resnapshot_state is None
            )
        elif self.kind is SyncInputKind.TENNIS_TRANSITION:
            valid_shape = (
                self.ticker is None
                and self.previous_state_sha256 is not None
                and type(provider)
                in (ProviderSnapshot, ProviderPoint, ProviderLifecycle)
                and self.tennis_transition is not None
                and book is None
                and self.book_transition is None
                and self.book_resnapshot_state is None
            )
        elif self.kind is SyncInputKind.BOOK_TRANSITION:
            valid_shape = (
                self.ticker is not None
                and (
                    self.previous_state_sha256 is not None
                    or type(book) is BookSnapshot
                )
                and provider is None
                and self.tennis_transition is None
                and type(book)
                in (BookSnapshot, BookDelta, MarketLifecycle)
                and self.book_resnapshot_state is None
            )
        elif self.kind is SyncInputKind.BOOK_RESNAPSHOT_REQUIRED:
            valid_shape = (
                self.ticker is not None
                and self.previous_state_sha256 is not None
                and provider is None
                and self.tennis_transition is None
                and book is None
                and self.book_transition is None
                and self.book_resnapshot_state is not None
            )
        else:
            valid_shape = (
                self.ticker is not None
                and self.previous_state_sha256 is None
                and provider is None
                and self.tennis_transition is None
                and book is None
                and self.book_transition is None
                and self.book_resnapshot_state is None
            )
        if not valid_shape:
            raise ExpertContractError("synchronization_input_shape")
        tickers = tuple(
            value
            for value in (
                self.ticker,
                None if book is None else book.ticker,
                None
                if self.book_transition is None
                else self.book_transition.state.ticker,
                None
                if self.book_resnapshot_state is None
                else self.book_resnapshot_state.ticker,
            )
            if value is not None
        )
        if len(set(tickers)) > 1:
            raise ExpertContractError("synchronization_input_ticker")
        if type(provider) is ProviderSnapshot:
            ProviderSnapshot.__post_init__(provider)
        elif type(provider) is ProviderPoint:
            ProviderPoint.__post_init__(provider)
        elif type(provider) is ProviderLifecycle:
            ProviderLifecycle.__post_init__(provider)
        if self.tennis_transition is not None:
            TennisTransitionResult.__post_init__(
                self.tennis_transition
            )
            TennisState.__post_init__(
                self.tennis_transition.state
            )
        if type(book) is BookSnapshot:
            BookSnapshot.__post_init__(book)
        elif type(book) is BookDelta:
            BookDelta.__post_init__(book)
        elif type(book) is MarketLifecycle:
            MarketLifecycle.__post_init__(book)
        if self.book_transition is not None:
            BookTransitionResult.__post_init__(
                self.book_transition
            )
            BookState.__post_init__(
                self.book_transition.state
            )
        if self.book_resnapshot_state is not None:
            BookState.__post_init__(
                self.book_resnapshot_state
            )


@dataclass(frozen=True, slots=True)
class SyncResult:
    canonical_match_id: str
    ticker: str
    snapshot: TrustedSnapshot | None
    opportunity: OpportunityFrame | None
    reason: SyncReason

    def __post_init__(self) -> None:
        _exact_self(self, SyncResult)
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _optional_exact(self.snapshot, TrustedSnapshot, "snapshot")
        _optional_exact(
            self.opportunity,
            OpportunityFrame,
            "opportunity",
        )
        _exact(self.reason, SyncReason, "reason")
        trusted = self.reason is SyncReason.TRUSTED_SYNCHRONIZED
        if trusted != (
            self.snapshot is not None and self.opportunity is not None
        ) or (
            not trusted
            and (
                self.snapshot is not None
                or self.opportunity is not None
            )
        ):
            raise ExpertContractError("sync_result_shape")
        if trusted:
            assert self.snapshot is not None
            assert self.opportunity is not None
            try:
                PairedTimeObservation.__post_init__(
                    self.snapshot.decision_time
                )
                TennisState.__post_init__(self.snapshot.tennis)
                BookState.__post_init__(self.snapshot.book)
                MatchBinding.__post_init__(self.snapshot.binding)
                TrustedSnapshot.__post_init__(self.snapshot)
                OpportunityFrame.__post_init__(self.opportunity)
            except (TypeError, ValueError):
                raise ExpertContractError(
                    "sync_result_snapshot"
                ) from None
            if (
                self.snapshot.binding.canonical_match_id
                != self.canonical_match_id
                or self.snapshot.book.ticker != self.ticker
                or self.opportunity.canonical_match_id
                != self.canonical_match_id
                or self.opportunity.ticker != self.ticker
                or self.opportunity.snapshot != self.snapshot
            ):
                raise ExpertContractError("sync_result_snapshot")


@dataclass(frozen=True, slots=True)
class SynchronizationTransitionResult:
    state: SynchronizationSessionState
    input: SynchronizationInput
    input_sha256: str
    prior_session_sha256: str
    prior_decision_sequence: int
    observation: PairedTimeObservation
    results: tuple[SyncResult, ...]

    def __post_init__(self) -> None:
        _exact_self(self, SynchronizationTransitionResult)
        _exact(
            self.state,
            SynchronizationSessionState,
            "state",
        )
        _exact(self.input, SynchronizationInput, "input")
        _sha256(self.input_sha256, "input_sha256")
        _sha256(
            self.prior_session_sha256,
            "prior_session_sha256",
        )
        _integer(
            self.prior_decision_sequence,
            "prior_decision_sequence",
        )
        _exact(self.observation, PairedTimeObservation, "observation")
        results = _exact_tuple(self.results, SyncResult, "results")
        try:
            SynchronizationInput.__post_init__(self.input)
        except (TypeError, ValueError):
            raise ExpertContractError(
                "synchronization_transition_input"
            ) from None
        if self.input_sha256 != expert_contract_sha256(self.input):
            raise ExpertContractError("synchronization_transition_input")
        try:
            SynchronizationSessionState.__post_init__(self.state)
            PairedTimeObservation.__post_init__(self.observation)
        except (TypeError, ValueError):
            raise ExpertContractError(
                "synchronization_transition_state"
            ) from None
        if self.state.last_observation != self.observation:
            raise ExpertContractError("synchronization_transition_state")
        try:
            for result in results:
                SyncResult.__post_init__(result)
                if result.snapshot is not None:
                    PairedTimeObservation.__post_init__(
                        result.snapshot.decision_time
                    )
                    TennisState.__post_init__(
                        result.snapshot.tennis
                    )
                    BookState.__post_init__(result.snapshot.book)
                    MatchBinding.__post_init__(
                        result.snapshot.binding
                    )
                    TrustedSnapshot.__post_init__(
                        result.snapshot
                    )
                if result.opportunity is not None:
                    OpportunityFrame.__post_init__(
                        result.opportunity
                    )
        except (TypeError, ValueError):
            raise ExpertContractError(
                "synchronization_transition_results"
            ) from None
        binding_pairs = tuple(
            (binding, metadata)
            for binding, metadata in zip(
                self.state.universe.bindings,
                self.state.universe.metadata,
                strict=True,
            )
            if binding.canonical_match_id
            == self.input.canonical_match_id
        )
        if self.input.kind in {
            SyncInputKind.TENNIS_ORIGIN,
            SyncInputKind.TENNIS_TRANSITION,
        }:
            valid_results = (
                len(binding_pairs) == 1
                and len(results) == 2
                and all(
                    result.canonical_match_id
                    == self.input.canonical_match_id
                    for result in results
                )
                and tuple(result.ticker for result in results)
                == tuple(
                    sorted(
                        (
                            binding_pairs[0][0].home_market_ticker,
                            binding_pairs[0][0].away_market_ticker,
                        )
                    )
                )
            )
        else:
            valid_results = (
                len(binding_pairs) == 1
                and len(results) == 1
                and results[0].canonical_match_id
                == self.input.canonical_match_id
                and results[0].ticker == self.input.ticker
            )
        if not valid_results:
            raise ExpertContractError(
                "synchronization_transition_results"
            )
        trusted_results = tuple(
            result
            for result in results
            if result.reason is SyncReason.TRUSTED_SYNCHRONIZED
        )
        if self.state.decision_sequence != (
            self.prior_decision_sequence + len(trusted_results)
        ):
            raise ExpertContractError(
                "synchronization_transition_results"
            )
        expected_sequences = tuple(
            range(
                self.prior_decision_sequence + 1,
                self.state.decision_sequence + 1,
            )
        )
        actual_sequences = tuple(
            result.snapshot.decision_sequence
            for result in trusted_results
            if result.snapshot is not None
        )
        if actual_sequences != expected_sequences:
            raise ExpertContractError(
                "synchronization_transition_results"
            )
        tennis_matches = tuple(
            cursor
            for cursor in self.state.tennis_cursors
            if cursor.canonical_match_id
            == self.input.canonical_match_id
        )
        for result in trusted_results:
            assert result.snapshot is not None
            assert result.opportunity is not None
            book_matches = tuple(
                cursor
                for cursor in self.state.book_cursors
                if (
                    cursor.canonical_match_id
                    == result.canonical_match_id
                    and cursor.ticker == result.ticker
                )
            )
            if (
                len(tennis_matches) != 1
                or len(book_matches) != 1
                or result.snapshot.binding != binding_pairs[0][0]
                or result.snapshot.decision_time != self.observation
                or tennis_matches[0].tennis
                != result.snapshot.tennis
                or book_matches[0].book != result.snapshot.book
                or result.snapshot.sync_policy_sha256
                != self.state.sync_policy_sha256
                or result.opportunity.universe_sha256
                != self.state.universe_sha256
                or book_matches[0].last_emission is None
                or result.snapshot.causal_provider_revision
                != (
                    None
                    if book_matches[0].causal_point_witness is None
                    else book_matches[0].causal_point_witness.revision
                )
            ):
                raise ExpertContractError(
                    "synchronization_transition_results"
                )
            emission = book_matches[0].last_emission
            if (
                emission.provider_correction_epoch
                != result.snapshot.tennis.correction_epoch
                or emission.provider_revision
                != result.snapshot.tennis.revision
                or emission.provider_event_semantic_sha256
                != result.snapshot.tennis.last_event_semantic_sha256
                or emission.book_connection_epoch
                != result.snapshot.book.connection_epoch
                or emission.book_sequence
                != result.snapshot.book.sequence
                or emission.fingerprint_sha256
                != _synchronization_emission_fingerprint(
                    universe_sha256=self.state.universe_sha256,
                    sync_policy_sha256=self.state.sync_policy_sha256,
                    binding=binding_pairs[0][0],
                    binding_metadata=binding_pairs[0][1],
                    tennis=result.snapshot.tennis,
                    book=result.snapshot.book,
                )
            ):
                raise ExpertContractError(
                    "synchronization_transition_results"
                )


def player_side_for_contract(
    binding: MatchBinding,
    ticker: str,
    contract_side: ContractSide,
) -> PlayerSide:
    _exact(binding, MatchBinding, "binding")
    _ticker(ticker, "ticker")
    _exact(contract_side, ContractSide, "contract_side")
    if ticker == binding.home_market_ticker:
        if contract_side is ContractSide.YES:
            return PlayerSide.HOME
        return PlayerSide.AWAY
    if ticker == binding.away_market_ticker:
        if contract_side is ContractSide.YES:
            return PlayerSide.AWAY
        return PlayerSide.HOME
    raise ExpertContractError("contract_mismatch")


_SETTLEMENT_RESULT_AUTHORITY: Final[str] = (
    "kalshi_finalized_market_result"
)
_SETTLEMENT_NATURAL_COMPLETION: Final[str] = (
    "yes_if_named_player_final_winner"
)
_SETTLEMENT_BINARY_CHOICES: Final[frozenset[str]] = frozenset(
    {"yes_if_named_player_final_winner", "void"}
)
_SETTLEMENT_ABANDONMENT_CHOICES: Final[frozenset[str]] = frozenset(
    {"await_latest_finalized_result", "void"}
)


def _validate_settlement_values(
    *,
    result_authority: object,
    natural_completion: object,
    retirement_after_point: object,
    walkover_before_point: object,
    default_after_point: object,
    disqualification_after_point: object,
    cancellation: object,
    postponement: object,
    abandonment: object,
    amendment: object,
    void_treatment: object,
    raw_rules_sha256: object,
) -> None:
    _string(result_authority, "result_authority")
    if result_authority != _SETTLEMENT_RESULT_AUTHORITY:
        raise ExpertContractError("result_authority")
    _string(natural_completion, "natural_completion")
    if natural_completion != _SETTLEMENT_NATURAL_COMPLETION:
        raise ExpertContractError("natural_completion")
    for name, value in (
        ("retirement_after_point", retirement_after_point),
        ("walkover_before_point", walkover_before_point),
        ("default_after_point", default_after_point),
        ("disqualification_after_point", disqualification_after_point),
    ):
        _string(value, name)
        if value not in _SETTLEMENT_BINARY_CHOICES:
            raise ExpertContractError(name)
    _string(cancellation, "cancellation")
    if cancellation != "void":
        raise ExpertContractError("cancellation")
    _string(postponement, "postponement")
    if postponement != "defer":
        raise ExpertContractError("postponement")
    _string(abandonment, "abandonment")
    if abandonment not in _SETTLEMENT_ABANDONMENT_CHOICES:
        raise ExpertContractError("abandonment")
    _string(amendment, "amendment")
    if amendment != "await_latest_finalized_result":
        raise ExpertContractError("amendment")
    _string(void_treatment, "void_treatment")
    if void_treatment != "no_directional_settlement":
        raise ExpertContractError("void_treatment")
    _sha256(raw_rules_sha256, "raw_rules_sha256")


def compute_settlement_projection_sha256(
    *,
    result_authority: str,
    natural_completion: str,
    retirement_after_point: str,
    walkover_before_point: str,
    default_after_point: str,
    disqualification_after_point: str,
    cancellation: str,
    postponement: str,
    abandonment: str,
    amendment: str,
    void_treatment: str,
    raw_rules_sha256: str,
) -> str:
    _validate_settlement_values(
        result_authority=result_authority,
        natural_completion=natural_completion,
        retirement_after_point=retirement_after_point,
        walkover_before_point=walkover_before_point,
        default_after_point=default_after_point,
        disqualification_after_point=disqualification_after_point,
        cancellation=cancellation,
        postponement=postponement,
        abandonment=abandonment,
        amendment=amendment,
        void_treatment=void_treatment,
        raw_rules_sha256=raw_rules_sha256,
    )
    projection = (
        result_authority,
        natural_completion,
        retirement_after_point,
        walkover_before_point,
        default_after_point,
        disqualification_after_point,
        cancellation,
        postponement,
        abandonment,
        amendment,
        void_treatment,
        raw_rules_sha256,
    )
    return sha256(
        b"INCI-BINDING-SETTLEMENT-PROJECTION-SHA256-V1\0"
        + canonical_expert_bytes(projection)
    ).hexdigest()


def compute_membership_projection_sha256(
    *,
    series_ticker: str,
    event_ticker: str,
    event_id: str,
    market_ticker: str,
    market_id: str,
    product: str,
    event_catalog_sha256: str,
    membership_source_id: str,
    membership_source_version: str,
    membership_captured_wall_ns: int,
    membership_evidence_sha256: str,
) -> str:
    _ticker(series_ticker, "series_ticker")
    _ticker(event_ticker, "event_ticker")
    _safe_id(event_id, "event_id")
    _ticker(market_ticker, "market_ticker")
    _safe_id(market_id, "market_id")
    _string(product, "product")
    if product != "match_winner":
        raise ExpertContractError("product")
    _sha256(event_catalog_sha256, "event_catalog_sha256")
    _safe_id(membership_source_id, "membership_source_id")
    _safe_id(
        membership_source_version,
        "membership_source_version",
    )
    _integer(
        membership_captured_wall_ns,
        "membership_captured_wall_ns",
    )
    _sha256(
        membership_evidence_sha256,
        "membership_evidence_sha256",
    )
    projection = (
        series_ticker,
        event_ticker,
        event_id,
        market_ticker,
        market_id,
        product,
        event_catalog_sha256,
        membership_source_id,
        membership_source_version,
        membership_captured_wall_ns,
        membership_evidence_sha256,
    )
    return sha256(
        b"INCI-BINDING-MEMBERSHIP-PROJECTION-SHA256-V1\0"
        + canonical_expert_bytes(projection)
    ).hexdigest()


def compute_binding_review_evidence_sha256(
    manifest_pin: ArtifactPin,
    bindings: tuple[MatchBinding, ...],
    metadata: tuple[BindingMetadata, ...],
) -> str:
    _exact(manifest_pin, ArtifactPin, "manifest_pin")
    _exact_tuple(bindings, MatchBinding, "bindings")
    _exact_tuple(metadata, BindingMetadata, "metadata")
    return sha256(
        b"INCI-BINDING-REVIEW-EVIDENCE-SHA256-V1\0"
        + canonical_expert_bytes((manifest_pin, bindings, metadata))
    ).hexdigest()


def canonical_binding_review_artifact_bytes(
    *,
    review_artifact_id: str,
    review_artifact_created_wall_ns: int,
    binding_artifact_id: str,
    binding_artifact_sha256: str,
    decision: str,
    reviewer_id: str,
    reviewed_wall_ns: int,
    review_evidence_sha256: str,
) -> bytes:
    _safe_id(review_artifact_id, "review_artifact_id")
    _integer(
        review_artifact_created_wall_ns,
        "review_artifact_created_wall_ns",
    )
    _safe_id(binding_artifact_id, "binding_artifact_id")
    _sha256(binding_artifact_sha256, "binding_artifact_sha256")
    _safe_id(decision, "decision")
    _safe_id(reviewer_id, "reviewer_id")
    _integer(reviewed_wall_ns, "reviewed_wall_ns")
    _sha256(review_evidence_sha256, "review_evidence_sha256")
    return json.dumps(
        {
            "schema_version": 1,
            "artifact_id": review_artifact_id,
            "artifact_created_wall_ns": review_artifact_created_wall_ns,
            "binding_artifact_id": binding_artifact_id,
            "binding_artifact_sha256": binding_artifact_sha256,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "reviewed_wall_ns": reviewed_wall_ns,
            "review_evidence_sha256": review_evidence_sha256,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def compute_binding_review_artifact_sha256(
    *,
    review_artifact_id: str,
    review_artifact_created_wall_ns: int,
    binding_artifact_id: str,
    binding_artifact_sha256: str,
    decision: str,
    reviewer_id: str,
    reviewed_wall_ns: int,
    review_evidence_sha256: str,
) -> str:
    return sha256(
        canonical_binding_review_artifact_bytes(
            review_artifact_id=review_artifact_id,
            review_artifact_created_wall_ns=(
                review_artifact_created_wall_ns
            ),
            binding_artifact_id=binding_artifact_id,
            binding_artifact_sha256=binding_artifact_sha256,
            decision=decision,
            reviewer_id=reviewer_id,
            reviewed_wall_ns=reviewed_wall_ns,
            review_evidence_sha256=review_evidence_sha256,
        )
    ).hexdigest()


def compute_binding_universe_sha256(
    manifest_pin: ArtifactPin,
    review: BindingReviewDecision,
    bindings: tuple[MatchBinding, ...],
    metadata: tuple[BindingMetadata, ...],
) -> str:
    _exact(manifest_pin, ArtifactPin, "manifest_pin")
    _exact(review, BindingReviewDecision, "review")
    _exact_tuple(bindings, MatchBinding, "bindings")
    _exact_tuple(metadata, BindingMetadata, "metadata")
    return sha256(
        b"INCI-BINDING-UNIVERSE-SHA256-V1\0"
        + canonical_expert_bytes(
            (
                manifest_pin.artifact_sha256,
                review,
                bindings,
                metadata,
            )
        )
    ).hexdigest()


def _validate_player_bijection(
    occurrences: list[tuple[str, str, str, str]],
) -> None:
    provider_to_canonical: dict[tuple[str, str, str], str] = {}
    for source_id, lineage, provider_id, canonical_id in occurrences:
        key = (source_id, lineage, provider_id)
        existing = provider_to_canonical.get(key)
        if existing is not None and existing != canonical_id:
            raise ExpertContractError(
                "binding_manifest_provider_player_collision"
            )
        provider_to_canonical[key] = canonical_id
    canonical_to_provider: dict[tuple[str, str, str], str] = {}
    for source_id, lineage, provider_id, canonical_id in occurrences:
        key = (source_id, lineage, canonical_id)
        existing = canonical_to_provider.get(key)
        if existing is not None and existing != provider_id:
            raise ExpertContractError(
                "binding_manifest_canonical_player_collision"
            )
        canonical_to_provider[key] = provider_id


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    universe_sha256: str
    max_score_age_ns: int
    max_book_age_ns: int
    max_lifecycle_age_ns: int
    max_score_book_skew_ns: int
    max_clock_uncertainty_ns: int
    large_book_move_threshold: Decimal
    explanation_window_ns: int
    minimum_close_horizon_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, SyncPolicy)
        _sha256(self.universe_sha256, "universe_sha256")
        _integer(self.max_score_age_ns, "max_score_age_ns", positive=True)
        _integer(self.max_book_age_ns, "max_book_age_ns", positive=True)
        _integer(
            self.max_lifecycle_age_ns,
            "max_lifecycle_age_ns",
            positive=True,
        )
        _integer(self.max_score_book_skew_ns, "max_score_book_skew_ns")
        _integer(self.max_clock_uncertainty_ns, "max_clock_uncertainty_ns")
        _probability(
            self.large_book_move_threshold,
            "large_book_move_threshold",
        )
        _integer(
            self.explanation_window_ns,
            "explanation_window_ns",
            positive=True,
        )
        _integer(self.minimum_close_horizon_ns, "minimum_close_horizon_ns")


@dataclass(frozen=True, slots=True)
class TrustedSnapshot:
    decision_sequence: int
    decision_time: PairedTimeObservation
    tennis: TennisState
    book: BookState
    binding: MatchBinding
    sync_policy_sha256: str
    causal_provider_revision: int | None = None

    def __post_init__(self) -> None:
        _exact_self(self, TrustedSnapshot)
        _integer(self.decision_sequence, "decision_sequence", positive=True)
        _exact(self.decision_time, PairedTimeObservation, "decision_time")
        _exact(self.tennis, TennisState, "tennis")
        _exact(self.book, BookState, "book")
        _exact(self.binding, MatchBinding, "binding")
        _sha256(self.sync_policy_sha256, "sync_policy_sha256")
        if self.causal_provider_revision is not None:
            _integer(
                self.causal_provider_revision,
                "causal_provider_revision",
            )
            if self.causal_provider_revision > self.tennis.revision:
                raise ExpertContractError("causal_provider_revision")
        if (
            self.tennis.provider_match_id != self.binding.provider_match_id
            or self.tennis.home_player_id
            != self.binding.provider_home_player_id
            or self.tennis.away_player_id
            != self.binding.provider_away_player_id
            or self.tennis.provider_source_id != self.binding.provider_source_id
            or self.tennis.revision_domain_id != self.binding.revision_domain_id
            or self.tennis.source_lineage_sha256
            != self.binding.source_lineage_sha256
            or self.tennis.match_format is not self.binding.match_format
            or self.tennis.scheduled_start_wall_ns
            != self.binding.scheduled_start_wall_ns
        ):
            raise ExpertContractError("binding_mismatch")
        if self.book.ticker not in {
            self.binding.home_market_ticker,
            self.binding.away_market_ticker,
        }:
            raise ExpertContractError("book_ticker")
        if (
            self.tennis.snapshot_complete is not True
            or self.tennis.block_reason is not None
            or self.tennis.status is not MatchStatus.LIVE
            or self.tennis.server_for_next_point is None
            or self.tennis.winner is not None
            or self.tennis.retired_side is not None
        ):
            raise ExpertContractError("tennis_untrusted")
        if (
            self.book.trusted is not True
            or self.book.sequence_gap is not False
            or self.book.market_status is not MarketStatus.OPEN
            or not (self.book.yes_bids or self.book.no_bids)
        ):
            raise ExpertContractError("book_untrusted")
        if self.decision_time.monotonic_ns < max(
            self.tennis.last_received_monotonic_ns,
            self.book.book_observed_monotonic_ns,
            self.book.lifecycle_observed_monotonic_ns,
        ):
            raise ExpertContractError("decision_time")
        if self.decision_time.wall_ns >= self.book.scheduled_close_wall_ns:
            raise ExpertContractError("decision_time")


@dataclass(frozen=True, slots=True)
class OpportunityFrame:
    opportunity_id: str
    universe_sha256: str
    canonical_match_id: str
    ticker: str
    decision_sequence: int
    decision_time: PairedTimeObservation
    binding_sha256: str
    provider_revision: int
    book_connection_epoch: int
    book_sequence: int
    snapshot_sha256: str
    snapshot: TrustedSnapshot

    def __post_init__(self) -> None:
        _exact_self(self, OpportunityFrame)
        _sha256(self.opportunity_id, "opportunity_id")
        _sha256(self.universe_sha256, "universe_sha256")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _integer(self.decision_sequence, "decision_sequence", positive=True)
        _exact(self.decision_time, PairedTimeObservation, "decision_time")
        _sha256(self.binding_sha256, "binding_sha256")
        _integer(self.provider_revision, "provider_revision")
        _integer(self.book_connection_epoch, "book_connection_epoch")
        _integer(self.book_sequence, "book_sequence", positive=True)
        _sha256(self.snapshot_sha256, "snapshot_sha256")
        _exact(self.snapshot, TrustedSnapshot, "snapshot")
        binding_digest = expert_contract_sha256(self.snapshot.binding)
        snapshot_digest = expert_contract_sha256(self.snapshot)
        if (
            self.canonical_match_id
            != self.snapshot.binding.canonical_match_id
            or self.ticker != self.snapshot.book.ticker
            or self.decision_sequence != self.snapshot.decision_sequence
            or self.decision_time != self.snapshot.decision_time
            or self.binding_sha256 != binding_digest
            or self.provider_revision != self.snapshot.tennis.revision
            or self.book_connection_epoch
            != self.snapshot.book.connection_epoch
            or self.book_sequence != self.snapshot.book.sequence
            or self.snapshot_sha256 != snapshot_digest
        ):
            raise ExpertContractError("opportunity_snapshot_mismatch")
        identity = {
            "universe_sha256": self.universe_sha256,
            "canonical_match_id": self.snapshot.binding.canonical_match_id,
            "ticker": self.snapshot.book.ticker,
            "decision_sequence": self.snapshot.decision_sequence,
            "decision_time": self.snapshot.decision_time,
            "binding_sha256": binding_digest,
            "provider_revision": self.snapshot.tennis.revision,
            "book_connection_epoch": self.snapshot.book.connection_epoch,
            "book_sequence": self.snapshot.book.sequence,
            "snapshot_sha256": snapshot_digest,
        }
        expected = sha256(
            b"INCI-OPPORTUNITY-ID-V1\0" + canonical_expert_bytes(identity)
        ).hexdigest()
        if self.opportunity_id != expected:
            raise ExpertContractError("opportunity_id")


@dataclass(frozen=True, slots=True)
class FairValueEstimate:
    player_side: PlayerSide
    fair_probability: Decimal
    lower_probability: Decimal
    upper_probability: Decimal
    supported: bool
    stratum: str
    model_sha256: str
    prematch_artifact_sha256: str
    feature_definition_sha256: str
    feature_vector_sha256: str
    calibration_artifact_sha256: str | None = None
    abstention_reason: DecisionReason | None = None

    def __post_init__(self) -> None:
        _exact_self(self, FairValueEstimate)
        _exact(self.player_side, PlayerSide, "player_side")
        _probability(self.fair_probability, "fair_probability")
        _probability(self.lower_probability, "lower_probability")
        _probability(self.upper_probability, "upper_probability")
        with localcontext(_DECIMAL_CONTEXT):
            if not (
                self.lower_probability
                <= self.fair_probability
                <= self.upper_probability
            ):
                raise ExpertContractError("probability_interval")
        _boolean(self.supported, "supported")
        _safe_id(self.stratum, "stratum")
        _sha256(self.model_sha256, "model_sha256")
        _sha256(
            self.prematch_artifact_sha256,
            "prematch_artifact_sha256",
        )
        _sha256(
            self.feature_definition_sha256,
            "feature_definition_sha256",
        )
        _sha256(self.feature_vector_sha256, "feature_vector_sha256")
        if self.calibration_artifact_sha256 is not None:
            _sha256(
                self.calibration_artifact_sha256,
                "calibration_artifact_sha256",
            )
        _optional_exact(
            self.abstention_reason,
            DecisionReason,
            "abstention_reason",
        )
        if self.supported:
            if self.abstention_reason is not None:
                raise ExpertContractError("abstention_reason")
        elif self.abstention_reason not in _MODEL_ABSTENTION_REASONS:
            raise ExpertContractError("abstention_reason")


@dataclass(frozen=True, slots=True)
class PolicyPathEstimate:
    path_id: str
    probability: Decimal
    filled_quantity: Decimal
    residual_quantity: Decimal
    net_pnl: Decimal
    exit_price: Decimal | None = None

    def __post_init__(self) -> None:
        _exact_self(self, PolicyPathEstimate)
        _safe_id(self.path_id, "path_id")
        _probability(self.probability, "probability")
        _quantity(self.filled_quantity, "filled_quantity")
        _quantity(self.residual_quantity, "residual_quantity")
        if self.residual_quantity > self.filled_quantity:
            raise ExpertContractError("residual_quantity")
        _decimal(self.net_pnl, "net_pnl")
        if self.exit_price is not None:
            _probability(self.exit_price, "exit_price")


@dataclass(frozen=True, slots=True)
class PolicyEstimate:
    opportunity_id: str
    canonical_match_id: str
    ticker: str
    contract_side: ContractSide
    player_side: PlayerSide
    quantity: Decimal
    limit_price: Decimal
    executable_quantity: Decimal
    paths: tuple[PolicyPathEstimate, ...]
    probability_tolerance: Decimal
    expected_net_pnl: Decimal
    lower_expected_net_pnl: Decimal
    upper_expected_net_pnl: Decimal
    fair_value_sha256: str
    fee_schedule_sha256: str
    policy_artifact_sha256: str
    supported: bool
    abstention_reason: DecisionReason | None

    def __post_init__(self) -> None:
        _exact_self(self, PolicyEstimate)
        _sha256(self.opportunity_id, "opportunity_id")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _ticker(self.ticker, "ticker")
        _exact(self.contract_side, ContractSide, "contract_side")
        _exact(self.player_side, PlayerSide, "player_side")
        _quantity(self.quantity, "quantity", positive=True)
        _probability(self.limit_price, "limit_price")
        _quantity(self.executable_quantity, "executable_quantity")
        if self.executable_quantity > self.quantity:
            raise ExpertContractError("executable_quantity")
        paths = _exact_tuple(self.paths, PolicyPathEstimate, "paths")
        path_ids = tuple(path.path_id for path in paths)  # type: ignore[attr-defined]
        if path_ids != tuple(sorted(path_ids)) or len(set(path_ids)) != len(path_ids):
            raise ExpertContractError("paths")
        for path in paths:
            assert type(path) is PolicyPathEstimate
            if (
                path.filled_quantity > self.executable_quantity
                or path.filled_quantity > self.quantity
            ):
                raise ExpertContractError("filled_quantity")
        _probability(self.probability_tolerance, "probability_tolerance")
        if self.probability_tolerance > Decimal("1E-12"):
            raise ExpertContractError("probability_tolerance")
        _decimal(self.expected_net_pnl, "expected_net_pnl")
        _decimal(self.lower_expected_net_pnl, "lower_expected_net_pnl")
        _decimal(self.upper_expected_net_pnl, "upper_expected_net_pnl")
        with localcontext(_DECIMAL_CONTEXT):
            if not (
                self.lower_expected_net_pnl
                <= self.expected_net_pnl
                <= self.upper_expected_net_pnl
            ):
                raise ExpertContractError("pnl_interval")
        _sha256(self.fair_value_sha256, "fair_value_sha256")
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        _sha256(self.policy_artifact_sha256, "policy_artifact_sha256")
        _boolean(self.supported, "supported")
        _optional_exact(
            self.abstention_reason,
            DecisionReason,
            "abstention_reason",
        )
        if self.supported:
            if not paths or self.abstention_reason is not None:
                raise ExpertContractError("supported")
            try:
                with localcontext(_DECIMAL_CONTEXT):
                    probability_sum = sum(
                        (path.probability for path in paths),
                        Decimal("0"),
                    )
                    if (
                        abs(probability_sum - Decimal("1"))
                        > self.probability_tolerance
                    ):
                        raise ExpertContractError("probability_sum")
                    weighted = sum(
                        (
                            path.probability * path.net_pnl
                            for path in paths
                        ),
                        Decimal("0"),
                    )
                    if weighted != self.expected_net_pnl:
                        raise ExpertContractError("expected_net_pnl")
            except DecimalException:
                raise ExpertContractError("aggregate_decimal") from None
        else:
            if (
                paths
                or self.abstention_reason not in _MODEL_ABSTENTION_REASONS
                or self.expected_net_pnl != Decimal("0")
                or self.lower_expected_net_pnl != Decimal("0")
                or self.upper_expected_net_pnl != Decimal("0")
            ):
                raise ExpertContractError("unsupported")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    opportunity_id: str
    canonical_match_id: str
    decision_sequence: int
    decision_time: PairedTimeObservation
    ticker: str | None
    contract_side: ContractSide | None
    player_side: PlayerSide | None
    quantity: Decimal
    limit_price: Decimal | None
    action: DecisionAction
    reason: DecisionReason
    decision_input_sha256: str
    policy_artifact_sha256: str
    fee_schedule_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, PolicyDecision)
        _sha256(self.opportunity_id, "opportunity_id")
        _safe_id(self.canonical_match_id, "canonical_match_id")
        _integer(self.decision_sequence, "decision_sequence", positive=True)
        _exact(self.decision_time, PairedTimeObservation, "decision_time")
        if self.ticker is not None:
            _ticker(self.ticker, "ticker")
        _optional_exact(self.contract_side, ContractSide, "contract_side")
        _optional_exact(self.player_side, PlayerSide, "player_side")
        _quantity(self.quantity, "quantity")
        if self.limit_price is not None:
            _probability(self.limit_price, "limit_price")
        _exact(self.action, DecisionAction, "action")
        _exact(self.reason, DecisionReason, "reason")
        _sha256(self.decision_input_sha256, "decision_input_sha256")
        _sha256(self.policy_artifact_sha256, "policy_artifact_sha256")
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        if self.action is DecisionAction.ABSTAIN:
            if (
                self.ticker is not None
                or self.contract_side is not None
                or self.player_side is not None
                or self.limit_price is not None
                or self.quantity != Decimal("0")
                or self.reason in _BUY_REASONS
                or self.reason in _SELL_REASONS
            ):
                raise ExpertContractError("abstain")
            return
        if (
            self.ticker is None
            or self.contract_side is None
            or self.player_side is None
            or self.limit_price is None
            or self.quantity <= Decimal("0")
        ):
            raise ExpertContractError("order_authority")
        if self.action is DecisionAction.PAPER_BUY:
            if self.reason not in _BUY_REASONS:
                raise ExpertContractError("reason")
        elif self.action is DecisionAction.PAPER_SELL:
            if self.reason not in _SELL_REASONS:
                raise ExpertContractError("reason")


_STRUCTURAL_SCHEMA_SPEC: Final[
    tuple[tuple[str, str, str], ...]
] = (
    (
        "session_manifest",
        "ExpertSessionManifestV1",
        "expert-session-manifest-v1.schema.json",
    ),
    (
        "journal_record",
        "ExpertJournalRecordV1",
        "expert-journal-record-v1.schema.json",
    ),
    (
        "parent_group",
        "ExpertJournalGroupV1",
        "expert-journal-group-v1.schema.json",
    ),
    (
        "session_terminal",
        "ExpertSessionTerminalV1",
        "expert-session-terminal-v1.schema.json",
    ),
)
_EVENT_SCHEMA_SPEC: Final[
    tuple[tuple[ExpertEventKindV1, int, str, str], ...]
] = (
    (
        ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
        1,
        "ExpertSynchronizationAppliedPayloadV1",
        "expert-synchronization-applied-v1.schema.json",
    ),
    (
        ExpertEventKindV1.OBSERVATION_IGNORED,
        1,
        "ExpertObservationIgnoredPayloadV1",
        "expert-observation-ignored-v1.schema.json",
    ),
    (
        ExpertEventKindV1.OBSERVATION_REJECTED,
        1,
        "ExpertObservationRejectedPayloadV1",
        "expert-observation-rejected-v1.schema.json",
    ),
)
_EVENT_SCHEMA_RESOURCE_SHA256_V1: Final[
    tuple[tuple[ExpertEventKindV1, str], ...]
] = (
    (
        ExpertEventKindV1.SYNCHRONIZATION_APPLIED,
        "b8a8cdb82ada61385864b88897c58340062304fb4c47c37d5bc3d5481b17e361",
    ),
    (
        ExpertEventKindV1.OBSERVATION_IGNORED,
        "033edd37c85bb05c8defcd5a2af649246572b8fd48682f80207854da7b2f1f8a",
    ),
    (
        ExpertEventKindV1.OBSERVATION_REJECTED,
        "8343568ac80eb5072404c2eac50bfe15412490a089c116bcc526ad23058a7137",
    ),
)
_NORMALIZER_ENTRY_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {"provider", "kalshi", "timer", "system"}
)
_REJECTED_SYNCHRONIZATION_REASONS: Final[
    frozenset[ExpertRejectReasonV1]
] = frozenset(
    {
        ExpertRejectReasonV1.SYNCHRONIZATION_SESSION_DRIFT,
        ExpertRejectReasonV1.REDUCER_EXCEPTION,
        ExpertRejectReasonV1.PRIOR_OUTCOME_HALTED,
    }
)


def compute_expert_provider_source_lineage_sha256(
    provider_id: str,
    product_tier: str,
    source_lineage_id: str,
    provider_manifest_canonical_sha256: str,
) -> str:
    _safe_id(provider_id, "provider_id")
    _safe_id(product_tier, "product_tier")
    _safe_id(source_lineage_id, "source_lineage_id")
    _sha256(
        provider_manifest_canonical_sha256,
        "provider_manifest_canonical_sha256",
    )
    return _domain_sha256(
        b"INCI-EXPERT-PROVIDER-SOURCE-LINEAGE-V1\0",
        (
            provider_id,
            product_tier,
            source_lineage_id,
            provider_manifest_canonical_sha256,
        ),
    )


def expert_event_schema_resource_sha256(
    event_kind: ExpertEventKindV1,
) -> str:
    _exact(event_kind, ExpertEventKindV1, "event_kind")
    matches = tuple(
        digest
        for kind, digest in _EVENT_SCHEMA_RESOURCE_SHA256_V1
        if kind is event_kind
    )
    if len(matches) != 1:
        raise ExpertContractError("event_schema_registry")
    return matches[0]


@dataclass(frozen=True, slots=True)
class ExpertCurrentEnvironmentV1:
    schema_version: int
    phase1_code_sha256: str
    phase1_adapter_code_sha256: str
    expert_code_sha256: str
    io_code_sha256: str
    expert_adapter_code_sha256: str
    runtime_code_sha256: str
    dependency_lock_sha256: str
    python_runtime_sha256: str
    normalizer_registry_sha256: str
    structural_schema_bundle_sha256: str
    event_schema_bundle_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertCurrentEnvironmentV1)
        _schema_version(self.schema_version)
        for item in fields(self)[1:]:
            _sha256(getattr(self, item.name), item.name)


@dataclass(frozen=True, slots=True)
class ExpertProviderDomainBindingV1:
    schema_version: int
    phase1_session_manifest_sha256: str
    match_binding_universe_sha256: str
    provider_id: str
    product_tier: str
    source_lineage_id: str
    provider_manifest_canonical_sha256: str
    provider_source_lineage_sha256: str
    revision_domain_id: str
    provider_domain_binding_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertProviderDomainBindingV1)
        _schema_version(self.schema_version)
        _sha256(
            self.phase1_session_manifest_sha256,
            "phase1_session_manifest_sha256",
        )
        _sha256(
            self.match_binding_universe_sha256,
            "match_binding_universe_sha256",
        )
        _safe_id(self.provider_id, "provider_id")
        _safe_id(self.product_tier, "product_tier")
        _safe_id(self.source_lineage_id, "source_lineage_id")
        _sha256(
            self.provider_manifest_canonical_sha256,
            "provider_manifest_canonical_sha256",
        )
        _sha256(
            self.provider_source_lineage_sha256,
            "provider_source_lineage_sha256",
        )
        _safe_id(self.revision_domain_id, "revision_domain_id")
        _sha256(
            self.provider_domain_binding_sha256,
            "provider_domain_binding_sha256",
        )
        if (
            self.provider_source_lineage_sha256
            != compute_expert_provider_source_lineage_sha256(
                self.provider_id,
                self.product_tier,
                self.source_lineage_id,
                self.provider_manifest_canonical_sha256,
            )
        ):
            raise ExpertContractError("provider_source_lineage_sha256")
        _self_digest(
            self,
            digest_field="provider_domain_binding_sha256",
            domain=b"INCI-EXPERT-PROVIDER-DOMAIN-BINDING-V1\0",
            name="provider_domain_binding_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertRetentionBindingV1:
    schema_version: int
    session_id: str
    evidence_session_manifest_sha256: str
    provider_request_binding_sha256: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    retention_delete_by_ns: int
    access_expires_at_ns: int
    analysis_expires_at_ns: int
    retention_binding_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertRetentionBindingV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        for name in (
            "evidence_session_manifest_sha256",
            "provider_request_binding_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
        ):
            _sha256(getattr(self, name), name)
        for name in (
            "retention_delete_by_ns",
            "access_expires_at_ns",
            "analysis_expires_at_ns",
        ):
            _integer(getattr(self, name), name, positive=True)
        if self.access_expires_at_ns > self.analysis_expires_at_ns:
            raise ExpertContractError("retention_window")
        _sha256(self.retention_binding_sha256, "retention_binding_sha256")
        _self_digest(
            self,
            digest_field="retention_binding_sha256",
            domain=b"INCI-EXPERT-RETENTION-BINDING-V1\0",
            name="retention_binding_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertSchemaPinV1:
    schema_role: str
    contract_name: str
    resource_name: str
    schema_resource_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertSchemaPinV1)
        _one_of_strings(
            self.schema_role,
            frozenset(item[0] for item in _STRUCTURAL_SCHEMA_SPEC),
            "schema_role",
        )
        _safe_id(self.contract_name, "contract_name")
        _safe_id(self.resource_name, "resource_name")
        _sha256(self.schema_resource_sha256, "schema_resource_sha256")
        matching = tuple(
            item
            for item in _STRUCTURAL_SCHEMA_SPEC
            if item[0] == self.schema_role
        )
        if len(matching) != 1 or (
            self.contract_name,
            self.resource_name,
        ) != matching[0][1:]:
            raise ExpertContractError("schema_pin")


@dataclass(frozen=True, slots=True)
class ExpertStructuralSchemaBundleV1:
    schema_version: int
    pins: tuple[ExpertSchemaPinV1, ...]
    bundle_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertStructuralSchemaBundleV1)
        _schema_version(self.schema_version)
        pins = _exact_tuple(self.pins, ExpertSchemaPinV1, "pins")
        for pin in pins:
            ExpertSchemaPinV1.__post_init__(pin)
        if tuple(
            (pin.schema_role, pin.contract_name, pin.resource_name)
            for pin in pins
        ) != _STRUCTURAL_SCHEMA_SPEC:
            raise ExpertContractError("structural_schema_bundle")
        _sha256(self.bundle_sha256, "bundle_sha256")
        _self_digest(
            self,
            digest_field="bundle_sha256",
            domain=b"INCI-EXPERT-STRUCTURAL-SCHEMA-BUNDLE-V1\0",
            name="bundle_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertEventSchemaPinV1:
    event_kind: ExpertEventKindV1
    event_version: int
    payload_contract_name: str
    resource_name: str
    schema_resource_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertEventSchemaPinV1)
        _exact(self.event_kind, ExpertEventKindV1, "event_kind")
        _integer(self.event_version, "event_version", positive=True)
        _safe_id(self.payload_contract_name, "payload_contract_name")
        _safe_id(self.resource_name, "resource_name")
        _sha256(self.schema_resource_sha256, "schema_resource_sha256")
        matching = tuple(
            item
            for item in _EVENT_SCHEMA_SPEC
            if item[:2] == (self.event_kind, self.event_version)
        )
        if len(matching) != 1 or (
            self.payload_contract_name,
            self.resource_name,
        ) != matching[0][2:]:
            raise ExpertContractError("event_schema_pin")
        if (
            self.schema_resource_sha256
            != expert_event_schema_resource_sha256(self.event_kind)
        ):
            raise ExpertContractError("schema_resource_sha256")


@dataclass(frozen=True, slots=True)
class ExpertEventSchemaBundleV1:
    schema_version: int
    pins: tuple[ExpertEventSchemaPinV1, ...]
    bundle_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertEventSchemaBundleV1)
        _schema_version(self.schema_version)
        pins = _exact_tuple(self.pins, ExpertEventSchemaPinV1, "pins")
        for pin in pins:
            ExpertEventSchemaPinV1.__post_init__(pin)
        if tuple(
            (
                pin.event_kind,
                pin.event_version,
                pin.payload_contract_name,
                pin.resource_name,
            )
            for pin in pins
        ) != _EVENT_SCHEMA_SPEC:
            raise ExpertContractError("event_schema_bundle")
        _sha256(self.bundle_sha256, "bundle_sha256")
        _self_digest(
            self,
            digest_field="bundle_sha256",
            domain=b"INCI-EXPERT-EVENT-SCHEMA-BUNDLE-V1\0",
            name="bundle_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertNormalizerPinV1:
    normalizer_id: str
    source_kind: str
    source_id: str
    event_type: str
    event_version: int
    normalizer_code_sha256: str
    normalizer_schema_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertNormalizerPinV1)
        _safe_id(self.normalizer_id, "normalizer_id")
        _one_of_strings(
            self.source_kind,
            _NORMALIZER_ENTRY_SOURCE_KINDS | {"fallback"},
            "source_kind",
        )
        _safe_id(self.source_id, "source_id")
        _safe_id(self.event_type, "event_type")
        _integer(self.event_version, "event_version", positive=True)
        _sha256(self.normalizer_code_sha256, "normalizer_code_sha256")
        _sha256(
            self.normalizer_schema_sha256,
            "normalizer_schema_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertNormalizerRegistryV1:
    schema_version: int
    fallback: ExpertNormalizerPinV1
    entries: tuple[ExpertNormalizerPinV1, ...]
    registry_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertNormalizerRegistryV1)
        _schema_version(self.schema_version)
        _exact(self.fallback, ExpertNormalizerPinV1, "fallback")
        ExpertNormalizerPinV1.__post_init__(self.fallback)
        entries = _exact_tuple(
            self.entries,
            ExpertNormalizerPinV1,
            "entries",
        )
        if (
            self.fallback.normalizer_id != "task6-fallback-v1"
            or self.fallback.source_kind != "fallback"
            or self.fallback.source_id != "task6"
            or self.fallback.event_type != "unregistered"
            or self.fallback.event_version != 1
            or len(entries) > 256
        ):
            raise ExpertContractError("normalizer_registry")
        for pin in entries:
            ExpertNormalizerPinV1.__post_init__(pin)
            if pin.source_kind not in _NORMALIZER_ENTRY_SOURCE_KINDS:
                raise ExpertContractError("normalizer_registry")
        keys = tuple(
            (
                pin.source_kind,
                pin.source_id,
                pin.event_type,
                pin.event_version,
            )
            for pin in entries
        )
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ExpertContractError("normalizer_registry")
        _sha256(self.registry_sha256, "registry_sha256")
        _self_digest(
            self,
            digest_field="registry_sha256",
            domain=b"INCI-EXPERT-NORMALIZER-REGISTRY-V1\0",
            name="registry_sha256",
        )


@dataclass(frozen=True, slots=True, init=False)
class ExpertCollectedEnvironmentV1:
    current: ExpertCurrentEnvironmentV1
    normalizers: ExpertNormalizerRegistryV1
    structural_schemas: ExpertStructuralSchemaBundleV1
    event_schemas: ExpertEventSchemaBundleV1

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, ExpertCollectedEnvironmentV1)
        for value, cls, name in (
            (self.current, ExpertCurrentEnvironmentV1, "current"),
            (
                self.normalizers,
                ExpertNormalizerRegistryV1,
                "normalizers",
            ),
            (
                self.structural_schemas,
                ExpertStructuralSchemaBundleV1,
                "structural_schemas",
            ),
            (
                self.event_schemas,
                ExpertEventSchemaBundleV1,
                "event_schemas",
            ),
        ):
            _exact(value, cls, name)
            cls.__post_init__(value)  # type: ignore[attr-defined]
        if (
            self.current.normalizer_registry_sha256
            != self.normalizers.registry_sha256
            or self.current.structural_schema_bundle_sha256
            != self.structural_schemas.bundle_sha256
            or self.current.event_schema_bundle_sha256
            != self.event_schemas.bundle_sha256
        ):
            raise ExpertContractError("collected_environment")


def _create_expert_collected_environment_v1(
    **values: object,
) -> ExpertCollectedEnvironmentV1:
    return _private_construct(  # type: ignore[return-value]
        ExpertCollectedEnvironmentV1,
        values,
    )


@dataclass(frozen=True, slots=True)
class ExpertCapacityProofV1:
    schema_version: int
    match_binding_universe_sha256: str
    sync_policy_sha256: str
    maximum_output_count: int
    maximum_synchronization_state_bytes: int
    maximum_transition_payload_bytes: int
    maximum_rejected_payload_bytes: int
    maximum_event_payload_bytes: int
    maximum_group_payload_area_bytes: int
    maximum_group_metadata_bytes: int
    maximum_group_frame_bytes: int
    maximum_terminal_metadata_bytes: int
    maximum_terminal_frame_bytes: int
    emergency_reserve_bytes: int
    proof_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertCapacityProofV1)
        _schema_version(self.schema_version)
        _sha256(
            self.match_binding_universe_sha256,
            "match_binding_universe_sha256",
        )
        _sha256(self.sync_policy_sha256, "sync_policy_sha256")
        for item in fields(self)[3:-1]:
            _integer(getattr(self, item.name), item.name, positive=True)
        if (
            self.maximum_output_count != 64
            or self.maximum_event_payload_bytes != 131_064
            or self.maximum_group_payload_area_bytes != 8_388_608
            or self.maximum_group_metadata_bytes != 8_388_532
            or self.maximum_group_frame_bytes != 16_777_216
            or self.maximum_terminal_metadata_bytes != 1_048_576
            or self.maximum_terminal_frame_bytes != 1_048_652
            or self.emergency_reserve_bytes != 17_825_868
            or self.maximum_synchronization_state_bytes != 131_064
            or self.maximum_transition_payload_bytes != 131_064
            or self.maximum_rejected_payload_bytes != 131_064
        ):
            raise ExpertContractError("capacity_proof")
        _sha256(self.proof_sha256, "proof_sha256")
        _self_digest(
            self,
            digest_field="proof_sha256",
            domain=b"INCI-EXPERT-CAPACITY-PROOF-V1\0",
            name="proof_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertSessionManifestV1:
    schema_version: int
    session_id: str
    evidence_session_manifest_sha256: str
    evidence_session_start_record_sha256: str
    provider_id: str
    product_tier: str
    source_lineage_id: str
    provider_manifest_file_sha256: str
    provider_manifest_canonical_sha256: str
    entitlement_id_sha256: str
    provider_request_binding_sha256: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    provider_domain: ExpertProviderDomainBindingV1
    environment: ExpertCurrentEnvironmentV1
    retention: ExpertRetentionBindingV1
    match_binding_universe_sha256: str
    binding_raw_artifact_id: str
    binding_raw_artifact_sha256: str
    binding_review_artifact_id: str
    binding_review_artifact_sha256: str
    sync_policy_sha256: str
    initial_synchronization_sha256: str
    normalizers: ExpertNormalizerRegistryV1
    structural_schemas: ExpertStructuralSchemaBundleV1
    event_schemas: ExpertEventSchemaBundleV1
    capacity: ExpertCapacityProofV1
    artifact_pins: tuple[ArtifactPin, ...]
    manifest_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertSessionManifestV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        for name in (
            "evidence_session_manifest_sha256",
            "evidence_session_start_record_sha256",
            "provider_manifest_file_sha256",
            "provider_manifest_canonical_sha256",
            "entitlement_id_sha256",
            "provider_request_binding_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
            "match_binding_universe_sha256",
            "binding_raw_artifact_sha256",
            "binding_review_artifact_sha256",
            "sync_policy_sha256",
            "initial_synchronization_sha256",
        ):
            _sha256(getattr(self, name), name)
        for name in (
            "provider_id",
            "product_tier",
            "source_lineage_id",
            "binding_raw_artifact_id",
            "binding_review_artifact_id",
        ):
            _safe_id(getattr(self, name), name)
        for value, cls, name in (
            (
                self.provider_domain,
                ExpertProviderDomainBindingV1,
                "provider_domain",
            ),
            (
                self.environment,
                ExpertCurrentEnvironmentV1,
                "environment",
            ),
            (self.retention, ExpertRetentionBindingV1, "retention"),
            (
                self.normalizers,
                ExpertNormalizerRegistryV1,
                "normalizers",
            ),
            (
                self.structural_schemas,
                ExpertStructuralSchemaBundleV1,
                "structural_schemas",
            ),
            (
                self.event_schemas,
                ExpertEventSchemaBundleV1,
                "event_schemas",
            ),
            (self.capacity, ExpertCapacityProofV1, "capacity"),
        ):
            _exact(value, cls, name)
            cls.__post_init__(value)  # type: ignore[attr-defined]
        pins = _exact_tuple(self.artifact_pins, ArtifactPin, "artifact_pins")
        if len(pins) > 256:
            raise ExpertContractError("artifact_pins")
        for pin in pins:
            ArtifactPin.__post_init__(pin)
        if tuple(pin.artifact_id for pin in pins) != tuple(
            sorted(pin.artifact_id for pin in pins)
        ) or len({pin.artifact_id for pin in pins}) != len(pins):
            raise ExpertContractError("artifact_pins")
        if (
            self.provider_domain.phase1_session_manifest_sha256
            != self.evidence_session_manifest_sha256
            or self.provider_domain.match_binding_universe_sha256
            != self.match_binding_universe_sha256
            or self.provider_domain.provider_id != self.provider_id
            or self.provider_domain.product_tier != self.product_tier
            or self.provider_domain.source_lineage_id
            != self.source_lineage_id
            or self.provider_domain.provider_manifest_canonical_sha256
            != self.provider_manifest_canonical_sha256
            or self.retention.session_id != self.session_id
            or self.retention.evidence_session_manifest_sha256
            != self.evidence_session_manifest_sha256
            or self.retention.provider_request_binding_sha256
            != self.provider_request_binding_sha256
            or self.retention.permission_artifact_sha256
            != self.permission_artifact_sha256
            or self.retention.qualification_artifact_sha256
            != self.qualification_artifact_sha256
            or self.retention.qualification_trace_sha256
            != self.qualification_trace_sha256
            or self.environment.normalizer_registry_sha256
            != self.normalizers.registry_sha256
            or self.environment.structural_schema_bundle_sha256
            != self.structural_schemas.bundle_sha256
            or self.environment.event_schema_bundle_sha256
            != self.event_schemas.bundle_sha256
            or self.normalizers.entries
            or self.capacity.match_binding_universe_sha256
            != self.match_binding_universe_sha256
            or self.capacity.sync_policy_sha256 != self.sync_policy_sha256
        ):
            raise ExpertContractError("manifest_binding")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _self_digest(
            self,
            digest_field="manifest_sha256",
            domain=b"INCI-EXPERT-SESSION-MANIFEST-V1\0",
            name="manifest_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertSynchronizationDraftV1:
    evidence: SynchronizationInput

    def __post_init__(self) -> None:
        _exact_self(self, ExpertSynchronizationDraftV1)
        _exact(self.evidence, SynchronizationInput, "evidence")
        SynchronizationInput.__post_init__(self.evidence)


@dataclass(frozen=True, slots=True)
class ExpertIgnoredDraftV1:
    reason: ExpertIgnoreReasonV1

    def __post_init__(self) -> None:
        _exact_self(self, ExpertIgnoredDraftV1)
        _exact(self.reason, ExpertIgnoreReasonV1, "reason")


@dataclass(frozen=True, slots=True)
class ExpertRejectedDraftV1:
    reason: ExpertRejectReasonV1

    def __post_init__(self) -> None:
        _exact_self(self, ExpertRejectedDraftV1)
        _exact(self.reason, ExpertRejectReasonV1, "reason")


ExpertObservationDraftV1 = (
    ExpertSynchronizationDraftV1 | ExpertIgnoredDraftV1 | ExpertRejectedDraftV1
)


@dataclass(frozen=True, slots=True)
class ExpertParentEvidenceV1:
    session_id: str
    ingest_seq: int
    record_sha256: str
    event_type: str
    event_version: int
    local_wall_ns: int
    local_monotonic_ns: int
    clock_uncertainty_ns: int

    def __post_init__(self) -> None:
        _exact_self(self, ExpertParentEvidenceV1)
        _session_id(self.session_id)
        _integer(self.ingest_seq, "ingest_seq", positive=True)
        _sha256(self.record_sha256, "record_sha256")
        _safe_id(self.event_type, "event_type")
        _integer(self.event_version, "event_version", positive=True)
        _integer(self.local_wall_ns, "local_wall_ns")
        _integer(self.local_monotonic_ns, "local_monotonic_ns")
        _integer(self.clock_uncertainty_ns, "clock_uncertainty_ns")


def _validate_observation_binding(
    *,
    parent: object,
    parent_output_index: object,
    parent_output_count: object,
    normalizer_id: object,
    normalizer_code_sha256: object,
    normalizer_schema_sha256: object,
) -> None:
    _exact(parent, ExpertParentEvidenceV1, "parent")
    ExpertParentEvidenceV1.__post_init__(parent)
    index = _integer(parent_output_index, "parent_output_index")
    count = _integer(
        parent_output_count,
        "parent_output_count",
        positive=True,
    )
    if count > 64 or index >= count:
        raise ExpertContractError("parent_output")
    _safe_id(normalizer_id, "normalizer_id")
    _sha256(normalizer_code_sha256, "normalizer_code_sha256")
    _sha256(normalizer_schema_sha256, "normalizer_schema_sha256")


@dataclass(frozen=True, slots=True)
class ExpertSynchronizationObservationV1:
    parent: ExpertParentEvidenceV1
    parent_output_index: int
    parent_output_count: int
    normalizer_id: str
    normalizer_code_sha256: str
    normalizer_schema_sha256: str
    evidence: SynchronizationInput
    observation: PairedTimeObservation

    def __post_init__(self) -> None:
        _exact_self(self, ExpertSynchronizationObservationV1)
        _validate_observation_binding(
            parent=self.parent,
            parent_output_index=self.parent_output_index,
            parent_output_count=self.parent_output_count,
            normalizer_id=self.normalizer_id,
            normalizer_code_sha256=self.normalizer_code_sha256,
            normalizer_schema_sha256=self.normalizer_schema_sha256,
        )
        _exact(self.evidence, SynchronizationInput, "evidence")
        SynchronizationInput.__post_init__(self.evidence)
        _exact(self.observation, PairedTimeObservation, "observation")
        PairedTimeObservation.__post_init__(self.observation)
        if self.observation != PairedTimeObservation(
            wall_ns=self.parent.local_wall_ns,
            monotonic_ns=self.parent.local_monotonic_ns,
            clock_uncertainty_ns=self.parent.clock_uncertainty_ns,
        ):
            raise ExpertContractError("observation_time")


@dataclass(frozen=True, slots=True)
class ExpertIgnoredObservationV1:
    parent: ExpertParentEvidenceV1
    parent_output_index: int
    parent_output_count: int
    normalizer_id: str
    normalizer_code_sha256: str
    normalizer_schema_sha256: str
    reason: ExpertIgnoreReasonV1

    def __post_init__(self) -> None:
        _exact_self(self, ExpertIgnoredObservationV1)
        _validate_observation_binding(
            parent=self.parent,
            parent_output_index=self.parent_output_index,
            parent_output_count=self.parent_output_count,
            normalizer_id=self.normalizer_id,
            normalizer_code_sha256=self.normalizer_code_sha256,
            normalizer_schema_sha256=self.normalizer_schema_sha256,
        )
        _exact(self.reason, ExpertIgnoreReasonV1, "reason")
        if self.parent_output_index != 0 or self.parent_output_count != 1:
            raise ExpertContractError("ignored_observation_shape")


@dataclass(frozen=True, slots=True)
class ExpertRejectedObservationV1:
    parent: ExpertParentEvidenceV1
    parent_output_index: int
    parent_output_count: int
    normalizer_id: str
    normalizer_code_sha256: str
    normalizer_schema_sha256: str
    reason: ExpertRejectReasonV1

    def __post_init__(self) -> None:
        _exact_self(self, ExpertRejectedObservationV1)
        _validate_observation_binding(
            parent=self.parent,
            parent_output_index=self.parent_output_index,
            parent_output_count=self.parent_output_count,
            normalizer_id=self.normalizer_id,
            normalizer_code_sha256=self.normalizer_code_sha256,
            normalizer_schema_sha256=self.normalizer_schema_sha256,
        )
        _exact(self.reason, ExpertRejectReasonV1, "reason")
        if self.parent_output_index != 0 or self.parent_output_count != 1:
            raise ExpertContractError("rejected_observation_shape")


ExpertObservationV1 = (
    ExpertSynchronizationObservationV1
    | ExpertIgnoredObservationV1
    | ExpertRejectedObservationV1
)


@dataclass(frozen=True, slots=True)
class ExpertStateV1:
    schema_version: int
    session_id: str
    expert_manifest_sha256: str
    match_binding_universe_sha256: str
    sync_policy_sha256: str
    initial_synchronization_sha256: str
    synchronization: SynchronizationSessionState
    rejected_parent_count: int
    halted: bool
    halt_reason: ExpertRejectReasonV1 | None

    def __post_init__(self) -> None:
        _exact_self(self, ExpertStateV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        for name in (
            "expert_manifest_sha256",
            "match_binding_universe_sha256",
            "sync_policy_sha256",
            "initial_synchronization_sha256",
        ):
            _sha256(getattr(self, name), name)
        _exact(
            self.synchronization,
            SynchronizationSessionState,
            "synchronization",
        )
        SynchronizationSessionState.__post_init__(self.synchronization)
        _integer(self.rejected_parent_count, "rejected_parent_count")
        _boolean(self.halted, "halted")
        _optional_exact(
            self.halt_reason,
            ExpertRejectReasonV1,
            "halt_reason",
        )
        if (
            self.synchronization.universe_sha256
            != self.match_binding_universe_sha256
            or self.synchronization.sync_policy_sha256
            != self.sync_policy_sha256
            or self.halted != (self.halt_reason is not None)
            or (self.rejected_parent_count == 0 and self.halted)
        ):
            raise ExpertContractError("expert_state")


@dataclass(frozen=True, slots=True)
class ExpertSynchronizationAppliedPayloadV1:
    observation: ExpertSynchronizationObservationV1
    transition: SynchronizationTransitionResult

    def __post_init__(self) -> None:
        _exact_self(self, ExpertSynchronizationAppliedPayloadV1)
        _exact(
            self.observation,
            ExpertSynchronizationObservationV1,
            "observation",
        )
        _exact(
            self.transition,
            SynchronizationTransitionResult,
            "transition",
        )
        ExpertSynchronizationObservationV1.__post_init__(self.observation)
        SynchronizationTransitionResult.__post_init__(self.transition)
        if (
            self.transition.input != self.observation.evidence
            or self.transition.input_sha256
            != expert_contract_sha256(self.observation.evidence)
            or self.transition.observation != self.observation.observation
            or self.transition.state.last_observation
            != self.observation.observation
        ):
            raise ExpertContractError("synchronization_payload")


@dataclass(frozen=True, slots=True)
class ExpertObservationIgnoredPayloadV1:
    observation: ExpertIgnoredObservationV1

    def __post_init__(self) -> None:
        _exact_self(self, ExpertObservationIgnoredPayloadV1)
        _exact(
            self.observation,
            ExpertIgnoredObservationV1,
            "observation",
        )
        ExpertIgnoredObservationV1.__post_init__(self.observation)


@dataclass(frozen=True, slots=True)
class ExpertObservationRejectedPayloadV1:
    observation: (
        ExpertSynchronizationObservationV1
        | ExpertIgnoredObservationV1
        | ExpertRejectedObservationV1
    )
    reason: ExpertRejectReasonV1

    def __post_init__(self) -> None:
        _exact_self(self, ExpertObservationRejectedPayloadV1)
        if type(self.observation) not in (
            ExpertSynchronizationObservationV1,
            ExpertIgnoredObservationV1,
            ExpertRejectedObservationV1,
        ):
            raise TypeError("observation")
        type(self.observation).__post_init__(self.observation)
        _exact(self.reason, ExpertRejectReasonV1, "reason")
        if type(self.observation) is ExpertRejectedObservationV1:
            valid = self.reason is self.observation.reason
        elif type(self.observation) is ExpertSynchronizationObservationV1:
            valid = self.reason in _REJECTED_SYNCHRONIZATION_REASONS
        else:
            valid = self.reason is ExpertRejectReasonV1.STATIC_SESSION_HALT
        if not valid:
            raise ExpertContractError("rejected_payload")


ExpertEventPayloadV1 = (
    ExpertSynchronizationAppliedPayloadV1
    | ExpertObservationIgnoredPayloadV1
    | ExpertObservationRejectedPayloadV1
)


@dataclass(frozen=True, slots=True)
class ExpertOutcomeDraftV1:
    event_kind: ExpertEventKindV1
    event_version: int
    event_schema_sha256: str
    payload: ExpertEventPayloadV1
    prior_expert_state_sha256: str
    post_state: ExpertStateV1
    post_expert_state_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertOutcomeDraftV1)
        _exact(self.event_kind, ExpertEventKindV1, "event_kind")
        if self.event_version != 1 or type(self.event_version) is not int:
            raise ExpertContractError("event_version")
        _sha256(self.event_schema_sha256, "event_schema_sha256")
        if self.event_schema_sha256 != expert_event_schema_resource_sha256(
            self.event_kind
        ):
            raise ExpertContractError("event_schema_sha256")
        expected_payload_type = {
            ExpertEventKindV1.SYNCHRONIZATION_APPLIED: (
                ExpertSynchronizationAppliedPayloadV1
            ),
            ExpertEventKindV1.OBSERVATION_IGNORED: (
                ExpertObservationIgnoredPayloadV1
            ),
            ExpertEventKindV1.OBSERVATION_REJECTED: (
                ExpertObservationRejectedPayloadV1
            ),
        }[self.event_kind]
        _exact(self.payload, expected_payload_type, "payload")
        expected_payload_type.__post_init__(self.payload)
        _sha256(
            self.prior_expert_state_sha256,
            "prior_expert_state_sha256",
        )
        _exact(self.post_state, ExpertStateV1, "post_state")
        ExpertStateV1.__post_init__(self.post_state)
        _sha256(
            self.post_expert_state_sha256,
            "post_expert_state_sha256",
        )
        if self.post_expert_state_sha256 != expert_state_sha256(
            self.post_state
        ):
            raise ExpertContractError("post_expert_state_sha256")


@dataclass(frozen=True, slots=True)
class ExpertReductionV1:
    prior_expert_state_sha256: str
    outcomes: tuple[ExpertOutcomeDraftV1, ...]
    final_state: ExpertStateV1
    final_expert_state_sha256: str
    halt_required: bool

    def __post_init__(self) -> None:
        _exact_self(self, ExpertReductionV1)
        _sha256(
            self.prior_expert_state_sha256,
            "prior_expert_state_sha256",
        )
        outcomes = _exact_tuple(
            self.outcomes,
            ExpertOutcomeDraftV1,
            "outcomes",
        )
        if not outcomes or len(outcomes) > 64:
            raise ExpertContractError("outcomes")
        expected_prior = self.prior_expert_state_sha256
        for outcome in outcomes:
            ExpertOutcomeDraftV1.__post_init__(outcome)
            if outcome.prior_expert_state_sha256 != expected_prior:
                raise ExpertContractError("outcome_state_chain")
            expected_prior = outcome.post_expert_state_sha256
        _exact(self.final_state, ExpertStateV1, "final_state")
        ExpertStateV1.__post_init__(self.final_state)
        _sha256(
            self.final_expert_state_sha256,
            "final_expert_state_sha256",
        )
        _boolean(self.halt_required, "halt_required")
        if (
            outcomes[-1].post_state != self.final_state
            or expected_prior != self.final_expert_state_sha256
            or self.final_expert_state_sha256
            != expert_state_sha256(self.final_state)
            or self.halt_required != self.final_state.halted
        ):
            raise ExpertContractError("reduction_final_state")


@dataclass(frozen=True, slots=True)
class ExpertJournalCursorV1:
    schema_version: int
    session_id: str
    group_count: int
    record_count: int
    last_parent_ingest_seq: int
    last_parent_record_sha256: str
    expert_seq: int
    expert_record_sha256: str
    expert_state_sha256: str
    expert_trace_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertJournalCursorV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        for name in (
            "group_count",
            "record_count",
            "last_parent_ingest_seq",
            "expert_seq",
        ):
            _integer(getattr(self, name), name)
        for name in (
            "last_parent_record_sha256",
            "expert_record_sha256",
            "expert_state_sha256",
            "expert_trace_sha256",
        ):
            _sha256(getattr(self, name), name)
        if (
            self.record_count < self.group_count
            or self.expert_seq != self.record_count
            or (self.group_count == 0) != (self.record_count == 0)
            or (self.group_count == 0)
            != (self.last_parent_ingest_seq == 0)
        ):
            raise ExpertContractError("journal_cursor")


@dataclass(frozen=True, slots=True)
class ExpertPayloadDescriptorV1:
    schema_version: int
    content_type: str
    payload_encoding: str
    payload_contract_name: str
    payload_length: int
    payload_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertPayloadDescriptorV1)
        _schema_version(self.schema_version)
        _one_of_strings(
            self.content_type,
            frozenset({"application/vnd.inci.expert+json"}),
            "content_type",
        )
        _one_of_strings(
            self.payload_encoding,
            frozenset({"canonical-json-v1"}),
            "payload_encoding",
        )
        _one_of_strings(
            self.payload_contract_name,
            frozenset(item[2] for item in _EVENT_SCHEMA_SPEC),
            "payload_contract_name",
        )
        _integer(self.payload_length, "payload_length")
        if self.payload_length > 131_064:
            raise ExpertContractError("payload_length")
        _sha256(self.payload_sha256, "payload_sha256")


@dataclass(frozen=True, slots=True)
class ExpertJournalRecordV1:
    schema_version: int
    session_id: str
    expert_manifest_sha256: str
    provider_request_binding_sha256: str
    match_binding_universe_sha256: str
    retention_binding_sha256: str
    expert_seq: int
    parent: ExpertParentEvidenceV1
    parent_output_index: int
    parent_output_count: int
    event_kind: ExpertEventKindV1
    event_version: int
    event_schema_sha256: str
    prior_expert_record_sha256: str
    prior_expert_state_sha256: str
    payload: ExpertPayloadDescriptorV1
    post_expert_state_sha256: str
    record_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertJournalRecordV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        for name in (
            "expert_manifest_sha256",
            "provider_request_binding_sha256",
            "match_binding_universe_sha256",
            "retention_binding_sha256",
            "event_schema_sha256",
            "prior_expert_record_sha256",
            "prior_expert_state_sha256",
            "post_expert_state_sha256",
        ):
            _sha256(getattr(self, name), name)
        _integer(self.expert_seq, "expert_seq", positive=True)
        _exact(self.parent, ExpertParentEvidenceV1, "parent")
        ExpertParentEvidenceV1.__post_init__(self.parent)
        _validate_observation_binding(
            parent=self.parent,
            parent_output_index=self.parent_output_index,
            parent_output_count=self.parent_output_count,
            normalizer_id="record-shape",
            normalizer_code_sha256="0" * 64,
            normalizer_schema_sha256="0" * 64,
        )
        _exact(self.event_kind, ExpertEventKindV1, "event_kind")
        if self.event_version != 1 or type(self.event_version) is not int:
            raise ExpertContractError("event_version")
        if self.event_schema_sha256 != expert_event_schema_resource_sha256(
            self.event_kind
        ):
            raise ExpertContractError("event_schema_sha256")
        _exact(self.payload, ExpertPayloadDescriptorV1, "payload")
        ExpertPayloadDescriptorV1.__post_init__(self.payload)
        expected_contract = {
            item[0]: item[2] for item in _EVENT_SCHEMA_SPEC
        }[self.event_kind]
        if self.payload.payload_contract_name != expected_contract:
            raise ExpertContractError("record_payload_contract")
        _sha256(self.record_sha256, "record_sha256")
        _self_digest(
            self,
            digest_field="record_sha256",
            domain=b"INCI-EXPERT-JOURNAL-RECORD-V1\0",
            name="record_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertTraceStepV1:
    schema_version: int
    expert_seq: int
    prior_trace_sha256: str
    expert_record_sha256: str
    post_expert_state_sha256: str
    post_trace_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertTraceStepV1)
        _schema_version(self.schema_version)
        _integer(self.expert_seq, "expert_seq", positive=True)
        for name in (
            "prior_trace_sha256",
            "expert_record_sha256",
            "post_expert_state_sha256",
            "post_trace_sha256",
        ):
            _sha256(getattr(self, name), name)
        _self_digest(
            self,
            digest_field="post_trace_sha256",
            domain=b"INCI-EXPERT-TRACE-STEP-V1\0",
            name="post_trace_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertJournalGroupV1:
    schema_version: int
    session_id: str
    expert_manifest_sha256: str
    group_sequence: int
    parent: ExpertParentEvidenceV1
    parent_output_count: int
    first_expert_seq: int
    prior_expert_record_sha256: str
    prior_expert_state_sha256: str
    records: tuple[ExpertJournalRecordV1, ...]
    trace_steps: tuple[ExpertTraceStepV1, ...]
    final_expert_record_sha256: str
    post_expert_state_sha256: str
    post_trace_sha256: str
    group_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertJournalGroupV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        _sha256(self.expert_manifest_sha256, "expert_manifest_sha256")
        _integer(self.group_sequence, "group_sequence", positive=True)
        _exact(self.parent, ExpertParentEvidenceV1, "parent")
        ExpertParentEvidenceV1.__post_init__(self.parent)
        _integer(
            self.parent_output_count,
            "parent_output_count",
            positive=True,
        )
        _integer(self.first_expert_seq, "first_expert_seq", positive=True)
        records = _exact_tuple(
            self.records,
            ExpertJournalRecordV1,
            "records",
        )
        traces = _exact_tuple(
            self.trace_steps,
            ExpertTraceStepV1,
            "trace_steps",
        )
        if (
            not records
            or len(records) > 64
            or len(records) != self.parent_output_count
            or len(traces) != len(records)
        ):
            raise ExpertContractError("group_shape")
        for name in (
            "prior_expert_record_sha256",
            "prior_expert_state_sha256",
            "final_expert_record_sha256",
            "post_expert_state_sha256",
            "post_trace_sha256",
        ):
            _sha256(getattr(self, name), name)
        expected_record = self.prior_expert_record_sha256
        expected_state = self.prior_expert_state_sha256
        expected_trace = traces[0].prior_trace_sha256
        for offset, (record, trace) in enumerate(
            zip(records, traces, strict=True)
        ):
            ExpertJournalRecordV1.__post_init__(record)
            ExpertTraceStepV1.__post_init__(trace)
            if (
                record.session_id != self.session_id
                or record.expert_manifest_sha256
                != self.expert_manifest_sha256
                or record.parent != self.parent
                or record.parent_output_count != self.parent_output_count
                or record.parent_output_index != offset
                or record.expert_seq != self.first_expert_seq + offset
                or record.prior_expert_record_sha256 != expected_record
                or record.prior_expert_state_sha256 != expected_state
                or trace.expert_seq != record.expert_seq
                or trace.prior_trace_sha256 != expected_trace
                or trace.expert_record_sha256 != record.record_sha256
                or trace.post_expert_state_sha256
                != record.post_expert_state_sha256
            ):
                raise ExpertContractError("group_chain")
            expected_record = record.record_sha256
            expected_state = record.post_expert_state_sha256
            expected_trace = trace.post_trace_sha256
        if (
            self.final_expert_record_sha256 != expected_record
            or self.post_expert_state_sha256 != expected_state
            or self.post_trace_sha256 != expected_trace
        ):
            raise ExpertContractError("group_final")
        _sha256(self.group_sha256, "group_sha256")
        _self_digest(
            self,
            digest_field="group_sha256",
            domain=b"INCI-EXPERT-JOURNAL-GROUP-V1\0",
            name="group_sha256",
        )


@dataclass(frozen=True, slots=True)
class ExpertSessionTerminalV1:
    schema_version: int
    session_id: str
    expert_manifest_sha256: str
    provider_request_binding_sha256: str
    match_binding_universe_sha256: str
    retention_binding_sha256: str
    evidence_terminal_ingest_seq: int
    evidence_terminal_record_sha256: str
    evidence_terminal_clean: bool
    evidence_terminal_reason: str
    evidence_raw_count: int
    evidence_derived_count: int
    expert_group_count: int
    expert_record_count: int
    last_parent_ingest_seq: int
    last_parent_record_sha256: str
    final_expert_seq: int
    final_expert_record_sha256: str
    final_expert_state_sha256: str
    final_expert_trace_sha256: str
    clean: bool
    reason: ExpertTerminalReasonV1
    research_evaluable: Literal[False]
    terminal_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ExpertSessionTerminalV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        for name in (
            "expert_manifest_sha256",
            "provider_request_binding_sha256",
            "match_binding_universe_sha256",
            "retention_binding_sha256",
            "evidence_terminal_record_sha256",
            "last_parent_record_sha256",
            "final_expert_record_sha256",
            "final_expert_state_sha256",
            "final_expert_trace_sha256",
        ):
            _sha256(getattr(self, name), name)
        for name in (
            "evidence_terminal_ingest_seq",
            "evidence_raw_count",
            "evidence_derived_count",
            "expert_group_count",
            "expert_record_count",
            "last_parent_ingest_seq",
            "final_expert_seq",
        ):
            _integer(getattr(self, name), name)
        _boolean(self.evidence_terminal_clean, "evidence_terminal_clean")
        _safe_id(self.evidence_terminal_reason, "evidence_terminal_reason")
        _boolean(self.clean, "clean")
        _exact(self.reason, ExpertTerminalReasonV1, "reason")
        if self.research_evaluable is not False:
            raise ExpertContractError("research_evaluable")
        permitted_evidence_reasons = (
            _PHASE1_CLEAN_TERMINAL_REASONS
            if self.evidence_terminal_clean
            else _PHASE1_HALTED_TERMINAL_REASONS
        )
        expected_evidence_terminal_ingest_seq = (
            2 + self.evidence_raw_count + self.evidence_derived_count
        )
        expected_last_parent_ingest_seq = (
            0
            if self.evidence_raw_count == 0
            else 2 * self.evidence_raw_count
        )
        if (
            self.evidence_terminal_ingest_seq
            != expected_evidence_terminal_ingest_seq
            or self.last_parent_ingest_seq
            != expected_last_parent_ingest_seq
            or self.evidence_terminal_reason
            not in permitted_evidence_reasons
            or self.evidence_raw_count != self.evidence_derived_count
            or self.evidence_raw_count != self.expert_group_count
            or self.expert_record_count < self.expert_group_count
            or self.final_expert_seq != self.expert_record_count
            or (
                self.last_parent_ingest_seq != 0
                and self.last_parent_ingest_seq
                >= self.evidence_terminal_ingest_seq
            )
            or (self.expert_group_count == 0)
            != (self.expert_record_count == 0)
            or (self.expert_group_count == 0)
            != (self.last_parent_ingest_seq == 0)
            or self.clean != self.evidence_terminal_clean
            or (
                self.clean
                and self.evidence_terminal_reason != self.reason.value
            )
            or (
                self.clean
                and self.reason
                not in (
                    ExpertTerminalReasonV1.OPERATOR_STOP,
                    ExpertTerminalReasonV1.SESSION_END,
                )
            )
            or (
                not self.clean
                and self.reason is not ExpertTerminalReasonV1.EXPERT_HALT
            )
        ):
            raise ExpertContractError("terminal_alignment")
        _sha256(self.terminal_sha256, "terminal_sha256")
        _self_digest(
            self,
            digest_field="terminal_sha256",
            domain=b"INCI-EXPERT-SESSION-TERMINAL-V1\0",
            name="terminal_sha256",
        )


@dataclass(frozen=True, slots=True)
class DurableExpertAppendReceiptV1:
    session_id: str
    group_sequence: int
    group_sha256: str
    last_parent_record_sha256: str
    last_expert_seq: int
    final_expert_record_sha256: str
    post_expert_state_sha256: str
    post_expert_trace_sha256: str
    durable_end_offset: int

    def __post_init__(self) -> None:
        _exact_self(self, DurableExpertAppendReceiptV1)
        _session_id(self.session_id)
        _integer(self.group_sequence, "group_sequence", positive=True)
        _integer(self.last_expert_seq, "last_expert_seq", positive=True)
        _integer(self.durable_end_offset, "durable_end_offset", positive=True)
        for name in (
            "group_sha256",
            "last_parent_record_sha256",
            "final_expert_record_sha256",
            "post_expert_state_sha256",
            "post_expert_trace_sha256",
        ):
            _sha256(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class DurableExpertTerminalReceiptV1:
    session_id: str
    terminal_sha256: str
    terminal_frame_sequence: int
    durable_end_offset: int
    reserve_already_consumed: Literal[True]

    def __post_init__(self) -> None:
        _exact_self(self, DurableExpertTerminalReceiptV1)
        _session_id(self.session_id)
        _sha256(self.terminal_sha256, "terminal_sha256")
        _integer(
            self.terminal_frame_sequence,
            "terminal_frame_sequence",
            positive=True,
        )
        _integer(self.durable_end_offset, "durable_end_offset", positive=True)
        if self.reserve_already_consumed is not True:
            raise ExpertContractError("reserve_already_consumed")


@dataclass(frozen=True, slots=True)
class DurableExpertEmergencyReceiptV1:
    session_id: str
    group_receipt: DurableExpertAppendReceiptV1
    terminal_receipt: DurableExpertTerminalReceiptV1
    reserve_already_consumed: Literal[True]

    def __post_init__(self) -> None:
        _exact_self(self, DurableExpertEmergencyReceiptV1)
        _session_id(self.session_id)
        _exact(
            self.group_receipt,
            DurableExpertAppendReceiptV1,
            "group_receipt",
        )
        _exact(
            self.terminal_receipt,
            DurableExpertTerminalReceiptV1,
            "terminal_receipt",
        )
        DurableExpertAppendReceiptV1.__post_init__(self.group_receipt)
        DurableExpertTerminalReceiptV1.__post_init__(self.terminal_receipt)
        if (
            self.group_receipt.session_id != self.session_id
            or self.terminal_receipt.session_id != self.session_id
            or self.reserve_already_consumed is not True
        ):
            raise ExpertContractError("emergency_receipt")


@dataclass(frozen=True, slots=True)
class ExpertPurgeReportV1:
    due_sessions: tuple[str, ...]
    evidence_missing_sessions: tuple[str, ...]
    evidence_replaced_sessions: tuple[str, ...]
    recovered_markers: tuple[str, ...]

    def __post_init__(self) -> None:
        _exact_self(self, ExpertPurgeReportV1)
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not tuple:
                raise TypeError(item.name)
            for session in value:
                _session_id(session)
            if value != tuple(sorted(value)) or len(set(value)) != len(value):
                raise ExpertContractError(item.name)


def _private_construct(
    cls: type[object],
    values: dict[str, object],
) -> object:
    expected = tuple(item.name for item in fields(cls))
    if set(values) != set(expected):
        raise ExpertContractError("private_contract_fields")
    instance = object.__new__(cls)
    for name in expected:
        object.__setattr__(instance, name, values[name])
    validator = getattr(instance, "_validate")
    validator()
    return instance


@dataclass(frozen=True, slots=True, init=False)
class ExpertPhysicalFileIdentityV1:
    schema_version: int
    role: str
    device: int
    inode: int
    uid: int
    mode: int
    link_count: int
    size: int
    mtime_ns: int
    ctime_ns: int
    canonical_marker_sha256: str | None
    file_header_sha256: str | None
    session_anchor_sha256: str
    identity_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, ExpertPhysicalFileIdentityV1)
        _schema_version(self.schema_version)
        _one_of_strings(
            self.role,
            frozenset(
                {
                    "phase1_marker",
                    "phase1_wal",
                    "expert_marker",
                    "expert_journal",
                }
            ),
            "role",
        )
        for name in (
            "device",
            "inode",
            "uid",
            "mode",
            "link_count",
            "size",
            "mtime_ns",
            "ctime_ns",
        ):
            _integer(getattr(self, name), name)
        if self.link_count != 1:
            raise ExpertContractError("link_count")
        if self.canonical_marker_sha256 is not None:
            _sha256(
                self.canonical_marker_sha256,
                "canonical_marker_sha256",
            )
        if self.file_header_sha256 is not None:
            _sha256(self.file_header_sha256, "file_header_sha256")
        marker_role = self.role in {"phase1_marker", "expert_marker"}
        if marker_role != (
            self.canonical_marker_sha256 is not None
            and self.file_header_sha256 is None
        ):
            raise ExpertContractError("physical_identity_role")
        if not marker_role and (
            self.file_header_sha256 is None
            or self.canonical_marker_sha256 is not None
        ):
            raise ExpertContractError("physical_identity_role")
        _sha256(self.session_anchor_sha256, "session_anchor_sha256")
        _sha256(self.identity_sha256, "identity_sha256")
        _self_digest(
            self,
            digest_field="identity_sha256",
            domain=b"INCI-EXPERT-PHYSICAL-FILE-IDENTITY-V1\0",
            name="identity_sha256",
        )


def _create_expert_physical_file_identity_v1(
    **values: object,
) -> ExpertPhysicalFileIdentityV1:
    return _private_construct(  # type: ignore[return-value]
        ExpertPhysicalFileIdentityV1,
        values,
    )


_PHASE1_TERMINAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "terminal_version",
        "clean",
        "reason",
        "trace_sha256",
        "final_state_sha256",
        "record_count_before_terminal",
        "raw_count",
        "derived_count",
        "last_applied_raw_seq",
        "config_file_sha256",
        "config_canonical_sha256",
        "code_sha256",
        "session_manifest_sha256",
        "provider_manifest_file_sha256",
        "provider_manifest_canonical_sha256",
        "entitlement_id_sha256",
        "permission_artifact_sha256",
        "qualification_artifact_sha256",
        "qualification_trace_sha256",
        "adapter_code_sha256",
        "auth_contract_sha256",
        "quota_contract_sha256",
        "required_retention_until_ns",
        "research_evaluable",
    }
)
_PHASE1_CLEAN_TERMINAL_REASONS: Final[frozenset[str]] = frozenset(
    {"operator_stop", "session_end"}
)
_PHASE1_HALTED_TERMINAL_REASONS: Final[frozenset[str]] = frozenset(
    {
        "operator_halt",
        "initialization_failure",
        "capture_contract_violation",
        "provider_gate_denied",
        "retention_global_halt",
        "disk_low",
        "reducer_exception",
        "derived_validation_failure",
        "trace_exception",
        "ingress_backpressure",
        "ingress_owner_unresponsive",
    }
)
_IDENTITY_DIAGNOSTIC_ISSUES: Final[
    frozenset[ExpertReplayDiagnosticIssueV1]
] = frozenset(
    {
        ExpertReplayDiagnosticIssueV1.ENTRY_MISSING,
        ExpertReplayDiagnosticIssueV1.ENTRY_NOT_REGULAR,
        ExpertReplayDiagnosticIssueV1.ENTRY_IDENTITY_INVALID,
        ExpertReplayDiagnosticIssueV1.ENTRY_REPLACED,
    }
)


def _phase1_session_anchor_sha256(
    session_manifest_digest: str,
    session_start_record_digest: str,
) -> str:
    _sha256(session_manifest_digest, "session_manifest_sha256")
    _sha256(
        session_start_record_digest,
        "session_start_record_sha256",
    )
    return _domain_sha256(
        b"INCI-EXPERT-PHASE1-SESSION-ANCHOR-V1\0",
        (session_manifest_digest, session_start_record_digest),
    )


def _validate_phase1_control_shape(
    event: PersistedEvent,
    *,
    manifest: SessionManifest,
    ingest_seq: int,
    event_type: str,
) -> None:
    content_type = {
        "SESSION_START": "application/vnd.inci.session-manifest+json",
        "SESSION_HALT": "application/vnd.inci.session-terminal+json",
    }[event_type]
    if (
        event.record_kind is not RecordKind.CONTROL
        or event.ingest_seq != ingest_seq
        or event.session_id != manifest.session_id
        or event.event_type != event_type
        or event.event_version != 1
        or event.source_kind is not SourceKind.SYSTEM
        or event.source_id != "tennis-v1"
        or event.source_entity_id != manifest.session_id
        or event.endpoint_id is not None
        or event.endpoint_state is not ProvenanceState.ABSENT
        or event.channel_id != "session-control"
        or event.channel_state is not ProvenanceState.SAFE_ORIGINAL
        or event.request_id is not None
        or event.request_id_state is not ProvenanceState.ABSENT
        or event.source_wall_ns is not None
        or event.source_generated_ns is not None
        or event.local_wall_ns != manifest.created_wall_ns
        or event.local_monotonic_ns != 0
        or event.clock_uncertainty_ns != 0
        or event.connection_epoch != 0
        or event.provider_sequence is not None
        or event.parent_ingest_seq is not None
        or event.content_type != content_type
        or event.payload_encoding != "canonical-json-v1"
        or event.payload_transform != "identity-public-market-v1"
        or event.retention_delete_by_ns is not None
    ):
        raise ExpertContractError(
            "session_start"
            if event_type == "SESSION_START"
            else "evidence_terminal"
        )


def _validate_phase1_replay_result(
    result: ReplayResult,
    *,
    manifest: SessionManifest,
) -> None:
    _exact(result, ReplayResult, "replay_result")
    if result.state is not None:
        _exact(result.state, FoundationState, "replay_result")
        FoundationState.__post_init__(result.state)
        if (
            result.state.session_id != manifest.session_id
            or result.state.raw_count != result.raw_count
        ):
            raise ExpertContractError("replay_result")
    if result.trace_sha256 is not None:
        _sha256(result.trace_sha256, "replay_result")
    for name in ("raw_count", "derived_count"):
        _integer(getattr(result, name), "replay_result")
    for name in ("terminal_clean", "wal_valid", "exact_replay"):
        _boolean(getattr(result, name), "replay_result")
    _optional_exact(result.scan_issue, ScanIssue, "replay_result")
    _optional_exact(result.replay_mismatch, ReplayMismatch, "replay_result")
    if result.research_evaluable is not False:
        raise ExpertContractError("replay_result")
    expected_wal_valid = result.scan_issue in {
        None,
        ScanIssue.MISSING_TERMINAL,
        ScanIssue.HALTED_TERMINAL,
    }
    exact_shape = (
        result.state is not None
        and result.trace_sha256 is not None
        and result.terminal_clean
        and result.wal_valid
        and result.scan_issue is None
        and result.replay_mismatch is None
    )
    if (
        result.state is None
        or result.trace_sha256 is None
        or result.wal_valid != expected_wal_valid
        or result.terminal_clean
        and result.scan_issue is not None
        or result.exact_replay != exact_shape
        or result.exact_replay
        and result.state is not None
        and result.state.derived_count != result.derived_count
    ):
        raise ExpertContractError("replay_result")


def _decode_phase1_terminal_payload(
    event: PersistedEvent,
) -> dict[str, object]:
    try:
        decoded = json.loads(event.payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ExpertContractError("evidence_terminal") from None
    if (
        type(decoded) is not dict
        or set(decoded) != _PHASE1_TERMINAL_KEYS
    ):
        raise ExpertContractError("evidence_terminal")
    try:
        canonical = canonical_json_bytes(decoded)
    except (TypeError, ValueError):
        raise ExpertContractError("evidence_terminal") from None
    if canonical != event.payload:
        raise ExpertContractError("evidence_terminal")
    return decoded


@dataclass(frozen=True, slots=True, init=False)
class EvidenceReplayContextV1:
    schema_version: int
    session_manifest: SessionManifest
    session_manifest_sha256: str
    session_start: PersistedEvent
    session_start_record_sha256: str
    replay_result: ReplayResult
    evidence_terminal: PersistedEvent | None
    evidence_terminal_record_sha256: str | None
    evidence_marker_identity: ExpertPhysicalFileIdentityV1
    evidence_wal_identity: ExpertPhysicalFileIdentityV1

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, EvidenceReplayContextV1)
        _schema_version(self.schema_version)
        _exact(self.session_manifest, SessionManifest, "session_manifest")
        _sha256(
            self.session_manifest_sha256,
            "session_manifest_sha256",
        )
        if (
            self.session_manifest_sha256
            != session_manifest_sha256(self.session_manifest)
        ):
            raise ExpertContractError("session_manifest_sha256")
        _exact(self.session_start, PersistedEvent, "session_start")
        PersistedEvent.__post_init__(self.session_start)
        _sha256(
            self.session_start_record_sha256,
            "session_start_record_sha256",
        )
        if (
            self.session_start_record_sha256
            != canonical_record_sha256(self.session_start)
            or self.session_start.payload
            != canonical_session_manifest_bytes(self.session_manifest)
        ):
            raise ExpertContractError("session_start")
        _validate_phase1_control_shape(
            self.session_start,
            manifest=self.session_manifest,
            ingest_seq=1,
            event_type="SESSION_START",
        )
        _validate_phase1_replay_result(
            self.replay_result,
            manifest=self.session_manifest,
        )
        if self.evidence_terminal is None:
            if (
                self.evidence_terminal_record_sha256 is not None
                or self.replay_result.terminal_clean
                or self.replay_result.exact_replay
                or self.replay_result.scan_issue
                not in {
                    ScanIssue.MISSING_TERMINAL,
                    ScanIssue.TORN_TAIL,
                    ScanIssue.CORRUPT_TAIL,
                }
            ):
                raise ExpertContractError("evidence_terminal")
        else:
            _exact(
                self.evidence_terminal,
                PersistedEvent,
                "evidence_terminal",
            )
            PersistedEvent.__post_init__(self.evidence_terminal)
            _sha256(
                self.evidence_terminal_record_sha256,
                "evidence_terminal_record_sha256",
            )
            expected_terminal_sequence = (
                2
                + self.replay_result.raw_count
                + self.replay_result.derived_count
            )
            if (
                self.evidence_terminal_record_sha256
                != canonical_record_sha256(self.evidence_terminal)
            ):
                raise ExpertContractError("evidence_terminal")
            _validate_phase1_control_shape(
                self.evidence_terminal,
                manifest=self.session_manifest,
                ingest_seq=expected_terminal_sequence,
                event_type="SESSION_HALT",
            )
            terminal = _decode_phase1_terminal_payload(
                self.evidence_terminal
            )
            for name in (
                "trace_sha256",
                "final_state_sha256",
                "config_file_sha256",
                "config_canonical_sha256",
                "code_sha256",
                "session_manifest_sha256",
                "provider_manifest_file_sha256",
                "provider_manifest_canonical_sha256",
                "entitlement_id_sha256",
                "permission_artifact_sha256",
                "qualification_artifact_sha256",
                "qualification_trace_sha256",
                "adapter_code_sha256",
                "auth_contract_sha256",
                "quota_contract_sha256",
            ):
                _sha256(terminal[name], "evidence_terminal")
            for name in (
                "record_count_before_terminal",
                "raw_count",
                "derived_count",
                "last_applied_raw_seq",
                "required_retention_until_ns",
            ):
                _integer(terminal[name], "evidence_terminal")
            _integer(terminal["terminal_version"], "evidence_terminal")
            _boolean(terminal["clean"], "evidence_terminal")
            reason = _string(terminal["reason"], "evidence_terminal")
            permitted_reasons = (
                _PHASE1_CLEAN_TERMINAL_REASONS
                if terminal["clean"]
                else _PHASE1_HALTED_TERMINAL_REASONS
            )
            expected_provenance = {
                "config_file_sha256": (
                    self.session_manifest.config_file_sha256
                ),
                "config_canonical_sha256": (
                    self.session_manifest.config_canonical_sha256
                ),
                "code_sha256": self.session_manifest.code_sha256,
                "session_manifest_sha256": (
                    self.session_manifest_sha256
                ),
                "provider_manifest_file_sha256": (
                    self.session_manifest.provider_manifest_file_sha256
                ),
                "provider_manifest_canonical_sha256": (
                    self.session_manifest.provider_manifest_canonical_sha256
                ),
                "entitlement_id_sha256": (
                    self.session_manifest.entitlement_id_sha256
                ),
                "permission_artifact_sha256": (
                    self.session_manifest.permission_artifact_sha256
                ),
                "qualification_artifact_sha256": (
                    self.session_manifest.qualification_artifact_sha256
                ),
                "qualification_trace_sha256": (
                    self.session_manifest.qualification_trace_sha256
                ),
                "adapter_code_sha256": (
                    self.session_manifest.adapter_code_sha256
                ),
                "auth_contract_sha256": (
                    self.session_manifest.auth_contract_sha256
                ),
                "quota_contract_sha256": (
                    self.session_manifest.quota_contract_sha256
                ),
                "required_retention_until_ns": (
                    self.session_manifest.required_retention_until_ns
                ),
            }
            if (
                terminal["terminal_version"] != 1
                or terminal["research_evaluable"] is not False
                or reason not in permitted_reasons
                or terminal["clean"] != self.replay_result.terminal_clean
                or terminal["record_count_before_terminal"]
                != expected_terminal_sequence - 1
                or terminal["raw_count"] != self.replay_result.raw_count
                or terminal["derived_count"]
                != self.replay_result.derived_count
                or any(
                    terminal[name] != value
                    for name, value in expected_provenance.items()
                )
                or not self.replay_result.wal_valid
                or self.replay_result.scan_issue
                is not (
                    None
                    if self.replay_result.terminal_clean
                    else ScanIssue.HALTED_TERMINAL
                )
            ):
                raise ExpertContractError("evidence_terminal")
            if self.replay_result.state is not None and (
                terminal["last_applied_raw_seq"]
                != self.replay_result.state.last_applied_raw_seq
                or terminal["final_state_sha256"]
                != sha256(
                    canonical_state_bytes(self.replay_result.state)
                ).hexdigest()
            ):
                raise ExpertContractError("evidence_terminal")
            if (
                self.replay_result.trace_sha256 is not None
                and terminal["trace_sha256"]
                != self.replay_result.trace_sha256
            ):
                raise ExpertContractError("evidence_terminal")
        for identity, role in (
            (self.evidence_marker_identity, "phase1_marker"),
            (self.evidence_wal_identity, "phase1_wal"),
        ):
            _exact(
                identity,
                ExpertPhysicalFileIdentityV1,
                f"{role}_identity",
            )
            identity._validate()
            if identity.role != role:
                raise ExpertContractError("evidence_identity")
        expected_anchor = _phase1_session_anchor_sha256(
            self.session_manifest_sha256,
            self.session_start_record_sha256,
        )
        if (
            self.evidence_marker_identity.session_anchor_sha256
            != expected_anchor
            or self.evidence_wal_identity.session_anchor_sha256
            != expected_anchor
        ):
            raise ExpertContractError("evidence_identity")


def _create_evidence_replay_context_v1(
    **values: object,
) -> EvidenceReplayContextV1:
    return _private_construct(  # type: ignore[return-value]
        EvidenceReplayContextV1,
        values,
    )


@dataclass(frozen=True, slots=True, init=False)
class RetentionReplayAuthorizationV1:
    schema_version: int
    session_id: str
    authorization_sequence: int
    authorized_operation: str
    expected_parent_ingest_seq: int | None
    evidence_session_manifest_sha256: str
    evidence_session_start_record_sha256: str
    evidence_terminal_record_sha256: str | None
    expert_manifest_sha256: str
    retention_binding_sha256: str
    provider_request_binding_sha256: str
    permission_artifact_sha256: str
    qualification_artifact_sha256: str
    qualification_trace_sha256: str
    evidence_marker_identity: ExpertPhysicalFileIdentityV1
    evidence_wal_identity: ExpertPhysicalFileIdentityV1
    companion_marker_identity: ExpertPhysicalFileIdentityV1
    companion_journal_identity: ExpertPhysicalFileIdentityV1
    common_deadline_ns: int
    final_sampled_wall_ns: int
    authorization_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, RetentionReplayAuthorizationV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        _integer(
            self.authorization_sequence,
            "authorization_sequence",
        )
        _one_of_strings(
            self.authorized_operation,
            frozenset({"begin", "parent_group", "finish"}),
            "authorized_operation",
        )
        if self.expected_parent_ingest_seq is not None:
            _integer(
                self.expected_parent_ingest_seq,
                "expected_parent_ingest_seq",
                positive=True,
            )
        if (self.authorized_operation == "parent_group") != (
            self.expected_parent_ingest_seq is not None
        ):
            raise ExpertContractError("authorization_parent")
        if (
            self.authorized_operation == "begin"
            and self.authorization_sequence != 0
            or self.authorized_operation != "begin"
            and self.authorization_sequence == 0
        ):
            raise ExpertContractError("authorization_sequence")
        for name in (
            "evidence_session_manifest_sha256",
            "evidence_session_start_record_sha256",
            "expert_manifest_sha256",
            "retention_binding_sha256",
            "provider_request_binding_sha256",
            "permission_artifact_sha256",
            "qualification_artifact_sha256",
            "qualification_trace_sha256",
        ):
            _sha256(getattr(self, name), name)
        if self.evidence_terminal_record_sha256 is not None:
            _sha256(
                self.evidence_terminal_record_sha256,
                "evidence_terminal_record_sha256",
            )
        for identity, role in (
            (self.evidence_marker_identity, "phase1_marker"),
            (self.evidence_wal_identity, "phase1_wal"),
            (self.companion_marker_identity, "expert_marker"),
            (self.companion_journal_identity, "expert_journal"),
        ):
            _exact(identity, ExpertPhysicalFileIdentityV1, role)
            identity._validate()
            if identity.role != role:
                raise ExpertContractError("authorization_identity")
        evidence_anchor = _phase1_session_anchor_sha256(
            self.evidence_session_manifest_sha256,
            self.evidence_session_start_record_sha256,
        )
        if (
            self.evidence_marker_identity.session_anchor_sha256
            != evidence_anchor
            or self.evidence_wal_identity.session_anchor_sha256
            != evidence_anchor
            or self.companion_marker_identity.session_anchor_sha256
            != self.companion_journal_identity.session_anchor_sha256
        ):
            raise ExpertContractError("authorization_identity")
        _integer(self.common_deadline_ns, "common_deadline_ns", positive=True)
        _integer(self.final_sampled_wall_ns, "final_sampled_wall_ns")
        if self.final_sampled_wall_ns >= self.common_deadline_ns:
            raise ExpertContractError("authorization_deadline")
        _sha256(self.authorization_sha256, "authorization_sha256")
        _self_digest(
            self,
            digest_field="authorization_sha256",
            domain=b"INCI-EXPERT-REPLAY-AUTHORIZATION-V1\0",
            name="authorization_sha256",
        )


def _create_retention_replay_authorization_v1(
    **values: object,
) -> RetentionReplayAuthorizationV1:
    return _private_construct(  # type: ignore[return-value]
        RetentionReplayAuthorizationV1,
        values,
    )


@dataclass(frozen=True, slots=True)
class ExpertJournalScanSummaryV1:
    schema_version: int
    file_size: int
    last_good_offset: int
    last_frame_sequence: int
    group_count: int
    record_count: int
    terminal_clean: bool
    issue: ExpertJournalScanIssueV1 | None
    journal_valid: bool

    def __post_init__(self) -> None:
        _exact_self(self, ExpertJournalScanSummaryV1)
        _schema_version(self.schema_version)
        for name in (
            "file_size",
            "last_good_offset",
            "last_frame_sequence",
            "group_count",
            "record_count",
        ):
            _integer(getattr(self, name), name)
        _boolean(self.terminal_clean, "terminal_clean")
        _optional_exact(self.issue, ExpertJournalScanIssueV1, "issue")
        _boolean(self.journal_valid, "journal_valid")
        if (
            self.last_good_offset > self.file_size
            or self.record_count < self.group_count
            or self.terminal_clean != self.journal_valid
            or self.journal_valid != (self.issue is None)
            or (
                self.journal_valid
                and self.last_good_offset != self.file_size
            )
        ):
            raise ExpertContractError("journal_scan_summary")


@dataclass(frozen=True, slots=True, init=False)
class ExpertReplayBeginReadyV1:
    evidence: EvidenceReplayContextV1
    manifest: ExpertSessionManifestV1

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, ExpertReplayBeginReadyV1)
        _exact(self.evidence, EvidenceReplayContextV1, "evidence")
        self.evidence._validate()
        _exact(self.manifest, ExpertSessionManifestV1, "manifest")
        ExpertSessionManifestV1.__post_init__(self.manifest)
        if (
            self.manifest.session_id
            != self.evidence.session_manifest.session_id
            or self.manifest.evidence_session_manifest_sha256
            != self.evidence.session_manifest_sha256
            or self.manifest.evidence_session_start_record_sha256
            != self.evidence.session_start_record_sha256
        ):
            raise ExpertContractError("replay_begin_ready")


def _create_expert_replay_begin_ready_v1(
    **values: object,
) -> ExpertReplayBeginReadyV1:
    return _private_construct(  # type: ignore[return-value]
        ExpertReplayBeginReadyV1,
        values,
    )


@dataclass(frozen=True, slots=True, init=False)
class ExpertReplayDiagnosticFileProofV1:
    schema_version: int
    role: ExpertReplayDiagnosticRoleV1
    entry_present: bool
    device: int | None
    inode: int | None
    uid: int | None
    mode: int | None
    link_count: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    observed_size: int
    observed_prefix_length: int
    observed_prefix_sha256: str
    issue: ExpertReplayDiagnosticIssueV1
    proof_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, ExpertReplayDiagnosticFileProofV1)
        _schema_version(self.schema_version)
        _exact(self.role, ExpertReplayDiagnosticRoleV1, "role")
        _boolean(self.entry_present, "entry_present")
        stat_names = (
            "device",
            "inode",
            "uid",
            "mode",
            "link_count",
            "mtime_ns",
            "ctime_ns",
        )
        for name in stat_names:
            value = getattr(self, name)
            if value is not None:
                _integer(value, name)
        _integer(self.observed_size, "observed_size")
        _integer(
            self.observed_prefix_length,
            "observed_prefix_length",
        )
        _sha256(
            self.observed_prefix_sha256,
            "observed_prefix_sha256",
        )
        _exact(self.issue, ExpertReplayDiagnosticIssueV1, "issue")
        if self.entry_present:
            if (
                any(getattr(self, name) is None for name in stat_names)
                or self.observed_prefix_length
                > min(self.observed_size, 4096)
                or self.issue
                is ExpertReplayDiagnosticIssueV1.ENTRY_MISSING
                or (
                    self.observed_prefix_length == 0
                    and self.observed_prefix_sha256
                    != sha256(b"").hexdigest()
                )
            ):
                raise ExpertContractError("diagnostic_file_proof")
        elif (
            any(getattr(self, name) is not None for name in stat_names)
            or self.observed_size != 0
            or self.observed_prefix_length != 0
            or self.observed_prefix_sha256 != sha256(b"").hexdigest()
            or self.issue is not ExpertReplayDiagnosticIssueV1.ENTRY_MISSING
        ):
            raise ExpertContractError("diagnostic_file_proof")
        _sha256(self.proof_sha256, "proof_sha256")
        _self_digest(
            self,
            digest_field="proof_sha256",
            domain=b"INCI-EXPERT-REPLAY-DIAGNOSTIC-FILE-PROOF-V1\0",
            name="proof_sha256",
        )


def _create_expert_replay_diagnostic_file_proof_v1(
    **values: object,
) -> ExpertReplayDiagnosticFileProofV1:
    return _private_construct(  # type: ignore[return-value]
        ExpertReplayDiagnosticFileProofV1,
        values,
    )


@dataclass(frozen=True, slots=True, init=False)
class ExpertReplayDiagnosticProofV1:
    schema_version: int
    session_id: str
    mismatch: ExpertReplayMismatchV1
    phase1_replay_summary_sha256: str | None
    file_proofs: tuple[ExpertReplayDiagnosticFileProofV1, ...]
    companion_scan: ExpertJournalScanSummaryV1 | None
    common_deadline_ns: int
    final_sampled_wall_ns: int
    acknowledged_parent_count: int
    acknowledged_expert_record_count: int
    proof_sha256: str

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, ExpertReplayDiagnosticProofV1)
        _schema_version(self.schema_version)
        _session_id(self.session_id)
        _exact(self.mismatch, ExpertReplayMismatchV1, "mismatch")
        if self.phase1_replay_summary_sha256 is not None:
            _sha256(
                self.phase1_replay_summary_sha256,
                "phase1_replay_summary_sha256",
            )
        proofs = _exact_tuple(
            self.file_proofs,
            ExpertReplayDiagnosticFileProofV1,
            "file_proofs",
        )
        for proof in proofs:
            proof._validate()
        roles = tuple(proof.role for proof in proofs)
        order = tuple(ExpertReplayDiagnosticRoleV1)
        if len(set(roles)) != len(roles) or roles != tuple(
            role for role in order if role in roles
        ):
            raise ExpertContractError("diagnostic_proof_roles")
        if self.companion_scan is not None:
            _exact(
                self.companion_scan,
                ExpertJournalScanSummaryV1,
                "companion_scan",
            )
            ExpertJournalScanSummaryV1.__post_init__(self.companion_scan)
        _integer(self.common_deadline_ns, "common_deadline_ns", positive=True)
        _integer(self.final_sampled_wall_ns, "final_sampled_wall_ns")
        _integer(
            self.acknowledged_parent_count,
            "acknowledged_parent_count",
        )
        _integer(
            self.acknowledged_expert_record_count,
            "acknowledged_expert_record_count",
        )
        if (
            self.acknowledged_expert_record_count
            < self.acknowledged_parent_count
        ):
            raise ExpertContractError("diagnostic_counts")
        if (
            self.mismatch is ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED
        ) != (self.final_sampled_wall_ns >= self.common_deadline_ns):
            raise ExpertContractError("diagnostic_deadline")
        if self.mismatch in {
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH,
        } and proofs:
            raise ExpertContractError("diagnostic_file_proofs")
        if (
            self.mismatch
            is ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH
            and (
                len(proofs) != 1
                or proofs[0].issue not in _IDENTITY_DIAGNOSTIC_ISSUES
            )
        ):
            raise ExpertContractError("diagnostic_file_proofs")
        if self.phase1_replay_summary_sha256 is None and (
            self.companion_scan is not None
            or self.acknowledged_parent_count != 0
            or self.acknowledged_expert_record_count != 0
        ):
            raise ExpertContractError("diagnostic_pre_replay")
        _sha256(self.proof_sha256, "proof_sha256")
        _self_digest(
            self,
            digest_field="proof_sha256",
            domain=b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
            name="proof_sha256",
        )


def _create_expert_replay_diagnostic_proof_v1(
    **values: object,
) -> ExpertReplayDiagnosticProofV1:
    return _private_construct(  # type: ignore[return-value]
        ExpertReplayDiagnosticProofV1,
        values,
    )


@dataclass(frozen=True, slots=True)
class ExpertReplayAccumulatorV1:
    schema_version: int
    manifest: ExpertSessionManifestV1
    current_environment: ExpertCurrentEnvironmentV1
    evidence: EvidenceReplayContextV1
    state: ExpertStateV1
    cursor: ExpertJournalCursorV1
    evidence_raw_count: int
    evidence_derived_count: int
    processed_parent_count: int
    last_authorization_sequence: int
    last_authorization_sha256: str
    mismatch: ExpertReplayMismatchV1 | None

    def __post_init__(self) -> None:
        _exact_self(self, ExpertReplayAccumulatorV1)
        _schema_version(self.schema_version)
        _exact(self.manifest, ExpertSessionManifestV1, "manifest")
        ExpertSessionManifestV1.__post_init__(self.manifest)
        _exact(
            self.current_environment,
            ExpertCurrentEnvironmentV1,
            "current_environment",
        )
        ExpertCurrentEnvironmentV1.__post_init__(self.current_environment)
        _exact(self.evidence, EvidenceReplayContextV1, "evidence")
        self.evidence._validate()
        _exact(self.state, ExpertStateV1, "state")
        ExpertStateV1.__post_init__(self.state)
        _exact(self.cursor, ExpertJournalCursorV1, "cursor")
        ExpertJournalCursorV1.__post_init__(self.cursor)
        for name in (
            "evidence_raw_count",
            "evidence_derived_count",
            "processed_parent_count",
            "last_authorization_sequence",
        ):
            _integer(getattr(self, name), name)
        _sha256(
            self.last_authorization_sha256,
            "last_authorization_sha256",
        )
        _optional_exact(self.mismatch, ExpertReplayMismatchV1, "mismatch")
        environment_equal = (
            self.manifest.environment == self.current_environment
        )
        earlier_environment_precedence = self.mismatch in {
            ExpertReplayMismatchV1.EVIDENCE_CONTEXT_MISMATCH,
            ExpertReplayMismatchV1.EVIDENCE_REPLAY_NOT_EXACT,
            ExpertReplayMismatchV1.EVIDENCE_TERMINAL_NOT_CLEAN,
            ExpertReplayMismatchV1.EVIDENCE_SESSION_MISMATCH,
            ExpertReplayMismatchV1.EVIDENCE_MANIFEST_MISMATCH,
            ExpertReplayMismatchV1.RETENTION_AUTHORIZATION_MISMATCH,
            ExpertReplayMismatchV1.RETENTION_DEADLINE_REACHED,
            ExpertReplayMismatchV1.EVIDENCE_IDENTITY_MISMATCH,
        }
        environment_relation_valid = (
            earlier_environment_precedence
            or (
                self.mismatch
                is ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
                and not environment_equal
            )
            or (
                self.mismatch
                is not ExpertReplayMismatchV1.CURRENT_ENVIRONMENT_MISMATCH
                and environment_equal
            )
        )
        if (
            not environment_relation_valid
            or self.manifest.session_id
            != self.evidence.session_manifest.session_id
            or self.state.session_id != self.manifest.session_id
            or self.cursor.session_id != self.manifest.session_id
            or self.cursor.expert_state_sha256 != expert_state_sha256(self.state)
            or self.processed_parent_count != self.cursor.group_count
            or self.processed_parent_count > self.evidence_raw_count
            or self.last_authorization_sequence
            != self.processed_parent_count
        ):
            raise ExpertContractError("replay_accumulator")


@dataclass(frozen=True, slots=True)
class ExpertReplayResultV1:
    state: ExpertStateV1 | None
    trace_sha256: str | None
    evidence_raw_count: int
    evidence_derived_count: int
    expert_group_count: int
    expert_record_count: int
    evidence_exact: bool
    companion_valid: bool
    terminals_aligned: bool
    exact_replay: bool
    mismatch: ExpertReplayMismatchV1 | None
    final_authorization_sha256: str | None
    evaluation_input_eligible: bool
    research_evaluable: Literal[False]

    def __post_init__(self) -> None:
        _exact_self(self, ExpertReplayResultV1)
        _optional_exact(self.state, ExpertStateV1, "state")
        if self.state is not None:
            ExpertStateV1.__post_init__(self.state)
        if self.trace_sha256 is not None:
            _sha256(self.trace_sha256, "trace_sha256")
        for name in (
            "evidence_raw_count",
            "evidence_derived_count",
            "expert_group_count",
            "expert_record_count",
        ):
            _integer(getattr(self, name), name)
        for name in (
            "evidence_exact",
            "companion_valid",
            "terminals_aligned",
            "exact_replay",
            "evaluation_input_eligible",
        ):
            _boolean(getattr(self, name), name)
        _optional_exact(self.mismatch, ExpertReplayMismatchV1, "mismatch")
        if self.final_authorization_sha256 is not None:
            _sha256(
                self.final_authorization_sha256,
                "final_authorization_sha256",
            )
        if self.research_evaluable is not False:
            raise ExpertContractError("research_evaluable")
        if self.expert_record_count < self.expert_group_count:
            raise ExpertContractError("replay_counts")
        exact_shape = (
            self.state is not None
            and self.trace_sha256 is not None
            and self.evidence_exact
            and self.companion_valid
            and self.terminals_aligned
            and self.mismatch is None
            and self.final_authorization_sha256 is not None
        )
        if (
            self.exact_replay != exact_shape
            or self.evaluation_input_eligible != self.exact_replay
        ):
            raise ExpertContractError("replay_result")


@dataclass(frozen=True, slots=True, init=False)
class ExpertReplayDeniedV1:
    result: ExpertReplayResultV1
    proof: ExpertReplayDiagnosticProofV1

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("private construction required")

    def _validate(self) -> None:
        _exact_self(self, ExpertReplayDeniedV1)
        _exact(self.result, ExpertReplayResultV1, "result")
        ExpertReplayResultV1.__post_init__(self.result)
        _exact(self.proof, ExpertReplayDiagnosticProofV1, "proof")
        self.proof._validate()
        if (
            self.result.state is not None
            or self.result.trace_sha256 is not None
            or self.result.companion_valid
            or self.result.terminals_aligned
            or self.result.exact_replay
            or self.result.evaluation_input_eligible
            or self.result.research_evaluable is not False
            or self.result.final_authorization_sha256 is not None
            or self.result.mismatch is not self.proof.mismatch
            or self.result.expert_group_count
            != self.proof.acknowledged_parent_count
            or self.result.expert_record_count
            != self.proof.acknowledged_expert_record_count
            or (
                self.proof.phase1_replay_summary_sha256 is None
                and (
                    self.result.evidence_exact
                    or self.result.evidence_raw_count != 0
                    or self.result.evidence_derived_count != 0
                )
            )
        ):
            raise ExpertContractError("replay_denied")


def _create_expert_replay_denied_v1(
    **values: object,
) -> ExpertReplayDeniedV1:
    return _private_construct(  # type: ignore[return-value]
        ExpertReplayDeniedV1,
        values,
    )


ExpertReplayBeginPreparationV1 = ExpertReplayBeginReadyV1 | ExpertReplayDeniedV1


def _compute_contract_self_digest(
    cls: type[object],
    digest_field: str,
    domain: bytes,
    values: dict[str, object],
) -> str:
    names = tuple(
        item.name for item in fields(cls) if item.name != digest_field
    )
    return _compute_exact_fields_sha256(domain, names, values)


def compute_expert_provider_domain_binding_sha256(
    **values: object,
) -> str:
    return _compute_contract_self_digest(
        ExpertProviderDomainBindingV1,
        "provider_domain_binding_sha256",
        b"INCI-EXPERT-PROVIDER-DOMAIN-BINDING-V1\0",
        values,
    )


def compute_expert_retention_binding_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertRetentionBindingV1,
        "retention_binding_sha256",
        b"INCI-EXPERT-RETENTION-BINDING-V1\0",
        values,
    )


def expert_structural_schema_bundle_sha256(
    *,
    schema_version: int,
    pins: tuple[ExpertSchemaPinV1, ...],
) -> str:
    return _domain_sha256(
        b"INCI-EXPERT-STRUCTURAL-SCHEMA-BUNDLE-V1\0",
        (schema_version, pins),
    )


def expert_event_schema_bundle_sha256(
    *,
    schema_version: int,
    pins: tuple[ExpertEventSchemaPinV1, ...],
) -> str:
    return _domain_sha256(
        b"INCI-EXPERT-EVENT-SCHEMA-BUNDLE-V1\0",
        (schema_version, pins),
    )


def expert_normalizer_registry_sha256(
    *,
    schema_version: int,
    fallback: ExpertNormalizerPinV1,
    entries: tuple[ExpertNormalizerPinV1, ...],
) -> str:
    return _domain_sha256(
        b"INCI-EXPERT-NORMALIZER-REGISTRY-V1\0",
        (schema_version, fallback, entries),
    )


def compute_expert_capacity_proof_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertCapacityProofV1,
        "proof_sha256",
        b"INCI-EXPERT-CAPACITY-PROOF-V1\0",
        values,
    )


def compute_expert_session_manifest_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertSessionManifestV1,
        "manifest_sha256",
        b"INCI-EXPERT-SESSION-MANIFEST-V1\0",
        values,
    )


def expert_observation_sha256(observation: ExpertObservationV1) -> str:
    if type(observation) not in (
        ExpertSynchronizationObservationV1,
        ExpertIgnoredObservationV1,
        ExpertRejectedObservationV1,
    ):
        raise TypeError("observation")
    type(observation).__post_init__(observation)
    return _domain_sha256(
        b"INCI-EXPERT-OBSERVATION-V1\0",
        observation,
    )


def expert_state_sha256(state: ExpertStateV1) -> str:
    _exact(state, ExpertStateV1, "state")
    ExpertStateV1.__post_init__(state)
    return _domain_sha256(b"INCI-EXPERT-STATE-V1\0", state)


def compute_expert_journal_record_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertJournalRecordV1,
        "record_sha256",
        b"INCI-EXPERT-JOURNAL-RECORD-V1\0",
        values,
    )


def expert_trace_seed_sha256(
    session_id: str,
    expert_manifest_sha256: str,
    initial_expert_state_sha256: str,
) -> str:
    _session_id(session_id)
    _sha256(expert_manifest_sha256, "expert_manifest_sha256")
    _sha256(
        initial_expert_state_sha256,
        "initial_expert_state_sha256",
    )
    return _domain_sha256(
        b"INCI-EXPERT-TRACE-SEED-V1\0",
        (
            session_id,
            expert_manifest_sha256,
            initial_expert_state_sha256,
        ),
    )


def compute_expert_trace_step_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertTraceStepV1,
        "post_trace_sha256",
        b"INCI-EXPERT-TRACE-STEP-V1\0",
        values,
    )


def compute_expert_journal_group_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertJournalGroupV1,
        "group_sha256",
        b"INCI-EXPERT-JOURNAL-GROUP-V1\0",
        values,
    )


def compute_expert_session_terminal_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertSessionTerminalV1,
        "terminal_sha256",
        b"INCI-EXPERT-SESSION-TERMINAL-V1\0",
        values,
    )


def compute_expert_physical_file_identity_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        ExpertPhysicalFileIdentityV1,
        "identity_sha256",
        b"INCI-EXPERT-PHYSICAL-FILE-IDENTITY-V1\0",
        values,
    )


def compute_retention_replay_authorization_sha256(**values: object) -> str:
    return _compute_contract_self_digest(
        RetentionReplayAuthorizationV1,
        "authorization_sha256",
        b"INCI-EXPERT-REPLAY-AUTHORIZATION-V1\0",
        values,
    )


def expert_phase1_replay_summary_sha256(result: ReplayResult) -> str:
    _exact(result, ReplayResult, "result")
    state_projection = (
        None
        if result.state is None
        else (
            result.state.session_id,
            result.state.last_applied_raw_seq,
            result.state.raw_count,
            result.state.derived_count,
            tuple(
                (kind.value, source_id, connection_epoch)
                for kind, source_id, connection_epoch
                in result.state.source_epochs
            ),
        )
    )
    return _domain_sha256(
        b"INCI-EXPERT-PHASE1-REPLAY-SUMMARY-V1\0",
        (
            state_projection,
            result.trace_sha256,
            result.raw_count,
            result.derived_count,
            result.terminal_clean,
            result.wal_valid,
            result.exact_replay,
            None if result.scan_issue is None else result.scan_issue.value,
            None
            if result.replay_mismatch is None
            else result.replay_mismatch.value,
            result.research_evaluable,
        ),
    )


def compute_expert_replay_diagnostic_file_proof_sha256(
    **values: object,
) -> str:
    return _compute_contract_self_digest(
        ExpertReplayDiagnosticFileProofV1,
        "proof_sha256",
        b"INCI-EXPERT-REPLAY-DIAGNOSTIC-FILE-PROOF-V1\0",
        values,
    )


def compute_expert_replay_diagnostic_proof_sha256(
    **values: object,
) -> str:
    return _compute_contract_self_digest(
        ExpertReplayDiagnosticProofV1,
        "proof_sha256",
        b"INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1\0",
        values,
    )


_REGISTERED_ENUMS: Final[tuple[type[Enum], ...]] = (
    PlayerSide,
    ContractSide,
    MatchStatus,
    MatchFormat,
    TerminationKind,
    ProviderLifecycleKind,
    ScoreValue,
    TransitionDisposition,
    TennisTransitionReason,
    MarketStatus,
    BookEventKind,
    SyncInputKind,
    DecisionAction,
    SyncReason,
    DecisionReason,
    ExpertEventKindV1,
    ExpertReplayDiagnosticRoleV1,
    ExpertReplayDiagnosticIssueV1,
    ExpertIgnoreReasonV1,
    ExpertRejectReasonV1,
    ExpertTerminalReasonV1,
    ExpertJournalFrameKindV1,
    ExpertJournalScanIssueV1,
    ExpertReplayMismatchV1,
)

_REGISTERED_DATACLASSES: Final[tuple[type[object], ...]] = (
    ArtifactPin,
    PairedTimeObservation,
    SetScore,
    ProviderSnapshot,
    ProviderPoint,
    ProviderLifecycle,
    TennisState,
    TennisTransitionResult,
    BookLevel,
    BookSnapshot,
    BookDelta,
    MarketLifecycle,
    BookState,
    BookTransitionResult,
    MatchBinding,
    BindingRoute,
    SettlementSemantics,
    BindingMarketMetadata,
    BindingMetadata,
    BindingReviewDecision,
    BindingUniverse,
    CausalPointWitness,
    PendingBookMove,
    TennisSyncCursor,
    LastSyncEmission,
    BookSyncCursor,
    SynchronizationSessionState,
    SynchronizationInput,
    SyncResult,
    SynchronizationTransitionResult,
    SyncPolicy,
    TrustedSnapshot,
    OpportunityFrame,
    FairValueEstimate,
    PolicyPathEstimate,
    PolicyEstimate,
    PolicyDecision,
    ExpertCurrentEnvironmentV1,
    ExpertProviderDomainBindingV1,
    ExpertRetentionBindingV1,
    ExpertSchemaPinV1,
    ExpertStructuralSchemaBundleV1,
    ExpertEventSchemaPinV1,
    ExpertEventSchemaBundleV1,
    ExpertNormalizerPinV1,
    ExpertNormalizerRegistryV1,
    ExpertCollectedEnvironmentV1,
    ExpertCapacityProofV1,
    ExpertSessionManifestV1,
    ExpertSynchronizationDraftV1,
    ExpertIgnoredDraftV1,
    ExpertRejectedDraftV1,
    ExpertParentEvidenceV1,
    ExpertSynchronizationObservationV1,
    ExpertIgnoredObservationV1,
    ExpertRejectedObservationV1,
    ExpertStateV1,
    ExpertSynchronizationAppliedPayloadV1,
    ExpertObservationIgnoredPayloadV1,
    ExpertObservationRejectedPayloadV1,
    ExpertOutcomeDraftV1,
    ExpertReductionV1,
    ExpertJournalCursorV1,
    ExpertPayloadDescriptorV1,
    ExpertJournalRecordV1,
    ExpertTraceStepV1,
    ExpertJournalGroupV1,
    ExpertSessionTerminalV1,
    DurableExpertAppendReceiptV1,
    DurableExpertTerminalReceiptV1,
    DurableExpertEmergencyReceiptV1,
    ExpertPurgeReportV1,
    ExpertPhysicalFileIdentityV1,
    EvidenceReplayContextV1,
    RetentionReplayAuthorizationV1,
    ExpertJournalScanSummaryV1,
    ExpertReplayBeginReadyV1,
    ExpertReplayDiagnosticFileProofV1,
    ExpertReplayDiagnosticProofV1,
    ExpertReplayAccumulatorV1,
    ExpertReplayResultV1,
    ExpertReplayDeniedV1,
)


def _canonical_decimal(value: Decimal) -> str:
    _decimal(value, "value")
    if value.is_zero():
        return "0"
    sign, digits, exponent = value.as_tuple()
    trailing_zero_count = 0
    for digit in reversed(digits):
        if digit != 0:
            break
        trailing_zero_count += 1
    n = len(digits) - trailing_zero_count
    e = exponent + trailing_zero_count
    sign_length = 1 if sign else 0
    if e >= 0:
        normalized_length = sign_length + n + e
    elif n + e > 0:
        normalized_length = sign_length + n + 1
    else:
        normalized_length = sign_length + 2 - e
    if normalized_length > 256:
        raise ExpertContractError("decimal_length")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if value.is_zero():
        rendered = "0"
    if len(rendered.encode("ascii")) != normalized_length:
        raise ExpertContractError("decimal_normalization")
    return rendered


def _project(value: object, active: set[int]) -> object:
    if value is None or type(value) is bool or type(value) is str:
        return value
    if type(value) is int:
        _integer(value, "value", nonnegative=False)
        return value
    if type(value) is Decimal:
        return {"$decimal": _canonical_decimal(value)}
    value_type = type(value)
    if value_type is SessionManifest:
        return {
            "$phase1_session_manifest_sha256": session_manifest_sha256(value)
        }
    if value_type is PersistedEvent:
        return {
            "$phase1_record_sha256": canonical_record_sha256(value)
        }
    if value_type is ReplayResult:
        return {
            "$phase1_replay_summary_sha256": (
                expert_phase1_replay_summary_sha256(value)
            )
        }
    if value_type in _REGISTERED_ENUMS:
        assert isinstance(value, Enum)
        return {"$enum": value_type.__name__, "value": value.value}
    is_container = (
        value_type is tuple
        or value_type is list
        or value_type is dict
        or value_type in _REGISTERED_DATACLASSES
    )
    if not is_container:
        raise TypeError("unsupported canonical value")
    identity = id(value)
    if identity in active:
        raise ExpertContractError("cycle")
    active.add(identity)
    try:
        if value_type is tuple:
            return {"$tuple": [_project(item, active) for item in value]}
        if value_type is list:
            return {"$list": [_project(item, active) for item in value]}
        if value_type is dict:
            keys = tuple(value.keys())
            for key in keys:
                if type(key) is not str:
                    raise TypeError("dict key")
            entries: list[list[object]] = []
            for key in sorted(keys):
                entries.append([key, _project(value[key], active)])
            return {"$dict": entries}
        projected_fields = {
            field.name: _project(getattr(value, field.name), active)
            for field in fields(value)
        }
        return {
            "$contract": value_type.__name__,
            "$version": 1,
            "fields": projected_fields,
        }
    finally:
        active.remove(identity)


def canonical_expert_bytes(value: object) -> bytes:
    document = {
        "canonical_version": 1,
        "domain": "inci-tennis-expert",
        "value": _project(value, set()),
    }
    return json.dumps(
        document,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def expert_contract_sha256(value: object) -> str:
    return sha256(
        b"INCI-EXPERT-CONTRACT-SHA256-V1\0"
        + canonical_expert_bytes(value)
    ).hexdigest()
