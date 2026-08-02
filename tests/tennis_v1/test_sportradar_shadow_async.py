from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


_PAYLOAD = b'{"sport_event":{"id":"sr:sport_event:123456"}}'


class _FakeContent:
    def __init__(self, chunks: tuple[object, ...]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, chunk_size: int):
        if chunk_size != 65_536:
            raise AssertionError("unexpected chunk size")
        for chunk in self._chunks:
            await asyncio.sleep(0)
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        payload: bytes = _PAYLOAD,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Encoding": "identity",
            "Content-Length": str(len(payload)),
        }
        self.content = _FakeContent((payload,))


class _FakeRequestContext:
    def __init__(
        self,
        response: _FakeResponse,
        *,
        entered: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self._response = response
        self._entered = entered
        self._release = release
        self.exited = False

    async def __aenter__(self) -> _FakeResponse:
        if self._entered is not None:
            self._entered.set()
        if self._release is not None:
            await self._release.wait()
        return self._response

    async def __aexit__(self, *_: object) -> None:
        self.exited = True


class _FakeSession:
    def __init__(self, request: _FakeRequestContext) -> None:
        self._request = request
        self.calls: list[dict[str, object]] = []
        self.close_calls = 0

    def get(self, url: str, **kwargs: object) -> _FakeRequestContext:
        self.calls.append({"url": url, **kwargs})
        return self._request

    async def close(self) -> None:
        self.close_calls += 1


def _outcome_rows(root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (root / "outcomes.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]


class SportradarShadowAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_after_durable_capture_keeps_completed_count(self) -> None:
        """Catches a persisted provider capture disappearing from terminal counts."""

        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession(_FakeRequestContext(_FakeResponse()))
            started = threading.Event()
            release = threading.Event()
            with TrialUsageLedger(root) as ledger:
                original = ledger.persist_raw

                def delayed_persist(*args: object, **kwargs: object) -> Path:
                    path = original(*args, **kwargs)
                    started.set()
                    if not release.wait(timeout=2):
                        raise AssertionError("test did not release persistence")
                    return path

                ledger.persist_raw = delayed_persist  # type: ignore[method-assign]
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                task = asyncio.create_task(
                    transport.fetch_summary("sr:sport_event:123456")
                )
                self.assertTrue(
                    await asyncio.to_thread(started.wait, 1),
                    "persistence never started",
                )
                task.cancel("cancel-after-provider-persist")
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                self.assertEqual(transport.completed_captures, 1)

            self.assertEqual(len(_outcome_rows(root)), 1)
            self.assertEqual(len(list((root / "raw").glob("*.json"))), 1)

    async def test_fetch_summary_is_get_only_and_durable_before_return(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = _FakeRequestContext(_FakeResponse())
            session = _FakeSession(request)
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                capture = await transport.fetch_summary(
                    "sr:sport_event:123456"
                )

            self.assertEqual(capture.payload, _PAYLOAD)
            self.assertEqual(capture.raw_path.read_bytes(), _PAYLOAD)
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(
                session.calls[0],
                {
                    "url": (
                        "https://api.sportradar.com/tennis/trial/v3/en/"
                        "sport_events/sr:sport_event:123456/summary.json"
                    ),
                    "headers": {
                        "accept": "application/json",
                        "accept-encoding": "identity",
                        "x-api-key": "safe-trial-key",
                    },
                    "allow_redirects": False,
                    "auto_decompress": False,
                },
            )
            self.assertTrue(request.exited)
            rows = _outcome_rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                (rows[0]["outcome"], rows[0]["code"]),
                ("captured", "sportradar_capture_persisted"),
            )
            self.assertEqual(rows[0]["raw_file"], capture.raw_path.name)

    async def test_fetch_timeline_uses_fixed_route(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            session = _FakeSession(_FakeRequestContext(_FakeResponse()))
            with TrialUsageLedger(Path(directory)) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                await transport.fetch_timeline("sr:sport_event:123456")
            self.assertEqual(
                session.calls[0]["url"],
                "https://api.sportradar.com/tennis/trial/v3/en/"
                "sport_events/sr:sport_event:123456/timeline.json",
            )

    async def test_fetch_live_summaries_uses_fixed_durable_route(self) -> None:
        """Catches live discovery bypassing the quota ledger or fixed route."""

        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession(_FakeRequestContext(_FakeResponse()))
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                capture = await transport.fetch_live_summaries()

            self.assertEqual(capture.payload, _PAYLOAD)
            self.assertEqual(
                session.calls[0]["url"],
                "https://api.sportradar.com/tennis/trial/v3/en/"
                "schedules/live/summaries.json",
            )
            usage = (root / "usage.jsonl").read_text(encoding="utf-8")
            self.assertIn('"route":"live_summaries"', usage)
            self.assertEqual(
                (_outcome_rows(root)[0]["outcome"], len(list((root / "raw").glob("*.json")))),
                ("captured", 1),
            )

    async def test_blocked_request_does_not_block_loop_and_timeout_is_recorded(
        self,
    ) -> None:
        from inci_tennis_io import sportradar_shadow_async as module
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered = asyncio.Event()
            never_release = asyncio.Event()
            session = _FakeSession(
                _FakeRequestContext(
                    _FakeResponse(),
                    entered=entered,
                    release=never_release,
                )
            )
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                with patch.object(module, "_TOTAL_TIMEOUT_SECONDS", 0.02):
                    fetch = asyncio.create_task(
                        transport.fetch_summary("sr:sport_event:123456")
                    )
                    await asyncio.wait_for(entered.wait(), timeout=0.5)
                    loop_progressed = False

                    async def heartbeat() -> None:
                        nonlocal loop_progressed
                        await asyncio.sleep(0)
                        loop_progressed = True

                    await asyncio.wait_for(heartbeat(), timeout=0.5)
                    self.assertTrue(loop_progressed)
                    with self.assertRaisesRegex(
                        SportradarTrialObserverError,
                        "sportradar_total_deadline",
                    ):
                        await fetch

            self.assertEqual(len(session.calls), 1)
            self.assertEqual(
                (root / "usage.jsonl").read_text(encoding="utf-8").count("\n"),
                1,
            )
            rows = _outcome_rows(root)
            self.assertEqual(
                (len(rows), rows[0]["outcome"], rows[0]["code"]),
                (1, "failed", "sportradar_total_deadline"),
            )
            self.assertEqual(list((root / "raw").glob("*.json")), [])

    async def test_external_cancellation_records_interrupted_attempt(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered = asyncio.Event()
            session = _FakeSession(
                _FakeRequestContext(
                    _FakeResponse(),
                    entered=entered,
                    release=asyncio.Event(),
                )
            )
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                task = asyncio.create_task(
                    transport.fetch_summary("sr:sport_event:123456")
                )
                await asyncio.wait_for(entered.wait(), timeout=0.5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            row = _outcome_rows(root)[0]
            self.assertEqual(
                (row["outcome"], row["code"]),
                (
                    "failed",
                    "sportradar_operator_interrupt_during_request",
                ),
            )

    async def test_http_failure_is_redacted_not_retried_and_has_outcome(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession(
                _FakeRequestContext(
                    _FakeResponse(b"secret body", status=503)
                )
            )
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_http_status_503",
                ) as caught:
                    await transport.fetch_summary("sr:sport_event:123456")
                self.assertNotIn("safe-trial-key", str(caught.exception))
                self.assertNotIn("secret body", str(caught.exception))
                self.assertNotIn("safe-trial-key", repr(transport))

            self.assertEqual(len(session.calls), 1)
            row = _outcome_rows(root)[0]
            self.assertEqual(
                (row["outcome"], row["code"], row["raw_file"]),
                ("failed", "sportradar_http_status_503", None),
            )

    async def test_response_contract_rejects_headers_length_body_and_secret(
        self,
    ) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        cases = (
            (
                _FakeResponse(headers={"Content-Type": "text/html"}),
                "sportradar_content_type_invalid",
            ),
            (
                _FakeResponse(
                    headers={
                        "Content-Type": "application/json",
                        "Content-Encoding": "gzip",
                    }
                ),
                "sportradar_content_encoding_invalid",
            ),
            (
                _FakeResponse(
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": "999",
                    }
                ),
                "sportradar_content_length_mismatch",
            ),
            (
                _FakeResponse(
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": "8388609",
                    }
                ),
                "sportradar_body_too_large",
            ),
            (
                _FakeResponse(b'{"echo":"safe-trial-key"}'),
                "sportradar_credential_reflected",
            ),
            (
                _FakeResponse(b'{"echo":"\\u0073afe-trial-key"}'),
                "sportradar_credential_reflected",
            ),
        )
        for index, (response, code) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                session = _FakeSession(_FakeRequestContext(response))
                with TrialUsageLedger(root) as ledger:
                    transport = SportradarShadowAsyncTransport(
                        api_key="safe-trial-key",
                        ledger=ledger,
                        session=session,
                    )
                    with self.assertRaisesRegex(
                        SportradarTrialObserverError,
                        code,
                    ):
                        await transport.fetch_summary(
                            "sr:sport_event:123456"
                        )
                self.assertEqual(list((root / "raw").glob("*.json")), [])
                self.assertEqual(_outcome_rows(root)[0]["code"], code)

    async def test_streamed_body_over_eight_mib_is_never_persisted(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = _FakeResponse(
                headers={"Content-Type": "application/json"}
            )
            response.content = _FakeContent((b"x" * 8_388_609,))
            session = _FakeSession(_FakeRequestContext(response))
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_body_too_large",
                ):
                    await transport.fetch_timeline("sr:sport_event:123456")
            self.assertEqual(list((root / "raw").glob("*.json")), [])
            self.assertEqual(
                _outcome_rows(root)[0]["code"],
                "sportradar_body_too_large",
            )

    async def test_deadline_after_durable_capture_does_not_duplicate_outcome(
        self,
    ) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        class Clock:
            value = 0

            def monotonic_ns(self) -> int:
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = Clock()
            session = _FakeSession(_FakeRequestContext(_FakeResponse()))
            with TrialUsageLedger(root) as ledger:
                original = ledger.persist_raw

                def delayed_persist(*args: object, **kwargs: object) -> Path:
                    raw_path = original(*args, **kwargs)
                    clock.value = 15_000_000_001
                    return raw_path

                ledger.persist_raw = delayed_persist  # type: ignore[method-assign]
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                    monotonic_ns=clock.monotonic_ns,
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_total_deadline",
                ):
                    await transport.fetch_summary("sr:sport_event:123456")

            rows = _outcome_rows(root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(
                (rows[0]["outcome"], rows[0]["code"]),
                ("captured", "sportradar_capture_persisted"),
            )
            raw_files = list((root / "raw").glob("*.json"))
            self.assertEqual(len(raw_files), 1)
            self.assertEqual(raw_files[0].read_bytes(), _PAYLOAD)

    async def test_default_session_factory_receives_exact_limits(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import TrialUsageLedger

        values: list[dict[str, object]] = []
        session = _FakeSession(_FakeRequestContext(_FakeResponse()))

        def factory(**kwargs: object) -> _FakeSession:
            values.append(kwargs)
            return session

        with tempfile.TemporaryDirectory() as directory:
            with TrialUsageLedger(Path(directory)) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session_factory=factory,
                )
                await transport.fetch_summary("sr:sport_event:123456")
                await transport.close()

        self.assertEqual(
            values,
            [
                {
                    "total_timeout_seconds": 15,
                    "connect_timeout_seconds": 3,
                    "read_timeout_seconds": 10,
                    "trust_env": False,
                    "auto_decompress": False,
                }
            ],
        )
        self.assertEqual(session.close_calls, 1)

    async def test_close_is_idempotent_and_closed_transport_cannot_request(
        self,
    ) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            session = _FakeSession(_FakeRequestContext(_FakeResponse()))
            with TrialUsageLedger(Path(directory)) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                await transport.close()
                await transport.close()
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_transport_closed",
                ):
                    await transport.fetch_summary("sr:sport_event:123456")

        self.assertEqual(session.close_calls, 1)
        self.assertEqual(session.calls, [])

    async def test_invalid_match_id_is_rejected_before_reservation(self) -> None:
        from inci_tennis_io.sportradar_shadow_async import (
            SportradarShadowAsyncTransport,
        )
        from inci_tennis_io.sportradar_trial_transport import (
            SportradarTrialObserverError,
            TrialUsageLedger,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _FakeSession(_FakeRequestContext(_FakeResponse()))
            with TrialUsageLedger(root) as ledger:
                transport = SportradarShadowAsyncTransport(
                    api_key="safe-trial-key",
                    ledger=ledger,
                    session=session,
                )
                with self.assertRaisesRegex(
                    SportradarTrialObserverError,
                    "sportradar_match_identifier_invalid",
                ):
                    await transport.fetch_summary("../portfolio/orders")

            self.assertEqual(session.calls, [])
            self.assertEqual(
                (root / "usage.jsonl").read_text(encoding="utf-8"),
                "",
            )


if __name__ == "__main__":
    unittest.main()
