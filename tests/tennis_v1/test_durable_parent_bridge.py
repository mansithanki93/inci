from __future__ import annotations

import ast
import gc
import hashlib
import inspect
from pathlib import Path
import threading
import time
import unittest
from unittest import mock
import weakref

import tennis_v1.ingress as ingress_module
from tennis_v1.canonical import canonical_json_bytes
from tennis_v1.codec import canonical_record_sha256
from tennis_v1.events import PersistedEvent
from tennis_v1.ingress import (
    BoundedIngress,
    DeferredEmergencyCommitSubjectV1,
    DurableCausalOrderCoordinateV1,
    DurableCausalPrecedesProofV1,
    DurableEvidenceTerminalV1,
    DurableIngressParentV1,
    DurableIngressReceipt,
    IngressClosed,
    IngressItem,
    IngressOwnerUnresponsive,
    _bind_durable_ingress_consumer_v1,
    _consume_durable_envelope_legacy_v1,
    _close_durable_causal_precedes_proof_after_deferred_append_failure_v1,
    _consume_durable_evidence_terminal_v1,
    _consume_durable_ingress_parent_v1,
    _consume_durable_causal_precedes_proof_after_deferred_append_v1,
    _issue_source_close_complete_coordinate_v1,
    _resolve_evidence_terminal_coordinate_v1,
    _validate_durable_evidence_terminal_for_consumer_v1,
    _validate_durable_ingress_parent_for_consumer_v1,
)
from tests.tennis_v1.test_ingress import (
    _join,
    _start_producer,
    _wait_until_queue_size,
    _wait_until_queued,
)
from tests.tennis_v1.test_sequencer import RuntimeFixture, captured


def _digest(domain: str, projection: object) -> str:
    return hashlib.sha256(
        domain.encode("ascii")
        + b"\x00"
        + canonical_json_bytes(projection)
    ).hexdigest()


def _captured_projection(value: object) -> dict[str, object]:
    return {
        "session_id": value.session_id,
        "event_type": value.event_type,
        "event_version": value.event_version,
        "source_kind": value.source_kind.value,
        "source_id": value.source_id,
        "source_entity_id": value.source_entity_id,
        "endpoint_id": value.endpoint_id,
        "endpoint_state": value.endpoint_state.value,
        "channel_id": value.channel_id,
        "channel_state": value.channel_state.value,
        "request_id": value.request_id,
        "request_id_state": value.request_id_state.value,
        "source_wall_ns": value.source_wall_ns,
        "source_generated_ns": value.source_generated_ns,
        "local_wall_ns": value.local_wall_ns,
        "local_monotonic_ns": value.local_monotonic_ns,
        "clock_uncertainty_ns": value.clock_uncertainty_ns,
        "connection_epoch": value.connection_epoch,
        "provider_sequence": value.provider_sequence,
        "content_type": value.content_type,
        "payload_encoding": value.payload_encoding,
        "payload_transform": value.payload_transform,
        "retention_delete_by_ns": value.retention_delete_by_ns,
        "payload_sha256": hashlib.sha256(value.payload).hexdigest(),
    }


def _item_sha256(value: IngressItem) -> str:
    return _digest(
        "INCI-INGRESS-ITEM-V1",
        {
            "producer_id": value.producer_id,
            "producer_sequence": value.producer_sequence,
            "captured": _captured_projection(value.captured),
        },
    )


def _receipt_sha256(value: DurableIngressReceipt) -> str:
    return _digest(
        "INCI-DURABLE-INGRESS-RECEIPT-V1",
        {
            "producer_id": value.producer_id,
            "producer_sequence": value.producer_sequence,
            "raw_ingest_seq": value.raw_ingest_seq,
            "raw_record_sha256": value.raw_record_sha256,
        },
    )


class DurableParentValueContractTests(unittest.TestCase):
    def test_exact_additive_api_signatures_are_frozen(self):
        self.assertEqual(
            tuple(inspect.signature(BoundedIngress.drain_one_parent).parameters),
            ("self", "runtime", "timeout_seconds"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    BoundedIngress.close_external_halt_terminal
                ).parameters
            ),
            ("self", "runtime"),
        )
        self.assertEqual(
            tuple(inspect.signature(_bind_durable_ingress_consumer_v1).parameters),
            ("ingress", "consumer"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    _validate_durable_ingress_parent_for_consumer_v1
                ).parameters
            ),
            ("envelope", "consumer"),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    _validate_durable_evidence_terminal_for_consumer_v1
                ).parameters
            ),
            ("envelope", "consumer"),
        )
        for finalizer in (
            _consume_durable_causal_precedes_proof_after_deferred_append_v1,
            _close_durable_causal_precedes_proof_after_deferred_append_failure_v1,
        ):
            with self.subTest(finalizer=finalizer.__name__):
                self.assertEqual(
                    tuple(inspect.signature(finalizer).parameters),
                    (
                        "proof",
                        "subject",
                        "controller",
                        "pending",
                        "terminal",
                    ),
                )

    def test_authoritative_values_are_opaque_redacted_and_non_subclassable(self):
        for value_type in (
            DurableIngressParentV1,
            DurableEvidenceTerminalV1,
            DurableCausalOrderCoordinateV1,
            DurableCausalPrecedesProofV1,
            DeferredEmergencyCommitSubjectV1,
        ):
            with self.subTest(value_type=value_type.__name__):
                with self.assertRaises(TypeError):
                    value_type()
                with self.assertRaises(TypeError):
                    type("Subclass", (value_type,), {})
                rebuilt = object.__new__(value_type)
                self.assertIn("redacted", repr(rebuilt))
                self.assertFalse(hasattr(rebuilt, "__dict__"))


class DurableParentRuntimeTests(RuntimeFixture):
    def setUp(self) -> None:
        super().setUp()
        self.runtime_instance = self.runtime()

    def ingress(self, *, receipt_timeout_seconds: float = 1.0) -> BoundedIngress:
        return BoundedIngress(
            capacity=4,
            producer_timeout_seconds=0.2,
            receipt_timeout_seconds=receipt_timeout_seconds,
        )

    def item(self, sequence: int) -> IngressItem:
        return IngressItem(
            producer_id="producer",
            producer_sequence=sequence,
            captured=captured(self.authorizer),
        )

    def assert_parent_issue_failure_closed(
        self,
        *,
        ingress: BoundedIngress,
        nodes: list[object],
        pairs: list[tuple[threading.Thread, object]],
        owner_error: BaseException | None,
        secret: str,
    ) -> None:
        for thread, result in pairs:
            _join(thread, result)
        first = pairs[0][1]
        second = pairs[1][1]
        self.assertIs(type(owner_error), IngressClosed)
        self.assertEqual(str(owner_error), "ingress_runtime_unavailable")
        self.assertNotIn(secret, str(owner_error))
        self.assertIsNone(first.error)
        self.assertIs(type(first.receipt), DurableIngressReceipt)
        self.assertIs(type(second.error), IngressClosed)
        self.assertIsNone(second.receipt)
        self.assertEqual([node.state for node in nodes], ["ABORTED", "ABORTED"])
        self.assertTrue(ingress._runtime_failed)  # type: ignore[attr-defined]
        self.assertIsNone(ingress._active_node)  # type: ignore[attr-defined]
        self.assertTrue(ingress._queue.empty())  # type: ignore[attr-defined]
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.enqueue(self.item(90))
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
        ingress.close_inputs()
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
        self.assertEqual(self._close_reasons, [])

    def test_parent_issues_only_after_producer_acknowledges_exact_receipt(self):
        ingress = self.ingress()
        item = self.item(7)
        producer, result = _start_producer(ingress, item)
        _wait_until_queued(ingress)

        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)

        self.assertIs(type(envelope), DurableIngressParentV1)
        self.assertIs(envelope.item, item)
        self.assertIs(type(envelope.parent), PersistedEvent)
        self.assertIs(envelope.receipt, result.receipt)
        self.assertEqual(envelope.schema_version, 1)
        self.assertEqual(
            envelope.receipt.raw_ingest_seq,
            envelope.parent.ingest_seq,
        )
        self.assertEqual(
            envelope.receipt.raw_record_sha256,
            canonical_record_sha256(envelope.parent),
        )
        expected = _digest(
            "INCI-DURABLE-INGRESS-PARENT-ENVELOPE-V1",
            {
                "schema_version": 1,
                "ingress_item_sha256": _item_sha256(item),
                "parent_record_sha256": canonical_record_sha256(
                    envelope.parent
                ),
                "durable_receipt_sha256": _receipt_sha256(
                    envelope.receipt
                ),
            },
        )
        self.assertEqual(envelope.envelope_sha256, expected)

    def test_pre_ack_barrier_records_ack_before_parent_issuance(self):
        """Freezes the acknowledgement winner at the pre-ack barrier."""
        ingress = self.ingress()
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        admitted_nodes: list[object] = []
        observed_states: list[str] = []

        class ObservedAcknowledgement:
            def __init__(self) -> None:
                self.real = threading.Event()

            def set(self) -> None:
                observed_states.append(admitted_nodes[0].state)
                self.real.set()

            def wait(self, timeout=None) -> bool:
                return self.real.wait(timeout)

        def install(node, *args, **kwargs):
            admitted_nodes.append(node)
            node.acknowledgement = ObservedAcknowledgement()
            return original_put(node, *args, **kwargs)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=install,
        ):
            producer, result = _start_producer(ingress, self.item(36))
            _wait_until_queued(ingress)
            envelope = ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
            _join(producer, result)

        self.assertEqual(observed_states, ["PRODUCER_ACKNOWLEDGED"])
        self.assertEqual(admitted_nodes[0].state, "PARENT_ISSUED")
        self.assertIs(type(envelope), DurableIngressParentV1)
        self.assertIs(result.receipt, envelope.receipt)

    def test_owner_ack_wait_expiry_aborts_without_parent(self):
        """Freezes the owner-wait expiry row after receipt publication."""
        ingress = self.ingress(receipt_timeout_seconds=1.0)
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        admitted_nodes: list[object] = []
        observed_states: list[str] = []
        owner_aborted = threading.Event()

        class WithheldCompletion:
            def __init__(self) -> None:
                self.set_calls = 0

            def set(self) -> None:
                self.set_calls += 1
                if self.set_calls >= 2:
                    owner_aborted.set()

            def wait(self, timeout=None) -> bool:
                return owner_aborted.wait(timeout)

        class ExpiringAcknowledgement:
            def set(self) -> None:
                pass

            def wait(self, timeout=None) -> bool:
                observed_states.append(admitted_nodes[0].state)
                return False

        def install(node, *args, **kwargs):
            admitted_nodes.append(node)
            node.completion = WithheldCompletion()
            node.acknowledgement = ExpiringAcknowledgement()
            return original_put(node, *args, **kwargs)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=install,
        ):
            producer, result = _start_producer(ingress, self.item(37))
            _wait_until_queued(ingress)
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            _join(producer, result)

        self.assertEqual(observed_states, ["RECEIPT_PUBLISHED"])
        self.assertEqual(admitted_nodes[0].state, "ABORTED")
        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        self.assertIsNone(result.receipt)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_owner_enters_ingesting_before_runtime_call_and_aborts_failure(self):
        ingress = self.ingress()
        admitted_nodes: list[object] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        def capture_node(node, *args, **kwargs):
            admitted_nodes.append(node)
            return original_put(node, *args, **kwargs)

        def fail_ingest(_runtime, _captured):
            self.assertEqual(admitted_nodes[0].state, "INGESTING")
            raise RuntimeError("controlled-ingest-failure")

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=capture_node,
        ):
            producer, result = _start_producer(ingress, self.item(23))
            _wait_until_queued(ingress)
            with mock.patch.object(
                type(self.runtime_instance),
                "ingest",
                new=fail_ingest,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "controlled-ingest-failure",
                ):
                    ingress.drain_one_parent(
                        self.runtime_instance,
                        timeout_seconds=0.2,
                    )
            _join(producer, result)

        self.assertEqual(admitted_nodes[0].state, "ABORTED")
        self.assertIs(type(result.error), IngressClosed)
        self.assertIsNone(result.receipt)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_parent_envelope_allocation_failure_poison_closes_exact_stream(self):
        """Catches an acknowledged RAW being skipped after allocation failure."""
        ingress = self.ingress(receipt_timeout_seconds=0.3)
        nodes: list[object] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_build = ingress_module._build_opaque_value
        secret = "SECRET-parent-envelope-allocation"

        def capture_node(node, *args, **kwargs):
            nodes.append(node)
            return original_put(node, *args, **kwargs)

        def fail_parent_allocation(value_type, fields):
            if value_type is DurableIngressParentV1:
                raise MemoryError(secret)
            return original_build(value_type, fields)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=capture_node,
        ):
            pairs = [
                _start_producer(ingress, self.item(sequence))
                for sequence in (41, 42)
            ]
            _wait_until_queue_size(ingress, 2)
            owner_error: BaseException | None = None
            outcome = None
            with mock.patch.object(
                ingress_module,
                "_build_opaque_value",
                side_effect=fail_parent_allocation,
            ):
                try:
                    outcome = ingress.drain_one_parent(
                        self.runtime_instance,
                        timeout_seconds=0.2,
                    )
                except BaseException as error:
                    owner_error = error

        self.assertIsNone(outcome)
        self.assert_parent_issue_failure_closed(
            ingress=ingress,
            nodes=nodes,
            pairs=pairs,
            owner_error=owner_error,
            secret=secret,
        )

    def test_parent_envelope_register_then_raise_rolls_back_and_poison_closes(self):
        """Catches leaked authority and continuation after ambiguous register."""
        ingress = self.ingress(receipt_timeout_seconds=0.3)
        nodes: list[object] = []
        registered_envelopes: list[DurableIngressParentV1] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_register = ingress_module._register_envelope_authority
        secret = "SECRET-parent-envelope-register"

        def capture_node(node, *args, **kwargs):
            nodes.append(node)
            return original_put(node, *args, **kwargs)

        def register_then_raise(envelope, authority):
            registered_envelopes.append(envelope)
            original_register(envelope, authority)
            raise RuntimeError(secret)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=capture_node,
        ):
            pairs = [
                _start_producer(ingress, self.item(sequence))
                for sequence in (43, 44)
            ]
            _wait_until_queue_size(ingress, 2)
            owner_error: BaseException | None = None
            outcome = None
            with mock.patch.object(
                ingress_module,
                "_register_envelope_authority",
                side_effect=register_then_raise,
            ):
                try:
                    outcome = ingress.drain_one_parent(
                        self.runtime_instance,
                        timeout_seconds=0.2,
                    )
                except BaseException as error:
                    owner_error = error

        self.assertIsNone(outcome)
        self.assertEqual(len(registered_envelopes), 1)
        self.assertIsNone(
            ingress_module._lookup_envelope_authority(
                registered_envelopes[0]
            )
        )
        self.assert_parent_issue_failure_closed(
            ingress=ingress,
            nodes=nodes,
            pairs=pairs,
            owner_error=owner_error,
            secret=secret,
        )

    def test_existing_active_node_is_never_overwritten_by_next_dequeue(self):
        """Catches loss of an exact active node through unconditional overwrite."""
        ingress = self.ingress(receipt_timeout_seconds=0.3)
        nodes: list[object] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        def capture_node(node, *args, **kwargs):
            nodes.append(node)
            return original_put(node, *args, **kwargs)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=capture_node,
        ):
            first_pair = _start_producer(ingress, self.item(45))
            _wait_until_queued(ingress)
            with ingress._condition:  # type: ignore[attr-defined]
                exact_active = ingress._queue.get_nowait()  # type: ignore[attr-defined]
                ingress._active_node = exact_active  # type: ignore[attr-defined]
            second_pair = _start_producer(ingress, self.item(46))
            _wait_until_queued(ingress)

            owner_error: BaseException | None = None
            outcome = None
            try:
                outcome = ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            except BaseException as error:
                owner_error = error

        _join(*first_pair)
        _join(*second_pair)
        self.assertIsNone(outcome)
        self.assertIs(type(owner_error), IngressClosed)
        self.assertEqual(str(owner_error), "ingress_runtime_unavailable")
        self.assertIs(type(first_pair[1].error), IngressClosed)
        self.assertIs(type(second_pair[1].error), IngressClosed)
        self.assertIsNone(first_pair[1].receipt)
        self.assertIsNone(second_pair[1].receipt)
        self.assertEqual([node.state for node in nodes], ["ABORTED", "ABORTED"])
        self.assertTrue(ingress._runtime_failed)  # type: ignore[attr-defined]
        self.assertIsNone(ingress._active_node)  # type: ignore[attr-defined]
        self.assertTrue(ingress._queue.empty())  # type: ignore[attr-defined]
        self.assertEqual(self._attempted_sequences, [])
        ingress.close_inputs()
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
        self.assertEqual(self._close_reasons, [])

    def test_raw_returned_state_precedes_receipt_publication(self):
        ingress = self.ingress()
        admitted_nodes: list[object] = []
        observed_states: list[str] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_digest = canonical_record_sha256

        def capture_node(node, *args, **kwargs):
            admitted_nodes.append(node)
            return original_put(node, *args, **kwargs)

        def observe_first_post_ingest_digest(value):
            if not observed_states:
                observed_states.append(admitted_nodes[0].state)
            return original_digest(value)

        with (
            mock.patch.object(
                ingress._queue,  # type: ignore[attr-defined]
                "put",
                side_effect=capture_node,
            ),
            mock.patch(
                "tennis_v1.ingress.canonical_record_sha256",
                side_effect=observe_first_post_ingest_digest,
            ),
        ):
            producer, result = _start_producer(ingress, self.item(24))
            _wait_until_queued(ingress)
            parent = ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
            _join(producer, result)

        self.assertEqual(observed_states, ["RAW_RETURNED"])
        self.assertIs(type(parent), DurableIngressParentV1)
        self.assertIsNotNone(result.receipt)

    def test_exact_bound_consumer_consumes_once_and_foreign_does_not_consume(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        with self.assertRaisesRegex(ValueError, "durable_ingress_parent_invalid"):
            _bind_durable_ingress_consumer_v1(ingress, object())

        producer, result = _start_producer(ingress, self.item(8))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        self.assertIs(type(envelope), DurableIngressParentV1)

        with self.assertRaisesRegex(ValueError, "durable_ingress_parent_invalid"):
            _consume_durable_ingress_parent_v1(envelope, object())
        self.assertIs(
            _consume_durable_ingress_parent_v1(envelope, consumer),
            envelope,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_consumed",
        ):
            _consume_durable_ingress_parent_v1(envelope, consumer)

    def test_nonconsuming_parent_validator_preserves_precedence_and_state(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        producer, result = _start_producer(ingress, self.item(38))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)

        self.assertIsNone(
            _validate_durable_ingress_parent_for_consumer_v1(
                envelope,
                consumer,
            )
        )
        self.assertIsNone(
            _validate_durable_ingress_parent_for_consumer_v1(
                envelope,
                consumer,
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _validate_durable_ingress_parent_for_consumer_v1(
                envelope,
                object(),
            )

        original_schema_version = envelope.schema_version
        object.__setattr__(envelope, "schema_version", 2)
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _validate_durable_ingress_parent_for_consumer_v1(
                envelope,
                consumer,
            )
        object.__setattr__(
            envelope,
            "schema_version",
            original_schema_version,
        )
        self.assertIs(
            _consume_durable_ingress_parent_v1(envelope, consumer),
            envelope,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_consumed",
        ):
            _validate_durable_ingress_parent_for_consumer_v1(
                envelope,
                consumer,
            )

        rebuilt = object.__new__(DurableIngressParentV1)
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _validate_durable_ingress_parent_for_consumer_v1(
                rebuilt,
                consumer,
            )
        with self.assertRaisesRegex(
            TypeError,
            "exact DurableIngressParentV1 required",
        ):
            _validate_durable_ingress_parent_for_consumer_v1(
                object(),
                consumer,
            )

    def test_nonconsuming_terminal_validator_preserves_precedence_and_state(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        ingress.close_inputs()
        terminal = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)

        self.assertIsNone(
            _validate_durable_evidence_terminal_for_consumer_v1(
                terminal,
                consumer,
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_invalid",
        ):
            _validate_durable_evidence_terminal_for_consumer_v1(
                terminal,
                object(),
            )
        self.assertIs(
            _consume_durable_evidence_terminal_v1(terminal, consumer),
            terminal,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_consumed",
        ):
            _validate_durable_evidence_terminal_for_consumer_v1(
                terminal,
                consumer,
            )

        rebuilt = object.__new__(DurableEvidenceTerminalV1)
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_invalid",
        ):
            _validate_durable_evidence_terminal_for_consumer_v1(
                rebuilt,
                consumer,
            )
        with self.assertRaisesRegex(
            TypeError,
            "exact DurableEvidenceTerminalV1 required",
        ):
            _validate_durable_evidence_terminal_for_consumer_v1(
                object(),
                consumer,
            )

    def test_corrupt_issued_parent_is_rejected_without_consuming_authority(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        producer, result = _start_producer(ingress, self.item(18))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        self.assertIs(type(envelope), DurableIngressParentV1)

        object.__setattr__(envelope, "schema_version", 2)
        with self.assertRaisesRegex(ValueError, "durable_ingress_parent_invalid"):
            _consume_durable_ingress_parent_v1(envelope, consumer)
        object.__setattr__(envelope, "schema_version", 1)
        self.assertIs(
            _consume_durable_ingress_parent_v1(envelope, consumer),
            envelope,
        )

    def test_coherent_nested_parent_mutation_cannot_rewrite_issuance(self):
        """Catches accepting a coherently re-digested post-issuance parent."""
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        producer, result = _start_producer(ingress, self.item(28))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        self.assertIs(type(envelope), DurableIngressParentV1)

        original_producer_id = envelope.item.producer_id
        original_producer_sequence = envelope.item.producer_sequence
        original_receipt_id = envelope.receipt.producer_id
        original_receipt_sequence = envelope.receipt.producer_sequence
        original_envelope_sha256 = envelope.envelope_sha256
        object.__setattr__(envelope.item, "producer_id", "forged-producer")
        object.__setattr__(envelope.item, "producer_sequence", 999)
        object.__setattr__(envelope.receipt, "producer_id", "forged-producer")
        object.__setattr__(envelope.receipt, "producer_sequence", 999)
        object.__setattr__(
            envelope,
            "envelope_sha256",
            _digest(
                "INCI-DURABLE-INGRESS-PARENT-ENVELOPE-V1",
                {
                    "schema_version": 1,
                    "ingress_item_sha256": _item_sha256(envelope.item),
                    "parent_record_sha256": canonical_record_sha256(
                        envelope.parent
                    ),
                    "durable_receipt_sha256": _receipt_sha256(
                        envelope.receipt
                    ),
                },
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _consume_durable_ingress_parent_v1(envelope, consumer)

        object.__setattr__(envelope.item, "producer_id", original_producer_id)
        object.__setattr__(
            envelope.item,
            "producer_sequence",
            original_producer_sequence,
        )
        object.__setattr__(envelope.receipt, "producer_id", original_receipt_id)
        object.__setattr__(
            envelope.receipt,
            "producer_sequence",
            original_receipt_sequence,
        )
        object.__setattr__(
            envelope,
            "envelope_sha256",
            original_envelope_sha256,
        )
        self.assertIs(
            _consume_durable_ingress_parent_v1(envelope, consumer),
            envelope,
        )

    def test_consumer_binding_rejects_an_already_issued_parent(self):
        """Catches late binding that leaves an issued legacy envelope unbound."""
        ingress = self.ingress()
        producer, result = _start_producer(ingress, self.item(29))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)

        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _bind_durable_ingress_consumer_v1(ingress, object())
        _consume_durable_envelope_legacy_v1(envelope)

    def test_admission_wins_binding_race_and_preserves_legacy_lane(self):
        """Catches binding between queue admission and owner processing."""
        ingress = self.ingress()
        producer, result = _start_producer(ingress, self.item(32))
        _wait_until_queued(ingress)

        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _bind_durable_ingress_consumer_v1(ingress, object())
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        _consume_durable_envelope_legacy_v1(envelope)

    def test_consumer_binding_is_rejected_after_poll_close_or_fault(self):
        """Catches binding after any admission-era transition has begun."""
        polled = self.ingress()
        self.assertIsNone(
            polled.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.01,
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _bind_durable_ingress_consumer_v1(polled, object())

        closed = self.ingress()
        closed.close_inputs()
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _bind_durable_ingress_consumer_v1(closed, object())

        faulted = self.ingress(receipt_timeout_seconds=0.03)
        producer, result = _start_producer(faulted, self.item(30))
        _wait_until_queued(faulted)
        self.assertTrue(result.done.wait(1.0))
        _join(producer, result)
        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_invalid",
        ):
            _bind_durable_ingress_consumer_v1(faulted, object())

    def test_consumed_parent_registry_releases_graph_but_diagnoses_live_repeat(self):
        """Catches the registry-authority-node-parent strong retention cycle."""
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        producer, result = _start_producer(ingress, self.item(31))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        envelope_reference = weakref.ref(envelope)

        del envelope
        gc.collect()
        fresh = envelope_reference()
        self.assertIsNotNone(fresh)
        self.assertIs(
            _consume_durable_ingress_parent_v1(fresh, consumer),
            fresh,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_ingress_parent_consumed",
        ):
            _consume_durable_ingress_parent_v1(fresh, consumer)
        del fresh
        gc.collect()
        self.assertIsNone(envelope_reference())

    def test_bound_consumer_cannot_be_bypassed_through_legacy_lane(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        producer, result = _start_producer(ingress, self.item(19))
        _wait_until_queued(ingress)
        envelope = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        self.assertIs(type(envelope), DurableIngressParentV1)

        with self.assertRaisesRegex(IngressClosed, "ingress_runtime_unavailable"):
            _consume_durable_envelope_legacy_v1(envelope)
        self.assertIs(
            _consume_durable_ingress_parent_v1(envelope, consumer),
            envelope,
        )

    def test_corrupt_published_receipt_never_acknowledges_or_issues_parent(self):
        ingress = self.ingress(receipt_timeout_seconds=0.2)
        real_completion = threading.Event()
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        admitted_nodes: list[object] = []

        class CorruptingCompletion:
            def set(self) -> None:
                if not real_completion.is_set():
                    node = admitted_nodes[0]
                    receipt = node.receipt
                    self.assert_is_receipt(receipt)
                    object.__setattr__(
                        receipt,
                        "raw_ingest_seq",
                        receipt.raw_ingest_seq + 1,
                    )
                real_completion.set()

            def wait(self, timeout=None) -> bool:
                return real_completion.wait(timeout)

            @staticmethod
            def assert_is_receipt(receipt: object) -> None:
                if type(receipt) is not DurableIngressReceipt:
                    raise AssertionError("receipt was not published")

        def install_corrupting_completion(node, *args, **kwargs):
            admitted_nodes.append(node)
            node.completion = CorruptingCompletion()
            return original_put(node, *args, **kwargs)

        with mock.patch.object(
            ingress._queue,  # type: ignore[attr-defined]
            "put",
            side_effect=install_corrupting_completion,
        ):
            producer, result = _start_producer(ingress, self.item(20))
            _wait_until_queued(ingress)
            owner_error: BaseException | None = None
            outcome = None
            try:
                outcome = ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.3,
                )
            except BaseException as error:
                owner_error = error
            _join(producer, result)

        self.assertIs(type(owner_error), IngressClosed)
        self.assertIsNone(result.receipt)
        self.assertIs(type(result.error), IngressClosed)
        self.assertIsNone(outcome)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_published_receipt_cannot_be_acknowledged_after_absolute_deadline(self):
        ingress = self.ingress(receipt_timeout_seconds=1.0)
        owner_thread = threading.current_thread()
        receipt_published = threading.Event()
        real_completion = threading.Event()
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        def controlled_monotonic() -> float:
            if (
                threading.current_thread() is not owner_thread
                and receipt_published.is_set()
            ):
                return 2.0
            return 0.0

        class DeadlineCrossingCompletion:
            def set(self) -> None:
                receipt_published.set()
                real_completion.set()

            def wait(self, timeout=None) -> bool:
                return real_completion.wait(timeout)

        def install_completion(node, *args, **kwargs):
            node.completion = DeadlineCrossingCompletion()
            return original_put(node, *args, **kwargs)

        with (
            mock.patch(
                "tennis_v1.ingress.time.monotonic",
                new=controlled_monotonic,
            ),
            mock.patch.object(
                ingress._queue,  # type: ignore[attr-defined]
                "put",
                side_effect=install_completion,
            ),
        ):
            producer, result = _start_producer(ingress, self.item(21))
            _wait_until_queued(ingress)
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.3,
                )
            _join(producer, result)

        self.assertIsNone(result.receipt)
        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_owner_first_deadline_abort_still_reports_owner_unresponsive(self):
        """Catches schedule-dependent IngressClosed for the same timeout winner."""
        ingress = self.ingress(receipt_timeout_seconds=1.0)
        owner_thread = threading.current_thread()
        admitted = threading.Event()
        completion = threading.Event()
        original_put = ingress._queue.put  # type: ignore[attr-defined]

        def controlled_monotonic() -> float:
            if threading.current_thread() is owner_thread and admitted.is_set():
                return 2.0
            return 0.0

        class OwnerDrivenCompletion:
            def set(self) -> None:
                completion.set()

            def wait(self, timeout=None) -> bool:
                return completion.wait()

        def install(node, *args, **kwargs):
            node.completion = OwnerDrivenCompletion()
            outcome = original_put(node, *args, **kwargs)
            admitted.set()
            return outcome

        with (
            mock.patch(
                "tennis_v1.ingress.time.monotonic",
                new=controlled_monotonic,
            ),
            mock.patch.object(
                ingress._queue,  # type: ignore[attr-defined]
                "put",
                side_effect=install,
            ),
        ):
            producer, result = _start_producer(ingress, self.item(32))
            _wait_until_queued(ingress)
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            _join(producer, result)

        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        self.assertIsNone(result.receipt)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_owner_claimed_pre_ingest_timeout_calls_ingest_zero_times(self):
        """Catches collapsing OWNER_CLAIMED into an unstoppable ingest call."""
        ingress = self.ingress(receipt_timeout_seconds=1.0)
        owner_thread = threading.current_thread()
        owner_claimed = threading.Event()
        timeout_recorded = threading.Event()
        real_completion = threading.Event()
        real_acknowledgement = threading.Event()
        admitted_nodes: list[object] = []
        observed_states: list[str] = []
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_ingest = type(self.runtime_instance).ingest
        ingest_calls = 0

        def controlled_monotonic() -> float:
            if (
                threading.current_thread() is not owner_thread
                and owner_claimed.is_set()
            ):
                return 2.0
            return 0.0

        class ClaimBarrierLock:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.owner_entries = 0

            def __enter__(self):
                if threading.current_thread() is owner_thread:
                    self.owner_entries += 1
                    if self.owner_entries == 2:
                        if not timeout_recorded.wait(1.0):
                            raise AssertionError("pre-ingest timeout not recorded")
                self.lock.acquire()
                return self

            def __exit__(self, *_args) -> None:
                node = admitted_nodes[0]
                if (
                    threading.current_thread() is owner_thread
                    and self.owner_entries == 1
                ):
                    observed_states.append(node.state)
                    owner_claimed.set()
                self.lock.release()

        class ClaimCompletion:
            def set(self) -> None:
                real_completion.set()

            def wait(self, timeout=None) -> bool:
                if not owner_claimed.wait(1.0):
                    raise AssertionError("owner never published claimed state")
                return False

        class TimeoutAcknowledgement:
            def set(self) -> None:
                timeout_recorded.set()
                real_acknowledgement.set()

            def wait(self, timeout=None) -> bool:
                return real_acknowledgement.wait(timeout)

        def install(node, *args, **kwargs):
            admitted_nodes.append(node)
            node.completion_lock = ClaimBarrierLock()
            node.completion = ClaimCompletion()
            node.acknowledgement = TimeoutAcknowledgement()
            return original_put(node, *args, **kwargs)

        def count_ingest(runtime, value):
            nonlocal ingest_calls
            ingest_calls += 1
            return original_ingest(runtime, value)

        with (
            mock.patch(
                "tennis_v1.ingress.time.monotonic",
                new=controlled_monotonic,
            ),
            mock.patch.object(
                ingress._queue,  # type: ignore[attr-defined]
                "put",
                side_effect=install,
            ),
            mock.patch.object(
                type(self.runtime_instance),
                "ingest",
                new=count_ingest,
            ),
        ):
            producer, result = _start_producer(ingress, self.item(33))
            _wait_until_queued(ingress)
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            _join(producer, result)

        self.assertEqual(observed_states, ["OWNER_CLAIMED"])
        self.assertEqual(ingest_calls, 0)
        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_timeout_during_ingest_keeps_raw_but_issues_no_parent(self):
        """Catches a durable RAW being converted after producer timeout."""
        ingress = self.ingress(receipt_timeout_seconds=1.0)
        owner_thread = threading.current_thread()
        ingest_started = threading.Event()
        timeout_recorded = threading.Event()
        real_acknowledgement = threading.Event()
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_ingest = type(self.runtime_instance).ingest
        returned_raw: list[PersistedEvent] = []

        def controlled_monotonic() -> float:
            if (
                threading.current_thread() is not owner_thread
                and ingest_started.is_set()
            ):
                return 2.0
            return 0.0

        class IngestBarrierCompletion:
            def set(self) -> None:
                pass

            def wait(self, timeout=None) -> bool:
                if not ingest_started.wait(1.0):
                    raise AssertionError("ingest did not start")
                return False

        class TimeoutAcknowledgement:
            def set(self) -> None:
                timeout_recorded.set()
                real_acknowledgement.set()

            def wait(self, timeout=None) -> bool:
                return real_acknowledgement.wait(timeout)

        def install(node, *args, **kwargs):
            node.completion = IngestBarrierCompletion()
            node.acknowledgement = TimeoutAcknowledgement()
            return original_put(node, *args, **kwargs)

        def blocked_ingest(runtime, value):
            ingest_started.set()
            if not timeout_recorded.wait(1.0):
                raise AssertionError("producer timeout did not win")
            raw = original_ingest(runtime, value)
            returned_raw.append(raw)
            return raw

        with (
            mock.patch(
                "tennis_v1.ingress.time.monotonic",
                new=controlled_monotonic,
            ),
            mock.patch.object(
                ingress._queue,  # type: ignore[attr-defined]
                "put",
                side_effect=install,
            ),
            mock.patch.object(
                type(self.runtime_instance),
                "ingest",
                new=blocked_ingest,
            ),
        ):
            producer, result = _start_producer(ingress, self.item(34))
            _wait_until_queued(ingress)
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            _join(producer, result)

        self.assertEqual(len(returned_raw), 1)
        self.assertIs(type(returned_raw[0]), PersistedEvent)
        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        self.assertIsNone(result.receipt)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_timeout_after_raw_before_receipt_keeps_raw_without_parent(self):
        """Catches publication after timeout wins at the RAW_RETURNED barrier."""
        ingress = self.ingress(receipt_timeout_seconds=1.0)
        owner_thread = threading.current_thread()
        raw_returned = threading.Event()
        timeout_recorded = threading.Event()
        real_acknowledgement = threading.Event()
        original_put = ingress._queue.put  # type: ignore[attr-defined]
        original_digest = canonical_record_sha256
        observed_states: list[str] = []
        admitted_nodes: list[object] = []

        def controlled_monotonic() -> float:
            if (
                threading.current_thread() is not owner_thread
                and raw_returned.is_set()
            ):
                return 2.0
            return 0.0

        class RawBarrierCompletion:
            def set(self) -> None:
                pass

            def wait(self, timeout=None) -> bool:
                if not raw_returned.wait(1.0):
                    raise AssertionError("RAW_RETURNED was not published")
                return False

        class TimeoutAcknowledgement:
            def set(self) -> None:
                timeout_recorded.set()
                real_acknowledgement.set()

            def wait(self, timeout=None) -> bool:
                return real_acknowledgement.wait(timeout)

        def install(node, *args, **kwargs):
            admitted_nodes.append(node)
            node.completion = RawBarrierCompletion()
            node.acknowledgement = TimeoutAcknowledgement()
            return original_put(node, *args, **kwargs)

        def block_receipt_digest(value):
            node = admitted_nodes[0]
            observed_states.append(node.state)
            raw_returned.set()
            if not timeout_recorded.wait(1.0):
                raise AssertionError("producer timeout did not win")
            return original_digest(value)

        with (
            mock.patch(
                "tennis_v1.ingress.time.monotonic",
                new=controlled_monotonic,
            ),
            mock.patch.object(
                ingress._queue,  # type: ignore[attr-defined]
                "put",
                side_effect=install,
            ),
            mock.patch(
                "tennis_v1.ingress.canonical_record_sha256",
                side_effect=block_receipt_digest,
            ),
        ):
            producer, result = _start_producer(ingress, self.item(35))
            _wait_until_queued(ingress)
            with self.assertRaisesRegex(
                IngressClosed,
                "ingress_runtime_unavailable",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            _join(producer, result)

        self.assertEqual(observed_states, ["RAW_RETURNED"])
        self.assertIs(type(result.error), IngressOwnerUnresponsive)
        self.assertIsNone(result.receipt)
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_legacy_drain_uses_same_parent_path_and_returns_bare_raw(self):
        ingress = self.ingress()
        producer, result = _start_producer(ingress, self.item(9))
        _wait_until_queued(ingress)
        raw = ingress.drain_one(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        self.assertIs(type(raw), PersistedEvent)
        self.assertEqual(result.receipt.raw_ingest_seq, raw.ingest_seq)
        self.assertEqual(
            result.receipt.raw_record_sha256,
            canonical_record_sha256(raw),
        )

    def test_receipt_timeout_never_issues_parent_for_durable_raw(self):
        ingress = self.ingress(receipt_timeout_seconds=0.03)
        producer, result = _start_producer(ingress, self.item(10))
        _wait_until_queued(ingress)
        self.assertTrue(result.done.wait(1.0))
        self.assertIs(type(result.error), IngressOwnerUnresponsive)

        with self.assertRaisesRegex(
            IngressClosed,
            "ingress_runtime_unavailable",
        ):
            ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
        producer.join()
        self.assertEqual(ingress.halt_reason, "owner_unresponsive")
        self.assertFalse(ingress._terminal_written)  # type: ignore[attr-defined]

    def test_external_halt_terminal_rejects_queued_admitted_work(self):
        ingress = self.ingress()
        self.assertIsNone(ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.01,
        ))
        producer, result = _start_producer(ingress, self.item(22))
        _wait_until_queued(ingress)

        with self.assertRaisesRegex(IngressClosed, "ingress_not_between_drains"):
            ingress.close_external_halt_terminal(self.runtime_instance)
        self.assertEqual(ingress._queue.qsize(), 1)  # type: ignore[attr-defined]

        parent = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        _join(producer, result)
        self.assertIs(type(parent), DurableIngressParentV1)
        terminal = ingress.close_external_halt_terminal(self.runtime_instance)
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)
        self.assertEqual(terminal.terminal_reason, "operator_halt")

    def test_terminal_envelope_binds_terminal_coordinate_and_consumes_once(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        ingress.close_inputs()
        terminal = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)
        self.assertEqual(terminal.schema_version, 1)
        self.assertEqual(terminal.terminal_reason, "operator_stop")
        self.assertIs(type(terminal.terminal), PersistedEvent)
        self.assertEqual(
            terminal.terminal_record_sha256,
            canonical_record_sha256(terminal.terminal),
        )
        coordinate = _resolve_evidence_terminal_coordinate_v1(terminal)
        self.assertIs(type(coordinate), DurableCausalOrderCoordinateV1)
        self.assertEqual(coordinate.stage, "EVIDENCE_TERMINAL_ISSUED")
        self.assertEqual(
            coordinate.coordinate_sha256,
            terminal.evidence_terminal_coordinate_sha256,
        )
        expected_coordinate = _digest(
            "INCI-DURABLE-CAUSAL-ORDER-COORDINATE-V1",
            {
                "schema_version": coordinate.schema_version,
                "session_id": coordinate.session_id,
                "ingress_identity_sha256": coordinate.ingress_identity_sha256,
                "runtime_identity_sha256": coordinate.runtime_identity_sha256,
                "stage": coordinate.stage,
                "ordinal": coordinate.ordinal,
            },
        )
        self.assertEqual(coordinate.coordinate_sha256, expected_coordinate)
        expected_terminal = _digest(
            "INCI-DURABLE-EVIDENCE-TERMINAL-ENVELOPE-V1",
            {
                "schema_version": terminal.schema_version,
                "session_id": terminal.session_id,
                "ingress_identity_sha256": terminal.ingress_identity_sha256,
                "terminal_record_sha256": terminal.terminal_record_sha256,
                "terminal_ingest_seq": terminal.terminal_ingest_seq,
                "evidence_terminal_coordinate_sha256": (
                    terminal.evidence_terminal_coordinate_sha256
                ),
                "terminal_reason": terminal.terminal_reason,
            },
        )
        self.assertEqual(terminal.envelope_sha256, expected_terminal)
        self.assertIs(
            _consume_durable_evidence_terminal_v1(terminal, consumer),
            terminal,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_consumed",
        ):
            _consume_durable_evidence_terminal_v1(terminal, consumer)

    def test_corrupt_terminal_or_coordinate_is_rejected_without_consumption(self):
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        ingress.close_inputs()
        terminal = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)
        coordinate = _resolve_evidence_terminal_coordinate_v1(terminal)

        object.__setattr__(coordinate, "stage", "SOURCE_CLOSE_COMPLETE")
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_invalid",
        ):
            _resolve_evidence_terminal_coordinate_v1(terminal)
        object.__setattr__(coordinate, "stage", "EVIDENCE_TERMINAL_ISSUED")

        object.__setattr__(terminal, "terminal_reason", "operator_halt")
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_invalid",
        ):
            _consume_durable_evidence_terminal_v1(terminal, consumer)
        object.__setattr__(terminal, "terminal_reason", "operator_stop")
        self.assertIs(
            _consume_durable_evidence_terminal_v1(terminal, consumer),
            terminal,
        )

    def test_coherent_coordinate_mutation_cannot_rewrite_terminal_issuance(self):
        """Catches a re-digested coordinate forging terminal causal order."""
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        ingress.close_inputs()
        terminal = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)
        coordinate = _resolve_evidence_terminal_coordinate_v1(terminal)

        original_session_id = coordinate.session_id
        original_ordinal = coordinate.ordinal
        original_coordinate_sha256 = coordinate.coordinate_sha256
        original_terminal_coordinate_sha256 = (
            terminal.evidence_terminal_coordinate_sha256
        )
        original_terminal_envelope_sha256 = terminal.envelope_sha256
        object.__setattr__(coordinate, "session_id", "forged-session")
        object.__setattr__(coordinate, "ordinal", coordinate.ordinal + 1000)
        object.__setattr__(
            coordinate,
            "coordinate_sha256",
            _digest(
                "INCI-DURABLE-CAUSAL-ORDER-COORDINATE-V1",
                {
                    "schema_version": coordinate.schema_version,
                    "session_id": coordinate.session_id,
                    "ingress_identity_sha256": (
                        coordinate.ingress_identity_sha256
                    ),
                    "runtime_identity_sha256": (
                        coordinate.runtime_identity_sha256
                    ),
                    "stage": coordinate.stage,
                    "ordinal": coordinate.ordinal,
                },
            ),
        )
        object.__setattr__(
            terminal,
            "evidence_terminal_coordinate_sha256",
            coordinate.coordinate_sha256,
        )
        object.__setattr__(
            terminal,
            "envelope_sha256",
            _digest(
                "INCI-DURABLE-EVIDENCE-TERMINAL-ENVELOPE-V1",
                {
                    "schema_version": terminal.schema_version,
                    "session_id": terminal.session_id,
                    "ingress_identity_sha256": (
                        terminal.ingress_identity_sha256
                    ),
                    "terminal_record_sha256": terminal.terminal_record_sha256,
                    "terminal_ingest_seq": terminal.terminal_ingest_seq,
                    "evidence_terminal_coordinate_sha256": (
                        terminal.evidence_terminal_coordinate_sha256
                    ),
                    "terminal_reason": terminal.terminal_reason,
                },
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_invalid",
        ):
            _consume_durable_evidence_terminal_v1(terminal, consumer)

        object.__setattr__(coordinate, "session_id", original_session_id)
        object.__setattr__(coordinate, "ordinal", original_ordinal)
        object.__setattr__(
            coordinate,
            "coordinate_sha256",
            original_coordinate_sha256,
        )
        object.__setattr__(
            terminal,
            "evidence_terminal_coordinate_sha256",
            original_terminal_coordinate_sha256,
        )
        object.__setattr__(
            terminal,
            "envelope_sha256",
            original_terminal_envelope_sha256,
        )
        self.assertIs(
            _consume_durable_evidence_terminal_v1(terminal, consumer),
            terminal,
        )

    def test_consumed_terminal_registry_releases_graph_but_keeps_fresh_live(self):
        """Catches premature fresh cleanup and retained consumed terminals."""
        ingress = self.ingress()
        consumer = object()
        _bind_durable_ingress_consumer_v1(ingress, consumer)
        ingress.close_inputs()
        terminal = ingress.drain_one_parent(
            self.runtime_instance,
            timeout_seconds=0.2,
        )
        self.assertIs(type(terminal), DurableEvidenceTerminalV1)
        terminal_reference = weakref.ref(terminal)

        del terminal
        gc.collect()
        fresh = terminal_reference()
        self.assertIsNotNone(fresh)
        self.assertIs(
            _consume_durable_evidence_terminal_v1(fresh, consumer),
            fresh,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_evidence_terminal_consumed",
        ):
            _consume_durable_evidence_terminal_v1(fresh, consumer)
        del fresh
        gc.collect()
        self.assertIsNone(terminal_reference())

    def test_terminal_envelope_issuance_uncertainty_is_never_retried(self):
        ingress = self.ingress()
        ingress.close_inputs()

        with mock.patch(
            "tennis_v1.ingress._issue_durable_evidence_terminal_v1",
            side_effect=RuntimeError("controlled-envelope-uncertainty"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "controlled-envelope-uncertainty",
            ):
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )

        self.assertEqual(self._close_reasons, [(True, "operator_stop")])
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
        self.assertEqual(self._close_reasons, [(True, "operator_stop")])

    def test_terminal_register_then_raise_rolls_back_and_releases_graph(self):
        """Catches a hidden terminal authority after ambiguous registration."""
        ingress = self.ingress()
        envelope_references: list[
            weakref.ReferenceType[DurableEvidenceTerminalV1]
        ] = []
        coordinate_references: list[
            weakref.ReferenceType[DurableCausalOrderCoordinateV1]
        ] = []
        original_register = ingress_module._register_envelope_authority
        secret = "controlled-terminal-register-uncertainty"

        def register_then_raise(envelope, authority):
            original_register(envelope, authority)
            envelope_references.append(weakref.ref(envelope))
            coordinate_references.append(weakref.ref(authority.coordinate))
            raise RuntimeError(secret)

        ingress.close_inputs()
        owner_error: BaseException | None = None
        with mock.patch.object(
            ingress_module,
            "_register_envelope_authority",
            new=register_then_raise,
        ):
            try:
                ingress.drain_one_parent(
                    self.runtime_instance,
                    timeout_seconds=0.2,
                )
            except BaseException as error:
                owner_error = error

        self.assertIs(type(owner_error), RuntimeError)
        self.assertEqual(str(owner_error), secret)
        self.assertEqual(len(envelope_references), 1)
        self.assertEqual(len(coordinate_references), 1)
        leaked_envelope = envelope_references[0]()
        self.assertIsNotNone(leaked_envelope)
        self.assertIsNone(
            ingress_module._lookup_envelope_authority(leaked_envelope)
        )
        self.assertEqual(self._close_reasons, [(True, "operator_stop")])
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.enqueue(self.item(91))
        with self.assertRaisesRegex(IngressClosed, "ingress_closed"):
            ingress.drain_one_parent(
                self.runtime_instance,
                timeout_seconds=0.2,
            )
        self.assertEqual(self._close_reasons, [(True, "operator_stop")])

        del owner_error
        del leaked_envelope
        gc.collect()
        self.assertIsNone(envelope_references[0]())
        self.assertIsNone(coordinate_references[0]())

    def test_source_close_coordinate_is_structurally_unavailable_before_a5(self):
        ingress = self.ingress()
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_coordinate_invalid",
        ):
            _issue_source_close_complete_coordinate_v1(ingress, object())


class DurableParentStaticBoundaryTests(unittest.TestCase):
    @staticmethod
    def _ingress_function(name: str) -> ast.FunctionDef:
        source_path = Path(__file__).parents[2] / "tennis_v1" / "ingress.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        matches = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        if len(matches) != 1:
            raise AssertionError(f"expected one ingress function {name}")
        return matches[0]

    def test_pre_a5_proof_path_contains_complete_fixed_private_commit_seam(self):
        """Catches restoring the permanent post-import failure stub."""
        source_path = Path(__file__).parents[2] / "tennis_v1" / "ingress.py"
        source = source_path.read_text(encoding="utf-8")
        function = self._ingress_function(
            "_issue_durable_causal_precedes_for_deferred_commit_v1"
        )
        segment = ast.get_source_segment(source, function)
        self.assertIsNotNone(segment)
        called_names = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        lifecycle_writes = [
            node
            for node in ast.walk(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Attribute)
                and target.attr == "lifecycle"
                for target in (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else (node.target,)
                )
            )
        ]
        for required in (
            "_DEFERRED_EMERGENCY_CLAIM_COMMIT_LOCK_V1",
            "_resolve_deferred_emergency_source_close_claim_for_ingress_commit_v1",
            "_resolve_deferred_emergency_pending_for_ingress_commit_v1",
        ):
            self.assertIn(required, segment)
        self.assertIn("_deferred_commit_transition_kernel_v1", called_names)
        self.assertIn("_register_proof_authority", called_names)
        self.assertIn("_unregister_proof_authority", called_names)
        self.assertGreaterEqual(len(lifecycle_writes), 4)

    def test_subject_issuer_uses_fixed_locks_index_and_shared_repeat_kernel(self):
        """Catches a scalar/weak/reimplemented production repeat path."""
        source_path = Path(__file__).parents[2] / "tennis_v1" / "ingress.py"
        source = source_path.read_text(encoding="utf-8")
        function = self._ingress_function(
            "_issue_deferred_emergency_commit_subject_v1"
        )
        segment = ast.get_source_segment(source, function)
        self.assertIsNotNone(segment)
        called_names = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertIn("_deferred_subject_repeat_kernel_v1", called_names)
        self.assertIn("_register_subject_authority", called_names)
        self.assertIn("_unregister_subject_authority", called_names)
        attributes = {
            node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)
        }
        self.assertIn("_causal_subject_lock", attributes)
        self.assertIn("_deferred_subject_by_pending", attributes)
        self.assertIn("publication_lock", attributes)

    def test_proof_finalizer_owns_only_proof_under_fixed_lock_order(self):
        """Catches controller-state mutation or a lockless proof close seam."""
        helper = self._ingress_function(
            "_finalize_durable_causal_precedes_proof_v1"
        )
        attributes = {
            node.attr for node in ast.walk(helper) if isinstance(node, ast.Attribute)
        }
        called_names = {
            node.func.id
            for node in ast.walk(helper)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        lifecycle_writes = [
            target
            for node in ast.walk(helper)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and target.attr == "lifecycle"
        ]
        self.assertIn("_causal_subject_lock", attributes)
        self.assertIn("publication_lock", attributes)
        self.assertIn(
            "_deferred_proof_finalization_kernel_v1",
            called_names,
        )
        self.assertEqual(len(lifecycle_writes), 1)
        self.assertIs(type(lifecycle_writes[0].value), ast.Name)
        self.assertEqual(lifecycle_writes[0].value.id, "proof_authority")

    def test_source_close_claim_surface_is_reserved_only_to_a5_owner(self):
        root = Path(__file__).parents[2]
        a5_owned_definitions = {
            "DeferredEmergencySourceCloseClaimV1",
            "claim_deferred_emergency_source_close_v1",
            "consume_deferred_emergency_source_close_before_terminal_v1",
        }
        discovered: list[tuple[str, str]] = []
        for source_path in (
            *sorted((root / "tennis_v1").glob("*.py")),
            *sorted((root / "inci_tennis_runtime").glob("*.py")),
        ):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            discovered.extend(
                (source_path.relative_to(root).as_posix(), node.name)
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in a5_owned_definitions
            )
        self.assertTrue(
            all(
                path == "inci_tennis_runtime/shadow_sources.py"
                for path, _ in discovered
            ),
            discovered,
        )

    def test_ingress_addition_has_no_external_authority_or_dynamic_callback(self):
        source_path = Path(__file__).parents[2] / "tennis_v1" / "ingress.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        forbidden_imports = {
            "requests",
            "socket",
            "executor",
            "kalshi_client",
            "market_data",
        }
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue(forbidden_imports.isdisjoint(imported))
        source = source_path.read_text(encoding="utf-8")
        for forbidden in ("--demo", "--live", "/portfolio/orders", "POST"):
            self.assertNotIn(forbidden, source)


class DeferredCausalCommitKernelTests(unittest.TestCase):
    """Behavioral evidence for the unreachable production reservation seam."""

    @staticmethod
    def _states(fixture: object) -> tuple[str, str, str]:
        return tuple(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(cell)
            for cell in (
                fixture.claim,
                fixture.subject,
                fixture.pending,
            )
        )

    def test_sealed_kernel_atomically_commits_exact_distinct_cells_once(self):
        """Catches a partial or retryable claim/subject/pending reservation."""
        fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        self.assertEqual(
            len(
                {
                    type(fixture.claim),
                    type(fixture.subject),
                    type(fixture.pending),
                }
            ),
            3,
        )
        self.assertEqual(self._states(fixture), ("CLAIMED", "FRESH", "FRESH"))

        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )

        self.assertIs(result.claim, fixture.claim)
        self.assertIs(result.subject, fixture.subject)
        self.assertIs(result.pending, fixture.pending)
        self.assertNotIn(
            type(result.proof),
            {
                type(fixture.claim),
                type(fixture.subject),
                type(fixture.pending),
            },
        )
        self.assertEqual(
            self._states(fixture),
            ("CONSUMED", "CONSUMED", "COMMIT_RESERVED"),
        )
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                result.proof
            ),
            "ISSUED",
        )
        observation = (
            ingress_module._observe_sealed_deferred_commit_fixture_v1(fixture)
        )
        self.assertIs(observation.reserved_claim, fixture.claim)
        self.assertIs(observation.reserved_subject, fixture.subject)
        self.assertIs(observation.reserved_terminal, fixture.terminal)
        self.assertIs(observation.reserved_causal_proof, result.proof)
        self.assertIs(
            observation.reserved_completion_scope,
            fixture.completion_scope,
        )
        self.assertIs(observation.scope_causal_proof, result.proof)
        self.assertIs(observation.scope_reservation_committed, True)
        self.assertEqual(observation.scope_lifecycle, "RESERVATION_COMMITTED")
        self.assertEqual(
            result.lock_order,
            ("controller_publication", "ingress_subject", "a5_claim"),
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._run_sealed_deferred_commit_kernel_v1(
                fixture.claim,
                fixture.subject,
                fixture.pending,
            )

    def test_sealed_mismatch_and_order_matrix_consumes_nothing(self):
        """Catches validation failures that mutate any reservation participant."""
        matrix = (
            ingress_module._issue_sealed_deferred_commit_kernel_matrix_v1()
        )
        expected_codes = {
            "wrong_session": "durable_causal_subject_mismatch",
            "wrong_runtime": "durable_causal_subject_mismatch",
            "wrong_ingress": "durable_causal_subject_mismatch",
            "wrong_controller": "durable_causal_subject_mismatch",
            "wrong_pending_parent": "durable_causal_subject_mismatch",
            "wrong_publication_epoch": "durable_causal_subject_mismatch",
            "wrong_owner": "durable_causal_subject_mismatch",
            "wrong_claim_lifecycle": "durable_causal_subject_mismatch",
            "wrong_subject_lifecycle": "durable_causal_subject_mismatch",
            "wrong_pending_lifecycle": "durable_causal_subject_mismatch",
            "wrong_scope_lifecycle": "durable_causal_subject_mismatch",
            "wrong_proof_lifecycle": "durable_causal_subject_mismatch",
            "wrong_before_stage": "durable_causal_order_invalid",
            "wrong_after_stage": "durable_causal_order_invalid",
            "equal_order": "durable_causal_order_invalid",
            "reverse_order": "durable_causal_order_invalid",
            "zero_before_order": "durable_causal_order_invalid",
            "negative_before_order": "durable_causal_order_invalid",
            "bool_before_order": "durable_causal_order_invalid",
            "overflow_after_order": "durable_causal_order_invalid",
        }
        self.assertEqual(
            {fixture.case_name for fixture in matrix},
            set(expected_codes),
        )
        for fixture in matrix:
            with self.subTest(case=fixture.case_name):
                before = (
                    ingress_module._observe_sealed_deferred_commit_fixture_v1(
                        fixture
                    )
                )
                with self.assertRaisesRegex(
                    ValueError,
                    expected_codes[fixture.case_name],
                ):
                    ingress_module._run_sealed_deferred_commit_kernel_v1(
                        fixture.claim,
                        fixture.subject,
                        fixture.pending,
                    )
                self.assertEqual(
                    ingress_module._observe_sealed_deferred_commit_fixture_v1(
                        fixture
                    ),
                    before,
                )

    def test_sealed_allocation_and_registration_failures_are_retryable(self):
        """Catches proof preparation failures that consume governed cells."""
        allocation_fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        with mock.patch.object(
            ingress_module,
            "_allocate_sealed_deferred_commit_proof_cell_v1",
            side_effect=MemoryError("controlled-proof-allocation"),
        ):
            with self.assertRaisesRegex(
                MemoryError,
                "controlled-proof-allocation",
            ):
                ingress_module._run_sealed_deferred_commit_kernel_v1(
                    allocation_fixture.claim,
                    allocation_fixture.subject,
                    allocation_fixture.pending,
                )
        self.assertEqual(
            self._states(allocation_fixture),
            ("CLAIMED", "FRESH", "FRESH"),
        )

        registration_fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        allocated: list[object] = []
        original_allocate = (
            ingress_module._allocate_sealed_deferred_commit_proof_cell_v1
        )
        original_register = (
            ingress_module._register_sealed_deferred_commit_cell_authority_v1
        )

        def capture_allocate(*args, **kwargs):
            proof = original_allocate(*args, **kwargs)
            allocated.append(proof)
            return proof

        def register_then_fail(*args, **kwargs):
            original_register(*args, **kwargs)
            raise RuntimeError("controlled-proof-registration")

        with (
            mock.patch.object(
                ingress_module,
                "_allocate_sealed_deferred_commit_proof_cell_v1",
                side_effect=capture_allocate,
            ),
            mock.patch.object(
                ingress_module,
                "_register_sealed_deferred_commit_cell_authority_v1",
                side_effect=register_then_fail,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "controlled-proof-registration",
            ):
                ingress_module._run_sealed_deferred_commit_kernel_v1(
                    registration_fixture.claim,
                    registration_fixture.subject,
                    registration_fixture.pending,
                )
        self.assertEqual(len(allocated), 1)
        self.assertIsNone(
            ingress_module._lookup_sealed_deferred_commit_cell_authority_v1(
                allocated[0]
            )
        )
        self.assertEqual(
            self._states(registration_fixture),
            ("CLAIMED", "FRESH", "FRESH"),
        )

    def test_sealed_and_production_authority_lanes_never_mix(self):
        """Catches conversion between sealed evidence and production authority."""
        fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        rebuilt_production_subject = object.__new__(
            DeferredEmergencyCommitSubjectV1
        )
        with self.assertRaisesRegex(
            TypeError,
            "exact sealed deferred commit subject cell required",
        ):
            ingress_module._run_sealed_deferred_commit_kernel_v1(
                fixture.claim,
                rebuilt_production_subject,
                fixture.pending,
            )
        for permuted in (
            (fixture.subject, fixture.claim, fixture.pending),
            (fixture.claim, fixture.pending, fixture.subject),
        ):
            with self.subTest(permuted=tuple(type(v).__name__ for v in permuted)):
                with self.assertRaisesRegex(
                    TypeError,
                    "exact sealed deferred commit .* cell required",
                ):
                    ingress_module._run_sealed_deferred_commit_kernel_v1(
                        *permuted
                    )
        rebuilt_claim = object.__new__(type(fixture.claim))
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._run_sealed_deferred_commit_kernel_v1(
                rebuilt_claim,
                fixture.subject,
                fixture.pending,
            )
        with self.assertRaisesRegex(
            TypeError,
            "exact DeferredEmergencyCommitSubjectV1 required",
        ):
            ingress_module._issue_durable_causal_precedes_for_deferred_commit_v1(
                claim=fixture.claim,
                subject=fixture.subject,
            )

    def test_sealed_commit_registry_releases_fixture_graph(self):
        """Catches a weak-registry authority cycle retaining sealed fixtures."""
        fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )
        references = tuple(
            weakref.ref(value)
            for value in (
                fixture.claim,
                fixture.subject,
                fixture.pending,
                fixture.completion_scope,
                fixture.terminal,
                result.proof,
            )
        )
        del result
        del fixture
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))

    def test_sealed_proof_success_requires_consumed_terminal_and_closes_once(self):
        fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._finalize_sealed_deferred_commit_proof_v1(
                fixture,
                result.proof,
                append_succeeded=True,
            )
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                result.proof
            ),
            "ISSUED",
        )

        ingress_module._consume_sealed_deferred_commit_terminal_v1(fixture)
        self.assertIsNone(
            ingress_module._finalize_sealed_deferred_commit_proof_v1(
                fixture,
                result.proof,
                append_succeeded=True,
            )
        )
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                result.proof
            ),
            "CONSUMED_BY_FUTURE_COMPLETION",
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_proof_consumed",
        ):
            ingress_module._finalize_sealed_deferred_commit_proof_v1(
                fixture,
                result.proof,
                append_succeeded=True,
            )

    def test_sealed_proof_failure_accepts_issued_or_consumed_terminal(self):
        for terminal_consumed in (False, True):
            with self.subTest(terminal_consumed=terminal_consumed):
                fixture = (
                    ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
                )
                result = ingress_module._run_sealed_deferred_commit_kernel_v1(
                    fixture.claim,
                    fixture.subject,
                    fixture.pending,
                )
                if terminal_consumed:
                    ingress_module._consume_sealed_deferred_commit_terminal_v1(
                        fixture
                    )
                self.assertIsNone(
                    ingress_module._finalize_sealed_deferred_commit_proof_v1(
                        fixture,
                        result.proof,
                        append_succeeded=False,
                    )
                )
                self.assertEqual(
                    ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                        result.proof
                    ),
                    "APPEND_FAILED_CLOSED",
                )

    def test_sealed_proof_foreign_or_rebuilt_mismatch_consumes_nothing(self):
        fixture = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )
        foreign = (
            ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        )
        foreign_result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            foreign.claim,
            foreign.subject,
            foreign.pending,
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._finalize_sealed_deferred_commit_proof_v1(
                fixture,
                foreign_result.proof,
                append_succeeded=False,
            )
        rebuilt = object.__new__(type(result.proof))
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._finalize_sealed_deferred_commit_proof_v1(
                fixture,
                rebuilt,
                append_succeeded=False,
            )
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                result.proof
            ),
            "ISSUED",
        )
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                foreign_result.proof
            ),
            "ISSUED",
        )

    def test_production_proof_finalizers_exact_type_reject_before_resolution(self):
        rebuilt_proof = object.__new__(DurableCausalPrecedesProofV1)
        rebuilt_subject = object.__new__(DeferredEmergencyCommitSubjectV1)
        rebuilt_terminal = object.__new__(DurableEvidenceTerminalV1)
        for finalizer in (
            _consume_durable_causal_precedes_proof_after_deferred_append_v1,
            _close_durable_causal_precedes_proof_after_deferred_append_failure_v1,
        ):
            with self.subTest(finalizer=finalizer.__name__):
                with self.assertRaisesRegex(
                    TypeError,
                    "exact DurableCausalPrecedesProofV1 required",
                ):
                    finalizer(
                        object(),
                        subject=rebuilt_subject,
                        controller=object(),
                        pending=object(),
                        terminal=rebuilt_terminal,
                    )
                with self.assertRaisesRegex(
                    TypeError,
                    "exact DeferredEmergencyCommitSubjectV1 required",
                ):
                    finalizer(
                        rebuilt_proof,
                        subject=object(),
                        controller=object(),
                        pending=object(),
                        terminal=rebuilt_terminal,
                    )
                with self.assertRaisesRegex(
                    TypeError,
                    "exact DurableEvidenceTerminalV1 required",
                ):
                    finalizer(
                        rebuilt_proof,
                        subject=rebuilt_subject,
                        controller=object(),
                        pending=object(),
                        terminal=object(),
                    )
                with self.assertRaisesRegex(
                    ValueError,
                    "durable_causal_subject_mismatch",
                ):
                    finalizer(
                        rebuilt_proof,
                        subject=rebuilt_subject,
                        controller=object(),
                        pending=object(),
                        terminal=rebuilt_terminal,
                    )


class DeferredSubjectRepeatKernelTests(unittest.TestCase):
    def test_initial_issue_and_same_scope_repeat_return_one_exact_subject(self):
        """Catches duplicate subjects for the same active completion scope."""
        fixture = ingress_module._issue_sealed_deferred_subject_fixture_v1()
        before = ingress_module._observe_sealed_deferred_subject_fixture_v1(
            fixture
        )
        subject = ingress_module._issue_sealed_deferred_subject_v1(
            fixture,
            before.current_scope,
            before.terminal,
        )
        repeated = ingress_module._issue_sealed_deferred_subject_v1(
            fixture,
            before.current_scope,
            before.terminal,
        )
        self.assertIs(repeated, subject)
        after = ingress_module._observe_sealed_deferred_subject_fixture_v1(
            fixture
        )
        self.assertIs(after.subject, subject)
        self.assertEqual(after.subject_lifecycle, "FRESH")

    def test_cleared_unreserved_scope_rebind_returns_same_subject(self):
        """Catches corrected-claim retry allocating a second subject."""
        fixture = ingress_module._issue_sealed_deferred_subject_fixture_v1()
        initial = ingress_module._observe_sealed_deferred_subject_fixture_v1(
            fixture
        )
        subject = ingress_module._issue_sealed_deferred_subject_v1(
            fixture,
            initial.current_scope,
            initial.terminal,
        )
        corrected_scope = (
            ingress_module._prepare_sealed_corrected_completion_scope_v1(
                fixture
            )
        )
        repeated = ingress_module._issue_sealed_deferred_subject_v1(
            fixture,
            corrected_scope,
            initial.terminal,
        )
        self.assertIs(repeated, subject)
        after = ingress_module._observe_sealed_deferred_subject_fixture_v1(
            fixture
        )
        self.assertIs(after.current_scope, corrected_scope)
        self.assertIs(after.subject, subject)

    def test_alternate_terminal_uncleared_reserved_and_consumed_reject(self):
        """Catches unsafe rebinds and post-consumption reissuance."""
        fixture = ingress_module._issue_sealed_deferred_subject_fixture_v1()
        initial = ingress_module._observe_sealed_deferred_subject_fixture_v1(
            fixture
        )
        subject = ingress_module._issue_sealed_deferred_subject_v1(
            fixture,
            initial.current_scope,
            initial.terminal,
        )
        foreign = ingress_module._issue_sealed_deferred_subject_fixture_v1()
        foreign_terminal = (
            ingress_module._observe_sealed_deferred_subject_fixture_v1(
                foreign
            ).terminal
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._issue_sealed_deferred_subject_v1(
                fixture,
                initial.current_scope,
                foreign_terminal,
            )

        uncleared_scope = (
            ingress_module._prepare_sealed_uncleared_completion_scope_v1(
                fixture
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._issue_sealed_deferred_subject_v1(
                fixture,
                uncleared_scope,
                initial.terminal,
            )
        self.assertIs(
            ingress_module._observe_sealed_deferred_subject_fixture_v1(
                fixture
            ).current_scope,
            initial.current_scope,
        )

        reserved = ingress_module._issue_sealed_deferred_subject_fixture_v1()
        reserved_before = (
            ingress_module._observe_sealed_deferred_subject_fixture_v1(reserved)
        )
        ingress_module._issue_sealed_deferred_subject_v1(
            reserved,
            reserved_before.current_scope,
            reserved_before.terminal,
        )
        reserved_scope = (
            ingress_module._prepare_sealed_reserved_completion_scope_v1(
                reserved
            )
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._issue_sealed_deferred_subject_v1(
                reserved,
                reserved_scope,
                reserved_before.terminal,
            )

        ingress_module._consume_sealed_deferred_subject_v1(fixture, subject)
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            ingress_module._issue_sealed_deferred_subject_v1(
                fixture,
                initial.current_scope,
                initial.terminal,
            )

    def test_subject_registration_failure_leaves_no_subject_and_can_retry(self):
        """Catches a leaked or half-indexed subject after registry failure."""
        fixture = ingress_module._issue_sealed_deferred_subject_fixture_v1()
        before = ingress_module._observe_sealed_deferred_subject_fixture_v1(
            fixture
        )
        allocated: list[object] = []
        original_allocate = ingress_module._allocate_sealed_deferred_subject_v1
        original_register = (
            ingress_module._register_sealed_deferred_subject_authority_v1
        )

        def capture_allocate():
            value = original_allocate()
            allocated.append(value)
            return value

        def register_then_fail(*args, **kwargs):
            original_register(*args, **kwargs)
            raise RuntimeError("controlled-subject-registration")

        with (
            mock.patch.object(
                ingress_module,
                "_allocate_sealed_deferred_subject_v1",
                side_effect=capture_allocate,
            ),
            mock.patch.object(
                ingress_module,
                "_register_sealed_deferred_subject_authority_v1",
                side_effect=register_then_fail,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "controlled-subject-registration",
            ):
                ingress_module._issue_sealed_deferred_subject_v1(
                    fixture,
                    before.current_scope,
                    before.terminal,
                )
        self.assertEqual(len(allocated), 1)
        self.assertIsNone(
            ingress_module._lookup_sealed_deferred_subject_authority_v1(
                allocated[0]
            )
        )
        self.assertIsNone(
            ingress_module._observe_sealed_deferred_subject_fixture_v1(
                fixture
            ).subject
        )
        subject = ingress_module._issue_sealed_deferred_subject_v1(
            fixture,
            before.current_scope,
            before.terminal,
        )
        self.assertIsNotNone(subject)


class Round19PreparedIngressContractRedTests(unittest.TestCase):
    """Stage-A RED for the Round-19 ingress-owned prepared commit seam."""

    def _required_ingress_symbol(self, name: str) -> object:
        value = getattr(ingress_module, name, None)
        self.assertIsNotNone(value, f"missing Stage-A ingress symbol: {name}")
        return value

    @staticmethod
    def _live_production_prepared_graph() -> dict[str, object]:
        ingress = BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.1,
            receipt_timeout_seconds=0.1,
        )
        proof = object.__new__(DurableCausalPrecedesProofV1)
        before = object.__new__(DurableCausalOrderCoordinateV1)
        after = object.__new__(DurableCausalOrderCoordinateV1)
        subject = object.__new__(DeferredEmergencyCommitSubjectV1)
        terminal = object.__new__(DurableEvidenceTerminalV1)
        controller = mock.Mock()
        pending = mock.Mock()
        pending_authority = mock.Mock()
        proof_authority = ingress_module._ProofAuthorityV1(
            before=before,
            after=after,
            subject=subject,
            claim=mock.Mock(),
            pending=pending,
            terminal=terminal,
            owner_pid=ingress_module.getpid(),
            owner_thread=threading.current_thread(),
            issuance=mock.Mock(),
            lifecycle="ISSUED",
        )
        ingress_module._register_proof_authority(proof, proof_authority)
        publication_lock = threading.RLock()
        prepared = (
            ingress_module._build_prepared_deferred_emergency_causal_proof_commit_v1(
                ingress=ingress,
                controller=controller,
                pending_authority=pending_authority,
                completion_scope=mock.Mock(),
                proof_authority=proof_authority,
                subject_authority=mock.Mock(),
                terminal_authority=mock.Mock(),
                publication_lock=publication_lock,
            )
        )
        ingress_module._register_prepared_deferred_emergency_causal_proof_commit_v1(
            proof,
            prepared,
        )
        object.__setattr__(proof, "_proof_authority", proof_authority)
        object.__setattr__(proof, "_prepared_commit", prepared)
        pending_authority.reserved_causal_proof = proof
        prepared.lifecycle = "PREPARED"
        prepared.success_armed = True
        registry_cell = ingress_module._PREPARED_DEFERRED_COMMIT_ENTRIES_V1[
            id(prepared)
        ]
        registry_cell.lifecycle = "LIVE"
        registry_cell.success_armed = True
        return {
            "ingress": ingress,
            "proof": proof,
            "prepared": prepared,
            "controller": controller,
            "pending": pending,
            "pending_authority": pending_authority,
            "subject": subject,
            "terminal": terminal,
            "proof_authority": proof_authority,
            "registry_cell": registry_cell,
            "publication_lock": publication_lock,
            "ingress_lock": ingress._causal_subject_lock,
        }

    def test_prepared_record_and_three_helper_abis_are_exact(self):
        prepared_type = self._required_ingress_symbol(
            "_PreparedDeferredEmergencyCausalProofCommitV1",
        )
        self.assertEqual(
            tuple(
                slot
                for slot in prepared_type.__slots__
                if slot != "__weakref__"
            ),
            (
                "ingress",
                "controller",
                "pending_authority",
                "completion_scope",
                "proof_authority",
                "subject_authority",
                "terminal_authority",
                "publication_lock",
                "ingress_lock",
                "owner_pid",
                "owner_thread",
                "target_proof_lifecycle",
                "lifecycle",
                "success_armed",
            ),
        )
        self.assertIn("__weakref__", prepared_type.__slots__)
        with self.assertRaises(TypeError):
            prepared_type()
        with self.assertRaises(TypeError):
            type("PreparedSubclass", (prepared_type,), {})
        rebuilt = object.__new__(prepared_type)
        self.assertFalse(hasattr(rebuilt, "__dict__"))
        self.assertIn("redacted", repr(rebuilt))
        self.assertIs(weakref.ref(rebuilt)(), rebuilt)
        with self.assertRaises(TypeError):
            rebuilt.__copy__()
        with self.assertRaises(TypeError):
            rebuilt.__deepcopy__({})
        with self.assertRaises(TypeError):
            rebuilt.__reduce__()
        with self.assertRaises(TypeError):
            rebuilt.__reduce_ex__(5)

        positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
        keyword_only = inspect.Parameter.KEYWORD_ONLY
        expected = {
            "_prepare_durable_causal_precedes_proof_commit_v1": (
                ("proof", positional),
                ("subject", keyword_only),
                ("controller", keyword_only),
                ("pending", keyword_only),
                ("terminal", keyword_only),
            ),
            "_commit_prepared_durable_causal_precedes_proof_v1": (
                ("prepared", positional),
            ),
            (
                "_close_durable_causal_precedes_proof_after_"
                "deferred_append_failure_v1"
            ): (
                ("proof", positional),
                ("subject", keyword_only),
                ("controller", keyword_only),
                ("pending", keyword_only),
                ("terminal", keyword_only),
            ),
        }
        for helper_name, wanted in expected.items():
            with self.subTest(helper=helper_name):
                helper = self._required_ingress_symbol(helper_name)
                parameters = inspect.signature(helper).parameters.values()
                self.assertEqual(
                    tuple((parameter.name, parameter.kind) for parameter in parameters),
                    wanted,
                )

    def test_a5_issuance_registers_both_authorities_before_publication(self):
        function = DurableParentStaticBoundaryTests._ingress_function(
            "_issue_durable_causal_precedes_for_deferred_commit_v1"
        )
        named_calls = [
            (node.func.id, node.lineno)
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        ]

        def call_lines(name: str) -> list[int]:
            return [line for called, line in named_calls if called == name]

        proof_registers = call_lines("_register_proof_authority")
        prepared_registers = call_lines(
            "_register_prepared_deferred_emergency_causal_proof_commit_v1"
        )
        proof_rollbacks = call_lines("_unregister_proof_authority")
        prepared_rollbacks = call_lines(
            "_unregister_prepared_deferred_emergency_causal_proof_commit_v1"
        )
        self.assertTrue(proof_registers, "proof provisional registration missing")
        self.assertTrue(
            prepared_registers,
            "prepared-record provisional registration missing",
        )
        self.assertTrue(proof_rollbacks, "proof registration rollback missing")
        self.assertTrue(
            prepared_rollbacks,
            "prepared-record registration rollback missing",
        )

        revalidation_lines = call_lines("_deferred_commit_transition_kernel_v1")
        self.assertGreaterEqual(
            len(revalidation_lines),
            2,
            "expected prebuild and locked prepublication validation",
        )
        prepublication_line = revalidation_lines[-1]
        self.assertLess(max(proof_registers), prepublication_line)
        self.assertLess(max(prepared_registers), prepublication_line)

        reservation_writes = [
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else (node.target,)
            )
            if isinstance(target, ast.Attribute)
            and target.attr
            in {
                "lifecycle",
                "reservation_committed",
                "reserved_claim",
                "reserved_subject",
                "reserved_terminal",
                "reserved_causal_proof",
                "reserved_completion_scope",
                "causal_proof",
            }
        ]
        self.assertTrue(reservation_writes)
        self.assertLess(prepublication_line, min(reservation_writes))

    def test_commit_entry_guard_rejects_unissued_record_without_mutation(self):
        prepared_type = self._required_ingress_symbol(
            "_PreparedDeferredEmergencyCausalProofCommitV1"
        )
        commit = self._required_ingress_symbol(
            "_commit_prepared_durable_causal_precedes_proof_v1"
        )
        with self.assertRaisesRegex(
            TypeError,
            "exact _PreparedDeferredEmergencyCausalProofCommitV1 required",
        ):
            commit(object())

        rebuilt = object.__new__(prepared_type)
        governed_slots = tuple(
            name
            for name in prepared_type.__slots__
            if name != "__weakref__"
        )
        sentinels = {name: object() for name in governed_slots}
        sentinels["owner_pid"] = 1
        sentinels["owner_thread"] = threading.current_thread()
        sentinels["target_proof_lifecycle"] = (
            "CONSUMED_BY_FUTURE_COMPLETION"
        )
        sentinels["lifecycle"] = "PREPARED"
        sentinels["success_armed"] = True
        for name, value in sentinels.items():
            object.__setattr__(rebuilt, name, value)
        before = tuple(
            object.__getattribute__(rebuilt, name)
            for name in governed_slots
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_proof_commit_invalid",
        ) as caught:
            commit(rebuilt)
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(
            tuple(
                object.__getattribute__(rebuilt, name)
                for name in governed_slots
            ),
            before,
        )

    def test_abort_finalizer_exact_abi_and_absent_pair_short_circuit(self):
        finalizer = self._required_ingress_symbol(
            "_abort_deferred_emergency_commit_subject_v1"
        )
        parameters = tuple(inspect.signature(finalizer).parameters.values())
        self.assertEqual(
            tuple((parameter.name, parameter.kind) for parameter in parameters),
            (
                ("subject", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ("controller", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ("pending", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                ("terminal", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ),
        )
        with mock.patch.object(
            ingress_module,
            "_lookup_subject_authority",
            side_effect=AssertionError("absent pair performed a ledger lookup"),
        ):
            self.assertIsNone(finalizer(None, object(), object(), None))

        with self.assertRaisesRegex(
            TypeError,
            "exact DeferredEmergencyCommitSubjectV1 required",
        ):
            finalizer(object(), object(), object(), None)
        with self.assertRaisesRegex(
            TypeError,
            "exact DurableEvidenceTerminalV1 required",
        ):
            finalizer(None, object(), object(), object())
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_mismatch",
        ):
            finalizer(
                object.__new__(DeferredEmergencyCommitSubjectV1),
                object(),
                object(),
                None,
            )

    def test_prepared_registry_has_no_value_self_retention(self):
        class Cell:
            __slots__ = ("__weakref__",)

        def issue_and_drop():
            ingress = BoundedIngress(
                capacity=1,
                producer_timeout_seconds=0.1,
                receipt_timeout_seconds=0.1,
            )
            proof = object.__new__(DurableCausalPrecedesProofV1)
            before = object.__new__(DurableCausalOrderCoordinateV1)
            after = object.__new__(DurableCausalOrderCoordinateV1)
            subject = object.__new__(DeferredEmergencyCommitSubjectV1)
            terminal = object.__new__(DurableEvidenceTerminalV1)
            claim = Cell()
            pending = Cell()
            issuance = Cell()
            controller = Cell()
            pending_authority = Cell()
            scope = Cell()
            subject_authority = Cell()
            terminal_authority = Cell()
            authority = ingress_module._ProofAuthorityV1(
                before=before,
                after=after,
                subject=subject,
                claim=claim,
                pending=pending,
                terminal=terminal,
                owner_pid=ingress_module.getpid(),
                owner_thread=threading.current_thread(),
                issuance=issuance,
            )
            ingress_module._register_proof_authority(proof, authority)
            prepared = (
                ingress_module._build_prepared_deferred_emergency_causal_proof_commit_v1(
                    ingress=ingress,
                    controller=controller,
                    pending_authority=pending_authority,
                    completion_scope=scope,
                    proof_authority=authority,
                    subject_authority=subject_authority,
                    terminal_authority=terminal_authority,
                    publication_lock=threading.RLock(),
                )
            )
            ingress_module._register_prepared_deferred_emergency_causal_proof_commit_v1(
                proof,
                prepared,
            )
            references = tuple(
                weakref.ref(value)
                for value in (
                    proof,
                    prepared,
                    before,
                    after,
                    subject,
                    terminal,
                    claim,
                    pending,
                    issuance,
                    controller,
                    pending_authority,
                    scope,
                    subject_authority,
                    terminal_authority,
                )
            )
            return references, id(proof), id(prepared)

        references, proof_id, prepared_id = issue_and_drop()
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertNotIn(
            proof_id,
            ingress_module._PREPARED_DEFERRED_COMMIT_ENTRIES_V1,
        )
        self.assertNotIn(
            prepared_id,
            ingress_module._PREPARED_DEFERRED_COMMIT_ENTRIES_V1,
        )

    def test_live_production_prepared_graph_has_no_registry_root(self):
        graph = self._live_production_prepared_graph()
        references = tuple(
            weakref.ref(graph[name])
            for name in (
                "proof",
                "prepared",
                "controller",
                "pending",
                "pending_authority",
                "subject",
                "terminal",
            )
        )
        proof_id = id(graph["proof"])
        prepared_id = id(graph["prepared"])
        del graph
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertNotIn(
            proof_id,
            ingress_module._PREPARED_DEFERRED_COMMIT_ENTRIES_V1,
        )
        self.assertNotIn(
            prepared_id,
            ingress_module._PREPARED_DEFERRED_COMMIT_ENTRIES_V1,
        )

    def test_issued_prepared_corruption_is_zero_mutation_and_tail_is_scalar(self):
        prepared_type = self._required_ingress_symbol(
            "_PreparedDeferredEmergencyCausalProofCommitV1"
        )
        commit = self._required_ingress_symbol(
            "_commit_prepared_durable_causal_precedes_proof_v1"
        )
        governed_slots = tuple(
            name
            for name in prepared_type.__slots__
            if name != "__weakref__"
        )
        corruptible_slots = (
            "ingress",
            "controller",
            "pending_authority",
            "completion_scope",
            "proof_authority",
            "subject_authority",
            "terminal_authority",
            "publication_lock",
            "ingress_lock",
            "owner_pid",
            "owner_thread",
            "target_proof_lifecycle",
        )
        for slot in corruptible_slots:
            with self.subTest(slot=slot):
                graph = self._live_production_prepared_graph()
                prepared = graph["prepared"]
                proof_authority = graph["proof_authority"]
                pending_authority = graph["pending_authority"]
                registry_cell = graph["registry_cell"]
                object.__setattr__(prepared, slot, object())
                token_before = tuple(
                    object.__getattribute__(prepared, name)
                    for name in governed_slots
                )
                registry_before = tuple(
                    getattr(registry_cell, name)
                    for name in registry_cell.__dataclass_fields__
                )
                lifecycle_before = proof_authority.lifecycle
                prepared_reference_before = proof_authority.prepared_commit
                reserved_proof_before = (
                    pending_authority.reserved_causal_proof
                )
                with graph["publication_lock"]:
                    with graph["ingress_lock"]:
                        with self.assertRaisesRegex(
                            ValueError,
                            "durable_causal_proof_commit_invalid",
                        ) as caught:
                            commit(prepared)
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(
                    tuple(
                        object.__getattribute__(prepared, name)
                        for name in governed_slots
                    ),
                    token_before,
                )
                self.assertEqual(
                    tuple(
                        getattr(registry_cell, name)
                        for name in registry_cell.__dataclass_fields__
                    ),
                    registry_before,
                )
                self.assertEqual(proof_authority.lifecycle, lifecycle_before)
                self.assertIs(
                    proof_authority.prepared_commit,
                    prepared_reference_before,
                )
                self.assertIs(
                    pending_authority.reserved_causal_proof,
                    reserved_proof_before,
                )

        function = DurableParentStaticBoundaryTests._ingress_function(
            "_commit_prepared_durable_causal_precedes_proof_v1"
        )
        proof_lifecycle_writes = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "proof_authority"
                and target.attr == "lifecycle"
                for target in node.targets
            )
        ]
        self.assertEqual(len(proof_lifecycle_writes), 1)
        scalar_start = proof_lifecycle_writes[0].lineno
        self.assertEqual(
            [
                node
                for node in ast.walk(function)
                if getattr(node, "lineno", 0) >= scalar_start
                and isinstance(node, ast.Call)
            ],
            [],
        )
        self.assertEqual(
            [
                node
                for node in ast.walk(function)
                if getattr(node, "lineno", 0) >= scalar_start
                and (
                    isinstance(node, ast.Delete)
                    or (
                        isinstance(node, ast.Subscript)
                        and isinstance(node.ctx, (ast.Store, ast.Del))
                    )
                )
            ],
            [],
        )
        for assignment in (
            node
            for node in ast.walk(function)
            if getattr(node, "lineno", 0) >= scalar_start
            and isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        ):
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else (assignment.target,)
            )
            for target in targets:
                self.assertIs(type(target), ast.Attribute)
                self.assertIs(type(target.value), ast.Name)
                self.assertIn(
                    target.value.id,
                    {"proof_authority", "prepared", "entry"},
                )
            self.assertIn(type(assignment.value), (ast.Name, ast.Constant))

    def test_prepared_helpers_require_held_locks_and_never_reacquire(self):
        for helper_name in (
            "_prepare_durable_causal_precedes_proof_commit_v1",
            "_commit_prepared_durable_causal_precedes_proof_v1",
            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1",
        ):
            with self.subTest(helper=helper_name):
                function = DurableParentStaticBoundaryTests._ingress_function(
                    helper_name
                )
                attributes = {
                    node.attr
                    for node in ast.walk(function)
                    if isinstance(node, ast.Attribute)
                }
                lock_operations = [
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"acquire", "release", "__enter__", "__exit__"}
                ]
                self.assertEqual(
                    [node for node in ast.walk(function) if isinstance(node, ast.With)],
                    [],
                )
                self.assertEqual(lock_operations, [])
                self.assertIn("publication_lock", attributes)
                self.assertIn("ingress_lock", attributes)

        failure = DurableParentStaticBoundaryTests._ingress_function(
            "_close_durable_causal_precedes_proof_after_deferred_append_failure_v1"
        )
        failure_calls = {
            node.func.id
            for node in ast.walk(failure)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertNotIn(
            "_finalize_durable_causal_precedes_proof_v1",
            failure_calls,
        )

    def test_abort_finalizer_nests_only_ingress_and_keeps_publication_owned(self):
        function = DurableParentStaticBoundaryTests._ingress_function(
            "_abort_deferred_emergency_commit_subject_v1"
        )
        contexts = [
            item.context_expr
            for node in ast.walk(function)
            if isinstance(node, ast.With)
            for item in node.items
        ]
        context_attributes = [
            node.attr
            for context in contexts
            for node in ast.walk(context)
            if isinstance(node, ast.Attribute)
        ]
        all_attributes = {
            node.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Attribute)
        }
        self.assertEqual(context_attributes.count("_causal_subject_lock"), 1)
        self.assertNotIn("publication_lock", context_attributes)
        self.assertIn("publication_lock", all_attributes)
        self.assertEqual(
            [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"acquire", "release"}
            ],
            [],
        )


class Round19SealedPreparedCommitRedTests(unittest.TestCase):
    @staticmethod
    def _authority(cell: object) -> object:
        authority = (
            ingress_module._lookup_sealed_deferred_commit_cell_authority_v1(
                cell
            )
        )
        if authority is None:
            raise AssertionError("sealed authority unexpectedly absent")
        return authority

    def test_reservation_publishes_unarmed_prepared_record(self):
        fixture = ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )
        self.assertTrue(
            hasattr(result, "prepared"),
            "sealed reservation result omitted prepared record",
        )
        observation = ingress_module._observe_sealed_deferred_commit_fixture_v1(
            fixture
        )
        self.assertTrue(
            hasattr(observation, "reserved_prepared_commit"),
            "sealed reservation observation omitted prepared record",
        )
        prepared = result.prepared
        self.assertIs(observation.reserved_prepared_commit, prepared)
        authority = (
            ingress_module._lookup_sealed_deferred_commit_cell_authority_v1(
                prepared
            )
        )
        self.assertIsNotNone(authority)
        self.assertEqual(authority.role, "PREPARED_COMMIT")
        self.assertEqual(authority.lifecycle, "PREPARED")
        self.assertIs(authority.success_armed, False)
        self.assertEqual(
            result.lock_order,
            ("controller_publication", "ingress_subject", "a5_claim"),
        )

    def test_provisional_proof_and_prepared_registration_cuts_roll_back(self):
        original_register = (
            ingress_module._register_sealed_deferred_commit_cell_authority_v1
        )
        original_kernel = ingress_module._deferred_commit_transition_kernel_v1
        for cut, expected_registered in (
            ("proof_register", 1),
            ("prepared_register", 2),
            ("prepublication", 2),
        ):
            with self.subTest(cut=cut):
                fixture = (
                    ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
                )
                before = (
                    ingress_module._observe_sealed_deferred_commit_fixture_v1(
                        fixture
                    )
                )
                provisionals: list[object] = []
                kernel_calls = 0

                def controlled_register(item, authority):
                    provisionals.append(item)
                    original_register(item, authority)
                    if (
                        cut == "proof_register"
                        or (
                            cut == "prepared_register"
                            and len(provisionals) == 2
                        )
                    ):
                        raise RuntimeError(f"controlled-{cut}")

                def controlled_kernel(value):
                    nonlocal kernel_calls
                    kernel_calls += 1
                    if cut == "prepublication" and kernel_calls == 2:
                        raise RuntimeError("controlled-prepublication")
                    return original_kernel(value)

                with (
                    mock.patch.object(
                        ingress_module,
                        "_register_sealed_deferred_commit_cell_authority_v1",
                        side_effect=controlled_register,
                    ),
                    mock.patch.object(
                        ingress_module,
                        "_deferred_commit_transition_kernel_v1",
                        side_effect=controlled_kernel,
                    ),
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        f"controlled-{cut}",
                    ):
                        ingress_module._run_sealed_deferred_commit_kernel_v1(
                            fixture.claim,
                            fixture.subject,
                            fixture.pending,
                        )
                self.assertEqual(len(provisionals), expected_registered)
                self.assertTrue(
                    all(
                        ingress_module._lookup_sealed_deferred_commit_cell_authority_v1(
                            item
                        )
                        is None
                        for item in provisionals
                    )
                )
                self.assertEqual(
                    ingress_module._observe_sealed_deferred_commit_fixture_v1(
                        fixture
                    ),
                    before,
                )

    def test_prepare_changes_only_success_armed_and_duplicate_is_consumed(self):
        fixture = ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )
        self.assertTrue(hasattr(result, "prepared"))
        prepare = getattr(
            ingress_module,
            "_prepare_sealed_deferred_commit_proof_v1",
            None,
        )
        self.assertIsNotNone(
            prepare,
            "missing sealed prepare helper",
        )
        authority = self._authority(result.prepared)
        field_names = tuple(authority.__dataclass_fields__)
        before = {
            name: getattr(authority, name)
            for name in field_names
        }
        self.assertIs(before["success_armed"], False)

        self.assertIs(prepare(fixture, result.proof), result.prepared)

        after = {
            name: getattr(authority, name)
            for name in field_names
        }
        self.assertIs(after["success_armed"], True)
        self.assertEqual(
            {name: value for name, value in after.items() if name != "success_armed"},
            {name: value for name, value in before.items() if name != "success_armed"},
        )
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_proof_commit_consumed",
        ):
            prepare(fixture, result.proof)

    def test_commit_consumes_proof_and_prepared_once_without_controller_cells(self):
        fixture = ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
        result = ingress_module._run_sealed_deferred_commit_kernel_v1(
            fixture.claim,
            fixture.subject,
            fixture.pending,
        )
        self.assertTrue(hasattr(result, "prepared"))
        prepare = getattr(
            ingress_module,
            "_prepare_sealed_deferred_commit_proof_v1",
            None,
        )
        commit = getattr(
            ingress_module,
            "_commit_sealed_prepared_deferred_commit_v1",
            None,
        )
        self.assertIsNotNone(prepare, "missing sealed prepare helper")
        self.assertIsNotNone(commit, "missing sealed commit helper")
        ingress_module._consume_sealed_deferred_commit_terminal_v1(fixture)
        prepared = prepare(fixture, result.proof)
        self.assertIsNone(commit(fixture, prepared))
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                result.proof
            ),
            "CONSUMED_BY_FUTURE_COMPLETION",
        )
        self.assertEqual(
            ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                prepared
            ),
            "COMMITTED",
        )
        observation = ingress_module._observe_sealed_deferred_commit_fixture_v1(
            fixture
        )
        self.assertEqual(observation.pending_lifecycle, "COMMIT_RESERVED")
        self.assertEqual(observation.scope_lifecycle, "RESERVATION_COMMITTED")
        before_repeat = observation
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_proof_commit_consumed",
        ):
            commit(fixture, prepared)
        self.assertEqual(
            ingress_module._observe_sealed_deferred_commit_fixture_v1(fixture),
            before_repeat,
        )

    def test_failure_closer_covers_terminal_and_arming_two_by_two(self):
        for terminal_consumed in (False, True):
            for success_armed in (False, True):
                with self.subTest(
                    terminal_consumed=terminal_consumed,
                    success_armed=success_armed,
                ):
                    fixture = (
                        ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
                    )
                    result = ingress_module._run_sealed_deferred_commit_kernel_v1(
                        fixture.claim,
                        fixture.subject,
                        fixture.pending,
                    )
                    self.assertTrue(hasattr(result, "prepared"))
                    if terminal_consumed:
                        ingress_module._consume_sealed_deferred_commit_terminal_v1(
                            fixture
                        )
                    if success_armed:
                        prepare = getattr(
                            ingress_module,
                            "_prepare_sealed_deferred_commit_proof_v1",
                            None,
                        )
                        self.assertIsNotNone(prepare)
                        prepare(fixture, result.proof)
                    ingress_module._finalize_sealed_deferred_commit_proof_v1(
                        fixture,
                        result.proof,
                        append_succeeded=False,
                    )
                    self.assertEqual(
                        ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                            result.proof
                        ),
                        "APPEND_FAILED_CLOSED",
                    )
                    self.assertEqual(
                        ingress_module._resolve_sealed_deferred_commit_cell_state_v1(
                            result.prepared
                        ),
                        "FAILED_CLOSED",
                    )
                    prepared_authority = self._authority(result.prepared)
                    self.assertIs(
                        prepared_authority.success_armed,
                        success_armed,
                    )
                    observation = (
                        ingress_module._observe_sealed_deferred_commit_fixture_v1(
                            fixture
                        )
                    )
                    self.assertEqual(
                        observation.pending_lifecycle,
                        "PUBLICATION_FAILED_CLOSED",
                    )
                    self.assertEqual(
                        observation.scope_lifecycle,
                        "APPEND_FAILED_CLOSED",
                    )
                    with self.assertRaisesRegex(
                        ValueError,
                        "durable_causal_proof_consumed",
                    ):
                        ingress_module._finalize_sealed_deferred_commit_proof_v1(
                            fixture,
                            result.proof,
                            append_succeeded=False,
                        )

    def test_commit_and_failure_close_release_every_sealed_graph(self):
        for outcome in ("commit", "failure"):
            with self.subTest(outcome=outcome):
                fixture = (
                    ingress_module._issue_sealed_deferred_commit_kernel_fixture_v1()
                )
                result = ingress_module._run_sealed_deferred_commit_kernel_v1(
                    fixture.claim,
                    fixture.subject,
                    fixture.pending,
                )
                self.assertTrue(hasattr(result, "prepared"))
                references = tuple(
                    weakref.ref(value)
                    for value in (
                        fixture.claim,
                        fixture.subject,
                        fixture.pending,
                        fixture.completion_scope,
                        fixture.terminal,
                        result.proof,
                        result.prepared,
                    )
                )
                if outcome == "commit":
                    ingress_module._consume_sealed_deferred_commit_terminal_v1(
                        fixture
                    )
                    prepare = getattr(
                        ingress_module,
                        "_prepare_sealed_deferred_commit_proof_v1",
                        None,
                    )
                    commit = getattr(
                        ingress_module,
                        "_commit_sealed_prepared_deferred_commit_v1",
                        None,
                    )
                    self.assertIsNotNone(prepare, "missing sealed prepare helper")
                    self.assertIsNotNone(commit, "missing sealed commit helper")
                    prepared = prepare(
                        fixture,
                        result.proof,
                    )
                    commit(
                        fixture,
                        prepared,
                    )
                    del prepared
                else:
                    ingress_module._finalize_sealed_deferred_commit_proof_v1(
                        fixture,
                        result.proof,
                        append_succeeded=False,
                    )
                del result
                del fixture
                gc.collect()
                self.assertTrue(
                    all(reference() is None for reference in references)
                )

    def test_production_commit_scalar_mutation_tail_has_no_calls(self):
        function = DurableParentStaticBoundaryTests._ingress_function(
            "_commit_prepared_durable_causal_precedes_proof_v1"
        )
        lifecycle_writes = [
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else (node.target,)
            )
            if isinstance(target, ast.Attribute)
            and target.attr == "lifecycle"
        ]
        self.assertTrue(
            lifecycle_writes,
            "scalar commit lifecycle assignment missing",
        )
        scalar_start = min(lifecycle_writes)
        forbidden = [
            node
            for node in ast.walk(function)
            if getattr(node, "lineno", 0) >= scalar_start
            and isinstance(
                node,
                (
                    ast.Call,
                    ast.Raise,
                    ast.Import,
                    ast.ImportFrom,
                    ast.With,
                    ast.Try,
                    ast.Await,
                    ast.Yield,
                    ast.YieldFrom,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                ),
            )
        ]
        self.assertEqual(
            forbidden,
            [],
            "prepared commit scalar tail contains a failure-capable operation",
        )


class Round19WeakSubjectIndexRedTests(unittest.TestCase):
    class _Cell:
        __slots__ = ("__weakref__",)

    def _symbol(self, name: str) -> object:
        value = getattr(ingress_module, name, None)
        self.assertIsNotNone(value, f"missing Stage-A ingress symbol: {name}")
        return value

    @staticmethod
    def _ingress() -> BoundedIngress:
        return BoundedIngress(
            capacity=1,
            producer_timeout_seconds=0.1,
            receipt_timeout_seconds=0.1,
        )

    def test_weak_index_caps_4096_purges_dead_and_handles_id_reuse(self):
        register = self._symbol(
            "_register_deferred_subject_index_entry_v1"
        )
        lookup = self._symbol("_lookup_deferred_subject_index_entry_v1")
        purge = self._symbol(
            "_purge_dead_deferred_subject_index_entries_v1"
        )
        ingress = self._ingress()
        triples = [
            (self._Cell(), self._Cell(), self._Cell())
            for _ in range(4096)
        ]
        for pending, subject, terminal in triples:
            self.assertIsNone(
                register(ingress, pending, subject, terminal)
            )
        del pending, subject, terminal
        index = ingress._deferred_subject_by_pending  # type: ignore[attr-defined]
        self.assertEqual(len(index), 4096)

        overflow = (self._Cell(), self._Cell(), self._Cell())
        with self.assertRaisesRegex(
            RuntimeError,
            "durable_causal_subject_registry_full",
        ) as caught:
            register(ingress, *overflow)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(lookup(ingress, overflow[0]))

        victim = triples.pop()
        victim_refs = tuple(weakref.ref(value) for value in victim)
        victim_id = id(victim[0])
        stale_entry = index[victim_id]
        del victim
        gc.collect()
        self.assertTrue(all(reference() is None for reference in victim_refs))
        self.assertIsNone(purge(ingress))
        self.assertEqual(len(index), 4095)

        replacement = (self._Cell(), self._Cell(), self._Cell())
        index[id(replacement[0])] = stale_entry
        self.assertIsNone(register(ingress, *replacement))
        replacement_entry = lookup(ingress, replacement[0])
        self.assertIsNotNone(replacement_entry)
        self.assertIsNot(replacement_entry, stale_entry)
        self.assertEqual(len(index), 4096)

    def test_weak_index_registration_failure_rolls_back_partial_entry(self):
        register = self._symbol(
            "_register_deferred_subject_index_entry_v1"
        )
        ingress = self._ingress()

        class RegisterThenRaise(dict):
            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                raise RuntimeError("controlled-subject-index-registration")

        failing_index = RegisterThenRaise()
        object.__setattr__(
            ingress,
            "_deferred_subject_by_pending",
            failing_index,
        )
        pending, subject, terminal = (
            self._Cell(),
            self._Cell(),
            self._Cell(),
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "controlled-subject-index-registration",
        ):
            register(ingress, pending, subject, terminal)
        self.assertEqual(failing_index, {})

    def test_real_subject_issuer_routes_only_through_weak_index_helpers(self):
        function = DurableParentStaticBoundaryTests._ingress_function(
            "_issue_deferred_emergency_commit_subject_v1"
        )
        called_names = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertIn(
            "_lookup_deferred_subject_index_entry_v1",
            called_names,
        )
        self.assertIn(
            "_register_deferred_subject_index_entry_v1",
            called_names,
        )
        direct_subscripts = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_deferred_subject_by_pending"
        ]
        direct_gets = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "_deferred_subject_by_pending"
        ]
        self.assertEqual(
            direct_subscripts + direct_gets,
            [],
            "subject issuer directly reads/writes the strong pending map",
        )


class Round19SealedSubjectAbortRedTests(unittest.TestCase):
    def _symbols(self):
        names = (
            "_issue_sealed_deferred_subject_abort_fixture_v1",
            "_run_sealed_deferred_subject_abort_v1",
            "_observe_sealed_deferred_subject_abort_fixture_v1",
        )
        values = tuple(getattr(ingress_module, name, None) for name in names)
        for name, value in zip(names, values, strict=True):
            self.assertIsNotNone(
                value,
                f"missing Stage-A sealed abort symbol: {name}",
            )
        return values

    def assert_fixed_lock_contract(self, observation: object) -> None:
        self.assertIs(observation.publication_lock_owned_on_entry, True)
        self.assertIs(observation.publication_lock_owned_on_return, True)
        self.assertIs(observation.ingress_lock_owned_on_return, False)
        self.assertEqual(
            observation.lock_trace,
            ("controller_publication", "ingress_subject"),
        )

    def test_first_abort_closes_subject_terminal_and_direct_repeat_is_consumed(self):
        issue, run, observe = self._symbols()
        fixture = issue()
        before = observe(fixture)
        self.assertEqual(before.subject_lifecycle, "FRESH")
        self.assertEqual(before.terminal_lifecycle, "ISSUED")
        self.assertIs(before.terminal_live_envelope_present, True)
        self.assertEqual(before.index_lifecycle, "LIVE")

        after = run(fixture, inject_uncertainty=False)

        self.assertEqual(after.subject_lifecycle, "ABORTED_CLOSED")
        self.assertEqual(after.terminal_lifecycle, "CONSUMED")
        self.assertIs(after.terminal_live_envelope_present, False)
        self.assertEqual(after.index_lifecycle, "ABORTED_CLOSED")
        self.assert_fixed_lock_contract(after)
        with self.assertRaisesRegex(
            ValueError,
            "durable_causal_subject_consumed",
        ):
            run(fixture, inject_uncertainty=False)
        repeated = observe(fixture)
        self.assertEqual(
            (
                repeated.subject_lifecycle,
                repeated.terminal_lifecycle,
                repeated.terminal_live_envelope_present,
                repeated.index_lifecycle,
            ),
            (
                "ABORTED_CLOSED",
                "CONSUMED",
                False,
                "ABORTED_CLOSED",
            ),
        )

    def test_abort_uncertainty_is_raw_from_none_and_releases_nested_lock(self):
        issue, run, observe = self._symbols()
        fixture = issue()
        with self.assertRaisesRegex(
            RuntimeError,
            "durable_causal_subject_abort_uncertain",
        ) as caught:
            run(fixture, inject_uncertainty=True)
        self.assertIsNone(caught.exception.__cause__)
        after = observe(fixture)
        self.assertEqual(after.subject_lifecycle, "FRESH")
        self.assertEqual(after.terminal_lifecycle, "ISSUED")
        self.assertIs(after.terminal_live_envelope_present, True)
        self.assertEqual(after.index_lifecycle, "LIVE")
        self.assert_fixed_lock_contract(after)


if __name__ == "__main__":
    unittest.main()
