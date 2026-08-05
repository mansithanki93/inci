# Inci Tennis v1 Event Core

## Offline two-model pilot

> **PLUMBING_ONLY / NO EDGE EVIDENCE / NO ORDERS**

The Model 1 static exact baseline and Model 2 per-point Bayesian pilot run only
against local canonical artifacts and replay records. The comparison is a
deterministic research plumbing check; it makes no profitability claim and has
no live transport or order authority.

Write a clearly synthetic local example (the target directory must not exist):

```bash
python -m inci_tennis_runtime.two_model_pilot_cli \
  --write-example /absolute/path/two-model-synthetic
```

Run the offline comparison (the output must not exist):

```bash
python -m inci_tennis_runtime.two_model_pilot_cli \
  --replay /absolute/path/two-model-synthetic/events.jsonl \
  --static-artifact /absolute/path/two-model-synthetic/static.json \
  --dynamic-artifact /absolute/path/two-model-synthetic/dynamic.json \
  --output /absolute/path/comparison.jsonl
```

## Tennis v1 Phase 1 Research-Only Boundary

Tennis v1 Phase 1 is a research-only event core. It captures validated input,
persists canonical evidence, reduces deterministic state, and supports
diagnostic inspection. It does not authorize execution, trading, or provider
transport inside the event core.

## WAL Is Canonical Tennis Input Evidence

The Tennis v1 write-ahead log (WAL) is the canonical record of admitted Tennis
input. Durable RAW records, their deterministic DERIVED records, session
control records, and the trace bind a session to the exact evidence that was
observed. Dashboard snapshots are disposable views and never replace or
modify WAL evidence.

## Why v6 CSV and Replay Stay Separate

The legacy v6 CSV logger and replay path are intentionally not imported into
Tennis v1. Their data model and timing assumptions are different from the
canonical event-core contract. Keeping the implementations separate prevents
legacy parsing, simulation, or execution behavior from becoming an implicit
authority over Tennis v1 evidence.

## Diagnostic Scan, Exact Replay, and Research Evaluability

A diagnostic scan describes readable records and integrity issues without
changing bytes. Exact replay means the complete canonical event sequence can
be reproduced under its recorded contracts. Those facts do not establish a
profitable strategy or validated research result: throughout Phase 1,
`research_evaluable=false`. Later qualification work must define and satisfy
the separate research-evaluability gate.

## Sealed Event Core Versus the Outer Shadow Collector

The sealed `tennis_v1` event core still has no provider network runtime, live
trading runtime, demo order runtime, exchange client, or order mutation path.
Provider artifacts admitted to that core remain local, pinned evidence only.

The explicitly separate outer command starts with the complete current-day
Kalshi `Tennis` / `Games` census:

```bash
python -m inci_tennis_runtime.live_shadow_cli --choose
```

Chooser mode needs the read-only Kalshi credentials but not a Sportradar key.
If `SPORTRADAR_API_KEY` and trial quota are available, one closed-ledger live
summary attempt may annotate the immutable Kalshi census. Missing/invalid
credentials, zero quota, 429, transport failure, malformed/stale/empty
provider data, unsupported coverage, or no exact match downgrades a valid game
to `PRICE_ONLY`; none may fabricate `VERIFIED`. Kalshi catalog incompleteness
or schema failure halts because Kalshi is the primary census.

Each eligible game has one state:

- `VERIFIED` is selectable and means only one fresh, exact, live
  observation-source correlation with degree one on both graph sides.
- `PRICE_ONLY` is selectable and has no provider/score observation stream.
- `CONFLICT` is visible, unnumbered, and non-selectable because identity is
  contradictory or ambiguous.

Both Markets must be structurally valid active binary $1 non-MVE siblings with
distinct player subtitles, but zero depth and one-sided depth remain valid.
The chooser prints `book=empty` or `book=one_sided`. An empty-book price
dashboard can remain quiet while the collector still receives and durably
processes frames; candidate prices appear only after the aggregate two-ticker
snapshot barrier. Quiet output alone does not prove a frozen collector.

The exact collection banners are:

```text
READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / NO SIGNALS / NO P&L / NO ORDERS
READ ONLY / PRICE ONLY / NO SCORE FEED / NO SIGNALS / NO P&L / NO ORDERS
```

Invalid choices reprompt without another network call. `Q` or EOF closes any
provider-discovery ledger and opens no collection socket or evidence session.
An interrupt at the prompt has the same no-start behavior. During collection,
Ctrl-C, SIGTERM, and SIGHUP converge on the durable terminal path before the
command reports stop; an unclean/missing terminal causes later audit to fail
closed.

Only an allowlisted provider transport/parser failure, attested by the
verified collector after durable close, may create a fresh linked price-only
session for the remaining duration. No provider score, reducer, book, or
generation state is carried forward, and earlier verified rows are not
relabeled. Kalshi, catalog, projection, identity, evidence, terminal, cleanup,
unknown, or falsely provider-coded failures halt without failover.
If verified failover leaves fewer than ten seconds, the collector halts instead
of opening a price-only session.

Provider quota is staged. Discovery reserves at most one call. A selected
`PRICE_ONLY` row reserves no collection-provider calls. A selected `VERIFIED`
row performs a second preflight for the planned summary/timeline collection
calls before either provider collection or the Kalshi WebSocket starts.

Collection ledgers named `session-<uuid>.jsonl` and private raw Kalshi frames
live under `~/.local/state/inci/tennis-shadow/`; provider trial ledgers and raw
discovery captures live under `~/.local/state/inci/sportradar-trial/`. Both
locations are derived from the OS account rather than repository settings.
The append-only chains bind session/terminal rows and referenced raw bytes.
They do not detect coherent deletion or rollback of the whole state root, so
immutable external archival is required for that threat.

Post-match settlement is separate and accepts an absolute completed session
ledger path:

```bash
python -m inci_tennis_runtime.shadow_settlement_cli \
  /absolute/path/to/session-<uuid>.jsonl
```

The command uses only fixed public Kalshi Market GETs and prints `pending`,
`final`, or `conflict`. An initial reconciliation with either Market in a
recognized non-final status is `pending` and writes nothing. After both Markets
are finalized, a syntactically admitted `void`, semantically invalid, or
noncomplementary result is a durable `conflict`. Only complementary finalized
binary results yield `final`. Raw Market responses and the label row are
append-only. Any changed normalized evidence after a durable final appends a
permanent conflict that supersedes the prior row without erasing it. Exit codes
are `0` for a result or help, `1` for a halt, `2` for usage, and `130` for an
interrupt. The exact sidecar root is
`Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/inci/tennis-shadow-settlement'`,
shown to operators as `~/.local/state/inci/tennis-shadow-settlement`. It is
explicitly not `HOME`-configurable.

The hybrid and settlement packages have no signal, strategy, fee, P&L,
executor, order, portfolio, or expert-synchronization authority. `VERIFIED`
does not qualify a provider/product, establish trusted synchronization, or
authorize execution. See the [Kalshi-first hybrid
design](../superpowers/specs/2026-08-01-kalshi-first-tennis-hybrid-design.md).

## Reviewed Canonical AST Closure

The Task 7 dependency boundary is closed over the exact reviewed Python
program, not an open-ended claim that arbitrary Python flow can be proven
safe. Every recursively discovered `tennis_v1` Python path and its entire
canonical module AST must match the frozen review table. Test-only positive
fixtures have a separate exact path-and-AST table. Unknown paths, missing or
added modules, path spoofing, and any unreviewed behavior change fail before
semantic exceptions are considered. The semantic dependency scanner remains
active as defense in depth.

Canonicalization uses `ast.dump` with fields included and source locations
excluded, so comments and layout do not affect the digest while literals,
annotations, defaults, decorators, comprehensions, and all other semantic AST
nodes do. The current digest table is frozen under CPython 3.14.5. A Python
minor-version change requires an explicit re-review and regenerated table
because Python's AST schema is versioned.

Entitlement Task 5 must rerun this boundary after its final files land. Any
Task 5 addition or behavior change under `tennis_v1` requires explicit
re-review of the recursive path inventory, the affected whole-module AST
digest, the semantic scan, and the complete boundary suite; a digest update
alone is not approval.

## Local entitlement preflight and external artifacts

The qualified Tennis v1 event-core path is not yet runnable against a
provider. The production adapter registry is empty, so preflight cannot
currently succeed for any provider in Phase 1.
`provider_manifest.example.json` is schema documentation only: its identities,
paths, dates, quotas, formats, and strata are synthetic disabled placeholders,
and it grants no authority. Running the separate unqualified shadow collector
does not satisfy, bypass, or modify this entitlement gate.

Actual provider manifests, permission evidence, qualification traces,
credentials, and reviewed trial terms remain outside Git and independently
digest-pinned. The local config pins the exact external manifest digest; that
manifest separately pins the permission, terms evidence, qualification, and
trace bytes. The committed example never substitutes for those artifacts and
does not imply provider permission.

No trial starts, auto-upgrades, subscribes, renews, or becomes paid because a
manifest exists or preflight runs. Before any provider trial, its exact terms
and product tier require separate review. Trial access never establishes
publication permission or permission to use data after its granted window.

Access, analysis, qualification, and physical-retention deadlines are
separate and fail closed. Access expiry blocks new provider data use
immediately. Later analysis requires separately granted post-expiry
permission and a current analysis deadline. Qualification ends at
`qualified_until`, cannot exceed its verified analysis window or the 30-day
freshness limit, and trial qualification remains capped by trial access.
Physical retention uses one session deadline, startup recovery, and a
process-wide halt if any due deletion cannot be proven.

Preflight is read-only, touches no state root, creates no filesystem output
or artifact, and is not runtime startup. Even an eligible diagnostic grants
no network, session, retention, or WAL authority. A runtime would have to
reload and re-evaluate the same pinned external evidence and complete locked
physical-retention startup before any provider operation or persistence
could be considered.
