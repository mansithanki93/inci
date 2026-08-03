# Task 3 Report — causal consensus-to-full-L2 research barrier

## Scope delivered

- Extended the existing two-ticker Kalshi reducer with a frozen, explicitly
  unqualified full-L2 export. It is available only in the reducer's existing
  `ready` state, copies both exact ladders and market IDs without mutable
  aliases, preserves generation/SID/global sequence, and binds them with a
  domain-separated canonical SHA-256 digest.
- Added a pure expert research module containing immutable Version-1 score
  support, accepted-consensus, L2 observation, frame, coverage, barrier state,
  and update contracts.
- Bound each accepted transition to the exact authoritative
  `ScoreConsensusResult`, exact supporter source IDs and proven lineages,
  provider-neutral normalized score-coordinate digests, raw-before-derived
  durable record references, acceptance clocks, market universe, and the last
  visible book watermark.
- Added a deterministic one-shot reducer that ignores pre-barrier books, pairs
  only the first causally eligible same-generation/SID observation, preserves
  all required censor outcomes, rejects conflicting/regressive replay, and
  requires explicit correction/consensus-epoch censoring before replacement.
- Documented the integration precondition that open/observe/censor callbacks
  arrive from the existing sequencer in global durable-record order. An L2
  callback that is otherwise post-barrier eligible but carries a durable
  record at or behind the consensus record is rejected as non-permissible;
  delayed books wholly before the barrier remain ignored.
- Added a state-wide last-consumed durable-record watermark. Accepted
  consensus, paired/censoring books, explicit censors, and otherwise ignored
  post-watermark book/censor inputs advance it. The cursor retains event kind
  plus a canonical event digest: exact same-sequence replay is idempotent,
  while conflicting same-sequence events and regressions fail closed.
  Coverage records bind the explicit censor event's durable sequence.
- Preserved the authority boundary: all new values are
  `research_only=True`, `execution_authorized=False`, and
  `qualification="unqualified_shadow"`; no trusted execution book,
  `OpportunityFrame`, model, signal, fill, runtime, store, registry, or order
  behavior was added.

## Files changed

- `inci_tennis_adapters/kalshi_v2.py`
- `inci_tennis_expert/consensus_l2_research.py`
- `tests/tennis_v1/test_consensus_l2_research.py`
- `.superpowers/sdd/2026-08-02-adaptive-first-set-dip-strategy-design/task-3-report.md`

Inventory/hash seals and journal-store inventory were intentionally not
edited; root owns integration.

## TDD evidence

### RED

- The three focused adapter-export tests initially failed with
  `AttributeError` because `UnqualifiedTwoTickerBookReducer.full_l2` did not
  exist.
- The initial expert suite failed at import with `ModuleNotFoundError` because
  `inci_tennis_expert.consensus_l2_research` did not exist.
- Subsequent focused regression tests independently exposed and then guarded:
  reconstructed-value replay relying on object identity; impossible update
  disposition/payload shapes; delayed pre-barrier old-generation handling;
  missing global supporter durable watermarks; and missing authoritative
  consensus/normalized-support binding. A final focused RED test also showed
  an otherwise eligible cross-stream durable regression was being ignored;
  it now fails closed while wholly pre-barrier observations remain ignored.
- The last review cycle produced focused RED failures for missing ignored-input
  cursor advancement and four forged state/update relationships. The final
  contracts now bind ARMED/ADVANCED/PAIRED/CENSORED updates to their transition,
  frame or coverage, durable cursor, event kind, and event digest.

### GREEN

Fresh final verification on 2026-08-03:

```text
python3 -m unittest tests.tennis_v1.test_consensus_l2_research
Ran 20 tests in 0.116s — OK

python3 -O -m unittest tests.tennis_v1.test_consensus_l2_research
Ran 20 tests in 0.137s — OK

python3 -m unittest \
  tests.tennis_v1.test_kalshi_readonly.UnqualifiedTwoTickerBookReducerTests
Ran 10 tests in 0.009s — OK

python3 -O -m unittest \
  tests.tennis_v1.test_kalshi_readonly.UnqualifiedTwoTickerBookReducerTests
Ran 10 tests in 0.009s — OK

python3 -m unittest tests.tennis_v1.test_score_consensus
Ran 56 tests in 0.007s — OK

python3 -O -m unittest tests.tennis_v1.test_score_consensus
Ran 56 tests in 0.007s — OK
```

Additional verification:

- `py_compile` passed for the changed adapter, expert module, and focused test.
- AST boundary check found no production `assert` and no expert import from
  adapters, I/O, or runtime.
- `git diff --check` passed.
- No changed line exceeds 88 characters.

## Limitations and integration requirement

- This is a forward-corpus capture foundation only. It does not collect,
  persist, label, train, forecast, signal, fill, or place orders.
- The pure reducer requires the caller to dispatch score, book, and censor
  inputs from the existing sequencer in global durable-record order. It
  rejects detectable regressions and explicit censor calls carry their durable
  sequence, but the module cannot buffer/reorder cross-stream callbacks.
  Preserving sequencer delivery order is an explicit future runtime
  integration requirement; runtime changes are forbidden in Task 3.
- The repository's generic adapter-authority boundary check will flag the new
  literal `execution_authorized=False` marker until root updates the owned AST
  seal. The required false authority marker is intentionally retained; no seal
  or inventory file is changed in this task commit.

## Reviewer finding dispositions

- Accepted and fixed: an otherwise eligible book with a durable record at or
  behind the active consensus barrier now rejects; a wholly pre-barrier
  delayed book still ignores before generation/SID checks.
- Accepted and fixed: a paired book or explicit censor now advances the
  state-wide durable watermark, so a later consensus transition cannot arm
  from an earlier durable record. Ignored post-watermark inputs also advance
  the cursor; exact sequence/kind/digest replay is idempotent and conflicts
  reject. Duplicate accepted-transition replay is resolved before regression.
- Rejected by root: retaining a prior censor reason as authorization for a
  later correction/epoch transition. Once the pending barrier was validly
  censored for the event then observed (for example, a gap or lifecycle
  change), Section 7 does not require a second retroactive correction coverage
  record. Truthful censor-event classification remains a caller/runtime
  responsibility.
- Accepted and fixed: `ConsensusL2BarrierUpdateV1` now enforces both the exact
  disposition/frame/coverage/pending-state shape matrix and relational binding
  to the frame/coverage transition, durable sequence, event kind, and digest.
  A pending state also requires its cursor to equal its accepted consensus.

## Commit

- Parent/base integrated before this work: `bf92e81`.
- The final Task 3 commit includes this report, so its SHA cannot be embedded
  in its own contents. The exact final SHA is returned to root with this
  report.
