# Inci — Kalshi Sports Scalping Research Bot

Inci is a **paper-research build** for studying short-term price retracement in
Kalshi Sports markets. It simulates latency, spread, depth, slippage, fees,
position limits, and shutdown behavior. Research evidence does not prove live
profitability.

The bot does not submit orders; demo/live remain disabled in `bot.py`, and
real-order mutation is independently locked off inside `Executor`. No flag,
configuration field, or environment variable unlocks it.

## Setup and commands

```bash
python -m pip install -r requirements.txt
python tests.py
python bot.py --list-sports
python bot.py --sports Tennis,Basketball
python bot.py --check
python analyze.py logs/ticks_v6_<YYYYMMDD>_<session-id>.csv
```

`--list-sports` is public and reflects the current canonical API Sports.
`--sports` accepts comma-separated, case-insensitive names and returns them
API-canonicalized in the API's stable order.

`--check` is read-only. It validates exchange, Market, bounded Sports metadata,
and authenticated portfolio response contracts. Its Sports milestone and Event
checks deliberately fetch one page each, report whether more pages exist, and
never perform discovery, ranking, order creation, cancellation, or synthetic
order polling. Missing credentials or malformed observed data fail the check;
valid empty portfolio collections produce a coverage warning.

Credentials stay outside Git:

```bash
export KALSHI_API_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY_PATH="/absolute/path/outside-the-repo/key.pem"
```

## Interactive live tennis shadow collector

The separate shadow command starts from the complete current-day Kalshi
`Tennis` / `Games` census and then optionally correlates Sportradar. Chooser
mode requires only the two Kalshi credentials above; `SPORTRADAR_API_KEY` is
optional.

```bash
export KALSHI_API_KEY_ID="your-read-only-key-id"
export KALSHI_PRIVATE_KEY_PATH="/absolute/path/outside-the-repo/key.pem"
# Optional: enables one quota-ledger-backed provider discovery attempt.
export SPORTRADAR_API_KEY="your-trial-key"

python -m inci_tennis_runtime.live_shadow_cli \
  --choose \
  --duration-seconds 600 \
  --poll-seconds 10
```

The chooser labels every structurally eligible two-Market Kalshi game exactly
once. `VERIFIED` means only one fresh, live, exact observation-source match;
it is selectable but remains unqualified and grants no execution authority.
`PRICE_ONLY` is also selectable and has no score feed. `CONFLICT` is displayed
under `CONFLICT / EXCLUDED`, unnumbered, and cannot be selected. Missing or
invalid provider credentials, unavailable quota, 429, transport or schema
failure, stale or empty provider data, unsupported coverage, and no exact
match all downgrade otherwise valid games to `PRICE_ONLY`. A partial or
malformed Kalshi census halts instead of downgrading because Kalshi is the
primary inventory.

Empty and one-sided books remain valid census members and print their initial
book state. They can stay quiet until an aggregate snapshot supplies usable
two-sided books; a quiet price dashboard is therefore not evidence that the
collector is frozen. The exact dashboard banners are:

```text
READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / NO SIGNALS / NO P&L / NO ORDERS
READ ONLY / PRICE ONLY / NO SCORE FEED / NO SIGNALS / NO P&L / NO ORDERS
```

Invalid input reprompts locally without rediscovery. `Q` or EOF closes any
provider-discovery ledger and exits before constructing a collection
WebSocket or evidence session. During collection, Ctrl-C, SIGTERM, and SIGHUP
request the same durable terminal path; an interrupt at the prompt likewise
opens no collection socket or evidence. A verified collector may start one
new linked price-only session only after an allowlisted, source-attested
provider transport/parser failure. It carries no score or book state and does
not relabel earlier evidence. Kalshi transport, catalog, projection, identity,
evidence-integrity, terminal, cleanup, and unknown failures halt without
failover.
If verified failover leaves fewer than ten seconds, the collector halts instead
of opening a price-only session.

Provider quota is staged: discovery reserves at most one call; choosing
`PRICE_ONLY` reserves no collection-provider calls; choosing `VERIFIED`
performs a second preflight for the planned summary/timeline collection calls
before a provider collection context or Kalshi WebSocket is opened.

The original explicit mode remains available for diagnostics:

```bash
python -m inci_tennis_runtime.live_shadow_cli \
  --match-id sr:sport_event:123456789 \
  --home-ticker KXTENNIS-MATCH-HOME \
  --away-ticker KXTENNIS-MATCH-AWAY \
  --duration-seconds 600 \
  --poll-seconds 10
```

The modes are mutually exclusive. Explicit mode still requires Sportradar and
is diagnostic `OPERATOR-SUPPLIED / UNVERIFIED` collection. In either mode,
`candidate` means only that both books passed the sequence/snapshot barrier;
it is not a trade candidate.

The Kalshi key must have exactly the `read` scope. Startup verifies it with the
read-only `GET /api_keys` endpoint and refuses full-access or write-scoped keys
before opening the WebSocket.

## Live Models 1+2 paper bridge

This separate command always prints the following line before opening a
capture source:

```text
LIVE MODELS 1+2 / PAPER ONLY / NO REAL ORDERS
```

For a deterministic, network-free run over two growing files:

```bash
python -m inci_tennis_runtime.live_two_model_paper_cli \
  --manifest /absolute/path/live-paper-match.json \
  --score-stream /absolute/path/growing-score-captures.jsonl \
  --kalshi-stream /absolute/path/growing-kalshi-frames.jsonl \
  --session-log /absolute/path/live-paper-session.jsonl \
  --checkpoint /absolute/path/live-paper-checkpoint.json \
  --bootstrap-home-serve 0.64 \
  --bootstrap-away-serve 0.61 \
  --duration-seconds 600
```

Use `--stop-at-eof` for a bounded fixture/dry run. Without it, the command
tails both files until the duration expires, emits a durable heartbeat at
least every 60 seconds, and appends a typed terminal on duration, SIGINT, or
SIGTERM. Growing-file polling retains a bounded incomplete final JSONL row
until its terminating newline arrives, authenticates the entire previously
observed byte prefix on every poll, and rejects any prefix mutation. In
`--stop-at-eof` mode, an incomplete final row is an error. Captured wall and
monotonic clocks must both be session-wide nondecreasing, including across
polls and resume.

Replay is a separate log-only route:

```bash
python -m inci_tennis_runtime.live_two_model_paper_cli \
  --replay-only \
  --session-log /absolute/path/live-paper-session.jsonl
```

The replay path requires an absolute existing regular terminal session log,
authenticates its hash chain, and recomputes every derived record. It does not
accept or read a manifest, checkpoint, score/Kalshi stream, artifact, bootstrap
prior, live option, or duration; it opens no writer or network transport.

Live read-only mode delegates match discovery, chooser UI, Sportradar capture,
and Kalshi WebSocket handling to the existing shadow collector:

```bash
python -m inci_tennis_runtime.live_two_model_paper_cli \
  --live-readonly \
  --manifest /absolute/path/live-paper-match.json \
  --session-log /absolute/path/live-paper-session.jsonl \
  --checkpoint /absolute/path/live-paper-checkpoint.json \
  --bootstrap-home-serve 0.64 \
  --bootstrap-away-serve 0.61 \
  --duration-seconds 600
```

The selected Sportradar match ID and exact HOME/AWAY Kalshi tickers must equal
the frozen manifest before subscription. The existing read-only catalog does
not expose provider player IDs or Kalshi market UUIDs: player IDs are therefore
verified on the first raw Sportradar capture, and lowercase market UUIDs plus
YES orientation are verified on the first complete raw L2 projection. Both
checks halt before any paper action. The Kalshi credential must have the exact
scope set `{"read"}`; `{"read","write"}`, `{"write"}`, empty, unknown, and
malformed scope sets halt before the WebSocket opens. No write/order transport
is imported by this command.

Live observer mode admits only a `VERIFIED` Sportradar chooser row and requires
at least a ten-second duration. It never starts a `PRICE_ONLY` row and never
fails over from a verified provider halt to price-only collection. Each paper
RAW record authenticates the concrete collector receipt, raw reference,
capture identity, digest, clocks, and (for Kalshi) connection generation that
were durably committed before the observer callback.
Natural 7–6 completed sets are accepted only when the raw period score includes
exact home/away tiebreak points proving a legal TB7 result; otherwise the score
fails closed rather than synthesizing missing tiebreak evidence.

Current live network score coverage is exactly Sportradar through the existing
trial capture transport. API-Tennis, GoalServe, and Live Tennis API have strict
raw parsers but no live network transport in this command; any of those
sources can join the same paper session through the growing score JSONL input.

The manifest is strict JSON with exactly these top-level fields:
`schema`, `version`, `canonical_match_id`, `scheduled_start_wall_ns`,
`match_format`, `home_player_id`, `away_player_id`, `providers`, `markets`,
`fee_schedule`, and `fee_series_ticker`. `schema` is
`inci.live-paper-match-manifest`, `version` is `1`, and `match_format` is
`STANDARD_ADVANTAGE_BO3_TB7_ALL_SETS`. Every provider row has exactly
`slot`, `source_id`, `provider_match_id`, `home_player_id`, `away_player_id`,
`independent_lineage_id`, `source_lineage_sha256`, `independence_proven`, and
`independence_proof_sha256`. A source can contribute to consensus only when
the envelope repeats the exact frozen proof digest and all identity fields.

Each score JSONL row has exactly this raw-capture envelope (values abbreviated):

```json
{"kind":"score_capture","provider_slot":"api_tennis","provider_source_id":"api-tennis-primary","provider_match_id":"101","home_player_id":"201","away_player_id":"202","independent_lineage_id":"lineage-a","source_lineage_sha256":"<64 lowercase hex>","independence_proven":true,"independence_proof_sha256":"<64 lowercase hex>","raw_capture_id":"capture-1","captured_wall_ns":1,"captured_monotonic_ns":1,"clock_uncertainty_ns":0,"payload_base64":"<raw provider bytes>"}
```

Each Kalshi JSONL row has exactly:

```json
{"kind":"kalshi_frame","physical_connection_generation":1,"captured_wall_ns":1,"captured_monotonic_ns":1,"clock_uncertainty_ns":0,"payload_base64":"<raw WebSocket frame>"}
```

The score payload is always parsed by `parse_live_score`; the Kalshi payload
is always parsed by `parse_unqualified_book_message` and applied to
`UnqualifiedTwoTickerBookReducer`. Normalized probabilities, top-of-book
projections, or caller-supplied books are not accepted envelope fields.

Bootstrap mode requires both `--bootstrap-home-serve` and
`--bootstrap-away-serve`. Trained mode replaces both priors with both
`--static-artifact` and `--dynamic-artifact`; the two modes are exclusive.
The five-second score/book freshness, one-second decision latency, $50 debit
cap, $5 entry/exit thresholds, 300-second maximum hold, and 60-second heartbeat
are frozen code/session constants and have no CLI overrides.

Before live transport starts, the command prints the configured provider proof
status and aggregate trust eligibility, artifact authority and digests,
canonical match/start/format, exact HOME/AWAY ticker/UUID/YES orientation, all
frozen policy constants, the paper state root, and `NO REAL ORDERS`. Dashboard
rows include elapsed time, factual source `seen`/`missing` health, score trust,
both models, executable HOME/AWAY top books and book age, pending and last
decision, cumulative typed rejection counts, position, and paper P&L. Neither
output includes credentials.

For capture/resume mode, all inputs must already exist as absolute regular
non-symlink files. The log and checkpoint must be absolute, distinct from every
input and each other, and non-symlink paths. Evidence rows append with
flush/fsync ordering; the
checkpoint uses temp-write, fsync, atomic replace, and parent-directory fsync.
An existing log/checkpoint is authenticated by the session replay APIs before
resume and is never truncated or silently replaced. The session log is held by
a nonblocking exclusive process lock, and each append re-authenticates the
size, full committed prefix, and inode on the same no-follow append descriptor.
The authenticated session configuration freezes the exact manifest digest and
the complete ordered provider identity/proof authority set.

Authority labels are intentionally narrow: provider score revisions are
`PAPER_LOCAL_REVISION_TRANSPORT_ONLY`; score trust is `SINGLE_SOURCE_PAPER`,
`CONSENSUS_PAPER`, or `ABSTAINED`; operator priors are
`OPERATOR_BOOTSTRAP / NO_EDGE_CLAIM`; trained artifacts remain
`TRAINED_ARTIFACT / RESEARCH_ONLY`; paper edge is
`SETTLEMENT_VALUE_PROXY`. None is execution authorization or a real-order
signal.

Collection ledgers and raw Kalshi captures are under
`~/.local/state/inci/tennis-shadow/`; provider trial usage and raw discovery
captures are under `~/.local/state/inci/sportradar-trial/`. These roots use the
OS account directory, not repository configuration. A missing durable terminal
fails the next audit closed. Hash chains detect row and referenced-raw
tampering, but cannot detect coherent deletion or rollback of the entire root;
archive completed evidence externally when that threat matters.

Settlement is a separate public-Kalshi-GET-only command and requires the
absolute completed session-ledger path:

```bash
python -m inci_tennis_runtime.shadow_settlement_cli \
  /absolute/path/to/session-<uuid>.jsonl
```

It prints `pending`, `final`, or `conflict`. An initial reconciliation with
either Market in a recognized non-final status is `pending` and writes nothing.
After both Markets are finalized, a syntactically admitted `void`, semantically
invalid, or noncomplementary result is a durable `conflict`. Only two finalized
Kalshi Markets with complementary binary results can produce `final`. Final and
conflict rows plus their raw responses are append-only under
`~/.local/state/inci/tennis-shadow-settlement/`. The exact production root is
`Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/inci/tennis-shadow-settlement'`:
the `~` above denotes the OS account home and is explicitly not configurable
through `HOME`.
Any changed normalized evidence after a durable final appends a permanent
conflict that supersedes the prior row without erasing it. Exit codes are `0`
for a result or help, `1` for a halt, `2` for usage, and `130` for an interrupt.

This workflow is observation-only. It has no signal, strategy, fee, P&L,
executor, order, portfolio, or expert-synchronization call path. `VERIFIED`
does not mean provider qualification, product qualification, trusted
synchronization, or permission to trade.

See the
[live tennis shadow collector design and operations record](docs/superpowers/specs/2026-08-01-live-tennis-shadow-collector-design.md)
and the
[interactive chooser design](docs/superpowers/specs/2026-08-01-interactive-shadow-match-chooser-design.md)
and the [Kalshi-first hybrid design](docs/superpowers/specs/2026-08-01-kalshi-first-tennis-hybrid-design.md)
for the complete operator and evidence contracts. Automated tests run beneath
an explicit socket/HTTP/WebSocket denial sentinel and make no live calls.

## Sports discovery

- In dynamic mode, only Games contracts are considered across every
  advertised Games-capable competition for each selected Sport.
- No Sport or league names are embedded in selection code. Classification uses
  official Sports filters, Series, Milestones, and nested Events; display
  titles and ticker text are never classifiers.
- The session window is the machine's local `[midnight, next midnight)`.
  Startup prints both local and UTC bounds, including daylight-saving offsets.
- Eligible individual contracts are ranked together across all selected
  Sports. Inci monitors the best ten total, selected once at startup, with no
  churn during discovery and no rotation during the session. Ranking favors
  executable two-sided depth, tighter spread, greater depth, earlier start,
  then ticker.
- `Config.tickers` is an explicit alternative to `--sports`: the sources are
  mutually exclusive, configured order is preserved, and the list is capped at ten.
  Every ticker must prove the complete relationship
  `Market → Event → official Series → current-day Games Milestone`.
- Partial or malformed Series, Milestone, or Event inventory fails closed and
  cannot claim a complete top ten. Recognized unsupported products are skipped
  loudly by type; malformed binary products still fail.

The chosen set is immutable for that process. Start a new paper session to
change Sports or refresh the day's candidates.

## Paper execution and safety

- Entries use executable ask prices; exits and risk marks use executable bids.
  Simulated fills wait for a newly observed quote after the configured latency,
  apply adverse slippage, respect available depth, and include estimated fees.
- Market exposure, pending entries, stale quotes, gaps, ambiguous state, loss
  limits, and API failures are handled conservatively. Critical failures halt
  the shared portfolio instead of being hidden by a healthy market.
- Paper shutdown cancels pending simulated orders and records residual
  inventory honestly. It never reuses a stale book to fabricate an exit.
- `can_close_early=true` is retained as visible lifecycle risk metadata but
  does not make paper research inert. Inci prints a one-time `PAPER-ONLY`
  notice and continues evaluating entries. The same condition remains a
  fail-closed entry block outside paper mode, while insufficient close horizon
  blocks every mode.
- Ctrl-C, SIGTERM, and SIGHUP use the same terminal-record path.
- Safe GETs have bounded 429 retry/backoff. Mutating requests are never
  automatically retried, and all real-order paths remain unreachable.
- Durable lock, journal, and loss-ledger paths share one account/environment
  state root. Credentials are read from the two environment variables above.

## Strict v6 research boundary

v6 rows carry selected Sports plus each contract's Sport, optional league,
Series ticker, Milestone ID, Event ticker, and scheduled start. They also carry
configuration/code fingerprints, session identity, timestamps, lifecycle
facts, book depth, fees, and a durable terminal state.

Replay and analysis accept only strict v6 files with exact headers, immutable
provenance, chronological rows, matching fingerprints, and a clean terminal.
Unchanged v5 files require the archived v5 code matching their logged code
fingerprint; they are not silently upgraded or mixed with v6.

`analyze.py` runs one chronological shared-portfolio replay. Sibling contracts
from one Event stay in the same stable TRAIN or TEST partition. It reports
overall and per-Sport TRAIN/TEST results, including selected Sports with no
eligible markets. A Sport is supported only when its own shared-portfolio TEST
partition is evaluable and has strictly positive net P&L. Positive overall P&L
cannot qualify a different Sport. Residual inventory, pending orders, data
gaps, a safety halt, an invalid terminal, or unprocessed rows make the research
non-evaluable.

## What remains unproven

- The dip-retracement hypothesis needs many clean sessions and untouched Events
  before its held-out result means anything.
- Price-only signals cannot distinguish temporary market noise from new game
  information; latency and adverse selection remain central risks.
- Paper fees are estimates and may omit Series-specific multipliers,
  promotions, maker rebates, or multi-fill rebates.
- Authenticated portfolio schemas still need a successful read-only `--check`
  from the operator's machine.
- No demo lifecycle has been exercised because order probing is intentionally
  prohibited in this build.

Do not discuss enabling orders until the full offline suite and authenticated
preflight pass, code/config are frozen, and multiple unseen per-Sport TEST
partitions remain positive after all simulated costs.

## Files

`bot.py` · `config.py` · `sports_discovery.py` · `market_data.py` ·
`schemas.py` · `kalshi_client.py` · `research_log.py` · `replay.py` ·
`analyze.py` · `strategy.py` · `signals.py` · `engine.py` · `executor.py` ·
`safety.py` · `order_resolution.py` · `order_journal.py` · `process_lock.py` ·
`pnl_ledger.py` · `fees.py` · `tests.py`
