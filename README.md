# Inci — Kalshi Sports Scalping Research Bot

Inci is a **paper-research build** for studying short-term price retracement in
Kalshi Sports markets. Paper execution is delayed IOC / taker-at-touch (aligned
with `time_in_force=immediate_or_cancel`): one fill attempt after simulated
latency, depth-capped, remainder canceled — not maker GTC. Take-profit is
variable: `take_profit` is the fee-covering arm floor, then `tp_trail_cents`
lets a spike run and exits on pullback from the peak (`0` = fixed TP). Paper
entries are also gated by a free ESPN ATP/WTA scoreboard feed plus a
score-based match-win probability model. ITF can bind via an optional Live
Tennis API key (`LIVETENNISAPI_KEY`); without a key or bind, or with no model
edge, there is no buy. It also models spread, stop-loss slippage, fees,
position limits, and shutdown behavior. Research evidence does not prove live
profitability.

The bot does not submit orders; demo/live remain disabled in `bot.py`, and
real-order mutation is independently locked off inside `Executor`. No flag,
configuration field, or environment variable unlocks it.

## Setup and commands

```bash
pip install requests cryptography
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
# Optional — ITF scoreboard bind (free key at https://livetennisapi.com/subscribe/free)
export LIVETENNISAPI_KEY="twjp_…"
```

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
