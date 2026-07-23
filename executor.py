"""Order execution.

Paper: nonblocking pending orders fill only from a same-ticker observation at
or after their due time, at ask/bid + slippage and bounded book depth.

Live/demo (disabled both at bot and executor levels): official Create V2 body
(side=bid|ask, separate price, fp string count, time_in_force,
self_trade_prevention_type). Create ack has no status; status comes from
polling GET /portfolio/orders/{id}. After a cancel, status is polled
UNTIL terminal; then fills AND authoritative positions are reconciled
BEFORE the success outcome is journaled. Any ambiguity halts.
"""
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

from fees import fee_usd, projected_scalp_pnl_usd
from schemas import TERMINAL_ORDER_STATUSES, build_order_body
from order_resolution import OrderIntent, OrderResolver, ResolutionError


class HaltError(Exception):
    """Execution reached a state that requires halt + reconciliation."""


class OrderExecutionDisabled(HaltError):
    """Real exchange mutation is disabled in this source build."""


REAL_ORDER_EXECUTION_ENABLED = False


@dataclass(frozen=True)
class PendingPaperOrder:
    ticker: str
    side: str
    contracts: Decimal
    due_at: float
    reason: str


class Executor:
    def __init__(self, config, client, feed, journal=None,
                 clock=time.time, sleep=time.sleep):
        self.cfg = config
        self.client = client
        self.feed = feed
        self.journal = journal
        self.clock = clock
        self.sleep = sleep
        self.pending_paper = []
        self.last_outcome_id = None
        self.last_observation = None

    def execute(self, ticker, side, contracts, expected_pre_position=None,
                max_entry_price=None):
        self.last_outcome_id = None
        self.last_observation = None
        if self.cfg.paper_trading:
            raise HaltError(
                "blocking paper execution is disabled; use submit_paper and "
                "process_due_paper_orders")
        if not self._real_orders_enabled():
            raise OrderExecutionDisabled(
                "real-order execution is disabled inside Executor")
        if expected_pre_position is None:
            raise HaltError("real-order execution requires an explicit "
                            "expected_pre_position")
        return self._live(ticker, side, contracts,
                          Decimal(str(expected_pre_position)),
                          max_entry_price=max_entry_price)

    def _real_orders_enabled(self):
        """Tests may override this method; production Executor stays locked."""
        return REAL_ORDER_EXECUTION_ENABLED

    # ---------- Paper ----------
    def _paper_fill(self, ticker, side, contracts):
        bid, bid_qty, ask, ask_qty = self.feed.top_of_book(ticker)
        if side == "BUY":
            if ask is None:
                return None
            price = min(Decimal(99), ask + self.cfg.sim_slippage_cents)
            avail = ask_qty if ask_qty is not None else Decimal(0)
        else:
            if bid is None:
                return None
            price = max(Decimal(0), bid - self.cfg.sim_slippage_cents)
            avail = bid_qty if bid_qty is not None else Decimal(0)
        filled = min(Decimal(str(contracts)), Decimal(str(avail)))
        if filled <= 0:
            return None
        fee = fee_usd(
            price, filled, side=side,
            balance_precision_usd=self.cfg.balance_precision_usd)
        tag = "" if filled == contracts else f" (PARTIAL {filled}/{contracts})"
        print(f"[PAPER] {side} {filled}x {ticker} @ {price}c fee {fee:.2f}{tag}")
        return price, filled, fee

    def submit_paper(self, ticker, side, contracts, reason="", now=None):
        if not self.cfg.paper_trading:
            raise HaltError("submit_paper called outside paper mode")
        if self.has_pending(ticker):
            return None
        submitted_at = self.clock() if now is None else now
        order = PendingPaperOrder(
            ticker=ticker, side=side,
            contracts=Decimal(str(contracts)),
            due_at=submitted_at + self.cfg.sim_latency_s,
            reason=reason)
        self.pending_paper.append(order)
        return order

    def has_pending(self, ticker=None):
        return any(ticker is None or o.ticker == ticker
                   for o in self.pending_paper)

    def pending_count(self, side=None):
        return sum(1 for order in self.pending_paper
                   if side is None or order.side == side)

    def process_due_paper_orders(self, now=None, ticker=None):
        now = self.clock() if now is None else now
        due = [o for o in self.pending_paper
               if o.due_at <= now and (ticker is None or o.ticker == ticker)]
        self.pending_paper = [o for o in self.pending_paper
                              if o not in due]
        return [(order, self._paper_fill(order.ticker, order.side,
                                         order.contracts))
                for order in due]

    def cancel_pending_paper(self):
        canceled = list(self.pending_paper)
        self.pending_paper.clear()
        return canceled

    # ---------- Live / demo (disabled at bot level in this build) ----------
    def _live(self, ticker, side, contracts, expected_pre_position,
              max_entry_price=None):
        if not self._real_orders_enabled():
            raise OrderExecutionDisabled(
                "real-order execution is disabled inside Executor")
        if side not in ("BUY", "SELL"):
            raise HaltError(f"unsupported order side {side!r}")
        if not hasattr(self.feed, "get_quote"):
            raise HaltError(
                f"cannot execute {ticker}: no fresh-quote interface")
        try:
            _, observed_bid, observed_ask, observed_at = \
                self.feed.get_quote(ticker)
        except Exception as e:
            raise HaltError(f"fresh quote failed for {ticker}: {e}") from e
        if observed_bid is None or observed_ask is None:
            raise HaltError(f"cannot execute {ticker}: fresh book is empty")
        self.last_observation = {
            "ticker": ticker, "bid": observed_bid, "ask": observed_ask,
            "observed_at": observed_at,
        }
        if side == "BUY":
            if max_entry_price is None:
                raise HaltError(
                    f"BUY {ticker} requires the signal's maximum entry price")
            try:
                max_entry_price = Decimal(str(max_entry_price))
            except Exception as error:
                raise HaltError("invalid maximum entry price") from error
            if (not max_entry_price.is_finite()
                    or not Decimal(0) < max_entry_price < Decimal(100)):
                raise HaltError("invalid maximum entry price")
            if observed_ask > max_entry_price:
                raise HaltError(
                    f"BUY requote rejected for {ticker}: ask {observed_ask} "
                    f"exceeds signal cap {max_entry_price}")
            if (hasattr(self.feed, "entry_allowed")
                    and not self.feed.entry_allowed(
                        ticker, observed_at,
                        self.cfg.max_hold_seconds
                        + self.cfg.close_buffer_seconds)):
                raise HaltError(
                    f"BUY requote rejected for {ticker}: too near close")
            if (hasattr(self.feed, "early_close_risk")
                    and self.feed.early_close_risk(ticker)):
                raise HaltError(
                    f"BUY requote rejected for {ticker}: market can close "
                    "early and no score/lifecycle guard is available")
            projected = projected_scalp_pnl_usd(
                observed_ask, self.cfg.take_profit,
                contracts,
                self.cfg.sim_slippage_cents,
                self.cfg.balance_precision_usd)
            if (not self.cfg.min_price <= observed_ask <= self.cfg.max_price
                    or observed_ask - observed_bid > self.cfg.max_spread
                    or projected <= 0):
                raise HaltError(
                    f"BUY requote rejected for {ticker}: bid={observed_bid}, "
                    f"ask={observed_ask}, projected={projected}")
        bid, _, ask, _ = self.feed.top_of_book(ticker)
        price = ask if side == "BUY" else bid
        if price is None:
            return None
        requested = Decimal(str(contracts))
        resolver = OrderResolver(self.client, self.cfg, self.clock, self.sleep)
        try:
            pre_position = resolver.position(ticker, "before submit")
        except ResolutionError as e:
            raise HaltError(str(e)) from e
        if pre_position != expected_pre_position:
            raise HaltError(f"pre-submit position mismatch for {ticker}: "
                            f"expected {expected_pre_position}, exchange "
                            f"reports {pre_position}")
        if side == "SELL" and (pre_position <= 0 or requested > pre_position):
            raise HaltError(f"refusing SELL {requested} for {ticker} from "
                            f"authoritative position {pre_position}")
        if side == "BUY" and pre_position < 0 and requested > -pre_position:
            raise HaltError(f"refusing BUY {requested} for {ticker}: it would "
                            f"cross negative position {pre_position}")
        cid = str(uuid.uuid4())
        reduce_only = side == "SELL" or pre_position < 0
        body = build_order_body(
            ticker, cid, "bid" if side == "BUY" else "ask",
            requested, price,
            self.cfg.time_in_force, self.cfg.self_trade_prevention_type,
            reduce_only=reduce_only, subaccount=self.cfg.subaccount)
        self.journal.record("submitted", client_order_id=cid, ticker=ticker,
                            side=side, count=str(requested),
                            price=str(price),
                            pre_position=str(pre_position))
        try:
            ack = self.client.create_order(body)      # ack: order_id, NO status
        except Exception as e:
            raise HaltError(f"order submit failed, outcome unknown "
                            f"(client_order_id={cid}): {e}")
        oid = ack["order_id"]
        self.journal.record("acked", client_order_id=cid, order_id=oid,
                            ack_fill=str(ack["fill_count"]),
                            ack_remaining=str(ack["remaining_count"]),
                            ack_client_order_id=ack.get("client_order_id"))
        if (ack.get("client_order_id") is not None
                and ack["client_order_id"] != cid):
            raise HaltError(f"create ack client_order_id mismatch for {oid}: "
                            f"expected {cid}, got {ack['client_order_id']}")
        if (ack["fill_count"] < 0 or ack["remaining_count"] < 0
                or ack["fill_count"] + ack["remaining_count"] > requested):
            raise HaltError(f"create ack quantities exceed requested "
                            f"count for {oid}: fill={ack['fill_count']} "
                            f"remaining={ack['remaining_count']} "
                            f"requested={requested}")
        intent = OrderIntent(oid, cid, ticker, side, requested, pre_position,
                             ack["fill_count"], ack["remaining_count"])

        # status ONLY via polling GET /portfolio/orders/{id}
        try:
            order = resolver.poll_terminal(
                oid, self.cfg.fill_timeout_s, cid)
            if order["status"] not in TERMINAL_ORDER_STATUSES:
                order = resolver.cancel_to_terminal(
                    oid, self.cfg.cancel_timeout_s, cid, initial=order)
            result = resolver.resolve(intent, initial=order)
        except ResolutionError as e:
            raise HaltError(str(e)) from e
        canceled = (result.order["initial_count"] - result.filled
                    - result.order["remaining_count"])
        self.journal.record("outcome", client_order_id=cid, order_id=oid,
                            ticker=ticker, side=side,
                            status=result.order["status"],
                            filled=str(result.filled),
                            remaining=str(result.order["remaining_count"]),
                            canceled=str(canceled),
                            avg_price=str(result.avg_price),
                            fee=str(result.fee),
                            api_position=str(result.post_position),
                            effective_ts=ack["ts_ms"] / 1000)
        self.last_outcome_id = oid
        if result.filled == 0:
            print(f"[LIVE] {side} {ticker}: no fill "
                  f"({result.order['status']})")
            return None
        tag = ("" if result.filled == requested
               else f" (PARTIAL {result.filled}/{requested})")
        print(f"[LIVE] {side} {result.filled}x {ticker} @ "
              f"{result.avg_price}c fee {result.fee:.2f}{tag}")
        return result.avg_price, result.filled, result.fee
