from __future__ import annotations

from dataclasses import dataclass
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
from typing import Final

from .contracts import (
    BookLevel,
    BookState,
    ContractSide,
    DecisionAction,
    DecisionReason,
    ExpertContractError,
    FairValueEstimate,
    MarketStatus,
    PlayerSide,
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
from .fee_schedule import (
    FrozenFeeSchedule,
    fill_fee_usd,
    round_trip_fee_usd,
)
from .market_book import executable_buy


_POLICY_DECIMAL_CONTEXT: Final[Context] = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_PNL_PLACES: Final[Decimal] = Decimal("0.0001")


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise ExpertContractError(name)
    return value


def _nonnegative_decimal(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number < Decimal("0"):
        raise ExpertContractError(name)
    return number


def _quantize_pnl(value: Decimal) -> Decimal:
    with localcontext(_POLICY_DECIMAL_CONTEXT):
        return value.quantize(_PNL_PLACES)


def contract_side_for_player(
    yes_player_side: PlayerSide,
    player_side: PlayerSide,
) -> ContractSide:
    _exact(yes_player_side, PlayerSide, "yes_player_side")
    _exact(player_side, PlayerSide, "player_side")
    if yes_player_side is player_side:
        return ContractSide.YES
    return ContractSide.NO


def _best_bid(book: BookState, contract_side: ContractSide) -> Decimal | None:
    ladder = (
        book.yes_bids if contract_side is ContractSide.YES else book.no_bids
    )
    if not ladder:
        return None
    return ladder[0].price


def _mid_price(book: BookState, contract_side: ContractSide) -> Decimal | None:
    bid = _best_bid(book, contract_side)
    complementary = (
        book.no_bids if contract_side is ContractSide.YES else book.yes_bids
    )
    if bid is None or not complementary:
        return None
    ask = Decimal("1") - complementary[0].price
    with localcontext(_POLICY_DECIMAL_CONTEXT):
        return (bid + ask) / Decimal("2")


def _executable_sell(
    book: BookState,
    contract_side: ContractSide,
    contracts: Decimal,
    limit_price: Decimal,
) -> tuple[Decimal, Decimal, tuple[BookLevel, ...]]:
    """Sell into same-side bids at or above limit_price."""
    if type(book) is not BookState:
        raise TypeError("book")
    if type(contract_side) is not ContractSide:
        raise TypeError("contract_side")
    if type(contracts) is not Decimal:
        raise TypeError("contracts")
    if type(limit_price) is not Decimal:
        raise TypeError("limit_price")
    try:
        with localcontext(_POLICY_DECIMAL_CONTEXT):
            if not contracts.is_finite() or contracts < Decimal("0"):
                raise ExpertContractError("contracts")
            if (
                not limit_price.is_finite()
                or limit_price < Decimal("0")
                or limit_price > Decimal("1")
            ):
                raise ExpertContractError("limit_price")
    except DecimalException as exc:
        raise ExpertContractError("scalp_decimal") from exc
    if not book.trusted or book.sequence_gap:
        raise ExpertContractError("book_untrusted")
    if book.market_status is not MarketStatus.OPEN:
        raise ExpertContractError("market_not_open")
    try:
        with localcontext(_POLICY_DECIMAL_CONTEXT):
            if contracts == Decimal("0"):
                return Decimal("0"), Decimal("0"), ()
            ladder = (
                book.yes_bids
                if contract_side is ContractSide.YES
                else book.no_bids
            )
            if not ladder:
                return Decimal("0"), Decimal("0"), ()
            remaining = contracts
            consumed: list[BookLevel] = []
            for bid_level in ladder:
                if bid_level.price < limit_price:
                    break
                quantity = min(remaining, bid_level.quantity)
                consumed.append(BookLevel(bid_level.price, quantity))
                remaining -= quantity
                if remaining == Decimal("0"):
                    break
            if not consumed:
                return Decimal("0"), Decimal("0"), ()
            filled = sum((item.quantity for item in consumed), Decimal("0"))
            total = sum(
                (item.price * item.quantity for item in consumed),
                Decimal("0"),
            )
            return filled, total / filled, tuple(consumed)
    except DecimalException as exc:
        raise ExpertContractError("scalp_decimal") from exc


@dataclass(frozen=True, slots=True)
class FrozenScalpPolicy:
    policy_id: str
    target_net_pnl_usd: Decimal
    quantity: Decimal
    entry_limit_slippage: Decimal
    convergence_tolerance: Decimal
    thesis_invalidation_gap: Decimal
    max_holding_wall_ns: int
    require_calibration: bool
    fee_schedule_sha256: str
    sealed: bool
    policy_artifact_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, FrozenScalpPolicy)
        _safe_id(self.policy_id, "policy_id")
        _nonnegative_decimal(self.target_net_pnl_usd, "target_net_pnl_usd")
        _quantity(self.quantity, "quantity", positive=True)
        _probability(self.entry_limit_slippage, "entry_limit_slippage")
        _probability(self.convergence_tolerance, "convergence_tolerance")
        _probability(
            self.thesis_invalidation_gap,
            "thesis_invalidation_gap",
        )
        _integer(self.max_holding_wall_ns, "max_holding_wall_ns", positive=True)
        _boolean(self.require_calibration, "require_calibration")
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        _boolean(self.sealed, "sealed")
        _sha256(self.policy_artifact_sha256, "policy_artifact_sha256")
        expected = scalp_policy_sha256(self)
        if self.policy_artifact_sha256 != expected:
            raise ExpertContractError("policy_artifact_sha256")
        if not self.sealed:
            raise ExpertContractError("policy_unsealed")


def scalp_policy_identity_payload(
    *,
    policy_id: str,
    target_net_pnl_usd: Decimal,
    quantity: Decimal,
    entry_limit_slippage: Decimal,
    convergence_tolerance: Decimal,
    thesis_invalidation_gap: Decimal,
    max_holding_wall_ns: int,
    require_calibration: bool,
    fee_schedule_sha256: str,
    sealed: bool,
) -> dict[str, object]:
    return {
        "schema": "frozen_scalp_policy_v1",
        "policy_id": policy_id,
        "target_net_pnl_usd": target_net_pnl_usd,
        "quantity": quantity,
        "entry_limit_slippage": entry_limit_slippage,
        "convergence_tolerance": convergence_tolerance,
        "thesis_invalidation_gap": thesis_invalidation_gap,
        "max_holding_wall_ns": max_holding_wall_ns,
        "require_calibration": require_calibration,
        "fee_schedule_sha256": fee_schedule_sha256,
        "sealed": sealed,
    }


def scalp_policy_sha256(policy: FrozenScalpPolicy) -> str:
    _exact_self(policy, FrozenScalpPolicy)
    return expert_contract_sha256(
        scalp_policy_identity_payload(
            policy_id=policy.policy_id,
            target_net_pnl_usd=policy.target_net_pnl_usd,
            quantity=policy.quantity,
            entry_limit_slippage=policy.entry_limit_slippage,
            convergence_tolerance=policy.convergence_tolerance,
            thesis_invalidation_gap=policy.thesis_invalidation_gap,
            max_holding_wall_ns=policy.max_holding_wall_ns,
            require_calibration=policy.require_calibration,
            fee_schedule_sha256=policy.fee_schedule_sha256,
            sealed=policy.sealed,
        )
    )


def sealed_short_horizon_scalp_policy(
    fee_schedule: FrozenFeeSchedule,
    *,
    policy_id: str = "kalshi-tennis-scalp-3usd-v1",
    target_net_pnl_usd: Decimal = Decimal("3.00"),
    quantity: Decimal = Decimal("50"),
    entry_limit_slippage: Decimal = Decimal("0.01"),
    convergence_tolerance: Decimal = Decimal("0.02"),
    thesis_invalidation_gap: Decimal = Decimal("0.03"),
    max_holding_wall_ns: int = 300_000_000_000,
    require_calibration: bool = True,
) -> FrozenScalpPolicy:
    _exact_self(fee_schedule, FrozenFeeSchedule)
    if not fee_schedule.sealed:
        raise ExpertContractError("fee_unsealed")
    payload = scalp_policy_identity_payload(
        policy_id=policy_id,
        target_net_pnl_usd=target_net_pnl_usd,
        quantity=quantity,
        entry_limit_slippage=entry_limit_slippage,
        convergence_tolerance=convergence_tolerance,
        thesis_invalidation_gap=thesis_invalidation_gap,
        max_holding_wall_ns=max_holding_wall_ns,
        require_calibration=require_calibration,
        fee_schedule_sha256=fee_schedule.schedule_sha256,
        sealed=True,
    )
    digest = expert_contract_sha256(payload)
    return FrozenScalpPolicy(
        policy_id=policy_id,
        target_net_pnl_usd=target_net_pnl_usd,
        quantity=quantity,
        entry_limit_slippage=entry_limit_slippage,
        convergence_tolerance=convergence_tolerance,
        thesis_invalidation_gap=thesis_invalidation_gap,
        max_holding_wall_ns=max_holding_wall_ns,
        require_calibration=require_calibration,
        fee_schedule_sha256=fee_schedule.schedule_sha256,
        sealed=True,
        policy_artifact_sha256=digest,
    )


@dataclass(frozen=True, slots=True)
class ScalpEntryEstimate:
    supported: bool
    abstention_reason: DecisionReason | None
    contract_side: ContractSide | None
    player_side: PlayerSide | None
    quantity: Decimal
    limit_price: Decimal | None
    executable_quantity: Decimal
    expected_entry_price: Decimal | None
    expected_exit_price: Decimal | None
    edge_vs_ask: Decimal
    projected_net_pnl: Decimal
    lower_projected_net_pnl: Decimal
    fair_value_sha256: str
    fee_schedule_sha256: str
    policy_artifact_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ScalpEntryEstimate)
        _boolean(self.supported, "supported")
        _optional_exact(
            self.abstention_reason,
            DecisionReason,
            "abstention_reason",
        )
        _optional_exact(self.contract_side, ContractSide, "contract_side")
        _optional_exact(self.player_side, PlayerSide, "player_side")
        _quantity(self.quantity, "quantity")
        if self.limit_price is not None:
            _probability(self.limit_price, "limit_price")
        _quantity(self.executable_quantity, "executable_quantity")
        if self.expected_entry_price is not None:
            _probability(self.expected_entry_price, "expected_entry_price")
        if self.expected_exit_price is not None:
            _probability(self.expected_exit_price, "expected_exit_price")
        _decimal(self.edge_vs_ask, "edge_vs_ask")
        _decimal(self.projected_net_pnl, "projected_net_pnl")
        _decimal(self.lower_projected_net_pnl, "lower_projected_net_pnl")
        _sha256(self.fair_value_sha256, "fair_value_sha256")
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        _sha256(self.policy_artifact_sha256, "policy_artifact_sha256")
        if self.supported:
            if (
                self.abstention_reason is not None
                or self.contract_side is None
                or self.player_side is None
                or self.limit_price is None
                or self.expected_entry_price is None
                or self.expected_exit_price is None
                or self.quantity <= Decimal("0")
                or self.executable_quantity <= Decimal("0")
                or self.executable_quantity > self.quantity
            ):
                raise ExpertContractError("supported")
            if self.lower_projected_net_pnl > self.projected_net_pnl:
                raise ExpertContractError("pnl_interval")
        elif (
            self.abstention_reason is None
            or self.contract_side is not None
            or self.player_side is not None
            or self.limit_price is not None
            or self.expected_entry_price is not None
            or self.expected_exit_price is not None
            or self.quantity != Decimal("0")
            or self.executable_quantity != Decimal("0")
            or self.edge_vs_ask != Decimal("0")
            or self.projected_net_pnl != Decimal("0")
            or self.lower_projected_net_pnl != Decimal("0")
        ):
            raise ExpertContractError("unsupported")


@dataclass(frozen=True, slots=True)
class ScalpDecision:
    action: DecisionAction
    reason: DecisionReason
    estimate: ScalpEntryEstimate
    decision_input_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ScalpDecision)
        _exact(self.action, DecisionAction, "action")
        _exact(self.reason, DecisionReason, "reason")
        _exact(self.estimate, ScalpEntryEstimate, "estimate")
        _sha256(self.decision_input_sha256, "decision_input_sha256")
        if self.action is DecisionAction.ABSTAIN:
            if self.reason in (
                DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE,
                DecisionReason.CONSERVATIVE_VALUE_POSITIVE,
                DecisionReason.BASELINE_SIGNAL_POSITIVE,
            ):
                raise ExpertContractError("abstain")
            return
        if self.action is not DecisionAction.PAPER_BUY:
            raise ExpertContractError("action")
        if self.reason is not DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE:
            raise ExpertContractError("reason")
        if not self.estimate.supported:
            raise ExpertContractError("estimate")


@dataclass(frozen=True, slots=True)
class ScalpPosition:
    ticker: str
    contract_side: ContractSide
    player_side: PlayerSide
    quantity: Decimal
    entry_price: Decimal
    entry_wall_ns: int
    entry_fair_probability: Decimal
    entry_lower_probability: Decimal
    fair_value_sha256: str
    fee_schedule_sha256: str
    policy_artifact_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ScalpPosition)
        _ticker(self.ticker, "ticker")
        _exact(self.contract_side, ContractSide, "contract_side")
        _exact(self.player_side, PlayerSide, "player_side")
        _quantity(self.quantity, "quantity", positive=True)
        _probability(self.entry_price, "entry_price")
        _integer(self.entry_wall_ns, "entry_wall_ns")
        _probability(self.entry_fair_probability, "entry_fair_probability")
        _probability(self.entry_lower_probability, "entry_lower_probability")
        if self.entry_lower_probability > self.entry_fair_probability:
            raise ExpertContractError("entry_probability_interval")
        _sha256(self.fair_value_sha256, "fair_value_sha256")
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")
        _sha256(self.policy_artifact_sha256, "policy_artifact_sha256")


@dataclass(frozen=True, slots=True)
class ScalpExitDecision:
    action: DecisionAction
    reason: DecisionReason
    limit_price: Decimal | None
    executable_quantity: Decimal
    expected_exit_price: Decimal | None
    projected_net_pnl: Decimal
    decision_input_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, ScalpExitDecision)
        _exact(self.action, DecisionAction, "action")
        _exact(self.reason, DecisionReason, "reason")
        if self.limit_price is not None:
            _probability(self.limit_price, "limit_price")
        _quantity(self.executable_quantity, "executable_quantity")
        if self.expected_exit_price is not None:
            _probability(self.expected_exit_price, "expected_exit_price")
        _decimal(self.projected_net_pnl, "projected_net_pnl")
        _sha256(self.decision_input_sha256, "decision_input_sha256")
        if self.action is DecisionAction.ABSTAIN:
            if (
                self.limit_price is not None
                or self.expected_exit_price is not None
                or self.executable_quantity != Decimal("0")
            ):
                raise ExpertContractError("abstain")
            return
        if self.action is not DecisionAction.PAPER_SELL:
            raise ExpertContractError("action")
        if self.reason not in (
            DecisionReason.FAIR_VALUE_CONVERGED,
            DecisionReason.THESIS_INVALIDATED,
            DecisionReason.HOLDING_HORIZON_REACHED,
            DecisionReason.MARKET_SUSPENDED_EXIT,
        ):
            raise ExpertContractError("reason")
        if (
            self.limit_price is None
            or self.expected_exit_price is None
            or self.executable_quantity <= Decimal("0")
        ):
            raise ExpertContractError("order_authority")


def _empty_entry_estimate(
    *,
    reason: DecisionReason,
    fair_value_sha256: str,
    fee_schedule_sha256: str,
    policy_artifact_sha256: str,
) -> ScalpEntryEstimate:
    return ScalpEntryEstimate(
        supported=False,
        abstention_reason=reason,
        contract_side=None,
        player_side=None,
        quantity=Decimal("0"),
        limit_price=None,
        executable_quantity=Decimal("0"),
        expected_entry_price=None,
        expected_exit_price=None,
        edge_vs_ask=Decimal("0"),
        projected_net_pnl=Decimal("0"),
        lower_projected_net_pnl=Decimal("0"),
        fair_value_sha256=fair_value_sha256,
        fee_schedule_sha256=fee_schedule_sha256,
        policy_artifact_sha256=policy_artifact_sha256,
    )


def _projected_net(
    fee_schedule: FrozenFeeSchedule,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    fees = round_trip_fee_usd(
        fee_schedule,
        entry_price,
        exit_price,
        quantity,
        taker=True,
    )
    with localcontext(_POLICY_DECIMAL_CONTEXT):
        gross = (exit_price - entry_price) * quantity
        return _quantize_pnl(gross - fees)


def estimate_scalp_entry(
    fair_value: FairValueEstimate,
    book: BookState,
    yes_player_side: PlayerSide,
    fee_schedule: FrozenFeeSchedule,
    policy: FrozenScalpPolicy,
) -> ScalpEntryEstimate:
    _exact(fair_value, FairValueEstimate, "fair_value")
    _exact(book, BookState, "book")
    _exact(yes_player_side, PlayerSide, "yes_player_side")
    _exact(fee_schedule, FrozenFeeSchedule, "fee_schedule")
    _exact(policy, FrozenScalpPolicy, "policy")
    if not fee_schedule.sealed or not policy.sealed:
        raise ExpertContractError("policy_unsealed")
    if policy.fee_schedule_sha256 != fee_schedule.schedule_sha256:
        raise ExpertContractError("fee_schedule_sha256")

    fair_digest = expert_contract_sha256(fair_value)
    if not fair_value.supported:
        reason = fair_value.abstention_reason
        if reason is None:
            reason = DecisionReason.MODEL_UNSUPPORTED
        return _empty_entry_estimate(
            reason=reason,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )
    if (
        policy.require_calibration
        and fair_value.calibration_artifact_sha256 is None
    ):
        return _empty_entry_estimate(
            reason=DecisionReason.MODEL_UNSUPPORTED,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )
    if not book.trusted or book.sequence_gap:
        return _empty_entry_estimate(
            reason=DecisionReason.SYNC_UNTRUSTED,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )
    if book.market_status is not MarketStatus.OPEN:
        return _empty_entry_estimate(
            reason=DecisionReason.SYNC_UNTRUSTED,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )

    contract_side = contract_side_for_player(
        yes_player_side,
        fair_value.player_side,
    )
    complementary = (
        book.no_bids if contract_side is ContractSide.YES else book.yes_bids
    )
    if not complementary:
        return _empty_entry_estimate(
            reason=DecisionReason.INSUFFICIENT_DEPTH,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )

    try:
        with localcontext(_POLICY_DECIMAL_CONTEXT):
            first_ask = Decimal("1") - complementary[0].price
            limit_price = min(
                Decimal("1"),
                first_ask + policy.entry_limit_slippage,
            )
    except DecimalException as exc:
        raise ExpertContractError("scalp_decimal") from exc

    filled, average_price, _levels = executable_buy(
        book,
        contract_side,
        policy.quantity,
        limit_price,
    )
    if filled <= Decimal("0"):
        return _empty_entry_estimate(
            reason=DecisionReason.INSUFFICIENT_DEPTH,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )
    if filled < policy.quantity:
        return _empty_entry_estimate(
            reason=DecisionReason.INSUFFICIENT_DEPTH,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )

    try:
        with localcontext(_POLICY_DECIMAL_CONTEXT):
            edge = fair_value.lower_probability - average_price
            center_exit = fair_value.fair_probability
            lower_exit = fair_value.lower_probability
            if center_exit <= average_price:
                return _empty_entry_estimate(
                    reason=DecisionReason.EDGE_BELOW_COST,
                    fair_value_sha256=fair_digest,
                    fee_schedule_sha256=fee_schedule.schedule_sha256,
                    policy_artifact_sha256=policy.policy_artifact_sha256,
                )
            projected = _projected_net(
                fee_schedule,
                average_price,
                center_exit,
                filled,
            )
            lower_projected = _projected_net(
                fee_schedule,
                average_price,
                max(lower_exit, average_price),
                filled,
            )
            if lower_exit <= average_price:
                lower_projected = _quantize_pnl(
                    -fill_fee_usd(
                        fee_schedule,
                        average_price,
                        filled,
                        side="BUY",
                        taker=True,
                    )
                )
    except DecimalException as exc:
        raise ExpertContractError("scalp_decimal") from exc

    if (
        edge <= Decimal("0")
        or lower_projected < policy.target_net_pnl_usd
    ):
        return _empty_entry_estimate(
            reason=DecisionReason.EDGE_BELOW_COST,
            fair_value_sha256=fair_digest,
            fee_schedule_sha256=fee_schedule.schedule_sha256,
            policy_artifact_sha256=policy.policy_artifact_sha256,
        )

    return ScalpEntryEstimate(
        supported=True,
        abstention_reason=None,
        contract_side=contract_side,
        player_side=fair_value.player_side,
        quantity=policy.quantity,
        limit_price=limit_price,
        executable_quantity=filled,
        expected_entry_price=average_price,
        expected_exit_price=center_exit,
        edge_vs_ask=_quantize_pnl(edge),
        projected_net_pnl=projected,
        lower_projected_net_pnl=lower_projected,
        fair_value_sha256=fair_digest,
        fee_schedule_sha256=fee_schedule.schedule_sha256,
        policy_artifact_sha256=policy.policy_artifact_sha256,
    )


def _estimate_decision_payload(estimate: ScalpEntryEstimate) -> dict[str, object]:
    return {
        "supported": estimate.supported,
        "abstention_reason": (
            None
            if estimate.abstention_reason is None
            else estimate.abstention_reason.value
        ),
        "contract_side": (
            None
            if estimate.contract_side is None
            else estimate.contract_side.value
        ),
        "player_side": (
            None
            if estimate.player_side is None
            else estimate.player_side.value
        ),
        "quantity": estimate.quantity,
        "limit_price": estimate.limit_price,
        "executable_quantity": estimate.executable_quantity,
        "expected_entry_price": estimate.expected_entry_price,
        "expected_exit_price": estimate.expected_exit_price,
        "edge_vs_ask": estimate.edge_vs_ask,
        "projected_net_pnl": estimate.projected_net_pnl,
        "lower_projected_net_pnl": estimate.lower_projected_net_pnl,
        "fair_value_sha256": estimate.fair_value_sha256,
        "fee_schedule_sha256": estimate.fee_schedule_sha256,
        "policy_artifact_sha256": estimate.policy_artifact_sha256,
    }


def _position_decision_payload(position: ScalpPosition) -> dict[str, object]:
    return {
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


def decide_scalp_entry(
    fair_value: FairValueEstimate,
    book: BookState,
    yes_player_side: PlayerSide,
    fee_schedule: FrozenFeeSchedule,
    policy: FrozenScalpPolicy,
) -> ScalpDecision:
    estimate = estimate_scalp_entry(
        fair_value,
        book,
        yes_player_side,
        fee_schedule,
        policy,
    )
    decision_input = expert_contract_sha256(
        {
            "schema": "scalp_entry_decision_v1",
            "estimate": _estimate_decision_payload(estimate),
            "policy_artifact_sha256": policy.policy_artifact_sha256,
            "fee_schedule_sha256": fee_schedule.schedule_sha256,
        }
    )
    if not estimate.supported:
        assert estimate.abstention_reason is not None
        return ScalpDecision(
            action=DecisionAction.ABSTAIN,
            reason=estimate.abstention_reason,
            estimate=estimate,
            decision_input_sha256=decision_input,
        )
    return ScalpDecision(
        action=DecisionAction.PAPER_BUY,
        reason=DecisionReason.SIMPLE_SCORE_VALUE_POSITIVE,
        estimate=estimate,
        decision_input_sha256=decision_input,
    )


def open_scalp_position(
    decision: ScalpDecision,
    fair_value: FairValueEstimate,
    *,
    ticker: str,
    entry_wall_ns: int,
) -> ScalpPosition:
    _exact(decision, ScalpDecision, "decision")
    _exact(fair_value, FairValueEstimate, "fair_value")
    if decision.action is not DecisionAction.PAPER_BUY:
        raise ExpertContractError("action")
    estimate = decision.estimate
    if (
        not estimate.supported
        or estimate.contract_side is None
        or estimate.player_side is None
        or estimate.expected_entry_price is None
        or estimate.expected_exit_price is None
        or not fair_value.supported
        or fair_value.player_side is not estimate.player_side
    ):
        raise ExpertContractError("estimate")
    if expert_contract_sha256(fair_value) != estimate.fair_value_sha256:
        raise ExpertContractError("fair_value_sha256")
    return ScalpPosition(
        ticker=ticker,
        contract_side=estimate.contract_side,
        player_side=estimate.player_side,
        quantity=estimate.executable_quantity,
        entry_price=estimate.expected_entry_price,
        entry_wall_ns=entry_wall_ns,
        entry_fair_probability=fair_value.fair_probability,
        entry_lower_probability=fair_value.lower_probability,
        fair_value_sha256=estimate.fair_value_sha256,
        fee_schedule_sha256=estimate.fee_schedule_sha256,
        policy_artifact_sha256=estimate.policy_artifact_sha256,
    )


def decide_scalp_exit(
    position: ScalpPosition,
    fair_value: FairValueEstimate,
    book: BookState,
    fee_schedule: FrozenFeeSchedule,
    policy: FrozenScalpPolicy,
    decision_wall_ns: int,
) -> ScalpExitDecision:
    _exact(position, ScalpPosition, "position")
    _exact(fair_value, FairValueEstimate, "fair_value")
    _exact(book, BookState, "book")
    _exact(fee_schedule, FrozenFeeSchedule, "fee_schedule")
    _exact(policy, FrozenScalpPolicy, "policy")
    _integer(decision_wall_ns, "decision_wall_ns")
    if policy.policy_artifact_sha256 != position.policy_artifact_sha256:
        raise ExpertContractError("policy_artifact_sha256")
    if fee_schedule.schedule_sha256 != position.fee_schedule_sha256:
        raise ExpertContractError("fee_schedule_sha256")
    if book.ticker != position.ticker:
        raise ExpertContractError("ticker")

    def _hold(reason: DecisionReason) -> ScalpExitDecision:
        return ScalpExitDecision(
            action=DecisionAction.ABSTAIN,
            reason=reason,
            limit_price=None,
            executable_quantity=Decimal("0"),
            expected_exit_price=None,
            projected_net_pnl=Decimal("0"),
            decision_input_sha256=expert_contract_sha256(
                {
                    "schema": "scalp_exit_decision_v1",
                    "position": _position_decision_payload(position),
                    "reason": reason.value,
                    "decision_wall_ns": decision_wall_ns,
                }
            ),
        )

    def _sell(
        reason: DecisionReason,
        *,
        limit_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        projected: Decimal,
    ) -> ScalpExitDecision:
        return ScalpExitDecision(
            action=DecisionAction.PAPER_SELL,
            reason=reason,
            limit_price=limit_price,
            executable_quantity=quantity,
            expected_exit_price=exit_price,
            projected_net_pnl=projected,
            decision_input_sha256=expert_contract_sha256(
                {
                    "schema": "scalp_exit_decision_v1",
                    "position": _position_decision_payload(position),
                    "reason": reason.value,
                    "limit_price": limit_price,
                    "exit_price": exit_price,
                    "decision_wall_ns": decision_wall_ns,
                }
            ),
        )

    if book.market_status is not MarketStatus.OPEN:
        # Exit authority is reserved for an open executable book. A suspended
        # or ended market abstains here; risk/runtime may force flatten later.
        return _hold(DecisionReason.MARKET_SUSPENDED_EXIT)

    if decision_wall_ns < position.entry_wall_ns:
        raise ExpertContractError("decision_wall_ns")
    held = decision_wall_ns - position.entry_wall_ns
    if held >= policy.max_holding_wall_ns:
        bid = _best_bid(book, position.contract_side)
        if bid is None:
            return _hold(DecisionReason.HOLDING_HORIZON_REACHED)
        limit_price = max(Decimal("0.01"), bid - policy.convergence_tolerance)
        filled, avg, _ = _executable_sell(
            book,
            position.contract_side,
            position.quantity,
            limit_price,
        )
        if filled < position.quantity:
            return _hold(DecisionReason.HOLDING_HORIZON_REACHED)
        projected = _projected_net(
            fee_schedule,
            position.entry_price,
            avg,
            filled,
        )
        return _sell(
            DecisionReason.HOLDING_HORIZON_REACHED,
            limit_price=limit_price,
            exit_price=avg,
            quantity=filled,
            projected=projected,
        )

    if not fair_value.supported:
        return _hold(
            fair_value.abstention_reason
            or DecisionReason.MODEL_UNSUPPORTED
        )
    if fair_value.player_side is not position.player_side:
        raise ExpertContractError("player_side")

    try:
        with localcontext(_POLICY_DECIMAL_CONTEXT):
            invalidation_line = (
                position.entry_price - policy.thesis_invalidation_gap
            )
            thesis_dead = fair_value.lower_probability < invalidation_line
    except DecimalException as exc:
        raise ExpertContractError("scalp_decimal") from exc

    bid = _best_bid(book, position.contract_side)
    mid = _mid_price(book, position.contract_side)
    if bid is None:
        return _hold(DecisionReason.INSUFFICIENT_DEPTH)

    if thesis_dead:
        limit_price = max(Decimal("0.01"), bid - policy.convergence_tolerance)
        filled, avg, _ = _executable_sell(
            book,
            position.contract_side,
            position.quantity,
            limit_price,
        )
        if filled < position.quantity:
            return _hold(DecisionReason.THESIS_INVALIDATED)
        projected = _projected_net(
            fee_schedule,
            position.entry_price,
            avg,
            filled,
        )
        return _sell(
            DecisionReason.THESIS_INVALIDATED,
            limit_price=limit_price,
            exit_price=avg,
            quantity=filled,
            projected=projected,
        )

    try:
        with localcontext(_POLICY_DECIMAL_CONTEXT):
            converged = bid >= (
                fair_value.fair_probability - policy.convergence_tolerance
            )
            if mid is not None:
                converged = converged or (
                    abs(fair_value.fair_probability - mid)
                    <= policy.convergence_tolerance
                    and bid > position.entry_price
                )
    except DecimalException as exc:
        raise ExpertContractError("scalp_decimal") from exc

    if not converged:
        return _hold(DecisionReason.SIGNAL_NOT_TRIGGERED)

    limit_price = max(
        Decimal("0.01"),
        fair_value.fair_probability - policy.convergence_tolerance,
    )
    filled, avg, _ = _executable_sell(
        book,
        position.contract_side,
        position.quantity,
        limit_price,
    )
    if filled < position.quantity:
        return _hold(DecisionReason.INSUFFICIENT_DEPTH)
    projected = _projected_net(
        fee_schedule,
        position.entry_price,
        avg,
        filled,
    )
    return _sell(
        DecisionReason.FAIR_VALUE_CONVERGED,
        limit_price=limit_price,
        exit_price=avg,
        quantity=filled,
        projected=projected,
    )
