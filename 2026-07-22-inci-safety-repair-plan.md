# Inci Safety Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the verified contract, reconciliation, shutdown, replay, and research-integrity defects while keeping every real-order path disabled.

**Architecture:** Strict schema and pagination boundaries feed a single authoritative order-reconciliation routine. A pending paper-order state machine is shared by real-time paper operation and replay so both consume the same quote sequence without look-ahead.

**Tech Stack:** Python 3.12, standard library, `requests`, `cryptography`, `Decimal`, script-based regression suite.

**Status (2026-07-22):** Implemented and verified by the 95-test offline
suite. The checkboxes below preserve the original execution checklist; the
current guarantees and remaining blockers are documented in `README.md`.

## Global Constraints

- Preserve `/Users/mthanki/Downloads/Inci/files (3)` unchanged.
- Modify only `/Users/mthanki/Downloads/Inci/files (4)`.
- Do not call production APIs or place demo/live orders.
- Keep `--live` and `--demo` disabled.
- Real-order execution must also be disabled inside `Executor`.
- Use test-first red/green cycles for every behavior change.
- Use the bundled Python plus pip's vendored `requests` for verification.

---

### Task 1: Strict V2 Contracts and Pagination

**Files:**
- Modify: `tests.py`
- Modify: `config.py`
- Modify: `schemas.py`
- Modify: `kalshi_client.py`

**Interfaces:**
- Produces `parse_create_ack`, `parse_order`, `parse_orderbook_response`, and
  fail-closed `_paginate` behavior consumed by execution and reconciliation.

- [ ] Add tests asserting `immediate_or_cancel` and `taker_at_cross`, rejecting
  obsolete enums, requiring `fill_count`, `remaining_count`, and `ts_ms`,
  parsing `remaining_count_fp`, and parsing `orderbook_fp` string levels.
- [ ] Run the focused contract tests and confirm they fail against the copied
  v5 code.
- [ ] Implement explicit enum validation and strict response envelopes.
- [ ] Add tests for missing collection keys, repeated cursors, and page-cap
  exhaustion raising `SchemaError`.
- [ ] Implement fail-closed pagination and rerun focused tests.

### Task 2: Convergent Order Resolution

**Files:**
- Modify: `tests.py`
- Modify: `executor.py`
- Modify: `schemas.py`
- Modify: `order_journal.py`

**Interfaces:**
- Produces an executor reconciliation helper that returns terminal status,
  aggregate fills, fees, and final authoritative position only when all
  quantities agree.

- [ ] Add a failing regression for `executed + zero fills + position=20`.
- [ ] Add a failing regression for delayed fill visibility that converges on a
  later poll.
- [ ] Add a failing regression proving contradictory state leaves the journal
  unresolved.
- [ ] Capture pre-submit position, validate polled fill quantity, and poll
  fills/positions to convergence before recording `outcome`.
- [ ] Run focused lifecycle tests through red then green.

### Task 3: Startup, Periodic, Flatten, and Shutdown Safety

**Files:**
- Modify: `tests.py`
- Modify: `safety.py`
- Modify: `engine.py`
- Modify: `bot.py`
- Modify: `kalshi_client.py`

**Interfaces:**
- Consumes the Task 2 reconciliation helper.
- Produces terminal-verified cancellation and authoritative flattening.

- [ ] Add failing tests for a cancel-acknowledged order that remains resting,
  a stray pending order, refusal of exchange-only unknown-basis exposure,
  partial authoritative flattening, and non-flat final verification.
- [ ] Make startup and shutdown poll canceled orders to terminal and verify
  fills plus positions before resolving them.
- [ ] Make periodic reconciliation include journal entries and all nonterminal
  orders, not positions alone.
- [ ] Drive flattening from refreshed exchange positions until flat or until a
  bounded failure raises.
- [ ] Route generic exceptions and Ctrl-C through safe shutdown.
- [ ] Run focused safety tests through red then green.

### Task 4: Hard Order Gate and Error Classification

**Files:**
- Modify: `tests.py`
- Modify: `executor.py`
- Modify: `bot.py`
- Modify: `safety.py`

**Interfaces:**
- Produces an executor-level `OrderExecutionDisabled` guard independent of
  `Config.paper_trading`.

- [ ] Add a failing test that sets `paper_trading=False` and proves no client
  create call can occur.
- [ ] Add failing tests showing a healthy ticker cannot erase a global error
  and that authentication/rate-limit failures halt globally.
- [ ] Add the executor-level guard and reset global health only after a full
  successful sweep.
- [ ] Run focused gate/error tests through red then green.

### Task 5: Shared Pending Paper Engine and Causal Replay

**Files:**
- Modify: `tests.py`
- Modify: `executor.py`
- Modify: `engine.py`
- Modify: `bot.py`
- Modify: `replay.py`
- Modify: `market_data.py`

**Interfaces:**
- Produces `submit_paper`, `process_due_paper_orders`, and a replay driver that
  calls the same functions after every quote update.

- [ ] Add a failing equivalence test that feeds identical timestamped books to
  real-time paper and replay and compares fills exactly.
- [ ] Add a failing test proving an empty ticker set processes zero rows.
- [ ] Replace blocking paper sleeps with pending orders due at
  `signal_time + sim_latency_s`.
- [ ] Process due orders only after quote ingestion in both runtime and replay.
- [ ] Mark any replay with residual inventory as incomplete and exclude it from
  positive TEST P&L.
- [ ] Run focused replay tests through red then green.

### Task 6: Research Grouping, Causality, and Fee Accuracy

**Files:**
- Modify: `tests.py`
- Modify: `research_log.py`
- Modify: `analyze.py`
- Modify: `signals.py`
- Modify: `fees.py`
- Modify: `schemas.py`

**Interfaces:**
- Produces event-level stable splitting and aggregate conservative paper fees.

- [ ] Add failing tests for event-group split integrity, future-history
  rejection, sorted input, censored horizons, and aggregate fee rounding.
- [ ] Log and parse event identifiers alongside ticker data.
- [ ] Extract and test the production split function rather than reimplementing
  it in tests.
- [ ] Reject future timestamps and omit unsupported markout horizons.
- [ ] Calculate aggregate taker fees with centicent rounding and retain
  exchange `fee_cost` as live authority.
- [ ] Run focused research tests through red then green.

### Task 7: Replace False Positives and Verify the Build

**Files:**
- Modify: `tests.py`
- Modify: `README.md`

**Interfaces:**
- Consumes every prior task's public behavior.
- Produces the final regression suite and honest readiness documentation.

- [ ] Replace broad self-catching exceptions with explicit expected exception
  assertions.
- [ ] Ensure the cancel-race test actually submits, cancels, observes an empty
  fill snapshot, and then observes the delayed fill.
- [ ] Ensure the replay test compares two drivers rather than ReplayFeed alone.
- [ ] Ensure the disabled-mode test checks exit status and zero client calls.
- [ ] Run the full suite using:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/mthanki/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages/pip/_vendor /Users/mthanki/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests.py`.
- [ ] Run AST compilation and a synthetic analyzer smoke test.
- [ ] Update README with exact test count, verified guarantees, limitations,
  and the statement that strategy profitability remains unproven.
