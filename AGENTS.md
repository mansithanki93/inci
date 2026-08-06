# AGENTS.md

## Cursor Cloud specific instructions

Inci is an **offline research bot** for Kalshi Sports scalping. There is no web
server, API, database, or long-running daemon — "running the product" means
running CLIs and test suites. Two generations coexist:

- **Legacy v6** — root-level modules (`bot.py`, `analyze.py`, `replay.py`,
  `tests.py`, ...). This is the primary product and the primary test gate.
- **Tennis v1** — a separate, research-only event core in the `tennis_v1/` and
  `inci_tennis_*/` packages, exercised via `tests/tennis_v1/` and the
  `inci_tennis_runtime` CLIs.

### Interpreter / venv (already provisioned by the update script)

- The startup update script creates a **Python 3.14.5** virtualenv at `.venv`
  (matching the `==3.14.5` pin in `pyproject.toml`) via `uv` and installs
  `requirements.txt` into it. Activate it before doing anything:
  `source .venv/bin/activate`.
- Only `requests` and `cryptography` are real runtime deps; the `tennis_v1`
  core is stdlib-only. No lint/format/type/build tooling exists in this repo,
  so there are no lint or build commands to run.

### Running the applications

- Legacy live discovery (hits the **public Kalshi API**, needs network):
  `python bot.py --list-sports` and `python bot.py --sports Tennis,Basketball`.
- Legacy paper session: `python bot.py` (default paper mode) or
  `python bot.py --sports <names>`. It discovers live markets and simulates
  until local midnight; stop it with `Ctrl-C`/`SIGTERM` to flush a **clean
  session terminal record** and write `logs/ticks_v6_*.csv` +
  `logs/trades_v6_*.csv`.
- Legacy replay/analysis: `python analyze.py logs/ticks_v6_<...>.csv`. Note the
  partial sample CSVs committed at the repo root (`ticks_v6_*.csv`) lack a
  clean terminal record and are rejected by `analyze.py`; generate a real log
  from a paper session first (as above).
- `python bot.py --check` and other authenticated calls need
  `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` (RSA `.pem`, kept outside
  the repo). Not required for offline work. Order/live/demo paths are hard
  disabled by design.
- Tennis v1 offline event-core dashboard (deterministic, no network):
  `python -m inci_tennis_runtime.shadow_cli --sample --all-stages --no-ansi`.

### Tests — invocation gotchas

- Legacy suite (primary gate, fully offline): `python tests.py` → prints
  `ALL TESTS PASS (202 tests)`.
- Tennis v1 tests MUST be discovered with the **repo root** as the top-level
  dir, otherwise the `tests/tennis_v1` package name shadows the real
  `tennis_v1` package and every module fails to import:
  `python -m unittest discover -s tests/tennis_v1 -t . -p 'test_*.py'`
  (or run a single module, e.g. `python -m unittest tests.tennis_v1.test_wal`).

### Known pre-existing Tennis v1 test failures (NOT environment issues)

These fail on any machine and are unrelated to setup — do not try to "fix" them
by changing the interpreter or reinstalling:

- `test_retention.py` and `test_task9_transition_evidence.py` hardcode the
  original author's path `/Users/mthanki/.venvs/inci-expert-py314/bin/python`
  and spawn it as a subprocess, so they fail everywhere else.
- `test_dependency_boundary` / `test_expert_dependency_boundary` compare frozen
  AST-digest tables that are (a) pinned to CPython 3.14.5 and (b) currently out
  of sync with the source (e.g. `entitlements.py`); `test_legacy_baseline`
  compares a frozen source sha256.
- Several `inci_tennis_expert` tests fail on pinned `schema_resource_sha256`
  digests, and `test_expert_controller` / `test_expert_replay` can hang under
  discovery — run modules individually with a timeout when triaging.

Using Python 3.14.5 (vs the system 3.12) markedly reduces these failures but
does not eliminate the hardcoded-path / out-of-sync-digest ones.
