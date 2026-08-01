from __future__ import annotations

import ast
from contextlib import nullcontext
from dataclasses import replace
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
import warnings
from unittest import mock

from tennis_v1.capture import CaptureValidationError
from tennis_v1.codec import canonical_record_sha256
from tennis_v1.entitlements import ProviderGateError, QualificationReason
from tennis_v1.events import CapturedInput, PersistedEvent, RecordKind
from tennis_v1.ingress import (
    BoundedIngress,
    DurableIngressReceipt,
    IngressBackpressureHalt,
    IngressClosed,
    IngressItem,
    IngressOwnerUnresponsive,
)
from tennis_v1.retention import (
    RetentionCoordinator,
    RetentionError,
    RetentionGlobalHalt,
)
from tennis_v1.replay_core import scan_diagnostic_prefix
from tennis_v1.sequencer import (
    EventRuntime,
    RuntimePoisoned,
    WrongOwnerThread,
    bind_provider_persistence_authorizer,
)
from tennis_v1.session import session_manifest_sha256
from tennis_v1.state import initial_state
from tennis_v1.wal import (
    JournalDurabilityError,
    JournalReader,
    JournalWriter,
    ScanIssue,
)
from tests.tennis_v1.test_sequencer import (
    RuntimeFixture,
    captured,
    concrete_environment,
)


class _FloatSubclass(float):
    pass


class _IntSubclass(int):
    pass


class _CapturedSubclass(CapturedInput):
    pass


class _ProducerResult:
    def __init__(self) -> None:
        self.receipt: DurableIngressReceipt | None = None
        self.error: BaseException | None = None
        self.done = threading.Event()


def _start_producer(
    ingress: BoundedIngress,
    item: IngressItem,
) -> tuple[threading.Thread, _ProducerResult]:
    result = _ProducerResult()

    def run() -> None:
        try:
            result.receipt = ingress.enqueue(item)
        except BaseException as error:
            result.error = error
        finally:
            result.done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def _join(thread: threading.Thread, result: _ProducerResult) -> None:
    if not result.done.wait(3.0):
        raise AssertionError("producer did not reach a terminal outcome")
    thread.join()


def _capture_ingress_exception(
    errors: list[BaseException],
    operation,
) -> None:
    try:
        operation()
    except BaseException as error:
        errors.append(error)


def _wait_until_queue_size(
    ingress: BoundedIngress,
    expected_size: int,
) -> None:
    deadline = time.monotonic() + 3.0
    with ingress._condition:  # type: ignore[attr-defined]
        while (
            ingress._queue.qsize()  # type: ignore[attr-defined]
            < expected_size
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise AssertionError("producer admission was not signaled")
            ingress._condition.wait(remaining)  # type: ignore[attr-defined]


def _wait_until_queued(ingress: BoundedIngress) -> None:
    _wait_until_queue_size(ingress, 1)


class IngressValueContractTests(unittest.TestCase):
    def test_external_halt_signature_accepts_only_exact_bound_runtime(self):
        signature = inspect.signature(BoundedIngress.close_external_halt)
        self.assertEqual(
            tuple(signature.parameters),
            ("self", "runtime"),
        )
        self.assertEqual(
            signature.parameters["runtime"].annotation,
            "EventRuntime",
        )
        self.assertEqual(
            signature.return_annotation,
            "PersistedEvent",
        )

    def test_capacity_requires_exact_positive_builtin_integer(self):
        for value in (True, False, 0, -1, 1.0, _IntSubclass(1)):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    BoundedIngress(
                        capacity=value,  # type: ignore[arg-type]
                        producer_timeout_seconds=0.1,
                        receipt_timeout_seconds=0.1,
                    )
        BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.1,
            receipt_timeout_seconds=0.1,
        )

    def test_constructor_timeouts_require_exact_positive_finite_floats(self):
        invalid = (
            True,
            1,
            0.0,
            -0.1,
            math.nan,
            math.inf,
            -math.inf,
            _FloatSubclass(0.1),
        )
        for field_name in (
            "producer_timeout_seconds",
            "receipt_timeout_seconds",
        ):
            for value in invalid:
                with self.subTest(field_name=field_name, value=value):
                    values = {
                        "capacity": 1,
                        "producer_timeout_seconds": 0.1,
                        "receipt_timeout_seconds": 0.1,
                    }
                    values[field_name] = value
                    with self.assertRaises((TypeError, ValueError)):
                        BoundedIngress(**values)

    def test_item_requires_exact_safe_identity_sequence_and_capture(self):
        sample = object.__new__(CapturedInput)
        for producer_id in (
            "",
            "contains space",
            "a" * 65,
            1,
        ):
            with self.subTest(producer_id=producer_id):
                with self.assertRaises((TypeError, ValueError)):
                    IngressItem(
                        producer_id=producer_id,  # type: ignore[arg-type]
                        producer_sequence=0,
                        captured=sample,
                    )
        for producer_sequence in (
            True,
            -1,
            0.0,
            _IntSubclass(0),
        ):
            with self.subTest(producer_sequence=producer_sequence):
                with self.assertRaises((TypeError, ValueError)):
                    IngressItem(
                        producer_id="producer",
                        producer_sequence=producer_sequence,  # type: ignore[arg-type]
                        captured=sample,
                    )
        with self.assertRaises(TypeError):
            IngressItem(
                producer_id="producer",
                producer_sequence=0,
                captured=object(),  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            IngressItem(
                producer_id="producer",
                producer_sequence=0,
                captured=object.__new__(_CapturedSubclass),
            )

    def test_receipt_requires_exact_identity_positive_raw_and_canonical_hash(self):
        for values in (
            {
                "producer_id": "",
                "producer_sequence": 0,
                "raw_ingest_seq": 1,
                "raw_record_sha256": "0" * 64,
            },
            {
                "producer_id": "producer",
                "producer_sequence": True,
                "raw_ingest_seq": 1,
                "raw_record_sha256": "0" * 64,
            },
            {
                "producer_id": "producer",
                "producer_sequence": 0,
                "raw_ingest_seq": 0,
                "raw_record_sha256": "0" * 64,
            },
            {
                "producer_id": "producer",
                "producer_sequence": 0,
                "raw_ingest_seq": True,
                "raw_record_sha256": "0" * 64,
            },
            {
                "producer_id": "producer",
                "producer_sequence": 0,
                "raw_ingest_seq": 1,
                "raw_record_sha256": "A" * 64,
            },
            {
                "producer_id": "producer",
                "producer_sequence": 0,
                "raw_ingest_seq": 1,
                "raw_record_sha256": "0" * 63,
            },
        ):
            with self.subTest(values=values):
                with self.assertRaises((TypeError, ValueError)):
                    DurableIngressReceipt(**values)

    def test_enqueue_rejects_nonexact_item_before_queue_state(self):
        ingress = BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.1,
            receipt_timeout_seconds=0.1,
        )
        with self.assertRaises(TypeError):
            ingress.enqueue(object())  # type: ignore[arg-type]
        self.assertEqual(ingress._queue.qsize(), 0)  # type: ignore[attr-defined]

    def test_enqueue_revalidates_forged_exact_item_before_any_state(self):
        ingress = BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.1,
            receipt_timeout_seconds=0.1,
        )
        valid_capture = object.__new__(CapturedInput)
        forged_items: list[IngressItem] = [object.__new__(IngressItem)]
        for field_name, value in (
            ("producer_id", ""),
            ("producer_id", 1),
            ("producer_sequence", -1),
            ("producer_sequence", True),
            ("captured", object()),
            ("captured", object.__new__(_CapturedSubclass)),
        ):
            item = object.__new__(IngressItem)
            object.__setattr__(item, "producer_id", "producer")
            object.__setattr__(item, "producer_sequence", 0)
            object.__setattr__(item, "captured", valid_capture)
            object.__setattr__(item, field_name, value)
            forged_items.append(item)

        for item in forged_items:
            with self.subTest(item_fields=tuple(
                name
                for name in ("producer_id", "producer_sequence", "captured")
                if hasattr(item, name)
            )):
                with self.assertRaises((TypeError, ValueError)):
                    ingress.enqueue(item)
                self.assertEqual(
                    ingress._queue.qsize(),  # type: ignore[attr-defined]
                    0,
                )
                self.assertIsNone(ingress.halt_reason)
                self.assertIsNone(
                    ingress._runtime,  # type: ignore[attr-defined]
                )

    def test_admission_deadline_includes_time_before_condition_acquisition(self):
        ingress = BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.03,
            receipt_timeout_seconds=0.1,
        )
        item = IngressItem(
            producer_id="producer",
            producer_sequence=0,
            captured=object.__new__(CapturedInput),
        )
        with mock.patch(
            "tennis_v1.ingress.time.monotonic",
            side_effect=(100.0, 100.04),
        ):
            with self.assertRaises(IngressBackpressureHalt):
                ingress.enqueue(item)
        self.assertEqual(ingress.halt_reason, "backpressure")
        self.assertEqual(ingress._queue.qsize(), 0)  # type: ignore[attr-defined]

    def test_drain_timeout_rejects_every_nonexact_or_nonpositive_value(self):
        ingress = BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.1,
            receipt_timeout_seconds=0.1,
        )
        for value in (
            True,
            1,
            0.0,
            -0.1,
            math.nan,
            math.inf,
            -math.inf,
            _FloatSubclass(0.1),
        ):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    ingress.drain_one(
                        object(),  # type: ignore[arg-type]
                        timeout_seconds=value,  # type: ignore[arg-type]
                    )


class IngressRuntimeTests(RuntimeFixture):
    def setUp(self) -> None:
        super().setUp()
        self.runtime_instance = self.runtime()

    def make_ingress(
        self,
        *,
        capacity: int = 4,
        producer_timeout_seconds: float = 0.2,
        receipt_timeout_seconds: float = 1.0,
    ) -> BoundedIngress:
        return BoundedIngress(
            capacity=capacity,
            producer_timeout_seconds=producer_timeout_seconds,
            receipt_timeout_seconds=receipt_timeout_seconds,
        )

    def item(self, sequence: int) -> IngressItem:
        return IngressItem(
            producer_id="producer",
            producer_sequence=sequence,
            captured=captured(self.authorizer),
        )

    def terminal_payload(self, terminal: PersistedEvent) -> dict[str, object]:
        self.assertEqual(terminal.record_kind, RecordKind.CONTROL)
        return json.loads(terminal.payload)

    def wal_path(self) -> Path:
        return (
            self.fixture.config.state_root
            / "sessions"
            / f"{self.session_manifest.session_id}.wal"
        )

    def diagnostic_records(self) -> tuple[tuple[PersistedEvent, ...], object]:
        capability = self.coordinator.issue_read_capability(
            persistence_authorizer=self.authorizer
        )
        with JournalReader.open(read_capability=capability) as reader:
            records = tuple(reader.iter_records(diagnostic_prefix=True))
            summary = reader.scan()
        return records, summary

    def admit_many(
        self,
        ingress: BoundedIngress,
        count: int,
        *,
        receipt_timeout_gate: threading.Event | None = None,
    ) -> tuple[
        list[tuple[threading.Thread, _ProducerResult]],
        list[object],
    ]:
        nodes: list[object] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        def capture_node(node, *args, **kwargs):
            if receipt_timeout_gate is not None:
                real_completion = threading.Event()

                class GatedTimeoutCompletion:
                    def set(self) -> None:
                        real_completion.set()

                    def wait(self, _timeout=None) -> bool:
                        if not receipt_timeout_gate.wait(2.0):
                            raise AssertionError(
                                "receipt timeout gate was not released"
                            )
                        return real_completion.is_set()

                node.completion = GatedTimeoutCompletion()
            result = original_put(node, *args, **kwargs)
            nodes.append(node)
            return result

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=capture_node,
        ):
            pairs = [
                _start_producer(ingress, self.item(index))
                for index in range(count)
            ]
            _wait_until_queue_size(ingress, count)
        self.assertEqual(len(nodes), count)
        return pairs, nodes

    def assert_failed_waiters_are_sanitized(
        self,
        pairs: list[tuple[threading.Thread, _ProducerResult]],
        *,
        secret: str,
    ) -> None:
        for thread, result in pairs:
            _join(thread, result)
            self.assertIsNone(result.receipt)
            self.assertIs(type(result.error), IngressClosed)
            self.assertEqual(
                str(result.error),
                "ingress_runtime_unavailable",
            )
            self.assertNotIn(secret, str(result.error))

    def assert_secret_absent(
        self,
        *,
        secret: str,
        ingress: BoundedIngress,
        nodes: list[object],
    ) -> None:
        encoded = secret.encode("ascii")
        self.assertNotIn(encoded, self.wal_path().read_bytes())
        self.assertNotIn(secret, repr(ingress))
        for node in nodes:
            self.assertNotIn(secret, repr(node))

    def assert_ingress_locks_available(
        self,
        ingress: BoundedIngress,
        nodes: list[object],
    ) -> None:
        admission_acquired = (
            ingress._admission_lock.acquire(  # type: ignore[attr-defined]
                blocking=False
            )
        )
        self.assertTrue(admission_acquired)
        ingress._admission_lock.release()  # type: ignore[attr-defined]
        for node in nodes:
            completion_acquired = node.completion_lock.acquire(
                blocking=False
            )
            self.assertTrue(completion_acquired)
            node.completion_lock.release()

    def test_enqueue_returns_only_after_matching_raw_fsync_receipt(self):
        ingress = self.make_ingress()
        thread, result = _start_producer(ingress, self.item(7))
        raw = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(thread, result)
        self.assertIsInstance(raw, PersistedEvent)
        self.assertEqual(raw.record_kind, RecordKind.RAW)
        self.assertIsNone(result.error)
        self.assertEqual(
            result.receipt,
            DurableIngressReceipt(
                producer_id="producer",
                producer_sequence=7,
                raw_ingest_seq=raw.ingest_seq,
                raw_record_sha256=canonical_record_sha256(raw),
            ),
        )
        self.assertIn(raw.ingest_seq, self._durable_sequences)

    def test_exact_item_with_deep_forged_capture_remains_task4_fatal(self):
        ingress = self.make_ingress()
        forged_capture = captured(self.authorizer)
        object.__setattr__(
            forged_capture,
            "session_id",
            "forged-session",
        )
        item = IngressItem(
            producer_id="producer",
            producer_sequence=1,
            captured=forged_capture,
        )
        producer_thread, producer = _start_producer(ingress, item)
        _wait_until_queued(ingress)

        with self.assertRaises(CaptureValidationError):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )

        _join(producer_thread, producer)
        self.assertIs(type(producer.error), IngressClosed)
        self.assertIsNone(producer.receipt)
        self.assertEqual(
            self._close_reasons,
            [(False, "capture_contract_violation")],
        )

    def test_each_enqueued_producer_item_is_persisted_exactly_once(self):
        ingress = self.make_ingress()
        thread, result = _start_producer(ingress, self.item(1))
        raw = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(thread, result)
        self.assertIsNotNone(result.receipt)
        self.assertIsNone(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        ingress.close_inputs()
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.01,
        )
        records, summary = self.diagnostic_records()
        self.assertEqual(
            sum(record.record_kind is RecordKind.RAW for record in records),
            1,
        )
        self.assertEqual(summary.raw_count, 1)
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "operator_stop",
        )
        self.assertEqual(raw.ingest_seq, result.receipt.raw_ingest_seq)

    def test_many_barrier_released_producers_create_one_gap_free_durable_order(self):
        count = 8
        ingress = self.make_ingress(
            capacity=count,
            receipt_timeout_seconds=30.0,
        )
        barrier = threading.Barrier(count + 1)
        admission_turns = [threading.Event() for _ in range(count)]
        admission_turns[0].set()
        admission_order: list[int] = []
        results = [_ProducerResult() for _ in range(count)]
        threads: list[threading.Thread] = []
        items = [
            IngressItem(
                producer_id="producer",
                producer_sequence=index,
                captured=captured(
                    self.authorizer,
                    source_wall_ns=count - index,
                    source_generated_ns=count - index,
                    provider_sequence=f"A-{index}",
                ),
            )
            for index in range(count)
        ]
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        def record_admission(node, *args, **kwargs):
            result = original_put(node, *args, **kwargs)
            index = node.item.producer_sequence
            admission_order.append(index)
            if index + 1 < count:
                admission_turns[index + 1].set()
            return result

        def producer(index: int) -> None:
            barrier.wait()
            if not admission_turns[index].wait(2.0):
                results[index].error = AssertionError(
                    "admission turn was not released"
                )
                results[index].done.set()
                return
            try:
                results[index].receipt = ingress.enqueue(items[index])
            except BaseException as error:
                results[index].error = error
            finally:
                results[index].done.set()

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=record_admission,
        ):
            for index in range(count):
                thread = threading.Thread(target=producer, args=(index,))
                thread.start()
                threads.append(thread)
            barrier.wait()
            _wait_until_queue_size(ingress, count)
        self.assertEqual(admission_order, list(range(count)))

        raws = tuple(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.5,
            )
            for _ in admission_order
        )
        for thread, result in zip(threads, results, strict=True):
            _join(thread, result)
        receipts = [result.receipt for result in results]
        self.assertTrue(all(receipt is not None for receipt in receipts))
        self.assertTrue(all(result.error is None for result in results))
        raw_sequences = tuple(raw.ingest_seq for raw in raws)
        receipt_sequences_in_admission_order = tuple(
            results[index].receipt.raw_ingest_seq
            for index in admission_order
        )
        self.assertEqual(
            receipt_sequences_in_admission_order,
            raw_sequences,
        )
        self.assertEqual(len(set(raw_sequences)), count)
        self.assertEqual(
            tuple(raw.provider_sequence for raw in raws),
            tuple(f"A-{index}" for index in admission_order),
        )
        self.assertEqual(
            tuple(raw.source_wall_ns for raw in raws),
            tuple(range(count, 0, -1)),
        )
        ingress.close_inputs()
        ingress.drain_one(self.runtime_instance, timeout_seconds=0.01)
        records, summary = self.diagnostic_records()
        self.assertEqual(
            tuple(record.ingest_seq for record in records),
            tuple(range(1, len(records) + 1)),
        )
        self.assertEqual(summary.raw_count, count)
        physical_data = tuple(
            record
            for record in records
            if record.record_kind in (RecordKind.RAW, RecordKind.DERIVED)
        )
        physical_raws = tuple(
            record
            for record in physical_data
            if record.record_kind is RecordKind.RAW
        )
        self.assertEqual(
            tuple(record.provider_sequence for record in physical_raws),
            tuple(f"A-{index}" for index in admission_order),
        )
        self.assertEqual(
            tuple(record.source_wall_ns for record in physical_raws),
            tuple(range(count, 0, -1)),
        )
        cursor = 0
        for raw in physical_raws:
            self.assertIs(physical_data[cursor], raw)
            cursor += 1
            while (
                cursor < len(physical_data)
                and physical_data[cursor].record_kind is RecordKind.DERIVED
            ):
                self.assertEqual(
                    physical_data[cursor].parent_ingest_seq,
                    raw.ingest_seq,
                )
                cursor += 1
        self.assertEqual(cursor, len(physical_data))
        replay = scan_diagnostic_prefix(
            expected_session_manifest_sha256=session_manifest_sha256(
                self.session_manifest
            ),
            persistence_authorizer=self.authorizer,
            coordinator=self.coordinator,
        )
        self.assertTrue(replay.exact_replay)
        self.assertEqual(replay.raw_count, count)
        self.assertEqual(replay.state.raw_count, count)

    def test_queue_full_blocks_only_to_bounded_timeout_then_requests_global_halt(self):
        ingress = self.make_ingress(
            capacity=1,
            producer_timeout_seconds=0.03,
        )
        first_thread, first = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        second_thread, second = _start_producer(ingress, self.item(2))
        _join(second_thread, second)
        self.assertIsInstance(second.error, IngressBackpressureHalt)
        self.assertEqual(ingress.halt_reason, "backpressure")
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(first_thread, first)
        self.assertIsNotNone(first.receipt)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "ingress_backpressure",
        )
        with self.assertRaises(IngressClosed):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        self.assertNotIn(
            "_latch_global_halt",
            Path(
                __import__("tennis_v1.ingress").ingress.__file__
            ).read_text(encoding="utf-8"),
        )

    def test_receipt_wait_is_bounded_and_owner_stall_requests_global_halt(self):
        ingress = self.make_ingress(receipt_timeout_seconds=0.03)
        thread, result = _start_producer(ingress, self.item(1))
        _join(thread, result)
        self.assertIsInstance(result.error, IngressOwnerUnresponsive)
        self.assertIsNone(result.receipt)
        self.assertEqual(ingress.halt_reason, "owner_unresponsive")
        with self.assertRaisesRegex(
            IngressClosed,
            "ingress_runtime_unavailable",
        ):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        self.assertEqual(self._close_reasons, [])

    def test_receipt_timeout_after_normal_close_overrides_clean_finalization(self):
        ingress = self.make_ingress(receipt_timeout_seconds=0.03)
        thread, result = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        ingress.close_inputs()
        _join(thread, result)
        self.assertIsInstance(result.error, IngressOwnerUnresponsive)
        self.assertEqual(ingress.halt_reason, "owner_unresponsive")
        with self.assertRaisesRegex(
            IngressClosed,
            "ingress_runtime_unavailable",
        ):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        self.assertEqual(self._close_reasons, [])

    def test_timeout_node_state_and_owner_fault_commit_atomically_before_close(
        self,
    ):
        ingress = self.make_ingress(receipt_timeout_seconds=0.03)
        timeout_node_released = threading.Event()
        owner_waiting_for_node = threading.Event()
        allow_producer_continue = threading.Event()
        owner_thread = threading.current_thread()
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        class PausingCompletionLock:
            def __init__(self) -> None:
                self._lock = threading.Lock()

            def __enter__(self):
                if threading.current_thread() is owner_thread:
                    owner_waiting_for_node.set()
                self._lock.acquire()
                return self

            def __exit__(self, *_):
                self._lock.release()
                if threading.current_thread() is not owner_thread:
                    timeout_node_released.set()
                    if not allow_producer_continue.wait(2.0):
                        raise AssertionError(
                            "producer timeout commit was not released"
                        )
                return False

            def acquire(self, *args, **kwargs):
                return self._lock.acquire(*args, **kwargs)

            def release(self):
                return self._lock.release()

        def install_lock(node, *args, **kwargs):
            node.completion_lock = PausingCompletionLock()
            return original_put(node, *args, **kwargs)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=install_lock,
        ):
            producer_thread, producer = _start_producer(
                ingress,
                self.item(1),
            )
            _wait_until_queued(ingress)
        ingress.close_inputs()
        self.assertTrue(timeout_node_released.wait(2.0))

        def release_timed_out_producer() -> None:
            if not owner_waiting_for_node.wait(2.0):
                raise AssertionError("owner did not contend on timed-out node")
            allow_producer_continue.set()

        releaser = threading.Thread(target=release_timed_out_producer)
        releaser.start()
        with self.assertRaisesRegex(
            IngressClosed,
            "ingress_runtime_unavailable",
        ):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        releaser.join()
        _join(producer_thread, producer)
        self.assertIsInstance(producer.error, IngressOwnerUnresponsive)
        self.assertEqual(ingress.halt_reason, "owner_unresponsive")
        self.assertEqual(self._close_reasons, [])

    def test_blocked_idle_check_releases_admission_and_queue_wins_session_end(
        self,
    ):
        ingress = self.make_ingress(
            producer_timeout_seconds=0.03,
            receipt_timeout_seconds=1.0,
        )
        candidate = self.item(1)
        self.fixture.now = self.fixture.request.session_end_utc
        poll_entered = threading.Event()
        release_poll = threading.Event()
        producer_result: list[
            tuple[threading.Thread, _ProducerResult]
        ] = []
        admitted_during_check: list[bool] = []
        original_check = EventRuntime.check_ingress_session_end

        def blocked_check(runtime):
            poll_entered.set()
            if not release_poll.wait(2.0):
                raise AssertionError("blocked poll was not released")
            return original_check(runtime)

        def supervise_timeout() -> None:
            if not poll_entered.wait(2.0):
                release_poll.set()
                return
            pair = _start_producer(ingress, candidate)
            producer_result.append(pair)
            try:
                _wait_until_queued(ingress)
                admitted_during_check.append(True)
            except AssertionError:
                admitted_during_check.append(False)
            release_poll.set()

        supervisor = threading.Thread(target=supervise_timeout)
        supervisor.start()
        with mock.patch.object(
            EventRuntime,
            "check_ingress_session_end",
            blocked_check,
        ):
            with self.assertRaises(ProviderGateError):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
        supervisor.join()
        self.assertEqual(admitted_during_check, [True])
        producer_thread, result = producer_result[0]
        _join(producer_thread, result)
        self.assertIsInstance(result.error, IngressClosed)
        self.assertIsNone(ingress.halt_reason)
        self.assertEqual(
            self._close_reasons,
            [(False, "provider_gate_denied")],
        )

    def test_full_queue_condition_wait_is_bounded_by_one_admission_deadline(
        self,
    ):
        ingress = self.make_ingress(
            capacity=1,
            producer_timeout_seconds=0.03,
            receipt_timeout_seconds=1.0,
        )
        first_thread, first = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        second_thread, second = _start_producer(ingress, self.item(2))
        self.assertTrue(second.done.wait(0.15))
        second_thread.join()
        self.assertIsInstance(second.error, IngressBackpressureHalt)
        self.assertEqual(ingress.halt_reason, "backpressure")
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(first_thread, first)
        self.assertIsNotNone(first.receipt)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "ingress_backpressure",
        )

    def test_ingress_session_check_gate_denial_preserves_task4_halt(self):
        ingress = self.make_ingress()
        self._authorizer_fail["poll"] = ProviderGateError(
            QualificationReason.ACCESS_EXPIRED
        )
        with self.assertRaises(ProviderGateError):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        self.assertEqual(
            self._close_reasons,
            [(False, "provider_gate_denied")],
        )
        with self.assertRaises(IngressClosed):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )

    def test_idle_check_unrelated_retention_halt_writes_one_fixed_terminal(
        self,
    ):
        ingress = self.make_ingress()
        self._coordinator_fail_at = len(self._coordinator_calls) + 1

        def unrelated_halt_is_control_eligible(_instance, *, session_id):
            self.assertEqual(session_id, self.session_manifest.session_id)

        with mock.patch.object(
            RetentionCoordinator,
            "require_control_halt_eligible",
            new=unrelated_halt_is_control_eligible,
        ):
            with self.assertRaises(RetentionGlobalHalt):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.01,
                )

        self.assertEqual(
            self._close_reasons,
            [(False, "retention_global_halt")],
        )
        records, summary = self.diagnostic_records()
        self.assertEqual(summary.issue, ScanIssue.HALTED_TERMINAL)
        self.assertEqual(
            sum(record.event_type == "SESSION_HALT" for record in records),
            1,
        )
        before = self.wal_path().read_bytes()
        for operation in (
            lambda: ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            ),
            lambda: ingress.enqueue(self.item(1)),
        ):
            with self.assertRaises(IngressClosed):
                operation()
        self.assertEqual(self._close_reasons.count(
            (False, "retention_global_halt")
        ), 1)
        self.assertEqual(self.wal_path().read_bytes(), before)

    def test_idle_check_due_ambiguous_and_incapability_halts_stay_unclean(
        self,
    ):
        for failure_kind in ("due", "ambiguous", "in_capability"):
            with self.subTest(failure_kind=failure_kind):
                self.reset_stack()
                self.runtime_instance = self.runtime()
                ingress = self.make_ingress()
                secret = f"SECRET-{failure_kind}"
                control_patch = mock.patch.object(
                    RetentionCoordinator,
                    "require_control_halt_eligible",
                    side_effect=RetentionError(secret),
                )
                context = (
                    control_patch
                    if failure_kind == "ambiguous"
                    else nullcontext()
                )
                if failure_kind in ("due", "ambiguous"):
                    self._coordinator_fail_at = (
                        len(self._coordinator_calls) + 1
                    )
                if failure_kind == "due":
                    self._control_eligible = False
                if failure_kind == "in_capability":
                    self._authorizer_fail["poll"] = (
                        RetentionGlobalHalt(secret)
                    )
                before = self.wal_path().read_bytes()

                with context:
                    with self.assertRaises(RetentionGlobalHalt):
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.01,
                        )

                self.assertEqual(self._close_reasons, [])
                self.assertEqual(self.wal_path().read_bytes(), before)
                with self.assertRaises(IngressClosed):
                    ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.01,
                    )
                self.assertNotIn(
                    secret.encode("ascii"),
                    self.wal_path().read_bytes(),
                )

    def test_blocked_put_cannot_linearize_after_close_or_first_halt(self):
        ingress = self.make_ingress(
            capacity=1,
            producer_timeout_seconds=0.03,
        )
        first_thread, first = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        ingress.close_inputs()
        second_thread, second = _start_producer(ingress, self.item(2))
        _join(second_thread, second)
        self.assertIsInstance(second.error, IngressClosed)
        self.assertEqual(ingress._queue.qsize(), 1)  # type: ignore[attr-defined]
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(first_thread, first)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "operator_stop",
        )
        with self.assertRaises(IngressClosed):
            ingress.enqueue(self.item(3))

    def test_backpressure_timeout_never_drops_and_continues_nothing(self):
        ingress = self.make_ingress(
            capacity=1,
            producer_timeout_seconds=0.03,
        )
        first_thread, first = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        rejected_thread, rejected = _start_producer(
            ingress,
            self.item(2),
        )
        _join(rejected_thread, rejected)
        with self.assertRaises(IngressBackpressureHalt):
            ingress.enqueue(self.item(3))
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(first_thread, first)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "ingress_backpressure",
        )
        records, summary = self.diagnostic_records()
        self.assertEqual(summary.raw_count, 1)
        self.assertEqual(
            sum(record.record_kind is RecordKind.RAW for record in records),
            1,
        )

    def test_backpressure_counts_provisional_durable_rejected_and_terminal_exactly(self):
        ingress = self.make_ingress(
            capacity=1,
            producer_timeout_seconds=0.03,
        )
        admitted_thread, admitted = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        rejected_thread, rejected = _start_producer(
            ingress,
            self.item(2),
        )
        _join(rejected_thread, rejected)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(admitted_thread, admitted)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        records, summary = self.diagnostic_records()
        self.assertIsNotNone(admitted.receipt)
        self.assertIsInstance(rejected.error, IngressBackpressureHalt)
        self.assertEqual(summary.raw_count, 1)
        self.assertEqual(
            sum(record.event_type == "SESSION_HALT" for record in records),
            1,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["record_count_before_terminal"],
            len(records) - 1,
        )

    def test_only_owner_thread_may_drain_or_close_runtime(self):
        ingress = self.make_ingress()
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        observed: list[BaseException] = []

        def wrong_owner() -> None:
            try:
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.01,
                )
            except BaseException as error:
                observed.append(error)

        worker = threading.Thread(target=wrong_owner)
        worker.start()
        worker.join()
        self.assertIsInstance(observed[0], WrongOwnerThread)
        self.assertEqual(ingress._queue.qsize(), 1)  # type: ignore[attr-defined]
        ingress.drain_one(self.runtime_instance, timeout_seconds=0.1)
        _join(producer_thread, producer)
        self.assertIsNotNone(producer.receipt)

    def test_close_inputs_rejects_late_enqueue_and_drains_existing_items(self):
        ingress = self.make_ingress()
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        ingress.close_inputs()
        ingress.close_inputs()
        with self.assertRaises(IngressClosed):
            ingress.enqueue(self.item(2))
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(producer_thread, producer)
        self.assertIsNotNone(producer.receipt)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "operator_stop",
        )
        self.assertEqual(self._close_reasons, [(True, "operator_stop")])

    def test_capacity_three_normal_and_each_pure_fault_drain_before_terminal(
        self,
    ):
        expected_terminals = {
            "normal": "operator_stop",
            "backpressure": "ingress_backpressure",
            "owner_unresponsive": "ingress_owner_unresponsive",
        }
        for mode, expected_terminal in expected_terminals.items():
            with self.subTest(mode=mode):
                self.reset_stack()
                self.runtime_instance = self.runtime()
                ingress = self.make_ingress(
                    capacity=3,
                    producer_timeout_seconds=0.03,
                    receipt_timeout_seconds=1.0,
                )
                receipt_timeout_gate = (
                    threading.Event()
                    if mode == "owner_unresponsive"
                    else None
                )
                pairs, _ = self.admit_many(
                    ingress,
                    3,
                    receipt_timeout_gate=receipt_timeout_gate,
                )
                rejected: _ProducerResult | None = None
                if mode == "normal":
                    ingress.close_inputs()
                elif mode == "backpressure":
                    rejected_thread, rejected = _start_producer(
                        ingress,
                        self.item(4),
                    )
                    _join(rejected_thread, rejected)
                    self.assertIsInstance(
                        rejected.error,
                        IngressBackpressureHalt,
                    )
                else:
                    assert receipt_timeout_gate is not None
                    receipt_timeout_gate.set()
                    for thread, result in pairs:
                        _join(thread, result)
                        self.assertIsInstance(
                            result.error,
                            IngressOwnerUnresponsive,
                        )

                if mode == "owner_unresponsive":
                    before = self.wal_path().read_bytes()
                    with self.assertRaisesRegex(
                        IngressClosed,
                        "ingress_runtime_unavailable",
                    ):
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                    self.assertEqual(self._close_reasons, [])
                    self.assertEqual(self.wal_path().read_bytes(), before)
                else:
                    outcomes = tuple(
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                        for _ in range(4)
                    )
                    self.assertTrue(all(
                        outcome.record_kind is RecordKind.RAW
                        for outcome in outcomes[:3]
                    ))
                    terminal = outcomes[-1]
                    self.assertEqual(
                        self.terminal_payload(terminal)["reason"],
                        expected_terminal,
                    )

                if mode != "owner_unresponsive":
                    for thread, result in pairs:
                        _join(thread, result)
                        self.assertIsNone(result.error)
                        self.assertIsNotNone(result.receipt)
                    receipt_sequences = {
                        result.receipt.raw_ingest_seq
                        for _, result in pairs
                    }
                    self.assertEqual(len(receipt_sequences), 3)
                else:
                    self.assertTrue(all(
                        result.receipt is None
                        for _, result in pairs
                    ))
                if rejected is not None:
                    self.assertIsNone(rejected.receipt)

                if mode == "owner_unresponsive":
                    self.assertEqual(
                        ingress._queue.qsize(),  # type: ignore[attr-defined]
                        0,
                    )
                    continue

                records, summary = self.diagnostic_records()
                raws = tuple(
                    record
                    for record in records
                    if record.record_kind is RecordKind.RAW
                )
                terminals = tuple(
                    record
                    for record in records
                    if record.event_type == "SESSION_HALT"
                )
                self.assertEqual(summary.raw_count, 3)
                self.assertEqual(len(raws), 3)
                self.assertEqual(len({
                    record.ingest_seq for record in raws
                }), 3)
                self.assertEqual(len(terminals), 1)
                self.assertIs(records[-1], terminals[0])
                self.assertEqual(
                    json.loads(terminals[0].payload)["reason"],
                    expected_terminal,
                )

    def test_close_fault_timeout_has_one_immutable_winner(self):
        ingress = self.make_ingress(
            capacity=1,
            producer_timeout_seconds=0.03,
        )
        first_thread, first = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        barrier = threading.Barrier(3)
        second_result = _ProducerResult()

        def close() -> None:
            barrier.wait()
            ingress.close_inputs()

        def second_enqueue() -> None:
            barrier.wait()
            try:
                second_result.receipt = ingress.enqueue(self.item(2))
            except BaseException as error:
                second_result.error = error
            finally:
                second_result.done.set()

        closer = threading.Thread(target=close)
        second = threading.Thread(target=second_enqueue)
        closer.start()
        second.start()
        barrier.wait()
        closer.join()
        _join(second, second_result)
        first_reason = ingress.halt_reason
        ingress.close_inputs()
        self.assertEqual(ingress.halt_reason, first_reason)
        self.assertIsInstance(
            second_result.error,
            (IngressClosed, IngressBackpressureHalt),
        )
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(first_thread, first)
        self.assertEqual(terminal.record_kind, RecordKind.RAW)
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        expected = (
            "ingress_backpressure"
            if first_reason == "backpressure"
            else "operator_stop"
        )
        self.assertEqual(self.terminal_payload(terminal)["reason"], expected)

    def test_completion_wins_immediately_before_timeout_lock_reacquire(self):
        original_factory = threading.Event
        wait_entered = original_factory()
        receipt_published = original_factory()
        release_timeout = original_factory()

        class ApparentTimeoutEvent:
            def __init__(self) -> None:
                self._real = original_factory()

            def set(self) -> None:
                self._real.set()
                receipt_published.set()

            def wait(self, timeout=None):
                wait_entered.set()
                if not release_timeout.wait(2.0):
                    raise AssertionError("timeout race was not released")
                return False

        raced = self.make_ingress(receipt_timeout_seconds=0.2)
        original_put = raced._queue.put  # type: ignore[attr-defined]

        def install_apparent_timeout(node, *args, **kwargs):
            node.completion = ApparentTimeoutEvent()
            return original_put(node, *args, **kwargs)

        def release_after_publication() -> None:
            if not wait_entered.wait(2.0):
                raise AssertionError("producer did not enter receipt wait")
            if not receipt_published.wait(2.0):
                raise AssertionError("owner did not publish receipt")
            release_timeout.set()

        with mock.patch.object(
            raced._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=install_apparent_timeout,
        ):
            thread, result = _start_producer(raced, self.item(1))
            self.assertTrue(wait_entered.wait(2.0))
            supervisor = threading.Thread(target=release_after_publication)
            supervisor.start()
            raw = raced.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            supervisor.join()
            _join(thread, result)
        self.assertIsNone(result.error)
        self.assertEqual(result.receipt.raw_ingest_seq, raw.ingest_seq)
        self.assertIsNone(raced.halt_reason)

    def test_timeout_wins_before_receipt_publication(self):
        ingress = self.make_ingress(receipt_timeout_seconds=0.03)
        nodes: list[object] = []
        ingest_calls = 0
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_ingest = EventRuntime.ingest

        def capture_node(node, *args, **kwargs):
            nodes.append(node)
            return original_put(node, *args, **kwargs)

        def observed_ingest(runtime, item):
            nonlocal ingest_calls
            ingest_calls += 1
            return original_ingest(runtime, item)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=capture_node,
        ):
            thread, result = _start_producer(ingress, self.item(1))
            _join(thread, result)
        self.assertIsInstance(result.error, IngressOwnerUnresponsive)
        before = self.wal_path().read_bytes()
        with mock.patch.object(EventRuntime, "ingest", new=observed_ingest):
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.1,
                )
        self.assertEqual(ingest_calls, 0)
        self.assertIsNone(result.receipt)
        node = nodes[0]
        self.assertEqual(node.state, "ABORTED")
        self.assertIsNone(node.receipt)
        self.assertEqual(self._close_reasons, [])
        self.assertEqual(self.wal_path().read_bytes(), before)

    def test_runtime_failure_before_raw_durability_fans_out_sanitized_errors(self):
        ingress = self.make_ingress(capacity=3)
        threads_results = [
            _start_producer(ingress, self.item(index))
            for index in range(3)
        ]
        _wait_until_queue_size(ingress, 3)
        self.assertEqual(ingress._queue.qsize(), 3)  # type: ignore[attr-defined]
        secret = "SECRET-runtime-failure"
        with mock.patch.object(
            EventRuntime,
            "ingest",
            side_effect=RuntimeError(secret),
        ):
            with self.assertRaisesRegex(RuntimeError, secret):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.1,
                )
        for thread, result in threads_results:
            _join(thread, result)
            self.assertIsInstance(result.error, IngressClosed)
            self.assertNotIn(secret, str(result.error))
            self.assertIsNone(result.receipt)
        self.assertNotIn(secret, repr(ingress))

    def test_reducer_failure_after_raw_fsync_fans_out_without_second_terminal(
        self,
    ):
        ingress = self.make_ingress(capacity=3)
        pairs, nodes = self.admit_many(ingress, 3)
        secret = "SECRET-post-raw-reducer"
        reducer_entered = threading.Event()

        def fail_after_raw_fsync(_state, raw):
            self.assertIn(raw.ingest_seq, self._durable_sequences)
            reducer_entered.set()
            raise RuntimeError(secret)

        with mock.patch(
            "tennis_v1.sequencer.reduce_event",
            side_effect=fail_after_raw_fsync,
        ):
            with self.assertRaisesRegex(RuntimeError, secret):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.1,
                )

        self.assertTrue(reducer_entered.is_set())
        self.assert_failed_waiters_are_sanitized(
            pairs,
            secret=secret,
        )
        self.assertEqual(
            self._close_reasons,
            [(False, "reducer_exception")],
        )
        records, summary = self.diagnostic_records()
        self.assertEqual(summary.raw_count, 1)
        self.assertEqual(summary.derived_count, 0)
        self.assertEqual(summary.issue, ScanIssue.HALTED_TERMINAL)
        terminals = tuple(
            record
            for record in records
            if record.event_type == "SESSION_HALT"
        )
        self.assertEqual(len(terminals), 1)
        self.assertEqual(
            json.loads(terminals[0].payload)["reason"],
            "reducer_exception",
        )
        self.assertNotIn(secret.encode("ascii"), terminals[0].payload)
        self.assert_secret_absent(
            secret=secret,
            ingress=ingress,
            nodes=nodes,
        )
        before = self.wal_path().read_bytes()
        with self.assertRaises(IngressClosed):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        with self.assertRaises(IngressClosed):
            ingress.enqueue(self.item(9))
        self.assertEqual(self._close_reasons.count(
            (False, "reducer_exception")
        ), 1)
        self.assertEqual(self.wal_path().read_bytes(), before)

    def test_raw_writer_poison_fans_out_and_never_reuses_uncertain_sequence(
        self,
    ):
        ingress = self.make_ingress(capacity=3)
        pairs, nodes = self.admit_many(ingress, 3)
        secret = "SECRET-raw-writer-poison"
        write_attempted = threading.Event()
        original_write = RetentionCoordinator._write_capability_bytes
        before = self.wal_path().read_bytes()

        def uncertain_write(instance, capability, frame):
            if instance is self.coordinator:
                write_attempted.set()
                raise OSError(secret)
            return original_write(instance, capability, frame)

        with mock.patch.object(
            RetentionCoordinator,
            "_write_capability_bytes",
            new=uncertain_write,
        ):
            with self.assertRaises(JournalDurabilityError):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.1,
                )

        self.assertTrue(write_attempted.is_set())
        self.assertTrue(self.writer.poisoned)
        self.assert_failed_waiters_are_sanitized(
            pairs,
            secret=secret,
        )
        self.assertEqual(self._close_reasons, [])
        self.assertEqual(self.wal_path().read_bytes(), before)
        attempted = tuple(self._attempted_sequences)
        self.assertEqual(len(attempted), 1)
        self.assert_secret_absent(
            secret=secret,
            ingress=ingress,
            nodes=nodes,
        )
        with self.assertRaises(IngressClosed):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        with self.assertRaises(IngressClosed):
            ingress.enqueue(self.item(9))
        self.assertEqual(tuple(self._attempted_sequences), attempted)
        self.assertEqual(self.wal_path().read_bytes(), before)

    def test_ingress_fault_terminal_uncertainty_never_retries_or_leaks(
        self,
    ):
        ingress = self.make_ingress(
            capacity=2,
            producer_timeout_seconds=0.03,
        )
        pairs, nodes = self.admit_many(ingress, 2)
        rejected_thread, rejected = _start_producer(
            ingress,
            self.item(3),
        )
        _join(rejected_thread, rejected)
        self.assertIsInstance(rejected.error, IngressBackpressureHalt)
        secret = "SECRET-ingress-terminal-uncertain"
        terminal_attempted = threading.Event()
        terminal_attempts = 0
        original_close = JournalWriter.close_halted

        def uncertain_terminal(instance, *, reason, **witnesses):
            nonlocal terminal_attempts
            if instance is self.writer:
                self.assertEqual(reason, "ingress_backpressure")
                terminal_attempts += 1
                terminal_attempted.set()
                raise JournalDurabilityError(secret)
            return original_close(
                instance,
                reason=reason,
                **witnesses,
            )

        with mock.patch.object(
            JournalWriter,
            "close_halted",
            new=uncertain_terminal,
        ):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            with self.assertRaises(JournalDurabilityError):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.1,
                )
            self.assertTrue(terminal_attempted.is_set())
            for thread, result in pairs:
                _join(thread, result)
                self.assertIsNone(result.error)
                self.assertIsNotNone(result.receipt)
            before = self.wal_path().read_bytes()
            with self.assertRaises(IngressClosed):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.01,
                )
            with self.assertRaises(IngressClosed):
                ingress.enqueue(self.item(9))

        self.assertEqual(terminal_attempts, 1)
        self.assertEqual(self._close_reasons, [])
        self.assertNotIn(secret, str(rejected.error))
        self.assert_secret_absent(
            secret=secret,
            ingress=ingress,
            nodes=nodes,
        )
        self.assertEqual(self.wal_path().read_bytes(), before)

    def test_clean_terminal_write_ack_and_mark_uncertainty_do_not_retry(
        self,
    ):
        for failure_mode in ("write", "ack", "mark"):
            with self.subTest(failure_mode=failure_mode):
                self.reset_stack()
                self.runtime_instance = self.runtime()
                ingress = self.make_ingress(capacity=2)
                pairs, nodes = self.admit_many(ingress, 2)
                ingress.close_inputs()
                secret = f"SECRET-clean-{failure_mode}"
                terminal_attempted = threading.Event()
                attempts = 0
                original_clean = JournalWriter.close_clean
                original_mark = RetentionCoordinator.mark_clean_terminal

                def fail_clean(instance, *, reason, **witnesses):
                    nonlocal attempts
                    if instance is self.writer:
                        self.assertEqual(reason, "operator_stop")
                        attempts += 1
                        terminal_attempted.set()
                        raise JournalDurabilityError(secret)
                    return original_clean(
                        instance,
                        reason=reason,
                        **witnesses,
                    )

                def fail_ack(*, write_capability):
                    nonlocal attempts
                    self.assertIsNotNone(write_capability)
                    attempts += 1
                    terminal_attempted.set()
                    raise RetentionError(secret)

                def fail_mark(instance, *, session_id):
                    nonlocal attempts
                    if instance is self.coordinator:
                        self.assertEqual(
                            session_id,
                            self.session_manifest.session_id,
                        )
                        attempts += 1
                        terminal_attempted.set()
                        raise RetentionError(secret)
                    return original_mark(
                        instance,
                        session_id=session_id,
                    )

                if failure_mode == "write":
                    patcher = mock.patch.object(
                        JournalWriter,
                        "close_clean",
                        new=fail_clean,
                    )
                    expected_error = JournalDurabilityError
                elif failure_mode == "ack":
                    patcher = mock.patch(
                        "tennis_v1.wal._ack_provider_wal_clean_terminal",
                        side_effect=fail_ack,
                    )
                    expected_error = JournalDurabilityError
                else:
                    patcher = mock.patch.object(
                        RetentionCoordinator,
                        "mark_clean_terminal",
                        new=fail_mark,
                    )
                    expected_error = RetentionError

                with patcher:
                    ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.1,
                    )
                    ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.1,
                    )
                    with self.assertRaises(expected_error):
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                    self.assertTrue(terminal_attempted.is_set())
                    for thread, result in pairs:
                        _join(thread, result)
                        self.assertIsNone(result.error)
                        self.assertIsNotNone(result.receipt)
                    before = self.wal_path().read_bytes()
                    with self.assertRaises(IngressClosed):
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.01,
                        )
                    with self.assertRaises(IngressClosed):
                        ingress.enqueue(self.item(9))

                self.assertEqual(attempts, 1)
                self.assert_secret_absent(
                    secret=secret,
                    ingress=ingress,
                    nodes=nodes,
                )
                self.assertEqual(self.wal_path().read_bytes(), before)
                expected_closes = (
                    [(True, "operator_stop")]
                    if failure_mode == "mark"
                    else []
                )
                self.assertEqual(self._close_reasons, expected_closes)

    def test_raw_fsync_blocked_reducer_timeout_is_durable_unacknowledged(self):
        ingress = self.make_ingress(receipt_timeout_seconds=0.03)
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        raw_fsynced = threading.Event()
        release_reducer = threading.Event()
        producer_timed_out = threading.Event()
        original_reduce = __import__(
            "tennis_v1.sequencer",
            fromlist=["reduce_event"],
        ).reduce_event

        def blocked_reduce(state, raw):
            self.assertIn(raw.ingest_seq, self._durable_sequences)
            raw_fsynced.set()
            if not release_reducer.wait(2.0):
                raise AssertionError("reducer release not signaled")
            return original_reduce(state, raw)

        def release_after_timeout() -> None:
            if not raw_fsynced.wait(2.0):
                return
            if not producer.done.wait(2.0):
                return
            producer_timed_out.set()
            release_reducer.set()

        helper = threading.Thread(target=release_after_timeout)
        helper.start()
        with mock.patch(
            "tennis_v1.sequencer.reduce_event",
            side_effect=blocked_reduce,
        ):
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.1,
                )
        helper.join()
        _join(producer_thread, producer)
        self.assertTrue(producer_timed_out.is_set())
        self.assertIsInstance(producer.error, IngressOwnerUnresponsive)
        self.assertIsNone(producer.receipt)
        self.assertEqual(self._close_reasons, [])

    def test_wrong_thread_drain_checks_before_dequeue(self):
        ingress = self.make_ingress()
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        size_before = ingress._queue.qsize()  # type: ignore[attr-defined]
        error: list[BaseException] = []

        def wrong_thread() -> None:
            try:
                ingress.drain_one(
                    self.runtime_instance,
                    timeout_seconds=0.01,
                )
            except BaseException as caught:
                error.append(caught)

        worker = threading.Thread(target=wrong_thread)
        worker.start()
        worker.join()
        self.assertIsInstance(error[0], WrongOwnerThread)
        self.assertEqual(
            ingress._queue.qsize(),  # type: ignore[attr-defined]
            size_before,
        )
        ingress.drain_one(self.runtime_instance, timeout_seconds=0.1)
        _join(producer_thread, producer)

    @unittest.skipUnless(hasattr(os, "fork"), "requires fork")
    def test_postfork_drain_checks_before_dequeue(self):
        ingress = self.make_ingress(
            capacity=2,
            receipt_timeout_seconds=1.0,
        )
        timeout_gate = threading.Event()
        pairs, _ = self.admit_many(
            ingress,
            2,
            receipt_timeout_gate=timeout_gate,
        )
        timeout_gate.set()
        for thread, producer in pairs:
            _join(thread, producer)
            self.assertIsInstance(
                producer.error,
                IngressOwnerUnresponsive,
            )
        parent_order = tuple(
            node.item.producer_sequence
            for node in ingress._queue.queue  # type: ignore[attr-defined]
        )
        self.assertEqual(len(parent_order), 2)
        wal_before_fork = self.wal_path().read_bytes()
        read_fd, write_fd = os.pipe()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            child = os.fork()
        if child == 0:
            try:
                os.close(read_fd)
                before_order = tuple(
                    node.item.producer_sequence
                    for node in ingress._queue.queue  # type: ignore[attr-defined]
                )
                before_wal = self.wal_path().read_bytes()
                error_name = None
                try:
                    ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.01,
                    )
                except BaseException as error:
                    error_name = type(error).__name__
                after_order = tuple(
                    node.item.producer_sequence
                    for node in ingress._queue.queue  # type: ignore[attr-defined]
                )
                after_wal = self.wal_path().read_bytes()
                payload = json.dumps(
                    {
                        "after_count": len(after_order),
                        "after_order": after_order,
                        "before_count": len(before_order),
                        "before_order": before_order,
                        "error": error_name,
                        "wal_unchanged": before_wal == after_wal,
                    },
                    sort_keys=True,
                ).encode("ascii")
                os.write(write_fd, payload)
            finally:
                os._exit(0)
        os.close(write_fd)
        observed = os.read(read_fd, 4096)
        os.close(read_fd)
        waited, status = os.waitpid(child, 0)
        self.assertEqual(waited, child)
        self.assertTrue(os.WIFEXITED(status))
        child_report = json.loads(observed)
        self.assertEqual(child_report["error"], "WrongOwnerThread")
        self.assertEqual(
            tuple(child_report["before_order"]),
            parent_order,
        )
        self.assertEqual(
            child_report["after_order"],
            child_report["before_order"],
        )
        self.assertEqual(child_report["before_count"], 2)
        self.assertEqual(child_report["after_count"], 2)
        self.assertIs(child_report["wal_unchanged"], True)
        self.assertEqual(
            ingress._queue.qsize(),  # type: ignore[attr-defined]
            2,
        )
        self.assertEqual(self.wal_path().read_bytes(), wal_before_fork)
        with self.assertRaisesRegex(
            IngressClosed,
            r"\Aingress_runtime_unavailable\Z",
        ):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        self.assertEqual(
            ingress._queue.qsize(),  # type: ignore[attr-defined]
            0,
        )
        self.assertEqual(self.wal_path().read_bytes(), wal_before_fork)
        self.assertEqual(self._close_reasons, [])
        for operation in (
            lambda: ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            ),
            lambda: ingress.close_external_halt(
                self.runtime_instance
            ),
        ):
            with self.assertRaisesRegex(
                IngressClosed,
                r"\Aingress_closed\Z",
            ):
                operation()

    def test_empty_queue_at_exact_session_end_writes_one_clean_terminal(self):
        ingress = self.make_ingress()
        self.fixture.now = self.fixture.request.session_end_utc
        terminal = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.01,
        )
        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "session_end",
        )
        self.assertEqual(self._close_reasons, [(True, "session_end")])
        with self.assertRaises(IngressClosed):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )

    def test_queued_item_at_exact_session_end_never_writes_clean_terminal(self):
        ingress = self.make_ingress()
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        self.fixture.now = self.fixture.request.session_end_utc
        with self.assertRaises(ProviderGateError):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        _join(producer_thread, producer)
        self.assertIsInstance(producer.error, IngressClosed)
        self.assertEqual(
            self._close_reasons,
            [(False, "provider_gate_denied")],
        )

    def test_queue_timeout_boundary_admission_is_not_skipped(self):
        ingress = self.make_ingress()
        candidate = self.item(1)
        producer_holder: list[
            tuple[threading.Thread, _ProducerResult]
        ] = []
        original_wait = ingress._condition.wait  # type: ignore[attr-defined]
        wait_called = threading.Event()

        def boundary_wait(timeout):
            wait_called.set()
            pair = _start_producer(ingress, candidate)
            producer_holder.append(pair)
            if not original_wait(2.0):
                raise AssertionError("boundary producer did not enqueue")
            return False

        checks = 0
        original_check = EventRuntime.check_ingress_session_end

        def tracked_check(runtime):
            nonlocal checks
            checks += 1
            return original_check(runtime)

        with mock.patch.object(
            ingress._condition,  # type: ignore[attr-defined]
            "wait",
            side_effect=boundary_wait,
        ), mock.patch.object(
            EventRuntime,
            "check_ingress_session_end",
            tracked_check,
        ):
            raw = ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.05,
            )
        self.assertTrue(wait_called.is_set())
        thread, result = producer_holder[0]
        _join(thread, result)
        self.assertEqual(result.receipt.raw_ingest_seq, raw.ingest_seq)
        self.assertEqual(checks, 1)
        self.assertIsNotNone(original_wait)

    def test_notify_without_state_change_does_not_end_idle_wait_early(self):
        ingress = self.make_ingress()
        waits: list[float] = []
        checks = 0

        def notify_only_wait(remaining):
            waits.append(remaining)
            return True

        def check_remains_open(_runtime):
            nonlocal checks
            checks += 1
            return False

        with mock.patch.object(
            ingress._condition,  # type: ignore[attr-defined]
            "wait",
            side_effect=notify_only_wait,
        ), mock.patch.object(
            EventRuntime,
            "check_ingress_session_end",
            check_remains_open,
        ), mock.patch(
            "tennis_v1.ingress.time.monotonic",
            side_effect=(10.0, 10.1, 10.2, 10.5),
        ):
            result = ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.5,
            )

        self.assertIsNone(result)
        self.assertEqual(checks, 3)
        self.assertEqual(len(waits), 2)
        self.assertAlmostEqual(waits[0], 0.4)
        self.assertAlmostEqual(waits[1], 0.3)
        self.assertGreater(waits[0], waits[1])

    def test_calls_after_terminal_raise_stable_closed_and_write_no_bytes(self):
        ingress = self.make_ingress()
        ingress.close_inputs()
        ingress.drain_one(self.runtime_instance, timeout_seconds=0.01)
        before = self.wal_path().read_bytes()
        for operation in (
            lambda: ingress.enqueue(self.item(1)),
            lambda: ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            ),
        ):
            with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
                operation()
        self.assertEqual(self.wal_path().read_bytes(), before)

    def test_calls_after_each_fault_terminal_raise_plain_stable_closed(self):
        for fault_reason in ("backpressure", "owner_unresponsive"):
            with self.subTest(fault_reason=fault_reason):
                self.reset_stack()
                self.runtime_instance = self.runtime()
                if fault_reason == "backpressure":
                    ingress = self.make_ingress(
                        capacity=1,
                        producer_timeout_seconds=0.03,
                    )
                    admitted_thread, admitted = _start_producer(
                        ingress,
                        self.item(1),
                    )
                    _wait_until_queued(ingress)
                    rejected_thread, rejected = _start_producer(
                        ingress,
                        self.item(2),
                    )
                    _join(rejected_thread, rejected)
                    self.assertIsInstance(
                        rejected.error,
                        IngressBackpressureHalt,
                    )
                    terminal = ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.1,
                    )
                    _join(admitted_thread, admitted)
                    self.assertEqual(terminal.record_kind, RecordKind.RAW)
                    terminal = ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.1,
                    )
                    expected_terminal = "ingress_backpressure"
                else:
                    ingress = self.make_ingress(
                        receipt_timeout_seconds=0.03,
                    )
                    admitted_thread, admitted = _start_producer(
                        ingress,
                        self.item(1),
                    )
                    _join(admitted_thread, admitted)
                    self.assertIsInstance(
                        admitted.error,
                        IngressOwnerUnresponsive,
                    )
                    with self.assertRaisesRegex(
                        IngressClosed,
                        "ingress_runtime_unavailable",
                    ):
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                    terminal = None
                    expected_terminal = None

                if terminal is not None:
                    self.assertEqual(
                        self.terminal_payload(terminal)["reason"],
                        expected_terminal,
                    )
                before = self.wal_path().read_bytes()
                operations = (
                    lambda: ingress.enqueue(self.item(3)),
                    lambda: ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.01,
                    ),
                )
                for operation in operations:
                    with self.assertRaises(IngressClosed) as caught:
                        operation()
                    self.assertIs(type(caught.exception), IngressClosed)
                    self.assertEqual(
                        str(caught.exception),
                        "ingress_closed",
                    )
                self.assertEqual(self.wal_path().read_bytes(), before)

    def test_first_valid_drain_binds_one_exact_runtime_before_dequeue(self):
        ingress = self.make_ingress(capacity=2)
        first_thread, first = _start_producer(ingress, self.item(1))
        _wait_until_queued(ingress)
        second_thread, second = _start_producer(ingress, self.item(2))
        _wait_until_queue_size(ingress, 2)

        with concrete_environment() as (
            second_fixture,
            second_coordinator,
            second_gate,
            second_manifest,
        ):
            second_authorizer = bind_provider_persistence_authorizer(
                gate=second_gate,
                coordinator=second_coordinator,
                session_manifest=second_manifest,
            )
            second_capability = second_coordinator.arm_before_wal(
                session_manifest=second_manifest,
                decision=second_authorizer.bound_decision,
                persistence_authorizer=second_authorizer,
            )
            second_writer = JournalWriter.create(
                write_capability=second_capability,
                session_manifest=second_manifest,
            )
            second_runtime = EventRuntime(
                writer=second_writer,
                state=initial_state(second_manifest.session_id),
                persistence_authorizer=second_authorizer,
                coordinator=second_coordinator,
            )
            second_path = (
                second_fixture.config.state_root
                / "sessions"
                / f"{second_manifest.session_id}.wal"
            )

            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            _join(first_thread, first)
            primary_before = self.wal_path().read_bytes()
            second_before = second_path.read_bytes()
            queued_before = ingress._queue.qsize()  # type: ignore[attr-defined]

            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_mismatch",
            ):
                ingress.drain_one(
                    second_runtime,
                    timeout_seconds=0.1,
                )

            self.assertEqual(
                ingress._queue.qsize(),  # type: ignore[attr-defined]
                queued_before,
            )
            self.assertEqual(self.wal_path().read_bytes(), primary_before)
            self.assertEqual(second_path.read_bytes(), second_before)
            self.assertNotIn(second_manifest.session_id, repr(ingress))
            self.assertFalse(second.done.is_set())

            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            _join(second_thread, second)
            ingress.close_inputs()
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            second_runtime.close_clean("operator_stop")

    def test_ingress_holds_no_node_or_admission_lock_across_runtime_calls(self):
        ingress = self.make_ingress()
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        original_ingest = EventRuntime.ingest

        def checked_ingest(runtime, candidate):
            acquired = ingress._admission_lock.acquire(  # type: ignore[attr-defined]
                blocking=False
            )
            self.assertTrue(acquired)
            ingress._admission_lock.release()  # type: ignore[attr-defined]
            node = ingress._active_node  # type: ignore[attr-defined]
            acquired_completion = node.completion_lock.acquire(
                blocking=False
            )
            self.assertTrue(acquired_completion)
            node.completion_lock.release()
            return original_ingest(runtime, candidate)

        with mock.patch.object(EventRuntime, "ingest", checked_ingest):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        _join(producer_thread, producer)

    def test_session_check_and_all_terminal_calls_hold_no_ingress_lock(self):
        ingress = self.make_ingress()
        check_calls = 0
        original_check = EventRuntime.check_ingress_session_end

        def checked_session_check(runtime):
            nonlocal check_calls
            check_calls += 1
            self.assert_ingress_locks_available(ingress, [])
            return original_check(runtime)

        with mock.patch.object(
            EventRuntime,
            "check_ingress_session_end",
            new=checked_session_check,
        ):
            self.assertIsNone(ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            ))
        self.assertGreaterEqual(check_calls, 1)

        cases = (
            (
                "operator_stop",
                "close_clean",
                "operator_stop",
            ),
            (
                "backpressure",
                "close_ingress_backpressure",
                "ingress_backpressure",
            ),
            (
                "owner_unresponsive",
                "close_ingress_owner_unresponsive",
                "ingress_owner_unresponsive",
            ),
            (
                "session_end",
                "close_ingress_session_end",
                "session_end",
            ),
        )
        for mode, method_name, expected_reason in cases:
            with self.subTest(mode=mode):
                self.reset_stack()
                self.runtime_instance = self.runtime()
                nodes: list[object] = []
                pairs: list[
                    tuple[threading.Thread, _ProducerResult]
                ] = []
                if mode == "session_end":
                    ingress = self.make_ingress()
                    self.fixture.now = self.fixture.request.session_end_utc
                elif mode == "backpressure":
                    ingress = self.make_ingress(
                        capacity=1,
                        producer_timeout_seconds=0.03,
                    )
                    pairs, nodes = self.admit_many(ingress, 1)
                    rejected_thread, rejected = _start_producer(
                        ingress,
                        self.item(2),
                    )
                    _join(rejected_thread, rejected)
                    self.assertIsInstance(
                        rejected.error,
                        IngressBackpressureHalt,
                    )
                elif mode == "owner_unresponsive":
                    ingress = self.make_ingress(
                        capacity=1,
                        receipt_timeout_seconds=1.0,
                    )
                    timeout_gate = threading.Event()
                    pairs, nodes = self.admit_many(
                        ingress,
                        1,
                        receipt_timeout_gate=timeout_gate,
                    )
                    timeout_gate.set()
                    _join(*pairs[0])
                    self.assertIsInstance(
                        pairs[0][1].error,
                        IngressOwnerUnresponsive,
                    )
                else:
                    ingress = self.make_ingress(capacity=1)
                    pairs, nodes = self.admit_many(ingress, 1)
                    ingress.close_inputs()

                original_terminal = getattr(EventRuntime, method_name)
                terminal_calls = 0

                def checked_terminal(runtime, *args, **kwargs):
                    nonlocal terminal_calls
                    terminal_calls += 1
                    self.assert_ingress_locks_available(
                        ingress,
                        nodes,
                    )
                    return original_terminal(
                        runtime,
                        *args,
                        **kwargs,
                    )

                with mock.patch.object(
                    EventRuntime,
                    method_name,
                    new=checked_terminal,
                ):
                    if mode in ("operator_stop", "backpressure"):
                        raw = ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                        self.assertEqual(raw.record_kind, RecordKind.RAW)
                        terminal = ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                    elif mode == "owner_unresponsive":
                        with self.assertRaisesRegex(
                            IngressClosed,
                            "ingress_runtime_unavailable",
                        ):
                            ingress.drain_one(
                                self.runtime_instance,
                                timeout_seconds=0.1,
                            )
                        terminal = None
                    else:
                        terminal = ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                for thread, result in pairs:
                    _join(thread, result)
                self.assertEqual(
                    terminal_calls,
                    0 if mode == "owner_unresponsive" else 1,
                )
                if terminal is not None:
                    self.assertEqual(
                        self.terminal_payload(terminal)["reason"],
                        expected_reason,
                    )

    def test_external_halt_rejects_queued_work_until_raw_parents_are_drained(
        self,
    ):
        ingress = self.make_ingress(
            capacity=3,
            receipt_timeout_seconds=30.0,
        )
        self.assertIsNone(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        pairs, nodes = self.admit_many(ingress, 3)
        wal_before = self.wal_path().read_bytes()

        with self.assertRaisesRegex(
            IngressClosed,
            r"\Aingress_not_between_drains\Z",
        ):
            ingress.close_external_halt(self.runtime_instance)
        self.assertEqual(
            ingress._queue.qsize(),  # type: ignore[attr-defined]
            3,
        )
        self.assertEqual(self.wal_path().read_bytes(), wal_before)
        self.assertEqual(self._close_reasons, [])

        raws = tuple(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
            for _ in range(3)
        )
        for thread, result in pairs:
            _join(thread, result)
            self.assertIsNone(result.error)
            self.assertIsNotNone(result.receipt)
        self.assertTrue(
            all(raw.record_kind is RecordKind.RAW for raw in raws)
        )
        self.assertEqual(
            tuple(result.receipt.raw_ingest_seq for _, result in pairs),
            tuple(raw.ingest_seq for raw in raws),
        )
        self.assertEqual(
            ingress._queue.qsize(),  # type: ignore[attr-defined]
            0,
        )
        self.assert_ingress_locks_available(ingress, nodes)

        terminal = ingress.close_external_halt(self.runtime_instance)

        self.assertEqual(
            self.terminal_payload(terminal)["reason"],
            "operator_halt",
        )
        self.assertEqual(
            self._close_reasons,
            [(False, "operator_halt")],
        )
        self.assertGreater(len(self.wal_path().read_bytes()), len(wal_before))
        records, summary = self.diagnostic_records()
        self.assertEqual(
            sum(
                record.record_kind is RecordKind.RAW
                for record in records
            ),
            3,
        )
        self.assertEqual(summary.raw_count, 3)

        after_terminal = self.wal_path().read_bytes()
        operations = (
            lambda: ingress.enqueue(self.item(9)),
            lambda: ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            ),
            lambda: ingress.close_external_halt(
                self.runtime_instance
            ),
        )
        for operation in operations:
            with self.assertRaisesRegex(
                IngressClosed,
                r"\Aingress_closed\Z",
            ):
                operation()
        ingress.close_inputs()
        self.assertEqual(self.wal_path().read_bytes(), after_terminal)

    def test_external_halt_honors_already_won_static_ingress_fault(self):
        cases = (
            (
                "backpressure",
                "ingress_backpressure",
                IngressBackpressureHalt,
            ),
            (
                "owner_unresponsive",
                "ingress_owner_unresponsive",
                IngressOwnerUnresponsive,
            ),
        )
        for mode, expected_reason, expected_error in cases:
            with self.subTest(mode=mode):
                self.reset_stack()
                self.runtime_instance = self.runtime()
                if mode == "backpressure":
                    ingress = self.make_ingress(
                        capacity=1,
                        producer_timeout_seconds=0.03,
                        receipt_timeout_seconds=30.0,
                    )
                else:
                    ingress = self.make_ingress(
                        capacity=1,
                        receipt_timeout_seconds=0.03,
                    )
                self.assertIsNone(
                    ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.01,
                    )
                )
                admitted_thread, admitted = _start_producer(
                    ingress,
                    self.item(1),
                )
                _wait_until_queued(ingress)
                if mode == "backpressure":
                    rejected_thread, rejected = _start_producer(
                        ingress,
                        self.item(2),
                    )
                    _join(rejected_thread, rejected)
                    self.assertIsInstance(
                        rejected.error,
                        expected_error,
                    )
                else:
                    _join(admitted_thread, admitted)
                    self.assertIsInstance(
                        admitted.error,
                        expected_error,
                    )

                wal_before = self.wal_path().read_bytes()
                with self.assertRaisesRegex(
                    IngressClosed,
                    r"\Aingress_not_between_drains\Z",
                ):
                    ingress.close_external_halt(
                        self.runtime_instance
                    )
                self.assertEqual(self.wal_path().read_bytes(), wal_before)

                if mode == "backpressure":
                    raw = ingress.drain_one(
                        self.runtime_instance,
                        timeout_seconds=0.1,
                    )
                    _join(admitted_thread, admitted)
                    self.assertEqual(raw.record_kind, RecordKind.RAW)
                    self.assertIsNone(admitted.error)
                    self.assertIsNotNone(admitted.receipt)
                    terminal = ingress.close_external_halt(
                        self.runtime_instance
                    )
                    self.assertEqual(
                        self.terminal_payload(terminal)["reason"],
                        expected_reason,
                    )
                    self.assertEqual(
                        self._close_reasons,
                        [(False, expected_reason)],
                    )
                    records, summary = self.diagnostic_records()
                    self.assertEqual(
                        sum(
                            record.record_kind is RecordKind.RAW
                            for record in records
                        ),
                        1,
                    )
                    self.assertEqual(summary.raw_count, 1)
                else:
                    with self.assertRaisesRegex(
                        IngressClosed,
                        r"\Aingress_runtime_unavailable\Z",
                    ):
                        ingress.drain_one(
                            self.runtime_instance,
                            timeout_seconds=0.1,
                        )
                    with self.assertRaisesRegex(
                        IngressClosed,
                        r"\Aingress_closed\Z",
                    ):
                        ingress.close_external_halt(
                            self.runtime_instance
                        )
                    self.assertEqual(self._close_reasons, [])
                    self.assertEqual(
                        self.wal_path().read_bytes(),
                        wal_before,
                    )

    def test_external_halt_requires_bound_owner_and_exact_runtime(self):
        ingress = self.make_ingress()
        before = self.wal_path().read_bytes()
        with self.assertRaisesRegex(
            IngressClosed,
            r"\Aingress_runtime_unbound\Z",
        ):
            ingress.close_external_halt(self.runtime_instance)
        self.assertEqual(self.wal_path().read_bytes(), before)

        self.assertIsNone(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        errors: list[BaseException] = []
        thread = threading.Thread(
            target=lambda: _capture_ingress_exception(
                errors,
                lambda: ingress.close_external_halt(
                    self.runtime_instance
                ),
            )
        )
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIs(type(errors[0]), WrongOwnerThread)
        self.assertEqual(self.wal_path().read_bytes(), before)

        with concrete_environment() as (
            _fixture,
            coordinator,
            gate,
            manifest,
        ):
            authorizer = bind_provider_persistence_authorizer(
                gate=gate,
                coordinator=coordinator,
                session_manifest=manifest,
            )
            capability = coordinator.arm_before_wal(
                session_manifest=manifest,
                decision=authorizer.bound_decision,
                persistence_authorizer=authorizer,
            )
            writer = JournalWriter.create(
                write_capability=capability,
                session_manifest=manifest,
            )
            other_runtime = EventRuntime(
                writer=writer,
                state=initial_state(manifest.session_id),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            with self.assertRaisesRegex(
                IngressClosed,
                r"\Aingress_runtime_mismatch\Z",
            ):
                ingress.close_external_halt(other_runtime)
            other_runtime.close_clean("operator_stop")
        self.assertEqual(self.wal_path().read_bytes(), before)
        ingress.close_external_halt(self.runtime_instance)

    def test_external_halt_rejects_active_or_polling_drain_state(self):
        ingress = self.make_ingress()
        self.assertIsNone(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        before = self.wal_path().read_bytes()
        for attribute, value in (
            ("_poll_in_progress", True),
            ("_active_node", object()),
        ):
            with self.subTest(attribute=attribute):
                setattr(ingress, attribute, value)
                try:
                    with self.assertRaisesRegex(
                        IngressClosed,
                        r"\Aingress_not_between_drains\Z",
                    ):
                        ingress.close_external_halt(
                            self.runtime_instance
                        )
                finally:
                    setattr(
                        ingress,
                        attribute,
                        False if attribute == "_poll_in_progress" else None,
                    )
                self.assertEqual(self.wal_path().read_bytes(), before)
        ingress.close_external_halt(self.runtime_instance)

    def test_external_halt_rejects_queue_without_waiting_on_node_lock(
        self,
    ):
        ingress = self.make_ingress(
            capacity=1,
            receipt_timeout_seconds=30.0,
        )
        self.assertIsNone(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        timeout_gate = threading.Event()
        producer_has_node_lock = threading.Event()
        release_producer = threading.Event()
        node_holder: list[object] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        class BarrierCompletion:
            def __init__(self) -> None:
                self._event = threading.Event()

            def set(self) -> None:
                self._event.set()

            def wait(self, _timeout=None) -> bool:
                if not timeout_gate.wait(2.0):
                    raise AssertionError("timeout barrier not released")
                return False

        class BarrierNodeLock:
            def __init__(self) -> None:
                self._lock = threading.Lock()

            def __enter__(self):
                self._lock.acquire()
                if threading.current_thread() is not threading.main_thread():
                    producer_has_node_lock.set()
                    if not release_producer.wait(2.0):
                        raise AssertionError(
                            "producer node lock was not released"
                        )
                return self

            def __exit__(self, *_):
                self._lock.release()
                return False

            def acquire(self, *args, **kwargs):
                return self._lock.acquire(*args, **kwargs)

            def release(self):
                return self._lock.release()

        def install_barriers(node, *args, **kwargs):
            node.completion = BarrierCompletion()
            node.completion_lock = BarrierNodeLock()
            node_holder.append(node)
            return original_put(node, *args, **kwargs)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=install_barriers,
        ):
            producer_thread, producer = _start_producer(
                ingress,
                self.item(1),
            )
            _wait_until_queued(ingress)
        timeout_gate.set()
        self.assertTrue(producer_has_node_lock.wait(2.0))

        before = self.wal_path().read_bytes()
        with self.assertRaisesRegex(
            IngressClosed,
            r"\Aingress_not_between_drains\Z",
        ):
            ingress.close_external_halt(self.runtime_instance)
        self.assertFalse(release_producer.is_set())
        self.assertTrue(producer_has_node_lock.is_set())
        self.assertEqual(
            ingress._queue.qsize(),  # type: ignore[attr-defined]
            1,
        )
        self.assertEqual(self.wal_path().read_bytes(), before)

        release_producer.set()
        _join(producer_thread, producer)
        self.assertIs(
            type(producer.error),
            IngressOwnerUnresponsive,
        )
        with self.assertRaisesRegex(
            IngressClosed,
            r"\Aingress_runtime_unavailable\Z",
        ):
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.1,
            )
        with self.assertRaisesRegex(
            IngressClosed,
            r"\Aingress_closed\Z",
        ):
            ingress.close_external_halt(self.runtime_instance)
        self.assertEqual(self._close_reasons, [])
        self.assertEqual(self.wal_path().read_bytes(), before)
        self.assertEqual(len(node_holder), 1)

    def test_external_halt_runtime_failure_is_terminally_closed(self):
        ingress = self.make_ingress(
            capacity=1,
            receipt_timeout_seconds=30.0,
        )
        self.assertIsNone(
            ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        producer_thread, producer = _start_producer(
            ingress,
            self.item(1),
        )
        _wait_until_queued(ingress)
        raw = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.1,
        )
        _join(producer_thread, producer)
        self.assertEqual(raw.record_kind, RecordKind.RAW)
        self.assertIsNone(producer.error)
        self.assertIsNotNone(producer.receipt)
        before = self.wal_path().read_bytes()

        with mock.patch.object(
            EventRuntime,
            "close_halted",
            side_effect=JournalDurabilityError("uncertain"),
        ):
            with self.assertRaises(JournalDurabilityError):
                ingress.close_external_halt(self.runtime_instance)
        self.assertEqual(self.wal_path().read_bytes(), before)
        for operation in (
            lambda: ingress.enqueue(self.item(2)),
            lambda: ingress.drain_one(
                self.runtime_instance,
                timeout_seconds=0.01,
            ),
            lambda: ingress.close_external_halt(
                self.runtime_instance
            ),
        ):
            with self.assertRaisesRegex(
                IngressClosed,
                r"\Aingress_closed\Z",
            ):
                operation()


INGRESS_CRASH_SCRIPT = r"""
import json
import os
import signal
import sys
import threading
from unittest import mock

from tennis_v1 import adapter_contract
from tennis_v1.entitlements import ProviderGate
from tennis_v1.ingress import BoundedIngress, IngressItem
from tennis_v1.replay_core import ReplayMismatch, scan_diagnostic_prefix
from tennis_v1.retention import RetentionCoordinator, RetentionError
import tennis_v1.sequencer as sequencer_module
from tennis_v1.sequencer import (
    EventRuntime,
    bind_provider_persistence_authorizer,
)
from tennis_v1.session import session_manifest_sha256
from tennis_v1.state import initial_state
from tennis_v1.wal import JournalWriter, ScanIssue
from tests.tennis_v1 import test_events as event_test_module
from tests.tennis_v1.test_sequencer import captured

kind = sys.argv[1]
assert kind in {"before_drain", "after_raw"}
for attempt in range(3):
    fixture = event_test_module.SessionContractTests(
        "test_session_manifest_requires_verified_eligible_matching_inputs"
    )
    fixture.setUp()
    coordinator = None
    try:
        manifest = fixture.build()
        signal_read, signal_write = os.pipe()
        block_read, block_write = os.pipe()
        with mock.patch.multiple(
            adapter_contract,
            __file__=fixture.builder.adapter_file,
            _ADAPTER_REGISTRY={
                ("synthetic-provider", "trial-v1"):
                    fixture.builder.registration
            },
        ):
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(signal_read)
                    os.close(block_write)
                    child_coordinator = RetentionCoordinator.acquire(
                        fixture.config,
                        clock_ns=lambda: manifest.created_wall_ns,
                    )
                    child_coordinator.recover_and_purge()
                    child_gate = ProviderGate(
                        fixture.config,
                        fixture.provider_manifest,
                        fixture.request,
                        environ={
                            "SYNTHETIC_API_KEY": "fixture-secret"
                        },
                        clock=lambda: fixture.now,
                    )
                    child_authorizer = (
                        bind_provider_persistence_authorizer(
                            gate=child_gate,
                            coordinator=child_coordinator,
                            session_manifest=manifest,
                        )
                    )
                    capability = child_coordinator.arm_before_wal(
                        session_manifest=manifest,
                        decision=child_authorizer.bound_decision,
                        persistence_authorizer=child_authorizer,
                    )
                    writer = JournalWriter.create(
                        write_capability=capability,
                        session_manifest=manifest,
                    )
                    runtime = EventRuntime(
                        writer=writer,
                        state=initial_state(manifest.session_id),
                        persistence_authorizer=child_authorizer,
                        coordinator=child_coordinator,
                    )
                    ingress = BoundedIngress(
                        capacity=1,
                        producer_timeout_seconds=5.0,
                        receipt_timeout_seconds=5.0,
                    )
                    item = IngressItem(
                        producer_id="crash-producer",
                        producer_sequence=attempt,
                        captured=captured(child_authorizer),
                    )
                    original_put = ingress._queue.put

                    if kind == "before_drain":
                        def admitted_then_block(node, *args, **kwargs):
                            result = original_put(
                                node,
                                *args,
                                **kwargs,
                            )
                            os.write(signal_write, b"B")
                            os.read(block_read, 1)
                            raise AssertionError(
                                "parent did not kill child"
                            )

                        with mock.patch.object(
                            ingress._queue,
                            "put",
                            side_effect=admitted_then_block,
                        ):
                            ingress.enqueue(item)
                    else:
                        admitted = threading.Event()

                        def record_admission(node, *args, **kwargs):
                            result = original_put(
                                node,
                                *args,
                                **kwargs,
                            )
                            admitted.set()
                            return result

                        def enqueue_item():
                            ingress.enqueue(item)

                        with mock.patch.object(
                            ingress._queue,
                            "put",
                            side_effect=record_admission,
                        ):
                            producer = threading.Thread(
                                target=enqueue_item,
                                daemon=True,
                            )
                            producer.start()
                            if not admitted.wait(5.0):
                                raise AssertionError(
                                    "item was not admitted"
                                )

                        def raw_fsynced_then_block(*_):
                            os.write(signal_write, b"R")
                            os.read(block_read, 1)
                            raise AssertionError(
                                "parent did not kill child"
                            )

                        with mock.patch.object(
                            sequencer_module,
                            "reduce_event",
                            raw_fsynced_then_block,
                        ):
                            ingress.drain_one(
                                runtime,
                                timeout_seconds=5.0,
                            )
                finally:
                    os._exit(91)

            os.close(signal_write)
            os.close(block_read)
            observed = os.read(signal_read, 1)
            expected = b"B" if kind == "before_drain" else b"R"
            assert observed == expected, observed
            os.kill(child_pid, signal.SIGKILL)
            waited, status = os.waitpid(child_pid, 0)
            assert waited == child_pid
            assert os.WIFSIGNALED(status)
            assert os.WTERMSIG(status) == signal.SIGKILL
            os.close(signal_read)
            os.close(block_write)

            path = (
                fixture.config.state_root
                / "sessions"
                / f"{manifest.session_id}.wal"
            )
            before = path.read_bytes()
            coordinator = RetentionCoordinator.acquire(
                fixture.config,
                clock_ns=lambda: manifest.created_wall_ns,
            )
            coordinator.recover_and_purge()
            gate = ProviderGate(
                fixture.config,
                fixture.provider_manifest,
                fixture.request,
                environ={"SYNTHETIC_API_KEY": "fixture-secret"},
                clock=lambda: fixture.now,
            )
            authorizer = bind_provider_persistence_authorizer(
                gate=gate,
                coordinator=coordinator,
                session_manifest=manifest,
            )
            rearm_before = path.read_bytes()
            try:
                coordinator.arm_before_wal(
                    session_manifest=manifest,
                    decision=authorizer.bound_decision,
                    persistence_authorizer=authorizer,
                )
            except RetentionError as error:
                assert (
                    str(error)
                    == "retention_session_already_armed"
                )
            else:
                raise AssertionError(
                    "crashed session rearmed or resumed"
                )
            assert path.read_bytes() == rearm_before

            result = scan_diagnostic_prefix(
                expected_session_manifest_sha256=(
                    session_manifest_sha256(manifest)
                ),
                persistence_authorizer=authorizer,
                coordinator=coordinator,
            )
            after = path.read_bytes()
            assert before == after
            assert result.scan_issue is ScanIssue.MISSING_TERMINAL
            assert result.exact_replay is False
            assert result.research_evaluable is False
            assert result.state is not None
            if kind == "before_drain":
                assert result.replay_mismatch is None
                assert result.raw_count == 0
                assert result.derived_count == 0
                assert result.state.raw_count == 0
            else:
                assert (
                    result.replay_mismatch
                    is ReplayMismatch.DERIVED_MISSING
                )
                assert result.raw_count == 1
                assert result.derived_count == 0
                assert result.state.raw_count == 1
    finally:
        if coordinator is not None:
            coordinator.close()
        fixture.tearDown()
print(json.dumps(
    {
        "attempts": 3,
        "kind": kind,
        "rearm_rejections": 3,
    },
    sort_keys=True,
))
"""


class IngressCrashRecoveryTests(unittest.TestCase):
    def run_crash_vector(self, kind: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", INGRESS_CRASH_SCRIPT, kind],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )

    def assert_crash_vector(self, kind: str) -> None:
        completed = self.run_crash_vector(kind)
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertIn('"attempts": 3', completed.stdout)
        self.assertIn(f'"kind": "{kind}"', completed.stdout)
        self.assertIn('"rearm_rejections": 3', completed.stdout)

    def test_crash_before_drain_preserves_only_provisional_volatile_work(self):
        self.assert_crash_vector("before_drain")

    def test_crash_after_raw_fsync_before_receipt_is_diagnostic_only(self):
        self.assert_crash_vector("after_raw")


class IngressStaticBoundaryTests(unittest.TestCase):
    def test_ingress_has_no_writer_retention_path_descriptor_or_capability(self):
        package = Path(__file__).resolve().parents[2] / "tennis_v1"
        source = (package / "ingress.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        attrs = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "JournalWriter",
            "RetentionCoordinator",
            "ProviderWalWriteCapability",
            "ProviderWalReadCapability",
            "Path",
            "open",
            "fileno",
            "_latch_global_halt",
            "_halt",
            "SimpleQueue",
            "_pending_faults",
        ):
            self.assertNotIn(forbidden, names | attrs)
        ingress_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "BoundedIngress"
        )
        calls_by_method = {
            method.name: tuple(
                call
                for call in ast.walk(method)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "close_halted"
            )
            for method in ingress_class.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(
            {
                name: len(calls)
                for name, calls in calls_by_method.items()
                if calls
            },
            {"_finalize": 1},
        )

    def test_only_ingress_calls_fixed_runtime_ingress_terminal_operations(self):
        package = Path(__file__).resolve().parents[2] / "tennis_v1"
        fixed_calls = {
            "close_ingress_backpressure",
            "close_ingress_owner_unresponsive",
            "check_ingress_session_end",
            "close_ingress_session_end",
        }
        observed: dict[str, set[str]] = {}
        for path in package.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in fixed_calls
            }
            if calls:
                observed[path.name] = calls
        self.assertEqual(observed, {"ingress.py": fixed_calls})

    def test_producer_facing_errors_and_internal_node_repr_hide_payload(self):
        source = Path(
            __import__("tennis_v1.ingress").ingress.__file__
        ).read_text(encoding="utf-8")
        self.assertNotIn("str(error)", source)
        self.assertNotIn("repr(error)", source)
        self.assertNotIn("BaseException =", source)


if __name__ == "__main__":
    unittest.main()
