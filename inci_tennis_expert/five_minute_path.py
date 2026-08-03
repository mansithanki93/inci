from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    localcontext,
)
from enum import Enum
from hashlib import sha256
from typing import Final

from inci_tennis_expert.contracts import (
    ContractSide,
    FairValueEstimate,
    PlayerSide,
    canonical_expert_bytes,
    expert_contract_sha256,
)
from inci_tennis_expert.first_set_model import (
    FirstSetReview,
    first_set_review_sha256,
)


__all__ = (
    "FiveMinutePathError",
    "DipReason",
    "CapacityStatus",
    "EntryAction",
    "EntryReason",
    "ExitAction",
    "ExitReason",
    "ForcedExitReason",
    "ForecastAbstentionReason",
    "DipObservation",
    "DipAssessment",
    "PriceLevel",
    "EntryCapacity",
    "EntrySnapshotBinding",
    "FiveMinuteForecast",
    "EntryGateInput",
    "EntryDecision",
    "PaperPosition",
    "ExitAssessment",
    "assess_v1_dip",
    "five_minute_forecast_sha256",
    "price_levels_sha256",
    "size_ioc_entry",
    "evaluate_entry",
    "assess_exit",
)


_DECIMAL_CONTEXT: Final[Context] = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_DIP_LOOKBACK_NS: Final[int] = 45_000_000_000
_HOLDING_HORIZON_NS: Final[int] = 300_000_000_000
_MAX_FEE_EVALUATIONS: Final[int] = 8_192


class FiveMinutePathError(ValueError):
    pass


def _integer(
    value: object,
    name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise TypeError(name)
    if minimum is not None and value < minimum:
        raise FiveMinutePathError(name)
    if maximum is not None and value > maximum:
        raise FiveMinutePathError(name)
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(name)
    return value


def _non_empty_text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(name)
    if not value or value != value.strip():
        raise FiveMinutePathError(name)
    return value


def _sha256(value: object, name: str) -> str:
    text = _non_empty_text(value, name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise FiveMinutePathError(name)
    return text


def _decimal(
    value: object,
    name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    minimum_exclusive: bool = False,
    integral: bool = False,
) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise FiveMinutePathError(name)
    if minimum is not None:
        if minimum_exclusive and value <= minimum:
            raise FiveMinutePathError(name)
        if not minimum_exclusive and value < minimum:
            raise FiveMinutePathError(name)
    if maximum is not None and value > maximum:
        raise FiveMinutePathError(name)
    if integral and value != value.to_integral_value():
        raise FiveMinutePathError(name)
    return value


def _price(value: object, name: str) -> Decimal:
    return _decimal(
        value,
        name,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        minimum_exclusive=True,
    )


def _quantity(
    value: object,
    name: str,
    *,
    positive: bool,
) -> Decimal:
    return _decimal(
        value,
        name,
        minimum=Decimal("0"),
        minimum_exclusive=positive,
        integral=True,
    )


def _exact(value: object, expected: type[object], name: str) -> None:
    if type(value) is not expected:
        raise TypeError(name)


def _fee_amount(
    fee: Callable[[Decimal, Decimal], Decimal],
    price: Decimal,
    quantity: Decimal,
) -> Decimal:
    amount = fee(price, quantity)
    return _decimal(amount, "fee", minimum=Decimal("0"))


def _fees_for_fills(
    fee: Callable[[Decimal, Decimal], Decimal],
    fills: tuple[PriceLevel, ...] | list[PriceLevel],
) -> Decimal:
    return sum(
        (_fee_amount(fee, fill.price, fill.quantity) for fill in fills),
        Decimal("0"),
    )


class DipReason(str, Enum):
    QUALIFIED = "qualified"
    NO_REFERENCE = "no_reference"
    BELOW_THRESHOLD = "below_threshold"


class CapacityStatus(str, Enum):
    ZERO = "zero"
    PARTIAL = "partial"
    FULL = "full"


class EntryAction(str, Enum):
    ABSTAIN = "abstain"
    RESEARCH_ELIGIBLE = "research_eligible"


class EntryReason(str, Enum):
    RESEARCH_ELIGIBLE = "research_eligible"
    SET_NOT_ELIGIBLE = "set_not_eligible"
    SCORE_UNTRUSTED = "score_untrusted"
    BOOK_UNTRUSTED = "book_untrusted"
    FIRST_SET_POSTERIOR_INVALID = "first_set_posterior_invalid"
    NO_FILLABLE_CAPACITY = "no_fillable_capacity"
    CAPACITY_ASK_MISMATCH = "capacity_ask_mismatch"
    NO_FAIR_VALUE_EDGE = "no_fair_value_edge"
    FAIR_VALUE_MISSING = "fair_value_missing"
    FAIR_VALUE_UNSUPPORTED = "fair_value_unsupported"
    FAIR_VALUE_BINDING_MISMATCH = "fair_value_binding_mismatch"
    DIP_INPUT_MISMATCH = "dip_input_mismatch"
    DIP_NOT_QUALIFIED = "dip_not_qualified"
    FORECAST_MISSING = "forecast_missing"
    FORECAST_UNSUPPORTED = "forecast_unsupported"
    FORECAST_UNFROZEN = "forecast_unfrozen"
    FORECAST_UNCALIBRATED = "forecast_uncalibrated"
    FORECAST_SIZE_MISMATCH = "forecast_size_mismatch"
    FORECAST_BINDING_MISMATCH = "forecast_binding_mismatch"
    FORECAST_PNL_INCONSISTENT = "forecast_pnl_inconsistent"
    LOWER_PNL_NOT_ABOVE_FIVE = "lower_pnl_not_above_five"
    SNAPSHOT_BINDING_MISSING = "snapshot_binding_missing"
    SNAPSHOT_BINDING_MISMATCH = "snapshot_binding_mismatch"
    STRATEGY_AUTHORITY_MISSING = "strategy_authority_missing"
    STRATEGY_AUTHORITY_MISMATCH = "strategy_authority_mismatch"
    MODEL_ARTIFACT_NOT_CAUSAL = "model_artifact_not_causal"
    FEE_SCHEDULE_CAPACITY_MISMATCH = "fee_schedule_capacity_mismatch"
    FORECAST_ARTIFACT_PIN_MISSING = "forecast_artifact_pin_missing"
    FORECAST_ARTIFACT_PIN_MISMATCH = "forecast_artifact_pin_mismatch"
    OUTCOME_ARTIFACT_PIN_MISSING = "outcome_artifact_pin_missing"
    OUTCOME_ARTIFACT_PIN_MISMATCH = "outcome_artifact_pin_mismatch"
    FEE_SCHEDULE_ARTIFACT_PIN_MISSING = "fee_schedule_artifact_pin_missing"
    FEE_SCHEDULE_ARTIFACT_PIN_MISMATCH = "fee_schedule_artifact_pin_mismatch"
    CONTRACT_SIDE_MISMATCH = "contract_side_mismatch"
    PAPER_MODEL_NOT_PROMOTED = "paper_model_not_promoted"


class ExitAction(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP = "stop"
    TIME = "time"
    HOLD = "hold"
    FORCED_EXIT = "forced_exit"
    PORTFOLIO_HALT = "portfolio_halt"


class ExitReason(str, Enum):
    TAKE_PROFIT_THRESHOLD = "take_profit_threshold"
    STOP_THRESHOLD = "stop_threshold"
    HOLDING_HORIZON = "holding_horizon"
    WITHIN_BOUNDS = "within_bounds"
    INSUFFICIENT_BID_DEPTH = "insufficient_bid_depth"
    THESIS_INVALIDATED = "thesis_invalidated"
    SOURCE_DISAGREEMENT = "source_disagreement"
    SOURCE_CORRECTION = "source_correction"
    MARKET_LIFECYCLE = "market_lifecycle"
    RISK_RULE = "risk_rule"
    FAIR_VALUE_REACHED = "fair_value_reached"


class ForcedExitReason(str, Enum):
    THESIS_INVALIDATED = "thesis_invalidated"
    SOURCE_DISAGREEMENT = "source_disagreement"
    SOURCE_CORRECTION = "source_correction"
    MARKET_LIFECYCLE = "market_lifecycle"
    RISK_RULE = "risk_rule"
    FAIR_VALUE_REACHED = "fair_value_reached"


class ForecastAbstentionReason(str, Enum):
    ARTIFACT_UNAVAILABLE = "artifact_unavailable"
    UNSUPPORTED_SCORE_STATE = "unsupported_score_state"
    INSUFFICIENT_MARKOUT_SUPPORT = "insufficient_markout_support"
    DISTRIBUTION_SHIFT = "distribution_shift"
    UNCALIBRATED_TAIL = "uncalibrated_tail"


@dataclass(frozen=True, slots=True)
class DipObservation:
    observed_monotonic_ns: int
    contract_side: ContractSide
    epoch: int
    set_number: int
    executable_ask: Decimal

    def __post_init__(self) -> None:
        _integer(
            self.observed_monotonic_ns,
            "observed_monotonic_ns",
            minimum=0,
        )
        _exact(self.contract_side, ContractSide, "contract_side")
        _integer(self.epoch, "epoch", minimum=0)
        _integer(self.set_number, "set_number", minimum=1, maximum=3)
        _price(self.executable_ask, "executable_ask")


@dataclass(frozen=True, slots=True)
class DipAssessment:
    current: DipObservation
    reference: DipObservation | None
    dip: Decimal
    reason: DipReason

    def __post_init__(self) -> None:
        _exact(self.current, DipObservation, "current")
        if self.reference is not None:
            _exact(self.reference, DipObservation, "reference")
        _decimal(
            self.dip,
            "dip",
            minimum=Decimal("-1"),
            maximum=Decimal("1"),
        )
        _exact(self.reason, DipReason, "reason")
        if self.reason is DipReason.NO_REFERENCE:
            if self.reference is not None or self.dip != Decimal("0"):
                raise FiveMinutePathError("dip_assessment")
            return
        if self.reference is None:
            raise FiveMinutePathError("dip_assessment")
        if (
            self.reference.observed_monotonic_ns
            >= self.current.observed_monotonic_ns
            or self.reference.observed_monotonic_ns
            < self.current.observed_monotonic_ns - _DIP_LOOKBACK_NS
            or self.reference.contract_side is not self.current.contract_side
            or self.reference.epoch != self.current.epoch
            or self.reference.set_number != self.current.set_number
        ):
            raise FiveMinutePathError("dip_assessment")
        try:
            with localcontext(_DECIMAL_CONTEXT):
                expected_dip = (
                    self.reference.executable_ask
                    - self.current.executable_ask
                )
        except DecimalException:
            raise FiveMinutePathError("decimal_arithmetic") from None
        if expected_dip != self.dip:
            raise FiveMinutePathError("dip_assessment")
        if self.reason is DipReason.QUALIFIED:
            if self.dip < Decimal("0.07"):
                raise FiveMinutePathError("dip_assessment")
        elif self.dip >= Decimal("0.07"):
            raise FiveMinutePathError("dip_assessment")

    @property
    def reference_ask(self) -> Decimal | None:
        return (
            None
            if self.reference is None
            else self.reference.executable_ask
        )

    @property
    def current_ask(self) -> Decimal:
        return self.current.executable_ask

    @property
    def qualified(self) -> bool:
        return self.reason is DipReason.QUALIFIED


@dataclass(frozen=True, slots=True)
class PriceLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        _price(self.price, "price")
        _quantity(self.quantity, "quantity", positive=True)


def price_levels_sha256(levels: tuple[PriceLevel, ...]) -> str:
    if type(levels) is not tuple:
        raise TypeError("levels")
    for level in levels:
        _exact(level, PriceLevel, "levels")
    projection = [
        {"price": level.price, "quantity": level.quantity}
        for level in levels
    ]
    return sha256(
        b"INCI-PRICE-LEVELS-V1\0" + canonical_expert_bytes(projection)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EntryCapacity:
    requested_quantity: Decimal
    filled_quantity: Decimal
    fills: tuple[PriceLevel, ...]
    gross_debit: Decimal
    entry_fee: Decimal
    all_in_debit: Decimal
    status: CapacityStatus

    def __post_init__(self) -> None:
        _quantity(
            self.requested_quantity,
            "requested_quantity",
            positive=True,
        )
        _quantity(self.filled_quantity, "filled_quantity", positive=False)
        if self.filled_quantity > self.requested_quantity:
            raise FiveMinutePathError("filled_quantity")
        if type(self.fills) is not tuple:
            raise TypeError("fills")
        for fill in self.fills:
            _exact(fill, PriceLevel, "fills")
        _decimal(self.gross_debit, "gross_debit", minimum=Decimal("0"))
        _decimal(self.entry_fee, "entry_fee", minimum=Decimal("0"))
        _decimal(
            self.all_in_debit,
            "all_in_debit",
            minimum=Decimal("0"),
            maximum=Decimal("50.00"),
        )
        _exact(self.status, CapacityStatus, "status")
        try:
            with localcontext(_DECIMAL_CONTEXT):
                fill_quantity = sum(
                    (fill.quantity for fill in self.fills),
                    Decimal("0"),
                )
                fill_gross = sum(
                    (
                        fill.price * fill.quantity
                        for fill in self.fills
                    ),
                    Decimal("0"),
                )
                expected_all_in = self.gross_debit + self.entry_fee
        except DecimalException:
            raise FiveMinutePathError("decimal_arithmetic") from None
        if (
            fill_quantity != self.filled_quantity
            or fill_gross != self.gross_debit
            or expected_all_in != self.all_in_debit
        ):
            raise FiveMinutePathError("entry_capacity")
        if self.filled_quantity == Decimal("0"):
            if (
                self.status is not CapacityStatus.ZERO
                or self.fills
                or self.all_in_debit != Decimal("0")
            ):
                raise FiveMinutePathError("entry_capacity")
        elif self.all_in_debit <= Decimal("0"):
            raise FiveMinutePathError("all_in_debit")
        elif self.filled_quantity == self.requested_quantity:
            if self.status is not CapacityStatus.FULL:
                raise FiveMinutePathError("status")
        elif self.status is not CapacityStatus.PARTIAL:
            raise FiveMinutePathError("status")


@dataclass(frozen=True, slots=True)
class EntrySnapshotBinding:
    """Immutable authority inputs shared by one entry gate and forecast."""

    canonical_match_id: str
    contract_side: ContractSide
    player_side: PlayerSide
    provider_revision: int
    provider_correction_epoch: int
    book_epoch: int
    book_sequence: int
    book_snapshot_sha256: str
    fee_series_ticker: str
    decision_wall_ns: int
    decision_monotonic_ns: int
    entry_ask_levels_sha256: str
    first_set_review_sha256: str
    first_set_point_history_sha256: str
    first_set_consensus_epoch: int
    session_manifest_sha256: str
    outcome_artifact_id: str
    outcome_artifact_sha256: str
    fair_value_estimate_sha256: str
    markout_artifact_id: str
    markout_artifact_sha256: str
    markout_forecast_sha256: str
    fee_schedule_artifact_id: str
    fee_schedule_sha256: str

    def __post_init__(self) -> None:
        _non_empty_text(self.canonical_match_id, "canonical_match_id")
        _exact(self.contract_side, ContractSide, "contract_side")
        _exact(self.player_side, PlayerSide, "player_side")
        _integer(self.provider_revision, "provider_revision", minimum=0)
        _integer(
            self.provider_correction_epoch,
            "provider_correction_epoch",
            minimum=0,
        )
        _integer(self.book_epoch, "book_epoch", minimum=0)
        _integer(self.book_sequence, "book_sequence", minimum=0)
        _sha256(self.book_snapshot_sha256, "book_snapshot_sha256")
        _non_empty_text(self.fee_series_ticker, "fee_series_ticker")
        _integer(self.decision_wall_ns, "decision_wall_ns", minimum=0)
        _integer(
            self.decision_monotonic_ns,
            "decision_monotonic_ns",
            minimum=0,
        )
        _sha256(
            self.entry_ask_levels_sha256,
            "entry_ask_levels_sha256",
        )
        _sha256(self.first_set_review_sha256, "first_set_review_sha256")
        _sha256(
            self.first_set_point_history_sha256,
            "first_set_point_history_sha256",
        )
        _integer(
            self.first_set_consensus_epoch,
            "first_set_consensus_epoch",
            minimum=0,
        )
        _sha256(self.session_manifest_sha256, "session_manifest_sha256")
        _non_empty_text(self.outcome_artifact_id, "outcome_artifact_id")
        _sha256(self.outcome_artifact_sha256, "outcome_artifact_sha256")
        _sha256(
            self.fair_value_estimate_sha256,
            "fair_value_estimate_sha256",
        )
        _non_empty_text(self.markout_artifact_id, "markout_artifact_id")
        _sha256(self.markout_artifact_sha256, "markout_artifact_sha256")
        _sha256(
            self.markout_forecast_sha256,
            "markout_forecast_sha256",
        )
        _non_empty_text(
            self.fee_schedule_artifact_id,
            "fee_schedule_artifact_id",
        )
        _sha256(self.fee_schedule_sha256, "fee_schedule_sha256")


@dataclass(frozen=True, slots=True)
class FiveMinuteForecast:
    artifact_version: str
    artifact_sha256: str
    supported: bool
    frozen: bool
    calibrated: bool
    quantity: Decimal
    expected_net_pnl: Decimal
    lower_expected_net_pnl: Decimal
    upper_expected_net_pnl: Decimal
    fill_probability: Decimal
    loss_probability: Decimal
    tail_loss_estimate: Decimal
    supporting_sample_count: int
    abstention_reason: ForecastAbstentionReason | None
    snapshot_binding: EntrySnapshotBinding | None = None

    def __post_init__(self) -> None:
        _non_empty_text(self.artifact_version, "artifact_version")
        _sha256(self.artifact_sha256, "artifact_sha256")
        _boolean(self.supported, "supported")
        _boolean(self.frozen, "frozen")
        _boolean(self.calibrated, "calibrated")
        _quantity(self.quantity, "quantity", positive=True)
        _decimal(self.expected_net_pnl, "expected_net_pnl")
        _decimal(
            self.lower_expected_net_pnl,
            "lower_expected_net_pnl",
        )
        _decimal(
            self.upper_expected_net_pnl,
            "upper_expected_net_pnl",
        )
        if not (
            self.lower_expected_net_pnl
            <= self.expected_net_pnl
            <= self.upper_expected_net_pnl
        ):
            raise FiveMinutePathError("pnl_interval")
        _decimal(
            self.fill_probability,
            "fill_probability",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        )
        _decimal(
            self.loss_probability,
            "loss_probability",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        )
        if self.loss_probability > self.fill_probability:
            raise FiveMinutePathError("probability_consistency")
        _decimal(
            self.tail_loss_estimate,
            "tail_loss_estimate",
            maximum=Decimal("0"),
        )
        _integer(
            self.supporting_sample_count,
            "supporting_sample_count",
            minimum=0,
        )
        if self.abstention_reason is not None:
            _exact(
                self.abstention_reason,
                ForecastAbstentionReason,
                "abstention_reason",
            )
        if self.snapshot_binding is not None:
            _exact(
                self.snapshot_binding,
                EntrySnapshotBinding,
                "snapshot_binding",
            )
        if self.supported:
            if (
                self.supporting_sample_count == 0
                or self.abstention_reason is not None
                or self.fill_probability <= Decimal("0")
            ):
                if self.supporting_sample_count == 0:
                    reason = "supporting_sample_count"
                elif self.abstention_reason is not None:
                    reason = "abstention_reason"
                else:
                    reason = "probability_consistency"
                raise FiveMinutePathError(reason)
        elif self.abstention_reason is None:
            raise FiveMinutePathError("abstention_reason")


def five_minute_forecast_sha256(forecast: FiveMinuteForecast) -> str:
    _exact(forecast, FiveMinuteForecast, "forecast")
    projection = {
        "artifact_version": forecast.artifact_version,
        "artifact_sha256": forecast.artifact_sha256,
        "supported": forecast.supported,
        "frozen": forecast.frozen,
        "calibrated": forecast.calibrated,
        "quantity": forecast.quantity,
        "expected_net_pnl": forecast.expected_net_pnl,
        "lower_expected_net_pnl": forecast.lower_expected_net_pnl,
        "upper_expected_net_pnl": forecast.upper_expected_net_pnl,
        "fill_probability": forecast.fill_probability,
        "loss_probability": forecast.loss_probability,
        "tail_loss_estimate": forecast.tail_loss_estimate,
        "supporting_sample_count": forecast.supporting_sample_count,
        "abstention_reason": (
            None
            if forecast.abstention_reason is None
            else forecast.abstention_reason.value
        ),
    }
    return sha256(
        b"INCI-FIVE-MINUTE-FORECAST-V1\0"
        + canonical_expert_bytes(projection)
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class EntryGateInput:
    set_number: int
    score_trusted: bool
    book_trusted: bool
    first_set_review: FirstSetReview | None
    current_ask: Decimal
    conservative_fair_value: Decimal
    fair_value: FairValueEstimate | None
    dip: DipAssessment
    capacity: EntryCapacity
    forecast: FiveMinuteForecast | None
    snapshot_binding: EntrySnapshotBinding | None = None

    def __post_init__(self) -> None:
        _integer(self.set_number, "set_number", minimum=1, maximum=3)
        _boolean(self.score_trusted, "score_trusted")
        _boolean(self.book_trusted, "book_trusted")
        if self.first_set_review is not None:
            _exact(self.first_set_review, FirstSetReview, "first_set_review")
            FirstSetReview.__post_init__(self.first_set_review)
        _price(self.current_ask, "current_ask")
        _decimal(
            self.conservative_fair_value,
            "conservative_fair_value",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        )
        if self.fair_value is not None:
            _exact(self.fair_value, FairValueEstimate, "fair_value")
            FairValueEstimate.__post_init__(self.fair_value)
        _exact(self.dip, DipAssessment, "dip")
        _exact(self.capacity, EntryCapacity, "capacity")
        DipAssessment.__post_init__(self.dip)
        EntryCapacity.__post_init__(self.capacity)
        if self.forecast is not None:
            _exact(self.forecast, FiveMinuteForecast, "forecast")
            FiveMinuteForecast.__post_init__(self.forecast)
        if self.snapshot_binding is not None:
            _exact(
                self.snapshot_binding,
                EntrySnapshotBinding,
                "snapshot_binding",
            )
            EntrySnapshotBinding.__post_init__(self.snapshot_binding)


@dataclass(frozen=True, slots=True)
class EntryDecision:
    action: EntryAction
    reason: EntryReason
    quantity: Decimal
    all_in_debit: Decimal

    def __post_init__(self) -> None:
        _exact(self.action, EntryAction, "action")
        _exact(self.reason, EntryReason, "reason")
        _quantity(self.quantity, "quantity", positive=False)
        _decimal(
            self.all_in_debit,
            "all_in_debit",
            minimum=Decimal("0"),
            maximum=Decimal("50.00"),
        )
        if self.action is EntryAction.ABSTAIN:
            if (
                self.quantity != Decimal("0")
                or self.all_in_debit != Decimal("0")
                or self.reason is EntryReason.RESEARCH_ELIGIBLE
            ):
                raise FiveMinutePathError("entry_decision")
        elif (
            self.reason is not EntryReason.RESEARCH_ELIGIBLE
            or self.quantity <= Decimal("0")
            or self.all_in_debit <= Decimal("0")
        ):
            raise FiveMinutePathError("entry_decision")


@dataclass(frozen=True, slots=True)
class PaperPosition:
    opened_monotonic_ns: int
    filled_quantity: Decimal
    entry_gross_debit: Decimal
    allocated_entry_fees: Decimal

    def __post_init__(self) -> None:
        _integer(
            self.opened_monotonic_ns,
            "opened_monotonic_ns",
            minimum=0,
        )
        _quantity(self.filled_quantity, "filled_quantity", positive=True)
        _decimal(
            self.entry_gross_debit,
            "entry_gross_debit",
            minimum=Decimal("0"),
            minimum_exclusive=True,
        )
        _decimal(
            self.allocated_entry_fees,
            "allocated_entry_fees",
            minimum=Decimal("0"),
        )
        try:
            with localcontext(_DECIMAL_CONTEXT):
                entry_cash_at_risk = (
                    self.entry_gross_debit
                    + self.allocated_entry_fees
                )
        except DecimalException:
            raise FiveMinutePathError("decimal_arithmetic") from None
        if entry_cash_at_risk > Decimal("50.00"):
            raise FiveMinutePathError("entry_cash_at_risk")


@dataclass(frozen=True, slots=True)
class ExitAssessment:
    action: ExitAction
    reason: ExitReason
    executable_quantity: Decimal
    fills: tuple[PriceLevel, ...]
    gross_proceeds: Decimal
    exit_fee: Decimal
    net_liquidation_pnl: Decimal | None
    residual_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _exact(self.action, ExitAction, "action")
        _exact(self.reason, ExitReason, "reason")
        _quantity(
            self.executable_quantity,
            "executable_quantity",
            positive=False,
        )
        if type(self.fills) is not tuple:
            raise TypeError("fills")
        for fill in self.fills:
            _exact(fill, PriceLevel, "fills")
        _decimal(
            self.gross_proceeds,
            "gross_proceeds",
            minimum=Decimal("0"),
        )
        _decimal(self.exit_fee, "exit_fee", minimum=Decimal("0"))
        _quantity(
            self.residual_quantity,
            "residual_quantity",
            positive=False,
        )
        if self.net_liquidation_pnl is not None:
            _decimal(
                self.net_liquidation_pnl,
                "net_liquidation_pnl",
            )


def assess_v1_dip(
    history: tuple[DipObservation, ...],
    current: DipObservation,
) -> DipAssessment:
    if type(history) is not tuple:
        raise TypeError("history")
    for observation in history:
        _exact(observation, DipObservation, "history")
    _exact(current, DipObservation, "current")
    prior = tuple(
        observation
        for observation in history
        if observation.observed_monotonic_ns
        < current.observed_monotonic_ns
        and observation.observed_monotonic_ns
        >= current.observed_monotonic_ns - _DIP_LOOKBACK_NS
        and observation.contract_side is current.contract_side
        and observation.epoch == current.epoch
        and observation.set_number == current.set_number
    )
    if not prior:
        return DipAssessment(
            current=current,
            reference=None,
            dip=Decimal("0"),
            reason=DipReason.NO_REFERENCE,
        )
    try:
        with localcontext(_DECIMAL_CONTEXT):
            reference = max(
                prior,
                key=lambda item: (
                    item.executable_ask,
                    item.observed_monotonic_ns,
                ),
            )
            dip = reference.executable_ask - current.executable_ask
            reason = (
                DipReason.QUALIFIED
                if dip >= Decimal("0.07")
                else DipReason.BELOW_THRESHOLD
            )
            return DipAssessment(
                current=current,
                reference=reference,
                dip=dip,
                reason=reason,
            )
    except DecimalException:
        raise FiveMinutePathError("decimal_arithmetic") from None


def size_ioc_entry(
    ask_levels: tuple[PriceLevel, ...],
    *,
    requested_quantity: Decimal,
    fee: Callable[[Decimal, Decimal], Decimal],
    debit_cap: Decimal = Decimal("50.00"),
) -> EntryCapacity:
    if type(ask_levels) is not tuple:
        raise TypeError("ask_levels")
    for level in ask_levels:
        _exact(level, PriceLevel, "ask_levels")
    if any(
        left.price > right.price
        for left, right in zip(ask_levels, ask_levels[1:])
    ):
        raise FiveMinutePathError("ask_levels")
    _quantity(
        requested_quantity,
        "requested_quantity",
        positive=True,
    )
    _decimal(
        debit_cap,
        "debit_cap",
        minimum=Decimal("0"),
        maximum=Decimal("50.00"),
        minimum_exclusive=True,
    )
    if not callable(fee):
        raise TypeError("fee")
    fee_evaluations = 0

    def bounded_fee(price: Decimal, quantity: Decimal) -> Decimal:
        nonlocal fee_evaluations
        if fee_evaluations >= _MAX_FEE_EVALUATIONS:
            raise FiveMinutePathError("fee_search_budget")
        fee_evaluations += 1
        return fee(price, quantity)

    try:
        with localcontext(_DECIMAL_CONTEXT):
            return _size_ioc_entry(
                ask_levels,
                requested_quantity=requested_quantity,
                fee=bounded_fee,
                debit_cap=debit_cap,
            )
    except DecimalException:
        raise FiveMinutePathError("decimal_arithmetic") from None


def _size_ioc_entry(
    ask_levels: tuple[PriceLevel, ...],
    *,
    requested_quantity: Decimal,
    fee: Callable[[Decimal, Decimal], Decimal],
    debit_cap: Decimal,
) -> EntryCapacity:
    filled_quantity = Decimal("0")
    gross_debit = Decimal("0")
    fills: list[PriceLevel] = []

    for level in ask_levels:
        remaining = requested_quantity - filled_quantity
        maximum = min(level.quantity, remaining)
        if maximum <= Decimal("0"):
            break

        gross_budget = debit_cap - gross_debit
        gross_affordable_units = int(
            (gross_budget / level.price).to_integral_value(
                rounding=ROUND_FLOOR,
            )
        )
        starting_units = min(int(maximum), gross_affordable_units)
        accepted_units = 0
        for candidate_units in range(starting_units, 0, -1):
            candidate_quantity = Decimal(candidate_units)
            candidate_gross = (
                gross_debit + level.price * candidate_quantity
            )
            candidate_fee = _fees_for_fills(
                fee,
                [*fills, PriceLevel(level.price, candidate_quantity)],
            )
            if candidate_gross + candidate_fee <= debit_cap:
                accepted_units = candidate_units
                break

        if accepted_units == 0:
            break
        accepted = Decimal(accepted_units)
        fills.append(PriceLevel(level.price, accepted))
        filled_quantity += accepted
        gross_debit += level.price * accepted
        if accepted < maximum:
            break

    if filled_quantity == Decimal("0"):
        entry_fee = Decimal("0")
    else:
        entry_fee = _fees_for_fills(fee, fills)
    all_in_debit = gross_debit + entry_fee
    if filled_quantity == Decimal("0"):
        status = CapacityStatus.ZERO
    elif filled_quantity == requested_quantity:
        status = CapacityStatus.FULL
    else:
        status = CapacityStatus.PARTIAL
    return EntryCapacity(
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        fills=tuple(fills),
        gross_debit=gross_debit,
        entry_fee=entry_fee,
        all_in_debit=all_in_debit,
        status=status,
    )


def _entry_abstain(reason: EntryReason) -> EntryDecision:
    return EntryDecision(
        action=EntryAction.ABSTAIN,
        reason=reason,
        quantity=Decimal("0"),
        all_in_debit=Decimal("0"),
    )


def evaluate_entry(candidate: EntryGateInput) -> EntryDecision:
    """Evaluate research eligibility; this function cannot reserve exposure."""
    _exact(candidate, EntryGateInput, "candidate")
    EntryGateInput.__post_init__(candidate)
    if candidate.set_number not in {2, 3}:
        return _entry_abstain(EntryReason.SET_NOT_ELIGIBLE)
    if not candidate.score_trusted:
        return _entry_abstain(EntryReason.SCORE_UNTRUSTED)
    if not candidate.book_trusted:
        return _entry_abstain(EntryReason.BOOK_UNTRUSTED)
    if (
        candidate.first_set_review is None
        or not candidate.first_set_review.supported
    ):
        return _entry_abstain(EntryReason.FIRST_SET_POSTERIOR_INVALID)
    if candidate.fair_value is None:
        return _entry_abstain(EntryReason.FAIR_VALUE_MISSING)
    if (
        not candidate.fair_value.supported
        or candidate.fair_value.calibration_artifact_sha256 is None
    ):
        return _entry_abstain(EntryReason.FAIR_VALUE_UNSUPPORTED)
    try:
        with localcontext(_DECIMAL_CONTEXT):
            expected_conservative_fair_value = (
                candidate.fair_value.lower_probability
                if candidate.dip.current.contract_side is ContractSide.YES
                else Decimal("1") - candidate.fair_value.upper_probability
            )
    except DecimalException:
        raise FiveMinutePathError("decimal_arithmetic") from None
    if candidate.conservative_fair_value != expected_conservative_fair_value:
        return _entry_abstain(EntryReason.FAIR_VALUE_BINDING_MISMATCH)
    if (
        candidate.capacity.filled_quantity <= Decimal("0")
        or candidate.capacity.all_in_debit <= Decimal("0")
    ):
        return _entry_abstain(EntryReason.NO_FILLABLE_CAPACITY)
    if (
        candidate.current_ask != candidate.dip.current.executable_ask
        or candidate.set_number != candidate.dip.current.set_number
    ):
        return _entry_abstain(EntryReason.DIP_INPUT_MISMATCH)
    if candidate.capacity.fills[0].price != candidate.current_ask:
        return _entry_abstain(EntryReason.CAPACITY_ASK_MISMATCH)
    try:
        with localcontext(_DECIMAL_CONTEXT):
            all_in_average = (
                candidate.capacity.all_in_debit
                / candidate.capacity.filled_quantity
            )
    except DecimalException:
        raise FiveMinutePathError("decimal_arithmetic") from None
    if (
        candidate.current_ask >= candidate.conservative_fair_value
        or all_in_average >= candidate.conservative_fair_value
        or any(
            fill.price >= candidate.conservative_fair_value
            for fill in candidate.capacity.fills
        )
    ):
        return _entry_abstain(EntryReason.NO_FAIR_VALUE_EDGE)
    if not candidate.dip.qualified:
        return _entry_abstain(EntryReason.DIP_NOT_QUALIFIED)
    if candidate.forecast is None:
        return _entry_abstain(EntryReason.FORECAST_MISSING)
    if (
        candidate.snapshot_binding is None
        or candidate.forecast.snapshot_binding is None
    ):
        return _entry_abstain(EntryReason.SNAPSHOT_BINDING_MISSING)
    if candidate.snapshot_binding != candidate.forecast.snapshot_binding:
        return _entry_abstain(EntryReason.SNAPSHOT_BINDING_MISMATCH)
    if (
        candidate.snapshot_binding.contract_side
        is not candidate.dip.current.contract_side
        or candidate.snapshot_binding.decision_monotonic_ns
        != candidate.dip.current.observed_monotonic_ns
    ):
        return _entry_abstain(EntryReason.SNAPSHOT_BINDING_MISMATCH)
    if (
        candidate.snapshot_binding.first_set_review_sha256
        != first_set_review_sha256(candidate.first_set_review)
        or candidate.snapshot_binding.first_set_point_history_sha256
        != candidate.first_set_review.point_history_sha256
        or candidate.first_set_review.consensus_epoch is None
        or candidate.snapshot_binding.first_set_consensus_epoch
        != candidate.first_set_review.consensus_epoch
    ):
        return _entry_abstain(EntryReason.FIRST_SET_POSTERIOR_INVALID)
    if (
        candidate.snapshot_binding.player_side
        is not candidate.fair_value.player_side
        or candidate.snapshot_binding.fair_value_estimate_sha256
        != expert_contract_sha256(candidate.fair_value)
        or candidate.snapshot_binding.outcome_artifact_sha256
        != candidate.fair_value.model_sha256
        or candidate.fair_value.calibration_artifact_sha256
        != candidate.fair_value.model_sha256
    ):
        return _entry_abstain(EntryReason.FAIR_VALUE_BINDING_MISMATCH)
    if (
        candidate.snapshot_binding.markout_forecast_sha256
        != five_minute_forecast_sha256(candidate.forecast)
    ):
        return _entry_abstain(EntryReason.FORECAST_BINDING_MISMATCH)
    if not candidate.forecast.supported:
        return _entry_abstain(EntryReason.FORECAST_UNSUPPORTED)
    if not candidate.forecast.frozen:
        return _entry_abstain(EntryReason.FORECAST_UNFROZEN)
    if not candidate.forecast.calibrated:
        return _entry_abstain(EntryReason.FORECAST_UNCALIBRATED)
    if candidate.forecast.quantity != candidate.capacity.filled_quantity:
        return _entry_abstain(EntryReason.FORECAST_SIZE_MISMATCH)
    try:
        with localcontext(_DECIMAL_CONTEXT):
            maximum_filled_profit = (
                candidate.capacity.filled_quantity
                - candidate.capacity.all_in_debit
            )
            maximum_expected_profit = (
                candidate.forecast.fill_probability
                * maximum_filled_profit
            )
            minimum_expected_profit = -(
                candidate.forecast.fill_probability
                * candidate.capacity.all_in_debit
            )
            minimum_tail_pnl = -candidate.capacity.all_in_debit
    except DecimalException:
        raise FiveMinutePathError("decimal_arithmetic") from None
    if (
        candidate.forecast.upper_expected_net_pnl
        > maximum_expected_profit
        or candidate.forecast.lower_expected_net_pnl
        < minimum_expected_profit
        or candidate.forecast.tail_loss_estimate < minimum_tail_pnl
    ):
        return _entry_abstain(EntryReason.FORECAST_PNL_INCONSISTENT)
    if candidate.forecast.lower_expected_net_pnl <= Decimal("5.00"):
        return _entry_abstain(EntryReason.LOWER_PNL_NOT_ABOVE_FIVE)
    return EntryDecision(
        action=EntryAction.RESEARCH_ELIGIBLE,
        reason=EntryReason.RESEARCH_ELIGIBLE,
        quantity=candidate.capacity.filled_quantity,
        all_in_debit=candidate.capacity.all_in_debit,
    )


def assess_exit(
    position: PaperPosition,
    bid_levels: tuple[PriceLevel, ...],
    *,
    now_monotonic_ns: int,
    fee: Callable[[Decimal, Decimal], Decimal],
    forced_exit: ForcedExitReason | None = None,
) -> ExitAssessment:
    _exact(position, PaperPosition, "position")
    if type(bid_levels) is not tuple:
        raise TypeError("bid_levels")
    for level in bid_levels:
        _exact(level, PriceLevel, "bid_levels")
    if any(
        left.price < right.price
        for left, right in zip(bid_levels, bid_levels[1:])
    ):
        raise FiveMinutePathError("bid_levels")
    _integer(
        now_monotonic_ns,
        "now_monotonic_ns",
        minimum=position.opened_monotonic_ns,
    )
    if not callable(fee):
        raise TypeError("fee")
    if forced_exit is not None:
        _exact(forced_exit, ForcedExitReason, "forced_exit")
    try:
        with localcontext(_DECIMAL_CONTEXT):
            return _assess_exit(
                position,
                bid_levels,
                now_monotonic_ns=now_monotonic_ns,
                fee=fee,
                forced_exit=forced_exit,
            )
    except DecimalException:
        raise FiveMinutePathError("decimal_arithmetic") from None


def _assess_exit(
    position: PaperPosition,
    bid_levels: tuple[PriceLevel, ...],
    *,
    now_monotonic_ns: int,
    fee: Callable[[Decimal, Decimal], Decimal],
    forced_exit: ForcedExitReason | None,
) -> ExitAssessment:
    remaining = position.filled_quantity
    gross_proceeds = Decimal("0")
    fills: list[PriceLevel] = []
    for level in bid_levels:
        quantity = min(remaining, level.quantity)
        if quantity <= Decimal("0"):
            break
        fills.append(PriceLevel(level.price, quantity))
        gross_proceeds += level.price * quantity
        remaining -= quantity
        if remaining == Decimal("0"):
            break
    executable_quantity = position.filled_quantity - remaining
    exit_fee = _fees_for_fills(fee, fills)
    if remaining > Decimal("0"):
        return ExitAssessment(
            action=ExitAction.PORTFOLIO_HALT,
            reason=ExitReason.INSUFFICIENT_BID_DEPTH,
            executable_quantity=executable_quantity,
            fills=tuple(fills),
            gross_proceeds=gross_proceeds,
            exit_fee=exit_fee,
            net_liquidation_pnl=None,
            residual_quantity=remaining,
        )
    net_pnl = (
        gross_proceeds
        - exit_fee
        - position.entry_gross_debit
        - position.allocated_entry_fees
    )
    if forced_exit is not None:
        action = ExitAction.FORCED_EXIT
        reason = ExitReason(forced_exit.value)
    elif net_pnl >= Decimal("5.00"):
        action = ExitAction.TAKE_PROFIT
        reason = ExitReason.TAKE_PROFIT_THRESHOLD
    elif net_pnl <= Decimal("-5.00"):
        action = ExitAction.STOP
        reason = ExitReason.STOP_THRESHOLD
    elif (
        now_monotonic_ns - position.opened_monotonic_ns
        >= _HOLDING_HORIZON_NS
    ):
        action = ExitAction.TIME
        reason = ExitReason.HOLDING_HORIZON
    else:
        action = ExitAction.HOLD
        reason = ExitReason.WITHIN_BOUNDS
    return ExitAssessment(
        action=action,
        reason=reason,
        executable_quantity=executable_quantity,
        fills=tuple(fills),
        gross_proceeds=gross_proceeds,
        exit_fee=exit_fee,
        net_liquidation_pnl=net_pnl,
    )
