"""Bounded multi-producer ingress for the single-owner Tennis runtime."""

from __future__ import annotations

from dataclasses import dataclass
import math
from os import getpid
import queue
import threading
import time

from .codec import canonical_record_sha256
from .events import (
    CapturedInput,
    PersistedEvent,
    _exact_nonnegative_integer,
    _safe_identifier,
    _sha256,
)
from .sequencer import EventRuntime, WrongOwnerThread


_BACKPRESSURE = "backpressure"
_OWNER_UNRESPONSIVE = "owner_unresponsive"
_PROVISIONAL = "provisional_queued"
_ACTIVE = "active"
_DURABLE_RECEIPTED = "durable_receipted"
_TIMED_OUT = "timed_out_unacknowledged"
_FAILED = "failed_unacknowledged"
_DURABLE_UNACKNOWLEDGED = "durable_unacknowledged"


class IngressClosed(RuntimeError):
    """Ingress admission or its bound runtime is permanently closed."""


class IngressBackpressureHalt(RuntimeError):
    """The bounded queue could not admit an item before its deadline."""


class IngressOwnerUnresponsive(RuntimeError):
    """An admitted item did not receive a durable result in time."""


def _positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}: positive_integer_required")
    if value < 1:
        raise ValueError(f"{field_name}: positive_integer_required")
    return value


def _positive_timeout(value: object, field_name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{field_name}: positive_finite_float_required")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{field_name}: positive_finite_float_required")
    return value


@dataclass(frozen=True, slots=True)
class IngressItem:
    producer_id: str
    producer_sequence: int
    captured: CapturedInput

    def __post_init__(self) -> None:
        _safe_identifier(self.producer_id, "producer_id")
        _exact_nonnegative_integer(
            self.producer_sequence,
            "producer_sequence",
        )
        if type(self.captured) is not CapturedInput:
            raise TypeError("captured: exact_CapturedInput_required")


def _validate_exact_ingress_item(item: IngressItem) -> None:
    try:
        producer_id = item.producer_id
        producer_sequence = item.producer_sequence
        captured = item.captured
    except AttributeError:
        raise TypeError("ingress_item_fields_required") from None
    _safe_identifier(producer_id, "producer_id")
    _exact_nonnegative_integer(
        producer_sequence,
        "producer_sequence",
    )
    if type(captured) is not CapturedInput:
        raise TypeError("captured: exact_CapturedInput_required")


@dataclass(frozen=True, slots=True)
class DurableIngressReceipt:
    producer_id: str
    producer_sequence: int
    raw_ingest_seq: int
    raw_record_sha256: str

    def __post_init__(self) -> None:
        _safe_identifier(self.producer_id, "producer_id")
        _exact_nonnegative_integer(
            self.producer_sequence,
            "producer_sequence",
        )
        _positive_integer(self.raw_ingest_seq, "raw_ingest_seq")
        _sha256(self.raw_record_sha256, "raw_record_sha256")


class _IngressNode:
    __slots__ = (
        "item",
        "completion",
        "completion_lock",
        "state",
        "receipt",
    )

    def __init__(self, item: IngressItem) -> None:
        self.item = item
        self.completion = threading.Event()
        self.completion_lock = threading.Lock()
        self.state = _PROVISIONAL
        self.receipt: DurableIngressReceipt | None = None

    def __repr__(self) -> str:
        return f"<_IngressNode state={self.state!r}>"


class BoundedIngress:
    """Serialize bounded concurrent capture into one owner-thread runtime."""

    __slots__ = (
        "_capacity",
        "_producer_timeout_seconds",
        "_receipt_timeout_seconds",
        "_queue",
        "_admission_lock",
        "_condition",
        "_owner_pid",
        "_owner_thread",
        "_runtime",
        "_normal_closed",
        "_fault_reason",
        "_runtime_failed",
        "_terminal_written",
        "_terminal_intent",
        "_poll_in_progress",
        "_active_node",
    )

    def __init__(
        self,
        *,
        capacity: int,
        producer_timeout_seconds: float,
        receipt_timeout_seconds: float,
    ) -> None:
        self._capacity = _positive_integer(capacity, "capacity")
        self._producer_timeout_seconds = _positive_timeout(
            producer_timeout_seconds,
            "producer_timeout_seconds",
        )
        self._receipt_timeout_seconds = _positive_timeout(
            receipt_timeout_seconds,
            "receipt_timeout_seconds",
        )
        self._queue: queue.Queue[_IngressNode] = queue.Queue(
            maxsize=self._capacity
        )
        self._admission_lock = threading.Lock()
        self._condition = threading.Condition(self._admission_lock)
        self._owner_pid = getpid()
        self._owner_thread = threading.current_thread()
        self._runtime: EventRuntime | None = None
        self._normal_closed = False
        self._fault_reason: str | None = None
        self._runtime_failed = False
        self._terminal_written = False
        self._terminal_intent: str | None = None
        self._poll_in_progress = False
        self._active_node: _IngressNode | None = None

    def __repr__(self) -> str:
        return "<BoundedIngress redacted>"

    @property
    def halt_reason(self) -> str | None:
        with self._admission_lock:
            return self._fault_reason

    def _require_owner(self) -> None:
        if (
            getpid() != self._owner_pid
            or threading.current_thread() is not self._owner_thread
        ):
            raise WrongOwnerThread("ingress_wrong_owner_thread")

    def _admission_open_locked(self) -> bool:
        return (
            not self._normal_closed
            and self._fault_reason is None
            and not self._runtime_failed
            and not self._terminal_written
            and self._terminal_intent is None
        )

    def _closed_error_locked(self) -> RuntimeError:
        if (
            self._runtime_failed
            or self._terminal_written
            or self._terminal_intent is not None
        ):
            return IngressClosed("ingress_closed")
        if self._fault_reason == _BACKPRESSURE:
            return IngressBackpressureHalt("ingress_backpressure")
        if self._fault_reason == _OWNER_UNRESPONSIVE:
            return IngressOwnerUnresponsive("ingress_owner_unresponsive")
        return IngressClosed("ingress_closed")

    def enqueue(self, item: IngressItem) -> DurableIngressReceipt:
        admission_started = time.monotonic()
        if type(item) is not IngressItem:
            raise TypeError("exact IngressItem required")
        _validate_exact_ingress_item(item)
        deadline = admission_started + self._producer_timeout_seconds
        node = _IngressNode(item)

        with self._condition:
            while True:
                if not self._admission_open_locked():
                    raise self._closed_error_locked()
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    self._fault_reason = _BACKPRESSURE
                    self._condition.notify_all()
                    raise IngressBackpressureHalt(
                        "ingress_backpressure"
                    )
                try:
                    self._queue.put_nowait(node)
                except queue.Full:
                    self._condition.wait(remaining)
                    continue
                self._condition.notify_all()
                break

        node.completion.wait(self._receipt_timeout_seconds)
        timeout_won = False
        with node.completion_lock:
            if node.state == _DURABLE_RECEIPTED:
                if node.receipt is None:
                    raise IngressClosed("ingress_runtime_unavailable")
                return node.receipt
            if node.state == _FAILED:
                raise IngressClosed("ingress_runtime_unavailable")
            if node.state in (_PROVISIONAL, _ACTIVE):
                with self._condition:
                    node.state = _TIMED_OUT
                    if (
                        self._fault_reason is None
                        and not self._runtime_failed
                        and not self._terminal_written
                        and self._terminal_intent is None
                    ):
                        self._fault_reason = _OWNER_UNRESPONSIVE
                    self._condition.notify_all()
                timeout_won = True
            elif node.state in (_TIMED_OUT, _DURABLE_UNACKNOWLEDGED):
                timeout_won = True
            else:
                raise IngressClosed("ingress_runtime_unavailable")

        if timeout_won:
            raise IngressOwnerUnresponsive("ingress_owner_unresponsive")
        raise IngressClosed("ingress_runtime_unavailable")

    def close_inputs(self) -> None:
        with self._condition:
            if (
                self._normal_closed
                or self._fault_reason is not None
                or self._runtime_failed
                or self._terminal_written
            ):
                return
            self._normal_closed = True
            self._condition.notify_all()

    def _next_action_locked(self) -> str | None:
        if (
            self._terminal_intent is not None
            or self._active_node is not None
            or not self._queue.empty()
        ):
            return None
        if self._fault_reason == _BACKPRESSURE:
            return _BACKPRESSURE
        if self._fault_reason == _OWNER_UNRESPONSIVE:
            return _OWNER_UNRESPONSIVE
        if self._normal_closed:
            return "operator_stop"
        return None

    def _claim_action_locked(self) -> str | None:
        action = self._next_action_locked()
        if action is not None:
            self._terminal_intent = action
        return action

    def _settle_failed_node(self, node: _IngressNode) -> None:
        with node.completion_lock:
            if node.state in (_PROVISIONAL, _ACTIVE):
                node.state = _FAILED
            node.completion.set()

    def _runtime_failure(self, active: _IngressNode | None) -> None:
        pending: list[_IngressNode] = []
        with self._condition:
            self._runtime_failed = True
            if self._active_node is active:
                self._active_node = None
            while True:
                try:
                    pending.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._condition.notify_all()
        if active is not None:
            self._settle_failed_node(active)
        for node in pending:
            self._settle_failed_node(node)

    def _finalize(
        self,
        runtime: EventRuntime,
        action: str,
    ) -> PersistedEvent:
        try:
            if action == _BACKPRESSURE:
                terminal = runtime.close_ingress_backpressure()
            elif action == _OWNER_UNRESPONSIVE:
                terminal = runtime.close_ingress_owner_unresponsive()
            elif action == "session_end":
                terminal = runtime.close_ingress_session_end()
            else:
                terminal = runtime.close_clean("operator_stop")
        except BaseException:
            self._runtime_failure(None)
            raise
        with self._condition:
            self._terminal_written = True
            self._condition.notify_all()
        return terminal

    def _process_node(
        self,
        runtime: EventRuntime,
        node: _IngressNode,
    ) -> PersistedEvent:
        with node.completion_lock:
            if node.state == _PROVISIONAL:
                node.state = _ACTIVE

        try:
            raw = runtime.ingest(node.item.captured)
            receipt = DurableIngressReceipt(
                producer_id=node.item.producer_id,
                producer_sequence=node.item.producer_sequence,
                raw_ingest_seq=raw.ingest_seq,
                raw_record_sha256=canonical_record_sha256(raw),
            )
        except BaseException:
            self._runtime_failure(node)
            raise

        with node.completion_lock:
            if node.state in (_PROVISIONAL, _ACTIVE):
                node.receipt = receipt
                node.state = _DURABLE_RECEIPTED
            elif node.state == _TIMED_OUT:
                node.receipt = None
                node.state = _DURABLE_UNACKNOWLEDGED
            node.completion.set()

        with self._condition:
            if self._active_node is node:
                self._active_node = None
            action = self._claim_action_locked()
            self._condition.notify_all()
        if action is not None:
            return self._finalize(runtime, action)
        return raw

    def drain_one(
        self,
        runtime: EventRuntime,
        *,
        timeout_seconds: float,
    ) -> PersistedEvent | None:
        timeout = _positive_timeout(timeout_seconds, "timeout_seconds")
        if type(runtime) is not EventRuntime:
            raise TypeError("exact EventRuntime required")
        self._require_owner()
        with self._condition:
            if self._runtime is not None and runtime is not self._runtime:
                raise IngressClosed("ingress_runtime_mismatch")
            if (
                self._terminal_written
                or self._runtime_failed
                or self._terminal_intent is not None
            ):
                raise IngressClosed("ingress_closed")
        runtime.require_owner()
        with self._condition:
            if self._runtime is None:
                self._runtime = runtime
            elif runtime is not self._runtime:
                raise IngressClosed("ingress_runtime_mismatch")
            if (
                self._terminal_written
                or self._runtime_failed
                or self._terminal_intent is not None
            ):
                raise IngressClosed("ingress_closed")
        deadline = time.monotonic() + timeout

        while True:
            node: _IngressNode | None = None
            action: str | None = None
            with self._condition:
                if (
                    self._terminal_written
                    or self._runtime_failed
                    or self._terminal_intent is not None
                ):
                    raise IngressClosed("ingress_closed")
                try:
                    node = self._queue.get_nowait()
                except queue.Empty:
                    node = None
                if node is not None:
                    self._active_node = node
                    self._condition.notify_all()
                else:
                    action = self._claim_action_locked()
                    if action is None:
                        self._poll_in_progress = True
            if node is not None:
                return self._process_node(runtime, node)
            if action is not None:
                return self._finalize(runtime, action)

            try:
                session_ended = runtime.check_ingress_session_end()
            except BaseException:
                self._runtime_failure(None)
                raise

            with self._condition:
                self._poll_in_progress = False
                try:
                    node = self._queue.get_nowait()
                except queue.Empty:
                    node = None
                if node is not None:
                    self._active_node = node
                    self._condition.notify_all()
                else:
                    action = self._claim_action_locked()
                    if (
                        action is None
                        and session_ended is True
                        and self._admission_open_locked()
                    ):
                        action = "session_end"
                        self._terminal_intent = action
                if node is None and action is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        return None
                    action = self._claim_action_locked()
                    if action is None:
                        self._condition.wait(remaining)
                        continue
            if node is not None:
                return self._process_node(runtime, node)
            if action is not None:
                return self._finalize(runtime, action)
