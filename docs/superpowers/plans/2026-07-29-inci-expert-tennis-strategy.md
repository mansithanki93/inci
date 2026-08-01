# Inci Expert Tennis Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a paper-only Tennis v1 system that combines synchronized point-level tennis state with Kalshi full-book state, estimates conservative complete-policy value, enforces canonical match risk, and proves or rejects its edge through sealed forward evidence.

**Architecture:** Preserve the existing immutable Phase-1 package and evidence
WAL byte-for-byte. Build the deterministic expert projection in a separately
sealed `inci_tennis_expert` package, bind its companion journal to parent
Phase-1 evidence records, isolate all read-only network capability in
`inci_tennis_io`, isolate provider parsing in `inci_tennis_adapters`, and
wire them through the capability-free `inci_tennis_runtime` composition root.
An exact binder and synchronizer create trusted decision snapshots;
interpretable probability, policy-value, risk, and full-depth IOC components
remain deterministic and replayable. Production provider activation, demo
execution, and live execution are not granted by this plan.

**Tech Stack:** CPython `3.14.5` exactly until the AST seal is redesigned;
standard library dataclasses/Decimal/asyncio/sqlite3/statistics;
`requests==2.34.2`, `cryptography==49.0.0`, and `websockets==16.1.1`.

## Global Constraints

- Tennis singles match-winner contracts only.
- The official paper scorecard always uses exactly one contract.
- The 5-, 10-, and 20-contract scorecards are isolated counterfactuals and never influence official decisions.
- The v6 dip strategy remains a paired baseline; no v6 trading file is modified without a separately approved containment change.
- No demo or live order endpoint, flag, environment override, or executor is added.
- No secret, API key, provider permission, trial artifact, binding artifact, raw provider payload, WAL, or research log enters Git.
- Production provider registration remains empty until the exact product tier, terms, permission, qualification trace, quota, adapter digest, and activation are separately authorized.
- All automated tests are network-free. Manual provider qualification and shadow capture require an explicit operator command.
- Raw events are durably persisted before expert reduction or policy observation.
- Unknown schema, sequence gaps, stale required state, ambiguous binding, or unpriceable occupied exposure fail closed.
- Foundation-only canonical state bytes, trace behavior, replay behavior, and protected legacy hashes remain unchanged.
- The 21 sealed Phase-1 production modules remain byte/AST-equivalent.
  Deterministic expert code lives in `inci_tennis_expert`, network capability
  lives in `inci_tennis_io`, and provider parsing lives in
  `inci_tennis_adapters`. Dependencies are one-way:
  `inci_tennis_io -> Phase-1 capture`, `inci_tennis_adapters -> expert
  contracts`, and `inci_tennis_expert -> immutable Phase-1 public evidence
  types`. Only `inci_tennis_runtime` may import both exact I/O ports and
  deterministic expert facades; it implements neither transport nor strategy.
  Phase 1 imports none of the four packages.
- A clean, operator-selected base commit/tag whose Tennis and v6 tests pass is
  preferred. When the approved source is not committed and Git is
  user-managed, execution may use the existing isolated worktree only after
  recording a content-addressed foundation snapshot, exact runtime/dependency
  digests, baseline test evidence, and the deviation in the SDD ledger.
- One official session uses one qualified provider/source lineage. Provider
  REST endpoints share one provider worker/epoch. Kalshi has exactly one
  active physical WebSocket at a time but may advance through multiple
  strictly increasing local book trust epochs, including in-band forced
  resnapshots on that same socket; it has no simultaneous REST order-book
  fallback.
- Provider fixtures and qualification observations must pass through the real
  Phase-1 capture factory. JSON floats, unsafe URLs, duplicate keys, secrets,
  or any other capture-contract failure deny activation; capability flags
  alone never authorize a provider.
- The expert runtime acquires a Kalshi account/environment/subaccount process
  lock before retention recovery, entitlement authorization, WAL/journal
  creation, or transport startup. It is mutually exclusive with v6.
- Historical model data is a separately entitled, lineage-sealed offline
  artifact. Multiple simultaneous live providers are deferred.
- The existing Phase-1 `SessionManifest`, state, reducer, terminal, and replay
  schemas remain unchanged and `research_evaluable` remains false. Expert
  evaluability exists only in a separately sealed expert evaluation manifest.
- Every feature, threshold, model artifact, policy artifact, latency scenario, and metric is frozen before an official forward scorecard.
- This workspace uses user-managed Git. Do not stage, commit, or push unless the user explicitly requests it; report changed files and verification after each task.

## External Activation Gate

Core implementation and synthetic end-to-end tests can finish without provider credentials. Real shadow capture cannot start until all existing entitlement and qualification gates pass for a specific provider tier.

The first candidate adapter targets the documented Sportradar Tennis v3 REST timeline contract, but remains unregistered. Its parser must prove stable IDs, point state, server, timestamps, correction/resnapshot behavior, and a trustworthy provider sequence or revision during qualification. If any mandatory capability is absent, activation remains denied; local polling order or locally generated counters cannot impersonate provider sequence.

Sportradar trial limits must be read from the operator's account and bound into the existing quota contract. The system reports the actually covered observed pool; it never claims ten simultaneously covered matches when the trial quota supports fewer.

## File Map

### Phase 0: immutable boundary and evidence bridge

- `inci_tennis_expert/`: deterministic expert contracts, reducers, models,
  policy, paper execution, scorecards, and companion replay.
- `inci_tennis_io/`: account lock and exact read-only provider/Kalshi
  transports; the only package allowed to import network libraries.
- `inci_tennis_adapters/`: strict provider payload normalization; no network,
  policy, risk, execution, or account state.
- `inci_tennis_runtime/`: capability-free startup/shutdown and dependency
  injection; no transport, parsing, model, policy, risk, or execution logic.
- `tests/tennis_v1/test_expert_dependency_boundary.py`: four-package seals,
  one-way imports, and read-only capability checks.

### Phase A: synchronized read-only state

- `inci_tennis_expert/contracts.py`: immutable domain, book, binding, synchronization, model, policy, and reason-code contracts.
- `inci_tennis_expert/tennis_score.py`: pure provider snapshot/point/correction reducer and tennis transition validation.
- `inci_tennis_expert/market_book.py`: pure full-book snapshot/delta reducer and executable depth calculations.
- `inci_tennis_expert/match_binding.py`: pinned external binding manifest and exact player/contract orientation.
- `inci_tennis_expert/synchronizer.py`: freshness, sequence, causal-explanation, lifecycle, and trusted-snapshot barrier.
- `inci_tennis_expert/state.py`: canonical aggregate expert projection state.
- `inci_tennis_expert/journal_codec.py`: pure companion-record validation,
  canonical encoding, and hash chaining.
- `inci_tennis_expert/reducer.py`: expert-event dispatch and deterministic derived records.
- `inci_tennis_expert/replay.py`: two-journal exact replay and parent-evidence comparison.
- `inci_tennis_adapters/sportradar_tennis_v3.py`: unregistered candidate payload parser.
- `inci_tennis_io/provider_readonly.py`: generic entitlement-bound read-only provider transport.
- `inci_tennis_io/kalshi_readonly.py`: authenticated read-only WebSocket transport and frame parser.
- `inci_tennis_io/account_lock.py`: shared read-only Kalshi session/rate-budget process lock.
- `inci_tennis_io/expert_journal_store.py`: append/fsync/read/recover/purge
  capability for the companion journal.
- `inci_tennis_io/pinned_artifacts.py`: restricted external artifact reads
  returning immutable bytes and exact digests.
- `inci_tennis_runtime/replay_service.py`: authority-bound Phase-1 plus expert
  replay composition.
- `inci_tennis_runtime/config.py`: external research configuration with
  environment/subaccount identity and artifact/package digests.
- `inci_tennis_runtime/shadow_runtime.py`: entitlement-bound dual-feed orchestration with no decision authority.
- `inci_tennis_runtime/shadow_cli.py`: `--check` and `--shadow` entry point only.

### Phase B: shadow intelligence

- `inci_tennis_io/historical_store.py`: separately entitled point-in-time
  SQLite feature store with lineage.
- `inci_tennis_expert/prematch_model.py`: surface/opponent/recency-shrunk serve and return prior.
- `inci_tennis_expert/win_probability.py`: exact tennis-state match-win recursion.
- `inci_tennis_expert/calibration.py`: chronological calibration, uncertainty, support, and abstention.

### Phase C: expert paper policy

- `inci_tennis_expert/fee_schedule.py`: versioned exact fee contracts.
- `inci_tennis_expert/policy_value.py`: conservative complete-policy expected net P&L.
- `inci_tennis_expert/baselines.py`: no-trade, frozen v6, and simple market-plus-score paired policies.
- `inci_tennis_expert/risk.py`: canonical match reservations, cooldown, attempts, and portfolio halt state.
- `inci_tennis_expert/paper_ioc.py`: latency-due bounded-limit IOC simulation.
- `inci_tennis_expert/virtual_liquidity.py`: non-reusable depth ledgers per scorecard.
- `inci_tennis_expert/scorecards.py`: isolated official and capacity portfolios.
- `inci_tennis_expert/ranking.py`: eligible-pool ranking with hysteresis.
- `inci_tennis_expert/dashboard.py`: overwrite-latest terminal view.
- `inci_tennis_runtime/paper_runtime.py`: one owner composition of trusted
  snapshots, models, ranking, risk, official/capacity scorecards, and WAL.

### Phase D: sealed evaluation

- `inci_tennis_expert/sealed_scorecard.py`: immutable scorecard manifest and run gate.
- `inci_tennis_expert/evaluation.py`: paired P&L, calibration, concentration, drawdown, and confidence bounds.
- `inci_tennis_expert/evaluation_artifact.py`: pure post-terminal evaluation
  evidence contract and canonical digest.
- `inci_tennis_io/evaluation_store.py`: permission-bound atomic persistence
  for the separate evaluation artifact.
- `tools/run_expert_evaluation.py`: offline sealed evaluation command.
- `tests/tennis_v1/`: one focused test module for each production module plus end-to-end and dependency-boundary tests.

---

### Task 0: Establish the immutable foundation and four-package boundary

**Files:**
- Create: `inci_tennis_expert/__init__.py`
- Create: `inci_tennis_io/__init__.py`
- Create: `inci_tennis_adapters/__init__.py`
- Create: `inci_tennis_runtime/__init__.py`
- Create: `tests/tennis_v1/test_expert_dependency_boundary.py`
- Create: `docs/superpowers/specs/2026-07-29-inci-expert-foundation.sha256`
- Modify: `pyproject.toml`

**Boundary contract:**

- Phase-1's 21 production modules and legacy v6 files are inputs, never edit
  targets.
- `inci_tennis_expert` is deterministic and cannot import network, credential,
  filesystem, subprocess, wall-clock, random, or legacy execution modules.
  Its only Phase-1 bindings are the immutable public evidence types
  `CapturedInput`, `PersistedEvent`, and `ReplayResult`.
- `inci_tennis_io` may import the exact network dependencies, Phase-1 capture
  factory, and immutable expert wire contracts. It exposes only read-only GET,
  WebSocket subscribe/update-subscription/unsubscribe, receive, and close
  capabilities remotely; its only local writes are account locking,
  retention-authorized companion-journal append/fsync/recovery/purge, and
  separately entitled historical-artifact storage.
- `inci_tennis_adapters` parses persisted capture records into immutable expert
  events. It cannot import network, account, policy, risk, execution, or
  scorecard modules.
- `inci_tennis_runtime` may import exact I/O Protocol/facade objects and exact
  expert engine facades. Its only Phase-1 composition bindings are
  `EventRuntime`, `replay_exact`, and the three immutable evidence types above.
  It owns sequencing and dependency injection but implements no network,
  parsing, model, policy, risk, simulation, or account logic.
- Phase 1 imports none of the new packages. Root v6 imports none of them.

- [ ] **Step 1: Establish the reproducible base**

Record an operator-selected source commit/tag when one exists, the exact
working-tree content digest, CPython `3.14.5`, dependency lock digest, all
Phase-1 tests, all v6 tests, the existing legacy hash result, and compilation
result. Because Git is user-managed, do not stage or commit. If the approved
source tree is not representable by a clean Git commit, use an isolated
content-addressed workspace copy and record that deviation in the SDD ledger;
never rewrite or clean the user's source tree.

- [ ] **Step 2: Write failing package-boundary tests**

Test the exact package inventories, one-way imports, forbidden capabilities,
and that adding an order verb/path, concrete socket call outside
`inci_tennis_io`, policy import in an adapter, or expert import in Phase 1
fails the seal. Prove `MatchStatus.LIVE` is accepted as inert vocabulary while
`is_live`, `live_mode`, execution flags, and `--live` remain rejected.

- [ ] **Step 3: Add minimal package roots and independent seals**

Create empty package roots and pin CPython `3.14.5`. Preserve the original
Phase-1 boundary test unchanged; the new test owns the expert/I/O/adapter
and runtime inventories and digests. Resealing occurs only after independent
review.

- [ ] **Step 4: Prove mutual non-authority**

Static tests assert no new package exposes `create_order`, `cancel_order`,
`amend_order`, demo/live flags, portfolio mutation routes, or imports from
root `executor.py`. The inert domain enum member `MatchStatus.LIVE` is
explicitly allowed. Contextual authority checks still reject `is_live`,
`live_mode`, demo/live execution flags, the `--live` route, and every order
or portfolio mutation capability.

- [ ] **Step 5: Run the foundation matrix**

```bash
python -m unittest tests.tennis_v1.test_expert_dependency_boundary -v
python -m unittest discover -s tests/tennis_v1 -p 'test_*.py' -t . -v
python tests.py
python -m compileall -q tennis_v1 inci_tennis_expert inci_tennis_io \
  inci_tennis_adapters inci_tennis_runtime tests/tennis_v1
git diff --check
```

Expected: all existing behavior passes, Phase-1 hashes remain unchanged, and
no network call occurs.

---

### Task 1: Freeze the interfaces and reason vocabulary

**Binding controller ruling:** Implement
`.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-1-controller-rulings.md`
exactly. That ruling incorporates the Task-2 tennis contracts and replaces
every conflicting or abbreviated contract shape, field name, timestamp
domain, invariant, vocabulary, serializer rule, digest formula, and test
example in this task.

**Files:**
- Create: `inci_tennis_expert/contracts.py`
- Create: `tests/tennis_v1/test_expert_contracts.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**
- Produces every enum, immutable dataclass, exception boundary, field order,
  vocabulary, and invariant listed in the binding Task-1 ruling.
- Produces pure `canonical_expert_bytes(value: object) -> bytes` and
  `expert_contract_sha256(value: object) -> str` using the ruling's exact
  registry, projection, Decimal normalization, and domains.
- Raw provider contracts may carry `MatchFormat.UNSUPPORTED`; binding,
  trusted snapshots, valuation, and policy cannot.
- Provider match identity and canonical match identity remain separate.
- All timestamps explicitly identify wall or monotonic domains.

- [ ] **Step 1: Write exact-type and canonicalization failures**

Implement the complete mandatory Task-1 test matrix from the ruling,
including exact scalar/nested types, optional order authority, identity
separation, timestamp domains, bounded canonical integers, preflighted huge
Decimal exponents, canonical known vectors, installed block-reason coverage,
book executability, and sealed-registry completeness.

- [ ] **Step 2: Run the focused test and observe the missing module**

Run: `python -m unittest tests.tennis_v1.test_expert_contracts -v`
Expected: FAIL with `ModuleNotFoundError: inci_tennis_expert.contracts`.

- [ ] **Step 3: Implement immutable exact contracts**

Implement the binding Task-1 ruling exactly. Do not infer a missing field,
enum value, validation rule, timestamp conversion, or digest formula from an
inline example.

- [ ] **Step 4: Add round-trip canonical tests for every contract**

Assert equal values produce identical bytes, field-order changes in source
dictionaries cannot change canonical bytes, and one field change changes the
SHA-256 digest.

- [ ] **Step 5: Complete review, reseal, and run focused boundary tests**

First run the contract tests and obtain independent review of the exact
production/test diff. After review is clean, the controller updates only the
approved module inventory and canonical AST digest in the expert boundary
test. Any production or focused-test change after review invalidates that
review and restarts the review/reseal step.

Run: `python -m unittest tests.tennis_v1.test_expert_contracts tests.tennis_v1.test_expert_dependency_boundary -v`
Expected: PASS.

---

### Task 2: Build the legal tennis-state reducer

**Binding controller ruling:** Implement
`.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-2-controller-rulings.md`
exactly, together with the structural contracts in the Task-1 controller
ruling. Those rulings replace every conflicting or abbreviated API, lifecycle,
revision, correction, score, reason-precedence, canonical-hash, and test
example in this task.

**Files:**
- Create: `inci_tennis_expert/tennis_score.py`
- Create: `tests/tennis_v1/test_tennis_score.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**
- Consumes exact `ProviderSnapshot`, `ProviderPoint`, `ProviderLifecycle`,
  and `TennisState`.
- Produces exact typed results:

```python
def state_from_snapshot(snapshot: ProviderSnapshot) -> TennisState: ...
def apply_point(
    state: TennisState, point: ProviderPoint
) -> TennisTransitionResult: ...
def apply_lifecycle(
    state: TennisState, event: ProviderLifecycle
) -> TennisTransitionResult: ...
def apply_correction(
    state: TennisState, replacement: ProviderSnapshot
) -> TennisTransitionResult: ...
```

- Bootstrap provider invalidity raises `TennisTransitionError` with the exact
  ruled reason. Incremental provider invalidity returns a blocked typed
  result. A corrupt current state raises `TennisStateInvariantError` and
  globally halts.

- [ ] **Step 1: Write the complete deterministic reducer matrix**

Implement every mandatory Task-2 test from the binding ruling, including
exact reachability, lifecycle reason precedence, revision/correction
barriers, malformed terminal/natural-confirmation payloads, retransmission
semantics, known digests, and both supported formats.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.tennis_v1.test_tennis_score -v`
Expected: FAIL because the reducer is absent.

- [ ] **Step 3: Implement deterministic tennis transitions**

Implement the exact Task-2 API, validation order, total point algorithm,
set/tiebreak service algorithm, lifecycle table, and typed disposition/reason
pairs. Do not add an unruled score-transition reason.

- [ ] **Step 4: Implement snapshot replacement and correction epochs**

Implement exact duplicate, epoch, receipt-time, complete-snapshot,
unsupported-format, replacement, block-preservation, and correction-lineage
rules from the binding ruling.

- [ ] **Step 5: Add property-style bounded transition enumeration**

Enumerate all legal point winners through at least one deuce game and one
7-point tiebreak. Assert score invariants, monotonic revision, exactly one
server, and absorbing terminal states.

- [ ] **Step 6: Complete review, reseal, and run tests**

Obtain independent review before the controller updates the approved expert
inventory/digest. Any production or focused-test change after review restarts
review and resealing.

Run: `python -m unittest tests.tennis_v1.test_tennis_score tests.tennis_v1.test_expert_dependency_boundary -v`
Expected: PASS.

---

### Task 3: Reconstruct the full executable Kalshi book

**Binding implementation authority:** Read
`.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-3-controller-rulings.md`
completely before writing tests or production code. That ruling replaces every
conflicting or abbreviated Task-3 API, transition, arithmetic, fixture, and
test sketch below.

**Files:**
- Modify: `inci_tennis_expert/contracts.py`
- Create: `inci_tennis_expert/market_book.py`
- Modify: `tests/tennis_v1/test_expert_contracts.py`
- Create: `tests/tennis_v1/test_market_book.py`
- Create: `tests/tennis_v1/fixtures/kalshi_orderbook_snapshot_v2.json`
- Create: `tests/tennis_v1/fixtures/kalshi_orderbook_delta_v2.json`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

```python
class BookEventKind(str, Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"
    LIFECYCLE = "lifecycle"


@dataclass(frozen=True, slots=True)
class BookTransitionResult:
    state: BookState
    accepted_event_kind: BookEventKind | None
    accepted_event_sha256: str | None
    executable_move: Decimal
    move_observed_monotonic_ns: int | None
    connection_epoch: int
    sequence: int
    top_of_book_changed: bool


def book_from_snapshot(snapshot: BookSnapshot) -> BookTransitionResult: ...
def require_book_resnapshot(state: BookState) -> BookState: ...
def apply_book_snapshot(
    state: BookState,
    snapshot: BookSnapshot,
) -> BookTransitionResult: ...
def apply_book_delta(
    state: BookState,
    delta: BookDelta,
) -> BookTransitionResult: ...
def apply_market_lifecycle(
    state: BookState,
    lifecycle: MarketLifecycle,
) -> BookTransitionResult: ...
def executable_buy(
    state: BookState,
    outcome: ContractSide,
    contracts: Decimal,
    limit_price: Decimal,
) -> tuple[Decimal, Decimal, tuple[BookLevel, ...]]: ...
```

`executable_buy` returns `(filled_contracts, average_price, consumed_levels)`.
Every exchange-event reducer returns an immutable canonical event-local
witness; callers advance with `result.state`. `require_book_resnapshot` is the
idempotent local trust barrier and returns `BookState` directly.

- [ ] **Step 1: Write additive contract and registry tests**

Add exact construction, invariant-precedence, canonical-byte, digest,
registry-round-trip, and backward-vector tests for `BookEventKind` and
`BookTransitionResult`. Record RED because the symbols and registry entries
do not exist. Add only those capability-free contracts to `contracts.py`;
existing contract semantics and bytes remain unchanged. Accepted snapshot and
delta results must wrap a trusted, gap-free state and must exactly equal that
state's latest executable-move magnitude and monotonic timestamp. Adversarial
constructor and registry-decode tests pin `accepted_event_state` before
`event_move` before `accepted_event_sha256`. Accepted lifecycle results remain
valid around a gapped state and never repair trust.

- [ ] **Step 2: Write snapshot, reciprocal ask, depth, and sequence tests**

```python
def test_buy_yes_consumes_no_bids_as_reciprocal_yes_asks():
    initial = book_from_snapshot(book(yes=[("0.40", "5")],
                                      no=[("0.45", "2"), ("0.44", "4")]))
    filled, average, levels = executable_buy(
        initial.state, ContractSide.YES, Decimal("3"), Decimal("0.56")
    )
    self.assertEqual(filled, Decimal("3"))
    self.assertEqual(
        average,
        Decimal(
            "0.55333333333333333333333333333333333333333333333333333333333333333333333333333333"
        ),
    )
    self.assertEqual(
        tuple(level.quantity for level in levels),
        (Decimal("2"), Decimal("1")),
    )
```

Run: `python -m unittest tests.tennis_v1.test_market_book -v`.
Expected: RED because `market_book.py` is absent.

- [ ] **Step 3: Implement fixed-point snapshot and delta reduction**

Store YES and NO bids in descending price order. A zero-quantity delta removes
the exact level. Require the first snapshot before deltas. Apply the binding
strict sequence, epoch, stale-event, gap-copy, resnapshot, lifecycle,
executable-move, digest, and stable-error precedence. Reject negative,
over-one-dollar, duplicate, float, malformed, crossed, or noncanonical levels
through the exact ruled branch.

Every disconnect, reconnect, subscription change, and forced resnapshot must
first call the exact-type, idempotent `require_book_resnapshot`. Task 8 owns
exactly one active physical WebSocket at a time, allocates a strictly newer
local trust epoch before a reconnect or forced-resnapshot snapshot (including
on the same physical socket), and never uses a REST order-book fallback.
Equal/older same-epoch deltas gap. Replacement time must be at least both
prior book and lifecycle times. Lifecycle events and replacement snapshots
obey the exact six-status graph in the binding ruling; invalid transitions
gap while preserving prior accepted identity and digests.

- [ ] **Step 4: Implement side-correct executable depth**

YES asks derive from reciprocal NO bids; NO asks derive from reciprocal YES
bids. Consumption stops before a level exceeds the limit. Zero and partial
fills are normal. Consumed levels contain the derived ask and consumed
quantity. The zero-fill average sentinel and all arithmetic use the exact
binding precision-80 private Decimal rules.

- [ ] **Step 5: Test reconnect, lifecycle, and causal-observer barriers**

After a gap, `BookState.trusted` remains false until
`apply_book_snapshot` accepts a complete snapshot under a strictly newer local
trust epoch. Old-epoch deltas cannot mutate state. Market lifecycle events use
the separate binding reducer and never fabricate or advance book sequence.
Table-test all 36 lifecycle pairs for both lifecycle events and replacement
snapshots. Table-test exact precedence collisions for every public API.

Every accepted snapshot/delta result carries its own identity, time,
magnitude, epoch, sequence, and top-of-book-change flag, including zero moves.
Its state is trusted/gap-free, and its magnitude/time exactly equal the
state's latest executable-move fields. Constructor and canonical-registry
tests reject every mismatch with the ruling's exact precedence.
Lifecycle/gap/idempotent results cannot relabel retained historical move
fields. Task 5 must ingest these results in order into its persistent
synchronization cursor. Its tests must prove:

```text
move == 0, threshold == 0            -> not large
move > 0, move == threshold          -> large
0 < move < positive threshold        -> not large
move > 0, threshold == 0             -> large
large -> tiny/zero                    -> UNEXPLAINED_BOOK_MOVE remains
large -> reconnect -> unchanged book -> UNEXPLAINED_BOOK_MOVE remains
```

Task 5 uses the literal predicate
`move > Decimal("0") and move >= threshold`; threshold equality is inclusive,
but zero is never large.
Only Task 5's exact causal point rule or paired tennis-and-book reset may clear
that barrier; Task-3 latest-move fields are diagnostics, not authorization.

- [ ] **Step 6: Complete review, reseal, and run tests**

Obtain independent review before the controller updates the approved expert
inventory/digest. Any production or focused-test change after review restarts
review and resealing.

Run: `python -m unittest tests.tennis_v1.test_expert_contracts tests.tennis_v1.test_market_book tests.tennis_v1.test_expert_dependency_boundary -v`
Expected: PASS.

---

### Task 4: Bind provider identities to one Kalshi match

**Binding implementation authority:** Read
`.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-4-controller-rulings.md`
completely before writing tests, schema, fixture, or production code. That
ruling replaces every conflicting or abbreviated Task-4 manifest, schema,
decoder, loader, resolution, collision, fixture, test, and resealing sketch
below. Task 1's sealed `MatchBinding`, `ArtifactPin`, orientation function, and
canonical encoding remain unchanged.

**Files:**
- Create: `inci_tennis_expert/match_binding.py`
- Create: `inci_tennis_io/pinned_artifacts.py`
- Create: `inci_tennis_expert/schemas/match-binding-v1.schema.json`
- Create: `inci_tennis_expert/schemas/binding-review-v1.schema.json`
- Create: `tests/tennis_v1/test_match_binding.py`
- Create: `tests/tennis_v1/fixtures/match_binding_schema_example.json`
- Create: `tests/tennis_v1/fixtures/binding_review_schema_example.json`
- Modify: `inci_tennis_expert/contracts.py`
- Modify: `tests/tennis_v1/test_expert_contracts.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

```python
def decode_binding_universe(
    manifest_payload: bytes,
    review_payload: bytes,
    *,
    manifest_pin: ArtifactPin,
    review_pin: ArtifactPin,
) -> BindingUniverse: ...


def binding_universe_sha256(
    universe: BindingUniverse,
) -> str: ...


def binding_metadata_for(
    universe: BindingUniverse,
    binding: MatchBinding,
) -> BindingMetadata: ...


def read_pinned_artifact(
    request: PinnedArtifactReadRequest,
) -> PinnedArtifactBytes: ...


def resolve_binding(
    provider_state: TennisState,
    kalshi_event_ticker: str,
    universe: BindingUniverse,
) -> MatchBinding: ...


def require_authorized_route(
    universe: BindingUniverse,
    binding: MatchBinding,
    market_ticker: str,
    contract_side: ContractSide,
) -> BindingRoute: ...
```

`player_side_for_contract` is the object-identical Task-1 re-export, never a
second implementation or route-authority bypass. The exact new
`BindingRoute`, `SettlementSemantics`, `BindingMarketMetadata`,
`BindingMetadata`, `BindingReviewDecision`, and `BindingUniverse` contracts,
four domain-separated digest functions, the canonical review-artifact raw
digest function, pinned-artifact request/result types, limits, stable errors,
and field order are defined completely by the binding ruling.

- [ ] **Step 1: Write the complete exact-or-deny RED suite**

First prove Task 3 independently reviewed, resealed, and green. Then cover
exact new contract fields, canonical registry order, independent canonical
vectors, all four domain-separated digests, canonical review bytes/raw digest,
raw-artifact-versus-universe identity, digest-before-parse precedence for both
payloads, strict JSON and both exact schema shapes, structural
provider/participant/competition/match fields, placeholders, machine-readable
event membership, independent settlement semantics, direct-YES-only routes,
the separate startup-authorized review decision, start projection, ordering
and every collision domain, bidirectional player-ID mapping per provider
source/lineage, complete binding/metadata projection,
subset/fabrication/mixed-artifact denial, exact value-membership resolution
and metadata lookup, pinned I/O delegation, fail-closed bytecode scanning,
Phase-1 import allowlisting, production resource inventory, Task-5 handoff,
and every stable precedence oracle in the ruling.

- [ ] **Step 2: Run focused test and verify RED**

Run:

```bash
/Users/mthanki/.venvs/inci-expert-py314/bin/python -B -m unittest \
  tests.tennis_v1.test_match_binding \
  tests.tennis_v1.test_expert_dependency_boundary -v
```

Expected: canonical missing-contract/module/resource RED without a network
call.

- [ ] **Step 3: Add the canonical contracts and sealed-universe digests**

Add only the ruled frozen/slotted contract types and digest functions to
`contracts.py`, in the exact field and canonical-registry order. Preserve all
existing contract behavior and vectors. Make `BindingUniverse` recompute the
review-evidence and universe domain hashes over the full ordered binding and
metadata projection plus the separate raw artifact digest and independently
pinned review. `BindingReviewDecision` must recompute its raw artifact SHA
from exact canonical review JSON fields, so a changed evidence projection
cannot retain the startup-authorized review digest. Reject every subset,
reorder, substitution, fabricated metadata record, mixed artifact, review
substitution, or forged digest that attempts to retain the authorized pins.

- [ ] **Step 4: Implement both strict decoders and canonical projections**

Create both exact closed Draft-2020-12 schemas and sanitized synthetic
fixtures. Verify both startup-authorized raw digests before UTF-8 or JSON
work. Map `RecursionError` and oversized integer tokens to the ruled stable
JSON branches. Require raw review bytes to equal the exact canonical encoding
whose recomputed SHA matches the independent review pin; alternate whitespace,
key order, escaping, or trailing bytes fail. Enforce the 128-binding and 1 MiB
manifest ceilings, 16 KiB review ceiling, and inclusive 900,000,000,000 ns
start tolerance.

Require machine-readable `sport == "tennis"`,
`competition_kind == "real"`, `competitor_count == 2`, confirmed player
participants, singles match-winner product, exact event membership, and
independent named-player settlement projections. Human-readable market/rule
text remains pinned evidence, never authority. Each machine-readable
membership record must retain exact source ID, source version, capture time,
event-catalog pin, raw evidence pin, and its digest projection; membership
capture must precede binding artifact creation and review.

Project the existing slim `MatchBinding` plus the complete canonical metadata,
including canonical player, tournament, season, draw, round, tour, tier,
surface, membership provenance, settlement, route, and evidence digests.
Across all HOME/AWAY occurrences sharing exact provider source and lineage,
enforce a bijection in both directions. One provider player ID maps to one
canonical ID. One canonical ID maps to one provider ID. Test both conflict
directions and the ruled provider-side precedence when both are invalid.
Authorize exactly HOME-YES and AWAY-YES; every NO route is unsupported.

- [ ] **Step 5: Implement universe-only lookup and one-read pinned I/O**

Validate the complete `BindingUniverse` before resolution, metadata lookup, or
route authorization. Resolve only one exact provider source, revision domain,
lineage, provider match, ordered players, supported format, provider scheduled
start, and Kalshi event ticker. Never use display names, ticker abbreviations,
candidate order, canonical match ID as provider ID, tolerance, or
`player_side_for_contract` to repair drift or grant a route.
Membership is exact deterministic dataclass value equality at one unique
index: a separately constructed exact-equal `MatchBinding` succeeds, while
any field-different value fails.

Use only exact `PinnedBytes`, `PinnedFileError`, and `read_pinned_file`
bindings from Phase 1. Delegate the exact path, digest, repository root,
forbidden state root, and byte limit once; translate failures without dynamic
data; never reopen the pathname. Require `Path` objects that are already
absolute and contain no retained `..`; do not claim to detect `.` or empty
components erased by `Path` construction.

Update the resource scanner to reject every `__pycache__` directory/entry and
every `.pyc` anywhere under a sealed package root, including plausible
matching-tag bytecode beside source. Run verification with the pinned
interpreter's `-B` flag or `PYTHONPYCACHEPREFIX` outside the repository. Before
sealing, remove only verified generated cache directories under the four exact
sealed package roots; never inventory them as resources.

- [ ] **Step 6: Complete independent review, controller reseal, and verification**

Freeze all three changed production modules, both schemas, both fixtures, and
focused tests. Obtain independent review before the controller changes any
AST digest, raw-resource digest, production/resource inventory, canonical
vector, registry expectation, or Phase-1 import allowlist. Any production,
schema, fixture, or focused-test edit restarts review.

After clean review, the controller records the three canonical AST seals, both
schemas' exact raw SHA-256 values, new registry/vector expectations, and the
three exact Phase-1 pinned-file imports, then runs:

```bash
/Users/mthanki/.venvs/inci-expert-py314/bin/python -B -m unittest \
  tests.tennis_v1.test_match_binding \
  tests.tennis_v1.test_pinned_file \
  tests.tennis_v1.test_expert_contracts \
  tests.tennis_v1.test_tennis_score \
  tests.tennis_v1.test_expert_dependency_boundary -v
/Users/mthanki/.venvs/inci-expert-py314/bin/python -B -m unittest discover \
  -s tests/tennis_v1 -t . -v
/Users/mthanki/.venvs/inci-expert-py314/bin/python -B tests.py
```

Expected: PASS with no network call and unchanged Phase-1, Task-2, v6, and
dependency digests except for the expressly reviewed Task-4 `contracts.py`
additions and their new seal.

Before Task 5 begins, update its ruled interface to consume the exact
`BindingUniverse`, member `MatchBinding`, paired `BindingMetadata`, and
`SyncPolicy`. It must require
`SyncPolicy.universe_sha256 == BindingUniverse.universe_sha256`, where the
latter is the recomputed domain hash; it must never compare policy to the raw
manifest digest or accept a bare binding tuple.

---

### Task 5: Create the synchronization and causal-explanation barrier

**Files:**
- Create: `inci_tennis_expert/synchronizer.py`
- Create: `tests/tennis_v1/test_synchronizer.py`
- Modify: `inci_tennis_expert/contracts.py`
- Modify: `inci_tennis_expert/tennis_score.py`
- Modify: `tests/tennis_v1/test_expert_contracts.py`
- Modify: `tests/tennis_v1/test_tennis_score.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`
- Binding specification:
  `.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-5-controller-rulings.md`

**Interfaces:**

```python
def validate_tennis_state(state: TennisState) -> None: ...


def synchronization_session_from_artifacts(
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> SynchronizationSessionState: ...


def assert_synchronization_session_compatible(
    state: SynchronizationSessionState,
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> None: ...


def synchronize(
    state: SynchronizationSessionState,
    evidence: SynchronizationInput,
    *,
    now: PairedTimeObservation,
) -> SynchronizationTransitionResult: ...


def validate_synchronization_transition(
    prior: SynchronizationSessionState,
    transition: SynchronizationTransitionResult,
) -> None: ...
```

- [ ] **Step 1: Read the binding ruling and prove the canonical RED**

Read the Task-5 controller ruling completely. Confirm Tasks 3 and 4 are
independently reviewed/resealed and the exact Task-3
`BookEventKind`/`BookTransitionResult` witness and final Task-4
`BindingUniverse`/paired `BindingMetadata` are present. Add failing tests for
the public reachability API, every new canonical contract/enum, and absent
stateful synchronizer.

- [ ] **Step 2: Extend contracts and expose the one tennis reachability validator**

Add exactly `SyncInputKind`, `CausalPointWitness`, `PendingBookMove`,
`TennisSyncCursor`, `LastSyncEmission`, `BookSyncCursor`,
`SynchronizationSessionState`, `SynchronizationInput`, `SyncResult`, and
`SynchronizationTransitionResult` with the ruling's exact field order and
invariants, including all pending/cursor/current-tennis/current-book
cross-object bounds under the existing static semantic branches. Bound every
last/causal/consumed point witness identity and receipt by current tennis,
require exact digest and receipt at equal coordinates, and bound pending move
time by current book observation. Register the contracts explicitly, add
known vectors, and add one-field constructor mutation tests. Expose
`validate_tennis_state` and make all Task-2 reducers reuse it. The transition
also carries `prior_decision_sequence` immediately after
`prior_session_sha256`; its constructor requires returned sequence = claimed
prior sequence + trusted-result count.

- [ ] **Step 3: Implement exact ordered evidence ingestion**

Create a pure immutable session reducer. Embed the complete exact
`BindingUniverse`, pair every member `MatchBinding` with exact same-index
`BindingMetadata`, require the computed universe digest in `SyncPolicy`, and
create one per-match and two per-ticker cursors. Every call receives exact
injected `now`; every returned transition embeds that observation and the
exact input unchanged. Task 6 wraps that exact input and observation with
their already-durable Phase-1 RAW parent before calling Task 5, copies and
proves the RAW parent's exact local wall/monotonic/uncertainty fields, and
rejects CONTROL as an observation parent. Recompute claimed Task-2/Task-3
results from the exact normalized `provider_event`/`book_event`; never trust
result shape alone. Implement
`validate_synchronization_transition(prior, transition)` by recomputing the
whole exact transition from that actual prior/input/observation and requiring
exact equality. Ingest explicit resnapshot inputs, reject skipped
evidence/mid-session drift with the stable global exception, and return
`CONTRACT_MISMATCH` without cursor mutation for syntactically valid unbound
book/clock tickers. For tennis origin, apply exact precedence: existing cursor
is global second-origin drift before identity checks; empty mismatched origin
is `BINDING_DRIFT` and stays empty; empty matching origin establishes.

- [ ] **Step 4: Implement the persistent causal barrier**

Observe every accepted Task-3 snapshot/delta witness in evidence order.
Persist large-move intervals across tiny/zero/deep/quantity/lifecycle/gap/
resnapshot transitions and canonical value round-trips, including a
pre-tennis-origin move with an optional epoch floor installed by origin.
Clear pending only with a new, unconsumed, current-correction-epoch
directional `POINT_APPLIED` witness or an exact paired reset evaluated on
replacement snapshot. A correction clears retained explanation but never
pending; process reset before replacement move, and let a new large reset
snapshot create a new interval. Persist one independent consumed-point
identity per ticker so a point cannot authorize multiple later moves; compare
that identity by canonical match, correction epoch, revision, and semantic
event digest, deliberately excluding receipt time. Canonical round-trip is a
pure contract test, not authority to resume an interrupted session.

- [ ] **Step 5: Implement literal blocker precedence and global emissions**

Apply all 23 `SyncReason` predicates in the ruling's literal order, with
explicit ownership for reserved/unreachable reasons and exact equality
boundaries. Emit only for a new provider identity or executable book
epoch/sequence, compute the observation-independent fingerprint over exact
universe/policy/binding/metadata/feed/book identities, and allocate one
session-wide decision sequence in canonical ticker order. Blocked and
duplicate evaluations consume no sequence.

- [ ] **Step 6: Prove precedence, state integrity, determinism, and interleaving**

Add exhaustive public-API tables and compatible first-match blocker
collisions, with mutually exclusive groups and reserved ownership tests.
Cover large-to-tiny/reconnect, pre-origin pending, point nonreuse, correction
semantics, paired-reset boundaries, exact event/result recomputation,
unbound-input nonmutation, origin precedence, one-field constructor mutation
matrix, and canonical round-trip with pending/explanation/consumed/emission
state. Run 1,000 independent and 1,000 sequential determinism cases plus
three-match/two-ticker interleavings under one global sequence. Bind Task 6
to mutually exclusive per-RAW-parent groups: either `1..64` synchronization
observations or exactly one Task-6-owned ignored/rejected wrapper. Only the
all-synchronization group enters the Task-5 loop and calls
`validate_synchronization_transition` against the current intermediate
synchronization state; comparing only a transition's claimed prior digest or
sequence is forbidden. Ignored/rejected wrappers have no Task-5
evidence/observation and never call Task 5. Every group kind performs one
outer CAS over the prior `ExpertStateV1` digest plus journal cursor/head,
append+fsyncs the whole group, and publishes only its final state. Reject
mixed and empty groups. Prove RAW-only parent/time copying, exact
empty `synchronization_session_from_artifacts` genesis and manifest pins,
CONTROL rejection, stale/racing group CAS behavior, and strict interrupted
session nonresume. No truncation, repair, completion, adoption, or prior
session continuation is allowed.

- [ ] **Step 7: Complete independent review, controller reseal, and verification**

Freeze contract, reducer, synchronizer, and focused tests. Any edit restarts
independent review. After clean review only, extend the explicit package
inventory and canonical AST seals, then run focused Task-5 tests, full Tennis
discovery, frozen v6, compilation, whitespace, foundation integrity,
dependency lock, canonical registry, resource inventory, and import-boundary
checks.

Run:
`/Users/mthanki/.venvs/inci-expert-py314/bin/python -B -m unittest tests.tennis_v1.test_synchronizer -v`
Expected: PASS.

---

### Task 6: Add a companion expert journal and exact two-journal replay

**Binding ruling:** Read
`.superpowers/sdd/2026-07-29-inci-expert-tennis-strategy/task-6-controller-rulings.md`
completely before RED. It replaces every abbreviated Task-6 contract in this
plan. Final Tasks 4 and 5 are now authoritative: Task 5 embeds the complete
`BindingUniverse`, uses exact `SynchronizationInput.provider_event` and
`.book_event`, returns `SynchronizationTransitionResult.input` and
`.observation`, and exposes
`synchronization_session_from_artifacts(universe, policy)`. Task 6 must use
those exact fields/signatures, exact empty genesis, RAW-only parent/time
proof, full authority-aware validation of every private intermediate
transition, and one group-level CAS. No compatibility wrapper is permitted.

**Files:**
- Modify: `tennis_v1/ingress.py`
- Modify: `tennis_v1/retention.py`
- Modify: `inci_tennis_expert/contracts.py`
- Modify: `inci_tennis_io/ports.py`
- Modify: `inci_tennis_io/facade.py`
- Modify: `tests/tennis_v1/test_retention.py`
- Modify: `tests/tennis_v1/test_ingress.py`
- Modify: `tests/tennis_v1/test_expert_contracts.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`
- Create: `inci_tennis_expert/state.py`
- Create: `inci_tennis_expert/observation.py`
- Create: `inci_tennis_expert/task6_fallback_normalizer.py`
- Create: `inci_tennis_expert/reducer.py`
- Create: `inci_tennis_expert/journal_codec.py`
- Create: `inci_tennis_expert/replay.py`
- Create: `inci_tennis_expert/facade.py`
- Create: `inci_tennis_io/expert_journal_store.py`
- Create: `inci_tennis_runtime/expert_controller.py`
- Create: `inci_tennis_runtime/replay_service.py`
- Create: `inci_tennis_expert/schemas/expert-session-manifest-v1.schema.json`
- Create: `inci_tennis_expert/schemas/expert-journal-record-v1.schema.json`
- Create: `inci_tennis_expert/schemas/expert-journal-group-v1.schema.json`
- Create: `inci_tennis_expert/schemas/expert-session-terminal-v1.schema.json`
- Create: `inci_tennis_expert/schemas/expert-synchronization-applied-v1.schema.json`
- Create: `inci_tennis_expert/schemas/expert-observation-ignored-v1.schema.json`
- Create: `inci_tennis_expert/schemas/expert-observation-rejected-v1.schema.json`
- Create: `inci_tennis_expert/schemas/task6-fallback-no-payload-v1.schema.json`
- Create: `tests/tennis_v1/test_expert_observation.py`
- Create: `tests/tennis_v1/test_expert_reducer.py`
- Create: `tests/tennis_v1/test_expert_journal_codec.py`
- Create: `tests/tennis_v1/test_expert_journal_store.py`
- Create: `tests/tennis_v1/test_expert_controller.py`
- Create: `tests/tennis_v1/test_expert_replay.py`

**Interfaces:**

```python
ExpertObservationV1 = (
    ExpertSynchronizationObservationV1
    | ExpertIgnoredObservationV1
    | ExpertRejectedObservationV1
)


def initial_expert_state(
    manifest: ExpertSessionManifestV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
) -> ExpertStateV1: ...


def reduce_expert_parent(
    state: ExpertStateV1,
    observations: tuple[ExpertObservationV1, ...],
) -> ExpertReductionV1: ...


def begin_expert_replay(
    *,
    manifest: ExpertSessionManifestV1,
    current_environment: ExpertCurrentEnvironmentV1,
    universe: BindingUniverse,
    policy: SyncPolicy,
    evidence: EvidenceReplayContextV1,
    authorization: RetentionReplayAuthorizationV1,
) -> ExpertReplayAccumulatorV1: ...


def replay_expert_parent_group(
    accumulator: ExpertReplayAccumulatorV1,
    *,
    authorization: RetentionReplayAuthorizationV1,
    parent: PersistedEvent,
    stored_group: ExpertJournalGroupV1,
    stored_payloads: tuple[bytes, ...],
) -> ExpertReplayAccumulatorV1: ...


def finish_expert_replay(
    accumulator: ExpertReplayAccumulatorV1,
    *,
    final_authorization: RetentionReplayAuthorizationV1,
    companion_terminal: ExpertSessionTerminalV1 | None,
    companion_scan: ExpertJournalScanSummaryV1,
) -> ExpertReplayResultV1: ...
```

The exact closed Task-6 event schemas are:

```text
(synchronization_applied, 1)
(observation_ignored, 1)
(observation_rejected, 1)
```

One literal Phase-1 session ID is repeated everywhere. The manifest binds the
complete Phase-1 provider/product/lineage, permission, qualification, trace
and request authority; exact Task-4 universe/raw/review pins; Task-5
`sync_policy_sha256` and exact empty `initial_synchronization_sha256`;
environment/capacity/normalizer pins; and complete four-resource structural
plus three-resource event schema bundles. It embeds the canonical
`ExpertProviderDomainBindingV1`: provider source equals Phase-1 provider,
lineage uses the ruled four-field domain-separated formula, and the one Task-4
revision domain is never equated to product tier. Environment and physical
file identities are collected through code-owned inventories/open-descriptor
surfaces, never caller digest bags. Genesis is recomputed with
`synchronization_session_from_artifacts(universe, policy)` and never accepts
caller-supplied or carried-forward state.

The Phase-1 code and adapter fields reuse the unchanged
`tennis_v1.fingerprints.code_sha256` and active-adapter closure authorities;
Task 6 defines no competing Phase-1 inventory domain. Task-6 resources come
from one code-owned validated source-distribution root containing the five
direct-sibling package roots plus `pyproject.toml` and `requirements.txt`.
Non-source, symlinked, split-root, wheel, or editable-indirection layouts fail
closed. The seven persisted structural/event schemas remain unchanged; one
eighth `task6-fallback-no-payload-v1.schema.json` resource pins the dedicated
static fallback normalizer and is not inserted into either persisted schema
bundle.

The Task-6 production service itself owns:

```text
BoundedIngress.drain_one -> EventRuntime.ingest -> durable raw
-> current Phase-1 transform+analysis/session authorization
-> pinned normalization -> pure reduction -> full group append+fsync
-> receipt comparison -> expert state publication
```

Every Phase-1 raw record has one nonempty parent group. `parent_output_index`
is zero-based, `parent_output_count` is `1..64`, and every Task-5 input,
including CLOCK and BOOK_RESNAPSHOT_REQUIRED, is tied to an already durable
RAW record. Parent proof copies the RAW record's exact `local_wall_ns`,
`local_monotonic_ns`, and `clock_uncertainty_ns`; wrapper time equals that
triple. CONTROL is forbidden as an observation parent. Phase-1
`SESSION_START` and terminal controls are separate manifest/terminal anchors.

The normalizer output is exclusively all `1..64` synchronization drafts,
exactly one ignored draft, or exactly one rejected draft. Production dispatch
is sealed/static/fallback-only in Task 6; tests call the pure binder with exact
drafts. Per-event and aggregate canonical-byte budgets are enforced.

- [ ] **Step 1: Lock final Task-4/Task-5 handoff and record RED**

Write a test-side reconciliation table for the final fields/signatures in
ruling section 1. It must assert `SynchronizationInput.provider_event` and
`.book_event`, the embedded `SynchronizationSessionState.universe`, exact
transition `.input`/`.observation`, copied parent time, and:

```text
binding_universe_sha256(universe) == manifest.match_binding_universe_sha256
policy.universe_sha256 == manifest.match_binding_universe_sha256
expert_contract_sha256(policy) == manifest.sync_policy_sha256
expert_contract_sha256(synchronization_session_from_artifacts(universe, policy))
  == manifest.initial_synchronization_sha256
provider_request_binding_sha256 != match_binding_universe_sha256
```

Assert the exact `ExpertProviderDomainBindingV1` equations:

```text
every binding.provider_source_id == Phase-1 provider_id
every binding.source_lineage_sha256
  == sha256(
       b"INCI-EXPERT-PROVIDER-SOURCE-LINEAGE-V1\0"
       + canonical_expert_bytes(
           (provider_id, product_tier, source_lineage_id,
            provider_manifest_canonical_sha256)))
every binding.revision_domain_id == the one bound feed revision_domain_id
revision_domain_id is not equated to product_tier
```

Task-7/8 adapter handoff must compute this same formula from authorized
Phase-1 inputs and may not accept a provider lineage shortcut.
`product_tier` has exactly two roles: it remains exact Phase-1 authorization
provenance, and it is intentionally one of the four lineage-preimage fields
to prevent cross-tier transplant. It is never equated to a Task-4
`revision_domain_id`, metadata tier, revision counter, or independent binding
field.

Add RED for the narrow
`RetentionCoordinator.issue_expert_state_root_account_lock_request()` bridge.
It must issue one opaque, owner/generation-bound request from the already
validated Phase-1 state root/account lock and atomically transfer a private
sampler for the same injected Phase-1 retention clock into the root authority.
No private coordinator attribute, ambient clock, or second root/path/lock
lookup is allowed. Add mutable-clock, sampling-error/global-halt, and exact
before/equal/after-deadline tests. The sampler is the sole reusable
capability: test repeated owner-thread samples and revocation on
root-close/generation/fork/cross-thread drift.

Add RED for the same module's exact read-only
`require_expert_companion_creation_live` guard. It must exact-bind the
authorizer/manifest/decision/coordinator/root/account lock/owner/generation,
require healthy current marker/WAL plus durable SESSION_START, independently
reject any durable or uncertain terminal, return no state, and perform no
mutation. Test operator-stop and halted terminals whose provider poll is
still false, every owner/session/generation mismatch, and prove the guard
cannot change capture, WAL, terminal, or retention state.

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_match_binding \
  tests.tennis_v1.test_synchronizer -v
```

Expected before Task 6: both prerequisite suites pass. Then add imports for
the absent Task-6 symbols and record RED with an import failure naming
`ExpertSessionManifestV1`.

- [ ] **Step 2: Add contract, domain-hash, and schema RED tests**

Add exact tests for every enum/dataclass in ruling sections 4-14, including
the structural/event schema bundles, capacity proof, exact
`EvidenceReplayContextV1`, exact `RetentionReplayAuthorizationV1`, RAW-only
parent/time proof, canonical `ExpertProviderDomainBindingV1`, private-
construction `ExpertPhysicalFileIdentityV1`, cursor
`last_parent_record_sha256`, emergency permit/receipt, and all 35 reachable
replay mismatch values.

Use a separate test-only canonical implementation to compute sanitized values
and expected digests at test time for these literal domains:

```text
INCI-EXPERT-CURRENT-ENVIRONMENT-V1
INCI-EXPERT-CODE-INVENTORY-V1
INCI-EXPERT-IO-CODE-INVENTORY-V1
INCI-EXPERT-ADAPTER-CODE-INVENTORY-V1
INCI-EXPERT-RUNTIME-CODE-INVENTORY-V1
INCI-EXPERT-DEPENDENCY-INVENTORY-V1
INCI-EXPERT-PYTHON-RUNTIME-INVENTORY-V1
INCI-EXPERT-NORMALIZER-CODE-V1
INCI-EXPERT-PROVIDER-SOURCE-LINEAGE-V1
INCI-EXPERT-PROVIDER-DOMAIN-BINDING-V1
INCI-EXPERT-RETENTION-BINDING-V1
INCI-EXPERT-STRUCTURAL-SCHEMA-BUNDLE-V1
INCI-EXPERT-EVENT-SCHEMA-BUNDLE-V1
INCI-EXPERT-NORMALIZER-REGISTRY-V1
INCI-EXPERT-CAPACITY-PROOF-V1
INCI-EXPERT-SESSION-MANIFEST-V1
INCI-EXPERT-OBSERVATION-V1
INCI-EXPERT-STATE-V1
INCI-EXPERT-JOURNAL-RECORD-V1
INCI-EXPERT-TRACE-SEED-V1
INCI-EXPERT-TRACE-STEP-V1
INCI-EXPERT-JOURNAL-GROUP-V1
INCI-EXPERT-SESSION-TERMINAL-V1
INCI-EXPERT-JOURNAL-FRAME-V1
INCI-EXPERT-REPLAY-AUTHORIZATION-V1
INCI-EXPERT-PHASE1-SESSION-ANCHOR-V1
INCI-EXPERT-COMPANION-SESSION-ANCHOR-V1
INCI-EXPERT-PHYSICAL-FILE-IDENTITY-V1
INCI-EXPERT-PHASE1-REPLAY-SUMMARY-V1
INCI-EXPERT-REPLAY-DIAGNOSTIC-FILE-PROOF-V1
INCI-EXPERT-REPLAY-DIAGNOSTIC-PROOF-V1
```

Create the seven strict persisted Draft-2020-12 schemas with
`additionalProperties:false`, closed event kinds/versions, exact integer and
digest ranges, and local references only. Structural roles/order are exactly
`session_manifest`, `journal_record`, `parent_group`, `session_terminal`
paired with the four ruled contracts/resources; event pins use the three
ruled event order. Bind both complete bundles.

Add the eighth `task6-fallback-no-payload-v1.schema.json` resource with exact
bytes `false\n`; it belongs to the static normalizer registry, not either
persisted schema bundle. Reuse the unchanged Phase-1 code fingerprint and
active-adapter digest authorities exactly. Define literal ordered expert,
I/O, runtime, dependency, schema, and Python-runtime inventories plus one
code-owned environment collector rooted at the validated five-package
source-distribution parent. Test every order/member/byte/runtime-field
mutation, every source-root/dependency replacement, and the fallback source/
schema seals; no session or replay service accepts caller-provided
environment field digests. Do not commit literal generated digest vectors,
encoded WAL/journal bytes, or hex/base64 fixtures.

Issue the one-shot environment collection authority only from the validated
root plus the exact bound `ProviderPersistenceAuthorizer` and
`RetentionCoordinator`; a root alone cannot infer a session. The authority
privately derives the Phase-1 manifest/provider/product tier and the collector
accepts none of those as caller values. Test wrong authorizer/coordinator/
root/session/generation combinations and caller provider-tier substitution.

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_contracts \
  tests.tennis_v1.test_expert_observation -v
```

Expected RED: absent Task-6 contracts/functions. Implement only the exact
contracts, registry additions, hash functions, schemas, and validators; rerun
to PASS without changing any earlier canonical vector.

- [ ] **Step 3: Add and implement total normalization and pure reduction**

Write failing tests proving each raw maps to:

```text
1..64 synchronization observations only
exactly one ignored observation
or exactly one rejected observation
```

Cover every final Task-5 input kind, CLOCK from TIMER raw,
BOOK_RESNAPSHOT_REQUIRED from KALSHI/SYSTEM raw, exact parent digest through
`tennis_v1.codec.canonical_record_sha256`, exact copied parent time and wrapper
observation, zero-based output indexes, strict normalizer pins, invalid
payload, mixed/empty/65-result normalizers, sanitized exceptions, per-event
and aggregate canonical-byte limits, Task-5 drift, correction, book gap, and
multi-match ordering.

Implement the declarative registry and static code-owned dispatch with its one
sealed Task-6 fallback and empty production provider/KALSHI/TIMER/SYSTEM entry
tuple. There is no injected callable/port. Tests invoke the pure binder with
exact drafts. The fallback implementation lives only in
`task6_fallback_normalizer.py`, never reads `parent.payload`, and returns one
`NORMALIZER_NOT_REGISTERED` ignored draft. Its code seal is the ruled
domain-separated exact-source digest; its schema seal is the raw SHA-256 of
the exact `false\n` eighth resource. Tasks 7/8 may later extend only reviewed
static dispatch.

Implement `prove_expert_capacity(universe, policy)` as a formal upper-bound
calculation, not sample serialization. Enforce the exact 131,064-byte
per-item and 8,388,608-byte aggregate limits independently for normalized
draft bytes and final event payload bytes, plus the exact group metadata/frame
limits. If a parent group cannot fit, discard any private intermediate
reduction, replace it with one static `GROUP_CAPACITY_EXCEEDED` rejected group
whose synchronization remains prior-state-identical, and halt; never split or
partially emit.

Implement `initial_expert_state(manifest, universe, policy)` by recomputing
exact empty Task-5 genesis. Implement `reduce_expert_parent` with exact
transition input/observation equality and every intermediate
`validate_synchronization_transition` check. Constructor tests enforce the
complete rejected payload reason matrix and exact `PRIOR_OUTCOME_HALTED` /
`PRIOR_GROUP_HALTED` forced reasons.

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_observation \
  tests.tennis_v1.test_expert_reducer \
  tests.tennis_v1.test_synchronizer -v
```

Expected: PASS, including 1,000 byte-identical reductions and full canonical
Task-5 state after every applied input.

- [ ] **Step 4: Add and implement the pure journal codec**

Write RED tests for the exact `>8sHHI` file header,
`>4sBBHQQII` frame prefix, `>Q32s4s` trailer, manifest/group/terminal frame
order, separate length-delimited payload area, frame SHA-256, 76-byte fixed
overhead, and these derived ceilings:

```text
group metadata        8,388,532
group payload area    8,388,608
maximum group frame  16,777,216
terminal metadata     1,048,576
maximum terminal      1,048,652
emergency reserve    17,825,868
```

Pin the trace seed to the exact canonical projection
`(session_id, expert_manifest_sha256, initial_expert_state_sha256)`.
Manifest frame is zero; group and frame sequences are `1..N`;
`first_expert_seq = prior expert_seq + 1`; record sequences are consecutive;
terminal frame is `group_count + 1`. Test empty, one-group, and multi-group
sessions plus every gap/duplicate/off-by-one mutation.

Generate bytes only in temporary directories. Test every final-frame cut,
interior corruption, bytes after terminal, oversized length before allocation,
unknown schema/version/kind, payload/record/group/trace/state mutation, and
bounded one-group decode.

Implement `journal_codec.py` as a pure bytes/contract module. It must not
import a path, filesystem, clock, process, retention, reader, writer, adapter,
or runtime.

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_journal_codec -v
```

Expected: PASS with independently computed test-time digests and no checked-in
generated digest constant, WAL, journal, hex dump, or base64 dump.

- [ ] **Step 5: Add and implement the restricted physical store**

Write RED tests for marker-first fsync, durable header/manifest before create
returns, complete-write loops, one group fsync before receipt, terminal as the
last frame, strict inventory, `0700` directories, `0600` regular single-link
files, owner/device/inode checks, `O_NOFOLLOW`, `O_EXCL`, and descriptor-
relative operations.

Marker `created_at_ns` is sampled internally from the same injected Phase-1
retention clock during marker construction, must be strictly before the
common deadline, and is bound into exact marker bytes/identity. Test
before/equal/after deadline, mutable-clock provenance, replay cross-check, and
rejection of caller/ambient/filesystem timestamp substitution.

Add the narrow retention bridge
`require_expert_companion_creation_live(persistence_authorizer=...)`. It
returns no state and, under the coordinator condition, validates the exact
authorizer/manifest/decision/root/account-lock/owner/generation and
independently proves the Phase-1 WAL session is healthy with durable
SESSION_START and no durable or uncertain terminal. At each creation seam,
call in exact order: `require_provider_operation`, rebound
`authorize_analysis`, `poll_session()` requiring false, the independent
nonterminal bridge, then same-clock deadline/root/identity validation. Repeat
immediately before marker first byte, reserve create/fallocate, journal-header
first byte, and create return. Failure writes/allocates nothing further,
exposes no writer, and purges. A false poll alone is insufficient; if a
terminal already exists, never call the ingress bridge. Add barrier-held
terminal/authorization/deadline races at all four seams.

Revalidate marker/root/analysis authority and sample the same clock before
every live write transition and receipt/publication boundary: before marker,
before reserve create/allocation, before journal header after reserve setup,
create return, permit CAS, held permit use, publication acknowledgement,
reserve release, terminal bytes,
between emergency group fsync and terminal bytes, and final terminal/
emergency receipt. Equality writes no next byte, purges, and publishes
nothing. Test every held-operation crossing with barriers.

Bind the exact `ProviderPersistenceAuthorizer`/`RetentionCoordinator` into
the writer at creation. Before expert normalization call the exact
`require_provider_operation`, `authorize_transform(raw)`,
`authorize_analysis`, and `poll_session(False)` sequence; repeat current
analysis/session authorization at every group write/publication seam. After
an exact Phase-1 terminal, terminalization requires analysis/retention and
exact reason alignment; poll `True` is accepted only for clean
`session_end`, while operator-stop/halted cases require their exact aligned
state and never reopen capture. Denial closes Phase 1 through
`close_external_halt` only while it is live; if its terminal already exists,
the bridge is forbidden and the companion is directly aborted/purged.
Persist no expert rejection/terminal after denial, publish nothing, and raise
only the fixed live-authorization exception. Test access expiry before
retention expiry, clean session-end terminal alignment, and every held-
operation race.

Acquire companion and read-only evidence subroots from one opaque request
issued by the already validated Phase-1 state-root/account lock. Test that a
second path/root/lock lookup and private `RetentionCoordinator` access are
impossible. Add the exact reviewed I/O-to-`journal_codec` dependency; do not
claim the I/O allowlist is unchanged.

Add private-construction `ExpertPhysicalFileIdentityV1` and I/O facade
collectors for `(phase1_marker, phase1_wal)` and
`(expert_marker, expert_journal)`. The identity digest binds role plus
open-descriptor device/inode/uid/mode/link count/size/mtime/ctime and the
role-specific entire marker digest or header digest plus exact
SESSION_START/manifest anchor. Re-derive from the same validated descriptors
before/after every bounded read, after every pair, and before finish; replay
callers cannot fabricate stat/content fields. Test every mutation and
replacement, including same-inode same-size mutation of a previously consumed
body byte.

Exercise partial/zero writes, fsync/close failure, capacity denial, exact
physically allocated 17,825,868-byte reserve, private reserve identity,
sparse/truncate-only rejection, deallocation/substitution, durable-
unacknowledged receipt loss,
PID/thread/fork/generation drift, copy/deepcopy/pickle/subclass/forgery/double
use, symlink/hard-link attacks, and every create/append/purge crash seam.
Poisoned writer paths must abort/close; reader paths must close/revoke.
Ordinary terminal issuance validates and then closes/unlinks the reserve,
fsyncs the sessions directory, marks `ordinary_terminal_bound`, and binds the
terminal permit before any terminal byte. Append then requires
`fstatvfs.available >= exact_terminal_frame_bytes`, complete-writes and fsyncs
the journal, validates the end offset, and closes the writer. Test that an
empty session can use the released reserve to reach its terminal; external
consumption before the check writes zero bytes and poisons, consumption after
the check is uncertain/poisoned with no retry, and no branch releases a
second reserve.

Add the exact store-owned, same-lock prewrite `fstatvfs` check:
`available = f_bavail * f_frsize` must be strictly greater than
`67_108_864 + 1_048_652 + candidate_group_frame_bytes` immediately before
the sole CAS. Equality raises static `ExpertPrewriteCapacityError`, writes
zero bytes, and claims no permit. Structural byte overflow remains
`GROUP_CAPACITY_EXCEEDED`; proven disk pressure builds the distinct
`PERSISTENCE_CAPACITY_EXCEEDED` rejection. Test malformed/stat failure,
equality, no-byte/no-state mutation, post-check ENOSPC poison, and the
post-reserve-release bound-frame recheck.

Add one combined emergency transition/API. After a proven no-write prewrite
capacity error and the exact Phase-1 terminal, resolve exactly one unseen
final RAW and build its persistence-capacity rejection group,
candidate state/cursor, and aligned terminal only in private memory; do not
claim durable catch-up or publish. When exact bound group+terminal sizes fit
17,825,868 bytes, sole-CAS issuance validates reserve identity/allocation,
closes and unlinks reserve, fsyncs sessions, marks `emergency_bound`, and
returns one permit binding the parent, both frames, and candidates. Combined
append writes+fsyncs group, then writes+fsyncs terminal, closes writer, and
returns both receipts. Only exact combined-receipt comparison makes catch-up
durable and permits final halted publication. Emergency terminal proves
reserve already consumed and never releases twice. Any transition/append
uncertainty poisons. An uncertain append never retries, truncates, repairs,
resumes, publishes, or terminalizes.

Implement exact ports/capabilities in `inci_tennis_io/ports.py`, physical
ownership in `expert_journal_store.py`, and safe exports in `facade.py`.
Neither port nor receipt exposes a path, descriptor, callback, generic byte
sink, or arbitrary filesystem authority.

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_journal_store -v
```

Expected: PASS with each controlled crash seam repeated at least three times
through pipe-controlled subprocesses and no timing sleeps.

- [ ] **Step 6: Add and implement atomic controller publication**

Build integration RED around the real temporary Phase-1 writer, capture
factory, `EventRuntime`, and `BoundedIngress`. The Task-6 production service
must call `BoundedIngress.drain_one` itself; it cannot accept an arbitrary
caller-constructed live `PersistedEvent`. Instrument:

```text
raw append -> raw fsync -> normalize -> reduce -> group append
-> group fsync -> receipt -> deadline/identity-safe acknowledgement
-> publication
```

For each parent, reduce every wrapper through a private intermediate
synchronization and call the exact Task-5 authority-aware transition
validator. Then
perform one—and only one—group-level CAS immediately before persistence over
the published `ExpertStateV1` digest, complete cursor including
`last_parent_record_sha256`, and journal generation/head. Append/fsync the
whole group, validate the exact receipt, and publish only the final state.
Issuing the one-shot append permit is that sole CAS; group append only
authenticates/consumes the claimed permit and does not repeat a mutable-state
compare. There is no per-wrapper CAS against published state.

The writer committed cursor/head/generation is authoritative and must equal
the controller published cursor; expected state digest must equal the
cursor's state digest. After group fsync the writer becomes receipt-pending
and rejects second permits/terminal. Only exact matching receipt publication
acknowledgement, including a fresh before-deadline sample and identity check,
clears it. The controller keeps the candidate private, acknowledges while
holding its publication lock, then assigns before releasing that lock.
Receipt loss, mismatch, expiry, identity loss, or crash is nonresumable and
publishes nothing. Test every state and prove acknowledgement is not a hidden
second CAS.

Test stale prior-state digest, stale complete cursor/head, two contenders,
failed append, lost durable receipt, crash after fsync/before publication,
diagnostic prefix reconstruction without resume/exactness, and no state
change or duplicate append on any lost race.

Also trigger the actual Phase-1 branch where `_process_node` persists a final
RAW and returns a Phase-1 terminal. Catch-up requires the same-process expert
writer/session to remain live, healthy, unpoisoned, and without an expert
terminal; Phase 1 may already be terminal. Permit exactly one unseen final RAW
before the expert terminal, including the capacity path. Never catch up after
expert terminal, restart, poison, or a second unseen RAW.

For a proven prewrite capacity failure, use only the ruled combined emergency
permit/append path from Step 5. Never expose independent reserved group and
terminal permits, omit/reorder the parent, or release reserve twice.

Add the one narrow `BoundedIngress.close_external_halt(runtime)` Phase-1
bridge from the ruling. It atomically stops admission between drains, selects
an already-won static ingress fault or otherwise `operator_halt`, fails and
wakes every queued producer without persisting it, delegates one exact
runtime terminal outside the ingress lock, and permanently rejects every
later enqueue/drain/close/second-bridge call. Use it after every durably
published ordinary halting group before the ordinary expert terminal, and
after a no-write capacity failure before emergency resolution. Test
concurrent queued producers, admission and producer-timeout races, one exact
Phase-1 terminal, only the current durable RAW as the final unseen parent,
all waiters released, no lock across runtime calls, and no later RAW.

Implement `inci_tennis_runtime/expert_controller.py` as the one serial
compare-append-publish arbiter. It consumes exact expert and I/O facades and
implements no parser, strategy, network, or filesystem behavior.

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_controller \
  tests.tennis_v1.test_ingress -v
```

Expected: PASS with publication only after the exact durable receipt.

- [ ] **Step 7: Add and implement bounded exact replay**

Write RED tests for `begin_expert_replay`,
`replay_expert_parent_group`, and `finish_expert_replay`. The runtime service
issues one `ExpertReplayConstructionAuthorityV1` from the exact bound
authorizer/coordinator with no expert-manifest argument. It derives the
session/Phase-1 anchors and samples the common deadline before any reader
acquisition. Successful issuance retains only validated root descriptor
authority and performs zero Phase-1/companion marker or journal opens/reads;
issuance itself may return a closed deadline denial. After its fresh
authorization/deadline gate, prepare descriptor-relatively authenticates the
companion marker, derives the journal basename and expected manifest digest
from those bytes, and only then lazily opens the companion journal. Prepare
invokes unchanged `replay_exact` internally, lazily opens and exclusively owns
fresh Phase-1 and companion readers only on the strict ready path, and
constructs exact
`EvidenceReplayContextV1` carrying the actual Phase-1
`SessionManifest`, exact `SESSION_START` `PersistedEvent`/digest,
`ReplayResult`, optional terminal anchor, and actual marker/WAL identities.
It creates exact `RetentionReplayAuthorizationV1` with common deadline,
static provenance/identity anchors, sampled authorized wall time, consecutive
authorization sequence, exact operation, and expected RAW parent sequence.

The replay service never imports, receives, retains, or directly calls
`JournalReader` or a companion read capability. Authority-mediated APIs cover
every verified DERIVED skip, RAW read, companion-group read, both terminals,
both physical EOFs, missing/extra cardinality, mechanically invalid companion
scan, missing/torn/corrupt companion manifest frame, any begin-time mismatch
diagnostic short circuit, and abort. Prepare returns an exact ready-vs-
denied union. A bootstrap denial creates no synthetic manifest, identity,
authorization, or accumulator; it returns an authority-built static nonexact
result plus bounded diagnostic proof and closes/revokes immediately. A
begin-time mismatch reads or normalizes no parent/group payload. Before every
underlying read, resample time and revalidate deadline, marker/journal
identity including mtime/ctime, Phase-1 header, exact SESSION_START
manifest/digest, and static anchors without issuing or advancing
authorization. After both RAW and companion group are privately held,
resample and revalidate all four identities/static anchors, then issue exactly
one next `parent_group` authorization consumed by one replay step and
acknowledge the exact returned accumulator. Begin is sequence zero; one
authorization exists per matched group; finish consumes and acknowledges the
final next authorization. No authorization exists for unmatched material,
and no unused/skipped/pre-read authorization exists. Every nonfinished exit
revokes both readers. Pure replay reads no ambient clock.

Every read, authorization issuance, and begin/parent/finish acknowledgement
resamples the same clock and revalidates identities/static authority before
accepting bytes or state. Deadline equality or identity loss moves to a
terminal authority-owned denial, closes/revokes/purges, discards any held pure
result, and exposes only `take_expert_replay_denial`; no later file read is
allowed. Test issue-before/ack-at-deadline and identity replacement at every
seam with barriers. At/after deadline, `RETENTION_DEADLINE_REACHED` outranks
content mismatches that would require forbidden reads.

Implement the ruling's total access-denial map at every read/issue/ack seam:
sample deadline first (item 7); before deadline map analysis/rebind/root/
session/generation/lifecycle loss to item 6, four evidence/companion file
identity loss to item 8, and installed current-environment drift to item 9.
Use no file proof for non-file items 6 and 9 or a deadline-first denial—never
fabricate a presence/absence observation—and the exact affected concrete role
proof for item 8. Cover pairwise collisions and lawful-observability
precedence.

After internal Phase-1 replay/context construction, apply every lawfully
proven item 1..10 before reading a companion byte. In particular, nonexact
Phase-1 replay and an otherwise valid missing/halted Phase-1 terminal must
return items 2 and 3 respectively even when the unread companion bootstrap is
also corrupt. Item 11 is available only when no already-proven item 1..10
outranks it. Add both collision regressions.

`replay_expert_parent_group` does not accept
`recomputed_observations`. It internally invokes sealed
`normalize_expert_parent(accumulator.manifest, parent)`, reduces those exact
observations, and compares storage.

The production replay service invokes the code-owned environment collector
itself before begin; it never forwards a caller-created
`ExpertCurrentEnvironmentV1`.

Cover all 35 reachable mismatch values in literal precedence, including
context, nonexact/unclean evidence, authorization, deadline equality,
identity replacement, session, manifest, environment, retention, schema,
code, runtime, dependency, provider-binding, match-universe, missing/extra/
reordered/wrong-kind/wrong-digest parents, group/chain/payload/state/trace/
terminal mutation, authorization reuse/skip/unused issuance, and long-session
bounded memory.

Implement pure replay in `inci_tennis_expert/replay.py` and authority/iterator
composition through the I/O facade plus
`inci_tennis_runtime/replay_service.py`. Pure replay accepts no `Iterable`,
path, reader, callback, clock, or retention capability. Add tests for every
mediated state edge, wrong/double acknowledgement, outstanding-token read,
one-sided EOF draining with bounded memory, and direct-reader import/call
prohibition. Add an open/read spy proving successful construction-authority
issuance touches zero Phase-1/companion marker or journal entry.
Mechanically invalid or absent companion manifest frames must
reach the same total typed bootstrap-denial path and
`COMPANION_SCAN_INVALID`; missing/halted but mechanically framed terminals
remain terminal mismatch material.

Add only these exact reviewed Task-6 Phase-1 bindings, with static tests
enforcing their ruled owning module:

```text
tennis_v1.codec.canonical_record_sha256
tennis_v1.adapter_contract.load_active_adapter_contract
tennis_v1.events.PersistedEvent
tennis_v1.events.RecordKind
tennis_v1.events.SessionManifest
tennis_v1.fingerprints.code_sha256
tennis_v1.ingress.BoundedIngress
tennis_v1.replay_core.ReplayResult
tennis_v1.replay_core.replay_exact
tennis_v1.retention.RetentionCoordinator
tennis_v1.retention.sample_expert_retention_wall_ns
tennis_v1.sequencer.EventRuntime
tennis_v1.sequencer.ProviderPersistenceAuthorizer
tennis_v1.session.session_manifest_sha256
tennis_v1.wal.JournalReader  # I/O authority only; forbidden in replay_service
```

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_replay \
  tests.tennis_v1.test_replay_core -v
```

Expected: PASS; runtime and the narrow root-request, same-clock sampler,
read-only creation-liveness, and ingress external-halt bridges have exact
reviewed allowlists, and I/O has the explicit reviewed `journal_codec` wire
binding.

- [ ] **Step 8: Add retention, terminal-alignment, and crash matrices**

Test the same exact Phase-1 delete-by, denial and purge at or after the
deadline, immediate companion purge when evidence is missing or replaced,
companion-first controlled deletion, and the explicit non-guarantee when the
unchanged Phase-1 expiry worker deletes evidence first. Evidence presence
requires actual marker/WAL identity plus exact Phase-1 header and
SESSION_START manifest/digest; basename presence is insufficient. Revalidate
the exact open-descriptor `ExpertPhysicalFileIdentityV1` objects before and
after each bounded read, after each RAW/group pair before authorization
issuance, and immediately before finish. Include stable
`mtime_ns`/`ctime_ns` and same-inode same-size mutation of a previously
consumed body byte.

Generate fixed sanitized Phase-1 and companion sessions under
`TemporaryDirectory`. Compute expected parent, manifest, initial/final state,
record, group, trace, and terminal bytes/digests with an independent test-only
implementation at runtime; commit no generated digest, WAL, journal, hex, or
base64 constant. Cover crashes after raw fsync, partial companion write,
group fsync, lost receipt, Phase-1 terminal, same-live-session catch-up, and
companion terminal.

Require aligned clean `operator_stop`/`session_end` terminals for
`evaluation_input_eligible=True`. Missing, halted, torn, corrupt, uncertain,
durable-unacknowledged, replaced, unaligned, at-deadline, or after-deadline
evidence is diagnostic, nonexact, non-evaluable, and strictly nonresumable.
No truncate, repair, completion, adoption, republish, or restart continuation
exists. `research_evaluable` remains literal false.

- [ ] **Step 9: Freeze, independently review, and reseal**

Before freeze, implement every Task-6 canonical enum/dataclass registry entry,
the static normalizer registry/dispatch, the seven persisted schema resources,
and the eighth fallback registry schema resource.
Freeze every Task-6 production file, schema, and focused test. Obtain
independent review of contracts/reducer, codec/store, controller,
retention/replay, schemas/resources, and boundary changes against the complete
Task-6 ruling. Any edit restarts the relevant review.

Only after CLEAN review update:

```text
expert/I/O/runtime AST inventories
eight-schema raw resource inventory
exact runtime Phase-1 import expectations
explicit I/O-to-journal_codec import expectation
```

Post-CLEAN controller work changes seal expectations only. It may not add
production contracts, registry entries, normalizers, schemas, behavior, or
literal generated digest vectors.

- [ ] **Step 10: Run the complete Task-6 gate**

```bash
python -m unittest \
  tests.tennis_v1.test_expert_contracts \
  tests.tennis_v1.test_expert_observation \
  tests.tennis_v1.test_expert_reducer \
  tests.tennis_v1.test_expert_journal_codec \
  tests.tennis_v1.test_expert_journal_store \
  tests.tennis_v1.test_expert_controller \
  tests.tennis_v1.test_expert_replay \
  tests.tennis_v1.test_expert_dependency_boundary \
  tests.tennis_v1.test_retention \
  tests.tennis_v1.test_ingress \
  tests.tennis_v1.test_replay_core \
  tests.tennis_v1.test_legacy_baseline -v
```

Then run:

```bash
python -m unittest discover -s tests/tennis_v1 -v
python tests.py
PYTHONPYCACHEPREFIX=/tmp/inci-task6-pycache python -m compileall -q \
  tennis_v1 inci_tennis_expert inci_tennis_io inci_tennis_runtime \
  inci_tennis_adapters \
  tests/tennis_v1
git diff --check
```

Expected: all focused, full Tennis, and frozen v6 tests pass; compilation and
whitespace checks pass; Phase-1 WAL/event bytes remain unchanged, with only
the separately reviewed opaque root-request/same-clock bridge, read-only
creation-liveness guard, and narrow ingress external-halt control path added
to behavior and source seals; no generated digest/WAL/journal fixture or
Python cache enters the tree; demo/live authority remains absent.

---

### Task 7: Add a qualified-provider runtime and an unregistered Sportradar candidate

**Files:**
- Create: `inci_tennis_adapters/registry.py`
- Create: `inci_tennis_adapters/sportradar_tennis_v3.py`
- Create: `inci_tennis_io/provider_readonly.py`
- Create: `tests/tennis_v1/test_sportradar_tennis_v3.py`
- Create: `tests/tennis_v1/fixtures/sportradar_tennis_timeline_v3.json`
- Create: `tests/tennis_v1/fixtures/sportradar_tennis_summary_v3.json`
- Create: `tools/qualify_sportradar_tennis_v3.py`
- Modify: `tests/tennis_v1/test_adapter_contract.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

```python
class SportradarTennisV3Adapter:
    def normalize_summary(
        self, payload: bytes, *, received_monotonic_ns: int
    ) -> ProviderSnapshot: ...
    def normalize_timeline(
        self,
        payload: bytes,
        *,
        prior: TennisState | None,
        received_monotonic_ns: int,
    ) -> tuple[
        ProviderSnapshot | ProviderPoint | ProviderLifecycle, ...
    ]: ...
```

The adapter emits only exact
`ProviderSnapshot | ProviderPoint | ProviderLifecycle` values; it never
collapses a provider lifecycle transition into an invented point.

- [ ] **Step 1: Write official-shape parser contract tests**

Use sanitized fixtures matching the documented Tennis v3 summary and timeline
fields: stable `sport_event` and competitor IDs, singles type, best-of,
coverage flags, event ID, event time, server, result, score, status, and
correction replacement. Qualification fixtures exercise `START`, `SUSPEND`,
`RESUME`, `WALKOVER`, `RETIREMENT`, `CANCEL`, and
`NATURAL_END_CONFIRMATION`.

- [ ] **Step 2: Write capability-denial tests**

Missing play-by-play coverage, server, timestamps, stable IDs, complete
snapshot, or trustworthy provider revision must produce a qualification
failure. A locally assigned poll number must not satisfy provider revision.

- [ ] **Step 3: Implement strict normalization**

Use `Decimal` and exact integer/string validation. Redact the API key from
exceptions and captured transport metadata. Map only documented event/status
values; unknown values freeze the match and persist the raw payload for
authorized diagnosis.

- [ ] **Step 4: Implement the explicit qualification tool**

The tool accepts `--manifest`, `--binding`, `--duration-seconds`, and
`--output-dir`. It runs only after existing preflight passes, writes evidence
outside Git, never starts a trial, never changes billing, and never edits the
production registry.

- [ ] **Step 5: Keep both production registrations empty**

Add tests asserting the existing Phase-1 adapter registry and the new expert
provider runtime registry are both empty after importing production modules.
The candidate adapter is importable only by the explicit qualification tool;
it cannot be selected by normal runtime startup. Real provider activation is
a later, separately reviewed external checkpoint.

- [ ] **Step 6: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_sportradar_tennis_v3 \
  tests.tennis_v1.test_adapter_contract \
  tests.tennis_v1.test_entitlements -v
```

Expected: PASS with no network calls.

**Manual gate:** Do not run the qualification tool until the operator has
created a trial, reviewed its exact terms, stored credentials outside Git,
and explicitly requested the observation.

---

### Task 8: Add read-only Kalshi WebSocket and REST capture

**Files:**
- Create: `inci_tennis_io/kalshi_readonly.py`
- Create: `inci_tennis_io/account_lock.py`
- Create: `tests/tennis_v1/test_kalshi_readonly.py`
- Create: `tests/tennis_v1/fixtures/kalshi_ws_orderbook_snapshot_v2.json`
- Create: `tests/tennis_v1/fixtures/kalshi_ws_orderbook_delta_v2.json`
- Create: `tests/tennis_v1/fixtures/kalshi_ws_lifecycle_v2.json`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

```python
class KalshiReadOnlyFeed:
    async def frames(
        self, market_tickers: tuple[str, ...]
    ) -> AsyncIterator[CapturedInput]: ...
```

- [ ] **Step 1: Write account-lock and startup-order failures**

Use subprocesses to prove mutual exclusion with the v6
environment/subaccount account lock. Freeze startup ordering as:
account lock -> retention recovery/purge -> entitlement authorization ->
retention arm/evidence WAL/expert journal -> transports. No network call or
state-root mutation may happen before these gates.

- [ ] **Step 2: Pin the WebSocket dependency**

Add `websockets==16.1.1` to both dependency files. Do not add an order SDK.

- [ ] **Step 3: Write signing, subscription, frame, reconnect, and redaction tests**

Mock the socket and clock. Assert the handshake signs only
`GET /trade-api/ws/v2`, subscriptions contain only read-only channels,
sequence gaps force snapshot recovery, and credentials never enter events or
errors. Prove there is never more than one active physical WebSocket, every
connected state has exactly one, and no REST order-book fallback is called.
For every affected existing market, disconnect, reconnect start,
subscription change, and forced resnapshot must synchronously call
`require_book_resnapshot` before another frame is normalized or reduced.
Repeating the call at reconnect is identity-idempotent. A reconnect and a
forced resnapshot, including an in-band resnapshot on the same physical
socket, allocate a strictly newer local book trust epoch. Old/equal-epoch
replacements fail, all deltas are rejected until the newer-epoch complete
snapshot is accepted, and only its exact next delta can advance.

- [ ] **Step 4: Implement strict frame parsing**

Accept current `orderbook_snapshot`, `orderbook_delta`, public trade, and
market-lifecycle frames. Convert fixed-point price and quantity strings to
`Decimal`. Unknown message type or schema freezes the affected stream.

- [ ] **Step 5: Implement physical-connection and local-trust-epoch barriers**

A physical WebSocket connection and `BookState.connection_epoch` are
different concepts. Maintain a single physical-socket authority: never more
than one socket is active, and each connected state has exactly one.
For every affected existing market, call `require_book_resnapshot`
synchronously when detecting disconnect, beginning reconnect, changing the
active subscription set, or forcing a resnapshot, before another frame can be
normalized or reduced. Calling it again at reconnect is required and
idempotent.

Before normalizing a reconnect or forced-resnapshot snapshot, allocate a
strictly newer local book trust epoch; this also applies to an in-band forced
resnapshot on the same physical WebSocket. An affected subscription change
that requires a replacement snapshot uses the same strictly newer local
epoch. Frames captured before the increment retain the old epoch. Reject all
deltas and lifecycle frames for the new epoch until
`apply_book_snapshot` accepts its complete snapshot, and never allow a delta
or lifecycle event to create trust. Close or invalidate an old socket before
opening its replacement. Never overlap physical sockets and never use REST
order-book data to repair a WebSocket gap.

- [ ] **Step 6: Add a static mutation boundary**

AST/string tests reject HTTP verbs `POST`, `PUT`, `PATCH`, `DELETE`, portfolio
order paths, order creation models, and imports from the legacy executor.

- [ ] **Step 7: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_kalshi_readonly \
  tests.tennis_v1.test_market_book \
  tests.tennis_v1.test_expert_dependency_boundary -v
```

Expected: PASS without network access.

---

### Task 9: Compose the shadow-only dual-feed runtime

**Files:**
- Create: `inci_tennis_expert/mailbox.py`
- Create: `inci_tennis_runtime/config.py`
- Create: `inci_tennis_runtime/schemas/research-runtime-config-v1.schema.json`
- Create: `inci_tennis_runtime/bootstrap.py`
- Create: `inci_tennis_runtime/shadow_runtime.py`
- Create: `inci_tennis_runtime/shadow_cli.py`
- Create: `tests/tennis_v1/test_shadow_runtime.py`
- Create: `tests/tennis_v1/test_expert_runtime_config.py`
- Modify: `pyproject.toml`
- Modify: `docs/tennis_v1/README.md`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

```python
def run_shadow_session(
    *,
    bootstrap: RuntimeBootstrapInputs,
    provider_binding: QualifiedProviderBinding,
    match_bindings: tuple[MatchBinding, ...],
    stop_event: threading.Event,
) -> ShadowSessionResult: ...
```

Produces `ShadowSessionResult(session_id, terminal_clean, raw_count,
derived_count, trace_sha256, synchronized_match_count, halt_reason)`.

`RuntimeBootstrapInputs` contains the exact environment/subaccount identity and
prevalidated unopened, one-shot capability objects for account-lock acquire,
retention recovery, entitlement evaluation, retention arm, Phase-1 writer
creation, companion-journal creation, provider transport creation, and Kalshi
transport creation. It cannot contain an already opened writer, Phase-1
`EventRuntime`, journal, connection, or transport. Each exact capability type
has one operation and no generic callable, socket, HTTP client, path override,
or execution authority. The composition creates `OpenedRuntime` only after the
ordered trace succeeds.

`ResearchRuntimeConfigV1` strictly binds production/demo environment identity,
subaccount, credential environment-variable names (never values), external
manifest/artifact paths and digests, clock thresholds, session horizon, and
the four package seals. Its account-lock path is derived through the same
OS-account-safe v6 namespace and cannot be overridden. Demo is rejected for
official research in this plan.

- [ ] **Step 1: Write orchestration ownership and shutdown tests**

Test the mandatory startup trace:

```text
account lock -> evidence and companion recovery/purge -> entitlement
-> retention arm -> evidence WAL -> companion journal -> transports
```

No later step can occur after an earlier failure. Also test one writer owner,
bounded ingress, provider gate expiry, Kalshi
disconnect, score gap, Ctrl-C clean terminal, crash/missing terminal, and
dashboard slowness that cannot block ingestion.

- [ ] **Step 2: Implement the read-only composition root**

Consume only the capabilities held by `RuntimeBootstrapInputs`; the composition
module implements no transport or decision logic. Start the qualified provider
and Kalshi capture producers only after the complete startup trace succeeds,
route each public
payload through the real Phase-1 capture factory, persist through
`BoundedIngress`, and only after durable evidence append reduce and append the
companion expert record. Drain only on the owner thread and publish immutable
expert mailbox snapshots. There is no model, policy, order, or P&L object in
this runtime.

- [ ] **Step 3: Add CLI commands**

Expose:

```text
inci-tennis-shadow --check --config <external-config>
inci-tennis-shadow --shadow --config <external-config>
```

Reject `--demo`, `--live`, `--trade`, and unknown execution-like flags.
With the production registry empty, `--shadow` fails before account lock,
state mutation, journal creation, or network. Synthetic/recorded tests inject
sealed test runtimes; a real command becomes reachable only in the separately
reviewed provider-activation change.

- [ ] **Step 4: Add Phase-A dashboard fields**

Show match ID, players, score, server, provider/book ages, connection epochs,
binding status, synchronization reason, raw/derived counts, trace, WAL
health, and terminal status.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_shadow_runtime \
  tests.tennis_v1.test_ingress \
  tests.tennis_v1.test_sequencer \
  tests.tennis_v1.test_mailbox -v
```

Expected: PASS.

---

### Task 10: Build the point-in-time historical store and pre-match prior

**Files:**
- Create: `inci_tennis_io/historical_store.py`
- Create: `inci_tennis_io/schemas/historical-entitlement-v1.schema.json`
- Create: `inci_tennis_io/schemas/historical-dataset-manifest-v1.schema.json`
- Create: `inci_tennis_expert/prematch_model.py`
- Create: `tests/tennis_v1/test_historical_store.py`
- Create: `tests/tennis_v1/test_prematch_model.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `HistoricalEntitlementArtifact`, `HistoricalDatasetManifest`,
  `HistoricalAccessDecision`, `HistoricalStore`, immutable `HistoricalRow`,
  `FrozenPrematchArtifact`, `PrematchFeatures`, and `PrematchPrior`.
- `PrematchFeatures` contains player IDs, surface, format, as-of timestamp,
  shrunk serve/return point estimates, effective sample sizes, uncertainty,
  source-row digests, and feature-definition digest.
- `PrematchPrior` contains home/away serve-point probabilities, lower/upper
  bounds, support status, training cutoff, and model digest.

```python
def build_features(
    rows: tuple[HistoricalRow, ...],
    *,
    player_home_id: str,
    player_away_id: str,
    surface: str,
    scheduled_start_wall_ns: int,
) -> PrematchFeatures: ...

def authorize_historical_dataset(
    entitlement: HistoricalEntitlementArtifact,
    manifest: HistoricalDatasetManifest,
    *,
    official_window_start_wall_ns: int,
    official_window_end_wall_ns: int,
) -> HistoricalAccessDecision: ...

def estimate_prematch(
    features: PrematchFeatures,
    artifact: FrozenPrematchArtifact,
) -> PrematchPrior: ...
```

- [ ] **Step 1: Write leakage and lineage tests**

Insert matches before and after the target start. Assert post-start rows,
revised-after-start statistics, and future rankings cannot affect features.
Assert every aggregate retains source event IDs and as-of timestamps.
Also deny unknown/expired permission, wrong provider/product/lineage, analysis
or derivative use not granted, retention/publication conflict, dataset digest
drift, or an official window outside the authorized period. Correct hashes
without an eligible entitlement remain unusable.

- [ ] **Step 2: Implement SQLite schema and point-in-time queries**

The I/O package opens or queries the store only after an exact eligible
`HistoricalAccessDecision` bound to the entitlement, dataset manifest,
provider/product/lineage, permitted use, time window, and digests. It uses
separate immutable source tables for matches, points,
rankings, surfaces, and provider lineage. Enable foreign keys, use explicit
transactions, and store canonical digests for imported rows. It returns a
frozen tuple of rows available strictly before the scheduled start; the
deterministic expert model receives rows, never a database/path/connection.

- [ ] **Step 3: Implement conservative empirical-Bayes features**

Estimate surface-specific serve and return point strength using Beta
shrinkage toward tour/surface priors, recency decay, opponent adjustment, and
an effective-sample-size uncertainty measure. Unknown players shrink heavily
instead of receiving a confident neutral estimate.

- [ ] **Step 4: Freeze the pre-match artifact**

Serialize coefficients, priors, feature definitions, training cutoff, source
digest, historical entitlement digest, historical dataset-manifest digest,
the exact eligible window-specific `HistoricalAccessDecision` digest, and
model SHA-256. `estimate_prematch` accepts only a validated frozen artifact
during official evaluation. The I/O loader and runtime require the same
decision object/digest and prove
`authorized_from_wall_ns <= official_window_start_wall_ns <
official_window_end_wall_ns <= authorized_until_wall_ns`.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_historical_store \
  tests.tennis_v1.test_prematch_model -v
```

Expected: PASS.

---

### Task 11: Compute live win probability, calibration, and abstention

**Files:**
- Create: `inci_tennis_expert/win_probability.py`
- Create: `inci_tennis_expert/calibration.py`
- Create: `tests/tennis_v1/test_win_probability.py`
- Create: `tests/tennis_v1/test_calibration.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `PredictionOutcome`, `CalibrationPolicy`, and
  `CalibrationArtifact`.
- `CalibrationArtifact` contains chronological cutoff, stratum definitions,
  sample counts, reliability bins, Brier/log-loss values, interval coverage,
  support thresholds, raw live-model digest, prematch-artifact digest,
  feature-definition digest, training-partition digest, and canonical artifact
  digest.

```python
def live_fair_value(
    state: TennisState, prior: PrematchPrior
) -> FairValueEstimate: ...

def calibrate_chronologically(
    predictions: tuple[PredictionOutcome, ...],
    policy: CalibrationPolicy,
) -> CalibrationArtifact: ...

def apply_calibration(
    raw_estimate: FairValueEstimate,
    artifact: CalibrationArtifact,
    *,
    state: TennisState,
) -> FairValueEstimate: ...
```

- [ ] **Step 1: Write mathematical invariants**

Test symmetry when players are swapped, monotonicity after winning a point,
probability 0/1 at terminal results, deuce and tiebreak recursion, best-of
three/five behavior, and exact repeatability.

- [ ] **Step 2: Implement memoized exact recursion**

Compute game, tiebreak, set, and match probabilities from the current score
and server-specific point probabilities. Do not use Monte Carlo in the
official estimator.

- [ ] **Step 3: Propagate uncertainty conservatively**

Evaluate lower/central/upper serve and return posterior inputs and return an
ordered probability interval. Mark unsupported formats or insufficient
effective sample size as `supported=False`.

- [ ] **Step 4: Implement chronological calibration**

Report Brier score, log loss, reliability bins, selected-action calibration,
support counts, and interval coverage by tour, tier, surface, score state,
side, and data-quality class. Calibration artifacts include the chronological
cutoff and cannot contain test outcomes.

At inference, `apply_calibration` selects only a preregistered supported
stratum, applies the frozen mapping and uncertainty widening, binds the
artifact digest to the returned estimate, and abstains when support is missing
or out of distribution. It rejects any mismatch between the estimate's raw
model/prematch/feature digests and the artifact's bound digests. The official
pipeline cannot pass a raw,
uncalibrated estimate to policy value.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_win_probability \
  tests.tennis_v1.test_calibration -v
```

Expected: PASS.

---

### Task 12: Build versioned fees, complete-policy value, and paired baselines

**Files:**
- Create: `inci_tennis_expert/fee_schedule.py`
- Create: `inci_tennis_expert/policy_value.py`
- Create: `inci_tennis_expert/baselines.py`
- Create: `tests/tennis_v1/test_fee_schedule.py`
- Create: `tests/tennis_v1/test_policy_value.py`
- Create: `tests/tennis_v1/test_baselines.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `FrozenFeeSchedule` and `FrozenPolicyArtifact`.
- Produces `NativeV6BaselinePolicyV1` and
  `PairedV6SignalBaselinePolicyV1`.
- `FrozenPolicyArtifact` contains the feature/model/calibration digests,
  exact `fee_schedule_sha256`,
  outcome buckets and probabilities by frozen stratum, latency scenarios,
  fill/partial-fill model, exit policy, residual policy, minimum conservative
  net threshold, training cutoff, exact two-side `entry_side_order`,
  strictly-positive `entry_quantity`, and `[0, 1]`
  `entry_limit_slippage`. After the independently reviewed
  Task-12 registry extension, `expert_contract_sha256` is its identity; it
  has no self-digest field.

```python
def validate_fair_value_for_opportunity(
    opportunity: OpportunityFrame,
    fair_value: FairValueEstimate,
) -> None: ...

def validate_policy_estimate_for_opportunity(
    opportunity: OpportunityFrame,
    fair_value: FairValueEstimate,
    fee_schedule: FrozenFeeSchedule,
    artifact: FrozenPolicyArtifact,
    estimate: PolicyEstimate,
) -> None: ...

def validate_policy_decision_for_opportunity(
    opportunity: OpportunityFrame,
    fair_value: FairValueEstimate,
    fee_schedule: FrozenFeeSchedule,
    estimate: PolicyEstimate,
    artifact: FrozenPolicyArtifact,
    decision: PolicyDecision,
) -> None: ...

def estimate_policy_value(
    opportunity: OpportunityFrame,
    fair_value: FairValueEstimate,
    fee_schedule: FrozenFeeSchedule,
    artifact: FrozenPolicyArtifact,
) -> PolicyEstimate: ...

def decide(
    opportunity: OpportunityFrame,
    fair_value: FairValueEstimate,
    fee_schedule: FrozenFeeSchedule,
    estimate: PolicyEstimate,
    *,
    artifact: FrozenPolicyArtifact,
) -> PolicyDecision: ...
```

- [ ] **Step 1: Write fee and negative-expectancy tests**

Assert exact ceiling rounding, series-specific fee versions, entry plus exit
fees, and rejection of the v6 pattern where a positive target payoff is
outweighed by stop/gap probability.

Also test all four `player_side_for_contract` orientations and every
opportunity/estimate/decision cross-object mismatch. Decision sequence and
paired time come only from `OpportunityFrame`. Construction, companion
journal acceptance, and replay must call the same pure validators; direct
dataclass construction cannot bypass them.

- [ ] **Step 1a: Freeze and test exact entry derivation**

For each side in `entry_side_order`, use the complementary ladder (NO bids
for a YES buy; YES bids for a NO buy), convert each level to executable ask
price `1 - bid`, set the candidate limit to
`min(1, first_ask + entry_limit_slippage)`, set requested quantity to
`entry_quantity`, and sum depth from `Decimal("0")` priced within that limit
capped at the request. Evaluate the full policy for both executable
candidates. The NO probability interval is exactly
`(1 - YES.upper, 1 - YES.fair, 1 - YES.lower)`. Choose greatest lower
expected net P&L and break an exact tie only by `entry_side_order`. For an
unsupported valuation, choose the first executable side in that order and
return the ruled empty zero-P&L estimate.

Test YES-only depth, NO-only depth, both sides with unequal value, exact
value tie, multi-level depth inside/outside the limit, request-size capping,
unsupported valuation, and direct-construction attempts to change side,
quantity, limit, executable quantity, path filled quantity beyond executable
depth, action, or reason precedence. Run all entry/valuation arithmetic in
the private precision-80 Decimal context and prove estimates, decisions, and
digests remain identical after adversarial changes to the process-global
Decimal context. A supported raw Task-11 fair value without calibration is
structurally valid but Task-12 valuation rejects it; an unsupported fair
value may omit calibration and propagates its exact `MODEL_*` reason.

- [ ] **Step 2: Define the complete outcome vector**

`PolicyEstimate` includes probability and net P&L for no fill, each partial
fill bucket, convergence exit, thesis invalidation, timeout, suspension,
settlement, and residual mark. Probabilities must sum exactly to one within a
frozen Decimal tolerance.

- [ ] **Step 3: Implement conservative authorization**

Return `PAPER_BUY` only when the lower confidence bound of expected net P&L
is strictly greater than `max(Decimal("0"), sealed_threshold)` and the policy
artifact is validated and sealed. Reject a policy artifact whose threshold is
negative. A malformed, unregistered, digest-mismatched, or otherwise
unsealed artifact raises `ExpertContractError("policy_unsealed")` before any
artifact entry field is used; no estimate is returned. A sealed artifact
contains complete path data for each declared stratum. No matching stratum
returns `MODEL_OUT_OF_DISTRIBUTION`; malformed data within a declared
stratum is an invalid artifact. Require the selected contract side's
complementary book ladder and all binding/fair-value/fee/artifact digests
from the Task-1 ruling.

The entry `decide` function has one exact precedence: unsupported estimate
means `ABSTAIN` with its abstention reason; otherwise zero executable
quantity means `INSUFFICIENT_DEPTH`; otherwise a lower bound at or below the
threshold means `EDGE_BELOW_COST`; otherwise return `PAPER_BUY` with
`CONSERVATIVE_VALUE_POSITIVE`. It never returns `PAPER_SELL`. Abstentions
carry no order authority; a buy copies the selected estimate's side,
requested quantity, and limit exactly. `decide` and the decision validator
both first revalidate the estimate against the supplied opportunity, fair
value, fee schedule, and artifact; the validator recomputes action/reason
precedence so direct dataclass construction cannot authorize a buy.

- [ ] **Step 4: Implement paired baselines**

Each baseline consumes every persisted shared `OpportunityFrame` before
expert eligibility/ranking and emits exactly one trade-or-abstain result. The
no-trade baseline always abstains. Freeze a Tennis-native native-v6
diagnostic with every current v6 semantic: 1.5-second observation cadence,
45-second lookback, 7-cent dip, 5-cent take-profit, 6-cent stop, 300-second
timeout, 60-second close buffer, 10-90-cent ask range, maximum 3-cent spread,
maximum three open positions, $30 realized daily-loss gate, 20-contract
projected-net admission check, one-second simulated latency, one-cent adverse
slippage, and $0.01 balance rounding. It never imports or modifies v6.

The paired promotion baseline preserves the exact native-v6 signal and
admission decision—including the original 20-contract projected-net gate—but
normalizes only post-decision execution to the common one-contract quantity,
book, primary latency scenario, fees, and canonical match risk so policy
comparisons share identical opportunities and execution assumptions. Golden
quote schedules cover every native admission/exit gate and explicitly prove
which P&L differences are caused by this declared normalization. The simple
score baseline compares calibrated fair probability with all-in executable
price without the path model.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_fee_schedule \
  tests.tennis_v1.test_policy_value \
  tests.tennis_v1.test_baselines \
  tests.tennis_v1.test_legacy_baseline -v
```

Expected: PASS.

---

### Task 13: Enforce canonical match risk and re-entry discipline

**Files:**
- Create: `inci_tennis_expert/risk.py`
- Create: `tests/tennis_v1/test_risk.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `RiskState`, `RiskRequest`, `FrozenRiskPolicy`,
  `RiskReservation`, `RiskRejection`, and `RiskEvent`.
- `FrozenRiskPolicy` contains official quantity, maximum occupied matches,
  signal-reset rule, score-transition rule, cooldown, per-match attempts and
  loss limit, consecutive-loss rule, session-loss rule, reservation expiry,
  and artifact digest.

```python
def reserve(
    state: RiskState,
    request: RiskRequest,
    policy: FrozenRiskPolicy,
) -> tuple[RiskState, RiskReservation | RiskRejection]: ...

def apply_risk_event(
    state: RiskState, event: RiskEvent, policy: FrozenRiskPolicy
) -> RiskState: ...
```

- [ ] **Step 1: Reproduce every failure from the -$30 session**

Test simultaneous opposing outcome denial, immediate post-stop denial,
history-still-dipped denial until signal reset, max attempts, match loss,
consecutive loss, session loss, three occupied matches, pending-order slot,
partial fill, and unpriceable exposure global halt.

- [ ] **Step 2: Implement a single immutable risk reducer**

All reservations and fills are keyed by canonical match ID. One owner applies
events in WAL order. A reservation contains match, outcome, quantity, maximum
loss, policy digest, decision digest, and expiry.

- [ ] **Step 3: Implement score-state re-entry**

Re-entry requires: no occupied/pending state, prior signal cleared, a strictly
new trusted provider revision, the configured score-state transition, and the
sealed cooldown deadline. A larger continuing dip cannot satisfy reset.

- [ ] **Step 4: Add crash/replay equivalence**

Replay the same risk events after every possible interruption point and assert
the same occupied slots, cooldowns, P&L, halt status, and rejection reason.

- [ ] **Step 5: Run tests**

Run: `python -m unittest tests.tennis_v1.test_risk -v`
Expected: PASS.

---

### Task 14: Simulate bounded-limit IOC and isolated capacity

**Files:**
- Create: `inci_tennis_expert/virtual_liquidity.py`
- Create: `inci_tennis_expert/paper_ioc.py`
- Create: `inci_tennis_expert/scorecards.py`
- Create: `tests/tennis_v1/test_virtual_liquidity.py`
- Create: `tests/tennis_v1/test_paper_ioc.py`
- Create: `tests/tennis_v1/test_scorecards.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `LatencyScenario`, `PendingPaperOrder`, `PaperFill`,
  `VirtualLiquidityLedger`, and `PortfolioScorecard`.
- Exactly one preregistered primary latency scenario controls official entry
  and official P&L. Additional p50/p95/p99 scenarios are labeled sensitivity
  analyses and cannot be selected after outcomes are observed.
- A `PendingPaperOrder` contains scorecard ID, opportunity ID, decision and
  reservation digests, canonical match/outcome, side, quantity, limit,
  decision sequence/time, due monotonic time, and expiry.

```python
def schedule_order(
    decision: PolicyDecision,
    authorization: OfficialOrderAuthorization | CapacityOrderAuthorization,
    latency: LatencyScenario,
    fee_schedule: FrozenFeeSchedule,
) -> PendingPaperOrder: ...

def execute_due(
    order: PendingPaperOrder,
    snapshot: TrustedSnapshot,
    ledger: VirtualLiquidityLedger,
    fee_schedule: FrozenFeeSchedule,
) -> tuple[PaperFill, ...]: ...
```

`OfficialOrderAuthorization` wraps the exact quantity-one
`RiskReservation`. `CapacityOrderAuthorization` is a non-authoritative copy
created only after an official accepted decision; it binds the official
decision/reservation digests, one fixed scorecard ID, and exactly one of
quantities 5/10/20. It can neither reserve risk nor produce official events.
`schedule_order` validates that authorization kind, scorecard ID, and quantity
agree and records the bound fee-schedule digest.

- [ ] **Step 1: Write no-fill, partial, gap, and stale-book tests**

Test latency repricing past the cap, depth smaller than requested size,
multiple levels, fee rounding, market suspension, score thesis invalidation,
and missing future quote.

- [ ] **Step 2: Prevent displayed-depth reuse**

Two simultaneous orders in one scorecard cannot consume the same level.
Repeated identical snapshots do not replenish liquidity. A later snapshot can
replenish only when its book epoch/sequence proves a new observation.

- [ ] **Step 3: Isolate scorecards**

Create independent ledgers and portfolios for quantities 1, 5, 10, and 20.
Only the quantity-1 scorecard emits official decisions and risk events.
Capacity authorizations are derived after the official reservation and
decision have become immutable. Capacity results are labeled counterfactual
and statistically dependent; their failures, fills, and P&L cannot affect the
official reservation, rank, decision, or journal events.

- [ ] **Step 4: Implement exits and residual marks**

Use the same due-order path for entries and exits. At clean termination, value
residuals with executable depth plus exit fee; unpriced residual quantity
makes the scorecard non-evaluable.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_virtual_liquidity \
  tests.tennis_v1.test_paper_ioc \
  tests.tennis_v1.test_scorecards -v
```

Expected: PASS.

---

### Task 15: Rank the observed pool and expose expert diagnostics

**Files:**
- Create: `inci_tennis_expert/ranking.py`
- Create: `inci_tennis_expert/dashboard.py`
- Create: `tests/tennis_v1/test_ranking.py`
- Create: `tests/tennis_v1/test_expert_dashboard.py`
- Modify: `inci_tennis_expert/mailbox.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `RankCandidate`, `FrozenRankingPolicy`, `RankedCandidate`, and
  `ExpertDashboardSnapshot`.
- `FrozenRankingPolicy` contains the value/depth/uncertainty/holding-time/risk
  weights, minimum residence, hysteresis, value-change threshold, top-ten
  limit, deterministic tie-break fields, and artifact digest.

```python
def rank_candidates(
    candidates: tuple[RankCandidate, ...],
    policy: FrozenRankingPolicy,
) -> tuple[RankedCandidate, ...]: ...

def render_snapshot(snapshot: ExpertDashboardSnapshot) -> str: ...
```

- [ ] **Step 1: Write ranking stability tests**

Test conservative value ordering, depth, uncertainty, holding time, risk,
deterministic tie-breaks, minimum residence time, hysteresis, and a top-ten
cap within the actually feed-covered pool.

- [ ] **Step 2: Implement ranking without trade pressure**

Zero or fewer than ten eligible matches is valid. Rank cannot turn an
ineligible candidate into an eligible one and cannot influence model,
execution, or scorecard inputs.

- [ ] **Step 3: Write dashboard golden tests**

Cover waiting, stale, unexplained move, cooldown, eligible, pending,
partial-fill, open, halted, and residual states. Include score/server, feed
ages, book depth, fair interval, policy value, rank, reason, positions, fees,
P&L, WAL, and terminal status.

- [ ] **Step 4: Keep rendering non-blocking**

Dashboard reads immutable overwrite-latest snapshots. Render exceptions
disable rendering and record an operational warning; they cannot block
ingress or alter decisions.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_ranking \
  tests.tennis_v1.test_expert_dashboard \
  tests.tennis_v1.test_mailbox -v
```

Expected: PASS.

---

### Task 16: Compose the expert paper runtime

**Files:**
- Create: `inci_tennis_runtime/paper_runtime.py`
- Create: `tests/tennis_v1/test_expert_paper_runtime.py`
- Modify: `inci_tennis_runtime/shadow_cli.py`
- Modify: `inci_tennis_expert/reducer.py`
- Modify: `inci_tennis_expert/replay.py`
- Modify: `tests/tennis_v1/test_expert_replay.py`
- Modify: `pyproject.toml`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

```python
def run_expert_paper_session(
    *,
    bootstrap: RuntimeBootstrapInputs,
    provider_binding: QualifiedProviderBinding,
    match_bindings: tuple[MatchBinding, ...],
    artifacts: FrozenExpertArtifacts,
    stop_event: threading.Event,
) -> ExpertPaperSessionResult: ...
```

Produces `FrozenExpertArtifacts`, which binds the prematch, calibration,
historical entitlement, historical dataset manifest, policy, fee, risk,
the exact eligible historical-access-decision digest, ranking,
synchronization, and latency artifact digests, and
`ExpertPaperSessionResult`, which reports terminal status, official and
capacity scorecards, replay witness, and halt reason.

- [ ] **Step 1: Write a no-authority-by-default test**

Missing, unsealed, mismatched, expired, or differently cut off artifacts must
prevent session startup before provider payload access. An empty production
provider registry and an ineligible binding remain hard failures. Prove the
same startup trace as Task 9—including companion recovery/purge—and prove that
no transport or journal opens after any earlier failure.

- [ ] **Step 2: Write the deterministic decision pipeline test**

For each trusted snapshot assert this exact order:

```text
persist shared OpportunityFrame
  -> fair value
  -> apply frozen calibration
  -> policy value
  -> eligibility/rank
  -> atomic official risk reservation
  -> official and capacity order scheduling
  -> due-time policy recheck
  -> virtual-liquidity execution
  -> fills/P&L/risk
  -> immutable dashboard snapshot
```

No component may observe a later score or book event while processing an
earlier decision sequence.

- [ ] **Step 3: Implement the single-owner paper composition**

Compose with the Phase-A runtime through `RuntimeBootstrapInputs` without modifying
its raw-first durability path. The runtime package only sequences injected
facades; model, policy, risk, execution, and transports remain implemented in
their sealed owner packages. Model, policy, risk, and execution consume only
persisted and reduced trusted snapshots. Official scorecard state is
authoritative for decisions; capacity scorecards receive copied opportunities
after the official decision and cannot feed back.

- [ ] **Step 4: Add paper-only CLI authority**

Expose:

```text
inci-tennis-paper --config <external-config> --artifacts <external-seal>
```

Require the existing provider preflight, exact artifact digests, and an
explicit paper-research acknowledgment. Reject `--demo`, `--live`, execution
URLs, and any attempt to set official quantity above one.

The production command remains mechanically unreachable while the provider
registries are empty. This plan proves only synthetic/recorded paper behavior;
a real official forward scorecard requires the separately reviewed provider
activation checkpoint.

- [ ] **Step 5: Test shutdown and residual handling**

Ctrl-C creates a clean terminal after pending paper orders cancel. Open paper
positions remain residual unless a genuinely newer executable book permits a
bounded IOC exit; no stale book is reused to fabricate closure.

- [ ] **Step 6: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_expert_paper_runtime \
  tests.tennis_v1.test_risk \
  tests.tennis_v1.test_paper_ioc \
  tests.tennis_v1.test_scorecards -v
```

Expected: PASS.

---

### Task 17: Seal forward scorecards and make the pass/fail decision reproducible

**Files:**
- Create: `inci_tennis_expert/sealed_scorecard.py`
- Create: `inci_tennis_expert/evaluation.py`
- Create: `inci_tennis_expert/evaluation_artifact.py`
- Create: `inci_tennis_io/evaluation_store.py`
- Create: `tools/run_expert_evaluation.py`
- Create: `tests/tennis_v1/test_sealed_scorecard.py`
- Create: `tests/tennis_v1/test_evaluation.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`

**Interfaces:**

- Produces `FrozenFeedCostArtifact`, `FrozenPromotionGate`,
  `SealedScorecard`, `EvaluationResult`, `EvaluationDecision`, and
  `ConcentrationResult`, plus independently sealed
  `ResearchEvaluationArtifactV1`.
- `EvaluationDecision` contains exactly `INSUFFICIENT_EVIDENCE`,
  `REJECT_EDGE`, `CONTINUE_FREE_RESEARCH`, and
  `ELIGIBLE_FOR_FEED_COST_REVIEW`.

```python
def decode_scorecard(
    manifest_bytes: bytes, *, expected_sha256: str
) -> SealedScorecard: ...
def evaluate_scorecard(
    scorecard: SealedScorecard,
    feed_cost: FrozenFeedCostArtifact,
    gate: FrozenPromotionGate,
) -> EvaluationResult: ...
```

The offline tool obtains scorecard, cost, and gate bytes only through
`inci_tennis_io.pinned_artifacts`; deterministic expert functions receive
immutable bytes or validated objects and never a path/file/reader callback.
After evaluating only clean, exact, terminal sessions, it canonicalizes a
`ResearchEvaluationArtifactV1` that binds every input session/evidence/expert
replay digest, analysis code, preregistration, cost/gate artifacts, result,
and creation time. `inci_tennis_io.evaluation_store` writes that artifact as a
new append-free, atomic, fsynced file with its own retention/permission
decision. It is not part of any session companion journal and cannot alter a
terminal session.

- [ ] **Step 1: Write seal and chronological-partition tests**

Assert code/config/model/policy/risk/ranking/latency/fee/binding/provider/data
digests, cutoff timestamps, selected metrics, concentration strata, sample
minimums, stopping rule, selection/multiplicity adjustment, allowed
demonstrated-capacity selection rule, complete feed-cost components, currency,
coverage period, taxes/fees, and artifact digests are immutable. Any mismatch
rejects evaluation before reading provider-derived payloads.

- [ ] **Step 2: Implement paired opportunity accounting**

Every `OpportunityFrame` in the preregistered synchronized universe has one ID
shared by no-trade, v6, simple-score, and expert policies before any
policy-specific value, eligibility, or ranking runs. Every policy must emit
exactly one trade-or-abstain record for every shared ID. Report no fill,
partial fill, fees, realized/unrealized P&L, residuals, and policy reasons
without dropping losing, ineligible, or abstained rows. Evaluation rejects a
missing, extra, duplicated, or policy-conditioned opportunity.

- [ ] **Step 3: Implement conservative metrics**

Report net P&L, paired differences, drawdown, loss streak, calibration,
selection-adjusted confidence bounds, and concentration by player,
tournament, week, tour, surface, side, and data-quality class. Use a seeded,
blocked-by-match bootstrap whose seed and block rule are sealed in the
manifest.

- [ ] **Step 4: Implement explicit outcomes**

`EvaluationResult.decision` is one of
`INSUFFICIENT_EVIDENCE`, `REJECT_EDGE`, `CONTINUE_FREE_RESEARCH`,
or `ELIGIBLE_FOR_FEED_COST_REVIEW`. The last value requires every approved
gate simultaneously: positive net P&L after all modeled costs; a
selection-adjusted conservative lower bound above zero; a paired conservative
lower bound above the strongest baseline; preregistered sample/stopping,
concentration, drawdown, and loss limits; exact two-journal replay; clean
evidence; and every position finalized or conservatively marked. The
preregistered capacity rule selects one of 1/5/10/20 before outcome inspection,
and that capacity's conservative profit must be at least twice the exact
complete feed cost from the bound cost artifact. Any failed evidence gate
returns `REJECT_EDGE` or `INSUFFICIENT_EVIDENCE`; an otherwise credible but
economically insufficient result returns `CONTINUE_FREE_RESEARCH`. No result
enables demo or live execution.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m unittest \
  tests.tennis_v1.test_sealed_scorecard \
  tests.tennis_v1.test_evaluation -v
```

Expected: PASS.

---

### Task 18: Prove the complete system and preserve every safety boundary

**Files:**
- Create: `tests/tennis_v1/test_expert_end_to_end.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`
- Modify: `docs/tennis_v1/README.md`

**Interfaces:**
- Consumes every interface from Tasks 0-17.
- Produces no new production interface.

- [ ] **Step 1: Build one deterministic synthetic match**

Feed a legal provider snapshot and point sequence, exact binding, Kalshi
snapshot/deltas, model artifacts, one eligible decision, one partial/no-fill
capacity branch, one official one-contract fill, convergence exit, clean
terminal, and exact replay.

- [ ] **Step 2: Add adversarial end-to-end variants**

Run variants for score gap, correction, unknown server, stale book,
unexplained move, opposing outcome attempt, immediate re-entry, thin depth,
latency gap, provider expiry, WAL fault, crash, residual inventory, and
dashboard failure.

- [ ] **Step 3: Prove execution mutation is unreachable**

Static and runtime tests assert:

- no `--demo` or `--live`;
- no order mutation URL or HTTP verb in any new package;
- no import of legacy `executor.py`;
- no production adapter registration;
- no credential or real/unsanitized provider payload in committed fixtures;
- official quantity is exactly one;
- capacity scorecards cannot change official decisions.

- [ ] **Step 4: Run the complete regression matrix**

Run:

```bash
python -m unittest discover -s tests/tennis_v1 -t . -v
python tests.py
python -m compileall -q tennis_v1 inci_tennis_expert inci_tennis_io \
  inci_tennis_adapters inci_tennis_runtime tests/tennis_v1 tools
git diff --check
```

Expected:

- every Tennis v1 test passes;
- every v6 test passes;
- compilation succeeds;
- no whitespace errors;
- nothing is staged or committed.

- [ ] **Step 5: Update operator documentation**

Document external secrets/artifacts, Phase-A shadow commands, reason codes,
clean shutdown, replay/evaluation commands, quota honesty, one-contract
official scoring, capacity labeling, provider-expiry behavior, and the
continued absence of demo/live execution.

## Execution Order and Review Gates

Tasks are sequential where their interfaces depend on prior work:

```text
0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9
9 -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18
```

Tasks execute sequentially because every task extends the same exact package
inventory seal. Production and focused-test implementation is reviewed first;
only then may the controller mechanically update the exact approved
inventory/digests and run the boundary test. A production or focused-test
change after review invalidates both the review and reseal. Every task
receives:

1. requirements review against this plan;
2. TDD implementation;
3. focused test verification;
4. independent code-quality review;
5. controller-owned exact reseal and boundary verification;
6. changed-file and residual-risk handoff.

No real provider capture occurs until the external activation gate passes.
No official paper scorecard begins until Phases A-C replay exactly and all
artifacts are frozen. No demo or live work is included.
