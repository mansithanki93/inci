from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from enum import Enum


_CONTEXT = Context(prec=50)


class FeeScheduleError(ValueError):
    pass


class LiquidityRole(str, Enum):
    TAKER = "taker"
    MAKER = "maker"


class FillSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise FeeScheduleError(name)
    if positive and value <= 0:
        raise FeeScheduleError(name)
    if not positive and value < 0:
        raise FeeScheduleError(name)
    return value


@dataclass(frozen=True, slots=True)
class FrozenFeeSchedule:
    schedule_id: str
    series_tickers: tuple[str, ...]
    taker_rate: Decimal
    maker_rate: Decimal
    taker_multiplier: Decimal
    maker_multiplier: Decimal
    trade_fee_precision: Decimal
    balance_precision: Decimal
    effective_from_wall_ns: int
    effective_until_wall_ns: int | None

    def __post_init__(self) -> None:
        if type(self.schedule_id) is not str or not self.schedule_id:
            raise FeeScheduleError("schedule_id")
        if (
            type(self.series_tickers) is not tuple
            or not self.series_tickers
            or any(type(value) is not str or not value for value in self.series_tickers)
            or self.series_tickers != tuple(sorted(set(self.series_tickers)))
        ):
            raise FeeScheduleError("series_tickers")
        _decimal(self.taker_rate, "taker_rate")
        _decimal(self.maker_rate, "maker_rate")
        _decimal(self.taker_multiplier, "taker_multiplier", positive=True)
        _decimal(self.maker_multiplier, "maker_multiplier", positive=True)
        _decimal(self.trade_fee_precision, "trade_fee_precision", positive=True)
        _decimal(self.balance_precision, "balance_precision", positive=True)
        if (
            type(self.effective_from_wall_ns) is not int
            or self.effective_from_wall_ns <= 0
        ):
            raise FeeScheduleError("effective_from_wall_ns")
        if self.effective_until_wall_ns is not None and (
            type(self.effective_until_wall_ns) is not int
            or self.effective_until_wall_ns <= self.effective_from_wall_ns
        ):
            raise FeeScheduleError("effective_until_wall_ns")


def fee_for_fill(
    schedule: FrozenFeeSchedule,
    *,
    series_ticker: str,
    price: Decimal,
    quantity: Decimal,
    role: LiquidityRole,
    side: FillSide,
    fill_wall_ns: int,
) -> Decimal:
    if type(schedule) is not FrozenFeeSchedule:
        raise FeeScheduleError("schedule")
    if type(series_ticker) is not str or series_ticker not in schedule.series_tickers:
        raise FeeScheduleError("series_ticker")
    p = _decimal(price, "price", positive=True)
    if p >= 1:
        raise FeeScheduleError("price")
    q = _decimal(quantity, "quantity", positive=True)
    if q != q.to_integral_value():
        raise FeeScheduleError("quantity")
    if type(role) is not LiquidityRole:
        raise FeeScheduleError("role")
    if type(side) is not FillSide:
        raise FeeScheduleError("side")
    if (
        type(fill_wall_ns) is not int
        or fill_wall_ns < schedule.effective_from_wall_ns
    ):
        raise FeeScheduleError("fill_wall_ns")
    if (
        schedule.effective_until_wall_ns is not None
        and fill_wall_ns >= schedule.effective_until_wall_ns
    ):
        raise FeeScheduleError("fill_wall_ns")

    with localcontext(_CONTEXT):
        rate = (
            schedule.taker_rate
            if role is LiquidityRole.TAKER
            else schedule.maker_rate
        )
        multiplier = (
            schedule.taker_multiplier
            if role is LiquidityRole.TAKER
            else schedule.maker_multiplier
        )
        raw_trade_fee = rate * multiplier * q * p * (Decimal("1") - p)
        trade_fee = (
            raw_trade_fee / schedule.trade_fee_precision
        ).to_integral_value(
            rounding=ROUND_CEILING
        ) * schedule.trade_fee_precision
        signed_revenue = p * q * (
            Decimal("-1") if side is FillSide.BUY else Decimal("1")
        )
        balance_change = signed_revenue - trade_fee
        floored_balance_change = (
            balance_change / schedule.balance_precision
        ).to_integral_value(rounding=ROUND_FLOOR) * schedule.balance_precision
        rounding_fee = balance_change - floored_balance_change
        return +(trade_fee + rounding_fee)
