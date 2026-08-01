# Inci Terminal Dashboard Design

Date: 2026-07-27

## Purpose

Replace Inci's quiet, scattered runtime output with a dependency-free terminal
dashboard that refreshes in place. The dashboard must make it obvious that the
paper bot is alive, which contracts it is processing, why each contract is
waiting, and what simulated positions and orders exist.

The dashboard is observational only. It must not change discovery, market-data
requests, signals, fills, risk checks, research logging, or the live/demo locks.

## Scope

The dashboard will show:

- paper-mode and running/halted state;
- selected Sports, session uptime, sweep number, current contract, and refresh
  time;
- monitored, watching, pending, open, quarantined, and unavailable counts;
- session realized P&L, UTC-day realized P&L, conservative open P&L, total net
  P&L, and the configured loss limit;
- every monitored contract, including Sport, game, contract, state, bid and
  ask with depth, spread, mid, current dip, quote age, and the exact reason Inci
  is waiting or acting;
- every pending paper order, including side, quantity, due/overdue state, and
  signal reason;
- every open paper position, including quantity, entry, current executable
  bid, move, executable depth, holding time, entry fee, take-profit, stop,
  and conservative net open P&L;
- the most recent signals, simulated fills, exits, P&L changes, quote errors,
  quarantines, and halt events;
- the active research tick/trade file paths and the `Ctrl-C` instruction.

The dashboard will not add mouse interaction, configuration screens, charts,
notifications, a browser UI, or exchange order execution.

## Chosen Approach

Use a small standard-library terminal renderer with ANSI cursor control. It
will render from existing in-memory bot state and make no API calls. This avoids
another installation dependency and works in macOS Terminal and the VS Code
integrated terminal.

The alternatives were rejected:

- `rich` would simplify presentation but add a package dependency and another
  environment setup step.
- append-only snapshots would preserve simple output but would recreate the
  unreadable scrolling problem.

## Layout

The screen is split into bounded sections so it remains readable rather than
placing every field in one excessively wide table.

### Header and risk summary

The first lines show:

```text
INCI v6 | PAPER | RUNNING | Sports: ... | Uptime ... | Sweep ... | Updated ...
Processing: 4/10 <ticker> | API request ... ms
Markets 10 | Watch 6 | Pending 1 | Open 1/3 | Quarantined 2 | API errors 0/5
Session realized ... | UTC-day realized ... | Open est. ... | Total ... / -$30 limit
```

The processing indicator updates while a sweep is underway, not only after the
entire ten-contract sweep. This is the primary heartbeat.

### Watchlist

All monitored contracts remain visible, including quarantined ones:

```text
#  STATE       SPORT       GAME / CONTRACT        BID x DEPTH  ASK x DEPTH  SPRD
1  WATCH       Basketball  ...                    48 x 20      50 x 15      2
2  QUARANTINED Tennis      ...                    --           --           --
```

A second compact metrics line/table shows `MID`, current `DIP/configured dip`,
quote age, start time, and `WHY/LAST DECISION`. Long game and contract names are
truncated to the available terminal width without changing their stored values.
The ticker remains available in the displayed contract text.

States are:

- `NO QUOTE`
- `WATCH`
- `BUY PENDING`
- `OPEN`
- `EXIT PENDING`
- `QUARANTINED`
- `HALT`

The reason field comes from the exact decision branch used by the strategy.
Examples include waiting for dip, spread too wide, outside price range, fee
filter, position capacity, close buffer, early-close risk, holding for target,
take-profit, stop-loss, and time exit. The renderer must not recreate trading
rules independently.

### Positions and pending orders

These sections appear only when populated. An explicit `None` line is shown
when empty so the absence of activity is unambiguous.

Open P&L uses the same fee-, depth-, and slippage-aware calculation as the risk
engine. A shared per-position calculation will be extracted and used both by
the risk total and the dashboard; the dashboard must not maintain parallel P&L
math.

### Recent activity

The bottom section retains the latest eight events in memory. It includes a
timestamp, category, ticker when applicable, and the original reason/detail.
Safety and halt events have priority over routine events when the buffer is
full.

The final line shows the tick and trade CSV paths plus `Ctrl-C to stop safely`.

## Components and Data Flow

A focused `terminal_dashboard.py` module will own:

- an in-memory view of per-contract decision and request state;
- a bounded recent-event buffer;
- terminal-width-aware table formatting;
- ANSI in-place rendering;
- interactive-terminal detection and plain-output fallback.

`bot.py` will create the dashboard after discovery and update sweep progress
before and after each quote request. It will render after each processed
contract, with redraws capped at four per second and unchanged frames skipped.
Rendering adds no sleeps to the quote loop.

`engine.py` and `strategy.py` will expose decision states from the same branches
that authorize or reject actions. `executor.py` and `safety.py` will report
significant runtime events to the dashboard through an optional reporting
interface. Existing behavior remains the default when no dashboard is supplied.

`market_data.py`, the strategy, executor, and safety objects remain the
authoritative sources for quotes, positions, pending orders, and safety state.
The dashboard takes read-only snapshots of those objects.

## Terminal and Failure Behavior

- ANSI rendering is enabled only when standard output is an interactive TTY
  and `TERM` is not `dumb`.
- Tests, redirected output, log capture, and unsupported terminals retain
  plain text without cursor-control sequences.
- A formatting or terminal-write error disables the dashboard and falls back
  to plain output. A presentation failure must never halt trading research.
- Missing values are displayed as `--`; they are never converted to zero.
- A never-quoted market is distinguished from a stale or quarantined market.
- Halt reasons remain visible during shutdown, followed by the existing final
  P&L and residual-position summary.
- The dashboard does not suppress or weaken durable CSV research logging.

## Verification

Tests will cover:

- rendering all ten monitored contracts;
- narrow- and wide-terminal truncation without broken rows;
- TTY dashboard mode and non-TTY plain fallback;
- heartbeat progress through a sweep;
- every contract-state mapping;
- exact decision reasons from the trading path;
- pending, partial-fill, open-position, exit, and quarantine displays;
- per-position conservative P&L summing exactly to the existing risk total;
- missing and stale quote presentation;
- bounded recent-event behavior with safety-event priority;
- dashboard failure falling back without affecting the loop;
- no API calls made by rendering;
- live and demo execution remaining disabled;
- the complete existing test suite.

## Acceptance Criteria

While Inci is running interactively, the operator can see within one screen:

1. that the process is alive and which contract it is currently checking;
2. the current executable market data for all monitored contracts;
3. why each contract is watching, pending, open, quarantined, or halted;
4. all simulated exposure and fee-aware P&L;
5. the most recent actions and safety events.

The screen refreshes in place, no third-party package is required, and research
or trading behavior is unchanged.
