# Live One-Match Tennis Shadow Collector: Design and Operations

Date: 2026-08-01  
Status: Implemented read-only research collector  
Trust: `unqualified_shadow`

## Purpose and hard boundary

This command records time-bracketed Sportradar tennis observations beside the
latest durably captured Kalshi order-book state for exactly one match and two
match-winner contracts. It produces synchronized research evidence only.

Every screen and evidence row is **READ ONLY / UNQUALIFIED / NO ORDERS**. The
collector does not produce or evaluate:

- trading signals or entries;
- fair value, edge, or BUY/SELL recommendations;
- P&L, replay profit, or strategy performance;
- paper, demo, or live orders;
- provider or strategy qualification; or
- trusted Tennis v1 synchronization evidence.

Kalshi access is confined to one authenticated `GET /api_keys` preflight that
proves the active key has exactly the `read` scope, followed by one active
read-only WebSocket connection at a time, an `orderbook_delta` subscription
for the two supplied tickers, and snapshot requests needed to restore book
continuity. There is no create, cancel, replace, portfolio, or order-status
route in this collector. Sportradar access is GET-only. No CLI flag or
environment value enables orders. Both Kalshi endpoints are fixed,
environment proxies are disabled, and redirects are refused so authenticated
headers cannot be forwarded to another host.

The operator-supplied home and away tickers are observational labels. The
collector does not prove that those contracts belong to the Sportradar match
or that the first ticker is the home player. Verify both mappings before a
session; the result remains unqualified even when the mapping is correct.

## Installation and credentials

Install the repository-pinned dependencies in the active virtual environment:

```bash
python -m pip install -r requirements.txt
```

Keep all credentials outside Git and load them through the environment:

```bash
export SPORTRADAR_API_KEY="your-trial-key"
export KALSHI_API_KEY_ID="your-read-only-key-id"
export KALSHI_PRIVATE_KEY_PATH="/absolute/path/outside-the-repo/key.pem"
chmod 600 "$KALSHI_PRIVATE_KEY_PATH"
```

`KALSHI_PRIVATE_KEY_PATH` must be absolute and must name a private,
owner-controlled regular file. Credential values and response bodies are not
printed in diagnostics or written to the shadow evidence ledger.

Use a dedicated Kalshi key whose scopes are exactly `read`. Kalshi keys that
also carry `write`, including older/default full-access keys, fail the scope
preflight before the WebSocket opens. Do not reuse Inci's existing trading key.

## Exact command

```bash
python -m inci_tennis_runtime.live_shadow_cli \
  --match-id sr:sport_event:123456789 \
  --home-ticker KXTENNIS-MATCH-HOME \
  --away-ticker KXTENNIS-MATCH-AWAY \
  --duration-seconds 600 \
  --poll-seconds 10
```

Arguments are intentionally narrow:

- `--match-id` is one Sportradar `sr:sport_event:<positive integer>` ID.
- `--home-ticker` and `--away-ticker` are two distinct Kalshi tickers.
- `--duration-seconds` is required and accepts 10 through 3,600 seconds.
- `--poll-seconds` accepts 1 through the session duration and defaults to 10.

The first provider call captures the match summary. Later provider calls fetch
the timeline at the poll interval while the event remains live. Kalshi frames
continue to be received and persisted while a provider request is in flight.
The session ends when its duration elapses, the provider reports a terminal
match state, the operator stops it, or a fail-closed error occurs.

## Trial call budget

For integer arguments, the command plans this many Sportradar requests:

```text
planned_calls = 1 + (duration_seconds - 1) // poll_seconds
```

Examples:

| Duration | Poll | Planned calls |
|---:|---:|---:|
| 10 seconds | 10 seconds | 1 |
| 60 seconds | 10 seconds | 6 |
| 600 seconds | 10 seconds | 60 |

The initial summary counts as one call. A timeline call is not started exactly
at the duration boundary. Each attempt is durably reserved before its GET, so
failed, timed-out, or interrupted attempts still consume trial quota. Before
either network transport is opened, startup compares `planned_calls` with both
the remaining per-session allowance and the remaining total-access allowance;
insufficient quota halts the command.

## Terminal dashboard

Startup immediately prints the safety banner, planned provider-call count,
and shadow evidence root. On an interactive terminal, each later dashboard
frame clears and replaces the prior frame. Redirected output appends complete
frames instead. Blocking terminal writes are moved off the event loop so a
slow flush cannot freeze unrelated async socket and timer work.

The dashboard fields mean:

| Field | Meaning |
|---|---|
| `MODE` | Always `READ ONLY / UNQUALIFIED / NO ORDERS`. |
| `TICKER MAPPING` | Always `OPERATOR-SUPPLIED / UNVERIFIED`. |
| `MATCH` / `PLAYERS` | Supplied Sportradar ID and provider-reported players. |
| `SCORE` / `SERVER` | Latest validated sets, games, points, and server observation. |
| `SPORTRADAR AGE` | Age of the provider-generated timestamp, or `--` when unavailable. |
| `HOME/AWAY TICKER` | The two operator-supplied labels. |
| `HOME/AWAY CANDIDATE BOOK` | YES bid/ask in dollar price units and top-level depth, or `--`. |
| `KALSHI STATUS` | Current projector state such as waiting, candidate, incomplete, or disconnected. |
| `KALSHI GEN / SEQ / AGE` | Connection generation, global sequence, and frame age. |
| `LAST EVENT` | Latest validated timeline event ID/type/result, when present. |
| `REASON` | Why this observational frame was recorded. It is not a recommendation. |
| `CAPTURES` | Durable Sportradar capture and Kalshi raw-frame counts for this process. |

The word `candidate` describes an unqualified display state, not a candidate
trade. Book prices are shown only after the subscription acknowledgement and
fresh snapshots for both tickers establish one aggregate sequence barrier.
Until both books are ready, both are hidden. A gap, duplicate, out-of-order
message, disconnect, malformed frame, or reconnect clears the displayed books;
a sequence gap requests a fresh snapshot. A valid empty or one-sided book is
shown as incomplete rather than fabricated or repeatedly resubscribed.

A quiet or apparently stationary screen is not evidence of a trade decision.
The program may simply be waiting for the next provider poll or Kalshi frame.
Use `CAPTURES`, ages, generation/sequence, and `REASON` to interpret progress.

## Durable evidence and restart behavior

State is derived from the operating-system account, not repository config.
Default paths are:

```text
~/.local/state/inci/tennis-shadow/
  shadow.lock
  session-<uuid>.jsonl
  raw/<session-uuid>-<frame-number>-kalshi.bin

~/.local/state/inci/sportradar-trial/
  usage.lock
  usage.jsonl
  outcomes.jsonl
  observations.jsonl
  raw/<access-attempt>_<route>.json
```

Files are private to the OS account. A Kalshi frame is written and fsynced as
immutable raw bytes before its durable capture receipt and before parsing.
Sportradar reserves quota before each GET, then durably records raw response and
outcome data before parser disposition. Joined observation rows reference both
raw artifacts by absolute path and SHA-256 digest and record provider request
start/completion clocks, Kalshi capture clocks, and combined uncertainty.

Each shadow row is bound to its session, contiguous row number, preceding row,
and canonical contents by a deterministic SHA-256 chain. Every referenced raw
provider capture is revalidated for ownership, private mode, path, and digest;
Kalshi references must point to a prior durable capture receipt in the same
session. Each `session-<uuid>.jsonl` must end with exactly one terminal record,
and its Kalshi raw-file inventory and digests must agree with the ledger. On
startup, a duplicate-key row, broken chain, forged reference, unclean session,
or other corrupt prior evidence causes a fail-closed refusal before collection
starts. Do not edit or delete those files to force startup; preserve them and
investigate the interrupted session. The independent Sportradar trial ledger
accounts for and records recovery of interrupted trial attempts so a restart
cannot restore spent quota.

The local hash chain has no external trust anchor. It detects edits, deletion,
and reordering within a ledger that remains present, plus missing raw files
referenced by present ledgers. It cannot prove that an operator did not delete
or roll back the entire state root. Archive completed ledger and raw trees to
separately controlled, immutable storage when rollback resistance matters.

Only one process may own each state root at a time. A second collector refuses
while the lock is held.

## Stop and failure behavior

Ctrl-C, SIGTERM, and SIGHUP request the same graceful stop. The collector stops
starting new work, closes the Kalshi socket, and durably writes
`operator_interrupt` terminal records to both evidence systems. The stop may
wait for in-progress durability work; it is not an instruction to abandon an
already-reserved provider attempt.

Task cancellation follows the same durability ordering, records `cancelled`
with the distinct trial-audit code `sportradar_shadow_task_cancelled`, closes
the socket, and then re-raises the original cancellation. Recoverable
Kalshi disconnects use at most three reconnect attempts with deterministic
1/2/4-second delays; a valid projection resets that budget. Protocol-terminal
errors and exhausted recovery halt immediately rather than reconnect forever.
Handled Ctrl-C, SIGTERM, or SIGHUP exits with shell status 130 after both
terminal records are durable.

Normal duration or match completion also writes a terminal record. Contract,
clock, persistence, quota, authentication, or transport failures halt closed,
clear candidate book values where applicable, and record a sanitized fixed
error code. A hard process kill or machine failure can prevent that terminal
write; the next shadow startup then refuses on the unclean session as described
above.

## Verification boundary

Automated tests use injected fake HTTP sessions, WebSocket connections, clocks,
temporary private state roots, and collector ports. They make no Sportradar or
Kalshi network calls and therefore consume no trial calls. Dependency tests pin
the exact HTTP/WebSocket library surfaces and review the allowed transport
imports.

Run the collector-specific no-network suite with:

```bash
python -B -W error -m unittest \
  tests.tennis_v1.test_sportradar_shadow_async \
  tests.tennis_v1.test_kalshi_readonly \
  tests.tennis_v1.test_shadow_evidence_integrity \
  tests.tennis_v1.test_live_shadow_collector \
  tests.tennis_v1.test_live_shadow_cli
```

Passing those tests proves the implemented contracts and fail-closed behavior
under their fixtures. It does not prove real-time provider correctness, match
binding, latency quality, data entitlement, strategy edge, profitability, or
trading readiness. The collector's output remains synchronized research
evidence with trust `unqualified_shadow` until a separate qualification process
is designed, implemented, and passed.
