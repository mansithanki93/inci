# Agent notes

## Cursor Cloud specific instructions

- Canonical suite: `.venv/bin/python tests.py` (208 tests). Prefer the venv interpreter pinned by `.cursor/install.sh` (CPython 3.14.5).
- Paper path is **delayed IOC / taker-at-touch**, not maker GTC. `Config.time_in_force=immediate_or_cancel` is authoritative: one attempt after `sim_latency_s`, depth-capped, remainder canceled. Do not describe paper fills as resting/maker.
- Take-profit is **trailing by default**: `take_profit` arms the floor; `tp_trail_cents` (default 2) sells on giveback from the peak bid so runners can extend. `tp_trail_cents=0` restores fixed arm exit. Stops still fire immediately.
- Exit priority while a SELL is pending: stop-loss > time exit > take-profit. Never cancel/resubmit an equal-priority pending exit (that slides `due_at`). Upgrade only when priority increases.
- Entry size/edge uses visible ask depth (`min(contracts_per_trade, ask_qty)`). Unknown/zero ask depth cannot enter.
- Run paper: `.venv/bin/python bot.py --sports Tennis,Basketball` (long-running; use `timeout -s INT` or tmux). State lives under `~/.local/state/inci/production/subaccount-0/`.
- Standard setup/run docs: `README.md`. Env bootstrap: `.cursor/environment.json` + `.cursor/install.sh`.
