from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest

from inci_tennis_io.sportradar_trial_transport import (
    TrialAttemptReservation,
    TrialCapture,
)


MATCH_ID = "sr:sport_event:123456"
TICKERS = ("KXTENNIS-MATCH-HOME", "KXTENNIS-MATCH-AWAY")
WALL_NS = 1_785_607_205_000_000_000


def _summary_payload() -> bytes:
    return json.dumps(
        {
            "generated_at": "2026-08-01T18:00:02+00:00",
            "sport_event": {
                "id": MATCH_ID,
                "start_time": "2026-08-01T18:00:00+00:00",
                "start_time_confirmed": True,
                "competitors": [
                    {
                        "id": "sr:competitor:101",
                        "name": "Player Home",
                        "qualifier": "home",
                    },
                    {
                        "id": "sr:competitor:202",
                        "name": "Player Away",
                        "qualifier": "away",
                    },
                ],
                "sport_event_context": {"mode": {"best_of": 3}},
            },
            "sport_event_status": {
                "status": "live",
                "match_status": "1st_set",
                "home_score": 0,
                "away_score": 0,
                "period_scores": [
                    {
                        "number": 1,
                        "type": "set",
                        "home_score": 3,
                        "away_score": 2,
                    }
                ],
                "game_state": {
                    "home_score": 30,
                    "away_score": 15,
                    "serving": "home",
                    "tie_break": False,
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


class _Clock:
    def __init__(self) -> None:
        self.monotonic = 1_000_000_000

    def monotonic_ns(self) -> int:
        return self.monotonic

    def wall_ns(self) -> int:
        return WALL_NS + self.monotonic

    def advance(self, seconds: float) -> None:
        self.monotonic += int(seconds * 1_000_000_000)

    async def pause(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)


class _CodedError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Frame:
    def __init__(self, payload: bytes, clock: _Clock, *, generation: int = 1) -> None:
        from hashlib import sha256

        self.payload = payload
        self.captured_wall_ns = clock.wall_ns()
        self.captured_monotonic_ns = clock.monotonic_ns()
        self.clock_uncertainty_ns = 7
        self.physical_connection_generation = generation
        self.raw_sha256 = sha256(payload).hexdigest()


class _KalshiTransport:
    def __init__(self, clock: _Clock, outcomes: list[object]) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.open_calls = 0
        self.subscribe_calls = 0
        self.close_calls = 0
        self.snapshot_requests: list[int] = []

    async def open_readonly(self) -> None:
        self.open_calls += 1

    async def subscribe(self) -> object:
        self.subscribe_calls += 1
        return SimpleNamespace(
            request_id=self.subscribe_calls,
            physical_connection_generation=self.open_calls,
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
        self.snapshot_requests.append(sid)
        return SimpleNamespace(
            request_id=100 + len(self.snapshot_requests),
            physical_connection_generation=self.open_calls,
        )

    async def close(self) -> None:
        self.close_calls += 1


class _SportradarTransport:
    def __init__(self, capture: TrialCapture) -> None:
        self.capture = capture
        self.summary_calls = 0
        self.timeline_calls = 0

    async def fetch_summary(self, match_id: str) -> TrialCapture:
        if match_id != MATCH_ID:
            raise AssertionError("wrong match")
        self.summary_calls += 1
        return self.capture

    async def fetch_timeline(self, match_id: str) -> TrialCapture:
        del match_id
        self.timeline_calls += 1
        raise _CodedError("sportradar_test_timeline_unavailable")


class _SportradarLedger:
    def __init__(self) -> None:
        self.observations: list[object] = []
        self.failures: list[tuple[str, object, str]] = []
        self.terminals: list[dict[str, object]] = []

    @property
    def session_attempts(self) -> int:
        return 1

    @property
    def remaining_session_attempts(self) -> int:
        return 399

    @property
    def remaining_access_attempts(self) -> int:
        return 999

    def record_observation(self, record: object) -> None:
        self.observations.append(record)

    def record_parser_failure(
        self, *, command: str, reservation: object, code: str
    ) -> None:
        self.failures.append((command, reservation, code))

    def record_session_terminal(self, **values: object) -> None:
        self.terminals.append(values)


class _Projector:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.subscriptions: list[object] = []
        self.snapshots: list[object] = []
        self.disconnects: list[int | None] = []

    def begin_subscription(self, receipt: object) -> None:
        self.subscriptions.append(receipt)

    def apply(self, frame: object) -> object:
        return self.callback(frame)

    def snapshot_requested(self, receipt: object) -> None:
        self.snapshots.append(receipt)

    def disconnect(self, generation: int | None) -> None:
        self.disconnects.append(generation)


def _capture(root: Path) -> TrialCapture:
    payload = _summary_payload()
    raw_path = root / "0001_summary.json"
    raw_path.write_bytes(payload)
    raw_path.chmod(0o600)
    return TrialCapture(
        reservation=TrialAttemptReservation(
            session_id="11111111-2222-3333-4444-555555555555",
            session_attempt=1,
            access_attempt=1,
            route="summary",
            started_wall_ns=WALL_NS,
        ),
        captured_wall_ns=WALL_NS,
        raw_path=raw_path,
        payload=payload,
    )


def _candidate_projection(*, sequence: int = 2, reason: str = "candidate_snapshot_applied"):
    from inci_tennis_runtime.live_shadow_collector import (
        CandidateMarketProjection,
        CandidateMarketView,
    )

    return CandidateMarketProjection(
        markets=(
            CandidateMarketView(
                TICKERS[0], "0.31", "0.34", "12.00", "8.00"
            ),
            CandidateMarketView(
                TICKERS[1], "0.66", "0.69", "6.00", "9.00"
            ),
        ),
        generation=1,
        sequence=sequence,
        subscription_id=27,
        status="candidate",
        reason=reason,
        snapshot_needed=False,
    )


def _blocked_projection(
    *,
    status: str,
    reason: str,
    sequence: int,
    snapshot_needed: bool,
):
    from inci_tennis_runtime.live_shadow_collector import (
        CandidateMarketProjection,
        CandidateMarketView,
    )

    return CandidateMarketProjection(
        markets=(
            CandidateMarketView(TICKERS[0], None, None, None, None),
            CandidateMarketView(TICKERS[1], None, None, None, None),
        ),
        generation=1,
        sequence=sequence,
        subscription_id=27,
        status=status,
        reason=reason,
        snapshot_needed=snapshot_needed,
    )


def _evidence_observation(source: TrialCapture, reference: object, clock: _Clock):
    from hashlib import sha256

    from inci_tennis_io.shadow_evidence import (
        ShadowEvidenceObservation,
        ShadowMarketCandidate,
    )

    return ShadowEvidenceObservation(
        observed_wall_ns=clock.wall_ns(),
        observed_monotonic_ns=clock.monotonic_ns(),
        clock_uncertainty_ns=7,
        provider_match_id=MATCH_ID,
        market_tickers=TICKERS,
        provider_generated_wall_ns=WALL_NS,
        provider_captured_wall_ns=source.captured_wall_ns,
        provider_request_started_wall_ns=WALL_NS,
        provider_request_started_monotonic_ns=clock.monotonic_ns(),
        provider_request_completed_wall_ns=WALL_NS,
        provider_request_completed_monotonic_ns=clock.monotonic_ns(),
        provider_clock_uncertainty_ns=0,
        provider_raw_path=str(source.raw_path),
        provider_raw_sha256=sha256(source.payload).hexdigest(),
        home_player_name="Player Home",
        away_player_name="Player Away",
        match_status="1st_set",
        sets=(0, 0),
        games=(3, 2),
        points=("30", "15"),
        server="home",
        sportradar_age_ns=3_000_000_000,
        progression="initial",
        last_event_id=None,
        last_event_type=None,
        last_event_result=None,
        kalshi_raw_path=reference.raw_path,
        kalshi_raw_sha256=reference.raw_sha256,
        kalshi_captured_wall_ns=reference.captured_wall_ns,
        kalshi_captured_monotonic_ns=reference.captured_monotonic_ns,
        kalshi_generation=1,
        kalshi_sequence=2,
        kalshi_age_ns=0,
        kalshi_status="candidate",
        home_market=ShadowMarketCandidate(
            TICKERS[0], "0.31", "0.34", "12.00", "8.00"
        ),
        away_market=ShadowMarketCandidate(
            TICKERS[1], "0.66", "0.69", "6.00", "9.00"
        ),
        reason="candidate_snapshot_applied",
        sportradar_captures=1,
        kalshi_frames=1,
    )


class ShadowEvidenceStoreTests(unittest.TestCase):
    def test_store_accepts_ready_aggregate_candidate_reason(self) -> None:
        """Catches rejecting the first fully synchronized two-book view."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = _capture(base)
            clock = _Clock()
            frame = _Frame(b'{"type":"opaque"}', clock)
            with ShadowEvidenceStore(base / "shadow") as store:
                reference = store.persist_kalshi_frame(frame)
                store.append_observation(
                    replace(
                        _evidence_observation(source, reference, clock),
                        reason="candidate_book_ready",
                    )
                )
                store.append_terminal(
                    reason="duration_elapsed",
                    code=None,
                    ended_wall_ns=clock.wall_ns(),
                    ended_monotonic_ns=clock.monotonic_ns(),
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_captures=1,
                    kalshi_frames=1,
                )

    def test_store_is_private_single_owner_append_fsync_and_reference_only(self) -> None:
        """Catches permissive files, duplicate raw payloads, or a second writer."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            source = _capture(Path(directory))
            clock = _Clock()
            frame = _Frame(b'{"type":"opaque"}', clock)
            with ShadowEvidenceStore(root) as store:
                reference = store.persist_kalshi_frame(frame)
                store.append_observation(
                    _evidence_observation(source, reference, clock)
                )
                with self.assertRaisesRegex(
                    ShadowEvidenceError, "shadow_evidence_locked"
                ):
                    ShadowEvidenceStore(root)

            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((root / "raw").stat().st_mode), 0o700
            )
            self.assertEqual(
                stat.S_IMODE(Path(reference.raw_path).stat().st_mode), 0o600
            )
            self.assertEqual(Path(reference.raw_path).suffix, ".bin")
            ledger = next(root.glob("session-*.jsonl"))
            self.assertEqual(stat.S_IMODE(ledger.stat().st_mode), 0o600)
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(rows[0]["kind"], "kalshi_capture")
            self.assertEqual(rows[0]["raw_path"], reference.raw_path)
            observation = next(row for row in rows if row["kind"] == "observation")
            self.assertEqual(observation["trust"], "unqualified_shadow")
            self.assertEqual(observation["kalshi_raw_path"], reference.raw_path)
            self.assertNotIn("kalshi_payload", observation)
            self.assertNotIn(source.payload.decode("utf-8"), ledger.read_text())

    def test_unclean_prior_session_fails_loudly(self) -> None:
        """Catches silently accepting a raw/ledger session without a terminal."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shadow"
            clock = _Clock()
            store = ShadowEvidenceStore(root)
            store.persist_kalshi_frame(_Frame(b'{"opaque":true}', clock))
            store.close()

            with self.assertRaisesRegex(
                ShadowEvidenceError, "shadow_evidence_unclean_session"
            ):
                ShadowEvidenceStore(root)

    def test_store_rejects_symlink_state_root(self) -> None:
        """Catches following an attacker-controlled state-root symlink."""

        from inci_tennis_io.shadow_evidence import (
            ShadowEvidenceError,
            ShadowEvidenceStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir(mode=0o700)
            link = base / "shadow"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                ShadowEvidenceError, "shadow_evidence_state_unsafe"
            ):
                ShadowEvidenceStore(link)


class LiveShadowCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_capture_observer_runs_only_after_durable_commits(self) -> None:
        """Catches paper projection observing raw inputs before shadow durability."""
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            capture = _capture(Path(directory))
            ledger = _SportradarLedger()
            committed = {"kalshi": False}
            observed: list[tuple[str, object, object]] = []

            class Evidence:
                def persist_kalshi_frame(self, frame: object) -> object:
                    committed["kalshi"] = True
                    return SimpleNamespace(
                        raw_sha256=frame.raw_sha256,
                        physical_connection_generation=frame.physical_connection_generation,
                        captured_wall_ns=frame.captured_wall_ns,
                        captured_monotonic_ns=frame.captured_monotonic_ns,
                        clock_uncertainty_ns=frame.clock_uncertainty_ns,
                        raw_path="/tmp/raw-kalshi-frame",
                    )

                def append_observation(self, record: object) -> None:
                    del record

                def append_terminal(self, **values: object) -> None:
                    del values

            class Observer:
                async def after_provider_commit(self, *, capture: object, durable_receipt: object, captured_wall_ns: int, captured_monotonic_ns: int, clock_uncertainty_ns: int) -> None:
                    self.assertion = bool(ledger.observations)
                    observed.append(("provider", capture, durable_receipt))

                async def after_kalshi_commit(self, *, frame: object, durable_receipt: object, captured_wall_ns: int, captured_monotonic_ns: int, clock_uncertainty_ns: int) -> None:
                    self.assertion = committed["kalshi"]
                    observed.append(("kalshi", frame, durable_receipt))

                async def after_heartbeat_commit(self, *, captured_wall_ns: int, captured_monotonic_ns: int) -> None:
                    observed.append(("heartbeat", captured_wall_ns, captured_monotonic_ns))

            observer = Observer()
            frame = _Frame(b"raw-kalshi-frame", clock)
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(capture),
                sportradar_ledger=ledger,
                kalshi_transport=_KalshiTransport(clock, [frame]),
                market_projector=_Projector(lambda _: _candidate_projection()),
                evidence_store=Evidence(),
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
                capture_observer=observer,
            )

            await collector._capture_summary()
            await collector._receive(1.0)
            clock.advance(60)
            collector._next_heartbeat_monotonic_ns = clock.monotonic_ns()
            await collector._emit_timeout_heartbeat_if_due()

        self.assertTrue(observer.assertion)
        self.assertEqual(
            tuple(kind for kind, _, _ in observed),
            ("provider", "kalshi", "heartbeat"),
        )
        self.assertIs(observed[0][1], capture)
        self.assertIs(observed[1][1], frame)

    async def test_capture_observer_failure_halts_without_relabelling_prior_evidence(self) -> None:
        """Catches observer failure being swallowed or rewriting committed shadow rows."""
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            capture = _capture(Path(directory))
            ledger = _SportradarLedger()

            class Observer:
                async def after_provider_commit(self, **values: object) -> None:
                    del values
                    raise RuntimeError("paper_bridge_halted")

                async def after_kalshi_commit(self, **values: object) -> None:
                    del values

            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(capture),
                sportradar_ledger=ledger,
                kalshi_transport=_KalshiTransport(clock, []),
                market_projector=_Projector(lambda _: None),
                evidence_store=SimpleNamespace(),
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
                capture_observer=Observer(),
            )

            with self.assertRaisesRegex(RuntimeError, "paper_bridge_halted"):
                await collector._capture_summary()

        self.assertEqual(len(ledger.observations), 1)

    async def test_provider_wait_with_receive_timeouts_still_publishes_heartbeat(
        self,
    ) -> None:
        """Catches an unresponsive provider hiding a live shadow session."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class BlockedProvider(_SportradarTransport):
            async def fetch_summary(self, match_id: str) -> TrialCapture:
                del match_id
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            rendered: list[str] = []
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=BlockedProvider(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=_KalshiTransport(clock, []),
                    market_projector=_Projector(lambda _: None),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: clock.monotonic_ns()
                    >= 61_000_000_000,
                    render=rendered.append,
                )

                self.assertEqual(
                    await collector.run(duration_seconds=61, poll_seconds=61),
                    "operator_interrupt",
                )

            ledger = next((base / "shadow").glob("session-*.jsonl"))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]

        heartbeats = [
            value
            for value in rendered
            if "kalshi_receive_timeout_heartbeat" in value
        ]
        self.assertEqual(len(heartbeats), 1)
        heartbeat = heartbeats[0]
        self.assertIn("ELAPSED", heartbeat)
        self.assertIn("KALSHI STATUS", heartbeat)
        self.assertIn("waiting", heartbeat)
        self.assertIn("CAPTURES", heartbeat)
        self.assertIn("Sportradar 0 | Kalshi 0", heartbeat)
        self.assertIn("NO SIGNALS", heartbeat)
        self.assertIn("NO ORDERS", heartbeat)
        for label in ("PLAYERS", "SCORE", "SERVER"):
            self.assertRegex(heartbeat, rf"\| {label}\s+\| --\s+\|")
        self.assertFalse(
            any(row.get("kind") == "observation" for row in rows)
        )
        self.assertEqual(rows[-1]["kind"], "terminal")

    async def test_receive_timeouts_publish_read_only_heartbeat(self) -> None:
        """Catches a no-frame shadow run becoming visually silent."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            rendered: list[str] = []
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=_SportradarTransport(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=_KalshiTransport(clock, []),
                    market_projector=_Projector(lambda _: None),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=rendered.append,
                )

                self.assertEqual(
                    await collector.run(duration_seconds=61, poll_seconds=61),
                    "duration_elapsed",
                )

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
            self.assertIn("CAPTURES", heartbeat)
            self.assertIn("Kalshi 0", heartbeat)
            self.assertIn("NO SIGNALS", heartbeat)
            self.assertIn("NO ORDERS", heartbeat)

            ledger = next((base / "shadow").glob("session-*.jsonl"))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            durable_heartbeats = [
                row
                for row in rows
                if row.get("reason") == "kalshi_receive_timeout_heartbeat"
            ]
            self.assertEqual(len(durable_heartbeats), 1)
            durable_heartbeat = durable_heartbeats[0]
            provider_summary = next(
                row
                for row in rows
                if row.get("reason") == "provider_summary_captured"
            )
            cadence_ns = (
                durable_heartbeat["observed_monotonic_ns"]
                - provider_summary["observed_monotonic_ns"]
            )
            self.assertGreaterEqual(cadence_ns, 59_000_000_000)
            self.assertLessEqual(cadence_ns, 61_000_000_000)
            terminal = rows[-1]
            self.assertEqual(terminal["kind"], "terminal")
            self.assertLess(rows.index(durable_heartbeat), rows.index(terminal))
            self.assertLess(
                durable_heartbeat["observed_monotonic_ns"],
                terminal["ended_monotonic_ns"],
            )

    async def test_shielded_cleanup_failure_has_no_orphaned_future(self) -> None:
        """Catches cancellation leaving a later cleanup exception unobserved."""

        from inci_tennis_runtime.live_shadow_collector import (
            _shielded_task_result,
        )

        started = asyncio.Event()
        release = asyncio.Event()
        exception_contexts: list[dict[str, object]] = []
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        loop.set_exception_handler(
            lambda _loop, context: exception_contexts.append(context)
        )

        async def failing_cleanup() -> object:
            started.set()
            await release.wait()
            raise OSError("injected cleanup failure")

        cleanup = asyncio.create_task(failing_cleanup())
        waiter = asyncio.create_task(_shielded_task_result(cleanup))
        await started.wait()
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()
        result, cancellation, error = await waiter
        await asyncio.sleep(0)

        self.assertIsNone(result)
        self.assertIsInstance(cancellation, asyncio.CancelledError)
        self.assertIsInstance(error, OSError)
        self.assertEqual(exception_contexts, [])

    async def test_slow_dashboard_writer_does_not_block_event_loop(self) -> None:
        """Catches terminal flush latency freezing unrelated async work."""

        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class Evidence:
            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                del record

            def append_terminal(self, **values: object) -> None:
                del values

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            render_started = threading.Event()
            render_release = threading.Event()
            loop_progressed = threading.Event()
            probe_result: list[bool] = []
            loop = asyncio.get_running_loop()

            def render(_: str) -> None:
                render_started.set()
                if not render_release.wait(timeout=2):
                    raise AssertionError("test did not release dashboard")

            def probe() -> None:
                if not render_started.wait(timeout=1):
                    probe_result.append(False)
                    render_release.set()
                    return
                loop.call_soon_threadsafe(loop_progressed.set)
                probe_result.append(loop_progressed.wait(timeout=0.2))
                render_release.set()

            thread = threading.Thread(target=probe)
            thread.start()
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(
                    _capture(Path(directory))
                ),
                sportradar_ledger=_SportradarLedger(),
                kalshi_transport=_KalshiTransport(clock, []),
                market_projector=_Projector(lambda _: None),
                evidence_store=Evidence(),
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=render,
            )

            await collector.run(duration_seconds=10, poll_seconds=10)
            thread.join(timeout=1)

        self.assertEqual(probe_result, [True])

    async def test_provider_capture_completed_during_cancel_is_counted(self) -> None:
        """Catches a durable provider capture being hidden by cancellation."""

        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class PersistedProvider:
            def __init__(self) -> None:
                self.completed_captures = 0
                self.started = asyncio.Event()

            async def fetch_summary(self, match_id: str) -> TrialCapture:
                del match_id
                self.completed_captures = 1
                self.started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            async def fetch_timeline(self, match_id: str) -> TrialCapture:
                raise AssertionError(f"unexpected timeline: {match_id}")

        class RecordingEvidence:
            def __init__(self) -> None:
                self.terminals: list[dict[str, object]] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                raise AssertionError(f"unexpected observation: {record!r}")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        clock = _Clock()
        provider = PersistedProvider()
        evidence = RecordingEvidence()
        collector = LiveShadowCollector(
            provider_match_id=MATCH_ID,
            market_tickers=TICKERS,
            sportradar_transport=provider,
            sportradar_ledger=_SportradarLedger(),
            kalshi_transport=_KalshiTransport(clock, []),
            market_projector=_Projector(lambda _: None),
            evidence_store=evidence,
            wall_ns=clock.wall_ns,
            monotonic_ns=clock.monotonic_ns,
            pause=clock.pause,
            stop_requested=lambda: False,
            render=lambda _: None,
        )
        task = asyncio.create_task(
            collector.run(duration_seconds=10, poll_seconds=10)
        )
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        task.cancel("provider-persisted")
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(evidence.terminals[0]["sportradar_captures"], 1)

    async def test_primary_halt_survives_kalshi_close_failure(self) -> None:
        """Catches cleanup replacing the failure that actually halted collection."""

        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
            _provider_failure_attestation,
        )

        class FailingProvider(_SportradarTransport):
            async def fetch_summary(self, match_id: str) -> TrialCapture:
                del match_id
                raise _CodedError("sportradar_primary_failure")

        class CloseFailingTransport(_KalshiTransport):
            async def close(self) -> None:
                self.close_calls += 1
                raise _CodedError("kalshi_cleanup_failure")

        class RecordingEvidence:
            def __init__(self) -> None:
                self.terminals: list[dict[str, object]] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                raise AssertionError(f"unexpected observation: {record!r}")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            transport = CloseFailingTransport(clock, [])
            evidence = RecordingEvidence()
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=FailingProvider(
                    _capture(Path(directory))
                ),
                sportradar_ledger=_SportradarLedger(),
                kalshi_transport=transport,
                market_projector=_Projector(lambda _: None),
                evidence_store=evidence,
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )

            with self.assertRaisesRegex(
                ShadowCollectorError, "sportradar_primary_failure"
            ) as raised:
                await collector.run(duration_seconds=10, poll_seconds=10)

        self.assertEqual(transport.close_calls, 1)
        self.assertIsNone(_provider_failure_attestation(raised.exception))
        self.assertEqual(
            evidence.terminals[0]["code"], "sportradar_primary_failure"
        )

    async def test_clean_provider_failure_attestation_is_source_bound_and_not_publicly_forgeable(
        self,
    ) -> None:
        """Catches trusting public flags, prefixes, or non-provider origins."""

        from inci_tennis_runtime.live_shadow_collector import (
            _CleanProviderFailureAttestation,
            LiveShadowCollector,
            ShadowCollectorError,
            _provider_failure_attestation,
            _provider_failure_attestation_is_valid,
        )

        with self.assertRaises(TypeError):
            ShadowCollectorError(
                "sportradar_transport_unavailable",
                failover_eligible=True,
            )

        class FailingProvider(_SportradarTransport):
            def __init__(self, capture: TrialCapture, code: str) -> None:
                super().__init__(capture)
                self.code = code

            async def fetch_summary(self, match_id: str) -> TrialCapture:
                del match_id
                raise _CodedError(self.code)

        class RecordingEvidence:
            def __init__(self) -> None:
                self.terminals: list[dict[str, object]] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                raise AssertionError(f"unexpected observation: {record!r}")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        for code, expected_source in (
            ("sportradar_transport_unavailable", "sportradar_fetch"),
            ("sportradar_http_status_429", "sportradar_fetch"),
            ("sportradar_new_unknown_failure", None),
        ):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                clock = _Clock()
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=FailingProvider(
                        _capture(Path(directory)), code
                    ),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=_KalshiTransport(clock, []),
                    market_projector=_Projector(lambda _: None),
                    evidence_store=RecordingEvidence(),
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )

                with self.assertRaises(ShadowCollectorError) as raised:
                    await collector.run(duration_seconds=10, poll_seconds=10)

                self.assertEqual(raised.exception.code, code)
                attestation = _provider_failure_attestation(raised.exception)
                if expected_source is None:
                    self.assertIsNone(attestation)
                else:
                    self.assertIs(type(attestation), _CleanProviderFailureAttestation)
                    self.assertEqual(attestation.code, code)
                    self.assertEqual(attestation.source, expected_source)
                    self.assertTrue(
                        _provider_failure_attestation_is_valid(
                            attestation, code
                        )
                    )
                    with self.assertRaises(AttributeError):
                        attestation.code = "sportradar_http_status_500"

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            malformed = replace(_capture(Path(directory)), payload=b"{}")
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(malformed),
                sportradar_ledger=_SportradarLedger(),
                kalshi_transport=_KalshiTransport(clock, []),
                market_projector=_Projector(lambda _: None),
                evidence_store=RecordingEvidence(),
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )
            with self.assertRaises(ShadowCollectorError) as raised:
                await collector.run(duration_seconds=10, poll_seconds=10)
            attestation = _provider_failure_attestation(raised.exception)
            self.assertIs(type(attestation), _CleanProviderFailureAttestation)
            self.assertEqual(attestation.source, "sportradar_parser")

    async def test_provider_coded_kalshi_and_evidence_errors_have_no_attestation(
        self,
    ) -> None:
        """Catches code text laundering non-provider failures into failover."""

        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
            _provider_failure_attestation,
        )

        class ProviderCodedKalshi(_KalshiTransport):
            async def open_readonly(self) -> None:
                self.open_calls += 1
                raise _CodedError("sportradar_transport_unavailable")

        class ProviderCodedEvidence:
            def __init__(self, fail_observation: bool) -> None:
                self.fail_observation = fail_observation
                self.terminals: list[dict[str, object]] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                del record
                if self.fail_observation:
                    raise _CodedError("sportradar_transport_unavailable")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        for label in ("kalshi", "evidence"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                clock = _Clock()
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=_SportradarTransport(
                        _capture(Path(directory))
                    ),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=(
                        ProviderCodedKalshi(clock, [])
                        if label == "kalshi"
                        else _KalshiTransport(clock, [])
                    ),
                    market_projector=_Projector(lambda _: None),
                    evidence_store=ProviderCodedEvidence(label == "evidence"),
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )

                with self.assertRaises(ShadowCollectorError) as raised:
                    await collector.run(duration_seconds=10, poll_seconds=10)

                self.assertEqual(
                    raised.exception.code,
                    "sportradar_transport_unavailable",
                )
                self.assertIsNone(
                    _provider_failure_attestation(raised.exception)
                )

    async def test_cancellation_during_open_still_closes_and_records_terminals(self) -> None:
        """Catches abandoning a partially opened socket on early cancellation."""

        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class OpeningTransport(_KalshiTransport):
            def __init__(self, clock: _Clock) -> None:
                super().__init__(clock, [])
                self.started = asyncio.Event()

            async def open_readonly(self) -> None:
                self.open_calls += 1
                self.started.set()
                await asyncio.Event().wait()

        class RecordingEvidence:
            def __init__(self) -> None:
                self.terminals: list[dict[str, object]] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                raise AssertionError(f"unexpected observation: {record!r}")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            transport = OpeningTransport(clock)
            evidence = RecordingEvidence()
            trial = _SportradarLedger()
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(
                    _capture(Path(directory))
                ),
                sportradar_ledger=trial,
                kalshi_transport=transport,
                market_projector=_Projector(lambda _: None),
                evidence_store=evidence,
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )
            run = asyncio.create_task(
                collector.run(duration_seconds=10, poll_seconds=10)
            )
            await asyncio.wait_for(transport.started.wait(), timeout=1)
            run.cancel("cancel-during-open")
            with self.assertRaises(asyncio.CancelledError):
                await run

        self.assertEqual(transport.close_calls, 1)
        self.assertEqual(evidence.terminals[0]["reason"], "cancelled")
        self.assertEqual(trial.terminals[0]["reason"], "cancelled")
        self.assertEqual(
            trial.terminals[0]["code"],
            "sportradar_shadow_task_cancelled",
        )

    async def test_cancellation_finishes_blocking_raw_and_terminals_then_reraises(self) -> None:
        """Catches swallowed cancellation, reconnect, or abandoned durable writes."""

        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class BlockedProvider(_SportradarTransport):
            def __init__(self, capture: TrialCapture) -> None:
                super().__init__(capture)
                self.started = asyncio.Event()

            async def fetch_summary(self, match_id: str) -> TrialCapture:
                if match_id != MATCH_ID:
                    raise AssertionError("wrong match")
                self.started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        class BlockingEvidence:
            def __init__(self, frame: _Frame) -> None:
                self.frame = frame
                self.persist_started = threading.Event()
                self.persist_release = threading.Event()
                self.persist_finished = threading.Event()
                self.terminals: list[dict[str, object]] = []
                self.worker_threads: list[int] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                self.worker_threads.append(threading.get_ident())
                self.persist_started.set()
                if not self.persist_release.wait(timeout=2):
                    raise AssertionError("test did not release persistence")
                self.persist_finished.set()
                return SimpleNamespace(
                    raw_path="/tmp/test-kalshi.bin",
                    raw_sha256=self.frame.raw_sha256,
                    captured_wall_ns=self.frame.captured_wall_ns,
                    captured_monotonic_ns=self.frame.captured_monotonic_ns,
                    clock_uncertainty_ns=self.frame.clock_uncertainty_ns,
                    physical_connection_generation=1,
                )

            def append_observation(self, record: object) -> None:
                del record
                self.worker_threads.append(threading.get_ident())

            def append_terminal(self, **values: object) -> None:
                self.worker_threads.append(threading.get_ident())
                self.terminals.append(values)

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            frame = _Frame(b'{"type":"orderbook_snapshot"}', clock)
            transport = _KalshiTransport(clock, [frame])
            provider = BlockedProvider(_capture(Path(directory)))
            evidence = BlockingEvidence(frame)
            trial = _SportradarLedger()
            projected: list[object] = []
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=provider,
                sportradar_ledger=trial,
                kalshi_transport=transport,
                market_projector=_Projector(
                    lambda value: projected.append(value)
                ),
                evidence_store=evidence,
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )
            run = asyncio.create_task(
                collector.run(duration_seconds=10, poll_seconds=10)
            )
            await asyncio.wait_for(provider.started.wait(), timeout=1)
            while not evidence.persist_started.is_set():
                await asyncio.sleep(0)
            run.cancel("original-shadow-cancel")
            await asyncio.sleep(0)
            self.assertFalse(run.done())
            evidence.persist_release.set()
            with self.assertRaises(asyncio.CancelledError) as cancelled:
                await asyncio.wait_for(run, timeout=1)

        self.assertIn("original-shadow-cancel", str(cancelled.exception))
        self.assertTrue(evidence.persist_finished.is_set())
        self.assertEqual(projected, [])
        self.assertEqual((transport.open_calls, transport.subscribe_calls), (1, 1))
        self.assertEqual(transport.close_calls, 1)
        self.assertEqual(evidence.terminals[0]["reason"], "cancelled")
        self.assertIsNone(evidence.terminals[0]["code"])
        self.assertEqual(evidence.terminals[0]["kalshi_frames"], 1)
        self.assertEqual(evidence.terminals[0]["sportradar_captures"], 0)
        self.assertEqual(
            trial.terminals,
            [
                {
                    "command": "shadow",
                    "provider_match_id": MATCH_ID,
                    "reason": "cancelled",
                    "code": "sportradar_shadow_task_cancelled",
                }
            ],
        )
        self.assertTrue(evidence.worker_threads)
        self.assertNotIn(threading.get_ident(), evidence.worker_threads)

    async def test_blocking_observation_persistence_does_not_starve_event_loop(self) -> None:
        """Catches synchronous evidence and trial-ledger writes on the event loop."""

        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class BlockingEvidence:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()
                self.terminals: list[dict[str, object]] = []
                self.thread_ids: list[int] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                del record
                self.thread_ids.append(threading.get_ident())
                self.started.set()
                if not self.release.wait(timeout=2):
                    raise AssertionError("test did not release observation")

            def append_terminal(self, **values: object) -> None:
                self.thread_ids.append(threading.get_ident())
                self.terminals.append(values)

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            evidence = BlockingEvidence()
            trial = _SportradarLedger()
            heartbeat = 0
            keep_beating = True

            async def beat() -> None:
                nonlocal heartbeat
                while keep_beating:
                    heartbeat += 1
                    await asyncio.sleep(0)

            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(
                    _capture(Path(directory))
                ),
                sportradar_ledger=trial,
                kalshi_transport=_KalshiTransport(clock, []),
                market_projector=_Projector(lambda _: None),
                evidence_store=evidence,
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )
            pulse = asyncio.create_task(beat())
            run = asyncio.create_task(
                collector.run(duration_seconds=10, poll_seconds=10)
            )
            while not evidence.started.is_set():
                await asyncio.sleep(0)
            before = heartbeat
            for _ in range(20):
                await asyncio.sleep(0)
            self.assertGreater(heartbeat, before)
            self.assertFalse(run.done())
            evidence.release.set()
            self.assertEqual(await asyncio.wait_for(run, timeout=1), "duration_elapsed")
            keep_beating = False
            await pulse

        self.assertNotIn(threading.get_ident(), evidence.thread_ids)

    async def test_recovery_is_bounded_exponential_and_never_loops_forever(self) -> None:
        """Catches unbounded reconnect churn after repeated receive failures."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
        )

        class RecordingClock(_Clock):
            def __init__(self) -> None:
                super().__init__()
                self.recovery_pauses: list[float] = []

            async def pause(self, seconds: float) -> None:
                if seconds > 0:
                    self.recovery_pauses.append(seconds)
                await super().pause(seconds)

        class BlockedProvider(_SportradarTransport):
            async def fetch_summary(self, match_id: str) -> TrialCapture:
                del match_id
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = RecordingClock()
            transport = _KalshiTransport(
                clock,
                [_CodedError("kalshi_ws_receive_failed") for _ in range(4)],
            )
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=BlockedProvider(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=transport,
                    market_projector=_Projector(lambda _: None),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                with self.assertRaisesRegex(
                    ShadowCollectorError, "kalshi_recovery_exhausted"
                ):
                    await asyncio.wait_for(
                        collector.run(duration_seconds=10, poll_seconds=10),
                        timeout=1,
                    )

            self.assertEqual(clock.recovery_pauses, [1.0, 2.0, 4.0])
            self.assertEqual((transport.open_calls, transport.subscribe_calls), (4, 4))
            self.assertEqual(transport.close_calls, 4)

    async def test_terminal_stream_error_halts_without_reconnect(self) -> None:
        """Catches retrying a terminal channel error as a transient disconnect."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
        )

        class BlockedProvider(_SportradarTransport):
            async def fetch_summary(self, match_id: str) -> TrialCapture:
                del match_id
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            transport = _KalshiTransport(
                clock, [_CodedError("kalshi_stream_terminal")]
            )
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=BlockedProvider(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=transport,
                    market_projector=_Projector(lambda _: None),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                with self.assertRaisesRegex(
                    ShadowCollectorError, "kalshi_stream_terminal"
                ):
                    await collector.run(duration_seconds=10, poll_seconds=10)

            self.assertEqual((transport.open_calls, transport.subscribe_calls), (1, 1))
            self.assertEqual(transport.close_calls, 1)

    async def test_kalshi_raw_is_consumed_while_provider_fetch_is_blocked(self) -> None:
        """Catches a synchronous provider call starving the Kalshi capture stream."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class BlockedProvider(_SportradarTransport):
            def __init__(self, capture: TrialCapture) -> None:
                super().__init__(capture)
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def fetch_summary(self, match_id: str) -> TrialCapture:
                if match_id != MATCH_ID:
                    raise AssertionError("wrong match")
                self.started.set()
                await self.release.wait()
                return self.capture

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            frame = _Frame(b'{"type":"orderbook_snapshot"}', clock)
            transport = _KalshiTransport(clock, [frame])
            provider = BlockedProvider(_capture(base))
            projected = asyncio.Event()
            with ShadowEvidenceStore(base / "shadow") as evidence:
                def project(_: object):
                    projected.set()
                    return _candidate_projection()

                projector = _Projector(project)

                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=provider,
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=transport,
                    market_projector=projector,
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                run = asyncio.create_task(
                    collector.run(duration_seconds=10, poll_seconds=10)
                )
                await asyncio.wait_for(provider.started.wait(), timeout=1)
                await asyncio.wait_for(projected.wait(), timeout=1)
                raw_files = tuple((base / "shadow" / "raw").glob("*.bin"))
                self.assertEqual(len(raw_files), 1)
                self.assertEqual(raw_files[0].read_bytes(), frame.payload)
                provider.release.set()
                self.assertEqual(await asyncio.wait_for(run, timeout=1), "duration_elapsed")

    async def test_provider_fetch_clock_bracket_is_durable(self) -> None:
        """Catches inventing provider clock uncertainty without its sampled bracket."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        class BracketedProvider(_SportradarTransport):
            def __init__(self, capture: TrialCapture, clock: _Clock) -> None:
                super().__init__(capture)
                self.clock = clock

            async def fetch_summary(self, match_id: str) -> TrialCapture:
                if match_id != MATCH_ID:
                    raise AssertionError("wrong match")
                self.clock.advance(0.25)
                return self.capture

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=BracketedProvider(_capture(base), clock),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=_KalshiTransport(clock, []),
                    market_projector=_Projector(lambda _: None),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                await collector.run(duration_seconds=10, poll_seconds=10)

            ledger = next((base / "shadow").glob("session-*.jsonl"))
            row = next(
                item
                for item in map(json.loads, ledger.read_text().splitlines())
                if item.get("reason") == "provider_summary_captured"
            )
            self.assertEqual(
                row["provider_request_completed_monotonic_ns"]
                - row["provider_request_started_monotonic_ns"],
                250_000_000,
            )
            self.assertEqual(row["provider_clock_uncertainty_ns"], 250_000_000)
            self.assertGreaterEqual(row["clock_uncertainty_ns"], 250_000_000)

    async def test_persists_frame_before_projection_and_writes_clean_terminal(self) -> None:
        """Catches parsing a Kalshi frame before its exact bytes are durable."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            frame = _Frame(b'{"type":"orderbook_snapshot"}', clock)
            transport = _KalshiTransport(clock, [frame])
            sportradar = _SportradarTransport(_capture(base))
            trial_ledger = _SportradarLedger()
            rendered: list[str] = []
            with ShadowEvidenceStore(base / "shadow") as evidence:
                def project(candidate: object):
                    raw_files = tuple((base / "shadow" / "raw").glob("*.bin"))
                    self.assertEqual(len(raw_files), 1)
                    self.assertEqual(raw_files[0].read_bytes(), frame.payload)
                    return _candidate_projection()

                projector = _Projector(project)

                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=sportradar,
                    sportradar_ledger=trial_ledger,
                    kalshi_transport=transport,
                    market_projector=projector,
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=rendered.append,
                )
                reason = await collector.run(
                    duration_seconds=10,
                    poll_seconds=10,
                )

            self.assertEqual(reason, "duration_elapsed")
            self.assertEqual((transport.open_calls, transport.subscribe_calls), (1, 1))
            self.assertEqual(len(projector.subscriptions), 1)
            self.assertEqual(transport.close_calls, 1)
            self.assertEqual(len(trial_ledger.observations), 1)
            self.assertEqual(
                trial_ledger.terminals,
                [
                    {
                        "command": "shadow",
                        "provider_match_id": MATCH_ID,
                        "reason": "duration_elapsed",
                    }
                ],
            )
            ledger = next((base / "shadow").glob("session-*.jsonl"))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(rows[-1]["kind"], "terminal")
            self.assertEqual(rows[-1]["reason"], "duration_elapsed")
            observation = next(
                row for row in rows if row.get("reason") == "candidate_snapshot_applied"
            )
            self.assertEqual(observation["provider_match_id"], MATCH_ID)
            self.assertEqual(observation["market_tickers"], list(TICKERS))
            self.assertEqual(observation["home_player_name"], "Player Home")
            self.assertEqual(observation["points"], ["30", "15"])
            self.assertEqual(observation["server"], "home")
            self.assertEqual(observation["kalshi_sequence"], 2)
            self.assertEqual(observation["trust"], "unqualified_shadow")
            self.assertTrue(rendered)
            self.assertIn("READ ONLY", rendered[-1])
            self.assertIn("UNQUALIFIED", rendered[-1])
            self.assertIn("NO ORDERS", rendered[-1])

    async def test_raw_persistence_failure_prevents_projection_and_closes(self) -> None:
        """Catches parsing/displaying a frame whose raw bytes were not durable."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceError
        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
        )

        class FailingEvidence:
            def __init__(self) -> None:
                self.observations: list[object] = []
                self.terminals: list[dict[str, object]] = []

            def append_observation(self, record: object) -> None:
                self.observations.append(record)

            def persist_kalshi_frame(self, frame: object) -> object:
                del frame
                raise ShadowEvidenceError("shadow_evidence_raw_write_failed")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            transport = _KalshiTransport(
                clock, [_Frame(b'{"type":"orderbook_snapshot"}', clock)]
            )
            evidence = FailingEvidence()
            projected: list[object] = []
            rendered: list[str] = []
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(_capture(base)),
                sportradar_ledger=_SportradarLedger(),
                kalshi_transport=transport,
                market_projector=_Projector(
                    lambda value: projected.append(value)
                ),
                evidence_store=evidence,
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=rendered.append,
            )

            with self.assertRaisesRegex(
                ShadowCollectorError, "shadow_evidence_raw_write_failed"
            ):
                await collector.run(duration_seconds=10, poll_seconds=10)

            self.assertEqual(projected, [])
            self.assertFalse(
                any("candidate_snapshot" in value for value in rendered)
            )
            self.assertEqual(transport.close_calls, 1)
            self.assertEqual(evidence.terminals[0]["reason"], "halted")
            self.assertEqual(
                evidence.terminals[0]["code"],
                "shadow_evidence_raw_write_failed",
            )

    async def test_malformed_provider_capture_is_counted_before_parser_failure(self) -> None:
        """Catches reporting zero raw captures when the durable payload is malformed."""

        from inci_tennis_runtime.live_shadow_collector import (
            LiveShadowCollector,
            ShadowCollectorError,
        )

        class RecordingEvidence:
            def __init__(self) -> None:
                self.terminals: list[dict[str, object]] = []

            def persist_kalshi_frame(self, frame: object) -> object:
                raise AssertionError(f"unexpected frame: {frame!r}")

            def append_observation(self, record: object) -> None:
                raise AssertionError(f"unexpected observation: {record!r}")

            def append_terminal(self, **values: object) -> None:
                self.terminals.append(values)

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            valid = _capture(base)
            malformed = replace(valid, payload=b"{}")
            clock = _Clock()
            evidence = RecordingEvidence()
            trial = _SportradarLedger()
            collector = LiveShadowCollector(
                provider_match_id=MATCH_ID,
                market_tickers=TICKERS,
                sportradar_transport=_SportradarTransport(malformed),
                sportradar_ledger=trial,
                kalshi_transport=_KalshiTransport(clock, []),
                market_projector=_Projector(lambda _: None),
                evidence_store=evidence,
                wall_ns=clock.wall_ns,
                monotonic_ns=clock.monotonic_ns,
                pause=clock.pause,
                stop_requested=lambda: False,
                render=lambda _: None,
            )
            with self.assertRaises(ShadowCollectorError):
                await collector.run(duration_seconds=10, poll_seconds=10)

        self.assertEqual(evidence.terminals[0]["sportradar_captures"], 1)
        self.assertEqual(len(trial.failures), 1)

    async def test_gap_clears_candidate_prices_and_requests_resnapshot(self) -> None:
        """Catches carrying candidate prices across a detected sequence gap."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            first = _Frame(b'{"seq":2}', clock)
            second = _Frame(b'{"seq":4}', clock)
            transport = _KalshiTransport(clock, [first, second])
            projections = iter(
                (
                    _candidate_projection(),
                    _blocked_projection(
                        status="gap",
                        reason="kalshi_sequence_gap",
                        sequence=4,
                        snapshot_needed=True,
                    ),
                )
            )
            projector = _Projector(lambda _: next(projections))
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=_SportradarTransport(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=transport,
                    market_projector=projector,
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                await collector.run(duration_seconds=10, poll_seconds=10)

            self.assertEqual(transport.snapshot_requests, [27])
            self.assertEqual(len(projector.snapshots), 1)
            ledger = next((base / "shadow").glob("session-*.jsonl"))
            gap = next(
                row
                for row in map(json.loads, ledger.read_text().splitlines())
                if row.get("reason") == "kalshi_sequence_gap"
            )
            self.assertEqual(gap["kalshi_status"], "gap")
            self.assertIsNone(gap["home_yes_bid"])
            self.assertIsNone(gap["home_yes_ask"])

    async def test_incomplete_snapshot_clears_both_books_without_resnapshot_loop(self) -> None:
        """Catches treating a valid empty book as a sequence failure."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            transport = _KalshiTransport(clock, [_Frame(b'{"seq":2}', clock)])
            projector = _Projector(
                lambda _: _blocked_projection(
                    status="incomplete",
                    reason="candidate_book_incomplete",
                    sequence=2,
                    snapshot_needed=False,
                )
            )
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=_SportradarTransport(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=transport,
                    market_projector=projector,
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                await collector.run(duration_seconds=10, poll_seconds=10)

            self.assertEqual(transport.snapshot_requests, [])
            self.assertEqual(projector.snapshots, [])
            ledger = next((base / "shadow").glob("session-*.jsonl"))
            row = next(
                item
                for item in map(json.loads, ledger.read_text().splitlines())
                if item.get("reason") == "candidate_book_incomplete"
            )
            self.assertEqual(row["kalshi_status"], "incomplete")
            self.assertIsNone(row["home_yes_bid"])
            self.assertIsNone(row["away_yes_bid"])

    async def test_parser_failure_reconnects_without_projecting_prices(self) -> None:
        """Catches leaving a malformed stream connected or retaining its book."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            transport = _KalshiTransport(clock, [_Frame(b"malformed", clock)])
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=_SportradarTransport(_capture(base)),
                    sportradar_ledger=_SportradarLedger(),
                    kalshi_transport=transport,
                    market_projector=_Projector(
                        lambda _: (_ for _ in ()).throw(
                            _CodedError("kalshi_ws_contract_invalid")
                        )
                    ),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: False,
                    render=lambda _: None,
                )
                await collector.run(duration_seconds=10, poll_seconds=10)

            self.assertEqual((transport.open_calls, transport.subscribe_calls), (2, 2))
            self.assertEqual(transport.close_calls, 2)
            ledger = next((base / "shadow").glob("session-*.jsonl"))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            parser_row = next(
                row for row in rows if row.get("reason") == "kalshi_parser_error"
            )
            self.assertEqual(parser_row["kalshi_status"], "error")
            self.assertIsNone(parser_row["home_yes_bid"])

    async def test_operator_stop_records_clean_terminal(self) -> None:
        """Catches converting an operator stop into a crash/halt record."""

        from inci_tennis_io.shadow_evidence import ShadowEvidenceStore
        from inci_tennis_runtime.live_shadow_collector import LiveShadowCollector

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            clock = _Clock()
            transport = _KalshiTransport(clock, [])
            trial = _SportradarLedger()
            with ShadowEvidenceStore(base / "shadow") as evidence:
                collector = LiveShadowCollector(
                    provider_match_id=MATCH_ID,
                    market_tickers=TICKERS,
                    sportradar_transport=_SportradarTransport(_capture(base)),
                    sportradar_ledger=trial,
                    kalshi_transport=transport,
                    market_projector=_Projector(lambda _: None),
                    evidence_store=evidence,
                    wall_ns=clock.wall_ns,
                    monotonic_ns=clock.monotonic_ns,
                    pause=clock.pause,
                    stop_requested=lambda: clock.monotonic_ns() >= 2_000_000_000,
                    render=lambda _: None,
                )
                reason = await collector.run(duration_seconds=10, poll_seconds=10)

            self.assertEqual(reason, "operator_interrupt")
            self.assertEqual(
                trial.terminals[0],
                {
                    "command": "shadow",
                    "provider_match_id": MATCH_ID,
                    "reason": "operator_interrupt",
                    "code": "sportradar_operator_interrupt",
                },
            )


class DashboardTests(unittest.TestCase):
    def test_verified_dashboard_keeps_observation_only_safety_label(self) -> None:
        """Catches verified source correlation implying trading authority."""

        from inci_tennis_runtime.live_shadow_collector import (
            ShadowDashboardView,
            render_shadow_dashboard,
        )

        rendered = render_shadow_dashboard(
            ShadowDashboardView(
                provider_match_id=MATCH_ID,
                players="Player Home vs Player Away",
                score="sets 0-0 | games 0-0 | points 0-0",
                server="--",
                sportradar_age_seconds=1.0,
                market_tickers=TICKERS,
                home_book="--",
                away_book="--",
                kalshi_status="waiting",
                kalshi_generation=None,
                kalshi_sequence=None,
                kalshi_age_seconds=None,
                last_event="--",
                reason="candidate_book_incomplete",
                sportradar_captures=1,
                kalshi_frames=0,
                mapping_mode="auto_matched",
            )
        )

        self.assertIn(
            "READ ONLY / VERIFIED SOURCE LINK / UNQUALIFIED / NO SIGNALS / NO P&L / NO ORDERS",
            rendered,
        )
        self.assertIn("VERIFIED SOURCE LINK / UNQUALIFIED", rendered)
        self.assertNotIn("OPERATOR-SUPPLIED", rendered)

    def test_dashboard_labels_candidate_data_without_trading_language(self) -> None:
        """Catches presenting unqualified evidence as a signal or execution."""

        from inci_tennis_runtime.live_shadow_collector import (
            ShadowDashboardView,
            render_shadow_dashboard,
        )

        rendered = render_shadow_dashboard(
            ShadowDashboardView(
                provider_match_id=MATCH_ID,
                players="Player Home vs Player Away",
                score="sets 0-0 | games 3-2 | points 30-15",
                server="home",
                sportradar_age_seconds=3.0,
                market_tickers=TICKERS,
                home_book="bid 0.31 x 12.00 | ask 0.34 x 8.00",
                away_book="--",
                kalshi_status="candidate",
                kalshi_generation=1,
                kalshi_sequence=2,
                kalshi_age_seconds=0.2,
                last_event="9003 point (ace)",
                reason="candidate_snapshot_applied",
                sportradar_captures=1,
                kalshi_frames=2,
            )
        )

        self.assertIn(
            "READ ONLY / UNQUALIFIED / NO SIGNALS / NO ORDERS",
            rendered,
        )
        self.assertIn(TICKERS[0], rendered)
        self.assertIn(TICKERS[1], rendered)
        self.assertIn("Player Home vs Player Away", rendered)
        self.assertIn("candidate_snapshot_applied", rendered)
        self.assertIn("KALSHI STATUS", rendered)
        self.assertIn("OPERATOR-SUPPLIED / UNVERIFIED", rendered)
        lowered = rendered.casefold()
        self.assertNotIn("recommend", lowered)
        self.assertNotIn("executable", lowered)
        self.assertNotIn("p&l", lowered)


if __name__ == "__main__":
    unittest.main()
