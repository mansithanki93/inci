# Interactive Shadow Match Chooser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed interactive chooser that automatically resolves a live Sportradar tennis match to exactly two Kalshi match-winner tickers and starts the existing read-only collector.

**Architecture:** A pure resolver consumes strict provider snapshots and a complete, official Kalshi Tennis/Games inventory. A narrow GET-only Kalshi catalog transport supplies that inventory, while the existing quota-ledger-backed Sportradar transport supplies live summaries. The CLI displays immutable numbered results, persists the chosen unqualified resolution, and delegates collection to the existing runtime.

**Tech Stack:** Python 3.14, dataclasses, `requests`, existing Kalshi/Sportradar schema adapters, `unittest`, append-only SHA-256 evidence ledger.

## Global Constraints

- Output remains `READ ONLY / AUTO-MATCHED / UNQUALIFIED / NO ORDERS`.
- Name normalization is limited to Unicode NFKC, whitespace collapse, and case folding.
- Match start tolerance is inclusive at 900 seconds.
- Both sides of the bipartite match graph must have degree one.
- The selector never uses fuzzy matching, ticker parsing, event-title parsing, liquidity ranking, or an order-capable client.
- The Sportradar discovery GET is durably reserved and included in quota preflight.
- Automated tests use fake transports and consume no live API calls.

---

### Task 1: Pure Match Resolver

**Files:**
- Create: `inci_tennis_adapters/shadow_match_chooser.py`
- Create: `tests/tennis_v1/test_shadow_match_chooser.py`

**Interfaces:**
- Consumes: `SportradarLiveSummariesSnapshot` and immutable Kalshi event candidates.
- Produces: `KalshiShadowGame`, `ShadowMatchChoice`, `ShadowUnavailableMatch`, `ShadowChooserSnapshot`, and `resolve_shadow_matches(...)`.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_exact_pair_reorders_tickers_to_provider_home_away():
    result = resolve_shadow_matches(provider_snapshot(), (kalshi_game(),))
    assert result.ready[0].provider_match_id == "sr:sport_event:123456"
    assert result.ready[0].market_tickers == ("HOME-TICKER", "AWAY-TICKER")

def test_ambiguous_graph_and_start_outside_900_seconds_are_unavailable():
    assert not resolve_shadow_matches(provider_snapshot(), ambiguous_games()).ready
    assert not resolve_shadow_matches(provider_snapshot(), late_game(901)).ready
```

Tests also cover reversed market order, NFKC/case/whitespace normalization, the inclusive 900-second boundary, duplicate normalized names, placeholders, non-live provider rows, terminal rows, exactly-two-market cardinality, degree-one enforcement, stable sorting, and deterministic snapshot hashing.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python -m unittest tests.tennis_v1.test_shadow_match_chooser
```

Expected: import failure because `inci_tennis_adapters.shadow_match_chooser` does not exist.

- [ ] **Step 3: Implement the minimal pure resolver**

```python
def normalize_player_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()

def resolve_shadow_matches(
    provider: SportradarLiveSummariesSnapshot,
    kalshi_games: tuple[KalshiShadowGame, ...],
) -> ShadowChooserSnapshot:
    # Validate both immutable inputs, build exact name/start edges, require
    # degree one on both sides, reorder tickers, classify every rejected row,
    # sort deterministically, and compute the canonical snapshot digest.
```

Use exact immutable dataclasses and fixed unavailable reason codes. Reject unsafe values at construction rather than coercing them.

- [ ] **Step 4: Run resolver tests and verify GREEN**

Run the Task 1 command. Expected: all resolver tests pass.

---

### Task 2: Complete GET-Only Kalshi Catalog And Live Discovery Capture

**Files:**
- Create: `inci_tennis_io/kalshi_shadow_catalog.py`
- Modify: `inci_tennis_io/sportradar_shadow_async.py`
- Modify: `sports_discovery.py`
- Modify: `tests/tennis_v1/test_sportradar_shadow_async.py`
- Create: `tests/tennis_v1/test_kalshi_shadow_catalog.py`

**Interfaces:**
- Produces `KalshiShadowCatalogTransport.discover_tennis_games()` returning `(tuple[KalshiShadowGame, ...], catalog_sha256)`.
- Produces `SportradarShadowTransport.fetch_live_summaries()` returning the existing durable `TrialCapture`.
- Produces a complete unranked Sports/Games inventory helper reused by the catalog transport.

- [ ] **Step 1: Write failing transport and inventory tests**

```python
def test_catalog_uses_only_fixed_public_get_routes_and_paginates():
    games, digest = transport.discover_tennis_games()
    assert session.methods == {"GET"}
    assert {game.event_ticker for game in games} == {"KX-EVENT-1"}
    assert len(digest) == 64

async def test_async_provider_fetches_live_summaries_through_trial_ledger():
    capture = await transport.fetch_live_summaries()
    assert capture.route == "live_summaries"
```

Tests reject redirects, proxies, oversized/duplicate-key/non-JSON bodies,
repeated or excessive cursors, unsupported product cardinality, partial sibling
pairs, off-Sport/off-Games provenance, and any non-GET or unapproved path.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.tennis_v1.test_kalshi_shadow_catalog tests.tennis_v1.test_sportradar_shadow_async
```

Expected: missing catalog module/method failures.

- [ ] **Step 3: Implement narrow catalog and provider method**

The catalog owns a `requests.Session` with `trust_env=False`, fixed timeouts,
redirects disabled, identity encoding, bounded bodies, strict duplicate-safe
JSON, explicit cursor handling, and only these paths:

```text
/trade-api/v2/search/filters_by_sport
/trade-api/v2/series
/trade-api/v2/milestones
/trade-api/v2/events
```

It reuses `schemas.py` parsers and official Series/Milestone/Event provenance.
It groups complete sibling pairs before returning games and hashes the canonical
normalized catalog. Add the live-summary route to the async Sportradar transport:

```python
async def fetch_live_summaries(self) -> TrialCapture:
    return await self._fetch(
        "live_summaries",
        "/tennis/trial/v3/en/schedules/live/summaries.json",
    )
```

- [ ] **Step 4: Run Task 2 tests and verify GREEN**

Run the Task 2 command. Expected: all catalog/provider tests pass.

---

### Task 3: Interactive CLI And Resolution Evidence

**Files:**
- Modify: `inci_tennis_io/shadow_evidence.py`
- Modify: `inci_tennis_runtime/live_shadow_cli.py`
- Modify: `inci_tennis_runtime/live_shadow_collector.py` only if the mapping label must cross the render boundary
- Modify: `tests/tennis_v1/test_shadow_evidence_integrity.py`
- Modify: `tests/tennis_v1/test_live_shadow_cli.py`
- Modify: `tests/tennis_v1/test_live_shadow_collector.py` only for a changed rendering contract

**Interfaces:**
- Adds mutually exclusive `--choose` and explicit identifier modes.
- Adds `stdin` injection to `run_cli(...)`.
- Adds `ShadowResolutionEvidence` and `ShadowEvidenceStore.append_resolution(...)`.

- [ ] **Step 1: Write failing evidence and CLI tests**

```python
def test_choose_lists_ready_rows_reprompts_locally_and_runs_selected_match():
    status = run_cli(["--choose"], stdin=io.StringIO("x\n2\n"), dependencies=fakes)
    assert status == 0
    assert fakes.discovery_calls == 1
    assert fakes.collector_match_id == "sr:sport_event:222222"

def test_zero_ready_or_quit_never_opens_websocket():
    assert run_cli(["--choose"], stdin=io.StringIO("q\n"), dependencies=fakes) == 0
    assert fakes.websocket_opens == 0

def test_resolution_is_first_durable_row_and_is_reaudited():
    store.append_resolution(resolution_evidence())
    assert read_rows(store.ledger_path)[0]["kind"] == "resolution"
```

Tests cover argument exclusivity/defaults, planned calls equal discovery plus
collection, invalid input without rediscovery, EOF/Ctrl-C, stable display,
unavailable rows not selectable, immutable selection fingerprint, provider raw
reference validation, evidence tampering, explicit-mode compatibility, and
`AUTO-MATCHED` dashboard labeling.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.tennis_v1.test_shadow_evidence_integrity tests.tennis_v1.test_live_shadow_cli tests.tennis_v1.test_live_shadow_collector
```

Expected: chooser arguments/evidence types are missing.

- [ ] **Step 3: Implement chooser orchestration and evidence**

Refactor the runtime so chooser discovery and collection share one trial ledger
and provider transport. Preflight `1 + planned_collection_calls` before the
discovery request. Parse the durable live capture, fetch the complete catalog
off the event loop, resolve once, render once, and prompt against the immutable
snapshot. After selection, append the resolution row before opening Kalshi WS.
Manual mode keeps its existing planned-call behavior and evidence shape.

- [ ] **Step 4: Run Task 3 tests and verify GREEN**

Run the Task 3 command. Expected: all chooser/evidence/runtime tests pass.

---

### Task 4: Documentation, Integrity Seals, Full Verification And Git Delivery

**Files:**
- Modify: `README.md`
- Modify: `docs/tennis_v1/README.md`
- Modify: `inci_tennis_io/expert_journal_store.py`
- Modify: `tests/tennis_v1/test_expert_dependency_boundary.py`
- Modify: `requirements.txt` or `pyproject.toml` only if implementation adds a direct dependency

**Interfaces:**
- Documents `python -m inci_tennis_runtime.live_shadow_cli --choose` as the primary operator command.
- Updates package inventories, reviewed imports, and AST/resource digests for every changed sealed file.

- [ ] **Step 1: Add documentation and boundary regression tests**

Assert the new modules have no execution imports, order methods, mutation route
literals, environment proxies, or unstated network dependencies. Document
chooser output, the extra discovery call, strict match rules, unavailable
reasons, and the unqualified identity limitation.

- [ ] **Step 2: Run focused and regression verification**

```bash
python -m unittest \
  tests.tennis_v1.test_shadow_match_chooser \
  tests.tennis_v1.test_kalshi_shadow_catalog \
  tests.tennis_v1.test_sportradar_shadow_async \
  tests.tennis_v1.test_shadow_evidence_integrity \
  tests.tennis_v1.test_live_shadow_cli \
  tests.tennis_v1.test_live_shadow_collector
python tests.py
python -m unittest tests.tennis_v1.test_expert_contracts
python -m unittest tests.tennis_v1.test_expert_dependency_boundary
python -m pip check
git diff --check
```

Record the known pre-existing `tennis_v1/ingress.py` boundary failure without
allowlisting or weakening it. Run all Python verification with bytecode writing
disabled and remove only cache artifacts created by this work.

- [ ] **Step 3: Commit and push**

```bash
git add README.md docs/tennis_v1/README.md \
  docs/superpowers/plans/2026-08-01-interactive-shadow-match-chooser-plan.md \
  inci_tennis_adapters/shadow_match_chooser.py \
  inci_tennis_io/kalshi_shadow_catalog.py \
  inci_tennis_io/shadow_evidence.py \
  inci_tennis_io/sportradar_shadow_async.py \
  inci_tennis_io/expert_journal_store.py \
  inci_tennis_runtime/live_shadow_cli.py \
  inci_tennis_runtime/live_shadow_collector.py \
  sports_discovery.py \
  tests/tennis_v1/test_shadow_match_chooser.py \
  tests/tennis_v1/test_kalshi_shadow_catalog.py \
  tests/tennis_v1/test_sportradar_shadow_async.py \
  tests/tennis_v1/test_shadow_evidence_integrity.py \
  tests/tennis_v1/test_live_shadow_cli.py \
  tests/tennis_v1/test_live_shadow_collector.py \
  tests/tennis_v1/test_expert_dependency_boundary.py
git commit -m "feat: add interactive tennis shadow match chooser"
git push origin feature/live-tennis-shadow-collector
```

Verify local `HEAD` equals `origin/feature/live-tennis-shadow-collector` and the
working tree contains no unintended secret, key, PEM, environment, or cache
file.
