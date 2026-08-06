# Live Models 1 and 2 Paper Bridge

Date: 2026-08-05
Status: Approved for implementation planning
Authority: live read-only inputs and simulated paper execution only

## 1. Purpose

Make the existing static and Bayesian tennis models operational during a live
match. The runtime must consume point-score observations and Kalshi full-L2
books, publish a forecast after every accepted point, and simulate bounded
paper entries and exits. It must never submit, cancel, or query the lifecycle
of a real order.

The first pilot intentionally distinguishes two claims:

- Models 1 and 2 estimate next-point, current-set, and match outcome
  probabilities.
- They do not estimate the Kalshi resale price exactly five minutes later.

Accordingly, Version 1 may trade a fee-aware settlement-value proxy and may
measure the position after five minutes, but it must not label that entry as a
five-minute price forecast. Model 3 remains the future component that learns
the five-minute markout and optimal-stopping value.

## 2. Non-negotiable boundaries

- Paper trades only. The legacy real/demo guards remain unchanged.
- Kalshi access is read-only and accepts only a key whose scopes are exactly
  `{"read"}`.
- The live-paper package cannot import `KalshiClient`, the legacy `Executor`,
  order creation/cancellation code, or any write-capable transport.
- The sealed offline pilot contracts and their two-lineage research authority
  are not weakened. Single-source live-paper inputs use separate contracts.
- No source disagreement, score correction, sequence gap, or missing point is
  guessed through.
- A price snapshot that caused a decision cannot fill that decision.
- Every source capture, forecast, abstention, paper action, simulated fill,
  position mark, and terminal is durable and replayable.
- A successful paper run is not evidence of live profitability.

## 3. Chosen architecture

The implementation is a new paper-only composition rather than a modification
of the legacy dip bot or an embedding of model code inside the evidence
collector.

```text
score source 1..3             Kalshi read-only WebSocket
        |                              |
        v                              v
durable raw score capture      durable raw L2 capture
        |                              |
        v                              v
paper score coordinator        immutable full-book reducer
        |                              |
        +----------> durable causal bridge <----------+
                             |
                             v
                 live Models 1 and 2 state
                             |
                             v
             forecast + settlement-value policy
                             |
                             v
                 paper-only delayed simulator
                             |
                             v
               append-only paper evidence log
```

One CLI owns the operator experience, but the capture, bridge, model, and
paper-simulation components communicate only through immutable values and
durable receipts. A model or paper-policy failure cannot relabel prior shadow
evidence or grant the collector additional authority.

## 4. Live score sources and trust

### 4.1 Source interface

Each source adapter returns a raw capture with:

- provider slot and source ID;
- independent-lineage ID and lineage digest;
- exact provider match and player IDs from a frozen match manifest;
- capture wall/monotonic clocks and uncertainty;
- raw payload bytes and digest; and
- normalized complete score coordinates when parseable.

Initial adapters reuse the existing Sportradar trial parser and the existing
API-Tennis, GoalServe, and Live Tennis API candidate parsers. Network adapters
are credential-gated and configurable; fixture and growing-JSONL adapters are
available for deterministic testing and operator dry runs. Credential values
never appear in configuration fingerprints or evidence rows.

The match manifest freezes canonical match ID, scheduled start, match format,
canonical player orientation, Kalshi market orientation, and every configured
provider's match/player IDs. Name-only runtime matching is not allowed.

### 4.2 Paper trust classes

- `CONSENSUS_PAPER`: at least two fresh, proven-independent lineages agree on
  all score coordinates and server.
- `SINGLE_SOURCE_PAPER`: exactly one fresh complete source is available and no
  fresh source disagrees.
- `ABSTAINED`: sources disagree, the primary is incomplete/stale, orientation
  is unresolved, or a transition cannot be proved.

Single-source input is permitted because the output is paper-only. It never
upgrades a research-qualified record and can never authorize a real order.
If a second fresh source disagrees, the coordinator abstains instead of
falling back to the single source.

## 5. Exact point transition and recovery

The paper bridge has its own immutable `LivePaperPointTransition`. It carries
the trust class, one or more real source-lineage digests, full before/after
tennis states, server, winner, source clocks, a contiguous local point ordinal,
separate consensus and correction epochs, and the durable parent receipts.

The local ordinal is independent of ledger row sequence numbers. Consensus
epoch and provider correction epoch remain distinct fields.

At startup, the first accepted score becomes an anchor. The runner emits an
initial state forecast but does not invent points before the anchor. A model
update occurs only when the next accepted score is exactly one legal tennis
point after the anchor.

For an unchanged score, no model update occurs. For a regression, correction,
or multi-point gap:

1. persist an abstention and quarantine the current anchor;
2. perform no paper action;
3. require a new stable complete observation after the quarantine barrier;
4. adopt that observation as a new anchor;
5. reset Model 2 to its frozen initial belief and increment the paper rebase
   epoch; and
6. label the next forecast `REBASED_PAPER` until a subsequent exact point is
   accepted.

This loses unobserved Bayesian history but never fabricates it. If a provider
supplies a complete, gap-free point tape, the bridge may replay its exact
missing points instead of rebasing.

## 6. Live model interface

The new live interface is state-based and does not mutate the offline
`PilotPointEvent` contract:

```python
open_live_two_model(static_artifact, dynamic_artifact, anchor)
    -> (LiveTwoModelState, LiveTwoModelForecast)

apply_live_paper_transition(state, transition)
    -> (LiveTwoModelState, LiveTwoModelForecast)
```

`LiveTwoModelForecast` contains:

- trust class and rebase state;
- Model 1 and Model 2 next-point probabilities;
- Model 1 and Model 2 current-set probabilities;
- Model 1 and Model 2 match probabilities and bounds;
- Model 2 prior/posterior belief digests;
- artifact, source, transition, and resulting-state digests; and
- a supported/abstained status with a fixed reason.

An initial forecast is emitted from the anchor. Thereafter the static model is
evaluated on the accepted after-state and the Bayesian model updates the
server's latent-state posterior from the observed point before forecasting the
next point.

The runner persists a checkpoint after every accepted transition. Restart
loads and authenticates that checkpoint or deterministically replays the
paper log from the anchor. A correction rebase is explicit rather than being
misrepresented as a contiguous Bayesian update.

## 7. Prematch artifacts

The preferred mode requires exact target-bound `ServeStrengthArtifact` and
`DynamicPointArtifact` files. They must match the selected canonical match and
scheduled start.

For a same-day pilot without trained target artifacts, an explicitly weaker
bootstrap mode is available:

- the operator supplies home and away serve-point priors;
- the bridge freezes those priors into a target-bound static artifact;
- a versioned paper-bootstrap dynamic parameter template is retargeted and
  digest-bound to the match; and
- every forecast and trade is labeled `OPERATOR_BOOTSTRAP / NO_EDGE_CLAIM`.

Bootstrap mode is allowed to simulate paper trades, but its results cannot be
used for promotion or profitability claims. No market price is used to create
the prematch priors, because doing so would make the model circular.

The Version 1 bootstrap dynamic template is frozen rather than tuned during a
live session:

```text
transition matrix = ((0.80, 0.15, 0.05),
                     (0.10, 0.80, 0.10),
                     (0.05, 0.15, 0.80))
initial weights   = (0.20, 0.60, 0.20) for each player
logit offsets     = (-0.50, 0.00, 0.50)
```

These are the existing synthetic-plumbing parameters, not fitted edge. Their
use is therefore always visible in the authority label and artifact digest.

## 8. Kalshi full-L2 bridge

The bridge rebuilds immutable full books from raw frames with the existing
Kalshi reducer. It retains market IDs, player orientation, YES bid/ask ladders,
physical connection generation, subscription ID, global sequence, capture
clocks, and raw-parent digest.

A model forecast can be evaluated only against a fresh, gap-free book whose
match binding and player orientation equal the frozen manifest. Disconnects,
generation changes, incomplete snapshots, sequence gaps, stale books, and
one-sided executable depth create typed abstentions.

## 9. Paper entry policy

Version 1 begins evaluating entries only after one set is complete and the
match remains live. It supports one open position per canonical match and may
open another only after the prior position is flat on a later observation.

For each player side, conservative fair probability is:

```text
home fair = min(Model 1 lower home bound, Model 2 lower home bound)
away fair = min(1 - Model 1 upper home bound,
                1 - Model 2 upper home bound)
```

Both models must support the same side. Sizing walks the executable ask ladder
and chooses the largest quantity whose simulated debit plus entry fees is at
most $50 and whose visible depth is sufficient.

The entry condition is:

```text
quantity * (conservative fair probability - executable ask VWAP)
    - conservative fees >= $5
```

This value is persisted as `SETTLEMENT_VALUE_PROXY`. It is not described as a
five-minute expected profit. A source-trust label, model disagreement, book
age, depth, fees, stake, expected edge, and every rejected gate are recorded.

Version 1 freezes decision-to-arrival latency at one second, matching the
legacy paper simulator. A source observation or L2 book older than five
seconds is stale for a new entry. These values are part of the session
fingerprint and cannot change during a session.

## 10. Paper execution and exits

An accepted BUY creates a durable pending paper action. It can fill only from
the first later gap-free L2 frame at or after the configured decision-to-
arrival latency. Price and quantity are recomputed from that later frame; a
worse book can reduce the fill or cause no fill.

The simulator maintains a virtual-liquidity ledger so two simulated actions
cannot consume the same displayed depth. Partial fills are allowed and are
recorded exactly.

Open positions are marked from executable bid ladders after fees. A SELL is
requested when executable net P&L reaches `+$5`, reaches `-$5`, or the actual
fill has been held for 300 seconds. A sell also fills only against later
available depth. Insufficient depth may leave residual paper inventory; the
runtime never fabricates a flattening fill.

The process refuses new entries when exposure cannot be priced, a prior action
is unresolved, evidence persistence fails, the match is terminal/suspended,
or the session risk state is inconsistent.

## 11. Persistence, dashboard, and replay

The paper evidence log is append-only and hash-chained. Raw parents are durable
before derived rows. Checkpoints, pending actions, fills, position changes,
and terminals are committed with fsync ordering sufficient for restart
recovery.

The dashboard banner is:

```text
LIVE MODELS 1+2 / PAPER ONLY / NO REAL ORDERS
```

It displays elapsed time, source health and trust, score/server, Model 1 and
Model 2 set/match probabilities, executable books, current decision, paper
position/P&L, and cumulative fixed-code rejection counters. It emits at least
one heartbeat every 60 seconds during quiet periods.

Replay consumes only the durable paper log and must reproduce every forecast,
decision, fill, position, P&L mark, abstention, and terminal byte-for-byte.

## 12. CLI

The new entry point is separate from the legacy bot:

```text
python -m inci_tennis_runtime.live_two_model_paper_cli \
  --choose \
  --match-manifest /absolute/path/live-paper-match.json \
  --static-artifact /absolute/path/static.json \
  --dynamic-artifact /absolute/path/dynamic.json \
  --duration-seconds 3600
```

Bootstrap mode replaces the two artifact arguments with an explicit
paper-bootstrap configuration. Configuration and evidence paths must be
absolute, regular, non-symlink files with restrictive permissions where they
contain credentials or private metadata.

Startup prints configured sources, trust eligibility, artifact identity,
market binding, stake/edge/exit settings, state root, and `NO REAL ORDERS`
before opening a score or Kalshi transport.

## 13. Error handling

- Source transport/parser failure isolates that source. Remaining complete
  sources may continue under the resulting trust class.
- Fresh source disagreement quarantines score updates and paper entries.
- Score gap/correction follows the explicit rebase procedure.
- Kalshi raw/evidence integrity, binding, sequence, or persistence failure
  halts the paper runner.
- Model/artifact/authentication failure halts before paper action.
- Dashboard failure cannot erase evidence; evidence failure cannot be reduced
  to a dashboard warning.
- Cancellation and OS termination signals finish pending durable writes and
  append one terminal before returning.

## 14. Testing

Development is test-first. Required coverage includes:

- single-source paper acceptance and labels;
- two-source agreement and disagreement quarantine;
- exact one-point transitions, unchanged scores, gaps, corrections, point-tape
  recovery, and rebase behavior;
- independent local point ordinals and separate consensus/correction epochs;
- initial and after-point Model 1/2 forecasts;
- target/artifact mismatch and bootstrap labeling;
- exact player/market orientation and full-L2 sequence handling;
- conservative fair-value and $50/$5 sizing math including fees/depth;
- decision-frame non-fill, delayed fill, worse-book no-fill, partial fill,
  virtual-liquidity isolation, and +$5/-$5/300-second exits;
- crash/restart recovery and byte-identical replay;
- heartbeat and rejection diagnostics;
- static dependency tests proving the package has no real-order capability;
- all existing two-model, collector, expert, and legacy regressions.

An end-to-end scripted integration must start at a first-set anchor, accept
later point transitions, emit both model forecasts, create a qualifying paper
BUY, fill it only from a later book, and close or retain it according to the
paper exit rules.

## 15. Acceptance criteria

The feature is complete when:

1. one configured live score source can drive clearly labeled paper forecasts;
2. a second agreeing source upgrades the trust label without changing the
   score result;
3. every exact accepted point produces Model 1 and Model 2 output;
4. gaps and disagreement abstain rather than fabricate points;
5. eligible second/third-set opportunities can create causal paper fills under
   the $50/$5 policy;
6. the dashboard explains every no-action interval;
7. restart and replay reproduce the durable session; and
8. dependency and runtime tests prove that no real Kalshi order can be sent.

## 16. Deferred work

- Training and validating a five-minute markout model and Model 3.
- Automated news/injury collection and historical data acquisition.
- Profitability promotion based on unseen matches.
- Any demo or real-order process.
