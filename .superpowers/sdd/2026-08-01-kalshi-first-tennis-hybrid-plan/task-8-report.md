# Task 8 — operator documentation, seals, and offline verification

## Scope

Implemented from clean commit `351003a` on
`feature/live-tennis-shadow-collector`. No package-root export was added, no
runtime evidence or credential was modified, and no push was performed.

Production closure now includes the Task 1–7 hybrid modules:

- adapters: discovery contracts, coverage registry, hybrid resolver, and
  Sportradar hybrid parser;
- I/O: Kalshi catalog, evidence/store facade, public finalized-Market
  transport, settlement source/store, and existing read-only bridges;
- runtime: price-only collector, chooser CLI, synchronized collector, and
  settlement CLI.

The exact environment inventories, canonical AST hashes, path-bound import
maps, reviewed clock calls, and GET-only transport exception are closed over
the reviewed source. The settlement transport exception applies only when its
exact path and canonical AST hash both match; its dedicated whole-module seal
and behavioral GET-only tests remain independent gates.

## TDD RED

After removing only generated `__pycache__`/`.pyc` artifacts:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_four_packages_have_independent_ast_seals tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_task8_environment_inventory_closes_candidate_sources tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_task8_package_maps_pin_exact_sources_and_resources
```

Result: `Ran 3 tests`; failed with three inventory errors and three map
failures naming exactly the unsealed Task 1–7 modules:
`shadow_discovery_contracts.py`, `shadow_provider_coverage.py`,
`kalshi_shadow_settlement.py`, `shadow_settlement_labels.py`,
`live_price_only_collector.py`, and `shadow_settlement_cli.py`.

No-network sentinel RED cycles:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_hybrid_no_network
```

Result: `Ran 1 test`; error was the expected missing
`tests.tennis_v1.hybrid_no_network` module.

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_hybrid_no_network.HybridNoNetworkSentinelTests.test_runtime_sentinel_blocks_http_and_websocket_attempts
```

Result: `Ran 1 test`; the unguarded HTTP attempt reached DNS and failed with a
name-resolution error, proving the missing high-level HTTP sentinel.

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_hybrid_no_network.HybridNoNetworkSentinelTests.test_suite_runner_keeps_the_sentinel_active_for_loaded_tests
```

Result: `Ran 1 test`; error was the expected missing `run_suite` entry point.

Expert environment-ledger RED after expanding the production inventories:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_expert_journal_store
```

First result: `Ran 114 tests`; three failures proved the exact package-root
comment and warm/cold environment gate budgets were stale (`87` versus `104`,
and corresponding cold budget). Second result: one remaining cold exact-count
failure (`229` versus `280`). Only those deterministic expectations were
updated.

## GREEN and regression verification

Sentinel unit tests:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_hybrid_no_network
```

Result: `Ran 3 tests in 0.028s` — `OK`. The sentinel rejects socket connect,
connect-ex, DNS, `requests.Session.request`, and `websockets.connect`, and the
suite runner keeps the sentinel active while loaded tests execute.

Focused hybrid/settlement suite under the runtime sentinel:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m tests.tennis_v1.hybrid_no_network tests.tennis_v1.test_shadow_provider_coverage tests.tennis_v1.test_kalshi_shadow_catalog tests.tennis_v1.test_shadow_match_chooser tests.tennis_v1.test_shadow_evidence_integrity tests.tennis_v1.test_live_price_only_collector tests.tennis_v1.test_live_shadow_cli tests.tennis_v1.test_live_shadow_collector tests.tennis_v1.test_kalshi_shadow_settlement tests.tennis_v1.test_shadow_settlement_labels tests.tennis_v1.test_sportradar_trial_observer tests.tennis_v1.test_shadow_settlement_cli
```

Result: `Ran 306 tests in 15.453s` — `OK`; any socket, HTTP, or WebSocket
attempt would have failed the suite. Existing price-only behavior tests also
prove zero Sportradar transport/trial-ledger construction.

Independent source-before-network settlement audit and finalized sentinel:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m tests.tennis_v1.hybrid_no_network tests.tennis_v1.test_shadow_settlement_source_audit tests.tennis_v1.test_hybrid_no_network
```

Result: `Ran 15 tests in 0.125s` — `OK`.

Focused seal/inventory/import closure:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_four_packages_have_independent_ast_seals tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_task8_package_maps_pin_exact_sources_and_resources tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_task8_environment_inventory_closes_candidate_sources tests.tennis_v1.test_expert_dependency_boundary.ExpertDependencyBoundaryTests.test_task8_special_imports_require_exact_reviewed_source
```

Result: `Ran 4 tests in 5.899s` — `OK`.

Root regression:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python tests.py
```

Result: `ALL TESTS PASS (202 tests)`.

Expert contracts:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_expert_contracts
```

Result: `Ran 84 tests in 0.194s` — `OK`.

Expert journal/store after the RED fixes:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_expert_journal_store
```

Result: `Ran 114 tests in 39.760s` — `OK`.

Passing boundary subset (all case methods except the named legacy violation):

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -c 'import unittest; from tests.tennis_v1.test_expert_dependency_boundary import ExpertDependencyBoundaryTests as Case; excluded="test_phase_one_and_root_v6_import_none_of_the_new_packages"; names=[name for name in unittest.defaultTestLoader.getTestCaseNames(Case) if name != excluded]; result=unittest.TextTestRunner().run(unittest.TestSuite(Case(name) for name in names)); raise SystemExit(0 if result.wasSuccessful() else 1)'
```

Result: `Ran 41 tests in 6.053s` — `OK`.

Full boundary, intentionally separate:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m unittest tests.tennis_v1.test_expert_dependency_boundary
```

Result: `Ran 42 tests in 6.151s`; exactly one error:
`test_phase_one_and_root_v6_import_none_of_the_new_packages` reports the known
pre-existing
`tennis_v1/ingress.py:new_package_import_forbidden`. Its imports and allowlist
were not changed or weakened.

Dependency health:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/inci-tennis-v1-venv/bin/python -m pip check
```

Result: `No broken requirements found.`

## Documentation and behavior self-review

- Chooser documentation is Kalshi-first and defines selectable `VERIFIED`,
  selectable `PRICE_ONLY`, and visible non-selectable `CONFLICT`.
- Every optional-provider downgrade is explicit; Kalshi catalog failures halt.
- Empty/one-sided initial books remain selectable; candidate publication waits
  for the two-Market aggregate snapshot barrier, so a quiet/static dashboard
  can still be healthy.
- Both exact dashboard banners, staged provider-call preflight, local reprompt,
  Q/EOF/interrupt no-start behavior, and durable signal stop behavior are
  documented.
- Failover is provider-only, source-attested, fresh-session, and carries no
  score/book/reducer state; Kalshi/evidence/unknown failures halt.
- Collection/provider evidence locations, append-only guarantees, and the
  whole-root rollback limitation are explicit.
- Settlement uses the exact absolute-session-path command. Initial recognized
  non-final evidence stays pending and writes nothing. Initial final/conflict
  labels require finalized Kalshi evidence; after a durable final, any changed
  normalized evidence appends permanent conflict, including non-final evidence,
  with a supersedes link to the prior row.
- The settlement root is exactly
  `Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/inci/tennis-shadow-settlement'`,
  displayed as `~/.local/state/inci/tennis-shadow-settlement`, and explicitly
  not `HOME`-configurable.
- The observation-only/no-signal/no-strategy/no-fee/no-P&L/no-executor/no-order/
  no-portfolio/no-expert-synchronization threat boundary is explicit.

## Staging and hygiene

Final staged files:

```text
.superpowers/sdd/2026-08-01-kalshi-first-tennis-hybrid-plan/task-8-report.md
README.md
docs/tennis_v1/README.md
inci_tennis_io/expert_journal_store.py
inci_tennis_io/shadow_settlement_labels.py
tests/tennis_v1/hybrid_no_network.py
tests/tennis_v1/test_expert_dependency_boundary.py
tests/tennis_v1/test_expert_journal_store.py
tests/tennis_v1/test_hybrid_no_network.py
```

Filename-only staged-path scan rejected `.env*`, `.pem`, `.key`, `.log`,
session JSONL, raw/log/cache directories, Python bytecode/cache, and
`.DS_Store`. Result: no suspicious staged path.

The staged-added-line secret scan searched for private-key headers and direct
assignments to `SPORTRADAR_API_KEY` / `KALSHI_API_KEY_ID` without printing any
matched line or value. Result: `README.md` only, containing the intentional
operator placeholder exports; no production/test/report file matched.

Generated `__pycache__`, `.pyc`, and `.DS_Store` artifacts were removed after
verification. A filename-only rescan found none. `git diff --check` and
`git diff --cached --check` both exited 0. `git status --porcelain` contained
only the nine intended staged Task-8 paths above.

Pre-existing tracked branch evidence was preserved and is not staged by Task
8:

```text
ticks_v2_20260722.csv
ticks_v6_20260729_53f42bc58d42435d82248ac83e5d779a.csv
trades_v2_20260722.csv
trades_v6_20260729_53f42bc58d42435d82248ac83e5d779a.csv
```

All scans reported filenames only; no credential value was printed.

## Commit

Command required and used:

```bash
git commit -m "chore: document and seal hybrid tennis workflow"
```

Result: commit created successfully with nine intended Task-8 files; no push
command was run.

The final commit hash is reported outside this self-containing commit because
embedding a commit's own hash in its tracked contents is not possible. No push
was performed.

## Concerns

- Known unchanged boundary error:
  `tennis_v1/ingress.py:new_package_import_forbidden`.
- Local hash chains cannot detect coherent rollback/deletion of an entire
  evidence or settlement root; external immutable archival remains required.
- Historical CSV evidence already tracked on the inherited branch is
  preserved. Task 8 neither stages nor removes runtime evidence.

## Independent review and remediation

The Task-8 and whole-branch adversarial reviews found no Critical issue and
five Important gaps. All were reproduced before correction:

- finalized-void and post-final supersession semantics were described
  incorrectly, and the settlement exit-code contract was omitted;
- exactly sealed observation-only runtime files could acquire expert
  synchronization imports during a seal refresh;
- the initial network sentinel left raw datagram/server/DNS paths and
  import-time test loading outside its denial window;
- the Kalshi catalog buffered an unbounded response before applying its
  advertised 8 MiB cap;
- a duplicate provider ID rejected by the row-isolating parser could leave its
  first row selectable as `VERIFIED` in the resolver.

Regression tests first failed on each exact path. The fixes now bind rejected
provider rows to any strict stable ID they expose, convert a linked duplicate
identity to non-selectable `CONFLICT`, stream/cap/close every catalog response,
install the network sentinel before test-module loading, deny raw
datagram/server/DNS paths, and reject expert imports for the four
observation-only runtime modules even after their AST digests are refreshed.
The broad row-level `KeyError` catch noted in Task 1 was also removed.

The subsequent scoped re-review found four remaining gaps: package-root expert
aliases, synchronization authority re-exported through an unrestricted runtime
peer, the public `socket.SocketType` alias, and the stale report sentence about
post-final non-final evidence. Each bypass was reproduced before correction.
The adjacent low-level `_socket.socket` constructor and IO package-root alias
were also reproduced and closed during the same remediation.
The observation-only audit now rejects the expert package root and permits only
an exact per-file set of reviewed runtime-peer bindings. The sentinel routes
both public and low-level socket constructor aliases through its denial path,
including nested sentinel contexts. This report now describes post-final
supersession accurately.

Post-second-remediation verification:

- hybrid, settlement, source-audit, and sentinel suite: `331/331` OK;
- root suite: `202/202` PASS;
- expert contracts: `84/84` OK;
- expert journal/store: `114/114` OK;
- passing dependency-boundary subset: `45/45` OK;
- full boundary: `46` tests with only the unchanged
  `tennis_v1/ingress.py:new_package_import_forbidden` error;
- `pip check` and `git diff --check`: clean.

The remediation still has no order, portfolio, signal, P&L, demo, or live
execution authority. A fresh scoped re-review and whole-branch re-review remain
required before push.

## Final pre-commit hardening and verification

A subsequent whole-source adversarial pass reproduced and closed the remaining
resource-boundary and durability gaps before the final seal refresh:

- pre-opened native socket descriptors are quarantined by the automated-test
  sentinel, closing immutable constructor/method alias bypasses;
- evidence and settlement-label audits enforce finite directory, per-file,
  aggregate-byte, and prospective-commit capacities before unknown content or
  network transports are used;
- catalog and settlement reconciliation have aggregate request/row/time limits,
  including streamed-chunk deadline checks and stable overlong-header rejection;
- price-only durable admission validates the exact selected session before any
  evidence or Kalshi transport side effect, including stale-provider downgrade;
- verified and price-only setup failures terminalize inside an open evidence
  store, preserve cancellation/interrupt semantics, and close any unstarted
  Kalshi transport;
- repeated cancellation cannot abandon Sportradar reservation/outcome writes or
  leave unobserved shield-future exceptions; every worker outcome is retrieved
  exactly once and the first cancellation is preserved;
- WebSocket subscribe/snapshot sends and finalized-Market reconciliation are
  bounded; settlement CLI success is emitted only after successful cleanup;
- numeric wire grammars are ASCII-only, and deep Sportradar JSON recursion maps
  to the sanitized wire-contract error instead of escaping raw interpreter data.

Final pre-commit verification on the sealed moving tree:

- hybrid/settlement behavior under the no-network sentinel: `422/422` OK;
- standalone sentinel self-tests: `11/11` OK;
- root v6 regression: `202/202` PASS;
- expert contracts: `84/84` OK;
- expert journal/store: `114/114` OK;
- passing dependency-boundary subset: `47/47` OK;
- full boundary: `48` tests with exactly the unchanged inherited
  `tennis_v1/ingress.py:new_package_import_forbidden` error;
- canonical package AST comparison: zero mismatches;
- Python parse: `100` files; `pip check`: no broken requirements;
- `git diff --check`: clean.

No test made a network call, and no order, portfolio, signal, fee, P&L, demo,
or live-execution authority was added.

## Final independent moving-tree review

An independent whole-source review found no unresolved Critical or Important
issues. Its final post-seal focused hybrid run passed `364/364` tests under the
no-network sentinel. Exact seals and dependency maps passed, and the boundary
suite passed `47/48`; its sole failure remains the inherited
`tennis_v1/ingress.py:new_package_import_forbidden` finding. The reviewer found
no prohibited execution, order, portfolio, signal, fee, P&L, expert-sync, or
unexpected network authority in the reviewed tree.
