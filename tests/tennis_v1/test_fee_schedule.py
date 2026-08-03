from __future__ import annotations

from decimal import Decimal
import unittest

from inci_tennis_expert.fee_schedule import (
    FillSide,
    FrozenFeeSchedule,
    LiquidityRole,
    fee_for_fill,
)


class FeeScheduleTests(unittest.TestCase):
    def schedule(self, *, trade_fee_precision: Decimal) -> FrozenFeeSchedule:
        return FrozenFeeSchedule(
            schedule_id="test-schedule",
            series_tickers=("KXWTAMATCH",),
            taker_rate=Decimal("0.07"),
            maker_rate=Decimal("0.0175"),
            taker_multiplier=Decimal("1"),
            maker_multiplier=Decimal("1"),
            trade_fee_precision=trade_fee_precision,
            balance_precision=Decimal("0.0001"),
            effective_from_wall_ns=1,
            effective_until_wall_ns=None,
        )

    def test_taker_buy_includes_trade_ceiling_and_balance_rounding(self) -> None:
        schedule = FrozenFeeSchedule(
            schedule_id="kalshi-tennis-2026-07-07",
            series_tickers=("KXATPMATCH", "KXWTAMATCH"),
            taker_rate=Decimal("0.07"),
            maker_rate=Decimal("0.0175"),
            taker_multiplier=Decimal("1"),
            maker_multiplier=Decimal("1"),
            trade_fee_precision=Decimal("0.0001"),
            balance_precision=Decimal("0.01"),
            effective_from_wall_ns=1,
            effective_until_wall_ns=None,
        )

        fee = fee_for_fill(
            schedule,
            series_ticker="KXWTAMATCH",
            price=Decimal("0.50"),
            quantity=Decimal("10"),
            role=LiquidityRole.TAKER,
            side=FillSide.BUY,
            fill_wall_ns=2,
        )

        self.assertEqual(fee, Decimal("0.18"))

    def test_trade_fee_ceiling_honors_the_configured_increment(self) -> None:
        fee = fee_for_fill(
            self.schedule(trade_fee_precision=Decimal("0.005")),
            series_ticker="KXWTAMATCH",
            price=Decimal("0.50"),
            quantity=Decimal("1"),
            role=LiquidityRole.TAKER,
            side=FillSide.BUY,
            fill_wall_ns=2,
        )

        self.assertEqual(fee, Decimal("0.0200"))


if __name__ == "__main__":
    unittest.main()
