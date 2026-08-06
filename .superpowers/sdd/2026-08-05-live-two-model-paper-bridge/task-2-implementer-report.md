# Task 2 implementation report — live Models 1 and 2

## Scope delivered

- Added `inci_tennis_expert/live_two_model.py`, a paper-only state bridge that
  accepts `LivePaperScoreAnchor` and `LivePaperPointTransition` directly; it
  does not construct or import `PilotPointEvent`.
- Added immutable `LiveTwoModelState` and `LiveTwoModelForecast` values.
  Forecasts expose both model estimates (next point/current set/match), model
  2 prior/posterior belief identities, artifact identities, raw-source
  commitment, anchor commitment, optional transition commitment, and resulting
  state commitment.
- Added deterministic, target-bound operator bootstrap artifacts using the
  frozen matrix `((.8,.15,.05),(.1,.8,.1),(.05,.15,.8))`, weights
  `(.2,.6,.2)`, and offsets `(-.5,0,.5)`. Bootstrap forecasts visibly carry
  `OPERATOR_BOOTSTRAP / NO_EDGE_CLAIM`; bootstrap artifacts cannot be
  relabeled or mixed with trained artifacts.
- Extracted state-based static and dynamic evaluation helpers. Existing
  completed-event APIs delegate to those helpers and retain their outputs.

## RED evidence

1. Before the live bridge existed:

   ```text
   /Users/mthanki/.venvs/inci-expert-py314/bin/python -m unittest \
     tests.tennis_v1.test_live_two_model -v
   ```

   Result: 4 failures, each `AssertionError: live two-model API is missing`
   caused by the absent `inci_tennis_expert.live_two_model` module.

2. After adding completed-event/state equivalence tests, temporarily removing
   the two state helper exports produced the expected focused RED result:

   ```text
   ImportError: cannot import name 'evaluate_static_state'
   ImportError: cannot import name 'evaluate_dynamic_state'
   ```

3. The authority-label contract RED run failed as expected before its fields
   were added:

   ```text
   AttributeError: 'LiveTwoModelForecast' object has no attribute
   'artifact_authority'
   AttributeError: 'LiveTwoModelForecast' object has no attribute
   'rebase_state'
   ```

4. Bootstrap authority hardening RED runs:

   ```text
   AssertionError: LiveTwoModelError not raised
   ```

   The first demonstrated relabeling both bootstrap artifacts as trained; the
   second demonstrated a mixed bootstrap/trained pair erasing the label.

5. Digest traceability RED run failed before the commitments were separated:

   ```text
   AttributeError: 'LiveTwoModelForecast' object has no attribute
   'anchor_sha256'
   ```

## GREEN evidence

Final required test command:

```text
/Users/mthanki/.venvs/inci-expert-py314/bin/python -m unittest \
  tests.tennis_v1.test_live_two_model \
  tests.tennis_v1.test_pilot_static_model \
  tests.tennis_v1.test_pilot_dynamic_model \
  tests.tennis_v1.test_two_model_pilot -v
```

Result: `Ran 42 tests in 2.239s` / `OK`.

The same final check included `git diff --check`; it completed cleanly.
An additional `python -m compileall -q` check passed for the three modified
model modules before the final authority/digest hardening; the final unit-test
run provides the fresh completion evidence.

## Self-review and constraints

- Verified `apply_live_paper_transition` requires the next local ordinal,
  matching before-score coordinates, and unchanged consensus/correction/rebase
  epochs. It calls the dynamic state updater exactly once with the observed
  server, then evaluates both models on the transition after-state.
- Verified rebase requires exactly the next rebase epoch, resets the local
  ordinal to zero and constructs a new `DynamicPointModel` from frozen
  artifacts; its forecast is `REBASED_PAPER`.
- Verified static/dynamic completed-event regression equivalence tests pass.
- Verified the live module imports no market, Kalshi, executor, client, order,
  cancellation, or transport capability. Independent review also found no
  remaining P1/P2 issues after the bootstrap-label and digest corrections.

## Residual concern

This task intentionally supplies only the state-model bridge. Durable
checkpointing/replay and paper execution remain later tasks; this module emits
the immutable commitments those layers need.

## Fix round 1 — artifact and match-binding seal

### Root cause

- Bootstrap classification examined only two public version strings. Recomputing
  the public artifact digests after changing those strings made the complete
  bootstrap template appear trained.
- `score_coordinates()` intentionally excludes scheduled start, provider match
  identity, player identities, and format. It is correct for score comparison
  but insufficient as the live-model continuity boundary.

### RED evidence

Focused live test command before the fix:

```text
/Users/mthanki/.venvs/inci-expert-py314/bin/python -m unittest \
  tests.tennis_v1.test_live_two_model -v
```

Result: `Ran 10 tests` / `FAILED (failures=5)`.

- Recomputed bootstrap artifacts with both versions retagged as trained were
  accepted (`AssertionError: LiveTwoModelError not raised`).
- A trained artifact carrying one bootstrap fingerprint was accepted.
- Scheduled-start, home-player, and provider-match drift with unchanged legal
  score coordinates were accepted before posterior update.

### GREEN implementation and evidence

- Bootstrap classification now validates the complete public bootstrap
  fingerprint: exact versions, partitions and static partition digest, frozen
  source/feature/code digests, dynamic template matrix/weights/offsets,
  cross-artifact target/cutoff invariants, and both self-digests. Any complete
  or partial bootstrap marker rejects a purported trained pair. This is strict
  public fingerprint classification, not a claim of secret provenance.
- `LiveTwoModelState.__post_init__` repeats the authority check.
- State freezes the anchor binding `(scheduled start, format, provider match,
  home player, away player)`. Every transition's before and after state must
  match it before `DynamicPointModel.observe_state_point` can run.
- Static/dynamic state-helper equivalence tests now also assert fixed known
  Decimal outcomes, preventing a shared regression from self-confirming.

Final requested command:

```text
/Users/mthanki/.venvs/inci-expert-py314/bin/python -m unittest \
  tests.tennis_v1.test_live_two_model \
  tests.tennis_v1.test_pilot_static_model \
  tests.tennis_v1.test_pilot_dynamic_model \
  tests.tennis_v1.test_two_model_pilot -v
```

Result: `Ran 47 tests in 2.371s` / `OK`. `git diff --check` was included and
completed cleanly.
