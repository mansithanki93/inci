# Inci Terminal Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free, in-place terminal dashboard that continuously shows Inci's complete paper-session market, decision, order, position, P&L, safety, and heartbeat state.

**Architecture:** A new `TerminalDashboard` is an optional runtime reporter and renderer. Existing strategy, execution, and safety branches publish decisions and events through that reporter, while the renderer reads only existing in-memory state and never calls Kalshi. The ordinary print path remains active for tests, redirected output, and unsupported terminals.

**Tech Stack:** Python 3.12 standard library, ANSI terminal control, existing custom `tests.py` assertion suite.

## Global Constraints

- Do not add third-party packages.
- Do not add API calls, sleeps, threads, or asynchronous work.
- Cap dashboard redraws at four per second and skip unchanged frames.
- Preserve ordinary output when stdout is not a TTY or `TERM=dumb`.
- A dashboard failure must fall back to ordinary output and must not halt the research loop.
- Missing values display as `--`; they must never be converted to zero.
- Decision explanations must be emitted by the exact branch that controls trading, not independently reconstructed by the renderer.
- Open-position P&L must share the risk engine's existing depth-, fee-, and slippage-aware calculation.
- Durable v6 research logging and provenance remain unchanged.
- Do not weaken code-fingerprint validation or add the presentation-only
  `terminal_dashboard.py` to `REPLAY_CODE_FILES`.
- `REAL_ORDER_EXECUTION_ENABLED = False`, `--live`, and `--demo` remain locked.
- Do not commit changes. The user will manually upload the changed files to GitHub.

---

### Task 1: Expose exact decision and position-mark state

**Files:**
- Modify: `signals.py:1-14`
- Modify: `strategy.py:15-126`
- Modify: `engine.py:12-188`
- Modify: `tests.py:1307-2335`

**Interfaces:**
- Consumes: existing `Position`, `Context`, `fee_usd`, and top-of-book values.
- Produces:
  - `current_dip(history, now_ts, current_mid, lookback_seconds)`.
  - `PositionOpenMark` frozen dataclass in `engine.py`.
  - `position_open_mark(ctx, ticker, position) -> PositionOpenMark`.
  - `ScalpStrategy(..., reporter=None)`.
  - `ScalpStrategy.session_realized_pnl`, reset only when a process session
    starts.
  - Reporter call `decision(ticker, state, reason, ts=None)`.

- [ ] **Step 1: Write failing regressions for shared dip and position P&L**

Add:

```python
class RolloverLedger:
    def __init__(
            self, before_day, after_day, rollover_at, starting_total):
        self.before_day = before_day
        self.after_day = after_day
        self.rollover_at = rollover_at
        self.starting_total = starting_total

    def utc_day(self, now):
        return self.before_day if now < self.rollover_at else self.after_day

    def today_total(self, now):
        return (
            self.starting_total
            if now < self.rollover_at else Decimal("0"))

    def record(self, pnl, ts=None, event_id=None):
        return None


def test_shared_current_dip_matches_entry_signal():
    from signals import current_dip, dip_signal

    history = [
        (60.0, Decimal("56")),
        (80.0, Decimal("54")),
    ]
    assert current_dip(
        history, 100.0, Decimal("52"), 45) == Decimal("4")
    assert dip_signal(
        history, 100.0, Decimal("52"), Decimal("7"), 45) is None
    assert dip_signal(
        history, 100.0, Decimal("52"), Decimal("4"), 45) == Decimal("4")


def test_position_open_marks_sum_to_risk_total():
    from engine import position_open_mark, open_pnl_usd

    class Feed:
        def top_of_book(self, ticker):
            return (
                Decimal("51"), Decimal("3"),
                Decimal("53"), Decimal("8"))

    cfg = Config()
    strategy = ScalpStrategy(cfg)
    strategy.record_fill(
        "T", "BUY", Decimal("52"), Decimal("5"),
        Decimal("0.10"), now=90.0)
    ctx = Context(
        cfg, Feed(), strategy, executor=None, log=None,
        safety=Safety(cfg), clock=lambda: 100.0)
    ctx.latest_bid["T"] = Decimal("51")
    marks = [
        position_open_mark(ctx, ticker, position)
        for ticker, position in ctx.strategy.positions.items()
    ]

    assert all(mark.valued for mark in marks)
    assert sum((mark.pnl_usd for mark in marks), Decimal("0")) \
        == open_pnl_usd(ctx)
    assert marks[0].executable_contracts <= \
        ctx.strategy.positions[marks[0].ticker].contracts


def test_session_realized_pnl_survives_utc_day_rollover():
    ledger = RolloverLedger(
        before_day="2026-07-27", after_day="2026-07-28",
        rollover_at=200.0, starting_total=Decimal("-2.00"))
    strategy = ScalpStrategy(Config(), ledger=ledger, now=100.0)
    strategy.record_fill(
        "T", "BUY", Decimal("50"), Decimal("1"), Decimal("0"), now=100.0)
    strategy.record_fill(
        "T", "SELL", Decimal("55"), Decimal("1"), Decimal("0"), now=201.0)

    assert strategy.realized_pnl == Decimal("0.05")
    assert strategy.session_realized_pnl == Decimal("0.05")
```

- [ ] **Step 2: Run the focused regression and confirm the missing interface**

Run:

```bash
python -c "import tests; tests.test_shared_current_dip_matches_entry_signal(); tests.test_position_open_marks_sum_to_risk_total(); tests.test_session_realized_pnl_survives_utc_day_rollover()"
```

Expected: import failure because `current_dip` and `position_open_mark` do not
exist.

- [ ] **Step 3: Extract shared dip observation and position-mark calculations**

Add to `signals.py` and make `dip_signal` call it:

```python
def current_dip(history, now_ts, current_mid, lookback_seconds):
    window = [
        mid for ts, mid in history
        if 0 < now_ts - ts <= lookback_seconds
    ]
    return None if not window else max(window) - current_mid


def dip_signal(
        history, now_ts, current_mid, dip_threshold, lookback_seconds):
    dip = current_dip(
        history, now_ts, current_mid, lookback_seconds)
    return dip if dip is not None and dip >= dip_threshold else None
```

Add to `engine.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PositionOpenMark:
    ticker: str
    bid: Decimal | None
    bid_depth: Decimal | None
    executable_contracts: Decimal
    exit_price: Decimal | None
    pnl_usd: Decimal
    valued: bool


def position_open_mark(ctx, ticker, position):
    bid = ctx.latest_bid.get(ticker)
    if bid is None:
        return PositionOpenMark(
            ticker, None, None, Decimal("0"), None, Decimal("0"), False)

    _, raw_depth, _, _ = (
        ctx.feed.top_of_book(ticker)
        if ctx.feed is not None else (bid, None, None, None)
    )
    depth = (None if raw_depth is None
             else max(Decimal("0"), Decimal(str(raw_depth))))
    contracts = Decimal(str(position.contracts))
    executable = min(
        contracts, Decimal("0") if depth is None else depth)
    exit_price = max(
        Decimal("0"),
        Decimal(str(bid)) - Decimal(str(ctx.cfg.sim_slippage_cents)))
    proceeds = exit_price * executable / Decimal("100")
    exit_fee = (
        fee_usd(
            exit_price, executable, side="SELL",
            balance_precision_usd=ctx.cfg.balance_precision_usd)
        if executable else Decimal("0")
    )
    cost = (
        Decimal(str(position.entry_price)) * contracts / Decimal("100")
        + Decimal(str(position.entry_fee_usd))
    )
    return PositionOpenMark(
        ticker, Decimal(str(bid)), depth, executable, exit_price,
        proceeds - exit_fee - cost, True)
```

Replace the body of `open_pnl_usd` with a sum over
`position_open_mark(...).pnl_usd`. Preserve the existing zero contribution when
the bid is missing; `check_loss_limit` remains responsible for halting
unpriceable exposure.

Initialize `ScalpStrategy.session_realized_pnl = Decimal("0")` for every new
process session. Increment it by the same net `pnl` as `realized_pnl` in the
SELL-fill branch. Do not reset it inside `refresh_daily_pnl`; UTC-day rollover
must reset only the daily risk value.

- [ ] **Step 4: Run the focused P&L regression**

Run:

```bash
python -c "import tests; tests.test_shared_current_dip_matches_entry_signal(); tests.test_position_open_marks_sum_to_risk_total(); tests.test_session_realized_pnl_survives_utc_day_rollover()"
```

Expected: PASS.

- [ ] **Step 5: Write failing regressions for exact decision reporting**

Add:

```python
from collections import defaultdict
from types import SimpleNamespace


class ReporterSpy:
    def __init__(self):
        self.decisions = {}
        self.events = []

    def decision(self, ticker, state, reason, ts=None):
        self.decisions[ticker] = SimpleNamespace(
            ticker=ticker, state=state, reason=reason, ts=ts)

    def emit(
            self, category, message, ticker=None, ts=None,
            important=False):
        self.events.append(SimpleNamespace(
            category=category, message=message, ticker=ticker,
            ts=ts, important=important))

    def last(self, ticker):
        return self.decisions[ticker]


class PaperTestFeed:
    def __init__(self, ask_depth=Decimal("20")):
        self.history = defaultdict(list)
        self.book = {}
        self.ask_depth = ask_depth

    def apply(self, ticker, ts, mid, bid, ask):
        self.history[ticker].append((ts, mid))
        self.book[ticker] = (
            bid, Decimal("20"), ask, self.ask_depth)

    def top_of_book(self, ticker):
        return self.book.get(
            ticker, (None, None, None, None))


def _paper_context_with_reporter(
        reporter=None, ask_depth=Decimal("20"),
        contracts_per_trade=Decimal("2")):
    cfg = Config()
    cfg.contracts_per_trade = contracts_per_trade
    feed = PaperTestFeed(ask_depth=ask_depth)
    feed.history["T"].append((60.0, Decimal("60")))
    strategy = ScalpStrategy(cfg, reporter=reporter)
    executor = Executor(cfg, None, feed, clock=lambda: 100.0)
    safety = Safety(cfg)
    return Context(
        cfg, feed, strategy, executor, None, safety,
        clock=lambda: 100.0, reporter=reporter)


def _paper_tick(ctx, ts, mid, bid, ask):
    ctx.feed.apply("T", ts, mid, bid, ask)
    process_tick(
        ctx, "T", mid, bid, ask, observed_at=ts)


def test_strategy_reports_exact_wait_and_action_reasons():
    reporter = ReporterSpy()
    cfg = Config()
    strategy = ScalpStrategy(cfg, reporter=reporter)

    strategy.check_entry(
        "WIDE", [], 100.0, Decimal("50"),
        Decimal("47"), Decimal("51"))
    assert reporter.last("WIDE").state == "WATCH"
    assert "spread" in reporter.last("WIDE").reason.lower()

    history = [(60.0, Decimal("60"))]
    signal = strategy.check_entry(
        "DIP", history, 100.0, Decimal("52"),
        Decimal("51"), Decimal("53"))
    assert signal["action"] == "BUY"
    assert reporter.last("DIP").state == "BUY SIGNAL"
    assert reporter.last("DIP").reason == signal["reason"]


def test_engine_reports_non_strategy_wait_gates():
    reporter = ReporterSpy()
    ctx = _paper_context_with_reporter(reporter=reporter)
    ctx.executor.submit_paper(
        "T", "BUY", Decimal("2"), "existing pending", now=99.5)

    _paper_tick(
        ctx, 100.0, Decimal("50"), Decimal("49"), Decimal("51"))

    assert reporter.last("T").state == "BUY PENDING"
    assert "fresh quote" in reporter.last("T").reason.lower()
```

- [ ] **Step 6: Run both decision regressions and confirm failure**

Run:

```bash
python -c "import tests; tests.test_strategy_reports_exact_wait_and_action_reasons(); tests.test_engine_reports_non_strategy_wait_gates()"
```

Expected: FAIL because strategy/context do not accept a reporter.

- [ ] **Step 7: Add optional decision reporting without changing return values**

Change `ScalpStrategy.__init__` to:

```python
def __init__(self, config, ledger=None, now=None, reporter=None):
    self.cfg = config
    self.ledger = ledger
    self.reporter = reporter
    self.positions = {}
    self._ledger_day = (
        ledger.utc_day(now) if ledger is not None else None)
    self.realized_pnl = (
        ledger.today_total(now) if ledger is not None else Decimal("0"))
    self.session_realized_pnl = Decimal("0")
```

Add:

```python
def _decision(self, ticker, state, reason, ts=None):
    if self.reporter is not None:
        self.reporter.decision(ticker, state, reason, ts=ts)
```

Call `_decision` immediately before every `check_entry` rejection and every
BUY signal. Use `current_dip` to report the observed dip even when it has not
reached the threshold. For an open position, call `_decision` from `check_exit`
with `OPEN` and a reason containing current bid movement, holding time, target,
and stop. Report SELL signals as `SELL SIGNAL`.

Add `reporter=None` to the end of `Context.__init__`, save it as
`self.reporter`, and report the engine-owned gates:

```python
ctx.reporter.decision(ticker, "BUY PENDING",
                      "waiting for the first fresh quote after latency", ts=now)
ctx.reporter.decision(ticker, "WATCH",
                      "maximum open-position capacity reached", ts=now)
ctx.reporter.decision(ticker, "WATCH",
                      "inside the configured close buffer", ts=now)
ctx.reporter.decision(ticker, "WATCH",
                      "market may close early", ts=now)
```

Guard each call when `ctx.reporter is None`. Do not change the existing
BUY/SELL dictionaries or `None` return values.

- [ ] **Step 8: Run all five Task 1 regressions**

Run:

```bash
python -c "import tests; tests.test_shared_current_dip_matches_entry_signal(); tests.test_position_open_marks_sum_to_risk_total(); tests.test_session_realized_pnl_survives_utc_day_rollover(); tests.test_strategy_reports_exact_wait_and_action_reasons(); tests.test_engine_reports_non_strategy_wait_gates()"
```

Expected: PASS.

- [ ] **Step 9: Run the existing risk and signal regressions**

Run:

```bash
python -c "import tests; tests.test_open_loss_limit_fee_aware(); tests.test_open_loss_mark_respects_zero_depth(); tests.test_pending_entries_count_toward_position_limit(); tests.test_replay_exact_paper_path_and_residual()"
```

Expected: PASS.

- [ ] **Step 10: Review the task diff without committing**

Run:

```bash
git diff --check -- signals.py strategy.py engine.py tests.py
git status --short -- signals.py strategy.py engine.py tests.py
```

Expected: no whitespace errors; only the intended files are listed.

---

### Task 2: Build the dependency-free renderer and fallback reporter

**Files:**
- Create: `terminal_dashboard.py`
- Modify: `tests.py:1307-2335`

**Interfaces:**
- Consumes: `Context`, `PriceFeed` discovery metadata, `PositionOpenMark`,
  pending paper orders, safety state, and research-log paths.
- Produces:
  - `TerminalDashboard(stream=None, clock=time.time, monotonic=time.monotonic, terminal_size=shutil.get_terminal_size, refresh_interval_s=0.25, enabled=None, session_started_at=None)`.
  - `active: bool`.
  - `emit(category, message, ticker=None, ts=None, important=False)`.
  - `decision(ticker, state, reason, ts=None)`.
  - `start_sweep(number)`.
  - `start_request(index, total, ticker)`.
  - `finish_request(elapsed_s)`.
  - `render(ctx, tickers, force=False) -> bool`.
  - `finish(ctx, tickers)`.

- [ ] **Step 1: Write failing tests for TTY detection and plain fallback**

Add:

```python
import io


class FakeStream(io.StringIO):
    def __init__(self, is_tty, fail_writes=False):
        super().__init__()
        self._is_tty = is_tty
        self.fail_writes = fail_writes

    def isatty(self):
        return self._is_tty

    def write(self, text):
        if self.fail_writes:
            raise OSError("simulated terminal failure")
        return super().write(text)


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def test_terminal_reporter_plain_fallback_preserves_messages():
    from terminal_dashboard import TerminalDashboard

    stream = FakeStream(is_tty=False)
    dashboard = TerminalDashboard(stream=stream, enabled=None)
    dashboard.emit("SIGNAL", "[signal] BUY T: dip 7c", ticker="T")

    assert not dashboard.active
    assert stream.getvalue() == "[signal] BUY T: dip 7c\n"
    assert "\x1b[" not in stream.getvalue()
```

- [ ] **Step 2: Run the fallback test and confirm the module is absent**

Run:

```bash
python -c "import tests; tests.test_terminal_reporter_plain_fallback_preserves_messages()"
```

Expected: `ModuleNotFoundError` for `terminal_dashboard`.

- [ ] **Step 3: Create the reporter state and fallback path**

Create `terminal_dashboard.py` with:

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import os
import shutil
import sys
import time


@dataclass(frozen=True)
class DecisionView:
    state: str
    reason: str
    updated_at: float


@dataclass(frozen=True)
class RuntimeEvent:
    ts: float
    category: str
    message: str
    ticker: str | None
    important: bool


class TerminalDashboard:
    MAX_EVENTS = 8

    def __init__(
            self, stream=None, clock=time.time, monotonic=time.monotonic,
            terminal_size=shutil.get_terminal_size,
            refresh_interval_s=0.25, enabled=None,
            session_started_at=None):
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock
        self.monotonic = monotonic
        self.terminal_size = terminal_size
        self.refresh_interval_s = refresh_interval_s
        detected = (
            callable(getattr(self.stream, "isatty", None))
            and self.stream.isatty()
            and os.environ.get("TERM", "") != "dumb"
        )
        self.active = detected if enabled is None else bool(enabled)
        self.session_started_at = (
            self.clock() if session_started_at is None
            else float(session_started_at)
        )
        self.decisions = {}
        self.events = []
        self.sweep = 0
        self.request_index = 0
        self.request_total = 0
        self.current_ticker = None
        self.request_elapsed_s = None
        self._last_render_at = None
        self._last_frame = None
        self._last_state_key = None

    def emit(self, category, message, ticker=None, ts=None, important=False):
        timestamp = self.clock() if ts is None else float(ts)
        if not self.active:
            try:
                print(message, file=self.stream)
            except (OSError, UnicodeError):
                pass
            return
        event = RuntimeEvent(
            timestamp, str(category), str(message), ticker, bool(important))
        self._append_event(event)

    def decision(self, ticker, state, reason, ts=None):
        timestamp = self.clock() if ts is None else float(ts)
        self.decisions[ticker] = DecisionView(state, reason, timestamp)

    def _append_event(self, event):
        if len(self.events) >= self.MAX_EVENTS:
            remove_at = 0
            if event.important:
                remove_at = next(
                    (index for index, existing in enumerate(self.events)
                     if not existing.important),
                    0)
            self.events.pop(remove_at)
        self.events.append(event)

    def start_sweep(self, number):
        self.sweep = int(number)

    def start_request(self, index, total, ticker):
        self.request_index = int(index)
        self.request_total = int(total)
        self.current_ticker = ticker
        self.request_elapsed_s = None

    def finish_request(self, elapsed_s):
        self.request_elapsed_s = max(0.0, float(elapsed_s))
```

- [ ] **Step 4: Run the fallback test**

Run:

```bash
python -c "import tests; tests.test_terminal_reporter_plain_fallback_preserves_messages()"
```

Expected: PASS.

- [ ] **Step 5: Write failing frame-content and status-precedence tests**

Add:

```python
from strategy import Position
from executor import PendingPaperOrder


class DashboardFeed:
    def __init__(self, contracts, now=100.0):
        from collections import defaultdict
        from types import SimpleNamespace

        self.client = SimpleNamespace(calls=0)
        self.contracts_by_ticker = {
            contract.ticker: contract for contract in contracts}
        self.provenance_by_ticker = {
            contract.ticker: contract.provenance for contract in contracts}
        self.history = defaultdict(list)
        self.last_book = {}
        self.last_good = {}
        for contract in contracts:
            ticker = contract.ticker
            self.history[ticker] = [
                (now - 20.0, Decimal("55")),
                (now, Decimal("50")),
            ]
            self.last_book[ticker] = (
                Decimal("49"), Decimal("20"),
                Decimal("51"), Decimal("15"))
            self.last_good[ticker] = now

    def top_of_book(self, ticker):
        return self.last_book.get(
            ticker, (None, None, None, None))


def _position(ticker):
    return Position(
        ticker=ticker, entry_price=Decimal("52"),
        contracts=Decimal("2"), opened_at=80.0,
        entry_fee_usd=Decimal("0.02"))


def _dashboard_context_with_ten_contracts(long_names=False):
    contracts = []
    for index in range(10):
        sport = "Tennis" if index % 2 == 0 else "Basketball"
        title = (
            "A deliberately long contract title for truncation"
            if long_names else f"Contract {index}")
        game = (
            "A deliberately long game name for terminal-width testing"
            if long_names else f"Game {index}")
        contracts.append(_task4_contract(
            ticker=f"T{index}", event_ticker=f"E{index}",
            sport=sport, title=title, game_title=game))
    feed = DashboardFeed(contracts)
    cfg = Config(sports=["Tennis", "Basketball"])
    strategy = ScalpStrategy(cfg)
    executor = Executor(cfg, None, feed)
    safety = Safety(cfg)
    log = SimpleNamespace(
        starting_pnl=Decimal("0"),
        tick_path="logs/ticks_v6_example.csv",
        trade_path="logs/trades_v6_example.csv")
    ctx = Context(
        cfg, feed, strategy, executor, log, safety,
        clock=lambda: 100.0)
    for ticker in feed.last_book:
        ctx.latest_bid[ticker] = feed.last_book[ticker][0]
        ctx.bid_ts[ticker] = 100.0
    return ctx, tuple(feed.contracts_by_ticker)


def test_terminal_dashboard_renders_all_contracts_and_sections():
    from terminal_dashboard import TerminalDashboard

    ctx, tickers = _dashboard_context_with_ten_contracts()
    stream = FakeStream(is_tty=True)
    dashboard = TerminalDashboard(
        stream=stream, enabled=True,
        terminal_size=lambda fallback=None: os.terminal_size((132, 40)))
    dashboard.decision(
        tickers[0], "WATCH", "waiting for a larger dip", ts=100.0)

    assert dashboard.render(ctx, tickers, force=True)
    frame = stream.getvalue()
    assert "INCI v6" in frame
    assert "WATCHLIST" in frame
    assert "LIVE DECISIONS" in frame
    assert "OPEN POSITIONS" in frame
    assert "PENDING ORDERS" in frame
    assert "RECENT ACTIVITY" in frame
    assert all(ticker in frame for ticker in tickers)
    assert ctx.log.tick_path in frame
    assert ctx.log.trade_path in frame


def test_dashboard_maps_pending_open_quarantine_and_no_quote_states():
    from terminal_dashboard import TerminalDashboard

    ctx, tickers = _dashboard_context_with_ten_contracts()
    ctx.executor.pending_paper.extend([
        PendingPaperOrder(tickers[0], "BUY", Decimal("2"), 101.0, "dip"),
        PendingPaperOrder(tickers[1], "SELL", Decimal("1"), 101.0, "target"),
    ])
    ctx.strategy.positions[tickers[1]] = _position(tickers[1])
    ctx.safety.quarantined.add(tickers[2])
    ctx.feed.last_book.pop(tickers[3], None)

    text = TerminalDashboard(enabled=True).build_frame(ctx, tickers, 132)
    assert "BUY PENDING" in text
    assert "EXIT PENDING" in text
    assert "QUARANTINED" in text
    assert "NO QUOTE" in text
```

Derive state from authoritative objects, using this precedence: `HALT`,
`QUARANTINED`, pending SELL (`EXIT PENDING`), open position (`OPEN`), pending
BUY (`BUY PENDING`), never quoted (`NO QUOTE`), then `WATCH`. Decision records
provide the explanation and observed dip; they do not override authoritative
exposure or safety state.

- [ ] **Step 6: Implement complete read-only frame construction**

Implement `build_frame(ctx, tickers, width)` with these sections and values:

```text
INCI v6 / PAPER / RUNNING or HALTED / selected Sports / uptime
sweep / request index and total / current ticker / request latency / update time
market-state counts / API error count and limit
session realized / UTC-day realized / conservative open / risk total / loss limit
WATCHLIST
LIVE DECISIONS
OPEN POSITIONS
PENDING ORDERS
RECENT ACTIVITY
tick CSV / trade CSV / Ctrl-C instruction
```

Read session realized from the strategy's process-local accumulator:

```python
session_realized = ctx.strategy.session_realized_pnl
```

Do not subtract `ctx.log.starting_pnl`: that calculation becomes wrong if the
session crosses UTC midnight. Derive the risk total as:

```python
risk_total = ctx.strategy.realized_pnl + open_pnl_usd(ctx)
```

Read market title/game/Sport from `feed.contracts_by_ticker` and
`feed.provenance_by_ticker`. Read executable values only from
`feed.last_book`; never display the discovery snapshot as a current quote.
Use `signals.current_dip` with the latest history row separated from its prior
rows to populate `current dip / configured dip`; do not duplicate its window
calculation in the renderer.

Use `position_open_mark` for each position row. Show `None` when the positions
or pending sections are empty. Use a two-line compact representation below
100 columns and enforce `len(visible_line) <= width` for every line.

- [ ] **Step 7: Run the content and state tests**

Run:

```bash
python -c "import tests; tests.test_terminal_dashboard_renders_all_contracts_and_sections(); tests.test_dashboard_maps_pending_open_quarantine_and_no_quote_states()"
```

Expected: PASS.

- [ ] **Step 8: Write failing tests for width, throttling, and event priority**

Add:

```python
def test_terminal_dashboard_narrow_width_never_wraps():
    from terminal_dashboard import TerminalDashboard

    ctx, tickers = _dashboard_context_with_ten_contracts(long_names=True)
    frame = TerminalDashboard(enabled=True).build_frame(ctx, tickers, 72)

    assert all(len(line) <= 72 for line in frame.splitlines())
    assert all(ticker in frame for ticker in tickers)


def test_terminal_dashboard_throttles_and_skips_unchanged_frames():
    from terminal_dashboard import TerminalDashboard

    ctx, tickers = _dashboard_context_with_ten_contracts()
    monotonic = MutableClock(10.0)
    stream = FakeStream(is_tty=True)
    dashboard = TerminalDashboard(
        stream=stream, enabled=True, monotonic=monotonic,
        refresh_interval_s=0.25)

    assert dashboard.render(ctx, tickers, force=True)
    first_size = len(stream.getvalue())
    monotonic.value = 10.10
    assert not dashboard.render(ctx, tickers)
    monotonic.value = 10.30
    assert not dashboard.render(ctx, tickers)
    assert len(stream.getvalue()) == first_size


def test_terminal_dashboard_prioritizes_safety_events_without_network_calls():
    from terminal_dashboard import TerminalDashboard

    ctx, tickers = _dashboard_context_with_ten_contracts()
    before = ctx.feed.client.calls
    dashboard = TerminalDashboard(enabled=True)
    for index in range(8):
        dashboard.emit("QUOTE", f"quote {index}", ts=float(index))
    dashboard.emit(
        "HALT", "loss limit reached", ts=9.0, important=True)

    frame = dashboard.build_frame(ctx, tickers, 120)
    assert "loss limit reached" in frame
    assert len(dashboard.events) == 8
    assert ctx.feed.client.calls == before
```

- [ ] **Step 9: Implement bounded ANSI rendering**

Implement:

```python
def render(self, ctx, tickers, force=False):
    if not self.active:
        return False
    now = self.monotonic()
    if (not force and self._last_render_at is not None
            and now - self._last_render_at < self.refresh_interval_s):
        return False
    try:
        width = max(
            20, self.terminal_size(fallback=(120, 40)).columns)
        state_key = self._state_key(ctx, tuple(tickers), width)
        if not force and state_key == self._last_state_key:
            self._last_render_at = now
            return False
        frame = self.build_frame(ctx, tuple(tickers), width)
        self.stream.write("\x1b[H\x1b[J" + frame)
        self.stream.flush()
    except Exception as error:
        self.active = False
        try:
            print(
                f"[dashboard] disabled after terminal error: {error}",
                file=self.stream)
        except (OSError, UnicodeError):
            pass
        return False
    self._last_frame = frame
    self._last_state_key = state_key
    self._last_render_at = now
    return True

def finish(self, ctx, tickers):
    if not self.active:
        return
    try:
        self.render(ctx, tickers, force=True)
        self.stream.write("\n")
        self.stream.flush()
    except Exception:
        pass
    finally:
        self.active = False
```

`_state_key` includes terminal width, books, histories, decisions, events,
positions, pending orders, safety state, and sweep/request progress. It excludes
display-only wall-clock text so unchanged state really is skipped.

Implement it as immutable in-memory values only:

```python
def _state_key(self, ctx, tickers, width):
    books = tuple(
        (ticker, tuple(ctx.feed.top_of_book(ticker)),
         tuple(ctx.feed.history.get(ticker, ())),
         ctx.feed.last_good.get(ticker))
        for ticker in tickers)
    positions = tuple(sorted(
        (ticker, position.entry_price, position.contracts,
         position.opened_at, position.entry_fee_usd)
        for ticker, position in ctx.strategy.positions.items()))
    pending = tuple(
        (order.ticker, order.side, order.contracts,
         order.due_at, order.reason)
        for order in ctx.executor.pending_paper)
    decisions = tuple(sorted(self.decisions.items()))
    events = tuple(self.events)
    safety = (
        ctx.safety.tripped_reason,
        tuple(sorted(ctx.safety.quarantined)),
        ctx.safety.consec_errors,
        tuple(sorted(ctx.safety.per_ticker.items())),
    )
    return (
        width, books, positions, pending, decisions, events, safety,
        ctx.strategy.realized_pnl,
        ctx.strategy.session_realized_pnl,
        self.sweep, self.request_index, self.request_total,
        self.current_ticker, self.request_elapsed_s,
    )
```

`render` and `finish` must never call `sleep`.

- [ ] **Step 10: Run all six Task 2 tests**

Run:

```bash
python -c "import tests; tests.test_terminal_reporter_plain_fallback_preserves_messages(); tests.test_terminal_dashboard_renders_all_contracts_and_sections(); tests.test_dashboard_maps_pending_open_quarantine_and_no_quote_states(); tests.test_terminal_dashboard_narrow_width_never_wraps(); tests.test_terminal_dashboard_throttles_and_skips_unchanged_frames(); tests.test_terminal_dashboard_prioritizes_safety_events_without_network_calls()"
```

Expected: PASS.

- [ ] **Step 11: Review the task diff without committing**

Run:

```bash
git diff --check -- terminal_dashboard.py tests.py
git status --short -- terminal_dashboard.py tests.py
```

Expected: no whitespace errors; `terminal_dashboard.py` is new and `tests.py`
is modified.

---

### Task 3: Route runtime events through the reporter

**Files:**
- Modify: `executor.py:43-136`
- Modify: `safety.py:13-93`
- Modify: `strategy.py:23-126`
- Modify: `engine.py:14-145`
- Modify: `tests.py:1307-2335`

**Interfaces:**
- Consumes: `TerminalDashboard.emit`, `TerminalDashboard.decision`, and the
  optional reporter fields created in Tasks 1 and 2.
- Produces:
  - `Executor(..., reporter=None)`.
  - `Safety(config, reporter=None)`.
  - `Safety.quarantine(ticker, reason, message=None) -> bool`, idempotently adding and
    reporting one market quarantine.
  - Plain-mode messages identical to the existing messages.
  - Dashboard recent events for signals, paper fills, P&L, errors,
    quarantines, and halts.

- [ ] **Step 1: Write a failing event-flow regression**

Add:

```python
def test_runtime_events_capture_signals_fills_pnl_and_safety():
    reporter = ReporterSpy()
    ctx = _paper_context_with_reporter(
        reporter=reporter, ask_depth=Decimal("1"),
        contracts_per_trade=Decimal("2"))

    _paper_tick(
        ctx, 100.0, Decimal("52"), Decimal("51"), Decimal("53"))
    _paper_tick(
        ctx, 102.0, Decimal("54"), Decimal("53"), Decimal("55"))
    _paper_tick(
        ctx, 103.0, Decimal("61.5"), Decimal("61"), Decimal("62"))
    _paper_tick(
        ctx, 105.0, Decimal("61.5"), Decimal("61"), Decimal("62"))
    ctx.safety.error("temporary quote failure", ticker="OTHER")
    ctx.safety.trip("operator test halt")

    categories = [event.category for event in reporter.events]
    assert "SIGNAL" in categories
    assert "PAPER" in categories
    assert "SAFETY" in categories
    assert "HALT" in categories
    assert any(
        "PARTIAL" in event.message
        for event in reporter.events if event.category == "PAPER")
    assert any(
        event.category == "PNL" for event in reporter.events)
```

- [ ] **Step 2: Write a failing plain-output compatibility regression**

Add:

```python
def test_reporter_absence_preserves_plain_runtime_output():
    output = io.StringIO()
    cfg = Config()
    safety = Safety(cfg)

    with contextlib.redirect_stdout(output):
        safety.error("temporary", ticker="T")
        safety.trip("test halt")

    text = output.getvalue()
    assert "[safety] T error" in text
    assert "[safety] HALT: test halt" in text
```

- [ ] **Step 3: Run both tests and confirm the event-flow failure**

Run:

```bash
python -c "import tests; tests.test_runtime_events_capture_signals_fills_pnl_and_safety(); tests.test_reporter_absence_preserves_plain_runtime_output()"
```

Expected: event-flow test fails because the reporter is not injected; the
compatibility test passes against existing output.

- [ ] **Step 4: Add optional reporters and replace direct runtime prints**

Append `reporter=None` to the constructors:

```python
Executor(..., clock=time.time, sleep=time.sleep, reporter=None)
Safety(config, reporter=None)
```

Save `self.reporter`. In each class add:

```python
def _emit(self, category, message, ticker=None, *, ts=None, important=False):
    if self.reporter is None:
        print(message)
    else:
        self.reporter.emit(
            category, message, ticker=ticker, ts=ts, important=important)
```

Update `_paper_context_with_reporter` in `tests.py` to pass the same reporter
into `Executor(..., reporter=reporter)` and `Safety(..., reporter=reporter)`.

Add to `Safety`:

```python
def quarantine(self, ticker, reason, message=None):
    if ticker in self.quarantined:
        return False
    self.quarantined.add(ticker)
    line = (
        f"[safety] QUARANTINED {ticker}: {reason}"
        if message is None else message)
    self._emit(
        "SAFETY", line, ticker=ticker)
    if self.reporter is not None:
        self.reporter.decision(
            ticker, "QUARANTINED", str(reason))
    return True
```

Route persistent-error and stale/unquoted quarantine branches through this
helper so each quarantine is reported exactly once. Pass each branch's current
print string as `message` so non-dashboard output remains byte-for-byte stable.

Replace only the runtime-facing prints:

- executor simulated fills -> `PAPER`;
- strategy fill/P&L lines -> `PNL`;
- safety errors and quarantines -> `SAFETY`;
- safety halt -> `HALT` with `important=True`;
- engine BUY/SELL signals -> `SIGNAL`.

Continue emitting the existing message text so plain output and existing tests
remain stable. Record a decision update alongside each signal, fill,
quarantine, and halt.

- [ ] **Step 5: Run both Task 3 regressions**

Run:

```bash
python -c "import tests; tests.test_runtime_events_capture_signals_fills_pnl_and_safety(); tests.test_reporter_absence_preserves_plain_runtime_output()"
```

Expected: PASS.

- [ ] **Step 6: Run execution and safety regressions**

Run:

```bash
python -c "import tests; tests.test_pending_paper_order_uses_first_observed_due_quote(); tests.test_per_market_quarantine(); tests.test_critical_market_errors_halt_instead_of_quarantine(); tests.test_global_error_not_erased_and_auth_rate_limit_halt(); tests.test_loss_breach_stops_before_next_market_action()"
```

Expected: PASS.

- [ ] **Step 7: Review the task diff without committing**

Run:

```bash
git diff --check -- executor.py safety.py strategy.py engine.py tests.py
git status --short -- executor.py safety.py strategy.py engine.py tests.py
```

Expected: no whitespace errors; only the listed source and test files changed.

---

### Task 4: Integrate the dashboard heartbeat into the real paper loop

**Files:**
- Modify: `bot.py:350-564`
- Modify: `tests.py:1879-2150`

**Interfaces:**
- Consumes: `TerminalDashboard`, reporter-aware `Context`, strategy, executor,
  and safety constructors.
- Produces:
  - A live progress update before/after each market request.
  - Request latency measured with `time.monotonic`.
  - One in-place final frame before ordinary shutdown text.
  - Automatic plain-output fallback.

- [ ] **Step 1: Write a failing run-loop heartbeat regression**

Add:

```python
class LoopReporterSpy(ReporterSpy):
    def __init__(self):
        super().__init__()
        self.sweeps = []
        self.requests = []
        self.current_request = None
        self.render_calls = 0
        self.finished = False

    def start_sweep(self, number):
        self.sweeps.append(number)

    def start_request(self, index, total, ticker):
        self.current_request = SimpleNamespace(
            index=index, total=total, ticker=ticker)

    def finish_request(self, elapsed_s):
        self.current_request.elapsed_s = elapsed_s
        self.requests.append(self.current_request)

    def render(self, ctx, tickers, force=False):
        self.render_calls += 1
        return False

    def finish(self, ctx, tickers):
        self.finished = True


class LoopFeed:
    def __init__(self):
        self.history = defaultdict(list)
        self.books = {}
        self.last_good = {}

    def get_quote(self, ticker):
        observed_at = 100.0
        mid = Decimal("50")
        bid = Decimal("49")
        ask = Decimal("51")
        self.books[ticker] = (
            bid, Decimal("20"), ask, Decimal("20"))
        self.history[ticker].append((observed_at, mid))
        self.last_good[ticker] = observed_at
        return mid, bid, ask, observed_at

    def top_of_book(self, ticker):
        return self.books.get(
            ticker, (None, None, None, None))

    def stale_tickers(self, tickers):
        return []


class LoopLog:
    def __init__(self):
        self.ended_clean = None

    def tick(self, *args, **kwargs):
        return None

    def trade(self, *args, **kwargs):
        return None

    def event(self, *args, **kwargs):
        return None

    def end(self, clean, reason):
        self.ended_clean = clean


def _single_sweep_context(reporter):
    cfg = Config()
    feed = LoopFeed()
    strategy = ScalpStrategy(cfg, reporter=reporter)
    executor = Executor(cfg, None, feed, reporter=reporter)
    safety = Safety(cfg, reporter=reporter)
    return Context(
        cfg, feed, strategy, executor, LoopLog(), safety,
        clock=lambda: 100.0, reporter=reporter)


def _stop_after_one_sweep(_seconds):
    raise KeyboardInterrupt()


def test_run_loop_reports_sweep_progress_and_request_latency():
    reporter = LoopReporterSpy()
    ctx = _single_sweep_context(reporter=reporter)

    assert run_loop(ctx, None, ["A", "B"], sleep=_stop_after_one_sweep)

    assert reporter.sweeps == [1]
    assert [item.ticker for item in reporter.requests] == ["A", "B"]
    assert all(item.elapsed_s >= 0 for item in reporter.requests)
    assert reporter.render_calls >= 2
    assert reporter.finished
```

The fake context ends by raising `KeyboardInterrupt` after one healthy sweep,
matching the existing clean-stop driver tests.

- [ ] **Step 2: Write a failing presentation-isolation regression**

Add:

```python
def test_dashboard_failure_falls_back_without_halting_loop():
    from terminal_dashboard import TerminalDashboard

    stream = FakeStream(is_tty=True, fail_writes=True)
    reporter = TerminalDashboard(stream=stream, enabled=True)
    ctx = _single_sweep_context(reporter=reporter)

    assert run_loop(ctx, None, ["A"], sleep=_stop_after_one_sweep)
    assert not reporter.active
    assert ctx.safety.tripped_reason == "operator interrupt"
    assert ctx.log.ended_clean
```

- [ ] **Step 3: Run both tests and confirm missing loop hooks**

Run:

```bash
python -c "import tests; tests.test_run_loop_reports_sweep_progress_and_request_latency(); tests.test_dashboard_failure_falls_back_without_halting_loop()"
```

Expected: FAIL because `run_loop` does not publish progress or finish the
reporter.

- [ ] **Step 4: Add heartbeat and render calls to `run_loop`**

At the top of `run_loop`, set:

```python
reporter = getattr(ctx, "reporter", None)
sweep_number = 0
```

At each sweep:

```python
sweep_number += 1
if reporter is not None:
    reporter.start_sweep(sweep_number)
```

Before each non-quarantined request:

```python
if reporter is not None:
    reporter.start_request(index, len(tickers), ticker)
    reporter.render(ctx, tickers)
request_started = time.monotonic()
```

After success or exception:

```python
if reporter is not None:
    reporter.finish_request(time.monotonic() - request_started)
    reporter.render(ctx, tickers)
```

On a quote exception, publish the exact error and set the per-ticker decision
to `QUARANTINED`, `WATCH`, or `HALT` according to the existing safety branch.
Do not alter quarantine thresholds or the critical-exposure halt behavior.
Replace `bot.py`'s direct `safety.quarantined.add(...)` and quarantine print
with:

```python
safety.quarantine(
    ticker, str(error),
    message=f"[safety] QUARANTINED {ticker}: {error}")
```

Before `safe_shutdown`, force one final render and call `finish` inside a
presentation-only `try/except (OSError, UnicodeError)` so a terminal problem
cannot change the shutdown outcome.

- [ ] **Step 5: Wire one reporter instance through `run_session`**

Import:

```python
from terminal_dashboard import TerminalDashboard
```

After `session_start` and before constructing strategy/executor/safety:

```python
reporter = TerminalDashboard(session_started_at=session_start)
strategy = ScalpStrategy(
    cfg, ledger=ledger, now=session_start, reporter=reporter)
executor = Executor(
    cfg, client, feed, journal=journal, reporter=reporter)
safety = Safety(cfg, reporter=reporter)
ctx = Context(
    cfg, feed, strategy, executor, log, safety, reporter=reporter)
```

Leave preflight, Sports listing, discovery telemetry, startup failures, and
final shutdown/P&L text as ordinary output.

- [ ] **Step 6: Add a single-reporter wiring regression**

Add:

```python
def test_run_session_wires_single_dashboard_reporter():
    import bot as bot_module

    discovery = _task4_discovery(_task4_contract())
    feed = _Task4RunFeed(discovery)
    reporter = ReporterSpy()
    captured = {}
    originals = (
        bot_module.PriceFeed, bot_module.TerminalDashboard,
        bot_module.run_loop)

    def dashboard_factory(**kwargs):
        captured["dashboard_kwargs"] = kwargs
        return reporter

    def loop(ctx, reconciler, tickers):
        captured["ctx"] = ctx
        ctx.log.end(clean=True, reason="operator interrupt")
        return True

    workdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        bot_module.PriceFeed = lambda cfg, client: feed
        bot_module.TerminalDashboard = dashboard_factory
        bot_module.run_loop = loop
        cfg = Config(
            sports=["Tennis"],
            state_root=os.path.join(workdir, "state"))

        assert bot_module.run_session(cfg, object()) == 0
        ctx = captured["ctx"]
        assert ctx.reporter is reporter
        assert ctx.strategy.reporter is reporter
        assert ctx.executor.reporter is reporter
        assert ctx.safety.reporter is reporter
        assert "session_started_at" in captured["dashboard_kwargs"]
    finally:
        (bot_module.PriceFeed, bot_module.TerminalDashboard,
         bot_module.run_loop) = originals
        os.chdir(old_cwd)
```

The test restores every patched module attribute and does not alter production
CLI flags or configuration.

- [ ] **Step 7: Run all Task 4 tests**

Run:

```bash
python -c "import tests; tests.test_run_loop_reports_sweep_progress_and_request_latency(); tests.test_dashboard_failure_falls_back_without_halting_loop(); tests.test_run_session_wires_single_dashboard_reporter()"
```

Expected: PASS.

- [ ] **Step 8: Run existing runtime and shutdown regressions**

Run:

```bash
python -c "import tests; tests.test_runtime_error_and_ctrl_c_preserve_honest_paper_residuals(); tests.test_staleness_is_checked_between_market_requests(); tests.test_global_halt_stops_current_market_sweep_immediately(); tests.test_termination_signals_route_through_interrupt(); tests.test_live_and_demo_disabled()"
```

Expected: PASS.

- [ ] **Step 9: Review the task diff without committing**

Run:

```bash
git diff --check -- bot.py tests.py
git status --short -- bot.py tests.py
```

Expected: no whitespace errors; only `bot.py` and `tests.py` are listed for
this task.

---

### Task 5: Document operation and run complete verification

**Files:**
- Modify: `README.md`
- Modify: `tests.py:7170-7303`
- Verify: `terminal_dashboard.py`
- Verify: `bot.py`
- Verify: `engine.py`
- Verify: `executor.py`
- Verify: `safety.py`
- Verify: `signals.py`
- Verify: `strategy.py`

**Interfaces:**
- Consumes: the completed dashboard and its 16 named regressions.
- Produces: operator instructions and a fully registered 216-test suite.

- [ ] **Step 1: Add the 16 new tests to the `tests.py` main registry**

Register these exact functions once:

```python
test_shared_current_dip_matches_entry_signal()
test_position_open_marks_sum_to_risk_total()
test_session_realized_pnl_survives_utc_day_rollover()
test_strategy_reports_exact_wait_and_action_reasons()
test_engine_reports_non_strategy_wait_gates()
test_terminal_reporter_plain_fallback_preserves_messages()
test_terminal_dashboard_renders_all_contracts_and_sections()
test_dashboard_maps_pending_open_quarantine_and_no_quote_states()
test_terminal_dashboard_narrow_width_never_wraps()
test_terminal_dashboard_throttles_and_skips_unchanged_frames()
test_terminal_dashboard_prioritizes_safety_events_without_network_calls()
test_runtime_events_capture_signals_fills_pnl_and_safety()
test_reporter_absence_preserves_plain_runtime_output()
test_run_loop_reports_sweep_progress_and_request_latency()
test_dashboard_failure_falls_back_without_halting_loop()
test_run_session_wires_single_dashboard_reporter()
```

This list contains 16 tests, so update the final line from 200 to:

```python
print("\nALL TESTS PASS (216 tests)")
```

- [ ] **Step 2: Add an operator-facing README section**

Document:

```text
Terminal dashboard
- Starts automatically only in an interactive terminal during paper mode.
- Shows all monitored contracts, decisions, pending orders, positions,
  conservative open P&L, safety state, and the latest eight events.
- Refreshes in place at no more than four frames per second.
- Makes no additional exchange requests.
- Redirected output and TERM=dumb retain ordinary line-oriented logs.
- Ctrl-C still performs the existing safe paper shutdown.
```

State explicitly that `QUARANTINED — empty executable book` means the contract
has no currently executable bid/ask/depth and is skipped for that session.
Also state that installing this change requires starting a new paper session:
the modified strategy/engine files produce a new v6 code fingerprint, and old
logs remain evaluable only with the matching archived source. Do not weaken
that provenance check or change the CSV schema version.

- [ ] **Step 3: Add a source-level lock/dependency assertion**

Extend `test_live_and_demo_disabled` or the dashboard dependency test:

```python
dashboard_source = Path("terminal_dashboard.py").read_text()
assert "import rich" not in dashboard_source
assert "from rich" not in dashboard_source
assert "requests" not in dashboard_source
assert "REAL_ORDER_EXECUTION_ENABLED = False" in \
    Path("executor.py").read_text()
```

- [ ] **Step 4: Check syntax for every Python source file**

Run:

```bash
python -m py_compile *.py
```

Expected: exit code 0.

- [ ] **Step 5: Run the complete offline suite**

Run:

```bash
python tests.py
```

Expected final line:

```text
ALL TESTS PASS (216 tests)
```

No production order endpoint may be called.

- [ ] **Step 6: Run a non-network terminal smoke test**

Use fake contexts from `tests.py`:

```bash
python -c "import tests; tests.test_terminal_dashboard_renders_all_contracts_and_sections(); tests.test_run_session_wires_single_dashboard_reporter()"
```

Expected: PASS with no network access.

- [ ] **Step 7: Run repository consistency checks**

Run:

```bash
git diff --check
git status --short
rg -n "REAL_ORDER_EXECUTION_ENABLED|Live trading is disabled|Demo order flow is disabled" executor.py bot.py
```

Expected:

- no whitespace errors;
- `REAL_ORDER_EXECUTION_ENABLED = False`;
- live/demo refusal text remains present;
- no credentials or `.pem` files appear in the changed-file list.

- [ ] **Step 8: Report the manual-upload file set without committing**

Expected runtime file set:

```text
README.md
bot.py
engine.py
executor.py
safety.py
signals.py
strategy.py
terminal_dashboard.py
tests.py
```

Expected documentation files:

```text
docs/superpowers/specs/2026-07-27-terminal-dashboard-design.md
docs/superpowers/plans/2026-07-27-terminal-dashboard.md
```

Do not include `__pycache__/`, credentials, `.pem` files, logs, or state files.
