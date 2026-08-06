# Task 1 implementer report

## Implementation summary

Added a paper-only score domain in `live_paper_contracts.py` and a pure
coordinator in `live_paper_score.py`. The implementation does not import or
reuse `PilotPointEvent`. It provides frozen source/anchor/transition/state and
decision contracts, deterministic domain-separated digests, coordinate
comparison that excludes provider transport identity but includes lifecycle,
server, and correction epoch, and an explicit `LiveScoreFacts` projection with
the `PAPER_LOCAL_REVISION_TRANSPORT_ONLY` authority label.

The coordinator uses a five-second monotonic freshness window. Consensus
requires two distinct proven `independent_lineage_id` values (and distinct
lineage digests); mirrors remain single-source paper. It accepts only a unique
one-point successor found by trying both legal winners through `apply_point`.
Gaps/corrections quarantine the anchor, and rebase either occurs immediately
on independent consensus or after two strictly later identical captures from
the same single lineage separated by at least 250 ms. Fresh disagreement
clears a pending rebase candidate.

## RED

First command requested by the task:

```text
$ python -m unittest tests.tennis_v1.test_live_paper_score -v
TypeError: dataclass() got an unexpected keyword argument 'slots'
```

The shell `python` is Python 3.9.25 while this repository requires Python
3.14.5. Re-ran the same focused test with the project-compatible interpreter:

```text
$ /opt/homebrew/bin/python3 -m unittest tests.tennis_v1.test_live_paper_score -v
Ran 8 tests in 0.003s
FAILED (failures=8)
```

Each failure was the expected `AssertionError: live paper score API is missing`
caused by `ModuleNotFoundError: inci_tennis_expert.live_paper_contracts`.

## GREEN / verification

```text
$ /opt/homebrew/bin/python3 -m py_compile inci_tennis_expert/live_paper_contracts.py inci_tennis_expert/live_paper_score.py && /opt/homebrew/bin/python3 -m unittest tests.tennis_v1.test_live_paper_score tests.tennis_v1.test_tennis_score tests.tennis_v1.test_pilot_contracts -v
Ran 47 tests in 0.039s
OK
```

`git diff --check` completed successfully.

## Files changed

- `inci_tennis_expert/live_paper_contracts.py` (new)
- `inci_tennis_expert/live_paper_score.py` (new)
- `tests/tennis_v1/test_live_paper_score.py` (new; eight behavioral tests)

## Self-review

- No imports from the sealed offline pilot contract or any order/Kalshi path.
- Fresh disagreement is not degraded into single-source acceptance.
- Candidate timestamps must be strictly later and both captures must remain
  fresh; disagreement or lineage change resets the candidate; rebasing resets
  the local point ordinal instead of inventing missing points.
- Source selection orders supporting digests deterministically and contract
  digests validate on construction.
- Independent review found and the implementation now rejects multiple
  unproven lineages, binds parser independence to the manifest context, and
  records proven independent-lineage IDs in consensus anchors/transitions.

## Concerns

The repository's default `python` is 3.9.25, incompatible with this project's
declared Python 3.14.5 requirement; validation used `/opt/homebrew/bin/python3`
(3.14.5). No product or implementation concern remains.
