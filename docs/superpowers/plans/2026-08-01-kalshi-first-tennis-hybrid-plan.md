# Kalshi-First Tennis Hybrid Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every structurally eligible current-day Kalshi tennis game selectable for read-only evidence collection, with exact `VERIFIED`, `PRICE_ONLY`, and non-selectable `CONFLICT` states, a separate Kalshi-only collector, and finalized Kalshi settlement labels.

**Architecture:** A strict GET-only Kalshi catalog is the primary census. A pure resolver annotates each game with optional, observation-only Sportradar evidence through a closed coverage registry. Verified rows keep the existing synchronized collector; price-only rows use additive collector/evidence types with no provider-shaped fields, and finalized results are reconciled later through a separate public GET-only boundary.

**Tech Stack:** Python 3.14, frozen dataclasses, `Decimal`, `requests`, authenticated read-only Kalshi WebSocket, `asyncio`, SHA-256 append-only JSONL ledgers, `unittest`.

## Global Constraints

- One collection command selects one game and exactly two Market tickers.
- The chooser lists all structurally eligible current-day Kalshi Tennis/Games Events regardless of instantaneous depth.
- `VERIFIED` means a fresh exact observation-only source correlation; `execution_authorized` is always `False`.
- `PRICE_ONLY` contains no provider, score, signal, P&L, recommendation, or order fields.
- `CONFLICT` is displayed and cannot be selected.
- Name normalization is only Unicode NFKC, whitespace collapse, and case folding.
- Match start tolerance is inclusive at 900 seconds.
- No ticker prefix, Event title, fuzzy name, nickname, or substring classification.
- Existing synchronized evidence schemas and explicit CLI mode remain compatible.
- Price-only code imports no strategy, signal, fee, P&L, executor, order, portfolio, or expert synchronization capability.
- Live and demo execution remain disabled; automated tests perform no network calls.
- Run Python verification with `PYTHONDONTWRITEBYTECODE=1` inherited by subprocesses.

---

### Task 1: Immutable Discovery Contracts And Closed Coverage Registry

**Files:**
- Create: `inci_tennis_adapters/shadow_discovery_contracts.py`
- Create: `inci_tennis_adapters/shadow_provider_coverage.py`
- Modify: `inci_tennis_adapters/sportradar_trial_v3.py`
- Create: `tests/tennis_v1/test_shadow_provider_coverage.py`
- Modify: `tests/tennis_v1/test_sportradar_trial_observer.py`

**Interfaces:**
- Produces `HybridStatus`, `KalshiCompetitionProvenance`, `KalshiShadowMarket`, `KalshiShadowGame`, `KalshiCatalogExclusion`, `KalshiShadowCatalogSnapshot`, `ProviderDiscoveryState`, `ProviderMatchRef`, `HybridMatchRow`, and `HybridChooserSnapshot`.
- Produces `SportradarCompetitionProvenance` and `parse_live_summaries_for_hybrid(payload: bytes) -> SportradarHybridDiscoverySnapshot` without changing the strict legacy parser.
- Produces `coverage_registry_sha256() -> str` and `assess_provider_route(game, provider) -> ProviderCoverageAssessment`.

- [ ] **Step 1: Write the failing contract and registry tests**

```python
def test_registry_is_exact_default_deny_and_observation_only():
    atp = provider(category_id="sr:category:3", competition_type="singles")
    assert assess_provider_route(kalshi_atp(), atp).state == "supported"
    assert assess_provider_route(kalshi_unknown(), atp).state == "unclassified"
    assert assess_provider_route(kalshi_itf(), provider_itf()).state == "unsupported"
    assert assess_provider_route(kalshi_atp(), atp).execution_authorized is False

def test_hybrid_parser_isolates_one_bad_row_but_rejects_bad_envelope():
    snapshot = parse_live_summaries_for_hybrid(valid_and_bad_rows_payload())
    assert len(snapshot.matches) == 1
    assert snapshot.diagnostics[0].code == "sportradar_wire_contract_invalid"
```

Fixtures cover ATP `sr:category:3`, WTA `sr:category:6`, Challenger
`sr:category:72`, WTA 125K `sr:category:871`, ITF Men
`sr:category:785`, ITF Women `sr:category:213`, exhibition
`sr:category:79`, doubles, unknown, contradictory Kalshi provenance, table
permutations, and registry digest stability.

- [ ] **Step 2: Run Task 1 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest \
  tests.tennis_v1.test_shadow_provider_coverage \
  tests.tennis_v1.test_sportradar_trial_observer
```

Expected: missing discovery contracts/registry and hybrid parser failures.

- [ ] **Step 3: Implement the minimal frozen contracts and exact registry**

```python
class HybridStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PRICE_ONLY = "PRICE_ONLY"
    CONFLICT = "CONFLICT"

@dataclass(frozen=True, slots=True)
class ProviderCoverageAssessment:
    state: str
    reason: str
    canonical_tour: str | None
    authority_scope: str = "observation_only"
    execution_authorized: bool = False
```

The registry uses exact tuples only. Kalshi rules initially include exact
reviewed structured fixtures and the observed `KXITFMATCH` /
`KXITFWMATCH` Series denials; no prefix comparison is allowed. Canonically
serialize and hash the sorted rule tables. Parse `sport_event_context` sport,
category, competition ID/name/type/gender/level into a discovery-only
projection. A bad summary row becomes an index plus sanitized code; bad JSON,
top-level shape, generated time, or summaries container rejects the envelope.

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run the Task 1 command. Expected: all Task 1 tests pass and legacy strict
parser behavior remains unchanged.

- [ ] **Step 5: Commit Task 1**

```bash
git add inci_tennis_adapters/shadow_discovery_contracts.py \
  inci_tennis_adapters/shadow_provider_coverage.py \
  inci_tennis_adapters/sportradar_trial_v3.py \
  tests/tennis_v1/test_shadow_provider_coverage.py \
  tests/tennis_v1/test_sportradar_trial_observer.py
git commit -m "feat: add hybrid tennis discovery contracts"
```

---

### Task 2: Complete Liquidity-Independent Kalshi Census

**Files:**
- Modify: `inci_tennis_io/kalshi_shadow_catalog.py`
- Modify: `tests/tennis_v1/test_kalshi_shadow_catalog.py`

**Interfaces:**
- Produces `discover_tennis_catalog(*, now=None) -> KalshiShadowCatalogSnapshot`.
- Keeps `discover_tennis_games(*, now=None) -> tuple[tuple[KalshiShadowGame, ...], str]` as a compatibility wrapper.
- Uses literal public route families `/search/filters_by_sport`, `/series`, `/milestones`, and validated `/events/{event_ticker}` only.

- [ ] **Step 1: Write failing catalog tests**

```python
def test_empty_and_one_sided_books_remain_in_complete_census():
    snapshot = transport(empty_and_one_sided_pages()).discover_tennis_catalog(now=NOW)
    assert [row.initial_book_state for row in snapshot.games] == ["empty", "one_sided"]

def test_event_series_is_explicit_and_never_inferred_from_ticker_prefix():
    snapshot = transport(nonprefix_event_with_tennis_series()).discover_tennis_catalog(now=NOW)
    assert snapshot.games[0].provenance.series_ticker == "KXTENNISGAMES"
```

Tests also assert direct Event GETs, official Tennis/Games provenance,
competition-query accumulation, Milestone league preservation, initial quote
and depth preservation, stable exclusions, exactly two active binary non-MVE
$1 siblings, all page permutations, no partial pagination, sanitized errors,
bounded pacing/429 retries, and no mutation/credential route.

- [ ] **Step 2: Run Task 2 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_kalshi_shadow_catalog
```

Expected: no `discover_tennis_catalog`, no provenance, and empty-book exclusion
failures.

- [ ] **Step 3: Implement the complete census**

Replace quote-dependent `_eligible_markets` with structural eligibility. A
zero size maps that side's executable price/depth to `None` but does not remove
the Market. Accumulate exact competition query keys per Milestone. Fetch each
deduplicated current-day game Event directly with
`with_nested_markets="true"`; validate its explicit Series against the exact
Tennis-tagged Series set. Return stable `KalshiCatalogExclusion` rows for every
rejected current-day expected Event. Hash full provenance, initial books,
retained games, and exclusions.

- [ ] **Step 4: Run Task 2 tests and verify GREEN**

Run the Task 2 command. Expected: all catalog tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add inci_tennis_io/kalshi_shadow_catalog.py \
  tests/tennis_v1/test_kalshi_shadow_catalog.py
git commit -m "feat: make tennis catalog exchange first"
```

---

### Task 3: Kalshi-Anchored Hybrid Resolver

**Files:**
- Modify: `inci_tennis_adapters/shadow_match_chooser.py`
- Modify: `tests/tennis_v1/test_shadow_match_chooser.py`

**Interfaces:**
- Consumes `KalshiShadowCatalogSnapshot`, optional
  `SportradarHybridDiscoverySnapshot`, `ProviderDiscoveryState`, and registry
  digest.
- Produces `resolve_hybrid_shadow_matches(...) -> HybridChooserSnapshot` with
  exactly one row per catalog game.
- Keeps `normalize_player_name` unchanged.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_every_kalshi_game_has_exactly_one_state():
    result = resolve_hybrid_shadow_matches(catalog_three_games(), provider_one_match())
    assert [row.status for row in result.rows] == [
        HybridStatus.VERIFIED,
        HybridStatus.PRICE_ONLY,
        HybridStatus.PRICE_ONLY,
    ]

def test_ambiguous_or_terminal_disagreement_is_conflict_and_not_selectable():
    result = resolve_hybrid_shadow_matches(catalog_one_game(), conflicting_provider())
    assert result.rows[0].status is HybridStatus.CONFLICT
    assert result.rows[0].selectable is False
```

Tests cover exact/reversed order, NFKC/case/whitespace, 900/901 seconds,
degree-one both sides, duplicate IDs/pairs, provider absent/empty/stale/error,
non-live pre-start price-only, terminal/live conflict, coverage denied/default
deny, deterministic sorting/digest, source permutation, and provider-only
diagnostics.

- [ ] **Step 2: Run Task 3 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_shadow_match_chooser
```

Expected: missing hybrid resolver and one-row-per-game contract failures.

- [ ] **Step 3: Implement the pure resolver**

Build exact pair/start/coverage edges. Classify zero usable edges as
`PRICE_ONLY`, exactly one degree-one edge as `VERIFIED`, and ambiguous or
contradictory evidence as `CONFLICT`. A verified row carries exactly one
`ProviderMatchRef`; a price-only row carries none; a conflict carries sorted
candidate diagnostics but no selected provider. Canonically hash all rows,
diagnostics, catalog digest, provider digest/state, resolver rule version
`kalshi-first-hybrid-v1`, and registry digest.

- [ ] **Step 4: Run Task 3 tests and verify GREEN**

Run the Task 3 command. Expected: all resolver tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add inci_tennis_adapters/shadow_match_chooser.py \
  tests/tennis_v1/test_shadow_match_chooser.py
git commit -m "feat: resolve hybrid tennis evidence states"
```

---

### Task 4: Additive Price-Only Evidence Grammar

**Files:**
- Modify: `inci_tennis_io/shadow_evidence.py`
- Modify: `inci_tennis_io/facade.py`
- Modify: `inci_tennis_io/__init__.py`
- Modify: `tests/tennis_v1/test_shadow_evidence_integrity.py`

**Interfaces:**
- Produces `KalshiOnlyCredentialMaterial` and
  `load_kalshi_only_credential_material(environ=None)`.
- Produces `PriceOnlySessionEvidence` and `PriceOnlyEvidenceObservation`.
- Adds `append_price_only_session`, `append_price_only_observation`,
  `append_price_only_terminal`, and `ensure_price_only_halted_terminal`.
- Exposes `terminal_row_sha256` after a durable terminal for linked failover.

- [ ] **Step 1: Write failing evidence grammar tests**

```python
def test_price_only_session_reopens_without_sportradar_fields():
    store.append_price_only_session(price_only_session())
    receipt = store.persist_kalshi_frame(frame())
    store.append_price_only_observation(price_only_observation(receipt))
    store.append_price_only_terminal(**terminal_values())
    store.close()
    ShadowEvidenceStore(root).close()

def test_kind_trust_mixing_and_provider_field_injection_are_corruption():
    mutate_row("trust", "unqualified_shadow")
    with assertRaisesRegex(ShadowEvidenceError, "shadow_evidence_prior_corrupt"):
        ShadowEvidenceStore(root)
```

Tests cover Kalshi-only credential requirements, legacy frozen-row reopening,
session-first grammar, exact field sets, first-row deletion, terminal anchor,
identity/count mismatch, capture-before-reference, raw tamper/missing/orphan,
candidate/noncandidate books, zero-frame terminal, cancellation-compatible
capture-only sessions, unclean shutdown, and whole-root rollback limitation
documentation.

- [ ] **Step 2: Run Task 4 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_shadow_evidence_integrity
```

Expected: missing price-only dataclasses/methods and credential loader failures.

- [ ] **Step 3: Implement exact additive schemas**

Use exact trust by kind:

```python
_TRUST_BY_KIND = {
    "resolution": "unqualified_shadow",
    "observation": "unqualified_shadow",
    "kalshi_capture": "unqualified_shadow",
    "terminal": "unqualified_shadow",
    "auto_terminal": "unqualified_shadow",
    "price_only_session": "PRICE_ONLY",
    "price_only_kalshi_capture": "PRICE_ONLY",
    "price_only_observation": "PRICE_ONLY",
    "price_only_terminal": "PRICE_ONLY",
}
```

Selecting price-only fixes the session mode before any raw capture. In that
mode `persist_kalshi_frame` emits the price-only capture kind. Audit legacy and
price-only grammars independently; never relax existing provider-required
validators. Bind optional failover predecessor session/terminal digests as an
all-or-none pair in the first row.

- [ ] **Step 4: Run Task 4 tests and verify GREEN**

Run the Task 4 command. Expected: all evidence tests pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add inci_tennis_io/shadow_evidence.py inci_tennis_io/facade.py \
  inci_tennis_io/__init__.py \
  tests/tennis_v1/test_shadow_evidence_integrity.py
git commit -m "feat: add price-only shadow evidence grammar"
```

---

### Task 5: Independent Price-Only Collector And Dashboard

**Files:**
- Create: `inci_tennis_runtime/live_price_only_collector.py`
- Create: `tests/tennis_v1/test_live_price_only_collector.py`

**Interfaces:**
- Produces `PriceOnlyDashboardView`, `render_price_only_dashboard`, and
  `PriceOnlyShadowCollector`.
- Reuses `CandidateMarketProjection`, the exact read-only Kalshi transport,
  projector, and shielded durability helpers; consumes no provider interface.

- [ ] **Step 1: Write failing collector tests**

```python
async def test_price_only_persists_before_projecting_and_never_calls_provider():
    result = await collector.run(duration_seconds=10)
    assert events.index("persist") < events.index("project")
    assert provider_calls == []
    assert result == "duration_elapsed"

async def test_gap_clears_books_and_requests_correlated_snapshot():
    await collector.run(duration_seconds=10)
    assert observations[-2].kalshi_status == "gap"
    assert observations[-2].market_a.yes_bid is None
    assert snapshot_requests == [EXPECTED_SID]
```

Tests cover constructor validation, session-before-socket ordering port,
aggregate two-book barrier, empty/one-sided snapshots, raw persist before
projection, sequence gap/duplicate/out-of-order, parser error,
disconnect/reconnect 1/2/4 seconds, generation reset, timeouts, duration,
interrupt, cancellation during raw/observation/terminal durability, raw-write
failure, close-error precedence, zero provider calls, and literal dashboard
copy.

- [ ] **Step 2: Run Task 5 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_live_price_only_collector
```

Expected: missing collector module.

- [ ] **Step 3: Implement the collector without provider-shaped state**

Copy no score fields from `LiveShadowCollector`. Maintain only two neutral
Market books, Kalshi receipt/generation/sequence/status, frame count, recovery
attempts, clocks, and terminal state. Persist every raw frame before projector
application. Use fixed normal terminal meanings `duration_elapsed`,
`operator_interrupt`, and `cancelled`; every exception becomes sanitized
`halted`. The dashboard literal is exactly:

```text
READ ONLY / PRICE ONLY / NO SCORE FEED / NO SIGNALS / NO P&L / NO ORDERS
```

- [ ] **Step 4: Run Task 5 tests and verify GREEN**

Run the Task 5 command. Expected: all price-only collector tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add inci_tennis_runtime/live_price_only_collector.py \
  tests/tennis_v1/test_live_price_only_collector.py
git commit -m "feat: collect Kalshi-only tennis evidence"
```

---

### Task 6: Exchange-First CLI, Optional Provider, And Verified Failover

**Files:**
- Modify: `inci_tennis_runtime/live_shadow_cli.py`
- Modify: `inci_tennis_runtime/live_shadow_collector.py`
- Modify: `tests/tennis_v1/test_live_shadow_cli.py`
- Modify: `tests/tennis_v1/test_live_shadow_collector.py`

**Interfaces:**
- `--choose` numbers both verified and price-only rows and never numbers conflicts.
- Chooser mode loads Kalshi-only credentials first; Sportradar key/ledger are lazy and optional.
- Adds `price_only_collector_factory` and `kalshi_only_credential_loader` dependency ports.
- Verified provider-specific failure may return a sanitized predecessor terminal binding and start a new price-only session for remaining duration.

- [ ] **Step 1: Write failing CLI and failover tests**

```python
def test_no_sportradar_key_still_lists_and_collects_price_only():
    status = run_cli(["--choose"], environ=kalshi_only_env(), stdin=StringIO("1\n"), dependencies=fakes)
    assert status == 0
    assert fakes.price_only_runs == 1
    assert fakes.provider_factories == 0

def test_provider_failure_after_verified_start_links_new_price_only_session():
    status = run_cli(["--choose"], stdin=StringIO("1\n"), dependencies=verified_then_provider_failure())
    assert status == 0
    assert price_session.predecessor_terminal_sha256 == verified_terminal_sha256
    assert all("score" not in row for row in price_observations)
```

Tests cover catalog-before-provider order, one-call discovery budgeting,
optional key, zero quota, 429/network/parser/stale/empty downgrade, deterministic
three-section display, shared numbering, conflict selection rejection, invalid
reprompt without network, Q/EOF/interrupt, staged verified quota preflight,
price-only no provider construction, session before socket, identity recheck,
provider-only failover, no failover for Kalshi/evidence errors, remaining
duration, and explicit manual-mode regression.

- [ ] **Step 2: Run Task 6 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest \
  tests.tennis_v1.test_live_shadow_cli \
  tests.tennis_v1.test_live_shadow_collector
```

Expected: provider-first ordering, mandatory key, missing price collector, and
failover failures.

- [ ] **Step 3: Implement staged chooser orchestration**

Fetch and validate the catalog first. If a provider key and one ledger attempt
exist, capture once; convert every provider-specific failure into a sanitized
`ProviderDiscoveryState` after closing the trial session. Resolve once and
render once. A price selection closes discovery then opens a price-only
evidence session and collector. A verified selection preflights remaining
calls, appends existing resolution evidence, and runs the synchronized
collector. On a provider-specific halt, close the verified session, calculate
remaining monotonic duration, and start a linked price-only session. Never
reuse prior score state.

- [ ] **Step 4: Run Task 6 tests and verify GREEN**

Run the Task 6 command. Expected: all CLI and synchronized collector tests pass.

- [ ] **Step 5: Commit Task 6**

```bash
git add inci_tennis_runtime/live_shadow_cli.py \
  inci_tennis_runtime/live_shadow_collector.py \
  tests/tennis_v1/test_live_shadow_cli.py \
  tests/tennis_v1/test_live_shadow_collector.py
git commit -m "feat: orchestrate hybrid tennis collection"
```

---

### Task 7: Finalized Kalshi Settlement Labels

**Files:**
- Create: `inci_tennis_io/kalshi_shadow_settlement.py`
- Create: `inci_tennis_io/shadow_settlement_labels.py`
- Create: `inci_tennis_runtime/shadow_settlement_cli.py`
- Create: `tests/tennis_v1/test_kalshi_shadow_settlement.py`
- Create: `tests/tennis_v1/test_shadow_settlement_labels.py`

**Interfaces:**
- Produces `KalshiShadowSettlementTransport.get_market_result(ticker) -> KalshiFinalMarketState` using public GET only.
- Produces `reconcile_shadow_settlement(session_path, transport, store, clocks) -> str` returning `pending`, `final`, or `conflict`.
- CLI: `python -m inci_tennis_runtime.shadow_settlement_cli /absolute/session-UUID.jsonl`.

- [ ] **Step 1: Write failing settlement tests**

```python
def test_only_finalized_complementary_binary_results_label_winner():
    result = reconcile(finalized_yes_no_pair())
    assert result.state == "final"
    assert result.winning_market_ticker == "MARKET-A"

def test_determined_disputed_amended_void_and_inconsistent_pairs_do_not_label():
    assert reconcile(determined_pair()).state == "pending"
    assert reconcile(both_yes_pair()).state == "conflict"
    assert reconcile(void_pair()).winning_market_ticker is None
```

Tests cover exact GET paths, redirects/proxies/body/JSON/schema failures,
current and historical route fallback, Event/ticker identity, finalized status,
settlement timestamp/value/result consistency, raw-response private storage,
source session/selection/terminal digest binding, pending without write,
idempotent rerun, conflict permanence, superseding append semantics, tampering,
and zero portfolio/order authority.

- [ ] **Step 2: Run Task 7 tests and verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest \
  tests.tennis_v1.test_kalshi_shadow_settlement \
  tests.tennis_v1.test_shadow_settlement_labels
```

Expected: missing settlement transport/store/runtime.

- [ ] **Step 3: Implement finalized-only reconciliation**

Accept result values only from strict Market responses. Statuses other than
`finalized` are pending. Final binary results must be complementary and their
`settlement_value_dollars` values must be exact 0/1 complements. Persist raw
responses before appending a canonical sidecar row. Bind the source session's
first and terminal row digests. Never infer a result from prices or provider
scores and never call `/portfolio`.

- [ ] **Step 4: Run Task 7 tests and verify GREEN**

Run the Task 7 command. Expected: all settlement tests pass.

- [ ] **Step 5: Commit Task 7**

```bash
git add inci_tennis_io/kalshi_shadow_settlement.py \
  inci_tennis_io/shadow_settlement_labels.py \
  inci_tennis_runtime/shadow_settlement_cli.py \
  tests/tennis_v1/test_kalshi_shadow_settlement.py \
  tests/tennis_v1/test_shadow_settlement_labels.py
git commit -m "feat: label finalized tennis shadow outcomes"
```

---

### Task 8: Operator Documentation, Seals, Full Verification, And Push

**Files:**
- Modify: `README.md`
- Modify: `docs/tennis_v1/README.md`
- Modify: `inci_tennis_io/expert_journal_store.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`
- Modify: package facades or inventories only where new public types require export.

**Interfaces:**
- Documents chooser states, optional Sportradar behavior, empty-book meaning,
  price-only dashboard, evidence locations, stop behavior, settlement command,
  and explicit no-signal/no-order boundary.
- Updates exact module inventories and canonical AST hashes for every sealed
  created/modified package file.

- [ ] **Step 1: Add behavior-level boundary tests and documentation**

Static tests parse the new IO/runtime modules and prove there is no import or
call path to signal, strategy, fee, P&L, executor, order, portfolio, or expert
synchronization code. Transport tests execute controlled fake requests and
prove only approved GET/WebSocket subscriptions occur. Documentation gives the
exact operator commands and explains that empty books can remain quiet without
the collector being frozen.

- [ ] **Step 2: Run focused hybrid verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest \
  tests.tennis_v1.test_shadow_provider_coverage \
  tests.tennis_v1.test_kalshi_shadow_catalog \
  tests.tennis_v1.test_shadow_match_chooser \
  tests.tennis_v1.test_shadow_evidence_integrity \
  tests.tennis_v1.test_live_price_only_collector \
  tests.tennis_v1.test_live_shadow_cli \
  tests.tennis_v1.test_live_shadow_collector \
  tests.tennis_v1.test_kalshi_shadow_settlement \
  tests.tennis_v1.test_shadow_settlement_labels
```

Expected: all focused hybrid tests pass with no network calls.

- [ ] **Step 3: Update seals and run full regression verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python tests.py
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_expert_contracts
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_expert_journal_store
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.tennis_v1.test_expert_dependency_boundary
python -m pip check
git diff --check
git status --short
```

Expected: root suite, expert contracts/journal, and clean boundary subset pass.
Run the known full boundary case separately and record only the pre-existing
`tennis_v1/ingress.py:new_package_import_forbidden` finding; do not add an
allowlist. Remove only generated `__pycache__`, `.pyc`, and `.DS_Store`
artifacts, never user evidence or credentials.

- [ ] **Step 4: Perform secret and authority scans**

```bash
git diff --cached --name-only
git grep -n -E 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|SPORTRADAR_API_KEY=|KALSHI_API_KEY_ID=' -- . ':!docs/**'
git status --porcelain
```

Expected: no credential value, PEM, private state, raw capture, cache, or log
is staged; only intended source/test/docs files differ.

- [ ] **Step 5: Commit, review, and push**

```bash
git add README.md docs/tennis_v1/README.md \
  inci_tennis_io/expert_journal_store.py \
  tests/tennis_v1/test_expert_dependency_boundary.py
git commit -m "docs: document hybrid tennis evidence workflow"
git push origin feature/live-tennis-shadow-collector
git rev-parse HEAD
git rev-parse origin/feature/live-tennis-shadow-collector
```

Expected: local and remote hashes match, working tree is clean, and no
credential/cache/log file is tracked.
