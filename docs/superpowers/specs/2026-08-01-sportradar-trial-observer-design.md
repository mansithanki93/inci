# Sportradar Tennis Trial Observer — Implemented Design

Date: 2026-08-01

## Outcome

Inci now has a separate, read-only Sportradar Tennis v3 trial observer. It can discover live matches, validate one match, or monitor one match's timeline. It records raw provider responses and audit metadata for later research. It does not feed the strategy, place Kalshi orders, or possess trading authority.

The implementation follows Sportradar's documented Tennis v3 summary and timeline shapes. Sportradar documents a one-second timeline cache and live point/game events in the [Sport Event Timeline reference](https://developer.sportradar.com/tennis/reference/sport-event-timeline). Its [Match Status Workflow](https://developer.sportradar.com/tennis/docs/ig-match-status-workflow) distinguishes overall `status` from detailed `match_status` and treats only `closed`, `cancelled`, and `abandoned` as terminal; `ended` remains observable until confirmation.

## Operator interface

The key is read only from the process environment:

```bash
export SPORTRADAR_API_KEY='your-trial-key'
```

Commands:

```bash
python -m inci_tennis_runtime.sportradar_trial_cli --list-live
python -m inci_tennis_runtime.sportradar_trial_cli --check --match-id sr:sport_event:123456
python -m inci_tennis_runtime.sportradar_trial_cli --observe --match-id sr:sport_event:123456 --duration-seconds 600
```

`--observe` polls every 10 seconds and accepts a duration from 10 through 3,600 seconds. The terminal table shows players, lifecycle state, sets, current-set games, points, server, tiebreak state, last event, source age, and remaining local trial-call budgets. Unknown provider fields display as `--`; they are never invented.

## Runtime boundaries

- Fixed origin: `https://api.sportradar.com`.
- Fixed GET-only trial routes: live summaries, one-match summary, and one-match timeline.
- Authentication: `x-api-key`; the key is redacted from representations and diagnostics.
- An owned `requests.Session` has `trust_env=False`, so ambient proxy and netrc credentials are not inherited.
- Redirects are disabled, content must be identity-encoded JSON, and bodies are capped at 8 MiB.
- Connect/read timeouts are 3/10 seconds. HTTP, body reading, and response close are inside a 15-second hard deadline.
- Responses reflecting the credential directly or through supported JSON escaping are rejected before raw persistence.
- Parsing is strict: duplicate keys, non-finite numbers, malformed identifiers/timestamps, unknown contract values, invalid score structures, and impossible timeline progression halt the observer.
- A live nonterminal response older than 60 seconds halts. A provider timestamp more than five seconds in the future halts. Terminal state takes precedence over stale-state rejection.
- SIGINT, SIGTERM, and SIGHUP request shutdown at safe boundaries; the session terminal record is persisted before exit where the filesystem remains available.

## Quota and durable evidence

The observer enforces at least one second between requests, no more than 400 attempts in one invocation, and no more than 1,000 attempts in the local lifetime ledger. An attempt is reserved and fsynced before its HTTP call, so crashes cannot refund trial quota.

State is private to the OS account under:

```text
~/.local/state/inci/sportradar-trial/
├── usage.lock
├── usage.jsonl
├── outcomes.jsonl
├── observations.jsonl
└── raw/
```

Only one observer process can own that state at a time. Captures, outcomes, observations, parser failures, and session terminals are cross-validated at startup. Missing outcomes, raw captures without a disposition, and sessions without terminal records are recovered conservatively and reported. Corrupt or inconsistent state fails closed.

## Verification completed

- Observer suite: 51/51 passed.
- Legacy Inci suite: 202/202 passed.
- Sportradar candidate suites: 75/76 passed; the sole failure is a pre-existing, unrelated test-only fixture-name check in `tools/task9_transition_evidence.py`.
- Dependency-boundary suite: 41/42 passed; the sole error is a pre-existing, unrelated legacy import from `tennis_v1/ingress.py`.
- All observer-related AST seals, package inventories, GET-only restrictions, and execution-authority prohibitions passed.
- CLI help smoke test passed.
- No live Sportradar request was made during automated verification.

## Residual limits

- Durable reservation/raw/audit fsync operations are deliberately not interrupted asynchronously. A stalled filesystem can therefore exceed the 15-second network deadline.
- Quota and 1-QPS enforcement are local to one OS account, machine, and state root. Reusing the same key elsewhere bypasses this ledger.
- Audit validation detects corruption and inconsistent state but is not cryptographically tamper-proof against an actor controlling the same OS account.
- Current provider compatibility still requires the first operator-run `--list-live` and `--check`; automated tests used captured contract fixtures and no real provider request.
- This observer only collects trustworthy provider evidence. Connecting observations to match binding, offline qualification, strategy decisions, or any execution path is a separate reviewed change.
