# One-Match Tennis Shadow Monitor Design

Date: 2026-08-01
Status: Approved for implementation
Authority: offline/replay observation only

## Purpose

Provide a readable, dependency-free terminal monitor for one bound tennis
match and its two match-winner contracts. The monitor exposes what the Tennis
v1 synchronization core currently knows without making a prediction,
recommendation, paper order, or real order.

## Safety boundary

- No network, credentials, provider registration, or order routes.
- No fair value, edge, BUY/SELL recommendation, P&L, or execution simulation.
- Input is an already-produced `SynchronizationTransitionResult`.
- The monitor never mutates synchronization state.
- Missing data is displayed as `--`. A blocked/waiting contract hides every
  book-derived executable value and displays the exact `SyncReason`. Raw
  provider score remains observational and is paired with its age; it never
  grants a contract trusted status.
- The sealed expert and adapter logic is not modified. The monitor uses the
  pre-reserved `inci_tennis_runtime/shadow_runtime.py` and
  `inci_tennis_runtime/shadow_cli.py` paths and is added to the explicit
  runtime source inventory and AST seal.

The current repository has no qualified production provider or Kalshi
transport. A deterministic display sample is therefore the first runnable
source. It does not claim to replay exchange/provider evidence. Focused tests
drive the projector through the real synchronization core. A qualified live
runtime can later call the same monitor API without changing its projection or
rendering logic.

## Display

The header shows:

- canonical match ID and provider match ID;
- match status;
- completed sets and current games/points;
- next server;
- provider revision and provider age;
- latest observation time and decision sequence.

The contract table shows both match-winner tickers:

- bound player side;
- market status;
- executable YES bid and derived YES ask;
- top-of-book bid/ask quantity;
- spread;
- book age;
- connection epoch and sequence;
- trusted/untrusted state and the exact synchronization reason.

YES ask is derived from the best NO bid as `1 - best_no_bid`. Prices display
as cents but remain `Decimal` internally. An absent side is `--`, never zero.

## Components

1. `OneMatchShadowMonitor` validates that each transition belongs to the
   configured match, projects the latest tennis/book cursors, and retains the
   latest reason independently for each ticker.
2. Immutable monitor view contracts separate projection from presentation.
3. A plain-text renderer always works. An ANSI renderer clears and refreshes
   only when output is a TTY and ANSI has not been disabled.
4. A deterministic synthetic sample sequence demonstrates incomplete and
   trusted views without external access. Focused tests drive the projector
   through the real synchronization core.
5. The CLI prints the final sample snapshot or every sample stage. A future
   qualified owner loop can pass views to the same terminal-frame function for
   in-place refresh; this task does not add a clock or sleep authority.

## Failure behavior

- Wrong match/ticker, malformed transitions, time regressions, or ambiguous
  bindings fail loudly.
- Renderer failures fall back to a concise plain-text diagnostic.
- Terminal width truncates display text only; underlying values are retained.
- No exception can trigger an external side effect because the package owns
  no external capability.

## Verification

Focused tests cover:

- initial incomplete state;
- trusted two-contract projection;
- score/server formatting;
- exact executable-price and depth derivation;
- stale/untrusted reasons without fabricated values;
- wrong-match rejection and observation regression;
- deterministic rendering and narrow-terminal behavior;
- ANSI/plain output selection;
- sample replay reaching a trusted synchronized state;
- static absence of network, credential, and execution imports/calls.
