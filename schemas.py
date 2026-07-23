"""V2 API contract layer. Every response field consumed by Inci is validated
here before business logic sees it.
Prices and quantities are Decimal END-TO-END: subpenny prices ("0.525")
and fractional quantities ("12.5") are preserved exactly, never coerced
through float. `bot.py --check` runs these validators against live
responses. Order mutation remains disabled; no order endpoint is probed by
the local test suite.

Endpoint split (official V2):
  create : POST   /portfolio/events/orders
  cancel : DELETE /portfolio/events/orders/{order_id}
  poll   : GET    /portfolio/orders/{order_id}
  list   : GET    /portfolio/orders
Create acknowledgments carry NO status — parse_create_ack handles them;
parse_order (with required status) is for polling/list responses only.
"""
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN


class SchemaError(Exception):
    pass


class UnsupportedMarketType(SchemaError):
    """A recognized product Inci deliberately does not trade.

    Collection callers may skip this narrow exception. Unknown product
    types and malformed supported markets continue to raise SchemaError.
    """
    def __init__(self, ticker, market_type):
        super().__init__(
            f"market {ticker}: unsupported market_type {market_type!r}")
        self.ticker = ticker
        self.market_type = market_type


class UnknownOrderState(SchemaError):
    def __init__(self, status, order_id):
        super().__init__(f"unknown order status {status!r} for {order_id}")
        self.status = status
        self.order_id = order_id


KNOWN_ORDER_STATUSES = {"resting", "executed", "canceled"}
TERMINAL_ORDER_STATUSES = {"executed", "canceled"}
MARKET_STATUSES = {
    "initialized", "inactive", "active", "closed", "determined",
    "disputed", "amended", "finalized",
}
TIME_IN_FORCE_VALUES = {
    "fill_or_kill", "good_till_canceled", "immediate_or_cancel",
}
SELF_TRADE_PREVENTION_VALUES = {"taker_at_cross", "maker"}

CREATE_CANCEL_ENDPOINT = "/portfolio/events/orders"   # create/cancel ONLY
ORDERS_ENDPOINT = "/portfolio/orders"                 # poll/list ONLY


def _diagnostic(value, limit=240):
    """Bound raw response diagnostics so failures stay useful and printable."""
    rendered = repr(value)
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit - 3] + "..."


def _req(d, field, ctx):
    if not isinstance(d, dict):
        raise SchemaError(
            f"{ctx}: expected object, got {_diagnostic(d)}")
    if field not in d or d[field] is None:
        raise SchemaError(f"{ctx}: missing field '{field}'")
    return d[field]


def _dec(v, ctx):
    try:
        value = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise SchemaError(f"{ctx}: unparseable numeric value {v!r}")
    if not value.is_finite():
        raise SchemaError(f"{ctx}: non-finite numeric value {v!r}")
    return value


def _response_dec(v, ctx):
    """Parse a documented fixed-point response string, without coercion."""
    if (not isinstance(v, str) or not v
            or re.fullmatch(r"-?\d+(?:\.\d+)?", v) is None):
        raise SchemaError(f"{ctx}: expected fixed-point string, got {v!r}")
    return _dec(v, ctx)


def _fp_str(d):
    """Decimal -> fixed-point string without exponent notation."""
    return format(d.normalize(), "f")


def _decimal_places(d):
    return max(0, -d.as_tuple().exponent)


def _quantity(v, ctx, *, response=False):
    if response and (not isinstance(v, str)
                     or re.fullmatch(r"\d+\.\d{2}", v) is None):
        raise SchemaError(
            f"{ctx}: expected response quantity with two decimals, got {v!r}")
    d = (_response_dec(v, ctx) if response else _dec(v, ctx))
    if d < 0:
        raise SchemaError(f"{ctx}: negative quantity {v!r}")
    if _decimal_places(d) > 2:
        raise SchemaError(f"{ctx}: more than 2 decimal places: {v!r}")
    return d


def _dollars_to_cents(v, ctx, *, response=True):
    """Fixed-point dollar value -> Decimal cents."""
    if v is None:
        return None
    dollars = (_response_dec(v, ctx) if response else _dec(v, ctx))
    if _decimal_places(dollars) > 4:
        raise SchemaError(f"{ctx}: more than 4 price decimals: {v!r}")
    c = dollars * 100
    if not (Decimal(0) <= c <= Decimal(100)):
        raise SchemaError(f"{ctx}: dollar value out of range: {v!r}")
    return c


def _dollar_amount(v, ctx, max_places=6):
    dollars = _response_dec(v, ctx)
    if dollars < 0 or _decimal_places(dollars) > max_places:
        raise SchemaError(f"{ctx}: invalid dollar amount {v!r}")
    return dollars


def _portfolio_dollars_to_cents(v, ctx):
    if v is None:
        return None
    dollars = _dollar_amount(v, ctx, max_places=6)
    cents = dollars * 100
    if cents > Decimal(100):
        raise SchemaError(f"{ctx}: price out of range {v!r}")
    return cents


def _size(v, ctx):
    """Depth/size (*_fp). Fractional preserved; negative -> SchemaError."""
    if v is None:
        return None
    return _quantity(v, ctx, response=True)


def _nonnegative_int(v, ctx):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise SchemaError(f"{ctx}: expected non-negative integer, got {v!r}")
    return v


def _subaccount(v, ctx):
    if (isinstance(v, bool) or not isinstance(v, int)
            or not 0 <= v <= 32):
        raise SchemaError(f"{ctx}: expected integer 0..32, got {v!r}")
    return v


def _text(v, ctx, *, nonempty=True):
    if not isinstance(v, str) or (nonempty and not v):
        raise SchemaError(f"{ctx}: expected nonempty string, got {v!r}")
    return v


def _iso_timestamp(v, ctx):
    text = _text(v, ctx)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise SchemaError(f"{ctx}: invalid ISO-8601 timestamp {v!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SchemaError(
            f"{ctx}: timestamp must include timezone, got {v!r}")
    return parsed.astimezone(timezone.utc).timestamp()


def parse_market(m):
    ticker = _text(_req(m, "ticker", "market"), "market.ticker")
    event_ticker = _text(_req(m, "event_ticker", "market"),
                         "market.event_ticker")
    market_type = _text(_req(m, "market_type", "market"),
                        "market.market_type")
    if market_type not in {"binary", "scalar"}:
        raise SchemaError(
            f"market {ticker}: unknown market_type {market_type!r}")
    if market_type == "scalar":
        raise UnsupportedMarketType(ticker, market_type)
    raw_mve_collection = m.get("mve_collection_ticker")
    if (raw_mve_collection is not None
            and not isinstance(raw_mve_collection, str)):
        raise SchemaError(
            "market.mve_collection_ticker: expected string or null, got "
            f"{raw_mve_collection!r}")
    if (raw_mve_collection
            or ticker.startswith("KXMVE")
            or event_ticker.startswith("KXMVE")):
        raise UnsupportedMarketType(ticker, "mve")
    yes_sub_title = _text(_req(m, "yes_sub_title", "market"),
                          "market.yes_sub_title", nonempty=False)
    no_sub_title = _text(_req(m, "no_sub_title", "market"),
                         "market.no_sub_title", nonempty=False)
    raw_title = m.get("title")
    title = (yes_sub_title if raw_title is None
             else _text(raw_title, "market.title", nonempty=False))
    status = _text(_req(m, "status", "market"), "market.status")
    if status not in MARKET_STATUSES:
        raise SchemaError(f"market {ticker}: unknown status {status!r}")
    close_ts = _iso_timestamp(
        _req(m, "close_time", "market"), "market.close_time")
    can_close_early = _req(m, "can_close_early", "market")
    if not isinstance(can_close_early, bool):
        raise SchemaError(
            "market.can_close_early: expected boolean, got "
            f"{can_close_early!r}")
    raw_notional = _req(m, "notional_value_dollars", "market")
    notional = _dollar_amount(
        raw_notional,
        "market.notional_value_dollars", max_places=6)
    if notional != Decimal(1):
        raise SchemaError(
            f"market {ticker}: expected $1 binary notional, got "
            f"{raw_notional!r}")
    bid = _dollars_to_cents(
        _req(m, "yes_bid_dollars", "market"), "market.yes_bid")
    ask = _dollars_to_cents(
        _req(m, "yes_ask_dollars", "market"), "market.yes_ask")
    bid_size = _size(
        _req(m, "yes_bid_size_fp", "market"), "market.yes_bid_size")
    ask_size = _size(
        _req(m, "yes_ask_size_fp", "market"), "market.yes_ask_size")
    # Current Market responses use a zero depth for an absent top-of-book
    # side. A required numeric placeholder is not an executable quote.
    if bid_size == 0:
        bid = None
    if ask_size == 0:
        ask = None
    if bid is not None and ask is not None and bid > ask:
        raise SchemaError(
            f"market {ticker}: crossed YES book bid={bid}, ask={ask}")
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": title,
        "market_type": market_type,
        "yes_sub_title": yes_sub_title,
        "no_sub_title": no_sub_title,
        "notional_value": notional,
        "close_ts": close_ts,
        "can_close_early": can_close_early,
        "yes_bid": bid,
        "yes_ask": ask,
        "yes_bid_size": bid_size,
        "yes_ask_size": ask_size,
        "status": status,
    }


def parse_orderbook_response(response):
    """Parse GET /markets/{ticker}/orderbook current fixed-point wrapper."""
    book = _req(response, "orderbook_fp", "orderbook_response")
    if not isinstance(book, dict):
        raise SchemaError(
            f"orderbook_fp: expected object, got {_diagnostic(book)}")
    out = {}
    for side, field in (("yes", "yes_dollars"), ("no", "no_dollars")):
        levels = _req(book, field, "orderbook_fp")
        if not isinstance(levels, list):
            raise SchemaError(
                f"orderbook_fp.{field}: expected list, got "
                f"{_diagnostic(levels)}")
        parsed = []
        for level in levels:
            if not isinstance(level, (list, tuple)) or len(level) != 2:
                raise SchemaError(f"orderbook_fp.{field}: bad level {level!r}")
            price = _dollars_to_cents(level[0], f"orderbook_fp.{field}.price")
            quantity = _size(level[1], f"orderbook_fp.{field}.quantity")
            if price is None or quantity is None:
                raise SchemaError(
                    f"orderbook_fp.{field}: null price/quantity in "
                    f"{_diagnostic(level)}")
            parsed.append((price, quantity))
        out[side] = parsed
    return out


def parse_create_ack(a):
    """POST /portfolio/events/orders acknowledgment: has order_id but NO
    status. Status comes only from polling GET /portfolio/orders/{id}."""
    ts_ms = _req(a, "ts_ms", "create_ack")
    _nonnegative_int(ts_ms, "create_ack.ts_ms")
    return {"order_id": _text(_req(a, "order_id", "create_ack"),
                              "create_ack.order_id"),
            "client_order_id": (_text(a["client_order_id"],
                                       "create_ack.client_order_id")
                                if a.get("client_order_id") is not None
                                else None),
            "fill_count": _quantity(_req(a, "fill_count", "create_ack"),
                                    "create_ack.fill_count", response=True),
            "remaining_count": _quantity(
                _req(a, "remaining_count", "create_ack"),
                "create_ack.remaining_count", response=True),
            "ts_ms": ts_ms,
            "average_fill_price": (
                _portfolio_dollars_to_cents(
                    a.get("average_fill_price"),
                    "create_ack.average_fill_price")
                if a.get("average_fill_price") is not None else None),
            "average_fee_paid": (
                _dollar_amount(a["average_fee_paid"],
                               "create_ack.average_fee_paid", max_places=6)
                if a.get("average_fee_paid") is not None else None)}


def parse_order(o):
    """Polled/listed order object (GET /portfolio/orders...). Status is
    REQUIRED here; unknown status raises."""
    oid = _text(_req(o, "order_id", "order"), "order.order_id")
    status = _text(_req(o, "status", "order"), "order.status")
    if status not in KNOWN_ORDER_STATUSES:
        raise UnknownOrderState(status, oid)
    ticker = _text(_req(o, "ticker", "order"), "order.ticker")
    client_order_id = _text(
        _req(o, "client_order_id", "order"), "order.client_order_id")
    raw_subaccount = o.get("subaccount_number")
    subaccount = (_subaccount(raw_subaccount, "order.subaccount_number")
                  if raw_subaccount is not None else None)
    fill_count = _quantity(_req(o, "fill_count_fp", "order"),
                           "order.fill_count_fp", response=True)
    remaining_count = _quantity(_req(o, "remaining_count_fp", "order"),
                                "order.remaining_count_fp", response=True)
    initial_count = _quantity(_req(o, "initial_count_fp", "order"),
                              "order.initial_count_fp", response=True)
    return {"order_id": oid, "status": status,
            "fill_count": fill_count,
            "remaining_count": remaining_count,
            "initial_count": initial_count, "ticker": ticker,
            "subaccount_number": subaccount,
            "client_order_id": client_order_id}


def parse_fill(f):
    raw_count = _req(f, "count_fp", "fill")
    count = _size(raw_count, "fill.count")
    if count is None or count <= 0:
        raise SchemaError(f"fill: non-positive count {raw_count!r}")
    price = _portfolio_dollars_to_cents(
        _req(f, "yes_price_dollars", "fill"), "fill.price")
    raw_fee = _req(f, "fee_cost", "fill")
    fee = _response_dec(raw_fee, "fill.fee_cost")
    if fee < 0 or _decimal_places(fee) > 6:
        raise SchemaError(
            f"fill.fee_cost: invalid dollar amount {raw_fee!r}")
    raw_subaccount = f.get("subaccount_number")
    raw_ts = f.get("ts")
    return {"count": count, "yes_price": price,
            "order_id": _text(_req(f, "order_id", "fill"),
                              "fill.order_id"),
            "fill_id": _text(_req(f, "fill_id", "fill"), "fill.fill_id"),
            "ticker": _text(_req(f, "ticker", "fill"), "fill.ticker"),
            "subaccount_number": (
                _subaccount(raw_subaccount, "fill.subaccount_number")
                if raw_subaccount is not None else None),
            "ts": (_nonnegative_int(raw_ts, "fill.ts")
                   if raw_ts is not None else None),
            "fee": fee}


def parse_position(p):
    ticker = _text(_req(p, "ticker", "position"), "position.ticker")
    raw_position = _req(p, "position_fp", "position")
    if (not isinstance(raw_position, str)
            or re.fullmatch(r"-?\d+\.\d{2}", raw_position) is None):
        raise SchemaError(
            "position.position_fp: expected response quantity with two "
            f"decimals, got {raw_position!r}")
    pos = _response_dec(raw_position, "position.position_fp")
    if _decimal_places(pos) > 2:
        raise SchemaError(f"position.position_fp: more than 2 decimal "
                          f"places: {p['position_fp']!r}")
    return {"ticker": ticker, "position": pos}


def build_order_body(ticker, client_order_id, side, count, price_cents,
                     time_in_force, self_trade_prevention_type,
                     reduce_only=False, subaccount=0):
    """Official Create V2 body for POST /portfolio/events/orders:
      side  = 'bid' (buy) | 'ask' (sell)
      price = separate dollar string (subpenny preserved: 52.5c -> '0.525')
      count = fixed-point string
      time_in_force, self_trade_prevention_type = required strings
    No legacy keys: no side=yes, no action, no type, no bid/ask price keys.
    Unit-tested here; end-to-end verification on DEMO only."""
    if side not in ("bid", "ask"):
        raise SchemaError(f"order: side must be bid|ask, got {side!r}")
    cnt = _quantity(count, "order.count")
    if cnt <= 0:
        raise SchemaError(f"order: non-positive count {count!r}")
    dollars = _dec(price_cents, "order.price") / 100
    if not (Decimal("0") < dollars < Decimal("1")):
        raise SchemaError(f"order: price out of range {price_cents!r}c")
    if _decimal_places(dollars) > 4:
        raise SchemaError(f"order: price has more than 4 dollar decimals: "
                          f"{price_cents!r}c")
    if time_in_force not in TIME_IN_FORCE_VALUES:
        raise SchemaError(f"order: invalid time_in_force {time_in_force!r}")
    if self_trade_prevention_type not in SELF_TRADE_PREVENTION_VALUES:
        raise SchemaError("order: invalid self_trade_prevention_type "
                          f"{self_trade_prevention_type!r}")
    if not isinstance(reduce_only, bool):
        raise SchemaError(
            f"order: reduce_only must be boolean, got {reduce_only!r}")
    subaccount = _subaccount(subaccount, "order.subaccount")
    return {"ticker": ticker, "client_order_id": client_order_id,
            "side": side, "count": _fp_str(cnt), "price": _fp_str(dollars),
            "time_in_force": time_in_force,
            "self_trade_prevention_type": self_trade_prevention_type,
            "reduce_only": reduce_only, "subaccount": subaccount}


def parse_cancel_ack(a):
    return {
        "order_id": _req(a, "order_id", "cancel_ack"),
        "client_order_id": a.get("client_order_id"),
        "reduced_by": _quantity(_req(a, "reduced_by", "cancel_ack"),
                                "cancel_ack.reduced_by", response=True),
        "ts_ms": _nonnegative_int(_req(a, "ts_ms", "cancel_ack"),
                                  "cancel_ack.ts_ms"),
    }


def parse_market_response(response):
    return parse_market(_req(response, "market", "market_response"))


def parse_order_response(response):
    return parse_order(_req(response, "order", "order_response"))


def parse_exchange_status(response):
    exchange_active = _req(response, "exchange_active", "exchange_status")
    trading_active = _req(response, "trading_active", "exchange_status")
    if not isinstance(exchange_active, bool) or not isinstance(trading_active, bool):
        raise SchemaError(
            "exchange_status: active fields must be booleans, got "
            f"exchange_active={exchange_active!r}, "
            f"trading_active={trading_active!r}")
    return {"exchange_active": exchange_active,
            "trading_active": trading_active}


def parse_balance(response):
    raw_balance = _req(response, "balance", "balance")
    balance = _nonnegative_int(raw_balance,
                               "balance.balance")
    raw_dollars = _req(response, "balance_dollars", "balance")
    dollars = _dollar_amount(
        raw_dollars,
        "balance.balance_dollars")
    portfolio_value = _nonnegative_int(
        _req(response, "portfolio_value", "balance"),
        "balance.portfolio_value")
    updated_ts = _nonnegative_int(_req(response, "updated_ts", "balance"),
                                  "balance.updated_ts")
        expected_legacy_balance = int(
        (dollars * 100).to_integral_value(rounding=ROUND_DOWN)
    )
    if balance != expected_legacy_balance:
        raise SchemaError(
            "balance: legacy cents do not match truncated balance_dollars, "
            f"got balance={raw_balance!r}, "
            f"balance_dollars={raw_dollars!r}")
    return {"balance": balance, "balance_dollars": dollars,
            "portfolio_value": portfolio_value, "updated_ts": updated_ts}
