from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
import unittest

from inci_tennis_expert.contracts import (
    BookLevel,
    MatchStatus,
    PlayerSide,
    ScoreValue,
    SetScore,
    SyncReason,
)
from inci_tennis_expert.synchronizer import (
    synchronization_session_from_artifacts,
    synchronize,
)
from inci_tennis_runtime.shadow_runtime import (
    MonitorInputError,
    OneMatchShadowMonitor,
    SyncDisplayState,
    render_monitor,
)
from tests.tennis_v1.test_synchronizer import (
    book_origin,
    clock_input,
    initial_book_input,
    observation,
    origin_input,
    policy,
    provider_origin,
    universe,
)


ROOT = Path(__file__).resolve().parents[2]


def apply(monitor, state, evidence, now):
    transition = synchronize(state, evidence, now=now)
    monitor.accept(transition)
    return transition.state, transition


def synchronized_monitor():
    value = universe()
    binding = value.bindings[0]
    state = synchronization_session_from_artifacts(value, policy(value))
    monitor = OneMatchShadowMonitor(state, binding.canonical_match_id)
    provider = provider_origin(
        binding,
        received_monotonic_ns=100,
        completed_sets=(SetScore(6, 4, None, None),),
        games_home=3,
        games_away=2,
        points_home=ScoreValue.THIRTY,
        points_away=ScoreValue.FIFTEEN,
        server_for_next_point=PlayerSide.HOME,
    )
    state, _ = apply(
        monitor,
        state,
        origin_input(provider, binding.canonical_match_id),
        observation(101),
    )
    home = book_origin(
        binding.home_market_ticker,
        observed_monotonic_ns=101,
        yes_bids=(BookLevel(Decimal("0.27"), Decimal("12.5")),),
        no_bids=(BookLevel(Decimal("0.70"), Decimal("8")),),
    )
    state, _ = apply(
        monitor,
        state,
        initial_book_input(binding.canonical_match_id, home),
        observation(102),
    )
    away = book_origin(
        binding.away_market_ticker,
        observed_monotonic_ns=102,
        yes_bids=(BookLevel(Decimal("0.68"), Decimal("6")),),
        no_bids=(BookLevel(Decimal("0.29"), Decimal("4.5")),),
    )
    state, transition = apply(
        monitor,
        state,
        initial_book_input(binding.canonical_match_id, away),
        observation(103),
    )
    return monitor, state, binding, transition


class ProjectionTests(unittest.TestCase):
    def test_initial_view_lists_both_contracts_without_inventing_data(self):
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(value, policy(value))
        monitor = OneMatchShadowMonitor(state, binding.canonical_match_id)

        view = monitor.view()

        self.assertEqual(view.canonical_match_id, binding.canonical_match_id)
        self.assertEqual(view.provider_match_id, binding.provider_match_id)
        self.assertIsNone(view.match_status)
        self.assertEqual(view.decision_sequence, 0)
        self.assertEqual(
            tuple(row.ticker for row in view.contracts),
            (binding.home_market_ticker, binding.away_market_ticker),
        )
        self.assertEqual(
            tuple(row.player_side for row in view.contracts),
            (PlayerSide.HOME, PlayerSide.AWAY),
        )
        for row in view.contracts:
            self.assertIsNone(row.yes_bid)
            self.assertIsNone(row.yes_ask)
            self.assertIsNone(row.spread)
            self.assertEqual(row.sync_state, SyncDisplayState.WAITING)
            self.assertEqual(row.reason, SyncReason.SNAPSHOT_INCOMPLETE)

    def test_trusted_projection_uses_executable_prices_depth_and_score(self):
        monitor, _, binding, _ = synchronized_monitor()

        view = monitor.view()
        home, away = view.contracts

        self.assertEqual(view.provider_match_id, binding.provider_match_id)
        self.assertEqual(view.match_status, MatchStatus.LIVE)
        self.assertEqual(view.completed_sets, ((6, 4),))
        self.assertEqual(view.games, (3, 2))
        self.assertEqual(view.points, ("30", "15"))
        self.assertEqual(view.server, PlayerSide.HOME)
        self.assertEqual(view.provider_revision, 0)
        self.assertEqual(view.provider_age_ns, 3)
        self.assertEqual(home.yes_bid, Decimal("0.27"))
        self.assertEqual(home.yes_ask, Decimal("0.30"))
        self.assertEqual(home.bid_quantity, Decimal("12.5"))
        self.assertEqual(home.ask_quantity, Decimal("8"))
        self.assertEqual(home.spread, Decimal("0.03"))
        self.assertEqual(home.book_age_ns, 2)
        self.assertEqual(home.sync_state, SyncDisplayState.TRUSTED)
        self.assertEqual(home.reason, SyncReason.TRUSTED_SYNCHRONIZED)
        self.assertEqual(away.yes_bid, Decimal("0.68"))
        self.assertEqual(away.yes_ask, Decimal("0.71"))
        self.assertEqual(away.spread, Decimal("0.03"))
        self.assertEqual(away.sync_state, SyncDisplayState.TRUSTED)

    def test_blocked_result_hides_observed_book_values_and_preserves_reason(self):
        monitor, state, binding, _ = synchronized_monitor()

        state, _ = apply(
            monitor,
            state,
            clock_input(binding.canonical_match_id, binding.home_market_ticker),
            observation(2_000),
        )
        del state
        home = monitor.view().contracts[0]

        self.assertIsNone(home.market_status)
        self.assertIsNone(home.yes_bid)
        self.assertIsNone(home.yes_ask)
        self.assertIsNone(home.bid_quantity)
        self.assertIsNone(home.ask_quantity)
        self.assertIsNone(home.spread)
        self.assertIsNone(home.book_age_ns)
        self.assertIsNone(home.connection_epoch)
        self.assertIsNone(home.sequence)
        self.assertEqual(home.sync_state, SyncDisplayState.BLOCKED)
        self.assertEqual(home.reason, SyncReason.SCORE_STALE)

    def test_future_source_times_render_missing_ages_instead_of_crashing(self):
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(value, policy(value))
        monitor = OneMatchShadowMonitor(state, binding.canonical_match_id)
        provider = provider_origin(
            binding,
            received_monotonic_ns=120,
        )
        state, _ = apply(
            monitor,
            state,
            origin_input(provider, binding.canonical_match_id),
            observation(110),
        )
        self.assertIsNone(monitor.view().provider_age_ns)

        book = book_origin(
            binding.home_market_ticker,
            observed_monotonic_ns=130,
        )
        state, _ = apply(
            monitor,
            state,
            initial_book_input(binding.canonical_match_id, book),
            observation(111),
        )
        del state
        home = monitor.view().contracts[0]
        self.assertEqual(home.sync_state, SyncDisplayState.BLOCKED)
        self.assertEqual(home.reason, SyncReason.CLOCK_UNCERTAIN)
        self.assertIsNone(home.book_age_ns)
        self.assertIsNone(home.yes_bid)

    def test_rejects_transition_for_another_match_and_replayed_transition(self):
        value = universe(2)
        first, second = value.bindings
        state = synchronization_session_from_artifacts(value, policy(value))
        monitor = OneMatchShadowMonitor(state, first.canonical_match_id)
        foreign = synchronize(
            state,
            origin_input(provider_origin(second), second.canonical_match_id),
            now=observation(101),
        )
        with self.assertRaisesRegex(MonitorInputError, "wrong_match"):
            monitor.accept(foreign)

        own = synchronize(
            state,
            origin_input(provider_origin(first), first.canonical_match_id),
            now=observation(101),
        )
        monitor.accept(own)
        with self.assertRaisesRegex(MonitorInputError, "transition_invalid"):
            monitor.accept(own)

    def test_rejects_wrong_ticker_malformed_input_and_unknown_binding(self):
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(value, policy(value))
        monitor = OneMatchShadowMonitor(state, binding.canonical_match_id)
        wrong_ticker = synchronize(
            state,
            clock_input(binding.canonical_match_id, "WRONGTICKER"),
            now=observation(101),
        )
        with self.assertRaisesRegex(MonitorInputError, "wrong_ticker"):
            monitor.accept(wrong_ticker)
        with self.assertRaisesRegex(MonitorInputError, "transition_invalid"):
            monitor.accept(object())
        with self.assertRaisesRegex(MonitorInputError, "match_binding"):
            OneMatchShadowMonitor(state, "unknown-match")


class RenderingTests(unittest.TestCase):
    def test_plain_renderer_is_readable_and_uses_missing_markers(self):
        value = universe()
        binding = value.bindings[0]
        state = synchronization_session_from_artifacts(value, policy(value))
        rendered = render_monitor(
            OneMatchShadowMonitor(state, binding.canonical_match_id).view(),
            width=140,
        )

        self.assertIn("INCI TENNIS SHADOW", rendered)
        self.assertIn("REPLAY / READ-ONLY", rendered)
        self.assertIn(binding.home_market_ticker, rendered)
        self.assertIn(binding.away_market_ticker, rendered)
        self.assertIn("snapshot_incomplete", rendered)
        self.assertIn("--", rendered)
        self.assertNotIn("BUY", rendered)
        self.assertNotIn("SELL", rendered)

    def test_renderer_formats_cents_and_narrow_width_deterministically(self):
        monitor, _, _, _ = synchronized_monitor()
        full = render_monitor(monitor.view(), width=160)
        narrow = render_monitor(monitor.view(), width=72)

        self.assertIn("27.0c", full)
        self.assertIn("30.0c", full)
        self.assertIn("3.0c", full)
        self.assertLessEqual(max(map(len, narrow.splitlines())), 72)
        self.assertEqual(narrow, render_monitor(monitor.view(), width=72))

    def test_shadow_runtime_has_no_external_or_order_authority(self):
        source = ROOT / "inci_tennis_runtime" / "shadow_runtime.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        forbidden_imports = {
            "requests",
            "socket",
            "urllib",
            "websocket",
            "websockets",
            "cryptography",
            "kalshi_client",
            "executor",
        }
        imported = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(imported & forbidden_imports)
        self.assertFalse(calls & {"create_order", "cancel_order", "post", "put", "delete"})


if __name__ == "__main__":
    unittest.main()
