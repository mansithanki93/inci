# Inci Tennis v1 Complete Design

**Status:** Approved for phased implementation on 2026-07-27.

**Purpose:** Build a research-only, event-driven tennis paper-trading system
that can test whether point-by-point tennis state creates an executable edge
in Kalshi singles match-winner contracts.

This design incorporates the independent strategy/model, data/exchange, and
operations adversarial reviews. Those corrections are requirements, not
optional hardening.

## 1. Safety Boundary

Tennis v1 is a separate package from the existing Inci v6 price-dip research
bot.

- Existing v6 behavior and its 200-test baseline remain unchanged.
- The current v6 baseline is byte-pinned even while it remains user-owned,
  uncommitted work. Tennis changes are not merge-ready until an operator
  supplies a base commit matching all protected hashes; the Tennis worktree
  never stages those pre-existing files.
- Tennis v1 has no HTTP `POST`, `PUT`, `PATCH`, or `DELETE` order surface.
- Demo and live orders cannot be enabled by configuration, environment
  variables, command-line flags, or provider manifests.
- Tennis v1 uses a read-only Kalshi transport.
- Phase 1 contains no provider or Kalshi network transport at all; read-only
  adapters begin only after the entitlement and dependency gates exist.
- Tennis v1 and v6 acquire the same account/environment process lock and are
  mutually exclusive. Concurrency requires a future supervisor with a shared
  rate-limit budget and account-level risk ledger.
- No provider subscription, automatic trial upgrade, or paid feature is
  authorized by this design.
- A provider trial ending disables that provider. It never triggers payment.

Paper results are research evidence only. They cannot authorize live trading.
Any future demo or live implementation is a separately reviewed code change.

## 2. Product Scope

### Included

- Two-player tennis singles.
- Match-winner contracts only.
- ATP, WTA, Grand Slam, Challenger, qualifying, and ITF matches when the exact
  match, format, contract, live-state feed, and executable book all qualify.
- Discovery of the complete available Kalshi tennis game-contract universe.
- A quota-bounded, feed-covered observed pool.
- Ranking and displaying the strongest ten eligible individual contracts.
- One official one-contract paper scorecard.
- Separate, non-influencing 5-, 10-, and 20-contract capacity scorecards.
- Historical pre-match fair-value modeling.
- Point-by-point live state updates.
- Full Kalshi order-book reconstruction.
- Causal paper IOC execution.
- Deterministic replay and sealed forward evaluation.

### Excluded

- Doubles.
- Set, game, point, spread, total, exact-score, futures, and multivariate
  contracts.
- Name-only or fuzzy automatic match binding.
- Inventing missing point paths.
- Automatic provider failover during an official scorecard.
- Claims about unsupported or unobserved tours, tiers, formats, or markets.
- Paid feeds before the evidence and business gates pass.
- Live or demo orders.

## 3. Release Gates

Each gate is fail-closed and must be independently visible in the dashboard
and research artifacts.

### Gate A: Provider entitlement

The provider's current terms and any written permission must allow the exact
research use, data retention, derived signals, and prediction-market context.
Trial expiry and retention deadlines must be machine-enforced.

### Gate B: Provider capability

The selected product tier must demonstrate stable match/player identifiers,
server and score state, source timestamps, sequence or revision semantics,
correction/resynchronization behavior, documented quotas, required tennis
formats, and adequate simultaneous-match coverage.

### Gate C: Exact binding

The provider match and both players must map to one Kalshi match-winner market
and exact YES exposure. Tournament, draw, round, schedule, format, and
settlement rules must agree. Any ambiguity blocks the match.

### Gate D: Causal synchronized state

The provider state and Kalshi book must both be fresh, sequence-complete,
clock-health-qualified, and in a reconciled epoch. A gap or correction starts
a new epoch and blocks entries until a safe snapshot barrier completes.

### Gate E: Executable research opportunity

The complete frozen policy must have positive conservative expected realized
P&L after fill probability, full-depth price, latency scenarios, slippage,
fees, exit behavior, and uncertainty.

### Gate F: Sealed evidence

A preregistered forward evaluation must reach its fixed, statistically powered
endpoint and pass both the alpha and business gates. Looking early does not
shorten the test.

## 4. Architecture

Tennis v1 lives under `tennis_v1/` and owns its runtime contracts:

```text
provider adapters ─┐
                   ├─> bounded ingress ─> durable WAL ─> sequencer
Kalshi read-only WS ┘                                  │
                                                      v
                                              pure state reducer
                                                      │
                     ┌────────────────────────────────┼──────────────┐
                     v                                v              v
               fair-value model                policy/risk      snapshots
                     │                                │              │
                     └──────────────> paper executor <┘          dashboard
                                           │
                                           v
                                derived audit + scorecards
```

### Determinism rule

Every provider frame, Kalshi frame, connection event, subscription
acknowledgement, correction, timer firing, and simulated order arrival receives
one strictly increasing `ingest_seq`. Only one reducer updates authoritative
state. Replay uses `ingest_seq`, never source timestamp sorting.

### Dependency rule

- Transports produce raw events and cannot access strategy state.
- The WAL persists a raw event before the reducer can observe it.
- The reducer is pure: prior state plus one event produces new state and
  derived events.
- One session-bound provider authorizer is constructed from the exact qualified
  provider binding. Capture, receive, persistence, transformation, derived
  persistence, close, and replay cannot substitute a different provider,
  entitlement, manifest, qualification artifact, adapter, request, or time
  window.
- Models and policy receive immutable state snapshots.
- The dashboard receives overwriteable snapshots and never performs network
  calls or blocks the reducer.
- Capacity scorecards receive the same raw events but maintain separate
  counterfactual portfolios.

## 5. Provider Entitlement and Qualification

An actual provider manifest is local state outside Git. A committed example
contains no credentials or claimed permissions.

The manifest records:

- provider and product-tier identifiers;
- entitlement identifier and source-lineage identifier;
- terms URL and version/date;
- an external written-permission evidence path and exact SHA-256 when
  required;
- allowed use, raw retention, derived-signal use, and publication status;
- trial start and expiry;
- raw-retention deadline;
- credential environment-variable names, never credential values;
- quotas and simultaneous-match capacity;
- stable match/player IDs;
- event/generated timestamps;
- sequence, correction, and resync behavior;
- supported match formats, tours, tiers, and rounds;
- qualification-soak evidence and result.

Qualification is not a self-declared manifest Boolean. The manifest points to
one repo-external qualification artifact and pins its exact SHA-256. The
loader verifies those bytes once and derives status, qualification time,
observed-match count, concurrent capacity, tested capabilities, structured
tour/tier/format/round strata, source lineage, and adapter-code SHA from that
artifact. A `passed` artifact with zero evidence, a missing time, a changed
adapter, or a provider/product/lineage mismatch is denied.

The qualification validity deadline is no later than the verified
`analysis_expires_at` deadline and no later than 30 days after
`qualified_at`. Trial terms intrinsically require
`analysis_expires_at == access_expires_at`, so trial qualification cannot
outlive access. Written permission may carry a separately verified
post-expiry analysis grant and later analysis deadline; analysis still requires
the authoritative runtime clock to be strictly before the analysis,
qualification, and raw-retention deadlines.

The immutable Tennis configuration stores both the external manifest path and
its expected SHA-256. The loader opens the file once, verifies exactly the
bytes it parses, rejects symlinks and non-regular files, and never trusts a
digest embedded inside the manifest. Written-permission evidence follows the
same external-path and digest-pinning rule.

Session provenance stores separate exact-file and canonical-semantic digests
for configuration and provider manifest. The names never overload one digest
with both meanings; the qualification and written-permission SHA values always
refer to exact external artifact bytes.

The qualified provider binding also constructs the only provider authorizer for
that session. The authorizer is bound to the session-manifest digest and
research-request digest before any provider-bearing WAL is created. Runtime
operations recheck that same binding; an arbitrary callback, rebuilt manifest,
or independently selected provider gate cannot authorize the session or its
replay.

Requested coverage is structured, not a free-form tag. Session end plus its
safety margin must fit inside the access window; the requested analysis and
raw-retention horizon must fit inside both the independent analysis deadline
and the raw-data deadline. Every requested rate, daily, burst, connection,
subscription, and resync demand is compared independently with the verified
product tier.

An unverified permission or capability is not equivalent to permission. The
gate blocks network startup when:

- the manifest is missing or malformed;
- any required permission is unverified or denied;
- the trial is expired;
- the retention deadline cannot cover the research session;
- the provider lacks a mandatory causal capability;
- credentials are absent;
- the requested pool exceeds a documented quota;
- retained data has passed its allowed deadline; or
- the qualification soak has not passed for the requested stratum.

Access expiry immediately blocks new receive, retry, resync, transformation,
prediction, and persistence. Retained data may be replayed or analyzed after
access expiry only when the manifest carries separately verified permission
and the authoritative clock is strictly before the analysis, qualification,
and raw-retention deadlines for that operation. Raw retention by itself does
not imply analysis permission.

Retention is physically enforced, not represented only by timestamps in event
metadata. A provider-bearing WAL is homogeneous under one conservative
session-wide delete-by deadline. Before the WAL can be created, a restrictive,
durable retention marker binds the session ID, WAL path, session-manifest
digest, provider binding, and delete-by deadline; a provider WAL is never
written unless that marker has been armed and its parent directory `fsync`ed.
Startup, new-session creation, and replay first purge every due marker. At or
after delete-by, no provider WAL, payload, or provider-derived artifact may be
opened or read.

Purge is crash-safe and ordered: unlink the provider-bearing WAL and its
provider-derived artifacts, `fsync` each affected parent directory, then remove
the marker and `fsync` its parent. A crash may leave an armed marker for already
deleted files, which the next purge safely completes; it must never leave
provider bytes without an armed marker. Any overdue marker, unlink failure,
directory-`fsync` failure, or ambiguous purge state blocks sessions and replay
and causes a global halt.

Provider selection is per match, not per tour. Two vendors with the same
upstream lineage are one source for disagreement purposes. A secondary source
is a disagreement detector; fields from conflicting feeds are never blended
into a synthetic score.

## 6. Tennis State and Match Format

The normalized state includes:

- canonical match and player IDs;
- tournament, season, draw, round, tour/tier, and surface;
- indoor/outdoor when known;
- best-of format;
- standard, deciding-set, match-tiebreak, no-ad, short-set, or other declared
  scoring rules;
- sets, games, points, tiebreak points, current server, and match status;
- provider source, sequence/revision, epoch, source time, generated time,
  receipt times, and trust state.

Non-adjacent, backward, impossible, or ambiguous updates are not repaired by
guessing. They start a new state epoch, block entries, and mark the affected
interval non-evaluable. Re-entry requires a complete provider snapshot that
reconstructs a valid state.

## 7. Exact Match and Contract Binding

Every eligible match has a frozen, hashed binding manifest containing:

- provider match ID;
- stable provider IDs for both players;
- canonical internal match/player IDs;
- tournament, season, draw, round, singles flag, format, and scheduled time;
- Kalshi series, event, and market tickers plus market ID;
- exact YES player/outcome;
- both economically equivalent YES/NO execution routes;
- contemporaneous market and settlement-rule text, source, version, and hash.

Structured fields must agree. Display names can assist a human review but
cannot authorize an automatic binding. Ambiguous qualifier, lucky-loser,
substitution, reschedule, rematch, or duplicate-name cases require manual
approval before research and create a new binding version.

Once a paper order is pending or a paper position exists, the binding is
immutable and remains pinned even if the match leaves the ranked pool.

Paper settlement is taken only from Kalshi's finalized result. Determined,
provisional, disputed, or amended states remain provisional. Pre-point
walkovers, post-point retirements/defaults/disqualifications, substitutions,
postponements, disputes, amendments, and finalization are distinct states.

## 8. Kalshi Market Data

Tennis v1 uses an authenticated read-only WebSocket and read-only REST
reconciliation.

- Subscribe explicitly with `use_yes_price: true`.
- Store prices and quantities as decimal strings and parse with `Decimal`.
- Require the initial snapshot before accepting deltas.
- Validate market ticker, market ID, subscription ID, sequence, price range,
  quantity, and nonnegative resulting levels.
- A gap, duplicate/out-of-order delta, reconnect, or subscription change
  invalidates the affected book and starts a new connection epoch.
- Re-enable only after acknowledgement plus a fresh valid snapshot and active,
  executable opposite-side depth.
- Never subtract public trades from the book; book deltas already reflect
  fills.
- Use lifecycle/trade channels as evidence, with REST reconciliation after
  gaps because not every channel supplies a complete sequenced history.
- Open, pending, and exiting matches remain subscribed regardless of top-ten
  rank.

## 9. Time and Durable Research Record

Every admitted raw event records:

- schema and adapter version;
- source and event type;
- exact source entity and connection epoch;
- source sequence/revision when present;
- provider event and generated timestamps when present;
- local UTC wall-clock receipt time;
- local monotonic receipt time;
- measured clock uncertainty;
- sanitized endpoint, channel, and request ID, each represented by a typed safe
  redaction sentinel rather than omission when its raw value cannot be retained;
- validated content type and payload transformation;
- original payload bytes or a permitted lossless representation;
- payload hash; and
- global `ingest_seq`.

Credentials, authorization headers, private-key material, and query-string
API keys are never journaled. Adapters cannot instantiate raw envelopes
directly. Strict capture factories reject duplicate JSON keys, unsafe content
types, headers, request objects, credential-like keys, PEM material, and URLs
with user information or queries; an approved redaction factory removes
secret values before any durable payload or hash exists. Transport errors are
rebuilt from an allowlist rather than serializing exceptions.

Safe capture includes complete provenance and does not trust adapters to assert
runtime-owned facts. A session clock authority supplies local wall/monotonic
receipt times and measured clock uncertainty, and a connection authority
supplies the connection epoch. The capture factory validates the bound source
entity plus bounded endpoint, channel, request-ID, and content-type fields and
rejects any mismatch with the session authorizer. Adapters provide permitted
source timestamps, source sequence/revision, and body bytes only; they cannot
construct or overwrite the authoritative envelope.

Before a provider-bearing WAL is opened, the runtime verifies its armed,
not-due homogeneous retention marker and its exact session/provider binding.
Replay performs the same purge and authorization before opening the WAL, then
requires the durable `SESSION_START` manifest to match the externally expected
session-manifest digest before reading a provider payload. Analysis permission
never permits reading provider bytes at or after the delete-by deadline.

The WAL is framed, checksummed, versioned, append-only, and `fsync`ed before a
raw event reaches the reducer. Torn or corrupt tails, disk-low conditions,
writer errors, missing clean terminals, and trace-hash mismatches make the
session unable to pass exact replay. Derived events reference parent
`ingest_seq` values and are recomputed during replay. A clean replay must
reproduce the full-record trace hash exactly. Any possible write or `fsync`
failure permanently poisons writer and runtime; the uncertain sequence is
never reused and no terminal is fabricated.

Failure handling distinguishes logic health from durability certainty. An
authorization, capture-contract, reducer, ingress, clock, or other logic
failure while the writer is known healthy appends and `fsync`s exactly one
`SESSION_HALT` with a stable machine-readable reason before closing. Once any
WAL write or `fsync` has an uncertain outcome, the writer is poisoned and no
further WAL record or terminal is attempted; the missing terminal is itself
the durable-evidence failure.

Every clean terminal, and every halted terminal written by a healthy writer,
includes the last successfully applied raw `ingest_seq` and a SHA-256 witness
of the canonical final authoritative state at that boundary, in addition to
the session/config/code/provider fingerprints, counts, and derived trace hash.
Exact replay recomputes and compares the final-state witness; a terminal with a
missing or mismatched witness cannot pass exact replay.

`wal_valid`, `exact_replay`, and `research_evaluable` are separate states.
Phase 1 can establish the first two mechanical properties, but always records
`research_evaluable=false` because exact binding, synchronized market state,
executable policy value, and sealed forward evidence do not yet exist.

After a crash, the prior scorecard ends with residual state and is
non-evaluable unless exact recovery is explicitly supported. A new session
replays through the last valid frame, obtains fresh provider and book
snapshots, and blocks entries until both converge.

## 10. Models and Trading Objective

### Pre-match fair value

Historical features are computed strictly as of the match start:

- surface-adjusted serve/return performance;
- opponent strength;
- recency with shrinkage;
- player/tour/tier uncertainty;
- format and surface;
- chronologically available rankings and form.

The prematch prior is frozen at match start. Each live point is consumed once.
Sparse and out-of-support players receive larger uncertainty and may be
blocked.

### Live update

The live model updates match-win probability from the valid tennis state and
the frozen prior. Calibration is measured in the selected-action region by
odds, side, tour, tier, surface, score state, and data-quality class.

### Unified policy value

Settlement fair value and scalp-path estimates are not independent edges.
The official decision target is:

```text
E[net realized P&L | current state, frozen entry/exit policy]
```

It includes no-fill, partial-fill, target, stop, timeout, settlement, market
halt, fees, slippage, latency, and displayed-depth outcomes. Fair value and
path estimates may be model features or conservative gates, but their
probabilities or edges are never added or multiplied as independent evidence.

Because no causal historical point-plus-Kalshi-L2 corpus currently exists, the
path model begins in shadow status. Its infrastructure can be built
concurrently, but it cannot authorize official paper orders until trained and
frozen on forward-captured development data.

The old fixed dip strategy remains an executable paired baseline only.

## 11. Universe, Ranking, and Entry

The universe is a visible funnel:

```text
discovered
  -> singles match-winner
  -> exact binding
  -> provider-entitled and feed-covered
  -> synchronized and fresh
  -> executable book
  -> model-supported
  -> eligible observed pool
  -> ranked top ten
```

Static discovery and liquidity screening can cover the broad universe. Live
point feeds are allocated only to the quota-bounded observed pool. The ranker
therefore claims "top ten within the eligible observed pool," never top ten
across unobserved worldwide matches.

Ranking uses a frozen shrinkage-adjusted lower bound on net policy value,
fillable depth, expected holding time, and risk. The cadence, tie-breakers,
minimum residence time, hysteresis, value-change threshold, same-match
re-entry rule, and opposite-side conflict behavior are preregistered.

Every candidate, eligibility transition, rank, signal, rejection, submitted
paper IOC, partial/no fill, exit, and settlement is logged. A large unexplained
book move that is not supported by the synchronized score state blocks entry
instead of being treated as a dip.

## 12. Paper Execution and Risk

Official paper execution is taker-style IOC:

1. A decision records its observed state and limit-price cap.
2. An `ORDER_DUE` timer event is scheduled at decision monotonic time plus a
   preregistered latency-scenario draw.
3. At `ORDER_DUE`, the policy is rechecked against causally available state.
4. The IOC consumes complementary full-book depth level by level up to its
   limit.
5. Zero and partial fills are normal outcomes; the remainder cancels
   immediately.
6. Per-fill fees use the effective versioned series/event schedule and exact
   rounding.

Paper research cannot measure actual matching-engine arrival latency without
orders. Results therefore report scenario distributions and clock uncertainty
at p50, p95, and p99 rather than claiming real engine latency.

A conservative virtual-liquidity ledger prevents simultaneous official paper
orders from reusing displayed depth. Four capacity portfolios (1, 5, 10, and
20 contracts) use independent counterfactual liquidity and state. They are
not independent statistical samples, and only size 1 is the official
scorecard.

Risk is keyed by canonical match ID across every ticker and outcome. The
single reducer atomically reserves risk before executor scheduling. Pending
buys, partial inventory, open inventory, and pending exits occupy position
capacity. One match can have at most one net directional exposure. The
official portfolio has at most three occupied match slots.

Session loss, match loss, consecutive-loss cooldown, stale-data behavior,
unvaluable exposure, and close/settlement behavior are frozen before a sealed
test. Any inability to value an occupied position is a global halt, not a
local quarantine.

## 13. Dashboard and Operations

The dependency-free terminal dashboard displays:

- coverage funnel and observed-pool quota;
- match, tournament, score, server, format, and trust epoch;
- book bid/ask, spread, full-depth summary, sequence, and age;
- fair probability with uncertainty;
- conservative policy value, fill probability, rank, and reason code;
- official and capacity positions, fees, realized/unrealized P&L;
- provider latency, corrections, disagreement, disconnects, and entitlement
  expiry;
- WAL health, free disk, clock health, process-lock status, and clean-terminal
  state.

Rendering consumes immutable overwriteable snapshots. Slow or broken display
code disables the UI only. WAL backpressure, writer failure, or disk-low state
blocks new entries and globally halts before any raw event is dropped.

State directories use restrictive permissions. Session rotation occurs only
at a boundary. Closed files receive checksum manifests and may be compressed
only after all retention rules permit it. Compression or rotation never
changes the armed delete-by deadline and must update the marker atomically
before replacing a provider-bearing path.

## 14. Validation and Payment Decision

### Dataset stages

1. Historical training set for the pre-match/live fair model.
2. Chronological development and calibration set.
3. Integration burn-in for feed, binding, book, and simulator repair.
4. Sealed confirmatory forward set.

Burn-in is never evidence. Before the sealed set begins, hash the model,
features, ranking, policy, simulator, latency scenarios, fee tables, rule
manifests, exclusions, baselines, endpoint, and analysis code. An
outcome-informed change invalidates and restarts the sealed test.

### Statistical gate

The endpoint is selected by a documented power calculation for the minimum
economically meaningful after-cost edge. Fixed counts such as 100 matches,
200 trades, or ten days are descriptive minimum-monitoring targets, not proof.
Do not stop when results first become positive.

The business estimand is all-in portfolio daily P&L, with all activity first
netted within match. Inference uses paired tournament-week/time blocks and
checks dependence by player and tournament. Strategy selection, subgroup
inspection, and interim looks receive multiplicity control or an explicitly
valid sequential method.

Executable paired baselines receive identical opportunities, latency, costs,
risk, and size:

- the legacy dip strategy;
- a simple market-plus-score tennis model; and
- the strongest no-paid-feed policy.

The sealed result must have:

- a selection-adjusted conservative lower bound above zero;
- a paired lower bound above the strongest baseline;
- no disqualifying concentration by player, tournament, week, tier, side,
  surface, or data-quality class;
- acceptable preregistered drawdown and loss behavior; and
- all open positions finalized before the scorecard ends.

### Business gate

One-contract evidence proves only one-contract behavior. Capacity uses the
separately frozen arrival-depth scorecards and cannot extrapolate beyond
observed eligible volume. A paid feed is considered only when the conservative
profit projection at demonstrated capacity is at least twice the complete
feed cost. This is a business margin, not proof of alpha.

If rights, capability, statistical evidence, or economics are insufficient,
the decision is `NO SUBSCRIPTION`.

## 15. Failure Scope

- One malformed or unavailable flat match: quarantine that match.
- An exposed/pending match failure: global halt.
- Provider-wide sequence/correction/auth/quota failure: provider-wide entry
  freeze.
- Kalshi connection gap: affected subscriptions freeze until snapshot
  barriers complete.
- Binding drift: quarantine match; exposed binding remains pinned.
- Clock-health failure, WAL failure, disk-low state, reducer exception,
  replay mismatch, safety-ledger failure, or process-lock conflict: global
  halt.
- An overdue retention marker, failed/ambiguous purge, session-authorizer
  mismatch, or attempt to read provider bytes at/after delete-by: global halt.
- Provider replacement or failover: new epoch and binding validation; current
  official scorecard becomes non-evaluable.

Every reason is durable and machine-readable when the writer remains known
healthy. A durability-uncertain writer attempts no terminal; its missing clean
terminal, poisoned runtime state, and external operational diagnostics identify
the failure without pretending another WAL write was safe.

## 16. Phased Build

### Phase 1: Contracts and deterministic foundation

Phase 1 is a paper-research foundation only. It starts no provider or Kalshi
network transport and performs no simulated, demo, or live order execution.

- provider entitlement/qualification gate;
- immutable Tennis configuration;
- session-bound provider authorization and replay binding;
- canonical raw and sequenced event contracts;
- strict safe-capture factories and bounded multi-producer ingress;
- single-writer sequencer and pure reducer;
- armed physical retention markers and crash-safe due purge;
- framed checksummed WAL, recovery, terminal, final-state witness, and trace
  hash;
- isolated tests and unchanged v6 baseline.

### Phase 2: Binding and data adapters

- provider adapters and qualification soak tooling;
- exact canonical identity/binding manifest;
- tennis scoring reducer and correction epochs;
- read-only Kalshi WebSocket book and lifecycle reconciliation.

### Phase 3: Models

- chronological historical feature pipeline;
- fair-value model and calibration;
- shadow path/policy-value capture;
- coverage and uncertainty abstention.

### Phase 4: Paper policy, execution, and risk

- top-ten eligible-pool ranker;
- causal IOC simulator and virtual-liquidity ledger;
- atomic match risk;
- independent capacity portfolios;
- terminal dashboard.

### Phase 5: Replay and sealed evaluation

- end-to-end deterministic replay;
- paired baselines;
- powered preregistration;
- sealed forward scorecard and business decision.

Each phase must be independently testable. Later phases consume frozen
interfaces from earlier phases rather than importing mutable v6 runtime
objects.

## 17. Phase 1 Acceptance Criteria

- The exact copied v6 suite remains at 200/200 passing and every protected
  legacy file matches the recorded starting SHA-256 manifest.
- Tennis configuration and provider manifests are immutable and fail closed.
- No actual provider is enabled by a committed example manifest.
- Expired, unverified, retention-inadequate, quota-inadequate, and
  capability-inadequate providers cannot start ingress.
- Every provider-bearing WAL has one durable homogeneous delete-by marker armed
  before WAL creation; due purge runs before sessions and replay, and no
  provider bytes are read at or after delete-by.
- Crash-safe unlink plus parent-directory `fsync` removes due provider data;
  an overdue marker or any purge ambiguity/failure globally halts.
- Runtime and replay use the same exact session-bound provider authorizer and
  reject a provider, entitlement, artifact, adapter, request, or manifest
  mismatch before provider payload access.
- No secrets appear in configuration representations, errors, or WAL frames.
- Safe capture records complete source/entity/endpoint/channel/request/content
  provenance while clock uncertainty and connection epoch come only from their
  runtime authorities.
- Concurrent inputs receive one no-drop, gap-free durable total order; bounded
  backpressure failure produces a global halted outcome.
- A raw event is `fsync`ed before reducer invocation.
- Replay reproduces the exact derived trace hash.
- Every healthy-writer terminal carries a final canonical-state digest that
  exact replay recomputes; a healthy logic failure emits one `fsync`ed
  `SESSION_HALT`, while a durability-uncertain writer emits no terminal.
- Truncated, corrupted, duplicated, skipped, or reordered frames fail closed.
- Missing clean terminal prevents exact replay; Phase 1 never claims research
  evaluability even for a mechanically clean exact replay.
- Dashboard slowness cannot block ingestion or durability.
- Static inspection confirms the Tennis package contains no order mutation
  transport.
- All Phase 1 tests are network-free.

## Official References

- Kalshi Developer Agreement:
  https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf
- Kalshi live game-stat coverage:
  https://docs.kalshi.com/api-reference/live-data/get-game-stats
- Kalshi order-book WebSocket:
  https://docs.kalshi.com/websockets/orderbook-updates
- Kalshi order-direction and `use_yes_price` convention:
  https://docs.kalshi.com/getting_started/order_direction
- Kalshi market lifecycle:
  https://docs.kalshi.com/getting_started/market_lifecycle
- Kalshi API rate limits:
  https://docs.kalshi.com/getting_started/rate_limits
- Sportradar terms:
  https://developer.sportradar.com/sportradar-updates/page/terms-and-conditions
