# Inci Tennis v1 Event Core

## Tennis v1 Phase 1 Research-Only Boundary

Tennis v1 Phase 1 is a research-only event core. It captures validated input,
persists canonical evidence, reduces deterministic state, and supports
diagnostic inspection. It does not authorize execution, trading, or provider
transport.

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

## No Live, Demo, or Provider-Network Runtime

Phase 1 has no provider network runtime, no live trading runtime, and no demo
order runtime. The package contains no exchange client, network transport, or
order mutation path. Provider artifacts are local, pinned evidence only.

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

Tennis v1 is not yet runnable against a provider. The production adapter
registry is empty, so preflight cannot currently succeed for any provider in
Phase 1. `provider_manifest.example.json` is schema documentation only: its
identities, paths, dates, quotas, formats, and strata are synthetic disabled
placeholders, and it grants no authority.

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
