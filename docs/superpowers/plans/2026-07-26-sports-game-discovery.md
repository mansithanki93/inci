# Sports Game Discovery Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: use test-driven development while
> implementing this plan. This plan changes discovery, startup, and research
> evidence contracts. Do not edit source files until the operator approves
> this plan.

**Goal:** Let the operator select one or more Sports for a paper session,
discover every current-day Games event across all live Kalshi competitions
for those Sports, rank all eligible individual contracts, and monitor the
best ten overall.

**Architecture:** Add a focused `sports_discovery.py` domain layer between
the strict API/schema layer and `PriceFeed`. Discovery uses only Kalshi's
Sports filters, series tags, milestones, events, and current Market fields.
It never classifies by titles, keywords, or hardcoded sport/league/ticker
lists. Successful discovery produces immutable per-ticker provenance before
the v6 research logger and its configuration fingerprint are created.

**Technology:** Python standard library (`argparse`, `dataclasses`,
`datetime`, `zoneinfo`, `Decimal`), existing `requests` client, existing
single-file regression suite in `tests.py`.

**Source contract references:**

- `GET /search/filters_by_sport` returns the ordered Sports map and its
  competition/scope filters.
- `GET /series?category=Sports` returns the complete Sports series list; the
  current documented response is non-cursor-paginated.
- `GET /milestones` supports `category`, `competition`,
  `minimum_start_date`, `limit <= 500`, and cursor pagination.
- `GET /events` supports `series_ticker`, `status`,
  `with_nested_markets`, `limit <= 200`, and cursor pagination.
- Nested Markets continue through the existing `parse_market` contract.

## Guardrails

- Preserve the current uncommitted bounded-discovery/rate-limit work in
  `README.md`, `bot.py`, `config.py`, `kalshi_client.py`, `market_data.py`,
  `research_log.py`, `schemas.py`, and `tests.py`.
- Use the word **Sports** in operator-facing names and messages; do not call
  the feature "multisport."
- Test first for every behavior change. Run one focused test and see the
  intended failure before writing its implementation.
- Do not probe any production order endpoint. Public discovery validation is
  unauthenticated and read-only.
- Keep live and demo execution unconditionally disabled.
- Do not weaken strict private portfolio pagination or the executor's
  independent real-order lock.
- Do not silently rank incomplete inventories. A malformed/truncated series,
  milestone, or event inventory is a startup failure.
- Treat a structurally valid Series row outside category `Sports`, or one
  with null/missing tags, as an ineligible row that is skipped and counted
  loudly. The live category-filtered endpoint currently returns both cases.
  Malformed row types and fields remain fatal.
- Do not rewrite or delete v5 logs. Current code rejects them and directs the
  operator to the archived v5 code.

## Target Interfaces

Create these immutable domain values in `sports_discovery.py`:

```python
@dataclass(frozen=True)
class ContractProvenance:
    sport: str
    league: str | None
    series_ticker: str
    milestone_id: str
    event_ticker: str
    scheduled_start_ts: float


@dataclass(frozen=True)
class SelectedContract:
    ticker: str
    title: str
    game_title: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    provenance: ContractProvenance


@dataclass(frozen=True)
class DiscoveryResult:
    contracts: tuple[SelectedContract, ...]
    selected_sports: tuple[str, ...]
    local_timezone: str
    session_start_utc: float
    session_end_utc: float
    stats: Mapping[str, int]

    @property
    def tickers(self):
        return tuple(contract.ticker for contract in self.contracts)

    @property
    def provenance_by_ticker(self):
        return {
            contract.ticker: contract.provenance
            for contract in self.contracts
        }
```

Public functions:

```python
def list_supported_sports(client) -> tuple[str, ...]: ...

def discover_game_contracts(
    cfg, client, *, now: datetime | None = None
) -> DiscoveryResult: ...

def rank_contracts(
    candidates: Iterable[SelectedContract],
    contracts_per_trade: Decimal,
) -> tuple[SelectedContract, ...]: ...
```

`discover_game_contracts` chooses exactly one source:

- `cfg.sports`: dynamic Sports discovery and global ranking.
- `cfg.tickers`: exact ticker validation through the same metadata graph,
  preserving the configured ticker order. More than
  `max_monitored_markets` is rejected rather than silently truncated.

The ranking key is:

```python
(
    -min(bid_size, ask_size, contracts_per_trade),
    ask - bid,
    -min(bid_size, ask_size),
    provenance.scheduled_start_ts,
    ticker,
)
```

## Task 1: Lock the CLI and Configuration Contract

**Files:**

- Modify: `config.py`
- Modify: `bot.py`
- Modify: `tests.py`

### Step 1: Add failing CLI/config tests

Add focused tests:

```python
def test_cli_parses_sports_without_hardcoded_choices(): ...
def test_cli_rejects_empty_or_duplicate_sports(): ...
def test_cli_rejects_sports_with_configured_tickers(): ...
def test_paper_startup_requires_sports_or_explicit_tickers(): ...
def test_list_sports_is_public_and_creates_no_session_artifact(): ...
def test_live_and_demo_remain_disabled_before_client_creation(): ...
```

Assertions must cover:

- `--sports " tennis ,Basketball "` produces the raw tuple
  `("tennis", "Basketball")`; canonicalization is deliberately deferred to
  the API metadata layer.
- Empty comma segments and case-insensitive duplicates fail with usage text.
- `--check`, `--list-sports`, `--live`, and `--demo` are exclusive modes;
  `--sports` is valid only for the normal paper mode.
- `--list-sports` makes one public filters call, prints `All sports` plus
  canonical names, never acquires the process lock, and never creates a
  research file.
- The existing live/demo tests still prove that no config or environment
  switch can unlock orders.

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_cli_parses_sports_without_hardcoded_choices()'
```

Expected: FAIL because `bot.py` has no Sports CLI parser.

### Step 2: Implement a pure parser and update Config

In `config.py`:

- replace `market_keywords` with `sports: list = field(default_factory=list)`;
- validate `sports` and `tickers` as lists of unique nonempty strings;
- do not require either list inside `Config.validate`, because `--check` and
  `--list-sports` must construct a valid config without a session selection;
- continue enforcing `1 <= max_monitored_markets <= 10`.

In `bot.py`, use `argparse` through a pure helper:

```python
@dataclass(frozen=True)
class CliOptions:
    mode: str
    requested_sports: tuple[str, ...]


def parse_cli(argv) -> CliOptions: ...
```

Keep the raw requested names until the live filters response resolves them.
Write them to `cfg.sports` before calling discovery. Session startup, not
`Config.validate`, enforces `cfg.sports XOR cfg.tickers`.

### Step 3: Run focused tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_cli_parses_sports_without_hardcoded_choices(); tests.test_cli_rejects_sports_with_configured_tickers(); tests.test_live_and_demo_disabled()'
```

Expected: PASS.

## Task 2: Add Strict Sports Metadata Schemas and Client Methods

**Files:**

- Modify: `schemas.py`
- Modify: `kalshi_client.py`
- Modify: `tests.py`

### Step 1: Add failing contract tests with current response fixtures

Add builders for complete filters, series, milestone, and nested-event
responses. Tests:

```python
def test_current_sports_filters_contract(): ...
def test_sports_filters_reject_malformed_scopes_and_competitions(): ...
def test_current_sports_series_contract(): ...
def test_series_response_rejects_duplicate_tickers_and_bad_tags(): ...
def test_current_milestone_contract(): ...
def test_milestone_contract_rejects_bad_details_dates_and_tickers(): ...
def test_current_nested_event_contract_uses_market_parser(): ...
def test_sports_client_uses_documented_public_queries(): ...
def test_discovery_cursors_missing_nonstring_repeated_and_capped_fail(): ...
def test_private_portfolio_pagination_remains_exhaustive(): ...
```

The filters fixture must mirror the observed shape:

```python
{
    "filters_by_sports": {
        "All sports": {
            "competitions": {},
            "scopes": ["Games", "Futures"],
        },
        "Tennis": {
            "competitions": {
                "ATP Washington": {
                    "scopes": ["Games", "Set 1 Winner"],
                },
            },
            "scopes": ["Games", "Set Winner"],
        },
    },
    "sport_ordering": ["All sports", "Tennis"],
}
```

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_current_sports_filters_contract()'
```

Expected: FAIL because the parsers do not exist.

### Step 2: Add normalized schema parsers

Add to `schemas.py`:

```python
def parse_sports_filters_response(response): ...
def parse_series_list_response(response): ...
def parse_milestone(milestone): ...
def parse_milestones_page(response): ...
def parse_event(event): ...
def parse_events_page(response): ...
```

Normalized outputs:

```python
# filters
{
    "sport_ordering": tuple[str, ...],
    "sports": {
        canonical_sport: {
            "scopes": frozenset[str],
            "competitions": {
                competition: frozenset[str],
            },
        },
    },
}

# series row
{
    "series_ticker": str,
    "category": str,
    "tags": tuple[str, ...],  # null/missing normalizes to ()
}

# milestone row
{
    "milestone_id": str,
    "category": "Sports",
    "start_ts": float,
    "title": str,
    "main_game_event_ticker": str | None,
    "league": str | None,
    "primary_event_tickers": tuple[str, ...],
    "related_event_tickers": tuple[str, ...],
}

# event row
{
    "event_ticker": str,
    "series_ticker": str,
    "title": str,
    "markets": tuple[parse_market(row), ...],
    "market_skips": Mapping[str, int],
}
```

Rules:

- require unique nonempty `sport_ordering` entries and exact matching keys in
  `filters_by_sports`;
- require all scopes, competition names, series tickers/tags, IDs, and event
  tickers to be correctly typed and unique;
- require `details` to be an object; accept a missing
  `main_game_event_ticker` as `None`, but reject a present non-string value;
- use `_iso_timestamp` for milestone dates;
- require nested Market `event_ticker` to match its parent event;
- call the existing strict `parse_market` for every nested Market;
- catch only `UnsupportedMarketType` inside event-list parsing, omit that
  Market from `markets`, and increment its type in `market_skips`;
- malformed binary Markets remain fatal;
- series uses the documented one-response `series` collection, not invented
  cursor pagination.

### Step 3: Add public client methods

Add to `kalshi_client.py`:

```python
def get_sports_filters(self): ...
def get_sports_series(self): ...
def get_sports_milestones(
    self, *, minimum_start_date,
    competition=None, related_event_ticker=None,
): ...
def get_open_events(self, *, series_ticker): ...
def get_event(self, event_ticker, *, with_nested_markets=True): ...
```

Queries:

```python
GET /search/filters_by_sport
GET /series?category=Sports
GET /milestones?category=Sports
    &competition=<official name>
    &minimum_start_date=<RFC3339 UTC>
    &limit=500
GET /events?series_ticker=<ticker>
    &status=open
    &with_nested_markets=true
    &limit=200
GET /events/<event_ticker>?with_nested_markets=true
```

Milestone/event pagination must be a strict bounded collection helper that:

- requires a string cursor;
- rejects a repeated cursor;
- rejects a nonempty cursor after `MAX_PAGES`;
- never returns a partial inventory.

`get_sports_milestones` requires exactly one of `competition` or
`related_event_ticker`. Dynamic Sports discovery uses `competition`;
explicit ticker proof uses `related_event_ticker`. Add
`parse_event_response` for the documented direct-event wrapper and reuse the
same normalized `parse_event` implementation as event-list rows.

Do not change `_paginate` semantics for orders/fills/positions and do not use
`scan_markets`, which intentionally permits partial discovery.

### Step 4: Run contract and pagination tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_current_sports_filters_contract(); tests.test_current_sports_series_contract(); tests.test_current_milestone_contract(); tests.test_current_nested_event_contract_uses_market_parser(); tests.test_private_portfolio_pagination_remains_exhaustive()'
```

Expected: PASS.

## Task 3: Build the Metadata-Only Games Discovery Pipeline

**Files:**

- Create: `sports_discovery.py`
- Modify: `research_log.py` (`REPLAY_CODE_FILES` only in this task)
- Modify: `tests.py`

### Step 1: Add failing domain tests

Add:

```python
def test_api_only_new_sport_works_without_source_change(): ...
def test_sport_selection_is_case_insensitive_and_canonical(): ...
def test_all_sports_and_unknown_sports_are_rejected_with_choices(): ...
def test_selected_sport_requires_games_scope_and_competition(): ...
def test_wimbledon_soccer_is_not_classified_as_tennis(): ...
def test_local_day_window_is_half_open_and_dst_safe(): ...
def test_main_game_event_is_preferred_over_props(): ...
def test_sole_primary_game_fallback_and_ambiguous_skip(): ...
def test_series_resolution_uses_unique_longest_official_prefix(): ...
def test_duplicate_metadata_must_be_identical(): ...
def test_incomplete_inventory_prevents_ranking(): ...
def test_contract_ranking_uses_all_five_tie_breakers(): ...
def test_best_ten_are_global_across_selected_sports(): ...
def test_explicit_tickers_must_be_today_games_and_within_cap(): ...
```

Key regression fixture:

```python
# The title contains "Wimbledon", but metadata says Soccer.
series = {"ticker": "KXCLUBFSPREAD", "tags": ["Soccer"]}
milestone = {
    "id": "soccer-1",
    "start_date": "2026-07-26T18:00:00Z",
    "title": "AFC Wimbledon vs Team B",
    "details": {"main_game_event_ticker": "KXCLUBFSPREAD-26JUL26WIMB"},
    "primary_event_tickers": [
        "KXCLUBFSPREAD-26JUL26WIMB",
        "KXCLUBFSPREADTOTAL-26JUL26WIMB",
    ],
}
```

Selecting Tennis must exclude it; selecting Soccer may include it. No test
or implementation may inspect `title` for classification.

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_wimbledon_soccer_is_not_classified_as_tennis()'
```

Expected: FAIL because metadata discovery does not exist.

### Step 2: Implement canonical sport selection and day window

In `sports_discovery.py`:

```python
def canonicalize_sports(requested, filters):
    # Case-insensitive exact match.
    # Reject All sports, unknown, ambiguous, duplicate canonical values,
    # missing Games scope, or no Games competitions.
    # Return in API sport_ordering order for a stable fingerprint.


def local_day_window(now=None):
    # Preserve the machine/local timezone of the supplied aware datetime.
    # Return [local midnight, next local midnight) converted to UTC epochs.
    # Construct the next local calendar day; do not add 86,400 seconds.
```

The DST test must show a 23-hour or 25-hour UTC interval where appropriate,
while still representing exactly one local calendar day.

### Step 3: Implement series and milestone resolution

Build `series_ticker -> canonical sport` only when the series has exactly one
tag matching the canonical filter Sports. Count and skip:

- no canonical sport tag;
- multiple canonical sport tags;
- Games-ambiguous milestone;
- unresolved/ambiguous series prefix;
- series sport outside the selected Sports;
- out-of-window milestone.

For each selected Sport, query every competition whose scopes contain the
exact string `Games`. For league provenance, prefer nonempty
`milestone.details.league`; otherwise use the Kalshi-supplied competition
filter name. Deduplicate milestones and event tickers; identical duplicates
are accepted once, conflicting metadata fails discovery.

An explicit ticker is resolved through
`related_event_ticker=<event_ticker>` without inventing a competition name;
its `league` is `None` unless the returned Kalshi metadata supplies one.

Games event choice:

```python
if milestone.main_game_event_ticker:
    event_ticker = milestone.main_game_event_ticker
elif len(milestone.primary_event_tickers) == 1:
    event_ticker = milestone.primary_event_tickers[0]
else:
    skip("games_ambiguous")
```

Resolve series using the unique longest prefix satisfying:

```python
event_ticker.startswith(series_ticker + "-")
```

### Step 4: Implement event filtering and ranking

Fetch complete open nested-event inventories for every resolved series and
retain only exact Games event tickers. Candidates require:

- event series equals the resolved series;
- active binary, non-MVE Market;
- bid and ask present;
- positive bid and ask depth;
- `ask - bid <= cfg.max_spread`.

Count unsupported products and illiquid/wide candidates separately. Rank all
Sports together with `rank_contracts`, then take
`cfg.max_monitored_markets`.

Explicit ticker mode:

- resolve each ticker's direct Market;
- resolve its event against the series inventory;
- find exactly one in-window milestone for which the event is the main/sole
  Games event;
- require the direct Market to be one of that exact event's parsed nested
  Markets;
- reject duplicate tickers, unresolved metadata, wrong day, non-Games event,
  ineligible book, or a list longer than the cap;
- preserve configured order instead of reranking.

### Step 5: Emit deterministic discovery telemetry

Provide one summary and one line per selected contract:

```text
[discover] day timezone=America/Los_Angeles
  local=[2026-07-26T00:00:00-07:00, 2026-07-27T00:00:00-07:00)
  utc=[2026-07-26T07:00:00Z, 2026-07-27T07:00:00Z)
[discover] pages=... rows=... candidates=... selected=10
  skips=games_ambiguous=..., unsupported_mve=..., ...
[discover] Tennis | ATP Washington | Player A vs Player B |
  TICKER | 2026-07-26T18:00:00Z | bid=... ask=... spread=... depth=(...,...)
```

Sort skip keys before printing so output is stable.

### Step 6: Run discovery tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_api_only_new_sport_works_without_source_change(); tests.test_wimbledon_soccer_is_not_classified_as_tennis(); tests.test_local_day_window_is_half_open_and_dst_safe(); tests.test_contract_ranking_uses_all_five_tie_breakers(); tests.test_best_ten_are_global_across_selected_sports()'
```

Expected: PASS.

## Task 4: Integrate Discovery with Startup and PriceFeed

**Files:**

- Modify: `market_data.py`
- Modify: `bot.py`
- Modify: `research_log.py`
- Modify: `engine.py`
- Modify: `tests.py`

### Step 1: Add failing startup/feed tests

Add:

```python
def test_feed_installs_immutable_discovery_provenance(): ...
def test_quote_event_mismatch_fails_closed(): ...
def test_discovery_failure_is_durable_and_returns_nonzero(): ...
def test_post_discovery_session_uses_canonical_sports(): ...
def test_run_session_monitors_selected_contracts_only_once(): ...
def test_keyboard_interrupt_and_system_exit_are_not_swallowed_by_discovery(): ...
```

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_quote_event_mismatch_fails_closed()'
```

Expected: FAIL because `PriceFeed` still overwrites `group_ids`.

### Step 2: Make startup discovery precede evidence creation

Reorder `run_session`:

1. validate static config and paper-only lock;
2. instantiate `PriceFeed`;
3. call `discover_game_contracts`;
4. replace `cfg.sports` with
   `list(discovery.selected_sports)` and validate again;
5. install contracts/provenance into the feed;
6. construct strategy, journal, executor, safety, and `ResearchLog`;
7. subscribe and run the existing loop.

This sequence is mandatory: `ResearchLog` fingerprints canonical API-resolved
Sports, so it cannot be created before successful discovery.

A complete discovery with zero eligible contracts is not schema failure:
create the canonical v6 session log, write one clean terminal record with
reason `no eligible Games contracts for selected Sports`, and return zero.
An incomplete inventory remains a nonzero startup halt.

### Step 3: Preserve durable startup failures honestly

Add to `research_log.py`:

```python
def write_startup_halt(
    reason, *, requested_sports=(), tickers=(), log_dir="logs",
    clock=time.time,
): ...
```

It appends and `fsync`s one JSONL operational record containing:

```python
{
    "schema_version": 6,
    "event": "session_halt",
    "ts": ...,
    "requested_sports": [...],
    "tickers": [...],
    "reason": "...",
}
```

Use a separate `startup_halts_v6.jsonl` file because a truthful research
configuration fingerprint cannot exist before Sports canonicalization.
This file is operational evidence only and is never accepted by replay or
analysis. A failure after a successful `ResearchLog` is created continues to
use `log.end(clean=False, reason=...)`.

Catch `Exception`, not `BaseException`, so `KeyboardInterrupt` and
`SystemExit` preserve their semantics.

### Step 4: Replace mutable grouping with provenance

In `PriceFeed`:

```python
def install_discovery(self, discovery): ...
def provenance(self, ticker) -> ContractProvenance: ...
def group_id(self, ticker):
    return self.provenance(ticker).event_ticker
```

Remove keyword discovery and mutable `group_ids`. `get_quote` must compare
the direct Market's `event_ticker` with immutable discovery provenance and
raise `SchemaError` on disagreement; it must never rewrite provenance.

Pass feed provenance for API-error, quarantine, quote, delayed-fill, and
immediate BUY/SELL logging paths. Do not miss delayed paper fills.

### Step 5: Run startup/feed tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_discovery_failure_is_durable_and_returns_nonzero(); tests.test_feed_installs_immutable_discovery_provenance(); tests.test_quote_event_mismatch_fails_closed(); tests.test_keyboard_interrupt_and_system_exit_are_not_swallowed_by_discovery()'
```

Expected: PASS.

## Task 5: Advance Research Logging to Strict v6 Provenance

**Files:**

- Modify: `research_log.py`
- Modify: `engine.py`
- Modify: `tests.py`

### Step 1: Add failing v6 logger tests

Add:

```python
def test_v6_config_fingerprint_uses_canonical_sports(): ...
def test_v6_quote_and_trade_rows_share_full_provenance(): ...
def test_delayed_paper_fill_logs_full_provenance(): ...
def test_v6_logger_rejects_missing_or_unknown_ticker_provenance(): ...
def test_v6_terminal_rows_are_unscoped(): ...
```

Fingerprint assertion:

```python
first = canonicalize_sports(
    ["basketball", "TENNIS"], filters_fixture)
second = canonicalize_sports(
    ["tennis", "BASKETBALL"], filters_fixture)
assert first == second == ("Basketball", "Tennis")
assert fingerprint_for(first) == fingerprint_for(second)
assert fingerprint_for(("Tennis",)) != \
       fingerprint_for(("Basketball",))
```

Canonicalization equivalence (`tennis` versus `Tennis`) is tested at the
discovery boundary; `config_fingerprint` must never normalize operator text
on its own.

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_v6_quote_and_trade_rows_share_full_provenance()'
```

Expected: FAIL while the logger still writes v5.

### Step 2: Change the schema and fingerprint

In `research_log.py`:

- set `RESEARCH_SCHEMA_VERSION = 6`;
- replace `market_keywords` with `sports` in
  `RESEARCH_CONFIG_FIELDS`;
- add `sports_discovery.py` to `REPLAY_CODE_FILES`;
- use `ticks_v6_*` and `trades_v6_*` filenames;
- add these columns to both quote and trade files:

```text
selected_sports,
sport, league, series_ticker, milestone_id,
event_ticker, scheduled_start_ts
```

Remove `event_id` from v6. `event_ticker` is both provenance and the
train/test grouping key.

`selected_sports` is the same canonical compact JSON array on every row,
including the terminal row. The loader validates that it is immutable,
restores it into a copy of the supplied/default replay configuration, and
then checks the logged configuration fingerprint. This lets `analyze.py`
reproduce a real `--sports` session fingerprint without requiring the
operator to repeat CLI arguments. Per-market provenance fields remain empty
on terminal rows.

Construct `ResearchLog` with:

```python
ResearchLog(
    ...,
    provenance_by_ticker=discovery.provenance_by_ticker,
)
```

Every ticker-bearing `tick`, `trade`, or `event` resolves provenance from
that immutable mapping. Callers cannot supply partial provenance. Terminal
rows have empty ticker and all six provenance fields empty.

### Step 3: Run logger/engine tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_v6_config_fingerprint_uses_canonical_sports(); tests.test_v6_quote_and_trade_rows_share_full_provenance(); tests.test_delayed_paper_fill_logs_full_provenance(); tests.test_v6_terminal_rows_are_unscoped()'
```

Expected: PASS.

## Task 6: Make Replay Strictly v6 and Provenance-Consistent

**Files:**

- Modify: `replay.py`
- Modify: `tests.py`

### Step 1: Convert the shared research fixture

Replace `RESEARCH_HEADER` and `research_row` in `tests.py` with v6 fields.
Every quote fixture supplies complete provenance. Terminal helpers write no
ticker/provenance.

### Step 2: Add failing replay gates

Add:

```python
def test_replay_rejects_v5_and_mixed_schema_logs(): ...
def test_replay_rejects_each_missing_provenance_field(): ...
def test_replay_rejects_invalid_or_drifting_provenance(): ...
def test_replay_accepts_empty_league_but_preserves_supplied_league(): ...
def test_runtime_v6_log_replays_same_fills_and_pnl(): ...
def test_replay_exposes_market_provenance(): ...
```

The v5 test must assert:

- the legacy file bytes are unchanged;
- the error directs the user to run the archived v5 code/fingerprint;
- no "diagnostic legacy replay" branch accepts it.

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_replay_rejects_v5_and_mixed_schema_logs()'
```

Expected: FAIL because `load_log` currently accepts legacy rows.

### Step 3: Implement one strict v6 loader

In `replay.load_log`:

- require every row's `schema_version == "6"`;
- retain existing single-session, fingerprint, timestamp, book, lifecycle,
  monotonicity, and terminal checks;
- require one immutable canonical `selected_sports` JSON array across every
  row, restore it into a copy of the replay config, and verify the config
  fingerprint against that restored config;
- require nonempty `sport`, `series_ticker`, `milestone_id`,
  `event_ticker`, and finite nonnegative `scheduled_start_ts` on every quote;
- accept `league == ""` only as unavailable;
- require one immutable provenance tuple per ticker;
- require quote `event_ticker` to be the grouping key;
- require terminal rows to have no ticker or provenance;
- reject any row after the terminal record.

Return the provenance map in metadata and add it to the `replay()` result as:

```python
"market_provenance": provenance_by_ticker
```

Keep the existing four-item trade tuples to avoid unrelated churn.

### Step 4: Run replay regressions

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_replay_rejects_v5_and_mixed_schema_logs(); tests.test_replay_rejects_invalid_or_drifting_provenance(); tests.test_runtime_v6_log_replays_same_fills_and_pnl(); tests.test_replay_exposes_market_provenance()'
```

Expected: PASS.

## Task 7: Add Per-Sport Held-Out Analysis

**Files:**

- Modify: `analyze.py`
- Modify: `tests.py`

### Step 1: Add failing analyzer tests

Add:

```python
def test_analyzer_requires_complete_consistent_v6_provenance(): ...
def test_analyzer_groups_sibling_contracts_by_event_ticker(): ...
def test_analyzer_reports_overall_and_each_sport_train_test(): ...
def test_positive_overall_does_not_qualify_nonpositive_sport(): ...
def test_sport_attribution_is_stable_across_input_order(): ...
```

Use a two-Sport fixture where the overall TEST result is positive but one
Sport's TEST result is zero or negative. Assert that only the Sport with its
own positive evaluable held-out result is labeled supported.

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_positive_overall_does_not_qualify_nonpositive_sport()'
```

Expected: FAIL because the analyzer has no Sport attribution.

### Step 2: Share strict v6 validation

Avoid two drifting CSV validators. Keep the single strict implementation in
`replay.load_log`; extend its `include_metadata=True` result to this exact
shape:

```python
(
    rows,
    data_gaps,
    starting_pnl,
    starting_day,
    terminal_status,
    terminal_reason,
    provenance_by_ticker,
)
```

Have `analyze.load` call that loader with the current expected configuration
and code fingerprints, require a clean terminal/no data gaps, and build its
point series from the normalized rows. It returns:

```python
series, groups, provenance_by_ticker
```

`groups[ticker]` is always `event_ticker`, so sibling outcome contracts stay
in one hash-stable TRAIN/TEST bucket.

### Step 3: Report portfolio and Sport attribution

Run one shared portfolio replay, then attribute
`replay_result["per_ticker_total"]` by:

- overall TRAIN;
- overall TEST;
- each canonical Sport's TRAIN;
- each canonical Sport's TEST.

For each Sport print market count, exits, net P&L/diagnostic status, and a
held-out label:

```text
SUPPORTED HYPOTHESIS: TEST is evaluable and net P&L > 0
NOT SUPPORTED: TEST <= 0, empty, or not evaluable
```

This is evidence about the tested paper strategy only, never a claim of live
profitability.

### Step 4: Run analyzer tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_analyzer_reports_overall_and_each_sport_train_test(); tests.test_positive_overall_does_not_qualify_nonpositive_sport(); tests.test_analyzer_groups_sibling_contracts_by_event_ticker()'
```

Expected: PASS.

## Task 8: Extend Preflight and Documentation

**Files:**

- Modify: `bot.py`
- Modify: `README.md`
- Modify: `tests.py`

### Step 1: Add failing preflight/output tests

Add:

```python
def test_preflight_validates_public_sports_metadata_without_orders(): ...
def test_preflight_reports_metadata_skips_and_unobserved_portfolio_rows(): ...
def test_preflight_never_calls_order_mutation_endpoints(): ...
def test_readme_documents_sports_commands_and_v6_break(): ...
```

Run:

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_preflight_validates_public_sports_metadata_without_orders()'
```

Expected: FAIL because preflight does not inspect Sports metadata.

### Step 2: Add bounded Sports checks

`--check` should:

- validate the filters response and print canonical Sports with Games;
- validate the full Sports series response;
- validate one bounded milestone page and one bounded nested-event page when
  the selected/current metadata provides them;
- label row schemas not observed in empty collections as warnings, preserving
  the prior fix;
- retain authenticated balance/orders/fills/positions checks;
- never create, cancel, or poll a test order.

This is contract coverage, not a claim that every current-day event was
exhaustively ranked during preflight.

### Step 3: Update README

Document:

```bash
python bot.py --list-sports
python bot.py --sports Tennis,Basketball
python bot.py --check
```

Also document:

- Games only, all live competitions in each selected Sport;
- best ten individual contracts globally;
- startup-only selection, no rotation;
- explicit ticker validation rules;
- local-day timezone behavior;
- strict complete-inventory failure behavior;
- v6 provenance fields and the v5 archived-code boundary;
- per-Sport TEST interpretation;
- live/demo remain disabled.

Remove all title-keyword/tennis-only discovery guidance.

### Step 4: Run focused preflight/docs tests

```bash
/Users/mthanki/.venvs/inci/bin/python -c \
  'import tests; tests.test_preflight_validates_public_sports_metadata_without_orders(); tests.test_preflight_never_calls_order_mutation_endpoints(); tests.test_readme_documents_sports_commands_and_v6_break()'
```

Expected: PASS.

## Task 9: Full Verification and Read-Only Production Check

**Files:**

- Verify all changed files.
- Do not make additional behavior changes during verification.

### Step 1: Syntax and source-policy checks

```bash
/Users/mthanki/.venvs/inci/bin/python -m py_compile \
  analyze.py bot.py config.py engine.py executor.py fees.py \
  kalshi_client.py market_data.py order_journal.py order_resolution.py \
  pnl_ledger.py process_lock.py replay.py research_log.py safety.py \
  schemas.py signals.py sports_discovery.py strategy.py tests.py

rg -n 'market_keywords|Wimbledon|Roland Garros|Australian Open|ATP|WTA' \
  --glob '*.py'

rg -n 'REAL_ORDER_EXECUTION_ENABLED|--live|--demo' \
  bot.py executor.py tests.py
```

Expected:

- syntax check exits 0;
- no implementation hardcodes Sports/leagues/title keywords (Sports names may
  appear only in test fixtures);
- both bot-level and executor-level real-order locks remain present.

### Step 2: Run the full offline suite

```bash
/Users/mthanki/.venvs/inci/bin/python tests.py
```

Expected: every registered test prints `PASS`, final line reports the exact
count, and no network call occurs.

### Step 3: Inspect the complete diff

```bash
git status --short
git diff --check
git diff --stat
git diff -- \
  README.md analyze.py bot.py config.py engine.py kalshi_client.py \
  market_data.py replay.py research_log.py schemas.py \
  sports_discovery.py tests.py
```

Confirm:

- prior uncommitted rate-limit work is preserved;
- only intended files changed;
- no API key ID, private-key path, PEM contents, or local credential value is
  present;
- no production order call was added to tests/preflight.

### Step 4: Run one public read-only production validation

First:

```bash
/Users/mthanki/.venvs/inci/bin/python bot.py --list-sports
```

Then choose two Sports currently advertising Games and run paper discovery
only long enough to print the selected contracts; stop with Ctrl-C before
collecting a research session if desired:

```bash
/Users/mthanki/.venvs/inci/bin/python bot.py \
  --sports Tennis,Soccer
```

Validate from output:

- canonical Sports;
- local and UTC day window;
- every queried competition came from the filters response;
- complete-inventory telemetry;
- deterministic global top-ten contract selection;
- no title-keyword false positive;
- no authenticated request is needed for discovery.

Do not call demo/live and do not probe create/cancel endpoints.

### Step 5: Final handoff

Report:

- exact changed files;
- exact offline test count and command;
- public validation result, including selected Sports and contract count;
- any Kalshi response drift or unobserved schema branch;
- residual limitation: strategy edge remains unproven until clean v6
  held-out paper sessions are positive per Sport.
