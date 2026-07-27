# Inci — Kalshi Sports Scalping Research Bot

Status: **paper-research build only**. Demo and live sessions are disabled in
`bot.py`, and real-order mutation is independently disabled inside
`Executor`. Changing configuration or an environment variable cannot unlock
orders.

This build improves execution safety and research honesty. It does **not**
prove that the dip-retracement strategy is profitable.

## Run

```bash
pip install requests cryptography
python tests.py
python bot.py --check     # read-only V2 schema/auth preflight
python bot.py             # paper mode
python analyze.py logs/ticks_v5_<YYYYMMDD>_<session-id>.csv
```

`tests.py` contains 104 contract, lifecycle, safety, replay, and research
regressions. The tests use fakes and local temporary files; they do not place
orders or probe production order endpoints.

## What is enforced

- Every API field Inci consumes is validated through `schemas.py`: official
  response envelopes, fixed-point strings, current order states, create and
  cancel acknowledgements, `orderbook_fp`, endpoint-specific cursors, and
  fail-closed pagination. Fake-HTTP tests cover request paths, signing,
  parameters, bodies, subaccount scope, and invalid JSON.
- Market parsing follows the current binary/$1 contract: `market_type`, side
  subtitles, lifecycle status, four-decimal fixed-point prices, and notional
  are validated. Deprecated `title` is optional. Zero-depth sides are treated
  as absent liquidity, and only `active` markets can become fresh quotes.
  Listing requests ask the API to exclude MVE products, then skip only known
  scalar/MVE rows per item. Discovery and `--check` always print the skipped
  count by type—even zero—while malformed binary or unknown product rows still
  fail the whole request. Direct reads of unsupported markets remain strict.
- IOC order acknowledgements, polled orders, order-filtered fills, exact live
  `fee_cost`, and authoritative position changes must agree across two stable
  observations before an outcome journal entry is written. Unfilled IOC
  quantity is recorded explicitly as canceled; terminal remaining quantity is
  not incorrectly forced to equal requested minus filled.
- Cancel acknowledgement is never treated as terminal truth. Startup,
  periodic checks, and shutdown attempt every identifiable order before
  reporting collected ambiguity. A filled outcome not durably applied to
  position/P&L state blocks restart and flattening.
- Flattening starts from freshly fetched exchange positions and proceeds only
  when each long exactly matches a local position with a known cost basis.
  Exchange-only, negative, or quantity-mismatched exposure requires manual
  reconciliation instead of inventing P&L. Safe partial exits are retried and
  final exchange flatness is verified. Ambiguous or unapplied orders block
  automatic flattening.
- The process lock, order journal, and loss ledger share one absolute,
  environment/subaccount directory. There is no user-selectable account
  namespace or per-file path override that can split these three safety
  records. Any future order-enabled mode must use the canonical state root.
  The root is derived from the OS account rather than `$HOME`, and changing
  environment/subaccount/root or derived paths after construction fails
  validation before the process lock is acquired.
  Realized UTC-day P&L is fsync-persisted, idempotent by event ID, validates
  timestamp/day integrity, and rolls over at UTC midnight.
- Open-risk marking includes fees, exit slippage, and executable bid depth;
  inventory beyond available depth is conservatively valued at zero.
- Authentication/authorization failures halt globally. Safe GET requests
  receive four bounded 429 retries with exponential backoff and fresh
  signatures; mutating POST/DELETE requests are never automatically retried.
  Persistent rate limiting still halts globally. Discovery uses 1,000-row
  pages and monitors at most 10 markets, reducing both startup pagination and
  quote-loop bursts. Market-local failures quarantine only that market, and
  one healthy ticker cannot erase global failures. Staleness is checked after
  every blocking quote request, before another market can act. A quote failure
  for a market with exposure or a pending order always halts; it can never be
  quarantined and skipped.
- Paper orders are non-blocking pending orders. They fill only on the first
  newly observed quote for that same market whose immutable observation time
  is at or after simulated latency. Blocking cached-book paper execution is
  rejected. Pending entries count toward the maximum-position limit.
- Real-time `PriceFeed` and `ReplayFeed` run the same decision and pending-fill
  path. Both reject new positions too near scheduled close or when the market
  can close early. Empty data partitions stay empty; future history cannot
  trigger a signal.
- A future live entry must carry the signal's original ask as a hard maximum;
  an adverse requote is rejected even if it remains inside global bounds.
  The executor's fresh requote is copied into the risk mark before the
  immediate post-fill loss check.
- Version-5 research logs use a unique file/session ID, process-start UTC day,
  starting daily P&L, per-row UTC day, real event IDs, market lifecycle facts,
  and config/code fingerprints. A durable clean terminal record is required;
  halted or unterminated sessions cannot contribute even diagnostic mark-outs,
  and reordered observations are rejected rather than sorted. Replay restores
  same-day loss state and resets it at UTC midnight like runtime—even when the
  first quote arrives after midnight. Sibling contracts remain in one stable
  TRAIN/TEST bucket, while one shared portfolio replay supplies group P&L.
  Nonfinite/crossed books, malformed rows, safety halts, unsupported horizons,
  data gaps, pending orders, and residual inventory fail closed. Raw mark-outs
  are non-executable diagnostics.
- Ctrl-C, SIGTERM, and SIGHUP enter the same shutdown path. Later termination
  signals are held while cancellation/reconciliation and the terminal research
  record complete.
- Paper fees include the aggregate taker-fee ceiling and documented account
  balance-rounding charge at configured `$0.01` (non-direct) or `$0.0001`
  (direct-member) precision. One simulated fill per order receives no
  multi-fill accumulator rebate. Live accounting would use API `fee_cost`.

## What is not yet proven

- The authenticated portfolio responses and order lifecycle have not been
  validated from this environment. Run `python bot.py --check` on your machine;
  missing credentials fail the check. Valid empty authenticated
  order/fill/position collections produce a loud row-coverage warning while
  their response envelopes remain accepted; malformed rows still fail.
- No demo order has been submitted, and both `--demo` and `--live` exit before
  creating files, making network calls, or entering an order path. Refusal is
  a nonzero exit so automation cannot mistake it for successful startup.
- The strategy still has no demonstrated edge. A valid result requires
  positive, fee-inclusive, **evaluable** TEST performance across unseen events
  and multiple tournaments. Any residual position, pending order, or logged
  data gap makes a replay incomplete.
- The generic paper fee model does not know every series-specific multiplier,
  maker rebate, promotion, or real multi-fill rebate sequence. Analyzer output
  is labeled `RESEARCH-EVALUABLE; ESTIMATED FEES`; use the current exchange fee
  schedule when evaluating a specific market.
- Price-only dip detection cannot tell market noise from match information.
  Tennis latency and adverse selection remain major strategy risks.
- `Config.subaccount` defaults to primary subaccount `0` and accepts only the
  documented range `0` through `32`. Before any future demo enablement,
  confirm every authenticated response remains in the configured subaccount.

## Deliberate enablement sequence

1. Run the complete local suite successfully.
2. Run authenticated `python bot.py --check` and resolve every schema failure.
3. Collect version-5, cleanly terminated single-session paper logs across
   multiple tournaments.
4. Freeze parameters, then evaluate untouched event-level TEST data. Require
   positive net results with no residuals, pending orders, or data gaps.
5. Review the current official API and fee documentation again.
6. Enable demo only through a reviewed source change and validate many clean
   lifecycle/restart sessions.
7. Consider a separate reviewed live-enablement change at minimal size.

## Files

`bot.py` · `config.py` · `engine.py` · `executor.py` ·
`order_resolution.py` · `order_journal.py` · `safety.py` ·
`process_lock.py` · `pnl_ledger.py` · `strategy.py` · `signals.py` ·
`fees.py` · `schemas.py` · `kalshi_client.py` · `market_data.py` ·
`research_log.py` · `replay.py` · `analyze.py` · `tests.py`
