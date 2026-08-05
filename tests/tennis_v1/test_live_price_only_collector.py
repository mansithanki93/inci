from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from hashlib import sha256
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest

from inci_tennis_io.shadow_evidence import (
    PriceOnlyEvidenceObservation,
    PriceOnlySessionEvidence,
    ShadowMarketCandidate,
)
from inci_tennis_runtime.live_price_only_collector import (
    PriceOnlyDashboardView,
    PriceOnlyShadowCollector,
    render_price_only_dashboard,
)
from inci_tennis_runtime.live_shadow_collector import (
    CandidateMarketProjection,
    CandidateMarketView,
    ShadowCollectorError,
)


EVENT_TICKER = "KXTENNIS-EVENT"
TICKERS = ("KXTENNIS-EVENT-A", "KXTENNIS-EVENT-B")
WALL_NS = 1_785_607_205_000_000_000


class _CodedError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Clock:
    def __init__(self) -> None:
        self.monotonic = 1_000_000_000
        self.pauses: list[float] = []

    def monotonic_ns(self) -> int:
        return self.monotonic

    def wall_ns(self) -> int:
        return WALL_NS + self.monotonic

    def advance(self, seconds: float) -> None:
        self.monotonic += int(seconds * 1_000_000_000)

    async def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.advance(seconds)
        await asyncio.sleep(0)


class _Frame:
    def __init__(
        self,
        payload: bytes,
        clock: _Clock,
        *,
        generation: int = 1,
    ) -> None:
        self.payload = payload
        self.captured_wall_ns = clock.wall_ns()
        self.captured_monotonic_ns = clock.monotonic_ns()
        self.clock_uncertainty_ns = 7
        self.physical_connection_generation = generation
        self.raw_sha256 = sha256(payload).hexdigest()


class _Transport:
    def __init__(
        self,
        clock: _Clock,
        outcomes: list[object],
        events: list[str] | None = None,
        *,
        open_failures: set[int] | None = None,
        close_failures: set[int] | None = None,
        snapshot_failures: set[int] | None = None,
    ) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.events = [] if events is None else events
        self.open_failures = set() if open_failures is None else open_failures
        self.close_failures = set() if close_failures is None else close_failures
        self.snapshot_failures = (
            set() if snapshot_failures is None else snapshot_failures
        )
        self.open_calls = 0
        self.subscribe_calls = 0
        self.close_calls = 0
        self.snapshot_requests: list[int] = []

    async def open_readonly(self) -> None:
        self.open_calls += 1
        self.events.append("open")
        if self.open_calls in self.open_failures:
            raise _CodedError("kalshi_ws_connect_failed")

    async def subscribe(self) -> object:
        self.subscribe_calls += 1
        self.events.append("subscribe")
        return SimpleNamespace(
            request_id=self.subscribe_calls,
            physical_connection_generation=self.open_calls,
            command="subscribe",
        )

    async def receive_one(self, timeout_seconds: float) -> object:
        self.clock.advance(min(timeout_seconds, 1.0))
        if self.outcomes:
            value = self.outcomes.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        raise _CodedError("kalshi_ws_receive_timeout")

    async def request_snapshot(self, sid: int) -> object:
        self.events.append("snapshot")
        self.snapshot_requests.append(sid)
        if len(self.snapshot_requests) in self.snapshot_failures:
            raise _CodedError("kalshi_ws_snapshot_failed")
        return SimpleNamespace(
            request_id=100 + len(self.snapshot_requests),
            physical_connection_generation=self.open_calls,
            command="get_snapshot",
        )

    async def close(self) -> None:
        self.close_calls += 1
        self.events.append("close")
        if self.close_calls in self.close_failures:
            raise _CodedError("kalshi_ws_close_failed")


class _Projector:
    def __init__(self, callback, events: list[str] | None = None) -> None:
        self.callback = callback
        self.events = [] if events is None else events
        self.subscriptions: list[object] = []
        self.snapshots: list[object] = []
        self.disconnects: list[int | None] = []

    def begin_subscription(self, receipt: object) -> None:
        self.subscriptions.append(receipt)

    def apply(self, frame: object) -> CandidateMarketProjection:
        self.events.append("project")
        return self.callback(frame)

    def snapshot_requested(self, receipt: object) -> None:
        self.snapshots.append(receipt)

    def disconnect(self, generation: int | None) -> None:
        self.disconnects.append(generation)


class _Evidence:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = [] if events is None else events
        self.sessions: list[PriceOnlySessionEvidence] = []
        self.observations: list[PriceOnlyEvidenceObservation] = []
        self.terminals: list[dict[str, object]] = []
        self.frames: list[object] = []

    def append_price_only_session(self, record: PriceOnlySessionEvidence) -> None:
        self.sessions.append(record)
        self.events.append("session")

    def persist_kalshi_frame(self, frame: object) -> object:
        self.frames.append(frame)
        self.events.append("persist")
        return SimpleNamespace(
            raw_path=f"/private/tmp/frame-{len(self.frames)}.bin",
            raw_sha256=frame.raw_sha256,
            captured_wall_ns=frame.captured_wall_ns,
            captured_monotonic_ns=frame.captured_monotonic_ns,
            clock_uncertainty_ns=frame.clock_uncertainty_ns,
            physical_connection_generation=(
                frame.physical_connection_generation
            ),
        )

    def append_price_only_observation(
        self, record: PriceOnlyEvidenceObservation
    ) -> None:
        self.observations.append(record)
        self.events.append("observation")

    def append_price_only_terminal(self, **values: object) -> None:
        self.terminals.append(values)
        self.events.append("terminal")


class _BlockingEvidence(_Evidence):
    def __init__(self, blocked_operation: str) -> None:
        super().__init__()
        self.blocked_operation = blocked_operation
        self.started = threading.Event()
        self.release = threading.Event()

    def _block(self, operation: str) -> None:
        if self.blocked_operation == operation:
            self.started.set()
            if not self.release.wait(2):
                raise AssertionError("test durability gate timed out")

    def append_price_only_session(self, record: PriceOnlySessionEvidence) -> None:
        self._block("session")
        super().append_price_only_session(record)

    def persist_kalshi_frame(self, frame: object) -> object:
        self._block("raw")
        return super().persist_kalshi_frame(frame)

    def append_price_only_observation(
        self, record: PriceOnlyEvidenceObservation
    ) -> None:
        self._block("observation")
        super().append_price_only_observation(record)

    def append_price_only_terminal(self, **values: object) -> None:
        self._block("terminal")
        super().append_price_only_terminal(**values)


def _session() -> PriceOnlySessionEvidence:
    empty_a = ShadowMarketCandidate(TICKERS[0], None, None, None, None)
    empty_b = ShadowMarketCandidate(TICKERS[1], None, None, None, None)
    return PriceOnlySessionEvidence(
        selected_wall_ns=WALL_NS,
        selected_monotonic_ns=1_000_000_000,
        event_ticker=EVENT_TICKER,
        player_a_name="Player A",
        player_b_name="Player B",
        market_tickers=TICKERS,
        scheduled_start_wall_ns=WALL_NS - 5_000_000_000,
        catalog_sport="Tennis",
        catalog_scope="Games",
        catalog_queried_competitions=("ATP",),
        catalog_series_ticker="KXTENNIS",
        catalog_milestone_id="milestone-1",
        catalog_milestone_league="ATP",
        initial_book_state="empty",
        initial_market_a=empty_a,
        initial_market_b=empty_b,
        provider_discovery_state="unavailable",
        provider_discovery_reason="provider_not_required",
        provider_discovery_raw_path=None,
        provider_discovery_raw_sha256=None,
        kalshi_catalog_sha256="a" * 64,
        resolver_snapshot_sha256="b" * 64,
        resolver_version="kalshi-first-hybrid-v1",
        registry_digest="c" * 64,
    )


def _projection(
    *,
    status: str = "candidate",
    reason: str = "candidate_book_ready",
    generation: int = 1,
    sequence: int = 2,
    sid: int = 27,
    snapshot_needed: bool = False,
) -> CandidateMarketProjection:
    if status == "candidate":
        markets = (
            CandidateMarketView(
                TICKERS[0], "0.31", "0.34", "12.00", "8.00"
            ),
            CandidateMarketView(
                TICKERS[1], "0.66", "0.69", "6.00", "9.00"
            ),
        )
    else:
        markets = (
            CandidateMarketView(TICKERS[0], None, None, None, None),
            CandidateMarketView(TICKERS[1], None, None, None, None),
        )
    return CandidateMarketProjection(
        markets=markets,
        generation=generation,
        sequence=sequence,
        subscription_id=sid,
        status=status,
        reason=reason,
        snapshot_needed=snapshot_needed,
    )


def _collector(
    *,
    clock: _Clock,
    transport: object,
    projector: object,
    evidence: object,
    session: PriceOnlySessionEvidence | None = None,
    stop_requested=lambda: False,
    render=lambda _: None,
) -> PriceOnlyShadowCollector:
    return PriceOnlyShadowCollector(
        session_evidence=_session() if session is None else session,
        kalshi_transport=transport,
        market_projector=projector,
        evidence_store=evidence,
        wall_ns=clock.wall_ns,
        monotonic_ns=clock.monotonic_ns,
        pause=clock.pause,
        stop_requested=stop_requested,
        render=render,
    )


class PriceOnlyCollectorPublicApiTests(unittest.TestCase):
    def test_public_constructor_and_state_have_no_provider_shape(self) -> None:
        """Catches coupling the independent collector back to a score feed."""

        source = Path(inspect.getfile(PriceOnlyShadowCollector)).read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported_modules = tuple(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ) + tuple(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for module in imported_modules:
            lowered = module.casefold()
            for forbidden in ("provider", "trial", "sportradar"):
                self.assertNotIn(forbidden, lowered, module)
        parameters = inspect.signature(PriceOnlyShadowCollector).parameters
        self.assertEqual(
            tuple(parameters),
            (
                "session_evidence",
                "kalshi_transport",
                "market_projector",
                "evidence_store",
                "wall_ns",
                "monotonic_ns",
                "pause",
                "stop_requested",
                "render",
            ),
        )
        clock = _Clock()
        collector = _collector(
            clock=clock,
            transport=_Transport(clock, []),
            projector=_Projector(lambda _: _projection()),
            evidence=_Evidence(),
        )
        state_names = " ".join(getattr(collector, "__slots__", ())).casefold()
        for forbidden in (
            "provider",
            "score",
            "server",
            "winner",
            "signal",
            "pnl",
            "order",
            "portfolio",
            "strategy",
            "fee",
            "executor",
            "expert",
        ):
            self.assertNotIn(forbidden, state_names)

    def test_constructor_and_duration_validation_fail_closed(self) -> None:
        """Catches malformed identity, missing ports, and bool durations."""

        clock = _Clock()
        values = {
            "clock": clock,
            "transport": _Transport(clock, []),
            "projector": _Projector(lambda _: _projection()),
            "evidence": _Evidence(),
        }
        invalid_session = replace(
            _session(), market_tickers=(TICKERS[0], TICKERS[0])
        )
        with self.assertRaisesRegex(
            ShadowCollectorError,
            "shadow_price_only_collector_configuration_invalid",
        ):
            _collector(**values, session=invalid_session)
        with self.assertRaisesRegex(
            ShadowCollectorError,
            "shadow_price_only_collector_configuration_invalid",
        ):
            _collector(**(values | {"projector": object()}))

    def test_dashboard_is_literal_neutral_and_sanitizes_terminal_text(self) -> None:
        """Catches trading/score language or terminal control injection."""

        rendered = render_price_only_dashboard(
            PriceOnlyDashboardView(
                event_ticker=EVENT_TICKER,
                player_a_name="Player\nA",
                player_b_name="Player B",
                market_tickers=TICKERS,
                market_a_book="bid 0.31 x 12.00 | ask 0.34 x 8.00",
                market_b_book="--",
                kalshi_status="candidate",
                kalshi_generation=1,
                kalshi_sequence=2,
                kalshi_age_seconds=0.2,
                reason="bad\t\x00reason",
                kalshi_frames=2,
            )
        )
        self.assertIn(
            "READ ONLY / PRICE ONLY / NO SCORE FEED / NO SIGNALS / NO P&L / NO ORDERS",
            rendered,
        )
        for expected in (EVENT_TICKER, *TICKERS, "Player A", "Player B"):
            self.assertIn(expected, rendered)
        self.assertNotIn("\nA", rendered)
        self.assertNotIn("\t", rendered)
        self.assertNotIn("\x00", rendered)


class PriceOnlyShadowCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_timeouts_publish_read_only_heartbeat(self) -> None:
        """Catches a no-frame price-only run becoming visually silent."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            rendered: list[str] = []
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = _collector(
                    clock=clock,
                    transport=_Transport(clock, []),
                    projector=_Projector(lambda _: _projection()),
                    evidence=evidence,
                    render=rendered.append,
                )

                self.assertEqual(
                    await collector.run(duration_seconds=61),
                    "duration_elapsed",
                )

            ledger = next((base / "shadow").glob("session-*.jsonl"))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]

        rendered_heartbeats = [
            value
            for value in rendered
            if "kalshi_receive_timeout_heartbeat" in value
        ]
        self.assertEqual(len(rendered_heartbeats), 1)
        heartbeat = rendered_heartbeats[0]
        self.assertIn("ELAPSED", heartbeat)
        self.assertIn("KALSHI STATUS", heartbeat)
        self.assertIn("waiting", heartbeat)
        self.assertIn("FRAMES", heartbeat)
        self.assertIn("0", heartbeat)
        self.assertIn("NO SIGNALS / NO P&L / NO ORDERS", heartbeat)

        durable_heartbeats = [
            row
            for row in rows
            if row.get("reason") == "kalshi_receive_timeout_heartbeat"
        ]
        self.assertEqual(len(durable_heartbeats), 1)
        durable_heartbeat = durable_heartbeats[0]
        session = rows[0]
        self.assertEqual(session["kind"], "price_only_session")
        cadence_ns = (
            durable_heartbeat["observed_monotonic_ns"]
            - session["selected_monotonic_ns"]
        )
        self.assertGreaterEqual(cadence_ns, 60_000_000_000)
        self.assertLessEqual(cadence_ns, 61_000_000_000)
        terminal = rows[-1]
        self.assertEqual(terminal["kind"], "price_only_terminal")
        self.assertLess(rows.index(durable_heartbeat), rows.index(terminal))
        self.assertLess(
            durable_heartbeat["observed_monotonic_ns"],
            terminal["ended_monotonic_ns"],
        )

    async def test_session_and_raw_are_durable_before_downstream(self) -> None:
        """Catches socket or projection publication preceding durable evidence."""

        events: list[str] = []
        clock = _Clock()
        frame = _Frame(b'{"type":"opaque"}', clock)
        transport = _Transport(clock, [frame], events)
        evidence = _Evidence(events)
        projector = _Projector(lambda _: _projection(), events)
        collector = _collector(
            clock=clock,
            transport=transport,
            projector=projector,
            evidence=evidence,
            render=lambda _: events.append("render"),
        )

        result = await collector.run(duration_seconds=10)

        self.assertEqual(result, "duration_elapsed")
        self.assertLess(events.index("session"), events.index("open"))
        for later in ("project", "observation", "render"):
            self.assertLess(events.index("persist"), events.index(later))
        self.assertLess(events.index("observation"), events.index("render"))
        self.assertEqual(evidence.sessions, [_session()])
        self.assertEqual(evidence.terminals[-1]["reason"], "duration_elapsed")

    async def test_injected_ports_never_probe_provider_or_trial_state(self) -> None:
        """Catches hidden provider access through an otherwise neutral port."""

        forbidden_accesses: list[str] = []

        class TrapMixin:
            def __getattr__(self, name: str) -> object:
                if any(
                    token in name.casefold()
                    for token in ("provider", "trial", "sportradar")
                ):
                    forbidden_accesses.append(name)
                    raise AssertionError(f"forbidden dependency access: {name}")
                raise AttributeError(name)

        class TrapTransport(TrapMixin, _Transport):
            pass

        class TrapProjector(TrapMixin, _Projector):
            pass

        class TrapEvidence(TrapMixin, _Evidence):
            pass

        clock = _Clock()
        result = await _collector(
            clock=clock,
            transport=TrapTransport(clock, [_Frame(b"neutral", clock)]),
            projector=TrapProjector(lambda _: _projection()),
            evidence=TrapEvidence(),
        ).run(duration_seconds=10)

        self.assertEqual(result, "duration_elapsed")
        self.assertEqual(forbidden_accesses, [])

    async def test_real_projector_holds_aggregate_two_book_barrier(self) -> None:
        """Catches exposing one ticker before both complete books are ready."""

        from inci_tennis_runtime.live_shadow_cli import UnqualifiedKalshiProjector

        clock = _Clock()

        def frame(value: object) -> _Frame:
            return _Frame(
                json.dumps(value, separators=(",", ":")).encode(), clock
            )

        outcomes = [
            frame(
                {
                    "id": 1,
                    "type": "subscribed",
                    "msg": {"channel": "orderbook_delta", "sid": 27},
                }
            ),
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 1,
                    "msg": {
                        "market_ticker": TICKERS[0],
                        "market_id": "11111111-2222-3333-4444-555555555555",
                        "yes_dollars_fp": [["0.3100", "12.00"]],
                        "no_dollars_fp": [["0.3400", "8.00"]],
                    },
                }
            ),
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 2,
                    "msg": {
                        "market_ticker": TICKERS[1],
                        "market_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "yes_dollars_fp": [["0.6600", "6.00"]],
                        "no_dollars_fp": [["0.6900", "9.00"]],
                    },
                }
            ),
        ]
        evidence = _Evidence()
        collector = _collector(
            clock=clock,
            transport=_Transport(clock, outcomes),
            projector=UnqualifiedKalshiProjector(TICKERS),
            evidence=evidence,
        )

        await collector.run(duration_seconds=10)

        self.assertEqual(
            [row.kalshi_status for row in evidence.observations[:3]],
            ["incomplete", "incomplete", "candidate"],
        )
        for row in evidence.observations[:2]:
            self.assertIsNone(row.market_a.yes_bid)
            self.assertIsNone(row.market_b.yes_bid)
        self.assertEqual(evidence.observations[2].market_a.yes_bid, "0.3100")
        self.assertEqual(evidence.observations[2].market_b.yes_bid, "0.6600")

    async def test_real_projector_clears_empty_or_one_sided_snapshot(self) -> None:
        """Catches publishing partial executable depth as a candidate."""

        from inci_tennis_runtime.live_shadow_cli import UnqualifiedKalshiProjector

        clock = _Clock()

        def frame(value: object) -> _Frame:
            return _Frame(json.dumps(value, separators=(",", ":")).encode(), clock)

        outcomes = [
            frame(
                {
                    "id": 1,
                    "type": "subscribed",
                    "msg": {"channel": "orderbook_delta", "sid": 27},
                }
            ),
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 1,
                    "msg": {
                        "market_ticker": TICKERS[0],
                        "market_id": "11111111-2222-3333-4444-555555555555",
                    },
                }
            ),
            frame(
                {
                    "type": "orderbook_snapshot",
                    "sid": 27,
                    "seq": 2,
                    "msg": {
                        "market_ticker": TICKERS[1],
                        "market_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "yes_dollars_fp": [["0.6600", "6.00"]],
                    },
                }
            ),
        ]
        evidence = _Evidence()
        await _collector(
            clock=clock,
            transport=_Transport(clock, outcomes),
            projector=UnqualifiedKalshiProjector(TICKERS),
            evidence=evidence,
        ).run(duration_seconds=10)

        self.assertNotIn(
            "candidate", [row.kalshi_status for row in evidence.observations]
        )
        self.assertTrue(
            all(
                row.market_a.yes_bid is None and row.market_b.yes_bid is None
                for row in evidence.observations
            )
        )

    async def test_gap_clears_both_books_and_requests_correlated_snapshot(self) -> None:
        """Catches carrying candidate prices across a sequence gap."""

        clock = _Clock()
        frames = [_Frame(b"one", clock), _Frame(b"two", clock)]
        projections = iter(
            (
                _projection(),
                _projection(
                    status="gap",
                    reason="kalshi_sequence_gap",
                    sequence=4,
                    snapshot_needed=True,
                ),
            )
        )
        evidence = _Evidence()
        transport = _Transport(clock, frames)
        projector = _Projector(lambda _: next(projections))

        await _collector(
            clock=clock,
            transport=transport,
            projector=projector,
            evidence=evidence,
        ).run(duration_seconds=10)

        gap = next(
            row
            for row in evidence.observations
            if row.reason == "kalshi_sequence_gap"
        )
        self.assertEqual(gap.kalshi_status, "gap")
        self.assertIsNone(gap.market_a.yes_bid)
        self.assertIsNone(gap.market_b.yes_bid)
        self.assertEqual(transport.snapshot_requests, [27])
        self.assertEqual(len(projector.snapshots), 1)
        self.assertEqual(projector.snapshots[0].request_id, 101)
        self.assertEqual(projector.snapshots[0].command, "get_snapshot")

    async def test_snapshot_failure_observes_gap_then_recovers_transport(self) -> None:
        """Catches snapshot I/O orphaning an already durable raw frame."""

        events: list[str] = []
        clock = _Clock()
        evidence = _Evidence(events)
        transport = _Transport(
            clock,
            [_Frame(b"gap", clock)],
            events,
            snapshot_failures={1},
        )
        projector = _Projector(
            lambda _: _projection(
                status="gap",
                reason="kalshi_sequence_gap",
                sequence=4,
                sid=73,
                snapshot_needed=True,
            ),
            events,
        )

        result = await _collector(
            clock=clock,
            transport=transport,
            projector=projector,
            evidence=evidence,
            render=lambda _: events.append("render"),
        ).run(duration_seconds=10)

        self.assertEqual(result, "duration_elapsed")
        self.assertEqual(len(evidence.frames), 1)
        self.assertLess(events.index("persist"), events.index("project"))
        self.assertLess(events.index("project"), events.index("observation"))
        self.assertLess(events.index("observation"), events.index("render"))
        self.assertLess(events.index("render"), events.index("snapshot"))
        self.assertEqual(transport.snapshot_requests, [73])
        self.assertEqual(
            [row.reason for row in evidence.observations[:3]],
            [
                "kalshi_sequence_gap",
                "kalshi_stream_disconnected",
                "kalshi_reconnected",
            ],
        )
        gap = evidence.observations[0]
        self.assertEqual(gap.kalshi_status, "gap")
        self.assertIsNone(gap.market_a.yes_bid)
        self.assertIsNone(gap.market_b.yes_bid)
        self.assertEqual(projector.disconnects, [1])
        self.assertEqual((transport.open_calls, transport.subscribe_calls), (2, 2))
        self.assertEqual(clock.pauses, [1.0])

    async def test_duplicate_out_of_order_and_ignored_clear_both_books(self) -> None:
        """Catches any noncandidate projection leaking stale prices."""

        cases = (
            ("duplicate", "kalshi_sequence_duplicate", True),
            ("out_of_order", "kalshi_sequence_out_of_order", True),
            ("ignored", "candidate_message_ignored", False),
        )
        for status, reason, snapshot_needed in cases:
            with self.subTest(status=status):
                clock = _Clock()
                evidence = _Evidence()
                transport = _Transport(clock, [_Frame(status.encode(), clock)])
                await _collector(
                    clock=clock,
                    transport=transport,
                    projector=_Projector(
                        lambda _, s=status, r=reason, n=snapshot_needed: _projection(
                            status=s,
                            reason=r,
                            snapshot_needed=n,
                        )
                    ),
                    evidence=evidence,
                ).run(duration_seconds=10)
                row = next(
                    item
                    for item in evidence.observations
                    if item.reason == reason
                )
                self.assertIsNone(row.market_a.yes_bid)
                self.assertIsNone(row.market_b.yes_bid)
                self.assertEqual(
                    transport.snapshot_requests,
                    [27] if snapshot_needed else [],
                )

    async def test_parser_error_uses_durable_generation_then_reconnects(self) -> None:
        """Catches fabricated generation or retained books after malformed raw."""

        clock = _Clock()
        frame = _Frame(b"malformed", clock, generation=7)
        evidence = _Evidence()
        transport = _Transport(clock, [frame])
        projector = _Projector(
            lambda _: (_ for _ in ()).throw(
                _CodedError("kalshi_ws_contract_invalid")
            )
        )

        await _collector(
            clock=clock,
            transport=transport,
            projector=projector,
            evidence=evidence,
        ).run(duration_seconds=10)

        parser = next(
            row
            for row in evidence.observations
            if row.reason == "kalshi_parser_error"
        )
        self.assertEqual(parser.kalshi_generation, 7)
        self.assertEqual(parser.kalshi_sequence, 0)
        self.assertIsNone(parser.market_a.yes_bid)
        self.assertEqual(projector.disconnects, [7])
        self.assertEqual((transport.open_calls, transport.subscribe_calls), (2, 2))
        self.assertEqual(clock.pauses, [1.0])

    async def test_disconnect_and_reconnect_clear_generation_and_books(self) -> None:
        """Catches displaying stale generation or prices across a socket epoch."""

        clock = _Clock()
        evidence = _Evidence()
        rendered: list[str] = []
        transport = _Transport(
            clock,
            [
                _Frame(b"candidate", clock),
                _CodedError("kalshi_ws_disconnected"),
            ],
        )
        projector = _Projector(lambda _: _projection())

        await _collector(
            clock=clock,
            transport=transport,
            projector=projector,
            evidence=evidence,
            render=rendered.append,
        ).run(duration_seconds=10)

        disconnected = next(
            row
            for row in evidence.observations
            if row.reason == "kalshi_stream_disconnected"
        )
        reconnected = next(
            row
            for row in evidence.observations
            if row.reason == "kalshi_reconnected"
        )
        for row in (disconnected, reconnected):
            self.assertIsNone(row.market_a.yes_bid)
            self.assertIsNone(row.market_b.yes_bid)
        self.assertEqual(projector.disconnects, [1])
        self.assertEqual(clock.pauses, [1.0])
        self.assertTrue(any("-- / --" in value for value in rendered[-2:]))

    async def test_recovery_uses_only_one_two_four_then_halts(self) -> None:
        """Catches unbounded reconnects or an unreviewed retry schedule."""

        clock = _Clock()
        evidence = _Evidence()
        transport = _Transport(
            clock,
            [_CodedError("kalshi_ws_disconnected")],
            open_failures={2, 3, 4},
        )

        with self.assertRaisesRegex(
            ShadowCollectorError, "kalshi_recovery_exhausted"
        ):
            await _collector(
                clock=clock,
                transport=transport,
                projector=_Projector(lambda _: _projection()),
                evidence=evidence,
            ).run(duration_seconds=10)

        self.assertEqual(clock.pauses, [1.0, 2.0, 4.0])
        self.assertEqual(transport.open_calls, 4)
        self.assertEqual(evidence.terminals[-1]["reason"], "halted")
        self.assertEqual(
            evidence.terminals[-1]["code"], "kalshi_recovery_exhausted"
        )

    async def test_timeout_duration_interrupt_and_terminal_stream(self) -> None:
        """Catches timeout-as-failure or reconnecting a terminal stream."""

        clock = _Clock()
        evidence = _Evidence()
        transport = _Transport(clock, [])
        result = await _collector(
            clock=clock,
            transport=transport,
            projector=_Projector(lambda _: _projection()),
            evidence=evidence,
        ).run(duration_seconds=10)
        self.assertEqual(result, "duration_elapsed")
        self.assertEqual(evidence.terminals[-1]["reason"], "duration_elapsed")

        clock = _Clock()
        evidence = _Evidence()
        result = await _collector(
            clock=clock,
            transport=_Transport(clock, []),
            projector=_Projector(lambda _: _projection()),
            evidence=evidence,
            stop_requested=lambda: True,
        ).run(duration_seconds=10)
        self.assertEqual(result, "operator_interrupt")
        self.assertEqual(evidence.terminals[-1]["reason"], "operator_interrupt")

        clock = _Clock()
        evidence = _Evidence()
        transport = _Transport(
            clock, [_CodedError("kalshi_stream_terminal")]
        )
        with self.assertRaisesRegex(
            ShadowCollectorError, "kalshi_stream_terminal"
        ):
            await _collector(
                clock=clock,
                transport=transport,
                projector=_Projector(lambda _: _projection()),
                evidence=evidence,
            ).run(duration_seconds=10)
        self.assertEqual(transport.open_calls, 1)
        self.assertEqual(clock.pauses, [])

    async def test_generation_mismatch_with_durable_raw_reference_halts(self) -> None:
        """Catches recording projector coordinates from another connection."""

        clock = _Clock()
        evidence = _Evidence()
        with self.assertRaisesRegex(
            ShadowCollectorError, "shadow_projection_generation_mismatch"
        ):
            await _collector(
                clock=clock,
                transport=_Transport(
                    clock, [_Frame(b"wrong-generation", clock, generation=7)]
                ),
                projector=_Projector(lambda _: _projection(generation=8)),
                evidence=evidence,
            ).run(duration_seconds=10)
        self.assertEqual(len(evidence.frames), 1)
        self.assertEqual(evidence.observations, [])
        self.assertEqual(evidence.terminals[-1]["kalshi_frames"], 1)

    async def test_raw_failure_and_cleanup_failures_preserve_primary_code(self) -> None:
        """Catches close/terminal cleanup masking raw evidence poisoning."""

        class PoisonedEvidence(_Evidence):
            def persist_kalshi_frame(self, frame: object) -> object:
                del frame
                raise _CodedError("shadow_evidence_raw_write_failed")

            def append_price_only_terminal(self, **values: object) -> None:
                del values
                raise _CodedError("shadow_evidence_closed")

        clock = _Clock()
        projected: list[object] = []
        transport = _Transport(
            clock,
            [_Frame(b"raw", clock)],
            close_failures={1},
        )
        with self.assertRaisesRegex(
            ShadowCollectorError, "shadow_evidence_raw_write_failed"
        ):
            await _collector(
                clock=clock,
                transport=transport,
                projector=_Projector(lambda value: projected.append(value)),
                evidence=PoisonedEvidence(),
            ).run(duration_seconds=10)
        self.assertEqual(projected, [])

    async def test_close_failure_converts_normal_result_to_sanitized_halt(self) -> None:
        """Catches returning success when transport close did not complete."""

        clock = _Clock()
        evidence = _Evidence()
        with self.assertRaisesRegex(
            ShadowCollectorError, "kalshi_ws_close_failed"
        ):
            await _collector(
                clock=clock,
                transport=_Transport(clock, [], close_failures={1}),
                projector=_Projector(lambda _: _projection()),
                evidence=evidence,
            ).run(duration_seconds=10)
        self.assertEqual(evidence.terminals[-1]["reason"], "halted")
        self.assertEqual(evidence.terminals[-1]["code"], "kalshi_ws_close_failed")

    async def _cancel_at_durability(
        self, operation: str
    ) -> tuple[object, object, object]:
        clock = _Clock()
        evidence = _BlockingEvidence(operation)
        transport = _Transport(clock, [_Frame(b"raw", clock)])
        projector = _Projector(lambda _: _projection())
        task = asyncio.create_task(
            _collector(
                clock=clock,
                transport=transport,
                projector=projector,
                evidence=evidence,
            ).run(duration_seconds=10)
        )
        started = await asyncio.to_thread(evidence.started.wait, 1)
        self.assertTrue(started)
        task.cancel()
        evidence.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        return evidence, transport, projector

    async def test_session_cancellation_finishes_row_without_socket(self) -> None:
        """Catches an orphan session row or opening before session durability."""

        evidence, transport, _ = await self._cancel_at_durability("session")
        self.assertEqual(len(evidence.sessions), 1)
        self.assertEqual(evidence.terminals[-1]["reason"], "cancelled")
        self.assertEqual(transport.open_calls, 0)

    async def test_raw_cancellation_counts_without_projection(self) -> None:
        """Catches parsing a frame after cancellation or undercounting its receipt."""

        evidence, _, projector = await self._cancel_at_durability("raw")
        self.assertEqual(len(evidence.frames), 1)
        self.assertEqual(evidence.observations, [])
        self.assertEqual(projector.events, [])
        self.assertEqual(evidence.terminals[-1]["kalshi_frames"], 1)

    async def test_observation_cancellation_finishes_before_terminal(self) -> None:
        """Catches terminal publication overtaking a durable observation."""

        evidence, _, projector = await self._cancel_at_durability("observation")
        self.assertEqual(len(projector.events), 1)
        self.assertEqual(len(evidence.observations), 1)
        self.assertLess(
            evidence.events.index("observation"),
            evidence.events.index("terminal"),
        )
        self.assertEqual(evidence.terminals[-1]["reason"], "cancelled")

    async def test_terminal_cancellation_finishes_then_reraises(self) -> None:
        """Catches swallowing cancellation or abandoning terminal durability."""

        evidence, transport, _ = await self._cancel_at_durability("terminal")
        self.assertEqual(len(evidence.terminals), 1)
        self.assertEqual(evidence.terminals[0]["reason"], "duration_elapsed")
        self.assertEqual(transport.close_calls, 1)

    async def test_run_rejects_invalid_duration_without_writing_or_socket(self) -> None:
        """Catches side effects before run-argument validation."""

        for duration in (True, 9, 3_601):
            with self.subTest(duration=duration):
                clock = _Clock()
                evidence = _Evidence()
                transport = _Transport(clock, [])
                with self.assertRaisesRegex(
                    ShadowCollectorError, "shadow_duration_invalid"
                ):
                    await _collector(
                        clock=clock,
                        transport=transport,
                        projector=_Projector(lambda _: _projection()),
                        evidence=evidence,
                    ).run(duration_seconds=duration)
                self.assertEqual(evidence.sessions, [])
                self.assertEqual(transport.open_calls, 0)


if __name__ == "__main__":
    unittest.main()
