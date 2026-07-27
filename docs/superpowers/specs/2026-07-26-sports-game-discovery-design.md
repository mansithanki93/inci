# Sports Game Discovery Design

## Goal

Allow an operator to select one or more sports for each paper-research
session while Inci dynamically discovers that day's active game contracts
across all leagues, ranks them by executable market quality, and monitors
the best ten individual contracts.

The implementation must not hardcode sport names, league names, event-title
keywords, or ticker prefixes. Kalshi's structured metadata is the source of
truth.

## Operator Interface

The primary command is:

```bash
python bot.py --sports Tennis,Basketball
```

`--sports` accepts comma-separated, case-insensitive names. Inci converts
them to the canonical names returned by Kalshi.

```bash
python bot.py --list-sports
```

`--list-sports` prints the currently supported sport names from Kalshi's
`GET /search/filters_by_sport` response and exits without creating a
research session. The composite UI entry `All sports` is displayed but is
not accepted as a session sport; the operator must name the sports they
want.

An explicit configured ticker list remains available for controlled
research. Explicit tickers and `--sports` are mutually exclusive. With
neither supplied, startup fails with usage guidance instead of falling back
to title keywords. Explicit tickers are resolved through Market, Event,
Series, and Milestone metadata and must still prove that they are today's
Games contracts; unresolved or non-Game tickers fail startup.

## Discovery Architecture

### 1. Validate sports dynamically

Fetch `GET /search/filters_by_sport` and strictly validate:

- `filters_by_sports` is an object.
- `sport_ordering` is a list of unique nonempty strings.
- Every requested sport resolves case-insensitively to exactly one canonical
  entry.
- Every selected sport exposes the `Games` scope.
- Every selected sport supplies at least one competition whose scopes
  include `Games`.

Unknown or ambiguous sport names fail startup and print the canonical
choices. No sport or league value is embedded in source code.

### 2. Build the official series-to-sport map

Fetch `GET /series?category=Sports`. Strictly validate every series row and
build:

```text
series_ticker -> canonical sport tag
```

A series is eligible only when it has exactly one canonical sport tag that
matches a selected sport. Missing, unknown, or ambiguous tags are skipped
and counted loudly.

### 3. Discover today's game milestones

Use the computer's local timezone to define the half-open session window:

```text
[local midnight today, local midnight tomorrow)
```

Print the timezone and both boundaries at startup, and convert them to UTC
for API comparison.

For every selected sport, take the competition names whose advertised
scopes include `Games`. Fetch bounded pages for each competition from:

```text
GET /milestones
    ?category=Sports
    &competition=<official competition name>
    &minimum_start_date=<UTC session start>
    &limit=500
```

Strictly validate milestone envelopes, cursors, timestamps, identifiers,
`details`, and event-ticker lists. Retain only milestones whose
`start_date` lies inside the session window. Deduplicate milestone IDs and
event tickers across competition queries. Competition names come entirely
from the live filters response; selecting a sport never embeds its leagues
in source code.

The Games event is selected as follows:

1. Use the nonempty `details.main_game_event_ticker` when present.
2. Otherwise use the sole `primary_event_tickers` entry when there is
   exactly one.
3. Otherwise skip the milestone as Games-ambiguous and count it loudly.

This rule excludes spread, total, set-winner, exact-score, futures, and
other prop events without interpreting their titles.

Resolve each Games event to a series by finding the unique longest official
series ticker followed by the event ticker's `-` separator. If no unique
official series matches, skip and count the event. The resolved series tag
determines the sport; milestone titles never do.

### 4. Fetch eligible game events and contracts

Group the retained Games event tickers by their resolved series ticker.
For each unique series, request bounded pages from:

```text
GET /events
    ?series_ticker=<series>
    &status=open
    &with_nested_markets=true
    &limit=200
```

Use conservative GET pacing and the existing bounded 429 backoff. Retain
only exact event-ticker matches from the milestone set.

Every nested market passes through the same strict current Market parser
used by direct quote reads. A candidate contract must be:

- binary and non-MVE;
- `active`;
- inside a Games event scheduled for the local day;
- equipped with a valid bid and ask;
- equipped with positive bid and ask depth; and
- no wider than the configured `max_spread`.

Malformed metadata, envelopes, cursors, or binary markets fail startup.
Recognized unsupported products and explicitly ambiguous Games mappings
are skipped and reported by category.

### 5. Rank and select ten contracts

Rank all eligible individual contracts across all selected sports with this
deterministic key:

1. larger fillable two-sided depth, where
   `fillable_depth = min(bid_size, ask_size, contracts_per_trade)`;
2. smaller bid/ask spread;
3. larger uncapped two-sided depth,
   `min(bid_size, ask_size)`;
4. earlier scheduled start time; and
5. ticker ascending as the final stable tie-breaker.

Select at most `max_monitored_markets`, whose validated production default
remains 10. Multiple outcome contracts from one game may occupy multiple
slots because the operator explicitly chose a ten-contract rather than a
ten-game limit.

Print one selection line per contract containing sport, league when
available, game title, ticker, start time, bid, ask, spread, and two-sided
depth. Also print totals for pages, rows, candidates, skips, and selected
contracts.

Selection occurs once at session startup. Rotation and automatic
replacement are outside this change; restarting Inci creates a new
selection for the current session.

## Research Provenance

Every quote and trade must retain:

- the canonical selected-sports list in the configuration fingerprint;
- canonical sport;
- league when Kalshi supplies it;
- series ticker;
- milestone ID;
- event ticker; and
- scheduled start timestamp.

The research CSV schema advances from v5 to v6. Replay and analysis accept
only complete, provenance-consistent v6 sessions and must never combine v5
and v6 rows. Existing v5 files remain untouched and can be evaluated only
with their archived v5 code fingerprint.

Analysis reports results separately by sport in addition to the overall
portfolio result. An overall profitable result cannot be used as evidence
for a sport whose own held-out result is non-positive.

## Failure and Safety Behavior

- Discovery is public and unauthenticated; it never needs the API key or
  private key.
- Every discovery collection is bounded and detects missing, non-string, or
  repeated cursors.
- Because the requirement is the best ten overall, a malformed series
  inventory or truncated milestone/event inventory fails startup cleanly
  rather than claiming that a partial inventory contains the best ten.
- Startup discovery failures write a durable `session_halt` record and
  return nonzero without swallowing `KeyboardInterrupt` or `SystemExit`.
- Orders, fills, and positions remain exhaustively paginated and fail
  closed.
- Live and demo order execution remain unconditionally disabled.
- This change does not add live scores, play-by-play data, periodic market
  rotation, or any claim that the retracement strategy is profitable in a
  particular sport.

## Verification

Tests use complete documented response fixtures and no production network
calls. Required regressions cover:

- a sport introduced only through API metadata works without source edits;
- canonical and case-insensitive sport selection;
- invalid sports fail with canonical choices;
- a Wimbledon soccer event is included for Soccer and excluded for Tennis;
- Games-event selection excludes spreads, totals, set winners, and props;
- local-day boundaries, including a daylight-saving transition;
- ambiguous or malformed series and milestone metadata;
- malformed and repeated cursors;
- incomplete discovery fails instead of ranking partial inventory;
- ranking order and deterministic tie-breakers;
- the global ten-contract cap across multiple sports;
- sport/league/series/milestone provenance through logging, replay, and
  analysis;
- per-sport held-out reporting;
- unchanged strict portfolio pagination; and
- unchanged live/demo execution locks.

The full local suite must pass, followed by one unauthenticated read-only
production discovery check. No production order endpoint may be probed.
