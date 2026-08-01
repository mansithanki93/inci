"""Size-one observational dashboard mailbox for Tennis v1."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import queue
import threading

from .events import (
    _exact_nonnegative_integer,
    _sha256,
    _validate_session_id,
)


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    session_id: str
    last_applied_raw_seq: int
    raw_count: int
    derived_count: int
    trace_sha256: str

    def __post_init__(self) -> None:
        if type(self) is not DashboardSnapshot:
            raise TypeError("exact DashboardSnapshot required")
        _validate_session_id(self.session_id)
        _exact_nonnegative_integer(
            self.last_applied_raw_seq,
            "last_applied_raw_seq",
        )
        _exact_nonnegative_integer(self.raw_count, "raw_count")
        _exact_nonnegative_integer(self.derived_count, "derived_count")
        _sha256(self.trace_sha256, "trace_sha256")


class LatestSnapshotMailbox:
    """Replace-latest handoff that cannot backpressure the event runtime."""

    __slots__ = (
        "_queue",
        "_replacement_lock",
        "_construction_pid",
        "_publisher_thread",
    )

    def __init__(self) -> None:
        self._queue: queue.Queue[DashboardSnapshot] = queue.Queue(maxsize=1)
        self._replacement_lock = threading.Lock()
        self._construction_pid = os.getpid()
        self._publisher_thread: threading.Thread | None = None

    def __repr__(self) -> str:
        return "<LatestSnapshotMailbox redacted>"

    def _require_original_process(self) -> None:
        if os.getpid() != self._construction_pid:
            raise RuntimeError("mailbox_forked_process")

    def publish(self, snapshot: DashboardSnapshot) -> None:
        if type(snapshot) is not DashboardSnapshot:
            raise TypeError("exact DashboardSnapshot required")
        DashboardSnapshot.__post_init__(snapshot)
        self._require_original_process()
        publisher = threading.current_thread()
        with self._replacement_lock:
            if (
                self._publisher_thread is not None
                and publisher is not self._publisher_thread
            ):
                raise RuntimeError("mailbox_wrong_publisher")
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(snapshot)
            if self._publisher_thread is None:
                self._publisher_thread = publisher

    def take(
        self,
        *,
        timeout: float | None = None,
    ) -> DashboardSnapshot:
        self._require_original_process()
        if timeout is not None:
            if type(timeout) is not float:
                raise TypeError("timeout: nonnegative_finite_float_or_none_required")
            if not math.isfinite(timeout) or timeout < 0.0:
                raise ValueError(
                    "timeout: nonnegative_finite_float_or_none_required"
                )
        return self._queue.get(timeout=timeout)
