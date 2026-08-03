# Inci Adaptive First-Set Dip Strategy Design

Date: 2026-08-02
Status: Draft for written-specification review
Authority: private shadow and paper research only

## 1. Purpose

Build an explainable Tennis strategy that:

1. creates a pre-match forecast from historical player strength, surface,
   serve and return performance, recent form, injuries or availability news,
   and external prediction evidence;
2. publishes match-winner and per-set win probabilities before play;
3. watches the complete first set without entering a position;
4. revises the remaining-match and remaining-set forecasts from trusted
   point-by-point evidence, with conservative shrinkage toward the pre-match
   prior;
5. considers fee-inclusive dip entries only in sets two and three; and
6. paper-buys at most $50 of exposure only when the conservative expected net
   liquidation profit within five minutes exceeds $5, then exits under the
   approved monetary and thesis-invalidation rules.

This replaces the legacy raw-price `dip_signal` as the experimental Tennis
entry policy. A price decline by itself is never a trade signal.

No strategy can guarantee profit. The first implementation measures whether
the hypothesis survives synchronized score data, real Kalshi depth, fees,
latency, corrections, and held-out evaluation.

## 2. Relationship to existing designs

This document narrows and extends the approved
`2026-07-29-inci-expert-tennis-strategy-design.md`.

It preserves:

- the sealed Tennis v1 evidence runtime;
- exact provider-match-to-Kalshi binding;
- sequenced full-order-book reconstruction and snapshot recovery;
- raw-before-derived evidence, the companion expert journal, and exact replay;
- one canonical directional exposure per match;
- fee-, spread-, depth-, latency-, and slippage-aware paper execution;
- fail-closed handling of stale, conflicting, corrected, or unpriceable state;
- an empty production-provider registry; and
- unreachable demo and live order placement.

It changes the research policy in four deliberate ways:

- multiple scorecard observations may contribute to a consensus score state;
- set one is a mandatory no-entry learning period;
- a separate five-minute markout model is required in addition to the
  match-outcome model; and
- the experimental policy uses dollar-denominated $50 / +$5 / -$5 rules,
  rather than the legacy five-cent take-profit and six-cent stop.

This draft does not silently replace the existing one-contract official paper
scorecard. One contract remains the official comparison policy. The `$10`,
`$25`, and `$50` variants are isolated experimental capacity scorecards until
the five-minute model is trained on forward-captured data, this written design
is approved, and the Slice 3 promotion gate passes. The `$50` variant is the
primary experiment requested here; it cannot influence the one-contract
baseline or authorize a live order.

The legacy v6 modules and behavior remain frozen as a comparison baseline.
New work belongs under the expert and restricted-I/O boundaries; it must not
add network, filesystem, model, signal, or order behavior to sealed Phase-1
modules.

## 3. Scope

### 3.1 In scope

- ATP, Challenger, WTA, WTA 125K, and ITF standard best-of-three singles when
  all required data and scoring rules are supported;
- standard best-of-three advantage scoring with supported tiebreak rules;
- match-winner Kalshi contracts;
- pre-match match-winner and individual-set probabilities;
- historical data imported as immutable, source-manifested artifacts;
- pre-match public news, injury, withdrawal, and availability evidence;
- external prediction probabilities imported as timestamped evidence;
- at least two and preferably three separately captured live scorecards;
- first-set Bayesian adaptation, followed by score-state-only updating with
  frozen service-strength posteriors in later sets;
- fee-inclusive five-minute markout forecasts;
- shadow signals and paper IOC execution;
- isolated strategy versions for later method comparison; and
- exact replay, calibration, markout, capacity, and P&L evaluation.

### 3.2 Out of scope for the first implementation

- live or demo Kalshi orders;
- autonomous browser-login flows;
- CAPTCHA solving, proxy rotation, fingerprint evasion, access-control bypass,
  or use of undocumented private credentials;
- shot-level ball tracking or broadcast computer vision;
- best-of-five, match-tiebreak, no-ad, short-set, or other nonstandard match
  formats;
- doubles, team competitions, exhibitions, nonstandard deciding-set formats,
  or matches whose rules cannot be normalized exactly;
- set-winner order execution unless a later design binds and validates an
  actual set-winner contract;
- generative-model decisions that cannot be deterministically replayed;
- automatic paid subscriptions or purchases; and
- retraining or threshold tuning from the held-out forward scorecard.

## 4. Approved monetary semantics

The phrases in the approved flow have the following exact meanings.

### 4.1 `$50 bet`

`entry_cash_at_risk` is the aggregate paper debit for the entry fill,
including entry fees and rounding. It must be greater than zero and no more
than `$50.00`. The requested contract quantity is derived from the current
bounded entry price and depth; it is not fixed at 50 contracts.

Unfilled quantity does not create exposure. Partial fills retain their actual
smaller debit and use the same dollar exit rules.

### 4.2 `profit within the next five minutes is more than $5`

The holding horizon is exactly 300 seconds from the simulated matching-engine
arrival of the entry order. The markout model produces a distribution of net
liquidation P&L at or before that horizon, including:

- entry and exit fees and fee rounding;
- executable asks and bids rather than midpoint prices;
- full-book depth and partial fills;
- configured latency and adverse slippage scenarios;
- the probability of no fill;
- likely score paths and server transitions;
- market suspension, early close, and unpriceable residual scenarios; and
- model uncertainty.

Entry requires the frozen conservative lower estimate of expected net P&L to
be strictly greater than `$5.00`. The arithmetic mean alone is insufficient.
Because the policy also exits at the first executable `+$5.00` trigger, this
is deliberately difficult and may produce no entries. The implementation must
report that result rather than weaken or reinterpret either threshold.

### 4.3 `sell if the loss goes below $5`

This is interpreted as a hard stop when fee-inclusive executable liquidation
P&L is less than or equal to `-$5.00`. It is evaluated from actual paper entry
cost and currently executable bid depth. A missing or stale bid never counts
as a harmless zero loss; it makes the exposure unpriceable and invokes the
existing fail-closed risk path.

The threshold is a trigger, not a guaranteed realized loss. Gaps, latency, and
insufficient bid depth may produce a worse simulated liquidation. Trigger P&L
and realized P&L are persisted separately.

### 4.4 Other exits

An open paper position exits at the earliest trusted opportunity when any of
these conditions holds:

- executable net liquidation P&L is at least `+$5.00`;
- executable net liquidation P&L is at most `-$5.00`;
- a separately frozen variable-remaining-horizon model says the conservative
  incremental value of continuing is no longer positive; Version 1 omits this
  trigger because it does not yet contain that model;
- the entry thesis is invalidated by a trusted score transition;
- the 300-second holding deadline is reached;
- a source disagreement, correction, lifecycle change, or risk rule requires
  closure; or
- the market converges to or exceeds the model's conservative fair value.

There is no averaging down. One strategy instance may have only one pending
or open direction for a canonical match. A stopped position cannot re-enter
until a new trusted score state, a complete signal reset, and the frozen
cooldown all occur.

## 5. Architecture

```text
Historical artifacts ─┐
News/injury evidence ──┼─> pre-match feature snapshot ─> outcome model ─────┐
External predictions ─┘                                                    │
                                                                            │
Scorecard adapters A/B/C -> canonical observations -> consensus reducer ───┤
                                                                            v
Kalshi WS -> sequenced full books -> exact binding + synchronization -> trusted snapshot
                                                                            │
                                  ┌─────────────────────────────────────────┤
                                  v                                         v
                       live outcome update                       5-minute markout model
                                  │                                         │
                                  └─────────────────────┬───────────────────┘
                                                        v
                                               policy + match risk
                                                        │
                                                        v
                                              shadow / paper IOC only
                                                        │
                                                        v
                                  expert journal + replay + paired scorecards
```

The outcome model answers, “How likely is each player to win the match and
each remaining set?” The markout model separately answers, “At this book,
score state, and size, what is the distribution of executable net P&L over
the next 300 seconds?” Neither may substitute for the other.

## 6. Component boundaries

### 6.1 Historical feature builder

Consumes only immutable historical match artifacts whose records predate the
target match. It resolves stable player identity and emits a canonical
`PreMatchFeatureSnapshot` with:

- overall and surface-adjusted Elo;
- opponent-adjusted service-point and return-point performance;
- recency-weighted form with explicit windows and shrinkage;
- rest, recent workload, and available match-duration evidence;
- head-to-head evidence with conservative small-sample shrinkage;
- tour, tournament tier, surface, indoor/outdoor state, match format, and
  scheduled start; and
- missingness, support counts, source digests, and as-of timestamps.

Feature definitions, recency windows, shrinkage parameters, and Elo update
rules are versioned and frozen before evaluation. Records at or after match
start are mechanically excluded.

The causal pre-match cutoff `T0` is the earlier of the scheduled start and the
first received in-play or serve event. Every eligible record must have been
received by `T0`; event time alone is insufficient. Corrections become usable
only when received and are never backdated during replay.

### 6.2 News and availability evidence

The pre-modeler may consume timestamped public evidence available before
match start. The first version recognizes only factual categories:

- confirmed withdrawal or walkover;
- recent retirement or medical timeout;
- reported injury, illness, or recovery;
- explicit player or tournament availability statement; and
- schedule or travel disruption.

Each item stores publisher, URL, publication time, capture time, quoted fact
or normalized factual summary, player binding, category, and confidence.
Unattributed rumors, post-start articles, and generative sentiment are not
eligible features. Missing news is `unknown`, never `healthy`.

News adjustments are bounded, versioned, and explainable. A confirmed
withdrawal blocks the match instead of manufacturing a near-certain trade.

### 6.3 External prediction evidence

Each external prediction is captured before scheduled start and normalized to
a probability with source, timestamp, methodology class when known, and raw
evidence digest. Bookmaker odds are de-vigged before use. Predictions derived
from Kalshi, copied from the same underlying source, posted after start, or
not bound to the exact players and format are excluded.

The model records external-prediction lineage and does not call a collection
independent merely because it appears on multiple websites.

Every missing input has an explicit indicator. The model must use an artifact
trained for the exact feature mask or abstain; it never dynamically reweights
whatever sources happened to be available for one match.

### 6.4 Scorecard adapters

Every scorecard adapter has one responsibility: capture a documented,
licensed score response and normalize it without making a trading decision.
It emits:

- source and source-lineage identifiers;
- stable or source-local match and player identifiers;
- scheduled start and match format when present;
- lifecycle status;
- completed sets, current games, point or tiebreak score, and current server;
- source-generated time or revision when present;
- local wall and monotonic capture times;
- raw response digest and parser version; and
- explicit missing, ambiguous, corrected, or unsupported fields.

Adapters use documented endpoints and explicit subscriber credentials only.
They obey source rate limits, caching rules, retention limits, and applicable
access terms. A blocked or disallowed source is disabled; the implementation
does not change identity, rotate addresses, discover undocumented browser
endpoints, bypass a challenge, or imitate a logged-in user.

Flashscore, Sofascore, and the public ATP, WTA, and ITF score pages are
human-only verification sources in this design. Their current terms prohibit
the automated harvesting contemplated here, so the build does not ship active
scrapers for them. Robots permission alone is not treated as a use license.

The three initial live adapter implementations target:

- API-Tennis REST/WebSocket;
- GoalServe REST/webhook; and
- Live Tennis API REST/WebSocket.

They are credential-gated and disabled by default. Naming an adapter is not a
claim that its subscription permits real-money prediction-market use. Before
activation, its manifest must prove the subscribed product, ATP/WTA/ITF
coverage, automated private-analysis permission, cadence, retention,
derivative-use rules, and exact session window. Fixture and replay adapters
are immediately usable but never count toward live consensus.

The initial research runtime supports three configured adapter slots. An
adapter is activated only when its manifest records the exact allowed access,
use, cadence, retention, derivative use, and publication decision.

### 6.5 Consensus reducer

Consensus is formed over normalized observations, not raw payload similarity.
One primary source is selected per match from a frozen ordering; other sources
are witnesses. A complete primary observation is accepted only when at least
one distinct permitted lineage reports the same complete state.

- Three active sources require the primary and at least one distinct permitted
  witness lineage to agree exactly.
- Two active sources require both to agree exactly.
- Fewer than two active permitted lineages cannot produce a trusted score.
- Known mirrors of one upstream lineage count once.
- Unknown lineage is recorded as unknown; agreement detects corruption but
  is not claimed as independent confirmation.

Agreement covers exact player pairing and orientation, lifecycle, completed
sets, current games, current point or tiebreak, and server. Observations must
fall within source-specific frozen freshness limits. A valid tennis state
transition must connect the prior consensus state to the candidate state.

Disagreement starts a quarantine barrier. The reducer waits for a complete
new consensus snapshot and records every competing observation. It never
majority-votes individual fields into a state no source actually reported. If
the primary disagrees while two witnesses agree, the match is quarantined;
the runtime does not silently outvote or replace the primary. A predeclared
failover source may become primary only at a new full-snapshot source epoch,
and its state still requires a distinct witness. Corrections create a new
correction epoch and invalidate derived decisions from the superseded state.

The runtime records source-receive-to-consensus latency for every transition.
Triangulation is allowed to reveal that the strategy is too slow: it is never
weakened during a session merely because a slower witness removes the apparent
edge.

### 6.6 Pre-match outcome model

Elo is a prior feature, not the complete live model. Version 1 estimates each
player's probability of winning a service point from historical serve,
return, opponent, surface, form, and bounded availability features. A
deterministic tennis scoring dynamic program converts those service-point
probabilities into:

- probability that each player wins set one;
- probability that each player wins each later set conditional on reaching
  it; and
- probability that a deciding set is reached; and
- match-win probability.

A calibration layer is fitted chronologically on development data and reports
support stratum, calibration error, and a probability interval. All input
features are timestamp-causal. Market prices and future prediction-site
updates are prohibited training features for the independent outcome model.

The complete `PreMatchPrediction` is persisted and frozen before the first
trusted point.

The pre-match ensemble combines eligible component probabilities on the
log-odds scale with frozen coefficients, then applies a frozen chronological
calibrator. Its serve-point priors are reconciled to the calibrated match
probability through one bounded matchup shift before the scoring calculation.
Match and per-set probabilities therefore come from one coherent scoring
model, not separately trained classifiers. If first server is unknown, the
result averages over a frozen first-server prior and widens uncertainty.

### 6.7 First-set and live adaptation

Set one is observation-only. No entry signal can be authorized, even if the
price and markout models appear favorable.

For each player, the model maintains a Beta prior over service-point strength.
The pre-match estimate supplies its mean and effective sample size. Every
trusted first-set service point updates the corresponding posterior exactly
once using a frozen evidence weight. The effective sample size and evidence
weight are learned from chronological development data and prevent one noisy
set from overwhelming long-run evidence.

At the end of set one, the runtime persists a `FirstSetReview` containing:

- pre-match prediction and uncertainty;
- observed service and return points by player;
- breaks and holds derivable from trusted score transitions;
- point timing support where source timestamps permit it;
- posterior service-point distributions;
- revised remaining-set and match probabilities; and
- every missing-data or abstention reason.

Version 1 freezes both service-strength posteriors when set one completes.
Sets two and three update match and set probabilities from exact score and
server state, but they do not re-estimate player strength. This isolates the
first-set-learning hypothesis and makes paired replay interpretable. A later
continuously adapting posterior is a separate strategy version, never an
in-session switch.

The first-set update requires a complete monotonic point/server history
confirmed by consensus. It never infers service-point counts from game scores.
An incomplete point sequence or ambiguous server blocks the posterior update
and all trades for that match.

Decisions occur only at point-result barriers. A barrier consists of one
complete accepted consensus transition followed by a causally paired,
sequenced Kalshi book snapshot. No inference is made from an in-progress
rally, a fieldwise partial score, or a book frame paired to the previous tennis
state.

### 6.8 Five-minute markout model

The initial markout model is an isolated, versioned strategy component. Its
features may include only information available at the decision snapshot:

- outcome-model fair value and interval;
- executable ask, bid, spread, full depth, and book imbalance;
- price distance from fair value;
- causal price changes over frozen lookback windows;
- current set, game, point, server, and leverage of the next point;
- first-set posterior changes and support counts;
- recent trusted point durations when available;
- market lifecycle and close horizon; and
- score, book, and clock freshness.

Its training label is simulated fee-inclusive executable P&L from a bounded
IOC entry at decision time to liquidation at the earliest of 300 seconds,
the frozen `+$5` or `-$5` trigger, policy invalidation, supported market
termination, or the first trustworthy forced-exit state. The label walks the
first causally eligible fresh book at each simulated order-due time; it never
uses the best future quote or assumes oracle exit timing. Labels consume future
data only during offline training and evaluation; no future field may enter
live features.

If no trustworthy book exists within the frozen tolerance after a simulated
entry or exit due time, the affected fill or label is unobservable. Such cases,
including disconnects, retirements, closures, and unresolved residuals, remain
in coverage accounting and are never silently discarded.

The repository has no causal historical point-plus-Kalshi-L2 corpus. Until
forward collection produces enough development observations and a model is
trained, calibrated, reviewed, and frozen, this component emits diagnostics
and abstentions only. It cannot authorize the official one-contract paper
policy or any experimental capacity fill.

Version 1 must expose at least the expected P&L, a conservative lower
estimate, fill probability, loss probability, tail-loss estimate, supporting
sample count, and an abstention reason. It must abstain for unsupported score
states, insufficient historical markouts, material distribution shift, or an
uncalibrated tail.

The strategy interface permits later markout methods, but a session freezes
one method and one parameter digest before collection. Results from multiple
methods are isolated counterfactual scorecards; they cannot select each other
using held-out outcomes.

### 6.9 Policy and risk

Entry is evaluated only after set one and only from a trusted synchronized
snapshot. It requires all of:

- match and format support;
- exact scorecard and Kalshi binding;
- score consensus and a valid transition;
- fresh sequenced executable book state;
- no active point ambiguity, correction, suspension, or close risk;
- supported and calibrated outcome and markout models;
- a genuine dip: current executable ask is below the model's conservative
  fair-value bound and at least seven cents below the greatest executable ask
  observed during the prior 45 seconds;
- conservative expected net 300-second P&L greater than `$5.00` at actual
  size and depth;
- entry debit including fees no greater than `$50.00`;
- successful canonical match-risk reservation; and
- all per-match, session, cooldown, and daily-loss limits passing.

The policy emits one decision record for every trusted decision snapshot,
including abstentions. A decision contains the exact probability, interval,
price cap, size, expected P&L distribution, fee version, source-consensus
digest, model digests, risk result, and reason code.

The 45-second lookback excludes the current observation and uses the same
contract side and valid score/book epoch. A lifecycle break, source epoch
change, sequence gap, or completed set resets the lookback. Seven cents and
45 seconds are frozen for Version 1; later values are separately versioned
counterfactuals. The dip gate is only a prerequisite and never substitutes for
the strict expected-P&L test.

### 6.10 Planned code ownership

The implementation reuses `TennisState`, `ProviderPoint`,
`OpportunityFrame`, `FairValueEstimate`, and `PolicyEstimate` from the expert
contracts, scoring transitions from `tennis_score.py`, synchronized
opportunities from `synchronizer.py`, and full-depth price walking from
`market_book.py`.

New deterministic modeling belongs in `inci_tennis_expert`, including
prematch evidence reduction, outcome and set estimates, score consensus,
Bayesian updates, five-minute markouts, policy value, fees, and risk. Network
and filesystem access belongs in `inci_tennis_io`; source parsing belongs in
`inci_tennis_adapters`; composition belongs in `inci_tennis_runtime`. The
legacy root strategy and executor are neither modified nor imported.

## 7. Paper execution

The first implementation remains taker-style bounded IOC paper execution.

1. Persist the trusted decision, risk reservation, and price cap.
2. Apply a preregistered order latency scenario.
3. At order due, require a new trusted score and book snapshot or reject.
4. Walk actual displayed depth up to the price cap and `$50.00` debit limit.
5. Record zero, partial, or complete fill and cancel the remainder.
6. Charge current versioned fees and exact account rounding.
7. Start the 300-second holding clock at simulated matching-engine arrival.
8. Evaluate exits on every trusted book or score transition.
9. Liquidate against actual bid depth; never use midpoint or stale depth.
10. Persist realized P&L, residual exposure, and every failed exit attempt.

Paper scorecards run at one-contract, `$10`, `$25`, and `$50` maximum debit as
isolated capacities. One contract remains the official comparison policy.
The requested `$50` experiment is the primary experimental scorecard; smaller
capacities diagnose price impact and fee rounding without influencing it.

## 8. Evidence and replay

Persist every raw source capture before derived use, subject to the source's
retention permission. Every derived record binds its parent sequence and
digest.

The companion expert journal adds:

- historical artifact and feature manifests;
- news and external-prediction evidence;
- normalized scorecard observations and source health;
- consensus candidates, disagreements, quarantines, and correction epochs;
- pre-match predictions and first-set reviews;
- every live posterior update;
- markout forecasts and support diagnostics;
- decisions and abstentions;
- risk reservations and cooldowns;
- paper order, fill, exit, fee, and P&L records; and
- strategy-version paired scorecards.

Replay must reconstruct byte-identical normalized observations, consensus
states, posterior values, model outputs, decisions, fills, and P&L from the
same frozen artifacts and configuration. Any missing raw capture, permission
record, digest, source timestamp, correction, terminal record, or model
artifact makes the session non-evaluable.

## 9. Failure handling

- One unavailable flat-match scorecard reduces consensus capacity and records
  source degradation.
- Fewer than two agreeing permitted lineages blocks new entries.
- Score disagreement, illegal transition, ambiguous server, or correction
  freezes entry until a complete consensus barrier passes.
- A score or book sequence gap invalidates derived state and requires a fresh
  snapshot.
- News or external-prediction failure marks those features missing; it cannot
  fabricate a negative or healthy signal.
- Unsupported historical identity, format, or insufficient model support
  causes abstention.
- A flat match may be quarantined independently.
- An exposed match with unpriceable, stale, or ambiguously bound state invokes
  the existing portfolio halt and conservative residual handling.
- Source access denial disables the adapter. It never triggers evasion.
- Evidence, journal, disk, replay, clock, lock, or risk-ledger failure causes a
  global halt.

## 10. Testing and evaluation

### 10.1 Deterministic tests

- Elo and surface-Elo fixtures;
- strict feature timestamp cutoff at match start;
- player identity and orientation resolution;
- legal and illegal tennis transitions;
- primary-plus-one-of-two, two-of-two, failover-epoch, mirrored-lineage,
  disagreement, stale, and correction consensus cases;
- exact scoring dynamic-program probabilities;
- Bayesian update, shrinkage, and duplicate-point rejection;
- set-one no-entry invariant;
- markout feature causality and future-label isolation;
- `$50.00` inclusive debit sizing;
- strict `>$5.00` conservative entry threshold;
- `+$5.00`, `-$5.00`, model invalidation, and 300-second exits;
- fees, rounding, spread, depth, latency, partial fills, and unpriced
  residuals;
- opposing-contract and re-entry prevention;
- exact replay and tamper detection;
- proof that real order endpoints remain unreachable;
- proof that deterministic packages have no network or filesystem access;
- exact package-inventory and hash-seal updates for every added module; and
- proof that a five-minute model without a trained, calibrated, frozen
  artifact can emit only `ABSTAIN`.

### 10.2 Source contract tests

Each real scorecard adapter has sanitized captured fixtures for normal,
tiebreak, suspended, corrected, retired, completed, missing-server, schema
change, and access-denied cases. Network tests are separate opt-in diagnostics
and cannot make deterministic tests depend on live websites.

### 10.3 Chronological research evaluation

Use train, validation, and untouched future-test partitions, grouped by match
and ordered chronologically with a frozen embargo around boundaries. Freeze
features, hyperparameters, thresholds, latency scenarios, and methods before
each forward scorecard.

Every shared opportunity is evaluated by:

1. no trade;
2. the frozen v6 blind-dip baseline;
3. pre-match fair value without first-set adaptation;
4. first-set-adapted fair value without the markout gate; and
5. the approved first-set-adapted five-minute strategy.

Report:

- number of matches, trusted states, candidates, entries, fills, and exits;
- source agreement, disagreement, staleness, and correction rates;
- match and set probability calibration, Brier score, and log loss;
- five-minute markout calibration and error by predicted-profit bucket;
- fee-inclusive net P&L, hit rate, profit factor, drawdown, and tail loss;
- entry-to-exit duration and exit-reason distribution;
- results by tour, surface, price, liquidity, player support, and source class;
- capacity at one-contract, `$10`, `$25`, and `$50` debit; and
- paired confidence intervals against every baseline.

Confidence intervals use match- or day-clustered resampling because overlapping
five-minute labels from one match are not independent. Coverage reports retain
disconnected, censored, retired, closed, and otherwise unobservable sessions.

Promotion requires positive held-out fee-inclusive P&L, a conservative paired
lower bound above the strongest baseline, acceptable drawdown and tail loss,
calibrated forecasts, no disqualifying concentration, exact replay, and clean
terminal evidence. Otherwise the result is `NO LIVE PROMOTION`.

## 11. Delivery slices

### Slice 1: source-neutral model and consensus foundation

- canonical contracts for features, news, predictions, scorecards,
  consensus, model outputs, markouts, decisions, and paper outcomes;
- immutable historical artifact importer and player identity mapping;
- surface Elo, serve/return priors, scoring dynamic program, and Bayesian
  update;
- three scorecard adapter slots plus deterministic fixtures;
- consensus reducer and replay; and
- no signals or paper orders.

### Slice 2: licensed live score capture and pre-match reports

- implement credential-gated API-Tennis, GoalServe, and Live Tennis API
  adapters, while activating only subscriptions with eligible manifests;
- require at least two permitted real score lineages before trusted state;
- capture raw scorecards and source health;
- generate pre-match match and set reports;
- run the first-set review and continuous score-state shadow revisions; and
- retain `NO ORDERS` authority.

### Slice 3: five-minute research dataset and model

- collect synchronized score/book opportunities and 300-second labels;
- train and calibrate the frozen markout model chronologically;
- emit shadow entry and exit decisions; and
- compare isolated strategy versions with no paper fills.

### Slice 4: paper policy

- bounded IOC simulator and virtual-liquidity ledger;
- `$50 / +$5 / -$5 / 300-second` policy and risk;
- dashboard, P&L, replay, and capacity scorecards; and
- sealed forward evaluation.

### Slice 5: later live consideration

Live execution is not part of this design. It requires a separate approved
design after the paper promotion gate passes, current exchange behavior is
revalidated, and the user explicitly authorizes real financial mutations.

## 12. Acceptance criteria

The implementation is accepted only when:

- pre-match inputs are causal, source-manifested, and replayable;
- match and per-set predictions include calibrated uncertainty;
- set one mechanically cannot authorize an entry;
- every live model update consumes one trusted consensus point exactly once;
- at least two permitted scorecard lineages are required for trusted state;
- disagreement and corrections cannot leak into a decision;
- the outcome and five-minute markout models are separate and versioned;
- entry debit including fees cannot exceed `$50.00`;
- entry requires a conservative expected net five-minute profit strictly over
  `$5.00`;
- exits enforce executable `+$5.00`, `-$5.00`, invalidation, and 300-second
  rules;
- spread, depth, latency, slippage, fees, no fills, partial fills, and
  residuals are modeled honestly;
- opposing positions, averaging down, and immediate post-stop re-entry are
  mechanically impossible;
- all decisions and abstentions exactly replay;
- held-out tests cannot influence features or thresholds;
- source denial cannot trigger access-control evasion;
- public Flashscore, Sofascore, ATP, WTA, and ITF pages are never automated;
- an unfrozen or unsupported five-minute artifact cannot authorize a paper
  fill; and
- real Kalshi order creation and cancellation remain disabled.

## 13. Source-access references

Access decisions are revalidated before implementation and again before each
live research session. Primary references reviewed for this draft:

- [Flashscore Terms of Use](https://www.flashscore.com/terms-of-use/)
- [Sofascore Terms and Conditions](https://www.sofascore.com/terms-and-conditions)
- [ATP Terms and Conditions](https://www.atptour.com/en/terms-and-conditions)
- [WTA Terms and Conditions](https://www.wtatennis.com/terms-and-conditions)
- [ITF Terms and Conditions](https://www.itftennis.com/en/about-us/terms-conditions/)
- [API-Tennis documentation](https://api-tennis.com/documentation)
- [GoalServe Tennis API sample](https://www.goalserve.com/en/sport-data-feeds/tennis-api/samples)
- [Live Tennis API reference](https://docs.livetennisapi.com/reference.html)
