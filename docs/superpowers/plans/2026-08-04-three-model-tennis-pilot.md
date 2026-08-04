# Inci Three-Model Tennis Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic research-only runner that compares a static
tennis probability baseline, a per-point dynamic Bayesian model, and a
fee-aware 300-second optimal-stopping shadow policy on identical causal data.

**Architecture:** Normalize every eligible atomic point into one immutable
event and feed it to Models 1 and 2. Model 2 integrates a three-state posterior
for each player through the existing exact best-of-three scoring recursion.
Model 3 consumes Model 2 plus a synchronized executable book and a frozen
empirical markout kernel; without that kernel it returns a typed unsupported
result. A separate runner persists byte-replayable comparison rows and a
chronological evaluator labels them later.

Version 1 makes decisions only on exact `ConsensusL2ResearchFrameV1` parents.
Shadow actions are delayed by a frozen execution scenario and can fill only on
a later gap-free full-L2 observation at or after matching-engine arrival.

**Tech Stack:** CPython 3.14.5, standard library only, immutable dataclasses,
`Decimal`, `unittest`, existing `inci_tennis_expert` score/book/fee contracts,
canonical JSONL, and SHA-256 artifact binding.

## Global Constraints

- Authority is offline replay and read-only shadow research only.
- Do not add an order-capable transport, order body, credential, or network
  call to the pilot modules.
- Do not add NumPy, SciPy, pandas, scikit-learn, or `hmmlearn`; the current
  project dependencies remain unchanged.
- Support only
  `MatchFormat.STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS` in Version 1.
- Consume only exact one-point transitions with known server and winner.
- Persist duplicate, correction, chronology, and book failures as typed
  abstentions; never approximate them.
- Train, validate, and test chronologically with match-level partitioning.
- Reject any frame whose binding, player/market orientation, consensus epoch,
  direct-successor book sequence, capture ordering, or freshness is not exact.
- Never fill a shadow action from the L2 snapshot used to make that action.
- Freeze every parameter and artifact digest before opening the future test
  partition.
- Model 3 must return `UNSUPPORTED_NO_MARKOUT_KERNEL` until a qualified frozen
  empirical kernel is supplied.
- A successful plumbing run is not evidence of profitability.
- Preserve the empty production-provider registry and the existing
  `PAPER_MODEL_NOT_PROMOTED` behavior.
- Follow test-driven development and commit after every task.

---

## File Structure

Create the following focused modules:

```text
inci_tennis_expert/
├── pilot_contracts.py          # shared immutable pilot values and digests
├── pilot_frame_adapter.py      # exact consensus/L2/binding projection
├── pilot_static_model.py       # Model 1 exact static baseline
├── pilot_dynamic_model.py      # Model 2 filter and posterior integration
├── pilot_training.py           # chronological pure-Python artifact fitting
├── pilot_markout.py            # causal 300-second labels and frozen kernel
├── pilot_optimal_stopping.py   # Model 3 Bellman solver
├── pilot_immediate_baseline.py # existing enter-now control policy
├── pilot_runner.py             # pure three-model event reduction
└── pilot_evaluator.py          # labels, metrics, and report projection

inci_tennis_runtime/
└── three_model_pilot_cli.py    # filesystem composition; no network/orders

tests/tennis_v1/
├── test_pilot_contracts.py
├── test_pilot_frame_adapter.py
├── test_pilot_static_model.py
├── test_pilot_dynamic_model.py
├── test_pilot_training.py
├── test_pilot_markout.py
├── test_pilot_optimal_stopping.py
├── test_pilot_immediate_baseline.py
├── test_pilot_runner.py
└── test_pilot_evaluator.py
```

Do not modify the empty package `__init__.py` files. Import concrete modules
directly, matching the repository's current pattern.

---

### Task 1: Define the canonical pilot contracts

**Files:**
- Create: `inci_tennis_expert/pilot_contracts.py`
- Create: `tests/tennis_v1/test_pilot_contracts.py`

**Interfaces:**
- Consumes: `TennisState` and `PlayerSide` from
  `inci_tennis_expert.contracts`.
- Produces: `PilotPointEvent`, `ServeStrengthArtifact`,
  `PilotOutcomeEstimate`, `DynamicBeliefSnapshot`, `PilotBookSnapshot`,
  `PilotExecutionScenario`, `PilotPolicyEstimate`,
  `PilotImmediateBaselineEstimate`, `PilotComparisonRow`, and canonical digest
  helpers.

- [ ] **Step 1: Write failing contract tests**

```python
class PilotPointEventTests(unittest.TestCase):
    def test_requires_one_legal_point_and_two_independent_lineages(self) -> None:
        with self.assertRaisesRegex(PilotContractError, "^point_transition$"):
            PilotPointEvent(
                canonical_match_id="match-a",
                point_id="point-1",
                sequence_number=1,
                before_state=_state(points_home=ScoreValue.LOVE),
                after_state=_state(points_home=ScoreValue.THIRTY),
                server=PlayerSide.HOME,
                winner=PlayerSide.HOME,
                consensus_epoch=0,
                consensus_transition_sha256="a" * 64,
                supporting_source_lineage_sha256s=("b" * 64, "c" * 64),
                received_wall_ns=2_000,
                accepted_monotonic_ns=3_000,
            )

    def test_canonical_digest_is_stable(self) -> None:
        event = _valid_event()
        self.assertEqual(pilot_contract_sha256(event), pilot_contract_sha256(event))
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_contracts -v
```

Expected: import failure for `inci_tennis_expert.pilot_contracts`.

- [ ] **Step 3: Implement exact immutable values**

Use these public shapes:

```python
class PilotSupportReason(str, Enum):
    DUPLICATE_POINT = "duplicate_point"
    INVALID_POINT_TRANSITION = "invalid_point_transition"
    SCORE_CORRECTED = "score_corrected"
    CONSENSUS_EPOCH_CHANGED = "consensus_epoch_changed"
    BOOK_UNTRUSTED = "book_untrusted"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    UNSUPPORTED_NO_MARKOUT_KERNEL = "unsupported_no_markout_kernel"


@dataclass(frozen=True, slots=True)
class PilotPointEvent:
    canonical_match_id: str
    point_id: str
    sequence_number: int
    before_state: TennisState
    after_state: TennisState
    server: PlayerSide
    winner: PlayerSide
    consensus_epoch: int
    consensus_transition_sha256: str
    supporting_source_lineage_sha256s: tuple[str, ...]
    received_wall_ns: int
    accepted_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class ServeStrengthArtifact:
    version: str
    artifact_sha256: str
    target_canonical_match_id: str
    target_scheduled_start_wall_ns: int
    cutoff_wall_ns: int
    training_match_ids: tuple[str, ...]
    training_match_ids_sha256: str
    source_data_sha256: str
    feature_definition_sha256: str
    code_sha256: str
    home_serve_point_probability: Decimal
    away_serve_point_probability: Decimal


@dataclass(frozen=True, slots=True)
class PilotOutcomeEstimate:
    model_version: str
    supported: bool
    home_next_point_probability: Decimal | None
    home_current_set_probability: Decimal | None
    home_match_probability: Decimal | None
    lower_home_match_probability: Decimal | None
    upper_home_match_probability: Decimal | None
    abstention_reason: PilotSupportReason | None


@dataclass(frozen=True, slots=True)
class PilotExecutionScenario:
    version: str
    artifact_sha256: str
    decision_to_arrival_ns: int
    maximum_pair_latency_ns: int
    flat_wait_horizon_ns: int
    holding_horizon_ns: int


@dataclass(frozen=True, slots=True)
class PilotImmediateBaselineEstimate:
    supported: bool
    action: PilotImmediateAction
    abstention_reason: PilotSupportReason | None
    point_event_sha256: str
    selected_player_side: PlayerSide | None
    selected_market_ticker: str | None
    selected_market_id: str | None
    selected_contract_side: ContractSide | None
    decision_book_sha256: str | None
    requested_quantity: Decimal
    decision_monotonic_ns: int
    arrival_due_monotonic_ns: int | None
```

Add equally strict frozen dataclasses for the belief, book, policy, and
comparison row. Use `expert_contract_sha256`-compatible canonical projections
or an isolated canonical JSON encoder with sorted keys, ASCII output, no NaN,
and `Decimal` serialized as fixed-point text.

`PilotBookSnapshot` must expose its bound contract's ordered `bid_levels`
and `ask_levels`, `captured_monotonic_ns`, source frame ID, sequence/generation,
consensus epoch, accepted-score digest, match/binding digests, contract/player
orientation, and trust/staleness state. Construct it only from the relevant
market inside a gap-free `ConsensusL2ResearchFrameV1`; do not flatten the
two-market frame or infer missing depth.

`PilotDecisionFrame` carries both authorized HOME-YES and AWAY-YES books.
`PilotPolicyEstimate` and `PilotImmediateBaselineEstimate` carry an exact
selected player side, market ticker/ID, and contract side whenever they choose
BUY or SELL; WAIT/ABSTAIN carries no route. A position remains locked to its
entry route. Route choice is made from the two current books and model values,
not injected as an unbound `selected_book` by the caller.

The artifact constructors must recompute their own SHA-256 values and reject a
cutoff at or after the target match's scheduled start. The target match must
not appear in the explicit training IDs bound by their digest. The execution scenario is
frozen and digest-bound like a model artifact; `holding_horizon_ns` is exactly
`300_000_000_000` in Version 1.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run:

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_contracts -v
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add inci_tennis_expert/pilot_contracts.py tests/tennis_v1/test_pilot_contracts.py
git commit -m "feat: add three-model pilot contracts"
```

---

### Task 1b: Project only causally bound point/L2 decision frames

**Files:**
- Create: `inci_tennis_expert/pilot_frame_adapter.py`
- Create: `tests/tennis_v1/test_pilot_frame_adapter.py`

**Interfaces:**
- Consumes: prior accepted `TennisState`, `ConsensusL2ResearchFrameV1`, the
  exact `MatchBinding` and `BindingMetadata`, expected consensus epoch, and a
  frozen `PilotExecutionScenario`.
- Produces: one `PilotDecisionFrame` containing the atomic `PilotPointEvent`
  and the two correctly oriented `PilotBookSnapshot` values.
- Has no overload that accepts a standalone or caller-assembled book.

- [ ] **Step 1: Write failing parent, orientation, and freshness tests**

```python
class PilotFrameAdapterTests(unittest.TestCase):
    def test_projects_exact_atomic_point_and_direct_successor_book(self) -> None:
        actual = build_pilot_decision_frame(
            prior_state=_before_point(),
            frame=_valid_consensus_l2_frame(),
            binding=_binding(),
            metadata=_binding_metadata(),
            expected_consensus_epoch=4,
            execution_scenario=_execution_scenario(),
        )
        self.assertEqual(actual.point_event.winner, PlayerSide.HOME)
        self.assertEqual(actual.home_book.player_side, PlayerSide.HOME)
        self.assertEqual(actual.home_book.source_frame_id, actual.source_frame_id)

    def test_swapped_market_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotFrameAdapterError, "^market_orientation$"):
            build_pilot_decision_frame(metadata=_swapped_metadata(), **_valid_args())

    def test_stale_pair_is_rejected_even_when_frame_contract_is_valid(self) -> None:
        with self.assertRaisesRegex(PilotFrameAdapterError, "^pair_stale$"):
            build_pilot_decision_frame(
                execution_scenario=_execution_scenario(maximum_pair_latency_ns=10),
                **_valid_args_without_scenario(),
            )
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_frame_adapter -v
```

Expected: import failure for `pilot_frame_adapter`.

- [ ] **Step 3: Implement the fail-closed adapter**

Accept the frame only when all of these are true:

1. the concrete parent is `ConsensusL2ResearchFrameV1`, whose constructor has
   already bound the exact canonical match, accepted-score digest, direct
   durable sequence successor, connection generation, subscription, and book
   capture ordering;
2. `MatchBinding`, `BindingMetadata`, transition, prior state, and frame agree
   on canonical match, scheduled start, match format, provider orientation,
   home/away market tickers, market IDs, and artifact digests;
3. the consensus and correction epochs equal the expected prior epoch;
4. enumerating the two legal next-point outcomes from `prior_state` yields
   exactly one state equal to `frame.consensus_transition.accepted_state`;
5. the paired-book delay is nonnegative and no greater than
   `maximum_pair_latency_ns`; and
6. YES bid/ask ladders are projected from the correct home or away market with
   exact `Decimal` arithmetic and no invented depth.

Persist every parent digest and clock in the projected decision frame. A
multi-point jump, duplicate, correction, ambiguous winner, unbound market, or
stale frame returns a typed abstention and never reaches a model.

- [ ] **Step 4: Run adapter and existing barrier tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_frame_adapter \
  tests.tennis_v1.test_consensus_l2_research \
  tests.tennis_v1.test_dependency_boundary -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 1b**

```bash
git add \
  inci_tennis_expert/pilot_frame_adapter.py \
  tests/tennis_v1/test_pilot_frame_adapter.py
git commit -m "feat: bind pilot points to causal L2 frames"
```

---

### Task 2: Implement Model 1, the static exact baseline

**Files:**
- Create: `inci_tennis_expert/pilot_static_model.py`
- Create: `tests/tennis_v1/test_pilot_static_model.py`

**Interfaces:**
- Consumes: `PilotPointEvent`, `ServeStrengthArtifact`.
- Produces:
  `evaluate_static_outcome(event, artifact) -> PilotOutcomeEstimate` for the
  event's `after_state`.
- Reuses: `standard_bo3_live_probabilities` from
  `inci_tennis_expert.win_probability`.

- [ ] **Step 1: Write failing equivalence and failure tests**

```python
class StaticPilotModelTests(unittest.TestCase):
    def test_matches_existing_exact_live_probability(self) -> None:
        state = _live_state()
        artifact = _serve_artifact(Decimal("0.64"), Decimal("0.61"))
        expected = standard_bo3_live_probabilities(
            state,
            Decimal("0.64"),
            Decimal("0.61"),
        )

        actual = evaluate_static_outcome(_point_event(after_state=state), artifact)

        self.assertTrue(actual.supported)
        self.assertEqual(actual.home_match_probability, expected.home_match_probability)
        self.assertEqual(
            actual.home_current_set_probability,
            expected.home_current_set_probability,
        )
        expected_next_point = (
            Decimal("0.64")
            if state.server_for_next_point is PlayerSide.HOME
            else Decimal("1") - Decimal("0.61")
        )
        self.assertEqual(actual.home_next_point_probability, expected_next_point)

    def test_rejects_artifact_not_frozen_before_target_match(self) -> None:
        actual = evaluate_static_outcome(
            _point_event(canonical_match_id="match-a"),
            _serve_artifact(
                target_canonical_match_id="match-a",
                target_scheduled_start_wall_ns=2_000,
                cutoff_wall_ns=2_000,
            ),
        )
        self.assertFalse(actual.supported)
        self.assertEqual(actual.abstention_reason, PilotSupportReason.ARTIFACT_MISMATCH)
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_static_model -v
```

Expected: import failure for `pilot_static_model`.

- [ ] **Step 3: Implement the narrow wrapper**

```python
def evaluate_static_outcome(
    event: PilotPointEvent,
    artifact: ServeStrengthArtifact,
) -> PilotOutcomeEstimate:
    if not _artifact_precedes_event(artifact, event):
        return unsupported_static(PilotSupportReason.ARTIFACT_MISMATCH)
    state = event.after_state
    value = standard_bo3_live_probabilities(
        state,
        artifact.home_serve_point_probability,
        artifact.away_serve_point_probability,
    )
    home_next_point_probability = (
        artifact.home_serve_point_probability
        if state.server_for_next_point is PlayerSide.HOME
        else Decimal("1") - artifact.away_serve_point_probability
    )
    return PilotOutcomeEstimate(
        model_version="pilot-static-v1",
        supported=True,
        home_next_point_probability=home_next_point_probability,
        home_current_set_probability=value.home_current_set_probability,
        home_match_probability=value.home_match_probability,
        lower_home_match_probability=value.home_match_probability,
        upper_home_match_probability=value.home_match_probability,
        abstention_reason=None,
    )
```

Convert `WinProbabilityError` into a typed unsupported estimate; do not weaken
the scoring calculator's validation. `_artifact_precedes_event` must also
verify target match and scheduled-start identity, recomputed artifact digest,
`cutoff_wall_ns < target_scheduled_start_wall_ns`, and exclusion of the target
match from `training_match_ids`; comparing only with the latest live receipt is
not sufficient.

- [ ] **Step 4: Run focused tests and existing probability tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_static_model \
  tests.tennis_v1.test_win_probability -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add inci_tennis_expert/pilot_static_model.py tests/tennis_v1/test_pilot_static_model.py
git commit -m "feat: add static tennis pilot baseline"
```

---

### Task 3: Implement Model 2's per-point Bayesian filter

**Files:**
- Create: `inci_tennis_expert/pilot_dynamic_model.py`
- Create: `tests/tennis_v1/test_pilot_dynamic_model.py`

**Interfaces:**
- Consumes: `PilotPointEvent`, `ServeStrengthArtifact`, and a frozen
  `DynamicPointArtifact`.
- Produces: functional
  `DynamicPointModel.observe(event) -> (next_model, belief)` and
  `DynamicPointModel.evaluate(event) -> PilotOutcomeEstimate`.

- [ ] **Step 1: Write failing posterior tests**

```python
class DynamicPointModelTests(unittest.TestCase):
    def test_server_win_moves_home_mass_upward(self) -> None:
        model = _model()
        before = model.belief
        next_model, after = model.observe(
            _point(server=PlayerSide.HOME, winner=PlayerSide.HOME)
        )
        self.assertGreater(after.home_weights[2], before.home_weights[2])
        self.assertEqual(after.away_weights, before.away_weights)
        self.assertEqual(sum(after.home_weights), Decimal("1"))
        self.assertEqual(model.belief, before)
        self.assertEqual(next_model.belief, after)

    def test_duplicate_point_is_rejected_without_state_change(self) -> None:
        model = _model()
        event = _point(point_id="point-1")
        model, _ = model.observe(event)
        frozen = model.belief
        with self.assertRaisesRegex(DynamicPointModelError, "^duplicate_point$"):
            model.observe(event)
        self.assertEqual(model.belief, frozen)

    def test_zero_offsets_equal_static_model(self) -> None:
        model = _model(offsets=(Decimal("0"),) * 3)
        event = _point(after_state=_live_state())
        self.assertEqual(
            model.evaluate(event).home_match_probability,
            evaluate_static_outcome(event, _serve_artifact()).home_match_probability,
        )
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_dynamic_model -v
```

Expected: import failure for `pilot_dynamic_model`.

- [ ] **Step 3: Implement parameters and log-space forward updates**

```python
class EffectivenessState(str, Enum):
    BELOW_BASELINE = "below_baseline"
    BASELINE = "baseline"
    ABOVE_BASELINE = "above_baseline"


@dataclass(frozen=True, slots=True)
class DynamicParameterCandidate:
    transition_matrix: tuple[
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
        tuple[Decimal, Decimal, Decimal],
    ]
    home_initial_weights: tuple[Decimal, Decimal, Decimal]
    away_initial_weights: tuple[Decimal, Decimal, Decimal]
    logit_offsets: tuple[Decimal, Decimal, Decimal]


@dataclass(frozen=True, slots=True)
class DynamicPointArtifact:
    version: str
    artifact_sha256: str
    target_canonical_match_id: str
    target_scheduled_start_wall_ns: int
    cutoff_wall_ns: int
    training_match_ids: tuple[str, ...]
    validation_match_ids: tuple[str, ...]
    source_data_sha256: str
    feature_definition_sha256: str
    code_sha256: str
    selected: DynamicParameterCandidate


@dataclass(frozen=True, slots=True)
class DynamicPointModel:
    def observe(
        self,
        event: PilotPointEvent,
    ) -> tuple[DynamicPointModel, DynamicBeliefSnapshot]:
        """Return a new model after one server transition and emission."""

    def evaluate(self, event: PilotPointEvent) -> PilotOutcomeEstimate:
        """Integrate all 3 x 3 latent-state pairs through exact scoring."""
```

For a server with baseline probability `p0`, calculate state probability with
`sigmoid(logit(p0) + offset)`. Apply the transition matrix before the emission,
normalize in log space, quantize the persisted weights, and verify the sum is
exactly one after deterministic residual allocation.

- [ ] **Step 4: Integrate all nine state pairs**

```python
for home_index, home_weight in enumerate(home_weights):
    for away_index, away_weight in enumerate(away_weights):
        value = standard_bo3_live_probabilities(
            state,
            home_state_probabilities[home_index],
            away_state_probabilities[away_index],
        )
        joint = home_weight * away_weight
        match_probability += joint * value.home_match_probability
        set_probability += joint * value.home_current_set_probability
```

Persist the minimum and maximum state-pair match probabilities as pilot bounds;
do not describe them as calibrated confidence intervals. Also persist the
home player's predictive probability for the *next* point: posterior-weighted
home serve probability when HOME serves and one minus the posterior-weighted
away serve probability when AWAY serves. This value is computed after the
current observation and before the next observation.

- [ ] **Step 5: Run focused and shared-model tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_dynamic_model \
  tests.tennis_v1.test_pilot_static_model \
  tests.tennis_v1.test_win_probability -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add inci_tennis_expert/pilot_dynamic_model.py tests/tennis_v1/test_pilot_dynamic_model.py
git commit -m "feat: add dynamic point effectiveness model"
```

---

### Task 4: Fit and freeze Model 2 without leakage

**Files:**
- Create: `inci_tennis_expert/pilot_training.py`
- Create: `tests/tennis_v1/test_pilot_training.py`

**Interfaces:**
- Consumes: chronological tuples of `PilotPointEvent`, a complete tuple of
  causally prior target-bound `ServeStrengthArtifact` values (one per
  train/validation match), and an explicit tuple of
  `DynamicParameterCandidate` values.
- Produces:
  `fit_dynamic_point_parameters -> DynamicTrainingResult`, followed by a
  target-bound `DynamicPointArtifact`, and canonical artifact JSON bytes.

- [ ] **Step 1: Write failing chronology and deterministic-selection tests**

```python
class PilotTrainingTests(unittest.TestCase):
    def test_unpartitioned_future_match_is_rejected_before_scoring(self) -> None:
        with self.assertRaisesRegex(PilotTrainingError, "^partition_coverage$"):
            fit_dynamic_point_parameters(
                events=(_old_match_event(), _future_match_event()),
                training_match_ids=("old-match",),
                validation_match_ids=("validation-match",),
                candidates=_candidate_grid(),
                serve_strength_artifacts=_serve_artifacts(),
            )

    def test_training_partition_must_precede_validation_partition(self) -> None:
        with self.assertRaisesRegex(PilotTrainingError, "^partition_chronology$"):
            fit_dynamic_point_parameters(**_validation_before_training_fixture())

    def test_equal_scores_choose_lexicographically_first_candidate(self) -> None:
        first, second = _equal_score_candidates()
        actual = fit_dynamic_point_parameters(
            events=_events(),
            training_match_ids=("match-a",),
            validation_match_ids=("match-b",),
            candidates=(second, first),
            serve_strength_artifacts=_serve_artifacts(),
        )
        self.assertEqual(actual.selected_candidate, first)
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_training -v
```

Expected: import failure for `pilot_training`.

- [ ] **Step 3: Implement a bounded standard-library grid search**

```python
def fit_dynamic_point_parameters(
    *,
    events: tuple[PilotPointEvent, ...],
    training_match_ids: tuple[str, ...],
    validation_match_ids: tuple[str, ...],
    candidates: tuple[DynamicParameterCandidate, ...],
    serve_strength_artifacts: tuple[ServeStrengthArtifact, ...],
) -> DynamicTrainingResult:
    ordered = _validate_partitioned_chronology(
        events,
        training_match_ids,
        validation_match_ids,
    )
    scored = tuple(
        _score_candidate(
            candidate,
            ordered,
            training_match_ids,
            validation_match_ids,
            serve_strength_artifacts,
        )
        for candidate in candidates
    )
    return min(scored, key=lambda row: (row.validation_log_loss, row.canonical_key))
```

Reset beliefs at each match boundary. Score each point using the predictive
probability before applying that point's observation. Store match partitions,
row counts, log loss, Brier score, source digests, and code fingerprint in the
training result.

The validator requires disjoint nonempty match partitions; exact coverage of
every supplied event by one of those partitions; strict point sequence within
each match; and every training match's scheduled start before every validation
match's scheduled start. It must not accept a future-test match and filter it
out later. Candidate scoring receives already-separated immutable train and
validation tuples, not the original event collection. It looks up the exact
target-bound serve artifact for each match and rejects missing, duplicate,
post-start, or training-self-including priors before candidate scoring.

After selection, `freeze_dynamic_point_artifact` binds the selected candidate,
training/validation IDs and their canonical source digest, training cutoff,
feature-definition digest, code fingerprint, and a target future match whose
scheduled start is strictly after the cutoff. The target match is absent from
both fitted partitions. The future target's points are never passed to either
fitting function.

- [ ] **Step 4: Run focused tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_training \
  tests.tennis_v1.test_pilot_dynamic_model -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add inci_tennis_expert/pilot_training.py tests/tennis_v1/test_pilot_training.py
git commit -m "feat: add causal dynamic model fitting"
```

---

### Task 5: Build causal 300-second markout labels and kernel artifacts

**Files:**
- Create: `inci_tennis_expert/pilot_markout.py`
- Create: `tests/tennis_v1/test_pilot_markout.py`

**Interfaces:**
- Consumes: chronological `PilotDecisionFrame` values, every intervening
  gap-free full-L2 observation, `PilotExecutionScenario`, fee function, and
  correction/censor events.
- Produces: action-independent `PilotFlatTransitionPath` values,
  counterfactual `PilotHoldingTransitionPath` values, terminal
  `PilotMarkoutLabel` values, and `FrozenMarkoutKernel`.
- Reuses: `PriceLevel`, `size_ioc_entry`, and `assess_exit` from
  `inci_tennis_expert.five_minute_path`.

For every eligible decision frame and each authorized HOME-YES/AWAY-YES route
—not only frames or routes selected by an old or new policy—create a FLAT
sample to the next eligible decision frame, flat-wait deadline, or censor.
Separately, simulate a counterfactual IOC at the first valid L2 observation at
or after
`decision_book.captured_monotonic_ns + decision_to_arrival_ns`. If it fills,
create a HOLDING path from the resulting `PaperPosition`; if it does not fill,
persist a typed no-fill sample. This prevents policy-selection bias in the
kernel.

The flat-wait clock begins at the decision frame. The independent 300-second
holding clock begins only at `PaperPosition.opened_monotonic_ns`. Waiting while
flat therefore consumes `flat_wait_horizon_ns` but does not consume a holding
horizon that does not yet exist.

- [ ] **Step 1: Write failing causality and censoring tests**

```python
class PilotMarkoutTests(unittest.TestCase):
    def test_decision_book_cannot_fill_its_own_action(self) -> None:
        sample = build_counterfactual_entry_path(
            decision=_decision_frame(at_ns=1_000),
            books=(
                _book(at_ns=1_000),
                _book(at_ns=1_099),
                _book(at_ns=1_100),
            ),
            execution_scenario=_execution_scenario(decision_to_arrival_ns=100),
            fee=_fee,
        )
        self.assertEqual(sample.arrival_book_monotonic_ns, 1_100)
        self.assertEqual(sample.position.opened_monotonic_ns, 1_100)

    def test_label_uses_first_gap_free_book_at_or_after_deadline(self) -> None:
        label = label_markout_path(
            frame=_frame(at_ns=1_000),
            position=_position(opened_monotonic_ns=1_000),
            books=(_book(at_ns=299_000_000_000), _book(at_ns=300_000_001_000)),
            corrections=(),
            fee=_fee,
        )
        self.assertEqual(label.deadline_book_monotonic_ns, 300_000_001_000)

    def test_score_correction_censors_entire_path(self) -> None:
        label = label_markout_path(
            frame=_frame(),
            position=_position(opened_monotonic_ns=1_000),
            books=_books(),
            corrections=(_correction_before_deadline(),),
            fee=_fee,
        )
        self.assertEqual(label.censor_reason, "score_corrected")
        self.assertIsNone(label.net_pnl)

    def test_flat_transition_exists_without_a_filled_position(self) -> None:
        path = build_flat_transition_path(_two_decision_frames(), _books(), ())
        self.assertEqual(path.position_state, PositionState.FLAT)
        self.assertGreater(path.steps[0].elapsed_ns, 0)
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_markout -v
```

Expected: import failure for `pilot_markout`.

- [ ] **Step 3: Implement action-independent one-step path records**

```python
@dataclass(frozen=True, slots=True)
class KernelTransitionV1:
    source_state_key: str
    position_state: PositionState
    elapsed_ns: int
    next_state_key: str | None
    probability: Decimal
    entry_fill_fraction: Decimal
    entry_price_delta: Decimal | None
    executable_exit_pnl_delta: Decimal | None
    terminal_reason: KernelTerminalReason | None
    support_count: int
```

Every transition advances positive event time. FLAT rows describe the next
paired point/book state and delayed-entry fill distribution without requiring
a position. HOLDING rows describe the next paired point/book state plus the
fee-inclusive executable liquidation change for the actual counterfactual
quantity. Terminal/censor outcomes have `next_state_key=None`. For every
source key, probabilities are deterministic `Decimal` values that sum exactly
to one after residual allocation. Persist bucket boundaries, event-time
semantics, execution-scenario digest, Model 2 artifact digest, source match
IDs, and support counts in the kernel.

- [ ] **Step 4: Implement holding-path terminal labeling**

```python
def label_markout_path(
    *,
    frame: ConsensusL2ResearchFrameV1,
    position: PaperPosition,
    books: tuple[PilotBookSnapshot, ...],
    corrections: tuple[PilotPathCensor, ...],
    fee: Callable[[Decimal, Decimal], Decimal],
) -> PilotMarkoutLabel:
    deadline = position.opened_monotonic_ns + 300_000_000_000
    eligible = tuple(book for book in books if book.captured_monotonic_ns >= deadline)
    if _has_prior_censor(corrections, deadline):
        return _censored_label(
            frame=frame,
            reason=_first_censor_reason(corrections, deadline),
            deadline_monotonic_ns=deadline,
        )
    if not eligible:
        return _censored_label(
            frame=frame,
            reason="deadline_book_missing",
            deadline_monotonic_ns=deadline,
        )
    return executable_label(
        frame=frame,
        position=position,
        book=eligible[0],
        exit_assessment=assess_exit(
            position,
            eligible[0].bid_levels,
            now_monotonic_ns=eligible[0].captured_monotonic_ns,
            fee=fee,
        ),
    )
```

Also persist every intermediate executable markout required by the later
`SELL/HOLD` Bellman states. Reject sequence gaps, zero-time transitions,
generation changes, reconnections, stale books, and inadequate depth.

- [ ] **Step 5: Implement frozen empirical kernel construction**

```python
def build_frozen_markout_kernel(
    flat_paths: tuple[PilotFlatTransitionPath, ...],
    holding_paths: tuple[PilotHoldingTransitionPath, ...],
    *,
    training_match_ids: tuple[str, ...],
    minimum_bucket_support: int,
    execution_scenario_sha256: str,
    dynamic_artifact_sha256: str,
    version: str,
) -> FrozenMarkoutKernel:
    eligible_flat = _training_only_uncensored(flat_paths, training_match_ids)
    eligible_holding = _training_only_uncensored(holding_paths, training_match_ids)
    return _kernel_or_unsupported(
        flat_buckets=_group_by_frozen_state_key(eligible_flat),
        holding_buckets=_group_by_frozen_state_key(eligible_holding),
        minimum_bucket_support=minimum_bucket_support,
        execution_scenario_sha256=execution_scenario_sha256,
        dynamic_artifact_sha256=dynamic_artifact_sha256,
        version=version,
    )
```

The state key is frozen before evaluation and may include only score leverage,
fair-value gap, spread, book imbalance, elapsed-time bin, and position state.
Build separate FLAT and HOLDING transition tables. No evaluation label may
enter the kernel, and a kernel lacking minimum support for either table must
return typed unsupported coverage for that state.

- [ ] **Step 6: Run focused and existing barrier/economics tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_markout \
  tests.tennis_v1.test_consensus_l2_research \
  tests.tennis_v1.test_five_minute_path -v
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add inci_tennis_expert/pilot_markout.py tests/tennis_v1/test_pilot_markout.py
git commit -m "feat: add causal pilot markout kernel"
```

---

### Task 6: Implement Model 3's finite-horizon Bellman solver

**Files:**
- Create: `inci_tennis_expert/pilot_optimal_stopping.py`
- Create: `tests/tennis_v1/test_pilot_optimal_stopping.py`

**Interfaces:**
- Consumes: `PilotOutcomeEstimate`, `PilotBookSnapshot`,
  `FrozenMarkoutKernel`, `PilotExecutionScenario`, `PilotPositionState`, and
  the mode-specific remaining clock.
- Produces:
  `evaluate_optimal_stopping(input) -> PilotPolicyEstimate`.

- [ ] **Step 1: Write failing terminal, wait, and unsupported tests**

```python
class PilotOptimalStoppingTests(unittest.TestCase):
    def test_missing_kernel_returns_typed_unsupported_result(self) -> None:
        actual = evaluate_optimal_stopping(_input(kernel=None))
        self.assertFalse(actual.supported)
        self.assertEqual(actual.action, PilotAction.ABSTAIN)
        self.assertEqual(
            actual.abstention_reason,
            PilotSupportReason.UNSUPPORTED_NO_MARKOUT_KERNEL,
        )

    def test_flat_state_waits_when_wait_value_exceeds_buy_value(self) -> None:
        actual = evaluate_optimal_stopping(_input(kernel=_wait_favored_kernel()))
        self.assertEqual(actual.action, PilotAction.WAIT)
        self.assertGreater(actual.wait_value, actual.buy_value)

    def test_holding_state_sells_at_terminal_horizon(self) -> None:
        actual = evaluate_optimal_stopping(
            _input(position=_holding(), remaining_holding_ns=0)
        )
        self.assertEqual(actual.action, PilotAction.SELL)

    def test_wait_then_buy_starts_a_fresh_holding_clock(self) -> None:
        actual = evaluate_optimal_stopping(
            _input(
                position=_flat(),
                remaining_flat_wait_ns=1_000,
                kernel=_wait_then_buy_kernel(elapsed_ns=400),
            )
        )
        self.assertEqual(actual.buy_branch_holding_horizon_ns, 300_000_000_000)
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_optimal_stopping -v
```

Expected: import failure for `pilot_optimal_stopping`.

- [ ] **Step 3: Implement backward induction with deterministic ties**

```python
def solve_value(
    state: DiscretePolicyState,
    kernel: FrozenMarkoutKernel,
) -> PolicyNodeValue:
    if (
        state.position is PositionState.HOLDING
        and state.remaining_holding_ns == 0
    ):
        return terminal_liquidation_value(state)
    if (
        state.position is PositionState.FLAT
        and state.remaining_flat_wait_ns == 0
    ):
        return terminal_flat_abstention(state)
    transitions = kernel.transitions_for(state.kernel_key)
    if state.position is PositionState.FLAT:
        buy = expected_buy_value(state, transitions)
        wait = expected_wait_value(state, transitions)
        return wait if wait.value >= buy.value else buy
    sell = executable_sell_value(state)
    hold = expected_hold_value(state, transitions)
    return sell if sell.value >= hold.value else hold
```

Use `WAIT` and `SELL` as deterministic tie winners. Walk executable levels,
include both entry and exit fee rounding, retain actual partial size, enforce
the `$50.00` all-in debit cap, and persist the selected action plus all action
values. Memoize only immutable discrete states.

Each `WAIT` transition subtracts its positive `elapsed_ns` from the flat-wait
clock. `BUY` uses the transition's delayed-arrival fill distribution; a fill
creates a HOLDING state with a fresh `holding_horizon_ns`, while a no-fill
remains FLAT with elapsed flat time removed. Each `HOLD` transition subtracts
elapsed time from the holding clock and clips at the forced-liquidation
deadline. The solver may use only the one-step FLAT or HOLDING table matching
the current mode; a terminal 300-second label alone is never treated as a
transition kernel.

- [ ] **Step 4: Add an import-boundary test**

Parse the pilot modules with `ast` and fail if any imports resolve to an order,
executor, credential, or Kalshi write transport module. The allowed runtime
composition is filesystem input/output only.

- [ ] **Step 5: Run focused and economics tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_optimal_stopping \
  tests.tennis_v1.test_five_minute_path \
  tests.tennis_v1.test_paper_policy -v
```

Expected: all selected tests pass and paper promotion remains disabled.

- [ ] **Step 6: Commit Task 6**

```bash
git add inci_tennis_expert/pilot_optimal_stopping.py tests/tennis_v1/test_pilot_optimal_stopping.py
git commit -m "feat: add pilot optimal stopping model"
```

---

### Task 6b: Implement the immediate-entry control policy

**Files:**
- Create: `inci_tennis_expert/pilot_immediate_baseline.py`
- Create: `tests/tennis_v1/test_pilot_immediate_baseline.py`

**Interfaces:**
- Consumes the same `PilotDecisionFrame`, Model 2 estimate, frozen markout
  artifact, fee function, execution scenario, and shadow position as Model 3.
- Produces a `PilotImmediateBaselineEstimate` and pending shadow action.
- Reuses `assess_v1_dip`, `size_ioc_entry`, `evaluate_entry`, and `assess_exit`
  from `inci_tennis_expert.five_minute_path` without weakening their gates.

This is the control for waiting optionality. When the existing 45-second/7-cent
dip, conservative fair-value, `$5` lower expected P&L, artifact, depth, fee,
and `$50` debit gates all pass, it chooses `BUY_NOW`; it has no `WAIT` action.
Once filled, it uses the existing executable `+$5 / -$5 / 300-second` exit
rules. It uses the same delayed-arrival book and no-fill semantics as Model 3.
Evaluate both authorized routes independently, choose the route with the
higher conservative expected net P&L, and abstain on an exact tie.

- [ ] **Step 1: Write failing equivalence and common-execution tests**

```python
class PilotImmediateBaselineTests(unittest.TestCase):
    def test_qualified_dip_chooses_buy_now_not_wait(self) -> None:
        actual = evaluate_immediate_baseline(_qualified_input())
        self.assertEqual(actual.action, PilotImmediateAction.BUY_NOW)

    def test_below_threshold_dip_abstains(self) -> None:
        actual = evaluate_immediate_baseline(_below_threshold_input())
        self.assertEqual(actual.action, PilotImmediateAction.ABSTAIN)

    def test_baseline_and_model3_use_same_arrival_book(self) -> None:
        baseline, model3 = replay_both_policies(_shared_execution_fixture())
        self.assertEqual(baseline.arrival_book_sha256, model3.arrival_book_sha256)
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_immediate_baseline -v
```

- [ ] **Step 3: Implement the no-wait baseline and typed projection**

Bind every decision, fill, fee, exit, and abstention to the same point, frame,
Model 2, kernel, and execution-scenario digests used by Model 3. A missing
qualified markout artifact is unsupported, not zero P&L. Do not retrofit an
entry after seeing a later book.

- [ ] **Step 4: Run baseline and existing economics tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_immediate_baseline \
  tests.tennis_v1.test_five_minute_path \
  tests.tennis_v1.test_paper_policy -v
```

Expected: all selected tests pass and paper promotion remains disabled.

- [ ] **Step 5: Commit Task 6b**

```bash
git add \
  inci_tennis_expert/pilot_immediate_baseline.py \
  tests/tennis_v1/test_pilot_immediate_baseline.py
git commit -m "feat: add immediate-entry pilot baseline"
```

---

### Task 7: Compose the three models and deterministic replay CLI

**Files:**
- Create: `inci_tennis_expert/pilot_runner.py`
- Create: `inci_tennis_runtime/three_model_pilot_cli.py`
- Create: `tests/tennis_v1/test_pilot_runner.py`

**Interfaces:**
- Consumes: an ordered replay stream of adapter-produced `PilotDecisionFrame`,
  intervening gap-free full-L2, and censor records plus frozen model, binding,
  kernel, and execution artifacts.
- Produces: one `PilotComparisonRow` per accepted or abstained point decision
  and separate append-only execution/label records plus canonical JSONL bytes.
- CLI modes:
  `--replay`, `--static-artifact`, `--dynamic-artifact`, optional
  `--markout-kernel`, `--binding`, `--binding-metadata`,
  `--execution-scenario`, and `--output`.

- [ ] **Step 1: Write failing shared-input and byte-replay tests**

```python
class PilotRunnerTests(unittest.TestCase):
    def test_all_models_bind_to_same_point_digest(self) -> None:
        _, row = run_pilot_event(_runner(), _decision_frame())
        self.assertEqual(row.static.point_event_sha256, row.point_event_sha256)
        self.assertEqual(row.dynamic.point_event_sha256, row.point_event_sha256)
        self.assertEqual(row.policy.point_event_sha256, row.point_event_sha256)

    def test_action_is_pending_until_a_later_arrival_book(self) -> None:
        state, _ = run_pilot_event(_runner(), _buy_decision_frame(at_ns=1_000))
        self.assertIsNone(state.policy_position)
        self.assertIsNotNone(state.policy_pending_action)
        before_due = run_pilot_book(state, _book_record(at_ns=1_099))
        self.assertIsNone(before_due.policy_position)
        at_due = run_pilot_book(before_due, _book_record(at_ns=1_100))
        self.assertEqual(at_due.policy_position.opened_monotonic_ns, 1_100)

    def test_replay_is_byte_identical(self) -> None:
        first = encode_comparison_rows(replay_pilot(_fixture()))
        second = encode_comparison_rows(replay_pilot(_fixture()))
        self.assertEqual(first, second)
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_runner -v
```

Expected: import failure for `pilot_runner`.

- [ ] **Step 3: Implement the pure reducer**

```python
def run_pilot_event(
    state: PilotRunnerState,
    decision: PilotDecisionFrame,
) -> tuple[PilotRunnerState, PilotComparisonRow]:
    event = decision.point_event
    static = evaluate_static_outcome(event, state.static_artifact)
    dynamic_model, belief = state.dynamic_model.observe(event)
    dynamic = dynamic_model.evaluate(event)
    policy = evaluate_optimal_stopping(
        policy_input(
            decision,
            dynamic,
            state.markout_kernel,
            state.execution_scenario,
            state.policy_position,
        )
    )
    immediate = evaluate_immediate_baseline(
        immediate_input(
            decision,
            dynamic,
            state.markout_kernel,
            state.execution_scenario,
            state.baseline_position,
        )
    )
    next_state = replace(
        state,
        dynamic_model=dynamic_model,
        belief=belief,
        policy_pending_action=schedule_shadow_action(
            state.policy_pending_action,
            policy,
            decision_books=(decision.home_book, decision.away_book),
            execution_scenario=state.execution_scenario,
        ),
        baseline_pending_action=schedule_shadow_action(
            state.baseline_pending_action,
            immediate,
            decision_books=(decision.home_book, decision.away_book),
            execution_scenario=state.execution_scenario,
        ),
        last_sequence_number=event.sequence_number,
        last_point_id=event.point_id,
    )
    row = comparison_row(
        event=event,
        static=static,
        belief=belief,
        dynamic=dynamic,
        policy=policy,
        immediate_baseline=immediate,
        books=(decision.home_book, decision.away_book),
    )
    return next_state, row
```

`run_pilot_book(state, record)` validates the same match/binding, strictly
increasing book sequence, unchanged connection generation/subscription, and
arrival clock before filling either policy's independent pending action with
`size_ioc_entry`. It also performs due holding exits. It never calls Models 1
or 2 and never makes a new `BUY/WAIT/SELL/HOLD` decision; Version 1 decisions
remain point-triggered.

Validate the decision-frame sequence before calling the functional Model 2
update. Persist an abstention row for a bad event while retaining the last
valid runner state. The runner stores separate positions, pending actions,
fees, and P&L for Model 3 and the immediate-entry baseline so they cannot
interfere.

- [ ] **Step 4: Implement the filesystem-only CLI**

```python
def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifacts = load_and_verify_frozen_artifacts(args)
    durable_records = load_replay_records(args.replay)
    outputs = replay_pilot(durable_records, artifacts)
    write_canonical_jsonl(args.output, outputs)
    return 0
```

Reject symlinks, non-regular inputs, overwriting an existing output, artifact
digest mismatch, malformed JSON, or trailing partial records. Do not download
historical data or open a network connection.

- [ ] **Step 5: Run focused CLI and model tests**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_pilot_frame_adapter \
  tests.tennis_v1.test_pilot_runner \
  tests.tennis_v1.test_pilot_static_model \
  tests.tennis_v1.test_pilot_dynamic_model \
  tests.tennis_v1.test_pilot_optimal_stopping \
  tests.tennis_v1.test_pilot_immediate_baseline -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 7**

```bash
git add \
  inci_tennis_expert/pilot_runner.py \
  inci_tennis_runtime/three_model_pilot_cli.py \
  tests/tennis_v1/test_pilot_runner.py
git commit -m "feat: add three-model pilot replay runner"
```

---

### Task 8: Evaluate forecasts and shadow policy chronologically

**Files:**
- Create: `inci_tennis_expert/pilot_evaluator.py`
- Create: `tests/tennis_v1/test_pilot_evaluator.py`

**Interfaces:**
- Consumes: frozen `PilotComparisonRow` values plus separate digest-keyed
  next-point, settlement, execution, and `PilotMarkoutLabel` records.
- Produces: `PilotEvaluationReport` and deterministic JSON projection.

- [ ] **Step 1: Write failing metric and leakage tests**

```python
class PilotEvaluatorTests(unittest.TestCase):
    def test_next_point_probability_is_scored_before_observation(self) -> None:
        report = evaluate_pilot(_rows_with_known_next_point())
        self.assertEqual(report.static.next_point_count, 1)
        self.assertEqual(report.dynamic.next_point_count, 1)

    def test_next_point_label_must_bind_prior_row_digest(self) -> None:
        with self.assertRaisesRegex(PilotEvaluationError, "^label_binding$"):
            evaluate_pilot(_future_point_bound_to_wrong_row())

    def test_unsupported_policy_is_not_counted_as_zero_pnl(self) -> None:
        report = evaluate_pilot(_rows_with_unsupported_policy())
        self.assertEqual(report.policy.supported_action_count, 0)
        self.assertIsNone(report.policy.mean_net_pnl)

    def test_future_test_match_cannot_appear_in_kernel_training(self) -> None:
        with self.assertRaisesRegex(PilotEvaluationError, "^partition_leakage$"):
            evaluate_pilot(_leaking_fixture())

    def test_incremental_pnl_is_model3_minus_immediate_baseline(self) -> None:
        report = evaluate_pilot(_paired_policy_fixture())
        self.assertEqual(
            report.incremental_policy_net_pnl,
            report.policy.cumulative_net_pnl
            - report.immediate_baseline.cumulative_net_pnl,
        )
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
.venv/bin/python -m unittest tests.tennis_v1.test_pilot_evaluator -v
```

Expected: import failure for `pilot_evaluator`.

- [ ] **Step 3: Implement exact report calculations**

```python
@dataclass(frozen=True, slots=True)
class ForecastMetrics:
    observation_count: int
    log_loss: Decimal | None
    brier_score: Decimal | None
    calibration_bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True, slots=True)
class PolicyMetrics:
    supported_action_count: int
    filled_position_count: int
    mean_net_pnl: Decimal | None
    cumulative_net_pnl: Decimal | None
    maximum_drawdown: Decimal | None
    tail_loss: Decimal | None
```

Calculate next-point log loss and Brier score, frozen-bin calibration, match
log loss, disagreement slices, abstention rates, executable net P&L,
drawdown, and match-clustered bootstrap intervals. A row after point `t`
contains `home_next_point_probability` and is scored only by the separate
label for point `t+1`; the final point in a match has no next-point label.
Seed the bootstrap from a frozen artifact value and resample whole matches,
never individual points.

Compute identical `PolicyMetrics` for Model 3 and the immediate-entry baseline
from their independent shadow ledgers. Report paired per-opportunity and
per-match incremental P&L (`Model 3 - immediate baseline`), action agreement,
wait value, and clustered confidence intervals. Unsupported/no-fill rows stay
distinct from zero-P&L filled rows.

- [ ] **Step 4: Add explicit report conclusions**

The report status must be one of:

```text
PLUMBING_ONLY
INSUFFICIENT_FORWARD_SUPPORT
NO_INCREMENTAL_EVIDENCE
POSITIVE_INCREMENTAL_EVIDENCE
```

`POSITIVE_INCREMENTAL_EVIDENCE` requires a predeclared positive lower bound
for Model 3's incremental fee-inclusive net P&L over the immediate-entry
baseline and no calibration regression beyond the frozen tolerance. A single
match cannot satisfy this status.

- [ ] **Step 5: Run all pilot tests**

```bash
.venv/bin/python -m unittest discover -s tests/tennis_v1 -p 'test_pilot_*.py' -v
```

Expected: all pilot tests pass.

- [ ] **Step 6: Commit Task 8**

```bash
git add inci_tennis_expert/pilot_evaluator.py tests/tennis_v1/test_pilot_evaluator.py
git commit -m "feat: add three-model pilot evaluation"
```

---

### Task 9: Verify the pilot against existing boundaries

**Files:**
- Modify only if required by an existing explicit inventory:
  repository inventory or AST-seal files identified by the failing boundary
  test.
- Do not modify legacy strategy behavior to satisfy a boundary test.

**Interfaces:**
- Consumes: completed Tasks 1–8.
- Produces: fresh verification evidence and an offline sample output.

- [ ] **Step 1: Run the complete pilot suite**

```bash
.venv/bin/python -m unittest discover -s tests/tennis_v1 -p 'test_pilot_*.py' -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run focused existing model and economics suites**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_win_probability \
  tests.tennis_v1.test_first_set_model \
  tests.tennis_v1.test_consensus_l2_research \
  tests.tennis_v1.test_five_minute_path \
  tests.tennis_v1.test_paper_policy -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Run the legacy top-level suite**

```bash
.venv/bin/python tests.py
```

Expected: all legacy tests pass.

- [ ] **Step 4: Run dependency boundaries and inspect imports**

```bash
.venv/bin/python -m unittest \
  tests.tennis_v1.test_expert_dependency_boundary \
  tests.tennis_v1.test_dependency_boundary -v
rg -n "order|credential|private_key|create_order|cancel_order" \
  inci_tennis_expert/pilot_*.py \
  inci_tennis_runtime/three_model_pilot_cli.py
```

Expected: boundary tests pass. Text matches may occur only in typed no-order
guards, documentation, or rejection tests; inspect every match.

- [ ] **Step 5: Run an offline deterministic replay twice**

```bash
.venv/bin/python -m inci_tennis_runtime.three_model_pilot_cli \
  --replay work/pilot/fixture.jsonl \
  --static-artifact work/pilot/static.json \
  --dynamic-artifact work/pilot/dynamic.json \
  --binding work/pilot/binding.json \
  --binding-metadata work/pilot/binding-metadata.json \
  --execution-scenario work/pilot/execution.json \
  --output work/pilot/run-1.jsonl
.venv/bin/python -m inci_tennis_runtime.three_model_pilot_cli \
  --replay work/pilot/fixture.jsonl \
  --static-artifact work/pilot/static.json \
  --dynamic-artifact work/pilot/dynamic.json \
  --binding work/pilot/binding.json \
  --binding-metadata work/pilot/binding-metadata.json \
  --execution-scenario work/pilot/execution.json \
  --output work/pilot/run-2.jsonl
shasum -a 256 work/pilot/run-1.jsonl work/pilot/run-2.jsonl
```

Expected: both output hashes are identical. Model 3 reports
`UNSUPPORTED_NO_MARKOUT_KERNEL` when `--markout-kernel` is omitted.

- [ ] **Step 6: Review the final diff**

```bash
git status --short
git diff --check
git diff --stat
```

Expected: only planned pilot files and any explicitly required inventory/seal
updates appear; `git diff --check` exits zero.

- [ ] **Step 7: Handle any inventory failure without guessing**

If Step 4 reports a missing explicit inventory or AST seal, stop this task and
record the exact failing test, expected inventory identifier, and files named
by that test. Do not regenerate or edit a seal speculatively. Create a focused
follow-up change with its own red/green verification. When Step 4 passes, no
inventory commit is required.

---

## Pilot Run Sequence

After implementation and verification:

1. import an explicitly approved historical point file into canonical
   `PilotPointEvent` JSONL without downloading it from the runtime;
2. split matches chronologically into train, validation, and untouched future
   test partitions;
3. fit and freeze the Model 2 artifact on train/validation only;
4. replay Models 1 and 2 on the future partition;
5. collect synchronized forward score and full-L2 sessions;
6. build separate FLAT and HOLDING one-step kernel tables from older qualified
   sessions only, then freeze them after minimum support is met;
7. replay Model 3 and the immediate-entry baseline with the same frozen
   execution scenario on later untouched sessions; and
8. publish the deterministic paired report with support counts and
   uncertainty.

The first few-hour run validates plumbing and compares Models 1 and 2. Model 3
is implemented and runs its support gate, but credible action comparison begins
only after the repository contains qualified forward markout evidence.
