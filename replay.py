"""Replay logged ticks through the paper decision/pending-fill path used by
the running bot. Logged observations supply an immutable virtual timestamp;
no blocking sleep or parallel fill simulator exists."""
import csv
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal

from config import Config
from strategy import ScalpStrategy
from executor import Executor
from engine import Context, process_tick
from safety import Safety
from fees import fee_usd
from research_log import config_fingerprint, code_fingerprint


class VirtualClock:
    def __init__(self, t0=0.0):
        self.t = t0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class ReplayFeed:
    """Only exposes books that the replay driver has already applied."""
    def __init__(self, clock):
        self.clock = clock
        self.history = defaultdict(lambda: deque(maxlen=600))
        self.books = {}
        self.close_times = {}
        self.can_close_early = {}

    def apply(self, ts, ticker, mid, bid, ask, bid_qty, ask_qty,
              close_ts=None, can_close_early=None):
        self.books[ticker] = (bid, bid_qty, ask, ask_qty)
        self.history[ticker].append((ts, mid))
        if close_ts is not None:
            self.close_times[ticker] = close_ts
        if can_close_early is not None:
            self.can_close_early[ticker] = can_close_early

    def top_of_book(self, ticker):
        return self.books.get(ticker, (None, None, None, None))

    def lifecycle(self, ticker):
        return (self.close_times.get(ticker),
                self.can_close_early.get(ticker))

    def entry_allowed(self, ticker, now, required_seconds):
        # Pre-v5 logs contain no lifecycle facts and remain mechanically
        # replayable for diagnostics only. V5 rows always populate this.
        close_ts = self.close_times.get(ticker)
        return (True if close_ts is None else
                now + float(required_seconds) < close_ts)

    def early_close_risk(self, ticker):
        return self.can_close_early.get(ticker, False)


def validate_logged_book(mid, bid, ask, bid_qty=None, ask_qty=None):
    """Validate one normalized executable book before research can use it."""
    prices = {"mid": mid, "bid": bid, "ask": ask}
    for name, value in prices.items():
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
            prices[name] = value
        if not value.is_finite() or not Decimal(0) <= value <= Decimal(100):
            raise ValueError(f"invalid {name}: {value}")
    mid, bid, ask = prices["mid"], prices["bid"], prices["ask"]
    if bid > ask:
        raise ValueError(f"crossed book: bid {bid} > ask {ask}")
    if mid != (bid + ask) / Decimal(2):
        raise ValueError(
            f"mid {mid} does not equal arithmetic book midpoint")
    depths = []
    for name, value in (("bid_qty", bid_qty), ("ask_qty", ask_qty)):
        if value is None:
            depths.append(None)
            continue
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        if not parsed.is_finite() or parsed < 0:
            raise ValueError(f"invalid {name}: {parsed}")
        depths.append(parsed)
    return mid, bid, ask, depths[0], depths[1]


def _utc_day(timestamp):
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"invalid timestamp: {timestamp}") from error


def load_log(path, tickers=None, include_metadata=False,
             expected_config_fingerprint=None,
             expected_code_fingerprint=None):
    rows = []
    data_gaps = 0
    session_id = None
    starting_pnl = None
    starting_day = None
    logged_config_fingerprint = None
    logged_code_fingerprint = None
    terminal_status = "missing"
    terminal_reason = None
    terminal_seen = False
    saw_v5 = False
    last_v5_timestamp = None
    last_observation_timestamp = None
    selected = None if tickers is None else frozenset(tickers)
    with open(path) as f:
        for r in csv.DictReader(f):
            is_v5 = r.get("schema_version") == "5"
            row_timestamp = None
            if is_v5:
                saw_v5 = True
                row_session = r.get("session_id")
                row_start = r.get("starting_daily_pnl_usd")
                row_start_day = r.get("starting_utc_day")
                row_day = r.get("utc_day")
                row_config_fingerprint = r.get("config_fingerprint")
                row_code_fingerprint = r.get("code_fingerprint")
                if (not row_session or row_start in (None, "")
                        or not row_start_day or not row_day
                        or not row_config_fingerprint
                        or not row_code_fingerprint):
                    if selected is None or r.get("ticker") in selected:
                        data_gaps += 1
                    continue
                try:
                    parsed_start = Decimal(row_start)
                    row_timestamp = float(r.get("ts", ""))
                except Exception as error:
                    raise ValueError("invalid v5 session metadata") from error
                if (not parsed_start.is_finite()
                        or not math.isfinite(row_timestamp)
                        or row_timestamp < 0):
                    raise ValueError("non-finite/negative v5 session metadata")
                if (last_v5_timestamp is not None
                        and row_timestamp < last_v5_timestamp):
                    raise ValueError(
                        "non-monotonic v5 observation timestamps")
                last_v5_timestamp = row_timestamp
                if terminal_seen:
                    raise ValueError("row appears after session terminal record")
                actual_day = _utc_day(row_timestamp)
                if row_day != actual_day:
                    raise ValueError(
                        f"utc_day {row_day!r} disagrees with timestamp day "
                        f"{actual_day!r}")
                try:
                    datetime.strptime(row_start_day, "%Y-%m-%d")
                except ValueError as error:
                    raise ValueError(
                        f"invalid starting_utc_day {row_start_day!r}") from error
                if row_day < row_start_day:
                    raise ValueError("quote precedes process-session start day")
                if session_id is None:
                    session_id = row_session
                    starting_pnl = parsed_start
                    starting_day = row_start_day
                    logged_config_fingerprint = row_config_fingerprint
                    logged_code_fingerprint = row_code_fingerprint
                elif (row_session != session_id
                      or parsed_start != starting_pnl
                      or row_start_day != starting_day
                      or row_config_fingerprint != logged_config_fingerprint
                      or row_code_fingerprint != logged_code_fingerprint):
                    raise ValueError(
                        "replay refuses mixed/inconsistent sessions")
                if (expected_config_fingerprint is not None
                        and row_config_fingerprint
                        != expected_config_fingerprint):
                    raise ValueError("research config fingerprint mismatch")
                if (expected_code_fingerprint is not None
                        and row_code_fingerprint != expected_code_fingerprint):
                    raise ValueError("research code fingerprint mismatch")
                event = r.get("event") or "quote"
                if event in ("session_end", "session_halt"):
                    if terminal_seen:
                        raise ValueError("multiple session terminal records")
                    if r.get("ticker") or r.get("event_id"):
                        raise ValueError(
                            "session terminal record must not name a market")
                    terminal_seen = True
                    terminal_status = (
                        "clean" if event == "session_end" else "halted")
                    terminal_reason = r.get("detail") or ""
                    continue
            if selected is not None and r.get("ticker") not in selected:
                continue
            if not is_v5:
                # Legacy/v3/v4 rows remain mechanically replayable for
                # diagnostics, but never qualify as evidence.
                data_gaps += 1
            if (r.get("event") or "quote") != "quote":
                data_gaps += 1
                continue
            ticker = r.get("ticker")
            if not ticker:
                raise ValueError("quote row lacks ticker")
            if is_v5 and not r.get("event_id"):
                data_gaps += 1
                continue
            def q(field):
                v = r.get(field)
                if v in (None, ""):
                    return None
                try:
                    return Decimal(v)
                except Exception as error:
                    raise ValueError(f"invalid {field}: {v!r}") from error
            if any(r.get(field) in (None, "")
                   for field in ("mid", "bid", "ask")):
                data_gaps += 1
                continue
            if any(r.get(field) in (None, "")
                   for field in ("bid_qty", "ask_qty")):
                data_gaps += 1
                if is_v5:
                    continue
            close_ts = None
            can_close_early = None
            if is_v5:
                if (r.get("close_ts") in (None, "")
                        or r.get("can_close_early") not in ("true", "false")):
                    data_gaps += 1
                    continue
                try:
                    close_ts = float(r["close_ts"])
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError("invalid close_ts") from error
                if (not math.isfinite(close_ts) or close_ts < 0):
                    raise ValueError("invalid close_ts")
                can_close_early = r["can_close_early"] == "true"
            try:
                timestamp = (row_timestamp if row_timestamp is not None
                             else float(r["ts"]))
            except Exception as error:
                raise ValueError(f"invalid timestamp {r.get('ts')!r}") from error
            if not math.isfinite(timestamp) or timestamp < 0:
                raise ValueError(f"invalid timestamp {timestamp!r}")
            if (last_observation_timestamp is not None
                    and timestamp < last_observation_timestamp):
                raise ValueError("non-monotonic observation timestamps")
            last_observation_timestamp = timestamp
            parsed = validate_logged_book(
                q("mid"), q("bid"), q("ask"),
                q("bid_qty"), q("ask_qty"))
            rows.append((timestamp, ticker, *parsed,
                         close_ts, can_close_early))
    if saw_v5 and not terminal_seen:
        data_gaps += 1
    result = (rows, data_gaps)
    if include_metadata:
        return result + ((starting_pnl if starting_pnl is not None
                          else Decimal(0)), starting_day,
                         terminal_status, terminal_reason)
    return result


def load_rows(path):
    return load_log(path)[0]


def value_residual(position, bid, bid_qty, cfg):
    """Conservative liquidation value for replay-only residual inventory."""
    contracts = Decimal(str(position.contracts))
    available = (Decimal(0) if bid_qty is None
                 else max(Decimal(0), Decimal(str(bid_qty))))
    executable = min(contracts, available) if bid is not None else Decimal(0)
    exit_price = (max(Decimal(0), Decimal(str(bid))
                      - Decimal(str(cfg.sim_slippage_cents)))
                  if bid is not None else Decimal(0))
    proceeds = exit_price * executable / Decimal(100)
    exit_fee = (fee_usd(
        exit_price, executable, side="SELL",
        balance_precision_usd=cfg.balance_precision_usd)
        if executable else Decimal(0))
    cost = (Decimal(str(position.entry_price)) * contracts / Decimal(100)
            + Decimal(str(position.entry_fee_usd)))
    return {
        "contracts": contracts,
        "executable_contracts": executable,
        "unpriced_contracts": contracts - executable,
        "exit_price": exit_price,
        "marked_pnl": proceeds - exit_fee - cost,
    }


def replay(path, tickers=None, cfg=None, verbose=False):
    """Run the paper bot over a tick log through the exact engine path.
    Returns a dict: realized (Decimal), residual_contracts, residual_marked
    (mark-to-last-bid net of fees for anything flatten could not close —
    counted so reported P&L cannot hide open inventory), trades."""
    cfg = cfg or Config()
    cfg.validate()
    cfg.paper_trading = True
    clock = VirtualClock()
    feed = ReplayFeed(clock)
    strat = ScalpStrategy(cfg)
    ex = Executor(cfg, client=None, feed=feed,
                  clock=clock.time, sleep=clock.sleep)
    safety = Safety(cfg)
    ctx = Context(cfg, feed, strat, ex, log=None, safety=safety,
                  clock=clock.time)

    trades = []
    per_ticker_realized = defaultdict(lambda: Decimal(0))
    orig = strat.record_fill

    def counting(ticker, side, price, filled, fee, now=None):
        trades.append((ticker, side, price, filled))
        before = strat.realized_pnl
        orig(ticker, side, price, filled, fee, now=now)
        per_ticker_realized[ticker] += strat.realized_pnl - before
    strat.record_fill = counting

    import io, contextlib
    sink = None if verbose else io.StringIO()
    all_rows, data_gaps, starting_pnl, starting_day, terminal_status, \
        terminal_reason = load_log(
            path, tickers=tickers, include_metadata=True,
            expected_config_fingerprint=config_fingerprint(cfg),
            expected_code_fingerprint=code_fingerprint())
    strat.realized_pnl = starting_pnl
    starting_realized = starting_pnl
    current_day = starting_day
    processed_count = 0
    session_realized = Decimal(0)
    with contextlib.redirect_stdout(sink) if sink else contextlib.nullcontext():
        for ts, ticker, mid, bid, ask, bq, aq, close_ts, \
                can_close_early in all_rows:
            row_day = _utc_day(ts)
            if current_day is None:
                current_day = row_day
            elif row_day != current_day:
                # Runtime's durable ledger resets the risk counter at UTC
                # midnight. Replay mirrors that reset before the first quote
                # of the new day while separately accumulating session P&L.
                strat.realized_pnl = Decimal(0)
                current_day = row_day
            clock.t = max(clock.t, ts)
            feed.apply(ts, ticker, mid, bid, ask, bq, aq,
                       close_ts, can_close_early)
            before = strat.realized_pnl
            process_tick(ctx, ticker, mid, bid, ask, observed_at=ts)
            session_realized += strat.realized_pnl - before
            processed_count += 1
            if safety.tripped:
                break
        pending_count = len(ex.pending_paper)
        # Never synthesize a post-EOF liquidation from the last cached book.
        # Open inventory is valued conservatively below and makes the replay
        # incomplete.
        # residual inventory: flatten retries may still leave contracts
        # (zero depth). Mark them at the last bid net of fees so the
        # reported result cannot silently exclude open inventory.
        residual_contracts = Decimal(0)
        residual_marked = Decimal(0)
        residuals = {}
        for t, pos in strat.positions.items():
            bid, bid_qty, _, _ = feed.top_of_book(t)
            detail = value_residual(pos, bid, bid_qty, cfg)
            residuals[t] = detail
            residual_contracts += detail["contracts"]
            residual_marked += detail["marked_pnl"]
    total_pnl = session_realized + residual_marked
    per_ticker_total = dict(per_ticker_realized)
    for ticker, detail in residuals.items():
        per_ticker_total[ticker] = (per_ticker_total.get(ticker, Decimal(0))
                                    + detail["marked_pnl"])
    return {"realized": session_realized,
            "starting_daily_pnl": starting_realized,
            "ending_daily_pnl": strat.realized_pnl,
            "residual_contracts": residual_contracts,
            "residual_marked": residual_marked,
            "residuals": residuals,
            "total_pnl": total_pnl,
            "pending_orders": pending_count,
            "rows_processed": processed_count,
            "rows_available": len(all_rows),
            "data_gaps": data_gaps,
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
            "halted": safety.tripped,
            "halt_reason": safety.tripped_reason,
            "evaluable": (len(all_rows) > 0 and not residual_contracts
                           and pending_count == 0 and data_gaps == 0
                           and terminal_status == "clean"
                           and not safety.tripped
                           and processed_count == len(all_rows)),
            "per_ticker_realized": dict(per_ticker_realized),
            "per_ticker_total": per_ticker_total,
            "trades": trades}
