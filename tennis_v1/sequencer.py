"""Provider-bound durable-before-reduce sequencing for Tennis v1."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import threading

from .capture import (
    CaptureValidationError,
    validate_capture_against_authority,
    validate_captured_input,
)
from .entitlements import (
    ProviderGate,
    ProviderGateError,
    ProviderSessionPoll,
    QualificationDecision,
    QualificationReason,
)
from .events import (
    CaptureAuthority,
    CapturedInput,
    DerivedDraft,
    PersistedEvent,
    RecordKind,
    SessionManifest,
    SourceKind,
)
from .reducer import initial_trace, next_trace, reduce_event
from .retention import (
    RetentionCoordinator,
    RetentionDueDeleteError,
    RetentionError,
    RetentionGlobalHalt,
)
from .session import (
    canonical_session_manifest_bytes,
    require_decision_matches_session,
)
from .state import FoundationState, canonical_state_bytes, initial_state
from .wal import (
    DiskLowError,
    JournalDurabilityError,
    JournalValidationError,
    JournalWriter,
)


_AUTHORIZER_SENTINEL = object()
_CLEAN_PUBLIC_REASONS = frozenset({"operator_stop"})
_HALT_PUBLIC_REASONS = frozenset({"operator_halt"})


class RuntimePoisoned(RuntimeError):
    """The runtime is permanently unusable after close or uncertain bytes."""


class WrongOwnerThread(RuntimeError):
    """The runtime was invoked outside its creating process/thread."""


def _rebind(
    decision: QualificationDecision,
    manifest: SessionManifest,
) -> QualificationDecision:
    require_decision_matches_session(decision, manifest)
    return decision


@dataclass(frozen=True, slots=True, init=False)
class ProviderPersistenceAuthorizer:
    gate: ProviderGate = field(repr=False, compare=False)
    coordinator: RetentionCoordinator = field(repr=False, compare=False)
    session_manifest: SessionManifest
    bound_decision: QualificationDecision

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("use bind_provider_persistence_authorizer")

    def authorize_session(self) -> None:
        decision = self.gate.require_start()
        _rebind(decision, self.session_manifest)

    def authorize_capture(
        self,
        authority: CaptureAuthority,
        captured: CapturedInput,
    ) -> None:
        self.coordinator.require_provider_operation()
        decision = self.gate.require_ingest()
        _rebind(decision, self.session_manifest)
        if captured.source_kind is SourceKind.PROVIDER:
            allowed = self.authorize_raw_persistence()
            if allowed < self.session_manifest.required_retention_until_ns:
                raise ProviderGateError(QualificationReason.RETENTION_TOO_SHORT)
        validate_capture_against_authority(
            authority,
            captured,
            self.session_manifest,
            performing_authorizer=self,
        )
        validate_captured_input(captured, self.session_manifest)

    def authorize_ingest(self, captured: CapturedInput) -> None:
        decision = self.gate.require_ingest()
        _rebind(decision, self.session_manifest)
        validate_captured_input(captured, self.session_manifest)

    def authorize_raw_persistence(self) -> int:
        allowed = self.gate.require_raw_persist()
        if type(allowed) is not int:
            raise ProviderGateError(QualificationReason.RETENTION_TOO_SHORT)
        return allowed

    def authorize_persist(self, captured: CapturedInput) -> int | None:
        validate_captured_input(captured, self.session_manifest)
        if captured.source_kind is not SourceKind.PROVIDER:
            return None
        allowed = self.authorize_raw_persistence()
        deadline = self.session_manifest.required_retention_until_ns
        if allowed < deadline:
            raise ProviderGateError(QualificationReason.RETENTION_TOO_SHORT)
        return deadline

    def authorize_transform(self, raw: PersistedEvent) -> None:
        if (
            type(raw) is not PersistedEvent
            or raw.record_kind is not RecordKind.RAW
            or raw.session_id != self.session_manifest.session_id
        ):
            raise CaptureValidationError("raw_session_binding_invalid")
        decision = self.gate.require_transform()
        _rebind(decision, self.session_manifest)

    def authorize_derived_persist(
        self,
        raw: PersistedEvent,
        draft: DerivedDraft,
    ) -> None:
        if (
            type(raw) is not PersistedEvent
            or raw.record_kind is not RecordKind.RAW
            or raw.session_id != self.session_manifest.session_id
            or type(draft) is not DerivedDraft
        ):
            raise CaptureValidationError("derived_session_binding_invalid")
        decision = self.gate.require_derived_persist()
        _rebind(decision, self.session_manifest)

    def authorize_analysis(self) -> QualificationDecision:
        decision = self.gate.require_analysis()
        return _rebind(decision, self.session_manifest)

    def authorize_close(self) -> None:
        decision = self.gate.require_close()
        _rebind(decision, self.session_manifest)

    def poll_session(self) -> bool:
        poll = self.gate.poll_session()
        if type(poll) is not ProviderSessionPoll:
            raise ProviderGateError(
                QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH
            )
        _rebind(poll.decision, self.session_manifest)
        return poll.session_ended


def _build_provider_persistence_authorizer(
    *,
    gate: ProviderGate,
    coordinator: RetentionCoordinator,
    session_manifest: SessionManifest,
    decision: QualificationDecision,
    sentinel: object,
) -> ProviderPersistenceAuthorizer:
    if sentinel is not _AUTHORIZER_SENTINEL:
        raise TypeError("private provider persistence constructor")
    instance = object.__new__(ProviderPersistenceAuthorizer)
    object.__setattr__(instance, "gate", gate)
    object.__setattr__(instance, "coordinator", coordinator)
    object.__setattr__(instance, "session_manifest", session_manifest)
    object.__setattr__(instance, "bound_decision", decision)
    return instance


def bind_provider_persistence_authorizer(
    *,
    gate: ProviderGate,
    coordinator: RetentionCoordinator,
    session_manifest: SessionManifest,
) -> ProviderPersistenceAuthorizer:
    if type(gate) is not ProviderGate:
        raise TypeError("exact ProviderGate required")
    if type(coordinator) is not RetentionCoordinator:
        raise TypeError("exact RetentionCoordinator required")
    if type(session_manifest) is not SessionManifest:
        raise TypeError("exact SessionManifest required")
    decision = gate.require_start()
    _rebind(decision, session_manifest)
    return _build_provider_persistence_authorizer(
        gate=gate,
        coordinator=coordinator,
        session_manifest=session_manifest,
        decision=decision,
        sentinel=_AUTHORIZER_SENTINEL,
    )


class EventRuntime:
    __slots__ = (
        "_writer",
        "_state",
        "_persistence_authorizer",
        "_coordinator",
        "_trace",
        "_owner_pid",
        "_owner_thread",
        "_poisoned",
        "_closed",
    )

    def __init__(
        self,
        *,
        writer: JournalWriter,
        state: FoundationState,
        persistence_authorizer: ProviderPersistenceAuthorizer,
        coordinator: RetentionCoordinator,
    ) -> None:
        if type(writer) is not JournalWriter:
            raise TypeError("exact JournalWriter required")
        if type(coordinator) is not RetentionCoordinator:
            raise TypeError("exact RetentionCoordinator required")
        if type(persistence_authorizer) is not ProviderPersistenceAuthorizer:
            raise TypeError("exact ProviderPersistenceAuthorizer required")
        try:
            manifest = writer.session_manifest
        except Exception:
            raise TypeError("writer manifest unavailable") from None
        if type(manifest) is not SessionManifest:
            raise TypeError("exact writer SessionManifest required")
        if type(state) is not FoundationState:
            raise TypeError("exact FoundationState required")
        if state != initial_state(manifest.session_id):
            raise ValueError("runtime_requires_exact_initial_state")
        try:
            authorizer_manifest = persistence_authorizer.session_manifest
            authorizer_coordinator = persistence_authorizer.coordinator
        except Exception:
            raise TypeError("persistence authorizer binding unavailable") from None
        if (
            authorizer_manifest is not manifest
            or authorizer_coordinator is not coordinator
        ):
            raise ValueError("runtime_session_binding_mismatch")
        try:
            start = writer.session_start
            if (
                type(start) is not PersistedEvent
                or start.session_id != manifest.session_id
                or start.payload != canonical_session_manifest_bytes(manifest)
            ):
                raise ValueError("runtime_session_start_binding_mismatch")
            trace = initial_trace(start)
        except (TypeError, ValueError):
            raise ValueError("runtime_session_start_binding_mismatch") from None

        self._writer = writer
        self._state = state
        self._persistence_authorizer = persistence_authorizer
        self._coordinator = coordinator
        self._trace = trace
        self._owner_pid = os.getpid()
        self._owner_thread = threading.current_thread()
        self._poisoned = False
        self._closed = False

        claimed = False
        try:
            writer.claim_runtime(
                persistence_authorizer=persistence_authorizer,
                coordinator=coordinator,
            )
            claimed = True
            self._require_coordinator()
            persistence_authorizer.authorize_session()
        except BaseException:
            if claimed and not self._writer_poisoned():
                try:
                    self._attempt_halt("initialization_failure")
                except BaseException:
                    pass
            self._closed = True
            raise

    @property
    def state(self) -> FoundationState:
        return self._state

    @property
    def trace_sha256(self) -> str:
        return self._trace.hex()

    def _writer_poisoned(self) -> bool:
        try:
            return self._writer.poisoned is True
        except Exception:
            return True

    def _require_owner_and_healthy(self) -> None:
        if (
            os.getpid() != self._owner_pid
            or threading.current_thread() is not self._owner_thread
        ):
            raise WrongOwnerThread("runtime_wrong_owner_thread")
        if self._poisoned or self._closed or self._writer_poisoned():
            raise RuntimePoisoned("runtime_permanently_unavailable")

    def require_owner(self) -> None:
        self._require_owner_and_healthy()

    def _witnesses(self) -> dict[str, object]:
        return {
            "trace_sha256": self._trace.hex(),
            "final_state_sha256": hashlib.sha256(
                canonical_state_bytes(self._state)
            ).hexdigest(),
            "last_applied_raw_seq": self._state.last_applied_raw_seq,
        }

    def _poison(self) -> None:
        self._poisoned = True
        self._closed = True

    def _attempt_preobserved_global_halt(self) -> None:
        try:
            self._coordinator.require_control_halt_eligible(
                session_id=self._writer.session_manifest.session_id,
            )
        except (RetentionDueDeleteError, RetentionGlobalHalt, RetentionError):
            self._closed = True
            return
        try:
            self._writer.close_halted(
                reason="retention_global_halt",
                **self._witnesses(),
            )
        except JournalDurabilityError:
            self._poison()
            raise
        except BaseException:
            self._closed = True
            return
        self._closed = True

    def _require_coordinator(self) -> None:
        try:
            self._coordinator.require_provider_operation()
        except RetentionGlobalHalt:
            self._attempt_preobserved_global_halt()
            raise

    def _attempt_halt(self, reason: str) -> PersistedEvent | None:
        if self._closed or self._poisoned or self._writer_poisoned():
            return None
        try:
            self._coordinator.require_provider_operation()
        except RetentionGlobalHalt:
            self._attempt_preobserved_global_halt()
            return None
        try:
            self._persistence_authorizer.authorize_close()
        except ProviderGateError:
            # A halt is the fail-closed outcome of a gate denial. It never
            # reopens provider capture or appends provider bytes.
            pass
        except RetentionGlobalHalt:
            self._closed = True
            return None
        except BaseException:
            pass
        try:
            terminal = self._writer.close_halted(
                reason=reason,
                **self._witnesses(),
            )
        except JournalDurabilityError:
            self._poison()
            raise
        except BaseException as error:
            self._poison()
            raise JournalDurabilityError(
                "journal_terminal_outcome_uncertain"
            ) from error
        self._closed = True
        return terminal

    def _gate_denied(self) -> None:
        self._attempt_halt("provider_gate_denied")

    def ingest(self, captured: CapturedInput) -> PersistedEvent:
        self._require_owner_and_healthy()
        if type(captured) is not CapturedInput:
            self._attempt_halt("capture_contract_violation")
            raise CaptureValidationError("captured_input_required")
        try:
            self._require_coordinator()
            self._persistence_authorizer.authorize_ingest(captured)
            delete_by_ns = self._persistence_authorizer.authorize_persist(
                captured
            )
        except ProviderGateError:
            self._gate_denied()
            raise
        except CaptureValidationError:
            self._attempt_halt("capture_contract_violation")
            raise
        except RetentionGlobalHalt:
            if not self._closed:
                self._poison()
            raise
        if captured.retention_delete_by_ns != delete_by_ns:
            self._attempt_halt("capture_contract_violation")
            raise CaptureValidationError("retention_deadline_mismatch")

        try:
            self._require_coordinator()
            raw = self._writer.append_raw(captured)
        except JournalValidationError:
            self._attempt_halt("capture_contract_violation")
            raise
        except DiskLowError:
            self._attempt_halt("disk_low")
            raise
        except JournalDurabilityError:
            self._poison()
            raise
        except RetentionGlobalHalt:
            if not self._closed:
                self._poison()
            raise

        failure_reason = "reducer_exception"
        try:
            self._require_coordinator()
            self._persistence_authorizer.authorize_transform(raw)
            reduction = reduce_event(self._state, raw)
            stored_outputs: list[PersistedEvent] = []
            for draft in reduction.outputs:
                failure_reason = "derived_validation_failure"
                self._require_coordinator()
                self._persistence_authorizer.authorize_derived_persist(
                    raw,
                    draft,
                )
                stored_outputs.append(
                    self._writer.append_derived(raw, draft)
                )
            stored = tuple(stored_outputs)
            self._require_coordinator()
            if self._persistence_authorizer.poll_session():
                raise ProviderGateError(
                    QualificationReason.SESSION_WINDOW_EXCEEDS_ACCESS
                )
            failure_reason = "trace_exception"
            new_trace = next_trace(
                self._trace,
                raw,
                stored,
                reduction.state,
            )
        except ProviderGateError:
            self._gate_denied()
            raise
        except DiskLowError:
            self._attempt_halt("disk_low")
            raise
        except JournalDurabilityError:
            self._poison()
            raise
        except RetentionGlobalHalt:
            if not self._closed:
                self._poison()
            raise
        except BaseException:
            self._attempt_halt(failure_reason)
            raise

        self._trace = new_trace
        self._state = reduction.state
        return raw

    def poll_entitlement(self) -> PersistedEvent | None:
        self._require_owner_and_healthy()
        try:
            self._require_coordinator()
            ended = self._persistence_authorizer.poll_session()
            if type(ended) is not bool:
                raise ProviderGateError(
                    QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH
                )
        except ProviderGateError:
            self._gate_denied()
            raise
        except RetentionGlobalHalt:
            if not self._closed:
                self._poison()
            raise
        if ended:
            return self._close_clean_internal("session_end")
        return None

    def _check_ingress_session_end_internal(self) -> bool:
        try:
            self._require_coordinator()
            ended = self._persistence_authorizer.poll_session()
            if type(ended) is not bool:
                raise ProviderGateError(
                    QualificationReason.QUALIFICATION_EVIDENCE_MISMATCH
                )
        except ProviderGateError:
            self._gate_denied()
            raise
        except RetentionGlobalHalt:
            if not self._closed:
                self._poison()
            raise
        return ended

    def check_ingress_session_end(self) -> bool:
        self._require_owner_and_healthy()
        return self._check_ingress_session_end_internal()

    def close_ingress_session_end(self) -> PersistedEvent:
        self._require_owner_and_healthy()
        if self._check_ingress_session_end_internal() is not True:
            self._poison()
            raise RuntimePoisoned("session_end_not_current")
        return self._close_clean_internal("session_end")

    def _close_clean_internal(self, reason: str) -> PersistedEvent:
        try:
            self._require_coordinator()
            self._persistence_authorizer.authorize_close()
        except ProviderGateError:
            self._gate_denied()
            raise
        except RetentionGlobalHalt:
            if not self._closed:
                self._poison()
            raise
        try:
            terminal = self._writer.close_clean(
                reason=reason,
                **self._witnesses(),
            )
        except JournalDurabilityError:
            self._poison()
            raise
        self._closed = True
        try:
            self._coordinator.mark_clean_terminal(
                session_id=self._writer.session_manifest.session_id
            )
        except BaseException:
            self._poisoned = True
            raise
        return terminal

    def close_clean(self, reason: str) -> PersistedEvent:
        self._require_owner_and_healthy()
        if reason not in _CLEAN_PUBLIC_REASONS:
            raise ValueError("unsupported_public_clean_reason")
        return self._close_clean_internal(reason)

    def close_halted(self, reason: str) -> PersistedEvent:
        self._require_owner_and_healthy()
        if reason not in _HALT_PUBLIC_REASONS:
            raise ValueError("unsupported_public_halt_reason")
        terminal = self._attempt_halt(reason)
        if terminal is None:
            raise RuntimePoisoned("halt_terminal_not_durable")
        return terminal

    def close_ingress_backpressure(self) -> PersistedEvent:
        self._require_owner_and_healthy()
        terminal = self._attempt_halt("ingress_backpressure")
        if terminal is None:
            raise RuntimePoisoned("halt_terminal_not_durable")
        return terminal

    def close_ingress_owner_unresponsive(self) -> PersistedEvent:
        self._require_owner_and_healthy()
        terminal = self._attempt_halt("ingress_owner_unresponsive")
        if terminal is None:
            raise RuntimePoisoned("halt_terminal_not_durable")
        return terminal
