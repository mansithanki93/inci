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
import math
import os
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType

from sports_discovery import ContractProvenance


RESEARCH_SCHEMA_VERSION = 6
RESEARCH_CONFIG_FIELDS = (
    "use_demo_env", "tickers", "sports", "max_monitored_markets",
    "dip_threshold", "lookback_seconds", "take_profit", "tp_trail_cents",
    "stop_loss",
    "max_hold_seconds", "contracts_per_trade", "max_open_positions",
    "max_daily_loss_usd", "min_price", "max_price", "max_spread",
    "sim_latency_s", "sim_slippage_cents", "balance_precision_usd",
    "poll_interval", "stale_data_s", "max_consec_errors",
    "close_buffer_seconds",
    "espn_gate_enabled", "espn_leagues", "espn_cache_s",
    "espn_min_model_prob", "espn_min_edge", "prefer_scoreboard_bind",
    "one_contract_per_event",
    "sibling_spike_enabled", "sibling_spike_cents",
    "sibling_spike_lookback_s",
    "live_tennis_enabled", "live_tennis_tours", "live_tennis_cache_s",
    "live_tennis_include_upcoming", "live_tennis_ticker_substrings",
)
REPLAY_CODE_FILES = (
    "analyze.py", "bot.py", "config.py", "engine.py", "executor.py",
    "fees.py", "kalshi_client.py", "market_data.py", "replay.py",
    "research_log.py", "safety.py", "schemas.py", "signals.py",
    "sports_discovery.py",
    "strategy.py",
)
TICK_HEADER = [
    "schema_version", "session_id", "starting_daily_pnl_usd",
    "starting_utc_day", "utc_day", "config_fingerprint",
    "code_fingerprint", "selected_sports", "ts", "ticker",
    "sport", "league", "series_ticker", "milestone_id", "event_ticker",
    "scheduled_start_ts", "event", "detail", "close_ts",
    "can_close_early", "mid", "bid", "ask", "bid_qty", "ask_qty",
]
TRADE_HEADER = [
    "schema_version", "session_id", "starting_daily_pnl_usd",
    "starting_utc_day", "utc_day", "config_fingerprint",
    "code_fingerprint", "selected_sports", "ts", "ticker",
    "sport", "league", "series_ticker", "milestone_id", "event_ticker",
    "scheduled_start_ts", "side", "price", "contracts", "fee_usd", "reason",
]


def write_startup_halt(
        reason, *, requested_sports=(), tickers=(), log_dir="logs",
        clock=time.time):
    """Durably record a failure before a canonical research log can exist."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("startup halt reason must be a nonempty string")

    def identities(values, field):
        if isinstance(values, (str, bytes)):
            raise ValueError(f"{field} must be a sequence")
        try:
            values = list(values)
        except TypeError as error:
            raise ValueError(f"{field} must be a sequence") from error
        if any(not isinstance(value, str) or not value
               for value in values):
            raise ValueError(
                f"{field} must contain nonempty strings")
        return values

    timestamp = clock()
    if (isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(float(timestamp))
            or float(timestamp) < 0):
        raise ValueError(
            "startup halt timestamp must be finite and nonnegative")
    payload = {
        "schema_version": 6,
        "event": "session_halt",
        "ts": float(timestamp),
        "requested_sports": identities(
            requested_sports, "requested_sports"),
        "tickers": identities(tickers, "tickers"),
        "reason": reason,
    }
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "startup_halts_v6.jsonl")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            payload, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


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
                 starting_pnl=0, config=None, session_start=None,
                 provenance_by_ticker=None):
        if config is None:
            from config import Config
            config = Config()
        config.validate()
        selected_sports = tuple(config.sports)
        if (not selected_sports
                or len(set(selected_sports)) != len(selected_sports)
                or any(not isinstance(sport, str) or not sport
                       for sport in selected_sports)):
            raise ValueError(
                "config.sports must be a nonempty unique canonical list")
        if not isinstance(provenance_by_ticker, Mapping):
            raise TypeError("provenance_by_ticker must be a mapping")
        if config.tickers and set(provenance_by_ticker) != set(config.tickers):
            raise ValueError(
                "explicit-ticker provenance keys must exactly match "
                "config.tickers")
        copied_provenance = {}
        event_provenance = {}
        for ticker, provenance in provenance_by_ticker.items():
            if not isinstance(ticker, str) or not ticker:
                raise ValueError(
                    "provenance ticker keys must be nonempty strings")
            if not isinstance(provenance, ContractProvenance):
                raise TypeError(
                    f"provenance for {ticker!r} must be ContractProvenance")
            if provenance.sport not in selected_sports:
                raise ValueError(
                    f"provenance Sport {provenance.sport!r} for {ticker!r} "
                    "is not selected")
            game = (
                provenance.sport, provenance.league,
                provenance.series_ticker, provenance.milestone_id,
                provenance.scheduled_start_ts,
            )
            prior = event_provenance.setdefault(
                provenance.event_ticker, game)
            if prior != game:
                raise ValueError(
                    f"event {provenance.event_ticker!r} has conflicting "
                    "game provenance")
            copied_provenance[ticker] = provenance
        selected_sports_text = json.dumps(
            list(selected_sports), separators=(",", ":"))
        captured_config_fingerprint = config_fingerprint(config)
        captured_code_fingerprint = code_fingerprint()
        captured_starting_pnl = Decimal(str(starting_pnl))
        if not captured_starting_pnl.is_finite():
            raise ValueError("starting_pnl must be finite")
        captured_session_id = session_id or uuid.uuid4().hex
        if (not captured_session_id
                or any(c not in "abcdefghijklmnopqrstuvwxyz"
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                       for c in captured_session_id)):
            raise ValueError("session_id must be nonempty and filename-safe")
        created_at = clock() if session_start is None else session_start
        created_at = self._timestamp(created_at, "session_start")
        starting_utc_day = datetime.fromtimestamp(
            created_at, timezone.utc).date().isoformat()

        # All session identity is validated and captured before any directory
        # or file is created.
        self.clock = clock
        self.session_id = captured_session_id
        self.starting_pnl = captured_starting_pnl
        self.starting_utc_day = starting_utc_day
        self.selected_sports = selected_sports
        self.selected_sports_text = selected_sports_text
        self.config_fingerprint = captured_config_fingerprint
        self.code_fingerprint = captured_code_fingerprint
        self.provenance_by_ticker = MappingProxyType(copied_provenance)
        self._ended = False
        os.makedirs(log_dir, exist_ok=True)
        day = self.starting_utc_day.replace("-", "")
        suffix = f"{day}_{self.session_id}"
        # One process session per file. A restart resets history/pending/local
        # paper positions, so replay must never infer continuity across it.
        self.tick_path = os.path.join(log_dir, f"ticks_v6_{suffix}.csv")
        self.trade_path = os.path.join(log_dir, f"trades_v6_{suffix}.csv")
        self._init_file(self.tick_path, TICK_HEADER)
        self._init_file(self.trade_path, TRADE_HEADER)

    @staticmethod
    def _timestamp(value, field="ts"):
        if (isinstance(value, bool)
                or not isinstance(value, (int, float, Decimal))):
            raise ValueError(f"{field} must be finite and nonnegative")
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError(f"{field} must be finite and nonnegative")
        try:
            datetime.fromtimestamp(parsed, timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError(
                f"{field} must be a supported timestamp") from error
        return parsed

    @staticmethod
    def _decimal(value, field, *, minimum=None, maximum=None):
        try:
            parsed = Decimal(str(value))
        except Exception as error:
            raise ValueError(f"{field} must be decimal") from error
        if not parsed.is_finite():
            raise ValueError(f"{field} must be finite")
        if minimum is not None and parsed < minimum:
            raise ValueError(f"{field} must be at least {minimum}")
        if maximum is not None and parsed > maximum:
            raise ValueError(f"{field} must be at most {maximum}")
        return parsed

    def _init_file(self, path, header):
        # Exclusive create prevents two processes/restarts from silently
        # sharing a session file even if a session identifier is reused.
        with open(path, "x", newline="") as f:
            csv.writer(f).writerow(header)

    def _ensure_open(self):
        if self._ended:
            raise ValueError("research session already has a terminal record")

    def _provenance(self, ticker):
        self._ensure_open()
        if not isinstance(ticker, str) or not ticker:
            raise ValueError("ticker must be a nonempty string")
        try:
            return self.provenance_by_ticker[ticker]
        except KeyError as error:
            raise ValueError(
                f"unknown ticker provenance: {ticker!r}") from error

    def _common(self, timestamp, ticker="", provenance=None):
        utc_day = datetime.fromtimestamp(
            timestamp, timezone.utc).date().isoformat()
        if provenance is None:
            provenance_values = ["", "", "", "", "", ""]
        else:
            provenance_values = [
                provenance.sport,
                "" if provenance.league is None else provenance.league,
                provenance.series_ticker,
                provenance.milestone_id,
                provenance.event_ticker,
                repr(float(provenance.scheduled_start_ts)),
            ]
        return [
            RESEARCH_SCHEMA_VERSION, self.session_id, self.starting_pnl,
            self.starting_utc_day, utc_day,
            self.config_fingerprint, self.code_fingerprint,
            self.selected_sports_text, repr(timestamp), ticker,
            *provenance_values,
        ]

    @staticmethod
    def _append(path, row):
        with open(path, "a", newline="") as handle:
            csv.writer(handle).writerow(row)

    def tick(self, ticker, mid, bid, ask, bid_qty=None, ask_qty=None, *,
             ts=None, event="quote", detail="",
             close_ts=None, can_close_early=None):
        provenance = self._provenance(ticker)
        timestamp = self._timestamp(
            self.clock() if ts is None else ts)
        if not isinstance(event, str) or not event:
            raise ValueError("event must be a nonempty string")
        if event in ("session_end", "session_halt"):
            raise ValueError("terminal events may be written only by end()")
        if not isinstance(detail, str):
            raise ValueError("detail must be a string")
        if event == "quote":
            if any(value is None for value in (
                    mid, bid, ask, bid_qty, ask_qty, close_ts,
                    can_close_early)):
                raise ValueError(
                    "quote requires complete lifecycle, book and depth")
            parsed_mid = self._decimal(
                mid, "mid", minimum=Decimal(0), maximum=Decimal(100))
            parsed_bid = self._decimal(
                bid, "bid", minimum=Decimal(0), maximum=Decimal(100))
            parsed_ask = self._decimal(
                ask, "ask", minimum=Decimal(0), maximum=Decimal(100))
            parsed_bid_qty = self._decimal(
                bid_qty, "bid_qty", minimum=Decimal(0))
            parsed_ask_qty = self._decimal(
                ask_qty, "ask_qty", minimum=Decimal(0))
            if parsed_bid > parsed_ask:
                raise ValueError("quote book is crossed")
            if parsed_mid != (parsed_bid + parsed_ask) / Decimal(2):
                raise ValueError("mid must equal arithmetic book midpoint")
            parsed_close_ts = self._timestamp(close_ts, "close_ts")
            if not isinstance(can_close_early, bool):
                raise ValueError("can_close_early must be boolean")
            lifecycle_book = [
                repr(parsed_close_ts), str(can_close_early).lower(),
                parsed_mid, parsed_bid, parsed_ask,
                parsed_bid_qty, parsed_ask_qty,
            ]
        else:
            if any(value is not None for value in (
                    mid, bid, ask, bid_qty, ask_qty, close_ts,
                    can_close_early)):
                raise ValueError(
                    "nonquote event must not contain lifecycle or book data")
            lifecycle_book = ["", "", "", "", "", "", ""]
        row = self._common(timestamp, ticker, provenance)
        self._append(
            self.tick_path,
            row + [event, detail, *lifecycle_book])

    def trade(self, ticker, side, price, contracts, reason, *, fee=None,
              ts=None):
        provenance = self._provenance(ticker)
        timestamp = self._timestamp(
            self.clock() if ts is None else ts)
        if side not in ("BUY", "SELL"):
            raise ValueError("trade side must be BUY or SELL")
        parsed_price = self._decimal(
            price, "price", minimum=Decimal(0), maximum=Decimal(100))
        parsed_contracts = self._decimal(
            contracts, "contracts", minimum=Decimal(0))
        if parsed_contracts <= 0:
            raise ValueError("contracts must be positive")
        parsed_fee = self._decimal(
            fee, "fee_usd", minimum=Decimal(0))
        if not isinstance(reason, str) or not reason:
            raise ValueError("trade reason must be a nonempty string")
        self._append(
            self.trade_path,
            self._common(timestamp, ticker, provenance) + [
                side, parsed_price, parsed_contracts, parsed_fee, reason])

    def event(self, ticker, event, *, ts=None, detail=""):
        if not ticker:
            raise ValueError(
                "tickerless nonterminal events are forbidden")
        self.tick(
            ticker, None, None, None, None, None, ts=ts,
            event=event, detail=detail)

    def end(self, *, clean, reason, ts=None):
        self._ensure_open()
        if not isinstance(clean, bool):
            raise ValueError("clean must be boolean")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("terminal reason must be a nonempty string")
        event = "session_end" if clean else "session_halt"
        timestamp = self._timestamp(
            self.clock() if ts is None else ts)
        row = self._common(timestamp) + [
            event, reason, "", "", "", "", "", "", "",
        ]
        self._append(self.tick_path, row)
        self._ended = True
        # Make the terminal marker durable before reporting shutdown complete.
        with open(self.tick_path, "a", encoding="utf-8") as handle:
            handle.flush()
            os.fsync(handle.fileno())
