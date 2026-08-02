# Kalshi-First Tennis Hybrid Evidence Design

Date: 2026-08-01

## Outcome

The tennis shadow command starts from Kalshi's current-day Tennis/Games
inventory instead of requiring Sportradar to list the same match. Every
structurally eligible two-contract game appears even when Sportradar has no
coverage and even when one or both books are empty. The operator selects one
game and Inci collects either synchronized score-plus-price evidence or
Kalshi-only price evidence.

This feature remains read-only research. It creates no recommendation, signal,
P&L, paper order, demo order, or live order. `VERIFIED` means only that a fresh
provider row was correlated uniquely to the selected Kalshi game for
observation. It is not provider qualification, product qualification, trusted
synchronization, or execution authority.

The v1 collection unit is one selected game with exactly two candidate
match-winner contracts. The chooser lists every eligible game, but one command
does not subscribe to all games concurrently. Multi-game collection is a
separate capacity and quota problem and is outside this change.

## Non-Negotiable Labels

The resolver emits exactly one state per valid Kalshi game:

- `VERIFIED`: one fresh live provider row matches the unordered player pair
  exactly, the starts differ by at most 900 seconds inclusive, and both sides
  of the match graph have degree one.
- `PRICE_ONLY`: the Kalshi game is collectable, but no trustworthy live score
  correlation exists. This includes absent credentials, exhausted quota,
  provider outage, empty provider results, known unsupported coverage, and no
  exact match.
- `CONFLICT`: evidence disagrees or is ambiguous. Examples are duplicate match
  candidates, multiple edges, a terminal provider row for an active Kalshi
  game, inconsistent source identity, or duplicate Kalshi identities. A
  conflict is displayed but cannot be selected.

The dashboard literals are:

```text
READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / NO SIGNALS / NO P&L / NO ORDERS
READ ONLY / PRICE ONLY / NO SCORE FEED / NO SIGNALS / NO P&L / NO ORDERS
```

No code may translate these states into the expert provider qualification
enums, `TRUSTED_SYNCHRONIZED`, strategy decisions, or order authority.

## Source Authority And Product Semantics

Kalshi is authoritative only for its own event identity, market identity,
order books, lifecycle, and final settlement. A score provider is authoritative
only for the score observations it lawfully supplies.

The catalog preserves the official Kalshi provenance used to find a game:

- canonical sport `Tennis`;
- scope `Games`;
- queried competition;
- Series ticker;
- Milestone ID and optional Milestone league;
- Event ticker and scheduled start;
- two Market tickers and their YES player subtitles;
- each Market's initial bid, ask, bid depth, and ask depth as nullable
  fixed-point strings;
- initial book state: `two_sided`, `one_sided`, or `empty`.

The catalog calls the pair `candidate_match_winner`, not proven
`match_winner`. Two binary $1 contracts with player subtitles and exactly two
siblings are sufficient for unqualified collection, but not for execution or
promotion into the expert match-binding contract. Product and settlement-rule
qualification still require the separate pinned evidence contract already
defined by the expert system.

Instantaneous liquidity never controls census membership. Both Markets must be
active binary non-MVE $1 contracts with distinct non-placeholder player names,
but zero depth and one-sided depth are valid initial book states. Scalar,
multi-leg, inactive, wrong-cardinality, duplicate-player, or malformed events
are excluded with counted stable reasons rather than disappearing silently.

## Exchange-First Catalog

`KalshiShadowCatalogTransport.discover_tennis_catalog()` returns an immutable
`KalshiShadowCatalogSnapshot` containing:

```python
games: tuple[KalshiShadowGame, ...]
excluded: tuple[KalshiCatalogExclusion, ...]
catalog_sha256: str
```

`discover_tennis_games()` remains as a compatibility wrapper returning
`(snapshot.games, snapshot.catalog_sha256)`.

The canonical digest covers every retained provenance field, market identity,
player name, initial book state, and every exclusion count/reason. Input page
order cannot change it. Later book movement cannot change it.

The transport stays public GET-only with fixed paths, no environment proxies,
bounded bodies, duplicate-key rejection, bounded pagination, redirect refusal,
and sanitized diagnostics. Catalog incompleteness or schema drift halts the
chooser because the exchange census is the primary boundary.

Every exact Series row whose structured tag is `Tennis` is scanned. The
Event's explicit `series_ticker` must agree with the Series query. Deriving a
Series by matching an Event ticker prefix is removed; ticker and title text are
never classification inputs.

## Optional Provider Discovery

Chooser mode requires only `KALSHI_API_KEY_ID` and
`KALSHI_PRIVATE_KEY_PATH`. `SPORTRADAR_API_KEY` becomes optional in chooser
mode and remains mandatory for the existing explicit synchronized mode.

The chooser fetches the Kalshi catalog first. If a Sportradar key and one trial
call are available, it performs exactly one quota-ledger-backed live-summary
capture. Provider credential absence, zero quota, 429, transport failure,
schema failure, stale response, or an empty response does not invalidate the
Kalshi catalog. It produces an explicit provider discovery state and makes
otherwise valid games `PRICE_ONLY`.

Any completed provider attempt is closed cleanly in the trial usage ledger.
The provider discovery call and its raw capture are never represented as score
evidence in a price-only observation.

The provider coverage registry is declarative and closed. It stores a version,
provider ID, observation authority, and explicit supported/unsupported tour
rules derived only from preserved source metadata. It has no wildcard
qualification. Unknown, ambiguous, doubles, exhibition, or unregistered tour
metadata cannot become `VERIFIED`; it remains `PRICE_ONLY` unless the registry
has an explicit observation-only route and the exact resolver also succeeds.

Because production tour strings have not yet been observed exhaustively, the
initial registry must prefer `unknown -> PRICE_ONLY` over guessed ticker or
title parsing. Tests use explicit ATP, Challenger, WTA, ITF, doubles,
exhibition, unknown, and ambiguous fixtures. Adding a real route later requires
a registry edit, tests, and a new registry digest.

The Sportradar live-summary projection preserves structured sport, category,
competition, type, gender, and level IDs/names from
`sport_event_context`. Exact category IDs and competition type—not display
name fragments—are the registry inputs. Singles is mandatory. The initial
reviewed Sportradar categories are ATP `sr:category:3`, WTA
`sr:category:6`, Challenger `sr:category:72`, and WTA 125K
`sr:category:871`; ITF and exhibition categories are explicitly denied.
Unknown IDs default to price-only because Sportradar documents that category
values may change.

One malformed provider row becomes a diagnostic and cannot remove unrelated
Kalshi games. A malformed top-level envelope or incomplete provider pagination
makes the provider snapshot unavailable, so all otherwise valid Kalshi games
remain price-only.

## Pure Kalshi-Anchored Resolution

Resolution is deterministic and Kalshi-anchored:

1. Validate and de-duplicate the complete Kalshi game census.
2. Validate provider rows and reject incomplete pagination or malformed stable
   identifiers.
3. Normalize player names only with Unicode NFKC, whitespace collapse, and
   case folding. Fuzzy matching, nickname tables, edit distance, token
   dropping, ticker parsing, and title parsing are prohibited.
4. Build edges only for exact unordered player pairs inside the inclusive
   900-second start window and an explicitly permitted observation route.
5. Classify every Kalshi game exactly once as `VERIFIED`, `PRICE_ONLY`, or
   `CONFLICT`.
6. Reorder verified Market tickers to provider home/away order. Price-only
   choices retain neutral `player_a`/`player_b` order from the canonical
   catalog.
7. Sort by scheduled start, Event ticker, and state, then hash the full result.

Provider-only rows are informational exclusions. They never create a choice
without a Kalshi game.

## Operator Flow And Quota

The primary command remains:

```bash
python -m inci_tennis_runtime.live_shadow_cli --choose
```

Output has three sections. `VERIFIED` and `PRICE ONLY` rows share one stable
numbering sequence and are selectable. `CONFLICT / EXCLUDED` rows are
unnumbered and not selectable. Each price-only row prints its reason and
initial book state so an empty book is visible rather than mistaken for a
freeze.

Quota is staged:

1. Provider discovery reserves at most one call.
2. Selecting `PRICE_ONLY` reserves no further provider calls.
3. Selecting `VERIFIED` performs a second preflight for the planned summary
   and timeline collection calls.

Invalid input reprompts locally without rediscovery. `Q`, EOF, or interrupt
closes any provider-discovery ledger cleanly and opens no collection socket.
Before opening the WebSocket, the CLI revalidates that the selected Event and
two Market identities still match the immutable snapshot.

## Separate Price-Only Collector

`PriceOnlyShadowCollector` is additive. The existing
`LiveShadowCollector` and its non-null Sportradar invariants remain unchanged.
The new collector consumes only:

- one Event ticker;
- two neutral player names and two Market tickers;
- the existing exact-scope Kalshi read-only WebSocket transport;
- the existing pure two-ticker projector;
- a price-only evidence store surface;
- clocks, stop callback, pause, and renderer.

It persists and fsyncs each raw Kalshi frame and capture receipt before parsing
or rendering it. Candidate prices appear only after the existing aggregate
snapshot barrier. Gap, duplicate, out-of-order, parser error, disconnect, and
reconnect clear both books. Recovery retains the bounded 1/2/4-second policy
and generation isolation.

It never fabricates score, server, provider age, match completion, winner,
signal, P&L, or recommendation fields. Duration, operator interrupt,
cancellation, and halt are its only terminal meanings; market settlement is a
separate reconciler.

If a verified synchronized collection later fails for a provider-specific
reason, the CLI closes that session and its trial ledger, then starts a new
linked price-only session for the remaining duration. Frames before the
barrier remain in the verified session; frames after failure are explicitly
price-only. There is no retroactive relabeling and no carried-forward score.
Kalshi transport or evidence-integrity failures still halt rather than
fail over.

## Price-Only Evidence Grammar

Existing synchronized schemas remain byte-for-byte compatible and retain
`trust="unqualified_shadow"`. Price-only sessions use four additive row kinds
with exact kind-to-trust mapping:

1. `price_only_session`, schema
   `inci-tennis-price-only-session-v1`, `trust="PRICE_ONLY"`.
2. `price_only_kalshi_capture`, schema
   `inci-tennis-price-only-kalshi-capture-v1`, `trust="PRICE_ONLY"`.
3. `price_only_observation`, schema
   `inci-tennis-price-only-observation-v1`, `trust="PRICE_ONLY"`.
4. `price_only_terminal`, schema
   `inci-tennis-price-only-terminal-v1`, `trust="PRICE_ONLY"`.

The first row binds selected wall and monotonic clocks, Event, player names,
two Market tickers, scheduled start, complete catalog provenance, initial book
state, provider discovery state/reason, optional all-or-none provider discovery
raw reference, catalog digest, resolver digest, resolver version, and registry
digest. It fixes `authority_scope="observation_only"`,
`execution_authorized=false`, and `score_feed="none"`.

Price observations contain only clocks, Event/Market identities, prior raw
Kalshi receipt, generation, sequence, age, book status, two candidate books,
reason, and cumulative frame count. Provider, score, signal, P&L,
recommendation, and order fields are not optional; they are forbidden by the
exact field set.

The terminal binds Event/tickers, frame count, fixed mapping mode, and the
first-row SHA-256. Audit accepts exactly:

```text
price_only_session
(price_only_kalshi_capture | price_only_observation)*
price_only_terminal
```

Mixed synchronized and price-only kinds are corruption. A candidate
observation requires a prior same-session receipt, generation, sequence, and
all book values. A noncandidate observation hides all book values. The
terminal frame count equals the durable capture-row count. Missing, altered,
or orphan raw files; first-row deletion; receipt-before-reference violation;
field injection; identity drift; terminal mismatch; chain rewrite; or unclean
shutdown refuses the next session.

The existing limitation remains explicit: a local hash chain cannot detect
deletion or rollback of the entire state root. Immutable external archival is
required before these artifacts could support promotion.

## Finalized Settlement Labels

Post-match truth is derived only from Kalshi's finalized Market state, never
from a 0/100 price, disappearance, last trade, or provider winner. A separate
read-only settlement command consumes a completed price-only or verified
session and performs fixed public GETs for both selected Market tickers.

`active`, `inactive`, `closed`, `determined`, `disputed`, and `amended` remain
`PENDING`. Only two `finalized` rows with populated settlement timestamps and
complementary binary results may emit `FINAL`. Exactly one YES winner identifies
the winning Market/player. Both YES, both NO, mismatched Event identities,
scalar results, or incompatible settlement values emit `CONFLICT`. A void or
cancel result is non-directional and never counted as a winner.

Raw settlement responses are persisted privately. The append-only label
sidecar binds the source session ID, selection-row digest, source terminal
digest, both raw-response hashes, result, winning ticker/player or null,
settlement timestamps, and label time. Re-running is idempotent for identical
evidence and appends a superseding row rather than overwriting if Kalshi later
amends a non-final state. A finalized conflict cannot be silently replaced.

The current Kalshi contract is based on the official Market Lifecycle and
Market REST documentation:

- https://docs.kalshi.com/getting_started/market_lifecycle
- https://docs.kalshi.com/api-reference/market/get-markets

No portfolio settlement endpoint or order-capable client is used.

## Failure And Security Boundaries

- Catalog schema or completeness failure halts because Kalshi is primary.
- Provider failures downgrade only provider availability; they never fabricate
  `VERIFIED` and never authorize a signal.
- `CONFLICT` is never selectable and cannot disappear because a provider row
  later vanishes from the same immutable snapshot.
- Every selected ticker is validated and subscribed exactly once, or startup
  refuses before partial collection.
- Catalog and settlement transports expose only literal public GET paths.
- WebSocket credentials retain their existing read scope and exact two-ticker
  subscription.
- Static dependency tests forbid imports of `signals`, `fees`, `strategy`,
  `engine`, `executor`, order, portfolio, or expert synchronization modules
  from price-only code.
- Automated tests use injected responses and consume zero live Kalshi or
  Sportradar calls.

## Acceptance Matrix

Tests must first fail, then cover:

- Kalshi games still appear with empty or one-sided books.
- Liquidity changes never alter census membership; the immutable snapshot
  still records and hashes the quotes observed at discovery time.
- No provider key, zero quota, empty provider result, 429, network error,
  malformed response, and stale response all yield selectable `PRICE_ONLY`.
- Exact reversed player order and the inclusive 900-second boundary verify;
  901 seconds, duplicate names, rematches, reschedules, multiple edges,
  terminal/live mismatch, and malformed stable IDs do not.
- ATP, Challenger, WTA, ITF, doubles, exhibition, unknown, and ambiguous
  registry fixtures route without wildcard fallback.
- `VERIFIED` carries observation-only/no-execution literals and cannot enter
  provider qualification or trading code.
- Chooser numbering is deterministic; conflict rows cannot be selected;
  reprompting causes no network call.
- Price-only startup does not construct a Sportradar transport or trial ledger,
  does not require a Sportradar key, and writes the session row before opening
  the socket.
- Persist-before-project, aggregate snapshot barrier, gap/resnapshot,
  reconnect generation reset, raw-write failure, cancellation during every
  durability boundary, and close-error precedence.
- Provider failure after a verified start creates a new linked price-only
  session with no carried score and no retroactive row mutation.
- Legacy synchronized evidence reopens unchanged; price-only grammar rejects
  trust/kind mixing, raw tampering, orphan files, field injection, first-row
  deletion, terminal mismatch, and unclean sessions.
- Settlement stays pending until finalized, accepts exactly one complementary
  winner, rejects conflict/void as directional labels, persists raw responses,
  and is idempotent.
- Full sealed-boundary tests prove no execution capability and no live network
  activity in tests.

## Delivery

Implementation is complete only when focused tests, the root 202-test legacy
suite, expert contract tests, clean sealed-boundary tests, `pip check`,
`git diff --check`, secret/cache scans, and remote-head verification pass. The
known pre-existing `tennis_v1/ingress.py:new_package_import_forbidden` boundary
finding is reported but not weakened or allowlisted.
