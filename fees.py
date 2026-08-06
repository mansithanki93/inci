"""Conservative single-fill paper fee estimate, Decimal end-to-end.

Kalshi first ceilings the trade fee to one centicent, then charges the balance
rounding needed to reach the account's target precision. A paper order creates
one aggregate simulated fill, so it has no multi-fill accumulator rebate.
Live accounting always uses the exchange's returned ``fee_cost``.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

_SEVEN = Decimal("0.07")
_CENTICENT = Decimal("0.0001")


def _d(v):
    return v if isinstance(v, Decimal) else Decimal(str(v))


def trade_fee_usd(price_cents, contracts, multiplier=1):
    p = _d(price_cents) / 100
    raw = (_d(multiplier) * _SEVEN * _d(contracts) * p * (1 - p))
    return raw.quantize(_CENTICENT, rounding=ROUND_CEILING)


def fee_usd(price_cents, contracts, multiplier=1, *, side="BUY",
            balance_precision_usd=Decimal("0.01")):
    """Estimated net fee for one aggregate fill.

    Reproduces the documented trade-fee ceiling and balance-rounding charge.
    It intentionally assumes no rebate because the simulator emits one fill
    per order; real fills always use API-reported fees.
    """
    price = _d(price_cents) / 100
    quantity = _d(contracts)
    precision = _d(balance_precision_usd)
    if side not in ("BUY", "SELL"):
        raise ValueError("fee side must be BUY or SELL")
    if (not price.is_finite() or not quantity.is_finite()
            or not precision.is_finite() or quantity < 0
            or not Decimal(0) <= price <= Decimal(1)
            or precision not in (Decimal("0.01"), Decimal("0.0001"))):
        raise ValueError("invalid fee inputs")
    trade_fee = trade_fee_usd(price_cents, quantity, multiplier)
    revenue = price * quantity * (Decimal(-1) if side == "BUY" else Decimal(1))
    balance_change = revenue - trade_fee
    floored = ((balance_change / precision).to_integral_value(
        rounding=ROUND_FLOOR) * precision)
    rounding_fee = balance_change - floored
    return trade_fee + rounding_fee


def fee_cents(price_cents, contracts=1, multiplier=1, *, side="BUY",
              balance_precision_usd=Decimal("0.01")):
    return fee_usd(
        price_cents, contracts, multiplier, side=side,
        balance_precision_usd=balance_precision_usd) * 100


def net_take_profit(entry_cents, tp_cents):
    return (_d(tp_cents) - fee_cents(entry_cents, side="BUY")
            - fee_cents(_d(entry_cents) + _d(tp_cents), side="SELL"))


def projected_scalp_pnl_usd(ask_cents, tp_cents, contracts,
                            slippage_cents,
                            balance_precision_usd=Decimal("0.01")):
    """Net paper P&L at the configured take-profit exit.

    Matches the resting paper path: BUY fills at the ask (no adverse slippage)
    and a take-profit SELL fills at the bid that printed entry+TP (also no
    adverse slippage). ``slippage_cents`` is retained for call-site compatibility
    (marketable stop-loss exits still use it in the executor) but does not
    affect this take-profit projection.
    Fees are rounded once per aggregate execution, matching the paper executor.
    """
    _ = slippage_cents
    qty = _d(contracts)
    entry = min(Decimal(99), _d(ask_cents))
    exit_fill = min(Decimal(99), entry + _d(tp_cents))
    gross = (exit_fill - entry) * qty / Decimal(100)
    return (gross
            - fee_usd(entry, qty, side="BUY",
                      balance_precision_usd=balance_precision_usd)
            - fee_usd(exit_fill, qty, side="SELL",
                      balance_precision_usd=balance_precision_usd))
