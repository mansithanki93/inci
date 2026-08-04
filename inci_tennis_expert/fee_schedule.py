from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    localcontext,
)
from typing import Final

from .contracts import (
    ExpertContractError,
    _boolean,
    _exact_self,
    _quantity,
    _safe_id,
    _sha256,
    expert_contract_sha256,
)


_FEE_DECIMAL_CONTEXT: Final[Context] = Context(
    prec=80,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_CENTICENT: Final[Decimal] = Decimal("0.0001")
_TAKER_COEFFICIENT: Final[Decimal] = Decimal("0.07")
_MAKER_COEFFICIENT: Final[Decimal] = Decimal("0.0175")
_BALANCE_PRECISION: Final[Decimal] = Decimal("0.01")


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(name)
    if not value.is_finite():
        raise ExpertContractError(name)
    return value


def _probability_price(value: object, name: str) -> Decimal:
    price = _decimal(value, name)
    if price < Decimal("0") or price > Decimal("1"):
        raise ExpertContractError(name)
    return price


def _positive_multiplier(value: object, name: str) -> Decimal:
    number = _decimal(value, name)
    if number <= Decimal("0"):
        raise ExpertContractError(name)
    return number


@dataclass(frozen=True, slots=True)
class FrozenFeeSchedule:
    schedule_id: str
    formula_version: str
    series_multiplier: Decimal
    taker_coefficient: Decimal
    maker_coefficient: Decimal
    balance_precision_usd: Decimal
    sealed: bool
    schedule_sha256: str

    def __post_init__(self) -> None:
        _exact_self(self, FrozenFeeSchedule)
        _safe_id(self.schedule_id, "schedule_id")
        _safe_id(self.formula_version, "formula_version")
        _positive_multiplier(self.series_multiplier, "series_multiplier")
        _positive_multiplier(self.taker_coefficient, "taker_coefficient")
        _positive_multiplier(self.maker_coefficient, "maker_coefficient")
        precision = _decimal(
            self.balance_precision_usd,
            "balance_precision_usd",
        )
        if precision not in (_BALANCE_PRECISION, _CENTICENT):
            raise ExpertContractError("balance_precision_usd")
        _boolean(self.sealed, "sealed")
        _sha256(self.schedule_sha256, "schedule_sha256")
        expected = fee_schedule_sha256(self)
        if self.schedule_sha256 != expected:
            raise ExpertContractError("schedule_sha256")
        if not self.sealed:
            raise ExpertContractError("fee_unsealed")


def fee_schedule_identity_payload(
    *,
    schedule_id: str,
    formula_version: str,
    series_multiplier: Decimal,
    taker_coefficient: Decimal,
    maker_coefficient: Decimal,
    balance_precision_usd: Decimal,
    sealed: bool,
) -> dict[str, object]:
    return {
        "schema": "frozen_fee_schedule_v1",
        "schedule_id": schedule_id,
        "formula_version": formula_version,
        "series_multiplier": series_multiplier,
        "taker_coefficient": taker_coefficient,
        "maker_coefficient": maker_coefficient,
        "balance_precision_usd": balance_precision_usd,
        "sealed": sealed,
    }


def fee_schedule_sha256(schedule: FrozenFeeSchedule) -> str:
    _exact_self(schedule, FrozenFeeSchedule)
    return expert_contract_sha256(
        fee_schedule_identity_payload(
            schedule_id=schedule.schedule_id,
            formula_version=schedule.formula_version,
            series_multiplier=schedule.series_multiplier,
            taker_coefficient=schedule.taker_coefficient,
            maker_coefficient=schedule.maker_coefficient,
            balance_precision_usd=schedule.balance_precision_usd,
            sealed=schedule.sealed,
        )
    )


def sealed_kalshi_taker_fee_schedule(
    *,
    schedule_id: str = "kalshi-taker-general-v1",
    series_multiplier: Decimal = Decimal("1"),
) -> FrozenFeeSchedule:
    payload = fee_schedule_identity_payload(
        schedule_id=schedule_id,
        formula_version="kalshi_general_taker_0_07_v1",
        series_multiplier=series_multiplier,
        taker_coefficient=_TAKER_COEFFICIENT,
        maker_coefficient=_MAKER_COEFFICIENT,
        balance_precision_usd=_BALANCE_PRECISION,
        sealed=True,
    )
    digest = expert_contract_sha256(payload)
    return FrozenFeeSchedule(
        schedule_id=schedule_id,
        formula_version="kalshi_general_taker_0_07_v1",
        series_multiplier=series_multiplier,
        taker_coefficient=_TAKER_COEFFICIENT,
        maker_coefficient=_MAKER_COEFFICIENT,
        balance_precision_usd=_BALANCE_PRECISION,
        sealed=True,
        schedule_sha256=digest,
    )


def trade_fee_usd(
    schedule: FrozenFeeSchedule,
    price: Decimal,
    contracts: Decimal,
    *,
    taker: bool = True,
) -> Decimal:
    _exact_self(schedule, FrozenFeeSchedule)
    if not schedule.sealed:
        raise ExpertContractError("fee_unsealed")
    _probability_price(price, "price")
    quantity = _quantity(contracts, "contracts")
    coefficient = (
        schedule.taker_coefficient if taker else schedule.maker_coefficient
    )
    try:
        with localcontext(_FEE_DECIMAL_CONTEXT):
            raw = (
                schedule.series_multiplier
                * coefficient
                * quantity
                * price
                * (Decimal("1") - price)
            )
            return raw.quantize(_CENTICENT, rounding=ROUND_CEILING)
    except DecimalException as exc:
        raise ExpertContractError("fee_decimal") from exc


def fill_fee_usd(
    schedule: FrozenFeeSchedule,
    price: Decimal,
    contracts: Decimal,
    *,
    side: str,
    taker: bool = True,
) -> Decimal:
    """Trade fee plus balance-rounding charge for one aggregate fill."""
    if side not in ("BUY", "SELL"):
        raise ExpertContractError("fee_side")
    trade_fee = trade_fee_usd(
        schedule,
        price,
        contracts,
        taker=taker,
    )
    quantity = _quantity(contracts, "contracts")
    try:
        with localcontext(_FEE_DECIMAL_CONTEXT):
            revenue = price * quantity * (
                Decimal("-1") if side == "BUY" else Decimal("1")
            )
            balance_change = revenue - trade_fee
            precision = schedule.balance_precision_usd
            floored = (
                (balance_change / precision).to_integral_value(
                    rounding=ROUND_FLOOR
                )
                * precision
            )
            rounding_fee = balance_change - floored
            return trade_fee + rounding_fee
    except DecimalException as exc:
        raise ExpertContractError("fee_decimal") from exc


def round_trip_fee_usd(
    schedule: FrozenFeeSchedule,
    entry_price: Decimal,
    exit_price: Decimal,
    contracts: Decimal,
    *,
    taker: bool = True,
) -> Decimal:
    return fill_fee_usd(
        schedule,
        entry_price,
        contracts,
        side="BUY",
        taker=taker,
    ) + fill_fee_usd(
        schedule,
        exit_price,
        contracts,
        side="SELL",
        taker=taker,
    )
