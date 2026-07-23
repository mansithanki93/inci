"""Research logger: records every tick and trade so the strategy's core
hypothesis ("dips partially retrace") can be tested on real data.

Produces versioned CSVs. Tick rows retain no-quote/safety events and an event
identifier so related contracts remain in the same research partition.

After a few days of paper trading, analyze.py answers: given a dip of
size X, how often and how far did the price retrace, and how fast?
"""
import csv
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal


RESEARCH_SCHEMA_VERSION = 5
RESEARCH_CONFIG_FIELDS = (
    "use_demo_env", "tickers", "market_keywords",
    "dip_threshold", "lookback_seconds", "take_profit", "stop_loss",
    "max_hold_seconds", "contracts_per_trade", "max_open_positions",
    "max_daily_loss_usd", "min_price", "max_price", "max_spread",
    "sim_latency_s", "sim_slippage_cents", "balance_precision_usd",
    "poll_interval", "stale_data_s", "max_consec_errors",
    "close_buffer_seconds",
)
REPLAY_CODE_FILES = (
    "analyze.py", "bot.py", "config.py", "engine.py", "executor.py",
    "fees.py", "kalshi_client.py", "market_data.py", "replay.py",
    "research_log.py", "safety.py", "schemas.py", "signals.py",
    "strategy.py",
)


def _jsonable(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def config_fingerprint(config):
    config.validate()
    payload = {name: _jsonable(getattr(config, name))
               for name in RESEARCH_CONFIG_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def code_fingerprint():
    root = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    for name in REPLAY_CODE_FILES:
        path = os.path.join(root, name)
        digest.update(name.encode() + b"\0")
        with open(path, "rb") as handle:
            digest.update(handle.read())
        digest.update(b"\0")
    return digest.hexdigest()


class ResearchLog:
    def __init__(self, log_dir="logs", clock=time.time, session_id=None,
                 starting_pnl=0, config=None, session_start=None):
        if config is None:
            from config import Config
            config = Config()
        os.makedirs(log_dir, exist_ok=True)
        self.clock = clock
        self.session_id = session_id or uuid.uuid4().hex
        self.starting_pnl = Decimal(str(starting_pnl))
        if not self.starting_pnl.is_finite():
            raise ValueError("starting_pnl must be finite")
        if (not self.session_id
                or any(c not in "abcdefghijklmnopqrstuvwxyz"
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                       for c in self.session_id)):
            raise ValueError("session_id must be nonempty and filename-safe")
        created_at = clock() if session_start is None else session_start
        self.starting_utc_day = datetime.fromtimestamp(
            created_at, timezone.utc).date().isoformat()
        self.config_fingerprint = config_fingerprint(config)
        self.code_fingerprint = code_fingerprint()
        self._ended = False
        day = self.starting_utc_day.replace("-", "")
        suffix = f"{day}_{self.session_id}"
        # One process session per file. A restart resets history/pending/local
        # paper positions, so replay must never infer continuity across it.
        self.tick_path = os.path.join(log_dir, f"ticks_v5_{suffix}.csv")
        self.trade_path = os.path.join(log_dir, f"trades_v5_{suffix}.csv")
        self._init_file(self.tick_path,
                        ["schema_version", "session_id",
                         "starting_daily_pnl_usd", "starting_utc_day",
                         "utc_day", "config_fingerprint",
                         "code_fingerprint", "ts",
                         "event_id", "ticker",
                         "event", "detail", "close_ts", "can_close_early",
                         "mid", "bid", "ask", "bid_qty", "ask_qty"])
        self._init_file(self.trade_path,
                        ["schema_version", "session_id",
                         "starting_daily_pnl_usd", "starting_utc_day",
                         "utc_day", "config_fingerprint",
                         "code_fingerprint", "ts",
                         "event_id", "ticker",
                         "side", "price", "contracts", "fee_usd", "reason"])

    def _init_file(self, path, header):
        # Exclusive create prevents two processes/restarts from silently
        # sharing a session file even if a session identifier is reused.
        with open(path, "x", newline="") as f:
            csv.writer(f).writerow(header)

    def tick(self, ticker, mid, bid, ask, bid_qty=None, ask_qty=None, *,
             ts=None, group_id=None, event="quote", detail="",
             close_ts=None, can_close_early=None):
        timestamp = self.clock() if ts is None else ts
        timestamp_text = repr(float(timestamp))
        if close_ts is not None:
            close_ts = float(close_ts)
            if close_ts < 0 or close_ts in (float("inf"), float("-inf")) \
                    or close_ts != close_ts:
                raise ValueError("close_ts must be finite and nonnegative")
        if can_close_early is not None and not isinstance(
                can_close_early, bool):
            raise ValueError("can_close_early must be boolean")
        utc_day = datetime.fromtimestamp(
            float(timestamp_text), timezone.utc).date().isoformat()
        with open(self.tick_path, "a", newline="") as f:
            csv.writer(f).writerow([
                RESEARCH_SCHEMA_VERSION, self.session_id, self.starting_pnl,
                self.starting_utc_day, utc_day,
                self.config_fingerprint, self.code_fingerprint,
                timestamp_text,
                group_id if group_id is not None else "", ticker,
                event, detail,
                "" if close_ts is None else repr(close_ts),
                ("" if can_close_early is None
                 else str(can_close_early).lower()),
                mid, bid, ask,
                bid_qty, ask_qty])

    def trade(self, ticker, side, price, contracts, reason, *, fee=None,
              ts=None, group_id=None):
        timestamp = self.clock() if ts is None else ts
        timestamp_text = repr(float(timestamp))
        utc_day = datetime.fromtimestamp(
            float(timestamp_text), timezone.utc).date().isoformat()
        with open(self.trade_path, "a", newline="") as f:
            csv.writer(f).writerow([
                RESEARCH_SCHEMA_VERSION, self.session_id, self.starting_pnl,
                self.starting_utc_day, utc_day,
                self.config_fingerprint, self.code_fingerprint, timestamp_text,
                group_id if group_id is not None else "", ticker,
                side, price, contracts, fee,
                reason])

    def event(self, ticker, event, *, ts=None, group_id=None, detail=""):
        self.tick(ticker, None, None, None, None, None, ts=ts,
                  group_id=group_id, event=event, detail=detail)

    def end(self, *, clean, reason, ts=None):
        if self._ended:
            raise ValueError("research session already has a terminal record")
        event = "session_end" if clean else "session_halt"
        timestamp = self.clock() if ts is None else ts
        self.event("", event, ts=timestamp, detail=str(reason))
        # Make the terminal marker durable before reporting shutdown complete.
        with open(self.tick_path, "a") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        self._ended = True
