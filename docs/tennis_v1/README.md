# Inci Tennis v1 Event Core

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

The repository now also contains an explicitly separate outer command,
`inci_tennis_runtime.live_shadow_cli`. Its primary interface is:

```bash
python -m inci_tennis_runtime.live_shadow_cli --choose
```

The chooser discovers live Sportradar rows, scans the complete current-day
official Kalshi `Tennis` / `Games` inventory, lists numbered `READY TO COLLECT`
rows and unnumbered `UNAVAILABLE` reasons, and starts the existing collector
only after a valid number is selected. Its duration and poll defaults are 600
and 10 seconds. The Sportradar quota reservation is the one discovery call
plus `1 + (duration_seconds - 1) // poll_seconds` collection calls: 61 calls at
the defaults. Invalid input reprompts without rediscovery; `Q`, EOF, Ctrl-C, or
zero ready rows exit without opening the Kalshi WebSocket or creating a
collection evidence session.

A selectable row requires exactly two supported active binary $1 non-MVE
match-winner contracts, distinct non-placeholder player names, an exact
unordered player-pair match after only Unicode NFKC, whitespace collapse, and
case folding, an inclusive 900-second start tolerance, and degree one on both
sides of the match graph. No fuzzy matching, ticker parsing, event-title
parsing, nickname inference, or token dropping is permitted. Only provider
rows whose lifecycle is `live` are selectable.

Automatic mode is visibly **READ ONLY / AUTO-MATCHED / UNQUALIFIED / NO
ORDERS**. The retained explicit-ID diagnostic mode is visibly
`OPERATOR-SUPPLIED / UNVERIFIED`; the modes are mutually exclusive. Selection
evidence binds the chosen identities to the raw Sportradar discovery capture,
its hash, the resolver version, the canonical chooser snapshot digest, and the
canonical normalized Kalshi catalog digest. The Kalshi digest is not a raw
response archive, so the original Kalshi response bytes cannot be reconstructed
from it. Display-name/start-time correlation therefore remains an unqualified
outer-layer observation, not a trusted cross-provider identity.

Neither mode admits observations into the sealed event core, upgrades them
into qualified evidence, grants execution authority, or exposes an order
route. See the [live shadow collector design and operations
record](../superpowers/specs/2026-08-01-live-tennis-shadow-collector-design.md)
and the [interactive chooser
design](../superpowers/specs/2026-08-01-interactive-shadow-match-chooser-design.md).

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
