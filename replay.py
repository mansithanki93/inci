"""Replay logged ticks through the paper decision/pending-fill path used by
the running bot. Logged observations supply an immutable virtual timestamp;
no blocking sleep or parallel fill simulator exists."""
import csv
import json
import math
from copy import deepcopy
from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal

from config import Config
from strategy import ScalpStrategy
from executor import Executor
from engine import Context, process_tick
from safety import Safety
from fees import fee_usd
from research_log import (
    TICK_HEADER, config_fingerprint, code_fingerprint,
)
from sports_discovery import ContractProvenance


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


def _legacy_error(header, raw_rows):
    fingerprint = None
    if "code_fingerprint" in header:
        index = header.index("code_fingerprint")
        for row in raw_rows:
            if index < len(row) and row[index]:
                fingerprint = row[index]
                break
    if "event_id" in header or any(row and row[0] == "5" for row in raw_rows):
        suffix = (f"; logged code fingerprint: {fingerprint}"
                  if fingerprint else "")
        return ValueError(
            "strict v6 replay rejects this v5 log; use archived v5 code"
            + suffix)
    return ValueError(
        "strict v6 replay requires the exact v6 tick header; use archived "
        "code for legacy research logs")


def _selected_sports(raw):
    try:
        parsed = json.loads(raw)
    except Exception as error:
        raise ValueError("selected_sports must be compact JSON") from error
    if (not isinstance(parsed, list) or not parsed
            or any(not isinstance(sport, str) or not sport
                   for sport in parsed)
            or len(set(parsed)) != len(parsed)):
        raise ValueError(
            "selected_sports must be a unique nonempty-string JSON array")
    if json.dumps(parsed, separators=(",", ":")) != raw:
        raise ValueError(
            "selected_sports must use exact compact JSON serialization")
    return tuple(parsed)


def _decimal_field(row, field, *, minimum=None):
    raw = row[field]
    if raw == "":
        raise ValueError(f"{field} is required")
    try:
        parsed = Decimal(raw)
    except Exception as error:
        raise ValueError(f"invalid {field}: {raw!r}") from error
    if not parsed.is_finite() or (minimum is not None and parsed < minimum):
        raise ValueError(f"invalid {field}: {raw!r}")
    return parsed


def _timestamp_field(row, field):
    raw = row[field]
    try:
        parsed = float(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"invalid {field}: {raw!r}") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"invalid {field}: {raw!r}")
    _utc_day(parsed)
    return parsed


def load_log(path, tickers=None, include_metadata=False, cfg=None,
             expected_code_fingerprint=None):
    """Strictly validate a complete v6 file before applying ticker filters."""
    selected_filter = None
    if tickers is not None:
        if isinstance(tickers, (str, bytes)):
            raise ValueError("tickers filter must be a collection")
        selected_filter = frozenset(tickers)
        if any(not isinstance(ticker, str) or not ticker
               for ticker in selected_filter):
            raise ValueError(
                "tickers filter must contain nonempty strings")

    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("strict v6 replay requires a CSV header") from error
        raw_rows = list(reader)
    if header != TICK_HEADER:
        raise _legacy_error(header, raw_rows)
    for number, values in enumerate(raw_rows, start=2):
        if len(values) != len(TICK_HEADER):
            raise ValueError(
                f"v6 row {number} has {len(values)} columns; "
                f"expected {len(TICK_HEADER)}")
    if not raw_rows:
        raise ValueError(
            "strict v6 replay requires session metadata rows")

    normalized_rows = []
    gap_tickers = []
    provenance_by_ticker = {}
    provenance_by_event = {}
    terminal_status = "missing"
    terminal_reason = None
    terminal_seen = False
    session_id = None
    starting_pnl = None
    starting_day = None
    logged_config = None
    logged_code = None
    logged_selected_text = None
    logged_selected_sports = None
    last_timestamp = None
    seen_rows = set()

    for number, values in enumerate(raw_rows, start=2):
        row_identity = tuple(values)
        if row_identity in seen_rows:
            raise ValueError(f"duplicate v6 row at line {number}")
        seen_rows.add(row_identity)
        row = dict(zip(TICK_HEADER, values))
        if row["schema_version"] != "6":
            if row["schema_version"] == "5":
                raise _legacy_error(TICK_HEADER, [values])
            raise ValueError(
                f"schema_version mismatch on row {number}: "
                f"{row['schema_version']!r}; strict v6 requires '6'")
        if terminal_seen:
            raise ValueError("row appears after session terminal record")
        if not row["session_id"]:
            raise ValueError("session_id is required")
        parsed_start = _decimal_field(
            row, "starting_daily_pnl_usd")
        timestamp = _timestamp_field(row, "ts")
        if last_timestamp is not None and timestamp < last_timestamp:
            raise ValueError("non-monotonic observation timestamps")
        last_timestamp = timestamp
        if not row["starting_utc_day"]:
            raise ValueError("starting_utc_day is required")
        try:
            datetime.strptime(row["starting_utc_day"], "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(
                f"invalid starting_utc_day "
                f"{row['starting_utc_day']!r}") from error
        actual_day = _utc_day(timestamp)
        if row["utc_day"] != actual_day:
            raise ValueError(
                f"utc_day {row['utc_day']!r} disagrees with timestamp "
                f"day {actual_day!r}")
        if row["utc_day"] < row["starting_utc_day"]:
            raise ValueError("row precedes process-session start day")
        if not row["config_fingerprint"]:
            raise ValueError("config_fingerprint is required")
        if not row["code_fingerprint"]:
            raise ValueError("code_fingerprint is required")
        selected_sports = _selected_sports(row["selected_sports"])
        if session_id is None:
            session_id = row["session_id"]
            starting_pnl = parsed_start
            starting_day = row["starting_utc_day"]
            logged_config = row["config_fingerprint"]
            logged_code = row["code_fingerprint"]
            logged_selected_text = row["selected_sports"]
            logged_selected_sports = selected_sports
        elif (
                row["session_id"] != session_id
                or parsed_start != starting_pnl
                or row["starting_utc_day"] != starting_day
                or row["config_fingerprint"] != logged_config
                or row["code_fingerprint"] != logged_code):
            raise ValueError(
                "replay refuses mixed/inconsistent session metadata")
        elif row["selected_sports"] != logged_selected_text:
            raise ValueError(
                "selected_sports changed within one session")

        event = row["event"]
        if event in ("session_end", "session_halt"):
            if terminal_seen:
                raise ValueError("multiple session terminal records")
            forbidden = (
                "ticker", "sport", "league", "series_ticker",
                "milestone_id", "event_ticker", "scheduled_start_ts",
                "close_ts", "can_close_early", "mid", "bid", "ask",
                "bid_qty", "ask_qty",
            )
            if any(row[field] != "" for field in forbidden):
                raise ValueError(
                    "session terminal record must be fully unscoped")
            if not row["detail"].strip():
                raise ValueError(
                    "session terminal record lacks a reason")
            terminal_seen = True
            terminal_status = (
                "clean" if event == "session_end" else "halted")
            terminal_reason = row["detail"]
            continue

        ticker = row["ticker"]
        if not ticker:
            raise ValueError(
                "tickerless nonterminal row is forbidden")
        if not event:
            raise ValueError("event must be a nonempty string")
        for field in (
                "sport", "series_ticker", "milestone_id",
                "event_ticker", "scheduled_start_ts"):
            if row[field] == "":
                raise ValueError(f"{field} is required")
        if row["sport"] not in selected_sports:
            raise ValueError(
                f"provenance Sport {row['sport']!r} is not selected")
        scheduled_start = _timestamp_field(
            row, "scheduled_start_ts")
        try:
            provenance = ContractProvenance(
                sport=row["sport"],
                league=(None if row["league"] == "" else row["league"]),
                series_ticker=row["series_ticker"],
                milestone_id=row["milestone_id"],
                event_ticker=row["event_ticker"],
                scheduled_start_ts=scheduled_start)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"invalid provenance for {ticker!r}: {error}") from error
        prior = provenance_by_ticker.setdefault(ticker, provenance)
        if prior != provenance:
            raise ValueError(
                f"ticker {ticker!r} has drifting provenance")
        game_provenance = (
            provenance.sport, provenance.league,
            provenance.series_ticker, provenance.milestone_id,
            provenance.scheduled_start_ts,
        )
        prior_game = provenance_by_event.setdefault(
            provenance.event_ticker, game_provenance)
        if prior_game != game_provenance:
            raise ValueError(
                f"event {provenance.event_ticker!r} has conflicting "
                "game provenance")

        lifecycle_book_fields = (
            "close_ts", "can_close_early", "mid", "bid", "ask",
            "bid_qty", "ask_qty",
        )
        if event != "quote":
            if any(row[field] != "" for field in lifecycle_book_fields):
                raise ValueError(
                    "ticker-bearing nonquote row must have blank "
                    "lifecycle/book fields")
            gap_tickers.append(ticker)
            continue
        if any(row[field] == "" for field in lifecycle_book_fields):
            raise ValueError(
                "malformed quote row has missing lifecycle/book field")
        close_ts = _timestamp_field(row, "close_ts")
        if row["can_close_early"] not in ("true", "false"):
            raise ValueError(
                "can_close_early must be 'true' or 'false'")
        parsed = validate_logged_book(
            _decimal_field(row, "mid"),
            _decimal_field(row, "bid"),
            _decimal_field(row, "ask"),
            _decimal_field(row, "bid_qty", minimum=Decimal(0)),
            _decimal_field(row, "ask_qty", minimum=Decimal(0)))
        normalized_rows.append((
            timestamp, ticker, *parsed, close_ts,
            row["can_close_early"] == "true"))

    base_config = deepcopy(cfg if cfg is not None else Config())
    base_config.sports = list(logged_selected_sports)
    base_config.validate()
    expected_config = config_fingerprint(base_config)
    expected_code = (
        expected_code_fingerprint
        if expected_code_fingerprint is not None
        else code_fingerprint())
    if logged_config != expected_config:
        raise ValueError("research config fingerprint mismatch")
    if logged_code != expected_code:
        raise ValueError("research code fingerprint mismatch")

    if selected_filter is None:
        rows = normalized_rows
        gaps = len(gap_tickers)
        selected_provenance = dict(provenance_by_ticker)
    else:
        rows = [
            row for row in normalized_rows if row[1] in selected_filter]
        gaps = sum(
            ticker in selected_filter for ticker in gap_tickers)
        selected_provenance = {
            ticker: provenance
            for ticker, provenance in provenance_by_ticker.items()
            if ticker in selected_filter
        }
    result = (rows, gaps)
    if include_metadata:
        return result + (
            starting_pnl, starting_day, terminal_status, terminal_reason,
            logged_selected_sports, selected_provenance,
        )
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
    cfg = deepcopy(cfg if cfg is not None else Config())
    all_rows, data_gaps, starting_pnl, starting_day, terminal_status, \
        terminal_reason, selected_sports, market_provenance = load_log(
            path, tickers=tickers, include_metadata=True, cfg=cfg,
            expected_code_fingerprint=code_fingerprint())
    cfg.sports = list(selected_sports)
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
            "selected_sports": selected_sports,
            "market_provenance": dict(market_provenance),
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
