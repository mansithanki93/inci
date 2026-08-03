"""Pure causal consensus-to-full-L2 capture contracts and reducer.

The accepted score supplied here must already have passed
``apply_score_consensus``.  This module does not select supporters, form a
consensus, qualify an execution book, or authorize an order.  It only records
an immutable research barrier and its first causally eligible unqualified L2
observation.

Callers must dispatch ``open``, ``observe``, and ``censor`` inputs in global
durable-record order from the existing sequencer.  The reducer rejects
detectable durable regressions but deliberately does not buffer or reorder
cross-stream callbacks; that integration belongs to the runtime boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from re import compile as pattern_compile

from inci_tennis_expert.contracts import (
    MatchFormat,
    MatchStatus,
    TennisState,
    TennisStateInvariantError,
)
from inci_tennis_expert.score_consensus import (
    ConsensusReason,
    ScoreConsensusResult,
)
from inci_tennis_expert.tennis_score import validate_tennis_state


RESEARCH_QUALIFICATION = "unqualified_shadow"

_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_MAX_L2_LEVELS = 1_024
_MAX_QUANTITY = Decimal("1000000000000")
_SAFE_ID_RE = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TICKER_RE = pattern_compile(r"[A-Z0-9][A-Z0-9._-]{0,127}\Z")
_UUID_RE = pattern_compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_SHA256_RE = pattern_compile(r"[0-9a-f]{64}\Z")

_ADAPTER_L2_DOMAIN = (
    b"inci-tennis-kalshi-unqualified-two-ticker-full-l2-v1\x00"
)
_ACCEPTED_SCORE_DOMAIN = b"inci-tennis-accepted-score-consensus-v1\x00"
_NORMALIZED_SCORE_DOMAIN = b"inci-tennis-normalized-score-coordinates-v1\x00"
_L2_OBSERVATION_DOMAIN = b"inci-tennis-unqualified-l2-observation-v1\x00"
_FRAME_DOMAIN = b"inci-tennis-consensus-l2-research-frame-v1\x00"
_COVERAGE_DOMAIN = b"inci-tennis-consensus-l2-coverage-v1\x00"
_CENSOR_EVENT_DOMAIN = b"inci-tennis-consensus-l2-censor-event-v1\x00"


class ConsensusL2ResearchError(ValueError):
    """Sanitized fixed-message rejection for research contract violations."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("consensus_l2_research_invalid")

    def __repr__(self) -> str:
        return "<ConsensusL2ResearchError>"


def _fail(code: str) -> None:
    raise ConsensusL2ResearchError(code)


class _ResearchValue:
    __slots__ = ()

    @property
    def schema_version(self) -> int:
        return 1

    @property
    def research_only(self) -> bool:
        return True

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def qualification(self) -> str:
        return RESEARCH_QUALIFICATION

    def __repr__(self) -> str:
        return f"<{type(self).__name__} unqualified_shadow>"


def _safe_id(value: object) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        _fail("identity_invalid")
    if value.lower().startswith(("http:", "https:", "file:")):
        _fail("identity_invalid")
    return value


def _ticker(value: object) -> str:
    if type(value) is not str or _TICKER_RE.fullmatch(value) is None:
        _fail("ticker_invalid")
    return value


def _market_id(value: object) -> str:
    if type(value) is not str or _UUID_RE.fullmatch(value) is None:
        _fail("market_id_invalid")
    return value


def _sha256(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail("sha256_invalid")
    return value


def _integer(value: object, *, positive: bool = False) -> int:
    if (
        type(value) is not int
        or value < (1 if positive else 0)
        or value > _MAX_SIGNED_64
    ):
        _fail("integer_invalid")
    return value


def _canonical_decimal(value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        _fail("decimal_invalid")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if value.is_zero() else rendered


def _canonical_project(value: object, active: set[int]) -> object:
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        _integer(value)
        return value
    if type(value) is Decimal:
        return {"$decimal": _canonical_decimal(value)}
    if isinstance(value, Enum):
        return {
            "$enum": type(value).__name__,
            "value": value.value,
        }
    value_type = type(value)
    if value_type not in (tuple, dict) and not is_dataclass(value):
        _fail("canonical_value_invalid")
    identity = id(value)
    if identity in active:
        _fail("canonical_cycle")
    active.add(identity)
    try:
        if value_type is tuple:
            return {
                "$tuple": [
                    _canonical_project(item, active) for item in value
                ]
            }
        if value_type is dict:
            if any(type(key) is not str for key in value):
                _fail("canonical_key_invalid")
            return {
                "$dict": [
                    [key, _canonical_project(value[key], active)]
                    for key in sorted(value)
                ]
            }
        return {
            "$dataclass": value_type.__name__,
            "fields": [
                [item.name, _canonical_project(getattr(value, item.name), active)]
                for item in fields(value)
            ],
        }
    finally:
        active.remove(identity)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _canonical_project(value, set()),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _digest(domain: bytes, value: object) -> str:
    return sha256(domain + _canonical_bytes(value)).hexdigest()


def normalized_score_coordinates_sha256_v1(
    canonical_match_id: str,
    state: TennisState,
) -> str:
    """Hash provider-neutral score coordinates already checked by consensus."""

    _safe_id(canonical_match_id)
    if type(state) is not TennisState:
        _fail("normalized_state_invalid")
    try:
        validate_tennis_state(state)
    except TennisStateInvariantError:
        _fail("normalized_state_invalid")
    return _digest(
        _NORMALIZED_SCORE_DOMAIN,
        {
            "schema_version": 1,
            "canonical_match_id": canonical_match_id,
            "scheduled_start_wall_ns": state.scheduled_start_wall_ns,
            "match_format": state.match_format,
            "status": state.status,
            "termination_kind": state.termination_kind,
            "winner": state.winner,
            "retired_side": state.retired_side,
            "completed_sets": state.completed_sets,
            "games_home": state.games_home,
            "games_away": state.games_away,
            "points_home": state.points_home,
            "points_away": state.points_away,
            "in_tiebreak": state.in_tiebreak,
            "tiebreak_points_home": state.tiebreak_points_home,
            "tiebreak_points_away": state.tiebreak_points_away,
            "tiebreak_first_server": state.tiebreak_first_server,
            "server_for_next_point": state.server_for_next_point,
        },
    )


def _market_universe(
    market_tickers: object,
    market_ids: object,
) -> tuple[tuple[str, str], tuple[str, str]]:
    if (
        type(market_tickers) is not tuple
        or len(market_tickers) != 2
        or type(market_ids) is not tuple
        or len(market_ids) != 2
    ):
        _fail("market_universe_invalid")
    tickers = (_ticker(market_tickers[0]), _ticker(market_tickers[1]))
    identifiers = (_market_id(market_ids[0]), _market_id(market_ids[1]))
    if len(set(tickers)) != 2 or len(set(identifiers)) != 2:
        _fail("market_universe_invalid")
    return tickers, identifiers


def _adapter_l2_state_sha256(
    markets: tuple[UnqualifiedL2MarketV1, UnqualifiedL2MarketV1],
    *,
    physical_connection_generation: int,
    subscription_id: int,
    global_sequence: int,
) -> str:
    projection = {
        "schema_version": 1,
        "physical_connection_generation": physical_connection_generation,
        "subscription_id": subscription_id,
        "global_sequence": global_sequence,
        "markets": [
            {
                "ticker": market.ticker,
                "market_id": market.market_id,
                "yes_levels": [
                    [
                        _canonical_decimal(level.price),
                        _canonical_decimal(level.quantity),
                    ]
                    for level in market.yes_levels
                ],
                "no_levels": [
                    [
                        _canonical_decimal(level.price),
                        _canonical_decimal(level.quantity),
                    ]
                    for level in market.no_levels
                ],
            }
            for market in markets
        ],
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return sha256(_ADAPTER_L2_DOMAIN + encoded).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class DurableRawScoreSupportRefV1(_ResearchValue):
    source_id: str
    source_lineage_sha256: str
    independence_lineage_id: str | None
    normalized_state_sha256: str
    raw_capture_sha256: str
    durable_record_sequence: int
    durable_record_sha256: str
    received_wall_ns: int
    received_monotonic_ns: int

    def __post_init__(self) -> None:
        _safe_id(self.source_id)
        _sha256(self.source_lineage_sha256)
        if self.independence_lineage_id is not None:
            _safe_id(self.independence_lineage_id)
        _sha256(self.normalized_state_sha256)
        _sha256(self.raw_capture_sha256)
        _integer(self.durable_record_sequence, positive=True)
        _sha256(self.durable_record_sha256)
        _integer(self.received_wall_ns)
        _integer(self.received_monotonic_ns)


@dataclass(frozen=True, slots=True, repr=False)
class AcceptedScoreConsensusTransitionV1(_ResearchValue):
    canonical_match_id: str
    accepted_state: TennisState
    authoritative_result: ScoreConsensusResult
    consensus_epoch: int
    correction_epoch: int
    supporters: tuple[DurableRawScoreSupportRefV1, ...]
    prior_accepted_score_sha256: str | None
    consensus_record_sequence: int
    consensus_record_sha256: str
    consensus_accepted_wall_ns: int
    consensus_accepted_monotonic_ns: int
    market_tickers: tuple[str, str]
    market_ids: tuple[str, str]
    last_book_physical_connection_generation: int
    last_book_subscription_id: int
    last_book_global_sequence: int
    accepted_score_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _safe_id(self.canonical_match_id)
        if type(self.accepted_state) is not TennisState:
            _fail("accepted_state_invalid")
        try:
            validate_tennis_state(self.accepted_state)
        except TennisStateInvariantError:
            _fail("accepted_state_invalid")
        if (
            self.accepted_state.match_format
            is not MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS
            or self.accepted_state.status is not MatchStatus.LIVE
            or self.accepted_state.server_for_next_point is None
            or self.accepted_state.snapshot_complete is not True
            or self.accepted_state.block_reason is not None
        ):
            _fail("accepted_state_invalid")
        if (
            type(self.authoritative_result) is not ScoreConsensusResult
            or self.authoritative_result.reason is not ConsensusReason.ACCEPTED
            or self.authoritative_result.accepted_state != self.accepted_state
        ):
            _fail("authoritative_result_invalid")
        _integer(self.consensus_epoch)
        _integer(self.correction_epoch)
        if self.correction_epoch != self.accepted_state.correction_epoch:
            _fail("correction_epoch_mismatch")
        if type(self.supporters) is not tuple or len(self.supporters) < 2:
            _fail("supporters_invalid")
        if any(
            type(supporter) is not DurableRawScoreSupportRefV1
            for supporter in self.supporters
        ):
            _fail("supporters_invalid")
        canonical_supporters = tuple(
            sorted(self.supporters, key=lambda supporter: supporter.source_id)
        )
        object.__setattr__(self, "supporters", canonical_supporters)
        source_ids = tuple(
            supporter.source_id for supporter in canonical_supporters
        )
        if (
            len(set(source_ids)) != len(source_ids)
            or source_ids.count(self.accepted_state.provider_source_id) != 1
            or not any(
                source_id != self.accepted_state.provider_source_id
                for source_id in source_ids
            )
        ):
            _fail("supporters_invalid")
        primary = next(
            supporter
            for supporter in canonical_supporters
            if supporter.source_id == self.accepted_state.provider_source_id
        )
        if (
            primary.source_lineage_sha256
            != self.accepted_state.source_lineage_sha256
            or primary.received_monotonic_ns
            != self.accepted_state.last_received_monotonic_ns
        ):
            _fail("primary_support_mismatch")
        proven_lineages = {
            supporter.independence_lineage_id
            for supporter in canonical_supporters
            if supporter.independence_lineage_id is not None
        }
        independence_by_source_lineage: dict[str, str | None] = {}
        for supporter in canonical_supporters:
            prior_identity = independence_by_source_lineage.get(
                supporter.source_lineage_sha256
            )
            if (
                supporter.source_lineage_sha256
                in independence_by_source_lineage
                and prior_identity != supporter.independence_lineage_id
            ):
                _fail("independence_lineage_alias")
            independence_by_source_lineage[
                supporter.source_lineage_sha256
            ] = supporter.independence_lineage_id
        if len(proven_lineages) < 2:
            _fail("independence_unproven")
        expected_source_ids = tuple(
            supporter.source_id for supporter in canonical_supporters
        )
        expected_lineages = tuple(sorted(proven_lineages))
        if (
            self.authoritative_result.supporting_source_ids
            != expected_source_ids
            or self.authoritative_result.supporting_lineages
            != expected_lineages
        ):
            _fail("authoritative_support_mismatch")
        normalized_state_sha256 = normalized_score_coordinates_sha256_v1(
            self.canonical_match_id,
            self.accepted_state,
        )
        if any(
            supporter.normalized_state_sha256 != normalized_state_sha256
            for supporter in canonical_supporters
        ):
            _fail("normalized_support_mismatch")
        _integer(self.consensus_record_sequence, positive=True)
        if self.prior_accepted_score_sha256 is not None:
            _sha256(self.prior_accepted_score_sha256)
        _sha256(self.consensus_record_sha256)
        _integer(self.consensus_accepted_wall_ns)
        _integer(self.consensus_accepted_monotonic_ns)
        durable_sequences = tuple(
            supporter.durable_record_sequence
            for supporter in canonical_supporters
        )
        if (
            len(set(durable_sequences)) != len(durable_sequences)
            or any(
                sequence >= self.consensus_record_sequence
                for sequence in durable_sequences
            )
            or any(
                supporter.received_wall_ns > self.consensus_accepted_wall_ns
                or supporter.received_monotonic_ns
                > self.consensus_accepted_monotonic_ns
                for supporter in canonical_supporters
            )
            or self.accepted_state.last_received_monotonic_ns
            > self.consensus_accepted_monotonic_ns
        ):
            _fail("supporter_durability_invalid")
        tickers, identifiers = _market_universe(
            self.market_tickers,
            self.market_ids,
        )
        object.__setattr__(self, "market_tickers", tickers)
        object.__setattr__(self, "market_ids", identifiers)
        _integer(
            self.last_book_physical_connection_generation,
            positive=True,
        )
        _integer(self.last_book_subscription_id, positive=True)
        _integer(self.last_book_global_sequence, positive=True)
        digest = _digest(
            _ACCEPTED_SCORE_DOMAIN,
            {
                "schema_version": 1,
                "canonical_match_id": self.canonical_match_id,
                "accepted_state": self.accepted_state,
                "authoritative_result": self.authoritative_result,
                "consensus_epoch": self.consensus_epoch,
                "correction_epoch": self.correction_epoch,
                "supporters": self.supporters,
                "prior_accepted_score_sha256": (
                    self.prior_accepted_score_sha256
                ),
                "consensus_record_sequence": self.consensus_record_sequence,
                "consensus_record_sha256": self.consensus_record_sha256,
                "consensus_accepted_wall_ns": (
                    self.consensus_accepted_wall_ns
                ),
                "consensus_accepted_monotonic_ns": (
                    self.consensus_accepted_monotonic_ns
                ),
                "market_tickers": self.market_tickers,
                "market_ids": self.market_ids,
                "last_book_physical_connection_generation": (
                    self.last_book_physical_connection_generation
                ),
                "last_book_subscription_id": (
                    self.last_book_subscription_id
                ),
                "last_book_global_sequence": self.last_book_global_sequence,
            },
        )
        object.__setattr__(self, "accepted_score_sha256", digest)


@dataclass(frozen=True, slots=True, repr=False)
class ResearchL2LevelV1(_ResearchValue):
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if (
            type(self.price) is not Decimal
            or not self.price.is_finite()
            or self.price < Decimal("0")
            or self.price > Decimal("1")
            or type(self.quantity) is not Decimal
            or not self.quantity.is_finite()
            or self.quantity <= Decimal("0")
            or self.quantity > _MAX_QUANTITY
        ):
            _fail("l2_level_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class UnqualifiedL2MarketV1(_ResearchValue):
    ticker: str
    market_id: str
    yes_levels: tuple[ResearchL2LevelV1, ...]
    no_levels: tuple[ResearchL2LevelV1, ...]

    def __post_init__(self) -> None:
        _ticker(self.ticker)
        _market_id(self.market_id)
        for ladder in (self.yes_levels, self.no_levels):
            if (
                type(ladder) is not tuple
                or not ladder
                or len(ladder) > _MAX_L2_LEVELS
                or any(type(level) is not ResearchL2LevelV1 for level in ladder)
            ):
                _fail("l2_ladder_invalid")
        yes = tuple(sorted(self.yes_levels, key=lambda level: level.price))
        no = tuple(sorted(self.no_levels, key=lambda level: level.price))
        if (
            len({level.price for level in yes}) != len(yes)
            or len({level.price for level in no}) != len(no)
            or yes[-1].price > no[0].price
        ):
            _fail("l2_ladder_invalid")
        object.__setattr__(self, "yes_levels", yes)
        object.__setattr__(self, "no_levels", no)


@dataclass(frozen=True, slots=True, repr=False)
class DurableRawBookParentRefV1(_ResearchValue):
    raw_frame_sha256: str
    durable_record_sequence: int
    durable_record_sha256: str
    received_wall_ns: int
    received_monotonic_ns: int

    def __post_init__(self) -> None:
        _sha256(self.raw_frame_sha256)
        _integer(self.durable_record_sequence, positive=True)
        _sha256(self.durable_record_sha256)
        _integer(self.received_wall_ns)
        _integer(self.received_monotonic_ns)


@dataclass(frozen=True, slots=True, repr=False)
class UnqualifiedTwoMarketL2ObservationV1(_ResearchValue):
    canonical_match_id: str
    markets: tuple[UnqualifiedL2MarketV1, UnqualifiedL2MarketV1]
    physical_connection_generation: int
    subscription_id: int
    global_sequence: int
    l2_state_sha256: str
    raw_parent: DurableRawBookParentRefV1
    captured_wall_ns: int
    captured_monotonic_ns: int
    observation_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _safe_id(self.canonical_match_id)
        if (
            type(self.markets) is not tuple
            or len(self.markets) != 2
            or any(type(market) is not UnqualifiedL2MarketV1 for market in self.markets)
        ):
            _fail("l2_markets_invalid")
        tickers = tuple(market.ticker for market in self.markets)
        identifiers = tuple(market.market_id for market in self.markets)
        _market_universe(tickers, identifiers)
        generation = _integer(
            self.physical_connection_generation,
            positive=True,
        )
        sid = _integer(self.subscription_id, positive=True)
        sequence = _integer(self.global_sequence, positive=True)
        _sha256(self.l2_state_sha256)
        if type(self.raw_parent) is not DurableRawBookParentRefV1:
            _fail("raw_book_parent_invalid")
        _integer(self.captured_wall_ns)
        _integer(self.captured_monotonic_ns)
        if (
            self.raw_parent.received_wall_ns != self.captured_wall_ns
            or self.raw_parent.received_monotonic_ns
            != self.captured_monotonic_ns
        ):
            _fail("book_capture_clock_mismatch")
        expected_l2_digest = _adapter_l2_state_sha256(
            self.markets,
            physical_connection_generation=generation,
            subscription_id=sid,
            global_sequence=sequence,
        )
        if self.l2_state_sha256 != expected_l2_digest:
            _fail("l2_state_digest_mismatch")
        digest = _digest(
            _L2_OBSERVATION_DOMAIN,
            {
                "schema_version": 1,
                "canonical_match_id": self.canonical_match_id,
                "markets": self.markets,
                "physical_connection_generation": generation,
                "subscription_id": sid,
                "global_sequence": sequence,
                "l2_state_sha256": self.l2_state_sha256,
                "raw_parent": self.raw_parent,
                "captured_wall_ns": self.captured_wall_ns,
                "captured_monotonic_ns": self.captured_monotonic_ns,
            },
        )
        object.__setattr__(self, "observation_sha256", digest)


class ConsensusL2CensorReasonV1(str, Enum):
    SCORE_ADVANCED = "score_advanced"
    SCORE_CORRECTED = "score_corrected"
    CONSENSUS_EPOCH_CHANGED = "consensus_epoch_changed"
    CONSENSUS_QUARANTINED = "consensus_quarantined"
    CONSENSUS_DISAGREEMENT = "consensus_disagreement"
    BOOK_SEQUENCE_GAP = "book_sequence_gap"
    BOOK_SEQUENCE_DUPLICATE = "book_sequence_duplicate"
    BOOK_SEQUENCE_OUT_OF_ORDER = "book_sequence_out_of_order"
    BOOK_GENERATION_CHANGED = "book_generation_changed"
    BOOK_RECONNECTED = "book_reconnected"
    LIFECYCLE_SUSPENDED = "lifecycle_suspended"
    LIFECYCLE_CLOSED = "lifecycle_closed"
    SESSION_ENDED = "session_ended"


class ConsensusL2DispositionV1(str, Enum):
    ARMED = "armed"
    ADVANCED = "advanced"
    IGNORED = "ignored"
    PAIRED = "paired"
    CENSORED = "censored"


class ConsensusL2DurableEventKindV1(str, Enum):
    ACCEPTED_CONSENSUS = "accepted_consensus"
    L2_BOOK = "l2_book"
    CENSOR = "censor"


@dataclass(frozen=True, slots=True, repr=False)
class ConsensusL2ResearchFrameV1(_ResearchValue):
    consensus_transition: AcceptedScoreConsensusTransitionV1
    l2_observation: UnqualifiedTwoMarketL2ObservationV1
    frame_id: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.consensus_transition)
            is not AcceptedScoreConsensusTransitionV1
            or type(self.l2_observation)
            is not UnqualifiedTwoMarketL2ObservationV1
        ):
            _fail("frame_parent_invalid")
        transition = self.consensus_transition
        observation = self.l2_observation
        if (
            observation.canonical_match_id != transition.canonical_match_id
            or tuple(market.ticker for market in observation.markets)
            != transition.market_tickers
            or tuple(market.market_id for market in observation.markets)
            != transition.market_ids
            or observation.physical_connection_generation
            != transition.last_book_physical_connection_generation
            or observation.subscription_id
            != transition.last_book_subscription_id
            or observation.global_sequence
            != transition.last_book_global_sequence + 1
            or observation.captured_monotonic_ns
            < transition.consensus_accepted_monotonic_ns
            or observation.raw_parent.durable_record_sequence
            <= transition.consensus_record_sequence
        ):
            _fail("frame_barrier_invalid")
        object.__setattr__(
            self,
            "frame_id",
            _digest(
                _FRAME_DOMAIN,
                {
                    "schema_version": 1,
                    "accepted_score_sha256": (
                        transition.accepted_score_sha256
                    ),
                    "supporters": transition.supporters,
                    "l2_observation_sha256": (
                        observation.observation_sha256
                    ),
                    "l2_state_sha256": observation.l2_state_sha256,
                    "raw_book_parent": observation.raw_parent,
                    "consensus_acceptance_clocks": (
                        transition.consensus_accepted_wall_ns,
                        transition.consensus_accepted_monotonic_ns,
                    ),
                    "book_capture_clocks": (
                        observation.captured_wall_ns,
                        observation.captured_monotonic_ns,
                    ),
                    "physical_connection_generation": (
                        observation.physical_connection_generation
                    ),
                    "subscription_id": observation.subscription_id,
                    "global_sequence": observation.global_sequence,
                    "market_tickers": transition.market_tickers,
                    "market_ids": transition.market_ids,
                },
            ),
        )


@dataclass(frozen=True, slots=True, repr=False)
class ConsensusL2CoverageV1(_ResearchValue):
    consensus_transition: AcceptedScoreConsensusTransitionV1
    reason: ConsensusL2CensorReasonV1
    event_sha256: str
    event_durable_record_sequence: int
    observed_wall_ns: int
    observed_monotonic_ns: int
    coverage_id: str = field(init=False)

    @property
    def accepted_score_sha256(self) -> str:
        return self.consensus_transition.accepted_score_sha256

    def __post_init__(self) -> None:
        if (
            type(self.consensus_transition)
            is not AcceptedScoreConsensusTransitionV1
            or type(self.reason) is not ConsensusL2CensorReasonV1
        ):
            _fail("coverage_parent_invalid")
        _sha256(self.event_sha256)
        _integer(self.event_durable_record_sequence, positive=True)
        _integer(self.observed_wall_ns)
        _integer(self.observed_monotonic_ns)
        if (
            self.event_durable_record_sequence
            <= self.consensus_transition.consensus_record_sequence
            or
            self.observed_monotonic_ns
            < self.consensus_transition.consensus_accepted_monotonic_ns
        ):
            _fail("coverage_clock_invalid")
        object.__setattr__(
            self,
            "coverage_id",
            _digest(
                _COVERAGE_DOMAIN,
                {
                    "schema_version": 1,
                    "consensus_transition": self.consensus_transition,
                    "reason": self.reason,
                    "event_sha256": self.event_sha256,
                    "event_durable_record_sequence": (
                        self.event_durable_record_sequence
                    ),
                    "observed_wall_ns": self.observed_wall_ns,
                    "observed_monotonic_ns": self.observed_monotonic_ns,
                },
            ),
        )


@dataclass(frozen=True, slots=True, repr=False)
class ConsensusL2BarrierStateV1(_ResearchValue):
    canonical_match_id: str
    market_tickers: tuple[str, str]
    market_ids: tuple[str, str]
    pending_transition: AcceptedScoreConsensusTransitionV1 | None
    last_transition: AcceptedScoreConsensusTransitionV1 | None
    last_transition_resolution: (
        ConsensusL2ResearchFrameV1 | ConsensusL2CoverageV1 | None
    )
    supporter_watermarks: tuple[DurableRawScoreSupportRefV1, ...]
    last_durable_record_sequence: int
    last_consumed_event_kind: ConsensusL2DurableEventKindV1 | None
    last_consumed_event_sha256: str | None
    session_ended: bool

    def __post_init__(self) -> None:
        _safe_id(self.canonical_match_id)
        tickers, identifiers = _market_universe(
            self.market_tickers,
            self.market_ids,
        )
        object.__setattr__(self, "market_tickers", tickers)
        object.__setattr__(self, "market_ids", identifiers)
        for transition in (self.pending_transition, self.last_transition):
            if (
                transition is not None
                and type(transition) is not AcceptedScoreConsensusTransitionV1
            ):
                _fail("barrier_state_invalid")
            if transition is not None and (
                transition.canonical_match_id != self.canonical_match_id
                or transition.market_tickers != self.market_tickers
                or transition.market_ids != self.market_ids
            ):
                _fail("barrier_state_universe_mismatch")
        if (
            self.pending_transition is not None
            and (
                self.last_transition is None
                or self.pending_transition.accepted_score_sha256
                != self.last_transition.accepted_score_sha256
                or self.pending_transition != self.last_transition
            )
        ):
            _fail("barrier_state_invalid")
        resolution = self.last_transition_resolution
        if resolution is not None and type(resolution) not in (
            ConsensusL2ResearchFrameV1,
            ConsensusL2CoverageV1,
        ):
            _fail("barrier_state_resolution_invalid")
        if self.last_transition is None:
            if resolution is not None:
                _fail("barrier_state_resolution_invalid")
        elif self.pending_transition is not None:
            if resolution is not None:
                _fail("barrier_state_resolution_invalid")
        elif resolution is None:
            _fail("barrier_state_resolution_missing")
        elif (
            resolution.consensus_transition != self.last_transition
        ):
            _fail("barrier_state_resolution_mismatch")
        if (
            type(self.supporter_watermarks) is not tuple
            or any(
                type(item) is not DurableRawScoreSupportRefV1
                for item in self.supporter_watermarks
            )
            or tuple(
                sorted(
                    self.supporter_watermarks,
                    key=lambda item: item.source_id,
                )
            )
            != self.supporter_watermarks
            or len(
                {item.source_id for item in self.supporter_watermarks}
            )
            != len(self.supporter_watermarks)
            or type(self.last_durable_record_sequence) is not int
            or self.last_durable_record_sequence < 0
            or self.last_durable_record_sequence > _MAX_SIGNED_64
            or type(self.session_ended) is not bool
        ):
            _fail("barrier_state_invalid")
        independence_by_source_lineage: dict[str, str | None] = {}
        for watermark in self.supporter_watermarks:
            prior_identity = independence_by_source_lineage.get(
                watermark.source_lineage_sha256
            )
            if (
                watermark.source_lineage_sha256
                in independence_by_source_lineage
                and prior_identity != watermark.independence_lineage_id
            ):
                _fail("supporter_watermark_lineage_alias")
            independence_by_source_lineage[
                watermark.source_lineage_sha256
            ] = watermark.independence_lineage_id
        if self.last_transition is None:
            if self.supporter_watermarks:
                _fail("supporter_watermark_without_transition")
        else:
            watermarks_by_source = {
                watermark.source_id: watermark
                for watermark in self.supporter_watermarks
            }
            if any(
                watermark.durable_record_sequence
                >= self.last_transition.consensus_record_sequence
                or watermark.received_wall_ns
                > self.last_transition.consensus_accepted_wall_ns
                or watermark.received_monotonic_ns
                > self.last_transition.consensus_accepted_monotonic_ns
                for watermark in self.supporter_watermarks
            ):
                _fail("supporter_watermark_from_future")
            for supporter in self.last_transition.supporters:
                watermark = watermarks_by_source.get(supporter.source_id)
                if (
                    watermark is None
                    or watermark.source_lineage_sha256
                    != supporter.source_lineage_sha256
                    or watermark.independence_lineage_id
                    != supporter.independence_lineage_id
                    or watermark.durable_record_sequence
                    < supporter.durable_record_sequence
                    or watermark.received_wall_ns
                    < supporter.received_wall_ns
                    or watermark.received_monotonic_ns
                    < supporter.received_monotonic_ns
                ):
                    _fail("supporter_watermark_missing")
        if (
            self.last_transition is not None
            and self.last_durable_record_sequence
            < self.last_transition.consensus_record_sequence
        ):
            _fail("barrier_state_invalid")
        if self.last_durable_record_sequence == 0:
            if (
                self.last_consumed_event_kind is not None
                or self.last_consumed_event_sha256 is not None
            ):
                _fail("barrier_state_invalid")
        elif (
            type(self.last_consumed_event_kind)
            is not ConsensusL2DurableEventKindV1
            or self.last_consumed_event_sha256 is None
        ):
            _fail("barrier_state_invalid")
        else:
            _sha256(self.last_consumed_event_sha256)
        if resolution is not None:
            if type(resolution) is ConsensusL2ResearchFrameV1:
                resolution_sequence = (
                    resolution.l2_observation.raw_parent.durable_record_sequence
                )
                resolution_kind = ConsensusL2DurableEventKindV1.L2_BOOK
                resolution_sha256 = (
                    resolution.l2_observation.observation_sha256
                )
            else:
                resolution_sequence = (
                    resolution.event_durable_record_sequence
                )
                if (
                    self.last_durable_record_sequence
                    == resolution_sequence
                    and self.last_consumed_event_kind
                    is ConsensusL2DurableEventKindV1.CENSOR
                ):
                    resolution_kind = ConsensusL2DurableEventKindV1.CENSOR
                    resolution_sha256 = _censor_event_sha256(
                        resolution.reason,
                        event_sha256=resolution.event_sha256,
                        durable_record_sequence=resolution_sequence,
                        observed_wall_ns=resolution.observed_wall_ns,
                        observed_monotonic_ns=(
                            resolution.observed_monotonic_ns
                        ),
                    )
                else:
                    resolution_kind = ConsensusL2DurableEventKindV1.L2_BOOK
                    resolution_sha256 = resolution.event_sha256
            if (
                resolution_sequence > self.last_durable_record_sequence
                or (
                    resolution_sequence
                    == self.last_durable_record_sequence
                    and (
                        self.last_consumed_event_kind is not resolution_kind
                        or self.last_consumed_event_sha256
                        != resolution_sha256
                    )
                )
            ):
                _fail("barrier_state_resolution_invalid")
        if (
            self.last_transition is not None
            and self.last_durable_record_sequence
            == self.last_transition.consensus_record_sequence
            and (
                self.pending_transition != self.last_transition
                or self.last_consumed_event_kind
                is not ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS
                or self.last_consumed_event_sha256
                != self.last_transition.accepted_score_sha256
            )
        ):
            _fail("barrier_state_invalid")
        if (
            self.last_consumed_event_kind
            is ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS
            and (
                self.last_transition is None
                or self.pending_transition is None
                or self.last_durable_record_sequence
                != self.last_transition.consensus_record_sequence
                or self.last_consumed_event_sha256
                != self.last_transition.accepted_score_sha256
            )
        ):
            _fail("barrier_state_invalid")
        if self.pending_transition is not None:
            pending_sequence = (
                self.pending_transition.consensus_record_sequence
            )
            if self.last_durable_record_sequence < pending_sequence:
                _fail("barrier_state_invalid")
            if self.last_durable_record_sequence == pending_sequence:
                if (
                    self.last_consumed_event_kind
                    is not ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS
                    or self.last_consumed_event_sha256
                    != self.pending_transition.accepted_score_sha256
                ):
                    _fail("barrier_state_invalid")
            elif (
                self.last_consumed_event_kind
                is not ConsensusL2DurableEventKindV1.L2_BOOK
            ):
                _fail("barrier_state_invalid")
        if self.session_ended and self.pending_transition is not None:
            _fail("barrier_state_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class ConsensusL2BarrierUpdateV1(_ResearchValue):
    state: ConsensusL2BarrierStateV1
    disposition: ConsensusL2DispositionV1
    frame: ConsensusL2ResearchFrameV1 | None
    coverage: ConsensusL2CoverageV1 | None

    def __post_init__(self) -> None:
        if (
            type(self.state) is not ConsensusL2BarrierStateV1
            or type(self.disposition) is not ConsensusL2DispositionV1
            or (
                self.frame is not None
                and type(self.frame) is not ConsensusL2ResearchFrameV1
            )
            or (
                self.coverage is not None
                and type(self.coverage) is not ConsensusL2CoverageV1
            )
        ):
            _fail("barrier_update_invalid")
        shape = (
            self.frame is not None,
            self.coverage is not None,
            self.state.pending_transition is not None,
        )
        allowed_shapes = {
            ConsensusL2DispositionV1.ARMED: (False, False, True),
            ConsensusL2DispositionV1.ADVANCED: (False, True, True),
            ConsensusL2DispositionV1.IGNORED: (False, False, shape[2]),
            ConsensusL2DispositionV1.PAIRED: (True, False, False),
            ConsensusL2DispositionV1.CENSORED: (False, True, False),
        }
        if shape != allowed_shapes[self.disposition]:
            _fail("barrier_update_invalid")
        if self.disposition in (
            ConsensusL2DispositionV1.ARMED,
            ConsensusL2DispositionV1.ADVANCED,
        ):
            pending = self.state.pending_transition
            if (
                pending is None
                or self.state.last_durable_record_sequence
                != pending.consensus_record_sequence
                or self.state.last_consumed_event_kind
                is not ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS
                or self.state.last_consumed_event_sha256
                != pending.accepted_score_sha256
            ):
                _fail("barrier_update_invalid")
        if self.disposition is ConsensusL2DispositionV1.ADVANCED:
            pending = self.state.pending_transition
            coverage = self.coverage
            if (
                pending is None
                or coverage is None
                or coverage.reason
                is not ConsensusL2CensorReasonV1.SCORE_ADVANCED
                or coverage.event_sha256 != pending.accepted_score_sha256
                or coverage.event_durable_record_sequence
                != pending.consensus_record_sequence
                or coverage.observed_wall_ns
                != pending.consensus_accepted_wall_ns
                or coverage.observed_monotonic_ns
                != pending.consensus_accepted_monotonic_ns
                or pending.prior_accepted_score_sha256 is None
                or coverage.consensus_transition.accepted_score_sha256
                != pending.prior_accepted_score_sha256
                or coverage.consensus_transition.canonical_match_id
                != pending.canonical_match_id
                or coverage.consensus_transition.market_tickers
                != pending.market_tickers
                or coverage.consensus_transition.market_ids
                != pending.market_ids
                or (
                    coverage.consensus_transition.consensus_epoch,
                    coverage.consensus_transition.correction_epoch,
                    coverage.consensus_transition.accepted_state.revision,
                )
                >= (
                    pending.consensus_epoch,
                    pending.correction_epoch,
                    pending.accepted_state.revision,
                )
            ):
                _fail("barrier_update_invalid")
        elif self.disposition is ConsensusL2DispositionV1.PAIRED:
            frame = self.frame
            if (
                frame is None
                or self.state.session_ended
                or self.state.last_transition_resolution != frame
                or self.state.last_transition != frame.consensus_transition
                or self.state.last_durable_record_sequence
                != frame.l2_observation.raw_parent.durable_record_sequence
                or self.state.last_consumed_event_kind
                is not ConsensusL2DurableEventKindV1.L2_BOOK
                or self.state.last_consumed_event_sha256
                != frame.l2_observation.observation_sha256
            ):
                _fail("barrier_update_invalid")
        elif self.disposition is ConsensusL2DispositionV1.CENSORED:
            coverage = self.coverage
            if coverage is None:
                _fail("barrier_update_invalid")
            if (
                self.state.last_consumed_event_kind
                is ConsensusL2DurableEventKindV1.CENSOR
            ):
                expected_event_sha256 = _censor_event_sha256(
                    coverage.reason,
                    event_sha256=coverage.event_sha256,
                    durable_record_sequence=(
                        coverage.event_durable_record_sequence
                    ),
                    observed_wall_ns=coverage.observed_wall_ns,
                    observed_monotonic_ns=coverage.observed_monotonic_ns,
                )
            elif (
                self.state.last_consumed_event_kind
                is ConsensusL2DurableEventKindV1.L2_BOOK
            ):
                expected_event_sha256 = coverage.event_sha256
            else:
                _fail("barrier_update_invalid")
            if (
                self.state.last_transition_resolution != coverage
                or self.state.last_transition
                != coverage.consensus_transition
                or self.state.last_durable_record_sequence
                != coverage.event_durable_record_sequence
                or self.state.last_consumed_event_sha256
                != expected_event_sha256
                or self.state.session_ended
                != (
                    coverage.reason
                    is ConsensusL2CensorReasonV1.SESSION_ENDED
                )
            ):
                _fail("barrier_update_invalid")


def initial_consensus_l2_barrier_v1(
    canonical_match_id: str,
    market_tickers: tuple[str, str],
    market_ids: tuple[str, str],
) -> ConsensusL2BarrierStateV1:
    return ConsensusL2BarrierStateV1(
        canonical_match_id=canonical_match_id,
        market_tickers=market_tickers,
        market_ids=market_ids,
        pending_transition=None,
        last_transition=None,
        last_transition_resolution=None,
        supporter_watermarks=(),
        last_durable_record_sequence=0,
        last_consumed_event_kind=None,
        last_consumed_event_sha256=None,
        session_ended=False,
    )


def _ignored(
    state: ConsensusL2BarrierStateV1,
) -> ConsensusL2BarrierUpdateV1:
    return ConsensusL2BarrierUpdateV1(
        state=state,
        disposition=ConsensusL2DispositionV1.IGNORED,
        frame=None,
        coverage=None,
    )


def _durable_event_is_new(
    state: ConsensusL2BarrierStateV1,
    kind: ConsensusL2DurableEventKindV1,
    sequence: int,
    event_sha256: str,
) -> bool:
    _integer(sequence, positive=True)
    _sha256(event_sha256)
    if sequence < state.last_durable_record_sequence:
        _fail("global_durable_watermark_regression")
    if sequence == state.last_durable_record_sequence:
        if (
            state.last_consumed_event_kind is kind
            and state.last_consumed_event_sha256 == event_sha256
        ):
            return False
        _fail("global_durable_event_conflict")
    return True


def _state_with_consumed_event(
    state: ConsensusL2BarrierStateV1,
    *,
    kind: ConsensusL2DurableEventKindV1,
    sequence: int,
    event_sha256: str,
    session_ended: bool | None = None,
) -> ConsensusL2BarrierStateV1:
    return ConsensusL2BarrierStateV1(
        canonical_match_id=state.canonical_match_id,
        market_tickers=state.market_tickers,
        market_ids=state.market_ids,
        pending_transition=state.pending_transition,
        last_transition=state.last_transition,
        last_transition_resolution=state.last_transition_resolution,
        supporter_watermarks=state.supporter_watermarks,
        last_durable_record_sequence=sequence,
        last_consumed_event_kind=kind,
        last_consumed_event_sha256=event_sha256,
        session_ended=(
            state.session_ended
            if session_ended is None
            else session_ended
        ),
    )


def _censor_event_sha256(
    reason: ConsensusL2CensorReasonV1,
    *,
    event_sha256: str,
    durable_record_sequence: int,
    observed_wall_ns: int,
    observed_monotonic_ns: int,
) -> str:
    return _digest(
        _CENSOR_EVENT_DOMAIN,
        {
            "schema_version": 1,
            "reason": reason,
            "event_sha256": event_sha256,
            "durable_record_sequence": durable_record_sequence,
            "observed_wall_ns": observed_wall_ns,
            "observed_monotonic_ns": observed_monotonic_ns,
        },
    )


def _transition_key(
    transition: AcceptedScoreConsensusTransitionV1,
) -> tuple[int, int, int]:
    return (
        transition.consensus_epoch,
        transition.correction_epoch,
        transition.accepted_state.revision,
    )


def _validate_transition_universe(
    state: ConsensusL2BarrierStateV1,
    transition: AcceptedScoreConsensusTransitionV1,
) -> None:
    if (
        transition.canonical_match_id != state.canonical_match_id
        or transition.market_tickers != state.market_tickers
        or transition.market_ids != state.market_ids
    ):
        _fail("barrier_universe_mismatch")


def _merge_supporter_watermarks(
    prior: tuple[DurableRawScoreSupportRefV1, ...],
    current: tuple[DurableRawScoreSupportRefV1, ...],
) -> tuple[DurableRawScoreSupportRefV1, ...]:
    merged = {item.source_id: item for item in prior}
    for supporter in current:
        previous = merged.get(supporter.source_id)
        if previous is not None and (
            supporter.source_lineage_sha256
            != previous.source_lineage_sha256
            or supporter.independence_lineage_id
            != previous.independence_lineage_id
            or supporter.durable_record_sequence
            <= previous.durable_record_sequence
            or supporter.received_wall_ns < previous.received_wall_ns
            or supporter.received_monotonic_ns
            < previous.received_monotonic_ns
        ):
            _fail("supporter_watermark_regression")
        merged[supporter.source_id] = supporter
    return tuple(sorted(merged.values(), key=lambda item: item.source_id))


def open_consensus_l2_barrier_v1(
    state: ConsensusL2BarrierStateV1,
    transition: AcceptedScoreConsensusTransitionV1,
) -> ConsensusL2BarrierUpdateV1:
    """Arm one already-authoritative accepted score transition."""

    if (
        type(state) is not ConsensusL2BarrierStateV1
        or type(transition) is not AcceptedScoreConsensusTransitionV1
        or state.session_ended
    ):
        _fail("barrier_open_invalid")
    _validate_transition_universe(state, transition)
    if not _durable_event_is_new(
        state,
        ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS,
        transition.consensus_record_sequence,
        transition.accepted_score_sha256,
    ):
        return _ignored(state)
    previous = state.last_transition
    if previous is None:
        if transition.prior_accepted_score_sha256 is not None:
            _fail("transition_parent_invalid")
    elif (
        transition.prior_accepted_score_sha256
        != previous.accepted_score_sha256
    ):
        _fail("transition_parent_invalid")
    if previous is not None:
        incoming_key = _transition_key(transition)
        previous_key = _transition_key(previous)
        if incoming_key == previous_key:
            if transition.accepted_score_sha256 == previous.accepted_score_sha256:
                return _ignored(state)
            _fail("transition_conflict")
        if incoming_key < previous_key:
            _fail("transition_regression")
        if state.pending_transition is not None and (
            transition.consensus_epoch
            != state.pending_transition.consensus_epoch
            or transition.correction_epoch
            != state.pending_transition.correction_epoch
        ):
            _fail("explicit_epoch_censor_required")
        if (
            transition.consensus_record_sequence
            <= previous.consensus_record_sequence
            or transition.consensus_accepted_wall_ns
            < previous.consensus_accepted_wall_ns
            or transition.consensus_accepted_monotonic_ns
            < previous.consensus_accepted_monotonic_ns
        ):
            _fail("transition_watermark_regression")
        if any(
            supporter.durable_record_sequence
            <= previous.consensus_record_sequence
            for supporter in transition.supporters
        ):
            _fail("supporter_global_watermark_regression")
    watermarks = _merge_supporter_watermarks(
        state.supporter_watermarks,
        transition.supporters,
    )
    coverage: ConsensusL2CoverageV1 | None = None
    if state.pending_transition is not None:
        coverage = ConsensusL2CoverageV1(
            consensus_transition=state.pending_transition,
            reason=ConsensusL2CensorReasonV1.SCORE_ADVANCED,
            event_sha256=transition.accepted_score_sha256,
            event_durable_record_sequence=(
                transition.consensus_record_sequence
            ),
            observed_wall_ns=transition.consensus_accepted_wall_ns,
            observed_monotonic_ns=(
                transition.consensus_accepted_monotonic_ns
            ),
        )
    next_state = ConsensusL2BarrierStateV1(
        canonical_match_id=state.canonical_match_id,
        market_tickers=state.market_tickers,
        market_ids=state.market_ids,
        pending_transition=transition,
        last_transition=transition,
        last_transition_resolution=None,
        supporter_watermarks=watermarks,
        last_durable_record_sequence=(
            transition.consensus_record_sequence
        ),
        last_consumed_event_kind=(
            ConsensusL2DurableEventKindV1.ACCEPTED_CONSENSUS
        ),
        last_consumed_event_sha256=transition.accepted_score_sha256,
        session_ended=False,
    )
    return ConsensusL2BarrierUpdateV1(
        state=next_state,
        disposition=(
            ConsensusL2DispositionV1.ADVANCED
            if coverage is not None
            else ConsensusL2DispositionV1.ARMED
        ),
        frame=None,
        coverage=coverage,
    )


def _validate_observation_universe(
    state: ConsensusL2BarrierStateV1,
    observation: UnqualifiedTwoMarketL2ObservationV1,
) -> None:
    if (
        observation.canonical_match_id != state.canonical_match_id
        or tuple(market.ticker for market in observation.markets)
        != state.market_tickers
        or tuple(market.market_id for market in observation.markets)
        != state.market_ids
    ):
        _fail("observation_universe_mismatch")


def observe_consensus_l2_book_v1(
    state: ConsensusL2BarrierStateV1,
    observation: UnqualifiedTwoMarketL2ObservationV1,
) -> ConsensusL2BarrierUpdateV1:
    """Pair only the first causally eligible ready, gap-free L2 copy."""

    if (
        type(state) is not ConsensusL2BarrierStateV1
        or type(observation) is not UnqualifiedTwoMarketL2ObservationV1
    ):
        _fail("book_observation_invalid")
    _validate_observation_universe(state, observation)
    sequence = observation.raw_parent.durable_record_sequence
    event_sha256 = observation.observation_sha256
    is_new = _durable_event_is_new(
        state,
        ConsensusL2DurableEventKindV1.L2_BOOK,
        sequence,
        event_sha256,
    )
    if not is_new:
        return _ignored(state)
    pending = state.pending_transition
    barrier = pending or state.last_transition
    if barrier is not None and (
        observation.captured_monotonic_ns
        < barrier.consensus_accepted_monotonic_ns
        or observation.global_sequence <= barrier.last_book_global_sequence
    ):
        return _ignored(
            _state_with_consumed_event(
                state,
                kind=ConsensusL2DurableEventKindV1.L2_BOOK,
                sequence=sequence,
                event_sha256=event_sha256,
            )
        )
    if pending is None:
        return _ignored(
            _state_with_consumed_event(
                state,
                kind=ConsensusL2DurableEventKindV1.L2_BOOK,
                sequence=sequence,
                event_sha256=event_sha256,
            )
        )
    if (
        observation.physical_connection_generation
        != pending.last_book_physical_connection_generation
    ):
        return _censor_consensus_l2_barrier_with_event_v1(
            state,
            ConsensusL2CensorReasonV1.BOOK_GENERATION_CHANGED,
            event_sha256=observation.observation_sha256,
            durable_record_sequence=sequence,
            observed_wall_ns=observation.captured_wall_ns,
            observed_monotonic_ns=observation.captured_monotonic_ns,
            consumed_event_kind=ConsensusL2DurableEventKindV1.L2_BOOK,
            consumed_event_sha256=event_sha256,
        )
    if observation.subscription_id != pending.last_book_subscription_id:
        return _censor_consensus_l2_barrier_with_event_v1(
            state,
            ConsensusL2CensorReasonV1.BOOK_RECONNECTED,
            event_sha256=observation.observation_sha256,
            durable_record_sequence=sequence,
            observed_wall_ns=observation.captured_wall_ns,
            observed_monotonic_ns=observation.captured_monotonic_ns,
            consumed_event_kind=ConsensusL2DurableEventKindV1.L2_BOOK,
            consumed_event_sha256=event_sha256,
        )
    if (
        observation.global_sequence
        != pending.last_book_global_sequence + 1
    ):
        return _censor_consensus_l2_barrier_with_event_v1(
            state,
            ConsensusL2CensorReasonV1.BOOK_SEQUENCE_GAP,
            event_sha256=observation.observation_sha256,
            durable_record_sequence=sequence,
            observed_wall_ns=observation.captured_wall_ns,
            observed_monotonic_ns=observation.captured_monotonic_ns,
            consumed_event_kind=ConsensusL2DurableEventKindV1.L2_BOOK,
            consumed_event_sha256=event_sha256,
        )
    frame = ConsensusL2ResearchFrameV1(pending, observation)
    next_state = ConsensusL2BarrierStateV1(
        canonical_match_id=state.canonical_match_id,
        market_tickers=state.market_tickers,
        market_ids=state.market_ids,
        pending_transition=None,
        last_transition=state.last_transition,
        last_transition_resolution=frame,
        supporter_watermarks=state.supporter_watermarks,
        last_durable_record_sequence=(
            observation.raw_parent.durable_record_sequence
        ),
        last_consumed_event_kind=ConsensusL2DurableEventKindV1.L2_BOOK,
        last_consumed_event_sha256=observation.observation_sha256,
        session_ended=state.session_ended,
    )
    return ConsensusL2BarrierUpdateV1(
        state=next_state,
        disposition=ConsensusL2DispositionV1.PAIRED,
        frame=frame,
        coverage=None,
    )


def _censor_consensus_l2_barrier_with_event_v1(
    state: ConsensusL2BarrierStateV1,
    reason: ConsensusL2CensorReasonV1,
    *,
    event_sha256: str,
    durable_record_sequence: int,
    observed_wall_ns: int,
    observed_monotonic_ns: int,
    consumed_event_kind: ConsensusL2DurableEventKindV1,
    consumed_event_sha256: str,
) -> ConsensusL2BarrierUpdateV1:
    if (
        type(state) is not ConsensusL2BarrierStateV1
        or type(reason) is not ConsensusL2CensorReasonV1
        or type(consumed_event_kind)
        is not ConsensusL2DurableEventKindV1
    ):
        _fail("censor_event_invalid")
    _sha256(event_sha256)
    _sha256(consumed_event_sha256)
    _integer(durable_record_sequence, positive=True)
    _integer(observed_wall_ns)
    _integer(observed_monotonic_ns)
    if not _durable_event_is_new(
        state,
        consumed_event_kind,
        durable_record_sequence,
        consumed_event_sha256,
    ):
        return _ignored(state)
    ended = state.session_ended or reason is ConsensusL2CensorReasonV1.SESSION_ENDED
    pending = state.pending_transition
    if pending is None:
        return _ignored(
            _state_with_consumed_event(
                state,
                kind=consumed_event_kind,
                sequence=durable_record_sequence,
                event_sha256=consumed_event_sha256,
                session_ended=ended,
            )
        )
    coverage = ConsensusL2CoverageV1(
        consensus_transition=pending,
        reason=reason,
        event_sha256=event_sha256,
        event_durable_record_sequence=durable_record_sequence,
        observed_wall_ns=observed_wall_ns,
        observed_monotonic_ns=observed_monotonic_ns,
    )
    next_state = ConsensusL2BarrierStateV1(
        canonical_match_id=state.canonical_match_id,
        market_tickers=state.market_tickers,
        market_ids=state.market_ids,
        pending_transition=None,
        last_transition=state.last_transition,
        last_transition_resolution=coverage,
        supporter_watermarks=state.supporter_watermarks,
        last_durable_record_sequence=durable_record_sequence,
        last_consumed_event_kind=consumed_event_kind,
        last_consumed_event_sha256=consumed_event_sha256,
        session_ended=ended,
    )
    return ConsensusL2BarrierUpdateV1(
        state=next_state,
        disposition=ConsensusL2DispositionV1.CENSORED,
        frame=None,
        coverage=coverage,
    )


def censor_consensus_l2_barrier_v1(
    state: ConsensusL2BarrierStateV1,
    reason: ConsensusL2CensorReasonV1,
    *,
    event_sha256: str,
    durable_record_sequence: int,
    observed_wall_ns: int,
    observed_monotonic_ns: int,
) -> ConsensusL2BarrierUpdateV1:
    """Consume one ordered censor event and close any pending barrier."""

    if (
        type(state) is not ConsensusL2BarrierStateV1
        or type(reason) is not ConsensusL2CensorReasonV1
    ):
        _fail("censor_event_invalid")
    _sha256(event_sha256)
    _integer(durable_record_sequence, positive=True)
    _integer(observed_wall_ns)
    _integer(observed_monotonic_ns)
    consumed_sha256 = _censor_event_sha256(
        reason,
        event_sha256=event_sha256,
        durable_record_sequence=durable_record_sequence,
        observed_wall_ns=observed_wall_ns,
        observed_monotonic_ns=observed_monotonic_ns,
    )
    return _censor_consensus_l2_barrier_with_event_v1(
        state,
        reason,
        event_sha256=event_sha256,
        durable_record_sequence=durable_record_sequence,
        observed_wall_ns=observed_wall_ns,
        observed_monotonic_ns=observed_monotonic_ns,
        consumed_event_kind=ConsensusL2DurableEventKindV1.CENSOR,
        consumed_event_sha256=consumed_sha256,
    )


def canonical_consensus_l2_research_bytes_v1(value: object) -> bytes:
    """Return canonical bytes for one immutable Version-1 research value."""

    if not isinstance(value, _ResearchValue):
        _fail("canonical_research_value_invalid")
    return _canonical_bytes(value)
