# Task 3 Report — causal consensus-to-full-L2 research barrier

## Scope delivered

- Extended the existing two-ticker Kalshi reducer with a frozen, explicitly
  unqualified full-L2 export. It exists only in the reducer's existing
  `ready` state, copies both exact ladders and market IDs without mutable
  aliases, preserves generation/SID/global sequence, and binds them with a
  domain-separated canonical SHA-256 digest.
- Added a pure expert research module containing immutable Version-1 score
  support, accepted-consensus, L2 observation, frame, coverage, barrier state,
  and update contracts.
- Bound each accepted transition to the exact authoritative
  `ScoreConsensusResult`, exact supporter source IDs and proven independent
  lineages, provider-neutral normalized score coordinates, raw-before-derived
  durable references, acceptance clocks, market universe, and last visible
  book watermark. One source-lineage digest cannot claim conflicting
  independence IDs.
- Added an accepted-transition parent-digest chain. A newer transition must
  name the exact prior accepted-score digest, and `ADVANCED` coverage must
  close that exact prior transition rather than any lower historical key.
- Added a deterministic one-shot reducer that consumes inputs through one
  state-wide durable cursor, ignores but records pre-barrier observations,
  and pairs only the first causally eligible same-generation/SID L2 copy.
  The paired book must be the exact next exchange sequence; a skipped sequence
  is censored as `BOOK_SEQUENCE_GAP`. The immutable frame constructor enforces
  the same direct-successor rule independently of the reducer.
- Made exact current-cursor kind/digest replay idempotent. A same-sequence
  conflict fails as `global_durable_event_conflict`, and every older input —
  including an exact older accepted-transition replay — fails as a global
  durable regression. Ignored inputs with newer durable records advance the
  cursor and fingerprint.
- Bound state reconstruction to the exact match/market universe, monotonic
  per-source supporter watermarks, and the latest transition's supporter
  evidence. Future, missing, aliased, or regressive supporter watermarks fail
  closed.
- Retained the exact frame or censor coverage that resolved the current
  transition. Pending states cannot carry a resolution; resolved transitions
  cannot omit one. This prevents replay or direct reconstruction from silently
  dropping an unresolved score barrier after an ignored L2 record.
- Bound `ARMED`, `ADVANCED`, `PAIRED`, and `CENSORED` update dispositions to
  their exact state cursor, event kind/digest, transition, frame or coverage,
  and session state.
- Preserved the authority boundary: every new value is
  `research_only=True`, `execution_authorized=False`, and
  `qualification="unqualified_shadow"`; no trusted execution book,
  `OpportunityFrame`, model, signal, fill, runtime, registry, or order
  behavior was added.

## Files changed

Task 3 implementation:

- `inci_tennis_adapters/kalshi_v2.py`
- `inci_tennis_expert/consensus_l2_research.py`
- `tests/tennis_v1/test_consensus_l2_research.py`
- this report

Root integration also added the expert module to the governed journal-store
inventory and refreshed the exact AST inventory in
`tests/tennis_v1/test_expert_dependency_boundary.py`.

## TDD evidence

### RED

- The adapter export tests initially failed because
  `UnqualifiedTwoTickerBookReducer.full_l2` did not exist; the expert suite
  initially failed because `inci_tennis_expert.consensus_l2_research` did not
  exist.
- Focused regressions exposed object-identity replay, impossible update
  shapes, missing authoritative consensus/support binding, global supporter
  watermark gaps, and cross-stream durable regressions.
- Adversarial re-review then reproduced cursor bypasses for logically ignored
  books and exact older transition replays; prior-transition coverage forgery;
  source-lineage aliasing; state-universe drift; missing/future supporter
  watermarks; skipped exchange sequences; direct-frame sequence bypass;
  dropped pending coverage; forged update dispositions; and lost transition
  resolution after a newer ignored L2 record.
- Each reproduced defect received a focused failing regression before the
  contract or reducer was hardened.

### GREEN

Fresh final focused verification on 2026-08-03:

```text
python3 -B -m unittest tests.tennis_v1.test_consensus_l2_research
Ran 23 tests — OK

python3 -B -O -m unittest tests.tennis_v1.test_consensus_l2_research
Ran 23 tests — OK

python3 -B -m unittest \
  tests.tennis_v1.test_kalshi_readonly.UnqualifiedTwoTickerBookReducerTests
Ran 10 tests — OK

python3 -B -O -m unittest \
  tests.tennis_v1.test_kalshi_readonly.UnqualifiedTwoTickerBookReducerTests
Ran 10 tests — OK

python3 -B -m unittest tests.tennis_v1.test_score_consensus
Ran 60 tests — OK

python3 -B -O -m unittest tests.tennis_v1.test_score_consensus
Ran 60 tests — OK
```

The independent final re-review ran those three suites together: 93/93 passed
in normal mode and 93/93 passed under `python -O`. It also ran the five-minute
path, paper policy, win-probability, and live-parser suites: 61/61 passed in
both modes.

Additional verification:

- compilation passed for the changed adapter, expert module, and tests;
- focused package inventory and exact AST seal checks passed in normal and
  optimized modes;
- the final canonical AST SHA-256 for
  `inci_tennis_expert/consensus_l2_research.py` is
  `9971f3407fdb56a6fcb6f833293b589ee4ecf9f5192a07024eb753ab936ae5d3`;
- `git diff --check` passed; and
- the independent final semantic review reported no remaining actionable code
  findings.

## Limitations and integration requirement

- This is a forward-corpus capture foundation only. It does not collect,
  persist, label, train, forecast, signal, fill, or place orders.
- The pure reducer requires the caller to dispatch score, book, and censor
  records from the existing sequencer in strict global durable-record order.
  It detects replay, regression, conflict, and exchange-sequence gaps, but it
  deliberately does not buffer or reorder arbitrary cross-stream callbacks.
  That remains an explicit future runtime integration requirement.
- A paired frame is accepted only for the direct exchange-sequence successor
  to the book watermark stored with the accepted score. A delayed intermediate
  pre-barrier record is consumed and ignored, so the later jump is censored
  rather than treated as gap-free. This is a conservative false-negative, not
  permission to weaken causal pairing.
- The module remains unqualified research-only code. Production adapters,
  runtime wiring, a durable corpus store, model training, paper IOC execution,
  and all live-order authority remain outside Task 3.

## Commit history

- Parent/base integrated before Task 3: `bf92e81`.
- Initial Task 3 implementation: `52b309e`.
- The post-integration adversarial hardening and governed seal refresh are
  included in the root integration commit created after this report.
