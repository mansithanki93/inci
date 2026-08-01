from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from inci_tennis_expert.contracts import (
    MarketStatus,
    MatchFormat,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SyncReason,
    SynchronizationSessionState,
    SynchronizationTransitionResult,
)
from inci_tennis_expert.synchronizer import (
    validate_synchronization_transition,
)


class MonitorInputError(ValueError):
    pass


class SyncDisplayState(str, Enum):
    WAITING = "waiting"
    TRUSTED = "trusted"
    UNCHANGED = "unchanged"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ContractMonitorView:
    player_side: PlayerSide
    player_id: str
    ticker: str
    market_status: MarketStatus | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    bid_quantity: Decimal | None
    ask_quantity: Decimal | None
    spread: Decimal | None
    book_age_ns: int | None
    connection_epoch: int | None
    sequence: int | None
    sync_state: SyncDisplayState
    reason: SyncReason
    evaluated_monotonic_ns: int | None

    def __post_init__(self) -> None:
        if type(self.player_side) is not PlayerSide:
            raise MonitorInputError("player_side")
        if type(self.player_id) is not str or not self.player_id:
            raise MonitorInputError("player_id")
        if type(self.ticker) is not str or not self.ticker:
            raise MonitorInputError("ticker")
        if self.market_status is not None and type(self.market_status) is not MarketStatus:
            raise MonitorInputError("market_status")
        for value, name in (
            (self.yes_bid, "yes_bid"),
            (self.yes_ask, "yes_ask"),
            (self.bid_quantity, "bid_quantity"),
            (self.ask_quantity, "ask_quantity"),
            (self.spread, "spread"),
        ):
            if value is not None and type(value) is not Decimal:
                raise MonitorInputError(name)
        if (self.yes_bid is None) != (self.bid_quantity is None):
            raise MonitorInputError("bid")
        if (self.yes_ask is None) != (self.ask_quantity is None):
            raise MonitorInputError("ask")
        if self.spread is None:
            if self.yes_bid is not None and self.yes_ask is not None:
                raise MonitorInputError("spread")
        elif (
            self.yes_bid is None
            or self.yes_ask is None
            or self.spread != self.yes_ask - self.yes_bid
            or self.spread < Decimal("0")
        ):
            raise MonitorInputError("spread")
        for value, name in (
            (self.book_age_ns, "book_age_ns"),
            (self.connection_epoch, "connection_epoch"),
            (self.sequence, "sequence"),
            (self.evaluated_monotonic_ns, "evaluated_monotonic_ns"),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise MonitorInputError(name)
        if (self.connection_epoch is None) != (self.sequence is None):
            raise MonitorInputError("book_identity")
        if type(self.sync_state) is not SyncDisplayState:
            raise MonitorInputError("sync_state")
        if type(self.reason) is not SyncReason:
            raise MonitorInputError("reason")
        if self.sync_state is SyncDisplayState.TRUSTED and self.reason is not SyncReason.TRUSTED_SYNCHRONIZED:
            raise MonitorInputError("trusted_reason")
        if self.sync_state is SyncDisplayState.WAITING and self.reason is not SyncReason.SNAPSHOT_INCOMPLETE:
            raise MonitorInputError("waiting_reason")


@dataclass(frozen=True, slots=True)
class MatchMonitorView:
    mode_label: str
    canonical_match_id: str
    provider_match_id: str
    event_ticker: str
    match_format: MatchFormat
    home_player_id: str
    away_player_id: str
    match_status: MatchStatus | None
    completed_sets: tuple[tuple[int, int], ...]
    games: tuple[int, int] | None
    points: tuple[str, str] | None
    server: PlayerSide | None
    provider_revision: int | None
    provider_age_ns: int | None
    observed_wall_ns: int | None
    observed_monotonic_ns: int | None
    decision_sequence: int
    contracts: tuple[ContractMonitorView, ContractMonitorView]

    def __post_init__(self) -> None:
        for value, name in (
            (self.mode_label, "mode_label"),
            (self.canonical_match_id, "canonical_match_id"),
            (self.provider_match_id, "provider_match_id"),
            (self.event_ticker, "event_ticker"),
            (self.home_player_id, "home_player_id"),
            (self.away_player_id, "away_player_id"),
        ):
            if type(value) is not str or not value:
                raise MonitorInputError(name)
        if type(self.match_format) is not MatchFormat:
            raise MonitorInputError("match_format")
        if self.match_status is not None and type(self.match_status) is not MatchStatus:
            raise MonitorInputError("match_status")
        if type(self.completed_sets) is not tuple or any(
            type(value) is not tuple
            or len(value) != 2
            or any(type(score) is not int or score < 0 for score in value)
            for value in self.completed_sets
        ):
            raise MonitorInputError("completed_sets")
        if self.games is not None and (
            type(self.games) is not tuple
            or len(self.games) != 2
            or any(type(value) is not int or value < 0 for value in self.games)
        ):
            raise MonitorInputError("games")
        if self.points is not None and (
            type(self.points) is not tuple
            or len(self.points) != 2
            or any(type(value) is not str or not value for value in self.points)
        ):
            raise MonitorInputError("points")
        if self.server is not None and type(self.server) is not PlayerSide:
            raise MonitorInputError("server")
        for value, name in (
            (self.provider_revision, "provider_revision"),
            (self.provider_age_ns, "provider_age_ns"),
            (self.observed_wall_ns, "observed_wall_ns"),
            (self.observed_monotonic_ns, "observed_monotonic_ns"),
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise MonitorInputError(name)
        if (self.observed_wall_ns is None) != (self.observed_monotonic_ns is None):
            raise MonitorInputError("observation")
        if type(self.decision_sequence) is not int or self.decision_sequence < 0:
            raise MonitorInputError("decision_sequence")
        if (
            type(self.contracts) is not tuple
            or len(self.contracts) != 2
            or type(self.contracts[0]) is not ContractMonitorView
            or type(self.contracts[1]) is not ContractMonitorView
            or self.contracts[0].player_side is not PlayerSide.HOME
            or self.contracts[1].player_side is not PlayerSide.AWAY
            or self.contracts[0].ticker == self.contracts[1].ticker
        ):
            raise MonitorInputError("contracts")


@dataclass(frozen=True, slots=True)
class _SyncMark:
    state: SyncDisplayState
    reason: SyncReason
    evaluated_monotonic_ns: int | None


class OneMatchShadowMonitor:
    def __init__(
        self,
        initial_state: SynchronizationSessionState,
        canonical_match_id: str,
    ) -> None:
        if type(initial_state) is not SynchronizationSessionState:
            raise MonitorInputError("initial_state")
        if type(canonical_match_id) is not str or not canonical_match_id:
            raise MonitorInputError("canonical_match_id")
        try:
            SynchronizationSessionState.__post_init__(initial_state)
        except (TypeError, ValueError):
            raise MonitorInputError("initial_state") from None
        bindings = tuple(
            binding
            for binding in initial_state.universe.bindings
            if binding.canonical_match_id == canonical_match_id
        )
        metadata = tuple(
            item
            for item in initial_state.universe.metadata
            if item.canonical_match_id == canonical_match_id
        )
        if len(bindings) != 1 or len(metadata) != 1:
            raise MonitorInputError("match_binding")
        self._state = initial_state
        self._binding = bindings[0]
        self._metadata = metadata[0]
        self._marks = {
            self._binding.home_market_ticker: _SyncMark(
                SyncDisplayState.WAITING,
                SyncReason.SNAPSHOT_INCOMPLETE,
                None,
            ),
            self._binding.away_market_ticker: _SyncMark(
                SyncDisplayState.WAITING,
                SyncReason.SNAPSHOT_INCOMPLETE,
                None,
            ),
        }

    def accept(self, transition: SynchronizationTransitionResult) -> None:
        if type(transition) is not SynchronizationTransitionResult:
            raise MonitorInputError("transition_invalid")
        if transition.input.canonical_match_id != self._binding.canonical_match_id:
            raise MonitorInputError("wrong_match")
        try:
            validate_synchronization_transition(self._state, transition)
        except (TypeError, ValueError, RuntimeError):
            raise MonitorInputError("transition_invalid") from None
        allowed = set(self._marks)
        pending: dict[str, _SyncMark] = {}
        for result in transition.results:
            if result.canonical_match_id != self._binding.canonical_match_id:
                raise MonitorInputError("wrong_match")
            if result.ticker not in allowed:
                raise MonitorInputError("wrong_ticker")
            prior = self._marks[result.ticker]
            if result.reason is SyncReason.TRUSTED_SYNCHRONIZED:
                state = SyncDisplayState.TRUSTED
            elif (
                result.reason is SyncReason.DUPLICATE_STATE_SUPPRESSED
                and prior.state in {SyncDisplayState.TRUSTED, SyncDisplayState.UNCHANGED}
            ):
                state = SyncDisplayState.UNCHANGED
            elif result.reason is SyncReason.SNAPSHOT_INCOMPLETE:
                state = SyncDisplayState.WAITING
            else:
                state = SyncDisplayState.BLOCKED
            pending[result.ticker] = _SyncMark(
                state,
                result.reason,
                transition.observation.monotonic_ns,
            )
        self._state = transition.state
        self._marks.update(pending)

    def view(self) -> MatchMonitorView:
        tennis_cursor = next(
            cursor
            for cursor in self._state.tennis_cursors
            if cursor.canonical_match_id == self._binding.canonical_match_id
        )
        tennis = tennis_cursor.tennis
        observation = self._state.last_observation
        if tennis is None:
            status = None
            completed_sets: tuple[tuple[int, int], ...] = ()
            games = None
            points = None
            server = None
            revision = None
            provider_age = None
        else:
            status = tennis.status
            completed_sets = tuple(
                (value.games_home, value.games_away)
                for value in tennis.completed_sets
            )
            games = (tennis.games_home, tennis.games_away)
            points = _point_labels(tennis)
            server = tennis.server_for_next_point
            revision = tennis.revision
            provider_age = _elapsed_ns(
                None if observation is None else observation.monotonic_ns,
                tennis.last_received_monotonic_ns,
            )
        contracts = (
            self._contract_view(PlayerSide.HOME),
            self._contract_view(PlayerSide.AWAY),
        )
        return MatchMonitorView(
            mode_label="REPLAY / READ-ONLY",
            canonical_match_id=self._binding.canonical_match_id,
            provider_match_id=self._binding.provider_match_id,
            event_ticker=self._binding.kalshi_event_ticker,
            match_format=self._binding.match_format,
            home_player_id=self._metadata.canonical_home_player_id,
            away_player_id=self._metadata.canonical_away_player_id,
            match_status=status,
            completed_sets=completed_sets,
            games=games,
            points=points,
            server=server,
            provider_revision=revision,
            provider_age_ns=provider_age,
            observed_wall_ns=None if observation is None else observation.wall_ns,
            observed_monotonic_ns=(
                None if observation is None else observation.monotonic_ns
            ),
            decision_sequence=self._state.decision_sequence,
            contracts=contracts,
        )

    def _contract_view(self, side: PlayerSide) -> ContractMonitorView:
        if side is PlayerSide.HOME:
            ticker = self._binding.home_market_ticker
            player_id = self._metadata.canonical_home_player_id
        else:
            ticker = self._binding.away_market_ticker
            player_id = self._metadata.canonical_away_player_id
        cursor = next(
            value
            for value in self._state.book_cursors
            if value.canonical_match_id == self._binding.canonical_match_id
            and value.ticker == ticker
        )
        book = cursor.book
        observation = self._state.last_observation
        mark = self._marks[ticker]
        visible = mark.state in {
            SyncDisplayState.TRUSTED,
            SyncDisplayState.UNCHANGED,
        }
        if book is None or not visible:
            market_status = None
            yes_bid = None
            yes_ask = None
            bid_quantity = None
            ask_quantity = None
            spread = None
            book_age = None
            connection_epoch = None
            sequence = None
        else:
            market_status = book.market_status
            best_yes = None if not book.yes_bids else book.yes_bids[0]
            best_no = None if not book.no_bids else book.no_bids[0]
            yes_bid = None if best_yes is None else best_yes.price
            bid_quantity = None if best_yes is None else best_yes.quantity
            yes_ask = None if best_no is None else Decimal("1") - best_no.price
            ask_quantity = None if best_no is None else best_no.quantity
            spread = (
                None
                if yes_bid is None or yes_ask is None
                else yes_ask - yes_bid
            )
            book_age = _elapsed_ns(
                None if observation is None else observation.monotonic_ns,
                book.book_observed_monotonic_ns,
            )
            connection_epoch = book.connection_epoch
            sequence = book.sequence
        return ContractMonitorView(
            player_side=side,
            player_id=player_id,
            ticker=ticker,
            market_status=market_status,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            bid_quantity=bid_quantity,
            ask_quantity=ask_quantity,
            spread=spread,
            book_age_ns=book_age,
            connection_epoch=connection_epoch,
            sequence=sequence,
            sync_state=mark.state,
            reason=mark.reason,
            evaluated_monotonic_ns=mark.evaluated_monotonic_ns,
        )


_POINT_LABELS = {
    ScoreValue.LOVE: "0",
    ScoreValue.FIFTEEN: "15",
    ScoreValue.THIRTY: "30",
    ScoreValue.FORTY: "40",
    ScoreValue.ADVANTAGE: "AD",
}


def _elapsed_ns(now_ns: int | None, source_ns: int) -> int | None:
    if now_ns is None or now_ns < source_ns:
        return None
    return now_ns - source_ns


def _point_labels(tennis: object) -> tuple[str, str]:
    if tennis.in_tiebreak:
        return (
            str(tennis.tiebreak_points_home),
            str(tennis.tiebreak_points_away),
        )
    return (_POINT_LABELS[tennis.points_home], _POINT_LABELS[tennis.points_away])


def _missing(value: object | None) -> str:
    return "--" if value is None else str(value)


def _cents(value: Decimal | None) -> str:
    return "--" if value is None else f"{value * Decimal('100'):.1f}c"


def _quantity(value: Decimal | None) -> str:
    if value is None:
        return "--"
    return format(value.normalize(), "f")


def _age(value: int | None) -> str:
    if value is None:
        return "--"
    if value < 1_000:
        return f"{value}ns"
    if value < 1_000_000:
        return f"{value / 1_000:.1f}us"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.1f}ms"
    return f"{value / 1_000_000_000:.2f}s"


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width == 1:
        return "…"
    return value[: width - 1] + "…"


def render_monitor(view: MatchMonitorView, *, width: int = 120) -> str:
    if type(view) is not MatchMonitorView:
        raise MonitorInputError("view")
    if type(width) is not int or width < 60 or width > 240:
        raise MonitorInputError("width")
    status = "--" if view.match_status is None else view.match_status.value.upper()
    sets = (
        "--"
        if not view.completed_sets
        else " ".join(f"{home}-{away}" for home, away in view.completed_sets)
    )
    games = "--" if view.games is None else f"{view.games[0]}-{view.games[1]}"
    points = "--" if view.points is None else f"{view.points[0]}-{view.points[1]}"
    server = "--" if view.server is None else view.server.value.upper()
    lines = [
        f"INCI TENNIS SHADOW | {view.mode_label} | NO ORDERS",
        (
            f"Match {view.canonical_match_id} | Provider {view.provider_match_id} "
            f"| Event {view.event_ticker} | {status}"
        ),
        (
            f"Players HOME={view.home_player_id} | AWAY={view.away_player_id} "
            f"| Server {server}"
        ),
        f"Score sets [{sets}] | games {games} | points {points}",
        (
            f"Provider rev {_missing(view.provider_revision)} age "
            f"{_age(view.provider_age_ns)} | decision {view.decision_sequence} "
            f"| wall_ns {_missing(view.observed_wall_ns)}"
        ),
        "-" * width,
        (
            f"{'SIDE':<5} {'TICKER':<18} {'MKT':<9} {'BID':>7} {'ASK':>7} "
            f"{'BID_Q':>7} {'ASK_Q':>7} {'SPRD':>7} {'AGE':>9} "
            f"{'EPOCH/SEQ':>11} {'SYNC':<9}"
        ),
    ]
    for row in view.contracts:
        market = "--" if row.market_status is None else row.market_status.value.upper()
        identity = (
            "--"
            if row.connection_epoch is None
            else f"{row.connection_epoch}/{row.sequence}"
        )
        lines.append(
            f"{row.player_side.value.upper():<5} {row.ticker:<18} {market:<9} "
            f"{_cents(row.yes_bid):>7} {_cents(row.yes_ask):>7} "
            f"{_quantity(row.bid_quantity):>7} {_quantity(row.ask_quantity):>7} "
            f"{_cents(row.spread):>7} {_age(row.book_age_ns):>9} "
            f"{identity:>11} {row.sync_state.value.upper():<9}"
        )
        lines.append(
            f"      reason={row.reason.value} | evaluated_ns="
            f"{_missing(row.evaluated_monotonic_ns)}"
        )
    return "\n".join(_clip(line, width) for line in lines)


__all__ = (
    "ContractMonitorView",
    "MatchMonitorView",
    "MonitorInputError",
    "OneMatchShadowMonitor",
    "SyncDisplayState",
    "render_monitor",
)
