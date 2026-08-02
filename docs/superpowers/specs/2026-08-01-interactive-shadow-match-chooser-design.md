# Interactive Shadow Match Chooser Design

Date: 2026-08-01

## Outcome

The read-only tennis shadow collector gains an interactive `--choose` mode.
The operator sees a numbered list of matches that Inci can resolve uniquely
between Sportradar and Kalshi, selects one number, and the existing collector
starts with the resolved Sportradar match ID and two Kalshi match-winner
tickers. The operator no longer copies or manually pairs provider IDs.

The feature remains **READ ONLY / AUTO-MATCHED / UNQUALIFIED / NO ORDERS**.
It does not qualify a provider binding, generate a signal, simulate a trade,
or obtain execution authority.

## Operator Interface

The primary command is:

```bash
python -m inci_tennis_runtime.live_shadow_cli --choose
```

`--duration-seconds` defaults to 600 and `--poll-seconds` defaults to 10.
The existing explicit `--match-id`, `--home-ticker`, and `--away-ticker`
interface remains available for diagnostics and backward compatibility, but
the two modes are mutually exclusive.

Chooser output has two sections. `READY TO COLLECT` contains numbered rows
and is the only selectable section. `UNAVAILABLE` contains unnumbered rows
with stable reasons such as missing provider coverage, missing Kalshi pair,
pre-start, terminal, or ambiguous identity. `Q` exits without opening the
Kalshi WebSocket or starting a collection session.

## Discovery Sources

Sportradar discovery performs one GET of Tennis v3 trial live summaries through
the existing quota ledger and raw-capture boundary. That extra call is reserved
durably and included in the displayed call budget.

Kalshi discovery uses a new narrow public GET-only catalog transport. Its fixed
routes are Sports filters, Sports Series, Sports milestones, and open Events
with nested Markets. It has no order method, portfolio route, write credential,
or mutation endpoint. Existing strict response schema parsers and official
Sports/Games provenance rules are reused.

The chooser scans the complete current-day Tennis Games inventory. It does not
reuse the trading bot's top-ten contract ranking because that ranking may split
two sibling markets or omit a valid game.

## Match Resolution

A Kalshi candidate must prove all of the following:

- official canonical Sport is `Tennis` and scope is `Games`;
- the Milestone identifies one main game Event, or exactly one primary Event;
- the Event is open and contains exactly two supported active binary $1
  non-MVE Markets;
- the two Markets have distinct, non-placeholder `yes_sub_title` player names;
- the two Markets form a unique player-to-ticker bijection.

Player names are normalized only with Unicode NFKC, whitespace collapse, and
case folding. Ticker parsing, event-title parsing, nickname inference, edit
distance, token dropping, and fuzzy matching are prohibited.

An edge exists only when the unordered normalized Kalshi player pair exactly
equals the unordered Sportradar home/away pair and scheduled starts differ by
no more than 900 seconds. Tickers are then reordered to Sportradar home/away
order. Both sides of the match graph must have degree one. Duplicate names,
multiple candidate events, multiple provider rows, or a non-bijective mapping
make the row unavailable.

Only Sportradar rows with lifecycle `live` are selectable. Pre-start and
terminal rows are displayed as unavailable rather than silently treated as
live.

## Selection And Evidence

Rows are sorted by scheduled start, provider match ID, and Kalshi Event ticker.
The displayed number indexes an immutable discovery snapshot. Before collector
startup, the chosen resolution record includes both identities, the two source
capture hashes, the resolver-rule version, and a canonical snapshot digest.
The selection is persisted with the shadow session evidence before live frames
are accepted.

This is an unqualified automatic correlation, not a production match binding.
Kalshi's current normalized Sports metadata does not provide a cross-provider
stable player ID, so display-name and start-time agreement cannot be promoted
to trusted execution authority.

## Failure Behavior

- Zero selectable rows exits without opening the WebSocket.
- Invalid input reprompts without repeating network discovery.
- EOF, `Q`, or Ctrl-C exits cleanly without a collector session.
- Any schema, quota, raw-persistence, pagination, identity, or scope failure
  fails closed with a sanitized stable code.
- A selection cannot be made from an unavailable or ambiguous row.
- Existing collector cancellation, reconnect, terminal-record, and read-only
  credential-scope guarantees remain unchanged.

## Verification

Tests must first fail and then cover exact/reversed player order, Unicode and
whitespace normalization, 900-second start boundary, two-sided graph
ambiguity, duplicate names, placeholder names, wrong product cardinality,
pre-start/terminal filtering, stable sorting, invalid input reprompt, quit/EOF,
single-discovery behavior, quota accounting, immutable selection binding,
no-WebSocket-on-empty-or-quit, evidence persistence, GET-only route authority,
and regression compatibility with the explicit CLI mode.

Automated tests use injected HTTP/WebSocket responses and consume no live
Sportradar trial calls or Kalshi requests.
