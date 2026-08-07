# Inci — Kalshi Sports Scalping Research Bot

Inci is a **paper-research build** for studying short-term price retracement in
Kalshi Sports markets. Paper execution is delayed IOC / taker-at-touch (aligned
with `time_in_force=immediate_or_cancel`): one fill attempt after simulated
latency, depth-capped, remainder canceled — not maker GTC. Take-profit is
variable: `take_profit` is the fee-covering arm floor, then `tp_trail_cents`
lets a spike run and exits on pullback from the peak (`0` = fixed TP). Paper
entries are also gated by a free ESPN ATP/WTA scoreboard feed plus a
score-based match-win probability model. ITF can bind via an optional Live
Tennis API key (`LIVETENNISAPI_KEY`). A read-only Models 1+2 prematch snapshot
can supply genuine player priors; without it, the neutral score transform is
only a collapse guard and never claims fair-value edge. It also models spread,
stop-loss slippage, fees, position limits, and shutdown behavior. Research
evidence does not prove live profitability.

The bot does not submit orders; demo/live remain disabled in `bot.py`, and
real-order mutation is independently locked off inside `Executor`. No flag,
configuration field, or environment variable unlocks it.

## Setup and commands

```bash
pip install requests cryptography
python tests.py
python bot.py --list-sports
python bot.py --sports Tennis
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

### Models 1+2 prematch bridge

The separate two-model pilot can atomically publish one JSON snapshot and the
paper bot will update both prematch probabilities from the live score. Enable
the read-only bridge with:

```bash
export INCI_TWO_MODEL_PRIOR_PATH="/absolute/path/outside-the-repo/priors.json"
python bot.py --sports Tennis
```

Snapshot format (probabilities must be decimal strings):

```json
{
  "schema_version": "inci-two-model-prematch-v2",
  "generated_at": "2026-08-06T11:59:45Z",
  "provenance": {
    "producer": "inci-two-model-pilot",
    "model_1_id": "inci-static-bo3-v1",
    "model_2_id": "inci-dynamic-bo3-v1"
  },
  "priors": [{
    "competition_id": "espn:181730",
    "athlete_id": "espn:athlete:1",
    "opponent_athlete_id": "espn:athlete:2",
    "player_name": "Ada Ace",
    "opponent_name": "Bea Break",
    "model_as_of": "2026-08-06T11:59:30Z",
    "match_start": "2026-08-06T12:00:00Z",
    "model_1_match_probability": "0.61",
    "model_2_match_probability": "0.64"
  }]
}
```

Identity matching is exact and provider-qualified (`espn:` or `lt:`), including
the competition, selected athlete, opponent athlete, and both names. Each prior
must satisfy `model_as_of <= generated_at <= match_start`, and `match_start`
must equal the immutable Kalshi scheduled start. The default maximum snapshot
age is 24 hours. A configured snapshot that is missing, stale, malformed,
changed while being read, or fails those identity/cutoff checks blocks entry.
The first valid prior is pinned for that scoreboard orientation so a live file
rewrite cannot become a new prematch baseline. Pinning does not suspend the
maximum-age check: an old prior expires and blocks new entries. Each decision
log retains both
raw and score-updated probabilities, the snapshot digest and generation time,
both model IDs, score identity/state, and the prior cutoffs. When the environment
variable is absent, price-dip paper research can still run if the neutral
score-collapse and other safety gates pass, but the log explicitly records that
no fair-value edge was claimed.

## Sports discovery

- In dynamic mode, only Games contracts are considered across every
  advertised Games-capable competition for each selected Sport.
- No Sport or league names are embedded in selection code. Classification uses
  official Sports filters, Series, Milestones, and nested Events; display
  titles and ticker text are never classifiers.
- The session window is the machine's local `[midnight, next midnight)`.
  Startup prints both local and UTC bounds, including daylight-saving offsets.
- Eligible individual contracts are ranked together across all selected
  Sports. Inci monitors the best ten total quote streams, selected once at startup,
  with no churn during discovery and no rotation during the session. With
  `prefer_scoreboard_bind` (default on), scoreboard-bindable contracts
  (ESPN / Live Tennis) rank ahead of unbound ones; within each tier ranking
  favors executable two-sided depth, tighter spread, greater depth, earlier
  start, then ticker. With `one_contract_per_event` (default on), at most
  one YES contract per Event is traded (better eligible model edge among
  siblings when scored), and entry refuses the other side while exposed.
  A protected trade is packaged with exactly one verified opposite YES watch;
  both sides must bind to the same scoreboard competition with mutually
  reversed athlete identities. Trade plus watch streams share the ten-market
  cap. Ambiguous/same-player props are not treated as opponents, and explicit
  tickers fail closed if their required sibling cannot be proved or would
  exceed the cap. Missing sibling evidence blocks new entries but never
  suppresses a stop, time exit, or due paper SELL. A sibling mid spike
  (`sibling_spike_cents`, default 15c in 45s) blocks entry.
  Disable sibling protection explicitly before using
  `one_contract_per_event=False`; the two modes cannot be combined.
- `Config.tickers` is an explicit alternative to `--sports`: the sources are
  mutually exclusive, configured order is preserved, and the list is capped at ten.
  Every ticker must prove the complete relationship
  `Market → Event → official Series → current-day Games Milestone`.
- Partial or malformed Series, Milestone, or Event inventory fails closed and
  cannot claim a complete top ten. Recognized unsupported products are skipped
  loudly by type; malformed binary products still fail.

The chosen set is immutable for that process. Start a new paper session to
change Sports or refresh the day's candidates. When the score gate is enabled,
the same immutable manifest also records the exact provider-qualified match,
player, and opponent IDs used during discovery (plus both names for a
two-model prior). Runtime entry and strict replay reject a scorecard whose
identity does not match that discovery binding.

## Paper execution and safety

- Entries use executable ask prices; exits and risk marks use executable bids.
  Each trade gets its own causal package: capture that trade and its verified
  watch evidence, evaluate the score gate, requote that trade once, and decide
  it before any unrelated trade's network calls. Entry eligibility and paper
  fills use only that post-evidence quote, with fair-value edge recalculated at
  its ask and live-score freshness rechecked at the decision timestamp.
  Simulated IOC fills require a newly observed quote after the configured
  latency and within `fill_timeout_s`, respect available depth, and include
  estimated fees. Stop-loss fills alone apply the configured adverse
  slippage. Tiny arrival partials that are fee-negative at the configured
  take-profit are canceled.
- Stop-loss overrides every other exit, and the binding 300-second time exit
  overrides take-profit/trailing behavior. A later sibling receipt cannot turn
  an earlier pre-latency trade quote into a fill.
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
facts, book depth, fees, an immutable trade/watch market manifest, and a durable
terminal state. Quote rows also carry a trade/watch role, sweep identity, an
`evidence` or `execution` phase, and the actual post-evidence decision timestamp.
Only trade execution rows carry a score-gate decision (including both model
revisions and prior provenance when configured) and complete sibling evidence.

Replay and analysis accept only strict v6 files with exact headers, immutable
provenance, chronological rows, matching fingerprints, and a clean terminal.
Replay independently recomputes market probability, both score-updated models,
model edge, score/prior freshness, cutoff agreement, and same-package sibling
movement; logged allow/deny fields are audit evidence, not an oracle. It installs
the complete package before processing its execution row so pending orders see
the same books as the live paper runtime. Strict replay also requires the
canonical sibling `trades_v6_*.csv` ledger and exactly matches every
reconstructed fill's timestamp, ticker, side, price, quantity, fee, and reason.
Within a session, provider score timestamps, lifecycle, completed sets, and
current-set games must advance monotonically; terminal matches cannot
resurrect and score rewinds make the session non-evaluable.
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
`pnl_ledger.py` · `fees.py` · `espn_tennis.py` · `live_tennis.py` ·
`espn_prob_gate.py` · `tennis_win_prob.py` · `two_model_prior.py` · `tests.py`
