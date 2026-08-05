"""The single per-tick decision path shared by bot.py (real time) and
replay.py (virtual time)."""
import time as _time
from decimal import Decimal

from executor import HaltError
from schemas import UnknownOrderState
from fees import fee_usd
from safety import ExposureError


class Context:
    def __init__(self, cfg, feed, strategy, executor, log, safety,
                 clock=_time.time):
        self.cfg = cfg
        self.feed = feed
        self.strategy = strategy
        self.executor = executor
        self.log = log
        self.safety = safety
        self.clock = clock
        self.latest_bid = {}
        self.bid_ts = {}          # ticker -> time of last usable bid
        self.entry_status = {}    # ticker -> stable lifecycle gate status


def set_entry_status(ctx, ticker, status, message=None):
    """Publish lifecycle gate state without repeating unchanged console text."""
    previous = ctx.entry_status.get(ticker)
    ctx.entry_status[ticker] = status
    if message is not None and previous != status:
        print(message)


def sync_execution_observation(ctx, ticker):
    """Move the executor's fresh requote into the risk mark immediately."""
    observation = getattr(ctx.executor, "last_observation", None)
    if not observation or observation.get("ticker") != ticker:
        return
    bid = observation.get("bid")
    observed_at = observation.get("observed_at")
    if bid is not None and observed_at is not None:
        ctx.latest_bid[ticker] = bid
        ctx.bid_ts[ticker] = observed_at


def process_tick(ctx, ticker, mid, bid, ask, observed_at=None):
    # One timestamp governs the complete causal processing of this quote.
    # In real time it is captured by PriceFeed; replay supplies the CSV row
    # timestamp.  This prevents processing delay from making an old quote
    # eligible for a latency-delayed paper fill.
    now = ctx.clock() if observed_at is None else observed_at
    if ctx.log:
        _, bq, _, aq = ctx.feed.top_of_book(ticker)
        close_ts, can_close_early = (
            ctx.feed.lifecycle(ticker)
            if hasattr(ctx.feed, "lifecycle") else (None, None))
        if mid is None:
            ctx.log.event(ticker, "no_quote", ts=now)
        else:
            ctx.log.tick(
                ticker, mid, bid, ask, bq, aq, ts=now,
                close_ts=close_ts, can_close_early=can_close_early)
    if bid is not None:
        ctx.latest_bid[ticker] = bid
        ctx.bid_ts[ticker] = now

    fresh_quote = mid is not None and bid is not None and ask is not None
    if ctx.cfg.paper_trading and fresh_quote:
        for pending, result in ctx.executor.process_due_paper_orders(
                now, ticker=ticker):
            if result:
                ctx.strategy.record_fill(
                    pending.ticker, pending.side, *result, now=now)
                if ctx.log:
                    ctx.log.trade(pending.ticker, pending.side,
                                  result[0], result[1], pending.reason,
                                  fee=result[2], ts=now)

    # Revalue all exposure after each observation (and any due fill), before
    # another exit/entry can be scheduled.  This gives runtime and replay the
    # same loss-check cadence and stops a breached account in this ticker,
    # rather than after the rest of a multi-market sweep.
    check_loss_limit(ctx)
    if ctx.safety.tripped:
        return
    # A pending resting BUY (awaiting entry) blocks further action on this
    # ticker. A working resting SELL does NOT block the exit re-check, so a
    # stop-loss can still preempt an unfilled take-profit.
    if ctx.cfg.paper_trading and ctx.executor.has_pending(ticker, side="BUY"):
        return

    exit_sig = ctx.strategy.check_exit(ticker, bid, now=now)
    if exit_sig:
        pos = ctx.strategy.positions[ticker]
        if ctx.cfg.paper_trading:
            is_stop = "stop-loss" in exit_sig["reason"]
            working_sell = ctx.executor.has_pending(ticker, side="SELL")
            if is_stop:
                # Stop-loss crosses immediately; replace any working maker
                # exit so risk is not left resting.
                print(f"[signal] SELL {ticker}: {exit_sig['reason']}")
                if working_sell:
                    ctx.executor.cancel_pending_paper(
                        ticker=ticker, side="SELL")
                ctx.executor.submit_paper(
                    ticker, "SELL", pos.contracts, exit_sig["reason"],
                    now=now, resting=False)
            elif not working_sell:
                # Rest a maker exit at the offer; it works until it fills or a
                # stop-loss preempts it.
                print(f"[signal] SELL {ticker}: {exit_sig['reason']}")
                ctx.executor.submit_paper(
                    ticker, "SELL", pos.contracts, exit_sig["reason"],
                    now=now, resting=True)
            return
        print(f"[signal] SELL {ticker}: {exit_sig['reason']}")
        result = ctx.executor.execute(ticker, "SELL", pos.contracts,
                                      expected_pre_position=pos.contracts)
        sync_execution_observation(ctx, ticker)
        if result:
            outcome_id = getattr(ctx.executor, "last_outcome_id", None)
            ctx.strategy.record_fill(ticker, "SELL", *result, now=now,
                                     event_id=outcome_id)
            journal = getattr(ctx.executor, "journal", None)
            if journal and outcome_id:
                journal.record("applied", order_id=outcome_id)
            if ctx.log:
                ctx.log.trade(ticker, "SELL", result[0], result[1],
                              exit_sig["reason"], fee=result[2],
                              ts=now)
        check_loss_limit(ctx)
        return

    # Holding a position with a working resting exit: nothing to enter here.
    if ctx.cfg.paper_trading and ctx.executor.has_pending(ticker, side="SELL"):
        return

    hist = list(ctx.feed.history[ticker])[:-1]
    if (ctx.cfg.paper_trading
            and len(ctx.strategy.positions)
            + ctx.executor.pending_count("BUY")
            >= ctx.cfg.max_open_positions):
        return
    if (hasattr(ctx.feed, "entry_allowed")
            and not ctx.feed.entry_allowed(
                ticker, now,
                ctx.cfg.max_hold_seconds + ctx.cfg.close_buffer_seconds)):
        set_entry_status(
            ctx, ticker, "blocked:close_horizon",
            f"[entry] BLOCKED {ticker}: insufficient close horizon")
        return
    early_close_risk = (
        hasattr(ctx.feed, "early_close_risk")
        and ctx.feed.early_close_risk(ticker))
    if early_close_risk:
        if not ctx.cfg.paper_trading:
            set_entry_status(
                ctx, ticker, "blocked:can_close_early",
                f"[entry] BLOCKED {ticker}: can_close_early=true "
                "outside paper mode")
            return
        set_entry_status(
            ctx, ticker, "paper_allowed:can_close_early",
            f"[entry] PAPER-ONLY {ticker}: can_close_early=true; "
            "entry remains enabled")
    else:
        set_entry_status(ctx, ticker, "eligible")
    entry_sig = ctx.strategy.check_entry(ticker, hist, now,
                                         mid, bid, ask)
    if entry_sig:
        print(f"[signal] BUY {ticker}: {entry_sig['reason']}")
        if ctx.cfg.paper_trading:
            ctx.executor.submit_paper(
                ticker, "BUY", ctx.cfg.contracts_per_trade,
                entry_sig["reason"], now=now)
            return
        result = ctx.executor.execute(
            ticker, "BUY", ctx.cfg.contracts_per_trade,
            expected_pre_position=Decimal(0), max_entry_price=ask)
        sync_execution_observation(ctx, ticker)
        if result:
            outcome_id = getattr(ctx.executor, "last_outcome_id", None)
            ctx.strategy.record_fill(ticker, "BUY", *result, now=now,
                                     event_id=outcome_id)
            journal = getattr(ctx.executor, "journal", None)
            if journal and outcome_id:
                journal.record("applied", order_id=outcome_id)
            if ctx.log:
                ctx.log.trade(ticker, "BUY", result[0], result[1],
                              entry_sig["reason"], fee=result[2],
                              ts=now)
        check_loss_limit(ctx)


def open_pnl_usd(ctx):
    """Conservative executable-depth mark, including unpriced inventory."""
    total = Decimal(0)
    for t, pos in ctx.strategy.positions.items():
        bid = ctx.latest_bid.get(t)
        if bid is not None:
            _, bid_qty, _, _ = (ctx.feed.top_of_book(t)
                                if ctx.feed is not None
                                else (bid, None, None, None))
            contracts = Decimal(str(pos.contracts))
            available = (Decimal(0) if bid_qty is None
                         else max(Decimal(0), Decimal(str(bid_qty))))
            executable = min(contracts, available)
            exit_price = max(
                Decimal(0), Decimal(str(bid))
                - Decimal(str(ctx.cfg.sim_slippage_cents)))
            proceeds = exit_price * executable / Decimal(100)
            exit_fee = (fee_usd(
                exit_price, executable, side="SELL",
                balance_precision_usd=ctx.cfg.balance_precision_usd)
                        if executable else Decimal(0))
            cost = (Decimal(str(pos.entry_price)) * contracts / Decimal(100)
                    + Decimal(str(pos.entry_fee_usd)))
            total += proceeds - exit_fee - cost
    return total


def check_loss_limit(ctx):
    """Missing-bid risk: an open position whose market has no FRESH bid
    cannot be valued — that is itself a halt condition, not a free pass."""
    now = ctx.clock()
    ctx.strategy.refresh_daily_pnl(now)
    for t in ctx.strategy.positions:
        ts = ctx.bid_ts.get(t)
        if ts is None or now - ts > ctx.cfg.stale_data_s:
            ctx.safety.trip(f"cannot value open position {t}: "
                            f"no fresh bid (> {ctx.cfg.stale_data_s}s)")
            return
    total = ctx.strategy.realized_pnl + open_pnl_usd(ctx)
    if total <= -Decimal(str(ctx.cfg.max_daily_loss_usd)):
        ctx.safety.trip(f"loss limit incl. open positions "
                        f"({total:+.2f} USD)")


def flatten_all(ctx):
    """Close every open position, with retries for partial fills.
    Live: refuses if the journal holds unresolved (ambiguous) orders —
    flattening on top of an ambiguous SELL risks a duplicate SELL — and
    sizes each close from AUTHORITATIVE exchange positions."""
    ex = ctx.executor
    live = not ctx.cfg.paper_trading
    if live and ex.journal and ex.journal.unresolved():
        raise ExposureError(
            "unresolved orders in journal; auto-flatten could duplicate an "
            "in-flight order")
    if live and ex.journal and ex.journal.unapplied_outcomes():
        raise ExposureError(
            "unapplied filled outcome; local position/P&L state is unsafe")

    if live:
        def authoritative_positions():
            try:
                rows = ex.client.get_positions()
            except Exception as e:
                raise ExposureError(
                    f"authoritative positions unavailable: {e}") from e
            result = {}
            for row in rows:
                if row["ticker"] in result:
                    raise ExposureError(
                        f"duplicate position rows for {row['ticker']}")
                if row["position"]:
                    result[row["ticker"]] = row["position"]
            return result

        for _ in range(ctx.cfg.flatten_retries):
            api_pos = authoritative_positions()
            if not api_pos:
                if ctx.strategy.positions:
                    raise ExposureError(
                        "exchange is flat but local position book is not; "
                        "P&L state requires manual reconciliation")
                return
            local_pos = {
                ticker: Decimal(str(position.contracts))
                for ticker, position in ctx.strategy.positions.items()
            }
            if api_pos != local_pos or any(qty <= 0 for qty in api_pos.values()):
                raise ExposureError(
                    "authoritative exposure lacks an exact local position "
                    "and cost basis; manual reconciliation required")
            for ticker, authoritative in api_pos.items():
                side = "SELL"
                qty = authoritative
                try:
                    result = ex.execute(
                        ticker, side, qty,
                        expected_pre_position=authoritative)
                except (HaltError, UnknownOrderState) as e:
                    raise ExposureError(
                        f"flatten failed for {ticker}: {e}") from e
                if result and ticker in ctx.strategy.positions:
                    outcome_id = getattr(ex, "last_outcome_id", None)
                    ctx.strategy.record_fill(ticker, "SELL", *result,
                                             now=ctx.clock(),
                                             event_id=outcome_id)
                    journal = getattr(ex, "journal", None)
                    if journal and outcome_id:
                        journal.record("applied", order_id=outcome_id)
        residual = authoritative_positions()
        if residual:
            raise ExposureError(
                "account not flat after bounded retries: "
                + ", ".join(f"{t}={q}" for t, q in residual.items()))
        if ctx.strategy.positions:
            raise ExposureError(
                "exchange is flat but local position book is not")
        return

    # A paper position cannot be honestly flattened without a newly observed
    # quote. Reusing the last book would consume the same displayed depth
    # repeatedly and fabricate fills.
    if hasattr(ex, "cancel_pending_paper"):
        ex.cancel_pending_paper()
    if ctx.strategy.positions:
        raise ExposureError(
            "paper positions require future quotes; retained as residual")
