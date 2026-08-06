# Agent notes

## Cursor Cloud specific instructions

- Canonical suite: `.venv/bin/python tests.py` (218 tests). Prefer the venv interpreter pinned by `.cursor/install.sh` (CPython 3.14.5).
- Paper path is **delayed IOC / taker-at-touch**, not maker GTC. `Config.time_in_force=immediate_or_cancel` is authoritative: one attempt after `sim_latency_s`, depth-capped, remainder canceled. Do not describe paper fills as resting/maker.
- Take-profit is **trailing by default**: `take_profit` arms the floor; `tp_trail_cents` (default 2) sells on giveback from the peak bid so runners can extend. `tp_trail_cents=0` restores fixed arm exit. Stops still fire immediately.
- Paper runtime attaches a **score + win-prob entry gate** (`espn_prob_gate.py`): ESPN ATP/WTA plus optional **Live Tennis API** ITF secondary feed (`live_tennis.py`). Entries need a name-bind to a live/pre match and model edge vs ask. ITF is consulted only after ESPN bind fails for tickers matching `live_tennis_ticker_substrings` (default `ITF`). Without `LIVETENNISAPI_KEY` (or `Config.live_tennis_api_key`), ITF stays fail-closed. Free tier is ~100 req/day — default `live_tennis_cache_s=120`. Sportradar is not used. Toggle: `Config.espn_gate_enabled` / `Config.live_tennis_enabled`.
- Discovery keeps `max_monitored_markets=10` but with `prefer_scoreboard_bind=True` (default) ranks **scoreboard-bindable** contracts ahead of unbound ones (depth/spread ranking within each tier). Gate caches are built before discovery so the same ESPN/LT snapshot is reused for entry checks.
- `one_contract_per_event=True` (default): at most one YES contract per Event in the monitored set (better model edge wins among siblings when the gate can score). Entry also blocks if another side of the same Event is already open/pending.
- Exit priority while a SELL is pending: stop-loss > time exit > take-profit. Never cancel/resubmit an equal-priority pending exit (that slides `due_at`). Upgrade only when priority increases.
- Entry size/edge uses visible ask depth (`min(contracts_per_trade, ask_qty)`). Unknown/zero ask depth cannot enter.
- Run paper: `.venv/bin/python bot.py --sports Tennis,Basketball` (long-running; use `timeout -s INT` or tmux). State lives under `~/.local/state/inci/production/subaccount-0/`.
- Standard setup/run docs: `README.md`. Env bootstrap: `.cursor/environment.json` + `.cursor/install.sh`.
