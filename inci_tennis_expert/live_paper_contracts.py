"""Immutable, paper-only score-coordination contracts.

These values deliberately do not reuse the offline ``PilotPointEvent``
authority.  A live score capture is sufficient only for paper coordination.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from re import ASCII, compile as pattern_compile

from inci_tennis_expert.contracts import PlayerSide, ProviderPoint, TennisState
from inci_tennis_expert.tennis_score import apply_point, validate_tennis_state


__all__ = (
    "LivePaperContractError",
    "PaperScoreTrust",
    "LivePaperScoreDecisionKind",
    "LivePaperSourceObservation",
    "LivePaperSupport",
    "LivePaperScoreAnchor",
    "LivePaperPointTransition",
    "LivePaperScoreDecision",
    "LivePaperRebaseCandidate",
    "LivePaperScoreCoordinatorState",
    "LivePaperMarketBinding",
    "live_paper_contract_sha256",
    "score_coordinates",
    "make_live_paper_anchor",
    "make_live_paper_transition",
)


_ID = pattern_compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", ASCII)
_SHA256 = pattern_compile(r"[0-9a-f]{64}\Z", ASCII)
_MAX_SIGNED_64 = 9_223_372_036_854_775_807
_PAPER_LOCAL_REVISION_AUTHORITY = "PAPER_LOCAL_REVISION_TRANSPORT_ONLY"


class LivePaperContractError(ValueError):
    """Fixed-code rejection for a paper-only score value."""


@dataclass(frozen=True, slots=True)
class LivePaperMarketBinding:
    """Frozen two-market identity and YES-to-player orientation."""

    canonical_match_id: str
    scheduled_start_wall_ns: int
    home_player_id: str
    away_player_id: str
    home_ticker: str
    home_market_id: str
    home_yes_player_side: PlayerSide
    away_ticker: str
    away_market_id: str
    away_yes_player_side: PlayerSide

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _integer(self.scheduled_start_wall_ns, "scheduled_start_wall_ns", positive=True)
        _id(self.home_player_id, "home_player_id")
        _id(self.away_player_id, "away_player_id")
        _id(self.home_ticker, "home_ticker")
        _id(self.away_ticker, "away_ticker")
        _id(self.home_market_id, "home_market_id")
        _id(self.away_market_id, "away_market_id")
        if (
            self.home_player_id == self.away_player_id
            or self.home_ticker == self.away_ticker
            or self.home_market_id == self.away_market_id
            or self.home_yes_player_side is not PlayerSide.HOME
            or self.away_yes_player_side is not PlayerSide.AWAY
        ):
            _fail("market_binding")


class PaperScoreTrust(str, Enum):
    CONSENSUS_PAPER = "CONSENSUS_PAPER"
    SINGLE_SOURCE_PAPER = "SINGLE_SOURCE_PAPER"
    ABSTAINED = "ABSTAINED"


class LivePaperScoreDecisionKind(str, Enum):
    ANCHORED = "anchored"
    UNCHANGED = "unchanged"
    POINT_ACCEPTED = "point_accepted"
    QUARANTINED = "quarantined"
    REBASED = "rebased"
    ABSTAINED = "abstained"


def _fail(code: str) -> None:
    raise LivePaperContractError(code)


def _id(value: object, name: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail(name)
    return value


def _digest(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(name)
    return value


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > _MAX_SIGNED_64:
        _fail(name)
    return value


def _state(value: object, name: str) -> TennisState:
    if type(value) is not TennisState:
        _fail(name)
    try:
        validate_tennis_state(value)
    except Exception:
        _fail(name)
    return value


def _unique_sorted_digests(value: object, name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) < minimum:
        _fail(name)
    if any(_SHA256.fullmatch(item) is None for item in value) or tuple(sorted(value)) != value or len(set(value)) != len(value):
        _fail(name)
    return value


def score_coordinates(state: TennisState) -> tuple[object, ...]:
    """Compare score meaning while excluding provider transport identity."""
    _state(state, "state")
    return (
        state.match_format,
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
        state.correction_epoch,
    )


def live_paper_contract_sha256(value: object) -> str:
    return sha256(b"INCI-LIVE-PAPER-SCORE-CONTRACT-V1\0" + _canonical_bytes(value)).hexdigest()


def _exact_point_successor(before: TennisState, after: TennisState, server: PlayerSide, winner: PlayerSide) -> bool:
    if before.server_for_next_point is not server:
        return False
    try:
        projected = apply_point(
            before,
            ProviderPoint(
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
                revision=before.revision + 1,
                point_winner=winner,
                server_before_point=server,
                source_wall_ns=after.last_source_wall_ns,
                source_generated_wall_ns=after.last_source_generated_wall_ns,
                received_monotonic_ns=after.last_received_monotonic_ns,
                clock_uncertainty_ns=after.last_clock_uncertainty_ns,
            ),
        ).state
    except Exception:
        return False
    return score_coordinates(projected) == score_coordinates(after)


def _canonical_project(value: object, active: set[int]) -> object:
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        _integer(value, "canonical_integer")
        return value
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


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        {"canonical_version": 1, "domain": "inci-live-paper-score", "value": _canonical_project(value, set())},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


@dataclass(frozen=True, slots=True)
class LivePaperSourceObservation:
    canonical_match_id: str
    provider_slot: str
    source_id: str
    independent_lineage_id: str
    lineage_sha256: str
    independence_proven: bool | None
    state: TennisState
    raw_receipt_sha256: str
    captured_wall_ns: int
    captured_monotonic_ns: int
    independence_proof_sha256: str | None = None
    authority_label: str = _PAPER_LOCAL_REVISION_AUTHORITY

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _id(self.provider_slot, "provider_slot")
        _id(self.source_id, "source_id")
        _id(self.independent_lineage_id, "independent_lineage_id")
        _digest(self.lineage_sha256, "lineage_sha256")
        if self.independence_proven is not None and type(self.independence_proven) is not bool:
            _fail("independence_proven")
        _state(self.state, "state")
        _digest(self.raw_receipt_sha256, "raw_receipt_sha256")
        _integer(self.captured_wall_ns, "captured_wall_ns")
        _integer(self.captured_monotonic_ns, "captured_monotonic_ns")
        if self.independence_proof_sha256 is not None:
            _digest(self.independence_proof_sha256, "independence_proof_sha256")
        if (self.independence_proven is True) != (
            self.independence_proof_sha256 is not None
        ):
            _fail("independence_proof_sha256")
        if self.authority_label != _PAPER_LOCAL_REVISION_AUTHORITY:
            _fail("authority_label")


@dataclass(frozen=True, slots=True)
class LivePaperSupport:
    raw_receipt_sha256: str
    lineage_sha256: str
    independent_lineage_id: str
    independence_proven: bool
    independence_proof_sha256: str | None = None

    def __post_init__(self) -> None:
        _digest(self.raw_receipt_sha256, "raw_receipt_sha256")
        _digest(self.lineage_sha256, "lineage_sha256")
        _id(self.independent_lineage_id, "independent_lineage_id")
        if type(self.independence_proven) is not bool:
            _fail("independence_proven")
        if self.independence_proof_sha256 is not None:
            _digest(self.independence_proof_sha256, "independence_proof_sha256")
        if self.independence_proven != (self.independence_proof_sha256 is not None):
            _fail("independence_proof_sha256")


def _supporting_sources(
    sources: object,
    *,
    lineages: tuple[str, ...],
    receipts: tuple[str, ...],
    proven_ids: tuple[str, ...],
    trust: PaperScoreTrust,
) -> None:
    if type(sources) is not tuple or not sources or any(type(source) is not LivePaperSupport for source in sources):
        _fail("supporting_sources")
    if tuple(sorted(sources, key=lambda source: source.raw_receipt_sha256)) != sources or len({source.raw_receipt_sha256 for source in sources}) != len(sources):
        _fail("supporting_sources")
    if lineages != tuple(sorted({source.lineage_sha256 for source in sources})) or receipts != tuple(source.raw_receipt_sha256 for source in sources):
        _fail("supporting_sources")
    actual_proven_ids = tuple(sorted({source.independent_lineage_id for source in sources if source.independence_proven}))
    all_ids = {source.independent_lineage_id for source in sources}
    if trust is PaperScoreTrust.CONSENSUS_PAPER and (
        len(actual_proven_ids) < 2
        or len({source.lineage_sha256 for source in sources if source.independence_proven}) < 2
        or any(not source.independence_proven for source in sources)
    ):
        _fail("consensus_support")
    if proven_ids != actual_proven_ids:
        _fail("supporting_sources")
    if trust is PaperScoreTrust.SINGLE_SOURCE_PAPER and len(all_ids) != 1:
        _fail("supporting_sources")


@dataclass(frozen=True, slots=True)
class LivePaperScoreAnchor:
    canonical_match_id: str
    state: TennisState
    trust: PaperScoreTrust
    supporting_lineage_sha256s: tuple[str, ...]
    parent_receipt_sha256s: tuple[str, ...]
    consensus_epoch: int
    correction_epoch: int
    rebase_epoch: int
    accepted_wall_ns: int
    accepted_monotonic_ns: int
    anchor_sha256: str
    supporting_independent_lineage_ids: tuple[str, ...] = ()
    supporting_sources: tuple[LivePaperSupport, ...] = ()

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _state(self.state, "state")
        if type(self.trust) is not PaperScoreTrust or self.trust is PaperScoreTrust.ABSTAINED:
            _fail("trust")
        _unique_sorted_digests(self.supporting_lineage_sha256s, "supporting_lineage_sha256s")
        _unique_sorted_digests(self.parent_receipt_sha256s, "parent_receipt_sha256s")
        independent_ids = self.supporting_independent_lineage_ids
        if type(independent_ids) is not tuple or tuple(sorted(independent_ids)) != independent_ids or len(set(independent_ids)) != len(independent_ids):
            _fail("supporting_independent_lineage_ids")
        if any(_ID.fullmatch(item) is None for item in independent_ids):
            _fail("supporting_independent_lineage_ids")
        if self.trust is PaperScoreTrust.CONSENSUS_PAPER and (len(self.supporting_lineage_sha256s) < 2 or len(independent_ids) < 2):
            _fail("supporting_lineage_sha256s")
        if self.trust is PaperScoreTrust.SINGLE_SOURCE_PAPER and len(independent_ids) > 1:
            _fail("supporting_independent_lineage_ids")
        _supporting_sources(
            self.supporting_sources,
            lineages=self.supporting_lineage_sha256s,
            receipts=self.parent_receipt_sha256s,
            proven_ids=independent_ids,
            trust=self.trust,
        )
        _integer(self.consensus_epoch, "consensus_epoch")
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.rebase_epoch, "rebase_epoch")
        _integer(self.accepted_wall_ns, "accepted_wall_ns")
        _integer(self.accepted_monotonic_ns, "accepted_monotonic_ns")
        _digest(self.anchor_sha256, "anchor_sha256")
        if self.correction_epoch != self.state.correction_epoch:
            _fail("correction_epoch")
        if self.anchor_sha256 != _anchor_digest(self):
            _fail("anchor_sha256")


@dataclass(frozen=True, slots=True)
class LivePaperPointTransition:
    canonical_match_id: str
    local_point_ordinal: int
    before_state: TennisState
    after_state: TennisState
    server: PlayerSide
    winner: PlayerSide
    trust: PaperScoreTrust
    supporting_lineage_sha256s: tuple[str, ...]
    parent_receipt_sha256s: tuple[str, ...]
    consensus_epoch: int
    correction_epoch: int
    rebase_epoch: int
    accepted_wall_ns: int
    accepted_monotonic_ns: int
    transition_sha256: str
    supporting_independent_lineage_ids: tuple[str, ...] = ()
    supporting_sources: tuple[LivePaperSupport, ...] = ()

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        _integer(self.local_point_ordinal, "local_point_ordinal", positive=True)
        _state(self.before_state, "before_state")
        _state(self.after_state, "after_state")
        if type(self.server) is not PlayerSide or type(self.winner) is not PlayerSide:
            _fail("point_transition")
        if type(self.trust) is not PaperScoreTrust or self.trust is PaperScoreTrust.ABSTAINED:
            _fail("trust")
        _unique_sorted_digests(self.supporting_lineage_sha256s, "supporting_lineage_sha256s")
        _unique_sorted_digests(self.parent_receipt_sha256s, "parent_receipt_sha256s")
        independent_ids = self.supporting_independent_lineage_ids
        if type(independent_ids) is not tuple or tuple(sorted(independent_ids)) != independent_ids or len(set(independent_ids)) != len(independent_ids):
            _fail("supporting_independent_lineage_ids")
        if any(_ID.fullmatch(item) is None for item in independent_ids):
            _fail("supporting_independent_lineage_ids")
        if self.trust is PaperScoreTrust.CONSENSUS_PAPER and (len(self.supporting_lineage_sha256s) < 2 or len(independent_ids) < 2):
            _fail("supporting_lineage_sha256s")
        if self.trust is PaperScoreTrust.SINGLE_SOURCE_PAPER and len(independent_ids) > 1:
            _fail("supporting_independent_lineage_ids")
        _supporting_sources(
            self.supporting_sources,
            lineages=self.supporting_lineage_sha256s,
            receipts=self.parent_receipt_sha256s,
            proven_ids=independent_ids,
            trust=self.trust,
        )
        _integer(self.consensus_epoch, "consensus_epoch")
        _integer(self.correction_epoch, "correction_epoch")
        _integer(self.rebase_epoch, "rebase_epoch")
        _integer(self.accepted_wall_ns, "accepted_wall_ns")
        _integer(self.accepted_monotonic_ns, "accepted_monotonic_ns")
        _digest(self.transition_sha256, "transition_sha256")
        if self.correction_epoch != self.after_state.correction_epoch:
            _fail("correction_epoch")
        if not _exact_point_successor(self.before_state, self.after_state, self.server, self.winner):
            _fail("point_transition")
        if self.transition_sha256 != _transition_digest(self):
            _fail("transition_sha256")


@dataclass(frozen=True, slots=True)
class LivePaperScoreDecision:
    kind: LivePaperScoreDecisionKind
    trust: PaperScoreTrust
    anchor: LivePaperScoreAnchor | None
    transition: LivePaperPointTransition | None
    reason: str

    def __post_init__(self) -> None:
        if type(self.kind) is not LivePaperScoreDecisionKind or type(self.trust) is not PaperScoreTrust:
            _fail("decision")
        if self.anchor is not None and type(self.anchor) is not LivePaperScoreAnchor:
            _fail("anchor")
        if self.transition is not None and type(self.transition) is not LivePaperPointTransition:
            _fail("transition")
        _id(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class LivePaperRebaseCandidate:
    state: TennisState
    independent_lineage_id: str
    first_captured_monotonic_ns: int
    latest_captured_monotonic_ns: int

    def __post_init__(self) -> None:
        _state(self.state, "state")
        _id(self.independent_lineage_id, "independent_lineage_id")
        _integer(self.first_captured_monotonic_ns, "first_captured_monotonic_ns")
        _integer(self.latest_captured_monotonic_ns, "latest_captured_monotonic_ns")
        if self.latest_captured_monotonic_ns < self.first_captured_monotonic_ns:
            _fail("latest_captured_monotonic_ns")


@dataclass(frozen=True, slots=True)
class LivePaperScoreCoordinatorState:
    canonical_match_id: str
    anchor: LivePaperScoreAnchor | None
    local_point_ordinal: int
    consensus_epoch: int
    rebase_epoch: int
    quarantined: bool
    quarantine_barrier_monotonic_ns: int | None
    rebase_candidate: LivePaperRebaseCandidate | None

    def __post_init__(self) -> None:
        _id(self.canonical_match_id, "canonical_match_id")
        if self.anchor is not None and type(self.anchor) is not LivePaperScoreAnchor:
            _fail("anchor")
        _integer(self.local_point_ordinal, "local_point_ordinal")
        _integer(self.consensus_epoch, "consensus_epoch")
        _integer(self.rebase_epoch, "rebase_epoch")
        if type(self.quarantined) is not bool:
            _fail("quarantined")
        if self.quarantine_barrier_monotonic_ns is not None:
            _integer(self.quarantine_barrier_monotonic_ns, "quarantine_barrier_monotonic_ns")
        if self.rebase_candidate is not None and type(self.rebase_candidate) is not LivePaperRebaseCandidate:
            _fail("rebase_candidate")
        if self.quarantined != (self.quarantine_barrier_monotonic_ns is not None):
            _fail("quarantine_barrier_monotonic_ns")
        if not self.quarantined and self.rebase_candidate is not None:
            _fail("rebase_candidate")
        if self.anchor is None and self.local_point_ordinal != 0:
            _fail("local_point_ordinal")
        if self.anchor is not None and (
            self.anchor.canonical_match_id != self.canonical_match_id
            or self.anchor.consensus_epoch != self.consensus_epoch
            or self.anchor.rebase_epoch != self.rebase_epoch
        ):
            _fail("anchor")


def _anchor_digest(anchor: LivePaperScoreAnchor) -> str:
    return _anchor_digest_values(**{
        name: getattr(anchor, name)
        for name in (
            "canonical_match_id", "state", "trust",
            "supporting_lineage_sha256s", "parent_receipt_sha256s",
            "consensus_epoch", "correction_epoch", "rebase_epoch",
            "accepted_wall_ns", "accepted_monotonic_ns",
            "supporting_independent_lineage_ids",
            "supporting_sources",
        )
    })


def _anchor_digest_values(**values: object) -> str:
    return live_paper_contract_sha256(values)


def _transition_digest(transition: LivePaperPointTransition) -> str:
    return _transition_digest_values(**{
        name: getattr(transition, name)
        for name in (
            "canonical_match_id", "local_point_ordinal", "before_state",
            "after_state", "server", "winner", "trust",
            "supporting_lineage_sha256s", "parent_receipt_sha256s",
            "consensus_epoch", "correction_epoch", "rebase_epoch",
            "accepted_wall_ns", "accepted_monotonic_ns",
            "supporting_independent_lineage_ids",
            "supporting_sources",
        )
    })


def _transition_digest_values(**values: object) -> str:
    return live_paper_contract_sha256(values)


def make_live_paper_anchor(**values: object) -> LivePaperScoreAnchor:
    return LivePaperScoreAnchor(
        anchor_sha256=_anchor_digest_values(**values),
        **values,
    )  # type: ignore[arg-type]


def make_live_paper_transition(**values: object) -> LivePaperPointTransition:
    return LivePaperPointTransition(
        transition_sha256=_transition_digest_values(**values),
        **values,
    )  # type: ignore[arg-type]
