"""Authoritative order-state convergence shared by execution and safety.

No caller may treat a cancel acknowledgement or a single terminal snapshot as
an outcome.  Resolution requires two identical, coherent observations across
the order, order-filtered fills, and the account position.
"""
import time
from dataclasses import dataclass
from decimal import Decimal

from schemas import TERMINAL_ORDER_STATUSES


class ResolutionError(Exception):
    """Exchange state could not be proven coherent within the deadline."""


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    client_order_id: str
    ticker: str
    side: str
    requested: Decimal
    pre_position: Decimal
    ack_fill: Decimal
    ack_remaining: Decimal


@dataclass(frozen=True)
class Resolution:
    order: dict
    avg_price: Decimal
    filled: Decimal
    fee: Decimal
    post_position: Decimal


class OrderResolver:
    def __init__(self, client, config, clock=time.time, sleep=time.sleep):
        self.client = client
        self.cfg = config
        self.clock = clock
        self.sleep = sleep

    def order(self, order_id, client_order_id=None):
        try:
            order = self.client.get_order(order_id)
        except Exception as e:
            raise ResolutionError(
                f"order status poll failed for {order_id}: {e}") from e
        if order.get("order_id") != order_id:
            raise ResolutionError(
                f"order identity mismatch: requested {order_id}, got "
                f"{order.get('order_id')!r}")
        actual_cid = order.get("client_order_id")
        if (client_order_id is not None and actual_cid is not None
                and actual_cid != client_order_id):
            raise ResolutionError(
                f"client_order_id mismatch for {order_id}: expected "
                f"{client_order_id}, got {actual_cid}")
        return order

    def position(self, ticker, context=""):
        try:
            positions = self.client.get_positions()
        except Exception as e:
            suffix = f" {context}" if context else ""
            raise ResolutionError(
                f"authoritative position fetch failed{suffix}: {e}") from e
        matches = [p["position"] for p in positions if p["ticker"] == ticker]
        if len(matches) > 1:
            raise ResolutionError(
                f"duplicate authoritative position rows for {ticker}")
        return matches[0] if matches else Decimal(0)

    def fills(self, order_id):
        try:
            rows = self.client.get_fills(order_id=order_id)
        except Exception as e:
            raise ResolutionError(
                f"fills lookup failed for {order_id}: {e}") from e
        filled = Decimal(0)
        notional = Decimal(0)
        fee = Decimal(0)
        for fill in rows:
            if fill.get("order_id") != order_id:
                raise ResolutionError(
                    f"fill returned for wrong order: expected {order_id}, "
                    f"got {fill.get('order_id')!r}")
            filled += fill["count"]
            notional += fill["yes_price"] * fill["count"]
            fee += fill["fee"]
        avg = notional / filled if filled else Decimal(0)
        return avg, filled, fee

    def poll_terminal(self, order_id, timeout_s, client_order_id=None,
                      initial=None):
        deadline = self.clock() + timeout_s
        order = initial or self.order(order_id, client_order_id)
        while order["status"] not in TERMINAL_ORDER_STATUSES:
            if self.clock() >= deadline:
                return order
            self.sleep(0.5)
            order = self.order(order_id, client_order_id)
        return order

    def cancel_to_terminal(self, order_id, timeout_s, client_order_id=None,
                           initial=None):
        order = initial or self.order(order_id, client_order_id)
        if order["status"] in TERMINAL_ORDER_STATUSES:
            return order
        cancel_error = None
        try:
            self.client.cancel_order(order_id)
        except Exception as e:
            # DELETE is not authoritative: a timeout can still mean the
            # exchange accepted the cancellation. Continue polling GET.
            cancel_error = e
        order = self.poll_terminal(order_id, timeout_s, client_order_id,
                                   initial=order)
        if order["status"] not in TERMINAL_ORDER_STATUSES:
            detail = f"; cancel error={cancel_error}" if cancel_error else ""
            raise ResolutionError(
                f"order {order_id} not terminal {timeout_s}s after cancel "
                f"(status={order['status']}){detail}")
        return order

    def resolve(self, intent, initial=None, timeout_s=None, stable_polls=2):
        timeout_s = (self.cfg.reconcile_timeout_s if timeout_s is None
                     else timeout_s)
        deadline = self.clock() + timeout_s
        order = initial or self.order(intent.order_id,
                                      intent.client_order_id)
        stable = 0
        prior = None
        last = None
        while True:
            avg, filled, fee = self.fills(intent.order_id)
            post = self.position(intent.ticker,
                                 f"after order {intent.order_id}")
            expected_post = (intent.pre_position + filled
                             if intent.side == "BUY"
                             else intent.pre_position - filled)
            counts_coherent = (
                order["initial_count"] == intent.requested
                and order["fill_count"] + order["remaining_count"]
                <= intent.requested
                and intent.ack_fill + intent.ack_remaining
                <= intent.requested
                and order["fill_count"] == intent.ack_fill
                and order["remaining_count"] == intent.ack_remaining
                and order["fill_count"] == filled
            )
            coherent = (
                order["status"] in TERMINAL_ORDER_STATUSES
                and order.get("ticker") == intent.ticker
                and counts_coherent
                and post == expected_post
            )
            snapshot = (order["status"], order.get("ticker"),
                        order["initial_count"],
                        order["fill_count"], order["remaining_count"],
                        filled, avg, fee, post)
            last = {
                "status": order["status"],
                "initial": order["initial_count"],
                "order_fill": order["fill_count"],
                "remaining": order["remaining_count"],
                "ack_fill": intent.ack_fill,
                "ack_remaining": intent.ack_remaining,
                "fills": filled,
                "pre_position": intent.pre_position,
                "post_position": post,
                "expected_position": expected_post,
            }
            if coherent:
                stable = stable + 1 if snapshot == prior else 1
                if stable >= stable_polls:
                    return Resolution(order, avg, filled, fee, post)
            else:
                stable = 0
            prior = snapshot
            if self.clock() >= deadline:
                raise ResolutionError(
                    f"order {intent.order_id} did not converge before "
                    f"reconciliation timeout: {last}")
            self.sleep(0.25)
            order = self.order(intent.order_id, intent.client_order_id)
