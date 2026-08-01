# One-Match Tennis Shadow Monitor Implementation Plan

**Goal:** Build an offline/replay terminal monitor for one bound tennis match
and both match-winner contracts in the pre-reserved runtime paths, with
explicit source-inventory and AST-seal updates and no change to deterministic
expert or adapter behavior.

**Architecture:** The pre-reserved `inci_tennis_runtime/shadow_runtime.py`
consumes immutable `SynchronizationTransitionResult` values. A pure projector
produces immutable view contracts and a dependency-free renderer formats them.
`inci_tennis_runtime/shadow_cli.py` supplies an offline synthetic sample and a
small terminal entry point. Focused tests exercise the projector through the
real synchronizer.

## Task 1: Define RED monitor behavior

- Add focused tests for the immutable view model, transition projection,
  pricing, missing data, validation, and rendering.
- Run the focused test file and confirm the missing implementation fails.

## Task 2: Implement the pure projector and renderer

- Add monitor view contracts and `OneMatchShadowMonitor`.
- Project tennis and book cursors from validated transitions.
- Derive executable top-of-book values using `Decimal`.
- Add deterministic plain/ANSI rendering with no side effects in the core.
- Run focused tests to GREEN.

## Task 3: Add deterministic replay and CLI

- Build a deterministic synthetic display sequence for CLI inspection. Drive
  the projector with real binding, tennis-score, market-book, and
  synchronization contracts in the focused runtime tests.
- Add `python -m inci_tennis_runtime.shadow_cli --sample`.
- Add `--all-stages` for a plain progression; keep TTY framing separately
  callable for a future qualified owner loop.
- Test CLI argument and rendering behavior with no clock or sleep authority.

## Task 4: Verify boundaries

- Run the focused monitor tests.
- Run synchronizer, market-book, tennis-score, and expert dependency-boundary
  tests.
- Run a direct sample CLI smoke test.
- Update and verify the explicit runtime inventory and AST seals.
- Remove repository bytecode/cache artifacts and report only Task 2 results.
