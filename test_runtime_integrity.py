"""Focused regression tests for live/replay entry-decision integrity."""
from collections import defaultdict, deque
from decimal import Decimal
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

from bot import run_loop
from config import Config
from engine import Context, _gate_snapshot, _sibling_snapshot, process_tick
from executor import Executor
from fees import fee_usd
from safety import Safety
from research_log import ResearchLog
from replay import replay
from market_data import PriceFeed
from strategy import Position, ScalpStrategy
from sports_discovery import (
    ContractProvenance,
    DiscoveryResult,
    SelectedContract,
)


class _Strategy:
    def __init__(self, entry=False):
        self.positions = {}
        self.realized_pnl = Decimal(0)
        self.fills = []
        self.entry = entry

    def refresh_daily_pnl(self, _now):
        return None

    def record_fill(self, ticker, side, price, contracts, fee, now=None):
        self.fills.append((ticker, side, price, contracts, fee, now))

    def check_exit(self, _ticker, _bid, now=None):
        return None

    def check_entry(self, ticker, history, now, mid, bid, ask, ask_qty=None):
        if not self.entry:
            return None
        return {"ticker": ticker, "contracts": Decimal(1),
                "reason": "test dip"}


class _Gate:
    def __init__(self, allow):
        self.allow = allow
        self.calls = []

    def enabled(self):
        return True

    def decide(self, **kwargs):
        self.calls.append(kwargs)
        model = Decimal("0.8") if self.allow else Decimal("0.2")
        return SimpleNamespace(
            allow=self.allow,
            reason="score current" if self.allow else "blocked:score changed",
            model_prob=model,
            market_prob=Decimal("0.5"),
            edge=Decimal("0.3") if self.allow else Decimal("-0.3"),
            espn_match_id="espn:M", espn_player="Player One",
            model_1_prob=model + Decimal("0.01"), model_2_prob=model,
            prior_source_sha256="a" * 64,
            prior_generated_at="1970-01-01T00:00:00Z",
            prior_model_1_id="MODEL-A", prior_model_2_id="MODEL-B",
            score_source="espn", score_match_id="espn:M",
            score_athlete_id="espn:athlete:one",
            score_opponent_id="espn:athlete:two",
            score_player_name="Player One",
            score_opponent_name="Player Two",
            score_timestamp=None, score_lifecycle_state="pre",
            score_observed=False, score_best_of=3,
            score_sets_for=0, score_sets_against=0,
            score_games_for=0, score_games_against=0,
            prematch_model_1_prob=model + Decimal("0.01"),
            prematch_model_2_prob=model,
            prior_model_as_of="1970-01-01T00:00:00Z",
            prior_match_start="1970-01-01T02:46:40Z")


class _Feed:
    def __init__(self):
        self.history = defaultdict(lambda: deque(maxlen=600))
        self.books = {"TRADE": (Decimal(49), Decimal(10),
                                Decimal(50), Decimal(10))}
        self.close_times = {"TRADE": 10_000}
        self.contracts_by_ticker = {
            "TRADE": SimpleNamespace(title="Player One",
                                     game_title="Player One v Player Two")}
        self.provenance_by_ticker = {
            "TRADE": ContractProvenance(
                sport="Tennis", league="ATP", series_ticker="KXATP",
                milestone_id="M", event_ticker="EVENT",
                scheduled_start_ts=10_000)}
        self.score_bindings_by_ticker = {
            "TRADE": (
                "espn:M", "espn:athlete:one", "espn:athlete:two",
                "Player One", "Player Two")}

    def top_of_book(self, ticker):
        return self.books.get(ticker, (None, None, None, None))

    def lifecycle(self, ticker):
        return self.close_times.get(ticker), False

    def entry_allowed(self, ticker, now, required_seconds):
        return now + required_seconds < self.close_times[ticker]

    def early_close_risk(self, _ticker):
        return False

    def sibling_tickers(self, _ticker):
        return ()


class RuntimeIntegrityTests(unittest.TestCase):
    def test_each_trade_decides_before_later_trade_requote_delay(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False, sim_latency_s=1,
            fill_timeout_s=4,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class SequentialFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.per_ticker_calls = defaultdict(int)
                self.books["A"] = self.books["TRADE"]
                self.books["B"] = self.books["TRADE"]
                self.close_times.update(A=10_000, B=10_000)

            def get_quote(self, ticker):
                self.calls.append(ticker)
                self.per_ticker_calls[ticker] += 1
                observed_at = {
                    ("A", 1): .5,
                    ("A", 2): 2.0,
                    ("B", 1): 3.0,
                    ("B", 2): 10.0,
                }[(ticker, self.per_ticker_calls[ticker])]
                now[0] = observed_at + .25
                bid, bid_qty, ask, ask_qty = self.books[ticker]
                mid = (bid + ask) / 2
                self.history[ticker].append((observed_at, mid))
                return mid, bid, ask, observed_at

            def stale_tickers(self, _tickers):
                return []

        feed = SequentialFeed()
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: now[0])
        executor.submit_paper(
            "A", "BUY", Decimal(1), "signal", now=0,
            limit_price=Decimal(50))
        ctx = Context(
            cfg, feed, strategy, executor, None, Safety(cfg),
            clock=lambda: now[0])

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ("A", "B"), sleep=stop_after_one_sweep,
            quote_tickers=("A", "B"))

        self.assertEqual(feed.calls, ["A", "A", "B", "B"])
        self.assertEqual(len(strategy.fills), 1)
        self.assertEqual(strategy.fills[0][0:2], ("A", "BUY"))
        self.assertEqual(strategy.fills[0][-1], 2.25)

    def test_pending_buy_uses_post_evidence_trade_requote(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )
        cfg.sim_latency_s = 1
        cfg.poll_interval = 0
        now = [0.0]

        class RequoteFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []

            def get_quote(self, ticker):
                self.calls.append(ticker)
                if len(self.calls) == 1:
                    observed_at = 1.5
                    self.books[ticker] = (
                        Decimal(49), Decimal(10),
                        Decimal(50), Decimal(10))
                else:
                    observed_at = 4.0
                    self.books[ticker] = (
                        Decimal(59), Decimal(10),
                        Decimal(60), Decimal(10))
                now[0] = observed_at + 0.25
                mid = (self.books[ticker][0] + self.books[ticker][2]) / 2
                self.history[ticker].append((observed_at, mid))
                return mid, self.books[ticker][0], self.books[ticker][2], observed_at

            def stale_tickers(self, _tickers):
                return []

        class AdvancingGate(_Gate):
            def decide(self, **kwargs):
                now[0] = 3.0
                return super().decide(**kwargs)

        feed = RequoteFeed()
        # The signal-time cap is 50c and the delayed BUY is already due.
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: now[0])
        executor.submit_paper(
            "TRADE", "BUY", Decimal(1), "signal", now=0,
            limit_price=Decimal(50))
        ctx = Context(
            cfg, feed, strategy, executor, None, Safety(cfg),
            clock=lambda: now[0], espn_gate=AdvancingGate(True))

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["TRADE"], sleep=stop_after_one_sweep,
            quote_tickers=("TRADE",))

        self.assertEqual(feed.calls, ["TRADE", "TRADE"])
        self.assertFalse(any(fill[1] == "BUY" for fill in strategy.fills))

    def test_failed_execution_requote_persists_only_the_failure_gap(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class FailedRequoteFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []

            def get_quote(self, ticker):
                self.calls.append(ticker)
                if len(self.calls) == 2:
                    now[0] = 2.0
                    raise RuntimeError("execution requote failed")
                now[0] = 1.0
                bid, bid_qty, ask, ask_qty = self.books[ticker]
                mid = (bid + ask) / 2
                self.history[ticker].append((now[0], mid))
                return mid, bid, ask, now[0]

            def stale_tickers(self, _tickers):
                return []

        class CaptureLog:
            def __init__(self):
                self.quotes = []
                self.events = []

            def tick(self, *args, **kwargs):
                self.quotes.append((args, kwargs))

            def event(self, ticker, event, **kwargs):
                self.events.append((ticker, event, kwargs))

            def trade(self, *_args, **_kwargs):
                return None

            def end(self, **_kwargs):
                return None

        feed = FailedRequoteFeed()
        capture = CaptureLog()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(
                cfg, None, feed, clock=lambda: now[0]),
            capture, Safety(cfg), clock=lambda: now[0])

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["TRADE"], sleep=stop_after_one_sweep,
            quote_tickers=("TRADE",))

        self.assertEqual(feed.calls, ["TRADE", "TRADE"])
        self.assertEqual(capture.quotes, [])
        self.assertEqual(list(feed.history["TRADE"]), [])
        self.assertEqual(
            [(ticker, event) for ticker, event, _detail in capture.events],
            [("TRADE", "api_error")])

    def test_missing_required_watch_persists_denied_entry_package(self):
        from market_data import MarketUnavailable

        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class FailedWatchFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.books["WATCH"] = (
                    Decimal(49), Decimal(10), Decimal(50), Decimal(10))

            def sibling_tickers(self, ticker):
                return ("WATCH",) if ticker == "TRADE" else ("TRADE",)

            def get_quote(self, ticker):
                self.calls.append(ticker)
                now[0] += 1.0
                if ticker == "WATCH":
                    raise MarketUnavailable("watch book unavailable")
                bid, bid_qty, ask, ask_qty = self.books[ticker]
                mid = (bid + ask) / 2
                self.history[ticker].append((now[0], mid))
                return mid, bid, ask, now[0]

            def stale_tickers(self, _tickers):
                return []

        class CaptureLog:
            def __init__(self):
                self.quotes = []
                self.events = []

            def tick(self, *args, **kwargs):
                self.quotes.append((args, kwargs))

            def event(self, ticker, event, **kwargs):
                self.events.append((ticker, event, kwargs))

            def trade(self, *_args, **_kwargs):
                return None

            def end(self, **_kwargs):
                return None

        feed = FailedWatchFeed()
        capture = CaptureLog()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(
                cfg, None, feed, clock=lambda: now[0]),
            capture, Safety(cfg), clock=lambda: now[0])

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["TRADE"], sleep=stop_after_one_sweep,
            quote_tickers=("TRADE", "WATCH"))

        self.assertEqual(feed.calls, ["TRADE", "WATCH", "TRADE"])
        self.assertEqual(
            [args[0] for args, _kwargs in capture.quotes],
            ["TRADE", "TRADE"])
        details = [
            json.loads(kwargs["detail"])
            for _args, kwargs in capture.quotes]
        self.assertEqual(
            [detail["quote_phase"] for detail in details],
            ["evidence", "execution"])
        self.assertFalse(details[-1]["siblings"]["complete"])
        self.assertIn(
            "missing current-sweep sibling quote",
            details[-1]["siblings"]["error"])
        self.assertEqual(len(feed.history["TRADE"]), 2)
        self.assertEqual(
            [(ticker, event) for ticker, event, _detail in capture.events],
            [("WATCH", "api_error"), ("WATCH", "quarantined")])

    def test_missing_required_watch_cannot_suppress_stop_exit(self):
        from market_data import MarketUnavailable

        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True, sim_latency_s=1,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class FailedWatchFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.books["TRADE"] = (
                    Decimal(40), Decimal(10), Decimal(42), Decimal(10))
                self.books["WATCH"] = (
                    Decimal(49), Decimal(10), Decimal(50), Decimal(10))

            def sibling_tickers(self, ticker):
                return ("WATCH",) if ticker == "TRADE" else ("TRADE",)

            def get_quote(self, ticker):
                self.calls.append(ticker)
                now[0] += 1.0
                if ticker == "WATCH":
                    raise MarketUnavailable("watch book unavailable")
                bid, bid_qty, ask, ask_qty = self.books[ticker]
                mid = (bid + ask) / 2
                self.history[ticker].append((now[0], mid))
                return mid, bid, ask, now[0]

            def stale_tickers(self, _tickers):
                return []

        class TrackingExecutor(Executor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.submissions = []

            def submit_paper(self, ticker, side, contracts, reason="",
                             now=None, resting=None, limit_price=None,
                             **kwargs):
                self.submissions.append((ticker, side, reason))
                return super().submit_paper(
                    ticker, side, contracts, reason, now=now,
                    resting=resting, limit_price=limit_price, **kwargs)

        class CaptureLog:
            def __init__(self):
                self.quotes = []

            def tick(self, ticker, *_args, **_kwargs):
                self.quotes.append(ticker)

            def event(self, *_args, **_kwargs):
                return None

            def trade(self, *_args, **_kwargs):
                return None

            def end(self, **_kwargs):
                return None

        feed = FailedWatchFeed()
        strategy = ScalpStrategy(cfg)
        strategy.positions["TRADE"] = Position(
            "TRADE", Decimal(50), Decimal(1), opened_at=0,
            entry_fee_usd=Decimal(0))
        executor = TrackingExecutor(
            cfg, None, feed, clock=lambda: now[0])
        capture = CaptureLog()
        ctx = Context(
            cfg, feed, strategy, executor, capture, Safety(cfg),
            clock=lambda: now[0])

        sweeps = [0]

        def stop_after_two_sweeps(_seconds):
            sweeps[0] += 1
            if sweeps[0] >= 2:
                raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["TRADE"], sleep=stop_after_two_sweeps,
            quote_tickers=("TRADE", "WATCH"))

        self.assertEqual(
            feed.calls,
            ["TRADE", "WATCH", "TRADE", "TRADE", "TRADE"])
        self.assertEqual(
            capture.quotes, ["TRADE", "TRADE", "TRADE", "TRADE"])
        self.assertTrue(any(
            ticker == "TRADE" and side == "SELL"
            and "stop-loss" in reason
            for ticker, side, reason in executor.submissions))
        self.assertNotIn("TRADE", strategy.positions)

    def test_failed_sibling_lookup_creates_denied_entry_package(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=True,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class FailedSiblingLookupFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.books["WATCH"] = (
                    Decimal(49), Decimal(10), Decimal(50), Decimal(10))

            def sibling_tickers(self, _ticker):
                raise RuntimeError("sibling provenance unavailable")

            def get_quote(self, ticker):
                self.calls.append(ticker)
                now[0] += 1.0
                bid, bid_qty, ask, ask_qty = self.books[ticker]
                mid = (bid + ask) / 2
                self.history[ticker].append((now[0], mid))
                return mid, bid, ask, now[0]

            def stale_tickers(self, _tickers):
                return []

        class CaptureLog:
            def __init__(self):
                self.quotes = []
                self.events = []

            def tick(self, ticker, *_args, **_kwargs):
                self.quotes.append(ticker)

            def event(self, ticker, event, **kwargs):
                self.events.append((ticker, event, kwargs))

            def trade(self, *_args, **_kwargs):
                return None

            def end(self, **_kwargs):
                return None

        feed = FailedSiblingLookupFeed()
        capture = CaptureLog()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(
                cfg, None, feed, clock=lambda: now[0]),
            capture, Safety(cfg), clock=lambda: now[0])

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["TRADE"], sleep=stop_after_one_sweep,
            quote_tickers=("TRADE", "WATCH"))

        self.assertEqual(feed.calls, ["TRADE", "TRADE", "WATCH"])
        self.assertEqual(capture.quotes, ["TRADE", "TRADE", "WATCH"])
        self.assertEqual(
            [(ticker, event) for ticker, event, _detail in capture.events],
            [("TRADE", "api_error")])

    def test_runtime_persists_post_gate_decision_and_quote_phases(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class RequoteFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.calls = []

            def get_quote(self, ticker):
                self.calls.append(ticker)
                if len(self.calls) == 1:
                    observed_at, bid, ask = 1.5, Decimal(49), Decimal(50)
                else:
                    observed_at, bid, ask = 4.0, Decimal(59), Decimal(60)
                self.books[ticker] = (
                    bid, Decimal(10), ask, Decimal(10))
                now[0] = observed_at + 0.25
                mid = (bid + ask) / 2
                self.history[ticker].append((observed_at, mid))
                return mid, bid, ask, observed_at

            def stale_tickers(self, _tickers):
                return []

        class AdvancingGate(_Gate):
            def decide(self, **kwargs):
                now[0] = 3.0
                return super().decide(**kwargs)

        class CaptureLog:
            def __init__(self):
                self.quotes = []

            def tick(self, ticker, mid, bid, ask, bid_qty, ask_qty, **kwargs):
                self.quotes.append({
                    "ticker": ticker, "mid": mid, "bid": bid, "ask": ask,
                    "bid_qty": bid_qty, "ask_qty": ask_qty, **kwargs,
                })

            def event(self, *_args, **_kwargs):
                return None

            def trade(self, *_args, **_kwargs):
                return None

            def end(self, **_kwargs):
                return None

        feed = RequoteFeed()
        capture = CaptureLog()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(
                cfg, None, feed, clock=lambda: now[0]),
            capture, Safety(cfg), clock=lambda: now[0],
            espn_gate=AdvancingGate(True))

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["TRADE"], sleep=stop_after_one_sweep,
            quote_tickers=("TRADE",))

        payloads = [json.loads(row["detail"]) for row in capture.quotes]
        self.assertEqual(
            [payload["quote_phase"] for payload in payloads],
            ["evidence", "execution"])
        self.assertEqual(
            [payload["decision_at"] for payload in payloads],
            [4.25, 4.25])
        self.assertEqual([row["ts"] for row in capture.quotes], [1.5, 4.0])
        execution_gate = payloads[-1]["score_gate"]
        self.assertEqual(execution_gate["market_prob"], "0.6")
        self.assertEqual(execution_gate["edge"], "0.2")
        self.assertEqual(execution_gate["score_best_of"], 3)
        self.assertIs(execution_gate["score_observed"], False)
        self.assertEqual(execution_gate["gate_observed_at"], "3.0")
        self.assertEqual(
            ctx.espn_gate.calls[0]["scheduled_start_ts"], 10_000)

    def test_post_evidence_reprice_blocks_score_that_ages_during_package(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )
        from engine import _reprice_gate_snapshot

        feed = _Feed()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(cfg, None, feed), None,
            Safety(cfg), clock=lambda: 100)
        snapshot = {
            "enabled": True, "allow": True, "reason": "score current",
            "gate_observed_at": "2",
            "model_prob": "0.8", "market_prob": "0.5", "edge": "0.3",
            "model_1_prob": "0.81", "model_2_prob": "0.8",
            "prior_source_sha256": "a" * 64,
            "prior_generated_at": "1970-01-01T00:00:00Z",
            "prior_model_1_id": "MODEL-A", "prior_model_2_id": "MODEL-B",
            "prematch_model_1_prob": "0.81",
            "prematch_model_2_prob": "0.8",
            "prior_model_as_of": "1970-01-01T00:00:00Z",
            "prior_match_start": "1970-01-01T02:46:40Z",
            "score_source": "espn", "score_match_id": "espn:M",
            "score_athlete_id": "espn:athlete:one",
            "score_opponent_id": "espn:athlete:two",
            "score_timestamp": "1", "score_lifecycle_state": "in",
            "score_observed": True, "score_best_of": 3,
            "score_sets_for": 0, "score_sets_against": 0,
            "score_games_for": 0, "score_games_against": 0,
        }

        updated = _reprice_gate_snapshot(
            ctx, snapshot, Decimal(50), decision_at=100)

        self.assertFalse(updated["allow"])
        self.assertIn("stale_score", updated["reason"])

    def test_runtime_pins_score_orientation_per_market(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )

        class DriftGate(_Gate):
            def decide(self, **kwargs):
                decision = super().decide(**kwargs)
                if len(self.calls) == 1:
                    return decision
                values = vars(decision).copy()
                values.update(
                    espn_match_id="espn:OTHER",
                    score_match_id="espn:OTHER",
                    score_athlete_id="espn:athlete:other-one",
                    score_opponent_id="espn:athlete:other-two")
                return SimpleNamespace(**values)

        feed = _Feed()
        gate = DriftGate(True)
        ctx = Context(
            cfg, feed, _Strategy(), Executor(cfg, None, feed), None,
            Safety(cfg), clock=lambda: 1, espn_gate=gate)

        first = _gate_snapshot(ctx, "TRADE", Decimal(50), required=True)
        second = _gate_snapshot(ctx, "TRADE", Decimal(50), required=True)

        self.assertTrue(first["allow"])
        self.assertFalse(second["allow"])
        self.assertIn("identity_drift", second["reason"])
        from engine import _reprice_gate_snapshot
        repriced = _reprice_gate_snapshot(
            ctx, second, Decimal(50), decision_at=1, ticker="TRADE")
        self.assertFalse(repriced["allow"])
        self.assertIn("identity_drift", repriced["reason"])

    def test_post_evidence_reprice_preserves_nonprice_hard_deny(self):
        from engine import _reprice_gate_snapshot

        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False)
        feed = _Feed()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(cfg, None, feed), None,
            Safety(cfg), clock=lambda: 2)
        snapshot = {
            "enabled": True, "allow": False,
            "reason": "blocked:score_progress_rewind",
            "gate_observed_at": "1.5",
            "model_prob": "0.8", "market_prob": "0.5", "edge": "0.3",
            "model_1_prob": "0.81", "model_2_prob": "0.8",
            "prior_source_sha256": "a" * 64,
            "prior_generated_at": "1970-01-01T00:00:01Z",
            "prior_model_1_id": "MODEL-A", "prior_model_2_id": "MODEL-B",
            "prematch_model_1_prob": "0.81",
            "prematch_model_2_prob": "0.8",
            "prior_model_as_of": "1970-01-01T00:00:01Z",
            "prior_match_start": "1970-01-01T02:46:40Z",
            "score_source": "espn", "score_match_id": "espn:M",
            "score_athlete_id": "espn:athlete:one",
            "score_opponent_id": "espn:athlete:two",
            "score_timestamp": "1", "score_lifecycle_state": "in",
            "score_observed": True, "score_best_of": 3,
            "score_sets_for": 0, "score_sets_against": 0,
            "score_games_for": 0, "score_games_against": 0,
        }

        repriced = _reprice_gate_snapshot(
            ctx, snapshot, Decimal(40), decision_at=2, ticker="TRADE")

        self.assertFalse(repriced["allow"])
        self.assertIn("score_progress_rewind", repriced["reason"])

    def test_post_evidence_reprice_expires_prior_at_decision(self):
        from engine import _reprice_gate_snapshot

        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False, two_model_prior_max_age_s=5)
        feed = _Feed()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(cfg, None, feed), None,
            Safety(cfg), clock=lambda: 6)
        snapshot = {
            "enabled": True, "allow": True, "reason": "models_ok",
            "gate_observed_at": "4",
            "model_prob": "0.8", "market_prob": "0.5", "edge": "0.3",
            "model_1_prob": "0.81", "model_2_prob": "0.8",
            "prior_source_sha256": "a" * 64,
            "prior_generated_at": "1970-01-01T00:00:00Z",
            "prior_model_1_id": "MODEL-A", "prior_model_2_id": "MODEL-B",
            "prematch_model_1_prob": "0.81",
            "prematch_model_2_prob": "0.8",
            "prior_model_as_of": "1970-01-01T00:00:00Z",
            "prior_match_start": "1970-01-01T02:46:40Z",
            "score_source": "espn", "score_match_id": "espn:M",
            "score_athlete_id": "espn:athlete:one",
            "score_opponent_id": "espn:athlete:two",
            "score_timestamp": "4", "score_lifecycle_state": "in",
            "score_observed": True, "score_best_of": 3,
            "score_sets_for": 0, "score_sets_against": 0,
            "score_games_for": 0, "score_games_against": 0,
        }

        repriced = _reprice_gate_snapshot(
            ctx, snapshot, Decimal(50), decision_at=6, ticker="TRADE")

        self.assertFalse(repriced["allow"])
        self.assertIn("stale_prematch_prior", repriced["reason"])

    def test_prematch_gate_cannot_cross_start_during_requote(self):
        from engine import _reprice_gate_snapshot

        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=True,
            sibling_spike_enabled=False,
        )
        feed = _Feed()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(cfg, None, feed), None,
            Safety(cfg), clock=lambda: 9_999, espn_gate=_Gate(True))
        before = _gate_snapshot(
            ctx, "TRADE", Decimal(50), required=True)

        at_start = _reprice_gate_snapshot(
            ctx, before, Decimal(50), decision_at=10_000,
            ticker="TRADE")

        self.assertFalse(at_start["allow"])
        self.assertIn("prematch_state", at_start["reason"])

    def test_buffered_quote_and_error_rows_keep_timestamp_order(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        cfg.poll_interval = 0
        now = [0.0]

        class InterleavedFeed:
            def __init__(self):
                self.calls = []
                self.history = defaultdict(lambda: deque(maxlen=600))
                self.books = {}

            def get_quote(self, ticker):
                self.calls.append(ticker)
                if ticker == "B":
                    now[0] = 4.0
                    raise RuntimeError("evidence failure")
                observed_at = 1.0 if len(self.calls) == 1 else 3.0
                now[0] = observed_at
                bid, ask = Decimal(49), Decimal(51)
                mid = (bid + ask) / 2
                self.books[ticker] = (
                    bid, Decimal(10), ask, Decimal(10))
                self.history[ticker].append((observed_at, mid))
                return mid, bid, ask, observed_at

            def top_of_book(self, ticker):
                return self.books.get(ticker, (None, None, None, None))

            def lifecycle(self, _ticker):
                return 10_000, False

            def stale_tickers(self, _tickers):
                return []

        class CaptureLog:
            def __init__(self):
                self.rows = []

            def tick(self, ticker, *_args, **kwargs):
                self.rows.append((kwargs["ts"], "quote", ticker))

            def event(self, ticker, event, **kwargs):
                self.rows.append((kwargs["ts"], event, ticker))

            def trade(self, *_args, **_kwargs):
                return None

            def end(self, **_kwargs):
                return None

        feed = InterleavedFeed()
        capture = CaptureLog()
        ctx = Context(
            cfg, feed, _Strategy(), Executor(
                cfg, None, feed, clock=lambda: now[0]),
            capture, Safety(cfg), clock=lambda: now[0])

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(
            ctx, None, ["A", "B"], sleep=stop_after_one_sweep,
            quote_tickers=("A", "B"))

        self.assertEqual(
            [timestamp for timestamp, _event, _ticker in capture.rows],
            [1.0, 3.0, 4.0])

    def test_same_quote_stop_preempts_due_time_exit_before_fill(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        feed.books["TRADE"] = (
            Decimal(40), Decimal(10), Decimal(42), Decimal(10))
        strategy = ScalpStrategy(cfg)
        strategy.positions["TRADE"] = Position(
            "TRADE", Decimal(50), Decimal(1), opened_at=0,
            entry_fee_usd=Decimal(0))
        fills = []
        original_record_fill = strategy.record_fill

        def record_fill(ticker, side, price, contracts, fee, now=None,
                        event_id=None):
            fills.append((ticker, side, price, contracts, fee, now))
            return original_record_fill(
                ticker, side, price, contracts, fee, now=now,
                event_id=event_id)

        strategy.record_fill = record_fill
        executor = Executor(cfg, None, feed, clock=lambda: 301)
        executor.submit_paper(
            "TRADE", "SELL", Decimal(1),
            "time exit 300s (+0c)", now=300)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 301)

        process_tick(
            ctx, "TRADE", Decimal(41), Decimal(40), Decimal(42),
            observed_at=301, decision_at=301,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0][1], "SELL")
        self.assertEqual(fills[0][2], Decimal(39))
        self.assertNotIn("TRADE", strategy.positions)

    def test_time_exit_cannot_fill_against_a_pre_deadline_quote(self):
        cfg = Config(
            sports=["Tennis"], max_hold_seconds=10, sim_latency_s=1,
            tp_trail_cents=0, espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        feed = _Feed()
        feed.books["TRADE"] = (
            Decimal(54), Decimal(10), Decimal(56), Decimal(10))
        strategy = ScalpStrategy(cfg)
        strategy.positions["TRADE"] = Position(
            "TRADE", Decimal(50), Decimal(1), opened_at=0,
            entry_fee_usd=Decimal(0))
        executor = Executor(cfg, None, feed, clock=lambda: 10.5)
        executor.submit_paper(
            "TRADE", "SELL", Decimal(1),
            "take-profit, bid +5c vs entry", now=8,
            limit_price=Decimal(55), entry_price=Decimal(50),
            entry_contracts=Decimal(1), entry_fee_usd=Decimal(0))
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 10.5)

        process_tick(
            ctx, "TRADE", Decimal(55), Decimal(54), Decimal(56),
            observed_at=9.5, decision_at=10.5,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )

        self.assertIn("TRADE", strategy.positions)
        pending = executor.get_pending("TRADE", side="SELL")
        self.assertIsNotNone(pending)
        self.assertTrue(pending.reason.startswith("time exit"))
        self.assertEqual(pending.due_at, 11.5)

    def test_pre_due_stop_upgrade_preserves_paid_latency(self):
        cfg = Config(
            sports=["Tennis"], tp_trail_cents=0,
            espn_gate_enabled=False, sibling_spike_enabled=False,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        strategy = ScalpStrategy(cfg)
        strategy.record_fill(
            "TRADE", "BUY", Decimal(50), Decimal(1), Decimal(0), now=0)
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 0)

        feed.books["TRADE"] = (
            Decimal(55), Decimal(10), Decimal(56), Decimal(10))
        process_tick(
            ctx, "TRADE", Decimal("55.5"), Decimal(55), Decimal(56),
            observed_at=0, decision_at=0,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )
        self.assertEqual(executor.get_pending("TRADE", "SELL").due_at, 1)

        feed.books["TRADE"] = (
            Decimal(40), Decimal(10), Decimal(41), Decimal(10))
        process_tick(
            ctx, "TRADE", Decimal("40.5"), Decimal(40), Decimal(41),
            observed_at=.5, decision_at=.5,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )

        upgraded = executor.get_pending("TRADE", "SELL")
        self.assertIn("stop-loss", upgraded.reason)
        self.assertEqual(upgraded.due_at, 1)
        self.assertEqual(upgraded.entry_price, Decimal(50))
        self.assertEqual(upgraded.entry_contracts, Decimal(1))
        self.assertEqual(upgraded.entry_fee_usd, Decimal(0))

    def test_fee_negative_partial_take_profit_sell_is_canceled(self):
        cfg = Config(
            sports=["Tennis"], tp_trail_cents=0,
            espn_gate_enabled=False, sibling_spike_enabled=False,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        feed.books["TRADE"] = (
            Decimal(58), Decimal("0.1"), Decimal(59), Decimal(20))
        strategy = ScalpStrategy(cfg)
        strategy.record_fill(
            "TRADE", "BUY", Decimal(53), Decimal(20),
            fee_usd(53, 20, side="BUY"), now=0)
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 0)
        disabled_gate = {
            "enabled": False, "allow": True,
            "reason": "score_gate_disabled",
        }
        disabled_siblings = {
            "enabled": False, "complete": True, "rises": (),
        }

        process_tick(
            ctx, "TRADE", Decimal("58.5"), Decimal(58), Decimal(59),
            observed_at=0, decision_at=0, gate_snapshot=disabled_gate,
            sibling_snapshot=disabled_siblings)
        self.assertTrue(executor.has_pending("TRADE", side="SELL"))

        process_tick(
            ctx, "TRADE", Decimal("58.5"), Decimal(58), Decimal(59),
            observed_at=1, decision_at=1, gate_snapshot=disabled_gate,
            sibling_snapshot=disabled_siblings)

        self.assertEqual(strategy.realized_pnl, Decimal(0))
        self.assertEqual(strategy.positions["TRADE"].contracts, Decimal(20))

    def test_replay_rejects_malformed_fair_value_evidence(self):
        from replay import _observation_detail

        base = {
            "market_role": "trade", "sweep_id": 1,
            "quote_phase": "execution", "decision_at": 2.0,
            "score_gate": {
                "enabled": True, "allow": True, "reason": "score_ok",
                "model_prob": "0.6", "market_prob": "0.5",
                "edge": "0.1", "model_1_prob": "0.61",
                "model_2_prob": "0.60",
            },
            "siblings": {
                "enabled": False, "complete": True, "rises": [],
            },
        }
        for field, invalid in (
                ("model_1_prob", "NaN"),
                ("model_2_prob", "1.1"),
                ("edge", "not-a-decimal"),
                ("prior_source_sha256", "short")):
            payload = json.loads(json.dumps(base))
            payload["score_gate"][field] = invalid
            with self.assertRaisesRegex(ValueError, field):
                _observation_detail(json.dumps(payload))

    def test_sibling_protection_requests_scoreboard_binding_even_without_preference(self):
        cfg = Config(
            sports=["Tennis"], prefer_scoreboard_bind=False,
            sibling_spike_enabled=True,
        )
        empty = DiscoveryResult(
            contracts=(), watch_contracts=(), selected_sports=("Tennis",),
            local_timezone="UTC",
            session_start_local="1970-01-01T00:00:00+00:00",
            session_end_local="1970-01-02T00:00:00+00:00",
            session_start_utc=0, session_end_utc=86_400, stats={})
        captured = {}
        binding_calls = []

        class Gate:
            def enabled(self):
                return True

            def binding_identity(self, **kwargs):
                binding_calls.append(kwargs)
                return (
                    "espn:match", "espn:athlete:a", "espn:athlete:b")

        def discover(_cfg, _client, **kwargs):
            captured.update(kwargs)
            return empty

        with patch("market_data.discover_game_contracts", discover):
            PriceFeed(cfg, client=None).discover(scoreboard_gate=Gate())

        binder = captured.get("bind_predicate")
        self.assertTrue(callable(binder))
        contract = SimpleNamespace(
            ticker="T", title="Ada Ace", game_title="Ace vs Break")
        self.assertEqual(
            binder(contract),
            ("espn:match", "espn:athlete:a", "espn:athlete:b"))
        self.assertEqual(binding_calls, [{
            "ticker": "T", "player_name": "Ada Ace",
            "event_title": "Ace vs Break",
        }])

    def test_discovery_model_ranking_receives_immutable_match_start(self):
        cfg = Config(
            sports=["Tennis"], prefer_scoreboard_bind=False,
            sibling_spike_enabled=False,
        )
        empty = DiscoveryResult(
            contracts=(), watch_contracts=(), selected_sports=("Tennis",),
            local_timezone="UTC",
            session_start_local="1970-01-01T00:00:00+00:00",
            session_end_local="1970-01-02T00:00:00+00:00",
            session_start_utc=0, session_end_utc=86_400, stats={})
        captured = {}
        score_calls = []

        class Gate:
            def enabled(self):
                return True

            def model_edge_score(self, **kwargs):
                score_calls.append(kwargs)
                return 1, Decimal("0.1")

        def discover(_cfg, _client, **kwargs):
            captured.update(kwargs)
            return empty

        with patch("market_data.discover_game_contracts", discover):
            PriceFeed(cfg, client=None).discover(scoreboard_gate=Gate())

        scorer = captured["sibling_score"]
        contract = SelectedContract(
            ticker="T", title="Will Ada Ace win the match?",
            game_title="Ada Ace vs Bea Break",
            bid=Decimal(49), ask=Decimal(50), bid_size=Decimal(10),
            ask_size=Decimal(10),
            provenance=ContractProvenance(
                sport="Tennis", league="ATP", series_ticker="KXATP",
                milestone_id="M", event_ticker="EVENT",
                scheduled_start_ts=10_000))

        self.assertEqual(scorer(contract), (1, Decimal("0.1")))
        self.assertEqual(score_calls[0]["scheduled_start_ts"], 10_000)

    def test_watch_quote_load_cannot_exceed_monitoring_cap(self):
        cfg = Config(sports=["Tennis"], max_monitored_markets=10)
        provenance = ContractProvenance(
            sport="Tennis", league="ATP", series_ticker="KXATP",
            milestone_id="M", event_ticker="EVENT",
            scheduled_start_ts=10_000)

        def contract(ticker):
            return SelectedContract(
                ticker=ticker, title=ticker, game_title="Match",
                bid=Decimal(49), ask=Decimal(50), bid_size=Decimal(10),
                ask_size=Decimal(10), provenance=provenance)

        discovery = DiscoveryResult(
            contracts=tuple(contract(f"TRADE-{i}") for i in range(10)),
            watch_contracts=(contract("WATCH"),),
            selected_sports=("Tennis",), local_timezone="UTC",
            session_start_local="1970-01-01T00:00:00+00:00",
            session_end_local="1970-01-02T00:00:00+00:00",
            session_start_utc=0, session_end_utc=86_400,
            stats={})

        with self.assertRaisesRegex(ValueError, "total quote cap"):
            PriceFeed(cfg, client=None).install_discovery(discovery)

    def test_due_buy_is_canceled_when_current_score_gate_blocks(self):
        cfg = Config(sports=["Tennis"], sibling_spike_enabled=False)
        cfg.sim_latency_s = 1
        feed = _Feed()
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        executor.submit_paper(
            "TRADE", "BUY", Decimal(1), "signal; score was good", now=0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 1, espn_gate=_Gate(False))

        process_tick(ctx, "TRADE", Decimal("49.5"), Decimal(49),
                     Decimal(50), observed_at=1)

        self.assertEqual(strategy.fills, [])
        self.assertFalse(executor.has_pending("TRADE", side="BUY"))

    def test_due_buy_rechecks_current_price_and_spread_bounds(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False, max_spread=3,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        feed.books["TRADE"] = (
            Decimal(10), Decimal(10), Decimal(50), Decimal(10))
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: 1)
        executor.submit_paper(
            "TRADE", "BUY", Decimal(1), "signal", now=0,
            limit_price=Decimal(50))
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 1)

        process_tick(
            ctx, "TRADE", Decimal(30), Decimal(10), Decimal(50),
            observed_at=1, decision_at=1,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )

        self.assertEqual(strategy.fills, [])
        self.assertFalse(executor.has_pending("TRADE", side="BUY"))

    def test_due_buy_cancels_before_fill_when_portfolio_loss_is_breached(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False, max_daily_loss_usd=2,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        feed.books["LOSS"] = (
            Decimal(20), Decimal(20), Decimal(22), Decimal(20))
        strategy = ScalpStrategy(cfg)
        strategy.positions["LOSS"] = Position(
            "LOSS", Decimal(50), Decimal(20), opened_at=0,
            entry_fee_usd=Decimal(0))
        executor = Executor(cfg, None, feed, clock=lambda: 1)
        executor.submit_paper(
            "TRADE", "BUY", Decimal(1), "signal", now=0,
            limit_price=Decimal(50))
        safety = Safety(cfg)
        ctx = Context(cfg, feed, strategy, executor, None, safety,
                      clock=lambda: 1)
        ctx.latest_bid["LOSS"] = Decimal(20)
        ctx.bid_ts["LOSS"] = 1

        process_tick(
            ctx, "TRADE", Decimal("49.5"), Decimal(49), Decimal(50),
            observed_at=1, decision_at=1,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )

        self.assertNotIn("TRADE", strategy.positions)
        self.assertFalse(executor.has_pending("TRADE", side="BUY"))
        self.assertIn("loss limit", safety.tripped_reason)

    def test_later_sibling_receipt_cannot_make_earlier_trade_quote_fill(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        executor.submit_paper("TRADE", "BUY", Decimal(1), "signal", now=0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 2)
        disabled_gate = {
            "enabled": False, "allow": True,
            "reason": "score_gate_disabled",
        }
        disabled_siblings = {
            "enabled": False, "complete": True, "rises": (),
        }

        # This trade quote arrived before due_at=1. A later sibling receipt
        # can delay the decision to t=2, but cannot turn the t=.5 book into a
        # post-latency fill observation.
        process_tick(
            ctx, "TRADE", Decimal("49.5"), Decimal(49), Decimal(50),
            observed_at=.5, decision_at=2,
            gate_snapshot=disabled_gate,
            sibling_snapshot=disabled_siblings,
        )
        self.assertEqual(strategy.fills, [])
        self.assertTrue(executor.has_pending("TRADE", side="BUY"))

        process_tick(
            ctx, "TRADE", Decimal("49.5"), Decimal(49), Decimal(50),
            observed_at=1.5, decision_at=2.5,
            gate_snapshot=disabled_gate,
            sibling_snapshot=disabled_siblings,
        )
        self.assertEqual(len(strategy.fills), 1)

    def test_due_buy_fill_timestamp_waits_for_completed_sweep(self):
        cfg = Config(
            sports=["Tennis"], espn_gate_enabled=False,
            sibling_spike_enabled=False,
        )
        cfg.sim_latency_s = 1
        feed = _Feed()
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        executor.submit_paper("TRADE", "BUY", Decimal(1), "signal", now=0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 2.5)

        process_tick(
            ctx, "TRADE", Decimal("49.5"), Decimal(49), Decimal(50),
            observed_at=1.5, decision_at=2.5,
            gate_snapshot={
                "enabled": False, "allow": True,
                "reason": "score_gate_disabled",
            },
            sibling_snapshot={
                "enabled": False, "complete": True, "rises": (),
            },
        )

        self.assertEqual(len(strategy.fills), 1)
        self.assertEqual(strategy.fills[0][-1], 2.5)

    def test_due_buy_is_canceled_when_close_horizon_changed(self):
        cfg = Config(sports=["Tennis"], sibling_spike_enabled=False)
        cfg.sim_latency_s = 1
        feed = _Feed()
        feed.close_times["TRADE"] = 350
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        executor.submit_paper("TRADE", "BUY", Decimal(1), "signal", now=0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 1, espn_gate=_Gate(True))

        process_tick(ctx, "TRADE", Decimal("49.5"), Decimal(49),
                     Decimal(50), observed_at=1)

        self.assertEqual(strategy.fills, [])
        self.assertFalse(executor.has_pending("TRADE", side="BUY"))

    def test_due_buy_is_canceled_when_ioc_arrival_is_stale(self):
        cfg = Config(sports=["Tennis"], sibling_spike_enabled=False)
        cfg.sim_latency_s = 1
        cfg.stale_data_s = 30
        feed = _Feed()
        strategy = _Strategy()
        executor = Executor(cfg, None, feed, clock=lambda: 0)
        executor.submit_paper("TRADE", "BUY", Decimal(1), "signal", now=0)
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 40, espn_gate=_Gate(True))

        process_tick(ctx, "TRADE", Decimal("49.5"), Decimal(49),
                     Decimal(50), observed_at=40)

        self.assertEqual(strategy.fills, [])
        self.assertFalse(executor.has_pending("TRADE", side="BUY"))

    def test_entry_fails_closed_when_sibling_protection_has_no_sibling(self):
        cfg = Config(sports=["Tennis"], espn_gate_enabled=False,
                     sibling_spike_enabled=True)
        feed = _Feed()
        strategy = _Strategy(entry=True)

        class RecordingExecutor:
            pending_paper = ()

            def __init__(self):
                self.submitted = []

            def has_pending(self, *_args, **_kwargs):
                return False

            def pending_count(self, *_args, **_kwargs):
                return 0

            def process_due_paper_orders(self, *_args, **_kwargs):
                return []

            def submit_paper(self, *args, **kwargs):
                self.submitted.append((args, kwargs))

        executor = RecordingExecutor()
        ctx = Context(cfg, feed, strategy, executor, None, Safety(cfg),
                      clock=lambda: 1)
        siblings = _sibling_snapshot(
            ctx, "TRADE", 1, observed_tickers={"TRADE"})

        process_tick(ctx, "TRADE", Decimal("49.5"), Decimal(49),
                     Decimal(50), observed_at=1,
                     sibling_snapshot=siblings)

        self.assertEqual(executor.submitted, [])

    def test_quote_sweep_observes_watch_sibling_before_trade_decision(self):
        cfg = Config(sports=["Tennis"], espn_gate_enabled=False,
                     sibling_spike_cents=15, sibling_spike_lookback_s=45)
        cfg.poll_interval = 0

        class SweepFeed(_Feed):
            def __init__(self):
                super().__init__()
                self.history["WATCH"].append((0, Decimal(5)))
                self.books["WATCH"] = (Decimal(39), Decimal(10),
                                       Decimal(41), Decimal(10))
                self.close_times["WATCH"] = 10_000
                self.calls = []
                self.trade_calls = 0

            def get_quote(self, ticker):
                self.calls.append(ticker)
                if ticker == "TRADE":
                    self.trade_calls += 1
                    observed_at = 1 if self.trade_calls == 1 else 3
                    self.history[ticker].append(
                        (observed_at, Decimal("49.5")))
                    return (Decimal("49.5"), Decimal(49), Decimal(50),
                            observed_at)
                self.history[ticker].append((2, Decimal(40)))
                return Decimal(40), Decimal(39), Decimal(41), 2

            def sibling_tickers(self, ticker):
                return ("WATCH",) if ticker == "TRADE" else ("TRADE",)

            def mid_rise_in_lookback(self, ticker, now, lookback):
                rows = [(ts, mid) for ts, mid in self.history[ticker]
                        if now - lookback <= ts <= now]
                if len(rows) < 2:
                    return Decimal(0)
                return rows[-1][1] - min(mid for _, mid in rows)

            def stale_tickers(self, _tickers):
                return []

        class RecordingExecutor:
            pending_paper = ()

            def __init__(self):
                self.submitted = []

            def has_pending(self, *_args, **_kwargs):
                return False

            def pending_count(self, *_args, **_kwargs):
                return 0

            def process_due_paper_orders(self, *_args, **_kwargs):
                return []

            def submit_paper(self, *args, **kwargs):
                self.submitted.append((args, kwargs))

            def cancel_pending_paper(self, *_args, **_kwargs):
                return []

        feed = SweepFeed()
        strategy = _Strategy(entry=True)
        executor = RecordingExecutor()
        safety = Safety(cfg)
        ctx = Context(cfg, feed, strategy, executor, None, safety,
                      clock=lambda: 3)

        def stop_after_one_sweep(_seconds):
            raise KeyboardInterrupt()

        run_loop(ctx, None, ["TRADE"], sleep=stop_after_one_sweep,
                 quote_tickers=("TRADE", "WATCH"))

        self.assertEqual(feed.calls, ["TRADE", "WATCH", "TRADE"])
        self.assertEqual(executor.submitted, [])

    def test_replay_uses_logged_gate_and_never_trades_watch_ticker(self):
        cfg = Config(sports=["Tennis"], tp_trail_cents=0)
        provenance = {
            ticker: ContractProvenance(
                sport="Tennis", league="ATP", series_ticker="KXATP",
                milestone_id="M", event_ticker="EVENT",
                scheduled_start_ts=10_000)
            for ticker in ("TRADE", "WATCH")
        }
        def detail(role, sweep, *, phase=None, allow=True, ask="61"):
            phase = phase or (
                "execution" if role == "trade" else "evidence")
            payload = {
                "market_role": role, "sweep_id": sweep,
                "quote_phase": phase,
                "decision_at": float(sweep * 2) + 0.1,
            }
            if phase == "execution":
                model = Decimal("0.8") if allow else Decimal("0.2")
                market = Decimal(ask) / Decimal(100)
                payload["score_gate"] = {
                    "enabled": True, "allow": allow,
                    "reason": ("score_ok" if allow
                               else "blocked:logged_score_change"),
                    "model_prob": str(model),
                    "market_prob": str(market),
                    "edge": str(model - market),
                    "model_1_prob": str(model + Decimal("0.01")),
                    "model_2_prob": str(model),
                    "prior_source_sha256": "a" * 64,
                    "prior_generated_at": "1970-01-01T00:00:00Z",
                    "prior_model_as_of": "1970-01-01T00:00:00Z",
                    "prior_match_start": "1970-01-01T02:46:40Z",
                    "prior_model_1_id": "MODEL-A",
                    "prior_model_2_id": "MODEL-B",
                    "prematch_model_1_prob": str(
                        model + Decimal("0.01")),
                    "prematch_model_2_prob": str(model),
                    "espn_match_id": "espn:M", "espn_player": "Player One",
                    "score_source": "espn", "score_match_id": "espn:M",
                    "score_athlete_id": "espn:athlete:one",
                    "score_opponent_id": "espn:athlete:two",
                    "score_player_name": "Player One",
                    "score_opponent_name": "Player Two",
                    "score_lifecycle_state": "pre",
                    "score_observed": False, "score_best_of": 3,
                    "score_sets_for": 0, "score_sets_against": 0,
                    "score_games_for": 0, "score_games_against": 0,
                    "gate_observed_at": str(
                        Decimal(sweep * 2) + Decimal("0.075")),
                }
                payload["siblings"] = {
                    "enabled": True, "complete": True,
                    "rises": [["WATCH", "0"]],
                }
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))

        def build_log(final_allow, session_id):
            log = ResearchLog(
                log_dir=tempfile.mkdtemp(), session_id=session_id,
                config=cfg, session_start=0,
                provenance_by_ticker=provenance,
                trade_tickers=("TRADE",), watch_tickers=("WATCH",),
                score_bindings_by_ticker={
                    "TRADE": (
                        "espn:M", "espn:athlete:one",
                        "espn:athlete:two", "Player One", "Player Two"),
                    "WATCH": (
                        "espn:M", "espn:athlete:two",
                        "espn:athlete:one", "Player Two", "Player One"),
                })
            for sweep in range(1, 21):
                base = float(sweep * 2)
                log.tick(
                    "WATCH", Decimal(40), Decimal(39), Decimal(41),
                    Decimal(100), Decimal(100), ts=base,
                    detail=detail("watch", sweep), close_ts=10_000,
                    can_close_early=False)
                log.tick(
                    "TRADE", Decimal(60), Decimal(59), Decimal(61),
                    Decimal(100), Decimal(100), ts=base + 0.05,
                    detail=detail("trade", sweep, phase="evidence"),
                    close_ts=10_000, can_close_early=False)
                log.tick(
                    "TRADE", Decimal(60), Decimal(59), Decimal(61),
                    Decimal(100), Decimal(100), ts=base + 0.1,
                    detail=detail(
                        "trade", sweep, allow=final_allow),
                    close_ts=10_000,
                    can_close_early=False)
            log.tick(
                "WATCH", Decimal(40), Decimal(39), Decimal(41),
                Decimal(100), Decimal(100), ts=42,
                detail=detail("watch", 21), close_ts=10_000,
                can_close_early=False)
            log.tick(
                "TRADE", Decimal(52), Decimal(51), Decimal(53),
                Decimal(100), Decimal(100), ts=42.05,
                detail=detail("trade", 21, phase="evidence"),
                close_ts=10_000, can_close_early=False)
            log.tick(
                "TRADE", Decimal(52), Decimal(51), Decimal(53),
                Decimal(100), Decimal(100), ts=42.1,
                detail=detail(
                    "trade", 21, allow=final_allow, ask="53"),
                close_ts=10_000,
                can_close_early=False)
            log.tick(
                "WATCH", Decimal(40), Decimal(39), Decimal(41),
                Decimal(100), Decimal(100), ts=44,
                detail=detail("watch", 22), close_ts=10_000,
                can_close_early=False)
            log.tick(
                "TRADE", Decimal(52), Decimal(51), Decimal(53),
                Decimal(100), Decimal(100), ts=44.05,
                detail=detail("trade", 22, phase="evidence"),
                close_ts=10_000, can_close_early=False)
            log.tick(
                "TRADE", Decimal(52), Decimal(51), Decimal(53),
                Decimal(100), Decimal(100), ts=44.1,
                detail=detail(
                    "trade", 22, allow=final_allow, ask="53"),
                close_ts=10_000,
                can_close_early=False)
            if final_allow:
                log.trade(
                    "TRADE", "BUY", Decimal(53), Decimal(20),
                    "dip 8.0c; entry ask 53c, size 20, projected net "
                    "$+0.3000; score_ok",
                    fee=Decimal("0.3500"), ts=44.1)
            log.end(clean=True, reason="operator interrupt", ts=45)
            return log.tick_path

        # Both sides prove replay consumes the recorded decision: it neither
        # bypasses a block nor invents a live-gate block for an allowed row.
        allowed = replay(build_log(True, "runtime-allow"), cfg=cfg)
        blocked = replay(build_log(False, "runtime-block"), cfg=cfg)

        self.assertEqual(
            [(ticker, side) for ticker, side, _price, _qty
             in allowed["trades"]],
            [("TRADE", "BUY")])
        self.assertEqual(blocked["trades"], [])
        self.assertEqual(allowed["trade_tickers"], ("TRADE",))


if __name__ == "__main__":
    unittest.main()
