# Live Models 1 and 2 Paper Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task by
> task. Every behavior change follows RED -> GREEN -> REFACTOR and every task
> ends in its own commit.

**Goal:** Run the existing static and Bayesian tennis models after each exact
live score transition and simulate fee-aware paper trades against later Kalshi
full-L2 frames, with no path to a real order.

**Architecture:** A paper-only score coordinator turns fresh normalized source
observations into anchors, exact point transitions, or typed abstentions. A
state-based two-model runner consumes only accepted anchors/transitions. A
separate pure policy and delayed L2 simulator produce paper actions and fills.
An append-only canonical JSONL session log makes the complete run replayable.
The first executable CLI reads growing JSONL score captures and Kalshi raw
WebSocket frames; network collection remains in the existing read-only
collector process and is not duplicated in the model process.

**Tech stack:** CPython 3.14.5, standard library only, immutable dataclasses,
`Decimal`, `unittest`, existing exact tennis recursion, existing candidate
score parsers, existing Kalshi V2 full-L2 reducer, canonical JSONL, SHA-256.

## Global constraints

- The startup banner is exactly
  `LIVE MODELS 1+2 / PAPER ONLY / NO REAL ORDERS`.
- Do not modify the legacy real/demo guards.
- New live-paper modules must not import `KalshiClient`, the legacy `Executor`,
  order creation/cancellation routes, or any write-capable transport.
- Preserve the offline `PilotPointEvent` two-lineage authority; single-source
  paper data uses new contracts and never becomes research/live authority.
- Score authority is `CONSENSUS_PAPER` for two or more fresh proven-independent
  agreeing lineages, `SINGLE_SOURCE_PAPER` for one fresh complete source and no
  fresh disagreement, otherwise `ABSTAINED`.
- Never infer a missing point. A duplicate causes no update; a score gap,
  regression, correction, stale input, or disagreement causes an abstention.
- Rebase only after a stable post-quarantine score; reset Model 2 to its frozen
  initial belief and label the forecast `REBASED_PAPER`.
- Keep consensus epoch, correction epoch, rebase epoch, durable record sequence,
  and local contiguous point ordinal as separate values.
- Bootstrap artifacts are always labeled
  `OPERATOR_BOOTSTRAP / NO_EDGE_CLAIM` and use the frozen parameters from the
  approved design. Market prices cannot be used as bootstrap priors.
- Paper entry begins only after one completed set. Both models must support the
  same selected player side. The conservative fair value is the minimum lower
  match bound for HOME, or the minimum complemented upper bound for AWAY.
- Maximum simulated entry debit plus entry fee is `$50.00`; minimum modeled
  settlement-value edge after conservative fees is `$5.00`.
- Decision-to-arrival latency is exactly one second. Score and L2 freshness
  limits are five seconds. A decision frame can never fill its own action.
- Exit at executable net P&L of at least `+$5.00`, at most `-$5.00`, or after
  300 seconds. A missing exit bid leaves a visible residual position; no
  fabricated flatten is allowed.
- Every input, forecast, rejection, action, fill, mark, checkpoint, heartbeat,
  and terminal is append-only, digest chained, and replayable.
- Version 1's policy claim is `SETTLEMENT_VALUE_PROXY`, not a five-minute price
  forecast and not evidence of profitability.

## File structure

```text
inci_tennis_expert/
├── live_paper_contracts.py       # immutable paper-only score/book/action values
├── live_paper_score.py           # multi-source trust, exact transition, rebase
├── live_two_model.py             # state-based Model 1/2 runner and bootstrap
├── live_paper_execution.py       # pure entry policy and delayed L2 simulator
└── live_paper_session.py         # reducer, canonical records, checkpoint/replay

inci_tennis_runtime/
└── live_two_model_paper_cli.py   # growing-JSONL composition; no order transport

tests/tennis_v1/
├── test_live_paper_score.py
├── test_live_two_model.py
├── test_live_paper_execution.py
├── test_live_paper_session.py
└── test_live_two_model_paper_cli.py
```

Import concrete modules directly; keep package `__init__.py` files unchanged.

---

### Task 1: Add paper-only score contracts and coordinator

**Files:**

- Create: `inci_tennis_expert/live_paper_contracts.py`
- Create: `inci_tennis_expert/live_paper_score.py`
- Create: `tests/tennis_v1/test_live_paper_score.py`

**Public interfaces:**

```python
class PaperScoreTrust(str, Enum):
    CONSENSUS_PAPER = "CONSENSUS_PAPER"
    SINGLE_SOURCE_PAPER = "SINGLE_SOURCE_PAPER"
    ABSTAINED = "ABSTAINED"


class LivePaperScoreDecisionKind(str, Enum):
    ANCHORED = "anchored"
    UNCHANGED = "unchanged"
    POINT_ACCEPTED = "point_accepted"
    QUARANTINED = "quarantined"
    REBASED = "rebased"
    ABSTAINED = "abstained"


@dataclass(frozen=True, slots=True)
class LivePaperSourceObservation:
    provider_slot: str
    source_id: str
    lineage_sha256: str
    independence_proven: bool | None
    state: TennisState
    raw_receipt_sha256: str
    captured_wall_ns: int
    captured_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class LivePaperScoreAnchor:
    canonical_match_id: str
    state: TennisState
    trust: PaperScoreTrust
    supporting_lineage_sha256s: tuple[str, ...]
    parent_receipt_sha256s: tuple[str, ...]
    consensus_epoch: int
    correction_epoch: int
    rebase_epoch: int
    accepted_wall_ns: int
    accepted_monotonic_ns: int
    anchor_sha256: str


@dataclass(frozen=True, slots=True)
class LivePaperPointTransition:
    canonical_match_id: str
    local_point_ordinal: int
    before_state: TennisState
    after_state: TennisState
    server: PlayerSide
    winner: PlayerSide
    trust: PaperScoreTrust
    supporting_lineage_sha256s: tuple[str, ...]
    parent_receipt_sha256s: tuple[str, ...]
    consensus_epoch: int
    correction_epoch: int
    rebase_epoch: int
    accepted_wall_ns: int
    accepted_monotonic_ns: int
    transition_sha256: str


def reduce_live_paper_scores(
    state: LivePaperScoreCoordinatorState,
    observations: tuple[LivePaperSourceObservation, ...],
    *,
    now_wall_ns: int,
    now_monotonic_ns: int,
) -> tuple[LivePaperScoreCoordinatorState, LivePaperScoreDecision]: ...


def observation_from_live_score_facts(
    *,
    canonical_match_id: str,
    context: LiveScoreCaptureContext,
    normalized: NormalizedLiveScore,
    local_revision: int,
) -> LivePaperSourceObservation: ...
```

- [ ] Write failing tests for one-source anchoring, two independent agreeing
  sources upgrading to consensus, two sources sharing a lineage remaining
  single-source, fresh disagreement abstaining, and stale/missing source
  abstention.
- [ ] Run `python -m unittest tests.tennis_v1.test_live_paper_score -v` and
  preserve the expected RED output in the task report.
- [ ] Implement strict frozen contracts, canonical projections/digests, and a
  score-coordinate comparison that ignores provider transport identity but
  does not ignore server, lifecycle, or correction epoch.
- [ ] Implement the explicit paper-only projection from manifest-bound
  `LiveScoreFacts` to a source `TennisState`. The locally assigned revision is
  transport bookkeeping, not provider correction authority; keep that fact in
  the contract/authority label and reject parser results without complete
  score coordinates or server.
- [ ] Write failing tests for unchanged score (no update), exact point
  successor (ordinal increments once and winner/server are exact), duplicate
  capture (no update), multi-point gap/correction (quarantine), and stable
  re-anchor (rebase epoch increments; ordinal does not invent missing points).
- [ ] Implement exact successor resolution by testing both legal point winners
  through the existing tennis scorer. Do not accept a transition unless one
  and only one winner produces the observed score.
- [ ] Run the focused tests and the existing score/pilot contract tests; commit
  as `feat: add live paper score coordinator`.

---

### Task 2: Add state-based live Models 1 and 2 plus bootstrap artifacts

**Files:**

- Create: `inci_tennis_expert/live_two_model.py`
- Modify: `inci_tennis_expert/pilot_static_model.py`
- Modify: `inci_tennis_expert/pilot_dynamic_model.py`
- Create: `tests/tennis_v1/test_live_two_model.py`
- Modify: `tests/tennis_v1/test_pilot_static_model.py`
- Modify: `tests/tennis_v1/test_pilot_dynamic_model.py`

**Public interfaces:**

```python
def build_operator_bootstrap_artifacts(
    *,
    canonical_match_id: str,
    scheduled_start_wall_ns: int,
    cutoff_wall_ns: int,
    home_serve_point_probability: Decimal,
    away_serve_point_probability: Decimal,
) -> tuple[ServeStrengthArtifact, DynamicPointArtifact]: ...


def open_live_two_model(
    *,
    static_artifact: ServeStrengthArtifact,
    dynamic_artifact: DynamicPointArtifact,
    anchor: LivePaperScoreAnchor,
    artifact_authority: LiveArtifactAuthority,
) -> tuple[LiveTwoModelState, LiveTwoModelForecast]: ...


def apply_live_paper_transition(
    state: LiveTwoModelState,
    transition: LivePaperPointTransition,
) -> tuple[LiveTwoModelState, LiveTwoModelForecast]: ...


def rebase_live_two_model(
    state: LiveTwoModelState,
    anchor: LivePaperScoreAnchor,
) -> tuple[LiveTwoModelState, LiveTwoModelForecast]: ...
```

- [ ] Write RED tests proving both models can forecast directly from an anchor
  state before a new point and that existing completed-event APIs retain their
  exact outputs after the state-based refactor.
- [ ] Extract small state-based evaluation helpers in the static and dynamic
  modules; make the existing event APIs delegate to them.
- [ ] Write RED tests for the frozen bootstrap matrix
  `((.8,.15,.05),(.1,.8,.1),(.05,.15,.8))`, initial weights
  `(.2,.6,.2)`, logit offsets `(-.5,0,.5)`, target/cutoff binding, and the
  visible `OPERATOR_BOOTSTRAP / NO_EDGE_CLAIM` authority.
- [ ] Implement deterministic bootstrap artifact creation using existing
  digest constructors; reject non-finite/out-of-range priors and cutoff at or
  after start.
- [ ] Write RED tests that an exact point updates only the server's Bayesian
  posterior, outputs next-point/set/match values from both models, maintains a
  contiguous local ordinal, rejects discontinuity, and keeps consensus,
  correction, and rebase epochs separate.
- [ ] Implement immutable live state/forecast values. On `rebase`, initialize a
  fresh `DynamicPointModel`, retain artifact identity, and label the initial
  forecast `REBASED_PAPER`.
- [ ] Run live-model plus all existing pilot model tests; commit as
  `feat: run two models from live paper scores`.

---

### Task 3: Add full-L2 paper policy and delayed simulator

**Files:**

- Create: `inci_tennis_expert/live_paper_execution.py`
- Create: `tests/tennis_v1/test_live_paper_execution.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Public interfaces:**

```python
def project_paper_l2(
    l2: UnqualifiedTwoTickerL2State,
    *,
    captured_wall_ns: int,
    captured_monotonic_ns: int,
    clock_uncertainty_ns: int,
    home_ticker: str,
    away_ticker: str,
) -> LivePaperL2Frame: ...


def evaluate_live_paper_entry(
    forecast: LiveTwoModelForecast,
    book: LivePaperL2Frame,
    state: PaperPortfolioState,
    *,
    decision_wall_ns: int,
    decision_monotonic_ns: int,
) -> PaperDecision: ...


def reduce_paper_book(
    state: PaperPortfolioState,
    book: LivePaperL2Frame,
    *,
    observed_wall_ns: int,
    observed_monotonic_ns: int,
) -> tuple[PaperPortfolioState, tuple[PaperEvent, ...]]: ...
```

- [ ] Write RED tests for converting Kalshi YES/NO bid ladders to executable
  YES bid/ask ladders without flattening depth, rejecting incomplete/crossed/
  gapped/stale/mismatched frames, and preserving generation/SID/sequence/raw
  parent identity.
- [ ] Implement the immutable book projection and virtual depth ledger.
- [ ] Write RED tests for: no entry before a set completes; both models must
  support the same side; conservative HOME/AWAY fair values; largest visible
  quantity under `$50` debit plus entry fee; minimum `$5` edge after entry and
  conservative exit fees; one open/pending position per match; and typed
  reasons for every rejection.
- [ ] Implement the pure `SETTLEMENT_VALUE_PROXY` entry policy with exact
  `Decimal` arithmetic and the existing versioned fee schedule.
- [ ] Write RED tests proving an action is due after exactly one second, the
  decision book cannot fill it, a later book can produce zero/partial/full
  fills, consumed virtual depth cannot be reused, and no stale frame can fill.
- [ ] Write RED tests for fee-aware executable bid marks and exits at `+$5`,
  `-$5`, or `300s`; a missing/insufficient bid must leave residual inventory.
- [ ] Implement the pure simulator with no client/transport field and no order
  vocabulary beyond explicitly named `PaperAction`/`PaperFill` values.
- [ ] Add an AST dependency-boundary test forbidding order-capable imports and
  strings/routes in all `live_paper_*` and `live_two_model` modules.
- [ ] Run focused execution, fee, L2 reducer, and dependency-boundary tests;
  commit as `feat: simulate bounded paper trades on full l2`.

---

### Task 4: Add durable session reduction, checkpoint, and replay

**Files:**

- Create: `inci_tennis_expert/live_paper_session.py`
- Create: `tests/tennis_v1/test_live_paper_session.py`

**Public interfaces:**

```python
def open_live_paper_session(config: LivePaperSessionConfig) -> LivePaperSessionState: ...


def reduce_live_paper_input(
    state: LivePaperSessionState,
    item: LivePaperInput,
) -> tuple[LivePaperSessionState, tuple[LivePaperRecord, ...]]: ...


def encode_live_paper_records(records: tuple[LivePaperRecord, ...]) -> bytes: ...


def replay_live_paper_records(raw: bytes) -> LivePaperReplayResult: ...
```

- [ ] Write RED tests for a digest-chained canonical JSONL envelope whose
  monotonic record ordinal is independent of the point ordinal.
- [ ] Implement record projections for raw score receipt, raw L2 receipt,
  anchor, transition, forecast, abstention, paper action, fill, mark,
  checkpoint, 60-second heartbeat, and terminal.
- [ ] Write RED tests for deterministic reduction of the same inputs to
  byte-identical output and identical final model/portfolio state.
- [ ] Implement a single pure session reducer that sequences score decisions,
  model updates/rebases, policy evaluation, delayed fills, exits, and marks.
- [ ] Write RED tests for checkpoint authentication, restart from checkpoint,
  fallback replay from the anchor on a missing/corrupt checkpoint, and refusal
  to append after terminal.
- [ ] Implement atomic checkpoint encoding/validation as a separate blob; the
  JSONL record remains the source of truth.
- [ ] Run the focused session tests plus Tasks 1-3 tests; commit as
  `feat: persist and replay live paper sessions`.

---

### Task 5: Add the runnable growing-JSONL CLI and operator documentation

**Files:**

- Create: `inci_tennis_runtime/live_two_model_paper_cli.py`
- Create: `tests/tennis_v1/test_live_two_model_paper_cli.py`
- Modify: `README.md`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**CLI contract:**

```text
python -m inci_tennis_runtime.live_two_model_paper_cli \
  --manifest /absolute/match-manifest.json \
  --score-stream /absolute/growing-score-captures.jsonl \
  --kalshi-stream /absolute/growing-kalshi-frames.jsonl \
  --session-log /absolute/live-paper-session.jsonl \
  --checkpoint /absolute/live-paper-checkpoint.json \
  --bootstrap-home-serve 0.64 \
  --bootstrap-away-serve 0.61
```

- [ ] Write RED parser/startup tests for absolute regular non-symlink inputs,
  distinct output paths, exact manifest player/market/provider bindings,
  artifact mode XOR bootstrap mode, five-second freshness, one-second latency,
  `$50` cap, `$5` thresholds, and 300-second hold being non-overridable.
- [ ] Implement strict manifest and growing-JSONL envelope loaders. Score rows
  carry provider slot/context plus base64 raw payload; Kalshi rows carry the
  physical generation/clocks plus base64 raw WebSocket payload. Reuse
  `parse_live_score` and `UnqualifiedTwoTickerBookReducer` rather than accepting
  caller-supplied normalized probabilities or books.
- [ ] Write RED end-to-end tests with fixture streams proving: banner first;
  initial anchor forecast; first-set gating; point update; one-second delayed
  partial paper fill on a later L2 frame; typed exit/terminal; and byte-identical
  replay with no network or credentials.
- [ ] Implement bounded tailing with a heartbeat every 60 seconds, SIGINT/SIGTERM
  clean terminal, immediate append+fsync of evidence rows, atomic checkpoint
  replacement, and a compact terminal dashboard showing score trust, both model
  probabilities, book age, paper position/P&L, and top rejection reason.
- [ ] Add dependency seals proving the CLI imports neither legacy bot/executor
  nor any order-capable transport. It may consume only already-captured
  growing files in Version 1.
- [ ] Document how the existing read-only collector (or a capture adapter) must
  write the two growing stream formats, how to run the paper bridge, what each
  authority label means, and the explicit limitation that this commit does not
  yet open provider/Kalshi sockets itself.
- [ ] Run focused CLI/session tests, all targeted tennis pilot/collector tests,
  `/Users/mthanki/.venvs/inci/bin/python tests.py`, and a no-network smoke run.
- [ ] Commit as `feat: add live two-model paper cli`.

---

## Final verification

- [ ] Run all five new test modules together with the existing static/dynamic/
  pilot, score, Kalshi V2, fee, collector, and dependency-boundary suites.
- [ ] Run all 206 legacy tests.
- [ ] Inspect `git diff <design-base>..HEAD` for any order-capable import or
  unrelated edit.
- [ ] Run the branch-wide code review gate and resolve all Critical/Important
  findings.
- [ ] Push `feature/two-model-pilot` and report exact commit SHAs, test counts,
  runnable command, and remaining limitation (capture files are external).
