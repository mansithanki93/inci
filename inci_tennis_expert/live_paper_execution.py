"""Pure full-depth paper policy and delayed fill simulator."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from re import ASCII, compile as pattern_compile

from inci_tennis_expert.contracts import PlayerSide
from inci_tennis_expert.fee_schedule import (
    FillSide,
    FrozenFeeSchedule,
    LiquidityRole,
    fee_for_fill,
)
from inci_tennis_expert.live_paper_contracts import LivePaperMarketBinding
from inci_tennis_expert.live_two_model import LiveTwoModelForecast


__all__ = (
    "LivePaperExecutionError",
    "LivePaperL2Level",
    "LivePaperL2Market",
    "LivePaperL2Frame",
    "PaperDecisionReason",
    "PaperProfitClaim",
    "PaperActionKind",
    "PaperAction",
    "PaperFill",
    "PaperEvent",
    "PaperPosition",
    "PaperPortfolioState",
    "PaperDecision",
    "project_paper_l2",
    "evaluate_live_paper_entry",
    "reduce_paper_book",
)


_SHA256 = pattern_compile(r"[0-9a-f]{64}\Z", ASCII)
_UUID = pattern_compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\Z"
)
_ONE_SECOND_NS = 1_000_000_000
_FRESHNESS_NS = 5_000_000_000
_HOLD_NS = 300_000_000_000
_MAX_DEBIT = Decimal("50")
_EXIT_TRIGGER = Decimal("5")
_ZERO = Decimal("0")
_ONE = Decimal("1")


class LivePaperExecutionError(ValueError):
    """Fixed-code rejection for malformed paper-only inputs."""


def _fail(code: str) -> None:
    raise LivePaperExecutionError(code)


def _decimal(value: object, name: str, *, price: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= _ZERO:
        _fail(name)
    if price and value >= _ONE:
        _fail(name)
    return value


def _level_price(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < _ZERO or value > _ONE:
        _fail(name)
    return value


def _executable(level: "LivePaperL2Level") -> bool:
    return _ZERO < level.price < _ONE


def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        _fail(name)
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(name)
    return value


@dataclass(frozen=True, slots=True)
class LivePaperL2Level:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _level_price(self.price, "price")
        _decimal(self.quantity, "quantity")


def _ladder(value: object, *, ascending: bool, name: str) -> tuple[LivePaperL2Level, ...]:
    if type(value) is not tuple or any(type(level) is not LivePaperL2Level for level in value):
        _fail(name)
    prices = tuple(level.price for level in value)
    if len(set(prices)) != len(prices) or value != tuple(sorted(value, key=lambda level: level.price, reverse=not ascending)):
        _fail(name)
    return value


@dataclass(frozen=True, slots=True)
class LivePaperL2Market:
    ticker: str
    market_id: str
    yes_player_side: PlayerSide
    yes_bids: tuple[LivePaperL2Level, ...]
    yes_asks: tuple[LivePaperL2Level, ...]

    def __post_init__(self) -> None:
        if type(self.ticker) is not str or not self.ticker or type(self.market_id) is not str or _UUID.fullmatch(self.market_id) is None:
            _fail("market")
        if type(self.yes_player_side) is not PlayerSide:
            _fail("market")
        _ladder(self.yes_bids, ascending=False, name="yes_bids")
        _ladder(self.yes_asks, ascending=True, name="yes_asks")
        if self.yes_bids and self.yes_asks and self.yes_bids[0].price > self.yes_asks[0].price:
            _fail("crossed_book")


@dataclass(frozen=True, slots=True)
class LivePaperL2Frame:
    binding: LivePaperMarketBinding
    home: LivePaperL2Market
    away: LivePaperL2Market
    physical_connection_generation: int
    subscription_id: int
    global_sequence: int
    raw_l2_state_sha256: str
    raw_parent_receipt_sha256: str
    captured_wall_ns: int
    captured_monotonic_ns: int
    clock_uncertainty_ns: int
    complete: bool = True
    gap_free: bool = True

    def __post_init__(self) -> None:
        if type(self.binding) is not LivePaperMarketBinding or type(self.home) is not LivePaperL2Market or type(self.away) is not LivePaperL2Market:
            _fail("frame")
        if (
            self.home.ticker != self.binding.home_ticker
            or self.home.market_id != self.binding.home_market_id
            or self.home.yes_player_side is not self.binding.home_yes_player_side
            or self.away.ticker != self.binding.away_ticker
            or self.away.market_id != self.binding.away_market_id
            or self.away.yes_player_side is not self.binding.away_yes_player_side
            or self.home.ticker == self.away.ticker
            or self.home.market_id == self.away.market_id
        ):
            _fail("market_binding")
        for value, name in ((self.physical_connection_generation, "generation"), (self.subscription_id, "subscription_id"), (self.global_sequence, "global_sequence")):
            _integer(value, name, positive=True)
        for value, name in ((self.captured_wall_ns, "captured_wall_ns"), (self.captured_monotonic_ns, "captured_monotonic_ns"), (self.clock_uncertainty_ns, "clock_uncertainty_ns")):
            _integer(value, name)
        _sha(self.raw_l2_state_sha256, "raw_l2_state_sha256")
        _sha(self.raw_parent_receipt_sha256, "raw_parent_receipt_sha256")
        if type(self.complete) is not bool or type(self.gap_free) is not bool:
            _fail("frame")

    def market_for(self, side: PlayerSide) -> LivePaperL2Market:
        return self.home if side is PlayerSide.HOME else self.away


class PaperDecisionReason(str, Enum):
    ACCEPTED = "accepted"
    FORECAST_UNSUPPORTED = "forecast_unsupported"
    MODEL_SUPPORT_MISSING = "model_support_missing"
    BEFORE_COMPLETED_SET = "before_completed_set"
    MATCH_NOT_LIVE = "match_not_live"
    OPEN_POSITION = "open_position"
    BOOK_STALE = "book_stale"
    BOOK_INCOMPLETE = "book_incomplete"
    BOOK_SEQUENCE_GAP = "book_sequence_gap"
    BOOK_BINDING_MISMATCH = "book_binding_mismatch"
    BOOK_CROSSED = "book_crossed"
    ASK_DEPTH_MISSING = "ask_depth_missing"
    DEBIT_LIMIT = "debit_limit"
    EDGE_BELOW_MINIMUM = "edge_below_minimum"


class PaperActionKind(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PaperProfitClaim(str, Enum):
    SETTLEMENT_VALUE_PROXY = "SETTLEMENT_VALUE_PROXY"


@dataclass(frozen=True, slots=True)
class PaperAction:
    kind: PaperActionKind
    canonical_match_id: str
    player_side: PlayerSide
    ticker: str
    market_id: str
    quantity: Decimal
    remaining_quantity: Decimal
    decision_wall_ns: int
    decision_monotonic_ns: int
    due_wall_ns: int
    due_monotonic_ns: int
    decision_generation: int
    decision_subscription_id: int
    decision_global_sequence: int
    decision_receipt_sha256: str
    decision_capture_wall_ns: int
    decision_capture_monotonic_ns: int
    conservative_fair_probability: Decimal | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not PaperActionKind or type(self.player_side) is not PlayerSide or type(self.canonical_match_id) is not str or not self.canonical_match_id or type(self.ticker) is not str or not self.ticker or type(self.market_id) is not str or _UUID.fullmatch(self.market_id) is None:
            _fail("action")
        for value, name in ((self.quantity, "quantity"), (self.remaining_quantity, "remaining_quantity")):
            _decimal(value, name)
            if value != value.to_integral_value() or value > self.quantity:
                _fail(name)
        for value, name, positive in ((self.decision_wall_ns, "decision_wall_ns", False), (self.decision_monotonic_ns, "decision_monotonic_ns", False), (self.due_wall_ns, "due_wall_ns", False), (self.due_monotonic_ns, "due_monotonic_ns", False), (self.decision_generation, "decision_generation", True), (self.decision_subscription_id, "decision_subscription_id", True), (self.decision_global_sequence, "decision_global_sequence", True), (self.decision_capture_wall_ns, "decision_capture_wall_ns", False), (self.decision_capture_monotonic_ns, "decision_capture_monotonic_ns", False)):
            _integer(value, name, positive=positive)
        _sha(self.decision_receipt_sha256, "decision_receipt_sha256")
        if self.due_wall_ns != self.decision_wall_ns + _ONE_SECOND_NS or self.due_monotonic_ns != self.decision_monotonic_ns + _ONE_SECOND_NS:
            _fail("action_due")
        fair = self.conservative_fair_probability
        if self.kind is PaperActionKind.BUY:
            if type(fair) is not Decimal or not fair.is_finite() or not _ZERO < fair <= _ONE:
                _fail("action_fair")
        elif fair is not None:
            _fail("action_fair")


@dataclass(frozen=True, slots=True)
class PaperFill:
    action_kind: PaperActionKind
    player_side: PlayerSide
    ticker: str
    quantity: Decimal
    debit_or_credit: Decimal
    fees: Decimal
    global_sequence: int
    raw_parent_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class PaperEvent:
    fill: PaperFill


@dataclass(frozen=True, slots=True)
class PaperPosition:
    canonical_match_id: str
    player_side: PlayerSide
    ticker: str
    market_id: str
    quantity: Decimal
    debit: Decimal
    entry_fees: Decimal
    opened_wall_ns: int
    opened_monotonic_ns: int

    def __post_init__(self) -> None:
        if type(self.canonical_match_id) is not str or not self.canonical_match_id or type(self.player_side) is not PlayerSide or type(self.ticker) is not str or not self.ticker or type(self.market_id) is not str or _UUID.fullmatch(self.market_id) is None:
            _fail("position")
        _decimal(self.quantity, "quantity")
        for value, name in ((self.debit, "debit"), (self.entry_fees, "entry_fees")):
            if type(value) is not Decimal or not value.is_finite() or value < _ZERO:
                _fail(name)
        if self.quantity != self.quantity.to_integral_value():
            _fail("quantity")
        _integer(self.opened_wall_ns, "opened_wall_ns")
        _integer(self.opened_monotonic_ns, "opened_monotonic_ns")


@dataclass(frozen=True, slots=True)
class _ConsumedDepth:
    receipt_sha256: str
    ticker: str
    kind: PaperActionKind
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class PaperPortfolioState:
    binding: LivePaperMarketBinding
    completed_sets: int
    match_live: bool
    fee_schedule: FrozenFeeSchedule
    fee_series_ticker: str
    pending_action: PaperAction | None = None
    position: PaperPosition | None = None
    consumed_depth: tuple[_ConsumedDepth, ...] = ()
    last_flat_global_sequence: int | None = None

    def __post_init__(self) -> None:
        if type(self.binding) is not LivePaperMarketBinding or type(self.completed_sets) is not int or self.completed_sets < 0 or type(self.match_live) is not bool or type(self.fee_schedule) is not FrozenFeeSchedule or type(self.fee_series_ticker) is not str or not self.fee_series_ticker:
            _fail("portfolio")
        if self.pending_action is not None and type(self.pending_action) is not PaperAction:
            _fail("portfolio")
        if self.position is not None and type(self.position) is not PaperPosition:
            _fail("portfolio")
        for value in (self.pending_action, self.position):
            if value is None:
                continue
            expected_ticker = (
                self.binding.home_ticker
                if value.player_side is PlayerSide.HOME
                else self.binding.away_ticker
            )
            expected_market_id = (
                self.binding.home_market_id
                if value.player_side is PlayerSide.HOME
                else self.binding.away_market_id
            )
            if value.canonical_match_id != self.binding.canonical_match_id:
                _fail("portfolio_binding")
            if value.ticker != expected_ticker or value.market_id != expected_market_id:
                _fail("portfolio_binding")
        if type(self.consumed_depth) is not tuple or any(type(item) is not _ConsumedDepth for item in self.consumed_depth):
            _fail("portfolio")
        if self.last_flat_global_sequence is not None:
            _integer(self.last_flat_global_sequence, "last_flat_global_sequence", positive=True)


@dataclass(frozen=True, slots=True)
class PaperDecision:
    reason: PaperDecisionReason
    action: PaperAction | None
    state: PaperPortfolioState
    player_side: PlayerSide | None = None
    conservative_fair_probability: Decimal | None = None
    expected_settlement_value_profit: Decimal | None = None
    claim: PaperProfitClaim = PaperProfitClaim.SETTLEMENT_VALUE_PROXY


def _levels(raw: object, *, inverse: bool, ascending: bool) -> tuple[LivePaperL2Level, ...]:
    if type(raw) is not tuple:
        _fail("l2_ladder")
    result: list[LivePaperL2Level] = []
    for item in raw:
        if type(item) is not tuple or len(item) != 2:
            _fail("l2_ladder")
        price, quantity = item
        _level_price(price, "l2_price")
        _decimal(quantity, "l2_quantity")
        result.append(LivePaperL2Level(_ONE - price if inverse else price, quantity))
    result.sort(key=lambda level: level.price, reverse=not ascending)
    if len({level.price for level in result}) != len(result):
        _fail("l2_ladder")
    return tuple(result)


def _project_market(raw: object, *, ticker: str, market_id: str, player_side: PlayerSide) -> LivePaperL2Market:
    if getattr(raw, "ticker", None) != ticker or getattr(raw, "market_id", None) != market_id:
        _fail("market_binding")
    return LivePaperL2Market(
        ticker=ticker, market_id=market_id, yes_player_side=player_side,
        yes_bids=_levels(getattr(raw, "yes_levels", None), inverse=False, ascending=False),
        yes_asks=_levels(getattr(raw, "no_levels", None), inverse=True, ascending=True),
    )


def project_paper_l2(
    l2: "UnqualifiedTwoTickerL2State",
    *, binding: LivePaperMarketBinding, raw_parent_receipt_sha256: str,
    captured_wall_ns: int, captured_monotonic_ns: int, clock_uncertainty_ns: int,
    home_ticker: str, away_ticker: str,
) -> LivePaperL2Frame:
    """Bind an immutable reducer export without inferring player orientation."""
    if type(binding) is not LivePaperMarketBinding or home_ticker != binding.home_ticker or away_ticker != binding.away_ticker:
        _fail("market_binding")
    markets = getattr(l2, "markets", None)
    if type(markets) is not tuple or len(markets) != 2:
        _fail("l2_incomplete")
    by_ticker = {getattr(market, "ticker", None): market for market in markets}
    if len(by_ticker) != 2 or binding.home_ticker not in by_ticker or binding.away_ticker not in by_ticker:
        _fail("market_binding")
    return LivePaperL2Frame(
        binding=binding,
        home=_project_market(by_ticker[binding.home_ticker], ticker=binding.home_ticker, market_id=binding.home_market_id, player_side=binding.home_yes_player_side),
        away=_project_market(by_ticker[binding.away_ticker], ticker=binding.away_ticker, market_id=binding.away_market_id, player_side=binding.away_yes_player_side),
        physical_connection_generation=getattr(l2, "physical_connection_generation", None),
        subscription_id=getattr(l2, "subscription_id", None),
        global_sequence=getattr(l2, "global_sequence", None),
        raw_l2_state_sha256=getattr(l2, "state_sha256", None),
        raw_parent_receipt_sha256=raw_parent_receipt_sha256,
        captured_wall_ns=captured_wall_ns, captured_monotonic_ns=captured_monotonic_ns,
        clock_uncertainty_ns=clock_uncertainty_ns,
    )


def _frame_reason(book: LivePaperL2Frame, state: PaperPortfolioState, now_wall: int, now_mono: int) -> PaperDecisionReason | None:
    if book.binding != state.binding:
        return PaperDecisionReason.BOOK_BINDING_MISMATCH
    if not book.complete:
        return PaperDecisionReason.BOOK_INCOMPLETE
    if not book.gap_free:
        return PaperDecisionReason.BOOK_SEQUENCE_GAP
    if (
        now_wall < book.captured_wall_ns
        or now_mono < book.captured_monotonic_ns
        or max(
            now_wall - book.captured_wall_ns,
            now_mono - book.captured_monotonic_ns,
        ) + book.clock_uncertainty_ns
        > _FRESHNESS_NS
    ):
        return PaperDecisionReason.BOOK_STALE
    for market in (book.home, book.away):
        if market.yes_bids and market.yes_asks and market.yes_bids[0].price > market.yes_asks[0].price:
            return PaperDecisionReason.BOOK_CROSSED
    return None


def _fee(state: PaperPortfolioState, *, price: Decimal, quantity: Decimal, side: FillSide, wall_ns: int) -> Decimal:
    return fee_for_fill(state.fee_schedule, series_ticker=state.fee_series_ticker, price=price, quantity=quantity, role=LiquidityRole.TAKER, side=side, fill_wall_ns=wall_ns)


def _walk(levels: tuple[LivePaperL2Level, ...], quantity: Decimal, *, consumed: tuple[_ConsumedDepth, ...], receipt: str, ticker: str, kind: PaperActionKind) -> tuple[tuple[tuple[Decimal, Decimal], ...], Decimal]:
    remaining = quantity
    taken: list[tuple[Decimal, Decimal]] = []
    for level in levels:
        if not _executable(level):
            continue
        used = sum((item.quantity for item in consumed if item.receipt_sha256 == receipt and item.ticker == ticker and item.kind is kind and item.price == level.price), _ZERO)
        available = level.quantity - used
        integral = available.to_integral_value(rounding=ROUND_FLOOR)
        if integral <= _ZERO:
            continue
        part = min(remaining, integral)
        if part > _ZERO:
            taken.append((level.price, part))
            remaining -= part
        if remaining == _ZERO:
            break
    return tuple(taken), quantity - remaining


def _buy_cost(state: PaperPortfolioState, asks: tuple[LivePaperL2Level, ...], quantity: Decimal, wall_ns: int) -> tuple[Decimal, Decimal] | None:
    pieces, filled = _walk(asks, quantity, consumed=(), receipt="", ticker="", kind=PaperActionKind.BUY)
    if filled != quantity:
        return None
    debit = sum((price * amount for price, amount in pieces), _ZERO)
    fees = sum((_fee(state, price=price, quantity=amount, side=FillSide.BUY, wall_ns=wall_ns) for price, amount in pieces), _ZERO)
    return debit, fees


def _fair_values(forecast: LiveTwoModelForecast) -> tuple[tuple[PlayerSide, Decimal], ...] | None:
    if not forecast.supported or not forecast.model_1.supported or not forecast.model_2.supported:
        return None
    values = (forecast.model_1.lower_home_match_probability, forecast.model_2.lower_home_match_probability, forecast.model_1.upper_home_match_probability, forecast.model_2.upper_home_match_probability)
    if any(type(value) is not Decimal for value in values):
        return None
    return ((PlayerSide.HOME, min(values[0], values[1])), (PlayerSide.AWAY, min(_ONE - values[2], _ONE - values[3])))  # type: ignore[arg-type]


def _new_action(kind: PaperActionKind, side: PlayerSide, ticker: str, market_id: str, quantity: Decimal, book: LivePaperL2Frame, wall_ns: int, mono_ns: int, *, conservative_fair_probability: Decimal | None = None) -> PaperAction:
    return PaperAction(kind=kind, canonical_match_id=book.binding.canonical_match_id, player_side=side, ticker=ticker, market_id=market_id, quantity=quantity, remaining_quantity=quantity, decision_wall_ns=wall_ns, decision_monotonic_ns=mono_ns, due_wall_ns=wall_ns + _ONE_SECOND_NS, due_monotonic_ns=mono_ns + _ONE_SECOND_NS, decision_generation=book.physical_connection_generation, decision_subscription_id=book.subscription_id, decision_global_sequence=book.global_sequence, decision_receipt_sha256=book.raw_parent_receipt_sha256, decision_capture_wall_ns=book.captured_wall_ns, decision_capture_monotonic_ns=book.captured_monotonic_ns, conservative_fair_probability=conservative_fair_probability)


def _settlement_value_profit(state: PaperPortfolioState, *, fair: Decimal, quantity: Decimal, debit: Decimal, entry_fees: Decimal, wall_ns: int) -> Decimal:
    conservative_exit_fee = _fee(state, price=Decimal("0.5"), quantity=quantity, side=FillSide.SELL, wall_ns=wall_ns)
    return quantity * fair - debit - entry_fees - conservative_exit_fee


def evaluate_live_paper_entry(
    forecast: LiveTwoModelForecast, book: LivePaperL2Frame, state: PaperPortfolioState,
    *, decision_wall_ns: int, decision_monotonic_ns: int,
) -> PaperDecision:
    """Create at most one fee-aware, delayed paper BUY action per match."""
    if type(forecast) is not LiveTwoModelForecast or type(book) is not LivePaperL2Frame or type(state) is not PaperPortfolioState:
        _fail("entry")
    _integer(decision_wall_ns, "decision_wall_ns")
    _integer(decision_monotonic_ns, "decision_monotonic_ns")
    if state.completed_sets < 1:
        return PaperDecision(PaperDecisionReason.BEFORE_COMPLETED_SET, None, state)
    if not state.match_live:
        return PaperDecision(PaperDecisionReason.MATCH_NOT_LIVE, None, state)
    if state.pending_action is not None or state.position is not None:
        return PaperDecision(PaperDecisionReason.OPEN_POSITION, None, state)
    if state.last_flat_global_sequence is not None and book.global_sequence <= state.last_flat_global_sequence:
        return PaperDecision(PaperDecisionReason.OPEN_POSITION, None, state)
    frame_reason = _frame_reason(book, state, decision_wall_ns, decision_monotonic_ns)
    if frame_reason is not None:
        return PaperDecision(frame_reason, None, state)
    values = _fair_values(forecast)
    if values is None:
        return PaperDecision(PaperDecisionReason.MODEL_SUPPORT_MISSING if forecast.supported else PaperDecisionReason.FORECAST_UNSUPPORTED, None, state)
    candidates: list[tuple[Decimal, PlayerSide, Decimal, Decimal, Decimal]] = []
    had_depth = False
    had_budget = False
    for side, fair in values:
        market = book.market_for(side)
        asks = tuple(level for level in market.yes_asks if _executable(level))
        if not asks:
            continue
        had_depth = True
        lowest = asks[0].price
        maximum = int((_MAX_DEBIT / lowest).to_integral_value(rounding=ROUND_FLOOR))
        available = sum((level.quantity.to_integral_value(rounding=ROUND_FLOOR) for level in asks), _ZERO)
        maximum = min(maximum, int(available))
        largest: tuple[Decimal, Decimal, Decimal] | None = None
        for integer_quantity in range(maximum, 0, -1):
            quantity = Decimal(integer_quantity)
            cost = _buy_cost(state, asks, quantity, decision_wall_ns)
            if cost is None:
                continue
            debit, entry_fees = cost
            if debit + entry_fees > _MAX_DEBIT:
                continue
            had_budget = True
            largest = (quantity, debit, entry_fees)
            break
        if largest is not None:
            quantity, debit, entry_fees = largest
            edge = _settlement_value_profit(state, fair=fair, quantity=quantity, debit=debit, entry_fees=entry_fees, wall_ns=decision_wall_ns)
            if edge >= _EXIT_TRIGGER:
                candidates.append((edge, side, fair, quantity, debit + entry_fees))
    if not had_depth:
        return PaperDecision(PaperDecisionReason.ASK_DEPTH_MISSING, None, state)
    if not had_budget:
        return PaperDecision(PaperDecisionReason.DEBIT_LIMIT, None, state)
    if not candidates:
        return PaperDecision(PaperDecisionReason.EDGE_BELOW_MINIMUM, None, state)
    edge, side, fair, quantity, _ = max(candidates, key=lambda item: (item[0], item[3]))
    market = book.market_for(side)
    action = _new_action(PaperActionKind.BUY, side, market.ticker, market.market_id, quantity, book, decision_wall_ns, decision_monotonic_ns, conservative_fair_probability=fair)
    return PaperDecision(PaperDecisionReason.ACCEPTED, action, replace(state, pending_action=action), side, fair, edge)


def _eligible(action: PaperAction, book: LivePaperL2Frame, observed_wall_ns: int, observed_monotonic_ns: int) -> bool:
    return observed_wall_ns >= action.due_wall_ns and observed_monotonic_ns >= action.due_monotonic_ns and book.captured_wall_ns >= action.due_wall_ns and book.captured_monotonic_ns >= action.due_monotonic_ns and book.physical_connection_generation == action.decision_generation and book.subscription_id == action.decision_subscription_id and book.global_sequence > action.decision_global_sequence and book.raw_parent_receipt_sha256 != action.decision_receipt_sha256 and book.captured_wall_ns > action.decision_capture_wall_ns and book.captured_monotonic_ns > action.decision_capture_monotonic_ns


def _arrival_buy_plan(state: PaperPortfolioState, action: PaperAction, book: LivePaperL2Frame) -> tuple[tuple[tuple[Decimal, Decimal], ...], Decimal, Decimal, Decimal] | None:
    levels = book.market_for(action.player_side).yes_asks
    _, available = _walk(levels, action.remaining_quantity, consumed=state.consumed_depth, receipt=book.raw_parent_receipt_sha256, ticker=action.ticker, kind=action.kind)
    prior = state.position
    prior_quantity = _ZERO if prior is None else prior.quantity
    prior_debit = _ZERO if prior is None else prior.debit
    prior_entry_fees = _ZERO if prior is None else prior.entry_fees
    fair = action.conservative_fair_probability
    if fair is None:
        _fail("action_fair")
    for integer_quantity in range(int(available), 0, -1):
        requested = Decimal(integer_quantity)
        pieces, quantity = _walk(levels, requested, consumed=state.consumed_depth, receipt=book.raw_parent_receipt_sha256, ticker=action.ticker, kind=action.kind)
        if quantity != requested:
            continue
        debit = sum((price * amount for price, amount in pieces), _ZERO)
        entry_fees = sum((_fee(state, price=price, quantity=amount, side=FillSide.BUY, wall_ns=book.captured_wall_ns) for price, amount in pieces), _ZERO)
        cumulative_debit = prior_debit + debit
        cumulative_entry_fees = prior_entry_fees + entry_fees
        if cumulative_debit + cumulative_entry_fees > _MAX_DEBIT:
            continue
        cumulative_quantity = prior_quantity + quantity
        edge = _settlement_value_profit(state, fair=fair, quantity=cumulative_quantity, debit=cumulative_debit, entry_fees=cumulative_entry_fees, wall_ns=book.captured_wall_ns)
        if edge >= _EXIT_TRIGGER:
            return pieces, quantity, debit, entry_fees
    return None


def _fill_action(state: PaperPortfolioState, action: PaperAction, book: LivePaperL2Frame) -> tuple[PaperPortfolioState, PaperEvent | None]:
    market = book.market_for(action.player_side)
    if action.kind is PaperActionKind.BUY:
        plan = _arrival_buy_plan(state, action, book)
        if plan is None:
            return state, None
        pieces, quantity, gross, fees = plan
    else:
        pieces, quantity = _walk(market.yes_bids, action.remaining_quantity, consumed=state.consumed_depth, receipt=book.raw_parent_receipt_sha256, ticker=action.ticker, kind=action.kind)
        if quantity == _ZERO:
            return state, None
        gross = sum((price * amount for price, amount in pieces), _ZERO)
        fees = sum((_fee(state, price=price, quantity=amount, side=FillSide.SELL, wall_ns=book.captured_wall_ns) for price, amount in pieces), _ZERO)
    consumed = state.consumed_depth + tuple(_ConsumedDepth(book.raw_parent_receipt_sha256, action.ticker, action.kind, price, amount) for price, amount in pieces)
    residual = action.remaining_quantity - quantity
    updated_action = replace(action, remaining_quantity=residual) if residual > _ZERO else None
    fill = PaperFill(action.kind, action.player_side, action.ticker, quantity, gross, fees, book.global_sequence, book.raw_parent_receipt_sha256)
    if action.kind is PaperActionKind.BUY:
        prior = state.position
        position = PaperPosition(action.canonical_match_id, action.player_side, action.ticker, action.market_id, quantity if prior is None else prior.quantity + quantity, gross if prior is None else prior.debit + gross, fees if prior is None else prior.entry_fees + fees, book.captured_wall_ns if prior is None else prior.opened_wall_ns, book.captured_monotonic_ns if prior is None else prior.opened_monotonic_ns)
        return replace(state, pending_action=updated_action, position=position, consumed_depth=consumed), PaperEvent(fill)
    position = state.position
    if position is None:
        _fail("sell_without_position")
    remaining_position = position.quantity - quantity
    if remaining_position == _ZERO:
        return replace(state, pending_action=updated_action, position=None, consumed_depth=consumed, last_flat_global_sequence=book.global_sequence), PaperEvent(fill)
    factor = remaining_position / position.quantity
    residual_position = PaperPosition(position.canonical_match_id, position.player_side, position.ticker, position.market_id, remaining_position, position.debit * factor, position.entry_fees * factor, position.opened_wall_ns, position.opened_monotonic_ns)
    return replace(state, pending_action=updated_action, position=residual_position, consumed_depth=consumed), PaperEvent(fill)


def _exit_action(state: PaperPortfolioState, book: LivePaperL2Frame, observed_wall_ns: int, observed_monotonic_ns: int) -> PaperAction | None:
    position = state.position
    if position is None or state.pending_action is not None:
        return None
    market = book.market_for(position.player_side)
    pieces, quantity = _walk(market.yes_bids, position.quantity, consumed=state.consumed_depth, receipt=book.raw_parent_receipt_sha256, ticker=position.ticker, kind=PaperActionKind.SELL)
    due_to_time = observed_monotonic_ns - position.opened_monotonic_ns >= _HOLD_NS
    if quantity == position.quantity:
        credit = sum((price * amount for price, amount in pieces), _ZERO)
        fees = sum((_fee(state, price=price, quantity=amount, side=FillSide.SELL, wall_ns=observed_wall_ns) for price, amount in pieces), _ZERO)
        pnl = credit - fees - position.debit - position.entry_fees
        if pnl >= _EXIT_TRIGGER or pnl <= -_EXIT_TRIGGER or due_to_time:
            return _new_action(PaperActionKind.SELL, position.player_side, position.ticker, position.market_id, position.quantity, book, observed_wall_ns, observed_monotonic_ns)
    elif due_to_time:
        return _new_action(PaperActionKind.SELL, position.player_side, position.ticker, position.market_id, position.quantity, book, observed_wall_ns, observed_monotonic_ns)
    return None


def reduce_paper_book(
    state: PaperPortfolioState, book: LivePaperL2Frame,
    *, observed_wall_ns: int, observed_monotonic_ns: int,
) -> tuple[PaperPortfolioState, tuple[PaperEvent, ...]]:
    """Apply one later L2 observation without any external capability."""
    if type(state) is not PaperPortfolioState or type(book) is not LivePaperL2Frame:
        _fail("reduce")
    _integer(observed_wall_ns, "observed_wall_ns")
    _integer(observed_monotonic_ns, "observed_monotonic_ns")
    if _frame_reason(book, state, observed_wall_ns, observed_monotonic_ns) is not None:
        return state, ()
    if state.pending_action is not None:
        if not _eligible(state.pending_action, book, observed_wall_ns, observed_monotonic_ns):
            return state, ()
        next_state, event = _fill_action(state, state.pending_action, book)
        return next_state, () if event is None else (event,)
    action = _exit_action(state, book, observed_wall_ns, observed_monotonic_ns)
    if action is None:
        return state, ()
    return replace(state, pending_action=action), ()
