# Inci Expert Tennis Strategy Design

Date: 2026-07-29
Status: Approved
Authority: paper research only

## 1. Purpose

Replace Inci v6's blind dip-retracement behavior with a synchronized,
score-aware, uncertainty-aware Tennis v1 research policy that trades only when
the conservative expected net result of the complete policy is positive.

Expert behavior does not mean frequent trading or guaranteed profit. The
system may monitor every eligible match and make zero trades when evidence is
insufficient.

## 2. Evidence motivating the change

The 2026-07-29 paper session exposed structural failures rather than a single
bad parameter:

- eight closed round trips produced two wins and six losses;
- realized session P&L was approximately -$17.93 before the remaining
  position was marked;
- the final daily paper P&L reached approximately -$30;
- entry targets projected only about $0.10-$0.16 of net profit while an
  ordinary configured stop could lose about $2 before adverse gaps;
- the runtime repeatedly re-entered continuing declines;
- mutually exclusive player contracts in the same match could be held
  simultaneously;
- the bot had no score, server, point, correction, or fair-value context.

This evidence diagnoses the v6 mechanics. It is not sufficient statistical
evidence to accept or reject every possible tennis strategy.

## 3. Scope and authority

### In scope

- tennis singles match-winner contracts;
- read-only tennis score-provider integration;
- read-only Kalshi market and full-order-book integration;
- exact provider-match-to-Kalshi-market binding;
- synchronized point, match, lifecycle, and book state;
- pre-match and live fair-value models with explicit uncertainty;
- conservative complete-policy value;
- event-level paper risk;
- one-contract official paper scorecard;
- separate non-influencing 5-, 10-, and 20-contract capacity scorecards;
- deterministic WAL, replay, dashboard, and sealed forward evaluation.

The first official implementation supports one live provider, one product
tier, and one source lineage for the complete observed pool in a session.
Historical model data is a separately entitled, lineage-sealed offline
artifact. Multi-provider disagreement and automatic provider replacement are
deferred.
Correct hashes alone do not authorize use: an official run requires a
fail-closed historical permission decision bound to provider, product,
lineage, allowed use, time window, retention/publication terms, dataset
manifest, and exact artifact digests.

### Out of scope

- set, game, point, total, spread, exact-score, futures, or multivariate
  contracts;
- demo or live order placement;
- maker-market-making strategies;
- automatic paid-provider upgrades or renewals;
- automatic provider failover during an official scorecard;
- claims of profitability before sealed forward evidence passes;
- changing the approved Tennis v1 Phase-1 durability and entitlement
  invariants except through a separately reviewed design.

The v6 strategy remains a frozen, one-contract paired baseline. It cannot
authorize Tennis v1 decisions.

## 4. Core decision invariant

An official paper entry is permitted only when:

```text
conservative lower bound of
E[net realized P&L | current synchronized state, frozen complete policy] > 0
```

The expectation includes:

- no fill and partial fill;
- entry and exit fees;
- spread and full-book impact;
- latency scenarios;
- target, invalidation, timeout, and settlement outcomes;
- adverse score changes;
- market suspension, closure, and residual inventory.

A positive take-profit result alone is not positive expected value.

## 5. System architecture

```text
Provider adapter ──> raw provider events ──> tennis-state reducer ─┐
                                                                  │
Kalshi adapter ────> lifecycle + trades + full order book ────────┤
                                                                  v
                                                    binding + synchronizer
                                                                  │
                                                                  v
                                                    trusted decision snapshot
                                                                  │
                         ┌────────────────────────────────────────┼─────────────┐
                         v                                        v             v
                 fair-value model                         policy-value model  risk
                         └────────────────────────────────────────┼─────────────┘
                                                                  v
                                                     paper decision/execution
                                                                  v
                                      evidence WAL + expert journal + replay
                                                  + scorecards + UI
```

Data capture, state reduction, modeling, risk authorization, execution
simulation, and presentation remain separate components with immutable
interfaces.

Canonical evidence contains no binary floating point. Probabilities, prices,
fees, expected value, and P&L use fixed-scale integers or canonical decimal
strings with explicit scale and rounding rules.

The sealed Phase-1 evidence runtime is not extended in place. Its 21
production modules durably record raw inputs under their existing manifest,
state, reducer, trace, and WAL contracts. A separately versioned and
separately sealed expert runtime consumes only raw records that the evidence
runtime has already acknowledged durable.
It writes a companion expert journal containing domain projections, trusted
snapshots, predictions, decisions, risk, fills, and scorecards, each bound to
the parent raw sequence and digest. Both journals share the exact session,
provider binding, retention deadline, and terminal outcome.

Before either journal or any transport exists, startup acquires the same
Kalshi account/environment/subaccount process-lock namespace used by v6. The
mandatory order is:

```text
account lock
  -> retention recovery and purge
  -> entitlement authorization
  -> retention arm
  -> evidence WAL and companion expert journal
  -> read-only transports
```

Failure at any step leaves later steps unstarted. v6 and the expert runtime
cannot monitor the same account/environment/subaccount concurrently.

The expert implementation has four one-way boundaries:

- a deterministic expert package containing contracts, reducers, models,
  policy, risk, simulation, replay, and evaluation;
- a restricted I/O package containing exact read-only Kalshi/provider network
  capabilities, account/process locks, historical-artifact storage, and
  companion-journal append/fsync/read/recovery/purge capabilities;
- provider-specific adapter packages containing strict wire-schema
  normalization.
- a capability-free runtime composition package that wires I/O ports to the
  deterministic expert engine, owns startup/shutdown order, and contains no
  model, policy, risk, execution, or transport implementation.

Phase 1 imports none of these packages. Deterministic expert code imports no
network, filesystem, wall-clock, randomness, or process capability. Network
code cannot import policy, risk, scorecard, or execution code. Adapters cannot
import I/O or policy. Only the composition package may import both the exact
read-only I/O ports and deterministic expert facades. Each package has an independently reviewed file inventory,
dependency allowlist, and code seal.

Replay first verifies the unchanged evidence WAL, then recomputes and compares
the companion expert journal. A missing, extra, mismatched, differently bound,
or retention-inconsistent companion record makes expert replay non-exact and
non-evaluable. This preserves old Phase-1 WAL compatibility while maintaining
the raw-before-reduce invariant.

## 6. Trusted match state

### Provider state

The provider adapter must supply, when applicable:

- stable match and player identifiers;
- match status and format;
- sets, games, points, and tiebreak points;
- current server;
- source and generated timestamps;
- sequence or revision information;
- correction semantics;
- complete snapshot/resynchronization capability.

### Kalshi state

The Kalshi adapter maintains:

- market and event lifecycle;
- complete YES and NO bid ladders;
- derived executable asks;
- book sequence and snapshot epochs;
- market trades;
- quote and transport timestamps;
- close and early-close behavior.

Kalshi currently supports real-time WebSocket order-book updates and
full-depth order-book snapshots. The implementation must validate the current
documented contract again when Phase 2 begins.

### Exact binding

One provider match and its two stable player identities must map to exactly
one Kalshi match-winner event and its two outcome contracts. Ambiguity,
orientation uncertainty, player substitution, or binding drift blocks the
match.

### Synchronization barrier

A trusted decision snapshot exists only when:

- provider and Kalshi identities agree;
- both feeds are fresh under frozen thresholds;
- sequences are complete;
- corrections are fully applied;
- provider and local clock uncertainty is acceptable;
- the score update associated with a book move is causally available;
- the market is open and executable.

An unexplained large book move blocks entry. It is never automatically labeled
a retracement opportunity.

## 7. Tennis probability model

### Pre-match prior

Compute features strictly from information available before scheduled start:

- surface-adjusted serve and return performance;
- opponent strength;
- recency with conservative shrinkage;
- rankings and form available at that time;
- tour, tier, surface, and format;
- player and matchup uncertainty.

The pre-match prior is frozen at match start.

### Live update

Consume each valid point exactly once and update match-win probability using:

- the frozen prior;
- current server;
- set, game, point, and tiebreak state;
- valid live serve and return observations;
- match format;
- explicit model uncertainty.

The first model must be interpretable and calibrated. More complex machine
learning can replace its internals later without changing the policy
interface.

Sparse players, unsupported formats, or out-of-distribution states increase
uncertainty and may force abstention.

### Model output

Each decision snapshot includes:

- fair match-win probability;
- conservative lower and upper bounds;
- calibration stratum and support status;
- model version and feature digest;
- explicit abstention reason when unsupported.

Market price is a benchmark and policy input, not a substitute for an
independent tennis estimate.

## 8. Entry and exit policy

### Entry

Entry requires all of:

- a trusted synchronized snapshot;
- model support and acceptable uncertainty;
- executable price within a frozen limit;
- complete-policy conservative value strictly above both zero and the
  nonnegative sealed entry threshold;
- sufficient full-book depth for the selected scorecard;
- no unexplained move, stale state, lifecycle restriction, cooldown, risk
  conflict, or pending order;
- successful atomic reservation of canonical match risk.

Price movement, stabilization, volatility, and score transition may be model
features or conservative gates. A raw dip is never sufficient.

### Exit

The frozen policy may exit when:

- the executable market converges to fair value;
- the score state invalidates the entry thesis;
- conservative policy value becomes negative;
- the maximum holding horizon is reached;
- match or portfolio risk requires closure;
- settlement is reached and settlement was included in the frozen policy.

Fixed 5-cent profit and 6-cent stop thresholds are removed from the expert
policy. Hard limits remain for maximum monetary and unpriceable-exposure risk.

## 9. Event-level risk

Risk keys use canonical match identity across all outcome tickers.

- One match may have at most one net directional exposure.
- Opposing player contracts may not be held or pending simultaneously.
- Pending buys, partial fills, open inventory, and pending exits occupy match
  capacity.
- Official paper size is exactly one contract.
- Capacity scorecards are isolated counterfactuals and cannot influence the
  official policy.
- At most three canonical matches may occupy official portfolio slots.
- A stopped or invalidated thesis cannot immediately re-enter.
- Re-entry requires complete signal reset, a new trusted score state, and the
  frozen cooldown rule.
- Per-match attempt, loss, and exposure limits are frozen before a scorecard.
- Consecutive-loss and session-loss limits trigger a portfolio cooldown or
  halt.
- Any inability to value occupied exposure is a global halt.

Threshold values are selected using development data, preregistered, and
sealed before forward testing. They cannot be tuned from test outcomes.

## 10. Paper execution

Official paper execution is taker-style, bounded-limit IOC:

1. Persist the decision snapshot and limit cap.
2. Schedule an order-due event using a preregistered latency scenario.
3. Recheck policy, lifecycle, synchronization, and risk at order due.
4. Consume complementary full-book depth level by level up to the limit.
5. Record zero, partial, or complete fill.
6. Cancel the unfilled remainder immediately.
7. Charge the effective versioned fee with exact rounding.

A virtual-liquidity ledger prevents simultaneous paper orders from reusing
displayed depth. Identical repeated observations cannot fabricate new
liquidity without evidence of a new book epoch or replenishment.

Results report p50, p95, and p99 latency scenarios rather than claiming
unmeasured matching-engine arrival latency.

## 11. Universe and ranking

Inci discovers the broad eligible tennis universe and exposes a visible
funnel:

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

The deeply observed pool is limited by provider quota. Ranking uses a frozen
shrinkage-adjusted lower bound on policy value, executable depth, uncertainty,
expected holding time, and risk. Monitoring ten matches does not imply a
required trade.

## 12. Audit, replay, and dashboard

Persist every:

- raw provider and Kalshi event;
- sequence gap, correction, disconnect, and resnapshot;
- binding and synchronization transition;
- model prediction, uncertainty, and support decision;
- candidate rank and eligibility transition;
- rejection reason;
- risk reservation;
- paper order, zero/partial/full fill, cancellation, exit, and settlement;
- fee, realized P&L, unrealized P&L, and residual.

Raw inputs and Phase-1 acceptance records remain in the sealed evidence WAL.
Expert-derived records remain in the session-bound companion expert journal.
Replay must reconstruct the same trusted states, predictions, decisions,
orders, fills, risk, and scorecards from the same frozen code and
configuration, while proving every expert record's parent evidence digest and
retention binding.

Forward evaluation occurs only after clean session terminals. Its result is a
separate immutable `ResearchEvaluationArtifact` binding the exact session
replay digests, preregistration, analysis code, cost/gate inputs, and output
under its own permission and retention decision. It is never appended to the
terminal session companion journal.

The dashboard displays score, server, book, freshness, fair probability,
uncertainty, conservative policy value, rank, risk, positions, P&L, and a
machine-readable reason such as:

```text
WAITING edge_below_cost
BLOCKED score_stale
BLOCKED unexplained_book_move
BLOCKED post_stop_cooldown
ELIGIBLE conservative_value_positive
PAPER_BUY one_contract
```

UI failure disables the UI only. Evidence, state, or risk failure blocks
entries or halts according to exposure scope.

## 13. Failure scope

- A flat match with malformed or unavailable data is quarantined.
- Binding drift quarantines a flat match; an exposed binding remains pinned
  and triggers a global halt if it cannot be valued.
- Provider disconnect, sequence gap, correction ambiguity, or quota failure
  freezes affected entries until a complete snapshot barrier passes.
- An exposed or pending match with stale or missing required state causes a
  global halt.
- Evidence-WAL, expert-journal, reducer, clock, disk, risk-ledger, replay, or
  process-lock failure causes a global halt.
- Provider replacement creates a new trust epoch and invalidates the current
  official scorecard; there is no silent failover.
- Initial sessions permit one connection epoch per source. Provider REST
  endpoints share one worker/epoch; Kalshi uses one WebSocket epoch with
  in-band snapshots and no simultaneous REST order-book fallback.

## 14. Validation

### Development

- Collect synchronized provider and full-book data in shadow mode.
- Use chronological train, validation, and untouched future-test partitions.
- Freeze features, model, policy, risk, latency scenarios, and metrics before
  the official scorecard.
- Control repeated model and parameter searches; test data never influences
  tuning.

### Paired scorecards

Create the shared opportunity universe at every preregistered trusted
synchronized decision-clock snapshot, before any model, value, eligibility,
ranking, or policy-specific selection. Persist its ID and require every policy
to emit exactly one trade-or-abstain outcome for every shared ID. Apply
identical opportunities, books, latency, fees, capacity, and risk to:

1. no-trade benchmark;
2. frozen v6 dip baseline;
3. simple market-plus-score baseline;
4. expert score-aware policy.

### Promotion gate

The expert policy must demonstrate:

- positive net performance after all modeled costs;
- a selection-adjusted conservative lower bound above zero;
- a paired conservative lower bound above the strongest baseline;
- no disqualifying concentration by player, tournament, week, tour, surface,
  side, or data-quality class;
- acceptable preregistered drawdown and loss behavior;
- exact replay and clean session evidence;
- every official position finalized or conservatively marked under the frozen
  policy.

One-contract evidence proves only one-contract behavior. A paid feed is
considered only when conservative profit at demonstrated capacity is at least
twice the complete feed cost. Failure of evidence produces `NO SUBSCRIPTION`,
not parameter relaxation.

## 15. Delivery phases

### Phase A: synchronized read-only vertical slice

- one unregistered candidate adapter and an empty production registry;
- typed tennis snapshot/delta and correction reducer;
- exact provider/Kalshi identity binding;
- read-only Kalshi WebSocket full-book reconstruction;
- synchronization and freshness barriers;
- synthetic/recorded dual-feed evidence plus exact two-journal replay;
- no paper decisions.

Real dual-feed capture is a separate activation checkpoint. It requires the
candidate's real sanitized frames to pass the existing Phase-1 capture
factory, entitlement/retention/quota qualification, sequence/correction soak,
and independent registry review. Core completion cannot claim that checkpoint.

### Phase B: shadow intelligence

- historical feature pipeline;
- frozen pre-match prior;
- live probability update;
- calibration, uncertainty, and abstention;
- shadow predictions and diagnostics;
- no paper orders.

### Phase C: expert paper policy

- complete-policy value;
- candidate funnel and ranking;
- event-level risk and cooldown;
- one-contract IOC simulator;
- isolated capacity scorecards;
- dashboard and P&L attribution.

### Phase D: sealed forward evaluation

- preregistered future-match scorecard;
- paired baselines;
- concentration, drawdown, calibration, and capacity analysis;
- explicit pass/fail and feed-cost decision.

### Phase E: later execution consideration

Demo and live execution remain disabled. Either requires a new design,
adversarial review, explicit authorization, and successful preceding evidence.

## 16. Acceptance criteria

This design is implemented only when:

- the legacy v6 baseline remains behaviorally frozen except for separately
  approved containment controls;
- the sealed Phase-1 production modules, foundation state bytes, old WAL
  replay, and trace behavior remain unchanged;
- Phase A exactly replays synchronized synthetic/recorded dual-feed sessions;
- real Phase-A capture remains explicitly unfulfilled until a separately
  reviewed provider activation passes the capture, entitlement, quota,
  sequence, correction, and retention gates;
- expert replay verifies both the evidence WAL and its parent-bound companion
  journal under one session and retention decision;
- the expert policy cannot act without fresh score, server, exact binding,
  executable full-book state, model support, and atomic match risk;
- opposing outcomes and immediate falling-knife re-entry are mechanically
  impossible;
- one-contract official and capacity scorecards are isolated;
- every decision and abstention is explainable and replayable;
- demo and live execution remain unreachable;
- promotion is controlled exclusively by sealed forward evidence.
