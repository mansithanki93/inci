# Inci Safety Repair Design

## Goal

Produce a paper-only Inci build whose API contracts fail closed, whose order
lifecycle never treats contradictory exchange state as resolved, and whose
replay produces the same decisions and fills as the running paper engine.

## Global Constraints

- Preserve `/Users/mthanki/Downloads/Inci/files (3)` unchanged.
- Work only in `/Users/mthanki/Downloads/Inci/files (4)`.
- Do not place orders or probe production order endpoints.
- Both `--live` and `--demo` remain disabled.
- The executor itself must reject real-order execution unless a deliberate
  source-code gate is changed; changing `Config.paper_trading` is insufficient.
- Use `Decimal` for prices, quantities, fees, and P&L.
- Every production change begins with a regression test that fails for the
  expected reason.

## API Contracts

Create V2 orders use `side=bid|ask`, fixed-point string `count` and `price`,
`time_in_force=immediate_or_cancel`, and
`self_trade_prevention_type=taker_at_cross`. Schema validators reject unknown
enum values, incomplete acknowledgements, missing collection envelopes,
invalid fixed-point precision, repeated cursors, and page-cap truncation.

Create/cancel remain on `/portfolio/events/orders`; poll/list remain on
`/portfolio/orders`. The orderbook parser accepts the current `orderbook_fp`
wrapper with `yes_dollars` and `no_dollars` string levels.

Current Market responses are restricted to binary, $1-notional contracts.
Lifecycle status and four-decimal fixed-point prices are validated; deprecated
`title` is optional, and zero-depth sides are normalized to absent liquidity.

## Authoritative Order Lifecycle

An order is resolved only when these sources agree:

1. The polled order is terminal.
2. The fills total is stable and matches the order's reported fill quantity.
3. The authoritative position changed in the expected direction by that fill
quantity relative to the pre-submit position snapshot.

For IOC orders, `filled + remaining` may be smaller than requested because the
difference is canceled quantity. That quantity is computed and journaled
explicitly; it is not mislabeled as resting inventory.

If state does not converge before the reconciliation timeout, the journal
entry remains unresolved and execution raises `HaltError`. Startup, periodic
reconciliation, flattening, and shutdown use the same terminal-order helper.
They never convert a cancel acknowledgement directly into a resolved outcome.

## Flattening and Shutdown

Flattening starts from exchange positions, but proceeds only when they exactly
match local long positions with known cost basis. Exchange-only, negative, or
quantity-mismatched exposure fails for manual reconciliation rather than
creating unverifiable P&L. It refreshes positions after each IOC attempt and
succeeds only when the final authoritative map is empty. Unresolved journal
entries prevent automatic flattening because they can represent an in-flight
buy or sell. Generic runtime failures, Ctrl-C, SIGTERM, and SIGHUP enter the
same safe shutdown path.

## Paper Execution and Replay

Paper orders are pending objects with a due timestamp. The main loop continues
to ingest quotes while simulated latency elapses; due orders fill from the
latest quote. Replay drives the same pending-order state machine with a virtual
clock. No replay feed preloads future quotes for direct lookup.

`tickers=None` means all rows; an empty set means zero rows. Replay returns
realized P&L and residual inventory separately. Any residual inventory marks
the run incomplete and cannot count as positive TEST evidence.

Version-5 logs carry process-start UTC day, starting daily P&L, per-row UTC
day, market lifecycle facts, config/code fingerprints, and a durable terminal
record. Replay restores restart loss state, performs UTC-midnight resets,
reports actual rows processed and halt reason, rejects reordered rows, and
never calls an incomplete or halted run evaluable.

## Research Integrity

Research rows preserve observed order; nonmonotonic timestamps are rejected
rather than silently sorted. Signal history rejects timestamps later than the
decision time. The logger records event identifiers so the stable split
operates at match/event level rather than at individual ticker level. One full
portfolio replay supplies group attribution. Horizons without sufficient
follow-up are omitted rather than valued at the last available tick.

## Fees

Paper fees use the aggregate taker formula with `Decimal`, the centicent trade
fee ceiling, and the documented account balance-rounding fee. A simulated
one-fill order receives no multi-fill rebate. The model remains an estimate;
live fills always use the exchange's `fee_cost`. Entry filtering and replay
share the same fee model.

## Verification

Regression tests must prove:

- official enum values pass and obsolete values fail;
- incomplete envelopes and truncated pagination raise;
- contradictory fills/positions keep the journal unresolved;
- startup cannot resolve an order that remains live;
- exchange-only exposure is refused for manual cost-basis reconciliation;
- generic exceptions and Ctrl-C use safe shutdown;
- empty data partitions remain empty;
- paper and replay produce identical fills for the same quote stream;
- zero-depth residual inventory cannot create positive TEST P&L;
- production mode cannot be reached through configuration alone.

No production API call is part of verification.
