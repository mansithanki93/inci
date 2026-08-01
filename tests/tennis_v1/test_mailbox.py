from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
import os
import queue
import select
import threading
import unittest
from unittest import mock

import tennis_v1.mailbox as mailbox_module
from tennis_v1.mailbox import DashboardSnapshot, LatestSnapshotMailbox
from tennis_v1.sequencer import (
    EventRuntime,
    bind_provider_persistence_authorizer,
)
from tennis_v1.state import initial_state
from tennis_v1.wal import JournalWriter
from tests.tennis_v1.test_sequencer import captured, concrete_environment


SESSION_ID = "12345678-1234-4234-8234-123456789abc"


def snapshot(sequence: int = 0) -> DashboardSnapshot:
    return DashboardSnapshot(
        session_id=SESSION_ID,
        last_applied_raw_seq=sequence,
        raw_count=sequence,
        derived_count=sequence,
        trace_sha256=f"{sequence:064x}",
    )


class MailboxTests(unittest.TestCase):
    def test_snapshot_is_exact_frozen_slotted_and_primitive_only(self) -> None:
        value = snapshot(1)
        self.assertIs(type(value), DashboardSnapshot)
        self.assertFalse(hasattr(value, "__dict__"))
        self.assertEqual(
            tuple(
                type(item)
                for item in (
                    value.session_id,
                    value.last_applied_raw_seq,
                    value.raw_count,
                    value.derived_count,
                    value.trace_sha256,
                )
            ),
            (str, int, int, int, str),
        )
        with self.assertRaises(FrozenInstanceError):
            value.raw_count = 2  # type: ignore[misc]

    def test_snapshot_rejects_invalid_identity_digest_and_integer_values(self) -> None:
        class IntegerSubclass(int):
            pass

        valid = {
            "session_id": SESSION_ID,
            "last_applied_raw_seq": 0,
            "raw_count": 0,
            "derived_count": 0,
            "trace_sha256": "0" * 64,
        }
        cases = (
            ("session_id", "unsafe/session"),
            ("trace_sha256", "not-a-digest"),
            ("last_applied_raw_seq", -1),
            ("last_applied_raw_seq", True),
            ("raw_count", IntegerSubclass(0)),
            ("derived_count", -1),
        )
        for field_name, invalid in cases:
            with self.subTest(field_name=field_name, invalid=invalid):
                values = dict(valid)
                values[field_name] = invalid
                with self.assertRaises((TypeError, ValueError)):
                    DashboardSnapshot(**values)

    def test_publish_never_waits_for_slow_consumer_and_keeps_latest(self) -> None:
        mailbox = LatestSnapshotMailbox()
        first = snapshot(1)
        latest = snapshot(3)
        consumed: list[DashboardSnapshot] = []
        consumer_started = threading.Event()
        release_consumer = threading.Event()

        mailbox.publish(first)

        def slow_consumer() -> None:
            consumed.append(mailbox.take(timeout=None))
            consumer_started.set()
            if not release_consumer.wait(2.0):
                raise AssertionError("slow_consumer_release_timeout")

        consumer = threading.Thread(target=slow_consumer)
        consumer.start()
        self.assertTrue(consumer_started.wait(2.0))
        mailbox.publish(snapshot(2))
        mailbox.publish(latest)
        self.assertEqual(mailbox.take(timeout=0.0), latest)
        release_consumer.set()
        consumer.join(2.0)
        self.assertFalse(consumer.is_alive())
        self.assertEqual(consumed, [first])

    def test_two_first_publishers_have_one_identity_winner_without_corruption(self) -> None:
        mailbox = LatestSnapshotMailbox()
        barrier = threading.Barrier(3)
        outcomes: list[tuple[str, DashboardSnapshot, object]] = []
        outcome_lock = threading.Lock()

        def publish(candidate: DashboardSnapshot) -> None:
            barrier.wait()
            try:
                mailbox.publish(candidate)
            except BaseException as error:
                result: tuple[str, DashboardSnapshot, object] = (
                    "rejected",
                    candidate,
                    error,
                )
            else:
                result = ("published", candidate, threading.current_thread())
            with outcome_lock:
                outcomes.append(result)

        candidates = (snapshot(1), snapshot(2))
        workers = tuple(
            threading.Thread(target=publish, args=(candidate,))
            for candidate in candidates
        )
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(2.0)
            self.assertFalse(worker.is_alive())

        published = [item for item in outcomes if item[0] == "published"]
        rejected = [item for item in outcomes if item[0] == "rejected"]
        self.assertEqual(len(published), 1)
        self.assertEqual(len(rejected), 1)
        self.assertIs(type(rejected[0][2]), RuntimeError)
        self.assertEqual(str(rejected[0][2]), "mailbox_wrong_publisher")
        self.assertEqual(mailbox.take(timeout=0.0), published[0][1])

    def test_equal_thread_ident_cannot_impersonate_publisher_object(self) -> None:
        mailbox = LatestSnapshotMailbox()
        first = threading.Thread()
        second = threading.Thread()
        first._ident = 1776  # type: ignore[attr-defined]
        second._ident = 1776  # type: ignore[attr-defined]
        self.assertEqual(first.ident, second.ident)

        with mock.patch.object(
            mailbox_module.threading,
            "current_thread",
            return_value=first,
        ):
            mailbox.publish(snapshot(1))
        with (
            mock.patch.object(
                mailbox_module.threading,
                "current_thread",
                return_value=second,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                r"\Amailbox_wrong_publisher\Z",
            ),
        ):
            mailbox.publish(snapshot(2))
        self.assertEqual(mailbox.take(timeout=0.0), snapshot(1))

    def test_invalid_and_failed_first_publish_do_not_claim_identity(self) -> None:
        mailbox = LatestSnapshotMailbox()
        invalid = object.__new__(DashboardSnapshot)
        object.__setattr__(invalid, "session_id", SESSION_ID)
        object.__setattr__(invalid, "last_applied_raw_seq", 0)
        object.__setattr__(invalid, "raw_count", -1)
        object.__setattr__(invalid, "derived_count", 0)
        object.__setattr__(invalid, "trace_sha256", "0" * 64)

        with self.assertRaises(ValueError):
            mailbox.publish(invalid)

        with (
            mock.patch.object(
                mailbox._queue,  # type: ignore[attr-defined]
                "put_nowait",
                side_effect=queue.Full,
            ),
            self.assertRaises(queue.Full),
        ):
            mailbox.publish(snapshot(1))

        failures: list[BaseException] = []

        def later_publisher() -> None:
            try:
                mailbox.publish(snapshot(2))
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=later_publisher)
        worker.start()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(mailbox.take(timeout=0.0), snapshot(2))

    def test_take_timeout_contract_and_exact_queue_empty(self) -> None:
        mailbox = LatestSnapshotMailbox()
        for timeout in (0.0, 0.01):
            with self.subTest(timeout=timeout), self.assertRaises(queue.Empty):
                mailbox.take(timeout=timeout)

        class FloatSubclass(float):
            pass

        invalid = (
            True,
            0,
            -0.1,
            math.nan,
            math.inf,
            -math.inf,
            FloatSubclass(0.0),
            "0",
            object(),
        )
        for timeout in invalid:
            with self.subTest(timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    mailbox.take(timeout=timeout)  # type: ignore[arg-type]

    def test_take_none_blocks_consumer_without_blocking_publisher(self) -> None:
        mailbox = LatestSnapshotMailbox()
        entered = threading.Event()
        received: list[DashboardSnapshot] = []
        failures: list[BaseException] = []

        def consumer() -> None:
            entered.set()
            try:
                received.append(mailbox.take(timeout=None))
            except BaseException as error:
                failures.append(error)

        worker = threading.Thread(target=consumer)
        worker.start()
        self.assertTrue(entered.wait(2.0))
        mailbox.publish(snapshot(4))
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(received, [snapshot(4)])

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_forked_child_rejects_publish_and_take_before_inherited_locks(self) -> None:
        mailbox = LatestSnapshotMailbox()
        read_fd, write_fd = os.pipe()
        child_pid = -1
        mailbox._replacement_lock.acquire()  # type: ignore[attr-defined]
        mailbox._queue.mutex.acquire()  # type: ignore[attr-defined]
        try:
            child_pid = os.fork()
            if child_pid == 0:
                os.close(read_fd)
                results: list[str] = []
                for operation in (
                    lambda: mailbox.publish(snapshot(1)),
                    lambda: mailbox.take(timeout=0.0),
                ):
                    try:
                        operation()
                    except BaseException as error:
                        results.append(f"{type(error).__name__}:{error}")
                    else:
                        results.append("accepted")
                os.write(write_fd, "|".join(results).encode("ascii"))
                os._exit(0)
        finally:
            if child_pid != 0:
                mailbox._queue.mutex.release()  # type: ignore[attr-defined]
                mailbox._replacement_lock.release()  # type: ignore[attr-defined]
                os.close(write_fd)

        ready, _, _ = select.select((read_fd,), (), (), 2.0)
        self.assertEqual(ready, [read_fd])
        payload = os.read(read_fd, 4096).decode("ascii")
        os.close(read_fd)
        waited_pid, status = os.waitpid(child_pid, 0)
        self.assertEqual(waited_pid, child_pid)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)
        self.assertEqual(
            payload,
            "RuntimeError:mailbox_forked_process|"
            "RuntimeError:mailbox_forked_process",
        )

    def test_failing_consumer_cannot_poison_real_runtime_writer_trace_or_wal(self) -> None:
        mailbox = LatestSnapshotMailbox()
        with concrete_environment() as (
            fixture,
            coordinator,
            gate,
            session_manifest,
        ):
            authorizer = bind_provider_persistence_authorizer(
                gate=gate,
                coordinator=coordinator,
                session_manifest=session_manifest,
            )
            capability = coordinator.arm_before_wal(
                session_manifest=session_manifest,
                decision=authorizer.bound_decision,
                persistence_authorizer=authorizer,
            )
            writer = JournalWriter.create(
                write_capability=capability,
                session_manifest=session_manifest,
            )
            runtime = EventRuntime(
                writer=writer,
                state=initial_state(session_manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            runtime.ingest(captured(authorizer, provider_sequence="A-1"))
            state_before = runtime.state
            trace_before = runtime.trace_sha256
            writer_before = (
                writer.latest_raw,
                writer.poisoned,
                object.__getattribute__(writer, "_next_seq"),
                object.__getattribute__(writer, "_record_count"),
                object.__getattribute__(writer, "_raw_count"),
                object.__getattribute__(writer, "_derived_count"),
            )
            wal_path = (
                fixture.config.state_root
                / "sessions"
                / f"{session_manifest.session_id}.wal"
            )
            wal_before = wal_path.read_bytes()
            dashboard_value = DashboardSnapshot(
                session_id=state_before.session_id,
                last_applied_raw_seq=state_before.last_applied_raw_seq,
                raw_count=state_before.raw_count,
                derived_count=state_before.derived_count,
                trace_sha256=trace_before,
            )
            mailbox.publish(dashboard_value)

            entered = threading.Event()
            failures: list[BaseException] = []

            def failing_consumer() -> None:
                value = mailbox.take(timeout=1.0)
                entered.set()
                try:
                    self.assertEqual(value, dashboard_value)
                    raise RuntimeError("dashboard_failed")
                except BaseException as error:
                    failures.append(error)

            consumer = threading.Thread(target=failing_consumer)
            consumer.start()
            self.assertTrue(entered.wait(2.0))
            consumer.join(2.0)
            self.assertFalse(consumer.is_alive())
            self.assertEqual(len(failures), 1)
            self.assertIs(type(failures[0]), RuntimeError)
            self.assertEqual(str(failures[0]), "dashboard_failed")

            self.assertIs(runtime.state, state_before)
            self.assertEqual(runtime.trace_sha256, trace_before)
            self.assertEqual(
                (
                    writer.latest_raw,
                    writer.poisoned,
                    object.__getattribute__(writer, "_next_seq"),
                    object.__getattribute__(writer, "_record_count"),
                    object.__getattribute__(writer, "_raw_count"),
                    object.__getattribute__(writer, "_derived_count"),
                ),
                writer_before,
            )
            self.assertEqual(wal_path.read_bytes(), wal_before)

            runtime.ingest(captured(authorizer, provider_sequence="A-2"))
            self.assertEqual(runtime.state.raw_count, 2)
            self.assertNotEqual(runtime.trace_sha256, trace_before)
            self.assertNotEqual(wal_path.read_bytes(), wal_before)
            latest = DashboardSnapshot(
                session_id=runtime.state.session_id,
                last_applied_raw_seq=runtime.state.last_applied_raw_seq,
                raw_count=runtime.state.raw_count,
                derived_count=runtime.state.derived_count,
                trace_sha256=runtime.trace_sha256,
            )
            mailbox.publish(latest)
            self.assertEqual(mailbox.take(timeout=0.0), latest)


if __name__ == "__main__":
    unittest.main()
